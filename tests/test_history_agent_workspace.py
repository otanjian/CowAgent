"""Opening history reads the same Agent store that the conversation uses."""

import json

import pytest
import web

from agent.memory import get_conversation_store, clear_conversation_store_cache
from agent.registry import AgentProfile, AgentRegistry
from auth.service import IdentityService
from channel.web import auth_handlers, web_channel
from common.runtime_identity import RuntimeIdentity, use_identity


@pytest.fixture
def history_api(tmp_path, monkeypatch):
    svc = IdentityService(str(tmp_path / "identity.db"))
    shared = tmp_path / "shared"
    erp = shared / "agents" / "erp"
    tenant = svc.bootstrap(tenant_code="acme", tenant_name="Acme", admin_username="root",
                           admin_display="Root", admin_password="HistoryTestPass1!",
                           shared_root=str(shared), allow_weak=True)
    uid = svc.list_platform_users()[0]["id"]
    token = svc.login("root", "HistoryTestPass1!").token
    registry = AgentRegistry([
        AgentProfile(id="default", name="Default", workspace=str(shared)),
        AgentProfile(id="erp", name="ERP", workspace=str(erp)),
        AgentProfile(id="unbound", name="Other", workspace=str(tmp_path / "other")),
    ], "default")
    for aid in ("default", "erp"):
        svc.bind_agent(tenant_id=tenant["id"], agent_id=aid, private_owner_user_id=uid)
    monkeypatch.setattr("agent.registry.get_agent_registry", lambda: registry)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: svc)
    monkeypatch.setattr(auth_handlers, "_get_service", lambda: svc)
    monkeypatch.setattr(web_channel, "_is_database_identity", lambda: True)
    for aid, content in (("default", "Default agent message"), ("erp", "ERP original message")):
        with use_identity(RuntimeIdentity(user_id=uid, tenant_id=tenant["id"], agent_id=aid)):
            get_conversation_store(registry.get(aid).workspace).append_messages(
                "same-session", [{"role": "user", "content": content}], channel_type="web")
    with use_identity(RuntimeIdentity(user_id="another-user", tenant_id=tenant["id"], agent_id="erp")):
        get_conversation_store(str(erp)).append_messages(
            "other-session", [{"role": "user", "content": "Private to another user"}], channel_type="web")
    app = web.application(("/api/history", "HistoryHandler"), vars(web_channel), autoreload=False)
    def request(agent_id, session_id="same-session"):
        response = app.request("/api/history?agent_id=" + agent_id + "&session_id=" + session_id,
                               headers={"Authorization": "Bearer " + token, "X-Tenant-ID": tenant["id"]})
        return json.loads(response.data)
    request.shared_root = shared
    request.erp_workspace = erp
    request.registry = registry
    request.identity = RuntimeIdentity(
        user_id=uid, tenant_id=tenant["id"], agent_id="erp"
    )
    yield request
    clear_conversation_store_cache()


def test_selected_agent_history_uses_its_persistent_workspace(history_api):
    result = history_api("erp")
    assert result["status"] == "success"
    assert [m["content"] for m in result["messages"]] == ["ERP original message"]
    assert [m["content"] for m in history_api("default")["messages"]] == ["Default agent message"]


def test_another_users_history_is_not_returned(history_api):
    result = history_api("erp", "other-session")
    assert result["status"] == "success" and result["messages"] == []


def test_unbound_agent_is_not_read(history_api):
    assert history_api("unbound")["status"] == "error"


def test_history_media_refs_are_rewritten_to_a_servable_url(history_api):
    """A workspace-relative image in a stored reply has to come back as a URL
    the browser can fetch. The live SSE path rewrites these; the history path
    reloads the same messages, so it has to rewrite them too or every image in
    a reopened conversation renders broken.

    The anchor is the same root the runtime uses for the request
    (``_get_workspace_root``: the session's open project, else the tenant shared
    root), so the file is placed there — exactly where an agent running in that
    root would have written it.
    """
    from agent.memory import get_conversation_store

    shared = history_api.shared_root
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    workspace = history_api.registry.get("erp").workspace
    store = get_conversation_store(workspace)
    with use_identity(history_api.identity):
        store.append_messages(
            "media-session",
            [
                {"role": "user", "content": "look at this"},
                {"role": "assistant", "content": "![shot](shot.png)"},
            ],
            channel_type="web",
        )

    result = history_api("erp", "media-session")

    assert result["status"] == "success", result
    contents = [m["content"] for m in result["messages"]]
    assert any(
        c == f"![shot](/api/file?path={shared / 'shot.png'})" for c in contents
    ), contents
    assert not any("](shot.png)" in c for c in contents), contents


def test_the_media_rewrite_leaves_refs_that_escape_the_root_alone(tmp_path):
    """The rewrite turns relative refs into servable URLs, so it must not
    follow a ref out of the workspace: a path like ``../../etc/passwd`` stays
    literal instead of becoming a URL that serves it."""
    from channel.web.web_channel import _rewrite_relative_media

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert _rewrite_relative_media("![ok](shot.png)", str(root)) == (
        f"![ok](/api/file?path={root / 'shot.png'})"
    )
    assert (
        _rewrite_relative_media("![no](../secret.png)", str(root))
        == "![no](../secret.png)"
    )
    assert (
        _rewrite_relative_media("![no](missing.png)", str(root))
        == "![no](missing.png)"
    )
