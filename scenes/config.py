"""场景配置路径解析、加载与校验。

主服务兼容入口：读取 ``Scene/catalog.json`` 和每个场景的 ``scene.json``，
向既有调用方返回 OneAgent 的 ``{ categories, scenes }`` 结构。
"""
import os
import json
from typing import Dict, List, Optional

from common.log import logger

SCENES_CONFIG_FILENAME = "scenes_config.json"
SKILL_MAPPING_FILENAME = "skill_mapping.json"


def scenes_dir() -> str:
    """场景应用顶层目录（``Scene/``）。"""
    from Scene.catalog import ROOT
    return str(ROOT)


def config_path() -> str:
    return os.path.join(scenes_dir(), "catalog.json")


def skill_mapping_path() -> str:
    return os.path.join(scenes_dir(), SKILL_MAPPING_FILENAME)


def load_config() -> Optional[Dict]:
    """加载并校验场景目录及每个场景的定义。

    配置缺失、JSON 解析失败或顶层结构非法时返回 ``None``（调用方降级为
    空结构，不抛出未捕获异常）。
    """
    path = config_path()
    if not os.path.exists(path):
        logger.warning(f"[scenes] 场景配置不存在: {path}")
        return None
    try:
        from Scene.catalog import read_catalog
        data = read_catalog(path)
    except Exception as e:
        logger.warning(f"[scenes] 场景配置解析失败: {e}")
        return None

    if not isinstance(data, dict):
        logger.warning("[scenes] 场景配置结构非法（非对象）")
        return None
    if not isinstance(data.get("categories"), list) or not isinstance(
        data.get("scenes"), list
    ):
        logger.warning("[scenes] 场景配置缺少 categories/scenes 列表")
        return None
    return data


def load_skill_mapping() -> Dict:
    """加载 ``skill_mapping.json``；缺失/解析失败返回空映射（不阻断）。"""
    path = skill_mapping_path()
    if not os.path.exists(path):
        logger.debug(f"[scenes] 技能映射表不存在: {path}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"[scenes] 技能映射表解析失败: {e}")
    return {}


def can_access_scene(scene: Dict, permissions=None) -> bool:
    """场景可访问性占位。

    v1 对认证用户默认返回真（不按 ``required_permission`` 过滤）。后续
    ``add-role-resource-authorization`` 落地后，在此按调用方权限集与场景
    ``required_permission`` 做实际判断。
    """
    return True
