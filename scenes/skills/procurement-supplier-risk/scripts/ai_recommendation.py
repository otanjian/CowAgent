#!/usr/bin/env python3
"""风险摘要与处置建议生成（已废弃 LLM 调用，保留占位兼容旧引用）。

设计变更（v3）：
- 原实现：脚本内调 OpenAI 生成 ai_summary/ai_recommendation
- 新实现：assess_risk.py 不再调用本模块，ai_* 字段留空
- 风险摘要与处置建议由对话 LLM 在 Agent Harness 上下文里基于评分结果生成

本文件保留是为了：
1. 向后兼容：旧测试或外部调用若引用 generate_recommendation()，仍可返回有效结构
2. 提供规则兜底模板：_fallback_recommendation() 仍可用，供对话 LLM 无法生成时参考

如果确认无外部引用，本文件可安全删除。
"""
from typing import Any, Dict


def generate_recommendation(assessment_item: Dict[str, Any]) -> Dict[str, Any]:
    """[已废弃] 返回空字段占位。

    原本调用 LLM 生成风险摘要，现已移除。返回空字段，由对话 LLM 填充。
    保留函数签名是为了向后兼容。
    """
    return _fallback_recommendation(assessment_item)


def _fallback_recommendation(item: Dict[str, Any]) -> Dict[str, Any]:
    """规则兜底模板（不调 LLM）：基于风险等级返回模板化建议。

    可供对话 LLM 在无法生成建议时参考，或作为最小可用输出。
    """
    level = item.get("risk_level", "未知")
    name = item.get("company_name", "该供应商")
    score = item.get("total_score", 0)
    summary = f"{name} 风险评分 {score}/100，风险等级：{level}"

    if level == "严重":
        rec = "建议立即暂停新业务合作，要求供应商提供履约担保并复核资质"
        actions = ["暂停新合同", "要求履约担保", "启动备选供应商评估"]
    elif level == "高":
        rec = "建议限制采购额度，加强到货验收，监控风险变化"
        actions = ["限制采购额度", "加强到货验收", "定期复核风险"]
    elif level == "中":
        rec = "建议持续观察，关注风险事件进展"
        actions = ["定期复核", "关注舆情", "保持沟通"]
    else:
        rec = "风险较低，可正常开展业务"
        actions = ["正常开展业务", "年度复核"]

    return {
        "ai_summary": summary,
        "ai_recommendation": rec,
        "priority_actions": actions,
    }


if __name__ == "__main__":
    # 单元测试：用 mock 评估结果跑一遍
    import json
    mock_item = {
        "supplier_code": "0000100001",
        "company_name": "测试化工有限公司",
        "credit_code": "91310000XXXXXXXXXX",
        "total_score": 75,
        "risk_level": "严重",
        "dimension_scores": {
            "judicial": 80, "shareholder": 60, "sap_internal": 60,
            "news": 40, "business": 25,
        },
        "key_risks": [
            "2024-05 被上海市浦东新区法院列为被执行人，执行标的 50 万元",
            "2024-08 被限制高消费",
        ],
        "sap_flags": {"posting_block": "X", "delete_flag": ""},
    }
    result = generate_recommendation(mock_item)
    print(json.dumps(result, ensure_ascii=False, indent=2))
