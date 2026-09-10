# encoding:utf-8
"""A tenant administrator's own Agent must be tenant-scoped at birth.

Regression shape: ``POST /api/agents`` wrote a new Agent into the *instance*
roster and the instance-root workspace, and never created an ``agent_bindings``
row. The read path (``_tenant_agents_projection``) filters the roster by the
tenant binding, so the creating tenant's list stayed empty and the console
looked as though creation had silently failed.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers


class TenantAgentCreationTests(unittest.TestCase):
    """One tenant (acme) with an admin, and an instance root kept disjoint."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.data_root = os.path.join(self.tmp, "data")
        self.instance = os.path.join(self.tmp, "instance")
        self.tenant_root = os.path.join(self.tmp, "tenants", "acme")
        for path in (self.data_root, self.instance, self.tenant_root):
            os.makedirs(path)

        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.tenant_root, allow_weak=True)
        self.tenant_id = self.svc.list_tenants()[0]["id"]
        self.platform_admin = self.svc.list_platform_users()[0]

        # A tenant administrator (NOT a platform admin) is the console user the
        # regression was reported from: a platform admin sees the whole roster
        # through a different branch, so it would not reproduce.
        self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username="acmeadmin", display_name="Tenant Admin",
            temporary_password="Str0ngTemp1", roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("acmeadmin", "Str0ngTemp1").token,
            "Str0ngTemp1", "Str0ngTaFinal")
        self.token = self.svc.login("acmeadmin", "Str0ngTaFinal").token

        # The instance root the roster is written under; without this the
        # service would fall back to ``~/cow`` and the test would touch the
        # developer's real install.
        with open(os.path.join(self.data_root, "config.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"agent_workspace": self.instance}, handle)

    def _app(self):
        return web.application(
            ("/api/agents", "AgentsHandler"), vars(web_channel), autoreload=False)

    def _request(self, path, method="GET", payload=None, token=None, tenant=None):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
        if tenant:
            headers["X-Tenant-ID"] = tenant
        kwargs["headers"] = headers

        settings = {"identity_mode": "database", "identity_db_path": self.db,
                    "agent_workspace": self.instance}
        with patch.object(web_channel, "conf", return_value=settings), \
                patch("config.conf", return_value=settings), \
                patch.object(web_channel, "get_data_root",
                             return_value=self.data_root), \
                patch.object(web_channel, "_reload_agent_runtime",
                             lambda *a, **k: None), \
                patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch.object(admin_handlers, "_is_database", lambda: True), \
                patch.object(admin_handlers, "_get_service", lambda: self.svc), \
                patch("auth.service.get_identity_service", lambda: self.svc):
            return self._app().request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _create(self, agent_id="agent-a", name="Agent A"):
        resp = self._request(
            "/api/agents", method="POST", token=self.token, tenant=self.tenant_id,
            payload={"action": "create", "id": agent_id, "name": name})
        self.assertEqual(resp.status, "200 OK", resp.data)
        body = self._json(resp)
        self.assertEqual(body.get("status"), "success", body)
        return body

    # --- the workspace is tenant-scoped --------------------------------

    def test_the_workspace_lands_inside_the_tenant_root(self):
        self._create()
        self.assertTrue(
            os.path.isdir(os.path.join(self.tenant_root, "agents", "agent-a")),
            sorted(os.listdir(self.tenant_root)))

    def test_the_workspace_is_not_written_into_the_instance_root(self):
        """The instance root holds the shared library and other tenants' Agents;
        an Agent created there is neither isolated nor tenant-visible."""
        self._create()
        self.assertFalse(
            os.path.exists(os.path.join(self.instance, "agents", "agent-a")))

    # --- the binding is what makes it visible --------------------------

    def test_the_created_agent_is_bound_to_the_creating_tenant(self):
        self._create()
        self.assertIn("agent-a", self.svc.tenant_agent_ids(self.tenant_id))

    def test_the_created_agent_appears_in_the_tenants_own_roster(self):
        self._create()
        body = self._json(self._request("/api/agents", token=self.token,
                                        tenant=self.tenant_id))
        self.assertEqual([a["id"] for a in body["agents"]], ["agent-a"])

    # --- the tenant gets a default to chat with ------------------------

    def test_the_first_agent_becomes_the_tenant_default(self):
        self._create()
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id),
                         "agent-a")

    def test_a_second_agent_does_not_steal_the_default(self):
        self._create("agent-a")
        self._create("agent-b")
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id),
                         "agent-a")

    # --- guards ---------------------------------------------------------

    def test_an_unknown_agent_id_cannot_escape_the_tenant_root(self):
        """The id becomes a path segment, so it must be validated before any
        directory is created rather than after ``AgentProfile`` rejects it."""
        resp = self._request(
            "/api/agents", method="POST", token=self.token, tenant=self.tenant_id,
            payload={"action": "create", "id": "../../escape", "name": "Escape"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp).get("status"), "error")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape")))

    # --- the tenant admin administers its OWN tenant's agents -----------

    def test_the_tenant_admin_can_edit_the_agent_it_created(self):
        self._create()
        resp = self._request(
            "/api/agents", method="POST", token=self.token, tenant=self.tenant_id,
            payload={"action": "update", "id": "agent-a", "name": "Renamed"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp).get("status"), "success")
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "agent-a")

    def test_another_tenants_agent_is_neither_visible_nor_editable(self):
        """The binding is the isolation boundary: the tenant-admin trust must
        grant nothing over an Agent bound to a different tenant."""
        self.svc.create_tenant(
            actor_user_id=self.platform_admin["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngAdminPass",
            shared_root=os.path.join(self.tmp, "tenants", "globex"))
        globex_id = [t for t in self.svc.list_tenants()
                     if t["code"] == "globex"][0]["id"]
        self.svc.change_password(self.svc.login("globexadmin", "Str0ngPass9").token,
                                 "Str0ngPass9", "Str0ngGlobex9")
        globex_token = self.svc.login("globexadmin", "Str0ngGlobex9").token

        resp = self._request(
            "/api/agents", method="POST", token=globex_token, tenant=globex_id,
            payload={"action": "create", "id": "globex-agent", "name": "Globex Agent"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["status"], "success")

        body = self._json(self._request("/api/agents", token=self.token,
                                        tenant=self.tenant_id))
        self.assertNotIn("globex-agent", [a["id"] for a in body["agents"]])

        resp = self._request(
            "/api/agents", method="POST", token=self.token, tenant=self.tenant_id,
            payload={"action": "update", "id": "globex-agent", "name": "Stolen"})
        self.assertTrue(str(resp.status).startswith("403"), resp.data)


if __name__ == "__main__":
    unittest.main()
