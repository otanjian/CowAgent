#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate Excel or Word quotation document."""

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    openpyxl = None

try:
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    Document = None


def load_json_or_dict(value):
    if isinstance(value, dict):
        return value
    if Path(value).exists():
        with open(value, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(value)


def calculate_quotation_options(unit_cost: float, quantity: int, payment_terms: str) -> list:
    """Generate economy, standard and premium quotation options."""
    # Quantity discount factor
    if quantity >= 1000:
        qty_factor = 1.0
    elif quantity >= 500:
        qty_factor = 1.04
    elif quantity >= 100:
        qty_factor = 1.08
    else:
        qty_factor = 1.15

    # Payment term factor
    if "预付" in payment_terms or "发货前" in payment_terms:
        payment_factor = 0.98
    elif "90" in payment_terms:
        payment_factor = 1.05
    elif "60" in payment_terms:
        payment_factor = 1.02
    else:
        payment_factor = 1.0

    base_price = unit_cost * qty_factor * payment_factor

    options = [
        {"name": "经济型", "margin_rate": 0.12, "note": "低价策略，快速占领市场"},
        {"name": "标准型", "margin_rate": 0.18, "note": "推荐方案"},
        {"name": "高配型", "margin_rate": 0.25, "note": "含加急交付和质量保障"}
    ]

    results = []
    for opt in options:
        unit_price = round(base_price / (1 - opt["margin_rate"]), 2)
        total_price = round(unit_price * quantity, 2)
        margin = round(total_price - unit_cost * quantity, 2)
        margin_pct = round(margin / total_price * 100, 1)
        results.append({
            "方案": opt["name"],
            "单价": unit_price,
            "数量": quantity,
            "总价": total_price,
            "毛利率": f"{margin_pct}%",
            "毛利额": margin,
            "备注": opt["note"]
        })
    return results


def analyze_risks(inquiry: dict, cost: dict, delivery: dict) -> dict:
    """Analyze quotation risks with pure Python rules (stable results)."""
    risks = []
    warnings = []
    positives = []

    qty = inquiry.get("quantity", 0)
    target_margin = 0.15
    unit_cost = cost.get("unit_cost", 0)
    payment_terms = inquiry.get("payment_terms", "")
    target_meet = delivery.get("target_meet", False)

    # Risk 1: Low margin risk
    if qty > 0:
        min_price = unit_cost / (1 - target_margin)
        if min_price > unit_cost * 1.1:
            risks.append({
                "level": "high",
                "category": "利润风险",
                "content": f"目标毛利率 {target_margin*100:.1f}% 对应的单价 {min_price:.2f} 元，建议报价不能低于此价格"
            })
        elif min_price > unit_cost * 1.05:
            warnings.append({
                "level": "medium",
                "category": "利润预警",
                "content": f"建议保持 10% 以上的毛利率"
            })

    # Risk 2: Delivery risk
    if not target_meet:
        risks.append({
            "level": "high",
            "category": "交期风险",
            "content": "无法满足客户目标交期，建议提前沟通"
        })

    # Risk 3: Payment risk
    if "90" in payment_terms:
        warnings.append({
            "level": "medium",
            "category": "回款风险",
            "content": f"付款方式为 {payment_terms}，账期较长，建议关注客户信用"
        })
    elif "预付" not in payment_terms and "发货前" not in payment_terms and "TT" not in payment_terms:
        warnings.append({
            "level": "low",
            "category": "回款提醒",
            "content": f"付款方式为 {payment_terms}，建议确认客户信用"
        })

    # Positive signals
    if qty >= 500:
        positives.append({
            "level": "positive",
            "category": "批量订单",
            "content": f"需求量 {qty} 件，属于批量订单，建议给予适当优惠"
        })

    if target_meet:
        positives.append({
            "level": "positive",
            "category": "交期保障",
            "content": "可以满足客户目标交期，建议作为优势强调"
        })

    return {
        "risks": risks,
        "warnings": warnings,
        "positives": positives
    }


def generate_explanation_text(inquiry: dict, cost: dict, delivery: dict, risks: dict) -> str:
    """Generate simple explanation text with pure Python (no LLM)."""
    parts = []
    parts.append("### 报价方案说明\n")
    parts.append(f"- 成本方面：直接材料占比最高，占总成本的 {cost.get('material_cost',0)/max(cost.get('total_cost',1),0.01)*100:.1f}%\n")

    if risks.get("risks"):
        parts.append("- 风险提示：\n")
        for r in risks.get("risks"):
            parts.append(f"  - [{r['level'].upper()}] {r['category']}：{r['content']}\n")

    if risks.get("positives"):
        parts.append("- 优势说明：\n")
        for p in risks.get("positives"):
            parts.append(f"  - {p['category']}：{p['content']}\n")

    parts.append("\n### 建议\n")
    parts.append("- 推荐标准型方案，平衡利润和竞争力\n")
    parts.append("- 交期如果有压力，建议提前和客户沟通\n")
    parts.append("- 付款方式建议争取更多预付款比例\n")

    return "".join(parts)


def generate_excel(inquiry: dict, cost: dict, delivery: dict, output_path: str):
    if openpyxl is None:
        raise RuntimeError("openpyxl is required for Excel output")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报价单"

    # Header
    ws.merge_cells("A1:F1")
    ws["A1"] = "销售报价单"
    ws["A1"].font = Font(size=18, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    # Basic info
    ws["A3"] = "客户名称"
    ws["B3"] = inquiry.get("customer", "")
    ws["D3"] = "报价日期"
    ws["E3"] = datetime.now().strftime("%Y-%m-%d")
    ws["A4"] = "产品名称"
    ws["B4"] = inquiry.get("product_name", "")
    ws["D4"] = "报价有效期"
    ws["E4"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    ws["A5"] = "需求量"
    ws["B5"] = inquiry.get("quantity", 0)
    ws["D5"] = "目标交期"
    ws["E5"] = inquiry.get("target_delivery", "")

    # Cost breakdown
    ws["A7"] = "成本构成"
    ws["A7"].font = Font(bold=True)
    headers = ["成本项目", "总成本", "单位成本", "占比"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=8, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="E5E7EB", end_color="E5E7EB", fill_type="solid")

    total = cost.get("total_cost", 0)
    rows = [
        ("直接材料", cost.get("material_cost", 0), cost.get("unit_material", 0)),
        ("直接人工", cost.get("labor_cost", 0), cost.get("unit_labor", 0)),
        ("制造费用", cost.get("overhead_cost", 0), cost.get("unit_overhead", 0)),
        ("管理费用分摊", cost.get("admin_cost", 0), cost.get("unit_admin", 0)),
        ("合计", total, cost.get("unit_cost", 0))
    ]
    for i, (name, total_amt, unit_amt) in enumerate(rows, 9):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=total_amt)
        ws.cell(row=i, column=3, value=unit_amt)
        ratio = round(total_amt / total * 100, 1) if total else 0
        ws.cell(row=i, column=4, value=f"{ratio}%")

    # Quotation options
    start_row = 15
    ws.cell(row=start_row, column=1, value="报价方案").font = Font(bold=True)
    payment_terms = inquiry.get("payment_terms", "")
    options = calculate_quotation_options(cost.get("unit_cost", 0), inquiry.get("quantity", 1), payment_terms)

    opt_headers = ["方案", "单价", "数量", "总价", "毛利率", "备注"]
    for col, header in enumerate(opt_headers, 1):
        cell = ws.cell(row=start_row + 1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="E5E7EB", end_color="E5E7EB", fill_type="solid")

    for i, opt in enumerate(options, start_row + 2):
        ws.cell(row=i, column=1, value=opt["方案"])
        ws.cell(row=i, column=2, value=opt["单价"])
        ws.cell(row=i, column=3, value=opt["数量"])
        ws.cell(row=i, column=4, value=opt["总价"])
        ws.cell(row=i, column=5, value=opt["毛利率"])
        ws.cell(row=i, column=6, value=opt["备注"])

    # Delivery info
    del_row = start_row + 7
    ws.cell(row=del_row, column=1, value="交期信息").font = Font(bold=True)
    ws.cell(row=del_row + 1, column=1, value="可承诺交期")
    ws.cell(row=del_row + 1, column=2, value=delivery.get("promised_delivery", ""))
    ws.cell(row=del_row + 2, column=1, value="是否满足目标交期")
    ws.cell(row=del_row + 2, column=2, value="是" if delivery.get("target_meet") else "否")
    ws.cell(row=del_row + 3, column=1, value="付款方式")
    ws.cell(row=del_row + 3, column=2, value=payment_terms)

    # Risk and explanation
    risks = analyze_risks(inquiry, cost, delivery)
    risk_row = del_row + 6
    ws.cell(row=risk_row, column=1, value="风险分析").font = Font(bold=True)
    all_items = risks.get("risks", []) + risks.get("warnings", []) + risks.get("positives", [])
    for i, item in enumerate(all_items, risk_row + 1):
        level = item.get("level", "")
        color = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid") if level == "high" else \
                PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid") if level == "medium" else \
                PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid") if level == "positive" else None
        ws.cell(row=i, column=1, value=item.get("category", ""))
        if color:
            ws.cell(row=i, column=1).fill = color
        ws.cell(row=i, column=2, value=item.get("content", ""))

    # Adjust column widths
    for col in range(1, 7):
        ws.column_dimensions[get_column_letter(col)].width = 25

    wb.save(output_path)


def generate_word(inquiry: dict, cost: dict, delivery: dict, output_path: str):
    if Document is None:
        raise RuntimeError("python-docx is required for Word output")

    doc = Document()
    title = doc.add_heading("销售报价书", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"客户名称：{inquiry.get('customer', '')}")
    doc.add_paragraph(f"产品名称：{inquiry.get('product_name', '')}")
    doc.add_paragraph(f"需求量：{inquiry.get('quantity', 0)}")
    doc.add_paragraph(f"报价日期：{datetime.now().strftime('%Y-%m-%d')}")

    doc.add_heading("一、成本核算", level=1)
    doc.add_paragraph(f"直接材料成本：{cost.get('material_cost', 0):.2f} 元")
    doc.add_paragraph(f"直接人工成本：{cost.get('labor_cost', 0):.2f} 元")
    doc.add_paragraph(f"制造费用：{cost.get('overhead_cost', 0):.2f} 元")
    doc.add_paragraph(f"管理费用分摊：{cost.get('admin_cost', 0):.2f} 元")
    doc.add_paragraph(f"合计总成本：{cost.get('total_cost', 0):.2f} 元")
    doc.add_paragraph(f"单位成本：{cost.get('unit_cost', 4):.4f} 元")

    doc.add_heading("二、报价方案", level=1)
    payment_terms = inquiry.get("payment_terms", "")
    options = calculate_quotation_options(cost.get("unit_cost", 0), inquiry.get("quantity", 1), payment_terms)
    for opt in options:
        doc.add_paragraph(
            f"{opt['方案']}：单价 {opt['单价']:.2f} 元，"
            f"总价 {opt['总价']:.2f} 元，毛利率 {opt['毛利率']}，{opt['备注']}"
        )

    doc.add_heading("三、交期承诺", level=1)
    doc.add_paragraph(f"可承诺交期：{delivery.get('promised_delivery', '')}")
    doc.add_paragraph(f"是否满足目标交期：{'是' if delivery.get('target_meet') else '否'}")

    doc.add_heading("四、商务条款", level=1)
    doc.add_paragraph(f"付款方式：{payment_terms}")
    doc.add_paragraph(f"报价有效期：{(datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')}")

    doc.save(output_path)


def generate_html(inquiry: dict, cost: dict, delivery: dict, output_path: str):
    """Generate a visual HTML quotation dashboard."""
    payment_terms = inquiry.get("payment_terms", "")
    options = calculate_quotation_options(cost.get("unit_cost", 0), inquiry.get("quantity", 1), payment_terms)

    cost_labels = ["直接材料", "直接人工", "制造费用", "管理费用分摊"]
    cost_values = [
        cost.get("material_cost", 0),
        cost.get("labor_cost", 0),
        cost.get("overhead_cost", 0),
        cost.get("admin_cost", 0)
    ]

    option_names = [o["方案"] for o in options]
    option_prices = [o["单价"] for o in options]
    option_margins = [float(o["毛利率"].replace("%", "")) for o in options]

    risks = analyze_risks(inquiry, cost, delivery)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>销售报价单 - {inquiry.get('customer', '')}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
</head>
<body class="bg-gray-50 p-6">
    <div class="max-w-6xl mx-auto space-y-6">
        <div class="bg-white rounded-xl shadow-sm p-6">
            <h1 class="text-2xl font-bold text-gray-800">销售报价单</h1>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                <div class="bg-blue-50 rounded-lg p-4">
                    <p class="text-sm text-gray-500">客户名称</p>
                    <p class="text-lg font-semibold text-gray-800">{inquiry.get('customer', '-')}</p>
                </div>
                <div class="bg-blue-50 rounded-lg p-4">
                    <p class="text-sm text-gray-500">产品名称</p>
                    <p class="text-lg font-semibold text-gray-800">{inquiry.get('product_name', '-')}</p>
                </div>
                <div class="bg-blue-50 rounded-lg p-4">
                    <p class="text-sm text-gray-500">需求量</p>
                    <p class="text-lg font-semibold text-gray-800">{inquiry.get('quantity', 0):,}</p>
                </div>
                <div class="bg-blue-50 rounded-lg p-4">
                    <p class="text-sm text-gray-500">可承诺交期</p>
                    <p class="text-lg font-semibold text-gray-800">{delivery.get('promised_delivery', '待评估')}</p>
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="bg-white rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-bold text-gray-800 mb-4">成本构成</h2>
                <div id="costChart" style="height: 300px;"></div>
            </div>
            <div class="bg-white rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-bold text-gray-800 mb-4">报价方案对比</h2>
                <div id="priceChart" style="height: 300px;"></div>
            </div>
        </div>

        <div class="bg-white rounded-xl shadow-sm p-6">
            <h2 class="text-lg font-bold text-gray-800 mb-4">风险分析 & 建议</h2>
            <div class="space-y-4">
                {''.join(f"<div class='p-4 rounded-lg border bg-red-50 border-red-100'><span class='inline-block px-2 py-1 text-xs font-bold rounded bg-red-100 text-red-700 mr-2'>{r['level'].upper()}</span><span class='font-medium text-gray-800'>{r['category']}</span><p class='text-sm text-gray-700 mt-1'>{r['content']}</p></div>" for r in risks.get('risks', []))}
                {''.join(f"<div class='p-4 rounded-lg border bg-yellow-50 border-yellow-100'><span class='inline-block px-2 py-1 text-xs font-bold rounded bg-yellow-100 text-yellow-700 mr-2'>{w['level'].upper()}</span><span class='font-medium text-gray-800'>{w['category']}</span><p class='text-sm text-gray-700 mt-1'>{w['content']}</p></div>" for w in risks.get('warnings', []))}
                {''.join(f"<div class='p-4 rounded-lg border bg-green-50 border-green-100'><span class='inline-block px-2 py-1 text-xs font-bold rounded bg-green-100 text-green-700 mr-2'>POSITIVE</span><span class='font-medium text-gray-800'>{p['category']}</span><p class='text-sm text-gray-700 mt-1'>{p['content']}</p></div>" for p in risks.get('positives', []))}
            </div>
            <div class="mt-6 p-4 bg-blue-50 rounded-lg">
                <h3 class="font-bold text-gray-800 mb-2">建议</h3>
                <ul class="text-sm text-gray-700 space-y-1">
                    <li>推荐标准型方案，平衡利润和竞争力</li>
                    <li>交期如果有压力，建议提前和客户沟通</li>
                    <li>付款方式建议争取更多预付款比例</li>
                </ul>
            </div>
        </div>

        <div class="bg-white rounded-xl shadow-sm p-6">
            <h2 class="text-lg font-bold text-gray-800 mb-4">报价方案明细</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-100">
                        <th class="p-3 rounded-tl-lg">方案</th>
                        <th class="p-3">单价（元）</th>
                        <th class="p-3">总价（元）</th>
                        <th class="p-3">毛利率</th>
                        <th class="p-3 rounded-tr-lg">备注</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"<tr class='border-b'><td class='p-3 font-medium'>{o['方案']}</td><td class='p-3'>{o['单价']:.2f}</td><td class='p-3'>{o['总价']:,.2f}</td><td class='p-3'>{o['毛利率']}</td><td class='p-3 text-gray-600'>{o['备注']}</td></tr>" for o in options)}
                </tbody>
            </table>
        </div>

        <div class="bg-white rounded-xl shadow-sm p-6">
            <h2 class="text-lg font-bold text-gray-800 mb-4">成本明细</h2>
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-100">
                        <th class="p-3 rounded-tl-lg">成本项目</th>
                        <th class="p-3">总成本（元）</th>
                        <th class="p-3">单位成本（元）</th>
                        <th class="p-3 rounded-tr-lg">占比</th>
                    </tr>
                </thead>
                <tbody>
                    <tr class="border-b"><td class="p-3">直接材料</td><td class="p-3">{cost.get('material_cost', 0):,.2f}</td><td class="p-3">{cost.get('unit_material', 0):.4f}</td><td class="p-3">{cost.get('material_cost', 0)/max(cost.get('total_cost', 1), 0.01)*100:.1f}%</td></tr>
                    <tr class="border-b"><td class="p-3">直接人工</td><td class="p-3">{cost.get('labor_cost', 0):,.2f}</td><td class="p-3">{cost.get('unit_labor', 0):.4f}</td><td class="p-3">{cost.get('labor_cost', 0)/max(cost.get('total_cost', 1), 0.01)*100:.1f}%</td></tr>
                    <tr class="border-b"><td class="p-3">制造费用</td><td class="p-3">{cost.get('overhead_cost', 0):,.2f}</td><td class="p-3">{cost.get('unit_overhead', 0):.4f}</td><td class="p-3">{cost.get('overhead_cost', 0)/max(cost.get('total_cost', 1), 0.01)*100:.1f}%</td></tr>
                    <tr class="border-b"><td class="p-3">管理费用分摊</td><td class="p-3">{cost.get('admin_cost', 0):,.2f}</td><td class="p-3">{cost.get('unit_admin', 0):.4f}</td><td class="p-3">{cost.get('admin_cost', 0)/max(cost.get('total_cost', 1), 0.01)*100:.1f}%</td></tr>
                    <tr class="bg-gray-50 font-bold"><td class="p-3 rounded-bl-lg">合计</td><td class="p-3">{cost.get('total_cost', 0):,.2f}</td><td class="p-3">{cost.get('unit_cost', 0):.4f}</td><td class="p-3 rounded-br-lg">100%</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const costChart = echarts.init(document.getElementById('costChart'));
        costChart.setOption({{
            tooltip: {{ trigger: 'item' }},
            legend: {{ bottom: '0%' }},
            series: [{{
                type: 'pie',
                radius: ['40%', '70%'],
                avoidLabelOverlap: false,
                label: {{ show: true, formatter: '{{b}}: {{d}}%' }},
                data: {json.dumps([{"value": v, "name": n} for v, n in zip(cost_values, cost_labels)], ensure_ascii=False)}
            }}]
        }});

        const priceChart = echarts.init(document.getElementById('priceChart'));
        priceChart.setOption({{
            tooltip: {{ trigger: 'axis' }},
            legend: {{ data: ['单价', '毛利率'] }},
            xAxis: {{ type: 'category', data: {json.dumps(option_names, ensure_ascii=False)} }},
            yAxis: [
                {{ type: 'value', name: '单价（元）' }},
                {{ type: 'value', name: '毛利率（%）' }}
            ],
            series: [
                {{ name: '单价', type: 'bar', data: {json.dumps(option_prices, ensure_ascii=False)}, itemStyle: {{ color: '#3b82f6' }} }},
                {{ name: '毛利率', type: 'line', yAxisIndex: 1, data: {json.dumps(option_margins, ensure_ascii=False)}, itemStyle: {{ color: '#10b981' }} }}
            ]
        }});

        window.addEventListener('resize', () => {{ costChart.resize(); priceChart.resize(); }});
    </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description="Generate quotation document")
    parser.add_argument("--inquiry", required=True, help="Parsed inquiry JSON file")
    parser.add_argument("--cost", required=True, help="Cost calculation JSON file")
    parser.add_argument("--delivery", default="{}", help="Delivery evaluation JSON file")
    parser.add_argument("--template", help="Template file path (optional)")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument("--format", choices=["excel", "word", "html"], default="excel", help="Output format")
    args = parser.parse_args()

    inquiry = load_json_or_dict(args.inquiry)
    cost = load_json_or_dict(args.cost)
    delivery = load_json_or_dict(args.delivery) if args.delivery != "{}" else {
        "promised_delivery": "待评估",
        "target_meet": False
    }

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    if args.format == "excel":
        generate_excel(inquiry, cost, delivery, args.output)
    elif args.format == "word":
        generate_word(inquiry, cost, delivery, args.output)
    else:
        generate_html(inquiry, cost, delivery, args.output)

    print(f"Quotation document saved to: {args.output}")


if __name__ == "__main__":
    main()
