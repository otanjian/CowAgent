"""Upstream-core seams: no fork definitions in place, upstream features kept.

Two obligations from groups 8 and 11, as executable checks rather than review
notes:

* the fork's own logic must reach upstream files through a seam — a hook, a
  registry, a composed schema — and not as an in-place branch or a rewritten
  signature, because an in-place branch is what makes every upstream edit a
  conflict (tasks 8.3, 8.9, 8.11);
* upstream features the fork deleted locally must be *kept* when the merge
  brings them back, with their security checks intact (task 8.4,
  ``scripts/conflict-baseline.txt`` records the obligation).

The first kind fails today if someone reintroduces a fork branch; the second
kind activates the moment the upstream symbol lands, and asserts the recorded
obligation in the meantime so the promise cannot be quietly dropped.
"""
import os
import re
import sys
import unittest

from tests._helpers import web_layer_source
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class StartupSeamTests(unittest.TestCase):
    """8.9/8.11: app.py runs hooks, it does not contain the fork's guards."""

    def test_app_py_has_no_inlined_fork_guard(self):
        source = _read("app.py")
        # The guard logic lives in common/startup_hooks.py; app.py may only ask
        # the registry to run it.
        self.assertNotIn("refuse_legacy_after_migration", source)
        self.assertIn("run_startup_hook", source)

    def test_app_py_boot_sequence_runs_both_fork_hooks(self):
        source = _read("app.py")
        self.assertIn("_guard_identity_mode_consistency()", source)
        self.assertIn("_migrate_conversation_tenancy()", source)

    def test_the_guards_exist_in_the_hook_module(self):
        source = _read("common", "startup_hooks.py")
        self.assertIn("def _identity_mode_consistency", source)
        self.assertIn("def _tenant_conversation_backfill", source)


class RouteSeamTests(unittest.TestCase):
    """8.11: route literals live in the registry, not in the handler file."""

    def test_web_channel_derives_its_urls_from_the_registry(self):
        source = _read("channel", "web", "web_channel.py")
        self.assertIn("from channel.web.route_registry import", source)
        self.assertIn("_derive_web_urls", source)


class SchemaSeamTests(unittest.TestCase):
    """8.11: the store's schema is composed, not literal."""

    def test_the_conversation_store_uses_the_composable_schema(self):
        source = _read("agent", "memory", "conversation_store.py")
        self.assertIn("conversation_schema", source)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS sessions", source)


class WorkspaceTenancySeamTests(unittest.TestCase):
    """8.1/8.3: the fork's tenancy rule for the working root is not inline.

    ``_get_workspace_root`` was upstream's function; the web-split change moved
    the fork's implementation of it into ``channel/web/fork/`` (design D2), so
    the file-level seam is now the fork package itself. What this guards is
    unchanged: the root comes from the caller's tenant, and that derivation
    lives in ``channel/web/tenant_workspace.py``, so the function body is
    interrupted by a single call instead of re-implementing the rule.

    Assertions read the whole web layer (``tests/_helpers.web_layer_source``)
    rather than one file, so moving the function between fork modules cannot
    quietly turn this into a no-op.
    """

    def test_web_channel_does_not_inline_tenant_workspace_logic(self):
        source = web_layer_source()
        start = source.index("def _get_workspace_root(")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        self.assertIn("resolve_tenant_workspace_root", body)
        # The tenancy derivation itself is not in upstream's function any more.
        self.assertNotIn("tenant_shared_root(", body)

    def setUp(self):
        # ``web.HTTPError`` builds response headers through ``web.ctx``; outside
        # a real request the context has no header list yet.
        import web
        if not hasattr(web.ctx, "headers"):
            web.ctx.headers = []

    def test_an_empty_tenant_scope_is_refused(self):
        import web
        from channel.web.tenant_workspace import resolve_tenant_workspace_root
        from common.runtime_identity import RuntimeIdentity, use_identity

        with use_identity(RuntimeIdentity()):
            with self.assertRaises(web.HTTPError) as caught:
                resolve_tenant_workspace_root()
        self.assertEqual(caught.exception.args[0], "403 Forbidden")

    def test_explicit_legacy_identity_mode_refuses_to_boot(self):
        """Task 5.1: explicit ``identity_mode=legacy`` aborts startup."""
        from unittest import mock
        from common import startup_hooks

        with mock.patch("config.conf", return_value={"identity_mode": "legacy"}):
            with self.assertRaises(RuntimeError):
                startup_hooks._identity_mode_consistency()

    def test_app_py_still_only_runs_guards_via_startup_hook(self):
        source = _read("app.py")
        self.assertIn("run_startup_hook", source)
        self.assertNotIn("def _identity_mode_consistency", source)

    def test_a_resolved_tenant_root_is_returned(self):
        from unittest import mock

        from channel.web.tenant_workspace import resolve_tenant_workspace_root
        from common.runtime_identity import RuntimeIdentity, use_identity

        service = mock.Mock()
        service.tenant_shared_root.return_value = "/s/alpha"
        with mock.patch("auth.service.get_identity_service", return_value=service):
            with use_identity(RuntimeIdentity(tenant_id="tnt-alpha")):
                self.assertEqual(
                    resolve_tenant_workspace_root(), "/s/alpha")

    def test_a_tenant_without_a_root_is_refused_not_defaulted(self):
        from unittest import mock

        import web
        from channel.web.tenant_workspace import resolve_tenant_workspace_root
        from common.runtime_identity import RuntimeIdentity, use_identity

        service = mock.Mock()
        service.tenant_shared_root.return_value = None
        with mock.patch("auth.service.get_identity_service", return_value=service):
            with use_identity(RuntimeIdentity(tenant_id="tnt-alpha")):
                with self.assertRaises(web.HTTPError):
                    resolve_tenant_workspace_root()


class UpstreamFeaturePreservationTests(unittest.TestCase):
    """8.4: upstream features the fork dropped locally must come back guarded."""

    def test_the_merge_obligation_is_recorded(self):
        source = _read("scripts", "conflict-baseline.txt")
        self.assertIn("_import_local_file", source)

    def test_local_file_import_is_loopback_and_token_guarded_when_present(self):
        source = web_layer_source()
        if "_import_local_file" not in source:
            # Upstream has not landed the feature in this fork's tree yet. The
            # obligation is recorded above; nothing to assert on behaviour, and
            # asserting its absence would contradict the promise to keep it.
            self.skipTest("upstream _import_local_file not merged yet (obligation recorded)")
        # Once merged, the feature must exist unchanged *and* keep the checks
        # that stop a remote caller from asking the server to read a local path.
        self.assertIn("_import_local_file", source)
        window = source[source.index("_import_local_file"):]
        window = window[:window.index("def ", 1)] if "def " in window[1:] else window
        self.assertTrue(
            re.search(r"REMOTE_ADDR|127\.0\.0\.1|::1", window),
            "local-file import must verify the request is loopback",
        )
        self.assertTrue(
            re.search(r"token", window, re.IGNORECASE),
            "local-file import must verify the per-boot token",
        )

    def test_the_fork_keeps_no_unauthenticated_local_path_route(self):
        """A path-taking route must be published in the registry as restricted.

        The fork removed the local-path import endpoint; if a future merge adds
        a route that reads an arbitrary server path, it has to be registered
        (and therefore policy-checked), not silently mounted.
        """
        from channel.web import route_registry
        policies = route_registry.derive_route_policy()
        urls = route_registry.derive_web_urls()
        for url in urls:
            if "local" in url and "file" in url:
                self.assertIn(
                    url, policies,
                    f"{url} reads local paths but is not policy-registered")


if __name__ == "__main__":
    unittest.main()
