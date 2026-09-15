# encoding:utf-8
"""Historical scheduled-task migration (tasks 4.1-4.3).

The migration is the one moment the ownership of *old* tasks is decided, so the
properties worth pinning are the destructive ones:

* a task that can be attributed is only stamped — its id, schedule, action and
  every upstream field survive verbatim, and it never runs twice;
* a task that cannot be attributed is quarantined (disabled, with a reason and a
  recovery hint) and is *not* handed to the administrator who happened to run the
  boot;
* the pass is idempotent, so a boot that dies half-way can simply be repeated —
  there is no state to roll back and no window in which a task fires as the Agent
  itself;
* the runtime refuses the same shape of task afterwards, which is what makes the
  migration a backstop rather than the only guard.
"""

import glob
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from agent.tools.scheduler.task_store import TaskStore

AGENT = "shared-agent"


def _task(task_id="t1", **overrides):
    now = datetime.now()
    task = {
        "id": task_id,
        "name": f"name-{task_id}",
        "enabled": True,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "next_run_at": (now + timedelta(hours=1)).isoformat(),
        "schedule": {"type": "interval", "seconds": 3600},
        "action": {"type": "agent_task", "task_description": "x",
                   "receiver": "user-1", "channel_type": "web"},
    }
    task.update(overrides)
    return task


def _migration_service(app, agents):
    """The same service the boot hook builds, over this app's workspaces."""
    from agent.tools.scheduler.authorization import TaskAccessService, TaskActor

    service = TaskAccessService(
        store_resolver=lambda _actor, agent_id: app.scheduler_store(agent_id),
        agent_ids=lambda _actor: list(agents),
        coordinator="migration",
    )
    return service, TaskActor(source="migration")


def test_a_task_with_an_owner_is_stamped_without_touching_its_schedule(web_app):
    app = web_app("app")
    app.add_agent(AGENT)
    seeded = _task(owner={"user_id": "usr-1", "tenant_id": app.tenant_id,
                          "agent_id": AGENT, "session_id": "s-1"})
    store = app.scheduler_store(AGENT)
    store.add_task(dict(seeded))
    service, actor = _migration_service(app, [AGENT])

    report = service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)

    assert report["stamped"] == 1
    assert report["quarantined"] == 0
    stored = store.get_task("t1")
    assert stored["scope"] == "personal"
    # Everything upstream wrote is still there, byte for byte.
    for field in ("id", "name", "schedule", "action", "next_run_at", "enabled"):
        assert stored[field] == seeded[field]
    assert "quarantine" not in stored


def test_a_task_without_an_owner_is_quarantined_and_disabled(web_app):
    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task())
    service, actor = _migration_service(app, [AGENT])

    report = service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)

    assert report["quarantined"] == 1
    assert report["quarantined_tasks"] == [{"agent_id": AGENT, "task_id": "t1"}]
    stored = store.get_task("t1")
    assert stored["enabled"] is False
    assert stored["scope"] == "public"
    assert stored["quarantine"]["reason"] == "no_owner"
    assert stored["quarantine"]["restore_hint"] == "ask_owner_to_recreate"
    assert "owner" not in stored


def test_the_migration_never_attributes_a_task_to_the_running_admin(web_app):
    """The admin who runs the boot is not the owner of anybody's task."""
    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task())
    service, actor = _migration_service(app, [AGENT])

    service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)

    stored = store.get_task("t1")
    assert not stored.get("owner")
    assert stored["scope"] == "public"


def test_an_interrupted_migration_can_be_re_run(web_app):
    """A half-finished pass leaves a state that one more pass completes.

    The interruption is modelled the way a crashed boot behaves: the first Agent
    was classified, the process stopped before reaching the second. Re-running
    must not duplicate tasks, must not create a second task per Agent, and must
    not renumber or re-own anything it already classified.
    """
    app = web_app("app")
    app.add_agent("primary", "research")
    first, second = app.scheduler_store("primary"), app.scheduler_store("research")
    first.add_task(_task("p1", owner={"user_id": "u1", "tenant_id": app.tenant_id,
                                     "agent_id": "primary"}))
    second.add_task(_task("r1"))
    service, actor = _migration_service(app, ["primary", "research"])

    partial = service.migrate_tasks(actor, agent_ids=["primary"], apply=True)
    assert partial["stamped"] == 1
    assert second.get_task("r1").get("scope") is None

    rest = service.migrate_tasks(actor, agent_ids=["primary", "research"],
                                 apply=True)

    assert rest["unchanged"] == 1        # the already-classified task
    assert rest["quarantined"] == 1      # the one the crash never reached
    assert [t["id"] for t in first.list_tasks()] == ["p1"]
    assert [t["id"] for t in second.list_tasks()] == ["r1"]
    assert first.get_task("p1")["scope"] == "personal"
    assert second.get_task("r1")["enabled"] is False


def test_a_second_pass_reports_everything_unchanged(web_app):
    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task("t1", owner={"user_id": "u1",
                                     "tenant_id": app.tenant_id, "agent_id": AGENT}))
    store.add_task(_task("t2"))
    store.add_task(_task("t3", scope="public"))
    service, actor = _migration_service(app, [AGENT])

    service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)
    before = {task["id"]: dict(task) for task in store.list_tasks()}
    again = service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)

    assert again["unchanged"] == 3
    assert again["stamped"] == 0 and again["quarantined"] == 0
    after = {task["id"]: task for task in store.list_tasks()}
    assert set(after) == set(before)
    for task_id, task in after.items():
        assert task["scope"] == before[task_id]["scope"]
        assert task["enabled"] == before[task_id]["enabled"]
        assert task.get("quarantine") == before[task_id].get("quarantine")


def test_the_boot_hook_runs_the_migration_and_is_idempotent(web_app):
    """``app.py``'s entry point reaches the real migration (task 2.3)."""
    from common import startup_hooks

    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task("t1", owner={"user_id": "u1",
                                     "tenant_id": app.tenant_id, "agent_id": AGENT}))
    store.add_task(_task("t2"))

    assert startup_hooks.run_startup_hook(
        startup_hooks.HOOK_SCHEDULER_TASK_MIGRATION) is True
    stored = {task["id"]: task for task in store.list_tasks()}
    assert stored["t1"]["scope"] == "personal"
    assert stored["t2"]["enabled"] is False

    # A second boot (the common case) changes nothing.
    snapshot = {tid: json.dumps(task, sort_keys=True)
                for tid, task in stored.items()}
    assert startup_hooks.run_startup_hook(
        startup_hooks.HOOK_SCHEDULER_TASK_MIGRATION) is True
    assert {task["id"]: json.dumps(task, sort_keys=True)
            for task in store.list_tasks()} == snapshot


def test_the_migration_tolerates_unreadable_stores(web_app):
    """A broken store must not take the boot down: nothing runs, nothing moves."""
    from common import startup_hooks

    app = web_app("app")
    app.add_agent(AGENT)
    path = app.scheduler_store(AGENT).store_path
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    with patch("auth.service.get_identity_service",
               side_effect=RuntimeError("database gone")):
        assert startup_hooks.run_startup_hook(
            startup_hooks.HOOK_SCHEDULER_TASK_MIGRATION) is True

    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "{not json"


def test_a_quarantined_task_never_delivers(web_app):
    """The runtime backstop: even a re-enabled, ownerless task must not fire."""
    from agent.tools.scheduler.integration import _make_execute_callback

    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task("t1"))
    service, actor = _migration_service(app, [AGENT])
    service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)
    assert store.get_task("t1")["enabled"] is False

    # Someone turns the quarantined task back on (an admin reading the console,
    # or a hand-edited file). It still must not run: the migration is a
    # backstop, not the only guard.
    store.update_task("t1", {"enabled": True})
    bridge = Mock()
    callback = _make_execute_callback(bridge, AGENT, store)

    delivered = callback(store.get_task("t1"))

    # Revalidation refuses before anything reaches the bridge, and the skip is
    # recorded on the task so a human can see why.
    assert delivered is True
    bridge.assert_not_called()
    stored = store.get_task("t1")
    assert stored["last_skip_reason"] == "unattributed"


def test_a_legacy_task_file_is_left_alone_when_there_is_no_agent(web_app):
    """Nothing to classify, nothing to open."""
    app = web_app("app")
    service, actor = _migration_service(app, [])

    report = service.migrate_tasks(actor, agent_ids=[], apply=True)

    assert report == {"agents": [], "stamped": 0, "quarantined": 0,
                      "quarantined_tasks": [], "unchanged": 0, "apply": True}


def test_the_migration_writes_through_the_shared_store_lease(web_app):
    """The write path is the store's, not a private JSON writer (task 3.3)."""
    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task("t1"))
    service, actor = _migration_service(app, [AGENT])

    service.migrate_tasks(actor, agent_ids=[AGENT], apply=True)

    # The store's own revision counter proves the write went through it.
    assert store.get_task("t1")["revision"] >= 1
    assert store.get_task("t1")["write_coordinator"] == "migration"


def test_the_boot_hook_backs_up_the_store_before_it_writes(web_app):
    """Task 4.3's consistent backup: the copy predates the migration's write."""
    from common import startup_hooks

    app = web_app("app")
    app.add_agent(AGENT)
    store = app.scheduler_store(AGENT)
    store.add_task(_task("t1", owner={"user_id": "u1",
                                     "tenant_id": app.tenant_id, "agent_id": AGENT}))
    store.add_task(_task("t2"))

    assert startup_hooks.run_startup_hook(
        startup_hooks.HOOK_SCHEDULER_TASK_MIGRATION) is True

    path = store.store_path
    backups = glob.glob(path + ".bak-pre-migration-*")
    assert len(backups) == 1, backups
    before = json.loads(Path(backups[0]).read_text(encoding="utf-8"))
    by_id = {task["id"]: task for task in before["tasks"].values()}
    # The snapshot is the *pre*-migration state: no scope stamp, still enabled.
    assert "scope" not in by_id["t1"]
    assert by_id["t2"].get("enabled", True) is True

    # A boot that finds everything classified writes no further backup.
    assert startup_hooks.run_startup_hook(
        startup_hooks.HOOK_SCHEDULER_TASK_MIGRATION) is True
    assert len(glob.glob(path + ".bak-pre-migration-*")) == 1
