"""工作台渲染分发（``openSceneById`` 的后端对应物，按需使用）。

v1 工作台以纯前端面板为主，本模块提供场景类型 → 工作台类型的分发映射，
供后端渲染或校验时复用；前端等价分发见
``channel/web/static/js/scenes/registry.js``。

分发规则（与设计文档一致）：
    凭证/财务会计 → ``voucher``
    财税/会计准则 → ``tax``
    财务报表审查   → ``financial_audit``
    SAP 数据分析   → ``sap_analysis``
    质量追溯       → ``quality_trace``
    生产计划/排产  → ``scheduling``
    其余           → ``base``（通用工作台）
"""
from typing import Dict, Optional

# skill_name -> workbench 类型（按语义键映射到专业工作台）。
_SKILL_TO_WORKBENCH = {
    "finance-voucher": "voucher",
    "finance-expert": "tax",
    "finance-analysis": "tax",
    "financial-report-audit": "financial_audit",
    "sap-integration": "sap_analysis",
    "quality-trace": "quality_trace",
    "production-plan": "scheduling",
    "pmc-scheduler-hmt-qd": "scheduling",
}

# scene id -> workbench 类型（显式覆盖，优先于 skill_name 推断）。
_SCENE_TO_WORKBENCH: Dict[str, str] = {}


def workbench_type_for_scene(scene: Optional[Dict]) -> str:
    """返回场景应使用的专业工作台类型，无专用渲染器时回退 ``base``。"""
    if not scene:
        return "base"
    scene_id = scene.get("id") or ""
    if scene_id in _SCENE_TO_WORKBENCH:
        return _SCENE_TO_WORKBENCH[scene_id]
    skill_name = scene.get("skill_name") or ""
    return _SKILL_TO_WORKBENCH.get(skill_name, "base")
