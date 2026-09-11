# encoding:utf-8
"""The tenant default Agent must resolve fail-closed (group 7, tasks 7.1-7.3).

A conversation has to be anchored to one Agent, and the user must not have to
pick it. That convenience is where the risk sits: if the resolution can land on
a stale, unbound or disabled Agent -- or, worse, on the process-global default
that may belong to another tenant -- then "just chat" becomes a cross-tenant
or privilege-escalation path.

The contract pinned here:

* a configured ``default_agent_id`` counts only while it is still *bound to this
  tenant and usable*; otherwise the answer falls back deterministically inside
  the tenant, and the reason is logged so the misconfiguration is diagnosable
  (7.1, 7.2);
* a disabled Agent is never resolved to, and a tenant whose every Agent is
  disabled resolves to nothing -- the caller refuses instead of borrowing
  anyone else's Agent (7.3);
* resolution never crosses a tenant boundary, whatever the global default says.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from auth.runtime import RequestContext
from channel.web.web_channel import _require_session_owner


def _svc():
    svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root="/s/acme", allow_weak=True)
    return svc


def _profile(agent_id, enabled=True):
    return AgentProfile(id=agent_id, name=agent_id, workspace=f"/w/{agent_id}",
                        enabled=enabled)


class DefaultAgentResolutionTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        set_agent_registry(None)

    def tearDown(self):
        set_agent_registry(None)

    # --- helpers ---------------------------------------------------------

    def _pin_roster(self, profiles, default=None):
        # The roster's own default must be enabled; pick the first usable one
        # unless the test says otherwise.
        default = default or next(
            (p.id for p in profiles if p.enabled), profiles[0].id)
        set_agent_registry(AgentRegistry(profiles, default))

    def _set_default(self, agent_id):
        with self.svc._tx() as con:
            con.execute("UPDATE tenants SET default_agent_id=? WHERE id=?",
                        (agent_id, self.tid))
            con.commit()

    def _ctx(self):
        return RequestContext(
            user_id=self.root["id"], username="root", display_name="Root",
            is_platform_admin=True, must_change_password=False,
            tenant_id=self.tid, membership=None,
            permissions={"agent.read", "agent.use"}, is_tenant_admin=True)

    def _bind(self, agent_id, owner=None, tenant=None):
        self.svc.bind_agent(tenant_id=tenant or self.tid, agent_id=agent_id,
                            private_owner_user_id=owner)

    # --- 7.1: a stale configured default falls back inside the tenant ---

    def test_a_configured_default_that_is_no_longer_bound_falls_back(self):
        self._pin_roster([_profile("ghost"), _profile("alpha"), _profile("beta")])
        self._bind("alpha")
        self._bind("beta")
        self._set_default("ghost")  # configured, but not bound here

        with self.assertLogs("log", level="WARNING") as captured:
            resolved = self.svc.resolved_default_agent_id(self.tid)

        self.assertEqual(resolved, "alpha")
        self.assertIn("ghost", "\n".join(captured.output))

    def test_a_configured_default_bound_to_another_tenant_is_not_borrowed(self):
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="other", name="Other",
            shared_root="/s/other", admin_username="otheradmin",
            admin_display="Other", admin_password="Str0ngPass3",
            recent_password="Str0ngAdminPass")
        self._pin_roster([_profile("beta")])
        self._bind("beta", tenant=other["id"])
        self._set_default("beta")  # stale: belongs to the other tenant

        self.assertIsNone(self.svc.resolved_default_agent_id(self.tid))

    def test_fallback_is_deterministic_and_independent_of_binding_order(self):
        self._pin_roster([_profile("alpha"), _profile("beta"), _profile("gamma")])
        for agent_id in ("gamma", "alpha", "beta"):
            self._bind(agent_id)
        first = self.svc.resolved_default_agent_id(self.tid)
        self.assertEqual(first, "alpha")
        self.assertEqual(self.svc.resolved_default_agent_id(self.tid), first)

    # --- 7.1: a disabled Agent is never resolved to ---------------------

    def test_a_disabled_configured_default_is_skipped(self):
        self._pin_roster([_profile("alpha", enabled=False), _profile("beta")])
        self._bind("alpha")
        self._bind("beta")
        self._set_default("alpha")

        with self.assertLogs("log", level="WARNING") as captured:
            resolved = self.svc.resolved_default_agent_id(self.tid)

        self.assertEqual(resolved, "beta")
        self.assertIn("alpha", "\n".join(captured.output))

    def test_a_disabled_agent_is_not_picked_by_the_fallback_either(self):
        self._pin_roster([_profile("alpha", enabled=False), _profile("beta")])
        self._bind("alpha")
        self._bind("beta")

        self.assertEqual(self.svc.resolved_default_agent_id(self.tid), "beta")

    def test_a_tenant_whose_only_agent_is_disabled_resolves_to_nothing(self):
        # The roster keeps a usable Agent (its own default must be enabled) but
        # this tenant is bound only to the disabled one.
        self._pin_roster([_profile("solo", enabled=False), _profile("spare")],
                         default="spare")
        self._bind("solo")

        self.assertIsNone(self.svc.resolved_default_agent_id(self.tid))

    # --- 7.3: refusal, not borrowing ------------------------------------

    def test_an_agent_less_tenant_is_refused_rather_than_defaulted(self):
        self._pin_roster([_profile("default")])  # the global default exists

        self.assertIsNone(self.svc.resolved_default_agent_id(self.tid))
        # web.py's HTTPError appends to ctx.headers while being constructed.
        web.ctx.headers = []
        with patch("auth.service.get_identity_service", return_value=self.svc):
            with self.assertRaises(web.HTTPError) as caught:
                _require_session_owner(self._ctx(), "sess-1", None)
        self.assertIn("403", str(caught.exception))

    def test_a_tenant_with_a_usable_agent_is_not_refused(self):
        self._pin_roster([_profile("alpha")])
        self._bind("alpha")

        with patch("auth.service.get_identity_service", return_value=self.svc):
            _require_session_owner(self._ctx(), "sess-1", None)  # must not raise


if __name__ == "__main__":
    unittest.main()
