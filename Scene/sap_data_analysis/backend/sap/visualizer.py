"""SAP 数据分析结果可视化。

基于 matplotlib 生成简单图表，输出 base64 PNG，供工作台“图表”模式使用。
后续看板模式在此基础上组合多个图表与 KPI 卡片。
"""

import base64
import io
import re
from typing import Any, Dict, List, Optional

from common.log import logger

# matplotlib 中文与样式处理
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _MATPLOTLIB_AVAILABLE = True
except Exception as e:
    logger.warning(f"[SAPVisualizer] matplotlib not available: {e}")
    _MATPLOTLIB_AVAILABLE = False


# 青绿/蓝色调商务配色
PALETTE = ["#06b6d4", "#3b82f6", "#10b981", "#6366f1", "#14b8a6", "#0ea5e9"]


def _detect_column_types(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """根据数据样本推断每列类型：date / number / text。"""
    if not rows:
        return {}
    result = {}
    headers = list(rows[0].keys())
    for h in headers:
        values = [row.get(h) for row in rows if row.get(h) not in (None, "")]
        if not values:
            result[h] = "text"
            continue

        # 日期检测：列名或值匹配日期格式
        if _is_date_column(h, values):
            result[h] = "date"
            continue

        # 数值检测
        if all(_is_number(v) for v in values[:20]):
            result[h] = "number"
            continue

        result[h] = "text"
    return result


def _is_date_column(name: str, values: List[Any]) -> bool:
    name_lower = name.lower()
    date_names = ["date", "dt", "日期", "时间", "年月", "period", "audat", "fkdat", "bedat", "bldat", "erdat", "ersda"]
    if any(kw in name_lower for kw in date_names):
        return True
    date_patterns = [
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{4}/\d{2}/\d{2}$",
        r"^\d{8}$",
    ]
    sample = str(values[0]) if values else ""
    return any(re.match(p, sample) for p in date_patterns)


def _is_number(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _to_number(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = v.strip().replace(",", "").replace(" ", "")
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _pick_columns(rows: List[Dict[str, Any]], types: Dict[str, str]) -> Dict[str, str]:
    """根据类型选择标签列和数值列。"""
    headers = list(rows[0].keys()) if rows else []
    date_cols = [h for h, t in types.items() if t == "date"]
    number_cols = [h for h, t in types.items() if t == "number"]
    text_cols = [h for h, t in types.items() if t == "text"]

    # 优先选择名字像金额/数量的数值列
    amount_names = ["金额", "amount", "净值", "netwr", "brtwr", "销售额", "采购额", "value", "total", "数量", "menge"]
    primary_value = None
    for h in number_cols:
        h_lower = h.lower()
        if any(kw in h_lower for kw in amount_names):
            primary_value = h
            break
    if not primary_value and number_cols:
        primary_value = number_cols[0]

    # 标签列：优先日期，其次文本列
    label_col = date_cols[0] if date_cols else (text_cols[0] if text_cols else None)

    return {
        "label": label_col,
        "value": primary_value,
        "date_cols": date_cols,
        "number_cols": number_cols,
        "text_cols": text_cols,
    }


def _configure_chinese_font():
    """设置中文字体，防止图表中文乱码。"""
    if not _MATPLOTLIB_AVAILABLE:
        return
    import matplotlib.pyplot as plt

    plt.rcParams["axes.unicode_minus"] = False
    font_candidates = ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    available = set([f.name for f in matplotlib.font_manager.fontManager.ttflist])
    for font in font_candidates:
        if font in available:
            plt.rcParams["font.sans-serif"] = [font] + plt.rcParams.get("font.sans-serif", [])
            break


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def _create_line_chart(labels: List[str], values: List[float], title: str = "趋势图") -> str:
    _configure_chinese_font()
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor="white")
    ax.plot(labels, values, color=PALETTE[0], marker="o", linewidth=2, markersize=6)
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1e293b")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.set_ylabel("数值", fontsize=10, color="#64748b")
    plt.xticks(rotation=30, ha="right", fontsize=9)
    plt.tight_layout()
    return _fig_to_base64(fig)


def _create_bar_chart(labels: List[str], values: List[float], title: str = "对比图") -> str:
    _configure_chinese_font()
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor="white")
    bars = ax.bar(labels, values, color=PALETTE[: len(labels)] + [PALETTE[0]] * max(0, len(labels) - len(PALETTE)))
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1e293b")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.set_ylabel("数值", fontsize=10, color="#64748b")
    # 在柱顶标注数值
    for bar in bars:
        height = bar.get_height()
        ax.annotate(
            f"{height:,.0f}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#475569",
        )
    plt.xticks(rotation=30, ha="right", fontsize=9)
    plt.tight_layout()
    return _fig_to_base64(fig)


def _create_pie_chart(labels: List[str], values: List[float], title: str = "占比图") -> str:
    _configure_chinese_font()
    fig, ax = plt.subplots(figsize=(7, 7), facecolor="white")
    colors = PALETTE[: len(labels)] + [PALETTE[i % len(PALETTE)] for i in range(max(0, len(labels) - len(PALETTE)))]
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        colors=colors,
        textprops={"fontsize": 10, "color": "#334155"},
    )
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1e293b")
    plt.tight_layout()
    return _fig_to_base64(fig)


def _create_kpi_card(value: float, label: str = "数值", unit: str = "") -> str:
    _configure_chinese_font()
    fig, ax = plt.subplots(figsize=(6, 3.5), facecolor="white")
    ax.text(0.5, 0.6, f"{value:,.2f}" if isinstance(value, float) else f"{value:,}",
            ha="center", va="center", fontsize=32, fontweight="bold", color=PALETTE[0],
            transform=ax.transAxes)
    unit_text = f" {unit}" if unit else ""
    ax.text(0.5, 0.3, f"{label}{unit_text}",
            ha="center", va="center", fontsize=14, color="#64748b",
            transform=ax.transAxes)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    plt.tight_layout()
    return _fig_to_base64(fig)


def generate_chart(
    rows: List[Dict[str, Any]],
    intent: str = "",
    title: str = "",
) -> Optional[Dict[str, Any]]:
    """根据数据自动生成合适图表，返回 {type, title, base64}。"""
    if not _MATPLOTLIB_AVAILABLE:
        return {"type": "error", "title": title or "图表生成失败", "base64": "", "message": "matplotlib 不可用"}
    if not rows:
        return {"type": "error", "title": title or "图表生成失败", "base64": "", "message": "无数据"}

    types = _detect_column_types(rows)
    cols = _pick_columns(rows, types)
    label_col = cols["label"]
    value_col = cols["value"]

    if not value_col:
        return {"type": "error", "title": title or "图表生成失败", "base64": "", "message": "未检测到数值列"}

    # 提取有效数据
    pairs = []
    for row in rows:
        v = _to_number(row.get(value_col))
        if v is None:
            continue
        label = row.get(label_col, "") if label_col else ""
        pairs.append((str(label), v))

    if not pairs:
        return {"type": "error", "title": title or "图表生成失败", "base64": "", "message": "无有效数值"}

    labels, values = zip(*pairs)
    labels = list(labels)
    values = list(values)

    chart_title = title or _guess_chart_title(intent, value_col)

    # 单值 → KPI 卡片
    if len(values) == 1:
        b64 = _create_kpi_card(values[0], label=chart_title)
        return {"type": "kpi", "title": chart_title, "base64": b64}

    # 日期标签 → 折线
    if label_col and types.get(label_col) == "date":
        b64 = _create_line_chart(labels, values, title=chart_title)
        return {"type": "line", "title": chart_title, "base64": b64}

    # 类别数量少 → 饼图
    if len(labels) <= 6:
        b64 = _create_pie_chart(labels, values, title=chart_title)
        return {"type": "pie", "title": chart_title, "base64": b64}

    # 默认柱状
    b64 = _create_bar_chart(labels, values, title=chart_title)
    return {"type": "bar", "title": chart_title, "base64": b64}


def _guess_chart_title(intent: str, value_col: str) -> str:
    if "销售" in intent or "sales" in intent:
        return "销售分析"
    if "采购" in intent or "purchase" in intent or "procurement" in intent:
        return "采购分析"
    if "库存" in intent or "inventory" in intent:
        return "库存分析"
    if "生产" in intent or "production" in intent:
        return "生产分析"
    if "财务" in intent or "finance" in intent:
        return "财务分析"
    if "客户" in intent:
        return "客户分析"
    if "供应商" in intent:
        return "供应商分析"
    return value_col
