"""Regression tests for silent scheduled agent tasks."""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.tools.scheduler.integration import _execute_agent_task
from agent.tools.scheduler.scheduler_tool import SchedulerTool


class _Context(dict):
    kwargs = {}


class _TaskStore:
    def __init__(self):
        self.added = []

    def add_task(self, task):
        self.added.append(task)


class _AgentBridge:
    def __init__(self, content="maintenance complete"):
        self.content = content
        self.calls = []

    def agent_reply(self, task_description, **kwargs):
        self.calls.append((task_description, kwargs))
        return SimpleNamespace(content=self.content)


class TestSchedulerSilentMode(unittest.TestCase):
    def test_schema_exposes_silent_for_agent_tasks(self):
        silent = SchedulerTool.params["properties"]["silent"]

        self.assertEqual(silent["type"], "boolean")
        self.assertFalse(silent["default"])

    def test_create_persists_silent_on_agent_task(self):
        """A create now carries a verified member owner (task 3.2).

        This test previously asserted the stored shape of an ownerless create;
        creating without an owner is itself refused now, so the legal identity
        fixture every runtime caller already has (a tenant member with
        ``agent.use`` on the Agent) is supplied explicitly and the real
        ``TaskStore`` is asserted instead of a recorder.
        """
        import os
        import tempfile
        from unittest.mock import patch

        from agent.tools.scheduler.task_store import TaskStore
        from common.runtime_identity import RuntimeIdentity, use_identity
        from tests._helpers import build_identity

        with tempfile.TemporaryDirectory() as tmp:
            stack = build_identity(tmp, agents=("shared-agent",))
            role = stack.agent_role("chat-op", ["shared-agent"])
            alice = stack.member("alice", ["member", role["code"]])
            store = TaskStore(os.path.join(tmp, "shared-agent", "scheduler",
                                           "tasks.json"))

            tool = SchedulerTool({"channel_type": "web", "agent_id": "shared-agent"})
            tool.task_store = store
            tool.current_context = _Context(
                receiver="user-1",
                session_id="session-1",
                isgroup=False,
            )

            with patch("auth.service.get_identity_service", lambda: stack.service):
                with use_identity(RuntimeIdentity(
                        agent_id="shared-agent", user_id=alice,
                        tenant_id=stack.tenant_id, session_id="session-1")):
                    result = tool.execute(
                        {
                            "action": "create",
                            "name": "refresh token",
                            "ai_task": "refresh the token",
                            "schedule_type": "interval",
                            "schedule_value": "3000",
                            "silent": True,
                        }
                    )

            self.assertEqual(result.status, "success", result.result)
            stored = store.list_tasks()
            self.assertEqual(len(stored), 1)
            self.assertIs(stored[0]["action"]["silent"], True)
            # The owner is the verified member, stamped from the identity — not
            # from anything the model passed.
            self.assertEqual(stored[0]["owner"]["user_id"], alice)
            self.assertEqual(stored[0]["scope"], "personal")

    def test_create_without_a_verified_identity_is_refused(self):
        """No identity, no task: an ownerless task cannot be revalidated."""
        from agent.tools.scheduler.task_store import TaskStore

        with tempfile.TemporaryDirectory() as tmp:
            tool = SchedulerTool({"channel_type": "web", "agent_id": "agent-x"})
            tool.task_store = TaskStore(os.path.join(tmp, "tasks.json"))
            tool.current_context = _Context(receiver="user-1", session_id="s-1")
            result = tool.execute({
                "action": "create", "name": "n", "message": "m",
                "schedule_type": "interval", "schedule_value": "60",
            })
            self.assertEqual(result.status, "error")
            self.assertIn("not_member", str(result.result))
            self.assertEqual(tool.task_store.list_tasks(), [])

    def test_silent_agent_task_executes_without_delivery(self):
        bridge = _AgentBridge()
        task = {
            "id": "task-1",
            "action": {
                "type": "agent_task",
                "task_description": "rotate logs",
                "receiver": "user-1",
                "is_group": False,
                "channel_type": "web",
                "silent": True,
            },
        }

        with patch("channel.channel_factory.create_channel") as create_channel:
            result = _execute_agent_task(task, bridge)

        self.assertTrue(result)
        self.assertEqual(len(bridge.calls), 1)
        create_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
