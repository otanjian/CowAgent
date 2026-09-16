# 1.3 双租户 / 多角色功能与数据范围矩阵

本文件记录 `tasks.md` 1.3 的矩阵定义与夹具现状。矩阵是后续各阶段验收用例的基准；实现阶段
（2.x–7.x）的所有拒绝/放行断言都回指本表的行。

## 1. 参与主体

| 代号 | 主体 | 构造方式（现状夹具） |
|---|---|---|
| `T1/U1` | 租户 1 普通用户 A | `WebAppHarness.member("alice", ["member"])` |
| `T1/U2` | 租户 1 普通用户 B | `WebAppHarness.member("bob", ["member"])` |
| `T1/TA1` | 租户 1 租户管理员 | `WebAppHarness.member("ta", ["tenant_admin"])` |
| `T2/TA2` | 租户 2 租户管理员（**非 T1 owner**） | `stack_factory` 扩展第二租户（现有先例：`tests/test_personal_console_multi_tenant_authorization.py`） |
| `PLAT` | 平台管理员（`authorization_mode=all`） | `bootstrap` 的 `root` 账号 |
| `CUSTOM` | 自定义角色成员（细粒度 grant） | `WebAppHarness.role(code, permissions, grants)` + `member(..., [code])` |

夹具真值：`tests/_helpers.py:250`（`WebAppHarness`）、`:399`（`member`）、`:377`（`role`）、`:355`（`private_agent`）。

## 2. 功能（动作）矩阵

「页面资格」= `/auth/context` 的 `console_pages[key]`（`available` / `read_allowed` / `menu_denied` / `reason`）。

| 动作 | T1/U1 | T1/TA1 | T2/TA2 | PLAT（当前租户 T1） | CUSTOM（仅公共编辑 grant） |
|---|---|---|---|---|---|
| 进入控制台 `/admin` | **ALLOW**（有正式页资格时） | ALLOW | ALLOW（在 T2） | ALLOW | ALLOW（有正式页资格时） |
| 智能体管理列表 | 本人私有 | 共享 + 本人私有 | 空/他租户拒绝 | 仅 T1 范围 | 按 grant 范围 |
| 智能体创建 | 本人私有（服务端定归属） | 本人私有 / 租户共享 | 拒绝（跨租户） | 按管理资格 | 需 `agent.edit` |
| 智能体编辑/启停/删除 | 本人私有 | 共享 + 本人私有 | 拒绝 | 按管理资格 | 需对应 grant |
| 用户默认 `set_user_default` | ALLOW（仅本人） | ALLOW（仅本人） | ALLOW（仅本人） | ALLOW（仅本人） | ALLOW（需可用候选） |
| 租户默认（旧 `set_default`/新命名动作） | **403** | ALLOW（仅共享目标） | 自身租户 ALLOW | 按平台职责 | **403** |
| 记忆列表/正文 | 本人用户记忆 + 本人私有智能体记忆 | 同左 + 获准共享记忆 | 拒绝跨租户 | 同 TA | 按 `memory.read` |
| 记忆写入/删除/清空 | 本人目标 | 同左 + 共享目标（需管理资格） | 拒绝 | 同 TA | 按 grant |
| 渠道列表 | 本人连接 | 租户公共 + 本人连接 | 拒绝 | 同 TA | 按 grant |
| 渠道创建（目标候选） | 仅本人私有 | 本人私有 / 共享 / 公共缺省 | 拒绝 | 同 TA | 需管理资格才能公共 |
| 工具/技能目录 | 获授权目录 | 获授权目录 + 公共维护 | 拒绝跨租户 | 按平台职责 | 目录可读；**公共写入拒绝**（无管理资格） |
| 组织与权限（成员/角色/组织） | **403** | ALLOW | 自身租户 ALLOW | 按平台职责 | **403** |
| 平台运维（租户/品牌/日志/审计） | **403** | **403** | **403** | ALLOW | **403** |

关键约束（来自 spec）：

- **owner 检查先于管理员例外**：`_private_agent_owned_by_another`（`channel/web/web_channel.py:350-373`）
  必须在任何 `is_platform_admin`/`is_tenant_admin` 快捷判定之前运行。现状已在知识写入
  （`_knowledge_write_authorized` `:376-410`）、聊天使用（`:952`/`:973`）、文件（`:10333`/`:10347`）等
  路径满足；4.x 需把同一顺序用于管理列表与维护动作。
- **公共编辑 grant ≠ 管理资格**：`CUSTOM` 列不得因持有公共 tool/skill 编辑 grant 而获得公共写入。
- **PLAT 不扩大数据范围**：`all` 只跳过功能与资源分配限制，仍受所选租户与私有 owner 约束
  （`workbench.models`/`admin.members` 等既有场景已在主规范固定）。

## 3. 数据范围矩阵

| 对象 | 真值 | T1/U1 | T1/TA1 | T2/TA2 | PLAT |
|---|---|---|---|---|---|
| 智能体 | `agent_bindings(tenant_id, private_owner_user_id)` | `tenant=T1 AND owner=U1` | `tenant=T1 AND (owner IS NULL OR owner=U1)` | 他租户不可见（404/空列表） | 当前所选租户内同上 |
| 用户记忆 | `<shared_root>/users/<uid>/memory` | `tenant=T1 AND user=U1` | 同左（**不能**读他人） | 拒绝 | 同 TA |
| 智能体记忆 | 智能体工作区实际根 | 本人私有智能体 | 共享 + 本人私有 | 拒绝 | 同上 |
| 渠道连接 | `tenant_channel_instances(scope, owner_user_id)` | `tenant=T1 AND scope='user' AND owner=U1` | 公共（`scope='tenant'`）+ 本人 | 拒绝 | 同上 |
| 工具/技能/模型 | `role_resource_grants` + 功能权限 | 获授权目录 + 本人可维护配置 | 同上 + 公共维护 | 拒绝 | 按平台职责 |

实测基线（见 `1-2`）：库内 9 条绑定全部 `private_owner_user_id=NULL`、`origin='unknown'`；
2 条渠道实例全部 `scope='tenant'`；仅 1 条 `memberships.default_agent_id` 非空。
因此**默认夹具必须先补齐私有对象**（`private_agent()` / `personal_channel_target()` 已存在，
见 `tests/_helpers.py:355`、`:173`），否则矩阵中「本人私有」行无对象可测。

## 4. 夹具缺口（阶段 2 起需补）

| 缺口 | 影响任务 | 补法 |
|---|---|---|
| 同一 `WebAppHarness` 内建第二租户 | 1.3、2.5、4.7、6.6 | 经 `stack_factory` 扩展，或按既有 `other_tenant` 先例（`tests/_helpers.py:101`） |
| 「非 owner 管理员」在同一租户内的显式构造 | 2.5、4.7 | `member("ta2", ["tenant_admin"])` + `private_agent(alice_uid, agent)` |
| 平台管理员选择租户 A（非其成员） | 2.5、4.2 | `root` 账号 + `X-Tenant-ID: T1` |
| 停用 / 不存在 / 跨租户目标 | 4.7、5.3、6.5、6.6 | roster 中置 `enabled=False`；只给 id；绑到 T2 |
| `memberships.default_agent_version` 列 | 4.4、4.6 | `_migration_25` 新增 |

## 5. 可复跑证据

```
.venv/bin/python -m pytest tests/test_personal_console_multi_tenant_authorization.py \
  tests/test_tenant_channel_console_scope.py tests/test_private_agent_lifecycle.py \
  tests/test_private_agent_owner_reachability.py tests/test_personal_channel_console.py -q
→ 见阶段 2 起始复跑结果（本文件记录矩阵定义，不重复执行）
```

夹具真值行：`tests/_helpers.py:101`（`other_tenant`）、`:173`（`personal_channel_target`）、
`:355`（`private_agent`）、`:377`（`role`）、`:399`（`member`）、`:503`（`_bootstrap_tenant`）。
