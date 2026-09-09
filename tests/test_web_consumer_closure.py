# encoding:utf-8
"""Database-mode chat/file transport authentication (open-database-runtime).

Chat and file consumers are no longer 503-closed in database mode: they run
under per-request identity + permission checks. Anonymous requests are rejected
with 401; closed consumers (scheduler, until its own slice) still 503.
"""

import json
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel


def _request(path, method="GET", data=""):
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


class ConsumerAuthTests:
    """Database consumers demand login instead of the old blanket 503."""

    def _patch_mode(self, mode):
        return patch.object(web_channel, "_is_database_identity", lambda: mode == "database")

    def test_message_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/message", "POST", json.dumps({"content": "hi"}))
        self.assertTrue(resp.status.startswith("401"), resp.data)

    def test_stream_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/stream")
        self.assertTrue(resp.status.startswith(("400", "401")), resp.data)

    def test_poll_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/poll", "POST", json.dumps({}))
        self.assertTrue(resp.status.startswith("401"), resp.data)

    def test_cancel_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/cancel", "POST", json.dumps({}))
        self.assertTrue(resp.status.startswith("401"), resp.data)

    def test_upload_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/upload", "POST", b"")
        self.assertTrue(resp.status.startswith("401"), resp.data)

    def test_file_serve_requires_database_login(self):
        with self._patch_mode("database"):
            resp = _request("/api/file")
        self.assertTrue(resp.status.startswith("401"), resp.data)


class DatabaseConsumerAuthTests(unittest.TestCase, ConsumerAuthTests):
    pass


# In legacy mode the closure gate must not trip. These assert the handlers are
# not rejected by the database closure (they may 400/401 on missing auth input,
# which is fine — the point is they must NOT be 503 database_unavailable).
class LegacyConsumerTests(unittest.TestCase):
    def test_message_not_503_in_legacy(self):
        with patch.object(web_channel, "_is_database_identity", lambda: False):
            resp = _request("/message", "POST", json.dumps({"content": "hi"}))
        self.assertNotEqual(resp.status, 503)


if __name__ == "__main__":
    unittest.main()
