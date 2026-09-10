# 个人会话与个人记忆 — 验收记录（沙箱真实实例）

对应 `openspec/changes/personal-conversation-and-memory` 的 **7.3 / 7.4**。

验收对象是一个**真实实例**：真实 `app.py` 进程、真实 HTTP 控制台、真实
`identity.db`、真实租户共享根与真实 SQLite 索引。它不是单元测试替身，也不写
任何生产数据 —— 数据根与租户根都落在 `/tmp` 沙箱里。

复现脚本：`scripts/acceptance_personal_context.py`。

---

## 1. 环境

| 项 | 值 |
| --- | --- |
| 进程 | `COW_DATA_DIR=/tmp/cow-acc COW_WEB_PORT=9898 python app.py` |
| 身份模式 | `identity_mode=database` |
| 数据根 | `/tmp/cow-acc`（`config.json` / `identity.db`） |
| 租户共享根 | `/tmp/cow-tenants/acc` |
| Agent 工作区 | `/tmp/cow-acc/agents/{alpha,beta}` |
| 账号 | `alice`（租户管理员）、`bob`（普通成员） |
| Agent | `alpha`（租户默认）、`beta`（对照） |

两个 Agent 的绑定均为**租户共享**（`private_owner_user_id IS NULL`），
`resolved_default_agent_id` 与 `tenant_default_agent_id` 都为 `alpha`。

## 2. 复现方式

```bash
# 1) 造沙箱数据根（身份库 + 两个 Agent），租户根必须落在数据根之外
mkdir -p /tmp/cow-acc/agents/alpha /tmp/cow-acc/agents/beta /tmp/cow-tenants/acc
#    config.json 需含：identity_mode=database、identity_db_path、
#    agent_workspace、agents[alpha,beta]、tenant_shared_base=/tmp/cow-tenants

# 2) 建租户与管理员
COW_DATA_DIR=/tmp/cow-acc cow management bootstrap \
    --tenant-code acc --tenant-name Acceptance \
    --admin-username alice --admin-display Alice \
    --shared-root /tmp/cow-tenants/acc \
    --password 'Str0ng-Pass!23' --allow-weak

# 3) 建第二个成员、绑定两个 Agent、指定 alpha 为默认（服务层调用，见文档脚注）

# 4) 起真实实例
COW_DATA_DIR=/tmp/cow-acc COW_WEB_PORT=9898 python app.py

# 5) 跑验收
python scripts/acceptance_personal_context.py \
    --data-dir /tmp/cow-acc --base-url http://127.0.0.1:9898 \
    --tenant-code acc --agent-a alpha --agent-b beta
```

## 3. 结果：23 / 23 通过

```
1. 成员无需选择 Agent 即可看到租户默认
  [PASS] admin 可登录                                   http 200
  [PASS] admin 看到本租户 Agent                          ids=['alpha','beta']
  [PASS] admin 恰好看到一个默认 Agent                     default=alpha
  [PASS] admin 可对默认 Agent 发起会话                    can_chat=True
  [PASS] member 可登录                                   http 200
  [PASS] member 看到本租户 Agent                         ids=['alpha','beta']
  [PASS] member 恰好看到一个默认 Agent                    default=alpha
  [PASS] member 可对默认 Agent 发起会话                   can_chat=True

2. 不选 Agent 直接开聊
  [PASS] admin 无 Agent 发起会话成功                      http 200 status=success
  [PASS] member 无 Agent 发起会话成功                     http 200 status=success
  [PASS] admin 的会话落在默认 Agent(alpha)                session=acc-admin-*
  [PASS] admin 的会话归属自己                             owner=usr_HBsN...
  [PASS] member 的会话落在默认 Agent(alpha)               session=acc-member-*
  [PASS] member 的会话归属自己                            owner=usr_0dfJ...

3. 会话互不可见
  [PASS] member 冒用 admin 的 session_id 被拒              http 404 code=not_found

4. 个人记忆：按用户隔离、跨 Agent 一致
  [PASS] admin 的日记摘要写入成功
  [PASS] 个人记忆落在用户域
         /tmp/cow-tenants/acc/users/usr_HBsN.../MEMORY.md
  [PASS] 个人记忆不在任何 Agent 工作区内
  [PASS] admin 的记忆从默认 Agent(alpha) 可见             hits=1
  [PASS] admin 的记忆从另一个 Agent(beta) 可见            hits=1
  [PASS] 另一个成员从 alpha 看不到                        hits=0
  [PASS] 另一个成员从 beta 看不到                         hits=0
  [PASS] 索引行归属正确
         (path=memory/users/usr_HBsN.../2026-09-10.md, scope=user, user_id=usr_HBsN...)
```

## 4. 落盘证据

**会话归属**（默认 Agent `alpha` 的 `memory/long-term/index.db`）：

```
session_id        channel_type  owner
acc-admin-10369   web           usr_HBsN2tsXoeqpE6ND
acc-member-10369  web           usr_0dfJwD-BLfan0_vu
```

两个账号都没有在请求里指定 Agent，会话仍然落到租户解析出的默认 Agent，且各归其主。

**记忆索引归属**（`chunks` 表）：

```
path                                              scope  user_id               source
memory/users/usr_HBsN2tsXoeqpE6ND/2026-09-10.md   user   usr_HBsN2tsXoeqpE6ND  memory
```

`scope=user` + 归属者 `user_id` 是检索侧唯一过滤依据（`user_id = ?` 或
`scope='shared'`），也是"跨 Agent 可见、跨用户不可见"的落点。

**磁盘布局**（用户域在租户共享根下，与 Agent 工作区平级）：

```
/tmp/cow-tenants/acc/users/usr_HBsN2tsXoeqpE6ND/memory/2026-09-10.md
/tmp/cow-tenants/acc/knowledge/log.md
```

**身份审计**（`audit_events`，与上述行为一一对应）：

```
tenant.bootstrap          tenant:tnt_03SJGw1aTh7GPrQQ   success
member.create             membership:mem_XcyYyWkT6Mn2bisz  success
agent.bind                agent:alpha                   success
agent.bind                agent:beta                    success
tenant.set_default_agent  tenant:tnt_03SJGw1aTh7GPrQQ   success
role.create               role:role_wglbJEGHhqezz9a5   success
member.update             membership:mem_XcyYyWkT6Mn2bisz  success
role.update               role:role_wglbJEGHhqezz9a5   success
```

## 5. 验收中发现并修复的问题

### 5.1（本次修复）共享知识目录让 `sync()` 整个崩溃

`MemoryManager.sync()` 通过 `state_dir.knowledge_dir(base=workspace)` 解析知识目录：
Agent 没有自己的 `knowledge/` 时，它解析到**租户共享根**下的共享副本 —— 一个位于
该 Agent 工作区**之外**的路径。而收集阶段仍用
`file_path.relative_to(workspace_dir)` 生成索引标签，直接抛 `ValueError`：

```
ValueError: '/…/acc/knowledge/log.md' is not in the subpath of '/…/agents/alpha'
```

影响面不止知识本身：异常发生在同步主循环里，**记忆与知识会一起停止索引**。这是
"通过 `state_dir` 解析共享知识"这一改动引入的回归，只有真实实例（存在共享
`knowledge/`）才会触发，单元测试的临时目录里没有这个目录。

修复（`agent/memory/manager.py`）：

- 知识文件带显式标签 `knowledge/<相对知识根>` 入索引，与原先"知识在工作区内"时的
  标签完全一致，因此**无需重建索引**；
- 同步主循环的标签推导加兜底：工作区外的文件不会再让整轮同步崩溃。

回归测试：
`tests/test_user_personal_memory.py::PersonalMemoryTestCase::test_shared_knowledge_is_indexed_without_crashing`
（先复现 `ValueError`，再断言标签为 `knowledge/log.md`）。

### 5.2（运维前置条件）普通成员要能开会话，需要角色授权

内置 `member` 角色只有 `agent.read` / `memory.read` 等读权限，**没有 `chat.use`**，
而内置角色不可修改。因此新成员无法开聊。租户管理员需要：

1. 建自定义角色，权限含 `chat.use` + `agent.use` + `agent.read`（+ 业务读权限）；
2. 把该角色赋给成员。

这是既有角色-资源授权模型的既定用法，不是本次改动引入的缺口；但**"不选智能体即可
开聊"成立的前提是成员已被授权**，运维建租户时需要按此配置。沙箱验收即按此授权后
执行（`role.create` / `member.update` / `role.update` 三条审计记录）。

> **后续修正（见 5.4）**：原先此处还要求"赋资源授权 `agent:<id>` 的 `read` 与 `use`
> （需逐个 Agent）"。该要求已由"共享默认智能体的可达性不依赖逐资源授权"取消：租户的
> **共享默认智能体**对持有功能权限的成员自动可读可用，无需逐资源授权；非默认智能体
> 仍需显式授权。因此普通成员**能开聊**只需上表第 1、2 步。

### 5.3（部署约束）租户共享根不能位于数据根之内

`state_dir` 的全局根防护会拒绝"租户共享根位于数据根（`config.json`/`identity.db`
所在目录）之内"的部署：

```
StateDirError: tenant '…' shared root '/…/cow-acc/tenant-roots/acc' resolves
inside the home/global workspace root; refusing to fall back
```

源部署（`COW_DATA_DIR` 未设`）下数据根是仓库目录，`~/.cow/tenant-roots` 不受影响；
但把 `COW_DATA_DIR` 指到 `~/.cow` 时，必须同时把 `tenant_shared_base` 指到
`~/.cow` **之外**，否则记忆路径解析会失败。

### 5.4（本次修复）真实实例「agent not found」的真正链路

在本机真实实例（database 身份模式，租户 `test15`）上，成员 `test15-2` 发送消息
报 `发送失败，请稍后再试。 agent not found`。**免选逻辑本身是生效的**：
`_resolve_tenant_default_agent(ctx)` 正确返回 `test15-verify`。失败来自另一条链路，
共四跳（每一跳都已用真实服务端函数复现）：

| # | 位置 | 结果 |
|---|---|---|
| 1 | `GET /api/agents` | 返回 `{"agents": []}` —— `test15-2` 的自定义角色只有 `agent.read`/`agent.use` **功能权限**，没有任何 `agent:<id>` 资源授权；`_resource_ids()` 对无授权成员返回 `set()`（不是 `None`），投影把每个智能体逐个过滤掉（此过滤为既有逻辑，本次仅新增租户管理员旁路） |
| 2 | `console.js` 目录加载 | 空目录时回落到**字面量** `'default'`（`… \|\| agentCatalog[0]?.id \|\| 'default'`） |
| 3 | `console.js` 全局 fetch 装饰 | 把 `activeAgentId` 自动附加到每个 `/api/*`、`/message`、`/stream` 请求 |
| 4 | `POST /message` | 携带 `agent_id='default'`；该标识属于**另一个租户** `tnt_xNHlQIA2XP-z6nQG`，与调用者租户 `test15` 不符 → `_require_tenant_binding` 抛 **404 `agent not found`** |

即：报错**不是**"免选"失败，而是控制台把**一个跨租户的、从未真实存在的标识**发给了
服务端。其后还压着第二个拦截点：`test15-verify` 当时 `private_owner_user_id` 指向
用户 `test15`，成员触发 `_require_private_owner` → **403 forbidden**（已实测）。

修复三处：

- **数据校正**：`cow management share-default-agents` 清除默认智能体的私有归属
  （dry-run 显示会改 2 条：`test15-verify`、`default`）。这正是 5b 的幂等校正入口。
- **前端**（需求：控制台不得虚构智能体标识）：`console.js` 去掉字面量兜底，
  空目录或记忆值不在目录中时**不携带**智能体标识，交由服务端解析租户默认；
  仅当记忆值仍在目录中才保留为用户显式选择。
- **服务端**（需求：共享默认智能体的可达性不依赖逐资源授权）：新增单点判据
  `_tenant_shared_default_agent()`，接入三处——投影可见性、`_require_agent_action`、
  `_workbench_chat_readiness`。放宽**仅限** `read`/`use`，且要求持有对应功能权限、
  智能体为**调用者本租户**的**解析默认**且**租户共享**。`edit` 仍严格按逐资源授权
  （`test15-2` 恰好持有 `agent.edit` 功能权限，若无该限制它就能编辑默认智能体）。

真实实例复验（`/tmp/cow-acc/verify_real.py`，逐跳调用出厂函数）：

```
user=test15-2 tenant=tnt_EA3qM-lHPLD8ZPwW tenant_admin=False
1) resolved tenant default      : test15-verify
2) GET /api/agents returns      : ['test15-verify']      # 修复前为 []
3) chat readiness on default    : (True, None)
4) private-owner gate           : PASS
5) agent.use gate               : PASS
6) agent.read gate              : PASS
7) chat.use gate                : PASS
8) agent.edit stays grant-only  : PASS (still refused)
RESULT: PASS - agent-less chat works end to end
```

其他账号一并复核：`test15`（租户管理员）、`admin`（平台管理员）均 PASS；
`e2e_member` 返回 `(False, 'permission_denied')` —— 该账号权限表里没有
`agent.use`/`chat.use`，属**如实告知无权**，而非修复前误导性的 `agent not found`。

回归对比（同一命令、`-p no:randomly`，全量 `tests/`）：

| | 失败 | 通过 |
|---|---|---|
| 修复前（HEAD 基线） | 33 | 1929 |
| 修复后（工作区） | 32 | 2333 |

失败集合差集为 **空**（零引入）；唯一差异是
`test_tenant_default_agent.py::…::test_two_tenants_each_mark_their_own_default`
由失败转为通过——该用例在 HEAD **隔离运行同样失败**，属本次一并修好的既有缺陷。
前端 `node:test` 套件 24 通过 / 3 失败 / 1 悬挂，其中 3 失败与 1 悬挂在 HEAD 基线
完全一致（既有）。

## 6. 覆盖边界（明确未验证的部分）

- **未覆盖模型回复**：沙箱 `config.json` 不含任何模型密钥，`POST /message`
  会在授权与落库之后、调用模型时失败，故 `msg_count` 与实际回复内容不在本次
  证据内。本次验收的断言边界是"授权通过 + 会话落库并归属正确"，这正是
  "不选智能体"要解决的问题。
- **已在真实实例上复核（见 5.4）**：本机真实实例经排查为 **database 身份模式**
  （存在 `identity.db`、租户 `test15` / `tnt_xNHlQIA2XP-z6nQG`、多用户）。此前"该实例
  为 legacy 身份模式"的判断有误，已据实修正。本次未做任何身份模式迁移（任务 6.2 的
  非目标未变），仅在既有 database 模式数据上执行了一次幂等的默认智能体共享校正，
  并以逐跳调用出厂函数的方式复核了授权链路。若要复跑面向多账号的脚本验收：

  ```bash
  python scripts/acceptance_personal_context.py \
      --data-dir <数据根> --base-url <控制台地址> \
      --tenant-code <租户> --admin <管理员账号:口令> --member <成员账号:口令>
  ```

- **未覆盖前端浏览器行为**：前端"不强制选择"由
  `tests/test_agent_chat_launch_frontend.cjs` 等 13 个 `node:test` 套件
  （185 通过 / 0 失败）覆盖，未做截图级验证。
