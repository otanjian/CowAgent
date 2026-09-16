# 6.2 目标候选与创建归属派生

本文件记录 `tasks.md` 6.2。6.1 建立了「同一页面、同一接口、按对象范围区分角色」；
6.2 把**归属怎么定下来**这件事收进服务端，使界面候选与写入判定同源。

## 1. 请求不再声明 scope / owner

`channel/web/admin_handlers.py:_requested_channel_scope(ctx, svc, data)` 只读两个来源：
已验证身份，和目标绑定事实。请求体里的 `scope`、`owner_user_id` **完全不读**。

派生规则：

| 调用者 | 目标 | 结果 |
| --- | --- | --- |
| 仅本人范围 | 任意 | `("user", 当前用户)`，请求要 `tenant` 也一样 |
| 其他 | 本人私有 Agent | `("user", 当前用户)` |
| 其他 | 共享 / 他人私有 / 未知 / 跨租户 | `("tenant", None)`，再交既有公共目标规则判定 |

判定只有一处：`IdentityService.channel_target_scope()`（`auth/service.py:8200`），
它读的是 `agent_bindings` 的 `tenant_id` 与 `private_owner_user_id`。

于是规范里「不得隐式改变归属」成为可检验的命题：一条连接落在哪个归属里，是**调用者
能点到哪个目标**的函数。owner 永远是服务端当前身份生成的
（`user-private-agent-management`：owner SHALL 由服务端当前身份生成，不能代填另一用户），
因此「代填同事 owner」与不代填得到相同归属——已由
`test_a_colleague_is_never_named_as_the_owner` 钉住。

## 2. 候选由服务端下发

`channel/web/web_channel.py:_channel_target_candidates(ctx)`（`:10001`）复用写路径同一个
对象谓词 `_iter_tenant_agents(ctx, action=SCOPE_MANAGE)`，并跳过已停用目标。每条候选带上
**它会产生的归属**：

```
{"id": ..., "name": ..., "scope": "user" | "tenant", "is_tenant_default": bool}
```

`scope` 由 `private_owner_user_id == ctx.user_id` 决定，与 `channel_target_scope` 同一规则，
所以**候选和写入不可能互相矛盾**——这正是规范要的
「界面候选也不允许选择这些目标」。

下发位置：`GET /api/tenant/channels` 的 `targets` 字段，与 `channel_types` 并列。
理由与类型目录相同：只有服务端知道哪些目标这个调用者点得动。

## 3. 前端只做筛选，不做判定

`tenantChannelAgentOptions()`（`console.js:14159`）：

* 有服务端候选 → 用它；本人范围只保留 `scope === 'user'`，且**不给空目标**
  （空目标在本人创建时必被 `personal_agent_required` 拒绝，摆一个点不成的选项就是
  「可点但被拒」形状）；
* 服务端未下发（旧部署）→ 退回本地 `agentCatalog`，且**不做 scope 过滤**——回退项
  本来就没有派生归属，用归属去过滤它会把成员的选择框清空，而回退路径存在的意义
  恰恰是让旧部署可用。已被 `an unread or older backend still produces a usable picker`
  钉住（这条用例在实现过程中确实抓到过一次该缺陷）。

管理范围保持原样：全部候选，保留空目标（租户公共连接可以刻意不绑定）。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_tenant_channel_member_access.py -q -p no:randomly
→ 24 passed
```

`ChannelTargetCandidateTests`：

* 成员只被提供本人私有目标，且标为 `scope: "user"`；
* 管理员的本人私有目标标 `user`、共享目标标 `tenant`（界面因此能在保存前说明归属）；
* 停用目标既不进候选、也会在写入时被拒（`personal_agent_disabled`）——两半同源；
* 同事的私有目标对任何人都不是候选。

`MemberCreateTests` 覆盖派生：成员建本人（请求要 `tenant` 也一样）、管理员缺省建公共、
管理员本人私有目标建本人、同事不被代填为 owner。

前端 `tests/test_tenant_channel_card_frontend.cjs`（`bootPicker` 三例）：候选来自服务端
且不发明未下发的目标、本人范围只列本人且无空目标、旧后端回退可用。
