## Why

RongAI 作为长命 fork 持续与上游 `master` 并行迭代，但定制逻辑直接内嵌在上游核心文件里。**实测合并基线**（`origin/master@9ad944dd` × `origin/rdai@dc760766`，合并基准 `e5e2a52d`）：**20 个冲突文件、41 个内容冲突块**，其中 `console.js` 12 处、`agent/memory/conversation_store.py` 11 处、`channel/web/web_channel.py` 3 处为架构级或语义级：

- **`conversation_store.py`（11 处）**：不只是两侧各加一列。fork 为 `sessions`/`messages` 增加所有者列并保留单列主键，上游则把主键与唯一约束重建为含智能体维度的复合键、新增索引、并引入独立迁移框架与迁移元数据表。
- **`app.py`（1 处）**：fork 的启动守卫与上游的会话迁移守卫同址。上游的 `_migrate_conversations()` 自述「add agent_id column / composite key」并**把所有 Agent 的会话折叠进单一全局文件**——与上面 `conversation_store.py` 的冲突是**同一根因**。
- **`channel/web/web_channel.py`（3 处）**：**与路由表无关**。是 fork 改写了上游公共方法签名（`upload_file` 增加授权目标参数）、以及上游新增桌面端「按本地路径导入文件」能力。
- **`console.js`（12 处）**：几乎全为 i18n 字典同址插入（fork 的租户渠道键 vs 上游的任务/记录键）与视图分派、i18n parity，而**不是**界面未拆分——fork 的身份/待办/外观界面**已**是独立文件并由 `chat.html` 加载。
- **`channel/channel_instances.py`、`agent/tools/scheduler/integration.py`、`.gitignore`、`chat.html`、`tests/test_scheduler_web_update.py`**：同址新增符号或语义分叉。
- **5 个删除/修改类冲突**：`README.md`、`docs/{zh,ja}/README*.md`、`desktop/.../PermissionSelector.tsx` 由 fork 删除、上游仍在修改，每次合并都复发且无处置决策。
- **6 个文档 `.mdx` 冲突（8 个冲突块）**：`docs/{,zh,ja}/intro/{architecture,index}.mdx` 为模板/i18n 内容冲突，无代码影响。
- **4 个 README 删除类冲突**：`README.md`、`docs/{zh,ja}/README*.md` 由 fork 删除、上游仍在修改——fork 移除了**全部** 25 个 README（fork 侧 README 数为 0），属一致政策而非偶发，故处置为保留删除（见 `scripts/conflict-baseline.txt`）。

另一条独立主线是安全：`doc/多租户与权限架构分析.html` 的静态审计表明多租户与 RBAC **设计意图完整、执行落地不完整**——边界靠约定而非机制保证。授权判定分散在各 handler 手工调用，`auth/http_policy.py` 的策略表**不执行鉴权**，一批登记为 `tenant` 的接口只调用 `_require_auth()` 而未进入作用域，构成跨租户/跨用户越权（IDOR）；工具授权与执行隔离在异常时 fail-open。

需要明确区分两者的关系：**路由双清单（`_WEB_URLS` 与 `ROUTE_POLICY`）是安全缺陷的直接成因（实测 115 条路由中 5 条无策略条目，含 `/api/identity/administered-tenants`），但它并不是实测合并冲突的来源**。本 change 同时做两件事，但两者各自的收益必须分开计量，不得互相背书。

## What Changes

- **建立单一路由事实源**：由一份权威路由清单同时派生 web.py 的 URL 表与 `ROUTE_POLICY` 策略表，消除两表漂移；权威清单逐条登记（不自动转录默认值），并把已实测的 5 条未登记路由各自补齐策略：`/admin`、`/api/identity/administered-tenants`、`/api/scenes`、`/api/scenes/activate`、`/api/scenes/workbench/import`。**该改动的收益是安全与可维护性，不声称减少 `web_channel.py` 的合并冲突。**
- **覆盖不变量以 handler 实现为第三腿**：`权威清单 ↔ 派生策略表` 同源、比对恒真，不足以发现缺口。校验改为三腿交叉：清单方法 ↔ 策略条目、策略路由 ↔ 清单、**清单登记方法 ↔ handler 内省实现方法**，任一不一致即失败。
- **路由门禁升级为真门禁**：`tenant`/`platform` 路由在进入 handler 前由门禁解析并注入请求上下文，缺失即 401/400/403，不再依赖每个 handler 自觉调用。
- **关闭已确认的越权面（P0）**：会话/消息/Agent 核心文件写入统一进入作用域并复用属主/绑定/资源校验；`_get_workspace_root` 在 database 模式空租户一律 403（不回落全局工作区）；平台名册写入收归平台域；scenes 路由登记策略并补上下文与 CSRF；日志接口限定平台/租户管理员；管理写统一来源/CSRF 校验。
- **执行授权与隔离改 fail-closed**：身份不可解析、身份库异常、ContextVar 身份丢失时一律拒绝执行并告警，不再静默放行。
- **数据层补纵深防御（P1 切片）**：为会话/消息/记忆索引固化租户维度作为第二道过滤，与既有所有者维度**分开登记**；既有行按**行所有者**的租户成员关系回填（不按 Agent 当前归属）；`default_agent_id` 解析校验绑定存在且启用、禁止回退进程全局默认；RBAC 关联表补 `tenant_id` 复合外键；配额检查改 fail-closed。
- **会话存储改为约束级组合接缝**：接缝登记六类扩展点——列定义、**主键与唯一约束**、索引、INSERT 列/占位/参数、公共 WHERE 片段、迁移步骤；显式决定合并后会话/消息行的最终复合主键，避免 fork 既有单列主键静默覆盖上游的智能体维度唯一性。
- **上游会话全局化迁移与存储接缝合并为同一决策**：`app.py` 的 `_migrate_conversations()` 与 `conversation_store.py` 的复合键是同一架构变更，本 change 统一决策 fork 的「按 Agent 工作区/租户隔离」如何与上游「单一全局会话文件 + 智能体维度」共存，而非分列两处各自处理。
- **迁移 fork 定制逻辑到上游稳定接缝**：把 `web_channel.py` 的 fork 专有路由登记抽为扩展注册；fork 所需授权目标参数经**接缝注入而非改写上游方法签名**（覆盖 `upload_file`、`post_message`），并显式要求保留上游桌面端按路径导入能力及其 loopback/令牌校验；`app.py` 的 fork 启动守卫改为扩展钩子；前端把 **i18n 按域命名空间拆分**并在加载时合并、**用视图注册表替代 `console.js` 内联视图分派**（身份/待办/外观模块已抽出，无需再抽）。
- **为冲突实测确认的其余上游文件补接缝**：`channel/channel_instances.py`（fork 凭据最小必填集与租户运行时 vs 上游类型标签/默认名，改为分区登记的独立扩展块，并登记为「人工纪律、待机制化」的后续项）；`agent/tools/scheduler/integration.py`（fork 与上游各自实现任务身份解析，收敛为单一接缝，并**同时确定身份上下文唯一入口模块与服务拓扑/回调契约**）；`tests/test_scheduler_web_update.py` 纳入随上游行为变更的修订清单。
- **删除/修改类冲突建立处置决策**：对 fork 删除、上游仍在修改的 5 个文件（`README.md`、`docs/{zh,ja}/README*.md`、`desktop/.../PermissionSelector.tsx`）在冲突基线清单中登记处置决策并持续生效，消除逐次人工裁决。
- **建立可合并性基建**：启用 `rerere`、新增 `.gitattributes` 标记 fork 单方二进制资源、提供上游同步脚本与冲突规模报告。这些为工程手段，不作为 spec 能力声明。
- **建立冲突基线冻结与漂移检测**：实施前重新冻结上游/分叉基线（以实测 20 文件/41 块为规划快照），同步脚本报告「与上次基线相比的漂移」，避免规划基于过期快照。
- **BREAKING**：`ROUTE_POLICY` 不再是可独立编辑的手工表，改为由权威清单派生；依赖直接编辑该表的流程需改走登记接口。database 模式下未解析到租户的请求由「回落全局工作区」改为 403。会话/消息表的最终主键以本 change 的显式决定为准，可能改变「`session_id` 全局唯一」的既有假设。
- **明确延后项**：用户级资源授权（`membership_resource_grants`）、Agent 元数据单一事实源、审批/配额完整落地、scheduler 触发重验开放、`channel_instances.py` 的搬迁式机制化、6 个文档 `.mdx` 冲突的逐文件策略（以 i18n/模板约束为准、一般合并）列为后续 change。

## Capabilities

### New Capabilities

- `fork-upstream-decoupling`: 长命 fork 与上游的可合并性契约——单一权威路由清单与三腿覆盖不变量、fork 定制逻辑的扩展接缝与迁移边界（含公共方法签名不得被改写、上游新增功能不得丢失）、会话存储约束级组合接缝与行级租户回填、前端 i18n 命名空间与视图注册、任务身份收敛与其服务拓扑、删除/修改类冲突的显式决策，以及合并后回归验证。

### Modified Capabilities

- `enterprise-access-enforcement`: 路由策略表由「文档标签」升级为真门禁（tenant/platform 自动解析并注入上下文）；已确认越权的会话/消息/Agent 文件与日志接口纳入作用域与属主校验；管理写统一来源/CSRF 校验。
- `console-route-lifecycle`: 已实测的 5 条未登记路由（含 `/api/identity/administered-tenants`）纳入权威清单并明确作用域；状态变更动作补上下文与 CSRF；覆盖不变量升级为以 handler 实现为第三腿的可验收校验。
- `resource-execution-authorization`: 工具授权与执行隔离在身份不可解析/身份库异常/身份丢失时 MUST fail-closed，原有 fail-open 允许路径移除。
- `tenant-channel-configuration`: 平台级渠道名册的写操作限定平台域并留审计；租户不可经 `/api/agents` 的渠道绑定动作改写全局名册或路由。
- `tenant-resource-isolation`: 会话/消息/记忆索引固化租户维度作为第二道过滤，并与所有者维度的语义明确区分；既有行按行所有者回填；默认智能体解析校验绑定存在且启用，禁止回退进程全局默认。
- `resource-quota`: 配额检查在异常时默认拒绝（可配置），不再 fail-open；配额行补齐租户外键。
- `audit-log`: 被拒绝的鉴权尝试与跨租户尝试纳入审计与告警指标，覆盖查询授权缺口。

## Impact

- **代码（接缝与路由）**：`channel/web/web_channel.py`（`_WEB_URLS`、`build_web_app`、`upload_file`/`post_message` 签名接缝、保留上游 `_import_local_file`）、`auth/http_policy.py`（策略表改为派生 + 门禁升级 + 三腿校验）、新增路由清单与扩展注册模块、`channel/web/static/js/console.js` 的 i18n 命名空间与视图注册、`chat.html` 挂载点。
- **代码（安全收口）**：`channel/web/web_channel.py`（`SessionDetailHandler`、`SessionTitleHandler`、`SessionClearContextHandler`、`MessageDeleteHandler`、`AgentCoreFileHandler`、`AgentAvatarHandler`、`LogsHandler`、`LogsDownloadHandler`、`_get_workspace_root`、`_bind_channel_instance`）、`channel/web/admin_handlers.py`（管理写 CSRF/Origin、`IdentityAdministeredTenantsHandler` 作用域）、`scenes/api.py` 与 `scenes/api_workbench.py`、`agent/protocol/agent_stream.py`、`agent/permission/isolation.py`、`app.py`。
- **代码（数据层与存储接缝）**：`agent/memory/conversation_store.py`（约束级组合接缝 + 租户维度 + 迁移）、`agent/memory/storage.py`、`auth/store.py`（迁移：RBAC 关联表 `tenant_id`、配额外键）、`auth/service.py`（默认智能体解析校验）、`agent/evolution/trigger.py`（后台线程身份补全，列为后续切片）。
- **代码（补缺口的冲突文件接缝）**：`channel/channel_instances.py`（fork `REQUIRED_CREDENTIAL_KEYS`/租户运行时与上游 `_CHANNEL_TYPE_LABELS`/`default_instance_name` 分区登记）、`agent/tools/scheduler/integration.py`（统一任务身份解析接缝 + 服务拓扑决策）、`common/runtime_identity.py`（`use_identity` 与 `identity_scope` 收敛为单一入口）、`tests/test_scheduler_web_update.py`（随上游行为修订）。
- **冲突基线清单**：新增持久化清单 `scripts/conflict-baseline.txt`（已落地，20 行数据）记录 20 个冲突文件的处置方式，含 5 个删除/修改类文件的决策与 6 个文档 `.mdx` 文件的一般合并策略；另有 `scripts/route-baseline.txt` 记录路由基线（140 条目 + 5 缺口）。
- **规范/测试**：新增三腿路由覆盖不变量测试（权威清单 ↔ 策略表 ↔ **handler 内省实现**）、门禁解析测试、跨租户 IDOR 用例（A 租户会话访问 B 租户 `session_id`/`agent_id` 期望 403/404）、fail-closed 注入测试、存储组合与复合主键测试、行所有者回填测试、DB 不变量测试、上游功能保留测试（桌面本地导入）；受影响既有测试套件（`tests/test_http_policy.py`、`tests/test_web_chat_boundary.py`、`tests/test_scheduler_web_update.py`、`tests/test_*identity*`、`tests/test_*tenant*`、前端 33 个 `.cjs` 契约测试）随之修订。
- **数据**：`identity.db` 迁移新增 RBAC 关联表 `tenant_id` 复合外键与配额外键；业务库会话/消息表按显式决定重建复合主键与唯一约束，新增租户维度列（`ALTER TABLE ... DEFAULT ''`，旧行按行所有者回填）。回滚只需停用第二道过滤，但**主键/唯一约束变更不可自动回滚**，需专门的回退迁移。
- **接口**：既有接口路径与请求/响应字段不变；变化集中在拒绝语义（缺租户/跨租户由「成功或回落」改为 401/403/404）。无新增对外业务接口。
- **依赖**：以 `openspec/specs/` 既有规范与 `doc/多租户与权限架构分析.html`（2026-09-10 审计）为需求基线。需求以 spec 为权威来源；本 change 不引用 PRD 编号——历史 change 与 `doc/` 材料中的 PRD 编号仅作存档记录。与在途 change `scan-onboarding-and-inbound-anchor` 在路由策略表与租户渠道写入面存在编辑重叠，本 change 声明其归档顺序前置。
