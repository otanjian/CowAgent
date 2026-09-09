# encoding:utf-8
"""Service + route tests for admin-bound external identity mappings.

Covers the ``external_identities`` migration/CRUD contract from the
``open-database-runtime`` change (tasks 1.1-1.4): uniqueness across the
(provider, issuer, subject) triple, immediate unbind visibility, admin-only
enforcement, and inbound resolution for the later IM channel work.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel, auth_handlers, admin_handlers
from auth.service import IdentityService, IdentityServiceError


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _member_login(svc, tid, username, password):
    """Create a plain member, complete its forced password change, return token."""
    root = svc.list_platform_users()[0]
    svc.create_member(
        actor_user_id=root["id"], tenant_id=tid, operation="create-new",
        username=username, display_name=username.title(),
        temporary_password=password, roles=["member"])
    t = svc.login(username, password).token
    svc.change_password(t, password, "Str0ngPassNew!")
    return svc.login(username, "Str0ngPassNew!").token


class ExternalIdentityServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.plain_token = _member_login(self.svc, self.tid, "plain", "Str0ngPassTmp")
        self.plain_user_id = self.svc.verify_session(self.plain_token)["user"]["id"]

    def test_bind_list_resolve_roundtrip(self):
        bound = self.svc.bind_external_identity(
            actor_user_id=self.root["id"], user_id=self.root["id"],
            provider="Feishu", issuer="cli_abc", subject="ou_123")
        self.assertTrue(bound["id"].startswith("ext_"))
        self.assertEqual(bound["provider"], "feishu")  # lowercased
        listed = self.svc.list_external_identities(user_id=self.root["id"])
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["issuer"], "cli_abc")
        self.assertEqual(listed["items"][0]["username"], "root")
        resolved = self.svc.find_user_for_external_identity(
            "feishu", "cli_abc", "ou_123")
        self.assertEqual(resolved["id"], self.root["id"])

    def test_duplicate_triple_conflict(self):
        self.svc.bind_external_identity(
            actor_user_id=self.root["id"], user_id=self.root["id"],
            provider="feishu", issuer="cli_abc", subject="ou_123")
        with self.assertRaises(IdentityServiceError) as cm:
            self.svc.bind_external_identity(
                actor_user_id=self.root["id"], user_id=self.plain_user_id,
                provider="feishu", issuer="cli_abc", subject="ou_123")
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.code, "conflict")

    def test_unbind_takes_effect_immediately(self):
        bound = self.svc.bind_external_identity(
            actor_user_id=self.root["id"], user_id=self.root["id"],
            provider="feishu", issuer="cli_abc", subject="ou_123")
        self.svc.delete_external_identity(
            actor_user_id=self.root["id"], binding_id=bound["id"])
        self.assertIsNone(self.svc.find_user_for_external_identity(
            "feishu", "cli_abc", "ou_123"))
        listed = self.svc.list_external_identities(user_id=self.root["id"])
        self.assertEqual(listed["total"], 0)

    def test_member_cannot_bind(self):
        with self.assertRaises(IdentityServiceError) as cm:
            self.svc.bind_external_identity(
                actor_user_id=self.plain_user_id, user_id=self.plain_user_id,
                provider="feishu", issuer="cli_abc", subject="ou_123")
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(cm.exception.code, "forbidden")

    def test_delete_missing_binding_404(self):
        with self.assertRaises(IdentityServiceError) as cm:
            self.svc.delete_external_identity(
                actor_user_id=self.root["id"], binding_id="ext_missing")
        self.assertEqual(cm.exception.status, 404)

    def test_inactive_user_cannot_be_bound_and_resolves_none(self):
        self.svc.bind_external_identity(
            actor_user_id=self.root["id"], user_id=self.plain_user_id,
            provider="dingtalk", issuer="corp_x", subject="u_1")
        # white-box disable (store is part of this module)
        self.svc._store.execute(
            "UPDATE users SET active=0 WHERE id=?", (self.plain_user_id,))
        with self.assertRaises(IdentityServiceError) as cm:
            self.svc.bind_external_identity(
                actor_user_id=self.root["id"], user_id=self.plain_user_id,
                provider="dingtalk", issuer="corp_x", subject="u_2")
        self.assertEqual(cm.exception.status, 400)
        # inbound resolution must not return a disabled account
        self.assertIsNone(self.svc.find_user_for_external_identity(
            "dingtalk", "corp_x", "u_1"))


class ExternalIdentityRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme", allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.member_token = _member_login(self.svc, self.tid, "plain", "Str0ngPassTmp")

    def _app(self):
        return web.application(
            (
                "/api/platform/users/([^/]+)/external-identities",
                "PlatformUserExternalIdentitiesHandler",
                "/api/platform/users/([^/]+)/external-identities/([^/]+)",
                "PlatformUserExternalIdentityHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="GET", data="", token=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = data
        if token:
            kwargs["headers"] = {"Authorization": f"Bearer {token}"}

        def _fake_service():
            return self.svc

        with patch.object(auth_handlers, "_is_database", lambda: True), \
                patch.object(auth_handlers, "_get_service", _fake_service), \
                patch.object(admin_handlers, "_is_database", lambda: True), \
                patch.object(admin_handlers, "_get_service", _fake_service):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def test_requires_authentication(self):
        resp = self._request(
            f"/api/platform/users/{self.root['id']}/external-identities")
        self.assertTrue(str(resp.status).startswith("4"))

    def test_member_rejected(self):
        resp = self._request(
            f"/api/platform/users/{self.root['id']}/external-identities",
            token=self.member_token)
        data = self._json(resp)
        self.assertEqual(data["code"], "forbidden")
        self.assertTrue(str(resp.status).startswith("403"))

    def test_admin_bind_list_delete(self):
        admin = self.svc.login("root", "Str0ngAdminPass").token
        bind_url = f"/api/platform/users/{self.root['id']}/external-identities"
        resp = self._request(
            bind_url, method="POST", token=admin,
            data=json.dumps({"provider": "feishu", "issuer": "cli_abc",
                             "subject": "ou_123"}))
        self.assertEqual(str(resp.status).startswith("2") is True, True, resp.data)
        binding = self._json(resp)["binding"]
        self.assertEqual(binding["provider"], "feishu")

        resp = self._request(bind_url, token=admin)
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total"], 1)

        resp = self._request(
            f"{bind_url}/{binding['id']}", method="DELETE", token=admin)
        self.assertEqual(self._json(resp)["status"], "success")

        resp = self._request(bind_url, token=admin)
        self.assertEqual(self._json(resp)["total"], 0)

    def test_admin_duplicate_bind_conflict(self):
        admin = self.svc.login("root", "Str0ngAdminPass").token
        bind_url = f"/api/platform/users/{self.root['id']}/external-identities"
        payload = json.dumps({"provider": "dingtalk", "issuer": "corp_x",
                              "subject": "u_1"})
        self._request(bind_url, method="POST", token=admin, data=payload)
        resp = self._request(bind_url, method="POST", token=admin, data=payload)
        data = self._json(resp)
        self.assertEqual(data["code"], "conflict")
        self.assertTrue(str(resp.status).startswith("409"))


if __name__ == "__main__":
    unittest.main()
