# encoding:utf-8
"""Focused tests for platform-account administration (tasks 4.2/4.3).

Covers enable/disable + admin-flag transitions for platform accounts with
optimistic versioning and recent-password re-checks, the last-usable-platform-
admin guard, cross-tenant admin continuity on global disable, session revocation
on disable and password reset, and the one-time temp password returned by reset.
"""

import os
import tempfile
import unittest

from auth.service import IdentityService, IdentityServiceError


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _setup():
    svc = IdentityService(_db_path())
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users_paged()["items"][0]
    return svc, tid, root


class PlatformUserStatusTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def _extra_admin(self, username="admin2", password="Str0ngAdminPass2"):
        # bind an existing global user as a second platform admin into the tenant
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username=username, display_name=username.title(), temporary_password=password,
            roles=["tenant_admin"], department_id=None)
        member = [m for m in self.svc.list_members(self.tid)["items"]
                  if m["username"] == username][0]
        # promote the user to platform admin (authenticated as root)
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=member["user_id"], active=True,
            is_platform_admin=True, expected_version=member["version"],
            recent_password="Str0ngAdminPass")
        # complete forced password change so this counts as a usable platform admin
        res = self.svc.login(username, password)
        self.svc.change_password(res.token, password, "Str0ngAdminFinal")
        users = self.svc.list_platform_users_paged()["items"]
        return [u for u in users if u["username"] == username][0]

    def _login_token(self, username, password):
        res = self.svc.login(username, password)
        return res.token

    def test_disable_requires_correct_recent_password(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=self.root["id"], user_id=self.root["id"], active=False,
                is_platform_admin=True, expected_version=self.root["version"],
                recent_password="WrongPassword")
        self.assertEqual(e.exception.status, 401)

    def test_version_conflict_rejected(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=self.root["id"], user_id=self.root["id"], active=True,
                is_platform_admin=False, expected_version=self.root["version"] + 5,
                recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "conflict")

    def test_non_platform_admin_cannot_act(self):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice", temporary_password="Str0ngPassTmp",
            roles=[], department_id=None)
        alice = [m for m in self.svc.list_members(self.tid)["items"]
                 if m["username"] == "alice"][0]
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=alice["user_id"], user_id=self.root["id"], active=True,
                is_platform_admin=True, expected_version=self.root["version"],
                recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.status, 403)

    def test_cannot_demote_last_completed_platform_admin(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=self.root["id"], user_id=self.root["id"], active=True,
                is_platform_admin=False, expected_version=self.root["version"],
                recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "last_admin")

    def test_promote_and_demote_ok_with_second_admin(self):
        admin2 = self._extra_admin()
        # now demote root; second completed platform admin still exists
        result = self.svc.set_platform_user_status(
            actor_user_id=admin2["id"], user_id=self.root["id"], active=True,
            is_platform_admin=False, expected_version=self.root["version"],
            recent_password="Str0ngAdminFinal")
        self.assertFalse(result["is_platform_admin"])

    def test_disable_revokes_sessions(self):
        # root disables an extra admin, sessions for that user revoked
        admin2 = self._extra_admin()
        token = self._login_token("admin2", "Str0ngAdminFinal")
        self.assertTrue(self.svc.verify_session(token))
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=admin2["id"], active=False,
            is_platform_admin=True, expected_version=admin2["version"],
            recent_password="Str0ngAdminPass")
        self.assertIsNone(self.svc.verify_session(token))

    def test_global_disable_requires_tenant_admin_continuity(self):
        # admin2 is the only tenant_admin alongside root; disabling root would
        # leave acme without an admin, so reject.
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=self.root["id"], user_id=self.root["id"], active=False,
                is_platform_admin=True, expected_version=self.root["version"],
                recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "last_admin")

    def test_reenable_requires_an_active_tenant(self):
        # A disabled account with no active membership in an active tenant must
        # NOT be re-enabled (member→tenant continuity). Sequence: create the
        # member, disable the account first (allowed), then deactivate the
        # now-disabled user's only membership (allowed, since the account is
        # disabled), then attempt to re-enable → rejected (no active tenant).
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="bob", display_name="Bob", temporary_password="Str0ngPassTmp",
            roles=[], department_id=None)
        bob = [m for m in self.svc.list_members(self.tid)["items"]
               if m["username"] == "bob"][0]
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=bob["user_id"], active=False,
            is_platform_admin=False, expected_version=1,
            recent_password="Str0ngAdminPass")
        # deactivate the disabled user's only membership (no admin continuity)
        self.svc.update_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            member_id=bob["id"], display_name="Bob",
            active=False, roles=None, department_id=None, position_text="",
            expected_version=1)
        # re-enabling must be rejected: bob has no active membership
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_platform_user_status(
                actor_user_id=self.root["id"], user_id=bob["user_id"], active=True,
                is_platform_admin=False, expected_version=2,
                recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "last_active_tenant_required")

    def test_reenable_ok_with_an_active_tenant(self):
        # A disabled account that KEEPS an active membership may be re-enabled.
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="carol", display_name="Carol", temporary_password="Str0ngPassTmp",
            roles=[], department_id=None)
        carol = [m for m in self.svc.list_members(self.tid)["items"]
                 if m["username"] == "carol"][0]
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=carol["user_id"], active=False,
            is_platform_admin=False, expected_version=1,
            recent_password="Str0ngAdminPass")
        result = self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=carol["user_id"], active=True,
            is_platform_admin=False, expected_version=2,
            recent_password="Str0ngAdminPass")
        self.assertTrue(result["active"])


class PlatformUserResetTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def _extra_admin(self):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="admin2", display_name="Admin2", temporary_password="Str0ngPassTmp",
            roles=["tenant_admin"], department_id=None)
        member = [m for m in self.svc.list_members(self.tid)["items"]
                  if m["username"] == "admin2"][0]
        self.svc.set_platform_user_status(
            actor_user_id=self.root["id"], user_id=member["user_id"], active=True,
            is_platform_admin=True, expected_version=member["version"],
            recent_password="Str0ngAdminPass")
        # complete forced password change so the account is a usable admin
        res = self.svc.login("admin2", "Str0ngPassTmp")
        self.svc.change_password(res.token, "Str0ngPassTmp", "Str0ngAdminFinal")
        return [u for u in self.svc.list_platform_users_paged()["items"]
                if u["username"] == "admin2"][0]

    def test_reset_returns_one_time_temp_and_forces_change(self):
        admin2 = self._extra_admin()
        result = self.svc.reset_platform_user_password(
            actor_user_id=self.root["id"], user_id=admin2["id"],
            expected_version=admin2["version"], recent_password="Str0ngAdminPass")
        self.assertTrue(result["temporary_password"])
        self.assertTrue(result["must_change_password"])
        # account now restricted and must change password on next login
        u = [x for x in self.svc.list_platform_users_paged()["items"]
             if x["id"] == admin2["id"]][0]
        self.assertTrue(u["must_change_password"])

    def test_reset_revokes_sessions(self):
        admin2 = self._extra_admin()
        token = self.svc.login("admin2", "Str0ngAdminFinal").token
        self.assertTrue(self.svc.verify_session(token))
        self.svc.reset_platform_user_password(
            actor_user_id=self.root["id"], user_id=admin2["id"],
            expected_version=admin2["version"], recent_password="Str0ngAdminPass")
        self.assertIsNone(self.svc.verify_session(token))

    def test_cannot_reset_self(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.reset_platform_user_password(
                actor_user_id=self.root["id"], user_id=self.root["id"],
                expected_version=self.root["version"], recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "self_reset_forbidden")

    def test_reset_version_conflict(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.reset_platform_user_password(
                actor_user_id=self.root["id"], user_id=self.root["id"],
                expected_version=self.root["version"] + 3, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "conflict")

    def test_reset_requires_recent_password(self):
        admin2 = self._extra_admin()
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.reset_platform_user_password(
                actor_user_id=self.root["id"], user_id=admin2["id"],
                expected_version=admin2["version"], recent_password="BadPassword")
        self.assertEqual(e.exception.code, "invalid_old")


class TenantNameTests(unittest.TestCase):
    """Task 4.4: tenant rename service closure (name, version, audit)."""

    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def _tenant_version(self):
        return self.svc.get_tenant(self.tid)["version"]

    def test_rename_succeeds_and_bumps_version(self):
        v = self._tenant_version()
        result = self.svc.set_tenant_name(
            actor_user_id=self.root["id"], tenant_id=self.tid, name="New Acme",
            expected_version=v, recent_password="Str0ngAdminPass")
        self.assertEqual(result["name"], "New Acme")
        self.assertEqual(self.svc.get_tenant(self.tid)["name"], "New Acme")
        self.assertGreater(self.svc.get_tenant(self.tid)["version"], v)

    def test_rename_version_conflict(self):
        v = self._tenant_version()
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_name(
                actor_user_id=self.root["id"], tenant_id=self.tid, name="New",
                expected_version=v + 7, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "conflict")

    def test_rename_requires_recent_password(self):
        v = self._tenant_version()
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_name(
                actor_user_id=self.root["id"], tenant_id=self.tid, name="New",
                expected_version=v, recent_password="Wrong")
        self.assertEqual(e.exception.code, "invalid_old")

    def test_rename_empty_rejected(self):
        v = self._tenant_version()
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.set_tenant_name(
                actor_user_id=self.root["id"], tenant_id=self.tid, name="   ",
                expected_version=v, recent_password="Str0ngAdminPass")
        self.assertEqual(e.exception.code, "bad_request")


class MemberFilterTests(unittest.TestCase):
    """Task 4.5: member status/role filters + dedup total + role_codes."""

    def setUp(self):
        self.svc, self.tid, self.root = _setup()

    def _make_member(self, username, roles=(), active=True, tid=None):
        tid = tid or self.tid
        result = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=tid, operation="create-new",
            username=username, display_name=username.title(),
            temporary_password="Str0ngPassTmp", roles=list(roles), department_id=None)
        if not active:
            # A user stays enabled, so deactivating this membership must not be
            # their only effective tenant. Give them another active membership in
            # a second tenant first, then deactivate the target membership.
            self._second_tenant_for(username, result["user_id"])
            self.svc.update_member(
                actor_user_id=self.root["id"], tenant_id=tid,
                member_id=result["membership_id"], display_name=username.title(),
                active=False, roles=None, department_id=None, position_text="",
                expected_version=1)
        return result

    def _second_tenant_for(self, username, user_id):
        import uuid
        code = "t" + uuid.uuid4().hex[:8]
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code=code, name=code.title(),
            shared_root="/s/" + code, admin_username="r_" + code[:6],
            admin_display="Root " + code[:6], admin_password="Str0ngAdminPass",
            recent_password="Str0ngAdminPass")
        tid2 = self.svc.list_tenants(q=code)[0]["id"]
        # root is a platform admin; bind the user as a member in the new tenant.
        self.svc.set_tenant_admin(
            actor_user_id=self.root["id"], tenant_id=tid2, user_id=user_id,
            display_name=username.title(), recent_password="Str0ngAdminPass")
        return tid2

    def test_status_active_and_inactive_filters(self):
        self._make_member("alice", roles=["member"], active=True)
        self._make_member("bob", roles=["member"], active=False)
        active = self.svc.list_members(self.tid, status="active")
        inactive = self.svc.list_members(self.tid, status="inactive")
        usernames_active = {m["username"] for m in active["items"]}
        usernames_inactive = {m["username"] for m in inactive["items"]}
        self.assertIn("alice", usernames_active)
        self.assertNotIn("bob", usernames_active)
        self.assertIn("bob", usernames_inactive)

    def test_role_filter(self):
        self._make_member("alice", roles=["member"])
        self._make_member("carol", roles=["tenant_admin"])
        admin = self.svc.list_members(self.tid, role="tenant_admin")
        usernames = {m["username"] for m in admin["items"]}
        self.assertIn("carol", usernames)
        self.assertNotIn("alice", usernames)
        member = self.svc.list_members(self.tid, role="member")
        self.assertEqual({m["username"] for m in member["items"]}, {"alice"})

    def test_invalid_status_rejected(self):
        with self.assertRaises(IdentityServiceError) as e:
            self.svc.list_members(self.tid, status="banana")
        self.assertEqual(e.exception.code, "bad_request")

    def test_role_codes_returned(self):
        self._make_member("alice", roles=["member"])
        items = self.svc.list_members(self.tid)["items"]
        alice = [m for m in items if m["username"] == "alice"][0]
        self.assertIn("member", alice["role_codes"])

    def test_dedup_total_for_role_filter(self):
        self._make_member("alice", roles=["member"])
        self._make_member("dave", roles=["member", "tenant_admin"])
        # a role filter must not double-count a member holding multiple roles.
        # root is the bootstrap tenant_admin, so no extra tenant_admin members exist.
        member = self.svc.list_members(self.tid, role="member")
        admin = self.svc.list_members(self.tid, role="tenant_admin")
        self.assertEqual(member["total"], 2)
        # only root is tenant_admin (dave has it but is also member; root is alone
        # among tenant_admin-only) -> tenant_admin total is 2 (root + dave)
        self.assertEqual(admin["total"], 2)


if __name__ == "__main__":
    unittest.main()
