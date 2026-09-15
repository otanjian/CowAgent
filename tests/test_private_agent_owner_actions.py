# encoding:utf-8
"""Owner actions on a private Agent, and the admin boundary around them.

Two tasks of change ``enable-member-personal-console``:

* **3.1** — the owner of a private Agent may *maintain* it (edit, enable/disable),
  not just read and launch it. The member role deliberately does not carry the
  tenant-wide maintenance permission ``agent.enable``, and it must not: adding it
  would let a member switch on any Agent they hold a grant for. So ownership is
  what authorises the owner here, narrowly, for their own object.
* **3.2** — the private-ownership refusal runs **before** the platform-admin
  bypass. "平台管理员" is not a reason to read a member's private Agent: an
  administrator's legitimate interest in a private object is governance metadata
  (that it exists, who owns it, whether to stop it), never its content.

Both are about *ordering*: the same checks existed, in the wrong order or in the
wrong set. These tests pin the order, because that is the whole change.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.policy import MEMBER_DEFAULT_PERMISSIONS
from auth.service import IdentityService, IdentityServiceError


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _PrivateAgentFixture(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_db_path())
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
        self.alice = self._member("alice")
        self.bob = self._member("bob")
        # A system-supplied private Agent (the member's personal assistant) and a
        # tenant-shared Agent, so "private" and "public" can be contrasted.
        self.private = "agent:personal-agent"
        self.svc.bind_agent(tenant_id=self.ta, agent_id="personal-agent",
                            private_owner_user_id=self.alice,
                            origin="provisioned_assistant")
        self.shared = "agent:shared-agent"
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

    def _grant(self, user_id, resource_id, actions, permissions=("agent.read",)):
        """Hand ``user_id`` an explicit grant for a *shared* resource."""
        import uuid
        role = self.svc.create_role(
            self.root["id"], self.ta, "grant-%s" % uuid.uuid4().hex[:10],
            "scoped", list(permissions),
            resource_grants=[{"resource_kind": "agent",
                              "resource_id": resource_id, "action": action}
                             for action in actions])
        membership = self.svc._membership(user_id, self.ta)
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                " VALUES(?,?,?)", (self.ta, membership["id"], role["id"]))
            con.commit()

    def _can(self, user_id, resource_id, action, *, permission=None):
        return self.svc.check_resource_action(
            user_id, self.ta, "agent", resource_id, action, permission=permission)


class OwnerActionTests(_PrivateAgentFixture):
    """3.1 — the owner maintains their own private Agent."""

    def test_the_member_role_does_not_carry_the_tenant_wide_enable_permission(self):
        # Guard against "fixing" 3.1 by widening the member role instead of
        # scoping the action to ownership.
        self.assertNotIn("agent.enable", MEMBER_DEFAULT_PERMISSIONS)

    def test_the_owner_may_read_and_use_its_own_private_agent(self):
        self.assertTrue(self._can(self.alice, self.private, "read"))
        self.assertTrue(self._can(self.alice, self.private, "use"))

    def test_the_owner_may_edit_its_own_private_agent_without_a_grant(self):
        self.assertTrue(self._can(self.alice, self.private, "edit"))

    def test_the_owner_may_enable_its_own_private_agent_without_the_permission(self):
        # ``agent.enable`` is a tenant-wide maintenance permission the member role
        # does not hold; ownership is what authorises this, for this object only.
        self.assertTrue(self._can(self.alice, self.private, "enable"))

    def test_the_owner_may_enable_with_the_permission_question_asked_explicitly(self):
        self.assertTrue(self._can(self.alice, self.private, "enable",
                                  permission="agent.enable"))

    def test_another_member_may_not_edit_or_enable_it(self):
        self.assertFalse(self._can(self.bob, self.private, "edit"))
        self.assertFalse(self._can(self.bob, self.private, "enable"))

    def test_another_member_may_not_read_it_either(self):
        self.assertFalse(self._can(self.bob, self.private, "read"))
        self.assertFalse(self._can(self.bob, self.private, "use"))

    def test_ownership_does_not_leak_into_shared_agents(self):
        """不对成员全局补发公共维护权限。"""
        self.assertFalse(self._can(self.alice, self.shared, "edit"))
        self.assertFalse(self._can(self.alice, self.shared, "enable"))

    def test_a_grant_still_governs_shared_agents(self):
        self._grant(self.bob, self.shared, ["edit"], permissions=["agent.read"])

        self.assertTrue(self._can(self.bob, self.shared, "edit"))
        self.assertFalse(self._can(self.bob, self.shared, "enable"))
        self.assertFalse(self._can(self.bob, self.private, "edit"))

    def test_the_unknown_agent_id_does_not_grant_anything(self):
        self.assertFalse(self._can(self.alice, "agent:nope", "edit"))
        self.assertFalse(self._can(self.alice, "agent:nope", "enable"))

    def test_the_catalog_lists_the_owners_private_agent_for_edit_and_enable(self):
        for action in ("edit", "enable"):
            ids = self.svc.resource_ids_for(self.alice, self.ta, "agent", action)
            self.assertIn(self.private, ids, action)

    def test_the_catalog_does_not_list_someone_elses_private_agent(self):
        for action in ("edit", "enable"):
            ids = self.svc.resource_ids_for(self.bob, self.ta, "agent", action)
            self.assertNotIn(self.private, ids, action)

    def test_the_catalog_does_not_hand_out_shared_agents_for_maintenance(self):
        for action in ("edit", "enable"):
            ids = self.svc.resource_ids_for(self.alice, self.ta, "agent", action)
            self.assertNotIn(self.shared, ids, action)

    def test_a_private_agent_is_not_maintainable_from_another_tenant(self):
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        tb = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]

        self.assertFalse(self.svc.check_resource_action(
            self.alice, tb, "agent", self.private, "edit"))


class PrivateContentPrecedesAdminBypassTests(_PrivateAgentFixture):
    """3.2 — the ownership refusal is evaluated before the ``all`` bypass."""

    def test_a_platform_admin_does_not_get_a_members_private_agent_content(self):
        for action in ("read", "use", "edit", "enable"):
            self.assertFalse(
                self._can(self.root["id"], self.private, action), action)

    def test_a_platform_admin_still_reaches_shared_agents(self):
        for action in ("read", "use", "edit", "enable"):
            self.assertTrue(self._can(self.root["id"], self.shared, action), action)

    def test_a_tenant_admin_does_not_get_a_members_private_agent_content(self):
        admin = self._tenant_admin("acmeadmin")
        self._grant(admin, self.shared, ["read", "use", "edit", "enable"],
                    permissions=["agent.read", "agent.use", "agent.edit"])

        for action in ("read", "use", "edit", "enable"):
            self.assertFalse(self._can(admin, self.private, action), action)

    def test_a_tenant_admin_still_reaches_shared_agents(self):
        admin = self._tenant_admin("acmeadmin2")
        self._grant(admin, self.shared, ["read", "use", "edit", "enable"],
                    permissions=["agent.read", "agent.use", "agent.edit"])

        for action in ("read", "use", "edit", "enable"):
            self.assertTrue(self._can(admin, self.shared, action), action)

    def test_the_bypass_is_untouched_for_other_resource_kinds(self):
        """Only Agent content moves behind ownership; nothing else widened."""
        self.assertTrue(self.svc.check_resource_action(
            self.root["id"], self.ta, "tool", "tool:anything", "execute"))
        self.assertTrue(self.svc.check_resource_action(
            self.root["id"], self.ta, "skill", "skill:anything", "edit"))

    def test_ownership_is_re_derived_so_a_release_takes_effect_immediately(self):
        self.assertTrue(self._can(self.alice, self.private, "edit"))

        # Releasing ownership (the audited "make tenant shared" path) must take
        # effect on the next check: nothing about ownership is memoized.
        self.svc.make_agent_tenant_shared(
            agent_id="personal-agent", actor_user_id=self.root["id"])

        self.assertFalse(self._can(self.alice, self.private, "edit"))
        # ...and once it is genuinely shared, the ordinary surface applies.
        self.assertTrue(self._can(self.root["id"], self.private, "edit"))

    def _tenant_admin(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["tenant_admin"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]


if __name__ == "__main__":
    unittest.main()
