# 4.2 分离管理集合与聊天使用集合（选择器读 use 范围）

本文件记录 `tasks.md` 4.2：`分离管理集合与聊天使用集合；管理列表包含停用对象并排除他人私有，
聊天保留已获授权共享对象。`

后端在 2.1 已把两条读取分开（`_iter_tenant_agents(action=SCOPE_USE|SCOPE_MANAGE)`：
`/api/agents` = 管理投影、`/api/agents?view=workbench` = 使用投影）。本次补齐的是**客户端**
那一半：聊天选择器仍在读管理投影，导致「管理集合」与「聊天可用集合」在前端被合成一个。

## 1. 改动前的问题

`console.js` 只有一套 Agent 清单：`loadAgentCatalog()` 读 `/api/agents`（管理投影）存入
`agentCatalog`，而聊天侧一律由它派生：

```
function enabledAgents() { return agentCatalog.filter(a => a.enabled === true); }
function availableChatAgents() { return enabledAgents().filter(a => a.can_chat !== false); }
function multiAgentMode() { return availableChatAgents().length > 1; }
```

`openNewChatMenu()`（新对话挑选器）、`renderTeamChatList()`（团队聊天成员）与 composer 的
切换/邀请候选都属于「和谁聊天」这个问题，却都从管理集合派生。真实租户实测（第 4 节）：

- **普通成员** `test15-2`：管理集合 `[]`（本人没有私有 Agent），于是挑选器为空、
  `multiAgentMode()` 为 false（caret 不出现）、`findAgent(activeAgentId)` 解析不到租户共享默认
  Agent，composer 身份回落到裸 id；
- **租户管理员** `test15`：挑选器里出现 `my-assistant-admin-test15-RC001`——那本是 RC001 的私有
  Agent（被旧的默认任命路径隐式转共享，见 4.5 与第 5 节修复）。

## 2. 前端：两套清单

| 用途 | 变量/函数 | 投影 | 覆盖范围 |
|---|---|---|---|
| 管理（Agent 页、创建/克隆来源、记忆目标、渠道目标） | `agentCatalog` / `enabledAgents()` | `/api/agents` | 共享 + 本人私有；含停用；不含他人私有 |
| 聊天（新对话挑选器、团队聊天、composer 切换/邀请、`multiAgentMode`、`findAgent` 回退） | `chatAgentCatalog` / `chatAgents()` / `availableChatAgents()` | `/api/agents?view=workbench` | 共享 + 本人私有；仅可用（`can_chat`） |

- `loadChatAgentCatalog()`：与目录读**并行**读取使用投影，复用 `fetchAgentWorkbench()` 既有的
  形状校验（旧后端返回管理快照会被判为形状不符而拒绝），落地后重绘 composer 身份、caret 与
  已打开的挑选器；失败不抛给目录读。
- `applyAgentWorkbench()`（工作台卡片）与选择器共用同一次读取结果，谁先落地都会填充
  `chatAgentCatalog`。
- `findAgent()` 先查管理目录、再回退到 use 集合：共享会话的 owner 名字/头像因此可解析，而管理
  集合本身没有被放宽。
- 回退语义：使用投影读不到（旧后端/失败）时按管理集合工作，即改动前的行为——只可能更窄，不会
  把不可用的对象混进选择器。

`enabledAgents()` 语义**未改动**，仍是管理集合（含停用），所以创建/克隆来源、记忆目标、渠道
目标、管理网格与详情都不会出现额外对象（`test_composer_agents_frontend.cjs` 全部通过）。

## 3. 服务端与运维侧（本次一并落地）

- `_appoint_tenant_default_agent` 拒绝私有目标（`private_agent_not_shareable`，409），不再清空
  `private_owner_user_id`（任务 4.5 的一半，早前已完成并有测试）。
- `restore_private_agent_owner(..., dry_run=False)`：dry-run 跑完**同一组**校验（已是本人所有、
  已有他人 owner、是租户默认、owner 非本租户成员）后不写、不审计，供运维预览。
- CLI `restore-private-owner`：`make_agent_tenant_shared` 的逆操作，审计
  `agent.restore_private_owner`；`--actor-username` 给出时必须真的具备资格，避免审计被伪造。
- `repair-tenant-defaults` 只释放非法租户默认指针、保留归属（不再隐式转共享）。

## 4. 实测证据（真实 `identity.db` + 真实 handler）

`GET /api/agents`（含 `agent.read` 权限判定）与 `?view=workbench` 在租户
`tnt_EA3qM-lHPLD8ZPwW` 的返回：

```
test15 (租户管理员)
  GET /api/agents               -> ['bug-butler-test15', 'business-analysis-test15', 'knowledge-qa-test15', 'my-assistant-admin-test15', 'tax-health-check-test15']
  GET /api/agents?view=workbench -> 同上
test15-2 (普通成员，无私有 Agent)
  GET /api/agents               -> []
  GET /api/agents?view=workbench -> ['bug-butler-test15', 'business-analysis-test15', 'knowledge-qa-test15', 'my-assistant-admin-test15', 'tax-health-check-test15']
RC001 (普通成员，有私有 Agent)
  GET /api/agents               -> ['my-assistant-admin-test15-RC001']
  GET /api/agents?view=workbench -> 上述 4 个共享 + ['my-assistant-admin-test15-RC001']
```

结论：管理员的挑选器 = 租户共享智能体（**不再包含** RC001 的私有 Agent）；成员的选择器保留
已获授权共享对象，同时本人私有 Agent 仍在。成员不再出现「管理集合为空 → 没有选择器」的回归。

## 5. 被误发布的私有 Agent 已还原

`my-assistant-admin-test15-RC001` 在 2026-09-15 15:04:18 被设为租户默认时（旧路径）丢失了
`private_owner_user_id`，随后默认指针改回 `my-assistant-admin-test15`，但它已读作租户级对象。
按确认的口径还原为 RC001 私有：

```
$ python -m cli management restore-private-owner --agent-id my-assistant-admin-test15-RC001 \
      --owner-username RC001 --dry-run
  tenant tnt_EA3qM-lHPLD8ZPwW: 'my-assistant-admin-test15-RC001' is shared
  -> would become private to RC001 (usr_2EaG0EhEqh85w60w)
  1 repair would be applied. Re-run without --dry-run to apply.

$ python -m cli management restore-private-owner --agent-id my-assistant-admin-test15-RC001 \
      --owner-username RC001 --reason "..."
  Restored private ownership: 'my-assistant-admin-test15-RC001' now belongs to RC001 (...).
```

审计：`agent.restore_private_owner` / `target=agent:my-assistant-admin-test15-RC001` / `result=success`。
RC001 的 `memberships.default_agent_id` 仍指向该 Agent（`origin=user`），因此「Rock 仍是他的默认、
仍可用」；其他成员的选择器中该 Agent 消失。修复前 `identity.db` 已备份。

## 6. 验证

| 命令 | 结果 |
|---|---|
| `node --test tests/test_agent_chat_launch_frontend.cjs` | 12 pass（新增 4 项：共享对象可被选中/可解析、管理集合不被放宽、不可用对象不进选择器） |
| `node --test tests/test_composer_agents_frontend.cjs` | 5 pass |
| `node --test --test-timeout=20000 "tests/**/*.cjs"` | 633 项，590 通过，43 失败——43 项全部落在改动前既有失败文件（`test_session_history_frontend.cjs` 36、`test_sidebar_account_frontend.cjs` 5、`test_appearance_browser.cjs` 1 缺 playwright、`_tmp_repro_modeldefaults.cjs` 1），无新增 |
| `pytest tests/test_default_agent_tenant_shared.py tests/test_tenant_default_agent_selection.py tests/test_management_repair_tenant_defaults.py tests/test_management_restore_private_owner.py` | 48 passed |
| `pytest tests/test_agent_workbench.py tests/test_tenant_default_agent.py tests/test_tenant_agent_creation.py tests/test_private_agent_owner_reachability.py tests/test_scope_consistency_acceptance.py` | 108 passed（含新增：管理分母保留停用对象、聊天读排除停用对象） |
| `pytest tests/test_personal_console_acceptance.py tests/test_personal_console_transport.py tests/test_personal_console_web.py` | 49 passed / 8 failed——8 项为改动前既有失败（成员页资格与 `menu_not_granted`/`capability_disabled` 口径，集中在 `test_personal_console_acceptance.py`），本次未新增 |

## 7. 未覆盖范围

- **原生 Desktop 镜像同一问题未改**：`desktop/src/renderer/src/store/agentStore.ts` 仍只读
  `/api/agents` 默认视图，成员身份下挑选器同样会漏掉共享 Agent。按 7.6 的边界单列，需与 Desktop
  验收切片一起处理。
- 浏览器实测未执行（本机缺 `playwright`），选择器视觉验收仍待真实浏览器切片。
- 4.4 `set_user_default`、4.6 供应与默认回落、4.7/4.8 双角色与默认验收仍未完成；本文件不据此
  宣称 4.3 之后的任务。
