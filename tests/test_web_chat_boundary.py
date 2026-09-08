"""Independent route checks for authenticated historical-chat continuation.

Use real identity, membership, agent bindings and durable session ownership;
only the actual model/queue transport is replaced so no model request is sent.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import web

import config
from agent.memory import clear_conversation_store_cache, get_conversation_store
from agent.registry import AgentProfile, AgentRegistry
from auth.service import IdentityService
from channel.web import web_channel
from common.runtime_identity import RuntimeIdentity, current_identity, use_identity


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    service = IdentityService(str(tmp_path / "identity.db"))
    tenant = service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "shared"), allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    member = service.create_member(
        actor_user_id=root, tenant_id=tenant, operation="create-new",
        username="member", display_name="Member", temporary_password="TempPass123!",
        roles=["member"],
    )["user_id"]
    member_token = service.login("member", "TempPass123!").token
    service.change_password(member_token, "TempPass123!", "MemberPass123!")
    member_token = service.login("member", "MemberPass123!").token
    root_token = service.login("root", "Str0ngAdminPass").token
    other_tenant = service.create_tenant(
        actor_user_id=root, code="other", name="Other", shared_root=str(tmp_path / "other"),
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    profiles = [AgentProfile(id=name, name=name, workspace=str(tmp_path / name))
                for name in ("shared-agent", "other-agent")]
    registry = AgentRegistry(profiles, "shared-agent")
    service.bind_agent(tenant_id=tenant, agent_id="shared-agent")
    service.bind_agent(tenant_id=other_tenant, agent_id="other-agent")
    monkeypatch.setattr("agent.registry.get_agent_registry", lambda: registry)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: service)
    settings = {"identity_mode": "database", "identity_db_path": str(tmp_path / "identity.db"),
                "web_password": "LegacyPasswordMustNotBypassDatabaseAuth"}
    monkeypatch.setattr(config, "conf", lambda: settings)
    monkeypatch.setattr(web_channel, "conf", lambda: settings)
    channel = SimpleNamespace(_sse_streams_lock=threading.RLock(), request_owners={})
    identities = []

    def post_message(*, auth_context, authorized_session):
        identities.append(current_identity())
        agent, session = authorized_session
        with channel._sse_streams_lock:
            rid = "request-" + str(len(channel.request_owners) + 1)
            channel.request_owners[rid] = (auth_context.tenant_id, auth_context.user_id, agent, session)
        web.header("Content-Type", "application/json")
        return json.dumps({"status": "success", "request_id": rid, "stream": True})

    channel.post_message = Mock(side_effect=post_message)
    channel.poll_response = Mock(return_value=json.dumps({"status": "success", "has_content": False}))
    channel.cancel_request = Mock(return_value=json.dumps({"status": "success", "cancelled": 1}))
    channel.stream_response = Mock(side_effect=lambda *_: iter([b'data: {"type":"stream_end"}\n\n']))
    monkeypatch.setattr(web_channel, "WebChannel", lambda: channel)
    app = web_channel.build_web_app()
    store = get_conversation_store(profiles[0].workspace)

    def request(path, *, body=None, token=root_token, selected_tenant=tenant, origin="http://test"):
        headers = {"Host": "test", "Origin": origin, "Content-Type": "application/json"}
        if token:
            headers["Cookie"] = "cow_session=" + token
        if selected_tenant:
            headers["X-Tenant-ID"] = selected_tenant
        return app.request(path, method="POST" if body is not None else "GET", headers=headers,
                           data=json.dumps(body) if body is not None else "")

    def seed(sid, owner=root, channel_type="web"):
        with use_identity(RuntimeIdentity(user_id=owner)):
            store.append_messages(sid, [{"role": "user", "content": "Original conversation"}],
                                  channel_type=channel_type)

    def send(sid="historical", **kwargs):
        return request("/message", body={"session_id": sid, "agent_id": "shared-agent",
                                         "message": "Continue", "stream": True}, **kwargs)

    yield SimpleNamespace(service=service, tenant=tenant, root=root, member=member,
                          root_token=root_token, member_token=member_token, other_tenant=other_tenant,
                          channel=channel, identities=identities, store=store, request=request,
                          seed=seed, send=send)
    clear_conversation_store_cache()


def status(response):
    return int(response.status.split()[0])


def payload(response):
    return json.loads(response.data)


def test_resuming_history_preserves_the_verified_identity_agent_and_session(boundary):
    b = boundary
    b.seed("historical")
    previous = current_identity()
    response = b.send()
    assert status(response) == 200
    call = b.channel.post_message.call_args.kwargs
    assert call["authorized_session"] == ("shared-agent", "historical")
    assert call["auth_context"].user_id == b.root
    assert call["auth_context"].tenant_id == b.tenant
    assert b.identities[0].user_id == b.root
    assert b.identities[0].tenant_id == b.tenant
    assert current_identity() == previous


def test_tenant_admin_cannot_continue_another_members_personal_chat(boundary):
    b = boundary
    b.seed("member-history", owner=b.member)
    response = b.send("member-history")
    assert status(response) == 404
    b.channel.post_message.assert_not_called()


def test_client_identity_claims_never_replace_verified_chat_owner(boundary):
    b = boundary
    response = b.request("/message", body={
        "session_id": "mine", "agent_id": "shared-agent", "message": "Continue",
        "user_id": b.member, "tenant_id": b.other_tenant,
        "runtime_identity": {"user_id": b.member, "tenant_id": b.other_tenant},
    })
    assert status(response) == 200
    ctx = b.channel.post_message.call_args.kwargs["auth_context"]
    assert ctx.user_id == b.root and ctx.tenant_id == b.tenant
    assert next(iter(b.channel.request_owners.values()))[:2] == (b.tenant, b.root)


@pytest.mark.parametrize("path, body", [
    ("/message", {"message": "Continue"}),
    ("/message", {"message": "/cancel"}),
    ("/message", {"message": "/steer Change direction"}),
    ("/message", {"message": "Change direction", "steer": True}),
    ("/poll", {}),
    ("/cancel", {}),
])
def test_another_users_session_is_rejected_before_any_runtime_action(boundary, path, body):
    b = boundary
    b.seed("private-history")
    response = b.request(path, token=b.member_token,
                         body={"session_id": "private-history", "agent_id": "shared-agent", **body})
    assert status(response) == 404
    assert payload(response)["code"] == "not_found"
    b.channel.post_message.assert_not_called()
    b.channel.poll_response.assert_not_called()
    b.channel.cancel_request.assert_not_called()


@pytest.mark.parametrize("owner, channel_type", [("", "web"), (None, "feishu")])
def test_legacy_and_nonweb_sessions_cannot_be_claimed(boundary, owner, channel_type):
    b = boundary
    b.seed("reserved", owner=b.root if owner is None else owner, channel_type=channel_type)
    response = b.send("reserved")
    assert status(response) == 404
    b.channel.post_message.assert_not_called()


def test_two_users_cannot_race_to_claim_the_same_new_session(boundary):
    b = boundary
    start = threading.Barrier(2)

    def claim(token):
        start.wait(timeout=5)
        return b.send("new-shared-id", token=token)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(claim, token) for token in (b.root_token, b.member_token)]
        responses = [future.result(timeout=15) for future in futures]
    assert sorted(status(response) for response in responses) == [200, 404]
    b.channel.post_message.assert_called_once()
    owner = b.channel.post_message.call_args.kwargs["auth_context"].user_id
    con = b.store._connect()
    try:
        assert con.execute("SELECT owner FROM sessions WHERE session_id='new-shared-id'").fetchone()[0] == owner
    finally:
        con.close()


def test_cookie_sse_reconnect_derives_tenant_from_the_owned_request(boundary):
    b = boundary
    rid = payload(b.send())["request_id"]
    response = b.request(f"/stream?request_id={rid}&after_seq=7", selected_tenant=None)
    assert status(response) == 200
    assert b"stream_end" in response.data
    b.channel.stream_response.assert_called_once_with(rid, 7)


@pytest.mark.parametrize("path", ["/stream", "/cancel"])
def test_other_users_request_id_cannot_be_read_or_cancelled(boundary, path):
    b = boundary
    rid = payload(b.send())["request_id"]
    response = (b.request(f"/stream?request_id={rid}", token=b.member_token, selected_tenant=None)
                if path == "/stream" else b.request(path, token=b.member_token, body={"request_id": rid}))
    assert status(response) == 404
    b.channel.stream_response.assert_not_called()
    b.channel.cancel_request.assert_not_called()


def test_cancel_rejects_a_request_session_mismatch(boundary):
    b = boundary
    rid = payload(b.send("one"))["request_id"]
    b.seed("two")
    response = b.request("/cancel", body={"request_id": rid, "session_id": "two", "agent_id": "shared-agent"})
    assert status(response) == 400
    b.channel.cancel_request.assert_not_called()


@pytest.mark.parametrize("change", ["logout", "membership"])
def test_stream_reconnect_revalidates_login_and_membership(boundary, change):
    b = boundary
    rid = payload(b.send(token=b.member_token))["request_id"]
    if change == "logout":
        b.service.revoke_session(b.member_token)
    else:
        with b.service._tx() as con:
            con.execute("UPDATE memberships SET active=0 WHERE user_id=? AND tenant_id=?", (b.member, b.tenant))
    response = b.request(f"/stream?request_id={rid}", token=b.member_token, selected_tenant=None)
    assert status(response) == (401 if change == "logout" else 403)
    b.channel.stream_response.assert_not_called()


def test_conflicting_stream_tenant_and_foreign_agent_are_rejected(boundary):
    b = boundary
    rid = payload(b.send())["request_id"]
    wrong_tenant = b.request(f"/stream?request_id={rid}&tenant_id={b.other_tenant}", selected_tenant=None)
    assert status(wrong_tenant) == 400
    b.channel.stream_response.assert_not_called()
    b.channel.post_message.reset_mock()
    wrong_agent = b.request("/message", body={"session_id": "foreign", "agent_id": "other-agent", "message": "Continue"})
    assert status(wrong_agent) == 404
    b.channel.post_message.assert_not_called()


@pytest.mark.parametrize("path", ["/message", "/cancel", "/poll"])
def test_cross_origin_cookie_writes_are_rejected(boundary, path):
    b = boundary
    b.seed("historical")
    response = b.request(path, origin="http://foreign", body={"session_id": "historical", "message": "Continue"})
    assert status(response) == 403
    assert payload(response)["code"] == "csrf_failed"
