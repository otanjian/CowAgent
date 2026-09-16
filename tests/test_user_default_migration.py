# encoding:utf-8
"""``_migration_25``: an independently versioned member default, and the repair
of default pointers that could never have resolved (change
``unify-console-by-data-scope``, tasks 2.3 / 4.4-4.6).

Two things are pinned here.

**The new columns.** ``memberships.default_agent_id`` used to be versioned by
``memberships.version``, which every display-name / department / position edit
also bumps — so "set my default" and "rename me" conflicted with each other for
no reason. The pointer gains its own ``default_agent_revision`` and an
``default_agent_origin`` (``'user'`` vs ``'provisioned'``), and an existing
preference is recorded as the member's own: the pre-upgrade code could not tell
the two origins apart, and treating a preference as the member's own is the only
reading that cannot silently overwrite it later.

**The repair.** A default pointer that cannot resolve is cleared, and the repair
*never touches ownership*: a private Agent that was wrongly appointed as its
tenant's default keeps ``private_owner_user_id``, so sharing it stays the
explicit, audited act ``make_agent_tenant_shared`` performs. Each repair appends
an audit event in the same migration transaction.

The fixture is a database stopped at version 24 with four kinds of pointer:
legal, unbound, foreign-tenant and private. The store is then opened normally, so
the real migration runner is what applies 25 (and 26).
"""

import os
import sqlite3
import tempfile
import unittest

from auth.store import IdentityStore, _migrations, migration_versions

BEFORE = 24  # migrations 1..24 exist; 25 and 26 are the ones under test.


def _build_legacy_store(path: str) -> None:
    """A database at version ``BEFORE`` holding one of every pointer shape."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "CREATE TABLE schema_migrations("
        " version INTEGER NOT NULL,"
        " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
    for index in range(BEFORE):
        _migrations[index](con)
    for user_id, username in (("u_root", "root"), ("u_alice", "alice"),
                              ("u_bob", "bob"), ("u_carol", "carol"),
                              ("u_dave", "dave"), ("u_erin", "erin")):
        con.execute(
            "INSERT INTO users(id, username, display_name, password_hash)"
            " VALUES(?,?,?,'x')", (user_id, username, username.title()))
    for tenant_id, code in (("t1", "acme"), ("t2", "globex"), ("t3", "initech")):
        con.execute(
            "INSERT INTO tenants(id, code, name, shared_root) VALUES(?,?,?,?)",
            (tenant_id, code, code.title(), "/s/" + code))
    # acme has a shared Agent, a private Agent owned by alice, and a private
    # Agent owned by bob. initech has a private Agent owned by alice.
    con.execute("INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                " VALUES('shared-assistant','t1',NULL)")
    con.execute("INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                " VALUES('alice-assistant','t1','u_alice')")
    con.execute("INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                " VALUES('bob-assistant','t1','u_bob')")
    con.execute("INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                " VALUES('initech-private','t3','u_alice')")
    for membership_id, user_id, default in (
            ("m_alice", "u_alice", "alice-assistant"),  # legal: her own private Agent
            ("m_bob", "u_bob", "ghost-assistant"),      # illegal: unbound
            ("m_carol", "u_carol", "shared-assistant"),  # legal: tenant-shared
    ):
        con.execute(
            "INSERT INTO memberships(id, tenant_id, user_id, display_name,"
            " default_agent_id) VALUES(?,?,?,?,?)",
            (membership_id, "t1", user_id, user_id, default))
    # A member with no registered preference at all.
    con.execute(
        "INSERT INTO memberships(id, tenant_id, user_id, display_name,"
        " default_agent_id) VALUES('m_dave','t1','u_dave','dave',NULL)")
    # A second membership naming *another member's* private Agent (illegal).
    con.execute(
        "INSERT INTO memberships(id, tenant_id, user_id, display_name,"
        " default_agent_id) VALUES('m_erin','t1','u_erin','erin','bob-assistant')")
    # Tenant defaults: legal (shared), foreign-tenant, and private.
    con.execute("UPDATE tenants SET default_agent_id='shared-assistant' WHERE id='t1'")
    con.execute("UPDATE tenants SET default_agent_id='alice-assistant' WHERE id='t2'")
    con.execute("UPDATE tenants SET default_agent_id='initech-private' WHERE id='t3'")
    con.execute("INSERT INTO schema_migrations(version) VALUES %s"
                % ",".join("(%d)" % (i + 1) for i in range(BEFORE)))
    con.commit()
    con.close()


class _Migrated(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "identity.db")
        _build_legacy_store(self.path)
        self.store = IdentityStore(self.path)

    def _row(self, sql, params=()):
        with self.store.connect() as con:
            return con.execute(sql, params).fetchone()

    def _rows(self, sql, params=()):
        with self.store.connect() as con:
            return con.execute(sql, params).fetchall()

    def _membership(self, membership_id):
        return self._row("SELECT * FROM memberships WHERE id=?", (membership_id,))

    def _audit(self, action):
        return self._rows("SELECT * FROM audit_events WHERE action=?", (action,))


class SchemaTests(_Migrated):
    def test_the_migration_chain_contains_both_new_versions_once(self):
        self.assertIn(25, migration_versions())
        self.assertIn(26, migration_versions())
        self.assertEqual(migration_versions().count(25), 1)
        self.assertEqual(migration_versions().count(26), 1)

    def test_the_revision_column_is_independent_and_not_null(self):
        with self.store.connect() as con:
            cols = {r["name"]: r for r in con.execute("PRAGMA table_info(memberships)")}
        self.assertIn("default_agent_revision", cols)
        self.assertEqual(cols["default_agent_revision"]["notnull"], 1)
        self.assertEqual(cols["default_agent_revision"]["dflt_value"], "1")

    def test_the_origin_column_is_nullable(self):
        with self.store.connect() as con:
            cols = {r["name"]: r for r in con.execute("PRAGMA table_info(memberships)")}
        self.assertIn("default_agent_origin", cols)
        self.assertEqual(cols["default_agent_origin"]["notnull"], 0)
        self.assertIsNone(cols["default_agent_origin"]["dflt_value"])

    def test_the_pointer_and_the_membership_version_are_left_where_they_were(self):
        """The default's version is its own; the membership's is the editor's."""
        row = self._membership("m_alice")
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["default_agent_revision"], 1)


class MemberDefaultRepairTests(_Migrated):
    def test_a_legal_default_is_preserved_and_marked_as_the_members_own(self):
        row = self._membership("m_alice")
        self.assertEqual(row["default_agent_id"], "alice-assistant")
        self.assertEqual(row["default_agent_origin"], "user")

    def test_a_shared_agent_default_is_preserved(self):
        row = self._membership("m_carol")
        self.assertEqual(row["default_agent_id"], "shared-assistant")
        self.assertEqual(row["default_agent_origin"], "user")

    def test_an_unbound_default_is_cleared(self):
        row = self._membership("m_bob")
        self.assertIsNone(row["default_agent_id"])
        self.assertIsNone(row["default_agent_origin"])

    def test_a_default_pointing_at_another_members_private_agent_is_cleared(self):
        row = self._membership("m_erin")
        self.assertIsNone(row["default_agent_id"])

    def test_a_members_no_preference_stays_empty(self):
        row = self._membership("m_dave")
        self.assertIsNone(row["default_agent_id"])
        self.assertIsNone(row["default_agent_origin"])

    def test_each_repair_is_audited_with_its_reason(self):
        events = self._audit("member.default_agent.repaired")
        by_membership = {e["target"]: e for e in events}
        self.assertEqual(set(by_membership),
                         {"membership:m_bob", "membership:m_erin"})
        self.assertIn("unbound", by_membership["membership:m_bob"]["redacted_changes"])
        self.assertIn("owned_by_another_member",
                      by_membership["membership:m_erin"]["redacted_changes"])
        self.assertIn("ghost-assistant",
                      by_membership["membership:m_bob"]["redacted_changes"])
        for event in events:
            self.assertEqual(event["tenant_id"], "t1")
            self.assertEqual(event["result"], "success")
            self.assertIsNone(event["actor_user_id"])

    def test_a_preserved_default_is_not_audited_as_a_repair(self):
        self.assertFalse([e for e in self._audit("member.default_agent.repaired")
                          if e["target"] == "membership:m_alice"])


class TenantDefaultRepairTests(_Migrated):
    def test_a_legal_tenant_default_is_left_alone(self):
        row = self._row("SELECT default_agent_id FROM tenants WHERE id='t1'")
        self.assertEqual(row["default_agent_id"], "shared-assistant")

    def test_a_foreign_tenant_pointer_is_cleared(self):
        row = self._row("SELECT default_agent_id FROM tenants WHERE id='t2'")
        self.assertIsNone(row["default_agent_id"])

    def test_a_private_tenant_default_is_cleared(self):
        row = self._row("SELECT default_agent_id FROM tenants WHERE id='t3'")
        self.assertIsNone(row["default_agent_id"])

    def test_the_repair_preserves_the_private_owner(self):
        """Sharing an Agent is an explicit act; a default must never perform it."""
        row = self._row("SELECT private_owner_user_id FROM agent_bindings"
                        " WHERE agent_id='initech-private'")
        self.assertEqual(row["private_owner_user_id"], "u_alice")

    def test_the_repair_does_not_orphan_the_owner_of_the_other_private_agents(self):
        for agent_id, owner in (("alice-assistant", "u_alice"),
                                ("bob-assistant", "u_bob")):
            row = self._row("SELECT private_owner_user_id FROM agent_bindings"
                            " WHERE agent_id=?", (agent_id,))
            self.assertEqual(row["private_owner_user_id"], owner, agent_id)

    def test_a_repair_does_not_delete_the_binding(self):
        row = self._row("SELECT COUNT(*) c FROM agent_bindings WHERE agent_id=?",
                        ("initech-private",))
        self.assertEqual(row["c"], 1)

    def test_each_tenant_repair_is_audited(self):
        events = self._audit("tenant.default_agent.repaired")
        by_tenant = {e["tenant_id"]: e for e in events}
        self.assertEqual(set(by_tenant), {"t2", "t3"})
        self.assertIn("foreign_tenant", by_tenant["t2"]["redacted_changes"])
        self.assertIn("private_agent", by_tenant["t3"]["redacted_changes"])
        self.assertIn("initech-private", by_tenant["t3"]["redacted_changes"])

    def test_a_legal_default_is_not_audited_as_a_repair(self):
        self.assertFalse([e for e in self._audit("tenant.default_agent.repaired")
                          if e["tenant_id"] == "t1"])


class ReplayTests(_Migrated):
    def test_reopening_the_store_neither_re_runs_nor_changes_anything(self):
        before = {
            "memberships": [dict(r) for r in self._rows(
                "SELECT id, default_agent_id, default_agent_origin,"
                " default_agent_revision, version FROM memberships ORDER BY id")],
            "tenants": [dict(r) for r in self._rows(
                "SELECT id, default_agent_id, version FROM tenants ORDER BY id")],
            "audit": len(self._rows("SELECT id FROM audit_events")),
        }
        IdentityStore(self.path)  # a second open must be a no-op
        after = {
            "memberships": [dict(r) for r in self._rows(
                "SELECT id, default_agent_id, default_agent_origin,"
                " default_agent_revision, version FROM memberships ORDER BY id")],
            "tenants": [dict(r) for r in self._rows(
                "SELECT id, default_agent_id, version FROM tenants ORDER BY id")],
            "audit": len(self._rows("SELECT id FROM audit_events")),
        }
        self.assertEqual(before, after)

    def test_every_version_is_recorded_exactly_once(self):
        versions = [r["version"] for r in self._rows(
            "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, migration_versions())


if __name__ == "__main__":
    unittest.main()
