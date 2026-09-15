# encoding:utf-8
"""The WeChat scan onboarding state machine, its receipt ledger and its commit.

The three failure modes this module was written to remove are pinned here:

* one process-global QR slot, which let two concurrent scans overwrite each other
  and let any console user poll any scan — covered by the session binding tests;
* a retried submit creating a second instance, or a replayed single-use grant
  being refused after the first submit *did* succeed — covered by the receipt
  and idempotency tests;
* a write refused for a later reason burning the authorization, which costs the
  operator another scan — covered by the "does not consume" tests, including the
  failure that happens *after* the instance row has committed.

Everything here drives the module's own seams (the create callable, the quota
reserver, the audit sink and the receipt sink), because the identity database is
the identity service's to open, not this module's. The fakes therefore stand in
for real service entry points rather than mocking this module's internals.
"""

from __future__ import annotations

import json
import threading
import time
import unittest

from auth import scan_authorization
from channel.web import scan_onboarding as so

#: Shaped like a real WeChat bot token. It must never appear in a returned dict,
#: a stored receipt or a log line.
BOT_TOKEN = "wxbot-9c1f4d7a2b6e8f30-never-echoed"
OWNER = "usr_member"
TENANT = "tnt_alpha"
AUTH_SESSION = "auth_ses_1"
PROVIDER = "weixin"
TARGET = "chan_wx_1"


def actor(**overrides) -> so.Actor:
    payload = {"user_id": OWNER, "tenant_id": TENANT,
               "auth_session_id": AUTH_SESSION}
    payload.update(overrides)
    return so.Actor(**payload)


def start(**overrides) -> so.Session:
    payload = {"provider": PROVIDER, "scope": "tenant", "purpose": "bind",
               "target": TARGET, "owner_user_id": OWNER, "tenant_id": TENANT,
               "auth_session_id": AUTH_SESSION}
    payload.update(overrides)
    return so.start_session(**payload)


def mint(**overrides) -> str:
    payload = {"actor_user_id": OWNER, "tenant_id": TENANT,
               "channel_type": PROVIDER}
    payload.update(overrides)
    return scan_authorization.mint(**payload)


def binding(**overrides) -> dict:
    payload = {"provider": PROVIDER, "scope": "tenant", "purpose": "bind",
               "target": TARGET, "owner_user_id": OWNER, "tenant_id": TENANT,
               "auth_session_id": AUTH_SESSION}
    payload.update(overrides)
    return payload


def expected_key(ticket: str, **overrides) -> str:
    """The receipt key the module must compute for the default commit request."""
    payload = {
        "channel_type": PROVIDER,
        "display_name": "My Bot",
        "agent_id": "",
        "credentials": {"weixin_token": BOT_TOKEN},
    }
    payload.update(overrides.pop("payload", {}))
    args = dict(binding(), scan_ticket=ticket, payload=payload)
    args.update(overrides)
    return so.receipt_key(**args)


def commit_kwargs(**overrides) -> dict:
    payload = {
        "display_name": "My Bot",
        "credentials": {"weixin_token": BOT_TOKEN},
    }
    payload.update(overrides)
    return payload


class CreateStub:
    """Stands in for ``IdentityService.create_tenant_channel_instance``.

    Counts its calls, because "the second submit does not create a second
    instance" is only observable as "the write path was entered once".
    """

    def __init__(self, *, failures: int = 0, error: Exception | None = None,
                 extra: dict | None = None, connected=None,
                 delay: float = 0.0) -> None:
        self.calls: list = []
        self.failures = failures
        self.error = error or RuntimeError("display name exists")
        self.extra = dict(extra or {})
        self.connected = connected
        self.delay = delay
        self._guard = threading.Lock()
        self._seen = 0
        self.created = 0

    def __call__(self, **kwargs):
        if self.delay:
            # A real create talks to the identity service, so widening the window
            # here is what makes the concurrency test able to catch a commit path
            # that is no longer serialized.
            time.sleep(self.delay)
        with self._guard:
            self.calls.append(dict(kwargs))
            self._seen += 1
            if self._seen <= self.failures:
                raise self.error
            self.created += 1
            instance_id = f"chan_created_{self.created}"
        result = {
            "id": instance_id,
            "channel_type": kwargs.get("channel_type", ""),
            "display_name": kwargs.get("display_name", ""),
            "agent_id": kwargs.get("agent_id", ""),
        }
        result.update(self.extra)
        if self.connected is not None:
            result["connected"] = self.connected
        return result


class RedeemingCreateStub(CreateStub):
    """A create that redeems the scan grant itself, like the delivered service.

    ``IdentityService.create_tenant_channel_instance`` consumes the ticket as its
    own transaction commits (see ``so._redeem_authorization``), so by the time the
    commit *tail* runs the grant is already spent. A fake that does not do this
    cannot reproduce the one ordering the real route always has.
    """

    def __call__(self, **kwargs):
        result = super().__call__(**kwargs)
        scan_authorization.consume(
            kwargs.get("scan_ticket", ""),
            actor_user_id=kwargs.get("actor_user_id", ""),
            tenant_id=kwargs.get("tenant_id", ""),
            channel_type=kwargs.get("channel_type", ""))
        return result


class QuotaStub:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.reserved: list = []
        self.released: list = []
        self.error = error

    def reserve(self, **kwargs):
        if self.error is not None:
            raise self.error
        token = f"reservation-{len(self.reserved) + 1}"
        self.reserved.append(dict(kwargs))
        return token

    def release(self, token):
        self.released.append(token)


class RefusedWrite(Exception):
    """An identity-service refusal, tagged the way the real one is."""

    code = "conflict"


class ScanOnboardingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Both registries are process-global on purpose (the grants already are),
        # so every test starts from empty ones.
        so._reset()
        scan_authorization._reset()

    def tearDown(self) -> None:
        so._reset()
        scan_authorization._reset()

    # --- session binding ---------------------------------------------------

    def test_two_initiators_get_independent_sessions(self):
        first = start(owner_user_id="usr_a", tenant_id="tnt_a",
                      auth_session_id="ses_a")
        second = start(owner_user_id="usr_b", tenant_id="tnt_b",
                      auth_session_id="ses_b")
        self.assertNotEqual(first.handle, second.handle)

        so.attach_qr(first.handle, qrcode="qr-a",
                     qrcode_url="https://vendor/qr-a.png",
                     base_url="https://vendor", actor=actor(
                         user_id="usr_a", tenant_id="tnt_a",
                         auth_session_id="ses_a"))
        # The other initiator's record is untouched: one session cannot overwrite
        # another the way the single global slot did.
        other = so.get_session(second.handle, actor=actor(
            user_id="usr_b", tenant_id="tnt_b", auth_session_id="ses_b"))
        self.assertEqual(other.qrcode, "")
        self.assertEqual(other.target, TARGET)
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.attach_qr(second.handle, qrcode="qr-b", actor=actor(
                user_id="usr_a", tenant_id="tnt_a", auth_session_id="ses_a"))
        self.assertEqual(ctx.exception.code, "not_owner")

    def test_cross_actor_reads_are_refused_without_disclosing_anything(self):
        session = start()
        session = so.attach_qr(session.handle, qrcode="qr-1", actor=actor())

        probes = [
            actor(user_id="usr_someone_else"),
            actor(tenant_id="tnt_other"),
            actor(auth_session_id="ses_other"),
        ]
        answers = set()
        for probe in probes:
            with self.assertRaises(so.ScanSessionError) as ctx:
                so.get_session(session.handle, actor=probe)
            self.assertEqual(ctx.exception.code, "not_owner")
            answers.add((ctx.exception.code, str(ctx.exception)))
        # All three mismatch dimensions answer identically...
        self.assertEqual(len(answers), 1)
        # ...and the answer says nothing about the handle, its owner, the tenant
        # or how far the scan got.
        code, message = answers.pop()
        for leak in (session.handle, OWNER, TENANT, AUTH_SESSION, TARGET,
                     "pending"):
            self.assertNotIn(leak, message)
        self.assertEqual(message, "scan session is not available to this actor")
        # A handle that never existed is answered as "no session at all": the two
        # refusals are deliberately different in type (None vs raise) so a caller
        # can audit a probe, but the wire answer is the caller's to collapse.
        self.assertIsNone(so.get_session("no-such-handle", actor=actor()))
        # The other two dimensions of a handle's identity refuse too, and only
        # after ownership was established — a foreign probe that also names the
        # wrong provider still gets the ownership answer, so the dimension check
        # can never be used as an oracle for "does this handle exist".
        for overrides in ({"provider": "feishu"}, {"scope": "personal"}):
            with self.assertRaises(so.ScanSessionError) as ctx:
                so.require_session(session.handle, actor=actor(), **overrides)
            self.assertEqual(ctx.exception.code, "binding_mismatch")
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.require_session(session.handle, actor=actor(user_id="usr_probe"),
                               provider="feishu")
        self.assertEqual(ctx.exception.code, "not_owner")

    def test_unauthenticated_reads_are_refused(self):
        session = start()
        for anonymous in (None, so.Actor(user_id=""), {}):
            with self.assertRaises(so.ScanOnboardingError) as ctx:
                so.get_session(session.handle, actor=anonymous)
            self.assertEqual(ctx.exception.code, "unauthenticated")

    def test_start_session_rejects_an_unbound_binding(self):
        with self.assertRaises(so.ScanOnboardingError) as ctx:
            so.start_session(provider=PROVIDER, scope="tenant", purpose="bind",
                             target=TARGET, owner_user_id="", tenant_id=TENANT,
                             auth_session_id=AUTH_SESSION)
        self.assertEqual(ctx.exception.code, "unauthenticated")
        for overrides in (
            {"provider": ""}, {"scope": "galaxy"}, {"target": ""},
            {"purpose": ""}, {"auth_session_id": ""}, {"tenant_id": ""},
        ):
            args = {"provider": PROVIDER, "scope": "tenant", "purpose": "bind",
                    "target": TARGET, "owner_user_id": OWNER,
                    "tenant_id": TENANT, "auth_session_id": AUTH_SESSION}
            args.update(overrides)
            with self.assertRaises(so.ScanSessionError) as ctx:
                so.start_session(**args)
            self.assertEqual(ctx.exception.code, "bad_binding")

    def test_session_matches_only_its_own_dimensions(self):
        session = start()
        self.assertTrue(session.matches(provider=PROVIDER, scope="tenant",
                                        purpose="bind", target=TARGET))
        self.assertFalse(session.matches(provider="feishu"))
        self.assertFalse(session.matches(scope="personal"))
        self.assertFalse(session.matches(target="chan_other"))
        self.assertTrue(session.owns(actor()))
        self.assertFalse(session.owns(actor(user_id="usr_other")))
        self.assertFalse(session.owns(None))

    # --- QR material and the status machine --------------------------------

    def test_qr_material_lives_only_while_pending(self):
        session = start()
        attached = so.attach_qr(session.handle, qrcode="qr-raw",
                                qrcode_url="https://vendor/qr.png",
                                base_url="https://vendor/api",
                                actor=actor())
        self.assertEqual(attached.qrcode, "qr-raw")
        self.assertEqual(attached.version, session.version + 1)

        payload = attached.payload()
        # The client projection carries the renderable URL, never the polling
        # token, and never the initiator's ownership fields.
        self.assertEqual(payload["qrcode_url"], "https://vendor/qr.png")
        self.assertNotIn("qrcode", payload)
        for leak in (BOT_TOKEN, OWNER, TENANT, AUTH_SESSION):
            self.assertNotIn(leak, json.dumps(payload))

        confirmed = so.mark_status(session.handle, "confirmed", actor=actor())
        self.assertEqual(confirmed.status, "confirmed")
        # Leaving pending scrubs the temporary QR material.
        self.assertEqual(confirmed.qrcode, "")
        self.assertEqual(confirmed.qrcode_url, "")
        self.assertEqual(confirmed.base_url, "")
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.attach_qr(session.handle, qrcode="qr-late", actor=actor())
        self.assertEqual(ctx.exception.code, "invalid_transition")
        # The terminal-ish status stays readable so a late poll learns the
        # outcome instead of starting a second scan.
        self.assertEqual(
            so.get_session(session.handle, actor=actor()).status, "confirmed")

    def test_status_machine_refuses_invalid_moves(self):
        session = start()
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.mark_status(session.handle, "teleported", actor=actor())
        self.assertEqual(ctx.exception.code, "invalid_status")

        so.mark_status(session.handle, "confirmed", actor=actor())
        # Re-asserting the current status is a no-op, so a retried poll does not
        # have to remember what it last sent.
        again = so.mark_status(session.handle, "confirmed", actor=actor())
        self.assertEqual(again.status, "confirmed")
        # A terminal state is a dead end, and reaching it goes through the
        # documented path: confirmed -> committing -> committed.
        so.mark_status(session.handle, "committing", actor=actor())
        so.mark_status(session.handle, "committed", actor=actor(), error="")
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.mark_status(session.handle, "confirmed", actor=actor())
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.assertTrue(so.get_session(session.handle, actor=actor()).terminal)

    def test_expiry_is_per_session_and_purge_resurrects_nothing(self):
        keep = start(ttl_seconds=3600)
        doomed = start(ttl_seconds=0)
        self.assertEqual(so.purge_expired(), 1)
        self.assertIsNone(so.get_session(doomed.handle, actor=actor()))
        self.assertEqual(
            so.get_session(keep.handle, actor=actor()).handle, keep.handle)
        # A purged handle answers exactly like an unknown one, and nothing about
        # it can be mutated back into existence.
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.attach_qr(doomed.handle, qrcode="qr-again", actor=actor())
        self.assertEqual(ctx.exception.code, "not_owner")
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.mark_status(doomed.handle, "confirmed", actor=actor())
        self.assertEqual(ctx.exception.code, "not_owner")
        # A fresh start is a fresh handle; a retired one is never reused.
        self.assertNotEqual(start().handle, doomed.handle)

    def test_unknown_and_expired_handles_read_as_none(self):
        self.assertIsNone(so.get_session("", actor=actor()))
        self.assertIsNone(so.get_session("no-such-handle", actor=actor()))
        expired = start(ttl_seconds=0)
        # Expired on access: refused and dropped in the same step.
        self.assertIsNone(so.get_session(expired.handle, actor=actor()))
        self.assertEqual(so.purge_expired(), 0)
        self.assertEqual(so.purge_expired(), 0)

    # --- receipt keys ------------------------------------------------------

    def test_receipt_key_binds_the_whole_operation(self):
        ticket = mint()
        base = dict(binding(), scan_ticket=ticket,
                    payload={"channel_type": PROVIDER, "display_name": "My Bot",
                             "credentials": {"weixin_token": BOT_TOKEN}})
        key = so.receipt_key(**base)
        self.assertEqual(key, so.receipt_key(**base))
        # The digest never carries the secret it is computed over.
        self.assertNotIn(BOT_TOKEN, key)

        for changed in (
            {"provider": "feishu"},
            {"scope": "personal"},
            {"purpose": "rebind"},
            {"target": "chan_other"},
            {"owner_user_id": "usr_other"},
            {"tenant_id": "tnt_other"},
            {"auth_session_id": "ses_other"},
            {"scan_ticket": "another-ticket"},
            {"payload": {"channel_type": PROVIDER, "display_name": "Other Bot",
                         "credentials": {"weixin_token": BOT_TOKEN}}},
            {"payload": {"channel_type": PROVIDER, "display_name": "My Bot",
                         "credentials": {"weixin_token": BOT_TOKEN + "-2"}}},
        ):
            args = dict(base)
            args.update(changed)
            self.assertNotEqual(so.receipt_key(**args), key,
                                f"key did not change for {sorted(changed)}")

        with self.assertRaises(so.ScanReceiptError) as ctx:
            so.receipt_key(**{**base, "scan_ticket": ""})
        self.assertEqual(ctx.exception.code, "bad_binding")

    def test_payload_normalization_is_order_and_none_insensitive_only(self):
        self.assertEqual(so.payload_digest({"b": "x", "a": 1}),
                         so.payload_digest({"a": 1, "b": "x"}))
        self.assertEqual(so.payload_digest({"a": 1, "b": None}),
                         so.payload_digest({"a": 1}))
        self.assertEqual(so.payload_digest({"a": " x "}),
                         so.payload_digest({"a": "x"}))
        # Internal spacing is meaningful: two different tokens must not fold.
        self.assertNotEqual(so.payload_digest({"a": "x  y"}),
                            so.payload_digest({"a": "x y"}))
        self.assertEqual(so.payload_digest(None), so.payload_digest({}))
        self.assertEqual(so.normalize_payload({"a": [1, 2]}), '{"a":[1,2]}')
        with self.assertRaises(so.ScanReceiptError) as ctx:
            so.payload_digest({"a": object()})
        self.assertEqual(ctx.exception.code, "bad_payload")
        deep: dict = {}
        cursor = deep
        for _ in range(so._MAX_PAYLOAD_DEPTH + 3):
            cursor["x"] = {}
            cursor = cursor["x"]
        with self.assertRaises(so.ScanReceiptError) as ctx:
            so.payload_digest(deep)
        self.assertEqual(ctx.exception.code, "bad_payload")

    def test_lookup_receipt_returns_only_non_sensitive_fields(self):
        with self.assertRaises(so.ScanReceiptError) as ctx:
            so.store_receipt("key-1", {"token": BOT_TOKEN})
        self.assertEqual(ctx.exception.code, "secret_in_receipt")
        with self.assertRaises(so.ScanReceiptError) as ctx:
            so.store_receipt("key-1", {"weixin_app_secret": "s"})
        self.assertEqual(ctx.exception.code, "secret_in_receipt")

        stored = so.store_receipt("key-1", {
            "instance_id": "chan_1",
            "saved": True,
            "secret_present": True,
            "not_a_declared_field": "dropped",
        })
        self.assertEqual(stored["receipt_state"], "complete")
        self.assertNotIn("not_a_declared_field", stored)
        found = so.lookup_receipt("key-1")
        self.assertEqual(found["instance_id"], "chan_1")
        self.assertTrue(found["secret_present"])
        self.assertNotIn("token", found)
        self.assertIsNone(so.lookup_receipt("no-such-key"))
        self.assertIsNone(so.lookup_receipt(""))
        # The 24h TTL is real, and an expired receipt is dropped on read.
        expired_at = time.time() + so.RECEIPT_TTL_SECONDS + 1
        self.assertIsNone(so.lookup_receipt("key-1", now=expired_at))
        self.assertIsNone(so.lookup_receipt("key-1"))

    def test_purging_receipts_neither_deletes_nor_revives_grants(self):
        outstanding = mint()
        redeemed = mint()
        so.store_receipt("key-1", {"instance_id": "chan_1", "saved": True})
        self.assertTrue(scan_authorization.consume(
            redeemed, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertEqual(so.purge_receipts(
            now=time.time() + so.RECEIPT_TTL_SECONDS + 1), 1)

        # The outstanding grant is untouched by the ledger's housekeeping...
        self.assertTrue(scan_authorization.verify(
            outstanding, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        # ...and the redeemed one stays redeemed: a purge cannot be a way to get
        # a second redemption out of a spent authorization.
        self.assertFalse(scan_authorization.verify(
            redeemed, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertFalse(scan_authorization.consume(
            redeemed, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertTrue(scan_authorization.consume(
            outstanding, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    # --- commit: the happy path and idempotency ----------------------------

    def _commit(self, session, ticket, create, quota=None, **overrides):
        kwargs = {
            "handle": session.handle,
            "actor": actor(),
            "scan_ticket": ticket,
            "channel_type": PROVIDER,
            "create_instance": create,
            **commit_kwargs(),
        }
        if quota is not None:
            kwargs["reserve_quota"] = quota.reserve
            kwargs["release_quota"] = quota.release
        kwargs.update(overrides)
        return so.commit_scan_binding(**kwargs)

    def test_commit_binds_the_create_to_the_session_not_the_client(self):
        session = start()
        ticket = mint()
        create = CreateStub(connected=True)
        quota = QuotaStub()
        audit: dict = {}

        result = self._commit(session, ticket, create, quota,
                              record_audit=lambda **kwargs: audit.update(kwargs))

        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(result["instance_id"], "chan_created_1")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["saved"])
        self.assertIs(result["connected"], True)
        self.assertTrue(result["secret_present"])
        self.assertTrue(result["authorization_consumed"])
        # The write is addressed by the server-bound binding, never by the
        # request: tenant, owner and actor all come from the session record.
        call = create.calls[0]
        self.assertEqual(call["tenant_id"], TENANT)
        self.assertEqual(call["actor_user_id"], OWNER)
        self.assertEqual(call["scan_ticket"], ticket)
        self.assertEqual(call["credentials"], {"weixin_token": BOT_TOKEN})
        self.assertFalse(call["allow_owner"])
        self.assertEqual(call["scope"], "tenant")
        # Quota, audit and the state machine all moved, in that order.
        self.assertEqual(quota.reserved[0]["tenant_id"], TENANT)
        self.assertEqual(audit["action"], "channel.scan.bind")
        self.assertEqual(audit["target"], "channel_instance:chan_created_1")
        self.assertEqual(audit["changes"]["outcome"], "committed")
        self.assertEqual(
            so.get_session(session.handle, actor=actor()).status, "committed")
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    def test_concurrent_submits_of_one_operation_commit_exactly_once(self):
        session = start()
        ticket = mint()
        create = CreateStub(delay=0.02)
        quota = QuotaStub()
        workers = 4
        barrier = threading.Barrier(workers)
        results: list = []
        errors: list = []

        def submit() -> None:
            barrier.wait()
            try:
                results.append(self._commit(
                    session, ticket, create, quota,
                    readback_guard=lambda readback: True))
            except Exception as error:  # noqa: BLE001 - surfaced by the asserts
                errors.append(error)

        threads = [threading.Thread(target=submit) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), workers)
        # One write, one reservation, one instance id handed to every caller.
        self.assertEqual(create.created, 1)
        self.assertEqual(len(quota.reserved), 1)
        self.assertEqual({item["instance_id"] for item in results},
                         {"chan_created_1"})
        self.assertEqual(sorted(item["outcome"] for item in results),
                         ["committed", "replayed", "replayed", "replayed"])
        for item in results:
            self.assertTrue(item["authorization_consumed"])

    def test_changed_content_or_binding_cannot_reuse_a_spent_authorization(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        self._commit(session, ticket, create, quota)

        # Same handle, same ticket, different content: a second create wearing
        # the first one's authorization.
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota,
                         display_name="Another Bot")
        self.assertEqual(ctx.exception.code, "authorization_refused")

        # Same ticket, a different session (different target): the grant is
        # bound to the session it was spent for.
        other = start(target="chan_wx_2")
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(other, ticket, create, quota)
        self.assertEqual(ctx.exception.code, "binding_mismatch")

        # A different authorization handle altogether: the spent grant cannot
        # be reached through it either.
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, mint(), create, quota)
        self.assertEqual(ctx.exception.code, "terminal")

        self.assertEqual(create.created, 1)

    def test_a_control_submit_with_a_fresh_grant_still_creates(self):
        first = start()
        create = CreateStub()
        quota = QuotaStub()
        self._commit(first, mint(), create, quota)
        second = start(target="chan_wx_2")
        result = self._commit(second, mint(), create, quota)
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(create.created, 2)

    # --- commit: failures keep the authorization usable --------------------

    def test_a_grant_for_another_channel_type_is_refused_and_stays_usable(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota, channel_type="feishu")
        self.assertEqual(ctx.exception.code, "authorization_refused")
        # A refusal before the write leaves no instance, no receipt and a session
        # the correct holder can still submit from.
        self.assertEqual(create.created, 0)
        self.assertIsNone(so.lookup_receipt(expected_key(ticket)))
        # The refusal happened before the state machine moved, so the session is
        # exactly where it started and the operator can submit again.
        self.assertEqual(
            so.get_session(session.handle, actor=actor()).status, "pending")
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

        result = self._commit(session, ticket, create, quota)
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(create.created, 1)

    def test_a_refused_write_keeps_the_authorization_and_the_retry_succeeds(self):
        session = start()
        ticket = mint()
        create = CreateStub(failures=1, error=RefusedWrite("display name exists"))
        quota = QuotaStub()

        with self.assertRaises(RefusedWrite):
            self._commit(session, ticket, create, quota)

        self.assertEqual(create.created, 0)
        # The grant survives a refusal that happened for another reason...
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        # ...nothing was receipted, the reservation was given back, and the
        # session is back to a state the operator can submit from.
        self.assertIsNone(so.lookup_receipt(expected_key(ticket)))
        self.assertEqual(quota.released, ["reservation-1"])
        self.assertEqual(
            so.get_session(session.handle, actor=actor()).status, "confirmed")

        result = self._commit(session, ticket, create, quota)
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(create.created, 1)
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    def test_a_quota_refusal_leaves_nothing_behind(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub(error=RuntimeError("tenant quota exhausted"))

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota)
        self.assertEqual(ctx.exception.code, "quota_refused")

        self.assertEqual(create.created, 0)
        self.assertEqual(create.calls, [])
        self.assertIsNone(so.lookup_receipt(expected_key(ticket)))
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertEqual(
            so.get_session(session.handle, actor=actor()).status, "confirmed")

    def test_an_unwired_quota_meter_is_refused_not_skipped(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create)
        self.assertEqual(ctx.exception.code, "quota_not_wired")
        self.assertEqual(create.created, 0)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

        # A deployment that genuinely has no meter says so explicitly.
        result = self._commit(session, ticket, create, quota_required=False)
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(create.created, 1)

    def test_a_failed_receipt_store_is_resumable_without_a_second_instance(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        key = expected_key(ticket)
        stored: list = []

        def flaky_sink(receipt_key_value, projection):
            stored.append(dict(projection))
            if len(stored) == 1:
                raise RuntimeError("shared ledger unavailable")

        with self.assertRaises(so.ScanReceiptError) as ctx:
            self._commit(session, ticket, create, quota,
                         receipt_sink=flaky_sink)
        self.assertEqual(ctx.exception.code, "receipt_store_failed")

        # The row committed, so it must not be written twice; the grant is
        # deliberately *not* consumed, because the operation is not finished and
        # the retry still has to be authorized.
        self.assertEqual(create.created, 1)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        staged = so.lookup_receipt(key)
        self.assertIsNotNone(staged)
        self.assertEqual(staged["receipt_state"], "staged")
        self.assertEqual(staged["instance_id"], "chan_created_1")
        self.assertNotIn(BOT_TOKEN, json.dumps(staged))

    def test_the_staged_tail_resumes_when_the_original_grant_is_still_live(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        key = expected_key(ticket)
        attempts: list = []

        def flaky_sink(receipt_key_value, projection):
            attempts.append(dict(projection))
            if len(attempts) == 1:
                raise RuntimeError("shared ledger unavailable")

        with self.assertRaises(so.ScanReceiptError):
            self._commit(session, ticket, create, quota,
                         receipt_sink=flaky_sink)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

        result = self._commit(session, ticket, create, quota,
                              receipt_sink=flaky_sink)
        self.assertEqual(result["outcome"], "resumed")
        self.assertEqual(result["instance_id"], "chan_created_1")
        # Still one instance, and now the authorization is redeemed.
        self.assertEqual(create.created, 1)
        self.assertEqual(so.lookup_receipt(key)["receipt_state"], "complete")
        self.assertEqual(result["authorization_consumed"], True)
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    def test_a_failed_audit_is_resumable_and_consumes_nothing(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        key = expected_key(ticket)
        audits: list = []

        def flaky_audit(**kwargs):
            audits.append(kwargs)
            if len(audits) == 1:
                raise RuntimeError("audit store unavailable")

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota,
                         record_audit=flaky_audit)
        self.assertEqual(ctx.exception.code, "audit_failed")
        self.assertEqual(create.created, 1)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertEqual(so.lookup_receipt(key)["receipt_state"], "staged")

        result = self._commit(session, ticket, create, quota,
                              record_audit=flaky_audit)
        self.assertEqual(result["outcome"], "resumed")
        self.assertEqual(create.created, 1)
        self.assertEqual(so.lookup_receipt(key)["receipt_state"], "complete")

    def test_a_tail_failure_resumes_even_after_the_create_redeemed_the_grant(self):
        """The real ordering: the create redeems the grant, the tail then fails.

        This is the ordering the WeChat route always has, because the delivered
        instance write consumes the ticket inside its own transaction. The tail
        (audit, receipt, session status) must therefore be finishable *after* the
        grant is spent — otherwise a committed instance would be answered with
        "authorization refused" and the operator would scan again for nothing.

        Reaching this branch at all still requires the exact handle, actor,
        auth session, tenant, target and content digest that staged the record
        (the receipt key binds all of them), and a resume creates nothing: it
        only finishes the tail of the operation that already committed.
        """
        session = start()
        ticket = mint()
        create = RedeemingCreateStub()
        quota = QuotaStub()
        key = expected_key(ticket)
        audits: list = []

        def flaky_audit(**kwargs):
            audits.append(kwargs)
            if len(audits) == 1:
                raise RuntimeError("audit store unavailable")

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota,
                         record_audit=flaky_audit)
        self.assertEqual(ctx.exception.code, "audit_failed")
        self.assertEqual(create.created, 1)
        # Spent — by the create, not by this module's own redemption step.
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertEqual(so.lookup_receipt(key)["receipt_state"], "staged")

        result = self._commit(session, ticket, create, quota,
                              record_audit=flaky_audit)
        self.assertEqual(result["outcome"], "resumed")
        self.assertEqual(result["instance_id"], "chan_created_1")
        self.assertEqual(create.created, 1, "a resume must not create a second row")
        self.assertTrue(result["authorization_consumed"])
        self.assertEqual(so.lookup_receipt(key)["receipt_state"], "complete")
        self.assertEqual(so.get_session(session.handle, actor=actor()).status,
                         "committed")

    def test_commit_refuses_unknown_and_terminal_handles(self):
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        with self.assertRaises(so.ScanSessionError) as ctx:
            so.commit_scan_binding(handle="no-such-handle", actor=actor(),
                                   scan_ticket=ticket, channel_type=PROVIDER,
                                   create_instance=create,
                                   reserve_quota=quota.reserve,
                                   **commit_kwargs())
        self.assertEqual(ctx.exception.code, "not_owner")

        session = start()
        so.mark_status(session.handle, "cancelled", actor=actor())
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota)
        self.assertEqual(ctx.exception.code, "terminal")
        self.assertEqual(create.created, 0)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    def test_commit_refuses_a_foreign_session(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        with self.assertRaises(so.ScanSessionError) as ctx:
            self._commit(session, ticket, create, QuotaStub(),
                         actor=actor(user_id="usr_other"))
        self.assertEqual(ctx.exception.code, "not_owner")
        self.assertEqual(create.created, 0)

    def test_personal_scope_uses_the_member_entry_point_binding(self):
        session = start(scope="personal")
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        result = self._commit(session, ticket, create, quota)

        call = create.calls[0]
        self.assertTrue(call["allow_owner"])
        self.assertEqual(call["scope"], "user")
        self.assertEqual(call["owner_user_id"], OWNER)
        self.assertEqual(result["scope"], "personal")

    # --- read-back ---------------------------------------------------------

    def test_readback_is_refused_when_the_binding_was_revoked(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        self._commit(session, ticket, create, quota)

        def revoked(readback: so.Readback) -> bool:
            # Stands in for "the instance is gone / governance-disabled": the
            # guard sees the stored result and refuses.
            return readback.result["instance_id"] != "chan_created_1"

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota,
                         readback_guard=revoked)
        self.assertEqual(ctx.exception.code, "readback_refused")
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota)
        self.assertEqual(ctx.exception.code, "readback_guard_missing")

        # The guard that can re-establish the binding lets the replay through.
        seen: list = []

        def allowed(readback: so.Readback) -> bool:
            seen.append(readback)
            return True

        result = self._commit(session, ticket, create, quota,
                              readback_guard=allowed)
        self.assertEqual(result["outcome"], "replayed")
        self.assertEqual(result["instance_id"], "chan_created_1")
        self.assertEqual(create.created, 1)
        self.assertEqual(seen[0].owner_user_id, OWNER)
        self.assertEqual(seen[0].auth_session_id, AUTH_SESSION)
        self.assertEqual(seen[0].channel_type, PROVIDER)

    def test_a_raising_readback_guard_refuses_the_replay(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        self._commit(session, ticket, create, quota)

        def broken(readback):
            raise RuntimeError("identity store unavailable")

        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, quota, readback_guard=broken)
        self.assertEqual(ctx.exception.code, "readback_refused")

    def test_a_receipt_outlives_the_session_that_started_it(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        result = self._commit(session, ticket, create, quota)

        later = time.time() + so.SESSION_TTL_SECONDS + 5
        self.assertEqual(so.purge_expired(now=later), 1)
        self.assertIsNone(so.get_session(session.handle, actor=actor(),
                                         now=later))
        # The replay is resolvable from the receipt alone: the QR session's 900s
        # lifetime must not cap the receipt's 24h one.
        replay = self._commit(session, ticket, create, quota,
                              readback_guard=lambda readback: True, now=later)
        self.assertEqual(replay["outcome"], "replayed")
        self.assertEqual(replay["instance_id"], result["instance_id"])
        self.assertEqual(create.created, 1)

    def test_an_expired_grant_does_not_take_the_receipt_with_it(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        result = self._commit(session, ticket, create, quota)
        key = result["receipt_key"]

        # The grant is gone (expired, or swept by a restart of the minting
        # process); the committed operation's receipt is unaffected.
        scan_authorization._reset()
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertIsNotNone(so.lookup_receipt(key))
        replay = self._commit(session, ticket, create, quota,
                              readback_guard=lambda readback: True)
        self.assertEqual(replay["outcome"], "replayed")
        self.assertEqual(create.created, 1)

    def test_an_expired_receipt_cannot_recreate_and_revives_nothing(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        quota = QuotaStub()
        result = self._commit(session, ticket, create, quota)
        key = result["receipt_key"]

        beyond = time.time() + so.RECEIPT_TTL_SECONDS + 1
        self.assertEqual(so.purge_receipts(now=beyond), 1)
        self.assertIsNone(so.lookup_receipt(key, now=beyond))
        # The spent authorization stays spent: dropping the receipt is not a way
        # to make the grant usable again.
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        self.assertFalse(scan_authorization.consume(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))
        # And the same request cannot be replayed into a second instance; it is
        # answered with an explicit refusal instead.
        with self.assertRaises(so.ScanOnboardingError) as ctx:
            self._commit(session, ticket, create, quota, now=beyond,
                         readback_guard=lambda readback: True)
        self.assertEqual(ctx.exception.code, "expired")
        self.assertEqual(create.created, 1)

    # --- secrets -----------------------------------------------------------

    def test_no_secret_reaches_the_result_the_receipt_or_the_logs(self):
        session = start()
        ticket = mint()
        create = CreateStub(extra={"token": BOT_TOKEN})
        quota = QuotaStub()
        with self.assertLogs("log", level="INFO") as captured:
            result = self._commit(session, ticket, create, quota)
            receipt = so.lookup_receipt(result["receipt_key"])

        self.assertTrue(result["secret_present"])
        self.assertIsNone(result["connected"])
        self.assertNotIn("token", result)
        self.assertNotIn("token", receipt)
        for surface in (result, receipt):
            self.assertNotIn(BOT_TOKEN, json.dumps(surface))
        logged = "\n".join(record.getMessage() for record in captured.records)
        self.assertNotIn(BOT_TOKEN, logged)
        self.assertNotIn("token", logged)

    def test_a_projection_carrying_a_credential_value_is_refused_resumably(self):
        session = start()
        ticket = mint()
        quota = QuotaStub()
        key = expected_key(ticket)
        # The create answers with the token in a declared field: storing that
        # would put a long-term secret in the ledger.
        bad = CreateStub(extra={"display_name": BOT_TOKEN})
        with self.assertRaises(so.ScanReceiptError) as ctx:
            self._commit(session, ticket, bad, quota)
        self.assertEqual(ctx.exception.code, "secret_in_receipt")
        self.assertEqual(bad.created, 1)

        journal = so.lookup_receipt(key)
        self.assertEqual(journal["receipt_state"], "staged")
        self.assertEqual(journal["instance_id"], "chan_created_1")
        self.assertNotIn(BOT_TOKEN, json.dumps(journal))
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

        # The retry resumes the journaled commit instead of creating again, and
        # the journal itself never carried the secret.
        good = CreateStub()
        result = self._commit(session, ticket, good, quota)
        self.assertEqual(result["outcome"], "resumed")
        self.assertEqual(good.created, 0)
        self.assertEqual(bad.created, 1)

    def test_a_secret_in_a_request_field_is_refused_before_the_write(self):
        session = start()
        ticket = mint()
        create = CreateStub()
        with self.assertRaises(so.ScanCommitError) as ctx:
            self._commit(session, ticket, create, QuotaStub(),
                         display_name=BOT_TOKEN)
        self.assertEqual(ctx.exception.code, "secret_in_request")
        self.assertEqual(create.created, 0)
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id=OWNER, tenant_id=TENANT,
            channel_type=PROVIDER))

    def test_a_failed_step_leaves_no_secret_in_the_session_error(self):
        session = start()
        ticket = mint()
        create = CreateStub(failures=1, error=RefusedWrite(BOT_TOKEN))
        with self.assertRaises(RefusedWrite) as ctx:
            with self.assertLogs("log", level="INFO") as captured:
                self._commit(session, ticket, create, QuotaStub())
        # The refusal tag recorded on the session is a type and a code, never the
        # lower layer's message (which this module cannot prove is secret-free).
        recorded = so.get_session(session.handle, actor=actor())
        self.assertNotIn(BOT_TOKEN, recorded.error)
        self.assertEqual(recorded.error, "RefusedWrite:conflict")
        logged = "\n".join(record.getMessage() for record in captured.records)
        self.assertNotIn(BOT_TOKEN, logged)
        self.assertEqual(str(ctx.exception), BOT_TOKEN)


if __name__ == "__main__":
    unittest.main()
