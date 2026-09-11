# encoding:utf-8
"""Platform file root + channel explicit registration (phase 1)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class PlatformFileRootTests(unittest.TestCase):
    def test_default_is_data_root_not_home_or_fs_root(self):
        from channel.web import web_channel

        with tempfile.TemporaryDirectory() as root:
            with patch.object(web_channel, "conf", return_value={"platform_file_root": ""}), \
                 patch.object(web_channel, "get_data_root", return_value=root):
                got = web_channel._platform_file_root()
        self.assertEqual(got, os.path.realpath(root))
        self.assertNotEqual(got, os.path.realpath(os.path.expanduser("~")))
        self.assertNotEqual(got, os.path.realpath("/"))

    def test_db_roots_include_platform_root_for_admin_only(self):
        from channel.web import web_channel

        with tempfile.TemporaryDirectory() as root:
            shared = os.path.join(root, "tenant")
            os.makedirs(shared)
            admin = MagicMock(
                is_platform_admin=True, tenant_id="t1")
            member = MagicMock(
                is_platform_admin=False, tenant_id="t1")
            svc = MagicMock()
            svc.tenant_shared_root.return_value = shared
            svc.tenant_agent_ids.return_value = []
            with patch.object(web_channel, "conf", return_value={"platform_file_root": ""}), \
                 patch.object(web_channel, "get_data_root", return_value=root), \
                 patch("auth.service.get_identity_service", return_value=svc), \
                 patch("agent.registry.get_agent_registry",
                       return_value=MagicMock(get=MagicMock(side_effect=KeyError))):
                admin_roots = web_channel._db_file_serve_roots(admin)
                member_roots = web_channel._db_file_serve_roots(member)
        self.assertIn(os.path.realpath(root), admin_roots)
        self.assertNotIn(os.path.realpath(root), member_roots)
        self.assertIn(os.path.realpath(shared), member_roots)


class ChannelExplicitRegistrationTests(unittest.TestCase):
    def test_database_mode_ignores_channel_type_alone(self):
        from channel import channel_instances

        settings = {"channel_type": "feishu", "feishu_app_id": "x"}
        with patch("config.conf", return_value={"identity_mode": "database"}):
            got = channel_instances.resolve_channel_instances(settings)
        self.assertEqual(got, [])

    def test_explicit_roster_still_resolves(self):
        from channel import channel_instances

        settings = {
            "channel_instances": [{
                "instance_id": "corp1",
                "channel_type": "feishu",
                "agent_id": "a1",
                "credentials": {"feishu_app_id": "x"},
            }],
        }
        with patch("config.conf", return_value={"identity_mode": "database"}):
            got = channel_instances.resolve_channel_instances(settings)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].instance_id, "corp1")

    def test_bootstrap_legacy_noop_in_database_mode(self):
        from channel import channel_instances

        settings = {"channel_type": "feishu", "feishu_app_id": "x"}
        roster = {"channel_instances": [], "default_agent_id": "a1"}
        with patch("config.conf", return_value={"identity_mode": "database"}):
            if not hasattr(channel_instances, "bootstrap_legacy_instances"):
                self.skipTest("bootstrap_legacy_instances already removed")
            got = channel_instances.bootstrap_legacy_instances(
                settings, roster, default_agent_id="a1")
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
