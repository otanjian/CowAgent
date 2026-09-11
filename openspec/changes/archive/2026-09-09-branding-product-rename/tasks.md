## 1. 品牌字符串替换（已实现）

- [x] 1.1 将 CLI 输出、运行状态与版本提示中的 `CowAgent` 替换为「容大AI」/ `RongAI`（`cli/cli.py`、`cli/commands/*`、`cli/utils.py`）。
- [x] 1.2 将网络请求 User-Agent 替换为 `RongAI` 前缀（`agent/tools/web_search/web_search.py`、`channel/feishu/feishu_static_card.py`、`lark_install.py` 等）。
- [x] 1.3 将 MCP `/OAuth` 客户端标识替换为 `RongAI`/`容大AI`（`agent/tools/mcp/mcp_client.py`、`mcp_oauth.py`）。
- [x] 1.4 将模型请求 `X-Title` 与 `_APP_TITLE` 替换为 `Rongda AI`（`models/linkai/link_ai_bot.py`、`models/openai/openai_http_client.py`、`models/custom_provider.py`）。
- [x] 1.5 将飞书渠道卡片、安装说明中的 `CowAgent` 替换为 `RongAI`（`channel/feishu/*`、`channel/chat_channel.py`）。

## 2. 品牌资产替换（已实现）

- [x] 2.1 新增 `rongda-ai-mark.svg`，替换桌面端 `BrandMark.tsx` 与 `logo.png`。
- [x] 2.2 替换 Web 端 `favicon.ico`、`logo.jpg` 与相关品牌资源。
- [x] 2.3 更新 `channel/web/branding.py` 与品牌组件引用，使用新品牌资产。

## 3. 已验证的重命名行为

- [x] 3.1 重命名不改变配置键、数据库字段、路由、会话/身份模型或数据归属。
- [x] 3.2 重命名不引入功能行为差异，仅改变品牌标识字符串与资源。
- [ ] 3.3 在真实 CLI 运行状态验证显示「容大AI」，不再出现 `CowAgent` 主品牌。
- [ ] 3.4 验证对外网络请求 User-Agent 为 `RongAI` 前缀，模型请求 `X-Title` 为 `Rongda AI`。
- [ ] 3.5 桌面端 `BrandMark` 组件与 Logo 在真实桌面端渲染使用新品牌资产。
- [ ] 3.6 飞书渠道进度/静态卡片与安装说明在真实渠道展示新品牌。

## 4. 文档与验收

- [x] 4.1 运行 OpenSpec 严格校验，确认 `branding-product-rename` 的 spec 各 requirement 均含 SHALL/MUST 与 WHEN/THEN 场景。
- [ ] 4.2 补充品牌替换的自动化测试，断言 CLI/User-Agent/MCP 客户端标识使用新品牌且不出现 `CowAgent` 主品牌。
