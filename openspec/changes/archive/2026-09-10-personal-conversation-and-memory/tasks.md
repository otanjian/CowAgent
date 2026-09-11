## 1. 失败测试（先写，确认 RED）

- [x] 1.1 新增 `tests/test_user_personal_memory.py`：以真实双用户、同一租户、同一智能体的运行时身份，经记忆写入路径保存 `scope=user` 内容；断言文件落在用户域 `shared_root()/users/<user_id>/` 而非智能体工作区。
- [x] 1.2 断言用户 B 无法检索到 A 的 `scope=user` 内容，且 `scope=shared` 内容双方均可见。
- [x] 1.3 断言同一用户经两个不同智能体写入的个人记忆互相可见（跨智能体一致）。
- [x] 1.4 新增/扩展 `memory_get` 用例：构造路径 `memory/users/<other>/...` 必须被拒绝，`memory/users/<self>/...` 可读。
- [x] 1.5 断言 `load_messages` 按 `owner` 过滤：另一用户的会话上下文不被加载。
- [x] 1.6 新增 `tests/test_comprehensive_chat_entry.py`：租户绑定多个智能体且未配置默认时，缺省 `agent_id` 的会话请求不再返回 `default agent ambiguous`，而是解析到确定性默认。
- [x] 1.7 断言缺省解析不得回退到未绑定当前租户的全局默认智能体（跨租户拒绝）。
- [x] 1.8 前端 `node:test` 用例：可聊智能体 > 1 时进入对话不强制弹出选择流程，默认直接锚定默认智能体。
- [x] 1.9 断言自动固化（溢出 flush / 每日摘要）在有 `user_id` 时归个人私有域，且不覆盖共享记忆文件。（每日摘要已覆盖；溢出 flush 待 4.5/4.8 贯通后补）
- [x] 1.10 断言 legacy（无 `user_id`）路径与变更前一致：路径解析、可见范围、既有会话读取均不变。
- [x] 1.11 运行上述用例，确认在实现前按预期失败（RED）。

## 2. 综合会话入口：默认智能体锚点

- [x] 2.1 `channel/web/web_channel.py`：抽出默认智能体的确定性解析 `_resolve_tenant_default_agent`，供 `_require_tenant_agent_binding`、`_require_session_owner`、`_tenant_default_agent_id` 复用同一实现，消除三处口径分叉。
- [x] 2.2（已修订）解析为**只读**：不按"读取时补齐默认并落审计"实现。确定性顺序已保证结果恒定，写副作用与 `tenants.version` 冲突不值得；显式默认仍由既有管理动作承担。见 `design.md` D2。
- [x] 2.3 缺省解析成功后，使缺省 `agent_id` 的会话请求走租户默认，MUST NOT 触发歧义 403；无任何可解析默认时仍拒绝。
- [x] 2.4 `channel/web/static/js/console.js`：`activeAgentId` 缺省锚定默认智能体，进入对话与"新对话"不再强制选择；`multiAgentMode()` 仅控制"切换/团队"可选入口。
- [x] 2.5 保留显式目标失效语义：显式选择的智能体被归档/删除/不可访问时提示并刷新，MUST NOT 静默替换为默认（本次未改动该分支，既有行为保留）。
- [x] 2.6 回归 `agent-chat-launch` 相关既有用例（含单智能体身份清晰、切换取消、重复点击）。

## 3. 个人人设注入

- [x] 3.1 定义用户域个人档案路径与读取助手（经 `common/state_dir.py`，禁止调用方自行拼接路径）。
- [x] 3.2 `bridge/agent_bridge.py`：新增 `_apply_user_persona_context(agent)`，接在 `_apply_employee_context` 之后，追加到 `extra_system_suffix`。
- [x] 3.3 保障三段（场景/员工/个人）共存与顺序稳定；无档案时不注入空段。
- [x] 3.4 会话归属守卫：仅当会话属于当前用户时注入个人段；共享/团队会话不注入，避免最后访问者覆盖。
- [x] 3.5 断言 `get_agent()` 每轮重新计算，档案更新后下一个请求生效。
- [x] 3.6 测试：个人段与场景/员工段共存、他人会话不含该段、共享会话不含个人段。

## 4. 个人记忆贯通

- [x] 4.1 `common/state_dir.py`：用户域记忆经 `memory_dir()`/`memory_file()`（不传 `base`）解析；`memory_index_db` 保持每智能体一份。
- [x] 4.2 `agent/memory/summarizer.py`：个人主记忆/每日记忆文件根切到用户域（`get_main_memory_file` / `get_today_memory_file` / `ensure_daily_memory_file` / `create_memory_files_if_needed`）。
- [x] 4.3 `agent/memory/manager.py`：`sync()` 额外扫描当前用户的用户域（`user_root()/MEMORY.md` 与 `user_root()/memory/**`），显式标注 `user_id`/`scope=user` 与索引标签，并修正 `relative_to(workspace_dir)` 对工作区外文件的处理（改为显式 rel_path，不再抛错）。
- [x] 4.4 `agent/memory/summarizer.py`：主记忆/每日记忆按用户域写入，保持 `user_id` 归属。
- [x] 4.5 `bridge/agent_initializer.py`：记忆工具构造传入 `user_id`；每日 flush 传入 `user_id`。
- [x] 4.6 `agent/protocol/agent_stream.py`、`agent/protocol/agent.py`：移除幽灵 `_current_user_id`，统一改用 `current_identity().user_id`（溢出 flush 等 5 处读取点）。
- [x] 4.7 `agent/evolution/trigger.py`、`agent/evolution/executor.py`：evolution 与日常摘要贯通 `user_id`。
- [x] 4.8 自动固化默认 `scope=user`：溢出 flush、每日摘要、dream 产物归个人私有域，MUST NOT 覆盖共享 `MEMORY.md`。
- [x] 4.9 `agent/prompt/workspace.py`：提示词加载的 `MEMORY.md` 支持用户域来源，legacy 下与现状一致。
- [x] 4.10 断言 `scope=user` 的 `user_id` 只来自已验证运行时身份，调用方提供的用户标识不得覆盖归属。

## 5. 隔离与安全加固

- [x] 5.1 `agent/tools/memory/memory_get.py`：新增 `_resolve_user_scoped`，拒绝 `memory/users/<other>/...`；本人用户域映射到 `user_root()`，根 `MEMORY.md` 与共享路径按既有语义允许。
- [x] 5.2 审计全部记忆读取路径，确认检索 WHERE 只在既有单一入口构造，新增路径复用而非旁路。
- [x] 5.3 `agent/memory/conversation_store.py`：`load_messages` 在存在已验证 `user_id` 时补 `owner` 过滤，与 `list_sessions` 口径一致。
- [x] 5.4 复核 `agent/permission/isolation.py`：用户域作为合法读写根保持允许，且不因新增用户域而放宽跨租户边界。
- [x] 5.5 双用户双租户验收：跨用户个人记忆不可读、跨租户不可读、共享记忆按既有范围可见。（单租户双用户检索隔离已覆盖；跨租户待补）

## 5b. 默认智能体为租户共享（实现期发现的叠加拦截）

- [x] 5b.1 新增 `tests/test_default_agent_tenant_shared.py`：覆盖"不推定 owner""显式私有仍受保护""显式转共享""任命默认即共享""缺省解析优先共享""幂等校正且不动非默认私有"。
- [x] 5b.2 `_adopt_created_agent_for_tenant` 不再以创建者作为私有 owner，新建智能体绑为租户共享。
- [x] 5b.3 `_appoint_tenant_default_agent` 清空私有归属并审计，保证"任命为默认"不可能留下私有默认。
- [x] 5b.4 新增 `IdentityService.make_agent_tenant_shared`（显式清空 owner，平台/租户管理员门禁 + 审计）；`bind_agent` 只能补写不能清空，无法承担该动作。
- [x] 5b.5 新增幂等校正 `ensure_shared_default_agents()`，只处理每个租户实际可解析为默认的智能体。
- [x] 5b.6 缺省解析改为在 `IdentityService.resolved_default_agent_id` 单点实现（配置默认 → 租户共享中稳定 id 最小 → 其余），Web 层委托调用；优先共享以避免锚定仅私有智能体。
- [x] 5b.7 真实库副本验证：校正前普通成员访问默认智能体 403，校正后 ALLOWED；`updated=2`，第二次运行 `updated=0`。**未对真实库执行写操作。**
- [x] 5b.8 为运维提供执行校正的入口（CLI 子命令或启动时幂等调用），并在真实库上执行；执行前备份。
- [x] 5b.9 复核 `register_default_tenancy` 的 `private_owner_user_id=admin_id` 初始注册路径：确认新装场景是否仍需该参数，或一并改为租户共享。
  - 结论：**保留该参数，但仅对非默认 Agent 生效**。`register` 是"存量单用户安装就地注册到租户"的迁移路径，注册前的全部内容历史上只属于那一个 admin，保留 `private_owner_user_id` 是迁移期的保守可见性选择；把它当成"新装默认共享"的入口会错误扩大历史内容的可见范围。
  - "默认 Agent 恒为租户共享"由紧随其后的 `svc.ensure_shared_default_agents()` 收口（`cli/commands/management.py`，register 命令尾部），因此 `register` 跑完后：非默认 Agent 仍私有给迁移 admin，**被解析为默认的那一个必定 `private_owner_user_id IS NULL`**。
  - 代码注释与 docstring 已写明这一分工（`cli/commands/management.py` 的 register docstring + 第 105-135 行），并有测试覆盖：`tests/test_management_share_default_agents.py`（`register` 后默认共享、`share-default-agents --dry-run` 不改数据）。

## 6. 兼容、迁移与回归

- [x] 6.1 确认无 schema 变更：`chunks.user_id`/`scope` 已存在，本次仅开始实际写入。
- [x] 6.2 确认无数据迁移：既有智能体工作区、会话 ID、消息内容、历史记忆不搬迁、不改写。
- [x] 6.3 legacy 回归：无 `user_id` 时 `user_root()` 坍缩到原状态根，记忆路径与可见范围、既有会话读取与变更前一致。
- [x] 6.4 目标套件回归：`agent-memory-explicit-add-tool`、`tenant-resource-isolation`、`session-history-workbench`、`agent-chat-launch`、会话归属与私有 owner 相关既有用例全绿。
  - 前端 `node:test` 目标套件 13 个文件、**185 pass / 0 fail**：`test_agent_chat_launch_frontend`(5)、`test_identity_admin_frontend`(18)、`test_nav_area_frontend`(5)、`test_sidebar_account_frontend`(41)、`test_channel_scope_nav_frontend`(7)、`test_tenant_channel_frontend`(12)、`test_tenant_create_frontend`(4)、`test_tenant_tabbed_editor_frontend`(63)、`test_workbench_menu_grant_frontend`(9)、`test_tenant_admin_account_picker`(9)、`test_forced_password_gate`(4)、`test_i18n_tenant_channel_keys`(4)、`test_i18n_tenant_editor_keys`(4)。
  - Python 侧：`test_tenant_default_agent` 10/10 全绿（含按新契约改写的 `test_no_agent_multiple_bound_no_default_resolves_deterministically`）。
- [x] 6.5 全量 Python 与前端 `node:test` 套件对比既有基线，确认无与本 change 相关的回归；既有/环境类失败逐条标注。
  - 基线（pristine HEAD，`git stash -u` 后跑全量）：**33 failed / 1929 passed**（测试数 1962）。
  - 本 change（未跟踪的新测试文件纳入）：**32 failed / 2277 passed**（测试数 2309）。
  - 差集核对：**新增回归 0 条** —— 当前 32 条失败是基线 33 条失败的**真子集**（`comm -13` 为空）；**本 change 额外修好 1 条既有失败**：`test_tenant_default_agent.py::TenantDefaultAgentProjectionTests::test_two_tenants_each_mark_their_own_default`。
  - 全量顺序下存在**运行间抖动**（同一源码同一命令，两次运行 36 failed 与 32 failed 之差为 `test_tenant_admin_skills_menu.py` 的 4 条，单跑 7/7 全绿；与记忆/知识套件组合亦全绿），已确认与本次改动无关。
  - 32 条残余失败逐条标注为既有/环境类，与本次改动无关：
    - 既有代码缺陷（基线同样失败）：`test_session_history_search.py`(5)、`test_identity_self_context.py`(2)、`test_read_edit_improvements.py::TestPdfReadingWindow`(3)、`test_claude_thinking.py`(1)、`test_dashscope_provider.py`(1)、`test_consumer_closure_acceptance.py`(1)、`test_tenant_create_containment.py::test_create_without_base_rejected_when_workspace_is_default_root`(1)。
    - 全量顺序下的测试污染类（单独运行全绿）：`test_security_ssrf_browser_navigate.py`(12)、`test_feishu_progress_card.py`(4)、`test_todo_service.py::TodoWebHandlerTests`(2)。
    - 另有一组既有污染已单独核对：`test_memory_global_config.py + test_knowledge_web.py` 组合下 `test_knowledge_web.py` 的 4 条失败，在基线（`agent/` 回退）同样失败。
  - 前端既有/环境类问题（非本 change 引入）：`tests/test_session_history_frontend.cjs` 在基线亦挂起；`branding_frontend.test.cjs`、`test_appearance_browser.cjs` 等为既有失败，均不在本 change 目标套件内。
- [x] 6.6 用户域目录按需创建（`ensure`），确认不会在只读请求上产生副作用目录。
- [x] 6.7 修复 7.3 验收暴露的回归：共享 `knowledge/` 位于 Agent 工作区之外时，`MemoryManager.sync()` 用 `relative_to(workspace_dir)` 生成索引标签会抛 `ValueError`，并连带中止记忆索引。
  - 现象：`ValueError: '<tenant shared root>/knowledge/log.md' is not in the subpath of '<agent workspace>'`。
  - 成因：本 change 改为经 `state_dir.knowledge_dir(base=workspace)` 解析知识目录，Agent 无自有 `knowledge/` 时解析到租户共享根（工作区之外）；收集阶段却仍按工作区取相对路径。单元测试的临时目录里没有共享 `knowledge/`，故未覆盖。
  - 修复：知识文件带显式标签 `knowledge/<相对知识根>` 入索引（与原先工作区内场景标签一致，**无需重建索引**）；同步主循环的标签推导加兜底，工作区外文件不再让整轮同步崩溃。
  - 回归测试：`tests/test_user_personal_memory.py::PersonalMemoryTestCase::test_shared_knowledge_is_indexed_without_crashing`（先 RED 复现 `ValueError`，再断言标签 `knowledge/log.md`）。
  - 既有污染核对：`test_memory_global_config.py + test_knowledge_web.py` 组合下 `test_knowledge_web.py` 的 4 条失败在基线（`agent/` 回退）同样失败，与本 change 无关。

## 7. 文档与验收

- [x] 7.1 更新 `docs/design/` 说明：个人层归属 `user_root`、默认智能体为综合会话锚点、个人记忆跨智能体一致的材料（含"为什么不新建 per-user 智能体"的取舍）。
- [x] 7.2 记录"个人档案与个人记忆的编辑入口"为本轮 Non-Goal，留待后续 change。
- [x] 7.3 真实实例验收：以双用户账号进入对话不选智能体即可开聊；A 的个人记忆在默认智能体与另一智能体下均可见、B 不可见；切回另一智能体后 A 的记忆仍在。
  - 已在**沙箱真实实例**上完成（真实 `app.py` 进程 + 真实 `identity.db` + 真实租户共享根 + 真实 SQLite），不写生产数据；验收脚本 `scripts/acceptance_personal_context.py`，**23/23 通过**（登录、Agent 列表与默认标记、无 Agent 发起会话、会话归属、跨用户会话 404、个人记忆用户域落盘、跨 Agent 可见、跨用户不可见、索引 `scope/user_id` 归属）。
  - 生产实例（后经排查为 **database 身份模式**，不是先前判断的 legacy）已在现场复核，见 8.1；无需身份模式迁移。若要面向多账号复跑脚本验收，用同一脚本即可（见验收文档第 6 节）。
  - 详见 `docs/design/personal-conversation-and-memory-acceptance.md`。
- [x] 7.4 验收证据落盘：路径、DB 归属（`scope`/`user_id`）、审计记录、前端行为截图或日志。
  - 路径：个人记忆 `/tmp/cow-tenants/acc/users/<user_id>/memory/2026-09-10.md`（用户域），与 Agent 工作区平级。
  - DB 归属：`chunks` 行 `path=memory/users/<user_id>/2026-09-10.md, scope=user, user_id=<alice>`；`sessions` 行 `owner=<发起者>` 且落在默认 Agent。
  - 审计：`tenant.bootstrap` / `member.create` / `agent.bind`×2 / `tenant.set_default_agent` / `role.create` / `member.update` / `role.update` 全部 `success`。
  - 前端行为：13 个 `node:test` 套件 185 通过 / 0 失败（含 `test_agent_chat_launch_frontend.cjs`）；未做截图级验证，已注明。
  - 全部证据与复现命令见 `docs/design/personal-conversation-and-memory-acceptance.md`。
  - 验收中发现并修复 1 个真实回归：共享 `knowledge/` 位于 Agent 工作区之外，`sync()` 用 `relative_to(workspace)` 取标签抛 `ValueError`，导致记忆与知识**一起停止索引**（见下 6.7）。
- [ ] 7.5 PRD 原文恢复后复核三处口径（个人助理是否为独立实体、自动固化默认归属、个人记忆是否跨租户/跨设备同步），需要时另开 change，不在本 change 内改口径。
  - 依赖 PRD 原文恢复，本轮无法执行。

## 8. 真实实例「agent not found」修复（用户现场反馈）

- [x] 8.1 复现并定位：本机真实实例（database 身份模式，租户 `test15`）成员 `test15-2` 发送
  报 `agent not found`。用真实服务端函数逐跳复现，确认**免选解析本身正常**
  （`_resolve_tenant_default_agent` 返回 `test15-verify`），失败来自四跳链路：
  `GET /api/agents` 因缺少 `agent:<id>` 资源授权返回空 → 控制台回落**字面量** `'default'`
  → 全局 fetch 装饰把它附加到 `POST /message` → 该标识属**另一租户**，租户绑定校验 404。
  其后还压着第二个拦截点：默认智能体带私有归属，成员触发 `_require_private_owner` 403。
  详见验收文档 5.4。
- [x] 8.2 服务端（需求：共享默认智能体的可达性不依赖逐资源授权）：新增单点判据
  `_tenant_shared_default_agent()`，接入投影可见性、`_require_agent_action`、
  `_workbench_chat_readiness`；放宽仅限 `read`/`use` 且要求持有对应功能权限、智能体为
  调用者本租户的解析默认且租户共享；`edit` 保持严格逐资源授权。
  - RED→GREEN：`tests/test_tenant_default_agent.py::SharedDefaultAgentReachabilityTests` 9 条
    （4 条"应放宽"先失败、5 条"必须保持收紧"护栏先通过）。
- [x] 8.3 前端（需求：控制台不得虚构智能体标识）：`console.js` 去掉字面量 `'default'` 兜底，
  空目录或记忆值不在目录中时不携带智能体标识，交由服务端解析租户默认；记忆值仍在目录中
  则保留为显式选择。
  - RED→GREEN：`tests/test_agent_chat_launch_frontend.cjs` 3 条（空目录、陈旧记忆值、仍有效的记忆值）。
  - 桩补齐：`tests/test_agent_workbench.py` 的 `_AllowAgentSvc`/`_AllowSvc` 补
    `resolved_default_agent_id`（新调用路径要求桩建模其接口）。
- [x] 8.4 数据校正：`cow management share-default-agents` 清除默认智能体私有归属
  （dry-run 预报 2 条；实跑 `Updated 2 Agent(s)`；幂等）。
- [x] 8.5 真实实例复验：`test15-2` 全链路 PASS（可见 → 就绪 → 私有门禁 → `use` → `read` → `chat.use`），
  且 `agent.edit` 仍被拒（未越界）；`test15`、`admin` 亦 PASS；`e2e_member` 如实返回
  `permission_denied`（缺 `agent.use`/`chat.use`）。
- [x] 8.6 回归对比：全量 `tests/` 修复前 33 失败 / 1929 通过 → 修复后 32 失败 / 2333 通过，
  失败集合差集为空（零引入）；唯一差异 `test_two_tenants_each_mark_their_own_default`
  由失败转通过（HEAD 隔离运行亦失败，属一并修好的既有缺陷）。前端 24 通过 / 3 既有失败 /
  1 既有悬挂，与基线一致。
- [x] 8.7 实例重启：确认唯一进程加载新服务端代码（进程启动时刻晚于源码 mtime），端口 9899 健康。
