# 11.1-11.5 产品规划 3.1 联合验收（成员个人控制台 × 数据库能力对等）

- 记录日期：2026-09-15
- 用例文件：`tests/test_plan_3_1_joint_acceptance.py`（新增，11 个用例；全部经 `WebAppHarness` 的真实 WSGI 应用与真实服务对象，未使用被测对象的桩）
- 联合对象：`enable-member-personal-console`（本人私有对象、专属助理、本人删除边界、个人记忆、个人工具/技能参数与凭据引用） × 本 change（10 个恢复入口、`member_personal_console`/`personal_channel_runtime` 能力登记、加固后的共享记忆文件访问、按 owner 的任务授权） 
- 结论：11.1/11.2/11.5 的**联合**断言已建立并实际执行；**11 个用例全部通过**。
  §4 记录的 blocker（成员经 `/api/agents` 删除入口可抹除系统供应助理）**已修复**并复验；
  11.3 的真实微信验收仍缺失（见 §5），因此第 11 组中 11.3 保持未勾选、`tasks.md` 不宣布全部覆盖、未归档。

## 1. 为什么必须联合验收（接缝）

两个 change 各自的套件可以同时全绿而组合失效，失败恰好落在接缝上，单一 change 的套件结构上覆盖不到：

| 接缝 | 单独套件为何测不到 |
| --- | --- |
| owner 所有权（兄弟 change）× `/api/agents` / `/api/scheduler*` 恢复入口（本 change） | 兄弟 change 的用例走 `PrivateAgentService`，本 change 的用例走管理员/共享对象；**"所有者经 HTTP 入口"这一组合路径两边都不覆盖** —— §4 的真实 blocker 正是这条缝 |
| 停用对象（兄弟 change 的生命周期）× 默认入口解析（本 change 的默认回落与运行解析） | "本人登记指针不得被改写"与"当前可解析默认要回落"必须同时成立，才推出"停用对象不被复活、也不被改派" |
| 个人写入（兄弟 change 的个人域）× 公共配置与公共记忆（本 change 的公共面与加固） | 兄弟 change 只读本人的域，本 change 只读公共面；"一方写入不动另一方"需要一次请求同时观察两侧 |
| 能力撤下（本 change 的能力登记）× 兄弟 change 的 owner 判定与已存字段 | 撤下后"拒绝仍发生在读之前""已存 owner/scope 不丢"是两个 change 的联合断言，任一单方都不足以证明 |

## 2. 对照设计 D1 的逐条记录（任务 11.4）

| 需求（D1 条款 / tasks） | 实现版本（文件 + 做了什么） | 已交付/复用项 | 本 change 补强 | 剩余阻断 |
| --- | --- | --- | --- | --- |
| 3.1.2 规则 5：停用对象不被默认入口复活（11.1） | `auth/service.py::member_default_agent_id`（本人登记指针）与 `resolved_default_agent_id`（当前可解析默认）分离，停用只影响后者；`channel/web/web_channel.py` 的 `_scheduler_access` 经 `agent/tools/scheduler/authorization.py::TaskAccessService._can_use_agent` 按**被寻址**的 Agent 解析授权与运行时 | 兄弟 change 的私有 Agent 生命周期（启停、个人默认登记） | 调度入口不再把停用对象改派到租户默认或共享 Agent：`/api/scheduler` 只列可达 Agent；`/api/scheduler/run` 对停用对象返回 `run_unavailable`(503) 且运行时解析只发生在被寻址的 Agent 上；经工具创建任务不落共享存储、也不自动启用对象 | 无 |
| 3.1.3 成员 3.2/3.6：本人与获权公共对象分开、任务不改归属（11.1） | `auth/service.py::resource_ids_for`（owner 动作由绑定派生，见 `PRIVATE_AGENT_OWNER_ACTIONS`）、`check_resource_action`、`bind_agent`；`agent/tools/scheduler/authorization.py::TaskAccessService.create_task` 原子写入 `scope` 与 `owner` | 兄弟 change 的自建/专属助理与 `origin` 来源判定 | 任务 `scope=personal`、`owner={user_id,tenant_id,agent_id}` 由服务端盖章（客户端参数不可覆盖），同一写入不进共享 Agent 的存储 | 无 |
| 3.1.3 成员 3.2：本人维护不加宽授权（11.1） | `auth/service.py::grants_for` / `tenant_resource_grants`：授权真值仍是角色 grant 表与所有权派生，本人维护路径没有写 grant 的入口 | 兄弟 change 的本人维护入口（配置/启停/删除） | 断言 `grants_for`、`role_resource_grants` 原始行、`tenant_resource_grants` 在"配置→停用→启用→建任务→写个人记忆→删除自建对象"前后**完全不变**；对共享 Agent 的 `agent.read`/`agent.edit` 仍拒绝 | 无 |
| 3.1.3 成员 3.2：本人删除边界（11.1） | `agent/private_agent.py::PrivateAgentService.delete_private_agent`（先判归属，再判 `origin='user_created'`，再加依赖冲突闸门）；投影 `actions.delete` 与之一致 | 兄弟 change 的删除语义与来源迁移 | 投影 `delete: false`、服务层 `forbidden` 实测一致，且拒绝发生在绑定被改动之前 | **有**：`channel/web/web_channel.py` 的 `AgentsHandler` `action == "delete"` 分支只校验 `agent.edit`，未经过来源判定 → §4 |
| 3.1.3 成员 3.5：个人工具/技能参数与凭据引用，不覆盖公共配置（11.2） | `auth/service.py::save_/get_/resolve_personal_resource_config`（归属恒为调用者本人，无 `user_id` 入参；保存门槛是"已持有该资源的 *使用* 动作"）、`resolve_credential`（owner 判定先于解密）、`channel/web/web_channel.py` 的 `PersonalResourceHandler` | 兄弟 change 的参数存储与个人凭据引用 | 获权项可保存并回读（返回本人 owner 的 `credentials` 行）、目录里有但未获权的项 `403 forbidden`、他人 GET 只得到空集；公共（无主）凭据计数不变 | 无 |
| 3.1.3 成员 3.4 + 5.6-5.8：加固后的共享记忆服务（11.2） | `agent/memory/personal.py::PersonalMemoryService` 经 `common/safe_fs.py` 的锚定根受限访问（`resolve_within` / `_DirChain` / `write_text_atomic`）在校验**真实归属**后使用文件；`channel/web/memory_console.py::resolve_target` 只接受相对条目 ID | 兄弟 change 的 `/api/memory/personal*` CRUD 与列表/正文字段 | 相对 ID 合法；绝对路径、`..` 穿越、非本存储扩展名一律 `invalid_entry`；跨根软链接在**读取元数据与正文之前**拒绝 `unsafe_path`(403)，且指向的外部文件内容不变；本 change 恢复的 `/api/memory` 与 `/api/memory/content` 读到同一结果 | 无 |
| 3.1.3 成员 3.4：本人写入不动他人与公共（11.2） | 同一服务 + `auth/service.py::tenant_shared_root`；个人域按可信 tenant/user 根解析（`common/state_dir.py::user_root`） | 兄弟 change 的个人记忆域划分 | 一次成员写入后重读：他人个人域正文与列表不变、租户公共记忆正文与字节数不变、公共凭据计数不变、花名册不变、`personal_resource_configs` 只含本人行 | 无 |
| 分阶段关闭：不切回 legacy（11.5） | `auth/capability_matrix.py`（能力登记与 `page_availability`）、`auth/policy.py::PERSONAL_CAPABILITY_SWITCHES` / `PERSONAL_PAGE_CAPABILITIES`、`auth/service.py::context_for_tenant` 的 `console_pages` | 兄弟 change 的开关语义（开关只决定"是否提供"，不决定"是否鉴权"） | 撤下 `member_personal_console` + `personal_channel_runtime` 后 5 个个人页均 `available=false`、`read_allowed=false`、`reason=capability_disabled`；`identity_mode` 仍为 `database`；匿名请求仍 401 | 无 |
| 分阶段关闭：不丢 owner/scope（11.5） | `auth/store.py` 的 `agent_bindings`（`tenant_id` / `private_owner_user_id` / `origin`）；任务的 `scope`/`owner` 由 `TaskAccessService` 盖章 | 兄弟 change 的来源迁移（`_migration_17`） | 撤下能力后重读绑定与任务：`tenant_id`、owner、`origin`、`scope` 全部存活；本人登记指针仍指向本人登记的对象；撤下后本人仍可停用自己已持有的对象，且绑定字段不变 | 无 |
| 分阶段关闭：不恢复管理员旁路（11.5） | `channel/web/memory_console.py` 的 `_require_private_owner`（非 owner 拒绝位于平台 `all` 与租户管理员公共权限之前）；`agent/memory/service.py` 只在 owner 路径被触达 | 兄弟 change 的 owner 优先拒绝 | 以 tripwire 证明 **refuse-before-read**：owner 请求确实到达 `MemoryService.list_files`/`get_content`（被替换为 tripwire 并触发），而 tenant_admin 与同租户他人都是 `403 not_owner` 且 tripwire 计数不增；撤下开关前后结论一致，同一管理员仍能读公共记忆 | 无 |

## 3. 用例清单（11 个，全部新增）

`tests/test_plan_3_1_joint_acceptance.py`：

| # | 用例 | 对应 |
| --- | --- | --- |
| 1 | `MemberOwnedObjectTests::test_11_1_the_owner_configures_toggles_and_deletes_what_they_own` | 11.1 本人配置/启停/删除 |
| 2 | `MemberOwnedObjectTests::test_11_1_the_supplied_assistant_is_not_an_object_the_member_created` | 11.1 专属助理边界（投影 + 服务层拒绝） |
| 3 | `MemberOwnedObjectTests::test_11_1_the_console_delete_route_keeps_an_object_the_member_did_not_create` | 11.1 删除入口（**blocker**，见 §4） |
| 4 | `MemberOwnedObjectTests::test_11_1_a_disabled_object_is_not_revived_by_a_scheduled_task` | 11.1 停用不被默认入口复活 |
| 5 | `MemberOwnedObjectTests::test_11_1_creating_a_task_addresses_the_hosting_object_and_stays_personal` | 11.1 创建任务按被寻址对象且保持本人归属 |
| 6 | `MemberOwnedObjectTests::test_11_1_neither_flow_widens_the_members_resource_grants` | 11.1 两条流程都不加宽授权 |
| 7 | `PersonalConfigurationTests::test_11_2_personal_parameters_are_owner_scoped_and_the_catalog_is_not` | 11.2 个人参数/凭据引用与未获权拒绝 |
| 8 | `PersonalConfigurationTests::test_11_2_personal_memory_takes_relative_ids_and_refuses_hostile_ones` | 11.2 加固后的记忆文件访问 |
| 9 | `PersonalConfigurationTests::test_11_2_a_members_writes_leave_others_and_the_public_state_alone` | 11.2 他人内容与公共配置不变 |
| 10 | `StagedShutdownTests::test_11_5_withdrawal_closes_surfaces_without_losing_stored_facts` | 11.5 撤下不切回 legacy、不丢 owner/scope |
| 11 | `StagedShutdownTests::test_11_5_the_administrator_refusal_happens_before_any_read` | 11.5 不恢复管理员旁路（refuse-before-read） |

## 4. Blocker（已修复；用例未改、未弱化）

**原始现象（2026-09-15 首轮）**：成员（专属助理的 owner，具备 `agent.edit`、无任何 `agent:<id>` 手写 grant）
对**系统供应**的专属助理发出 `POST /api/agents {"action": "delete", "id": "alice-assistant"}` 时请求成功；
`agent_bindings` 行与该 Agent 的 roster 行同时消失。同一对象上投影 `actions.delete` 为 `false`、
`PrivateAgentService.delete_private_agent` 也以 `forbidden` 拒绝——即**投影与服务层的"不得删除"没有落在
HTTP 入口上**。根因：`channel/web/web_channel.py` 的 `AgentsHandler` POST 分支 `action == "delete"` 只做
`_require_agent_action(ctx, agent_id, "edit", "agent.edit")` 后直接调用 `AgentAdminService.delete_agent`，
`origin='user_created'` 的来源判定只存在于 `agent/private_agent.py::PrivateAgentService.delete_private_agent`，
该入口未接入。

**修复**：`channel/web/web_channel.py` 新增 `_require_deletable_provenance(ctx, agent_id)` 并在 `delete`
分支于授权检查之后调用。判定顺序是"先归属、再来源"：不是本租户的绑定直接放行（行为不变，跨租户/共享对象
仍由 grant 挡住）；绑定属于调用者本人且 `origin` 属于 `SUPPLIED_ASSISTANT_ORIGINS`（系统/平台供应）时，
以 `403 {"code": "forbidden", "status": "error"}` 拒绝。因此"成员不得抹除系统供应助理"由**服务端**判定，
不再只靠前端隐藏控件；本人自建对象仍可删除。

**复验（同一用例、未改断言，只把响应口径钉到稳定字段）**：

```
$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py -q -p no:randomly
→ 11 passed in 10.85s

$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py -q            # 随机顺序
→ 11 passed
```

用例断言现在明确读出机器码（`code == "forbidden"`、`status == "error"`）并复核绑定仍存在，
因此"拒绝发生在绑定被改动之前"是可观测事实；既有断言一条未删、一条未弱化，也没有新增跳过标记。

## 5. 刻意不覆盖（不伪造）

1. **11.3（微信扫码到个人收发的真实提供方验收）整体不覆盖。** 它要求 7.8-7.10 的真实类型验收（真实提供方、手机端扫码、个人收发、适用审批、解绑、治理停用、群聊隔离）；其中 7.9（适用审批消费者）与 7.1-7.7（扫码配置面）已交付并有用例，但 7.8 的真实提供方验收与 7.10 的类型开放本环境无法产生，故本文件与用例文件都不涉及；未验收类型继续保持关闭。11.3 不因本组其他项通过而视为覆盖。
2. **Desktop 客户端面未单独取证。** 11.2 要求"Web/Desktop 本人维护"，本组用例经 Web/HTTP 面（`WebAppHarness` 的真实 WSGI 应用）验证**共用后端入口**（`/api/personal-resources/*`、`/api/memory/personal*`、`/api/memory`）；Desktop 渲染进程的这些页面（`desktop/src/renderer/src/pages/{MemoryPage,ChannelsPage,TasksPage,AgentsPage}.tsx`）走同一批 HTTP 入口，但本组未在真实打包客户端里重跑一遍，真实客户端演练属任务 8.7（未通过）。
3. **`/api/agents` 的 `archive` 动作未断言。** 它与 blocker 同属一个 `agent.edit` 门禁，但不在 11.1-11.5 的明文要求内；如需沿用 §4 的结论必须单独取证，本文件不作推断。
4. **记忆发布/清空的并发协议（5.6-5.8 的另一半）不在本组。** 本组只断言接缝相关的一半（真实归属、拒绝先于读取、写入不越域）；并发屏障与发布 generation 由 `tests/test_memory_console_scope.py` 等既有切片背书（见 `evidence/5-memory-console.md`），本轮随 C4 一并跑过但未逐条复验。
5. **多写者/跨进程形态未覆盖**（与 4 同源）：本组是单进程内的一致性与授权验收。

## 6. 验证记录（命令与真实输出尾部）

命令中 `-q -p no:randomly` 为仓库约定的跑法；`--deselect <blocker 用例>` 在首轮用于把阻断项排除后展示
"其余项与邻居套件同时全绿"，**本轮已不再需要**：blocker 修复后所有组合都整轮跑、无 deselect。

```text
$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py -q -p no:randomly
...........                                                              [100%]
11 passed in 10.85s

$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py -q       # 随机顺序，证明用例间无顺序依赖
...........                                                              [100%]
11 passed in 11.14s
```

```text
$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py \
      tests/test_personal_delivery_drill.py tests/test_personal_memory_console.py \
      tests/test_private_agent_owner_reachability.py tests/test_personal_console_acceptance.py \
      tests/test_private_resource_acceptance.py -q -p no:randomly
.............................................................            [100%]
117 passed in 39.47s
```

```text
$ .venv/bin/python -m pytest tests/test_plan_3_1_joint_acceptance.py \
      tests/test_scheduler_task_authorization.py tests/test_scheduler_web_update.py \
      tests/test_scheduler_tool_dispatch.py tests/test_capability_matrix.py \
      tests/test_personal_capability_switches.py tests/test_personal_resource_config.py \
      tests/test_private_agent_owner_actions.py tests/test_memory_console_scope.py \
      -q -p no:randomly
........................................................................ [ 69%]
................................................................         [100%]
208 passed in 61.36s (0:01:01)
```

- 用例文件内自带清理（`tearDown`/`addCleanup`）：`reset_scheduler_services()`、`ToolManager.reset_instances()`、`set_agent_registry(None)`（console 的运行时重载会**钉住** roster 注册表，跨用例/跨文件会读到别人的花名册）、`COW_CREDENTIAL_MASTER_KEY` 复原。顺序依赖由随机顺序跑法与跨文件组合跑法各验证一次。
- 首轮（只有 blocker 的那次）**未修改**产品代码、`auth/capability_matrix.py`、`channel/web/route_registry.py`、
  `channel/web/web_channel.py`、任何 spec、`tasks.md` 或任何既有测试文件，只新增
  `tests/test_plan_3_1_joint_acceptance.py` 与本文件；**本轮修复**按 §4 只改
  `channel/web/web_channel.py`（新增 `_require_deletable_provenance` 并在 delete 分支调用）与用例里对
  拒绝响应的稳定字段断言，测试文件与规格均未被删弱。
