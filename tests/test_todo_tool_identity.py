"""Conversation-created todos must use the same verified owner/store as Web."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import web

from agent.registry import AgentProfile, AgentRegistry
from agent.tools.todo.todo_tool import TodoTool
from auth.service import IdentityService
from channel.chat_channel import ChatChannel
from channel.web import todo_handlers, web_channel
from common.runtime_identity import RuntimeIdentity, current_identity, identity_scope, submit, use_identity


@pytest.fixture
def identities(tmp_path, monkeypatch):
    svc = IdentityService(str(tmp_path / "identity.db"))
    settings = {"identity_mode": "database", "identity_db_path": str(tmp_path / "identity.db"),
                "todo_enabled": True, "web_password": "test-web-password"}
    monkeypatch.setattr("config.conf", lambda: settings)
    monkeypatch.setattr(web_channel, "conf", lambda: settings)
    monkeypatch.setattr("config.get_data_root", lambda: str(tmp_path / "private"))
    profiles, records = [], []
    for code in ("alpha", "beta"):
        workspace = str(tmp_path / "workspaces" / code)
        tenant = svc.bootstrap(tenant_code=code, tenant_name=code, admin_username=code,
                               admin_display=code, admin_password="TodoTestPassword1!",
                               shared_root=workspace, allow_weak=True)
        login = svc.login(code, "TodoTestPassword1!")
        svc.bind_agent(tenant_id=tenant["id"], agent_id=code)
        profiles.append(AgentProfile(id=code, name=code, workspace=workspace))
        records.append((login, RuntimeIdentity(
            agent_id=code, user_id=login.user_id, tenant_id=tenant["id"],
            session_id="chat-" + code,
            web_auth_session_id=svc.verify_session(login.token)["session"]["id"],
        )))
    registry = AgentRegistry(profiles, "alpha")
    monkeypatch.setattr("agent.registry.get_agent_registry", lambda: registry)
    app = web.application(("/api/todos", "TodosHandler",
                           "/api/todos/([^/]+)", "TodoDetailHandler"),
                          vars(todo_handlers), autoreload=False)

    def request(login, identity, *, method="GET", payload=None):
        response = app.request("/api/todos", method=method,
                               headers={"Authorization": "Bearer " + login.token,
                                        "X-Tenant-ID": identity.tenant_id},
                               data=json.dumps(payload) if payload else None)
        assert response.status == "200 OK", response.data
        return json.loads(response.data)

    return svc, records, settings, request


def test_tool_and_web_share_owner_private_store_and_source(identities, tmp_path):
    _, records, _, request = identities
    login, ident = records[0]
    other_login, other = records[1]
    # Neither shared tool configuration nor additional model arguments may
    # select an owner, source conversation, tenant, or storage directory.
    tool = TodoTool({"user_id": other.user_id, "tenant_id": other.tenant_id,
                     "cwd": str(tmp_path / "untrusted"), "session_id": "forged"})
    with use_identity(ident):
        created = tool.execute({"action": "create", "title": "From the conversation",
                                "owner_id": other.user_id, "tenant_id": other.tenant_id,
                                "agent_id": "beta", "session_id": "forged"})
        assert created.status == "success", created.result
    item = request(login, ident)["items"][0]
    assert item["id"] == created.result["todo_id"]
    assert item["agent_id"] == ident.agent_id
    assert item["session_id"] == ident.session_id
    assert request(other_login, other)["total"] == 0
    web_item = request(login, ident, method="POST", payload={"title": "From Web", "create_key": "web-test"})["item"]
    with use_identity(ident):
        assert tool.execute({"action": "list"}).result["total"] == 2
        assert tool.execute({"action": "get", "todo_id": web_item["id"]}).status == "success"
    with use_identity(other):
        assert tool.execute({"action": "get", "todo_id": item["id"]}).status == "error"
    assert (tmp_path / "private" / "tenants" / ident.tenant_id / "todo" / "todos.db").is_file()
    assert not (tmp_path / "private" / "todo" / "todos.db").exists()
    assert not list((tmp_path / "workspaces").rglob("todos.db"))


def test_two_users_in_same_tenant_do_not_share_todos(identities):
    svc, records, _, request = identities
    login, ident = records[0]
    other_login, other = records[1]
    svc.create_member(actor_user_id=ident.user_id, tenant_id=ident.tenant_id,
                      operation="bind-existing", username=other_login.username,
                      display_name="Beta", temporary_password="", roles=["member"])
    other = other.derive(tenant_id=ident.tenant_id, agent_id=ident.agent_id)
    tool = TodoTool()
    with use_identity(ident):
        item = tool.execute({"action": "create", "title": "Only mine"}).result
    with use_identity(other):
        assert tool.execute({"action": "list"}).result["total"] == 0
        assert tool.execute({"action": "get", "todo_id": item["todo_id"]}).status == "error"
    assert request(other_login, other)["total"] == 0
    assert request(login, ident)["total"] == 1


@pytest.mark.parametrize("mutation", [
    "UPDATE users SET active=0 WHERE id=?",
    "UPDATE users SET must_change_password=1 WHERE id=?",
    "UPDATE memberships SET active=0 WHERE user_id=?",
    "UPDATE auth_sessions SET revoked_at=1 WHERE user_id=?",
    "UPDATE auth_sessions SET expires_at=1 WHERE user_id=?",
    "UPDATE auth_sessions SET restricted=1 WHERE user_id=?",
    "DELETE FROM membership_roles WHERE membership_id IN (SELECT id FROM memberships WHERE user_id=?)",
    "UPDATE tenants SET active=0 WHERE id IN (SELECT tenant_id FROM memberships WHERE user_id=?)",
    "DELETE FROM agent_bindings WHERE tenant_id IN (SELECT tenant_id FROM memberships WHERE user_id=?)",
])
def test_authorization_changes_take_effect_before_storage(identities, tmp_path, mutation):
    svc, records, _, _ = identities
    _, ident = records[0]
    tool = TodoTool()
    with use_identity(ident):
        assert tool.is_available()
        with svc._tx() as con:
            con.execute(mutation, (ident.user_id,))
        assert not tool.is_available()
        assert tool.execute({"action": "create", "title": "Must refuse"}).status == "error"
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("change", [
    {"web_auth_session_id": None}, {"web_auth_session_id": "unknown"},
    {"user_id": None}, {"tenant_id": None}, {"agent_id": None}, {"session_id": None},
    {"agent_id": "beta"},
])
def test_incomplete_or_untrusted_delegation_has_no_legacy_fallback(identities, tmp_path, change):
    _, records, _, _ = identities
    _, ident = records[0]
    with use_identity(ident.derive(**change)):
        assert not TodoTool().is_available()
        assert TodoTool().execute({"action": "list"}).status == "error"
    assert not (tmp_path / "private").exists()


def test_login_session_cannot_be_borrowed_from_another_user(identities):
    _, records, _, _ = identities
    with use_identity(records[0][1].derive(web_auth_session_id=records[1][1].web_auth_session_id)):
        assert TodoTool().execute({"action": "list"}).status == "error"


def test_web_proof_survives_worker_and_derived_identity_without_token(identities, monkeypatch):
    svc, records, _, _ = identities
    login, ident = records[0]
    monkeypatch.setattr("channel.web.auth_handlers._get_service", lambda: svc)
    monkeypatch.setattr("channel.web.auth_handlers._session_token", lambda: login.token)
    with use_identity(RuntimeIdentity(user_id=ident.user_id, tenant_id=ident.tenant_id)):
        snapshot = web_channel._web_runtime_identity_snapshot()
    assert login.token not in repr(snapshot)
    restored = ChatChannel._identity_for(None, {"runtime_identity": snapshot,
                                               "agent_id": ident.agent_id,
                                               "session_id": ident.session_id})
    assert restored == ident
    with use_identity(restored), identity_scope(run_id="child-run"), ThreadPoolExecutor(1) as pool:
        assert submit(pool, current_identity).result().web_auth_session_id == ident.web_auth_session_id
        assert submit(pool, lambda: TodoTool().execute({"action": "create", "title": "Worker task"})).result().status == "success"


def test_scheduler_and_external_contexts_cannot_supply_web_delegation(identities):
    for channel in ("scheduler", "feishu", "agent"):
        restored = ChatChannel._identity_for(None, {"agent_id": "alpha", "session_id": "external",
                                                   "channel_type": channel})
        with use_identity(restored):
            assert not TodoTool().is_available()
            assert TodoTool().execute({"action": "create", "title": "Forbidden"}).status == "error"


def test_legacy_requires_actual_web_login_and_keeps_local_owner(identities, monkeypatch, tmp_path):
    _, _, settings, _ = identities
    settings["identity_mode"] = "legacy"
    monkeypatch.setattr(web_channel, "_check_auth", lambda: True)
    with use_identity(RuntimeIdentity()):
        snapshot = web_channel._web_runtime_identity_snapshot()
    ident = ChatChannel._identity_for(None, {"runtime_identity": snapshot,
                                            "agent_id": "alpha", "session_id": "legacy"})
    with use_identity(ident):
        tool = TodoTool()
        assert tool._service().actor.owner_id == "local-owner"
        assert tool.execute({"action": "create", "title": "Legacy task"}).status == "success"
        settings["web_password"] = ""
        assert not tool.is_available()
    assert (tmp_path / "private" / "todo" / "todos.db").is_file()


def test_disabled_feature_creates_no_storage(identities, tmp_path):
    _, records, settings, _ = identities
    settings["todo_enabled"] = False
    with use_identity(records[0][1]):
        assert not TodoTool().is_available()
        assert TodoTool().execute({"action": "list"}).status == "error"
    assert not (tmp_path / "private").exists()
