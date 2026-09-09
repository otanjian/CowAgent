# encoding:utf-8
"""Scheduler owner snapshot + trigger-time revalidation (open-database-runtime 5.x).

A scheduled task is created by a tenant member under chat.use + the Agent's
``agent.use`` grant and fires later on a timer. These tests pin down that the
creator is snapshotted onto the task and that a fire is skipped (with the
reason persisted) once the member's membership or grants are gone — before
anything reaches the model/channel. Legacy tasks without an owner are untouched.
"""

import os

import pytest

from agent.tools.scheduler import identity as sid
from agent.tools.scheduler.identity import (
    AGENT_DENIED, AGENT_UNBOUND, CHAT_DENIED, NOT_MEMBER,
    PASSWORD_CHANGE_REQUIRED, owner_snapshot, revalidate_owner,
)


def _ready_member(service, root, tenant, username, roles, *, temp="TempPass123!"):
    """Create a member who completed the initial password change."""
    m = service.create_member(
        actor_user_id=root, tenant_id=tenant, operation="create-new",
        username=username, display_name=username.title(), temporary_password=temp,
        roles=list(roles),
    )["user_id"]
    token = service.login(username, temp).token
    service.change_password(token, temp, "MemberPass123!")
    return m


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from auth.service import IdentityService

    service = IdentityService(str(tmp_path / "identity.db"))
    tenant = service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "shared"), allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    op = service.create_role(
        actor_user_id=root, tenant_id=tenant, code="chat-op", name="Chat operator",
        permissions=["chat.use", "agent.use", "agent.read"],
        resource_grants=[{"resource_kind": "agent", "resource_id": "agent:shared-agent",
                          "action": "use"}],
    )
    member = _ready_member(service, root, tenant, "member", ["member", op["code"]])
    service.bind_agent(tenant_id=tenant, agent_id="shared-agent")
    monkeypatch.setattr("auth.service.get_identity_service", lambda: service)
    return {"service": service, "tenant": tenant, "root": root,
            "member": member, "op_role": op}


def _owner(svc, user_id=None):
    return {
        "user_id": user_id or svc["member"],
        "tenant_id": svc["tenant"],
        "agent_id": "shared-agent",
        "created_at": "2026-09-09T00:00:00",
    }


def _task(svc, **overrides):
    task = {
        "id": "task-1",
        "name": "reminder",
        "enabled": True,
        "schedule": {"type": "interval", "seconds": 3600},
        "action": {"type": "send_message", "content": "ping",
                   "receiver": "member", "channel_type": "web",
                   "notify_session_id": "sess-1"},
        "owner": _owner(svc),
    }
    task.update(overrides)
    return task


def test_legacy_task_without_owner_always_passes(svc):
    assert revalidate_owner(_task(svc, owner=None)) is None
    assert revalidate_owner(_task(svc, owner={})) is None
    assert revalidate_owner({}) is None


def test_active_member_with_grants_passes(svc):
    assert revalidate_owner(_task(svc)) is None


def test_platform_admin_owner_passes(svc):
    assert revalidate_owner(_task(svc, owner=_owner(svc, svc["root"]))) is None


def test_unbound_agent_reason(svc):
    task = _task(svc, owner=_owner(svc))
    task["owner"]["agent_id"] = "unbound-agent"
    assert revalidate_owner(task) == AGENT_UNBOUND


def test_agent_bound_to_other_tenant_reason(svc):
    # Owner belongs to acme but the target Agent is bound to another tenant —
    # the trigger must refuse to fire under the wrong tenant boundary.
    other = svc["service"].create_tenant(
        actor_user_id=svc["root"], code="other", name="Other", shared_root="",
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    svc["service"].bind_agent(tenant_id=other, agent_id="other-agent")
    task = _task(svc)
    task["owner"]["agent_id"] = "other-agent"
    assert revalidate_owner(task) == AGENT_UNBOUND


def test_password_change_pending_reason(svc):
    # A brand-new member who has not completed the initial password change is
    # denied, exactly like the Web chat gate.
    created = svc["service"].create_member(
        actor_user_id=svc["root"], tenant_id=svc["tenant"], operation="create-new",
        username="fresh", display_name="Fresh", temporary_password="TempPass123!",
        roles=["member"],
    )
    task = _task(svc, owner=_owner(svc, created["user_id"]))
    assert revalidate_owner(task) == PASSWORD_CHANGE_REQUIRED


def test_member_of_other_tenant_not_member(svc):
    other = svc["service"].create_tenant(
        actor_user_id=svc["root"], code="other", name="Other", shared_root="",
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    foreign = _ready_member(svc["service"], svc["root"], other, "foreign", ["member"])
    task = _task(svc, owner=_owner(svc, foreign))
    assert revalidate_owner(task) == NOT_MEMBER


def test_missing_chat_use_reason(svc):
    plain = _ready_member(svc["service"], svc["root"], svc["tenant"], "plain", ["member"])
    task = _task(svc, owner=_owner(svc, plain))
    assert revalidate_owner(task) == CHAT_DENIED


def test_agent_grant_revoked_denied_before_fire(svc):
    # The central 撤权 test: revoke the agent.use resource grant; the next fire
    # must be skipped with AGENT_DENIED even though chat.use is untouched.
    assert revalidate_owner(_task(svc)) is None
    role = [r for r in svc["service"].list_roles(svc["tenant"]) if r["code"] == "chat-op"][0]
    svc["service"].update_role(
        svc["root"], svc["tenant"], role["id"], "Chat operator",
        ["chat.use", "agent.use", "agent.read"], expected_version=role["version"],
        resource_grants=[], model_defaults={},
    )
    assert revalidate_owner(_task(svc)) == AGENT_DENIED
    # Re-granting restores the fire (immediate effect, no restart needed).
    role = [r for r in svc["service"].list_roles(svc["tenant"]) if r["code"] == "chat-op"][0]
    svc["service"].update_role(
        svc["root"], svc["tenant"], role["id"], "Chat operator",
        ["chat.use", "agent.use", "agent.read"], expected_version=role["version"],
        resource_grants=[{"resource_kind": "agent", "resource_id": "agent:shared-agent",
                          "action": "use"}],
        model_defaults={},
    )
    assert revalidate_owner(_task(svc)) is None


def test_owner_snapshot_only_for_db_identity(svc):
    from common.runtime_identity import RuntimeIdentity, use_identity

    with use_identity(RuntimeIdentity(agent_id="shared-agent")):
        assert owner_snapshot({}) is None  # legacy: no user/tenant
    with use_identity(RuntimeIdentity(
        agent_id="shared-agent", user_id=svc["member"], tenant_id=svc["tenant"],
        session_id="sess-1",
    )):
        owner = owner_snapshot({"agent_id": "shared-agent", "session_id": "sess-1"})
        assert owner == {
            "user_id": svc["member"], "tenant_id": svc["tenant"],
            "agent_id": "shared-agent", "session_id": "sess-1",
            "created_at": owner["created_at"],
        }


def test_execute_callback_skips_revoked_fire_and_records(svc, tmp_path, monkeypatch):
    """5.3: after grant revocation a fire returns True (no retry loop), records
    the reason, and never reaches the channel/bridge."""
    from agent.tools.scheduler.integration import _make_execute_callback
    from agent.tools.scheduler.scheduler_service import SchedulerService
    from agent.tools.scheduler.task_store import TaskStore

    store_path = str(tmp_path / "scheduler" / "tasks.json")
    store = TaskStore(store_path)
    bridge_calls = []
    callback = _make_execute_callback(_FakeBridge(bridge_calls), "shared-agent", store)
    service = SchedulerService(store, callback)

    task = _task(svc, id="revoked", action={
        "type": "noop", "receiver": "member", "channel_type": "unknown"})
    store.add_task(task)
    # allow baseline fire before revocation
    assert callback(task) is True
    assert not store.get_task("revoked").get("last_skip_reason")

    role = [r for r in svc["service"].list_roles(svc["tenant"]) if r["code"] == "chat-op"][0]
    svc["service"].update_role(
        svc["root"], svc["tenant"], role["id"], "Chat operator",
        ["chat.use", "agent.use", "agent.read"], expected_version=role["version"],
        resource_grants=[], model_defaults={},
    )
    assert service._execute_task(store.get_task("revoked")) is True
    stored = store.get_task("revoked")
    assert stored["last_skip_reason"] == AGENT_DENIED
    assert stored.get("last_skip_at")
    assert "[agent_denied]" in stored["last_error"]
    assert bridge_calls == []  # nothing reached the channel/bridge
    # the schedule survives: re-enabling is still possible for recurring tasks
    assert stored["enabled"] is True


class _FakeBridge:
    def __init__(self, calls):
        self.calls = calls

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError(f"bridge must not be reached, touched {name}")
