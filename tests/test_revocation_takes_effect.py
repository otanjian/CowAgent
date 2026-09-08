# encoding:utf-8
"""Acceptance tests for revocation taking effect immediately (task 4.3).

Verifies that a role / membership / tenant / global-account revocation is picked
up by the NEXT request (no cached permission snapshot), that ordinary members
cannot perform identity management, and that an audit write failure rolls back
the identity change (same-index transaction).
"""

import os
import tempfile
import unittest
from unittest import mock

from auth.runtime import resolve_context, revalidate_context, IdentityContextError
from auth.service import IdentityService, IdentityServiceError


def _svc():
    return IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))


def _bootstrap(svc):
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tenant = svc.list_tenants()[0]
    return root, tenant


def _create_member(svc, root, tid, username, roles=None):
    return svc.create_member(
        actor_user_id=root["id"], tenant_id=tid["id"], operation="create-new",
        username=username, display_name=username, temporary_password="Str0ngPassTmp",
        roles=roles or ["member"])


def _second_tenant(svc, root, *usernames):
    """Create a second active tenant and bind root + usernames into it.

    Needed only to satisfy the member→tenant continuity rule (an enabled user
    must keep at least one active membership in an active tenant). Returning the
    beta tenant lets tests deactivate/revoke membership in the original tenant
    without the write itself being rejected. Root (the platform admin) is always
    bound too, so deactivating the original tenant doesn't strip root's last
    active tenant. Uses ``set_tenant_admin`` (a platform-admin operation) since
    root is a platform admin and the target tenant has no other admin yet.
    """
    svc.create_tenant(
        actor_user_id=root["id"], code="beta", name="Beta",
        shared_root="/s/beta", admin_username="broot", admin_display="Broot",
        admin_password="Str0ngAdminBeta", recent_password="Str0ngAdminPass")
    beta = [t for t in svc.list_tenants() if t["code"] == "beta"][0]
    for username in ("root",) + tuple(usernames):
        user = [u for u in svc.list_platform_users() if u["username"] == username][0]
        svc.set_tenant_admin(
            actor_user_id=root["id"], tenant_id=beta["id"], user_id=user["id"],
            display_name=username, recent_password="Str0ngAdminPass")
    return beta


class ImmediateRevocationTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.root, self.tid = _bootstrap(self.svc)
        self.token = self.svc.login("root", "Str0ngAdminPass").token

    def test_membership_revocation_rejects_next_request(self):
        member = _create_member(self.svc, self.root, self.tid, "alice")
        alice_token = self.svc.login("alice", "Str0ngPassTmp").token
        # alice can access the tenant
        ctx = resolve_context(self.svc, alice_token, self.tid["id"])
        self.assertEqual(ctx.tenant_id, self.tid["id"])
        # keep alice an active member of a second tenant so the membership
        # revocation below is legal under the member→tenant continuity rule.
        _second_tenant(self.svc, self.root, "alice")
        # revoke the membership (deactivate the member)
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            member_id=member["membership_id"], display_name="alice", active=False,
            roles=["member"], department_id=None, position_text="",
            expected_version=1)
        # the SAME token now fails on the next request
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, alice_token, self.tid["id"])
        self.assertEqual(e.exception.status, 403)

    def test_role_revocation_revalidates_immediately(self):
        # make a second admin, then remove its admin role
        _create_member(self.svc, self.root, self.tid, "bob", roles=["tenant_admin"])
        ctx = resolve_context(self.svc, _member_token(self.svc, "bob"), self.tid["id"])
        self.assertTrue(ctx.is_tenant_admin)
        # demote bob to member via service
        members = self.svc.list_members(self.tid["id"])["items"]
        bob_row = [m for m in members if m["username"] == "bob"][0]
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"],
            member_id=bob_row["id"], display_name="bob", active=True,
            roles=["member"], department_id=None, position_text="",
            expected_version=bob_row["version"])
        ctx2 = revalidate_context(self.svc, ctx)
        self.assertFalse(ctx2.is_tenant_admin)

    def test_tenant_deactivation_rejects_next_request(self):
        member = _create_member(self.svc, self.root, self.tid, "carol")
        carol_token = self.svc.login("carol", "Str0ngPassTmp").token
        resolve_context(self.svc, carol_token, self.tid["id"])  # ok before
        # keep carol an active member of a second tenant so deactivating acme is
        # legal under the member→tenant continuity rule (carol keeps a tenant).
        _second_tenant(self.svc, self.root, "carol")
        # deactivate the tenant
        self.svc.set_tenant_status(
            actor_user_id=self.root["id"], tenant_id=self.tid["id"], active=False,
            expected_version=1, recent_password="Str0ngAdminPass")
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, carol_token, self.tid["id"])
        self.assertEqual(e.exception.status, 403)

    def test_global_account_disable_rejects_next_request(self):
        member = _create_member(self.svc, self.root, self.tid, "dave")
        dave_token = self.svc.login("dave", "Str0ngPassTmp").token
        resolve_context(self.svc, dave_token, self.tid["id"])  # ok before
        # disable the global account (what a platform admin reset does)
        with self.svc._tx() as con:
            con.execute("UPDATE users SET active=0 WHERE username='dave'")
            con.commit()
        with self.assertRaises(IdentityContextError) as e:
            resolve_context(self.svc, dave_token, self.tid["id"])
        # disabling a global account revokes its session token -> 401
        self.assertEqual(e.exception.status, 401)


class AuditRollbackTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.root, self.tid = _bootstrap(self.svc)

    def test_audit_write_failure_rolls_back_creation(self):
        with mock.patch.object(self.svc._audit, "record", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.svc.create_member(
                    actor_user_id=self.root["id"], tenant_id=self.tid["id"],
                    operation="create-new", username="erin", display_name="Erin",
                    temporary_password="Str0ngPassTmp", roles=["member"])
        # the user must NOT have been persisted (transaction rolled back)
        self.assertIsNone(self.svc._find_user_by_username("erin"))
        members = self.svc.list_members(self.tid["id"])["items"]
        self.assertNotIn("erin", [m["username"] for m in members])


class MemberCannotManageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.svc = _svc()
        self.root, self.tid = _bootstrap(self.svc)

    def test_member_cannot_write_member(self):
        member = _create_member(self.svc, self.root, self.tid, "frank")
        # frank is a plain member, trying to create another member -> forbidden
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.create_member(
                actor_user_id=member["user_id"], tenant_id=self.tid["id"],
                operation="create-new", username="grace", display_name="Grace",
                temporary_password="Str0ngPassTmp", roles=["member"])
        self.assertEqual(e.exception.status, 403)

    def test_member_cannot_access_platform_tenants(self):
        member = _create_member(self.svc, self.root, self.tid, "heidi")
        # member is not a platform admin
        self.assertFalse(self.svc.is_platform_admin(member["user_id"]))
        with self.assertRaises(IdentityServiceError):
            self.svc.create_tenant(
                actor_user_id=member["user_id"], code="beta", name="Beta",
                shared_root="/s/beta", admin_username="beta", admin_display="Beta",
                admin_password="Str0ngPass2", recent_password="x")


def _member_token(svc, username):
    return svc.login(username, "Str0ngPassTmp").token


if __name__ == "__main__":
    unittest.main()
