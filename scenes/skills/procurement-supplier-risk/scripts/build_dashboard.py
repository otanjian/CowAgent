#!/usr/bin/env python3
"""生成供应商风险预警 HTML 看板。

用法：
    python build_dashboard.py \\
        --input tmp/supplier_risk_assessment.json

说明：
- --output 可选，未指定时默认落在 --input 所在目录（工作台会话目录，跨会话不覆盖）。

输出独立的 HTML 文件，含：
- 风险等级分布饼图
- Top 10 高风险供应商条形图
- 各维度评分热力图
- 严重/高风险供应商详细列表
"""
import argparse
import json
import os
from datetime import datetime
from typing import Any, Dict, List


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>供应商风险预警看板</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: #f5f7fa; color: #1f2937; }}
  h1 {{ color: #1e40af; margin-bottom: 8px; }}
  .meta {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
  .summary-cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: white; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .card-label {{ font-size: 13px; color: #6b7280; margin-bottom: 4px; }}
  .card-value {{ font-size: 28px; font-weight: 600; }}
  .chart {{ background: white; padding: 16px; border-radius: 8px; margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .chart-title {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #1f2937; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px;
           overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #305496; color: white; padding: 10px 8px; text-align: left; font-size: 13px; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid #e5e7eb; font-size: 13px; vertical-align: top; }}
  tr:hover {{ background: #f9fafb; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-severe {{ background: #fee2e2; color: #991b1b; }}
  .badge-high {{ background: #fef3c7; color: #92400e; }}
  .badge-mid {{ background: #fef9c3; color: #854d0e; }}
  .badge-low {{ background: #dcfce7; color: #166534; }}
</style>
</head>
<body>
<h1>供应商风险预警看板</h1>
<div class="meta">
  生成时间：{generated_at} | 供应商总数：{total} | 风险分布：严重 {severe} / 高 {high} / 中 {mid} / 低 {low}
</div>

<div class="summary-cards">
  <div class="card"><div class="card-label">供应商总数</div><div class="card-value">{total}</div></div>
  <div class="card"><div class="card-label">严重风险</div><div class="card-value" style="color:#dc2626">{severe}</div></div>
  <div class="card"><div class="card-label">高风险</div><div class="card-value" style="color:#d97706">{high}</div></div>
  <div class="card"><div class="card-label">中风险</div><div class="card-value" style="color:#ca8a04">{mid}</div></div>
</div>

<div class="chart">
  <div class="chart-title">风险等级分布</div>
  <div id="pie" style="height: 320px;"></div>
</div>

<div class="chart">
  <div class="chart-title">Top 10 高风险供应商</div>
  <div id="top10" style="height: 400px;"></div>
</div>

<div class="chart">
  <div class="chart-title">各供应商维度评分热力图</div>
  <div id="heatmap" style="height: 500px;"></div>
</div>

<h2>严重/高风险供应商明细</h2>
{risk_table}

<script>
const pieData = {pie_data};
const top10Data = {top10_data};
const heatmapData = {heatmap_data};

Plotly.newPlot('pie', pieData.data, pieData.layout);
Plotly.newPlot('top10', top10Data.data, top10Data.layout);
Plotly.newPlot('heatmap', heatmapData.data, heatmapData.layout);
</script>
</body>
</html>
"""


def _risk_badge(level: str) -> str:
    cls = {"严重": "badge-severe", "高": "badge-high", "中": "badge-mid", "低": "badge-low"}.get(level, "badge-low")
    return f'<span class="badge {cls}">{level}</span>'


def _build_risk_table(assessments: List[Dict[str, Any]]) -> str:
    """生成严重/高风险供应商明细表 HTML。"""
    high_risk = [a for a in assessments if a.get("risk_level") in ("严重", "高")]
    if not high_risk:
        return "<p>无严重/高风险供应商</p>"

    rows_html = []
    for a in high_risk:
        key_risks = a.get("key_risks", []) or []
        key_risks_html = "<br>".join(f"• {r}" for r in key_risks[:3]) if key_risks else "无"
        rows_html.append(f"""
        <tr>
          <td>{a.get('supplier_code', '')}</td>
          <td>{a.get('company_name', '')}</td>
          <td>{_risk_badge(a.get('risk_level', ''))} {a.get('total_score', 0)}/100</td>
          <td>{a.get('ai_summary', '')}</td>
          <td>{key_risks_html}</td>
          <td>{a.get('ai_recommendation', '')}</td>
        </tr>
        """)

    return f"""
    <table>
      <thead><tr>
        <th>供应商编码</th><th>名称</th><th>风险等级</th>
        <th>风险摘要</th><th>关键风险点</th><th>处置建议</th>
      </tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    """


def _build_pie_data(dist: Dict[str, int]) -> Dict[str, Any]:
    labels = ["严重", "高", "中", "低"]
    values = [dist.get(l, 0) for l in labels]
    colors = ["#dc2626", "#d97706", "#ca8a04", "#16a34a"]
    return {
        "data": [{
            "type": "pie",
            "labels": labels,
            "values": values,
            "marker": {"colors": colors},
            "textinfo": "label+value+percent",
        }],
        "layout": {"margin": {"l": 20, "r": 20, "t": 20, "b": 20}},
    }


def _build_top10_data(assessments: List[Dict[str, Any]]) -> Dict[str, Any]:
    top10 = sorted(assessments, key=lambda x: -x.get("total_score", 0))[:10]
    top10.reverse()  # 横向条形图从下到上倒序
    names = [a.get("company_name", "")[:20] for a in top10]
    scores = [a.get("total_score", 0) for a in top10]
    levels = [a.get("risk_level", "") for a in top10]
    colors = [{"严重": "#dc2626", "高": "#d97706", "中": "#ca8a04", "低": "#16a34a"}.get(l, "#6b7280") for l in levels]
    return {
        "data": [{
            "type": "bar",
            "x": scores,
            "y": names,
            "orientation": "h",
            "marker": {"color": colors},
            "text": [f"{s}/100 ({l})" for s, l in zip(scores, levels)],
            "textposition": "outside",
        }],
        "layout": {
            "margin": {"l": 180, "r": 60, "t": 20, "b": 40},
            "xaxis": {"range": [0, 100], "title": "风险评分"},
        },
    }


def _build_heatmap_data(assessments: List[Dict[str, Any]]) -> Dict[str, Any]:
    top20 = sorted(assessments, key=lambda x: -x.get("total_score", 0))[:20]
    names = [a.get("company_name", "")[:15] for a in top20]
    dims = ["司法", "股东", "SAP内部", "舆情", "工商"]
    dim_keys = ["judicial", "shareholder", "sap_internal", "news", "business"]
    z = [[a.get("dimension_scores", {}).get(k, 0) for k in dim_keys] for a in top20]
    return {
        "data": [{
            "type": "heatmap",
            "z": z,
            "x": dims,
            "y": names,
            "colorscale": "YlOrRd",
            "zmin": 0,
            "zmax": 100,
            "text": z,
            "texttemplate": "%{text}",
        }],
        "layout": {
            "margin": {"l": 180, "r": 20, "t": 20, "b": 40},
        },
    }


def build_dashboard(input_path: str, output_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assessments = data.get("assessments", []) or []
    dist = (data.get("meta", {}) or {}).get("risk_distribution", {}) or {}
    generated_at = (data.get("meta", {}) or {}).get("generated_at", datetime.now().isoformat())

    pie_data = _build_pie_data(dist)
    top10_data = _build_top10_data(assessments)
    heatmap_data = _build_heatmap_data(assessments)
    risk_table_html = _build_risk_table(assessments)

    html = HTML_TEMPLATE.format(
        generated_at=generated_at[:19].replace("T", " "),
        total=len(assessments),
        severe=dist.get("严重", 0),
        high=dist.get("高", 0),
        mid=dist.get("中", 0),
        low=dist.get("低", 0),
        pie_data=json.dumps(pie_data, ensure_ascii=False),
        top10_data=json.dumps(top10_data, ensure_ascii=False),
        heatmap_data=json.dumps(heatmap_data, ensure_ascii=False),
        risk_table=risk_table_html,
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(assessments)


def main():
    ap = argparse.ArgumentParser(description="生成供应商风险预警 HTML 看板")
    ap.add_argument("--input", required=True, help="评估 JSON 路径")
    ap.add_argument("--output", help="输出 HTML 路径（默认：--input 所在目录/供应商风险看板.html，与输入同目录跨会话不覆盖）")
    args = ap.parse_args()

    # 未显式指定输出路径时，默认落到 --input 所在目录（工作台会话目录），与比价分析一致
    output = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.input)) or ".",
        "供应商风险看板.html",
    )
    n = build_dashboard(args.input, output)
    print(f"Wrote {output} ({n} suppliers)")


if __name__ == "__main__":
    main()
