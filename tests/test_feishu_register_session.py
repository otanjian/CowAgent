# encoding:utf-8
"""Feishu one-click register sessions are bound to their initiator (P0).

A register session holds a freshly created Feishu app's ``app_id``/``app_secret``.
In a multi-tenant, multi-user deployment the session state must therefore belong
to the identity that started it:

* only the initiator may read the session — another identity gets nothing and
  cannot even tell whether the session exists;
* starting a new session must not cancel somebody else's;
* the credentials are handed out once and then forgotten.

These tests exercise the session store directly: the SDK worker is the only part
that needs the network, and it is deliberately kept out of the store.
"""

import json
import unittest

from channel.web.web_channel import FeishuRegisterHandler as H


class FeishuRegisterSessionTests(unittest.TestCase):
    def setUp(self):
        H._reset_sessions()

    # --- 2.1 ownership ---------------------------------------------------

    def test_only_the_initiator_can_read_the_session(self):
        handle = H._create_session("user-a", "tenant-1")
        self.assertIsNone(H._session_for(handle, "user-b", "tenant-1"))
        self.assertIsNotNone(H._session_for(handle, "user-a", "tenant-1"))

    def test_the_owning_tenant_is_part_of_the_identity(self):
        handle = H._create_session("user-a", "tenant-1")
        # Same user id, different tenant: a different identity, not the owner.
        self.assertIsNone(H._session_for(handle, "user-a", "tenant-2"))

    def test_an_unknown_handle_is_indistinguishable_from_a_foreign_one(self):
        handle = H._create_session("user-a", "tenant-1")
        foreign = H._poll_payload(handle, "user-b", "tenant-1")
        unknown = H._poll_payload("no-such-handle", "user-b", "tenant-1")
        self.assertEqual(foreign, unknown)
        self.assertNotIn("app_secret", foreign)
        self.assertNotIn("app_id", foreign)

    def test_handles_are_opaque_and_unique(self):
        first = H._create_session("user-a", "tenant-1")
        second = H._create_session("user-a", "tenant-1")
        self.assertNotEqual(first, second)
        # Long enough that it cannot be guessed by enumeration.
        self.assertGreaterEqual(len(first), 32)

    # --- 2.2 concurrent sessions ----------------------------------------

    def test_new_session_does_not_cancel_another_identity(self):
        a = H._create_session("user-a", "tenant-1")
        b = H._create_session("user-b", "tenant-1")
        session_a = H._session_for(a, "user-a", "tenant-1")
        self.assertIsNotNone(session_a, "A's session was dropped by B's start")
        self.assertFalse(session_a["cancel_event"].is_set(),
                         "B's start cancelled A's in-flight registration")
        self.assertIsNotNone(H._session_for(b, "user-b", "tenant-1"))

    def test_new_session_replaces_the_same_identitys_previous_one(self):
        a1 = H._create_session("user-a", "tenant-1")
        old = H._session_for(a1, "user-a", "tenant-1")
        a2 = H._create_session("user-a", "tenant-1")
        # The superseded session stops polling, and the new one is live.
        self.assertTrue(old["cancel_event"].is_set())
        self.assertIsNotNone(H._session_for(a2, "user-a", "tenant-1"))
        self.assertIsNone(H._session_for(a1, "user-a", "tenant-1"))

    # --- 2.3 credentials are handed out once -----------------------------

    def test_credentials_are_returned_once_and_then_forgotten(self):
        handle = H._create_session("user-a", "tenant-1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")

        first = H._poll_payload(handle, "user-a", "tenant-1")
        self.assertEqual(first["register_status"], "done")
        self.assertEqual(first["app_id"], "cli_abc")
        self.assertEqual(first["app_secret"], "s3cr3t")

        second = H._poll_payload(handle, "user-a", "tenant-1")
        self.assertNotIn("app_secret", second)
        self.assertNotIn("app_id", second)

    # --- 2.4 terminal states --------------------------------------------

    def test_terminal_states_carry_no_credentials(self):
        for status in ("error", "expired", "denied"):
            handle = H._create_session("user-a", "tenant-1")
            H._set_status(handle, status, app_id="cli_x", app_secret="leak",
                          error="boom")
            payload = H._poll_payload(handle, "user-a", "tenant-1")
            self.assertEqual(payload["register_status"], status, status)
            self.assertNotIn("app_secret", payload)
            self.assertNotIn("app_id", payload)

    def test_pending_session_reports_progress_without_credentials(self):
        handle = H._create_session("user-a", "tenant-1")
        # 与 SDK worker 的真实写入一致：会话内部字段为 ``url``，应答键为 ``qrcode_url``。
        H._set_status(handle, "pending", url="https://qr",
                      qr_image="data:image/png;base64,x")
        payload = H._poll_payload(handle, "user-a", "tenant-1")
        self.assertEqual(payload["register_status"], "pending")
        self.assertEqual(payload["qrcode_url"], "https://qr")
        self.assertNotIn("app_secret", payload)

    # --- 2.9 no plaintext escape ----------------------------------------

    def test_foreign_poll_never_carries_the_secret_in_its_body(self):
        handle = H._create_session("user-a", "tenant-1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t-value")

        foreign_body = json.dumps(H._poll_payload(handle, "user-b", "tenant-1"))
        self.assertNotIn("s3cr3t-value", foreign_body)
        self.assertNotIn("cli_abc", foreign_body)

        # The owner still receives it, and only once.
        owner_body = json.dumps(H._poll_payload(handle, "user-a", "tenant-1"))
        self.assertIn("s3cr3t-value", owner_body)

    def test_no_register_log_line_carries_the_secret(self):
        """A secret must never reach the log file, so no log call may name it."""
        import inspect
        source = inspect.getsource(H)
        offenders = [
            line.strip() for line in source.splitlines()
            if "logger." in line
            and ("app_secret" in line or "client_secret" in line)
        ]
        self.assertEqual(offenders, [])

    # --- 3.13 a completed scan also authorizes the create it leads to ----

    def test_a_completed_scan_mints_a_grant_the_console_can_redeem(self):
        """The console auto-persists on success and shows no password prompt.

        The grant is the only proof of presence that create carries, so it has
        to be minted for the identity that ran the scan — and be usable.
        """
        from auth import scan_authorization

        handle = H._create_session("user-a", "tenant-1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")
        payload = H._poll_payload(handle, "user-a", "tenant-1")

        ticket = payload.get("scan_ticket", "")
        self.assertTrue(ticket, "a successful scan must hand back a grant")
        self.assertTrue(
            scan_authorization.verify(
                ticket, actor_user_id="user-a", tenant_id="tenant-1",
                channel_type="feishu"),
            "the grant does not fit the identity that ran the scan")
        self.assertFalse(
            scan_authorization.verify(
                ticket, actor_user_id="user-b", tenant_id="tenant-1",
                channel_type="feishu"),
            "the grant is not bound to its initiator")

    def test_terminal_and_pending_states_mint_no_grant(self):
        # Only a completed scan proves presence; an abandoned one must not
        # leave an authorization behind.
        from auth import scan_authorization

        for status in ("error", "expired", "denied", "pending"):
            handle = H._create_session("user-a", "tenant-1")
            H._set_status(handle, status, app_id="cli_x", app_secret="sec")
            payload = H._poll_payload(handle, "user-a", "tenant-1")
            self.assertNotIn("scan_ticket", payload, status)

    def test_dropping_the_sessions_drops_their_grants(self):
        from auth import scan_authorization

        handle = H._create_session("user-a", "tenant-1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")
        ticket = H._poll_payload(handle, "user-a", "tenant-1")["scan_ticket"]

        H._reset_sessions()
        self.assertFalse(
            scan_authorization.verify(
                ticket, actor_user_id="user-a", tenant_id="tenant-1",
                channel_type="feishu"),
            "a grant outlived the session that justified it")

    # --- 4.1 the public and personal consoles scan independently ---------

    def test_starting_a_public_scan_does_not_cancel_a_personal_one(self):
        """One member, two consoles, two live registrations.

        The personal workbench and the public page are different writes against
        the same provider, so starting one must not tear down the other's QR —
        otherwise opening the other console silently kills the scan in flight.
        """
        public = H._create_session("user-a", "tenant-1", scope="tenant")
        personal = H._create_session(
            "user-a", "tenant-1", scope="personal", agent_id="alice-own")
        self.assertNotEqual(public, personal)

        public_session = H._session_for(public, "user-a", "tenant-1")
        self.assertIsNotNone(public_session)
        self.assertFalse(public_session["cancel_event"].is_set(),
                         "the personal scan cancelled the public one")
        self.assertIsNotNone(
            H._session_for(personal, "user-a", "tenant-1"))
        # ...and the reverse: starting the public one again leaves the personal
        # one alone.
        H._create_session("user-a", "tenant-1", scope="tenant")
        personal_session = H._session_for(personal, "user-a", "tenant-1")
        self.assertIsNotNone(personal_session)
        self.assertFalse(personal_session["cancel_event"].is_set())

    def test_the_same_surface_and_target_still_replaces_its_own_scan(self):
        first = H._create_session("user-a", "tenant-1", scope="personal",
                                  agent_id="alice-own")
        H._create_session("user-a", "tenant-1", scope="personal",
                          agent_id="alice-other")
        # A different target is a different scan (changing the target restarts
        # the flow), while the same target is a restart of the same one.
        self.assertIsNotNone(
            H._session_for(first, "user-a", "tenant-1"))
        again = H._create_session("user-a", "tenant-1", scope="personal",
                                  agent_id="alice-own")
        self.assertIsNotNone(H._session_for(again, "user-a", "tenant-1"))
        self.assertIsNone(H._session_for(first, "user-a", "tenant-1"))

    def test_a_handle_from_another_login_session_is_not_readable(self):
        handle = H._create_session("user-a", "tenant-1", scope="personal",
                                   agent_id="alice-own",
                                   auth_session_id="ses_1")
        self.assertIsNotNone(
            H._session_for(handle, "user-a", "tenant-1", "ses_1"))
        # Same account, same tenant, same target — a *different* login. The
        # handle was started by one session and is not that other session's to
        # read, and the refusal is indistinguishable from an unknown handle.
        self.assertIsNone(
            H._session_for(handle, "user-a", "tenant-1", "ses_2"))
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")
        self.assertEqual(
            H._poll_payload(handle, "user-a", "tenant-1", "ses_2"),
            H._poll_payload("no-such-handle", "user-a", "tenant-1", "ses_2"))

    def test_a_personal_scan_binds_its_grant_to_scope_target_and_session(self):
        from auth import scan_authorization

        handle = H._create_session("user-a", "tenant-1", scope="personal",
                                   agent_id="alice-own",
                                   auth_session_id="ses_1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")
        payload = H._poll_payload(handle, "user-a", "tenant-1", "ses_1")

        # The surface and the target travel back with the credentials: the
        # console has to send them with the create, and the create cannot be
        # assembled without knowing which Agent the operator picked.
        self.assertEqual(payload["scope"], "personal")
        self.assertEqual(payload["agent_id"], "alice-own")

        ticket = payload["scan_ticket"]
        binding = {"actor_user_id": "user-a", "tenant_id": "tenant-1",
                   "channel_type": "feishu", "scope": "personal",
                   "agent_id": "alice-own", "auth_session_id": "ses_1"}
        self.assertTrue(scan_authorization.verify(ticket, **binding))
        # Neither the public console nor another target nor another login can
        # spend it.
        for changed in (
            {"scope": "tenant", "agent_id": ""},
            {"agent_id": "alice-other"},
            {"auth_session_id": "ses_2"},
            {"actor_user_id": "user-b"},
            {"tenant_id": "tenant-2"},
        ):
            probe = dict(binding)
            probe.update(changed)
            self.assertFalse(scan_authorization.verify(ticket, **probe),
                             f"grant accepted for {sorted(changed)}")

    def test_a_public_scan_binds_its_grant_to_the_tenant_surface(self):
        from auth import scan_authorization

        handle = H._create_session("user-a", "tenant-1", scope="tenant",
                                   auth_session_id="ses_1")
        H._set_status(handle, "done", app_id="cli_abc", app_secret="s3cr3t")
        payload = H._poll_payload(handle, "user-a", "tenant-1", "ses_1")

        self.assertEqual(payload["scope"], "tenant")
        self.assertEqual(payload["agent_id"], "")
        ticket = payload["scan_ticket"]
        self.assertTrue(scan_authorization.verify(
            ticket, actor_user_id="user-a", tenant_id="tenant-1",
            channel_type="feishu", scope="tenant", auth_session_id="ses_1"))
        # A historical public authorization must not create a personal
        # instance: the scope the write declares is not the one it was minted
        # for, so it is refused before anything is consumed.
        self.assertFalse(scan_authorization.verify(
            ticket, actor_user_id="user-a", tenant_id="tenant-1",
            channel_type="feishu", scope="personal", agent_id="alice-own",
            auth_session_id="ses_1"))
        self.assertFalse(scan_authorization.consume(
            ticket, actor_user_id="user-a", tenant_id="tenant-1",
            channel_type="feishu", scope="personal", agent_id="alice-own",
            auth_session_id="ses_1"))
        self.assertTrue(scan_authorization.consume(
            ticket, actor_user_id="user-a", tenant_id="tenant-1",
            channel_type="feishu", scope="tenant", auth_session_id="ses_1"))


if __name__ == "__main__":
    unittest.main()
