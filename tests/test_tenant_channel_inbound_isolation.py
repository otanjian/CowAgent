# encoding:utf-8
"""Inbound tenant isolation for tenant-owned channel instances (5.x).

An inbound message arriving on a tenant channel instance must execute only in
that instance's tenant. The tenant is derived from the instance's bound Agent —
which :meth:`IdentityService.create_tenant_channel_instance` already refuses
unless it belongs to the same tenant — so the enforced chain is:

    channel instance -> same-tenant Agent -> tenant member

These tests drive the real service (instance creation) and the real inbound
gate (``channel/external_identity.py``); they do not re-implement either.
"""

import pytest

from bridge.reply import ReplyType
from channel import external_identity as ex
from channel.chat_channel import ChatChannel

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
ACME_APP_ID = "cli_acme_instance"
GLOBEX_APP_ID = "cli_globex_instance"


def _bundle(app_id):
    return {
        "feishu_app_id": app_id,
        "feishu_app_secret": "secret-" + app_id,
        "feishu_bot_name": "bot",
    }


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


class _Fixture:
    pass


@pytest.fixture
def f(tmp_path, monkeypatch):
    """Two tenants, each with a bound Agent, a channel instance and a member."""
    from auth.service import IdentityService

    f = _Fixture()
    f.service = IdentityService(str(tmp_path / "identity.db"))
    f.acme = f.service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "acme"), allow_weak=True)["id"]
    f.root = f.service.list_platform_users()[0]["id"]
    f.globex = f.service.create_tenant(
        actor_user_id=f.root, code="globex", name="Globex",
        recent_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "globex"))["id"]

    f.acme_agent = "agent-acme"
    f.globex_agent = "agent-globex"
    f.service.bind_agent(tenant_id=f.acme, agent_id=f.acme_agent)
    f.service.bind_agent(tenant_id=f.globex, agent_id=f.globex_agent)

    f.acme_instance = f.service.create_tenant_channel_instance(
        actor_user_id=f.root, tenant_id=f.acme, channel_type="feishu",
        display_name="Acme Bot", agent_id=f.acme_agent,
        credentials=_bundle(ACME_APP_ID), recent_password="Str0ngAdminPass")
    f.globex_instance = f.service.create_tenant_channel_instance(
        actor_user_id=f.root, tenant_id=f.globex, channel_type="feishu",
        display_name="Globex Bot", agent_id=f.globex_agent,
        credentials=_bundle(GLOBEX_APP_ID), recent_password="Str0ngAdminPass")

    f.acme_member = _member(f, f.acme, "acme-user", f.acme_agent, ACME_APP_ID, "ou_acme")
    f.globex_member = _member(
        f, f.globex, "globex-user", f.globex_agent, GLOBEX_APP_ID, "ou_globex")

    monkeypatch.setattr("auth.service.get_identity_service", lambda: f.service)
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)
    return f


def _member(f, tenant_id, username, agent_id, issuer, subject):
    """Create a chat-capable member mapped to an external IM identity."""
    root = f.root
    role = f.service.create_role(
        actor_user_id=root, tenant_id=tenant_id, code=f"chat-op-{username}",
        name="Chat operator",
        permissions=["chat.use", "agent.use", "agent.read"],
        resource_grants=[{"resource_kind": "agent",
                          "resource_id": f"agent:{agent_id}", "action": "use"}])
    user_id = f.service.create_member(
        actor_user_id=root, tenant_id=tenant_id, operation="create-new",
        username=username, display_name=username.title(),
        temporary_password="TempPass123!", roles=["member", role["code"]],
    )["user_id"]
    token = f.service.login(username, "TempPass123!").token
    f.service.change_password(token, "TempPass123!", "MemberPass123!")
    f.service.bind_external_identity(
        actor_user_id=root, user_id=user_id, provider="feishu",
        issuer=issuer, subject=subject)
    return user_id


def _ctx(issuer, subject):
    return ex.stamp_external_identity({}, provider="feishu", issuer=issuer,
                                      subject=subject)


def test_instance_inbound_resolves_to_the_instance_tenant(f):
    ctx, reason = ex.resolve_actor_for_context(_ctx(ACME_APP_ID, "ou_acme"), f.acme_agent)
    assert reason is None
    assert ctx.user_id == f.acme_member
    assert ctx.tenant_id == f.acme


def test_each_instance_resolves_to_its_own_tenant(f):
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(GLOBEX_APP_ID, "ou_globex"), f.globex_agent)
    assert reason is None
    assert ctx.user_id == f.globex_member
    assert ctx.tenant_id == f.globex
    assert ctx.tenant_id != f.acme


def test_identity_from_another_tenant_cannot_run_on_this_instance(f):
    """Acme's identity arriving on Globex's instance must not execute."""
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(ACME_APP_ID, "ou_acme"), f.globex_agent)
    assert ctx is None
    assert reason == ex.NOT_MEMBER


def test_the_issuer_is_instance_scoped(f):
    """The same subject under a different app id is a different identity."""
    ctx, reason = ex.resolve_actor_for_context(
        _ctx("cli_someone_else", "ou_acme"), f.acme_agent)
    assert ctx is None
    assert reason == ex.UNBOUND


def test_the_agent_binding_is_what_decides_the_tenant(f):
    """Same identity, two Agents: the binding alone flips allow to deny.

    This is the discriminating case — if the tenant ever stopped coming from
    the Agent binding, both halves would collapse to the same outcome.
    """
    identity = _ctx(ACME_APP_ID, "ou_acme")
    allowed, deny_reason = ex.resolve_actor_for_context(dict(identity), f.acme_agent)
    denied, deny_code = ex.resolve_actor_for_context(dict(identity), f.globex_agent)
    assert deny_reason is None
    assert allowed.tenant_id == f.acme
    assert denied is None
    assert deny_code == ex.NOT_MEMBER


def test_unbound_sender_is_denied(f):
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(ACME_APP_ID, "ou_stranger"), f.acme_agent)
    assert ctx is None
    assert reason == ex.UNBOUND


def test_instance_agent_must_be_same_tenant(f):
    """The creation-time guard is what makes the inbound chain trustworthy."""
    from auth.service import IdentityServiceError
    with pytest.raises(IdentityServiceError) as caught:
        f.service.create_tenant_channel_instance(
            actor_user_id=f.root, tenant_id=f.acme, channel_type="feishu",
            display_name="Misrouted", agent_id=f.globex_agent,
            credentials=_bundle("cli_misrouted"),
            recent_password="Str0ngAdminPass")
    assert caught.value.code == "forbidden"
    assert caught.value.status == 403


class _FakeBridge:
    def __init__(self, agent_id):
        self.agent_id = agent_id

    def __call__(self):
        return self

    def get_agent_bridge(self):
        return self

    def route_context(self, context):
        return self.agent_id


class _ThinChannel(ChatChannel):
    def __init__(self, channel_type):
        self.channel_type = channel_type
        self.futures, self.sessions, self.lock = {}, {}, None
        self.sent = []

    def _send_reply(self, context, reply):
        self.sent.append(reply)


def test_denied_inbound_never_reaches_the_agent(f, monkeypatch):
    """A cross-tenant sender gets a fixed notice, not a model call."""
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(f.globex_agent))
    channel = _ThinChannel("feishu")
    context = _ctx(ACME_APP_ID, "ou_acme")
    assert channel._preflight_external_inbound(context) is True
    assert len(channel.sent) == 1
    assert channel.sent[0].type == ReplyType.TEXT
    assert "organization" in channel.sent[0].content.lower() or \
        "组织" in channel.sent[0].content


def test_authorized_instance_inbound_is_scoped_and_queued(f, monkeypatch):
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(f.acme_agent))
    channel = _ThinChannel("feishu")
    context = _ctx(ACME_APP_ID, "ou_acme")
    assert channel._preflight_external_inbound(context) is False
    assert context["runtime_identity"]["tenant_id"] == f.acme
    assert context["runtime_identity"]["user_id"] == f.acme_member
    assert context["runtime_identity"]["agent_id"] == f.acme_agent
    assert channel.sent == []


def test_legacy_instance_level_routing_is_unchanged(f, monkeypatch):
    """A plain Agent binding (no tenant instance) still resolves as before."""
    monkeypatch.setattr("bridge.bridge.Bridge", _FakeBridge(f.acme_agent))
    channel = _ThinChannel("feishu")
    context = _ctx(ACME_APP_ID, "ou_acme")
    assert channel._needs_external_db_mapping(context) is True
    ctx, reason = ex.resolve_actor_for_context(context, f.acme_agent)
    assert reason is None and ctx.tenant_id == f.acme


# --- the instance anchor parameter (task 6.4/6.8) -------------------------

def test_the_instance_parameter_outranks_the_routed_agent(f):
    """The instance's tenant decides, not the Agent the router happened to pick.

    Acme's sender on an Acme instance with Globex's Agent routed in: the anchor
    makes this a permission deny (no grant on that foreign Agent), whereas
    anchoring on the routed Agent's tenant would have made it a membership
    deny — a different code, so the two rules are distinguishable.
    """
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(ACME_APP_ID, "ou_acme"), f.globex_agent, f.acme)
    assert ctx is None
    assert reason == ex.PERMISSION_DENIED


def test_an_instance_anchor_with_the_instances_own_agent_resolves(f):
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(ACME_APP_ID, "ou_acme"), f.acme_agent, f.acme)
    assert reason is None
    assert ctx.user_id == f.acme_member
    assert ctx.tenant_id == f.acme


def test_omitting_the_instance_parameter_keeps_the_agent_anchor(f):
    """Platform/legacy callers pass two arguments and are unaffected."""
    ctx, reason = ex.resolve_actor_for_context(
        _ctx(ACME_APP_ID, "ou_acme"), f.acme_agent)
    assert reason is None
    assert ctx.tenant_id == f.acme


def test_the_store_is_the_source_of_truth_for_the_instance_tenant(f):
    context = _ctx(ACME_APP_ID, "ou_acme")
    context["instance_id"] = f.acme_instance["id"]
    assert ex.instance_tenant_id(context) == f.acme


def test_no_instance_id_means_no_instance_anchor(f):
    assert ex.instance_tenant_id(_ctx(ACME_APP_ID, "ou_acme")) == ""


def test_an_unknown_instance_id_yields_no_anchor(f):
    """A platform roster instance is not tenant-owned: nothing to anchor on."""
    context = _ctx(ACME_APP_ID, "ou_acme")
    context["instance_id"] = "not-a-stored-instance"
    assert ex.instance_tenant_id(context) == ""


def test_a_lookup_failure_never_grants_an_anchor(f, monkeypatch):
    """If the store cannot be read we fall back to the Agent binding, and the
    normal gates still apply rather than an exception leaking out."""
    context = _ctx(ACME_APP_ID, "ou_acme")
    context["instance_id"] = f.acme_instance["id"]

    def _boom(_self, _instance_id):
        raise RuntimeError("identity store unavailable")

    monkeypatch.setattr(type(f.service), "get_tenant_channel_instance_row", _boom)
    assert ex.instance_tenant_id(context) == ""
