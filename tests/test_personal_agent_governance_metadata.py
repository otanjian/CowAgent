# encoding:utf-8
"""Governance metadata vs private content (task 3.3).

An administrator must be able to *govern* members' personal access — know that it
exists, on which platform, for whom, and whether it was stopped — without reading
the member's private configuration. The stop action and its redacted audit were
built in task 2.5; this file pins the third leg: the discoverability read that
makes the action usable, and the exact boundary of what it may reveal.

The line is easy to blur later ("the admin needs the display name to know what
they are stopping"), so each forbidden field is asserted individually rather than
by spot-checking the happy path.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError

#: Fields a governance read must never carry: the member's own configuration.
PRIVATE_FIELDS = (
    "display_name",
    "agent_id",
    "credential_id",
    "credentials",
    "params",
    "params_json",
    "external_identity_id",
    "ciphertext",
    "version",
)

FEISHU_BUNDLE = {
    "feishu_app_id": "cli_private_secret_id",
    "feishu_app_secret": "s3cr3t-private-app-secret",
    "feishu_bot_name": "Private Bot",
}


class _Fixture(unittest.TestCase):
    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    ROOT_PASSWORD = "Str0ngRootFinal"

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)
        self.svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", self.ROOT_PASSWORD)
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a")
        self.alice = self._member("alice")
        self.bob = self._member("bob")

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _member(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]

    def _personal_instance(self, user_id, name="我的飞书", channel_type="feishu",
                           scope="user"):
        # Each instance gets its own external application: two instances may not
        # share one (task 6.3), and this file is about governance metadata, not
        # about that rule.
        self._app_seq = getattr(self, "_app_seq", 0) + 1
        bundle = dict(FEISHU_BUNDLE,
                      feishu_app_id="cli_private_secret_%d" % self._app_seq)
        return self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type=channel_type, display_name=name,
            recent_password=self.ROOT_PASSWORD, agent_id="agent-a",
            credentials=bundle, scope=scope,
            owner_user_id=user_id if scope == "user" else None)

    def _governance(self):
        return self.svc.list_personal_channel_instances_for_governance(
            self.root["id"], self.ta)

    def _audit_rows(self, action):
        return self.svc._store.execute(
            "SELECT redacted_changes FROM audit_events WHERE action=?", (action,))


class GovernanceListingTests(_Fixture):
    def test_an_administrator_sees_that_personal_access_exists(self):
        instance = self._personal_instance(self.alice)

        rows = self._governance()

        self.assertEqual([r["id"] for r in rows], [instance["id"]])

    def test_the_listing_carries_the_owner_and_the_platform(self):
        self._personal_instance(self.alice, channel_type="feishu")

        row = self._governance()[0]

        self.assertEqual(row["owner_user_id"], self.alice)
        self.assertEqual(row["channel_type"], "feishu")
        self.assertTrue(row["active"])
        self.assertFalse(row["governance_disabled"])

    def test_the_listing_never_carries_private_configuration(self):
        self._personal_instance(self.alice)

        for row in self._governance():
            for field in PRIVATE_FIELDS:
                self.assertNotIn(field, row, field)

    def test_the_projection_is_a_choice_not_an_absence_of_data(self):
        """The withheld fields exist on the row; they are projected away."""
        instance = self._personal_instance(self.alice, name="我的飞书")
        raw = self.svc._store.execute(
            "SELECT display_name, agent_id FROM tenant_channel_instances WHERE id=?",
            (instance["id"],))[0]

        self.assertEqual(raw["display_name"], "我的飞书")
        self.assertEqual(raw["agent_id"], "agent-a")
        self.assertNotIn("display_name", self._governance()[0])

    def test_the_listing_does_not_leak_the_credential_material(self):
        self._personal_instance(self.alice)

        rendered = repr(self._governance())

        for secret in FEISHU_BUNDLE.values():
            self.assertNotIn(secret, rendered)

    def test_the_listing_covers_every_member_not_just_the_caller(self):
        self._personal_instance(self.alice, name="alice 的")
        self._personal_instance(self.bob, name="bob 的")

        owners = {row["owner_user_id"] for row in self._governance()}

        self.assertEqual(owners, {self.alice, self.bob})

    def test_a_stopped_instance_is_reported_as_stopped(self):
        instance = self._personal_instance(self.alice)
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance["id"], disabled=True,
            recent_password=self.ROOT_PASSWORD, reason="违规")

        row = self._governance()[0]

        self.assertTrue(row["governance_disabled"])
        self.assertFalse(row["active"], "the stop must take effect immediately")
        self.assertIsNotNone(row["governance_disabled_at"])

    def test_tenant_scoped_instances_are_absent_from_the_governance_view(self):
        self._personal_instance(self.alice, name="团队飞书", scope="tenant")

        self.assertEqual(self._governance(), [])


class GovernanceListingAuthorizationTests(_Fixture):
    def test_a_member_may_not_list_personal_access(self):
        self._personal_instance(self.alice)

        with self.assertRaises(IdentityServiceError) as exc:
            self.svc.list_personal_channel_instances_for_governance(
                self.bob, self.ta)

        self.assertEqual(exc.exception.code, "forbidden")

    def test_an_administrator_of_another_tenant_may_not_list_it(self):
        self._personal_instance(self.alice)
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex",
            admin_password="Str0ngPass9", recent_password=self.ROOT_PASSWORD,
            shared_root="/s/globex")
        tb = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]
        gadmin = [m for m in self.svc.list_members(tb)["items"]
                  if m["username"] == "globexadmin"][0]["user_id"]

        with self.assertRaises(IdentityServiceError) as exc:
            self.svc.list_personal_channel_instances_for_governance(gadmin, self.ta)

        self.assertEqual(exc.exception.code, "forbidden")


class GovernanceMetadataIsNotAReadTests(_Fixture):
    """The metadata read must not become a content read through another door."""

    def test_stopping_does_not_grant_the_admin_the_configuration(self):
        """A stop blocks what would restore service; it is not a config read."""
        instance = self._personal_instance(self.alice, name="改名前的飞书")
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance["id"], disabled=True,
            recent_password=self.ROOT_PASSWORD, reason="违规")

        with self.assertRaises(IdentityServiceError) as exc:
            self.svc.update_tenant_channel_instance(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                instance_id=instance["id"], expected_version=2,
                recent_password=self.ROOT_PASSWORD,
                credentials=dict(FEISHU_BUNDLE))

        self.assertEqual(exc.exception.code, "governance_disabled")

    def test_the_stop_itself_is_audited_without_private_material(self):
        instance = self._personal_instance(self.alice)
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance["id"], disabled=True,
            recent_password=self.ROOT_PASSWORD, reason="违规")

        rendered = repr([r["redacted_changes"] for r in
                         self._audit_rows("channel.personal.governance_disable")])

        self.assertNotIn("我的飞书", rendered)
        self.assertNotIn("feishu", rendered)
        self.assertNotIn(FEISHU_BUNDLE["feishu_app_secret"], rendered)
        self.assertNotIn(self.alice, rendered)


if __name__ == "__main__":
    unittest.main()
