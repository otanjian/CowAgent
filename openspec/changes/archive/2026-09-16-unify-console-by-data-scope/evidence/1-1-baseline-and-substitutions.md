# 1.1 主规范、归档与工作区基线

本文件记录 `tasks.md` 1.1 的核对结果：以当前工作区代码与 `openspec/specs/` 主规范为基线，列出本 change
替代的入口/流程决策、保留的数据隔离成果，以及迁移号与共享文件的冲突结论。所有结论来自实读代码与可复跑命令，
不以归档 change 的勾选状态代替检查。

基线提交：`f960c56b`（分支 `rdai`，工作区含未提交改动）。

## 1. 主规范基线

| capability | 主规范位置 | 本 change 的关系 |
|---|---|---|
| `unified-console-access` | `openspec/specs/`（不存在，ADDED） | 新建 |
| `user-default-agent-selection` | 不存在，ADDED | 新建 |
| `console-information-architecture` | `openspec/specs/console-information-architecture/spec.md` | MODIFIED：控制台对成员开放 |
| `console-navigation-availability` | `openspec/specs/console-navigation-availability/spec.md` | MODIFIED：统一页面授权与旧菜单映射 |
| `member-personal-console` | `openspec/specs/member-personal-console/`（未提交，本工作区新增） | MODIFIED + 退役 |
| `sidebar-account-menu` | `openspec/specs/sidebar-account-menu/spec.md` | MODIFIED + REMOVED「我的资源」 |
| `agent-workbench` / `user-private-agent-management` / `rbac-authorization` | 对应主规范 | MODIFIED：共用生命周期与对象范围 |
| `tenant-default-agent-administration` / `user-personal-agent-provisioning` / `agent-chat-launch` | 对应主规范 | MODIFIED：用户默认与租户默认分离 |
| `database-memory-console` / `user-personal-context` / `tenant-skills-tools-console` | 对应主规范 | MODIFIED：记忆/工具技能共用页面 |
| `tenant-channel-configuration` / `personal-channel-configuration` / `channel-scan-onboarding` | 对应主规范 | MODIFIED/ADDED：渠道统一接入与扫码 |

校验结果：

```
openspec validate unify-console-by-data-scope --strict
→ Change 'unify-console-by-data-scope' is valid
```

## 2. 本 change 替代的入口/流程决策

| 旧决策（来源 change） | 现状代码位置 | 本 change 的替代 |
|---|---|---|
| 普通成员引导到独立个人页面 | `channel/web/static/js/personal-console.js`（831 行，五个 `personal-*` 视图）；`_SIGNED_CONSOLE_PAGES` 的 `personal.*`（`auth/service.py:105-111`） | 成员使用正式 `agents`/`memory`/`channels`/`skills`/`config` 页面；`personal.*` 仅保留限期转接 |
| 控制台入口仅管理员 | `_qualifyAdminConsoleEntry`（`channel/web/static/js/console.js:17543-17547`）：`isPlatformAdmin \|\| isTenantAdmin` | 改为「有效身份 + 当前租户 + 至少一个获授权正式业务页面」 |
| 「我的资源」五项位于账号菜单 | `channel/web/chat.html:451-483`（`#account-menu-resources` + 五个 `data-view="personal-*"`） | 删除标题与五项，保留账号设置等 |
| 管理列表与聊天使用共用遍历 | `_iter_tenant_agents`（`channel/web/web_channel.py:9687`）；`_tenant_agents_projection`（`:9727`） | 拆分管理集合与聊天使用集合 |
| 管理员专属 `set_default` 语义 | `AgentsHandler` `set_default`（`channel/web/web_channel.py:10232`）→ `appoint_tenant_default_agent`（`auth/service.py:1271`），会 **清除私有 owner**（`auth/service.py:1305-1311`） | 详情「设为默认」改为用户默认 `set_user_default`；租户默认保留明确管理动作且拒绝私有目标 |
| 个人/租户两套渠道分支 | `PersonalChannelHandler`（`channel/web/web_channel.py:8919`，`allow_owner=True`）vs `TenantChannelsHandler`（`channel/web/admin_handlers.py:976`，`_require_tenant_admin`） | 同一实例服务，归属由服务端派生 |
| 五个个人能力开关决定独立实现 | `PERSONAL_CAPABILITY_SWITCHES`（`auth/policy.py:526-532`）、默认值（`:540-546`）、`PERSONAL_PAGE_CAPABILITIES`（`:551-560`）、`config.py:286-290` | 迁移为同一对象条件的部署限制；旧键完成后移除运行读取 |

## 3. 保留的数据隔离成果（不作为替代对象）

以下为既有 change 已合并的真值，本 change 复用而不重建：

| 成果 | 真值位置 | 说明 |
|---|---|---|
| 私有智能体归属 | `agent_bindings.private_owner_user_id`（`auth/store.py:165-172`）、`origin`（`_migration_17`，`auth/store.py:898-926`） | 唯一归属真相，不新增第二份 |
| 私有 owner 判定 | `_private_agent_owned_by_another`（`channel/web/web_channel.py:350-373`）、`is_private_agent_owner`（`auth/service.py:1857`）、`private_agent_ids`（`auth/service.py:1840`） | owner 检查先于管理员例外 |
| 个人记忆存储协议 | `agent/memory/personal.py`（`PersonalMemoryService` `:393`，`_scope_update` `:195`，`version`/`generation`/pending index） | 唯一版本与检索真值 |
| 渠道实例归属 | `tenant_channel_instances.scope`/`owner_user_id`（`_migration_16`，`auth/store.py:850-889`）、`governance_disabled_*` | 实例级归属与治理停用 |
| 扫码一次性授权 | `auth/scan_authorization.py`（`mint` `:88`，`claim` `:168`，`consume` `:220`，`_matches` `:244`） | 单次消费语义保留 |
| 用户默认指针 | `memberships.default_agent_id`（`_migration_14`，`auth/store.py:790-812`） | 仅新增独立版本列，不新建表 |
| 菜单映射机制 | `_migration_20`（`auth/store.py:1033-1069`） | 幂等、非破坏性插入，作为本 change 映射迁移的先例 |
| 审计 | `_audit_in_tx`（`auth/service.py:505`） | 复用，不新增审计载体 |

## 4. 迁移号与共享文件冲突结论

**迁移号**：当前 `auth/store.py` 有 24 个迁移，schema 版本 = `len(_migrations)`。新迁移追加为
`_migration_25`，注册于 `auth/store.py:1216`（`_migrations.append(_migration_24)`）之后。编号与归档
change 无冲突（归档 change 不新增迁移）。

```
rg -c '^_migrations\.append\(_migration_' auth/store.py
→ 24
```

**共享文件**：本 change 与工作区未提交改动共享以下文件，实施前需复核差异，避免覆盖：

| 文件 | 未提交改动 | 冲突风险 |
|---|---|---|
| `auth/service.py` | +950 行（个人控制台/扫码/渠道） | 高：2.x/4.x/5.x/6.x 均改此文件 |
| `channel/web/web_channel.py` | +162 行 | 高：4.x/5.x/8.1 |
| `channel/web/static/js/console.js` | ±210 行（仅 tenant-channel 委派区域） | 高：3.x |
| `channel/web/scan_onboarding.py` 相关 | `auth/scan_authorization.py` +181 行 | 中：6.4 |
| `channel/channel_instances.py` | +54 行 | 中：6.x |
| `channel/web/chat.html` | +5 行 | 中：3.3/3.4 |

`console.js` 工作区改动经核对仅落在 `@@ -14135` … `@@ -14979` 的 tenant-channel 委派区块，与 3.x 触及的
导航区块（`VIEW_META` `:1609-1640`、`_applySidebarPermissions` `:18098-18206`、`_qualifyAdminConsoleEntry`
`:17543-17547`）无行重叠。

## 5. 未合入主规范的历史提案不作为基线

| 归档 change | 状态 | 处理 |
|---|---|---|
| `enable-member-personal-console` | 已归档，其 delta 已同步主规范（部分为未提交状态） | 复用数据隔离成果，替代其入口/流程决策 |
| `move-personal-menu-to-account` | 已归档 | 其「账号菜单承载个人入口」决策被本 change 反向取代 |
| `remove-account-personal-resources-menu` | 已归档（`openspec/changes/archive/2026-09-15-remove-account-personal-resources-menu`） | 其 delta 与 3.3 目标一致；归档不构成实现完成，账号菜单仍含五项（`chat.html:451-483`），本 change 实作 |
| `upgrade-personal-channel-workbench` | 已归档 | 共用组件 `channel-workbench.js` 复用；「个人工作台」入口不复用 |
| `complete-desktop-and-scan-real-acceptance` | 已归档 | 归档 ≠ 真实运行验收；原生 Desktop 与真实提供方范围仍单列未验收 |

## 6. 可复跑证据

```
.venv/bin/python -m pytest tests/test_personal_console_menu.py tests/test_personal_console_pages.py \
  tests/test_personal_console_multi_tenant_authorization.py tests/test_personal_capability_switches.py \
  tests/test_default_agent_tenant_shared.py tests/test_tenant_default_agent.py \
  tests/test_memory_console_scope.py tests/test_tenant_channel_console_scope.py -q
→ 156 passed in 59.28s

openspec validate unify-console-by-data-scope --strict
→ valid
```
