# encoding:utf-8
"""The bounded test pool (task group 4).

Subject under test: ``integrations/external/adapters/pool.py``.

The spec and design require a connection test to be a *bounded* operation: a
per-tenant and global concurrency cap, a real deadline that cancels I/O, the
slot released even when the adapter misbehaves, and an abandonable worker for a
probe that cannot be interrupted ("实现有界同步测试池、分范围限流/配额、I/O 超时
取消和资源释放;不可中断 SDK 调用使用可终止 worker,并验证压力下不拖垮 Web").

Coordination is by events, never by sleeping; the pools used here are explicit
instances so the tests do not touch the process-global deployment pools.
"""

from __future__ import annotations

import threading

import pytest

from integrations.external.adapters import pool as pool_mod
from integrations.external.adapters.base import (
    AdapterError,
    Cancelled,
    ConnectionAdapter,
    DeadlineExceeded,
    ExecutionContext,
    ProbeResult,
    STAGE_AUTH,
    STAGE_INTERNAL,
    STAGE_TIMEOUT,
    stage_ok,
)
from integrations.external.adapters.pool import (
    PoolBusy,
    PoolLimits,
    abandoned_snapshot,
    limits_from_mapping,
    reset_abandoned,
    reset_pools,
    run_probe,
    run_probe_with_kill_switch,
)
# Aliased so pytest does not try to collect the production class as a test.
from integrations.external.adapters.pool import TestPool as _TestPool

KIND = "pool_test_kind"


@pytest.fixture(autouse=True)
def _clean_pool_state():
    reset_pools()
    reset_abandoned()
    try:
        yield
    finally:
        reset_pools()
        reset_abandoned()


class _RecordingAdapter(ConnectionAdapter):
    """A probe whose behaviour is supplied by the test."""

    kind = KIND
    actions = frozenset({"probe.check"})
    write_actions = frozenset()

    def __init__(self, behaviour=None):
        self.calls = 0
        self.contexts = []
        self._behaviour = behaviour or (
            lambda ctx: ProbeResult(stages=(stage_ok("probe"),)))

    def probe(self, ctx):
        self.calls += 1
        self.contexts.append(ctx)
        return self._behaviour(ctx)


def _pool(**overrides) -> _TestPool:
    values = dict(global_limit=8, tenant_limit=8, user_limit=8, timeout=5.0)
    values.update(overrides)
    return _TestPool(PoolLimits(**values))


def _ctx(**overrides) -> ExecutionContext:
    values = dict(
        kind=KIND, scope="tenant", tenant_id="tenant-a", owner_user_id=None,
        connection_id="conn-a", config={}, secret_resolver=lambda slot: "",
        config_version=3, secret_versions={"password": 1},
        actor_user_id="user-a",
    )
    values.update(overrides)
    return ExecutionContext(**values)


def _raise(exc):
    def behaviour(ctx):
        raise exc
    return behaviour


# -- a well-behaved probe ----------------------------------------------------

def test_run_probe_returns_ok_and_calls_the_adapter_exactly_once():
    adapter = _RecordingAdapter()
    pool = _pool()
    result = run_probe(adapter, _ctx(), pool=pool)
    assert result.outcome == "ok"
    assert result.ok is True
    assert adapter.calls == 1
    assert pool.snapshot()["in_use"] == 0


# -- concurrency bounds, enforced and released -------------------------------

def test_the_global_limit_refuses_once_exhausted():
    pool = _pool(global_limit=1)
    with pool.admit(tenant_id="t1", user_id="u1"):
        with pytest.raises(PoolBusy) as caught:
            with pool.admit(tenant_id="t2", user_id="u2"):
                pass
    assert caught.value.code == "test_pool_busy"
    assert caught.value.retry_after >= 1


def test_the_per_tenant_limit_refuses_a_second_probe_for_the_same_tenant():
    pool = _pool(tenant_limit=1)
    with pool.admit(tenant_id="t1", user_id="u1"):
        with pytest.raises(PoolBusy):
            with pool.admit(tenant_id="t1", user_id="u2"):
                pass
    # A different tenant is a different bucket.
    with pool.admit(tenant_id="t2", user_id="u2"):
        pass


def test_the_per_user_limit_refuses_a_second_probe_for_the_same_user():
    pool = _pool(user_limit=1)
    with pool.admit(tenant_id="t1", user_id="u1"):
        with pytest.raises(PoolBusy):
            with pool.admit(tenant_id="t1", user_id="u1"):
                pass
    # A different user under the same tenant has its own slot.
    with pool.admit(tenant_id="t1", user_id="u2"):
        pass


def test_pool_buckets_are_keyed_on_tenant_and_on_user():
    # The tenant bucket is shared by every probe of a tenant, but each user
    # has a separate per-user slot and a probe with no user consumes none.
    pool = _pool(tenant_limit=2, user_limit=1)
    with pool.admit(tenant_id="t1", user_id="u1", scope="personal"):
        with pool.admit(tenant_id="t1", user_id="u2", scope="personal"):
            # The tenant has two slots in use, so a third is refused...
            with pytest.raises(PoolBusy):
                with pool.admit(tenant_id="t1", user_id="u3", scope="personal"):
                    pass
    # ...and a tenant-level probe (no owner) is admitted and does not touch
    # any per-user bucket.
    with pool.admit(tenant_id="t2", user_id="", scope="tenant"):
        with pool.admit(tenant_id="t2", user_id="u1", scope="personal"):
            pass


def test_a_raising_adapter_releases_its_slot():
    adapter = _RecordingAdapter(_raise(RuntimeError("adapter defect")))
    pool = _pool()
    result = run_probe(adapter, _ctx(), pool=pool)
    assert result.outcome == "failed"
    assert pool.snapshot()["in_use"] == 0
    # The released slot is genuinely reusable.
    assert run_probe(_RecordingAdapter(), _ctx(connection_id="conn-b"),
                     pool=pool).ok is True


def test_bounds_hold_under_real_overlapping_probes():
    started = threading.Event()
    release = threading.Event()

    def blocking(ctx):
        started.set()
        assert release.wait(5)
        return ProbeResult(stages=(stage_ok("probe"),))

    pool = _pool(global_limit=1)
    outcome = {}

    thread = threading.Thread(
        target=lambda: outcome.update(
            result=run_probe(_RecordingAdapter(blocking), _ctx(), pool=pool)))
    thread.start()
    assert started.wait(5)
    try:
        with pytest.raises(PoolBusy):
            run_probe(_RecordingAdapter(), _ctx(connection_id="conn-b"), pool=pool)
    finally:
        release.set()
        thread.join(5)
    assert thread.is_alive() is False
    assert outcome["result"].ok is True
    assert pool.snapshot()["in_use"] == 0


# -- the deadline and cancellation on the context ----------------------------

def test_run_probe_sets_a_deadline_and_cancels_after_it_returns():
    observed = {}

    def probe(ctx):
        observed["deadline"] = ctx.deadline
        observed["remaining"] = ctx.remaining()
        observed["cancelled_during"] = ctx.cancelled
        return ProbeResult(stages=(stage_ok("probe"),))

    pool = _pool(timeout=7.5)
    ctx = _ctx()
    run_probe(_RecordingAdapter(probe), ctx, pool=pool)

    assert observed["deadline"] is not None
    assert 0 < observed["remaining"] <= 7.5
    assert observed["cancelled_during"] is False
    # After the pool returns, a late I/O step must not think it still has budget.
    assert ctx.cancelled is True
    assert ctx.deadline is None
    with pytest.raises(Cancelled):
        ctx.check_alive()


def test_the_pool_deadline_comes_from_the_limits():
    pool = _pool(timeout=3.25)
    assert pool.limits.timeout == 3.25


# -- adapter failures are reported, never propagated -------------------------

def test_deadline_exceeded_becomes_a_timeout_stage():
    adapter = _RecordingAdapter(_raise(DeadlineExceeded()))
    result = run_probe(adapter, _ctx(), pool=_pool())
    assert result.outcome == "failed"
    assert result.cancelled is True
    stage = result.failed_stage()
    assert stage.code == "timeout"
    assert stage.stage == STAGE_TIMEOUT


def test_an_adapter_error_keeps_its_code_and_stage():
    adapter = _RecordingAdapter(
        _raise(AdapterError("bad credentials", code="auth_failed",
                            stage=STAGE_AUTH)))
    result = run_probe(adapter, _ctx(), pool=_pool())
    stage = result.failed_stage()
    assert stage.code == "auth_failed"
    assert stage.stage == STAGE_AUTH


def test_an_unexpected_exception_is_reported_not_propagated():
    adapter = _RecordingAdapter(_raise(RuntimeError("kaboom-internal-detail")))
    result = run_probe(adapter, _ctx(), pool=_pool())
    stage = result.failed_stage()
    assert stage.code == "adapter_error"
    assert stage.stage == STAGE_INTERNAL
    # A defect is a reported failure, not an unhandled 500, and it does not
    # leak the adapter's internal message into the projection.
    assert "kaboom-internal-detail" not in stage.detail


# -- uninterruptible probes --------------------------------------------------

def test_an_uninterruptible_probe_is_refused_when_not_accepted():
    adapter = _RecordingAdapter()
    pool = _pool(uninterruptible_ok=False)
    result = run_probe(adapter, _ctx(), pool=pool, uninterruptible=True)
    stage = result.failed_stage()
    assert stage.code == "uninterruptible_not_permitted"
    assert adapter.calls == 0
    assert pool.snapshot()["in_use"] == 0


def test_an_uninterruptible_probe_runs_when_the_deployment_accepts_it():
    adapter = _RecordingAdapter()
    pool = _pool(uninterruptible_ok=True)
    result = run_probe(adapter, _ctx(), pool=pool, uninterruptible=True)
    assert result.ok is True
    assert adapter.calls == 1


# -- limits_from_mapping -----------------------------------------------------

def test_limits_from_mapping_reads_and_validates_the_config_block():
    limits = limits_from_mapping({"test_pool": {
        "global_limit": 4, "tenant_limit": 3, "user_limit": 2,
        "timeout": 12.5, "uninterruptible_ok": True}})
    assert (limits.global_limit, limits.tenant_limit, limits.user_limit) == (4, 3, 2)
    assert limits.timeout == pytest.approx(12.5)
    assert limits.uninterruptible_ok is True


def test_limits_from_mapping_falls_back_for_zero_or_negative_values():
    limits = limits_from_mapping({"test_pool": {
        "global_limit": 0, "tenant_limit": -3, "user_limit": "0",
        "timeout": 0}})
    assert limits.global_limit == 16
    assert limits.tenant_limit == 2
    assert limits.user_limit == 1
    assert limits.timeout == pytest.approx(30.0)


def test_limits_from_mapping_falls_back_for_malformed_values():
    limits = limits_from_mapping({"test_pool": {
        "global_limit": "many", "timeout": "soon"}})
    assert limits.global_limit == 16
    assert limits.timeout == pytest.approx(30.0)


def test_limits_from_mapping_is_empty_safe():
    limits = limits_from_mapping(None)
    assert limits.global_limit == 16
    assert limits.uninterruptible_ok is False


# -- the kill switch for a worker that never returns -------------------------

def test_the_kill_switch_abandons_a_worker_that_never_returns():
    gate = threading.Event()

    def never_returns(ctx):
        gate.wait(5)
        return ProbeResult(stages=(stage_ok("probe"),))

    adapter = _RecordingAdapter(never_returns)
    ctx = _ctx(connection_id="conn-hang")
    try:
        result = run_probe_with_kill_switch(
            adapter, ctx, hard_timeout=0.05, deployment_key="kill-switch-test")
        assert result.outcome == "failed"
        assert result.cancelled is True
        stage = result.failed_stage()
        assert stage.code == "timeout"
        assert stage.stage == STAGE_TIMEOUT
        # The leak is reported rather than hidden.
        assert abandoned_snapshot().get("%s:conn-hang" % KIND, 0) >= 1
        # The slot was released even though the worker may still be running.
        pool = pool_mod.pool_for_deployment("kill-switch-test")
        assert pool.snapshot()["in_use"] == 0
    finally:
        gate.set()


def test_the_kill_switch_returns_the_result_of_a_worker_that_finishes():
    adapter = _RecordingAdapter()
    result = run_probe_with_kill_switch(
        adapter, _ctx(connection_id="conn-fast"), hard_timeout=5,
        deployment_key="kill-switch-test")
    assert result.ok is True
    assert abandoned_snapshot() == {}


def test_the_kill_switch_reports_an_adapter_error():
    adapter = _RecordingAdapter(
        _raise(AdapterError("nope", code="protocol_error", stage="protocol")))
    result = run_probe_with_kill_switch(
        adapter, _ctx(connection_id="conn-err"), hard_timeout=5,
        deployment_key="kill-switch-test")
    stage = result.failed_stage()
    assert stage.code == "protocol_error"
    assert stage.stage == "protocol"
