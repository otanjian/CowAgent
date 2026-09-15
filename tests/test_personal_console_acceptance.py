# encoding:utf-8
"""Stage 8 acceptance matrix for the member personal console (task 8.6).

The page projection has unit tests (``test_personal_console_pages.py``), the
handlers have projection tests (``test_personal_console_web.py``) and the wire
has transport tests (``test_personal_console_transport.py``). This file covers
the six combinations task 8.6 names, because each one is a way the same feature
can go wrong at a *different* layer:

* **默认 member** — the shipped built-in role must open all five pages with no
  extra grants; if it does not, the feature is invisible out of the box.
* **多个角色并集** — a member may hold several roles, and menu grants live in one
  while the functional permission lives in another. Only their union may decide
  the page, so a per-role pairing must not be required.
* **显式菜单限制** — once a role carries menu grants the console is bound to that
  set; a page outside it is denied from the payload, not merely hidden.
* **目录可读但执行关闭** — the page stays readable while its runtime consumer is
  closed, and the closed state is stated rather than omitted.
* **直接 API 越权** — a member who cannot use the page cannot reach the same
  capability by calling a public maintenance endpoint or another owner's object.
* **桌面/窄屏交互** — the layout behaviour is asserted in the frontend contract
  (``test_personal_console_frontend.cjs``) and in a real browser pass
  (``test_personal_console_browser.cjs``).
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from auth.policy import BUILTIN_MENU_DEFAULTS
from auth.service import IdentityService
from channel.web import web_channel

TOOLS_BASE = "http://localhost:9899"
MEMBERS = ("member",)

PERSONAL_IDS = ("personal.agents", "personal.channels", "personal.memory",
                "personal.tools", "personal.skills")


def _menu(page_id):
    return {"resource_kind": "menu", "resource_id": "nav:" + page_id,
            "action": "view"}


class _Fixture(unittest.TestCase):
    """One tenant, a platform admin, and the roles each test needs."""

    def setUp(self):
        from channel.web import auth_handlers
        auth_handlers.reset_login_rate_limiter()
        temporary = tempfile.TemporaryDirectory(prefix="personal-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root_dir = temporary.name
        self.db_path = os.path.join(temporary.name, "identity.db")
        self.service = IdentityService(self.db_path)
        tenant = self.service.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(temporary.name, "acme"), allow_weak=True)
        self.tenant_id = tenant["id"]
        self.root_user_id = self.service.list_platform_users()[0]["id"]
        self.service.change_password(
            self.service.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root_token = self.service.login("root", "Str0ngRootFinal").token

        settings = {
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "agent_workspace": os.path.join(temporary.name, "acme"),
        }
        # Kept for subclasses that re-patch configuration (e.g. the capability
        # switch matrix in ``test_personal_capability_switches.py``), so they can
        # narrow one switch without restating the identity settings.
        self.settings = settings
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = web_channel.build_web_app()

    # -- fixtures ---------------------------------------------------------

    def _role(self, code, permissions, grants=()):
        return self.service.create_role(
            self.root_user_id, self.tenant_id, code, code, list(permissions),
            resource_grants=list(grants))

    def _member(self, username, roles):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            created = self.service.create_member(
                actor_user_id=self.root_user_id, tenant_id=self.tenant_id,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=roles)
        token = self.service.login(username, "MemTempPass1").token
        self.service.change_password(token, "MemTempPass1", "MemPassFinal1")
        return created["user_id"], self.service.login(username, "MemPassFinal1").token

    def _pages(self, token):
        return self.service.context_for_tenant(token, self.tenant_id)["console_pages"]

    def _headers(self, token, *, tenant=True, json_body=False):
        headers = {"Host": "localhost:9899", "Origin": TOOLS_BASE,
                   "Cookie": "cow_session=" + token}
        if tenant:
            headers["X-Tenant-ID"] = self.tenant_id
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, path, token, *, method="GET", body=None):
        return self.app.request(
            path, method=method,
            headers=self._headers(token, json_body=body is not None),
            data=json.dumps(body) if body is not None else None)

    def _body(self, response):
        return json.loads(response.data.decode("utf-8"))


class DefaultMemberPageTests(_Fixture):
    """默认 member：出箱即可用，且不因此获得管理页面。"""

    def test_the_builtin_member_role_opens_every_personal_page(self):
        _, token = self._member("plain", list(MEMBERS))
        pages = self._pages(token)
        for pid in PERSONAL_IDS:
            entry = pages[pid]
            self.assertTrue(entry["available"], pid)
            self.assertTrue(entry["read_allowed"], pid)
            self.assertFalse(entry.get("menu_denied"), pid)
            self.assertEqual(entry["scope"], "self", pid)

    def test_the_builtin_member_menu_defaults_are_the_registered_pages(self):
        """The default table and the registry must not drift apart."""
        defaults = set(BUILTIN_MENU_DEFAULTS["member"])
        for pid in PERSONAL_IDS:
            self.assertIn("nav:" + pid, defaults, pid)

    def test_the_builtin_member_role_opens_no_management_page(self):
        _, token = self._member("plain2", list(MEMBERS))
        pages = self._pages(token)
        for pid in ("admin.channels", "admin.members", "admin.roles",
                    "admin.skills"):
            if pid not in pages:
                continue
            self.assertFalse(pages[pid]["available"], pid)
            self.assertNotEqual(pages[pid]["scope"], "self", pid)


class RoleUnionTests(_Fixture):
    """多个角色并集：页面由并集决定，不要求单个角色自洽。"""

    def test_menu_grants_from_two_roles_are_unioned(self):
        self._role("memory-only", ["memory.read"],
                   [_menu("personal.memory")])
        self._role("tools-only", ["tool.read"],
                   [_menu("personal.tools")])
        _, token = self._member("union", ["memory-only", "tools-only"])
        pages = self._pages(token)
        self.assertTrue(pages["personal.memory"]["available"])
        self.assertTrue(pages["personal.tools"]["available"])
        # Neither role lists the Agent page: the restrictive menu set is the
        # union, so an unlisted page is denied by the payload.
        self.assertTrue(pages["personal.agents"].get("menu_denied"))
        self.assertFalse(pages["personal.agents"]["available"])
        self.assertEqual(pages["personal.agents"]["actions"], {})

    def test_a_menu_grant_and_its_permission_may_come_from_different_roles(self):
        """The menu grant is in one role, ``skill.read`` in another.

        A per-role pairing (menu + permission in the *same* role) would deny
        this member. The union must be computed over menu grants and over
        functional permissions independently.
        """
        self._role("skills-menu", [], [_menu("personal.skills")])
        self._role("skills-read", ["skill.read"])
        _, token = self._member("split", ["skills-menu", "skills-read"])
        pages = self._pages(token)
        self.assertFalse(pages["personal.skills"].get("menu_denied"))
        self.assertTrue(pages["personal.skills"]["available"],
                        "the permission from one role must satisfy another role's menu grant")
        self.assertTrue(pages["personal.skills"]["read_allowed"])

    def test_a_menu_grant_alone_reports_the_missing_permission(self):
        """The negative control for the test above: menu without permission."""
        self._role("skills-menu-only", [], [_menu("personal.skills")])
        _, token = self._member("menuonly", ["skills-menu-only"])
        entry = self._pages(token)["personal.skills"]
        self.assertFalse(entry.get("menu_denied"), "the menu grant is present")
        self.assertFalse(entry["available"])
        self.assertEqual(entry["reason"], "no_permission")


class ExplicitMenuRestrictionTests(_Fixture):
    """显式菜单限制：带菜单授权的角色被绑定到该集合。"""

    def test_an_explicit_menu_set_binds_the_member_to_it(self):
        self._role("one-page", ["memory.read", "agent.read", "tool.read"],
                   [_menu("personal.memory")])
        _, token = self._member("restricted", ["one-page"])
        pages = self._pages(token)
        self.assertTrue(pages["personal.memory"]["available"])
        self.assertFalse(pages["personal.memory"].get("menu_denied"))
        for pid in ("personal.agents", "personal.tools"):
            entry = pages[pid]
            self.assertTrue(entry.get("menu_denied"), pid)
            self.assertEqual(entry["reason"], "menu_not_granted", pid)
            self.assertEqual(entry["states"],
                             {"read": False, "config": False, "execution": False},
                             pid)
            self.assertEqual(entry["actions"], {}, pid)

    def test_a_role_without_menu_grants_stays_permission_governed(self):
        """Backwards compatibility: only a role that carries menu grants is bound.

        Every pre-existing custom role carries none, so its members must keep the
        pages their functional permissions already allowed instead of being
        narrowed by the new mechanism.
        """
        self._role("legacy", ["memory.read", "agent.read"])
        _, token = self._member("legacy", ["legacy"])
        pages = self._pages(token)
        for pid in ("personal.memory", "personal.agents"):
            self.assertFalse(pages[pid].get("menu_denied"), pid)
            self.assertTrue(pages[pid]["available"], pid)


class ReadableCatalogClosedExecutionTests(_Fixture):
    """目录可读但执行关闭：读得到、说得出关闭，但不假装可运行。"""

    def test_the_channel_page_stays_readable_while_execution_is_closed(self):
        _, token = self._member("channelreader", list(MEMBERS))
        entry = self._pages(token)["personal.channels"]
        self.assertTrue(entry["read_allowed"])
        self.assertTrue(entry["states"]["read"])
        self.assertTrue(entry["states"]["config"])
        self.assertFalse(entry["states"]["execution"],
                         "no channel type has a recorded personal acceptance yet")
        body = self._body(self._request("/api/personal/channels", token))
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["items"], [])

    def test_a_granted_channel_type_opens_execution(self):
        """Execution needs *both* halves of the gate since task 9.1: the per-type
        acceptance and the deployment-wide master switch."""
        switches = dict(self.settings)
        switches["personal_channel_runtime"] = True
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})), \
                patch.object(config, "conf", return_value=switches):
            _, token = self._member("channelopen", list(MEMBERS))
            entry = self._pages(token)["personal.channels"]
        self.assertTrue(entry["states"]["execution"])

    def test_the_accepted_type_alone_does_not_open_execution(self):
        """The negative control: a recorded type with the master withdrawn stays
        closed, so a rollback is one configuration change."""
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})):
            _, token = self._member("channelheld", list(MEMBERS))
            entry = self._pages(token)["personal.channels"]
        self.assertTrue(entry["states"]["read"], "the catalogue stays readable")
        self.assertFalse(entry["states"]["execution"])

    def test_an_empty_catalog_page_is_readable_and_says_so(self):
        _, token = self._member("catalogreader", list(MEMBERS))
        for pid in ("personal.tools", "personal.skills"):
            entry = self._pages(token)[pid]
            self.assertTrue(entry["read_allowed"], pid)
            self.assertFalse(entry["states"]["config"], pid)
            self.assertFalse(entry["states"]["execution"], pid)
        body = self._body(self._request("/api/personal/resources?kind=tool", token))
        self.assertEqual(body["resources"], [],
                         "an ungranted catalog is empty, not fabricated")


class DirectApiEscalationTests(_Fixture):
    """直接 API 越权：页面被拒时，绕过页面也无法获得能力。"""

    def setUp(self):
        super().setUp()
        self.reader_id, self.reader_token = self._member("reader", list(MEMBERS))
        self.plain_id, self.plain_token = self._member("plain", list(MEMBERS))

    def _grant(self, role_code, kind, resource_id, action):
        role = [r for r in self.service.list_roles(self.tenant_id)
                if r["code"] == role_code][0]
        with self.service._store.connect() as con:
            con.execute(
                "INSERT INTO role_resource_grants(id, tenant_id, role_id,"
                " resource_kind, resource_id, action) VALUES(?,?,?,?,?,?)",
                ("g-" + role_code + "-" + action, self.tenant_id, role["id"],
                 kind, resource_id, action))
            con.commit()

    def test_the_public_channel_api_refuses_a_member(self):
        """A member page must not become a second door into tenant channels."""
        for method, body in (("GET", None),
                             ("POST", {"channel_type": "feishu",
                                       "display_name": "mine"})):
            response = self._request("/api/channels", self.plain_token,
                                     method=method, body=body)
            self.assertNotIn(response.status, ("200", "200 OK"), method)

    def test_a_dropped_permission_denies_the_page_and_leaks_no_row(self):
        """The page is denied at the projection; the wire leaks nothing.

        ``/api/personal/resources`` is a session-scoped self surface, so it does
        not answer 403 on a missing functional permission — but it must not
        answer with anything either: the service filters by the caller's current
        grants, so the response is an empty catalog while the page itself is
        denied by ``console_pages``. Neither path opens the capability.
        """
        role = [r for r in self.service.list_roles(self.tenant_id)
                if r["code"] == "member"][0]
        kept = [p for p in role["permissions"] if p != "tool.read"]
        self.service.update_role(self.root_user_id, self.tenant_id, role["id"],
                                 role["name"], kept,
                                 expected_version=role["version"])
        page = self._pages(self.plain_token)["personal.tools"]
        self.assertFalse(page["read_allowed"])
        self.assertFalse(page["available"])
        self.assertEqual(page["reason"], "no_permission")
        body = self._body(self._request("/api/personal/resources",
                                        self.plain_token))
        self.assertEqual(body["resources"], [])

    def test_a_save_for_a_read_only_grant_is_refused(self):
        self._grant("member", "tool", "builtin:echo", "read")
        response = self._request(
            "/api/personal/resources", self.reader_token, method="POST",
            body={"resource_kind": "tool", "resource_id": "builtin:echo",
                  "params": {"timeout": 9}})
        self.assertEqual(response.status, "403 Forbidden")
        rows = self.service._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs")
        self.assertEqual(rows[0]["c"], 0)

    def test_a_forged_owner_in_the_body_is_ignored(self):
        self._grant("member", "tool", "builtin:echo", "execute")
        response = self._request(
            "/api/personal/resources", self.reader_token, method="POST",
            body={"resource_kind": "tool", "resource_id": "builtin:echo",
                  "params": {"timeout": 9}, "user_id": self.plain_id,
                  "owner_user_id": self.plain_id,
                  "actor_user_id": self.plain_id})
        self.assertIn(response.status, ("200", "200 OK"))
        rows = self.service._store.execute(
            "SELECT user_id FROM personal_resource_configs")
        self.assertEqual([r["user_id"] for r in rows], [self.reader_id])

    def test_another_members_personal_channel_is_not_reachable(self):
        """The instance id in the path is not an authorization."""
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "acceptance-key"}):
            self.service.create_personal_channel_instance(
                actor_user_id=self.plain_id, tenant_id=self.tenant_id,
                channel_type="feishu", display_name="plain's",
                credentials={"feishu_app_id": "cli_fixture",
                             "feishu_app_secret": "fixture-secret",
                             "feishu_token": "fixture-token",
                             "feishu_bot_name": "fixture-bot"},
                recent_password="MemPassFinal1")
        instance_id = self.service.list_personal_channel_instances(
            actor_user_id=self.plain_id, tenant_id=self.tenant_id)["items"][0]["id"]
        instance = self.service.list_personal_channel_instances(
            actor_user_id=self.plain_id, tenant_id=self.tenant_id)["items"][0]
        response = self._request(
            "/api/personal/channels/" + instance_id, self.reader_token,
            method="POST", body={"action": "update", "display_name": "stolen",
                                 "expected_version": instance["version"],
                                 "recent_password": "MemPassFinal1"})
        # The service answers 403 on a foreign id, and the body must not claim
        # success either way.
        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertNotEqual(self._body(response).get("status"), "success")
        owned = self.service.list_personal_channel_instances(
            actor_user_id=self.plain_id, tenant_id=self.tenant_id)["items"][0]
        self.assertEqual(owned["display_name"], "plain's")

    def test_the_owner_can_edit_their_own_personal_channel(self):
        """The positive control for the refusal above, over the same endpoint."""
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "acceptance-key"}):
            self.service.create_personal_channel_instance(
                actor_user_id=self.plain_id, tenant_id=self.tenant_id,
                channel_type="feishu", display_name="before",
                credentials={"feishu_app_id": "cli_own",
                             "feishu_app_secret": "fixture-secret",
                             "feishu_token": "fixture-token",
                             "feishu_bot_name": "fixture-bot"},
                recent_password="MemPassFinal1")
        instance = self.service.list_personal_channel_instances(
            actor_user_id=self.plain_id, tenant_id=self.tenant_id)["items"][0]
        response = self._request(
            "/api/personal/channels/" + instance["id"], self.plain_token,
            method="POST", body={"action": "update", "display_name": "after",
                                 "expected_version": instance["version"],
                                 "recent_password": "MemPassFinal1"})
        self.assertIn(response.status, ("200", "200 OK"))
        self.assertEqual(self._body(response)["status"], "success")
        self.assertEqual(
            self._body(response)["instance"]["display_name"], "after")

    def test_a_member_cannot_delete_another_members_private_agent(self):
        self.service.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id="mem-plain", user_id=self.plain_id,
            origin="user_created", actor_user_id=self.plain_id)
        response = self._request("/api/agents", self.reader_token,
                                 method="POST",
                                 body={"action": "delete", "id": "mem-plain"})
        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertIsNotNone(self.service.get_agent_binding("mem-plain"),
                             "a foreign owner must not be able to delete the object")

    def test_the_agent_view_never_returns_another_members_agents(self):
        self.service.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id="mem-plain-2", user_id=self.plain_id,
            origin="user_created", actor_user_id=self.plain_id)
        with patch.object(web_channel, "_tenant_agents_admin_projection",
                          return_value={"agents": [{"id": "mem-plain-2",
                                                    "name": "plain's"}],
                                        "default_agent_id": ""}):
            body = self._body(self._request("/api/agents?view=personal",
                                            self.reader_token))
        self.assertEqual(body["agents"], [])


if __name__ == "__main__":
    unittest.main()
