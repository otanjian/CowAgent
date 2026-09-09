"""SAP 数据分析 - V2 查询建议器。

由后端 channel/web/sap/query_planner.py 在 V1 模板无法匹配时调用。
接收 JSON {question, context, available_tables}，返回 JSON {status, plan}。

本脚本通过调用配置好的 LLM（OpenAI 兼容接口）将自然语言问题翻译为结构化 QueryPlan。
若 LLM 不可用，则回退到本地硬编码模板库。

注意：本脚本仅生成查询建议，不执行任何 SAP 连接或数据抽取，最终由后端校验执行。
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# -------------------------------------------------------------------------
# SAP 查询能力目录（catalog）加载
# -------------------------------------------------------------------------
_CATALOG: Optional[Dict[str, Any]] = None


def _load_catalog() -> Dict[str, Any]:
    """加载 sap_query_catalog.json，失败时返回空字典。"""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(script_dir, "..", "..", "references", "sap_query_catalog.json"),
        os.path.join(script_dir, "..", "..", "..", "skills", "sap-integration", "references", "sap_query_catalog.json"),
    ]
    for path in candidate_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _CATALOG = json.load(f)
                    return _CATALOG
            except Exception:
                pass
    _CATALOG = {}
    return _CATALOG


def _get_catalog_apis() -> Dict[str, Any]:
    return _load_catalog().get("apis", {})


def _get_catalog_relations() -> Dict[str, List[str]]:
    return _load_catalog().get("relations", {})


# -------------------------------------------------------------------------
# 本地硬编码扩展模板库（LLM 不可用时的兜底）
# -------------------------------------------------------------------------
_V2_TEMPLATES = [
    {
        "intent": "material_list",
        "domain": "inventory",
        "source": "table",
        "table": "MARA",
        "fields": ["MATNR", "MTART", "MATKL", "MEINS", "ERNAM", "ERSDA"],
        "where_template": "ERSDA BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["ERSDA DESC"],
        "max_rows": 5000,
        "field_mapping": {
            "MATNR": "物料编码",
            "MTART": "物料类型",
            "MATKL": "物料组",
            "MEINS": "基本单位",
            "ERNAM": "创建者",
            "ERSDA": "创建日期",
        },
        "keywords": ["物料", "物料号", "物料编号", "材料", "产品", "MARA"],
    },
    {
        "intent": "purchase_request_this_month",
        "domain": "procurement",
        "source": "table",
        "table": "EBAN",
        "fields": ["BANFN", "BNFPO", "BADAT", "MATNR", "TXZ01", "MENGE", "MEINS"],
        "where_template": "BADAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["BADAT DESC"],
        "max_rows": 5000,
        "field_mapping": {
            "BANFN": "采购申请号",
            "BNFPO": "行项目",
            "BADAT": "申请日期",
            "MATNR": "物料编码",
            "TXZ01": "物料描述",
            "MENGE": "数量",
            "MEINS": "单位",
        },
        "keywords": ["采购申请", "请购", "申购"],
    },
    {
        "intent": "sales_order_items_this_month",
        "domain": "sales",
        "source": "multi_table",
        "table": "VBAK",
        "fields": ["VBAK.VBELN", "VBAK.AUDAT", "VBAK.KUNNR", "VBAP.POSNR", "VBAP.MATNR", "VBAP.NETWR"],
        "where_template": "VBAK.AUDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["VBAK.AUDAT DESC"],
        "max_rows": 5000,
        "joins": [
            {
                "table": "VBAP",
                "on": "VBAP.VBELN = VBAK.VBELN",
                "fields": ["POSNR", "MATNR", "NETWR"],
                "join_type": "inner",
            }
        ],
        "field_mapping": {
            "VBAK.VBELN": "销售订单号",
            "VBAK.AUDAT": "订单日期",
            "VBAK.KUNNR": "客户编码",
            "VBAP.POSNR": "行项目",
            "VBAP.MATNR": "物料编码",
            "VBAP.NETWR": "行项目净值",
        },
        "keywords": ["销售订单明细", "销售订单行项目", "订单明细"],
    },
    {
        "intent": "sales_amount_overview",
        "domain": "sales",
        "source": "table",
        "table": "VBRK",
        "fields": ["VBELN", "FKDAT", "KUNNR", "NETWR", "WAERK"],
        "where_template": "FKDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": "SUM",
        "group_by": ["WAERK"],
        "order_by": [],
        "max_rows": 5000,
        "field_mapping": {
            "VBELN": "发票号",
            "FKDAT": "发票日期",
            "KUNNR": "客户编码",
            "NETWR": "净值",
            "WAERK": "货币",
        },
        "keywords": ["销量", "销售额", "销售收入", "销售情况", "销售分析"],
    },
    {
        "intent": "production_orders_this_month",
        "domain": "production",
        "source": "multi_table",
        "table": "AUFK",
        "fields": ["AUFK.AUFNR", "AUFK.AUART", "AUFK.WERKS", "AFKO.GAMNG", "AUFK.ERDAT"],
        "where_template": "AUFK.ERDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["AUFK.ERDAT DESC"],
        "max_rows": 5000,
        "joins": [
            {
                "table": "AFKO",
                "on": "AFKO.AUFNR = AUFK.AUFNR",
                "fields": ["GAMNG"],
                "join_type": "left",
            }
        ],
        "field_mapping": {
            "AUFK.AUFNR": "生产订单号",
            "AUFK.AUART": "订单类型",
            "AUFK.WERKS": "工厂",
            "AFKO.GAMNG": "订单数量",
            "AUFK.ERDAT": "创建日期",
        },
        "keywords": ["生产订单", "生产情况", "本月生产", "工单", "生产工单"],
    },
    {
        "intent": "production_order_items",
        "domain": "production",
        "source": "multi_table",
        "table": "AUFK",
        "fields": ["AUFK.AUFNR", "AUFK.AUART", "AUFK.WERKS", "AFPO.POSNR", "AFPO.MATNR", "AFPO.MEINS", "AFPO.MENGE"],
        "where_template": "AUFK.ERDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["AUFK.ERDAT DESC"],
        "max_rows": 5000,
        "joins": [
            {
                "table": "AFPO",
                "on": "AFPO.AUFNR = AUFK.AUFNR",
                "fields": ["POSNR", "MATNR", "MEINS", "MENGE"],
                "join_type": "inner",
            }
        ],
        "field_mapping": {
            "AUFK.AUFNR": "生产订单号",
            "AUFK.AUART": "订单类型",
            "AUFK.WERKS": "工厂",
            "AFPO.POSNR": "行项目",
            "AFPO.MATNR": "物料编码",
            "AFPO.MEINS": "单位",
            "AFPO.MENGE": "数量",
        },
        "keywords": ["生产订单明细", "生产订单行项目", "生产订单物料", "工单明细"],
    },
    {
        "intent": "finance_receivables",
        "domain": "finance",
        "source": "table",
        "table": "BSID",
        "fields": ["KUNNR", "BUKRS", "BELNR", "GJAHR", "BUZEI", "DMBTR", "BUDAT"],
        "where_template": "BUDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["BUDAT DESC"],
        "max_rows": 5000,
        "field_mapping": {
            "KUNNR": "客户编码",
            "BUKRS": "公司代码",
            "BELNR": "凭证号",
            "GJAHR": "会计年度",
            "BUZEI": "行项目",
            "DMBTR": "金额",
            "BUDAT": "过账日期",
        },
        "keywords": ["应收账款", "客户未清项", "应收款", "客户欠款", "未清客户账款"],
    },
    {
        "intent": "finance_payables",
        "domain": "finance",
        "source": "table",
        "table": "BSIK",
        "fields": ["LIFNR", "BUKRS", "BELNR", "GJAHR", "BUZEI", "DMBTR", "BUDAT"],
        "where_template": "BUDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["BUDAT DESC"],
        "max_rows": 5000,
        "field_mapping": {
            "LIFNR": "供应商编码",
            "BUKRS": "公司代码",
            "BELNR": "凭证号",
            "GJAHR": "会计年度",
            "BUZEI": "行项目",
            "DMBTR": "金额",
            "BUDAT": "过账日期",
        },
        "keywords": ["应付账款", "供应商未清项", "应付款", "供应商欠款", "未清供应商账款"],
    },
    {
        "intent": "customer_credit_limit",
        "domain": "master_data",
        "source": "table",
        "table": "KNKK",
        "fields": ["KUNNR", "KKBER", "KLIMK", "OBLIG", "SKFOR", "KNKAU"],
        "where_template": "",
        "aggregation": None,
        "group_by": [],
        "order_by": ["KUNNR", "KKBER"],
        "max_rows": 5000,
        "field_mapping": {
            "KUNNR": "客户编码",
            "KKBER": "信用控制范围",
            "KLIMK": "信用额度",
            "OBLIG": "信用风险总额",
            "SKFOR": "应收账款",
            "KNKAU": "销售值",
        },
        "keywords": ["信用额度", "客户信用", "信用限额", "客户额度", "信用控制"],
    },
    {
        "intent": "customer_payment_history",
        "domain": "finance",
        "source": "table",
        "table": "BSAD",
        "fields": ["KUNNR", "BUKRS", "BELNR", "GJAHR", "BUZEI", "DMBTR", "BUDAT", "AUGDT", "AUGBL"],
        "where_template": "BUDAT BETWEEN '{date_from_sap}' AND '{date_to_sap}'",
        "aggregation": None,
        "group_by": [],
        "order_by": ["BUDAT DESC"],
        "max_rows": 5000,
        "field_mapping": {
            "KUNNR": "客户编码",
            "BUKRS": "公司代码",
            "BELNR": "凭证号",
            "GJAHR": "会计年度",
            "BUZEI": "行项目",
            "DMBTR": "金额",
            "BUDAT": "过账日期",
            "AUGDT": "清账日期",
            "AUGBL": "清账凭证号",
        },
        "keywords": ["付款历史", "客户付款", "客户已清项", "客户清账", "清账历史"],
    },
    {
        "intent": "finance_gl_accounts",
        "domain": "finance",
        "source": "table",
        "table": "SKA1",
        "fields": ["KTOPL", "SAKNR", "TXT20"],
        "where_template": "",
        "aggregation": None,
        "group_by": [],
        "order_by": ["SAKNR"],
        "max_rows": 5000,
        "field_mapping": {
            "KTOPL": "科目表",
            "SAKNR": "总账科目号",
            "TXT20": "科目短文本",
        },
        "keywords": ["总账科目", "科目表", "会计科目", "科目列表"],
    },
]


# -------------------------------------------------------------------------
# LLM 调用
# -------------------------------------------------------------------------

def _get_config_value(key: str, default: Any = None) -> Any:
    """读取环境变量或项目 config.json 中的配置。"""
    env_key = key.upper().replace(".", "_")
    if os.environ.get(env_key):
        return os.environ.get(env_key)

    # 尝试从项目根目录 config.json 读取
    # 当前脚本路径：skills/sap-integration/scripts/query_planner.py
    # 向上 3 层即项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(script_dir))
    )
    config_path = os.path.join(project_root, "config.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if key in cfg:
                return cfg[key]
        except Exception:
            pass
    return default


def _get_model_config() -> Dict[str, str]:
    """解析当前项目配置的模型类型并返回 API key/base/model。"""
    model = _get_config_value("model", "deepseek-v4-pro")
    bot_type = _get_config_value("bot_type", "")

    # Ollama（按 bot_type 判断，因为 model 名可能是 qwen3.6:35b 等本地模型名）
    if bot_type == "ollama":
        return {
            "api_key": _get_config_value("ollama_api_key") or "ollama",
            "api_base": (_get_config_value("ollama_api_base") or "http://localhost:11434/v1").rstrip("/"),
            "model": model,
        }

    # DeepSeek
    if model.startswith("deepseek"):
        return {
            "api_key": _get_config_value("deepseek_api_key") or _get_config_value("open_ai_api_key"),
            "api_base": (_get_config_value("deepseek_api_base") or _get_config_value("open_ai_api_base") or "https://api.deepseek.com/v1").rstrip("/"),
            "model": model,
        }

    # Moonshot
    if model.startswith("moonshot"):
        return {
            "api_key": _get_config_value("moonshot_api_key") or _get_config_value("open_ai_api_key"),
            "api_base": (_get_config_value("moonshot_base_url") or _get_config_value("open_ai_api_base") or "https://api.moonshot.cn/v3").rstrip("/"),
            "model": model,
        }

    # DashScope / Qwen
    if model.startswith(("qwen", "qvq", "qwq")):
        return {
            "api_key": _get_config_value("dashscope_api_key") or _get_config_value("open_ai_api_key"),
            "api_base": (_get_config_value("dashscope_api_base") or _get_config_value("open_ai_api_base") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
            "model": model,
        }

    # OpenAI / 兼容
    return {
        "api_key": _get_config_value("open_ai_api_key"),
        "api_base": (_get_config_value("open_ai_api_base") or "https://api.openai.com/v1").rstrip("/"),
        "model": model,
    }


def _call_llm(messages: List[Dict[str, str]], temperature: float = 0.2) -> Optional[str]:
    """调用 LLM，返回模型生成的文本。"""
    try:
        import requests
    except ImportError:
        return None

    cfg = _get_model_config()
    api_key = cfg.get("api_key")
    api_base = cfg.get("api_base")
    model = cfg.get("model")

    if not api_key or not api_base:
        return None

    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    # 部分模型（如 deepseek-v4-pro）在默认参数下更稳定
    if model.startswith("deepseek-v4"):
        payload.pop("temperature", None)

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        # 强制按 UTF-8 解码，避免 requests 自动推断编码导致中文乱码
        text = resp.content.decode("utf-8", errors="replace")
        data = json.loads(text)
        content = data["choices"][0]["message"]["content"]
        # 防御性二次解码（某些接口会对中文字符做错误编码）
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return content
    except Exception as e:
        print(json.dumps({"status": "llm_warning", "message": str(e)}, ensure_ascii=False), file=sys.stderr)
        return None


# -------------------------------------------------------------------------
# Prompt 构建
# -------------------------------------------------------------------------

def _build_catalog_field_lines() -> List[str]:
    """根据 catalog 生成字段业务含义说明行。"""
    lines = []
    for api_key, meta in _get_catalog_apis().items():
        api_type = meta.get("type", "table")
        fields = meta.get("fields", {})
        if not fields:
            continue
        field_parts = [f"{k} {v}" for k, v in fields.items()]
        name = meta.get("name", api_key)
        if api_type == "bapi":
            lines.append(f"- {api_key}（{name}，只读 BAPI）: {', '.join(field_parts)}")
        else:
            date_field = meta.get("date_field", "")
            cc_field = meta.get("company_code_field", "")
            extra = []
            if date_field:
                extra.append(f"日期字段 {date_field}")
            if cc_field:
                extra.append(f"公司代码字段 {cc_field}")
            extra_str = f"（{', '.join(extra)}）" if extra else ""
            lines.append(f"- {api_key}: {', '.join(field_parts)}{extra_str}")
    return lines


def _build_catalog_bapi_lines() -> List[str]:
    """生成 BAPI 参数与输出说明。"""
    lines = []
    for api_key, meta in _get_catalog_apis().items():
        if meta.get("type") != "bapi":
            continue
        params = meta.get("parameters", {})
        param_parts = []
        for pk, pv in params.items():
            desc = pv.get("description", "")
            ptype = pv.get("type", "")
            default = pv.get("default")
            req = pv.get("required", False)
            part = f"{pk}({ptype}{' 必填' if req else ''}{f' 默认={default}' if default is not None else ''}) {desc}"
            param_parts.append(part)
        out_path = meta.get("output_path", "")
        out_hint = f"输出内表路径: {out_path}" if out_path else "输出为单条结构"
        lines.append(f"- {api_key}: {meta.get('name', api_key)}; 参数: {'; '.join(param_parts)}; {out_hint}")
    return lines


def _build_system_prompt(available_tables: List[str]) -> str:
    tables_str = ", ".join(available_tables) if available_tables else "未限制"
    today_str = datetime.now().strftime('%Y-%m-%d')

    catalog_apis = _get_catalog_apis()
    catalog_field_lines = _build_catalog_field_lines()
    catalog_bapi_lines = _build_catalog_bapi_lines()

    # 只读 BAPI 名称列表，用于 prompt 提示
    readonly_bapis = [k for k, v in catalog_apis.items() if v.get("type") == "bapi" and v.get("readonly", False)]
    bapis_str = ", ".join(readonly_bapis) if readonly_bapis else "未配置"

    # 读取 sap-tables.md —— SAP 常用表、字段、关联关系完整参考
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tables_md_path = os.path.join(script_dir, "..", "references", "sap-tables.md")
    tables_md = ""
    if os.path.exists(tables_md_path):
        try:
            with open(tables_md_path, "r", encoding="utf-8") as f:
                tables_md = f.read()
        except Exception:
            tables_md = ""
    else:
        # 尝试从项目根目录找
        alt_path = os.path.join(script_dir, "..", "..", "..", "skills", "sap-integration", "references", "sap-tables.md")
        if os.path.exists(alt_path):
            try:
                with open(alt_path, "r", encoding="utf-8") as f:
                    tables_md = f.read()
            except Exception:
                tables_md = ""

    prompt = f"""你是一位 SAP 数据查询专家。请根据用户的自然语言问题，生成一个 JSON 格式的 SAP 查询计划（QueryPlan）。

可用 SAP 表（仅允许使用这些表）：{tables_str}
可用只读 BAPI：{bapis_str}

领域（domain）必须从以下选项中选择：master_data, procurement, sales, finance, inventory, production, cost。

来源（source）说明：
- table：单表查询，通过 RFC_READ_TABLE 读取指定表
- multi_table：多表关联查询，目前支持 VBAK+VBAP 销售订单头+行项目、VBAK+VBAP+MAKT 按物料描述过滤、AUFK+AFPO 生产订单头+行项目、AUFK+AFKO 生产订单排程
- bapi：调用只读 BAPI。当问题能用只读 BAPI 解决时（如查客户未清项、客户余额、销售订单状态），优先使用 bapi，并填写 bapi_name、bapi_parameters、bapi_output_table_path

重要约束：
- 底层使用 SAP RFC_READ_TABLE，不支持 SQL 子查询（如 IN (SELECT ...)），必须改用 JOIN
- 不支持聚合函数（SUM/COUNT/AVG/MIN/MAX）和 GROUP BY，fields 中严禁出现 "SUM(NETWR)" 这类表达式，只能放真实表字段名；如需汇总请在 fields 中保留明细字段，由后端在应用层聚合
- 多表查询的字段必须使用 "表名.字段名" 格式
- 主表 where 只能包含主表字段条件；涉及关联表的过滤条件必须放到对应 join 的 where 中
- join_type 说明：left 保留主表所有行；inner 只保留关联表也命中的行。当关联表的 where 用于过滤结果（如按物料描述筛选）时，必须使用 "inner"
- 当用户要求 "最大的 N 条" / "最小的 N 条" / "Top N" 时，必须设置 top_n=N，同时保持 max_rows 足够大（如 5000 或更大），以便后端在应用层排序后截断
- BAPI 参数中客户号/供应商号/物料号/订单号等 ALPHA 类型需前导补零；日期使用 YYYYMMDD
- BAPI 输出字段默认只取 default_fields，严禁返回全部字段
- 若问题涉及多表（fields 中出现多个表名前缀），可只声明主表和所需字段；若未写 joins，后端会根据 catalog relations 自动补全关联

输出必须是严格的 JSON 对象，不要包含任何 markdown 代码块或其他解释文字，仅返回 JSON。JSON 结构如下：
{{
  "intent": "意图标识，英文小写下划线",
  "domain": "领域",
  "source": "table|multi_table|bapi",
  "table": "主表名",
  "fields": ["字段1", "字段2"],
  "where": "Open SQL WHERE 条件，日期使用 YYYYMMDD 格式字符串，例如 ERDAT BETWEEN '20260101' AND '20260721'",
  "aggregation": null 或 "SUM"/"COUNT"/"AVG",
  "group_by": [],
  "order_by": ["字段 DESC"],
  "max_rows": 5000,
  "top_n": 0,
  "field_mapping": {{"字段": "中文表头"}},
  "joins": [],
  "bapi_name": "",
  "bapi_parameters": {{}},
  "bapi_output_table_path": "",
  "pre_query": null,
  "confidence": 0.85
}}

pre_query 用于混合编排：当需要用 BAPI 先取一组 key（如客户所有销售订单号），再用这些 key 去查表时，设置 pre_query。pre_query 结构：
{{
  "source": "bapi",
  "bapi_name": "BAPI_SALESORDER_GETLIST",
  "bapi_parameters": {{"CUSTOMER_NUMBER":"0001100001","DOCUMENT_DATE":"20260101","DOCUMENT_DATE_TO":"20260731"}},
  "bapi_output_table_path": "SALES_ORDERS",
  "output_key_field": "DOC_NUMBER"
}}
主查询 where 中使用占位符 {{PRE_QUERY_KEYS}}，例如 "VBAK.VBELN IN ({{PRE_QUERY_KEYS}})"

字段业务含义参考：
{chr(10).join(catalog_field_lines)}

只读 BAPI 说明：
{chr(10).join(catalog_bapi_lines)}

SAP 常用表、字段与表关联关系完整参考（必须据此选择正确的表和字段）：
{tables_md}

注意：信用额度（KNKK）和付款历史（BSAD）存储在不同表中，一次查询只能返回一个表的结果。若用户同时询问两者，优先按付款历史（BSAD）生成计划。

时间词处理规则（today={today_str}）：
- "今年" / "本年度" / "YTD"：从今年 1 月 1 日到 {today_str}
- "本月" / "这个月" / "当月"：从本月 1 日到本月最后一天
- "上月" / "上个月"：上个月完整范围
- "本季度" / "这个季度" / "当季"：本季度完整范围
- "上季度" / "上个季度"：上一季度完整范围
- "第一季度" / "Q1" / "第1季度"：当年 1 月 1 日至 3 月 31 日
- "第二季度" / "Q2" / "第2季度"：当年 4 月 1 日至 6 月 30 日
- "第三季度" / "Q3" / "第3季度"：当年 7 月 1 日至 9 月 30 日
- "第四季度" / "Q4" / "第4季度"：当年 10 月 1 日至 12 月 31 日
- "去年Q1" / "去年第一季度"：去年对应季度
- "去年" / "上一年" / "上年度"：去年 1 月 1 日到去年 12 月 31 日
- "近 N 个月" / "最近 N 个月" / "近 N 年" / "最近 N 年" / "近 N 天" / "最近 N 天"：从今天往前推 N 个单位（含今天），例如 "近三个月" = {today_str} 往前推 3 个月
- "近半年" / "最近半年"：从今天往前推 6 个月
- "截止目前" / "截至目前" / "累计" / "总共" / "所有" / "全部"：不限制日期范围，where 留空
- 没有明确时间词时：默认今年 1 月 1 日至今

示例：
问题："今年新增了多少客户" → fields 包含 ["KUNNR","NAME1","ERDAT"]，where 为 "ERDAT BETWEEN '20260101' AND '{today_str.replace('-', '')}'"
问题："今年新增的物料号有多少" → table 为 MARA，fields 包含 ["MATNR","ERSDA"]，where 为 "ERSDA BETWEEN '20260101' AND '{today_str.replace('-', '')}'"
问题："本月采购金额" → table 为 EKPO，fields 包含 ["EBELN","BEDAT","BRTWR","WAERS"]，where 为 "BEDAT BETWEEN '20260701' AND '20260731'"
问题："销售订单明细" → source 为 multi_table，table 为 VBAK，joins 包含 VBAP
问题："今年销量情况" → source 为 table，table 为 VBRK，fields 包含 ["VBELN","FKDAT","KUNNR","NETWR","WAERK"]，where 为 "FKDAT BETWEEN '20260101' AND '20260722'"
问题："今年三角连接件卖了多少" → source 为 multi_table，table 为 VBAK，fields 包含 ["VBAK.VBELN","VBAP.POSNR","VBAP.MATNR","VBAP.KWMENG","MAKT.MAKTX"]，where 仅含 "VBAK.AUDAT BETWEEN '20260101' AND '20260721'"，joins 为 [{{"table":"VBAP","on":"VBAK.VBELN = VBAP.VBELN","join_type":"left"}},{{"table":"MAKT","on":"VBAP.MATNR = MAKT.MATNR","where":"MAKT.MAKTX LIKE '%三角连接件%' AND MAKT.SPRAS = '1'","join_type":"inner"}}]
问题："今年生产成本是多少" → source 为 multi_table，table 为 AUFK，fields 包含 ["AUFK.AUFNR","AUFK.AUART","AUFK.WERKS","COEP.KSTAR","COEP.WKG001"]，joins 为 [{{"table":"AFPO","on":"AUFK.AUFNR = AFPO.AUFNR","join_type":"left"}},{{"table":"COEP","on":"AUFK.OBJNR = COEP.OBJNR","where":"COEP.GJAHR = '2026'","join_type":"inner"}}]
问题："本月未清客户账款" → source 为 bapi，bapi_name 为 "BAPI_AR_ACC_GETOPENITEMS"，bapi_parameters 为 {{"COMPANYCODE":"1000","CUSTOMER":"0001100001","KEYDATE":"{today_str.replace('-', '')}"}}，bapi_output_table_path 为 "OPENITEMS"，fields 取 ["COMP_CODE","CUSTOMER","DOC_NO","FIS_PERIOD","BLINE_DATE","DMBTR","WAERS"]
问题："上个月生产订单完成情况" → source 为 multi_table，table 为 AUFK，fields 包含 ["AUFK.AUFNR","AUFK.AUART","AUFK.WERKS","AFKO.GSTRP","AFKO.GLTRP","TJ02T.TXT30"]，joins 为 [{{"table":"AFKO","on":"AUFK.AUFNR = AFKO.AUFNR","join_type":"left"}},{{"table":"JEST","on":"AUFK.OBJNR = JEST.OBJNR","join_type":"left"}},{{"table":"TJ02T","on":"JEST.STAT = TJ02T.ISTAT","where":"TJ02T.SPRAS = '1'","join_type":"inner"}}]
问题："今年总账科目余额" → source 为 multi_table，table 为 SKA1，fields 包含 ["SKA1.SAKNR","SKA1.TXT20","BSEG.HKONT","BSEG.WRBTR"]（禁止写 SUM(...)），joins 为 [{{"table":"BSEG","on":"SKA1.SAKNR = BSEG.HKONT","join_type":"inner"}}]
问题："本月生产订单" → source 为 multi_table，table 为 AUFK，fields 包含 ["AUFK.AUFNR","AUFK.AUART","AUFK.WERKS","AFKO.GAMNG","AUFK.ERDAT"]，where 为 "AUFK.ERDAT BETWEEN '20260701' AND '20260731'"，joins 为 [{{"table":"AFKO","on":"AFKO.AUFNR = AUFK.AUFNR","join_type":"left"}}]
问题："生产订单明细" → source 为 multi_table，table 为 AUFK，fields 包含 ["AUFK.AUFNR","AUFK.AUART","AUFK.WERKS","AFPO.POSNR","AFPO.MATNR","AFPO.MEINS","AFPO.MENGE"]，where 仅含 "AUFK.ERDAT BETWEEN '20260101' AND '{today_str.replace('-', '')}'"，joins 为 [{{"table":"AFPO","on":"AFPO.AUFNR = AUFK.AUFNR","join_type":"inner"}}]
问题："生产订单排程" → source 为 multi_table，table 为 AUFK，fields 包含 ["AUFK.AUFNR","AUFK.AUART","AUFK.WERKS","AFKO.GSTRP","AFKO.GLTRP","AFKO.GAMNG"]，joins 为 [{{"table":"AFKO","on":"AFKO.AUFNR = AUFK.AUFNR","join_type":"left"}}]
问题："本月应收账款" → source 为 table，table 为 BSID，fields 包含 ["KUNNR","BUKRS","BELNR","GJAHR","BUZEI","DMBTR","BUDAT"]，where 为 "BUDAT BETWEEN '20260701' AND '20260731'"
问题："本月应付账款" → source 为 table，table 为 BSIK，fields 包含 ["LIFNR","BUKRS","BELNR","GJAHR","BUZEI","DMBTR","BUDAT"]，where 为 "BUDAT BETWEEN '20260701' AND '20260731'"
问题："查询客户 0001100001 的信用额度" → source 为 table，table 为 KNKK，fields 包含 ["KUNNR","KKBER","KLIMK","OBLIG","SKFOR","KNKAU"]，where 为 "KUNNR = '0001100001'"
问题："查询客户 0001100001 的付款历史" → source 为 table，table 为 BSAD，fields 包含 ["KUNNR","BUKRS","BELNR","GJAHR","BUZEI","DMBTR","BUDAT","AUGDT","AUGBL"]，where 为 "KUNNR = '0001100001' AND BUDAT BETWEEN '20260101' AND '20260731'"
问题："查询客户 0001100001 的信用额度和付款历史" → source 为 table，table 为 BSAD，fields 包含 ["KUNNR","BUKRS","BELNR","GJAHR","BUZEI","DMBTR","BUDAT","AUGDT","AUGBL"]，where 为 "KUNNR = '0001100001' AND BUDAT BETWEEN '20260101' AND '20260731'"
问题："MARA 中 DISST 不为空且数字最大的 20 条" → source 为 table，table 为 MARA，fields 包含 ["MATNR","DISST"]，where 为 "DISST <> ''"，order_by 为 ["DISST DESC"]，max_rows 为 5000，top_n 为 20，field_mapping 为 {{"MATNR":"物料编码","DISST":"低层次码"}}
问题："查询客户 0001100001 今年的销售订单明细" → source 为 multi_table，table 为 VBAK，fields 包含 ["VBAK.VBELN","VBAK.AUDAT","VBAK.KUNNR","VBAP.POSNR","VBAP.MATNR","VBAP.KWMENG"]，where 为 "VBAK.VBELN IN ({{PRE_QUERY_KEYS}}) AND VBAK.AUDAT BETWEEN '20260101' AND '{today_str.replace('-', '')}'"，joins 为 [{{"table":"VBAP","on":"VBAK.VBELN = VBAP.VBELN","join_type":"left"}}]，pre_query 为 {{"source":"bapi","bapi_name":"BAPI_SALESORDER_GETLIST","bapi_parameters":{{"CUSTOMER_NUMBER":"0001100001","DOCUMENT_DATE":"20260101","DOCUMENT_DATE_TO":"{today_str.replace('-', '')}"}},"bapi_output_table_path":"SALES_ORDERS","output_key_field":"DOC_NUMBER"}}
问题："查物料 1000000082 的库存" → source 为 table，table 为 MARD，fields 包含 ["MATNR","WERKS","LGORT","LABST","MEINS"]，where 为 "MATNR = '00000000001000000082'"
"""
    return prompt


def _build_user_prompt(question: str, context: Dict[str, Any], available_tables: List[str]) -> str:
    today = datetime.now()
    date_hint = f"""
当前日期：{today.strftime('%Y-%m-%d')}
用户未填写 date_from 时，请根据问题中的时间词推导；如没有明确时间词，默认使用今年 1 月 1 日至今。
用户提供的上下文：{json.dumps(context, ensure_ascii=False)}
"""
    # 重规划模式：在提示中加入失败上下文
    if context.get("is_replan"):
        failed_table = context.get("failed_table", "")
        failed_fields = context.get("failed_fields", [])
        failed_where = context.get("failed_where", "")
        failed_source = context.get("failed_source", "table")
        error_message = context.get("error_message", "")
        replan_hint = f"""

【重要】这是一个修正重规划请求。上一轮查询计划执行失败，请根据错误信息生成修正后的计划。

上一轮失败信息：
- 问题：{question}
- 失败的表：{failed_table}
- 失败的字段：{failed_fields}
- 失败的WHERE条件：{failed_where}
- 来源类型：{failed_source}
- SAP 错误：{error_message}

请分析失败原因，选择正确的表和字段生成修正计划。特别注意：
1. 如果错误是 FIELD_NOT_VALID，说明字段不存在于该表中，请选择正确的表或移除无效字段
2. 如果错误涉及编码格式，请确保 MATNR/KUNNR/LIFNR/VBELN/EBELN/AUFNR 等ALPHA字段补零到标准长度
3. 如果表不存在，请从 SAP 常用表参考中选择同领域的替代表
4. 参考上面提供的 SAP 常用表与字段速查文档来确定正确的表和字段
"""
        date_hint += replan_hint

    return f"问题：{question}\n可用 SAP 表：{', '.join(available_tables) if available_tables else '未限制'}{date_hint}\n请直接输出 QueryPlan JSON："


# -------------------------------------------------------------------------
# 日期推导
# -------------------------------------------------------------------------

def _infer_dates(question_lower: str, context: Dict[str, Any]) -> Dict[str, Any]:
    today = datetime.now()
    result = dict(context)

    def _ensure(date_from: str, date_to: str):
        if not result.get("date_from"):
            result["date_from"] = date_from
        if not result.get("date_to"):
            result["date_to"] = date_to

    if _match_specific_quarter(question_lower, today, _ensure):
        pass
    elif any(kw in question_lower for kw in ["今年", "本年度", "ytd", "year to date"]):
        _ensure(today.replace(month=1, day=1).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["本月", "这个月", "当月"]):
        start = today.replace(day=1)
        end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["上月", "上个月"]):
        first_day = today.replace(day=1)
        end = first_day - timedelta(days=1)
        start = end.replace(day=1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["本季度", "这个季度", "当季"]):
        q = (today.month - 1) // 3
        start = today.replace(month=q * 3 + 1, day=1)
        end = (today.replace(month=q * 3 + 4, day=1) if q < 3 else today.replace(year=today.year + 1, month=1, day=1)) - timedelta(days=1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["上季度", "上一季度", "上个季度"]):
        q = (today.month - 1) // 3 - 1
        year = today.year
        if q < 0:
            q = 3
            year -= 1
        start = datetime(year, q * 3 + 1, 1)
        end = (datetime(year, q * 3 + 4, 1) if q < 3 else datetime(year + 1, 1, 1)) - timedelta(days=1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["去年", "上一年", "上年度"]):
        last_year = today.year - 1
        _ensure(f"{last_year}-01-01", f"{last_year}-12-31")
    elif _match_specific_year(question_lower, today, _ensure):
        pass
    elif any(kw in question_lower for kw in ["同期", "同比"]):
        # 去年同期/同比：去年 1 月 1 日 至 今天同期
        last_year = today.year - 1
        _ensure(f"{last_year}-01-01", today.strftime("%Y-%m-%d"))
    elif _match_recent_period(question_lower, today, _ensure):
        pass
    elif "这几个月" in question_lower:
        # "这几个月" 视为近 3 个月（从今天往前推 3 个月，含今天）
        start = today
        for _ in range(3):
            start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
        _ensure(start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    elif any(kw in question_lower for kw in ["截止目前", "截至目前", "累计", "总共", "所有", "全部"]):
        result.setdefault("date_from", "")
        result.setdefault("date_to", "")
    else:
        # 默认今年至今
        if not result.get("date_from") and not result.get("date_to"):
            _ensure(today.replace(month=1, day=1).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))

    return result


def _match_specific_quarter(question_lower: str, today: datetime, _ensure) -> bool:
    """处理 '第一季度 / Q1 / 去年Q1 / 去年第一季度' 等具体季度表达。"""
    quarter_map = {
        "第一季度": 1, "q1": 1, "第1季度": 1,
        "第二季度": 2, "q2": 2, "第2季度": 2,
        "第三季度": 3, "q3": 3, "第3季度": 3,
        "第四季度": 4, "q4": 4, "第4季度": 4,
    }
    quarter = None
    for kw, q in quarter_map.items():
        if kw in question_lower:
            quarter = q
            break
    if quarter is None:
        return False

    year = today.year
    if any(kw in question_lower for kw in ["去年", "上一年", "上年度"]):
        year -= 1

    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = datetime(year, start_month, 1)
    if end_month == 12:
        end = datetime(year, 12, 31)
    else:
        end = (datetime(year, end_month + 1, 1) - timedelta(days=1))

    _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    return True


def _match_specific_year(question_lower: str, today: datetime, _ensure) -> bool:
    """处理 '2025年'、'2025年同期' 等具体年份表达。"""
    match = re.search(r"(?<!\d)(19|20)(\d{2})(?!\d)", question_lower)
    if not match:
        return False
    year = int(match.group(1) + match.group(2))
    if "同期" in question_lower:
        _ensure(f"{year}-01-01", today.strftime("%Y-%m-%d"))
    else:
        _ensure(f"{year}-01-01", f"{year}-12-31")
    return True


def _match_recent_period(question_lower: str, today: datetime, _ensure) -> bool:
    """处理 '近 N 个月/天/年' 这类相对时间表达。"""
    cn_numbers = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }

    def _parse_number(s: str) -> int:
        s = s.strip()
        if s.isdigit():
            return int(s)
        total = 0
        for ch in s:
            if ch in cn_numbers:
                total += cn_numbers[ch]
        return total if total > 0 else 1

    match = re.search(r"(?:近|最近)\s*([一二两三四五六七八九十\d]+|半)\s*个?\s*(月|天|日|年)", question_lower)
    if not match:
        return False

    raw_num = match.group(1)
    unit = match.group(2)
    if raw_num == "半":
        num = 6
        unit = "月"
    else:
        num = _parse_number(raw_num)

    end = today
    if unit in ("月",):
        month = today.month - num
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        try:
            start = today.replace(year=year, month=month)
        except ValueError:
            next_month = (today.replace(year=year, month=month, day=1) + timedelta(days=32)).replace(day=1)
            start = next_month - timedelta(days=1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    elif unit in ("年",):
        try:
            start = today.replace(year=today.year - num)
        except ValueError:
            start = today.replace(year=today.year - num, month=2, day=28)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    else:
        # 天/日
        start = today - timedelta(days=num - 1)
        _ensure(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    return True


# -------------------------------------------------------------------------
# 模板兜底
# -------------------------------------------------------------------------

def _escape(value: str) -> str:
    return value.replace("'", "''")


def _render_where(template: str, context: Dict[str, Any]) -> str:
    date_from = context.get("date_from", "")
    date_to = context.get("date_to", "")
    company_code = context.get("company_code", "")
    rendered = template.format(
        date_from_sap=date_from.replace("-", ""),
        date_to_sap=date_to.replace("-", ""),
        company_code=_escape(company_code),
        date_from=date_from,
        date_to=date_to,
    )
    # RFC_READ_TABLE 不接受 1=1 常量表达式，无日期范围时返回空 WHERE
    if not date_from and not date_to and "between" in template.lower():
        return ""
    return rendered


def _fallback_plan(question: str, context: Dict[str, Any], available_tables: List[str]) -> Optional[Dict[str, Any]]:
    question_lower = question.lower()
    context = _infer_dates(question_lower, context)

    best_tpl = None
    best_score = 0
    for tpl in _V2_TEMPLATES:
        score = sum(1 for kw in tpl.get("keywords", []) if kw.lower() in question_lower)
        if available_tables and tpl.get("table", "").upper() not in [t.upper() for t in available_tables]:
            score = max(0, score - 1)
        if score > best_score:
            best_score = score
            best_tpl = tpl

    if not best_tpl or best_score == 0:
        return None

    max_rows = min(int(context.get("max_rows", best_tpl.get("max_rows", 5000))), 10000)
    return {
        "intent": best_tpl["intent"],
        "domain": best_tpl["domain"],
        "source": best_tpl["source"],
        "table": best_tpl.get("table", ""),
        "fields": best_tpl.get("fields", []),
        "where": _render_where(best_tpl.get("where_template", ""), context),
        "aggregation": best_tpl.get("aggregation"),
        "group_by": best_tpl.get("group_by", []),
        "order_by": best_tpl.get("order_by", []),
        "max_rows": max_rows,
        "field_mapping": best_tpl.get("field_mapping", {}),
        "joins": best_tpl.get("joins", []),
        "bapi_name": "",
        "bapi_parameters": {},
        "bapi_output_table_path": "",
        "confidence": 0.6,
    }
    return plan


# -------------------------------------------------------------------------
# 基于 catalog 的 plan 后处理（自动 JOIN 补全 / BAPI 字段精简）
# -------------------------------------------------------------------------
def _parse_relation_key(key: str) -> tuple:
    """解析关系键，如 KNA1.KUNNR 或 BKPF.BUKRS+BELNR+GJAHR。"""
    if "." not in key:
        return key.upper(), []
    table, fields_str = key.split(".", 1)
    return table.upper(), [f.upper() for f in fields_str.split("+")]


def _build_relation_graph(relations: Dict[str, List[str]]) -> Dict[str, Dict[str, tuple]]:
    """把 relations 构建成无向图：表 -> {邻接表: (本表字段, 邻接表字段)}。"""
    graph = defaultdict(dict)
    for key, values in relations.items():
        left_table, left_fields = _parse_relation_key(key)
        if not left_fields:
            continue
        for val in values:
            right_table, right_fields = _parse_relation_key(val)
            if not right_fields or right_table == left_table:
                continue
            graph[left_table][right_table] = (left_fields, right_fields)
            graph[right_table][left_table] = (right_fields, left_fields)
    return graph


def _find_join_path(main_table: str, target_table: str, catalog: Dict[str, Any]) -> List[tuple]:
    """在 catalog relations 中找从主表到目标表的最短 JOIN 路径。"""
    main = main_table.upper()
    target = target_table.upper()
    if main == target:
        return []
    graph = _build_relation_graph(catalog.get("relations", {}))
    if main not in graph:
        return []

    queue = [(main, [])]
    visited = {main}
    while queue:
        current, path = queue.pop(0)
        for neighbor, (cur_fields, nei_fields) in graph.get(current, {}).items():
            if neighbor in visited:
                continue
            new_path = path + [(current, neighbor, cur_fields, nei_fields)]
            if neighbor == target:
                return new_path
            visited.add(neighbor)
            queue.append((neighbor, new_path))
    return []


def _enrich_plan_with_catalog(plan: Dict[str, Any]) -> None:
    """根据 catalog 自动补全 JOIN、精简 BAPI 字段。"""
    catalog = _load_catalog()
    apis = catalog.get("apis", {})
    source = plan.get("source", "table")
    main_table = (plan.get("table") or "").upper()

    # BAPI 字段精简：只保留 default_fields
    if source == "bapi" and plan.get("bapi_name"):
        bapi_meta = apis.get(plan["bapi_name"].upper())
        if bapi_meta:
            default_fields = [f.upper() for f in bapi_meta.get("default_fields", [])]
            if default_fields:
                requested = [f.upper() for f in plan.get("fields", [])]
                pruned = [f for f in requested if f in default_fields]
                plan["fields"] = pruned if pruned else list(default_fields)
            field_mapping = bapi_meta.get("field_mapping", {})
            plan["field_mapping"] = {
                k: v for k, v in field_mapping.items()
                if k.upper() in [f.upper() for f in plan["fields"]]
            }
        return

    # 自动补全 JOIN：fields 中出现其他表前缀时，从 catalog 找关联路径
    if source in ("table", "multi_table") and main_table:
        fields = plan.get("fields", [])
        mentioned_tables = set()
        for f in fields:
            if "." in f:
                mentioned_tables.add(f.split(".", 1)[0].upper())
            else:
                mentioned_tables.add(main_table)
        mentioned_tables.discard(main_table)

        existing_join_tables = {j.get("table", "").upper() for j in plan.get("joins", [])}
        new_joins = []
        for target in mentioned_tables:
            if target in existing_join_tables:
                continue
            path = _find_join_path(main_table, target, catalog)
            if not path:
                continue
            for from_table, to_table, from_fields, to_fields in path:
                on_parts = [
                    f"{from_table}.{lf} = {to_table}.{rf}"
                    for lf, rf in zip(from_fields, to_fields)
                ]
                on = " AND ".join(on_parts)
                target_meta = apis.get(to_table, {})
                join_fields = target_meta.get("default_fields", list(target_meta.get("fields", {}).keys())) or []
                new_joins.append({
                    "table": to_table,
                    "on": on,
                    "join_type": "left",
                    "fields": join_fields,
                    "where": "",
                })
                existing_join_tables.add(to_table)
        if new_joins:
            plan.setdefault("joins", []).extend(new_joins)
            if plan.get("source") == "table":
                plan["source"] = "multi_table"


# -------------------------------------------------------------------------
# LLM 计划生成
# -------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[str]:
    """从模型返回文本中提取 JSON。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _llm_plan(question: str, context: Dict[str, Any], available_tables: List[str]) -> Optional[Dict[str, Any]]:
    """调用 LLM 生成 QueryPlan。"""
    messages = [
        {"role": "system", "content": _build_system_prompt(available_tables)},
        {"role": "user", "content": _build_user_prompt(question, context, available_tables)},
    ]
    content = _call_llm(messages)
    if not content:
        return None

    try:
        json_text = _extract_json(content)
        plan = json.loads(json_text)
        if not isinstance(plan, dict):
            return None
        # 合并用户上下文中的明确参数
        for key in ["date_from", "date_to", "company_code", "max_rows"]:
            if key in context and context[key]:
                if key == "max_rows":
                    plan[key] = min(int(context[key]), 10000)
                else:
                    # 仅当模型没填或冲突时才覆盖
                    if key not in plan or not plan[key]:
                        plan[key] = context[key]
        # 确保 where 与日期一致
        plan["confidence"] = plan.get("confidence", 0.85)
        return plan
    except Exception as e:
        print(json.dumps({"status": "parse_warning", "message": str(e)}, ensure_ascii=False), file=sys.stderr)
        return None


# -------------------------------------------------------------------------
# 主入口
# -------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "message": f"invalid json: {e}"}, ensure_ascii=False))
            sys.exit(1)
    else:
        question = sys.argv[1] if len(sys.argv) > 1 else ""
        context = {}
        if len(sys.argv) > 2:
            context["date_from"] = sys.argv[2]
        if len(sys.argv) > 3:
            context["date_to"] = sys.argv[3]
        payload = {"question": question, "context": context, "available_tables": []}

    question = payload.get("question", "")
    context = payload.get("context", {})
    available_tables = payload.get("available_tables", [])

    # 先推导日期上下文
    context = _infer_dates(question.lower(), context)

    # 尝试 LLM
    plan = _llm_plan(question, context, available_tables)
    if plan:
        _enrich_plan_with_catalog(plan)
        print(json.dumps({"status": "success", "plan": plan}, ensure_ascii=False, indent=2))
        sys.exit(0)

    # LLM 失败则回退到本地模板
    plan = _fallback_plan(question, context, available_tables)
    if plan:
        _enrich_plan_with_catalog(plan)
        print(json.dumps({"status": "success", "plan": plan}, ensure_ascii=False, indent=2))
        sys.exit(0)

    print(
        json.dumps(
            {
                "status": "error",
                "message": "V2 planner 无法匹配查询意图，请使用更明确的关键词或联系管理员扩展模板。",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
