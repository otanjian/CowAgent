# encoding:utf-8
"""P0 IDOR closure for session/message and Agent file/avatar endpoints.

The audit (doc/多租户与权限架构分析.html) found that these handlers ran with no
scope or ownership check: any authenticated user who knew a ``session_id`` or
``agent_id`` could delete/rename another tenant's session or rewrite another
Agent's core files. Task 4.x wraps each in the request scope and the same
owner/binding/resource-action guards the correctly-written handlers already use.

Two properties are pinned here:

* the guards themselves refuse (a foreign ``sessions`` row is a 404, an
  unbound Agent is a 403, an empty tenant scope never falls back to the global
  default workspace);
* the handlers consult the guard *before* any side effect, and a guard refusal
  propagates as a real 4xx rather than being swallowed into a 200 JSON error.
"""

import contextlib
import os
import tempfile
import time
import types

import pytest

import web as _web
from auth.runtime import RequestContext
from channel.web import web_channel as wc
from common.runtime_identity import RuntimeIdentity, use_identity
from agent.memory.conversation_store import get_conversation_store
from agent.registry import AgentProfile


class _SpyStore:
    """Records the mutating calls a handler must not reach."""

    def __init__(self):
        self.calls = []

    def clear_session(self, session_id):
        self.calls.append(("clear_session", session_id))
        return True

    def clear_context(self, session_id):
        self.calls.append(("clear_context", session_id))
        return 0

    def rename_session(self, session_id, title):
        self.calls.append(("rename_session", session_id, title))
        return True

    def set_pinned(self, session_id, pinned):
        self.calls.append(("set_pinned", session_id, pinned))
        return True

    def delete_message_pair(self, session_id, user_seq, **kwargs):
        self.calls.append(("delete_message_pair", session_id, user_seq))
        return 1


def _ctx(user_id="u_cur", tenant_id="t1", permissions=()):
    return RequestContext(
        user_id=user_id, username=user_id, display_name=user_id,
        is_platform_admin=False, must_change_password=False,
        tenant_id=tenant_id, membership=None,
        permissions=set(permissions), is_tenant_admin=False,
    )


@contextlib.contextmanager
def _fake_scope(ctx):
    yield ctx


def _register_test_agent(monkeypatch, tmp_path, agent_id="alpha"):
    ws = tmp_path / "ws"
    ws.mkdir()
    profile = AgentProfile(
        id=agent_id, name=agent_id, enabled=True, workspace=str(ws), model="m",
    )
    reg = types.SimpleNamespace(get=lambda _id=None, require_enabled=True: profile)
    import agent.registry as _reg

    monkeypatch.setattr(_reg, "get_agent_registry", lambda: reg)
    return ws


def _insert_session(ws, session_id, owner, channel_type="web"):
    store = get_conversation_store(str(ws))
    with store._lock:
        con = store._connect()
        try:
            with con:
                con.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, channel_type, created_at, last_active, msg_count, owner) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (session_id, channel_type, int(time.time()), int(time.time()), owner),
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------

def test_session_scope_rejects_a_foreign_session(monkeypatch, tmp_path):
    ws = _register_test_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(wc, "_require_tenant_agent_binding",
                        lambda ctx, agent_id: "alpha")
    monkeypatch.setattr(wc, "_tenant_ids_for_context", lambda ctx: ["alpha"])
    _insert_session(ws, "sess-foreign", owner="u_other")
    _web.ctx.headers = []
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id="u_cur")):
        with pytest.raises(_web.HTTPError) as exc:
            wc._require_session_scope(_ctx(), "sess-foreign", "alpha")
    assert exc.value.args[0] == "404 Not Found"


def test_session_scope_accepts_own_web_session(monkeypatch, tmp_path):
    ws = _register_test_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(wc, "_require_tenant_agent_binding",
                        lambda ctx, agent_id: "alpha")
    monkeypatch.setattr(wc, "_tenant_ids_for_context", lambda ctx: ["alpha"])
    _insert_session(ws, "sess-own", owner="u_cur")
    _web.ctx.headers = []
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id="u_cur")):
        assert wc._require_session_scope(_ctx(), "sess-own", "alpha") == "alpha"


# ---------------------------------------------------------------------------
# Handlers must refuse before any side effect, and surface a real 4xx
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("handler_name,invoke,method_call", [
    ("SessionDetailHandler", "DELETE", ("sess-x",)),
    ("SessionTitleHandler", "POST", ("sess-x",)),
    ("SessionClearContextHandler", "POST", ("sess-x",)),
])
def test_session_handlers_refuse_before_touching_the_store(
        monkeypatch, tmp_path, handler_name, invoke, method_call):
    import agent.memory as _memory

    spy = _SpyStore()
    monkeypatch.setattr(_memory, "get_conversation_store", lambda ws: spy)
    monkeypatch.setattr(wc, "_require_auth", lambda: None)
    monkeypatch.setattr(wc.web, "header", lambda *a, **k: None)
    monkeypatch.setattr(wc.web, "input", lambda **k: {})
    monkeypatch.setattr(wc.web, "data", lambda: b'{"title": "hacked", "user_message": "hi"}')
    monkeypatch.setattr(wc, "_db_scope", lambda: _fake_scope(_ctx()))
    monkeypatch.setattr(wc, "_require_read_permission", lambda ctx, perm: None)
    monkeypatch.setattr(wc, "_get_workspace_root", lambda *a, **k: "/tmp/ws")

    def _refuse(ctx, session_id, agent_id):
        raise _web.HTTPError("404 Not Found", {}, '{"status":"error"}')

    monkeypatch.setattr(wc, "_require_session_scope", _refuse)

    handler = getattr(wc, handler_name)()
    with pytest.raises(_web.HTTPError):
        getattr(handler, invoke)(*method_call)
    assert spy.calls == [], f"{handler_name}.{invoke} reached the store"


def test_message_delete_refuses_before_touching_the_store(monkeypatch):
    import json as _json

    import agent.memory as _memory

    spy = _SpyStore()
    monkeypatch.setattr(_memory, "get_conversation_store", lambda ws: spy)
    monkeypatch.setattr(wc, "_require_auth", lambda: None)
    monkeypatch.setattr(wc.web, "header", lambda *a, **k: None)
    monkeypatch.setattr(
        wc.web, "data",
        lambda: _json.dumps({"session_id": "sess-x", "user_seq": 1}).encode())
    monkeypatch.setattr(wc, "_db_scope", lambda: _fake_scope(_ctx()))
    monkeypatch.setattr(wc, "_require_read_permission", lambda ctx, perm: None)
    monkeypatch.setattr(wc, "_get_workspace_root", lambda *a, **k: "/tmp/ws")

    def _refuse(ctx, session_id, agent_id):
        raise _web.HTTPError("404 Not Found", {}, '{"status":"error"}')

    monkeypatch.setattr(wc, "_require_session_scope", _refuse)

    with pytest.raises(_web.HTTPError):
        wc.MessageDeleteHandler().POST()
    assert spy.calls == []


def test_core_file_read_refuses_without_edit_grant(monkeypatch):
    reads = []

    class _Svc:
        def read_core_file(self, agent_id, filename):
            reads.append((agent_id, filename))
            return {}

    monkeypatch.setattr(wc, "_require_auth", lambda: None)
    monkeypatch.setattr(wc.web, "header", lambda *a, **k: None)
    monkeypatch.setattr(wc, "_db_scope", lambda: _fake_scope(_ctx()))
    monkeypatch.setattr(wc, "_require_tenant_agent_binding",
                        lambda ctx, agent_id: "alpha")
    monkeypatch.setattr(wc, "_agent_admin_service", lambda: _Svc())

    def _refuse(ctx, agent_id, action, permission):
        raise _web.HTTPError("403 Forbidden", {}, '{"status":"error"}')

    monkeypatch.setattr(wc, "_require_agent_action", _refuse)

    with pytest.raises(_web.HTTPError):
        wc.AgentCoreFileHandler().GET("alpha", "soul.md")
    assert reads == []


def test_avatar_upload_refuses_without_edit_grant(monkeypatch):
    updates = []

    class _Svc:
        def update_agent(self, agent_id, **kwargs):
            updates.append((agent_id, kwargs))
            return {}

        def snapshot(self):
            return {"revision": 1}

    monkeypatch.setattr(wc, "_require_auth", lambda: None)
    monkeypatch.setattr(wc.web, "header", lambda *a, **k: None)
    monkeypatch.setattr(wc, "_db_scope", lambda: _fake_scope(_ctx()))
    monkeypatch.setattr(wc, "_require_tenant_agent_binding",
                        lambda ctx, agent_id: "alpha")
    monkeypatch.setattr(wc, "_agent_admin_service", lambda: _Svc())

    def _refuse(ctx, agent_id, action, permission):
        raise _web.HTTPError("403 Forbidden", {}, '{"status":"error"}')

    monkeypatch.setattr(wc, "_require_agent_action", _refuse)

    with pytest.raises(_web.HTTPError):
        wc.AgentAvatarHandler().POST("alpha")
    assert updates == []


# ---------------------------------------------------------------------------
# D3: empty tenant scope in database mode never falls back to the global default
# ---------------------------------------------------------------------------

def test_workspace_root_refuses_empty_tenant_in_database_mode(monkeypatch):
    monkeypatch.setattr(wc, "_is_database_identity", lambda: True)
    with use_identity(RuntimeIdentity()):
        with pytest.raises(_web.HTTPError) as exc:
            wc._get_workspace_root()
    assert exc.value.args[0] == "403 Forbidden"


def test_workspace_root_keeps_the_legacy_fallback(monkeypatch, tmp_path):
    ws = _register_test_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(wc, "_is_database_identity", lambda: False)
    with use_identity(RuntimeIdentity()):
        assert wc._get_workspace_root(agent_id="alpha") == str(ws)
