# encoding:utf-8
"""The *instance* channel roster is a platform control, not a tenant one.

``POST /api/agents`` with ``action=bind_channel_instance`` rewrites
``channel_instances[].agent_id`` in the instance ``team.json`` -- the routing
table that decides which Agent *every* tenant's inbound IM traffic on the
shared instance reaches. The route itself is tenant-scoped and database
identity grants a tenant administrator ``agent.edit`` over its own Agents
without a per-resource grant (``_tenant_admin_owns_agent``), so before this
change a tenant administrator could rewrite the shared roster that its own
tenant boundary is supposed to be sealed against.

The platform surface for roster writes is ``/api/channels`` (platform policy);
a tenant's own channels live in the identity database and are managed through
``/api/tenant/channels``. This pins the guard for the instance-level path:
only a platform admin may bind, and the successful platform write is audited.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers


class InstanceRosterPlatformGuardTests(unittest.TestCase):
    """One tenant (acme) with a tenant admin that is NOT a platform admin."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.data_root = os.path.join(self.tmp, "data")
        self.instance = os.path.join(self.tmp, "instance")
        for path in (self.data_root, self.instance):
            os.makedirs(path)

        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(self.tmp, "tenants", "acme"), allow_weak=True)
        self.tenant_id = self.svc.list_tenants()[0]["id"]
        self.platform_admin = self.svc.list_platform_users()[0]
        self.root_token = self.svc.login("root", "Str0ngAdminPass").token

        # A tenant administrator (not a platform admin) is the console user the
        # hole was reachable from: a platform admin takes a different branch.
        self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username="acmeadmin", display_name="Tenant Admin",
            temporary_password="Str0ngTemp1", roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("acmeadmin", "Str0ngTemp1").token,
            "Str0ngTemp1", "Str0ngTaFinal")
        self.tenant_admin_token = self.svc.login("acmeadmin", "Str0ngTaFinal").token

        # The tenant admin must *own* the Agent it tries to bind: otherwise the
        # existing resource grant would refuse it and the test would pass for
        # the wrong reason.
        self.svc.bind_agent(
            tenant_id=self.tenant_id, agent_id="agent-a",
            actor_user_id=self.platform_admin["id"])

    def _app(self):
        return web.application(
            ("/api/agents", "AgentsHandler"), vars(web_channel), autoreload=False)

    def _request(self, payload, token=None, tenant=None):
        settings = {"identity_mode": "database", "identity_db_path": self.db,
                    "agent_workspace": self.instance}
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
        if tenant:
            headers["X-Tenant-ID"] = tenant

        patches = [
            patch.object(web_channel, "conf", return_value=settings),
            patch("config.conf", return_value=settings),
            patch.object(web_channel, "get_data_root", return_value=self.data_root),
            patch.object(web_channel, "_reload_agent_runtime", lambda *a, **k: None),
            patch("auth.service.get_identity_service", lambda: self.svc),
            patch.object(auth_handlers, "_get_service", lambda: self.svc),
            patch.object(admin_handlers, "_get_service", lambda: self.svc),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        return self._app().request("/api/agents", method="POST",
                                   data=json.dumps(payload), headers=headers)

    def _bind(self, token=None, tenant=None):
        """Call the handler with the roster write spied on.

        ``calls`` is the spy's record: if it stays empty the instance roster was
        never touched, which is the property a rejected caller must have.
        """
        calls = []

        def spy(**kwargs):
            calls.append(kwargs)
            return {"instance_id": kwargs.get("instance_id") or kwargs.get("channel_type"),
                    "agent_id": kwargs.get("agent_id") or "",
                    "members": list(kwargs.get("members") or [])}

        with patch.object(web_channel, "_bind_channel_instance", side_effect=spy):
            resp = self._request(
                {"action": "bind_channel_instance", "id": "agent-a",
                 "channel_type": "wecom", "instance_id": "corp1", "members": []},
                token=token, tenant=tenant)
        return resp, calls

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # --- the tenant boundary on the shared roster ----------------------

    def test_a_tenant_admin_cannot_rewrite_the_instance_roster(self):
        resp, calls = self._bind(token=self.tenant_admin_token, tenant=self.tenant_id)
        self.assertTrue(str(resp.status).startswith("403"), resp.data)
        self.assertEqual(calls, [], "the instance roster must not be written")

    def test_the_refusal_is_reported_as_forbidden_not_a_handler_error(self):
        resp, _ = self._bind(token=self.tenant_admin_token, tenant=self.tenant_id)
        self.assertEqual(self._json(resp)["code"], "forbidden")

    # --- the platform admin keeps the write, with an audit trail --------

    def test_a_platform_admin_can_bind_the_instance_roster(self):
        resp, calls = self._bind(token=self.root_token, tenant=self.tenant_id)
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["status"], "success")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["instance_id"], "corp1")

    def test_the_platform_write_is_audited(self):
        self._bind(token=self.root_token, tenant=self.tenant_id)
        events = [e for e in self.svc.list_audit(None)
                  if e["action"] == "channel.instance.bind"]
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]["target"], "channel:corp1")
        self.assertEqual(events[0]["actor_user_id"], self.platform_admin["id"])
        changes = json.loads(events[0]["redacted_changes"])
        self.assertEqual(changes["channel_type"], "wecom")
        self.assertEqual(changes["agent_id"], "agent-a")

    def test_a_rejected_bind_writes_no_audit_event(self):
        self._bind(token=self.tenant_admin_token, tenant=self.tenant_id)
        actions = [e["action"] for e in self.svc.list_audit(None)]
        self.assertNotIn("channel.instance.bind", actions)


if __name__ == "__main__":
    unittest.main()
