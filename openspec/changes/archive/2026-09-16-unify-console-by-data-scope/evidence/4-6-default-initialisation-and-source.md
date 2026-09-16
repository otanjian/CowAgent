# 任务 4.6 证据：供应只初始化空偏好、解析返回来源、删除清理停用保留

对应 `openspec/changes/unify-console-by-data-scope/tasks.md` 的任务 4.6：

> 实现系统供应只初始化空偏好、与用户选择竞争保护，默认解析过滤合法候选并返回生效目标和回落来源；删除清理默认引用，保留停用偏好。

## 1. 问题：一个字段，两个写入者

任务 4.4 之后，`memberships.default_agent_id` 有两个人想写：

- **本人**：`set_user_default_agent`（任务 4.4，控制台「设为我的默认」）；
- **系统供应**：`PersonalAssistantProvisioner.provision` 建好私有助理后登记为本人默认。

原供应的保护只有 `owned_agent_id`——「该成员是否已有私有助理」。它盖不住真正的窗口：**成员还没有私有助理、但已经选过默认**。此时供应照写，把人的选择覆盖掉。

同时，`default_agent_origin`（迁移 14/25）已经存在，但只有 `_migration_25` 的默认校正路径读它；写路径没人用它做判据。

## 2. 竞争保护：由 `origin` 在**一条语句**里裁决

`auth/service.py` 新增 `IdentityService.initialize_member_default_agent`：

```sql
UPDATE memberships SET default_agent_id=?,
       default_agent_revision=default_agent_revision+1,
       default_agent_origin='provisioned', updated_at=unixepoch()
 WHERE tenant_id=? AND user_id=? AND default_agent_origin IS NULL
```

- 「是否已有登记」与「写入」是**同一条 UPDATE**，位于 `BEGIN IMMEDIATE` 事务内，因此用户选择不可能插在读写之间被静默覆盖；`rowcount == 0` 就是「别人先到」，回报 `skipped` / `already_registered`。
- 判据是 **origin 而非指针值**：选过却被删除的成员、与从未选过的成员，指针都可能是 NULL，只有后者 origin 为 NULL。「origin 为 NULL」=「从未登记过偏好」，这也是 4.4「首次选择可以不传 revision」的依据。
- 目标仍按其他登记写入者的规则校验租户归属（跨租户 → 404），所以即使拿到过期 id 也无法登记别家 Agent。
- 只在真正写入时落 `member.default_agent.initialised` 审计。

`agent/personal_assistant.py`：

- 新增 `initialize_member_default`（薄封装，带自己的 docstring 说明「为什么不是 `set_member_default_agent`」）；
- `provision` 改用它，并把结果作为 `default_registration` / `default_registration_reason` 附加到返回体：助理**建成了**，但「是否成为其默认」是**另一件事**，调用方有权看到。`_record` 因此新增 `extra=`，且 `extra` 有意不进审计载荷——审计保持「id + 单个 reason code」的固定词表；
- `origin="provisioned_assistant"` 的绑定 origin 保持不变（这是**绑定**的来源，与默认偏好的 `origin` 是两列）。

## 3. 解析：返回生效目标**与回落来源**

`auth/service.py` 新增 `resolve_default_agent(tenant_id, user_id) -> {"agent_id", "source"}`，`resolved_default_agent_id` 改为它的单值视图（同一规则，不可能分叉）。`source` 取值：

| source | 含义 |
| --- | --- |
| `user` | 本人已登记的偏好，可达且可用（是人是供应写的由 `default_agent_origin` 表达） |
| `tenant` | 租户配置的 `default_agent_id` |
| `shared` | 无偏好无配置时的兜底，落在**共享**池 |
| `any` | 兜底落在**非共享**池（该租户只剩私有 Agent） |
| `None` | 无可用候选 → 目标也是 `None` |

`shared` 与 `any` 分开是刻意的：把只含私有 Agent 的池叫 `shared` 是**虚假陈述**。

`channel/web/web_channel.py`：

- `_resolve_default_agent(ctx)` 返回该字典；`_resolve_tenant_default_agent(ctx)` 变成它的单值视图（原来这两个函数各自持有一份规则，现在只有一份）；
- `/api/agents` 载荷（`_tenant_agents_admin_projection`）新增 `default_resolution`，与既有 `default_agent_id`／`is_default`／`is_user_default` 并列：前者是「新会话实际落点及原因」，后者是「面板编辑的租户级条目」。

## 4. 删除清理 / 停用保留

`release_deleted_agent` 原来只把 `memberships.default_agent_id` 置空，**留下 origin**。这会让该行自称「有人登记过偏好」却没有目标——4.4 把「origin 非 NULL」读作「已有登记」，于是该成员在读到 revision 之前**无法再选**。现在两者一起释放并递增 revision（指针与来源是同一件事）：

```sql
UPDATE memberships SET default_agent_id=NULL,
       default_agent_revision=default_agent_revision+1,
       default_agent_origin=NULL, updated_at=unixepoch()
 WHERE default_agent_id=?
```

**停用不清理**：`resolve_default_agent` 只是跳过不可用偏好（打日志），不写库；重新启用即恢复为锚点。清理属于删除路径——停用是临时状态，清掉等于悄悄丢弃用户既做过、又因目标停用而无法重做的选择。

## 5. 控制台消费「回落来源」

`channel/web/static/js/console.js`：

- 新增模块级 `defaultResolution`，在 `loadAgentCatalog` 读取、在 `_invalidateAccountIdentity` 清空（与 `userDefault`／`tenantDefaultManageable` 同步，换租户后不得沿用旧租户的结论）；
- 新增 `agentAnchorHintText()`，把 `source` 映射为各自文案，**未知 source 走中性文案**——新服务端加值时只能说「不确定」，不能替它宣称「租户配置的」；
- `renderAgentDetail()` 在动作行上方渲染 `#agent-anchor-hint`，仅当该 Agent 就是锚点。

`channel/web/static/js/i18n/agents.js` 三语各加 5 个键（`agents_anchor_source_user` / `_tenant` / `_shared` / `_any` / `_unknown`），`channel/web/static/css/console.css` 加 `.agent-anchor-hint`。

## 6. 验收

| 测试 | 覆盖 |
| --- | --- |
| `tests/test_user_default_initialisation.py`（新，21 passed） | 供应只初始化空偏好；重跑不重贴 `origin`；首个写入者胜；跨租户 404；只写才审计；解析来源四值 + 无候选；租户级/成员级/管理员口径；他人私有不可达；**删除释放指针与 origin 且可不带 revision 重选；停用保留偏好、重启恢复为锚点** |
| `tests/test_user_default_agent_selection.py`（+5，23 passed） | `/api/agents` 的 `default_resolution`：兜底报为兜底而非租户决定、任命后报 `tenant`、本人选择报 `user`、锚点恒在本调用者可达范围内、按调用者而非按租户 |
| `tests/test_user_default_agent_frontend.cjs`（+5，15 passed） | 锚点提示只出现在锚点；`shared`/`any` 用回落文案且不借用租户口径；`user` 自陈；未知 source 中性；无解析则不渲染 |
| `tests/test_console_i18n_parity.cjs`（5 passed） | 三语键集一致、快照同步 |

回归：`tests/test_user_default_agent_selection.py tests/test_user_personal_agent_provisioning.py tests/test_tenant_default_agent_selection.py tests/test_default_agent_tenant_shared.py tests/test_default_agent_fail_closed.py tests/test_user_default_migration.py tests/test_private_agent_owner_reachability.py` 合计 **164 passed**。

### 关于并行全量 Python 回归

以 `pytest tests/ -k "agent or console or tenant or auth or scope or personal"` 选定 208 个文件分 6 组并行跑，得到 41 项失败。已逐条核对：**没有一条引用本次新增的任何符号**（`resolve_default_agent`／`initialize_member_default`／`default_agent_origin`／`default_resolution` 在失败输出中零出现），其成因是既有未提交阶段的既有失败：

- `test_weixin_qr_flow.py`（9 项）：`401 recent password required`，来自扫码确认路径的敏感操作口令门，属既有未提交的扫码/授权阶段；
- `test_personal_console_*.py`／`test_capability_matrix.py`／`test_personal_capability_switches.py`／`test_plan_3_1_joint_acceptance.py`／`test_tenant_admin_skills_menu.py`：`menu_not_granted` 与 `capability_disabled` 口径差、个人页 action/states 差异，即本 change 先前证据已记录的既有漂移。

因此任务 4.6 未引入新回归；上述既有失败不属本任务范围，留待阶段 8 的回归收口统一处理。
