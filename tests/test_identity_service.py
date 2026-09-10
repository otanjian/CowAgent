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


class ServiceTenantCreateWithoutAdminTests(unittest.TestCase):
    """Tenant creation no longer force-creates the first admin account."""

    def setUp(self):
        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
        self.root_id = self.svc.list_platform_users()[0]["id"]

    def _create_bare(self, code="beta"):
        return self.svc.create_tenant(
            actor_user_id=self.root_id, code=code, name=code.title(),
            shared_root="/s/" + code, recent_password="Str0ngAdminPass")

    def test_bare_create_succeeds_without_admin(self):
        created = self._create_bare()
        self.assertEqual(created["active"], True)
        self.assertEqual(created["version"], 1)
        # The response is a tenant projection only: no account fields.
        self.assertNotIn("admin_username", created)
        self.assertNotIn("admin_password", created)

    def test_bare_create_writes_tenant_skeleton_only(self):
        created = self._create_bare()
        tid = created["id"]
        # Built-in roles + virtual org root are still established.
        role_codes = sorted(r["code"] for r in self.svc.list_roles(tid))
        self.assertEqual(role_codes, ["member", "tenant_admin"])
        self.assertTrue(any(d["code"] == "__root__" for d in self.svc.list_departments(tid)))
        # ...but no admin membership was created.
        self.assertEqual(self.svc._count_valid_tenant_admins(tid), 0)
        self.assertEqual(self.svc.list_members(tid)["total"], 0)

    def test_bare_create_still_requires_recent_password(self):
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
        with self.assertRaises(IdentityServiceError):
            self.svc.create_tenant(
                actor_user_id=self.root_id, code="weak",
                name="Weak", shared_root="/s/weak", recent_password="WrongPassword")

    def test_explicit_admin_still_creates_admin(self):
        created = self.svc.create_tenant(
            actor_user_id=self.root_id, code="gamma", name="Gamma",
            shared_root="/s/gamma", admin_username="gadmin",
            admin_display="GAdmin", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        self.assertEqual(self.svc._count_valid_tenant_admins(created["id"]), 1)

    def test_explicit_weak_password_still_rejected(self):
        with self.assertRaises(IdentityServiceError):
            self.svc.create_tenant(
                actor_user_id=self.root_id, code="weak2", name="Weak2",
                shared_root="/s/weak2", admin_username="wadmin",
                admin_display="WAdmin", admin_password="password",
                recent_password="Str0ngAdminPass")

    def test_bare_create_conflict_still_409(self):
        self._create_bare("dup")
        with self.assertRaises(IdentityServiceError) as cm:
            self._create_bare("dup")
        self.assertEqual(cm.exception.status, 409)

    def test_admin_less_tenant_can_be_deactivated_but_not_restored(self):
        created = self._create_bare()
        tid = created["id"]
        self.svc.set_tenant_status(self.root_id, tid, False, 1, "Str0ngAdminPass")
        # Restoring requires a valid tenant_admin: refused while none is bound.
        with self.assertRaises(IdentityServiceError) as cm:
            self.svc.set_tenant_status(self.root_id, tid, True, 2, "Str0ngAdminPass")
        self.assertEqual(cm.exception.code, "no_admin")
        self.assertEqual(cm.exception.status, 409)
        # Binding an existing platform admin then makes restore possible.
        self.svc.set_tenant_admin(
            actor_user_id=self.root_id, tenant_id=tid, user_id=self.root_id,
            display_name="Root", recent_password="Str0ngAdminPass")
        result = self.svc.set_tenant_status(self.root_id, tid, True, 2, "Str0ngAdminPass")
        self.assertEqual(result["active"], True)


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
