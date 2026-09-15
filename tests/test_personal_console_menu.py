# encoding:utf-8
"""The five personal console pages and their default menu grants.

Change ``enable-member-personal-console`` registers ``personal.agents``,
``personal.channels``, ``personal.memory``, ``personal.tools`` and
``personal.skills``, and gives the built-in ``member``/``tenant_admin`` roles the
matching ``nav:<page>`` menu grants — for new tenants and, via a one-time
migration, for existing ones.

The subtlety these tests pin down: menu grants are *restrictive* once a role has
any (``auth/service.py`` compat rule — a role with at least one ``menu`` grant is
bound to that set). Built-in roles had none, so they were never restricted.
Seeding only the five new pages would therefore have silently hidden every page a
member could already open (会话历史, 知识库, 我的待办, …). So the defaults record
the pages each built-in role could *already* reach, plus the five new personal
pages: the gating turns on with no page lost, no page widened.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import (
    BUILTIN_MENU_DEFAULTS,
    PERSONAL_CONSOLE_PAGES,
    IdentityService,
    _SIGNED_CONSOLE_PAGES,
)
from auth.store import _migration_20, migration_versions

PERSONAL_IDS = (
    "personal.agents",
    "personal.channels",
    "personal.memory",
    "personal.tools",
    "personal.skills",
)


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.path = _db_path()
        self.svc = IdentityService(self.path)
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

    def _readable(self, token):
        pages = self._pages(token)
        return {k for k, v in pages.items()
                if isinstance(v, dict) and v.get("read_allowed")}

    def _role_id(self, code):
        return [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == code][0]["id"]

    def _menu_grants(self, role_code):
        return {
            r["resource_id"] for r in self.svc._store.execute(
                "SELECT resource_id FROM role_resource_grants WHERE role_id=?"
                " AND resource_kind='menu'", (self._role_id(role_code),))
        }

    def _clear_builtin_menu_grants(self):
        """Reproduce a tenant created before menu defaults existed."""
        with self.svc._store.connect() as con:
            for code in ("member", "tenant_admin"):
                con.execute(
                    "DELETE FROM role_resource_grants WHERE resource_kind='menu'"
                    " AND role_id=?", (self._role_id(code),))
            con.commit()


class PersonalPageRegistrationTests(unittest.TestCase):
    """Design D1 fixes the five ids; they are signed pages with a self scope."""

    def test_the_five_personal_pages_are_registered(self):
        self.assertEqual(set(PERSONAL_CONSOLE_PAGES), set(PERSONAL_IDS))
        for pid in PERSONAL_IDS:
            self.assertIn(pid, _SIGNED_CONSOLE_PAGES, pid)

    def test_every_personal_page_is_self_scoped(self):
        for pid in PERSONAL_IDS:
            self.assertEqual(_SIGNED_CONSOLE_PAGES[pid]["scope"], "self",
                             "%s must not claim a tenant scope" % pid)

    def test_pages_that_have_a_member_read_permission_declare_it(self):
        expected = {
            "personal.agents": "agent.read",
            "personal.memory": "memory.read",
            "personal.tools": "tool.read",
            "personal.skills": "skill.read",
        }
        for pid, permission in expected.items():
            self.assertEqual(_SIGNED_CONSOLE_PAGES[pid]["permission"], permission)
        # Channels have no functional member permission yet: the personal
        # channel consumer gates that page, so it stays an empty permission
        # rather than borrowing an unrelated one.
        self.assertEqual(_SIGNED_CONSOLE_PAGES["personal.channels"]["permission"], "")


class NewTenantMenuDefaultsTests(_Fixture):
    """A freshly created tenant already carries the defaults."""

    def test_builtin_member_gets_the_documented_default_set(self):
        self.assertEqual(self._menu_grants("member"),
                         set(BUILTIN_MENU_DEFAULTS["member"]))

    def test_builtin_tenant_admin_gets_the_documented_default_set(self):
        self.assertEqual(self._menu_grants("tenant_admin"),
                         set(BUILTIN_MENU_DEFAULTS["tenant_admin"]))

    def test_every_default_is_a_registered_menu_page(self):
        for code, grants in BUILTIN_MENU_DEFAULTS.items():
            for grant in grants:
                self.assertTrue(grant.startswith("nav:"), (code, grant))
                self.assertIn(grant[len("nav:"):], _SIGNED_CONSOLE_PAGES, (code, grant))

    def test_all_five_personal_pages_are_granted_to_both_roles(self):
        for code in ("member", "tenant_admin"):
            grants = self._menu_grants(code)
            for pid in PERSONAL_IDS:
                self.assertIn("nav:%s" % pid, grants, (code, pid))

    def test_tenant_admin_defaults_superset_member_defaults(self):
        self.assertTrue(set(BUILTIN_MENU_DEFAULTS["member"])
                        <= set(BUILTIN_MENU_DEFAULTS["tenant_admin"]))

    def test_a_custom_role_is_not_granted_anything(self):
        role = self.svc.create_role(
            self.root["id"], self.tenant, "custom1", "自定义", ["history.read"])
        grants = self.svc._store.execute(
            "SELECT resource_id FROM role_resource_grants WHERE role_id=?",
            (role["id"],))
        self.assertEqual(list(grants), [])


class MenuBackfillMigrationTests(_Fixture):
    """The one-time backfill for tenants that predate the defaults."""

    def _run_migration(self):
        with self.svc._store.connect() as con:
            _migration_20(con)
            con.commit()
        return {}

    def test_the_migration_is_registered(self):
        # Not "the latest": later stages add migrations of their own, and pinning
        # this to the tail makes the test fail for reasons unrelated to menu
        # grants. What matters is that version 20 is in the chain, exactly once.
        self.assertIn(20, migration_versions())
        self.assertEqual(migration_versions().count(20), 1)

    def test_it_restores_the_defaults_for_a_legacy_tenant(self):
        self._clear_builtin_menu_grants()
        self.assertEqual(self._menu_grants("member"), set())

        self._run_migration()

        self.assertEqual(self._menu_grants("member"),
                         set(BUILTIN_MENU_DEFAULTS["member"]))
        self.assertEqual(self._menu_grants("tenant_admin"),
                         set(BUILTIN_MENU_DEFAULTS["tenant_admin"]))

    def test_running_it_again_changes_nothing(self):
        self._clear_builtin_menu_grants()
        self._run_migration()
        first = {code: self._menu_grants(code) for code in ("member", "tenant_admin")}

        self._run_migration()

        self.assertEqual({c: self._menu_grants(c) for c in first}, first)

    def test_it_does_not_duplicate_a_grant_that_already_exists(self):
        self._clear_builtin_menu_grants()
        with self.svc._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                ("pre-existing", self.tenant, self._role_id("member"), "menu",
                 "nav:workbench.history", "view"))
            con.commit()

        self._run_migration()

        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='menu' AND resource_id='nav:workbench.history'",
            (self._role_id("member"),))
        self.assertEqual(rows[0]["c"], 1)
        # The rest of the defaults still landed.
        self.assertIn("nav:personal.skills", self._menu_grants("member"))

    def test_it_leaves_non_menu_grants_untouched(self):
        with self.svc._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                ("tool-grant", self.tenant, self._role_id("member"), "tool",
                 "builtin:echo", "execute"))
            con.commit()

        self._run_migration()

        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='tool'", (self._role_id("member"),))
        self.assertEqual(rows[0]["c"], 1)

    def test_it_does_not_touch_custom_roles(self):
        self.svc.create_role(
            self.root["id"], self.tenant, "custom2", "自定义", ["history.read"],
            resource_grants=[{"resource_kind": "menu",
                              "resource_id": "nav:workbench.chat",
                              "action": "view"}])

        self._run_migration()

        role = [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == "custom2"][0]
        grants = {g["resource_id"] for g in role["resource_grants"]
                  if g["resource_kind"] == "menu"}
        self.assertEqual(grants, {"nav:workbench.chat"})


class ZeroVisibilityRegressionTests(_Fixture):
    """The whole point of migrating reachability: nothing a member could open
    before the defaults existed becomes closed."""

    def test_a_member_keeps_every_page_it_could_already_open(self):
        self._clear_builtin_menu_grants()
        before = self._readable(self.member_token)

        with self.svc._store.connect() as con:
            _migration_20(con)
            con.commit()
        after = self._readable(self.member_token)

        self.assertTrue(before <= after, "lost pages: %s" % (before - after))

    def test_the_default_grants_are_what_keeps_the_personal_pages_open(self):
        """Once gating is on, the personal pages live or die by their grant.

        The pages are also *incidentally* readable while a role carries no menu
        grant at all (the compat rule), so "it is readable" alone proves nothing.
        The meaningful check is the transition: turn gating on without seeding the
        defaults, and the personal pages are denied; seed them, and they return.
        """
        self._clear_builtin_menu_grants()
        with self.svc._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                ("gate-only", self.tenant, self._role_id("member"), "menu",
                 "nav:workbench.chat", "view"))
            con.commit()
        gated = self._readable(self.member_token)
        for pid in ("personal.agents", "personal.memory", "personal.tools",
                    "personal.skills"):
            self.assertNotIn(pid, gated, pid)

        with self.svc._store.connect() as con:
            _migration_20(con)
            con.commit()

        after = self._readable(self.member_token)
        for pid in ("personal.agents", "personal.memory", "personal.tools",
                    "personal.skills"):
            self.assertIn(pid, after, pid)

    def test_a_tenant_admin_keeps_its_management_pages(self):
        self._clear_builtin_menu_grants()
        before = self._readable(self.admin_token)

        with self.svc._store.connect() as con:
            _migration_20(con)
            con.commit()

        self.assertTrue(before <= self._readable(self.admin_token))

    def test_a_granted_personal_page_is_not_reported_as_menu_denied(self):
        pages = self._pages(self.member_token)
        for pid in ("personal.agents", "personal.memory", "personal.tools",
                    "personal.skills"):
            self.assertFalse(pages[pid].get("menu_denied", False), pid)

    def test_a_page_the_member_always_had_is_still_readable(self):
        pages = self._pages(self.member_token)
        for pid in ("workbench.history", "workbench.knowledge", "workbench.todos",
                    "workbench.agents"):
            self.assertTrue(pages[pid]["read_allowed"], (pid, pages[pid]))


class ExplicitEditAfterMigrationTests(_Fixture):
    """Once the defaults are recorded, an administrator's edit is authoritative.

    The migration must be a *one-time* classification, never a default that is
    re-asserted on every request — otherwise a deliberately removed page would
    reappear.
    """

    def test_removing_a_personal_grant_denies_that_page(self):
        role = [r for r in self.svc.list_roles(self.tenant) if r["code"] == "member"][0]
        remaining = [g for g in role["resource_grants"]
                     if g["resource_id"] != "nav:personal.skills"]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"],
            role["permissions"], expected_version=role["version"],
            resource_grants=remaining)

        page = self._pages(self.member_token)["personal.skills"]

        self.assertFalse(page["read_allowed"])
        self.assertTrue(page.get("menu_denied"))

    def test_reopening_the_store_does_not_restore_a_removed_grant(self):
        role = [r for r in self.svc.list_roles(self.tenant) if r["code"] == "member"][0]
        remaining = [g for g in role["resource_grants"]
                     if g["resource_id"] != "nav:personal.skills"]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"],
            role["permissions"], expected_version=role["version"],
            resource_grants=remaining)

        reopened = IdentityService(self.path)

        grants = {r["resource_id"] for r in reopened._store.execute(
            "SELECT resource_id FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='menu'", (self._role_id("member"),))}
        self.assertNotIn("nav:personal.skills", grants)


if __name__ == "__main__":
    unittest.main()
