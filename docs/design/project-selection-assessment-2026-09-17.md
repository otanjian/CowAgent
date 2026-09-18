# OneAgent 与 rsmagent：单一项目保留评估

日期：2026-09-17。结论基于两个本地项目当前工作区及一次定向测试，包含未提交改动。

## 决策建议

**保留 rsmagent 作为唯一主项目。停止 OneAgent 的独立产品演进，将需要保留的业务技能、算法和连接适配器迁入 rsmagent。**

这个建议采用的目标是：持续建设可复用、可向不同企业交付的 AI Agent 平台，支持多租户、多成员、资源授权和业务扩展。该方向与本项目现有产品规划及身份架构一致。即使暂时不能迁移任何 OneAgent 模块，长期主项目选择仍倾向 rsmagent，但应明确接受业务场景暂时减少。

只有当目标明确收缩为“继续服务既有制造业环境，立即使用已经验收的 SAP、OA、气袋排产和质量追溯，且没有时间迁移”，选择才应改为 OneAgent。前提是这些实际业务链路已在目标环境验收；仅有源码与页面不能证明这一点。

## 决策依据

| 维度 | OneAgent | rsmagent | 判断 |
|---|---|---|---|
| 现成业务内容 | SAP、OA、排产、质量、财务等代码与模板更丰富；根目录 41 个技能包 | 当前默认业务场景为空，根目录 3 个技能包，专用工作台已移除 | OneAgent 优势明确 |
| 身份与租户建模 | User 上直接保存一个 tenant_id，角色保存在用户记录中，身份数据主要为 JSON | User、Membership、租户角色分开；数据库唯一约束、同租户复合外键、迁移机制 | 多企业复用优先 rsmagent |
| 授权与私有资源治理 | 已有 RBAC、租户目录和路径检查，不能说没有权限体系 | 统一 HTTP 策略、对象 owner/scope、模型/工具/技能资源授权、私有 Agent 和操作审批 | rsmagent 的治理结构更完整 |
| 账号安全基础 | 加盐单轮 SHA-256；HMAC token，已有撤销机制 | 版本化 PBKDF2，随机会话 token、仅存摘要、过期及撤销、逐请求身份重验 | rsmagent 优势明确；未进行全面安全审计 |
| Agent 协作 | 原有 Agent 内核、数字员工，另有 AgentMesh 插件路径 | 原生 Agent 注册、团队会话、子 Agent、委派及取消/模型 fallback 等模块 | 持续扩展优先 rsmagent |
| 回归验证基础 | 当前交付目录没有统一顶层 tests；部分技能有独立测试/验证脚本 | tests 下有 333 个 Python 文件和 63 个 JS/CJS 文件，含辅助文件；本次选取 11 个测试文件执行 | rsmagent 更容易持续验证变更 |
| 桌面与产品扩展 | 当前目录未见独立桌面工程 | 已有 Electron 桌面工程、Web/桌面身份桥接 | rsmagent 有演进资产，但未验收功能不计为可交付优势 |
| 运维及部署 | 有定制 Docker/Compose、热加载、网页重启；当前 Dockerfile 引用了缺失的 docker/entrypoint.sh | 有 CLI、桌面构建及发布脚本；根 Dockerfile 只继承上游镜像，未复制本 fork 源码 | 两边均需补齐可复现的发布验证，不能直接判任一方部署成熟 |
| 代码维护压力 | web_channel.py 约 8,100 行，console.js 约 21,400 行，业务逻辑大量集中于控制台 | web_channel.py 约 13,600 行，console.js 约 20,200 行，auth/service.py 约 10,600 行 | 两边都有大文件与耦合问题；rsmagent 不能被描述为架构已经干净 |

### 源码证据

- 身份模型：[OneAgent User](/Users/jiantan/ai_assistant/oneagent/auth/models.py:22)、[OneAgent JSON 存储](/Users/jiantan/ai_assistant/oneagent/auth/store.py:1)、[rsmagent 数据库模型](/Users/jiantan/ai_assistant/rsmagent/auth/store.py:79)。
- 账号机制：[OneAgent 密码哈希](/Users/jiantan/ai_assistant/oneagent/auth/password.py:13)、[rsmagent 密码哈希](/Users/jiantan/ai_assistant/rsmagent/auth/password.py:1)、[rsmagent 会话存储](/Users/jiantan/ai_assistant/rsmagent/auth/session.py:1)。
- 授权入口：[路由与权限登记](/Users/jiantan/ai_assistant/rsmagent/channel/web/route_registry.py:1)、[身份重验](/Users/jiantan/ai_assistant/rsmagent/auth/runtime.py:1)、[对象范围](/Users/jiantan/ai_assistant/rsmagent/auth/object_scope.py:1)。
- Agent 演进：[团队能力](/Users/jiantan/ai_assistant/rsmagent/agent/team.py:1)、[子 Agent 执行](/Users/jiantan/ai_assistant/rsmagent/agent/subagent/runner.py:1)。
- 部署现状：[OneAgent Dockerfile](/Users/jiantan/ai_assistant/oneagent/Dockerfile:1)、[rsmagent Dockerfile](/Users/jiantan/ai_assistant/rsmagent/Dockerfile:1)。本次没有执行镜像构建。
- 业务差距：[完整能力差异清单](/Users/jiantan/ai_assistant/rsmagent/docs/design/oneagent-capability-gap-2026-09-17.md:1)。

## 为什么平台基础比技能数量更影响本次选择

这是对代码耦合范围的工程判断，不是已经验证的工期报价：

1. **向 rsmagent 补业务模块，改动通常可以围绕对应连接器、技能、算法和工作台展开。** 文档生成/财务分析等独立脚本和模板适合先迁移；SAP/OA/排产涉及网络、凭据和文件产物，需要较多适配，但可以逐条业务链验收。
2. **向 OneAgent 补齐 rsmagent 的身份和资源模型，影响范围更广。** 登录、成员资格、角色、Agent、会话、文件、记忆、工具、后台任务、渠道以及桌面身份都要统一，并涉及存量身份和资源归属迁移。
3. **已有业务资产并不要求继续维护第二套平台。** 有价值的算法、脚本、模板和协议适配器可以归入唯一主项目；需要重新接入主项目的身份、授权、凭据与目录规则。

因此，预计“保留 rsmagent、按需求迁入业务能力”的长期成本及架构返工风险，低于“保留 OneAgent、整体重建平台治理”。具体人日无法由本次静态对比可靠给出。

OneAgent 的 38 个额外技能包也不能直接当作 38 个独立成熟产品：其中有同类能力、指导文档、外部 MCP 依赖和声明禁用的包，部分场景存在演示数据路径。

## rsmagent 必须面对的当前问题

1. **业务交付内容减少。** 当前内置场景与专用工作台已移除；使用 rsmagent 做主项目，需要选定真实业务样板补回来，不能只继续扩建框架。
2. **桌面企业能力尚未完成真实客户端验收。** `desktop_tenant_context` 仍为 `accepted=False`、`open={}`。[能力登记](/Users/jiantan/ai_assistant/rsmagent/auth/capability_matrix.py:335)
3. **个人渠道真实执行尚未登记验收类型。** `PERSONAL_RUNTIME_ACCEPTED_TYPES` 和 `PUBLIC_PERSONAL_INGRESS_TYPES` 当前为空；已有消息通道适配器不能自动证明该个人执行模式已可用。[类型登记](/Users/jiantan/ai_assistant/rsmagent/channel/channel_instances.py:1340)
4. **回归尚未全绿，且存在大规模在途改动。** 本次定向测试结果如下；未据此声明全仓通过或可以直接发布。
5. **执行隔离的边界要准确表述。** 本项目的隔离模块明确属于参数/路径级控制，不是操作系统沙箱。涉及不可信租户任意代码执行时，仍需另行评估进程或容器隔离。[实现边界](/Users/jiantan/ai_assistant/rsmagent/agent/permission/isolation.py:1)
6. **发布方式需要收口。** 当前根 Dockerfile 没有复制本项目代码，桌面更新地址和 CI 继承配置也需要按实际交付核验。发布脚本存在不代表当前 fork 已能自动可靠发布。

## 本次实际验证

运行环境：rsmagent 的 `.venv/bin/python`，Python 3.14.3。测试使用仓库已有临时目录/测试身份库机制。

```bash
.venv/bin/python -m pytest -q \
  tests/test_identity_session.py \
  tests/test_identity_runtime.py \
  tests/test_route_registry.py \
  tests/test_execution_isolation.py \
  tests/test_private_agent_integration.py \
  tests/test_agent_delegation.py \
  tests/test_chat_model_fallback.py \
  tests/test_scheduler_tool_dispatch.py \
  tests/test_task_store_concurrency.py \
  tests/test_identity_concurrency_acceptance.py \
  tests/test_migration_recovery_acceptance.py
```

结果：**129 passed，1 failed，10.67 秒**。

失败项：`tests/test_route_registry.py::OrderingTests::test_registry_order_preserves_first_match_semantics`。

核对发现，当前 `/help(?:/(.*))?` 路由中的可选正则分组不被测试的 `_sample_path()` 处理，生成了 `/help(?:/a/b)?` 这个非预期样例。[样例生成器](/Users/jiantan/ai_assistant/rsmagent/tests/test_route_registry.py:56)

追加只读验证：实际 `/help`、`/help/`、`/help/a/b` 均能被 `_match_policy(..., "GET")` 匹配为 public。现有证据指向测试样例生成器与新路由形式不兼容，不支持将其描述成线上 `/help` 路由已经失效。本次只评估，未修复该测试。

测试数量不等于覆盖率。本次没有跑全量测试、浏览器端到端、打包桌面端、压测，也没有连接 SAP/OA/邮箱/会议等外部服务。OneAgent 当前目录没有同等统一测试套件，本次未做对称运行对比。

## 单项目收敛建议

1. 确定 rsmagent 为唯一继续开发、发布和修复的主项目；先收口当前在途改动与测试失败，形成可复现版本。
2. 按真实交付需求从 OneAgent 迁入一到两条完整业务链。通用办公可先选 Word/PDF/Excel、文档脱敏或财报分析；制造业可选 SAP 采购分析或排产。每条链包含输入、执行、权限、产物和回归。
3. 随后补知识库文件转换、Token 统计、任务运行历史等通用缺口。OA/邮箱/SSO 按业务优先级接入。
4. 保留 rsmagent 的统一身份、资源授权和目录机制。迁移业务模块时适配这些机制，避免重新引入第二份身份库和并行权限实现。
5. 完成所需资产和存量数据处置后，OneAgent 退出活跃项目维护；本次没有迁移、停服、归档或删除任何项目。

本次只新增评估文档，没有修改业务代码或运行配置。
