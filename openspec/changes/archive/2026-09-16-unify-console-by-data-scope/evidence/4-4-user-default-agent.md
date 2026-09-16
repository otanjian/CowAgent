# 4.4 用户默认智能体（`set_user_default`）

本文件记录 `tasks.md` 4.4：`在正式智能体接口增加 set_user_default 动作，固定当前 tenant/user、
校验可管理且可使用候选、独立版本和原子审计；详情“设为默认”对所有用户使用该动作。`

## 1. 改动前的问题

控制台详情页只有一个「设为默认」按钮，它提交 `{action:"set_default"}`：

    POST /api/agents {"action":"set_default","id":...}     // tenant 级，admin-only

而 `POST /api/agents` 的 `set_default` 分支在非管理员时直接 403。于是：

- **普通成员拿不到任何默认设置入口**。产品口径要求「默认智能体可以按用户进行设置，由用户自行设置」，
  但正式接口里没有这一步，`memberships.default_agent_id` 只能由系统供应（`agent/personal_assistant.py`）
    10|  写入，用户自己无法更改；
- **`memberships.default_agent_revision` 从未被写过**。该列与 `default_agent_origin` 由迁移
  （`auth/store.py` `_migration_25`）建立并写了文档口径，但没有任何写入方使用，等于一个没有实现的
  乐观锁；
- **两个「默认」被混为一谈**：`is_default` 在投影里是*解析后的锚点*（本人偏好 → 租户默认 →
  确定性回落），而「我设的默认」是*用户自己的偏好*。前端只有一个 `defaultAgentId`，用回落值渲染
  「当前默认」徽标与按钮，会把「没有任何人选择过」显示成「已经是默认」。

## 2. 服务端

### 2.1 `IdentityService.set_user_default_agent`（新增）

    20|签名：`set_user_default_agent(*, tenant_id, user_id, agent_id, expected_revision=None, actor_user_id=None)`

主体是**参数**而不是请求体字段：HTTP 层传入已验证会话的 tenant/user，所以任何请求都无法改到别人
的偏好。候选资格只问一次 `auth.object_scope`：

    scope = ObjectScope(tenant_id=tenant_id, user_id=user_id,
                        is_admin=bool(self._is_tenant_admin(...) or self.is_platform_admin(...)))
    scope.allows_agent(binding, action=MANAGE)

`MANAGE` 同时给出「可管理」与「可使用」两半：共享对象对全员可 `use`（本方法只问管理范围，使用者
由下面的 enabled 校验兜住），私有对象只有 owner 可 `manage`。**归属判定在管理员例外之前**，所以
非 owner 的管理员与普通成员一样被拒——这与 2.1 的既定不变量一致，不可能出现「管理员把默认指向
他人私有对象」。

提交时校验（全部在写之前，拒绝就不落任何写）：

| 校验 | 拒绝码 | 状态 |
|---|---|---|
| 租户/用户/目标缺一不可 | `invalid_request` | 400 |
| membership 存在 | `not_found` | 404 |
| 目标绑到本租户 | `not_found` | 404 |
| `scope.allows_agent(target, MANAGE)` | `forbidden` | 403 |
| `_agent_is_usable(target)`（未停用） | `agent_not_usable` | 409 |
| `expected_revision` 过期，或已有偏好但未带版本号 | `version_conflict` | 409 |

### 2.2 独立乐观锁与幂等

`memberships.default_agent_revision` 是**指针自己的**锁，刻意不复用 `memberships.version`（后者归
租户编辑器的草稿）；写入是带条件的 UPDATE：

    UPDATE memberships SET default_agent_id=?, default_agent_revision=default_agent_revision+1,
           default_agent_origin='user'
     WHERE tenant_id=? AND user_id=? AND default_agent_revision=?

`rowcount == 0` 即 `version_conflict`。`BEGIN IMMEDIATE` 已经排除读写之间的插入窗口，这条守卫是同一
结论的第二道保险，保证竞态下的失败方是**报错重读**而不是静默覆盖。

幂等：同目标重复提交（带当前版本）返回 `changed=false`，**不写、不审计、不动版本号**——授权仍会
重跑，所以幂等不是绕过；而不动版本号正是客户端刚读到的值仍然有效的原因。

`expected_revision=None` 的语义被刻意收窄：只有 `default_agent_origin IS NULL`（从未登记过偏好）
    50|时才接受。一旦已有偏好，不带版本号即 `version_conflict`——否则一个不老实的旧客户端就是一台
丢失更新机器。

### 2.3 原子审计

`member.set_default_agent` 与指针写在同一个事务里（`_audit_in_tx`），`target=membership:<id>`、
`redacted_changes={"default_agent_id":..., "origin":"user"}`。沿用系统供应已有的动作名，调用方靠
`actor_user_id` 区分是「用户自己选的」还是「供应写的」。

### 2.4 HTTP 动作 `set_user_default`

`AgentsHandler.POST` 新增分支（顺序即语义）：

1. `ctx` 无租户 → 403；
2. **先查绑定**：目标不属于本租户（或不存在的 id）→ **404 `not_found`**，与
   `_require_tenant_agent_binding` 等既有路径一致。若先问权限会得到 403，那既与既有路径口径冲突，
   又等于向调用者确认「这个 id 在别处存在」；
3. `_require_agent_action(ctx, id, "edit", "agent.edit")`：功能性权限，与其他 Agent 写路径一致；
    60|4. 调服务，`IdentityServiceError` 以自身 `code`/`status` 原样抛出（`channel/web/auth_handlers._error`），
   不降级成 handler 的 200-with-status:error。

请求体里的 `user_id`/`tenant_id` **不被读取**。

### 2.5 投影：把两种「默认」分开

`_tenant_agents_admin_projection` 每行新增 `is_user_default`，并新增响应级 `user_default`
（`{agent_id, revision, origin}`，由 `_user_default_pointer` 读 `IdentityService.user_default_agent`）与
`tenant_default_manageable`（平台管理员或租户管理员为 true）。

`default_agent_id`（解析锚点）与 `user_default.agent_id`（登记偏好）是两个不同的字段，所以「回落
锚定」不会被误报成「用户选过」。

## 3. 前端（`channel/web/static/js/console.js`）

- 新增状态 `userDefault` / `tenantDefaultManageable`，由 `loadAgentCatalog()` 从同一份 payload 落地；
  `_invalidateAccountIdentity()`（切租户/登出）清空，避免拿上一个租户的指针渲染按钮。
- `renderAgentDetail()`：
  - `isMyDefault = userDefault.agent_id === agent.id` → 已是我登记的默认则不显示按钮；
  - 停用对象不显示（服务端必然 `agent_not_usable`，按钮只能是必败按钮）；
  - **回落的 `defaultAgentId` 不再被当成用户偏好**——未登记偏好时按钮仍然出现，这正是「由用户自行
   设置」所需的入口；
  - 租户动作改为按 `tenantDefaultManageable` 显示，不再靠「成员看不到这个页面」隐式成立。
- 新增 `setAgentAsMyDefault()`：提交 `{action:"set_user_default", id, default_revision: userDefault.revision}`，
   90|按 `version_conflict` / `agent_not_usable` / `forbidden` 分别给文案，成功后 `loadAgentCatalog()` 整表重载
  （锚点、网格顺序、记忆中的偏好要一起动），失败不重载、不画成功。
- `setAgentAsDefault()`（租户动作）保留，仅更新注释说明与用户动作的区别。

## 4. 文案（三语）

`agents_set_tenant_default`（原「设为默认」改为「设为租户默认」）+ `agents_set_my_default` 及
`_done` / `_failed` / `_forbidden` / `_conflict` / `_disabled` 四类失败文案，zh-CN / zh-TW / en 三语齐备，
并同步 `tests/fixtures/console_i18n_snapshot.json`。

## 5. 测试

| 文件 | 断言 |
|---|---|
| `tests/test_user_default_agent_selection.py`（新增，18 项） | 真 WSGI：本人私有可设、管理员可设共享、按用户独立、`origin='user'` 与版本自增、审计落库；请求体 `user_id`/`tenant_id` 无效、管理员不能改他人偏好；共享对象对成员一律拒绝（**先给 `agent:shared-agent` 的显式 edit 授权**，证明拒绝来自对象范围而不是缺功能权限）、他人私有对成员与管理员都拒绝、外租户 404、停用 409 且不半写；租户动作仍是另一件事（成员 403、设用户默认不动租户指针）；过期版本 409、不带版本号覆盖已登记偏好 409、同目标重试幂等（不审计不涨版本）、同读版本的两路并发只有一个 200；`provisioned`/`user` 语义 |
| `tests/test_user_default_agent_frontend.cjs`（新增，10 项） | 全部用户可见「设为我的默认」、已登记的不再显示、**回落锚点不等于我的选择**、停用对象不显示；租户动作仅在 `tenant_default_manageable` 时显示、已是租户默认不显示；提交体只含 `id` + 读到的 `default_revision`（不含 user/tenant）、首次选择发 `null`、失败不重载、三个拒绝码各有文案 |
| `tests/test_tenant_default_agent_frontend.cjs`（更新） | 原 4 项按新文案 `agents_set_tenant_default` 与新 payload 字段调整，仍全绿 |

## 6. 验证

| 命令 | 结果 |
|---|---|
| `pytest tests/test_user_default_agent_selection.py` | 18 passed |
| `pytest tests/test_scope_consistency_acceptance.py tests/test_tenant_default_agent_selection.py tests/test_private_agent_lifecycle.py tests/test_user_personal_agent_provisioning.py tests/test_private_agent_owner_reachability.py` | 140 passed |
| `pytest tests/test_agent_web_management.py tests/test_agent_workbench.py tests/test_default_agent_tenant_shared.py tests/test_agent_capability_enforcement.py tests/test_identity_agent_bindings.py tests/test_tenant_default_agent_selection.py` | 84 passed |
| `node --test tests/test_user_default_agent_frontend.cjs` | 10 pass |
| `node --test tests/test_tenant_default_agent_frontend.cjs` | 4 pass |
| `node --test tests/test_console_i18n_parity.cjs` | 5 pass（三语与快照一致） |
| `node --test tests/*.cjs` | 641 项 / 598 通过 / 43 失败——**与改动前逐项一致**（36 `test_session_history_frontend.cjs` 缺 `queueMicrotask`、5 `test_sidebar_account_frontend.cjs`、1 `test_appearance_browser.cjs` 缺 playwright、1 `_tmp_repro_modeldefaults.cjs`），无新增 |

## 7. 4.5 的收口（本次一并核对）

4.5 的三项里有两项在更早的切片已经落地，本次只补上「明确命名」这一半，故在此一并记录并给出代码位置，
避免只凭 `tasks.md` 的勾选判断：

| 4.5 的口径 | 落点 | 状态 |
|---|---|---|
| 租户默认放在**明确命名**的管理配置 | 投影新增 `tenant_default_manageable`（平台/租户管理员为 true，功能性 `agent.edit` 授权**不算**）；详情页按该字段显示「设为租户默认」，与「设为我的默认」文案分离（`agents_set_tenant_default` / `agents_set_my_default`） | 本次落地 |
| 旧 `set_default` 仍为受管理资格保护的租户动作 | `channel/web/web_channel.py` `set_default` 分支仍要求 `ctx.is_platform_admin or ctx.is_tenant_admin`，未改成资源授权 | 既有，本次未改 |
| 新旧租户动作均拒绝私有目标 | `auth/service.py:1472` `_appoint_tenant_default_agent` 对 `private_owner_user_id is not None` 抛 `private_agent_not_shareable`(409)，且**不清空** owner；`set_user_default_agent` 以 `ObjectScope` owner-first 拒绝他人私有（本文件 §2.1） | 既有 + 本次 |
| 清理会隐式转共享的默认校正路径 | `auth/store.py` `_migration_25`：校正只清空无法解析的指针并逐条审计，`private_owner_user_id` 原样保留（注释即写明「sharing 是显式且被审计的动作」） | 既有，本次核对 |

因此 `tasks.md` 的 4.5 勾选是以上四项合并的结果，其中只有「明确命名」是本次改动。

## 8. 未覆盖范围

- **浏览器验收未执行**：本机缺 playwright，「设为我的默认」的真实点击与提示语视觉未验收，待第 3.6
   切片一起做。
- **原生 Desktop 未接入**：`desktop/src/renderer/src/store/agentStore.ts` 未加用户默认动作。
- **全量 `pytest tests/ -k "agent or console or tenant or auth or scope or personal"` 未跑完**：单次耗时
  超过 16 分钟，在本次暂停前主动终止；上面三组窄回归已完成。
- 4.1（创建生命周期合并）、4.6（供应只初始化空偏好 + 默认解析来源）仍**未完成**，本文件不据此宣称。
