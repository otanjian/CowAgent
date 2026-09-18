# encoding:utf-8
"""Session-scoped routes must address the Agent's own conversation store.

Conversations live one database per Agent workspace
(``<workspace>/memory/long-term/index.db``). The history list merges those
workspaces and the ownership check reads the addressed Agent's workspace, so a
rename/archive/delete addressed by session id has to land in the *same* file.
Resolving the store from the request's workspace root instead (in database mode
that is the tenant shared root) makes every write miss and answer
``session not found`` -- the bug these cases pin down.

Nothing here stubs ``_get_workspace_root``: the whole point is to exercise the
real resolution path.
"""

import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
import web

from channel.web import web_channel


def _seed(store, rows):
    """Seed like the runtime does: rows carry the agent *and* tenant dimension.

    Under the global store every handle stamps its own ``agent_id`` (the default
    Agent's is ``""``), and the ambient identity publishes a tenant, so a row
    without a tenant is invisible to every scoped query (``dimension_clause``
    matches ``tenant_id`` exactly).
    """
    conn = store._connect()
    try:
        conn.executemany(
            """INSERT INTO sessions
               (agent_id, session_id, title, owner, tenant_id, channel_type,
                created_at, last_active, msg_count, pinned)
               VALUES (?, ?, ?, ?, 'tenant-1', 'web', 1, ?, 1, 0)""",
            [(store._agent_id,) + tuple(row) for row in rows],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def agent_environment(tmp_path, monkeypatch):
    """Two Agents with separate stores; ``b`` is *not* the default Agent."""
    import agent.memory
    import agent.registry
    import auth.service
    import common.state_dir
    from agent.workspace import project_store, session_prefs

    profiles = {
        name: SimpleNamespace(id=name, name=name, avatar=None,
                              workspace=str(tmp_path / name), enabled=True)
        for name in ("a", "b")
    }
    stores = {}
    opened = []

    registry = SimpleNamespace(
        list=lambda include_disabled=False: list(profiles.values()),
        # ``get`` must answer the no-argument call too: resolving the global file
        # asks for the *default* Agent (``get(require_enabled=False)``). A stub
        # that required ``agent_id`` raised TypeError, which ``_default_db_path``
        # swallows by falling back to ``~/cow`` -- seeding the developer's real
        # database instead of ``tmp_path``.
        get=lambda agent_id=None, **kwargs: profiles[agent_id or "a"],
        default_agent_id="a",
    )
    monkeypatch.setattr(agent.registry, "get_agent_registry", lambda: registry)

    # Seed *through the real resolver* so the rows live in the very file a
    # handler opens (``<workspace>/memory/long-term/index.db``). A bare
    # ``ConversationStore(path)`` would quietly seed a second database and make
    # these cases pass or fail for the wrong reason.
    from agent.memory import conversation_store as _cs
    monkeypatch.setattr(_cs, "_store_instances", {})
    real_get_store = agent.memory.get_conversation_store
    for profile in profiles.values():
        stores[profile.workspace] = real_get_store(profile.workspace)

    def recording_get_store(workspace=None):
        opened.append(str(workspace))
        return real_get_store(workspace)

    monkeypatch.setattr(agent.memory, "get_conversation_store", recording_get_store)
    monkeypatch.setattr(auth.service, "get_identity_service", lambda: SimpleNamespace(
        tenant_agent_ids=lambda tenant: ["a", "b"],
        get_agent_binding=lambda agent_id: (
            {"tenant_id": "tenant-1", "agent_id": agent_id} if agent_id in profiles else None
        ),
        # A tenant shared root that is *not* any Agent workspace, exactly like a
        # real deployment: if a handler resolves the store from here it reads a
        # different database and cannot see the session.
        tenant_shared_root=lambda tenant: str(tmp_path / "tenant-root"),
        tenant_default_agent_id=lambda tenant: "a",
        resolved_default_agent_id=lambda tenant: "a",
    ))
    monkeypatch.setattr(session_prefs, "members_index", lambda: {})
    monkeypatch.setattr(project_store, "get_project_map", lambda agent_id: {})
    monkeypatch.setattr(project_store, "get_order", lambda: [])
    monkeypatch.setattr(common.state_dir, "state_root_str", lambda: str(tmp_path))
    monkeypatch.setattr(web_channel, "_db_scope", _fake_db_scope)
    monkeypatch.setattr(web_channel, "_require_read_permission", lambda ctx, perm: None)

    from auth.runtime import RequestContext
    ctx = RequestContext(
        user_id="u1", username="u1", display_name="u1",
        is_platform_admin=False, must_change_password=False,
        tenant_id="tenant-1", membership={"id": "m1"},
        permissions={"history.read"}, is_tenant_admin=False,
    )
    return SimpleNamespace(stores=stores, profiles=profiles, opened=opened, ctx=ctx)


def _ctx():
    from auth.runtime import RequestContext
    return RequestContext(
        user_id="u1", username="u1", display_name="u1",
        is_platform_admin=False, must_change_password=False,
        tenant_id="tenant-1", membership={"id": "m1"},
        permissions={"history.read"}, is_tenant_admin=False,
    )


@contextmanager
def _identity_scope(ctx):
    """Mimic the real ``_db_scope``: publish the request identity, then yield.

    Publishing matters: ``_get_workspace_root`` asks the ambient identity for a
    tenant, so without it every case would 403 instead of reproducing the
    production symptom (wrong store -> ``session not found``).
    """
    from auth.runtime import to_runtime_identity
    from common.runtime_identity import use_identity
    with use_identity(to_runtime_identity(ctx)):
        yield ctx


def _fake_db_scope():
    return _identity_scope(_ctx())


def _app(pattern, handler):
    return web.application((pattern, handler), vars(web_channel), autoreload=False)


def _put_session(env, session_id, body):
    app = _app("/api/sessions/(.*)", "SessionDetailHandler")
    response = app.request(
        "/api/sessions/" + session_id, method="PUT",
        data=json.dumps(body), headers={"Content-Type": "application/json"},
    )
    return response, json.loads(response.data.decode("utf-8"))


def _delete_session(env, session_id):
    app = _app("/api/sessions/(.*)", "SessionDetailHandler")
    response = app.request("/api/sessions/" + session_id + "?agent_id=b", method="DELETE")
    return response, json.loads(response.data.decode("utf-8"))


def _list_sessions(env, **params):
    from urllib.parse import urlencode
    app = _app("/api/sessions", "SessionsHandler")
    response = app.request("/api/sessions?" + urlencode(params))
    return response, json.loads(response.data.decode("utf-8"))


def test_rename_lands_in_the_addressed_agents_store(agent_environment):
    env = agent_environment
    _seed(env.stores[env.profiles["b"].workspace], [("s-b", "旧标题", "u1", 10)])

    response, data = _put_session(env, "s-b", {"title": "新标题", "agent_id": "b"})

    assert response.status == "200 OK"
    assert data["status"] == "success", data
    assert env.stores[env.profiles["b"].workspace].list_sessions(
        channel_type="web", user_id="u1")["sessions"][0]["title"] == "新标题"


def test_archive_lands_in_the_addressed_agents_store(agent_environment):
    env = agent_environment
    _seed(env.stores[env.profiles["b"].workspace], [("s-b", "会话", "u1", 10)])

    response, data = _put_session(env, "s-b", {"archived": True, "agent_id": "b"})

    assert response.status == "200 OK"
    assert data["status"] == "success", data
    store = env.stores[env.profiles["b"].workspace]
    assert store.list_sessions(channel_type="web", user_id="u1")["total"] == 0
    assert store.list_sessions(channel_type="web", user_id="u1", archived=True)["total"] == 1


def test_delete_lands_in_the_addressed_agents_store(agent_environment):
    env = agent_environment
    _seed(env.stores[env.profiles["b"].workspace], [("s-b", "会话", "u1", 10)])

    response, data = _delete_session(env, "s-b")

    assert response.status == "200 OK"
    assert data["status"] == "success", data
    assert env.stores[env.profiles["b"].workspace].list_sessions(
        channel_type="web", user_id="u1")["total"] == 0


def test_list_and_write_resolve_the_same_store(agent_environment):
    """The row a list can show must be the row a write can hit."""
    env = agent_environment
    _seed(env.stores[env.profiles["b"].workspace], [("s-b", "会话", "u1", 10)])

    env.opened.clear()
    _, listed = _list_sessions(env, agent_id="b")
    list_workspaces = set(env.opened)
    assert listed["sessions"][0]["session_id"] == "s-b"

    env.opened.clear()
    _, wrote = _put_session(env, "s-b", {"title": "改名", "agent_id": "b"})
    write_workspaces = set(env.opened)

    assert wrote["status"] == "success", wrote
    assert write_workspaces == list_workspaces, (
        f"write resolved {write_workspaces}, list resolved {list_workspaces}")


def test_workspace_root_resolution_stays_tenant_scoped(agent_environment, monkeypatch):
    """The file/preview seam must keep answering the tenant root, not an Agent."""
    from common.runtime_identity import RuntimeIdentity, use_identity

    seen = {}

    def fake_tenant_root(*, database_mode=True):
        seen["database_mode"] = database_mode
        return "/tenant/shared/root"

    monkeypatch.setattr("channel.web.tenant_workspace.resolve_tenant_workspace_root",
                        fake_tenant_root)
    with use_identity(RuntimeIdentity(tenant_id="tenant-1", user_id="u1", agent_id="b")):
        assert web_channel._get_workspace_root(agent_id="b") == "/tenant/shared/root"
    assert seen["database_mode"] is True
