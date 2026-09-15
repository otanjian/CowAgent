## 1. 基线与依赖切片

- [x] 1.1 合并梳理 owner 可达性、私有文件范围、工作区控制台与知识写权限 change 的实际改动，记录 requirement 归档顺序和接口交集，不覆盖其他 change 的成果。
- [x] 1.2 建立当前租户本人、同租户他人、tenant_admin、有效成员平台管理员、跨租户及失效成员的授权矩阵，区分个人操作、公共操作和治理元数据。
- [x] 1.3 确认凭据、审计、硬配额、资源执行及适用隔离/审批切片的证据；为缺少真实证据的后续消费者登记关闭门槛。
- [x] 1.4 定位现有 Agent、记忆、工作区、文件、预览与知识中的管理员 owner 旁路，记录需要本 change 收紧的调用点和旧测试预期。

## 2. 身份域、策略与迁移

- [x] 2.1 为渠道实例增加 tenant/user 作用域与 owner 约束，保持历史实例、ID、凭证版本和公共管理入口兼容。
  > **已完成（2026-09-14）**。`restrict-knowledge-write-authorization` 已归档（`archive/2026-09-14-restrict-knowledge-write-authorization`），阻塞解除。
  > 迁移号 `_migration_16`：新增 `scope`（NOT NULL DEFAULT `'tenant'`）与可空 `owner_user_id`；唯一键改为
  > `(tenant_id, scope, COALESCE(owner_user_id,''), channel_type, display_name) WHERE active=1`。
  > `_resolve_instance_scope` 统一校验 scope/owner（未知 scope、缺失 owner、tenant 带 owner、owner 非本租户在职成员一律 400）；
  > `list_tenant_channel_instances` 只返回 `scope='tenant'`，个人实例不进公共管理列表。
  > 兼容性由 `test_upgrading_an_existing_instance_keeps_its_id_and_credential_history` 钉住（已验证：禁用迁移即失败 `no such column: scope`）；
  > 新增泄漏护栏的变异测试见 `tests/test_tenant_channel_mutations.py` 用例 4。
- [x] 2.2 为私有 Agent 增加系统供应/成员自建来源及供应幂等约束；只按可信证据回填存量助理，未知来源禁止推定可删除。
  > **已完成（2026-09-14）**。`_migration_17` 给 `agent_bindings` 加 `origin TEXT NOT NULL DEFAULT 'unknown'`。
  > 取值 `provisioned_assistant` / `user_created` / `unknown`；`unknown` 是刻意保留的显式未知，**不猜成可删除**——
  > 存量行只被分类、绝不回填成可替换。
  > 供应幂等改为按来源判定（`SUPPLIED_ASSISTANT_ORIGINS` = `provisioned_assistant` + `unknown`）：
  > 成员自建（`user_created`）不再作为跳过依据，而存量 `unknown` 仍跳过，避免给既有成员多发一份；
  > `personalize_existing` 回填同样只修系统供应的那些，不改写成员自建对象。
  > `bind_agent` 增 `origin` 参数并做白名单校验；重绑只在仍为 `unknown` 时补写，已记录的来源不被覆盖。
- [x] 2.3 实现本人绑定挑战及当前租户个人渠道关联，复用外部身份唯一映射，保证解绑个人关联不删除其他租户的映射。
  > **已完成（2026-09-14）**。`_migration_18` 新增 `binding_challenges`（一次性挑战，服务端固定 tenant/user/instance/purpose，
  > 只存哈希，`consumed_at` + `attempts` 限制重放与爆破）与 `personal_channel_links`（`(tenant_id,user_id,instance_id)` 主键的本人路由，
  > 引用而非拥有 `external_identities`）。
  > 服务层：`create_binding_challenge` / `consume_binding_challenge` / `link_personal_channel` / `unlink_personal_channel` / `personal_channel_link`。
  > 未知目标、他人实例、跨租户实例一律拒绝；三元组已绑到他人返回 409 且不覆盖（复用 `_bind_external_identity_row` 的校验与审计）；
  > 解绑只删本人路由，全局映射保留（另一租户路由不受影响，已测）。
  > HTTP 编排与提供方验签回调属控制台/运行消费者切片（2.6、7.x），不在本任务内。
  > 实现时被自己的测试抓到一处真实缺陷：SQLite 的 `TEXT PRIMARY KEY` 不隐含 NOT NULL，已显式补 `NOT NULL`。
- [x] 2.4 实现个人工具/技能参数与个人凭证引用的唯一存储，禁止敏感字段进入公共配置和明文文件。
  > **已完成（2026-09-14）**。`_migration_19`：`credentials.owner_user_id`（可空，租户级凭据保持 NULL，不回填）
  > 与 `personal_resource_configs`（PK `(tenant_id, user_id, resource_kind, resource_id)`，只存非敏感 `params_json` + `credential_id` 引用）。
  > 服务层：`save_/get_/resolve_personal_resource_config`；归属恒为调用者本人，无 `user_id` 入参，
  > 管理员也无法写入他人个人配置。保存门槛是「已持有该资源的 *使用* 动作」（`tool:execute` / `skill:use`），
  > 不是公共维护动作，因此个人保存**不可能新增授权**；`resolve` 在**使用点重验**授权，
  > 撤权后已存参数与凭据立即失效（需求「不产生额外执行资格」）。
  > 敏感值只经 `credentials` 加密保存（owner 作用域、版本化、审计），从不进入参数 blob 或公共配置文件。
  > 防泄漏：`list_credentials` 增加 owner 过滤（他人个人凭据对租户管理员不可见）；
  > `resolve_credential` 增加 owner 判定（非 owner 即使持有管理资格也拒绝，防管理员代用/注入），
  > 且拒绝发生在解密之前、存在性披露之前。owner 本人无需 `credential.use` 即可解析自己的个人凭据。
  > 三条关键护栏均已变异验证（去掉即有用例失败），源码逐字节还原。
- [x] 2.5 接入个人资源额度、允许渠道类型和治理停用状态；创建/启用采用原子配额判定，owner 不能清除治理停用。
  > **已完成（2026-09-14）**。门槛 Q1 解除后落地（证据见 `evidence/2-7-stage2-drill-evidence.md` §5）。
  >
  > `_migration_21`：新增 `tenant_channel_policies`（`personal_enabled`、`allowed_types_json`、
  > 个人/租户两级 `*_instance_limit`，`-1` = 不限）与实例上的 `governance_disabled_at/by`。
  > **无策略行 = 未收紧，而非拒绝**，故升级后的租户行为与升级前完全一致（存量实例两列默认 NULL、仍在运行）。
  >
  > 服务层：
  > - `get_/set_tenant_channel_policy`（租户控制面 + 近期口令 + 审计）；未就绪/未知类型拒绝。
  > - `_enforce_personal_instance_policy` 在**同一个 `BEGIN IMMEDIATE` 事务内**读策略、计数、再插入，
  >   计数**包含已停用实例**——「停用不能成为无限创建对象的配额绕过」（design D-x）。
  > - 创建（`scope='user'`）与启用都走该判定；启用时重验，覆盖「建后租户收紧策略」。
  > - `set_personal_instance_governance`：停用同时置 `active=0`（后续消息前即生效）；
  >   **解除限制不自动恢复**，须本人明确启用。
  > - 编辑路径：元数据编辑仍允许（「配置仅在允许范围内处理」），但**凭据轮换与启用被拒绝**
  >   （`governance_disabled` 403），杜绝借轮换/重启绕过。
  > - `list_enabled_tenant_channel_instances` 增 `governance_disabled_at IS NULL` 作为独立第二道闸。
  > - 投影新增 `governance_disabled/_at/_by`——只暴露治理元数据，不暴露私有配置；
  >   治理审计的 `redacted_changes` 不含渠道类型、显示名、owner 与凭据。
  >
  > 验证：`tests/test_personal_instance_policy.py` 41 项全绿（含**双并发创建抢最后一个名额**：
  > 恰好一胜一败、败者不留实例与凭据行）。9 处关键护栏变异验证全部被发现：
  > 计数排除停用实例、允许类型判定、个人开关、启用时治理闸、停用置 `active=0`、
  > 轮换绕过闸、启动列表治理过滤（经直改行使其成为独立可测闸）、解除限制不自动恢复、租户级额度。
  > 唯一需调整的既有用例是实例表列清单断言（新增两列，属预期）。
- [x] 2.6 登记五类个人页面，并为新建及存量内置 member/tenant_admin 幂等补入新个人菜单 grant，不修改旧管理 grant 或自定义角色。
  > **已完成（2026-09-14）**，但实现口径经确认后调整过，见下。
  >
  > **击中一个规范内在冲突**：`auth/service.py` 的兼容规则是「角色持有 ≥1 条 `menu` grant 即绑定该集合」，
  > 而内置 `member`/`tenant_admin` 原本**没有任何** menu grant（只受功能权限约束）。
  > 若按字面「只补 5 条新个人页面」，成员会瞬间进入受限模式，`会话历史/知识库/我的待办/智能体工作台` 等
  > 当前可达页面**全部消失**；`tenant_admin` 更会连带丢掉 `admin.agents/skills/channels/memory`。
  > 这与本 change 自身「关闭新能力 SHALL 保留已有独立能力」相矛盾。已就此请示并确认口径。
  >
  > **采纳口径（零可见性回归）**：默认集合 = 该内置角色**迁移前已可达**的页面 + 5 个新个人页面。
  > 门禁启用后无人丢失任何页面，也未放宽任何原本不可达的页面。据此同步修正了
  > `specs/console-navigation-availability` 与 `design.md` D1 中「只补新页面」的表述（原措辞会引起上述回归）。
  >
  > 实现：
  > - `auth/policy.py`：`PERSONAL_CONSOLE_PAGES`（5 个 id）与 `BUILTIN_MENU_DEFAULTS`（member 11 条 / tenant_admin 16 条）。
  >   放在无依赖的 policy 模块，因为 service 与 store 都要消费，而 store 不能反向 import service（环形）。
  > - `_SIGNED_CONSOLE_PAGES` 登记 5 个页面，`scope` 一律 `self`；4 个挂载成员已具备的读权限，
  >   `personal.channels` 因暂无对应成员功能权限而留空、由个人渠道消费者开关控制（分阶段开放）。
  > - 新建租户：`_seed_tenant_defaults` 写入同一份默认集合。
  > - 存量迁移：`_migration_20` 仅对 `builtin=1` 且 code ∈ {member, tenant_admin} 的角色，
  >   以 `INSERT ... WHERE NOT EXISTS` 补入；幂等、不覆盖已有 grant、不重置被显式移除的授权、
  >   不读也不写任何非 menu grant、完全不触碰自定义角色。
  >
  > 验证：`tests/test_personal_console_menu.py` 22 项全绿（含零回归超集断言、
  > 「默认 grant 是页面开放的必要条件」的迁移前后对照、迁移后显式移除不会被重新补回）。
  > `test_menu_grant_enforcement.py` 等 218 项既有菜单/导航用例无需修改即全绿；
  > 前端 4 个相关 cjs 通过（`test_sidebar_account_frontend.cjs` 的失败属其他进行中变更，
  > 本次未改动任何 `.js/.cjs/.html`）。`openspec validate --strict` 通过。
  > 变异验证：「幂等补入」与「新建租户写入默认集合」两处去掉即有用例失败；
  > 「不触碰自定义角色」由 SQL 的 `builtin=1`、`code IN (...)` 与按 code 查默认表**三重**独立保证，
  > 单点变异不会失败（属刻意冗余），三处同时削弱后用例即失败，确认断言非空转。
- [x] 2.7 完成真实临时身份库上的迁移、重复迁移、中断恢复、唯一约束和双请求配额竞争测试，形成进入下一阶段的证据。
  > **已完成（2026-09-14）**，证据见 `evidence/2-7-stage2-drill-evidence.md`；测试 17 项全绿
  > （`tests/test_stage2_migration_and_quota_evidence.py`）。
  >
  > 覆盖：迁移在真实临时库落地、同进程重复打开不变、中断（真实写入后抛异常）不记录版本且不留部分行、
  > 重试后完整且无重复、阶段二唯一约束由数据库层强制、双请求配额竞争不超发。
  >
  > **关闭门槛 Q1 的关键发现**：仅有「结果类」竞争测试不足以证明机制——
  > 把 `_tx()` 的 `BEGIN IMMEDIATE` 换成延迟 `BEGIN`，结果类测试**仍通过**（两线程未被强制交错）；
  > 而把限额判定去掉则会被发现。故补做确定性机制测试：持锁方 `BEGIN IMMEDIATE` 后只读不写，
  > 第二连接必须拿不到锁；对照的延迟 `BEGIN` 只读不写则不持锁。该测试经判别力验证
  > （持锁方改为延迟 `BEGIN` 时失败）。**Q1 据此解除，2.5 可独立落地。**
  >
  > 连带修正：`test_identity_service_writes.py` 中一处用裸 SQL 删除内置 `tenant_admin` 角色的夹具
  > 因新增外键（grant → role）而失败；生产路径不受影响（`delete_role` 拒绝删内置角色，
  > 自定义角色先清 grant 再删），已按同一顺序修正夹具。

## 3. 统一授权与私有数据边界

- [x] 3.1 在统一授权服务中加入本人私有 Agent 的编辑、启停及适用删除授权，保持已有读用逻辑；不对成员全局补发公共维护权限。

  > 落地：`PRIVATE_AGENT_OWNER_ACTIONS` 扩到 `read/use/edit/enable`，owner 对自己的私有 Agent 无需
  > 手写 `agent:<id>` grant 即可编辑/启停；`MEMBER_DEFAULT_PERMISSIONS` 保持不含 `agent.enable`
  > （不给成员全局补发公共维护权限），owner 的 `enable` 由 `PRIVATE_AGENT_OWNER_EXEMPT_ACTIONS`
  > 单独豁免功能权限，作用域仅限该对象。`resource_ids_for` 为 `edit`/`enable` 同步并入本人私有 id。
  > 证据：`tests/test_private_agent_owner_actions.py`（20 例，含"成员角色仍无 agent.enable"
  > 与"授权不外溢到共享 Agent"两处反向断言）。
  >
  > 反转旧契约：`test_private_agent_owner_reachability.py` 中"owner 无 grant 不能 edit/enable"
  > 4 例按 3.1 改为断言允许（并保留"他人仍被拒"用例）。

- [x] 3.2 将私有归属拒绝放在管理员旁路前，统一列表、对象详情、会话/runs、记忆和文件路径的判断。

  > 落地：`check_resource_action` 先做私有归属判定再走 `all` 旁路——非 owner（含平台管理员）对私有
  > Agent 一律 False；`resource_ids_for` 对管理员仍返回全量视图，但私有对象不经由该函数放行。Web 层
  > 新增 `_private_agent_owned_by_another`，并置于 `_require_agent_action` 与
  > `_knowledge_write_authorized` 的管理员短路之前，覆盖控制台实际调用的对象详情/知识写入口。
  > 证据：`tests/test_private_agent_owner_actions.py`（`PrivateContentPrecedesAdminBypassTests`）
  > 与 `tests/test_private_agent_web_gate_ordering.py`（11 例）。
  >
  > 反转旧契约：`test_knowledge_console_database.py` 中"tenant_admin 可写成员私有 own 知识库"
  > 改为断言 403，并补一条"owner 本人可写"作为对照（拒绝源于归属，而非对该库一刀切）。
  >
  > 待续：会话/runs、记忆与文件路径的同类判定（3.4/3.6）与治理元数据投影（3.3）。
- [x] 3.3 区分私有内容与治理元数据；实现管理员按政策停用个人接入的单独动作及脱敏审计。

  > 落地：治理动作与脱敏审计在 2.5 已实现（`set_personal_instance_governance`，audit 只记
  > `disabled`/`reason`，不含渠道类型、显示名、owner 身份、凭据）。本任务补齐第三块——
  > **可发现性读**：`list_personal_channel_instances_for_governance` 仅返回治理元数据
  > （id / channel_type / owner_user_id / active / governance_disabled*），显式排除
  > `display_name`、`agent_id`、凭据与参数，因此"按政策治理"不会变成"读取每个成员的私有配置"。
  > 同源约束：治理停用后仍可改普通元数据，但**凭据轮换与启用被拒**（`governance_disabled`），
  > 即停用不能被 owner 或管理员用来恢复服务。
  > 证据：`tests/test_personal_agent_governance_metadata.py`（12 例，含逐字段禁止断言与
  > "投影是选择而非数据缺失"对照）。
  >
  > 待续：桌面端/控制台治理视图（阶段 5/7）复用该投影。
- [x] 3.4 收紧工作区、绝对/相对路径、共享根嵌套、下载与预览消费的实际 owner 校验，使旧私有令牌不能绕过新边界。

  > 落地：找到并移除了**最后一个管理员旁路**——`_db_path_owner_forbidden` 对 `tenant_admin`
  > 一律 `return False`（即"可读"）。该函数是文件/记忆/知识/工作区所有路径判定的唯一汇合点，
  > 因此一处删除即统一收紧了「工作区、绝对/相对路径、共享根嵌套、下载」全部入口；删除依据是
  > 平台文件根规范中「`tenant_admin` 不因管理资格取得文件内容，仅在治理接口获得脱敏元数据」。
  >
  > 补齐 `/preview` **消费期** owner 复检：能力令牌原先就是完整授权，一旦签发即长期有效，
  > 旧私有链接可跨归属变更继续读取。新增 `_static_path_private_owner`（用**静态**工作区登记
  > 解析路径归属，不依赖请求身份，故匿名 iframe 仍可用）与 `_preview_consumer_may_read`
  > （私有路径额外要求当前会话即属主）。公共工作区文件保持不需要身份。
  >
  > 反转旧契约 2 处：`test_private_agent_file_scope.py` 的"owner 与 tenant_admin 均可读私有 Agent"
  > 改为断言管理员被拒 + 补"绝对路径同样被拒"；`test_user_personal_agent_provisioning.py` 的
  > "仅属主与 tenant_admin 可达"改为"仅属主"，并补"共享 Agent 无属主门槛"对照。
  > 证据：`tests/test_private_agent_file_scope.py` 新增 `PrivatePreviewConsumptionTests` 6 项
  > （含"解除归属后 URL 对被拒者重新开放"证明是**再推导**而非令牌内固化）。
  >
  > 4 处变异全部被捕获（恢复旁路、移除预览复检、预览接受任意会话、不可解析身份即放行）。

- [x] 3.6 通过真实 handler 与文件读写链路验证所有者允许、管理员私有内容拒绝、共享资源既有授权保留及身份库故障拒绝，作为私有维护开放门槛。

  > 证据见 `evidence/3-6-private-maintenance-acceptance.md`；用例
  > `tests/test_private_resource_acceptance.py`（13 项）。
  >
  > 经真实 app 打通四个入口（`workspace/read`、`workspace/write`、`/api/file`、
  > `/preview`）验证四项：属主读**写**放行；管理员四种入口全拒且磁盘内容逐字节未变；
  > 共享资源既有授权不变（含匿名预览仍可用、解除归属后立即恢复）；身份库故障**拒绝**。
  >
  > **本门槛抓出一处真实缺陷**：`_static_path_private_owner` 吞掉身份库异常并返回 `None`，
  > 而 `None` 语义为"非私有"——故障期间私有文件被当作公开文件，预览链路直接吐出正文。
  > 已改为抛出 `_PrivateOwnerLookupFailed` 并 fail closed（"未知"≠"不可达"）。
  >
  > 故障注入点经专门断言（`test_the_outage_is_actually_exercised`）确认真的被触发——
  > 否则管理员本就因"非属主"被拒，测试会假通过。
  > 5 处变异全部被捕获。
  >
  > 未覆盖并留待对应阶段：控制台 UI 编辑交互（阶段 8）、记忆/知识同类链路（3.5、阶段 5）、
  > 真实渠道入站本人路由（阶段 7）。

- [x] 3.5 与知识写权限 change 合并私有知识 owner 优先检查及 can_write_knowledge 投影，保留其公共写规则和权限目录迁移。

  > 落地：owner 优先检查在 3.2 已接入 `_knowledge_write_authorized`（先 `_private_agent_owned_by_another`
  > 再管理员短路）；3.4 移除 `_db_path_owner_forbidden` 的 `tenant_admin` 旁路后，知识**读取**
  > （list/read/graph 都经 `_require_private_owner`）也同步收紧，满足
  > 「非 owner 的 tenant_admin 与平台管理员 MUST NOT 读取私有知识」。
  >
  > 本任务补齐规范新增的**投影一致性**要求：`can_write_knowledge` 与实际写入路径同源
  > （`_tenant_agents_admin_projection` 直接调用 `_knowledge_write_authorized`），
  > 因此「公共写资格不会投影为所有私有对象可写」。
  > 新增 `AdminKnowledgeWriteProjectionTests`（4 项）：租户管理员视图中成员私有对象
  > `can_write_knowledge=False`、同视图共享对象仍为 True（**非空转**对照）、
  > 逐对象与写入路径结论一致、属主本人自有库仍为 True。
  >
  > 保留既有公共写规则与权限目录迁移：`test_knowledge_console_database.py` 的公共写用例
  > （平台管理员/租户管理员写共享库、`knowledge.write` 已退役的授权忽略用例）全部保持通过，
  > 未改动 `auth/policy.py` 的权限目录或任何迁移号。
  >
  > 2 处变异被捕获（移除 owner 优先检查、管理员短路前移）。

- [x] 3.6 通过真实 handler 与文件读写链路验证所有者允许、管理员私有内容拒绝、共享资源既有授权保留及身份库故障拒绝，作为私有维护开放门槛。

## 4. 私有智能体生命周期

- [x] 4.1 实现成员自建入口，服务端固定 tenant/owner 并拒绝伪造归属或 private-to-shared 写入。

  > 落地：新增 `agent/private_agent.py` 的 `PrivateAgentService.create_private_agent`。
  > **接口形状即控制**：没有 `owner_user_id`、没有 `scope` 参数，tenant 与 owner 只从可信
  > 调用上下文取；请求若**尝试**指定 owner 或要求共享作用域，在**任何写入之前**直接拒绝
  > （`requested_owner_user_id` / `requested_scope` 只用于被拒绝）。
  > 成员资格每次从存储现读（非信任会话缓存）；跨租户成员、失效成员一律 403。
  >
  > 拒绝式校验（而非忽略）是刻意的：忽略会让调用方仍以为它选择了 owner，而这正是拒绝要
  > 防的认知；会发这两个字段的调用方，要么困惑、要么在试探。
  > 用例 `tests/test_private_agent_lifecycle.py`（20 项）。6 处变异全部被捕获。

- [~] 4.2 复用安全工作区初始化、获权模板筛选和资源范围检查，实现幂等创建与失败补偿，不复制模板的私有运行数据或凭证。

  > **部分完成**：模板筛选（按调用者实际 `agent:use` 授权，他人私有 Agent 与未绑定对象拒绝）、
  > 重试幂等（绑定前崩溃的孤儿 roster 条目被**采用**而非重复 clone）、失败补偿（绑定失败即
  > 撤销 roster 条目与工作区，且只删 `agents/<id>` 这类自建布局）均已落地并变异验证。
  > 未复制模板私有运行数据/凭证：走 `clone_agent`，不复制会话、记忆、运行记录与凭证。
  >
  > **未完成**：原子配额校验（个人/租户额度）与并发创建竞争。配额需要为私有 Agent 定义
  > 计量口径（现有 `_QUOTA_METRICS` 只有 tokens/tool_calls/messages/storage_bytes，
  > 不适用于「对象个数」），属需要独立设计的切片，不在本轮内注入猜测性机制。

- [x] 4.3 修正系统助理供应幂等条件，支持已有自建私有智能体的成员仍获得专属助理，且自建不覆盖个人或租户默认登记。

  > 落地：2.2 已把幂等口径改为**按来源判定**（`SUPPLIED_ASSISTANT_ORIGINS`），4.1 的自建
  > 记录 `origin='user_created'`，两者互补。本轮补齐双向前提的固定证据：
  > 只有自建对象的成员 `owned_agent_id` 为 `None`（仍会获得专属助理）；
  > 已有专属助理的成员自建第二个对象后，`resolved_default_agent_id` 仍为专属助理、
  > `tenant_default_agent_id` 不变（自建不移动任何默认登记）。
  > 另固定「成员不能删除系统供应助理」（`origin` 非 `user_created` 即拒绝）。
- [x] 4.4 接入本人配置、核心文件编辑、启停和调试，保存与实际资源分发均重验模型/工具/技能授权。

  > 落地：所有者经既有 `/api/agents`（update：配置/启停）、`/api/agents/<id>/files/<name>`
  > （核心文件读写）入口即可维护本人私有对象——3.1/3.2 的 owner 优先授权已使 `edit`/`enable`
  > 生效，本轮以真实 handler 用例固定（`OwnerMaintenanceTests`）。
  >
  > **新增保存侧重验**：`web_channel._require_configured_capabilities` 在 update 分支于
  > roster 写入前调用，对请求**实际命名**的 `model`/`skills`/`tools_allowlist` 逐项按调用者
  > 当前资源授权重验，未授权即 403 并在消息中点名资源。只判本次请求给出的字段，因此模板克隆
  > 带来的、所有者本就不能用的既有资产不会让无关保存变成拒绝。
  > 匹配用资源 id 的尾段（`builtin:` / `mcp:<conn>:` / `provider:<pid>:`），不臆测名字来自哪里。
  > 仅作用于**调用者本人的私有对象**：他人私有对象先拒绝（3.2），共享对象仍以显式 `agent.edit`
  > grant 为准；管理员与 legacy 与运行期同样不受限。
  >
  > **运行期重验**复用既有链路而非新增：工具逐次调用经
  > `agent_stream._resource_tool_denial`（`tool.execute` + 资源 grant，含 tenant_admin 与记忆工具豁免），
  > 模型在发送时经 `_require_model_use`。4.6 以 owner 私有对象补集成证据。
  >
  > 用例 `tests/test_private_agent_capability_save.py`（19 项）；7 处变异全部被捕获。
- [x] 4.5 实现自建对象删除及引用/运行冲突处理；拒绝成员删除系统助理、共享对象和他人对象。

  > 落地：`delete_private_agent` 在归属（本人）与来源（`origin='user_created'`）两道判定**之后**
  > 增加依赖冲突闸门，返回 `code='conflict'`/409 并**逐条点名**依赖：
  > 活动渠道实例（`IdentityService.channel_instances_referencing_agent`，两种 scope 都算，
  > 仅统计 `active=1` 的实时路由）与运行中实例（`AgentBridge.has_live_agent`）。
  > 冲突在 roster 写入**之前**抛出，失败不留半成品。
  > 「查不到」按冲突处理（fail closed）：渠道库读取异常、运行探针异常都不当作「无依赖」；
  > 探针可注入（`runtime_probe`），默认询问进程内 Bridge。
  >
  > **修正一处真实缺陷**：原实现只删 roster 条目、不释放 `agent_bindings`，而读取路径
  > 故意把 roster 未知的 Agent 视为可用，遗留绑定会让已删除对象继续被解析。现按
  > `release_deleted_agent` 先解除绑定/清空租户与个人默认，再删 roster——两存储无法同事务，
  > 该顺序留下的是「不可达的可恢复孤儿」而非「看似存在的幽灵」。
  >
  > 用例 `tests/test_private_agent_delete_conflicts.py`（12 项）；8 处变异全部被捕获。
- [x] 4.6 覆盖并发创建、重试、配置版本冲突、供应补偿、停用不自动复活及运行中撤权的集成测试。

  > 落地：新增 `tests/test_private_agent_integration.py`（6 项），把此前分散在单元层的性质
  > 放到**真实组件接缝**上验证：
  >
  > - **并发创建**：两线程共享一个真实身份库与一个线程安全的 roster，额度仅 1 时恰一个成功、
  >   另一个得到 `quota_exceeded`；终态以**存储中的绑定数**为准，而不是以调用返回为准。
  > - **配置版本冲突**：驱动真实 `AgentsHandler.POST` + 真实 `AgentAdminService`，
  >   同一旧 revision 的两次保存只有一次落库，后到者得到 `code='stale_roster'`/409 且**未覆盖**；
  >   刷新 revision 后第二次可成功（证明拒绝的是陈旧性，不是这次编辑）。
  > - **停用不自动复活**：个人默认指向已停用对象时解析跳过它并给出可用的共享对象，
  >   既不复活该对象（roster `enabled` 仍为 False），也不抹掉成员偏好（重新启用即可恢复）。
  > - **运行中撤权**：真实 `AgentStreamExecutor` + 真实身份库，先放行已授权工具调用，
  >   删除角色授予后**下一次**调用即拒绝并点名工具——不沿用运行开始时的权限快照。
  >
  > 重试/绑定前补偿/原子配额与并发锁已分别在 4.1/4.2 的用例中固定（`test_private_agent_lifecycle.py`、
  > `test_private_agent_quota.py` 的 `LockMechanismTests`），本任务不重复而是补跨组件视角。
  >
  > 「停用后该对象不再接受新任务」的运行期部分由 `tests/test_default_agent_fail_closed.py`
  > 已固定的 fail-closed 解析规则承载，未在本文件重测。

## 5. 个人记忆管理

- [x] 5.1 实现当前 tenant/user 的记忆列表、读取、编辑、删除和清空，并与选中私有 Agent 记忆区分入口和存储作用域。

  > 落地：`agent/memory/personal.py` 的 `PersonalMemoryService`（list/read/save/delete/clear），
  > 入口 `PersonalMemoryHandler` / `PersonalMemoryContentHandler`，路由
  > `/api/memory/personal`、`/api/memory/personal/content`（策略 `personal`）。
  > 「我的记忆」解析到 `state_dir.user_root()`（当前租户共享根下 `users/<user_id>`），
  > 「该私有 Agent 记忆」仍是 `MemoryService(workspace_root)` 的 Agent 工作区读取；
  > 本入口不接受 `agent_id`，两者不是同一地址空间（`ListingTests` 同时固定了两侧）。

- [x] 5.2 将相对标识、真实路径归属和版本校验接入写操作；拒绝跨用户、跨租户及共享记忆误写。

  > 落地：入口只接受 `MEMORY.md` / `memory/<name>.md` 两种相对标识，归属来自
  > `_require_scope()`（可信租户 + 用户，缺失即 403 `no_identity`），解析结果恒在调用者
  > 自己的用户域内 —— 他人的个人记忆没有可构造的地址（路径形状/绝对路径/嵌套目录/他人
  > 用户段全部 `invalid_entry`）。保存与删除携带 revision，冲突返回 409 `stale_revision`
  > 且不覆盖新内容；写路径不触及共享 Agent 记忆与租户知识。

- [x] 5.3 实现内容与索引修改的恢复记录，保证已删除内容不从检索旧索引返回；失败不显示保存或清空成功。

  > 落地：保存后定向重建该条目在**每个已知 Agent 索引库**中的行（跨智能体可见）；删除/清空
  > 对同样范围执行 `delete_by_path`。任一失败不算成功：返回 `index_state="pending"`、
  > HTTP 层 `status="pending"`/`code="index_pending"`（不是 success），标签写入作用域标记的
  > `pending_index`，并由 `MemoryManager.search` 在结果出口减掉这些标签 —— 即使索引行尚存，
  > 已删除正文也不会返回。`retry_index` 重试并只影响目标标签。

- [x] 5.4 为清空接入作用域版本或等效失效机制，拒绝清空前排队任务凭旧状态回写；保留清空后新任务的正常固化。

  > 落地：`<user_root>/.memory-scope.json` 的 `generation` 在清空时**先**递增，再删除内容。
  > `flush_from_messages` 在派发时捕获 generation 并带入后台 worker，
  > `write_daily_summary` 写入前复核：版本过期即拒绝写回（返回 False 并记日志），
  > 清空后的新任务按当前版本正常落盘；同步写入（/compact）没有可陈旧的版本，保持原行为。

- [x] 5.5 通过跨智能体读取、同账号跨租户隔离、管理员拒绝、并发编辑、索引失败与旧任务回写测试，形成记忆写入启用证据。

  > 落地：`tests/test_personal_memory_console.py`（30 项全绿）+ 证据
  > `evidence/5-5-personal-memory-evidence.md`。覆盖跨智能体检索、同账号跨租户为空、
  > 平台/租户管理员看不到他人正文、同一版本两次并发保存恰一个成功、索引清理失败仍不返回
  > 已删除正文且如实报告 pending、清空前排队任务回写被拒。8 项变异检查全部被捕获。

## 6. 成员个人渠道接入

- [x] 6.1 新增受登记的个人渠道 API，复用实例服务的版本/审计/凭证事务，固定当前 tenant/owner；原公共接口维持原管理员门槛。

  > 落地：`auth/service.py` 的 `list/get/create/update/set_active/revoke_personal_channel_credentials`，
  > 通过 `allow_owner=True` 复用 `create/update/set_tenant_channel_instance_active` 的既有事务，
  > 只把「谁有权写这一行」换成 `_personal_instance_row`/活跃成员校验；`scope, owner_user_id`
  > 由服务端固定为 `("user", actor)`，请求体不接受这两个字段。入口
  > `channel/web/web_channel.py::PersonalChannelHandler`/`PersonalChannelInstanceHandler`，
  > 路由 `/api/personal/channels[/<id>]`（策略 `personal`），公共路由策略未改。
- [x] 6.2 实现本人凭证配置、更换和撤销，所有管理投影仅给掩码；检验旧版本及跨 owner 的解密/使用拒绝。

  > 落地：三个投影只回字段名与版本号（`_personal_channel_projection`/`_personal_credential_projection`/
  > `_personal_binding_projection`，subject 只回后 4 位）；轮换追加 `credential_versions` 且只保留
  > 一行凭证；`expected_version` 不符即在写前中止；撤销是 `active=0` + 实例停用，同事务提交。
- [x] 6.3 为已支持多实例的渠道登记个人接入就绪状态，拒绝未就绪类型，并检测个人/公共作用域的外部应用连接冲突。

  > 落地：`channel/channel_instances.py` 的 `PERSONAL_READY_CHANNEL_TYPES`/`personal_channel_ready`/
  > `personal_channel_types`（配置只能收窄）；`app_identity` + `auth/crypto.py::fingerprint` 做
  > keyed digest；`_assert_no_app_conflict` 在 create/update/enable 三处判决，`_migration_23` 的
  > `idx_tenant_channel_instances_app` 兜并发。判决刻意不对称：个人对全部活动实例让路，公共只对
  > 个人实例让路（两个公共实例共用应用是上游既有行为）。
- [x] 6.4 实现自助绑定挑战、提供方证明校验、单次消费、限次/过期处理和三元组唯一冲突；不能以昵称或手填 subject 直接建绑。

  > 落地：`start_personal_channel_binding`（只回码一次）→ 本人私聊发码 →
  > `redeem_personal_channel_challenge`（三元组来自入站观察，浏览器无法指定）。
  > 单次消费由 `BEGIN IMMEDIATE` 判读 + `UPDATE … WHERE consumed_at IS NULL` 两层保证；
  > 错码消耗次数、`CHALLENGE_MAX_ATTEMPTS` 后锁定、过期 410；他人已绑三元组 conflict 且不被覆盖。
- [x] 6.5 实现本人当前实例身份关联及解绑，验证其他租户的既有映射和接入保持不变。

  > 落地：`link_personal_channel`/`unlink_personal_channel(_instance)`；解绑只删
  > `(tenant, user, instance)` 路由，`external_identities` 全局映射保留。用例同时覆盖
  > 同人跨租户两条路由与「解绑后发送者不再解析」。
- [x] 6.6 调整未绑定尝试采集与管理列表，个人接入及挑战消息不保存或展示私聊预览、挑战码和凭证。

  > 落地：`record_external_identity_attempt(personal_flow=…)` 由
  > `channel/external_identity.py::looks_like_binding_code` 在入站侧置位；写入侧按
  > `personal_flow` 或 `_instance_is_personal` 丢弃昵称/预览，`list_external_identity_attempts`
  > 读取时再按实例归属清空一次，历史行同样不显示。
- [x] 6.7 实现实例保存与实际连接状态分离、重连失败诊断、治理停用优先和 owner 明确重新启用。

  > 落地：保存提交后再应用运行时，返回 `{applied, pending, error}`，`saved` 不谎报 `connected`；
  > 失败可诊断（`apply_tenant_instance_runtime` 的回执）。启用路径在事务内重判治理停用与
  > 个人策略（`_enforce_personal_instance_policy`）并重判应用占用，治理停用始终优先。
- [x] 6.8 完成真实身份/凭证服务的集成验收：并发版本冲突、审计失败回滚、应用占用、配额竞争、重放、伪造 owner 与解绑失效。

  > 落地：`tests/test_personal_channel_console.py`（54 项）+ `tests/test_personal_channel_binding.py`（31 项）
  > + 证据 `evidence/6-8-personal-channel-evidence.md`；相邻回归 454 项全绿。
  > 8 项变异检查全部被捕获；其中应用占用与挑战单次消费是双保险，需同时破坏两层才暴露，
  > 因此 6.8 补了同码并发用例 `test_one_code_arriving_twice_at_once_links_only_its_sender`。

## 7. 个人渠道执行

- [x] 7.1 在入站路径验证实例 tenant/scope、发送者证明、有效成员、本人关联与目标 owner，个人路由无效时不回退公共/平台默认或服务身份。

  > 落地：`IdentityService.resolve_personal_channel_inbound`（唯一决策点，返回
  > `{allowed, reason, owner_user_id, agent_id}`，从不抛异常）+
  > `channel/chat_channel.py::_preflight_personal_inbound` / `_serve_personal_route` /
  > `_scope_to_member`。逐条重验：实例 tenant 取自**实例行**（非发送者）、scope=user、
  > 治理停用、群聊、凭证有效、本人关联、发送者三元组一致、成员有效、目标为本人私有 Agent
  > 且本人仍有 `agent.use`。拒绝时只发固定通知，绝不绑定 `runtime_identity`。
  > 证据：`tests/test_personal_channel_inbound.py`（38 项）。
- [x] 7.2 将公共渠道绑定限制为同租户公共智能体，添加经验证本人私聊的显式个人路由；群聊不进入私有智能体或注入个人记忆。

  > 落地：公共实例写入路径 `_require_public_instance_agent` 只接受同租户**公共**智能体；
  > 入站缺省值改用新增的 `resolved_public_default_agent_id`（仅同租户公共智能体，无则拒绝，
  > 不回退全局默认/他人私有 Agent）。个人路由经 `resolve_shared_personal_route` 在共享实例上
  > 按发送者三元组解析，命中即用本人私有 Agent，无效时**拒绝而非**回退到共享 Agent；
  > 群聊两条路径都直接拒绝，不注入个人记忆。
- [x] 7.3 使成员停用、渠道治理停用、凭证撤销、身份解绑及资源撤权在后续消息和调用前生效，并关闭对应失效连接。

  > 落地：`_reconcile_personal_runtime` 接入 `set_personal_instance_governance`、
  > `revoke_personal_channel_credentials`、`unlink_personal_channel_instance`、
  > `update_member`（停用成员时遍历其全部个人实例）与 `delete_external_identity`（同事务
  > 清理关联）；`channel_instances.apply_tenant_instance_runtime` 对 user 作用域实例新增
  > `_owner_is_active_member` 检查。上述事实在每条消息上重新推导，因此第二次提问即被拒绝。
- [x] 7.4 通过现有 Bridge/工具分发的真实身份链路验证 owner 私聊成功、他人/群聊/跨租户拒绝及缺权无副作用；替身测试仅计为组件证据。

  > 落地：`test_the_owners_run_reaches_the_real_tool_dispatch_as_the_owner` 用
  > `ChatChannel._identity_for` + `AgentStreamExecutor._resource_tool_denial` 真实链路断言
  > 被认证的成员就是工具将运行的成员；`test_a_refused_message_never_yields_a_dispatchable_identity`
  > 断言拒绝后无身份可分发（fail-closed）。仅对厂商传输与 Agent 对象构造打桩，
  > 故本项只登记为**组件证据**，生产能力仍以 7.5 的开关为准。
- [x] 7.5 对至少一种实际支持的渠道完成受控端到端接入与消息验收；未提供真实渠道条件时保持个人执行开关关闭并准确记录待验收范围。

  > 落地（未验收分支）：`channel/channel_instances.py` 的
  > `PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 出厂为空
  > frozenset，运行期与请求期双重生效（`personal_runtime_enabled` 在
  > `apply_tenant_instance_runtime` 与 `resolve_personal_channel_inbound` 各判一次）。
  > 待验收范围：飞书 / 企业微信 / 钉钉个人接入真机接入与消息投递；验收通过前不改变默认值。


## 8. 个人控制台与工具技能目录

- [x] 8.1 为五类个人页面生成权威 console_pages 和对象动作，分开读取、配置与执行状态；旧 admin 页面标识保留原含义。
- [x] 8.2 复用已有控制台壳与编辑组件，呈现个人渠道、私有智能体、个人记忆和已授权目录；明确当前租户及本人作用域。
- [x] 8.3 目录、搜索、总数和分页先按资源授权过滤；个人参数保存只写本人配置，不改公共安装、正文、启停或凭证。
- [x] 8.4 处理侧栏、工作台快捷入口、直接 URL、未保存输入、租户切换、403/503 和迟到响应；拒绝页面不启动被拒绝消费者。
- [x] 8.5 更新简体、繁体、英文文案及相关快照，避免以“系统管理权限”描述成员个人能力。
- [x] 8.6 完成默认 member、多个角色并集、显式菜单限制、目录可读但执行关闭、直接 API 越权及桌面/窄屏交互测试。

  > 证据：`evidence/8-personal-console-evidence.md`（19 项验收矩阵 + 真实 Chromium
  > 桌面/窄屏 10 场景；浏览器验证发现并修复了「渠道动作名不一致」与
  > 「惰性容器不激活」两个真实运行缺陷；个人渠道真机执行仍按任务 7.5 保持关闭）。

## 9. 分阶段启用与交付

- [x] 9.1 登记设计中的独立能力开关，接入后端入口、消费者启动与权威投影，保证关闭新能力不会关闭已验收目录或撤去 owner 检查。

  > 开关登记在 `auth/policy.py`（名字、出箱默认值、页面依赖），取值走既有配置目录
  > （`config.py` 五个键，缺省或不可读时回落默认）。后端入口：自建私有智能体、个人渠道
  > 新建/编辑/重新启用/发起绑定挑战、个人参数保存、新增记忆内容按 `capability_disabled`
  > 拒绝；撤销凭据、解绑、停用、删除、清空一律放行，避免把成员困在自己收不回的对象上。
  > 消费者启动：`personal_runtime_enabled` 改为「总开关 + 逐类型验收」双条件，并接入
  > `load_tenant_channel_instances` 启动合成（个人实例在类型未验收或 owner 非有效成员时
  > 不启动）。权威投影在撤回时报告 `capability_disabled` 与依赖开关名，前端据此命名能力
  > 并在侧栏隐藏入口。证据：`evidence/9-1-capability-switches.md`。
- [x] 9.2 逐阶段核对前置切片的实际证据；私有知识与在途知识权限变更完成合并后才开放对应写路径，个人执行必须完成实际渠道验收。

  > 复核见 `evidence/9-2-prerequisite-evidence.md`（1.3 的时点快照在交付时点重做）。
  >
  > 四条登记门槛：Q1 配额并发竞争已有独立用例（双请求原子配额 / 用户桶竞争）→ 解除；
  > Q3 个人渠道语境的隔离已由阶段 7 的入站路径验证覆盖；Q4 依赖的
  > `restrict-knowledge-write-authorization` **已归档合并**（全任务勾选、`_migration_15` 落地、
  > 权限目录已退役 `knowledge.write`、其 6.3 留有真实浏览器验收），故本 change 的私有知识写
  > 路径允许开放；Q2 `action-approval` 仍无真实消费者 → 不开放任何需要审批的能力，而设计把
  > 「适用审批」列为个人渠道**执行**门槛，执行本身已关闭，因此不阻塞已开启开关。
  >
  > 个人执行：`PERSONAL_RUNTIME_ACCEPTED_TYPES` / `PUBLIC_PERSONAL_INGRESS_TYPES` 仍为空集，
  > `personal_channel_runtime` 工厂态关闭 —— 以「关闭 + 准确记录待验收范围」结项。
  >
  > 复制运行：个人控制台/私有维护/能力开关 8 文件 150 passed；记忆/渠道/知识 10 文件 284 passed。
- [x] 9.3 完成双租户多用户完整授权回归、现有公共渠道/目录回归及新增方法级路由基线检查；不以模拟界面证明生产能力已开放。

  > 证据：`evidence/9-3-regression-and-route-baseline.md`。
  >
  > 新增 `tests/test_personal_console_multi_tenant_authorization.py`（16 项）：tenant A（acme，
  > 平台管理员 root）+ tenant B（beta，真实租户管理员），成员 alice/bob/carol 交叉验证
  > 个人渠道、私有 Agent、个人记忆、治理投影与公共渠道目录；两处变异（去掉 owner 过滤、
  > 把 owner 短路移到 `all` 之后）**均被捕获**。
  >
  > 公共回归：渠道 11 文件 181 passed + 7 subtests；智能体/目录/RBAC 12 文件 246 passed；
  > `.cjs` 逐文件扫描与 HEAD worktree 对比，唯一失败集合仍是 HEAD 上即存在的 5 个
  > sidebar account 旧断言（本 change 未新增）；真实浏览器契约 11 scenarios 全过、
  > 无 page error、无未预期请求。
  >
  > 路由基线：`scripts/route-baseline.txt` 追加 9 条方法级 `personal` 条目，
  > `tests/test_route_registry.py` 新增 `PersonalConsoleRouteTests`（4 项）钉住方法集合与
  > 「管理面不得变成 personal」；把一条改成 `tenant` 会失败 3 项。
  >
  > 同时记录两处基线既有缺陷（不属本 change）：全仓 `pytest` 单进程收集因 `scenes/config.py`
  > 与顶层 `config.py` 同名冲突而失败（HEAD 104 个错误），以及 5 个 sidebar account 前端用例
  > 的 legacy/unknown 断言过期。`tests/test_console_view_registry.cjs` 由本 change 引入的
  > `_activateViewContainer` 依赖导致的沙箱缺口已修正（6/6）。
- [x] 9.4 演练幂等升级、中断补偿、关闭开关、停止个人连接与相容版本恢复，证明不会恢复旧管理员私有旁路或误启动个人实例。

  > 证据：`evidence/9-4-delivery-drill.md`；用例 `tests/test_personal_delivery_drill.py`（8 项，
  > 同批 87 passed + 4 subtests）。
  >
  > D1 重启零变更（版本序列等于 `migration_versions()`，个人列在位）；D2 迁移失败回滚到
  > 「版本未记、部分 DDL 不在、重试即续做」；D3 个人渠道创建在「密文已写、审计失败」时整笔回滚
  > 且重试如初；D4 私有 Agent 克隆中断不留 binding、不动默认 Agent；D5 关闭四项开关后
  > 目录仍在、owner 检查仍在、成员仍能撤回自有对象；D6 撤下 `personal_channel_runtime` 后
  > `apply_tenant_instance_runtime` 报 `pending` 「已保存未连接」并停旧连接、启动合成不再拉个人实例；
  > D7 一致备份恢复到副本后数据保留、无需补迁移、且不误启动个人实例；D8 直接锚定
  > `_db_path_owner_forbidden`——同伴/声称 `is_tenant_admin`/声称 `is_platform_admin` 一律拒绝。
  >
  > 变异 5 处全部被捕获（运行时闸门、启动合成 scope 分支、迁移提前 commit、实例行提前 commit、
  > 角色化私有读绕过），逐条还原后全绿。运维口径（升级/关开关/停连接/**禁止降级到旧构建**）
  > 已写入证据文件第 8 节。
- [x] 9.5 同步产品规划、实施状态和运维说明，清理相关规范 Purpose 的旧管理员私有读取口径；记录支持渠道、迁移影响及尚未通过的切片。

  > 交付/运维说明：`docs/design/member-personal-console-delivery.md`（可启用范围表、五个开关语义、
  > 支持渠道与「配置就绪 ≠ 执行就绪」、迁移 16–24 的逐版本影响与「无需回填」结论、
  > 升级/撤下/停连接/恢复操作、实施状态与证据索引、未通过切片、与兄弟 change 的共享面登记）。
  >
  > 规范口径同步（旧「管理员可读取成员私有内容」口径 → owner 正文唯一可达、管理员仅治理元数据）：
  > `openspec/specs/user-personal-agent-provisioning/spec.md`（Purpose + 需求 + 场景）、
  > `openspec/specs/tenant-resource-isolation/spec.md`（需求 + 新增「管理员经共享根访问成员私有文件」
  > 场景 + 「私有 Agent 仅属主可读正文」）、
  > `openspec/specs/rbac-authorization/spec.md`（固定个人与共享资源策略：写动作由所有权派生，
  > 不再要求逐资源 grant）、
  > `openspec/specs/agent-chat-launch/spec.md`（「显式私有智能体仍然受保护」）；
  > 变更内 delta 同步改名两处以「管理员可读」命名的场景。
  >
  > 支持渠道：`personal_channel_types()` 与租户渠道共用声明（feishu、dingtalk、qq、telegram、
  > slack、discord、weixin、wecom_bot），但 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 与
  > `PUBLIC_PERSONAL_INGRESS_TYPES` 均为空 → 本交付为「配置可保存、连接不开放」。
  >
  > 未通过切片：个人渠道真实执行（无类型完成端到端验收）、动作审批真实消费方（9.2 Q2）、
  > 全仓单进程 `pytest` 收集冲突（HEAD 既有，104 错误）、5 个 sidebar account 旧前端断言（HEAD 既有）。
  >
  > 校验：`openspec validate enable-member-personal-console --strict` 通过；
  > `openspec validate --specs --strict` → 71 passed / 0 failed。
- [x] 9.6 完成 OpenSpec 规格与实施验收记录，按真实完成情况勾选任务并报告可启用范围；未通过的阶段保持未完成。

  > 证据：`evidence/9-6-final-acceptance.md`；交付/运维说明 `docs/design/member-personal-console-delivery.md`。
  >
  > 校验：`openspec validate enable-member-personal-console --strict` 通过；
  > `openspec validate --specs --strict` → 71 passed / 0 failed。
  >
  > 后端：28 个本 change 直接相关文件一次全量 `.venv/bin/pytest ... -q` → **722 passed, 3 subtests**；
  > 更宽回归数字见 9.3 证据。前端：`test_personal_console_frontend.cjs` 56/0、
  > `test_console_i18n_parity.cjs` 5/0、`test_console_view_registry.cjs` 6/0、
  > `test_tenant_channel_card_frontend.cjs` 28/0、`test_execution_permission_ui.cjs` 5/0；
  > 真实 Chromium 契约 `test_personal_console_browser.cjs` **11 scenarios passed**（无 page error、
  > 无未预期请求；本机已用测试所用 Playwright 包自身的 CLI 安装匹配的 `chromium-headless-shell`
  > 构建，不再依赖符号链接）。
  >
  > 可启用范围：`member_personal_console`、`user_private_agent_management`、`personal_memory_write`、
  > `personal_channel_onboarding` 四项开启并已验收；`personal_channel_runtime` **保持关闭**。
  > 未通过切片（个人渠道真机执行、动作审批真实消费方、HEAD 既有的全仓 pytest 收集冲突与 5 个
  > sidebar account 旧断言）在第 5 节明确记录，不声明已开放。任务按真实完成情况勾选，
  > `7.5` 勾选的是其「保持开关关闭并记录待验收范围」分支。

