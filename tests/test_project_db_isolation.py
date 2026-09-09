# encoding:utf-8
"""Two-user isolation for user-private projects (database mode)."""

import os
import tempfile
from pathlib import Path

import pytest

from common.runtime_identity import RuntimeIdentity, use_identity
from agent.workspace import project_store


def _user_root(base: Path, user_id: str) -> Path:
    return base / "users" / user_id


def test_user_b_cannot_bind_user_a_project(monkeypatch, tmp_path):
    # User A creates a project under A's user_root. User B (same tenant) must
    # NOT be able to bind a path under A's root even if B knows the path
    # (store-level containment rejects it via _require_within_user_root).
    import common.state_dir as sd
    user_a = "u_a"
    user_b = "u_b"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)

    a_root = _user_root(fake_shared, user_a) / "projects"
    a_root.mkdir(parents=True)
    a_proj = a_root / "alpha"
    a_proj.mkdir()

    # As user A, creation/binding works.
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_a)):
        bound = project_store.set_project_dir("sess-a", str(a_proj), "alpha")
        assert bound == os.path.realpath(str(a_proj))

    # As user B, binding A's path must be rejected.
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_b)):
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.set_project_dir("sess-b", str(a_proj), "alpha")


def test_user_b_cannot_rename_or_delete_user_a_project(monkeypatch, tmp_path):
    import common.state_dir as sd
    user_a = "u_a"
    user_b = "u_b"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)

    a_root = _user_root(fake_shared, user_a) / "projects"
    a_root.mkdir(parents=True)
    a_proj = a_root / "beta"
    a_proj.mkdir()

    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_b)):
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.rename_project(str(a_proj), "betanew")
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.delete_project(str(a_proj), "alpha")


def test_require_owned_session_rejects_foreign_owner(monkeypatch, tmp_path):
    """_require_owned_session must 404 on a session owned by another user.

    This is the web-layer guard that stops a member binding another user's
    session to a project. It reads the durable sessions.owner (and requires
    channel_type 'web'), mirroring _authorize_chat_session.
    """
    import time
    import types
    import web as _web
    from channel.web import web_channel as wc
    from common.runtime_identity import RuntimeIdentity, use_identity
    from agent.memory.conversation_store import get_conversation_store
    from agent.registry import AgentProfile
    import agent.registry as _reg
    from auth.runtime import RequestContext

    user_current = "u_cur"
    user_other = "u_other"
    ws = tmp_path / "ws"
    ws.mkdir()
    agent_id = "alpha"

    # Fake registry whose profile exposes the test workspace.
    profile = AgentProfile(
        id=agent_id, name="alpha", enabled=True,
        workspace=str(ws), model="m",
    )
    reg = types.SimpleNamespace(get=lambda _id=None, require_enabled=True: profile)
    # get_agent_registry is imported locally inside _require_owned_session, so
    # patch the source module attribute it imports from.
    monkeypatch.setattr(_reg, "get_agent_registry", lambda: reg)
    # Bypass tenant-binding machinery: it already ran at the policy layer.
    monkeypatch.setattr(wc, "_require_tenant_agent_binding", lambda ctx, a: agent_id)

    # Insert a session owned by ANOTHER user into the store.
    store = get_conversation_store(str(ws))
    with store._lock:
        con = store._connect()
        try:
            with con:
                con.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, channel_type, created_at, last_active, msg_count, owner) "
                    "VALUES ('sess-foreign', 'web', ?, ?, 0, ?)",
                    (int(time.time()), int(time.time()), user_other),
                )
        finally:
            con.close()

    ctx = RequestContext(
        user_id=user_current, username="cur", display_name="cur",
        is_platform_admin=False, must_change_password=False,
        tenant_id="t1", membership=None, permissions=set(), is_tenant_admin=False,
    )
    _web.ctx.headers = []
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_current)):
        with pytest.raises(_web.HTTPError) as exc:
            wc._require_owned_session(ctx, "sess-foreign", agent_id)
    assert exc.value.args[0] == "404 Not Found"


def test_require_owned_session_accepts_own_web_session(monkeypatch, tmp_path):
    """A session owned by the caller (channel_type web) is allowed through."""
    import time
    import types
    import web as _web
    from channel.web import web_channel as wc
    from common.runtime_identity import RuntimeIdentity, use_identity
    from agent.memory.conversation_store import get_conversation_store
    from agent.registry import AgentProfile
    import agent.registry as _reg
    from auth.runtime import RequestContext

    user_cur = "u_cur"
    ws = tmp_path / "ws"
    ws.mkdir()
    agent_id = "alpha"

    profile = AgentProfile(
        id=agent_id, name="alpha", enabled=True,
        workspace=str(ws), model="m",
    )
    reg = types.SimpleNamespace(get=lambda _id=None, require_enabled=True: profile)
    monkeypatch.setattr(_reg, "get_agent_registry", lambda: reg)
    monkeypatch.setattr(wc, "_require_tenant_agent_binding", lambda ctx, a: agent_id)

    store = get_conversation_store(str(ws))
    with store._lock:
        con = store._connect()
        try:
            with con:
                con.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, channel_type, created_at, last_active, msg_count, owner) "
                    "VALUES ('sess-own', 'web', ?, ?, 0, ?)",
                    (int(time.time()), int(time.time()), user_cur),
                )
        finally:
            con.close()

    ctx = RequestContext(
        user_id=user_cur, username="cur", display_name="cur",
        is_platform_admin=False, must_change_password=False,
        tenant_id="t1", membership=None, permissions=set(), is_tenant_admin=False,
    )
    # Should not raise.
    _web.ctx.headers = []
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_cur)):
        wc._require_owned_session(ctx, "sess-own", agent_id)
