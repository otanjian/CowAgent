# encoding:utf-8
"""The five personal console pages as the server projects them (task 8.1).

`member-personal-console` asks for an *authoritative* page projection: the page
may be read, its configuration may be written, and its runtime consumer may be
open — three separate facts. Collapsing them is how a console ends up promising
a member that they can chat through a channel whose inbound path has never been
accepted, or hiding an already-open catalog because its execution is closed.

The pages are also *personal*: every one is ``self`` scoped, and the pre-existing
``admin.*`` ids keep their original meaning — a member's personal channel page
must never become a second way into tenant channel management.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.policy import PERSONAL_CONSOLE_PAGES
from auth.service import IdentityService

MEMBER_IDS = ("personal.agents", "personal.channels", "personal.memory",
              "personal.tools", "personal.skills")

#: Page-level action vocabulary (design D1: finite booleans, no inferred verbs).
ACTIONS = frozenset({"create", "update", "enable", "delete", "configure"})


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
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
        self.tenant = self.svc.list_tenants()[0]["id"]
        self.member_token = self._member("member1", ["member"])
        self.admin_token = self._member("admin1", ["tenant_admin"])

    def _member(self, username, roles):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=roles)
        token = self.svc.login(username, "MemTempPass1").token
        self.svc.change_password(token, "MemTempPass1", "MemPassFinal1")
        return self.svc.login(username, "MemPassFinal1").token

    def _pages(self, token):
        return self.svc.context_for_tenant(token, self.tenant)["console_pages"]

    def _role_id(self, code):
        return [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == code][0]["id"]

    def _grant(self, role_code, kind, resource_id, action, grant_id="g1"):
        with self.svc._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                (grant_id, self.tenant, self._role_id(role_code), kind,
                 resource_id, action))
            con.commit()

    def _drop_permission(self, role_code, permission):
        role = [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == role_code][0]
        kept = [p for p in role["permissions"] if p != permission]
        self.svc.update_role(self.root["id"], self.tenant, role["id"],
                             role["name"], kept, expected_version=role["version"])


class PersonalPageAvailabilityTests(_Fixture):
    def test_every_personal_page_is_available_and_self_scoped(self):
        pages = self._pages(self.member_token)
        for pid in MEMBER_IDS:
            entry = pages[pid]
            self.assertTrue(entry["available"], (pid, entry))
            self.assertTrue(entry["read_allowed"], (pid, entry))
            self.assertEqual(entry["scope"], "self", (pid, entry))

    def test_the_projection_covers_exactly_the_five_registered_pages(self):
        pages = self._pages(self.member_token)
        self.assertEqual(set(PERSONAL_CONSOLE_PAGES), set(MEMBER_IDS))
        for pid in MEMBER_IDS:
            self.assertIn(pid, pages, pid)


class PersonalPageStateTests(_Fixture):
    """Read / configure / execute are three answers, not one."""

    def test_the_three_states_are_reported_for_every_page(self):
        pages = self._pages(self.member_token)
        for pid in MEMBER_IDS:
            states = pages[pid].get("states")
            self.assertIsInstance(states, dict, pid)
            self.assertEqual(set(states), {"read", "config", "execution"}, pid)

    def test_a_member_may_read_and_configure_while_runtime_gaps_show(self):
        pages = self._pages(self.member_token)
        # Channels: the configuration slice is open, the inbound execution slice
        # is not (task 7.5 ships every type unaccepted), and the page says so
        # instead of promising a conversation.
        channels = pages["personal.channels"]
        self.assertEqual(channels["states"],
                         {"read": True, "config": True, "execution": False})
        for pid in ("personal.agents", "personal.memory"):
            self.assertTrue(pages[pid]["states"]["read"], pid)
            self.assertTrue(pages[pid]["states"]["config"], pid)
            self.assertTrue(pages[pid]["states"]["execution"], pid)

    def test_an_empty_catalog_is_readable_but_not_configurable(self):
        """The real state of "no tool or skill granted to me".

        The page has to stay readable (the member learns the catalog is empty)
        while reporting that there is nothing to configure or run: an ungranted
        catalog must not be dressed up as an open one.
        """
        pages = self._pages(self.member_token)
        for pid in ("personal.tools", "personal.skills"):
            states = pages[pid]["states"]
            self.assertTrue(states["read"], pid)
            self.assertFalse(states["config"], pid)
            self.assertFalse(states["execution"], pid)

    def test_a_granted_resource_opens_configure_and_execute(self):
        self._grant("member", "tool", "builtin:alpha", "execute")
        pages = self._pages(self.member_token)
        self.assertEqual(pages["personal.tools"]["states"],
                         {"read": True, "config": True, "execution": True})

    def test_a_read_grant_alone_does_not_open_configuration(self):
        """Reading the catalog is not using the resource.

        ``read`` on a tool says the member may see it; ``configure``/``execute``
        are separate grants. The page must keep the difference, or a read-only
        allocation would look like a working configuration.
        """
        self._grant("member", "tool", "builtin:alpha", "read")
        states = self._pages(self.member_token)["personal.tools"]["states"]
        self.assertTrue(states["read"])
        self.assertFalse(states["config"])
        self.assertFalse(states["execution"])

    def test_personal_channel_execution_follows_the_accepted_types(self):
        """Execution has two halves: the per-type acceptance *and* the master switch.

        ``PERSONAL_RUNTIME_ACCEPTED_TYPES`` alone is not enough (task 9.1): the
        deployment-wide ``personal_channel_runtime`` switch ships off because no
        type has a recorded real end-to-end acceptance yet, so a rollback is one
        configuration change. The negative control lives in
        ``test_personal_console_acceptance``.
        """
        import config as config_module
        import channel.channel_instances as channel_instances

        switches = dict(config_module.conf() or {})
        switches["personal_channel_runtime"] = True
        with patch.object(channel_instances, "PERSONAL_RUNTIME_ACCEPTED_TYPES",
                          frozenset({"feishu"})), \
                patch.object(config_module, "conf", return_value=switches):
            states = self._pages(self.member_token)["personal.channels"]["states"]
        self.assertTrue(states["execution"])

    def test_a_tenant_that_disabled_personal_agents_loses_creation(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            personal_enabled=False)
        entry = self._pages(self.member_token)["personal.agents"]
        self.assertTrue(entry["read_allowed"], "existing agents stay readable")
        self.assertFalse(entry["states"]["config"])
        self.assertFalse(entry["actions"]["create"])


class PersonalPageActionTests(_Fixture):
    """The page's verbs are finite and page-specific (design D1)."""

    def test_actions_are_a_subset_of_the_finite_vocabulary(self):
        pages = self._pages(self.member_token)
        for pid in MEMBER_IDS:
            actions = pages[pid].get("actions")
            self.assertIsInstance(actions, dict, pid)
            self.assertTrue(set(actions) <= ACTIONS, (pid, actions))
            for verb, allowed in actions.items():
                self.assertIsInstance(allowed, bool, (pid, verb))

    def test_owned_objects_offer_create_and_maintenance(self):
        pages = self._pages(self.member_token)
        self.assertEqual(pages["personal.agents"]["actions"],
                         {"create": True, "update": True, "enable": True})
        channels = pages["personal.channels"]["actions"]
        self.assertTrue(channels["create"])
        self.assertTrue(channels["update"])
        self.assertTrue(channels["enable"])
        self.assertNotIn("delete", channels,
                         "the personal channel API revokes and unlinks, it does not delete")

    def test_memory_offers_editing_not_creation(self):
        actions = self._pages(self.member_token)["personal.memory"]["actions"]
        self.assertTrue(actions["update"])
        self.assertTrue(actions["delete"])
        self.assertNotIn("create", actions)
        self.assertNotIn("enable", actions, "memory has no enable/disable state")

    def test_a_catalog_page_configures_only_what_it_may_use(self):
        pages = self._pages(self.member_token)
        for pid in ("personal.tools", "personal.skills"):
            self.assertFalse(pages[pid]["actions"]["configure"], pid)
        self._grant("member", "skill", "builtin:writer", "use")
        pages = self._pages(self.member_token)
        self.assertTrue(pages["personal.skills"]["actions"]["configure"])
        self.assertFalse(pages["personal.tools"]["actions"]["configure"],
                         "a skill grant must not open the tool page")


class PersonalPageDenialTests(_Fixture):
    def test_a_missing_functional_permission_denies_the_page(self):
        self._drop_permission("member", "tool.read")
        entry = self._pages(self.member_token)["personal.tools"]
        self.assertFalse(entry["read_allowed"])
        self.assertFalse(entry["available"])
        self.assertEqual(entry["reason"], "no_permission")
        self.assertEqual(entry["actions"], {})
        self.assertEqual(entry["states"],
                         {"read": False, "config": False, "execution": False})

    def test_a_withheld_menu_grant_clears_the_actions_and_states(self):
        """A page the menu withholds must not leak its capability flags.

        The client is told to hide the entry; if the same payload still said
        "create: true, config: true", a tampered client could render a usable
        page from the very response that denied it.
        """
        with self.svc._store.connect() as con:
            con.execute(
                "DELETE FROM role_resource_grants WHERE resource_kind='menu'"
                " AND role_id=?", (self._role_id("member"),))
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                ("gate-only", self.tenant, self._role_id("member"), "menu",
                 "nav:workbench.chat", "view"))
            con.commit()
        pages = self._pages(self.member_token)
        for pid in MEMBER_IDS:
            entry = pages[pid]
            self.assertTrue(entry.get("menu_denied"), pid)
            self.assertFalse(entry["available"], pid)
            self.assertEqual(entry["reason"], "menu_not_granted", pid)
            self.assertEqual(entry["actions"], {}, pid)
            self.assertEqual(entry["states"],
                             {"read": False, "config": False, "execution": False},
                             pid)

    def test_the_platform_admin_sees_every_personal_page(self):
        """Platform ``all`` is not menu-gated, but stays personally scoped."""
        self.svc.change_password(self.svc.login("root", "Str0ngRootFinal").token,
                                 "Str0ngRootFinal", "Str0ngRootFinal2")
        token = self.svc.login("root", "Str0ngRootFinal2").token
        pages = self._pages(token)
        for pid in MEMBER_IDS:
            self.assertTrue(pages[pid]["available"], pid)
            self.assertEqual(pages[pid]["scope"], "self", pid)


class ExistingAdminPageTests(_Fixture):
    """The personal branch must not quietly rewrite the management pages."""

    def test_the_admin_ids_keep_their_own_scope(self):
        pages = self._pages(self.admin_token)
        self.assertEqual(pages["admin.memory"]["scope"], "agent")
        self.assertIn(pages["admin.channels"]["scope"], ("tenant", "platform"))
        self.assertIn(pages["admin.skills"]["scope"], ("agent", "tenant"))
        for pid, entry in pages.items():
            if pid.startswith("admin."):
                self.assertNotEqual(entry.get("scope"), "self", pid)

    def test_a_personal_page_does_not_grant_the_management_page(self):
        pages = self._pages(self.member_token)
        self.assertTrue(pages["personal.channels"]["read_allowed"])
        self.assertFalse(pages["admin.channels"]["available"])
        self.assertTrue(pages["personal.memory"]["read_allowed"])
        self.assertFalse(pages["admin.memory"]["available"])

    def test_admin_pages_are_unaffected_by_the_personal_projection(self):
        """Same tenant, same identity: only the personal ids changed shape."""
        before = self._pages(self.admin_token)
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})):
            after = self._pages(self.admin_token)
        for pid, entry in before.items():
            if pid.startswith("admin."):
                if pid == "admin.channels":
                    continue  # channels reports live runtime state by design
                self.assertEqual(entry, after[pid], pid)


if __name__ == "__main__":
    unittest.main()
