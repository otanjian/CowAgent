# encoding:utf-8
"""The boot refuses to read the new external-connection store too early (12.3).

``store_version`` selects which store the external-connection runtime reads.
``new`` is only safe once every importable legacy record has been imported, and
before this task the check that says so — ``assert_store_version_safe`` — had no
caller: a deployment could switch to ``new`` and have the runtime read an empty
store while the tenant ERP files and per-Agent ``mcp.json`` still held the real
connections. The failure would look like "my connections disappeared", with the
data still on disk.

What each test pins
-------------------
* The shipped default (``legacy``) boots: the guard must not be a new way for a
  normal deployment to fail.
* ``new`` with unimported legacy records refuses the boot, with a message that
  names the fix — the whole value of the check is in what it tells the operator.
* ``new`` with nothing pending boots. A guard that refuses even the safe case
  would be turned off rather than fixed.
* A scan that breaks while the deployment reads ``legacy`` only warns: refusing
  to boot over a store nobody is reading is an availability bug.
* The seam is a registry hook with an ``app.py`` entry point, and it is in the
  required manifest so a dropped registration cannot silently disable it.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from common import startup_hooks
from common.startup_hooks import (
    HOOK_EXTERNAL_STORE_VERSION,
    is_registered,
)

from tests._helpers import build_identity


class LegacyModeTests(unittest.TestCase):
    """The default deployment is not affected."""

    def test_the_shipped_default_does_not_even_scan(self):
        """``legacy`` boots without paying for the legacy scan.

        The shipped default is ``legacy``, and the guard must not turn a normal
        boot into a full walk of every tenant root and Agent workspace. Asserted
        by the scan never being reached, not by reading the clock.
        """
        from common.startup_hooks import _external_store_version_guard

        with patch("integrations.external.migration.active_store_version",
                   return_value="legacy"), \
                patch("integrations.external.migration."
                      "assert_store_version_safe",
                      side_effect=AssertionError("must not scan in legacy mode")):
            _external_store_version_guard()  # must not raise

    def test_a_broken_scan_does_not_refuse_a_legacy_boot(self):
        """An unused store must not be able to take the console down."""
        from common.startup_hooks import _external_store_version_guard

        with patch("integrations.external.migration.active_store_version",
                   return_value="legacy"), \
                patch("auth.service.get_identity_service",
                      side_effect=RuntimeError("store unreadable")):
            _external_store_version_guard()  # must not raise


class NewModeTests(unittest.TestCase):
    """``new`` is the mode the check exists for."""

    def test_new_with_unimported_legacy_records_refuses_the_boot(self):
        from integrations.external.migration import MigrationError
        from common.startup_hooks import _external_store_version_guard

        with tempfile.TemporaryDirectory() as tmp:
            stack = build_identity(tmp)
            with patch("integrations.external.migration.active_store_version",
                       return_value="new"), \
                    patch("auth.service.get_identity_service",
                          return_value=stack.service), \
                    patch(
                        "integrations.external.migration."
                        "assert_store_version_safe",
                        side_effect=MigrationError(
                            "store_version='new' is unsafe: 2 legacy record(s)"
                            " are unimported")):
                with self.assertRaises(MigrationError) as caught:
                    _external_store_version_guard()
                self.assertIn("unimported", str(caught.exception))

    def test_new_with_nothing_pending_boots(self):
        from common.startup_hooks import _external_store_version_guard

        with tempfile.TemporaryDirectory() as tmp:
            stack = build_identity(tmp)
            with patch("integrations.external.migration.active_store_version",
                       return_value="new"), \
                    patch("auth.service.get_identity_service",
                          return_value=stack.service), \
                    patch(
                        "integrations.external.migration."
                        "assert_store_version_safe",
                        return_value={"store_version": "new", "ok": True,
                                      "imported": 3}):
                _external_store_version_guard()  # must not raise

    def test_an_unreadable_mode_never_refuses_the_boot(self):
        """Not knowing the mode is not an entitlement to refuse."""
        from common.startup_hooks import _external_store_version_is_new

        with patch("integrations.external.migration.active_store_version",
                   side_effect=RuntimeError("no config")):
            self.assertFalse(_external_store_version_is_new())


class SeamTests(unittest.TestCase):
    def test_the_guard_is_registered(self):
        self.assertTrue(is_registered(HOOK_EXTERNAL_STORE_VERSION))

    def test_the_manifest_covers_it(self):
        """A dropped registration would make the check a silent no-op."""
        self.assertIn(HOOK_EXTERNAL_STORE_VERSION,
                      startup_hooks.REQUIRED_HOOKS)

    def test_the_boot_calls_the_entry_point(self):
        import app

        with patch("common.startup_hooks.run_startup_hook",
                   return_value=True) as run:
            self.assertTrue(app._guard_external_store_version())
        run.assert_called_once_with(HOOK_EXTERNAL_STORE_VERSION)

    def test_the_entry_point_is_in_the_run_sequence(self):
        with open(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "app.py"), encoding="utf-8") as handle:
            source = handle.read()
        boot = source[source.index("def run():"):]
        self.assertIn("_guard_external_store_version()", boot)
        # After the identity guard: a boot with no identity has no store to
        # check, and a refusal must name the identity problem first.
        self.assertLess(boot.index("_guard_identity_mode_consistency()"),
                        boot.index("_guard_external_store_version()"))
