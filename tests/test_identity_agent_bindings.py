# encoding:utf-8
"""Focused tests for the agent-binding projection used by task 3.8.

Verifies that agent_bindings can be created and queried by tenant, and that the
default-agent / shared-root accessors behave correctly (including the unbound
and unknown-tenant cases).
"""

import os
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _setup():
    svc = IdentityService(_db_path())
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users()[0]
    return svc, tid, root


class AgentBindingProjectionTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def _bind(self, agent_id, tenant_id=None, owner=None):
        tenant_id = tenant_id or self.tid
        with self.svc._tx() as con:
            if owner:
                con.execute(
                    "INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                    " VALUES (?,?,?)", (agent_id, tenant_id, owner))
            else:
                con.execute(
                    "INSERT INTO agent_bindings(agent_id, tenant_id)"
                    " VALUES (?,?)", (agent_id, tenant_id))
        return agent_id

    def test_unbound_agent_returns_none(self):
        self.assertIsNone(self.svc.get_agent_binding("agent-missing"))

    def test_bind_and_lookup_agent(self):
        self._bind("agent-a")
        binding = self.svc.get_agent_binding("agent-a")
        self.assertEqual(binding["tenant_id"], self.tid)
        self.assertIsNone(binding["private_owner_user_id"])

    def test_agents_for_tenant(self):
        self._bind("agent-a")
        self._bind("agent-b")
        ids = self.svc.tenant_agent_ids(self.tid)
        self.assertIn("agent-a", ids)
        self.assertIn("agent-b", ids)

    def test_agents_do_not_leak_across_tenants(self):
        self._bind("agent-a")
        # create a second tenant
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta", shared_root="/s/beta",
            admin_username="betaadmin", admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        beta = [t for t in self.svc.list_tenants() if t["code"] == "beta"][0]
        self._bind("agent-b", tenant_id=beta["id"])
        self.assertNotIn("agent-b", self.svc.tenant_agent_ids(self.tid))
        self.assertIn("agent-b", self.svc.tenant_agent_ids(beta["id"]))

    def test_shared_root_accessor(self):
        self.assertEqual(self.svc.tenant_shared_root(self.tid), "/s/acme")
        self.assertIsNone(self.svc.tenant_shared_root("missing-tenant"))

    def test_default_agent_accessor(self):
        self.assertIsNone(self.svc.tenant_default_agent_id(self.tid))


class BindAgentTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_bind_agent_records_owner(self):
        b = self.svc.bind_agent(
            tenant_id=self.tid, agent_id="agent-x", private_owner_user_id=self.root["id"])
        self.assertEqual(b["tenant_id"], self.tid)
        self.assertEqual(b["private_owner_user_id"], self.root["id"])

    def test_bind_agent_idempotent(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="agent-x")
        # re-run with the same tenant -> no error, same binding
        b = self.svc.bind_agent(tenant_id=self.tid, agent_id="agent-x")
        self.assertEqual(b["tenant_id"], self.tid)

    def test_bind_agent_rejects_cross_tenant(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="agent-x")
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta", shared_root="/s/beta",
            admin_username="betaadmin", admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        beta = [t for t in self.svc.list_tenants() if t["code"] == "beta"][0]
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.bind_agent(tenant_id=beta["id"], agent_id="agent-x")
        self.assertEqual(e.exception.code, "conflict")

    def test_bind_agent_unknown_tenant_rejected(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.bind_agent(tenant_id="missing", agent_id="agent-x")
        self.assertEqual(e.exception.code, "not_found")


class RegisterTenancyTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def test_register_binds_agents_with_private_owner(self):
        summary = self.svc.register_default_tenancy(
            tenant_id=self.tid, private_owner_user_id=self.root["id"],
            agent_ids=["a1", "a2"])
        self.assertEqual(summary["bound"], 2)
        self.assertEqual(summary["already_registered"], 0)
        self.assertEqual(self.svc.get_agent_binding("a1")["private_owner_user_id"],
                         self.root["id"])

    def test_register_idempotent(self):
        self.svc.register_default_tenancy(
            tenant_id=self.tid, private_owner_user_id=self.root["id"], agent_ids=["a1"])
        summary = self.svc.register_default_tenancy(
            tenant_id=self.tid, private_owner_user_id=self.root["id"], agent_ids=["a1"])
        self.assertEqual(summary["bound"], 0)
        self.assertEqual(summary["already_registered"], 1)

    def test_register_rejects_cross_tenant_agent(self):
        self.svc.bind_agent(tenant_id=self.tid, agent_id="a1")
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta", shared_root="/s/beta",
            admin_username="betaadmin", admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        beta = [t for t in self.svc.list_tenants() if t["code"] == "beta"][0]
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.register_default_tenancy(
                tenant_id=beta["id"], private_owner_user_id=self.root["id"], agent_ids=["a1"])
        self.assertEqual(e.exception.code, "conflict")


if __name__ == "__main__":
    unittest.main()
