# encoding:utf-8
"""HTTP surface for external identity binding, in both administrator scopes.

The platform routes existed already; this file pins the tenant routes added next
to them and, more importantly, pins that they are *not* interchangeable:

* a tenant admin reaches their own members through the tenant route;
* the same admin is refused on the platform route (no widening);
* a plain member is refused on both;
* a member of another tenant is not reachable through the tenant route even
  with a correct-looking membership id.
"""

import json
import os
import unittest
from unittest.mock import patch

import web

from auth import http_policy  # noqa: F401 - imported for its policy side effects
from channel.web import admin_handlers
from channel.web import auth_handlers
from channel.web import web_channel  # noqa: F401 - handler registry for the app


class _Fixture(unittest.TestCase):
    def setUp(self):
        from auth.service import IdentityService

        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = "ab" * 32
        self.svc = IdentityService(os.path.join(self._tmp(), "identity.db"))
        self.ta = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")["id"]
        self.root = self.svc.list_platform_users()[0]
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")

        # A *pure* tenant admin (no platform role) is what proves the tenant
        # route stands on its own; ``root`` above is deliberately both roles.
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password="Str0ngRootFinal",
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")

        self.member_a = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username="acmemember",
            display_name="Acme Member", temporary_password="Str0ngMember1",
            roles=["member"])["user_id"]
        self.svc.change_password(
            self.svc.login("acmemember", "Str0ngMember1").token,
            "Str0ngMember1", "Str0ngMemberFinal")
        self.member_b = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tb,
            operation="create-new", username="globexmember",
            display_name="Globex Member", temporary_password="Str0ngMember2",
            roles=["member"])["user_id"]
        self.svc.change_password(
            self.svc.login("globexmember", "Str0ngMember2").token,
            "Str0ngMember2", "Str0ngGlobexMemberFinal")

        self.token_a = self.svc.login("root", "Str0ngRootFinal").token
        self.token_b = self.svc.login("globexadmin", "Str0ngGlobexFinal").token
        self.token_member = self.svc.login("acmemember", "Str0ngMemberFinal").token

        self.mem_a = self._membership(self.ta, self.member_a)
        self.mem_b = self._membership(self.tb, self.member_b)

    def _tmp(self):
        import tempfile

        if not hasattr(self, "_dir"):
            self._dir = tempfile.mkdtemp()
        return self._dir

    def _membership(self, tenant_id, user_id):
        return [m for m in self.svc.list_members(tenant_id)["items"]
                if m["user_id"] == user_id][0]

    def tearDown(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    def _app(self):
        return web.application(
            (
                "/api/platform/users/([^/]+)/external-identities",
                "PlatformUserExternalIdentitiesHandler",
                "/api/platform/users/([^/]+)/external-identities/([^/]+)",
                "PlatformUserExternalIdentityHandler",
                "/api/platform/external-identity-attempts",
                "PlatformExternalIdentityAttemptsHandler",
                "/api/tenant/members/([^/]+)/external-identities",
                "TenantMemberExternalIdentitiesHandler",
                "/api/tenant/members/([^/]+)/external-identities/([^/]+)",
                "TenantMemberExternalIdentityHandler",
                "/api/tenant/external-identity-attempts",
                "ExternalIdentityAttemptsHandler",
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

        with patch.object(admin_handlers, "_is_database", lambda: True), \
                patch.object(admin_handlers, "_get_service", lambda: self.svc), \
                patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc):
            return self._app().request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _assert_status(self, resp, code):
        self.assertTrue(str(resp.status).startswith(str(code)),
                        f"expected {code}, got {resp.status}: {resp.data}")

    def _bind(self, member_id, token, tenant, **over):
        payload = {"provider": "feishu", "issuer": "cli_acme",
                   "subject": "ou_acme_1"}
        payload.update(over)
        return self._request(f"/api/tenant/members/{member_id}/external-identities",
                             method="POST", payload=payload, token=token,
                             tenant=tenant)


class TenantBindingHttpTests(_Fixture):
    def test_a_tenant_admin_binds_and_lists_a_member(self):
        resp = self._bind(self.mem_a["id"], self.token_a, self.ta)
        self._assert_status(resp, 200)
        binding = self._json(resp)["binding"]
        self.assertEqual(binding["user_id"], self.member_a)

        resp = self._request(
            f"/api/tenant/members/{self.mem_a['id']}/external-identities",
            token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 200)
        items = self._json(resp)["items"]
        self.assertEqual([i["subject"] for i in items], ["ou_acme_1"])
        # The console shows "who" next to "which open_id", so the row must name
        # the account it belongs to.
        self.assertEqual(items[0]["username"], "acmemember")

    def test_a_tenant_admin_deletes_a_member_binding(self):
        binding = self._json(
            self._bind(self.mem_a["id"], self.token_a, self.ta))["binding"]
        resp = self._request(
            f"/api/tenant/members/{self.mem_a['id']}/external-identities/{binding['id']}",
            method="DELETE", token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 200)
        resp = self._request(
            f"/api/tenant/members/{self.mem_a['id']}/external-identities",
            token=self.token_a, tenant=self.ta)
        self.assertEqual(self._json(resp)["items"], [])

    def test_a_tenant_admin_cannot_bind_across_tenants(self):
        # mem_b belongs to globex; the acting tenant is acme.
        resp = self._bind(self.mem_b["id"], self.token_a, self.ta,
                          issuer="cli_globex", subject="ou_globex_1")
        self._assert_status(resp, 404)

    def test_a_tenant_admin_cannot_list_or_delete_across_tenants(self):
        foreign = self._json(self._bind(
            self.mem_b["id"], self.token_b, self.tb,
            issuer="cli_globex", subject="ou_globex_1"))["binding"]
        resp = self._request(
            f"/api/tenant/members/{self.mem_b['id']}/external-identities",
            token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 404)
        resp = self._request(
            f"/api/tenant/members/{self.mem_b['id']}/external-identities/{foreign['id']}",
            method="DELETE", token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 404)
        # Still bound.
        self.assertTrue(self.svc.list_external_identities(user_id=self.member_b)["items"])

    def test_a_plain_member_cannot_use_the_tenant_route(self):
        resp = self._bind(self.mem_a["id"], self.token_member, self.ta)
        self._assert_status(resp, 403)
        resp = self._request(
            f"/api/tenant/members/{self.mem_a['id']}/external-identities",
            token=self.token_member, tenant=self.ta)
        self._assert_status(resp, 403)
        resp = self._request("/api/tenant/external-identity-attempts",
                             token=self.token_member, tenant=self.ta)
        self._assert_status(resp, 403)

    def test_the_tenant_route_requires_a_tenant_selection(self):
        resp = self._request(
            f"/api/tenant/members/{self.mem_a['id']}/external-identities",
            token=self.token_a)
        self._assert_status(resp, 400)

    def test_the_tenant_route_still_validates_the_triple(self):
        resp = self._bind(self.mem_a["id"], self.token_a, self.ta,
                          provider="Not A Provider")
        self._assert_status(resp, 400)
        resp = self._bind(self.mem_a["id"], self.token_a, self.ta, subject="")
        self._assert_status(resp, 400)

    def test_a_duplicate_triple_is_refused_not_overwritten(self):
        self._bind(self.mem_a["id"], self.token_a, self.ta)
        resp = self._bind(self.mem_a["id"], self.token_a, self.ta)
        self._assert_status(resp, 409)


class PlatformBindingHttpTests(_Fixture):
    def test_a_platform_admin_still_binds_anyone(self):
        resp = self._request(
            f"/api/platform/users/{self.member_b}/external-identities",
            method="POST", token=self.token_a,
            payload={"provider": "feishu", "issuer": "cli_globex",
                     "subject": "ou_by_platform"})
        self._assert_status(resp, 200)

    def test_a_pure_tenant_admin_is_refused_on_the_platform_route(self):
        resp = self._request(
            f"/api/platform/users/{self.member_a}/external-identities",
            method="POST", token=self.token_b,
            payload={"provider": "feishu", "issuer": "cli_acme",
                     "subject": "ou_sneaky"})
        self._assert_status(resp, 403)


class AttemptsHttpTests(_Fixture):
    def _record(self, *, tenant_id, subject="ou_stranger", instance="chan_1"):
        self.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_acme", subject=subject,
            tenant_id=tenant_id, channel_type="feishu", instance_id=instance)

    def test_a_tenant_admin_sees_only_its_own_attempts(self):
        self._record(tenant_id=self.ta)
        self._record(tenant_id=self.tb, subject="ou_globex_stranger")
        self._record(tenant_id="", subject="ou_orphan")

        resp = self._request("/api/tenant/external-identity-attempts",
                             token=self.token_a, tenant=self.ta)
        self._assert_status(resp, 200)
        items = self._json(resp)["items"]
        self.assertEqual([i["subject"] for i in items], ["ou_stranger"])
        # The picker fills the form from the attempt, so it needs the channel
        # and the delivering instance to prefill rather than ask.
        self.assertEqual(items[0]["channel_type"], "feishu")
        self.assertEqual(items[0]["instance_id"], "chan_1")

    def test_the_platform_admin_sees_everything_including_orphans(self):
        self._record(tenant_id=self.ta)
        self._record(tenant_id="", subject="ou_orphan")
        resp = self._request("/api/platform/external-identity-attempts",
                             token=self.token_a)
        self._assert_status(resp, 200)
        self.assertEqual({i["subject"] for i in self._json(resp)["items"]},
                         {"ou_stranger", "ou_orphan"})

    def test_binding_removes_the_pending_attempt(self):
        self._record(tenant_id=self.ta)
        self._bind(self.mem_a["id"], self.token_a, self.ta,
                   subject="ou_stranger")
        resp = self._request("/api/tenant/external-identity-attempts",
                             token=self.token_a, tenant=self.ta)
        self.assertEqual(self._json(resp)["items"], [])


if __name__ == "__main__":
    unittest.main()
