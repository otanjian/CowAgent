# encoding:utf-8
"""Personal Feishu scans: what the grant binds, and what the create re-checks.

Tasks 4.1 and 4.2 of ``upgrade-personal-channel-workbench``. The member runs two
legitimate scans against the same vendor, so the one-time grant a finished scan
mints has to say *which* one it belongs to — the member, the tenant, the login
session, the surface (public / personal), the channel type, the purpose, and the
private Agent the member picked before the QR appeared. Anything less and a scan
started in the public console could provision a private channel, or a scan from
one login could be spent from another.

The create that redeems the grant is equally load-bearing:

* the target and the tenant policy are re-decided *at creation*, not trusted from
  the scan: an Agent disabled or handed over in the seconds between the QR and
  the save must refuse the write;
* the grant is redeemed only once the row has committed, so a create refused for
  any later reason (a duplicate name, a withdrawn membership) costs the member no
  second scan;
* one grant may produce at most one instance, even when two replaces of it differ
  in everything the unique indexes would otherwise separate;
* a *create* grant is never an edit credential — the edit/enable/disable/revoke/
  binding verbs keep their own recent-password and version rules.

These drive the real ``IdentityService`` over a private identity database and the
real handlers over a ``web.application``; only the vendor SDK and the channel
runtime are stubbed, because they need the network and a live provider.
"""

import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import web

from auth import scan_authorization
from auth.service import IdentityService, IdentityServiceError
from channel import channel_instances
from channel.web import auth_handlers, web_channel
from channel.web.web_channel import FeishuRegisterHandler
from tests._helpers import (
    install_personal_target_roster,
    personal_channel_target,
)

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

MEMBER_PW = "Str0ngMemberFinal"
SHARED_AGENT = "agent-shared"
ALICE_OWN = "alice-own"
ALICE_OTHER = "alice-other"
BOB_OWN = "bob-own"
GLOBEX_OWN = "globex-own"

FEISHU = {
    "feishu_app_id": "cli_personal_a",
    "feishu_app_secret": "s3cr3t-personal",
    "feishu_bot_name": "Alice Bot",
}


def _expect(test, code, status, fn, *args, **kwargs):
    with test.assertRaises(IdentityServiceError) as caught:
        fn(*args, **kwargs)
    test.assertEqual(caught.exception.code, code, str(caught.exception))
    test.assertEqual(caught.exception.status, status)
    return caught.exception


class _PersonalScanFixture(unittest.TestCase):
    """One tenant with two members and their private Agents, plus a second tenant.

    ``agent-shared`` is bound with no private owner, which is the shape a personal
    scan *must* refuse even though the same id is a legitimate target for a shared
    instance — the two surfaces are covered by different predicates and the tests
    below keep them apart.
    """

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_master_key)

        # The personal target predicate reads the Agent Registry as well as the
        # identity binding, so the roster has to exist and be pinned to a private
        # workspace.
        install_personal_target_roster(
            self, SHARED_AGENT, ALICE_OWN, ALICE_OTHER, BOB_OWN, GLOBEX_OWN)

        self.svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
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
        self.svc.bind_agent(tenant_id=self.ta, agent_id=SHARED_AGENT,
                            private_owner_user_id=None)

        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants()
                   if t["code"] == "globex"][0]["id"]
        self.globex_admin = [m for m in self.svc.list_members(self.tb)["items"]
                             if m["username"] == "globexadmin"][0]["user_id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        personal_channel_target(self.svc, tenant_id=self.tb,
                                user_id=self.globex_admin, agent_id=GLOBEX_OWN)

        self.alice = self._add_member("alice")
        self.bob = self._add_member("bob")
        personal_channel_target(self.svc, tenant_id=self.ta,
                                user_id=self.alice, agent_id=ALICE_OWN,
                                origin="provisioned_assistant")
        personal_channel_target(self.svc, tenant_id=self.ta,
                                user_id=self.alice, agent_id=ALICE_OTHER)
        personal_channel_target(self.svc, tenant_id=self.ta,
                                user_id=self.bob, agent_id=BOB_OWN)

        self.token_alice = self.svc.login("alice", MEMBER_PW).token
        self.token_bob = self.svc.login("bob", MEMBER_PW).token
        self.ses_alice = self._session_id(self.token_alice)
        self.ses_bob = self._session_id(self.token_bob)

        scan_authorization._reset()
        self.addCleanup(scan_authorization._reset)

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _session_id(self, token):
        return self.svc.verify_session(token)["session"]["id"]

    def _add_member(self, username):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="MemTempPass1",
            roles=["member"])
        user_id = [m for m in self.svc.list_members(self.ta)["items"]
                   if m["username"] == username][0]["user_id"]
        session = self.svc.login(username, "MemTempPass1")
        self.svc.change_password(session.token, "MemTempPass1", MEMBER_PW)
        return user_id

    # -- the grant a finished personal scan mints ---------------------------

    def _grant(self, **overrides):
        payload = {
            "actor_user_id": self.alice, "tenant_id": self.ta,
            "channel_type": "feishu", "scope": scan_authorization.SCOPE_PERSONAL,
            "agent_id": ALICE_OWN, "auth_session_id": self.ses_alice,
        }
        payload.update(overrides)
        return scan_authorization.mint(**payload)

    def _binding(self, **overrides):
        payload = {
            "actor_user_id": self.alice, "tenant_id": self.ta,
            "channel_type": "feishu", "scope": scan_authorization.SCOPE_PERSONAL,
            "agent_id": ALICE_OWN, "auth_session_id": self.ses_alice,
        }
        payload.update(overrides)
        return payload

    def _instance_rows(self, user_id=None):
        rows = self.svc._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE tenant_id=?"
            + (" AND owner_user_id=?" if user_id else "") + " ORDER BY created_at",
            (self.ta, user_id) if user_id else (self.ta,))
        return [dict(row) for row in rows]

    def _create(self, *, user_id=None, display_name="Alice Bot",
                agent_id=None, credentials=None, scan_ticket="",
                auth_session_id=None, password="", tenant_id=None,
                channel_type="feishu"):
        return self.svc.create_personal_channel_instance(
            actor_user_id=user_id or self.alice,
            tenant_id=tenant_id or self.ta,
            channel_type=channel_type, display_name=display_name,
            agent_id=agent_id or ALICE_OWN,
            credentials=dict(credentials or FEISHU),
            recent_password=password,
            scan_ticket=scan_ticket,
            auth_session_id=(self.ses_alice if auth_session_id is None
                             else auth_session_id))


class PersonalScanGrantCreateTests(_PersonalScanFixture):
    """4.2 — ``scan_ticket`` on personal creation."""

    def test_a_scan_grant_creates_the_instance_without_a_password(self):
        ticket = self._grant()
        created = self._create(scan_ticket=ticket)
        self.assertEqual(created["display_name"], "Alice Bot")
        row = self._instance_rows(self.alice)[0]
        self.assertEqual(row["scope"], "user")
        self.assertEqual(row["owner_user_id"], self.alice)
        self.assertEqual(row["agent_id"], ALICE_OWN)
        # The successful create redeems the grant: a second one is refused, so a
        # leaked ticket cannot be replayed into a second instance.
        self.assertFalse(scan_authorization.verify(ticket, **self._binding()))

    def test_a_public_scope_grant_cannot_create_a_personal_instance(self):
        ticket = self._grant(scope=scan_authorization.SCOPE_TENANT,
                             agent_id="")
        _expect(self, "invalid_old", 401, self._create, scan_ticket=ticket)
        self.assertEqual(self._instance_rows(), [])
        # The refusal is the *write* describing itself, so the public create the
        # grant was actually minted for still works.
        self.assertTrue(scan_authorization.verify(
            ticket, **self._binding(scope=scan_authorization.SCOPE_TENANT,
                                    agent_id="")))

    def test_a_grant_for_another_target_is_refused_and_not_consumed(self):
        ticket = self._grant(agent_id=ALICE_OTHER)
        _expect(self, "invalid_old", 401, self._create, scan_ticket=ticket,
                agent_id=ALICE_OWN)
        self.assertEqual(self._instance_rows(), [])
        # The write named a different Agent than the scan did, so the refusal is
        # a mismatch, not a redemption: the target the member actually scanned
        # for is still authorized.
        self.assertTrue(
            scan_authorization.verify(ticket,
                                      **self._binding(agent_id=ALICE_OTHER)))

    def test_a_grant_from_another_login_session_is_refused(self):
        ticket = self._grant()
        _expect(self, "invalid_old", 401, self._create, scan_ticket=ticket,
                auth_session_id="ses_from_another_login")
        # A caller that names no session at all is not the session that ran the
        # scan either, and the grant survives both probes.
        _expect(self, "invalid_old", 401, self._create, scan_ticket=ticket,
                auth_session_id="")
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))

    def test_a_grant_does_not_cross_user_tenant_or_type(self):
        cases = (
            ("another user", {"actor_user_id": self.bob,
                              "auth_session_id": self.ses_bob}),
            ("another tenant", {"tenant_id": self.tb,
                                "actor_user_id": self.globex_admin,
                                "auth_session_id": "ses_globex"}),
            ("another channel type", {"channel_type": "wecom_bot"}),
        )
        for label, changed in cases:
            ticket = self._grant(**changed)
            binding = self._binding(**changed)
            _expect(self, "invalid_old", 401, self._create, scan_ticket=ticket)
            self.assertEqual(self._instance_rows(), [], label)
            # A refused presentation is a probe, not a redemption: the grant the
            # other identity still needs is intact.
            self.assertTrue(scan_authorization.verify(ticket, **binding), label)

    def test_a_create_refused_for_another_reason_keeps_the_grant(self):
        # A duplicate display name is refused *after* authorization. Redeeming
        # the grant there would cost the member a second scan for nothing.
        self._create(password=MEMBER_PW)
        ticket = self._grant()
        _expect(self, "conflict", 409, self._create, scan_ticket=ticket)
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))

        retry = self._create(display_name="Alice Bot 2", scan_ticket=ticket,
                             credentials=dict(FEISHU,
                                              feishu_app_id="cli_second_app"))
        self.assertEqual(retry["display_name"], "Alice Bot 2")
        self.assertEqual(len(self._instance_rows(self.alice)), 2)

    def test_creation_re_checks_the_current_target_not_the_scan(self):
        """The scan proved the target was legal *then*; the write decides *now*.

        A member whose Assistant is switched off between the QR and the save must
        not get a channel that cannot run, and must not lose the authorization
        either — the reason is fixable and the scan should not have to be redone
        blindly.
        """
        ticket = self._grant()
        roster = self.svc.personal_channel_workspace(
            actor_user_id=self.alice, tenant_id=self.ta)
        self.assertTrue([option for option in roster["agent_options"]
                         if option["id"] == ALICE_OWN and option["enabled"]])

        from config import Config, conf as _conf

        settings = Config(dict(_conf()))
        settings["agents"] = [{"id": ALICE_OWN, "name": ALICE_OWN,
                               "enabled": False}]
        with patch("config.conf", lambda: settings):
            _expect(self, "personal_agent_disabled", 403, self._create,
                    scan_ticket=ticket)
        self.assertEqual(self._instance_rows(), [])
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))

    def test_a_target_that_changed_hands_after_the_scan_is_refused(self):
        ticket = self._grant()
        # The Agent is handed to another member while the QR is on screen. The
        # ownership fact is re-read at creation, so the grant cannot carry the
        # old verdict past it.
        self.svc._store.execute(
            "UPDATE agent_bindings SET private_owner_user_id=? WHERE agent_id=?",
            (self.bob, ALICE_OWN))
        _expect(self, "personal_agent_forbidden", 403, self._create,
                scan_ticket=ticket)
        self.assertEqual(self._instance_rows(), [])
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))

    def test_two_concurrent_creates_with_one_grant_make_one_instance(self):
        """At most one create per grant, even when the two requests differ.

        The two writes are deliberately different in every field the unique
        indexes separate — display name *and* the external application — so
        nothing but the grant itself can be what refuses the loser. Redeeming the
        ticket after the commit would let both through.
        """
        ticket = self._grant()
        other_app = dict(FEISHU, feishu_app_id="cli_personal_b")
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=10)

        def _create(display_name, credentials):
            barrier.wait()
            try:
                created = self._create(display_name=display_name,
                                       credentials=credentials,
                                       scan_ticket=ticket)
                with lock:
                    outcomes.append(("ok", created["id"]))
            except IdentityServiceError as e:
                with lock:
                    outcomes.append(("refused", e.code))
            except BaseException as e:  # surfaced by the assertions below
                with lock:
                    outcomes.append(("error", repr(e)))

        threads = [
            threading.Thread(target=_create, args=("Concurrent A", dict(FEISHU))),
            threading.Thread(target=_create, args=("Concurrent B", other_app)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        winners = [o for o in outcomes if o[0] == "ok"]
        self.assertEqual(len(outcomes), 2, outcomes)
        self.assertEqual([o[0] for o in outcomes].count("ok"), 1, outcomes)
        rows = self._instance_rows(self.alice)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["id"], winners[0][1],
                         "the stored instance is not the one reported created")
        self.assertFalse(scan_authorization.verify(ticket, **self._binding()))

    def test_a_manual_create_still_works_without_a_grant(self):
        # The password path is not narrowed by any of the above.
        created = self._create(password=MEMBER_PW)
        self.assertEqual(created["display_name"], "Alice Bot")
        _expect(self, "invalid_old", 401, self._create,
                display_name="No Password")

    def test_a_create_grant_is_not_an_edit_credential(self):
        created = self._create(password=MEMBER_PW)
        ticket = self._grant()
        # The edit verb takes a password and a version, never a grant. Handing it
        # one leaves the grant untouched and the instance unchanged.
        _expect(self, "invalid_old", 401,
                self.svc.update_personal_channel_instance,
                actor_user_id=self.alice, tenant_id=self.ta,
                instance_id=created["id"], expected_version=created["version"],
                display_name="Renamed By Grant", recent_password="")
        row = self._instance_rows(self.alice)[0]
        self.assertEqual(row["display_name"], "Alice Bot")
        self.assertEqual(row["version"], created["version"])
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))
        # ...and it is still a create grant afterwards.
        self._create(display_name="Created By Grant", scan_ticket=ticket,
                     credentials=dict(FEISHU,
                                      feishu_app_id="cli_created_by_grant"))


class PersonalScanRouteTests(_PersonalScanFixture):
    """4.1/4.2 over HTTP: the handler wiring, not just the service."""

    def _app(self):
        return web.application(
            (
                "/api/feishu/register", "FeishuRegisterHandler",
                "/api/personal/channels", "PersonalChannelHandler",
                "/api/personal/channels/([^/]+)", "PersonalChannelInstanceHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def setUp(self):
        super().setUp()
        FeishuRegisterHandler._reset_sessions()
        self.addCleanup(FeishuRegisterHandler._reset_sessions)
        self.started = []

        def _fake_thread(_handler, handle):
            # The SDK worker needs the network; the session store is what these
            # tests read, so it is stood in for by the QR it would report.
            self.started.append(handle)
            FeishuRegisterHandler._set_status(
                handle, "pending", url="https://vendor/qr.png",
                qr_image="data:image/png;base64,x")

        self._thread_patch = patch.object(
            FeishuRegisterHandler, "_start_register_thread", _fake_thread)
        self._thread_patch.start()
        self.addCleanup(self._thread_patch.stop)

        def _fake_service():
            return self.svc

        patcher = patch.object(auth_handlers, "_get_service", _fake_service)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("auth.service.get_identity_service", _fake_service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._runtime = patch.object(
            channel_instances, "apply_tenant_instance_runtime",
            lambda instance_id: {"applied": True, "pending": False, "error": ""})
        self._runtime.start()
        self.addCleanup(self._runtime.stop)

    def _request(self, path, method="GET", payload=None, token=None, tenant=None):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
        if tenant:
            headers["X-Tenant-ID"] = tenant
        kwargs["headers"] = headers
        return self._app().request(path, **kwargs)

    def _body(self, resp):
        return json.loads(resp.data.decode("utf-8"))

    def _assert_status(self, resp, code):
        self.assertTrue(resp.status.startswith(str(code)), (resp.status, resp.data))

    def _start_scan(self, query="", token=None, tenant=None):
        suffix = f"?{query}" if query else ""
        resp = self._request("/api/feishu/register" + suffix,
                             token=token or self.token_alice,
                             tenant=tenant or self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        return self._body(resp)

    def _finish_scan(self, handle, token=None, tenant=None):
        resp = self._request("/api/feishu/register", method="POST",
                             payload={"handle": handle},
                             token=token or self.token_alice,
                             tenant=tenant or self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        return self._body(resp)

    def test_a_personal_scan_without_a_target_is_refused_before_registration(self):
        body = self._start_scan("scope=personal")
        self.assertEqual(body["status"], "error", body)
        self.assertEqual(body["code"], "personal_agent_required")
        # Nothing was opened with the vendor and no session was left behind: the
        # member is told to pick a target *before* the QR, not after scanning it.
        self.assertEqual(self.started, [])
        self.assertEqual(FeishuRegisterHandler._sessions, {})

    def test_a_personal_scan_must_name_the_members_own_private_target(self):
        for agent_id, code in ((SHARED_AGENT, "personal_agent_forbidden"),
                               (BOB_OWN, "personal_agent_forbidden")):
            body = self._start_scan(f"scope=personal&agent_id={agent_id}")
            self.assertEqual(body["status"], "error", (agent_id, body))
            self.assertEqual(body["code"], code, agent_id)
        self.assertEqual(self.started, [])
        self.assertEqual(FeishuRegisterHandler._sessions, {})

    def test_a_shared_scan_may_not_name_a_personal_target(self):
        body = self._start_scan(f"scope=tenant&agent_id={ALICE_OWN}")
        self.assertEqual(body["status"], "error", body)
        self.assertEqual(body["code"], "bad_request", body)
        self.assertEqual(self.started, [])

    def test_the_scope_is_not_taken_from_the_client_without_a_check(self):
        body = self._start_scan("scope=platform")
        self.assertEqual(body["status"], "error", body)
        self.assertEqual(body["code"], "bad_request", body)
        self.assertEqual(self.started, [])

    def test_a_personal_scan_creates_the_instance_from_its_own_grant(self):
        started = self._start_scan(f"scope=personal&agent_id={ALICE_OWN}")
        handle = started["handle"]
        FeishuRegisterHandler._set_status(handle, "done", app_id="cli_scanned",
                                          app_secret="scanned-secret")
        scanned = self._finish_scan(handle)
        self.assertEqual(scanned["scope"], "personal")
        self.assertEqual(scanned["agent_id"], ALICE_OWN)

        resp = self._request(
            "/api/personal/channels", method="POST",
            payload={
                "channel_type": "feishu",
                "display_name": "Feishu · nned",
                "agent_id": ALICE_OWN,
                "credentials": {"feishu_app_id": scanned["app_id"],
                                "feishu_app_secret": scanned["app_secret"],
                                "feishu_bot_name": "Scanned Bot"},
                "scan_ticket": scanned["scan_ticket"],
            },
            token=self.token_alice, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        body = self._body(resp)
        self.assertEqual(body["instance"]["display_name"], "Feishu · nned")
        # The instance is the member's own and carries the target the scan bound.
        rows = self._instance_rows(self.alice)
        self.assertEqual([(r["scope"], r["agent_id"]) for r in rows],
                         [("user", ALICE_OWN)])

    def test_the_scan_grant_is_not_transferable_to_another_target_over_http(self):
        started = self._start_scan(f"scope=personal&agent_id={ALICE_OWN}")
        handle = started["handle"]
        FeishuRegisterHandler._set_status(handle, "done", app_id="cli_scanned",
                                          app_secret="scanned-secret")
        ticket = self._finish_scan(handle)["scan_ticket"]

        resp = self._request(
            "/api/personal/channels", method="POST",
            payload={"channel_type": "feishu", "display_name": "Sneaky",
                     "agent_id": ALICE_OTHER, "credentials": dict(FEISHU),
                     "scan_ticket": ticket},
            token=self.token_alice, tenant=self.ta)
        self._assert_status(resp, 401)
        self.assertEqual(self._instance_rows(self.alice), [])

    def test_a_login_session_that_did_not_scan_cannot_poll_or_spend(self):
        started = self._start_scan(f"scope=personal&agent_id={ALICE_OWN}")
        handle = started["handle"]
        FeishuRegisterHandler._set_status(handle, "done", app_id="cli_scanned",
                                          app_secret="scanned-secret")

        # Alice's second login: same account, same tenant, a different session.
        relogin = self.svc.login("alice", MEMBER_PW).token
        body = self._finish_scan(handle, token=relogin)
        self.assertEqual(body["register_status"], "expired")
        self.assertNotIn("scan_ticket", body)
        self.assertNotIn("app_secret", json.dumps(body))

    def test_the_create_grant_is_refused_for_every_password_gated_verb(self):
        created = self._create(password=MEMBER_PW)
        ticket = self._grant()
        for payload in (
            {"action": "update", "display_name": "Renamed By Grant"},
            {"action": "enable"},
            {"action": "disable"},
            {"action": "revoke"},
        ):
            resp = self._request(
                f"/api/personal/channels/{created['id']}", method="POST",
                payload={**payload, "expected_version": created["version"],
                         "scan_ticket": ticket},
                token=self.token_alice, tenant=self.ta)
            self._assert_status(resp, 401)
        row = self._instance_rows(self.alice)[0]
        self.assertEqual(row["display_name"], "Alice Bot")
        self.assertEqual(row["active"], 1)
        # The verb that could not use the grant does not spend it either.
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))
        self._create(display_name="Created By Grant", scan_ticket=ticket,
                     credentials=dict(FEISHU,
                                      feishu_app_id="cli_from_grant_after"))

    def test_closing_verbs_are_not_authorized_by_a_create_grant(self):
        """Unlink and start_binding never ask for a grant, and never spend one.

        They are the member's *closing* / identity verbs, deliberately reachable
        without a recent password so a withdrawn capability can never strand an
        instance. The grant in the body is therefore irrelevant to them — what
        matters is that it authorizes nothing there and survives untouched.
        """
        created = self._create(password=MEMBER_PW)
        ticket = self._grant()
        challenge = self._request(
            f"/api/personal/channels/{created['id']}", method="POST",
            payload={"action": "start_binding",
                     "expected_version": created["version"],
                     "scan_ticket": ticket},
            token=self.token_alice, tenant=self.ta)
        self.assertEqual(challenge.status, "200 OK", challenge.data)
        self.assertTrue(self._body(challenge)["challenge"]["code"])
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))

        unlink = self._request(
            f"/api/personal/channels/{created['id']}", method="POST",
            payload={"action": "unlink",
                     "expected_version": created["version"],
                     "scan_ticket": ticket},
            token=self.token_alice, tenant=self.ta)
        self.assertEqual(unlink.status, "200 OK", unlink.data)
        row = self._instance_rows(self.alice)[0]
        # Removing the message identity is not clearing the target.
        self.assertEqual(row["agent_id"], ALICE_OWN)
        self.assertTrue(scan_authorization.verify(ticket, **self._binding()))


if __name__ == "__main__":
    unittest.main()
