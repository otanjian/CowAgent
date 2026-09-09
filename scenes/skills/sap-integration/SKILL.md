---
name: sap-integration
description: SAP 数据分析专用技能。当用户在 SAP 数据分析场景下需要：1）理解已抽取的 SAP 数据文件；2）对数据做分组/聚合/趋势分析；3）解释 SAP 字段业务含义；4）生成可复用的分析脚本或查询建议时调用。本技能不执行任何 SAP 连接或数据抽取，数据文件由后端 SAP 数据分析服务统一提供。
---

# SAP 数据分析技能

## 能力

- 读取并解析 SAP 数据分析服务生成的 JSON 数据文件（含 `meta` 与 `data`）。
- 解释 SAP 字段含义（如 `KNA1.ERDAT` 表示客户主数据创建日期）。
- 对已有数据进行汇总、分组统计、趋势分析、异常识别。
- 根据用户追问生成下一步分析建议或 Python/Pandas 分析脚本。
- 在 V1 模板无法匹配时，为后端 `QueryPlanner` 提供查询建议（仅作为建议，最终由后端校验执行）。

## 禁止行为

- 不连接 SAP 系统，不执行 RFC/BAPI/OData 调用。
- 不接收或处理 SAP 连接参数（ashost、sysnr、client、username、password 等）。
- 不读取除本服务生成的 JSON/CSV 以外的 SAP 数据源。
- 不将连接参数、密码等敏感信息写入分析结果或脚本。

## 调用场景

- 用户问"这个字段是什么意思"。
- 用户要求"再按城市分组统计"。
- 用户要求"把结果导出成 Excel"。
- 用户要求"帮我写一段 Python 分析这段数据"。
- 用户提出新的自然语言问题，V1 模板未覆盖，需要生成 SAP 查询建议。

## 可用脚本

- `scripts/data_analyzer.py`：读取 JSON 数据文件，执行分组、聚合、统计、可视化建议。
- `scripts/query_planner.py`：根据自然语言问题 + 范围参数，输出 SAP 查询建议。

## 数据文件格式

本技能消费的 JSON 文件由后端 `channel/web/sap/data_exporter.py` 生成，结构如下：

```json
{
  "meta": {
    "question": "用户问题",
    "connection_id": "连接ID",
    "connection_name": "连接名称",
    "system": "sap",
    "tenant_id": "租户ID",
    "query_plan": { "意图、领域、表、字段、WHERE..." },
    "generated_at": "ISO8601 时间",
    "total_rows": 100,
    "files": { "json": "绝对路径" }
  },
  "data": [
    {"客户编码": "1000000001", "客户名称": "...", "创建日期": "20250715"}
  ]
}
```

详细说明见 `references/data-file-schema.md`。

## 常用表与字段速查

见 `references/sap-tables.md`。

## 查询计划示例

见 `references/query-plan-examples.md`。
