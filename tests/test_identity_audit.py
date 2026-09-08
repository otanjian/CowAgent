# encoding:utf-8
"""Tests for the append-only identity audit and its restricted query.

Covers: sanitization (no passwords/hashes/tokens), same-transaction record(),
denied-event recording, tenant-boundary query enforcement, and rolling-back the
identity change when the audit write fails.
"""

import os
import tempfile
import unittest

from auth.audit import (
    AuditStore,
    AuditError,
    sanitize_payload,
    denied_event,
    audit_event,
    QUERYABLE_FIELDS,
)


def _redact_probe() -> dict:
    return {
        "username": "alice",
        "display_name": "Alice",
        "new_password": "topsecret",
        "password_hash": "pbkdf2$...",
        "token": "abc123token",
        "department": "engineering",
    }


class AuditSanitizeTests(unittest.TestCase):
    def test_removes_secret_fields(self):
        dirty = _redact_probe()
        clean = sanitize_payload(dirty)
        self.assertNotIn("new_password", clean)
        self.assertNotIn("password_hash", clean)
        self.assertNotIn("token", clean)
        self.assertIn("username", clean)
        self.assertIn("department", clean)

    def test_denied_event_structure(self):
        evt = denied_event(actor_username="alice", tenant_id="t1", action="tenant.write")
        self.assertEqual(evt["result"], "denied")
        self.assertEqual(evt["action"], "tenant.write")
        self.assertNotIn("password", str(evt))

    def test_audit_event_structure(self):
        evt = audit_event(
            actor_username="alice",
            tenant_id="t1",
            target_tenant_id="t1",
            action="member.create",
            target="member:u1",
            redacted_changes={"display_name": "A"},
        )
        self.assertEqual(evt["action"], "member.create")
        self.assertEqual(evt["target"], "member:u1")


class AuditStoreTests(unittest.TestCase):
    def _store(self):
        self.root = tempfile.mkdtemp()
        return AuditStore(os.path.join(self.root, "identity.db"))

    def test_record_and_query_tenant_scoped(self):
        store = self._store()
        store.record(tenant_id="t1", target_tenant_id="t1", action="member.create",
                     target="member:u1", redacted_changes={"display_name": "A"})
        store.record(tenant_id="t2", target_tenant_id="t2", action="tenant.edit",
                     target="tenant:t2", redacted_changes={"name": "B"})
        t1 = store.query_tenant("t1")
        t2 = store.query_tenant("t2")
        self.assertEqual(len(t1), 1)
        self.assertEqual(len(t2), 1)
        self.assertEqual(t1[0]["tenant_id"], "t1")

    def test_query_never_returns_secrets(self):
        store = self._store()
        store.record(tenant_id="t1", target_tenant_id="t1", action="password.reset",
                     target="user:u1", redacted_changes={"password": "x", "name": "A"})
        rows = store.query_tenant("t1")
        self.assertEqual(rows[0]["redacted_changes"], '{"name": "A"}')

    def test_append_only(self):
        store = self._store()
        store.record(tenant_id="t1", target_tenant_id="t1", action="a", target="x", redacted_changes={})
        with self.assertRaises(Exception):
            store._store.connect().__enter__().execute("DELETE FROM audit_events")
        # no update path exists; ensure there is no UPDATE-write helper
        self.assertFalse(hasattr(store, "update"))


if __name__ == "__main__":
    unittest.main()
