#!/usr/bin/env python3
"""生成供应商风险预警 Excel 报告。

用法：
    python generate_report.py \\
        --input tmp/supplier_risk_assessment.json \\
        --template skills/procurement-supplier-risk/assets/templates/供应商风险报告模板.xlsx

说明：
- --output 可选，未指定时默认落在 --input 所在目录（工作台会话目录，跨会话不覆盖）。

若模板不存在，自动生成无模板版（带表头与风险等级颜色标注）。
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, List

# 风险等级颜色（与 v2 设计文档对齐）
RISK_LEVEL_COLORS = {
    "严重": "FFC7CE",  # 红色背景
    "高":   "FFEB9C",   # 橙色背景
    "中":   "FFEB9C",   # 浅黄背景
    "低":   "C6EFCE",   # 绿色背景
}


def _try_load_template(template_path: str):
    """尝试加载模板，返回 (wb, ws, start_row) 或 None。"""
    if not template_path or not os.path.isfile(template_path):
        return None
    try:
        from openpyxl import load_workbook
        wb = load_workbook(template_path)
        ws = wb.active
        return wb, ws, 2  # 模板从第 2 行开始填数据
    except Exception:
        return None


def _build_no_template_workbook():
    """无模板时，从头构建工作簿。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "供应商风险预警"

    # 表头
    headers = [
        "供应商编码", "供应商名称", "统一社会信用代码",
        "风险评分", "风险等级",
        "司法风险", "股东与关联", "SAP内部", "新闻舆情", "工商信息",
        "SAP过账冻结", "SAP删除标记", "创建日期",
        "关键风险点", "风险摘要", "处置建议",
    ]
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="305496")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # 列宽
    col_widths = [14, 28, 22, 10, 10, 10, 10, 10, 10, 10, 12, 12, 12, 50, 50, 50]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = w

    ws.freeze_panes = "A2"
    return wb, ws, 2


def _fill_row(ws, row_idx: int, assessment: Dict[str, Any]):
    """填充一行供应商数据。"""
    from openpyxl.styles import PatternFill, Alignment

    dim = assessment.get("dimension_scores", {})
    sap = assessment.get("sap_flags", {}) or {}
    key_risks = assessment.get("key_risks", []) or []
    risk_level = assessment.get("risk_level", "")

    values = [
        assessment.get("supplier_code", ""),
        assessment.get("company_name", ""),
        assessment.get("credit_code", ""),
        assessment.get("total_score", 0),
        risk_level,
        dim.get("judicial", 0),
        dim.get("shareholder", 0),
        dim.get("sap_internal", 0),
        dim.get("news", 0),
        dim.get("business", 0),
        sap.get("posting_block", "") or "正常",
        sap.get("delete_flag", "") or "正常",
        sap.get("created_date", ""),
        "\n".join(f"- {r}" for r in key_risks) if key_risks else "无",
        assessment.get("ai_summary", ""),
        assessment.get("ai_recommendation", ""),
    ]
    for col, val in enumerate(values, 1):
        cell = ws.cell(row=row_idx, column=col, value=val)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        # 风险等级单元格染色
        if col == 5 and risk_level in RISK_LEVEL_COLORS:
            cell.fill = PatternFill("solid", fgColor=RISK_LEVEL_COLORS[risk_level])


def generate_report(input_path: str, template_path: str, output_path: str):
    """生成 Excel 报告。"""
    import openpyxl  # noqa: F401  (确保 openpyxl 可用)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assessments = data.get("assessments", []) or []

    # 按风险评分倒序
    assessments.sort(key=lambda x: -x.get("total_score", 0))

    # 加载模板或新建
    tpl = _try_load_template(template_path)
    if tpl:
        wb, ws, start_row = tpl
    else:
        wb, ws, start_row = _build_no_template_workbook()

    # 填数据
    for i, asm in enumerate(assessments):
        _fill_row(ws, start_row + i, asm)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb.save(output_path)
    return len(assessments)


def main():
    ap = argparse.ArgumentParser(description="生成供应商风险预警 Excel 报告")
    ap.add_argument("--input", required=True, help="评估 JSON 路径")
    ap.add_argument("--template", help="Excel 模板路径（可选，无则自动生成表头）")
    ap.add_argument("--output", help="输出 Excel 路径（默认：--input 所在目录/供应商风险预警报告.xlsx，与输入同目录跨会话不覆盖）")
    args = ap.parse_args()

    # 未显式指定输出路径时，默认落到 --input 所在目录（工作台会话目录），与比价分析一致
    output = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.input)) or ".",
        "供应商风险预警报告.xlsx",
    )
    n = generate_report(args.input, args.template, output)
    print(f"Wrote {output} ({n} suppliers)")


if __name__ == "__main__":
    main()
