# encoding:utf-8
"""Stage 8 acceptance matrix for the member personal console (task 8.6).

The page projection has unit tests (``test_personal_console_pages.py``), the
handlers have projection tests (``test_personal_console_web.py``) and the wire
has transport tests (``test_personal_console_transport.py``). This file covers
the six combinations task 8.6 names, because each one is a way the same feature
can go wrong at a *different* layer:

* **默认 member** — the shipped built-in role must open the shared business
  pages (``admin.agents`` / ``admin.channels`` / ``admin.memory`` /
  ``admin.skills``) with no extra grants; if it does not, the feature is
  invisible out of the box. The retired ``personal.*`` ids must no longer be the
  member's menu entries (change ``unify-console-by-data-scope``, task 2.4).
* **多个角色并集** — a member may hold several roles, and menu grants live in one
  while the functional permission lives in another. Only their union may decide
  the page, so a per-role pairing must not be required.
* **显式菜单限制** — once a role carries menu grants the console is bound to that
  set; a page outside it is denied from the payload, not merely hidden.
* **目录可读但执行关闭** — the page stays readable while its runtime consumer is
  closed, and the closed state is stated rather than omitted. The member's own
  connections are carried by ``admin.channels`` now (task 6.1), so the three
  separated states are read there rather than on the retired
  ``personal.channels``.
* **直接 API 越权** — a member who cannot use the page cannot reach the same
  capability by calling a public maintenance endpoint or another owner's object.
* **桌面/窄屏交互** — the layout behaviour is asserted in a real browser pass
  (``test_personal_console_browser.cjs``), which since the retirement
  (``unify-console-by-data-scope`` task 8.8) drives the *shared production page*
  and proves each retired ``#view-personal-*`` address forwards without ever
  fetching ``personal-console.js``.
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

#: The pages the built-in ``member`` role carries now (change
#: ``unify-console-by-data-scope``, task 2.4): the *same* console pages an
#: administrator uses. What each one lists is decided by the caller's data
#: scope, not by the page id, so there is no second workbench to assert.
MEMBER_PAGES = ("admin.agents", "admin.channels", "admin.memory", "admin.skills")

#: The object range each shared page answers a plain member with — the page's
#: own declared range, not the caller's role name. ``admin.agents`` /
#: ``admin.memory`` / ``admin.skills`` are Agent-scoped (their rosters narrow to
#: the caller's own objects), while ``admin.channels`` reports the member's own
#: connections (``tenant=T AND scope='user' AND owner=U``).
MEMBER_PAGE_SCOPES = {
    "admin.agents": "agent",
    "admin.channels": "self",
    "admin.memory": "agent",
    "admin.skills": "agent",
}

#: The retired personal pages (task 8.8). They are not issued any more — not
#: signed, not projected, not granted — so the assertions below are about their
#: *absence* together with the mapping that keeps a legacy grant resolving.
RETIRED_PERSONAL_IDS = ("personal.agents", "personal.channels", "personal.memory",
                        "personal.tools", "personal.skills")


def _menu(page_id):
    return {"resource_kind": "menu", "resource_id": "nav:" + page_id,
            "action": "view"}


class _Fixture(unittest.TestCase):
    """One tenant, a platform admin, and the roles each test needs."""

    #: Agents the harness's roster must contain.
    #:
    #: A personal channel instance may only route to a private Agent of its own
    #: owner, and that target is verified against the **Agent Registry** — the
    #: same roster the runtime would start a connection from, not merely the
    #: identity binding. A fixture that creates an instance therefore has to
    #: provide a roster; without one the create would be refused for a reason
    #: that has nothing to do with what the test is about, and with a *shared*
    #: target it would be exercising a shape production must reject.
    ROSTER_AGENTS = ("mem-plain", "mem-plain-2", "mem-plain-own",
                     "mem-plain-target", "target-alice", "target-carol")

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
            # The roster personal-target verification resolves against. Enabled
            # for every id, so "the target is switched off" stays a case a test
            # has to set up deliberately rather than inherit.
            "agents": [{"id": agent_id, "name": agent_id, "enabled": True}
                       for agent_id in self.ROSTER_AGENTS],
            "default_agent_id": self.ROSTER_AGENTS[0],
        }
        # Kept for subclasses that re-patch configuration (e.g. the capability
        # switch matrix in ``test_personal_capability_switches.py``), so they can
        # narrow one switch without restating the identity settings.
        self.settings = settings
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        # A registry may already have been resolved from a *previous* test's
        # settings (the process-wide instance is keyed on the settings it was
        # built from, so it is not simply replaced). Unpin it so this fixture's
        # roster is the one every lookup below sees.
        from agent.registry import set_agent_registry

        set_agent_registry(None)
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

    def _private_agent(self, user_id, agent_id, *, origin="user_created"):
        """Bind *agent_id* as a private Agent of *user_id* and return it.

        The order matters and mirrors the console's: the target has to exist and
        belong to the member *before* a channel instance can name it. A test that
        skips this would be creating a personal instance with a target the
        service is required to refuse.
        """
        self.service.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id=agent_id, user_id=user_id,
            origin=origin, actor_user_id=user_id)
        return agent_id

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
    """默认 member：出箱即可用，且共用正式业务页面而非退役的个人页面。"""

    def test_the_builtin_member_role_opens_the_shared_business_pages(self):
        _, token = self._member("plain", list(MEMBERS))
        pages = self._pages(token)
        for pid in MEMBER_PAGES:
            entry = pages[pid]
            self.assertTrue(entry["available"], pid)
            self.assertTrue(entry["read_allowed"], pid)
            self.assertFalse(entry.get("menu_denied"), pid)
            self.assertEqual(entry["scope"], MEMBER_PAGE_SCOPES[pid], pid)
        # The retired ids are *gone* from the payload, not merely withheld: the
        # contract change is proved by the absence plus the mapping, so there is
        # no entry left for a legacy client to render (task 8.8).
        for pid in RETIRED_PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)

    def test_the_builtin_member_menu_defaults_are_the_registered_pages(self):
        """The default table and the registry must not drift apart."""
        defaults = set(BUILTIN_MENU_DEFAULTS["member"])
        for pid in MEMBER_PAGES:
            self.assertIn("nav:" + pid, defaults, pid)
        # ``unify-console-by-data-scope`` retired the personal ids from the
        # default, not only from a migration: a freshly bootstrapped member must
        # never be handed one.
        for pid in RETIRED_PERSONAL_IDS:
            self.assertNotIn("nav:" + pid, defaults, pid)

    def test_the_builtin_member_role_opens_no_tenant_administration_page(self):
        """The shared business pages are not the tenant-administration ones.

        The member *does* open ``admin.*`` ids now — that is the point of the
        shared console — but 成员管理 / 角色权限 / 组织架构 need the
        tenant-administration qualification, which no object range substitutes
        for. Those stay closed and are reported as not granted.
        """
        _, token = self._member("plain2", list(MEMBERS))
        pages = self._pages(token)
        for pid in ("admin.members", "admin.roles", "admin.organization"):
            self.assertFalse(pages[pid]["available"], pid)
            self.assertNotEqual(pages[pid]["scope"], "self", pid)
            self.assertTrue(pages[pid].get("menu_denied"), pid)


class RoleUnionTests(_Fixture):
    """多个角色并集：页面由并集决定，不要求单个角色自洽。"""

    # The union is asserted on the pages that carry the surfaces now. A retired
    # ``personal.*`` id still *participates* in the union — it canonicalises to
    # the formal page it became (``canonical_menu_id``) — so the legacy spelling
    # is used deliberately in the first case to pin that path too.
    def test_menu_grants_from_two_roles_are_unioned(self):
        self._role("memory-only", ["memory.read"],
                   [_menu("personal.memory")])
        self._role("catalog-only", ["tool.read"],
                   [_menu("admin.skills")])
        _, token = self._member("union", ["memory-only", "catalog-only"])
        pages = self._pages(token)
        self.assertTrue(pages["admin.memory"]["available"])
        self.assertTrue(pages["admin.skills"]["available"])
        # Neither role lists the Agent page: the restrictive menu set is the
        # union, so an unlisted page is denied by the payload.
        self.assertTrue(pages["admin.agents"].get("menu_denied"))
        self.assertFalse(pages["admin.agents"]["available"])
        self.assertEqual(pages["admin.agents"]["actions"], {})

    def test_a_menu_grant_and_its_permission_may_come_from_different_roles(self):
        """The menu grant is in one role, ``skill.read`` in another.

        A per-role pairing (menu + permission in the *same* role) would deny
        this member. The union must be computed over menu grants and over
        functional permissions independently.
        """
        self._role("skills-menu", [], [_menu("admin.skills")])
        self._role("skills-read", ["skill.read"])
        _, token = self._member("split", ["skills-menu", "skills-read"])
        pages = self._pages(token)
        self.assertFalse(pages["admin.skills"].get("menu_denied"))
        self.assertTrue(pages["admin.skills"]["available"],
                        "the permission from one role must satisfy another role's menu grant")
        self.assertTrue(pages["admin.skills"]["read_allowed"])

    def test_a_menu_grant_alone_reports_the_missing_permission(self):
        """The negative control for the test above: menu without permission."""
        self._role("skills-menu-only", [], [_menu("admin.skills")])
        _, token = self._member("menuonly", ["skills-menu-only"])
        entry = self._pages(token)["admin.skills"]
        self.assertFalse(entry.get("menu_denied"), "the menu grant is present")
        self.assertFalse(entry["available"])
        self.assertEqual(entry["reason"], "no_permission")


class ExplicitMenuRestrictionTests(_Fixture):
    """显式菜单限制：带菜单授权的角色被绑定到该集合。"""

    def test_an_explicit_menu_set_binds_the_member_to_it(self):
        self._role("one-page", ["memory.read", "agent.read", "tool.read"],
                   [_menu("admin.memory")])
        _, token = self._member("restricted", ["one-page"])
        pages = self._pages(token)
        self.assertTrue(pages["admin.memory"]["available"])
        self.assertFalse(pages["admin.memory"].get("menu_denied"))
        for pid in ("admin.agents", "admin.skills"):
            entry = pages[pid]
            self.assertTrue(entry.get("menu_denied"), pid)
            self.assertEqual(entry["reason"], "menu_not_granted", pid)
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
        for pid in ("admin.memory", "admin.agents"):
            self.assertFalse(pages[pid].get("menu_denied"), pid)
            self.assertTrue(pages[pid]["available"], pid)


class LegacyPersonalMenuGrantTests(_Fixture):
    """A legacy ``nav:personal.*`` grant follows the map, in both directions.

    ``LEGACY_PERSONAL_MENU_MAP`` is what the migration applies to the rows that
    existed when it ran. A role created *after* it — or edited by hand through
    the API — can carry the same retired id, and then the map has not been
    consulted at all: the retired page opens (the surface tasks 3.3/3.4 removed)
    while the formal page it became stays menu-denied. So the grant is at once
    too wide and lost.

    Both halves are asserted, because fixing only one leaves the other: a grant
    that transfers but does not stop opening the alias still reopens the retired
    surface, and a grant that stops opening the alias without transferring simply
    takes a page away from the member.
    """

    def test_a_legacy_grant_reaches_the_formal_page_and_not_the_retired_one(self):
        self._role("legacy-id", ["agent.read"], [_menu("personal.agents")])
        _, token = self._member("legacyid", ["legacy-id"])
        pages = self._pages(token)

        self.assertFalse(pages["admin.agents"].get("menu_denied"),
                         "the retired id must resolve to the page it became")
        self.assertTrue(pages["admin.agents"]["available"],
                        "the grant must transfer, not be silently lost")

        self.assertNotIn("personal.agents", pages,
                         "the retired id must not be reissued by the mapping")

    def test_the_tools_and_skills_ids_both_resolve_to_the_one_catalogue_page(self):
        """The many-to-one case: one page carries both retired ids."""
        for legacy in ("personal.tools", "personal.skills"):
            self._role(legacy.replace(".", "-"), ["tool.read", "skill.read"],
                       [_menu(legacy)])
            _, token = self._member(legacy.replace(".", "-") + "-u",
                                    [legacy.replace(".", "-")])
            pages = self._pages(token)
            self.assertFalse(pages["admin.skills"].get("menu_denied"), legacy)
            self.assertTrue(pages["admin.skills"]["available"], legacy)


class ReadableCatalogClosedExecutionTests(_Fixture):
    """目录可读但执行关闭：读得到、说得出关闭，但不假装可运行。

    The member's own connections are carried by ``admin.channels`` — the same
    消息渠道 page an administrator uses, answered for the caller's own
    ``tenant=T AND scope='user' AND owner=U`` range (task 6.1). The three
    separated states moved with the surface, so the guarantee is proven on the
    page that now reports it rather than on the retired ``personal.channels``.
    """

    def test_the_channel_page_stays_readable_while_execution_is_closed(self):
        _, token = self._member("channelreader", list(MEMBERS))
        entry = self._pages(token)["admin.channels"]
        self.assertTrue(entry["read_allowed"])
        self.assertTrue(entry["states"]["read"])
        self.assertTrue(entry["states"]["config"])
        self.assertFalse(entry["states"]["execution"],
                         "no channel type has a recorded personal acceptance yet")
        # Readable over the *shared* interface, with the member's own (empty)
        # list rather than a refusal or another member's rows.
        body = self._body(self._request("/api/tenant/channels", token))
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
            entry = self._pages(token)["admin.channels"]
        self.assertTrue(entry["states"]["config"])
        self.assertTrue(entry["states"]["execution"])

    def test_the_accepted_type_alone_does_not_open_execution(self):
        """The negative control: a recorded type with the master withdrawn stays
        closed, so a rollback is one configuration change."""
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})):
            _, token = self._member("channelheld", list(MEMBERS))
            entry = self._pages(token)["admin.channels"]
        self.assertTrue(entry["states"]["read"], "the catalogue stays readable")
        self.assertFalse(entry["states"]["execution"])

    def test_an_empty_catalog_page_is_readable_and_says_so(self):
        """No ready channel type: the page still opens and *states* it closed.

        An empty catalogue must not read as an unavailable page, and the closed
        ``config``/``execution`` states must be stated rather than omitted — the
        same distinction the retired page drew.
        """
        _, token = self._member("catalogreader", list(MEMBERS))
        with patch("channel.channel_instances.PERSONAL_READY_CHANNEL_TYPES",
                   frozenset()):
            entry = self._pages(token)["admin.channels"]
            self.assertTrue(entry["read_allowed"])
            self.assertTrue(entry["states"]["read"])
            self.assertFalse(entry["states"]["config"])
            self.assertFalse(entry["states"]["execution"])

    def test_an_ungranted_resource_catalog_is_readable_but_empty(self):
        """``admin.skills`` succeeds the retired ``personal.tools`` /
        ``personal.skills`` pages.

        A member with no resource grant still reaches 工具与技能; what they see
        is their own empty catalogue — not a refusal, and not the tenant-wide
        list. The refusal side (a role that lacks the functional read
        permission) is the negative control in
        ``test_tenant_admin_skills_menu.InterfaceTests``.
        """
        _, token = self._member("skillreader", list(MEMBERS))
        entry = self._pages(token)["admin.skills"]
        self.assertTrue(entry["read_allowed"])
        self.assertTrue(entry["available"])
        self.assertEqual(entry["reason"], "")
        body = self._body(self._request("/api/tools", token))
        self.assertEqual(body["tools"], [],
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

        ``/api/tools`` is the page's own catalog read, gated on the functional
        ``tool.read`` (task 5.5 moved the personal-parameter verbs onto this same
        endpoint, so *this* is the wire the member's editor would use). Once the
        permission is dropped the request is refused rather than answered with a
        partial catalogue, and the retirement is asserted on the payload itself:
        the five ``personal.*`` ids are not in it (task 8.8).
        """
        role = [r for r in self.service.list_roles(self.tenant_id)
                if r["code"] == "member"][0]
        kept = [p for p in role["permissions"] if p != "tool.read"]
        self.service.update_role(self.root_user_id, self.tenant_id, role["id"],
                                 role["name"], kept,
                                 expected_version=role["version"])
        pages = self._pages(self.plain_token)
        for pid in RETIRED_PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)
        response = self._request("/api/tools", self.plain_token)
        self.assertEqual(response.status, "403 Forbidden",
                         "the catalogue must refuse rather than list a subset")
        self.assertNotIn("builtin:", response.data.decode("utf-8"),
                         "no resource id may survive in the refusal")

    def test_a_save_for_a_read_only_grant_is_refused(self):
        self._grant("member", "tool", "builtin:read", "read")
        response = self._request(
            "/api/tools", self.reader_token, method="POST",
            body={"action": "save-personal", "resource_id": "builtin:read",
                  "params": {"timeout": 9}})
        self.assertEqual(response.status, "403 Forbidden")
        rows = self.service._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs")
        self.assertEqual(rows[0]["c"], 0)

    def test_a_forged_owner_in_the_body_is_ignored(self):
        self._grant("member", "tool", "builtin:read", "execute")
        response = self._request(
            "/api/tools", self.reader_token, method="POST",
            body={"action": "save-personal", "resource_id": "builtin:read",
                  "params": {"timeout": 9}, "user_id": self.plain_id,
                  "owner_user_id": self.plain_id,
                  "actor_user_id": self.plain_id})
        self.assertIn(response.status, ("200", "200 OK"))
        rows = self.service._store.execute(
            "SELECT user_id FROM personal_resource_configs")
        self.assertEqual([r["user_id"] for r in rows], [self.reader_id])

    def test_another_members_personal_channel_is_not_reachable(self):
        """The instance id in the path is not an authorization."""
        target = self._private_agent(self.plain_id, "mem-plain-target")
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "acceptance-key"}):
            self.service.create_personal_channel_instance(
                actor_user_id=self.plain_id, tenant_id=self.tenant_id,
                channel_type="feishu", display_name="plain's",
                agent_id=target,
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
        target = self._private_agent(self.plain_id, "mem-plain-own")
        with patch.dict(os.environ, {"COW_CREDENTIAL_MASTER_KEY": "acceptance-key"}):
            self.service.create_personal_channel_instance(
                actor_user_id=self.plain_id, tenant_id=self.tenant_id,
                channel_type="feishu", display_name="before",
                agent_id=target,
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
