# encoding:utf-8
"""数据库模式下执行授权由角色决定，legacy 保留权限模式。

回归背景：database 多租户下工具能否执行本应由角色的 ``tool.execute`` +
资源 grant、租户执行隔离和配额决定，但旧会话权限模式会先一步拒绝，导致按角色
配置可用的工具仍被拦下，界面还提示"调整权限"。本用例锁定：

* database 模式：角色已授权即执行，不受会话 read-only 模式影响；未授权则拒绝。
* legacy 模式：仍在运行时应用 read-only/workspace-write 模式。
* 全局默认权限在 database 模式只读（由角色控制）。
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


class LegacyModeExecutionGateTest(unittest.TestCase):
    def test_read_only_still_refuses_a_write(self):
        executor = _executor("read-only")
        with patch("agent.permission.isolation.database_mode", return_value=False):
            denial = executor._permission_denial(
                "write", {"path": "/tmp/x.md", "content": "x"})
        self.assertIsNotNone(denial)

    def test_full_access_allows_a_write(self):
        executor = _executor("full-access")
        with patch("agent.permission.isolation.database_mode", return_value=False):
            denial = executor._permission_denial(
                "write", {"path": "/tmp/x.md", "content": "x"})
        self.assertIsNone(denial)


class PermissionModeProjectionTest(unittest.TestCase):
    """The global default permission is editable only in legacy mode."""

    def test_database_mode_is_read_only_and_roles_own_it(self):
        from channel.web import web_channel

        with patch.object(web_channel, "conf",
                          return_value={"identity_mode": "database"}):
            projection = web_channel._permission_mode_projection()
        self.assertFalse(projection["permission_mode_editable"])
        self.assertEqual(projection["permission_mode_source"], "role")

    def test_legacy_mode_stays_editable(self):
        from channel.web import web_channel

        with patch.object(web_channel, "conf",
                          return_value={"identity_mode": "legacy"}):
            projection = web_channel._permission_mode_projection()
        self.assertTrue(projection["permission_mode_editable"])
        self.assertEqual(projection["permission_mode_source"], "config")


if __name__ == "__main__":
    unittest.main()
