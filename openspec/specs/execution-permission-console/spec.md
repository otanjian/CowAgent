# execution-permission-console Specification

## Purpose
约束 Web 控制台与 Desktop 不再提供会话内自行调整执行权限的入口，并让工具被拒反馈与全局默认权限展示如实反映"执行由角色授权控制"，避免暗示用户可以绕过角色授权自行提权。
## Requirements
### Requirement: 会话界面不提供自行调整执行权限
Web 控制台与 Desktop 的对话界面 SHALL NOT 提供会话级权限模式选择器或「调整权限」操作按钮；输入框区域与工具被拒提示都 MUST NOT 暴露可提升执行权限的自助入口。

#### Scenario: 对话界面不存在权限选择器
- **WHEN** 用户打开任意会话的 Web 控制台或 Desktop 对话界面
- **THEN** 输入框区域不显示 permission 选择 chip，页面上不存在「调整权限」按钮

#### Scenario: 工具被拒提示无操作按钮
- **WHEN** 一次工具调用被拒绝并在对话中显示提示
- **THEN** 提示只说明原因，不包含可点击的调整权限入口

### Requirement: 被拒提示说明真实原因
工具被拒提示 SHALL 说明拒绝来源。legacy 模式下由权限模式拒绝 SHALL 显示当前模式；database 模式下由角色授权或执行隔离拒绝 SHALL 说明当前角色未获授权，MUST NOT 显示会话权限模式或暗示可通过聊天调整。

#### Scenario: database 模式角色拒绝
- **WHEN** database 模式下工具因缺少 `tool.execute` 或对应资源 grant 被拒绝
- **THEN** 提示说明当前角色未获授权并可联系管理员，不显示"当前权限为…"的模式文案

#### Scenario: legacy 模式模式拒绝
- **WHEN** legacy 模式下工具因当前 read-only/workspace-write 模式被拒绝
- **THEN** 提示显示当前权限模式，但不提供调整入口

### Requirement: 全局默认权限在数据库模式下只读
平台设置页的全局「默认权限」配置 SHALL 在 database 身份模式下只读展示并说明执行权限由角色资源授权控制，MUST NOT 允许通过该设置改变 database 模式的实际执行授权；legacy 模式 SHALL 保持可编辑。

#### Scenario: 数据库模式查看默认权限
- **WHEN** 平台管理员在 database 模式下打开设置页
- **THEN** 「默认权限」不可编辑并显示由角色授权控制的说明

#### Scenario: legacy 模式保持可编辑
- **WHEN** 平台管理员在 legacy 模式下打开设置页
- **THEN** 「默认权限」仍可编辑并保存

### Requirement: 会话设置接口不接受数据库模式覆盖
会话设置接口 SHALL 在 database 身份模式下不返回可用于自行调整的权限模式集合，且 MUST NOT 接受或持久化会话级 permission 覆盖；legacy 模式 SHALL 保留现有行为。

#### Scenario: 数据库模式提交会话权限覆盖
- **WHEN** database 模式下客户端提交 `permission` 会话设置
- **THEN** 服务端不应用该覆盖，实际工具授权仍由角色决定

