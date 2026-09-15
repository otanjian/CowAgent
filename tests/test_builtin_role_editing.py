# encoding:utf-8
"""Built-in role defaults, stored-authority resolution and edit guards.

Covers the "use + create your own resources" member default, the full-catalogue
tenant_admin default, the persisted-set authority (including an explicit clear),
the tenant_admin self-lock guard, delete/code protection, version conflicts and
the versioned backfill migration.
"""

import json
import os
import sqlite3
import tempfile
import unittest

from auth import store as identity_store
from auth.policy import (
    MEMBER_CODE,
    MEMBER_DEFAULT_PERMISSIONS,
    TENANT_ADMIN_CODE,
    TENANT_ADMIN_DEFAULT_PERMISSIONS,
    default_permissions_for,
)
from auth.service import IdentityService, IdentityServiceError


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed(svc):
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme Corp",
        admin_username="root", admin_display="Root",
        admin_password="Str0ngAdminPass", shared_root="/s/acme", allow_weak=True)
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tenant = svc.list_tenants()[0]
    return root, tenant


def _role_by_code(svc, tenant_id, code):
    return next(r for r in svc.list_roles(tenant_id) if r["code"] == code)


class DefaultSetTests(unittest.TestCase):
    def test_member_default_is_use_and_create_tier(self):
        perms = set(default_permissions_for(MEMBER_CODE))
        for pid in ("chat.use", "agent.use", "agent.edit", "model.use",
                    "skill.read", "skill.use", "skill.edit",
                    "tool.read", "tool.execute", "tool.configure"):
            self.assertIn(pid, perms)
        for pid in ("tenant.members.read", "tenant.org.read", "knowledge.write"):
            self.assertNotIn(pid, perms)
        self.assertEqual(perms, set(MEMBER_DEFAULT_PERMISSIONS))

    def test_tenant_admin_default_is_a_superset(self):
        admin = set(default_permissions_for(TENANT_ADMIN_CODE))
        self.assertTrue(set(default_permissions_for(MEMBER_CODE)) <= admin)
        self.assertNotIn("knowledge.write", admin)
        self.assertIn("tenant.members.read", admin)
        self.assertEqual(admin, set(TENANT_ADMIN_DEFAULT_PERMISSIONS))


class BuiltinEffectivePermissionTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db())
        self.root, self.tenant = _seed(self.svc)
        self.tid = self.tenant["id"]
        self.member = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice",
            temporary_password="TmpPass123!", roles=[MEMBER_CODE])["user_id"]

    def test_member_effective_permissions_include_chat_use(self):
        perms = self.svc.permissions_for(self.member, self.tid)
        self.assertIn("chat.use", perms)
        self.assertIn("agent.use", perms)
        self.assertIn("model.use", perms)
        self.assertNotIn("tenant.members.read", perms)

    def test_editing_builtin_member_role_changes_effective_permissions(self):
        role = _role_by_code(self.svc, self.tid, MEMBER_CODE)
        self.svc.update_role(
            actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"],
            name=role["name"], permissions=["chat.use"],
            expected_version=role["version"])
        self.assertEqual(self.svc.permissions_for(self.member, self.tid), {"chat.use"})

    def test_cleared_builtin_role_is_not_refilled(self):
        role = _role_by_code(self.svc, self.tid, MEMBER_CODE)
        self.svc.update_role(
            actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"],
            name=role["name"], permissions=[],
            expected_version=role["version"])
        self.assertEqual(self.svc.permissions_for(self.member, self.tid), set())

    def test_tenant_admin_edit_is_applied(self):
        role = _role_by_code(self.svc, self.tid, TENANT_ADMIN_CODE)
        new_perms = sorted(set(TENANT_ADMIN_DEFAULT_PERMISSIONS) - {"knowledge.read"})
        self.svc.update_role(
            actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"],
            name=role["name"], permissions=new_perms,
            expected_version=role["version"])
        self.assertNotIn(
            "knowledge.read", self.svc.permissions_for(self.root["id"], self.tid))


class BuiltinGuardTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db())
        self.root, self.tenant = _seed(self.svc)
        self.tid = self.tenant["id"]

    def test_tenant_admin_must_keep_identity_read(self):
        role = _role_by_code(self.svc, self.tid, TENANT_ADMIN_CODE)
        perms = [p for p in TENANT_ADMIN_DEFAULT_PERMISSIONS if p != "tenant.members.read"]
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.update_role(
                actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"],
                name=role["name"], permissions=perms,
                expected_version=role["version"])
        self.assertEqual(ctx.exception.code, "builtin_minimum_permissions")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(
            set(_role_by_code(self.svc, self.tid, TENANT_ADMIN_CODE)["permissions"]),
            set(TENANT_ADMIN_DEFAULT_PERMISSIONS))

    def test_builtin_role_cannot_be_deleted(self):
        role = _role_by_code(self.svc, self.tid, MEMBER_CODE)
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.delete_role(
                actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"])
        self.assertEqual(ctx.exception.code, "forbidden")
        self.assertEqual(ctx.exception.status, 403)

    def test_builtin_code_cannot_be_reused(self):
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.create_role(
                actor_user_id=self.root["id"], tenant_id=self.tid,
                code=MEMBER_CODE, name="Copy", permissions=[])
        self.assertEqual(ctx.exception.code, "forbidden")

    def test_version_conflict_still_applies_to_builtin(self):
        role = _role_by_code(self.svc, self.tid, MEMBER_CODE)
        with self.assertRaises(IdentityServiceError) as ctx:
            self.svc.update_role(
                actor_user_id=self.root["id"], tenant_id=self.tid, role_id=role["id"],
                name=role["name"], permissions=["chat.use"],
                expected_version=role["version"] + 99)
        self.assertEqual(ctx.exception.code, "conflict")


class BackfillMigrationTests(unittest.TestCase):
    def test_migration_backfills_builtin_rows_only(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE roles (id TEXT, tenant_id TEXT, code TEXT, name TEXT,"
                " builtin INTEGER, permissions_json TEXT, version INTEGER)")
            con.execute("INSERT INTO roles VALUES ('r1','t1',?,?,1,?,3)",
                        (MEMBER_CODE, "成员", json.dumps(["tenant.info.read"])))
            con.execute("INSERT INTO roles VALUES ('r2','t1',?,?,1,?,1)",
                        (TENANT_ADMIN_CODE, "管理员", json.dumps([])))
            con.execute("INSERT INTO roles VALUES ('r3','t1','custom','自定义',0,?,5)",
                        (json.dumps(["agent.read"]),))
            con.execute("INSERT INTO roles VALUES ('r4','t2',?,?,1,?,1)",
                        (MEMBER_CODE, "成员", json.dumps([])))

            identity_store._migration_13(con)

            rows = {r[0]: r for r in con.execute(
                "SELECT id, permissions_json, version FROM roles")}
            self.assertEqual(json.loads(rows["r1"][1]),
                             sorted(default_permissions_for(MEMBER_CODE)))
            self.assertEqual(rows["r1"][2], 4)
            self.assertEqual(json.loads(rows["r2"][1]),
                             sorted(default_permissions_for(TENANT_ADMIN_CODE)))
            # A custom role is untouched, in both set and version.
            self.assertEqual(json.loads(rows["r3"][1]), ["agent.read"])
            self.assertEqual(rows["r3"][2], 5)
            # A second tenant's built-in row is backfilled too.
            self.assertEqual(json.loads(rows["r4"][1]),
                             sorted(default_permissions_for(MEMBER_CODE)))
        finally:
            con.close()


class RetiredPermissionMigrationTests(unittest.TestCase):
    """Migration 15 strips the retired ``knowledge.write`` id from every role."""

    def test_retired_id_is_stripped_and_other_permissions_survive(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE roles (id TEXT, tenant_id TEXT, code TEXT, name TEXT,"
                " builtin INTEGER, permissions_json TEXT, version INTEGER)")
            con.execute("INSERT INTO roles VALUES ('r1','t1',?,?,1,?,3)",
                        (MEMBER_CODE, "成员",
                         json.dumps(["agent.read", "knowledge.write"])))
            con.execute("INSERT INTO roles VALUES ('r2','t1','custom','自定义',0,?,5)",
                        (json.dumps(["knowledge.write", "knowledge.read"]),))
            con.execute("INSERT INTO roles VALUES ('r3','t1','plain','无权限',0,?,1)",
                        (json.dumps(["agent.read"]),))

            identity_store._migration_15(con)

            rows = {r[0]: r for r in con.execute(
                "SELECT id, permissions_json, version FROM roles")}
            self.assertEqual(json.loads(rows["r1"][1]), ["agent.read"])
            self.assertEqual(rows["r1"][2], 4)
            # A custom role holding the retired id is stripped too.
            self.assertEqual(json.loads(rows["r2"][1]), ["knowledge.read"])
            self.assertEqual(rows["r2"][2], 6)
            # A role without the id is untouched, in both set and version.
            self.assertEqual(json.loads(rows["r3"][1]), ["agent.read"])
            self.assertEqual(rows["r3"][2], 1)
        finally:
            con.close()

    def test_the_retired_id_is_absent_from_the_catalog(self):
        from auth.policy import PERMISSION_METADATA
        from auth.policy import PERMISSION_CATALOG

        self.assertNotIn("knowledge.write", PERMISSION_CATALOG)
        self.assertNotIn("knowledge.write", PERMISSION_METADATA)
        from auth.policy import PermissionError, normalize_permissions

        with self.assertRaises(PermissionError):
            normalize_permissions(["knowledge.write"])


if __name__ == "__main__":
    unittest.main()
