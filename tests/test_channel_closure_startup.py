# encoding:utf-8
"""Startup-channel closure for database identity mode (task 3.11).

Verifies ``_resolve_startup_channels`` yields only the web console in database
mode and the original channel types in legacy mode, and that ``run()`` skips the
scheduler/MCP warmup (and the terminal consumer) in database mode.
"""

import unittest
from unittest.mock import patch

import app
from channel.channel_instances import ChannelInstance


class StartupChannelResolutionTests(unittest.TestCase):
    def _names(self, mode, raw):
        with patch("app._identity_mode", return_value=mode):
            return app._resolve_startup_channels(raw)

    def test_database_mode_yields_only_web(self):
        names = self._names("database", "feishu, dingtalk, web")
        self.assertEqual(names, ["web"])

    def test_database_mode_without_web_yields_web(self):
        names = self._names("database", "feishu, dingtalk")
        self.assertEqual(names, ["web"])

    def test_legacy_mode_preserves_channel_types(self):
        names = self._names("legacy", "feishu, dingtalk, web")
        self.assertEqual(names, ["feishu", "dingtalk", "web"])

    def test_database_mode_skips_external_instances(self):
        inst = ChannelInstance(
            instance_id="fs-1", channel_type="feishu", agent_id="a",
            credentials={"app_id": "x", "app_secret": "y"})
        with patch("app._identity_mode", return_value="database"):
            names = app._resolve_startup_channels("feishu")
        # No web in config, feishu dropped -> falls back to web only.
        self.assertEqual(names, ["web"])
        # db_only_entry drops a feishu instance, keeps a web instance.
        self.assertFalse(app._db_only_entry(inst))
        self.assertTrue(app._db_only_entry(
            ChannelInstance(instance_id="w-1", channel_type="web", agent_id="a")))

    def test_db_only_entry_keeps_web_instance(self):
        self.assertTrue(app._db_only_entry(
            ChannelInstance(instance_id="w-1", channel_type="web", agent_id="a")))


class RunWarmupSkipTests(unittest.TestCase):
    def test_run_skips_scheduler_and_mcp_warmup_in_database(self):
        """In database mode run() must not start scheduler/MCP warmup."""
        with patch("app._identity_mode", return_value="database"), \
                patch("app.load_config") as load_config, \
                patch("app._guard_identity_mode_consistency"), \
                patch("app._migrate_team_roster"), \
                patch("app._warn_if_legacy_workspace_data_exists"), \
                patch("app.sigterm_handler_wrap"), \
                patch("app._sync_builtin_skills"), \
                patch("app._scaffold_subagent_assets"), \
                patch("app._resolve_startup_channels", return_value=["web"]), \
                patch("app._has_web_entry", return_value=True), \
                patch("app._warmup_mcp_tools") as warmup_mcp, \
                patch("app._warmup_scheduler") as warmup_sched, \
                patch("app.ChannelManager") as cm, \
                patch("app.DESKTOP_MODE", False), \
                patch("app.time.sleep", side_effect=KeyboardInterrupt):
            app.run()
        warmup_mcp.assert_not_called()
        warmup_sched.assert_not_called()

    def test_run_starts_warmup_in_legacy(self):
        with patch("app._identity_mode", return_value="legacy"), \
                patch("app.load_config"), \
                patch("app._guard_identity_mode_consistency"), \
                patch("app._migrate_team_roster"), \
                patch("app._warn_if_legacy_workspace_data_exists"), \
                patch("app.sigterm_handler_wrap"), \
                patch("app._sync_builtin_skills"), \
                patch("app._scaffold_subagent_assets"), \
                patch("app._resolve_startup_channels", return_value=["web"]), \
                patch("app._has_web_entry", return_value=True), \
                patch("app._warmup_mcp_tools") as warmup_mcp, \
                patch("app._warmup_scheduler") as warmup_sched, \
                patch("app.ChannelManager") as cm, \
                patch("app.DESKTOP_MODE", False), \
                patch("app.time.sleep", side_effect=KeyboardInterrupt):
            app.run()
        warmup_mcp.assert_called_once()
        warmup_sched.assert_called_once()


if __name__ == "__main__":
    unittest.main()
