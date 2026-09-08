# encoding:utf-8
"""Acceptance tests for department/org + password semantics (task 4.4).

Covers: department cycle rejection on move, same-tenant parent validation,
single nullable department assignment, position_text not conferring business
authorization, department deactivation guard, and temporary password handling.
"""

import os
import tempfile
import unittest

from auth.runtime import resolve_context
from auth.service import IdentityService, IdentityServiceError


def _svc():
    return IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))


def _setup():
    svc = _svc()
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tid = svc.list_tenants()[0]
    return svc, root, tid


class DepartmentCycleTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.root, self.tid = _setup()

    def test_move_creates_cycle_rejected(self):
        eng = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        plat = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="plat", name="Platform", parent_id=eng["id"])
        # try to move eng under plat -> cycle
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_department(
                actor_user_id=self.root["id"], tenant_id=self.tid["id"],
                dept_id=eng["id"], parent_id=plat["id"], expected_version=1)
        self.assertEqual(e.exception.code, "cycle")

    def test_move_self_as_parent_rejected(self):
        eng = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_department(
                actor_user_id=self.root["id"], tenant_id=self.tid["id"],
                dept_id=eng["id"], parent_id=eng["id"], expected_version=1)
        self.assertEqual(e.exception.code, "cycle")

    def test_valid_move_accepted(self):
        eng = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        org = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="org", name="Org", parent_id=None)
        self.svc.update_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            dept_id=eng["id"], parent_id=org["id"], sort_order=5, expected_version=1)
        dept = [d for d in self.svc.list_departments(self.tid["id"]) if d["id"] == eng["id"]][0]
        self.assertEqual(dept["parent_id"], org["id"])
        self.assertEqual(dept["sort_order"], 5)

    def test_move_rejects_cross_tenant_parent(self):
        # beta tenant's dept cannot be the parent of an acme dept
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root="/s/beta", admin_username="beta", admin_display="Beta",
            admin_password="Str0ngPass2", recent_password="Str0ngAdminPass")
        beta = [t for t in self.svc.list_tenants() if t["code"] == "beta"][0]
        beta_admin = [u for u in self.svc.list_platform_users() if u["username"] == "beta"][0]
        beta_dept = self.svc.create_department(
            actor_user_id=beta_admin["id"], tenant_id=beta["id"],
            code="beng", name="BEng", parent_id=None)
        acme_dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="aeng", name="AEng", parent_id=None)
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_department(
                actor_user_id=self.root["id"], tenant_id=self.tid["id"],
                dept_id=acme_dept["id"], parent_id=beta_dept["id"], expected_version=1)
        self.assertEqual(e.exception.code, "invalid_parent")


class DepartmentSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.root, self.tid = _setup()

    def test_department_nullable(self):
        m = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"], operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=["member"], department_id=None)
        self.assertIsNone(m.get("department_id"))
        # assign then clear to null
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        member = self.svc.get_membership(m["user_id"], self.tid["id"])
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            member_id=member["id"], display_name="Alice", active=True,
            roles=["member"], department_id=dept["id"], position_text="",
            expected_version=member["version"])
        member = self.svc.get_membership(m["user_id"], self.tid["id"])
        self.assertEqual(member["department_id"], dept["id"])
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            member_id=member["id"], display_name="Alice", active=True,
            roles=["member"], department_id=None, position_text="",
            expected_version=member["version"])
        member = self.svc.get_membership(m["user_id"], self.tid["id"])
        self.assertIsNone(member["department_id"])

    def test_position_text_does_not_confer_permission(self):
        m = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"], operation="create-new",
            username="bob", display_name="Bob", temporary_password="Str0ngPassTmp",
            roles=["member"], department_id=None, position_text="CEO")
        token = self.svc.login("bob", "Str0ngPassTmp").token
        ctx = resolve_context(self.svc, token, self.tid["id"])
        self.assertFalse(ctx.is_tenant_admin)
        # position "CEO" must NOT grant admin permissions
        self.assertNotIn("tenant.member.write", ctx.permissions)
        self.assertNotIn("tenant.org.write", ctx.permissions)

    def test_deactivate_department_with_members_rejected(self):
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"], operation="create-new",
            username="carol", display_name="Carol", temporary_password="Str0ngPassTmp",
            roles=["member"], department_id=dept["id"])
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_department(
                actor_user_id=self.root["id"], tenant_id=self.tid["id"],
                dept_id=dept["id"], active=False, expected_version=1)
        self.assertEqual(e.exception.code, "in_use")

    def test_deactivate_leaf_department_ok(self):
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            code="eng", name="Eng", parent_id=None)
        self.svc.update_department(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            dept_id=dept["id"], active=False, expected_version=1)
        d = [d for d in self.svc.list_departments(self.tid["id"]) if d["id"] == dept["id"]][0]
        self.assertFalse(d["active"])


class TempPasswordTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.root, self.tid = _setup()

    def test_temp_password_requires_change_and_revokes_on_change(self):
        m = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"], operation="create-new",
            username="dave", display_name="Dave", temporary_password="Str0ngPassTmp",
            roles=["member"])
        token = self.svc.login("dave", "Str0ngPassTmp").token
        self.assertTrue(self.svc.verify_session(token)["user"]["must_change_password"])
        # a restricted user is flagged for a forced change; the HTTP _db_scope
        # turns this flag into a 403 on any business endpoint.
        ctx = resolve_context(self.svc, token, self.tid["id"])
        self.assertTrue(ctx.must_change_password)
        # changing the password revokes the temporary session and clears the flag
        self.svc.change_password(token, "Str0ngPassTmp", "Str0ngNewPass")
        self.assertIsNone(self.svc.verify_session(token))
        new_token = self.svc.login("dave", "Str0ngNewPass").token
        self.assertFalse(self.svc.verify_session(new_token)["user"]["must_change_password"])


if __name__ == "__main__":
    unittest.main()
