# encoding:utf-8
"""Self-service proof of control and the personal channel link it produces.

Change ``enable-member-personal-console`` lets a member attach a channel
instance to *themselves* rather than to the tenant. Two records make that safe:

``binding_challenges`` holds a one-time, short-lived proof that the person who
starts the flow in the console is the same person who then speaks to the bot in
a private chat. The server fixes the tenant, user, instance and purpose up
front, so a challenge cannot be replayed against a different target.

``personal_channel_links`` records the resulting "this instance routes to this
member" fact. It is deliberately a *separate* table from the global
``external_identities`` mapping: unlink must drop only this tenant's personal
route, because the same provider identity may still be in use by the same person
in another tenant, or by the tenant's own public binding flow.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError
from auth.store import IdentityStore, migration_versions
from tests._helpers import install_personal_target_roster, personal_channel_target

CHALLENGES = "binding_challenges"
LINKS = "personal_channel_links"


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed_tenant(con, tenant_id="t1", code="acme"):
    con.execute(
        "INSERT INTO tenants(id, code, name, shared_root) VALUES(?,?,?,?)",
        (tenant_id, code, code.title(), "/s/" + code),
    )


def _seed_prereqs(con):
    """Everything the two new tables reference, so FKs can be exercised."""
    _seed_tenant(con)
    con.execute(
        "INSERT INTO users(id, username, display_name, password_hash)"
        " VALUES('u1','alice','Alice','x')")
    con.execute(
        "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
        " display_name, agent_id, scope, owner_user_id, created_by)"
        " VALUES('ci1','t1','feishu','Alice Bot','','user','u1','u1')")
    con.execute(
        "INSERT INTO external_identities(id, user_id, provider, issuer, subject)"
        " VALUES('ext1','u1','feishu','fs','ou_alice')")


def _insert_challenge(con, **over):
    row = {
        "id": "ch1",
        "tenant_id": "t1",
        "user_id": "u1",
        "instance_id": "ci1",
        "purpose": "channel_link",
        "code_hash": "hash-1",
        "expires_at": 4102444800,
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(
        "INSERT INTO %s(%s) VALUES(%s)" % (CHALLENGES, cols, marks),
        tuple(row.values()),
    )


def _insert_link(con, **over):
    row = {
        "tenant_id": "t1",
        "user_id": "u1",
        "instance_id": "ci1",
        "external_identity_id": "ext1",
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(
        "INSERT INTO %s(%s) VALUES(%s)" % (LINKS, cols, marks),
        tuple(row.values()),
    )


class _SchemaBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")
        self.store = IdentityStore(self.path)

    def _columns(self, table):
        with self.store.connect() as con:
            return {
                r["name"]: {"notnull": r["notnull"], "default": r["dflt_value"]}
                for r in con.execute("PRAGMA table_info(%s)" % table)
            }


class BindingChallengeSchemaTests(_SchemaBase):
    """A challenge is one row with a server-fixed scope and a hard expiry."""

    def test_the_migration_is_registered(self):
        with self.store.connect() as con:
            applied = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(applied, migration_versions())

    def test_columns_are_the_expected_set(self):
        self.assertEqual(
            set(self._columns(CHALLENGES)),
            {"id", "tenant_id", "user_id", "instance_id", "purpose",
             "code_hash", "attempts", "expires_at", "consumed_at", "created_at",
             "target_agent_id"},
        )

    def test_scope_and_code_columns_are_not_null(self):
        """``id`` is included because SQLite does not imply NOT NULL for a TEXT
        primary key — a bare ``TEXT PRIMARY KEY`` accepts NULL."""
        cols = self._columns(CHALLENGES)
        for name in ("id", "tenant_id", "user_id", "instance_id", "purpose",
                     "code_hash", "attempts", "expires_at"):
            self.assertEqual(cols[name]["notnull"], 1, "%s must be NOT NULL" % name)

    def test_attempts_start_at_zero_and_consumed_at_is_null(self):
        cols = self._columns(CHALLENGES)
        self.assertEqual(cols["attempts"]["default"], "0")
        self.assertEqual(cols["attempts"]["notnull"], 1)
        self.assertIsNone(cols["consumed_at"]["default"])
        self.assertEqual(cols["consumed_at"]["notnull"], 0)

    def test_a_challenge_starts_attempted_zero_and_unconsumed(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_challenge(con)
            row = con.execute(
                "SELECT attempts, consumed_at, created_at FROM %s"
                " WHERE id='ch1'" % CHALLENGES).fetchone()
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["consumed_at"])
        self.assertIsNotNone(row["created_at"])

    def test_the_same_code_hash_can_back_two_challenges(self):
        """A hash is not a key: uniqueness belongs to the challenge id."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_challenge(con)
            _insert_challenge(con, id="ch2")
            n = con.execute("SELECT COUNT(*) FROM %s" % CHALLENGES).fetchone()[0]
        self.assertEqual(n, 2)

    def test_an_unknown_tenant_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_challenge(con, tenant_id="nope")

    def test_an_unknown_instance_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_challenge(con, instance_id="nope")


class PersonalChannelLinkSchemaTests(_SchemaBase):
    """One row per (tenant, user, instance): the personal route's identity."""

    def test_columns_are_the_expected_set(self):
        self.assertEqual(
            set(self._columns(LINKS)),
            {"tenant_id", "user_id", "instance_id", "external_identity_id",
             "active", "created_at", "target_agent_id"},
        )

    def test_link_columns_are_not_null(self):
        cols = self._columns(LINKS)
        for name in ("tenant_id", "user_id", "instance_id",
                     "external_identity_id", "active"):
            self.assertEqual(cols[name]["notnull"], 1, "%s must be NOT NULL" % name)

    def test_a_link_is_active_by_default(self):
        cols = self._columns(LINKS)
        self.assertEqual(cols["active"]["default"], "1")

    def test_one_route_per_user_and_instance(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_link(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_link(con)

    def test_the_same_identity_may_route_in_two_tenants(self):
        """The global mapping is reused, so one person's identity can back a
        personal route in more than one tenant at once."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            _seed_tenant(con, "t2", "globex")
            con.execute(
                "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
                " display_name, agent_id, scope, owner_user_id, created_by)"
                " VALUES('ci2','t2','feishu','Alice Bot','','user','u1','u1')")
            _insert_link(con)
            _insert_link(con, tenant_id="t2", instance_id="ci2")
            n = con.execute("SELECT COUNT(*) FROM %s" % LINKS).fetchone()[0]
        self.assertEqual(n, 2)

    def test_deleting_the_link_row_is_independent_of_the_global_mapping(self):
        """The red line: removing the route must leave ``external_identities``
        untouched, because other tenants (and the public bind flow) may use it."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_link(con)
            con.execute("DELETE FROM %s" % LINKS)
            left = con.execute(
                "SELECT COUNT(*) FROM external_identities").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM %s" % LINKS).fetchone()[0]
        self.assertEqual(left, 1, "the global identity mapping must survive")
        self.assertEqual(n, 0)

    def test_an_unknown_external_identity_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_link(con, external_identity_id="nope")


class PersonalBindingUpgradeTests(unittest.TestCase):
    """Upgrading an existing store adds the tables and changes nothing else."""

    def _pre_migration_store(self):
        from auth.store import _migrations
        path = _db_path()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(17):
            _migrations[i](con)
        _seed_prereqs(con)
        con.execute(
            "INSERT INTO schema_migrations(version) VALUES %s"
            % ",".join("(%d)" % (i + 1) for i in range(17)))
        con.commit()
        con.close()
        return path

    def test_the_upgrade_preserves_identities_and_adds_empty_tables(self):
        path = self._pre_migration_store()
        store = IdentityStore(path)
        with store.connect() as con:
            identities = [dict(r) for r in con.execute(
                "SELECT id, user_id, provider, subject FROM external_identities")]
            challenges = con.execute(
                "SELECT COUNT(*) FROM %s" % CHALLENGES).fetchone()[0]
            links = con.execute("SELECT COUNT(*) FROM %s" % LINKS).fetchone()[0]
        self.assertEqual(identities, [{
            "id": "ext1", "user_id": "u1", "provider": "feishu",
            "subject": "ou_alice"}])
        self.assertEqual(challenges, 0, "the upgrade must not invent challenges")
        self.assertEqual(links, 0, "the upgrade must not invent routes")


class _LinkFixture(unittest.TestCase):
    """Acme with a platform admin, one member and a personal Feishu instance."""

    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    FEISHU_BUNDLE = {
        "feishu_app_id": "cli_alice",
        "feishu_app_secret": "s3cr3t-app-secret",
        "feishu_bot_name": "Alice Bot",
    }
    #: The registry the personal-target predicate consults. ``agent-a``/``agent-b``
    #: stay the two tenants' own Agents; the ``target-*`` ids are the members'
    #: private ones, handed out one per (tenant, member) by :meth:`_target_for`.
    ROSTER_AGENTS = ("agent-a", "agent-b", "target-0", "target-1", "target-2")

    def setUp(self):
        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_key)
        install_personal_target_roster(self, *self.ROSTER_AGENTS)

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.tenant = self.svc.list_tenants()[0]["id"]

        self.svc.bind_agent(tenant_id=self.tenant, agent_id="agent-a")
        # A second tenant exists so "unlinking here must not touch there" can be
        # proved against a genuinely different tenant, not just a sibling route.
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        self.tenant_b = [t for t in self.svc.list_tenants()
                         if t["code"] == "globex"][0]["id"]
        self.svc.bind_agent(tenant_id=self.tenant_b, agent_id="agent-b")
        self.member_id = self._add_member("alice", "Alice")
        self._targets = {}
        self.instance = self._personal_instance(self.member_id)

    def _restore_key(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    def _add_member(self, username, display):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                operation="create-new", username=username,
                display_name=display, temporary_password="MemTempPass1",
                roles=["member"])
        return [m for m in self.svc.list_members(self.tenant)["items"]
                if m["username"] == username][0]["user_id"]

    def _target_for(self, tenant_id, user_id):
        """The private Agent *user_id* owns in *tenant_id*, bound on first use.

        A personal instance may only route to a target its own owner holds
        privately, and the registry has to see it enabled too, so the fixture
        cannot simply share one Agent: each (tenant, member) pair gets one of the
        ``target-*`` ids the roster above declares.
        """
        key = (tenant_id, user_id)
        if key not in self._targets:
            agent_id = "target-%d" % len(self._targets)
            personal_channel_target(self.svc, tenant_id=tenant_id,
                                    user_id=user_id, agent_id=agent_id)
            self._targets[key] = agent_id
        return self._targets[key]

    def _personal_instance(self, owner_user_id, display_name="Alice Bot",
                           app_id="cli_alice", tenant_id=None,
                           agent_id=None):
        tenant_id = tenant_id or self.tenant
        # The default target is the owner's own private Agent in this tenant,
        # which is what the personal create is required to verify.
        agent_id = agent_id or self._target_for(tenant_id, owner_user_id)
        return self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=tenant_id,
            channel_type="feishu", display_name=display_name, agent_id=agent_id,
            credentials=dict(self.FEISHU_BUNDLE, feishu_app_id=app_id),
            recent_password="Str0ngRootFinal",
            scope="user", owner_user_id=owner_user_id)

    def _mint(self, **over):
        args = dict(tenant_id=self.tenant, user_id=self.member_id,
                    instance_id=self.instance["id"])
        args.update(over)
        return self.svc.create_binding_challenge(**args)


class BindingChallengeTests(_LinkFixture):
    """A challenge is single-use, bounded and scoped to what the server fixed."""

    def test_a_challenge_returns_a_code_once_and_stores_only_its_hash(self):
        challenge = self._mint()
        self.assertTrue(challenge["code"])
        self.assertTrue(challenge["challenge_id"])
        rows = self.svc._store.execute(
            "SELECT code_hash FROM binding_challenges WHERE id=?",
            (challenge["challenge_id"],))
        stored = rows[0]["code_hash"]
        self.assertNotIn(challenge["code"], stored,
                         "the plaintext code must never be stored")

    def test_a_challenge_cannot_be_redeemed_twice(self):
        challenge = self._mint()
        self.svc.consume_binding_challenge(
            challenge_id=challenge["challenge_id"], code=challenge["code"])

        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.consume_binding_challenge(
                challenge_id=challenge["challenge_id"], code=challenge["code"])
        self.assertEqual(caught.exception.code, "conflict")

    def test_a_wrong_code_does_not_consume_the_challenge(self):
        challenge = self._mint()
        with self.assertRaises(IdentityServiceError):
            self.svc.consume_binding_challenge(
                challenge_id=challenge["challenge_id"], code="00000000")

        # The real code still works: a typo must not burn the challenge.
        self.svc.consume_binding_challenge(
            challenge_id=challenge["challenge_id"], code=challenge["code"])

    def test_guessing_is_bounded_by_an_attempt_limit(self):
        challenge = self._mint()
        for _ in range(5):
            with self.assertRaises(IdentityServiceError):
                self.svc.consume_binding_challenge(
                    challenge_id=challenge["challenge_id"], code="00000000")

        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.consume_binding_challenge(
                challenge_id=challenge["challenge_id"], code=challenge["code"])
        self.assertIn(caught.exception.code, ("forbidden", "too_many_requests"))

    def test_an_expired_challenge_is_refused(self):
        challenge = self._mint()
        self.svc._store.execute(
            "UPDATE binding_challenges SET expires_at=1 WHERE id=?",
            (challenge["challenge_id"],))

        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.consume_binding_challenge(
                challenge_id=challenge["challenge_id"], code=challenge["code"])
        self.assertEqual(caught.exception.code, "expired")

    def test_an_unknown_challenge_is_not_found(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.consume_binding_challenge(
                challenge_id="nope", code="12345678")
        self.assertEqual(caught.exception.code, "not_found")

    def test_a_challenge_cannot_target_another_members_instance(self):
        other = self._add_member("bob", "Bob")
        with self.assertRaises(IdentityServiceError):
            self._mint(user_id=other)

    def test_a_challenge_cannot_target_another_tenants_instance(self):
        with self.assertRaises(IdentityServiceError):
            self._mint(tenant_id="other-tenant")


class PersonalChannelLinkTests(_LinkFixture):
    """Linking reuses the global identity mapping; unlinking must not touch it."""

    PROVIDER = "feishu"
    ISSUER = "fs"
    SUBJECT = "ou_alice"

    def _link(self, **over):
        args = dict(tenant_id=self.tenant, user_id=self.member_id,
                    instance_id=self.instance["id"], provider=self.PROVIDER,
                    issuer=self.ISSUER, subject=self.SUBJECT)
        args.update(over)
        return self.svc.link_personal_channel(**args)

    def test_linking_creates_the_route_and_the_global_mapping(self):
        link = self._link()
        self.assertEqual(link["tenant_id"], self.tenant)
        self.assertEqual(link["user_id"], self.member_id)
        self.assertEqual(link["instance_id"], self.instance["id"])
        self.assertTrue(link["active"])
        self.assertIsNotNone(self.svc.find_user_for_external_identity(
            self.PROVIDER, self.ISSUER, self.SUBJECT))

    def test_linking_reuses_an_existing_mapping_for_the_same_user(self):
        """Re-linking must not pile up identity rows."""
        self._link()
        self._link()
        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM external_identities")
        self.assertEqual(rows[0]["c"], 1)

    def test_a_triple_bound_to_another_user_conflicts_without_overwriting(self):
        bob = self._add_member("bob", "Bob")
        bob_instance = self._personal_instance(bob, "Bob Bot", app_id="cli_bob")
        self.svc.link_personal_channel(
            tenant_id=self.tenant, user_id=bob, instance_id=bob_instance["id"],
            provider=self.PROVIDER, issuer=self.ISSUER, subject=self.SUBJECT)

        with self.assertRaises(IdentityServiceError) as caught:
            self._link()  # Alice tries to claim an identity already Bob's
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(
            self.svc.find_user_for_external_identity(
                self.PROVIDER, self.ISSUER, self.SUBJECT)["id"], bob)

    def test_unlinking_removes_the_route_but_not_the_global_mapping(self):
        """The red line: another tenant may still depend on that mapping."""
        self._link()
        self.svc.unlink_personal_channel(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"])

        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"]))
        self.assertIsNotNone(
            self.svc.find_user_for_external_identity(
                self.PROVIDER, self.ISSUER, self.SUBJECT),
            "unlink must not delete the global identity mapping")

    def test_unlinking_in_one_tenant_leaves_another_tenants_route_alone(self):
        """The red line: the same person may route the same provider identity
        in two tenants; dropping the route here must leave the one there."""
        self._link()
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tenant_b,
            operation="bind-existing", username="alice", display_name="Alice",
            temporary_password="MemTempPass1", roles=["member"])
        foreign = self._personal_instance(
            self.member_id, "Globex Bot", app_id="cli_globex",
            tenant_id=self.tenant_b)
        self.svc.link_personal_channel(
            tenant_id=self.tenant_b, user_id=self.member_id,
            instance_id=foreign["id"], provider=self.PROVIDER,
            issuer=self.ISSUER, subject=self.SUBJECT)

        self.svc.unlink_personal_channel(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"])

        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"]))
        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.tenant_b, user_id=self.member_id,
            instance_id=foreign["id"]),
            "another tenant's personal route must survive")

    def test_relinking_after_unlink_reactivates_the_route(self):
        self._link()
        self.svc.unlink_personal_channel(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"])

        again = self._link()

        self.assertTrue(again["active"])
        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.tenant, user_id=self.member_id,
            instance_id=self.instance["id"]))

    def test_a_route_is_not_visible_to_a_different_user(self):
        """The key includes the user, so another member cannot resolve this
        route even when they name the same instance."""
        self._link()
        bob = self._add_member("bob", "Bob")

        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.tenant, user_id=bob,
            instance_id=self.instance["id"]))


if __name__ == "__main__":
    unittest.main()
