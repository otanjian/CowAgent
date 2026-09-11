# encoding:utf-8
"""Auto-init when identity.db has no platform admin (database-bootstrap)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class DatabaseBootstrapAutoInitTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.db = os.path.join(self.root, "identity.db")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _svc(self):
        from auth.service import IdentityService
        return IdentityService(self.db)

    def test_first_start_creates_admin_and_writes_password_file(self):
        from common import startup_hooks

        printed = []
        with patch("config.conf", return_value={
                 "identity_mode": "database",
                 "identity_db_path": self.db,
             }), \
             patch("config.get_data_root", return_value=self.root), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(a)):
            startup_hooks._database_bootstrap_auto_init()

        svc = self._svc()
        self.assertTrue(svc.has_any_platform_admin())
        pw_path = os.path.join(self.root, ".bootstrap_admin_password")
        self.assertTrue(os.path.isfile(pw_path))
        self.assertEqual(os.stat(pw_path).st_mode & 0o777, 0o600)
        with open(pw_path, encoding="utf-8") as fh:
            password = fh.read().strip()
        self.assertGreaterEqual(len(password), 16)
        # Password must appear on console print, not only in the file.
        self.assertTrue(any(password in "".join(map(str, args)) for args in printed))

        user = svc._find_user_by_username("admin")
        self.assertIsNotNone(user)
        self.assertEqual(user["must_change_password"], 1)

    def test_second_start_is_idempotent(self):
        from common import startup_hooks

        with patch("config.conf", return_value={
                 "identity_mode": "database",
                 "identity_db_path": self.db,
             }), \
             patch("config.get_data_root", return_value=self.root):
            startup_hooks._database_bootstrap_auto_init()
            with open(
                os.path.join(self.root, ".bootstrap_admin_password"),
                encoding="utf-8",
            ) as fh:
                first_pw = fh.read()
            # Remove the one-shot file so a re-run cannot be detected by it.
            os.remove(os.path.join(self.root, ".bootstrap_admin_password"))
            startup_hooks._database_bootstrap_auto_init()

        self.assertFalse(
            os.path.exists(os.path.join(self.root, ".bootstrap_admin_password")),
            "must not rewrite password on re-start",
        )
        svc = self._svc()
        admins = svc._store.execute(
            "SELECT id FROM users WHERE is_platform_admin=1 OR id IN ("
            " SELECT user_id FROM user_platform_roles upr"
            " JOIN platform_roles r ON r.id=upr.platform_role_id"
            " WHERE r.code='platform_admin')")
        self.assertEqual(len(admins), 1)

    def test_init_failure_refuses_boot(self):
        from common import startup_hooks

        # Make the data root unwritable for the identity db parent... use a
        # file where a directory is expected so IdentityStore cannot create.
        bad_db = os.path.join(self.root, "not_a_dir")
        with open(bad_db, "w", encoding="utf-8") as fh:
            fh.write("x")
        nested = os.path.join(bad_db, "identity.db")

        with patch("config.conf", return_value={
                 "identity_mode": "database",
                 "identity_db_path": nested,
             }), \
             patch("config.get_data_root", return_value=self.root):
            with self.assertRaises(RuntimeError):
                startup_hooks._database_bootstrap_auto_init()

    def test_legacy_mode_skips_auto_init_during_phase1(self):
        """Explicit legacy is refused by the consistency guard; auto-init skips."""
        from common import startup_hooks

        with patch("config.conf", return_value={
                 "identity_mode": "legacy",
                 "identity_db_path": self.db,
             }), \
             patch("config.get_data_root", return_value=self.root):
            startup_hooks._database_bootstrap_auto_init()

        self.assertFalse(os.path.exists(self.db))

    def test_missing_identity_mode_still_bootstraps(self):
        from common import startup_hooks

        with patch("config.conf", return_value={"identity_db_path": self.db}), \
             patch("config.get_data_root", return_value=self.root), \
             patch("builtins.print"):
            startup_hooks._database_bootstrap_auto_init()
        self.assertTrue(self._svc().has_any_platform_admin())


if __name__ == "__main__":
    unittest.main()
