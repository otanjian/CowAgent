# 5.1（前半）记忆目标集合由服务端下发

对应 `tasks.md` 5.1 的第一句与两个要求条款中的「目标集合 SHALL 在服务端过滤」。
规范依据 `specs/database-memory-console/spec.md` 的两条 MODIFIED requirement：
「记忆浏览兼容入口明确个人和智能体作用域」、「记忆浏览与个人写入使用同一内容版本」。

**本文件只记录 5.1 已完成的那一半。** 剩下的另一半（智能体作用域的编辑/删除/清空）
尚未实现，见文末 §5。

## 1. 缺陷形状：候选来自「能用」范围，不是「能管理」范围

记忆页的目标选择器由 `agentCatalog` 构建（`console.js:renderMemoryAgentSelect`）。
`agentCatalog` 是**使用**范围：对普通成员而言它包含本租户的共享智能体。

于是成员在记忆页看到 `shared-agent` 并可以选中它，请求发到 `/api/memory?agent_id=shared-agent`
后被服务端拒绝——「卡片可点、发送被拒」。共享智能体的记忆是**租户资源**，管理它需要租户
管理资格（`allows_agent_memory` = `allows_agent(action=MANAGE)`），聊天使用不构成管理资格。

同一形状在任务 6.2 已经修过一次（渠道目标候选）。这里按同样的方式修：**服务端下发合法
目标集合**，界面只渲染它。

## 2. 第二个缺陷：个人记忆域在界面上不可达

`viewingMemoryAgentId()` 是 `memoryAgentId || activeAgentId || defaultAgentId`，并且选择器
只列出智能体——**没有「我的记忆」这一项**。

即使列出了，用 `''` 表示它也会失败：`''` 在控制台里的含义是「还没选」，会被兜底成当前
智能体。两个含义不能共用一个值，否则个人域永远选不中——而个人域正是**没有私有智能体的
成员唯一能到的地方**。

## 3. 实现

### 服务端（`channel/web/memory_console.py`）

`target_list(ctx)` 随列表响应下发 `targets`：

* 首项恒为 `{"kind": "personal", "value": "personal", "scope": "personal"}`；
* 其余来自 `_iter_tenant_agents(ctx, action=MANAGE, include_disabled=True)`，每项带
  `agent_id` / `name` / `scope`（`private_agent` 或 `shared`）/ `enabled`。

`action=MANAGE` 是关键：与读取路径 `resolve_target` → `allows_agent_memory` 用**同一个
判据**，所以下发的候选与请求的裁决不可能互相矛盾——这正是「目标集合在服务端过滤」句所
要求的，也是它值得下发的原因。

`include_disabled=True` 是刻意的判断：**停用拒绝的是新流量，不是已存内容**。智能体停用
后它的记忆仍在盘上，所有者仍应能读取与清理，所以它留在集合里（与 6.2 的渠道候选相反——
那里目标要接收新消息，停用目标不是合法选择），但带 `enabled: false` 如实说明状态。

`scope` 由归属判定（`binding.private_owner_user_id == ctx.user_id`），不由角色名判定。

### 前端（`channel/web/static/js/console.js`）

* `MEMORY_PERSONAL = 'personal'`：个人域是**一个被选中的目标**，与 `''`（未选）区分开；
* `memoryTargets`：列表响应里的服务端集合；
* `memoryTargetQuery()`：把所选目标翻译成 `scope=personal` 或 `agent_id=...`，读取与正文
  两个请求共用，两处不可能写出不同的目标；
* `memoryTargetOptions()`：服务端集合存在时以它为准（**不接受**目录里的未下发项）；集合
  为空（旧后端）时退回 `personal + agentCatalog`，保留旧行为而不是把选择器清空；
* 入口的「目标已被删除就丢弃」守卫跳过 `MEMORY_PERSONAL`，否则个人域会在每次进入时被清掉。

i18n 新增 `memory_target_personal`（三语），快照同步。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_memory_console_scope.py tests/test_personal_memory_console.py \
  tests/test_personal_memory_scope_protocol.py tests/test_user_personal_memory.py \
  tests/test_personal_memory_tool_execution.py tests/test_memory_storage_tenant_scope.py \
  tests/test_memory_global_config.py tests/test_doc_edit.py -q -p no:randomly
→ 155 passed
```

新增于 `tests/test_memory_console_scope.py`：

* `test_the_target_set_names_only_domains_the_caller_may_address` —— 成员得到
  `personal + alice-agent`，**没有** `shared-agent`（他可与之聊天）也**没有** `bob-agent`；
* `test_an_administrator_target_set_holds_the_shared_agents_and_their_own` —— 管理员得到
  `shared-agent`（`scope=shared`），但没有他人私有对象；
* `test_every_offered_target_is_one_this_caller_can_actually_read` —— **下发 ⇔ 可读**，
  两个方向一起断言：每个下发的目标读取都 200，未下发的 `shared-agent` 读取被拒。
  只断言其一的话，「下发了一个读不到的目标」这种缺陷仍然漏过；
* `test_a_stopped_agent_stays_a_memory_target_and_says_so` —— 停用对象仍在集合内、
  `enabled` 为 `false`，且其记忆仍可读。这条同时钉住了 `include_disabled=True` 这个选择；
* fixture 新增 `set_agent_enabled`：改的是注册表真正读的那份 roster 并丢缓存，
  改别的文件会对着过期 roster 断言。

```
node --test tests/test_memory_target_picker_frontend.cjs tests/test_console_i18n_parity.cjs \
  tests/test_admin_home_frontend.cjs
→ 16 passed
```

`tests/test_memory_target_picker_frontend.cjs`（新建，6 条）：服务端集合优先且拒绝目录里
未下发的项、个人域是可选项而非空选择、选定智能体寻址该智能体、未选时兜底当前智能体、
旧后端仍给出可用选择器、停用对象不消失。

## 5. 未完成：5.1 的后半与 5.2/5.3

规范同一句还要求「提供同一列表、分页、类别、内容**和管理动作**」，以及
「编辑、删除和清空 SHALL 在目标范围内重验权限、版本和 generation」。
当前实情：

* **个人域**的编辑/删除/清空已存在，走 `/api/memory/personal` 的动作
  `save`/`delete`/`clear`/`retry_index`，委托 `agent/memory/personal.py`；
* **智能体域**（本人私有智能体、管理员共享）**没有**编辑/删除/清空：
  `agent/memory/service.py:MemoryService.dispatch` 只支持 `list` 与 `content`
  （`service.py:152`），内容编辑目前经平台文件接口（`memoryEditor` + `rel_path`）。

所以 5.1 未完成的部分是：为智能体域补齐 `save`/`delete`/`clear`，并保证目标范围内重验
权限、版本/generation、防逃逸、索引屏蔽与重试，且「系统只读类别不因角色变化转为可写」。
这是新写入能力，不是接线，故未在本轮实现。5.2（收敛到同一领域服务）、5.3（验收）依赖它。
`tasks.md` 中 5.1 保持未勾选。
