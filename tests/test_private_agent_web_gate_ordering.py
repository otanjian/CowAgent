# encoding:utf-8
"""The web gates must place private ownership ahead of the admin shortcut.

``check_resource_action`` orders the two checks correctly (task 3.2), but the web
layer has its own fast paths — ``_tenant_admin_owns_agent`` returns early for any
administrator, and ``_knowledge_write_authorized`` writes "any data root" for an
admin. Those shortcuts, reached *before* the service, are exactly where a private
Agent could still be read or rewritten by an administrator. These tests pin the
ordering at that layer, since fixing only the service would leave the door open on
the paths the console actually calls.
"""

import json
import os
import tempfile
import unittest

from unittest.mock import patch

from auth.runtime import RequestContext
from auth.service import IdentityService
from channel.web import web_channel
import web


class _Fixture(unittest.TestCase):
    def setUp(self):
        web.ctx.headers = []
        web.ctx.status = "200 OK"
        self.svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.alice = self._member("alice")
        self.bob = self._member("bob")
        self.svc.bind_agent(tenant_id=self.ta, agent_id="alice-agent",
                            private_owner_user_id=self.alice,
                            origin="provisioned_assistant")
        self.svc.bind_agent(tenant_id=self.ta, agent_id="shared-agent")

    def _member(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]

    def _ctx(self, user_id, *, platform_admin=False, tenant_admin=False):
        perms = set(self.svc.permissions_for(user_id, self.ta))
        if platform_admin:
            perms |= {"agent.read", "agent.use", "agent.edit", "agent.enable"}
        return RequestContext(
            user_id=user_id, username="u", display_name="U",
            is_platform_admin=platform_admin, must_change_password=False,
            tenant_id=self.ta, membership=None, permissions=perms,
            is_tenant_admin=tenant_admin)


class PrivateOwnershipPrecedesAdminGateTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.patcher = patch("auth.service.get_identity_service",
                             return_value=self.svc)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _forbidden(self, fn):
        with self.assertRaises(web_channel.web.HTTPError) as exc:
            fn()
        return exc.exception.args[0]

    def test_a_platform_admin_may_not_edit_a_members_private_agent(self):
        ctx = self._ctx(self.root["id"], platform_admin=True)
        self.assertEqual(
            self._forbidden(lambda: web_channel._require_agent_action(
                ctx, "alice-agent", "edit", "agent.edit")),
            "403 Forbidden")

    def test_a_tenant_admin_may_not_edit_a_members_private_agent(self):
        ctx = self._ctx(self.root["id"], tenant_admin=True)
        self.assertEqual(
            self._forbidden(lambda: web_channel._require_agent_action(
                ctx, "alice-agent", "edit", "agent.edit")),
            "403 Forbidden")

    def test_an_administrator_may_still_edit_a_shared_agent(self):
        ctx = self._ctx(self.root["id"], platform_admin=True)
        web_channel._require_agent_action(ctx, "shared-agent", "edit", "agent.edit")

    def test_the_owner_still_may_edit_its_own_private_agent(self):
        ctx = self._ctx(self.alice)
        web_channel._require_agent_action(ctx, "alice-agent", "edit", "agent.edit")

    def test_another_member_may_not_edit_it(self):
        ctx = self._ctx(self.bob)
        self.assertEqual(
            self._forbidden(lambda: web_channel._require_agent_action(
                ctx, "alice-agent", "edit", "agent.edit")),
            "403 Forbidden")

    def test_a_platform_admin_may_not_write_a_members_private_knowledge(self):
        ctx = self._ctx(self.root["id"], platform_admin=True)
        self.assertFalse(
            web_channel._knowledge_write_authorized(ctx, "alice-agent"))

    def test_a_tenant_admin_may_not_write_a_members_private_knowledge(self):
        ctx = self._ctx(self.root["id"], tenant_admin=True)
        self.assertFalse(
            web_channel._knowledge_write_authorized(ctx, "alice-agent"))

    def test_an_administrator_may_write_shared_agent_knowledge(self):
        ctx = self._ctx(self.root["id"], platform_admin=True)
        self.assertTrue(web_channel._knowledge_write_authorized(ctx, "shared-agent"))

    def test_an_unknown_agent_id_is_not_treated_as_private(self):
        """The predicate narrows only; it never invents an owner."""
        ctx = self._ctx(self.root["id"], platform_admin=True)
        self.assertFalse(
            web_channel._private_agent_owned_by_another(ctx, "never-bound"))

    def test_a_cross_tenant_agent_id_is_not_matched(self):
        ctx = self._ctx(self.bob)
        self.assertFalse(
            web_channel._private_agent_owned_by_another(ctx, "other-tenant-agent"))

    def test_the_ordering_holds_without_the_service_permission_question(self):
        """The web gate is the one the console calls; it must not need the service."""
        ctx = self._ctx(self.root["id"], platform_admin=True)
        self.assertTrue(
            web_channel._private_agent_owned_by_another(ctx, "alice-agent"))
        self.assertFalse(
            web_channel._private_agent_owned_by_another(
                self._ctx(self.alice), "alice-agent"))


if __name__ == "__main__":
    unittest.main()
