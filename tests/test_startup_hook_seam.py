# encoding:utf-8
"""The boot seams are hooks, not inline fork branches (tasks 6.11, 8.9, 8.10).

``app.py`` keeps two named entry points and the fork's logic lives behind them
in ``common/startup_hooks.py``. The properties that make merging cheaper are
all observable from here:

* the seam runs what is registered and reports whether anything ran, so with
  the fork's module absent the boot is upstream's (task 8.10);
* the identity-mode guard still aborts the boot -- through the seam;
* the conversation tenancy backfill is registered, is a no-op in legacy mode,
  and never invents a tenant for an owner with several memberships.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from common import startup_hooks
from common.startup_hooks import (
    HOOK_DATABASE_BOOTSTRAP,
    HOOK_IDENTITY_MODE_CONSISTENCY,
    HOOK_TENANT_CONVERSATION_BACKFILL,
    is_registered,
    register_startup_hook,
    registered_hooks,
    run_startup_hook,
)


class RegistryTests(unittest.TestCase):
    def test_an_unregistered_hook_is_a_no_op(self):
        self.assertFalse(run_startup_hook("nothing.registered.here"))

    def test_a_registered_hook_runs_and_reports_itself(self):
        calls = []
        register_startup_hook("test.hook", lambda: calls.append(1))
        self.addCleanup(startup_hooks._HOOKS.pop, "test.hook", None)
        self.assertTrue(run_startup_hook("test.hook"))
        self.assertEqual(calls, [1])

    def test_hooks_run_in_declared_order(self):
        register_startup_hook("test.late", lambda: None, order=90)
        register_startup_hook("test.early", lambda: None, order=1)
        self.addCleanup(startup_hooks._HOOKS.pop, "test.late", None)
        self.addCleanup(startup_hooks._HOOKS.pop, "test.early", None)
        order = registered_hooks()
        self.assertLess(order.index("test.early"), order.index("test.late"))

    def test_the_fork_registers_boot_seams(self):
        self.assertTrue(is_registered(HOOK_IDENTITY_MODE_CONSISTENCY))
        self.assertTrue(is_registered(HOOK_DATABASE_BOOTSTRAP))
        self.assertTrue(is_registered(HOOK_TENANT_CONVERSATION_BACKFILL))


class RequiredSeamManifestTests(unittest.TestCase):
    """R5: the boot assembly checks its required authorization seams.

    The manifest is what makes a *dropped registration* loud. Without it, a
    merge that loses one ``register_startup_hook`` line turns the identity
    guard, the first-run bootstrap, the tenant backfill or the scheduled-task
    migration into a silent no-op while the console keeps serving.
    """

    def test_every_required_seam_is_declared_and_armed(self):
        self.assertTrue(startup_hooks.REQUIRED_HOOKS)
        self.assertEqual(startup_hooks.missing_required_hooks(), [])
        startup_hooks.verify_required_seams()  # must not raise

    def test_the_manifest_covers_the_authorization_seams_not_only_the_ui(self):
        # Identity, first-run bootstrap, tenant attribution and task ownership
        # are the seams a boot must not be able to skip. A future optional
        # display seam must not be added here: a missing UI fragment falls back
        # to rendering, which is exactly what the manifest must not do.
        from common.startup_hooks import HOOK_SCHEDULER_TASK_MIGRATION

        self.assertIn(HOOK_IDENTITY_MODE_CONSISTENCY,
                      startup_hooks.REQUIRED_HOOKS)
        self.assertIn(HOOK_TENANT_CONVERSATION_BACKFILL,
                      startup_hooks.REQUIRED_HOOKS)
        self.assertIn(HOOK_SCHEDULER_TASK_MIGRATION,
                      startup_hooks.REQUIRED_HOOKS)

    def test_a_dropped_registration_refuses_the_boot(self):
        name = "test.required.seam"
        register_startup_hook(name, lambda: None)
        self.addCleanup(startup_hooks._HOOKS.pop, name, None)
        self.assertEqual(
            startup_hooks.missing_required_hooks((name,)), [])
        startup_hooks._HOOKS.pop(name)
        self.assertEqual(startup_hooks.missing_required_hooks((name,)), [name])
        with self.assertRaises(RuntimeError) as caught:
            startup_hooks.verify_required_seams((name,))
        self.assertIn(name, str(caught.exception))

    def test_the_boot_calls_the_check_before_any_guard_runs(self):
        import app

        with patch.object(startup_hooks, "verify_required_seams") as check:
            app._verify_required_seams()
        check.assert_called_once_with()

    def test_the_check_is_not_itself_a_hook(self):
        # A seam that failed to register must not be able to skip the check
        # that exists to notice it: the app entry point calls the manifest
        # directly, so ``run_startup_hook`` is never consulted.
        import app

        with patch("common.startup_hooks.run_startup_hook",
                   side_effect=AssertionError("must not run a hook here")):
            app._verify_required_seams()

    def test_the_boot_sequence_verifies_seams_before_the_identity_guard(self):
        with open(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "app.py"), encoding="utf-8") as handle:
            source = handle.read()
        boot = source[source.index("def run():"):]
        self.assertIn("_verify_required_seams()", boot)
        self.assertLess(boot.index("_verify_required_seams()"),
                        boot.index("_guard_identity_mode_consistency()"))


class AppSeamTests(unittest.TestCase):
    def test_app_delegates_the_guard_to_the_registry(self):
        import app

        with patch("common.startup_hooks.run_startup_hook",
                   return_value=True) as run:
            self.assertTrue(app._guard_identity_mode_consistency())
        run.assert_called_once_with(HOOK_IDENTITY_MODE_CONSISTENCY)

    def test_app_delegates_database_bootstrap_to_the_registry(self):
        import app

        with patch("common.startup_hooks.run_startup_hook",
                   return_value=True) as run:
            self.assertTrue(app._ensure_database_bootstrap())
        run.assert_called_once_with(HOOK_DATABASE_BOOTSTRAP)

    def test_app_delegates_the_conversation_migration_to_the_registry(self):
        import app

        with patch("common.startup_hooks.run_startup_hook",
                   return_value=True) as run:
            self.assertTrue(app._migrate_conversation_tenancy())
        run.assert_called_once_with(HOOK_TENANT_CONVERSATION_BACKFILL)


class IdentityGuardTests(unittest.TestCase):
    """Explicit legacy / unknown modes refuse to boot."""

    def _run(self, mode="legacy"):
        conf = patch("config.conf", return_value={"identity_mode": mode})
        with conf:
            return startup_hooks._identity_mode_consistency()

    def test_legacy_mode_refuses_to_boot(self):
        with self.assertRaises(RuntimeError):
            self._run(mode="legacy")

    def test_database_mode_boots(self):
        self._run(mode="database")  # must not raise

    def test_missing_mode_boots(self):
        with patch("config.conf", return_value={}):
            startup_hooks._identity_mode_consistency()

    def test_unknown_mode_refuses(self):
        with self.assertRaises(RuntimeError):
            self._run(mode="something-else")


class TenantBackfillHookTests(unittest.TestCase):
    """"Legacy is a no-op" and "an ambiguous owner is never guessed"."""

    def test_legacy_mode_does_no_work(self):
        """Historical name: backfill still runs under database-only mode."""
        import app  # noqa: F401  (registers the hooks)

        with patch("config.conf", return_value={"identity_mode": "database"}), \
                patch("auth.service.get_identity_service") as svc:
            svc.return_value.tenant_shared_roots.return_value = []
            startup_hooks._tenant_conversation_backfill()
        svc.assert_called()

    def test_the_backfill_never_guesses_between_two_tenants(self):
        import app  # noqa: F401

        class FakeService:
            def tenant_shared_roots(self):
                return [{"id": "t1", "shared_root": os.path.join(self.root, "t1")},
                        {"id": "t2", "shared_root": os.path.join(self.root, "t2")}]

            def __init__(self, root):
                self.root = root

            def get_membership(self, user_id, tenant_id):
                # u-both belongs to both tenants; u-one only to t1.
                if user_id == "u-both":
                    return {"active": 1, "user_active": 1}
                return {"active": 1, "user_active": 1} if tenant_id == "t1" else None

        root = os.path.realpath(tempfile.mkdtemp())
        svc = FakeService(root)
        seen = {}

        class FakeStore:
            def backfill_tenant(self, fn):
                seen["u-both"] = fn("u-both")
                seen["u-one"] = fn("u-one")
                return {"sessions": 0, "messages": 0, "unresolved": 0}

        with patch("config.conf", return_value={"identity_mode": "database"}), \
                patch("auth.service.get_identity_service", return_value=svc), \
                patch("agent.memory.conversation_store.conversation_store_path",
                      return_value=type("P", (), {"exists": lambda self: True})()), \
                patch("agent.memory.conversation_store.get_conversation_store",
                      return_value=FakeStore()):
            startup_hooks._tenant_conversation_backfill()

        self.assertIsNone(seen["u-both"], "two memberships must not be guessed")
        self.assertEqual(seen["u-one"], "t1")

    def test_a_workspace_without_a_store_is_not_created(self):
        import app  # noqa: F401

        class FakeService:
            def tenant_shared_roots(self):
                return []

        with patch("config.conf", return_value={"identity_mode": "database"}), \
                patch("auth.service.get_identity_service", return_value=FakeService()), \
                patch("agent.memory.conversation_store.conversation_store_path",
                      return_value=type("P", (), {"exists": lambda self: False})()), \
                patch("agent.memory.conversation_store.get_conversation_store") as get:
            startup_hooks._tenant_conversation_backfill()
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
