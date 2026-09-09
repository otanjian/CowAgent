# encoding:utf-8
"""Route-level tests for the database identity backend handlers.

These exercise the handler wiring (auth context resolution, X-Tenant-ID gating,
tenant-admin/protected writes, audit query) without booting a live server. The
service is pointed at a temp identity.db and ``identity_mode`` is patched to
"database" so the handlers take the database path.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel, auth_handlers, admin_handlers
from auth.policy import BUILTIN_ROLES, PERMISSION_CATALOG
from auth.service import IdentityService


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class DatabaseAuthHandlerTests(unittest.TestCase):
    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme",
            allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/auth/check", "DbAuthCheckHandler",
                "/auth/logout", "DbAuthLogoutHandler",
                "/auth/password", "DbAuthPasswordHandler",
                "/auth/me", "DbAuthMeHandler",
                "/auth/context", "DbAuthContextHandler",
                "/api/platform/users", "PlatformUsersHandler",
                "/api/platform/users/([^/]+)/password", "PlatformUserPasswordHandler",
                "/api/platform/users/([^/]+)", "PlatformUsersHandler",
                "/api/platform/tenants", "PlatformTenantsHandler",
                "/api/platform/tenants/([^/]+)", "PlatformTenantHandler",
                "/api/tenant/members", "TenantMembersHandler",
                "/api/tenant/members/([^/]+)", "TenantMemberHandler",
                "/api/tenant/roles", "TenantRolesHandler",
                "/api/tenant/permissions", "TenantPermissionsHandler",
                "/api/tenant/departments", "TenantDepartmentsHandler",
                "/api/tenant/departments/([^/]+)", "TenantDepartmentHandler",
                "/api/identity/audit", "IdentityAuditHandler",
                "/api/identity/administered-tenants", "IdentityAdministeredTenantsHandler",
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

        with patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_is_database", lambda: True), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def test_login_sets_cookie_and_returns_tenants(self):
        resp = self._request(
            "/auth/login", method="POST",
            data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        # Web login only issues the session Cookie; no reusable token is returned.
        self.assertEqual(data["token"], "")
        self.assertGreaterEqual(len(data["tenants"]), 1)
        # cookie was set in the Set-Cookie header
        self.assertIn("cow_session", str(getattr(resp, "headers", {})))

    def test_login_bad_password(self):
        resp = self._request(
            "/auth/login", method="POST",
            data=json.dumps({"username": "root", "password": "wrong"}))
        data = self._json(resp)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "invalid_login")

    def test_platform_tenants_requires_login(self):
        resp = self._request("/api/platform/tenants", method="GET")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_platform_tenants_admin_ok(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/platform/tenants", method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["items"]), 1)

    def test_member_list_requires_tenant_selection(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        # no X-Tenant-ID -> 400
        resp = self._request("/api/tenant/members", method="GET", token=token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_member_list_with_tenant_ok(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/tenant/members", method="GET", token=token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")

    def test_permission_catalog_with_tenant_ok(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/tenant/permissions", token=token, tenant=self.tid)
        self.assertTrue(str(resp.status).startswith("200"))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        # permissions is now a list of {id, group, label, description, scope, assignable}
        self.assertEqual([p["id"] for p in data["permissions"]], list(PERMISSION_CATALOG))
        for p in data["permissions"]:
            self.assertEqual(
                set(p.keys()),
                {"id", "group", "label", "description", "scope", "assignable"},
            )
        self.assertEqual(data["builtin_roles"], BUILTIN_ROLES)

    def test_permission_catalog_requires_login(self):
        resp = self._request("/api/tenant/permissions", tenant=self.tid)
        self.assertTrue(str(resp.status).startswith("401"))
        self.assertEqual(self._json(resp)["code"], "unauthorized")

    def test_permission_catalog_requires_tenant_selection(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/tenant/permissions", token=token)
        self.assertTrue(str(resp.status).startswith("400"))
        self.assertEqual(self._json(resp)["code"], "missing_tenant")

    def test_audit_query_scoped(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/identity/audit", method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertTrue(len(data["items"]) >= 1)

    def test_self_context_whitelist_shape(self):
        # bootstrap admin (allow_weak=True) is immediately usable; full projection
        token = self.svc.login("root", "Str0ngAdminPass").token
        ctx2 = self.svc.self_context(token)
        self.assertEqual(ctx2["user"]["username"], "root")
        self.assertFalse(ctx2["must_change_password"])
        self.assertGreaterEqual(len(ctx2["tenants"]), 1)
        entry = ctx2["tenants"][0]
        self.assertEqual(set(entry.keys()), {"id", "code", "name", "membership"})
        membership = entry["membership"]
        self.assertIn("roles", membership)
        self.assertTrue(all(set(r.keys()) == {"code", "name"} for r in membership["roles"]))

    def test_self_context_route_requires_session(self):
        resp = self._request("/auth/me", method="GET")
        data = self._json(resp)
        self.assertEqual(data["code"], "unauthorized")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_self_context_route_returns_projection(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/auth/me", method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["username"], "root")
        self.assertIn("tenants", data)
        # response is no-store
        self.assertIn("no-store", str(getattr(resp, "headers", {})).lower())

    def test_self_context_does_not_depend_on_tenant_header(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/auth/me", method="GET", token=token, tenant="bad-tenant")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")

    def test_password_weak_returns_400(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request(
            "/auth/password", method="POST",
            data=json.dumps({"old_password": "Str0ngAdminPass", "new_password": "password"}),
            token=token)
        data = self._json(resp)
        self.assertEqual(data["code"], "weak_password")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_password_invalid_old_returns_401_invalid_old(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request(
            "/auth/password", method="POST",
            data=json.dumps({"old_password": "wrong", "new_password": "Str0ngNewPass"}),
            token=token)
        data = self._json(resp)
        self.assertEqual(data["code"], "invalid_old")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_password_success_clears_cookie_and_must_relogin(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request(
            "/auth/password", method="POST",
            data=json.dumps({"old_password": "Str0ngAdminPass", "new_password": "Str0ngNewPass"}),
            token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["must_relogin"])
        # cookie cleared via Set-Cookie with empty value
        headers = str(getattr(resp, "headers", {}))
        self.assertIn("cow_session", headers)

    def test_password_cross_origin_rejected(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request(
            "/auth/password", method="POST",
            data=json.dumps({"old_password": "Str0ngAdminPass", "new_password": "Str0ngNewPass"}),
            token=token)
        # no-set validation on cross-origin: with no Origin header the write is
        # allowed (same-origin default). Verify no-store on the response.
        self.assertIn("no-store", str(getattr(resp, "headers", {})).lower())

    def test_restricted_tenant_access_blocked(self):
        # a genuinely restricted user (forced password change) must be blocked
        # from tenant-scoped admin reads with password_change_required.
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="other", name="Other",
            shared_root="/s/other", admin_username="root2", admin_display="Root2",
            admin_password="Str0ngAdminPass2", recent_password="Str0ngAdminPass")
        token = self.svc.login("root2", "Str0ngAdminPass2").token
        other_tid = next(t for t in self.svc.list_tenants() if t["code"] == "other")["id"]
        resp = self._request("/api/tenant/members", method="GET", token=token, tenant=other_tid)
        data = self._json(resp)
        self.assertEqual(data["code"], "password_change_required")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_department_update_via_handler(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        dept = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="eng", name="Eng", parent_id=None)
        payload = json.dumps({
            "name": "Engineering", "sort_order": 3,
            "expected_version": dept["version"]})
        resp = self._request(
            f"/api/tenant/departments/{dept['id']}", method="PUT",
            data=payload, token=token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["department"]["name"], "Engineering")
        self.assertEqual(data["department"]["sort_order"], 3)

    def test_department_update_cycle_rejected(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        eng = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="eng", name="Eng", parent_id=None)
        plat = self.svc.create_department(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            code="plat", name="Platform", parent_id=eng["id"])
        payload = json.dumps({"parent_id": plat["id"], "expected_version": eng["version"]})
        resp = self._request(
            f"/api/tenant/departments/{eng['id']}", method="PUT",
            data=payload, token=token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["code"], "cycle")

    # --- legacy handlers delegate to database handlers in database mode -----

    def _legacy_app(self):
        # The real web_channel routing resolves /auth/login -> AuthLoginHandler
        # (legacy), which MUST delegate to DbAuthLoginHandler in database mode.
        return web.application(
            ("/auth/login", "AuthLoginHandler",
             "/auth/check", "AuthCheckHandler",
             "/auth/logout", "AuthLogoutHandler"),
            vars(web_channel),
            autoreload=False,
        )

    def _legacy_request(self, path, method="GET", data="", token=None):
        app = self._legacy_app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if headers:
            kwargs["headers"] = headers

        def _fake_service():
            return self.svc

        with patch.object(web_channel, "_is_database_identity", lambda: True), \
                patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    def test_legacy_login_delegates_to_db_handler(self):
        resp = self._legacy_request(
            "/auth/login", method="POST",
            data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["token"], "")
        # database-mode cookie is cow_session, not the legacy cow_auth_token
        self.assertIn("cow_session", str(getattr(resp, "headers", {})))
        self.assertNotIn("cow_auth_token", str(getattr(resp, "headers", {})))

    def test_legacy_check_delegates_to_db_handler(self):
        resp = self._legacy_request("/auth/check", method="GET")
        data = self._json(resp)
        # not logged in -> database handler reports identity_mode=database
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["identity_mode"], "database")
        self.assertFalse(data["authenticated"])

    def test_self_context_rejects_legacy_mode(self):
        # In legacy identity mode, /auth/me must explicitly reject the database
        # capability rather than fabricating a database account.
        from channel.web.auth_handlers import DbAuthMeHandler
        app = web.application(("/auth/me", "DbAuthMeHandler"), vars(web_channel), autoreload=False)
        with patch.object(auth_handlers, "_is_database", lambda: False):
            resp = app.request("/auth/me", method="GET")
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["code"], "not_database")

    # --- /auth/context (task 5.2) -----------------------------------------
    def test_context_requires_tenant_selection(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/auth/context", method="GET", token=token)
        self.assertEqual(self._json(resp)["code"], "missing_tenant")
        self.assertTrue(str(resp.status).startswith("400"))

    def test_context_requires_login(self):
        resp = self._request("/auth/context", method="GET", tenant=self.tid)
        self.assertEqual(self._json(resp)["code"], "unauthorized")
        self.assertTrue(str(resp.status).startswith("401"))

    def test_context_returns_effective_permissions_without_tenant_info_read(self):
        # A plain member (no tenant.info.read granted) can still read context.
        root = self.svc.list_platform_users()[0]
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="ctxuser", display_name="Ctx", temporary_password="Str0ngPassTmp",
            roles=["member"])
        # complete the forced password change so the account is unrestricted
        member_token = self.svc.login("ctxuser", "Str0ngPassTmp").token
        self.svc.change_password(member_token, "Str0ngPassTmp", "Str0ngPassNew!")
        member_token = self.svc.login("ctxuser", "Str0ngPassNew!").token
        resp = self._request("/auth/context", method="GET", token=member_token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertIn("effective_permissions", data)
        # member default excludes tenant.members.read / tenant.org.read
        self.assertNotIn("tenant.members.read", data["effective_permissions"])
        self.assertFalse(data["is_tenant_admin"])
        self.assertIn("consumers", data)
        self.assertIsInstance(data["consumers"], dict)

    def test_context_admin_has_nine_permissions(self):
        from auth.policy import default_permissions_for, TENANT_ADMIN_CODE
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/auth/context", method="GET", token=token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        # The built-in tenant_admin carries its explicit nine-id default set; the
        # catalogue does NOT auto-widen it when new resource ids are added.
        self.assertEqual(set(data["effective_permissions"]), set(default_permissions_for(TENANT_ADMIN_CODE)))
        self.assertTrue(data["is_tenant_admin"])

    def test_context_missing_membership_forbidden(self):
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="noctx", name="NoCtx",
            shared_root="/s/noctx", admin_username="nobody", admin_display="Nobody",
            admin_password="Str0ngPassTmp2", recent_password="Str0ngAdminPass")
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/auth/context", method="GET", token=token, tenant=other["id"])
        self.assertEqual(self._json(resp)["code"], "forbidden")
        self.assertTrue(str(resp.status).startswith("403"))

    # --- platform users list (task 4.1) ------------------------------------
    def test_platform_users_requires_login(self):
        resp = self._request("/api/platform/users", method="GET")
        self.assertTrue(str(resp.status).startswith("401"))

    def test_platform_users_platform_admin_ok(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/platform/users", method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        self.assertEqual(item["username"], "root")
        self.assertNotIn("password_hash", item)
        self.assertNotIn("temp_password_expires_at", item)

    def test_platform_users_member_rejected(self):
        root = self.svc.list_platform_users()[0]
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="plain", display_name="Plain", temporary_password="Str0ngPassTmp",
            roles=["member"])
        mt = self.svc.login("plain", "Str0ngPassTmp").token
        self.svc.change_password(mt, "Str0ngPassTmp", "Str0ngPassNew!")
        mt = self.svc.login("plain", "Str0ngPassNew!").token
        resp = self._request("/api/platform/users", method="GET", token=mt)
        self.assertEqual(self._json(resp)["code"], "forbidden")
        self.assertTrue(str(resp.status).startswith("403"))

    def test_platform_users_filter_and_paging(self):
        root = self.svc.list_platform_users()[0]
        for i in range(3):
            self.svc.create_member(
                actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
                username=f"user{i}", display_name=f"User {i}", temporary_password="Str0ngPassTmp",
                roles=["member"])
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/platform/users?status=active&page=1&page_size=2",
                             method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total"], 4)
        self.assertEqual(len(data["items"]), 2)

    def test_platform_users_bad_status_filter(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/platform/users?status=bogus", method="GET", token=token)
        self.assertEqual(self._json(resp)["code"], "bad_request")
        self.assertTrue(str(resp.status).startswith("4"))

    # --- platform user status/admin (task 4.2) ----------------------------
    def test_platform_user_patch_status(self):
        root = self.svc.list_platform_users()[0]
        # add a second completed platform admin so demoting root is legal
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="admin2", display_name="Admin2", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"])
        m = [x for x in self.svc.list_members(self.tid)["items"] if x["username"] == "admin2"][0]
        self.svc.set_platform_user_status(
            actor_user_id=root["id"], user_id=m["user_id"], active=True,
            is_platform_admin=True, expected_version=m["version"],
            recent_password="Str0ngAdminPass")
        res = self.svc.login("admin2", "Str0ngPassTmp")
        self.svc.change_password(res.token, "Str0ngPassTmp", "Str0ngAdminFinal")
        admin2 = [x for x in self.svc.list_platform_users() if x["username"] == "admin2"][0]

        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({
            "active": True, "is_platform_admin": False,
            "expected_version": root["version"], "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/users/{root['id']}", method="PATCH",
                             data=payload, token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["user"]["is_platform_admin"])

    def test_platform_user_patch_requires_recent_password(self):
        root = self.svc.list_platform_users()[0]
        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({
            "active": True, "is_platform_admin": False,
            "expected_version": root["version"], "recent_password": "Wrong"})
        resp = self._request(f"/api/platform/users/{root['id']}", method="PATCH",
                             data=payload, token=token)
        self.assertEqual(self._json(resp)["code"], "invalid_old")

    def test_platform_user_patch_last_admin_rejected(self):
        root = self.svc.list_platform_users()[0]
        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({
            "active": True, "is_platform_admin": False,
            "expected_version": root["version"], "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/users/{root['id']}", method="PATCH",
                             data=payload, token=token)
        self.assertEqual(self._json(resp)["code"], "last_admin")

    # --- platform user password reset (task 4.3) --------------------------
    def test_platform_user_reset_password(self):
        root = self.svc.list_platform_users()[0]
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="resetme", display_name="Reset", temporary_password="Str0ngPassTmp",
            roles=["member"])
        m = [x for x in self.svc.list_members(self.tid)["items"] if x["username"] == "resetme"][0]
        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({"expected_version": m["version"], "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/users/{m['user_id']}/password",
                             method="POST", data=payload, token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["temporary_password"])
        self.assertTrue(data["must_change_password"])

    def test_platform_user_reset_self_rejected(self):
        root = self.svc.list_platform_users()[0]
        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({"expected_version": root["version"], "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/users/{root['id']}/password",
                             method="POST", data=payload, token=token)
        self.assertEqual(self._json(resp)["code"], "self_reset_forbidden")

    # --- tenant rename (task 4.4) -----------------------------------------
    def test_tenant_rename_via_handler(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        v = self.svc.get_tenant(self.tid)["version"]
        payload = json.dumps({"name": "Acme Corp", "expected_version": v,
                              "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/tenants/{self.tid}", method="POST",
                             data=payload, token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tenant"]["name"], "Acme Corp")

    def test_tenant_rename_version_conflict(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        v = self.svc.get_tenant(self.tid)["version"]
        payload = json.dumps({"name": "Acme Corp", "expected_version": v + 9,
                              "recent_password": "Str0ngAdminPass"})
        resp = self._request(f"/api/platform/tenants/{self.tid}", method="POST",
                             data=payload, token=token)
        self.assertEqual(self._json(resp)["code"], "conflict")

    # --- tenant create derives controlled shared_root (task 4.4) ----------
    def test_tenant_create_derives_shared_root_when_omitted(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        # Patch a controlled deployment base (tmp, realpath'd like the real
        # resolver) so derivation does not depend on the ambient registry.
        deploy_base = os.path.realpath(tempfile.mkdtemp())
        payload = json.dumps({
            "code": "derived", "name": "Derived",
            "admin_username": "droot", "admin_display": "DRoot",
            "admin_password": "Str0ngPass9",
            "recent_password": "Str0ngAdminPass",
            # A malicious client-supplied server path must be ignored by the
            # handler (design §4: the web form never accepts a shared_root).
            "shared_root": "/etc",
        })
        with patch("auth.service._deployment_shared_base", lambda svc=None: deploy_base):
            resp = self._request("/api/platform/tenants", method="POST",
                                 data=payload, token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        # The created tenant must not carry the client-supplied path; it should be
        # derived under the deployment base (ends with /tenants/<code>) and never
        # exposed back to the client.
        derived = dict(self.svc.get_tenant(next(t["id"] for t in self.svc.list_tenants() if t["code"] == "derived")))
        self.assertTrue(derived["shared_root"].endswith(os.path.join("tenants", "derived")),
                        derived["shared_root"])
        self.assertNotIn("/etc", derived["shared_root"])
        self.assertNotIn("shared_root", data["tenant"])

    def test_tenant_create_missing_deployment_root_fails_cleanly(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        payload = json.dumps({
            "code": "noroot", "name": "NoRoot",
            "admin_username": "nroot", "admin_display": "NRoot",
            "admin_password": "Str0ngPass8",
        })
        # Unit-level: with no resolvable deployment base, create_tenant must raise
        # a config_error (503) rather than persisting an unusable root.
        with patch("auth.service._deployment_shared_base", lambda svc=None: None), \
             patch.object(self.svc, "_require_recent_password", lambda *a, **k: None):
            with self.assertRaises(Exception) as cm:
                self.svc.create_tenant(
                    actor_user_id=self.root["id"], code="noroot", name="NoRoot",
                    admin_username="nroot", admin_display="NRoot",
                    admin_password="Str0ngPass8", recent_password="Str0ngAdminPass")
            self.assertEqual(getattr(cm.exception, "code", None), "config_error")
            self.assertEqual(getattr(cm.exception, "status", None), 503)

    # --- member filters (task 4.5) ----------------------------------------
    def test_members_status_and_role_filters(self):
        root = self.svc.list_platform_users()[0]
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=["member"])
        self.svc.create_member(
            actor_user_id=root["id"], tenant_id=self.tid, operation="create-new",
            username="carol", display_name="Carol", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"])
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/tenant/members?role=member", method="GET",
                             token=token, tenant=self.tid)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        usernames = {m["username"] for m in data["items"]}
        self.assertIn("alice", usernames)
        self.assertNotIn("carol", usernames)

    def test_members_bad_status_filter(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/tenant/members?status=bogus", method="GET",
                             token=token, tenant=self.tid)
        self.assertEqual(self._json(resp)["code"], "bad_request")

    # --- administered-tenants (multi-tenant member assignment) ------------

    def _make_admin_in_beta(self):
        """Create beta + gamma tenants; make root a tenant_admin of beta too."""
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root="/s/beta", admin_username="root2", admin_display="Root2",
            admin_password="Str0ngAdminPass2", recent_password="Str0ngAdminPass")
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="gamma", name="Gamma",
            shared_root="/s/gamma", admin_username="root3", admin_display="Root3",
            admin_password="Str0ngAdminPass3", recent_password="Str0ngAdminPass")
        beta_tid = next(t for t in self.svc.list_tenants() if t["code"] == "beta")["id"]
        gamma_tid = next(t for t in self.svc.list_tenants() if t["code"] == "gamma")["id"]
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=beta_tid, user_id=self.root["id"],
            display_name="Root", recent_password="Str0ngAdminPass")
        return beta_tid, gamma_tid

    def test_administered_tenants_requires_login(self):
        resp = self._request("/api/identity/administered-tenants", method="GET")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_administered_tenants_lists_only_admin_tenants(self):
        self._make_admin_in_beta()
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/identity/administered-tenants", method="GET", token=token)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        codes = {t["code"] for t in data["items"]}
        self.assertIn("acme", codes)
        self.assertIn("beta", codes)
        self.assertNotIn("gamma", codes)

    def test_administered_tenants_reports_target_membership(self):
        beta_tid, _gamma_tid = self._make_admin_in_beta()
        member = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=["member"])
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request(
            "/api/identity/administered-tenants?user_id=" + member["user_id"],
            method="GET", token=token)
        data = self._json(resp)
        items = {t["code"]: t for t in data["items"]}
        self.assertEqual(items["acme"]["member"], True)
        self.assertEqual(items["acme"]["member_id"], member["membership_id"])
        self.assertEqual(items["beta"]["member"], False)
        self.assertIn("member_version", items["acme"])

    # --- tools/skills console read gating (platform admin vs member) ------

    @staticmethod
    def _platform_admin_ctx():
        from auth.runtime import RequestContext
        return RequestContext(
            user_id="usr_admin", username="root", display_name="Root",
            is_platform_admin=True, must_change_password=False,
            tenant_id="tnt_acme", membership={"id": "m_admin"},
            permissions={"agent.read"}, is_tenant_admin=True)

    @staticmethod
    def _member_ctx():
        from auth.runtime import RequestContext
        return RequestContext(
            user_id="usr_member", username="alice", display_name="Alice",
            is_platform_admin=False, must_change_password=False,
            tenant_id="tnt_acme", membership={"id": "m_member"},
            permissions={"agent.read"}, is_tenant_admin=False)

    def _request_tools_skills(self, path, ctx):
        """Drive ToolsHandler/SkillsHandler with a patched _db_scope context."""
        import contextlib

        @contextlib.contextmanager
        def _fake_db_scope():
            yield ctx

        with patch.object(web_channel, "_is_database_identity", lambda: True), \
                patch.object(web_channel, "_db_scope", _fake_db_scope), \
                patch.object(web_channel, "_require_auth", lambda: None):
            app = web_channel.build_web_app()
            return app.request(path, method="GET")

    def test_platform_admin_can_list_tools(self):
        resp = self._request_tools_skills("/api/tools", self._platform_admin_ctx())
        # A platform admin must not be 403-gated by a functional tool.read they
        # do not hold; the catalog is returned (even if empty of tools).
        self.assertEqual(resp.status, "200 OK", resp.data[:200])
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")

    def test_platform_admin_can_list_skills(self):
        resp = self._request_tools_skills("/api/skills", self._platform_admin_ctx())
        self.assertEqual(resp.status, "200 OK", resp.data[:200])
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")
        self.assertIn("skills", data)

    def test_member_without_tool_grant_is_rejected(self):
        resp = self._request_tools_skills("/api/tools", self._member_ctx())
        self.assertEqual(resp.status, "403 Forbidden", resp.data[:200])

    def test_member_without_skill_grant_is_rejected(self):
        resp = self._request_tools_skills("/api/skills", self._member_ctx())
        self.assertEqual(resp.status, "403 Forbidden", resp.data[:200])


if __name__ == "__main__":
    unittest.main()
