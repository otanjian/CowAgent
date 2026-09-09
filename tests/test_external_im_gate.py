# encoding:utf-8
"""External IM inbound mapping for database mode (open-database-runtime 4.x).

The unified gate in ``channel/external_identity.py`` turns a stamped inbound
author into a tenant member ``RequestContext`` before any model/tool execution:
no binding / non-member / forced password change / missing chat.use or
agent.use => a fixed deny reason and never a model call.
"""

import pytest

from channel import external_identity as ex
from channel.chat_channel import ChatChannel
from bridge.reply import Reply, ReplyType


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from auth.service import IdentityService

    service = IdentityService(str(tmp_path / "identity.db"))
    tenant = service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "shared"), allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    op = service.create_role(
        actor_user_id=root, tenant_id=tenant, code="chat-op", name="Chat operator",
        permissions=["chat.use", "agent.use", "agent.read"],
        resource_grants=[{"resource_kind": "agent", "resource_id": "agent:shared-agent",
                          "action": "use"}],
    )
    member = service.create_member(
        actor_user_id=root, tenant_id=tenant, operation="create-new",
        username="member", display_name="Member", temporary_password="TempPass123!",
        roles=["member", op["code"]],
    )["user_id"]
    _member_token = service.login("member", "TempPass123!").token
    service.change_password(_member_token, "TempPass123!", "MemberPass123!")
    service.bind_agent(tenant_id=tenant, agent_id="shared-agent")
    service.bind_external_identity(
        actor_user_id=root, user_id=member, provider="feishu",
        issuer="cli_app_acme", subject="ou_open_1",
    )
    monkeypatch.setattr("auth.service.get_identity_service", lambda: service)
    return SimpleService(service, tenant, root, member)


class SimpleService:
    def __init__(self, service, tenant, root, member):
        self.service, self.tenant, self.root, self.member = service, tenant, root, member


def _ctx(provider="feishu", issuer="cli_app_acme", subject="ou_open_1"):
    return ex.stamp_external_identity({}, provider=provider, issuer=issuer, subject=subject)


def _bind_user(svc, username, subject, *, tenant=None, roles=("member",), actor=None):
    actor = actor or svc.root
    tenant = tenant or svc.tenant
    m = svc.service.create_member(
        actor_user_id=actor, tenant_id=tenant, operation="create-new",
        username=username, display_name=username.title(), temporary_password="TempPass123!",
        roles=list(roles),
    )["user_id"]
    # A real org user has completed the initial password change; without it the
    # IM gate (like Web) rejects with PASSWORD_CHANGE_REQUIRED.
    token = svc.service.login(username, "TempPass123!").token
    svc.service.change_password(token, "TempPass123!", "MemberPass123!")
    svc.service.bind_external_identity(
        actor_user_id=actor, user_id=m, provider="feishu",
        issuer="cli_app_acme", subject=subject,
    )
    return m


def test_stamp_round_trips_and_normalizes_provider():
    ctx = _ctx()
    assert ctx["external_identity"] == {
        "provider": "feishu", "issuer": "cli_app_acme", "subject": "ou_open_1",
    }


def test_bound_authorized_member_resolves_to_tenant_context(svc):
    ctx, reason = ex.resolve_actor_for_context(_ctx(), "shared-agent")
    assert reason is None
    assert ctx.user_id == svc.member
    assert ctx.tenant_id == svc.tenant
    assert "chat.use" in ctx.permissions
    assert ctx.is_tenant_admin is False


def test_unbound_author_denied_without_model(svc):
    ctx, reason = ex.resolve_actor_for_context(_ctx(subject="ou_stranger"), "shared-agent")
    assert ctx is None and reason == ex.UNBOUND
    # Unbound after deletion is immediately denied.
    bindings = svc.service.list_external_identities(
        user_id=svc.member, page=1, page_size=50)["items"]
    svc.service.delete_external_identity(
        actor_user_id=svc.root, binding_id=bindings[0]["id"])
    ctx, reason = ex.resolve_actor_for_context(_ctx(), "shared-agent")
    assert ctx is None and reason == ex.UNBOUND


def test_wrong_issuer_is_a_different_identity(svc):
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(issuer="cli_other_corp", subject="ou_open_1"), "shared-agent")
    assert ctx is None and reason == ex.UNBOUND


def test_member_of_other_tenant_denied_not_member(svc):
    other = svc.service.create_tenant(
        actor_user_id=svc.root, code="other", name="Other", shared_root="",
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    _bind_user(svc, "foreign", "ou_other", tenant=other)
    ctx, reason = ex.resolve_actor_for_context(_ctx(subject="ou_other"), "shared-agent")
    assert ctx is None and reason == ex.NOT_MEMBER


def test_missing_chat_use_or_agent_grant_denied_permission(svc):
    _bind_user(svc, "readonly", "ou_readonly")  # built-in member role only
    ctx, reason = ex.resolve_actor_for_context(_ctx(subject="ou_readonly"), "shared-agent")
    assert ctx is None and reason == ex.PERMISSION_DENIED


def test_agent_unbound_to_tenant_denied(svc):
    ctx, reason = ex.resolve_actor_for_context(_ctx(), "unbound-agent")
    assert ctx is None and reason == ex.TENANT_UNBOUND


def test_forced_password_change_denied_until_set(svc):
    m = svc.service.create_member(
        actor_user_id=svc.root, tenant_id=svc.tenant, operation="create-new",
        username="freshman", display_name="Fresh", temporary_password="TempPass123!",
        roles=["member"],
    )["user_id"]
    svc.service.bind_external_identity(
        actor_user_id=svc.root, user_id=m, provider="feishu",
        issuer="cli_app_acme", subject="ou_freshman",
    )
    ctx, reason = ex.resolve_actor_for_context(_ctx(subject="ou_freshman"), "shared-agent")
    assert ctx is None and reason == ex.PASSWORD_CHANGE_REQUIRED


def test_platform_admin_passes(svc):
    # The bootstrap platform/tenant admin, mapped as a feishu identity, bypasses
    # the member grant gates exactly like the Web console.
    svc.service.bind_external_identity(
        actor_user_id=svc.root, user_id=svc.root, provider="feishu",
        issuer="cli_app_acme", subject="ou_root",
    )
    ctx, reason = ex.resolve_actor_for_context(_ctx(subject="ou_root"), "shared-agent")
    assert reason is None
    assert ctx.is_platform_admin


def test_deny_notice_is_bilingual_and_stable():
    for reason in (ex.UNBOUND, ex.NOT_MEMBER, ex.PERMISSION_DENIED,
                   ex.PASSWORD_CHANGE_REQUIRED, ex.TENANT_UNBOUND,
                   ex.UNSUPPORTED_CHANNEL):
        text = ex.deny_notice(reason)
        assert isinstance(text, str) and len(text) > 10
    assert ex.deny_notice("unknown-code")


class _ThinChannel(ChatChannel):
    def __init__(self, channel_type):
        # Bypass ChatChannel.__init__ (which spawns a consume thread) — this
        # test only exercises pure predicate logic.
        self.channel_type = channel_type
        self.futures, self.sessions, self.lock = {}, {}, None
        self.sent = []

    def _send_reply(self, context, reply):
        self.sent.append(reply)


def test_preflight_scopes_authorized_member(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr(
        "bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx()
    assert ch._needs_external_db_mapping(context) is True
    assert ch._preflight_external_inbound(context) is False
    assert context["runtime_identity"]["user_id"] == svc.member
    assert context["runtime_identity"]["tenant_id"] == svc.tenant
    assert context["runtime_identity"]["agent_id"] == "shared-agent"
    assert not ch.sent  # authorized run never queues a notice


def test_preflight_denied_sends_notice(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    assert ch._preflight_external_inbound(context) is True
    assert len(ch.sent) == 1 and ch.sent[0].type == ReplyType.TEXT
    assert "管理员" in ch.sent[0].content  # fixed bilingual notice, no model call


def test_preflight_unstamped_sends_unsupported(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("wecom_bot")
    context = {"channel_type": "wecom_bot", "content": "hello"}
    assert ch._preflight_external_inbound(context) is True
    assert len(ch.sent) == 1 and "尚未开放" in ch.sent[0].content


def test_preflight_disabled_agent_sends_disabled(svc, monkeypatch):
    from agent.routing import AgentUnavailableError

    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr(
        "bridge.bridge.Bridge",
        _FakeBridge(error=AgentUnavailableError("agent off")),
    )
    ch = _ThinChannel("feishu")
    context = _ctx()
    assert ch._preflight_external_inbound(context) is True
    assert len(ch.sent) == 1 and "停用" in ch.sent[0].content


class _FakeBridge:
    """Minimal stand-in for the Bridge used by preflight tests.

    The real module exports ``Bridge`` as a *class* and the code under test
    calls ``Bridge()``. Monkeypatching an *instance* of this class therefore
    needs ``__call__`` so ``Bridge()`` returns the configured route.
    """

    def __init__(self, agent_id=None, error=None):
        self.agent_id, self.error = agent_id, error

    def __call__(self):
        return self

    def get_agent_bridge(self):
        return self

    def route_context(self, context):
        if self.error:
            raise self.error
        return self.agent_id


def test_needs_mapping_predicates(monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    ch = _ThinChannel("feishu")
    # Non-web db inbound needs mapping unless already scoped.
    assert ch._needs_external_db_mapping(_ctx()) is True
    # Web db messages are authorized synchronously in the handler.
    web = {"channel_type": "web", "content": "x"}
    assert ch._needs_external_db_mapping(web) is False
    # An upstream-scoped context (web/openai) never goes through the gate.
    scoped = {"channel_type": "feishu", "content": "x",
              "runtime_identity": {"user_id": "u1", "tenant_id": "t1"}}
    assert ch._needs_external_db_mapping(scoped) is False
    # Legacy mode skips everything.
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: False)
    assert ch._needs_external_db_mapping(_ctx()) is False
