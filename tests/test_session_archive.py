# encoding:utf-8
"""Session archiving: hidden by default, recoverable, scoped, non-destructive.

Store-level cases pin the query contract (default list excludes archived,
explicit query returns them, ids/space counting agree, scope is enforced).
Route-level cases pin the ``archived`` parameter on ``PUT /api/sessions/{id}``
and ``GET /api/sessions`` plus the backwards-compatible default.
"""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import web

from agent.memory.conversation_store import ConversationStore
from channel.web import web_channel


def _seed(store, rows):
    conn = store._connect()
    try:
        conn.executemany(
            """INSERT INTO sessions
               (session_id, title, owner, channel_type, created_at, last_active,
                msg_count, pinned) VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _seed_scoped(store, rows):
    """Rows carrying explicit agent_id / tenant_id so scope can be exercised."""
    conn = store._connect()
    try:
        conn.executemany(
            """INSERT INTO sessions
               (session_id, title, owner, channel_type, created_at, last_active,
                msg_count, pinned, agent_id, tenant_id)
               VALUES (?, ?, ?, 'web', 1, ?, 1, 0, ?, ?)""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "conversations.db")


def test_archive_hides_session_and_explicit_query_returns_it(store):
    _seed(store, [
        ("keep", "保留", "u1", "web", 10, 1, 0),
        ("gone", "归档", "u1", "web", 20, 1, 0),
    ])
    assert store.list_sessions(channel_type="web", user_id="u1")["total"] == 2

    assert store.set_archived("gone", True) is True

    visible = store.list_sessions(channel_type="web", user_id="u1")
    assert [s["session_id"] for s in visible["sessions"]] == ["keep"]
    assert visible["total"] == 1

    archived = store.list_sessions(channel_type="web", user_id="u1", archived=True)
    assert [s["session_id"] for s in archived["sessions"]] == ["gone"]
    assert archived["total"] == 1


def test_archive_keeps_pin_and_restore_returns_to_top(store):
    _seed(store, [
        ("pin", "置顶", "u1", "web", 1, 1, 1),
        ("recent", "最新", "u1", "web", 99, 1, 0),
    ])
    assert store.set_archived("pin", True) is True
    assert [s["session_id"] for s in store.list_sessions(user_id="u1")["sessions"]] == ["recent"]
    assert store.list_sessions(user_id="u1", archived=True)["sessions"][0]["pinned"] is True

    assert store.set_archived("pin", False) is True
    assert [s["session_id"] for s in store.list_sessions(user_id="u1")["sessions"]] == ["pin", "recent"]
    assert store.list_sessions(user_id="u1", archived=True)["total"] == 0


def test_set_archived_returns_false_for_missing_session(store):
    assert store.set_archived("nope", True) is False


def test_list_session_ids_excludes_archived_by_default(store):
    _seed(store, [
        ("keep", "保留", "u1", "web", 10, 1, 0),
        ("gone", "归档", "u1", "web", 20, 1, 0),
    ])
    store.set_archived("gone", True)
    assert store.list_session_ids(channel_type="web", user_id="u1") == ["keep"]
    assert store.list_session_ids(channel_type="web", user_id="u1", archived=True) == ["gone"]


def test_archived_query_respects_owner(store):
    _seed(store, [("theirs", "theirs", "u2", "web", 20, 1, 0)])
    store.set_archived("theirs", True)
    assert store.list_sessions(channel_type="web", user_id="u1", archived=True)["total"] == 0
    assert store.list_sessions(channel_type="web", user_id="u2", archived=True)["total"] == 1


def test_set_archived_respects_ambient_scope(store):
    from common.runtime_identity import RuntimeIdentity, use_identity

    _seed_scoped(store, [
        ("a1-s", "a1", "u1", 10, "a1", "t1"),
        ("a2-s", "a2", "u1", 20, "a2", "t1"),
    ])
    with use_identity(RuntimeIdentity(agent_id="a1", tenant_id="t1")):
        assert store.set_archived("a1-s", True) is True
        assert store.set_archived("a2-s", True) is False


def test_archived_flag_persists_across_reopen(tmp_path):
    path = tmp_path / "conversations.db"
    first = ConversationStore(path)
    _seed(first, [("s1", "t", "u1", "web", 1, 1, 0)])
    first.set_archived("s1", True)

    reopened = ConversationStore(path)
    assert reopened.list_sessions(channel_type="web", user_id="u1")["total"] == 0
    assert reopened.list_sessions(channel_type="web", user_id="u1", archived=True)["total"] == 1


# ---------------------------------------------------------------------------
# Route-level wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_environment(tmp_path, monkeypatch):
    import agent.memory
    import agent.registry
    import auth.service
    import common.state_dir
    from agent.workspace import project_store, session_prefs

    profiles = {
        name: SimpleNamespace(id=name, name=name, avatar=None,
                              workspace=name, enabled=name != "disabled")
        for name in ("a", "b", "foreign", "disabled")
    }
    stores = {name: ConversationStore(tmp_path / (name + ".db")) for name in profiles}

    registry = SimpleNamespace(
        list=lambda include_disabled=False: [
            profile for profile in profiles.values() if include_disabled or profile.enabled
        ],
        get=lambda agent_id, **kwargs: profiles[agent_id or "a"],
    )
    monkeypatch.setattr(agent.registry, "get_agent_registry", lambda: registry)
    monkeypatch.setattr(agent.memory, "get_conversation_store", lambda workspace: stores[workspace])
    monkeypatch.setattr(auth.service, "get_identity_service", lambda: SimpleNamespace(
        tenant_agent_ids=lambda tenant: ["a", "b", "disabled"] if tenant == "tenant-1" else [],
        get_agent_binding=lambda agent_id: (
            {"tenant_id": "tenant-1", "agent_id": agent_id} if agent_id in profiles else None
        ),
    ))
    monkeypatch.setattr(session_prefs, "members_index", lambda: {})
    monkeypatch.setattr(project_store, "get_project_map", lambda agent_id: {})
    monkeypatch.setattr(project_store, "get_order", lambda: [])
    monkeypatch.setattr(common.state_dir, "state_root_str", lambda: str(tmp_path))
    # No ``_get_workspace_root`` stub: session routes must resolve the store from
    # the addressed Agent's workspace (its own database), not from a workspace
    # root. Stubbing it here is how the wrong-store bug stayed hidden.
    from auth.runtime import RequestContext
    ctx = RequestContext(
        user_id="u1", username="u1", display_name="u1",
        is_platform_admin=False, must_change_password=False,
        tenant_id="tenant-1", membership={"id": "m1"},
        permissions={"history.read"}, is_tenant_admin=False,
    )
    return SimpleNamespace(stores=stores, ctx=ctx)


def _get_sessions(monkeypatch, ctx, **params):
    monkeypatch.setattr(web_channel, "_db_scope", lambda: nullcontext(ctx))
    app = web.application(("/api/sessions", "SessionsHandler"), vars(web_channel), autoreload=False)
    response = app.request("/api/sessions?" + urlencode(params))
    return response, json.loads(response.data.decode("utf-8"))


def _put_session(monkeypatch, ctx, session_id, body):
    monkeypatch.setattr(web_channel, "_db_scope", lambda: nullcontext(ctx))
    app = web.application(("/api/sessions/(.*)", "SessionDetailHandler"), vars(web_channel), autoreload=False)
    response = app.request(
        "/api/sessions/" + session_id, method="PUT",
        data=json.dumps(body), headers={"Content-Type": "application/json"},
    )
    return response, json.loads(response.data.decode("utf-8"))


def test_get_archived_param_returns_only_archived(agent_environment, monkeypatch):
    env = agent_environment
    _seed(env.stores["a"], [
        ("keep", "保留", "u1", "web", 10, 1, 0),
        ("gone", "归档", "u1", "web", 20, 1, 0),
    ])
    env.stores["a"].set_archived("gone", True)

    _, default = _get_sessions(monkeypatch, env.ctx, scope="all")
    assert [s["session_id"] for s in default["sessions"]] == ["keep"]
    assert default["total"] == 1 and "query" not in default

    _, archived = _get_sessions(monkeypatch, env.ctx, scope="all", archived="1")
    assert [s["session_id"] for s in archived["sessions"]] == ["gone"]
    assert archived["total"] == 1


def test_get_archived_respects_query_and_owner(agent_environment, monkeypatch):
    env = agent_environment
    _seed(env.stores["a"], [
        ("mine", "已归档我的", "u1", "web", 10, 1, 0),
        ("theirs", "已归档他人", "u2", "web", 20, 1, 0),
    ])
    env.stores["a"].set_archived("mine", True)
    env.stores["a"].set_archived("theirs", True)

    _, result = _get_sessions(monkeypatch, env.ctx, scope="all", archived="1", q="我的")
    assert [s["session_id"] for s in result["sessions"]] == ["mine"]
    assert result["total"] == 1 and result["query"] == "我的"


def test_put_archived_toggles_and_default_is_untouched(agent_environment, monkeypatch):
    env = agent_environment
    _seed(env.stores["a"], [("s1", "会话", "u1", "web", 10, 1, 0)])

    response, data = _put_session(monkeypatch, env.ctx, "s1", {"archived": True, "agent_id": "a"})
    assert response.status == "200 OK" and data["status"] == "success"
    assert env.stores["a"].list_sessions(channel_type="web", user_id="u1")["total"] == 0
    assert env.stores["a"].list_sessions(channel_type="web", user_id="u1", archived=True)["total"] == 1

    response, data = _put_session(monkeypatch, env.ctx, "s1", {"archived": False, "agent_id": "a"})
    assert response.status == "200 OK" and data["status"] == "success"
    assert env.stores["a"].list_sessions(channel_type="web", user_id="u1")["total"] == 1


def test_put_without_any_field_is_rejected(agent_environment, monkeypatch):
    env = agent_environment
    response, data = _put_session(monkeypatch, env.ctx, "s1", {"agent_id": "a"})
    assert data["status"] == "error"


def test_put_archived_requires_history_permission(agent_environment, monkeypatch):
    env = agent_environment
    env.ctx.permissions = set()
    response, data = _put_session(monkeypatch, env.ctx, "s1", {"archived": True, "agent_id": "a"})
    assert response.status == "403 Forbidden"
    assert data["status"] == "error"
