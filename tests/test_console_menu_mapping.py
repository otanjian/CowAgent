# encoding:utf-8
"""Built-in menu defaults and the legacy personal → formal page migration.

Change ``enable-member-personal-console`` registered five personal console pages
(``personal.agents`` …) and gave the built-in ``member``/``tenant_admin`` roles
the matching ``nav:<page>`` grants. Change ``unify-console-by-data-scope`` retires
that menu: a member reaches agents, channels, memory, tools and skills through the
*same* console pages an administrator uses, and what those pages list is decided
by the caller's data scope.

So there are two contracts to pin, and a subtlety between them:

* **New tenants** register the *formal* pages (``BUILTIN_MENU_DEFAULTS``), never
  the personal ids. Menu grants are restrictive once a role has any, so the
  default set is still "the pages each role could already reach, plus the
  business pages it now shares with its administrators" — gating turns on with no
  page lost, no page widened.
* **Existing tenants** hold the legacy ids. ``_migration_26`` maps exactly the
  grants that are *present* onto the formal page each became, deletes the legacy
  id, bumps the role's version and audits what changed. It must not touch
  non-menu grants, resurrect a grant an administrator removed, batch-reset custom
  roles, or count one role twice when both ``personal.tools`` and
  ``personal.skills`` are held (the console has a single 工具与技能 page).

The zero-visibility regression at the bottom is the same one the earlier change
had: whatever a member could open before the defaults existed must still open
after mapping.

Task 8.8 closes the loop on the ids themselves: the five ``personal.*`` pages are
no longer *signed* by ``_SIGNED_CONSOLE_PAGES`` and no longer projected, so no
identity can be handed one. The vocabulary survives only as
``LEGACY_PERSONAL_MENU_MAP``, read by ``canonical_menu_id`` (grants, at request
time) and by ``_migration_26`` (rows, once). Both halves are asserted here,
because retiring the issuance must never retire the canonicalization.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import (
    BUILTIN_MENU_DEFAULTS,
    IdentityService,
    _SIGNED_CONSOLE_PAGES,
    canonical_menu_id,
)
from auth.policy import LEGACY_PERSONAL_MENU_MAP
from auth.store import _migration_20, _migration_26, migration_versions

PERSONAL_IDS = (
    "personal.agents",
    "personal.channels",
    "personal.memory",
    "personal.tools",
    "personal.skills",
)

#: The formal page each legacy personal page became.
FORMAL_PAGES = tuple(sorted(set(LEGACY_PERSONAL_MENU_MAP.values())))


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

    def _run(self, migration):
        with self.svc._store.connect() as con:
            migration(con)
            con.commit()

    def _grant(self, role_code, resource_id, *, kind="menu", action="view",
               grant_id=None):
        with self.svc._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                (grant_id or "g-%s-%s" % (role_code, resource_id), self.tenant,
                 self._role_id(role_code), kind, resource_id, action))
            con.commit()

    def _role_version(self, code):
        return [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == code][0]["version"]


class LegacyPageVocabularyTests(unittest.TestCase):
    """The legacy ids survive as *canonicalization vocabulary*, not as pages.

    Task 8.8 retired the issuance: ``_SIGNED_CONSOLE_PAGES`` no longer carries a
    single ``personal.*`` id, so the projection cannot sign, grant or serve one.
    What must survive is the *mapping* — ``LEGACY_PERSONAL_MENU_MAP`` is read by
    ``canonical_menu_id`` when grants are read and by ``_migration_26`` when they
    are rewritten, and both are what let an already-written
    ``nav:personal.agents`` row keep resolving to ``nav:admin.agents``.

    The address half (an old ``#view-personal-*`` bookmark still forwarding) is
    the console's own contract (task 8.1, ``tests/test_personal_address_forward_frontend.cjs``);
    it deliberately no longer needs the *server* to recognize the id, because the
    console resolves the address before it asks the projection about a page.
    """

    def test_the_five_legacy_pages_are_no_longer_signed(self):
        self.assertEqual(set(PERSONAL_IDS), set(LEGACY_PERSONAL_MENU_MAP))
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, _SIGNED_CONSOLE_PAGES, pid)

    def test_the_canonicalizer_maps_every_legacy_id_to_a_signed_page(self):
        for pid in PERSONAL_IDS:
            self.assertEqual(canonical_menu_id("nav:%s" % pid),
                             "nav:%s" % LEGACY_PERSONAL_MENU_MAP[pid], pid)
            self.assertIn(LEGACY_PERSONAL_MENU_MAP[pid], _SIGNED_CONSOLE_PAGES, pid)
            self.assertFalse(
                LEGACY_PERSONAL_MENU_MAP[pid].startswith("personal."), pid)

    def test_the_canonicalizer_leaves_every_other_id_alone(self):
        for raw in ("nav:admin.agents", "nav:workbench.chat", "builtin:echo", ""):
            self.assertEqual(canonical_menu_id(raw), raw, raw)

    def test_every_legacy_page_maps_to_a_signed_formal_page(self):
        for pid in PERSONAL_IDS:
            self.assertIn(pid, LEGACY_PERSONAL_MENU_MAP, pid)
            target = LEGACY_PERSONAL_MENU_MAP[pid]
            self.assertIn(target, _SIGNED_CONSOLE_PAGES, pid)
            self.assertFalse(target.startswith("personal."), pid)

    def test_tools_and_skills_share_one_formal_page(self):
        """The console has one 工具与技能 page: the mapping is many-to-one."""
        self.assertEqual(LEGACY_PERSONAL_MENU_MAP["personal.tools"],
                         LEGACY_PERSONAL_MENU_MAP["personal.skills"])


class NewTenantMenuDefaultsTests(_Fixture):
    """A freshly created tenant already carries the formal defaults."""

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

    def test_no_builtin_role_is_granted_a_personal_page(self):
        for code in ("member", "tenant_admin"):
            grants = self._menu_grants(code)
            for pid in PERSONAL_IDS:
                self.assertNotIn("nav:%s" % pid, grants, (code, pid))

    def test_the_formal_business_pages_are_granted_to_a_member(self):
        grants = self._menu_grants("member")
        for pid in FORMAL_PAGES:
            self.assertIn("nav:%s" % pid, grants, pid)

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


class LegacyPersonalMenuMappingTests(_Fixture):
    """``_migration_26`` maps a legacy role onto the formal pages."""

    def _seed_legacy_grants(self, pid="member", *, legacy=PERSONAL_IDS):
        """Give a role exactly the legacy personal grants (gating on, nothing else)."""
        self._clear_builtin_menu_grants()
        for item in legacy:
            self._grant(pid, "nav:%s" % item)

    def test_the_migration_is_registered(self):
        self.assertIn(26, migration_versions())
        self.assertEqual(migration_versions().count(26), 1)

    def test_it_replaces_the_legacy_ids_with_the_formal_pages(self):
        self._seed_legacy_grants()
        self._run(_migration_26)

        grants = self._menu_grants("member")
        for pid in PERSONAL_IDS:
            self.assertNotIn("nav:%s" % pid, grants, pid)
        for pid in FORMAL_PAGES:
            self.assertIn("nav:%s" % pid, grants, pid)

    def test_a_legacy_grant_opens_the_formal_page_without_the_migration(self):
        """The read-path half of the canonicalization invariant (task 8.8).

        ``_migration_26`` rewrites the rows that exist when it runs. A grant
        written *after* it — or restored from a backup, or held by a role the
        migration never touched — must behave identically, or a legacy id would
        be simultaneously too wide (it satisfies a retired page's own gate) and
        too narrow (it does not satisfy the formal page's). The projection
        canonicalizes when it reads grants, so the member reaches exactly the
        page the legacy grant used to stand for.
        """
        self._seed_legacy_grants()

        pages = self._pages(self.member_token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)
        for pid in FORMAL_PAGES:
            self.assertFalse(pages[pid].get("menu_denied", False), pid)
        self.assertTrue(pages["admin.agents"]["read_allowed"],
                        "a nav:personal.agents grant still reaches 智能体管理")

    def test_a_legacy_grant_still_stands_for_its_page_after_a_resign(self):
        """The same invariant on a fresh service instance (nothing cached)."""
        self._seed_legacy_grants()
        reopened = IdentityService(self.path)
        pages = reopened.context_for_tenant(
            self.member_token, self.tenant)["console_pages"]
        self.assertTrue(pages["admin.agents"]["read_allowed"])
        self.assertNotIn("personal.agents", pages)

    def test_the_same_migration_maps_a_tenant_admins_legacy_grants(self):
        self._seed_legacy_grants("tenant_admin")
        self._run(_migration_26)

        grants = self._menu_grants("tenant_admin")
        for pid in PERSONAL_IDS:
            self.assertNotIn("nav:%s" % pid, grants, pid)
        for pid in FORMAL_PAGES:
            self.assertIn("nav:%s" % pid, grants, pid)

    def test_it_bumps_the_role_version_and_audits_what_changed(self):
        self._seed_legacy_grants()
        before = self._role_version("member")
        self._run(_migration_26)

        self.assertEqual(self._role_version("member"), before + 1)
        events = self.svc._store.execute(
            "SELECT * FROM audit_events WHERE action='role.menu.legacy_personal_mapped'")
        self.assertEqual(len(events), 1, [dict(e) for e in events])
        self.assertIn("nav:admin.agents", events[0]["redacted_changes"])
        self.assertIn("nav:personal.agents", events[0]["redacted_changes"])

    def test_tools_and_skills_collapse_onto_one_grant(self):
        """Holding both is one page, not two — the console has one 工具与技能 page."""
        self._seed_legacy_grants(legacy=("personal.tools", "personal.skills"))
        self._run(_migration_26)

        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='menu' AND resource_id='nav:admin.skills'",
            (self._role_id("member"),))
        self.assertEqual(rows[0]["c"], 1)

    def test_it_is_idempotent(self):
        self._seed_legacy_grants()
        self._run(_migration_26)
        once = self._menu_grants("member")
        version = self._role_version("member")

        self._run(_migration_26)

        self.assertEqual(self._menu_grants("member"), once)
        self.assertEqual(self._role_version("member"), version,
                         "a re-run must not bump the version again")

    def test_it_does_not_resurrect_a_grant_that_is_absent(self):
        """Only ids actually present are mapped (an administrator's withdrawal
        must not be undone), so a role without any legacy id is untouched."""
        self._clear_builtin_menu_grants()
        self._grant("member", "nav:workbench.history")
        version = self._role_version("member")

        self._run(_migration_26)

        self.assertEqual(self._menu_grants("member"), {"nav:workbench.history"})
        self.assertEqual(self._role_version("member"), version)

    def test_it_leaves_non_menu_grants_untouched(self):
        self._seed_legacy_grants()
        self._grant("member", "builtin:echo", kind="tool", action="execute")

        self._run(_migration_26)

        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='tool'", (self._role_id("member"),))
        self.assertEqual(rows[0]["c"], 1)

    def test_it_does_not_touch_a_custom_role_without_legacy_grants(self):
        self.svc.create_role(
            self.root["id"], self.tenant, "custom2", "自定义", ["history.read"],
            resource_grants=[{"resource_kind": "menu",
                              "resource_id": "nav:workbench.chat",
                              "action": "view"}])

        self._run(_migration_26)

        role = [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == "custom2"][0]
        grants = {g["resource_id"] for g in role["resource_grants"]
                  if g["resource_kind"] == "menu"}
        self.assertEqual(grants, {"nav:workbench.chat"})

    def test_a_custom_role_with_legacy_grants_keeps_its_other_grants(self):
        role = self.svc.create_role(
            self.root["id"], self.tenant, "custom3", "自定义", ["history.read"],
            resource_grants=[{"resource_kind": "menu",
                              "resource_id": "nav:personal.memory",
                              "action": "view"},
                             {"resource_kind": "menu",
                              "resource_id": "nav:workbench.history",
                              "action": "view"}])
        self._run(_migration_26)

        role = [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == "custom3"][0]
        grants = {g["resource_id"] for g in role["resource_grants"]
                  if g["resource_kind"] == "menu"}
        self.assertEqual(grants, {"nav:admin.memory", "nav:workbench.history"})


class ZeroVisibilityRegressionTests(_Fixture):
    """Mapping reachability must not close a page a member could already open.

    The five personal pages are the exception, and deliberately so: retiring them
    is what this change is for (``test_a_member_no_longer_reaches_a_personal_page``).
    Everything else a member could reach before the defaults existed must still
    be reachable after them.
    """

    def test_a_member_keeps_every_page_it_could_already_open(self):
        self._clear_builtin_menu_grants()
        before = self._readable(self.member_token)

        self._run(_migration_20)
        after = self._readable(self.member_token)

        lost = (before - set(PERSONAL_IDS)) - after
        self.assertFalse(lost, "lost pages: %s" % sorted(lost))

    def test_the_formal_grants_are_what_keep_the_business_pages_open(self):
        """Once gating is on, a business page lives or dies by its grant.

        Some pages are *incidentally* readable while a role carries no menu grant
        at all (the compat rule), so "it is readable" alone proves nothing. The
        meaningful check is the transition: turn gating on without seeding the
        defaults, and the page is denied; seed them, and it returns.

        ``admin.agents`` is the page to use: the others answer to their own
        consumer's open state as well (the memory page's registry slice, the
        channels page's tenant control, the skills page's resource grant), which
        is exactly the separation this projection keeps — a menu grant never
        substitutes for the consumer's rules.
        """
        self._clear_builtin_menu_grants()
        self._grant("member", "nav:workbench.chat", grant_id="gate-only")
        self.assertNotIn("admin.agents", self._readable(self.member_token))

        self._run(_migration_20)

        self.assertIn("admin.agents", self._readable(self.member_token))

    def test_a_member_no_longer_reaches_a_personal_page(self):
        """The removal is the point: the account menu's five entries are gone."""
        self._clear_builtin_menu_grants()
        self._grant("member", "nav:workbench.chat", grant_id="gate-only")
        readable = self._readable(self.member_token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, readable, pid)

    def test_a_tenant_admin_keeps_its_management_pages(self):
        self._clear_builtin_menu_grants()
        before = self._readable(self.admin_token)

        self._run(_migration_20)

        lost = (before - set(PERSONAL_IDS)) - self._readable(self.admin_token)
        self.assertFalse(lost, "lost pages: %s" % sorted(lost))

    def test_a_granted_business_page_is_not_reported_as_menu_denied(self):
        pages = self._pages(self.member_token)
        for pid in ("admin.agents", "admin.memory", "admin.skills",
                    "admin.channels"):
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

    def test_removing_a_business_grant_denies_that_page(self):
        role = [r for r in self.svc.list_roles(self.tenant) if r["code"] == "member"][0]
        remaining = [g for g in role["resource_grants"]
                     if g["resource_id"] != "nav:admin.skills"]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"],
            role["permissions"], expected_version=role["version"],
            resource_grants=remaining)

        page = self._pages(self.member_token)["admin.skills"]

        self.assertFalse(page["read_allowed"])
        self.assertTrue(page.get("menu_denied"))

    def test_reopening_the_store_does_not_restore_a_removed_grant(self):
        role = [r for r in self.svc.list_roles(self.tenant) if r["code"] == "member"][0]
        remaining = [g for g in role["resource_grants"]
                     if g["resource_id"] != "nav:admin.skills"]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"],
            role["permissions"], expected_version=role["version"],
            resource_grants=remaining)

        reopened = IdentityService(self.path)

        grants = {r["resource_id"] for r in reopened._store.execute(
            "SELECT resource_id FROM role_resource_grants WHERE role_id=?"
            " AND resource_kind='menu'", (self._role_id("member"),))}
        self.assertNotIn("nav:admin.skills", grants)

    def test_removing_the_mapped_grant_leaves_the_legacy_id_ungranted(self):
        """After mapping, revoking the formal page must not revive the alias."""
        role = [r for r in self.svc.list_roles(self.tenant) if r["code"] == "member"][0]
        remaining = [g for g in role["resource_grants"]
                     if g["resource_id"] != "nav:admin.memory"]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"],
            role["permissions"], expected_version=role["version"],
            resource_grants=remaining)

        grants = self._menu_grants("member")
        self.assertNotIn("nav:admin.memory", grants)
        self.assertNotIn("nav:personal.memory", grants)
        self.assertFalse(self._pages(self.member_token)["admin.memory"]["read_allowed"])


if __name__ == "__main__":
    unittest.main()
