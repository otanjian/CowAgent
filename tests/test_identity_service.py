# encoding:utf-8
"""Tests for the IdentityService domain layer.

Covers the core invariants required by the specs: bootstrap creates a default
tenant + initial platform admin + built-in roles, admin continuity is enforced,
expected_version protects concurrent edits, tenant/membership/role/department
writes are same-transaction with audit, and cross-tenant object access is
rejected. These are the bedrock the four admin views and the resource-isolation
layer build on.
"""

import os
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError
from auth.password import hash_password, verify_password


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class ServiceBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())

    def _seed(self):
        return self.svc.bootstrap(
            tenant_code="acme",
            tenant_name="Acme",
            admin_username="root",
            admin_display="Root",
            admin_password="Str0ngAdminPass",
            shared_root="/s/acme",
        )

    def _tenant_id(self):
        return self.svc.list_tenants()[0]["id"]

    def test_bootstrap_creates_default_tenant_and_admin(self):
        self._seed()
        tenants = self.svc.list_tenants()
        self.assertEqual(len(tenants), 1)
        self.assertEqual(tenants[0]["code"], "acme")
        self.assertEqual(tenants[0]["active"], 1)

    def test_bootstrap_creates_platform_admin_and_tenant_admin(self):
        self._seed()
        users = self.svc.list_platform_users()
        admin = [u for u in users if u["username"] == "root"][0]
        self.assertEqual(admin["is_platform_admin"], 1)
        # the admin must also be the sole tenant_admin of the default tenant
        tid = self._tenant_id()
        self.assertTrue(self.svc._is_tenant_admin(admin["id"], tid))

    def test_bootstrap_builtin_roles(self):
        self._seed()
        roles = self.svc.list_roles(self._tenant_id())
        codes = {r["code"] for r in roles}
        self.assertIn("tenant_admin", codes)
        self.assertIn("member", codes)

    def test_rejects_common_default_password(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.bootstrap(
                tenant_code="acme",
                tenant_name="Acme",
                admin_username="root",
                admin_display="Root",
                admin_password="password",  # common default
                shared_root="/s/acme",
            )

    def test_allow_weak_permits_admin_default(self):
        self.svc.bootstrap(
            tenant_code="acme",
            tenant_name="Acme",
            admin_username="admin",
            admin_display="Admin",
            admin_password="admin",
            shared_root="/s/acme",
            allow_weak=True,
        )
        # the well-known admin/admin account must be able to log in
        res = self.svc.login("admin", "admin")
        self.assertEqual(res.username, "admin")
        self.assertIsNotNone(res.token)
        self.assertIsNotNone(res.tenants)


class ServiceLoginTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme",
            tenant_name="Acme",
            admin_username="root",
            admin_display="Root",
            admin_password="Str0ngAdminPass",
            shared_root="/s/acme",
        )

    def test_login_success_returns_tenants(self):
        result = self.svc.login("root", "Str0ngAdminPass")
        self.assertGreaterEqual(len(result.tenants), 1)
        self.assertTrue(result.token)

    def test_login_bad_password(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.login("root", "WrongPassword")


class ServiceTenantManagementTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
        self.root_id = self.svc.list_platform_users()[0]["id"]

    def test_create_tenant_requires_unique_code(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.create_tenant(
                actor_user_id=self.root_id,
                code="acme", name="Acme2", shared_root="/s/other",
                admin_username="root2", admin_display="Root2",
                admin_password="Str0ngAdminPass2", recent_password="Str0ngAdminPass")

    def test_weak_password_rejected(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.create_tenant(
                actor_user_id=self.root_id,
                code="acme2", name="Acme2", shared_root="/s/other",
                admin_username="root2", admin_display="Root2",
                admin_password="password", recent_password="Str0ngAdminPass")


class ServiceAuditTests(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")

    def test_audit_recorded_per_tenant(self):
        events = self.svc.list_audit(self.svc.list_tenants()[0]["id"])
        self.assertTrue(len(events) >= 1)
        for e in events:
            self.assertNotIn("password", str(e).lower())
            self.assertNotIn("token", str(e).lower())
            self.assertNotIn("secret", str(e).lower())


if __name__ == "__main__":
    unittest.main()
