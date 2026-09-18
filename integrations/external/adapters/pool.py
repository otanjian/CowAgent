# encoding:utf-8
"""The bounded test pool: how a probe is allowed to consume the process.

The spec is explicit that a connection test is a *bounded* operation and that
it must not be able to take the web process down
("实现有界同步测试池、分范围限流/配额、I/O 超时取消和资源释放；不可中断 SDK
调用使用可终止 worker，并验证压力下不拖垮 Web").

What that means concretely, and what this module therefore does:

* **Bounded concurrency, per scope and globally.** A tenant cannot occupy the
  whole pool, and a single tenant repeatedly testing cannot starve others.
  The limit is checked and released in ``finally``, so a raising adapter
  cannot leak a slot.
* **Admission instead of queueing.** A caller that would exceed its share is
  refused immediately with a retry hint (``429`` at the HTTP layer). It is
  deliberately *not* queued: a queued test holds a web worker while the user
  watches a spinner, which is the behaviour that "拖垮 Web" describes.
* **A real deadline, not a UI spinner.** The pool sets an absolute deadline on
  the context and clears the cancel flag at the end, so an adapter that polls
  :meth:`ExecutionContext.check_alive` stops talking to the remote system.
* **A kill switch for uninterruptible work.** An SDK call that cannot be
  interrupted (SAP RFC) runs on a worker that can be abandoned: the result is
  discarded, the slot is released, and the caller is told the probe timed out.
  The thread itself may linger, which is reported rather than hidden — that is
  why the deployment can keep such a probe closed
  (``readiness.<kind>.uninterruptible_ok``).
* **Per-scope quotas.** Testing consumes the existing quota meter, so the cost
  is attributed to the real tenant rather than to the platform

Nothing here reaches the database or the network: it is scheduling and
bookkeeping, which keeps the pool unit-testable without an environment.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from integrations.external.adapters.base import (
    AdapterError,
    DeadlineExceeded,
    ExecutionContext,
    ProbeResult,
    STAGE_TIMEOUT,
    STAGE_INTERNAL,
    stage_failed,
    stage_ok,
)
from integrations.external.errors import invalid

# -- defaults (a deployment may tighten, never loosen beyond these) ----------

DEFAULT_GLOBAL_LIMIT = 16
DEFAULT_TENANT_LIMIT = 2
DEFAULT_USER_LIMIT = 1
DEFAULT_TIMEOUT = 30.0


@dataclass
class PoolLimits:
    """Resolved pool bounds."""

    global_limit: int = DEFAULT_GLOBAL_LIMIT
    tenant_limit: int = DEFAULT_TENANT_LIMIT
    user_limit: int = DEFAULT_USER_LIMIT
    timeout: float = DEFAULT_TIMEOUT
    #: Whether a kind whose probe cannot be interrupted may run at all.
    #: False keeps stdio/SAP probes closed until an isolation story exists.
    uninterruptible_ok: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "global_limit": self.global_limit,
            "tenant_limit": self.tenant_limit,
            "user_limit": self.user_limit,
            "timeout": self.timeout,
            "uninterruptible_ok": self.uninterruptible_ok,
        }


def limits_from_mapping(raw: Optional[Mapping[str, Any]]) -> PoolLimits:
    data = dict(raw or {})
    pool = dict(data.get("test_pool") or {})

    def _positive(key: str, default: int) -> int:
        try:
            value = int(str(pool.get(key, default)).strip())
        except (TypeError, ValueError):
            return default
        return value if value >= 1 else default

    def _seconds(key: str, default: float) -> float:
        try:
            value = float(str(pool.get(key, default)).strip())
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return PoolLimits(
        global_limit=_positive("global_limit", DEFAULT_GLOBAL_LIMIT),
        tenant_limit=_positive("tenant_limit", DEFAULT_TENANT_LIMIT),
        user_limit=_positive("user_limit", DEFAULT_USER_LIMIT),
        timeout=_seconds("timeout", DEFAULT_TIMEOUT),
        uninterruptible_ok=bool(pool.get("uninterruptible_ok", False)),
    )


class PoolBusy(AdapterError):
    """The attempt was refused because the caller's share is in use."""

    def __init__(self, message: str, *, retry_after: int = 5) -> None:
        super().__init__(message, code="test_pool_busy", stage=STAGE_INTERNAL)
        self.retry_after = retry_after


@dataclass
class _Reservation:
    scope_key: str
    user_key: str
    started: float
    released: bool = False


class TestPool:
    """A process-wide admission controller for connection tests.

    Keyed by deployment *and* scope: the process may serve several deployments
    in tests, and every test app gets its own pool through
    :func:`pool_for_deployment` so a busy pool in one does not refuse another.
    """

    def __init__(self, limits: Optional[PoolLimits] = None) -> None:
        self._lock = threading.Lock()
        self._reservations: List[_Reservation] = []
        self._limits = limits or PoolLimits()

    # -- limits --------------------------------------------------------------

    @property
    def limits(self) -> PoolLimits:
        with self._lock:
            return self._limits

    def configure(self, limits: PoolLimits) -> None:
        with self._lock:
            self._limits = limits

    # -- admission -----------------------------------------------------------

    def _in_use(self, scope_key: str, user_key: str) -> Tuple[int, int, int]:
        total = tenant = user = 0
        for res in self._reservations:
            if res.released:
                continue
            total += 1
            if res.scope_key == scope_key:
                tenant += 1
                if user_key and res.user_key == user_key:
                    user += 1
        return total, tenant, user

    @contextmanager
    def admit(self, *, tenant_id: Optional[str], user_id: Optional[str],
              kind: str = "", scope: str = ""):
        """Reserve a slot for one attempt, or refuse with :class:`PoolBusy`.

        The caller's identity decides the bucket. A personal mailbox probe
        keys on the owner, a tenant probe on the tenant, so one member testing
        their mailbox cannot block a tenant admin testing SAP, and vice versa.
        """
        scope_key = str(tenant_id or "platform")
        user_key = str(user_id or "")
        limits = self.limits
        with self._lock:
            total, tenant, user = self._in_use(scope_key, user_key)
            if total >= limits.global_limit:
                raise PoolBusy("the connection test pool is full", retry_after=5)
            if tenant >= limits.tenant_limit:
                raise PoolBusy(
                    "too many connection tests are already running for this "
                    "account", retry_after=5)
            if user_key and user >= limits.user_limit:
                raise PoolBusy(
                    "a connection test is already running for this user",
                    retry_after=3)
            reservation = _Reservation(scope_key=scope_key, user_key=user_key,
                                       started=time.monotonic())
            self._reservations.append(reservation)
        try:
            yield reservation
        finally:
            with self._lock:
                reservation.released = True
                # Keep the list from growing without bound across a long
                # uptime: drop finished reservations once they are all done.
                if len(self._reservations) > 512:
                    self._reservations = [
                        r for r in self._reservations if not r.released]

    # -- observed state, for the console and for tests -----------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            live = {
                "tenant": sum(1 for r in self._reservations
                              if not r.released and r.scope_key != "platform"),
                "platform": sum(1 for r in self._reservations
                                if not r.released and r.scope_key == "platform"),
            }
            return {
                "limits": self._limits.as_dict(),
                "in_use": sum(live.values()),
                "by_scope": live,
            }


_POOLS: Dict[str, TestPool] = {}
_POOLS_LOCK = threading.Lock()


def pool_for_deployment(deployment_key: str = "default") -> TestPool:
    with _POOLS_LOCK:
        pool = _POOLS.get(deployment_key)
        if pool is None:
            pool = TestPool(limits_from_mapping(current_pool_config()))
            _POOLS[deployment_key] = pool
        return pool


def current_pool_config() -> Mapping[str, Any]:
    try:
        from config import conf
        raw = (conf() or {}).get("external_connections") or {}
        return raw if isinstance(raw, Mapping) else {}
    except Exception:  # noqa: BLE001
        return {}


def reset_pools() -> None:
    """Drop every pool. For tests and for a config reload."""
    with _POOLS_LOCK:
        _POOLS.clear()


# -- running a probe ---------------------------------------------------------

@dataclass
class ProbeOutcome:
    """A probe attempt after the pool has bounded it."""

    result: ProbeResult
    waited_ms: int = 0

    def as_dict(self) -> Dict[str, Any]:
        payload = self.result.as_dict()
        payload["waited_ms"] = self.waited_ms
        return payload


def run_probe(adapter, ctx: ExecutionContext, *,
              pool: Optional[TestPool] = None,
              uninterruptible: bool = False,
              deployment_key: str = "default") -> ProbeResult:
    """Run ``adapter.probe`` under the pool's bounds.

    ``uninterruptible`` marks a probe that cannot be stopped once started (an
    SDK that offers no cancellation). Such a probe may only run when the
    deployment has accepted the isolation story; otherwise it is refused with
    the reason, which is how stdio and SAP keep closed without the code being
    absent.
    """
    pool = pool or pool_for_deployment(deployment_key)
    limits = pool.limits
    if uninterruptible and not limits.uninterruptible_ok:
        return ProbeResult(stages=(
            stage_failed(
                "isolation", "uninterruptible_not_permitted",
                stage=STAGE_INTERNAL,
                detail="this probe cannot be cancelled and the deployment has "
                       "not accepted the isolation requirements for it"),
        ), config_version=ctx.config_version,
            secret_versions=dict(ctx.secret_versions))

    cancel = threading.Event()
    ctx.cancel = cancel
    ctx.deadline = time.monotonic() + limits.timeout
    started = time.monotonic()
    try:
        with pool.admit(tenant_id=ctx.tenant_id, user_id=ctx.actor_user_id,
                        kind=ctx.kind, scope=ctx.scope):
            try:
                result = adapter.probe(ctx)
            except DeadlineExceeded:
                result = ProbeResult(stages=(
                    stage_failed("deadline", "timeout", stage=STAGE_TIMEOUT,
                                 detail="the connection test ran out of time"),
                ), cancelled=True, config_version=ctx.config_version,
                    secret_versions=dict(ctx.secret_versions))
            except AdapterError as exc:
                result = ProbeResult(stages=(
                    stage_failed("probe", exc.code, stage=exc.stage,
                                 detail=str(exc)),
                ), config_version=ctx.config_version,
                    secret_versions=dict(ctx.secret_versions))
            except Exception as exc:  # noqa: BLE001 - an adapter bug is a
                # reported internal failure, never an unhandled 500 that hides
                # which connection misbehaved.
                result = ProbeResult(stages=(
                    stage_failed("probe", "adapter_error",
                                 stage=STAGE_INTERNAL,
                                 detail=type(exc).__name__),
                ), config_version=ctx.config_version,
                    secret_versions=dict(ctx.secret_versions))
    finally:
        # Belt and braces: a later I/O step must not think it is still inside
        # the budget, and the adapter must see cancellation if it is somehow
        # still running.
        cancel.set()
        ctx.deadline = None

    if result.config_version != ctx.config_version:
        result = ProbeResult(
            stages=result.stages, metadata=result.metadata,
            cancelled=result.cancelled, config_version=ctx.config_version,
            secret_versions=dict(ctx.secret_versions))
    return result


def run_probe_with_kill_switch(adapter, ctx: ExecutionContext, *,
                               hard_timeout: float,
                               deployment_key: str = "default") -> ProbeResult:
    """Run a probe on an abandonable worker.

    For a probe that ignores :meth:`ExecutionContext.check_alive` (a blocking
    SDK call), the pool cannot cancel it. It can still stop *waiting*: the
    worker is left to finish or die on its own, the slot is released, and the
    caller receives a timeout result. The abandoned worker is counted so an
    operator can see the leak rather than discover it as an incident.
    """
    pool = pool_for_deployment(deployment_key)
    result_box: List[ProbeResult] = []
    error_box: List[BaseException] = []

    def _work() -> None:
        try:
            result_box.append(adapter.probe(ctx))
        except BaseException as exc:  # noqa: BLE001 - carried to the caller
            error_box.append(exc)

    with pool.admit(tenant_id=ctx.tenant_id, user_id=ctx.actor_user_id,
                    kind=ctx.kind, scope=ctx.scope):
        worker = threading.Thread(target=_work, daemon=True,
                                  name="external-probe-%s" % ctx.connection_id)
        worker.start()
        worker.join(hard_timeout)
        if worker.is_alive():
            _register_abandoned(ctx)
            return ProbeResult(stages=(
                stage_failed(
                    "deadline", "timeout", stage=STAGE_TIMEOUT,
                    detail="the probe did not return in time and was "
                           "abandoned; the underlying call may still be "
                           "running"),
            ), cancelled=True, config_version=ctx.config_version,
                secret_versions=dict(ctx.secret_versions))
    if error_box:
        exc = error_box[0]
        if isinstance(exc, AdapterError):
            return ProbeResult(stages=(
                stage_failed("probe", exc.code, stage=exc.stage,
                             detail=str(exc)),
            ), config_version=ctx.config_version,
                secret_versions=dict(ctx.secret_versions))
        return ProbeResult(stages=(
            stage_failed("probe", "adapter_error", stage=STAGE_INTERNAL,
                         detail=type(exc).__name__),
        ), config_version=ctx.config_version,
            secret_versions=dict(ctx.secret_versions))
    if result_box:
        return result_box[0]
    return ProbeResult(stages=(
        stage_failed("probe", "no_result", stage=STAGE_INTERNAL),
    ), config_version=ctx.config_version,
        secret_versions=dict(ctx.secret_versions))


_ABANDONED_LOCK = threading.Lock()
_ABANDONED: Dict[str, int] = {}


def _register_abandoned(ctx: ExecutionContext) -> None:
    key = "%s:%s" % (ctx.kind, ctx.connection_id)
    with _ABANDONED_LOCK:
        _ABANDONED[key] = _ABANDONED.get(key, 0) + 1


def abandoned_snapshot() -> Dict[str, int]:
    """Abandoned probe workers, by kind and connection.

    Exposed so the "did the kill switch leak?" question has an answer that is
    not a guess.
    """
    with _ABANDONED_LOCK:
        return dict(_ABANDONED)


def reset_abandoned() -> None:
    with _ABANDONED_LOCK:
        _ABANDONED.clear()
