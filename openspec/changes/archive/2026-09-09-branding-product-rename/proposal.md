## Why

既有 `branding-settings` change（已归档）只覆盖 Web 运行时品牌替换，其 spec 明确声明「本期 SHALL 保留桌面 React 品牌位、桌面安装图标、托盘图标、文档站与各 Channel 的既有品牌行为，不将这些位置纳入运行时品牌替换」。但 `rdai` 分支已经将产品名从 `CowAgent` 横切改为 `容大AI` / `RongAI` / `Rongda AI`，涉及 CLI 输出、网络 User-Agent、应用标题、桌面端品牌资产与各 Channel 的客户端标识。这些品牌标识改动目前没有被任何已归档 change 覆盖，需要单独建模为一次产品品牌重命名，以保证品牌在运行时、桌面、文档与各渠道保持一致。

## What Changes

- 将产品品牌名从 `CowAgent` 统一为 `容大AI`（中文）与 `RongAI`（英文/拉丁），涉及 CLI 提示与运行状态、web/desktop/feishu 等渠道的客户端标识、网络 User-Agent、应用标题与 `client_name` 等字符串。
- 新增/替换桌面端与 Web 的品牌 Logo 资产（`rongda-ai-mark.svg`、`logo.png`、`BrandMark.tsx`、`favicon.ico` 等）。
- 统一 MCP/OAuth 客户端标识（`RongAI-MCP-OAuth/1.0`、`client_name: "容大AI"`、`clientInfo.name` 等）。
- 保持既有配置项、数据库字段、路由、接口协议不变；重命名不改变任何功能行为或数据归属。
- README 等文档品牌文本随动，但具体 README 的同步策略不在本 change 强制范围（部分 README 已被策略性停止跟踪）。

## Capabilities

### New Capabilities

- `branding-product-rename`: 将产品品牌从 `CowAgent` 统一替换为 `容大AI` / `RongAI`，覆盖 CLI、网络 User-Agent、应用标题、桌面与渠道客户端标识及相关品牌资产，保证品牌标识在各产品面一致且不改变功能行为。

### Modified Capabilities

无。`openspec/specs/` 中既有 `branding-settings` 仅描述 Web 运行时品牌设置，其明确排除的「桌面/Channel/文档站」位置正是本 change 补全的部分；本 change 作为独立 capability 建模，不修改 `branding-settings` 既有 requirement。

## Impact

- 代码范围：`cli/`（`cli.py`、`commands/`、`utils.py`）、`agent/tools/mcp/`（`mcp_client.py`、`mcp_oauth.py`）、`agent/tools/web_search/web_search.py`、`models/`（`custom_provider.py`、`linkai/link_ai_bot.py`、`openai/openai_http_client.py`）、`channel/`（`chat_channel.py`、`feishu/*`、`web/*`）、`plugins/cow_cli/cow_cli.py`、`desktop/src/renderer/src/*`（品牌资产与 TSX 组件）、`bridge/` 等。
- 数据唯一归属：无数据模型变更；品牌名为展现与网络标识字符串，不写入业务数据或影响唯一约束。
- 兼容与恢复：重命名不改变配置键、路由、协议或数据；回滚即还原品牌字符串与资产。无数据迁移。
- 跨 change 依赖：补充 `branding-settings`（已归档）明确排除的桌面/Channel 品牌位；不改变身份、租户、todo 或平台配置能力。
- 验收：验证 CLI 运行状态与命令提示显示 `容大AI`、网络请求 User-Agent 为 `RongAI`、MCP/OAuth 客户端标识为 `RongAI`/`容大AI`、桌面与渠道品牌资产正确引用新 Logo，且不引入功能行为变化。
