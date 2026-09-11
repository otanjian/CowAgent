## Context

当前 Web 控制台侧边栏为静态 HTML（`channel/web/chat.html`）配合 `console.js` 中的 `VIEW_META` 与 `navigateTo()` 驱动；分组通过 `menu-group` DOM 节点与 `data-group` 标识，菜单项通过 `sidebar-item` + `data-view` 绑定。桌面端 `NavRail.tsx` 为平铺菜单（无分组），因此本 change 的导航重构仅涉及 Web 控制台。认证当前为纯密码（`web_password` 单值），无账号体系；登录态由 `cow_auth_token` 会话承载。品牌标识为位图 `logo.jpg`/`logo.png`，已替换为「容大AI」字标。

## Goals / Non-Goals

**Goals:**

- 重构 Web 控制台侧边栏为四个一级分组（工作台/管理/监控/系统设置），调整菜单归属与顺序。
- 新增「系统设置」分组及九个占位子项；为待办/场景应用/系统设置子项建立占位（点击不跳转）行为。
- 将登录升级为「账号 + 密码」，新增 `web_username`，侧边栏底部展示当前登录账号。
- 统一品牌标识为「容大AI」字标。

**Non-Goals:**

- 不实现待办、场景应用及系统设置各子项的后端功能（本 change 仅菜单骨架）。
- 不引入多租户/成员资格/角色权限数据库模型（归属后续 PRD，与 `AuthSession` 分离，机器主体不伪造 Membership）。
- 不改变桌面端 NavRail 的分组结构（其平铺布局不在本 change 范围）。
- 不实现真正的用户数据库账号体系，仅基于配置（`web_username`）支持单一账号登录。

## Decisions

- **菜单结构用静态 HTML + `VIEW_META` 映射**：沿用现有模式，不引入动态渲染，改动最小、语言切换与面包屑联动路径既有。备选：JS 动态生成侧边栏（复杂度高、需重构 `applyI18n` 与 `navigateTo`，否决）。
- **占位项采用 `navigateTo` 早退守卫**：在包裹后的 `navigateTo` 顶部对 `todo`/`scenarios`（及其余系统设置子项）直接 `return`，实现「点击不跳转、不报错」。备选：为每个占位项创建空 `view-*` 容器（污染 DOM、产生空页面，否决）。
- **登录账号推进方式**：新增 `web_username` 配置项，`AuthLoginHandler` 同时校验账号与密码；登录表单增加账号输入框；侧边栏底部由「版本号」改为「登录账号」，并在未启用登录时回退展示版本号。备选：仅展示系统运行用户（`getpass.getuser()`，与「登录账号」语义不符，否决）；仅配置字段不校验（无法满足账号密码登录规格，否决）。
- **品牌标识**：将字标 SVG 光栅化为透明 PNG 置于既有 `logo.jpg`/`logo.png` 路径，避免改动引用与构建；桌面端经 Vite `publicDir` 复用同一资源。

## Risks / Trade-offs

- **占位项与真实导航的区分依赖硬编码视图名** → 后续新增真实视图时若复用 `todo`/`scenarios` 名需同步移除守卫，否则功能被吞；以 tasks 明确标注待填充视图名。
- **单一 `web_username` 仅支持单账号** → 多账号/用户管理不在本 change 范围，后续接入数据库用户体系时视为演进而非缺陷。
- **桌面端与 Web 端菜单结构不一致** → 已明确桌面端平铺结构不在范围；若后续需对齐，需在 tasks 中单列。
- **未配置账号时回退逻辑** → 需保证不影响既有纯密码/免登录部署；迁移时以 `web_username` 是否为空作为开关。

## Migration Plan

1. 更新 `config.py` 默认字典新增 `web_username` 为空串（默认不启用账号）。
2. 后端 `AuthLoginHandler`/`AuthCheckHandler` 增加账号校验分支，`*_is_password_enabled` 兼容空账号（仅密码时放行账号字段）。
3. 前端登录表单与侧边栏底部展示逻辑按配置开关切换。
4. 回滚：恢复 `web_username` 为空并还原登录/侧边栏逻辑，登录态令牌机制兼容。

## Open Questions

- 系统设置各子项的屏幕路由 `view-*` 命名是否需预先约定（平台/租户/用户等）以向后兼容？——当前以占位守卫承载，命名留待功能切片实现时确定。
