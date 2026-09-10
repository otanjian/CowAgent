# encoding:utf-8
"""A tenant's default Agent must be tenant-shared (change personal-conversation-and-memory).

``private_owner_user_id`` is an *exclusive* read gate: when it is set, only that
owner and a tenant admin may open its memory, and the check runs on the chat
authorize path. So an Agent marked private can still be a tenant's default —
and then every other member is locked out of the very entry the console
promises them.

Two writes used to infer a private owner from whoever happened to act:

* ``register_default_tenancy`` stamped the initial admin on every legacy Agent;
* ``_adopt_created_agent_for_tenant`` stamped the creating user on every new one.

Ownership is a deliberate act, so it must be set explicitly and never inferred.
These tests pin the invariant: a default Agent is shared, private ownership is
explicit, and the existing rows can be corrected idempotently.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import web

from auth.runtime import RequestContext
from auth.service import IdentityService


@pytest.fixture()
def env(monkeypatch):
    import types

    from channel.web import web_channel as wc

    db = os.path.join(tempfile.mkdtemp(), "identity.db")
    svc = IdentityService(db)
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=tempfile.mkdtemp(prefix="shared-"), allow_weak=True)
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users()[0]
    member = svc.create_member(
        actor_user_id=root["id"], tenant_id=tid, operation="create-new",
        username="plain", display_name="Plain",
        temporary_password="TmpPass123!", roles=[])["user_id"]

    monkeypatch.setattr("auth.service.get_identity_service", lambda: svc)
    web.ctx.headers = []

    admin_ctx = RequestContext(
        user_id=root["id"], username="root", display_name="Root",
        is_platform_admin=False, must_change_password=False,
        tenant_id=tid, membership=None, permissions=set(), is_tenant_admin=True)
    member_ctx = RequestContext(
        user_id=member, username="plain", display_name="Plain",
        is_platform_admin=False, must_change_password=False,
        tenant_id=tid, membership=None, permissions=set(), is_tenant_admin=False)

    return types.SimpleNamespace(
        svc=svc, tid=tid, root_user=root, member_id=member, wc=wc,
        admin_ctx=admin_ctx, member_ctx=member_ctx)


# --- ownership is not inferred ------------------------------------------

def test_adopting_a_new_agent_binds_it_tenant_shared(env):
    env.wc._adopt_created_agent_for_tenant(env.admin_ctx, "agent-new")
    binding = env.svc.get_agent_binding("agent-new")
    assert binding["tenant_id"] == env.tid
    assert binding["private_owner_user_id"] is None, (
        "a freshly created Agent was marked private to its creator; ownership "
        "must be an explicit act, not a side effect of who clicked create")


def test_adopted_first_agent_is_the_default_and_usable_by_members(env):
    env.wc._adopt_created_agent_for_tenant(env.admin_ctx, "agent-first")
    assert env.wc._resolve_tenant_default_agent(env.admin_ctx) == "agent-first"
    # The plain member must be able to open the default entry.
    env.wc._require_private_owner(env.member_ctx, "agent-first")


# --- ownership stays enforced where it is explicit ----------------------

def test_explicitly_private_agent_still_locks_out_members(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-private",
                       private_owner_user_id=env.root_user["id"])
    with pytest.raises(web.HTTPError) as exc:
        env.wc._require_private_owner(env.member_ctx, "agent-private")
    assert exc.value.args[0] == "403 Forbidden"


# --- an explicit way to make an Agent tenant-shared ---------------------

def test_make_tenant_shared_clears_an_existing_owner(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a",
                       private_owner_user_id=env.root_user["id"])
    # ``bind_agent`` cannot clear an owner, only repair a missing one.
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a",
                       private_owner_user_id=None)
    assert env.svc.get_agent_binding("agent-a")["private_owner_user_id"] == env.root_user["id"]

    env.svc.make_agent_tenant_shared(
        agent_id="agent-a", actor_user_id=env.root_user["id"])
    assert env.svc.get_agent_binding("agent-a")["private_owner_user_id"] is None
    env.wc._require_private_owner(env.member_ctx, "agent-a")


def test_appointing_a_private_agent_as_default_makes_it_shared(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-p", actor_user_id=env.root_user["id"])
    assert env.svc.tenant_default_agent_id(env.tid) == "agent-p"
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None, (
        "a tenant default must be tenant-shared, or members are locked out of it")


# --- the fallback never anchors on an exclusively-private Agent ----------

def test_fallback_prefers_a_tenant_shared_agent(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a",
                       private_owner_user_id=env.root_user["id"])
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-z")
    assert env.wc._resolve_tenant_default_agent(env.admin_ctx) == "agent-z", (
        "the Agent-less entry anchored on a private Agent while a shared one existed")


# --- correcting existing rows -------------------------------------------

def test_backfill_makes_resolved_defaults_shared_and_is_idempotent(env):
    # Mirrors the real install: legacy Agents stamped with the admin's id and
    # no configured tenant default.
    env.svc.bind_agent(tenant_id=env.tid, agent_id="default",
                       private_owner_user_id=env.root_user["id"])
    env.svc.bind_agent(tenant_id=env.tid, agent_id="erpnext",
                       private_owner_user_id=env.root_user["id"])

    first = env.svc.ensure_shared_default_agents()
    assert first["updated"] >= 1
    resolved = env.wc._resolve_tenant_default_agent(env.admin_ctx)
    assert resolved is not None
    assert env.svc.get_agent_binding(resolved)["private_owner_user_id"] is None
    env.wc._require_private_owner(env.member_ctx, resolved)

    # Idempotent: a second run changes nothing.
    second = env.svc.ensure_shared_default_agents()
    assert second["updated"] == 0


def test_backfill_leaves_non_default_private_agents_alone(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-shared")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-mine",
                       private_owner_user_id=env.member_id)
    env.svc._appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-shared", actor_user_id=env.root_user["id"])

    env.svc.ensure_shared_default_agents()
    # A private Agent that is not the tenant default keeps its owner.
    assert env.svc.get_agent_binding("agent-mine")["private_owner_user_id"] == env.member_id
