# encoding:utf-8
"""Operational repair that gives a tenant-shared Agent its owner back.

An Agent can be shared by accident — most of all by the appointment path that
used to clear ``private_owner_user_id`` as a side effect — and until now the
only way back was raw SQL. ``make_agent_tenant_shared`` is the deliberate,
audited widening; this command is its inverse for the case where the widening
was never intended, so it narrows who can read the Agent's memory and is
therefore previewable, audited and idempotent.

The service-level behaviour is pinned in ``test_default_agent_tenant_shared``;
these tests pin the *operational* half: an operator can see what would change,
apply it, re-run safely, and read a refusal instead of a silent publication.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth.service import IdentityService  # noqa: E402
from cli.commands.management import management  # noqa: E402


class RestorePrivateOwnerCliTestCase(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "identity.db")
        self.shared = tempfile.mkdtemp(prefix="shared-")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared, allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.member_id = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            operation="create-new", username="member", display_name="Member",
            temporary_password="TmpPass123!", roles=[])["user_id"]
        # The shape the old appointment path left behind: a tenant-shared Agent
        # that used to belong to the member.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="mine")
        self.runner = CliRunner()
        self._patch = patch("cli.commands.management._identity_db_path",
                            lambda: self.db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _invoke(self, *args):
        return self.runner.invoke(management, list(args))

    def _owner(self):
        return self.svc.get_agent_binding("mine")["private_owner_user_id"]

    def _audit(self):
        return [dict(r) for r in self.svc._store.execute(  # type: ignore[attr-defined]
            "SELECT * FROM audit_events WHERE action='agent.restore_private_owner'")]

    def test_dry_run_reports_without_writing(self):
        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "member", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("mine", result.output)
        self.assertIn("member", result.output)
        self.assertIsNone(self._owner(), "a preview wrote the repair")
        self.assertEqual(self._audit(), [])

    def test_apply_restores_the_owner_and_audits_it(self):
        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "member")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self._owner(), self.member_id)
        rows = self._audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "success")
        self.assertEqual(rows[0]["target"], "agent:mine")
        self.assertIn(self.member_id, rows[0]["redacted_changes"])

    def test_a_second_run_is_a_no_op(self):
        self._invoke("restore-private-owner", "--agent-id", "mine",
                     "--owner-username", "member")
        again = self._invoke("restore-private-owner", "--agent-id", "mine",
                            "--owner-username", "member")
        self.assertEqual(again.exit_code, 0, again.output)
        self.assertEqual(self._owner(), self.member_id)
        self.assertEqual(len(self._audit()), 1,
                         "a no-op repair appended a second audit event")

    def test_a_tenant_default_is_refused_with_its_reason(self):
        """The default is the entry every member shares, so it stays shared."""
        self.svc.appoint_tenant_default_agent(
            tenant_id=self.tid, agent_id="mine", actor_user_id=self.root["id"])

        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "member")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("default", result.output)
        self.assertIsNone(self._owner())

    def test_an_unknown_owner_is_refused(self):
        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "nobody")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(self._owner(), "an unknown owner wrote an owner")

    def test_an_agent_that_is_not_bound_is_refused(self):
        result = self._invoke("restore-private-owner", "--agent-id", "ghost",
                              "--owner-username", "member")
        self.assertNotEqual(result.exit_code, 0)

    def test_the_actor_is_gated_when_one_is_named(self):
        """A named actor must actually qualify, so the audit cannot be forged."""
        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "member",
                              "--actor-username", "member")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(self._owner())

    def test_a_qualifying_actor_is_recorded_on_the_event(self):
        result = self._invoke("restore-private-owner", "--agent-id", "mine",
                              "--owner-username", "member",
                              "--actor-username", "root")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self._audit()[0]["actor_user_id"], self.root["id"])


if __name__ == "__main__":
    unittest.main()
