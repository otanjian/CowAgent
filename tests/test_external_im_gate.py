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
                   ex.AGENT_UNAVAILABLE, ex.UNSUPPORTED_CHANNEL):
        text = ex.deny_notice(reason)
        assert isinstance(text, str) and len(text) > 10
    assert ex.deny_notice("unknown-code")


def test_an_instance_anchor_does_not_need_the_agent_binding(svc):
    """TENANT_UNBOUND is a legacy-path answer only.

    An instance-owned message is anchored by its instance row, so a missing
    Agent route no longer means "no organization" — the caller passes the
    effective Agent alongside the anchor instead.
    """
    legacy_ctx, legacy_reason = ex.resolve_actor_for_context(_ctx(), "unbound-agent")
    assert legacy_ctx is None and legacy_reason == ex.TENANT_UNBOUND

    ctx, reason = ex.resolve_actor_for_context(_ctx(), "shared-agent", svc.tenant)
    assert reason is None
    assert ctx.tenant_id == svc.tenant


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


def test_a_denied_inbound_is_remembered_so_an_admin_can_pick_it(svc, monkeypatch):
    """The console's "waiting to be bound" list is fed by this path.

    Without it an administrator cannot bind anybody: they are asked for an
    ``open_id`` that only exists inside the log line this denial writes.
    """
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["channel_type"] = "feishu"
    context["instance_id"] = "chan_x"
    assert ch._preflight_external_inbound(context) is True

    items = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"]
    assert [a["subject"] for a in items] == ["ou_stranger"]
    assert items[0]["provider"] == "feishu"
    assert items[0]["issuer"] == "cli_app_acme"
    assert items[0]["channel_type"] == "feishu"
    assert items[0]["instance_id"] == "chan_x"


class _FakeMsg:
    """The subset of ``ChatMessage`` the attempt evidence is read from."""

    def __init__(self, *, nickname="", content="", ctype=None, is_group=False,
                 resolved_name=None):
        from bridge.context import ContextType

        self.actual_user_nickname = nickname
        self.from_user_nickname = nickname
        self.content = content
        self.ctype = ctype or ContextType.TEXT
        self.is_group = is_group
        self._resolved_name = resolved_name

    def content_with_quote(self):
        return self.content

    def resolve_sender_name(self):
        """Optional channel hook: a provider that only knows an opaque id."""
        if self._resolved_name is None:
            raise AssertionError("the resolver must not be asked when a name exists")
        return self._resolved_name


def test_a_denied_inbound_carries_who_and_what_was_said(svc, monkeypatch):
    """The evidence is read from the standard message fields, not from Feishu.

    Any channel that populates ``ChatMessage`` therefore gets the same
    administrator experience without a per-provider code path.
    """
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["channel_type"] = "feishu"
    context["instance_id"] = "chan_x"
    context["msg"] = _FakeMsg(nickname="张三", content="帮我查下报销")
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert item["sender_name"] == "张三"
    assert item["message_preview"] == "帮我查下报销"
    assert item["is_group"] == 0


def test_a_group_denial_is_marked_as_one(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["isgroup"] = True
    context["msg"] = _FakeMsg(nickname="李四", content="总结一下", is_group=True)
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert item["is_group"] == 1


def test_a_non_text_denial_previews_its_kind_not_a_local_path(svc, monkeypatch):
    """An image message's ``content`` is a downloaded file path.

    Storing it would put a server filesystem path in front of an administrator
    and identify nothing, so a non-text message is summarised by kind.
    """
    from bridge.context import ContextType

    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _FakeMsg(nickname="王五",
                              content="/var/folders/tmp/abc123.png",
                              ctype=ContextType.IMAGE)
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert "/var/folders" not in item["message_preview"]
    assert item["message_preview"], "a kind label is still evidence"


def test_a_channel_that_only_knows_an_opaque_id_can_still_name_the_sender(svc, monkeypatch):
    """Feishu carries an open_id, not a name — the optional hook bridges that.

    Asked lazily, so channels that already have the name never pay for a lookup.
    """
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _FakeMsg(content="帮我查下报销", resolved_name="张三")
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert item["sender_name"] == "张三"
    assert item["message_preview"] == "帮我查下报销"


def test_a_name_lookup_that_fails_leaves_the_attempt_bindable(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))

    class _AngryResolver(_FakeMsg):
        def resolve_sender_name(self):
            raise RuntimeError("no contact scope")

    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _AngryResolver(content="你好")
    assert ch._preflight_external_inbound(context) is True

    items = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"]
    assert [a["subject"] for a in items] == ["ou_stranger"]
    assert items[0]["sender_name"] == ""
    assert items[0]["message_preview"] == "你好"


def test_a_quoted_reply_previews_the_authors_own_words(svc, monkeypatch):
    """A reply's quoted parent is scaffolding; the sender's words are the clue."""
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))

    class _QuotedMsg:
        actual_user_nickname = "张三"
        content = "这笔报销按新标准"
        is_group = False

        def content_with_quote(self):
            return ("[Quoted message]\n旧标准是什么\n[/Quoted message]\n\n"
                    "这笔报销按新标准")

    from bridge.context import ContextType
    _QuotedMsg.ctype = ContextType.TEXT

    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _QuotedMsg()
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert item["message_preview"] == "这笔报销按新标准"
    assert "Quoted message" not in item["message_preview"]


def test_a_long_message_is_truncated(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _FakeMsg(nickname="张三", content="很长的内容" * 200)
    assert ch._preflight_external_inbound(context) is True

    item = svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"][0]
    assert len(item["message_preview"]) <= 200


def test_a_failing_evidence_extraction_still_refuses_the_message(svc, monkeypatch):
    """The refusal is the contract; the evidence is a courtesy."""
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))

    class _ExplodingMsg:
        @property
        def actual_user_nickname(self):
            raise RuntimeError("boom")

        @property
        def content(self):
            raise RuntimeError("boom")

    ch = _ThinChannel("feishu")
    context = _ctx(subject="ou_stranger")
    context["instance_id"] = "chan_x"
    context["msg"] = _ExplodingMsg()
    assert ch._preflight_external_inbound(context) is True
    assert len(ch.sent) == 1 and "管理员" in ch.sent[0].content


def test_an_authorized_inbound_remembers_nothing(svc, monkeypatch):
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    ch = _ThinChannel("feishu")
    assert ch._preflight_external_inbound(_ctx()) is False
    assert svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"] == []


def test_a_non_binding_denial_is_not_offered_for_binding(svc, monkeypatch):
    """Only ``external_unbound`` is answered by binding an account.

    A known author who is simply not in this organization cannot be fixed by
    binding them again, so offering them in the list would be a dead end.
    """
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    other = svc.service.create_tenant(
        actor_user_id=svc.root, code="other2", name="Other2", shared_root="",
        admin_username="other2", admin_display="Other2",
        admin_password="OtherPass123!", recent_password="Str0ngAdminPass",
    )["id"]
    _bind_user(svc, "foreign2", "ou_elsewhere", tenant=other)
    ch = _ThinChannel("feishu")
    assert ch._preflight_external_inbound(_ctx(subject="ou_elsewhere")) is True
    assert svc.service.list_external_identity_attempts(
        actor_user_id=svc.root)["items"] == []


def test_a_binding_deny_logs_the_triple_an_admin_needs(svc, monkeypatch):
    """The notice tells the user to ask an administrator, so the log has to
    tell the administrator what to bind.

    Without the triple the only way to recover is to reproduce the message with
    instrumentation, because the subject is never stored anywhere on the deny
    path — the binding that would carry it is exactly what is missing.
    """
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(agent_id="shared-agent"))
    lines = []

    class _Recorder:
        def info(self, msg, *a, **k):
            lines.append(str(msg) % a if a else str(msg))

        def warning(self, msg, *a, **k):
            lines.append(str(msg) % a if a else str(msg))

        def error(self, msg, *a, **k):
            lines.append(str(msg) % a if a else str(msg))

    monkeypatch.setattr("channel.chat_channel.logger", _Recorder())
    ch = _ThinChannel("feishu")
    ch._preflight_external_inbound(_ctx(subject="ou_stranger"))

    deny = [line for line in lines if "external inbound denied" in line]
    assert deny, f"the deny was not logged at all: {lines}"
    joined = " ".join(deny)
    assert "ou_stranger" in joined, "the admin cannot bind without the subject"
    assert "cli_app_acme" in joined, "the issuer decides which app the binding fits"
    assert "feishu" in joined


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
