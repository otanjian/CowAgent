# encoding:utf-8
"""End-to-end acceptance for copying a tenant's agents.

These are the spec-level scenarios of change ``copy-default-tenant-agents``,
driven through the real roster (files), the real ``identity.db`` and the real
runtime reload — no mocks of the owners involved. Where a unit test already
pins one step (``test_tenant_agent_provisioning`` for the orchestration,
``test_tenant_agent_copy_http`` for the HTTP boundary,
``test_agent_clone_binding`` for the migration), this file asserts the outcome
the operator and the tenant actually see:

* a target tenant can list and *use* the clones afterwards,
* the source tenant is untouched,
* clones are isolated to the target's own root and invisible cross-tenant,
* the two sides evolve independently,
* unselected candidates never appear,
* re-opening the database and re-running the copy neither duplicates nor
  orphans anything.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import web

from agent import team
from agent.admin import AgentAdminService
from agent.registry import AgentRegistry, get_agent_registry, set_agent_registry
from agent.tenant_provisioning import TenantAgentProvisioner
from auth.runtime import RequestContext
from auth.service import IdentityService
from channel.web import web_channel
from channel.web.web_channel import _require_session_owner, _resolve_tenant_default_agent

PLATFORM_PASSWORD = "Str0ngAdminPass"


class _AcceptanceBase(unittest.TestCase):
    """Two source agents in a source tenant, one empty target tenant, two members."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.instance = os.path.join(self.tmp, "instance")
        self.target_root = os.path.join(self.tmp, "tenants", "globex")
        os.makedirs(os.path.join(self.instance, "agents", "beta"))
        os.makedirs(self.target_root)
        with open(os.path.join(self.instance, "agents", "beta", "AGENT.md"),
                  "w", encoding="utf-8") as handle:
            handle.write("# Beta persona")
        # A credential-looking file must never travel with a clone.
        with open(os.path.join(self.instance, "agents", "beta", ".env"),
                  "w", encoding="utf-8") as handle:
            handle.write("SECRET=do-not-copy")

        self.config_path = os.path.join(self.tmp, "config.json")
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
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle)
        set_agent_registry(AgentRegistry.from_config(team.resolve(settings)))
        self.addCleanup(set_agent_registry, None)

        self.svc = IdentityService(self.db)
        self.source = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password=PLATFORM_PASSWORD,
            shared_root=self.instance, allow_weak=True)
        self.platform_id = self.svc.list_platform_users()[0]["id"]
        self.target = self.svc.create_tenant(
            actor_user_id=self.platform_id, code="globex", name="Globex",
            shared_root=self.target_root, admin_username="globexadmin",
            admin_display="Globex", admin_password="Str0ngPass9",
            recent_password=PLATFORM_PASSWORD)
        self.target_id = self.target["id"]
        self.svc.bind_agent(tenant_id=self.source["id"], agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.source["id"], agent_id="beta")

        self.source_member = self._member(self.source["id"], "acmeops")
        self.target_member = self._member(self.target_id, "globexops")

        self.admin = AgentAdminService(self.config_path)
        self.provisioner = TenantAgentProvisioner(self.svc, self.admin)
        # web.py's HTTPError appends to ctx.headers during construction; ensure a
        # request-shaped context exists so an assertion on the refusal works.
        web.ctx.headers = []

    # --- harness -------------------------------------------------------------

    def _member(self, tenant_id, username):
        self.svc.create_member(
            actor_user_id=self.platform_id, tenant_id=tenant_id,
            operation="create-new", username=username, display_name=username,
            temporary_password="Str0ngTemp1", roles=["tenant_admin"])
        first = self.svc.login(username, "Str0ngTemp1")
        self.svc.change_password(first.token, "Str0ngTemp1", "Str0ngFinal1")
        for item in self.svc.list_members(tenant_id)["items"]:
            if item["username"] == username:
                return item["user_id"]
        raise AssertionError("member %s was not created" % username)

    def _ctx(self, user_id, tenant_id):
        return RequestContext(
            user_id=user_id, username="member", display_name="Member",
            is_platform_admin=False, must_change_password=False,
            tenant_id=tenant_id, membership=None,
            permissions=set(self.svc.permissions_for(user_id, tenant_id)),
            is_tenant_admin=True)

    def _copy(self, selected, target_id=None):
        return self.provisioner.copy(
            target_tenant_id=target_id or self.target_id,
            source_agent_ids=selected, recent_password=PLATFORM_PASSWORD,
            actor_user_id=self.platform_id)

    def _reload(self):
        # Exactly what the HTTP handler does after a successful copy, so the
        # "usable without a restart" claim is observed rather than assumed.
        web_channel._reload_agent_runtime(self.admin)

    def _workspace(self, agent_id):
        return get_agent_registry().get(agent_id).workspace_path

    def _source_ids(self):
        return set(self.svc.tenant_agent_ids(self.source["id"]))

    def _as_member(self, ctx, fn, *args):
        with patch("auth.service.get_identity_service", lambda: self.svc):
            return fn(ctx, *args)


class VisibleAndUsableAfterCopyTests(_AcceptanceBase):
    """Scenario: a copy is only useful if the target can actually run the clone."""

    def test_the_target_member_can_list_and_use_the_clones(self):
        self._copy(["alpha", "beta"])
        self._reload()

        member = self._ctx(self.target_member, self.target_id)
        visible = self._as_member(member, web_channel._tenant_ids_for_context)
        self.assertIn("alpha-globex", visible)
        self.assertIn("beta-globex", visible)

        # No agent selected: the runtime must anchor the session to a clone the
        # member is actually allowed to use (the appointed default).
        anchored = self._as_member(member, _resolve_tenant_default_agent)
        self.assertIn(anchored, {"alpha-globex", "beta-globex"})
        self._as_member(member, _require_session_owner, "sess-1", None)

    def test_the_clone_is_loadable_after_the_runtime_reload(self):
        self._copy(["alpha"])
        self._reload()

        profile = get_agent_registry().get("alpha-globex")
        self.assertTrue(profile.enabled)
        self.assertTrue(str(profile.workspace_path).startswith(
            os.path.realpath(self.target_root)))

    def test_the_source_tenant_is_untouched(self):
        before_agents = {a["id"]: a for a in self.admin.snapshot()["agents"]}
        before_source = set(self._source_ids())
        before_default = self.svc.tenant_default_agent_id(self.source["id"])

        self._copy(["alpha", "beta"])
        self._reload()

        self.assertEqual(self._source_ids(), before_source)
        self.assertEqual(self.svc.tenant_default_agent_id(self.source["id"]),
                         before_default)
        after_agents = {a["id"]: a for a in self.admin.snapshot()["agents"]}
        for agent_id in ("alpha", "beta"):
            self.assertEqual(after_agents[agent_id]["enabled"],
                             before_agents[agent_id]["enabled"])
            self.assertEqual(after_agents[agent_id]["workspace"],
                             before_agents[agent_id]["workspace"])
        # The source's bindings still point at the source's own agents.
        for binding in self.svc.agents_for_tenant(self.source["id"]):
            self.assertIn(binding["agent_id"], {"alpha", "beta"})
            self.assertIsNone(binding["cloned_from_agent_id"])


class IsolationAcceptanceTests(_AcceptanceBase):
    """Scenario: a clone belongs to the target tenant and nowhere else."""

    def test_every_clone_workspace_is_inside_the_target_shared_root(self):
        self._copy(["alpha", "beta"])
        self._reload()

        agents_root = os.path.realpath(os.path.join(self.target_root, "agents"))
        for agent_id in ("alpha-globex", "beta-globex"):
            workspace = os.path.realpath(str(self._workspace(agent_id)))
            self.assertEqual(os.path.dirname(workspace), agents_root,
                             "the clone lives under the target tenant's own root")
            self.assertFalse(workspace.startswith(os.path.realpath(self.instance)),
                             "never inside the source tenant's root")

    def test_a_clone_carries_the_persona_but_no_credentials_or_history(self):
        self._copy(["beta"])
        self._reload()

        workspace = self._workspace("beta-globex")
        self.assertTrue((workspace / "AGENT.md").is_file(),
                        "the persona is what makes the clone useful")
        self.assertFalse((workspace / ".env").exists(),
                         "credentials are never copied")
        for forbidden in (workspace / "memory" / "long-term" / "index.db",
                          workspace / "sessions.db"):
            self.assertFalse(forbidden.exists(),
                             "runtime state must be left behind: %s" % forbidden)

    def test_the_clone_is_bound_only_to_the_target_tenant(self):
        self._copy(["alpha"])
        binding = self.svc.get_agent_binding("alpha-globex")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["tenant_id"], self.target_id)
        self.assertEqual(
            len([b for b in self.svc.list_agent_bindings()
                 if b["agent_id"] == "alpha-globex"]), 1,
            "exactly one binding, in the target tenant")

    def test_another_tenant_cannot_reach_the_clone(self):
        self._copy(["alpha"])

        source_member = self._ctx(self.source_member, self.source["id"])
        with self.assertRaises(web.HTTPError):
            self._as_member(source_member, _require_session_owner,
                            "sess-x", "alpha-globex")
        visible = self._as_member(source_member, web_channel._tenant_ids_for_context)
        self.assertNotIn("alpha-globex", visible)
        self.assertEqual(set(visible), self._source_ids())


class IndependentEvolutionTests(_AcceptanceBase):
    """Scenario: copying is not linking — each side is maintained on its own."""

    def test_editing_the_clone_leaves_the_source_alone(self):
        self._copy(["alpha"])
        self._reload()

        self.admin.update_agent("alpha-globex", name="Renamed Clone")
        self.admin.update_agent("alpha-globex", enabled=False)
        self._reload()

        clone = get_agent_registry().get("alpha-globex", require_enabled=False)
        source = get_agent_registry().get("alpha")
        self.assertEqual(clone.name, "Renamed Clone")
        self.assertFalse(clone.enabled)
        self.assertEqual(source.name, "Alpha")
        self.assertTrue(source.enabled)

    def test_editing_the_source_leaves_the_clone_alone(self):
        self._copy(["alpha"])
        self._reload()
        clone_workspace = self._workspace("alpha-globex")
        persona = clone_workspace / "AGENT.md"
        before = persona.read_text(encoding="utf-8") if persona.exists() else ""

        self.admin.update_agent("alpha", name="Renamed Source")
        with open(os.path.join(self.instance, "AGENT.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("# Alpha persona v2")
        self._reload()

        self.assertEqual(get_agent_registry().get("alpha").name, "Renamed Source")
        self.assertEqual(get_agent_registry().get("alpha-globex").name,
                         "Alpha")  # the clone kept the name it was copied with
        after = persona.read_text(encoding="utf-8") if persona.exists() else ""
        self.assertEqual(after, before,
                         "editing the source persona does not reach into the clone")


class SelectionFidelityTests(_AcceptanceBase):
    """Scenario: only what was checked is copied."""

    def test_unselected_candidates_are_absent_from_the_target(self):
        result = self._copy(["alpha"])
        self._reload()

        self.assertEqual([c["agent_id"] for c in result["copied_agent_ids"]],
                         ["alpha-globex"])
        self.assertEqual(self.svc.tenant_agent_ids(self.target_id), ["alpha-globex"])
        self.assertFalse(os.path.exists(
            os.path.join(self.target_root, "agents", "beta-globex")))
        self.assertNotIn("beta-globex", {a["id"] for a in self.admin.snapshot()["agents"]})

    def test_a_rerun_neither_duplicates_nor_orphans(self):
        self._copy(["alpha"])
        result = self._copy(["alpha", "beta"])
        self._reload()

        self.assertEqual([c["agent_id"] for c in result["skipped_agent_ids"]],
                         ["alpha-globex"])
        self.assertEqual([c["agent_id"] for c in result["copied_agent_ids"]],
                         ["beta-globex"])
        self.assertEqual(sorted(self.svc.tenant_agent_ids(self.target_id)),
                         ["alpha-globex", "beta-globex"])

        # No orphan: every roster agent in the target's clone family is bound,
        # and every binding points at a roster entry that exists.
        roster_ids = {a["id"] for a in self.admin.snapshot()["agents"]}
        clones = [a for a in roster_ids if a.endswith("-globex")]
        bound = set(self.svc.tenant_agent_ids(self.target_id))
        self.assertEqual(set(clones), bound)
        for binding in self.svc.list_agent_bindings():
            self.assertIn(binding["agent_id"], roster_ids,
                          "a binding must never outlive its agent")

    def test_reopening_the_database_and_rerunning_changes_nothing(self):
        self._copy(["alpha", "beta"])
        self._reload()
        before = sorted(self.svc.tenant_agent_ids(self.target_id))

        # A restart re-opens the same database and re-runs every migration.
        self.svc = IdentityService(self.db)
        self.provisioner = TenantAgentProvisioner(self.svc, self.admin)
        result = self._copy(["alpha", "beta"])

        self.assertEqual(sorted(self.svc.tenant_agent_ids(self.target_id)), before)
        self.assertEqual(result["copied"], 0)
        self.assertEqual(result["failed"], [])
        self.assertEqual(sorted(c["agent_id"] for c in result["skipped_agent_ids"]),
                         before)
        # The pre-existing bindings predate any copy and carry no provenance...
        self.assertIsNone(self.svc.get_agent_binding("alpha")["cloned_from_agent_id"])
        # ...while the clones still record exactly which source they came from.
        self.assertEqual(
            self.svc.get_agent_binding("alpha-globex")["cloned_from_agent_id"], "alpha")
        self.assertEqual(
            self.svc.clone_of(self.target_id, "beta")["agent_id"], "beta-globex")


if __name__ == "__main__":
    unittest.main()
