# encoding:utf-8
"""Validate the console navigation presentation switch (task 3.2).

``web_navigation_mode`` is a layout-only switch with allowed values
``classic`` | ``split``. It must never change authentication, authorization,
identity mode or consumer open/closed state, and it must fall back safely to
``classic`` when the value is absent or invalid. ``ChatHandler.GET`` also
injects a validated value so the client never trusts an arbitrary string.

These tests import the real ``web`` module (present in this venv); they patch
only the header call and the config value, so no request context is required.
"""

import os
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _import_wc():
    import channel.web.web_channel as wc
    return wc


class TestWebNavigationMode(unittest.TestCase):
    def _mode_for(self, config_value):
        web_channel = _import_wc()
        # Only set the key when a value is supplied so we can test "absent".
        effective = {} if config_value is None else {"web_navigation_mode": config_value}
        with patch.object(web_channel, "conf", lambda: effective):
            return web_channel._web_navigation_mode()

    def test_defaults_to_classic_when_absent(self):
        self.assertEqual(self._mode_for(None), "classic")

    def test_accepts_valid_values(self):
        self.assertEqual(self._mode_for("classic"), "classic")
        self.assertEqual(self._mode_for("split"), "split")

    def test_is_case_insensitive_and_trimmed(self):
        self.assertEqual(self._mode_for("  SPLIT  "), "split")
        self.assertEqual(self._mode_for("Classic"), "classic")

    def test_invalid_values_fall_back_to_classic(self):
        for bad in ("", "grid", "mixed", "true", "split2", "admin", "1", "none"):
            self.assertEqual(self._mode_for(bad), "classic", msg=f"value={bad!r}")

    def _chat_handler_output(self, config_value):
        from channel.web.web_channel import ChatHandler

        web_channel = _import_wc()
        effective = {} if config_value is None else {"web_navigation_mode": config_value}
        html = "<html>{{COW_DEFAULT_LANG}}/{{COW_NAVIGATION_MODE}}</html>"
        with patch.object(web_channel, "conf", lambda: effective):
            with patch("channel.web.web_channel._require_auth", lambda: None):
                with patch("builtins.open", mock_open(read_data=html)):
                    with patch.object(web_channel.web, "header", lambda *a, **k: None):
                        return ChatHandler().GET()

    def test_chat_handler_injects_validated_mode(self):
        out = self._chat_handler_output("split")
        self.assertIn("split", out)
        self.assertNotIn("{{COW_NAVIGATION_MODE}}", out)

    def test_chat_handler_defaults_invalid_mode(self):
        out = self._chat_handler_output("bogus")
        self.assertIn("classic", out)
        self.assertNotIn("{{COW_NAVIGATION_MODE}}", out)

    def test_admin_url_maps_to_chat_handler(self):
        web_channel = _import_wc()
        urls = list(web_channel._WEB_URLS)
        self.assertIn("/admin", urls)
        idx = urls.index("/admin")
        self.assertEqual(urls[idx + 1], "ChatHandler")
        # Same shell as /chat — both must resolve to ChatHandler.
        chat_idx = urls.index("/chat")
        self.assertEqual(urls[chat_idx + 1], "ChatHandler")


if __name__ == "__main__":
    unittest.main()
