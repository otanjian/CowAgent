# encoding:utf-8
"""Console-scope projection and interface denial for message channels.

Change ``tenant-owned-message-channels`` keeps ONE console page key
(``admin.channels``) and reports the *relative* scope per operator: a platform
admin sees the instance-level page, a tenant admin sees its own tenant's, and —
since task 6.1 — a plain member sees their own connections on the same business
surface (``scope: "self"``). The projection is display-only, so each scope must
ALSO be enforced at the interface — a page that reports ``available`` must not be
a hole, and a scope that the projection withholds must be refused by the handler.

The interface check matters more than usual here: the HTTP-method policy
processor classifies a route but deliberately does not duplicate handler
authorization, so a route flipped from ``closed`` to ``platform`` is only as
safe as the guard inside its handler.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

import config
from auth.service import IdentityService
from channel.web import web_channel

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

CHANNELS_PAGE = "admin.channels"


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _ScopeFixture(unittest.TestCase):
    """acme with a platform admin, a tenant-admin-only account and a member."""

    def setUp(self):
        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_key)

        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]

        self._mk_member("acmeadmin", "Acme Admin", ["tenant_admin"])
        self._mk_member("acmemember", "Acme Member", ["member"])

        self.token_root = self.svc.login("root", "Str0ngRootFinal").token
        self.token_admin = self.svc.login("acmeadmin", "MemPassFinal1").token
        self.token_member = self.svc.login("acmemember", "MemPassFinal1").token

    def _mk_member(self, username, display, roles):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username=username, display_name=display,
            temporary_password="MemTempPass1", roles=roles)
        token = self.svc.login(username, "MemTempPass1").token
        self.svc.change_password(token, "MemTempPass1", "MemPassFinal1")

    def _restore_key(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    def _page(self, token):
        return self.svc.context_for_tenant(token, self.ta)["console_pages"][CHANNELS_PAGE]


class ProjectionTests(_ScopeFixture):

    def test_a_platform_admin_sees_the_platform_scope(self):
        page = self._page(self.token_root)
        self.assertTrue(page["available"], page)
        self.assertEqual(page["scope"], "platform")

    def test_a_tenant_admin_sees_the_tenant_scope(self):
        page = self._page(self.token_admin)
        self.assertTrue(page["available"], page)
        self.assertEqual(page["scope"], "tenant")

    def test_a_plain_member_gets_the_same_page_as_their_own_surface(self):
        """One page, two relative scopes (task 6.1).

        The member is no longer told the channel page is closed for them: the
        tenant business interface answers them with their own connections, so the
        projection reports ``self`` and the object range — proven per request by
        ``auth.object_scope`` — is what limits them. A closed page in front of a
        working surface is the defect this change removes.
        """
        page = self._page(self.token_member)
        self.assertTrue(page["available"], page)
        self.assertTrue(page["read_allowed"], page)
        self.assertEqual(page["scope"], "self")
        self.assertTrue(page["actions"].get("create"), page)
        self.assertTrue(page["actions"].get("update"), page)

    def test_the_channel_page_is_no_longer_reported_as_closed(self):
        for token in (self.token_root, self.token_admin):
            page = self._page(token)
            self.assertNotEqual(page["reason"], "consumer_closed", page)

    def test_capability_report_agrees_with_the_page(self):
        """The consumer label and the page must not disagree (the original F6
        mismatch: ``channels`` reported available while the page said closed)."""
        for token in (self.token_root, self.token_admin):
            ctx = self.svc.context_for_tenant(token, self.ta)
            self.assertTrue(ctx["consumers"]["channels"]["available"])
            self.assertTrue(ctx["console_pages"][CHANNELS_PAGE]["available"])


class InstanceLevelInterfaceDenialTests(_ScopeFixture):
    """``/api/channels`` must refuse anyone who is not a platform admin.

    Exercised through the real app factory so the policy processor is in the
    path: classifying the route ``platform`` does not enforce it, the handler
    guard does.
    """

    def _patch_db(self):
        settings = {"identity_mode": "database", "identity_db_path": self.db}
        for target in (config, web_channel):
            p = patch.object(target, "conf", return_value=settings)
            p.start()
            self.addCleanup(p.stop)

    def _get(self, path, method="GET", token=None, payload=None):
        kwargs = {"method": method, "headers": {"Host": "test"}}
        if token:
            kwargs["headers"]["Cookie"] = f"cow_session={token}"
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        return web_channel.build_web_app().request(path, **kwargs)

    def test_a_platform_admin_reaches_the_instance_page(self):
        self._patch_db()
        resp = self._get("/api/channels", token=self.token_root)
        self.assertNotEqual(resp.status, "503 Service Unavailable", resp.data)
        self.assertNotEqual(resp.status, "403", resp.data)
        self.assertTrue(str(resp.status).startswith("200"), resp.data)

    def test_a_tenant_admin_is_refused_the_instance_page(self):
        """The tenant admin must not read the instance-level (global) config."""
        self._patch_db()
        resp = self._get("/api/channels", token=self.token_admin)
        self.assertTrue(str(resp.status).startswith("403"),
                        f"tenant admin reached the global channel config: {resp.status} {resp.data}")

    def test_a_plain_member_is_refused_the_instance_page(self):
        self._patch_db()
        resp = self._get("/api/channels", token=self.token_member)
        self.assertTrue(str(resp.status).startswith("403"),
                        f"member reached the global channel config: {resp.status} {resp.data}")

    def test_a_tenant_admin_cannot_write_the_instance_config(self):
        """The write path is the dangerous one: a save/connect would let a
        tenant admin repoint the *global* channel credentials."""
        self._patch_db()
        resp = self._get("/api/channels", method="POST", token=self.token_admin,
                         payload={"action": "save", "channel": "feishu",
                                  "config": {"feishu_app_id": "x"}})
        self.assertTrue(str(resp.status).startswith("403"),
                        f"tenant admin wrote the global channel config: {resp.status} {resp.data}")

    def test_a_plain_member_cannot_write_the_instance_config(self):
        self._patch_db()
        resp = self._get("/api/channels", method="POST", token=self.token_member,
                         payload={"action": "save", "channel": "feishu",
                                  "config": {"feishu_app_id": "x"}})
        self.assertTrue(str(resp.status).startswith("403"),
                        f"member wrote the global channel config: {resp.status} {resp.data}")


if __name__ == "__main__":
    unittest.main()
