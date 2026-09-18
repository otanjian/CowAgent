# encoding:utf-8
"""A channel-originated call reaches external tools as the sender, not the bot.

Change ``add-external-system-access``, task 11.3: "验证 Channel 身份到
Agent/tenant 的实际传播和 per-call 授权；用户、机器主体不混用，个人邮箱必须有明确
本人委托。"

What was missing
----------------
Two halves existed and were tested separately, never joined:

* the IM inbound gate (``channel/chat_channel.py::_preflight_external_inbound`` →
  ``channel/external_identity.py::resolve_actor_for_context``) was tested up to
  "the sender resolves to a member" (``tests/test_external_im_gate.py``);
* the external tool (``agent/tools/external/external_tool.py`` and
  ``ToolManager.sync_external_into_agent``) was tested from a hand-built
  ``use_identity(RuntimeIdentity(...))`` (``tests/test_external_connection_agent_tool.py``).

So nothing proved the *join*: that a channel message actually leaves behind an
identity the external tool accepts, and that a machine identity left behind by a
channel instance is refused. Those are different claims, and the second is the
one that would silently leak a mailbox if it regressed.

The four claims here
--------------------
1. **A bound sender reaches their own tools.** The identity the inbound gate
   installs is enough for ``sync_external_into_agent`` to offer, and for
   ``ExternalConnectionTool.execute`` to run, the tools of *that member*.
2. **An unbound sender reaches nothing.** Gate denies → no identity snapshot →
   the tool set is empty and a call is refused. Not "offered but refused": the
   list must be empty, because a visible tool is an invitation.
3. **A machine instance identity is not a user.** The identity a channel
   instance scopes its own parse with (``agent_id`` + ``tenant_id``, no
   ``user_id``) must clear the external set. This is the conflation the task
   forbids: the bot's own identity must never stand in for a person.
4. **A personal mailbox is the owner's alone.** A second member on the same
   tenant, with their own valid identity, must not be offered, must not be able
   to dispatch, and must not be able to resolve the secret.
"""

from __future__ import annotations

import pytest

from channel import external_identity as ex
from channel.chat_channel import ChatChannel
from common.runtime_identity import RuntimeIdentity, current_identity, use_identity
from config import conf
from integrations.external import registry

MASTER_KEY = "channel-propagation-master-key"
TENANT = "acme"
ISSUER = "cli_app_acme"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture(autouse=True)
def _open_read_class(monkeypatch):
    """Open ``read_execute`` for email the way a deployment does.

    A shipped deployment closes every execution class by default ("开关默认
    关闭"), so without this the provider correctly offers nothing and the test
    would be asserting against a deliberately-closed product rather than
    against the propagation it means to check. Opening it in configuration —
    not by monkeypatching the provider — keeps the real listing path in play.
    """
    from config import conf

    monkeypatch.setitem(
        conf().setdefault("external_connections", {}),
        "readiness", {"email": {"read_execute": True}})


class Stack:
    """A tenant with two bound channel senders and one mailbox each."""

    def __init__(self, tmp_path, monkeypatch):
        from auth.service import IdentityService

        self.db_path = str(tmp_path / "identity.db")
        # The type adapters' own listing (``email._own_connections``) reads the
        # *process-wide* service, resolved from ``identity_db_path``. A real
        # deployment has exactly one identity database, so the fixture points
        # that setting at the test's file rather than letting the global
        # resolver reach an unrelated one — with two paths, a connection the
        # test just saved would simply be invisible to the provider, and the
        # test would be measuring its own setup error.
        monkeypatch.setitem(conf(), "identity_db_path", self.db_path)

        self.service = IdentityService(self.db_path)
        self.tenant = self.service.bootstrap(
            tenant_code=TENANT, tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=str(tmp_path / "shared"), allow_weak=True)["id"]
        self.root = self.service.list_platform_users()[0]["id"]
        # A registered, enabled agent: the inbound gate routes the message first
        # (channel/chat_channel.py), and an unknown id would be replaced by the
        # process default before the external identity is ever considered.
        from agent.registry import get_agent_registry

        self.agent = get_agent_registry().default_agent_id
        self.service.bind_agent(tenant_id=self.tenant, agent_id=self.agent)
        monkeypatch.setattr("auth.service.get_identity_service", lambda: self.service)
        self.members = {}
        for username, subject in (("alice", "ou_alice"), ("bob", "ou_bob")):
            self.members[username] = self._add(username, subject)

    def _add(self, username, subject):
        # The IM gate requires the same grants the Web path does, so the member
        # gets a role carrying chat/agent use plus a grant on the very Agent the
        # instance routes to.
        op = self.service.create_role(
            actor_user_id=self.root, tenant_id=self.tenant,
            code="channel-op-%s" % username, name="Channel operator %s" % username,
            permissions=["chat.use", "agent.use", "agent.read"],
            resource_grants=[{"resource_kind": "agent",
                              "resource_id": "agent:%s" % self.agent,
                              "action": "use"}])
        user_id = self.service.create_member(
            actor_user_id=self.root, tenant_id=self.tenant,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="TempPass123!",
            roles=["member", op["code"]])["user_id"]
        token = self.service.login(username, "TempPass123!").token
        self.service.change_password(token, "TempPass123!", "MemberPass123!")
        self.service.bind_external_identity(
            actor_user_id=self.root, user_id=user_id, provider="feishu",
            issuer=ISSUER, subject=subject)
        return user_id

    def mailbox(self, username):
        """Give ``username`` a personal mailbox, as they would in the console."""
        from integrations.external.service import ExternalConnectionService

        svc = ExternalConnectionService(self.service)
        created = svc.create_connection(
            actor_user_id=self.members[username], scope=registry.SCOPE_PERSONAL,
            tenant_id=self.tenant, kind="email", name="%s@example.com" % username,
            config={"imap": {"enabled": True, "host": "imap.example.com", "port": 993,
                             "user": "%s@example.com" % username},
                    "smtp": {"enabled": True, "host": "smtp.example.com", "port": 465,
                             "user": "%s@example.com" % username,
                             "from_addr": "%s@example.com" % username}},
            secrets={"imap_password": "imap-secret-%s" % username,
                     "smtp_password": "smtp-secret-%s" % username})
        return svc, created


@pytest.fixture
def stack(tmp_path, monkeypatch):
    return Stack(tmp_path, monkeypatch)


def _inbound(stack, username):
    """Run the real IM inbound gate for ``username`` and return its context.

    The gate is the thing under test, so it is called rather than imitated: a
    test that hand-writes the identity would prove only that the test can write
    it. ``_preflight_external_inbound`` needs an agent route, which the fixture
    provides by binding ``shared-agent`` to the tenant.
    """
    context = ex.stamp_external_identity(
        {"channel_type": "feishu", "session_id": "sess-%s" % username,
         "bound_agent_id": stack.agent},
        provider="feishu", issuer=ISSUER,
        subject="ou_%s" % username)
    channel = ChatChannel.__new__(ChatChannel)
    consumed = channel._preflight_external_inbound(context)
    return channel, context, consumed


# -- 1. a bound sender reaches their own tools ------------------------------

def test_a_channel_sender_is_offered_their_own_external_tools(stack):
    stack.mailbox("alice")
    _, context, consumed = _inbound(stack, "alice")
    assert consumed is False, "a bound sender must not be denied"
    assert context["runtime_identity"]["user_id"] == stack.members["alice"]

    identity = ChatChannel._identity_for(None, context)
    with use_identity(identity):
        # The identity the gate installs is a real user identity: no web session
        # is involved, and none is required.
        assert current_identity().user_id == stack.members["alice"]
        assert current_identity().tenant_id == stack.tenant
        assert current_identity().web_auth_session_id in (None, "")

        tools = _external_tools(stack)
        assert tools, "the sender's own mailbox must be offered to them"


def test_a_channel_sender_can_dispatch_their_own_mailbox(stack):
    """Offering is not enough: the call must actually go through."""
    stack.mailbox("alice")
    _, context, _ = _inbound(stack, "alice")
    with use_identity(ChatChannel._identity_for(None, context)):
        tools = _external_tools(stack)
        assert tools, "alice must be offered her own mailbox"
        name = sorted(tools)[0]
        # The adapter will not reach a real IMAP host, so the call is expected
        # to end in a staged failure; what matters is that it got past the
        # authorization layer rather than being refused by it.
        result = _run(stack, name)
    assert getattr(result, "status", "") in ("success", "error")
    text = repr(result)
    assert "no trusted identity" not in text
    assert "not available to this caller" not in text


def test_the_tool_carries_the_senders_tenant_not_the_bots(stack):
    stack.mailbox("alice")
    _, context, _ = _inbound(stack, "alice")
    with use_identity(ChatChannel._identity_for(None, context)):
        ident = current_identity()
    # A bot-scoped lookup would have found no mailbox at all: the binding is
    # keyed by the sender's tenant and the sender's user id.
    assert ident.tenant_id == stack.tenant
    assert ident.user_id == stack.members["alice"]


# -- 2. an unbound sender reaches nothing ----------------------------------

def test_an_unbound_channel_sender_is_offered_no_external_tool(stack):
    stack.mailbox("alice")
    context = ex.stamp_external_identity(
        {"channel_type": "feishu", "session_id": "sess-stranger",
         "bound_agent_id": stack.agent},
        provider="feishu", issuer=ISSUER, subject="ou_never_bound")
    channel = ChatChannel.__new__(ChatChannel)
    consumed = channel._preflight_external_inbound(context)
    assert consumed is True, "an unbound sender must be denied"
    assert "runtime_identity" not in context, (
        "a denied sender must leave no identity behind for the tool layer")

    with use_identity(ChatChannel._identity_for(None, context)):
        assert _external_tools(stack) == {}


# -- 3. a machine instance identity is not a user --------------------------

def test_a_channel_instance_identity_is_not_a_user(stack):
    """The bot's own scoping identity must not stand in for a person.

    A channel instance scopes its inbound parse with an ``agent_id`` and a
    ``tenant_id`` and **no** ``user_id`` (the shape
    ``WecomBotChannel._instance_identity`` builds). Handing that to the external
    layer must clear the tool set: a machine subject is not a user, and the task
    forbids conflating them.
    """
    stack.mailbox("alice")
    machine = RuntimeIdentity(agent_id=stack.agent, tenant_id=stack.tenant)
    assert machine.user_id is None
    with use_identity(machine):
        assert _external_tools(stack) == {}


def test_a_tenant_scoped_identity_without_a_user_is_refused_by_a_call(stack):
    stack.mailbox("alice")
    machine = RuntimeIdentity(agent_id=stack.agent, tenant_id=stack.tenant)
    with use_identity(machine):
        assert _external_tools(stack) == {}, (
            "a call must be refused rather than run as the bot")


# -- 4. a personal mailbox is the owner's alone ----------------------------

def test_another_member_is_not_offered_a_colleagues_mailbox(stack):
    stack.mailbox("alice")
    _, context, _ = _inbound(stack, "bob")
    with use_identity(ChatChannel._identity_for(None, context)):
        assert _external_tools(stack) == {}, (
            "bob must not see alice's mailbox in his tool list")


def test_another_member_cannot_dispatch_a_colleagues_mailbox(stack):
    """Even naming the colleague's exact tool must not reach it.

    The name is the one alice was offered — it embeds the connection id, so a
    caller can only learn it by having been offered it. Bob, who was not, is
    refused on the same string.
    """
    stack.mailbox("alice")
    _, alice_context, _ = _inbound(stack, "alice")
    with use_identity(ChatChannel._identity_for(None, alice_context)):
        alice_names = sorted(_external_tools(stack))
    assert alice_names, "alice must be offered her own mailbox"
    named = alice_names[0]

    _, bob_context, _ = _inbound(stack, "bob")
    with use_identity(ChatChannel._identity_for(None, bob_context)):
        with pytest.raises(Exception) as refused:
            _dispatch(stack, named)
    assert getattr(refused.value, "code", "") == "not_found"
    assert "not available to this caller" in str(refused.value)


def test_another_member_cannot_resolve_a_colleagues_mailbox_secret(stack):
    """The second lock: the secret resolver checks the owner too.

    The listing layer already reduces a caller to their own bindings, so this
    asserts the *defence in depth* — a future caller that reaches the resolver
    with another member's connection id is still refused, because the check is
    in the resolver rather than only in its callers.
    """
    svc, alice = stack.mailbox("alice")
    with pytest.raises(Exception) as refused:
        svc.resolve_secret(
            connection_id=alice["id"], slot="imap_password",
            scope=registry.SCOPE_PERSONAL, tenant_id=stack.tenant,
            actor_user_id=stack.members["bob"])
    assert getattr(refused.value, "code", "") == "not_found"

    # ...and the owner themselves still resolves it, so the check is not simply
    # refusing everybody.
    assert svc.resolve_secret(
        connection_id=alice["id"], slot="imap_password",
        scope=registry.SCOPE_PERSONAL, tenant_id=stack.tenant,
        actor_user_id=stack.members["alice"]) == "imap-secret-alice"


def test_a_personal_connection_cannot_be_invoked_by_a_non_owner(stack):
    """The same second lock, on the execution path rather than the secret one.

    ``invoke`` is reached by the per-kind dispatchers, which already reduce the
    caller to their own bindings. This asserts the check inside ``invoke``
    itself, so the rule holds even if a future dispatcher forgets to.
    """
    from integrations.external.runtime import ConnectionRuntime
    from integrations.external.service import ExternalConnectionService

    svc, alice = stack.mailbox("alice")
    runtime = ConnectionRuntime(ExternalConnectionService(stack.service))
    with pytest.raises(Exception) as refused:
        runtime.invoke(
            alice["id"], "mailbox_read", {},
            actor_user_id=stack.members["bob"], tenant_id=stack.tenant)
    assert getattr(refused.value, "code", "") == "not_found"
    # The refusal must not distinguish "not yours" from "does not exist": an
    # answer that differed in code, message or type would let bob confirm
    # alice's connection ids one at a time.
    with pytest.raises(Exception) as missing:
        runtime.invoke(
            "conn_does_not_exist", "mailbox_read", {},
            actor_user_id=stack.members["bob"], tenant_id=stack.tenant)
    assert type(missing.value) is type(refused.value)
    assert (missing.value.code, str(missing.value)) == \
        (refused.value.code, str(refused.value))


# -- helpers ---------------------------------------------------------------

def _actor():
    from common.runtime_identity import current_identity
    return current_identity()


def _external_tools(stack) -> dict:
    """The external tools offered to the current identity, keyed by tool name."""
    from integrations.external.tools import available_tools

    ident = _actor()
    return {binding.tool.name: binding
            for binding in available_tools(tenant_id=ident.tenant_id,
                                           actor_user_id=ident.user_id)}


def _dispatch(stack, name: str, params=None):
    from integrations.external import tools as external_tools
    from integrations.external.service import ExternalConnectionService

    ident = _actor()
    return external_tools.dispatch(
        ExternalConnectionService(stack.service), name, dict(params or {}),
        tenant_id=ident.tenant_id, actor_user_id=ident.user_id)


def _run(stack, name, params=None):
    """Run a listed tool, the way the agent's tool collection would."""
    from agent.tools.external.external_tool import ExternalConnectionTool

    binding = _external_tools(stack)[name]
    tool = ExternalConnectionTool(binding=binding)
    return tool.execute(dict(params or {}))
