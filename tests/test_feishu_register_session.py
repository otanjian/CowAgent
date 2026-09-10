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


if __name__ == "__main__":
    unittest.main()
