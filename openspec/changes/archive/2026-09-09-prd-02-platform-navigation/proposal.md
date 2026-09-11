## Why

平台已完成「容大AI」品牌与企业化改造的主体路由，但 Web 控制台侧边栏仍是基于 RongAI 的旧一级菜单分组（工作台/管理/监控），且缺少企业化用户体系需要的系统设置入口与登录账号展示。本次以 PRD-02 平台导航为基线，统一品牌标识、重构一级菜单分组，并引入登录账号体系，作为后续租户/用户/权限等功能的前置导航骨架。

## What Changes

- 品牌标识替换：将全站 logo 由 RongAI 图标替换为「容大AI」深色版字标（`rongda-ai-logo-dark.svg`），覆盖 Web 控制台、桌面端品牌标、文档。
- 一级菜单重命名：将原「对话」分组改名「工作台」。
- 一级菜单重构（Web 控制台侧边栏）：
  - 工作台分组下新增/移入：对话、智能体、待办、定时、场景应用；
  - 定时从「管理」移入「工作台」，并与待办同级；
  - 管理分组仅保留：配置、技能、记忆、知识、通道；
  - 监控分组保留：日志。
- 新增一级菜单「系统设置」分组（置于侧边栏最底部）：平台设置、租户、用户、角色权限、组织架构、品牌设置、审计、备份升级、开放 API。
- 菜单占位：待办、场景应用以及系统设置下各子项仅作为菜单项展示，点击不跳转，功能不实现（后续切片接入）。
- 登录账号体系（**BREAKING**）：登录由「纯密码」升级为「账号 + 密码」，新增 `web_username` 配置；侧边栏底部红框处原本显示版本号的位置改为展示当前登录账号。

## Capabilities

### New Capabilities

- `platform-navigation`: Web 控制台侧边栏一级菜单分组结构（工作台/管理/监控/系统设置）、菜单项归属、占位项行为与面包屑导航。
- `user-auth`: 控制台登录账号体系，覆盖登录方式（账号+密码）、`web_username` 配置、登录态会话与侧边栏登录账号展示。

### Modified Capabilities

- 无（当前 `openspec/specs/` 尚无既有规范，本次为全部新建）。

## Impact

- 前端：`channel/web/chat.html`、`channel/web/static/js/console.js`（侧边栏 HTML、`VIEW_META` 分组映射、`navigateTo` 占位处理、三语 i18n）。
- 桌面端：`desktop/src/renderer/src/i18n.ts`（菜单文案）、`desktop/src/renderer/src/assets/logo.png`。
- 品牌资源：`channel/web/static/logo.jpg`、`docs/images/logo.jpg`。
- 后端：`config.py`（新增 `web_username` 配置项）、`channel/web/web_channel.py`（`AuthLoginHandler`/`AuthCheckHandler` 账号校验与登录态）。
- 依赖：无新增运行时依赖。
