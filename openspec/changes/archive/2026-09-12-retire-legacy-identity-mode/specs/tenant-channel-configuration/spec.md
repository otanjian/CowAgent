## ADDED Requirements

### Requirement: 渠道实例必须显式登记且禁止隐式合成

平台级与租户级渠道实例 SHALL 来自显式登记的实例记录，包含明确的渠道类型、显示名称、作用域与启用状态。系统 MUST NOT 由旧 `channel_type` 字段、单租户配置或历史 legacy 布局隐式合成渠道实例，MUST NOT 在未显式登记时启动对应通道或解析其入站消息。作用域 SHALL 明确区分平台级与租户级，且 MUST NOT 互相替代或越界。

#### Scenario: 显式登记实例启动
- **WHEN** 存在一条启用中的显式渠道实例记录
- **THEN** 系统按该实例的渠道类型与作用域启动通道并按归属解析入站消息

#### Scenario: 仅有旧 channel_type 配置
- **WHEN** 配置中只有旧 `channel_type` 字段而没有显式实例记录
- **THEN** 系统不合成实例、不启动该通道，并记录可诊断原因

#### Scenario: 作用域越界
- **WHEN** 租户域入口尝试改写平台级实例名册或获取其凭据
- **THEN** 请求被拒绝，平台级名册与凭据保持不变
