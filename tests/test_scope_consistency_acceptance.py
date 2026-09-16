# encoding:utf-8
"""Scope consistency across the new and legacy surfaces (change
``unify-console-by-data-scope``, task 2.5).

Task 2.1 moved every object question into :mod:`auth.object_scope`; task 2.4 moved
the legacy personal menu grants onto the formal pages. This file is the
*acceptance* half: it drives the real WSGI application — gate, resolver, storage
and identity service together — and pins the properties the two tasks promise,
because a predicate that is right in isolation still leaks if one caller asks a
different question:

* **cross-user refusal** — a member never reads, edits or even learns the
  identifier of another member's private Agent;
* **cross-tenant refusal** — a foreign tenant's member cannot address this
  tenant's objects at all;
* **no invisible statistics** — the list a caller receives is the list they may
  reach; the count is not padded with rows they cannot see;
* **forged ownership** — a request body may *name* a target but can never name
  its own ``tenant``/``owner``/``scope``; those come from the verified session;
* **revalidation at submit time** — a grant, an owner or a permission revoked
  after the page was read refuses the write, so the write never trusts the read;
* **migration interruption and audit failure** — a migration that fails leaves no
  partial state and is retried on the next open, and an identity write is rolled
  back when its audit event cannot be recorded (they share one transaction).
"""

from __future__ import annotations

import json

from unittest.mock import patch

import pytest

from tests._helpers import IdentityStack

MEMBER = IdentityStack.MEMBER_PASSWORD


def _status(response) -> int:
    return int(response.status.split()[0])


def _body(response) -> dict:
    return json.loads(response.data.decode("utf-8"))


class _World:
    """One tenant with a shared Agent, two members owning private Agents, an
    administrator, and a second tenant with its own member.

    The **roster is faked**. ``AgentsHandler.POST`` writes through
    ``AgentAdminService(<data root>/config.json)``, and ``get_data_root()``
    resolves outside this harness, so a real save would rewrite the developer's
    own ``team.json``. Every write test here is about *who is allowed through the
    gate*, so the recorder stands in for the store: a refusal must leave it empty,
    which is the property under test, and a permitted save must reach it, which is
    the non-vacuity control.
    """

    def __init__(self, harness):
        self.h = harness
        self.tenant_id = harness.tenant_id
        harness.add_agent("shared-agent", "alice-agent", "bob-agent")
        self.alice = harness.member("alice", ["member"])
        self.bob = harness.member("bob", ["member"])
        self.admin = harness.member("admin", ["tenant_admin"])
        self.alice_token = harness.login("alice")
        self.bob_token = harness.login("bob")
        self.admin_token = harness.login("admin")
        harness.service.bind_agent(tenant_id=self.tenant_id, agent_id="alice-agent",
                                   private_owner_user_id=self.alice)
        harness.service.bind_agent(tenant_id=self.tenant_id, agent_id="bob-agent",
                                   private_owner_user_id=self.bob)
        # A shared Agent is reachable for *use* only through an explicit grant,
        # which is a separate fact from ownership (see the read/write tests).
        # ``read``/``use`` are granted, ``edit`` deliberately is not: that is what
        # makes "the read range is not the write range" observable below.
        self.grant_agent_access("shared-agent", "read", "use")
        other = harness.stack.other_tenant(code="globex", username="glenda")
        self.other_tenant = other["tenant_id"]
        # ``other_tenant`` provisions its member through the service directly, so
        # the harness never recorded the password; read it from the fixture
        # constant rather than the per-username map.
        self.glenda_token = harness.service.login(
            "glenda", IdentityStack.MEMBER_PASSWORD).token
        self.written = []
        self._install_fake_roster()

    # -- fixture wiring ------------------------------------------------------

    def _install_fake_roster(self):
        import channel.web.web_channel as web_channel

        recorder = self

        class _FakeRoster:
            def update_agent(self, agent_id, **fields):
                recorder.written.append((agent_id, fields))
                return {"id": agent_id}

            def archive_agent(self, agent_id, **kwargs):
                recorder.written.append((agent_id, {"archive": True}))
                return {"id": agent_id}

            def delete_agent(self, agent_id, **kwargs):
                recorder.written.append((agent_id, {"delete": True}))
                return {"id": agent_id}

            def snapshot(self):
                return {"revision": "rev-1", "agents": [], "channel_instances": []}

        self.fake_roster = _FakeRoster()
        self._patches = [
            patch.object(web_channel, "_agent_admin_service",
                         lambda: self.fake_roster),
        ]
        for name in ("_reload_agent_runtime", "_drop_agent_runtime"):
            if hasattr(web_channel, name):
                self._patches.append(
                    patch.object(web_channel, name, lambda *a, **k: None))
        for patcher in self._patches:
            patcher.start()

    def close(self):
        for patcher in reversed(getattr(self, "_patches", [])):
            patcher.stop()

    # -- requests ------------------------------------------------------------

    def agents(self, token, **kwargs):
        response = self.h.get("/api/agents", token=token, **kwargs)
        assert _status(response) == 200, response.data
        return _body(response)

    def ids(self, token, **kwargs):
        return [a["id"] for a in self.agents(token, **kwargs)["agents"]]

    def use_ids(self, token, **kwargs):
        """The workbench (use) projection: the range a chat may start from."""
        response = self.h.get("/api/agents?view=workbench", token=token, **kwargs)
        assert _status(response) == 200, response.data
        return [a["id"] for a in _body(response)["agents"]]

    def update(self, token, agent_id, **body):
        payload = {"action": "update", "id": agent_id}
        payload.update(body)
        return self.h.post("/api/agents", payload, token=token)

    def binding(self, agent_id):
        return self.h.service.get_agent_binding(agent_id)

    def set_role_permissions(self, code, permissions):
        row = [r for r in self.h.service.list_roles(self.tenant_id)
               if r["code"] == code][0]
        return self.h.service.update_role(
            actor_user_id=self.h.admin_id, tenant_id=self.tenant_id,
            role_id=row["id"], name=row["name"], permissions=list(permissions),
            expected_version=row["version"], resource_grants=row["resource_grants"])

    def grant_agent_access(self, agent_id, *actions, role_code="member"):
        """Give the role explicit ``agent:<id>`` grants for *actions*."""
        row = [r for r in self.h.service.list_roles(self.tenant_id)
               if r["code"] == role_code][0]
        grants = list(row["resource_grants"])
        for action in actions:
            entry = {"resource_kind": "agent", "resource_id": f"agent:{agent_id}",
                     "action": action}
            if entry not in grants:
                grants.append(entry)
        return self.h.service.update_role(
            actor_user_id=self.h.admin_id, tenant_id=self.tenant_id,
            role_id=row["id"], name=row["name"], permissions=row["permissions"],
            expected_version=row["version"], resource_grants=grants)


@pytest.fixture
def world(web_app):
    built = _World(web_app("scope-consistency"))
    yield built
    built.close()


# --- cross-user refusal -----------------------------------------------------

def test_a_members_management_list_is_their_own_private_agents(world):
    """管理 is ``tenant=T AND owner=U``: a shared Agent is not a management row.

    The two members' pages must not be interchangeable, which is what makes the
    shared/private split observable at the surface and not only in the predicate.
    """
    assert world.ids(world.alice_token) == ["alice-agent"]
    assert world.ids(world.bob_token) == ["bob-agent"]


def test_the_shared_agent_is_usable_by_both_members(world):
    """The *use* range is wider than the management range, by design."""
    assert "shared-agent" in world.use_ids(world.alice_token)
    assert "shared-agent" in world.use_ids(world.bob_token)
    assert "bob-agent" not in world.use_ids(world.alice_token)


def test_another_members_private_agent_does_not_appear_even_as_an_identifier(world):
    """No identifier, no count: the row is absent, not flagged."""
    payloads = [
        world.h.get("/api/agents", token=world.alice_token).data.decode("utf-8"),
        world.h.get("/api/agents?view=workbench",
                    token=world.alice_token).data.decode("utf-8"),
        world.h.get("/api/agents?view=personal",
                    token=world.alice_token).data.decode("utf-8"),
    ]
    for payload in payloads:
        assert "bob-agent" not in payload, payload

    agents = _body(world.h.get("/api/agents", token=world.alice_token))["agents"]
    assert len(agents) == 1, "the length is the length this caller may manage"


def test_a_member_cannot_edit_another_members_private_agent(world):
    response = world.update(world.alice_token, "bob-agent", name="renamed")
    assert _status(response) == 403, response.data
    assert world.written == [], "a refused save must not reach the roster"
    assert world.binding("bob-agent")["private_owner_user_id"] == world.bob


def test_a_member_cannot_delete_another_members_private_agent(world):
    response = world.h.post("/api/agents", {"action": "delete", "id": "bob-agent"},
                            token=world.alice_token)
    assert _status(response) == 403, response.data
    assert world.written == [], "a refused delete must not reach the roster"
    assert world.binding("bob-agent") is not None
    assert world.binding("bob-agent")["private_owner_user_id"] == world.bob


def test_a_member_cannot_make_another_members_agent_shared(world):
    """Ownership is not a body field: naming it must not transfer the object."""
    response = world.update(world.alice_token, "bob-agent",
                            private_owner_user_id=None, scope="tenant")
    assert _status(response) == 403, response.data
    assert world.written == [], "a refused save must not reach the roster"
    assert world.binding("bob-agent")["private_owner_user_id"] == world.bob


def test_the_tenant_admin_administers_the_shared_surface_not_the_private_one(world):
    """The administrator sees shared Agents, and no member's private one."""
    assert sorted(world.ids(world.admin_token)) == ["shared-agent"]


def test_the_tenant_admin_cannot_edit_a_members_private_agent(world):
    response = world.update(world.admin_token, "alice-agent", name="renamed")
    assert _status(response) == 403, response.data
    assert world.written == [], "a refused save must not reach the roster"
    assert world.binding("alice-agent")["private_owner_user_id"] == world.alice


def test_the_owner_still_edits_their_own_private_agent(world):
    """Non-vacuity: the refusals above are not a blanket denial."""
    response = world.update(world.alice_token, "alice-agent", name="Alice Assistant")
    assert _status(response) == 200, response.data
    assert _body(response).get("status") == "success", response.data
    assert world.written[-1][0] == "alice-agent"
    assert world.written[-1][1].get("name") == "Alice Assistant"


# --- page eligibility is not management qualification -----------------------

def test_an_open_console_does_not_open_role_management(world):
    """正式页面资格 ≠ 管理资格: the org/role endpoints keep their own gate."""
    response = world.h.post("/api/tenant/roles",
                            {"code": "makers", "name": "Makers",
                             "permissions": ["agent.read"]},
                            token=world.alice_token)
    assert _status(response) in (401, 403), response.data
    codes = [r["code"] for r in world.h.service.list_roles(world.tenant_id)]
    assert "makers" not in codes


def test_a_public_edit_grant_is_not_management_qualification(world):
    """A custom role's public ``edit`` grant alone must not confer management.

    Task 2.2: a member may be granted the *functional* public-skill edit right
    (``skill.edit``) and even an ``edit`` grant on a public skill id, and still
    not hold the public-configuration management qualification — that comes from
    the administration role, on its own.
    """
    from auth.object_scope import ObjectScope
    from auth.runtime import member_context

    world.grant_agent_access("shared-agent", "edit")
    row = [r for r in world.h.service.list_roles(world.tenant_id)
           if r["code"] == "member"][0]
    grants = list(row["resource_grants"]) + [
        {"resource_kind": "skill", "resource_id": "skill:public-skill",
         "action": "edit"}]
    world.h.service.update_role(
        actor_user_id=world.h.admin_id, tenant_id=world.tenant_id,
        role_id=row["id"], name=row["name"], permissions=row["permissions"],
        expected_version=row["version"], resource_grants=grants)

    ctx = member_context(world.h.service, world.alice, world.tenant_id)
    assert ObjectScope.from_context(ctx).allows_public_configuration() is False
    assert world.h.service.check_resource_action(
        world.alice, world.tenant_id, "skill", "skill:public-skill", "edit",
        permission="skill.edit") is True
    admin_ctx = member_context(world.h.service, world.admin, world.tenant_id)
    assert ObjectScope.from_context(admin_ctx).allows_public_configuration() is True


# --- cross-tenant refusal ---------------------------------------------------

def test_a_foreign_member_cannot_select_this_tenant(world):
    response = world.h.get("/api/agents", token=world.glenda_token,
                           headers={"X-Tenant-ID": world.tenant_id})
    assert _status(response) in (403, 404), response.data
    assert "alice-agent" not in response.data.decode("utf-8")


def test_a_foreign_member_never_receives_this_tenants_agents(world):
    """Whatever globex answers, the payload names nothing of acme's."""
    response = world.h.get("/api/agents", token=world.glenda_token,
                           headers={"X-Tenant-ID": world.other_tenant})
    payload = response.data.decode("utf-8")
    assert "alice-agent" not in payload
    assert "shared-agent" not in payload


def test_an_agent_of_another_tenant_is_not_addressable(world):
    response = world.update(world.glenda_token, "alice-agent", name="renamed",
                            headers={"X-Tenant-ID": world.other_tenant})
    assert _status(response) in (403, 404), response.data
    assert world.binding("alice-agent")["private_owner_user_id"] == world.alice


# --- revalidation at submit time --------------------------------------------

def test_a_write_refuses_when_the_object_stopped_being_the_callers(world):
    """The page was read while the Agent was hers; by submit time it is shared."""
    assert "alice-agent" in world.ids(world.alice_token)

    world.h.service.make_agent_tenant_shared(agent_id="alice-agent",
                                             actor_user_id=world.h.admin_id)

    response = world.update(world.alice_token, "alice-agent", name="renamed")
    assert _status(response) == 403, response.data
    assert world.h.service.get_agent_binding("alice-agent")["private_owner_user_id"] is None


def test_a_write_refuses_when_the_functional_permission_was_withdrawn(world):
    """Ownership alone is not enough: the write re-checks the permission too."""
    role = [r for r in world.h.service.list_roles(world.tenant_id)
            if r["code"] == "member"][0]
    permissions = [p for p in role["permissions"] if p != "agent.edit"]
    world.set_role_permissions("member", permissions)

    response = world.update(world.alice_token, "alice-agent", name="renamed")
    assert _status(response) == 403, response.data


def test_the_read_range_is_not_the_write_range(world):
    """Being able to *see* or *use* an object never grants the write by itself."""
    assert "shared-agent" in world.use_ids(world.alice_token)
    response = world.update(world.alice_token, "shared-agent", name="renamed")
    assert _status(response) == 403, response.data
    assert world.written == [], "a refused save must not reach the roster"


# --- forged ownership -------------------------------------------------------

def test_a_body_cannot_move_the_target_to_another_tenant(world):
    """The body's ``tenant_id`` is not a field the roster write ever sees."""
    response = world.update(world.alice_token, "alice-agent", name="ok",
                            tenant_id=world.other_tenant)
    assert _body(response).get("status") == "success", response.data
    assert world.written[-1][0] == "alice-agent"
    assert "tenant_id" not in world.written[-1][1]
    binding = world.binding("alice-agent")
    assert binding["tenant_id"] == world.tenant_id, "the tenant came from the session"
    assert binding["private_owner_user_id"] == world.alice


# --- migration interruption and audit failure -------------------------------

class _InterruptedMigrationTests:
    """Driven without the web app; see the module-level cases below."""


def test_a_failed_migration_leaves_no_partial_state_and_is_retried(tmp_path):
    from auth.store import IdentityStore, _migrations

    path = str(tmp_path / "identity.db")
    real = _migrations[-1]
    seen = {"runs": 0}

    def failing(con):
        seen["runs"] += 1
        # A migration is one transaction: this half is written before the raise,
        # so the rollback is what the assertion below actually observes.
        con.execute("CREATE TABLE partial_state(x TEXT)")
        raise RuntimeError("interrupted")

    def working(con):
        con.execute("CREATE TABLE completed_state(x TEXT)")

    _migrations[-1] = failing
    try:
        with pytest.raises(RuntimeError):
            IdentityStore(path)
    finally:
        _migrations[-1] = working
    try:
        store = IdentityStore(path)
        with store.connect() as con:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            versions = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        assert "partial_state" not in tables, "a failed migration must roll back"
        assert "completed_state" in tables
        assert len(versions) == len(set(versions))
    finally:
        _migrations[-1] = real
    assert seen["runs"] == 1


def test_an_identity_write_rolls_back_when_its_audit_event_fails(world):
    """The write and its audit event commit together or not at all.

    The default pointer is a *change* (from the shared Agent to her own), so the
    service is committed to appending an audit event; making that append fail must
    take the pointer change down with it, otherwise the console would show a
    default nothing can explain.
    """
    from auth.service import IdentityService, IdentityServiceError

    svc = world.h.service
    svc.set_member_default_agent(tenant_id=world.tenant_id, user_id=world.alice,
                                 agent_id="shared-agent")
    before = svc.member_default_agent_id(world.tenant_id, world.alice)
    audits_before = [row for row in svc.list_audit(world.tenant_id)
                     if row.get("action") == "member.set_default_agent"]

    def _boom(*args, **kwargs):
        raise IdentityServiceError("audit unavailable", code="audit_failed",
                                   status=503)

    original = IdentityService._audit_in_tx
    IdentityService._audit_in_tx = _boom
    try:
        with pytest.raises(IdentityServiceError):
            svc.set_member_default_agent(
                tenant_id=world.tenant_id, user_id=world.alice,
                agent_id="alice-agent")
    finally:
        IdentityService._audit_in_tx = original

    assert svc.member_default_agent_id(world.tenant_id, world.alice) == before
    audits_after = [row for row in svc.list_audit(world.tenant_id)
                    if row.get("action") == "member.set_default_agent"]
    assert len(audits_after) == len(audits_before)
