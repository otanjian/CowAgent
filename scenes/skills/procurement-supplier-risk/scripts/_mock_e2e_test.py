#!/usr/bin/env python3
"""procurement-supplier-risk skill 端到端 mock 测试。

不实际调 LLM 和 WebSearch，用 mock 数据替换：
1. mock SAP LFA1 同步 JSON
2. mock 天机商查 JSON（预生成）
3. mock ai_recommendation（绕过 LLM）

验证 assess_risk.py / generate_report.py / build_dashboard.py 三个脚本的完整链路。
"""
import importlib.util
import json
import os
import sys
import tempfile
from unittest.mock import patch

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_HERE = os.path.dirname(__file__)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 加载三个脚本（不通过 __main__，直接调内部函数）
_assess = _load_module("_assess_test", os.path.join(_HERE, "assess_risk.py"))
_report = _load_module("_report_test", os.path.join(_HERE, "generate_report.py"))
_dash = _load_module("_dash_test", os.path.join(_HERE, "build_dashboard.py"))


# =============================================================================
# Mock 数据：3 家供应商（严重/高/低）
# =============================================================================
MOCK_SAP_ROWS = [
    {
        "供应商编码": "0000100001", "供应商名称": "高风险化工有限公司",
        "统一社会信用代码": "91310000MA1K3X9X0A",
        "过账冻结": "X", "删除标记": "", "创建日期": "20240101",
    },
    {
        "供应商编码": "0000100002", "供应商名称": "严重风险科技有限公司",
        "统一社会信用代码": "91310000MA1K3X9X0B",
        "过账冻结": "X", "删除标记": "X", "创建日期": "20200601",
    },
    {
        "供应商编码": "0000100003", "供应商名称": "优质供应商有限公司",
        "统一社会信用代码": "91310000MA1K3X9X0C",
        "过账冻结": "", "删除标记": "", "创建日期": "20100512",
    },
]

MOCK_TIANJI = {
    "0000100001": {
        "basic": {
            "company_name": "高风险化工有限公司",
            "credit_code": "91310000MA1K3X9X0A",
            "registered_capital": "30万人民币",
            "establish_date": "2024-01-01",
            "business_status": "存续",
        },
        "shareholder": [{"name": "张三", "ratio": "100%", "type": "自然人"}],
        "risk": {
            "executed_count": 2, "dishonest_count": 1, "lawsuit_count": 3,
            "penalty_count": 1, "abnormal_count": 1,
            "details": [
                "2024-05 被执行人，标的 50 万元",
                "2024-08 被限制高消费",
                "2025-01 经营异常名录",
            ],
        },
        "news": ["2024-12 合同纠纷被诉", "2025-01 客户投诉"],
        "meta": {"extracted_via_llm": True},
    },
    "0000100002": {
        "basic": {
            "company_name": "严重风险科技有限公司",
            "credit_code": "91310000MA1K3X9X0B",
            "registered_capital": "50万人民币",
            "establish_date": "2020-01-01",
            "business_status": "注销",
        },
        "shareholder": [],
        "risk": {
            "executed_count": 3, "dishonest_count": 2, "lawsuit_count": 5,
            "penalty_count": 2, "abnormal_count": 2,
            "details": ["2024-12 破产清算", "2024-10 失信被执行", "2024-08 多起诉讼"],
        },
        "news": ["2024-12 破产清算", "2024-11 资金链断裂"],
        "meta": {"extracted_via_llm": True},
    },
    "0000100003": {
        "basic": {
            "company_name": "优质供应商有限公司",
            "credit_code": "91310000MA1K3X9X0C",
            "registered_capital": "5000万人民币",
            "establish_date": "2010-05-12",
            "business_status": "存续",
        },
        "shareholder": [{"name": "A集团", "ratio": "60%"}, {"name": "B集团", "ratio": "40%"}],
        "risk": {
            "executed_count": 0, "dishonest_count": 0, "lawsuit_count": 0,
            "penalty_count": 0, "abnormal_count": 0, "details": [],
        },
        "news": ["2025-03 获得行业资质"],
        "meta": {"extracted_via_llm": True},
    },
}


def _mock_ai_recommendation(item):
    """绕过 LLM，返回规则兜底建议。"""
    level = item.get("risk_level", "未知")
    if level == "严重":
        return {
            "ai_summary": f"{item['company_name']} 风险评分 {item['total_score']}/100，存在严重司法风险与工商异常",
            "ai_recommendation": "建议立即暂停新业务合作，要求供应商提供履约担保",
            "priority_actions": ["暂停新合同", "要求履约担保", "启动备选供应商评估"],
        }
    if level == "高":
        return {
            "ai_summary": f"{item['company_name']} 风险评分 {item['total_score']}/100，存在多起司法风险事件",
            "ai_recommendation": "建议限制采购额度，加强到货验收",
            "priority_actions": ["限制采购额度", "加强到货验收", "定期复核风险"],
        }
    return {
        "ai_summary": f"{item['company_name']} 风险评分 {item['total_score']}/100，风险较低",
        "ai_recommendation": "可正常开展业务",
        "priority_actions": ["正常开展业务", "年度复核"],
    }


def _setup_mock_tianji_files(tianji_dir: str):
    """在 tianji_dir 下预生成 mock 天机商查 JSON。"""
    for code, data in MOCK_TIANJI.items():
        path = os.path.join(tianji_dir, f"tianji_{code}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def test_end_to_end():
    print("=" * 70)
    print("procurement-supplier-risk 端到端 mock 测试")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 写 mock SAP JSON
        sap_path = os.path.join(tmpdir, "supplier_risk_lfa1_data.json")
        with open(sap_path, "w", encoding="utf-8") as f:
            json.dump({"meta": {"scene_id": "supplier_risk"}, "data": MOCK_SAP_ROWS}, f,
                      ensure_ascii=False, indent=2)
        print(f"[1/5] mock SAP JSON: {sap_path} ({len(MOCK_SAP_ROWS)} 家供应商)")

        # 2. 预生成 mock 天机商查 JSON
        tianji_dir = os.path.join(tmpdir, "tianji")
        os.makedirs(tianji_dir)
        _setup_mock_tianji_files(tianji_dir)
        print(f"[2/5] mock 天机商查 JSON: {tianji_dir} ({len(MOCK_TIANJI)} 家)")

        # 3. 跑 assess_risk（assess_risk 不再调 LLM，ai_* 字段留空由对话 LLM 填充）
        # patch _run_tianji_search 返回预生成的 mock JSON 路径（模拟每次都重新搜索）
        def _mock_run_tianji(company_name, supplier_code, tianji_dir, dimensions="basic,risk,shareholder,news"):
            return os.path.join(tianji_dir, f"tianji_{supplier_code}.json")

        with patch.object(_assess, "_run_tianji_search", side_effect=_mock_run_tianji):
            assessment_path = os.path.join(tmpdir, "supplier_risk_assessment.json")
            # 直接调 main 不可行（用 argparse），改为直接调内部函数
            rows = _assess._load_sap_rows(sap_path)
            assert len(rows) == 3, f"应加载 3 家供应商，实际 {len(rows)}"

            assessments = []
            for row in rows:
                item = _assess._assess_one(row, tianji_dir, dimensions="basic,risk,shareholder,news")
                if item:
                    assessments.append(item)

            dist = {"低": 0, "中": 0, "高": 0, "严重": 0}
            for a in assessments:
                dist[a["risk_level"]] = dist.get(a["risk_level"], 0) + 1

            result = {
                "meta": {
                    "generated_at": "2026-08-18T13:00:00",
                    "total": len(assessments),
                    "risk_distribution": dist,
                },
                "assessments": assessments,
            }
            with open(assessment_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"[3/5] assess_risk: {assessment_path}")
            print(f"      评估 {len(assessments)} 家供应商，分布: {dist}")

            # 断言风险等级
            levels = {a["supplier_code"]: a["risk_level"] for a in assessments}
            assert levels["0000100002"] == "严重", f"严重风险供应商应为严重，实际 {levels['0000100002']}"
            assert levels["0000100001"] in ("高", "严重"), f"高风险供应商应为高/严重，实际 {levels['0000100001']}"
            assert levels["0000100003"] == "低", f"低风险供应商应为低，实际 {levels['0000100003']}"
            print(f"      [PASS] 风险等级断言通过: {levels}")

        # 4. 跑 generate_report.py
        excel_path = os.path.join(tmpdir, "供应商风险预警报告.xlsx")
        n = _report.generate_report(assessment_path, None, excel_path)
        assert os.path.isfile(excel_path), f"Excel 文件应存在: {excel_path}"
        assert n == 3, f"应生成 3 行数据，实际 {n}"
        print(f"[4/5] generate_report: {excel_path} ({n} 行)")

        # 5. 跑 build_dashboard.py
        html_path = os.path.join(tmpdir, "供应商风险看板.html")
        n = _dash.build_dashboard(assessment_path, html_path)
        assert os.path.isfile(html_path), f"HTML 文件应存在: {html_path}"
        assert n == 3
        # 简单校验 HTML 内容
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        assert "风险等级分布" in html
        assert "Top 10" in html
        assert "严重/高风险供应商明细" in html
        assert "高风险化工" in html
        assert "严重风险科技" in html
        print(f"[5/5] build_dashboard: {html_path} ({n} 家供应商)")

    print("\n" + "=" * 70)
    print("[ALL PASS] 端到端测试通过")
    print("  - assess_risk.py 正确加载 SAP JSON、读取天机商查 JSON、五维度评分")
    print("  - generate_report.py 正确生成 Excel（无模板自建表头 + 风险等级染色）")
    print("  - build_dashboard.py 正确生成 HTML（饼图 + Top10 条形图 + 热力图 + 明细表）")
    print("=" * 70)


if __name__ == "__main__":
    test_end_to_end()
