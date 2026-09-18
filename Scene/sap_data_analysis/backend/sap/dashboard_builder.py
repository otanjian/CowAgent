"""SAP 数据分析看板生成器。

基于 matplotlib 生成多图表组合 HTML 看板，支持财务、销售、采购、库存、生产、主数据等领域。
"""

from typing import Any, Dict, List, Optional

from .ai_insights import generate_insights_for_sap_rows
from .visualizer import generate_chart


# 领域看板配置：标题、主题色、KPI/图表偏好、洞察重点
DOMAIN_DASHBOARD_CONFIGS = {
    "finance": {
        "title": "财务数据分析看板",
        "theme": "#06b6d4",
        "gradient": "linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%)",
        "kpi_value_keywords": ["额", "金额", "value", "amount", "total", "netwr", "brtwr", "dmbtr", "wrbtr"],
        "kpi_count_keywords": ["数", "count", "笔数", "条数", "凭证数"],
        "date_keywords": ["date", "dt", "日期", "audat", "bldat", "budat", "erdat"],
        "category_keywords": ["科目", "account", "类型", "type", "凭证", "document", "bukrs", "公司"],
        "chart_titles": ["财务趋势", "TOP 科目/公司", "财务结构"],
        "insight_focus": ["异常波动", "趋势变化", "集中度"],
    },
    "sales": {
        "title": "销售数据分析看板",
        "theme": "#10b981",
        "gradient": "linear-gradient(135deg, #10b981 0%, #06b6d4 100%)",
        "kpi_value_keywords": ["销售额", "金额", "净值", "netwr", "brtwr", "value", "amount", "总价", "收入"],
        "kpi_count_keywords": ["订单数", "数量", "menge", "count", "笔数", "行项目"],
        "date_keywords": ["audat", "erdat", "fkdat", "date", "dt", "日期", "交货日期"],
        "category_keywords": ["客户", "kunnr", "name1", "产品", "matnr", "销售组织", "vkorg", "物料组"],
        "chart_titles": ["销售趋势", "TOP 客户/产品", "销售占比"],
        "insight_focus": ["销售趋势", "客户集中度", "产品贡献"],
    },
    "procurement": {
        "title": "采购数据分析看板",
        "theme": "#f59e0b",
        "gradient": "linear-gradient(135deg, #f59e0b 0%, #ef4444 100%)",
        "kpi_value_keywords": ["采购额", "金额", "净值", "netwr", "brtwr", "value", "amount", "总价"],
        "kpi_count_keywords": ["订单数", "数量", "menge", "count", "笔数", "行项目", "采购申请"],
        "date_keywords": ["bedat", "erdat", "date", "dt", "日期", "交货日期"],
        "category_keywords": ["供应商", "lifnr", "name1", "物料", "matnr", "采购组织", "ekorg", "采购组"],
        "chart_titles": ["采购趋势", "TOP 供应商/物料", "采购结构"],
        "insight_focus": ["采购趋势", "供应商集中度", "采购波动"],
    },
    "inventory": {
        "title": "库存数据分析看板",
        "theme": "#6366f1",
        "gradient": "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)",
        "kpi_value_keywords": ["库存金额", "金额", "value", "amount", "labst", "menge", "数量", "库存价值"],
        "kpi_count_keywords": ["物料数", "count", "库存记录", "批次", "仓位"],
        "date_keywords": ["date", "dt", "日期", "erdat", "wdate", "盘点日期"],
        "category_keywords": ["物料", "matnr", "maktx", "仓库", "werks", "lgort", "库存地点", "物料组"],
        "chart_titles": ["库存趋势", "TOP 物料/仓库", "库存结构"],
        "insight_focus": ["库存周转", "呆滞风险", "库存集中度"],
    },
    "production": {
        "title": "生产数据分析看板",
        "theme": "#0ea5e9",
        "gradient": "linear-gradient(135deg, #0ea5e9 0%, #3b82f6 100%)",
        "kpi_value_keywords": ["产量", "数量", "menge", "gamng", "金额", "value", "工时", "成本"],
        "kpi_count_keywords": ["工单数", "count", "订单数", "aufnr", "工序数"],
        "date_keywords": ["gstrp", "gltrp", "erdat", "date", "dt", "日期", "开工", "完工"],
        "category_keywords": ["工单", "aufnr", "物料", "matnr", "工厂", "werks", "工单类型", "auart"],
        "chart_titles": ["生产趋势", "TOP 工单/物料", "生产结构"],
        "insight_focus": ["产能趋势", "工单延期", "物料消耗"],
    },
    "master_data": {
        "title": "主数据分析看板",
        "theme": "#64748b",
        "gradient": "linear-gradient(135deg, #64748b 0%, #94a3b8 100%)",
        "kpi_value_keywords": ["数量", "count", "金额", "value"],
        "kpi_count_keywords": ["客户数", "供应商数", "物料数", "count", "记录数"],
        "date_keywords": ["erdat", "ersda", "date", "dt", "日期", "创建日期"],
        "category_keywords": ["类型", "type", "mtart", "land1", "国家", "城市", "分类"],
        "chart_titles": ["新增趋势", "分布统计", "结构占比"],
        "insight_focus": ["新增趋势", "分布均衡性", "主数据质量"],
    },
}


def _get_domain_config(domain: str) -> Dict[str, Any]:
    """获取领域配置，未知领域使用 master_data 作为兜底。"""
    return DOMAIN_DASHBOARD_CONFIGS.get(domain, DOMAIN_DASHBOARD_CONFIGS["master_data"])


# 这些字段名不应被当作数值列求和（公司代码、客户/供应商/物料编码、订单号、凭证号等）
_NUMERIC_BLACKLIST_KEYWORDS = {
    "bukrs", "kunnr", "lifnr", "matnr", "vbeln", "ebeln", "aufnr", "belnr",
    "gjahr", "buzei", "posnr", "kkber", "saknr", "werks", "lgort", "ekorg",
    "vkorg", "objnr", "stat", "mandt", "spras", "land1", "ktopl",
    "公司代码", "客户编码", "客户号", "供应商编码", "供应商号", "物料编码", "物料号",
    "销售订单号", "采购订单号", "生产订单号", "凭证号", "发票号", "申请号",
    "编码", "代码", "年度", "期间", "行项目", "公司", "工厂", "库存地点",
}


def _is_likely_code_column(col: str) -> bool:
    """判断列名是否看起来是编码/编号类字段，应避免数值累加。"""
    col_lower = col.lower()
    return any(kw.lower() in col_lower for kw in _NUMERIC_BLACKLIST_KEYWORDS)


def _infer_numeric_columns(rows: List[Dict[str, Any]]) -> List[str]:
    """从数据样本中推断数值列（金额、数量等），排除编码类字段。"""
    if not rows:
        return []
    numeric_cols = []
    for col in rows[0].keys():
        if _is_likely_code_column(col):
            continue
        values = [row.get(col) for row in rows[:20] if row.get(col) not in (None, "")]
        if not values:
            continue
        if all(isinstance(v, (int, float)) for v in values):
            numeric_cols.append(col)
        else:
            try:
                [float(str(v).replace(",", "").replace(" ", "")) for v in values[:5]]
                numeric_cols.append(col)
            except ValueError:
                pass
    return numeric_cols


def _infer_text_columns(rows: List[Dict[str, Any]], numeric_cols: List[str]) -> List[str]:
    """从数据样本中推断文本/分类列。"""
    if not rows:
        return []
    text_cols = []
    for col in rows[0].keys():
        if col in numeric_cols:
            continue
        values = [row.get(col) for row in rows if row.get(col) not in (None, "")]
        if not values:
            continue
        unique_ratio = len(set(values)) / len(values)
        if unique_ratio < 0.5:
            text_cols.append(col)
    return text_cols


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _column_matches_keywords(col: str, keywords: List[str]) -> bool:
    """判断列名是否匹配一组关键词。"""
    col_lower = col.lower()
    return any(kw.lower() in col_lower for kw in keywords)


def _pick_kpi_columns(numeric_cols: List[str], config: Dict[str, Any]) -> List[str]:
    """根据领域配置选择优先用于 KPI 的数值列。"""
    if not numeric_cols:
        return []

    # 优先匹配价值类关键词
    value_cols = [c for c in numeric_cols if _column_matches_keywords(c, config["kpi_value_keywords"])]
    count_cols = [c for c in numeric_cols if _column_matches_keywords(c, config["kpi_count_keywords"])]
    others = [c for c in numeric_cols if c not in value_cols and c not in count_cols]

    selected = (value_cols[:2] + count_cols[:2] + others)[:4]
    # 如果偏好没命中，取前 4 个
    if not selected:
        selected = numeric_cols[:4]
    return selected


def _pick_date_column(cols: List[str], config: Dict[str, Any]) -> Optional[str]:
    """根据领域配置选择日期列。"""
    for col in cols:
        if _column_matches_keywords(col, config["date_keywords"]):
            return col
    return None


def _pick_category_column(text_cols: List[str], numeric_cols: List[str], config: Dict[str, Any]) -> Optional[str]:
    """根据领域配置选择分类/维度列。"""
    candidates = text_cols + numeric_cols
    for col in candidates:
        if _column_matches_keywords(col, config["category_keywords"]):
            return col
    return text_cols[0] if text_cols else None


def _generate_kpi_cards(
    rows: List[Dict[str, Any]],
    numeric_cols: List[str],
    config: Dict[str, Any],
) -> str:
    """生成领域化 KPI 卡片 HTML。"""
    if not numeric_cols or not rows:
        return ""

    selected_cols = _pick_kpi_columns(numeric_cols, config)
    cards = []
    for col in selected_cols:
        values = [_to_float(row.get(col)) for row in rows]
        values = [v for v in values if v is not None]
        if not values:
            continue
        total = sum(values)
        avg = total / len(values)
        max_val = max(values)
        min_val = min(values)

        # 金额类展示合计，数量/计数类展示合计+平均
        is_amount = _column_matches_keywords(col, config["kpi_value_keywords"])
        is_count = _column_matches_keywords(col, config["kpi_count_keywords"])

        if is_amount or (not is_count and total != int(total)):
            display_value = f"{total:,.2f}" if total != int(total) else f"{total:,.0f}"
            subtitle = f"平均: {avg:,.2f} | 最大: {max_val:,.2f}"
        else:
            display_value = f"{total:,.0f}"
            subtitle = f"平均: {avg:,.2f} | 最大: {max_val:,.0f}"

        cards.append(
            f"""
            <div class="sap-db-kpi-card">
                <div class="sap-db-kpi-value">{display_value}</div>
                <div class="sap-db-kpi-label">{col}</div>
                <div class="sap-db-kpi-subtitle">{subtitle}</div>
            </div>
            """
        )

    if not cards:
        return ""

    return f'<div class="sap-db-kpi-grid">{ "".join(cards) }</div>'


def _generate_chart_grid(
    rows: List[Dict[str, Any]],
    intent: str,
    numeric_cols: List[str],
    text_cols: List[str],
    config: Dict[str, Any],
) -> str:
    """生成领域化图表网格 HTML。"""
    charts = []
    date_col = _pick_date_column(list(rows[0].keys()) if rows else [], config)
    category_col = _pick_category_column(text_cols, numeric_cols, config)
    value_col = _pick_kpi_columns(numeric_cols, config)[0] if numeric_cols else None

    # 1. 趋势图：按日期 + 优先数值列聚合
    if date_col and value_col and len(rows) > 1:
        from collections import OrderedDict
        trend_data: Dict[str, float] = OrderedDict()
        for row in sorted(rows, key=lambda r: str(r.get(date_col, ""))):
            d = row.get(date_col)
            v = _to_float(row.get(value_col))
            if d is None or v is None:
                continue
            k = str(d)
            trend_data[k] = trend_data.get(k, 0.0) + v
        if len(trend_data) >= 2:
            labels, values = zip(*trend_data.items())
            trend_rows = [{date_col: k, value_col: v} for k, v in trend_data.items()]
            chart = generate_chart(trend_rows, intent=intent, title=config["chart_titles"][0])
            if chart and chart.get("base64"):
                charts.append(_chart_card(chart))

    # 2. TOP N 分类对比
    if category_col and value_col and len(rows) > 1:
        from collections import defaultdict
        agg: Dict[str, float] = defaultdict(float)
        for row in rows:
            cat = row.get(category_col)
            v = _to_float(row.get(value_col))
            if cat is None or v is None:
                continue
            agg[str(cat)] += v
        if len(agg) >= 2:
            top_items = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:15]
            top_rows = [{category_col: k, value_col: v} for k, v in top_items]
            chart = generate_chart(top_rows, intent=intent, title=config["chart_titles"][1])
            if chart and chart.get("base64"):
                charts.append(_chart_card(chart))

    # 3. 结构占比图（饼图）—— 如果分类列与数值列都存在且类别不多
    if category_col and value_col and len(rows) > 1:
        from collections import defaultdict
        agg2: Dict[str, float] = defaultdict(float)
        for row in rows:
            cat = row.get(category_col)
            v = _to_float(row.get(value_col))
            if cat is None or v is None:
                continue
            agg2[str(cat)] += v
        if 2 <= len(agg2) <= 10:
            pie_rows = [{category_col: k, value_col: v} for k, v in agg2.items()]
            chart = generate_chart(pie_rows, intent=intent, title=config["chart_titles"][2])
            if chart and chart.get("base64"):
                charts.append(_chart_card(chart))

    if not charts:
        return '<div class="sap-db-empty">数据不足，无法生成图表</div>'

    return f'<div class="sap-db-chart-grid">{ "".join(charts) }</div>'


def _chart_card(chart: Dict[str, Any]) -> str:
    return f"""
    <div class="sap-db-chart-card">
        <div class="sap-db-chart-title">{chart['title']}</div>
        <img src="data:image/png;base64,{chart['base64']}" class="sap-db-chart-img" />
    </div>
    """


def _generate_insights_section(insights_result: Dict[str, Any], config: Dict[str, Any]) -> str:
    """生成洞察摘要 HTML。"""
    insights = insights_result.get("insights", [])[:5]
    recommendations = insights_result.get("recommendations", [])[:5]

    if not insights and not recommendations:
        return ""

    focus_tags = " · ".join(config.get("insight_focus", []))
    html = '<div class="sap-db-insights-section">'
    html += f'<div class="sap-db-section-title">数据洞察 <span class="sap-db-focus">({focus_tags})</span></div>'
    html += f'<div class="sap-db-summary">{insights_result.get("summary", "")}</div>'

    if insights:
        html += '<div class="sap-db-insight-list">'
        for idx, item in enumerate(insights, 1):
            severity = item.get("severity", "info")
            html += f"""
            <div class="sap-db-insight-item sap-db-severity-{severity}">
                <div class="sap-db-insight-title">{idx}. {item.get('title', '')}</div>
                <div class="sap-db-insight-desc">{item.get('description', '')}</div>
            </div>
            """
        html += "</div>"

    if recommendations:
        html += '<div class="sap-db-recommendations"><div class="sap-db-section-title">建议</div><ul>'
        for rec in recommendations:
            html += f"<li>{rec}</li>"
        html += "</ul></div>"

    html += "</div>"
    return html


def _generate_table_section(rows: List[Dict[str, Any]]) -> str:
    """生成明细表格 HTML。"""
    if not rows:
        return ""

    headers = list(rows[0].keys())
    html = '<div class="sap-db-section-title">明细数据（前 20 行）</div>'
    html += '<div class="sap-db-table-wrap"><table class="sap-db-table"><thead><tr>'
    for h in headers:
        html += f"<th>{h}</th>"
    html += "</tr></thead><tbody>"

    for row in rows[:20]:
        html += "<tr>"
        for h in headers:
            v = row.get(h, "")
            html += f"<td>{v if v is not None else ''}</td>"
        html += "</tr>"
    html += "</tbody></table></div>"

    return html


def _dashboard_html_template(title: str, content: str, generated_at: str, gradient: str, theme: str) -> str:
    """看板 HTML 模板。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
            margin: 0;
            padding: 24px;
            background: #f8fafc;
            color: #1e293b;
        }}
        .sap-db-container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .sap-db-header {{
            background: {gradient};
            color: white;
            padding: 28px 32px;
            border-radius: 16px;
            margin-bottom: 24px;
        }}
        .sap-db-header h1 {{ margin: 0 0 8px 0; font-size: 24px; font-weight: 600; }}
        .sap-db-header .sap-db-meta {{ font-size: 13px; opacity: 0.9; }}
        .sap-db-section-title {{
            font-size: 16px;
            font-weight: 600;
            color: #334155;
            margin: 24px 0 16px 0;
            padding-left: 12px;
            border-left: 4px solid {theme};
        }}
        .sap-db-focus {{
            font-size: 12px;
            color: #64748b;
            font-weight: 400;
        }}
        .sap-db-kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .sap-db-kpi-card {{
            background: white;
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid #e2e8f0;
        }}
        .sap-db-kpi-value {{
            font-size: 28px;
            font-weight: 700;
            color: {theme};
            margin-bottom: 6px;
        }}
        .sap-db-kpi-label {{
            font-size: 13px;
            color: #64748b;
            margin-bottom: 4px;
        }}
        .sap-db-kpi-subtitle {{
            font-size: 12px;
            color: #94a3b8;
        }}
        .sap-db-chart-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }}
        .sap-db-chart-card {{
            background: white;
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid #e2e8f0;
        }}
        .sap-db-chart-title {{
            font-size: 14px;
            font-weight: 600;
            color: #334155;
            margin-bottom: 12px;
        }}
        .sap-db-chart-img {{
            width: 100%;
            height: auto;
            border-radius: 8px;
        }}
        .sap-db-insights-section {{
            background: white;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid #e2e8f0;
        }}
        .sap-db-summary {{
            background: #ecfeff;
            border-left: 4px solid {theme};
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 13px;
            color: #0e7490;
            margin-bottom: 16px;
        }}
        .sap-db-insight-list {{
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        .sap-db-insight-item {{
            padding: 12px 16px;
            border-radius: 10px;
            background: #f8fafc;
            border-left: 3px solid #cbd5e1;
        }}
        .sap-db-insight-item.sap-db-severity-warning {{ border-left-color: #f59e0b; background: #fffbeb; }}
        .sap-db-insight-item.sap-db-severity-critical {{ border-left-color: #ef4444; background: #fef2f2; }}
        .sap-db-insight-item.sap-db-severity-info {{ border-left-color: {theme}; background: #ecfeff; }}
        .sap-db-insight-title {{ font-size: 13px; font-weight: 600; color: #334155; margin-bottom: 4px; }}
        .sap-db-insight-desc {{ font-size: 12px; color: #64748b; }}
        .sap-db-recommendations {{ margin-top: 20px; }}
        .sap-db-recommendations ul {{ padding-left: 18px; margin: 0; }}
        .sap-db-recommendations li {{ font-size: 13px; color: #475569; margin-bottom: 6px; }}
        .sap-db-table-wrap {{
            background: white;
            border-radius: 16px;
            padding: 16px;
            overflow-x: auto;
            border: 1px solid #e2e8f0;
        }}
        .sap-db-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        .sap-db-table th {{
            text-align: left;
            padding: 10px 12px;
            background: #f1f5f9;
            color: #475569;
            font-weight: 600;
            white-space: nowrap;
        }}
        .sap-db-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid #f1f5f9;
            color: #334155;
            white-space: nowrap;
        }}
        .sap-db-empty {{
            text-align: center;
            padding: 40px;
            color: #94a3b8;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="sap-db-container">
        <div class="sap-db-header">
            <h1>{title}</h1>
            <div class="sap-db-meta">生成时间: {generated_at}</div>
        </div>
        {content}
    </div>
</body>
</html>"""


def build_dashboard(
    rows: List[Dict[str, Any]],
    intent: str = "",
    domain: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """生成 SAP 数据分析看板，返回 {html, title, insights}。

    根据 domain 自动选择领域化配色、KPI 重点、图表组合和洞察方向。
    """
    if not rows:
        return {"html": "", "title": title or "数据看板", "insights": {}}

    config = _get_domain_config(domain)
    generated_at = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display_title = title or config["title"]

    numeric_cols = _infer_numeric_columns(rows)
    text_cols = _infer_text_columns(rows, numeric_cols)

    # 生成洞察
    insights_result = generate_insights_for_sap_rows(rows)

    # 组装内容
    parts = []
    parts.append(_generate_kpi_cards(rows, numeric_cols, config))
    parts.append(_generate_chart_grid(rows, intent, numeric_cols, text_cols, config))
    parts.append(_generate_insights_section(insights_result, config))
    parts.append(_generate_table_section(rows))

    content = "".join(parts)
    html = _dashboard_html_template(
        display_title, content, generated_at, gradient=config["gradient"], theme=config["theme"]
    )

    return {"html": html, "title": display_title, "insights": insights_result}


def build_finance_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成财务专用看板。"""
    return build_dashboard(rows, intent=intent, domain="finance", title="财务数据分析看板")


def build_sales_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成销售专用看板。"""
    return build_dashboard(rows, intent=intent, domain="sales", title="销售数据分析看板")


def build_procurement_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成采购专用看板。"""
    return build_dashboard(rows, intent=intent, domain="procurement", title="采购数据分析看板")


def build_inventory_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成库存专用看板。"""
    return build_dashboard(rows, intent=intent, domain="inventory", title="库存数据分析看板")


def build_production_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成生产专用看板。"""
    return build_dashboard(rows, intent=intent, domain="production", title="生产数据分析看板")


def build_master_data_dashboard(rows: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """生成主数据专用看板。"""
    return build_dashboard(rows, intent=intent, domain="master_data", title="主数据分析看板")