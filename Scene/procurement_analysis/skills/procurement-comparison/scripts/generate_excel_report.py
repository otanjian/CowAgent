#!/usr/bin/env python3
"""按采购比价分析模板生成 Excel 报表，并对最高价、最低价做颜色标注。

用法：
    python generate_excel_report.py \
        --input tmp/ekbe_history.json \
        --template assets/upload_templates/采购比价分析模板.XLSX \
        --output tmp/采购比价分析结果.xlsx \
        --max-cols 10
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List
from collections import defaultdict

from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Alignment, Font, Border, Side
from openpyxl.utils import get_column_letter


# 最高价红色、最低价绿色
HIGH_PRICE_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
LOW_PRICE_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
HEADER_FILL = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")


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
    for rec in records:
        matnr = rec.get("MATNR") or rec.get("matnr") or ""
        if not matnr.strip():
            continue  # 跳过物料号为空的记录
        maktx = rec.get("TXZ01") or rec.get("maktx") or rec.get("txz01") or ""
        if matnr not in groups:
            groups[matnr] = {"matnr": matnr, "maktx": maktx, "records": []}
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
    """显示时去掉物料编码前导零。"""
    if isinstance(value, str) and value.isdigit():
        return value.lstrip("0") or "0"
    return value


def _write_report(
    materials: List[Dict[str, Any]],
    template_path: str,
    output_path: str,
    max_cols: int,
) -> None:
    wb = load_workbook(template_path)
    ws = wb.active

    # 取消所有合并单元格，避免 MergedCell 只读问题
    merged_ranges = list(ws.merged_cells.ranges)
    for merged_range in merged_ranges:
        ws.unmerge_cells(str(merged_range))

    # 清空模板全部列的内容和样式，防止残留"采购时间N"占位符
    max_clear_col = max(ws.max_column, 3 + max_cols)
    for row in ws.iter_rows(min_row=1, max_row=max(ws.max_row, 3), min_col=1, max_col=max_clear_col):
        for cell in row:
            if isinstance(cell, type(ws.cell(row=1, column=1))):
                cell.value = None
                cell.fill = PatternFill(fill_type=None)
                cell.border = Border()
                cell.font = Font()
                cell.alignment = Alignment()

    # 物料按编码升序
    materials = sorted(materials, key=lambda m: _strip_leading_zeros(m.get("matnr", "")))

    # 全局时间轴：收集所有物料所有记录的唯一日期，升序，截断 max_cols
    timeline_set: set = set()
    for m in materials:
        for r in m.get("records", []):
            d = r.get("aedat", "")
            if d:
                timeline_set.add(d)
    timeline: List[str] = sorted(timeline_set)[:max_cols]

    total_cols = max(3 + len(timeline), 3)

    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    # 第 1 行：标题（合并前先给所有单元格设置边框，避免 MergedCell 边框丢失）
    for col in range(1, total_cols + 1):
        ws.cell(row=1, column=col).border = thin_border
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    title_cell = ws.cell(row=1, column=1, value="采购历史记录")
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")

    # 第 2 行：表头（日期时间轴，升序，全部为真实日期）
    headers = ["序号", "物料代码", "短文本"] + timeline
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # 日期 → 时间轴列索引列表（同日期多条记录占多列）
    date_col_map: Dict[str, List[int]] = defaultdict(list)
    for i, d in enumerate(timeline):
        date_col_map[d].append(i)

    # 写入每个物料：按日期匹配到时间轴对应列
    for row_idx, material in enumerate(materials, 1):
        excel_row = row_idx + 2
        records = sorted(material.get("records", []), key=lambda r: r.get("aedat") or "")

        # 序号
        ws.cell(row=excel_row, column=1, value=row_idx).border = thin_border
        # 物料代码（显示时去掉前导零）
        ws.cell(row=excel_row, column=2, value=_strip_leading_zeros(material.get("matnr", ""))).border = thin_border
        # 短文本
        ws.cell(row=excel_row, column=3, value=material.get("maktx", "")).border = thin_border

        # 收集净价用于极值标注
        net_prices = [r["netpr"] for r in records if r.get("netpr") is not None]
        max_price = max(net_prices) if net_prices else None
        min_price = min(net_prices) if net_prices else None

        # 每物料独立计数：同日期多条记录依次占用该日期在时间轴中的各列
        used: Dict[str, int] = defaultdict(int)
        for rec in records:
            d = rec.get("aedat", "")
            cols = date_col_map.get(d)
            if not cols:
                continue  # 日期超出时间轴截断范围，忽略
            idx = used[d]
            if idx >= len(cols):
                continue
            used[d] += 1
            data_col = 4 + cols[idx]

            price = rec.get("netpr")
            price_cell = ws.cell(row=excel_row, column=data_col, value=price)
            price_cell.border = thin_border
            price_cell.number_format = "0.00"
            # 仅当存在不同价格时才标注极值；整列价格相同则不标色，避免视觉干扰
            if price is not None and max_price is not None and min_price is not None and max_price != min_price:
                if abs(price - max_price) < 1e-9:
                    price_cell.fill = HIGH_PRICE_FILL
                elif abs(price - min_price) < 1e-9:
                    price_cell.fill = LOW_PRICE_FILL

    # 自动调整列宽
    for col in range(1, total_cols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 35

    # 冻结首行和前三列
    ws.freeze_panes = "D3"

    # 确保报表区域内所有单元格都有细边框（包括无数据的空白单元格）
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=total_cols):
        for cell in row:
            if not cell.border or not cell.border.left or cell.border.left.style is None:
                cell.border = thin_border

    wb.save(output_path)


def main():
    parser = argparse.ArgumentParser(description="生成采购比价分析 Excel 报表")
    parser.add_argument("--input", required=True, help="EKPO 数据 JSON 文件路径")
    parser.add_argument("--template", required=True, help="Excel 模板路径")
    parser.add_argument("--output", required=True, help="输出 Excel 文件路径")
    parser.add_argument("--max-cols", type=int, default=100, help="时间轴最多展示多少列（默认100）")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"输入文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.template):
        print(f"模板文件不存在: {args.template}", file=sys.stderr)
        sys.exit(1)

    data = _load_ekbe_history(args.input)
    materials = _normalize_input(data)

    if not materials:
        print("没有物料数据可生成报表", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    _write_report(materials, args.template, args.output, args.max_cols)

    print(f"已生成 Excel 报表: {args.output}")
    print(f"物料数量: {len(materials)}")


if __name__ == "__main__":
    main()
