# encoding:utf-8
"""Cross-tenant isolation acceptance for tenant-owned channel instances (10.1).

The capability's whole point is that a tenant administrator can manage *its own*
channels and nothing else. This consolidates the four isolation properties a
reviewer should be able to check at a glance, and it adds the one that is easy to
get wrong: a request for another tenant's instance must be INDISTINGUISHABLE
from a request for an instance that does not exist at all. A distinguishable
response (403 vs 404, or a different message) would confirm existence and let an
attacker enumerate another tenant's instance ids.

Isolation is asserted against the HTTP surface, not the service, because the
handler is where a scope mix-up would surface.
"""

import json
import unittest

from tests.test_tenant_channel_http import _ChannelHttpFixture, FEISHU


class CrossTenantIsolationAcceptance(_ChannelHttpFixture):

    def _body_of(self, resp):
        """Status + error code/message, the parts that could leak existence."""
        try:
            payload = json.loads(resp.data.decode("utf-8"))
        except Exception:
            payload = {}
        return (
            str(resp.status),
            payload.get("code"),
            payload.get("message"),
        )

    # -- 1. cannot read ----------------------------------------------------
    def test_b_tenant_cannot_see_a_tenants_instances(self):
        self._create_ok()
        listing = self._json(self._request("/api/tenant/channels",
                                           token=self.token_b, tenant=self.tb))
        self.assertEqual(listing["items"], [])
        self.assertEqual(listing["total"], 0)

    def test_a_tenants_own_list_is_unaffected_by_b_tenants_rows(self):
        self._create_ok()
        created_b = self._create(
            {"channel_type": "feishu", "display_name": "Globex Bot",
             "agent_id": "agent-b", "credentials": dict(FEISHU),
             "recent_password": "Str0ngGlobexFinal"},
            token=self.token_b, tenant=self.tb)
        self.assertEqual(created_b.status, "200 OK", created_b.data)
        listing = self._json(self._request("/api/tenant/channels",
                                           token=self.token_a, tenant=self.ta))
        names = [i["display_name"] for i in listing["items"]]
        self.assertEqual(names, ["Acme Bot"])

    # -- 2. cannot touch ---------------------------------------------------
    def test_editing_another_tenants_instance_is_indistinguishable_from_missing(self):
        created = self._create_ok()
        payload = {"display_name": "Stolen", "expected_version": created["version"],
                   "recent_password": "Str0ngGlobexFinal"}

        cross = self._request(f"/api/tenant/channels/{created['id']}", method="POST",
                              payload=payload, token=self.token_b, tenant=self.tb)
        missing = self._request("/api/tenant/channels/chan_does_not_exist", method="POST",
                                payload=payload, token=self.token_b, tenant=self.tb)
        self.assertEqual(self._body_of(cross), self._body_of(missing),
                         "a cross-tenant edit leaked existence")

    def test_toggling_another_tenants_instance_is_indistinguishable_from_missing(self):
        created = self._create_ok()
        payload = {"active": False, "expected_version": created["version"],
                   "recent_password": "Str0ngGlobexFinal"}

        cross = self._request(f"/api/tenant/channels/{created['id']}/active",
                              method="POST", payload=payload,
                              token=self.token_b, tenant=self.tb)
        missing = self._request("/api/tenant/channels/chan_does_not_exist/active",
                                method="POST", payload=payload,
                                token=self.token_b, tenant=self.tb)
        self.assertEqual(self._body_of(cross), self._body_of(missing),
                         "a cross-tenant toggle leaked existence")

    def test_a_cross_tenant_edit_does_not_mutate_the_target(self):
        created = self._create_ok()
        self._request(f"/api/tenant/channels/{created['id']}", method="POST",
                      payload={"display_name": "Stolen",
                               "expected_version": created["version"],
                               "recent_password": "Str0ngGlobexFinal"},
                      token=self.token_b, tenant=self.tb)
        listing = self._json(self._request("/api/tenant/channels",
                                           token=self.token_a, tenant=self.ta))
        self.assertEqual(listing["items"][0]["display_name"], "Acme Bot")
        self.assertEqual(listing["items"][0]["version"], created["version"])

    # -- 3. cannot bind another tenant's Agent -----------------------------
    def test_binding_another_tenants_agent_is_refused(self):
        resp = self._create({
            "channel_type": "feishu", "display_name": "Wrong Agent",
            "agent_id": "agent-a",            # belongs to acme, caller is globex
            "credentials": dict(FEISHU),
            "recent_password": "Str0ngGlobexFinal",
        }, token=self.token_b, tenant=self.tb)
        self.assertTrue(str(resp.status).startswith(("400", "403")), resp.status)
        listing = self._json(self._request("/api/tenant/channels",
                                           token=self.token_b, tenant=self.tb))
        self.assertEqual(listing["items"], [], "the refused create was not rolled back")

    # -- 4. the injected runtime bundle is tenant-scoped -------------------
    def test_the_runtime_bundle_lookup_is_scoped_by_tenant(self):
        created = self._create_ok()
        from auth.service import IdentityServiceError
        # Same instance id, wrong tenant: must not resolve.
        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.channel_instance_credentials(self.tb, created["id"])
        self.assertIn("not found", str(caught.exception).lower())
        # The owning tenant still resolves its own bundle.
        bundle = self.svc.channel_instance_credentials(self.ta, created["id"])
        self.assertTrue(bundle)


if __name__ == "__main__":
    unittest.main()
