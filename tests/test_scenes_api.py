# encoding:utf-8
"""场景数据接口测试（阶段 0）。

覆盖 ``GET /api/scenes`` 与 ``POST /api/scenes/activate`` 的处理器接线与
``scenes.service`` 的目录/激活逻辑：正常返回、配置缺失/解析失败、缺参、
场景不存在、子场景激活（合并父元数据）与会话上下文写入。

鉴权与租户门禁分别由 ``tests/test_http_gate.py`` /
``tests/test_scenes_tenant_scope.py`` 覆盖；本文件把 ``_db_scope`` 打桩为带
``chat.use`` 的 ``RequestContext``，专注验证场景接口本身。
"""
import contextlib
import json
import unittest
from unittest.mock import patch

import web

from auth.runtime import RequestContext
from channel.web import web_channel
from common.runtime_identity import RuntimeIdentity, use_identity
from scenes import service as scenes_service
from scenes import config as scenes_config


_TENANT = "tnt_test"


def _ctx():
    return RequestContext(
        user_id="u_test", username="u_test", display_name="u_test",
        is_platform_admin=False, must_change_password=False,
        tenant_id=_TENANT, membership={"id": "m1"},
        permissions={"chat.use"}, is_tenant_admin=False,
    )


@contextlib.contextmanager
def _fake_db_scope():
    with use_identity(RuntimeIdentity(user_id="u_test", tenant_id=_TENANT)):
        yield _ctx()


class ScenesApiTests(unittest.TestCase):
    def setUp(self):
        scenes_service.clear_all_scene_context()

    def tearDown(self):
        scenes_service.clear_all_scene_context()

    def _app(self):
        # Minimal routes only: HTTP policy is covered elsewhere. These tests
        # exercise the handler body under a faked request scope.
        return web.application(
            (
                "/api/scenes", "ScenesHandler",
                "/api/scenes/activate", "SceneActivateHandler",
            ),
            vars(web_channel),
            autoreload=False,
        )

    def _request(self, path, method="GET", data=None):
        app = self._app()
        kwargs = {"method": method, "headers": {"Host": "test"}}
        if data is not None:
            kwargs["data"] = json.dumps(data)
            kwargs["headers"]["Content-Type"] = "application/json"
        with patch.object(web_channel, "_db_scope", _fake_db_scope):
            return app.request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    # ------------------------------------------------------------------
    # GET /api/scenes
    # ------------------------------------------------------------------
    def test_scenes_returns_catalog(self):
        resp = self._request("/api/scenes", method="GET")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["categories"]), 1)
        self.assertGreaterEqual(len(data["scenes"]), 1)
        self.assertIn("required_permission", data["scenes"][0])

    def test_scenes_config_missing_returns_empty(self):
        with patch.object(scenes_config, "load_config", return_value=None):
            resp = self._request("/api/scenes", method="GET")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["categories"], [])
        self.assertEqual(data["scenes"], [])

    def test_scenes_config_parse_failure_returns_empty(self):
        # load_config 内部解析失败时降级返回 None，service 再降级为空结构。
        import tempfile, os
        bad = os.path.join(tempfile.mkdtemp(), "scenes_config.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        with patch.object(scenes_config, "config_path", return_value=bad):
            self.assertIsNone(scenes_config.load_config())
            resp = self._request("/api/scenes", method="GET")
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["categories"], [])
        self.assertEqual(data["scenes"], [])

    # ------------------------------------------------------------------
    # POST /api/scenes/activate
    # ------------------------------------------------------------------
    def test_activate_missing_params(self):
        resp = self._request("/api/scenes/activate", method="POST", data={})
        data = self._json(resp)
        self.assertEqual(data["status"], "error")
        self.assertIn("scene_id", data["message"])

    def test_activate_scene_not_found(self):
        resp = self._request(
            "/api/scenes/activate",
            method="POST",
            data={"scene_id": "does_not_exist", "session_id": "s1"},
        )
        data = self._json(resp)
        self.assertEqual(data["status"], "error")
        self.assertIn("not found", data["message"])

    def test_activate_top_scene_writes_context(self):
        resp = self._request(
            "/api/scenes/activate",
            method="POST",
            data={"scene_id": "procurement_supplier", "session_id": "s1"},
        )
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["scene"]["id"], "procurement_supplier")
        # 会话上下文已写入（按请求租户命名空间）
        ctx = scenes_service.get_scene_context("s1", tenant_id=_TENANT)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["id"], "procurement_supplier")

    def test_activate_sub_scene_merges_parent(self):
        resp = self._request(
            "/api/scenes/activate",
            method="POST",
            data={"scene_id": "supplier_qualification", "session_id": "s2"},
        )
        data = self._json(resp)
        self.assertEqual(data["status"], "success")
        scene = data["scene"]
        # 子场景激活：合并父场景元数据
        self.assertEqual(scene["id"], "supplier_qualification")
        self.assertEqual(scene["parent_id"], "procurement_supplier")
        self.assertEqual(scene["parent_name"], "供应商管理")
        self.assertEqual(scene["skill_name"], "procurement-supplier")

    # ------------------------------------------------------------------
    # service 层直接验证
    # ------------------------------------------------------------------
    def test_find_scene_top_and_sub(self):
        scene, is_sub = scenes_service.find_scene("finance_voucher")
        self.assertEqual(scene["id"], "finance_voucher")
        self.assertFalse(is_sub)

        scene, is_sub = scenes_service.find_scene("supplier_qualification")
        self.assertTrue(is_sub)
        self.assertEqual(scene["parent_id"], "procurement_supplier")

        scene, is_sub = scenes_service.find_scene("nope")
        self.assertIsNone(scene)
        self.assertFalse(is_sub)


if __name__ == "__main__":
    unittest.main()
