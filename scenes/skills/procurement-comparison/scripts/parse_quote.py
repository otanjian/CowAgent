#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse supplier quotation files into structured JSON.

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

try:
    import pandas as pd
except ImportError:
    pd = None


# Field aliases for flexible Excel header matching
FIELD_ALIASES = {
    "物料编码": ["物料编码", "料号", "编码", "零件号", "物料号", "Item Code"],
    "物料名称": ["物料名称", "品名", "产品", "物料", "产品名称", "Description"],
    "规格": ["规格", "型号", "规格型号", "Spec", "Specification"],
    "数量": ["数量", "Qty", "qty", "采购量"],
    "单位": ["单位", "Unit", "unit"],
    "单价": ["单价", "含税单价", "未税单价", "价格", "Unit Price", "Price"],
    "总价": ["总价", "金额", "合计", "Total", "Total Amount"],
    "税率": ["税率", "Tax Rate", "tax"],
    "交期": ["交期", "交货期", "交付周期", "交货日期", "Delivery", "Lead Time"],
    "付款条件": ["付款条件", "付款方式", "结算方式", "Payment Terms"],
    "起订量": ["起订量", "MOQ", "最小订单", "Minimum Order"],
    "质保期": ["质保期", "保修期", "Warranty"],
    "有效期": ["有效期", "报价有效期", "Valid Until"],
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
    """Find the row index containing headers like 物料编码, 物料名称 etc."""
    for i in range(min(max_rows, len(df))):
        row = df.iloc[i].astype(str).tolist()
        score = 0
        for field in FIELD_ALIASES.keys():
            if find_field_column(row, field) >= 0:
                score += 1
        if score >= 2:
            return i
    return 0


def parse_number(text) -> float:
    """Parse numeric value from string."""
    if text is None:
        return 0
    text = str(text).strip().replace(",", "").replace("，", "").replace("¥", "").replace("$", "").replace("元", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else 0


def parse_excel(file_path: str, supplier_name: str = "") -> dict:
    """Parse structured Excel quotation with flexible header matching."""
    if pd is None:
        raise RuntimeError("pandas and openpyxl are required for Excel files")

    df = pd.read_excel(file_path, header=None)
    header_row = find_header_row(df)
    headers = df.iloc[header_row].astype(str).tolist()

    # Use all rows after header as data
    data_start = header_row + 1
    items = []

    idx_map = {}
    for field in FIELD_ALIASES.keys():
        col = find_field_column(headers, field)
        if col >= 0:
            idx_map[field] = col

    for i in range(data_start, len(df)):
        row = df.iloc[i].astype(str).tolist()

        # Skip empty rows
        if all(str(c).strip() == "" or str(c).strip() == "nan" for c in row):
            continue

        item = {
            "物料编码": "",
            "物料名称": "",
            "规格": "",
            "数量": 1,
            "单位": "件",
            "单价": 0,
            "总价": 0,
            "税率": "13%",
            "交期": "",
            "付款条件": "",
            "起订量": 1,
            "质保期": "12个月",
            "有效期": ""
        }

        if "物料编码" in idx_map:
            item["物料编码"] = str(row[idx_map["物料编码"]]).strip()
        if "物料名称" in idx_map:
            item["物料名称"] = str(row[idx_map["物料名称"]]).strip()
        if "规格" in idx_map:
            item["规格"] = str(row[idx_map["规格"]]).strip()
        if "数量" in idx_map:
            item["数量"] = int(parse_number(row[idx_map["数量"]])) or 1
        if "单位" in idx_map:
            item["单位"] = str(row[idx_map["单位"]]).strip() or "件"
        if "单价" in idx_map:
            item["单价"] = parse_number(row[idx_map["单价"]])
        if "总价" in idx_map:
            item["总价"] = parse_number(row[idx_map["总价"]])
        if "税率" in idx_map:
            tax_text = str(row[idx_map["税率"]]).strip()
            m = re.search(r"\d+(?:\.\d+)?", tax_text)
            item["税率"] = f"{m.group()}%" if m else "13%"
        if "交期" in idx_map:
            item["交期"] = str(row[idx_map["交期"]]).strip()
        if "付款条件" in idx_map:
            item["付款条件"] = str(row[idx_map["付款条件"]]).strip()
        if "起订量" in idx_map:
            item["起订量"] = int(parse_number(row[idx_map["起订量"]])) or 1
        if "质保期" in idx_map:
            item["质保期"] = str(row[idx_map["质保期"]]).strip() or "12个月"
        if "有效期" in idx_map:
            item["有效期"] = str(row[idx_map["有效期"]]).strip()

        # Calculate total if missing
        if item["总价"] == 0 and item["单价"] > 0 and item["数量"] > 0:
            item["总价"] = round(item["单价"] * item["数量"], 2)

        # Only keep rows with material name or unit price
        if item["物料名称"] or item["单价"] > 0 or item["总价"] > 0:
            items.append(item)

    # Try to extract supplier name from filename or top rows
    supplier = supplier_name
    if not supplier:
        supplier = _extract_supplier_from_rows(df, header_row)

    total_amount = sum(item.get("总价", 0) for item in items)

    return {
        "supplier": supplier or "未知供应商",
        "quote_date": "",
        "currency": "CNY",
        "items": items,
        "total_amount": round(total_amount, 2),
        "payment_terms": items[0].get("付款条件", "") if items else "",
        "delivery_terms": "送货上门",
        "delivery_period": items[0].get("交期", "") if items else "",
        "remarks": ""
    }


def _extract_supplier_from_rows(df, header_row: int, max_rows: int = 5) -> str:
    """Try to extract supplier name from rows before header."""
    for i in range(min(header_row, max_rows)):
        text = " ".join(str(c) for c in df.iloc[i].astype(str).tolist() if str(c).strip())
        m = re.search(r"(?:供应商|报价单位|卖方|乙方)[:：]\s*(.+?)(?:\n|$)", text)
        if m:
            return m.group(1).strip()
        m = re.search(r"^(.+?(?:公司|厂|集团))", text)
        if m:
            return m.group(1).strip()
    return ""


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
        if pd is None:
            raise RuntimeError("pandas and openpyxl are required for Excel files")
        df = pd.read_excel(str(path), header=None)
        return df.to_string(index=False)

    raise ValueError(f"Unsupported file format: {suffix}")


def extract_supplier(text: str, fallback: str = "") -> str:
    """Extract supplier name from text."""
    patterns = [
        r"(?:供应商|报价单位|卖方|乙方)[:：]\s*(.+?)(?:\n|$)",
        r"(?:致|To)[:：]\s*(.+?)(?:\n|$)",
        r"^(.+?)(?:公司|厂|集团)"
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return fallback or "未知供应商"


def extract_quote_date(text: str) -> str:
    """Extract quotation date."""
    patterns = [
        r"(?:报价日期|日期|Date)[:：]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)",
        r"(?:报价有效期|有效期)[:：]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return ""


def extract_payment_terms(text: str) -> str:
    """Extract payment terms."""
    patterns = [
        r"(?:付款方式|付款条件|结算方式|Payment)[:：]?\s*(.+?)(?:\n|$|，|,)",
        r"(?:月结|预付|货到付款|信用证)(?:\s*\d+\s*天)?"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0).strip() if "付款" not in m.group(0) else m.group(1).strip()
    return ""


def extract_delivery(text: str) -> str:
    """Extract delivery period."""
    patterns = [
        r"(?:交期|交货期|交付周期|Delivery)[:：]?\s*(\d+\s*(?:天|工作日|weeks?|days?))",
        r"(\d+\s*(?:天|工作日))(?:内)?(?:交货|交付)"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return ""


def _save_item(item: dict, text: str) -> dict:
    """Finalize an item with default values."""
    if not item:
        return None
    if not ("物料名称" in item or "单价" in item):
        return None

    item.setdefault("物料编码", "")
    item.setdefault("物料名称", "")
    item.setdefault("规格", "")
    item.setdefault("数量", 1)
    item.setdefault("单位", "件")
    item.setdefault("单价", 0)
    if "总价" not in item and "单价" in item and "数量" in item:
        item["总价"] = item["单价"] * item["数量"]
    item.setdefault("税率", "13%")
    item.setdefault("交期", extract_delivery(text))
    item.setdefault("付款条件", extract_payment_terms(text))
    item.setdefault("起订量", 1)
    item.setdefault("质保期", "12个月")
    item.setdefault("有效期", "")
    return item


def extract_items(text: str) -> list:
    """Extract line items from quotation text."""
    items = []
    lines = text.split("\n")
    current_item = {}

    def is_new_item(line_text: str) -> bool:
        # Use material code as the delimiter for a new item
        return bool(re.search(r"(?:物料编码|料号|编码)[:：]", line_text))

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Start new item when encountering material code/name
        if is_new_item(line) and current_item:
            saved = _save_item(current_item, text)
            if saved:
                items.append(saved)
            current_item = {}

        # Material code
        m = re.search(r"(?:物料编码|料号|编码)[:：]\s*([A-Za-z0-9\-]+)", line)
        if m:
            current_item["物料编码"] = m.group(1).strip()
            continue

        # Material name
        m = re.search(r"(?:物料名称|品名|产品)[:：]\s*(.+?)(?:\n|$|，|,)", line)
        if m:
            current_item["物料名称"] = m.group(1).strip()
            continue

        # Specification
        m = re.search(r"(?:规格|型号|Spec)[:：]\s*(.+?)(?:\n|$|，|,)", line)
        if m:
            current_item["规格"] = m.group(1).strip()
            continue

        # Quantity
        m = re.search(r"(?:数量|Qty)[:：]?\s*(\d+[\d,]*)(?:\s*(?:件|个|套|台|kg|KG|吨|米|支))?(?:\n|$|，|,)", line)
        if m:
            current_item["数量"] = int(m.group(1).replace(",", ""))
            continue

        # Unit
        m = re.search(r"(?:单位|Unit)[:：]\s*(.+?)(?:\n|$|，|,)", line)
        if m:
            current_item["单位"] = m.group(1).strip()
            continue

        # Unit price
        m = re.search(r"(?:单价|Unit Price)[:：]?\s*([\d.]+)(?:\s*元)?", line)
        if m:
            current_item["单价"] = float(m.group(1))
            continue

        # Total price
        m = re.search(r"(?:总价|金额|Total)[:：]?\s*([\d,]+(?:\.\d+)?)(?:\s*元)?", line)
        if m:
            current_item["总价"] = float(m.group(1).replace(",", ""))
            continue

        # Tax rate
        m = re.search(r"(?:税率|Tax)[:：]?\s*(\d+(?:\.\d+)?)\s*%", line)
        if m:
            current_item["税率"] = f"{m.group(1)}%"
            continue

        # Warranty
        m = re.search(r"(?:质保期|保修期|Warranty)[:：]?\s*(.+?)(?:\n|$|，|,)", line)
        if m:
            current_item["质保期"] = m.group(1).strip()
            continue

        # MOQ
        m = re.search(r"(?:起订量|MOQ|最小订单)[:：]?\s*(\d+)", line)
        if m:
            current_item["起订量"] = int(m.group(1))
            continue

        # Validity
        m = re.search(r"(?:有效期|Valid)[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)", line)
        if m:
            current_item["有效期"] = m.group(1).strip()
            continue

    # Save last item
    saved = _save_item(current_item, text)
    if saved:
        items.append(saved)

    return items


def parse_text(text: str, supplier_name: str = "") -> dict:
    """Parse quotation text into structured data."""
    supplier = supplier_name or extract_supplier(text)
    items = extract_items(text)

    # If no items extracted, create a placeholder from overall price
    if not items:
        total_match = re.search(r"(?:合计|总计|Total Amount)[:：]?\s*([\d,]+(?:\.\d+)?)", text)
        total = float(total_match.group(1).replace(",", "")) if total_match else 0
        items.append({
            "物料编码": "",
            "物料名称": "报价合计",
            "规格": "",
            "数量": 1,
            "单位": "批",
            "单价": total,
            "总价": total,
            "税率": "13%",
            "交期": extract_delivery(text),
            "付款条件": extract_payment_terms(text),
            "起订量": 1,
            "质保期": "12个月",
            "有效期": ""
        })

    total_amount = sum(item.get("总价", 0) for item in items)

    return {
        "supplier": supplier,
        "quote_date": extract_quote_date(text),
        "currency": "CNY",
        "items": items,
        "total_amount": round(total_amount, 2),
        "payment_terms": extract_payment_terms(text),
        "delivery_terms": "送货上门",
        "delivery_period": extract_delivery(text),
        "remarks": ""
    }


def extract_with_llm(text: str, model_config: dict = None) -> dict:
    """Reserved interface for LLM-based unstructured extraction.

    When the platform provides an LLM invocation interface for skills,
    implement this function to call the model and return structured JSON.
    Currently returns a placeholder with raw text so the caller can decide.
    """
    # TODO: integrate with platform LLM when available
    return {
        "supplier": "",
        "quote_date": "",
        "currency": "CNY",
        "items": [],
        "total_amount": 0,
        "payment_terms": "",
        "delivery_terms": "送货上门",
        "delivery_period": "",
        "remarks": "",
        "_raw_text": text,
        "_llm_extraction_needed": True,
        "_note": "LLM extraction not yet enabled; please upload a template file or provide parsed JSON."
    }


def parse_file(file_path: str, supplier_name: str = "", use_llm: bool = False) -> dict:
    """Parse quotation file with best-effort strategy."""
    suffix = Path(file_path).suffix.lower()

    # Strategy 1: structured Excel
    if suffix in [".xlsx", ".xls"]:
        try:
            result = parse_excel(file_path, supplier_name)
            if result.get("items"):
                return result
        except Exception as e:
            print(f"Excel structured parse failed: {e}", file=sys.stderr)

    # Strategy 2: text extraction + regex
    text = read_file(file_path)
    result = parse_text(text, supplier_name)
    if result.get("items"):
        return result

    # Strategy 3: LLM fallback (reserved)
    if use_llm:
        return extract_with_llm(text)

    # Return regex result even if incomplete, with raw text attached
    result["_raw_text"] = text
    result["_parse_warning"] = "Could not extract line items; consider using template or enabling LLM extraction."
    return result


def main():
    parser = argparse.ArgumentParser(description="Parse supplier quotation files")
    parser.add_argument("--input", help="Input quotation file path")
    parser.add_argument("--input-json", help="Pre-parsed quotation JSON file path")
    parser.add_argument("--supplier", default="", help="Supplier name")
    parser.add_argument("--output", default="tmp/quote_parsed.json", help="Output JSON path")
    parser.add_argument("--use-llm", action="store_true", help="Enable LLM fallback for unstructured files")
    args = parser.parse_args()

    if args.input_json:
        if not os.path.exists(args.input_json):
            print(f"Error: input JSON not found: {args.input_json}", file=sys.stderr)
            sys.exit(1)
        with open(args.input_json, "r", encoding="utf-8") as f:
            result = json.load(f)
    elif args.input:
        if not os.path.exists(args.input):
            print(f"Error: input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        result = parse_file(args.input, args.supplier, use_llm=args.use_llm)
    else:
        print("Error: either --input or --input-json must be provided", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Parsed quotation saved to: {args.output}")


if __name__ == "__main__":
    main()
