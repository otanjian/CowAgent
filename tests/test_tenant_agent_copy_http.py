# encoding:utf-8
"""HTTP boundary tests for copying a tenant's agents.

``GET/POST /api/platform/tenants/{tenant_id}/agents`` is the tenant editor's
Agent tab: it reads the target's own agents plus the copyable candidates from the
source tenant, and it copies a checked selection. The endpoint is
platform-admin-only and every write goes through the actor's recent password.

These tests drive the real handler and the real provisioning orchestration over a
throwaway instance root; they are written before the endpoint exists.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import web

from agent import team
from agent.registry import AgentRegistry, set_agent_registry
from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers

ROUTE = "/api/platform/tenants/([^/]+)/agents"
PLATFORM_PASSWORD = "Str0ngAdminPass"


class TenantAgentCopyHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.data_root = os.path.join(self.tmp, "data")
        self.instance = os.path.join(self.tmp, "instance")
        self.target_root = os.path.join(self.tmp, "tenants", "globex")
        os.makedirs(self.data_root)
        os.makedirs(os.path.join(self.instance, "agents", "beta"))
        os.makedirs(self.target_root)
        with open(os.path.join(self.instance, "agents", "beta", "AGENT.md"),
                  "w", encoding="utf-8") as handle:
            handle.write("# Beta persona")

        settings = {
            "agent_workspace": self.instance,
            "default_agent_id": "alpha",
            "agents": [
                {"id": "alpha", "name": "Alpha", "workspace": self.instance,
                 "enabled": True},
                {"id": "beta", "name": "Beta",
                 "workspace": os.path.join(self.instance, "agents", "beta"),
                 "enabled": True},
            ],
            "channel_instances": [],
        }
        with open(os.path.join(self.data_root, "config.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(settings, handle)
        set_agent_registry(AgentRegistry.from_config(team.resolve(settings)))
        self.addCleanup(set_agent_registry, None)

        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password=PLATFORM_PASSWORD,
            shared_root=self.instance, allow_weak=True)
        self.default_tenant_id = self.svc.list_tenants()[0]["id"]
        self.admin_id = self.svc.list_platform_users()[0]["id"]
        self.admin_token = self.svc.login("root", PLATFORM_PASSWORD).token
        self.target = self.svc.create_tenant(
            actor_user_id=self.admin_id, code="globex", name="Globex",
            shared_root=self.target_root, admin_username="globexadmin",
            admin_display="Globex", admin_password="Str0ngPass9",
            recent_password=PLATFORM_PASSWORD)
        self.target_id = self.target["id"]
        self.svc.bind_agent(tenant_id=self.default_tenant_id, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.default_tenant_id, agent_id="beta")

        # A tenant administrator is *not* a platform admin: the 403 case.
        self.svc.create_member(
            actor_user_id=self.admin_id, tenant_id=self.target_id,
            operation="create-new", username="globexops",
            display_name="Globex Ops", temporary_password="Str0ngTemp1",
            roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("globexops", "Str0ngTemp1").token,
            "Str0ngTemp1", "Str0ngOpsFinal")
        self.tenant_token = self.svc.login("globexops", "Str0ngOpsFinal").token

    # --- harness -------------------------------------------------------------

    def _request(self, tenant_id=None, method="GET", payload=None, token=None,
                 database=True):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = "cow_session=%s" % token
        kwargs["headers"] = headers
        settings = {"identity_mode": "database", "identity_db_path": self.db,
                    "agent_workspace": self.instance}
        app = web.application((ROUTE, "PlatformTenantAgentsHandler"),
                              vars(web_channel), autoreload=False)
        path = ROUTE.replace("([^/]+)", tenant_id or self.target_id)
        with patch.object(web_channel, "conf", return_value=settings), \
                patch("config.conf", return_value=settings), \
                patch("config.get_data_root", return_value=self.data_root), \
                patch.object(web_channel, "get_data_root",
                             return_value=self.data_root), \
                patch.object(web_channel, "_reload_agent_runtime",
                             return_value=None) as reload_mock, \
                patch.object(auth_handlers, "_is_database", lambda: database), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch.object(admin_handlers, "_is_database", lambda: database), \
                patch.object(admin_handlers, "_get_service", lambda: self.svc), \
                patch("auth.service.get_identity_service", lambda: self.svc):
            response = app.request(path, **kwargs)
        return response, reload_mock

    @staticmethod
    def _body(response):
        return json.loads(response.data.decode("utf-8"))

    @staticmethod
    def _status(response):
        """web.py reports the bare numeric status; compare on that."""
        return str(response.status).split()[0]

    def _copy(self, selected, token=None, recent_password=PLATFORM_PASSWORD,
              tenant_id=None):
        return self._request(
            tenant_id=tenant_id, method="POST", token=token or self.admin_token,
            payload={"action": "copy", "source_agent_ids": selected,
                     "recent_password": recent_password})

    def _audit(self, action="tenant.copy_agents", result=None):
        sql = "SELECT * FROM audit_events WHERE action=?"
        params = [action]
        if result:
            sql += " AND result=?"
            params.append(result)
        return [dict(r) for r in self.svc._store.execute(sql, tuple(params))]

    def _target_agent_ids(self):
        return set(self.svc.tenant_agent_ids(self.target_id))

    # --- GET -----------------------------------------------------------------

    def test_get_returns_the_target_agents_and_the_source_candidates(self):
        response, _ = self._request(token=self.admin_token)

        self.assertEqual(response.status, "200 OK", response.data)
        body = self._body(response)
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["agents"], [])
        source = body["copy_source"]
        self.assertEqual(source["tenant_id"], self.default_tenant_id)
        self.assertEqual(source["code"], "acme")
        self.assertEqual([c["source_agent_id"] for c in source["candidates"]],
                         ["alpha", "beta"])
        self.assertTrue(source["candidates"][0]["is_default"])

    def test_get_marks_already_copied_candidates(self):
        self._copy(["alpha"])

        _, _ = self._request(token=self.admin_token)
        response, _ = self._request(token=self.admin_token)

        body = self._body(response)
        agents = {a["id"]: a for a in body["agents"]}
        self.assertEqual(set(agents), {"alpha-globex"})
        self.assertTrue(agents["alpha-globex"]["is_default"])
        candidates = {c["source_agent_id"]: c
                      for c in body["copy_source"]["candidates"]}
        self.assertTrue(candidates["alpha"]["already_copied"])
        self.assertEqual(candidates["alpha"]["clone_agent_id"], "alpha-globex")
        self.assertFalse(candidates["beta"]["already_copied"])

    def test_get_never_leaks_a_workspace_or_host_path(self):
        response, _ = self._request(token=self.admin_token)
        blob = json.dumps(self._body(response))
        for leak in (self.instance, self.target_root, "workspace", "shared_root"):
            self.assertNotIn(leak, blob)

    def test_get_reports_that_a_tenant_cannot_copy_from_itself(self):
        response, _ = self._request(tenant_id=self.default_tenant_id,
                                    token=self.admin_token)

        body = self._body(response)
        self.assertEqual(response.status, "200 OK")
        self.assertIsNone(body["copy_source"])
        self.assertTrue(body["source_error"])

    def test_get_requires_a_platform_admin(self):
        response, _ = self._request(token=self.tenant_token)
        self.assertEqual(self._status(response), "403")

    # --- POST: authorization and validation ----------------------------------

    def test_non_platform_admin_post_is_forbidden_and_writes_nothing(self):
        response, _ = self._copy(["alpha"], token=self.tenant_token)

        self.assertEqual(self._status(response), "403")
        self.assertEqual(self._target_agent_ids(), set())
        denied = self._audit(result="denied")
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["target_tenant_id"], self.target_id)
        self.assertEqual(self._audit(result="success"), [])

    def test_post_without_a_valid_recent_password_writes_nothing(self):
        response, _ = self._copy(["alpha"], recent_password="WrongPassword")

        self.assertEqual(self._status(response), "401", response.data)
        self.assertEqual(self._target_agent_ids(), set())
        self.assertEqual(self._audit(result="success"), [])
        self.assertNotIn("alpha-globex",
                         {a["agent_id"] for a in self.svc.list_agent_bindings()})
        self.assertFalse(os.path.exists(
            os.path.join(self.target_root, "agents", "alpha-globex")))

    def test_empty_selection_is_rejected_without_writing(self):
        response, _ = self._copy([])

        self.assertEqual(self._status(response), "400")
        self.assertEqual(self._target_agent_ids(), set())
        self.assertEqual(self._audit(result="success"), [])

    def test_an_id_outside_the_candidates_is_rejected_without_writing(self):
        response, _ = self._copy(["alpha", "ghost"])

        self.assertEqual(self._status(response), "400")
        self.assertEqual(self._target_agent_ids(), set())
        self.assertEqual(self._audit(result="success"), [])

    def test_an_unknown_target_tenant_is_rejected(self):
        response, _ = self._request(tenant_id="nope", method="POST",
                                    token=self.admin_token,
                                    payload={"action": "copy",
                                             "source_agent_ids": ["alpha"],
                                             "recent_password": PLATFORM_PASSWORD})
        self.assertEqual(self._status(response), "404")

    def test_the_endpoint_is_closed_in_legacy_identity_mode(self):
        response, _ = self._request(token=self.admin_token, database=False)
        self.assertEqual(self._status(response), "400")
        self.assertEqual(self._body(response)["code"], "not_database")

    # --- POST: the copy ------------------------------------------------------

    def test_post_copies_the_selection_and_reloads_the_runtime(self):
        response, reload_mock = self._copy(["alpha"])

        self.assertEqual(response.status, "200 OK", response.data)
        body = self._body(response)
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["selected"], 1)
        self.assertEqual(body["copied"], 1)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["failed"], [])
        self.assertEqual(body["default_agent_id"], "alpha-globex")
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})
        reload_mock.assert_called_once()

    def test_post_writes_one_redacted_summary_audit(self):
        self._copy(["alpha"])

        events = self._audit(result="success")
        self.assertEqual(len(events), 1)
        changes = json.loads(events[0]["redacted_changes"])
        self.assertEqual(changes["source_tenant_id"], self.default_tenant_id)
        self.assertEqual(changes["copied_agent_ids"], ["alpha-globex"])
        self.assertEqual(changes["default_agent_id"], "alpha-globex")
        blob = json.dumps(events[0])
        for secret in (PLATFORM_PASSWORD, "password_hash", "cow_session", "sk-"):
            self.assertNotIn(secret, blob)

    def test_post_reports_a_rerun_as_skipped(self):
        self._copy(["alpha"])

        response, _ = self._copy(["alpha"])

        body = self._body(response)
        self.assertEqual((body["copied"], body["skipped"]), (0, 1))
        self.assertEqual(body["skipped_agent_ids"],
                         [{"source_agent_id": "alpha", "agent_id": "alpha-globex"}])

    def test_post_requires_the_copy_action(self):
        response, _ = self._request(
            method="POST", token=self.admin_token,
            payload={"source_agent_ids": ["alpha"],
                     "recent_password": PLATFORM_PASSWORD})
        self.assertEqual(self._status(response), "400")
        self.assertEqual(self._target_agent_ids(), set())


if __name__ == "__main__":
    unittest.main()
