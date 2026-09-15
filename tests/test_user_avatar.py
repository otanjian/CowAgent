# encoding:utf-8
"""Route-level tests for ``GET /api/users/<user_id>/avatar``.

The endpoint serves an account's *uploaded* avatar bytes to the account itself,
a valid platform admin, or a caller sharing at least one active tenant
membership with the target — the same visibility the member directory already
grants. It must never serve the bundled defaults (those are static assets), must
return 404 when the account has no upload, and must never leak a filesystem path,
a password hash or any other account field.

These tests drive the database handler through a real ``web.application`` (no
live server) with a temp ``shared_root`` so avatar writes land in a scratch dir.
"""

import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import web

import common.state_dir as sd
from auth.service import IdentityService
from channel.web import auth_handlers, web_channel

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_GIF = b"GIF89a" + b"\x01" * 32


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class UserAvatarReadTests(unittest.TestCase):
    def setUp(self):
        auth_handlers.reset_login_rate_limiter()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_shared_root = sd.shared_root
        sd.shared_root = lambda: self.tmp
        self.addCleanup(lambda: setattr(sd, "shared_root", self._orig_shared_root))

        self.svc = IdentityService(_mk_db())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=str(self.tmp / "acme"), allow_weak=True)
        self.acme = self.svc.list_tenants()[0]["id"]
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.root_token = self._complete_login("root", "Str0ngAdminPass")

        self.alice = self._create_member("alice")
        self.bob = self._create_member("bob")
        self.alice_token = self._complete_login("alice", "Temp-Pass-1")
        self.bob_token = self._complete_login("bob", "Temp-Pass-1")

        # A second tenant with its own admin: shares no membership with acme.
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            admin_username="carol", admin_display="Carol",
            admin_password="Str0ngAdminPass", recent_password="Str0ngAdminPass",
            shared_root=str(self.tmp / "beta"))
        self.carol_token = self._complete_login("carol", "Str0ngAdminPass")

    # --- helpers ---------------------------------------------------------

    def _create_member(self, username):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.acme,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="Temp-Pass-1",
            roles=["member"])
        return [m for m in self.svc.list_members(self.acme)["items"]
                if m["username"] == username][0]

    def _complete_login(self, username, password):
        """Log in, clearing a forced password change, and return a live token."""
        res = self.svc.login(username, password)
        if not self.svc.self_context(res.token).get("must_change_password"):
            return res.token
        final = password + "-final"
        self.svc.change_password(res.token, password, final)
        return self.svc.login(username, final).token

    def _upload(self, token, data=_PNG, suffix=".png"):
        """Write avatar bytes to disk and set the metadata flag like the handler."""
        user_id = self.svc.self_context(token)["user"]["id"]
        auth_handlers._write_user_avatar(user_id, data, suffix)
        self.svc.set_self_avatar(token)
        return user_id

    def _user_ids(self, username):
        return [u["id"] for u in self.svc.list_platform_users()
                if u["username"] == username][0]

    def _app(self):
        return web.application(
            (
                "/auth/login", "DbAuthLoginHandler",
                "/auth/me", "DbAuthMeHandler",
                "/api/users/([^/]+)/avatar", "DbUserAvatarHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _get(self, user_id, token=None):
        app = self._app()
        kwargs = {"method": "GET"}
        if token:
            kwargs["headers"] = {"Authorization": f"Bearer {token}"}

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_get_service", _fake_service):
            return app.request(f"/api/users/{user_id}/avatar", **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # --- allowed reads ---------------------------------------------------

    def test_self_read_returns_uploaded_bytes(self):
        uid = self._upload(self.alice_token, _PNG)
        resp = self._get(uid, token=self.alice_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("200"))
        self.assertEqual(resp.data, _PNG)

    def test_platform_admin_reads_another_account(self):
        uid = self._upload(self.bob_token, _GIF, ".gif")
        resp = self._get(uid, token=self.root_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("200"))
        self.assertEqual(resp.data, _GIF)

    def test_shared_tenant_member_reads_another_account(self):
        uid = self._upload(self.bob_token, _PNG)
        resp = self._get(uid, token=self.alice_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("200"))
        self.assertEqual(resp.data, _PNG)

    def test_content_type_follows_the_stored_extension(self):
        uid = self._upload(self.bob_token, _GIF, ".gif")
        resp = self._get(uid, token=self.root_token)
        self.assertIn("image/gif", resp.headers.get("Content-Type", ""))

    # --- denied / absent -------------------------------------------------

    def test_requires_a_session(self):
        uid = self._upload(self.alice_token)
        resp = self._get(uid)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("401"))
        self.assertEqual(self._json(resp)["code"], "unauthorized")

    def test_unknown_target_is_404(self):
        resp = self._get("usr_does_not_exist", token=self.root_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("404"))

    def test_no_upload_is_404_not_the_bundled_default(self):
        uid = self._user_ids("bob")
        resp = self._get(uid, token=self.root_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("404"))
        self.assertEqual(self._json(resp)["message"], "no avatar")

    def test_unrelated_tenant_member_is_forbidden(self):
        uid = self._upload(self.alice_token)
        resp = self._get(uid, token=self.carol_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("403"))
        self.assertEqual(self._json(resp)["code"], "forbidden")

    def test_inactive_actor_is_unauthorized(self):
        uid = self._upload(self.alice_token)
        alice_user = [u for u in self.svc.list_platform_users()
                      if u["username"] == "alice"][0]
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=alice_user["id"],
            active=False, is_platform_admin=False,
            expected_version=alice_user["version"],
            recent_password="Str0ngAdminPass")
        resp = self._get(uid, token=self.alice_token)
        self.assertTrue(str(getattr(resp, "status", "")).startswith("401"))

    # --- read-only + no leakage -----------------------------------------

    def test_read_is_read_only(self):
        alice_id = self._upload(self.alice_token, _PNG)
        bob_id = self._upload(self.bob_token, _GIF, ".gif")
        before = {
            u["id"]: (u["avatar"], u["version"])
            for u in self.svc.list_platform_users_paged()["items"]
        }
        sessions_before = self.svc._store.execute(
            "SELECT COUNT(*) AS c FROM auth_sessions")[0]["c"]
        for token in (self.alice_token, self.root_token):
            for uid in (alice_id, bob_id):
                self._get(uid, token=token)
        after = {
            u["id"]: (u["avatar"], u["version"])
            for u in self.svc.list_platform_users_paged()["items"]
        }
        self.assertEqual(after, before)
        self.assertEqual(
            self.svc._store.execute("SELECT COUNT(*) AS c FROM auth_sessions")[0]["c"],
            sessions_before)

    def test_response_never_leaks_a_path_or_another_field(self):
        uid = self._upload(self.alice_token, _PNG)
        resp = self._get(uid, token=self.alice_token)
        self.assertEqual(resp.data, _PNG)
        self.assertNotIn(b"user-", resp.data)
        self.assertNotIn(str(self.tmp).encode(), resp.data)
        # The 404 body is a plain JSON error, not a path or a hash.
        missing = self._get(self._user_ids("bob"), token=self.root_token)
        body = missing.data.decode("utf-8")
        self.assertNotIn(str(self.tmp), body)
        self.assertNotIn("password", body)

    # --- list projections carry the flag (not the bytes) -----------------

    def test_list_projections_expose_the_avatar_flag_only(self):
        self._upload(self.alice_token, _PNG)
        member = [m for m in self.svc.list_members(self.acme)["items"]
                  if m["username"] == "alice"][0]
        self.assertEqual(member["avatar"], "image")
        self.assertNotIn("user-", json.dumps(member))
        platform = [u for u in self.svc.list_platform_users_paged()["items"]
                    if u["username"] == "alice"][0]
        self.assertEqual(platform["avatar"], "image")
        bob = [u for u in self.svc.list_platform_users_paged()["items"]
               if u["username"] == "bob"][0]
        self.assertIsNone(bob["avatar"])


class DefaultAvatarAssetTests(unittest.TestCase):
    """The five bundled defaults ship as static SVG under the existing /assets."""

    def test_all_five_default_avatars_are_served_as_well_formed_svg(self):
        import xml.dom.minidom

        app = web.application(
            ("/assets/(.*)", "AssetsHandler"), vars(web_channel), autoreload=False)
        for n in range(1, 6):
            resp = app.request(f"/assets/avatars/default-{n}.svg", method="GET")
            self.assertTrue(str(getattr(resp, "status", "")).startswith("200"),
                            f"default-{n}.svg must be served")
            self.assertIn("image/svg+xml", resp.headers.get("Content-Type", ""))
            body = resp.data.decode("utf-8")
            self.assertIn("<svg", body)
            # 64x64 so it scales crisply into any disc without a raster asset.
            self.assertIn('viewBox="0 0 64 64"', body)
            xml.dom.minidom.parseString(body)  # raises on malformed markup


if __name__ == "__main__":
    unittest.main()
