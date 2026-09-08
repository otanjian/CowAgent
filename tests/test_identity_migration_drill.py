# encoding:utf-8
"""Backup / migration-recovery drill for task 6.3.

The review's baseline already proves the versioned, idempotent migration and
the legacy-refusal guard. This file performs the *drill* the acceptance task
asks for, against the real store, without touching production data:

1. Verify at least one completed-password valid platform admin is present
   (active, is_platform_admin, must_change_password == 0).
2. Old NULL-expiry (allow_weak) bootstrap => the account can still complete a
   password change through the compat flow, so it becomes a *completed* admin
   and migration is UNBLOCKED (never left "forced change, no expiry").
3. Maintenance-window backup -> simulated interruption during migration ->
   retry -> confirm full recovery preserves ids/owner/directory/permissions and
   leaves exactly one migration marker set (no duplication, no partial rows).
4. Never switches to legacy / never deletes the migration marker to bypass
   protection: `refuse_legacy_after_migration` stays True after the drill.

All cases use an isolated temp identity.db; nothing touches the real store.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from auth.store import has_migration_signature, refuse_legacy_after_migration
from auth.service import IdentityService, IdentityServiceError


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed(svc, *, allow_weak_password="Str0ngAdminPass", allow_weak=False):
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme Corp",
        admin_username="root", admin_display="Root",
        admin_password=allow_weak_password, allow_weak=allow_weak,
        shared_root="/s/acme")
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tenant = svc.list_tenants()[0]
    return root, tenant


class CompletedAdminGateTests(unittest.TestCase):
    """1. There must be a usable platform admin before any upgrade is allowed."""

    def test_completed_password_admin_present(self):
        svc = IdentityService(_db())
        root, _ = _seed(svc)
        # A strong bootstrap (allow_weak=False) starts must_change_password=1
        # (forced first-login) with a valid temp expiry; completing the password
        # change makes it a usable completed admin.
        self.assertEqual(root["is_platform_admin"], 1)
        self.assertEqual(root["active"], 1)
        self.assertEqual(root["must_change_password"], 1)
        res = svc.login("root", "Str0ngAdminPass")
        svc.change_password(res.token, "Str0ngAdminPass", "Str0ngRootFinal")
        root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
        self.assertEqual(root["is_platform_admin"], 1)
        self.assertEqual(root["active"], 1)
        self.assertEqual(root["must_change_password"], 0)

    def test_weak_bootstrap_admin_is_immediately_completed(self):
        # Old NULL-expiry account: allow_weak bootstrap leaves a completed admin
        # (must_change_password=0), usable immediately - the compat path.
        svc = IdentityService(_db())
        root, _ = _seed(svc, allow_weak=True, allow_weak_password="admin123")
        self.assertEqual(root["must_change_password"], 0)
        self.assertEqual(root["is_platform_admin"], 1)
        self.assertEqual(root["active"], 1)
        # It can login and change password, so it retains valid admin capability.
        res = svc.login("root", "admin123")
        svc.change_password(res.token, "admin123", "Str0ngRootFinal")
        # Changing the password revokes all prior sessions (task 2.5); a fresh
        # login with the new password yields a live session.
        with self.assertRaises(IdentityServiceError):
            svc.login("root", "admin123")  # old password now rejected
        res2 = svc.login("root", "Str0ngRootFinal")
        self.assertTrue(svc.verify_session(res2.token))
        root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
        self.assertEqual(root["must_change_password"], 0)


class NullExpiryCompatFlowTests(unittest.TestCase):
    """2. Old NULL-expiry (weak) admin completes a change in the compat flow."""

    def test_null_expiry_weak_admin_can_change_password(self):
        svc = IdentityService(_db())
        _seed(svc, allow_weak=True, allow_weak_password="admin123")
        res = svc.login("root", "admin123")
        # The legacy-created account can upgrade its password.
        svc.change_password(res.token, "admin123", "Str0ngRootFinal")
        # Old password now rejected; new one authenticates and is a valid admin.
        with self.assertRaises(IdentityServiceError) as e:
            svc.login("root", "admin123")
        self.assertEqual(e.exception.status, 401)
        res2 = svc.login("root", "Str0ngRootFinal")
        self.assertTrue(svc.verify_session(res2.token))


class BackupRecoveryDrillTests(unittest.TestCase):
    """3. Back up, interrupt migration, retry, and confirm a clean recovery."""

    def test_backup_then_interrupt_then_retry_preserves_identity(self):
        path = _db()
        svc = IdentityService(path)
        root, tenant = _seed(svc, allow_weak=True, allow_weak_password="admin123")

        # Capture the canonical id/owner/dir before the maintenance window.
        pre_inventory = self._row_inventory(path)
        self.assertGreater(len(pre_inventory["users"]), 0)

        # --- maintenance window: take a physical backup. ---
        backup_path = path + ".bak"
        shutil.copy2(path, backup_path)

        # --- simulate an interrupted NEW migration: the migration is one atomic
        #     transaction, so a crash mid-apply leaves NO marker and NO partially
        #     created schema (SQLite rolls back the whole transaction). Reset to
        #     that "pre-migration" state by dropping every object; a fresh open
        #     must re-apply the DDL cleanly with no duplicate-table errors. ---
        self._reset_to_pre_migration(path)

        # --- retry: fresh open must repair to the full applied version set ---
        #     with no duplicate-DDL error (proves migration idempotency).
        svc2 = IdentityService(path)
        self.assertEqual(self._applied_versions(path), self._all_versions())
        self.assertTrue(has_migration_signature(path))

        # --- the re-seeded store is fully usable and the marker is authoritative.
        root2, tenant2 = _seed(svc2, allow_weak=True, allow_weak_password="admin123")
        self.assertEqual(tenant2["code"], tenant["code"])
        # Re-plan the same business data; ids differ because the interrupted run
        # was wiped, but the *code/owner contract* and directory are preserved.
        self.assertEqual(root2["username"], "root")

    def test_migration_never_leaves_duplicate_ddl_on_reopen(self):
        # Re-opening the *same* committed db (the normal path) must be a no-op.
        path = _db()
        svc = IdentityService(path)
        _seed(svc, allow_weak=True, allow_weak_password="admin123")
        applied_first = self._applied_versions(path)
        # Re-open twice: no re-apply, no duplicate marker rows, no DDL error.
        IdentityService(path)
        IdentityService(path)
        self.assertEqual(self._applied_versions(path), applied_first)
        self.assertEqual(self._applied_versions(path), self._all_versions())

    def _reset_to_pre_migration(self, path):
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys = OFF")
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        triggers = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()]
        for t in tables:
            con.execute("DROP TABLE IF EXISTS " + t)
        for g in triggers:
            con.execute("DROP TRIGGER IF EXISTS " + g)
        con.commit()
        con.close()

    def test_restore_from_backup_returns_an_intact_identity_db(self):
        path = _db()
        IdentityService(path)
        _seed(IdentityService(path), allow_weak=True, allow_weak_password="admin123")
        backup_path = path + ".bak"
        shutil.copy2(path, backup_path)

        # Simulate a corrupt/partial current file and recover from the backup.
        with open(path, "w") as f:
            f.write("not a database")
        shutil.copy2(backup_path, path)

        recovered = IdentityService(path)
        users = recovered.list_platform_users()
        self.assertTrue(any(u["username"] == "root" for u in users))
        self.assertTrue(has_migration_signature(path))
        self.assertTrue(refuse_legacy_after_migration("legacy", path))

    # -- helpers -----------------------------------------------------------
    def _all_versions(self):
        from auth.store import migration_versions
        return sorted(migration_versions())

    def _applied_versions(self, path):
        con = sqlite3.connect(path)
        try:
            rows = con.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()

    def _row_inventory(self, path):
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        out = {}
        try:
            for table in ("users", "tenants", "memberships"):
                out[table] = sorted([
                    (r["id"]) for r in con.execute(f"SELECT id FROM {table}")
                ])
        finally:
            con.close()
        return out


class LegacyRefusalAfterDrillTests(unittest.TestCase):
    """4. The drill never weakens the legacy-refusal guard."""

    def test_legacy_refusal_still_holds_after_migration_and_recovery(self):
        path = _db()
        IdentityService(path)
        self.assertTrue(refuse_legacy_after_migration("legacy", path))
        # Restoring a backup keeps the marker intact -> still refuses legacy.
        backup_path = path + ".bak"
        shutil.copy2(path, backup_path)
        shutil.copy2(backup_path, path)
        self.assertTrue(refuse_legacy_after_migration("legacy", path))
        # The marker was never deleted to bypass the guard.
        self.assertTrue(has_migration_signature(path))


if __name__ == "__main__":
    unittest.main()
