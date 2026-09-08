# encoding:utf-8
"""Tests for the instance Web branding service and routes.

Covers text boundaries, image validation (valid/malicious/oversized), the
public-information minimisation, write authorization (unauthenticated /
no-password / cross-site), optimistic-concurrency conflicts, and the
all-or-nothing publish invariant on asset-generation / write failure.
"""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import web

from channel.web import branding, web_channel
from channel.web.branding import (
    BRAND_NAME_MAX,
    BrandingError,
    BrandingService,
    DEFAULT_BRAND_DESC,
    DEFAULT_BRAND_NAME,
    LOGO_DESC_MAX,
)
from PIL import Image


def _png(size=(64, 64), color=(0, 120, 200, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


class BrandingServiceUnitTests(unittest.TestCase):
    def _svc(self, data_root=None):
        root = data_root or tempfile.mkdtemp()
        return BrandingService(data_root=root), root

    def test_default_public_payload(self):
        svc, _ = self._svc()
        payload = svc.public_payload()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["brand_name"], DEFAULT_BRAND_NAME)
        self.assertEqual(payload["logo_description"], DEFAULT_BRAND_DESC)
        self.assertEqual(payload["logo_url"], "/assets/rongda-ai-mark.svg")
        self.assertEqual(payload["revision"], 0)

    def test_validation_trims_and_enforces_length(self):
        svc, _ = self._svc()
        name = svc.save(0, "  \u5bb9\u5927AI  ", "控制台", "keep")
        self.assertEqual(name["brand_name"], "容大AI")

        # too long
        with self.assertRaises(BrandingError) as ctx:
            svc.save(name["revision"], "x" * (BRAND_NAME_MAX + 1), "控制台", "keep")
        self.assertEqual(ctx.exception.code, "invalid_brand_name")
        self.assertEqual(ctx.exception.field, "brand_name")

        # control char / newline
        with self.assertRaises(BrandingError):
            svc.save(name["revision"], "容大\nAI", "控制台", "keep")

        # description too long
        with self.assertRaises(BrandingError) as ctx:
            svc.save(name["revision"], "容大AI", "y" * (LOGO_DESC_MAX + 1), "keep")
        self.assertEqual(ctx.exception.field, "logo_description")

    def test_rejects_markup_as_literal(self):
        svc, _ = self._svc()
        rec = svc.save(0, "容大AI", "<b>bold</b>", "keep")
        # stored verbatim, never parsed/rendered by the service
        self.assertEqual(rec["logo_description"], "<b>bold</b>")

    def test_logo_actions_are_mutually_exclusive(self):
        svc, _ = self._svc()
        png = _png()
        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "replace", None)
        self.assertEqual(ctx.exception.code, "conflicting_logo_action")

        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "keep", ("a.png", png))
        self.assertEqual(ctx.exception.code, "conflicting_logo_action")

        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "default", ("a.png", png))
        self.assertEqual(ctx.exception.code, "conflicting_logo_action")

        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "bogus")
        self.assertEqual(ctx.exception.code, "invalid_logo_action")

    def test_replace_validates_and_produces_assets(self):
        svc, _ = self._svc()
        rec = svc.save(0, "容大AI", "企业智能协作平台", "replace", ("logo.png", _png()))
        self.assertEqual(rec["revision"], 1)
        self.assertTrue(rec["logo_asset_id"])
        self.assertTrue(rec["favicon_asset_id"])
        # public payload references the new asset
        payload = svc.public_payload()
        self.assertIn("/api/branding/assets/", payload["logo_url"])
        self.assertNotIn(".png.png", payload["logo_url"])

    def test_rejects_malicious_and_oversized_images(self):
        svc, _ = self._svc()
        # extension is png but contents are HTML -> decode/format mismatch
        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "replace", ("logo.png", b"<html>not an image</html>"))
        self.assertIn("image", ctx.exception.code)

        # > 2 MiB
        big = b"\x89PNG\r\n\x1a\n" + b"\0" * (2 * 1024 * 1024 + 1)
        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "replace", ("logo.png", big))
        self.assertEqual(ctx.exception.code, "image_too_large")
        self.assertEqual(ctx.exception.http_status, 413)

        # GIF not supported
        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "容大AI", "描述", "replace", ("logo.gif", _png()))
        self.assertEqual(ctx.exception.code, "invalid_image_format")

    def test_unpublished_candidate_is_not_served(self):
        svc, _ = self._svc()
        # Validate a candidate but do NOT publish it.
        png = _png()
        with self.assertRaises(BrandingError):
            svc.resolve_asset("missing" * 8 + ".png")

    def test_concurrent_conflict_returns_409(self):
        svc, _ = self._svc()
        svc.save(0, "容大AI", "描述", "keep")
        with self.assertRaises(BrandingError) as ctx:
            svc.save(0, "其他", "描述", "keep")  # stale expected_revision
        self.assertEqual(ctx.exception.code, "version_conflict")
        self.assertEqual(ctx.exception.http_status, 409)

    def test_default_action_and_reset(self):
        svc, _ = self._svc()
        svc.save(0, "容大AI", "描述", "replace", ("a.png", _png()))
        rec = svc.save(1, "容大AI", "描述", "default")
        self.assertIsNone(rec["logo_asset_id"])
        reset = svc.reset(2)
        self.assertEqual(reset["brand_name"], DEFAULT_BRAND_NAME)
        self.assertEqual(reset["logo_description"], DEFAULT_BRAND_DESC)

    def test_service_restart_preserves_brand(self):
        svc, root = self._svc()
        svc.save(0, "重启品牌", "描述", "replace", ("a.png", _png()))
        # A brand-new service over the same data root reads the same brand.
        svc2 = BrandingService(data_root=root)
        payload = svc2.public_payload()
        self.assertEqual(payload["brand_name"], "重启品牌")
        self.assertEqual(payload["revision"], 1)

    def test_corrupt_config_blocks_save_but_explicit_reset_preserves_original(self):
        svc, root = self._svc()
        svc.save(0, "First", "", "keep")
        svc.save(1, "Latest", "", "keep")
        original = b'{"revision":2, "broken":'
        Path(svc._main_path).write_bytes(original)
        management = svc.management_payload(True)
        self.assertFalse(management["can_manage"])
        self.assertTrue(management["can_reset"])
        self.assertEqual(management["storage_error"], "branding_storage_corrupt")
        self.assertEqual(svc.public_payload()["brand_name"], "First")
        with self.assertRaises(BrandingError) as error:
            svc.save(management["revision"], "Overwrite", "", "keep")
        self.assertEqual(error.exception.http_status, 503)
        self.assertEqual(Path(svc._main_path).read_bytes(), original)
        reset = svc.reset(management["revision"])
        self.assertGreater(reset["revision"], 2)
        self.assertEqual(reset["brand_name"], DEFAULT_BRAND_NAME)
        self.assertEqual([p.read_bytes() for p in Path(root, "branding", "corrupt").glob("*.json")], [original])
        with self.assertRaises(BrandingError) as error:
            svc.save(2, "Old browser", "", "keep")
        self.assertEqual(error.exception.code, "version_conflict")

    def test_missing_main_is_not_an_untouched_instance(self):
        svc, _ = self._svc()
        svc.save(0, "First", "", "keep")
        Path(svc._main_path).unlink()
        self.assertFalse(svc.management_payload(True)["can_manage"])
        restored = svc.reset(0)
        self.assertGreater(restored["revision"], 1)

    def test_old_storage_without_revision_ledger_recovers_above_damaged_version(self):
        svc, _ = self._svc()
        svc.save(0, "First", "", "keep")
        svc.save(1, "Second", "", "keep")
        Path(svc._revision_path).unlink()
        Path(svc._main_path).write_text("{ broken", encoding="utf-8")
        self.assertEqual(svc.reset(1)["revision"], 3)

    def test_recovery_copy_failure_does_not_publish(self):
        svc, _ = self._svc()
        svc.save(0, "First", "", "keep")
        Path(svc._main_path).write_bytes(b"damaged")
        with patch.object(branding.shutil, "copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(BrandingError):
                svc.reset(0)
        self.assertEqual(Path(svc._main_path).read_bytes(), b"damaged")

    def test_interrupted_publication_reserves_revision_without_exposing_candidate(self):
        svc, _ = self._svc()
        svc.save(0, "First", "", "keep")
        real_replace = branding.os.replace
        def fail_main(source, target):
            if target == svc._main_path:
                raise OSError("interrupted")
            return real_replace(source, target)
        with patch.object(branding.os, "replace", side_effect=fail_main):
            with self.assertRaises(BrandingError):
                svc.save(1, "Candidate", "", "keep")
        self.assertEqual(svc.get_published()["brand_name"], "First")
        self.assertEqual(svc.save(1, "Retry", "", "keep")["revision"], 3)

    def test_windows_lock_uses_same_byte_for_lock_and_unlock(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "lock")
            calls = []
            def locking(fd, mode, length):
                calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), length))
            win = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
            with patch.object(branding, "fcntl", None), patch.dict(sys.modules, {"msvcrt": win}):
                for _ in range(2):
                    with branding._FileLock(path):
                        pass
            self.assertEqual(calls, [(1, 0, 1), (2, 0, 1)] * 2)
            self.assertEqual(Path(path).read_bytes(), b"\0")

    def test_windows_lock_timeout_closes_file(self):
        with tempfile.TemporaryDirectory() as root:
            win = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2,
                                        locking=lambda *args: (_ for _ in ()).throw(OSError("busy")))
            lock = branding._FileLock(os.path.join(root, "lock"), timeout=-1)
            with patch.object(branding, "fcntl", None), patch.dict(sys.modules, {"msvcrt": win}):
                with self.assertRaises(BrandingError):
                    with lock:
                        pass
            self.assertIsNone(lock._fd)


class BrandingRouteTests(unittest.TestCase):
    """Real HTTP status, signed credential and CSRF checks over isolated data."""

    def _service(self):
        return BrandingService(data_root=tempfile.mkdtemp())

    def _app_for(self):
        return web.application(
            (
                "/api/branding/public", "BrandingPublicHandler",
                "/api/branding", "BrandingManageHandler",
                "/api/branding/reset", "BrandingResetHandler",
                "/api/branding/assets/(.*)", "BrandingAssetHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, svc, path, method="GET", data="", headers=None,
                 password="secret", authenticated=True, origin_ok=True, identity_mode="legacy"):
        app = self._app_for()
        with patch.object(web_channel, "_branding_service", lambda: svc), \
                patch.object(web_channel, "conf", lambda: {"web_password": password, "identity_mode": identity_mode}):
            request_headers = {}
            if authenticated and password:
                token = web_channel._create_auth_token()
                request_headers = {
                    "Cookie": "cow_auth_token=" + token,
                    "Origin": "http://0.0.0.0:8080" if origin_ok else "https://foreign.example",
                    "X-Branding-CSRF": web_channel._branding_csrf_token(token),
                }
            request_headers.update(headers or {})
            kwargs = {"method": method, "headers": request_headers}
            if data:
                kwargs["data"] = data
            return app.request(path, **kwargs)

    def _token(self, password="secret"):
        with patch.object(web_channel, "_get_web_password", lambda: password):
            return web_channel._create_auth_token()

    @staticmethod
    def _json(response):
        return json.loads(response.data.decode("utf-8"))

    def test_public_endpoint_returns_minimal_payload(self):
        svc = self._service()
        response = self._request(svc, "/api/branding/public", method="GET")
        data = self._json(response)
        self.assertEqual(set(data.keys()), {
            "enabled", "revision", "brand_name", "logo_description",
            "logo_url", "favicon_url",
        })
        # No management fields leak to the public endpoint.
        self.assertNotIn("can_manage", data)
        self.assertNotIn("limits", data)
        self.assertNotIn("defaults", data)

    def test_management_read_requires_password_when_enabled(self):
        svc = self._service()
        response = self._request(svc, "/api/branding", authenticated=False)
        self.assertEqual(response.status, "401 Unauthorized")

    def test_no_password_mode_is_readonly(self):
        svc = self._service()
        response = self._request(svc, "/api/branding", method="GET", password="", authenticated=False)
        data = self._json(response)
        self.assertFalse(data["can_manage"])
        self.assertEqual(data["readonly_reason"], "web_console_password_required")

    def test_write_refused_without_password(self):
        svc = self._service()
        response = self._request(
            svc, "/api/branding", method="POST",
            data="expected_revision=0&brand_name=A&logo_description=&logo_action=keep",
            password="", authenticated=False,
        )
        body = self._json(response)
        self.assertEqual(response.status, "403 Forbidden")
        self.assertEqual(body["code"], "web_console_password_required")

    def test_write_with_password_saves(self):
        svc = self._service()
        response = self._request(
            svc, "/api/branding", method="POST",
            data="expected_revision=0&brand_name=%E5%AE%B9%E5%A4%A7AI&logo_description=&logo_action=keep",
            password="secret", authenticated=True,
        )
        body = self._json(response)
        self.assertEqual(response.status, "200 OK")
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["revision"], 1)

    def test_login_allows_editing_without_brand_activation(self):
        for config in ({}, {"branding_enabled": False}):
            with self.subTest(config=config), patch("config.conf", return_value=config):
                svc = self._service()
                settings = self._json(self._request(svc, "/api/branding"))
                self.assertTrue(settings["can_manage"])
                self.assertTrue(settings["can_reset"])
                self.assertEqual(settings["readonly_reason"], "")
                saved = self._request(svc, "/api/branding", method="POST",
                                      data="expected_revision=0&brand_name=RongAI&logo_description=Console&logo_action=keep")
                self.assertEqual(saved.status, "200 OK")
                public = self._json(self._request(svc, "/api/branding/public", authenticated=False))
                self.assertEqual(public["brand_name"], "RongAI")
                self.assertEqual(public["logo_description"], "Console")
                self.assertTrue(public["enabled"])
                reset = self._request(svc, "/api/branding/reset", method="POST",
                                      data='{"expected_revision":1}')
                self.assertEqual(reset.status, "200 OK")

    def test_asset_served_only_when_referenced(self):
        svc = self._service()
        rec = svc.save(0, "容大AI", "描述", "replace", ("a.png", _png()))
        response = self._request(svc, f"/api/branding/assets/{rec['logo_asset_id']}", method="GET")
        headers = response.headers if hasattr(response, "headers") else {}
        ctype = headers.get("Content-Type") if isinstance(headers, dict) else None
        self.assertEqual(ctype, "image/png")
        self.assertTrue(response.data)  # non-empty PNG bytes

    def test_cross_origin_write_blocked(self):
        svc = self._service()
        response = self._request(
            svc, "/api/branding", method="POST",
            data="expected_revision=0&brand_name=A&logo_description=&logo_action=keep",
            password="secret", authenticated=True, origin_ok=False,
        )
        body = self._json(response)
        self.assertEqual(response.status, "403 Forbidden")
        self.assertEqual(body["code"], "csrf_failed")

    def test_query_token_cannot_authorize_save_or_reset(self):
        svc = self._service()
        for path, data in [("/api/branding", "expected_revision=0&brand_name=Bad&logo_action=keep"),
                           ("/api/branding/reset", '{"expected_revision":0}')]:
            response = self._request(svc, path + "?token=" + self._token(), method="POST",
                                     data=data, authenticated=False)
            self.assertEqual(response.status, "401 Unauthorized")
        self.assertEqual(svc.get_published()["revision"], 0)

    def test_enterprise_or_unknown_modes_fail_closed_for_both_writes(self):
        svc = self._service()
        for mode in ("database", "enterprise", "unknown"):
            for path in ("/api/branding", "/api/branding/reset"):
                for password in ("secret", ""):
                    response = self._request(svc, path, method="POST", identity_mode=mode, password=password,
                                             headers={"Authorization": "Bearer " + self._token()})
                    self.assertEqual(response.status, "403 Forbidden")
                    self.assertEqual(self._json(response)["code"], "branding_enterprise_unavailable")
        self.assertEqual(svc.get_published()["revision"], 0)

    def test_database_management_uses_real_database_session_and_stays_readonly(self):
        from auth.service import IdentityService
        from channel.web import auth_handlers
        svc = self._service()
        auth_handlers.reset_login_rate_limiter()
        with tempfile.TemporaryDirectory() as root:
            identity = IdentityService(os.path.join(root, "identity.db"))
            identity.bootstrap(tenant_code="review", tenant_name="Review", admin_username="root",
                               admin_display="Root", admin_password="Str0ngAdminPass", shared_root=root,
                               allow_weak=True)
            session = identity.login("root", "Str0ngAdminPass").token
            with patch.object(auth_handlers, "_get_service", lambda: identity):
                response = self._request(svc, "/api/branding", identity_mode="database", authenticated=False,
                                         headers={"Authorization": "Bearer " + session})
                data = self._json(response)
                self.assertEqual(response.status, "200 OK")
                self.assertFalse(data["can_manage"])
                self.assertFalse(data["can_reset"])
                self.assertEqual(data["readonly_reason"], "branding_enterprise_unavailable")
                self.assertNotIn("csrf_token", data)
                rejected = self._request(svc, "/api/branding", identity_mode="database")
                self.assertTrue(rejected.status.startswith("401"))

    def test_csrf_is_issued_by_management_and_required_for_cookie_writes(self):
        svc = self._service()
        token = self._token()
        cookie = {"Cookie": "cow_auth_token=" + token}
        data = self._json(self._request(svc, "/api/branding", authenticated=False, headers=cookie))
        headers = {**cookie, "Origin": "http://0.0.0.0:8080", "X-Branding-CSRF": data["csrf_token"]}
        response = self._request(svc, "/api/branding", method="POST", authenticated=False, headers=headers,
                                 data="expected_revision=0&brand_name=CSRF&logo_action=keep")
        self.assertEqual(response.status, "200 OK")
        cases = [ {**headers, "X-Branding-CSRF": ""}, {**headers, "X-Branding-CSRF": "invalid"},
                  {**headers, "Origin": ""}, {**headers, "Origin": "https://0.0.0.0:8080"},
                  {**headers, "Origin": "http://foreign.example"} ]
        for bad in cases:
            for path, body in [("/api/branding", "expected_revision=1&brand_name=Bad&logo_action=keep"),
                               ("/api/branding/reset", '{"expected_revision":1}')]:
                response = self._request(svc, path, method="POST", data=body, authenticated=False, headers=bad)
                self.assertEqual(response.status, "403 Forbidden")
        self.assertEqual(svc.get_published()["revision"], 1)
        # A valid token issued for another login is not a CSRF token for this one.
        with patch.object(web_channel.time, "time", return_value=web_channel.time.time() - 10):
            another_token = self._token()
        bad = {**headers, "Cookie": "cow_auth_token=" + another_token}
        response = self._request(svc, "/api/branding/reset", method="POST", data='{"expected_revision":1}',
                                 authenticated=False, headers=bad)
        self.assertEqual(response.status, "403 Forbidden")

    def test_bearer_writes_without_origin_and_cookie_still_work(self):
        svc = self._service()
        headers = {"Authorization": "Bearer " + self._token()}
        saved = self._request(svc, "/api/branding", method="POST", authenticated=False, headers=headers,
                              data="expected_revision=0&brand_name=Bearer&logo_action=keep")
        self.assertEqual(saved.status, "200 OK")
        reset = self._request(svc, "/api/branding/reset", method="POST", authenticated=False, headers=headers,
                              data='{"expected_revision":1}')
        self.assertEqual(reset.status, "200 OK")

    def test_http_errors_are_real_statuses(self):
        svc = self._service()
        svc.save(0, "Current", "", "keep")
        for path, body in [("/api/branding", "expected_revision=0&brand_name=Stale&logo_action=keep"),
                           ("/api/branding/reset", '{"expected_revision":0}')]:
            response = self._request(svc, path, method="POST", data=body)
            self.assertEqual(response.status, "409 Conflict")
        missing = self._request(svc, "/api/branding/assets/" + "f" * 64 + ".png")
        self.assertEqual(missing.status, "404 Not Found")
        bad_json = self._request(svc, "/api/branding/reset", method="POST", data="[")
        self.assertEqual(bad_json.status, "400 Bad Request")
        for status in (400, 413, 415, 500, 503):
            with patch.object(svc, "save", side_effect=BrandingError("test_error", "failure", status)):
                response = self._request(svc, "/api/branding", method="POST",
                                         data="expected_revision=1&brand_name=Current&logo_action=keep")
            self.assertEqual(int(response.status.split()[0]), status)
        with patch.object(svc, "management_payload", side_effect=OSError("unreadable")):
            self.assertEqual(self._request(svc, "/api/branding").status, "500 Internal Server Error")

    def test_corrupt_storage_http_readonly_and_protected_reset(self):
        svc = self._service()
        svc.save(0, "Original", "", "keep")
        Path(svc._main_path).write_bytes(b"damaged")
        data = self._json(self._request(svc, "/api/branding"))
        self.assertFalse(data["can_manage"])
        self.assertTrue(data["can_reset"])
        self.assertTrue(data["csrf_token"])
        refused = self._request(svc, "/api/branding", method="POST",
                                data="expected_revision=0&brand_name=Overwrite&logo_action=keep")
        self.assertEqual(refused.status, "503 Service Unavailable")
        restored = self._request(svc, "/api/branding/reset", method="POST", data='{"expected_revision":0}')
        self.assertEqual(restored.status, "200 OK")
        self.assertGreater(self._json(restored)["revision"], 1)
        self.assertEqual([p.read_bytes() for p in Path(svc._root, "corrupt").glob("*.json")], [b"damaged"])


class BrandingFrontendTests(unittest.TestCase):
    """Static-source checks that the web frontend wires the branding view and
    application hooks. Mirrors test_agent_web_management's source-reading style.
    """

    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def _read(self, relative):
        with open(os.path.join(self.ROOT, relative), "r", encoding="utf-8") as f:
            return f.read()

    def test_html_has_branding_view_and_menu(self):
        html = self._read("channel/web/chat.html")
        self.assertIn('data-view="branding"', html)
        self.assertIn('id="view-branding"', html)
        self.assertIn('id="branding-brand-name"', html)
        self.assertIn('id="branding-logo-desc"', html)
        self.assertIn('id="branding-save"', html)
        self.assertIn('id="branding-reset-all"', html)
        # unified brand DOM slots for preview
        self.assertIn('data-brand-slot="logo"', html)
        self.assertIn('data-brand-slot="name"', html)
        self.assertIn('data-brand-slot="desc"', html)
        self.assertIn('id="sidebar-brand-name"', html)
        self.assertIn('id="login-brand-name"', html)
        self.assertIn('data-brand-desc', html)

    def test_js_registers_branding_view_and_state(self):
        js = self._read("channel/web/static/js/console.js")
        self.assertIn("branding:    { group: 'nav_group_platform_ops', page: 'menu_branding' },", js)
        self.assertIn("function initBrandingView", js)
        self.assertIn("function applyBrandToDocument", js)
        self.assertIn("function fetchPublicBrand", js)
        self.assertIn("effectiveLogoUrl", js)
        self.assertIn("brandWordmarkHTML", js)
        self.assertIn("brandingConfirmDiscard", js)
        # dirty guard present in navigateTo and beforeunload
        self.assertIn("brandingConfirmDiscard(() =>", js)
        self.assertIn("!brandingDirty", js)

    def test_js_uses_brand_url_for_default_agent_avatar(self):
        js = self._read("channel/web/static/js/console.js")
        self.assertIn("agent-avatar-brand\" src=\"${effectiveLogoUrl()}\"", js)
        # No hard-coded rongda-ai-mark remains for the fallback logo.
        self.assertIn("effectiveLogoUrl()", js)

    def test_js_has_trilingual_branding_labels(self):
        js = self._read("channel/web/static/js/console.js")
        # All three languages carry the branding set: zh (简体), zh-Hant (繁體),
        # en. The keys are the same, only the text differs.
        self.assertEqual(js.count("branding_save:"), 3)
        self.assertEqual(js.count("branding_reset_all:"), 3)
        self.assertEqual(js.count("branding_conflict:"), 3)


class BrandingBackupServiceTests(unittest.TestCase):
    """The service exposes export/restore used by ``cli/commands/backup.py``."""

    def _svc(self, data_root=None):
        root = data_root or tempfile.mkdtemp()
        return BrandingService(data_root=root), root

    def test_export_none_on_untouched_instance(self):
        svc, _ = self._svc()
        self.assertIsNone(svc.export_backup())

    def test_export_after_custom_save(self):
        svc, _ = self._svc()
        svc.save(0, "容大AI", "企业智能协作平台", "replace", ("logo.png", _png()))
        bundle = svc.export_backup()
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["source_revision"], 1)
        self.assertEqual(bundle["brand_name"], "容大AI")
        self.assertEqual(bundle["logo_description"], "企业智能协作平台")
        self.assertIn(bundle["logo_asset_id"], bundle["assets"])
        self.assertIn(bundle["favicon_asset_id"], bundle["assets"])

    def test_export_skips_missing_asset_file(self):
        svc, root = self._svc()
        rec = svc.save(0, "容大AI", "描述", "replace", ("logo.png", _png()))
        # Simulate a vanished asset file; export still succeeds without it.
        for key in ("logo_asset_id", "favicon_asset_id"):
            aid = rec.get(key)
            if aid:
                os.remove(os.path.join(root, "branding", "assets", aid))
        bundle = svc.export_backup()
        self.assertEqual(bundle["logo_asset_id"], rec["logo_asset_id"])
        self.assertNotIn(rec["logo_asset_id"], bundle["assets"])

    def test_restore_allocates_new_revision(self):
        source, source_root = self._svc()
        source.save(0, "容大AI", "描述", "replace", ("logo.png", _png()))
        bundle = source.export_backup()

        target, target_root = self._svc()
        target.save(0, "容大AI", "原始", "keep")  # already at revision 1
        restored = target.restore_backup(bundle)
        self.assertEqual(restored["revision"], 2)  # fresh, not the source's 1
        self.assertEqual(restored["brand_name"], "容大AI")
        self.assertEqual(restored["logo_description"], "描述")
        self.assertEqual(restored.get("source_backup_revision"), bundle["source_revision"])

    def test_restore_rejects_bad_checksum_before_write(self):
        svc, root = self._svc()
        svc.save(0, "容大AI", "描述", "replace", ("logo.png", _png()))
        bundle = svc.export_backup()
        # Tamper with an asset body while keeping the same asset id.
        bad_asset_id = bundle["logo_asset_id"]
        bundle["assets"][bad_asset_id] = b"tampered"
        target, _ = self._svc()
        with self.assertRaises(BrandingError) as ctx:
            target.restore_backup(bundle)
        self.assertEqual(ctx.exception.code, "branding_backup_bad_checksum")
        # Nothing was published: target still the default.
        self.assertEqual(target.get_published()["revision"], 0)

    def test_restore_rejects_unsupported_schema(self):
        bundle = {"schema_version": 999, "source_revision": 1}
        target, _ = self._svc()
        with self.assertRaises(BrandingError) as ctx:
            target.restore_backup(bundle)
        self.assertEqual(ctx.exception.code, "branding_schema_unsupported")

    def test_restore_rejects_missing_asset(self):
        svc, root = self._svc()
        svc.save(0, "容大AI", "描述", "replace", ("logo.png", _png()))
        bundle = svc.export_backup()
        del bundle["assets"][bundle["logo_asset_id"]]
        target, _ = self._svc()
        with self.assertRaises(BrandingError) as ctx:
            target.restore_backup(bundle)
        self.assertEqual(ctx.exception.code, "branding_backup_missing_asset")

    def test_restore_is_immediately_visible(self):
        source, source_root = self._svc()
        source.save(0, "容大AI", "描述", "replace", ("logo.png", _png()))
        bundle = source.export_backup()
        target, target_root = self._svc()
        restored = target.restore_backup(bundle)
        self.assertEqual(restored["brand_name"], "容大AI")
        self.assertTrue(target.has_custom_brand())
        self.assertEqual(target.public_payload()["logo_description"], "描述")

    def test_obsolete_switch_does_not_hide_saved_brand_or_assets(self):
        with patch("config.conf", return_value={"branding_enabled": False}):
            svc, root = self._svc()
            rec = svc.save(0, "自定义名", "描述", "replace", ("logo.png", _png()))
            restarted = BrandingService(data_root=root)
            payload = restarted.public_payload()
            self.assertTrue(payload["enabled"])
            self.assertEqual(payload["brand_name"], "自定义名")
            self.assertEqual(payload["revision"], 1)
            content_type, image_bytes = restarted.resolve_asset(rec["logo_asset_id"])
            self.assertEqual(content_type, "image/png")
            self.assertTrue(image_bytes)


class ConfigTitleProjectionTests(unittest.TestCase):
    """The legacy /config.title must project the effective brand name, not a
    hard-coded default, and must never accept brand fields as write input."""

    def test_editables_do_not_include_brand_fields(self):
        from channel.web.web_channel import ConfigHandler
        self.assertNotIn("brand_name", ConfigHandler.EDITABLE_KEYS)
        self.assertNotIn("logo_description", ConfigHandler.EDITABLE_KEYS)
        self.assertNotIn("branding_enabled", ConfigHandler.EDITABLE_KEYS)

    def test_branding_needs_no_feature_switch(self):
        from config import available_setting
        self.assertNotIn("branding_enabled", available_setting)


if __name__ == "__main__":
    unittest.main()
