# encoding:utf-8
"""Migration tests for the tenant-owned channel instance table.

Change ``tenant-owned-message-channels`` adds ``tenant_channel_instances`` so a
tenant administrator can own channel instances without touching ``team.json``.
These tests lock the schema contract *before* the migration exists: columns and
defaults, NOT NULL requirements, the ``(tenant_id, display_name)`` uniqueness
that encodes "same channel type may repeat, display name may not", the
optimistic-concurrency ``version`` default, and the audit fields.
"""

import os
import sqlite3
import tempfile
import unittest

from auth.store import IdentityStore, migration_versions

TABLE = "tenant_channel_instances"


def _seed_tenant(con, tenant_id="t1", code="acme"):
    con.execute(
        "INSERT INTO tenants(id, code, name, shared_root) VALUES(?,?,?,?)",
        (tenant_id, code, code.title(), "/s/" + code),
    )


def _insert_instance(con, **over):
    row = {
        "id": "ci1",
        "tenant_id": "t1",
        "channel_type": "feishu",
        "display_name": "Support Bot",
        "agent_id": "a1",
        "created_by": "u_admin",
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(
        "INSERT INTO %s(%s) VALUES(%s)" % (TABLE, cols, marks), tuple(row.values())
    )


class TenantChannelInstancesMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")
        self.store = IdentityStore(self.path)

    def _columns(self):
        with self.store.connect() as con:
            return {
                r["name"]: {"notnull": r["notnull"], "default": r["dflt_value"]}
                for r in con.execute("PRAGMA table_info(%s)" % TABLE)
            }

    def test_migration_registers_a_new_version(self):
        with self.store.connect() as con:
            applied = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(applied, migration_versions())
        self.assertGreater(len(migration_versions()), 6)

    def test_table_exists_with_expected_columns(self):
        cols = self._columns()
        self.assertEqual(
            set(cols),
            {
                "id", "tenant_id", "channel_type", "display_name", "agent_id",
                "active", "version", "created_by", "created_at", "updated_at",
            },
        )

    def test_ownership_and_identity_columns_are_not_null(self):
        cols = self._columns()
        for name in (
            "id", "tenant_id", "channel_type", "display_name",
            "created_by", "active", "version",
        ):
            self.assertEqual(cols[name]["notnull"], 1, "%s must be NOT NULL" % name)

    def test_defaults_for_optional_and_concurrency_columns(self):
        cols = self._columns()
        self.assertEqual(cols["active"]["default"], "1")
        self.assertEqual(cols["version"]["default"], "1")
        self.assertEqual(cols["agent_id"]["default"], "''")

    def test_audit_fields_are_populated_on_insert(self):
        with self.store.connect() as con:
            _seed_tenant(con)
            _insert_instance(con)
            row = con.execute(
                "SELECT created_by, created_at, updated_at, version, active"
                " FROM %s WHERE id='ci1'" % TABLE).fetchone()
        self.assertEqual(row["created_by"], "u_admin")
        self.assertIsNotNone(row["created_at"])
        self.assertIsNotNone(row["updated_at"])
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["active"], 1)

    def test_display_name_unique_within_tenant(self):
        with self.store.connect() as con:
            _seed_tenant(con)
            _insert_instance(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_instance(con, id="ci2")

    def test_same_display_name_allowed_across_tenants(self):
        with self.store.connect() as con:
            _seed_tenant(con, "t1", "acme")
            _seed_tenant(con, "t2", "globex")
            _insert_instance(con)
            _insert_instance(con, id="ci2", tenant_id="t2")
            n = con.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
        self.assertEqual(n, 2)

    def test_same_channel_type_may_repeat_within_a_tenant(self):
        """Two Feishu bots in one tenant are legitimate (display names differ)."""
        with self.store.connect() as con:
            _seed_tenant(con)
            _insert_instance(con)
            _insert_instance(con, id="ci2", display_name="Sales Bot")
            n = con.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
        self.assertEqual(n, 2)

    def test_disabled_instance_does_not_hold_its_display_name(self):
        """Uniqueness is scoped to enabled instances, so disabling frees the name."""
        with self.store.connect() as con:
            _seed_tenant(con)
            _insert_instance(con)
            con.execute("UPDATE %s SET active=0 WHERE id='ci1'" % TABLE)
            _insert_instance(con, id="ci2")
            n = con.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
        self.assertEqual(n, 2)

    def test_two_enabled_instances_cannot_share_a_display_name(self):
        with self.store.connect() as con:
            _seed_tenant(con)
            _insert_instance(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_instance(con, id="ci2", active=1)

    def test_required_columns_reject_missing_values(self):
        for missing in ("channel_type", "display_name", "created_by"):
            with self.subTest(missing=missing):
                root = tempfile.mkdtemp()
                store = IdentityStore(os.path.join(root, "identity.db"))
                with store.connect() as con:
                    _seed_tenant(con)
                    with self.assertRaises(sqlite3.IntegrityError):
                        _insert_instance(con, **{missing: None})

    def test_unknown_tenant_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            con.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_instance(con, tenant_id="nope")


class MigrationReplayAndNonInterferenceTests(unittest.TestCase):
    """Replaying is safe, adds no data, and leaves pre-existing stores alone."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")

    def _pre_migration_store(self):
        """Build a store at version 6 with one credential and one version row."""
        from auth.store import _migrations
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(6):
            _migrations[i](con)
        _seed_tenant(con)
        con.execute(
            "INSERT INTO users(id, username, display_name, password_hash)"
            " VALUES('u1','root','Root','x')")
        con.execute(
            "INSERT INTO credentials(id, tenant_id, name, resource_kind,"
            " resource_id, ciphertext, created_by) VALUES"
            " ('cred1','t1','legacy:app','','','cipher-old','u1')")
        con.execute(
            "INSERT INTO credential_versions(credential_id, version, ciphertext,"
            " action, changed_by) VALUES('cred1',1,'cipher-old','create','u1')")
        con.execute(
            "INSERT INTO schema_migrations(version) VALUES (1),(2),(3),(4),(5),(6)")
        con.commit()
        con.close()

    def test_replay_does_not_duplicate_ddl_or_versions(self):
        self._pre_migration_store()
        IdentityStore(self.path)
        store = IdentityStore(self.path)  # reopen must be a no-op
        with store.connect() as con:
            versions = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
            n_instances = con.execute(
                "SELECT COUNT(*) FROM tenant_channel_instances").fetchone()[0]
        self.assertEqual(versions, migration_versions())
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(n_instances, 0, "migration must not backfill instances")

    def test_migration_preserves_existing_credential_state(self):
        self._pre_migration_store()
        store = IdentityStore(self.path)
        with store.connect() as con:
            cred = con.execute(
                "SELECT * FROM credentials WHERE id='cred1'").fetchone()
            vers = con.execute(
                "SELECT * FROM credential_versions WHERE credential_id='cred1'"
            ).fetchall()
        self.assertEqual(cred["ciphertext"], "cipher-old")
        self.assertEqual(cred["resource_kind"], "")
        self.assertEqual(cred["version"], 1)
        self.assertEqual(len(vers), 1)
        self.assertEqual(vers[0]["ciphertext"], "cipher-old")

    def test_migration_does_not_touch_team_json(self):
        team_json = os.path.join(self.root, "team.json")
        payload = '{"channel_instances":[{"id":"legacy","credentials":{"app_secret":"plain"}}]}'
        with open(team_json, "w", encoding="utf-8") as fh:
            fh.write(payload)
        IdentityStore(self.path)
        with open(team_json, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), payload)


class MigrationRollbackDrillTests(unittest.TestCase):
    """Rollback is additive-only; disabling an instance is the whole procedure.

    The change adds a table and never mutates pre-existing rows, so recovery
    must not require dropping anything or rewriting credential history. The
    end-to-end consequence (a disabled instance no longer starts) is covered by
    the service and startup slices; this drill locks the storage property.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")

    def _seed_live_instance(self):
        IdentityStore(self.path)
        store = IdentityStore(self.path)
        with store.connect() as con:
            _seed_tenant(con)
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash)"
                " VALUES('u1','root','Root','x')")
            _insert_instance(con, id="ci1")
            con.execute(
                "INSERT INTO credentials(id, tenant_id, name, resource_kind,"
                " resource_id, ciphertext, created_by) VALUES"
                " ('cred1','t1','channel:ci1','channel','ci1','cipher-v1','u1')")
            con.execute(
                "INSERT INTO credential_versions(credential_id, version, ciphertext,"
                " action, changed_by) VALUES('cred1',1,'cipher-v1','create','u1')")
        return store

    def test_disabling_the_instance_keeps_table_and_credential_history(self):
        store = self._seed_live_instance()
        with store.connect() as con:
            con.execute(
                "UPDATE tenant_channel_instances SET active=0 WHERE id='ci1'")
            inst = con.execute(
                "SELECT active, version FROM tenant_channel_instances"
                " WHERE id='ci1'").fetchone()
            cred = con.execute(
                "SELECT active, version, ciphertext FROM credentials"
                " WHERE id='cred1'").fetchone()
            vers = con.execute(
                "SELECT COUNT(*) FROM credential_versions"
                " WHERE credential_id='cred1'").fetchone()[0]
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(inst["active"], 0)
        self.assertEqual(cred["version"], 1)
        self.assertEqual(cred["ciphertext"], "cipher-v1")
        self.assertEqual(vers, 1)
        self.assertIn("tenant_channel_instances", tables)

    def test_rollback_never_rewrites_credential_rows(self):
        """Nothing in the recovery path mutates credential scope or ciphertext."""
        store = self._seed_live_instance()
        with store.connect() as con:
            before = con.execute(
                "SELECT tenant_id, name, resource_kind, resource_id, ciphertext"
                " FROM credentials WHERE id='cred1'").fetchone()
            con.execute(
                "UPDATE tenant_channel_instances SET active=0 WHERE id='ci1'")
            after = con.execute(
                "SELECT tenant_id, name, resource_kind, resource_id, ciphertext"
                " FROM credentials WHERE id='cred1'").fetchone()
        self.assertEqual(dict(before), dict(after))


if __name__ == "__main__":
    unittest.main()
