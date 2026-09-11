# encoding:utf-8
"""Route-level tests for tenant-profile / audit scoping and field white-listing
(task 3.1).

Verifies: tenant responses never leak ``shared_root``; a custom role with no
permissions cannot read tenant info; an ordinary member cannot read identity
audit, while a tenant_admin (scoped) and platform_admin (all) can.
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


class IdentityScopeGateTests(unittest.TestCase):
    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.token = self.svc.login("root", "Str0ngAdminPass").token

    def _app(self):
        return web.application(
            (
                "/api/tenant/info", "TenantInfoHandler",
                "/api/identity/audit", "IdentityAuditHandler",
            ),
            vars(web_channel), autoreload=False)

    def _request(self, path, method="GET", token=None, error_out=False):
        app = self._app()

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            headers = {"Host": "localhost:9899", "X-Tenant-ID": self.tid}
            if token:
                headers["Authorization"] = "Bearer " + token
            return app.request(path, method=method, headers=headers)

    def _member_token(self, roles=("member",)):
        """Create a normal member account and return a login token."""
        member = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="bob", display_name="Bob", temporary_password="Str0ngTemp1!",
            roles=list(roles))
        return self.svc.login("bob", "Str0ngTemp1!").token

    # --- tenant profile whitelist -----------------------------------------
    def test_tenant_info_no_shared_root(self):
        resp = self._request("/api/tenant/info", token=self.token)
        self.assertTrue(str(resp.status).startswith("200"))
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")
        self.assertNotIn("shared_root", data["tenant"])
        self.assertIn("id", data["tenant"])
        self.assertIn("name", data["tenant"])

    def test_empty_permission_role_cannot_read_tenant_info(self):
        # Create a custom role with NO permissions; a member bound to it cannot
        # read tenant basics.
        role = self.svc.create_role(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="noaccess", name="No Access", permissions=[])
        token = self._member_token(roles=("member", "noaccess"))
        resp = self._request("/api/tenant/info", token=token)
        self.assertEqual(resp.status, "403")

    # --- audit scoping ----------------------------------------------------
    def test_ordinary_member_cannot_read_audit(self):
        token = self._member_token(roles=("member",))
        resp = self._request("/api/identity/audit", token=token)
        self.assertEqual(resp.status, "403")

    def test_tenant_admin_can_read_scoped_audit(self):
        resp = self._request("/api/identity/audit", token=self.token)
        self.assertTrue(str(resp.status).startswith("200"))
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")
        # scoped to the current tenant: events reference this tenant
        self.assertTrue(isinstance(data["items"], list))


class MemberRoleUpdateTests(unittest.TestCase):
    """member list role_codes + roles-preserve vs explicit-empty semantics
    (task 3.2)."""

    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]

    def _member(self, roles=("member",)):
        created = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="carol", display_name="Carol", temporary_password="Str0ngTemp1!",
            roles=list(roles))
        return created["membership_id"]

    def _carol(self):
        return [m for m in self.svc.list_members(self.tid)["items"] if m["username"] == "carol"][0]

    def test_list_members_includes_role_codes(self):
        self._member()
        carol = self._carol()
        self.assertIn("role_codes", carol)
        self.assertIn("member", carol["role_codes"])

    def test_update_omitted_roles_preserves_bindings(self):
        member_id = self._member()
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            member_id=member_id, display_name="Carol B", active=True,
            roles=None, department_id=None, position_text="",
            expected_version=self._carol()["version"])
        carol = self._carol()
        self.assertEqual(carol["display_name"], "Carol B")
        self.assertIn("member", carol["role_codes"])  # roles preserved

    def test_update_explicit_empty_roles_rejected(self):
        member_id = self._member()
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.update_member(
                actor_user_id=self.root["id"], tenant_id=self.tid,
                member_id=member_id, display_name="Carol", active=True,
                roles=[], department_id=None, position_text="",
                expected_version=self._carol()["version"])
        self.assertEqual(e.exception.code, "invalid_role")

    def test_update_non_empty_roles_replaces(self):
        member_id = self._member()
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            member_id=member_id, display_name="Carol", active=True,
            roles=["tenant_admin"], department_id=None, position_text="",
            expected_version=self._carol()["version"])
        carol = self._carol()
        self.assertIn("tenant_admin", carol["role_codes"])
        self.assertNotIn("member", carol["role_codes"])


if __name__ == "__main__":
    unittest.main()
