# OneAgent 与 rsmagent 能力差异清单

核对日期：2026-09-17。

对比对象为本机 `/Users/jiantan/ai_assistant/oneagent` 与 `/Users/jiantan/ai_assistant/rsmagent` 的当前工作区，包含未提交改动。本报告比较源码及默认交付内容，不代表外部系统已经接通，也不代表部署环境另外安装的技能或 MCP 服务不存在。

核对方法：比较模块与技能目录，检查后端路由、处理器、前端入口和关键执行代码；对本项目搜索替代实现。未启动服务、调用业务接口或运行生产任务。

## 结论

主要差距集中在企业业务集成、专用工作台、预置业务技能，以及用量统计、企业登录和任务管理的部分配套功能。Agent 循环、多模型、工具、MCP、记忆、知识库、数字员工、多智能体、权限和多通道本项目已有，不能列为整体缺失。

当前工作区有一个影响结论的重要变化：本项目原来的内置业务场景、专用工作台和配套技能已经移除。`scenes/scenes_config.json` 的分类及场景均为空，技能映射也为空，工作台分发只返回 `base`。因此，旧文档中的“已迁入 OneAgent 场景”不能作为当前能力证据。

- [本项目场景目录](/Users/jiantan/ai_assistant/rsmagent/scenes/scenes_config.json:1)
- [本项目场景模块说明](/Users/jiantan/ai_assistant/rsmagent/scenes/__init__.py:1)
- [本项目工作台分发](/Users/jiantan/ai_assistant/rsmagent/scenes/renderer.py:1)

## 一、平台功能与业务集成缺口

“缺少”表示本项目未发现对应的专用实现；“部分具备”表示已有基础能力，但缺少表中列出的部分。通用 Bash、浏览器或 MCP 的可扩展性，不计为某项专用集成已经交付。

| 编号 | 能力 | OneAgent 已有实现 | 本项目当前差距 | 主要代码依据 |
|---|---|---|---|---|
| A01 | SAP 专用连接与数据访问 | ERP 连接维护、连接测试，SAP ADT SQL / RFC 双 Provider，RFC 表读取及 BAPI 调用基础 | 缺少 SAP Provider、ERP 专用连接管理和对应 API；通用凭据管理不能替代这些适配器 | [连接管理](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:7373)、[Provider 工厂](/Users/jiantan/ai_assistant/oneagent/channel/web/sap/factory.py:1)、[RFC 实现](/Users/jiantan/ai_assistant/oneagent/channel/web/sap/rfc_provider.py:96) |
| A02 | SAP 自然语言数据分析 | 问题转查询计划、执行取数、失败后重新规划、按领域生成看板及 AI 解读、CSV 导出 | 缺少后端查询规划和执行服务；旧 SAP 专用工作台已移除 | [分析处理器](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/sap_data_analysis.py:44)、[查询规划](/Users/jiantan/ai_assistant/oneagent/channel/web/sap/query_planner.py:1) |
| A03 | 采购场景直接同步 SAP 数据 | 场景选择连接并取数、供应商数据同步、采购历史数据同步，采购比价保留原始 SAP 字段供脚本分析 | 缺少采购导入、ERP 同步和采购报表生成接口及配套工作台 | [采购同步处理器](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:7507)、[采购比价技能](/Users/jiantan/ai_assistant/oneagent/skills/procurement-comparison/SKILL.md:1) |
| A04 | 通用制造业生产排产 | 多工序、设备/班组约束、物料齐套、BOM 排程、甘特图、负荷及瓶颈分析、插单 What-if、历史方案 | 缺少生产排程算法和专用页面；现有 scheduler 是定时任务能力，不等同于制造排产 | [排程引擎](/Users/jiantan/ai_assistant/oneagent/agent/tools/scheduler/scheduler_engine.py:185)、[排产接口](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel_scheduling.py:298) |
| A05 | 气袋业务专用排产 | Excel 模板解析/预览、预估出货、产能、班组、规则维护、计算排产、结果及历史报告 | 缺少气袋业务处理器、工作台、模板及排程技能 | [气袋排产处理器](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/airbag_scheduling.py:862)、[业务算法](/Users/jiantan/ai_assistant/oneagent/skills/pmc-scheduler-hmt-qd/scripts/schedule_production.py:1) |
| A06 | 泛微 OA / E10 业务集成 | OA 连接配置与登录测试；配套 CLI/技能支持待办、已办、我发起、详情、附件、审批记录以及提交/退回等操作 | 缺少 OA 连接管理、业务协议适配及专用技能；本项目内部操作审批不等同于 OA 流程审批 | [OA 配置](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/oa_connection.py:29)、[OA 技能](/Users/jiantan/ai_assistant/oneagent/skills/oa-audit-manager/SKILL.md:1)、[E10 API 技能](</Users/jiantan/ai_assistant/oneagent/skills/Weaver E10 Api/SKILL.md:1>) |
| A07 | 个人邮箱连接与邮件操作 | 用户独立的 IMAP/SMTP 配置、加密保存、收发连通性测试，配套邮件搜索/读取/标记/附件发送脚本 | 缺少邮箱配置页面/API 和邮件操作技能；普通用户资料中的邮箱字段不算邮件集成 | [邮箱配置](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/email_config.py:54)、[邮件技能](/Users/jiantan/ai_assistant/oneagent/skills/imap-smtp-email/SKILL.md:1) |
| A08 | 钉钉单点登录及扫码重置密码 | 钉钉 OAuth 授权、回调、登录、扫码重置密码 | 缺少 Web 登录层的钉钉 SSO 流程；已有钉钉消息通道和外部身份绑定不能直接替代 SSO | [SSO 登录](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:1910)、[扫码重置](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:1963)、[授权实现](/Users/jiantan/ai_assistant/oneagent/auth/dingtalk_sso.py:159) |
| A09 | 模型 Token 消耗统计及调用明细 | 调用采集、SQLite 存储，按日期/用户/模型/供应商筛选，输入/输出/总 Token 汇总，调用耗时/状态及摘要记录 | 有模型返回的 usage、上下文估算和 quota 结构，但缺少对应统计服务、调用明细库、查询 API 和页面 | [统计 API](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:5205)、[调用采集](/Users/jiantan/ai_assistant/oneagent/agent/token_usage/recorder.py:71)、[本项目配额查询](/Users/jiantan/ai_assistant/rsmagent/auth/service.py:10316) |
| A10 | 知识库上传时转换 Word/PDF 等文件 | 知识库上传 DOCX/PDF 后提取文字并转 Markdown，CSV 转表格、代码文件转代码块 | 部分具备：知识库已有；直接导入仅允许 `.md` / `.txt`，缺少上述入库转换链路。这不等于聊天不能处理附件 | [OneAgent 转换](/Users/jiantan/ai_assistant/oneagent/agent/knowledge/service.py:51)、[本项目格式限制](/Users/jiantan/ai_assistant/rsmagent/agent/knowledge/service.py:36) |
| A11 | 定时任务的表单创建与模板库 | Web 表单创建任务，内置每日新闻、周报等任务模板和模板接口 | 部分具备：已有对话创建、列表、编辑、启停、删除和立即运行；缺少同等的 Web 新建表单及模板库 | [创建接口](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/scheduler.py:292)、[模板接口](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/scheduler.py:460)、[本项目路由](/Users/jiantan/ai_assistant/rsmagent/channel/web/route_registry.py:218) |
| A12 | 定时任务逐次运行历史 | 每次运行记录执行时间、结果、耗时等，支持单任务/全部任务历史查询 | 部分具备：本项目有运行状态及错误记录，但未发现同等的逐次任务历史存储、查询 API 和历史页面 | [历史查询](/Users/jiantan/ai_assistant/oneagent/channel/web/handlers/scheduler.py:474)、[历史写入](/Users/jiantan/ai_assistant/oneagent/agent/tools/scheduler/integration.py:227)、[本项目执行状态](/Users/jiantan/ai_assistant/rsmagent/agent/tools/scheduler/scheduler_service.py:286) |
| A13 | 预置业务场景目录和专用工作台 | 配置有 10 类、26 个主场景、81 个子场景；含采购、凭证、财税、财务审查、SAP、质量及排产等专用交互 | 部分具备：场景框架和通用工作台保留，但默认目录为空，专用工作台已移除。上述场景数量只是配置条目数，不代表每项都是完整业务产品 | [OneAgent 场景配置](/Users/jiantan/ai_assistant/oneagent/scenes_config.json:1)、[本项目空目录](/Users/jiantan/ai_assistant/rsmagent/scenes/scenes_config.json:1)、[仅保留通用分发](/Users/jiantan/ai_assistant/rsmagent/channel/web/static/js/scenes/registry.js:1) |
| A14 | 源码变更热加载及 Web 重启服务入口 | 可配置文件监听、Web 模块热替换、配置热应用、监督进程重启，以及 `/api/restart` | 部分具备：本项目已有 CLI 重启和通道重启；未发现对应文件监听热加载框架和 Web 服务重启接口。OneAgent 热加载默认关闭 | [热加载框架](/Users/jiantan/ai_assistant/oneagent/common/hot_reload.py:1)、[Web 重启](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:6539)、[本项目 CLI](/Users/jiantan/ai_assistant/rsmagent/cli/commands/process.py:189) |
| A15 | Ollama 专用管理 | 独立 Ollama Provider、命名实例管理、读取 `/api/tags` 获取本地已安装模型 | 部分具备：可以通过自定义 OpenAI 兼容 Provider 接入 Ollama；缺少专用实例界面及原生模型列表读取。不能写成“不支持本地模型” | [本地模型列表](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:4088)、[Ollama 适配器](/Users/jiantan/ai_assistant/oneagent/models/ollama/ollama_bot.py:34)、[本项目通用适配](/Users/jiantan/ai_assistant/rsmagent/models/custom_provider.py:82) |
| A16 | 独立的用户/角色模型匹配策略库 | 按用户/角色、capability 和 priority 匹配命名策略，并记录命中策略 | 机制差异：本项目有角色模型默认值、模型授权、Agent/会话选择及 fallback；缺少同等独立命名/优先级策略库。现有多角色默认模型冲突会提示选择，不自动按优先级裁决 | [OneAgent 策略解析](/Users/jiantan/ai_assistant/oneagent/models/user_model_resolver.py:235)、[本项目角色默认模型解析](/Users/jiantan/ai_assistant/rsmagent/auth/service.py:2369) |

### 判断边界

- A01–A03 的真实适配证据主要是 SAP。OneAgent 的采购处理器对部分非 SAP 路径返回演示数据，不能据此宣称金蝶、用友、Oracle 等都已完成真实 ERP 集成。[演示分支](/Users/jiantan/ai_assistant/oneagent/channel/web/web_channel.py:7580)
- OA、SAP、邮箱、海康、会议等能力需要外部服务、依赖及凭据；本次确认实现存在，未做连通性验收。
- A09 不等于已经提供完整成本结算或计费系统；本次确认的是 Token 用量和调用日志。
- A16 属于产品机制差异，是否需要补齐应取决于是否需要统一策略库，不宜把当前不同的冲突处理方式直接视作缺陷。

## 二、本项目未预置的技能包完整清单

以项目根目录 `skills/*/SKILL.md` 统计：OneAgent 有 **41 个**，本项目有 **3 个**，同名共有 `image-generation`、`knowledge-wiki`、`skill-creator`，因此有 **38 个包的差异**。当前检视到的本项目 `scenes/skills` 和仓库内租户目录中也没有补齐这些 SKILL.md。

这里的“未预置”仅表示没有随当前源码提供专用提示词、脚本、模板或参考资料，不表示通用模型绝对无法完成该类任务。部分条目与上表重叠，不能相加为独立能力总数。

| 编号 | OneAgent 技能包 | 专用能力/资产 | 判断 |
|---|---|---|---|
| B01 | [Weaver E10 Api](</Users/jiantan/ai_assistant/oneagent/skills/Weaver E10 Api/SKILL.md:1>) | E10 OAuth token 管理、创建流程、查待办、审批与退回脚本 | 未预置，需外部系统 |
| B02 | [agent-browser](/Users/jiantan/ai_assistant/oneagent/skills/agent-browser/SKILL.md:1) | agent-browser CLI 的操作规范 | 包缺少，但本项目已有浏览器自动化工具，不算浏览器能力缺失 |
| B03 | [bid-analysis](/Users/jiantan/ai_assistant/oneagent/skills/bid-analysis/SKILL.md:1) | 招标文件解析、Word 报告及交互式投标工作台模板 | 未预置 |
| B04 | [check-prd](/Users/jiantan/ai_assistant/oneagent/skills/check-prd/SKILL.md:1) | B 端 PRD 的 14 维审查规范、验证及构建脚本 | 未预置专用规范 |
| B05 | [create-prd](/Users/jiantan/ai_assistant/oneagent/skills/create-prd/SKILL.md:1) | 结构化 B 端 PRD 生成、章节与自检模板 | 未预置专用规范 |
| B06 | [data-analyst](/Users/jiantan/ai_assistant/oneagent/skills/data-analyst/SKILL.md:1) | SQL 查询、数据分析及报表操作规范和脚本 | 未预置专用包 |
| B07 | [dev-expert-1.0.48](/Users/jiantan/ai_assistant/oneagent/skills/dev-expert-1.0.48/SKILL.md:1) | 开发任务方法、审查规范、扫描与校验 hooks | 未预置；不等于本项目不能编程 |
| B08 | [dingtalk-meetings-skill-1.0.0](/Users/jiantan/ai_assistant/oneagent/skills/dingtalk-meetings-skill-1.0.0/SKILL.md:1) | 钉钉会议/日历、参会人、忙闲、会议室操作说明 | 未预置；OneAgent 也依赖另外配置日历 MCP |
| B09 | [doc-desensitization](/Users/jiantan/ai_assistant/oneagent/skills/doc-desensitization/SKILL.md:1) | 文档敏感数据识别、替换脱敏、映射表还原 | 未预置专用处理管线 |
| B10 | [docx](/Users/jiantan/ai_assistant/oneagent/skills/docx/SKILL.md:1) | Word 文档生成/编辑、批注修订及 Office 文件校验脚本 | 未预置专用包 |
| B11 | [drawio-flowchart-skill-main](/Users/jiantan/ai_assistant/oneagent/skills/drawio-flowchart-skill-main/SKILL.md:1) | draw.io 流程图 XML 生成规范及模板 | 未预置专用包 |
| B12 | [drawio-skill](/Users/jiantan/ai_assistant/oneagent/skills/drawio-skill/SKILL.md:1) | 架构图/ER/UML/拓扑绘制，布局、转换及导出工具 | 未预置；部分功能依赖 draw.io/Graphviz |
| B13 | [fin-report-analysis](/Users/jiantan/ai_assistant/oneagent/skills/fin-report-analysis/SKILL.md:1) | 财报结构变化、财务比率分析和报告脚本 | 未预置 |
| B14 | [finance-ledger-generator](/Users/jiantan/ai_assistant/oneagent/skills/finance-ledger-generator/SKILL.md:1) | 会计凭证、科目/辅助核算、规则学习、导入模板生成 | 未预置 |
| B15 | [financial-reprot-audit](/Users/jiantan/ai_assistant/oneagent/skills/financial-reprot-audit/SKILL.md:1) | 多格式财报解析、税款比对、收入/成本/关联方等审查策略 | 未预置 |
| B16 | [frontend-design](/Users/jiantan/ai_assistant/oneagent/skills/frontend-design/SKILL.md:1) | 前端设计规范 | 未预置专用规范；不等于不能生成前端 |
| B17 | [hikvision-record](/Users/jiantan/ai_assistant/oneagent/skills/hikvision-record/SKILL.md:1) | 海康监控点、预览/回放取流、录像下载、气袋条码录像追溯 | 未预置，需外部系统 |
| B18 | [imap-smtp-email](/Users/jiantan/ai_assistant/oneagent/skills/imap-smtp-email/SKILL.md:1) | IMAP 邮件查询/读取/标记、SMTP 邮件及附件发送 | 未预置，需邮箱服务 |
| B19 | [mcp-integration](/Users/jiantan/ai_assistant/oneagent/skills/mcp-integration/SKILL.md:1) | MCP 接入指导、协议说明及配置示例 | 包缺少，但本项目已有 MCP 客户端及管理能力 |
| B20 | [md-docx](/Users/jiantan/ai_assistant/oneagent/skills/md-docx/SKILL.md:1) | Markdown 与 Word 互转脚本 | 未预置 |
| B21 | [meeting-and-brief-1.5.0](/Users/jiantan/ai_assistant/oneagent/skills/meeting-and-brief-1.5.0/SKILL.md:1) | 会议纪要、月度简报、归档检索、客户代号及 DOCX 模板 | 包存在但 frontmatter 声明 `disable: true`，不据此认定默认启用 |
| B22 | [oa-audit-manager](/Users/jiantan/ai_assistant/oneagent/skills/oa-audit-manager/SKILL.md:1) | OA 待办/已办/抄送/详情/附件/关联流程/审单操作 CLI | 未预置，需外部系统 |
| B23 | [pdf](/Users/jiantan/ai_assistant/oneagent/skills/pdf/SKILL.md:1) | PDF 处理规范，表单字段检查、填写及渲染辅助脚本 | 未预置专用包 |
| B24 | [pmc-scheduler](/Users/jiantan/ai_assistant/oneagent/skills/pmc-scheduler/SKILL.md:1) | 通用生产排产、约束计算、甘特图和数据模板 | 未预置 |
| B25 | [pmc-scheduler-hmt-qd](/Users/jiantan/ai_assistant/oneagent/skills/pmc-scheduler-hmt-qd/SKILL.md:1) | 气袋 Excel 排产、班组/夜班/备货等业务规则和模板 | 未预置 |
| B26 | [ppt-hmt](/Users/jiantan/ai_assistant/oneagent/skills/ppt-hmt/SKILL.md:1) | 文档转 SVG 页面/PPTX、模板、图表、动画及演示文稿工具链 | 未预置专用工具链 |
| B27 | [procurement-comparison](/Users/jiantan/ai_assistant/oneagent/skills/procurement-comparison/SKILL.md:1) | SAP EKBE 历史采购价、报价对比、Excel 报表及 HTML 看板 | 未预置 |
| B28 | [procurement-supplier-risk](/Users/jiantan/ai_assistant/oneagent/skills/procurement-supplier-risk/SKILL.md:1) | 供应商五维风险评分、外部风险查询、Excel/HTML 报告 | 未预置 |
| B29 | [quality-trace](/Users/jiantan/ai_assistant/oneagent/skills/quality-trace/SKILL.md:1) | 气袋总查询、面料 LOT 反查、原料追溯 | 未预置，需外部业务接口 |
| B30 | [sales-quotation](/Users/jiantan/ai_assistant/oneagent/skills/sales-quotation/SKILL.md:1) | 询价解析、BOM 成本、报价方案、毛利及交期评估脚本 | 未预置 |
| B31 | [sap-abap-skills](/Users/jiantan/ai_assistant/oneagent/skills/sap-abap-skills/SKILL.md:1) | ABAP/RAP/CDS 等开发参考资料和规范 | 未预置专用知识包；不等于绝对不能写 ABAP |
| B32 | [sap-integration](/Users/jiantan/ai_assistant/oneagent/skills/sap-integration/SKILL.md:1) | 已抽取 SAP 文件的字段解释、聚合分析及查询建议 | 未预置；此技能本身不负责 SAP 连接取数 |
| B33 | [skill-writer](/Users/jiantan/ai_assistant/oneagent/skills/skill-writer/SKILL.md:1) | 技能编写指导 | 包缺少，但本项目已有 skill-creator，同类能力已有 |
| B34 | [taxation-expert](/Users/jiantan/ai_assistant/oneagent/skills/taxation-expert/SKILL.md:1) | 财税咨询方法、参考资料、输出和引用模板 | 未预置专用知识包 |
| B35 | [tencent-meeting](/Users/jiantan/ai_assistant/oneagent/skills/tencent-meeting/SKILL.md:1) | 腾讯会议预约/管理、录制、转写、智能纪要及权限申请适配 | 未预置，需外部服务/token |
| B36 | [tianji-business-search](/Users/jiantan/ai_assistant/oneagent/skills/tianji-business-search/SKILL.md:1) | 企业工商、股东、司法及舆情等商查脚本 | 未预置，需外部查询服务 |
| B37 | [xlsx](/Users/jiantan/ai_assistant/oneagent/skills/xlsx/SKILL.md:1) | Excel 公式、格式、分析操作规范及重算脚本 | 未预置专用包 |
| B38 | [合同审查专家](/Users/jiantan/ai_assistant/oneagent/skills/合同审查专家/SKILL.md:1) | 合同风险、条款审查及标注式 Word 报告输出规范 | 未预置专用知识包 |

## 三、已排除的误判

| 看似差异 | 为什么不列为整体缺失 | 本项目依据 |
|---|---|---|
| `agent/employee` 目录不存在 | 数字员工已由 Agent 注册、管理、个人 Agent、资源绑定及团队能力承接 | [Agent 管理](/Users/jiantan/ai_assistant/rsmagent/agent/admin.py:107)、[团队能力](/Users/jiantan/ai_assistant/rsmagent/agent/team.py:1) |
| `agent/audit` 目录不存在 | 审计已有独立实现，不能按同名目录判断 | [审计](/Users/jiantan/ai_assistant/rsmagent/auth/audit.py:1) |
| `auth/rbac.py`、`auth/tenant_context.py` 不存在 | 认证、租户、角色、资源权限及身份上下文已在新架构实现 | [身份服务](/Users/jiantan/ai_assistant/rsmagent/auth/service.py:1)、[授权策略](/Users/jiantan/ai_assistant/rsmagent/auth/policy.py:1) |
| `models/custom_model_store.py` 不存在 | 已有多自定义 Provider 及模型配置 | [自定义 Provider](/Users/jiantan/ai_assistant/rsmagent/models/custom_provider.py:1) |
| OneAgent 的 `plugins/agent` 不存在 | 本项目已有原生团队、子 Agent 及委派实现；不必依赖同一个 AgentMesh 插件 | [子 Agent](/Users/jiantan/ai_assistant/rsmagent/agent/subagent/runner.py:1)、[委派工具](/Users/jiantan/ai_assistant/rsmagent/agent/tools/agent_delegate/agent_delegate.py:1) |
| OneAgent 的专门 MCP 配置文件机制不同 | 本项目工具管理、MCP 客户端及 OAuth 都有实现 | [工具管理](/Users/jiantan/ai_assistant/rsmagent/agent/tools/tool_manager.py:1)、[MCP OAuth](/Users/jiantan/ai_assistant/rsmagent/agent/tools/mcp/mcp_oauth.py:1) |
| 两边的知识库上传 API 名不同 | 本项目已有目录、图谱、文档管理和批量导入；实际缺口只是 A10 的格式转换 | [知识库服务](/Users/jiantan/ai_assistant/rsmagent/agent/knowledge/service.py:28) |
| OneAgent 场景菜单很多 | 部分场景仅有配置/提示词或演示数据；不能逐菜单都认定为已经完成业务系统集成 | [场景配置](/Users/jiantan/ai_assistant/oneagent/scenes_config.json:1) |

这是一份源码能力清单，不是迁移实施方案。除本报告外，本次未修改业务代码或运行时配置。
