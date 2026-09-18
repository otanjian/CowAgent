# encoding:utf-8
"""Plan 3.1 joint acceptance: the member personal console on the recovered surfaces.

Why this file exists
--------------------
Two changes are being delivered together: ``enable-member-personal-console`` (the
member's own private Agent, the supplied assistant, personal memory, personal
tool/skill parameters) and ``complete-database-capability-parity`` (the ten
recovered HTTP surfaces, the shared scheduler task authorization service, the
hardened personal-memory file access). Each change has its own suite, and both
can be green while the *combination* is broken, because the failure lives exactly
where the two meet:

    * a *disabled* private object silently revived — or silently serviced by the
      tenant's default Agent — by a scheduler entry point that resolves its Agent
      through the default instead of through the address it was given;
    * a member's personal write that widens their own grants, or that lands in
      another member's domain, or in the tenant's public configuration;
    * a capability withdrawal that reopens a surface instead of closing it, drops
      the owner/scope fields from the objects already stored, or restores the
      retired administrator bypass into a member's private content.

None of those are provable inside a single-change suite, so they are asserted
here, against the real application (``WebAppHarness``) and the real services.

Two deliberate omissions, both recorded in
``openspec/changes/archive/2026-09-15-complete-database-capability-parity/evidence/11-plan-3-1-joint-acceptance.md``:

    * **11.3 (WeChat provider acceptance) is not covered at all.** It requires the
      real provider acceptance from tasks 7.8-7.10, which cannot be produced in
      this environment; faking it would be worse than leaving it open. It is
      carried by ``complete-desktop-and-scan-real-acceptance`` (task 4.1).
    * ``test_11_1_the_console_delete_route_keeps_an_object_the_member_did_not_create``
      is the regression for defect **D3**, which used to be red on purpose: the
      projection advertised ``delete: false`` and ``PrivateAgentService`` refused,
      but ``POST /api/agents {action: delete}`` still erased the *system-supplied*
      assistant. The route now refuses on provenance (see the evidence file §4), so
      this test asserts the refusal instead of documenting the defect.
"""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import config as config_module
from agent.admin import AgentAdminService
from agent.private_agent import PrivateAgentService
from auth.service import IdentityServiceError
from channel.web import web_channel
from tests._helpers import WebAppHarness

#: The tenant's shared Agent, every member's default entry point.
SHARED = "shared-agent"
#: Alice's own private Agent (``user_created``).
PRIVATE = "alice-agent"
#: The assistant the system supplies Alice (``provisioned_assistant``).
ASSISTANT = "alice-assistant"
#: Another member's private Agent, which Alice must never reach.
PEER = "bob-agent"

MAIN_ENTRY = "MEMORY.md"


class _JointFixture(unittest.TestCase):
    """One tenant, one roster, and the members the two changes meet over.

    Alice holds the ordinary ``member`` role plus a personal role that grants one
    tool and one skill, so the personal parameter surface has something to grant
    and something to refuse. Bob is the plain member whose content the member's
    writes must leave alone.
    """

    #: Personal parameters carry a credential, which needs the master key the
    #: deployment sets; the drill sibling supplies it the same way.
    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

    def setUp(self):
        # Process-wide state a previous test can leave behind: the console's own
        # runtime reload *pins* the roster registry, and a pinned registry would
        # make this harness read another test's roster.
        from agent.registry import set_agent_registry
        set_agent_registry(None)
        self.addCleanup(set_agent_registry, None)
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_key)

        self.tmp = tempfile.mkdtemp(prefix="plan31-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.harness = WebAppHarness(os.path.join(self.tmp, "app"))
        self.addCleanup(self.harness.close)
        self.addCleanup(self._stop_schedulers)
        self.tenant = self.harness.tenant_id

        self.harness.add_agent(SHARED, PRIVATE, ASSISTANT, PEER)
        personal = self.harness.role(
            "personal-user", ["tool.read", "skill.read", "memory.read"],
            grants=[("tool", "builtin:read", "read"),
                    ("tool", "builtin:read", "execute"),
                    ("skill", "custom:writer", "use")])
        self.alice = self.harness.member("alice", ["member", personal["code"]])
        self.bob = self.harness.member("bob", ["member"])
        self.admin = self.harness.member("tadmin", ["tenant_admin"])
        self.alice_token = self.harness.login("alice")
        self.bob_token = self.harness.login("bob")
        self.admin_token = self.harness.login("tadmin")

        self._bind(PRIVATE, self.alice, "user_created")
        self._bind(ASSISTANT, self.alice, "provisioned_assistant")
        self._bind(PEER, self.bob, "user_created")
        self.harness.service.set_member_default_agent(
            tenant_id=self.tenant, user_id=self.alice, agent_id=ASSISTANT)

        # ``/api/agents`` resolves its roster through the deployment's
        # ``config.json`` while the rest of the app resolves it through
        # ``conf()``. In tests only ``conf()`` is redirected, so the route's own
        # factory has to be pointed at the same settings -- the *real* service
        # class, not a recorder, so the handler and the roster are both real.
        admin_patcher = patch.object(web_channel, "_agent_admin_service",
                                     self._admin_service)
        admin_patcher.start()
        self.addCleanup(admin_patcher.stop)

    # -- setup helpers -----------------------------------------------------

    def _admin_service(self):
        return AgentAdminService(os.path.join(self.tmp, "config.json"),
                                 settings=config_module.conf())

    def _bind(self, agent_id, owner, origin):
        self.harness.service.bind_agent(
            tenant_id=self.tenant, agent_id=agent_id,
            private_owner_user_id=owner, origin=origin)

    def _restore_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _stop_schedulers(self):
        """The runtime reload inside ``/api/agents`` starts real schedulers."""
        try:
            from agent.tools.scheduler.integration import reset_scheduler_services
            reset_scheduler_services()
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass
        try:
            from agent.tools.tool_manager import ToolManager
            ToolManager.reset_instances()
        except Exception:  # noqa: BLE001
            pass

    # -- assertion helpers -------------------------------------------------

    def _personal_agents(self, token):
        response = self.harness.get("/api/agents?view=personal", token=token)
        self.assertEqual(response.status, "200 OK", response.data)
        return self.harness.json(response)

    def _agents_post(self, token, **body):
        response = self.harness.post("/api/agents", body, token=token)
        return self.harness.json(response)

    def _roster(self):
        from agent import team
        with open(team.team_file(self.harness._settings), encoding="utf-8") as handle:
            return json.load(handle)

    def _roster_row(self, agent_id):
        rows = [row for row in self._roster()["agents"] if row["id"] == agent_id]
        return rows[0] if rows else {}

    def _roster_ids(self):
        return [row["id"] for row in self._roster()["agents"]]

    def _binding(self, agent_id):
        return self.harness.service.get_agent_binding(agent_id)

    def _task(self, agent_id, task_id):
        store = self.harness.scheduler_store(agent_id)
        return [task for task in store.list_tasks() if task["id"] == task_id][0]

    def _grants(self, user_id):
        """The member's effective role grants, as ``IdentityService`` reports them."""
        rows = self.harness.service.grants_for(user_id, self.tenant)
        return sorted((row["resource_kind"], row["resource_id"], row["action"])
                      for row in rows)

    def _role_grants(self):
        """The raw role-level grant rows ``IdentityService`` reads, tenant-wide."""
        return sorted(
            (row["resource_kind"], row["resource_id"], row["action"])
            for row in self.harness.service._store.execute(
                "SELECT resource_kind, resource_id, action FROM role_resource_grants"))

    def _may(self, user_id, agent_id, action, permission):
        return self.harness.service.check_resource_action(
            user_id, self.tenant, "agent", f"agent:{agent_id}", action,
            permission=permission)

    def _personal_root(self, user_id):
        from common import state_dir
        from common.runtime_identity import RuntimeIdentity
        return state_dir.user_root(RuntimeIdentity(
            agent_id=SHARED, user_id=user_id, tenant_id=self.tenant))

    def _switch(self, **overrides):
        self.harness._settings.update(overrides)
        return self.harness._settings

    def _memory(self, path, token, **params):
        query = ("?" + urlencode(params)) if params else ""
        return self.harness.get(path + query, token=token)

    def _list_tasks(self, token):
        response = self.harness.get("/api/scheduler", token=token)
        self.assertEqual(response.status, "200 OK", response.data)
        return self.harness.json(response)

    def _scheduler_tool(self, agent_id):
        """The ``scheduler`` tool exactly as the Agent hosting that Agent gets it."""
        from tests.test_scheduler_tool_dispatch import _tool
        from agent.tools.tool_manager import ToolManager
        ToolManager.reset_instances()
        return _tool(self.harness, agent_id)

    def _as_agent_owner(self, agent_id, user_id):
        from tests.test_scheduler_tool_dispatch import _as
        return _as(self.harness, agent_id, user_id)


class MemberOwnedObjectTests(_JointFixture):
    """11.1 — the objects a member owns, and the flows that may not revive them."""

    def test_11_1_the_owner_configures_toggles_and_deletes_what_they_own(self):
        listed = self._personal_agents(self.alice_token)
        by_id = {row["id"]: row for row in listed["agents"]}
        self.assertEqual(sorted(by_id), sorted([PRIVATE, ASSISTANT]))
        self.assertEqual(listed["scope"], "self")
        # The member's registered default is still the object they registered.
        self.assertEqual(listed["default_agent_id"], ASSISTANT)
        self.assertFalse(by_id[PRIVATE]["is_default"])
        self.assertEqual(by_id[PRIVATE]["scope"], "private")
        self.assertEqual(by_id[PRIVATE]["origin"], "user_created")
        self.assertTrue(by_id[PRIVATE]["actions"]["edit"])
        self.assertTrue(by_id[PRIVATE]["actions"]["delete"])

        # configure
        body = self._agents_post(self.alice_token, action="update", id=PRIVATE,
                                 name="Alice Helper")
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(self._roster_row(PRIVATE)["name"], "Alice Helper")

        # disable -> the object is no longer an entry point (the chat directory
        # drops it) but it stays findable in the *management* range, because the
        # owner has to be able to re-enable it from the same page (spec
        # ``agent-workbench``: 管理列表 SHALL 包含范围内停用对象 … 管理页仍可找到并
        # 重新启用本人对象), and ownership survives.
        body = self._agents_post(self.alice_token, action="update", id=PRIVATE,
                                 enabled=False)
        self.assertEqual(body.get("status"), "success", body)
        self.assertFalse(self._roster_row(PRIVATE).get("enabled", True))
        managed = {row["id"]: row for row in
                   self._personal_agents(self.alice_token)["agents"]}
        self.assertIn(PRIVATE, managed, "a stopped object must stay manageable")
        self.assertFalse(managed[PRIVATE]["can_chat"],
                         "a stopped object is not a chat entry point")
        self.assertEqual(managed[PRIVATE]["actions"], by_id[PRIVATE]["actions"])
        disabled = self._binding(PRIVATE)
        self.assertEqual(disabled["private_owner_user_id"], self.alice)
        self.assertEqual(disabled["tenant_id"], self.tenant)
        self.assertEqual(disabled["origin"], "user_created")

        # enable again
        body = self._agents_post(self.alice_token, action="update", id=PRIVATE,
                                 enabled=True)
        self.assertEqual(body.get("status"), "success", body)
        self.assertTrue(self._roster_row(PRIVATE).get("enabled", True))

        # delete: the roster entry and the tenant binding both go, and the
        # member's *other* object is not collateral
        body = self._agents_post(self.alice_token, action="delete", id=PRIVATE)
        self.assertEqual(body.get("status"), "success", body)
        self.assertIsNone(self._binding(PRIVATE))
        self.assertNotIn(PRIVATE, self._roster_ids())
        self.assertEqual(self._binding(ASSISTANT)["private_owner_user_id"], self.alice)
        self.assertIn(ASSISTANT, self._roster_ids())

    def test_11_1_the_supplied_assistant_is_not_an_object_the_member_created(self):
        """Owner maintenance reaches it; owner *deletion* does not."""
        row = {r["id"]: r for r in
               self._personal_agents(self.alice_token)["agents"]}[ASSISTANT]
        self.assertTrue(row["is_system_assistant"])
        self.assertEqual(row["origin"], "provisioned_assistant")
        self.assertTrue(row["actions"]["edit"])
        self.assertTrue(row["actions"]["enable"])
        self.assertFalse(row["actions"]["delete"],
                         "an object the member did not create is not theirs to erase")

        # The owner-facing maintenance service enforces the same rule the
        # projection advertises, and refuses before touching the binding.
        service = PrivateAgentService(self.harness.service)
        with self.assertRaises(IdentityServiceError) as refusal:
            service.delete_private_agent(user_id=self.alice, tenant_id=self.tenant,
                                         agent_id=ASSISTANT)
        self.assertEqual(refusal.exception.code, "forbidden")
        self.assertEqual(self._binding(ASSISTANT)["private_owner_user_id"], self.alice)
        self.assertIn(ASSISTANT, self._roster_ids())

        # ...while disabling it stays the owner's right, and the ownership facts
        # (and the member's own default pointer) survive the toggle.
        body = self._agents_post(self.alice_token, action="update", id=ASSISTANT,
                                 enabled=False)
        self.assertEqual(body.get("status"), "success", body)
        self.assertFalse(self._roster_row(ASSISTANT).get("enabled", True))
        binding = self._binding(ASSISTANT)
        self.assertEqual(binding["origin"], "provisioned_assistant")
        self.assertEqual(binding["private_owner_user_id"], self.alice)
        self.assertEqual(self.harness.service.member_default_agent_id(
            self.tenant, self.alice), ASSISTANT)

    def test_11_1_the_console_delete_route_keeps_an_object_the_member_did_not_create(self):
        """The projection and the owner service both say no; the route must too.

        This is the recorded blocker of group 11: ``POST /api/agents``
        ``{action: delete}`` erases the *system-supplied* assistant for its own
        owner, even though the projection advertises ``delete: false`` and
        ``PrivateAgentService`` refuses. The request below is exactly the one the
        personal console sends, and the property under test is the one the spec
        asks for: the object the member did not create survives.
        """
        advertised = {row["id"]: row for row in
                      self._personal_agents(self.alice_token)["agents"]}[ASSISTANT]
        self.assertFalse(advertised["actions"]["delete"])

        body = self._agents_post(self.alice_token, action="delete", id=ASSISTANT)

        # The refusal carries a machine code, so the console can tell the member
        # *why* instead of rendering a generic failure (the read surfaces of this
        # change use the same convention).
        self.assertEqual(body.get("code"), "forbidden", body)
        self.assertEqual(body.get("status"), "error", body)

        self.assertIsNotNone(
            self._binding(ASSISTANT),
            "the supplied assistant was erased by its own owner; the route "
            "answered %r" % (body,))
        self.assertIn(ASSISTANT, self._roster_ids(),
                      "the supplied assistant's roster row was erased; the route "
                      "answered %r" % (body,))
        self.assertEqual(self.harness.service.member_default_agent_id(
            self.tenant, self.alice), ASSISTANT)

    def test_11_1_a_disabled_object_is_not_revived_by_a_scheduled_task(self):
        self._agents_post(self.alice_token, action="update", id=ASSISTANT,
                          enabled=False)
        self.assertFalse(self._roster_row(ASSISTANT).get("enabled", True))
        # Disabled is not "replaced": the resolver stops offering it, and the
        # pointer the member registered is left exactly as they registered it.
        self.assertEqual(self.harness.service.member_default_agent_id(
            self.tenant, self.alice), ASSISTANT)
        self.assertNotEqual(self.harness.service.resolved_default_agent_id(
            self.tenant, self.alice), ASSISTANT)

        self.harness.personal_task(ASSISTANT, self.alice, id="t-off", name="t-off")
        self.harness.personal_task(SHARED, self.alice, id="t-shared", name="t-shared")
        self.harness.personal_task(PRIVATE, self.alice, id="t-private", name="t-private")

        listed = self._list_tasks(self.alice_token)
        self.assertEqual(sorted(task["id"] for task in listed["tasks"]),
                         ["t-private", "t-shared"])
        # the disabled object's task is not relabelled onto another Agent
        for task in listed["tasks"]:
            self.assertNotEqual(task["agent_id"], ASSISTANT, task)
        self.assertNotIn(ASSISTANT, [row["agent_id"] for row in listed["agents"]])
        self.assertIn(SHARED, [row["agent_id"] for row in listed["agents"]])

        # Running it refuses, and the runtime is resolved for the *addressed*
        # Agent only -- never for the tenant default that happens to be live.
        calls = []

        def recorder(agent_id):
            calls.append(agent_id)
            return None

        with patch.object(web_channel, "_scheduler_service_for_web", recorder):
            response = self.harness.post(
                "/api/scheduler/run",
                {"agent_id": ASSISTANT, "task_id": "t-off"},
                token=self.alice_token)
        self.assertEqual(response.status, "503 Service Unavailable", response.data)
        self.assertEqual(self.harness.json(response).get("code"), "run_unavailable")
        self.assertEqual(calls, [ASSISTANT],
                         "the run must address the disabled Agent, not the default")

        # A live runtime exists for the shared default Agent, and the disabled
        # Agent's task was not serviced through it: nothing ran anywhere.
        from agent.registry import get_agent_registry
        from agent.tools.scheduler.integration import (
            get_scheduler_service, init_scheduler)
        init_scheduler(SimpleNamespace(agent_registry=get_agent_registry()),
                       agent_id=SHARED)
        self.assertIsNotNone(get_scheduler_service(agent_id=SHARED))
        self.assertIsNone(get_scheduler_service(agent_id=ASSISTANT))
        off = self._task(ASSISTANT, "t-off")
        self.assertTrue(off["enabled"])
        self.assertFalse(off.get("last_run_at"))
        self.assertEqual([task["id"] for task in
                          self.harness.scheduler_store(SHARED).list_tasks()],
                         ["t-shared"])

    def test_11_1_creating_a_task_addresses_the_hosting_object_and_stays_personal(self):
        self._agents_post(self.alice_token, action="update", id=ASSISTANT,
                          enabled=False)

        tool = self._scheduler_tool(ASSISTANT)
        with self._as_agent_owner(ASSISTANT, self.alice):
            result = tool.execute({"action": "create", "name": "nightly",
                                   "message": "hi", "schedule_type": "interval",
                                   "schedule_value": "3600"})
        self.assertEqual(result.status, "success", result.result)

        stored = self.harness.scheduler_store(ASSISTANT).list_tasks()
        self.assertEqual(len(stored), 1, stored)
        task = stored[0]
        self.assertEqual(task["scope"], "personal")
        self.assertEqual(task["owner"]["user_id"], self.alice)
        self.assertEqual(task["owner"]["tenant_id"], self.tenant)
        self.assertEqual(task["owner"]["agent_id"], ASSISTANT)
        self.assertTrue(task["next_run_at"])
        # the same write never lands in the tenant's shared Agent
        self.assertEqual(self.harness.scheduler_store(SHARED).list_tasks(), [])
        # and creating it did not silently switch the object back on
        self.assertFalse(self._roster_row(ASSISTANT).get("enabled", True))

    def test_11_1_neither_flow_widens_the_members_resource_grants(self):
        grants_before = self._grants(self.alice)
        role_grants_before = self._role_grants()
        tenant_grants_before = sorted(
            (row["resource_kind"], row["resource_id"], row["action"])
            for row in self.harness.service.tenant_resource_grants(self.tenant))
        self.assertFalse(self._may(self.alice, SHARED, "read", "agent.read"),
                         "an Agent the member holds no grant for stays out of reach")

        self._agents_post(self.alice_token, action="update", id=PRIVATE,
                          name="Renamed")
        self._agents_post(self.alice_token, action="update", id=PRIVATE,
                          enabled=False)
        self._agents_post(self.alice_token, action="update", id=PRIVATE,
                          enabled=True)
        tool = self._scheduler_tool(PRIVATE)
        with self._as_agent_owner(PRIVATE, self.alice):
            tool.execute({"action": "create", "name": "nightly", "message": "hi",
                          "schedule_type": "interval", "schedule_value": "3600"})
        saved = self.harness.post(
            "/api/memory/personal",
            {"action": "save", "id": MAIN_ENTRY, "content": "ALICE\n"},
            token=self.alice_token)
        self.assertEqual(self.harness.json(saved).get("status"), "success", saved.data)
        self._agents_post(self.alice_token, action="delete", id=PRIVATE)

        self.assertEqual(self._grants(self.alice), grants_before,
                         "maintaining your own object must not write a role grant")
        self.assertEqual(self._role_grants(), role_grants_before,
                         "no row may be written into role_resource_grants")
        self.assertEqual(
            sorted((row["resource_kind"], row["resource_id"], row["action"])
                   for row in self.harness.service.tenant_resource_grants(self.tenant)),
            tenant_grants_before)
        self.assertFalse(self._may(self.alice, SHARED, "read", "agent.read"))
        self.assertFalse(self._may(self.alice, SHARED, "edit", "agent.edit"))


class PersonalConfigurationTests(_JointFixture):
    """11.2 — personal parameters and personal memory over the hardened service."""

    def test_11_2_personal_parameters_are_owner_scoped_and_the_catalog_is_not(self):
        # The personal parameters live on the resource row of the *shared*
        # 工具与技能 page (task 5.5 of unify-console-by-data-scope), so the grant
        # that puts the row there is the resource ``read`` — exactly the action
        # set the console's own grant picker emits for a tool
        # (``identity-admin.js``: read/execute/configure). The ``execute`` grant
        # is what the owner-scoped write answers to.
        granted = self.harness.json(
            self.harness.get("/api/tools", token=self.alice_token))
        rows = {row["resource_id"]: row for row in granted["tools"]}
        self.assertIn("builtin:read", rows)
        personal = rows["builtin:read"]["personal"]
        self.assertFalse(personal["configured"])
        self.assertEqual(personal["params"], {})
        self.assertTrue(personal["actions"]["configure"])

        # An item the *catalog* carries but this member holds no grant for is
        # refused, not silently configured.
        refusal = self.harness.post(
            "/api/tools",
            {"action": "save-personal", "resource_id": "builtin:scheduler",
             "params": {"timeout": 1}},
            token=self.alice_token)
        self.assertEqual(refusal.status, "403 Forbidden", refusal.data)
        self.assertEqual(self.harness.json(refusal).get("code"), "forbidden")

        saved = self.harness.post(
            "/api/tools",
            {"action": "save-personal", "resource_id": "builtin:read",
             "params": {"timeout": 9}, "secret": "alice-token"},
            token=self.alice_token)
        self.assertEqual(saved.status, "200 OK", saved.data)
        config = self.harness.json(saved)["config"]
        self.assertEqual(config["user_id"], self.alice)
        self.assertEqual(config["params"], {"timeout": 9})
        self.assertTrue(config["credential_id"])

        reread = self.harness.json(
            self.harness.get("/api/tools", token=self.alice_token))
        rows = {row["resource_id"]: row for row in reread["tools"]}
        self.assertTrue(rows["builtin:read"]["personal"]["configured"])
        self.assertTrue(rows["builtin:read"]["personal"]["has_credential"])

        # The credential is the member's own, never a public one.
        owned = self.harness.service._store.execute(
            "SELECT owner_user_id FROM credentials WHERE id=?",
            (config["credential_id"],))
        self.assertEqual(owned[0]["owner_user_id"], self.alice)

        # Nothing of the member's configuration is visible to another member:
        # Bob holds no tool grant, so his catalogue carries no row at all (and
        # certainly not Alice's parameters).
        peer = self.harness.json(
            self.harness.get("/api/tools", token=self.bob_token))
        self.assertEqual([row for row in peer["tools"]
                          if row.get("personal")], [])

    def test_11_2_personal_memory_takes_relative_ids_and_refuses_hostile_ones(self):
        # a member maintaining their own surface, read back through the memory
        # browse surface this change recovered
        saved = self.harness.post(
            "/api/memory/personal",
            {"action": "save", "id": MAIN_ENTRY, "content": "ALICE-NOTE\n"},
            token=self.alice_token)
        self.assertEqual(self.harness.json(saved).get("status"), "success", saved.data)
        nested = self.harness.post(
            "/api/memory/personal",
            {"action": "save", "id": "memory/notes.md", "content": "nested\n"},
            token=self.alice_token)
        self.assertEqual(self.harness.json(nested).get("status"), "success",
                         nested.data)

        listing = self.harness.json(
            self._memory("/api/memory", self.alice_token, scope="personal"))
        self.assertEqual(sorted(row["id"] for row in listing["list"]),
                         [MAIN_ENTRY, "memory/notes.md"])
        content = self.harness.json(
            self._memory("/api/memory/content", self.alice_token,
                         scope="personal", filename=MAIN_ENTRY))
        self.assertEqual(content["content"], "ALICE-NOTE\n")
        self.assertTrue(content["read_only"])

        # the address grammar is the only way to name an entry: no absolute
        # path, no traversal, no extension the store does not own
        for hostile in ("../../etc/passwd", "/etc/passwd", "memory/../MEMORY.md",
                        "notes.md", "memory/x.txt"):
            response = self.harness.post(
                "/api/memory/personal",
                {"action": "save", "id": hostile, "content": "x"},
                token=self.alice_token)
            self.assertEqual(self.harness.json(response).get("code"), "invalid_entry",
                             (hostile, response.data))
        read = self._memory("/api/memory/personal/content", self.alice_token,
                            id="../../etc/passwd")
        self.assertEqual(self.harness.json(read).get("code"), "invalid_entry",
                         read.data)

        # a symlinked entry is refused, and the file it points at is untouched
        outside = os.path.join(self.tmp, "outside.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("SECRET-OUTSIDE\n")
        entry = self._personal_root(self.bob) / MAIN_ENTRY
        entry.parent.mkdir(parents=True, exist_ok=True)
        if entry.exists() or entry.is_symlink():
            entry.unlink()
        os.symlink(outside, str(entry))
        read = self._memory("/api/memory/personal/content", self.bob_token,
                            id=MAIN_ENTRY)
        self.assertEqual(read.status, "403 Forbidden", read.data)
        self.assertEqual(self.harness.json(read).get("code"), "unsafe_path")
        write = self.harness.post(
            "/api/memory/personal",
            {"action": "save", "id": MAIN_ENTRY, "content": "overwritten"},
            token=self.bob_token)
        self.assertEqual(write.status, "403 Forbidden", write.data)
        self.assertEqual(self.harness.json(write).get("code"), "unsafe_path")
        with open(outside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "SECRET-OUTSIDE\n")

    def test_11_2_a_members_writes_leave_others_and_the_public_state_alone(self):
        from agent import team
        with open(team.team_file(self.harness._settings), encoding="utf-8") as handle:
            roster_before = handle.read()
        public_memory = os.path.join(
            self.harness.service.tenant_shared_root(self.tenant), MAIN_ENTRY)
        os.makedirs(os.path.dirname(public_memory), exist_ok=True)
        with open(public_memory, "w", encoding="utf-8") as handle:
            handle.write("TENANT-PUBLIC\n")
        public_credentials_before = self.harness.service._store.execute(
            "SELECT COUNT(*) c FROM credentials WHERE owner_user_id IS NULL")[0]["c"]

        # the other member's own memory, seeded through the same surface
        self.harness.post("/api/memory/personal",
                          {"action": "save", "id": MAIN_ENTRY, "content": "BOB\n"},
                          token=self.bob_token)
        bob_root = self._personal_root(self.bob)

        # the member's *personal* writes: their own memory and parameters. None
        # of them may touch the tenant's public configuration.
        self.harness.post("/api/memory/personal",
                          {"action": "save", "id": MAIN_ENTRY,
                           "content": "ALICE\n"}, token=self.alice_token)
        personal_write = self.harness.post(
            "/api/tools",
            {"action": "save-personal", "resource_id": "builtin:read",
             "params": {"tone": "brief"}}, token=self.alice_token)
        self.assertEqual(personal_write.status, "200 OK", personal_write.data)

        with open(team.team_file(self.harness._settings), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), roster_before,
                             "a member's personal write must not edit the roster")
        with open(public_memory, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "TENANT-PUBLIC\n")
        self.assertEqual(
            self.harness.service._store.execute(
                "SELECT COUNT(*) c FROM credentials WHERE owner_user_id IS NULL"
            )[0]["c"], public_credentials_before,
            "a personal save must not create or rewrite a public credential")

        # the member maintaining their own Agent edits exactly their own roster
        # row: no row appears or disappears, and no tenant-wide default moves
        self._agents_post(self.alice_token, action="update", id=PRIVATE,
                          name="Renamed")
        roster_after = self._roster()
        self.assertEqual(sorted(row["id"] for row in roster_after["agents"]),
                         sorted(agent_id for agent_id in
                                (SHARED, PRIVATE, ASSISTANT, PEER)))
        renamed = [row for row in roster_after["agents"] if row["id"] == PRIVATE][0]
        self.assertEqual(renamed["name"], "Renamed")
        self.assertEqual(roster_after["default_agent_id"],
                         json.loads(roster_before)["default_agent_id"])
        with open(public_memory, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "TENANT-PUBLIC\n")

        # the other member's domain is untouched, and still only theirs
        with open(os.path.join(str(bob_root), MAIN_ENTRY), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "BOB\n")
        bob_listing = self.harness.json(
            self._memory("/api/memory", self.bob_token, scope="personal"))
        self.assertEqual([row["id"] for row in bob_listing["list"]], [MAIN_ENTRY])
        personal_rows = self.harness.service._store.execute(
            "SELECT user_id, resource_id FROM personal_resource_configs"
            " WHERE tenant_id=?", (self.tenant,))
        self.assertEqual({row["user_id"] for row in personal_rows}, {self.alice},
                         "only the member's own configuration rows may exist")

        # the tenant's shared memory stays readable by an administrator, and is
        # not what the member's write just modified: still exactly the public
        # entry, at the public size
        shared = self._memory("/api/memory", self.admin_token, agent_id=SHARED)
        self.assertEqual(shared.status, "200 OK", shared.data)
        shared_body = self.harness.json(shared)
        self.assertEqual(shared_body["scope"], "shared")
        self.assertEqual(
            [(entry["filename"], entry["size"]) for entry in shared_body["list"]],
            [(MAIN_ENTRY, len("TENANT-PUBLIC\n"))], shared_body)


class StagedShutdownTests(_JointFixture):
    """11.5 — withdraw the switches, disable an object, and re-read everything."""

    def test_11_5_withdrawal_closes_surfaces_without_losing_stored_facts(self):
        self.harness.personal_task(PRIVATE, self.alice, id="t-private",
                                   name="t-private")
        before = {agent_id: self._binding(agent_id) for agent_id in (PRIVATE, ASSISTANT)}
        self.assertEqual(config_module.conf().get("identity_mode"), "database")

        self._switch(member_personal_console=False, personal_channel_runtime=False)

        # 1. the surfaces report the withdrawal, with the switch that did it —
        #    not a legacy fallback, and not "your menu is unauthorized".
        #    The five ``personal.*`` pages were retired with the personal console
        #    (change unify-console-by-data-scope, tasks 8.1/8.8): the member now
        #    reaches the *formal* page that carries the same objects, and that
        #    page reports the member's own switches itself. So the withdrawal is
        #    still reported by the switch that caused it — a page that no longer
        #    exists answering "capability_disabled" would be evidence of nothing.
        pages = self.harness.service.context_for_tenant(
            self.alice_token, self.tenant)["console_pages"]
        for retired in ("personal.agents", "personal.channels", "personal.memory",
                        "personal.tools", "personal.skills"):
            self.assertNotIn(retired, pages, retired)

        # 渠道实例：正式页带着成员自己的两个开关与三个分离状态（任务 8.1），
        # 开关关掉后，它拒掉的"开通"不再被提供，而"关闭"类动作仍然可达——
        # 撤权不得把成员锁在一个他自己关不掉的连接里。
        channels = pages["admin.channels"]
        self.assertEqual(channels["switches"]["member_personal_console"], False,
                         channels)
        self.assertEqual(channels["switches"]["personal_channel_onboarding"], True,
                         channels)
        self.assertFalse(channels["actions"]["create"], channels)
        self.assertTrue(channels["actions"]["update"], channels)

        # 记忆：撤权只拒绝**写入**，读到的既有事实不被隐藏——这正是本类的标题
        # （"without losing stored facts"）在投影面的那一半。
        memory = pages["admin.memory"]
        self.assertTrue(memory["read_allowed"], memory)
        self.assertFalse(memory.get("menu_denied", False), memory)

        # 撤权不是"你的菜单没权限"：成员自己的面上没有一条以权限为由的拒绝。
        for page in ("admin.channels", "admin.memory"):
            self.assertNotEqual(pages[page].get("reason"), "no_permission", page)
            self.assertFalse(pages[page].get("menu_denied", False), page)

        # 2. the objects already stored keep owner and scope
        for agent_id, binding in before.items():
            after = self._binding(agent_id)
            self.assertEqual(after["tenant_id"], binding["tenant_id"], agent_id)
            self.assertEqual(after["private_owner_user_id"],
                             binding["private_owner_user_id"], agent_id)
            self.assertEqual(after["origin"], binding["origin"], agent_id)
        self.assertEqual(self.harness.service.member_default_agent_id(
            self.tenant, self.alice), ASSISTANT)
        task = self._task(PRIVATE, "t-private")
        self.assertEqual(task["scope"], "personal")
        self.assertEqual(task["owner"]["user_id"], self.alice)
        self.assertEqual(task["owner"]["tenant_id"], self.tenant)
        self.assertEqual(task["owner"]["agent_id"], PRIVATE)

        # 3. withdrawing is not a route back to legacy identity: the surfaces
        #    still authenticate and still refuse a foreign reader
        anonymous = self.harness.get("/api/memory/personal")
        self.assertEqual(anonymous.status.split()[0], "401", anonymous.data)
        refusal = self._memory("/api/memory", self.admin_token, agent_id=PRIVATE)
        self.assertEqual(refusal.status, "403 Forbidden", refusal.data)
        self.assertEqual(self.harness.json(refusal).get("code"), "not_owner")
        self.assertEqual(config_module.conf().get("identity_mode"), "database")

        # 4. the member can still withdraw an object they already hold, and the
        #    withdrawal does not strand or resurrect it
        body = self._agents_post(self.alice_token, action="update", id=PRIVATE,
                                 enabled=False)
        self.assertEqual(body.get("status"), "success", body)
        self.assertFalse(self._roster_row(PRIVATE).get("enabled", True))
        binding = self._binding(PRIVATE)
        self.assertEqual(binding["private_owner_user_id"], self.alice)
        self.assertEqual(binding["tenant_id"], self.tenant)
        self.assertEqual(binding["origin"], "user_created")

    def test_11_5_the_administrator_refusal_happens_before_any_read(self):
        from agent.memory.service import MemoryService

        fired = []
        self.addCleanup(fired.clear)

        def tripwire(*args, **kwargs):
            fired.append(1)
            raise AssertionError("a private read reached the memory service")

        for label, withdraw in (("delivered", False), ("withdrawn", True)):
            if withdraw:
                self._switch(member_personal_console=False,
                             personal_channel_runtime=False)
            with patch.object(MemoryService, "list_files", tripwire), \
                    patch.object(MemoryService, "get_content", tripwire):
                owner = self._memory("/api/memory", self.alice_token,
                                     agent_id=PRIVATE)
                reached = len(fired)
                admin = self._memory("/api/memory", self.admin_token,
                                     agent_id=PRIVATE)
                peer = self._memory("/api/memory", self.bob_token,
                                    agent_id=PRIVATE)
                after_admin = len(fired)
            self.assertGreater(reached, 0,
                               "%s: the tripwire is not on the live read path" % label)
            self.assertEqual(owner.status, "503 Service Unavailable", owner.data)
            self.assertEqual(admin.status, "403 Forbidden", admin.data)
            self.assertEqual(self.harness.json(admin).get("code"), "not_owner",
                             (label, admin.data))
            self.assertEqual(peer.status, "403 Forbidden", peer.data)
            self.assertEqual(self.harness.json(peer).get("code"), "not_owner",
                             (label, peer.data))
            self.assertEqual(after_admin, reached,
                             "%s: a non-owner request read before it was refused"
                             % label)

        # the same administrator still reaches shared content, so the refusal
        # above is ownership, not "administrators cannot read memory"
        shared = self._memory("/api/memory", self.admin_token, agent_id=SHARED)
        self.assertEqual(shared.status, "200 OK", shared.data)


if __name__ == "__main__":
    unittest.main()
