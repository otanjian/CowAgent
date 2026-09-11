"""Database-level tenant consistency for RBAC, grants and quota (7.4-7.7).

The application layer checks these, but a check is advice: the point of these
tests is that a *direct* ``INSERT`` — the shape a future code path, a migration
script or a hand at the sqlite3 prompt would use — cannot create a cross-tenant
row at all. Each case below writes with sqlite3 directly, bypassing
``IdentityService``, and expects the database to refuse.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.service import IdentityService, IdentityServiceError  # noqa: E402
from auth.store import IdentityStore  # noqa: E402


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "identity.db")
        self.store = IdentityStore(self.db_path)
        self.svc = IdentityService(self.db_path)
        self.svc.bootstrap(
            tenant_code="alpha", tenant_name="Alpha",
            admin_username="alpha_admin", admin_display="Alpha Admin",
            admin_password="Passw0rd!alpha", shared_root=os.path.join(self.tmp.name, "a"),
            allow_weak=True,
        )
        tenants = {t["code"]: t for t in self.svc.list_tenants()}
        self.alpha = tenants["alpha"]
        login = self.svc.login("alpha_admin", "Passw0rd!alpha")
        self.alpha_admin = {"user_id": login.user_id, "token": login.token}
        # A second tenant, created through the public path (no admin account:
        # only its roles are needed here).
        self.svc.create_tenant(
            actor_user_id=self.alpha_admin["user_id"], code="beta", name="Beta",
            recent_password="Passw0rd!alpha",
            shared_root=os.path.join(self.tmp.name, "b"),
        )
        self.beta = {t["code"]: t for t in self.svc.list_tenants()}["beta"]

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _roles(self, con, tenant_id):
        return {r["code"]: r["id"] for r in con.execute(
            "SELECT id, code FROM roles WHERE tenant_id=?", (tenant_id,))}

    def _membership(self, con, tenant_id):
        return con.execute(
            "SELECT * FROM memberships WHERE tenant_id=? LIMIT 1", (tenant_id,)).fetchone()

    def _user(self, con, tenant_id):
        membership = self._membership(con, tenant_id)
        return membership["user_id"], membership["id"]


class CrossTenantEdgeTests(_Fixture):
    """7.4: a membership may not carry another tenant's role."""

    def test_a_direct_cross_tenant_role_binding_is_rejected(self):
        con = self._connect()
        try:
            beta_roles = self._roles(con, self.beta["id"])
            _, alpha_membership = self._user(con, self.alpha["id"])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                    " VALUES (?,?,?)",
                    (self.alpha["id"], alpha_membership, beta_roles["member"]),
                )
        finally:
            con.close()

    def test_a_tenant_id_that_disagrees_with_the_membership_is_rejected(self):
        """Even with an otherwise-valid role, the tenant column must agree.

        This is the case a plain two-column foreign key misses: the membership
        and the role may each exist, while the row still asserts a third tenant.
        """
        con = self._connect()
        try:
            alpha_roles = self._roles(con, self.alpha["id"])
            _, alpha_membership = self._user(con, self.alpha["id"])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                    " VALUES (?,?,?)",
                    (self.beta["id"], alpha_membership, alpha_roles["member"]),
                )
        finally:
            con.close()

    def test_the_service_path_still_binds_the_tenant_admin_role(self):
        """The constraint must not have broken the legitimate write."""
        con = self._connect()
        try:
            _, membership_id = self._user(con, self.alpha["id"])
            rows = con.execute(
                "SELECT r.code FROM membership_roles mr JOIN roles r ON r.id = mr.role_id"
                " WHERE mr.membership_id=?", (membership_id,)).fetchall()
            self.assertIn("tenant_admin", {r["code"] for r in rows})
        finally:
            con.close()

    def test_a_migrated_database_keeps_its_legitimate_edges(self):
        """7.4 (migration): the rebuild derives tenant_id, never drops valid rows."""
        con = self._connect()
        try:
            before = con.execute("SELECT COUNT(*) c FROM membership_roles").fetchone()["c"]
            self.assertGreater(before, 0)
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) c FROM membership_roles WHERE tenant_id=''"
                ).fetchone()["c"], 0)
        finally:
            con.close()


class CrossTenantGrantTests(_Fixture):
    """7.4: a grant may not name another tenant's role."""

    def test_a_direct_cross_tenant_grant_is_rejected(self):
        con = self._connect()
        try:
            beta_roles = self._roles(con, self.beta["id"])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO role_resource_grants("
                    "id, tenant_id, role_id, resource_kind, resource_id, action)"
                    " VALUES (?,?,?,?,?,?)",
                    ("grant-x", self.alpha["id"], beta_roles["member"],
                     "tool", "web_search", "use"),
                )
        finally:
            con.close()

    def test_a_tenant_id_that_disagrees_with_the_role_is_rejected(self):
        con = self._connect()
        try:
            alpha_roles = self._roles(con, self.alpha["id"])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO role_resource_grants("
                    "id, tenant_id, role_id, resource_kind, resource_id, action)"
                    " VALUES (?,?,?,?,?,?)",
                    ("grant-y", self.beta["id"], alpha_roles["member"],
                     "tool", "web_search", "use"),
                )
        finally:
            con.close()

    def test_the_grant_helper_stamps_the_roles_own_tenant(self):
        """A grant written through the service carries the role's tenant."""
        con = self._connect()
        try:
            alpha_roles = self._roles(con, self.alpha["id"])
        finally:
            con.close()
        with self.svc._tx() as con:
            self.svc._insert_grants_tx(
                con, alpha_roles["member"],
                [{"resource_kind": "tool", "resource_id": "web_search", "action": "use"}])
            con.commit()
        con = self._connect()
        try:
            row = con.execute(
                "SELECT tenant_id FROM role_resource_grants WHERE role_id=?",
                (alpha_roles["member"],)).fetchone()
            self.assertEqual(row["tenant_id"], self.alpha["id"])
        finally:
            con.close()


class QuotaTenantTests(_Fixture):
    """7.7: a quota row cannot exist for a tenant that does not."""

    def test_a_limit_for_an_unknown_tenant_is_rejected(self):
        con = self._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO quota_limits(tenant_id, user_id, metric, hard_limit)"
                    " VALUES (?,?,?,?)", ("tnt-ghost", "", "tokens", 5))
        finally:
            con.close()

    def test_usage_for_an_unknown_tenant_is_rejected(self):
        con = self._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO quota_usage(tenant_id, user_id, metric, window_start, used)"
                    " VALUES (?,?,?,?,?)", ("tnt-ghost", "", "tokens", 0, 1))
        finally:
            con.close()

    def test_a_real_limit_still_writes_and_reads(self):
        self.svc.set_quota(
            actor_user_id=self.alpha_admin["user_id"], tenant_id=self.alpha["id"],
            metric="tokens", hard_limit=100,
        )
        status = self.svc.quota_status(
            actor_user_id=self.alpha_admin["user_id"], tenant_id=self.alpha["id"])
        self.assertEqual(len(status["limits"]), 1)
        self.assertEqual(status["limits"][0]["hard_limit"], 100)


class ConsumeQuotaFailClosedTests(_Fixture):
    """7.6: a broken meter refuses the consumption instead of allowing it."""

    def setUp(self):
        super().setUp()
        self.svc.set_quota(
            actor_user_id=self.alpha_admin["user_id"], tenant_id=self.alpha["id"],
            metric="tokens", hard_limit=5,
        )
        self.user_id = self.alpha_admin["user_id"]

    def test_a_storage_error_is_raised_and_never_returns_true(self):
        with mock.patch.object(IdentityService, "_tx",
                               side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(IdentityServiceError) as caught:
                self.svc.consume_quota(
                    user_id=self.user_id, tenant_id=self.alpha["id"],
                    metric="tokens", amount=1)
        self.assertEqual(caught.exception.code, "quota_error")
        self.assertEqual(caught.exception.status, 500)

    def test_consumption_is_recorded_while_the_meter_works(self):
        self.assertTrue(self.svc.consume_quota(
            user_id=self.user_id, tenant_id=self.alpha["id"],
            metric="tokens", amount=1))
        status = self.svc.quota_status(
            actor_user_id=self.user_id, tenant_id=self.alpha["id"])
        self.assertEqual(status["usage"][0]["used"], 1)

    def test_the_limit_is_enforced(self):
        for _ in range(5):
            self.assertTrue(self.svc.consume_quota(
                user_id=self.user_id, tenant_id=self.alpha["id"],
                metric="tokens", amount=1))
        self.assertFalse(self.svc.consume_quota(
            user_id=self.user_id, tenant_id=self.alpha["id"],
            metric="tokens", amount=1))

    def test_an_unset_config_keeps_the_gate_closed(self):
        """7.7: absent/malformed config must not silently become fail-open."""
        for value in (None, "", "maybe", 0, "false"):
            with mock.patch("config.conf", return_value={"quota_fail_open": value}):
                self.assertFalse(IdentityService._quota_fail_open())

    def test_an_explicit_relaxation_allows_and_says_so_in_the_log(self):
        """The opt-out still refuses to be silent: the bypass is logged loudly."""
        with mock.patch.object(IdentityService, "_tx",
                               side_effect=sqlite3.OperationalError("disk I/O error")):
            with mock.patch("config.conf", return_value={"quota_fail_open": True}):
                with self.assertLogs("log", level="WARNING") as logs:
                    allowed = self.svc.consume_quota(
                        user_id=self.user_id, tenant_id=self.alpha["id"],
                        metric="tokens", amount=3)
        self.assertTrue(allowed)
        self.assertTrue(any("FAIL-OPEN" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
