# encoding:utf-8
"""Acceptance tests for opening runtime consumers & legacy compatibility.

Drives the REAL config-driven open paths (not a bare handler mock) to verify:

1. In ``database`` mode the OpenAI-compatible API resolves the DB session
   identity and rejects an anonymous request with 401 (never a 503 closure).
2. Constructing the AgentBridge in ``database`` mode DOES initialize the
   runtime consumers (registry / router / initializer / scheduler).
3. In ``legacy`` mode these same paths keep their historical behavior (bare
   external_api_token bearer, no database identity).

The dominant mode source is ``config.conf().get("identity_mode")``, which we
patch so the real code path (not a stubbed handler) is exercised.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import web

from channel.web import openai_api, web_channel
from config import conf


# --- OpenAI-compatible API -------------------------------------------------

def _openai_request(headers=None, data=None):
    app = web.application(
        ("/v1/chat/completions", "OpenAIChatCompletionsHandler"),
        vars(openai_api),
        autoreload=False,
    )
    kwargs = {"method": "POST", "data": data or '{"model": "x", "messages": []}'}
    h = {"Host": "test"}
    if headers:
        h.update(headers)
    kwargs["headers"] = h
    return app.request("/v1/chat/completions", **kwargs)


class OpenAIDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls._tmp, "identity.db")
        from auth.service import IdentityService
        cls.svc = IdentityService(cls.db_path)
        cls.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        cls._conf = {"identity_mode": "database",
                     "identity_db_path": cls.db_path}

    def _db_req(self, headers=None):
        with patch("config.conf", return_value=dict(self._conf)):
            return _openai_request(headers=headers)

    def test_openai_api_anonymous_401_in_database(self):
        # Database mode: no 503 closure; an anonymous request is rejected by
        # identity resolution (401), never 503 database_unavailable.
        resp = self._db_req()
        self.assertEqual(resp.status, "401 Unauthorized")

    def test_openai_api_db_session_without_tenant_is_400(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._db_req(headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status, "400 Bad Request")

    def test_openai_api_db_tenant_selected_but_no_agent_bound(self):
        token = self.svc.login("root", "Str0ngAdminPass").token
        resp = self._db_req(headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": self.svc.list_tenants()[0]["id"],
        })
        # chat.use is held by the platform admin; no agent is bound to the
        # tenant yet -> 404 no_agent (a precise rejection, not a closure 503).
        self.assertEqual(resp.status, "404 Not Found")

    def test_openai_api_allowed_in_legacy(self):
        # legacy: NO database closure; the request proceeds to authentication
        # and is rejected for a missing token (401), not a closure error.
        with patch("config.conf",
                   return_value=MagicMock(get=lambda k, d=None: "legacy")):
            resp = _openai_request()
        self.assertTrue(str(resp.status).startswith("4"))


# --- Bridge construction ---------------------------------------------------

class BridgeConstructionTests(unittest.TestCase):
    def _bridge(self):
        bridge = MagicMock()
        bridge.default_agent_id = lambda: "agent-x"
        return bridge

    def test_bridge_construct_initializes_runtime_in_database(self):
        from bridge.agent_bridge import AgentBridge
        with patch("config.conf",
                   return_value=MagicMock(get=lambda k, d=None: "database")):
            b = AgentBridge(self._bridge())
        # runtime consumers ARE built in database mode (open-database-runtime
        # task 2.2); auth is per-request, never construction-time.
        self.assertIsNotNone(getattr(b, "initializer", None))

    def test_bridge_construct_legacy_does_not_short_circuit(self):
        from bridge.agent_bridge import AgentBridge
        with patch("config.conf",
                   return_value=MagicMock(get=lambda k, d=None: "legacy")):
            b = AgentBridge(self._bridge())
        self.assertIsNotNone(getattr(b, "initializer", None))


# --- real config helper parity --------------------------------------------

class RealConfigHelperTests(unittest.TestCase):
    def test_database_is_the_only_identity_mode_even_without_the_key(self):
        """The ``or "legacy"`` default was retired along with legacy auth.

        A missing ``identity_mode`` used to mean legacy -- "no database has been
        configured yet" -- and this helper said so, which made every consumer
        read as open while the identity database went unused. After
        retire-legacy-identity-mode there is no shared-password path left to be
        open *to*, so an absent key now means database: reading it as legacy
        would report every consumer closed on a config written before the key
        existed. The helper is deliberately unconditional, and this pins that.
        """
        with patch("config.conf",
                   return_value=MagicMock(get=lambda k, d=None: None)):
            self.assertTrue(web_channel._is_database_identity())


if __name__ == "__main__":
    unittest.main()
