# encoding:utf-8
"""Type adapters: ``validate`` / ``probe`` / ``describe`` / ``invoke``.

Why this module exists
----------------------
The control plane stores *what* a connection is. An adapter owns *how to talk
to it*, and nothing else. Every adapter is handed an
:class:`ExecutionContext` that the service already built from a verified
request — tenant, owner, connection, resolved secret, deadline, cancellation
and the deployment's network policy. An adapter therefore cannot:

* discover the tenant, owner or scope from the process environment (the spec
  forbids "适配器从全局环境猜租户", design §4);
* read a secret it was not given for *this* call (``ctx.secret(slot)`` is the
  only door, and it records what was read);
* reach an address the deployment policy did not allow (:mod:`netpolicy`);
* outlive its deadline or ignore cancellation.

The four entry points
---------------------
``validate_config``
    Pure, offline shape validation. The registry already owns the canonical
    form (:func:`integrations.external.registry.validate_config`); an adapter
    implements this only for checks that need the type's own knowledge (an OA
    site URL's shape, an IMAP/SMTP pairing) and MUST NOT perform I/O.
``probe``
    A real, bounded connectivity/authentication check. It MUST NOT perform a
    business write — "MCP 仅握手与发现，ERP/OA/邮箱仅认证与必要只读探测".
``describe_capabilities``
    What this *specific* configuration can do, independent of deployment
    readiness. The registry merges it with the deployment's open classes.
``invoke``
    A real business call. It receives an action name from the adapter's own
    declared action list, so an unknown or unapproved action is refused here
    rather than by the caller remembering to check.

Nothing in this module performs I/O or reads configuration: it is the contract
and the value objects, which is what makes it safe for every adapter and the
test pool to import.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from common.log import logger

# -- staged result vocabulary ------------------------------------------------
#
# The spec requires the failure to be *identifiable*, not just "failed"
# ("测试故障 SHALL 区分配置、认证、网络/TLS、超时、缺少依赖、策略阻止和服务异常").
# The console renders the stage, so these names are user-visible.

STAGE_CONFIG = "config"
STAGE_NETWORK = "network"
STAGE_TLS = "tls"
STAGE_AUTH = "auth"
STAGE_PROTOCOL = "protocol"
STAGE_POLICY = "policy"
STAGE_DEPENDENCY = "dependency"
STAGE_TIMEOUT = "timeout"
STAGE_INTERNAL = "internal"

STAGES: Tuple[str, ...] = (
    STAGE_CONFIG, STAGE_NETWORK, STAGE_TLS, STAGE_AUTH, STAGE_PROTOCOL,
    STAGE_POLICY, STAGE_DEPENDENCY, STAGE_TIMEOUT, STAGE_INTERNAL,
)

#: ``ok`` — the stage passed.
#: ``failed`` — the stage ran and refused.
#: ``skipped`` — a previous stage failed, so this one could not be attempted
#:   honestly. Reported as skipped rather than failed so the console does not
#:   claim two independent failures.
#: ``partial`` — the stage covers more than one thing (IMAP + SMTP) and only
#:   some of them passed. The spec requires ``部分成功`` to be expressible
#:   without pretending the whole protocol is healthy.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PARTIAL = "partial"

STATUSES: Tuple[str, ...] = (STATUS_OK, STATUS_FAILED, STATUS_SKIPPED,
                             STATUS_PARTIAL)

#: Overall probe outcomes. ``partial`` is a first-class outcome, not a
#: rounding of two booleans: an email account whose IMAP works and whose SMTP
#: does not is neither success nor plain failure.
OUTCOME_OK = "ok"
OUTCOME_PARTIAL = "partial"
OUTCOME_FAILED = "failed"


@dataclass(frozen=True)
class StageResult:
    """One named step of a probe, with its own status and redacted detail."""

    name: str
    status: str
    stage: str = STAGE_INTERNAL
    #: A short, non-secret explanation. Never a raw remote error body: the
    #: spec forbids unfiltered remote errors in the projection.
    detail: str = ""
    code: str = ""
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("unknown stage status %r" % (self.status,))
        if self.stage not in STAGES:
            raise ValueError("unknown stage %r" % (self.stage,))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "stage": self.stage,
            "detail": self.detail,
            "code": self.code,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class ProbeResult:
    """A probe's outcome: an overall verdict plus the per-stage evidence.

    Constructed through the helpers rather than the raw initialiser so a
    caller cannot report ``ok`` while a stage failed — the inconsistency the
    spec calls out as "一次测试成功不能被解释为可执行授权" has a structural
    counterpart here: the overall outcome is *derived*, never asserted.
    """

    stages: Tuple[StageResult, ...] = ()
    #: Non-secret metadata a probe legitimately learned, e.g. discovered MCP
    #: tool names for a summary. Bounded by the caller's limits.
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: Set when the whole probe was abandoned rather than refused.
    cancelled: bool = False
    #: The configuration/secret versions this result was produced from, so a
    #: late result cannot be written onto a newer connection (spec: 结果绑定版本).
    config_version: int = 0
    secret_versions: Mapping[str, int] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        if self.cancelled:
            return OUTCOME_FAILED
        attempted = [s for s in self.stages if s.status != STATUS_SKIPPED]
        if not attempted:
            return OUTCOME_FAILED
        if any(s.status == STATUS_FAILED for s in attempted):
            # A partial stage that *also* has a failed stage is still a
            # failure: something the probe attempted did not work.
            return (OUTCOME_PARTIAL
                    if all(s.status == STATUS_PARTIAL for s in attempted)
                    else OUTCOME_FAILED)
        if any(s.status == STATUS_PARTIAL for s in attempted):
            return OUTCOME_PARTIAL
        return OUTCOME_OK

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK

    def failed_stage(self) -> Optional[StageResult]:
        for stage in self.stages:
            if stage.status == STATUS_FAILED:
                return stage
        return None

    def as_dict(self) -> Dict[str, Any]:
        failure = self.failed_stage()
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "cancelled": self.cancelled,
            "code": failure.code if failure else "",
            "stage": failure.stage if failure else "",
            "stages": [s.as_dict() for s in self.stages],
            "metadata": dict(self.metadata),
            "config_version": self.config_version,
            "secret_versions": dict(self.secret_versions),
        }


def stage_ok(name: str, *, stage: str = STAGE_INTERNAL, detail: str = "",
             duration_ms: int = 0) -> StageResult:
    return StageResult(name=name, status=STATUS_OK, stage=stage, detail=detail,
                       duration_ms=duration_ms)


def stage_failed(name: str, code: str, *, stage: str, detail: str = "",
                 duration_ms: int = 0) -> StageResult:
    return StageResult(name=name, status=STATUS_FAILED, stage=stage,
                       detail=detail, code=code, duration_ms=duration_ms)


def stage_skipped(name: str, *, stage: str = STAGE_INTERNAL,
                  detail: str = "") -> StageResult:
    return StageResult(name=name, status=STATUS_SKIPPED, stage=stage,
                       detail=detail)


def stage_partial(name: str, code: str, *, stage: str, detail: str = "",
                  duration_ms: int = 0) -> StageResult:
    return StageResult(name=name, status=STATUS_PARTIAL, stage=stage,
                       detail=detail, code=code, duration_ms=duration_ms)


@dataclass(frozen=True)
class CapabilityReport:
    """What this configuration can do, before deployment readiness is applied.

    ``classes`` uses the same vocabulary as the registry's access classes so
    the two can be intersected: ``configure`` is always served; ``test``,
    ``read_execute`` and ``write_execute`` are gated by deployment evidence.
    """

    classes: FrozenSet[str] = frozenset({"configure"})
    #: Per-action availability, e.g. ``{"send": False}`` with a reason. Used by
    #: OA and email, whose write actions open individually.
    actions: Mapping[str, bool] = field(default_factory=dict)
    #: Why something is not available, in the deployment's own words. Reported
    #: to the console instead of a success placeholder.
    reasons: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "classes": sorted(self.classes),
            "actions": dict(self.actions),
            "reasons": dict(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class InvokeResult:
    """A business call's outcome, including the honest "unknown" case.

    ``outcome_unknown`` is a distinct verdict, not an error: the spec requires
    an external action that may have been received to be recorded as unknown
    and reconciled, never silently retried ("外部提交超时 SHALL 标记结果未知").
    """

    ok: bool
    #: Present only when ``ok``.
    data: Any = None
    code: str = ""
    stage: str = ""
    message: str = ""
    #: True when the remote side may have accepted the request. A caller MUST
    #: reconcile rather than resend.
    outcome_unknown: bool = False
    duration_ms: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "outcome_unknown": self.outcome_unknown,
            "duration_ms": self.duration_ms,
        }


def invoke_ok(data: Any = None, *, duration_ms: int = 0) -> InvokeResult:
    return InvokeResult(ok=True, data=data, duration_ms=duration_ms)


def invoke_failed(code: str, *, stage: str = STAGE_INTERNAL, message: str = "",
                  duration_ms: int = 0) -> InvokeResult:
    return InvokeResult(ok=False, code=code, stage=stage, message=message,
                        duration_ms=duration_ms)


def invoke_unknown(code: str, *, stage: str = STAGE_TIMEOUT, message: str = "",
                   duration_ms: int = 0) -> InvokeResult:
    """The request may have reached the remote system. Do not resend blindly."""
    return InvokeResult(ok=False, code=code, stage=stage, message=message,
                        outcome_unknown=True, duration_ms=duration_ms)


# -- execution context -------------------------------------------------------

class AdapterError(Exception):
    """A refusal an adapter raises where a result object is not convenient.

    ``code`` and ``stage`` are the same pair a :class:`StageResult` carries, so
    an adapter's raised refusal and its returned one are interchangeable at the
    caller — which is what lets a probe report the same staged vocabulary
    whether the failure arrived as an exception or as a value.

    This is also the common base of the control plane's
    :class:`~integrations.external.errors.ExternalConnectionError`: the two are
    one refusal observed at two layers (a stage for the adapter, an HTTP status
    for the API). Keeping them in one hierarchy means no seam has to guess
    which of the two it might receive, and a new error type cannot silently
    escape a handler written against the other.
    """

    def __init__(self, message: str, *, code: str = "adapter_error",
                 stage: str = STAGE_INTERNAL) -> None:
        super().__init__(message)
        # ``message`` is set as an attribute as well as the args: a caller that
        # renders a refusal for a person reads it directly, and relying on
        # ``str(exc)`` makes the wording a side effect of the exception's
        # constructor.
        self.message = message
        self.code = code
        self.stage = stage


class DeadlineExceeded(AdapterError):
    def __init__(self, message: str = "operation deadline exceeded") -> None:
        super().__init__(message, code="timeout", stage=STAGE_TIMEOUT)


class PolicyRefused(AdapterError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="target_not_allowed", stage=STAGE_POLICY)


class Cancelled(AdapterError):
    def __init__(self, message: str = "operation cancelled") -> None:
        super().__init__(message, code="cancelled", stage=STAGE_TIMEOUT)


@dataclass
class ExecutionContext:
    """Everything one attempt is allowed to know, and nothing more.

    Built by the service from a verified request. Fields are read-only in
    spirit: adapters receive this object and must not mutate it.
    """

    kind: str
    scope: str
    tenant_id: Optional[str]
    owner_user_id: Optional[str]
    connection_id: str
    config: Mapping[str, Any]
    #: Resolves a secret slot for *this* connection only. Raises
    #: :class:`AdapterError` for an unknown or unavailable slot.
    secret_resolver: Callable[[str], str]
    #: The effective connection version, so a result can be bound to it.
    config_version: int = 0
    #: Which secret versions were read, for the same reason.
    secret_versions: Mapping[str, int] = field(default_factory=dict)
    #: The actor driving the attempt (for audit and per-call authorization).
    actor_user_id: str = ""
    agent_id: str = ""
    run_id: str = ""
    #: Whether this attempt is a saved-connection probe or a draft probe.
    #: A draft probe must not persist anything.
    draft: bool = False
    #: Absolute deadline (``time.monotonic`` base). ``None`` means the pool's
    #: own bound applies.
    deadline: Optional[float] = None
    #: Cooperative cancellation. Adapters must poll this between I/O steps and
    #: must not treat a set flag as a remote failure.
    cancel: Optional[threading.Event] = None
    #: Deployment limits, already resolved (timeouts, sizes, redirects...).
    limits: Mapping[str, Any] = field(default_factory=dict)
    #: Extra, non-secret facts a caller passes down (e.g. an OAuth state).
    extra: Mapping[str, Any] = field(default_factory=dict)

    # -- secret access -------------------------------------------------------

    def secret(self, slot: str) -> str:
        """Resolve one secret slot, recording that it was read.

        Every read goes through here so the audit and the returned
        ``secret_versions`` reflect what the attempt actually used.
        """
        try:
            return self.secret_resolver(slot)
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise the resolver's error
            raise AdapterError("secret %r is unavailable" % slot,
                               code="secret_unavailable",
                               stage=STAGE_CONFIG) from exc

    def optional_secret(self, slot: str) -> str:
        """Like :meth:`secret` but returns ``""`` when the slot is empty."""
        try:
            return self.secret(slot)
        except AdapterError:
            return ""

    def has_secret(self, slot: str) -> bool:
        try:
            return bool(self.secret(slot))
        except AdapterError:
            return False

    # -- time and cancellation ----------------------------------------------

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    def remaining(self) -> Optional[float]:
        """Seconds left, or ``None`` when unbounded."""
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()

    def check_alive(self) -> None:
        """Raise if the attempt was cancelled or has run out of time.

        Adapters call this between I/O steps: a bounded test must not keep
        talking to a remote system after the console stopped waiting.
        """
        if self.cancelled:
            raise Cancelled()
        left = self.remaining()
        if left is not None and left <= 0:
            raise DeadlineExceeded()

    def io_timeout(self, default: float) -> float:
        """The timeout to hand an I/O call: the smaller of the two bounds.

        The remaining budget is a *ceiling*, never raised to a floor. A minimum
        like ``0.1`` looks harmless and is not: it lets one I/O call outlive the
        attempt's deadline, which is exactly the bound the deadline exists to
        enforce, and the attempt then reports a remote failure it caused itself.
        When nothing is left the answer is a refusal, not a shorter-than-asked
        call: adapters call :meth:`check_alive` first, and this is the same
        verdict for the path that forgets to.
        """
        left = self.remaining()
        if left is None:
            return float(default)
        if left <= 0:
            raise DeadlineExceeded()
        return min(float(default), float(left))


# -- the adapter contract ----------------------------------------------------

class ConnectionAdapter:
    """Base class for a connection type's implementation.

    A subclass declares :attr:`kind` and implements the four entry points. It
    is registered with :func:`register_adapter` and then reachable through
    :func:`adapter_for`.
    """

    kind: str = ""
    #: Actions this adapter can serve. ``invoke`` refuses anything else, which
    #: is where "未支持接口明确关闭" is enforced.
    actions: FrozenSet[str] = frozenset()
    #: Actions that change the remote system. Used by the risk catalogue and
    #: the approval binding, never as the sole gate.
    write_actions: FrozenSet[str] = frozenset()

    # -- offline ------------------------------------------------------------

    def validate_config(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Type-specific offline checks. MUST NOT perform I/O.

        The registry has already produced the canonical form; return it
        unchanged when everything is acceptable, or raise
        :class:`~integrations.external.errors.ExternalConnectionError`.
        """
        return dict(config or {})

    # -- online -------------------------------------------------------------

    def probe(self, ctx: ExecutionContext) -> ProbeResult:  # pragma: no cover
        raise NotImplementedError

    def describe_capabilities(self, ctx: ExecutionContext) -> CapabilityReport:
        """What this configuration can do, ignoring deployment readiness."""
        return CapabilityReport()

    def invoke(self, ctx: ExecutionContext, action: str,
               params: Mapping[str, Any]) -> InvokeResult:  # pragma: no cover
        raise NotImplementedError

    # -- helpers for subclasses --------------------------------------------

    def guard_action(self, action: str) -> None:
        if action not in self.actions:
            raise AdapterError("action %r is not supported by %s" % (action, self.kind),
                               code="unsupported_action", stage=STAGE_CONFIG)


# -- registry ----------------------------------------------------------------

_ADAPTERS: Dict[str, ConnectionAdapter] = {}
_ADAPTER_CLASSES: Dict[str, type] = {}
#: Why an adapter kind could not be imported in this build, by kind. Kept so a
#: skipped kind is *explained* rather than silently missing.
_IMPORT_ERRORS: Dict[str, str] = {}
_IMPORT_TRACEBACKS: Dict[str, str] = {}
_REGISTRY_LOCK = threading.Lock()


def register_adapter(adapter_cls: type) -> type:
    """Register an adapter class by its ``kind``. Usable as a decorator."""
    kind = str(getattr(adapter_cls, "kind", "") or "")
    if not kind:
        raise ValueError("adapter %r declares no kind" % (adapter_cls,))
    with _REGISTRY_LOCK:
        _ADAPTER_CLASSES[kind] = adapter_cls
        _ADAPTERS.pop(kind, None)
    return adapter_cls


def _builtin_adapters() -> Dict[str, str]:
    """kind -> module path. Kept lazy so importing this module needs no SDK.

    A kind whose module is absent simply has no adapter: the capability
    projection then reports the type as not implementable, which is the honest
    answer rather than a crash at import time.
    """
    return {
        "mcp": "integrations.external.adapters.mcp",
        "erp": "integrations.external.adapters.erp",
        "oa": "integrations.external.adapters.oa",
        "email": "integrations.external.adapters.email",
    }


def load_adapters() -> None:
    """Import the built-in adapter modules so they self-register.

    A module that cannot be imported is *skipped*, not fatal, and the reason is
    kept: one adapter's import error would otherwise disable every external
    capability in the build, which turns a local defect into a global outage.
    The failure stays visible through :func:`adapter_import_error`, so a
    skipped kind is reported by name rather than looking like a build that
    never had it.
    """
    import importlib
    import traceback

    for kind, module_path in _builtin_adapters().items():
        with _REGISTRY_LOCK:
            if kind in _ADAPTER_CLASSES:
                continue
        try:
            importlib.import_module(module_path)
        except ImportError as error:
            # No adapter for this kind in this build. Reported per kind by
            # :func:`adapter_available`, not raised here.
            with _REGISTRY_LOCK:
                _IMPORT_ERRORS[kind] = "import_failed: %s" % error
            continue
        except Exception as error:  # noqa: BLE001 - one bad module, not all of them
            with _REGISTRY_LOCK:
                _IMPORT_ERRORS[kind] = "%s: %s" % (type(error).__name__, error)
                _IMPORT_TRACEBACKS[kind] = traceback.format_exc()
            logger.error("[external] adapter %r failed to load: %s", kind, error)
            continue


def adapter_import_error(kind: str) -> str:
    """Why this build has no adapter for ``kind`` ("" when it loaded fine)."""
    load_adapters()
    with _REGISTRY_LOCK:
        if kind in _ADAPTER_CLASSES or kind in _ADAPTERS:
            return ""
        return _IMPORT_ERRORS.get(str(kind or "").strip(), "")


def adapter_import_traceback(kind: str) -> str:
    """The traceback behind :func:`adapter_import_error` ("" when none)."""
    with _REGISTRY_LOCK:
        return _IMPORT_TRACEBACKS.get(str(kind or "").strip(), "")


def adapter_for(kind: str) -> ConnectionAdapter:
    """The adapter instance for ``kind``.

    Raises :class:`AdapterError` (code ``no_adapter``) when this build has no
    implementation, so a caller reports a concrete reason instead of an
    ``AttributeError``.
    """
    kind = str(kind or "").strip()
    with _REGISTRY_LOCK:
        cached = _ADAPTERS.get(kind)
        if cached is not None:
            return cached
    load_adapters()
    with _REGISTRY_LOCK:
        cached = _ADAPTERS.get(kind)
        if cached is not None:
            return cached
        cls = _ADAPTER_CLASSES.get(kind)
        if cls is None:
            raise AdapterError("no adapter is registered for %r" % kind,
                               code="no_adapter", stage=STAGE_DEPENDENCY)
        instance = cls()
        _ADAPTERS[kind] = instance
        return instance


def adapter_available(kind: str) -> bool:
    try:
        adapter_for(kind)
    except AdapterError:
        return False
    return True


def registered_kinds() -> List[str]:
    load_adapters()
    with _REGISTRY_LOCK:
        return sorted(_ADAPTER_CLASSES)
