# encoding:utf-8
"""Scenes are a tenant-scoped surface (change group 4, tasks 4.9/4.10).

The scene handlers ran on the bare console password: no request scope, no
permission, no origin check on the writes. Two consequences the audit pinned:

* ``session_id`` is **client-generated** and the activation state was stored in
  a process-global map keyed by it alone, so one tenant could activate a scene
  *for another tenant's session id* and have its prompt/skills injected into
  that conversation;
* the workbench import resolved its workspace outside any scope, so in database
  mode it either wrote into the process-global default workspace or failed with
  an unrelated error -- never deliberately inside the caller's tenant.

These tests pin the scope, the permission, the origin gate, and the tenant
namespace of the activation state.
"""

import contextlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.runtime import RequestContext
from channel.web import web_channel
from common.runtime_identity import RuntimeIdentity, use_identity
from scenes import api as scenes_api
from scenes import service as scenes_service


def _ctx(user_id="u_acme", tenant_id="tnt_acme", permissions=("chat.use",),
         is_tenant_admin=False):
    return RequestContext(
        user_id=user_id, username=user_id, display_name=user_id,
        is_platform_admin=False, must_change_password=False,
        tenant_id=tenant_id, membership={"id": "m1"},
        permissions=set(permissions), is_tenant_admin=is_tenant_admin,
    )


def _cm_scope(ctx):
    """Model ``web_channel._db_scope``: apply the ambient runtime identity.

    The real scope publishes the resolved tenant to ``current_identity()`` so
    downstream resolution (``_get_workspace_root``, the scene namespace) sees
    it; a stub that only yields the context would not exercise that contract.
    """

    @contextlib.contextmanager
    def scope():
        if ctx is None:
            yield None
            return
        with use_identity(RuntimeIdentity(user_id=ctx.user_id,
                                          tenant_id=ctx.tenant_id)):
            yield ctx

    return scope


IMPORT_SCENE = {"id": "wb_scene", "name": "WB", "import_config":
                {"enabled": True, "accept": ".txt"}}


@contextlib.contextmanager
def _handler_auth(db_scope):
    """Apply a faked ``_db_scope`` for handler-level scene tests.

    HTTP policy / real session resolution is covered elsewhere; these tests
    drive the handler body under an explicit request context.
    """
    with patch.object(web_channel, "_db_scope", db_scope):
        yield


class ScenesTenantScopeTests(unittest.TestCase):
    def setUp(self):
        scenes_service.clear_all_scene_context()
        self.addCleanup(scenes_service.clear_all_scene_context)
        self.scene_id = scenes_service.get_catalog()["scenes"][0]["id"]

    def tearDown(self):
        scenes_service.clear_all_scene_context()

    def _app(self):
        return web.application(
            (
                "/api/scenes", "ScenesHandler",
                "/api/scenes/activate", "SceneActivateHandler",
                "/api/scenes/workbench/import", "SceneWorkbenchImportHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, data, headers=None):
        """POST through the real handler with a patched request scope."""
        request_headers = {"Host": "test", "Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        return self._app().request(path, method="POST",
                                   data=json.dumps(data) if data is not None else "",
                                   headers=request_headers)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # --- the activation state is namespaced by tenant -------------------

    def test_an_activation_only_lands_in_the_callers_tenant(self):
        with _handler_auth(_cm_scope(_ctx(tenant_id="tnt_acme"))):
            resp = self._request("/api/scenes/activate",
                                 {"scene_id": self.scene_id, "session_id": "sess-x"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertIsNotNone(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_acme"))
        # The very same session id in another tenant must not see it: the id is
        # client-generated, so possession of it is not an authorization.
        self.assertIsNone(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_globex"))

    def test_a_tenant_cannot_read_another_tenants_activation(self):
        scenes_service.set_scene_context(
            "sess-x", {"id": "globex-scene"}, tenant_id="tnt_globex")
        with _handler_auth(_cm_scope(_ctx(tenant_id="tnt_acme"))):
            resp = self._request("/api/scenes/activate",
                                 {"scene_id": self.scene_id, "session_id": "sess-x"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_globex"),
            {"id": "globex-scene"})
        self.assertEqual(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_acme")["id"],
            self.scene_id)

    # --- the guards -----------------------------------------------------

    def test_the_catalog_requires_chat_use(self):
        with _handler_auth(_cm_scope(_ctx(permissions=()))):
            resp = self._app().request("/api/scenes", method="GET",
                                       headers={"Host": "test"})
        self.assertTrue(str(resp.status).startswith("403"),
                        (resp.status, resp.data))

    def test_the_catalog_is_served_with_chat_use(self):
        with _handler_auth(_cm_scope(_ctx())):
            resp = self._app().request("/api/scenes", method="GET",
                                       headers={"Host": "test"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["status"], "success")

    def test_a_cross_origin_cookie_write_is_refused_before_any_state_change(self):
        with _handler_auth(_cm_scope(_ctx())):
            resp = self._request(
                "/api/scenes/activate",
                {"scene_id": self.scene_id, "session_id": "sess-x"},
                headers={"Cookie": "cow_session=whatever",
                         "Origin": "http://evil.example"})
        self.assertTrue(str(resp.status).startswith("403"),
                        (resp.status, resp.data))
        self.assertEqual(self._json(resp)["code"], "csrf_failed")
        self.assertIsNone(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_acme"))

    def test_a_same_origin_cookie_write_is_allowed(self):
        with _handler_auth(_cm_scope(_ctx())):
            resp = self._request(
                "/api/scenes/activate",
                {"scene_id": self.scene_id, "session_id": "sess-x"},
                headers={"Cookie": "cow_session=whatever",
                         "Origin": "http://test"})
        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertIsNotNone(
            scenes_service.get_scene_context("sess-x", tenant_id="tnt_acme"))
    # --- the workbench import stays inside the tenant -------------------

    def _import(self, headers=None):
        return self._request(
            "/api/scenes/workbench/import",
            {"scene_id": IMPORT_SCENE["id"], "filename": "import.txt",
             "content": "hello", "content_encoding": "text"},
            headers=headers)

    def test_the_import_writes_into_the_tenant_root_not_the_global_workspace(self):
        tenant_root = tempfile.mkdtemp()
        global_root = tempfile.mkdtemp()
        seen = {}

        @contextlib.contextmanager
        def scope():
            seen["tenant"] = "tnt_acme"
            with use_identity(RuntimeIdentity(user_id="u_acme",
                                              tenant_id="tnt_acme")):
                yield _ctx()

        def resolve_workspace(agent_id=None):
            # Mirrors the real resolver: the tenant's shared root when a tenant
            # is in scope, the process-global default otherwise.
            return tenant_root if seen.get("tenant") else global_root

        with _handler_auth(scope), \
                patch.object(scenes_service, "find_scene",
                             lambda scene_id: (dict(IMPORT_SCENE), True)), \
                patch("scenes.api_workbench._resolve_workspace_root", resolve_workspace):
            resp = self._import()

        self.assertEqual(resp.status, "200 OK", (resp.status, resp.data))
        self.assertTrue(
            os.path.exists(os.path.join(tenant_root, "tmp", "scenes", "import.txt")),
            resp.data)
        self.assertFalse(os.path.exists(os.path.join(global_root, "tmp")))

    def test_the_import_refuses_when_the_scope_cannot_be_resolved(self):
        """A missing scope must refuse, not fall back to the global workspace."""
        global_root = tempfile.mkdtemp()
        calls = []

        def resolve_workspace(agent_id=None):
            calls.append(agent_id)
            return global_root

        @contextlib.contextmanager
        def scope():
            raise web.HTTPError(
                "400 Bad Request", {"Content-Type": "application/json"},
                json.dumps({"status": "error",
                            "message": "tenant selection required",
                            "code": "missing_tenant"}))
            yield  # pragma: no cover

        with _handler_auth(scope), \
                patch.object(scenes_service, "find_scene",
                             lambda scene_id: (dict(IMPORT_SCENE), True)), \
                patch("scenes.api_workbench._resolve_workspace_root", resolve_workspace):
            resp = self._import()

        self.assertTrue(str(resp.status).startswith("400"),
                        (resp.status, resp.data))
        self.assertEqual(self._json(resp)["code"], "missing_tenant")
        self.assertEqual(calls, [], "the workspace must not be resolved without scope")
        self.assertFalse(os.path.exists(os.path.join(global_root, "tmp")))

    def test_a_cross_origin_import_is_refused(self):
        tenant_root = tempfile.mkdtemp()

        with _handler_auth(_cm_scope(_ctx())), \
                patch.object(scenes_service, "find_scene",
                             lambda scene_id: (dict(IMPORT_SCENE), True)), \
                patch("scenes.api_workbench._resolve_workspace_root",
                      lambda agent_id=None: tenant_root):
            resp = self._import(headers={"Cookie": "cow_session=whatever",
                                         "Origin": "http://evil.example"})
        self.assertTrue(str(resp.status).startswith(("401", "403")),
                        (resp.status, resp.data))
        self.assertFalse(os.path.exists(os.path.join(tenant_root, "tmp")))


if __name__ == "__main__":
    unittest.main()
