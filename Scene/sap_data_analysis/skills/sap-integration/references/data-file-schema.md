# SAP 数据分析 JSON 文件 Schema

本文件由后端 `channel/web/sap/data_exporter.py` 生成，供 `data_analyzer.py` 等脚本消费。

## 顶层结构

```json
{
  "meta": { ... },
  "data": [ ... ]
}
```

## meta

| 字段 | 类型 | 说明 |
|---|---|---|
| `question` | string | 用户的自然语言问题 |
| `connection_id` | string | SAP 连接 ID |
| `connection_name` | string | SAP 连接名称（如"日泰QAS"） |
| `system` | string | 固定为 "sap" |
| `tenant_id` | string | 当前租户 ID |
| `query_plan` | object | 后端生成的 QueryPlan |
| `generated_at` | string | ISO8601 格式生成时间 |
| `total_rows` | integer | 数据行数 |
| `files.json` | string | JSON 文件绝对路径 |

## query_plan

| 字段 | 类型 | 说明 |
|---|---|---|
| `intent` | string | 意图标识 |
| `domain` | string | 领域：master_data/procurement/sales/finance/inventory |
| `source` | string | 数据来源：table/multi_table/bapi |
| `table` | string | 主表 |
| `fields` | array<string> | 查询字段 |
| `where` | string | Open SQL WHERE 条件 |
| `aggregation` | string | 聚合方式：COUNT/SUM/AVG/NONE |
| `group_by` | array<string> | 分组字段 |
| `order_by` | array<string> | 排序字段 |
| `max_rows` | integer | 最大返回行数 |
| `field_mapping` | object | SAP 字段 → 中文表头 |
| `joins` | array | 多表关联配置 |
| `bapi_name` | string | BAPI 名称 |
| `bapi_parameters` | object | BAPI 入参 |
| `bapi_output_table_path` | string | 输出内表路径 |
| `confidence` | number | 计划置信度 |

## data

`data` 为对象数组，对象的 key 为 `field_mapping` 中定义的中文表头或原始 SAP 字段名，value 为字符串或数值。

示例：

```json
[
  {"客户编码": "1000000001", "客户名称": "...", "创建日期": "20250715"}
]
```
