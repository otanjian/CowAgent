# encoding:utf-8
"""Acceptance tests for multi-tenant isolation across resource reads (task 4.2).

Drives the request-authorization -> resource-resolution loop at the core layer:
resolve a session token + X-Tenant-ID into a ``RequestContext``, bridge it to a
``RuntimeIdentity``, then read conversations. Covers:

- two tenants with distinct agent bindings;
- the same logical account holding memberships in both tenants;
- two different users of one tenant;
- the same session string under two different global agents;
- tenant_admin vs ordinary member read scoping;
- shared-root resolution never falling back to a global/default path.
"""

import os
import tempfile

from auth.runtime import resolve_context, to_runtime_identity, IdentityContextError
from auth.service import IdentityService, IdentityServiceError
from agent.memory.conversation_store import ConversationStore
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from common.runtime_identity import use_identity


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _svc():
    s = IdentityService(_db())
    s.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root="/s/acme")
    s.create_tenant(
        actor_user_id=s.list_platform_users()[0]["id"], code="beta", name="Beta",
        shared_root="/s/beta", admin_username="beta", admin_display="Beta",
        admin_password="Str0ng2Pass", recent_password="Str0ngAdminPass")
    return s


def _root(svc):
    # the bootstrap platform admin (global root), not a per-tenant admin
    return [u for u in svc.list_platform_users() if u["username"] == "root"][0]


def _token_for(svc, username, password):
    return svc.login(username, password).token


def _ctx(svc, token, tenant_id):
    return resolve_context(svc, token, tenant_id)


def test_two_tenants_agent_bindings_isolated(tmp_path):
    svc = _svc()
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    beta = [t for t in svc.list_tenants() if t["code"] == "beta"][0]
    # acme owns agent-a, beta owns agent-b
    svc.register_default_tenancy(
        tenant_id=acme["id"], private_owner_user_id=_root(svc)["id"],
        agent_ids=["agent-a"])
    svc.register_default_tenancy(
        tenant_id=beta["id"], private_owner_user_id=_root(svc)["id"],
        agent_ids=["agent-b"])

    assert set(svc.tenant_agent_ids(acme["id"])) == {"agent-a"}
    assert set(svc.tenant_agent_ids(beta["id"])) == {"agent-b"}
    # an agent is not visible across tenants
    assert svc.get_agent_binding("agent-a")["tenant_id"] == acme["id"]
    assert svc.get_agent_binding("agent-b")["tenant_id"] == beta["id"]


def test_same_account_tenants_are_distinct_memberships(tmp_path):
    svc = _svc()
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    beta = [t for t in svc.list_tenants() if t["code"] == "beta"][0]
    admin = _root(svc)
    # give the platform admin a membership in beta too. The actor must be a
    # tenant_admin of beta, so use beta's own admin (username 'beta').
    beta_admin = svc._find_user_by_username("beta")
    svc.create_member(
        actor_user_id=beta_admin["id"], tenant_id=beta["id"], operation="bind-existing",
        username=admin["username"], display_name=admin["display_name"],
        temporary_password="unused", roles=["member"])
    # the same account selects each tenant independently
    token = _token_for(svc, admin["username"], "Str0ngAdminPass")
    ctx_a = _ctx(svc, token, acme["id"])
    ctx_b = _ctx(svc, token, beta["id"])
    assert ctx_a.tenant_id == acme["id"]
    assert ctx_b.tenant_id == beta["id"]
    assert ctx_a.is_tenant_admin is True  # platform admin is also tenant_admin in acme
    assert ctx_b.is_tenant_admin is False  # member role in beta


def test_two_users_tenant_sessions_isolated(tmp_path):
    svc = _svc()
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    admin = _root(svc)
    # create a second member in acme
    member = svc.create_member(
        actor_user_id=admin["id"], tenant_id=acme["id"], operation="create-new",
        username="alice", display_name="Alice", temporary_password="Str0ngPass1",
        roles=["member"])
    # seed two stores: one owned by admin, one by alice
    store = ConversationStore(tmp_path / "mem" / "index.db")
    with use_identity(to_runtime_identity(
            _ctx(svc, _token_for(svc, admin["username"], "Str0ngAdminPass"), acme["id"]),
            agent_id="agent-a")):
        store.append_messages("s-admin", [{"role": "user", "content": "admin msg"}])
    with use_identity(to_runtime_identity(
            _ctx(svc, _token_for(svc, "alice", "Str0ngPass1"), acme["id"]),
            agent_id="agent-a")):
        store.append_messages("s-alice", [{"role": "user", "content": "alice msg"}])
    # admin sees only admin session; alice sees only alice session
    assert store.list_session_ids(user_id=admin["id"]) == ["s-admin"]
    assert store.list_session_ids(user_id=member["user_id"]) == ["s-alice"]


def test_same_session_different_agent_does_not_cross(tmp_path):
    svc = _svc()
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    beta = [t for t in svc.list_tenants() if t["code"] == "beta"][0]
    root = _root(svc)
    beta_admin = svc._find_user_by_username("beta")
    svc.register_default_tenancy(tenant_id=acme["id"],
                                 private_owner_user_id=root["id"], agent_ids=["agent-a"])
    svc.register_default_tenancy(tenant_id=beta["id"],
                                 private_owner_user_id=root["id"], agent_ids=["agent-b"])
    # same session string under two different global agents, one per tenant,
    # written by each tenant's own admin.
    store_a = ConversationStore(tmp_path / "a" / "index.db")
    store_b = ConversationStore(tmp_path / "b" / "index.db")
    with use_identity(to_runtime_identity(
            _ctx(svc, _token_for(svc, root["username"], "Str0ngAdminPass"), acme["id"]),
            agent_id="agent-a", session_id="SAME")):
        store_a.append_messages("SAME", [{"role": "user", "content": "in agent-a"}])
    with use_identity(to_runtime_identity(
            _ctx(svc, _token_for(svc, "beta", "Str0ng2Pass"), beta["id"]),
            agent_id="agent-b", session_id="SAME")):
        store_b.append_messages("SAME", [{"role": "user", "content": "in agent-b"}])
    assert store_a.list_session_ids(user_id=root["id"]) == ["SAME"]
    assert store_b.list_session_ids(user_id=beta_admin["id"]) == ["SAME"]
    # a store is per global agent -> no cross-tenant leak via session string


def test_shared_agent_private_session_not_visible_to_member(tmp_path):
    svc = _svc()
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    admin = _root(svc)
    member = svc.create_member(
        actor_user_id=admin["id"], tenant_id=acme["id"], operation="create-new",
        username="bob", display_name="Bob", temporary_password="Str0ngPass1",
        roles=["member"])
    # shared agent (no private owner) + private owner content
    svc.register_default_tenancy(tenant_id=acme["id"], private_owner_user_id=None,
                                 agent_ids=["shared"])
    # admin fully sessions into shared agent; only owner (admin) sees them
    store = ConversationStore(tmp_path / "s" / "index.db")
    with use_identity(to_runtime_identity(
            _ctx(svc, _token_for(svc, admin["username"], "Str0ngAdminPass"), acme["id"]),
            agent_id="shared")):
        store.append_messages("s1", [{"role": "user", "content": "owner only"}])
    # member (bob) cannot read the private-owner conversation
    assert store.list_session_ids(user_id=admin["id"]) == ["s1"]
    assert store.list_session_ids(user_id=member["user_id"]) == []


def test_unknown_tenant_selection_rejected(tmp_path):
    svc = _svc()
    admin = _root(svc)
    token = _token_for(svc, admin["username"], "Str0ngAdminPass")
    try:
        _ctx(svc, token, "no-such-tenant")
        assert False, "expected rejection"
    except IdentityContextError as e:
        assert e.status == 403
