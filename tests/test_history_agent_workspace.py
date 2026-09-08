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
