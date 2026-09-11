# encoding:utf-8
"""Identity must survive, or be refused at, every thread boundary.

Fork-decoupling task 5.5: execution boundaries that leave the request's identity
context (worker threads, scheduled tasks, async executors) MUST either carry the
identity across, or have the isolation/authorization gates refuse — an empty
identity must never be read as a legitimate "no user dimension" and run
unconfined.

The mechanisms the codebase relies on:

* ``common.runtime_identity.submit`` / ``.wrap`` — copy the ContextVar into the
  worker (used by the sub-agent runner and the parallel tool path);
* ``ChatChannel._handle`` — rebuilds identity from the message context inside
  the worker;
* the fail-closed gates themselves — the backstop that turns a leaky boundary
  into a refusal instead of a silent bypass.

These tests pin both halves so a future refactor that drops ``copy_context`` or
reintroduces a permissive branch is caught here rather than in production.
"""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from common.runtime_identity import (
    RuntimeIdentity,
    current_identity,
    submit,
    use_identity,
    wrap,
)


class _Ident:
    user_id = "u1"
    tenant_id = "t1"
    agent_id = "alpha"


class IdentityCarriageTest(unittest.TestCase):
    """The mechanisms that are supposed to carry identity actually do."""

    def test_plain_thread_loses_identity(self):
        """Baseline for the tests below: a raw thread does NOT inherit it."""
        seen = []

        def work():
            seen.append(current_identity().user_id)

        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            thread = threading.Thread(target=work)
            thread.start()
            thread.join()
        self.assertIsNone(seen[0])

    def test_wrap_carries_identity_into_a_plain_thread(self):
        seen = []

        def work():
            seen.append(current_identity().user_id)

        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            thread = threading.Thread(target=wrap(work))
            thread.start()
            thread.join()
        self.assertEqual(seen[0], "u1")

    def test_submit_carries_identity_into_a_pool(self):
        with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
            with ThreadPoolExecutor(max_workers=1) as pool:
                got = submit(pool, lambda: current_identity().user_id).result()
        self.assertEqual(got, "u1")


class BoundaryFailClosedTest(unittest.TestCase):
    """A boundary that does leak identity is refused, not run unconfined."""

    def test_isolation_gate_refuses_a_worker_thread_without_identity(self):
        import agent.permission.isolation as iso

        # Gate on, real identity resolution: the worker thread starts with the
        # default (empty) identity, exactly like a scheduled task that forgot to
        # scope, and the gate must refuse it rather than run unconfined.
        with patch.object(iso, "enabled", return_value=True):
            with ThreadPoolExecutor(max_workers=1) as pool:
                decision = pool.submit(
                    iso.isolation_decision, "bash", {"command": "echo hi"}, None
                ).result()
        self.assertFalse(decision.allowed)

    def test_tool_grant_gate_refuses_a_worker_thread_without_identity(self):
        from agent.protocol.agent_stream import AgentStreamExecutor
        from agent.tools.base_tool import BaseTool

        class _Tool(BaseTool):
            name = "gated_probe"
            description = "not self-authorized"

        executor = AgentStreamExecutor(
            agent=None, model=None, system_prompt="", tools=[_Tool()])
        with patch("agent.permission.isolation.database_mode",
                   return_value=True):
            with ThreadPoolExecutor(max_workers=1) as pool:
                denial = pool.submit(
                    executor._resource_tool_denial, "gated_probe").result()
        self.assertIsNotNone(denial)


if __name__ == "__main__":
    unittest.main()
