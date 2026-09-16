# 2.4 旧个人菜单 → 正式菜单的事务化幂等映射

本文件记录 `tasks.md` 2.4。实现分两处：映射表在 `auth/policy.py:539`
`LEGACY_PERSONAL_MENU_MAP`，迁移在 `auth/store.py:1348` `_migration_26`；测试为
`tests/test_console_menu_mapping.py`（替代原 `tests/test_personal_console_menu.py`）。

## 1. 映射

```
personal.agents   → admin.agents
personal.channels → admin.channels
personal.memory   → admin.memory
personal.tools    → admin.skills      # 多对一：控制台只有「工具与技能」一个页面
personal.skills   → admin.skills
```

多对一是刻意的：同时持有 `personal.tools` 与 `personal.skills` 的角色**只**获得一次
`admin.skills`，不发明第二个页面。

`BUILTIN_MENU_DEFAULTS`（`auth/policy.py:506`）同步改为授予**正式页**（`admin.agents` /
`admin.channels` / `admin.memory` / `admin.skills` 等），因此新建租户与迁移后的租户得到同一套页面；
`tenant_admin` 仍是 `member` 的严格超集。

## 2. 迁移行为（逐角色）

`_migration_26` 只处理**实际持有**旧 id 的角色：

1. 插入映射后的正式 id，带 `NOT EXISTS` 守卫（已有该页的角色不受影响）；
2. 从该角色删除旧 id；
3. `roles.version = version + 1`，并追加审计 `role.menu.legacy_personal_mapped`（载荷含
   `removed` / `added` / `version`）。

## 3. 三条保护

| 保护 | 做法 | 为什么 |
|---|---|---|
| 自定义授权不被破坏 | 只增删映射命中的 `menu` grant；其它菜单 id 与全部非 `menu` grant 原样保留 | 迁移不是重写授权 |
| 主动撤权不被回滚 | 只映射**当前存在**的旧 id，不推断「缺失 = 曾被撤权」并补回 | 管理员的显式撤权优先于迁移 |
| 多对一不重复 | 目标 id 用集合去重 + 插入守卫 | 同页只出现一次 |

事务性与幂等：迁移运行器把本迁移包在一个事务里（加、删、版本、审计一起提交或一起回滚）；重跑时
已无旧 id 可映射，`continue`。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_console_menu_mapping.py -q
→ 29 passed
```

`tests/test_console_menu_mapping.py` 覆盖：

- 旧 id 全部消失、映射后的正式页出现，且**只**出现一次（多对一）；
- 自定义角色的其它菜单与资源 grant 逐条保留；
- 被显式撤掉的正式页不会被迁移补回；持有旧 id 但从未持有正式页的角色**获得**正式页；
- `roles.version` 被抬高一次，审计事件记录了 removed / added；
- 重放不给任何角色第二次变化，也不产生第二条审计；
- 新建租户（`BUILTIN_MENU_DEFAULTS`）与迁移后的租户页面集合一致；
- 零可见性回归：`personal.*` 不再可读，正式业务页在迁移后仍被授权并打开。

## 5. 相关回归

`tests/test_personal_console_menu.py` 已删除（其断言建立在旧 `personal.*` 授权之上）；
`tests/test_agent_workbench.py`、`tests/test_private_agent_owner_reachability.py` 随菜单默认值与
管理范围一起更新。全套见 2.5 证据。
