# encoding:utf-8
"""Stage 9.3 — two-tenant, multi-user authorization regression.

`enable-member-personal-console` adds a surface where a *member* writes objects
that only they may read. Every single-tenant test in this change proves the
member/admin split inside one tenant; this file proves the pair that actually
has to hold in production: **two tenants, several users, one identity service**.

The matrix each test walks:

    actor                object                     must
    -------------------  -------------------------  -----------------------------
    same tenant owner    own object                 reach (positive control)
    same tenant peer     another member's object    be refused
    other tenant member  the object                 be invisible and refused
    other tenant admin   the object                 be refused (no cross tenant)
    platform admin       private content            refused; governance metadata only
    platform admin       private configuration      never selected
    tenant admin         public tenant channels     unchanged (still manages them)

The last row is the reason this file exists next to the Stage-8 acceptance
matrix: tightening the personal path must not have relaxed — or duplicated — the
public `/api/channels` management gate.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityServiceError

from tests.test_personal_console_acceptance import (
    TOOLS_BASE, _Fixture as _AcceptanceFixture)

PROVISIONER = "agent.personal_assistant.get_personal_assistant_provisioner"
MEMBER_PASSWORD = "MemPassFinal1"
BETA_ADMIN_PASSWORD = "Str0ngBetaFinal"


class _TwoTenantFixture(_AcceptanceFixture):
    """Tenant A (``acme``, from the Stage-8 harness) plus tenant B (``beta``).

    Tenant A ships a platform admin (``root``) and a member role; tenant B is
    created through the ordinary platform-admin path, so its admin is a real
    tenant admin rather than a second platform account.
    """

    def setUp(self):
        super().setUp()
        beta = self.service.create_tenant(
            actor_user_id=self.root_user_id, code="beta", name="Beta",
            admin_username="betaadmin", admin_display="Beta Admin",
            admin_password="Str0ngBetaPass", recent_password="Str0ngRootFinal",
            shared_root=os.path.join(self.root_dir, "beta"))
        self.beta_id = beta["id"]
        # A tenant admin account starts under a must-change temporary password;
        # settle it once so every later login is a normal session.
        self.service.change_password(
            self.service.login("betaadmin", "Str0ngBetaPass").token,
            "Str0ngBetaPass", BETA_ADMIN_PASSWORD)
        self.beta_admin_token = self.service.login(
            "betaadmin", BETA_ADMIN_PASSWORD).token
        self.beta_admin_id = self._member_id_in(self.beta_id, "betaadmin")

    # -- helpers ----------------------------------------------------------

    def _member_id_in(self, tenant_id, username):
        return [m for m in self.service.list_members(tenant_id)["items"]
                if m["username"] == username][0]["user_id"]

    def _plain_member(self, tenant_id, actor_user_id, username):
        """Create a member in *tenant_id* and return ``(user_id, token)``."""
        with patch(PROVISIONER,
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            created = self.service.create_member(
                actor_user_id=actor_user_id, tenant_id=tenant_id,
                operation="create-new", username=username,
                display_name=username, temporary_password="MemTempPass1",
                roles=["member"])
        token = self.service.login(username, "MemTempPass1").token
        self.service.change_password(token, "MemTempPass1", MEMBER_PASSWORD)
        return created["user_id"], self.service.login(username, MEMBER_PASSWORD).token

    def _headers_for(self, token, tenant_id, *, json_body=False):
        headers = {"Host": "localhost:9899", "Origin": TOOLS_BASE,
                   "Cookie": "cow_session=" + token}
        if tenant_id:
            headers["X-Tenant-ID"] = tenant_id
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_in(self, path, token, tenant_id, *, method="GET", body=None):
        """A request whose tenant header is chosen per call, not per fixture."""
        return self.app.request(
            path, method=method,
            headers=self._headers_for(token, tenant_id, json_body=body is not None),
            data=json.dumps(body) if body is not None else None)

    def _personal_instance(self, user_id, tenant_id, display_name, app_id, *,
                           agent_id):
        """Create one member's personal instance, over a target they own.

        ``agent_id`` is a parameter rather than something derived from the
        member, because the target is now part of what a personal instance *is*:
        the fixture has to bind that Agent as the member's own private one first,
        and the id has to be in :data:`ROSTER_AGENTS` so the registry resolves it
        as enabled too.
        """
        self._private_agent(user_id, tenant_id, agent_id)
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "multi-tenant-key"}):
            self.service.create_personal_channel_instance(
                actor_user_id=user_id, tenant_id=tenant_id,
                channel_type="feishu", display_name=display_name,
                agent_id=agent_id,
                credentials={"feishu_app_id": app_id,
                             "feishu_app_secret": "fixture-secret",
                             "feishu_token": "fixture-token",
                             "feishu_bot_name": "fixture-bot"},
                recent_password=MEMBER_PASSWORD)
        return self.service.list_personal_channel_instances(
            actor_user_id=user_id, tenant_id=tenant_id)["items"][0]

    def _private_agent(self, user_id, tenant_id, agent_id):
        self.service.bind_private_agent_with_quota(
            tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
            origin="user_created", actor_user_id=user_id)
        return agent_id


class CrossTenantPersonalChannelTests(_TwoTenantFixture):
    """A personal channel instance belongs to one tenant *and* one member."""

    def setUp(self):
        super().setUp()
        self.alice_id, self.alice_token = self._plain_member(
            self.tenant_id, self.root_user_id, "alice")
        self.carol_id, self.carol_token = self._plain_member(
            self.beta_id, self.beta_admin_id, "carol")
        self.instance = self._personal_instance(
            self.alice_id, self.tenant_id, "alice's", "cli_mt_alice",
            agent_id="target-alice")

    def test_the_other_tenants_member_sees_nothing(self):
        listing = self.service.list_personal_channel_instances(
            actor_user_id=self.carol_id, tenant_id=self.beta_id)
        self.assertEqual(listing["items"], [])

        body = self._body(self._request_in(
            "/api/personal/channels", self.carol_token, self.beta_id))
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["total"], 0)

    def test_a_foreign_instance_id_is_not_an_authorization(self):
        """Same wire shape as the in-tenant case, across a tenant boundary."""
        response = self._request_in(
            "/api/personal/channels/" + self.instance["id"], self.carol_token,
            self.beta_id, method="POST",
            body={"action": "update", "display_name": "stolen",
                  "expected_version": self.instance["version"],
                  "recent_password": MEMBER_PASSWORD})
        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertNotEqual(self._body(response).get("status"), "success")

        with self.assertRaises(IdentityServiceError):
            self.service.update_personal_channel_instance(
                actor_user_id=self.carol_id, tenant_id=self.beta_id,
                instance_id=self.instance["id"],
                expected_version=self.instance["version"],
                display_name="stolen", recent_password=MEMBER_PASSWORD)

        # The positive control: the owner's row is untouched and still editable.
        owned = self.service.list_personal_channel_instances(
            actor_user_id=self.alice_id, tenant_id=self.tenant_id)["items"][0]
        self.assertEqual(owned["display_name"], "alice's")

    def test_a_forged_tenant_header_does_not_reach_another_tenants_rows(self):
        """The tenant is taken from the verified context, never from the header
        alone: a member of beta cannot borrow acme's header to read acme."""
        body = self._body(self._request_in(
            "/api/personal/channels", self.carol_token, self.tenant_id))
        self.assertNotEqual(body.get("status"), "success")
        self.assertNotIn("alice's", json.dumps(body, ensure_ascii=False))

    def test_the_platform_admin_cannot_read_the_member_configuration(self):
        """Governance metadata yes; the private configuration no."""
        rows = self.service.list_personal_channel_instances_for_governance(
            self.root_user_id, self.tenant_id)
        row = [r for r in rows if r["id"] == self.instance["id"]][0]
        self.assertEqual(row["owner_user_id"], self.alice_id)
        self.assertEqual(row["channel_type"], "feishu")
        for forbidden in ("display_name", "agent_id", "credentials",
                          "parameters", "config"):
            self.assertNotIn(forbidden, row, forbidden)

        # And the personal API never becomes a second, richer view for them:
        # the listing is owner-scoped in SQL, so the admin's own list is empty.
        self.assertEqual(self.service.list_personal_channel_instances(
            actor_user_id=self.root_user_id, tenant_id=self.tenant_id)["items"],
            [])

    def test_the_other_tenants_admin_is_refused_governance_and_configuration(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self.service.set_personal_instance_governance(
                actor_user_id=self.beta_admin_id, tenant_id=self.tenant_id,
                instance_id=self.instance["id"], disabled=True,
                recent_password=BETA_ADMIN_PASSWORD, reason="not mine to stop")
        self.assertEqual(caught.exception.status, 403)

        with self.assertRaises(IdentityServiceError) as caught:
            self.service.set_personal_instance_governance(
                actor_user_id=self.beta_admin_id, tenant_id=self.beta_id,
                instance_id=self.instance["id"], disabled=True,
                recent_password=BETA_ADMIN_PASSWORD, reason="not in my tenant")
        self.assertIn(caught.exception.status, (403, 404))

        row = self.service.list_personal_channel_instances_for_governance(
            self.root_user_id, self.tenant_id)[0]
        self.assertFalse(row["governance_disabled"])

    def test_the_platform_admin_can_still_govern_the_instance(self):
        """The stop is the administrator capability that must keep working."""
        self.service.set_personal_instance_governance(
            actor_user_id=self.root_user_id, tenant_id=self.tenant_id,
            instance_id=self.instance["id"], disabled=True,
            recent_password="Str0ngRootFinal", reason="policy")
        row = self.service.list_personal_channel_instances_for_governance(
            self.root_user_id, self.tenant_id)[0]
        self.assertTrue(row["governance_disabled"])

        # A governance stop beats the owner: they cannot re-enable past it.
        owned = self.service.list_personal_channel_instances(
            actor_user_id=self.alice_id, tenant_id=self.tenant_id)["items"][0]
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "multi-tenant-key"}):
            with self.assertRaises(IdentityServiceError):
                self.service.set_personal_channel_instance_active(
                    actor_user_id=self.alice_id, tenant_id=self.tenant_id,
                    instance_id=self.instance["id"], active=True,
                    expected_version=owned["version"],
                    recent_password=MEMBER_PASSWORD)


class CrossTenantPrivateAgentTests(_TwoTenantFixture):
    """Owner-first authorization holds across tenants, users and bypasses."""

    def setUp(self):
        super().setUp()
        self.alice_id, self.alice_token = self._plain_member(
            self.tenant_id, self.root_user_id, "alice")
        self.bob_id, self.bob_token = self._plain_member(
            self.tenant_id, self.root_user_id, "bob")
        self.carol_id, self.carol_token = self._plain_member(
            self.beta_id, self.beta_admin_id, "carol")
        self.agent_id = self._private_agent(self.alice_id, self.tenant_id, "mem-alice")

    def test_only_the_owner_passes_the_resource_check(self):
        cases = (
            (self.alice_id, self.tenant_id, True, "owner"),
            (self.bob_id, self.tenant_id, False, "same-tenant peer"),
            (self.carol_id, self.beta_id, False, "other-tenant member"),
            (self.carol_id, self.tenant_id, False, "other-tenant member, forged tenant"),
            (self.beta_admin_id, self.beta_id, False, "other-tenant admin"),
            (self.root_user_id, self.tenant_id, False, "platform admin"),
        )
        for user_id, tenant_id, expected, label in cases:
            self.assertEqual(
                self.service.check_resource_action(
                    user_id, tenant_id, "agent", self.agent_id, "read"),
                expected, label)
            self.assertEqual(
                self.service.check_resource_action(
                    user_id, tenant_id, "agent", self.agent_id, "edit"),
                expected, label + " (edit)")

    def test_the_other_tenants_projection_never_carries_the_object(self):
        body = self._body(self._request_in(
            "/api/agents?view=personal", self.carol_token, self.beta_id))
        self.assertEqual(body["agents"], [])

        # And a foreign tenant header is refused before the projection runs.
        response = self._request_in(
            "/api/agents?view=personal", self.carol_token, self.tenant_id)
        self.assertNotIn(response.status, ("200", "200 OK"))

    def test_a_delete_from_another_tenant_leaves_the_object_in_place(self):
        response = self._request_in(
            "/api/agents", self.carol_token, self.beta_id, method="POST",
            body={"action": "delete", "id": self.agent_id})
        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertIsNotNone(self.service.get_agent_binding(self.agent_id))

    def test_agent_management_actions_stay_owner_scoped_across_tenants(self):
        """The management API keeps its authorization: an owner may act on their
        own object (the positive control), and nobody else may — the same tenant
        peer, the other tenant's member and its admin are all refused."""
        bob_agent = self._private_agent(self.bob_id, self.tenant_id, "mem-bob")

        for actor_id, tenant_id, agent_id, expected in (
                (self.alice_id, self.tenant_id, self.agent_id, True),
                (self.alice_id, self.tenant_id, bob_agent, False),
                (self.carol_id, self.beta_id, self.agent_id, False),
                (self.beta_admin_id, self.beta_id, self.agent_id, False)):
            self.assertEqual(
                self.service.check_resource_action(
                    actor_id, tenant_id, "agent", agent_id, "edit"),
                expected, (actor_id, agent_id))

        # The wire agrees with the matrix for the two refusals a member could
        # attempt against a foreign object: the owner gate runs before the
        # roster is touched, so the refusal is a 403 and the binding is intact.
        for token, tenant_id, agent_id in (
                (self.alice_token, self.tenant_id, bob_agent),
                (self.carol_token, self.beta_id, self.agent_id)):
            response = self._request_in(
                "/api/agents", token, tenant_id, method="POST",
                body={"action": "update", "id": agent_id, "name": "stolen",
                      "revision": self.service.get_agent_binding(agent_id)
                      .get("revision")})
            self.assertNotIn(response.status, ("200", "200 OK"), (token, agent_id))
            self.assertNotEqual(self._body(response).get("status"), "success")

        self.assertIsNotNone(self.service.get_agent_binding(bob_agent))


class CrossTenantPersonalMemoryTests(_TwoTenantFixture):
    """Personal memory is stored and read per (tenant, user) — never shared."""

    def setUp(self):
        super().setUp()
        self.state = tempfile.mkdtemp(prefix="multi-tenant-memory-")
        patcher = patch(
            "common.state_dir.user_root",
            side_effect=lambda ident: os.path.join(
                self.state, ident.tenant_id, ident.user_id))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.alice_id, self.alice_token = self._plain_member(
            self.tenant_id, self.root_user_id, "alice")
        self.carol_id, self.carol_token = self._plain_member(
            self.beta_id, self.beta_admin_id, "carol")

    def _memory(self, user_id, tenant_id):
        from agent.memory.personal import PersonalMemoryService
        from common.runtime_identity import RuntimeIdentity

        return PersonalMemoryService(identity=RuntimeIdentity(
            agent_id="agent-" + user_id, user_id=user_id, tenant_id=tenant_id))

    def test_memory_written_in_one_tenant_is_absent_in_the_other(self):
        entry = "memory/notes.md"
        self._memory(self.alice_id, self.tenant_id).save(entry, "alice only")

        carol = self._memory(self.carol_id, self.beta_id)
        self.assertEqual(carol.list_entries(), [])
        # Reading the same entry id is not a lookup into someone else's root.
        self.assertEqual(carol.read(entry)["content"], "")

        alice = self._memory(self.alice_id, self.tenant_id)
        self.assertEqual(alice.read(entry)["content"], "alice only")

    def test_the_same_account_id_in_another_tenant_gets_its_own_root(self):
        """Defence in depth: the root is keyed by tenant *and* user, so even a
        mistaken membership cannot resolve to the other tenant's files."""
        entry = "memory/notes.md"
        self._memory(self.alice_id, self.tenant_id).save(entry, "acme copy")
        other = self._memory(self.alice_id, self.beta_id)
        self.assertEqual(other.list_entries(), [])
        self.assertEqual(other.read(entry)["content"], "")

    def test_the_other_tenants_admin_gets_no_memory_surface(self):
        self._memory(self.alice_id, self.tenant_id).save(
            "memory/notes.md", "alice only")
        response = self._request_in("/api/memory/personal",
                                    self.beta_admin_token, self.beta_id)
        body = self._body(response)
        self.assertNotIn("alice only", json.dumps(body, ensure_ascii=False))


class PublicChannelSurfaceRegressionTests(_TwoTenantFixture):
    """（现有公共渠道/目录回归）The tenant channel management gate is unchanged."""

    def setUp(self):
        super().setUp()
        self.alice_id, self.alice_token = self._plain_member(
            self.tenant_id, self.root_user_id, "alice")
        self.carol_id, self.carol_token = self._plain_member(
            self.beta_id, self.beta_admin_id, "carol")

    def test_the_member_reaches_the_shared_api_scoped_to_their_own_range(self):
        """One interface serves both roles; the range is what differs (task 6.1).

        The platform catalogue (``/api/channels``) is still platform-domain and
        keeps its administrator gate. The tenant catalogue is no longer gated by
        role: a member reaches it and is answered with **their own** connections,
        which is the same surface an administrator uses. What must not change is
        that they cannot read or write anything outside that range — asserted by
        the *shape* of the answer here, and by the write refusals below.
        """
        for method, body in (("GET", None),
                             ("POST", {"channel_type": "feishu",
                                       "display_name": "mine"})):
            response = self._request_in("/api/channels", self.alice_token,
                                        self.tenant_id, method=method,
                                        body=body)
            self.assertNotIn(response.status, ("200", "200 OK"),
                             ("/api/channels", method))

        listed = self._body(self._request_in("/api/tenant/channels",
                                             self.alice_token, self.tenant_id))
        self.assertEqual(listed.get("status"), "success")
        self.assertEqual(listed.get("scope"), "self")
        # Alice owns one personal instance (see ``_personal_instance`` callers in
        # this class); what matters is that only rows she owns are named, so a
        # colleague's row never appears.
        self.assertNotIn("cli_mt_catalogue", json.dumps(listed, ensure_ascii=False))

        # And she cannot create on the tenant's public surface: naming a target
        # she does not own is refused, and her connection would be her own anyway.
        created = self._request_in("/api/tenant/channels", self.alice_token,
                                   self.tenant_id, method="POST",
                                   body={"channel_type": "feishu",
                                         "display_name": "shared attempt",
                                         "agent_id": "target-alice"})
        self.assertNotIn(created.status, ("200", "200 OK"),
                         "a member must not create a tenant-wide connection")

    def test_each_tenant_admin_sees_only_their_own_tenant_channels(self):
        body = self._body(self._request_in("/api/tenant/channels",
                                           self.beta_admin_token, self.beta_id))
        self.assertEqual(body.get("status"), "success")
        beta_rows = body.get("items") or []

        # A tenant admin cannot list another tenant's catalogue by swapping the
        # header: the context resolves the tenant from their own memberships.
        swapped = self._body(self._request_in("/api/tenant/channels",
                                              self.beta_admin_token,
                                              self.tenant_id))
        self.assertNotEqual(swapped.get("status"), "success")
        self.assertNotIn("acme", json.dumps(swapped, ensure_ascii=False))

        # And acme's own personal rows are not in beta's tenant catalogue.
        instance = self._personal_instance(
            self.alice_id, self.tenant_id, "alice's", "cli_mt_catalogue",
            agent_id="target-alice")
        self.assertNotIn(instance["id"], {row["id"] for row in beta_rows})

    def test_a_personal_instance_does_not_appear_in_the_tenant_catalogue(self):
        """Personal rows and tenant rows stay separate catalogues: the member's
        object must not leak into the operator's list of their own tenant."""
        instance = self._personal_instance(
            self.alice_id, self.tenant_id, "alice's", "cli_mt_catalogue_b",
            agent_id="target-alice")
        rows = self.service.list_tenant_channel_instances(
            actor_user_id=self.root_user_id, tenant_id=self.tenant_id)["items"]
        self.assertNotIn(instance["id"], {row["id"] for row in rows})


if __name__ == "__main__":
    unittest.main()
