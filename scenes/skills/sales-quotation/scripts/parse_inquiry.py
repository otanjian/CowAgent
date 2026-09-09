#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse customer inquiry files into structured JSON.

Supports:
- Structured Excel/CSV files (template-based, fast and cheap)
- Semi-structured PDF/Word/text files (regex extraction)
- Pre-parsed JSON from upstream LLM (--input_json)
- Future LLM extraction fallback (reserved interface)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# Field aliases for flexible Excel header matching
FIELD_ALIASES = {
    "customer": ["客户名称", "客户", "买方", "需方", "客户公司", "公司名称", "客户单位"],
    "product_name": ["产品名称", "产品", "品名", "型号", "物料名称", "物料", "产品型号"],
    "quantity": ["需求量", "数量", "采购量", "订单数量", "数量(件)", "数量（件）", "Qty", "qty"],
    "target_delivery": ["目标交期", "交期", "交付日期", "要求交期", "交货期", "交付时间", "期望交期"],
    "payment_terms": ["付款方式", "付款条件", "结算方式", "付款条款", "支付方式"],
}


def normalize_header(header: str) -> str:
    """Normalize header text for matching."""
    if header is None:
        return ""
    h = str(header).strip().lower()
    h = re.sub(r"[\s()（）\[\]【】]", "", h)
    return h


def find_field_column(headers: list, field: str) -> int:
    """Find column index for a field using aliases."""
    aliases = [normalize_header(a) for a in FIELD_ALIASES.get(field, [])]
    for idx, h in enumerate(headers):
        nh = normalize_header(h)
        if nh in aliases:
            return idx
        # Fuzzy: alias is substring of header or vice versa
        for alias in aliases:
            if alias and (alias in nh or nh in alias):
                return idx
    return -1


def find_header_row(df, max_rows: int = 10) -> int:
    """Find the row index containing headers like 客户名称, 产品名称 etc."""
    for i in range(min(max_rows, len(df))):
        row = df.iloc[i].astype(str).tolist()
        score = 0
        for field in FIELD_ALIASES.keys():
            if find_field_column(row, field) >= 0:
                score += 1
        if score >= 2:
            return i
    return 0


def parse_excel(file_path: str) -> dict:
    """Parse structured Excel with flexible header matching."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas and openpyxl are required for Excel files")

    df = pd.read_excel(file_path, header=None)
    header_row = find_header_row(df)
    headers = df.iloc[header_row].astype(str).tolist()

    result = {
        "customer": "",
        "product_name": "",
        "specifications": {},
        "quantity": 0,
        "target_delivery": "",
        "payment_terms": "",
        "special_requirements": []
    }

    # Use first data row
    data_start = header_row + 1
    if data_start >= len(df):
        return result

    row = df.iloc[data_start].astype(str).tolist()

    customer_idx = find_field_column(headers, "customer")
    product_idx = find_field_column(headers, "product_name")
    qty_idx = find_field_column(headers, "quantity")
    delivery_idx = find_field_column(headers, "target_delivery")
    payment_idx = find_field_column(headers, "payment_terms")

    if customer_idx >= 0:
        result["customer"] = str(row[customer_idx]).strip()
    if product_idx >= 0:
        result["product_name"] = str(row[product_idx]).strip()
    if qty_idx >= 0:
        qty_text = str(row[qty_idx]).strip().replace(",", "").replace("，", "")
        m = re.search(r"\d+", qty_text)
        if m:
            result["quantity"] = int(m.group())
    if delivery_idx >= 0:
        result["target_delivery"] = str(row[delivery_idx]).strip()
    if payment_idx >= 0:
        result["payment_terms"] = str(row[payment_idx]).strip()

    # Try to extract specifications from extra columns
    for idx, header in enumerate(headers):
        h = normalize_header(header)
        if "材质" in h or "材料" in h:
            result["specifications"]["material"] = str(row[idx]).strip()
        elif "尺寸" in h or "规格" in h:
            result["specifications"]["size"] = str(row[idx]).strip()
        elif "公差" in h or "精度" in h:
            result["specifications"]["tolerance"] = str(row[idx]).strip()
        elif "表面" in h:
            result["specifications"]["surface"] = str(row[idx]).strip()

    return result


def parse_text(text: str) -> dict:
    """Extract key fields from raw inquiry text using regex patterns."""
    result = {
        "customer": "",
        "product_name": "",
        "specifications": {},
        "quantity": 0,
        "target_delivery": "",
        "payment_terms": "",
        "special_requirements": []
    }

    # Customer name
    customer_patterns = [
        r"(?:客户名称|客户|买方|需方)[:：]\s*(.+?)(?:\n|$)",
        r"(?:致|To)[:：]\s*(.+?)(?:\n|$)"
    ]
    for pattern in customer_patterns:
        m = re.search(pattern, text)
        if m:
            result["customer"] = m.group(1).strip()
            break

    # Product name
    product_patterns = [
        r"(?:产品名称|产品|品名|型号|物料)[:：]\s*(.+?)(?:\n|$)",
        r"(?:报价产品|询价产品)[:：]\s*(.+?)(?:\n|$)"
    ]
    for pattern in product_patterns:
        m = re.search(pattern, text)
        if m:
            result["product_name"] = m.group(1).strip()
            break

    # Quantity
    qty_patterns = [
        r"(?:数量|需求量|采购量)[:：]?\s*(\d+[\d,]*)(?:\s*(?:件|个|套|台|kg|KG|吨|米|支))?",
        r"(\d+[\d,]*)\s*(?:件|个|套|台)"
    ]
    for pattern in qty_patterns:
        m = re.search(pattern, text)
        if m:
            result["quantity"] = int(m.group(1).replace(",", ""))
            break

    # Target delivery
    delivery_patterns = [
        r"(?:目标交期|交期|交付日期|要求交期)[:：]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)",
        r"(?:交货期|交付时间)[:：]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)"
    ]
    for pattern in delivery_patterns:
        m = re.search(pattern, text)
        if m:
            result["target_delivery"] = m.group(1).strip()
            break

    # Payment terms
    payment_patterns = [
        r"(?:付款方式|付款条件|结算方式)[:：]\s*(.+?)(?:\n|$)",
        r"(?:月结|预付|货到付款|信用证)(?:\s*\d+\s*天)?"
    ]
    for pattern in payment_patterns:
        m = re.search(pattern, text)
        if m:
            result["payment_terms"] = m.group(0).strip() if "付款" not in m.group(0) else m.group(1).strip()
            break

    # Specifications
    spec_patterns = {
        "material": r"(?:材质|材料|物料)[:：]\s*(.+?)(?:\n|$|，|,)",
        "size": r"(?:尺寸|规格|型号)[:：]\s*(.+?)(?:\n|$|，|,)",
        "tolerance": r"(?:公差|精度)[:：]\s*(.+?)(?:\n|$|，|,)",
        "surface": r"(?:表面处理|表面)[:：]\s*(.+?)(?:\n|$|，|,)"
    }
    for key, pattern in spec_patterns.items():
        m = re.search(pattern, text)
        if m:
            result["specifications"][key] = m.group(1).strip()

    # Special requirements
    req_patterns = [
        r"(?:特殊要求|技术要求|质量要求|备注)[:：]\s*(.+?)(?:\n\n|\n[^：]+[:：]|$)",
    ]
    for pattern in req_patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            reqs = [r.strip() for r in m.group(1).strip().split(",") if r.strip()]
            result["special_requirements"] = reqs
            break

    return result


def read_file(file_path: str) -> str:
    """Read text content from various file formats."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in [".txt", ".md", ".json"]:
        return path.read_text(encoding="utf-8")

    if suffix in [".docx", ".doc"]:
        try:
            import docx
            doc = docx.Document(str(path))
            return "\n".join([para.text for para in doc.paragraphs])
        except ImportError:
            raise RuntimeError("python-docx is required for Word files")

    if suffix == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        except ImportError:
            raise RuntimeError("pdfplumber is required for PDF files")

    if suffix in [".xlsx", ".xls"]:
        try:
            import pandas as pd
            df = pd.read_excel(str(path), header=None)
            return df.to_string(index=False)
        except ImportError:
            raise RuntimeError("pandas and openpyxl are required for Excel files")

    raise ValueError(f"Unsupported file format: {suffix}")


def extract_with_llm(text: str, model_config: dict = None) -> dict:
    """Reserved interface for LLM-based unstructured extraction.

    When the platform provides an LLM invocation interface for skills,
    implement this function to call the model and return structured JSON.
    Currently returns a placeholder with raw text so the caller can decide.
    """
    # TODO: integrate with platform LLM when available
    return {
        "customer": "",
        "product_name": "",
        "specifications": {},
        "quantity": 0,
        "target_delivery": "",
        "payment_terms": "",
        "special_requirements": [],
        "_raw_text": text,
        "_llm_extraction_needed": True,
        "_note": "LLM extraction not yet enabled; please upload a template file or provide parsed JSON."
    }


def parse_file(file_path: str, use_llm: bool = False) -> dict:
    """Parse inquiry file with best-effort strategy."""
    suffix = Path(file_path).suffix.lower()

    # Strategy 1: structured Excel
    if suffix in [".xlsx", ".xls"]:
        try:
            result = parse_excel(file_path)
            # If key fields found, return directly
            if result.get("customer") or result.get("product_name") or result.get("quantity"):
                return result
        except Exception as e:
            print(f"Excel structured parse failed: {e}", file=sys.stderr)

    # Strategy 2: text extraction + regex
    text = read_file(file_path)
    result = parse_text(text)
    if result.get("customer") or result.get("product_name") or result.get("quantity"):
        return result

    # Strategy 3: LLM fallback (reserved)
    if use_llm:
        return extract_with_llm(text)

    # Return regex result even if incomplete, with raw text attached
    result["_raw_text"] = text
    result["_parse_warning"] = "Could not extract key fields; consider using template or enabling LLM extraction."
    return result


def main():
    parser = argparse.ArgumentParser(description="Parse customer inquiry files")
    parser.add_argument("--input", help="Input inquiry file path")
    parser.add_argument("--input-json", help="Pre-parsed inquiry JSON file path")
    parser.add_argument("--output", default="tmp/inquiry_parsed.json", help="Output JSON path")
    parser.add_argument("--ocr", action="store_true", help="Enable OCR for image/PDF files")
    parser.add_argument("--use-llm", action="store_true", help="Enable LLM fallback for unstructured files")
    args = parser.parse_args()

    if args.input_json:
        # Use pre-parsed JSON from upstream (e.g., LLM extraction)
        if not os.path.exists(args.input_json):
            print(f"Error: input JSON not found: {args.input_json}", file=sys.stderr)
            sys.exit(1)
        with open(args.input_json, "r", encoding="utf-8") as f:
            result = json.load(f)
    elif args.input:
        if not os.path.exists(args.input):
            print(f"Error: input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        result = parse_file(args.input, use_llm=args.use_llm)
    else:
        print("Error: either --input or --input-json must be provided", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Parsed inquiry saved to: {args.output}")


if __name__ == "__main__":
    main()
