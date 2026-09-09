# encoding:utf-8
"""Tests for the shared HTTP-method policy processor (task 2.1).

Verifies that the real ``build_web_app()`` app factory and the test harness share
the same gate: closed/deferred consumers return 503 in database mode, unknown
URLs stay 404, a matched route with an unregistered method is rejected (405),
and legacy mode is not gated by the database closure.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import web

import config
from auth.service import IdentityService
from channel.web import web_channel


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class HttpPolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme",
            allow_weak=True)

    def _app(self):
        return web_channel.build_web_app()

    def _request(self, path, method="GET", data="", headers=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        h = {"Host": "test"}
        if headers:
            h.update(headers)
        kwargs["headers"] = h
        return app.request(path, **kwargs)

    def _patch_db(self):
        settings = {"identity_mode": "database", "identity_db_path": self.db}
        patchers = [
            patch.object(config, "conf", return_value=settings),
            patch.object(web_channel, "conf", return_value=settings),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_unknown_url_returns_404(self):
        resp = self._request("/does/not/exist", method="GET")
        self.assertTrue(str(resp.status).startswith("404"))

    def test_unregistered_method_rejected(self):
        # /auth/login is only registered for POST; a GET must be rejected (405).
        resp = self._request("/auth/login", method="GET")
        self.assertEqual(resp.status, "405 Method Not Allowed")

    def test_closed_consumer_503_in_database(self):
        # Consumers still without a tenant boundary (scheduler is opened only
        # with its own slice, group 5) stay 503 database_unavailable.
        self._patch_db()
        resp = self._request("/api/scheduler", method="GET")
        self.assertEqual(resp.status, "503 Service Unavailable")
        self.assertIn("database_unavailable", resp.data.decode("utf-8"))

    def test_file_serve_requires_auth_in_database(self):
        # /api/file is now open under a tenant policy (task 2.4); an anonymous
        # database request must be rejected by authentication, not a blanket 503.
        self._patch_db()
        resp = self._request("/api/file", method="GET")
        self.assertTrue(str(resp.status).startswith("401"), resp.data)

    def test_upload_requires_auth_in_database(self):
        self._patch_db()
        resp = self._request("/upload", method="POST", data=b"")
        self.assertTrue(str(resp.status).startswith("401"), resp.data)

    def test_public_route_unaffected_by_database_gate(self):
        self._patch_db()
        resp = self._request("/api/health", method="GET")
        self.assertNotEqual(resp.status, "503 Service Unavailable")

    def test_legacy_mode_not_gated_by_database_closure(self):
        # In legacy identity mode the /upload consumer is not forced closed.
        settings = {"identity_mode": "legacy", "identity_db_path": self.db}
        with patch.object(config, "conf", return_value=settings):
            resp = self._request("/upload", method="POST", data=b"")
        self.assertNotEqual(resp.status, "503 Service Unavailable")

    def test_admin_cannot_override_still_closed_consumer(self):
        # A valid database login must NOT let admin status bypass closure of a
        # consumer that remains closed (scheduler until its slice lands).
        self._patch_db()
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._request("/api/scheduler", method="GET",
                             headers={"Cookie": f"cow_session={token}"})
        self.assertEqual(resp.status, "503 Service Unavailable")

    def test_platform_account_routes_classified_platform(self):
        # Platform account detail / password routes (tasks 4.1-4.3) must be
        # reachable through the real app factory: they are platform-domain, not
        # an unknown URL (404) and not an unregistered method (405).
        from auth.http_policy import ROUTE_POLICY, _match_policy
        # password reset route -> platform
        entry, matched = _match_policy("/api/platform/users/abc/password", "POST")
        self.assertTrue(matched)
        self.assertEqual(entry["policy"], "platform")
        # enable/disable / admin flag route (PATCH) -> platform
        entry, matched = _match_policy("/api/platform/users/abc", "PATCH")
        self.assertTrue(matched)
        self.assertEqual(entry["policy"], "platform")
        # A GET on the detail route is unregistered: the route exists but the
        # method is not declared, so the completeness gate returns 405 (not a
        # 500 from invoking a handler that has no GET(user_id)).
        entry, matched = _match_policy("/api/platform/users/abc", "GET")
        self.assertTrue(matched)
        self.assertIsNone(entry)

    def test_models_and_config_console_classified_platform(self):
        # /config and /api/models belong to the platform admin control plane
        # (global model/provider config). In database mode they must no longer be
        # closed/deferred: a platform admin reaches them, the gate does not 503.
        from auth.http_policy import ROUTE_POLICY, _match_policy
        for path, method in (("/config", "GET"), ("/config", "POST"),
                             ("/api/models", "GET"), ("/api/models", "POST")):
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, path)
            self.assertEqual(entry["policy"], "platform", path)

    def test_session_settings_get_registered(self):
        """The composer's model/agent selectors read /api/sessions/:id/settings
        (GET); the completeness gate must not reject it with 405. The handler
        implements GET and the frontend requires it to populate the effective
        model/permission/team, so both GET and POST must be registered."""
        from auth.http_policy import ROUTE_POLICY, _match_policy
        # Both the read (GET) and write (POST) path are registered.
        entry, matched = _match_policy("/api/sessions/sess_a/settings", "GET")
        self.assertTrue(matched)
        self.assertEqual(entry["policy"], "tenant")
        entry, matched = _match_policy("/api/sessions/sess_a/settings", "POST")
        self.assertTrue(matched)
        self.assertEqual(entry["policy"], "tenant")

    def test_require_read_permission_allows_platform_admin_in_database(self):
        # A platform admin (authorization_mode "all") must pass a functional
        # read-permission gate even when their role set lacks the specific
        # permission (e.g. skill.read / tool.read, which the built-in roles do
        # not carry by default). All other auth helpers treat platform-all as
        # unrestricted, so the read-permission gate must too — otherwise
        # /api/skills and /api/tools return 403 to a platform admin and the
        # console's 工具与技能 page stays empty.
        from auth.runtime import RequestContext
        web.ctx.headers = []
        ctx = RequestContext(
            user_id="u1", username="admin", display_name="A",
            is_platform_admin=True, must_change_password=False,
            tenant_id="tnt_1", membership={"id": "m1"}, permissions={"agent.read"},
            is_tenant_admin=True)
        # Must NOT raise HTTPError for a platform admin.
        web_channel._require_read_permission(ctx, "skill.read")
        web_channel._require_read_permission(ctx, "tool.read")

    def test_require_read_permission_rejects_member_without_permission(self):
        # A normal member who lacks the functional permission must be rejected
        # (403), even if they hold agent.read. This preserves the tighter
        # skill.read/tool.read gating required by the resource-authorization
        # milestone for non-platform members.
        from auth.runtime import RequestContext
        web.ctx.headers = []
        ctx = RequestContext(
            user_id="u2", username="member", display_name="M",
            is_platform_admin=False, must_change_password=False,
            tenant_id="tnt_1", membership={"id": "m2"}, permissions={"agent.read"},
            is_tenant_admin=False)
        with self.assertRaises(web.HTTPError) as cm:
            web_channel._require_read_permission(ctx, "skill.read")
        self.assertEqual(cm.exception.args[0], "403 Forbidden")

    def test_require_platform_console_rejects_non_admin_in_database(self):
        # The config/models console guard must reject a resolved context that is
        # not a platform admin (403), and allow a platform admin through. This is
        # what turns the platform policy into a real authorization decision.
        from auth.runtime import RequestContext

        def _ctx(is_platform_admin):
            return RequestContext(
                user_id="u1", username="admin", display_name="A",
                is_platform_admin=is_platform_admin, must_change_password=False,
                tenant_id=None, membership=None, permissions=set(),
                is_tenant_admin=False)

        with patch.object(web_channel, "_is_database_identity", return_value=True):
            with patch("channel.web.auth_handlers._require_context", return_value=_ctx(False)):
                with patch("channel.web.admin_handlers._require_platform_admin",
                           side_effect=web.HTTPError("403 Forbidden", {}, b"x")):
                    with self.assertRaises(web.HTTPError):
                        web_channel._require_platform_console()
            # A platform admin passes the admin gate (no raise).
            with patch("channel.web.auth_handlers._require_context", return_value=_ctx(True)):
                web_channel._require_platform_console()  # must not raise


if __name__ == "__main__":
    unittest.main()
