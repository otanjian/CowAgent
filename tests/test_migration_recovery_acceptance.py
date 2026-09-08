# encoding:utf-8
"""Acceptance tests for migration recovery & legacy refusal (task 4.6).

Covers:
- ``has_migration_signature`` (a real migrated identity.db carries a marker).
- ``refuse_legacy_after_migration`` refuses to boot legacy over migrated data
  but allows database mode (and allows legacy over a non-migrated db).
- The app-level ``_guard_identity_mode_consistency`` aborts startup when legacy
  would read migrated data (patched config + real signature detection).
- Migration is idempotent/versioned: opening an already-migrated db twice does
  not duplicate the marker or corrupt rows.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.store import (
    has_migration_signature,
    refuse_legacy_after_migration,
)
from auth.service import IdentityService


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class MigrationSignatureTests(unittest.TestCase):
    def test_no_db_has_no_signature(self):
        self.assertFalse(has_migration_signature(os.path.join(tempfile.mkdtemp(), "missing.db")))

    def test_migrated_db_has_signature(self):
        path = _db()
        IdentityService(path)  # bootstraps? no; migration runs on store init
        self.assertTrue(has_migration_signature(path))

    def test_untouched_db_has_no_signature(self):
        path = _db()
        # create an empty sqlite file without any IAM migration
        import sqlite3
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE unrelated(x)")
        con.commit()
        con.close()
        self.assertFalse(has_migration_signature(path))


class RefuseLegacyTests(unittest.TestCase):
    def test_legacy_over_migrated_refused(self):
        path = _db()
        IdentityService(path)
        self.assertTrue(refuse_legacy_after_migration("legacy", path))

    def test_database_over_migrated_allowed(self):
        path = _db()
        IdentityService(path)
        self.assertFalse(refuse_legacy_after_migration("database", path))

    def test_legacy_over_fresh_allowed(self):
        self.assertFalse(refuse_legacy_after_migration("legacy", _db()))

    def test_missing_db_legacy_allowed(self):
        self.assertFalse(refuse_legacy_after_migration(
            "legacy", os.path.join(tempfile.mkdtemp(), "absent.db")))


class IdempotentMigrationTests(unittest.TestCase):
    def test_reopen_does_not_duplicate_marker(self):
        path = _db()
        IdentityService(path)
        IdentityService(path)  # open again; migration must be a no-op
        self.assertTrue(refuse_legacy_after_migration("legacy", path))
        import sqlite3
        con = sqlite3.connect(path)
        n = con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        con.close()
        # exactly the version set, no duplicates
        from auth.store import migration_versions
        self.assertEqual(n, len(migration_versions()))


class AppGuardTests(unittest.TestCase):
    def _guard(self, mode="legacy"):
        import app
        with patch("config.conf", return_value={"identity_mode": mode,
                                               "identity_db_path": ""}), \
             patch("config.get_data_root", return_value=tempfile.mkdtemp()):
            app._guard_identity_mode_consistency()

    def test_guard_allows_database_mode(self):
        # database mode never refuses regardless of db state
        import app
        with patch("config.conf", return_value={"identity_mode": "database",
                                               "identity_db_path": ""}), \
             patch("config.get_data_root", return_value=tempfile.mkdtemp()):
            app._guard_identity_mode_consistency()  # must not raise

    def test_guard_raises_when_legacy_reads_migrated_db(self):
        import app
        db = _db()
        IdentityService(db)  # migrate it
        with patch("config.conf", return_value={"identity_mode": "legacy",
                                               "identity_db_path": db}), \
             patch("config.get_data_root", return_value=tempfile.mkdtemp()):
            with self.assertRaises(RuntimeError):
                app._guard_identity_mode_consistency()


if __name__ == "__main__":
    unittest.main()
