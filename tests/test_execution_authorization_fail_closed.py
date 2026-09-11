# encoding:utf-8
"""Execution authorization fails closed when the identity cannot be resolved.

The audit (doc/多租户与权限架构分析.html S6) found two fail-open paths: an
identity-service exception in the per-tool resource gate skipped the check and
let the call run, and any unexpected error in the composite permission gate fell
through to the historical unrestricted behaviour. In a database-mode deployment
that turns an identity outage into a blanket authorization bypass.

These tests pin the fail-closed contract for database mode while keeping legacy
installs (which have no identity system at all) unrestricted.
"""

import unittest
from unittest.mock import patch

from agent.tools.base_tool import BaseTool
from common.runtime_identity import RuntimeIdentity, use_identity


class _GatedTool(BaseTool):
    """A normal tool that has not opted out of the resource grant."""

    name = "gated_probe"
    description = "A tool that is not self-authorized."


class _BoomSvc:
    """Identity service that blows up instead of answering."""

    def check_resource_action(self, *args, **kwargs):
        raise RuntimeError("identity db down")


class _AllowSvc:
    def __init__(self):
        self.calls = 0

    def check_resource_action(self, *args, **kwargs):
        self.calls += 1
        return True


class _AgentStub:
    """Just enough agent for ``_permission_denial`` to reach its gates."""

    def effective_cwd(self):
        return "/tmp"

    def effective_permission_mode(self):
        return "workspace-write"

    def write_roots(self):
        return ["/tmp"]


def _executor(tools):
    from agent.protocol.agent_stream import AgentStreamExecutor

    return AgentStreamExecutor(
        agent=None, model=None, system_prompt="", tools=tools)


class ResourceToolDenialFailClosedTest(unittest.TestCase):
    def test_identity_service_exception_denies(self):
        executor = _executor([_GatedTool()])
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service",
                       side_effect=RuntimeError("boom")):
                denial = executor._resource_tool_denial("gated_probe")
        self.assertIsNotNone(denial)

    def test_missing_identity_in_database_mode_denies(self):
        executor = _executor([_GatedTool()])
        with patch("agent.permission.isolation.database_mode",
                   return_value=True):
            denial = executor._resource_tool_denial("gated_probe")
        self.assertIsNotNone(denial)

    def test_missing_identity_in_legacy_mode_stays_unrestricted(self):
        executor = _executor([_GatedTool()])
        with patch("agent.permission.isolation.database_mode",
                   return_value=False):
            self.assertIsNone(executor._resource_tool_denial("gated_probe"))

    def test_self_authorized_tools_still_skip_the_gate(self):
        from agent.tools.todo.todo_tool import TodoTool

        executor = _executor([TodoTool()])
        with patch("agent.permission.isolation.database_mode",
                   return_value=True):
            self.assertIsNone(executor._resource_tool_denial("todo"))

    def test_granted_tool_still_passes(self):
        executor = _executor([_GatedTool()])
        allow = _AllowSvc()
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=allow):
                self.assertIsNone(
                    executor._resource_tool_denial("gated_probe"))
        self.assertEqual(allow.calls, 1)


class PermissionDenialFailClosedTest(unittest.TestCase):
    def test_unexpected_error_in_database_mode_denies(self):
        executor = _executor([_GatedTool()])
        executor.agent = _AgentStub()
        with patch("agent.permission.isolation.database_mode",
                   return_value=True):
            with patch("agent.permission.isolation.isolation_decision",
                       side_effect=RuntimeError("gate exploded")):
                denial = executor._permission_denial("gated_probe", {})
        self.assertIsNotNone(denial)

    def test_unexpected_error_in_legacy_mode_stays_unrestricted(self):
        executor = _executor([_GatedTool()])
        executor.agent = _AgentStub()
        with patch("agent.permission.isolation.database_mode",
                   return_value=False):
            with patch("agent.permission.isolation.isolation_decision",
                       side_effect=RuntimeError("gate exploded")):
                denial = executor._permission_denial("gated_probe", {})
        self.assertIsNone(denial)


if __name__ == "__main__":
    unittest.main()
