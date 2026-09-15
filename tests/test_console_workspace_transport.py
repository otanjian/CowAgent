# encoding:utf-8
"""Console workspace panel transport in database mode.

The chat page's right-hand panel (``#workspace-panel``) drives six routes:

* ``GET /api/workspace/tree|search|resolve|meta|read`` for the file tree,
  metadata, preview body and search;
* ``POST /api/workspace/write`` for the preview editor's save.

In database identity mode every one of them used to be ``closed`` in the route
registry, so the HTTP gate answered ``503 database_unavailable`` before the
handler ran and the panel rendered the raw backend string. These tests pin the
open contract plus the tenant scoping that must come with it: a member may only
browse/preview/edit their own tenant's roots, an absolute path into another
tenant (or the platform root) is invisible, and the write path goes through the
unified origin/CSRF gate.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from agent.memory import clear_conversation_store_cache
from agent.registry import AgentProfile, AgentRegistry
from auth.service import IdentityService
from channel.web import web_channel
from tests._helpers import cookie_value as _cookie_value


class ConsoleWorkspaceTransportTests(unittest.TestCase):
    """``/api/workspace/*`` under real database auth."""

    def setUp(self):
        from channel.web import auth_handlers
        auth_handlers.reset_login_rate_limiter()
        temporary = tempfile.TemporaryDirectory(prefix="console-workspace-")
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.db_path = os.path.join(temporary.name, "identity.db")
        self.service = IdentityService(self.db_path)
        tenant = self.service.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(temporary.name, "acme"), allow_weak=True,
        )
        self.tenant_id = tenant["id"]
        self.admin_id = self.service.list_platform_users()[0]["id"]

        other = self.service.create_tenant(
            actor_user_id=self.admin_id, code="other", name="Other",
            shared_root=os.path.join(temporary.name, "other"),
            admin_username="other-root", admin_display="Other Root",
            admin_password="OtherStr0ngPass", recent_password="Str0ngAdminPass",
        )
        self.other_tenant_id = other["id"]

        self.shared_root = os.path.realpath(os.path.join(temporary.name, "acme"))
        self.agent_workspace = os.path.join(self.shared_root, "agents", "business-analysis")
        self.foreign_workspace = os.path.join(
            os.path.realpath(os.path.join(temporary.name, "other")),
            "agents", "foreign-agent")
        for path in (os.path.join(self.agent_workspace, "docs"),
                     os.path.join(self.foreign_workspace, "docs")):
            os.makedirs(path, exist_ok=True)
        self.own_file = os.path.join(self.agent_workspace, "docs", "note.txt")
        with open(self.own_file, "wb") as handle:
            handle.write(b"hello-own")
        self.foreign_file = os.path.join(self.foreign_workspace, "docs", "secret.txt")
        with open(self.foreign_file, "wb") as handle:
            handle.write(b"hello-foreign")

        registry = AgentRegistry([
            AgentProfile(id="business-analysis", name="BA", workspace=self.agent_workspace),
            AgentProfile(id="foreign-agent", name="Foreign", workspace=self.foreign_workspace),
        ], "business-analysis")
        self.service.bind_agent(tenant_id=self.tenant_id, agent_id="business-analysis")
        self.service.bind_agent(tenant_id=self.other_tenant_id, agent_id="foreign-agent")

        settings = {
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "agent_workspace": self.shared_root,
        }
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        registry_patch = patch("agent.registry.get_agent_registry", return_value=registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.addCleanup(clear_conversation_store_cache)
        self.app = web_channel.build_web_app()

        login = self.app.request(
            "/auth/login", method="POST",
            headers={"Host": "localhost:9899", "Origin": "http://localhost:9899",
                     "Content-Type": "application/json"},
            data=json.dumps({"username": "root", "password": "Str0ngAdminPass"}),
        )
        self.assertEqual(login.status, "200 OK")
        self.token = _cookie_value(login, "cow_session")
        self.assertTrue(self.token)

    # -- helpers ---------------------------------------------------------

    def _headers(self, *, tenant=None, origin="http://localhost:9899"):
        headers = {
            "Host": "localhost:9899",
            "Origin": origin,
            "Cookie": "cow_session=" + self.token,
        }
        if tenant:
            headers["X-Tenant-ID"] = tenant
        return headers

    def _post_write(self, path, content, *, headers=None):
        return self.app.request(
            "/api/workspace/write", method="POST",
            headers=headers or self._headers(tenant=self.tenant_id),
            data=json.dumps({"path": path, "content": content}),
        )

    # -- route gate ------------------------------------------------------

    def test_tree_requires_a_tenant_selection(self):
        """A tenant route rejected before the handler -- 400, never 503."""
        response = self.app.request(
            "/api/workspace/tree", method="GET",
            headers={"Host": "localhost:9899", "Cookie": "cow_session=" + self.token},
        )
        self.assertEqual(int(response.status.split()[0]), 400, response.data)
        self.assertEqual(json.loads(response.data.decode("utf-8"))["code"],
                         "missing_tenant")

    def test_tree_still_requires_credentials(self):
        response = self.app.request(
            "/api/workspace/tree", method="GET",
            headers={"Host": "localhost:9899", "X-Tenant-ID": self.tenant_id},
        )
        self.assertEqual(int(response.status.split()[0]), 401, response.data)

    # -- read path -------------------------------------------------------

    def test_tree_lists_the_callers_tenant_root(self):
        response = self.app.request(
            "/api/workspace/tree", method="GET",
            headers=self._headers(tenant=self.tenant_id),
        )
        self.assertEqual(response.status, "200 OK", response.data)
        body = json.loads(response.data.decode("utf-8"))
        self.assertEqual(body["status"], "success")
        self.assertEqual(os.path.realpath(body["root"]), self.shared_root)

    def test_read_returns_an_own_file(self):
        response = self.app.request(
            "/api/workspace/read?path=" + "agents/business-analysis/docs/note.txt",
            method="GET", headers=self._headers(tenant=self.tenant_id),
        )
        self.assertEqual(response.status, "200 OK", response.data)
        body = json.loads(response.data.decode("utf-8"))
        self.assertEqual(body["status"], "success")
        self.assertIn("hello-own", body["content"])

    def test_resolve_refuses_a_cross_tenant_absolute_path(self):
        from urllib.parse import quote
        response = self.app.request(
            "/api/workspace/resolve?path=" + quote(os.path.realpath(self.foreign_file)),
            method="GET", headers=self._headers(tenant=self.tenant_id),
        )
        self.assertIn(int(response.status.split()[0]), (403, 404), response.data)
        self.assertNotIn(b"foreign", response.data)
        self.assertNotIn(b"preview_url", response.data)

    def test_read_refuses_an_escaping_relative_path(self):
        response = self.app.request(
            "/api/workspace/read?path=" + "../other/agents/foreign-agent/docs/secret.txt",
            method="GET", headers=self._headers(tenant=self.tenant_id),
        )
        # The route must actually run (not the closed 503), then refuse the
        # escape without leaking the foreign file.
        self.assertNotEqual(int(response.status.split()[0]), 503, response.data)
        self.assertNotIn(b"hello-foreign", response.data)

    # -- write path ------------------------------------------------------

    def test_write_rejects_a_cross_origin_cookie_request(self):
        response = self._post_write(
            "agents/business-analysis/docs/note.txt", "tampered",
            headers=self._headers(tenant=self.tenant_id, origin="http://evil.example"),
        )
        self.assertEqual(int(response.status.split()[0]), 403, response.data)
        self.assertEqual(json.loads(response.data.decode("utf-8"))["code"],
                         "csrf_failed")
        with open(self.own_file, "rb") as handle:
            self.assertEqual(handle.read(), b"hello-own")

    def test_write_accepts_a_same_origin_request(self):
        response = self._post_write(
            "agents/business-analysis/docs/note.txt", "updated",
            headers=self._headers(tenant=self.tenant_id),
        )
        self.assertEqual(response.status, "200 OK", response.data)
        with open(self.own_file, "rb") as handle:
            self.assertEqual(handle.read(), b"updated")

    def test_write_refuses_a_cross_tenant_absolute_path(self):
        response = self._post_write(
            os.path.realpath(self.foreign_file), "tampered",
            headers=self._headers(tenant=self.tenant_id),
        )
        # The route must actually run and deny by tenant scope, not sit closed.
        self.assertIn(int(response.status.split()[0]), (403, 404), response.data)
        self.assertNotIn(b"foreign", response.data)
        with open(self.foreign_file, "rb") as handle:
            self.assertEqual(handle.read(), b"hello-foreign")


if __name__ == "__main__":
    unittest.main()
