#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare multiple supplier quotes and generate scoring."""

import argparse
import json
import re
import sys
from pathlib import Path
from statistics import mean

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


def parse_days(delivery_text: str) -> int:
    """Extract number of days from delivery text."""
    if not delivery_text:
        return 30
    m = re.search(r"(\d+)", str(delivery_text))
    return int(m.group(1)) if m else 30


def score_payment_terms(terms: str) -> int:
    """Score payment terms (higher is better for buyer)."""
    terms = str(terms)
    if "预付" in terms or "发货前" in terms:
        return 90
    if "货到付款" in terms:
        return 85
    if "90" in terms:
        return 40
    if "60" in terms:
        return 60
    if "30" in terms:
        return 75
    return 70


def score_delivery(days: int) -> int:
    """Score delivery period (shorter is better)."""
    if days <= 7:
        return 95
    if days <= 15:
        return 85
    if days <= 25:
        return 70
    if days <= 35:
        return 55
    return 40


def score_quality(warranty: str, history_quality: float = 0) -> int:
    """Score quality based on warranty and historical quality."""
    if history_quality > 0:
        return min(int(history_quality), 100)
    warranty_months = 12
    m = re.search(r"(\d+)", str(warranty))
    if m:
        warranty_months = int(m.group(1))
    if warranty_months >= 24:
        return 90
    if warranty_months >= 12:
        return 80
    if warranty_months >= 6:
        return 65
    return 50


def score_service(cooperation_level: str = "", payment_score: int = 70) -> int:
    """Score service based on cooperation level and payment terms."""
    level_scores = {"战略": 95, "核心": 85, "合格": 75, "临时": 60, "": 70}
    level_score = level_scores.get(cooperation_level, 70)
    return int((level_score + payment_score) / 2)


def build_item_matrix(quotes: list, history_prices: dict) -> list:
    """Build comparison matrix for each unique item."""
    # Collect all unique item keys
    item_map = {}
    for quote in quotes:
        supplier = quote.get("supplier", "未知")
        for item in quote.get("items", []):
            key = item.get("物料编码", "") or item.get("物料名称", "")
            if not key:
                continue
            if key not in item_map:
                item_map[key] = {
                    "物料编码": item.get("物料编码", ""),
                    "物料名称": item.get("物料名称", ""),
                    "规格": item.get("规格", ""),
                    "suppliers": {}
                }
            item_map[key]["suppliers"][supplier] = {
                "单价": item.get("单价", 0),
                "总价": item.get("总价", 0),
                "数量": item.get("数量", 1),
                "单位": item.get("单位", "件"),
                "交期": item.get("交期", ""),
                "付款条件": item.get("付款条件", ""),
                "质保期": item.get("质保期", ""),
                "起订量": item.get("起订量", 1),
                "税率": item.get("税率", "13%")
            }

    matrix = []
    for key, data in item_map.items():
        supplier_prices = {s: info["单价"] for s, info in data["suppliers"].items() if info["单价"] > 0}
        if not supplier_prices:
            continue

        min_price = min(supplier_prices.values())
        max_price = max(supplier_prices.values())
        hist_price = history_prices.get(key, 0)

        row = {
            "物料编码": data["物料编码"],
            "物料名称": data["物料名称"],
            "规格": data["规格"],
            "历史采购价": hist_price,
            "最低价": min_price,
            "最高价": max_price,
            "供应商报价": data["suppliers"],
            "建议目标价": round(min(hist_price * 0.98 if hist_price > 0 else float('inf'), min_price * 1.02), 2) if hist_price > 0 else round(min_price * 0.98, 2),
            "议价上限": round(min_price * 1.05, 2),
            "理想价": round(min_price * 0.95, 2)
        }

        # Generate suggestion
        if hist_price > 0 and min_price > hist_price * 1.05:
            row["建议"] = f"本次最低价比历史价高 {(min_price/hist_price-1)*100:.1f}%，重点议价"
        elif hist_price > 0 and min_price < hist_price * 0.90:
            row["建议"] = "本次最低价比历史价低，注意质量/交付风险"
        else:
            cheapest_supplier = min(supplier_prices, key=supplier_prices.get)
            row["建议"] = f"选择 {cheapest_supplier} 或继续议价"

        matrix.append(row)

    return matrix


def build_supplier_scores(quotes: list, matrix: list, supplier_profiles: dict, weights: dict) -> list:
    """Build supplier score cards."""
    supplier_names = [q.get("supplier", f"供应商{i+1}") for i, q in enumerate(quotes)]

    # Calculate average price ratio for each supplier
    supplier_prices = {s: [] for s in supplier_names}
    for row in matrix:
        for supplier, info in row["供应商报价"].items():
            supplier_prices[supplier].append(info["单价"])

    scores = []
    for supplier in supplier_names:
        prices = supplier_prices.get(supplier, [])
        avg_price = mean(prices) if prices else 0

        # Price score: compared to lowest price in matrix
        all_prices = [info["单价"] for row in matrix for info in row["供应商报价"].values() if info["单价"] > 0]
        min_avg = mean(all_prices) * 0.9 if all_prices else avg_price
        price_score = max(0, min(100, int(100 - (avg_price - min_avg) / max(min_avg, 1) * 50)))

        # Delivery score
        delivery_days = []
        for q in quotes:
            if q.get("supplier") == supplier:
                for item in q.get("items", []):
                    days = parse_days(item.get("交期", ""))
                    delivery_days.append(days)
        avg_days = mean(delivery_days) if delivery_days else 30
        delivery_score = score_delivery(avg_days)

        # Quality score
        warranties = []
        for q in quotes:
            if q.get("supplier") == supplier:
                for item in q.get("items", []):
                    warranties.append(item.get("质保期", "12个月"))
        avg_quality = mean([score_quality(w) for w in warranties]) if warranties else 75
        profile = supplier_profiles.get(supplier, {})
        if profile.get("历史评分"):
            avg_quality = int((avg_quality + float(profile["历史评分"])) / 2)

        # Service score
        payment_scores = []
        for q in quotes:
            if q.get("supplier") == supplier:
                payment_scores.append(score_payment_terms(q.get("payment_terms", "")))
                for item in q.get("items", []):
                    payment_scores.append(score_payment_terms(item.get("付款条件", "")))
        avg_payment = mean(payment_scores) if payment_scores else 70
        service_score = score_service(profile.get("合作等级", ""), int(avg_payment))

        # Weighted total
        total = round(
            price_score * weights.get("价格", 0.4) +
            delivery_score * weights.get("交期", 0.25) +
            avg_quality * weights.get("质量", 0.2) +
            service_score * weights.get("服务", 0.15),
            1
        )

        scores.append({
            "供应商": supplier,
            "价格得分": price_score,
            "交期得分": delivery_score,
            "质量得分": avg_quality,
            "服务得分": service_score,
            "综合得分": total,
            "平均报价": round(avg_price, 2),
            "平均交期": round(avg_days, 1)
        })

    scores.sort(key=lambda x: x["综合得分"], reverse=True)
    for i, s in enumerate(scores):
        s["排名"] = i + 1

    return scores


def generate_risks(matrix: list, supplier_scores: list) -> list:
    """Generate risk warnings."""
    risks = []

    for row in matrix:
        hist_price = row.get("历史采购价", 0)
        min_price = row.get("最低价", 0)
        if hist_price > 0 and min_price > hist_price * 1.15:
            risks.append({
                "类型": "价格异常",
                "描述": f"{row['物料名称']} 本次最低价 {min_price} 元显著高于历史价 {hist_price} 元",
                "等级": "高"
            })
        if hist_price > 0 and min_price < hist_price * 0.85:
            risks.append({
                "类型": "价格异常",
                "描述": f"{row['物料名称']} 本次最低价 {min_price} 元显著低于历史价 {hist_price} 元，需警惕质量风险",
                "等级": "中"
            })

    for s in supplier_scores:
        if s["交期得分"] < 60:
            risks.append({
                "类型": "交期风险",
                "描述": f"{s['供应商']} 平均交期 {s['平均交期']} 天，交付能力不足",
                "等级": "中"
            })
        if s["服务得分"] < 60:
            risks.append({
                "类型": "付款风险",
                "描述": f"{s['供应商']} 付款条件不利，资金占用成本高",
                "等级": "中"
            })

    if len(supplier_scores) == 1:
        risks.append({
            "类型": "供应风险",
            "描述": "仅有一家供应商报价，存在单一来源风险",
            "等级": "高"
        })

    return risks


def main():
    parser = argparse.ArgumentParser(description="Compare supplier quotes")
    parser.add_argument("--quotes", required=True, help="Comma-separated parsed quote JSON files")
    parser.add_argument("--history", help="Historical price Excel/JSON file")
    parser.add_argument("--supplier-profile", help="Supplier profile Excel/JSON file")
    parser.add_argument("--weights", default="{}", help="Scoring weights JSON")
    parser.add_argument("--output", default="tmp/comparison_result.json", help="Output JSON path")
    args = parser.parse_args()

    # Load quotes
    quote_files = [f.strip() for f in args.quotes.split(",")]
    quotes = []
    for f in quote_files:
        quotes.append(load_json_or_dict(f))

    # Load history prices
    history_prices = {}
    if args.history:
        history_items = read_excel_or_json(args.history)
        for item in history_items:
            key = item.get("物料编码", "") or item.get("物料名称", "")
            if key:
                history_prices[key] = float(item.get("历史采购价", item.get("price", 0)))

    # Load supplier profiles
    supplier_profiles = {}
    if args.supplier_profile:
        profiles = read_excel_or_json(args.supplier_profile)
        for p in profiles:
            name = p.get("供应商名称", p.get("supplier", ""))
            if name:
                supplier_profiles[name] = p

    # Load weights
    weights = load_json_or_dict(args.weights) if args.weights != "{}" else {
        "价格": 0.4,
        "交期": 0.25,
        "质量": 0.2,
        "服务": 0.15
    }

    matrix = build_item_matrix(quotes, history_prices)
    scores = build_supplier_scores(quotes, matrix, supplier_profiles, weights)
    risks = generate_risks(matrix, scores)

    result = {
        "quotes_count": len(quotes),
        "suppliers": [q.get("supplier", "") for q in quotes],
        "comparison_matrix": matrix,
        "supplier_scores": scores,
        "risks": risks,
        "recommendations": generate_recommendations(matrix, scores)
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Comparison result saved to: {args.output}")
    if scores:
        print(f"Top supplier: {scores[0]['供应商']} (score: {scores[0]['综合得分']})")


def generate_recommendations(matrix: list, scores: list) -> list:
    """Generate purchase recommendations."""
    recs = []
    for row in matrix:
        supplier_prices = row.get("供应商报价", {})
        if not supplier_prices:
            continue
        cheapest = min(supplier_prices.items(), key=lambda x: x[1]["单价"])
        recs.append({
            "物料": row["物料名称"],
            "建议供应商": cheapest[0],
            "建议单价": cheapest[1]["单价"],
            "目标价": row.get("建议目标价", cheapest[1]["单价"]),
            "理由": f"{cheapest[0]} 本次报价最低"
        })

    if scores:
        recs.append({
            "物料": "综合评估",
            "建议供应商": scores[0]["供应商"],
            "建议单价": scores[0]["平均报价"],
            "目标价": scores[0]["平均报价"],
            "理由": f"综合得分最高 ({scores[0]['综合得分']} 分)"
        })

    return recs


if __name__ == "__main__":
    main()
