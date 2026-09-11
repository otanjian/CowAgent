"""场景工作台 HTTP 处理器：文件导入。

``POST /api/scenes/workbench/import``

将工作台子场景导入文件（base64 或纯文本）保存到该请求对应 Agent 的
工作区临时目录（```<workspace>/tmp/scenes/``），供后续 Agent 读取。处理器在
请求作用域内运行：database 模式要求已选择租户并持有 ``chat.use``，写操作另经
统一的来源/CSRF 校验；工作区根目录在工作区内解析，因此只会落到本租户的共享
根目录，绝不回落到全局默认 Agent 的工作区。
"""
import base64
import json
import os
import re

import web

from scenes import service as scenes_service
from scenes.workbenches import base as wb_base


def _auth():
    """复用既有鉴权（惰性导入避免模块加载期循环依赖）。"""
    from channel.web.web_channel import _require_auth

    _require_auth()


def _db_scope():
    """请求作用域（legacy 模式为空操作），惰性导入避免循环依赖。"""
    from channel.web.web_channel import _db_scope as scope

    return scope()


def _require_chat_use(ctx):
    from channel.web.web_channel import _require_chat_use as guard

    return guard(ctx)


def _require_management_write():
    from channel.web.auth_handlers import require_management_write

    return require_management_write()


def _safe_filename(name: str) -> str:
    """仅保留合法文件名字符，避免路径穿越。"""
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fa5]", "_", name or "")
    return cleaned or "workbench_import.txt"


def _resolve_workspace_root(agent_id: str = None) -> str:
    """解析工作区根目录（与既有上传一致的数据库/遗留模式）。"""
    from channel.web.web_channel import _get_workspace_root
    return _get_workspace_root(agent_id=agent_id)


def _decode_content(encoded: str, encoding: str) -> bytes:
    """解码上传内容：base64 或 utf-8 纯文本。"""
    if not encoded:
        raise ValueError("content is required")
    if encoding == "base64":
        # 兼容 DataURL 前缀 ``data:...;base64,``。
        if "," in encoded and encoded.strip().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            return base64.b64decode(encoded)
        except Exception as e:
            raise ValueError(f"invalid base64 content: {e}")
    return encoded.encode("utf-8")


class SceneWorkbenchImportHandler:
    """POST /api/scenes/workbench/import。

    请求体：``{ scene_id, session_id, filename, content, content_encoding }``。
    ``content_encoding`` 取 ``text``（默认）或 ``base64``。导入元数据（可选）
    经 ``wb_base.build_workbench_payload`` 剔除连接凭据后返回。
    """
    def POST(self):
        _auth()
        _require_management_write()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            body = json.loads(web.data() or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        scene_id = body.get("scene_id")
        filename = body.get("filename")
        content = body.get("content")
        encoding = body.get("content_encoding", "text")

        if not scene_id or not filename:
            return json.dumps(
                {"status": "error", "message": "scene_id and filename are required"},
                ensure_ascii=False,
            )

        # 场景校验：导入仅在已配置 import_config 的子场景允许。
        scene, _ = scenes_service.find_scene(scene_id)
        if scene is None:
            return json.dumps(
                {"status": "error", "message": f"scene not found: {scene_id}"},
                ensure_ascii=False,
            )
        import_config = (scene or {}).get("import_config")
        if not import_config or not isinstance(import_config, dict) or not import_config.get("enabled"):
            return json.dumps(
                {"status": "error", "message": "this scene does not support import"},
                ensure_ascii=False,
            )

        # 文件扩展名校验。
        try:
            ext = wb_base.validate_import_extension(import_config, filename)
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

        # 解码内容。
        try:
            data = _decode_content(content, encoding)
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
        if not data:
            return json.dumps(
                {"status": "error", "message": "empty content"}, ensure_ascii=False
            )

        # 落盘到工作区临时目录 scenes/。整个解析+写入在请求作用域内进行：
        # 工作区由租户的共享根目录决定（database 模式下缺租户即 403），因此
        # 不会写入全局默认 Agent 的工作区。
        try:
            with _db_scope() as ctx:
                _require_chat_use(ctx)
                root = _resolve_workspace_root()
                scenes_tmp = os.path.join(root, "tmp", "scenes")
                os.makedirs(scenes_tmp, exist_ok=True)
                safe_name = _safe_filename(filename)
                if not safe_name.endswith(ext):
                    safe_name += ext
                save_path = os.path.join(scenes_tmp, safe_name)
                with open(save_path, "wb") as f:
                    f.write(data)
        except web.HTTPError:
            raise
        except Exception as e:
            return json.dumps(
                {"status": "error", "message": f"import failed: {e}"},
                ensure_ascii=False,
            )

        payload = wb_base.build_workbench_payload(
            scene.get("import_config"), scene.get("erp_config")
        )
        return json.dumps(
            {
                "status": "success",
                "scene_id": scene_id,
                "filename": safe_name,
                "path": save_path,
                "size": len(data),
                "workbench": payload,
            },
            ensure_ascii=False,
        )
