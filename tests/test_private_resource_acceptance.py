# encoding:utf-8
"""Acceptance gate for opening private maintenance (task 3.6).

The individual rules were built and unit-tested across tasks 3.1–3.4. This file
does not re-test them in isolation; it drives the **real handlers end to end** —
`/api/workspace/read`, `/api/workspace/write`, `/api/file`, `/preview` — through
the actual app, because a rule that holds in a helper and leaks in a handler is
exactly the failure mode the earlier changes shipped. Four properties are
required before a member may be handed private maintenance:

1. the owner is allowed (read *and* write);
2. an administrator is refused a member's private content on every one of those
   entry points;
3. shared resources keep their pre-existing authorization exactly — no new owner
   gate appears where there was none;
4. an identity-store failure **denies** rather than allowing. Property 4 is the
   one that decides whether the gate is real: a fail-open lookup would make 1–3
   cosmetic.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from agent.memory import clear_conversation_store_cache
from agent.registry import AgentProfile, AgentRegistry
from auth.service import IdentityService, IdentityServiceError
from channel.web import web_channel
from tests._helpers import cookie_value as _cookie_value


class PrivateMaintenanceAcceptanceTests(unittest.TestCase):
    WEAK = {"allow_weak": True}

    def setUp(self):
        from channel.web import auth_handlers
        auth_handlers.reset_login_rate_limiter()
        temporary = tempfile.TemporaryDirectory(prefix="private-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.db_path = os.path.join(temporary.name, "identity.db")
        self.platform_root = os.path.join(temporary.name, "platform")
        self.shared_root = os.path.join(temporary.name, "acme")
        self.shared_ws = os.path.join(self.shared_root, "agents", "shared-agent")
        self.private_ws = os.path.join(self.shared_root, "agents", "private-agent")
        for path in (self.platform_root, self.shared_ws, self.private_ws):
            os.makedirs(path, exist_ok=True)
        self.service = IdentityService(self.db_path)
        tenant = self.service.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared_root, **self.WEAK)
        self.tenant_id = tenant["id"]
        self.admin_id = self.service.list_platform_users()[0]["id"]
        self.owner_id = self._member("carol")
        self.other_id = self._member("bob")
        self.private_file = self._seed(
            os.path.join(self.private_ws, "secret.txt"), b"carols-private-body")
        self.shared_file = self._seed(
            os.path.join(self.shared_ws, "public.txt"), b"public-body")
        registry = AgentRegistry([
            AgentProfile(id="shared-agent", name="Shared", workspace=self.shared_ws),
            AgentProfile(id="private-agent", name="Private", workspace=self.private_ws),
        ], "shared-agent")
        self.service.bind_agent(tenant_id=self.tenant_id, agent_id="shared-agent")
        self.service.bind_agent(tenant_id=self.tenant_id, agent_id="private-agent",
                                private_owner_user_id=self.owner_id)
        settings = {
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "platform_file_root": self.platform_root,
            "agent_workspace": self.shared_root,
        }
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        registry_patch = patch("agent.registry.get_agent_registry", return_value=registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        clear_conversation_store_cache()
        self.addCleanup(clear_conversation_store_cache)
        self.app = web_channel.build_web_app()
        self.owner_cookie = self._cookie_login("carol", "CarolFinalPass1")
        self.other_cookie = self._cookie_login("bob", "BobFinalPass1")
        self.admin_cookie = self._cookie_login("root", "Str0ngAdminPass")

    # -- fixture --------------------------------------------------------

    def _member(self, username):
        created = self.service.create_member(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            operation="create-new", username=username, display_name=username.title(),
            temporary_password="TempPass1!", roles=["member"])
        first = self.service.login(username, "TempPass1!").token
        self.service.change_password(first, "TempPass1!",
                                     username.title() + "FinalPass1")
        return created["user_id"]

    @staticmethod
    def _seed(path, body):
        with open(path, "wb") as handle:
            handle.write(body)
        return os.path.realpath(path)

    def _cookie_login(self, username, password):
        response = self.app.request(
            "/auth/login", method="POST",
            headers={"Host": "localhost:9899", "Origin": "http://localhost:9899",
                     "Content-Type": "application/json"},
            data=json.dumps({"username": username, "password": password}),
        )
        self.assertEqual(response.status, "200 OK", response.data)
        token = _cookie_value(response, "cow_session")
        self.assertTrue(token)
        return token

    def _headers(self, cookie):
        return {"Host": "localhost:9899", "Origin": "http://localhost:9899",
                "Cookie": "cow_session=" + cookie,
                "X-Tenant-ID": self.tenant_id,
                "Content-Type": "application/json"}

    def _read(self, path, cookie, agent):
        from urllib.parse import urlencode
        return self.app.request(
            "/api/workspace/read?" + urlencode({"path": path, "agent": agent}),
            method="GET", headers=self._headers(cookie))

    def _write(self, path, content, cookie, agent):
        return self.app.request(
            "/api/workspace/write", method="POST", headers=self._headers(cookie),
            data=json.dumps({"path": path, "content": content, "agent": agent}))

    def _file(self, path, cookie, extra=None):
        from urllib.parse import quote, urlencode
        query = {"path": quote(os.path.realpath(path))}
        query.update(extra or {})
        return self.app.request("/api/file?" + urlencode(query), method="GET",
                                headers=self._headers(cookie))

    def _preview(self, path, cookie=None):
        from urllib.parse import quote
        url = "/preview/%s/%s" % (
            web_channel._encode_dir_token(os.path.dirname(path)),
            quote(os.path.basename(path)))
        headers = {"Host": "localhost:9899", "Origin": "http://localhost:9899"}
        if cookie:
            headers["Cookie"] = "cow_session=" + cookie
        return self.app.request(url, method="GET", headers=headers)

    @staticmethod
    def _denied(response):
        return int(response.status.split()[0]) in (403, 404)

    # -- 1. the owner is allowed ----------------------------------------

    def test_the_owner_reads_and_writes_its_private_workspace(self):
        read = self._read(self.private_file, self.owner_cookie, "private-agent")
        self.assertEqual(read.status, "200 OK", read.data)
        payload = json.loads(read.data.decode("utf-8"))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["content"], "carols-private-body")

        write = self._write(self.private_file, "owner wrote this",
                            self.owner_cookie, "private-agent")
        self.assertEqual(write.status, "200 OK", write.data)
        self.assertEqual(json.loads(write.data.decode("utf-8"))["status"], "success")
        with open(self.private_file, "rb") as handle:
            self.assertEqual(handle.read(), b"owner wrote this")

    def test_the_owner_downloads_and_previews_its_private_file(self):
        download = self._file(self.private_file, self.owner_cookie)
        self.assertEqual(download.status, "200 OK", download.data)
        self.assertEqual(download.data, b"carols-private-body")

        preview = self._preview(self.private_file, self.owner_cookie)
        self.assertEqual(preview.status, "200 OK", preview.data)
        self.assertIn(b"carols-private-body", preview.data)

    # -- 2. an administrator is refused ---------------------------------

    def test_an_administrator_cannot_read_write_download_or_preview_it(self):
        read = self._read(self.private_file, self.admin_cookie, "private-agent")
        self.assertTrue(self._denied(read), read.data)
        self.assertNotIn(b"carols-private-body", read.data)

        write = self._write(self.private_file, "defaced",
                            self.admin_cookie, "private-agent")
        self.assertTrue(
            self._denied(write)
            or json.loads(write.data.decode("utf-8")).get("status") == "error",
            write.data)
        with open(self.private_file, "rb") as handle:
            self.assertEqual(handle.read(), b"carols-private-body")

        download = self._file(self.private_file, self.admin_cookie)
        self.assertTrue(self._denied(download), download.data)
        self.assertNotIn(b"carols-private-body", download.data)

        preview = self._preview(self.private_file, self.admin_cookie)
        self.assertTrue(self._denied(preview), preview.data)
        self.assertNotIn(b"carols-private-body", preview.data)

    def test_another_member_is_refused_the_same_ways(self):
        for response in (
            self._read(self.private_file, self.other_cookie, "private-agent"),
            self._file(self.private_file, self.other_cookie),
            self._preview(self.private_file, self.other_cookie),
        ):
            self.assertTrue(self._denied(response), response.data)
            self.assertNotIn(b"carols-private-body", response.data)

    def test_naming_a_shared_agent_does_not_reach_the_private_path(self):
        """The declared Agent is a cross-check, never the authority."""
        response = self._read(self.private_file, self.admin_cookie, "shared-agent")
        self.assertTrue(self._denied(response), response.data)
        self.assertNotIn(b"carols-private-body", response.data)

    # -- 3. shared resources are unaffected ------------------------------

    def test_a_shared_agent_stays_reachable_for_members_and_admins(self):
        for cookie in (self.owner_cookie, self.other_cookie, self.admin_cookie):
            response = self._read(self.shared_file, cookie, "shared-agent")
            self.assertEqual(response.status, "200 OK", response.data)
            payload = json.loads(response.data.decode("utf-8"))
            self.assertEqual(payload["status"], "success", response.data)
            self.assertEqual(payload["content"], "public-body")

    def test_a_shared_download_and_preview_stay_open(self):
        for cookie in (self.owner_cookie, self.admin_cookie):
            download = self._file(self.shared_file, cookie)
            self.assertEqual(download.status, "200 OK", download.data)
        # ...and an anonymous preview of a shared file still works: the
        # capability token remains the authority for non-private content.
        preview = self._preview(self.shared_file)
        self.assertEqual(preview.status, "200 OK", preview.data)

    def test_releasing_ownership_restores_the_ordinary_surface(self):
        self.assertTrue(self._denied(
            self._read(self.private_file, self.admin_cookie, "private-agent")))

        self.service.make_agent_tenant_shared(
            agent_id="private-agent", actor_user_id=self.admin_id)

        response = self._read(self.private_file, self.admin_cookie, "private-agent")
        self.assertEqual(response.status, "200 OK", response.data)
        self.assertEqual(
            json.loads(response.data.decode("utf-8"))["content"],
            "carols-private-body")

    # -- 4. an identity-store failure denies -----------------------------

    class _FlakyService:
        """The real service, but ``get_agent_binding`` fails for one Agent.

        The handlers resolve the service through ``get_identity_service()``, so
        the fault has to be injected there — patching the fixture's own instance
        would leave the gate reading a healthy store and the test would pass for
        the wrong reason (the admin is refused anyway, being a non-owner).
        """

        def __init__(self, inner, bad_agent):
            self._inner = inner
            self._bad = bad_agent
            self.raised = False

        def get_agent_binding(self, agent_id):
            if agent_id == self._bad:
                self.raised = True
                raise IdentityServiceError("identity store unavailable",
                                           code="unavailable", status=503)
            return self._inner.get_agent_binding(agent_id)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _with_failing_store(self):
        flaky = self._FlakyService(self.service, "private-agent")
        patcher = patch("auth.service.get_identity_service", return_value=flaky)
        patcher.start()
        self.addCleanup(patcher.stop)
        return flaky

    def test_the_outage_is_actually_exercised(self):
        """Guard against the test passing because the fault never fired."""
        flaky = self._with_failing_store()

        response = self._file(self.private_file, self.admin_cookie)

        self.assertTrue(flaky.raised, "the failing lookup was never reached")
        self.assertTrue(self._denied(response), response.data)
        self.assertNotIn(b"carols-private-body", response.data)

    def test_an_identity_lookup_failure_denies_rather_than_allows(self):
        flaky = self._with_failing_store()

        response = self._file(self.private_file, self.admin_cookie)

        self.assertTrue(flaky.raised)
        self.assertTrue(self._denied(response), response.data)
        self.assertNotIn(b"carols-private-body", response.data)

    def test_a_broken_identity_store_does_not_widen_the_workspace_read(self):
        flaky = self._with_failing_store()

        response = self._read(self.private_file, self.admin_cookie,
                              "private-agent")

        self.assertTrue(flaky.raised)
        # The handler answers 200 with an error envelope rather than a status
        # code; what matters is that no content crosses and no success is
        # reported.
        self.assertNotIn(b"carols-private-body", response.data)
        self.assertNotIn(b'"status": "success"', response.data)

    def test_a_broken_identity_store_does_not_widen_the_preview(self):
        flaky = self._with_failing_store()

        response = self._preview(self.private_file, self.admin_cookie)

        self.assertTrue(flaky.raised)
        self.assertTrue(self._denied(response), response.data)
        self.assertNotIn(b"carols-private-body", response.data)


if __name__ == "__main__":
    unittest.main()
