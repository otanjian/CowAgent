## Context

见 `proposal.md` 的 Why。本设计处理两条主线的交汇点，当前代码接入点如下（以函数/模块名为准，审计日期 2026-09-10）。

**实测合并基线**（`origin/master@9ad944dd` × `origin/rdai@dc760766`，merge-base `e5e2a52d`）：20 个冲突文件、41 个内容冲突块。

| 文件 | 冲突块 | 性质 |
|---|---|---|
| `channel/web/static/js/console.js` | 12 | i18n 字典同址插入 + 视图分派 |
| `agent/memory/conversation_store.py` | 11 | 列 + **主键/唯一约束/索引/迁移框架**语义分叉 |
| `channel/web/web_channel.py` | 3 | **公共方法签名改写** + 上游新增桌面功能 |
| `docs/{,zh,ja}/intro/*.mdx` | 8 | 文档模板/i18n 内容冲突（6 个文件） |
| `channel/web/chat.html` | 2 | fork 块与上游块同址 |
| `channel/channel_instances.py` | 1 | 双方同址新增模块级符号 |
| `agent/tools/scheduler/integration.py` | 1 | 任务身份解析语义分叉 |
| `app.py` | 1 | 双方同址新增启动守卫 |
| `.gitignore`、`tests/test_scheduler_web_update.py` | 各 1 | 忽略块 / 上游行为变更 |
| `README.md`、`docs/{zh,ja}/README*.md`、`desktop/.../PermissionSelector.tsx` | 0（DU） | **fork 删除、上游修改** |

代码接入点：

- **路由双清单**：`channel/web/web_channel.py` 的 `_WEB_URLS`（约 1466 行起）与 `auth/http_policy.py` 的 `ROUTE_POLICY` 是两份手工清单；`build_web_app()` 用前者建 `web.application`，用后者装 `enforce_http_policy`。二者无机器校验，且 `enforce_http_policy` 对 `tenant/platform` 只透传 `handler()`（文件头注释自认是「route-completeness gate」）。实测 `_WEB_URLS` **115 条** vs `ROUTE_POLICY` **110 条**，缺 5 条：`/admin`、`/api/identity/administered-tenants`、`/api/scenes`、`/api/scenes/activate`、`/api/scenes/workbench/import`。
- **作用域回落**：`_get_workspace_root()`（约 1049 行）在 `current_identity().tenant_id` 为空时走 `get_agent_registry().get(agent_id).workspace`——即全局默认 Agent 工作区。
- **未收口 handler**：`SessionDetailHandler.DELETE/PUT`、`SessionTitleHandler.POST`、`SessionClearContextHandler.POST`、`MessageDeleteHandler.POST`、`AgentCoreFileHandler.GET/PUT`、`AgentAvatarHandler.GET/POST`、`LogsHandler.GET`、`LogsDownloadHandler.GET` 均只调用 `_require_auth()`（已逐一核对：`SessionDetailHandler.DELETE/PUT`、`AgentAvatarHandler.GET/POST` 确为仅 `_require_auth()`）。对照正确写法是 `HistoryHandler.GET`（`_db_scope` + `_require_read_permission` + `_require_tenant_agent_binding` + `_require_session_owner`）。
- **所有者维度语义**：`conversation_store.py` 的 `owner` 列存的是**用户**而非租户——`web_channel.py::_require_owned_session` 以 `row[0] != ctx.user_id` 比对，`backfill_owner()` 把无主行登记给默认租户初始管理员。故 `owner`（逐用户）与拟新增的 `tenant_id`（逐租户）是**两个不同维度**，不可混用。
- **上游会话架构变更**：`app.py` 冲突显示上游新增 `_migrate_conversations()`，自述「Level 1（add agent_id column / composite key）」并**把所有 Agent 的会话折叠进单一全局文件**；`conversation_store.py` 上游侧把 `sessions`/`messages` 主键改为 `(agent_id, session_id)`、唯一约束改为 `(agent_id, session_id, seq)`、索引改为 `(agent_id, last_active)`，并新增 `_MIGRATION_ADD_SESSION_AGENT_ID`/`_MSG_AGENT_ID` 与 `_migration_meta` 表。**二者是同一根因。**
- **fork 签名分叉**：`web_channel.py` 中 fork 把 `upload_file(self)` 改为 `upload_file(self, *, agent_id=None)`、`post_message` 增加 `auth_context`/`authorized_session` 参数；上游在同文件新增 `_import_local_file`（桌面端按本地路径导入，含 loopback + 每启动令牌校验）。实测 3 处冲突**均不涉及 `_WEB_URLS`**。
- **前端现状**：`console.js` 约 20898 行（约 1MB）。fork 的身份/待办/外观界面**已**抽为独立文件（`identity-admin.js` 4176 行、`todos.js` 782 行、`appearance.js` 115 行）并由 `chat.html` 加载；`loadTenantView`/`loadMembersView`/`loadRolesView` 等均定义在 `identity-admin.js`。`console.js` 保留的是 i18n 字典、视图分派与 i18n parity，共 12 处冲突。
- **身份上下文**：`common/runtime_identity.py` 同时定义 `identity_scope(**overrides)`（第 85 行）与 `use_identity(identity)`（第 100 行）；`agent/tools/scheduler/identity.py` 装的是所有者快照与重验（`owner_snapshot`/`revalidate_owner`/`skip_record`）。
- **fail-open 点**：`agent/protocol/agent_stream.py::_resource_tool_denial`（异常仅 warning 后放行）、`agent/permission/isolation.py::isolation_decision`（身份缺失返回 `Decision(True)`）。

约束：保留上游兼容接口与既有业务 session/run ID；需求以 `openspec/specs/` 既有规范为权威来源，问题基线来自 `doc/多租户与权限架构分析.html` 审计（历史材料中的 PRD 编号仅作存档记录，不作为现行基线）；`scan-onboarding-and-inbound-anchor` 在途且同样编辑路由策略表与租户渠道写入面，存在编辑重叠。

## Goals / Non-Goals

**Goals:**

- 建立**单一权威路由清单**，由它派生 URL 表与授权策略表，并以**三腿交叉**不变量（含 handler 内省）使「合并上游丢路由」与「handler 实现未被登记」变成可被 CI 拦下的机械错误。
- 把 `tenant/platform` 路由的门禁升级为**真门禁**（进入 handler 前解析并注入上下文），使安全边界由框架保证而非 handler 自觉。
- 关闭审计确认的 P0 越权面，并把执行授权/隔离改为 **fail-closed**。
- 为 fork 定制逻辑建立**稳定接缝**（路由扩展注册、启动扩展钩子、存储约束级组合接缝、前端 i18n 命名空间与视图注册），把定制代码迁出上游核心文件，且**不改写上游公共方法签名**。
- 明确并记录**会话/消息行的最终主键与唯一约束**，使上游多智能体复合键与 fork 所有者/租户维度可组合而非互相覆盖。
- 为**删除/修改类冲突**建立一次性处置决策，消除逐次人工裁决。
- 提供可重复、可量化的上游同步流程。
- 落地 P1 中**低风险、高收益**的纵深防御切片：业务存储租户维度（按行所有者回填）、默认 Agent 解析校验、RBAC 关联表复合外键、配额 fail-closed。

**Non-Goals:**

- 不在本 change 落地用户级资源授权（`membership_resource_grants`）、Agent 元数据收归身份库（outbox/单一事实源）、审批/配额完整生命周期、租户删除闭环。这些列为后续 change。
- 不改变对外业务接口路径与请求/响应字段（拒绝语义变化除外）。
- 不重构与可合并性无关的上游模块；不追求一次性彻底解耦。
- 不修改上游专有文件的历史，不引入对上游的 rebase 工作流（保留 merge 策略）。
- 不为 6 个文档 `.mdx` 冲突（`docs/{,zh,ja}/intro/{architecture,index}.mdx`）逐文件定制策略（以文档模板与 i18n 约束为准，属上游文档体系，一般合并即可，无代码影响）；`channel_instances.py` 的搬迁式机制化列为带触发条件的后续项。

## Decisions

### D1：权威清单的物理形态、派生方式与其收益边界

**决策**：新增模块（暂定 `channel/web/route_registry.py`）承载权威清单；每条登记项包含 `pattern`、`methods → {policy, permission}`、`source`（`upstream` / `fork:<name>`）、可选 `handler`。`_WEB_URLS` 与 `ROUTE_POLICY` 改为由该清单在 import 时派生（保持现有数据结构形态与调用点签名不变，使 `build_web_app` 与测试无感）。fork 扩展通过 `register_fork_routes()` 注册，而非编辑核心字面量。登记 MUST 逐条进行并人工确认策略；禁止「批量转录 `_WEB_URLS` 后自动生成策略」的初始化脚本，否则策略退化为未经验证的默认值。

**收益边界（必须显式）**：该改动解决的是**安全缺口**（5 条路由无策略）与**双清单漂移**，**不减少** `web_channel.py` 的实测冲突——那里的 3 处冲突是方法签名与上游新功能，与路由表无关。proposal 与评审材料 MUST NOT 把 D1 计入可合并性收益。

**取舍**：替代方案是「生成静态文件」，会引入构建步骤与生成物漂移；替代方案二是「元数据注释 + 静态检查」，改动小但仍保留两份清单，无法根治。选派生方案，因为它在不改变调用契约的前提下消除第二份清单。

**风险控制**：派生结果先与现有 `_WEB_URLS`/`ROUTE_POLICY` 做一次性等价断言（迁移期），确保行为零变化后再切换；等价断言把 5 条缺口显式列出为「待补策略」，而非静默接受。

### D1b：覆盖不变量必须包含 handler 内省

**决策**：不变量校验为三腿交叉，任一不一致即失败：

1. 权威清单登记的每个方法 → 存在策略条目；
2. 策略表登记的每个路由 → 存在于权威清单或显式外部来源；
3. 权威清单登记的方法集合 → 与对应 handler 类内省出的 `get`/`post`/`put`/`delete` 集合一致。

**理由**：D1 使 URL 表与策略表同源，第 1、2 腿因此**构造上恒真**，无法发现「handler 实现了 POST 而清单只登记 GET」——这正是未登记路由与未授权可达的成因。第 3 腿是唯一非恒真的比对，是本不变量的实质。

### D2：门禁如何注入上下文而不重复鉴权

**决策**：`enforce_http_policy` 对 `tenant`/`platform` 路由调用统一的上下文解析入口（复用既有 `resolve_context`），把结果放入 ContextVar 供 handler 取用；解析失败按缺失类型返回 401/400/403。门禁只做「上下文存在性 + 身份域 + 必需 permission」的判定，**不**替代 handler 的资源属主校验（对象级授权仍在 handler，但对象级校验所需的身份已由门禁保证）。

**取舍**：替代方案是「装饰器逐个包 handler」——仍需每个 handler 记得加，且遗漏不可静态发现。选门禁集中解析，因为它是唯一能覆盖「未来新增 handler」的位置。

**兼容**：legacy 模式门禁为空操作，行为不变。

### D3：`_get_workspace_root` 空租户的严格化——以钩子实现，而非原地分支

**决策**：database 模式下 `ident.tenant_id` 为空时返回 403，删除到 `get_agent_registry().get(agent_id).workspace` 的回退分支。为避免把 fork 专有语义写死进上游核心函数（违反本 change 自身的接缝原则），该严格性 SHALL 经**工作区解析策略钩子**接入：上游函数保留「可注入的解析策略」接缝，fork 注册「空租户即拒绝」策略；未注册策略时按上游语义回退。legacy 模式行为不变。

**取舍**：替代方案是直接在上游函数内加 fork 分支——改动更小，但每次上游修改该函数都会冲突，且与本 change 的核心目标自相矛盾。若实施评估认为钩子成本过高，退路是**显式记录为已接受例外**（在 design 与 evidence 中标明「此为上游核心文件中的 fork 行为分叉，接受其合并成本」），而不是默认忽略。

**这是本 change 的 BREAKING 点之一。**

### D4：会话存储——约束级组合接缝与显式主键决策

**决策**：在 `conversation_store.py` 引入扩展点，覆盖**六类**：列定义列表、**主键与唯一约束**、索引定义、INSERT 列/占位/参数构造、公共 WHERE 片段、迁移步骤登记。上游的智能体维度与 fork 的所有者/租户维度各作为独立扩展项登记，最终 DDL/SQL 由扩展项组合生成。

**已决定（tasks 0.1，方案 (a)+(c) 合并）**：`sessions` 采用 `PRIMARY KEY (agent_id, session_id)`，`messages` 采用 `UNIQUE (agent_id, session_id, seq)`，索引采用 `(agent_id, session_id, seq)` 与 `(agent_id, last_active)`——即**完全采纳上游的复合键与索引**；fork 的 `owner` 与新增 `tenant_id` 均为**非键过滤列**，隔离由 WHERE 条件承担，不进入键。

理由：上游的复合键是其「单一全局会话文件 + 智能体维度」架构的基础（`ConversationStore.__init__(db_path, agent_id="")` 以 `self._agent_id` 作用域化全部查询），fork 若保留单列主键将持续与上游的键定义冲突——这正是本次 11 处冲突的结构性来源。方案的 (b) 超集会让 fork 继续分叉、上游下次改键再冲突，故排除。fork 的隔离意图（逐用户、逐租户）本就由过滤条件实现，不依赖键，因此无能力损失。

**连带影响（实施 MUST 处理）**：

1. `_require_owned_session` 现以 `WHERE session_id=?` 取行并假设单行；须改为同时按 `agent_id` 限定（该方法已通过 `_require_tenant_agent_binding` 解析出 agent，改造自然）。
2. `backfill_owner` 等按 `owner=''` 或 `session_id` 定位的语句须补 `agent_id` 维度。
3. 既有 session/run ID 若在不同 Agent 间出现过重复，迁移时 `(agent_id, session_id)` 可能冲突；迁移前 SHALL 做重复性检测并人工裁决，不得静默丢弃。
4. 迁移须为 `agent_id` 提供 `DEFAULT ''`（上游即如此，使既有行归入默认 Agent、单 Agent 安装行为不变），fork 的 `owner`/`tenant_id` 同样 `DEFAULT ''` 并由 6.8 回填。

**若接缝只登记列而不登记约束，上述决定会漏掉，合并后 fork 的单列主键会静默覆盖上游的复合键，丢失智能体维度唯一性——即本 change 要消除的静默降级。**

**维度区分**：`owner`（逐用户，既有）与 `tenant_id`（逐租户，新增）SHALL 作为两个独立扩展项登记，语义不重叠。新增 `tenant_id` 的理由是提供比用户更粗粒度的纵深防御（当 Agent 归属被错误重绑或工作区被跨租户复用时仍能拦截），而非替代 `owner`。

**取舍**：替代方案是「等上游稳定后再加列」，无法解决当前每次合并都冲突的问题。选接缝，因为它把「两边各加一列/各改一约束」从冲突变成加法。

**未决实施参数**：扩展点的具体函数签名与是否引入轻量 dataclass 描述列/约束，留待实施时按最小改动确定；不作为规格行为。

### D4b：渠道实例模块的分区符号登记（人工纪律，待机制化）

**决策**：`channel/channel_instances.py` 中 fork 新增的符号（`REQUIRED_CREDENTIAL_KEYS`、`required_credential_keys`、租户运行时状态与 `apply_tenant_instance_runtime` 等）与上游新增的符号（`_CHANNEL_TYPE_LABELS`、`default_instance_name`）分别以独立区块承载，区块边界与注释标明归属与用途，使两侧新增落在不同区段而非同一字面量。`CREDENTIAL_KEYS` 等既有共享常量保持原位。

**性质说明（必须记录）**：这是**人工纪律而非机制**——下一次上游在同一锚点附近插入符号仍可能冲突。真正机制化需要把 fork 常量迁到独立模块再重导出，但 `REQUIRED_CREDENTIAL_KEYS`/`required_credential_keys` 被 `auth/service.py` 与前端契约共用，搬迁会扩大改动面。**本 change 采用分区登记，并把「搬迁式机制化」登记为后续项，触发条件：该文件再次产生同址冲突，或 `auth/service.py` 的凭据校验路径发生变更时。**

**实施记录（任务 8.12/8.13）**：文件底部新增单行分隔 `# PARTITION DIVIDER (D4b): add fork symbols below this line only; upstream symbols above.`——其上方为上游/共享符号区（含 `CREDENTIAL_KEYS`、`MULTI_INSTANCE_READY`、全部 base 函数，并为上游的 `_CHANNEL_TYPE_LABELS`/`default_instance_name` 预留），下方为 fork 专属区（凭据最小必填集、租户运行时状态、租户控制台契约）。`tests/test_channel_instances_partition.py` 以 AST 断言两侧符号归属（fork 符号必须在分隔线下方、上游/共享符号必须在上方），并以 `REQUIRED_CREDENTIAL_KEYS` 全量 golden 快照锁定每个已声明类型的返回值；本次仅为纯重排 + 分隔注释，无任何符号/取值/默认值/校验变化。

**再次强调性质**：该分区是**人工纪律而非机制**——它不提供任何 import 期或运行期强制，仅以分隔注释与测试约束位置。转「搬迁式机制化」（fork 常量迁至独立模块 + 重导出）的触发条件明确为两条：**(a)** `channel/channel_instances.py` 再次产生同址冲突；**(b)** `auth/service.py` 的凭据校验路径发生变更（搬迁调用面届时一并调整）。

### D4c：调度任务身份收敛——身份接缝与服务拓扑一并决定

**决策**：实测双方各自实现任务身份解析——fork 的 `_execution_identity(task, agent_id)` + `_make_execute_callback(agent_bridge, agent_id, task_store)`（按任务 `owner` 快照还原成员身份，回调闭包在单个 `agent_id` 上）与上游的 `_resolve_task_agent_id(agent_bridge, task)` + `identity_scope(agent_id=...)`（按任务自身 Agent 解析，全局单一服务）。这不是文本冲突而是**语义分叉**，合并后可能同时存在两条身份路径。

收敛为单一解析接缝：以「任务身份快照（tenant/user/agent/session）→ 执行身份」为唯一入口，租户成员身份与 Agent-only 身份作为该接缝的两种输入；两侧特有行为（revalidation、delivery channel、run 记录）作为下游策略接入。

**同时必须决定的第二件事——服务拓扑与回调契约（已决定，tasks 0.2）**：**采纳上游的全局单一服务**。依据是上游已明确将其设计为全局并自带迁移路径：`get_task_store()`/`get_scheduler_service()`/`get_recipient_store()` 的文档字符串均写明 `workspace_root`/`agent_id` 参数被忽略（「The one global task store/service」），回调签名为 `execute_task_callback(task: dict, trigger: str = "scheduled")`，并在内部 `with identity_scope(agent_id=_resolve_task_agent_id(agent_bridge, task))` 解析身份；上游还提供了 `_migrate_legacy_task_stores(task_store)` 用于吸收 fork 的按工作区任务存储。fork 的 `_make_execute_callback(agent_bridge, agent_id, task_store)` 闭包式单 Agent 服务因此退役，其成员身份还原由身份接缝作为输入接入。

**落点澄清**：身份上下文机制位于 `common/runtime_identity.py`（`identity_scope` 与 `use_identity` 并存，收敛为单一入口）；`agent/tools/scheduler/identity.py` 承载所有者快照与重验（fork 特有行为）。两者职责不同，不得混为一谈。

**取舍**：替代方案是「保留 fork 版本、丢弃上游版本」或反之——都会丢失一侧能力（fork 丢失上游的跨 Agent 任务执行与 run 记录，上游丢失 fork 的成员身份还原）。选收敛，因为这是唯一能同时保住两侧对外行为的路径；代价是需要一份跨两个实现的合并设计。

### D5：前端——i18n 命名空间拆分与视图注册（模块已抽出）

**决策（修正原设想）**：fork 的身份/待办/外观界面**已**是独立文件并由 `chat.html` 加载，**不再需要「抽出为独立模块」**。实测 `console.js` 的 12 处冲突集中在两类：

1. **i18n 字典同址插入**：fork 的 `tenant_channel_*`/`channels_*` 键与上游的 `tasks_*`/`records_*` 键插在同一 dict 字面量。对策：把 i18n 按域拆为独立命名空间文件（如 `i18n/tenant-channel.js`、`i18n/tasks.js`），在加载时合并为单一查找表；`console.js` 不承载任何域的语言键。
2. **视图分派**：`switchView`/repaint 中的 fork 分支（`loadPlatformUsersView`、`loadTodosView` 等，`console.js` 内约 13 处引用）。对策：引入视图注册表，fork 模块注册 `{id, label, load, repaint}`，`console.js` 只按注册表迭代，不含 fork 专有分支。i18n parity 测试扩展为「各域命名空间键完整且无重叠」。

**取舍**：替代方案是「整文件 fork 覆盖」，会让上游前端改进永久丢失。选命名空间 + 注册表，代价是一次性迁移工作量；因模块已抽出，工作量显著小于原设想。

**既有保障**：仓库已有 33 个前端 `.cjs` 契约测试，迁移前后各跑一次以锁定行为。

### D6：同步基建的具体默认值（工程手段，不作 spec 能力）

**决策**：`rerere.enabled=true`（本地/仓库级建议，脚本不强制写用户全局配置）；`.gitattributes` 仅标注 fork 单方定制的二进制资源（图标、logo、图片）为 `binary`；同步脚本 `scripts/sync-from-master.sh` 执行 `fetch` + 合并尝试 + 冲突规模报告，**不自动提交或推送**，并与持久化基线清单对比标出漂移。

**性质说明**：以上为实现手段，落在 tasks/README；spec 只声明行为契约（可重复、报告漂移、不自动提交、不静默丢弃上游改动）。

**取舍**：不引入 `merge.ours` 等会静默丢弃上游改动的策略（对长命 fork 危险）。二进制标注只覆盖确属单方定制的文件。

### D7：P0/P1 切片范围与顺序

**决策**：先做会话/消息主键与身份的**模型决策**（D4 主键、D4c 拓扑）与 D1/D1b/D2（机制）+ P0 收口（D3 及 handler 收口），再做 D4/D5（接缝与迁移），最后做 P1 低风险切片。fail-closed（S6）与门禁（S1）为最高优先级。

**理由**：模型决策是 D4/D4c 接缝的前置输入，先定模型可避免接缝实现后返工。

**取舍**：P1 中需要新模型的项（用户级授权、Agent 单一事实源）不做，避免把可合并性与大功能改造混在一个 change。

### D8：seed 抽公共函数（P1 D8 in 报告）

**决策**：把 `bootstrap` 与 `create_tenant` 中重复的租户默认初始化抽为单一 `_seed_tenant_defaults(tenant_id)`。低风险、消除漂移，纳入本 change。

### D9：删除/修改类冲突的处置决策

**决策**：对实测的 5 个「fork 删除、上游修改」文件，在持久化基线清单中登记处置决策并持续生效：

- `desktop/src/renderer/src/components/PermissionSelector.tsx`：**保留删除（已确认，tasks 0.4）**。依据：该文件在 `dc760766` 被删除，全仓库 `desktop/src` 已无 `PermissionSelector` 引用，且既有测试 `tests/test_execution_permission_ui.cjs` 以 `assert.equal(fs.existsSync(...), false)` **断言该文件必须不存在**——fork 已用 RBAC 角色授权取代自助式会话权限模式（同批归档的 `rbac-controlled-execution-permission`）。决策记入基线清单并附替代物说明；恢复文件会直接使该测试失败。
- `README.md`、`docs/zh/README.md`、`docs/zh/README-Hant.md`、`docs/ja/README.md`：fork 文档策略与上游 README 体系不同，需明确「保留 fork 版本 / 采用上游 / 合并」三选一。

**理由**：该类冲突每次合并都复发，且实测未被任何接缝方案覆盖。决策一旦记录，后续合并按清单自动取舍，不再逐次人工裁决。

**取舍**：不采用 `merge.ours` 全局策略（会静默丢弃上游改动）；只对已确认属 fork 有意删除的文件做单文件决策。

### D10：`app.py` 会话全局化迁移与存储接缝合并为同一决策

**决策（已决定，tasks 0.3）**：**跟随上游全局化**。上游把「单一全局会话文件 + `agent_id` 维度」作为该架构的机制（`conversation_store.py` 注释明言 `agent_id` 是「the mechanism that lets one global file hold every Agent's transcripts」，主键与唯一约束均为 `(agent_id, session_id[, seq])`），并以 `ALTER TABLE ... ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''` 让既有行归入默认 Agent 使单 Agent 安装行为不变；`app.py` 在启动时调用 `migrate_conversations_to_global(kickoff_async=True)`。本 change 因此**不保留 fork 的按 Agent 会话文件物理分离**，fork 的租户与用户隔离改由存储层的 `tenant_id` + `owner` 过滤列承担（即 D4/D6，与 0.1 的非键列决定一致）。

`app.py` 侧不做 fork 分支：上游的 `_migrate_conversations()` 与 `_migrate_legacy_task_stores()` 作为上游能力保留；fork 的启动守卫（身份模式一致性）经 8.9 的扩展钩子登记，与上游迁移调用并存而非互斥。

**理由**：分列两处各自处理会产出互不兼容的实现（例如 app.py 采用上游全局化、而存储接缝仍假设每 Agent 一个文件）。且上游已提供迁移路径，fork 的隔离意图由过滤条件即可等价实现，物理分离并非必要。

**已接受的代价与复核条件**：物理文件分离曾提供一层「不同 Agent 落不同文件」的天然隔离；全局化后该层由查询过滤替代，属纵深防御的层级下降而非缺失（`tenant_id` 过滤 + 既有租户/属主校验仍在）。**复核条件**：若实施中发现某条读取路径无法可靠携带身份（例如后台线程无身份上下文），则不得依赖过滤兜底，须按 5.5 在该边界显式检测身份，或把该切片拆为独立 change 改为保留物理分离。

### D11：`web_channel.py` 签名接缝与上游功能保留

**决策**：

- fork 所需授权目标（`upload_file` 的 `agent_id`、`post_message` 的 `auth_context`/`authorized_session`）SHALL 经接缝获得——例如由门禁注入的请求上下文（D2）或独立的授权目标解析器，MUST NOT 以改写上游公共方法签名为代价；
- 上游 `_import_local_file`（桌面端按路径导入）及其 loopback + 每启动令牌校验 SHALL 在合并中完整保留，作为回归项之一；
- 若某参数确实无法经上下文获得，则以**独立包装函数**承载 fork 行为，上游方法体保持原样。

**取舍**：替代方案是继续维护 fork 的签名分叉——改动最小，但每次上游修改这些方法都会冲突，且这正是实测冲突的来源之一。

## Risks / Trade-offs

- **[派生清单改变行为]** → 迁移期以一次性等价断言对比派生结果与现有 `_WEB_URLS`/`ROUTE_POLICY`（含 5 条缺口的显式列示），不一致即失败；切换前后各跑一次全量路由测试。
- **[不变量仍可能被绕过]** → D1b 的第三腿以内省 handler 为准；对以上游装饰器动态生成方法的 handler，内省可能失真，实施时需对这类 handler 单列白名单并附理由。
- **[主键/唯一约束变更不可回滚]** → 复合键迁移 SHALL 提供专门的回退迁移；上线前在以生产数据规模复制出的库上演练；不可确定归属或有冲突 `(agent_id, session_id)` 的行须先人工裁决。
- **[行所有者回填错误]** → 归属不可确定（所有者缺失、无有效成员关系、多义）的行一律不赋予推测租户维度并保持不可跨租户读出；回填前后行数比对。
- **[门禁集中解析引入性能开销]** → 解析复用请求级缓存（同一请求已解析则复用），不新增每次请求的重复身份库读取；对 `public`/`closed` 路由不解析。
- **[门禁 fail-closed 误伤合法请求]** → 先以「解析失败仅告警」观察一轮，再切拒绝；但 `tenant/platform` 的身份域判定立即生效（该判定本就是现状错误面）。
- **[BREAKING：空租户 403]** → 先在测试与被关闭消费者上验证无合法调用依赖回退；发布说明标注；legacy 行为不变。
- **[D4c 拓扑决策与上游冲突]** → 若选定全局单一服务而上游继续演进每 Agent 服务（或反之），该决策需重新评估；记录为可复审决策。
- **[D10 架构不确定性]** → 若 fork 的按 Agent 隔离与上游全局化不可调和，把该部分拆为独立 change，避免阻塞安全收口切片。
- **[前端迁移产生回归]** → 以既有 33 个 `.cjs` 前端契约测试锁定行为，迁移前后各跑一次；i18n 命名空间迁移期保留旧键聚合层作为兼容别名直至测试迁移完成。
- **[删除/修改类冲突决策错误]** → `PermissionSelector.tsx` 已确认由测试强制其不存在，决策风险低；其余 4 个文档文件的决策记入清单并附理由，便于后续复核。
- **[D4b 仍为人工纪律]** → 已记录触发条件（同址再冲突或 `auth/service.py` 凭据路径变更），届时转机制化。
- **[与在途 change 的编辑重叠]** → 声明 `scan-onboarding-and-inbound-anchor` 归档顺序前置；本 change 在其之后改 `ROUTE_POLICY` 与租户渠道写入面，避免互相覆盖。
- **[fail-closed 改变既有测试期望]** → 明确修订受影响测试（`test_http_policy`、`test_web_chat_boundary` 等）并记录为有意行为变更，不放宽断言迁就旧行为。

## Migration Plan

1. **模型决策先行**：选定会话/消息最终主键与唯一约束（D4）、调度服务拓扑与回调契约（D4c）、上游会话全局化的共存方式（D10），并记录。**这是后续接缝实现的前置输入。**
2. **机制先行**：落地权威清单与派生（D1）+ 三腿不变量（D1b），以等价断言确认零行为变化；补 5 条未登记路由的策略（D1/D9 边界）。
3. **门禁升级**（D2）与 **P0 收口**（D3 + handler 收口 + 管理写 CSRF + 日志限定 + scenes 登记 + `administered-tenants` 作用域）：这一步同时是安全修复和「路由不再需要手工双清单」的收益兑现。
4. **接缝与迁移**（D4/D5/D11/D10）：约束级存储接缝 + 主键迁移 + 租户维度与行所有者回填；`web_channel.py` 签名接缝与上游功能保留；前端 i18n 命名空间与视图注册。
5. **删除/修改类冲突决策落地**（D9）：写入持久化基线清单。
6. **P1 低风险切片**：默认 Agent 解析校验、RBAC 关联表复合外键、配额 fail-closed、seed 抽公共函数。
7. **同步基建**（D6）：`rerere` + `.gitattributes` + 同步脚本 + 基线漂移报告。
8. **回滚策略**：机制项（派生清单/门禁）可按策略回退到「透传」；`tenant_id` 第二道过滤可停用；前端模块可按挂载点开关停用；数据迁移中的**主键/唯一约束变更不可自动回滚**，SHALL 有专门回退迁移且不回滚已回填列（无害）。每步为独立可回滚切片，不做一次性大切换。
