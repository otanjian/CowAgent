#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate achievable delivery date based on inventory and capacity."""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
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


def read_excel_or_json(file_path: str) -> list:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        if pd is None:
            raise RuntimeError("pandas and openpyxl are required")
        df = pd.read_excel(str(path))
        return df.to_dict("records")
    if suffix == ".json":
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else data.get("items", [])
    raise ValueError(f"Unsupported file format: {suffix}")


def evaluate_delivery(inquiry: dict, inventory: list, capacity: dict) -> dict:
    quantity = max(inquiry.get("quantity", 1), 1)
    target = inquiry.get("target_delivery", "")

    # Material availability check
    material_shortages = []
    available_material_days = 0
    for item in inventory:
        available = float(item.get("可用库存", item.get("available", item.get("库存", 0))))
        required_per_unit = float(item.get("需求数量", item.get("required", item.get("需求", 0))))
        required = required_per_unit * quantity
        lead_time = int(item.get("采购周期", item.get("lead_time", item.get("提前期", 0))))
        if required > 0 and available < required:
            shortage = required - available
            material_shortages.append({
                "物料编码": item.get("物料编码", item.get("code", "")),
                "物料名称": item.get("物料名称", item.get("name", "")),
                "单件需求": required_per_unit,
                "总需求": required,
                "可用库存": available,
                "缺口": shortage,
                "采购周期(天)": lead_time
            })
            available_material_days = max(available_material_days, lead_time)

    # Capacity check
    daily_capacity = float(capacity.get("日产能", capacity.get("daily_capacity", capacity.get("产能", 1000))))
    current_load = float(capacity.get("当前负荷", capacity.get("current_load", 0)))
    available_capacity = max(daily_capacity - current_load, daily_capacity * 0.3)
    production_days = math.ceil(quantity / available_capacity)

    # Standard buffer
    buffer_days = int(capacity.get("缓冲天数", capacity.get("buffer_days", 2)))
    total_lead_days = available_material_days + production_days + buffer_days

    # Calculate promised date
    start_date = datetime.now()
    promised_date = (start_date + timedelta(days=total_lead_days)).strftime("%Y-%m-%d")

    # Compare with target
    target_ok = True
    delay_days = 0
    if target:
        try:
            target_date = datetime.strptime(target, "%Y-%m-%d")
            actual_date = datetime.strptime(promised_date, "%Y-%m-%d")
            if actual_date > target_date:
                target_ok = False
                delay_days = (actual_date - target_date).days
        except ValueError:
            pass

    return {
        "quantity": quantity,
        "target_delivery": target,
        "promised_delivery": promised_date,
        "production_days": production_days,
        "material_wait_days": available_material_days,
        "buffer_days": buffer_days,
        "total_lead_days": total_lead_days,
        "daily_capacity": daily_capacity,
        "current_load": current_load,
        "available_capacity": available_capacity,
        "target_meet": target_ok,
        "delay_days": delay_days,
        "material_shortages": material_shortages,
        "suggestions": generate_suggestions(material_shortages, target_ok, delay_days)
    }


def generate_suggestions(shortages, target_ok, delay_days):
    suggestions = []
    if shortages:
        suggestions.append(f"存在 {len(shortages)} 项物料缺口，建议启动紧急采购或调拨")
    if not target_ok:
        suggestions.append(f"预计无法满足目标交期，将延期 {delay_days} 天，建议与客户协商或安排加班")
    if target_ok and not shortages:
        suggestions.append("库存和产能均满足要求，可按目标交期交付")
    return suggestions


def main():
    parser = argparse.ArgumentParser(description="Evaluate delivery date")
    parser.add_argument("--inquiry", required=True, help="Parsed inquiry JSON file")
    parser.add_argument("--inventory", required=True, help="Inventory Excel/JSON file")
    parser.add_argument("--capacity", required=True, help="Capacity Excel/JSON file")
    parser.add_argument("--output", default="tmp/delivery_evaluation.json", help="Output JSON path")
    args = parser.parse_args()

    inquiry = load_json_or_dict(args.inquiry)
    inventory = read_excel_or_json(args.inventory)
    capacity_raw = read_excel_or_json(args.capacity)
    # Capacity template usually has one record per product/process
    capacity = capacity_raw[0] if isinstance(capacity_raw, list) and capacity_raw else capacity_raw

    result = evaluate_delivery(inquiry, inventory, capacity)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Delivery evaluation saved to: {args.output}")
    print(f"Promised delivery: {result['promised_delivery']}, Target met: {result['target_meet']}")


if __name__ == "__main__":
    main()
