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
from auth.service import IdentityService, IdentityServiceError


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


# --- the inverse repair: giving an Agent its owner back ------------------

def _restore(env, agent_id, owner, actor=None, dry_run=False):
    return env.svc.restore_private_agent_owner(
        agent_id=agent_id, owner_user_id=owner,
        actor_user_id=actor or env.root_user["id"], reason="test",
        dry_run=dry_run)


def _audit_rows(svc, action):
    return [dict(r) for r in svc._store.execute(  # type: ignore[attr-defined]
        "SELECT * FROM audit_events WHERE action=?", (action,))]


def test_restoring_an_owner_narrows_the_read_range_again(env):
    """The repair that undoes an accidental share, and it is audited.

    Making an Agent tenant-shared widens who can read it, and that act is
    explicit and audited — so the inverse has to exist too, for the case where
    the widening was never intended. Narrowing a range is not something to do
    silently either, hence the audit event.
    """
    other = env.svc.create_member(
        actor_user_id=env.root_user["id"], tenant_id=env.tid,
        operation="create-new", username="other", display_name="Other",
        temporary_password="TmpPass123!", roles=[])["user_id"]
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")
    other_ctx = RequestContext(
        user_id=other, username="other", display_name="Other",
        is_platform_admin=False, must_change_password=False,
        tenant_id=env.tid, membership=None, permissions=set(), is_tenant_admin=False)
    # Before: shared, so any member may reach it.
    env.wc._require_private_owner(other_ctx, "agent-p")

    result = _restore(env, "agent-p", env.member_id)

    assert result["private_owner_user_id"] == env.member_id
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] == env.member_id
    events = _audit_rows(env.svc, "agent.restore_private_owner")
    assert len(events) == 1 and events[0]["result"] == "success"
    assert events[0]["target"] == "agent:agent-p"
    # After: the owner keeps it, everyone else is locked out again.
    env.wc._require_private_owner(env.member_ctx, "agent-p")
    with pytest.raises(web.HTTPError) as exc:
        env.wc._require_private_owner(other_ctx, "agent-p")
    assert exc.value.args[0] == "403 Forbidden"


def test_dry_run_previews_the_repair_without_writing_or_auditing(env):
    """An operator previews a range-narrowing repair before applying it.

    The repair locks other members out of an Agent, so the preview has to run
    the *same* checks the write runs — a preview that only looked plausible
    would let an operator approve a repair that then fails.
    """
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")

    preview = _restore(env, "agent-p", env.member_id, dry_run=True)

    assert preview["changed"] is True
    assert preview["dry_run"] is True
    assert preview["private_owner_user_id"] == env.member_id
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None
    assert _audit_rows(env.svc, "agent.restore_private_owner") == []


def test_a_dry_run_still_refuses_an_illegal_repair(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])

    with pytest.raises(IdentityServiceError) as caught:
        _restore(env, "agent-p", env.member_id, dry_run=True)

    assert caught.value.code == "agent_already_owned"


def test_restoring_refuses_an_agent_someone_else_already_owns(env):
    """A repair fills in a missing owner; it never transfers one."""
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])

    with pytest.raises(IdentityServiceError) as caught:
        _restore(env, "agent-p", env.member_id)

    assert caught.value.code == "agent_already_owned"
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] \
        == env.root_user["id"]
    assert _audit_rows(env.svc, "agent.restore_private_owner") == []


def test_restoring_is_idempotent(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")
    _restore(env, "agent-p", env.member_id)

    again = _restore(env, "agent-p", env.member_id)

    assert again.get("changed") is False
    assert len(_audit_rows(env.svc, "agent.restore_private_owner")) == 1, (
        "a no-op repair must not append a second event")


def test_restoring_refuses_a_tenant_default(env):
    """A tenant default is the entry every member shares, so it stays shared.

    Restoring the owner on it would lock every other member out of the entry
    point the console offers them — the mirror image of the appointment rule.
    """
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-p", actor_user_id=env.root_user["id"])

    with pytest.raises(IdentityServiceError) as caught:
        _restore(env, "agent-p", env.member_id)

    assert caught.value.code == "agent_is_tenant_default"
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None
    assert env.svc.tenant_default_agent_id(env.tid) == "agent-p"


def test_restoring_requires_management_qualification(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")

    with pytest.raises(IdentityServiceError) as caught:
        _restore(env, "agent-p", env.member_id, actor=env.member_id)

    assert caught.value.status == 403
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None


def test_restoring_refuses_an_owner_outside_the_tenant(env):
    """Ownership grants a *read* right, so it cannot name a stranger."""
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p")
    other = env.svc.create_tenant(
        actor_user_id=env.root_user["id"], code="globex", name="Globex",
        shared_root=tempfile.mkdtemp(prefix="globex-"),
        recent_password="Str0ngAdminPass")
    outsider = env.svc.create_member(
        actor_user_id=env.root_user["id"], tenant_id=other["id"],
        operation="create-new", username="outsider", display_name="Outsider",
        temporary_password="TmpPass123!", roles=[])["user_id"]

    with pytest.raises(IdentityServiceError) as caught:
        _restore(env, "agent-p", outsider)

    assert caught.value.code == "not_found"
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None


def test_appointing_a_private_agent_as_default_is_refused(env):
    """A tenant default is a *shared* entry, but sharing stays an explicit act.

    A tenant default must be tenant-shared (``private_owner_user_id`` is an
    exclusive read gate, so a private default locks every other member out of
    the entry the console offers them). Appointment used to reach that state by
    clearing the owner, which silently published somebody's private Agent: it
    survived the appointment being reverted, and the Agent then appeared to
    every member in the chat picker as if it were a tenant-level one. The
    contract is now refusal — ``make_agent_tenant_shared`` is the explicit,
    audited way to share.
    """
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])

    with pytest.raises(IdentityServiceError) as caught:
        env.svc.appoint_tenant_default_agent(
            tenant_id=env.tid, agent_id="agent-p",
            actor_user_id=env.root_user["id"])

    assert caught.value.code == "private_agent_not_shareable"
    assert env.svc.tenant_default_agent_id(env.tid) is None, (
        "a refused appointment must not move the pointer")
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] \
        == env.root_user["id"], "the refusal must leave ownership untouched"
    assert not [dict(r) for r in env.svc._store.execute(  # type: ignore[attr-defined]
        "SELECT * FROM audit_events WHERE action='tenant.set_default_agent'"
        " AND result='success'")], "nothing was appointed, so nothing is audited"


def test_a_shared_agent_is_still_appointable_after_explicit_sharing(env):
    """Non-vacuity: the refusal is about ownership, not about appointing."""
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])
    env.svc.make_agent_tenant_shared(
        agent_id="agent-p", actor_user_id=env.root_user["id"])

    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-p", actor_user_id=env.root_user["id"])

    assert env.svc.tenant_default_agent_id(env.tid) == "agent-p"
    assert env.svc.get_agent_binding("agent-p")["private_owner_user_id"] is None


# --- the fallback never anchors on an exclusively-private Agent ----------

def test_fallback_prefers_a_tenant_shared_agent(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a",
                       private_owner_user_id=env.root_user["id"])
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-z")
    assert env.wc._resolve_tenant_default_agent(env.admin_ctx) == "agent-z", (
        "the Agent-less entry anchored on a private Agent while a shared one existed")


# --- correcting existing rows -------------------------------------------

def test_repair_releases_an_illegal_pointer_and_keeps_the_owner(env):
    """Legacy shape: a private Agent stored as the tenant default.

    The pointer is what is illegal — the default is the entry every member
    shares — so the repair releases the pointer and leaves ownership exactly as
    it was. Clearing the owner instead is what silently published a member's
    private Agent in the first place.
    """
    env.svc.bind_agent(tenant_id=env.tid, agent_id="default",
                       private_owner_user_id=env.root_user["id"])
    env.svc._store.execute(  # written directly: the write path refuses this now
        "UPDATE tenants SET default_agent_id='default' WHERE id=?", (env.tid,))

    first = env.svc.release_illegal_tenant_defaults()

    assert first["updated"] == 1
    assert env.svc.tenant_default_agent_id(env.tid) is None
    assert env.svc.get_agent_binding("default")["private_owner_user_id"] \
        == env.root_user["id"], "the repair must not publish a private Agent"
    events = [dict(r) for r in env.svc._store.execute(  # type: ignore[attr-defined]
        "SELECT * FROM audit_events WHERE action='tenant.default_agent.repaired'")]
    assert len(events) == 1 and events[0]["result"] == "success"
    assert env.root_user["id"] in events[0]["redacted_changes"]

    # Idempotent: a second run changes nothing.
    second = env.svc.release_illegal_tenant_defaults()
    assert second["updated"] == 0


def test_repair_leaves_non_default_private_agents_alone(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-shared")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-mine",
                       private_owner_user_id=env.member_id)
    env.svc._appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-shared", actor_user_id=env.root_user["id"])

    summary = env.svc.release_illegal_tenant_defaults()

    assert summary["updated"] == 0
    # A private Agent that is not the tenant default keeps its owner...
    assert env.svc.get_agent_binding("agent-mine")["private_owner_user_id"] == env.member_id
    # ...and the legal default stays appointed.
    assert env.svc.tenant_default_agent_id(env.tid) == "agent-shared"


def test_repair_ignores_a_private_agent_that_only_resolves(env):
    """No stored pointer, so there is no default to repair — and nothing to share.

    Resolution already prefers a shared candidate for the tenant's Agent-less
    entry, so a private Agent that merely sits first among the bindings is not a
    default that needs correcting; turning it into a tenant-level Agent here is
    what used to leak it into every member's picker.
    """
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a",
                       private_owner_user_id=env.root_user["id"])

    summary = env.svc.release_illegal_tenant_defaults()

    assert summary["updated"] == 0
    assert env.svc.get_agent_binding("agent-a")["private_owner_user_id"] \
        == env.root_user["id"]
