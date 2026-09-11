# encoding:utf-8
"""Platform-level read-only file root + tenant-scoped file serve (2.8–2.10)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from auth.service import IdentityService


class PlatformFileRootConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data_root = os.path.join(self.tmp, "data")
        os.makedirs(self.data_root)

    def test_default_platform_file_root_is_data_root_not_home_or_fs_root(self):
        from channel.web import web_channel

        with patch.object(web_channel, "conf", return_value={
                 "identity_mode": "database",
             }), \
             patch.object(web_channel, "get_data_root", return_value=self.data_root):
            root = web_channel._platform_file_root()

        self.assertEqual(root, os.path.realpath(self.data_root))
        self.assertNotEqual(root, os.path.realpath(os.path.expanduser("~")))
        self.assertNotEqual(root, os.path.realpath("/"))

    def test_explicit_platform_file_root_is_honored(self):
        from channel.web import web_channel

        custom = os.path.join(self.tmp, "custom_root")
        os.makedirs(custom)
        with patch.object(web_channel, "conf", return_value={
                 "identity_mode": "database",
                 "platform_file_root": custom,
             }), \
             patch.object(web_channel, "get_data_root", return_value=self.data_root):
            root = web_channel._platform_file_root()

        self.assertEqual(root, os.path.realpath(custom))


class DatabaseFileServeScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data_root = os.path.join(self.tmp, "data")
        self.acme_shared = os.path.join(self.tmp, "tenants", "acme")
        self.globex_shared = os.path.join(self.tmp, "tenants", "globex")
        for path in (self.data_root, self.acme_shared, self.globex_shared):
            os.makedirs(path)

        self.db = os.path.join(self.tmp, "identity.db")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.acme_shared, allow_weak=True)
        self.root = self.svc.list_platform_users()[0]
        self.acme_id = self.svc.list_tenants()[0]["id"]
        self.globex_id = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            recent_password="Str0ngAdminPass",
            shared_root=self.globex_shared)["id"]

        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.acme_id,
            operation="create-new", username="member", display_name="Member",
            temporary_password="Str0ngTemp1", roles=["member"])
        self.svc.change_password(
            self.svc.login("member", "Str0ngTemp1").token,
            "Str0ngTemp1", "Str0ngMemFinal")
        self.member = self.svc._find_user_by_username("member")

        # Seed readable files.
        self.platform_file = os.path.join(self.data_root, "platform.txt")
        self.acme_file = os.path.join(self.acme_shared, "acme.txt")
        self.globex_file = os.path.join(self.globex_shared, "globex.txt")
        for path, body in (
            (self.platform_file, b"platform"),
            (self.acme_file, b"acme"),
            (self.globex_file, b"globex"),
        ):
            with open(path, "wb") as fh:
                fh.write(body)

        self._patches = [
            patch("channel.web.web_channel.conf", return_value={
                "identity_mode": "database",
                "identity_db_path": self.db,
                "platform_file_root": self.data_root,
            }),
            patch("channel.web.web_channel.get_data_root", return_value=self.data_root),
            patch("auth.service.get_identity_service", return_value=self.svc),
            patch("agent.registry.get_agent_registry",
                  return_value=MagicMock(get=MagicMock(side_effect=KeyError))),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _ctx(self, *, user_id, username, tenant_id, is_platform_admin=False,
             permissions=None):
        return SimpleNamespace(
            user_id=user_id,
            username=username,
            tenant_id=tenant_id,
            is_platform_admin=is_platform_admin,
            is_tenant_admin=False,
            permissions=set(permissions or ()),
            membership={"active": True} if tenant_id else None,
        )

    def test_platform_admin_may_read_platform_file_root(self):
        from channel.web import web_channel

        ctx = self._ctx(
            user_id=self.root["id"], username="root", tenant_id=self.acme_id,
            is_platform_admin=True)
        audit = MagicMock()
        with patch.object(self.svc, "record_audit", audit):
            allowed, via = web_channel._authorize_db_file_path(
                ctx, os.path.realpath(self.platform_file))
        self.assertTrue(allowed)
        self.assertEqual(via, "platform")
        audit.assert_called()
        self.assertEqual(audit.call_args.kwargs.get("action")
                         or audit.call_args[1].get("action"),
                         "platform.file.read")

    def test_plain_member_denied_platform_file_root(self):
        from channel.web import web_channel

        ctx = self._ctx(
            user_id=self.member["id"], username="member", tenant_id=self.acme_id)
        allowed, via = web_channel._authorize_db_file_path(
            ctx, os.path.realpath(self.platform_file))
        self.assertFalse(allowed)
        self.assertIn(via, ("forbidden", "not_found", "denied"))

    def test_member_may_read_own_tenant_shared_root(self):
        from channel.web import web_channel

        ctx = self._ctx(
            user_id=self.member["id"], username="member", tenant_id=self.acme_id)
        with patch.object(web_channel, "_db_file_serve_roots",
                          return_value=[os.path.realpath(self.acme_shared)]):
            allowed, via = web_channel._authorize_db_file_path(
                ctx, os.path.realpath(self.acme_file))
        self.assertTrue(allowed)
        self.assertEqual(via, "tenant")

    def test_cross_tenant_path_is_invisible(self):
        from channel.web import web_channel

        ctx = self._ctx(
            user_id=self.member["id"], username="member", tenant_id=self.acme_id)
        with patch.object(web_channel, "_db_file_serve_roots",
                          return_value=[os.path.realpath(self.acme_shared)]):
            allowed, via = web_channel._authorize_db_file_path(
                ctx, os.path.realpath(self.globex_file))
        self.assertFalse(allowed)
        self.assertIn(via, ("forbidden", "not_found", "denied"))

    def test_missing_tenant_is_forbidden(self):
        from channel.web import web_channel

        ctx = self._ctx(
            user_id=self.root["id"], username="root", tenant_id=None,
            is_platform_admin=True)
        with self.assertRaises(web_channel.web.HTTPError) as raised:
            web_channel._authorize_db_file_path(
                ctx, os.path.realpath(self.platform_file))
        self.assertIn("403", str(raised.exception))

    def test_is_path_allowed_honors_db_scope_not_home(self):
        """Preview/artifact callers of _is_path_allowed must not use ~ in DB mode."""
        from channel.web import web_channel

        home_file = os.path.join(os.path.expanduser("~"), ".cowagent_pfb_probe")
        # Do not create under real home; just assert a synthetic home-like path
        # that is outside data_root is refused in database mode.
        outside = os.path.join(self.tmp, "outside_home_like", "secret.txt")
        os.makedirs(os.path.dirname(outside), exist_ok=True)
        with open(outside, "wb") as fh:
            fh.write(b"secret")

        with patch.object(web_channel, "_serve_allowed_roots",
                          return_value=[os.path.realpath(os.path.dirname(outside))]):
            # Even if legacy roots would allow it, database mode must refuse.
            self.assertFalse(
                web_channel._is_path_allowed(os.path.realpath(outside)))

        # Paths under platform_file_root remain allowed for the capability check.
        self.assertTrue(
            web_channel._is_path_allowed(os.path.realpath(self.platform_file)))


if __name__ == "__main__":
    unittest.main()
