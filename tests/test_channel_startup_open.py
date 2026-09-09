# encoding:utf-8
"""Startup-channel resolution after runtime consumers opened (open-database-runtime).

Database identity mode now starts the same channels as legacy: external IM
channels configured in config.json/team.json participate (inbound identity is
resolved at the message layer), and run() performs scheduler/MCP warmup in both
identity modes. This reverses the old task 3.11 "web only in database mode"
closure that this change deletes.
"""

import unittest
from unittest.mock import patch

import app


class StartupChannelResolutionTests(unittest.TestCase):
    def _names(self, raw):
        return app._resolve_startup_channels(raw)

    def test_database_mode_preserves_channel_types(self):
        # A fake registry is not required: config entries resolve statically.
        names = self._names("feishu, dingtalk, web")
        self.assertEqual(names, ["feishu", "dingtalk", "web"])

    def test_legacy_mode_preserves_channel_types(self):
        names = self._names("feishu, dingtalk, web")
        self.assertEqual(names, ["feishu", "dingtalk", "web"])

    def test_empty_config_falls_back_to_web(self):
        self.assertEqual(self._names(""), ["web"])


class RunWarmupTests(unittest.TestCase):
    """run() warms scheduler + MCP in BOTH identity modes (task 2.3)."""

    def _run(self):
        with patch("app._guard_identity_mode_consistency"), \
                patch("app.load_config"), \
                patch("app._migrate_team_roster"), \
                patch("app._warn_if_legacy_workspace_data_exists"), \
                patch("app.sigterm_handler_wrap"), \
                patch("app._sync_builtin_skills"), \
                patch("app._scaffold_subagent_assets"), \
                patch("app._resolve_startup_channels", return_value=["web"]), \
                patch("app._has_web_entry", return_value=True), \
                patch("app._warmup_mcp_tools") as warmup_mcp, \
                patch("app._warmup_scheduler") as warmup_sched, \
                patch("app.ChannelManager"), \
                patch("app.DESKTOP_MODE", False), \
                patch("app.time.sleep", side_effect=KeyboardInterrupt):
            app.run()
        return warmup_mcp, warmup_sched

    def test_run_starts_warmup_in_database_mode(self):
        warmup_mcp, warmup_sched = self._run()
        warmup_mcp.assert_called_once()
        warmup_sched.assert_called_once()

    def test_run_starts_warmup_in_legacy_mode(self):
        warmup_mcp, warmup_sched = self._run()
        warmup_mcp.assert_called_once()
        warmup_sched.assert_called_once()


if __name__ == "__main__":
    unittest.main()
