# encoding:utf-8
"""Tests for temp-password/bootstrap expiry and restricted-session TTL (task 2.5).

A real bootstrap (allow_weak=False) MUST record a finite temp-password deadline
so the initial admin is never left forced-password-change with no expiry. An
expired temp credential must not login or extend a restricted session past its
deadline.
"""

import os
import tempfile
import time
import unittest

from auth.password import hash_password
from auth.service import IdentityService, IdentityServiceError


def _svc():
    return IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))


def _bootstrap(svc, allow_weak=False):
    return svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root="/s/acme", allow_weak=allow_weak)


class TempPasswordExpiryTests(unittest.TestCase):
    def test_real_bootstrap_records_temp_expiry(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        user = svc._find_user_by_username("root")
        self.assertEqual(user["must_change_password"], 1)
        # The account must carry a finite, future deadline (never NULL when
        # forced password change is required).
        self.assertIsNotNone(user["temp_password_expires_at"])
        self.assertGreater(user["temp_password_expires_at"], time.time())

    def test_bootstrap_weak_skips_expiry_and_forced_change(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=True)
        user = svc._find_user_by_username("root")
        self.assertEqual(user["must_change_password"], 0)
        self.assertIsNone(user["temp_password_expires_at"])

    def test_login_rejects_expired_temp_password(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        svc._store.execute(
            "UPDATE users SET temp_password_expires_at=? WHERE username='root'",
            (int(time.time()) - 60,),
        )
        with self.assertRaises(IdentityServiceError) as e:
            svc.login("root", "Str0ngAdminPass")
        self.assertEqual(e.exception.code, "invalid_login")

    def test_renewed_temp_password_may_login(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        svc._store.execute(
            "UPDATE users SET temp_password_expires_at=? WHERE username='root'",
            (int(time.time()) + 3600,),
        )
        result = svc.login("root", "Str0ngAdminPass")
        self.assertTrue(result.must_change_password)
        self.assertTrue(result.restricted)

    def test_restricted_session_rejected_after_temp_expiry(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        result = svc.login("root", "Str0ngAdminPass")
        self.assertTrue(result.restricted)
        # Expire the temp credential; the already-issued restricted session must
        # no longer verify (it cannot be used to change the password).
        svc._store.execute(
            "UPDATE users SET temp_password_expires_at=? WHERE username='root'",
            (int(time.time()) - 60,),
        )
        self.assertIsNone(svc.verify_session(result.token))

    def test_restricted_session_ttl_capped_by_temp_expiry(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        from auth.session import session_ttl_seconds
        svc._store.execute(
            "UPDATE users SET temp_password_expires_at=? WHERE username='root'",
            (int(time.time()) + 1800,),  # 30 minutes
        )
        result = svc.login("root", "Str0ngAdminPass")
        session = svc.verify_session(result.token)["session"]
        # Session should not outlive the 30-minute temp deadline.
        self.assertLessEqual(session["expires_at"], int(time.time()) + 1800)
        self.assertLess(session["expires_at"], int(time.time()) + session_ttl_seconds(restricted=True))

    def test_change_password_clears_temp_expiry(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        result = svc.login("root", "Str0ngAdminPass")
        svc.change_password(result.token, "Str0ngAdminPass", "NewStr0ngPass2")
        user = svc._find_user_by_username("root")
        self.assertEqual(user["must_change_password"], 0)
        self.assertIsNone(user["temp_password_expires_at"])

    def test_change_password_rechecks_hash_after_concurrent_reset(self):
        # The old-password proof is re-verified on the same connection that
        # commits the change; if the account was reset in the meantime, the stale
        # proof must be rejected rather than silently applied.
        svc = _svc()
        _bootstrap(svc, allow_weak=True)
        result = svc.login("root", "Str0ngAdminPass")
        # simulate a concurrent reset that changes the hash + bumps version
        svc._store.execute(
            "UPDATE users SET password_hash=?, version=version+1 WHERE username='root'",
            (hash_password("ConcurrentReset1"),),
        )
        with self.assertRaises(IdentityServiceError) as e:
            svc.change_password(result.token, "Str0ngAdminPass", "NewStr0ngPass2")
        self.assertEqual(e.exception.code, "invalid_old")

    def test_bootstrap_real_admin_must_change_password_on_first_login(self):
        svc = _svc()
        _bootstrap(svc, allow_weak=False)
        result = svc.login("root", "Str0ngAdminPass")
        self.assertTrue(result.must_change_password)
        self.assertTrue(result.restricted)


if __name__ == "__main__":
    unittest.main()
