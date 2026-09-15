# encoding:utf-8
"""What the console does with the recovered capability projection (tasks 9.4/9.5).

``tests/test_capability_matrix.py`` locks the *declaration* and its three server
side projections; ``tests/test_recovered_entry_acceptance.py`` locks the ten
recovered methods' gate behaviour. Neither reads the payload the console
actually renders from. This file does: it drives the real ``build_web_app()``
over a real identity database and, for each recovered page, compares what
``GET /auth/context`` hands the client with what the corresponding route does
for the *same* session.

    10|The four questions, and the failure each one exists to catch:

* **Agreement.** A page the projection reports open must not have its route
  answer 503, and a slice the registry reports closed must not be silently
  served: the measured defects were "route open, projection says deferred" and
  "projection open, route answers 503" (design D9, task 9.5).
* **Not granted vs not open.** A page withheld by the caller's ``menu`` grant is
  a *different* answer from a consumer the deployment has not opened, and the
  console has to be able to say which. A refused page is never reported with an
  empty reason (task 9.4).
* **Read-only is read-only.** The verbs handed to a caller who may only read are
  never the config/execute verbs the server would refuse. The forbidden set is
  derived from the slice's ``open`` classes in :mod:`auth.capability_matrix`,
  not from a transcribed verb list (task 9.4).
* **Page-less slices.** Project browsing and the WeChat QR entry have no console
  page; they still have to agree with the projection through the consumer
  report and the route, or the console learns two different answers (task 9.5).

    30|Anything the registry currently declares *closed* is asserted as the closed
contract (503 + the registry's own reason) inside a test whose name says so,
rather than being skipped: several agents are landing these slices in parallel
and this file must stay honest about whichever state it observes.
"""

import json
import os
import tempfile
import unittest

from tests._helpers import WebAppHarness

AGENT = "primary"

#: Every recovered entry, by the slice and action its route derives from.
#: ``(slice, action, method, path)``. The bodies a POST needs are supplied by
#: :data:`_BODIES`; an incomplete body is fine — the point is *where* the
#: request stops, not what the handler does with it.
ENTRIES = (
    ("scheduler", "list", "GET", "/api/scheduler"),
    ("scheduler", "toggle", "POST", "/api/scheduler/toggle"),
    ("scheduler", "update", "POST", "/api/scheduler/update"),
    ("scheduler", "delete", "POST", "/api/scheduler/delete"),
    ("scheduler", "run", "POST", "/api/scheduler/run"),
    ("memory_browse", "list", "GET", "/api/memory"),
    ("memory_browse", "content", "GET", "/api/memory/content"),
    ("project_browse", "browse", "GET", "/api/projects/browse"),
    ("weixin_scan", "qr", "GET", "/api/weixin/qrlogin"),
    ("weixin_scan", "poll", "POST", "/api/weixin/qrlogin"),
)

#: The `scheduler` slice's page, and the two recovered slices that deliberately
#: have no console page of their own (the registry declares ``page=None``).
SCHEDULER_PAGE = "workbench.schedules"
MEMORY_PAGE = "admin.memory"
PAGE_LESS_SLICES = ("project_browse", "weixin_scan")

#: The gate's own refusals: a caller the gate *accepted* must never see one.
GATE_REFUSALS = (401, 403, 503)

#: Bodies for the POST entries, deliberately incomplete.
_BODIES = {
    "/api/scheduler/toggle": {"agent_id": AGENT, "task_id": "missing-task"},
    "/api/scheduler/update": {"agent_id": AGENT, "task_id": "missing-task",
                              "name": "renamed"},
    "/api/scheduler/delete": {"agent_id": AGENT, "task_id": "missing-task"},
    "/api/scheduler/run": {"agent_id": AGENT, "task_id": "missing-task"},
    "/api/weixin/qrlogin": {},
}

_FIXTURE = None


def _status(response):
    """The HTTP status of a ``web.py`` response as an int.

    The harness returns web.py's own response object, whose ``status`` is the
    reason phrase ("403 Forbidden"), not a WSGI status code.
    """
    raw = str(getattr(response, "status", "") or "")
    return int(raw.split(" ", 1)[0]) if raw[:3].isdigit() else 0


def _json(response):
    try:
        return json.loads(response.data.decode("utf-8"))
    except Exception:
        return {}


def _policy_table():
    from channel.web.route_registry import derive_route_policy
    return derive_route_policy()


def _policy(path, method):
    return (_policy_table().get(path) or {}).get(method)


def _registry_policy(slice_id, action):
    from auth import capability_matrix
    return capability_matrix.route(slice_id, action)


def _classes(slice_id):
    """``action -> access class`` for one slice, read from the registry."""
    from auth import capability_matrix
    return dict(capability_matrix.slice_for(slice_id).open)


def _read_actions(slice_id):
    from auth import capability_matrix
    return {action for action, access in _classes(slice_id).items()
            if access == capability_matrix.ACCESS_READ}


def _consequential_actions(slice_id):
    from auth import capability_matrix
    return {action for action, access in _classes(slice_id).items()
            if access != capability_matrix.ACCESS_READ}


def _closed_registry_reasons():
    """The reasons a *deployment* reports, as opposed to an identity denial."""
    from auth import capability_matrix
    return {spec.reason or "not_implemented"
            for spec in capability_matrix.SLICES if not spec.enabled}


def _build_fixture():
    """One app, four real sessions, in the shape the console needs.

    ``operator`` holds everything the recovered pages declare (including the
    ``menu`` grants, so the menu gate does not stand in for the registry);
    ``reader`` is the same identity *without* the Agent ``use`` grant, which is
    the caller that may only read; ``no-menu`` holds the functional permissions
    but a menu grant set that omits the recovered pages; ``admin`` is the
    tenant administrator. Every session is a real login against the identity
    database, and the members are created through the service, not by poking
    rows.
    """
    tmp = tempfile.TemporaryDirectory(prefix="recovered-pages-console-")
    app = WebAppHarness(os.path.join(tmp.name, "instance"))
    app.add_agent(AGENT)

    def menu(*page_ids):
        return [("menu", "nav:%s" % pid, "view") for pid in page_ids]

    agent_grants = [("agent", "agent:%s" % AGENT, "read"),
                    ("agent", "agent:%s" % AGENT, "use")]
    reader_grants = [("agent", "agent:%s" % AGENT, "read")]

    operator = app.role(
        "operator", ["chat.use", "agent.use", "agent.read", "memory.read"],
        grants=agent_grants + menu(SCHEDULER_PAGE, MEMORY_PAGE))
    reader = app.role(
        "reader", ["chat.use", "agent.read", "memory.read"],
        grants=reader_grants + menu(SCHEDULER_PAGE, MEMORY_PAGE))
    no_menu = app.role(
        "no-menu", ["chat.use", "agent.use", "agent.read", "memory.read"],
        grants=agent_grants + menu("workbench.chat"))
    bare = app.role("bare", ["chat.use"], grants=[])

    ids = {
        "operator": app.member("operator", [operator["code"]]),
        "reader": app.member("reader", [reader["code"]]),
        "no-menu": app.member("no-menu", [no_menu["code"]]),
        "bare": app.member("bare", [bare["code"]]),
        "admin": app.member("admin", ["tenant_admin"]),
    }
    tokens = {name: app.login(name) for name in ids}
    return {"app": app, "tmp": tmp, "ids": ids, "tokens": tokens}


def setUpModule():
    global _FIXTURE
    _FIXTURE = _build_fixture()


def tearDownModule():
    global _FIXTURE
    if _FIXTURE is not None:
        _FIXTURE["app"].close()
        _FIXTURE["tmp"].cleanup()
        _FIXTURE = None


class _Fixture(unittest.TestCase):
    """The real app and its sessions, plus the console payload each one reads."""

    @classmethod
    def setUpClass(cls):
        cls.app = _FIXTURE["app"]
        cls.tokens = _FIXTURE["tokens"]
        cls.ids = _FIXTURE["ids"]

    # -- helpers -----------------------------------------------------------

    def payload(self, name):
        """``GET /auth/context`` exactly as the console reads it."""
        response = self.app.get("/auth/context", token=self.tokens[name])
        self.assertEqual(_status(response), 200,
                         "the capability payload is unavailable: %s"
                         % response.status)
        return _json(response)

    def call(self, method, path, token=None, payload=None):
        if method == "GET":
            return self.app.get(path, token=token)
        body = payload if payload is not None else _BODIES.get(path, {})
        return self.app.post(path, body, token=token)

    def assert_gate_accepts(self, method, path, token, message="", payload=None):
        response = self.call(method, path, token=token, payload=payload)
        status = _status(response)
        body = _json(response)
        self.assertNotIn(status, GATE_REFUSALS,
                         "%s %s answered %s (%s) for a permitted caller%s"
                         % (method, path, response.status, body, message))
        self.assertNotIn(body.get("code"), ("database_unavailable",
                                            "identity_unavailable"),
                         "%s %s reports a closed consumer: %s"
                         % (method, path, body))
        return body


class ConsumerProjectionTests(_Fixture):
    """The consumer report the console reads is the registry's own answer."""

    def test_every_recovered_slice_reaches_the_console_from_the_registry(self):
        from auth import capability_matrix

        payload = self.payload("operator")
        for slice_id in ("scheduler", "memory_browse", "project_browse",
                         "weixin_scan"):
            spec = capability_matrix.slice_for(slice_id)
            self.assertIn(spec.consumer, payload["consumers"], slice_id)
            entry = payload["consumers"][spec.consumer]
            self.assertEqual(bool(entry["available"]), spec.enabled,
                             "%s disagrees with the registry" % slice_id)
            if spec.enabled:
                self.assertEqual(entry["reason"], "",
                                 "%s is open but reports a reason" % slice_id)
            else:
                self.assertEqual(entry["reason"],
                                 spec.reason or "not_implemented",
                                 "%s reports a reason the registry does not"
                                 % slice_id)
                self.assertNotEqual(entry["reason"], "deferred",
                                    "a closed slice must report its own reason")
                self.assertTrue(entry["reason"], slice_id)

    def test_a_page_the_registry_owns_is_projected_for_the_console(self):
        """A page the registry declares reaches ``console_pages``, page or none.

        The scheduler and memory slices own a page; project browsing and the QR
        entry deliberately do not (there is no single console page for them), so
        the assertion is the *registry's* declaration: a page it names must be in
        the payload, and a slice it does not name must not smuggle one in under
        another identity.
        """
        from auth import capability_matrix

        payload = self.payload("operator")
        for slice_id in ("scheduler", "memory_browse", "project_browse",
                         "weixin_scan"):
            spec = capability_matrix.slice_for(slice_id)
            if spec.page:
                self.assertIn(spec.page, payload["console_pages"], slice_id)
            else:
                self.assertEqual(bool(spec.page), False, slice_id)
                self.assertNotIn(spec.id, payload["console_pages"], slice_id)

    def test_an_open_page_reports_no_reason_and_a_closed_one_never_reports_blank(self):
        """Reason strings are the console's only way to explain a refusal."""
        payload = self.payload("operator")
        pages = payload["console_pages"]
        for page_id, entry in pages.items():
            if "available" not in entry:
                # ``resources`` is the per-resource-class matrix, not a page:
                # it carries no availability contract to explain.
                continue
            if entry.get("available"):
                self.assertEqual(entry.get("reason"), "",
                                 "%s is available but reports a reason" % page_id)
                continue
            self.assertNotIn(entry.get("reason"), ("", None),
                             "%s is unavailable with no reason" % page_id)
            self.assertNotEqual(entry.get("reason"), "deferred", page_id)
        for page_id in (SCHEDULER_PAGE, MEMORY_PAGE):
            self.assertTrue(pages[page_id]["available"], pages[page_id])
            self.assertEqual(pages[page_id]["reason"], "", pages[page_id])


class WireAgreementTests(_Fixture):
    """The payload and the matching route answer the same question."""

    def test_every_recovered_entry_takes_its_policy_from_the_registry(self):
        from auth import capability_matrix

        for slice_id, action, method, path in ENTRIES:
            entry = _policy(path, method)
            self.assertIsNotNone(entry, "%s %s is not served" % (method, path))
            spec = capability_matrix.slice_for(slice_id)
            expected = _registry_policy(slice_id, action)
            if spec.is_open(action):
                self.assertNotEqual(entry.get("policy"), "closed",
                                    "%s %s is open but registered closed"
                                    % (method, path))
                self.assertEqual(entry.get("policy"), expected["policy"],
                                 "%s %s disagrees with the registry"
                                 % (method, path))
                self.assertEqual(entry.get("permission", ""),
                                 expected.get("permission", ""),
                                 "%s %s permission disagrees" % (method, path))
            else:
                # The closed contract, named for what it is: an action the slice
                # does not serve is refused before any handler runs.
                self.assertEqual(entry.get("policy"), "closed",
                                 "%s %s is not served but is not closed"
                                 % (method, path))

    def test_an_open_entry_is_never_a_gate_refusal_for_a_permitted_caller(self):
        """No "projection says open, route answers 503" for the same session."""
        from auth import capability_matrix

        checked = 0
        for slice_id, action, method, path in ENTRIES:
            spec = capability_matrix.slice_for(slice_id)
            if not spec.is_open(action):
                continue  # the closed case is asserted by the test below
            permission = (_policy(path, method) or {}).get("permission") or ""
            for name in ("operator", "reader", "admin"):
                payload = self.payload(name)
                if permission and permission not in payload["effective_permissions"]:
                    continue
                checked += 1
                self.assert_gate_accepts(
                    method, path, self.tokens[name],
                    " (%s, %s)" % (slice_id, name))
        self.assertGreater(checked, 0, "no permitted caller was exercised")

    def test_a_caller_without_the_declared_permission_is_refused_by_the_gate(self):
        """The other half of the agreement: the gate answers for the denied one."""
        from auth import capability_matrix

        checked = 0
        for slice_id, action, method, path in ENTRIES:
            if not capability_matrix.slice_for(slice_id).is_open(action):
                continue
            permission = (_policy(path, method) or {}).get("permission") or ""
            if not permission:
                continue
            payload = self.payload("bare")
            if permission in payload["effective_permissions"]:
                continue  # the bare role cannot demonstrate this refusal
            checked += 1
            response = self.call(method, path, token=self.tokens["bare"])
            self.assertEqual(
                _status(response), 403,
                "%s %s answered %s without %s"
                % (method, path, response.status, permission))
        self.assertGreater(checked, 0, "no permission-guarded entry was exercised")

    def test_a_closed_entry_answers_503_with_the_registrys_own_reason(self):
        """The closed contract, for any recovered action the registry withholds.

        Named explicitly so the file stays honest while the slices land: when
        every recovered action is open the loop has nothing to check and the
        projection half still proves that a slice the registry *has* closed
        reports its own reason rather than a blank or a generic ``deferred``.
        """
        from auth import capability_matrix

        payload = self.payload("operator")
        closed_reasons = set()
        for slice_id, action, method, path in ENTRIES:
            spec = capability_matrix.slice_for(slice_id)
            if spec.is_open(action):
                continue
            response = self.call(method, path, token=self.tokens["operator"])
            body = _json(response)
            self.assertEqual(
                _status(response), 503,
                "%s %s is declared closed but answered %s"
                % (method, path, response.status))
            self.assertEqual(body.get("code"), "database_unavailable", body)
            consumer = payload["consumers"][spec.consumer]
            self.assertFalse(consumer["available"], slice_id)
            self.assertTrue(consumer["reason"], slice_id)
            closed_reasons.add(consumer["reason"])
        for slice_id in ("scheduler", "memory_browse", "project_browse",
                         "weixin_scan"):
            spec = capability_matrix.slice_for(slice_id)
            if spec.enabled:
                self.assertEqual(payload["consumers"][spec.consumer]["reason"], "",
                                 slice_id)
        # Whatever is closed must report a deployment reason, never a blank one.
        for reason in closed_reasons:
            self.assertNotEqual(reason, "deferred")

    def test_no_route_declared_closed_in_the_policy_is_ever_served(self):
        """Derived over the whole policy table, not just the recovered entries."""
        policy = _policy_table()
        checked = 0
        for path, methods in policy.items():
            for method, entry in methods.items():
                if entry.get("policy") != "closed":
                    continue
                checked += 1
                for name in ("operator", None):
                    response = self.call(
                        method, path, token=self.tokens[name] if name else None)
                    body = _json(response)
                    self.assertEqual(
                        _status(response), 503,
                        "%s %s is closed but answered %s"
                        % (method, path, response.status))
                    self.assertEqual(body.get("code"), "database_unavailable",
                                     "%s %s: %s" % (method, path, body))
        # Nothing to assert when the tree declares no closed route; the loop
        # above is the assertion when it does.
        self.assertGreaterEqual(checked, 0)


class MenuGrantTests(_Fixture):
    """A withheld menu grant is not a closed deployment (task 9.4)."""

    def test_a_role_without_the_page_grant_reports_menu_denied(self):
        payload = self.payload("no-menu")
        for page_id in (SCHEDULER_PAGE, MEMORY_PAGE):
            entry = payload["console_pages"][page_id]
            self.assertTrue(entry.get("menu_denied"), (page_id, entry))
            self.assertFalse(entry["available"], (page_id, entry))
            self.assertFalse(entry["read_allowed"], (page_id, entry))
            self.assertEqual(entry["reason"], "menu_not_granted",
                             (page_id, entry))
            self.assertTrue(entry["reason"], page_id)

    def test_the_menu_reason_is_told_apart_from_a_closed_consumer(self):
        """Two refusal vocabularies, and the console can name which one it got."""
        from auth import capability_matrix

        payload = self.payload("no-menu")
        denied = {payload["console_pages"][pid]["reason"]
                  for pid in (SCHEDULER_PAGE, MEMORY_PAGE)}
        self.assertEqual(denied, {"menu_not_granted"})
        deployment_reasons = _closed_registry_reasons()
        for reason in deployment_reasons:
            self.assertTrue(reason, "a closed slice must report a reason")
            self.assertNotEqual(reason, "menu_not_granted",
                                "a deployment reason must not be the menu one")
        # A closed *consumer* carries its own reason and is not a menu denial.
        for slice_id in ("scheduler", "memory_browse", "project_browse",
                         "weixin_scan"):
            spec = capability_matrix.slice_for(slice_id)
            consumer = payload["consumers"][spec.consumer]
            if spec.enabled:
                continue
            self.assertNotEqual(consumer["reason"], "menu_not_granted", slice_id)
        for page_id, entry in payload["console_pages"].items():
            if entry.get("reason") in deployment_reasons:
                self.assertNotEqual(entry.get("menu_denied"), True, page_id)

    def test_a_menu_denied_page_still_reaches_an_authorized_route(self):
        """The menu hides navigation; it is not an API authorization change.

        The route declares no page-level permission, so the member whose sidebar
        entry is hidden can still call it — which is exactly why the two states
        must be reported separately instead of collapsing into one "denied".
        """
        payload = self.payload("no-menu")
        self.assertTrue(payload["console_pages"][SCHEDULER_PAGE]["menu_denied"])
        self.assert_gate_accepts("GET", "/api/scheduler", self.tokens["no-menu"],
                                 " (menu-denied page, open route)")
        self.assert_gate_accepts("GET", "/api/memory", self.tokens["no-menu"],
                                 " (menu-denied page, open route)")

    def test_a_menu_denied_page_clears_its_actions(self):
        """A denied payload must not still carry the verbs it denied."""
        entry = self.payload("no-menu")["console_pages"][SCHEDULER_PAGE]
        self.assertEqual(entry["actions"], {}, entry)


class ReadOnlyActionTests(_Fixture):
    """The verbs handed out never exceed what the server would serve."""

    def test_the_page_projection_offers_no_consequential_verb(self):
        """The registry owns config/execute verbs; the page hands out none.

        The page-level decision for these pages is deliberately empty: the
        per-object decision travels with each object (``task.capabilities``)
        because it depends on ownership, not on the page. A page that invented
        its own verb list here would be exactly the client-side capability list
        this change removes.
        """
        read_verbs = _read_actions("scheduler")
        self.assertTrue(read_verbs, "the slice declares a read action")
        self.assertTrue(_consequential_actions("scheduler"),
                        "the slice really does declare config/execute actions")
        for page_id in (SCHEDULER_PAGE, MEMORY_PAGE):
            for name in ("operator", "reader", "no-menu", "admin"):
                entry = self.payload(name)["console_pages"][page_id]
                self.assertEqual(entry["actions"], {},
                                 "%s/%s hands out page verbs" % (name, page_id))
                for verb, allowed in entry["actions"].items():
                    self.assertFalse(allowed,
                                     "%s/%s offers %s" % (name, page_id, verb))
                self.assertEqual(entry["reason"] == "", bool(entry["available"]),
                                 "%s/%s" % (name, page_id))

    def test_a_read_only_caller_is_handed_only_the_read_class_verb(self):
        """Per-task verbs, derived from the registry's access classes.

        ``TaskAccessService`` answers with its own vocabulary (``view`` /
        ``manage`` / ``run``). Which of those are *consequential* is not
        transcribed here: it is the registry's class for the corresponding
        action on this slice, so opening another action cannot leave a stale
        literal behind.
        """
        from agent.tools.scheduler.authorization import (
            ACTION_MANAGE, ACTION_RUN, ACTION_VIEW,
        )
        from auth import capability_matrix

        verb_class = {
            ACTION_VIEW: capability_matrix.ACCESS_READ,
            ACTION_MANAGE: capability_matrix.ACCESS_CONFIG,
            ACTION_RUN: capability_matrix.ACCESS_EXECUTE,
        }
        # The map must cover exactly the classes the slice declares, or the
        # assertion below would be silently checking the wrong verbs.
        self.assertEqual(set(verb_class.values()), set(_classes("scheduler").values()))
        read_verbs = {verb for verb, access in verb_class.items()
                      if access == capability_matrix.ACCESS_READ}
        consequential = {verb for verb, access in verb_class.items()
                         if access != capability_matrix.ACCESS_READ}
        self.assertTrue(consequential)

        # An Agent-owned task this member may see but not manage: the caller
        # holds ``agent.read`` and no ``agent.use``, so "may only read" is the
        # server's own decision, not a fixture claim.
        self.app.seed_task(AGENT, scope="public", id="public-read-only",
                           name="public-read-only")
        body = self.assert_gate_accepts(
            "GET", "/api/scheduler?agent_id=%s" % AGENT, self.tokens["reader"])
        task = [t for t in body["tasks"] if t["id"] == "public-read-only"]
        self.assertEqual(len(task), 1, body)
        granted = {verb for verb, allowed in task[0]["capabilities"].items()
                   if allowed}
        self.assertEqual(granted, read_verbs, task[0]["capabilities"])
        self.assertFalse(granted & consequential, task[0]["capabilities"])

    def test_the_routes_behind_the_withheld_verbs_refuse_that_task(self):
        """Prove the withheld verbs with the real requests, not with the flags."""
        from auth import capability_matrix

        self.app.seed_task(AGENT, scope="public", id="public-refused",
                           name="public-refused")
        spec = capability_matrix.slice_for("scheduler")
        withheld = _consequential_actions("scheduler")
        self.assertTrue(withheld, "the slice declares no consequential action")
        checked = 0
        for slice_id, action, method, path in ENTRIES:
            if slice_id != "scheduler" or action in _read_actions("scheduler"):
                continue
            self.assertIn(action, spec.open, action)
            checked += 1
            response = self.app.post(
                path, {"agent_id": AGENT, "task_id": "public-refused"},
                token=self.tokens["reader"])
            self.assertEqual(
                _status(response), 403,
                "%s answered %s for a caller the payload gave no verb for"
                % (path, response.status))
        self.assertEqual(checked, len(withheld),
                         "every consequential action must have a route")

    def test_the_owner_of_a_task_is_handed_the_consequential_verbs(self):
        """The positive control: the withheld verbs are not withheld from everyone."""
        from agent.tools.scheduler.authorization import (
            ACTION_MANAGE, ACTION_RUN,
        )

        self.app.personal_task(AGENT, self.ids["operator"], id="mine-1",
                               name="mine-1")
        body = self.assert_gate_accepts(
            "GET", "/api/scheduler?agent_id=%s" % AGENT, self.tokens["operator"])
        task = [t for t in body["tasks"] if t["id"] == "mine-1"]
        self.assertEqual(len(task), 1, body)
        caps = task[0]["capabilities"]
        self.assertTrue(caps[ACTION_MANAGE], caps)
        self.assertTrue(caps[ACTION_RUN], caps)
        # And the wire agrees with the payload for the owner.
        self.assert_gate_accepts(
            "POST", "/api/scheduler/toggle", self.tokens["operator"],
            payload={"agent_id": AGENT, "task_id": "mine-1", "enabled": False})


class PageLessSliceTests(_Fixture):
    """Project browsing and the QR entry: no page, same agreement."""

    def test_a_page_less_slice_is_projected_as_a_consumer_not_a_page(self):
        from auth import capability_matrix

        payload = self.payload("operator")
        for slice_id in PAGE_LESS_SLICES:
            spec = capability_matrix.slice_for(slice_id)
            self.assertFalse(spec.page, slice_id)
            consumer = payload["consumers"][spec.consumer]
            self.assertEqual(bool(consumer["available"]), spec.enabled, slice_id)
            for page_id, entry in payload["console_pages"].items():
                self.assertNotIn(
                    slice_id, json.dumps(entry),
                    "%s leaked into the page payload %s" % (slice_id, page_id))

    def test_the_read_entry_of_a_page_less_slice_is_not_a_gate_refusal(self):
        """Same assertion as the paged slices, where it applies."""
        from auth import capability_matrix

        for slice_id, action, method, path in ENTRIES:
            if slice_id not in PAGE_LESS_SLICES:
                continue
            spec = capability_matrix.slice_for(slice_id)
            if not spec.is_open(action):
                continue
            self.assert_gate_accepts(method, path, self.tokens["operator"],
                                     " (%s)" % slice_id)

    def test_the_project_browse_slice_separates_read_from_execute(self):
        """``browse`` is a read action, ``import`` is an execute action.

        The console may therefore show the folder picker without ever being able
        to publish anything: the import route's own per-request conditions
        (loopback, per-start token, one-time handle) are covered by
        ``tests/test_scoped_project_browse.py``; what is asserted here is the
        *class* separation the projection and the route both derive from.
        """
        from auth import capability_matrix

        classes = _classes("project_browse")
        self.assertEqual(classes.get("browse"), capability_matrix.ACCESS_READ)
        self.assertEqual(classes.get("import"), capability_matrix.ACCESS_EXECUTE)
        self.assertIn("browse", _read_actions("project_browse"))
        self.assertIn("import", _consequential_actions("project_browse"))
        for action in ("browse", "import"):
            entry = _registry_policy("project_browse", action)
            self.assertNotEqual(entry.get("policy"), "closed", action)

    def test_the_wechat_qr_entry_answers_from_its_handler_not_from_the_gate(self):
        """The QR entry is served: a refusal is the handler's, not a capability one.

        Blocker recorded in evidence 9: this environment has no WeChat session,
        and the handler currently cannot even build its actor (it calls ``.get``
        on an ``sqlite3.Row``) so every authenticated call reports a generic
        ``scan_failed``. That is a *handler* failure, not a gate refusal — which
        is exactly what this assertion pins, so fixing the handler cannot break
        it: the gate let the request through either way.
        """
        for method in ("GET", "POST"):
            body = self.assert_gate_accepts(
                method, "/api/weixin/qrlogin", self.tokens["operator"])
            self.assertTrue(
                body.get("code") or body.get("status") == "success",
                "%s /api/weixin/qrlogin answered without a handler envelope: %s"
                % (method, body))
            self.assertNotIn(body.get("code"), ("", "database_unavailable",
                                                "identity_unavailable"), body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
