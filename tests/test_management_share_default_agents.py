# encoding:utf-8
"""Operational correction of private tenant-default Agents (task 5b.8/5b.9).

Installations that predate the fix hold an Agent that is both the tenant's
default and private to its initial admin. Every other member is then refused,
which users experience as "a conversation forces me to pick an Agent".

The service-level invariant is pinned in ``test_default_agent_tenant_shared``;
these tests pin the *operational* half: an operator can preview the correction,
apply it, and re-run safely, and the legacy ``register`` migration cannot leave
a private default behind.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cli.commands.management import management  # noqa: E402
from auth.service import IdentityService  # noqa: E402


class ShareDefaultAgentsCliTestCase(unittest.TestCase):
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
        # A private default: the shape the old code produced.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="solo",
                            private_owner_user_id=self.root["id"])
        self.runner = CliRunner()
        # Point the CLI at this test's database.
        self._patch = patch("cli.commands.management._identity_db_path",
                            lambda: self.db)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _invoke(self, *args):
        return self.runner.invoke(management, list(args))

    def test_dry_run_reports_without_writing(self):
        result = self._invoke("share-default-agents", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("solo", result.output)
        # The point of --dry-run: nothing changed.
        self.assertEqual(
            self.svc.get_agent_binding("solo")["private_owner_user_id"],
            self.root["id"])

    def test_apply_then_rerun_is_idempotent(self):
        first = self._invoke("share-default-agents")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("Updated 1", first.output)
        self.assertIsNone(
            self.svc.get_agent_binding("solo")["private_owner_user_id"])

        second = self._invoke("share-default-agents")
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("Updated 0", second.output)

    def test_dry_run_unknown_tenant_fails_cleanly(self):
        result = self._invoke("share-default-agents", "--dry-run",
                              "--tenant-code", "nope")
        self.assertNotEqual(result.exit_code, 0)


class RegisterLeavesNoPrivateDefaultTestCase(unittest.TestCase):
    """``register`` designates the legacy default tenancy, so it must not leave
    a private Agent that the tenant resolves as its default."""

    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "identity.db")
        self.shared = tempfile.mkdtemp(prefix="shared-")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="default", tenant_name="Default", admin_username="admin",
            admin_display="Admin", admin_password="Str0ngAdminPass",
            shared_root=self.shared, allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.admin = self.svc.list_platform_users()[0]
        self.runner = CliRunner()
        self._patch_db = patch("cli.commands.management._identity_db_path",
                               lambda: self.db)
        self._patch_db.start()
        # ``register`` enumerates the real registry; give it one Agent.
        self._patch_registry = patch(
            "cli.commands.management._registered_agent_ids", lambda: ["alpha"])
        self._patch_registry.start()

    def tearDown(self):
        self._patch_registry.stop()
        self._patch_db.stop()

    def test_register_ends_with_a_shared_default(self):
        result = self.runner.invoke(management, [
            "register", "--tenant-code", "default", "--admin-username", "admin"])
        self.assertEqual(result.exit_code, 0, result.output)
        binding = self.svc.get_agent_binding("alpha")
        self.assertEqual(binding["tenant_id"], self.tid)
        self.assertIsNone(
            binding["private_owner_user_id"],
            "register left the tenant default private to the admin")


if __name__ == "__main__":
    unittest.main()
