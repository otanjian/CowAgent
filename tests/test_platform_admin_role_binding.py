# encoding:utf-8
"""Platform-admin-as-role regression tests (change platform-admin-role).

These lock the *new* source-of-truth: platform qualification is derived from a
platform-scoped built-in role binding, and ``users.is_platform_admin`` is only
a derived mirror. They must FAIL before the migration + service predicates are
switched over, and PASS after.
"""

import os
import sqlite3
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError
from auth.policy import PLATFORM_ADMIN_CODE
from auth.runtime import resolve_context, revalidate_context, RequestContext


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _setup(svc):
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tid = svc.list_tenants()[0]["id"]
    return root, tid


def _completed_root(svc, root):
    """Complete root's forced password change so it counts as a usable admin."""
    svc.change_password(
        svc.login("root", "Str0ngAdminPass").token,
        "Str0ngAdminPass", "Str0ngRootFinal")
    return [u for u in svc.list_platform_users() if u["username"] == "root"][0]


def _bindings(db_path, user_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT r.code FROM user_platform_roles upr"
            " JOIN platform_roles r ON r.id = upr.platform_role_id"
            " WHERE upr.user_id = ?", (user_id,)).fetchall()
        return {r["code"] for r in rows}
    finally:
        con.close()


def _platform_role_row(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM platform_roles WHERE code=?", (PLATFORM_ADMIN_CODE,)
        ).fetchone()
    finally:
        con.close()


class MigrationBindingTests(unittest.TestCase):
    """The version-6 migration creates, seeds and backfills the new tables."""

    def test_migration_creates_and_seeds_platform_admin_role(self):
        svc = IdentityService(_db_path())
        row = _platform_role_row(svc._store.db_path)
        self.assertIsNotNone(row)
        self.assertEqual(row["builtin"], 1)
        self.assertEqual(row["name"], "平台管理员")

    def test_bootstrap_binds_initial_admin(self):
        svc = IdentityService(_db_path())
        root, _ = _setup(svc)
        self.assertEqual(_bindings(svc._store.db_path, root["id"]), {"platform_admin"})
        self.assertEqual(root["is_platform_admin"], 1)

    def test_migration_backfills_existing_platform_admin(self):
        # Simulate a pre-migration store: create schema_migrations, apply
        # migrations 1..5, insert a platform-admin user (mirror=1), then open
        # the service so migration 6 runs and must backfill the binding.
        from auth.store import _migrations
        path = _db_path()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(5):
            _migrations[i](con)
        con.execute(
            "INSERT INTO users(id, username, display_name, password_hash,"
            " active, is_platform_admin, must_change_password, version)"
            " VALUES ('u_legacy', 'legacy', 'Legacy', 'x', 1, 1, 0, 1)")
        con.execute(
            "INSERT INTO schema_migrations(version) VALUES (1),(2),(3),(4),(5)")
        con.commit()
        con.close()

        svc = IdentityService(path)
        self.assertEqual(_bindings(path, "u_legacy"), {"platform_admin"})
        # mirror stays consistent after backfill
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT is_platform_admin FROM users WHERE id='u_legacy'").fetchone()
        con.close()
        self.assertEqual(row["is_platform_admin"], 1)


class BindingIsSourceOfTruthTests(unittest.TestCase):
    """Qualification derives from the binding, not the mirror column."""

    def test_qualification_false_when_binding_missing_but_mirror_set(self):
        svc = IdentityService(_db_path())
        root, _ = _setup(svc)
        # Break the invariant by hand: keep mirror=1 but drop the binding.
        con = sqlite3.connect(svc._store.db_path)
        con.execute("DELETE FROM user_platform_roles WHERE user_id=?", (root["id"],))
        con.commit()
        con.close()
        self.assertFalse(svc.is_platform_admin_user(root["id"]))
        self.assertFalse(svc.is_platform_admin(root["id"]))

    def test_set_platform_user_status_promote_demote_syncs_binding(self):
        svc = IdentityService(_db_path())
        root, tid = _setup(svc)
        root = _completed_root(svc, root)  # usable safety net for the demote
        svc.create_member(
            actor_user_id=root["id"], tenant_id=tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=[], department_id=None)
        alice = [m for m in svc.list_members(tid)["items"]
                 if m["username"] == "alice"][0]
        # promote
        svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=alice["user_id"], active=True,
            is_platform_admin=True, expected_version=alice["version"],
            recent_password="Str0ngRootFinal")
        self.assertEqual(_bindings(svc._store.db_path, alice["user_id"]), {"platform_admin"})
        # demote
        svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=alice["user_id"], active=True,
            is_platform_admin=False, expected_version=alice["version"] + 1,
            recent_password="Str0ngRootFinal")
        self.assertEqual(_bindings(svc._store.db_path, alice["user_id"]), set())


class RevocationTakesEffectTests(unittest.TestCase):
    """After a binding is removed, the next authorization derivation is 'role'."""

    def test_authorization_mode_role_after_revocation(self):
        svc = IdentityService(_db_path())
        root, tid = _setup(svc)
        root = _completed_root(svc, root)
        svc.create_member(
            actor_user_id=root["id"], tenant_id=tid, operation="create-new",
            username="bob", display_name="Bob", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"], department_id=None)
        bob = [m for m in svc.list_members(tid)["items"]
               if m["username"] == "bob"][0]
        svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=bob["user_id"], active=True,
            is_platform_admin=True, expected_version=bob["version"],
            recent_password="Str0ngRootFinal")
        self.assertEqual(svc.authorization_mode(bob["user_id"], tid), "all")
        svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=bob["user_id"], active=True,
            is_platform_admin=False, expected_version=bob["version"] + 1,
            recent_password="Str0ngRootFinal")
        self.assertEqual(svc.authorization_mode(bob["user_id"], tid), "role")

    def test_revalidate_context_rederives_platform_qualification(self):
        svc = IdentityService(_db_path())
        root, tid = _setup(svc)
        token = svc.login("root", "Str0ngAdminPass").token
        ctx = resolve_context(svc, token, None)
        self.assertTrue(ctx.is_platform_admin)
        # Add a second completed admin so root can be demoted.
        svc.create_member(
            actor_user_id=root["id"], tenant_id=tid, operation="create-new",
            username="carol", display_name="Carol", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"], department_id=None)
        carol = [m for m in svc.list_members(tid)["items"]
                 if m["username"] == "carol"][0]
        svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=carol["user_id"], active=True,
            is_platform_admin=True, expected_version=carol["version"],
            recent_password="Str0ngAdminPass")
        # Complete carol's forced change so she is a usable fallback.
        svc.change_password(
            svc.login("carol", "Str0ngPassTmp").token,
            "Str0ngPassTmp", "Str0ngCarolFinal")
        # Demote root (carol remains a completed admin).
        root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
        svc.set_platform_user_status(
            actor_user_id=carol["user_id"], user_id=root["id"], active=True,
            is_platform_admin=False, expected_version=root["version"],
            recent_password="Str0ngCarolFinal")
        # A stale context still claims platform admin; revalidation must not.
        stale = RequestContext(
            user_id=root["id"], username="root", display_name="Root",
            is_platform_admin=True, must_change_password=False, tenant_id=None,
            membership=None, permissions=set(), is_tenant_admin=False)
        refreshed = revalidate_context(svc, stale)
        self.assertFalse(refreshed.is_platform_admin)


if __name__ == "__main__":
    unittest.main()
