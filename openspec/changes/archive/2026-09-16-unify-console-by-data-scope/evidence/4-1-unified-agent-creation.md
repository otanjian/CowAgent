# 4.1 统一创建生命周期

本文件记录 `tasks.md` 4.1：把「管理创建」和「私有创建」合入同一生命周期
（同一表单、同一接口、同一领域服务），并让归属、配额、幂等、审计、补偿对两种角色一致。

## 1. 归属由服务端派生

`channel/web/web_channel.py:_creation_scope(ctx, body)`（`:804`）是唯一判定：

* 普通成员 → `private`（请求要 `shared` 直接 403，成员不因「租户还空着」被隐式共享）；
* 管理员 → 请求缺省 `shared`，显式 `private` 时创建本人私有对象；
* 未知 scope 值 → 400，不静默落到某个默认。

owner 一律取自已校验的 `ctx.user_id`，请求体里的 owner/tenant 字段不参与。

## 2. 绑定与首个对象规则

`_adopt_created_agent_for_tenant(ctx, agent_id, *, scope)`（`:843`）：

| scope | 绑定动作 | origin |
| --- | --- | --- |
| `private` | `bind_private_agent_with_quota` | `user_created` |
| `shared` | `bind_agent` | `admin_created`（本 change 新增的 origin） |

「首个对象承接租户默认」只在 `shared` 分支发生（`appoint_tenant_default_agent`）。
普通成员创建首个私有对象**不会**把它变成租户默认，也不会共享——这正是
`agent-workbench` 规范里「普通用户创建首个智能体」场景。

## 3. 失败补偿

`_rollback_created_agent(ctx, agent_id)`（`:887`）在绑定/审计任一步失败时按序回收：
名册删除 → 身份解绑 → 工作区移除。补偿路径调用
`delete_agent(..., require_unreferenced=False)`——补偿要能删掉半成品，不能被引用冲突卡住。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_unified_agent_creation.py -q -p no:randomly
→ 15 passed
```

`tests/test_unified_agent_creation.py` 逐条钉住：成员创建私有对象（owner 与 `user_created`
origin 正确）、成员首个对象不成为租户默认、成员请求共享被 403、成员对象对同事不可见、
owner 可显式要求私有、未知 scope 被 400、管理员缺省建共享（`admin_created`）、
管理员首个共享对象初始化租户默认、管理员可显式建私有且不成为租户默认、
控制台创建绝不记录成 `unknown` origin、管理员共享对象可正常退役、
成员获配的助理仍受来源保护、绑定被拒时补偿回滚、回滚后同 id 可重试。
