# encoding:utf-8
"""Tenant-creation shared-root containment tests (3.9 cross-tenant rule at write time).

Reproduces the production incident shape: the default tenant registers the
engineering workspace as its shared root, and a second tenant created from the
web console used to derive ``<workspace>/tenants/<code>`` -- silently nested
inside the default tenant's root. Every later ``state_dir.shared_root()`` then
failed the read-time containment guard ("overlaps tenant") for BOTH tenants.

The fix moves the containment check to creation time: creating a tenant whose
shared root equals/contains/is contained by an existing tenant's root is refused
with a clear error before any row is written, and deriving a root for a new
tenant never nests under an existing tenant's root (a configured deployment
base is required when the engineering workspace is already the default tenant's
root).
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from config import conf


class TenantCreateContainmentTests(unittest.TestCase):
    """Default tenant root == engineering workspace (the incident shape)."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.eng = os.path.join(tmp, "eng")          # engineering workspace
        os.makedirs(self.eng)
        self.base = os.path.join(tmp, "tenants-base")  # operator-configured base
        os.makedirs(self.base)
        self.disjoint = os.path.join(tmp, "disjoint")  # an unrelated directory
        os.makedirs(self.disjoint)
        registry = AgentRegistry(
            [AgentProfile(id="alpha", name="Alpha", workspace=self.eng)], "alpha")
        set_agent_registry(registry)
        self.addCleanup(lambda: set_agent_registry(None))

        self.db = os.path.join(tmp, "identity.db")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="default", tenant_name="默认", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.eng, allow_weak=True)
        self.default_tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]

    def _create(self, code, shared_root=None):
        return self.svc.create_tenant(
            actor_user_id=self.root["id"], code=code, name=code.title(),
            admin_username="a" + code, admin_display="A" + code.title(),
            admin_password="Str0ngPass9", recent_password="Str0ngAdminPass",
            shared_root=shared_root)

    def _tenant_codes(self):
        return [t["code"] for t in self.svc.list_tenants()]

    def test_create_without_base_rejected_when_workspace_is_default_root(self):
        """No configured base and workspace == default root: refuse to derive
        a nested root (503 config_error), persist nothing."""
        # Force the "no controlled base" condition regardless of ambient state:
        # another test may have left COW_TENANT_BASE behind, or -- since
        # ``get_tenant_shared_base()`` falls back to the config key once the env
        # var is empty -- a test that called ``load_config()`` may have pulled a
        # ``tenant_shared_base`` out of a checked-in ``config.json``. Patch both
        # channels, so this test always exercises the "nothing configured" path
        # instead of silently deriving a root from an ambient base.
        with patch.dict(os.environ, {"COW_TENANT_BASE": ""}, clear=False), \
                patch.dict(conf(), {"tenant_shared_base": ""}):
            with self.assertRaises(IdentityServiceError) as cm:
                self._create("e2e-target")
        self.assertEqual(cm.exception.code, "config_error")
        self.assertEqual(cm.exception.status, 503)
        self.assertNotIn("e2e-target", self._tenant_codes())

    def test_explicit_root_nested_inside_default_root_rejected(self):
        """shared_root under the default tenant's root must be refused at
        creation (409 shared_root_conflict), not silently poison resolution."""
        with self.assertRaises(IdentityServiceError) as cm:
            self._create("nested", shared_root=os.path.join(self.eng, "tenants", "nested"))
        self.assertEqual(cm.exception.code, "shared_root_conflict")
        self.assertIn("overlaps tenant", str(cm.exception))
        self.assertNotIn("nested", self._tenant_codes())

    def test_explicit_root_equal_to_default_root_rejected(self):
        with self.assertRaises(IdentityServiceError) as cm:
            self._create("same", shared_root=self.eng)
        self.assertEqual(cm.exception.code, "shared_root_conflict")
        self.assertIn("overlaps tenant", str(cm.exception))
        self.assertNotIn("same", self._tenant_codes())

    def test_derive_uses_configured_base_outside_default_root(self):
        """With COW_TENANT_BASE set to a disjoint directory, deriving succeeds
        and lands under that base, never under the default tenant's root."""
        with patch.dict(os.environ, {"COW_TENANT_BASE": self.base}, clear=False):
            created = self._create("derived")
        row = self.svc.get_tenant(created["id"])
        self.assertTrue(
            os.path.realpath(row["shared_root"]).startswith(
                os.path.realpath(os.path.join(self.base, "tenants", "derived"))),
            row["shared_root"])

    def test_configured_base_is_reusable_across_tenants(self):
        """A base stays usable for every later tenant, not just the first.

        Regression: the base-level pre-check rejected any base that merely
        *contained* an existing tenant root, so the second tenant under the
        same base was refused (503 config_error) even though its derived root
        ``<base>/tenants/<code>`` is a sibling and never overlaps.
        """
        with patch.dict(os.environ, {"COW_TENANT_BASE": self.base}, clear=False):
            first = self._create("first")
            second = self._create("second")
        self.assertEqual(
            os.path.realpath(self.svc.get_tenant(first["id"])["shared_root"]),
            os.path.realpath(os.path.join(self.base, "tenants", "first")))
        self.assertEqual(
            os.path.realpath(self.svc.get_tenant(second["id"])["shared_root"]),
            os.path.realpath(os.path.join(self.base, "tenants", "second")))
        self.assertIn("first", self._tenant_codes())
        self.assertIn("second", self._tenant_codes())

    def test_disjoint_explicit_root_still_accepted(self):
        created = self._create("side", shared_root=self.disjoint)
        self.assertIn("side", self._tenant_codes())
        # The default tenant keeps resolving (its root no longer overlaps anyone).
        row = self.svc.get_tenant(self.default_tid)
        self.assertEqual(os.path.realpath(row["shared_root"]),
                         os.path.realpath(self.eng))


if __name__ == "__main__":
    unittest.main()
