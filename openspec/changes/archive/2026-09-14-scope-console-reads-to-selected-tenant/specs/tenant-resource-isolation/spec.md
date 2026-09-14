## MODIFIED Requirements

### Requirement: Agent 租户绑定与现有标识保持稳定

系统 SHALL 保持 Agent ID 全局唯一并为每个可访问 Agent 建立唯一租户绑定；读取 Agent 或业务存储前 SHALL 校验该绑定。租户列表和默认 Agent 选择 SHALL 只在当前租户内进行，无 Agent 时返回空列表。既有业务 session/run ID 和每 Agent 存储 SHALL 保持兼容，未绑定或绑定读取失败时 MUST 拒绝访问而不是使用全局默认 Agent。

该「只在当前租户内」的约束 SHALL 同样约束平台管理员在租户业务控制台中的读取：其可见 Agent 集合由**当前所选租户**的绑定决定，MUST NOT 因平台资格扩大为实例全量名单；未选租户时 SHALL 为无可见 Agent。跨租户的智能体管理 SHALL 经明确目标租户的专用平台入口独立验证并记录审计，MUST NOT 以业务控制台的租户选择代替该入口，也 MUST NOT 借此建立 Membership 或改变操作者的业务租户。

#### Scenario: 读取本租户安全概览
- **WHEN** 有权成员请求本租户的 Agent 列表
- **THEN** 仅返回已绑定且获准查看的 Agent 名称等安全字段，不返回 workspace、模型凭据或其他租户记录

#### Scenario: 相同会话标识属于不同 Agent
- **WHEN** 两租户的不同全局 Agent 使用相同业务 session 字符串
- **THEN** 请求仅能访问所选租户合法 Agent 下的记录，不能靠 session 字符串控制另一租户资源

#### Scenario: 缺失或损坏绑定
- **WHEN** 新租户没有 Agent，或绑定/配置读取失败
- **THEN** 无 Agent 时返回空列表，读取失败时拒绝操作，均不选取其他租户或平台默认 Agent

#### Scenario: 平台管理员读取当前租户
- **WHEN** 平台管理员在业务控制台选中租户 A 并请求 Agent 列表或相关汇总
- **THEN** 结果只包含绑定 A 的 Agent，绑定其他租户的 Agent 不出现，也不被计入汇总

#### Scenario: 平台管理员未选租户
- **WHEN** 平台管理员在业务控制台没有有效租户选择
- **THEN** 可见 Agent 为空，系统不返回实例全量 Agent，也不借用全局默认 Agent

#### Scenario: 跨租户管理经平台入口
- **WHEN** 平台管理员管理未加入租户的 Agent
- **THEN** 该操作经专用平台入口以明确目标租户执行并记录审计，不改变其业务租户，不建立虚假成员关系
