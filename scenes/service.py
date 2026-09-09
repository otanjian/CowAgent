"""场景数据服务：目录、激活与会话上下文。

场景上下文以模块级 ``_session_scenes`` 承载（与 OneAgent 的
``WebChannel._class_scenes[session_id]`` 同款会话级共享状态），并由
``AgentBridge.get_agent()`` 在重建 Agent 时通过 ``extra_system_suffix``
注入场景提示词。单测可调用 ``clear_scene_context`` 清理，避免跨测试泄漏。
"""
import threading
from typing import Dict, List, Optional, Tuple

from common.log import logger

from scenes import config as scenes_config

# session_id -> 激活后的场景上下文（子场景已合并父元数据）
_session_scenes: Dict[str, Dict] = {}
_session_scenes_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def get_catalog() -> Dict:
    """返回 ``{categories, scenes}``；配置缺失/解析失败返回空结构。"""
    data = scenes_config.load_config()
    if not data:
        return {"categories": [], "scenes": []}
    categories = list(data.get("categories", []) or [])
    scenes = list(data.get("scenes", []) or [])
    # v1 可访问性占位：默认全部可见（保留 required_permission 字段）。
    visible = [s for s in scenes if scenes_config.can_access_scene(s)]
    return {"categories": categories, "scenes": visible}


# ---------------------------------------------------------------------------
# 场景查找
# ---------------------------------------------------------------------------
def _find_top_scene(scene_id: str) -> Optional[Dict]:
    data = scenes_config.load_config()
    if not data:
        return None
    for scene in data.get("scenes", []) or []:
        if scene.get("id") == scene_id:
            return scene
    return None


def _find_sub_scene(scene_id: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """在子场景中查找，返回 ``(parent, sub_scene)``。"""
    data = scenes_config.load_config()
    if not data:
        return None, None
    for scene in data.get("scenes", []) or []:
        for sub in scene.get("sub_scenes", []) or []:
            if sub.get("id") == scene_id:
                return scene, sub
    return None, None


def _merge_parent_context(parent: Dict, sub: Dict) -> Dict:
    """把父场景元数据合并进子场景上下文。"""
    ctx = dict(sub)
    ctx["parent_id"] = parent.get("id")
    ctx["parent_name"] = parent.get("name")
    ctx["category"] = ctx.get("category") or parent.get("category")
    ctx["has_workbench"] = bool(
        ctx.get("has_workbench", parent.get("has_workbench", False))
    )
    ctx["workbench_title"] = ctx.get("workbench_title") or parent.get(
        "workbench_title"
    )
    if not ctx.get("skill_name"):
        ctx["skill_name"] = parent.get("skill_name")
    return ctx


def find_scene(scene_id: str) -> Tuple[Optional[Dict], bool]:
    """查找场景（含子场景）。

    返回 ``(scene_context, is_sub_scene)``；未找到返回 ``(None, False)``。
    """
    top = _find_top_scene(scene_id)
    if top is not None:
        return top, False
    parent, sub = _find_sub_scene(scene_id)
    if parent is not None and sub is not None:
        return _merge_parent_context(parent, sub), True
    return None, False


# ---------------------------------------------------------------------------
# 会话上下文
# ---------------------------------------------------------------------------
def set_scene_context(session_id: str, scene: Dict) -> None:
    with _session_scenes_lock:
        _session_scenes[session_id] = scene


def get_scene_context(session_id: str) -> Optional[Dict]:
    with _session_scenes_lock:
        return _session_scenes.get(session_id)


def clear_scene_context(session_id: str) -> Optional[Dict]:
    with _session_scenes_lock:
        return _session_scenes.pop(session_id, None)


def clear_all_scene_context() -> None:
    with _session_scenes_lock:
        _session_scenes.clear()


# ---------------------------------------------------------------------------
# 技能选择集
# ---------------------------------------------------------------------------
def resolve_skill_names(scene: Dict) -> List[str]:
    """按映射表把场景 ``skill_name`` 解析为已注册技能名列表。

    未配置 ``skill_name``、映射缺失或标注「未映射」时返回空列表（不阻断）。
    """
    skill_name = (scene or {}).get("skill_name")
    if not skill_name:
        return []
    mapping = scenes_config.load_skill_mapping()
    entry = mapping.get(skill_name)
    if not entry:
        return []
    if entry.get("mapped") is False:
        return []
    name = entry.get("name") or skill_name
    return [name]


# ---------------------------------------------------------------------------
# 激活
# ---------------------------------------------------------------------------
def activate(scene_id: str, session_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    """激活场景：查场景（含子场景）、写会话上下文、失效该会话 Agent。

    返回 ``(scene_context, error)``，成功时 ``error`` 为 ``None``。
    """
    if not scene_id or not session_id:
        return None, "scene_id and session_id are required"
    scene, _ = find_scene(scene_id)
    if scene is None:
        return None, f"scene not found: {scene_id}"

    set_scene_context(session_id, scene)

    # 失效该会话现存 Agent 实例，下次 get_agent 重建时注入场景提示词。
    try:
        from bridge.bridge import Bridge

        Bridge().get_agent_bridge().clear_session(session_id)
    except Exception as e:
        # 失效失败不阻断激活：场景上下文已写入，最坏情况是下一轮仍用旧 Agent。
        logger.warning(f"[scenes] 失效会话 Agent 失败（不影响激活）: {e}")

    return scene, None
