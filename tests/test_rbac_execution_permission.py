# encoding:utf-8
"""Database-mode execution authorization is owned by roles.

Regression: under multi-tenant identity, tool execution is gated by the
caller's ``tool.execute`` resource grant, tenant isolation, and quota — not by
a session permission-mode knob. These tests lock:

* role-granted tools run even when the session mode says read-only;
* role without grant is refused;
* the console projection keeps the global default permission read-only
  (``permission_mode_source=role``), including when conf still pins legacy.
"""

import unittest
from unittest.mock import patch

from agent.permission.policy import Decision
from agent.tools.base_tool import BaseTool
from common.runtime_identity import RuntimeIdentity, use_identity


class _WriteTool(BaseTool):
    """A normal path-writing tool gated by ``tool.execute`` in database mode."""

    name = "write"
    description = "write a file"


class _AgentStub:
    """Just enough agent for ``_permission_denial`` to run both gates."""

    def __init__(self, mode="read-only"):
        self._mode = mode

    def effective_cwd(self):
        return "/tmp"

    def effective_permission_mode(self):
        return self._mode

    def write_roots(self):
        return ["/tmp"]


class _GrantSvc:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def check_resource_action(self, user_id, tenant_id, kind, resource_id,
                              action, permission=None):
        self.calls.append((kind, resource_id, action, permission))
        return self.allowed


def _executor(mode="read-only"):
    from agent.protocol.agent_stream import AgentStreamExecutor

    return AgentStreamExecutor(
        agent=_AgentStub(mode), model=None, system_prompt="",
        tools=[_WriteTool()])


def _database_patches(grant, isolation_allowed=True):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("agent.permission.isolation.database_mode", return_value=True))
    stack.enter_context(
        patch("agent.permission.isolation.isolation_decision",
              return_value=Decision(isolation_allowed)))
    stack.enter_context(
        patch("auth.service.get_identity_service", return_value=grant))
    return stack


class DatabaseModeExecutionGateTest(unittest.TestCase):
    def test_role_granted_tool_runs_despite_read_only_mode(self):
        executor = _executor("read-only")
        grant = _GrantSvc(allowed=True)
        with _database_patches(grant), use_identity(
                RuntimeIdentity(user_id="u1", tenant_id="t1")):
            denial = executor._permission_denial(
                "write", {"path": "/tmp/x.md", "content": "x"})
        self.assertIsNone(denial)
        # The coarse role grant was actually the decision point.
        self.assertEqual(grant.calls[0][:3], ("tool", "builtin:write", "execute"))

    def test_role_without_grant_is_refused(self):
        executor = _executor("full-access")
        grant = _GrantSvc(allowed=False)
        with _database_patches(grant), use_identity(
                RuntimeIdentity(user_id="u1", tenant_id="t1")):
            denial = executor._permission_denial(
                "write", {"path": "/tmp/x.md", "content": "x"})
        self.assertIsNotNone(denial)

    def test_missing_identity_fails_closed_even_when_mode_says_full_access(self):
        """Session full-access must not bypass isolation without identity."""
        executor = _executor("full-access")
        with use_identity(RuntimeIdentity()):
            denial = executor._permission_denial(
                "write", {"path": "/tmp/x.md", "content": "x"})
        self.assertIsNotNone(denial)


class PermissionModeProjectionTest(unittest.TestCase):
    """The global default permission is never a self-service knob."""

    def test_database_mode_is_read_only_and_roles_own_it(self):
        from channel.web import web_channel

        with patch.object(web_channel, "conf",
                          return_value={"identity_mode": "database"}):
            projection = web_channel._permission_mode_projection()
        self.assertFalse(projection["permission_mode_editable"])
        self.assertEqual(projection["permission_mode_source"], "role")

    def test_legacy_conf_pin_stays_role_owned(self):
        """Explicit legacy conf must not restore editable config-source mode."""
        from channel.web import web_channel

        with patch.object(web_channel, "conf",
                          return_value={"identity_mode": "legacy"}):
            projection = web_channel._permission_mode_projection()
        self.assertFalse(projection["permission_mode_editable"])
        self.assertEqual(projection["permission_mode_source"], "role")


if __name__ == "__main__":
    unittest.main()
