# encoding:utf-8
"""External actions are refused by default, through the real call path.

Change ``add-external-system-access``, group 9: the point of these tests is not
the risk catalogue's arithmetic (that is ``tests/test_external_risk.py``) but
that the only path to an external action actually consults it. The connection
is a real row, the service is the real
:class:`ExternalConnectionService` over a real ``identity.db``, the switch is
the real deployment switch, and the deployment is left at its shipped default —
so what is measured is what a deployment gets, not what a stub was told to do.

Three properties, each with its own reason:

* **Closed means closed.** At the shipped default, a declared action is refused
  with ``execution_not_available`` because no class is open — and the refusal
  happens *before* the adapter, so "the class is closed" cannot be defeated by
  an adapter that would have been happy to run.
* **A write needs an approval bound to it.** With the write class open, an
  approval-requiring action with no approval is refused, and an approval issued
  for different parameters does not authorise this call: the parameters are
  part of the digest, so an approval covers exactly the request it was issued
  for and is not a blank cheque.
* **A read is gated on the class, not on an approval.** Conflating the two
  would make the feature unusable and tempt someone to weaken the write gate.
"""

from __future__ import annotations

import pytest

from tests._helpers import build_identity

MASTER_KEY = "e2e-gate-master-key"

#: A real kind, so the real registry, the real type spec and the real
#: readiness switch are all exercised.
KIND = "mcp"
MCP_CONFIG = {
    "transport": "streamable_http", "url": "https://mcp.example.com/mcp",
}


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def svc(stack):
    from integrations.external.service import ExternalConnectionService
    return ExternalConnectionService(stack.service)


@pytest.fixture
def connection(svc, stack):
    return svc.create_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=stack.tenant_id,
        kind=KIND, name="Gate fake", config=MCP_CONFIG)


def _open(monkeypatch, *classes):
    """Open execution classes the way a deployment does: in configuration."""
    from config import conf
    monkeypatch.setitem(
        conf().setdefault("external_connections", {}),
        "readiness", {KIND: {name: True for name in classes}})


# -- closed by default ----------------------------------------------------

def test_nothing_is_open_at_the_shipped_default(svc, connection):
    """The default deployment serves `configure` and nothing else."""
    from integrations.external import registry
    assert registry.open_classes(KIND) == frozenset({"configure"})


def test_a_declared_read_is_refused_while_the_read_class_is_closed(
        svc, stack, connection):
    """An installed adapter is not a licence to run it."""
    result = svc.invoke_action(
        connection["id"], "tools.list", {},
        actor_user_id=stack.root, tenant_id=stack.tenant_id)
    assert result.ok is False
    assert result.code == "execution_not_available"
    assert result.stage == "policy"


def test_a_declared_write_is_refused_while_the_write_class_is_closed(
        svc, stack, connection):
    result = svc.invoke_action(
        connection["id"], "tools.call", {"name": "remote_tool", "arguments": {}},
        actor_user_id=stack.root, tenant_id=stack.tenant_id)
    assert result.ok is False
    assert result.code == "execution_not_available"


def test_an_undeclared_action_is_refused_before_any_class_check(
        svc, stack, connection, monkeypatch):
    """An action the adapter never declared has no class to open.

    Checked with a class open, so the answer can only come from the action
    list: the declaration is the first gate, and it is not skippable by
    configuration.
    """
    _open(monkeypatch, "read_execute")
    result = svc.invoke_action(
        connection["id"], "drop_everything", {},
        actor_user_id=stack.root, tenant_id=stack.tenant_id)
    assert result.ok is False
    assert result.code == "unsupported_action"
    assert result.stage == "config"


def test_the_effective_switch_follows_the_configuration(svc, connection, monkeypatch):
    """Opening a class is a configuration fact that takes effect immediately."""
    from integrations.external import registry
    _open(monkeypatch, "read_execute")
    assert "read_execute" in registry.open_classes(KIND)
    assert "write_execute" not in registry.open_classes(KIND)


def test_a_class_the_type_cannot_serve_is_not_openable(monkeypatch):
    """Configuration cannot open a class the type does not declare.

    MCP does not offer write execution, so asking for it is ignored rather than
    granted — the switch is a filter over what the type can do, not a way to
    invent a capability.
    """
    from integrations.external import registry
    _open(monkeypatch, "write_execute")
    assert "write_execute" not in registry.open_classes(KIND)
    assert registry.unavailable_reason(KIND, "write_execute") == \
        "not_supported_by_type"


# -- an approval is bound to the request it was issued for ----------------

def _decision(*, digest, approver_user_id, revoked=False, issued_at=None):
    """An approval record in the shape ``ApprovalDecision.parse`` reads."""
    import time
    return {
        "approved": True, "approver_user_id": approver_user_id,
        "digest": digest, "issued_at": issued_at or int(time.time()),
        "expires_at": 0, "revoked": revoked, "scope": "once",
    }


def test_the_risk_gate_refuses_a_write_with_no_approval(connection, stack):
    """The gate itself, called the way ``ConnectionRuntime.invoke`` calls it.

    The refusal must name the requirement — not a silent no-op, and not a
    pass-through to the adapter (which has no reachable target here, so
    reaching it would show up as a different stage).
    """
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", {"name": "remote_tool", "arguments": {}},
            connection_id=connection["id"], config_version=connection["version"],
            secret_versions={}, actor_user_id=stack.root,
            tenant_id=stack.tenant_id, approval=None)
    # The catalogue classifies an unknown MCP tool as a write, so this is an
    # approval refusal rather than the risk-not-acknowledged code.
    assert caught.value.code.startswith("approval_")


def test_an_approval_for_other_parameters_does_not_authorise_this_call(
        connection, stack):
    """The digest covers the parameters, so a re-used approval covers nothing.

    This is what makes an approval a statement about *one* request: issuing it
    for a harmless value must not authorise a different one.
    """
    from integrations.external import risk
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    approver = stack.member("approver", ["member"])
    approved_params = {"name": "remote_tool", "arguments": {"mode": "dry_run"}}
    binding = risk.approval_binding(
        kind=KIND, action="tools.call", connection_id=connection["id"],
        config_version=connection["version"], secret_versions={},
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        target=None, params=approved_params, key="")
    decision = _decision(digest=binding["digest"],
                         approver_user_id=approver)

    # The approved parameters pass the digest check...
    check_invocation(
        KIND, "tools.call", approved_params,
        connection_id=connection["id"], config_version=connection["version"],
        secret_versions={}, actor_user_id=stack.root,
        tenant_id=stack.tenant_id, approval=decision)

    # ...and a different request does not.
    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", {"name": "remote_tool",
                                 "arguments": {"mode": "live"}},
            connection_id=connection["id"], config_version=connection["version"],
            secret_versions={}, actor_user_id=stack.root,
            tenant_id=stack.tenant_id, approval=decision)
    assert caught.value.code.startswith("approval_")


def test_an_approval_bound_to_another_requester_is_refused(connection, stack):
    """The requester is in the binding, so an approval for a colleague's call
    does not authorise mine."""
    from integrations.external import risk
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    approver = stack.member("approver2", ["member"])
    requester = stack.member("requester2", ["member"])
    params = {"name": "remote_tool", "arguments": {}}
    # The binding names a *different* requester than the call below.
    binding = risk.approval_binding(
        kind=KIND, action="tools.call", connection_id=connection["id"],
        config_version=connection["version"], secret_versions={},
        actor_user_id=requester, tenant_id=stack.tenant_id,
        target=None, params=params, key="")
    decision = _decision(digest=binding["digest"],
                         approver_user_id=approver)
    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", params,
            connection_id=connection["id"], config_version=connection["version"],
            secret_versions={}, actor_user_id=stack.root,
            tenant_id=stack.tenant_id, approval=decision)
    assert caught.value.code.startswith("approval_")


def test_an_approval_for_another_connection_is_refused(connection, stack, svc):
    """The connection is in the binding too: approval for A is not approval for B."""
    from integrations.external import risk
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    approver = stack.member("approver3", ["member"])
    other = svc.create_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=stack.tenant_id,
        kind=KIND, name="Other MCP", config={
            "transport": "streamable_http",
            "url": "https://other.example.com/mcp"})
    params = {"name": "remote_tool", "arguments": {}}
    binding = risk.approval_binding(
        kind=KIND, action="tools.call", connection_id=other["id"],
        config_version=other["version"], secret_versions={},
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        target=None, params=params, key="")
    decision = _decision(digest=binding["digest"],
                         approver_user_id=approver)
    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", params,
            connection_id=connection["id"],
            config_version=connection["version"], secret_versions={},
            actor_user_id=stack.root, tenant_id=stack.tenant_id,
            approval=decision)
    assert caught.value.code.startswith("approval_")


def test_a_revoked_approval_is_refused(connection, stack):
    """A revoked approval stops working, and says so distinctively."""
    from integrations.external import risk
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    approver = stack.member("approver4", ["member"])
    params = {"name": "remote_tool", "arguments": {}}
    binding = risk.approval_binding(
        kind=KIND, action="tools.call", connection_id=connection["id"],
        config_version=connection["version"], secret_versions={},
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        target=None, params=params, key="")
    decision = _decision(digest=binding["digest"],
                         approver_user_id=approver, revoked=True)
    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", params,
            connection_id=connection["id"], config_version=connection["version"],
            secret_versions={}, actor_user_id=stack.root,
            tenant_id=stack.tenant_id, approval=decision)
    assert caught.value.code == "approval_revoked"


def test_the_approver_must_not_be_the_requester(connection, stack):
    """Separation of duties, stated as a refusal code of its own."""
    from integrations.external import risk
    from integrations.external.risk import check_invocation
    from integrations.external.errors import ExternalConnectionError

    params = {"name": "remote_tool", "arguments": {}}
    binding = risk.approval_binding(
        kind=KIND, action="tools.call", connection_id=connection["id"],
        config_version=connection["version"], secret_versions={},
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        target=None, params=params, key="")
    decision = _decision(digest=binding["digest"], approver_user_id=stack.root)
    with pytest.raises(ExternalConnectionError) as caught:
        check_invocation(
            KIND, "tools.call", params,
            connection_id=connection["id"], config_version=connection["version"],
            secret_versions={}, actor_user_id=stack.root,
            tenant_id=stack.tenant_id, approval=decision)
    assert caught.value.code == "approval_not_separated"
