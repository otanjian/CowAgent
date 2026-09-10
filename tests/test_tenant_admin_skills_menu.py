# encoding:utf-8
"""The built-in tenant admin gets 工具与技能 (``admin.skills``) by default.

The console page ``admin.skills`` is registered with an empty functional
permission, so it used to be invisible to everyone but a platform ``all``
account. A tenant admin qualifies as its own tenant's manager and must see the
page and read the tenant's skills/tools catalog without a per-resource grant
(read-only, mirroring the tenant-admin Agent exemption). A plain member still
needs the functional read permission *and* an explicit grant, and platform
``all`` stays unrestricted.

The projection is display-only, so the interface must agree: a page that reports
``read_allowed`` must not 403 at ``/api/skills`` / ``/api/tools``.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import web

import config
from auth.service import IdentityService
from channel.web import web_channel

SKILLS_PAGE = "admin.skills"


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
    """acme with a platform admin, a tenant admin and a plain member."""

    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(tempfile.mkdtemp(), "acme"))
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

    def _page(self, token):
        return self.svc.context_for_tenant(token, self.ta)["console_pages"][SKILLS_PAGE]


class ProjectionTests(_Fixture):

    def test_a_tenant_admin_sees_the_skills_page(self):
        page = self._page(self.token_admin)
        self.assertTrue(page["available"], page)
        self.assertTrue(page["read_allowed"], page)

    def test_a_platform_admin_sees_the_skills_page(self):
        page = self._page(self.token_root)
        self.assertTrue(page["available"], page)
        self.assertTrue(page["read_allowed"], page)

    def test_a_plain_member_gets_no_skills_administration(self):
        page = self._page(self.token_member)
        self.assertFalse(page["read_allowed"], page)
        self.assertFalse(page["available"], page)


class InterfaceTests(_Fixture):
    """The catalog interfaces must agree with the projection (no 403 hole)."""

    def _patch_db(self):
        settings = {"identity_mode": "database", "identity_db_path": self.db}
        for target in (config, web_channel):
            p = patch.object(target, "conf", return_value=settings)
            p.start()
            self.addCleanup(p.stop)

    def _get(self, path, token):
        return web_channel.build_web_app().request(
            path, method="GET",
            headers={"Host": "test", "Cookie": f"cow_session={token}",
                     "X-Tenant-ID": self.ta})

    def test_a_tenant_admin_can_list_skills(self):
        self._patch_db()
        resp = self._get("/api/skills", self.token_admin)
        self.assertTrue(str(resp.status).startswith("200"), resp.data[:300])
        self.assertEqual(json.loads(resp.data.decode("utf-8"))["status"], "success")

    def test_a_tenant_admin_can_list_tools(self):
        self._patch_db()
        resp = self._get("/api/tools", self.token_admin)
        self.assertTrue(str(resp.status).startswith("200"), resp.data[:300])
        data = json.loads(resp.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")
        # The built-in tool registry is loaded from source; the tenant admin
        # must see the catalog, not an empty grant-filtered list.
        self.assertTrue(data["tools"], data)

    def test_a_plain_member_is_refused_the_skills_catalog(self):
        self._patch_db()
        resp = self._get("/api/skills", self.token_member)
        self.assertTrue(str(resp.status).startswith("403"), resp.data[:300])

    def test_a_plain_member_is_refused_the_tools_catalog(self):
        self._patch_db()
        resp = self._get("/api/tools", self.token_member)
        self.assertTrue(str(resp.status).startswith("403"), resp.data[:300])

    def test_a_tenant_admin_may_browse_skill_content_read_only(self):
        """Read-only includes opening a skill, but never writing it."""
        self._patch_db()
        listing = self._get("/api/skills", self.token_admin)
        skills = json.loads(listing.data.decode("utf-8")).get("skills") or []
        if not skills:
            self.skipTest("no skills installed in this environment")
        name = skills[0]["name"]
        content = self._get(
            "/api/skills/content?name=" + quote(name), self.token_admin)
        self.assertTrue(str(content.status).startswith("200"), content.data[:300])

    def test_a_tenant_admin_still_cannot_write_a_skill(self):
        """The default grant is read-only: enable/disable needs its own grant."""
        self._patch_db()
        resp = web_channel.build_web_app().request(
            "/api/skills", method="POST",
            headers={"Host": "test", "Cookie": f"cow_session={self.token_admin}",
                     "X-Tenant-ID": self.ta},
            data=json.dumps({"action": "open", "name": "skill-creator"}))
        self.assertTrue(str(resp.status).startswith("403"), resp.data[:300])


if __name__ == "__main__":
    unittest.main()
