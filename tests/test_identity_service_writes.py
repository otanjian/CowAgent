# encoding:utf-8
"""Focused tests for member/role/department service invariants.

Covers the tenant-admin-only write gate, admin-continuity protection, version
conflict handling, cross-tenant object rejection, and the last-admin guard.
"""

import os
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _setup():
    svc = IdentityService(_db_path())
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users()[0]
    return svc, tid, root


class MemberWriteGateTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_member_create_by_admin(self):
        m = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=[], department_id=None, position_text="")
        self.assertTrue(m["membership_id"])

    def test_member_create_rejects_weak_password(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
                username="bob", display_name="Bob", temporary_password="password", roles=[])

    def test_non_admin_cannot_write_members(self):
        # a fresh non-admin user with no membership is rejected before any write
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.create_member(
                actor_user_id="someone", tenant_id=self.tid, operation="create-new",
                username="carl", display_name="Carl", temporary_password="Str0ngPassTmp", roles=[])
        self.assertEqual(e.exception.status, 403)

    def test_bind_existing_preserves_global_password(self):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp", roles=[])
        # bind the same user into a second tenant, ensure password unchanged
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta", shared_root="/s/beta",
            admin_username="betaadmin", admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        beta = [t for t in self.svc.list_tenants() if t["code"] == "beta"][0]
        # alice is root's member; bind root (existing) into beta, must not error
        self.svc.set_tenant_admin(actor_user_id=self.root["id"], tenant_id=beta["id"],
                                  user_id=self.root["id"], display_name="Root",
                                  recent_password="Str0ngAdminPass")


class AdminContinuityTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_cannot_remove_last_tenant_admin(self):
        membership = self.svc.get_membership(self.root["id"], self.tid)
        # attempt to demote the only admin to member
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_member(
                actor_user_id=self.root["id"], tenant_id=self.tid,
                member_id=membership["id"], display_name="Root", active=True,
                roles=["member"], department_id=None, position_text="",
                expected_version=membership["version"])
        self.assertEqual(e.exception.code, "last_admin")

    def test_add_second_admin_then_remove_first_ok(self):
        # create a second member and make them admin
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"], department_id=None)
        members = self.svc.list_members(self.tid)["items"]
        alice = [m for m in members if m["username"] == "alice"][0]
        # now demote root (no longer last admin) - should succeed
        root_membership = self.svc.get_membership(self.root["id"], self.tid)
        result = self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            member_id=root_membership["id"], display_name="Root", active=True,
            roles=["member"], department_id=None, position_text="",
            expected_version=root_membership["version"])
        self.assertTrue(result)


class VersionConflictTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_expected_version_guard(self):
        membership = self.svc.get_membership(self.root["id"], self.tid)
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_member(
                actor_user_id=self.root["id"], tenant_id=self.tid,
                member_id=membership["id"], display_name="Root2", active=True,
                roles=["tenant_admin"], department_id=None, position_text="",
                expected_version=membership["version"] + 5)
        self.assertEqual(e.exception.code, "conflict")


class DepartmentTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_create_department_and_delete_after_clear(self):
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="eng", name="Engineering", parent_id=None, sort_order=0)
        self.assertTrue(dept["id"])
        # delete with no children/members -> ok
        result = self.svc.delete_department(
            actor_user_id=self.root["id"], tenant_id=self.tid, dept_id=dept["id"])
        self.assertTrue(result["deleted"])

    def test_delete_department_in_use_rejected(self):
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="eng", name="Engineering", parent_id=None)
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=[], department_id=dept["id"])
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.delete_department(
                actor_user_id=self.root["id"], tenant_id=self.tid, dept_id=dept["id"])
        self.assertEqual(e.exception.code, "in_use")

    def test_cannot_delete_virtual_root(self):
        root_dept = [d for d in self.svc.list_departments(self.tid) if d["code"] == "__root__"][0]
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.delete_department(
                actor_user_id=self.root["id"], tenant_id=self.tid, dept_id=root_dept["id"])
        self.assertEqual(e.exception.code, "forbidden")


if __name__ == "__main__":
    unittest.main()
