# 2.1 统一对象范围（智能体 / 记忆 / 连接）

本文件记录 `tasks.md` 2.1：把「这个对象是否在调用者范围内」收敛成一个实现，并让列表、详情与写入
都问同一个问题。矩阵沿用 `evidence/1-3-role-and-scope-matrix.md` 的行。

## 1. 单一权威

新增 `auth/object_scope.py`（唯一实现，无第二份归属判定）。对外只提供「一个已解析请求上下文 +
一个对象」的问答：

| 谓词 | 语义 | 关键分支 |
|---|---|---|
| `allows_agent(binding, action=USE\|MANAGE)` | 智能体是否在范围内 | 私有 → **只看 owner**；共享 → `USE` 放行（功能 grant 另判），`MANAGE` 需管理资格 |
| `allows_personal_memory()` | 本人用户记忆 | 只要 `tenant + user`（根路径由身份推导，无对象 id） |
| `allows_agent_memory(binding)` | 智能体记忆 | 复用 `allows_agent(..., MANAGE)`，共享智能体记忆是租户资源 |
| `allows_channel_instance(row)` | 连接实例 | 行内 `scope='user'` → **只看 owner**；`scope='tenant'` → 管理资格；未知 scope → 先 owner，永不落到「公共」 |
| `allows_public_configuration()` | 公共配置维护 | 仅管理资格；**成员持有的公共 `edit` grant 不替代**（见 2.2） |

两条不变式（模块 docstring 明文，测试逐条钉住）：

1. **范围是推导的，不是传入的**：`ObjectScope.from_context` 只读已校验的
   `auth.runtime.RequestContext`；返回体可以指名目标，但永远不能指定自己的 `owner`/`scope`/`tenant`。
2. **owner 检查先于管理员例外**：所有私有分支只由 owner 决定，不读 `is_admin`。非 owner 管理员与
   任何非 owner 一样被拒，管理员仍治理租户共享面。

范围随上下文为 `None` 时为空集（fail-closed）。

## 2. 消费点

| 面 | 位置 | 问的问题 |
|---|---|---|
| 智能体管理读（列表/详情/总数） | `channel/web/web_channel.py:9746` `_iter_tenant_agents(action=MANAGE)`、`:9806` `_tenant_agents_admin_projection` | `allows_agent(MANAGE)`，`include_disabled=True`（管理面要能看到已停用对象再启用） |
| 聊天/工作台「可用」读 | 同文件 `_iter_tenant_agents(action=USE)`（`?view=workbench`） | `allows_agent(USE)` + 该智能体的 `read` grant |
| 「我的智能体」 | `?view=personal`（`_personal_agents_projection`） | 只本人私有 |
| 智能体写入（编辑/启停/归档/删除、技能开关与技能正文） | `channel/web/web_channel.py:641` `_require_agent_action`（`:602` 先 `_private_agent_owned_by_another`，owner 优先）、`:615` `_require_agent_management_scope`（`:638` 问 `allows_agent(MANAGE)`） | owner → 管理资格，二者按序，不并列 |
| 智能体记忆（共享范围） | `channel/web/memory_console.py:347` `_resolve_agent_target` | `allows_agent_memory(binding)`；成员即使有 `chat.use` 也不得读共享智能体记忆 |
| 用户记忆 | 个人根由身份推导（`<shared_root>/users/<uid>`），无对象 id 可伪造 | `allows_personal_memory()` 的同一前提 |
| 连接实例 | `allows_channel_instance(row)` | 行内 `scope`/`owner_user_id` 决定；请求字段不参与 |

`_agent_binding_for(ctx, agent_id)` 是转接器：把智能体 id 换成 `agent_bindings` 行交给谓词，使
`web_channel` 各处不再各自读行判定。

## 3. 阶段边界（不是本任务的遗漏）

- 连接（渠道）的**页面级**统一在阶段 6/7（`tasks.md` 6.x/7.x）：`/api/tenant/channels` 目前仍是
  `_require_tenant_admin`（`channel/web/admin_handlers.py:976`），个人渠道另有既有面。
  `allows_channel_instance` 已就位并有单测，阶段 6 以它替换页面自己的判定，不再新增第二份。
- 记忆/工具/技能/模型的**共用页面**在阶段 5；本阶段只保证这些接口背后的归属判定已收敛。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_object_scope.py -q
→ 20 passed, 3 subtests passed
```

`tests/test_object_scope.py` 逐条钉住：owner 先于管理员、管理员不因 `is_admin` 多读一个私有对象、
共享对象在 `USE`/`MANAGE` 两个动作下的差异、未知 `scope` 不落公共、伪造 `owner`/`tenant` 无效、
空上下文 fail-closed、公共配置只认管理资格。

配套回归（详见 2.5 证据）：`tests/test_agent_workbench.py`、
`tests/test_private_agent_owner_reachability.py`、`tests/test_memory_console_scope.py`、
`tests/test_private_agent_lifecycle.py`、`tests/test_private_agent_capability_save.py`、
`tests/test_tenant_channel_instances_service.py`。
