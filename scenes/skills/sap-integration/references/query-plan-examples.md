# 查询计划示例

本文档展示自然语言问题如何映射为后端 `QueryPlan`。

## 新增客户

**用户问题**：这个月新增了多少家客户

**QueryPlan**：

```json
{
  "intent": "new_customers_this_month",
  "domain": "master_data",
  "source": "table",
  "table": "KNA1",
  "fields": ["KUNNR", "NAME1", "ORT01", "LAND1", "ERDAT"],
  "where": "ERDAT BETWEEN '20250701' AND '20250731'",
  "aggregation": null,
  "group_by": [],
  "order_by": ["ERDAT DESC"],
  "max_rows": 5000,
  "field_mapping": {
    "KUNNR": "客户编码",
    "NAME1": "客户名称",
    "ORT01": "城市",
    "LAND1": "国家",
    "ERDAT": "创建日期"
  }
}
```

## 本月销售额

**用户问题**：本月销售额是多少

**QueryPlan**：

```json
{
  "intent": "sales_amount_this_month",
  "domain": "sales",
  "source": "table",
  "table": "VBRK",
  "fields": ["VBELN", "FKDAT", "KUNNR", "NETWR", "WAERK"],
  "where": "FKDAT BETWEEN '20250701' AND '20250731'",
  "aggregation": "SUM",
  "group_by": ["WAERK"],
  "order_by": [],
  "max_rows": 5000,
  "field_mapping": {
    "VBELN": "发票号",
    "FKDAT": "发票日期",
    "KUNNR": "客户编码",
    "NETWR": "净值",
    "WAERK": "货币"
  }
}
```

## 多表关联：销售订单明细

**用户问题**：本月销售订单及行项目明细

**QueryPlan**：

```json
{
  "intent": "sales_order_items_this_month",
  "domain": "sales",
  "source": "multi_table",
  "table": "VBAK",
  "fields": ["VBAK.VBELN", "VBAK.AUDAT", "VBAK.KUNNR", "VBAP.POSNR", "VBAP.MATNR", "VBAP.NETWR"],
  "where": "VBAK.AUDAT BETWEEN '20250701' AND '20250731'",
  "aggregation": null,
  "group_by": [],
  "order_by": ["VBAK.AUDAT DESC"],
  "max_rows": 5000,
  "joins": [
    {
      "table": "VBAP",
      "on": "VBAP.VBELN = VBAK.VBELN",
      "fields": ["POSNR", "MATNR", "NETWR"],
      "join_type": "inner"
    }
  ],
  "field_mapping": {
    "VBAK.VBELN": "销售订单号",
    "VBAK.AUDAT": "订单日期",
    "VBAK.KUNNR": "客户编码",
    "VBAP.POSNR": "行项目",
    "VBAP.MATNR": "物料编码",
    "VBAP.NETWR": "行项目净值"
  }
}
```

## BAPI：销售订单状态

**用户问题**：查询销售订单 1000000001 的状态

**QueryPlan**：

```json
{
  "intent": "sales_order_status",
  "domain": "sales",
  "source": "bapi",
  "bapi_name": "BAPI_SALESORDER_GETSTATUS",
  "bapi_parameters": {
    "SALESDOCUMENT": "1000000001"
  },
  "bapi_output_table_path": "STATUSINFO",
  "field_mapping": {}
}
```
