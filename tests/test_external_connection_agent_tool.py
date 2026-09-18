# encoding:utf-8
"""External capabilities as agent tools.

Change ``add-external-system-access``, task 11.x boundary: the agent-side tool
is the *last* gate before an external action runs, and these tests pin the four
properties that make it safe to hand to a model:

* the model-visible name is namespaced, so an external capability cannot shadow
  a built-in tool;
* a call under no trusted identity is refused, and no parameter can supply one —
  the tenant and user come from the runtime, never from the call;
* a refusal arriving as an authorization error is reported as a refusal with its
  stable code, not as an empty success;
* ``outcome_unknown`` is never rendered as success, because a remote write that
  may or may not have landed must not be reported as done.

The declaration/dispatch pair itself is covered in
``tests/test_external_tool_registry.py``.
"""

from __future__ import annotations

import pytest

from agent.tools.external.external_tool import (
    TOOL_PREFIX,
    ExternalConnectionTool,
    external_tools_for,
    reconcile_external_tools,
)
from common.runtime_identity import RuntimeIdentity, use_identity
from integrations.external import tools as external_tools
from integrations.external.adapters import base as adapter_base
from integrations.external.errors import forbidden


class _FakeAdapter(adapter_base.ConnectionAdapter):
    """A stand-in adapter: the tool layer is under test, not the transport."""

    kind = "fake"
    actions = frozenset({"read_thing", "write_thing"})
    write_actions = frozenset({"write_thing"})

    def probe(self, ctx):
        return adapter_base.probe_ok()

    def invoke(self, ctx, action, params):
        if action == "write_thing":
            return adapter_base.invoke_unknown("timeout", message="no answer")
        return adapter_base.invoke_ok({"action": action, "params": dict(params)})


def _binding(name="fake.read_thing", *, write=False, connection_id="conn_1",
             connection_name="示例系统"):
    return external_tools.ToolBinding(
        tool=external_tools.ExternalTool(
            name=name, kind="fake", action=name.split(".", 1)[1], write=write),
        connection_id=connection_id, connection_name=connection_name,
        scope="tenant")


def _as_tool(binding):
    return ExternalConnectionTool(binding)


# -- naming ---------------------------------------------------------------

def test_the_model_sees_a_namespaced_name():
    """The prefix is what keeps an external name from colliding with a built-in."""
    tool = _as_tool(_binding())
    assert tool.name == TOOL_PREFIX + "fake.read_thing"
    assert not tool.name.startswith("mcp_")


def test_a_write_action_says_so_in_its_description():
    """The model is told before it calls: a write is not discovered afterwards."""
    read = _as_tool(_binding())
    write = _as_tool(_binding("fake.write_thing", write=True))
    assert "只读" in read.description
    assert "修改" in write.description


def test_the_tool_declares_itself_self_authorized():
    """The per-call authorization replaces the coarse grant, not duplicates it.

    ``agent``'s dispatch skips ``tool.execute`` for a self-authorized tool
    because the tool refuses on its own; leaving the flag off would make the
    *grant* the answer to "is this allowed now" instead of the re-derived
    permission, which is the confusion the per-call check exists to remove.
    """
    assert _as_tool(_binding()).self_authorized is True


# -- identity -------------------------------------------------------------

def test_a_call_without_a_trusted_identity_is_refused(monkeypatch):
    """No identity, no call — and the refusal is a result, not an exception."""
    called = []

    def _never(*args, **kwargs):
        called.append(args)
        raise AssertionError("dispatch must not run without an identity")

    monkeypatch.setattr(external_tools, "dispatch", _never)
    with use_identity(RuntimeIdentity()):
        result = _as_tool(_binding()).execute({})
    assert result.status == "error"
    assert not called


def test_a_tenant_without_a_user_is_refused():
    """A half-resolved identity is not an identity."""
    with use_identity(RuntimeIdentity(tenant_id="tenant-1")):
        result = _as_tool(_binding()).execute({})
    assert result.status == "error"
    assert "身份" in str(result.result)


def test_no_parameter_can_supply_the_identity(monkeypatch):
    """Model-supplied ``tenant_id``/``user_id`` are ordinary params, ignored.

    The point is not that they are rejected loudly but that they are not *read*:
    the dispatch receives the runtime's identity no matter what the call says.
    """
    seen = {}

    def _capture(service, name, params, *, tenant_id, actor_user_id, **kwargs):
        seen["tenant_id"] = tenant_id
        seen["actor_user_id"] = actor_user_id
        seen["params"] = params
        return adapter_base.invoke_ok({"echo": dict(params)})

    monkeypatch.setattr(external_tools, "dispatch", _capture)
    monkeypatch.setattr(
        "auth.service.get_identity_service", lambda: object())
    with use_identity(RuntimeIdentity(tenant_id="tenant-1", user_id="user-1")):
        result = _as_tool(_binding()).execute(
            {"tenant_id": "other-tenant", "user_id": "other-user"})
    assert result.status == "success"
    assert seen["tenant_id"] == "tenant-1"
    assert seen["actor_user_id"] == "user-1"
    # The params are forwarded verbatim; the identity keys are simply not
    # interpreted here, so the adapter decides what to do with them.
    assert seen["params"]["tenant_id"] == "other-tenant"


# -- refusal rendering ----------------------------------------------------

def test_an_authorization_refusal_keeps_its_stable_code(monkeypatch):
    """A caller branches on ``code``; paraphrasing it here would break that."""
    def _refuse(*args, **kwargs):
        raise forbidden("connection is not open for this actor",
                        code="connection_not_open")

    monkeypatch.setattr(external_tools, "dispatch", _refuse)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: object())
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        result = _as_tool(_binding()).execute({})
    assert result.status == "error"
    assert result.result["code"] == "connection_not_open"
    assert "not open" in result.result["message"]


def test_a_refusal_without_a_code_is_still_a_refusal(monkeypatch):
    """An unclassified failure must not read as success with no data."""
    def _boom(*args, **kwargs):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(external_tools, "dispatch", _boom)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: object())
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        result = _as_tool(_binding()).execute({})
    assert result.status == "error"
    assert result.result["code"] == "unavailable"


# -- outcome_unknown ------------------------------------------------------

def test_an_unknown_outcome_is_not_reported_as_success(monkeypatch):
    """The remote side may have applied the write; the model must not assume.

    ``InvokeResult.ok`` is False for ``outcome_unknown`` too, which is exactly
    why this asserts on the *status* the model sees and not only on the flag: a
    tool that mapped "not False" to success would report a state nobody
    confirmed.
    """
    def _unknown(*args, **kwargs):
        return adapter_base.invoke_unknown(
            "timeout", message="远端未在期限内应答")

    monkeypatch.setattr(external_tools, "dispatch", _unknown)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: object())
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        result = _as_tool(_binding("fake.write_thing", write=True)).execute({})
    assert result.status == "error"
    assert result.result["outcome_unknown"] is True
    assert result.result["message"] == "远端未在期限内应答"


def test_a_successful_invoke_returns_the_adapter_payload(monkeypatch):
    monkeypatch.setattr(
        external_tools, "dispatch",
        lambda *a, **k: adapter_base.invoke_ok({"rows": [1, 2]}))
    monkeypatch.setattr("auth.service.get_identity_service", lambda: object())
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        result = _as_tool(_binding()).execute({"table": "T001"})
    assert result.status == "success"
    assert result.result["ok"] is True
    assert result.result["data"] == {"rows": [1, 2]}


# -- building and reconciling ---------------------------------------------

class _AllowAll:
    """An identity service that grants everything: the stub for wrapping tests."""

    def check_resource_action(self, *args, **kwargs) -> bool:
        return True

    def tenant_admin_may_execute_tool(self, *args, **kwargs) -> bool:
        return False


class _StubConnectionService:
    """A connection service whose identity grants everything.

    ``authorized_tools`` reads the grant from the *connection* service's
    identity — the same database the built-in providers read their connections
    from — so stubbing that one accessor stubs both halves together, and a
    stubbed provider cannot end up paired with a real, unrelated identity store.
    """

    _identity = _AllowAll()


def _install(monkeypatch, bindings):
    monkeypatch.setattr(
        external_tools, "available_tools", lambda **kwargs: list(bindings))
    # The wrapper also passes every binding through the resource-execution
    # authorization before offering it (``integrations.external.authorization``).
    # These tests are about the *wrapping* — naming, reconcile, the sync seam —
    # and they run under synthetic ids ("t"/"u") that no identity database
    # knows, so the grant decision is stubbed to "allowed" here. That decision
    # itself is tested against a real store in
    # ``tests/test_external_authorization.py``. What must not happen is for a
    # stubbed *provider* to be the only thing standing between a caller and a
    # connection, which is why the same accessor stubs both.
    monkeypatch.setattr(
        "integrations.external.service.get_external_connection_service",
        lambda: _StubConnectionService())


def test_a_bad_declaration_costs_only_itself(monkeypatch):
    """One unbindable declaration must not empty the actor's tool list."""
    original = ExternalConnectionTool

    class _Exploding(original):
        def __init__(self, binding):
            raise ValueError("this binding cannot be built")

    monkeypatch.setattr(
        "agent.tools.external.external_tool.ExternalConnectionTool",
        _Exploding)
    _install(monkeypatch, [_binding()])
    assert external_tools_for(tenant_id="t", actor_user_id="u") == {}


def test_building_tools_stores_nothing_globally(monkeypatch):
    """The result is returned, never published.

    ``ToolManager`` is a process-wide singleton and the capability set is per
    actor, so storing one actor's tools in the manager would leave them in the
    next actor's list until something overwrote them. The caller binds the
    result to the object whose lifetime matches.
    """
    _install(monkeypatch, [_binding()])
    built = external_tools_for(tenant_id="t", actor_user_id="u")
    assert set(built) == {TOOL_PREFIX + "fake.read_thing"}
    assert isinstance(built[TOOL_PREFIX + "fake.read_thing"],
                      ExternalConnectionTool)


def test_reconcile_adds_the_new_and_removes_the_gone(monkeypatch):
    """The whole decision is returned in one shot, so no caller applies half."""
    _install(monkeypatch, [_binding(), _binding("fake.write_thing", write=True)])
    wanted_src = external_tools_for(tenant_id="t", actor_user_id="u")
    stale = {TOOL_PREFIX + "fake.vanished"}
    wanted, added, removed = reconcile_external_tools(stale, wanted_src)
    assert set(wanted) == {TOOL_PREFIX + "fake.read_thing",
                           TOOL_PREFIX + "fake.write_thing"}
    assert added == sorted(wanted)
    assert removed == [TOOL_PREFIX + "fake.vanished"]


def test_reconcile_applies_the_callers_filter(monkeypatch):
    """A denied capability is neither offered nor reported as a removal."""
    _install(monkeypatch, [_binding(), _binding("fake.write_thing", write=True)])
    wanted_src = external_tools_for(tenant_id="t", actor_user_id="u")
    wanted, added, removed = reconcile_external_tools(
        set(), wanted_src, allowed={TOOL_PREFIX + "fake.read_thing"})
    assert set(wanted) == {TOOL_PREFIX + "fake.read_thing"}
    assert added == [TOOL_PREFIX + "fake.read_thing"]
    assert removed == []


def test_reconcile_never_removes_something_it_did_not_own(monkeypatch):
    """A built-in tool name is never in the external set to begin with."""
    _install(monkeypatch, [_binding()])
    wanted_src = external_tools_for(tenant_id="t", actor_user_id="u")
    existing_external = {TOOL_PREFIX + "fake.read_thing"}
    wanted, added, removed = reconcile_external_tools(
        existing_external, wanted_src)
    assert added == [] and removed == []
    assert set(wanted) == existing_external


def test_reconcile_with_no_identity_offers_nothing(monkeypatch):
    """An empty result is how the caller learns to remove the previous set."""
    _install(monkeypatch, [])
    wanted, added, removed = reconcile_external_tools(
        {TOOL_PREFIX + "fake.read_thing"}, {})
    assert wanted == {}
    assert added == []
    assert removed == [TOOL_PREFIX + "fake.read_thing"]


# -- the ToolManager seam -------------------------------------------------

class _Agent:
    """The two shapes RongAI uses, plus the profile the policy reads."""

    def __init__(self, tools=None, profile=None, restricted=False):
        self.tools = {} if tools is None else tools
        self.agent_profile = profile
        if restricted:
            self._evolution_restricted = True


def _manager():
    from agent.tools import ToolManager
    return ToolManager()


def test_sync_adds_external_tools_to_a_dict_shaped_agent(monkeypatch):
    _install(monkeypatch, [_binding()])
    agent = _Agent(tools={"read": object()})
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        added, removed = _manager().sync_external_into_agent(agent)
    assert added == [TOOL_PREFIX + "fake.read_thing"]
    assert removed == []
    assert TOOL_PREFIX + "fake.read_thing" in agent.tools
    # A built-in tool is untouched.
    assert "read" in agent.tools


def test_sync_appends_to_a_list_shaped_agent(monkeypatch):
    _install(monkeypatch, [_binding()])
    builtin = object()
    agent = _Agent(tools=[builtin])
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        _manager().sync_external_into_agent(agent)
    assert builtin is agent.tools[0]
    assert [t.name for t in agent.tools[1:]] == [TOOL_PREFIX + "fake.read_thing"]


def test_a_later_sync_removes_a_revoked_capability(monkeypatch):
    """Revocation takes effect at the next turn, not at the next restart."""
    _install(monkeypatch, [_binding()])
    agent = _Agent()
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        _manager().sync_external_into_agent(agent)
    assert TOOL_PREFIX + "fake.read_thing" in agent.tools

    _install(monkeypatch, [])
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        added, removed = _manager().sync_external_into_agent(agent)
    assert added == []
    assert removed == [TOOL_PREFIX + "fake.read_thing"]
    assert agent.tools == {}


def test_a_turn_without_an_identity_clears_the_previous_set(monkeypatch):
    """The manager is shared; a leftover tool must not survive into a new turn.

    This is the isolation property: external tools are the one kind of tool
    whose *existence* depends on who is asking, so a turn that cannot name an
    actor gets an empty set rather than the last actor's.
    """
    _install(monkeypatch, [_binding()])
    agent = _Agent()
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        _manager().sync_external_into_agent(agent)
    with use_identity(RuntimeIdentity()):
        added, removed = _manager().sync_external_into_agent(agent)
    assert added == []
    assert agent.tools == {}


def test_the_review_agent_never_receives_an_external_tool(monkeypatch):
    """The reduced Self-Evolution toolset is a boundary, not a default."""
    _install(monkeypatch, [_binding()])
    agent = _Agent(restricted=True)
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        added, removed = _manager().sync_external_into_agent(agent)
    assert (added, removed) == ([], [])
    assert agent.tools == {}


def test_a_denied_external_tool_is_not_added(monkeypatch):
    """A profile that denies a tool denies it here too.

    Otherwise the external entrance would be the way around a scene's or an
    employee profile's tool policy (spec: 不通过其他工具入口绕过).
    """
    from agent.registry import AgentProfile

    _install(monkeypatch, [_binding()])
    profile = AgentProfile(
        id="a", name="a", workspace="/tmp",
        tools_denylist=(TOOL_PREFIX + "fake.read_thing",))
    agent = _Agent(profile=profile)
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        added, removed = _manager().sync_external_into_agent(agent)
    assert (added, removed) == ([], [])
    assert agent.tools == {}


def test_an_allowlist_keeps_only_the_listed_external_tool(monkeypatch):
    from agent.registry import AgentProfile

    _install(monkeypatch, [_binding(), _binding("fake.write_thing", write=True)])
    profile = AgentProfile(
        id="a", name="a", workspace="/tmp",
        tools_allowlist=(TOOL_PREFIX + "fake.read_thing",))
    agent = _Agent(profile=profile)
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        added, removed = _manager().sync_external_into_agent(agent)
    assert added == [TOOL_PREFIX + "fake.read_thing"]
    assert set(agent.tools) == {TOOL_PREFIX + "fake.read_thing"}


def test_an_agent_without_a_tools_attribute_is_ignored():
    """A non-Agent caller gets a no-op, not an AttributeError."""
    assert _manager().sync_external_into_agent(object()) == ([], [])


# -- the write path is closed by default ---------------------------------

def test_a_write_tool_refuses_when_no_approval_travels_with_the_call(monkeypatch):
    """A write from the agent path is refused, not silently run.

    Two independent gates guard an external write, and this pins the second
    one: ``agent``'s approval gate needs the deployment to *declare* the action
    (``approval_required_actions``), while the risk catalogue inside
    ``ConnectionRuntime.invoke`` refuses any approval-requiring action with no
    approval bound to it. So a deployment that forgot to declare an external
    write still does not get an unattended write — it gets a refusal.

    This is also why the agent-side tool passes no ``approval``: there is
    nothing legitimate for it to carry until write classes have their own
    acceptance evidence (task 9.8), and passing a model-supplied value through
    would be the way around the gate rather than through it.
    """
    from integrations.external.errors import forbidden as _forbidden

    def _refuse_write(service, name, params, *, tenant_id, actor_user_id,
                      **kwargs):
        raise _forbidden("risk gate refused %s" % name,
                         code="approval_required")

    monkeypatch.setattr(external_tools, "dispatch", _refuse_write)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: object())
    tool = _as_tool(_binding("fake.write_thing", write=True))
    with use_identity(RuntimeIdentity(tenant_id="t", user_id="u")):
        result = tool.execute({"to": "someone@example.com"})
    assert result.status == "error"
    assert result.result["code"] == "approval_required"


# -- the real adapter registry is importable -----------------------------

def test_one_broken_adapter_module_does_not_disable_the_rest(monkeypatch):
    """A defective adapter must cost its own kind, not every kind.

    This is the failure mode that turns a local defect into a global outage: if
    ``load_adapters`` let an import error through, *one* adapter module would
    empty the external tool list for the whole build. The registry keeps the
    reason instead, so a skipped kind can be reported by name.
    """
    import importlib

    from integrations.external.adapters import base as real_base

    def _import(module_path):
        if module_path == "explodes_at_import_time":
            raise SyntaxError("this is not python(")
        if module_path == "absent_module_for_this_build":
            raise ImportError("no such module")
        raise AssertionError("unexpected import of %r" % module_path)

    monkeypatch.setattr(real_base, "_builtin_adapters",
                        lambda: {"broken": "explodes_at_import_time",
                                 "fake": "absent_module_for_this_build"})
    monkeypatch.setattr(importlib, "import_module", _import)
    monkeypatch.setattr(real_base, "_IMPORT_ERRORS", {})
    monkeypatch.setattr(real_base, "_IMPORT_TRACEBACKS", {})

    real_base.load_adapters()  # must not raise
    assert real_base.adapter_import_error("broken").startswith("SyntaxError")
    # The absent module is explained separately from the broken one.
    assert real_base.adapter_import_error("fake").startswith("import_failed")


def test_the_real_provider_loader_does_not_raise():
    """``load_providers`` imports every adapter module; a broken import here
    would empty the tool list globally, so it is asserted on its own."""
    external_tools.load_providers()


@pytest.fixture(autouse=True)
def _clean_providers():
    yield
    external_tools.reset()
