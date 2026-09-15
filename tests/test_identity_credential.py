# encoding:utf-8
"""Tests for the unified credential-selection rules (task 2.2).

Covers cookie/bearer precedence, same-value-repeated-as-cookie, different-value
-> mixed (400), malformed bearer, and no fallback on an invalid selection.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import web

from auth.credential import select_credential, bearer_token_from_header
from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers
from tests._helpers import cookie_value


class CredentialSelectionTests(unittest.TestCase):
    def test_only_cookie(self):
        sel = select_credential("tok123", "")
        self.assertEqual(sel.token, "tok123")
        self.assertEqual(sel.source, "cookie")
        self.assertFalse(sel.both_present)
        self.assertFalse(sel.mixed)

    def test_only_bearer(self):
        sel = select_credential("", "tok123")
        self.assertEqual(sel.token, "tok123")
        self.assertEqual(sel.source, "bearer")
        self.assertFalse(sel.both_present)
        self.assertFalse(sel.mixed)

    def test_same_value_repeated_is_cookie(self):
        # Old clients send both; same value -> authenticate once, as a cookie.
        sel = select_credential("tok123", "tok123")
        self.assertEqual(sel.token, "tok123")
        self.assertEqual(sel.source, "cookie")
        self.assertTrue(sel.both_present)
        self.assertFalse(sel.mixed)

    def test_different_values_is_mixed(self):
        # Different values -> no fallback, hard 400 mixed_credentials.
        sel = select_credential("tok123", "other")
        self.assertEqual(sel.token, "")
        self.assertEqual(sel.source, "")
        self.assertTrue(sel.both_present)
        self.assertTrue(sel.mixed)

    def test_none(self):
        sel = select_credential("", "")
        self.assertEqual(sel.token, "")
        self.assertEqual(sel.source, "")
        self.assertFalse(sel.both_present)
        self.assertFalse(sel.mixed)

    def test_malformed_bearer_is_empty(self):
        # A bare token is not a bearer form -> treated as absent.
        self.assertEqual(bearer_token_from_header("plain"), "")
        self.assertEqual(bearer_token_from_header(""), "")
        self.assertEqual(bearer_token_from_header("Basic abc"), "")
        # well-formed bearer returns the token
        self.assertEqual(bearer_token_from_header("Bearer tok123"), "tok123")
        # inner whitespace is malformed
        self.assertEqual(bearer_token_from_header("Bearer tok 123"), "")

    def test_whitespace_stripped(self):
        sel = select_credential(" tok123 ", " tok123 ")
        self.assertEqual(sel.token, "tok123")
        self.assertEqual(sel.source, "cookie")


class CredentialRouteTests(unittest.TestCase):
    """Route-level enforcement of the unified credential rules (task 2.2).

    Exercises the real auth handlers against a temp identity store, verifying
    that a mixed cookie+bearer is a hard 400, a same-value pair is treated as a
    cookie (origin check applies), and the web login response carries no token.
    """

    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = os.path.join(tempfile.mkdtemp(), "identity.db")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/auth/me", "DbAuthMeHandler",
                "/auth/password", "DbAuthPasswordHandler",
                "/api/platform/tenants", "PlatformTenantsHandler",
            ),
            vars(web_channel), autoreload=False)

    def _request(self, path, method="GET", data="", cookie=None, bearer=None,
                 origin=None, host="localhost:9899", tenant=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        headers = {"Host": host}
        if origin:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = f"cow_session={cookie}"
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if tenant:
            headers["X-Tenant-ID"] = tenant
        kwargs["headers"] = headers

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    def _token(self):
        return self.svc.login("root", "Str0ngAdminPass").token

    def test_mixed_cookie_and_bearer_400(self):
        resp = self._request("/auth/me", method="GET",
                             cookie=self._token(), bearer="different-token")
        self.assertEqual(resp.status, "400")
        self.assertEqual(self._json(resp)["code"], "mixed_credentials")

    def test_same_value_repeated_is_ok(self):
        token = self._token()
        resp = self._request("/auth/me", method="GET", cookie=token, bearer=token)
        self.assertTrue(str(resp.status).startswith("200"))
        self.assertEqual(self._json(resp)["status"], "success")

    def test_same_value_repeated_still_origin_checked_on_write(self):
        # Same-value repeated credential is treated as a cookie request, so a
        # cross-origin write is rejected (no bearer exemption).
        token = self._token()
        resp = self._request("/auth/password", method="POST",
                             data=json.dumps({"old_password": "Str0ngAdminPass",
                                              "new_password": "Str0ngPass2"}),
                             cookie=token, bearer=token,
                             origin="https://evil.example.com")
        self.assertEqual(resp.status, "403")
        self.assertEqual(self._json(resp)["code"], "cross_origin")

    def test_web_login_sets_a_cookie_and_returns_no_reusable_token(self):
        """Login establishes the session with a Cookie only.

        ``enterprise-access-enforcement`` / ``identity-session``: the Web
        ``/auth/login`` response keeps the compatibility ``token`` field but it
        must be empty, so a readable login response can never be replayed as a
        native Bearer credential. Desktop obtains its own AuthSession through the
        authorization-code + PKCE exchange (``/auth/desktop/authorize`` ->
        ``/auth/desktop/token``) instead of being handed one here.
        """
        resp = self._request("/auth/login", method="POST",
                             data=json.dumps({"username": "root",
                                              "password": "Str0ngAdminPass"}),
                             origin="http://localhost:9899")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data.get("token"), "")
        self.assertTrue(cookie_value(resp, "cow_session"),
                        "login must set the session Cookie")

    def test_invalid_cookie_no_fallback_to_bearer(self):
        # A malformed cookie plus an unrelated bearer are DIFFERENT values ->
        # hard 400 mixed_credentials; the request never falls back to either.
        resp = self._request("/auth/me", method="GET",
                             cookie="bad-cookie", bearer="bad-bearer")
        self.assertEqual(resp.status, "400")
        self.assertEqual(self._json(resp)["code"], "mixed_credentials")

    def test_selected_credential_invalid_returns_401(self):
        # A single invalid bearer that is well-formed but not a live session ->
        # 401 (not a fallback to a different source).
        resp = self._request("/auth/me", method="GET", bearer="invalid-live-token")
        self.assertEqual(resp.status, "401")
        self.assertEqual(self._json(resp)["code"], "unauthorized")

    def test_platform_ignores_residual_tenant_header(self):
        # A platform endpoint must resolve by platform identity alone; a stale
        # X-Tenant-ID (even one the user is not a member of) must not block it.
        token = self._token()
        resp = self._request("/api/platform/tenants", method="GET",
                             cookie=token, tenant="nonexistent-tenant")
        self.assertTrue(str(resp.status).startswith("200"))
        self.assertEqual(self._json(resp)["status"], "success")

    def test_personal_endpoint_ignores_residual_tenant_header(self):
        # /auth/me reports the account identity, not the tenant header.
        token = self._token()
        resp = self._request("/auth/me", method="GET", cookie=token, tenant=self.tid)
        self.assertTrue(str(resp.status).startswith("200"))
        body = self._json(resp)
        self.assertEqual(body["user"]["username"], "root")
        # The residual tenant header is not used to grant/deny the self read.
        self.assertNotEqual(body["status"], "error")

    def test_restricted_platform_admin_cannot_reach_platform_endpoint(self):
        # A restricted (must_change_password) platform admin must be denied the
        # platform control plane; admin qualification does not bypass it.
        token = self.svc.login("root", "Str0ngAdminPass").token
        self.svc._store.execute(
            "UPDATE users SET must_change_password=1, temp_password_expires_at=? "
            "WHERE username='root'", (int(time.time()) + 3600,))
        resp = self._request("/api/platform/tenants", method="GET", cookie=token)
        self.assertEqual(resp.status, "403")
        self.assertEqual(self._json(resp)["code"], "password_change_required")

    def test_restricted_session_still_reads_self_and_changes_password(self):
        # The whitelist keeps minimal self read + password change working.
        token = self.svc.login("root", "Str0ngAdminPass").token
        self.svc._store.execute(
            "UPDATE users SET must_change_password=1, temp_password_expires_at=? "
            "WHERE username='root'", (int(time.time()) + 3600,))
        me = self._request("/auth/me", method="GET", cookie=token)
        self.assertTrue(str(me.status).startswith("200"))
        body = self._json(me)
        self.assertEqual(body["user"]["username"], "root")
        self.assertTrue(body["must_change_password"])
        # change-password is a cookie write -> same-origin required.
        resp = self._request("/auth/password", method="POST",
                             data=json.dumps({"old_password": "Str0ngAdminPass",
                                              "new_password": "RestrictedPass2!"}),
                             cookie=token, origin="http://localhost:9899")
        self.assertTrue(str(resp.status).startswith("200"))

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
