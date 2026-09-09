"""场景 HTTP 处理器。

- ``ScenesHandler``           ``GET /api/scenes``
- ``SceneActivateHandler``    ``POST /api/scenes/activate``

两者复用 ``channel.web.web_channel._require_auth()`` 鉴权。v1 对认证用户
默认返回全部场景（不做场景级权限拒绝），并保留 ``required_permission`` 字段。
"""
import json

import web

from scenes import service as scenes_service


def _auth():
    """复用既有鉴权（惰性导入避免模块加载期循环依赖）。"""
    from channel.web.web_channel import _require_auth

    _require_auth()


class ScenesHandler:
    def GET(self):
        _auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        catalog = scenes_service.get_catalog()
        return json.dumps(
            {
                "status": "success",
                "categories": catalog["categories"],
                "scenes": catalog["scenes"],
                "has_scene_access": True,
            },
            ensure_ascii=False,
        )


class SceneActivateHandler:
    def POST(self):
        _auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(web.data() or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        scene, err = scenes_service.activate(
            body.get("scene_id"), body.get("session_id")
        )
        if err:
            return json.dumps({"status": "error", "message": err}, ensure_ascii=False)
        return json.dumps({"status": "success", "scene": scene}, ensure_ascii=False)
