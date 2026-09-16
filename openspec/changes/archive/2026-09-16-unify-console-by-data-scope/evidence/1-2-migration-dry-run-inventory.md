# 1.2 迁移预演盘点（只读）

本文件记录 `tasks.md` 1.2 的只读盘点结果。命令均在只读模式（`mode=ro`）下对 `identity.db` 执行，
不写入任何数据。对象清单来自实读代码（`file:line`）。

## 1. 正式页面与旧个人页面 grant

### 1.1 后端签约页面（24 个）

真值：`_SIGNED_CONSOLE_PAGES`（`auth/service.py:74-111`）。

| 组 | 页面键 | 权限 | scope |
|---|---|---|---|
| workbench | `workbench.chat` | — | self |
| workbench | `workbench.history` | `history.read` | self |
| workbench | `workbench.agents` | `agent.read` | tenant |
| workbench | `workbench.todos` | `todo.read` | self |
| workbench | `workbench.schedules` | — | self |
| workbench | `workbench.knowledge` | `knowledge.read` | agent |
| workbench | `workbench.scenes` | — | tenant |
| 正式业务 | `admin.agents` | `agent.read` | agent |
| 正式业务 | `admin.skills` | — | agent |
| 正式业务 | `admin.memory` | `memory.read` (+`resource_kind: agent`) | agent |
| 正式业务 | `admin.models` | — | platform |
| 正式业务 | `admin.channels` | — | platform |
| 组织与权限 | `admin.members` | `tenant.members.read` | tenant |
| 组织与权限 | `admin.roles` | `tenant.members.read` | tenant |
| 组织与权限 | `admin.organization` | `tenant.org.read` | tenant |
| 平台运维 | `admin.tenants` / `admin.branding` / `admin.settings` / `admin.logs` | — | platform |
| 旧个人 | `personal.agents` | `agent.read` | self |
| 旧个人 | `personal.channels` | — | self |
| 旧个人 | `personal.memory` | `memory.read` | self |
| 旧个人 | `personal.tools` | `tool.read` | self |
| 旧个人 | `personal.skills` | `skill.read` | self |

`_TENANT_ADMIN_CORE_PAGES`（`auth/service.py:119-121`）对 tenant_admin 豁免 `admin.members` /
`admin.roles` / `admin.organization` 的菜单拒绝。

### 1.2 运行时 grant 分布（只读查询）

```
SELECT resource_kind, action, COUNT(*) n FROM role_resource_grants
GROUP BY resource_kind, action ORDER BY n DESC
```

| kind | action | 条数 |
|---|---|---|
| tool | configure / execute / read | 各 82 |
| **menu** | **view** | **80** |
| skill | edit / enable / read / use | 各 43 |
| agent | edit / enable / read / use | 各 11 |
| model | read / use | 各 7 |

**旧个人菜单 grant 实测（关键迁移输入）**：库内 5 个角色持有 `nav:personal.*`：

| 角色 | code | builtin | `nav:personal.*` 数量 |
|---|---|---|---|
| `role_g4nqcBAgX-T5efUm` | member | 1 | **0** |
| `role_VCw3X4aelR8D9nv-` | tenant_admin | 1 | 5 |
| `role_EMlFcuNSqyIHPxkU` | test15-1（自定义） | 0 | 0 |
| `role_Fi2QN6cZPjvY6Uth` | member | 1 | 5 |
| `role_88GUL-gwh1sA50oP` | tenant_admin | 1 | 5 |
| `role_YW2PflP4oKURHuMa` | e2e_m（自定义） | 0 | 0 |

结论：

1. 存在**持有旧个人 grant 的内置角色**（`tenant_admin` ×2、`member` ×1）——映射必须覆盖。
2. 存在**不含任何 `nav:personal.*` 的内置 member 角色**（`role_g4nqcBAgX-T5efUm`）——迁移**不得**为其
   推定授权（spec: 不把不存在的旧授权推定为应授权）。
3. 存在自定义角色持有 `nav:admin.models` / `nav:admin.scenes` 等**非个人**菜单 grant（`role_EMlFcuNSqyIHPxkU`）——
   映射仅处理 `nav:personal.*`，不得改写其他菜单项或非菜单 grant。

### 1.3 旧个人 URL 与 API

| 旧入口 | 现状 | 迁移目标 |
|---|---|---|
| `#view-personal-agents` / `personal-console.js` 视图 | `personal-console.js:62-85` | 正式 `agents` 页 |
| `#view-personal-channels` | 同上 | 正式 `channels` 页 |
| `#view-personal-memory` | 同上 | 正式 `memory` 页 |
| `#view-personal-tools` / `#view-personal-skills` | 同上 | 正式 `skills` 页（页内资源各自鉴权） |
| `GET/POST /api/memory/personal` | `route_registry.py:238-239` | 薄适配 → 统一记忆服务 |
| `GET /api/memory/personal/content` | `route_registry.py:240` | 同上 |
| `GET/POST /api/personal/channels` | `route_registry.py:241-242` | 薄适配 → 统一渠道服务 |
| `GET/POST /api/personal/channels/<id>` | `route_registry.py:243-244` | 同上 |
| `GET/POST /api/personal/resources` | `route_registry.py:245-246` | 薄适配 → 统一工具/技能参数 |

正式业务接口真值：`/api/agents`（`route_registry.py:15-20` / `:222-224`，policy `tenant`）、
`/api/memory` 与 `/api/memory/content`（`:288-289`，policy `tenant` + `memory.read`）、
`/api/tenant/channels`（`:95-98`，policy `tenant`）、`/api/skills`、`/api/skills/content`、`/api/tools`（`:197-199`）。

## 2. 默认指针

| 指针 | 真值列 | 实测值（只读） |
|---|---|---|
| 租户默认 | `tenants.default_agent_id` | `tnt_xNHlQIA2XP-z6nQG → my-assistant-admin`；`tnt_EA3qM-lHPLD8ZPwW → my-assistant-admin-test15` |
| 用户默认 | `memberships.default_agent_id`（`_migration_14`） | 仅 1 条非空：`usr_2EaG0EhEqh85w60w @ tnt_EA3qM… → my-assistant-admin-test15-RC001`；其余 4 条为 `NULL` |
| 成员编辑版本 | `memberships.version` | 值域 1–5，**已被成员编辑器用作乐观锁**，故用户默认需独立版本列 |

实测两个租户默认指向的绑定 `private_owner_user_id` 均为 `NULL`（合法共享指针），
**本库不存在非法租户默认**；但 spec 要求实现修复路径，故仍需迁移代码与可复跑测试。

## 3. 资源 owner / scope

| 资源 | 归属列 | 实测 |
|---|---|---|
| 智能体 | `agent_bindings(tenant_id, private_owner_user_id, origin, cloned_from_agent_id)` | 9 条绑定，**全部 `private_owner_user_id=NULL`**；`origin` 全部为 `unknown` |
| 渠道实例 | `tenant_channel_instances(tenant_id, scope, owner_user_id, agent_id, active, governance_disabled_*)` | 2 条，**全部 `scope='tenant'`、`owner_user_id=NULL`**；无个人实例 |
| 个人记忆 | `<tenant shared_root>/users/<user_id>/memory`（`common/state_dir.py:421`） | 由实际根派生，无独立表 |

**来源保护实测**：`origin` 全部为 `unknown`，而 `unknown` 计入 `SUPPLIED_ASSISTANT_ORIGINS`
（`agent/personal_assistant.py:295-298`、`channel/web/web_channel.py:562-564`）。因此**现有 9 个绑定
全部受删除来源保护**，普通 owner 与管理员 owner 均不可删除——这正是 spec 要求的「来源保护对两种角色一致」。

## 4. 旧开关

| 开关 | 声明 | 默认 | 当前读取点 | 迁移结论 |
|---|---|---|---|---|
| `member_personal_console` | `auth/policy.py:527-532`、`config.py:285-290` | `True` | `personal_capability_enabled`（`auth/policy.py:580-596`） | 迁移为共用功能限制；完成后移除运行读取 |
| `user_private_agent_management` | 同上 | `True` | 同上 | 同上 |
| `personal_memory_write` | 同上 | `True` | 同上 | 同上 |
| `personal_channel_onboarding` | 同上 | `True` | 同上 | 同上 |
| `personal_channel_runtime` | 同上 | **`False`** | 同上 + `PERSONAL_RUNTIME_ACCEPTED_TYPES`（`channel/channel_instances.py:1224`，当前 `frozenset()`） | 保持关闭（无真实往返证据），登记为未验收 |

页面→开关映射：`PERSONAL_PAGE_CAPABILITIES`（`auth/policy.py:551-560`）。**代码中不存在旧键别名或
legacy-key 迁移路径**，因此开关迁移需新建，且必须保留显式关闭意图。

## 5. 客户端使用情况

| 客户端 | 现状 | 依据 |
|---|---|---|
| Web 控制台（正式页） | 调用 `/api/agents`、`/api/memory`、`/api/tenant/channels`、`/api/skills`、`/api/tools` | `route_registry.py` |
| Web 个人页 | 调用 `/api/agents?view=personal`、`/api/personal/channels`、`/api/memory/personal`、`/api/personal/resources` | `personal-console.js:62-85` |
| 账号菜单「我的资源」 | **不预加载数据**，仅按 `console_pages` 投影重算可见性 | `console.js:339-392`（`_renderAccountResources` / `refreshAccountResources`） |
| Desktop | 使用与 Web 相同契约 | 原生 Desktop 登录/传输**未验收**（见 1-4） |

扫描「正式页面调用旧 API」：`personal-console.js` 是唯一消费 `/api/personal/*` 与
`/api/memory/personal*` 的前端模块；正式页面（`console.js`）不调用它们。因此兼容期结束后可
按调用观测退役。

## 6. 可复跑证据

```
.venv/bin/python - <<'PY'   # 只读
import sqlite3
con = sqlite3.connect('file:identity.db?mode=ro', uri=True)
...
PY
→ 5 个角色持有 nav:personal.*（2×tenant_admin、1×member；另有 2 个内置/自定义角色为 0）

rg -c '^_migrations\.append\(_migration_' auth/store.py
→ 24

rg -n 'personal\.' scripts/route-baseline.txt
→ /api/memory/personal(.*)、/api/personal/(channels|resources) 共 9 条 personal policy 行
```
