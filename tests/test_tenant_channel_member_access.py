# encoding:utf-8
"""Task 6.1 — the tenant business channel surface serves both roles.

Change ``unify-console-by-data-scope`` removes the member-facing personal channel
workbench and lets a member maintain their own connections from the *same* page
and the *same* tenant business interface an administrator uses. What separates
the two roles is no longer which endpoint they reach but which objects the
request may touch (``auth.object_scope.allows_channel_instance``):

======================  =========================================
调用者                   ``/api/tenant/channels`` 的范围
======================  =========================================
普通成员                  ``tenant=T AND scope='user' AND owner=U``
租户管理员                上者，加上 ``tenant=T AND scope='tenant'``
平台管理员                平台实例级面（``/api/channels``），不经此处
======================  =========================================

These tests fix that contract at the handler boundary, and the page projection
that has to agree with it: a page that reports the surface unavailable while the
interface answers 200 is the exact defect this change exists to remove (and its
mirror, a page that promises writes the interface then refuses).
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel, auth_handlers, admin_handlers
from auth.service import IdentityService

from tests._helpers import install_personal_target_roster

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

FEISHU = {
    "feishu_app_id": "cli_member",
    "feishu_app_secret": "member-app-secret",
    "feishu_bot_name": "Member Bot",
}
#: The member's own private Agent — the only target a personal create may name.
MEMBER_AGENT = "acme-member-agent"
#: The administrator's own private Agent, for the administrator-as-owner cases.
ADMIN_AGENT = "acme-admin-agent"


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
    """One tenant, an administrator, and a plain member who owns an Agent."""

    def setUp(self):
        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_key)

        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        # A shared Agent the tenant's public connections may route to.
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)

        # A tenant administrator who is *not* the platform administrator: the two
        # roles report different page scopes (``tenant`` vs ``platform``), and
        # conflating them here would hide that difference.
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username="acmeadmin",
            display_name="Acme Admin", temporary_password="Str0ngAcmeAdmin1",
            roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("acmeadmin", "Str0ngAcmeAdmin1").token,
            "Str0ngAcmeAdmin1", "Str0ngAcmeAdminFinal")
        self.tenant_admin_id = [m for m in self.svc.list_members(self.ta)["items"]
                                if m["username"] == "acmeadmin"][0]["user_id"]

        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username="acmemember",
            display_name="Acme Member", temporary_password="Str0ngMember1",
            roles=["member"])
        self.svc.change_password(
            self.svc.login("acmemember", "Str0ngMember1").token,
            "Str0ngMember1", "Str0ngMemberFinal")
        self.member_id = [m for m in self.svc.list_members(self.ta)["items"]
                          if m["username"] == "acmemember"][0]["user_id"]
        # Each role gets a private Agent of its own: a personal connection may
        # only route to its own owner's private Agent, so the administrator and
        # the member must not share one.
        self.svc.bind_agent(tenant_id=self.ta, agent_id=MEMBER_AGENT,
                            private_owner_user_id=self.member_id,
                            origin="user_created")
        self.svc.bind_agent(tenant_id=self.ta, agent_id=ADMIN_AGENT,
                            private_owner_user_id=self.tenant_admin_id,
                            origin="user_created")

        self.token_platform = self.svc.login("root", "Str0ngRootFinal").token
        self.token_admin = self.svc.login("acmeadmin", "Str0ngAcmeAdminFinal").token
        self.token_member = self.svc.login("acmemember", "Str0ngMemberFinal").token

        # A personal create is verified against the Agent Registry as well as the
        # identity binding, so the roster has to hold both private Agents. The
        # helper leaves the pinned settings on ``self.roster_settings``, which is
        # how a test stops an Agent later.
        install_personal_target_roster(
            self, "agent-a", ADMIN_AGENT, MEMBER_AGENT)

    def _restore_key(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    # -- requests ----------------------------------------------------------

    def _app(self):
        return web.application(
            (
                "/api/tenant/channels", "TenantChannelsHandler",
                "/api/tenant/channels/([^/]+)/active", "TenantChannelActiveHandler",
                "/api/tenant/channels/([^/]+)", "TenantChannelHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def request(self, path, *, method="GET", payload=None, token=None,
                tenant=True):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
            headers["Host"] = "test"
            headers["Origin"] = "http://test"
        if tenant:
            headers["X-Tenant-ID"] = self.ta
        kwargs["headers"] = headers

        def _fake_service():
            return self.svc

        # Two different accessors reach the identity store: the handlers' own
        # ``_get_service`` (which reads ``conf()`` per call) and
        # ``auth.service.get_identity_service``, used by the Agent-scope helpers
        # the channel handler calls. Patching only the first leaves the second
        # pointed at the developer's real database, which is how an "empty
        # candidate list" would be read as a product answer.
        with patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_get_service", _fake_service), \
                patch("auth.service.get_identity_service", _fake_service):
            return self._app().request(path, **kwargs)

    @staticmethod
    def body(resp):
        return json.loads(resp.data.decode("utf-8"))

    def assert_status(self, resp, code):
        self.assertTrue(str(resp.status).startswith(str(code)),
                        f"expected {code}, got {resp.status}: {resp.data}")

    # -- writes ------------------------------------------------------------

    def credentials(self, *, app=None, secret="member-app-secret"):
        """A credential bundle with a fresh application identity by default.

        One external application may be connected by exactly one instance, so a
        test creating several connections has to give each its own app id — that
        rule is a real product rule (``app_conflict``), not fixture noise.
        """
        self._app_seq = getattr(self, "_app_seq", 0) + 1
        return {"feishu_app_id": app or f"cli_app_{self._app_seq}",
                "feishu_app_secret": secret,
                "feishu_bot_name": "Bot"}

    def create(self, payload, *, token, tenant=True):
        return self.request("/api/tenant/channels", method="POST",
                            payload=payload, token=token, tenant=tenant)

    def create_personal(self, *, token=None, agent_id=MEMBER_AGENT,
                        name="Member Bot", app=None):
        """The member's own connection, through the shared interface."""
        return self.create({
            "channel_type": "feishu", "display_name": name,
            "agent_id": agent_id, "credentials": self.credentials(app=app),
            "recent_password": "Str0ngMemberFinal",
        }, token=token or self.token_member)

    def create_public(self, *, name="Tenant Bot", app=None):
        return self.create({
            "channel_type": "feishu", "display_name": name,
            "agent_id": "agent-a", "credentials": self.credentials(app=app),
            "recent_password": "Str0ngAcmeAdminFinal",
        }, token=self.token_admin)

    def create_admin_own(self, *, name="Admin Bot", app=None):
        """The administrator's *own* connection: named by target, not by scope.

        Nothing in this payload says "personal" — choosing their own private
        Agent is what makes it one (task 6.1).
        """
        return self.create({
            "channel_type": "feishu", "display_name": name,
            "agent_id": ADMIN_AGENT, "credentials": self.credentials(app=app),
            "recent_password": "Str0ngAcmeAdminFinal",
        }, token=self.token_admin)

    def listing(self, *, token):
        return self.request("/api/tenant/channels", token=token)

    def pages(self, token):
        return self.svc.context_for_tenant(token, self.ta)["console_pages"]

    def stored_row(self, instance_id):
        rows = self.svc._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE id=?",
            (instance_id,))
        return dict(rows[0]) if rows else None


class MemberListingTests(_Fixture):
    """A member sees their own connections and nothing else."""

    def test_a_member_lists_only_their_own_connections(self):
        public = self.body(self.create_public())["instance"]
        mine = self.body(self.create_personal())["instance"]

        resp = self.listing(token=self.token_member)
        self.assert_status(resp, 200)
        listed = self.body(resp)
        self.assertEqual([item["id"] for item in listed["items"]], [mine["id"]])
        self.assertNotIn(public["id"], json.dumps(listed))

    def test_a_colleagues_connection_never_appears_for_this_member(self):
        """The member branch is scoped by owner, not merely by ``scope='user'``.

        Scoping by kind alone would list *every* colleague's personal instance in
        the tenant — the leak this branch has to prevent, now that a member can
        reach the listing at all (task 6.1).
        """
        self.body(self.create_personal())
        colleague_token, colleague = self._another_member_connection()

        listed = self.body(self.listing(token=self.token_member))
        self.assertEqual(len(listed["items"]), 1)
        self.assertNotIn(colleague, json.dumps(listed))

        # And the colleague is not handed this member's row either: the refusal
        # is symmetric, which is what "only their own" has to mean.
        theirs = self.body(self.listing(token=colleague_token))
        self.assertEqual([item["id"] for item in theirs["items"]], [colleague])

    def _another_member_connection(self):
        """A second member of the same tenant with a personal connection.

        Returns ``(token, instance_id)``. The second member needs their own
        private Agent — a personal connection may only route to its owner's — so
        the roster grows by one for the duration of the test.
        """
        target, username = "acme-second-agent", "acmebob"
        temporary, chosen = "Str0ngBobPass1", "Str0ngBobFinal1"
        self.svc.create_member(
            actor_user_id=self.svc.list_platform_users()[0]["id"],
            tenant_id=self.ta, operation="create-new", username=username,
            display_name="Bob", temporary_password=temporary, roles=["member"])
        self.svc.change_password(
            self.svc.login(username, temporary).token, temporary, chosen)
        user_id = [m for m in self.svc.list_members(self.ta)["items"]
                   if m["username"] == username][0]["user_id"]
        self.svc.bind_agent(tenant_id=self.ta, agent_id=target,
                            private_owner_user_id=user_id,
                            origin="user_created")
        # The registry has to know the new target, or the create is refused for a
        # reason unrelated to this test.
        self.roster_settings["agents"].append(
            {"id": target, "name": target, "enabled": True})
        from agent.registry import set_agent_registry

        set_agent_registry(None)
        token = self.svc.login(username, chosen).token
        created = self.body(self.create({
            "channel_type": "feishu", "display_name": "Bob Bot",
            "agent_id": target, "credentials": self.credentials(),
            "recent_password": chosen,
        }, token=token))["instance"]
        return token, created["id"]

    def test_an_administrator_lists_the_public_connections_and_their_own(self):
        public = self.body(self.create_public())["instance"]
        own = self.body(self.create_admin_own())["instance"]
        # The member's private connection is *not* the administrator's to read,
        # even though it lives in the same tenant table.
        member_connection = self.body(self.create_personal())["instance"]

        listed = self.body(self.listing(token=self.token_admin))
        self.assertEqual(sorted(item["id"] for item in listed["items"]),
                         sorted([public["id"], own["id"]]))
        self.assertNotIn(member_connection["id"], json.dumps(listed))

    def test_a_member_listing_carries_no_credentials(self):
        self.create_personal()
        raw = self.listing(token=self.token_member).data.decode("utf-8")
        self.assertNotIn("member-app-secret", raw)
        for item in json.loads(raw)["items"]:
            for forbidden in ("credentials", "ciphertext", "secret", "token"):
                self.assertNotIn(forbidden, item)

    def test_the_type_catalogue_is_the_one_the_member_may_actually_use(self):
        """The member is offered the *personal* contract, not the tenant one.

        A type the member could pick but the create would refuse is the same
        defect as a hidden page: the list and the write have to answer from one
        declaration.
        """
        listed = self.body(self.listing(token=self.token_member))
        self.assertTrue(listed["channel_types"])
        for entry in listed["channel_types"]:
            self.assertIn("ready", entry)


class MemberCreateTests(_Fixture):
    """The same create path, with the scope derived from the verified identity."""

    def test_a_member_creates_their_own_connection(self):
        resp = self.create_personal()
        self.assert_status(resp, 200)
        created = self.body(resp)["instance"]
        row = self.stored_row(created["id"])
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], self.member_id)
        self.assertEqual(row["tenant_id"], self.ta)

    def test_a_member_cannot_write_a_public_connection(self):
        """A named public scope is not a field the member gets to set.

        The pair is forced rather than trusted, so the attempt cannot land a
        tenant-wide connection owned by a member even when the body asks for one.
        """
        resp = self.create({
            "channel_type": "feishu", "display_name": "Sneaky Bot",
            "agent_id": "agent-a", "credentials": self.credentials(),
            "recent_password": "Str0ngMemberFinal",
            "scope": "tenant", "owner_user_id": self.member_id,
        }, token=self.token_member)
        # Either the write is refused or it lands as the member's own; what must
        # never happen is a `scope='tenant'` row owned by the member.
        if str(resp.status).startswith("200"):
            row = self.stored_row(self.body(resp)["instance"]["id"])
            self.assertEqual(row["scope"], "user")
            self.assertEqual(row["owner_user_id"], self.member_id)
        else:
            self.assert_status(resp, 403)

    def test_an_administrator_still_creates_a_public_connection_by_default(self):
        created = self.body(self.create_public())["instance"]
        row = self.stored_row(created["id"])
        self.assertEqual(row["scope"], "tenant")
        self.assertEqual(row["owner_user_id"], None)

    def test_an_administrator_can_still_create_their_own_connection(self):
        """The same form, the same endpoint, a private target (task 6.1).

        The administrator does not ask for a personal connection; they choose
        their own private Agent, and that is what the connection becomes.
        """
        resp = self.create_admin_own()
        self.assert_status(resp, 200)
        row = self.stored_row(self.body(resp)["instance"]["id"])
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], self.tenant_admin_id)

    def test_an_administrator_naming_a_shared_agent_gets_a_public_connection(self):
        created = self.body(self.create_public())["instance"]
        row = self.stored_row(created["id"])
        self.assertEqual(row["scope"], "tenant")
        self.assertIsNone(row["owner_user_id"])

    def test_a_colleague_is_never_named_as_the_owner(self):
        """Ownership is generated from the identity, not read from the body."""
        resp = self.create({
            "channel_type": "feishu", "display_name": "Framed Bot",
            "agent_id": ADMIN_AGENT, "credentials": self.credentials(),
            "recent_password": "Str0ngAcmeAdminFinal",
            "owner_user_id": self.member_id, "scope": "user",
        }, token=self.token_admin)
        self.assert_status(resp, 200)
        row = self.stored_row(self.body(resp)["instance"]["id"])
        self.assertEqual(row["owner_user_id"], self.tenant_admin_id)
        self.assertNotEqual(row["owner_user_id"], self.member_id)

    def test_a_member_cannot_route_to_a_shared_agent(self):
        resp = self.create_personal(agent_id="agent-a")
        self.assert_status(resp, 403)
        self.assertNotIn("Forbidden", self.body(resp).get("message", ""))


class MemberWriteTests(_Fixture):
    """Edit and toggle obey ownership, not the page the caller came through."""

    def setUp(self):
        super().setUp()
        self.mine = self.body(self.create_personal())["instance"]

    def edit(self, instance_id, *, token, version=None, name="Renamed Bot"):
        return self.request(f"/api/tenant/channels/{instance_id}", method="POST",
                            payload={"display_name": name,
                                     "expected_version": self.mine["version"]
                                     if version is None else version,
                                     "recent_password": "Str0ngMemberFinal"},
                            token=token)

    def toggle(self, instance_id, *, token, active=False, version=None,
               password="Str0ngMemberFinal"):
        return self.request(
            f"/api/tenant/channels/{instance_id}/active", method="POST",
            payload={"active": active,
                     "expected_version": self.mine["version"]
                     if version is None else version,
                     "recent_password": password},
            token=token)

    def test_the_owner_edits_their_own_connection(self):
        resp = self.edit(self.mine["id"], token=self.token_member)
        self.assert_status(resp, 200)
        self.assertEqual(self.body(resp)["instance"]["display_name"],
                         "Renamed Bot")

    def test_the_owner_toggles_their_own_connection(self):
        resp = self.toggle(self.mine["id"], token=self.token_member)
        self.assert_status(resp, 200)
        self.assertFalse(self.body(resp)["instance"]["active"])

    def test_a_member_cannot_edit_another_members_connection(self):
        self.create_admin_own(name="Admin Own")
        others = [item for item in self.body(
            self.listing(token=self.token_admin))["items"]
            if item["scope"] == "user" and item["id"] != self.mine["id"]][0]
        resp = self.edit(others["id"], token=self.token_member)
        self.assert_status(resp, 403)

    def test_a_member_cannot_toggle_a_public_connection(self):
        public = self.body(self.create_public())["instance"]
        resp = self.toggle(public["id"], token=self.token_member)
        self.assert_status(resp, 403)

    def test_the_administrator_reaches_both_ranges(self):
        public = self.body(self.create_public())["instance"]
        resp = self.toggle(public["id"], token=self.token_admin,
                           password="Str0ngAcmeAdminFinal")
        self.assert_status(resp, 200)
        resp = self.toggle(self.mine["id"], token=self.token_admin,
                           password="Str0ngAcmeAdminFinal")
        # The administrator is not this object's owner: the tenant's governance
        # stop is a separate surface, and the editable list must not become one.
        self.assert_status(resp, 403)


class ChannelTargetCandidateTests(_Fixture):
    """The choose-a-target list is the server's answer, not the console's guess.

    ``tenant-channel-configuration``: 界面候选也不允许选择这些目标. A candidate the
    create would refuse is the same defect as a hidden page, so the list and the
    write have to answer from one derivation (task 6.2).
    """

    def targets(self, *, token):
        return self.body(self.listing(token=token))["targets"]

    def test_a_member_is_offered_only_their_own_private_agents(self):
        rows = self.targets(token=self.token_member)
        self.assertEqual([row["id"] for row in rows], [MEMBER_AGENT])
        self.assertEqual(rows[0]["scope"], "user")

    def test_the_ownership_each_target_produces_is_reported(self):
        """The form has to be able to say what it is about to create."""
        rows = self.targets(token=self.token_admin)
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[ADMIN_AGENT]["scope"], "user",
                         "an administrator's own private Agent makes their own connection")
        self.assertEqual(by_id["agent-a"]["scope"], "tenant",
                         "a shared Agent makes the tenant's public connection")

    def test_a_disabled_target_is_never_offered_and_is_refused_on_write(self):
        """A stopped Agent accepts nothing new, so it is neither a candidate nor
        a legal target.

        Both halves have to agree: offering a target whose save is refused is the
        "clickable but refused" shape, and accepting one the picker hides would
        be a hole. The predicate behind them is the same one.
        """
        self._set_agent_enabled(ADMIN_AGENT, False)
        try:
            rows = self.targets(token=self.token_admin)
            resp = self.create_admin_own(name="Too Late")
        finally:
            self._set_agent_enabled(ADMIN_AGENT, True)
        self.assertNotIn(ADMIN_AGENT, [row["id"] for row in rows])
        self.assert_status(resp, 403)
        self.assertEqual(self.body(resp)["code"], "personal_agent_disabled")

    def _set_agent_enabled(self, agent_id, enabled):
        """Stop/start an Agent in the roster the registry actually reads.

        The registry is built lazily from ``conf()`` and cached process-wide, so
        the flag is flipped in the pinned settings and the cache dropped —
        editing a file the registry does not read would assert against a stale
        roster.
        """
        for entry in self.roster_settings.get("agents", []):
            if entry.get("id") == agent_id:
                entry["enabled"] = bool(enabled)
        from agent.registry import set_agent_registry

        set_agent_registry(None)

    def test_another_members_private_agent_is_not_a_candidate(self):
        rows = self.targets(token=self.token_admin)
        self.assertNotIn(MEMBER_AGENT, [row["id"] for row in rows])
        rows = self.targets(token=self.token_member)
        self.assertNotIn(ADMIN_AGENT, [row["id"] for row in rows])


class ChannelsPageProjectionTests(_Fixture):
    """The page the console paints has to agree with the interface above."""

    def test_the_channels_page_is_available_to_a_member_as_their_own_surface(self):
        page = self.pages(self.token_member)["admin.channels"]
        self.assertTrue(page["available"])
        self.assertTrue(page["read_allowed"])
        self.assertEqual(page["scope"], "self")
        self.assertEqual(page["reason"], "")
        self.assertTrue(page["actions"]["create"])
        self.assertTrue(page["actions"]["update"])

    def test_the_administrator_keeps_the_tenant_scope(self):
        page = self.pages(self.token_admin)["admin.channels"]
        self.assertTrue(page["available"])
        self.assertEqual(page["scope"], "tenant")

    def test_the_platform_administrator_keeps_the_instance_scope(self):
        """The instance-level page is still a different surface.

        ``/api/channels`` is platform-domain, so the page for a platform
        administrator reports ``platform`` — a member must never be given that
        answer, and the platform administrator must not lose it.
        """
        page = self.pages(self.token_platform)["admin.channels"]
        self.assertTrue(page["available"])
        self.assertEqual(page["scope"], "platform")

    def test_the_page_follows_the_selected_tenant_membership(self):
        """A member of another tenant has no range here, so no page.

        The scope is derived from the membership in the *selected* tenant, not
        from the role name the account carries somewhere else.
        """
        other = IdentityService(_mk_db())
        other.bootstrap(tenant_code="globex", tenant_name="Globex",
                        admin_username="globexadmin", admin_display="G",
                        admin_password="Str0ngPass9", shared_root="/s/globex")
        other.change_password(
            other.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        tb = other.list_tenants()[0]["id"]
        other.create_member(
            actor_user_id=other.list_platform_users()[0]["id"], tenant_id=tb,
            operation="create-new", username="globexmember",
            display_name="Globex Member", temporary_password="Str0ngGlobal1",
            roles=["member"])
        other.change_password(
            other.login("globexmember", "Str0ngGlobal1").token,
            "Str0ngGlobal1", "Str0ngGlobalFinal")
        token = other.login("globexmember", "Str0ngGlobalFinal").token
        try:
            page = other.context_for_tenant(token, self.ta)["console_pages"]["admin.channels"]
        except Exception:
            # No valid membership in the selected tenant is a refusal, not a
            # page: either shape is acceptable, silently opening the page is not.
            return
        self.assertFalse(page["available"])
        self.assertNotEqual(page["scope"], "self")
