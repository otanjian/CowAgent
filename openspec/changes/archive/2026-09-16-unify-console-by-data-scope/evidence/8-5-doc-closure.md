# 8.5 按实际结果更新设计/交付文档：规划 / 已实现 / 已验收 / 未覆盖

对应 `tasks.md` 8.5：「按实际结果更新统一控制台方案、菜单/权限/记忆设计及交付文档，区分规划、
已实现、已验收与未覆盖；同步本 change 对旧要求的取代矩阵和必要的 capability Purpose。」

本文件是这次文档收口的**索引与判定依据**：第 2 节给出分项判定（含证据），第 3 节是取代矩阵，
第 4 节列出需要同步的 capability Purpose，第 5 节逐文档记录改了什么，第 6 节是复核命令，
第 7 节是**没做到的部分及其原因**。

## 1. 判定口径（四个状态不能混用）

| 状态 | 判据 | 反例（不算「已验收」） |
| --- | --- | --- |
| **规划** | 只存在于设计文本，代码里没有实现 | 文档里写了接口名不等于接口存在 |
| **已实现** | 代码存在（`file:line` 可指），有测试，但**没有**端到端或真实环境验收 | 单元测试通过 ≠ 真实链路通过 |
| **已验收** | 本 change 内有证据文件 + **可复跑命令**，且在目标形态（真实服务/隔离库/浏览器）上跑过 | 只跑替身、只勾任务框 |
| **未覆盖** | 本应在本 change 覆盖，但未验证或未交付 | 必须写明原因与前置，**不得**记为通过 |

「归档」不是任何一种状态：`openspec/changes/archive/**` 只说明当时写了什么。

## 2. 分项实际状态

### 2.1 已验收

| 切片 | 状态 | 证据 / 判定依据 |
| --- | --- | --- |
| 统一对象范围与资格分离（2.1、2.2） | ✅ 已验收 | `auth/object_scope.py`（`ObjectScope`：`allows_agent` `:117`、`allows_personal_memory` `:137`、`allows_agent_memory` `:147`、`allows_channel_instance` `:159`、`allows_public_configuration` `:183`）+ `tests/test_object_scope.py`（20 项）+ `evidence/2-1-object-scope.md`、`evidence/2-2-qualification-split.md`、`evidence/2-5-scope-consistency-drill.md` |
| 用户默认独立版本迁移（2.3） | ✅ 已验收 | `_migration_25`（`auth/store.py:1233-1345`）+ `evidence/2-3-user-default-migration.md` + `tests/test_user_default_migration.py`（21 项）+ 本 change `tests/test_console_migration_drill.py` 的断点/回滚用例 |
| 旧个人菜单的幂等映射（2.4） | ✅ 已验收 | `_migration_26`（`auth/store.py:1348-1428`）+ `evidence/2-4-menu-mapping-migration.md` + 8.3 演练（含变异验证） |
| 控制台准入改为正式页面资格（3.1） | ✅ 已验收 | `console.js:18356`（`_qualifyAdminConsoleEntry`，注释即任务 3.1 的判据）+ `evidence/3-1-console-entry-qualification.md` |
| 账号菜单「我的资源」五项删除（3.3、3.4） | ✅ 已验收 | `evidence/3-3-account-menu-resources-removed.md` + `tests/test_account_menu_no_personal_resources.cjs`（7 项，本周期重跑 7 passed）；`chat.html` 中 `id="view-personal` 计数 0 |
| 首页按范围统计（3.5） | ✅ 已验收 | `evidence/3-5-overview-by-scope.md` |
| 真实浏览器双角色共用页面（3.6） | ✅ 已验收（**范围受限**） | `evidence/3-6-browser-dual-role-acceptance.md`：真实浏览器只读验收双角色；**当场未造态**的部分（直达/返回/离页取消、租户切换）在该文件里已自记为未覆盖 |
| 智能体统一生命周期（4.1、4.3） | ✅ 已验收 | `evidence/4-1-unified-agent-creation.md`、`evidence/4-3-unified-agent-lifecycle.md` |
| 用户默认「设为默认」统一动作（4.4） | ✅ 已验收 | `set_user_default_agent`（`auth/service.py:861`）+ `set_user_default` 动作（`channel/web/web_channel.py:10820`、`:10846`）+ `evidence/4-4-user-default-agent.md` |
| 租户默认分离且拒绝私有目标（4.5） | ✅ 已验收 | `set_tenant_default_agent`（`auth/service.py:1603`）与旧 `appoint_tenant_default_agent`（`:1620`）并存分权 + `evidence/4-6-default-initialisation-and-source.md` |
| 供应只初始化空偏好 + 默认解析来源（4.6） | ✅ 已验收 | `evidence/4-6-default-initialisation-and-source.md` |
| 生命周期/默认一致性验证（4.7、4.8） | ✅ 已验收（**含显式差异记录**） | `evidence/4-7-lifecycle-and-defaults-verification.md`（租户默认无乐观锁——规范未要求；`agent_bindings.agent_id` 主键使「一 Agent 绑两租户」不可表示；无「克隆来源」模板判据）、`evidence/4-8-default-resolution-scope.md` |
| 记忆读面统一 + 共享写入核（5.1 前半、5.2） | ✅ 已验收（**写面受限**） | `evidence/5-1-memory-target-set.md`、`evidence/5-1b-write-path-and-acceptance.md`、`evidence/5-1b-write-path-design.md` |
| 记忆写面拒绝矩阵与索引屏蔽（5.3） | ✅ 已验收（**口径更正为拒绝**） | `tests/test_memory_console_write.py`（19 项，见 §6）——「本人私有智能体记忆**可写**」实测为提权，验收结论是**拒绝**；读面按范围、写面限管理资格 |
| 公共技能定义/全局启停的资格收口（5.4 前半、5.5） | ✅ 已验收 | `evidence/5-4-public-surface-authority.md` §1–2 + `tests/test_skill_public_surface_scope.py`（8 项）与前端 4 项，含变异验证 |
| 渠道配置面统一（6.1–6.6） | ✅ 已验收 | `evidence/6-1-shared-channel-surface.md`、`6-2-channel-target-derivation.md`、`6-3-to-6-6-write-merge-and-scan.md`（637 passed） |
| 运行面合并（7.2） | ✅ 已验收 | `evidence/7-2-7-3-runtime-merge-audit.md`：三个运行文件零角色标记，分支键只有行自身 `scope`/`owner_user_id`，入站按存储归属+目标+观测发送者三元组校验（14 项） |
| 旧地址受权转接（8.1） | ✅ 已验收 | `evidence/8-1-legacy-personal-address-forward.md` + `tests/test_personal_address_forward_frontend.cjs`（6 项，本周期重跑 6 passed） |
| 兼容周期调用观测与收口判定（8.2） | ✅ 已验收（**判定为「数据面收口、组件面未收口」**） | `evidence/8-2-compat-cycle.md` + `tests/test_compat_surface_closure.py`（11 项，观测器，含 4 次变异验证；其中 2 次在本任务第二轮补跑） |
| 菜单/偏好迁移演练（8.3） | ✅ 已验收 | `evidence/8-3-migration-drill.md` + `tests/test_console_migration_drill.py`（12 项，含两次变异验证） |

### 2.2 已实现（缺端到端/真实环境验收）

| 切片 | 状态 | 缺口 |
| --- | --- | --- |
| 运行面开关迁移（7.3） | 🟡 部分 | 目录/配置/执行各单一来源、`personal_channel_runtime` 关闭在三个 seam 成立；**残留两处「显式关闭被新入口绕过」**（`personal_channel_onboarding` 关闭时经 `/api/tenant/channels` 新建仍成功；`member_personal_console` 关闭时私有 Agent 创建仍成功且投影报 `create=True`），登记在 `tasks.md` 7.3 与 `evidence/7-1-runtime-preflight.md` |
| 前端回归基线（8.4 前半） | 🟡 部分 | 基线已固定并可复跑（§6），但「真实浏览器 + 真实渠道」的回归未完成 |
| **成员模型目录（5.4）** | 🟡 已实现 | 快照内已落地：`admin.models` 页 scope 改为 `tenant`（`auth/service.py:103`）、新增 `model_catalog_open()`（`:3393`）并把页面 `available`/`read_allowed` 与它对齐（`:3737-3751`），写面仍单独报 `actions.manage`（平台资格）。用例：`tests/test_member_model_catalog.py` + `tests/test_session_model_catalog.py` + `tests/test_session_model_scope.py`（本周期重跑 `29 passed`）、前端 `tests/test_member_model_catalog_frontend.cjs`（`7 passed`）。**缺口**：属主证据 `evidence/5-4-public-surface-authority.md` §3 截至 23:54 仍写「死代码、未交付」，尚未回填；本文件按**代码事实**登记，不代替属主验收 |
| **个人参数并入共用详情组件（5.4）** | 🟡 已实现 | 快照内已落地：工具/技能详情组件渲染个人参数区（`console.js:12050`、`12100-12142`，只读态见 `:12117-12126`），写面走 `/api/tools`、`/api/skills` 的 POST（`route_registry.py:197-198`，注释即「owner fixed from the session；工具定义不变」）。**缺口**：缺一条把它钉住的用例与属主证据（属主范围） |
| **个人资源面退役（5.5 后半）** | 🟡 已实现 | `/api/personal/resources` 从路由表移除、冻结基线标 `REMOVED`（`route-baseline.txt:245-246`）；`personal-tools`/`personal-skills` 视图零注册；账号命名空间五个旧个人键删除。零出现断言 + 变异验证见 `evidence/8-2-compat-cycle.md` §1.2、§2。**缺口**：属主证据未回填（同 5.4 行） |

### 2.3 未覆盖（含原因，不得记为通过）

| 未覆盖项 | 原因 / 前置 |
| --- | --- |
| **真实提供方渠道运行**（7.4、7.5、7.6） | 阻塞于**真实凭据与真实运行进程**，不是代码：① 缺与既有密文同一把 `COW_CREDENTIAL_MASTER_KEY`；② 无真实 `*_app_id`/`*_secret` 与 ≥2 个真实账号；③ 需真正装配 `_channel_mgr` 的进程，否则 `apply` 永远 `pending`，「已连接」不可观测。三类阻塞任一成立即足够，详见 `evidence/7-1-runtime-preflight.md`。**不得**用归档 change 的勾选状态替代。 |
| **本人连接的运行开关与验收集** | `PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()`（`channel/channel_instances.py:1340`）、`PUBLIC_PERSONAL_INGRESS_TYPES`（`:1346`）为空、`personal_channel_runtime=False`（`config.py:290`）；7.6 要求「显式启用」与真实证据**同批提交** |
| **渠道类型目录准入性**（7.7） | 8 项里 5 项从不 `stamp_external_identity`（`INBOUND_IDENTITY_STAMPING_TYPES` 只有 `feishu`/`dingtalk`/`wecom_bot`，`channel/channel_instances.py:122`），其入站永远无法按实例证明发送者，却能被创建并报 `connected`；裁定与推荐见 `evidence/7-channel-type-admissibility.md` |
| **成员写本人私有智能体记忆**（5.1/5.3 原句） | 数据库形态下**智能体记忆根就是租户共享根**，按范围放行会让成员写入共享范围、删除共享文件（`evidence/5-1b-write-path-design.md` §6 有实测输出）。前置是**每个智能体独立记忆根**（架构改动），本轮口径回写为读面按范围 + 写面限管理资格 |
| **退役组件本体与旧 i18n 命名空间**（5.5/8.6） | 个人资源面已移除（§2.2），但 `personal-console.js` 仍注册 3 个视图（`70-82`、`799-805`）并由 `chat.html:2821` 加载，旧 i18n 仍由 `chat.html:2803` 加载，投影仍签发 5 个 `personal.*` 页（`auth/service.py:117-121`、`173-187`）。删除属**生产代码改动**，须与 8.6 收口同批 |
| **退役后未同步的 i18n 快照**（并发发现） | 账号命名空间删掉五个旧个人键后，`tests/test_console_i18n_parity.cjs` 从 `5/5 passed` 变 `3/5`：其冻结快照 `tests/fixtures/console_i18n_snapshot.json` 仍期望这些键（在 HEAD 内容沙箱里跑是 5/5，证明是本次退役引入而非既有失败）。修法在属主范围（更新快照或保留键位），本任务只登记，见 `evidence/8-2-compat-cycle.md` §7 |
| **旧开关的独立运行读取移除** | 五个开关仍各自被逐点读取（台账见 `evidence/8-2-compat-cycle.md` §3），尚未由统一能力状态供给；前置是阶段 7 运行改造 |
| **原生 Desktop 真实打包客户端演练** | `desktop_tenant_context` 切片 `accepted=false`、`open={}`（`docs/design/database-capability-parity-delivery.md` §2）；8.7 未执行。8.2 只能证明受版本控制的 `desktop/`（117 个文件，其中 `desktop/src/**` 96 个）检索不到旧个人令牌（0 命中），**不能**证明打包产物行为 |
| **生产流量计数** | 仓库没有针对旧入口的计数器/日志/审计事件，兼容周期的「调用观测」只能做到静态 + 断言级；要拿到真实调用次数需先在兼容层埋点（生产代码改动） |
| **真实浏览器复检** | 本机无 Playwright（`tests/test_appearance_browser.cjs` 整文件因此失败），兼容周期的界面观测沿用 3.6 的真实浏览器记录，未在本周期重跑 |
| **租户默认乐观锁** | 规范未要求；`evidence/4-7-lifecycle-and-defaults-verification.md` 已显式记录为差异而非缺陷 |
| **本人执行路径的动作审批消费** | 适用动作审批消费者本身已交付（`docs/design/database-capability-parity-delivery.md` §9），但本人执行路径无真实消费方；属开启执行前的前置 |

### 2.4 规划（文档里有、代码里没有）

| 规划项 | 现状核对 |
| --- | --- |
| `resource_catalog` 资源目录表 | **不存在**（全仓 0 命中） |
| `model_policies` 模型策略表 | **不存在**（0 命中）；已实现的是 `roles.model_defaults_json`（每角色每能力最多一个默认） |
| `tenants.authorization_revision` | **不存在**（0 命中）；`tenants` 只有 `version` |
| `AuthorizationService`（`check_action`/`filter_resources`/`grantable_resources`/`effective_navigation`/`explain`） | **不存在**（0 命中）；实际判定分散在 `auth/object_scope.py` 与 `auth/service.py` 的资源授权读取（`:2020`、`:2039`） |
| `membership_resource_grants`（成员例外） | **不存在**（0 命中），设计文档自己也写明「不作为本次前置」 |
| 角色编辑的七个资源页签（功能/菜单/技能/工具/模型/智能体 + 有效权限预览） | **未实现**：`identity-admin.js` 无资源选择页签；已实现的是功能权限多选 + `resource_grants`/`model_defaults` 保存 |
| 工作文件独立页面、菜单搜索/收藏 | **未实现**（`menu-structure-audit-and-plan.md` §4/§7 的「后续」项） |
| 场景应用、审计、备份升级、开放 API 入口 | **仍为占位/隐藏**，与文档口径一致 |

**已实现的反例**（避免把规划误记为缺失）：`role_resource_grants`（`_migration_11`，`auth/store.py:681`）、
`tenant_resource_grants`（同迁移，`auth/service.py:2039`、`:2443`）、`roles.model_defaults_json`
（角色授权保存路径 `auth/service.py:6460`、`:6499`）**都已存在**并有用例
（`tests/test_identity_resource_authorization.py`）。

## 3. 取代矩阵（本 change 取代的旧要求）

矩阵的**每一行都是本 change 的实际落点**，不是意图。旧材料保持原样，只在文档里标注适用范围。

| 旧材料 / 旧要求 | 被取代的部分 | 保留的部分 | 实际落点 |
| --- | --- | --- | --- |
| `enable-member-personal-console`（成员个人控制台交付说明） | 独立 personal 页面、个人 API 实现、个人专属功能分支 | 数据归属、凭据加密、审计、配额、记忆版本、真实验收记录 | 页面/地址转接（`evidence/8-1-*.md`）；个人资源面已移除（视图零注册 + `/api/personal/resources` 标 `REMOVED`）；退役组件本体、旧 i18n 与旧端点薄适配**未收口**（8.2 §1.2、§5） |
| `move-personal-menu-to-account`（个人入口放进账号菜单） | 「账号菜单承载五个个人入口」决策 | 账号身份恢复、菜单可访问性、焦点与响应式 | 五项入口已删除（`evidence/3-3-*.md`）；账号面板语义保留 |
| `remove-account-personal-resources-menu` | 仅删菜单而保留独立个人页面的最终形态 | 删标题与五项、保留账号设置 | `chat.html` 无 personal 容器（`id="view-personal` = 0）；个人资源面视图与端点已移除；组件本体退役待 8.6 |
| `upgrade-personal-channel-workbench` | 独立个人渠道工作台与个人运行分支 | 目标归属、扫码安全、实例版本、真实状态成果 | 渠道配置面合流（`evidence/6-1-*.md`、`6-3-to-6-6-*.md`）；运行面合流（`evidence/7-2-7-3-*.md`） |
| 既有租户默认方案（`tenant-default-agent-administration` 原文） | 共用详情按钮写租户默认、任命私有即转共享 | 明确命名的租户默认配置、删除指针清理 | 用户默认与租户默认分离（4.4/4.5、`evidence/4-6-*.md`）；`private_owner_user_id` 不被任何迁移改写（8.3 静态护栏） |
| 权限/多租户方案（管理员专属控制台） | 管理员专属控制台、独立个人操作面 | User/Membership/Role、多租户、owner、公共/平台边界 | 控制台准入改为正式页面资格（`console.js:18356`）；组织与权限、平台运维保留管理资格 |
| 个人对话与记忆方案（`personal-conversation-and-memory`） | 个人页面作为独立管理入口 | 当前 tenant/user 记忆归属、跨获准智能体检索与清空一致性 | 读面统一（`evidence/5-1-*.md`）；**写面为管理资格**（口径更正，`evidence/5-1b-*.md` §6） |
| `complete-database-capability-parity` / `complete-desktop-and-scan-real-acceptance` | 「个人入口作为独立消费者」的开放登记口径 | 10 个方法的真实路由/授权、扫码配置面、动作审批消费者 | 渠道配置面合流 6.1–6.6；执行面与 Desktop **仍为未覆盖**（同 §2.3） |
| `role-resource-authorization-plan` 的「平台 all + 有限 grants」 | 「控制台只有管理员能用」 | `role_resource_grants`/`tenant_resource_grants`/`model_defaults_json`、平台 all | grants 与平台 all 已存在；对象的**归属范围**由本 change 的 `ObjectScope` 补充 |
| `menu-structure-audit-and-plan` 的「22 个入口一一对应」 | 5 个占位入口的伪可用 | 命名、分组、可访问性结论 | 占位入口仍隐藏/占位；菜单 grant 走权威投影 |

## 4. 需要同步的 capability Purpose

Purpose 属主规范（`openspec/specs/<capability>/spec.md`），按 OpenSpec 规则**不在本 change 内直接改**
（delta 只承载 requirement 的增删改；主规范在归档时合并）。因此这里登记**待归档同步项**，
由 8.6/归档步骤执行：

| capability | 现状 Purpose | 为什么需要同步 | 建议口径 |
| --- | --- | --- | --- |
| `member-personal-console` | 「为具有有效租户成员身份的用户提供**统一控制台中的个人资源入口**……」 | 本 change 的 delta 已删除「成员默认获得个人控制台入口」等 5 条 requirement（`specs/member-personal-console/spec.md`），主规范 Purpose 仍以「个人资源入口」为主语，读起来像还有独立入口 | 改为「成员在**同一套正式控制台**中按数据范围维护本人对象；旧个人入口仅在兼容期内转接」 |
| `user-personal-context` | `TBD - created by archiving change personal-conversation-and-memory. Update Purpose after archive.` | 占位文本从未回填，而本 change 修改了该 capability（记忆改由统一页面维护） | 写实：本人人设与用户记忆按 tenant/user 归属，从统一记忆页面维护；读面按范围、写面限管理资格 |
| `console-information-architecture` | 描述「工作台与管理控制台两个独立导航区域」 | 本 change 新增了「控制台对成员开放」的 MODIFIED/ADDED requirement，Purpose 未提成员可获得业务页面 | 补一句：控制台区域的入口按**页面资格**而非管理员身份展示 |
| `sidebar-account-menu` | 描述账号卡片与「个人资源与设置」提示 | 本 change 删除了该菜单里的五项业务入口（delta 有 REMOVED） | 明确账号菜单只承载账号操作，业务资源从控制台进入 |
| `database-memory-console` | 「将既有记忆页面连接到统一的个人记忆管理能力」 | 写面口径已更正为「管理资格」，读面按范围 | 在 Purpose 里区分**读面按范围 / 写面按管理资格** |
| `unified-console-access`、`user-default-agent-selection` | 本 change 的 ADDED capability，delta 内已有 Purpose | 无需同步 | — |

## 5. 本次更新的文档

| 文档 | 更新的位置 | 怎么分类 |
| --- | --- | --- |
| `docs/design/unified-console-access-plan.md` | 顶部状态行；新增 §0「实际状态（2026-09-16 复核）」；§6 取代矩阵补「实际落点」列；§7 去掉「未执行产品改造」 | 主体已验收；分项列出已验收/已实现/未覆盖/规划 |
| `docs/design/menu-structure-audit-and-plan.md` | 顶部变更块改为实际状态；新增 §10「按实际结果的分类」；§4 目标结构标注「部分已实现 / 规划」 | 占位入口与账号菜单结论=已验收；split 区域与命名=已实现/已验收；工作文件、菜单搜索=规划 |
| `docs/design/personal-conversation-and-memory.md` | 顶部变更块改为实际状态；§3.3 标注「已验收」；§5 Non-Goal 标注现状 | 用户默认/租户默认=已验收；个人记忆编辑入口=部分（读面+本人用户记忆写）；私有智能体记忆写=未覆盖 |
| `docs/design/personal-conversation-and-memory-acceptance.md` | 顶部变更块加「本轮关系」；正文历史结论保持原样 | 23/23=历史验收（当时实现）；本 change 不重跑该脚本 |
| `docs/design/role-resource-authorization-plan.md` | 顶部变更块改为实际状态；§5 表逐项标注存在性；新增 §9「实际状态」 | grants/平台 all=已实现；成员模型目录=🟡 已实现（快照内 `admin.models` scope 改 `tenant` + `model_catalog_open()`，未验收）；七个页签、`resource_catalog`、`model_policies`、`AuthorizationService`、`authorization_revision`=规划 |
| `docs/design/role-resource-authorization-delivery.md` | 顶部变更块改为实际状态；§6.2 未交付项确认仍为未交付；新增 §8「本 change 之上的增量」 | 组件集成=已验收（历史）；线上运行开放=未覆盖（与阶段 7 同一原因） |
| `docs/design/user-role-permission-gap-and-plan.md` | 顶部变更块改为实际状态；§9 补「成员模型目录已实现」 | 身份底座与 A/B=已实现；资源授权=C 部分已实现；成员模型目录=🟡 已实现（未验收）；模型策略、运行消费者、SSO、Desktop 企业适配=规划/未覆盖 |
| `docs/design/member-personal-console-delivery.md` | 顶部变更块改为实际状态；新增 §10「新方案下的实际状态」 | 五项开关的原切片结论保持历史；新方案下：入口/页面容器已删、旧地址受权转接、资源面（视图 + 端点 + 旧 i18n 键）已移除、菜单 grant 已映射；**仍活**的是退役组件本体、旧 i18n 文件、旧端点转接与后端签发的 5 个 `personal.*` 页 |
| `docs/design/database-capability-parity-delivery.md` | 顶部变更块改为实际状态；新增 §11「与本 change 的关系」 | 10 个方法与扫码配置面=已验收（历史）；渠道执行面、Desktop=未覆盖（同 §2.3 原因） |

共同约定（所有文档一致）：

- 顶部块保留原日期与历史结论，**新增**一段「实际状态（2026-09-16）」指向本文件，不再写「待实施」。
- 历史验收记录**不改写**；新结论以本文件的分类为准。
- 凡是未取得真实凭据/真实客户端的切片，一律写「未覆盖 + 原因」，**不写**「已通过」。

## 6. 复核命令（本文件引用的判定都能重跑）

```
# 前端基线（8.4 基线重跑：失败集合已变动，见第 3 行）
node --test tests/*.cjs
→ ℹ tests 699   ℹ pass 654   ℹ fail 45
   45 = test_session_history_frontend.cjs 36 + test_sidebar_account_frontend.cjs 5
      + test_console_i18n_parity.cjs 2 + _tmp_repro_modeldefaults.cjs 1
      + test_appearance_browser.cjs 1（无 Playwright）
   8.4 基线为 695/652/43；本周期多出的 2 条全在 test_console_i18n_parity.cjs，
   由本次「删账号菜单旧个人键」引入（同一用例在 HEAD 内容沙箱 /tmp/fe38 里 5/5 passed），
   不是既有失败。逐文件复核命令：
   node --test tests/test_session_history_frontend.cjs tests/test_sidebar_account_frontend.cjs \
     tests/test_console_i18n_parity.cjs tests/_tmp_repro_modeldefaults.cjs \
     tests/test_appearance_browser.cjs

# 迁移/兼容收口组
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_user_default_migration.py tests/test_personal_instance_target_repair.py \
  tests/test_management_repair_tenant_defaults.py tests/test_console_menu_mapping.py \
  tests/test_console_migration_drill.py tests/test_compat_surface_closure.py -q -p no:randomly
→ 110 passed, 23 subtests passed (0:01:22)

# 兼容转接（旧地址受权转接）
node --test tests/test_personal_address_forward_frontend.cjs \
  tests/test_account_menu_no_personal_resources.cjs tests/test_personal_console_frontend.cjs
→ tests 70 / pass 70 / fail 0

# 成员模型目录（5.4 快照内已实现，属主范围）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_member_model_catalog.py tests/test_session_model_catalog.py \
  tests/test_session_model_scope.py -q -p no:randomly
→ 29 passed (0:00:25)
node --test tests/test_member_model_catalog_frontend.cjs
→ tests 7 / pass 7 / fail 0

# 变更本身的校验（本任务改动的文档/证据不属 spec，但改动后仍需通过）
openspec validate unify-console-by-data-scope --strict
→ Change 'unify-console-by-data-scope' is valid

# 对象范围 / 记忆写面 / 技能公共面 / 交付演练 / 开关
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_object_scope.py tests/test_memory_console_write.py \
  tests/test_skill_public_surface_scope.py tests/test_personal_delivery_drill.py \
  tests/test_personal_capability_switches.py -q -p no:randomly
→ 83 passed, 3 subtests passed
  （20 + 19 + 8 + 8 + 28 项）
```

记忆、技能公共面、交付演练与开关的用例结果见 §2.1 各行引用的证据文件；本文件不重复粘贴。

## 7. 未覆盖（本任务自身）

* **主规范 Purpose 未直接改写**：按 OpenSpec 规则，Purpose 属主规范、由归档合并落地，delta 只承载
  requirement。§4 已给出逐项建议口径与理由，**未在本次改主规范**；若 8.6 判定应由本 change 一并
  改主规范，请按 §4 执行，不要把它当作已完成。
* **真实凭据/真实客户端相关的一切结论**：一律未覆盖（§2.3），本文件不给出任何「运行可用」表述。
* **文档内部的旧计数**：历史文档里的事件性数字（测试计数、切片数量）保持原样，只在新增段落里区分
  当时与现在——改写历史数字会让旧证据失去可追溯性。
* **并行收口造成的「同一时刻两种事实」**：本任务进行期间，另一路 worker 正在同一批文件上关闭
  5.4/5.5（`auth/service.py` 的 `admin.models`、`channel/web/route_registry.py`、
  `personal-console.js`、账号 i18n）。本文件与 8.2 的做法是：**按观测到的代码事实登记**，
  同时标注「属主证据未回填、未验收」，不代替属主勾任务、不改属主证据。
  代价是行号会漂移——所以每处结论都绑定 sha256 前缀与可重跑的断言（见 8.2 §0）。
* **`docs/design/*.md` 的系统性重写**：本次只做「状态分层 + 取代矩阵 + 逐项判定」，
  没有把历史文档改写成新方案的口吻。历史章节保留原样是有意为之（可追溯性），
  若归档时要求单一叙事，需要另开一次文档改写，不属本任务。
