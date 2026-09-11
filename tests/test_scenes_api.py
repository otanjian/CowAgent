# encoding:utf-8
"""场景数据接口测试（阶段 0）。

覆盖 ``GET /api/scenes`` 与 ``POST /api/scenes/activate`` 的处理器接线与
``scenes.service`` 的目录/激活逻辑：正常返回、配置缺失/解析失败、缺参、
场景不存在、子场景激活（合并父元数据）与会话上下文写入。

鉴权已在 ``_require_auth`` 单测中覆盖，本文件把 ``_require_auth`` 打桩为空
操作，专注验证场景接口本身。
"""
import json
import unittest
from unittest.mock import patch

import web

from channel.web import web_channel
from scenes import service as scenes_service
from scenes import config as scenes_config


class ScenesApiTests(unittest.TestCase):
    def setUp(self):
        scenes_service.clear_all_scene_context()

    def tearDown(self):
        scenes_service.clear_all_scene_context()

    def _app(self):
        return web_channel.build_web_app()

    def _request(self, path, method="GET", data=None):
        app = self._app()
        kwargs = {"method": method, "headers": {"Host": "test"}}
        if data:
            kwargs["data"] = json.dumps(data)
        # These are scene-handler tests: they stub the legacy ``_require_auth``
        # console password, long before the multi-tenant console existed. Pin the
        # *gate's* mode to legacy so the result does not depend on whether an
        # earlier test file already called ``config.load_config()`` (which reads
        # this developer machine's ``./config.json``, ``identity_mode=database``).
        # ``web_channel.conf`` is deliberately left alone: the handlers keep
        # whatever mode they would have had, so this pin changes nothing but the
        # HTTP policy gate. The tenant-gate contract for these routes is asserted
        # separately in ``tests/test_http_gate.py``.
        with patch.object(web_channel, "_require_auth", lambda: None), \
                patch("config.conf", lambda: {"identity_mode": "legacy"}):
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
        # 会话上下文已写入
        ctx = scenes_service.get_scene_context("s1")
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
