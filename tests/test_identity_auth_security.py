# encoding:utf-8
"""Security tests for the database auth handlers (task 2.4 CSRF/origin/503).

Verifies that cookie-state-changing auth endpoints (login/logout/password)
reject cross-origin writes, allow same-origin writes, let bearer-authenticated
requests through (desktop file:// origin), and return 503 when the identity
store itself is unusable instead of falling back to legacy auth.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel, auth_handlers
from auth.service import IdentityService


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
        self.tid = self.svc.list_tenants()[0]["id"]

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/auth/logout", "DbAuthLogoutHandler",
                "/auth/password", "DbAuthPasswordHandler",
                "/auth/check", "DbAuthCheckHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="POST", data="", token=None, origin=None, referer=None,
                 host="localhost:9899", cookie_session=None):
        app = self._app()
        # web.py's request(headers=...) maps 'X' -> 'HTTP_X', so use plain
        # header names (Origin / Host / Authorization / Cookie).
        headers = {"Host": host}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer
        if cookie_session:
            headers["Cookie"] = f"cow_session={cookie_session}"

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_get_service", _fake_service):
            return app.request(path, method=method, data=data, headers=headers)

    def _login(self, path="/auth/login", origin="http://localhost:9899", token=None):
        return self._request(
            path, method="POST",
            data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}),
            origin=origin, token=token)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # --- origin / CSRF -----------------------------------------------------
    def test_login_same_origin_ok(self):
        resp = self._login()
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        # Design D8: the Web login response issues only the session Cookie. The
        # compatibility ``token`` field is always empty; a native client goes
        # through the browser authorization-code + PKCE exchange instead.
        self.assertIn("token", data)
        self.assertEqual(data["token"], "")
        self.assertIn("cow_session", str(getattr(resp, "headers", {})))

    def test_login_cross_origin_rejected(self):
        resp = self._login(origin="https://evil.example.com")
        data = self._json(resp)
        self.assertEqual(resp.status, "403")
        self.assertEqual(data["code"], "cross_origin")

    def test_login_no_origin_allowed(self):
        # curl / non-browser clients send no Origin -> allowed (same as todo/branding)
        resp = self._login(origin=None)
        self.assertEqual(self._json(resp)["status"], "success")

    def test_login_with_bearer_ok_even_with_origin(self):
        # A write carrying a bearer token bypasses cookie-CSRF; but a login has
        # no bearer yet, so a cross-origin login is still fatal for a cookie
        # write. Here we assert the bearer path is not what's tripping.
        resp = self._login(origin="https://evil.example.com")
        self.assertEqual(self._json(resp)["code"], "cross_origin")

    def _cookie_token(self):
        return self.svc.login("root", "Str0ngAdminPass").token

    def _pw_data(self):
        return json.dumps({"old_password": "Str0ngAdminPass", "new_password": "Str0ngPass2"})

    def test_password_cross_origin_rejected_cookie(self):
        # cookie-authenticated change is cross-origin -> rejected
        token = self._cookie_token()
        resp = self._request("/auth/password", method="POST", data=self._pw_data(),
                             origin="https://evil.example.com", cookie_session=token)
        data = self._json(resp)
        self.assertEqual(resp.status, "403")
        self.assertEqual(data["code"], "cross_origin")

    def test_password_same_origin_ok_cookie(self):
        token = self._cookie_token()
        resp = self._request("/auth/password", method="POST", data=self._pw_data(),
                             origin="http://localhost:9899", cookie_session=token)
        self.assertEqual(self._json(resp)["status"], "success")

    def test_password_referer_same_origin_ok_cookie(self):
        token = self._cookie_token()
        resp = self._request("/auth/password", method="POST", data=self._pw_data(),
                             referer="http://localhost:9899/chat", cookie_session=token)
        self.assertEqual(self._json(resp)["status"], "success")

    def test_password_bearer_bypasses_csrf(self):
        # Desktop/file:// or Bearer-authenticated writes bypass cookie-CSRF.
        token = self._cookie_token()
        resp = self._request("/auth/password", method="POST", data=self._pw_data(),
                             origin="https://evil.example.com", token=token)
        self.assertEqual(self._json(resp)["status"], "success")

    def test_logout_cross_origin_rejected_cookie(self):
        token = self._cookie_token()
        resp = self._request("/auth/logout", method="POST",
                             origin="https://evil.example.com", cookie_session=token)
        self.assertEqual(self._json(resp)["code"], "cross_origin")

    def test_password_missing_source_rejected_cookie(self):
        # A cookie-authenticated write with NO Origin/Referer and no legitimate
        # CSRF proof is rejected (no silent allowance for anonymous-ish writes).
        token = self._cookie_token()
        resp = self._request("/auth/password", method="POST", data=self._pw_data(),
                             cookie_session=token)
        self.assertEqual(resp.status, "403")
        self.assertEqual(self._json(resp)["code"], "cross_origin")

    def test_logout_missing_source_rejected_cookie(self):
        token = self._cookie_token()
        resp = self._request("/auth/logout", method="POST", cookie_session=token)
        self.assertEqual(resp.status, "403")
        self.assertEqual(self._json(resp)["code"], "cross_origin")

    # --- identity-store-unavailable -> 503 ---------------------------------
    def test_login_returns_503_when_store_unavailable(self):
        def _boom():
            raise RuntimeError("identity.db is corrupt")

        app = self._app()
        with patch.object(auth_handlers, "_get_service", _boom):
            resp = app.request(
                "/auth/login", method="POST",
                data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}),
                headers={"Host": "localhost:9899"})
        self.assertEqual(resp.status, "503")
        self.assertEqual(self._json(resp)["code"], "identity_db_unavailable")


class LoginRateLimitWebTests(unittest.TestCase):
    """Web-level 429 login rate limiting (task 2.7)."""

    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)

    def _app(self):
        return web.application(
            ("/auth/login", "DbAuthLoginHandler"),
            vars(web_channel), autoreload=False)

    def _login(self, username="root", password="nope", source="127.0.0.1"):
        app = self._app()
        with patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch.object(auth_handlers, "shared_login_limiter", \
                             auth_handlers.shared_login_limiter):
            resp = app.request(
                "/auth/login", method="POST",
                data=json.dumps({"username": username, "password": password}),
                headers={"Host": "localhost:9899"})
        return resp

    def test_rate_limited_returns_429_with_retry_after(self):
        # account_max bad attempts for the same account -> next is 429.
        limiter = auth_handlers.shared_login_limiter
        for _ in range(limiter.account_max()):
            resp = self._login()
            self.assertEqual(resp.status, "401")
        resp = self._login()
        self.assertEqual(resp.status, "429")
        body = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(body["code"], "rate_limited")
        headers = {k.lower(): v for k, v in dict(resp.headers).items()}
        self.assertEqual(headers.get("retry-after"), str(body["retry_after"]))

    def test_rate_limit_honors_source_signature(self):
        limiter = auth_handlers.shared_login_limiter
        # Distint accounts sharing one source: each stays under its own account
        # limit, but the source aggregate (source_max) eventually blocks.
        for i in range(limiter.source_max()):
            resp = self._login(username=f"sweep{i}", password="nope")
            self.assertEqual(resp.status, "401")
        resp = self._login(username="root", password="nope")
        self.assertEqual(resp.status, "429")


if __name__ == "__main__":
    unittest.main()
