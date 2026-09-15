# encoding:utf-8
"""Personal memory tools run for members without a per-resource grant.

``memory_search`` / ``memory_get`` / ``memory_add`` are injected per Agent
(``bridge/agent_initializer``) and are skipped by the engine-level tool catalog,
so ``builtin:memory_<name>`` never enters ``tenant_resource_grants`` or a role's
grants: the resource cannot be allocated through any console, while the runtime
gate still demanded it. The result was that no ordinary member (and no tenant
admin either) could ever run them.

The exemption is narrow by construction:

* only the three identity-scoped memory tools, matched by tool name;
* the caller must be an active member of the current tenant and hold BOTH
  ``tool.execute`` and ``memory.read``;
* only the resource grant is skipped - isolation, quota and the Agent tool
  scope stay in force;
* ``memory_add`` with ``scope=shared`` writes tenant-shared memory, which is
  not the caller's own scope, and stays grant-gated.

Every other tool, and every malformed argument, stays exactly as strict as
before.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from agent.tools.base_tool import BaseTool
from common.runtime_identity import RuntimeIdentity, use_identity

MEMORY_TOOLS = ("memory_search", "memory_get", "memory_add")


class _NamedTool(BaseTool):
    """A tool whose ``builtin:<name>`` resource id we control."""

    description = "test tool"

    def __init__(self, name="tool"):
        self.name = name


def _executor(tools):
    from agent.protocol.agent_stream import AgentStreamExecutor

    return AgentStreamExecutor(agent=None, model=None, system_prompt="", tools=tools)


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


def _drop_permission(svc, tenant, actor_id, role_code, permission):
    """Remove one permission from the tenant's builtin role."""
    role = [r for r in svc.list_roles(tenant["id"]) if r["code"] == role_code][0]
    svc.update_role(
        actor_user_id=actor_id, tenant_id=tenant["id"], role_id=role["id"],
        name=role["name"],
        permissions=[p for p in role["permissions"] if p != permission],
        expected_version=role["version"])


class PersonalMemoryToolServiceTest(unittest.TestCase):
    """``IdentityService.personal_memory_tool_may_execute`` semantics."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant, self.root = _bootstrap(self.tmp)

    def _allowed(self, user_id, tool_name, arguments=None):
        return self.svc.personal_memory_tool_may_execute(
            user_id, self.tenant["id"], tool_name, arguments)

    def test_member_without_grants_may_run_memory_search_and_get(self):
        member = _member(self.svc, self.root, self.tenant, "mem1", ["member"])
        # No grant rows exist for the memory tools anywhere.
        self.assertEqual(self.svc.grants_for(member["id"], self.tenant["id"])
                         and [g for g in self.svc.grants_for(
                             member["id"], self.tenant["id"])
                             if g["resource_id"] in
                             {f"builtin:{t}" for t in MEMORY_TOOLS}], [])
        for tool in ("memory_search", "memory_get"):
            self.assertTrue(self._allowed(member["id"], tool), tool)

    def test_member_may_run_own_scope_memory_add(self):
        member = _member(self.svc, self.root, self.tenant, "mem2", ["member"])
        self.assertTrue(self._allowed(member["id"], "memory_add", {}))
        self.assertTrue(self._allowed(
            member["id"], "memory_add", {"content": "x", "scope": "user"}))
        self.assertTrue(self._allowed(
            member["id"], "memory_add", {"content": "x", "scope": "session"}))

    def test_member_may_not_run_shared_scope_memory_add(self):
        member = _member(self.svc, self.root, self.tenant, "mem3", ["member"])
        self.assertFalse(self._allowed(
            member["id"], "memory_add", {"content": "x", "scope": "shared"}))

    def test_missing_memory_read_is_denied(self):
        member = _member(self.svc, self.root, self.tenant, "mem4", ["member"])
        _drop_permission(self.svc, self.tenant, self.root["id"], "member",
                         "memory.read")
        for tool in MEMORY_TOOLS:
            self.assertFalse(self._allowed(member["id"], tool), tool)

    def test_missing_tool_execute_is_denied(self):
        member = _member(self.svc, self.root, self.tenant, "mem5", ["member"])
        _drop_permission(self.svc, self.tenant, self.root["id"], "member",
                         "tool.execute")
        self.assertFalse(self._allowed(member["id"], "memory_search"))

    def test_revoking_memory_read_takes_effect_on_the_next_call(self):
        member = _member(self.svc, self.root, self.tenant, "mem6", ["member"])
        self.assertTrue(self._allowed(member["id"], "memory_search"))
        _drop_permission(self.svc, self.tenant, self.root["id"], "member",
                         "memory.read")
        self.assertFalse(self._allowed(member["id"], "memory_search"))

    def test_other_tools_are_not_exempt(self):
        member = _member(self.svc, self.root, self.tenant, "mem7", ["member"])
        for tool in ("web_search", "bash", "write", "memory_search_evil"):
            self.assertFalse(self._allowed(member["id"], tool), tool)

    def test_missing_arguments_take_the_tool_default_scope(self):
        """No arguments means the tool's own default (``user``), not ``shared``."""
        member = _member(self.svc, self.root, self.tenant, "mem8", ["member"])
        self.assertTrue(self._allowed(member["id"], "memory_add", None))

    def test_malformed_arguments_are_not_exempted(self):
        member = _member(self.svc, self.root, self.tenant, "mem8b", ["member"])
        for arguments in ("not-a-dict", ["shared"], {"scope": ["shared"]}):
            self.assertFalse(
                self._allowed(member["id"], "memory_add", arguments), arguments)

    def test_non_member_and_empty_identity_are_denied(self):
        outsider = _member(self.svc, self.root, self.tenant, "mem9", ["member"])
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="other", name="Other",
            recent_password="Str0ngAdminPass")
        self.assertFalse(self.svc.personal_memory_tool_may_execute(
            outsider["id"], other["id"], "memory_search", {}))
        self.assertFalse(self._allowed("", "memory_search", {}))
        self.assertFalse(self.svc.personal_memory_tool_may_execute(
            outsider["id"], "", "memory_search", {}))

    def test_tenant_admin_keeps_working(self):
        admin = _member(self.svc, self.root, self.tenant, "m10", ["tenant_admin"])
        self.assertTrue(self._allowed(admin["id"], "memory_search"))


class _DenyGrantSvc:
    """Service whose grant check always refuses, for gate wiring tests."""

    def __init__(self, memory_exempt):
        self.memory_exempt = memory_exempt
        self.memory_calls = []

    def check_resource_action(self, *args, **kwargs):
        return False

    def personal_memory_tool_may_execute(self, user_id, tenant_id, tool_name,
                                         arguments=None):
        self.memory_calls.append((tool_name, arguments))
        return self.memory_exempt


class PersonalMemoryToolGateTest(unittest.TestCase):
    """The runtime gate consults the exemption only after the grant check fails."""

    def test_gate_passes_arguments_through_and_allows(self):
        svc = _DenyGrantSvc(memory_exempt=True)
        executor = _executor([_NamedTool(name="memory_search")])
        arguments = {"query": "purchase"}
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                self.assertIsNone(
                    executor._resource_tool_denial("memory_search", arguments))
        self.assertEqual(svc.memory_calls, [("memory_search", arguments)])

    def test_gate_still_denies_when_the_exemption_refuses(self):
        svc = _DenyGrantSvc(memory_exempt=False)
        executor = _executor([_NamedTool(name="memory_search")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                denial = executor._resource_tool_denial("memory_search", {})
        self.assertIsNotNone(denial)
        self.assertIn("memory_search", denial)

    def test_other_tools_never_reach_the_memory_exemption(self):
        svc = _DenyGrantSvc(memory_exempt=True)
        executor = _executor([_NamedTool(name="web_search")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=svc):
                denial = executor._resource_tool_denial("web_search", {})
        self.assertIsNotNone(denial)
        self.assertEqual(svc.memory_calls, [])

    def test_service_without_the_method_fails_closed(self):
        class _NoMethodSvc:
            def check_resource_action(self, *args, **kwargs):
                return False

        executor = _executor([_NamedTool(name="memory_search")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service",
                       return_value=_NoMethodSvc()):
                self.assertIsNotNone(
                    executor._resource_tool_denial("memory_search", {}))

    def test_exemption_exception_fails_closed(self):
        class _BoomSvc:
            def check_resource_action(self, *args, **kwargs):
                return False

            def personal_memory_tool_may_execute(self, *args, **kwargs):
                raise RuntimeError("identity db down")

        executor = _executor([_NamedTool(name="memory_search")])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service",
                       return_value=_BoomSvc()):
                self.assertIsNotNone(
                    executor._resource_tool_denial("memory_search", {}))


class PersonalMemoryToolEndToEndTest(unittest.TestCase):
    """Real service + real gate: the reported refusal is gone for members."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc, self.tenant, self.root = _bootstrap(self.tmp)

    def test_member_runs_memory_search_and_own_scope_add(self):
        member = _member(self.svc, self.root, self.tenant, "e2e_m", ["member"])
        executor = _executor([_NamedTool(name="memory_search"),
                              _NamedTool(name="memory_add")])
        ident = RuntimeIdentity(user_id=member["id"], tenant_id=self.tenant["id"])
        with use_identity(ident):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                self.assertIsNone(executor._resource_tool_denial(
                    "memory_search", {"query": "purchase"}))
                self.assertIsNone(executor._resource_tool_denial(
                    "memory_add", {"content": "x", "scope": "user"}))

    def test_member_shared_write_and_ungranted_tool_stay_refused(self):
        member = _member(self.svc, self.root, self.tenant, "e2e_m2", ["member"])
        executor = _executor([_NamedTool(name="memory_add"),
                              _NamedTool(name="web_search")])
        ident = RuntimeIdentity(user_id=member["id"], tenant_id=self.tenant["id"])
        with use_identity(ident):
            with patch("auth.service.get_identity_service", return_value=self.svc):
                self.assertIsNotNone(executor._resource_tool_denial(
                    "memory_add", {"content": "x", "scope": "shared"}))
                self.assertIsNotNone(
                    executor._resource_tool_denial("web_search", {"query": "x"}))


if __name__ == "__main__":
    unittest.main()
