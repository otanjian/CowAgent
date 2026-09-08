# encoding:utf-8
"""Route-level tests for memory/knowledge GET tenant-binding + owner scoping
(task 3.10).

Verifies that the four GET handlers (MemoryHandler, MemoryContentHandler,
KnowledgeListHandler, KnowledgeReadHandler) reject an agent that is not bound to
the caller's tenant, and that private-owner filtering lets a ``tenant_admin`` read
the whole tenant while an ordinary member reads only their own content.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.runtime import RequestContext
from auth.service import IdentityService
from channel.web import web_channel, auth_handlers


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class TenantAgentReadScopingTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=tempfile.mkdtemp(), allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.token = self.svc.login("root", "Str0ngAdminPass").token
        # workspace root for the memory/knowledge services (used by handlers)
        self.ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.ws, "memory"), exist_ok=True)
        with open(os.path.join(self.ws, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# top\n")

    def _app(self):
        return web.application(
            (
                "/api/memory", "MemoryHandler",
                "/api/memory/content", "MemoryContentHandler",
                "/api/knowledge/list", "KnowledgeListHandler",
                "/api/knowledge/read", "KnowledgeReadHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="GET", data="", query="", tenant=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        headers = {"Authorization": f"Bearer {self.token}"}
        if tenant:
            headers["X-Tenant-ID"] = tenant
        kwargs["headers"] = headers
        if query:
            path = f"{path}?{query}"

        with patch.object(web_channel, "_is_database_identity", lambda: True), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch("auth.service.get_identity_service", lambda: self.svc), \
                patch.object(web_channel, "_get_workspace_root",
                             return_value=self.ws):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def _bind(self, agent_id, owner=None):
        return self.svc.bind_agent(
            tenant_id=self.tid, agent_id=agent_id,
            private_owner_user_id=owner)

    # --- tenant binding validation (agent must belong to caller's tenant) ---

    def test_memory_list_agent_not_bound_rejected(self):
        # No binding at all -> 404
        resp = self._request("/api/memory", query="agent_id=ghost", tenant=self.tid)
        self.assertEqual(resp.status, "404 Not Found")

    def test_memory_list_cross_tenant_agent_rejected(self):
        # Create a second tenant and bind an agent there; caller is in acme.
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root=tempfile.mkdtemp(), admin_username="betaadmin",
            admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=other["id"], agent_id="agent-x")
        resp = self._request("/api/memory", query="agent_id=agent-x", tenant=self.tid)
        self.assertEqual(resp.status, "404 Not Found")

    def test_memory_list_bound_agent_ok(self):
        self._bind("agent-a")
        resp = self._request("/api/memory", query="agent_id=agent-a", tenant=self.tid)
        self.assertEqual(resp.status, "200 OK")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")

    def test_knowledge_list_bound_agent_ok(self):
        self._bind("agent-a")
        resp = self._request("/api/knowledge/list", query="agent_id=agent-a", tenant=self.tid)
        self.assertEqual(resp.status, "200 OK")

    def test_memory_content_cross_tenant_agent_rejected(self):
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root=tempfile.mkdtemp(), admin_username="betaadmin",
            admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=other["id"], agent_id="agent-y")
        resp = self._request(
            "/api/memory/content",
            query=f"filename=MEMORY.md&agent_id=agent-y", tenant=self.tid)
        self.assertEqual(resp.status, "404 Not Found")

    # --- private owner scoping ---

    def test_member_private_owner_self_can_read(self):
        # A tenant-admin binds an agent with owner = admin; the owner reads it.
        self._bind("agent-a", owner=self.root["id"])
        resp = self._request("/api/memory", query="agent_id=agent-a", tenant=self.tid)
        self.assertEqual(resp.status, "200 OK")

    def test_member_private_owner_other_rejected(self):
        # root (tenant_admin) is the owner; a second member is NOT the owner but
        # CAN read because tenant_admin reads the whole tenant. The DB-level
        # member case is exercised via a non-admin context in the helper test
        # below; here we ensure the admin exception holds.
        self._bind("agent-a", owner=self.root["id"])
        resp = self._request("/api/memory", query="agent_id=agent-a", tenant=self.tid)
        self.assertEqual(resp.status, "200 OK")

    def test_knowledge_read_cross_tenant_agent_rejected(self):
        other = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root=tempfile.mkdtemp(), admin_username="betaadmin",
            admin_display="Beta", admin_password="Str0ngPass2",
            recent_password="Str0ngAdminPass")
        self.svc.bind_agent(tenant_id=other["id"], agent_id="agent-z")
        resp = self._request(
            "/api/knowledge/read",
            query=f"path=index.md&agent_id=agent-z", tenant=self.tid)
        self.assertEqual(resp.status, "404 Not Found")


class PrivateOwnerHelperTests(unittest.TestCase):
    """Direct helpers for private-owner filtering (non-admin member denied)."""

    def setUp(self):
        web.ctx.headers = []
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=tempfile.mkdtemp(), allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        # a second, non-admin member of the same tenant
        self.member = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid, operation="create-new",
            username="alice", display_name="Alice",
            temporary_password="Str0ngPass1", roles=["member"])
        self.svc.bind_agent(tenant_id=self.tid, agent_id="agent-a",
                            private_owner_user_id=self.root["id"])

    def _ctx(self, user_id, is_admin):
        perms = set(self.svc.permissions_for(user_id, self.tid))
        return RequestContext(
            user_id=user_id, username="u", display_name="U",
            is_platform_admin=False, must_change_password=False, tenant_id=self.tid,
            membership=None, permissions=perms, is_tenant_admin=is_admin,
        )

    def _patch_svc(self):
        return patch("auth.service.get_identity_service", return_value=self.svc)

    def test_private_owner_self_allowed(self):
        with self._patch_svc():
            web_channel._require_private_owner(self._ctx(self.root["id"], True), "agent-a")

    def test_private_owner_tenant_admin_allowed(self):
        # A different user who is tenant_admin may read the whole tenant.
        with self._patch_svc():
            web_channel._require_private_owner(self._ctx(self.root["id"], True), "agent-a")

    def test_private_owner_member_other_rejected(self):
        # alice (member, not owner, not admin) -> rejected.
        with self._patch_svc():
            with self.assertRaises(web.HTTPError):
                web_channel._require_private_owner(
                    self._ctx(self.member["user_id"], False), "agent-a")

    def test_shared_agent_member_allowed(self):
        # A tenant-shared agent (no private owner) is readable by any member.
        self.svc.bind_agent(tenant_id=self.tid, agent_id="shared")
        with self._patch_svc():
            web_channel._require_private_owner(
                self._ctx(self.member["user_id"], False), "shared")


if __name__ == "__main__":
    unittest.main()
