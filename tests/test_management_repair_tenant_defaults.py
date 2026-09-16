# encoding:utf-8
"""Operational repair of illegal tenant-default pointers (task 5b.8/5b.9, 4.5).

An Agent may not be both private and the tenant's default: the default is the
entry every member shares, so ``private_owner_user_id`` would lock them out of
it. The historical repair cleared the *owner*, which silently published a
member's private Agent — and the publication survived the default being moved
away, so the Agent kept reading as a tenant-level one (it appeared in every
member's chat picker).

The repair now only ever releases the *pointer*: the owner is left exactly as it
was, and sharing an Agent stays an explicit console action
(``make_agent_tenant_shared``). The service-level side is pinned in
``test_default_agent_tenant_shared``; these tests pin the *operational* half: an
operator can preview the repair, apply it, and re-run safely, without any
ownership changing.
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


class RepairTenantDefaultsCliTestCase(unittest.TestCase):
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
        # The shape the old appointment path produced: a private Agent that is
        # also the stored tenant default. Written directly, because the write
        # path refuses it now (that refusal is the fix).
        self.svc.bind_agent(tenant_id=self.tid, agent_id="solo",
                            private_owner_user_id=self.root["id"])
        self.svc._store.execute(
            "UPDATE tenants SET default_agent_id='solo' WHERE id=?", (self.tid,))
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
        result = self._invoke("repair-tenant-defaults", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("solo", result.output)
        self.assertIn("owner preserved", result.output)
        # The point of --dry-run: nothing changed.
        self.assertEqual(self.svc.tenant_default_agent_id(self.tid), "solo")
        self.assertEqual(
            self.svc.get_agent_binding("solo")["private_owner_user_id"],
            self.root["id"])

    def test_apply_releases_the_pointer_and_keeps_the_owner(self):
        first = self._invoke("repair-tenant-defaults")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("Released 1", first.output)
        self.assertIsNone(self.svc.tenant_default_agent_id(self.tid))
        self.assertEqual(
            self.svc.get_agent_binding("solo")["private_owner_user_id"],
            self.root["id"],
            "the repair must not publish a private Agent")

        # Idempotent: the pointer is already gone.
        second = self._invoke("repair-tenant-defaults")
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("Released 0", second.output)

    def test_the_repair_is_audited_with_the_preserved_owner(self):
        self._invoke("repair-tenant-defaults")
        rows = [dict(r) for r in self.svc._store.execute(  # type: ignore[attr-defined]
            "SELECT * FROM audit_events WHERE action='tenant.default_agent.repaired'")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "success")
        self.assertIn(self.root["id"], rows[0]["redacted_changes"])

    def test_the_legacy_command_name_still_runs_the_repair(self):
        """A runbook may still call the old name; it must not share anything."""
        result = self._invoke("share-default-agents")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(self.svc.tenant_default_agent_id(self.tid))
        self.assertEqual(
            self.svc.get_agent_binding("solo")["private_owner_user_id"],
            self.root["id"])

    def test_dry_run_unknown_tenant_fails_cleanly(self):
        result = self._invoke("repair-tenant-defaults", "--dry-run",
                              "--tenant-code", "nope")
        self.assertNotEqual(result.exit_code, 0)

    def test_a_private_agent_that_only_resolves_is_left_alone(self):
        """No stored pointer means no default to repair — and no publication."""
        self.svc._store.execute(
            "UPDATE tenants SET default_agent_id=NULL WHERE id=?", (self.tid,))

        result = self._invoke("repair-tenant-defaults")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Released 0", result.output)
        self.assertEqual(
            self.svc.get_agent_binding("solo")["private_owner_user_id"],
            self.root["id"])


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

    def test_register_shares_it_through_the_explicit_audited_action(self):
        self.runner.invoke(management, [
            "register", "--tenant-code", "default", "--admin-username", "admin"])
        rows = [dict(r) for r in self.svc._store.execute(  # type: ignore[attr-defined]
            "SELECT * FROM audit_events WHERE action='agent.make_tenant_shared'"
            " AND target='agent:alpha'")]
        self.assertEqual(len(rows), 1, (
            "the register migration shares the default as a deliberate, named act"
            " — no correction path may publish a private Agent silently"))


if __name__ == "__main__":
    unittest.main()
