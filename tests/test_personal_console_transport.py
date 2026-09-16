# encoding:utf-8
"""Request-level transport for the member personal console surfaces.

The unit tests (``tests/test_personal_console_web.py``) pin the projections and
the owner scoping inside the handlers. These tests drive the real web app, so
they additionally pin what the *wire* enforces (task 8.4/8.6):

* the new surfaces answer only to an authenticated session with a tenant
  selected — no session, no tenant, or a foreign tenant is refused before any
  handler runs;
* a member's list is filtered by their own grants, and a save for an ungranted
  resource is refused by the service, not silently accepted by the page;
* one member never observes another member's configuration.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from auth.service import IdentityService
from channel.web import web_channel
from tests._helpers import cookie_value as _cookie_value

TOOLS_BASE = "http://localhost:9899"


class PersonalConsoleTransportTests(unittest.TestCase):
    """The personal-parameter verbs on the shared page, and ``/api/agents?view=personal``."""

    def setUp(self):
        from channel.web import auth_handlers
        auth_handlers.reset_login_rate_limiter()
        temporary = tempfile.TemporaryDirectory(prefix="personal-console-")
        self.addCleanup(temporary.cleanup)
        self.root_dir = temporary.name
        self.db_path = os.path.join(temporary.name, "identity.db")
        self.service = IdentityService(self.db_path)
        tenant = self.service.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(temporary.name, "acme"), allow_weak=True,
        )
        self.tenant_id = tenant["id"]
        self.admin_id = self.service.list_platform_users()[0]["id"]
        self.service.change_password(
            self.service.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")

        self.service.create_role(
            self.admin_id, self.tenant_id, "personalizer", "Personalizer",
            ["agent.read", "tool.read", "skill.read"],
            resource_grants=[
                # The console's grant picker emits the kind's whole action set
                # (``identity-admin.js``: tool = read/execute/configure), so a
                # granted tool carries ``read`` as well as ``execute``. The read
                # is what puts the row on the shared 工具与技能 page; the
                # ``execute`` is what the owner-scoped personal write answers to.
                {"resource_kind": "tool", "resource_id": "builtin:read",
                 "action": "read"},
                {"resource_kind": "tool", "resource_id": "builtin:read",
                 "action": "execute"},
                {"resource_kind": "skill", "resource_id": "custom:writer",
                 "action": "read"},
                {"resource_kind": "skill", "resource_id": "custom:writer",
                 "action": "use"},
            ])
        self.alice_id = self._member("alice", ["personalizer"])
        self.carol_id = self._member("carol", ["personalizer"])
        self.limited_role = self.service.create_role(
            self.admin_id, self.tenant_id, "limited", "Limited", ["history.read"])
        self.bob_id = self._member("bob", ["limited"])
        self.alice_token = self._login("alice", "MemPassFinal1")
        self.carol_token = self._login("carol", "MemPassFinal1")
        self.bob_token = self._login("bob", "MemPassFinal1")

        settings = {
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "agent_workspace": os.path.join(temporary.name, "acme"),
        }
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = web_channel.build_web_app()

    # -- helpers ---------------------------------------------------------

    def _member(self, username, roles):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            created = self.service.create_member(
                actor_user_id=self.admin_id, tenant_id=self.tenant_id,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=roles)
        token = self.service.login(username, "MemTempPass1").token
        self.service.change_password(token, "MemTempPass1", "MemPassFinal1")
        return created["user_id"]

    def _login(self, username, password):
        token = self.service.login(username, password).token
        return token

    def _headers(self, token, *, tenant=True, origin=TOOLS_BASE, json_body=False):
        headers = {"Host": "localhost:9899", "Origin": origin,
                   "Cookie": "cow_session=" + token}
        if tenant:
            headers["X-Tenant-ID"] = self.tenant_id
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _get_tools(self, token, *, tenant=True):
        return self.app.request("/api/tools",
                                headers=self._headers(token, tenant=tenant))

    def _post_tools(self, token, body, *, tenant=True):
        return self.app.request(
            "/api/tools", method="POST",
            headers=self._headers(token, tenant=tenant, json_body=True),
            data=json.dumps(body))

    def _tool_row(self, token, resource_id="builtin:read"):
        rows = self._body(self._get_tools(token))["tools"]
        return [r for r in rows if r["resource_id"] == resource_id][0]

    def _get_agents(self, token, *, view="personal", tenant=True):
        return self.app.request(
            "/api/agents?view=" + view,
            headers=self._headers(token, tenant=tenant))

    def _body(self, response):
        return json.loads(response.data.decode("utf-8"))

    # -- the personal parameters on the shared 工具与技能 page ---------------

    def test_a_granted_member_gets_the_personal_state_on_the_row(self):
        """The detail component reads its panel from the row, not a second call."""
        row = self._tool_row(self.alice_token)

        self.assertEqual(row["resource_id"], "builtin:read")
        self.assertEqual(row["personal"]["resource_kind"], "tool")
        self.assertFalse(row["personal"]["configured"])
        self.assertEqual(row["personal"]["params"], {})
        self.assertTrue(row["personal"]["actions"]["configure"],
                        "a granted resource offers its owner the editor")
        self.assertFalse(row["personal"]["actions"]["clear"],
                         "nothing is saved yet, so there is nothing to clear")

    def test_the_catalog_shows_no_row_the_member_may_not_read(self):
        response = self._get_tools(self.alice_token)

        ids = {row["resource_id"] for row in self._body(response)["tools"]}
        self.assertEqual(ids, {"builtin:read"},
                         "the shared page is read-filtered; one grant, one row")

    def test_a_member_without_the_read_permission_is_refused_rather_than_shown_an_empty_page(self):
        """Control case: the page says no instead of pretending the catalog is empty."""
        response = self._get_tools(self.bob_token)

        self.assertEqual(response.status, "403 Forbidden")

    def test_saving_parameters_round_trips_through_the_page_endpoint(self):
        saved = self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:read",
            "params": {"timeout": 12}})
        self.assertIn(saved.status, ("200", "200 OK"))

        personal = self._tool_row(self.alice_token)["personal"]
        self.assertTrue(personal["configured"])
        self.assertEqual(personal["params"], {"timeout": 12})
        self.assertTrue(personal["actions"]["clear"])

    def test_a_save_lands_on_the_callers_own_row_only(self):
        self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:read",
            "params": {"timeout": 12}})

        rows = self.service._store.execute(
            "SELECT user_id FROM personal_resource_configs")
        self.assertEqual([r["user_id"] for r in rows], [self.alice_id])

    def test_another_member_never_sees_the_saved_configuration(self):
        """Carol holds the same grant: same row, her own (empty) configuration."""
        self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:read",
            "params": {"timeout": 12}})

        other = self._tool_row(self.carol_token)["personal"]
        self.assertFalse(other["configured"])
        self.assertEqual(other["params"], {})

    def test_saving_an_ungranted_resource_is_refused(self):
        response = self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:rm",
            "params": {}})

        self.assertEqual(response.status, "403 Forbidden")
        self.assertEqual(self._body(response)["code"], "forbidden")
        rows = self.service._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs")
        self.assertEqual(rows[0]["c"], 0)

    def test_clearing_is_idempotent_over_the_wire(self):
        self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:read",
            "params": {"timeout": 12}})

        cleared = self._post_tools(self.alice_token, {
            "action": "clear-personal", "resource_id": "builtin:read"})
        again = self._post_tools(self.alice_token, {
            "action": "clear-personal", "resource_id": "builtin:read"})

        self.assertIn(cleared.status, ("200", "200 OK"))
        self.assertIn(again.status, ("200", "200 OK"))
        self.assertFalse(self._tool_row(self.alice_token)["personal"]["configured"])

    def test_the_public_definition_fields_are_untouched_by_a_personal_save(self):
        """A personal write may not become a tool-definition write.

        The same method carries the management verbs, so the boundary is asserted
        on the effect rather than on the verb name: the catalog row's own fields
        (name/description/resource_id) are identical after the save.
        """
        before = {k: v for k, v in self._tool_row(self.alice_token).items()
                  if k != "personal"}

        self._post_tools(self.alice_token, {
            "action": "save-personal", "resource_id": "builtin:read",
            "params": {"timeout": 12}, "description": "hijacked", "name": "rm",
            "enabled": False})

        after = {k: v for k, v in self._tool_row(self.alice_token).items()
                 if k != "personal"}
        self.assertEqual(after, before)

    def test_the_retired_personal_resource_route_is_gone(self):
        for method, kwargs in (("GET", {}), ("POST", {"data": "{}"})):
            response = self.app.request(
                "/api/personal/resources", method=method,
                headers=self._headers(self.alice_token, json_body=bool(kwargs)),
                **kwargs)
            self.assertNotIn(response.status, ("200", "200 OK"),
                             "the retired surface must answer like any unknown URL")

    # -- the member Agents surface ---------------------------------------

    def test_the_member_agent_view_answers_self_scoped(self):
        response = self._get_agents(self.alice_token)

        self.assertIn(response.status, ("200", "200 OK"))
        body = self._body(response)
        self.assertEqual(body["scope"], "self")
        # No Agent is bound to this member, and the projection must not fall
        # back to the tenant's shared roster.
        self.assertEqual(body["agents"], [])

    def test_the_member_agent_view_does_not_leak_another_members_agents(self):
        with patch.object(web_channel, "_tenant_agents_admin_projection",
                          return_value={"agents": [{"id": "a-foreign"}],
                                        "default_agent_id": ""}):
            response = self._get_agents(self.alice_token)

        self.assertIn(response.status, ("200", "200 OK"))
        self.assertEqual(self._body(response)["agents"], [])

    def test_the_member_agent_view_needs_the_read_permission(self):
        """Bob's role holds no ``agent.read``: the wire refuses the page."""
        response = self._get_agents(self.bob_token)

        self.assertEqual(response.status, "403 Forbidden")

    # -- the gate itself -------------------------------------------------

    def test_the_personal_surfaces_require_a_session(self):
        for response in (
            self.app.request("/api/tools", method="POST",
                             headers={"Host": "localhost:9899",
                                      "Content-Type": "application/json"},
                             data="{}"),
            self.app.request("/api/agents?view=personal",
                             headers={"Host": "localhost:9899"}),
        ):
            self.assertNotIn(response.status, ("200", "200 OK"),
                             "an unauthenticated request must not be served")

    def test_a_personal_surface_needs_a_tenant_selection(self):
        response = self._get_tools(self.alice_token, tenant=False)

        self.assertNotIn(response.status, ("200", "200 OK"))

    def test_a_foreign_tenant_is_refused(self):
        """A tenant the member does not belong to cannot be named by a header."""
        other = self.service.create_tenant(
            actor_user_id=self.admin_id, code="other", name="Other",
            shared_root=os.path.join(self.root_dir, "other"),
            admin_username="other-root", admin_display="Other Root",
            admin_password="OtherStr0ngPass", recent_password="Str0ngRootFinal")

        headers = self._headers(self.alice_token, json_body=True)
        headers["X-Tenant-ID"] = other["id"]
        response = self.app.request(
            "/api/tools", method="POST", headers=headers,
            data=json.dumps({"action": "save-personal",
                             "resource_id": "builtin:read",
                             "params": {"timeout": 12}}))

        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertNotIn(self.alice_id, json.dumps(self._body(response)))
        rows = self.service._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs")
        self.assertEqual(rows[0]["c"], 0, "a foreign tenant writes nothing")


if __name__ == "__main__":
    unittest.main()
