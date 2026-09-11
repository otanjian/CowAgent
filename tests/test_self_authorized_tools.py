# encoding:utf-8
"""Self-authorized tools bypass the coarse ``tool.execute`` resource grant.

Personal todo and scheduler act only on the caller's own data in the current
conversation and carry their own identity checks (todo revalidates session,
membership and ``todo.read``/``todo.write``; scheduler stamps an owner snapshot
and revalidates ``chat.use`` + ``agent.use`` at fire time). A logged-in tenant
user must therefore be able to create their own todo/reminder in chat even when
their role has no explicit ``builtin:<tool>`` execute grant — the same way the
console todo page already lets them.

Every other tool still requires the grant: the exemption is a per-tool property,
not a blanket relaxation of the resource gate.
"""

import unittest
from unittest.mock import patch

from agent.tools.base_tool import BaseTool
from agent.tools.todo.todo_tool import TodoTool
from agent.tools.scheduler.scheduler_tool import SchedulerTool
from common.runtime_identity import RuntimeIdentity, use_identity


class _GatedTool(BaseTool):
    """A normal tool that has not opted out of the resource grant."""

    name = "gated_probe"
    description = "A tool that is not self-authorized."


class _DenySvc:
    """Identity service whose resource grant check always refuses."""

    def __init__(self):
        self.calls = []

    def check_resource_action(self, user_id, tenant_id, kind, resource_id,
                              action, permission=None):
        self.calls.append((kind, resource_id, action, permission))
        return False


def _executor(tools):
    from agent.protocol.agent_stream import AgentStreamExecutor

    return AgentStreamExecutor(
        agent=None, model=None, system_prompt="", tools=tools)


class SelfAuthorizedToolsTest(unittest.TestCase):
    def test_personal_todo_and_scheduler_skip_the_tool_grant(self):
        executor = _executor([TodoTool(), SchedulerTool(), _GatedTool()])
        deny = _DenySvc()
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=deny):
                self.assertIsNone(executor._resource_tool_denial("todo"))
                self.assertIsNone(executor._resource_tool_denial("scheduler"))
        # The grant gate is never even consulted for a self-authorized tool.
        self.assertEqual(deny.calls, [])

    def test_other_tools_still_require_the_execute_grant(self):
        executor = _executor([TodoTool(), SchedulerTool(), _GatedTool()])
        deny = _DenySvc()
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with patch("auth.service.get_identity_service", return_value=deny):
                denial = executor._resource_tool_denial("gated_probe")
        self.assertIsNotNone(denial)
        self.assertIn("gated_probe", denial)
        self.assertEqual(len(deny.calls), 1)

    def test_missing_identity_is_fail_closed(self):
        executor = _executor([_GatedTool()])
        # Database-only: unresolved identity refuses the tool call.
        denial = executor._resource_tool_denial("gated_probe")
        self.assertIsNotNone(denial)
        self.assertIn("身份", denial)


class _AgentStub:
    """Just enough agent for ``_permission_denial`` to reach the tool gate."""

    def effective_cwd(self):
        return "/tmp"

    def effective_permission_mode(self):
        return "workspace-write"

    def write_roots(self):
        return ["/tmp"]


class ReportedScenarioTest(unittest.TestCase):
    """The bug as reported: a tenant admin in workspace-write mode asks the
    model to set a reminder, and the scheduler call is refused even though the
    console lets the same user create their own todo."""

    def test_tenant_admin_reminder_is_not_refused(self):
        import os
        import tempfile

        from auth.service import IdentityService
        from agent.protocol.agent_stream import AgentStreamExecutor

        svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
        svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        tenant = svc.list_tenants()[0]
        root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
        created = svc.create_member(
            actor_user_id=root["id"], tenant_id=tenant["id"],
            operation="create-new", username="tenantadmin", display_name="TA",
            temporary_password="TmpPass123!", roles=["tenant_admin"])
        admin = svc._find_user_by_id(created["user_id"])

        executor = AgentStreamExecutor(
            agent=_AgentStub(), model=None, system_prompt="",
            tools=[TodoTool(), SchedulerTool()])
        ident = RuntimeIdentity(user_id=admin["id"], tenant_id=tenant["id"])
        with use_identity(ident):
            with patch("auth.service.get_identity_service", return_value=svc):
                self.assertIsNone(
                    executor._permission_denial("scheduler", {"action": "create"}))
                self.assertIsNone(
                    executor._permission_denial("todo", {"action": "create"}))


if __name__ == "__main__":
    unittest.main()
