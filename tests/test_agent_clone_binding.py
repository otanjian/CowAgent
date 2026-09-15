# encoding:utf-8
"""Tests for clone provenance on agent bindings and the tenant default agent.

Change ``copy-default-tenant-agents`` copies the default tenant's agents into
another tenant as independent agents. To make a repeated copy idempotent without
relying on id/name conventions, each binding records which source agent it was
cloned from (``agent_bindings.cloned_from_agent_id``), and the orchestration
layer needs a narrow way to appoint a tenant's default agent.

These tests lock the storage contract and the service behaviour. They are
written before the implementation exists.
"""

import os
import sqlite3
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError
from auth.store import IdentityStore, migration_versions


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed_tenant(con, tenant_id="t1", code="acme"):
    con.execute(
        "INSERT INTO tenants(id, code, name, shared_root) VALUES(?,?,?,?)",
        (tenant_id, code, code.title(), "/s/" + code),
    )


def _seed_binding(con, agent_id, tenant_id="t1", cloned_from=None):
    con.execute(
        "INSERT INTO agent_bindings(agent_id, tenant_id, cloned_from_agent_id)"
        " VALUES(?,?,?)",
        (agent_id, tenant_id, cloned_from),
    )


class AgentBindingOriginColumnTests(unittest.TestCase):
    """Private-agent provenance: migration 17 adds ``agent_bindings.origin``.

    Change ``enable-member-personal-console`` has to tell a *system-provisioned*
    personal assistant apart from one a member created by hand, because only the
    system-made kind may make a later provisioning run skip. Legacy rows carry no
    such record, so the default is a deliberate ``'unknown'`` — never a guess
    that a row is system-made (and therefore safe to treat as replaceable).
    """

    def setUp(self):
        self.store = IdentityStore(_db_path())

    def _columns(self):
        with self.store.connect() as con:
            return {
                r["name"]: {"notnull": r["notnull"], "default": r["dflt_value"]}
                for r in con.execute("PRAGMA table_info(agent_bindings)")
            }

    def test_origin_is_not_null_and_defaults_to_unknown(self):
        cols = self._columns()
        self.assertIn("origin", cols)
        self.assertEqual(cols["origin"]["notnull"], 1,
                         "origin must be NOT NULL so an unlabelled row is impossible")
        self.assertEqual(cols["origin"]["default"], "'unknown'")

    def test_a_legacy_binding_reads_as_unknown_without_being_backfilled(self):
        """The upgrade must not invent provenance: an old row stays unknown."""
        with self.store.connect() as con:
            _seed_tenant(con)
            _seed_binding(con, "a1")  # no origin supplied
            row = con.execute(
                "SELECT origin FROM agent_bindings WHERE agent_id='a1'").fetchone()
        self.assertEqual(row["origin"], "unknown")

    def test_a_binding_may_record_a_known_origin(self):
        with self.store.connect() as con:
            _seed_tenant(con)
            con.execute(
                "INSERT INTO agent_bindings(agent_id, tenant_id, origin)"
                " VALUES('a1','t1','provisioned_assistant')")
            con.execute(
                "INSERT INTO agent_bindings(agent_id, tenant_id, origin)"
                " VALUES('a2','t1','user_created')")
            origins = {r["agent_id"]: r["origin"] for r in con.execute(
                "SELECT agent_id, origin FROM agent_bindings")}
        self.assertEqual(origins, {"a1": "provisioned_assistant",
                                   "a2": "user_created"})

    def test_upgrading_keeps_a_private_owners_binding_intact(self):
        """The compatibility red line: an existing private binding keeps its id,
        its owner and its clone provenance, and only gains 'unknown'."""
        from auth.store import _migrations

        path = _db_path()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(16):
            _migrations[i](con)
        _seed_tenant(con)
        con.execute(
            "INSERT INTO users(id, username, display_name, password_hash)"
            " VALUES('u1','root','Root','x')")
        con.execute(
            "INSERT INTO agent_bindings(agent_id, tenant_id,"
            " private_owner_user_id, cloned_from_agent_id)"
            " VALUES('a1','t1','u1','src')")
        con.execute("INSERT INTO schema_migrations(version) VALUES %s"
                    % ",".join("(%d)" % (i + 1) for i in range(16)))
        con.commit()
        con.close()

        store = IdentityStore(path)  # applies the origin migration
        with store.connect() as con:
            row = con.execute(
                "SELECT agent_id, tenant_id, private_owner_user_id,"
                " cloned_from_agent_id, origin FROM agent_bindings"
                " WHERE agent_id='a1'").fetchone()
        self.assertEqual(row["agent_id"], "a1")
        self.assertEqual(row["tenant_id"], "t1")
        self.assertEqual(row["private_owner_user_id"], "u1")
        self.assertEqual(row["cloned_from_agent_id"], "src")
        self.assertEqual(row["origin"], "unknown")


class AgentBindingCloneColumnTests(unittest.TestCase):
    """The provenance column and its uniqueness live in migration 8."""

    def setUp(self):
        self.store = IdentityStore(_db_path())

    def _columns(self):
        with self.store.connect() as con:
            return {
                r["name"]: {"notnull": r["notnull"], "default": r["dflt_value"]}
                for r in con.execute("PRAGMA table_info(agent_bindings)")
            }

    def test_migration_registers_a_new_version(self):
        with self.store.connect() as con:
            applied = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(applied, migration_versions())
        self.assertGreaterEqual(len(migration_versions()), 8)

    def test_provenance_column_is_optional(self):
        cols = self._columns()
        self.assertIn("cloned_from_agent_id", cols)
        self.assertEqual(
            cols["cloned_from_agent_id"]["notnull"], 0,
            "a plain bind has no source agent",
        )

    def test_duplicate_provenance_within_a_tenant_is_rejected(self):
        with self.store.connect() as con:
            _seed_tenant(con)
            _seed_binding(con, "s1-acme", cloned_from="s1")
            with self.assertRaises(sqlite3.IntegrityError):
                _seed_binding(con, "s1-acme-2", cloned_from="s1")

    def test_the_same_source_may_be_cloned_into_another_tenant(self):
        with self.store.connect() as con:
            _seed_tenant(con, "t1", "acme")
            _seed_tenant(con, "t2", "globex")
            _seed_binding(con, "s1-acme", "t1", cloned_from="s1")
            _seed_binding(con, "s1-globex", "t2", cloned_from="s1")
            n = con.execute("SELECT COUNT(*) FROM agent_bindings").fetchone()[0]
        self.assertEqual(n, 2)

    def test_plain_bindings_are_not_constrained_by_the_partial_index(self):
        """NULL provenance is excluded from the index, so many plain binds fit."""
        with self.store.connect() as con:
            _seed_tenant(con)
            for agent_id in ("a1", "a2", "a3"):
                _seed_binding(con, agent_id)
            n = con.execute("SELECT COUNT(*) FROM agent_bindings").fetchone()[0]
        self.assertEqual(n, 3)


class AgentBindingMigrationUpgradeTests(unittest.TestCase):
    """An existing store upgrades in place and keeps its bindings intact."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")

    def _pre_migration_store(self):
        from auth.store import _migrations
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(7):
            _migrations[i](con)
        _seed_tenant(con)
        # The pre-migration schema has no provenance column, so seed the legacy
        # row the way the old code did.
        con.execute(
            "INSERT INTO agent_bindings(agent_id, tenant_id) VALUES('legacy-agent','t1')")
        con.execute(
            "INSERT INTO schema_migrations(version) VALUES (1),(2),(3),(4),(5),(6),(7)")
        con.commit()
        con.close()

    def test_upgrade_keeps_existing_bindings_with_a_null_provenance(self):
        self._pre_migration_store()
        store = IdentityStore(self.path)
        with store.connect() as con:
            row = con.execute(
                "SELECT * FROM agent_bindings WHERE agent_id='legacy-agent'").fetchone()
            versions = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertIsNotNone(row)
        self.assertEqual(row["tenant_id"], "t1")
        self.assertIsNone(row["cloned_from_agent_id"])
        self.assertEqual(versions, migration_versions())

    def test_replay_is_idempotent(self):
        self._pre_migration_store()
        IdentityStore(self.path)
        store = IdentityStore(self.path)  # reopen must be a no-op
        with store.connect() as con:
            versions = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
            n = con.execute("SELECT COUNT(*) FROM agent_bindings").fetchone()[0]
        self.assertEqual(versions, migration_versions())
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(n, 1)


class _ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())
        self.default = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.root_id = self.svc.list_platform_users()[0]["id"]
        self.target = self.svc.create_tenant(
            actor_user_id=self.root_id, code="globex", name="Globex",
            shared_root="/s/globex", admin_username="globexadmin",
            admin_display="Globex", admin_password="Str0ngPass9",
            recent_password="Str0ngAdminPass")

    def _audit_actions(self):
        rows = self.svc.query_audit_platform(limit=200)  # type: ignore[attr-defined]
        return [r["action"] for r in rows]


class BindAgentCloneProvenanceTests(_ServiceFixture):
    def test_bind_records_the_source_agent(self):
        binding = self.svc.bind_agent(
            tenant_id=self.target["id"], agent_id="s1-globex",
            cloned_from_agent_id="s1")
        self.assertEqual(binding["cloned_from_agent_id"], "s1")
        self.assertEqual(
            self.svc.clone_of(self.target["id"], "s1")["agent_id"], "s1-globex")

    def test_clone_of_is_none_when_the_source_was_never_copied(self):
        self.assertIsNone(self.svc.clone_of(self.target["id"], "s1"))

    def test_a_second_clone_of_the_same_source_in_one_tenant_is_refused(self):
        self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1-globex",
                            cloned_from_agent_id="s1")
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1-again",
                                cloned_from_agent_id="s1")
        self.assertEqual(ctx.exception.status, 409)
        self.assertIsNone(self.svc.get_agent_binding("s1-again"))

    def test_plain_bind_without_a_source_still_works(self):
        binding = self.svc.bind_agent(
            tenant_id=self.target["id"], agent_id="standalone")
        self.assertIsNone(binding["cloned_from_agent_id"])

    def test_rebinding_the_same_agent_and_tenant_is_still_idempotent(self):
        first = self.svc.bind_agent(
            tenant_id=self.target["id"], agent_id="s1-globex",
            cloned_from_agent_id="s1")
        again = self.svc.bind_agent(
            tenant_id=self.target["id"], agent_id="s1-globex",
            cloned_from_agent_id="s1")
        self.assertEqual(first["agent_id"], again["agent_id"])
        self.assertEqual(
            len(self.svc.list_agent_bindings(self.target["id"])), 1)

    def test_cross_tenant_rebinding_is_still_rejected(self):
        self.svc.bind_agent(tenant_id=self.default["id"], agent_id="s1")
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1")
        self.assertEqual(ctx.exception.status, 409)

    def test_bind_audit_names_the_actor_when_one_is_given(self):
        self.svc.bind_agent(
            tenant_id=self.target["id"], agent_id="s1-globex",
            cloned_from_agent_id="s1", actor_user_id=self.root_id)
        rows = self.svc._store.execute(  # type: ignore[attr-defined]
            "SELECT * FROM audit_events WHERE action='agent.bind'"
            " AND target='agent:s1-globex'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor_user_id"], self.root_id)


class SetTenantDefaultAgentTests(_ServiceFixture):
    def _set_default(self, agent_id, tenant=None):
        return self.svc.set_tenant_default_agent(
            tenant_id=(tenant or self.target)["id"], agent_id=agent_id,
            actor_user_id=self.root_id)

    def test_appoints_a_bound_agent_as_the_default(self):
        self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1-globex")
        result = self._set_default("s1-globex")
        self.assertEqual(result["default_agent_id"], "s1-globex")
        self.assertEqual(
            self.svc.tenant_default_agent_id(self.target["id"]), "s1-globex")

    def test_records_an_audit_event(self):
        self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1-globex")
        self._set_default("s1-globex")
        rows = self.svc._store.execute(  # type: ignore[attr-defined]
            "SELECT * FROM audit_events WHERE action='tenant.set_default_agent'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_tenant_id"], self.target["id"])

    def test_refuses_an_agent_that_is_not_bound_to_the_tenant(self):
        with self.assertRaises(IdentityServiceError) as ctx:
            self._set_default("ghost")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIsNone(self.svc.tenant_default_agent_id(self.target["id"]))

    def test_refuses_an_unknown_tenant(self):
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.set_tenant_default_agent(
                tenant_id="nope", agent_id="a1", actor_user_id=self.root_id)
        self.assertEqual(ctx.exception.status, 404)

    def test_does_not_bump_the_tenant_version(self):
        """The tenant editor chains its batch on the version, so appointing the
        default agent must not invalidate a concurrently loaded draft."""
        self.svc.bind_agent(tenant_id=self.target["id"], agent_id="s1-globex")
        before = self.svc.get_tenant(self.target["id"])["version"]
        self._set_default("s1-globex")
        self.assertEqual(self.svc.get_tenant(self.target["id"])["version"], before)


if __name__ == "__main__":
    unittest.main()
