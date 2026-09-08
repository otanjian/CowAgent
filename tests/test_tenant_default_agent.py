# encoding:utf-8
"""Tests for the tenant-bound default-agent projection (task 3.8).

Verifies that ``_tenant_agents_projection`` marks an Agent as ``is_default`` only
when it is the *calling tenant's* bound default agent — never the global default
— and that two tenants each bound to a different default Agent get their own
``is_default`` without crossing into the other's. Also covers
``_require_session_owner``'s preference for the tenant default agent.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.runtime import RequestContext
from auth.service import IdentityService
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from channel.web.web_channel import (
    _tenant_agents_projection,
    _require_session_owner,
)


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _svc():
    svc = IdentityService(_db())
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root="/s/acme", allow_weak=True)
    return svc


def _ctx(svc, user_id, tenant_id, is_admin=True, is_platform_admin=True):
    perms = set(svc.permissions_for(user_id, tenant_id))
    return RequestContext(
        user_id=user_id,
        username="root",
        display_name="Root",
        is_platform_admin=is_platform_admin,
        must_change_password=False,
        tenant_id=tenant_id,
        membership=None,
        permissions=perms | {"agent.read", "memory.read"},
        is_tenant_admin=is_admin,
    )


class TenantDefaultAgentProjectionTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        # Two agents, one global default (alpha) one not.
        self.tmp = tempfile.mkdtemp()
        self.reg = AgentRegistry(
            [
                AgentProfile(id="alpha", name="Alpha", workspace=os.path.join(self.tmp, "alpha")),
                AgentProfile(id="beta", name="Beta", workspace=os.path.join(self.tmp, "beta")),
            ],
            "alpha",
        )
        set_agent_registry(self.reg)

    def tearDown(self):
        set_agent_registry(None)

    def _patch_svc(self):
        return patch("auth.service.get_identity_service",
                     return_value=self.svc)

    def test_is_default_marks_the_tenant_bound_default_agent(self):
        """A tenant whose configured default is 'beta' marks beta, not the global default."""
        # Bind both agents to the tenant; set default_agent_id = beta.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        with self.svc._tx() as con:
            con.execute("UPDATE tenants SET default_agent_id=? WHERE id=?",
                        ("beta", self.tid))
            con.commit()
        ctx = _ctx(self.svc, self.root["id"], self.tid)
        with self._patch_svc():
            agents = _tenant_agents_projection(ctx)["agents"]
        by_id = {a["id"]: a for a in agents}
        self.assertTrue(by_id["beta"]["is_default"])
        self.assertFalse(by_id["alpha"]["is_default"])

    def test_two_tenants_each_mark_their_own_default(self):
        """Two tenants each bound to a different default mark their own, no cross."""
        # acme defaults to alpha, beta defaults to beta — distinct agents.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        with self.svc._tx() as con:
            con.execute("UPDATE tenants SET default_agent_id=? WHERE id=?",
                        ("alpha", self.tid))
            con.commit()
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root="/s/beta", admin_username="betaadmin",
            admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=other["id"], agent_id="beta")
        with self.svc._tx() as con:
            con.execute("UPDATE tenants SET default_agent_id=? WHERE id=?",
                        ("beta", other["id"]))
            con.commit()

        ctx_a = _ctx(self.svc, self.root["id"], self.tid)
        # The beta tenant admin (created by create_tenant) is NOT a platform admin,
        # so it must be scoped to its own tenant; pass is_platform_admin=False.
        beta_uid = self.svc._find_user_by_username("betaadmin")["id"]
        ctx_b = _ctx(self.svc, beta_uid, other["id"], is_platform_admin=False)

        with self._patch_svc():
            agents_a = {a["id"]: a for a in _tenant_agents_projection(ctx_a)["agents"]}
            agents_b = {a["id"]: a for a in _tenant_agents_projection(ctx_b)["agents"]}
        # Root is a platform admin, so it spans tenants and sees both agents —
        # but only acme's bound default (alpha) is marked default in acme's
        # context. betaadmin is an ordinary tenant admin: it sees only beta.
        self.assertEqual(set(agents_a), {"alpha", "beta"})
        self.assertEqual(set(agents_b), {"beta"})
        self.assertTrue(agents_a["alpha"]["is_default"])
        self.assertFalse(agents_a["beta"]["is_default"])
        self.assertTrue(agents_b["beta"]["is_default"])

    def test_fallback_to_single_bound_agent_when_no_default_configured(self):
        """No configured default + exactly one bound agent -> that agent is default."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], self.tid)
        with self._patch_svc():
            agents = _tenant_agents_projection(ctx)["agents"]
        by_id = {a["id"]: a for a in agents}
        self.assertTrue(by_id["alpha"]["is_default"])

    def test_no_default_when_multiple_bound_and_none_configured(self):
        """Multiple bound agents + no configured default -> nothing is project default."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        ctx = _ctx(self.svc, self.root["id"], self.tid)
        with self._patch_svc():
            agents = _tenant_agents_projection(ctx)["agents"]
        # global default is alpha, but tenant default is unambiguous -> neither.
        self.assertFalse(all(a["is_default"] for a in agents))

    def test_platform_admin_sees_all_agents_across_tenants(self):
        """A platform admin sees the whole roster, including unbound agents."""
        # Bind only alpha to the tenant; beta stays unbound.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], self.tid)
        with self._patch_svc():
            ids = {a["id"] for a in _tenant_agents_projection(ctx)["agents"]}
        # Both registered agents visible to the platform admin, even the unbound one.
        self.assertEqual(ids, {"alpha", "beta"})

    def test_platform_admin_without_tenant_sees_all_agents(self):
        """A platform admin with no tenant selection still sees every agent."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], None)
        with self._patch_svc():
            projection = _tenant_agents_projection(ctx)["agents"]
        self.assertEqual({a["id"] for a in projection}, {"alpha", "beta"})
        # No tenant selected -> no tenant-bound default is resolved, so no
        # agent is marked is_default; the console falls back to the global
        # default via its own preference logic.
        self.assertFalse(any(a["is_default"] for a in projection))


class SessionOwnerDefaultAgentTests(unittest.TestCase):
    """``_require_session_owner`` prefers the tenant default agent (task 3.8)."""

    def setUp(self):
        # web.py's HTTPError appends to ctx.headers during construction; ensure
        # it exists so a raised 403/404 is built without an attribute error.
        web.ctx.headers = []
        self.svc = _svc()
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.reg = AgentRegistry(
            [
                AgentProfile(id="alpha", name="Alpha", workspace=os.path.join(tempfile.mkdtemp(), "alpha")),
                AgentProfile(id="beta", name="Beta", workspace=os.path.join(tempfile.mkdtemp(), "beta")),
            ],
            "alpha",
        )
        set_agent_registry(self.reg)

    def tearDown(self):
        set_agent_registry(None)

    def _ctx(self, is_admin=True):
        perms = set(self.svc.permissions_for(self.root["id"], self.tid))
        return RequestContext(
            user_id=self.root["id"], username="root", display_name="Root",
            is_platform_admin=True, must_change_password=False, tenant_id=self.tid,
            membership=None, permissions=perms | {"history.read"},
            is_tenant_admin=is_admin,
        )

    def test_no_agent_uses_tenant_default_when_set(self):
        """No agent selected: tenant's configured default resolves without error."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        with self.svc._tx() as con:
            con.execute("UPDATE tenants SET default_agent_id=? WHERE id=?",
                        ("alpha", self.tid))
            con.commit()
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            # Should not raise (default is unambiguous via tenant default).
            _require_session_owner(self._ctx(), "sess", None)

    def test_no_agent_single_bound_still_allowed(self):
        """No agent + one bound agent still resolves (backward-compatible)."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            _require_session_owner(self._ctx(), "sess", None)

    def test_no_agent_multiple_bound_no_default_rejected(self):
        """No agent + multiple bound + no default -> rejected (ambiguous)."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            with self.assertRaises(web.HTTPError):
                _require_session_owner(self._ctx(), "sess", None)

    def test_explicit_agent_must_be_bound_to_tenant(self):
        """An explicit agent_id not bound to the tenant is rejected."""
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            with self.assertRaises(web.HTTPError):
                _require_session_owner(self._ctx(), "sess", "ghost-agent")


if __name__ == "__main__":
    unittest.main()
