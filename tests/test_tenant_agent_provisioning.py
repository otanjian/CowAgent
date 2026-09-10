# encoding:utf-8
"""Tests for the tenant agent provisioning orchestration.

Change ``copy-default-tenant-agents`` lets a platform administrator copy agents
from the default tenant into another tenant. ``agent/tenant_provisioning.py``
owns that orchestration: resolving the source tenant, listing candidates,
validating a selection as a whole, generating identities, copying one agent at a
time with idempotent skips and per-agent compensation, and appointing the target
tenant's default agent.

The layer spans two stores (the file-backed roster and ``identity.db``), so these
tests drive the real ``AgentAdminService`` and ``IdentityService`` against a
throwaway instance root. They are written before the module exists.
"""

import json
import os
import re
import shutil
import tempfile
import unittest

from agent import team
from agent.admin import AgentAdminError, AgentAdminService
from agent.registry import AgentRegistry, set_agent_registry
from agent.tenant_provisioning import (
    TenantAgentProvisioner,
    TenantProvisioningError,
    get_tenant_agent_provisioner,
)
from auth.service import IdentityService, IdentityServiceError

AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class _Base(unittest.TestCase):
    """One instance root holding two source agents, plus a clean target tenant."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.instance = os.path.join(self.tmp, "instance")
        self.target_root = os.path.join(self.tmp, "tenants", "globex")
        os.makedirs(os.path.join(self.instance, "agents", "beta"))
        os.makedirs(self.target_root)
        with open(os.path.join(self.instance, "agents", "beta", "AGENT.md"),
                  "w", encoding="utf-8") as handle:
            handle.write("# Beta persona")

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
        self._pin(settings)

        self.svc = IdentityService(os.path.join(self.tmp, "identity.db"))
        self.default_tenant = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.instance, allow_weak=True)
        self.root_id = self.svc.list_platform_users()[0]["id"]
        self.target = self.svc.create_tenant(
            actor_user_id=self.root_id, code="globex", name="Globex",
            shared_root=self.target_root, admin_username="globexadmin",
            admin_display="Globex", admin_password="Str0ngPass9",
            recent_password="Str0ngAdminPass")
        self.target_id = self.target["id"]
        self.svc.bind_agent(tenant_id=self.default_tenant["id"], agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.default_tenant["id"], agent_id="beta")

        self.admin = AgentAdminService(self.config_path)
        self.provisioner = TenantAgentProvisioner(self.svc, self.admin)

    def tearDown(self):
        set_agent_registry(None)

    def _pin(self, settings):
        set_agent_registry(AgentRegistry.from_config(team.resolve(settings)))

    def _copy(self, selected, target_id=None, provisioner=None,
              recent_password="Str0ngAdminPass"):
        return (provisioner or self.provisioner).copy(
            target_tenant_id=target_id or self.target_id,
            source_agent_ids=selected, recent_password=recent_password,
            actor_user_id=self.root_id)

    def _target_agent_ids(self):
        return set(self.svc.tenant_agent_ids(self.target_id))

    def _roster_ids(self):
        return {a["id"] for a in self.admin.snapshot()["agents"]}

    def _new_tenant(self, code, admin_username):
        return self.svc.create_tenant(
            actor_user_id=self.root_id, code=code, name=code.title(),
            shared_root=os.path.join(self.tmp, "tenants", code),
            admin_username=admin_username, admin_display=code.title(),
            admin_password="Str0ngPass9", recent_password="Str0ngAdminPass")


class SourceResolutionTests(_Base):
    def test_source_is_the_tenant_holding_the_global_default_agents_binding(self):
        source = self.provisioner.resolve_source(self.target_id)
        self.assertEqual(source.tenant_id, self.default_tenant["id"])
        self.assertEqual(source.resolved_by, "default_agent_binding")
        self.assertEqual(source.default_agent_id, "alpha")

    def test_source_falls_back_to_the_tenant_with_the_most_bindings(self):
        self.admin.create_agent("gamma", "Gamma")
        self.admin.update_agent("gamma", make_default=True)  # unbound default
        other = self._new_tenant("initech", "initechadmin")
        for agent_id in ("ghost-1", "ghost-2", "ghost-3"):
            self.svc.bind_agent(tenant_id=other["id"], agent_id=agent_id)
        self.svc.bind_agent(tenant_id=self.target_id, agent_id="ghost-4")

        source = self.provisioner.resolve_source(self.target_id)

        self.assertEqual(source.tenant_id, other["id"])
        self.assertEqual(source.resolved_by, "most_bindings")
        self.assertIsNone(source.default_agent_id)

    def test_source_is_unresolvable_with_no_agent_bindings(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "instance")
        os.makedirs(os.path.join(root, "agents"))
        config_path = os.path.join(tmp, "config.json")
        settings = {"agent_workspace": root, "default_agent_id": "alpha",
                    "agents": [{"id": "alpha", "name": "Alpha", "workspace": root,
                                "enabled": True}],
                    "channel_instances": []}
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle)
        self._pin(settings)
        self.addCleanup(set_agent_registry, None)
        svc = IdentityService(os.path.join(tmp, "identity.db"))
        svc.bootstrap(tenant_code="acme", tenant_name="Acme", admin_username="root",
                      admin_display="Root", admin_password="Str0ngAdminPass",
                      shared_root=root, allow_weak=True)
        root_id = svc.list_platform_users()[0]["id"]
        target = svc.create_tenant(
            actor_user_id=root_id, code="globex", name="Globex",
            shared_root=os.path.join(tmp, "tenants", "globex"),
            admin_username="globexadmin", admin_display="Globex",
            admin_password="Str0ngPass9", recent_password="Str0ngAdminPass")
        provisioner = TenantAgentProvisioner(svc, AgentAdminService(config_path))

        with self.assertRaises(TenantProvisioningError) as ctx:
            provisioner.resolve_source(target["id"])

        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("no tenant", str(ctx.exception))

    def test_reading_never_resolves_the_target_tenant_as_the_source(self):
        self.svc.bind_agent(tenant_id=self.target_id, agent_id="ghost")
        self.admin.create_agent("gamma", "Gamma")
        self.admin.update_agent("gamma", make_default=True)
        source = self.provisioner.resolve_source(self.target_id)
        self.assertNotEqual(source.tenant_id, self.target_id)


class CandidateReadingTests(_Base):
    def test_candidates_are_the_source_tenants_bound_agents_including_disabled(self):
        self.admin.archive_agent("beta")

        reading = self.provisioner.read_tenant(self.target_id)

        source = reading["copy_source"]
        self.assertEqual(source["tenant_id"], self.default_tenant["id"])
        candidates = {c["source_agent_id"]: c for c in source["candidates"]}
        self.assertEqual(set(candidates), {"alpha", "beta"})
        self.assertTrue(candidates["alpha"]["enabled"])
        self.assertFalse(candidates["beta"]["enabled"])
        self.assertTrue(candidates["alpha"]["is_default"])

    def test_candidates_mark_already_copied_agents_with_their_clone_id(self):
        self._copy(["alpha"])

        source = self.provisioner.read_tenant(self.target_id)["copy_source"]
        candidates = {c["source_agent_id"]: c for c in source["candidates"]}

        self.assertTrue(candidates["alpha"]["already_copied"])
        self.assertEqual(candidates["alpha"]["clone_agent_id"], "alpha-globex")
        self.assertFalse(candidates["beta"]["already_copied"])
        self.assertIsNone(candidates["beta"]["clone_agent_id"])

    def test_candidates_never_expose_host_paths(self):
        reading = self.provisioner.read_tenant(self.target_id)
        blob = json.dumps(reading)
        for leak in ("workspace", "shared_root", self.instance, self.target_root):
            self.assertNotIn(leak, blob)

    def test_reading_reports_an_empty_candidate_set_with_a_reason(self):
        self.admin.create_agent("gamma", "Gamma")
        self.admin.update_agent("gamma", make_default=True)  # unbound default
        stale = self._new_tenant("stale", "staleadmin")
        for agent_id in ("ghost-1", "ghost-2", "ghost-3"):
            self.svc.bind_agent(tenant_id=stale["id"], agent_id=agent_id)

        source = self.provisioner.read_tenant(self.target_id)["copy_source"]

        self.assertEqual(source["tenant_id"], stale["id"])
        self.assertEqual(source["candidates"], [])
        self.assertTrue(source["reason"])

    def test_reading_reports_an_unresolvable_source_without_raising(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "instance")
        os.makedirs(os.path.join(root, "agents"))
        config_path = os.path.join(tmp, "config.json")
        settings = {"agent_workspace": root, "default_agent_id": "alpha",
                    "agents": [{"id": "alpha", "name": "Alpha", "workspace": root,
                                "enabled": True}],
                    "channel_instances": []}
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle)
        self._pin(settings)
        self.addCleanup(set_agent_registry, None)
        svc = IdentityService(os.path.join(tmp, "identity.db"))
        svc.bootstrap(tenant_code="acme", tenant_name="Acme", admin_username="root",
                      admin_display="Root", admin_password="Str0ngAdminPass",
                      shared_root=root, allow_weak=True)
        root_id = svc.list_platform_users()[0]["id"]
        target = svc.create_tenant(
            actor_user_id=root_id, code="globex", name="Globex",
            shared_root=os.path.join(tmp, "tenants", "globex"),
            admin_username="globexadmin", admin_display="Globex",
            admin_password="Str0ngPass9", recent_password="Str0ngAdminPass")
        provisioner = TenantAgentProvisioner(svc, AgentAdminService(config_path))

        reading = provisioner.read_tenant(target["id"])

        self.assertIsNone(reading["copy_source"])
        self.assertTrue(reading["source_error"])

    def test_reading_lists_the_targets_own_bound_agents(self):
        self._copy(["alpha"])

        reading = self.provisioner.read_tenant(self.target_id)

        agents = {a["id"]: a for a in reading["agents"]}
        self.assertEqual(set(agents), {"alpha-globex"})
        self.assertEqual(agents["alpha-globex"]["name"], "Alpha")
        self.assertTrue(agents["alpha-globex"]["is_default"])
        self.assertNotIn("workspace", agents["alpha-globex"])


class SelectionValidationTests(_Base):
    def test_empty_selection_is_refused_without_writing(self):
        with self.assertRaises(TenantProvisioningError) as ctx:
            self._copy([])
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self._target_agent_ids(), set())
        self.assertEqual(self._roster_ids(), {"alpha", "beta"})

    def test_unknown_selection_id_rejects_the_whole_request(self):
        with self.assertRaises(TenantProvisioningError) as ctx:
            self._copy(["alpha", "ghost"])
        self.assertEqual(ctx.exception.status, 400)
        # The valid member of the selection must not have been copied either.
        self.assertEqual(self._target_agent_ids(), set())
        self.assertEqual(self._roster_ids(), {"alpha", "beta"})

    def test_unselected_candidates_are_not_copied(self):
        result = self._copy(["alpha"])
        self.assertEqual(result["selected"], 1)
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})
        self.assertNotIn("beta-globex", self._roster_ids())


class IdentityGenerationTests(_Base):
    def test_new_id_and_workspace_are_derived_from_source_and_target(self):
        result = self._copy(["alpha"])
        self.assertEqual(result["copied_agent_ids"],
                         [{"source_agent_id": "alpha", "agent_id": "alpha-globex"}])
        profile = next(a for a in self.admin.snapshot()["agents"]
                       if a["id"] == "alpha-globex")
        self.assertEqual(os.path.realpath(profile["workspace"]),
                         os.path.realpath(os.path.join(
                             self.target_root, "agents", "alpha-globex")))
        self.assertTrue(os.path.isfile(os.path.join(profile["workspace"], "AGENT.md")))

    def test_id_collision_appends_a_sequence_number(self):
        self.admin.create_agent("alpha-globex", "Squatter")

        result = self._copy(["alpha"])

        self.assertEqual(result["copied_agent_ids"],
                         [{"source_agent_id": "alpha", "agent_id": "alpha-globex-2"}])
        self.assertEqual(self._target_agent_ids(), {"alpha-globex-2"})

    def test_overlong_source_ids_are_truncated_to_the_registry_limit(self):
        long_id = "a" * 60
        self.admin.create_agent(long_id, "Long")
        self.svc.bind_agent(tenant_id=self.default_tenant["id"], agent_id=long_id)

        result = self._copy([long_id])

        new_id = result["copied_agent_ids"][0]["agent_id"]
        self.assertLessEqual(len(new_id), 64)
        self.assertRegex(new_id, AGENT_ID_RE)
        self.assertIn(new_id, self._roster_ids())


class IdempotencyTests(_Base):
    def test_rerunning_the_same_selection_only_skips(self):
        self._copy(["alpha"])

        result = self._copy(["alpha"])

        self.assertEqual((result["copied"], result["skipped"]), (0, 1))
        self.assertEqual(result["failed"], [])
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})
        self.assertEqual(
            len([a for a in self._roster_ids() if a.startswith("alpha-globex")]), 1)

    def test_mixed_selection_copies_only_the_missing(self):
        self._copy(["alpha"])

        result = self._copy(["alpha", "beta"])

        self.assertEqual((result["copied"], result["skipped"]), (1, 1))
        self.assertEqual(result["skipped_agent_ids"],
                         [{"source_agent_id": "alpha", "agent_id": "alpha-globex"}])
        self.assertEqual(self._target_agent_ids(), {"alpha-globex", "beta-globex"})

    def test_skip_survives_renaming_the_clone_in_the_target(self):
        self._copy(["alpha"])
        self.admin.update_agent("alpha-globex", name="Totally Different")

        result = self._copy(["alpha"])

        self.assertEqual((result["copied"], result["skipped"]), (0, 1))
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})


class DefaultAgentTakeoverTests(_Base):
    def test_empty_target_adopts_the_clone_of_the_source_default(self):
        result = self._copy(["alpha", "beta"])
        self.assertEqual(result["default_agent_id"], "alpha-globex")
        self.assertEqual(self.svc.tenant_default_agent_id(self.target_id), "alpha-globex")

    def test_empty_target_without_the_source_default_uses_the_first_copied(self):
        result = self._copy(["beta"])
        self.assertEqual(result["default_agent_id"], "beta-globex")
        self.assertEqual(self.svc.tenant_default_agent_id(self.target_id), "beta-globex")

    def test_non_empty_target_keeps_its_default(self):
        self.svc.bind_agent(tenant_id=self.target_id, agent_id="squatter")
        self.svc.set_tenant_default_agent(
            tenant_id=self.target_id, agent_id="squatter", actor_user_id=self.root_id)

        result = self._copy(["alpha"])

        self.assertIsNone(result["default_agent_id"])
        self.assertEqual(self.svc.tenant_default_agent_id(self.target_id), "squatter")

    def test_all_skipped_selection_does_not_touch_the_default(self):
        self._copy(["alpha"])          # target now non-empty, no default appointed
        self.svc.set_tenant_default_agent(
            tenant_id=self.target_id, agent_id="alpha-globex", actor_user_id=self.root_id)

        result = self._copy(["alpha"])

        self.assertEqual((result["copied"], result["skipped"]), (0, 1))
        self.assertIsNone(result["default_agent_id"])
        self.assertEqual(self.svc.tenant_default_agent_id(self.target_id), "alpha-globex")


class _FailingAdminService:
    """Delegates to the real service but blows up on one chosen source agent."""

    def __init__(self, inner, failing_source):
        self._inner = inner
        self._failing = failing_source

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def clone_agent(self, source_agent_id, agent_id, name=None, workspace=None,
                    revision=None):
        if source_agent_id == self._failing:
            raise AgentAdminError("cannot create workspace")
        return self._inner.clone_agent(source_agent_id, agent_id, name=name,
                                      workspace=workspace, revision=revision)


class _FailingBindService:
    def __init__(self, inner, failing_agent_id):
        self._inner = inner
        self._failing = failing_agent_id

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def bind_agent(self, **kwargs):
        if kwargs.get("agent_id") == self._failing:
            raise IdentityServiceError("database is locked")
        return self._inner.bind_agent(**kwargs)


class FailureCompensationTests(_Base):
    def test_a_failing_agent_is_reported_without_discarding_the_others(self):
        provisioner = TenantAgentProvisioner(
            self.svc, _FailingAdminService(self.admin, "beta"))

        result = self._copy(["alpha", "beta"], provisioner=provisioner)

        self.assertEqual(result["copied"], 1)
        self.assertEqual([f["source_agent_id"] for f in result["failed"]], ["beta"])
        self.assertTrue(result["failed"][0]["error"])
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})
        self.assertNotIn("beta-globex", self._roster_ids())

    def test_post_clone_failure_rolls_back_the_roster_and_the_workspace(self):
        provisioner = TenantAgentProvisioner(
            _FailingBindService(self.svc, "beta-globex"), self.admin)

        result = self._copy(["alpha", "beta"], provisioner=provisioner)

        self.assertEqual(result["copied"], 1)
        self.assertEqual([f["source_agent_id"] for f in result["failed"]], ["beta"])
        self.assertNotIn("beta-globex", self._roster_ids())
        self.assertFalse(os.path.exists(
            os.path.join(self.target_root, "agents", "beta-globex")))
        self.assertEqual(self._target_agent_ids(), {"alpha-globex"})

    def test_rerun_after_a_failure_completes_the_copy(self):
        provisioner = TenantAgentProvisioner(
            self.svc, _FailingAdminService(self.admin, "beta"))
        self._copy(["alpha", "beta"], provisioner=provisioner)

        result = self._copy(["alpha", "beta"])

        self.assertEqual((result["copied"], result["skipped"], result["failed"]),
                         (1, 1, []))
        self.assertEqual(self._target_agent_ids(), {"alpha-globex", "beta-globex"})

    def test_an_orphan_roster_entry_is_adopted_not_duplicated(self):
        """A crash between the roster write and the bind leaves an unbound clone."""
        orphan_ws = os.path.join(self.target_root, "agents", "alpha-globex")
        self.admin.clone_agent("alpha", "alpha-globex", workspace=orphan_ws)
        self.assertIsNone(self.svc.get_agent_binding("alpha-globex"))

        result = self._copy(["alpha"])

        self.assertEqual(result["copied_agent_ids"],
                         [{"source_agent_id": "alpha", "agent_id": "alpha-globex"}])
        self.assertEqual(self.svc.get_agent_binding("alpha-globex")["cloned_from_agent_id"],
                         "alpha")
        self.assertNotIn("alpha-globex-2", self._roster_ids())


class GuardTests(_Base):
    def test_target_equal_to_source_is_refused(self):
        before = {b["agent_id"]
                  for b in self.svc.list_agent_bindings(self.default_tenant["id"])}

        with self.assertRaises(TenantProvisioningError) as ctx:
            self._copy(["alpha"], target_id=self.default_tenant["id"])

        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(
            {b["agent_id"]
             for b in self.svc.list_agent_bindings(self.default_tenant["id"])}, before)
        self.assertEqual(self._roster_ids(), {"alpha", "beta"})

    def test_unknown_target_tenant_is_refused(self):
        with self.assertRaises(TenantProvisioningError) as ctx:
            self._copy(["alpha"], target_id="nope")
        self.assertEqual(ctx.exception.status, 404)

    def test_the_module_exposes_a_process_wide_provisioner(self):
        self.assertIsInstance(get_tenant_agent_provisioner(), TenantAgentProvisioner)


if __name__ == "__main__":
    unittest.main()
