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
    _tenant_agents_admin_projection,
    _require_session_owner,
    _require_agent_action,
    _workbench_chat_readiness,
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

    def test_admin_projection_keeps_editable_fields_for_round_trip(self):
        """The console edits model/provider from this read; dropping them made
        the Agent form silently write "follow the global model" back on save."""
        self.reg = AgentRegistry(
            [AgentProfile(
                id="alpha", name="Alpha", workspace=os.path.join(self.tmp, "alpha"),
                model="deepseek-v4-flash", bot_type="deepseek",
                position="ERP 助手", category="erp", tags=["erp", "finance"],
                greeting="你好", persona_summary="专业", scene_id="erp",
            )],
            "alpha",
        )
        set_agent_registry(self.reg)
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], self.tid)

        with self._patch_svc():
            data = _tenant_agents_admin_projection(ctx)

        alpha = data["agents"][0]
        self.assertEqual(alpha["model"], "deepseek-v4-flash")
        self.assertEqual(alpha["bot_type"], "deepseek")
        self.assertEqual(alpha["position"], "ERP 助手")
        self.assertEqual(alpha["category"], "erp")
        self.assertEqual(list(alpha["tags"]), ["erp", "finance"])
        self.assertEqual(alpha["greeting"], "你好")
        self.assertEqual(alpha["persona_summary"], "专业")
        self.assertEqual(alpha["scene_id"], "erp")
        self.assertIn("is_default", alpha)
        self.assertIn("knowledge_mode", alpha)
        self.assertEqual(data["default_agent_id"], "alpha")
        # A tenant-facing read never leaks workspace paths, revision or the
        # instance-wide channel list.
        self.assertNotIn("workspace", alpha)
        self.assertNotIn("revision", data)
        self.assertNotIn("channel_instances", data)

    def test_workbench_projection_still_withholds_management_fields(self):
        """The minimal workbench read must not grow the editable fields."""
        self.reg = AgentRegistry(
            [AgentProfile(
                id="alpha", name="Alpha", workspace=os.path.join(self.tmp, "alpha"),
                model="deepseek-v4-flash", bot_type="deepseek")],
            "alpha",
        )
        set_agent_registry(self.reg)
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], self.tid)

        with self._patch_svc():
            alpha = _tenant_agents_projection(ctx)["agents"][0]

        self.assertNotIn("model", alpha)
        self.assertNotIn("bot_type", alpha)

    def test_bare_read_in_database_mode_returns_the_model_pin(self):
        """``GET /api/agents`` (management read) must round-trip the model pin,
        otherwise the console's save writes "follow global" back."""
        import json
        import channel.web.web_channel as web_channel
        from channel.web.web_channel import AgentsHandler

        self.reg = AgentRegistry(
            [AgentProfile(
                id="alpha", name="Alpha", workspace=os.path.join(self.tmp, "alpha"),
                model="deepseek-v4-flash", bot_type="deepseek")],
            "alpha",
        )
        set_agent_registry(self.reg)
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        ctx = _ctx(self.svc, self.root["id"], self.tid)

        class _Scope:
            def __enter__(self_inner):
                return ctx

            def __exit__(self_inner, *exc):
                return False

        with self._patch_svc(), \
                patch.object(web_channel, "_require_auth"), \
                patch.object(web_channel, "_require_read_permission"), \
                patch.object(web_channel, "_db_scope", return_value=_Scope()), \
                patch.object(web_channel.web, "header"), \
                patch.object(web_channel.web, "input",
                             return_value=web_channel.web.storage(view="")):
            data = json.loads(AgentsHandler().GET())

        self.assertEqual(data["status"], "success")
        alpha = next(a for a in data["agents"] if a["id"] == "alpha")
        self.assertEqual(alpha["model"], "deepseek-v4-flash")
        self.assertEqual(alpha["bot_type"], "deepseek")
        self.assertNotIn("workspace", alpha)

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

    def test_no_agent_multiple_bound_no_default_resolves_deterministically(self):
        """No agent + multiple bound + no configured default -> the tenant's
        deterministic default, never an ambiguity error (change
        personal-conversation-and-memory, task 2.3).

        Superseded contract: this used to raise 403 ``default agent ambiguous``.
        That is precisely the "a conversation forces me to pick an Agent"
        symptom, so the resolution is now stable and read-only instead.
        """
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            # Should not raise: members start chatting without choosing.
            _require_session_owner(self._ctx(), "sess", None)
            resolved = self.svc.resolved_default_agent_id(self.tid)
        # Deterministic and stable: the smallest bound id, not load order.
        self.assertEqual(resolved, "alpha")
        self.assertEqual(self.svc.resolved_default_agent_id(self.tid), resolved)

    def test_explicit_agent_must_be_bound_to_tenant(self):
        """An explicit agent_id not bound to the tenant is rejected."""
        with patch("auth.service.get_identity_service",
                   return_value=self.svc):
            with self.assertRaises(web.HTTPError):
                _require_session_owner(self._ctx(), "sess", "ghost-agent")


class SharedDefaultAgentReachabilityTests(unittest.TestCase):
    """A tenant-shared default Agent needs no per-resource grant.

    Rationale (change personal-conversation-and-memory): the console's promise is
    that a member opens the chat and just types — the server anchors the session
    to the tenant's default Agent. That promise is only kept if the member can
    actually *reach* that Agent. Requiring a hand-crafted ``agent:<id>`` grant for
    the very entry point every member shares makes "no Agent selection" a lie:
    the projection hides the Agent, the console falls back to an invented id, and
    the send fails.

    So the tenant's *resolved default* Agent — and only that one, and only while
    it is tenant-shared — is reachable with the functional permission alone. Every
    other Agent keeps the fine-grained grant model untouched.
    """

    def setUp(self):
        web.ctx.headers = []
        self.svc = _svc()
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        reg_root = tempfile.mkdtemp()
        self.reg = AgentRegistry(
            [
                AgentProfile(id="alpha", name="Alpha", workspace=os.path.join(reg_root, "alpha")),
                AgentProfile(id="beta", name="Beta", workspace=os.path.join(reg_root, "beta")),
            ],
            "alpha",
        )
        set_agent_registry(self.reg)

    def tearDown(self):
        set_agent_registry(None)

    def _ctx(self, *permissions, is_tenant_admin=False):
        return RequestContext(
            user_id="usr_plain", username="plain", display_name="Plain",
            is_platform_admin=False, must_change_password=False,
            tenant_id=self.tid, membership=None,
            permissions=set(permissions), is_tenant_admin=is_tenant_admin,
        )

    def _as_member(self, *permissions):
        """The default Agent is shared unless a test says otherwise."""
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        return self._ctx(*permissions)

    def _visible(self, ctx):
        with patch("auth.service.get_identity_service", return_value=self.svc):
            return {a["id"] for a in _tenant_agents_projection(ctx)["agents"]}

    # --- visibility -----------------------------------------------------

    def test_shared_default_is_visible_with_only_the_functional_read_grant(self):
        ctx = self._as_member("agent.read", "chat.use")
        self.assertEqual(self._visible(ctx), {"alpha"}, (
            "the tenant's shared default Agent was hidden from a member holding "
            "agent.read, so the console has nothing to anchor the chat to"))

    def test_relaxation_does_not_expose_other_tenant_agents(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        ctx = self._ctx("agent.read", "chat.use")
        # alpha is the deterministic default (smallest shared id); beta is not.
        self.assertEqual(self._visible(ctx), {"alpha"}, (
            "only the tenant's default Agent is reachable by default; a second "
            "Agent still needs an explicit agent:<id> grant"))

    def test_relaxation_requires_the_functional_read_permission(self):
        ctx = self._as_member("chat.use")
        self.assertEqual(self._visible(ctx), set(), (
            "an Agent became visible without agent.read — the relaxation must "
            "not hand out a resource the member has no permission to read"))

    def test_a_private_default_is_not_relaxed(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha",
                            private_owner_user_id=self.root["id"])
        ctx = self._ctx("agent.read", "chat.use")
        self.assertEqual(self._visible(ctx), set(), (
            "a private Agent is an exclusive resource; being the default must "
            "not leak it to other members"))

    # --- chat readiness (the console card's promise) --------------------

    def test_shared_default_is_chat_ready_for_a_member(self):
        ctx = self._as_member("agent.read", "agent.use", "chat.use")
        with patch("auth.service.get_identity_service", return_value=self.svc):
            self.assertEqual(_workbench_chat_readiness(ctx, "alpha"), (True, None), (
                "the console would render the default Agent as un-chattable, then "
                "the send path would agree — the member is locked out of the entry"))

    def test_chat_readiness_still_needs_the_functional_use_permission(self):
        ctx = self._as_member("agent.read", "chat.use")
        with patch("auth.service.get_identity_service", return_value=self.svc):
            self.assertEqual(_workbench_chat_readiness(ctx, "alpha")[1], "permission_denied")

    # --- the send path gate --------------------------------------------

    def test_member_may_use_the_shared_default_without_an_explicit_grant(self):
        ctx = self._as_member("agent.read", "agent.use")
        with patch("auth.service.get_identity_service", return_value=self.svc):
            _require_agent_action(ctx, "alpha", "use", "agent.use")

    def test_member_may_not_use_a_non_default_agent_without_a_grant(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tid, agent_id="beta")
        ctx = self._ctx("agent.read", "agent.use")
        with patch("auth.service.get_identity_service", return_value=self.svc):
            with self.assertRaises(web.HTTPError):
                _require_agent_action(ctx, "beta", "use", "agent.use")

    def test_relaxation_never_crosses_a_tenant_boundary(self):
        """Another tenant's shared default is still not reachable here."""
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="other", name="Other",
            shared_root="/s/other", admin_username="otheradmin",
            admin_display="Other", admin_password="Str0ngPass3",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=other["id"], agent_id="beta")
        ctx = self._as_member("agent.read", "agent.use")
        self.assertEqual(self.svc.resolved_default_agent_id(other["id"]), "beta")
        with patch("auth.service.get_identity_service", return_value=self.svc):
            with self.assertRaises(web.HTTPError):
                _require_agent_action(ctx, "beta", "use", "agent.use")


if __name__ == "__main__":
    unittest.main()
