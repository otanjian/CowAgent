## MODIFIED Requirements

### Requirement: 固定个人和租户共享资源策略

系统 SHALL 以可信 Agent 租户绑定及资源所有者实现固定策略，不提供 SELF/部门/CUSTOM 等可编辑范围枚举。私有 Agent 及其资产 SHALL 仅由所有者本人读取、使用和维护；本人所有权 SHALL 作为统一对象授权来源，覆盖读、用、配置、调试、启停以及对本人自建对象的删除，无需逐对象角色 grant，并受当前有效身份、个人能力开放状态和租户策略约束。所有权 MUST NOT 扩大模型、工具、技能等依赖资源的授权，也不授予公共资源维护资格。

所有权的读取、使用与维护资格 SHALL 由 Agent 租户绑定本身派生：判定 SHALL 从既有绑定派生，MUST NOT 要求写入用户级 grant 记录或新增授权载体，该系统内 MUST NOT 存在第二份所有权真相。该派生 SHALL 同时作用于列表投影与发送路径，使同一主体对同一私属智能体得到一致结论。放宽 MUST NOT 扩展到非所有者、MUST NOT 扩大到共享 Agent 的同类维护动作或公共资源、MUST NOT 跨租户匹配。

明确租户共享的 Agent 资产 SHALL 继续按成员有效功能和资源授权开放。会话、消息、runs 和个人记忆 SHALL 额外校验个人 owner 及租户；tenant_admin 或平台 all MUST NOT 绕过本人私有内容边界。管理员的资源归属、数量、状态和用量治理投影 SHALL 与私有配置、正文和凭证分离。部门或岗位变更 MUST NOT 扩大资源权限，历史或缺失归属不得自动视为共享；列表、检索、总数和分页 SHALL 先授权再计算。

#### Scenario: 共享 Agent 中的个人会话
- **WHEN** 两名成员均可查看租户共享 Agent，且各自拥有会话记录
- **THEN** 普通成员仍仅能读取本人会话、消息和 runs，Agent 共享不使另一成员的个人历史可见

#### Scenario: 私有资产或组织变化
- **WHEN** 成员获得业务读取权限或被移到其他部门，但目标资产仍属于他人的私有 Agent
- **THEN** 组织和角色读取权限不绕过所有者策略，只有所有者本人在满足有效身份及相应动作条件时可读

#### Scenario: 列表分页与缺失归属
- **WHEN** 用户检索包含不可见资源的列表，或遇到未绑定 Agent、无法确定所有者的历史个人资源
- **THEN** 列表与总数仅统计授权资源，缺失归属的资源被拒绝而非当作租户共享返回

#### Scenario: 所有者无需逐对象维护 grant

- **WHEN** 有效 member 维护本人私有 Agent，但角色没有该对象的编辑或启停 grant
- **THEN** 在本人能力开放且租户策略允许时可以维护，不向其授予共享 Agent 的同类动作

#### Scenario: 管理员身份不授予私有内容

- **WHEN** 非 owner 的 tenant_admin 或平台管理员请求成员私有 Agent、会话、记忆或核心文件
- **THEN** 私有内容访问被拒绝，管理者只能通过获准治理接口查看必要的脱敏元数据

#### Scenario: 所有者仅持功能权限即可读可用

- **WHEN** 成员持有 `agent.read` / `agent.use`，其角色未携带任何 `agent:<id>` 资源 grant，而被请求的私属 Agent 的 `private_owner_user_id` 正是该成员
- **THEN** 该私属 Agent 对其出现在可见列表中且可发起会话，列表投影与发送路径给出同一结论，不出现「列表可见但发送被拒」或「有权限却不可见」

#### Scenario: 他人私属资源不放宽

- **WHEN** 成员请求同租户内 `private_owner_user_id` 为**他人**的私属 Agent，且其角色未携带该 Agent 的资源 grant
- **THEN** 该请求被拒绝且该 Agent 不出现在其列表中，不因请求者本人也拥有某个私属 Agent 而放行

#### Scenario: 写动作不随所有权放宽

- **WHEN** 私属 Agent 的所有者本人尝试编辑、调试或启停该 Agent，而其角色未携带该 Agent 的 `edit` / `enable` 资源 grant
- **THEN** 该维护动作在有效身份、个人能力开放状态与租户策略允许时放行，且 MUST NOT 因此获得共享 Agent 的同类动作资格

