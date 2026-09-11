# branding-product-rename Specification

## Purpose
将产品品牌从 `CowAgent` 统一替换为「容大AI」(中文) / `RongAI`(拉丁) 的横切重命名，覆盖 CLI、网络 User-Agent、应用标题、桌面与渠道客户端标识及相关品牌资产，使品牌标识在各产品面一致且不改变既有功能行为、配置键、路由与数据归属。
## Requirements
### Requirement: 产品品牌统一标识

系统 SHALL 将产品对外品牌名统一为中文「容大AI」与拉丁 `RongAI`(或 `Rongda AI` 兼容写法)，MUST NOT 在面向用户的 CLI 提示、运行状态、应用标题与渠道客户端标识中继续使用 `CowAgent` 作为主品牌名。品牌名变更 MUST NOT 改变配置键、数据库字段、会话/身份模型、路由或数据归属，MUST NOT 引入功能行为差异。

#### Scenario: CLI 输出使用新品牌
- **WHEN** 用户运行 CLI 命令查看运行状态或命令列表
- **THEN** 界面输出显示「容大AI」品牌名与版本（如「容大AI v…」），不再显示 `CowAgent`

#### Scenario: 应用标题使用新品牌
- **WHEN** 客户端设置对外应用标题或 `_APP_TITLE`
- **THEN** 标题使用 `Rongda AI` / `容大AI`，不出现 `CowAgent` 主品牌名

### Requirement: 网络标识使用新品牌

发起对外网络请求的 User-Agent 与客户端标识 SHALL 使用新品牌（`RongAI` 相关前缀），MUST NOT 继续以 `CowAgent` 作为默认 User-Agent 或 MCP/OAuth 客户端名。标识变更 MUST NOT 影响请求语义、鉴权或与第三方服务的协议兼容性，仅改变标识字符串。

#### Scenario: Web 请求 User-Agent
- **WHEN** 工具或渠道发起对外网络请求
- **THEN** 使用的 User-Agent 为 `RongAI` 前缀（如 `RongAI/Feishu`），不出现 `CowAgent/…`

#### Scenario: MCP/OAuth 客户端标识
- **WHEN** MCP 客户端初始化或 OAuth 流程上报客户端信息
- **THEN** `clientInfo.name`、`client_name` 与 `_UA` 使用 `RongAI`/`容大AI`，不再使用 `CowAgent`

#### Scenario: 模型请求 X-Title
- **WHEN** 模型供应商请求设置 `X-Title` 头
- **THEN** 标题使用 `Rongda AI`，不出现 `CowAgent`

### Requirement: 品牌资产替换为品牌标识

桌面端与 Web 的品牌 Logo、图标与品牌组件 SHALL 引用新品牌资产（`rongda-ai-mark`、`BrandMark`、新 `logo` 等），MUST NOT 依赖旧 `CowAgent` 品牌资产作为默认展示。资产替换 MUST NOT 改变应用程序功能、主题或用户偏好系统。

#### Scenario: 桌面端品牌组件
- **WHEN** 桌面端渲染品牌标记与登录品牌区
- **THEN** 使用新品牌 Logo 与 `BrandMark` 组件，不显示旧 `CowAgent` Logo

#### Scenario: Web 品牌资产
- **WHEN** Web 端加载 favicon 或品牌 Logo
- **THEN** 引用新品牌资产，不依赖旧 `CowAgent` 资产作为默认

### Requirement: 渠道客户端标识使用新品牌

各 Channel（飞书等）的客户端标识、卡片元数据与安装注释 SHALL 使用新品牌，MUST NOT 继续以 `CowAgent` 作为标识。品牌替换 MUST NOT 改变渠道功能、卡片交互或消息协议。

#### Scenario: 渠道卡片标识
- **WHEN** 飞书渠道渲染进度卡片或静态卡片
- **THEN** 使用新品牌的注释与 User-Agent，不出现 `CowAgent` 主品牌名

#### Scenario: 渠道安装说明
- **WHEN** 渠道安装或布局说明引用产品归属
- **THEN** 使用新品牌（如 `RongAI`），不声明 `CowAgent` 归属

