# encoding:utf-8
"""A member's own message channels (change enable-member-personal-console 6.x).

The member console registers channel instances the member *owns*: the tenant and
the owner are fixed from the verified session, the credential is write-only,
and the identity link is created only from a code the member proved control of
by sending it from their own IM account.

These tests lock the properties that make the surface safe to expose:

* scope/owner cannot be named by the client, and the tenant's own channel
  surface keeps its administrator gate untouched (6.1);
* the credential is masked everywhere, and a stale version or a foreign owner is
  refused without touching the stored ciphertext (6.2);
* only channel types declared ready may be onboarded, and one external
  application may not be connected twice across the personal/public boundary
  (6.3);
* the challenge is single-use, attempt-bounded and expiry-bounded, and a link can
  only be created from a triple the *provider* observed (6.4);
* unlinking removes this tenant's route and nothing else (6.5);
* a refused personal inbound contributes no private preview or challenge code to
  the administrator's pending list (6.6);
* "saved" and "connected" are reported separately, and a governance stop wins
  over an owner's enable (6.7).
"""

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from auth.crypto import decrypt_secret
from auth.service import IdentityService, IdentityServiceError


@contextmanager
def _personal_runtime_on(*channel_types):
    """Raise the staged personal-execution switch for one test.

    Production ships ``PERSONAL_RUNTIME_ACCEPTED_TYPES`` empty (task 7.5: no
    real vendor acceptance has been recorded yet), which keeps every personal
    connection closed. Tests raise it per type so the code paths behind the gate
    are exercised *against the same gate* the deployment keeps shut — never by
    removing the check.
    """
    with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
               frozenset(channel_types)), \
            patch("channel.channel_instances.PUBLIC_PERSONAL_INGRESS_TYPES",
                  frozenset(channel_types)), \
            patch("config.conf", lambda: {"personal_channel_runtime": True}):
        yield

FEISHU_BUNDLE = {
    "feishu_app_id": "cli_personal_a",
    "feishu_app_secret": "s3cr3t-personal",
    "feishu_bot_name": "Alice Bot",
}
TENANT_BUNDLE = {
    "feishu_app_id": "cli_tenant_shared",
    "feishu_app_secret": "s3cr3t-tenant",
    "feishu_bot_name": "Tenant Bot",
}


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _expect_error(test, expected_code, expected_status, fn, *args, **kwargs):
    with test.assertRaises(IdentityServiceError) as caught:
        fn(*args, **kwargs)
    test.assertEqual(caught.exception.code, expected_code, str(caught.exception))
    test.assertEqual(caught.exception.status, expected_status)
    return caught.exception


class _Fixture(unittest.TestCase):
    """One tenant with two members, plus a second tenant to cross over to."""

    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

    ROOT_PW = "Str0ngRootFinal"
    MEMBER_PW = "Str0ngMemberFinal"

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", self.ROOT_PW)
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]

        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password=self.ROOT_PW,
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants()
                   if t["code"] == "globex"][0]["id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        self.admin_b = [m for m in self.svc.list_members(self.tb)["items"]
                        if m["username"] == "globexadmin"][0]["user_id"]

        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)
        self.svc.bind_agent(tenant_id=self.tb, agent_id="agent-b",
                            private_owner_user_id=None)

        self.alice = self._add_member("alice")
        self.bob = self._add_member("bob")

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _add_member(self, username, tenant_id=None):
        tenant_id = tenant_id or self.ta
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=tenant_id,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="MemTempPass1",
            roles=["member"])
        user_id = [m for m in self.svc.list_members(tenant_id)["items"]
                   if m["username"] == username][0]["user_id"]
        # The console refuses a forced password change, and a credential write
        # proves presence with the account password, so the member has to be a
        # normal account before any of this is reachable.
        session = self.svc.login(username, "MemTempPass1")
        self.svc.change_password(session.token, "MemTempPass1", self.MEMBER_PW)
        return user_id

    # -- helpers ---------------------------------------------------------

    def _credential_rows(self, tenant_id):
        return [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM credentials WHERE tenant_id=? AND resource_kind='channel'"
            " ORDER BY name", (tenant_id,))]

    def _instance_row(self, instance_id):
        rows = self.svc._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE id=?", (instance_id,))
        return dict(rows[0]) if rows else None

    def _create(self, owner, *, display_name="My Bot",
                credentials=None, channel_type="feishu", agent_id="agent-a",
                tenant_id=None, password=None):
        return self.svc.create_personal_channel_instance(
            actor_user_id=owner, tenant_id=tenant_id or self.ta,
            channel_type=channel_type, display_name=display_name,
            agent_id=agent_id,
            credentials=dict(credentials or FEISHU_BUNDLE),
            recent_password=password or self.MEMBER_PW)

    def _tenant_instance(self, display_name="Tenant Bot", **over):
        args = dict(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type="feishu", display_name=display_name,
            agent_id="agent-a", credentials=dict(TENANT_BUNDLE),
            recent_password=self.ROOT_PW)
        args.update(over)
        return self.svc.create_tenant_channel_instance(**args)


class PersonalSurfaceTests(_Fixture):
    """6.1 — a registered personal surface with a fixed tenant and owner."""

    def test_create_forces_the_callers_own_scope_and_owner(self):
        created = self._create(self.alice)
        row = self._instance_row(created["id"])
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], self.alice)
        self.assertEqual(row["tenant_id"], self.ta)
        # The client-facing projection does not even carry the owner field, so
        # there is nothing to forge and nothing to overwrite.
        self.assertNotIn("owner_user_id", created)

    def test_the_listing_shows_only_my_own_instances(self):
        mine = self._create(self.alice, display_name="Alice Bot")
        self._create(self.bob, display_name="Bob Bot",
                     credentials=dict(FEISHU_BUNDLE,
                                      feishu_app_id="cli_bob"))
        listing = self.svc.list_personal_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)
        self.assertEqual([i["id"] for i in listing["items"]], [mine["id"]])

    def test_another_members_instance_is_not_addressable(self):
        bobs = self._create(self.bob, credentials=dict(FEISHU_BUNDLE,
                                                       feishu_app_id="cli_bob"))
        _expect_error(
            self, "forbidden", 403, self.svc.get_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=bobs["id"])
        _expect_error(
            self, "forbidden", 403, self.svc.set_personal_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=bobs["id"], active=False, expected_version=1,
            recent_password=self.MEMBER_PW)

    def test_another_tenants_personal_instance_is_out_of_reach(self):
        elsewhere = self.svc.create_tenant_channel_instance(
            actor_user_id=self.admin_b, tenant_id=self.tb,
            channel_type="feishu", display_name="Globex Bot",
            agent_id="agent-b", credentials=dict(FEISHU_BUNDLE),
            recent_password="Str0ngGlobexFinal",
            scope="user", owner_user_id=self.admin_b)
        # The id exists; the (tenant, id) pair does not.
        _expect_error(
            self, "not_found", 404, self.svc.get_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=elsewhere["id"])

    def test_the_public_surface_keeps_its_administrator_gate(self):
        _expect_error(
            self, "forbidden", 403, self.svc.create_tenant_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta, channel_type="feishu",
            display_name="Sneaky", agent_id="agent-a",
            credentials=dict(FEISHU_BUNDLE), recent_password=self.MEMBER_PW)
        _expect_error(
            self, "forbidden", 403,
            self.svc.list_tenant_channel_instances,
            actor_user_id=self.alice, tenant_id=self.ta)
        _expect_error(
            self, "forbidden", 403, self.svc.set_tenant_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta, instance_id="ci_x",
            active=False, expected_version=1, recent_password=self.MEMBER_PW)

    def test_a_non_member_cannot_register_anything(self):
        _expect_error(
            self, "forbidden", 403, self.svc.create_personal_channel_instance,
            actor_user_id=self.admin_b, tenant_id=self.ta, channel_type="feishu",
            display_name="Outsider", agent_id="agent-a",
            credentials=dict(FEISHU_BUNDLE),
            recent_password="Str0ngGlobexFinal")


class CredentialMaskingTests(_Fixture):
    """6.2 — masked projections, versioned rotation, owner-scoped revoke."""

    def test_the_projection_reports_only_names_and_a_version(self):
        created = self._create(self.alice)
        credential = created["credential"]
        self.assertTrue(credential["configured"])
        self.assertEqual(credential["version"], 1)
        self.assertIn("feishu_app_id", credential["fields"])
        blob = json.dumps(created, default=str)
        self.assertNotIn("s3cr3t-personal", blob)
        self.assertNotIn("cli_personal_a", blob)

    def test_a_rotation_appends_a_version_and_keeps_one_credential_row(self):
        created = self._create(self.alice)
        before = self._credential_rows(self.ta)
        self.assertEqual(len(before), 1)
        updated = self.svc.update_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            credentials={"feishu_app_id": "cli_personal_a",
                         "feishu_app_secret": "rotated-secret"},
            recent_password=self.MEMBER_PW)
        after = self._credential_rows(self.ta)
        self.assertEqual(len(after), 1, "an instance has exactly one credential")
        self.assertEqual(after[0]["version"], 2)
        versions = [dict(r) for r in self.svc._store.execute(
            "SELECT version, action FROM credential_versions WHERE credential_id=?"
            " ORDER BY version", (after[0]["id"],))]
        self.assertEqual([v["version"] for v in versions], [1, 2])
        self.assertEqual(updated["credential"]["version"], 2)

    def test_the_running_bundle_is_the_new_one_not_the_old(self):
        created = self._create(self.alice)
        before = self.svc.channel_instance_credentials(self.ta, created["id"])
        self.assertEqual(before["feishu_app_secret"], "s3cr3t-personal")
        self.svc.update_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            credentials={"feishu_app_secret": "rotated-secret"},
            recent_password=self.MEMBER_PW)
        bundle = self.svc.channel_instance_credentials(self.ta, created["id"])
        self.assertEqual(bundle["feishu_app_secret"], "rotated-secret")
        self.assertEqual(bundle["feishu_app_id"], "cli_personal_a")

    def test_a_stale_version_is_refused_and_changes_nothing(self):
        created = self._create(self.alice)
        self.svc.update_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            display_name="Renamed once", recent_password=self.MEMBER_PW)
        rows_before = self._credential_rows(self.ta)
        _expect_error(
            self, "conflict", 409, self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            display_name="Renamed twice", recent_password=self.MEMBER_PW)
        row = self._instance_row(created["id"])
        self.assertEqual(row["display_name"], "Renamed once")
        self.assertEqual(self._credential_rows(self.ta)[0]["ciphertext"],
                         rows_before[0]["ciphertext"])

    def test_another_member_cannot_rotate_my_credential(self):
        created = self._create(self.alice)
        before = self._credential_rows(self.ta)[0]["ciphertext"]
        _expect_error(
            self, "forbidden", 403, self.svc.update_personal_channel_instance,
            actor_user_id=self.bob, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            credentials={"feishu_app_secret": "stolen"},
            recent_password=self.MEMBER_PW)
        self.assertEqual(self._credential_rows(self.ta)[0]["ciphertext"], before)

    def test_revoke_deactivates_the_credential_and_stops_the_instance(self):
        created = self._create(self.alice)
        revoked = self.svc.revoke_personal_channel_credentials(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            recent_password=self.MEMBER_PW)
        self.assertFalse(revoked["active"])
        self.assertFalse(revoked["credential"]["configured"])
        self.assertFalse(self._credential_rows(self.ta)[0]["active"])
        # "Revoked" has to mean "unusable": the runtime path must not be able to
        # run the instance with the withdrawn credential.
        _expect_error(self, "not_found", 404,
                      self.svc.channel_instance_credentials, self.ta,
                      created["id"])
        actions = [r["action"] for r in self.svc._store.execute(
            "SELECT action FROM audit_events ORDER BY rowid")]
        self.assertIn("channel.credential.revoke", actions)

    def test_revoke_requires_the_owners_password(self):
        created = self._create(self.alice)
        _expect_error(
            self, "invalid_old", 401,
            self.svc.revoke_personal_channel_credentials,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            recent_password="wrong-password")


class ReadinessTests(_Fixture):
    """6.3 — only declared-ready types, and one application per connection."""

    def test_the_type_catalog_carries_a_verdict_per_type(self):
        from channel.channel_instances import personal_channel_types

        catalog = {item["channel_type"]: item for item in personal_channel_types()}
        # A type the console can offer is a type the server accepts: the field
        # contract and the readiness verdict come from one declaration.
        self.assertTrue(catalog["feishu"]["ready"])
        self.assertEqual(catalog["feishu"]["reason"], "")
        self.assertTrue(catalog["feishu"]["credential_fields"])
        for item in catalog.values():
            # "ready" and "no reason" are the same statement, or the console
            # would show a form whose save is refused.
            self.assertEqual(item["ready"], item["reason"] == "")

    def test_an_unknown_type_is_refused_with_an_actionable_reason(self):
        from channel.channel_instances import personal_channel_ready

        ready, reason = personal_channel_ready("wechat_kf")
        self.assertFalse(ready)
        self.assertIn(reason, {"not_multi_instance", "no_credential_contract"})
        _expect_error(
            self, "channel_type_not_ready", 403,
            self.svc.create_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            channel_type="wechat_kf", display_name="Weird", agent_id="agent-a",
            credentials={"wechat_kf_token": "x"},
            recent_password=self.MEMBER_PW)

    def test_configuration_can_narrow_the_ready_set_but_never_widen_it(self):
        from config import conf

        with patch.object(conf(), "get",
                          side_effect=lambda key, default=None: (
                              ["telegram"] if key == "personal_channel_ready_types"
                              else default)):
            from channel.channel_instances import personal_channel_ready

            self.assertEqual(personal_channel_ready("telegram"), (True, ""))
            self.assertEqual(personal_channel_ready("feishu"),
                             (False, "not_allowed"))
            # A type outside the base set cannot be opened by configuration.
            self.assertFalse(personal_channel_ready("wechat_kf")[0])
            _expect_error(
                self, "channel_type_not_ready", 403,
                self.svc.create_personal_channel_instance,
                actor_user_id=self.alice, tenant_id=self.ta,
                channel_type="feishu", display_name="Narrowed",
                agent_id="agent-a", credentials=dict(FEISHU_BUNDLE),
                recent_password=self.MEMBER_PW)

    def test_a_personal_instance_may_not_borrow_the_tenants_application(self):
        self._tenant_instance()
        _expect_error(
            self, "app_conflict", 409, self._create, self.alice,
            credentials=dict(TENANT_BUNDLE))

    def test_two_personal_instances_may_not_share_an_application(self):
        self._create(self.alice)
        _expect_error(self, "app_conflict", 409, self._create, self.bob)

    def test_two_shared_instances_may_still_share_an_application(self):
        """The upstream tenant surface's own behaviour is not narrowed here."""
        self._tenant_instance(display_name="Tenant Bot")
        self._tenant_instance(display_name="Tenant Bot 2")
        listing = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        self.assertEqual(listing["total"], 2)

    def test_a_rotation_onto_a_taken_application_is_refused(self):
        self._tenant_instance()
        mine = self._create(self.alice)
        before = self._instance_row(mine["id"])["app_fingerprint"]
        _expect_error(
            self, "app_conflict", 409,
            self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=mine["id"], expected_version=mine["version"],
            credentials={"feishu_app_id": "cli_tenant_shared"},
            recent_password=self.MEMBER_PW)
        # The refusal left the old application in place.
        self.assertEqual(self._instance_row(mine["id"])["app_fingerprint"], before)

    def test_the_fingerprint_is_not_the_application_id(self):
        created = self._create(self.alice)
        row = self._instance_row(created["id"])
        self.assertTrue(row["app_fingerprint"])
        self.assertNotIn("cli_personal_a", row["app_fingerprint"])

    def test_enabling_an_instance_whose_application_is_taken_is_refused(self):
        first = self._create(self.alice)
        second = self._create(self.bob, credentials=dict(FEISHU_BUNDLE,
                                                         feishu_app_id="cli_bob"))
        # Move Bob's instance onto Alice's application while Alice's is off, so
        # the write itself is legal…
        disabled_second = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.bob, tenant_id=self.ta,
            instance_id=second["id"], active=False,
            expected_version=second["version"], recent_password=self.MEMBER_PW)
        rotated = self.svc.update_personal_channel_instance(
            actor_user_id=self.bob, tenant_id=self.ta,
            instance_id=second["id"], expected_version=disabled_second["version"],
            credentials={"feishu_app_id": "cli_personal_a"},
            recent_password=self.MEMBER_PW)
        # …and the collision is caught when one of them is actually enabled,
        # which is when the vendor connection would fight.
        _expect_error(
            self, "app_conflict", 409,
            self.svc.set_personal_channel_instance_active,
            actor_user_id=self.bob, tenant_id=self.ta,
            instance_id=second["id"], active=True,
            expected_version=rotated["version"],
            recent_password=self.MEMBER_PW)
        self.assertFalse(self.svc.get_personal_channel_instance(
            actor_user_id=self.bob, tenant_id=self.ta,
            instance_id=second["id"])["active"])
        del first


class BindingChallengeTests(_Fixture):
    """6.4 — proof of control, single use, bounded attempts, unique triples."""

    def test_a_started_challenge_stores_only_a_hash(self):
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        code = challenge["code"]
        self.assertTrue(code)
        rows = [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM binding_challenges")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instance_id"], created["id"])
        self.assertEqual(rows[0]["user_id"], self.alice)
        self.assertNotIn(code, rows[0]["code_hash"])
        self.assertGreater(rows[0]["expires_at"], 0)

    def test_a_code_is_redeemed_once_and_links_the_observed_sender(self):
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        redeemed = self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=created["id"], code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject="ou_alice")
        self.assertEqual(redeemed["link"]["subject"], "ou_alice")
        status = self.svc.personal_channel_binding_status(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.assertEqual(status["link"]["provider"], "feishu")
        # The owner confirms *which* account is linked without reading back the
        # identifier the provider minted.
        self.assertTrue(status["link"]["subject_masked"].startswith("••••"))
        self.assertNotIn("ou_alice", json.dumps(status, default=str))

    def test_the_same_code_cannot_be_replayed(self):
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=created["id"], code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject="ou_alice")
        _expect_error(
            self, "not_found", 404,
            self.svc.redeem_personal_channel_challenge,
            tenant_id=self.ta, instance_id=created["id"],
            code=challenge["code"], provider="feishu",
            issuer="cli_personal_a", subject="ou_mallory")
        # …and the replay did not relink the instance to the second sender.
        link = self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"])
        self.assertEqual(link["subject"], "ou_alice")

    def test_one_code_arriving_twice_at_once_links_only_its_sender(self):
        """The claim is what makes single-use true, not the lookup that precedes
        it: two inbounds carrying one code must resolve to exactly one link, and
        the loser must not relink the instance to the sender it observed."""
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def _redeem(subject):
            barrier.wait()
            try:
                self.svc.redeem_personal_channel_challenge(
                    tenant_id=self.ta, instance_id=created["id"],
                    code=challenge["code"], provider="feishu",
                    issuer="cli_personal_a", subject=subject)
                with lock:
                    outcomes.append((subject, "ok"))
            except IdentityServiceError as e:
                with lock:
                    outcomes.append((subject, e.code))

        threads = [threading.Thread(target=_redeem, args=(subject,))
                   for subject in ("ou_alice", "ou_mallory")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([o[1] for o in outcomes].count("ok"), 1, outcomes)
        link = self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"])
        winner = [o[0] for o in outcomes if o[1] == "ok"][0]
        self.assertEqual(link["subject"], winner,
                         "the loser must not have overwritten the winner")
        consumed = self.svc._store.execute(
            "SELECT consumed_at FROM binding_challenges WHERE id=?",
            (challenge["challenge_id"],))[0]
        self.assertIsNotNone(consumed["consumed_at"])

    def test_a_wrong_code_burns_an_attempt_and_then_locks(self):
        from auth.service import CHALLENGE_MAX_ATTEMPTS

        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        for _ in range(CHALLENGE_MAX_ATTEMPTS):
            _expect_error(
                self, "bad_request", 400,
                self.svc.redeem_personal_channel_challenge,
                tenant_id=self.ta, instance_id=created["id"], code="00000000",
                provider="feishu", issuer="cli_personal_a", subject="ou_alice")
        attempts = self.svc._store.execute(
            "SELECT attempts, consumed_at FROM binding_challenges"
            " WHERE id=?", (challenge["challenge_id"],))[0]
        self.assertEqual(attempts["attempts"], CHALLENGE_MAX_ATTEMPTS)
        self.assertIsNone(attempts["consumed_at"])
        # Even the right code is now refused: the budget, not the code's
        # entropy, is what bounds guessing.
        _expect_error(
            self, "too_many_requests", 429,
            self.svc.redeem_personal_channel_challenge,
            tenant_id=self.ta, instance_id=created["id"], code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject="ou_alice")

    def test_an_expired_code_is_usable_no_more(self):
        from auth import service as service_module

        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        with patch.object(service_module.time, "time",
                          return_value=challenge["expires_at"] + 1):
            _expect_error(
                self, "expired", 410,
                self.svc.redeem_personal_channel_challenge,
                tenant_id=self.ta, instance_id=created["id"],
                code=challenge["code"], provider="feishu",
                issuer="cli_personal_a", subject="ou_alice")
            self.assertIsNone(self.svc.personal_channel_binding_status(
                actor_user_id=self.alice, tenant_id=self.ta,
                instance_id=created["id"])["challenge"])

    def test_a_code_for_another_instance_does_not_link_mine(self):
        mine = self._create(self.alice)
        bobs = self._create(self.bob, credentials=dict(FEISHU_BUNDLE,
                                                       feishu_app_id="cli_bob"))
        bobs_challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.bob, tenant_id=self.ta, instance_id=bobs["id"])
        # The code is scoped to bob's instance: presenting it against mine is a
        # lookup miss, not a check a caller could influence.
        _expect_error(
            self, "not_found", 404,
            self.svc.redeem_personal_channel_challenge,
            tenant_id=self.ta, instance_id=mine["id"],
            code=bobs_challenge["code"], provider="feishu",
            issuer="cli_bob", subject="ou_bob")

    def test_a_triple_bound_to_someone_else_is_never_taken_over(self):
        bobs = self._create(self.bob, credentials=dict(FEISHU_BUNDLE,
                                                       feishu_app_id="cli_bob"))
        bobs_challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.bob, tenant_id=self.ta, instance_id=bobs["id"])
        self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=bobs["id"],
            code=bobs_challenge["code"], provider="feishu",
            issuer="cli_bob", subject="ou_shared")
        # A separate instance holding the *same* identity is Bob's own business,
        # but Alice must not be able to bind an identity that resolves to Bob.
        mine = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=mine["id"])
        _expect_error(
            self, "conflict", 409,
            self.svc.redeem_personal_channel_challenge,
            tenant_id=self.ta, instance_id=mine["id"], code=challenge["code"],
            provider="feishu", issuer="cli_bob", subject="ou_shared")

    def test_the_console_surface_offers_no_way_to_name_a_subject(self):
        """只有提供方观察到的三元组能建绑：控制台的 action 里没有「按 subject 绑定」。"""
        import inspect

        from channel.web import web_channel

        source = inspect.getsource(web_channel.PersonalChannelInstanceHandler)
        for forbidden in ('body.get("subject")', "body.get('subject')",
                          '"bind"'):
            self.assertNotIn(forbidden, source)
        for action in ("update", "revoke", "start_binding", "unlink"):
            self.assertIn(f'action == "{action}"', source)
        # enable/disable share one branch, which is still a closed enumeration:
        # anything else answers "unknown action".
        self.assertIn('action in ("enable", "disable")', source)
        self.assertIn('"unknown action: {action}"', source)

    def test_a_shared_instance_has_no_self_service_link(self):
        shared = self._tenant_instance()
        # A shared instance has no owner, so there is no instance-level link to
        # establish: its personal routes exist only as per-member challenge rows
        # that a member mints for themselves (task 7.2). A code nobody minted
        # against it therefore matches nothing, and — importantly — the refusal
        # creates no route either.
        _expect_error(
            self, "not_found", 404,
            self.svc.redeem_personal_channel_challenge,
            tenant_id=self.ta, instance_id=shared["id"], code="12345678",
            provider="feishu", issuer="cli_tenant_shared", subject="ou_alice")
        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=shared["id"]))


class IdentityLinkTests(_Fixture):
    """6.5 — the owner's own route only, and other tenants stay intact."""

    def _link(self, owner, instance_id, subject):
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=owner, tenant_id=self.ta, instance_id=instance_id)
        return self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=instance_id, code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject=subject)

    def test_unlinking_removes_the_route_and_keeps_the_identity(self):
        mine = self._create(self.alice)
        self._link(self.alice, mine["id"], "ou_alice")
        self.svc.unlink_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=mine["id"])
        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=mine["id"]))
        # The global mapping survives: the same person may still be using it in
        # another tenant, and the admin bind flow may depend on it.
        self.assertIsNotNone(self.svc.find_user_for_external_identity(
            "feishu", "cli_personal_a", "ou_alice"))

    def test_unlinking_here_does_not_touch_another_tenants_mapping(self):
        mine = self._create(self.alice)
        self._link(self.alice, mine["id"], "ou_alice")
        # Alice is also a member of Globex with her own personal instance there.
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tb,
            operation="bind-existing", username="alice", display_name="Alice",
            temporary_password="MemTempPass1", roles=["member"])
        foreign = self.svc.create_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.tb, channel_type="feishu",
            display_name="Globex Bot", agent_id="agent-b",
            credentials=dict(FEISHU_BUNDLE), recent_password=self.MEMBER_PW)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.tb,
            instance_id=foreign["id"])
        self.svc.redeem_personal_channel_challenge(
            tenant_id=self.tb, instance_id=foreign["id"],
            code=challenge["code"], provider="feishu",
            issuer="cli_personal_a", subject="ou_alice")

        self.svc.unlink_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=mine["id"])

        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.tb, user_id=self.alice, instance_id=foreign["id"]))

    def test_another_member_cannot_unlink_my_route(self):
        mine = self._create(self.alice)
        self._link(self.alice, mine["id"], "ou_alice")
        _expect_error(
            self, "forbidden", 403,
            self.svc.unlink_personal_channel_instance,
            actor_user_id=self.bob, tenant_id=self.ta, instance_id=mine["id"])
        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=mine["id"]))


class AttemptRedactionTests(_Fixture):
    """6.6 — a personal inbound contributes no private content."""

    def test_a_personal_attempt_stores_no_preview_or_nickname(self):
        created = self._create(self.alice)
        self.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_personal_a", subject="ou_stranger",
            tenant_id=self.ta, channel_type="feishu",
            instance_id=created["id"],
            sender_name="Stranger", message_preview="my private message 1234",
            is_group=False)
        row = dict(self.svc._store.execute(
            "SELECT * FROM external_identity_attempts")[0])
        self.assertEqual(row["instance_id"], created["id"])
        self.assertEqual(row["message_preview"], "")
        self.assertEqual(row["sender_name"], "")
        # The triple is kept: it is what the administrator binds, and it is not
        # private content.
        self.assertEqual(row["subject"], "ou_stranger")

    def test_a_challenge_code_never_reaches_the_store(self):
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_personal_a", subject="ou_stranger",
            tenant_id=self.ta, channel_type="feishu",
            instance_id=created["id"], personal_flow=True,
            message_preview=f"bind {challenge['code']}", is_group=False)
        row = dict(self.svc._store.execute(
            "SELECT * FROM external_identity_attempts")[0])
        self.assertNotIn(challenge["code"], json.dumps(row, default=str))

    def test_a_flagged_flow_is_redacted_even_on_a_shared_instance(self):
        shared = self._tenant_instance()
        self.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_tenant_shared", subject="ou_x",
            tenant_id=self.ta, channel_type="feishu", instance_id=shared["id"],
            personal_flow=True, sender_name="Stranger",
            message_preview="private text", is_group=False)
        row = dict(self.svc._store.execute(
            "SELECT * FROM external_identity_attempts")[0])
        self.assertEqual(row["message_preview"], "")
        self.assertEqual(row["sender_name"], "")

    def test_an_ordinary_shared_attempt_keeps_its_evidence(self):
        shared = self._tenant_instance()
        self.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_tenant_shared", subject="ou_y",
            tenant_id=self.ta, channel_type="feishu", instance_id=shared["id"],
            sender_name="Someone", message_preview="hello", is_group=False)
        row = dict(self.svc._store.execute(
            "SELECT * FROM external_identity_attempts")[0])
        self.assertEqual(row["message_preview"], "hello")
        self.assertEqual(row["sender_name"], "Someone")

    def test_the_administrator_list_redacts_personal_rows(self):
        created = self._create(self.alice)
        # A row written before this rule existed (or by a caller that could not
        # know) must not be displayed either: the read path is the last line.
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO external_identity_attempts(provider, issuer, subject,"
                " tenant_id, channel_type, instance_id, attempts, last_seen_at,"
                " sender_name, message_preview, is_group)"
                " VALUES ('feishu','cli_personal_a','ou_old',?, 'feishu', ?, 1,"
                " unixepoch(), 'Alice', 'old private text', 0)",
                (self.ta, created["id"]))
            con.commit()
        listed = self.svc.list_external_identity_attempts(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        item = listed["items"][0]
        self.assertEqual(item["message_preview"], "")
        self.assertEqual(item["sender_name"], "")


class RuntimeStateTests(_Fixture):
    """6.7 — saved is not connected, governance wins, the owner re-enables."""

    def test_disabling_keeps_the_credential_and_enabling_restores_it(self):
        created = self._create(self.alice)
        disabled = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=False,
            expected_version=created["version"], recent_password=self.MEMBER_PW)
        self.assertFalse(disabled["active"])
        self.assertTrue(disabled["credential"]["configured"])
        enabled = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=True,
            expected_version=disabled["version"], recent_password=self.MEMBER_PW)
        self.assertTrue(enabled["active"])

    def test_a_governance_stop_outranks_the_owners_enable(self):
        created = self._create(self.alice)
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], disabled=True,
            recent_password=self.ROOT_PW, reason="policy")
        row = self._instance_row(created["id"])
        _expect_error(
            self, "governance_disabled", 403,
            self.svc.set_personal_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=True,
            expected_version=row["version"], recent_password=self.MEMBER_PW)
        # Lifting the stop does not turn it back on — the owner has to say so.
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], disabled=False,
            recent_password=self.ROOT_PW)
        still_off = self.svc.get_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.assertFalse(still_off["active"])
        back = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=True,
            expected_version=still_off["version"],
            recent_password=self.MEMBER_PW)
        self.assertTrue(back["active"])

    def test_a_governance_stop_also_blocks_a_credential_rotation(self):
        created = self._create(self.alice)
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], disabled=True,
            recent_password=self.ROOT_PW)
        row = self._instance_row(created["id"])
        _expect_error(
            self, "governance_disabled", 403,
            self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=row["version"],
            credentials={"feishu_app_secret": "resurrected"},
            recent_password=self.MEMBER_PW)

    def test_the_owner_listing_shows_the_governance_flag(self):
        created = self._create(self.alice)
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], disabled=True,
            recent_password=self.ROOT_PW)
        listed = self.svc.list_personal_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)
        self.assertTrue(listed["items"][0]["governance_disabled"])
        # …and nothing about who stopped it.
        self.assertNotIn("governance_disabled_by",
                         json.dumps(listed["items"][0], default=str))

    def test_a_saved_instance_reports_a_failed_connection_separately(self):
        from channel.web import web_channel

        ctx = _FakeCtx(self.alice, self.ta)
        payload = json.dumps({
            "channel_type": "feishu", "display_name": "My Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU_BUNDLE),
            "recent_password": self.MEMBER_PW,
        }).encode()
        with _handler_scope(web_channel, ctx, self.svc), \
                patch("channel.channel_instances.apply_tenant_instance_runtime",
                      side_effect=RuntimeError("vendor refused the bot token")), \
                patch.object(web_channel.web, "header"), \
                patch.object(web_channel.web, "data", return_value=payload):
            body = json.loads(web_channel.PersonalChannelHandler().POST())
        self.assertEqual(body["status"], "success")
        self.assertFalse(body["runtime"]["applied"])
        self.assertTrue(body["runtime"]["pending"])
        self.assertIn("vendor refused", body["runtime"]["error"])
        # The instance really is stored, so "saved" is not a lie.
        self.assertEqual(
            self.svc.list_personal_channel_instances(
                actor_user_id=self.alice, tenant_id=self.ta)["total"], 1)

    def test_the_runtime_report_is_not_a_second_save(self):
        """A failed connection must not make the console retry a committed write."""
        from channel.web import web_channel

        created = self._create(self.alice)
        with patch("channel.channel_instances.apply_tenant_instance_runtime",
                   return_value={"applied": False, "pending": True,
                                 "error": "reconnect failed: invalid app secret"}):
            report = web_channel._apply_personal_channel_runtime(created["id"])
        self.assertFalse(report["applied"])
        self.assertEqual(self.svc.list_personal_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)["total"], 1)

    def test_a_connection_failure_is_diagnosable_not_silent(self):
        from channel.channel_instances import apply_tenant_instance_runtime

        created = self._create(self.alice)
        with patch("auth.service.get_identity_service", return_value=self.svc), \
                _personal_runtime_on("feishu"), \
                patch("channel.channel_instances._runtime_manager",
                      return_value=_ExplodingManager()):
            result = apply_tenant_instance_runtime(created["id"])
        self.assertFalse(result["applied"])
        self.assertIn("refused", result["error"])

    def test_the_execution_switch_keeps_an_unaccepted_type_disconnected(self):
        """7.5 — no real acceptance recorded means no live connection.

        The instance is saved and inspectable, but the vendor connection stays
        closed and the report says *pending*, not "connected": a type whose real
        inbound boundary has not been verified must not route a member's private
        conversations.
        """
        from channel.channel_instances import apply_tenant_instance_runtime

        created = self._create(self.alice)
        with patch("auth.service.get_identity_service", return_value=self.svc), \
                patch("channel.channel_instances._runtime_manager",
                      return_value=_ExplodingManager()):
            result = apply_tenant_instance_runtime(created["id"])
        self.assertFalse(result["applied"])
        self.assertTrue(result["pending"])
        self.assertIn("not enabled", result["error"])

    def test_a_disabled_instance_reports_nothing_to_run(self):
        from channel.channel_instances import apply_tenant_instance_runtime

        created = self._create(self.alice)
        disabled = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=False,
            expected_version=created["version"], recent_password=self.MEMBER_PW)
        with patch("auth.service.get_identity_service", return_value=self.svc):
            result = apply_tenant_instance_runtime(created["id"])
        self.assertTrue(result["applied"])
        self.assertFalse(disabled["active"])


class IntegrationTests(_Fixture):
    """6.8 — concurrency, rollback and quota under the real service."""

    def test_two_pages_saving_the_same_version_produce_one_winner(self):
        created = self._create(self.alice)
        outcomes = []
        lock = threading.Lock()

        def _save(name):
            try:
                self.svc.update_personal_channel_instance(
                    actor_user_id=self.alice, tenant_id=self.ta,
                    instance_id=created["id"],
                    expected_version=created["version"],
                    display_name=name, recent_password=self.MEMBER_PW)
                with lock:
                    outcomes.append((name, "ok"))
            except IdentityServiceError as e:
                with lock:
                    outcomes.append((name, e.code))

        threads = [threading.Thread(target=_save, args=(name,))
                   for name in ("FIRST-PAGE", "SECOND-PAGE")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([o[1] for o in outcomes].count("ok"), 1, outcomes)
        self.assertEqual([o[1] for o in outcomes].count("conflict"), 1, outcomes)
        row = self._instance_row(created["id"])
        self.assertIn(row["display_name"], ("FIRST-PAGE", "SECOND-PAGE"))

    def test_a_failed_audit_rolls_the_whole_create_back(self):
        from auth.service import IdentityService as _Svc

        with patch.object(_Svc, "_audit_in_tx",
                          side_effect=RuntimeError("audit store down")):
            with self.assertRaises(RuntimeError):
                self._create(self.alice)
        self.assertEqual(self._credential_rows(self.ta), [])
        self.assertEqual(self.svc.list_personal_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)["total"], 0)

    def test_a_quota_refusal_leaves_no_partial_row(self):
        self.svc.set_tenant_channel_policy(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            personal_enabled=True, allowed_types=[],
            personal_instance_limit=1, tenant_personal_instance_limit=-1,
            recent_password=self.ROOT_PW)
        self._create(self.alice)
        _expect_error(self, "quota_exceeded", 403, self._create, self.alice,
                      display_name="Second Bot",
                      credentials=dict(FEISHU_BUNDLE,
                                       feishu_app_id="cli_personal_b"))
        rows = [dict(r) for r in self.svc._store.execute(
            "SELECT scope, owner_user_id FROM tenant_channel_instances")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self._credential_rows(self.ta)), 1)

    def test_a_disabled_type_cannot_be_re_enabled_after_the_policy_narrows(self):
        created = self._create(self.alice)
        disabled = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=False,
            expected_version=created["version"], recent_password=self.MEMBER_PW)
        self.svc.set_tenant_channel_policy(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            personal_enabled=True, allowed_types=["telegram"],
            personal_instance_limit=-1, tenant_personal_instance_limit=-1,
            recent_password=self.ROOT_PW)
        _expect_error(
            self, "channel_type_not_allowed", 403,
            self.svc.set_personal_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], active=True,
            expected_version=disabled["version"], recent_password=self.MEMBER_PW)

    def test_an_unlinked_route_stops_resolving_its_sender(self):
        """解绑失效: after unlinking, the sender no longer maps to this route."""
        created = self._create(self.alice)
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=created["id"], code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject="ou_alice")
        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"]))
        self.svc.unlink_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"])
        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"]))

    def test_the_public_route_policies_are_unchanged(self):
        from auth.http_policy import ROUTE_POLICY

        self.assertEqual(ROUTE_POLICY["/api/tenant/channels"]["POST"]["policy"],
                         "tenant")
        self.assertEqual(
            ROUTE_POLICY["/api/tenant/channels/([^/]+)"]["POST"]["policy"],
            "tenant")
        self.assertEqual(
            ROUTE_POLICY["/api/tenant/channels/([^/]+)/active"]["POST"]["policy"],
            "tenant")
        for path in ("/api/personal/channels", "/api/personal/channels/([^/]+)"):
            self.assertEqual(ROUTE_POLICY[path]["GET"]["policy"], "personal")
            self.assertEqual(ROUTE_POLICY[path]["POST"]["policy"], "personal")


class _FakeCtx:
    def __init__(self, user_id, tenant_id):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.is_platform_admin = False
        self.must_change_password = False
        self.permissions = frozenset({"chat.use"})


class _FakeScope:
    def __init__(self, ctx):
        self.ctx = ctx

    def __enter__(self):
        return self.ctx

    def __exit__(self, *exc):
        return False


def _handler_scope(web_channel, ctx, service):
    return _ComposedPatch(
        patch.object(web_channel, "_db_scope", lambda: _FakeScope(ctx)),
        patch.object(web_channel, "_personal_channel_service",
                     lambda: service))


class _ComposedPatch:
    """Two patches entered as one ``with`` (keeps the test's intent readable)."""

    def __init__(self, *patches):
        self._patches = patches

    def __enter__(self):
        return [p.__enter__() for p in self._patches]

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)
        return False


class _ExplodingManager:
    def restart(self, instance):
        raise RuntimeError("vendor refused the bot token")


if __name__ == "__main__":
    unittest.main()
