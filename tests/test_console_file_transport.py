# encoding:utf-8
"""Console file download / preview transport in database mode.

A generated artifact is offered to the console as a card carrying two browser
URLs:

* ``raw_url = /api/file?path=...`` — a plain ``<a download>`` navigation (and,
  elsewhere, an ``<img>`` subresource). The browser issues it itself, so it
  structurally cannot attach ``X-Tenant-ID``.
* ``preview_url = /preview/<hmac-dir-token>/<name>`` — a capability URL for the
  sandboxed preview iframe, which cannot send the session cookie either.

These tests pin the contract for both ends:

* ``GET /api/file`` resolves its tenant from the *file* (the addressed
  resource's workspace) instead of demanding a header, while still enforcing
  membership, conflicting-selection and cross-tenant invisibility;
* ``GET /preview/<token>/<name>`` serves a tenant/agent workspace file for a
  valid capability token, and still refuses a token for a directory outside the
  trusted roots.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

import config
from agent.memory import clear_conversation_store_cache
from agent.registry import AgentProfile, AgentRegistry
from auth.service import IdentityService
from channel.web import web_channel
from tests._helpers import cookie_value as _cookie_value


class ConsoleFileTransportTests(unittest.TestCase):
    """`GET /api/file` and `GET /preview/...` under real database auth."""

    def setUp(self):
        from channel.web import auth_handlers
        auth_handlers.reset_login_rate_limiter()
        temporary = tempfile.TemporaryDirectory(prefix="console-file-")
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

        # The reported layout: a generated HTML artifact under a non-default
        # Agent's workspace, inside the tenant shared root.
        self.agent_workspace = os.path.join(temporary.name, "acme", "agents", "business-analysis")
        self.foreign_workspace = os.path.join(temporary.name, "other", "agents", "foreign-agent")
        for path in (self.agent_workspace, self.foreign_workspace):
            os.makedirs(os.path.join(path, "websites"), exist_ok=True)
        self.artifact = os.path.join(self.agent_workspace, "websites", "report.html")
        with open(self.artifact, "wb") as handle:
            handle.write(b"<html>artifact</html>")
        self.foreign_file = os.path.join(self.foreign_workspace, "websites", "secret.html")
        with open(self.foreign_file, "wb") as handle:
            handle.write(b"<html>foreign</html>")

        registry = AgentRegistry([
            AgentProfile(id="business-analysis", name="BA", workspace=self.agent_workspace),
            AgentProfile(id="foreign-agent", name="Foreign", workspace=self.foreign_workspace),
        ], "business-analysis")
        self.service.bind_agent(tenant_id=self.tenant_id, agent_id="business-analysis")
        self.service.bind_agent(tenant_id=self.other_tenant_id, agent_id="foreign-agent")

        settings = {
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "agent_workspace": os.path.join(temporary.name, "acme"),
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

    def _headers(self, *, tenant=None):
        headers = {
            "Host": "localhost:9899",
            "Origin": "http://localhost:9899",
            "Cookie": "cow_session=" + self.token,
        }
        if tenant:
            headers["X-Tenant-ID"] = tenant
        return headers

    def _file_url(self, path):
        from urllib.parse import quote
        return "/api/file?path=" + quote(os.path.realpath(path))

    # -- GET /api/file (download navigation / <img> subresource) ---------

    def test_download_without_tenant_header_serves_own_workspace_file(self):
        """A card download cannot send X-Tenant-ID; the path supplies the tenant."""
        response = self.app.request(
            self._file_url(self.artifact), method="GET", headers=self._headers(),
        )
        self.assertEqual(response.status, "200 OK", response.data)
        self.assertEqual(response.data, b"<html>artifact</html>")

    def test_download_refuses_a_cross_tenant_path(self):
        """Deriving the tenant from the path must not skip membership."""
        response = self.app.request(
            self._file_url(self.foreign_file), method="GET", headers=self._headers(),
        )
        self.assertEqual(int(response.status.split()[0]), 403)
        self.assertNotIn(b"foreign", response.data)

    def test_download_rejects_a_conflicting_selection(self):
        """An explicit selection that disagrees with the file is a 400."""
        response = self.app.request(
            self._file_url(self.artifact), method="GET",
            headers=self._headers(tenant=self.other_tenant_id),
        )
        self.assertEqual(int(response.status.split()[0]), 400)
        self.assertEqual(json.loads(response.data.decode("utf-8"))["code"],
                         "conflicting_tenant")

    def test_download_still_requires_credentials(self):
        """Resource derivation is not an authentication bypass."""
        response = self.app.request(
            self._file_url(self.artifact), method="GET",
            headers={"Host": "localhost:9899", "Origin": "http://localhost:9899"},
        )
        self.assertEqual(int(response.status.split()[0]), 401)

    def test_download_hides_a_path_outside_every_workspace(self):
        outside = os.path.join(self.root, "loose-secret.html")
        with open(outside, "wb") as handle:
            handle.write(b"<html>loose</html>")
        response = self.app.request(
            self._file_url(outside), method="GET", headers=self._headers(),
        )
        self.assertEqual(int(response.status.split()[0]), 404)
        self.assertNotIn(b"loose", response.data)

    # -- GET /preview/<token>/<name> (capability iframe) -----------------

    def _preview_url(self, path):
        from urllib.parse import quote
        real = os.path.realpath(path)
        token = web_channel._encode_dir_token(os.path.dirname(real))
        return f"/preview/{token}/{quote(os.path.basename(real))}"

    def test_preview_serves_a_tenant_workspace_file_without_a_cookie(self):
        """The HMAC directory token authorizes the read; the iframe has no cookie."""
        with patch.object(web_channel, "_get_preview_secret", return_value=b"test-secret"):
            response = self.app.request(
                self._preview_url(self.artifact), method="GET",
                headers={"Host": "localhost:9899"},
            )
        self.assertEqual(response.status, "200 OK", response.data)
        # HTML previews gain the console's scrollbar chrome; the file body is
        # otherwise served verbatim.
        self.assertIn(b"artifact", response.data)

    def test_preview_refuses_a_token_for_an_untrusted_directory(self):
        """A leaked token for a non-workspace directory still must not read out."""
        outside_dir = os.path.join(self.root, "loose")
        os.makedirs(outside_dir, exist_ok=True)
        loose = os.path.join(outside_dir, "secret.html")
        with open(loose, "wb") as handle:
            handle.write(b"<html>loose</html>")
        with patch.object(web_channel, "_get_preview_secret", return_value=b"test-secret"):
            response = self.app.request(
                self._preview_url(loose), method="GET",
                headers={"Host": "localhost:9899"},
            )
        self.assertEqual(int(response.status.split()[0]), 404)
        self.assertNotIn(b"loose", response.data)


if __name__ == "__main__":
    unittest.main()
