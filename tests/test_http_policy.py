# encoding:utf-8
"""Tests for the shared HTTP-method policy processor (task 2.1).

Verifies that the real ``build_web_app()`` app factory and the test harness share
the same gate: closed/deferred consumers return 503 in database mode, unknown
URLs stay 404, a matched route with an unregistered method is rejected (405),
and an explicit legacy conf pin still fail-closes rather than pass through.
"""

import json
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
        self.tid = self.svc.list_tenants()[0]["id"]

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
        # database request must be rejected by the gate, not a blanket 503.
        #
        # tenant selection is resolved before the session (design D2), so the
        # anonymous rejection order is: no selection -> 400, selection but no
        # session -> 401. Both are asserted so the order is pinned, not loosened.
        self._patch_db()
        resp = self._request("/api/file", method="GET")
        self.assertEqual(resp.status, "400 Bad Request")
        self.assertIn(b"missing_tenant", resp.data)

        resp = self._request("/api/file", method="GET", headers={"X-Tenant-ID": self.tid})
        self.assertEqual(resp.status, "401 Unauthorized")

    def test_upload_requires_auth_in_database(self):
        self._patch_db()
        resp = self._request("/upload", method="POST", data=b"")
        self.assertEqual(resp.status, "400 Bad Request")
        self.assertIn(b"missing_tenant", resp.data)

        resp = self._request("/upload", method="POST", data=b"",
                             headers={"X-Tenant-ID": self.tid})
        self.assertEqual(resp.status, "401 Unauthorized")

    def test_public_route_unaffected_by_database_gate(self):
        self._patch_db()
        resp = self._request("/api/health", method="GET")
        self.assertNotEqual(resp.status, "503 Service Unavailable")

    def test_explicit_legacy_conf_still_gates_upload(self):
        # Database is the only identity mode: even an explicit legacy pin in
        # conf must not restore shared-password pass-through for consumers.
        settings = {"identity_mode": "legacy", "identity_db_path": self.db}
        with patch.object(config, "conf", return_value=settings), \
                patch.object(web_channel, "conf", return_value=settings):
            resp = self._request("/upload", method="POST", data=b"")
        self.assertTrue(
            str(resp.status).startswith(("400", "401")),
            resp.status,
        )

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

    def test_tenant_admins_read_registered(self):
        """The tenant editor reads the current tenant admin over
        ``GET /api/platform/tenants/:id/admins`` (the handler implements GET and
        the editor's read-only block needs it). Only POST was registered, so the
        completeness gate answered 405 and the console showed "read failed" —
        exactly the failure mode this pins. Both methods must be platform-domain
        and must reach the handler rather than being rejected by the gate."""
        from auth.http_policy import _match_policy
        for method in ("GET", "POST"):
            entry, matched = _match_policy("/api/platform/tenants/tnt_1/admins", method)
            self.assertTrue(matched, method)
            self.assertIsNotNone(entry, f"{method} is not registered for the admins route")
            self.assertEqual(entry["policy"], "platform", method)

    def test_tenant_admins_get_reaches_handler_through_real_app(self):
        # Observed through the real app factory: no session means the handler's
        # own auth gate answers 401. A 405 here would mean the completeness gate
        # rejected the method before the handler could run.
        self._patch_db()
        resp = self._request("/api/platform/tenants/tnt_1/admins", method="GET")
        self.assertNotEqual(resp.status, "405 Method Not Allowed")
        self.assertTrue(str(resp.status).startswith("401"), resp.status)

    def test_tenant_agents_read_and_copy_are_registered(self):
        """The tenant editor's Agent tab reads (GET) and copies (POST) over the
        same route. Both methods must be platform-domain: a missing GET would
        405 the tab the moment it opens, and a missing POST would make the
        copy silently unreachable."""
        from auth.http_policy import _match_policy
        for method in ("GET", "POST"):
            entry, matched = _match_policy("/api/platform/tenants/tnt_1/agents", method)
            self.assertTrue(matched, method)
            self.assertIsNotNone(entry, f"{method} is not registered for the agents route")
            self.assertEqual(entry["policy"], "platform", method)

    def test_tenant_agents_get_reaches_handler_through_real_app(self):
        # Observed through the real app factory: no session means the handler's
        # own auth gate answers 401, never a 405 from the completeness gate.
        self._patch_db()
        resp = self._request("/api/platform/tenants/tnt_1/agents", method="GET")
        self.assertNotEqual(resp.status, "405 Method Not Allowed")
        self.assertTrue(str(resp.status).startswith("401"), resp.status)

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

    def test_still_closed_interactive_channel_actions(self):
        """Weixin QR login stays closed; the Feishu register flow is now open
        under a ``personal`` policy with both methods declared (the start call is
        a GET, so an unregistered GET would have been a 405, not a 503)."""
        from auth.http_policy import _match_policy

        entry, matched = _match_policy("/api/weixin/qrlogin", "GET")
        self.assertTrue(matched)
        self.assertEqual(entry["policy"], "closed")

        for method in ("GET", "POST"):
            entry, matched = _match_policy("/api/feishu/register", method)
            self.assertTrue(matched, method)
            self.assertIsNotNone(entry, f"{method} is not registered for /api/feishu/register")
            self.assertEqual(entry["policy"], "personal", method)

        # The deferred consumer is untouched: still a 503 in database mode.
        self._patch_db()
        resp = self._request("/api/weixin/qrlogin", method="GET")
        self.assertEqual(resp.status, "503 Service Unavailable")

    def test_register_route_rejects_unregistered_method(self):
        """Opening the route must not open every method on it.

        A known path with an unregistered HTTP method is still rejected by the
        completeness gate (405) before any handler runs.
        """
        self._patch_db()
        # ``POST /api/weixin/qrlogin`` is now registered (the handler implements
        # the documented QR status poll) and answers 503 as a closed consumer in
        # database mode. ``DELETE`` is not implemented by any of these handlers,
        # so it must still be rejected by the completeness gate.
        for path, method in (("/api/feishu/register", "DELETE"),
                             ("/api/weixin/qrlogin", "DELETE")):
            resp = self._request(path, method=method)
            self.assertEqual(resp.status, "405 Method Not Allowed", (path, method))

    def test_instance_channel_route_is_platform_for_get_and_post(self):
        """``/api/channels`` serves the instance-level page. Only GET used to be
        registered, so a POST was rejected by the completeness gate; the page
        saves and connects through POST, so both methods must be declared."""
        from auth.http_policy import _match_policy
        for method in ("GET", "POST"):
            entry, matched = _match_policy("/api/channels", method)
            self.assertTrue(matched, method)
            self.assertIsNotNone(entry, f"{method} is not registered for /api/channels")
            self.assertEqual(entry["policy"], "platform", method)

    def test_instance_channel_page_reachable_by_platform_admin(self):
        """The capability report signs ``channels`` as available; before this
        change the endpoint answered 503, so the report and reality disagreed."""
        self._patch_db()
        token = self.svc.login("root", "Str0ngAdminPass").token
        for method in ("GET", "POST"):
            resp = self._request("/api/channels", method=method,
                                 data=json.dumps({"action": "save", "channel": "feishu"})
                                 if method == "POST" else None,
                                 headers={"Cookie": f"cow_session={token}"})
            self.assertNotEqual(resp.status, "503 Service Unavailable",
                                f"{method}: {resp.data}")
            self.assertNotEqual(resp.status, "405 Method Not Allowed",
                                f"{method}: {resp.data}")

    def test_tenant_channel_routes_are_tenant_domain(self):
        from auth.http_policy import _match_policy
        for path, method in (("/api/tenant/channels", "GET"),
                             ("/api/tenant/channels", "POST"),
                             ("/api/tenant/channels/chan_1", "POST"),
                             ("/api/tenant/channels/chan_1/active", "POST")):
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, f"{path} {method}")
            self.assertIsNotNone(entry, f"{path} {method} is not registered")
            self.assertEqual(entry["policy"], "tenant", f"{path} {method}")

    def test_tenant_channel_routes_require_auth_not_closed(self):
        self._patch_db()
        for path, method in (("/api/tenant/channels", "GET"),
                             ("/api/tenant/channels", "POST"),
                             ("/api/tenant/channels/chan_1", "POST"),
                             ("/api/tenant/channels/chan_1/active", "POST")):
            # A tenant endpoint resolves the tenant selection before the session
            # (the documented contract: missing tenant is 400, bad session 401),
            # so name a tenant and expect 401 rather than a 503 (still closed) or
            # 405 (method not registered).
            resp = self._request(path, method=method,
                                 headers={"X-Tenant-ID": "tnt_1"})
            self.assertTrue(str(resp.status).startswith("401"),
                            f"{path} {method} got {resp.status}")

    def test_tenant_channel_unregistered_method_is_rejected(self):
        """Registration completeness (7.6): the route exists, but DELETE is not
        part of its contract and must not reach the handler."""
        from auth.http_policy import _match_policy
        entry, matched = _match_policy("/api/tenant/channels", "DELETE")
        self.assertTrue(matched)
        self.assertIsNone(entry)
        self._patch_db()
        resp = self._request("/api/tenant/channels", method="DELETE")
        self.assertEqual(resp.status, "405 Method Not Allowed")

    def test_external_identity_binding_routes_are_registered_per_scope(self):
        """Both administrators maintain IM bindings, through different surfaces.

        A tenant admin must go through the tenant routes (which confine them to
        their own memberships); the platform routes stay platform-only. Pinning
        both here is what keeps "the tenant surface" from quietly becoming a
        widening of the global one.
        """
        from auth.http_policy import _match_policy
        tenant_routes = (
            ("/api/tenant/members/mem_1/external-identities", "GET"),
            ("/api/tenant/members/mem_1/external-identities", "POST"),
            ("/api/tenant/members/mem_1/external-identities/ext_1", "DELETE"),
            ("/api/tenant/external-identity-attempts", "GET"),
        )
        for path, method in tenant_routes:
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, f"{path} {method}")
            self.assertIsNotNone(entry, f"{path} {method} is not registered")
            self.assertEqual(entry["policy"], "tenant", f"{path} {method}")

        platform_routes = (
            ("/api/platform/users/usr_1/external-identities", "GET"),
            ("/api/platform/users/usr_1/external-identities", "POST"),
            ("/api/platform/users/usr_1/external-identities/ext_1", "DELETE"),
            ("/api/platform/external-identity-attempts", "GET"),
        )
        for path, method in platform_routes:
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, f"{path} {method}")
            self.assertIsNotNone(entry, f"{path} {method} is not registered")
            self.assertEqual(entry["policy"], "platform", f"{path} {method}")

    def test_the_member_update_route_kept_its_policy(self):
        """The new member sub-routes must not have displaced the plain one.

        ``/api/tenant/members/{id}`` is matched by the *shorter* pattern, so
        adding two nested routes next to it is exactly the edit that can shadow
        or overwrite it — which would silently disable member editing.
        """
        from auth.http_policy import _match_policy
        entry, matched = _match_policy("/api/tenant/members/mem_1", "POST")
        self.assertTrue(matched)
        self.assertIsNotNone(entry, "member update lost its policy entry")
        self.assertEqual(entry["policy"], "tenant")

    def test_the_new_binding_routes_require_auth_not_closed(self):
        """They are tenant/platform domain, so an anonymous call is 401 with a
        tenant header — not 503 (still closed) and not 405 (unregistered)."""
        self._patch_db()
        for path, method in (
                ("/api/tenant/members/mem_1/external-identities", "GET"),
                ("/api/tenant/external-identity-attempts", "GET")):
            resp = self._request(path, method=method,
                                 headers={"X-Tenant-ID": "tnt_1"})
            self.assertTrue(str(resp.status).startswith("401"),
                            f"{path} {method} got {resp.status}")

    def test_projects_routes_open_in_database(self):
        # /api/projects* (except browse) must now be tenant domain, not 503.
        self._patch_db()
        for path, method in [
            ("/api/projects", "GET"),
            ("/api/projects/create", "POST"),
            ("/api/projects/select", "POST"),
            ("/api/projects/order", "POST"),
            ("/api/projects/manage", "PUT"),
            ("/api/projects/manage", "DELETE"),
        ]:
            # Pass data=b"" (the existing convention) so _request omits the body
            # and web.py never tries to encode a bytes payload.
            resp = self._request(path, method=method, data=b"")
            # Anonymous database request is rejected by the gate, not a blanket
            # 503: no tenant selection -> 400 (checked first), selection but no
            # session -> 401.
            self.assertEqual(resp.status, "400 Bad Request", f"{path} {method} got {resp.status}")
            self.assertIn(b"missing_tenant", resp.data)

            resp = self._request(path, method=method, data=b"",
                                 headers={"X-Tenant-ID": self.tid})
            self.assertEqual(resp.status, "401 Unauthorized",
                             f"{path} {method} got {resp.status}")

    def test_projects_browse_still_closed_in_database(self):
        self._patch_db()
        resp = self._request("/api/projects/browse", method="GET")
        self.assertEqual(resp.status, "503 Service Unavailable")
        self.assertIn("database_unavailable", resp.data.decode("utf-8"))

    def test_agent_write_routes_registered(self):
        """The whole agent management surface is POST/PUT, but only GET was
        registered, so the completeness gate answered 405 and the console's
        "创建智能体" button did nothing (the handler's own auth never ran).

        ``AgentsHandler`` implements GET+POST for create/update/archive/delete/
        knowledge-mode/team-bind; ``AgentAvatarHandler`` POSTs the upload; and
        ``AgentCoreFileHandler`` PUTs an edited core file. Each method must be
        registered as a tenant-domain route."""
        from auth.http_policy import _match_policy
        for path, method in [
            ("/api/agents", "POST"),
            ("/api/agents/agent_a/avatar", "POST"),
            ("/api/agents/agent_a/files/AGENT.md", "PUT"),
        ]:
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, f"{path} {method} does not match any route")
            self.assertIsNotNone(entry, f"{path} {method} is not registered")
            self.assertEqual(entry["policy"], "tenant", f"{path} {method}")

    def test_agent_create_reaches_handler_through_real_app(self):
        # No session means the handler's own auth gate answers 401. A 405 here
        # means the completeness gate rejected the method first, which is exactly
        # the reported "创建智能体" failure.
        self._patch_db()
        # Pass data=b"" (the existing convention) so _request omits the body and
        # web.py never tries to encode a bytes payload.
        resp = self._request("/api/agents", method="POST", data=b"",
                             headers={"X-Tenant-ID": "tnt_1"})
        self.assertNotEqual(resp.status, "405 Method Not Allowed")
        self.assertTrue(str(resp.status).startswith("401"), resp.status)

    def test_session_detail_write_methods_registered(self):
        """``SessionDetailHandler`` implements PUT (rename / pin) and DELETE
        (delete conversation), both used by the console; only GET was registered
        so rename, pin and delete all 405'd before the handler ran."""
        from auth.http_policy import _match_policy
        for path, method in [
            ("/api/sessions/sess_a", "PUT"),
            ("/api/sessions/sess_a", "DELETE"),
        ]:
            entry, matched = _match_policy(path, method)
            self.assertTrue(matched, f"{path} {method} does not match any route")
            self.assertIsNotNone(entry, f"{path} {method} is not registered")
            self.assertEqual(entry["policy"], "tenant", f"{path} {method}")

    def test_session_delete_reaches_handler_through_real_app(self):
        self._patch_db()
        resp = self._request("/api/sessions/sess_a", method="DELETE",
                             headers={"X-Tenant-ID": "tnt_1"})
        self.assertNotEqual(resp.status, "405 Method Not Allowed")
        self.assertTrue(str(resp.status).startswith("401"), resp.status)


if __name__ == "__main__":
    unittest.main()
