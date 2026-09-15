# encoding:utf-8
"""The single capability registry: declaration invariants and the three projections.

Tasks 2.4, 3.7, 9.5. ``auth/capability_matrix.py`` is the one place that answers
"is this capability served at all?". Three consumers read it — the route table
(``channel/web/route_registry.py``), the consumer availability report
(``IdentityService._consumer_availability``) and the per-page projection
(``page_availability``) — and this file locks the agreement between them.

The failures it exists to catch are the ones measured in the audit: a route that
was open while the projection said "deferred", and a projection that was open
while the route answered 503. Both are now impossible without failing here.
"""

import glob
import os
import tempfile
import unittest

from tests._helpers import WebAppHarness


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The ten methods recovered by this change, and the registry slice+action each
#: one takes its policy from (task 9.2's per-method registration). A missing or
#: renamed slice is a hard failure here, not a silently closed route.
RECOVERED = {
    ("/api/scheduler", "GET"): ("scheduler", "list"),
    ("/api/scheduler/run", "POST"): ("scheduler", "run"),
    ("/api/scheduler/toggle", "POST"): ("scheduler", "toggle"),
    ("/api/scheduler/update", "POST"): ("scheduler", "update"),
    ("/api/scheduler/delete", "POST"): ("scheduler", "delete"),
    ("/api/memory", "GET"): ("memory_browse", "list"),
    ("/api/memory/content", "GET"): ("memory_browse", "content"),
    ("/api/projects/browse", "GET"): ("project_browse", "browse"),
    ("/api/weixin/qrlogin", "GET"): ("weixin_scan", "qr"),
    ("/api/weixin/qrlogin", "POST"): ("weixin_scan", "poll"),
}


class DeclarationInvariantTests(unittest.TestCase):
    """``check_consistency`` and the declaration's own shape."""

    def test_the_declaration_is_consistent(self):
        from auth import capability_matrix

        self.assertEqual(capability_matrix.check_consistency(), [])

    def test_slice_ids_and_consumers_are_unique(self):
        from auth import capability_matrix

        ids = [spec.id for spec in capability_matrix.SLICES]
        consumers = [spec.consumer for spec in capability_matrix.SLICES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(consumers), len(set(consumers)))

    def test_every_declared_capability_has_a_spec(self):
        """A slice must name a capability that exists as a main spec or a delta."""
        from auth import capability_matrix

        for spec in capability_matrix.SLICES:
            main = os.path.join(REPO, "openspec", "specs", spec.capability, "spec.md")
            deltas = glob.glob(os.path.join(
                REPO, "openspec", "changes", "*", "specs", spec.capability, "spec.md"))
            self.assertTrue(os.path.isfile(main) or deltas,
                            "%s names missing capability %r" % (spec.id, spec.capability))

    def test_an_open_action_on_an_unimplemented_slice_is_refused(self):
        from auth import capability_matrix

        probe = capability_matrix.Slice(
            "probe", capability="rbac-authorization", consumer="probe_consumer",
            page=None, scope=frozenset({"tenant"}),
            open={"list": capability_matrix.ACCESS_READ},
            implemented=False, accepted=False, reason="not_implemented")
        problems = self._problems_with(probe)
        self.assertTrue(any("not implemented" in p for p in problems), problems)

    def test_an_execute_action_without_acceptance_is_refused(self):
        from auth import capability_matrix

        probe = capability_matrix.Slice(
            "probe", capability="rbac-authorization", consumer="probe_consumer",
            page=None, scope=frozenset({"tenant"}),
            open={"run": capability_matrix.ACCESS_EXECUTE},
            implemented=True, accepted=False, reason="awaiting_acceptance")
        problems = self._problems_with(probe)
        self.assertTrue(any("acceptance" in p for p in problems), problems)

    def test_an_unknown_access_class_is_refused(self):
        from auth import capability_matrix

        probe = capability_matrix.Slice(
            "probe", capability="rbac-authorization", consumer="probe_consumer",
            page=None, scope=frozenset({"tenant"}),
            open={"list": "readonly-ish"},
            implemented=True, accepted=True, reason="")
        problems = self._problems_with(probe)
        self.assertTrue(any("unknown access class" in p for p in problems), problems)

    @staticmethod
    def _problems_with(probe):
        """Run ``check_consistency`` against the real declaration plus a probe."""
        from unittest import mock

        from auth import capability_matrix

        with mock.patch.object(capability_matrix, "SLICES",
                               tuple(capability_matrix.SLICES) + (probe,)):
            return capability_matrix.check_consistency()


class RouteDerivationTests(unittest.TestCase):
    """The route table derives from the registry, per action (tasks 9.2/9.5)."""

    def test_the_recovered_methods_take_their_policy_from_the_registry(self):
        from auth import capability_matrix
        from channel.web.route_registry import derive_route_policy

        policy = derive_route_policy()
        for (path, method), (slice_id, action) in RECOVERED.items():
            self.assertIn(path, policy, path)
            self.assertIn(method, policy[path], (path, method))
            expected = capability_matrix.route(slice_id, action)
            entry = policy[path][method]
            self.assertEqual(entry.get("policy"), expected["policy"],
                             "route and registry disagree for %s %s" % (method, path))
            if "permission" in expected:
                self.assertEqual(entry.get("permission"), expected["permission"])

    def test_a_closed_action_still_yields_a_closed_route(self):
        """An action the slice does not serve must be ``closed``, never a default."""
        from auth import capability_matrix
        from channel.web.route_registry import derive_route_policy

        policy = derive_route_policy()
        for (path, method), (slice_id, action) in RECOVERED.items():
            spec = capability_matrix.slice_for(slice_id)
            if spec.is_open(action):
                continue
            self.assertEqual(policy[path][method].get("policy"), "closed",
                             "%s %s is not open but is not closed" % (method, path))

    def test_an_unknown_slice_is_a_hard_error(self):
        from auth import capability_matrix

        with self.assertRaises(KeyError):
            capability_matrix.route("no-such-slice", "list")


class ProjectionAgreementTests(unittest.TestCase):
    """Route, consumer report and page projection cannot disagree (task 9.5)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="capability-matrix-")
        cls.app = WebAppHarness(os.path.join(cls._tmp.name, "instance"))

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls._tmp.cleanup()

    def test_every_slice_consumer_is_reported_from_the_registry(self):
        from auth import capability_matrix

        reported = self.app.service._consumer_availability()
        for spec in capability_matrix.SLICES:
            self.assertIn(spec.consumer, reported, spec.consumer)
            entry = reported[spec.consumer]
            self.assertEqual(bool(entry.get("available")), spec.enabled,
                             "%s projection disagrees with the declaration" % spec.consumer)
            if not spec.enabled:
                self.assertTrue(entry.get("reason"),
                                "%s is unavailable with no reason" % spec.consumer)

    def test_an_open_slice_is_open_in_the_route_the_consumer_and_the_page(self):
        """The scheduler slice is the worked example: all three legs agree."""
        from auth import capability_matrix
        from channel.web.route_registry import derive_route_policy

        spec = capability_matrix.slice_for("scheduler")
        self.assertTrue(spec.enabled, "the scheduler slice is expected to be open")
        entry = self.app.service._consumer_availability()[spec.consumer]
        self.assertTrue(entry["available"])
        pages = capability_matrix.page_availability()
        self.assertTrue(pages[spec.page]["available"], spec.page)
        policy = derive_route_policy()
        self.assertNotEqual(policy["/api/scheduler"]["GET"].get("policy"), "closed")

    def test_a_closed_slice_reports_its_own_reason_not_deferred(self):
        from auth import capability_matrix

        pages = capability_matrix.page_availability()
        for spec in capability_matrix.SLICES:
            if not spec.page or spec.enabled:
                continue
            self.assertEqual(pages[spec.page]["reason"], spec.reason or "not_implemented",
                             "%s reports a reason the declaration does not" % spec.page)

    def test_a_page_reports_the_access_classes_its_slices_serve(self):
        """Read/config/execute are separate refusals and must be reported apart.

        A member whose task list is open must keep pausing or deleting their own
        task even when running one is refused, so the page cannot collapse the
        three classes into one boolean (spec ``database-runtime-consumers``).
        """
        from auth import capability_matrix

        for page_id in capability_matrix.page_availability():
            states = capability_matrix.page_states(page_id)
            expected = {"read": False, "config": False, "execution": False}
            for spec in capability_matrix.SLICES:
                if spec.page != page_id:
                    continue
                for access in spec.open.values():
                    expected[{"read": "read", "config": "config",
                              "execute": "execution"}[access]] = True
            self.assertEqual(states, expected, page_id)

    def test_a_page_nobody_serves_reports_every_class_closed(self):
        from auth import capability_matrix

        self.assertEqual(capability_matrix.page_states("no.such.page"),
                         {"read": False, "config": False, "execution": False})


class ConsolePageProjectionTests(unittest.TestCase):
    """The page projection the console actually reads answers from the registry.

    ``capability_matrix.page_availability()`` agreeing with the route table is
    not enough: the console renders ``console_pages`` from
    ``context_for_tenant``, so a page the registry owns must reach that payload
    with the registry's answer. The measured defect was a page whose route had
    opened while the payload still said ``deferred`` (tasks 9.1/9.5).
    """

    @classmethod
    def setUpClass(cls):
        from auth import capability_matrix

        cls._tmp = tempfile.TemporaryDirectory(prefix="console-pages-")
        cls.app = WebAppHarness(os.path.join(cls._tmp.name, "instance"))
        cls.app.add_agent("primary")
        # The member is granted every permission the registry-owned pages
        # declare, so the *only* thing that can keep a page closed is the
        # registry itself -- an identity-level denial would make this file fail
        # for the wrong reason.
        permissions = {"chat.use", "agent.use", "agent.read"}
        for spec in capability_matrix.SLICES:
            if spec.page and spec.permission:
                permissions.add(spec.permission)
        role = cls.app.role("console-member", sorted(permissions),
                            grants=[("agent", "primary", "read")])
        cls.app.member("alice", [role["code"]])
        cls.token = cls.app.login("alice")
        # The same permissions *without* the Agent register: the memory page
        # reads an Agent's memory, so this identity must still see it closed.
        bare = cls.app.role("console-member-bare", ["chat.use", "agent.use", "agent.read",
                                                    "memory.read"])
        cls.app.member("bob", [bare["code"]])
        cls.bare_token = cls.app.login("bob")

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls._tmp.cleanup()

    def _pages(self, token=None):
        body = self.app.service.context_for_tenant(token or self.token,
                                                   self.app.tenant_id)
        return body["console_pages"]

    def test_every_registry_owned_page_reaches_the_console_payload(self):
        from auth import capability_matrix

        pages = self._pages()
        for page_id, declared in capability_matrix.page_availability().items():
            self.assertIn(page_id, pages, "%s never reaches console_pages" % page_id)
            self.assertEqual(bool(pages[page_id]["available"]), bool(declared["available"]),
                             "%s disagrees with the registry" % page_id)

    def test_an_open_page_is_never_reported_deferred(self):
        from auth import capability_matrix

        pages = self._pages()
        for page_id, declared in capability_matrix.page_availability().items():
            if not declared["available"]:
                continue
            self.assertTrue(pages[page_id]["available"], pages[page_id])
            self.assertNotEqual(pages[page_id]["reason"], "deferred", pages[page_id])

    def test_a_closed_page_never_falls_back_to_deferred(self):
        from auth import capability_matrix

        pages = self._pages()
        for page_id, declared in capability_matrix.page_availability().items():
            if declared["available"]:
                continue
            self.assertNotEqual(pages[page_id]["reason"], "deferred", page_id)
            self.assertFalse(pages[page_id]["available"], page_id)

    def test_the_console_payload_reports_the_registry_states(self):
        """The page's read/config/execute reach the payload the console reads."""
        from auth import capability_matrix

        pages = self._pages()
        for page_id in capability_matrix.page_availability():
            entry = pages[page_id]
            expected = capability_matrix.page_states(page_id)
            if not entry["read_allowed"]:
                # A refused page must not leak the capability flags.
                expected = {"read": False, "config": False, "execution": False}
            self.assertEqual(entry.get("states"), expected, page_id)

    def test_opening_a_slice_does_not_hand_the_page_to_every_member(self):
        """The registry answers "is it served"; the identity still has to qualify.

        The built-in member role carries ``memory.read``, so a page whose
        consumer just opened would otherwise appear for every member — while the
        handler (and the neighbouring management pages) also require a readable
        Agent. The projection must keep those two answers the same.
        """
        pages = self._pages(self.bare_token)
        entry = pages["admin.memory"]
        self.assertFalse(entry["read_allowed"], entry)
        self.assertFalse(entry["available"], entry)
        self.assertNotEqual(entry["reason"], "deferred", entry)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
