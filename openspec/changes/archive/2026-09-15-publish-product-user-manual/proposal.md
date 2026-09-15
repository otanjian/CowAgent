## Why

产品介绍站点 `webhelp/` 目前只有安装命令页（`quickstart.php`）与能力原理文档（`docs/` 下 31 篇本地化归档），缺少一份**面向使用者的操作手册**：新用户不知道从哪里登录、控制台有哪些页面、日常怎么用智能体与记忆知识库、管理员怎么完成租户/成员/角色/审计等管控动作。首页 Hero 主按钮当前指向 `quickstart.php`（仅部署命令），无法承载这条使用路径。

同时，站点既有文案已与产品实际身份口径**不一致**：`quickstart.php` 的提示仍在讲「公网部署请务必设置访问密码」，而共享访问密码 `web_password` 已退役，控制台登录只认数据库账号（见 `openspec/specs/user-auth`、`openspec/specs/enterprise-access-enforcement`）。手册必须写对，且站点不能再自相矛盾。

## What Changes

- 文档站点新增「产品使用手册」页面 `manual.php`：章节化操作指引（准备工作 / 安装与启动 / 首次初始化与登录 / 控制台导览 / 日常使用 / 企业管控 / 命令速查 / 故障排查 / 深入阅读），原理细节深链既有能力文档，不复制既有正文。
- 首页 Hero 主按钮改为指向手册并显示手册文案；**新增**文案键承载，既有 `cta.primary` 保持不变，其余页面 CTA 目标不变。
- 手册与站点既有文案统一到产品实际身份口径：登录为数据库账号，身份模式仅支持 `database`；修正 `quickstart.php` 的「访问密码」提示。
- 语言包按站点约定补齐 `manual.*` 双语键（`zh` / `en` 结构一一对应）。
- 站点既有约束保持：零第三方依赖、完全离线自包含、无站外请求。

## Capabilities

### New Capabilities

- `product-manual-site`: 文档站点的产品使用手册页面、其首页入口、内容与产品实际行为的口径一致性，以及双语与离线自包含约束。

### Modified Capabilities

（无。本变更不触及运行时产品或既有 capability 的行为要求。）

## Impact

- 站点页面：新增 `webhelp/manual.php`；修改 `webhelp/index.php`（Hero 主按钮）。
- 站点内容与文案：`webhelp/includes/content.php`（手册章节结构）、`webhelp/lang/zh.php`、`webhelp/lang/en.php`（`manual.*`、`cta.manual`，并修正 `quickstart.port_note`）。
- 样式：仅复用既有组件；如需步骤块样式，在 `webhelp/assets/css/style.css` 末尾追加 `.manual-*` 一节。
- 文档正文与清单（`webhelp/docs/**`、`tools/build-docs.php`）不变：手册只链接既有文档，不新增文档正文。
- 运行时后端、Web 控制台、桌面端、渠道与 CLI **均不变**；本变更只影响介绍站点。
