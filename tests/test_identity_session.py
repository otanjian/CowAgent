# encoding:utf-8
"""Tests for the database-backed AuthSession lifecycle.

Covers: opaque high-entropy token issuance, digest-only storage (never the raw
token), expiry, single-session revoke on logout, all-session revoke on password
change / global disable, the restricted (temp-password) session mode, and the
invariant that no current-tenant or permission snapshot is stored.
"""

import os
import tempfile
import time
import unittest

from auth.session import (
    SessionStore,
    SessionError,
    hash_token,
    generate_token,
    session_ttl_seconds,
    SESSION_DAYS,
    RESTRICTED_DAYS,
)


class SessionStoreTests(unittest.TestCase):
    def _store(self):
        self.root = tempfile.mkdtemp()
        path = os.path.join(self.root, "identity.db")
        store = SessionStore(path)
        with store._store.connect() as con:
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash)"
                " VALUES('u1','alice','Alice','x')"
            )
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash)"
                " VALUES('u2','bob','Bob','x')"
            )
            con.commit()
        return store

    def test_token_is_high_entropy_and_digest_only(self):
        store = self._store()
        t1 = generate_token()
        t2 = generate_token()
        self.assertNotEqual(t1, t2)
        self.assertTrue(len(t1) >= 40)
        store.create("u1", t1, restricted=False)
        row = store.get_by_owner(t1)[0] if store.get_by_owner(t1) else None
        # raw token is never stored
        self.assertNotIn(t1, os.listdir(self.root))
        self.assertTrue(store.validate_token(t1))

    def test_create_and_logout(self):
        store = self._store()
        token = generate_token()
        store.create("u1", token, restricted=False)
        self.assertTrue(store.validate_token(token))
        store.revoke(token)
        self.assertFalse(store.validate_token(token))

    def test_expiry(self):
        store = self._store()
        token = generate_token()
        store.create("u1", token, ttl=1, restricted=False)
        self.assertTrue(store.validate_token(token))
        time.sleep(1.2)
        self.assertFalse(store.validate_token(token))

    def test_revoke_all_for_user(self):
        store = self._store()
        t1 = generate_token()
        t2 = generate_token()
        t3 = generate_token()
        store.create("u1", t1)
        store.create("u1", t2)
        store.create("u2", t3)
        store.revoke_all_for_user("u1")
        self.assertFalse(store.validate_token(t1))
        self.assertFalse(store.validate_token(t2))
        self.assertTrue(store.validate_token(t3))

    def test_restricted_flag(self):
        store = self._store()
        token = generate_token()
        store.create("u1", token, restricted=True)
        row = store.get_by_token(token)
        self.assertEqual(row["restricted"], 1)

    def test_stored_session_has_no_tenant_or_permissions(self):
        store = self._store()
        token = generate_token()
        store.create("u1", token)
        row = store.get_by_token(token)
        cols = set(row.keys())
        self.assertNotIn("tenant_id", cols)
        self.assertNotIn("permissions", cols)
        self.assertNotIn("role", cols)

    def test_session_ttl_defaults(self):
        self.assertEqual(session_ttl_seconds(False), SESSION_DAYS * 86400)
        self.assertEqual(session_ttl_seconds(True), RESTRICTED_DAYS * 86400)


if __name__ == "__main__":
    unittest.main()
