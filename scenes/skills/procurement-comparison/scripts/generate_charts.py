#!/usr/bin/env python3
"""根据 EKPO 采购订单行项目数据生成 HTML 可视化看板。

用法：
    python generate_charts.py \
        --input tmp/ekbe_history.json \
        --output tmp/采购比价分析看板.html
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _load_ekbe_history(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_float(value: Any) -> Any:
    """SAP 返回的多为字符串，安全转为 float；空值返回 None。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_input(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化输入数据为 materials 列表。

    兼容两种输入：
    1. 分组结构（fetch_ekbe_history.py 输出）：{"materials": [{"matnr","maktx","records":[...]}]}
    2. 扁平结构（工作台 ERP 同步上传）：{"records": [{MATNR, TXZ01, NETPR, AEDAT, ...}]}
       扁平结构按 MATNR 分组，SAP 大写字段名映射为脚本期望的小写字段名。
    """
    materials = data.get("materials")
    if materials:
        # 分组结构：确保每条记录按日期排序，物料列表按编码排序
        for m in materials:
            (m.get("records") or []).sort(key=lambda r: r.get("aedat") or "")
        materials.sort(key=lambda m: m.get("matnr") or "")
        return materials

    records = data.get("records") or []
    if not records:
        return []

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in records:
        matnr = rec.get("MATNR") or rec.get("matnr") or ""
        if not matnr.strip():
            continue  # 跳过物料号为空的记录
        maktx = rec.get("TXZ01") or rec.get("maktx") or rec.get("txz01") or ""
        if matnr not in groups:
            groups[matnr] = {"matnr": matnr, "maktx": maktx, "records": []}
            order.append(matnr)
        groups[matnr]["records"].append({
            "aedat": rec.get("AEDAT") or rec.get("aedat") or "",
            "netpr": _to_float(rec.get("NETPR") if "NETPR" in rec else rec.get("netpr")),
            "menge": _to_float(rec.get("MENGE") if "MENGE" in rec else rec.get("menge")),
            "netwr": _to_float(rec.get("NETWR") if "NETWR" in rec else rec.get("netwr")),
            "ebeln": rec.get("EBELN") or rec.get("ebeln") or "",
            "ebelp": rec.get("EBELP") or rec.get("ebelp") or "",
        })

    # 每个物料的记录按日期排序，物料列表按物料编码排序
    for m in groups.values():
        m["records"].sort(key=lambda r: r.get("aedat") or "")
    return [groups[m] for m in sorted(groups.keys())]


def _strip_leading_zeros(value: str) -> str:
    if isinstance(value, str) and value.isdigit():
        return value.lstrip("0") or "0"
    return value


def _build_material_trend(material: Dict[str, Any]) -> go.Figure:
    """单个物料的价格趋势折线图。"""
    records = material.get("records", [])
    dates = [r["aedat"] for r in records]
    prices = [r["netpr"] for r in records]
    label = f"{_strip_leading_zeros(material.get('matnr', ''))} {material.get('maktx', '')}".strip()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=prices, mode="lines+markers+text",
        text=[f"{p:.2f}" for p in prices],
        textposition="top center",
        name=label,
        line=dict(color="#3b82f6", width=2),
        marker=dict(size=8),
    ))
    fig.update_layout(
        title=dict(text=f"{label} 价格趋势", font=dict(size=16)),
        xaxis_title="采购时间",
        yaxis_title="净价",
        template="plotly_white",
        hovermode="x unified",
        margin=dict(l=60, r=40, t=60, b=60),
    )
    return fig


def _build_overall_charts(materials: List[Dict[str, Any]]) -> go.Figure:
    """整体箱线图与分布直方图。"""
    labels = []
    all_prices = []
    for m in materials:
        prices = [r["netpr"] for r in m.get("records", []) if r.get("netpr") is not None]
        if prices:
            label = f"{_strip_leading_zeros(m.get('matnr', ''))}"
            labels.append(label)
            all_prices.append(prices)

    if not all_prices:
        return go.Figure()

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("各物料净价分布（箱线图）", "整体净价分布（直方图）"),
        horizontal_spacing=0.12,
    )

    # 箱线图
    fig.add_trace(go.Box(
        y=labels[::-1],
        x=[p for grp in all_prices[::-1] for p in grp],
        name="箱线图",
        orientation="h",
        marker_color="#10b981",
    ), row=1, col=1)

    # 直方图
    flat_prices = [p for grp in all_prices for p in grp]
    fig.add_trace(go.Histogram(
        x=flat_prices,
        nbinsx=20,
        marker_color="#3b82f6",
        name="频次",
    ), row=1, col=2)

    fig.update_layout(
        title=dict(text="采购历史价格整体分析", font=dict(size=18)),
        template="plotly_white",
        showlegend=False,
        margin=dict(l=80, r=40, t=80, b=60),
    )
    fig.update_xaxes(title_text="净价", row=1, col=1)
    fig.update_xaxes(title_text="净价", row=1, col=2)
    fig.update_yaxes(title_text="频次", row=1, col=2)
    return fig


def _build_summary_table(materials: List[Dict[str, Any]]) -> str:
    """生成汇总指标 HTML 表格。"""
    rows = []
    for m in materials:
        prices = [r["netpr"] for r in m.get("records", []) if r.get("netpr") is not None]
        if not prices:
            continue
        rows.append({
            "matnr": _strip_leading_zeros(m.get("matnr", "")),
            "maktx": m.get("maktx", ""),
            "count": len(prices),
            "max": max(prices),
            "min": min(prices),
            "avg": sum(prices) / len(prices),
            "latest": prices[-1],
        })

    html = [
        '<table class="summary-table">',
        "<thead><tr><th>物料代码</th><th>短文本</th><th>笔数</th><th>最高价</th><th>最低价</th><th>平均价</th><th>最近价</th></tr></thead>",
        "<tbody>",
    ]
    for r in rows:
        html.append(
            f"<tr><td>{r['matnr']}</td><td>{r['maktx']}</td><td>{r['count']}</td>"
            f"<td>{r['max']:.2f}</td><td>{r['min']:.2f}</td><td>{r['avg']:.2f}</td><td>{r['latest']:.2f}</td></tr>"
        )
    html.append("</tbody></table>")
    return "\n".join(html)


def _build_dashboard(materials: List[Dict[str, Any]]) -> str:
    """组装完整 HTML 看板。"""
    trend_charts = []
    for m in materials:
        fig = _build_material_trend(m)
        trend_charts.append(fig.to_html(full_html=False, include_plotlyjs=False))

    overall_fig = _build_overall_charts(materials)
    overall_html = overall_fig.to_html(full_html=False, include_plotlyjs=False)

    summary_html = _build_summary_table(materials)

    trend_sections = "\n".join(
        f'<div class="chart-section">{chart}</div>' for chart in trend_charts
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>采购比价分析看板</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 24px; background: #f8fafc; color: #1e293b; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ font-size: 22px; margin-bottom: 8px; }}
        .subtitle {{ color: #64748b; margin-bottom: 24px; font-size: 14px; }}
        .summary-section {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
        .summary-section h2 {{ font-size: 16px; margin-bottom: 16px; }}
        .summary-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        .summary-table th, .summary-table td {{ padding: 10px 12px; border: 1px solid #e2e8f0; text-align: left; }}
        .summary-table th {{ background: #f1f5f9; font-weight: 600; }}
        .chart-section {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    </style>
</head>
<body>
    <div class="container">
        <h1>采购比价分析看板</h1>
        <div class="subtitle">基于 SAP EKPO 采购订单行项目记录生成</div>

        <div class="summary-section">
            <h2>汇总指标</h2>
            {summary_html}
        </div>

        <div class="chart-section">
            {overall_html}
        </div>

        {trend_sections}
    </div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="生成采购比价分析 HTML 看板")
    parser.add_argument("--input", required=True, help="EKPO 数据 JSON 文件路径")
    parser.add_argument("--output", required=True, help="输出 HTML 文件路径")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"输入文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    data = _load_ekbe_history(args.input)
    materials = _normalize_input(data)

    if not materials:
        print("没有物料数据可生成看板", file=sys.stderr)
        sys.exit(1)

    html = _build_dashboard(materials)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"已生成 HTML 看板: {args.output}")
    print(f"物料数量: {len(materials)}")


if __name__ == "__main__":
    main()
