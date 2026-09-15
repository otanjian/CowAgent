# encoding:utf-8
"""The action-approval consumer at the real dispatch seams (task 7.9).

``auth/service.py`` owns the approval engine (request / decide / cancel /
revoke / expire / audit) and is covered by ``tests/test_control_plane.py``.
This file covers the part the engine deliberately does not decide: **which**
actions need an approval, and what happens at the moment one is *consumed* on
the way to a real side effect.

Two seams are exercised, both the delivered ones rather than a copy:

* the Agent's tool dispatch (``AgentStreamExecutor._execute_tool`` →
  ``_permission_denial``), where a declared ``tool:<name>`` action must present an
  approval that matches its own tenant, requester, action, target and parameter
  digest — and where a call that is *not* declared runs as before, with a
  recorded basis rather than an omission;
* the scheduler's outbound delivery (``_execute_send_message``), where a
  declared ``scheduler:send_message`` action delivers nothing until an approval
  covers exactly that channel type, receiver and content.

The applicability itself is deployment configuration (``approval_required_actions``),
so these tests declare a policy rather than depend on one: the shipped default is
"nothing declared", which is what keeps delivered behaviour unchanged.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import approval_gate as gate  # noqa: E402
from agent.approval_gate import (  # noqa: E402
    APPROVAL_ARGUMENT, approval_decision, action_basis, request_digest,
    required_actions, tool_action_id,
)
from agent.protocol.agent_stream import AgentStreamExecutor  # noqa: E402
from agent.tools.base_tool import BaseTool, ToolResult  # noqa: E402
from auth.service import IdentityServiceError  # noqa: E402
from common.runtime_identity import identity_scope  # noqa: E402
from tests._helpers import build_identity  # noqa: E402

REQUIRED = "tool:reporting"


class _Recorder(BaseTool):
    """The tool the declared action dispatches. Counts its own side effects."""

    name = "reporting"
    params = {"type": "object", "properties": {}}

    def __init__(self):
        self.calls = []

    def execute(self, params):
        self.calls.append(dict(params))
        return ToolResult.success("done")


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A real identity database whose member may run the declared action."""
    stack = build_identity(tmp_path, agents=("primary",))
    monkeypatch.setattr("auth.service.get_identity_service", lambda: stack.service)
    stack.agent_role("member-chat", ["primary"],
                     permissions=["chat.use", "agent.use", "agent.read", "tool.execute"])
    alice = stack.member("alice", ["member-chat"])
    bob = stack.member("bob", ["member-chat"])
    return SimpleNamespace(stack=stack, alice=alice, bob=bob,
                           tenant=stack.tenant_id, root=stack.root,
                           service=stack.service)


def _policy(*required):
    return {gate.REQUIRED_ACTIONS_KEY: list(required)}


def _request(stack, action, *, target, parameters, user=None, expires_in_s=1800):
    spec = (parameters or {})
    return stack.service.request_approval(
        actor_user_id=user or stack.alice, tenant_id=stack.tenant,
        agent_id="primary", action=action, payload=spec,
        expires_in_s=expires_in_s,
        target=target,
        digest=request_digest(action, target, spec),
    )


def _approve(stack, approval_id, *, decide_as=None):
    return stack.service.decide_approval(
        actor_user_id=decide_as or stack.root, tenant_id=stack.tenant,
        approval_id=approval_id, approve=True, note="ok",
    )


def _decide(stack, approval_id, approve):
    return stack.service.decide_approval(
        actor_user_id=stack.root, tenant_id=stack.tenant,
        approval_id=approval_id, approve=approve,
    )


# =====================================================================
# 1. Applicability is server-side, and "no approval" is a recorded basis
# =====================================================================

class TestBasis:
    """The policy answers "why", for applicable and non-applicable alike."""

    def test_the_shipped_default_requires_nothing(self):
        """
        Shipping the consumer must not start gating actions nobody declared:
        the delivered behaviour has no approval policy, so the default is none.
        """
        assert required_actions({}) == frozenset()
        assert required_actions({"approval_required_actions": ""}) == frozenset()

    def test_a_declared_action_is_required_and_its_basis_says_so(self):
        policy = _policy(REQUIRED)
        assert gate.is_required(REQUIRED, policy)
        basis = action_basis(REQUIRED, policy)
        assert "approval_required_actions" in basis

    def test_every_recorded_non_applicable_action_carries_a_basis(self):
        """
        Opening the gate for an action is not the same as having no gate: each
        recorded non-applicable id must say why, and stay inside a declared
        class, so a reviewer can check the claim against the code.
        """
        assert gate.NOT_APPLICABLE_ACTIONS, "the record must not be empty"
        for action_id, basis in gate.NOT_APPLICABLE_ACTIONS.items():
            assert action_id.split(":")[0].split(".")[0] in gate.ACTION_CLASSES
            assert len(basis.strip()) > 20, action_id

    def test_an_undeclared_action_gets_a_basis_too(self):
        """Silence is not a policy: the undeclared case explains itself."""
        basis = action_basis("tool:some_builtin_tool", {})
        assert "tool.execute" in basis

    def test_a_client_cannot_make_its_own_action_applicable(self):
        """Applicability comes from configuration, not from the call."""
        decision = approval_decision(
            REQUIRED, parameters={APPROVAL_ARGUMENT: "apr_x", "declared": "true"},
            config={},
        )
        assert decision.allowed and not decision.required

    def test_the_canonical_digest_ignores_the_approval_reference(self):
        """
        The reference to an approval is transport. If it were digested, pointing
        at an approval would change the request the approval was issued for.
        """
        plain = {"path": "/tmp/x", "message": "hi"}
        with_ref = dict(plain, **{APPROVAL_ARGUMENT: "apr_1"})
        assert (request_digest(REQUIRED, "t", plain)
                == request_digest(REQUIRED, "t", with_ref))

    def test_the_canonical_digest_is_order_insensitive_and_type_strict(self):
        """Same request, same digest; a changed value, a different digest."""
        assert (request_digest(REQUIRED, "t", {"a": 1, "b": 2})
                == request_digest(REQUIRED, "t", {"b": 2, "a": 1}))
        assert (request_digest(REQUIRED, "t", {"a": 1})
                != request_digest(REQUIRED, "t", {"a": 2}))
        assert (request_digest(REQUIRED, "t", {"a": 1})
                != request_digest(REQUIRED, "other", {"a": 1}))


# =====================================================================
# 2. Consumption: the matrix the requirement lists
# =====================================================================

class TestConsumption:
    """One approval authorises one action: pending/denied/revoked/expired/replay."""

    def _requested(self, stack, *, decide=None, expiry=1800,
                   parameters=None, target="chan:ops", action=REQUIRED, user=None):
        parameters = parameters if parameters is not None else {"path": "/tmp/x"}
        requested = _request(stack, action, target=target, parameters=parameters,
                             user=user, expires_in_s=expiry)
        if decide == "approve":
            _approve(stack, requested["id"])
        elif decide == "deny":
            _decide(stack, requested["id"], False)
        return requested

    def _gate(self, stack, approval_id, *, parameters=None, target="chan:ops",
              action=REQUIRED, user=None):
        """One dispatch attempt, exactly as a seam would make it."""
        parameters = dict(parameters if parameters is not None else {"path": "/tmp/x"})
        if approval_id:
            parameters[APPROVAL_ARGUMENT] = approval_id
        with identity_scope(user_id=user or stack.alice, tenant_id=stack.tenant,
                            agent_id="primary"):
            return approval_decision(action, parameters=parameters, target=target,
                                     config=_policy(REQUIRED), service=stack.service)

    def test_a_call_without_a_reference_is_refused_as_required(self, stack):
        """No reference at all is its own refusal: "ask for an approval first"."""
        decision = self._gate(stack, "")
        assert not decision.allowed
        assert decision.code == gate.CODE_REQUIRED

    def test_pending_is_refused_with_its_own_code(self, stack):
        requested = self._requested(stack)
        decision = self._gate(stack, requested["id"])
        assert not decision.allowed and decision.code == gate.CODE_PENDING

    def test_denied_revoked_expired_and_consumed_are_distinct_refusals(self, stack):
        cases = {}

        denied = self._requested(stack)
        _decide(stack, denied["id"], False)

        revoked = self._requested(stack, decide="approve")
        stack.service.revoke_approval(actor_user_id=stack.root,
                                      tenant_id=stack.tenant,
                                      approval_id=revoked["id"])

        expired = self._requested(stack, decide="approve", expiry=60)
        # The approval is approved and then runs out of time: the sweep only
        # flips pending rows, so the executor has to refuse it itself.
        stack.service._store.execute(
            "UPDATE approvals SET expires_at=? WHERE id=?",
            (1, expired["id"]))

        consumed = self._requested(stack, decide="approve")
        stack.service.consume_action_approval(
            actor_user_id=stack.alice, tenant_id=stack.tenant,
            approval_id=consumed["id"], action=REQUIRED, agent_id="primary",
            target="chan:ops",
            digest=request_digest(REQUIRED, "chan:ops", {"path": "/tmp/x"}))

        for name, requested in (("denied", denied), ("revoked", revoked),
                                ("expired", expired), ("consumed", consumed)):
            decision = self._gate(stack, requested["id"])
            cases[name] = decision.code
            assert not decision.allowed, name
        assert cases == {"denied": gate.CODE_DENIED, "revoked": gate.CODE_REVOKED,
                         "expired": gate.CODE_EXPIRED,
                         "consumed": gate.CODE_CONSUMED}

    def test_an_approved_matching_approval_is_consumed_once(self, stack):
        requested = self._requested(stack, decide="approve")
        first = self._gate(stack, requested["id"])
        assert first.allowed and first.required

        second = self._gate(stack, requested["id"])
        assert not second.allowed and second.code == gate.CODE_CONSUMED
        row = [a for a in stack.service.list_approvals(
            actor_user_id=stack.root, tenant_id=stack.tenant)
            if a["id"] == requested["id"]][0]
        assert row["status"] == "consumed"

    def test_changing_the_parameters_after_approval_is_a_mismatch(self, stack):
        """
        The approval covers a canonical digest, so "the same action with other
        parameters" is not the approved action — it is a different one.
        """
        requested = self._requested(stack, decide="approve")
        decision = self._gate(stack, requested["id"],
                              parameters={"path": "/tmp/other"})
        assert not decision.allowed and decision.code == gate.CODE_MISMATCH

    def test_changing_the_target_after_approval_is_a_mismatch(self, stack):
        requested = self._requested(stack, decide="approve", target="chan:ops")
        decision = self._gate(stack, requested["id"], target="chan:other")
        assert not decision.allowed and decision.code == gate.CODE_MISMATCH

    def test_another_members_approval_cannot_stand_in(self, stack):
        """An approval is the requester's own authority to act, not a token."""
        mine = self._requested(stack, decide="approve")
        decision = self._gate(stack, mine["id"], user=stack.bob)
        assert not decision.allowed and decision.code == gate.CODE_MISMATCH

    def test_another_tenants_approval_is_not_found(self, stack):
        other = stack.service.bootstrap(
            tenant_code="beta", tenant_name="Beta", admin_username="other-root",
            admin_display="Other", admin_password="Str0ngAdminPass",
            shared_root=str(Path(stack.stack.shared_root) / "beta"),
            allow_weak=True)
        other_tenant = other["id"]
        other_admin = [u["id"] for u in stack.service.list_platform_users()
                       if u["username"] == "other-root"][0]
        created = stack.service.create_member(
            actor_user_id=other_admin, tenant_id=other_tenant,
            operation="create-new", username="carol", display_name="Carol",
            temporary_password="TempPass123!", roles=[])
        token = stack.service.login("carol", "TempPass123!").token
        stack.service.change_password(token, "TempPass123!", "CarolPass123!")
        requested = stack.service.request_approval(
            actor_user_id=created["user_id"], tenant_id=other_tenant,
            agent_id="", action=REQUIRED, payload={"path": "/tmp/x"},
            target="chan:ops",
            digest=request_digest(REQUIRED, "chan:ops", {"path": "/tmp/x"}))
        stack.service.decide_approval(
            actor_user_id=other_admin, tenant_id=other_tenant,
            approval_id=requested["id"], approve=True)

        # The approval is real, approved and un-consumed — in the other tenant.
        # The row lookup is scoped, so it cannot be redeemed here.
        decision = self._gate(stack, requested["id"])
        assert not decision.allowed and decision.code == gate.CODE_UNKNOWN

    def test_a_reference_to_a_nonexistent_approval_is_refused(self, stack):
        decision = self._gate(stack, "apr_nope")
        assert not decision.allowed and decision.code == gate.CODE_UNKNOWN

    def test_an_unresolvable_identity_is_refused_before_anything_else(self, stack):
        """No identity means nothing to bind the approval to: fail closed."""
        requested = self._requested(stack, decide="approve")
        decision = approval_decision(
            REQUIRED,
            parameters={"path": "/tmp/x", APPROVAL_ARGUMENT: requested["id"]},
            config=_policy(REQUIRED), service=stack.service)
        assert not decision.allowed and decision.code == gate.CODE_UNVERIFIED


# =====================================================================
# 3. The engine itself: one conditional update, no double consumption
# =====================================================================

class TestEngine:
    def test_two_executors_cannot_consume_the_same_approval(self, stack):
        requested = _request(stack, REQUIRED, target="chan:ops",
                             parameters={"path": "/tmp/x"})
        _approve(stack, requested["id"])
        digest = request_digest(REQUIRED, "chan:ops", {"path": "/tmp/x"})
        first = stack.service.consume_action_approval(
            actor_user_id=stack.alice, tenant_id=stack.tenant,
            approval_id=requested["id"], action=REQUIRED, agent_id="primary",
            target="chan:ops", digest=digest)
        assert first["status"] == "consumed"
        with pytest.raises(IdentityServiceError) as exc:
            stack.service.consume_action_approval(
                actor_user_id=stack.alice, tenant_id=stack.tenant,
                approval_id=requested["id"], action=REQUIRED, agent_id="primary",
                target="chan:ops", digest=digest)
        assert exc.value.code == gate.CODE_CONSUMED

    def test_consumption_is_audited_without_the_payload(self, stack):
        requested = _request(stack, REQUIRED, target="chan:ops",
                             parameters={"path": "/tmp/x", "content": "secret-ish"})
        _approve(stack, requested["id"])
        stack.service.consume_action_approval(
            actor_user_id=stack.alice, tenant_id=stack.tenant,
            approval_id=requested["id"], action=REQUIRED, agent_id="primary",
            target="chan:ops",
            digest=request_digest(REQUIRED, "chan:ops",
                                  {"path": "/tmp/x", "content": "secret-ish"}))
        events = stack.service.list_audit(tenant_id=stack.tenant)
        rows = [e for e in events if e.get("action") == "approval.consume"]
        assert rows, "consumption must be traceable to an approver and a time"
        assert "secret-ish" not in json.dumps(rows, ensure_ascii=False, default=str)


# =====================================================================
# 4. The real seams: tool dispatch and scheduled delivery
# =====================================================================

class TestToolDispatchSeam:
    """``AgentStreamExecutor._execute_tool`` consults the gate for real."""

    def _executor(self, tool, monkeypatch):
        executor = object.__new__(AgentStreamExecutor)
        executor.tools = {tool.name: tool}
        executor.model = None
        executor.agent = SimpleNamespace(effective_cwd=lambda: "/tmp")
        executor.cancel_event = None
        executor._record_tool_result = lambda *a, **kw: None
        executor._check_consecutive_failures = lambda *a, **kw: (False, None, False)
        executor._emit_event = lambda kind, data: None
        # The gates *before* the approval gate already passed: identity,
        # isolation, per-resource authorization and quota. What is under test
        # here is the last gate in the chain.
        executor._resource_tool_denial = lambda *a, **kw: None
        executor._quota_tool_denial = lambda *a, **kw: None
        monkeypatch.setattr("agent.permission.isolation.isolation_decision",
                            lambda *a, **kw: SimpleNamespace(allowed=True, reason=""))
        return executor

    def _call(self, executor, arguments):
        return executor._execute_tool(
            {"id": "call_1", "name": "reporting", "arguments": dict(arguments)})

    def test_a_declared_action_without_an_approval_never_reaches_the_tool(
            self, stack, monkeypatch):
        monkeypatch.setattr("config.conf", lambda: _policy(REQUIRED), raising=False)
        tool = _Recorder()
        executor = self._executor(tool, monkeypatch)
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            result = self._call(executor, {"path": "/tmp/x"})
        assert result["status"] == "error"
        assert tool.calls == [], "a refused call must produce no side effect"
        assert "审批" in result["result"] or "approval" in result["result"].lower()

    def test_the_same_action_runs_when_its_approval_matches(self, stack, monkeypatch):
        monkeypatch.setattr("config.conf", lambda: _policy(REQUIRED), raising=False)
        requested = _request(stack, REQUIRED, target="",
                             parameters={"path": "/tmp/x"})
        _approve(stack, requested["id"])
        tool = _Recorder()
        executor = self._executor(tool, monkeypatch)
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            result = self._call(executor,
                                {"path": "/tmp/x", APPROVAL_ARGUMENT: requested["id"]})
        assert result["status"] == "success", result
        # The tool saw its own parameters and nothing of the transport.
        assert tool.calls == [{"path": "/tmp/x"}]
        # ...and the approval is burnt: the same reference cannot run it twice.
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            again = self._call(executor,
                               {"path": "/tmp/x", APPROVAL_ARGUMENT: requested["id"]})
        assert again["status"] == "error"
        assert tool.calls == [{"path": "/tmp/x"}], "a replay must not run again"

    def test_an_undeclared_action_still_runs_with_a_recorded_basis(
            self, stack, monkeypatch):
        """The default policy is "not applicable" — and it says why."""
        monkeypatch.setattr("config.conf", lambda: {}, raising=False)
        tool = _Recorder()
        executor = self._executor(tool, monkeypatch)
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            result = self._call(executor, {"path": "/tmp/x"})
        assert result["status"] == "success", result
        assert tool.calls == [{"path": "/tmp/x"}]
        assert approval_decision(tool_action_id("reporting"), config={}).basis

    def test_a_tool_that_could_not_be_reached_is_reported_as_an_error(
            self, stack, monkeypatch):
        """A refusal is a normal tool error: the model reads why and can stop."""
        monkeypatch.setattr("config.conf", lambda: _policy(REQUIRED), raising=False)
        tool = _Recorder()
        executor = self._executor(tool, monkeypatch)
        with identity_scope(user_id="", tenant_id="", agent_id="primary"):
            result = self._call(executor, {"path": "/tmp/x"})
        assert result["status"] == "error"
        assert tool.calls == []


class TestSchedulerDeliverySeam:
    """``_execute_send_message`` refuses before it creates a channel."""

    def _task(self, content="hello", receiver="ops", channel="web"):
        return {"id": "t-1",
                "action": {"type": "send_message", "content": content,
                           "receiver": receiver, "channel_type": channel}}

    def test_a_declared_delivery_without_an_approval_delivers_nothing(
            self, stack, monkeypatch):
        from agent.tools.scheduler import integration as sched

        monkeypatch.setattr("config.conf",
                            lambda: _policy("scheduler:send_message"), raising=False)
        sent = []
        monkeypatch.setattr("channel.channel_factory.create_channel",
                            lambda channel_type: sent.append(channel_type) or None)
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            delivered = sched._execute_send_message(self._task(), None, "primary")
        assert delivered is False
        assert sent == [], "the refusal must happen before a channel is created"

    def test_the_approved_delivery_reaches_the_channel(self, stack, monkeypatch):
        from agent.tools.scheduler import integration as sched

        monkeypatch.setattr("config.conf",
                            lambda: _policy("scheduler:send_message"), raising=False)
        action = self._task()["action"]
        task = self._task()
        task["action"][APPROVAL_ARGUMENT] = _approve(
            stack,
            _request(stack, "scheduler:send_message", target="web:ops",
                     parameters=action)["id"])["id"]

        class _Channel:
            def __init__(self):
                self.replies = []

            def send(self, reply, context):
                self.replies.append((reply, context))
                return True

        channel = _Channel()
        monkeypatch.setattr("channel.channel_factory.create_channel",
                            lambda channel_type: channel)
        with identity_scope(user_id=stack.alice, tenant_id=stack.tenant, agent_id="primary"):
            delivered = sched._execute_send_message(task, None, "primary")
        assert delivered is True
        assert len(channel.replies) == 1

    def test_the_scheduler_action_records_its_non_applicable_basis(self):
        """``agent_task`` is the recorded "no approval" case for this class."""
        basis = action_basis("scheduler.agent_task", {})
        assert "own session" in basis
