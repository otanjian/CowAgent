# encoding:utf-8
"""Route-level tests for the tenant-owned channel endpoints.

Change ``tenant-owned-message-channels`` exposes one tenant-scoped surface
(``/api/tenant/channels``) for a tenant administrator to list, create, edit and
enable/disable *its own* channel instances, while the instance-level
``/api/channels`` page stays platform-domain.

These exercise the handler wiring without a live server: the service points at
a temp identity.db and ``identity_mode`` is patched to "database".
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from channel import channel_instances
from channel.web import web_channel, auth_handlers, admin_handlers
from auth.service import IdentityService

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

FEISHU = {
    "feishu_app_id": "cli_acme",
    "feishu_app_secret": "acme-app-secret",
    "feishu_bot_name": "Acme Bot",
}


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _ChannelHttpFixture(unittest.TestCase):
    """Two tenants (acme/globex), an admin each, plus a plain member."""

    def setUp(self):
        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_key)

        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)

        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        self.svc.bind_agent(tenant_id=self.tb, agent_id="agent-b",
                            private_owner_user_id=None)

        # A plain (non-admin) member of acme.
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username="acmemember",
            display_name="Acme Member", temporary_password="Str0ngMember1",
            roles=["member"])
        self.svc.change_password(
            self.svc.login("acmemember", "Str0ngMember1").token,
            "Str0ngMember1", "Str0ngMemberFinal")

        self.token_a = self.svc.login("root", "Str0ngRootFinal").token
        self.token_b = self.svc.login("globexadmin", "Str0ngGlobexFinal").token
        self.token_member = self.svc.login("acmemember", "Str0ngMemberFinal").token

    def _restore_key(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    def _app(self):
        return web.application(
            (
                "/api/tenant/channels", "TenantChannelsHandler",
                "/api/tenant/channels/([^/]+)/active", "TenantChannelActiveHandler",
                "/api/tenant/channels/([^/]+)", "TenantChannelHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="GET", payload=None, token=None, tenant=None):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
            # Management writes are origin/CSRF-checked: a cookie write must
            # present a same-origin pair (Host + matching Origin).
            headers["Host"] = "test"
            headers["Origin"] = "http://test"
        if tenant:
            headers["X-Tenant-ID"] = tenant
        kwargs["headers"] = headers

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_is_database", lambda: True), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            return self._app().request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _assert_status(self, resp, code):
        """Handler errors raise ``web.HTTPError(str(status))``, so the status is
        a bare number (e.g. "403"), not the "403 Forbidden" phrase the policy
        processor emits."""
        self.assertTrue(str(resp.status).startswith(str(code)),
                        f"expected {code}, got {resp.status}: {resp.data}")

    def _create(self, payload, token=None, tenant=None):
        return self._request("/api/tenant/channels", method="POST",
                             payload=payload, token=token, tenant=tenant)

    def _create_ok(self, **over):
        payload = {
            "channel_type": "feishu",
            "display_name": "Acme Bot",
            "agent_id": "agent-a",
            "credentials": dict(FEISHU),
            "recent_password": "Str0ngRootFinal",
        }
        payload.update(over)
        resp = self._create(payload, token=self.token_a, tenant=self.ta)
        assert resp.status == "200 OK", resp.data
        return self._json(resp)["instance"]


class DatabaseModeGateTests(_ChannelHttpFixture):
    """The capability exists only in database mode (10.7).

    Legacy mode has no identity.db, so no tenant, no credential store and no
    audit trail: the endpoints must refuse rather than half-work. This pins the
    "no feature flag, gated by deployment mode" decision.
    """

    def _legacy_request(self, path, method="GET", payload=None):
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload)
        else:
            data = None
        headers["Cookie"] = f"cow_session={self.token_a}"
        headers["X-Tenant-ID"] = self.ta
        headers["Host"] = "test"
        headers["Origin"] = "http://test"
        kwargs = {"method": method, "headers": headers}
        if data is not None:
            kwargs["data"] = data

        with patch.object(auth_handlers, "_is_database", lambda: False), \
                patch.object(admin_handlers, "_is_database", lambda: False):
            return self._app().request(path, **kwargs)

    def test_list_is_refused_in_legacy_mode(self):
        resp = self._legacy_request("/api/tenant/channels")
        self.assertTrue(str(resp.status).startswith("400"), resp.status)
        self.assertEqual(self._json(resp).get("code"), "not_database")

    def test_create_is_refused_in_legacy_mode(self):
        resp = self._legacy_request("/api/tenant/channels", method="POST", payload={
            "channel_type": "feishu", "display_name": "X", "agent_id": "",
            "credentials": dict(FEISHU), "recent_password": "Str0ngRootFinal"})
        self.assertTrue(str(resp.status).startswith("400"), resp.status)
        self.assertEqual(self._json(resp).get("code"), "not_database")


class GetTenantChannelTypesTests(_ChannelHttpFixture):
    """The form's type/field contract must equal the server's creation gate."""

    def _list(self, token=None, tenant=None):
        return self._json(self._request("/api/tenant/channels",
                                        token=token or self.token_a,
                                        tenant=tenant or self.ta))

    def test_the_form_contract_offers_feishu_and_withholds_wechatcom_app(self):
        body = self._list()
        types = {t["channel_type"] for t in body["channel_types"]}
        self.assertIn("feishu", types)
        self.assertNotIn("wechatcom_app", types)

    def test_every_offered_type_is_actually_creatable(self):
        """A type the form offers but the server rejects would be a dead end."""
        body = self._list()
        self.assertTrue(body["channel_types"])
        for entry in body["channel_types"]:
            self.assertTrue(entry["credential_fields"], entry)
            credentials = {}
            for field in entry["credential_fields"]:
                self.assertTrue(field["key"])
                self.assertIn("label", field)
                credentials[field["key"]] = "value"
            resp = self._create({
                "channel_type": entry["channel_type"],
                "display_name": f"Bot {entry['channel_type']}",
                "agent_id": "agent-a",
                "credentials": credentials,
                "recent_password": "Str0ngRootFinal",
            }, token=self.token_a, tenant=self.ta)
            self.assertEqual(resp.status, "200 OK",
                             f"{entry['channel_type']}: {resp.data}")

    def test_secret_looking_fields_are_marked_secret(self):
        body = self._list()
        feishu = [t for t in body["channel_types"]
                  if t["channel_type"] == "feishu"][0]
        by_key = {f["key"]: f for f in feishu["credential_fields"]}
        self.assertTrue(by_key["feishu_app_secret"]["secret"])
        self.assertFalse(by_key["feishu_app_id"]["secret"])

    def test_the_list_response_carries_no_plaintext_credentials(self):
        self._create_ok()
        listed = self._list()
        raw = json.dumps(listed)
        self.assertNotIn("acme-app-secret", raw)
        # The contract names the fields (so the form can render them) but the
        # instance projection must not carry a value for any of them.
        for item in listed["items"]:
            for forbidden in ("credentials", "ciphertext", "secret", "token"):
                self.assertNotIn(forbidden, item)


class ListTests(_ChannelHttpFixture):

    def test_list_is_empty_before_anything_is_created(self):
        resp = self._request("/api/tenant/channels", token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK")
        self.assertEqual(self._json(resp)["items"], [])

    def test_list_returns_created_instances(self):
        created = self._create_ok()
        resp = self._request("/api/tenant/channels", token=self.token_a, tenant=self.ta)
        body = self._json(resp)
        self.assertEqual([i["id"] for i in body["items"]], [created["id"]])

    def test_list_never_carries_plaintext_credentials(self):
        self._create_ok()
        resp = self._request("/api/tenant/channels", token=self.token_a, tenant=self.ta)
        raw = resp.data.decode("utf-8")
        # The field *contract* legitimately names the keys; no VALUE may appear.
        self.assertNotIn("acme-app-secret", raw)
        for item in self._json(resp)["items"]:
            for forbidden in ("credentials", "ciphertext", "secret", "token"):
                self.assertNotIn(forbidden, item)

    def test_a_plain_member_is_denied(self):
        resp = self._request("/api/tenant/channels", token=self.token_member,
                             tenant=self.ta)
        self._assert_status(resp, 403)

    def test_anonymous_is_denied(self):
        resp = self._request("/api/tenant/channels", tenant=self.ta)
        self.assertTrue(str(resp.status).startswith("401"), resp.status)


class CreateTests(_ChannelHttpFixture):

    def test_create_returns_a_masked_projection_and_a_version(self):
        created = self._create_ok()
        self.assertEqual(created["channel_type"], "feishu")
        self.assertEqual(created["display_name"], "Acme Bot")
        self.assertEqual(created["agent_id"], "agent-a")
        self.assertTrue(created["active"])
        self.assertGreaterEqual(created["version"], 1)

    def test_create_requires_the_recent_password(self):
        resp = self._create({
            "channel_type": "feishu", "display_name": "Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU),
            "recent_password": "not-the-password",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 401)
        self.assertIn("invalid_old", resp.data.decode("utf-8"))

    def test_a_duplicate_display_name_conflicts(self):
        self._create_ok()
        resp = self._create({
            "channel_type": "feishu", "display_name": "Acme Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU),
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 409)

    def test_another_tenants_agent_is_rejected(self):
        resp = self._create({
            "channel_type": "feishu", "display_name": "Bot",
            "agent_id": "agent-b", "credentials": dict(FEISHU),
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 403)
        # The message must not reveal which tenant the Agent belongs to.
        self.assertNotIn("globex", resp.data.decode("utf-8").lower())

    def test_an_unready_channel_type_is_rejected(self):
        resp = self._create({
            "channel_type": "wechatcom_app", "display_name": "WeCom",
            "agent_id": "agent-a",
            "credentials": {"wechatcom_corp_id": "corp"},
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 400)

    def test_a_blank_credential_field_is_rejected(self):
        # Every field that IS sent must be non-empty: an empty secret would be
        # stored as a real-looking credential that silently cannot authenticate.
        resp = self._create({
            "channel_type": "feishu", "display_name": "Bot",
            "agent_id": "agent-a",
            "credentials": dict(FEISHU, feishu_app_secret="   "),
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 400)

    def test_a_plain_member_cannot_create(self):
        resp = self._create({
            "channel_type": "feishu", "display_name": "Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU),
            "recent_password": "Str0ngMemberFinal",
        }, token=self.token_member, tenant=self.ta)
        self._assert_status(resp, 403)

    def test_an_unknown_field_is_rejected_rather_than_stored_inert(self):
        resp = self._create({
            "channel_type": "feishu", "display_name": "Bot",
            "agent_id": "agent-a",
            "credentials": dict(FEISHU, feishu_app_secret_typo="x"),
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 400)


class UpdateTests(_ChannelHttpFixture):

    def test_edit_bumps_the_version(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}", method="POST",
            payload={"display_name": "Acme Renamed",
                     "expected_version": created["version"],
                     "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK")
        updated = self._json(resp)["instance"]
        self.assertEqual(updated["display_name"], "Acme Renamed")
        self.assertGreater(updated["version"], created["version"])

    def test_a_stale_expected_version_conflicts(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}", method="POST",
            payload={"display_name": "Renamed", "expected_version": 0,
                     "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 409)

    def test_rotation_keeps_a_single_masked_instance(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}", method="POST",
            payload={"credentials": dict(FEISHU, feishu_app_secret="rotated"),
                     "expected_version": created["version"],
                     "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK")
        self.assertNotIn("rotated", resp.data.decode("utf-8"))

    def test_another_tenant_cannot_edit_this_instance(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}", method="POST",
            payload={"display_name": "Stolen", "expected_version": created["version"],
                     "recent_password": "Str0ngGlobexFinal"},
            token=self.token_b, tenant=self.tb)
        self.assertTrue(str(resp.status).startswith(("403", "404")), resp.status)


class SetActiveTests(_ChannelHttpFixture):

    def test_disable_then_the_list_still_shows_the_instance(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}/active", method="POST",
            payload={"active": False, "expected_version": created["version"],
                     "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK")
        self.assertFalse(self._json(resp)["instance"]["active"])

        listing = self._json(self._request("/api/tenant/channels",
                                           token=self.token_a, tenant=self.ta))
        self.assertEqual(len(listing["items"]), 1)
        self.assertFalse(listing["items"][0]["active"])

    def test_disable_requires_the_recent_password(self):
        created = self._create_ok()
        resp = self._request(
            f"/api/tenant/channels/{created['id']}/active", method="POST",
            payload={"active": False, "expected_version": created["version"],
                     "recent_password": "nope"},
            token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 401)

    def test_a_display_name_freed_by_disabling_can_be_reused(self):
        created = self._create_ok()
        self._request(
            f"/api/tenant/channels/{created['id']}/active", method="POST",
            payload={"active": False, "expected_version": created["version"],
                     "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        resp = self._create({
            "channel_type": "feishu", "display_name": "Acme Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU),
            "recent_password": "Str0ngRootFinal",
        }, token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK")


class RuntimeApplyOnWriteTests(_ChannelHttpFixture):
    """A save takes effect now, and says so when it could not.

    Change ``scan-onboarding-and-inbound-anchor`` group 4: the tenant write
    contract stays the masked projection, plus a ``runtime`` report the console
    renders as applied / not-applied-yet with a reason. Reporting the failure is
    the point — the credential write has already committed, so a runtime problem
    must never be turned into a failed save, and must never be hidden either.
    """

    def _recorder(self, outcome=None, raises=None):
        calls = []

        def _apply(instance_id):
            calls.append(instance_id)
            if raises is not None:
                raise raises
            return dict(outcome or {"applied": True, "pending": False, "error": ""})

        return calls, patch.object(
            channel_instances, "apply_tenant_instance_runtime", _apply)

    def _create_payload(self):
        return {
            "channel_type": "feishu", "display_name": "Acme Bot",
            "agent_id": "agent-a", "credentials": dict(FEISHU),
            "recent_password": "Str0ngRootFinal",
        }

    def test_create_puts_the_new_instance_into_service_now(self):
        calls, patched = self._recorder()
        with patched:
            resp = self._create(self._create_payload(),
                                token=self.token_a, tenant=self.ta)
        body = self._json(resp)
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(calls, [body["instance"]["id"]],
                         "a create must bring the instance up on its own credentials")
        self.assertTrue(body["runtime"]["applied"])

    def test_a_failed_apply_is_reported_and_the_save_still_succeeds(self):
        calls, patched = self._recorder(
            {"applied": False, "pending": False, "error": "invalid app secret"})
        with patched:
            resp = self._create(self._create_payload(),
                                token=self.token_a, tenant=self.ta)
        body = self._json(resp)
        self.assertEqual(resp.status, "200 OK",
                         "the credential write already committed")
        self.assertEqual(calls, [body["instance"]["id"]])
        self.assertFalse(body["runtime"]["applied"])
        self.assertIn("invalid app secret", body["runtime"]["error"])

    def test_a_runtime_explosion_cannot_fail_a_committed_save(self):
        calls, patched = self._recorder(raises=RuntimeError("boom"))
        with patched:
            resp = self._create(self._create_payload(),
                                token=self.token_a, tenant=self.ta)
        body = self._json(resp)
        self.assertEqual(resp.status, "200 OK",
                         "a runtime fault must not fail a saved instance")
        self.assertIn("instance", body)
        self.assertFalse(body["runtime"]["applied"])
        self.assertTrue(body["runtime"]["error"], "the fault must be diagnosable")

    def test_edit_puts_the_new_credentials_into_service_now(self):
        created = self._create_ok()
        calls, patched = self._recorder()
        with patched:
            resp = self._request(
                f"/api/tenant/channels/{created['id']}", method="POST",
                payload={"expected_version": created["version"],
                         "credentials": {"feishu_app_secret": "rotated-secret"},
                         "recent_password": "Str0ngRootFinal"},
                token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(calls, [created["id"]],
                         "rotated credentials must reach the running channel")

    def test_disabling_puts_the_instance_out_of_service_now(self):
        created = self._create_ok()
        calls, patched = self._recorder()
        with patched:
            resp = self._request(
                f"/api/tenant/channels/{created['id']}/active", method="POST",
                payload={"active": False, "expected_version": created["version"],
                         "recent_password": "Str0ngRootFinal"},
                token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(calls, [created["id"]],
                         "disabling is the rollback path and must take effect now")


class ScanGrantCreateTests(_ChannelHttpFixture):
    """A completed scan authorizes the create it leads to (auto-persist).

    The console redeems the grant itself: a scan that reports success is
    followed by a create with no password prompt, so the grant — minted by the
    server when the scan finished, bound to the operator who ran it — is the
    only proof of presence the write carries. These pin that it is accepted,
    that it is good for exactly one create, and that it cannot be stretched
    onto another tenant or another vendor.
    """

    def setUp(self):
        super().setUp()
        from auth import scan_authorization
        scan_authorization._reset()
        self.addCleanup(scan_authorization._reset)
        self._grant = scan_authorization

    def _grant_for(self, tenant, channel_type="feishu", actor=None):
        return self._grant.mint(
            actor_user_id=actor or self.root["id"], tenant_id=tenant,
            channel_type=channel_type)

    def _create_with_grant(self, ticket, **over):
        payload = {
            "channel_type": "feishu",
            "display_name": "Feishu · dd23",
            "agent_id": "agent-a",
            "credentials": dict(FEISHU),
            "scan_ticket": ticket,
        }
        payload.update(over)
        return self._create(payload, token=self.token_a, tenant=self.ta)

    def test_a_scan_grant_stands_in_for_the_password(self):
        # No ``recent_password`` at all: this is the auto-persist path, where
        # the operator never sees a prompt.
        resp = self._create_with_grant(self._grant_for(self.ta))
        self.assertEqual(resp.status, "200 OK", resp.data)
        body = self._json(resp)
        self.assertEqual(body["instance"]["display_name"], "Feishu · dd23")

    def test_a_scan_grant_is_redeemed_exactly_once(self):
        ticket = self._grant_for(self.ta)
        self.assertEqual(
            self._create_with_grant(ticket).status, "200 OK")
        second = self._create_with_grant(ticket, display_name="Second")
        self._assert_status(second, 401)

    def test_a_scan_grant_for_another_tenant_is_refused(self):
        resp = self._create_with_grant(self._grant_for(self.tb))
        self._assert_status(resp, 401)

    def test_a_scan_grant_for_another_vendor_is_refused(self):
        resp = self._create_with_grant(self._grant_for(self.ta, "wecom_bot"))
        self._assert_status(resp, 401)

    def test_a_create_with_neither_password_nor_grant_is_refused(self):
        resp = self._create_with_grant("")
        self._assert_status(resp, 401)

    def test_a_create_refused_for_another_reason_keeps_the_grant(self):
        # A duplicate name is refused after authorization; the operator must not
        # have to scan again because of it.
        self._create_ok()
        ticket = self._grant_for(self.ta)
        dup = self._create_with_grant(ticket, display_name="Acme Bot")
        self._assert_status(dup, 409)
        retry = self._create_with_grant(ticket)
        self.assertEqual(retry.status, "200 OK", retry.data)

    def test_a_grant_does_not_let_a_member_write(self):
        # Authorization and presence are separate gates: holding a grant never
        # substitutes for being allowed to manage the tenant. The member holds a
        # grant bound to their own id, so only the tenant check can refuse this.
        member = [m for m in self.svc.list_members(self.ta)["items"]
                  if m.get("username") == "acmemember"][0]
        payload = {
            "channel_type": "feishu",
            "display_name": "Member Bot",
            "agent_id": "agent-a",
            "credentials": dict(FEISHU),
            "scan_ticket": self._grant_for(self.ta, actor=member["user_id"]),
        }
        resp = self._create(payload, token=self.token_member, tenant=self.ta)
        self._assert_status(resp, 403)


if __name__ == "__main__":
    unittest.main()
