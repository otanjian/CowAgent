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
    """``/api/personal/resources`` and ``/api/agents?view=personal``."""

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
                {"resource_kind": "tool", "resource_id": "builtin:echo",
                 "action": "execute"},
                {"resource_kind": "skill", "resource_id": "custom:writer",
                 "action": "use"},
            ])
        self.alice_id = self._member("alice", ["personalizer"])
        self.limited_role = self.service.create_role(
            self.admin_id, self.tenant_id, "limited", "Limited", ["history.read"])
        self.bob_id = self._member("bob", ["limited"])
        self.alice_token = self._login("alice", "MemPassFinal1")
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

    def _get_resources(self, token, *, tenant=True, kind=""):
        path = "/api/personal/resources"
        if kind:
            path += "?kind=" + kind
        return self.app.request(path, headers=self._headers(token, tenant=tenant))

    def _post_resource(self, token, body, *, tenant=True):
        return self.app.request(
            "/api/personal/resources", method="POST",
            headers=self._headers(token, tenant=tenant, json_body=True),
            data=json.dumps(body))

    def _get_agents(self, token, *, view="personal", tenant=True):
        return self.app.request(
            "/api/agents?view=" + view,
            headers=self._headers(token, tenant=tenant))

    def _body(self, response):
        return json.loads(response.data.decode("utf-8"))

    # -- the personal resource surface -----------------------------------

    def test_a_member_sees_only_the_resources_they_are_granted(self):
        response = self._get_resources(self.alice_token)

        self.assertEqual(response.status, "200 OK")
        body = self._body(response)
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["scope"], "personal")
        self.assertEqual(
            {(r["resource_kind"], r["resource_id"]) for r in body["resources"]},
            {("tool", "builtin:echo"), ("skill", "custom:writer")})

    def test_a_member_without_grants_sees_an_empty_list(self):
        response = self._get_resources(self.bob_token)

        self.assertEqual(response.status, "200 OK")
        self.assertEqual(self._body(response)["resources"], [])

    def test_a_kind_filter_narrows_the_transport_list(self):
        body = self._body(self._get_resources(self.alice_token, kind="tool"))
        self.assertEqual([r["resource_kind"] for r in body["resources"]], ["tool"])

    def _entry(self, resources, kind):
        return [r for r in resources if r["resource_kind"] == kind][0]

    def test_saving_parameters_round_trips(self):
        saved = self._post_resource(self.alice_token, {
            "resource_kind": "tool", "resource_id": "builtin:echo",
            "params": {"timeout": 12}})
        self.assertIn(saved.status, ("200", "200 OK"))

        entry = self._entry(
            self._body(self._get_resources(self.alice_token))["resources"], "tool")
        self.assertTrue(entry["configured"])
        self.assertEqual(entry["params"], {"timeout": 12})

    def test_a_save_lands_on_the_callers_own_row_only(self):
        self._post_resource(self.alice_token, {
            "resource_kind": "tool", "resource_id": "builtin:echo",
            "params": {"timeout": 12}})

        rows = self.service._store.execute(
            "SELECT user_id FROM personal_resource_configs")
        self.assertEqual([r["user_id"] for r in rows], [self.alice_id])

    def test_another_member_never_sees_the_saved_configuration(self):
        self._post_resource(self.alice_token, {
            "resource_kind": "tool", "resource_id": "builtin:echo",
            "params": {"timeout": 12}})

        listing = self._body(self._get_resources(self.bob_token))["resources"]
        self.assertEqual([r for r in listing if r["configured"]], [])

    def test_saving_an_ungranted_resource_is_refused(self):
        response = self._post_resource(self.alice_token, {
            "resource_kind": "tool", "resource_id": "builtin:rm",
            "params": {}})

        self.assertEqual(response.status, "403 Forbidden")
        self.assertEqual(self._body(response)["code"], "forbidden")
        rows = self.service._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs")
        self.assertEqual(rows[0]["c"], 0)

    def test_clearing_is_idempotent_over_the_wire(self):
        self._post_resource(self.alice_token, {
            "resource_kind": "tool", "resource_id": "builtin:echo",
            "params": {"timeout": 12}})

        cleared = self._post_resource(self.alice_token, {
            "action": "clear", "resource_kind": "tool",
            "resource_id": "builtin:echo"})
        again = self._post_resource(self.alice_token, {
            "action": "clear", "resource_kind": "tool",
            "resource_id": "builtin:echo"})

        self.assertIn(cleared.status, ("200", "200 OK"))
        self.assertIn(again.status, ("200", "200 OK"))
        entry = self._entry(
            self._body(self._get_resources(self.alice_token))["resources"], "tool")
        self.assertFalse(entry["configured"])

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
            self.app.request("/api/personal/resources",
                             headers={"Host": "localhost:9899"}),
            self.app.request("/api/agents?view=personal",
                             headers={"Host": "localhost:9899"}),
        ):
            self.assertNotIn(response.status, ("200", "200 OK"),
                             "an unauthenticated request must not be served")

    def test_a_personal_surface_needs_a_tenant_selection(self):
        response = self._get_resources(self.alice_token, tenant=False)

        self.assertNotIn(response.status, ("200", "200 OK"))

    def test_a_foreign_tenant_is_refused(self):
        """A tenant the member does not belong to cannot be named by a header."""
        other = self.service.create_tenant(
            actor_user_id=self.admin_id, code="other", name="Other",
            shared_root=os.path.join(self.root_dir, "other"),
            admin_username="other-root", admin_display="Other Root",
            admin_password="OtherStr0ngPass", recent_password="Str0ngRootFinal")

        headers = self._headers(self.alice_token)
        headers["X-Tenant-ID"] = other["id"]
        response = self.app.request("/api/personal/resources", headers=headers)

        self.assertNotIn(response.status, ("200", "200 OK"))
        self.assertNotIn(self.alice_id, json.dumps(self._body(response)))


if __name__ == "__main__":
    unittest.main()
