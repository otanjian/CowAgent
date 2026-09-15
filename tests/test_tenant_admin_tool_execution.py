# encoding:utf-8
"""Tenant administrators may execute their tenant's available tools.

A tenant admin is its tenant's resource manager (产品规划 3.1). The built-in
``tenant_admin`` role holds ``tool.execute``, but its resource grants cover only
builtin tools: tenant-owned MCP tools (configured in the tenant Agent workspace)
never enter ``tenant_resource_grants`` at all. Requiring a per-resource grant
therefore locks the tenant admin out of the very tools it manages.

The exemption is deliberately narrow:

* the caller must be an active ``tenant_admin`` member of the *current* tenant;
* the caller must still hold the functional ``tool.execute`` permission;
* the tool must be available to that tenant - either inside the tenant's
  allocatable tool-execute set, or a tenant-owned MCP tool (``mcp:...``) whose
  connection is declared by an Agent *bound to that tenant*.

The MCP branch needs an explicit ``agent_id`` binding lookup: the resource id
alone (``mcp:<connection>:<tool>``) names a connection, not a tenant, so trusting
the prefix would admit any ``mcp:`` id the caller can spell. The agent binding is
the same isolation boundary ``_tenant_admin_owns_agent`` relies on.

It must not widen ordinary members, platform-shared resources the tenant was not
granted, or cross-tenant boundaries.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from agent.tools.base_tool import BaseTool
from common.runtime_identity import RuntimeIdentity, use_identity


class _NamedTool(BaseTool):
    """A tool whose ``builtin:<name>`` resource id we control."""

    description = "test tool"

    def __init__(self, name="tool"):
        self.name = name


def _executor(tools):
    from agent.protocol.agent_stream import AgentStreamExecutor

    return AgentStreamExecutor(agent=None, model=None, system_prompt="", tools=tools)


class _DenyGrantSvc:
    """Service with the standard gate refusing, plus a configurable exemption."""

    def __init__(self, exempt):
        self.exempt = exempt
        self.exempt_calls = []
        self.exempt_agents = []

    def check_resource_action(self, *args, **kwargs):
        return False

    def tenant_admin_may_execute_tool(self, user_id, tenant_id, resource_id,
                                      agent_id=None):
        self.exempt_calls.append(resource_id)
        self.exempt_agents.append(agent_id)
        return self.exempt


class _NoExemptionMethodSvc:
    def check_resource_action(self, *args, **kwargs):
        return False


class _ExemptionBoomSvc:
    def check_resource_action(self, *args, **kwargs):
        return False

    def tenant_admin_may_execute_tool(self, *args, **kwargs):
        raise RuntimeError("identity db down")


class TenantAdminToolExecutionGateTest(unittest.TestCase):
    """The runtime gate consults the exemption only after the grant check fails."""

    def test_exemption_allows_when_grant_missing(self):
        svc = _DenyGrantSvc(exempt=True)
        executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                self.assertIsNone(executor._resource_tool_denial("bash"))
        self.assertEqual(svc.exempt_calls, ["builtin:bash"])

    def test_no_exemption_still_denies(self):
        svc = _DenyGrantSvc(exempt=False)
        executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                denial = executor._resource_tool_denial("bash")
        self.assertIsNotNone(denial)
        self.assertIn("bash", denial)

    def test_mcp_tool_id_is_passed_to_exemption(self):
        svc = _DenyGrantSvc(exempt=True)
        executor = _executor([_NamedTool(name="erp_run_python_code")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                with patch.object(
                    executor, "_tool_resource_id",
                    return_value="mcp:erpnext:erp_run_python_code",
                ):
                    self.assertIsNone(
                        executor._resource_tool_denial("erp_run_python_code"))
        self.assertEqual(svc.exempt_calls, ["mcp:erpnext:erp_run_python_code"])

    def test_agent_id_is_forwarded_to_the_exemption(self):
        """The service cannot verify an MCP connection's tenant without it."""
        svc = _DenyGrantSvc(exempt=True)
        executor = _executor([_NamedTool(name="erp_report_list")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1",
                                          agent_id="ag_erp")):
            with patch("auth.service.get_identity_service", return_value=svc):
                with patch.object(executor, "_tool_resource_id",
                                  return_value="mcp:erpnext:erp_report_list"):
                    executor._resource_tool_denial("erp_report_list")
        self.assertEqual(svc.exempt_agents, ["ag_erp"])

    def test_missing_agent_id_still_reaches_the_exemption(self):
        """No agent in the identity is a decision for the service, not the gate."""
        svc = _DenyGrantSvc(exempt=False)
        executor = _executor([_NamedTool(name="erp_report_list")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                with patch.object(executor, "_tool_resource_id",
                                  return_value="mcp:erpnext:erp_report_list"):
                    denial = executor._resource_tool_denial("erp_report_list")
        self.assertEqual(svc.exempt_agents, [None])
        self.assertIsNotNone(denial)

    def test_service_without_exemption_method_fails_closed(self):
        executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service",
                       return_value=_NoExemptionMethodSvc()):
                self.assertIsNotNone(executor._resource_tool_denial("bash"))

    def test_exemption_exception_fails_closed(self):
        executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service",
                       return_value=_ExemptionBoomSvc()):
                self.assertIsNotNone(executor._resource_tool_denial("bash"))


def _bootstrap(tmp):
    from auth.service import IdentityService

    svc = IdentityService(os.path.join(tmp, "identity.db"))
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=os.path.join(tmp, "shared"), allow_weak=True)
    tenant = svc.list_tenants()[0]
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    return svc, tenant, root


def _member(svc, root, tenant, username, roles):
    created = svc.create_member(
        actor_user_id=root["id"], tenant_id=tenant["id"],
        operation="create-new", username=username, display_name=username,
        temporary_password="TmpPass123!", roles=roles)
    return svc._find_user_by_id(created["user_id"])


class TenantAdminToolExecutionServiceTest(unittest.TestCase):
    """``IdentityService.tenant_admin_may_execute_tool`` semantics."""

    AGENT = "ag_erp"          # bound to this tenant -> owns its MCP connections
    OTHER_AGENT = "ag_other"  # bound to another tenant

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.svc, self.tenant, self.root = _bootstrap(self._tmp)
        # The platform opens exactly one builtin tool to this tenant.
        self.svc.set_tenant_resource_grants(
            actor_user_id=self.root["id"], tenant_id=self.tenant["id"],
            grants=[{"resource_kind": "tool", "resource_id": "builtin:bash",
                     "action": "execute"}],
            expected_version=self.tenant["version"])
        # The tenant's own Agent declares the ERP MCP connection.
        self.svc.bind_agent(tenant_id=self.tenant["id"], agent_id=self.AGENT)
        # A second tenant owns a different Agent (and so a different workspace).
        self.other_tenant = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="other", name="Other",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=self.other_tenant["id"],
                            agent_id=self.OTHER_AGENT)

    def _allowed(self, user_id, resource_id, agent_id=None):
        return self.svc.tenant_admin_may_execute_tool(
            user_id, self.tenant["id"], resource_id, agent_id=agent_id)

    def test_tenant_granted_builtin_tool_is_allowed(self):
        admin = _member(self.svc, self.root, self.tenant, "ta1", ["tenant_admin"])
        self.assertTrue(self._allowed(admin["id"], "builtin:bash"))

    def test_ungranted_builtin_tool_is_denied(self):
        admin = _member(self.svc, self.root, self.tenant, "ta2", ["tenant_admin"])
        self.assertFalse(self._allowed(admin["id"], "builtin:write"))

    def test_tenant_owned_mcp_tool_is_allowed(self):
        admin = _member(self.svc, self.root, self.tenant, "ta3", ["tenant_admin"])
        self.assertTrue(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=self.AGENT))

    def test_mcp_tool_of_an_agent_bound_to_another_tenant_is_denied(self):
        admin = _member(self.svc, self.root, self.tenant, "ta6", ["tenant_admin"])
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=self.OTHER_AGENT))

    def test_mcp_tool_of_an_unbound_agent_is_denied(self):
        admin = _member(self.svc, self.root, self.tenant, "ta7", ["tenant_admin"])
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id="ag_never_bound"))

    def test_mcp_tool_without_an_agent_id_is_denied(self):
        """The id alone names a connection, not a tenant: no binding, no trust."""
        admin = _member(self.svc, self.root, self.tenant, "ta8", ["tenant_admin"])
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code"))
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=""))

    def test_builtin_branch_does_not_need_an_agent_id(self):
        admin = _member(self.svc, self.root, self.tenant, "ta9", ["tenant_admin"])
        self.assertTrue(self._allowed(admin["id"], "builtin:bash", agent_id=None))

    def test_plain_member_is_denied(self):
        member = _member(self.svc, self.root, self.tenant, "mem1", ["member"])
        self.assertFalse(self._allowed(member["id"], "builtin:bash"))
        self.assertFalse(
            self._allowed(member["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=self.AGENT))

    def test_tenant_admin_without_functional_permission_is_denied(self):
        admin = _member(self.svc, self.root, self.tenant, "ta4", ["tenant_admin"])
        role = [r for r in self.svc.list_roles(self.tenant["id"])
                if r["code"] == "tenant_admin"][0]
        self.svc.update_role(
            actor_user_id=admin["id"], tenant_id=self.tenant["id"],
            role_id=role["id"], name=role["name"],
            permissions=[p for p in role["permissions"] if p != "tool.execute"],
            expected_version=role["version"])
        self.assertFalse(self._allowed(admin["id"], "builtin:bash"))
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=self.AGENT))

    def test_revoking_tenant_admin_role_changes_next_call(self):
        admin = _member(self.svc, self.root, self.tenant, "ta5", ["tenant_admin"])
        self.assertTrue(self._allowed(admin["id"], "builtin:bash"))
        membership = self.svc.get_membership(admin["id"], self.tenant["id"])
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tenant["id"],
            member_id=membership["id"], display_name=membership["display_name"],
            active=True, roles=["member"],
            department_id=membership.get("department_id"),
            position_text=membership.get("position_text") or "",
            expected_version=membership["version"])
        self.assertFalse(self._allowed(admin["id"], "builtin:bash"))
        self.assertFalse(
            self._allowed(admin["id"], "mcp:erpnext:erp_run_python_code",
                          agent_id=self.AGENT))


class TenantAdminToolExecutionEndToEndTest(unittest.TestCase):
    """Real service + real runtime gate: the reported scenario now passes."""

    AGENT = "ag_erp"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant, self.root = _bootstrap(self.tmp)
        self.svc.set_tenant_resource_grants(
            actor_user_id=self.root["id"], tenant_id=self.tenant["id"],
            grants=[{"resource_kind": "tool", "resource_id": "builtin:bash",
                     "action": "execute"}],
            expected_version=self.tenant["version"])
        self.svc.bind_agent(tenant_id=self.tenant["id"], agent_id=self.AGENT)

    def test_tenant_admin_executes_ungranted_tenant_tool(self):
        admin = _member(self.svc, self.root, self.tenant, "ta_e2e", ["tenant_admin"])

        executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id=admin["id"],
                                          tenant_id=self.tenant["id"])):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                self.assertIsNone(executor._resource_tool_denial("bash"))

        # A plain member is still refused.
        member = _member(self.svc, self.root, self.tenant, "m_e2e", ["member"])
        member_executor = _executor([_NamedTool(name="bash")])
        with use_identity(RuntimeIdentity(user_id=member["id"],
                                          tenant_id=self.tenant["id"])):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                self.assertIsNotNone(
                    member_executor._resource_tool_denial("bash"))

    def test_tenant_admin_executes_tenant_mcp_tool_end_to_end(self):
        """The reported ERP case: real service, real gate, real binding lookup."""
        admin = _member(self.svc, self.root, self.tenant, "ta_mcp", ["tenant_admin"])
        executor = _executor([_NamedTool(name="erp_run_python_code")])
        with use_identity(RuntimeIdentity(user_id=admin["id"],
                                          tenant_id=self.tenant["id"],
                                          agent_id=self.AGENT)):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                with patch.object(
                    executor, "_tool_resource_id",
                    return_value="mcp:erpnext:erp_run_python_code",
                ):
                    self.assertIsNone(
                        executor._resource_tool_denial("erp_run_python_code"))

    def test_same_mcp_tool_is_refused_from_an_unbound_agent(self):
        admin = _member(self.svc, self.root, self.tenant, "ta_mcp2", ["tenant_admin"])
        executor = _executor([_NamedTool(name="erp_run_python_code")])
        with use_identity(RuntimeIdentity(user_id=admin["id"],
                                          tenant_id=self.tenant["id"],
                                          agent_id="ag_never_bound")):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                with patch.object(
                    executor, "_tool_resource_id",
                    return_value="mcp:erpnext:erp_run_python_code",
                ):
                    self.assertIsNotNone(
                        executor._resource_tool_denial("erp_run_python_code"))


if __name__ == "__main__":
    unittest.main()
