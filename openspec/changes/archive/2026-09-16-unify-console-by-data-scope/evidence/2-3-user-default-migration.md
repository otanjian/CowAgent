# 2.3 用户默认独立版本迁移（成员 / 租户默认指针）

本文件记录 `tasks.md` 2.3。实现为 `auth/store.py:1233` `_migration_25`（已通过
`_migrations.append` 挂到迁移链），测试为 `tests/test_user_default_migration.py`。

## 1. 为什么单独版本化

`memberships.default_agent_id` 原先由 `memberships.version` 版本化，而后者会被显示名/部门/岗位等
**任何**编辑器改动抬高：一次「设我的默认」与一次无关的资料编辑会互相冲突，且客户端拿到一个版本号
无法判断它对应哪一份草稿。迁移把默认指针拆成自己的列：

| 列 | 含义 |
|---|---|
| `default_agent_revision` | 成员默认自己的乐观并发值（`set_user_default` 以它做版本比较），`INTEGER NOT NULL DEFAULT 1` |
| `default_agent_origin` | `'user'` = 成员自己选的；`'provisioned'` = 系统供给登记；`NULL` = 没有已登记偏好 |

`NULL` 语义是为任务 4.6 留的：供给流程可以初始化一个空偏好，却永远不能覆盖成员自己的选择。

## 2. 一次性修复非法指针（保留归属 + 审计）

同一迁移在**同一事务**内修复永远解析不出来的指针，并且**不碰所有权**：

| 指针 | 非法情形 | 处理 |
|---|---|---|
| 租户默认 | 未绑定 / 绑到别的租户 / **私有** | 清空（新会话回落到共享智能体）；`private_owner_user_id` **原样保留**——共享是显式的、有审计的动作（`make_agent_tenant_shared`），不能成为设默认的副作用 |
| 成员默认 | 未绑定 / 绑到别的租户 / 私有给**另一个**成员 | 清空 |

每一次修复与每一次来源回填都在本事务内追加审计事件：

- `tenant.default_agent.repaired`
- `member.default_agent.repaired`
- 合法指针回填 `default_agent_origin='user'`（升级前的代码无法区分两种来源，把既有偏好当作成员
  自己的，是唯一不会在后续被静默覆盖的选择）

审计载荷含 `repaired_from`、`reason`、`private_owner_preserved`、`user_id`，不带凭据。

## 3. 幂等与中断

迁移由迁移运行器包在**一个事务**里，因此「加列 + 修复 + 审计」要么一起提交要么一起回滚；
重跑时已修复的行不再匹配（`default_agent_id IS NULL`），合法行只在 `default_agent_origin IS NULL`
时回填，故幂等。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_user_default_migration.py -q
→ 21 passed
```

`tests/test_user_default_migration.py` 以一套**旧库状态**为输入（合法个人默认、合法共享默认、未绑定、
跨租户、他人私有、私有租户默认并存），断言：

- `default_agent_revision` / `default_agent_origin` 两列存在、非空、且与 `memberships.version` 独立；
- 合法默认被保留并标记来源；四类非法默认被清空；
- 每次修复都有对应审计事件，且 `private_owner_user_id` 未被任何一次修复改写；
- 重放（再次打开同一库）不产生第二次修复或第二条审计。

> 命名说明：`evidence/1-3-role-and-scope-matrix.md` 的缺口表写作
> `memberships.default_agent_version`，落地列名是 `default_agent_revision`（外加 `default_agent_origin`）。
> 以本文件为准。

## 5. 消费方

`IdentityService.set_member_default_agent` / `member_default_agent_id`
（`auth/service.py:730`、`:745`）读写这两列；写入带审计（`member.set_default_agent`），审计失败与写入
同事务回滚（见 2.5 证据）。
