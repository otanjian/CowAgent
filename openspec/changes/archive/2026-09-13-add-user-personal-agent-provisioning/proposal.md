## Why

租户管理员新建一个用户后，该用户只得到一个租户共享的入口：租户默认智能体。产品规划 3.1 的用户级定位是「个人空间独立于他人、个人记忆只属于本人」，但当前没有任何用户级私有智能体——同一租户的所有成员共用一个「智能办公助理」，日程、待办与材料整理会写进同一份工作区与同一份记忆，靠会话归属各自隔离而智能体本身不隔离。

本 change 让每个新建用户自动获得一份**自己的**「智能办公助理」：以租户现有的同名智能体为模板克隆出独立工作区、绑定为只有本人（与本租户管理员）可读的私属资源，并登记为该用户的个人默认智能体，使「进入对话不选智能体」时锚定到本人这一份，而不是租户共享的那一份。

## What Changes

- 新建成员（`create-new` 与 `bind-existing`，幂等）时自动为该用户创建一份私有「智能办公助理」：独立 agent id、独立工作区（`<租户共享根>/agents/<新 id>`）、`private_owner_user_id` 为该用户。
- 克隆来源：该用户所属租户**已绑定**的智能体中，启用且名称为「智能办公助理」的那个；名称与显式 id 均可由配置覆盖。找不到来源时成员照常创建成功，跳过本次创建并在响应与审计中给出可诊断原因，MUST NOT 阻断用户创建。
- 人设个人化：克隆后 `USER.md` 改写为该用户本人的资料（显示名/用户名/岗位/部门），并把源人设中「原所属人」的标识（可配置别名表，默认 `admin`）在 `AGENT.md` 与描述类字段中替换为该用户的显示名，使新用户拿到的人设指向本人。
- 新增用户级默认智能体登记（`memberships.default_agent_id`），并把它接入缺省解析：**个人默认 → 租户默认 → 租户共享最小稳定 id → 任意可用绑定**，且个人默认仅在「仍是本租户可用绑定」且「调用者可到达（本人私属或租户共享）」时生效。
- 个人默认智能体被删除时清理其登记，不留脏指针；克隆/绑定中途失败时补偿已产生的半成品（roster 条目与工作区），成员创建不受影响。
- MUST NOT 改变：租户默认智能体仍是**租户共享**资源（本 change 不移交租户默认指针、不清除既有私有归属）、`agent_bindings` 表结构、既有私有归属的显式设置/共享转换规则、平台管理员创建租户管理员的路径。

## Capabilities

### New Capabilities
- `user-personal-agent-provisioning`: 新建成员时自动克隆租户的「智能办公助理」为该用户的私有智能体并登记为个人默认；覆盖来源解析、命名与工作区归属、人设个人化、幂等、缺源跳过、失败补偿、删除清理、权限门槛与审计。

### Modified Capabilities
- `agent-chat-launch`: 「租户恒有可解析的默认智能体」新增用户级优先层——个人默认智能体优先于租户默认，以及它在失效、不可达、跨租户、缺权限时的回落与拒绝场景。

## Impact

- 后端身份域：`auth/store.py`（迁移 14：`memberships.default_agent_id`）、`auth/service.py`（`create_member` 编排调用；`resolved_default_agent_id` 增加用户维度；`release_deleted_agent` 清理个人默认登记；新增个人默认的读写方法）。
- 后端智能体域：新增 `agent/personal_assistant.py`（模板解析、id/工作区规划、克隆+绑定+个人化、跳过与补偿的编排，复用 `AgentAdminService.clone_agent` 与 `IdentityService.bind_agent`）；`agent/admin.py` 新增人设个人化原语。
- Web 层：`channel/web/web_channel.py` 的缺省解析调用点透传 `ctx.user_id`；`channel/web/admin_handlers.py` 的成员创建响应回带个人智能体结果（成功/跳过/失败原因）。
- 配置：`config.py` 新增个人助理模板名称/显式 id 与源所属人别名表（均有默认值，不配置也能工作）。
- 测试：新增 `tests/test_user_personal_agent_provisioning.py`（来源解析、幂等、缺源跳过、私属不可被他人读取、个人默认解析优先级与失效回落、删除清理、补偿、审计），并复核既有默认智能体与成员管理用例不回归。
- 不改动：`agent_bindings` 表结构与 `cloned_from_agent_id` 偏唯一索引（个人助理克隆**不写**该列，否则同租户第二个用户的克隆会撞唯一约束）、租户默认的共享转换语义、`scripts/route-baseline.txt`（不新增路由）。
