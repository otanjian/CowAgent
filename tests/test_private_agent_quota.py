# encoding:utf-8
"""Atomic quota for member-created private Agents (task 4.2).

The requirement has two halves that pull in opposite directions:

* a create must be **idempotent** — retrying after a failure must not consume a
  second slot or leave a duplicate;
* the quota check must be **atomic** — two requests racing for the last slot must
  not both win.

Those are why the check and the binding live in *one* ``BEGIN IMMEDIATE``
transaction instead of a read followed by an insert. The race test below is a
result test (check-then-act would usually pass it by luck), so a separate
mechanism test pins the lock itself: without it, "the test passed" would not mean
"the guard works".
"""

import os
import tempfile
import threading
import unittest

from auth.service import IdentityService, IdentityServiceError


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "identity.db")
        self.svc = IdentityService(self.db)
        boot = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(self.tmp, "acme"))
        self.tenant_id = boot["id"]
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]["id"]
        self.alice = self._member("alice")
        self.bob = self._member("bob")

    def _member(self, username):
        import json
        from unittest.mock import patch

        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: json.dumps({})})()):
            self.svc.create_member(
                actor_user_id=self.root, tenant_id=self.tenant_id,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.tenant_id)["items"]
                if m["username"] == username][0]["user_id"]

    def _bind(self, agent_id, user_id):
        return self.svc.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id=agent_id, user_id=user_id,
            origin="user_created", actor_user_id=user_id)

    def _count_private(self):
        return len([b for b in self.svc.agents_for_tenant(self.tenant_id)
                    if b.get("private_owner_user_id")])


class PolicyDefaultsTests(_Fixture):
    """No row means "nothing narrowed", matching the channel policy's rule."""

    def test_an_absent_policy_is_permissive_not_denied(self):
        self.assertEqual(
            self.svc.get_private_agent_policy(self.root, self.tenant_id),
            {"tenant_id": self.tenant_id, "personal_enabled": True,
             "member_agent_limit": -1, "tenant_agent_limit": -1})

    def test_creation_works_without_a_policy_row(self):
        self._bind("a-1", self.alice)

        self.assertEqual(self._count_private(), 1)

    def test_a_member_cannot_read_the_policy(self):
        with self.assertRaises(IdentityServiceError) as exc:
            self.svc.get_private_agent_policy(self.alice, self.tenant_id)
        self.assertEqual(exc.exception.code, "forbidden")

    def test_setting_is_audited_without_private_material(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=2)
        rows = [r for r in self.svc.list_audit(self.tenant_id)
                if r["action"] == "tenant.private_agent_policy.set"]
        self.assertEqual(len(rows), 1)
        import json as _json
        changes = rows[0]["redacted_changes"]
        if isinstance(changes, str):
            changes = _json.loads(changes)
        self.assertEqual(changes["member_agent_limit"], 2)
        self.assertNotIn("private_owner_user_id", changes)


class MemberLimitTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=2)

    def test_the_limit_counts_one_members_objects(self):
        self._bind("a-1", self.alice)
        self._bind("a-2", self.alice)

        with self.assertRaises(IdentityServiceError) as exc:
            self._bind("a-3", self.alice)

        self.assertEqual(exc.exception.code, "quota_exceeded")
        self.assertEqual(exc.exception.status, 409)

    def test_the_limit_does_not_apply_to_another_member(self):
        self._bind("a-1", self.alice)
        self._bind("a-2", self.alice)

        self._bind("b-1", self.bob)

        self.assertEqual(self._count_private(), 3)

    def test_a_shared_binding_does_not_consume_a_slot(self):
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="shared-1")

        self._bind("a-1", self.alice)
        self._bind("a-2", self.alice)

        self.assertEqual(self._count_private(), 2)

    def test_raising_the_limit_immediately_allows_more(self):
        self._bind("a-1", self.alice)
        self._bind("a-2", self.alice)
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=3)

        self._bind("a-3", self.alice)

        self.assertEqual(self._count_private(), 3)


class CountingIsBindingBasedTests(_Fixture):
    """The slot is counted from the binding, so no other state can dodge it.

    There is deliberately no "disabled" column on ``agent_bindings``: an object's
    enabled-ness lives in the roster, and the quota is derived from the binding
    rows alone. That is what makes "disable it and create another" impossible —
    the slot is consumed by the *binding*, whatever the roster says about the
    object afterwards. This test pins that by removing the object from the roster
    entirely and showing the slot is still gone.
    """

    def test_a_binding_with_no_roster_entry_still_consumes_the_slot(self):
        """The slot belongs to the *binding*, not to a live roster object.

        A create that leaves a binding behind for an object the roster no longer
        lists — or one that is disabled there — must not hand the member a second
        slot. Counting bindings is what guarantees that; counting "enabled
        objects" would let "disable and recreate" run forever.
        """
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=1)
        self.svc._store.execute(
            "INSERT INTO agent_bindings(agent_id, tenant_id,"
            " private_owner_user_id, origin) VALUES ('ghost', ?, ?, 'user_created')",
            (self.tenant_id, self.alice))

        with self.assertRaises(IdentityServiceError) as exc:
            self._bind("a-2", self.alice)

        self.assertEqual(exc.exception.code, "quota_exceeded")


class PersonalDisabledTests(_Fixture):
    def test_creation_is_refused_when_personal_agents_are_switched_off(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            personal_enabled=False)

        with self.assertRaises(IdentityServiceError) as exc:
            self._bind("a-1", self.alice)

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(self._count_private(), 0)

    def test_shared_bindings_are_unaffected_by_the_personal_switch(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            personal_enabled=False)

        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="shared-1")

        self.assertEqual(
            self.svc.get_agent_binding("shared-1")["private_owner_user_id"], None)


class IdempotentRebindTests(_Fixture):
    def test_rebinding_the_same_agent_does_not_consume_a_second_slot(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=1)
        self._bind("a-1", self.alice)

        self._bind("a-1", self.alice)

        self.assertEqual(self._count_private(), 1)


class TenantLimitTests(_Fixture):
    def test_the_tenant_limit_caps_every_member_together(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            tenant_agent_limit=2)
        self._bind("a-1", self.alice)
        self._bind("b-1", self.bob)

        with self.assertRaises(IdentityServiceError) as exc:
            self._bind("a-2", self.alice)

        self.assertEqual(exc.exception.code, "quota_exceeded")


class ConcurrencyTests(_Fixture):
    """Two requests, one slot left: exactly one may win."""

    def _race(self, bindings):
        barrier = threading.Barrier(len(bindings))
        results = []

        def worker(index):
            barrier.wait()
            try:
                self._bind(bindings[index], self.alice)
                results.append("ok")
            except IdentityServiceError:
                results.append("denied")

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(len(bindings))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_only_one_of_two_racing_requests_gets_the_last_slot(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=1)

        results = self._race(["race-a", "race-b"])

        self.assertEqual(sorted(results), ["denied", "ok"])
        self.assertEqual(self._count_private(), 1,
                         "no over-allocation past the limit")

    def test_the_loser_leaves_no_binding_behind(self):
        self.svc.set_private_agent_policy(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            member_agent_limit=1)

        self._race(["race-a", "race-b"])

        bound = {b["agent_id"] for b in self.svc.agents_for_tenant(self.tenant_id)
                 if b.get("private_owner_user_id")}
        self.assertEqual(len(bound), 1)
        self.assertTrue(bound <= {"race-a", "race-b"})


class LockMechanismTests(_Fixture):
    """Non-vacuity for the race: the count really is taken under a write lock.

    A result test alone cannot tell a correct implementation from a lucky one.
    Here the first connection opens ``BEGIN IMMEDIATE`` and does nothing but read;
    the second must be unable to take the write lock until it commits. If the
    transaction were a deferred ``BEGIN``, the read would hold no lock and the
    second connection would sail through — which is what this test detects.
    """

    def test_begin_immediate_holds_the_write_lock(self):
        """The holder's ``_tx()`` really does hold the SQLite write lock.

        The challenger deliberately uses a short ``busy_timeout`` so the test
        measures the *lock*, not the wall clock: it must be refused almost
        immediately, and it then releases the holder. A deferred ``BEGIN`` would
        take no write lock, so the challenger's insert would succeed — which is
        precisely the mutation this detects.
        """
        import sqlite3

        held = threading.Event()
        released = threading.Event()
        attempt = {}

        def holder():
            with self.svc._tx() as con:
                con.execute(
                    "SELECT COUNT(*) FROM agent_bindings WHERE tenant_id=?",
                    (self.tenant_id,)).fetchone()
                # ``_tx()`` ran ``BEGIN IMMEDIATE`` before yielding, so from here
                # the write lock is ours until the block exits.
                held.set()
                released.wait(20)

        def challenger():
            held.wait(20)
            probe = sqlite3.connect(self.db)
            probe.execute("PRAGMA busy_timeout = 200")
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.execute(
                    "INSERT INTO agent_bindings(agent_id, tenant_id, origin)"
                    " VALUES ('lock-probe', ?, 'unknown')", (self.tenant_id,))
                probe.commit()
                attempt["result"] = "acquired"
            except sqlite3.OperationalError as exc:
                attempt["result"] = "blocked"
                attempt["error"] = str(exc)
            finally:
                probe.close()
                released.set()

        holder_thread = threading.Thread(target=holder)
        challenger_thread = threading.Thread(target=challenger)
        holder_thread.start()
        challenger_thread.start()
        challenger_thread.join(30)
        holder_thread.join(30)

        self.assertEqual(attempt.get("result"), "blocked", attempt)
        self.assertIn("lock", attempt.get("error", "").lower())


if __name__ == "__main__":
    unittest.main()
