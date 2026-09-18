#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calculate product cost based on BOM and process route."""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    pd = None


def load_json_or_dict(value):
    if isinstance(value, dict):
        return value
    if Path(value).exists():
        with open(value, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(value)


def get_value(row: dict, keys: list, default=0):
    """Get first non-None value from row using multiple possible keys."""
    for key in keys:
        if key in row and row[key] is not None and str(row[key]) != "nan":
            return row[key]
    return default


def read_excel_or_json(file_path: str) -> list:
    """Read Excel or JSON file into list of dicts."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        if pd is None:
            raise RuntimeError("pandas and openpyxl are required for Excel files")
        df = pd.read_excel(str(path))
        return df.to_dict("records")
    if suffix == ".json":
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else data.get("items", [])
    raise ValueError(f"Unsupported file format: {suffix}")


def calculate_cost(inquiry: dict, bom_items: list, process_steps: list, rates: dict) -> dict:
    """Calculate total cost breakdown."""
    quantity = max(inquiry.get("quantity", 1), 1)

    # Material cost (usage is per-unit)
    material_cost = 0.0
    material_details = []
    for item in bom_items:
        usage = float(get_value(item, ["用量", "qty", "quantity"]))
        unit_price = float(get_value(item, ["单价", "price", "unit_price"]))
        loss_rate = float(get_value(item, ["损耗率", "损耗率(%)", "loss_rate", "损耗"])) / 100
        cost = usage * unit_price * (1 + loss_rate) * quantity
        material_cost += cost
        material_details.append({
            "物料编码": item.get("物料编码", item.get("code", "")),
            "物料名称": item.get("物料名称", item.get("name", "")),
            "单件用量": usage,
            "单价": unit_price,
            "损耗率": loss_rate * 100,
            "总成本": round(cost, 2)
        })

    # Labor and manufacturing cost (hours are per-unit)
    labor_cost = 0.0
    overhead_cost = 0.0
    process_details = []
    for step in process_steps:
        hours = float(get_value(step, ["工时", "工时(小时)", "hours", "工时定额"]))
        labor_rate = float(get_value(step, ["人工费率", "人工费率(元/小时)", "labor_rate", "人工费"]))
        overhead_rate = float(get_value(step, ["制造费用率", "制造费用率(元/小时)", "overhead_rate", "制造费"]))
        lc = hours * labor_rate * quantity
        oc = hours * overhead_rate * quantity
        labor_cost += lc
        overhead_cost += oc
        process_details.append({
            "工序": step.get("工序", step.get("operation", "")),
            "设备": step.get("设备", step.get("equipment", "")),
            "单件工时": hours,
            "人工费率": labor_rate,
            "制造费用率": overhead_rate,
            "人工成本": round(lc, 2),
            "制造费用": round(oc, 2)
        })

    direct_cost = material_cost + labor_cost + overhead_cost
    admin_rate = float(rates.get("管理费率", rates.get("admin_rate", rates.get("管理费用率", 0.1))))
    admin_cost = direct_cost * admin_rate
    total_cost = direct_cost + admin_cost

    # Per-unit cost
    unit_material = material_cost / quantity
    unit_labor = labor_cost / quantity
    unit_overhead = overhead_cost / quantity
    unit_admin = admin_cost / quantity
    unit_cost = total_cost / quantity

    return {
        "quantity": quantity,
        "currency": rates.get("币种", "CNY"),
        "material_cost": round(material_cost, 2),
        "labor_cost": round(labor_cost, 2),
        "overhead_cost": round(overhead_cost, 2),
        "admin_cost": round(admin_cost, 2),
        "total_cost": round(total_cost, 2),
        "unit_cost": round(unit_cost, 4),
        "unit_material": round(unit_material, 4),
        "unit_labor": round(unit_labor, 4),
        "unit_overhead": round(unit_overhead, 4),
        "unit_admin": round(unit_admin, 4),
        "material_details": material_details,
        "process_details": process_details
    }


def main():
    parser = argparse.ArgumentParser(description="Calculate product cost")
    parser.add_argument("--inquiry", required=True, help="Parsed inquiry JSON file")
    parser.add_argument("--bom", required=True, help="BOM Excel/JSON file")
    parser.add_argument("--process", required=True, help="Process route Excel/JSON file")
    parser.add_argument("--rates", default="{}", help="Cost rates JSON file or JSON string")
    parser.add_argument("--output", default="tmp/cost_calculation.json", help="Output JSON path")
    args = parser.parse_args()

    inquiry = load_json_or_dict(args.inquiry)
    bom_items = read_excel_or_json(args.bom)
    process_steps = read_excel_or_json(args.process)
    rates = load_json_or_dict(args.rates) if args.rates != "{}" else {"管理费率": 0.1}

    result = calculate_cost(inquiry, bom_items, process_steps, rates)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Cost calculation saved to: {args.output}")
    print(f"Total cost: {result['total_cost']:.2f}, Unit cost: {result['unit_cost']:.4f}")


if __name__ == "__main__":
    main()
