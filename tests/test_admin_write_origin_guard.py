# encoding:utf-8
"""Every management write goes through one origin/CSRF gate (task 4.12).

``channel/web/admin_handlers.py`` carries 25 state-changing methods across the
platform console and the tenant console. Before this guard each of them relied
on the surrounding request being cookie-authenticated without ever asking where
the request came from, so a cross-site form post carrying the operator's cookie
could create tenants, members, roles, departments and channel bindings.

The rule this file pins down:

* a **cookie**-authenticated write must present a same-origin pair
  (``Host`` + matching ``Origin``/``Referer``); a missing source is refused
  rather than silently allowed, because these endpoints have no CSRF token flow;
* a **bearer**-authenticated write is exempt -- the bearer is the independent
  proof of intent, and it is exactly how the desktop client writes;
* the refusal happens *before* the write, so nothing is persisted.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class AdminWriteOriginGuardTests(unittest.TestCase):
    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.shared_root = os.path.join(os.path.realpath(tempfile.mkdtemp()), "acme")
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared_root, allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]

    # --- harness ---------------------------------------------------------

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/api/platform/users/([^/]+)", "PlatformUsersHandler",
                "/api/tenant/departments", "TenantDepartmentsHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _patches(self):
        return [
            patch.object(auth_handlers, "_get_service", lambda: self.svc),
            patch.object(admin_handlers, "_get_service", lambda: self.svc),
            patch("auth.service.get_identity_service", lambda: self.svc),
        ]

    def _request(self, path, method="GET", data="", cookie=None, bearer=None,
                 tenant=None, host=None, origin=None):
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        headers = {}
        if cookie:
            headers["Cookie"] = "cow_session=%s" % cookie
        if bearer:
            headers["Authorization"] = "Bearer %s" % bearer
        if tenant:
            headers["X-Tenant-ID"] = tenant
        if host:
            headers["Host"] = host
        if origin:
            headers["Origin"] = origin
        if headers:
            kwargs["headers"] = headers
        for p in self._patches():
            p.start()
            self.addCleanup(p.stop)
        return self._app().request(path, **kwargs)

    def _login_cookie(self):
        resp = self._request(
            "/auth/login", method="POST",
            data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}))
        self.assertEqual(resp.status, "200 OK", resp.data)
        import re
        match = re.search(r"cow_session=([^;,\s]+)", str(getattr(resp, "headers", "")))
        self.assertIsNotNone(match, "login did not set a cow_session cookie")
        return match.group(1)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _department_names(self):
        return [d["name"] for d in self.svc.list_departments(self.tid)]

    def _create_department(self, **headers):
        return self._request(
            "/api/tenant/departments", method="POST",
            data=json.dumps({"code": "eng", "name": "Engineering"}),
            tenant=self.tid, **headers)

    # --- cookie writes must prove their origin ---------------------------

    def test_a_cookie_write_without_an_origin_is_refused(self):
        cookie = self._login_cookie()
        resp = self._create_department(cookie=cookie, host="test")
        self.assertTrue(str(resp.status).startswith("403"), (resp.status, resp.data))
        self.assertEqual(self._json(resp)["code"], "csrf_failed")
        self.assertNotIn("Engineering", self._department_names())

    def test_a_cross_origin_cookie_write_is_refused(self):
        cookie = self._login_cookie()
        resp = self._create_department(cookie=cookie, host="test",
                                       origin="http://evil.example")
        self.assertTrue(str(resp.status).startswith("403"), (resp.status, resp.data))
        self.assertEqual(self._json(resp)["code"], "csrf_failed")
        self.assertNotIn("Engineering", self._department_names())

    def test_a_same_origin_cookie_write_is_allowed(self):
        cookie = self._login_cookie()
        resp = self._create_department(cookie=cookie, host="test",
                                       origin="http://test")
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["status"], "success")
        self.assertIn("Engineering", self._department_names())

    def test_a_referer_stands_in_for_the_origin_header(self):
        cookie = self._login_cookie()
        kwargs = {"method": "POST",
                  "data": json.dumps({"code": "eng", "name": "Engineering"}),
                  "headers": {"Cookie": "cow_session=%s" % cookie,
                              "Host": "test",
                              "X-Tenant-ID": self.tid,
                              "Referer": "http://test/console"}}
        for p in self._patches():
            p.start()
            self.addCleanup(p.stop)
        resp = self._app().request("/api/tenant/departments", **kwargs)
        self.assertEqual(resp.status, "200 OK", resp.data)

    def test_a_platform_write_is_guarded_the_same_way(self):
        cookie = self._login_cookie()
        resp = self._request(
            "/api/platform/users/%s" % self.root["id"], method="PATCH",
            data=json.dumps({"active": False, "expected_version": 1,
                             "recent_password": "Str0ngAdminPass"}),
            cookie=cookie, host="test", origin="http://evil.example")
        self.assertTrue(str(resp.status).startswith("403"), (resp.status, resp.data))
        still = [u for u in self.svc.list_platform_users() if u["id"] == self.root["id"]]
        self.assertTrue(still and still[0]["active"])

    # --- bearer writes are the desktop client's path ---------------------

    def test_a_bearer_write_needs_no_origin(self):
        """A valid bearer is the independent proof; the desktop client relies on it."""
        # The cookie/bearer rules reject a *repeated same-value* credential, so
        # the bearer here must be a real bearer session, not a copy of a cookie.
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._create_department(
            bearer=token, host="test", origin="http://evil.example")
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertIn("Engineering", self._department_names())

    def test_a_bare_bearer_does_not_buy_the_exemption(self):
        cookie = self._login_cookie()
        resp = self._create_department(
            cookie=cookie, bearer="not-a-real-token",
            host="test", origin="http://evil.example")
        self.assertTrue(str(resp.status).startswith("403"), (resp.status, resp.data))
        self.assertNotIn("Engineering", self._department_names())


if __name__ == "__main__":
    unittest.main()
