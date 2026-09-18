# encoding:utf-8
"""Adapter contract, execution context and registry (task group 4).

Subjects under test: ``integrations/external/adapters/base.py``.

The specs make the adapter contract the boundary where the control plane's
guarantees are enforced: the overall probe outcome is *derived* from the stages
("测试故障 SHALL 区分配置、认证、网络/TLS、超时、缺少依赖、策略阻止和服务异常"),
a context carries exactly the tenant/secret/deadline of one attempt and no
ambient fallback ("适配器从全局环境猜租户" is forbidden), and an unknown action
is refused by the adapter rather than by the caller remembering to check.

Everything here uses a throwaway adapter registered under a kind string no real
adapter will ever use, and removes it again: ``register_adapter`` is
process-global, so leaving a kind behind would leak into another test.
"""

from __future__ import annotations

import threading
import time

import pytest

from integrations.external.adapters import base as base_mod
from integrations.external.adapters.base import (
    AdapterError,
    Cancelled,
    ConnectionAdapter,
    DeadlineExceeded,
    ExecutionContext,
    ProbeResult,
    STAGE_AUTH,
    STAGE_TIMEOUT,
    STATUS_OK,
    StageResult,
    adapter_available,
    adapter_for,
    invoke_failed,
    invoke_ok,
    invoke_unknown,
    register_adapter,
    registered_kinds,
    stage_failed,
    stage_ok,
    stage_partial,
    stage_skipped,
)

KIND = "probe_test_kind"
ACTION = "probe.one"


@pytest.fixture
def adapter_kind():
    """Register one throwaway adapter and remove it afterwards."""
    cls = type("ProbeTestAdapter", (ConnectionAdapter,), {
        "kind": KIND,
        "actions": frozenset({ACTION}),
        "write_actions": frozenset(),
    })
    register_adapter(cls)
    try:
        yield KIND
    finally:
        # No unregister API exists, so the test removes the two entries it
        # created rather than leaving a resolvable kind in the process.
        base_mod._ADAPTER_CLASSES.pop(KIND, None)
        base_mod._ADAPTERS.pop(KIND, None)


def _context(**overrides) -> ExecutionContext:
    values = dict(
        kind=KIND, scope="tenant", tenant_id="tenant-a", owner_user_id=None,
        connection_id="conn-a", config={}, secret_resolver=lambda slot: "",
        config_version=4, secret_versions={}, actor_user_id="user-a",
    )
    values.update(overrides)
    return ExecutionContext(**values)


# -- ProbeResult: the outcome is derived, never asserted ---------------------

def test_probe_outcome_is_ok_when_every_attempted_stage_is_ok():
    result = ProbeResult(stages=(stage_ok("handshake"),
                                 stage_skipped("tools", stage=STAGE_AUTH),
                                 stage_ok("tools")))
    assert result.outcome == "ok"
    assert result.ok is True


def test_probe_outcome_is_failed_when_a_stage_failed():
    result = ProbeResult(stages=(
        stage_ok("handshake"),
        stage_failed("login", "bad_credentials", stage=STAGE_AUTH),
    ))
    assert result.outcome == "failed"
    assert result.ok is False
    assert result.failed_stage().code == "bad_credentials"


def test_probe_outcome_is_partial_when_every_attempted_stage_is_partial():
    result = ProbeResult(stages=(
        stage_partial("imap", "auth_failed", stage=STAGE_AUTH),
        stage_partial("smtp", "auth_failed", stage=STAGE_AUTH),
    ))
    assert result.outcome == "partial"
    assert result.ok is False


def test_probe_outcome_is_failed_when_failed_and_partial_are_mixed():
    result = ProbeResult(stages=(
        stage_partial("imap", "auth_failed", stage=STAGE_AUTH),
        stage_failed("smtp", "connection_refused", stage=STAGE_AUTH),
    ))
    assert result.outcome == "failed"


def test_probe_outcome_is_failed_when_no_stage_was_attempted():
    result = ProbeResult(stages=(stage_skipped("handshake"),
                                 stage_skipped("tools")))
    assert result.outcome == "failed"


def test_probe_outcome_is_failed_when_cancelled_even_if_the_stages_look_ok():
    result = ProbeResult(stages=(stage_ok("handshake"),), cancelled=True)
    assert result.outcome == "failed"
    assert result.ok is False


def test_the_outcome_cannot_be_passed_in_by_a_caller():
    # ``outcome``/``ok`` are properties, so a probe cannot claim success while
    # a stage failed: there is no constructor argument for the verdict.
    with pytest.raises(TypeError):
        ProbeResult(outcome="ok")
    with pytest.raises(TypeError):
        ProbeResult(ok=True)


def test_as_dict_reports_the_derived_outcome_and_failure_code():
    payload = ProbeResult(stages=(
        stage_failed("login", "bad_credentials", stage=STAGE_AUTH),
    )).as_dict()
    assert payload["outcome"] == "failed"
    assert payload["ok"] is False
    assert payload["code"] == "bad_credentials"
    assert payload["stage"] == STAGE_AUTH


# -- StageResult vocabulary --------------------------------------------------

def test_stage_result_rejects_an_unknown_status():
    with pytest.raises(ValueError):
        StageResult(name="x", status="bogus")


def test_stage_result_rejects_an_unknown_stage():
    with pytest.raises(ValueError):
        StageResult(name="x", status=STATUS_OK, stage="bogus")


# -- InvokeResult helpers ----------------------------------------------------

def test_invoke_ok_is_ok_and_not_unknown():
    result = invoke_ok({"count": 2})
    assert result.ok is True
    assert result.outcome_unknown is False
    assert result.data == {"count": 2}


def test_invoke_failed_keeps_its_code_and_stage():
    result = invoke_failed("bad_query", stage=STAGE_AUTH)
    assert result.ok is False
    assert result.outcome_unknown is False
    assert result.code == "bad_query"
    assert result.stage == STAGE_AUTH


def test_invoke_unknown_marks_the_outcome_unknown():
    result = invoke_unknown("timeout_after_send")
    assert result.ok is False
    assert result.outcome_unknown is True
    assert result.stage == STAGE_TIMEOUT


# -- ExecutionContext: secrets ----------------------------------------------

def test_secret_goes_through_the_resolver_and_the_versions_are_recorded():
    seen = []

    def resolver(slot):
        seen.append(slot)
        return {"password": "s3cr3t"}[slot]

    ctx = _context(secret_resolver=resolver, secret_versions={"password": 7})
    assert ctx.secret("password") == "s3cr3t"
    assert seen == ["password"]
    # The context carries which secret versions it was built with, so a result
    # can be bound to the credential it actually used.
    assert dict(ctx.secret_versions) == {"password": 7}


def test_an_unknown_secret_slot_raises_secret_unavailable():
    def resolver(slot):
        raise KeyError(slot)

    ctx = _context(secret_resolver=resolver)
    with pytest.raises(AdapterError) as caught:
        ctx.secret("missing")
    assert caught.value.code == "secret_unavailable"


def test_optional_secret_is_empty_and_has_secret_is_false_for_an_unknown_slot():
    def resolver(slot):
        raise KeyError(slot)

    ctx = _context(secret_resolver=resolver)
    assert ctx.optional_secret("missing") == ""
    assert ctx.has_secret("missing") is False


def test_has_secret_reflects_an_empty_versus_configured_slot():
    def resolver(slot):
        return {"present": "value", "empty": ""}[slot]

    ctx = _context(secret_resolver=resolver)
    assert ctx.has_secret("present") is True
    assert ctx.has_secret("empty") is False


def test_a_context_resolves_only_the_tenant_and_connection_it_was_built_for():
    by_tenant = {"tenant-a": "secret-a", "tenant-b": "secret-b"}

    def resolver_for(tenant):
        def resolve(slot):
            if slot != "password":
                raise KeyError(slot)
            return by_tenant[tenant]
        return resolve

    a = _context(tenant_id="tenant-a", connection_id="conn-a",
                 secret_resolver=resolver_for("tenant-a"))
    b = _context(tenant_id="tenant-b", connection_id="conn-b",
                 secret_resolver=resolver_for("tenant-b"))
    assert a.secret("password") == "secret-a"
    assert b.secret("password") == "secret-b"
    # A different connection/tenant is a different context, not a fallback.
    assert (a.connection_id, a.tenant_id) != (b.connection_id, b.tenant_id)


def test_there_is_no_ambient_environment_fallback_for_a_missing_secret(monkeypatch):
    monkeypatch.setenv("EXTERNAL_CONNECTION_PASSWORD", "from-the-environment")

    def resolver(slot):
        raise KeyError(slot)

    ctx = _context(secret_resolver=resolver)
    with pytest.raises(AdapterError) as caught:
        ctx.secret("password")
    assert caught.value.code == "secret_unavailable"


# -- ExecutionContext: time and cancellation --------------------------------

def test_check_alive_raises_cancelled_when_the_cancel_event_is_set():
    cancel = threading.Event()
    cancel.set()
    ctx = _context(cancel=cancel)
    assert ctx.cancelled is True
    with pytest.raises(Cancelled) as caught:
        ctx.check_alive()
    assert caught.value.code == "cancelled"


def test_check_alive_raises_deadline_exceeded_past_the_deadline():
    ctx = _context(deadline=time.monotonic() - 1)
    with pytest.raises(DeadlineExceeded) as caught:
        ctx.check_alive()
    assert caught.value.code == "timeout"


def test_remaining_is_none_without_a_deadline_and_positive_with_one():
    assert _context().remaining() is None
    ctx = _context(deadline=time.monotonic() + 5)
    assert ctx.remaining() > 0


def test_io_timeout_is_bounded_by_the_default_and_the_deadline():
    ctx = _context(deadline=time.monotonic() + 5)
    remaining = ctx.remaining()
    assert ctx.io_timeout(30.0) <= remaining
    assert ctx.io_timeout(0.25) == pytest.approx(0.25, abs=0.05)
    # No deadline: the caller's default is what it gets.
    assert _context().io_timeout(12.5) == pytest.approx(12.5)


def test_io_timeout_never_returns_more_than_the_remaining_time():
    # An I/O call must not be handed a timeout longer than the attempt has
    # left, or the deadline stops bounding the remote call.
    ctx = _context(deadline=time.monotonic() + 0.01)
    remaining = ctx.remaining()
    assert ctx.io_timeout(30.0) <= remaining


# -- the contract helper and the registry -----------------------------------

def test_guard_action_refuses_an_undeclared_action(adapter_kind):
    adapter = adapter_for(adapter_kind)
    adapter.guard_action(ACTION)  # declared: no refusal
    with pytest.raises(AdapterError) as caught:
        adapter.guard_action("probe.not_declared")
    assert caught.value.code == "unsupported_action"


def test_adapter_for_refuses_an_unregistered_kind_with_no_adapter():
    with pytest.raises(AdapterError) as caught:
        adapter_for("kind_that_no_adapter_declares")
    assert caught.value.code == "no_adapter"
    assert adapter_available("kind_that_no_adapter_declares") is False


def test_registered_kinds_lists_what_was_registered(adapter_kind):
    assert adapter_kind in registered_kinds()
    assert adapter_available(adapter_kind) is True
    # Resolution is cached per kind, which is what makes an adapter instance
    # stable across calls.
    assert adapter_for(adapter_kind) is adapter_for(adapter_kind)
