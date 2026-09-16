# 7.3 旧个人开关被新入口绕过的收口：修复与验证（R1 / R2）

本文件记录 `evidence/7-2-7-3-runtime-merge-audit.md`（下称 7.2/7.3 审计）"残留工作清单"中
**R1**（`personal_channel_onboarding` 的显式关闭被 `/api/tenant/channels` 绕过）与
**R2**（`member_personal_console` 的显式关闭被私有 Agent 创建共用落点绕过、且投影与接口两个答案）
的**实现与验证**结果。

**范围与声明**

* 只改「旧开关在新共用入口上的强制点」与「同一页面的动作投影」。**不改**入站、运行期合成、
  凭据契约集合、配额、治理停用、目录声明。R3（更正 `evidence/7-1-runtime-preflight.md:263` 的
  错误判定）不属本文件范围，审计已登记。R4（真实提供方往返）仍受凭据阻塞，与
  `evidence/7-1-runtime-preflight.md` 结论一致，本文件不含任何真实厂商往返。
* 全部结论是**进程内**证据：真实 `build_web_app()` WSGI 应用 + 真实 `IdentityService` +
  真实加密（`COW_CREDENTIAL_MASTER_KEY`）+ 真实 SQLite 写事务。
* `auth/service.py`、`channel/web/web_channel.py`、`channel/web/route_registry.py`、
  `channel/web/static/js/console.js` 在本轮写作期间仍被其它 agent 并发编辑。下文行号是
  **读取时刻**快照（基准 `cbdb9884` + 工作树），所有引用同时给出函数名/常量名，漂移后按名定位。
  本轮全部编辑都是**定点替换**（无整文件重写），每次编辑前后都复核了邻接区域；
  `channel/web/web_channel.py`、`route_registry.py`、`console.js` **零改动**。
* 判定基准是**代码强制**。凡「只在注释或规范里成立」的一律不记为已修。

## 0. 结论摘要

| 项 | 结论 |
| --- | --- |
| R1 是否关闭 | **是**。共用写入口的开启方向（新建、重新启用）在 `scope='user'` 上复读两个开关，`capability_disabled` 403，且**不落行/不落凭据/不落审计**；扫码门、旧包装、共用门三者同答案 |
| R2 是否关闭 | **是**。私有 Agent 创建的共用落点 `bind_private_agent_with_quota` 复读 `member_personal_console`；控制台创建路径在 **clone 之前**就拒绝（不留补偿痕迹）；`admin.channels.actions.create` 与投影的 `switches` 块不再自相矛盾 |
| 关闭方向是否保留 | **是**。停用、撤销、解绑等"收回自有对象"动作不进任何新增闸门；成员不会被卡在一条关不掉的连接上（§3 双向核对） |
| 决策 | 采纳任务给定的裁决：**开关在写路径强制**。理由：留着一个只有页面隐藏、接口仍开的"杀开关"是虚假安全感，与本 change「后端拒绝它不提供的东西」的治理原则冲突；且**这不是语义外推**——`member_personal_console` 早已在 `save_personal_resource_config`（`auth/service.py:5780`）这条写路径上强制（§3） |
| 已知后果（故意） | 开关关闭时，私有 Agent 创建对**API 客户端同样拒绝**（`bind_private_agent_with_quota` 是共用落点）。这是本裁决要求的行为，不是回归 |
| 遗留 | 共用**编辑**路径（`/api/tenant/channels/<id>`）在关闭期仍可改名/换绑/换密钥——旧包装会拒、共用门不拒。属同类缺陷的第三条门，本轮**未覆盖**，已给最小落点与冲突理由（§9） |
| 变异验证 | 5 个守卫逐个关闭：3 / 2 / 1 / 1 / 2 例转红，恢复后 17/17 绿（§6） |
| 并发复核 | 另一 agent 的 4 个标记（`_require_skill_write_scope` / `_resolved_skill` / `_annotate_skill_actions` / `memoryEntryEditable`）在编辑前后逐字存活（§8） |

## 1. 两条缺陷与可达路径（复核）

### 1.1 R1：`personal_channel_onboarding` 的关闭被共用写入口绕过

审计给出的探针（成员夹具，开关置 False）：

```
legacy create    capability_disabled 403      ← 旧入口：关闭生效
shared create    -> 200 OK (row created)      ← 新入口：关闭被绕过
shared re-enable -> 200 OK (row active=1)     ← 新入口：关闭被绕过
```

复核结论与审计一致，并补上一条审计未点明的事实：**同页扫码门读该开关**
（`channel/weixin_scan_adapter.py:316` 显式 `require_personal_capability("personal_channel_onboarding")`
后在 `:319` 调用**同一条** `service.create_tenant_channel_instance`），手工表单门不读。
同一个 `admin.channels` 页面上，两个入口对同一个关闭值给出两个答案。本轮补的闸门落在被两门共用的
那个函数里，所以扫码门、旧包装、共用门现在是同一答案（且扫码门顺带也获得总开关的收窄）。

根因定位（复核后）：

* 旧包装 `create_personal_channel_instance` / `set_personal_channel_instance_active` 在
  **调用共用服务之前**先 `require_personal_capability("personal_channel_onboarding")`；
* 共用门 `channel/web/admin_handlers.py::TenantChannelsHandler.POST`（`/api/tenant/channels`）与
  `TenantChannelActiveHandler.POST`（`/api/tenant/channels/<id>/active`）不读任何开关，
  直接调 `create_tenant_channel_instance` / `set_tenant_channel_instance_active`；
* 服务内 `allow_owner=True` 分支只校验 `personal_channel_ready`（声明集 + 配置收窄），
  **不含**任何能力开关。

### 1.2 R2：`member_personal_console` 的关闭被私有 Agent 创建绕过，且投影自相矛盾

复跑同一探针（`member_personal_console=False`）：

```
member_personal_console=False        -> bind_private_agent_with_quota SUCCEEDED
user_private_agent_management=False  -> bind_private_agent_with_quota capability_disabled 403
```

同时：

* `admin.agents.actions.create` 在该状态下已是 `False`（`auth/service.py` 的 agents 分支
  已读 `personal_page_capabilities("personal.agents")`）——**投影说关，接口不拒**；
* `admin.channels.actions.create` 仍为 `True`，同一投影却携带
  `switches = {'member_personal_console': False, 'personal_channel_onboarding': True}`
  ——**投影说关，动作却报可用**。

即 R2 是**同一个形状的两个侧面**：一个写路径不读开关，一个投影不读开关。

## 2. 修复

三处定点编辑（全部为单段 find-and-replace，无整文件写入）：

### 2.1 R1 与 R2 的写路径闸门：`_enforce_personal_instance_policy`

`auth/service.py:7889`（函数定义），新增两行在函数体首部（`:7911-7912`）：

```python
        self.require_personal_capability("member_personal_console")
        self.require_personal_capability("personal_channel_onboarding")
        policy = self._effective_channel_policy(con, tenant_id)
```

docstring 同步改写（首句 + 一段"为什么在这里"）：

```
        """Refuse a personal instance the tenant policy or a withdrawn deployment
        switch does not allow.
        ...
        The deployment's own switches are read here too, and *here* is the point:
        this function is reached only for ``scope='user'`` and only from the two
        writes that *open* a connection (create and enable). ... Both switches
        behind that page are read, not just the slice one, because the page
        reports both and either being off means the member's surface is off.
        Disabling and revoking never reach this function, so a withdrawal still
        cannot strand a member with a connection they can no longer turn off.
        """
```

**为什么放在这个函数**：它是审计点名的最小落点，且**只**在 `scope='user'` 的两条开启路径上被调用
（`create_tenant_channel_instance` 创建分支、`set_tenant_channel_instance_active` 的 `if active:`
分支），所以公共/租户连接与成员自己的停用路径都不受影响——"off 只拒开启"的对称性由调用点结构保证，
不需要在函数里再判方向。

**为什么读两个开关（超出审计最小修法的一行）**：审计只要求补 `personal_channel_onboarding`。
但 R2 的裁决同时要求"投影必须与写路径同答案"，而这一页的 `switches` 块**同时报告两个开关**
（`PERSONAL_PAGE_CAPABILITIES` 把 `personal.channels` 同时挂在 `member_personal_console` 与
`personal_channel_onboarding` 上，`auth/policy.py:589-596`，该页条目在 `:592-593`）。只补分片开关的话，会得到新的两个答案：
投影 `create=False`、接口 `200`。因此把总开关也放在同一处、只作用于成员自己的开启写。
这一行**是本轮唯一超出审计最小修法的行为扩展**；若复核者要更窄的读法，删掉 `:7911` 一行即可
（届时 §2.3 的投影必须同步只读分片开关，否则会留下"页说不、接口说可"的反向不一致）。

### 2.2 R2 的共用落点：`bind_private_agent_with_quota`

`auth/service.py:1464`（该函数定义在 `:1416`），紧邻既有的
`user_private_agent_management` 检查之后：

```python
        self.require_personal_capability("user_private_agent_management")
        # The console-wide switch is the same kind of withdrawal one level up,
        # and this insert is where it has to be answered: the projection already
        # reports ``admin.agents.actions.create = False`` when it is off, and a
        # page that says "off" while this write still lands is exactly the
        # false security ("the API stayed open") the switch must not give. Read
        # *after* the retry above for the same reason as its neighbour, so a
        # retried bind of an object the member already holds is not a new object.
        self.require_personal_capability("member_personal_console")
```

顺序刻意与邻居一致（幂等重试在前）：重试已有绑定不算"新建对象"，与
`user_private_agent_management` 的既有取向逐字同形。

### 2.3 R2 的投影：`admin.channels` 的 `create` 跟随开关

`auth/service.py:3561-3569`（注释）、`:3570`（`switches` 计算前移）、`:3576-3580`（`create` 条件）：

```python
            switches = personal_page_capabilities("personal.channels")
            entry = {
                ...
                "actions": {
                    "create": bool(allowed
                                   and (ready is None or bool(ready))
                                   and (scope != "self"
                                        or all(switches.values()))),
                    "update": allowed,
                },
            }
```

* `switches` 从原来的 `if scope == "self":` 块**前移**到动作块之前（同一函数、同一纯函数调用，
  不再重复求值）；`if scope == "self":` 块内的 `entry["switches"] = switches` 保持原样。
* `scope != "self"` 短路是**必要的**：控制用户的 `create` 同时涵盖公共连接，公共连接不受这两个开关
  约束，把总开关套到管理页上会取走它仍可用的动作（由 §4 的管理员控制用例钉住）。
* 只读 `personal.channels` 的开关集合，不新增第二个开关来源；投影与写路径读的是同一个
  `personal_page_capabilities`。

### 2.4 R2：控制台创建路径的"早退"（可选优化，但被测试钉住）

`agent/private_agent.py:209`（该文件不在并发编辑清单内）：

```python
        self._svc.require_personal_capability("user_private_agent_management")
        # The console-wide switch, in the same cheap position: it is enforced by
        # ``bind_private_agent_with_quota`` below, and reading it here too means a
        # withdrawn console does not clone a workspace only to delete it again.
        self._svc.require_personal_capability("member_personal_console")
```

它不是权威闸门（权威在 §2.2）：即使删掉，写入仍会被拒，只是先 clone 再靠 `_compensate` 删掉。
保留它的理由是与邻居 `user_private_agent_management` 的既有形状一致，并且它有**独立可观测**的断言
（§6 M4：`roster.deleted == []`），不会变成无人检验的死代码。

## 3. R2 的决策、理由与后果（含"开关语义是否只是页面可见性"的核对）

**裁决（任务给定，本轮照做）**：开关在**写路径**强制；"off"意味着能力关闭，而不只是页面隐藏。

**核对：这个开关的语义**，避免把"页面可见性开关"误当运行闸门。三条代码事实否掉了
"它只约束页面可用性"这一读法：

1. `member_personal_console` **已经在一条写路径上强制**：`save_personal_resource_config`
   （`auth/service.py:5762`，require 在 `:5780`）在"保存新个人参数"时 `require_personal_capability("member_personal_console")`。
   一个"只管页面"的开关不会有写侧调用点；这一处是既有的、可复核的先例。
2. `auth/policy.py:559-563` 的开关语义注释写明：开关"只决定能力是否**被提供**（offered）……
   关闭只会收窄控制台**展示与接受**的东西"——"accepts"即写侧。
3. 归档规范把它写死为开启类写入的拒绝集合：`console-navigation-availability/spec.md:48`
   「开启类写入（**新建自建智能体**、新建/编辑个人渠道实例、**重新启用**、发起绑定挑战、保存个人参数、
   新增记忆内容）SHALL 在对应开关关闭时以 `capability_disabled` 拒绝且**不产生部分写入**」。
   R2 的"新建自建智能体"正是该句点名的一项，因此 §2.2 不是语义外推，而是补齐既有规范要求的强制点。

**理由（记录在 evidence，按任务要求）**：一个把页面隐藏、接口仍开的"杀开关"是**虚假安全感**——
它把部署者的撤权意图变成一句 UI 陈述，而调用记录、审计与落库仍是开的。本 change 的治理原则是
"后端拒绝它不提供的东西"（`design.md:107`：「显式关闭值先保留为对应对象范围的部署限制……
不能借新入口绕过旧关闭意图；管理员维护相同归属对象也受相同限制」）。写路径强制是该原则唯一的落地方式。

**后果（如实登记）**：开关关闭时，私有 Agent 创建对 **API 客户端同样拒绝**
（`bind_private_agent_with_quota` 是共用落点，`channel/web/web_channel.py` 的
`_adopt_created_agent_for_tenant` 私有分支也从这里走）。这是**故意**的行为，不是副作用：
"off 是能力关闭"的定义就包含 API 面。控制台路径另有 §2.4 的早退，因此不会先 clone 再删。

**对称性核对（代码 + 规范双向，而不是照抄任务里的括注）**：

| 方向 | 动作 | 代码 | 规范 | 本轮行为 |
| --- | --- | --- | --- | --- |
| 开启 | 新建本人连接 / 重新启用 | 旧包装读开关（`auth/service.py:9365`→require `:9391`、`:9439`→require `:9455` 仅开启分支）；共用门原先不读 | `spec.md:48` 拒 | **拒绝**，且不落行（§4 控制用例） |
| 开启 | 新建自建智能体 | 共用落点原先不读 | `spec.md:48` 拒 | **拒绝**（§4 控制用例） |
| 关闭 | 停用本人连接 | `set_tenant_channel_instance_active` 只在 `if active:` 内调用闸门 | `spec.md:48/64-66` 必须可用 | **仍可用**（§4 控制用例） |
| 关闭 | 撤销凭据 | `revoke_personal_channel_credentials`（`:9470`）无能力检查 | 同左 | **仍可用**（既有测试） |
| 关闭 | 删除自建对象 / 清空记忆与配置 | `delete_private_agent`、记忆删除/清空不吃创建类开关 | 同左 | **仍可用**（既有测试） |

结论：审计说的"故意不对称"成立——**off 只拒开启，不拒关闭**；且规范的开集合比审计括注的
`(create/enable)` **更宽**（含"编辑个人渠道实例"），这一条本轮**未收口**，见 §9。

## 4. 控制用例（防"一律拒绝"通过）

新文件 `tests/test_personal_switch_entry_point_enforcement.py`（17 例，全部为可观测效果：
行是否存在、列的值、拒绝码；不只看状态码）：

| 控制用例 | 断言的可观测事实 |
| --- | --- |
| `test_the_shared_create_lands_when_onboarding_is_on` | 同一次请求成功，且库里正好 1 行、`active=1`、`owner_user_id=本人`、`scope='user'` |
| `test_the_legacy_wrapper_and_the_shared_door_give_one_answer` | 同一个关闭值下旧包装与共用门给出**同一个** `capability_disabled`，且两门都不落行 |
| `test_the_shared_re_enable_lands_when_onboarding_is_on` | 同一次启用在开关开时把 `active` 从 0 变 1 |
| `test_disabling_stays_reachable_when_onboarding_is_withdrawn` | 开关关闭后停用仍 **200** 且 `active=0`——被拒的是开启方向，不是任务整条 |
| `test_the_private_agent_bind_lands_when_the_console_is_on` | 同一次 bind 产出 `private_owner_user_id`/`origin` 正确的绑定行 |
| `test_the_console_create_path_lands_when_the_console_is_on` | 控制台创建路径在开关开时落地：roster 有行 + 绑定行 |
| `test_the_console_switch_never_reaches_a_tenant_connection` | 总开关关闭时**管理员仍能**创建公共连接，库里出现唯一的 `scope='tenant'` 行 |
| `test_the_members_own_channel_write_is_refused_by_the_console_switch` 的对照见上 | 成员自己的开关关闭时被拒、且不落行 |
| `test_the_page_offers_create_again_with_the_switches_on` | 开关全开时 `create`/`update` 都回来，`switches` 两块都为 `True` |
| `test_the_withdrawal_does_not_narrow_the_administrators_page` | 管理页 `scope='tenant'`、**不带** `switches` 块、`create` 仍 `True`（钉住 §2.3 的 `scope != "self"` 短路） |

其中三条专门防"一律拒绝"：公共连接仍可创建（不是把渠道写全关）、停用仍可用（不是只留下一半）、
管理页动作不被成员开关收窄（不是把开关外溢到别的对象范围）。

## 5. 测试与运行结果

### 5.1 新增文件

`tests/test_personal_switch_entry_point_enforcement.py`（**新增**，17 例）。用真实
`build_web_app()`（`web_app` fixture）+ `tests/_helpers.WebAppHarness`，开关通过
`config.conf().update(...)` 改变——读的正是每次请求都会读的那份配置映射，因此不需要重启，
也不会有"只在启动时读一次"的缓存让用例与代码路径脱节。渠道写入经真实路由策略与 handler，
私有 Agent 走真实 `PrivateAgentService`（roster 用记录器，避免写开发者自己的 `config.json`）。

### 5.2 任务指定命令

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
    tests/test_personal_switch_entry_point_enforcement.py \
    tests/test_personal_instance_policy.py \
    tests/test_personal_instance_target_repair.py \
    tests/test_tenant_channel_member_access.py \
    tests/test_personal_channel_console.py \
    tests/test_capability_matrix.py -q -p no:randomly
→ 218 passed, 28 subtests passed
```

### 5.3 邻接套件（本轮改动可能外溢的面）

```
$ ... pytest tests/test_personal_capability_switches.py tests/test_personal_console_pages.py \
    tests/test_tenant_channel_console_scope.py tests/test_personal_delivery_drill.py \
    tests/test_scope_consistency_acceptance.py tests/test_skill_public_surface_scope.py \
    tests/test_menu_grant_enforcement.py tests/test_unified_agent_creation.py \
    tests/test_private_agent_quota.py tests/test_personal_console_acceptance.py \
    tests/test_plan_3_1_joint_acceptance.py -q -p no:randomly
→ 173 passed
```

（§7 记录的 2 例 `test_personal_channel_console.py` 中间态失败在本文件定稿时已由并发 agent 侧消失：
该文件单独跑为 `86 passed, 5 subtests passed`，比我初读时的 55 例更多——对方在补齐而不是回退。
我未改动该文件。）

### 5.4 修复前 / 修复后的可复跑对照（同一新文件，隔离沙箱）

为了能自由回退守卫而不碰并发编辑中的共享 `auth/service.py`，对照在
`git worktree` 快照 + 工作树覆盖的隔离副本里跑（`/tmp/cowagent-mut`，
`import auth.service.__file__` 已核为该副本）。同一份 17 例文件：

```
# 修复前（五个守卫全部回退）
→ 9 failed, 8 passed
   FAILED test_the_shared_create_is_refused_when_onboarding_is_withdrawn
   FAILED test_the_legacy_wrapper_and_the_shared_door_give_one_answer
   FAILED test_the_shared_re_enable_is_refused_when_onboarding_is_withdrawn
   FAILED test_the_members_own_channel_write_is_refused_by_the_console_switch
   FAILED test_the_members_own_re_enable_is_refused_by_the_console_switch
   FAILED test_private_agent_creation_is_refused_when_the_console_is_withdrawn
   FAILED test_the_console_create_path_refuses_before_it_clones_anything
   FAILED test_the_members_channel_page_does_not_advertise_a_refused_create
   FAILED test_a_withdrawn_onboarding_switch_withdraws_the_advertised_create
   passed  = 8 例全部是控制用例（开关开时落地、停用仍可用、管理员页不收窄、公共连接不受影响）

# 修复后（同一沙箱，同一文件）
→ 17 passed
```

即：**转红的 9 例逐条对应两条缺陷的断言，8 例控制用例在两种状态下都绿**——
"一律拒绝"不可能通过这组测试。

## 6. 变异验证（守卫逐个关闭 → 转红 → 恢复 → 转绿）

下表在隔离沙箱里于 2026-09-16 重跑确认（同一份 17 例文件；每个变异的红/绿都为实测摘要行）：

| # | 守卫 | 变异方式 | 结果（实测） |
| --- | --- | --- | --- |
| M1 | `_enforce_personal_instance_policy` 的分片开关（`auth/service.py:7912`） | 该行删除 | `3 failed, 14 passed`：共用新建被拒 / 旧包装与共用门同答案 / 共用重新启用被拒。恢复后 `17 passed` |
| M2 | 同函数的控制台总开关（`:7911`） | 该行删除 | `2 failed, 15 passed`：成员自己的渠道新建 / 重新启用被控制台开关拒绝。恢复后 `17 passed` |
| M3 | `bind_private_agent_with_quota` 的开关（`:1464`） | 该行删除 | `1 failed, 16 passed`：`bind_private_agent_with_quota` 的 API 面拒绝（控制台路径仍被 §2.4 早退拦住）。恢复后 `17 passed` |
| M4 | `private_agent.py` 的早退（`:209`） | 该行删除 | `1 failed, 16 passed`：`test_the_console_create_path_refuses_before_it_clones_anything`。拒绝码仍是 `('capability_disabled', 403)`，实测差异只在 roster：`assert ([], ['mine-usr_…']) == ([], [])` —— 先 clone、再由 `_compensate` 删除，正是这条断言要防的形状。恢复后 `17 passed` |
| M5 | `admin.channels` 投影的开关短路（`:3576-3580`） | `or all(switches.values())` → `or True` | `2 failed, 15 passed`：两个投影用例。恢复后 `17 passed` |
| M6 | 五个守卫同时回退（= 上文 §5.4 的"修复前"） | 全部删除 | `9 failed, 8 passed` |

恢复后复核：`rg -n "MUTATION" auth/service.py agent/private_agent.py` 无命中，
沙箱与主工作树的新文件均 `17 passed`——即上表的红/绿由守卫本身造成，不是环境噪声。

## 7. 判为**非本改动**的中间态失败（已证明）

写作期间 `tests/test_personal_channel_console.py` 曾出现 2 例失败
（`ReadinessTests::test_configuration_can_narrow_the_ready_set_but_never_widen_it` 断言
`personal_channel_ready("telegram") == (True, "")`，实测 `(False, 'no_inbound_identity')`；
`WorkbenchProjectionTests::test_the_type_list_is_the_declarations_narrowed_by_the_tenant` 同类）。
判为非本改动，证据两条：

1. **原因码来源**：`no_inbound_identity` 由并发 agent 正在改的
   `channel/channel_instances.py::personal_channel_ready`（`if not inbound_identity_admissible(ctype)`）
   产生；该文件本轮**零改动**，我的改动不可能造出这个字符串。
2. **反证实验**：在 `cbdb9884` 全新 worktree 中，只把工作树的 `channel/channel_instances.py`
   覆盖进去（其余全为 HEAD），
   `test_configuration_can_narrow_the_ready_set_but_never_widen_it` **同样转红**；
   而该 worktree 的 HEAD 版本 `tests/test_personal_channel_console.py` 是 `55 passed`。

同一窗口内还观察到 `tests/test_personal_console_acceptance.py` 的 4 例 `/api/personal/resources`
404（该地址已从 `channel/web/route_registry.py` 退役，本轮对该文件零改动），以及
`test_plan_3_1_joint_acceptance.py` 的 2 例污染失败。**这些在本文件定稿时已随并发 agent 的推进消失**：
§5.2 / §5.3 的最终结果全部通过，我未改动上述任何文件。

## 8. 并发编辑复核（另一 agent 的标记存活）

每次编辑前后都复核，编辑完成后再次确认（`rg`，2026-09-16，定稿时刻）：

```
$ rg -n "^def _require_skill_write_scope|^def _resolved_skill|^def _annotate_skill_actions" \
    channel/web/web_channel.py
670:def _require_skill_write_scope(ctx: "Optional[RequestContext]", agent_id: str) -> None:
707:def _resolved_skill(service, name: str, resource_id: str):
8873:def _annotate_skill_actions(ctx: "Optional[RequestContext]",

$ rg -c memoryEntryEditable channel/web/static/js/console.js
3
```

四个标记均存活。**行号在本轮内持续漂移**（`_require_skill_write_scope` 从 649→670、
`_resolved_skill` 686→707、`_annotate_skill_actions` 8852→8873，均在 `web_channel.py` 内），
这正是对方仍在编辑同一文件的直接证据；我本轮**未编辑** `web_channel.py`、`route_registry.py`、
`console.js` 中的任何一行。**没有发生覆盖**，无需从 git 恢复任何内容。

## 9. 未覆盖（含原因）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 共用**编辑**路径（`/api/tenant/channels/<id>`）在关闭期仍可改名/换绑/换密钥 | **未收口（同类第三条门，已知缺口）** | 规范 `console-navigation-availability/spec.md:48` 把"编辑个人渠道实例"也列入开启类拒绝集合，旧包装 `update_personal_channel_instance`（`auth/service.py:9409`，require `:9422`）确实拒绝，共用 `update_tenant_channel_instance` 不拒。本轮不收口的理由：① 审计 R1 的最小修法只覆盖 `_enforce_personal_instance_policy`（该函数不在编辑路径上）；② 改动要落在 `update_tenant_channel_instance` 的 `if row["scope"] == "user":`（`:8556`）里，而该处注释明写"纯改名仍复核 owner，但**不**复核 target，因为拒绝改名会让一条不可用行变得不可修复、不可关闭"（`:8557-8562`）——是否用部署开关覆盖这条修复路径属行为取舍，应由变更负责人裁决，不宜由本轮顺手改掉 |
| 旧包装的 `update_personal_channel_instance`（编辑） | 与共用门**不同答案**（旧拒、新可） | 同上；旧包装的行为本就符合规范，缺口只在共用门 |
| `start_personal_channel_binding`（发起绑定挑战）经共用门的路径 | 未核 | 该动作目前只在旧包装（`auth/service.py:9579`，require `:9602`）与个人自服务面出现；本轮未逐条确认是否存在共用的等价入口，故标未覆盖 |
| 真实提供方往返验收（本人连接/公共连接） | **未覆盖（受阻）** | 与 `evidence/7-1-runtime-preflight.md` 同一阻塞：`COW_CREDENTIAL_MASTER_KEY` 之外还需真实 `*_app_id`/`*_secret`、≥2 真实账号与装配 `_channel_mgr` 的运行进程。本轮只证到"行落库/不落库、开关读/不读、投影报什么"，未证任何厂商往返 |
| 浏览器端真实点击（控制台按钮隐藏） | **未覆盖** | 本轮的投影断言是服务端投影的字段值；前端如何渲染该字段未在浏览器里复验（`console.js` 属并发编辑文件，本轮零改动） |
| 并发编辑下的行号稳定性 | **部分失效风险** | 写作期间 `auth/service.py` 被并发编辑（本轮内即位移数十行）。所有引用同时给出函数名/常量名，复核时按名定位 |

## 10. 可复跑证据

```bash
# 1) 两条缺陷的可达性（修复前探针，与新用例同形；见 §5.4）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_personal_switch_entry_point_enforcement.py -q -p no:randomly

# 2) 守卫是否存在（按名定位，抗行号漂移）
rg -n "def _enforce_personal_instance_policy" auth/service.py
rg -n -A2 'def _enforce_personal_instance_policy' auth/service.py   # 首两行即两个 require_...
rg -n -B8 'require_personal_capability\("member_personal_console"\)' auth/service.py
rg -n "member_personal_console" agent/private_agent.py
rg -n "or all\(switches.values\(\)\)" auth/service.py

# 3) 关闭方向仍然可达（不是"一律拒绝"）
rg -n -A4 'def set_personal_channel_instance_active' auth/service.py     # 旧包装只在 active 时查开关
rg -n -A6 'def revoke_personal_channel_credentials' auth/service.py      # 撤销不查能力开关

# 4) 变异验证（§6）：在隔离副本里逐个把守卫改回"无条件放行"，
#    重跑第 1 条命令应转红，恢复后 17/17 绿。共享工作树不要动——
#    本轮的沙箱做法：git worktree add --detach /tmp/cowagent-mut HEAD，
#    再 rsync 工作树覆盖（.git/.venv/__pycache__ 排除），
#    并用 python -c "import auth.service; print(auth.service.__file__)" 确认导入指向沙箱。

# 5) 本轮命令与结果
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_personal_switch_entry_point_enforcement.py \
  tests/test_personal_instance_policy.py tests/test_personal_instance_target_repair.py \
  tests/test_tenant_channel_member_access.py tests/test_personal_channel_console.py \
  tests/test_capability_matrix.py -q -p no:randomly
# → 218 passed, 28 subtests passed
```

（以上均已在 2026-09-16 本轮实际执行。）

## 11. 第三条门（7.9）：共用**编辑**路径的开关强制

本节收口 §9 表首行登记的遗留项，也是 R1/R2 之后同类缺陷的**第三条门**（`tasks.md` 7.9）。

**范围与声明**

* 只改「共用编辑写入口上旧开关的强制点」与配套用例。**不改**入站、运行期合成、凭据契约集合、
  配额、治理停用、目录声明、页面投影（投影面的取舍见 §11.8 第 2 行）。
* 全部结论是**进程内**证据：真实 `build_web_app()` WSGI 应用 + 真实路由策略 + 真实 SQLite 写事务
  + 真实加密（`COW_CREDENTIAL_MASTER_KEY`）。
* `auth/service.py` 在本轮写作期间仍被其它 agent 并发编辑，下文行号是**读取时刻**快照
  （基准 `cbdb9884` + 工作树），所有引用同时给出函数名/常量名。本轮编辑为**定点替换**
  （无整文件重写），编辑前后都复核了邻接区域；并发标记存活见 §11.7。
* 判定基准是**代码强制**。凡「只在注释或规范里成立」的一律不记为已修。

## 11.1 缺口（复核）

| 门 | 关闭期行为（修复前） | 规范 |
| --- | --- | --- |
| 旧包装 `update_personal_channel_instance`（`auth/service.py:9447`，require `:9449`） | 拒绝（首行即 `require_personal_capability("personal_channel_onboarding")`） | 合规范 |
| 共用门 `update_tenant_channel_instance`（`:8488`） | **接受**改名/换绑/换密钥并落库 | `spec.md:202` 明列「新建/**编辑**个人渠道实例」属开启类写入 |
| 共用门的唯一生产调用者 `POST /api/tenant/channels/<id>`（`channel/web/admin_handlers.py:1136`） | 同上 | 同上 |

成员控制台编辑自己的连接走的**就是**共用门：`channel/web/static/js/console.js:15596`（`editing`
分支）与 `:15597`（创建分支）同页同函数，URL 都是 `/api/tenant/channels[/<id>]`。所以「同一次
点击、旧包装拒、共用门收」是一面两个答案，与本 change 反复消除的形状一致。

生产调用者已核（`rg 'update_tenant_channel_instance'` 排除 `tests/`，仅两处）：
`admin_handlers.py:1136`（共用门）与 `auth/service.py:9461`（旧包装转发）。即共用门的 HTTP 面
只有这一个入口，服务面只有旧包装一个转发者——没有第三条路径需要补闸门。

## 11.2 owner 与 operator 的区分（代码 + 实测，不猜）

`allow_owner` 是唯一区分，且它就是**成员本人自助面**：`admin_handlers.py:977
_function `_is_channel_owner_only(ctx)` 定义为「既非租户管理员、也非平台管理员」，三个写入口
（创建 `:1114`、编辑 `:1149`、启停 `:1184`）都用它填 `allow_owner`；旧包装则硬编码 `allow_owner=True`
（`auth/service.py:9468`）。服务内这一支先 `_personal_instance_row` 自证「行是我自己的
`scope='user'`」，另一支要求管理资格（`:8527-8530`）。故 **(a) = `allow_owner=True`**。

**关键裁决：任务描述的 (b)「运维/租户管理员治理编辑成员行」在本函数上不可达。** 实测
（§11.8 探针输出第 5 行）：

```
admin edits member's personal row (switches off): 403 forbidden
```

`_require_channel_instance_in_range`（`:8461`）经 `auth/object_scope.allows_channel_instance`
判定，`scope='user'` 行**只**由 owner 决定，管理资格不构成例外（`object_scope.py:159-173`：
「The owner check precedes the administrator exception」）。已有测试逐字钉住这一点：
`tests/test_tenant_channel_member_access.py::MemberWriteTests::test_the_administrator_reaches_both_ranges`
（管理员启停成员行 → 403）与
`tests/test_personal_agent_governance_metadata.py::GovernanceMetadataIsNotAReadTests::
test_stopping_does_not_grant_the_admin_the_configuration`（管理员编辑成员行 → `forbidden`，其
docstring 明写「管理员改写其他成员的本人连接 → 请求拒绝，治理停机仍只经独立治理动作」）。
真正的治理面是 `set_personal_instance_governance`（`:7995`）与只读治理投影
`list_personal_channel_instances_for_governance`（`:7996`），两者都**不**经本函数。

因此 operator 在本函数上可达的写只有两格：租户公共行、以及**管理员本人的**本人行；两格都由
`allow_owner=False` 分支服务，本轮零改动。逐格实测见下表（探针脚本见 §11.8，未纳入仓库）。

| 调用者 / 入参 | `allow_owner` | 关闭期实测（行属本人） |
| --- | --- | --- |
| 成员经共用门编辑自己的行 | `True` | 403 `capability_disabled`（本轮修复） |
| 旧包装转发（`/api/personal/channels/<id>` `action=update`） | `True` | 403（旧包装首行已拒，行为未变） |
| 租户/平台管理员编辑**公共**行 | `False` | 200，照常改名（控制用例） |
| 租户/平台管理员编辑**他人本人行** | `False` | 403 **`forbidden`**（守行范围，非本开关） |
| 租户/平台管理员编辑**本人的本人行** | `False` | 200（**不**受开关约束；取舍登记见 §11.8 第 1 行） |

## 11.3 「解绑」是否经此编辑路径：**否**（三条互相独立的核对）

1. **解绑有自己的方法与路由**：`unlink_personal_channel_instance`（`auth/service.py:9853`），
   生产只被 `channel/web/web_channel.py:9521` 调用（旧个人路由 `POST /api/personal/channels/<id>`
   的 `action=unlink`），**不**调用 `update_tenant_channel_instance`。它只做 `_require_member`
   + 本人归属 + `_claim_personal_instance_version`，不吃任何能力开关。已加用例
   `test_unlinking_stays_reachable_when_the_switch_is_withdrawn`（双开关关闭 → 仍成功、行
   `active=1`、名字未变）。
2. **共用门没有「解绑」动作**：`route_registry.py:160-162` 只有 `POST /api/tenant/channels`、
   `/active`、`/<id>`（编辑）三条；共用面不存在可被误伤的解绑入口。
3. **本人行也不允许经此路径清空目标**：`auth/service.py:8603-8613` 对 `scope='user'` 的**具名
   空目标**一律 `personal_agent_required`/400（注释明写「The public path is untouched: a shared
   instance may deliberately be unbound」）。所以「把连接与智能体解绑」在本人侧本就不经此路径；
   唯一经此路径成立的解绑是**公共行**的（`tests/test_personal_instance_target_repair.py::
   test_a_public_target_is_still_optional_and_still_unbindable`），而它走 `allow_owner=False`
   分支，不被新守卫碰到。

另两项规范保证的收回动作同样各有其路：撤销凭据 `revoke_personal_channel_credentials`（`:9508`）、
停用 `set_personal_channel_instance_active`（`:9477`；旧包装只在 `if active:` 内查开关，`:9493`）。
两者都在本函数之外，§4/§5 的既有控制用例继续绿，另加
`test_disabling_the_members_own_instance_is_still_reachable`（双开关关闭 → 仍 200、`active=0`）。

## 11.4 守卫

`auth/service.py:8523-8537`（`update_tenant_channel_instance` 的 `if allow_owner:` 分支），
docstring 同步补一段（`:8512-8519`）：

```python
        if allow_owner:
            # The owner rule answers *first*: a foreign or unknown id stays
            # refused for what it is, and the withdrawal never becomes a probe
            # for which ids exist (spec: 关闭开关不撤去 owner 检查). Only then
            # does the member's own surface meet the switches — ``allow_owner``
            # is exactly that surface (task 6.1; the operator branch below never
            # sets it), so an operator editing the tenant's public rows, or their
            # own, is deliberately not narrowed by the member's switch.
            self._personal_instance_row(tenant_id, actor_user_id, instance_id)
            # Both switches behind the page are read, for the same reason the
            # create/enable guard reads both: the page reports both, and either
            # being off means the member's surface is off. Refused before the
            # transaction opens, so no field, version or credential is touched.
            self.require_personal_capability("member_personal_console")
            self.require_personal_capability("personal_channel_onboarding")
```

四点理由（与 §2.1 的裁决同源）：

1. **复用既有权威**：`require_personal_capability`（`:1395`），不新增第二个事实源；拒绝码/状态
   `capability_disabled`/403 与旧包装、`_enforce_personal_instance_policy` 逐字同形。
2. **位置 = 所有权证明之后、`_require_recent_password` 与 `_tx()` 之前**。所有权规则仍先作答
   （规范「关闭开关不撤去 owner 检查」的本人行版本：同事行仍是 `forbidden`、不存在的 id 仍是
   `not_found`，撤权不会被读成「这个 id 存不存在」的探针）；同时拒绝先于任何字段/版本/凭据版本
   写入——「不产生部分写入」由**位置**保证，而不是靠回滚补偿（§11.5 第 1/6 条钉住）。
3. **只作用于 `allow_owner=True`**，即成员本人自助面——这是任务给定的 (a)，也是 §2.3
   「部署开关只收窄成员自助面」的同批先例。
4. **为什么读两个开关**：与 §2.1 同一理由——同页 `switches` 块同时报两个开关
   （`personal.channels` 同时挂在 `member_personal_console` 与 `personal_channel_onboarding`，
   `auth/policy.py:589-596`），投影的 `create` 读 `all(switches)`，同页 create/enable 守卫也读
   两个；只读分片开关会留下新的「同页 create 与重新启用被拒、编辑被收」的不对称。相对旧包装
   （只读分片开关）这是**收窄**而非放宽，且不改出箱默认（两开关默认皆 True）。若复核者要更窄的
   读法，删 `:8536` 一行即可——旧包装契约仍被 `:8537` 满足。

## 11.5 控制用例（防「一律拒绝」通过）

新用例追加在既有文件 `tests/test_personal_switch_entry_point_enforcement.py`（17 → **28** 例，
全部断言可观测效果：行是否存在、列的值、凭据版本计数、拒绝码）：

| # | 用例 | 断言的可观测事实 |
| --- | --- | --- |
| 1 | `test_the_members_own_edit_is_refused_when_onboarding_is_withdrawn`（`:514`） | 开关关闭 + 共用门改名 → 403 `capability_disabled`，且**库里 `display_name` / `version` / `agent_id` 三者均未变** |
| 2 | `test_the_members_own_edit_lands_when_onboarding_is_on`（`:531`） | 同一请求、开关开 → 200，`display_name` 变、`version+1`（控制） |
| 3 | `test_the_members_own_edit_is_refused_by_the_console_switch`（`:541`） | 总开关关闭 → 403 `capability_disabled`，行不动 |
| 4/5 | `..._retarget_is_refused_when_onboarding_is_withdrawn`（`:558`）/ `..._retarget_lands_when_onboarding_is_on`（`:571`） | 换绑被拒时 `agent_id` 未移动；开关开时确实移动（控制） |
| 6/7 | `..._rotation_is_refused_when_onboarding_is_withdrawn`（`:581`）/ `..._rotation_lands_when_onboarding_is_on`（`:598`） | 换密钥被拒时 `credential_versions` 仍为 1（无部分写入）；开关开时变 2（控制） |
| 8 | `test_the_operators_edit_still_lands_when_the_switches_are_withdrawn`（`:607`） | **反过度拦截控制**：两开关皆关时，租户管理员经共用门新建**公共**行（200、`scope='tenant'`）并改名（200、落库可见） |
| 9 | `test_the_owner_rule_still_answers_a_foreign_id_when_withdrawn`（`:638`） | 开关关闭时：同事的本人行 → 403 **`forbidden`**（不是 `capability_disabled`）；不存在的 id → 404 `not_found`。钉住守卫**位置** |
| 10 | `test_unlinking_stays_reachable_when_the_switch_is_withdrawn`（`:664`） | 解绑在双开关关闭后仍成功，行 `active=1`、名字未变 |
| 11 | `test_disabling_the_members_own_instance_is_still_reachable`（`:686`） | 停用在双开关关闭后仍 200、`active=0` |

「一律拒绝」不可能通过：2/5/7 是同一写法的成功控制，8 是 operator 面控制（且顺带复证 §2.3 的
`scope != "self"` 短路），9 是位置控制，10/11 是规范「收回自有对象仍可用」的控制。新增的
`_World.edit()`（`:141`）、`credential_versions()`（`:181`）、`colleague_instance()`（`:193`）
是观察夹具，未改动既有 17 例。

## 11.6 变异验证（隔离沙箱，守卫逐个改回放行 → 转红 → 复原 → 转绿）

沙箱做法与 §10 第 4 条一致：`git worktree add --detach /tmp/cowagent-mut7 HEAD`，再 rsync 工作树
覆盖（排除 `.git`/`.venv`/`__pycache__`），并用
`python -c "import auth.service, tests.test_personal_switch_entry_point_enforcement as t"` 确认
导入指向沙箱（实测输出 `/private/tmp/cowagent-mut7/...`）。共享工作树**未**被改回。

| # | 变异 | 结果（实测） |
| --- | --- | --- |
| M7 | 删掉 `:8536-8537` 两行 require | `4 failed, 24 passed`：失败正是 1/3/4/6 四条拒绝断言；恢复后 `28 passed` |
| M8 | 把两行 require 移到 `_personal_instance_row` **之前** | `1 failed, 27 passed`：只有第 9 条转红（同事行与不存在 id 被答成 `capability_disabled`）；恢复后 `28 passed` |

**修复前 / 修复后（同一份 28 例文件）**：修复前 = M7 的 `4 failed, 24 passed`；修复后 = `28 passed`。
四个失败逐条对应本节的拒绝断言，24 条控制用例在两种状态下都绿。M8 另证第 9 条有独立价值
（只删守卫时它不红，只有挪位置时才红）。复原复核：沙箱内
`rg -n "MUTATION" auth/service.py` 无命中，且 `28 passed`。

## 11.7 命令与结果

```
# 1) 任务指定命令（8 个文件）
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
    tests/test_personal_switch_entry_point_enforcement.py \
    tests/test_tenant_channel_member_access.py tests/test_personal_instance_policy.py \
    tests/test_personal_channel_console.py tests/test_tenant_channel_instances_service.py \
    tests/test_tenant_channel_http.py tests/test_personal_console_transport.py \
    tests/test_personal_console_acceptance.py -q -p no:randomly
→ 309 passed, 5 subtests passed

# 2) 邻接套件（本改动可能外溢的面：旧包装、目标修复、治理、开关、投影、范围）
$ ... pytest tests/test_personal_instance_target_repair.py \
    tests/test_personal_agent_governance_metadata.py tests/test_personal_capability_switches.py \
    tests/test_channel_type_admissibility.py tests/test_tenant_channel_startup_synthesis.py \
    tests/test_tenant_channel_required_credentials.py \
    tests/test_personal_console_multi_tenant_authorization.py tests/test_personal_scan_scope.py \
    tests/test_scan_onboarding_state.py tests/test_tenant_channel_console_scope.py \
    tests/test_object_scope.py tests/test_capability_matrix.py -q -p no:randomly
→ 238 passed, 26 subtests passed

# 3) 新文件单独
→ 28 passed
```

无失败，故无需分类「预存在 / 本改动」。**无**任何一例是本轮引入后需要判为预存在的。

**并发复核（编辑前后各一次 `rg`，2026-09-16）**：

```
$ rg -n "^def _require_skill_write_scope|^def _resolved_skill|^def _annotate_skill_actions" \
    channel/web/web_channel.py
670:def _require_skill_write_scope(...)
707:def _resolved_skill(service, name: str, resource_id: str):
8873:def _annotate_skill_actions(...)
$ rg -c memoryEntryEditable channel/web/static/js/console.js            → 3
$ rg -n "^INBOUND_IDENTITY_STAMPING_TYPES|^def inbound_identity_admissible" \
    channel/channel_instances.py
122:INBOUND_IDENTITY_STAMPING_TYPES = frozenset({
129:def inbound_identity_admissible(channel_type: str) -> bool:
$ rg -n "require_personal_capability" auth/service.py
1456 user_private_agent_management / 1464 member_personal_console / 5780 member_personal_console /
7911-7912（§2.1 两行）/ 8536-8537（本轮新增）/ 9429 / 9460 / 9493（if active:）/ 9640
```

七个标记全部逐字存活；本轮新增后 `auth/service.py` 通过 `ast.parse`（10688 行），
`update_tenant_channel_instance` / `set_tenant_channel_instance_active` /
`create_tenant_channel_instance` / `update_personal_channel_instance` /
`set_personal_channel_instance_active` / `revoke_personal_channel_credentials` /
`unlink_personal_channel_instance` / `_enforce_personal_instance_policy` /
`_personal_instance_row` 全部在位。**未**从 git 恢复任何内容，**未**发生覆盖。

## 11.8 未覆盖（含原因）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 管理员**本人的**本人行编辑不受开关约束 | **已知取舍，已实测** | 探针（`allow_owner=False`）：管理员本人 create 开关开 → 200（`scope='user'`、owner=管理员）；同一 create 开关关 → 403 `capability_disabled`；同一**已有**行的编辑开关关 → **200**。即创建侧（`_enforce_personal_instance_policy` 只按 `scope=='user'` 判、不辨演员）比编辑侧严一格。本轮按任务「gate only (a)」不动它：① §2.3 的 `scope != "self"` 短路确立了同批先例——部署开关只收窄成员自助面，不收窄管理员面；② 该格不产生权限提升（管理员本身有权），且规范保证的收回动作全不受影响。若要收口，最小改法是把守卫下沉到事务内 `if row["scope"] == "user":` 块（现 `:8578` 附近），但那与「只 gate (a)」相抵、且会把开关外溢到管理员面，故登记给变更负责人裁决，不由本轮单方面改掉 |
| 页面投影 `actions.update` 未随开关收窄 | **未收口（已核影响面）** | 现状 `entry["actions"]["update"] = allowed`（`:3581`），开关关闭期成员页仍报 `update: True` 而写路径新拒——与 §2.3 收口 `create` 之前的形状同源。**实测影响面为零的 UI 后果**：`rg` 全量 JS 后，`actions.create` 与 `actions.update` 在 `channel/web/static/js/*.js` 中**无任何读取点**；成员渠道卡片的「编辑」「停用/启用」按钮是两个无条件按钮（`console.js:15129-15138`），故收窄该字段既不会修掉「可点但被拒」，也不会误伤停用按钮（停用不走该字段）。本轮不收口：① 任务是写路径；② 该处是并发 agent 刚编辑过的区域（§2.3 的 `:3570-3580`）；③ 该字段同时覆盖 operator 面，真要收窄必须照 §2.3 加同一个 `scope != "self"` 短路，属投影面第二轮的行为取舍。**未覆盖的验收面**：成员在开关关闭期从浏览器点「编辑」会看到被拒的错误卡（预期行为），但该交互未在浏览器里复验 |
| 本人行的「改名以修复不可用行」在开关关闭期被一并拒绝 | **行为已变更（与旧包装一致）** | `update_personal_channel_instance` 旧包装本就整条拒绝（首行 require），故这不是本轮引入的收窄，而是把共用门对齐旧契约。规范 `spec.md:202` 的「关闭不得使成员无法收回自有对象」列举的是撤销凭据/解绑/停用/删除/清空，不含「改名」；`:8557-8562` 注释里「拒绝改名会让一条不可用行变得不可修复」讲的是**开关开启**时的修复路径，未涉及关闭期。关闭期仍可停用、撤销、解绑（§11.3 三条用例） |
| `start_personal_channel_binding`（发起绑定挑战）经共用门的路径 | 未核（沿用 §9 登记） | 该动作目前只在旧包装（`:9640`，require 前后）与个人自服务面出现；本轮未逐条确认是否存在共用的等价入口。它与本次收口的编辑路径无交集 |
| 真实提供方往返验收 | **未覆盖（受阻）** | 与 §9 / `evidence/7-1-runtime-preflight.md` 同一阻塞：缺真实 `*_app_id`/`*_secret`、≥2 真实账号与装配 `_channel_mgr` 的运行进程。本轮只证到「行落库/不落库、凭据版本不追加、开关读/不读」，未证任何厂商往返 |
| 真实浏览器点击（开关关闭期成员点「编辑」的呈现） | **未覆盖** | 同 §9：`console.js` 属并发编辑文件，本轮零改动；本轮的拒绝证据是服务端可观测效果 |
| 并发编辑下的行号稳定性 | **部分失效风险** | 写作期间 `auth/service.py` 被并发编辑。所有引用同时给出函数名/常量名，复核时按名定位 |

## 11.9 可复跑证据

```bash
# 1) 本轮新用例（开关在编辑路径上的三条门一并覆盖）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_personal_switch_entry_point_enforcement.py -q -p no:randomly
# → 28 passed

# 2) 守卫是否存在（按名定位，抗行号漂移）
rg -n -A14 'def update_tenant_channel_instance' auth/service.py   # if allow_owner: 分支内的两行 require
rg -n 'require_personal_capability\("member_personal_console"\)' auth/service.py   # 4 处：1464/5780/7911/8536

# 3) 区分 owner 与 operator 的事实源
rg -n -A12 'def _is_channel_owner_only' channel/web/admin_handlers.py
rg -n -A18 'def allows_channel_instance' auth/object_scope.py      # scope='user' 只由 owner 决定

# 4) 解绑不经此路径
rg -n "unlink_personal_channel_instance|revoke_personal_channel_credentials" --glob '!tests/**' .
rg -n -A6 'def update_personal_channel_instance' auth/service.py   # 旧包装 locate
rg -n 'a personal channel instance requires one of your own private' auth/service.py

# 5) 变异验证（§11.6）：沙箱 git worktree add --detach /tmp/cowagent-mut7 HEAD
#    + rsync 工作树覆盖（.git/.venv/__pycache__ 排除），
#    删守卫 → 4 失败/24 通过；挪守卫到所有权证明之前 → 1 失败/27 通过；复原 → 28 通过。
#    共享工作树不要动。
```

（以上均已在 2026-09-16 本轮实际执行。）
