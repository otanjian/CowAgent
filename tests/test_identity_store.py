# encoding:utf-8
"""Tests for the identity.db schema and connection management.

Covers: the full table set, Foreign Keys and UNIQUE constraints that enforce
the tenant boundaries, the append-only migration version table, and the
optimistic-concurrency ``version`` column present on every mutable object.
"""

import os
import tempfile
import unittest

from auth.store import IdentityStore, IdentityStoreError, migration_versions


class IdentityStoreSchemaTests(unittest.TestCase):
    def _store(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")
        return IdentityStore(self.path)

    def test_connection_and_migrations(self):
        store = self._store()
        with store.connect() as con:
            cursor = con.execute("SELECT version FROM schema_migrations ORDER BY version")
            applied = [row[0] for row in cursor.fetchall()]
        self.assertEqual(applied, migration_versions())

    def test_required_tables_exist(self):
        store = self._store()
        tables = {
            row[0]
            for row in store.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (
            "users",
            "tenants",
            "memberships",
            "roles",
            "membership_roles",
            "departments",
            "auth_sessions",
            "agent_bindings",
            "audit_events",
            "schema_migrations",
        ):
            self.assertIn(table, tables)

    def test_memberships_tenant_user_unique(self):
        store = self._store()
        with store.connect() as con:
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash)"
                " VALUES('u1','alice','Alice','x')"
            )
            con.execute(
                "INSERT INTO tenants(id, code, name, shared_root)"
                " VALUES('t1','acme','Acme','/s/t1')"
            )
            con.execute(
                "INSERT INTO memberships(id, tenant_id, user_id, display_name)"
                " VALUES('m1','t1','u1','Alice')"
            )
            # duplicate (tenant,user) must be rejected
            with self.assertRaises(Exception):
                con.execute(
                    "INSERT INTO memberships(id, tenant_id, user_id, display_name)"
                    " VALUES('m2','t1','u1','Alice2')"
                )

    def test_agent_bindings_agent_unique(self):
        store = self._store()
        with store.connect() as con:
            con.execute(
                "INSERT INTO tenants(id, code, name, shared_root)"
                " VALUES('t1','acme','Acme','/s/t1')"
            )
            con.execute(
                "INSERT INTO agent_bindings(agent_id, tenant_id)"
                " VALUES('agent-1','t1')"
            )
            with self.assertRaises(Exception):
                con.execute(
                    "INSERT INTO agent_bindings(agent_id, tenant_id)"
                    " VALUES('agent-1','t1')"
                )

    def test_roles_have_version_and_builtin(self):
        store = self._store()
        with store.connect() as con:
            con.execute(
                "INSERT INTO tenants(id, code, name, shared_root)"
                " VALUES('t1','acme','Acme','/s/t1')"
            )
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json)"
                " VALUES('r1','t1','member','Member',1,'[]')"
            )
            row = con.execute(
                "SELECT version, builtin FROM roles WHERE id='r1'"
            ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 1)

    def test_foreign_key_enforced(self):
        store = self._store()
        with store.connect() as con:
            # membership referencing a non-existent user must fail
            with self.assertRaises(Exception):
                con.execute(
                    "INSERT INTO memberships(id, tenant_id, user_id, display_name)"
                    " VALUES('m1','t1','nope','x')"
                )


if __name__ == "__main__":
    unittest.main()
