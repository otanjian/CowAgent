"""SAP 场景查询配置与字段映射。

每个需要对接 SAP 的子场景都需要在这里配置：
- ADT_SQL_TEMPLATES: ADT SQL 查询模板（run-sql 方式）
- RFC_QUERIES: RFC_READ_TABLE 查询参数（pyrfc 方式）
- FIELD_MAPPINGS: SAP 字段到中文表头的映射

新增场景时只需要在这三个字典里增加对应配置即可。
"""

from typing import Any, Dict, List

# =============================================================================
# ADT SQL 模板
# 使用 Open SQL 语法，模板参数通过 .format() 注入。
# 注意：ADT Data Preview 对返回行数和数据量有限制，适合中小数据量场景。
# =============================================================================
def _build_sql_where(supplier_code_range: str = "", lifnr_field: str = "a~lifnr") -> str:
    """根据供应商编码范围构造 Open SQL WHERE 片段。"""
    conditions = []
    if supplier_code_range:
        parts = [p.strip() for p in supplier_code_range.split("-")]
        escaped = [p.replace("'", "''") for p in parts]
        if len(parts) == 2:
            conditions.append(f"{lifnr_field} BETWEEN '{escaped[0]}' AND '{escaped[1]}'")
        else:
            conditions.append(f"{lifnr_field} = '{escaped[0]}'")
    return " AND ".join(conditions) if conditions else ""


ADT_SQL_TEMPLATES: Dict[str, str] = {
    "supplier_qualification": """
        SELECT
            a~lifnr   AS supplier_code,
            a~name1   AS company_name,
            a~ort01   AS city,
            a~land1   AS country,
            a~telf1   AS phone,
            a~stras   AS address,
            a~pstlz   AS postal_code,
            a~erdat   AS created_date
        FROM lfa1 AS a
        WHERE a~loevm = ''
          AND a~sperr = ''
          {where_clause}
        ORDER BY a~lifnr
        UP TO {max_rows} ROWS
    """,
    "supplier_performance": """
        SELECT
            a~lifnr   AS supplier_code,
            a~name1   AS company_name,
            b~waers   AS currency,
            b~zterm   AS payment_terms
        FROM lfa1 AS a
        LEFT OUTER JOIN lfm1 AS b ON a~lifnr = b~lifnr
        WHERE a~loevm = ''
          {where_clause}
        ORDER BY a~lifnr
        UP TO {max_rows} ROWS
    """,
    "supplier_risk": """
        SELECT
            a~lifnr   AS supplier_code,
            a~name1   AS company_name,
            a~stcd1   AS credit_code,
            a~stcd2   AS tax_code_supplement,
            a~ktokk   AS account_group,
            a~sperr   AS posting_block,
            a~loevm   AS delete_flag,
            a~land1   AS country,
            a~ort01   AS city,
            a~erdat   AS created_date
        FROM lfa1 AS a
        WHERE {where_clause}
        ORDER BY a~lifnr
        UP TO {max_rows} ROWS
    """,
}

# =============================================================================
# RFC 查询配置
# 使用 RFC_READ_TABLE 读取 SAP 表，支持字段选择、WHERE 条件、分批读取。
# 字段名必须大写，WHERE 条件使用 Open SQL 语法。
# =============================================================================
RFC_QUERIES: Dict[str, Dict[str, Any]] = {
    # -------------------------------------------------------------------------
    # 供应商管理
    # -------------------------------------------------------------------------
    "supplier_qualification": {
        "table": "LFA1",
        "fields": [
            "LIFNR",   # 供应商编码
            "NAME1",   # 名称
            "ORT01",   # 城市
            "LAND1",   # 国家
            "TELF1",   # 电话
            "STRAS",   # 地址
            "PSTLZ",   # 邮编
            "ERDAT",   # 创建日期
        ],
        "where": "LOEVM = '' AND SPERR = ''",
        "filters": {"supplier_code_range": "LIFNR"},
    },
    "supplier_performance": {
        "table": "LFA1",
        "fields": [
            "LIFNR",
            "NAME1",
        ],
        "where": "LOEVM = ''",
        "filters": {"supplier_code_range": "LIFNR"},
    },
    "supplier_classification": {
        "table": "LFA1",
        "fields": [
            "LIFNR",   # 供应商编码
            "NAME1",   # 名称
            "KTOKK",   # 供应商账户组（用于分级）
            "LAND1",   # 国家
            "ORT01",   # 城市
            "ERDAT",   # 创建日期
        ],
        "where": "LOEVM = ''",
        "filters": {"supplier_code_range": "LIFNR"},
    },
    "supplier_risk": {
        "table": "LFA1",
        "fields": [
            "LIFNR",   # 供应商编码
            "NAME1",   # 名称
            "STCD1",   # 统一社会信用代码（天机商查核对关键字段）
            "STCD2",   # 税号补充
            "KTOKK",   # 账户组（分级）
            "SPERR",   # 过账冻结
            "LOEVM",   # 删除标记
            "LAND1",   # 国家
            "ORT01",   # 城市
            "ERDAT",   # 创建日期（判断新供应商加分）
        ],
        "where": "",
        "filters": {
            "supplier_code_range": "LIFNR",
            "materials": "LIFNR",       # ERP 同步过滤器：供应商编码输入框
            "material_names": "NAME1",  # ERP 同步过滤器：供应商名称输入框
        },
    },

    # -------------------------------------------------------------------------
    # 采购成本控制
    # -------------------------------------------------------------------------
    "tco_analysis": {
        "table": "EKPO",
        "fields": [
            "EBELN",   # 采购订单号
            "EBELP",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "NETPR",   # 净价
            "BRTWR",   # 总金额
            "WAERS",   # 货币
            "WERKS",   # 工厂
            "AEDAT",   # 创建日期
        ],
        "where": "",
        "filters": {},
    },
    "market_research": {
        "table": "EKPO",
        "fields": [
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "NETPR",   # 净价
            "WAERS",   # 货币
            "WERKS",   # 工厂
            "AEDAT",   # 创建日期
        ],
        "where": "",
        "filters": {},
    },
    "negotiation_strategy": {
        "table": "EKPO",
        "fields": [
            "EBELN",   # 采购订单号
            "EBELP",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "NETPR",   # 净价
            "WAERS",   # 货币
            "WERKS",   # 工厂
            "AEDAT",   # 创建日期
        ],
        "where": "",
        "filters": {},
    },
    "cost_reduction": {
        "table": "EKPO",
        "fields": [
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MATKL",   # 物料组
            "MENGE",   # 数量
            "NETPR",   # 净价
            "BRTWR",   # 总金额
            "WAERS",   # 货币
            "WERKS",   # 工厂
        ],
        "where": "",
        "filters": {},
    },
    "supplier_quote_comparison": {
        "table": "EKPO",
        "fields": [
            "MATNR",   # 物料编码
            "TXZ01",   # 物料短文本
            "NETPR",   # 净价
            "MENGE",   # 采购数量
            "NETWR",   # 净价金额
            "EBELN",   # 采购订单号
            "EBELP",   # 采购订单行项目
            "AEDAT",   # 创建日期
        ],
        "where": "MATNR <> ''",
        "filters": {
            "materials": "MATNR",
            "material_names": "TXZ01",
            "date_range": "AEDAT",
        },
    },

    # -------------------------------------------------------------------------
    # 招投标辅助
    # -------------------------------------------------------------------------
    "tender_document": {
        "table": "EBAN",
        "fields": [
            "BANFN",   # 采购申请号
            "BNFPO",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "MEINS",   # 单位
            "LFDAT",   # 需求日期
            "WERKS",   # 工厂
            "PREIS",   # 价格
            "WAERS",   # 货币
        ],
        "where": "",
        "filters": {},
    },
    "evaluation_criteria": {
        "table": "EBAN",
        "fields": [
            "BANFN",   # 采购申请号
            "BNFPO",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "MEINS",   # 单位
            "PREIS",   # 价格
            "WAERS",   # 货币
        ],
        "where": "",
        "filters": {},
    },
    "bid_analysis": {
        "table": "EBAN",
        "fields": [
            "BANFN",   # 采购申请号
            "BNFPO",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "MEINS",   # 单位
            "PREIS",   # 价格
            "WAERS",   # 货币
        ],
        "where": "",
        "filters": {},
    },
    "compliance_review": {
        "table": "EBAN",
        "fields": [
            "BANFN",   # 采购申请号
            "BNFPO",   # 行项目
            "MATNR",   # 物料编码
            "TXZ01",   # 物料描述
            "MENGE",   # 数量
            "MEINS",   # 单位
            "PREIS",   # 价格
            "WAERS",   # 货币
        ],
        "where": "",
        "filters": {},
    },
}

# =============================================================================
# 字段映射
# 将 SAP 原始字段名（大小写均可）映射为前端/AI 提示词使用的中文表头。
# 注意：ADT SQL 中建议使用小写别名，RFC 返回的是大写字段名，这里都要覆盖。
# =============================================================================
FIELD_MAPPINGS: Dict[str, Dict[str, str]] = {
    # -------------------------------------------------------------------------
    # 供应商管理
    # -------------------------------------------------------------------------
    "supplier_qualification": {
        # ADT SQL 别名
        "supplier_code": "供应商编码",
        "company_name": "企业名称",
        "city": "城市",
        "country": "国家",
        "phone": "联系电话",
        "address": "注册地址",
        "postal_code": "邮编",
        "created_date": "创建日期",
        # RFC 原始字段名（大写）
        "LIFNR": "供应商编码",
        "NAME1": "企业名称",
        "ORT01": "城市",
        "LAND1": "国家",
        "TELF1": "联系电话",
        "STRAS": "注册地址",
        "PSTLZ": "邮编",
        "ERDAT": "创建日期",
    },
    "supplier_performance": {
        "supplier_code": "供应商编码",
        "company_name": "企业名称",
        "currency": "货币",
        "payment_terms": "付款条件",
        "LIFNR": "供应商编码",
        "NAME1": "企业名称",
    },
    "supplier_classification": {
        "LIFNR": "供应商编码",
        "NAME1": "供应商名称",
        "KTOKK": "当前等级",
        "LAND1": "国家",
        "ORT01": "城市",
        "ERDAT": "创建日期",
    },
    "supplier_risk": {
        "LIFNR": "供应商编码",
        "NAME1": "供应商名称",
        "STCD1": "统一社会信用代码",
        "STCD2": "税号补充",
        "KTOKK": "账户组",
        "SPERR": "过账冻结",
        "LOEVM": "删除标记",
        "LAND1": "国家",
        "ORT01": "城市",
        "ERDAT": "创建日期",
        # ADT SQL 别名映射
        "supplier_code": "供应商编码",
        "company_name": "供应商名称",
        "credit_code": "统一社会信用代码",
        "tax_code_supplement": "税号补充",
        "account_group": "账户组",
        "posting_block": "过账冻结",
        "delete_flag": "删除标记",
        "created_date": "创建日期",
    },

    # -------------------------------------------------------------------------
    # 采购成本控制
    # -------------------------------------------------------------------------
    "tco_analysis": {
        "EBELN": "采购订单号",
        "EBELP": "行项目",
        "MATNR": "物料名称",
        "TXZ01": "物料描述",
        "MENGE": "年采购量",
        "NETPR": "采购单价",
        "BRTWR": "总金额",
        "WAERS": "货币",
        "WERKS": "工厂",
        "AEDAT": "创建日期",
    },
    "market_research": {
        "MATNR": "物料名称",
        "TXZ01": "规格型号",
        "NETPR": "当前价格",
        "WAERS": "货币",
        "WERKS": "工厂",
        "AEDAT": "价格有效期",
    },
    "negotiation_strategy": {
        "EBELN": "采购订单号",
        "EBELP": "行项目",
        "MATNR": "物料名称",
        "TXZ01": "物料描述",
        "MENGE": "采购数量",
        "NETPR": "当前报价",
        "WAERS": "货币",
        "WERKS": "工厂",
        "AEDAT": "创建日期",
    },
    "cost_reduction": {
        "MATNR": "降本项目",
        "TXZ01": "项目描述",
        "MATKL": "分类",
        "MENGE": "数量",
        "NETPR": "当前成本",
        "BRTWR": "目标成本",
        "WAERS": "货币",
        "WERKS": "工厂",
    },
    "supplier_quote_comparison": {
        "MATNR": "物料编码",
        "TXZ01": "物料短文本",
        "NETPR": "净价",
        "MENGE": "采购数量",
        "NETWR": "净价金额",
        "EBELN": "采购订单号",
        "EBELP": "采购订单行项目",
        "AEDAT": "创建日期",
    },

    # -------------------------------------------------------------------------
    # 招投标辅助
    # -------------------------------------------------------------------------
    "tender_document": {
        "BANFN": "项目名称",
        "BNFPO": "项目行号",
        "MATNR": "采购内容",
        "TXZ01": "内容描述",
        "MENGE": "数量",
        "MEINS": "单位",
        "LFDAT": "交货期限",
        "WERKS": "工厂",
        "PREIS": "预算金额",
        "WAERS": "货币",
    },
    "evaluation_criteria": {
        "BANFN": "评审维度",
        "BNFPO": "行项目",
        "MATNR": "评分标准",
        "TXZ01": "标准描述",
        "MENGE": "数量",
        "MEINS": "单位",
        "PREIS": "满分分值",
        "WAERS": "货币",
    },
    "bid_analysis": {
        "BANFN": "投标编号",
        "BNFPO": "行项目",
        "MATNR": "采购物料",
        "TXZ01": "物料描述",
        "MENGE": "数量",
        "MEINS": "单位",
        "PREIS": "投标报价",
        "WAERS": "货币",
    },
    "compliance_review": {
        "BANFN": "审查阶段",
        "BNFPO": "行项目",
        "MATNR": "审查事项",
        "TXZ01": "事项描述",
        "MENGE": "数量",
        "MEINS": "单位",
        "PREIS": "涉及金额",
        "WAERS": "货币",
    },
}
