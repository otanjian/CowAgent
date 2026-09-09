## Description: <br>
SAP 数据分析专用技能。用于理解后端已抽取的 SAP 数据文件、解释 SAP 字段业务含义、对数据进行分组/聚合/趋势分析、生成可复用的分析脚本或查询建议。本技能不执行任何 SAP 连接或 RFC/BAPI 调用。 <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[Highlander89](https://clawhub.ai/user/Highlander89) <br>

### License/Terms of Use: <br>


## Use Case: <br>
用户在 SAP 数据分析场景下基于已落地的 JSON 数据文件进行二次分析：解释字段含义、分组统计、趋势分析、异常检测、生成 Python/Pandas 脚本，或在 V1 模板未覆盖时生成查询建议供后端校验执行。 <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: 分析的 JSON 数据文件可能包含敏感业务数据。 <br>
Mitigation: 数据文件保存于租户隔离目录，访问需经后端鉴权；分析结果应按企业数据分级进行管控。 <br>
Risk: 生成的查询建议需经后端白名单与权限校验后方可执行。 <br>
Mitigation: 本技能仅输出建议，不直接连接 SAP；最终执行由后端 PermissionGuard 与 FetchExecutor 完成。 <br>


## Reference(s): <br>
- [SAP 常用表与字段速查](artifact/references/sap-tables.md) <br>
- [查询计划示例](artifact/references/query-plan-examples.md) <br>
- [数据文件 Schema](artifact/references/data-file-schema.md) <br>


## Skill Output: <br>
**Output Type(s):** [text, markdown, code, guidance] <br>
**Output Format:** [Markdown 分析结论、JSON 统计结果、Python/Pandas 分析脚本] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [分析输出可能包含敏感业务数据，应按企业数据安全规范处理。] <br>

## Skill Version(s): <br>
2.0.0 (source: server release metadata) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>
