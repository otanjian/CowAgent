# 侧栏账号菜单实施与验收

2026-09-08 完成 `add-sidebar-account-menu`。本 change 已实施，未归档。

## 实施结果

- `channel/web/chat.html`、`channel/web/static/css/console.css`：底部账号卡片、姓名首字/人物图标、两行身份、向上菜单、版本入口迁移、启动检查遮罩；支持导航独立滚动、长名、手机和短视口。
- `channel/web/static/js/console.js`：消费既有认证响应，分开认证结论与资料完整性；补齐启动重试、多租户选择回调清理、账号检查重试、身份代次与请求序号、旧 401 隔离、退出失败恢复及三语文案。普通检查不重新初始化业务数据，品牌/版本独立更新。
- 菜单关闭态不进入 Tab 顺序，Escape 返回卡片，外部点击/焦点离开/窗口缩放收起；双向兼容顶部菜单。真实 Chromium 验证后补齐禁用重试按钮前保存焦点，避免焦点掉回页面。
- 新增 `tests/test_sidebar_account_frontend.cjs`，为既有品牌隔离测试补充新增渲染依赖；`docs/channels/web.mdx` 增加 Account Menu 使用说明。

所有修改均叠加在当前工作区已有改动之上，未重置其他 change。没有新增后端接口、数据库字段、身份持久化、运行时依赖或 feature flag；既有顶部租户初始化等独立问题未纳入改造。

## 验证结果

| 层次 | 结果 | 证据 |
| --- | --- | --- |
| JavaScript 语法与补丁空白检查 | 通过 | `node --check channel/web/static/js/console.js`；相关文件 `git diff --check` |
| 前端实际处理器回归 | 69/69 通过 | 账号 18 项，加既有品牌、历史会话、智能体工作台回归；使用现有 `node:test` + VM |
| 后端既有回归 | 58 项及 2 个 subtests 通过 | `.venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_branding.py -q` |
| 隔离真实 HTTP | 47 项契约检查通过，另确认共享密码模式真实 401 路径 | [认证切片](evidence/auth-contract.md)、[HTTP 结果](evidence/auth-contract.json) |
| 实际 Chrome 页面 | 37 项检查通过，0 个 JavaScript 页面异常 | [浏览器结果](evidence/browser-acceptance.json) |
| 当前实例静态文件 | 匹配 | `http://localhost:9899/assets/js/console.js` 与工作区文件 SHA-256 一致 |

浏览器使用独立上下文和真实生产路由，数据库为临时 SQLite，覆盖零/单/多租户普通账号、Cookie 刷新、真实会话撤销后重新登录、两种退出入口、共享密码与免登录及开关切换。浏览器中的初次检查失败和离线重试为明确的网络故障注入；旧响应乱序、退出错误 JSON 等难以稳定复现的路径由前端受控传输回归验证，不将其当作真实服务器故障验收。

交互覆盖桌面、手机、手机横屏、短桌面视口、文字缩放、长姓名中的 `<script>` 纯文本、Escape/Tab/触摸、菜单双向互斥、三语和深浅主题。重试保留历史视图和可用焦点，业务初始化不重复。

## 页面证据

- [桌面账号菜单](evidence/database-desktop.png)
- [长姓名与深色主题](evidence/database-long-name-dark.png)
- [手机](evidence/database-mobile.png)
- [手机横屏](evidence/database-mobile-landscape.png)
- [共享密码模式](evidence/legacy-password-desktop.png)、[免登录模式](evidence/legacy-open-desktop.png)

## 已知的既有后端限制

数据库租户管理路由在失效会话下使用不完整的 HTTP 状态字符串，严格 WSGI 环境会返回 500。此行为发生在现有认证处理器，本次未更改后端。数据库过期恢复使用真实撤销后 `/auth/check` 返回 `authenticated:false` 验收；全局 401 使用共享密码模式真实 `/auth/logout` 清 Cookie 后访问 `/api/agents` 验收。前端不会把 500 或业务 403 擅自当作退出，也不据此宣称数据库租户接口已修复。

## 交付与回退

17 项任务全部完成；按现有方式交付 HTML/CSS/JavaScript，当前 9899 实例已能读取新版脚本，刷新页面即可加载。无需迁移用户、AuthSession 或业务会话。回退时仅撤回本 change 的静态资源与测试/说明增量，保留其他在途改动和业务数据。

临时验收服务 19891–19894 已全部停止；没有停止或修改 9899，也没有使用或修改用户的真实账号、配置或身份数据库。测试凭据仅存于权限为 0600 的临时 fixture 文件，未纳入本 change。
