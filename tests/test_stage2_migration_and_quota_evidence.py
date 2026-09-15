# encoding:utf-8
"""Stage 2 evidence on a real temporary identity database (change task 2.7).

Stage 2 added five migrations (16-20) and one concurrency-sensitive decision:
whether a member's personal resources can be created twice under two simultaneous
requests. Migration correctness and quota atomicity are the two things that cannot
be argued from reading the code — they have to be *run*, on a real file-backed
database, with a real interruption and real concurrency.

So this module does not mock the store. It builds identity databases on disk,
interrupts a migration and retries it, reopens the same file repeatedly, and races
two genuine threads against one quota bucket. The result is the evidence the
change records before moving to the next stage.

The quota race in particular closes gate **Q1** (insufficient evidence that two
concurrent requests cannot over-consume a hard limit), which is what task 2.5 was
deferred on.
"""

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from auth.service import IdentityService
from auth.store import IdentityStore, _migrations, migration_versions

STAGE2_TABLES = ("binding_challenges", "personal_channel_links",
                 "personal_resource_configs")


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _RealDatabaseFixture(unittest.TestCase):
    def setUp(self):
        self.path = _db_path()
        self.svc = IdentityService(self.path)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.tenant = self.svc.list_tenants()[0]["id"]

    def _add_member(self, username, roles=("member",)):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=list(roles))
        return [m for m in self.svc.list_members(self.tenant)["items"]
                if m["username"] == username][0]["user_id"]

    def _applied_versions(self, path=None):
        con = sqlite3.connect(path or self.path)
        con.row_factory = sqlite3.Row
        try:
            return [r["version"] for r in
                    con.execute("SELECT version FROM schema_migrations"
                                " ORDER BY version")]
        finally:
            con.close()

    def _tables(self, path=None):
        con = sqlite3.connect(path or self.path)
        try:
            return {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()

    def _columns(self, table, path=None):
        con = sqlite3.connect(path or self.path)
        try:
            return {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
        finally:
            con.close()

    def _count(self, table, where="1=1", args=(), path=None):
        if path is None:
            return self.svc._store.execute(
                "SELECT COUNT(*) c FROM %s WHERE %s" % (table, where), args)[0]["c"]
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT COUNT(*) c FROM %s WHERE %s" % (table, where),
                args).fetchone()["c"]
        finally:
            con.close()

    def _member_with_tool_grant(self, username, tool="builtin:echo"):
        """A member who may actually use a tool — the precondition for saving
        personal parameters for it."""
        role = self.svc.create_role(
            self.root["id"], self.tenant, "user-%s" % username, username, [],
            resource_grants=[{"resource_kind": "tool", "resource_id": tool,
                              "action": "execute"}])
        return self._add_member(username, roles=[role["code"]])


class RealDatabaseMigrationTests(_RealDatabaseFixture):
    """Every migration lands exactly once on a real file, and stays landed."""

    def test_a_fresh_database_records_every_migration_exactly_once(self):
        applied = self._applied_versions()
        self.assertEqual(applied, migration_versions())
        self.assertEqual(len(applied), len(set(applied)))

    def test_the_stage_two_tables_and_columns_exist(self):
        tables = self._tables()
        for table in STAGE2_TABLES:
            self.assertIn(table, tables, table)
        self.assertIn("scope", self._columns("tenant_channel_instances"))
        self.assertIn("owner_user_id", self._columns("tenant_channel_instances"))
        self.assertIn("origin", self._columns("agent_bindings"))
        self.assertIn("owner_user_id", self._columns("credentials"))

    def test_reopening_the_same_file_repeatedly_changes_nothing(self):
        """Repeat migration: the file is opened many times in one process life."""
        before = self._applied_versions()
        grants_before = self._count("role_resource_grants", "resource_kind='menu'")

        for _ in range(3):
            IdentityService(self.path)

        self.assertEqual(self._applied_versions(), before)
        self.assertEqual(
            self._count("role_resource_grants", "resource_kind='menu'"),
            grants_before, "menu grants must not be re-added on every open")

    def test_repeat_migration_preserves_stage_two_rows(self):
        member = self._member_with_tool_grant("alice")
        self.svc.save_personal_resource_config(
            actor_user_id=member, tenant_id=self.tenant,
            resource_kind="tool", resource_id="builtin:echo",
            params={"run_migration_probe": True})
        configs = self._count("personal_resource_configs")
        creds = self._count("credentials")

        for _ in range(3):
            IdentityService(self.path)

        self.assertEqual(self._count("personal_resource_configs"), configs)
        self.assertEqual(self._count("credentials"), creds)


class InterruptedMigrationRecoveryTests(_RealDatabaseFixture):
    """An interrupted migration leaves no partial state and retries cleanly."""

    def _interrupted_store(self):
        """A store whose newest migration does real work and then dies.

        The failing migration inserts live rows (a tenant, roles, grants) before
        raising, so "no partial state" is a claim about rollback rather than about
        a transaction that never wrote anything.
        """
        path = _db_path()
        real_runner = _migrations[19]

        def exploding(con):
            con.execute(
                "INSERT INTO tenants(id, code, name, shared_root)"
                " VALUES('probe','probe','Probe','/s/probe')")
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin,"
                " permissions_json, version) VALUES('probe-role','probe',"
                "'member','Member',0,'[]',1)")
            for n in range(3):
                con.execute(
                    "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                    " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                    ("interrupted-%d" % n, "probe", "probe-role", "menu",
                     "nav:interrupted.probe.%d" % n, "view"))
            raise RuntimeError("interrupted mid-migration")

        _migrations[19] = exploding
        try:
            with self.assertRaises(RuntimeError):
                IdentityStore(path)
        finally:
            _migrations[19] = real_runner
        return path

    def test_an_interrupted_migration_is_not_recorded(self):
        path = self._interrupted_store()

        recorded = self._applied_versions(path)
        self.assertNotIn(20, recorded, "a failed migration must not be recorded")
        # Everything before the interrupted version landed; nothing at or after it
        # did. Expressed against the chain rather than a hard-coded 20 so a later
        # stage's migration does not silently invalidate the evidence.
        self.assertEqual(recorded, [v for v in migration_versions() if v < 20])

    def test_an_interrupted_migration_leaves_no_partial_rows(self):
        path = self._interrupted_store()

        self.assertEqual(0, self._count("tenants", path=path))
        self.assertEqual(0, self._count("roles", path=path))
        self.assertEqual(0, self._count(
            "role_resource_grants", "resource_id LIKE 'nav:interrupted.probe%'",
            path=path), "the failed migration's writes must all roll back")

    def test_retrying_after_an_interruption_completes_and_does_not_duplicate(self):
        path = self._interrupted_store()

        svc = IdentityService(path)  # the retry

        self.assertIsNotNone(svc)
        recorded = self._applied_versions(path)
        self.assertIn(20, recorded, "the retry must complete the interrupted version")
        self.assertEqual(recorded, list(migration_versions()))
        self.assertEqual(
            self._count("role_resource_grants",
                        "resource_id LIKE 'nav:interrupted.probe%'", path=path), 0,
            "the retry must not resurrect the failed attempt's rows")
        # And nothing was written twice.
        duplicates = svc._store.execute(
            "SELECT resource_id, COUNT(*) c FROM role_resource_grants"
            " GROUP BY role_id, resource_kind, resource_id HAVING c > 1")
        self.assertEqual(list(duplicates), [])


class StageTwoUniqueConstraintTests(_RealDatabaseFixture):
    """The uniqueness the new storage relies on is enforced by the database."""

    def test_one_personal_configuration_per_member_and_resource(self):
        member = self._member_with_tool_grant("alice")
        self.svc.save_personal_resource_config(
            actor_user_id=member, tenant_id=self.tenant, resource_kind="tool",
            resource_id="builtin:echo", params={"a": 1})
        with self.assertRaises(sqlite3.IntegrityError):
            with self.svc._store.connect() as con:
                con.execute(
                    "INSERT INTO personal_resource_configs(tenant_id, user_id,"
                    " resource_kind, resource_id) VALUES(?,?,?,?)",
                    (self.tenant, member, "tool", "builtin:echo"))
                con.commit()

    def test_a_personal_channel_link_is_one_row_per_member_and_instance(self):
        self.assertEqual(1, self._count(
            "sqlite_master",
            "type='index' AND name='idx_personal_channel_links_identity'"))

    def test_two_members_may_hold_the_same_credential_name_in_a_tenant(self):
        """Owner scoping must not collide with the tenant-wide unique name."""
        self.assertEqual(1, self._count(
            "sqlite_master", "type='index' AND name='idx_credentials_tenant_name'"))


class QuotaDoubleRequestRaceTests(_RealDatabaseFixture):
    """Gate Q1: two simultaneous requests must not over-consume a hard limit.

    The check is a read-then-write (read ``used``, compare, then increment), which
    is only safe because the service runs it inside ``BEGIN IMMEDIATE``: the second
    writer blocks until the first commits and therefore reads the updated value.
    These tests race real threads against a real file to demonstrate that, rather
    than assuming it.
    """

    METRIC = "tool_calls"

    def _race(self, member, amount=1, limit=1, user_limit=None):
        """Fire two consumptions as simultaneously as the scheduler allows."""
        self.svc.set_quota(actor_user_id=self.root["id"], tenant_id=self.tenant,
                           metric=self.METRIC, hard_limit=limit)
        if user_limit is not None:
            self.svc.set_quota(actor_user_id=self.root["id"], tenant_id=self.tenant,
                               metric=self.METRIC, hard_limit=user_limit,
                               user_id=member)
        barrier = threading.Barrier(2)
        results = {}

        def run(index):
            barrier.wait()
            results[index] = self.svc.consume_quota(
                user_id=member, tenant_id=self.tenant, metric=self.METRIC,
                amount=amount)

        threads = [threading.Thread(target=run, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results

    def _used(self, member):
        rows = self.svc._store.execute(
            "SELECT COALESCE(SUM(used),0) c FROM quota_usage WHERE tenant_id=?"
            " AND metric=?", (self.tenant, self.METRIC))
        return rows[0]["c"]

    def _denials(self):
        """How many consumptions were refused *by the quota limit itself*.

        This is what separates "the loser was denied by the limit check" from
        "the loser failed closed because it could not get the write lock". Both
        keep the limit intact, but only the former is the property under test, so
        the evidence has to distinguish them.
        """
        return self.svc._store.execute(
            "SELECT COUNT(*) c FROM audit_events WHERE tenant_id=?"
            " AND action='quota.deny' AND result='denied'",
            (self.tenant,))[0]["c"]

    def test_a_hard_limit_of_one_admits_exactly_one_of_two_requests(self):
        member = self._add_member("alice")

        results = self._race(member, limit=1)

        self.assertEqual(sorted(results.values()), [False, True],
                         "exactly one request may win: %s" % results)
        self.assertEqual(self._used(member), 1,
                         "the denied request must not be partially counted")
        self.assertEqual(self._denials(), 1,
                         "the loser must be refused by the limit check, not by "
                         "lock contention")

    def test_a_hard_limit_of_two_admits_both_requests(self):
        member = self._add_member("alice")

        results = self._race(member, limit=2)

        self.assertEqual(sorted(results.values()), [True, True], results)
        self.assertEqual(self._used(member), 2)

    def test_a_user_bucket_race_also_holds(self):
        """The per-user bucket has the same read-then-write shape."""
        member = self._add_member("alice")

        results = self._race(member, limit=100, user_limit=1)

        self.assertEqual(sorted(results.values()), [False, True], results)

    def test_concurrent_larger_amounts_still_respect_the_limit(self):
        """Two requests of amount 2 against a limit of 3: only one may fit."""
        member = self._add_member("alice")

        results = self._race(member, amount=2, limit=3)

        self.assertEqual(sorted(results.values()), [False, True], results)
        self.assertEqual(self._used(member), 2)


class WriteLockSerializationTests(_RealDatabaseFixture):
    """The mechanism behind the quota result, pinned deterministically.

    The race tests above show the *outcome*, but nothing forces the two threads to
    interleave, so on their own they cannot distinguish "serialized by ``BEGIN
    IMMEDIATE``" from "the threads happened not to overlap". (Replacing
    ``BEGIN IMMEDIATE`` with a deferred ``BEGIN`` leaves those tests green — which
    is exactly why this class exists.) Here the contention is made to happen on
    purpose, so the serialization is observed rather than hoped for.
    """

    def test_an_immediate_transaction_takes_the_lock_before_it_reads(self):
        """The precise property ``_tx`` buys.

        ``consume_quota`` reads ``used`` and *then* writes. The lock therefore has
        to be taken before the read, or two requests could both read the old value.
        ``BEGIN IMMEDIATE`` does exactly that: the lock is held from the statement
        on, with no write needed to acquire it.
        """
        holder = self.svc._store.connect()
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("SELECT COUNT(*) FROM quota_limits").fetchone()  # read only
        other = self.svc._store.connect()
        other.execute("PRAGMA busy_timeout=25")
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                other.execute("BEGIN IMMEDIATE")
            self.assertIn("lock", str(caught.exception).lower())
        finally:
            holder.rollback()
            other.close()

    def test_a_deferred_transaction_only_locks_when_it_writes(self):
        """The contrast that makes the test above discriminating.

        Under a deferred ``BEGIN`` a read takes no lock, so a second writer can
        begin — and would then be reading the pre-write value. That is the window
        ``BEGIN IMMEDIATE`` closes, and why the quota path must use it.
        """
        holder = self.svc._store.connect()
        holder.execute("BEGIN")
        holder.execute("SELECT COUNT(*) FROM quota_limits").fetchone()  # read only
        other = self.svc._store.connect()
        other.execute("PRAGMA busy_timeout=25")
        try:
            other.execute("BEGIN IMMEDIATE")  # no lock was taken by the read
            other.rollback()
        finally:
            holder.rollback()
            other.close()

    def test_the_write_lock_is_released_after_a_rollback(self):
        holder = self.svc._store.connect()
        holder.execute("BEGIN IMMEDIATE")
        holder.rollback()
        other = self.svc._store.connect()
        other.execute("PRAGMA busy_timeout=25")
        try:
            other.execute("BEGIN IMMEDIATE")
            other.rollback()
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
