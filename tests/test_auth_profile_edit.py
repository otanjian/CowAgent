# encoding:utf-8
"""Route-level tests for the self-service profile & avatar edit endpoints.

Covers PATCH /auth/profile (global display_name + selected-tenant member
display_name / position) and GET/POST /auth/profile/avatar against the
database identity handlers, without booting a live server. The service is
pointed at a temp identity.db and identity_mode is patched to "database".
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


class AuthProfileEditTests(unittest.TestCase):
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
        self.token = self.svc.login("root", "Str0ngAdminPass").token

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/auth/me", "DbAuthMeHandler",
                "/auth/context", "DbAuthContextHandler",
                "/auth/profile", "DbSelfProfileHandler",
                "/auth/profile/avatar", "DbSelfAvatarHandler",
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

        with patch.object(auth_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # --- PATCH /auth/profile ---------------------------------------------

    def test_profile_edit_requires_session(self):
        resp = self._request("/auth/profile", method="PATCH",
                             data=json.dumps({"display_name": "New"}))
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))
        self.assertEqual(self._json(resp)["code"], "unauthorized")

    def test_profile_edit_global_display_name(self):
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             data=json.dumps({"display_name": "Renamed"}))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["display_name"], "Renamed")
        # persisted
        self.assertEqual(self.svc.self_context(self.token)["user"]["display_name"], "Renamed")

    def test_profile_edit_member_name_and_position(self):
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             tenant=self.tid,
                             data=json.dumps({
                                 "member_display_name": "Team Leado",
                                 "position_text": "Architect",
                             }))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        entry = data["tenants"][0]
        m = entry["membership"]
        self.assertEqual(m["display_name"], "Team Leado")
        self.assertEqual(m["position_text"], "Architect")

    def test_profile_edit_blank_display_name_rejected(self):
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             data=json.dumps({"display_name": "   "}))
        self.assertTrue(str(resp.status).startswith("400"))
        self.assertEqual(self._json(resp)["code"], "invalid_display_name")

    def test_profile_edit_not_a_member_of_requested_tenant(self):
        # Claim to edit a tenant the caller is not a member of -> 403
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             tenant="nope",
                             data=json.dumps({"member_display_name": "X"}))
        self.assertTrue(str(resp.status).startswith("403"))
        self.assertEqual(self._json(resp)["code"], "not_a_member")

    def test_profile_edit_cannot_change_roles_or_username(self):
        # Sending unknown/malicious keys must be ignored (only whitelisted
        # fields are read), and username must remain untouched.
        before = self.svc.self_context(self.token)["user"]["username"]
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             data=json.dumps({
                                 "display_name": "Renamed",
                                 "username": "root2",
                                 "is_platform_admin": False,
                             }))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["username"], before)
        self.assertTrue(data["user"]["is_platform_admin"])

    def test_profile_edit_no_op_returns_current(self):
        resp = self._request("/auth/profile", method="PATCH", token=self.token,
                             data=json.dumps({"display_name": "Root"}))
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["display_name"], "Root")

    # --- /auth/profile/avatar -------------------------------------------

    def test_avatar_get_requires_session(self):
        resp = self._request("/auth/profile/avatar", method="GET")
        self.assertTrue(str(getattr(resp, "status", "")).startswith("4"))

    def test_avatar_flag_set_via_service(self):
        # Drive the metadata flag through the service (the HTTP multipart decode
        # path is exercised by the handler tests; the flag persistence is the
        # contract this test locks down).
        ctx = self.svc.set_self_avatar(self.token)
        self.assertEqual(ctx["user"]["avatar"], "image")
        # re-read /auth/me: the avatar flag is now present in the projection
        ctx2 = self.svc.self_context(self.token)
        self.assertEqual(ctx2["user"]["avatar"], "image")

    def test_avatar_flag_appears_in_list_projections_without_bytes_or_paths(self):
        # The admin member/platform lists mirror only the metadata flag so the
        # client can choose the uploaded picture or the bundled default; no
        # filesystem path or image bytes may leak into a list projection.
        self.svc.set_self_avatar(self.token)
        member = [m for m in self.svc.list_members(self.tid)["items"]
                  if m["username"] == "root"][0]
        self.assertEqual(member["avatar"], "image")
        self.assertNotIn("user-", json.dumps(member))
        platform = [u for u in self.svc.list_platform_users_paged()["items"]
                    if u["username"] == "root"][0]
        self.assertEqual(platform["avatar"], "image")
        self.assertNotIn("user-", json.dumps(platform))

    def test_avatar_flag_is_null_before_any_upload(self):
        member = [m for m in self.svc.list_members(self.tid)["items"]
                  if m["username"] == "root"][0]
        self.assertIsNone(member["avatar"])

    def test_avatar_get_404_when_no_file_on_disk(self):
        # Even after the flag is set, without an actual file on disk the GET
        # must return 404 JSON, never crash.
        self.svc.set_self_avatar(self.token)
        resp = self._request("/auth/profile/avatar", method="GET", token=self.token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("404"))
        self.assertEqual(self._json(resp)["status"], "error")

    def test_avatar_upload_and_get_roundtrip(self):
        # Drive the full handler path with a fake upload object + a patched
        # shared_root so the bytes land on a temp dir, then GET them back.
        import io
        import pathlib
        import tempfile
        import common.state_dir as sd
        from channel.web import auth_handlers as ah

        tmp = pathlib.Path(tempfile.mkdtemp())
        orig_sr = sd.shared_root
        sd.shared_root = lambda: tmp
        try:
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

            class FakeUpload:
                filename = "a.png"
                def __init__(self, data): self.file = io.BytesIO(data)

            def fake_raw_input():
                return {"avatar": FakeUpload(png)}

            def fake_write(user_id, raw, suffix):
                return ah._write_user_avatar(user_id, raw, suffix)

            # POST upload success
            with patch.object(ah, "_raw_web_input", fake_raw_input):
                resp = self._request("/auth/profile/avatar", method="POST", token=self.token)
            data = self._json(resp)
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["user"]["avatar"], "image")

            # GET returns the bytes now on disk
            resp = self._request("/auth/profile/avatar", method="GET", token=self.token)
            self.assertTrue(str(getattr(resp, "status", "")).startswith("200"))
            self.assertEqual(resp.data, png)
        finally:
            sd.shared_root = orig_sr

    def test_avatar_upload_rejects_oversize(self):
        # A >2 MiB payload is rejected by the handler before any disk write.
        import io
        import pathlib
        import tempfile
        import common.state_dir as sd

        tmp = pathlib.Path(tempfile.mkdtemp())
        orig_sr = sd.shared_root
        sd.shared_root = lambda: tmp
        try:
            from channel.web import auth_handlers as ah
            big = b"\x89PNG" + b"\x00" * (2 * 1024 * 1024 + 10)

            class FakeUpload:
                filename = "a.png"
                def __init__(self, data): self.file = io.BytesIO(data)

            def fake_raw_input():
                return {"avatar": FakeUpload(big)}

            with patch.object(ah, "_raw_web_input", fake_raw_input):
                resp = self._request("/auth/profile/avatar", method="POST", token=self.token)
            self.assertTrue(str(getattr(resp, "status", "")).startswith("400"))
            self.assertEqual(self._json(resp)["code"], "avatar_too_large")
        finally:
            sd.shared_root = orig_sr


if __name__ == "__main__":
    unittest.main()
