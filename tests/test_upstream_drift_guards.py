# encoding:utf-8
"""Upstream-drift guards (task 10.5).

Every obligation in this change survives only as long as a future ``master``
update cannot slip past it unnoticed. The merge itself is a text problem; these
are the *semantic* drifts a clean merge hides, and each one here is expressed as
a case someone can re-run after any upstream sync:

1. an upstream **new HTTP method** on an already-recovered URL cannot appear
   without a policy: appending a second entry for the same pattern is refused,
   extending an entry with a method whose policy is unknown is refused, and a
   method implemented by the handler but left out of the registry is reported by
   the coverage invariant;
2. an upstream **new task field** (top level or inside ``action``) is preserved
   through a real console edit, while the protected identity fields stay
   un-forgeable — "new functionality kept" and "still authorizes the same way"
   in one case.

The remaining drifts listed in the task (a new Desktop transport, a new memory
index/publish entry, a new channel action dispatch) are covered by their own
suites: ``tests/test_desktop_tenant_context*.py``, ``tests/test_memory_console.py``
and the channel seams, because each of them needs that subsystem's fixtures
rather than the route registry's.
"""

import json
import unittest

from channel.web.route_registry import (
    CoverageViolation,
    RouteEntry,
    check_route_coverage,
    derive_route_policy,
    _validate_entry,
)
from tests._helpers import WebAppHarness

AGENT = "shared-agent"


class RegistryDriftTests(unittest.TestCase):
    """A new HTTP method must be classified, not appended."""

    def test_appending_a_second_entry_for_a_recovered_url_is_refused(self):
        """The naive "add the new method as another route" edit cannot land."""
        entries = [
            RouteEntry("/api/scheduler", "SchedulerHandler", "upstream",
                       {"GET": {"policy": "tenant"}}),
            # The upstream sync adds PUT to the same URL as a second entry.
            RouteEntry("/api/scheduler", "SchedulerHandler", "upstream",
                       {"PUT": {"policy": "tenant"}}),
        ]
        with self.assertRaises(ValueError) as error:
            derive_route_policy(entries)
        self.assertIn("duplicate route pattern", str(error.exception))

    def test_a_new_method_without_a_policy_is_refused(self):
        """An upstream method added with no/unknown policy fails validation."""
        for methods in ({"PUT": {}}, {"PUT": {"policy": "whatever"}},
                        {"TRACE": {"policy": "tenant"}}):
            entry = RouteEntry("/api/scheduler", "SchedulerHandler", "upstream",
                               methods)
            with self.assertRaises(CoverageViolation, msg=methods):
                _validate_entry(entry)

    def test_a_handler_method_missing_from_the_registry_is_reported(self):
        """An upstream method on a recovered handler blocks acceptance until
        it is registered with a policy."""

        class _Drifted:
            def GET(self):  # noqa: N802 - upstream handler shape
                return "ok"

            def PUT(self):  # noqa: N802 - the new upstream method
                return "ok"

        entries = [RouteEntry("/api/scheduler", "_Drifted", "upstream",
                              {"GET": {"policy": "tenant"}})]
        violations = check_route_coverage({"_Drifted": _Drifted}, entries)
        self.assertTrue(any("PUT" in v for v in violations), violations)

    def test_the_real_registry_is_currently_classified(self):
        """The invariant the two probes above protect, on the real table."""
        import channel.web.web_channel as web_channel

        self.assertEqual(check_route_coverage(vars(web_channel)), [])


class TaskFieldDriftTests(unittest.TestCase):
    """An upstream task field survives preservation *and* authorization."""

    @classmethod
    def setUpClass(cls):
        cls.app = WebAppHarness(root=cls._root())
        cls.app.add_agent(AGENT)
        role = cls.app.role("console-member", ["chat.use", "agent.use", "agent.read"])
        cls.alice = cls.app.member("alice", [role["code"]])
        cls.token = cls.app.login("alice")

    @classmethod
    def _root(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory(prefix="drift-guard-")
        return cls._tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls._tmp.cleanup()

    def _task_with_upstream_fields(self, task_id="t-drift"):
        store = self.app.scheduler_store(AGENT)
        self.app.personal_task(
            AGENT, self.alice, id=task_id, name=task_id,
            # Two shapes of "upstream added a field": one at the task level, one
            # inside the action the scheduler hands to the Agent.
            upstream_trace={"origin": "master", "revision": 7},
            action={"type": "message", "content": "ping", "channel_type": "web",
                    "receiver": self.alice,
                    "upstream_action_field": {"nested": True}})
        return store

    def test_a_new_upstream_field_survives_an_edit(self):
        store = self._task_with_upstream_fields("t-keep")
        response = self.app.post("/api/scheduler/update",
                                 {"task_id": "t-keep", "agent_id": AGENT,
                                  "name": "renamed"}, token=self.token)
        body = json.loads(response.data.decode("utf-8"))
        self.assertEqual(body["status"], "success", body)
        task = store.get_task("t-keep")
        self.assertEqual(task["name"], "renamed")
        self.assertEqual(task.get("upstream_trace"),
                         {"origin": "master", "revision": 7})
        self.assertEqual((task.get("action") or {}).get("upstream_action_field"),
                         {"nested": True})

    def test_an_edit_still_cannot_forge_the_protected_identity(self):
        store = self._task_with_upstream_fields("t-forge")
        for patch in ({"owner": {"user_id": "someone-else"}},
                      {"tenant_id": "other-tenant"},
                      {"action": {"receiver": "someone-else"}}):
            body = dict(patch)
            body.update({"task_id": "t-forge", "agent_id": AGENT})
            response = self.app.post("/api/scheduler/update", body, token=self.token)
            payload = json.loads(response.data.decode("utf-8"))
            self.assertEqual(payload["status"], "error", (patch, payload))
            self.assertEqual(payload["code"], "forged_field", (patch, payload))
        task = store.get_task("t-forge")
        self.assertEqual((task.get("owner") or {}).get("user_id"), self.alice)
        self.assertEqual((task.get("action") or {}).get("receiver"), self.alice)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
