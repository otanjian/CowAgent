#!/usr/bin/env python3
"""从 SAP EKPO 采购订单行项目读取历史采购记录或读取已同步的 JSON 数据。

用法：
    # 模式 1：读取已同步的 JSON 数据（推荐，由工作台 ERP 同步生成）
    python fetch_ekbe_history.py \
        --input-json tmp/workbench/session_xxx/supplier_quote_comparison_ekpo_data.json \
        --output tmp/ekpo_history.json

    # 模式 2：直接从 SAP EKPO 取数（需要配置 SAP 连接）
    python fetch_ekbe_history.py \
        --connection SAP_PROD \
        --materials "1000000048,1000000049" \
        --start-date 2024-08-05 \
        --end-date 2026-08-05 \
        --output tmp/ekpo_history.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List


def _parse_materials(value: str) -> List[str]:
    """解析物料输入，支持逗号/换行/空格分隔。"""
    if not value:
        return []
    return [m.strip() for m in re.split(r"[,\s\n]+", value) if m.strip()]


def _pad_matnr(value: str) -> str:
    """纯数字物料编码前导补零至 18 位。"""
    return value.zfill(18) if re.match(r"^\d+$", value) else value


def _normalize_date(value: str) -> str:
    """统一日期格式为 YYYY-MM-DD。"""
    value = value.strip()
    if re.match(r"^\d{8}$", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _compute_net_price(record: Dict[str, Any]) -> float:
    """获取净价：EKPO 直接取 NETPR 字段。"""
    netpr = record.get("NETPR")
    if netpr is not None:
        return float(netpr)
    # 兜底：如果 NETPR 为空，用 NETWR / MENGE 计算
    netwr = record.get("NETWR") or 0
    menge = record.get("MENGE") or 0
    if not menge:
        return 0.0
    return float(netwr) / float(menge)


def _load_input_json(path: str) -> List[Dict[str, Any]]:
    """读取工作台同步生成的 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("records") or data.get("data") or []
    return data


def _aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按物料聚合 EKPO 记录。"""
    by_material: Dict[str, Dict[str, Any]] = {}

    for rec in records:
        matnr = rec.get("MATNR", "")
        aedat = rec.get("AEDAT", "")
        if not matnr or not aedat:
            continue

        menge = abs(float(rec.get("MENGE") or 0))
        net_price = _compute_net_price(rec)
        normalized_aedat = _normalize_date(str(aedat))

        entry = {
            "aedat": normalized_aedat,
            "netpr": net_price,
            "menge": menge,
            "netwr": abs(float(rec.get("NETWR") or 0)),
            "waers": rec.get("WAERS", ""),
            "ebeln": rec.get("EBELN", ""),
            "ebelp": rec.get("EBELP", ""),
        }

        if matnr not in by_material:
            by_material[matnr] = {
                "matnr": matnr,
                "maktx": rec.get("TXZ01", ""),
                "records": [],
            }
        by_material[matnr]["records"].append(entry)

    # 每个物料按日期升序排序
    for matnr in by_material:
        by_material[matnr]["records"].sort(key=lambda r: r["aedat"])

    return {
        "generated_at": datetime.now().isoformat(),
        "materials": list(by_material.values()),
    }


def _fetch_from_sap(args) -> List[Dict[str, Any]]:
    """通过后端 API 或直接 SAP 连接取数（模式 2）。

    当前优先通过 /api/procurement/erp-sync 调用，保持连接参数由后端管理。
    """
    raise NotImplementedError(
        "模式 2 暂未实现。请使用 --input-json 读取工作台同步后的 JSON 数据，"
        "或调用后端 /api/procurement/erp-sync 接口生成该文件。"
    )


def main():
    parser = argparse.ArgumentParser(description="抓取 SAP EKPO 采购订单行项目记录")
    parser.add_argument("--input-json", help="已同步的 EKPO JSON 文件路径")
    parser.add_argument("--connection", help="SAP 连接配置 ID")
    parser.add_argument("--materials", help="物料代码或短文本列表，逗号/换行分隔")
    parser.add_argument("--start-date", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--max-rows", type=int, default=1000, help="最大返回行数")
    parser.add_argument("--output", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    if args.input_json:
        if not os.path.exists(args.input_json):
            print(f"输入文件不存在: {args.input_json}", file=sys.stderr)
            sys.exit(1)
        records = _load_input_json(args.input_json)
    elif args.connection:
        records = _fetch_from_sap(args)
    else:
        print("必须提供 --input-json 或 --connection 参数", file=sys.stderr)
        sys.exit(1)

    result = _aggregate(records)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"已生成 EKPO 聚合结果: {args.output}")
    print(f"物料数量: {len(result['materials'])}")
    for m in result["materials"]:
        print(f"  - {m['matnr']}: {len(m['records'])} 笔记录")


if __name__ == "__main__":
    main()
