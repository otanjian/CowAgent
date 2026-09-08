# encoding:utf-8
"""Tests for per-request identity context resolution (X-Tenant-ID flow).

Covers: valid token + selected tenant resolves to an authorized context, missing
or conflicting tenant selection is an error, non-member/stale-role is rejected,
platform endpoints need no tenant selection, and revalidation picks up a role
change.
"""

import os
import tempfile
import unittest

from auth.runtime import resolve_context, revalidate_context, IdentityContextError
from auth.service import IdentityService


def _svc():
    svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme",
        allow_weak=True)
    return svc


class RuntimeContextTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.token = self.svc.login("root", "Str0ngAdminPass").token
        self.tenant_id = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]

    def test_valid_token_and_tenant(self):
        ctx = resolve_context(self.svc, self.token, self.tenant_id)
        self.assertEqual(ctx.tenant_id, self.tenant_id)
        self.assertTrue(ctx.is_tenant_admin)
        self.assertIn("history.read", ctx.permissions)

    def test_no_tenant_on_platform_endpoint(self):
        ctx = resolve_context(self.svc, self.token, None)
        self.assertFalse(ctx.has_tenant)
        self.assertTrue(ctx.is_platform_admin)

    def test_missing_token(self):
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, "badtoken", self.tenant_id)
        self.assertEqual(e.exception.status, 401)

    def test_conflicting_tenant_sources(self):
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, self.token, self.tenant_id,
                            header_tenant_sources=[self.tenant_id, "other"])
        self.assertEqual(e.exception.code, "conflicting_tenant")

    def test_non_member_tenant_rejected(self):
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root="/s/beta", admin_username="betaadmin",
            admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        # root is NOT a member of beta -> 403
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, self.token, other["id"])
        self.assertEqual(e.exception.status, 403)

    def test_revalidate_picks_up_role_change(self):
        ctx = resolve_context(self.svc, self.token, self.tenant_id)
        # remove the admin role from the membership, revalidate
        membership = self.svc.get_membership(self.root["id"], self.tenant_id)
        with self.svc._tx() as con:
            con.execute("DELETE FROM membership_roles WHERE membership_id=?", (membership["id"],))
            con.commit()
        ctx2 = revalidate_context(self.svc, ctx)
        self.assertFalse(ctx2.is_tenant_admin)


if __name__ == "__main__":
    unittest.main()
