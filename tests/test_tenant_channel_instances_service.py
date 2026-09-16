# encoding:utf-8
"""Service-layer tests for tenant-owned channel instances.

Change ``tenant-owned-message-channels`` adds a tenant-scoped channel instance
lifecycle on ``IdentityService``: create (instance + one encrypted credential
bundle in a single transaction), edit/rotate, enable/disable, masked list, plus
the authorization, optimistic-concurrency, uniqueness and recent-password
guards that make it safe to expose to a tenant administrator.
"""

import json
import os
import tempfile
import unittest

from auth.crypto import decrypt_secret
from auth.service import IdentityService, IdentityServiceError
from tests._helpers import install_personal_target_roster, personal_channel_target

FEISHU_BUNDLE = {
    "feishu_app_id": "cli_tenant_a",
    "feishu_app_secret": "s3cr3t-app-secret",
    "feishu_bot_name": "Acme Bot",
}


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _expect_error(test, code, status, fn, *args, **kwargs):
    with test.assertRaises(IdentityServiceError) as caught:
        fn(*args, **kwargs)
    test.assertEqual(caught.exception.code, code)
    test.assertEqual(caught.exception.status, status)
    return caught.exception


class _ChannelServiceFixture(unittest.TestCase):
    """Two tenants, each with a completed (non-forced-change) administrator."""

    #: 32-byte hex master key; credential storage refuses to run without one.
    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        # pytest's autouse fixtures do not reach unittest.TestCase classes, so
        # the key is managed here and restored by the cleanup.
        self.addCleanup(self._restore_master_key)

        self.svc = IdentityService(_db_path())
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

        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        self.admin_b = [m for m in self.svc.list_members(self.tb)["items"]
                        if m["username"] == "globexadmin"][0]["user_id"]

        # Agents must be bound to a tenant before a channel instance may use
        # them, so seed one per tenant.
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)
        self.svc.bind_agent(tenant_id=self.tb, agent_id="agent-b",
                            private_owner_user_id=None)

    # -- helpers ---------------------------------------------------------

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _as_root(self, **over):
        args = dict(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type="feishu", display_name="Support Bot",
            agent_id="agent-a", credentials=dict(FEISHU_BUNDLE),
            recent_password="Str0ngRootFinal")
        args.update(over)
        return self.svc.create_tenant_channel_instance(**args)

    def _instance_row(self, instance_id):
        rows = self.svc._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE id=?", (instance_id,))
        return dict(rows[0]) if rows else None

    def _credential_rows(self, tenant_id):
        return [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM credentials WHERE tenant_id=? AND resource_kind='channel'"
            " ORDER BY name", (tenant_id,))]

    def _credential_versions(self, credential_id):
        return [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM credential_versions WHERE credential_id=?"
            " ORDER BY version", (credential_id,))]

    def _audit_actions(self):
        return [r["action"] for r in self.svc._store.execute(
            "SELECT action FROM audit_events ORDER BY time")]


class CreateInstanceTests(_ChannelServiceFixture):
    def test_creates_instance_with_single_encrypted_credential_bundle(self):
        created = self._as_root()
        row = self._instance_row(created["id"])
        self.assertEqual(row["tenant_id"], self.ta)
        self.assertEqual(row["channel_type"], "feishu")
        self.assertEqual(row["display_name"], "Support Bot")
        self.assertEqual(row["agent_id"], "agent-a")
        self.assertEqual(row["active"], 1)
        self.assertEqual(row["version"], 1)

        creds = self._credential_rows(self.ta)
        self.assertEqual(len(creds), 1, "one bundle per instance, not one row per field")
        self.assertEqual(creds[0]["resource_kind"], "channel")
        self.assertEqual(creds[0]["resource_id"], created["id"])
        self.assertEqual(creds[0]["name"], "channel:%s" % created["id"])
        self.assertEqual(json.loads(decrypt_secret(creds[0]["ciphertext"])),
                         FEISHU_BUNDLE)

    def test_credential_bundle_is_encrypted_at_rest(self):
        created = self._as_root()
        cred = self._credential_rows(self.ta)[0]
        self.assertNotIn("s3cr3t-app-secret", cred["ciphertext"])
        self.assertNotIn("feishu_app_secret", cred["ciphertext"])
        versions = self._credential_versions(cred["id"])
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["action"], "create")
        self.assertNotIn("s3cr3t-app-secret", versions[0]["ciphertext"])

    def test_creation_is_audited_as_instance_create_and_credential_create(self):
        self._as_root()
        actions = self._audit_actions()
        self.assertIn("credential.create", actions)
        self.assertTrue(any("channel" in a for a in actions),
                        "expected a channel instance audit event, got %r" % actions)

    def test_duplicate_display_name_rolls_back_everything(self):
        self._as_root()
        before_creds = len(self._credential_rows(self.ta))
        _expect_error(self, "conflict", 409, self._as_root)
        self.assertEqual(len(self._credential_rows(self.ta)), before_creds,
                         "a rejected create must not leave an orphan credential")
        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM tenant_channel_instances")
        self.assertEqual(rows[0]["c"], 1)

    def test_same_channel_type_with_another_display_name_is_allowed(self):
        first = self._as_root()
        second = self._as_root(display_name="Sales Bot")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self._credential_rows(self.ta)), 2)

    def test_agent_from_another_tenant_is_rejected(self):
        _expect_error(self, "forbidden", 403, self._as_root, agent_id="agent-b")
        self.assertEqual(self._credential_rows(self.ta), [])

    def test_unknown_agent_is_rejected(self):
        _expect_error(self, "forbidden", 403, self._as_root, agent_id="ghost")

    def test_empty_credential_bundle_is_rejected(self):
        _expect_error(self, "bad_request", 400, self._as_root, credentials={})

    def test_unsupported_channel_type_is_rejected(self):
        _expect_error(self, "bad_request", 400, self._as_root,
                      channel_type="not_a_channel")

    def test_wechatcom_app_is_not_tenant_configurable(self):
        """Deferred slice: fixed-port webhook inbound cannot be per-instance.

        The rejection must also leave no trace, so a rejected attempt cannot be
        used to probe or to half-create state.
        """
        _expect_error(self, "bad_request", 400, self._as_root,
                      channel_type="wechatcom_app")
        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM tenant_channel_instances")
        self.assertEqual(rows[0]["c"], 0)
        self.assertEqual(self._credential_rows(self.ta), [])


class InstanceScopeTests(_ChannelServiceFixture):
    """Scope and ownership constraints on channel instances.

    Change ``enable-member-personal-console``: a member may own a *personal*
    channel instance, which lives in the same table as a tenant one. These lock
    the two properties the console depends on — a personal instance must always
    name a real owner, and it must never surface in the public list.
    """

    #: Private Agents this class gives its members, so a personal instance has a
    #: target it is actually allowed to name. A *shared* Agent is what these
    #: tests used to pass, and the write path now refuses exactly that.
    PERSONAL_TARGETS = ("member-target-a", "member-target-b")

    def setUp(self):
        super().setUp()
        install_personal_target_roster(
            self, "agent-a", "agent-b", *self.PERSONAL_TARGETS)

    def _add_member(self, username="acmemember"):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="MemTempPass1",
            roles=["member"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]

    def _member_target(self, user_id, agent_id="member-target-a"):
        """The private Agent this member's personal instance routes to."""
        return personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=user_id, agent_id=agent_id)

    def test_defaults_to_tenant_scope_without_an_owner(self):
        created = self._as_root()
        self.assertEqual(created["scope"], "tenant")
        self.assertIsNone(created["owner_user_id"])
        row = self._instance_row(created["id"])
        self.assertEqual(row["scope"], "tenant")
        self.assertIsNone(row["owner_user_id"])

    def test_a_personal_instance_records_its_owner(self):
        member = self._add_member()
        created = self._as_root(
            scope="user", owner_user_id=member,
            agent_id=self._member_target(member))
        self.assertEqual(created["scope"], "user")
        self.assertEqual(created["owner_user_id"], member)
        row = self._instance_row(created["id"])
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], member)

    def test_a_personal_instance_requires_an_owner(self):
        _expect_error(self, "bad_request", 400, self._as_root, scope="user")

    def test_a_tenant_instance_must_not_name_an_owner(self):
        member = self._add_member()
        _expect_error(self, "bad_request", 400, self._as_root,
                      scope="tenant", owner_user_id=member)

    def test_an_unknown_scope_is_rejected(self):
        _expect_error(self, "bad_request", 400, self._as_root,
                      scope="department")

    def test_the_owner_must_be_a_known_member(self):
        _expect_error(self, "bad_request", 400, self._as_root,
                      scope="user", owner_user_id="ghost")

    def test_the_owner_must_belong_to_this_tenant(self):
        _expect_error(self, "bad_request", 400, self._as_root,
                      scope="user", owner_user_id=self.admin_b)

    def test_a_rejected_scope_leaves_no_instance_or_credential(self):
        member = self._add_member()
        _expect_error(self, "bad_request", 400, self._as_root, scope="user")
        self.assertEqual(self._credential_rows(self.ta), [])
        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM tenant_channel_instances")
        self.assertEqual(rows[0]["c"], 0)

    def test_the_public_list_does_not_expose_personal_instances(self):
        """A personal instance is not the tenant's to administer."""
        member = self._add_member()
        self._as_root(display_name="Tenant Bot")
        # A different application on purpose: a personal instance may not point
        # at the tenant's app (task 6.3), and this test is about the *listing*.
        self._as_root(display_name="Private Bot", scope="user",
                      owner_user_id=member,
                      agent_id=self._member_target(member),
                      credentials=dict(FEISHU_BUNDLE,
                                       feishu_app_id="cli_private"))
        listing = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        self.assertEqual([i["display_name"] for i in listing["items"]],
                         ["Tenant Bot"])


    def test_renaming_does_not_collide_with_a_personal_instance(self):
        """The name check must use the same key as the index, or a rename would
        be refused for a name that is free in its own scope."""
        member = self._add_member()
        tenant = self._as_root(display_name="Tenant Bot")
        self._as_root(display_name="Private Bot", scope="user",
                      owner_user_id=member,
                      agent_id=self._member_target(member),
                      credentials=dict(FEISHU_BUNDLE,
                                       feishu_app_id="cli_private"))
        updated = self.svc.update_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=tenant["id"], expected_version=tenant["version"],
            recent_password="Str0ngRootFinal", display_name="Private Bot")
        self.assertEqual(updated["display_name"], "Private Bot")
        self.assertEqual(updated["scope"], "tenant")

    def test_the_runtime_row_exposes_scope_and_owner(self):
        """Inbound routing has to know whether an instance is personal and
        whose it is before it can resolve a sender."""
        member = self._add_member()
        created = self._as_root(
            scope="user", owner_user_id=member,
            agent_id=self._member_target(member))
        row = self.svc.get_tenant_channel_instance_row(created["id"])
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], member)


class ListInstanceTests(_ChannelServiceFixture):
    def test_list_returns_masked_projection_without_any_secret(self):
        self._as_root()
        listed = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        self.assertEqual(listed["total"], 1)
        item = listed["items"][0]
        self.assertEqual(item["display_name"], "Support Bot")
        self.assertTrue(item["active"])
        blob = json.dumps(listed, default=str)
        self.assertNotIn("s3cr3t-app-secret", blob)
        self.assertNotIn("cli_tenant_a", blob)
        self.assertEqual(item.get("credentials"), None)
        self.assertIn("channel_type", item)

    def test_list_never_returns_another_tenants_instances(self):
        self._as_root()
        self.svc.create_tenant_channel_instance(
            actor_user_id=self.admin_b, tenant_id=self.tb, channel_type="feishu",
            display_name="Globex Bot", agent_id="", credentials=dict(FEISHU_BUNDLE),
            recent_password="Str0ngGlobexFinal")
        mine = self.svc.list_tenant_channel_instances(
            actor_user_id=self.admin_b, tenant_id=self.tb)
        self.assertEqual([i["display_name"] for i in mine["items"]], ["Globex Bot"])
        theirs = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        self.assertEqual([i["display_name"] for i in theirs["items"]],
                         ["Support Bot"])


class UpdateInstanceTests(_ChannelServiceFixture):
    def test_update_bumps_version_and_rotates_bundle_without_a_new_row(self):
        created = self._as_root()
        cred_id = self._credential_rows(self.ta)[0]["id"]
        new_bundle = dict(FEISHU_BUNDLE, feishu_app_secret="r0tated-secret")
        updated = self.svc.update_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], display_name="Renamed Bot",
            credentials=new_bundle, expected_version=1,
            recent_password="Str0ngRootFinal")
        row = self._instance_row(created["id"])
        self.assertEqual(row["display_name"], "Renamed Bot")
        self.assertEqual(row["version"], 2)
        self.assertEqual(updated["version"], 2)

        creds = self._credential_rows(self.ta)
        self.assertEqual(len(creds), 1, "rotation must not add a credential row")
        self.assertEqual(creds[0]["id"], cred_id)
        self.assertEqual(json.loads(decrypt_secret(creds[0]["ciphertext"])), new_bundle)
        versions = self._credential_versions(cred_id)
        self.assertEqual([v["action"] for v in versions], ["create", "rotated"])

    def test_update_without_credentials_leaves_the_bundle_untouched(self):
        created = self._as_root()
        cred_before = self._credential_rows(self.ta)[0]
        self.svc.update_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], display_name="Renamed Only",
            expected_version=1, recent_password="Str0ngRootFinal")
        cred_after = self._credential_rows(self.ta)[0]
        self.assertEqual(cred_before["ciphertext"], cred_after["ciphertext"])
        self.assertEqual(cred_before["version"], cred_after["version"])

    def test_stale_version_conflicts_and_changes_nothing(self):
        created = self._as_root()
        before = self._instance_row(created["id"])
        cred_before = self._credential_rows(self.ta)[0]["ciphertext"]
        _expect_error(
            self, "conflict", 409, self.svc.update_tenant_channel_instance,
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], display_name="Should Not Apply",
            credentials=dict(FEISHU_BUNDLE, feishu_app_secret="should-not-apply"),
            expected_version=99, recent_password="Str0ngRootFinal")
        self.assertEqual(self._instance_row(created["id"]), before)
        self.assertEqual(self._credential_rows(self.ta)[0]["ciphertext"], cred_before)

    def test_rename_onto_an_existing_display_name_conflicts(self):
        self._as_root()
        second = self._as_root(display_name="Sales Bot")
        _expect_error(
            self, "conflict", 409, self.svc.update_tenant_channel_instance,
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=second["id"], display_name="Support Bot",
            expected_version=1, recent_password="Str0ngRootFinal")

    def test_update_of_another_tenants_instance_is_not_found(self):
        self._as_root()
        other = self.svc.create_tenant_channel_instance(
            actor_user_id=self.admin_b, tenant_id=self.tb,
            channel_type="feishu", display_name="Globex Bot",
            agent_id="", credentials=dict(FEISHU_BUNDLE),
            recent_password="Str0ngGlobexFinal")
        _expect_error(
            self, "not_found", 404, self.svc.update_tenant_channel_instance,
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=other["id"], display_name="Hijacked",
            expected_version=1, recent_password="Str0ngRootFinal")


class SetActiveTests(_ChannelServiceFixture):
    def test_disable_changes_only_the_switch(self):
        created = self._as_root()
        cred_before = self._credential_rows(self.ta)[0]
        result = self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], active=False, expected_version=1,
            recent_password="Str0ngRootFinal")
        row = self._instance_row(created["id"])
        self.assertEqual(row["active"], 0)
        self.assertEqual(row["version"], 2)
        self.assertFalse(result["active"])
        cred_after = self._credential_rows(self.ta)[0]
        self.assertEqual(cred_before["ciphertext"], cred_after["ciphertext"])
        self.assertEqual(cred_before["version"], cred_after["version"])
        self.assertEqual(len(self._credential_versions(cred_before["id"])), 1)

    def test_disabling_frees_the_display_name_for_reuse(self):
        first = self._as_root()
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=first["id"], active=False, expected_version=1,
            recent_password="Str0ngRootFinal")
        recreated = self._as_root()
        self.assertNotEqual(recreated["id"], first["id"])

    def test_re_enable_restores_the_instance(self):
        created = self._as_root()
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], active=False, expected_version=1,
            recent_password="Str0ngRootFinal")
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], active=True, expected_version=2,
            recent_password="Str0ngRootFinal")
        self.assertEqual(self._instance_row(created["id"])["active"], 1)

    def test_stale_version_on_toggle_conflicts(self):
        created = self._as_root()
        _expect_error(
            self, "conflict", 409, self.svc.set_tenant_channel_instance_active,
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], active=False, expected_version=7,
            recent_password="Str0ngRootFinal")
        self.assertEqual(self._instance_row(created["id"])["active"], 1)


class AuthorizationTests(_ChannelServiceFixture):
    def test_tenant_admin_cannot_touch_another_tenants_instances(self):
        self._as_root()
        _expect_error(
            self, "forbidden", 403, self.svc.list_tenant_channel_instances,
            actor_user_id=self.admin_b, tenant_id=self.ta)
        _expect_error(
            self, "forbidden", 403, self.svc.create_tenant_channel_instance,
            actor_user_id=self.admin_b, tenant_id=self.ta,
            channel_type="feishu", display_name="Intruder", agent_id="",
            credentials=dict(FEISHU_BUNDLE), recent_password="Str0ngGlobexFinal")

    def test_tenant_admin_can_manage_its_own_tenant(self):
        created = self.svc.create_tenant_channel_instance(
            actor_user_id=self.admin_b, tenant_id=self.tb,
            channel_type="feishu", display_name="Globex Bot", agent_id="",
            credentials=dict(FEISHU_BUNDLE), recent_password="Str0ngGlobexFinal")
        self.assertEqual(created["tenant_id"], self.tb)
        listed = self.svc.list_tenant_channel_instances(
            actor_user_id=self.admin_b, tenant_id=self.tb)
        self.assertEqual(listed["total"], 1)

    def test_plain_member_is_denied(self):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta, operation="create-new",
            username="mallory", display_name="Mallory",
            temporary_password="Str0ngPassTmp", roles=[], department_id=None)
        mallory = [m for m in self.svc.list_members(self.ta)["items"]
                   if m["username"] == "mallory"][0]
        _expect_error(
            self, "forbidden", 403, self.svc.create_tenant_channel_instance,
            actor_user_id=mallory["user_id"], tenant_id=self.ta,
            channel_type="feishu", display_name="Sneaky", agent_id="",
            credentials=dict(FEISHU_BUNDLE), recent_password="Str0ngPassTmp")


class RecentPasswordTests(_ChannelServiceFixture):
    def test_wrong_recent_password_is_rejected(self):
        _expect_error(self, "invalid_old", 401, self._as_root,
                      recent_password="not-my-password")
        self.assertEqual(self._credential_rows(self.ta), [])

    def test_missing_recent_password_is_rejected(self):
        _expect_error(self, "invalid_old", 401, self._as_root, recent_password="")

    def test_success_path_never_retains_or_returns_the_password(self):
        created = self._as_root()
        blob = json.dumps(created, default=str)
        self.assertNotIn("Str0ngRootFinal", blob)
        for row in self.svc._store.execute("SELECT * FROM audit_events"):
            self.assertNotIn("Str0ngRootFinal", json.dumps(dict(row), default=str))
        for row in self.svc._store.execute("SELECT * FROM credential_versions"):
            self.assertNotIn("Str0ngRootFinal", json.dumps(dict(row), default=str))


class ChannelInstanceCredentialInjectionTests(_ChannelServiceFixture):
    """The system-side injection path: no actor, dual-keyed, never over HTTP.

    Channel startup is an unattended path, so it cannot use the actor-checked
    ``resolve_credential``. This path is deliberately internal: it is keyed by
    tenant *and* instance (never by name alone), and it must not loosen the
    actor-checked path it sits beside.
    """

    def _member(self):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta, operation="create-new",
            username="erin", display_name="Erin",
            temporary_password="Str0ngPassTmp", roles=[], department_id=None)
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == "erin"][0]["user_id"]

    def test_resolves_the_bundle_by_tenant_and_instance(self):
        created = self._as_root()
        bundle = self.svc.channel_instance_credentials(self.ta, created["id"])
        self.assertEqual(bundle, FEISHU_BUNDLE)

    def test_unknown_instance_is_not_found(self):
        _expect_error(self, "not_found", 404, self.svc.channel_instance_credentials,
                      self.ta, "chan_does_not_exist")

    def test_another_tenants_instance_is_not_found(self):
        created = self._as_root()
        _expect_error(self, "not_found", 404, self.svc.channel_instance_credentials,
                      self.tb, created["id"])

    def test_revoked_credential_cannot_be_injected(self):
        created = self._as_root()
        self.svc.revoke_credential(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            name="channel:%s" % created["id"])
        _expect_error(self, "not_found", 404, self.svc.channel_instance_credentials,
                      self.ta, created["id"])

    def test_credential_bound_to_a_different_resource_is_refused(self):
        created = self._as_root()
        with self.svc._store.connect() as con:
            con.execute("UPDATE credentials SET resource_id='somewhere-else'"
                        " WHERE name=?", ("channel:%s" % created["id"],))
        _expect_error(self, "forbidden", 403, self.svc.channel_instance_credentials,
                      self.ta, created["id"])

    def test_injection_takes_no_actor(self):
        import inspect
        params = inspect.signature(self.svc.channel_instance_credentials).parameters
        self.assertEqual(list(params), ["tenant_id", "instance_id"])

    def test_injection_path_is_not_reachable_over_http(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        guarded = [root / "auth" / "http_policy.py"]
        guarded += sorted((root / "channel" / "web").rglob("*.py"))
        for path in guarded:
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(
                "channel_instance_credentials", text,
                "%s must not reach the internal injection path" % path)

    def test_actor_checked_resolve_credential_is_not_loosened(self):
        """The internal bypass must not have relaxed the actor-checked path."""
        created = self._as_root()
        erin = self._member()
        _expect_error(
            self, "forbidden", 403, self.svc.resolve_credential,
            actor_user_id=erin, tenant_id=self.ta,
            name="channel:%s" % created["id"],
            resource_kind="channel", resource_id=created["id"])
        # ...while the tenant controller still can resolve it.
        self.assertEqual(
            self.svc.resolve_credential(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                name="channel:%s" % created["id"],
                resource_kind="channel", resource_id=created["id"]),
            json.dumps(FEISHU_BUNDLE, ensure_ascii=False, sort_keys=True))


class NoNewFunctionalPermissionTests(_ChannelServiceFixture):
    """The capability is gated by tenant control, not by a new permission.

    ``docs``/``design`` require that tenant-owned channels add no functional
    permission and leave the built-in role defaults untouched, so a future
    catalogue edit cannot silently widen what a provisioned role can do.
    """

    def test_no_channel_permission_entered_the_catalog(self):
        from auth.policy import PERMISSION_CATALOG
        offenders = [p for p in PERMISSION_CATALOG if "channel" in p.lower()]
        self.assertEqual(offenders, [])

    def test_builtin_role_defaults_are_unchanged(self):
        # The channels capability adds no permission and the built-in seed still
        # comes from the explicit policy defaults (single source of truth).
        from auth.policy import (
            BUILTIN_ROLES, TENANT_ADMIN_CODE, MEMBER_CODE, default_permissions_for,
        )
        rows = self.svc._store.execute(
            "SELECT code, permissions_json FROM roles WHERE tenant_id=? AND builtin=1",
            (self.ta,))
        by_code = {r["code"]: json.loads(r["permissions_json"]) for r in rows}
        self.assertEqual(sorted(by_code), sorted(BUILTIN_ROLES))
        self.assertEqual(by_code["tenant_admin"],
                         sorted(default_permissions_for(TENANT_ADMIN_CODE)))
        self.assertEqual(by_code["member"],
                         sorted(default_permissions_for(MEMBER_CODE)))


if __name__ == "__main__":
    unittest.main()
