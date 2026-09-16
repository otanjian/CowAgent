## Why

账号菜单中的“我的资源”分组及五个个人资源入口需要按用户要求移除，使账号面板集中呈现身份、账号设置、帮助、退出和版本信息。该菜单由聊天工作台与管理控制台共用，本 change 按所有页面的账号菜单统一移除编写。

## What Changes

- 从 `/chat`、`/admin` 及其桌面、移动端共享账号菜单中删除“我的资源”标题、“我的智能体、我的渠道、我的记忆、我的工具、我的技能”五项，以及该分组专属的检查、失败重试与空白占位。
- 保留头像与账号身份、“账号设置”及个人资料/账号安全/个人偏好、帮助与关于、退出登录、品牌版本链接；整理相邻分隔线和间距。
- 将账号触发器中“个人资源与设置”的用途提示改为“账号设置”，清理只服务于已删除菜单的个人区域状态、焦点目标和事件绑定，三语同步。
- 菜单移除是呈现变更，不撤销 `personal.*` / `nav:personal.*` 授权，不删除个人页面、加载器、后端接口、已有资源或凭据。已有合法深链接和业务内入口继续按原授权访问。
- 进入仍可直接访问的个人页面时，以页面标题和面包屑说明位置，不虚构账号菜单当前项，不选中其他主导航项；不新增替代个人中心、顶部页签或侧栏个人资源分组。
- 更新账号菜单及个人页面相关前端/浏览器测试，覆盖重绘不恢复菜单、无多余请求、键盘/移动端交互和直接地址兼容。

## Capabilities

### New Capabilities

无。本次只调整现有账号菜单和导航呈现。

### Modified Capabilities

- `sidebar-account-menu`：移除个人资源菜单分组并保持账号操作、身份恢复、用途提示、焦点与响应式布局完整。
- `console-information-architecture`：规定五个个人页面撤下菜单入口后的定位与直接地址兼容，不将无菜单项误判为无权限或强制增加替代入口。

## Impact

- 实现接入点：`channel/web/chat.html`、`channel/web/static/js/console.js`、`channel/web/static/css/console.css`、必要的 `appearance.css`、`channel/web/static/js/i18n/account.js`；仅清理无其他引用的菜单专用代码和翻译键。
- 回归重点：`tests/test_sidebar_account_frontend.cjs`、`tests/test_personal_console_frontend.cjs`、`tests/test_personal_console_browser.cjs`、账号操作/路由测试及 `tests/fixtures/console_i18n_snapshot.json`。
- 数据唯一归属不变：全局账号、当前租户、页面权限及个人资源仍分别来自既有服务；不新增存储、数据库迁移、API、权限或功能开关。
- `move-personal-menu-to-account` 已实现但未归档，本 change 接替其中“资源入口放在账号菜单”的呈现决策，保留其账号面板与导航保护成果。由于主规范尚未合入前序个人入口条款，本次增量以当前 `openspec/specs/` 为基线；实施/归档前必须按设计中的协调表处理前序冲突条款，不能把相反要求同时合入主规范。
- `upgrade-personal-channel-workbench` 正在实现，本 change 不取消渠道工作台及其后端能力；两者重叠的菜单保留要求改按本 change 的最终入口策略协调。当前工作区已有相关未提交改动，生成本 change 仅新增本目录文档，不改写或回退这些实现。
- 本轮产物仅为提案、设计、规格和待执行任务，不执行产品代码删除、提交或部署。
