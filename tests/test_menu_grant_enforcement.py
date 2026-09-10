# encoding:utf-8
"""Menu resource grants must actually gate console page visibility.

Regression: a custom role with an explicit ``menu`` grant set that omitted
``nav:workbench.history`` still saw the session-history entry, because the
console-page projection only consulted the functional permission and the
frontend skipped all non-``admin.*`` pages. The compat rule under test:

* a member whose effective roles carry at least one explicit ``menu`` grant is
  restricted to that set (intersected with the functional read permission);
* a member whose roles carry **no** ``menu`` grant (built-in roles and legacy
  custom roles) keeps the functional-permission behaviour;
* platform ``all`` is never restricted by menu grants.
"""

import os
import tempfile
import unittest

from auth.service import IdentityService


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.svc = IdentityService(_mk_db())
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
        self.token_root = self.svc.login("root", "Str0ngRootFinal").token

    def _member(self, username, roles):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username=username, display_name=username,
            temporary_password="MemTempPass1", roles=roles)
        token = self.svc.login(username, "MemTempPass1").token
        self.svc.change_password(token, "MemTempPass1", "MemPassFinal1")
        return self.svc.login(username, "MemPassFinal1").token

    def _page(self, token, key):
        return self.svc.context_for_tenant(token, self.ta)["console_pages"][key]


class MenuGrantEnforcementTests(_Fixture):
    def test_unlisted_menu_is_not_readable_when_role_has_menu_grants(self):
        self.svc.create_role(
            self.root["id"], self.ta, "restricted", "受限角色",
            ["history.read", "knowledge.read", "agent.read"],
            resource_grants=[
                {"resource_kind": "menu",
                 "resource_id": "nav:workbench.knowledge", "action": "view"},
            ])
        token = self._member("restricteduser", ["restricted"])
        self.assertFalse(self._page(token, "workbench.history")["read_allowed"])
        self.assertTrue(self._page(token, "workbench.knowledge")["read_allowed"])

    def test_listed_menu_stays_readable(self):
        self.svc.create_role(
            self.root["id"], self.ta, "limited", "受限角色",
            ["history.read", "knowledge.read"],
            resource_grants=[
                {"resource_kind": "menu",
                 "resource_id": "nav:workbench.history", "action": "view"},
            ])
        token = self._member("limiteduser", ["limited"])
        self.assertTrue(self._page(token, "workbench.history")["read_allowed"])
        self.assertFalse(self._page(token, "workbench.knowledge")["read_allowed"])

    def test_menu_grants_also_gate_admin_pages(self):
        self.svc.create_role(
            self.root["id"], self.ta, "orgreader", "组织只读",
            ["tenant.members.read", "tenant.org.read"],
            resource_grants=[
                {"resource_kind": "menu",
                 "resource_id": "nav:admin.organization", "action": "view"},
            ])
        token = self._member("orgreaderuser", ["orgreader"])
        self.assertFalse(self._page(token, "admin.members")["read_allowed"])
        self.assertTrue(self._page(token, "admin.organization")["read_allowed"])

    def test_tenant_admin_keeps_org_perm_pages_despite_menu_grants(self):
        # A tenant admin's membership may also carry a custom role whose explicit
        # ``menu`` grants omit the current-tenant management pages. The built-in
        # tenant_admin qualification owns 组织与权限 unconditionally, so a menu
        # grant held by a *non-admin* role must not strip the admin surface the
        # qualification itself confers.
        self.svc.create_role(
            self.root["id"], self.ta, "restricted-nav", "受限导航",
            ["tenant.members.read", "tenant.org.read"],
            resource_grants=[
                {"resource_kind": "menu",
                 "resource_id": "nav:workbench.chat", "action": "view"},
            ])
        token = self._member("acmeadmin", ["tenant_admin", "restricted-nav"])
        for key in ("admin.members", "admin.roles", "admin.organization"):
            page = self._page(token, key)
            self.assertTrue(page["available"], (key, page))
            self.assertTrue(page["read_allowed"], (key, page))
            self.assertNotEqual(page.get("reason"), "menu_not_granted", (key, page))
            self.assertFalse(page.get("menu_denied", False), (key, page))

    def test_plain_member_without_org_perm_menu_grants_is_still_denied(self):
        # Regression guard: the tenant-admin exemption must not leak to an
        # ordinary member whose restrictive menu grant set omits these pages.
        self.svc.create_role(
            self.root["id"], self.ta, "restricted-nav", "受限导航",
            ["tenant.members.read", "tenant.org.read"],
            resource_grants=[
                {"resource_kind": "menu",
                 "resource_id": "nav:workbench.chat", "action": "view"},
            ])
        token = self._member("plainnav", ["restricted-nav"])
        for key in ("admin.members", "admin.roles", "admin.organization"):
            page = self._page(token, key)
            self.assertFalse(page["read_allowed"], (key, page))
            self.assertTrue(page.get("menu_denied", False), (key, page))

    def test_role_without_menu_grants_keeps_functional_behaviour(self):
        token = self._member("builtinuser", ["member"])
        self.assertTrue(self._page(token, "workbench.history")["read_allowed"])
        self.assertTrue(self._page(token, "workbench.knowledge")["read_allowed"])

    def test_platform_all_is_not_restricted_by_menu_grants(self):
        self.assertTrue(self._page(self.token_root, "workbench.history")["read_allowed"])
        self.assertTrue(self._page(self.token_root, "admin.members")["read_allowed"])


if __name__ == "__main__":
    unittest.main()
