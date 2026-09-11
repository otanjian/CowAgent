# agent-capability-bindings Specification

## Purpose
定义按智能体绑定知识条目、技能、SOP 与工具白/黑名单的持久化与管理界面，使数字员工能力装配可配置、可审计且与全局技能库分离。
## Requirements
### Requirement: 四类能力绑定持久化
系统 SHALL 为每个智能体持久化以下绑定（均可为空列表表示明确不选；技能字段继续兼容既有语义：`null`/缺省表示使用全部已安装技能）：

- 知识条目标识列表（在 shared/own 模式之上的可见文档/条目集合）
- 技能名称列表（沿用既有 allow-list）
- SOP 标识列表（Phase 1 允许自由文本 ID；不要求完整 SOP 状态机运行时）
- 工具白名单与工具黑名单（工具名为平台已注册工具标识）

系统 SHALL 在智能体管理 API 的更新操作中接受上述字段，并纳入 `revision` 乐观并发。

#### Scenario: 更新能力绑定
- **WHEN** 管理用户为智能体设置知识条目、技能子集、SOP ID 与工具白/黑名单并保存
- **THEN** 后续读取返回相同绑定，且不影响其他智能体的绑定

#### Scenario: 技能全部语义保持
- **WHEN** 智能体技能字段为缺省或显式表示「全部」
- **THEN** 行为与改造前「使用全部已安装技能」一致

#### Scenario: 并发写冲突
- **WHEN** 两个客户端基于同一 `revision` 先后提交不同绑定
- **THEN** 后写按既有冲突规则失败或要求刷新，不静默覆盖

### Requirement: 能力页签管理 UI
智能体管理 SHALL 提供「能力」页签（可与既有「技能」页签合并或升级），分别管理知识、技能、SOP、工具四类绑定。工具管理 SHALL 支持从已注册工具目录勾选白名单，并支持配置黑名单。知识绑定 SHALL 从当前身份可见的知识条目中选择。SOP Phase 1 SHALL 至少支持按 ID 添加/删除。

#### Scenario: 从目录勾选工具
- **WHEN** 用户在能力页打开工具选择器
- **THEN** 展示当前平台已注册工具列表，勾选结果写入该智能体工具白名单

#### Scenario: 添加 SOP 标识
- **WHEN** 用户输入 SOP ID 并确认添加
- **THEN** 该 ID 出现在智能体 SOP 列表中并随配置保存

#### Scenario: 无权知识不可绑定
- **WHEN** 当前主体无权读取某知识条目
- **THEN** 选择器不提供该项；即使客户端伪造 ID，保存或运行时也不得扩大可读范围

### Requirement: 与知识 shared/own 共存
系统 SHALL 保留既有知识库 shared/own 模式。`knowledge_ids` SHALL 表示在该模式下进一步限制或指定可见条目；当 `knowledge_ids` 为空且未显式配置时，系统 MUST 不额外收窄改造前的知识可见行为。

#### Scenario: 未配置知识条目列表
- **WHEN** 智能体未设置 `knowledge_ids`（缺省）
- **THEN** 知识可见性仅由既有 shared/own 与权限规则决定

#### Scenario: 配置了知识条目列表
- **WHEN** 智能体设置了非空 `knowledge_ids`
- **THEN** 运行时知识检索/列举不得返回列表外且越权的条目

