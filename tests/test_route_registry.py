# encoding:utf-8
"""Single route registry + three-leg coverage invariant (change group 2).

Pins the defects measured before this change:

* ``channel/web/web_channel.py::_WEB_URLS`` (115 patterns) and
  ``auth/http_policy.py::ROUTE_POLICY`` (110 patterns) were two hand-maintained
  literals. The 5 paths present only in the URL table were not merely
  unpoliced: ``_match_policy`` reported "unknown URL", so ``enforce_http_policy``
  passed the request straight to a handler that only did ``_require_auth()``.
* The policy table declared ``GET /api/sessions/{id}`` although no handler
  implements ``GET`` (a dead entry), and omitted methods the handlers do
  implement (``POST /api/weixin/qrlogin``, ``POST /api/todos``,
  ``PATCH /api/todos/{id}``), which the completeness gate answered with 405.

Both tables are now derived from ``channel/web/route_registry.py``, and the
coverage invariant's third leg cross-checks the *handler implementations*
(the only non-tautological leg, since legs 1 and 2 are same-source).
"""

from __future__ import annotations

import os
import unittest

from auth import http_policy
from channel.web import route_registry, web_channel
from channel.web.route_registry import (
    ROUTES,
    RouteEntry,
    P,
    _FORK_ROUTES,
    check_route_coverage,
    derive_route_policy,
    derive_web_urls,
    register_fork_routes,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE = os.path.join(_REPO_ROOT, "scripts", "route-baseline.txt")

#: The five routes measured as registered in ``_WEB_URLS`` but absent from
#: ``ROUTE_POLICY``, with the verified policy each now carries (``/admin`` was
#: later revised from ``tenant`` to ``public`` by browser verification: a
#: document navigation cannot send ``X-Tenant-ID``, so a ``tenant`` shell route
#: answered 400 to every browser visit — see ``scripts/route-baseline.txt``).
_GAP_ROUTES = (
    ("/admin", "GET", "public"),
    ("/api/identity/administered-tenants", "GET", "personal"),
    ("/api/scenes", "GET", "tenant"),
    ("/api/scenes/activate", "POST", "tenant"),
    ("/api/scenes/workbench/import", "POST", "tenant"),
)


def _sample_path(pattern: str) -> str:
    """A concrete path a route's pattern matches, for first-match assertions."""
    out = pattern.replace("([^/]+)", "a")
    out = out.replace("(.*)", "a/b")
    out = out.replace("(.+)", "a/b")
    return out


def _baseline_rows():
    """Parse the frozen route baseline into ``{(pattern, method): (policy, perm)}``.

    The file is append-only, so the resolution sections at the bottom override
    the historical rows for the same route/method.
    """
    rows = {}
    with open(_BASELINE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            pattern, method, policy, permission = parts
            rows[(pattern, method)] = (policy, "" if permission == "-" else permission)
    return rows


class RegistryDerivationTests(unittest.TestCase):
    """``_WEB_URLS`` and ``ROUTE_POLICY`` are projections of one registry."""

    def test_both_tables_are_derived_from_the_registry(self):
        self.assertEqual(tuple(web_channel._WEB_URLS), derive_web_urls())
        self.assertEqual(http_policy.ROUTE_POLICY, derive_route_policy())

    def test_core_files_no_longer_carry_route_literals(self):
        """The tables must not be re-declared by hand in the core files."""
        with open(os.path.join(_REPO_ROOT, "channel", "web", "web_channel.py"),
                  encoding="utf-8") as fh:
            wc_src = fh.read()
        self.assertIn("_WEB_URLS = _derive_web_urls()", wc_src)
        self.assertNotIn("'/api/health', 'HealthHandler'", wc_src)

        with open(os.path.join(_REPO_ROOT, "auth", "http_policy.py"),
                  encoding="utf-8") as fh:
            policy_src = fh.read()
        self.assertIn("derive_route_policy()", policy_src)
        self.assertNotIn('"/api/tenant":', policy_src)
        self.assertNotIn("'/api/health'", policy_src)

    def test_no_duplicate_patterns(self):
        policy = derive_route_policy()
        self.assertEqual(len(policy), len(ROUTES))

    def test_every_route_is_source_tagged(self):
        for entry in ROUTES:
            self.assertTrue(
                entry.source == "upstream" or entry.source.startswith("fork:"),
                "route %r has source %r" % (entry.pattern, entry.source))

    def test_stream_declares_its_tenant_is_resource_derived(self):
        """The one route whose tenant cannot come from a header (group 3).

        A native ``EventSource`` reconnect sends the session cookie but no
        ``X-Tenant-ID``, so the gate must not demand a selection there.
        Recording that in the registry keeps the exemption explicit and
        reviewable instead of a special case buried in the gate.
        """
        entry = derive_route_policy()["/stream"]["GET"]
        self.assertEqual(entry["policy"], "tenant")
        self.assertTrue(entry.get("tenant_from_resource"))

    def test_upload_readback_declares_its_tenant_is_resource_derived(self):
        """The other header-less route: an ``<img>``/``<audio>`` subresource.

        The console loads uploaded thumbnails and clips straight from
        ``/uploads/...``, so the browser issues the GET itself and cannot attach
        ``X-Tenant-ID``. Like ``/stream``, the exemption is recorded in the
        registry rather than special-cased in the gate; the handler derives the
        tenant from the addressed Agent's binding.
        """
        entry = derive_route_policy()["/uploads/(.*)"]["GET"]
        self.assertEqual(entry["policy"], "tenant")
        self.assertTrue(entry.get("tenant_from_resource"))

    def test_file_serve_declares_its_tenant_is_resource_derived(self):
        """The download/preview asset URL is also a browser-native request.

        An artifact card downloads through ``<a href=/api/file?path=...>`` and
        the console renders message images as ``<img src=/api/file?...>``; both
        are issued by the browser and cannot attach ``X-Tenant-ID``. The
        exemption is recorded in the registry, and the handler derives the
        tenant from the addressed file's own workspace.
        """
        entry = derive_route_policy()["/api/file"]["GET"]
        self.assertEqual(entry["policy"], "tenant")
        self.assertTrue(entry.get("tenant_from_resource"))

    def test_a_plain_tenant_route_does_not_claim_the_exemption(self):
        entry = derive_route_policy()["/api/sessions"]["GET"]
        self.assertEqual(entry["policy"], "tenant")
        self.assertFalse(entry.get("tenant_from_resource", False))


class FrozenBaselineEquivalenceTests(unittest.TestCase):
    """The migration must be behavior-preserving for every pre-existing entry."""

    def test_derived_policy_matches_frozen_baseline(self):
        derived = derive_route_policy()
        checked = 0
        for (pattern, method), (policy, permission) in _baseline_rows().items():
            if policy == "UNREGISTERED":
                continue  # historical gap, asserted by the next test
            entry = derived.get(pattern, {}).get(method)
            if policy == "REMOVED":
                # A dead registration (declared, but no handler method).
                self.assertIsNone(entry, "%s %s should have been removed" % (pattern, method))
                continue
            self.assertIsNotNone(entry, "%s %s is missing from the registry" % (pattern, method))
            self.assertEqual(entry["policy"], policy, "%s %s" % (pattern, method))
            self.assertEqual(entry.get("permission", ""), permission,
                             "%s %s permission" % (pattern, method))
            checked += 1
        self.assertGreater(checked, 130)

    def test_previously_unregistered_routes_are_now_gated(self):
        """Regression pin: these used to bypass ``enforce_http_policy``.

        Before the change each returned ``(None, False)`` from
        ``_match_policy`` -- "unknown URL" -- so a request that web.py *did*
        route reached a handler guarded only by ``_require_auth()``.
        """
        for pattern, method, policy in _GAP_ROUTES:
            entry, matched = http_policy._match_policy(pattern, method)
            self.assertTrue(matched, "%s %s must match a route" % (pattern, method))
            self.assertIsNotNone(entry, "%s %s must have a policy entry" % (pattern, method))
            self.assertEqual(entry["policy"], policy, "%s %s" % (pattern, method))

    def test_declared_but_unimplemented_method_is_gone(self):
        """``GET /api/sessions/{id}`` was dead: no handler implements it."""
        self.assertIsNone(getattr(web_channel.SessionDetailHandler, "GET", None))
        entry, matched = http_policy._match_policy("/api/sessions/sess_a", "GET")
        self.assertTrue(matched)
        self.assertIsNone(entry, "a dead registration would let the gate pass")

    def test_implemented_but_undeclared_methods_are_now_registered(self):
        """These handlers documented a method the policy table omitted (405)."""
        for pattern, method in (("/api/weixin/qrlogin", "POST"),
                                ("/api/todos", "POST"),
                                ("/api/todos/item_1", "PATCH")):
            entry, matched = http_policy._match_policy(pattern, method)
            self.assertTrue(matched, "%s %s" % (pattern, method))
            self.assertIsNotNone(entry, "%s %s must be registered" % (pattern, method))


class CoverageInvariantTests(unittest.TestCase):
    """Leg 3 must compare against handler implementations, not the registry."""

    def test_invariant_holds_for_the_real_registry(self):
        self.assertEqual(check_route_coverage(vars(web_channel)), [])

    def test_handler_method_missing_from_registry_fails(self):
        """The task-2.8 case: handler implements POST, registry only says GET."""
        class _ProbeHandler:
            def GET(self):
                return "get"

            def POST(self):
                return "post"

        routes = (RouteEntry("/probe", "_ProbeHandler", "fork:test",
                             {"GET": P("tenant")}),)
        violations = check_route_coverage({"_ProbeHandler": _ProbeHandler}, routes)
        self.assertTrue(any("implements POST" in v for v in violations), violations)

    def test_registered_method_absent_from_handler_fails(self):
        """The ``GET /api/sessions/{id}`` shape: registered but not implemented."""
        class _ProbeHandler:
            def GET(self):
                return "get"

        routes = (RouteEntry("/probe", "_ProbeHandler", "fork:test",
                             {"GET": P("tenant"), "DELETE": P("tenant")}),)
        violations = check_route_coverage({"_ProbeHandler": _ProbeHandler}, routes)
        self.assertTrue(any("does not implement it" in v for v in violations), violations)

    def test_unknown_handler_fails(self):
        routes = (RouteEntry("/probe", "NopeHandler", "fork:test",
                             {"GET": P("tenant")}),)
        violations = check_route_coverage({}, routes)
        self.assertTrue(any("unknown handler" in v for v in violations), violations)


class OrderingTests(unittest.TestCase):
    """First match wins, so a route must resolve to its own entry."""

    def test_registry_order_preserves_first_match_semantics(self):
        for entry in ROUTES:
            sample = _sample_path(entry.pattern)
            for method, expected in entry.methods.items():
                resolved, matched = http_policy._match_policy(sample, method)
                self.assertTrue(matched, "%s %s (%s)" % (entry.pattern, method, sample))
                self.assertIsNotNone(resolved, "%s %s (%s)" % (entry.pattern, method, sample))
                self.assertEqual(
                    resolved, expected,
                    "%s %s resolved to a different route's policy" % (sample, method))


class ForkExtensionTests(unittest.TestCase):
    """Fork routes register without editing the core literal (task 2.6/2.11)."""

    def test_registered_fork_route_lands_in_both_tables(self):
        entry = RouteEntry("/api/fork/probe", "HealthHandler", "fork:probe",
                           {"GET": P("tenant", comment="probe")})
        saved = list(_FORK_ROUTES)
        try:
            register_fork_routes(entry)
            urls = derive_web_urls()
            policy = derive_route_policy()
        finally:
            del _FORK_ROUTES[:]
            _FORK_ROUTES.extend(saved)

        self.assertIn("/api/fork/probe", urls)
        self.assertEqual(urls[urls.index("/api/fork/probe") + 1], "HealthHandler")
        self.assertEqual(policy["/api/fork/probe"]["GET"]["policy"], "tenant")
        self.assertEqual(check_route_coverage(vars(web_channel), (entry,)), [])

    def test_core_files_were_not_edited_for_the_extension(self):
        for rel in (("channel", "web", "web_channel.py"), ("auth", "http_policy.py")):
            with open(os.path.join(_REPO_ROOT, *rel), encoding="utf-8") as fh:
                self.assertNotIn("/api/fork/probe", fh.read(), rel)


class PersonalConsoleRouteTests(unittest.TestCase):
    """Method-level pin for the member personal console routes (task 9.3).

    ``personal`` is the entire route-level claim: a session, no more. The tenant
    comes from the verified request context and the owner from that session, so a
    route-level permission would only be a second, weaker authority next to the
    owner/menu/functional checks the handler runs. These assertions freeze the
    policy *and* the method set, because a route silently gaining or losing a
    method is exactly the drift the frozen baseline exists to catch.
    """

    PERSONAL_ROUTES = (
        ("/api/memory/personal", "GET"),
        ("/api/memory/personal", "POST"),
        ("/api/memory/personal/content", "GET"),
        ("/api/personal/channels", "GET"),
        ("/api/personal/channels", "POST"),
        ("/api/personal/channels/([^/]+)", "GET"),
        ("/api/personal/channels/([^/]+)", "POST"),
        ("/api/personal/resources", "GET"),
        ("/api/personal/resources", "POST"),
    )

    #: Routes a member must never reach through the personal surface, with the
    #: policy each keeps. They are asserted together so a future edit that
    #: "simplifies" one of them into `personal` fails here.
    UNCHANGED_MANAGEMENT_ROUTES = (
        ("/api/channels", "GET", "platform"),
        ("/api/channels", "POST", "platform"),
        ("/api/tenant/channels", "GET", "tenant"),
        ("/api/tenant/channels", "POST", "tenant"),
        ("/api/agents", "GET", "tenant"),
        ("/api/agents", "POST", "tenant"),
    )

    def test_every_personal_route_is_session_scoped_and_carries_no_permission(self):
        derived = derive_route_policy()
        for pattern, method in self.PERSONAL_ROUTES:
            entry = derived.get(pattern, {}).get(method)
            self.assertIsNotNone(entry, "%s %s is not registered" % (pattern, method))
            self.assertEqual(entry["policy"], "personal", "%s %s" % (pattern, method))
            self.assertEqual(entry.get("permission", ""), "",
                             "%s %s must not claim a route-level permission"
                             % (pattern, method))
            # A tenant-scoped policy would answer 400 before the handler whenever a
            # browser cannot send the header; a public one would skip the session.
            self.assertNotIn(entry["policy"], ("public", "tenant", "platform"),
                             "%s %s" % (pattern, method))

    def test_the_frozen_baseline_records_exactly_these_methods(self):
        rows = _baseline_rows()
        for pattern, method in self.PERSONAL_ROUTES:
            self.assertEqual(rows.get((pattern, method)), ("personal", ""),
                             "%s %s" % (pattern, method))

    def test_the_management_surfaces_keep_their_administrator_policies(self):
        derived = derive_route_policy()
        for pattern, method, policy in self.UNCHANGED_MANAGEMENT_ROUTES:
            entry = derived.get(pattern, {}).get(method)
            self.assertIsNotNone(entry, "%s %s" % (pattern, method))
            self.assertEqual(entry["policy"], policy, "%s %s" % (pattern, method))
            self.assertNotEqual(entry["policy"], "personal",
                                "%s %s must not become a self surface"
                                % (pattern, method))

    def test_a_session_is_required_before_the_handler_runs(self):
        """`personal` still authenticates: the gate's `require_tenant` is False,
        but the context it resolves must exist. Asserted on the real gate so a
        policy string alone cannot pass this."""
        for pattern, method in self.PERSONAL_ROUTES:
            entry, matched = http_policy._match_policy(
                _sample_path(pattern), method)
            self.assertTrue(matched, "%s %s" % (pattern, method))
            self.assertEqual(entry["policy"], "personal", "%s %s" % (pattern, method))


if __name__ == "__main__":
    unittest.main()
