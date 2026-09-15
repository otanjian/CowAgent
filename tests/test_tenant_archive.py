# encoding:utf-8
"""Tests for tenant archive (soft delete) and restore.

Covers ``IdentityService.archive_tenant`` / ``restore_tenant`` / the tenant
list status filter, and the platform HTTP surface (``DELETE`` and
``operation='restore'``), per the ``add-tenant-archive`` change.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService, IdentityServiceError
from channel.web import web_channel, auth_handlers, admin_handlers


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _TenantArchiveFixture(unittest.TestCase):
    """Bootstraps an ``acme`` tenant with one platform admin (``root``)."""

    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.shared_root = os.path.join(os.path.realpath(tempfile.mkdtemp()), "acme")
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared_root, allow_weak=True)
        self.tid = [t for t in self.svc.list_tenants() if t["code"] == "acme"][0]["id"]
        self.root = self.svc.list_platform_users()[0]

    def _bare(self, code):
        """A tenant with no members: archivable without breaking continuity."""
        return self.svc.create_tenant(
            actor_user_id=self.root["id"], code=code, name=code.title(),
            shared_root="/s/" + code, recent_password="Str0ngAdminPass")

    def _with_admin(self, code, admin_username):
        """A tenant whose only member is its own admin (continuity-sensitive)."""
        return self.svc.create_tenant(
            actor_user_id=self.root["id"], code=code, name=code.title(),
            shared_root="/s/" + code, admin_username=admin_username,
            admin_display=admin_username, admin_password="Str0ngPassXyz",
            recent_password="Str0ngAdminPass")

    def _actions(self):
        return [r["action"] for r in self.svc._store.execute(
            "SELECT action FROM audit_events ORDER BY rowid")]

    def _archive(self, tenant_id, *, version=1, password="Str0ngAdminPass",
                 current_tenant_id=None):
        return self.svc.archive_tenant(
            actor_user_id=self.root["id"], tenant_id=tenant_id,
            expected_version=version,
            recent_password=password, current_tenant_id=current_tenant_id)


class TenantArchiveServiceTests(_TenantArchiveFixture):
    def test_archive_keeps_data_and_exits_service(self):
        beta = self._bare("beta")
        result = self._archive(beta["id"])

        self.assertTrue(result["archived"])
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNotNone(after["archived_at"])
        self.assertEqual(after["active"], 0)
        self.assertEqual(after["version"], 2)

        # Default list hides it; the archived filter surfaces it with a flag.
        self.assertNotIn("beta", [t["code"] for t in self.svc.list_tenants()])
        archived = self.svc.list_tenants(status="archived")
        self.assertEqual([t["code"] for t in archived], ["beta"])
        self.assertTrue(archived[0]["archived"])
        # Identity data is retained, not deleted.
        self.assertIsNotNone(self.svc.get_tenant(beta["id"]))
        self.assertEqual(self._actions().count("tenant.archive"), 1)

    def test_archive_removes_tenant_from_effective_tenants(self):
        beta = self._bare("beta")
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            user_id=self.root["id"], display_name="Root",
            recent_password="Str0ngAdminPass")
        self._archive(beta["id"])
        codes = [t["code"] for t in self.svc._active_tenants_for(self.root["id"])]
        self.assertNotIn("beta", codes)

    def test_archive_rejects_wrong_recent_password(self):
        beta = self._bare("beta")
        with self.assertRaises(IdentityServiceError) as e:
            self._archive(beta["id"], password="WrongPassword")
        self.assertEqual(e.exception.code, "invalid_old")
        self.assertEqual(self.svc.get_tenant(beta["id"])["active"], 1)

    def test_archive_default_tenant_protected(self):
        default = self._bare("default")
        with self.assertRaises(IdentityServiceError) as e:
            self._archive(default["id"])
        self.assertEqual(e.exception.code, "tenant_protected")
        self.assertEqual(e.exception.status, 403)
        self.assertIsNone(self.svc.get_tenant(default["id"])["archived_at"])

    def test_archive_current_tenant_protected(self):
        beta = self._bare("beta")
        with self.assertRaises(IdentityServiceError) as e:
            self._archive(beta["id"], current_tenant_id=beta["id"])
        self.assertEqual(e.exception.code, "tenant_protected")
        self.assertIsNone(self.svc.get_tenant(beta["id"])["archived_at"])

    def test_archive_version_conflict_changes_nothing(self):
        beta = self._bare("beta")
        with self.assertRaises(IdentityServiceError) as e:
            self._archive(beta["id"], version=99)
        self.assertEqual(e.exception.code, "conflict")
        self.assertEqual(e.exception.status, 409)
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNone(after["archived_at"])
        self.assertEqual(after["version"], 1)

    def test_archive_enforces_member_tenant_continuity(self):
        beta = self._with_admin("beta", "betaadmin")
        with self.assertRaises(IdentityServiceError) as e:
            self._archive(beta["id"])
        self.assertEqual(e.exception.code, "last_active_tenant_required")
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNone(after["archived_at"])
        self.assertEqual(after["active"], 1)
        self.assertEqual(self._actions().count("tenant.archive"), 0)

    def test_archive_rolls_back_when_audit_fails(self):
        beta = self._bare("beta")
        with patch.object(self.svc, "_audit_in_tx",
                          side_effect=RuntimeError("audit store down")):
            with self.assertRaises(RuntimeError):
                self._archive(beta["id"])
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNone(after["archived_at"])
        self.assertEqual(after["active"], 1)
        self.assertEqual(after["version"], 1)
        self.assertNotIn("tenant.archive", self._actions())

    def test_archived_tenant_cannot_be_edited_or_enabled(self):
        beta = self._bare("beta")
        self._archive(beta["id"])

        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_name(
                self.root["id"], beta["id"], "Beta2", 2, "Str0ngAdminPass")
        self.assertEqual(e.exception.code, "archived")
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_status(
                self.root["id"], beta["id"], True, 2, "Str0ngAdminPass")
        self.assertEqual(e.exception.code, "archived")
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_profile(
                actor_user_id=self.root["id"], tenant_id=beta["id"], name="Beta3",
                active=True, expected_version=2, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "archived")

        after = self.svc.get_tenant(beta["id"])
        self.assertEqual(after["name"], "Beta")
        self.assertEqual(after["active"], 0)
        self.assertEqual(after["version"], 2)

    def test_restore_archived_tenant_with_valid_admin(self):
        beta = self._bare("beta")
        # root already belongs to acme, so admin-ing beta keeps continuity when
        # beta is archived; root is then beta's valid tenant_admin for restore.
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            user_id=self.root["id"], display_name="Root",
            recent_password="Str0ngAdminPass")
        self._archive(beta["id"])

        result = self.svc.restore_tenant(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            expected_version=2, recent_password="Str0ngAdminPass")
        self.assertFalse(result["archived"])
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNone(after["archived_at"])
        self.assertEqual(after["active"], 1)
        self.assertEqual(after["version"], 3)
        self.assertIn("beta", [t["code"] for t in self.svc.list_tenants()])
        self.assertEqual(self._actions().count("tenant.restore"), 1)

    def test_restore_without_valid_admin_rejected(self):
        beta = self._bare("beta")
        self._archive(beta["id"])
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.restore_tenant(
                actor_user_id=self.root["id"], tenant_id=beta["id"],
                expected_version=2, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "no_admin")
        self.assertEqual(e.exception.status, 409)
        after = self.svc.get_tenant(beta["id"])
        self.assertIsNotNone(after["archived_at"])
        self.assertEqual(after["active"], 0)

    def test_restore_rejects_wrong_password_and_version(self):
        beta = self._bare("beta")
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            user_id=self.root["id"], display_name="Root",
            recent_password="Str0ngAdminPass")
        self._archive(beta["id"])
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.restore_tenant(
                actor_user_id=self.root["id"], tenant_id=beta["id"],
                expected_version=2, recent_password="WrongPassword")
        self.assertEqual(e.exception.code, "invalid_old")
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.restore_tenant(
                actor_user_id=self.root["id"], tenant_id=beta["id"],
                expected_version=99, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "conflict")

    def test_archived_code_stays_reserved(self):
        beta = self._bare("beta")
        self._archive(beta["id"])
        with self.assertRaises(IdentityServiceError) as e:
            self._bare("beta")
        self.assertEqual(e.exception.code, "conflict")

    def test_list_tenants_status_filter(self):
        beta = self._bare("beta")
        self._archive(beta["id"])
        gamma = self._bare("gamma")
        self.svc.set_tenant_status(
            self.root["id"], gamma["id"], False, 1, "Str0ngAdminPass")

        self.assertEqual(
            [t["code"] for t in self.svc.list_tenants(status="active")], ["acme"])
        self.assertEqual(
            [t["code"] for t in self.svc.list_tenants(status="inactive")], ["gamma"])
        self.assertEqual(
            [t["code"] for t in self.svc.list_tenants(status="archived")], ["beta"])
        self.assertEqual(
            sorted(t["code"] for t in self.svc.list_tenants(status="all")),
            ["acme", "beta", "gamma"])
        self.assertNotIn("beta", [t["code"] for t in self.svc.list_tenants()])


class TenantArchiveHttpTests(_TenantArchiveFixture):
    def _app(self):
        return web.application(
            (
                "/api/platform/tenants", "PlatformTenantsHandler",
                "/api/platform/tenants/([^/]+)", "PlatformTenantHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="GET", data="", token=None, tenant=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if tenant:
            headers["X-Tenant-ID"] = tenant
        if headers:
            kwargs["headers"] = headers

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _token(self):
        return self.svc.login("root", "Str0ngAdminPass").token

    def test_delete_archives_tenant(self):
        beta = self._bare("beta")
        resp = self._request(
            "/api/platform/tenants/" + beta["id"], method="DELETE",
            data=json.dumps({"expected_version": 1,
                             "recent_password": "Str0ngAdminPass"}),
            token=self._token())
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["tenant"]["archived"])
        self.assertEqual(self.svc.get_tenant(beta["id"])["active"], 0)

    def test_delete_rejects_wrong_password(self):
        beta = self._bare("beta")
        resp = self._request(
            "/api/platform/tenants/" + beta["id"], method="DELETE",
            data=json.dumps({"expected_version": 1,
                             "recent_password": "WrongPassword"}),
            token=self._token())
        data = self._json(resp)
        self.assertEqual(data["status"], "error")
        self.assertEqual(self.svc.get_tenant(beta["id"])["active"], 1)

    def test_delete_requires_login(self):
        beta = self._bare("beta")
        resp = self._request(
            "/api/platform/tenants/" + beta["id"], method="DELETE",
            data=json.dumps({"expected_version": 1,
                             "recent_password": "Str0ngAdminPass"}))
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_restore_operation(self):
        beta = self._bare("beta")
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            user_id=self.root["id"], display_name="Root",
            recent_password="Str0ngAdminPass")
        self._archive(beta["id"])
        resp = self._request(
            "/api/platform/tenants/" + beta["id"], method="POST",
            data=json.dumps({"operation": "restore", "expected_version": 2,
                             "recent_password": "Str0ngAdminPass"}),
            token=self._token())
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["tenant"]["archived"])
        self.assertEqual(self.svc.get_tenant(beta["id"])["active"], 1)

    def test_list_status_filter(self):
        beta = self._bare("beta")
        self._archive(beta["id"])
        token = self._token()
        resp = self._request("/api/platform/tenants?status=archived", token=token)
        codes = [t["code"] for t in self._json(resp)["items"]]
        self.assertEqual(codes, ["beta"])
        resp = self._request("/api/platform/tenants", token=token)
        self.assertNotIn("beta", [t["code"] for t in self._json(resp)["items"]])


if __name__ == "__main__":
    unittest.main()
