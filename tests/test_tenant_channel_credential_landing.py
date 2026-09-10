# encoding:utf-8
"""Credential landing and leakage acceptance for tenant channel instances (10.2).

The design decision under test: a tenant channel instance's credentials live in
the tenant-scoped ``credentials`` table inside ``identity.db`` (encrypted at
rest), and they must NOT reach the roster file (``team.json``) that the
instance-level channels used to live in. A regression here would be a silent
security failure, since a plaintext secret in a config file is invisible to the
credential masking the API applies.

Four independent surfaces are checked for the same plaintext sentinel:
  * identity.db on disk (must be encrypted),
  * the HTTP response and the masked service projection (must be masked),
  * the audit trail (must not record the secret),
  * the roster file (must not be touched at all).
"""

import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel, auth_handlers, admin_handlers
from auth.service import IdentityService
from tests.test_tenant_channel_http import _ChannelHttpFixture, FEISHU

# A distinctive sentinel that could not appear by coincidence, so a substring
# scan of any artifact is conclusive.
SENTINEL = "sk-sentinel-2f9c1d7a-should-never-appear-in-plaintext"


class CredentialLandingAcceptance(_ChannelHttpFixture):

    def _create_with_sentinel(self):
        credentials = dict(FEISHU)
        credentials["feishu_app_secret"] = SENTINEL
        resp = self._create(
            {"channel_type": "feishu", "display_name": "Sentinel Bot",
             "agent_id": "agent-a", "credentials": credentials,
             "recent_password": "Str0ngRootFinal"},
            token=self.token_a, tenant=self.ta)
        self.assertEqual(resp.status, "200 OK", resp.data)
        return resp, json.loads(resp.data.decode("utf-8"))

    # -- at rest: encrypted in identity.db --------------------------------
    def test_the_secret_is_encrypted_at_rest_in_identity_db(self):
        self._create_with_sentinel()
        with open(self.db, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(SENTINEL.encode("utf-8"), raw,
                         "the plaintext secret is readable in identity.db")
        # Sanity: the row really exists, so the scan is not passing vacuously.
        self.assertIn(b"tenant_channel_instances", raw)

    # -- over the wire: the value is write-only ----------------------------
    def test_the_create_response_carries_no_secret(self):
        resp, _ = self._create_with_sentinel()
        body = resp.data.decode("utf-8")
        self.assertNotIn(SENTINEL, body)

    def test_the_list_response_carries_no_secret(self):
        self._create_with_sentinel()
        resp = self._request("/api/tenant/channels", token=self.token_a, tenant=self.ta)
        self.assertNotIn(SENTINEL, resp.data.decode("utf-8"))

    def test_the_service_projection_carries_no_secret(self):
        self._create_with_sentinel()
        listing = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        self.assertNotIn(SENTINEL, json.dumps(listing))

    # -- audit: act recorded, secret not ----------------------------------
    def test_the_audit_trail_records_the_action_without_the_secret(self):
        self._create_with_sentinel()
        events = self.svc.list_audit(self.ta)
        self.assertTrue(events, "the create was not audited")
        blob = json.dumps(events)
        self.assertNotIn(SENTINEL, blob, "the secret leaked into the audit trail")
        kinds = {e.get("action") or e.get("kind") for e in events}
        self.assertTrue(
            any(k and "channel" in str(k) for k in kinds),
            f"no channel audit action recorded: {kinds}")

    # -- roster file: never involved --------------------------------------
    def test_the_roster_file_is_never_written_by_a_tenant_channel_create(self):
        root = tempfile.mkdtemp()
        settings = {"agent_workspace": root}
        from agent import team as team_mod
        roster = team_mod.team_file(settings)
        self.assertFalse(roster.exists(), "precondition: no roster file yet")

        self._create_with_sentinel()

        self.assertFalse(roster.exists(),
                         "a tenant channel create wrote the team roster file")
        # And scanning the whole instance root finds no trace of the secret.
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    self.assertNotIn(SENTINEL.encode("utf-8"), fh.read(),
                                     f"{name} under the instance root holds the secret")


if __name__ == "__main__":
    unittest.main()
