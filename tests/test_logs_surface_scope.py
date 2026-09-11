# encoding:utf-8
"""``run.log`` is platform control-plane data, and its output is redacted.

``GET /api/logs`` (SSE) and ``GET /api/logs/download`` served the
*process-global* ``run.log`` to any authenticated console user -- in database
mode a plain member of any tenant could stream or download every other tenant's
activity, plus whatever a handler happened to log (vendor tokens, session
cookies). Tasks 4.11 closes both halves: the route policy is ``platform`` (the
gate refuses a non-platform-admin before the handler) and the handler refuses in
its own right, and the emitted lines are redacted.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.runtime import RequestContext
from channel.web import web_channel


def _ctx(*, is_platform_admin=False, tenant_id="tnt_acme"):
    return RequestContext(
        user_id="u1", username="u1", display_name="u1",
        is_platform_admin=is_platform_admin, must_change_password=False,
        tenant_id=tenant_id, membership={"id": "m1"},
        permissions={"chat.use"}, is_tenant_admin=True,
    )


class LogsSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.data_root = tempfile.mkdtemp()
        self.log_path = os.path.join(self.data_root, "run.log")
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("line one: nothing to see\n")
            f.write("Authorization: Bearer sk-live-abc123\n")
            f.write("password=hunter2 ok\n")
            f.write("token = \"tok_123\"\n")

    def _app(self):
        return web.application(
            (
                "/api/logs", "LogsHandler",
                "/api/logs/download", "LogsDownloadHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _patched(self, ctx):
        """The guard/mode stubs shared by the app-level and generator-level calls."""
        from auth.runtime import IdentityContextError

        def resolve_context(require_tenant=False):
            if ctx is None:
                raise IdentityContextError("unauthorized", "unauthorized", 401)
            return ctx

        return [
            patch.object(web_channel, "get_data_root", lambda: self.data_root),
            patch.object(web_channel, "_is_database_identity", lambda: True),
            patch("channel.web.auth_handlers._require_context",
                  side_effect=resolve_context),
        ]

    def _request(self, path, ctx):
        """Drive the real ``_require_platform_console`` guard through the app.

        Only the context resolution and the mode flag are stubbed; the identity
        domain decision (``_require_platform_admin``) and the handler are real.
        """
        patches = self._patched(ctx)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return self._app().request(path, method="GET", headers={"Host": "test"})

    def _stream(self, ctx):
        """Call ``LogsHandler.GET`` directly and return its (unconsumed) generator.

        Running the SSE response through ``app.request`` would consume the whole
        stream, and the handler deliberately tails for up to ten minutes. The
        first chunk is all this test needs.
        """
        patches = self._patched(ctx)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        web.ctx.headers = []
        web.ctx.env = {"REQUEST_METHOD": "GET", "HTTP_HOST": "test"}
        return web_channel.LogsHandler().GET()

    @staticmethod
    def _body(resp):
        return resp.data if isinstance(resp.data, bytes) else b"".join(resp.data)

    # --- who may read it ------------------------------------------------

    def test_a_member_cannot_stream_the_logs(self):
        resp = self._request("/api/logs", _ctx(is_platform_admin=False))
        self.assertTrue(str(resp.status).startswith("403"),
                        (resp.status, resp.data))

    def test_a_member_cannot_download_the_logs(self):
        resp = self._request("/api/logs/download", _ctx(is_platform_admin=False))
        self.assertTrue(str(resp.status).startswith("403"),
                        (resp.status, resp.data))

    def test_a_platform_admin_can_download_the_logs(self):
        resp = self._request("/api/logs/download", _ctx(is_platform_admin=True))
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertIn(b"line one", resp.data)

    def test_a_platform_admin_can_stream_the_logs(self):
        gen = self._stream(_ctx(is_platform_admin=True))
        try:
            first = next(gen)
        finally:
            gen.close()
        self.assertIn(b"line one", first)

    # --- the output is redacted -----------------------------------------

    def test_credentials_are_masked_in_the_download(self):
        resp = self._request("/api/logs/download", _ctx(is_platform_admin=True))
        body = self._body(resp)
        self.assertIn(b"line one", body)
        self.assertNotIn(b"sk-live-abc123", body)
        self.assertNotIn(b"hunter2", body)
        self.assertNotIn(b"tok_123", body)
        # The key survives so the line stays diagnosable.
        self.assertIn(b"password", body)

    def test_credentials_are_masked_in_the_stream(self):
        gen = self._stream(_ctx(is_platform_admin=True))
        try:
            first = next(gen)
        finally:
            gen.close()
        self.assertNotIn(b"sk-live-abc123", first)
        payload = json.loads(first.decode("utf-8").split("data: ", 1)[1])
        self.assertNotIn("sk-live-abc123", payload["content"])
        self.assertNotIn("hunter2", payload["content"])

    # --- the helper itself ----------------------------------------------

    def test_a_clean_line_is_untouched(self):
        line = "[INFO][2026-01-01 00:00:00][x.py:1] - did a thing: ok"
        self.assertEqual(web_channel._redact_log_line(line), line)


if __name__ == "__main__":
    unittest.main()
