"""场景 HTTP 处理器。

- ``ScenesHandler``           ``GET /api/scenes``
- ``SceneActivateHandler``    ``POST /api/scenes/activate``

两者在请求作用域内执行：database 模式下要求已选择租户（``_db_scope``）并持有
``chat.use``（``_require_chat_use``），激活写操作另经统一的来源/CSRF 校验
（``require_management_write``）。会话场景上下文按租户命名空间存放
（``scenes.service``），因此一次激活只会影响本租户的会话。
"""
import json

import web

from scenes import service as scenes_service


def _db_scope():
    """请求作用域：解析数据库会话与租户（无身份则失败关闭）。"""
    from channel.web.web_channel import _db_scope as scope

    return scope()


def _require_chat_use(ctx):
    from channel.web.web_channel import _require_chat_use as guard

    return guard(ctx)


def _require_management_write():
    from channel.web.auth_handlers import require_management_write

    return require_management_write()


class ScenesHandler:
    def GET(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        with _db_scope() as ctx:
            _require_chat_use(ctx)
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
        _require_management_write()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(web.data() or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        with _db_scope() as ctx:
            _require_chat_use(ctx)
            # 场景上下文按请求租户命名空间写入：客户端自造的 session_id 不再
            # 成为跨租户注入的通道（见 scenes.service._scene_scope）。
            scene, err = scenes_service.activate(
                body.get("scene_id"), body.get("session_id")
            )
        if err:
            return json.dumps({"status": "error", "message": err}, ensure_ascii=False)
        return json.dumps({"status": "success", "scene": scene}, ensure_ascii=False)
