# encoding:utf-8
"""Tests for the fixed permission catalog, built-in roles and authorization.

Covers the fixed read-only directory, forbidden/unknown/qualified permissions,
the member default grant, the built-in role protections, and the deterministic
authorization decision against a resolved identity + membership context.
"""

import unittest

from auth.policy import (
    PERMISSION_CATALOG,
    BUILTIN_ROLES,
    MEMBER_DEFAULT_PERMISSIONS,
    TENANT_ADMIN_CODE,
    MEMBER_CODE,
    PermissionError,
    normalize_permissions,
    default_permissions_for,
    is_admin_role,
    permissions_for_roles,
)


class PermissionCatalogTests(unittest.TestCase):
    def test_catalog_has_seven_read_permissions(self):
        self.assertGreaterEqual(len(PERMISSION_CATALOG), 7)
        for p in (
            "tenant.info.read",
            "tenant.members.read",
            "tenant.org.read",
            "agent.read",
            "history.read",
            "knowledge.read",
            "memory.read",
        ):
            self.assertIn(p, PERMISSION_CATALOG)

    def test_knowledge_write_is_not_a_catalog_item(self):
        from auth.policy import PERMISSION_METADATA

        # Writes are authorized by data-root + Agent ownership, so the id must
        # not exist as an assignable catalogue entry any more.
        self.assertNotIn("knowledge.write", PERMISSION_CATALOG)
        self.assertNotIn("knowledge.write", PERMISSION_METADATA)
        with self.assertRaises(PermissionError):
            normalize_permissions(["knowledge.write"])

    def test_unknown_and_admin_permissions_rejected(self):
        for bad in ("tenant.admin", "platform.admin", "tenant.info.write", "*", "history.read.*"):
            with self.assertRaises(PermissionError):
                normalize_permissions([bad])

    def test_builtin_roles_defined(self):
        self.assertEqual(set(BUILTIN_ROLES), {TENANT_ADMIN_CODE, MEMBER_CODE})
        self.assertEqual(is_admin_role(TENANT_ADMIN_CODE), True)
        self.assertEqual(is_admin_role(MEMBER_CODE), False)
        self.assertEqual(is_admin_role("custom"), False)

    def test_member_default_permissions(self):
        perms = default_permissions_for(MEMBER_CODE)
        self.assertIn("tenant.info.read", perms)
        for p in ("agent.read", "history.read", "knowledge.read", "memory.read"):
            self.assertIn(p, perms)
        # member does NOT get org/members read by default nor any write
        self.assertNotIn("tenant.members.read", perms)
        self.assertNotIn("tenant.org.read", perms)

    def test_permissions_for_roles_merges_union(self):
        # admin implicitly has everything; a custom role unions its catalog set
        perms = permissions_for_roles([TENANT_ADMIN_CODE], {})
        self.assertIn("tenant.members.read", perms)
        self.assertIn("history.read", perms)
        # union across two custom roles
        merged = permissions_for_roles(
            ["r1"], {"r1": ["agent.read", "history.read"]}
        )
        self.assertEqual(merged, {"agent.read", "history.read"})

    def test_normalize_dedupes_and_validates(self):
        perms = normalize_permissions(["agent.read", "agent.read", "history.read"])
        self.assertEqual(perms, {"agent.read", "history.read"})


if __name__ == "__main__":
    unittest.main()
