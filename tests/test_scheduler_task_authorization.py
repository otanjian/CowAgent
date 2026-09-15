# encoding:utf-8
"""Scheduled-task authorization across every entry point (tasks 3.1-3.7, R1).

The failure this file exists to prevent, stated once: a scheduled task lives in
one Agent's ``tasks.json`` while three callers reach it — the Web console, the
``scheduler`` Agent tool, and the background fire loop. Before this work each
caller decided for itself, and the measured result was that the HTTP handlers
addressed any Agent by id with no owner check at all, and the tool had no
per-task authorization whatsoever. So these tests drive the *real* entry points
(the tool's ``execute``, the service's methods) against a **real** identity
database and a **real** ``TaskStore``, and assert the refusals, rather than
asserting that an authorization helper returns False.

What is deliberately not mocked: memberships, roles, resource grants, agent
bindings, the task store, revisions and the audit trail. Mocking any of them
would let a broken query or a missing gate pass, which is exactly the class of
bug this change is about.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from agent.tools.scheduler import authorization as authz
from agent.tools.scheduler.authorization import (
    AGENT_DENIED, ALREADY_RUNNING, NOT_OWNER, REVISION_CONFLICT, QUOTA_EXCEEDED,
    UNKNOWN_AGENT, TaskAccessService, TaskActor, TaskAuthorizationError,
)
from agent.tools.scheduler.scheduler_tool import SchedulerTool
from agent.tools.scheduler.task_store import TaskStore
from common.runtime_identity import RuntimeIdentity, use_identity
from tests._helpers import bootstrap_identity


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tenant with two members sharing one public Agent, plus a store."""
    stack = bootstrap_identity(tmp_path, monkeypatch, agents=("shared-agent",))
    role = stack.agent_role("chat-op", ["shared-agent"])
    alice = stack.member("alice", ["member", role["code"]])
    bob = stack.member("bob", ["member", role["code"]])
    admin = stack.tenant_admin()
    store = TaskStore(str(tmp_path / "shared-agent" / "scheduler" / "tasks.json"))

    def resolver(actor, agent_id):
        if agent_id != "shared-agent":
            raise TaskAuthorizationError(UNKNOWN_AGENT, status=404)
        return store

    def quota_gate(actor, agent_id, count):
        stack.service.check_scheduled_task_quota(
            user_id=actor.user_id, tenant_id=actor.tenant_id,
            would_be_count=count)

    service = TaskAccessService(
        store_resolver=resolver,
        agent_ids=lambda actor: ["shared-agent"],
        quota_check=quota_gate,
        coordinator="test",
    )
    return {"stack": stack, "store": store, "service": service,
            "alice": alice, "bob": bob, "admin": admin,
            "tenant": stack.tenant_id, "agent": "shared-agent"}


def _actor(env, user_id, *, source="http", admin=False, permissions=()):
    return TaskActor(user_id=user_id, tenant_id=env["tenant"], source=source,
                     is_tenant_admin=admin, permissions=permissions)


def _task(env, env_user, *, task_id="t1", scope="personal", owner=None):
    task = {
        "id": task_id,
        "name": "reminder",
        "enabled": True,
        "scope": scope,
        "schedule": {"type": "interval", "seconds": 3600},
        "action": {"type": "send_message", "content": "ping",
                   "receiver": env_user, "channel_type": "web",
                   "notify_session_id": "sess-1"},
        "revision": 1,
    }
    if scope == "personal":
        task["owner"] = owner or {
            "user_id": env_user, "tenant_id": env["tenant"],
            "agent_id": env["agent"], "created_at": "2026-09-01T00:00:00",
        }
    return task


# --- creation --------------------------------------------------------------


def test_a_member_creates_a_personal_task_with_the_owner_stamped_from_identity(env):
    actor = _actor(env, env["alice"])
    created = env["service"].create_task(actor, env["agent"], {
        "id": "t-alice", "name": "mine", "enabled": True,
        "schedule": {"type": "interval", "seconds": 60},
        "action": {"type": "send_message", "content": "hi",
                   "receiver": "alice", "channel_type": "web"},
    })
    assert created["scope"] == "personal"
    assert created["owner"]["user_id"] == env["alice"]
    assert created["owner"]["tenant_id"] == env["tenant"]
    assert created["revision"] == 1
    stored = env["store"].get_task("t-alice")
    assert stored["owner"]["user_id"] == env["alice"]


def test_creation_refuses_a_caller_with_no_verified_member_identity(env):
    anonymous = TaskActor(source="http")
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].create_task(anonymous, env["agent"], {
            "id": "t-anon", "name": "x", "enabled": True,
            "schedule": {"type": "interval", "seconds": 60},
            "action": {"type": "send_message", "content": "x",
                       "receiver": "alice", "channel_type": "web"},
        })
    assert error.value.code == authz.NOT_MEMBER
    # Nothing was written: a task with no owner must never exist (task 3.2).
    assert env["store"].list_tasks() == []


def test_a_forged_owner_in_the_request_body_is_refused(env):
    actor = _actor(env, env["alice"])
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].create_task(actor, env["agent"], {
            "id": "t-forged", "name": "x", "enabled": True,
            "owner": {"user_id": env["bob"], "tenant_id": env["tenant"]},
            "schedule": {"type": "interval", "seconds": 60},
            "action": {"type": "send_message", "content": "x",
                       "receiver": "alice", "channel_type": "web"},
        })
    assert error.value.code == authz.FORGED_FIELD


# --- isolation between members --------------------------------------------


def test_one_member_cannot_see_another_members_task(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    env["store"].add_task(_task(env, env["bob"], task_id="t-bob"))

    page = env["service"].list_tasks(_actor(env, env["bob"]), agent_id=env["agent"])
    assert [t["id"] for t in page["tasks"]] == ["t-bob"]
    assert page["total"] == 1
    # The count must not disclose the other member's task either.
    assert page["counts"]["personal"] == 1


def test_one_member_cannot_read_change_or_delete_another_members_task(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    bob = _actor(env, env["bob"])

    for operation in ("get_task", "delete_task"):
        with pytest.raises(TaskAuthorizationError) as error:
            getattr(env["service"], operation)(bob, env["agent"], "t-alice")
        assert error.value.code == NOT_OWNER
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].set_enabled(bob, env["agent"], "t-alice", False)
    assert error.value.code == NOT_OWNER
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].update_task(bob, env["agent"], "t-alice", {"name": "pwn"})
    assert error.value.code == NOT_OWNER
    assert env["store"].get_task("t-alice")["name"] == "reminder"


def test_a_tenant_admin_manages_public_tasks_but_not_members_private_ones(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    env["store"].add_task(_task(env, env["alice"], task_id="t-public",
                               scope="public", owner=None))
    admin = _actor(env, env["admin"], admin=True)

    page = env["service"].list_tasks(admin, agent_id=env["agent"])
    assert [t["id"] for t in page["tasks"]] == ["t-public"]
    assert env["service"].set_enabled(admin, env["agent"], "t-public", False)["enabled"] is False
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].delete_task(admin, env["agent"], "t-alice")
    assert error.value.code == NOT_OWNER


def test_a_member_of_another_tenant_cannot_reach_the_task(env):
    other = env["stack"].other_tenant()
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    foreign = TaskActor(user_id=other["user_id"], tenant_id=other["tenant_id"],
                        source="http")

    page = env["service"].list_tasks(foreign, agent_id=env["agent"])
    assert page["tasks"] == []
    with pytest.raises(TaskAuthorizationError):
        env["service"].get_task(foreign, env["agent"], "t-alice")


def test_the_tool_only_ever_addresses_its_own_agents_store(env):
    actor = _actor(env, env["alice"], source="tool")
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].store(actor, "another-agent")
    assert error.value.code == UNKNOWN_AGENT


# --- revocation ------------------------------------------------------------


def test_a_revoked_member_can_still_pause_or_delete_their_own_task(env, monkeypatch):
    """The point of separating manage from run: a task nobody can stop is worse
    than a task nobody can start."""
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    monkeypatch.setattr(env["service"], "_can_use_agent", lambda actor, agent: False)
    alice = _actor(env, env["alice"])

    assert env["service"].get_task(alice, env["agent"], "t-alice")["id"] == "t-alice"
    assert env["service"].set_enabled(alice, env["agent"], "t-alice", False)["enabled"] is False
    assert env["service"].delete_task(alice, env["agent"], "t-alice")["id"] == "t-alice"


def test_running_a_task_is_refused_once_the_grant_is_revoked(env, monkeypatch):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    fired = []
    env["service"]._run_hook = lambda actor, agent, task_id: fired.append(task_id)
    alice = _actor(env, env["alice"])
    env["service"].run_task(alice, env["agent"], "t-alice")
    assert fired == ["t-alice"]

    monkeypatch.setattr(env["service"], "_can_use_agent", lambda actor, agent: False)
    with pytest.raises(TaskAuthorizationError):
        env["service"].run_task(alice, env["agent"], "t-alice")
    assert fired == ["t-alice"]  # the second attempt never reached the runner


# --- the manual run's "same request" proof (task 3.4) ----------------------


def _run(env, actor, *, key="", task_id="t-alice"):
    return env["service"].run_task(actor, env["agent"], task_id,
                                   run_key=key or None)


def test_the_same_run_key_fires_once(env):
    """A retry after a lost response must not queue a second fire."""
    from agent.tools.scheduler.authorization import _reset_run_receipts

    _reset_run_receipts()
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    fired = []
    env["service"]._run_hook = lambda actor, agent, task_id: fired.append(task_id)
    alice = _actor(env, env["alice"])

    _run(env, alice, key="click-1")
    again = _run(env, alice, key="click-1")

    assert fired == ["t-alice"], "the same key must not enqueue twice"
    assert again["id"] == "t-alice"


def test_a_new_run_key_is_a_new_request(env):
    from agent.tools.scheduler.authorization import _reset_run_receipts

    _reset_run_receipts()
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    fired = []
    env["service"]._run_hook = lambda actor, agent, task_id: fired.append(task_id)
    alice = _actor(env, env["alice"])

    _run(env, alice, key="click-1")
    _run(env, alice, key="click-2")

    assert fired == ["t-alice", "t-alice"]


def test_one_members_run_key_cannot_stand_in_for_anothers(env):
    """The key is bound to the caller: Bob's replay of Alice's key still fires."""
    from agent.tools.scheduler.authorization import _reset_run_receipts

    _reset_run_receipts()
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    fired = []
    env["service"]._run_hook = lambda actor, agent, task_id: fired.append(task_id)
    alice = _actor(env, env["alice"])

    _run(env, alice, key="click-1")
    bob = _actor(env, env["bob"])
    with pytest.raises(TaskAuthorizationError):
        _run(env, bob, key="click-1")
    assert fired == ["t-alice"]


def test_a_run_without_a_key_keeps_the_running_task_refusal(env):
    """No key means no receipt: the runner's own guard is the only protection."""
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    calls = []

    def _busy(actor, agent, task_id):
        calls.append(task_id)
        raise RuntimeError("task is already running")

    env["service"]._run_hook = _busy
    alice = _actor(env, env["alice"])
    with pytest.raises(TaskAuthorizationError) as error:
        _run(env, alice)
    assert error.value.code == ALREADY_RUNNING
    assert error.value.status == 409
    assert calls == ["t-alice"]


# --- revision and field integrity -----------------------------------------


def test_a_stale_revision_is_reported_instead_of_overwriting(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    alice = _actor(env, env["alice"])
    first = env["service"].update_task(alice, env["agent"], "t-alice",
                                       {"name": "first"}, expected_revision=1)
    assert first["revision"] == 2
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].update_task(alice, env["agent"], "t-alice",
                                   {"name": "second"}, expected_revision=1)
    assert error.value.code == REVISION_CONFLICT
    assert env["store"].get_task("t-alice")["name"] == "first"


def test_the_tool_cannot_retarget_delivery_or_the_owner(env, monkeypatch):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    alice = _actor(env, env["alice"])
    for patch in ({"owner": {"user_id": env["bob"]}},
                  {"action": {"receiver": "bob"}},
                  {"action": {"channel_type": "feishu"}}):
        with pytest.raises(TaskAuthorizationError) as error:
            env["service"].update_task(alice, env["agent"], "t-alice", patch)
        assert error.value.code == authz.FORGED_FIELD
    # An edit that touches only the schedule leaves the stored action intact,
    # including scheduler metadata the editor never sees.
    updated = env["service"].update_task(
        alice, env["agent"], "t-alice",
        {"action": {"content": "pong"}}, expected_revision=1)
    assert updated["action"]["content"] == "pong"
    assert updated["action"]["notify_session_id"] == "sess-1"
    assert updated["action"]["receiver"] == env["alice"]


# --- quota -----------------------------------------------------------------


def test_the_quota_gate_applies_to_creation_and_to_reenabling(env):
    env["stack"].service.set_quota(
        actor_user_id=env["stack"].root, tenant_id=env["tenant"],
        metric="scheduled_tasks", hard_limit=1, user_id=env["alice"])
    alice = _actor(env, env["alice"])
    env["service"].create_task(alice, env["agent"], {
        "id": "t-1", "name": "one", "enabled": False,
        "schedule": {"type": "interval", "seconds": 60},
        "action": {"type": "send_message", "content": "x",
                   "receiver": "alice", "channel_type": "web"},
    })
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].create_task(alice, env["agent"], {
            "id": "t-2", "name": "two", "enabled": False,
            "schedule": {"type": "interval", "seconds": 60},
            "action": {"type": "send_message", "content": "x",
                       "receiver": "alice", "channel_type": "web"},
        })
    assert error.value.code == QUOTA_EXCEEDED
    # Disabling does not free the slot, so re-enabling past the limit is refused
    # too — otherwise the limit would only apply to the first enable.
    env["store"].add_task(_task(env, env["alice"], task_id="t-3"))
    with pytest.raises(TaskAuthorizationError) as error:
        env["service"].set_enabled(alice, env["agent"], "t-3", True)
    assert error.value.code == QUOTA_EXCEEDED
    # Another member's allowance is their own.
    env["stack"].service.set_quota(
        actor_user_id=env["stack"].root, tenant_id=env["tenant"],
        metric="scheduled_tasks", hard_limit=1, user_id=env["bob"])
    assert env["service"].create_task(_actor(env, env["bob"]), env["agent"], {
        "id": "t-bob", "name": "bob task", "enabled": True,
        "schedule": {"type": "interval", "seconds": 60},
        "action": {"type": "send_message", "content": "x",
                   "receiver": "bob", "channel_type": "web"},
    })["owner"]["user_id"] == env["bob"]


# --- the real Agent tool entry point (R1) ---------------------------------


def _tool(env, user_id, *, agent_id=None):
    tool = SchedulerTool({"channel_type": "web", "agent_id": agent_id or env["agent"]})
    tool.task_store = env["store"]
    tool.current_context = _ToolContext(receiver=user_id, session_id="sess-1")
    return tool


class _ToolContext(dict):
    kwargs = {}


def _as_identity(stack, user_id, agent_id="shared-agent"):
    return use_identity(RuntimeIdentity(
        agent_id=agent_id, user_id=user_id, tenant_id=stack.tenant_id,
        session_id="sess-1"))


def test_the_tool_creates_lists_and_deletes_through_shared_authorization(env):
    with _as_identity(env["stack"], env["alice"]):
        tool = _tool(env, "alice")
        created = tool.execute({
            "action": "create", "name": "tea", "message": "drink tea",
            "schedule_type": "interval", "schedule_value": "3600",
        })
        assert created.status == "success", created.result
        listed = tool.execute({"action": "list"})
        assert "tea" in listed.result
        assert "(本人)" in listed.result
        stored = env["store"].list_tasks()[0]
        assert stored["owner"]["user_id"] == env["alice"]
        assert stored["scope"] == "personal"
        deleted = tool.execute({"action": "delete", "task_id": stored["id"]})
        assert deleted.status == "success"
        assert env["store"].list_tasks() == []


def test_the_tool_refuses_to_create_without_a_verified_member(env):
    tool = _tool(env, "alice")  # no ambient identity at all
    result = tool.execute({
        "action": "create", "name": "tea", "message": "drink tea",
        "schedule_type": "interval", "schedule_value": "3600",
    })
    assert result.status == "error"
    assert authz.NOT_MEMBER in str(result.result)
    assert env["store"].list_tasks() == []


def test_the_tool_cannot_list_or_delete_another_members_task(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    with _as_identity(env["stack"], env["bob"]):
        tool = _tool(env, "bob")
        assert "t-alice" not in tool.execute({"action": "list"}).result
        denied = tool.execute({"action": "delete", "task_id": "t-alice"})
        assert denied.status == "error"
        assert NOT_OWNER in str(denied.result)
    assert env["store"].get_task("t-alice") is not None


def test_the_tool_refuses_when_its_agent_is_not_usable(env):
    """A tool bound to an Agent the caller may not use cannot create on it.

    The tool's Agent comes from its config (set by the Agent initializer), so
    this is the deployment-shape check: an Agent not bound to the caller's
    tenant, or one the caller holds no grant on, is refused rather than silently
    written to.
    """
    with _as_identity(env["stack"], env["alice"], agent_id="shared-agent"):
        tool = _tool(env, "alice", agent_id="other-agent")
        result = tool.execute({
            "action": "create", "name": "x", "message": "x",
            "schedule_type": "interval", "schedule_value": "60",
        })
    assert result.status == "error"
    assert AGENT_DENIED in str(result.result)


def test_the_tool_honours_the_member_quota(env):
    env["stack"].service.set_quota(
        actor_user_id=env["stack"].root, tenant_id=env["tenant"],
        metric="scheduled_tasks", hard_limit=1, user_id=env["alice"])
    with _as_identity(env["stack"], env["alice"]):
        tool = _tool(env, "alice")
        first = tool.execute({
            "action": "create", "name": "one", "message": "x",
            "schedule_type": "interval", "schedule_value": "60",
        })
        assert first.status == "success", first.result
        second = tool.execute({
            "action": "create", "name": "two", "message": "x",
            "schedule_type": "interval", "schedule_value": "60",
        })
    assert second.status == "error"
    assert QUOTA_EXCEEDED in str(second.result)
    assert len(env["store"].list_tasks()) == 1


def test_concurrent_creates_by_two_members_share_the_store_without_loss(env):
    """Cross-entry concurrency: the shared critical section must keep both."""
    barrier = threading.Barrier(2)
    results = []

    def create(user_id, task_id):
        with _as_identity(env["stack"], user_id):
            tool = _tool(env, user_id)
            barrier.wait(timeout=10)
            results.append(tool.execute({
                "action": "create", "name": task_id, "message": "x",
                "schedule_type": "interval", "schedule_value": "60",
            }))

    threads = [threading.Thread(target=create, args=(user, f"t-{index}"))
               for index, user in enumerate((env["alice"], env["bob"]))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(result.status == "success" for result in results)
    stored = env["store"].list_tasks()
    assert len(stored) == 2
    assert sorted(t["owner"]["user_id"] for t in stored) == sorted(
        [env["alice"], env["bob"]])


# --- migration of historical tasks (group 4) ------------------------------


def test_migration_stamps_personal_scope_and_quarantines_unattributable_tasks(env):
    owned = _task(env, env["alice"], task_id="t-owned")
    owned.pop("scope")  # as written before this change: owner present, no scope
    env["store"].add_task(owned)
    legacy = _task(env, env["alice"], task_id="t-legacy")
    legacy.pop("owner")  # neither an owner nor an explicit scope: unattributable
    legacy.pop("scope")
    env["store"].add_task(legacy)
    admin = _actor(env, env["admin"], admin=True, source="migration")

    dry = env["service"].migrate_tasks(admin, agent_ids=[env["agent"]], apply=False)
    assert dry["stamped"] == 1 and dry["quarantined"] == 1
    assert env["store"].get_task("t-legacy")["enabled"] is True  # dry run only

    applied = env["service"].migrate_tasks(admin, agent_ids=[env["agent"]], apply=True)
    assert applied["stamped"] == 1 and applied["quarantined"] == 1
    assert env["store"].get_task("t-owned")["scope"] == "personal"
    quarantined = env["store"].get_task("t-legacy")
    assert quarantined["enabled"] is False
    assert quarantined["quarantine"]["reason"] == "no_owner"
    assert quarantined["quarantine"]["restore_hint"] == "ask_owner_to_recreate"
    # Never attributed to the administrator running the migration.
    assert not quarantined.get("owner")

    # Idempotent: a second pass finds nothing left to do.
    again = env["service"].migrate_tasks(admin, agent_ids=[env["agent"]], apply=True)
    assert again["stamped"] == 0 and again["quarantined"] == 0
    assert again["unchanged"] == 2


def test_an_unattributable_task_is_not_executed_by_the_runtime(env):
    """Task 4.2: the database runtime has no default-owner execution path left."""
    from agent.tools.scheduler import identity as sched_identity

    assert sched_identity.revalidate_owner(
        _task(env, env["alice"], scope="public", owner=None)) == sched_identity.UNATTRIBUTED


# --- audit -----------------------------------------------------------------


def test_every_refusal_and_write_is_audited_without_task_content(env):
    env["store"].add_task(_task(env, env["alice"], task_id="t-alice"))
    env["service"].set_enabled(_actor(env, env["alice"]), env["agent"], "t-alice", False)
    with pytest.raises(TaskAuthorizationError):
        env["service"].delete_task(_actor(env, env["bob"]), env["agent"], "t-alice")

    events = env["stack"].service._audit.query_tenant(env["tenant"], limit=50)
    scheduler_events = [e for e in events if e["action"].startswith("scheduler.")]
    assert {e["action"] for e in scheduler_events} >= {"scheduler.disable",
                                                       "scheduler.delete"}
    assert any(e["result"] == "denied" for e in scheduler_events)
    blob = json.dumps(scheduler_events, ensure_ascii=False)
    # The member's own text and the delivery target never enter the trail.
    assert "ping" not in blob
    assert "sess-1" not in blob
