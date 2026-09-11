# encoding:utf-8
"""Database-mode chat/file transport authentication (open-database-runtime).

Chat and file consumers are no longer 503-closed in database mode: they run
under per-request identity + permission checks. Anonymous requests are rejected
with 400 (missing tenant) or 401 (no session); closed consumers (scheduler,
until its own slice) still 503.
"""

import json
import unittest

import web

from channel.web import web_channel


def _request(path, method="GET", data="", headers=None):
    app = web.application(
        (
            path, _handler_for(path),
        ),
        vars(web_channel),
        autoreload=False,
    )
    kwargs = {"method": method}
    if data:
        kwargs["data"] = data
    if headers:
        kwargs["headers"] = headers
    return app.request(path, **kwargs)


def _handler_for(path):
    # Chat authentication and the file/voice consumers.
    return {
        "/message": "MessageHandler",
        "/stream": "StreamHandler",
        "/poll": "PollHandler",
        "/cancel": "CancelHandler",
        "/upload": "UploadHandler",
        "/api/file": "FileServeHandler",
        "/api/scheduler": "SchedulerHandler",
    }.get(path, "RootHandler")


def _assert_auth_rejected(testcase, resp):
    """Anonymous database requests fail closed (tenant gate or session)."""
    status = str(resp.status)
    testcase.assertTrue(
        status.startswith(("400", "401")),
        "expected 400/401, got %s: %s" % (status, resp.data),
    )
    testcase.assertNotEqual(status, "503 Service Unavailable")


class DatabaseConsumerAuthTests(unittest.TestCase):
    """Database consumers demand login instead of the old blanket 503."""

    def test_message_requires_database_login(self):
        resp = _request("/message", "POST", json.dumps({"content": "hi"}))
        _assert_auth_rejected(self, resp)

    def test_stream_requires_database_login(self):
        resp = _request("/stream")
        _assert_auth_rejected(self, resp)

    def test_poll_requires_database_login(self):
        resp = _request("/poll", "POST", json.dumps({}))
        _assert_auth_rejected(self, resp)

    def test_cancel_requires_database_login(self):
        resp = _request("/cancel", "POST", json.dumps({}))
        _assert_auth_rejected(self, resp)

    def test_upload_requires_database_login(self):
        resp = _request("/upload", "POST", b"")
        _assert_auth_rejected(self, resp)

    def test_file_serve_requires_database_login(self):
        resp = _request("/api/file")
        _assert_auth_rejected(self, resp)

    def test_anonymous_never_bypasses_as_shared_password(self):
        """Pinning a shared password in conf must not open consumers."""
        from unittest.mock import patch

        settings = {"identity_mode": "database", "web_password": "shared"}
        with patch.object(web_channel, "conf", return_value=settings), \
                patch("config.conf", return_value=settings):
            resp = _request("/message", "POST", json.dumps({"content": "hi"}),
                            headers={"Cookie": "cow_auth_token=forged"})
        _assert_auth_rejected(self, resp)


if __name__ == "__main__":
    unittest.main()
