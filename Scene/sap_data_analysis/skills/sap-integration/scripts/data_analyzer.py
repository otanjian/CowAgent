"""SAP 数据分析 - 数据文件二次分析器。

读取后端生成的 JSON 数据文件，执行分组、聚合、统计、趋势分析、异常识别，
并输出分析结果或 Python/Pandas 分析脚本建议。

本脚本不连接 SAP，不处理连接参数。
"""

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List


def load_data_file(file_path: str) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_likely_id_field(field_name: str) -> bool:
    """根据字段名判断是否可能是 ID/编码类字段，避免误识别为数值。"""
    lower = field_name.lower()
    keywords = ["编码", "代码", "号", "id", "编号", "key"]
    return any(kw in lower for kw in keywords)


def summarize(payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = payload.get("data", [])
    meta = payload.get("meta", {})
    if not rows:
        return {"total_rows": 0, "columns": [], "note": "数据为空"}

    columns = list(rows[0].keys())
    numeric_columns = []
    for col in columns:
        if _is_likely_id_field(col):
            continue
        if any(isinstance(_to_number(row.get(col)), (int, float)) for row in rows):
            numeric_columns.append(col)

    summary = {
        "question": meta.get("question", ""),
        "total_rows": len(rows),
        "columns": columns,
        "numeric_columns": numeric_columns,
    }

    for col in numeric_columns:
        values = [_to_number(row.get(col)) for row in rows]
        values = [v for v in values if v is not None]
        if values:
            summary[col] = {
                "sum": round(sum(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "count": len(values),
            }
    return summary


def group_by(payload: Dict[str, Any], group_field: str, agg_field: str) -> Dict[str, Any]:
    rows = payload.get("data", [])
    groups: Dict[str, List[float]] = {}
    for row in rows:
        key = str(row.get(group_field, "未知"))
        val = _to_number(row.get(agg_field))
        if val is None:
            continue
        groups.setdefault(key, []).append(val)

    result = {
        "group_field": group_field,
        "agg_field": agg_field,
        "groups": [
            {
                "key": k,
                "count": len(v),
                "sum": round(sum(v), 2),
                "avg": round(sum(v) / len(v), 2) if v else 0,
                "min": round(min(v), 2) if v else 0,
                "max": round(max(v), 2) if v else 0,
            }
            for k, v in sorted(groups.items(), key=lambda x: sum(x[1]), reverse=True)
        ],
    }
    return result


def trend_analysis(payload: Dict[str, Any], date_field: str, value_field: str) -> Dict[str, Any]:
    rows = payload.get("data", [])
    series: Dict[str, List[float]] = {}
    for row in rows:
        date_key = str(row.get(date_field, ""))[:6]  # YYYYMM
        if not date_key.isdigit() or len(date_key) != 6:
            continue
        val = _to_number(row.get(value_field))
        if val is None:
            continue
        series.setdefault(date_key, []).append(val)

    trend = [
        {"period": k, "sum": round(sum(v), 2), "count": len(v)}
        for k, v in sorted(series.items())
    ]
    return {"date_field": date_field, "value_field": value_field, "trend": trend}


def detect_outliers(payload: Dict[str, Any], field: str, threshold: float = 2.0) -> Dict[str, Any]:
    rows = payload.get("data", [])
    values = [_to_number(row.get(field)) for row in rows]
    values = [v for v in values if v is not None]
    if not values:
        return {"field": field, "outliers": []}

    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    std = variance ** 0.5

    outliers = []
    for row in rows:
        val = _to_number(row.get(field))
        if val is None:
            continue
        if abs(val - avg) > threshold * std:
            outliers.append({"row": row, "value": val, "deviation": round(val - avg, 2)})

    return {
        "field": field,
        "avg": round(avg, 2),
        "std": round(std, 2),
        "outliers": outliers[:20],
    }


def generate_python_script(payload: Dict[str, Any], task: str) -> str:
    file_path = payload.get("meta", {}).get("files", {}).get("json", "sap_data.json")
    return f"""# 基于 SAP 数据分析结果生成的 Pandas 分析脚本
import json
import pandas as pd

with open(r"{file_path}", "r", encoding="utf-8") as f:
    payload = json.load(f)

df = pd.DataFrame(payload["data"])
print(f"共 {{len(df)}} 行，{{len(df.columns)}} 列")
print(df.head())

# 任务：{task}
# 示例：按某列分组统计
# print(df.groupby("分类字段")["数值字段"].sum().sort_values(ascending=False))
"""


def explain_field(field_name: str) -> str:
    """解释常见 SAP 字段业务含义。"""
    explanations = {
        "KUNNR": "客户编码（KNA1/KUNNR）",
        "LIFNR": "供应商编码（LFA1/LIFNR）",
        "MATNR": "物料编码（MARA/MATNR）",
        "VBELN": "销售订单/发票凭证号",
        "EBELN": "采购订单号",
        "BANFN": "采购申请号",
        "ERDAT": "创建日期",
        "AUDAT": "订单日期",
        "FKDAT": "发票日期",
        "BEDAT": "采购订单日期",
        "BLDAT": "凭证日期",
        "BUKRS": "公司代码",
        "NETWR": "净值（Net Value）",
        "BRTWR": "总金额（Gross Value）",
        "WAERS": "货币代码",
        "MEINS": "基本计量单位",
        "MENGE": "数量",
    }
    return explanations.get(field_name.upper(), f"字段 {field_name} 的业务含义未收录，请结合 query_plan 与 SAP 数据字典确认。")


def _to_number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        cleaned = str(value).replace(",", "").strip()
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except (ValueError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="SAP 数据分析器")
    parser.add_argument("file", help="后端生成的 JSON 数据文件路径")
    parser.add_argument("--summary", action="store_true", help="输出整体统计摘要")
    parser.add_argument("--group", help="按指定字段分组")
    parser.add_argument("--agg", help="聚合字段（与 --group 配合使用）")
    parser.add_argument("--trend", help="趋势分析日期字段（与 --value 配合使用）")
    parser.add_argument("--value", help="趋势分析数值字段")
    parser.add_argument("--outliers", help="异常值检测字段")
    parser.add_argument("--explain", help="解释指定字段含义")
    parser.add_argument("--script", action="store_true", help="生成 Python 分析脚本")
    parser.add_argument("--task", default="通用分析", help="脚本任务描述")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(json.dumps({"status": "error", "message": f"文件不存在: {args.file}"}, ensure_ascii=False))
        sys.exit(1)

    payload = load_data_file(args.file)

    if args.explain:
        result = {"status": "success", "field": args.explain, "meaning": explain_field(args.explain)}
    elif args.summary:
        result = {"status": "success", "summary": summarize(payload)}
    elif args.group and args.agg:
        result = {"status": "success", "group": group_by(payload, args.group, args.agg)}
    elif args.trend and args.value:
        result = {"status": "success", "trend": trend_analysis(payload, args.trend, args.value)}
    elif args.outliers:
        result = {"status": "success", "outliers": detect_outliers(payload, args.outliers)}
    elif args.script:
        result = {"status": "success", "script": generate_python_script(payload, args.task)}
    else:
        result = {"status": "success", "summary": summarize(payload)}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
