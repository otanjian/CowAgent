# encoding:utf-8
"""Integration coverage for the private-Agent lifecycle (task 4.6).

Each test here wires a *real* component to the private-Agent surface and checks
the property the spec asks for at the seam, not in isolation:

* concurrent creation cannot exceed the quota — two threads, one private Agent
  service, one identity store;
* a config save is version-guarded — two pages holding the same old revision
  cannot both win, and the loser gets a conflict rather than a silent overwrite;
* disabling an object does not let the default resolution quietly revive it;
* revoking a resource grant takes effect on the *next* call, so a run started
  under an old permission set does not keep it.

Retry/idempotency, pre-bind compensation and the atomic quota are pinned in
``test_private_agent_lifecycle.py`` and ``test_private_agent_quota.py``; this
file is the cross-component view.
"""

import contextlib
import json
import os
import tempfile
import threading
import unittest

from unittest.mock import patch

import pytest
import web

from agent import team
from agent.admin import AgentAdminService, StaleRosterError
from agent.registry import (
    AgentProfile,
    AgentRegistry,
    get_agent_registry,
    set_agent_registry,
)
from auth.runtime import RequestContext
from auth.service import IdentityService, IdentityServiceError
from channel.web import web_channel


def _bootstrap(tmp):
    svc = IdentityService(os.path.join(tmp, "identity.db"))
    boot = svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=os.path.join(tmp, "shared"), allow_weak=True)
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    return svc, boot["id"], root


def _member(svc, root, tenant_id, username):
    with patch("agent.personal_assistant.get_personal_assistant_provisioner",
               return_value=type("P", (), {"provision": lambda *a, **k: None})()):
        svc.create_member(
            actor_user_id=root["id"], tenant_id=tenant_id,
            operation="create-new", username=username, display_name=username,
            temporary_password="MemTempPass1", roles=["member"])
    return [m for m in svc.list_members(tenant_id)["items"]
            if m["username"] == username][0]["user_id"]


class _ThreadSafeRoster:
    def __init__(self):
        self._lock = threading.Lock()
        self.agents = []

    def snapshot(self):
        with self._lock:
            return {"agents": list(self.agents)}

    def create_agent(self, agent_id, name, workspace=None, **fields):
        with self._lock:
            self.agents.append({"id": agent_id, "name": name,
                                "workspace": workspace, "enabled": True,
                                "description": "", "persona_summary": ""})
        return {"id": agent_id}

    def delete_agent(self, agent_id, revision=None):
        with self._lock:
            self.agents = [a for a in self.agents if a["id"] != agent_id]
        return {"id": agent_id}


class ConcurrentCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant_id, self.root = _bootstrap(self.tmp)
        self.alice = _member(self.svc, self.root, self.tenant_id, "alice")
        self.roster = _ThreadSafeRoster()
        self.svc.set_private_agent_policy(
            actor_user_id=self.root["id"], tenant_id=self.tenant_id,
            member_agent_limit=1)

    def test_two_threads_cannot_exceed_one_slot(self):
        from agent.private_agent import PrivateAgentService

        svc = PrivateAgentService(self.svc, admin_service=self.roster)
        outcomes = []
        barrier = threading.Barrier(2)

        def attempt(name):
            barrier.wait()
            try:
                result = svc.create_private_agent(
                    user_id=self.alice, tenant_id=self.tenant_id, name=name)
                outcomes.append(("created", result["agent_id"]))
            except IdentityServiceError as exc:
                outcomes.append(("refused", exc.code))

        threads = [threading.Thread(target=attempt, args=(name,))
                   for name in ("first", "second")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        created = [o for o in outcomes if o[0] == "created"]
        refused = [o for o in outcomes if o[0] == "refused"]
        self.assertEqual(len(created), 1, outcomes)
        self.assertEqual(len(refused), 1, outcomes)
        self.assertEqual(refused[0][1], "quota_exceeded")

        owned = [b for b in self.svc.agents_for_tenant(self.tenant_id)
                 if b.get("origin") == "user_created"]
        self.assertEqual(len(owned), 1, "the quota is what the store holds")


class ConfigVersionConflictTests(unittest.TestCase):
    """Two saves from one old revision: one wins, the other is a conflict."""

    def setUp(self):
        web.ctx.headers = []
        web.ctx.status = "200 OK"
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant_id, self.root = _bootstrap(self.tmp)
        self.alice = _member(self.svc, self.root, self.tenant_id, "alice")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="pa",
                            private_owner_user_id=self.alice,
                            origin="user_created")

        primary = os.path.join(self.tmp, "primary")
        pa_ws = os.path.join(self.tmp, "pa")
        os.makedirs(primary, exist_ok=True)
        os.makedirs(pa_ws, exist_ok=True)
        self.config_path = os.path.join(self.tmp, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"agent_workspace": self.tmp, "default_agent_id": "primary",
                       "agents": [{"id": "primary", "name": "Primary",
                                   "workspace": primary, "enabled": True},
                                  {"id": "pa", "name": "PA",
                                   "workspace": pa_ws, "enabled": True}],
                       "channel_instances": []}, handle)
        set_agent_registry(AgentRegistry.from_config(
            team.resolve({"agent_workspace": self.tmp})))
        self.addCleanup(set_agent_registry, None)
        self.admin = AgentAdminService(self.config_path)

    def _post(self, payload):
        with patch.object(web_channel, "_db_scope", self._scope), \
                patch.object(web_channel, "_agent_admin_service",
                             lambda: self.admin), \
                patch.object(web_channel, "_reload_agent_runtime",
                             lambda *a, **k: None), \
                patch("auth.service.get_identity_service", lambda: self.svc), \
                patch.object(web_channel.web, "data",
                             lambda: json.dumps(payload).encode()):
            return web_channel.AgentsHandler().POST()

    @contextlib.contextmanager
    def _scope(self):
        ctx = RequestContext(
            user_id=self.alice, username="alice", display_name="Alice",
            is_platform_admin=False, must_change_password=False,
            tenant_id=self.tenant_id, membership=None,
            permissions=set(self.svc.permissions_for(self.alice, self.tenant_id)),
            is_tenant_admin=False)
        yield ctx

    def test_a_stale_revision_is_refused_without_overwriting(self):
        revision = self.admin.snapshot()["revision"]
        first = json.loads(self._post(
            {"action": "update", "id": "pa", "name": "First", "revision": revision}))
        self.assertEqual(first.get("status"), "success", first)

        body = json.loads(self._post(
            {"action": "update", "id": "pa", "name": "Second",
             "revision": revision}))
        self.assertEqual(body.get("status"), "error", body)
        self.assertEqual(body.get("code"), "stale_roster", body)
        self.assertTrue(web.ctx.status.startswith("409"), web.ctx.status)
        self.assertEqual(
            [a for a in self.admin.snapshot()["agents"] if a["id"] == "pa"][0]["name"],
            "First")

    def test_the_second_page_can_win_after_refreshing(self):
        stale = self.admin.snapshot()["revision"]
        self._post({"action": "update", "id": "pa", "name": "First",
                    "revision": stale})
        fresh = self.admin.snapshot()["revision"]
        body = json.loads(self._post(
            {"action": "update", "id": "pa", "name": "Second", "revision": fresh}))
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(
            [a for a in self.admin.snapshot()["agents"] if a["id"] == "pa"][0]["name"],
            "Second")


class DisableDoesNotReviveTests(unittest.TestCase):
    """Resolution skips a disabled object; it does not switch it back on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant_id, self.root = _bootstrap(self.tmp)
        self.alice = _member(self.svc, self.root, self.tenant_id, "alice")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="pa",
                            private_owner_user_id=self.alice,
                            origin="user_created")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="shared")
        self.svc.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=self.alice, agent_id="pa")
        set_agent_registry(AgentRegistry(
            [AgentProfile(id="pa", name="PA", workspace="/w/pa", enabled=False),
             AgentProfile(id="shared", name="Shared", workspace="/w/shared")],
            "shared"))
        self.addCleanup(set_agent_registry, None)

    def test_a_disabled_personal_default_is_skipped_not_re_enabled(self):
        resolved = self.svc.resolved_default_agent_id(self.tenant_id, self.alice)
        self.assertNotEqual(resolved, "pa")
        self.assertEqual(resolved, "shared")
        self.assertFalse(
            get_agent_registry().get("pa", require_enabled=False).enabled,
            "resolution must not have switched the object back on")

    def test_the_members_preference_survives_the_skip(self):
        """Re-enabling restores the choice, so the skip must not erase it."""
        self.svc.resolved_default_agent_id(self.tenant_id, self.alice)
        self.assertEqual(
            self.svc.member_default_agent_id(self.tenant_id, self.alice), "pa")


class RevocationDuringRunTests(unittest.TestCase):
    """A grant revoked mid-run denies the next call (task 4.6/4.4)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant_id, self.root = _bootstrap(self.tmp)
        self.alice = _member(self.svc, self.root, self.tenant_id, "alice")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="pa",
                            private_owner_user_id=self.alice,
                            origin="user_created")
        self.role = self.svc.create_role(
            self.root["id"], self.tenant_id, "tool-grant", "Tool grant",
            ["tool.execute"],
            resource_grants=[{"resource_kind": "tool",
                              "resource_id": "builtin:search",
                              "action": "execute"}])
        membership = self.svc._membership(self.alice, self.tenant_id)
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                " VALUES(?,?,?)", (self.tenant_id, membership["id"], self.role["id"]))
            con.commit()

    def _denial(self):
        from agent.protocol.agent_stream import AgentStreamExecutor
        from agent.tools.base_tool import BaseTool
        from common.runtime_identity import RuntimeIdentity, use_identity

        class _Tool(BaseTool):
            description = "search"

            def __init__(self):
                self.name = "search"

        executor = AgentStreamExecutor(agent=None, model=None,
                                       system_prompt="", tools=[_Tool()])
        with use_identity(RuntimeIdentity(user_id=self.alice,
                                          tenant_id=self.tenant_id,
                                          agent_id="pa")):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                return executor._resource_tool_denial("search")

    def test_the_granted_call_passes_then_the_revoked_one_is_refused(self):
        self.assertIsNone(self._denial())
        membership = self.svc._membership(self.alice, self.tenant_id)
        with self.svc._tx() as con:
            con.execute("DELETE FROM membership_roles WHERE membership_id=?",
                        (membership["id"],))
            con.commit()
        denial = self._denial()
        self.assertIsNotNone(denial, "a revoked grant must deny the next call")
        self.assertIn("search", denial)


if __name__ == "__main__":
    unittest.main()
