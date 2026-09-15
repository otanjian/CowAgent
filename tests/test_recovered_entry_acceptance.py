# encoding:utf-8
"""The ten recovered entries: one authorization regression over the real app.

Tasks 9.2/9.3/10.3. The four capability slices this change opens were each
accepted by their own tests (scheduler transport, memory scope adaptation,
project browser, WeChat scan). What those cannot show is the property the ten
methods share: every one of them left ``closed`` and now has to behave like the
rest of the surface -- refuse an anonymous caller, refuse a caller without the
declared permission, and *reach* the handler for one that has it. A regression
here is exactly the failure this change exists to prevent: a method that is
open in the registry but still answers 503, or one that answers everyone
because its route kept ``public``.

The expectations are derived from the registry, not hardcoded per route, so the
file also fails if a future edit closes a slice it believes is open.
"""

import json
import os
import tempfile
import unittest

from tests._helpers import WebAppHarness

AGENT = "primary"

#: The ten methods recovered by this change: (method, path, payload). The
#: request bodies are deliberately incomplete -- the point is where the request
#: stops, not what the handler does with it. ``{}`` means "no payload".
RECOVERED = [
    ("GET", "/api/scheduler", None),
    ("POST", "/api/scheduler/run", {"agent_id": AGENT, "task_id": "missing-task"}),
    ("POST", "/api/scheduler/toggle", {"agent_id": AGENT, "task_id": "missing-task"}),
    ("POST", "/api/scheduler/update", {"agent_id": AGENT, "task_id": "missing-task",
                                       "name": "renamed"}),
    ("POST", "/api/scheduler/delete", {"agent_id": AGENT, "task_id": "missing-task"}),
    ("GET", "/api/memory", None),
    ("GET", "/api/memory/content", None),
    ("GET", "/api/projects/browse", None),
    ("GET", "/api/weixin/qrlogin", None),
    ("POST", "/api/weixin/qrlogin", {}),
]

#: Refusals the *gate* produces. A handler may answer 400/404/409/422 for an
#: incomplete request, but it must never answer one of these for a caller the
#: gate accepted -- that would mean the entry is closed or unauthorized after
#: the gate said yes.
GATE_REFUSALS = (401, 403, 503)


def _policy(path, method):
    """The registry-derived route entry the gate will actually apply."""
    from channel.web.route_registry import derive_route_policy
    return (derive_route_policy().get(path) or {}).get(method)


def _status(response):
    """The HTTP status of a ``web.py`` response as an int.

    ``WebAppHarness`` returns web.py's own response object, whose ``status`` is
    the reason phrase string ("401 Unauthorized"), not a WSGI status code.
    """
    raw = str(getattr(response, "status", "") or "")
    return int(raw.split(" ", 1)[0]) if raw[:3].isdigit() else 0


def _closed_body(body):
    return isinstance(body, dict) and body.get("code") in (
        "database_unavailable", "identity_unavailable")


class RecoveredEntryTests(unittest.TestCase):
    """Every recovered method answers the same four questions the same way."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="recovered-entries-")
        cls.app = WebAppHarness(os.path.join(cls._tmp.name, "instance"))
        cls.app.add_agent(AGENT)

        # The permissions the recovered routes declare, plus what a member needs
        # to be a member at all. Derived, so opening a slice with a new
        # permission cannot leave this fixture behind.
        cls.permissions = set()
        for method, path, _payload in RECOVERED:
            entry = _policy(path, method)
            if entry.get("permission"):
                cls.permissions.add(entry["permission"])
        cls.permissions |= {"chat.use", "agent.use", "agent.read", "memory.read"}

        granted = cls.app.role("recovered-granted", sorted(cls.permissions))
        bare = cls.app.role("recovered-bare", ["chat.use"])
        cls.app.member("granted", [granted["code"]])
        cls.app.member("bare", [bare["code"]])
        cls.tenant = cls.app.tenant_id
        cls.granted_token = cls.app.login("granted")
        cls.bare_token = cls.app.login("bare")

    @classmethod
    def tearDownClass(cls):
        cls.app.close()
        cls._tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def _body(self, response):
        try:
            return json.loads(response.data.decode("utf-8"))
        except Exception:
            return {}

    def _call(self, method, path, payload, token=None, tenant=True, **extra):
        headers = dict(extra)
        if method == "GET":
            return self.app.get(path, token=token, tenant=tenant,
                                headers=headers or None)
        return self.app.post(path, payload or {}, token=token, tenant=tenant,
                             headers=headers or None)

    # -- the matrix --------------------------------------------------------

    def test_all_four_slices_are_open(self):
        """The change is only complete when every recovered slice is open."""
        from auth import capability_matrix

        for slice_id in ("scheduler", "memory_browse", "project_browse", "weixin_scan"):
            spec = capability_matrix.slice_for(slice_id)
            self.assertTrue(spec.implemented, slice_id)
            self.assertTrue(spec.accepted, slice_id)
            self.assertTrue(spec.enabled, slice_id)

    def test_every_recovered_method_is_registered_and_not_closed(self):
        for method, path, _payload in RECOVERED:
            entry = _policy(path, method)
            self.assertIsNotNone(entry, "%s %s is not served by any slice" % (method, path))
            self.assertNotEqual(entry.get("policy"), "closed",
                                "%s %s is still closed" % (method, path))

    def test_an_anonymous_caller_is_refused_by_every_entry(self):
        for method, path, payload in RECOVERED:
            response = self._call(method, path, payload)
            self.assertEqual(_status(response), 401,
                             "%s %s answered %s for an anonymous caller"
                             % (method, path, response.status))

    def test_a_caller_without_a_tenant_is_refused(self):
        for method, path, payload in RECOVERED:
            response = self._call(method, path, payload,
                                  token=self.granted_token, tenant=False)
            self.assertEqual(_status(response), 400,
                             "%s %s answered %s without a tenant selection"
                             % (method, path, response.status))

    def test_a_caller_with_the_declared_permission_reaches_the_handler(self):
        for method, path, payload in RECOVERED:
            response = self._call(method, path, payload, token=self.granted_token)
            body = self._body(response)
            self.assertNotIn(
                _status(response), GATE_REFUSALS,
                "%s %s answered %s (%s) for a permitted member"
                % (method, path, response.status, body))
            self.assertFalse(
                _closed_body(body),
                "%s %s still reports a closed consumer: %s" % (method, path, body))

    def test_a_caller_without_the_declared_permission_is_refused(self):
        checked = 0
        for method, path, payload in RECOVERED:
            entry = _policy(path, method) or {}
            permission = entry.get("permission")
            if not permission:
                continue  # no route-level permission: the handler owns the check
            checked += 1
            response = self._call(method, path, payload, token=self.bare_token)
            self.assertEqual(_status(response), 403,
                             "%s %s answered %s without %s"
                             % (method, path, response.status, permission))
        self.assertGreater(checked, 0, "no recovered route declares a permission")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
