# encoding:utf-8
"""End-to-end inbound closure for a tenant-owned Feishu channel (10.5).

This is the "one tenant's Feishu channel, from inbound to execution" evidence:
a single flow walks every real component in order and stops only at the model
boundary, which is replaced by a recorder. Nothing here re-implements the chain;
the point is that the real pieces compose.

    tenant admin creates a Feishu instance (identity.db, encrypted)
      -> startup synthesis decrypts it into a ChannelInstance
      -> ChannelManager's entry list includes it
      -> an inbound Feishu event maps (app_id, open_id) to a tenant member
      -> the message is scoped to that tenant and its Agent
      -> the Agent is invoked ... OR the sender is refused and no run happens

The two assertions that matter are at the end: an authorized sender reaches the
Agent with the tenant's identity attached, and an unauthorized one is stopped at
the preflight gate — which is the contract that says "the caller must not run
the agent".
"""

import json
import os

import pytest

from bridge.reply import ReplyType
from channel import external_identity as ex
from channel.channel_instances import (
    load_tenant_channel_instances,
    resolve_channel_instances,
)
from channel.chat_channel import ChatChannel

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
ACME_APP_ID = "cli_acme_closure"
GLOBEX_APP_ID = "cli_globex_closure"
ACME_SECOND_APP_ID = "cli_acme_closure_no_agent"
ACME_SUBJECT = "ou_acme_closure"
GLOBEX_SUBJECT = "ou_globex_closure"

pytestmark = pytest.mark.usefixtures("_master_key")


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


class _RecordingBridge:
    """Stands in for the real Bridge; records which Agents were routed to."""

    def __init__(self):
        self.routed = []

    def __call__(self):
        return self

    def get_agent_bridge(self):
        return self

    def route_context(self, context):
        agent_id = (context.get("_agent_id") or context.get("bound_agent_id")
                    or self._agent_id)
        self.routed.append(agent_id)
        return agent_id

    _agent_id = "agent-acme"


class _RecorderChannel(ChatChannel):
    """A ChatChannel whose reply generation is recorded instead of run."""

    def __init__(self):
        self.channel_type = "feishu"
        self.futures, self.sessions, self.lock = {}, {}, None
        self.sent = []
        self.generated = []

    def _send_reply(self, context, reply):
        self.sent.append(reply)

    def _generate_reply(self, context):
        self.generated.append(context)
        return None


@pytest.fixture
def closure(tmp_path, monkeypatch):
    """Acme with a working Feishu instance, Plus Globex to attack it from."""
    from auth.service import IdentityService

    svc = IdentityService(str(tmp_path / "identity.db"))
    acme = svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "acme"), allow_weak=True)["id"]
    root = svc.list_platform_users()[0]["id"]
    globex = svc.create_tenant(
        actor_user_id=root, code="globex", name="Globex",
        recent_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "globex"))["id"]

    acme_agent, globex_agent = "agent-acme", "agent-globex"
    svc.bind_agent(tenant_id=acme, agent_id=acme_agent)
    svc.bind_agent(tenant_id=globex, agent_id=globex_agent)

    # 1. The tenant administrator configures its own Feishu channel.
    instance = svc.create_tenant_channel_instance(
        actor_user_id=root, tenant_id=acme, channel_type="feishu",
        display_name="Acme Support", agent_id=acme_agent,
        credentials={"feishu_app_id": ACME_APP_ID,
                     "feishu_app_secret": "acme-closure-secret",
                     "feishu_bot_name": "Acme Support"},
        recent_password="Str0ngAdminPass")

    acme_member = _member(svc, root, acme, "acme-closure", acme_agent,
                          ACME_APP_ID, ACME_SUBJECT)
    globex_member = _member(svc, root, globex, "globex-closure", globex_agent,
                            GLOBEX_APP_ID, GLOBEX_SUBJECT)

    monkeypatch.setattr("auth.service.get_identity_service", lambda: svc)
    monkeypatch.setattr("channel.external_identity.is_database_mode", lambda: True)

    return {
        "svc": svc, "root": root, "acme": acme, "globex": globex, "instance": instance,
        "acme_agent": acme_agent, "globex_agent": globex_agent,
        "acme_member": acme_member, "globex_member": globex_member,
    }


def _member(svc, root, tenant_id, username, agent_id, issuer, subject):
    role = svc.create_role(
        actor_user_id=root, tenant_id=tenant_id, code=f"chat-op-{username}",
        name="Chat operator", permissions=["chat.use", "agent.use", "agent.read"],
        resource_grants=[{"resource_kind": "agent",
                          "resource_id": f"agent:{agent_id}", "action": "use"}])
    user_id = svc.create_member(
        actor_user_id=root, tenant_id=tenant_id, operation="create-new",
        username=username, display_name=username.title(),
        temporary_password="TempPass123!", roles=["member", role["code"]])["user_id"]
    token = svc.login(username, "TempPass123!").token
    svc.change_password(token, "TempPass123!", "MemberPass123!")
    svc.bind_external_identity(actor_user_id=root, user_id=user_id,
                               provider="feishu", issuer=issuer, subject=subject)
    return user_id


def _inbound(issuer, subject):
    return ex.stamp_external_identity({}, provider="feishu", issuer=issuer,
                                      subject=subject)


def test_the_whole_closure_from_creation_to_agent_invocation(closure, monkeypatch):
    bridge = _RecordingBridge()
    bridge._agent_id = closure["acme_agent"]
    monkeypatch.setattr("bridge.bridge.Bridge", bridge)

    # 2. Startup synthesis turns the stored row into a runnable instance with
    #    decrypted credentials.
    synthesized = load_tenant_channel_instances()
    assert [i.instance_id for i in synthesized] == [closure["instance"]["id"]]
    inst = synthesized[0]
    assert inst.channel_type == "feishu"
    assert inst.agent_id == closure["acme_agent"]
    assert inst.legacy is False
    assert inst.credentials["feishu_app_id"] == ACME_APP_ID
    assert inst.credentials["feishu_app_secret"] == "acme-closure-secret"

    # 3. The startup entry list carries it into the ChannelManager.
    entries = resolve_channel_instances({"channel_type": "web"}, synthesized)
    assert closure["instance"]["id"] in [e.instance_id for e in entries]

    # 4. An inbound Feishu event from Acme's app resolves to Acme's member.
    context = _inbound(ACME_APP_ID, ACME_SUBJECT)
    channel = _RecorderChannel()
    assert channel._preflight_external_inbound(context) is False, "authorized inbound was refused"
    assert context["runtime_identity"]["tenant_id"] == closure["acme"]
    assert context["runtime_identity"]["user_id"] == closure["acme_member"]
    assert context["runtime_identity"]["agent_id"] == closure["acme_agent"]

    # 5. The Agent is reachable with that identity; the closure is complete.
    assert bridge.routed == [closure["acme_agent"]]
    assert channel.sent == [], "an authorized inbound should not get a notice"

    # The stored secret never surfaced on any user-facing surface.
    listing = closure["svc"].list_tenant_channel_instances(
        actor_user_id=closure["root"], tenant_id=closure["acme"])
    assert "acme-closure-secret" not in json.dumps(listing)


def test_a_globex_sender_cannot_execute_on_acmes_instance(closure, monkeypatch):
    """The refusal half of the closure: no identity, no Agent run.

    The message arrives on Acme's channel instance, so the inbound router binds
    it to Acme's Agent. Globex's *identity* is what must fail: it resolves to a
    member of another tenant, who may not run on Acme's Agent.
    """
    bridge = _RecordingBridge()
    bridge._agent_id = closure["acme_agent"]      # the instance's Agent
    monkeypatch.setattr("bridge.bridge.Bridge", bridge)

    channel = _RecorderChannel()
    context = _inbound(GLOBEX_APP_ID, GLOBEX_SUBJECT)
    consumed = channel._preflight_external_inbound(context)

    assert consumed is True, "the caller was told to run the agent for a foreign sender"
    assert "runtime_identity" not in context, "a foreign sender was scoped to a tenant"
    assert channel.generated == [], "the model was invoked for a foreign sender"
    assert len(channel.sent) == 1
    assert channel.sent[0].type == ReplyType.TEXT


def test_an_unknown_sender_cannot_execute_on_acmes_instance(closure, monkeypatch):
    bridge = _RecordingBridge()
    bridge._agent_id = closure["acme_agent"]
    monkeypatch.setattr("bridge.bridge.Bridge", bridge)

    channel = _RecorderChannel()
    context = _inbound(ACME_APP_ID, "ou_never_bound")
    assert channel._preflight_external_inbound(context) is True
    assert "runtime_identity" not in context
    assert channel.generated == []


def test_an_agent_less_instance_runs_with_the_tenants_own_default(closure, monkeypatch):
    """6.10: an Agent-less instance still runs, and only inside its tenant.

    The process-global default here is Globex's Agent, so this also proves the
    inbound path never borrows it: the instance row supplies the tenant, the
    tenant's default supplies the Agent, and the model boundary is the recorder.
    """
    svc, root, acme = closure["svc"], closure["root"], closure["acme"]
    unbound = svc.create_tenant_channel_instance(
        actor_user_id=root, tenant_id=acme, channel_type="feishu",
        display_name="Acme Support (no agent)", agent_id="",
        credentials={"feishu_app_id": ACME_SECOND_APP_ID,
                     "feishu_app_secret": "acme-no-agent-secret",
                     "feishu_bot_name": "Acme Support"},
        recent_password="Str0ngAdminPass")
    member = _member(svc, root, acme, "acme-no-agent", closure["acme_agent"],
                     ACME_SECOND_APP_ID, "ou_acme_no_agent")

    # Startup synthesis carries the instance's tenant, with no Agent to follow.
    entry = [i for i in load_tenant_channel_instances()
             if i.instance_id == unbound["id"]]
    assert len(entry) == 1
    assert entry[0].agent_id == ""
    assert entry[0].tenant_id == acme

    bridge = _RecordingBridge()
    bridge._agent_id = closure["globex_agent"]   # the process-global default
    monkeypatch.setattr("bridge.bridge.Bridge", bridge)

    channel = _RecorderChannel()
    context = _inbound(ACME_SECOND_APP_ID, "ou_acme_no_agent")
    context["instance_id"] = unbound["id"]
    assert channel._preflight_external_inbound(context) is False, \
        [r.content for r in channel.sent]

    assert context["runtime_identity"]["tenant_id"] == acme
    assert context["runtime_identity"]["user_id"] == member
    assert context["runtime_identity"]["agent_id"] == closure["acme_agent"]
    assert context["runtime_identity"]["agent_id"] != closure["globex_agent"]
    assert bridge.routed == [closure["acme_agent"]]
    assert channel.sent == [], "an authorized inbound should not get a notice"
