#!/usr/bin/env python3
"""端到端风险评估入口：SAP JSON → 天机商查 → 五维度评分 → 输出评估 JSON。

设计原则：本脚本只做「搜索 + 评分 + 报告数据准备」三件确定性的事，不调用 LLM。
风险摘要与处置建议由对话 LLM 在 Agent Harness 上下文里基于评分结果生成——
避免双重 LLM 调用，保持上下文连贯，降低成本与延迟。

用法：
    python skills/procurement-supplier-risk/scripts/assess_risk.py \\
        --sap-json tmp/workbench/session_xxx/supplier_risk_erp_data.csv

说明：
- --sap-json 必填；--tianji-dir / --output 可选，未指定时默认落在 --sap-json 所在目录
  （工作台会话目录 `tmp/workbench/{session_id}/`），不同会话数据互不覆盖
- 相对路径自动锚定到**租户工作空间**（如 <工作空间>/tmp/xxx），不会写入项目根目录。

支持输入文件格式：
- CSV（.csv）：ERP 同步导出的原始格式，自动映射列名（LIFNR/供应商编码/lifnr 等）
- JSON：[{...}, ...] 或 {"data": [...]} 格式

内部流程：
1. 读取 SAP 同步数据（CSV 或 JSON），提取每家供应商的编码/名称/统一社会信用代码/SPERR/LOEVM/ERDAT
2. 对每家供应商：
   a. subprocess 调用 tianji-business-search/scripts/search_business.py（每次都重新搜索，不使用缓存）
   b. 加载天机商查 JSON
   c. 五维度评分（scoring_rules）
3. 汇总所有供应商评估结果 + 风险分布统计，输出 JSON
4. ai_summary/ai_recommendation/priority_actions 留空，由对话 LLM 基于评分填充
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# 确保项目根在 sys.path（用 __file__ 的绝对路径计算，不依赖 cwd）
# 注意：bash 工具的 cwd 可能是租户工作空间（如 ~/one），不是项目根
# 工作空间下有 skills/ 副本，但没有 agent/ 和 common/ 包
# 方案：从 __file__ 向上找，优先找包含 agent/ + common/ 的目录（项目根）
def _find_project_root() -> str:
    """从当前文件位置向上查找项目根（包含 agent/ 和 common/ 的目录）。"""
    # 0. 优先使用平台注入的项目根环境变量（父进程 config.load_config 注入，
    #    子进程继承）。脚本从租户工作空间副本运行时，__file__ 向上永远
    #    找不到项目根（工作空间与项目根是两棵不同的目录树），必须靠它。
    env_root = os.environ.get("ONEAGENT_PROJECT_ROOT", "").strip()
    if env_root and os.path.isdir(os.path.join(env_root, "agent", "tools")) and \
       os.path.isdir(os.path.join(env_root, "common")):
        return os.path.abspath(env_root)

    # 1. 从 __file__ 向上找包含 agent/ + common/ 的目录
    for candidate in [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.realpath(__file__)),
    ]:
        cur = candidate
        for _ in range(10):
            if os.path.isdir(os.path.join(cur, "agent", "tools")) and \
               os.path.isdir(os.path.join(cur, "common")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    known_roots = [
        os.path.expanduser("~/xmhmtai_agent"),
        os.getcwd(),
    ]
    for root in known_roots:
        if root and os.path.isdir(os.path.join(root, "agent", "tools")) and \
           os.path.isdir(os.path.join(root, "common")):
            return os.path.abspath(root)

    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = _find_project_root()
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common.log import logger


# =============================================================================
# 租户工作空间解析（数据落盘锚定）
# =============================================================================
def _resolve_tenant_workspace() -> str:
    """解析当前租户工作空间根目录（租户环境）。

    评估过程中产生的中间数据（天机商查 JSON、评估结果 JSON）一律锚定在
    租户工作空间，避免因调用方 cwd 不同而把文件写进项目根目录的 tmp/
    （违反多租户数据隔离规范）。
    优先级：
    1. 当前工作目录本身位于 tenants/<tenant_id> 下（Agent bash 工具 / 工作台
       都以租户工作空间为 cwd）→ 直接用 cwd；
    2. 进程内请求上下文（web handler 直接调用）→ auth.tenant_context；
    3. 兜底：config.json 的 agent_workspace（单租户模式/无法识别租户时的全局工作空间）。
    """
    cwd = os.path.abspath(os.getcwd())
    parts = [p for p in cwd.split(os.sep) if p]
    if "tenants" in parts:
        return cwd

    try:
        from auth.tenant_context import get_tenant_workspace_root
        root = get_tenant_workspace_root()
        if root:
            return os.path.abspath(root)
    except Exception:
        pass

    try:
        from common.utils import expand_path
        from config import conf
        root = expand_path(conf().get("agent_workspace", "~/one"))
        if root:
            return os.path.abspath(root)
    except Exception:
        pass

    return os.path.abspath(os.path.expanduser("~/one"))


def _anchor_to_workspace(path: str, workspace: Optional[str] = None) -> str:
    """把相对路径锚定到租户工作空间；绝对路径原样返回。"""
    if os.path.isabs(path):
        return path
    workspace = workspace or _resolve_tenant_workspace()
    return os.path.normpath(os.path.join(workspace, path))


# 用 importlib 加载同目录的 scoring_rules.py（避免包导入）
def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_scoring = _load_module("_scoring", os.path.join(_HERE, "scoring_rules.py"))


# =============================================================================
# SAP JSON 加载（兼容多种格式）
# =============================================================================
def _load_sap_rows(path: str) -> List[Dict[str, Any]]:
    """读取 SAP 同步数据文件，返回供应商记录列表。

    自动识别文件格式：
    - **CSV**（.csv）：ERP 同步导出的原始格式，用 utf-8-sig 读取避免 BOM
    - **JSON**：
      - [{...}, {...}]                             # 直接数组
      - {"data": [...]}                            # data_exporter 标准格式
      - {"records": [...]}                         # 变体
      - {"meta": {...}, "data": [...]}             # 含元数据

    CSV 列名兼容（大小写不敏感，自动映射）：
      LIFNR / 供应商编码 / supplier_code
      NAME1 / 供应商名称 / company_name
      STCD1 / 统一社会信用代码 / credit_code
      SPERR / 过账冻结 / posting_block
      LOEVM / 删除标记 / delete_flag
      ERDAT / 创建日期 / created_date
    """
    lower_path = path.lower()
    if lower_path.endswith(".csv"):
        return _load_csv_rows(path)

    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for key in ("data", "records", "rows", "results"):
            if key in d and isinstance(d[key], list):
                return d[key]
    return []


def _load_csv_rows(path: str) -> List[Dict[str, Any]]:
    """读取 CSV 文件，自动映射列名到 SAP LFA1 标准字段。

    CSV 表头兼容中英文和 SAP 原始字段名，大小写不敏感。
    """
    import csv

    # 列名别名映射（小写 → SAP 标准字段名）
    alias_map = {
        "lifnr": "LIFNR",
        "供应商编码": "LIFNR",
        "supplier_code": "LIFNR",
        "供应商代码": "LIFNR",
        "name1": "NAME1",
        "供应商名称": "NAME1",
        "company_name": "NAME1",
        "name": "NAME1",
        "stcd1": "STCD1",
        "统一社会信用代码": "STCD1",
        "credit_code": "STCD1",
        "税号": "STCD1",
        "sperr": "SPERR",
        "过账冻结": "SPERR",
        "posting_block": "SPERR",
        "loevm": "LOEVM",
        "删除标记": "LOEVM",
        "delete_flag": "LOEVM",
        "erdat": "ERDAT",
        "创建日期": "ERDAT",
        "created_date": "ERDAT",
        "成立日期": "ERDAT",
    }

    # 用 utf-8-sig 自动处理 BOM
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows: List[Dict[str, Any]] = []
        for raw_row in reader:
            mapped: Dict[str, Any] = {}
            for col_name, value in raw_row.items():
                if col_name is None:
                    continue
                # 跳过空值
                if value is None or str(value).strip() == "":
                    continue
                # 大小写不敏感 + 中文别名映射
                key_lower = col_name.strip().lower()
                if key_lower in alias_map:
                    sap_field = alias_map[key_lower]
                    mapped[sap_field] = str(value).strip()
                else:
                    # 未知列保留原始名（方便扩展）
                    mapped[col_name.strip()] = str(value).strip()
            if mapped:
                rows.append(mapped)
        return rows


# =============================================================================
# 天机商查调用（不使用缓存，每次都重新搜索）
# =============================================================================
def _tianji_path_for(tianji_dir: str, supplier_code: str) -> str:
    return os.path.join(tianji_dir, f"tianji_{supplier_code}.json")


def _run_tianji_search(
    company_name: str, supplier_code: str, tianji_dir: str,
    dimensions: str = "basic,risk,shareholder,news",
) -> Optional[str]:
    """subprocess 调用 search_business.py（每次都重新搜索，不读旧缓存）。"""
    # 转绝对路径：先锚定到租户工作空间，再转绝对路径，防止 cwd 不同导致写入项目根 tmp/
    out = os.path.abspath(_anchor_to_workspace(_tianji_path_for(tianji_dir, supplier_code)))
    script = os.path.join(_PROJECT_ROOT, "skills", "tianji-business-search", "scripts", "search_business.py")
    if not os.path.isfile(script):
        logger.error(f"[assess_risk] search_business.py 不存在: {script}")
        return None
    cmd = [
        sys.executable, script,
        "--company", company_name,
        "--dimensions", dimensions,
        "--output", out,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=_PROJECT_ROOT,
        )
        if result.returncode != 0:
            logger.warning(
                f"[assess_risk] search_business.py 失败 supplier={supplier_code}: {result.stderr[:300]}"
            )
            return None
        return out if os.path.isfile(out) else None
    except subprocess.TimeoutExpired:
        logger.warning(f"[assess_risk] search_business.py 超时 supplier={supplier_code}")
        return None
    except Exception as e:
        logger.warning(f"[assess_risk] search_business.py 异常 supplier={supplier_code}: {e}")
        return None


def _load_tianji(path: Optional[str]) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[assess_risk] 加载天机 JSON 失败 {path}: {e}")
        return {}


# =============================================================================
# 单供应商评估
# =============================================================================
def _extract_sap_field(row: Dict[str, Any], *keys: str, default: str = "") -> str:
    """从 SAP 行里按多种 key 取值（兼容中文/英文/ADT 别名）。"""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _assess_one(
    row: Dict[str, Any],
    tianji_dir: str,
    dimensions: str,
) -> Optional[Dict[str, Any]]:
    """评估单家供应商（每次都重新搜索，不使用缓存）。"""
    supplier_code = _extract_sap_field(row, "供应商编码", "LIFNR", "supplier_code")
    company_name = _extract_sap_field(row, "供应商名称", "NAME1", "company_name")
    if not supplier_code or not company_name:
        logger.warning(f"[assess_risk] 跳过缺少编码/名称的记录: {row}")
        return None

    # 每次都重新调天机商查（不读旧缓存）
    tianji_path = _run_tianji_search(company_name, supplier_code, tianji_dir, dimensions)
    tianji = _load_tianji(tianji_path)

    # 五维度评分
    dim_scores = {
        "judicial":     _scoring.score_judicial(tianji.get("risk", {})),
        "shareholder":  _scoring.score_shareholder(tianji.get("shareholder", []), tianji.get("basic", {})),
        "sap_internal": _scoring.score_sap_internal(row),
        "news":         _scoring.score_news(tianji.get("news", [])),
        "business":     _scoring.score_business(tianji.get("basic", {})),
    }
    total_score, risk_level = _scoring.compute_total(dim_scores)

    # SAP 标记
    sap_posting_block = _extract_sap_field(row, "过账冻结", "SPERR", "posting_block")
    sap_delete_flag = _extract_sap_field(row, "删除标记", "LOEVM", "delete_flag")
    erdat = _extract_sap_field(row, "创建日期", "ERDAT", "created_date")

    # 统一社会信用代码（优先 SAP STCD1，其次天机 basic.credit_code）
    credit_code = (
        _extract_sap_field(row, "统一社会信用代码", "STCD1", "credit_code")
        or (tianji.get("basic", {}) or {}).get("credit_code", "")
    )

    item = {
        "supplier_code": supplier_code,
        "company_name": company_name,
        "credit_code": credit_code,
        "total_score": total_score,
        "risk_level": risk_level,
        "dimension_scores": dim_scores,
        "key_risks": (tianji.get("risk", {}) or {}).get("details", [])[:5],
        "sap_flags": {
            "posting_block": sap_posting_block,
            "delete_flag": sap_delete_flag,
            "created_date": erdat,
        },
        "tianji_path": tianji_path,
        "tianji_extracted": bool(tianji),
        # 留空：由对话 LLM 在 Agent Harness 上下文里基于评分生成
        "ai_summary": "",
        "ai_recommendation": "",
        "priority_actions": [],
    }

    return item


# =============================================================================
# 主流程
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="供应商风险评估端到端入口")
    ap.add_argument("--sap-json", required=True, help="SAP LFA1 同步数据路径（必填，支持 CSV 或 JSON）")
    ap.add_argument("--tianji-dir", help="存放天机商查 JSON 的目录（默认：--sap-json 所在目录，即工作台会话目录，跨会话不覆盖）")
    ap.add_argument("--output", help="输出评估 JSON 路径（默认：--sap-json 所在目录/supplier_risk_assessment.json）")
    ap.add_argument(
        "--max-suppliers", type=int, default=50,
        help="单轮评估最大供应商数，防搜索过载",
    )
    ap.add_argument(
        "--dimensions", default="basic,risk,shareholder,news",
        help="天机商查搜索维度，默认 basic,risk,shareholder,news",
    )
    args = ap.parse_args()

    # 与 search_business.py 保持一致：加载 config.json，保证租户工作空间解析可用。
    # config.py 的 load_config() 用相对路径 "./config.json" 定位配置，
    # 必须临时切到项目根再加载（Agent bash 的 cwd 是租户工作空间），加载完还原。
    old_cwd = os.getcwd()
    try:
        os.chdir(_PROJECT_ROOT)
        from config import load_config
        load_config()
    except Exception as _e:
        logger.warning(f"[assess_risk] load_config 失败: {_e}")
    finally:
        os.chdir(old_cwd)

    # 相对路径统一锚定到租户工作空间，防止 cwd 不同导致写入项目根 tmp/
    args.sap_json = _anchor_to_workspace(args.sap_json)

    # 与比价分析一致：未显式指定时，中间/结果数据默认落到 --sap-json 所在目录
    # （工作台上传即 `tmp/workbench/{session_id}/`），不同会话数据落在各自目录，互不覆盖
    args.tianji_dir = (
        _anchor_to_workspace(args.tianji_dir)
        if args.tianji_dir
        else (os.path.dirname(args.sap_json) or ".")
    )
    args.output = (
        _anchor_to_workspace(args.output)
        if args.output
        else os.path.join(os.path.dirname(args.sap_json) or ".", "supplier_risk_assessment.json")
    )

    os.makedirs(args.tianji_dir, exist_ok=True)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    # 加载 SAP 数据
    if not os.path.isfile(args.sap_json):
        logger.error(f"[assess_risk] SAP 数据文件不存在: {args.sap_json}")
        sys.exit(1)
    rows = _load_sap_rows(args.sap_json)[:args.max_suppliers]
    logger.info(f"[assess_risk] 加载 {len(rows)} 家供应商")

    # 逐家评估（每次都重新搜索，不使用缓存）
    assessments: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        logger.info(f"[assess_risk] [{i}/{len(rows)}] 评估 {row.get('LIFNR') or row.get('供应商编码')}")
        item = _assess_one(row, args.tianji_dir, args.dimensions)
        if item:
            assessments.append(item)

    # 风险分布统计
    dist = {"低": 0, "中": 0, "高": 0, "严重": 0}
    for a in assessments:
        level = a.get("risk_level", "低")
        dist[level] = dist.get(level, 0) + 1

    result = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "total": len(assessments),
            "risk_distribution": dist,
            "dimensions": args.dimensions,
        },
        "assessments": assessments,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(
        f"Wrote {args.output}\n"
        f"  total={len(assessments)}\n"
        f"  risk_distribution={dist}"
    )


if __name__ == "__main__":
    main()
