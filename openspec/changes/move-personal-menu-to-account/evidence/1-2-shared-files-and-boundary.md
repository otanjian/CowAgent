# 任务 1.2 — 共享文件与最小修改边界

任务要求：记录当前工作区与 `complete-database-capability-parity` 的共享文件改动，确认本
change 的最小修改边界；登记账号「六项」规范与当前实际入口的既有差异，保留已有账号和租户
选择能力。

## 1. 工作区现状（实施前）

`git status` / `git diff --stat` 显示工作树中已有大量未提交改动（本 change 开始前即存在，
含 `enable-member-personal-console` 与 `complete-database-capability-parity` 的实施产物）。
与本 change 相关的共享文件：

| 文件 | 工作树改动 | 本 change 触达范围（最小边界） |
| --- | --- | --- |
| `channel/web/chat.html` | +124/-… | 仅 `#sidebar-account-menu` 内部结构与 `#sidebar-nav` 的「我的」组（删除）；不动其他菜单、脚本顺序、fragment 挂载点 |
| `channel/web/static/js/console.js` | +956/… | 仅账号菜单渲染/开关、`VIEW_META` 个人条目注释、`navigateTo` 离页检查顺序、`_applySidebarPermissions` 的个人入口分支、`showUnavailableView` 的当前项清理；不重写路由 |
| `channel/web/static/js/personal-console.js` | 未改（新增文件） | 不修改（只在实施阶段按需读取 `PERSONAL_VIEWS`） |
| `channel/web/static/css/console.css` | +335/… | 仅账号菜单分组标题、个人入口当前项、区域状态、移动底部面板与遮罩 |
| `channel/web/static/js/i18n/account.js` | +6/… | 仅新增本 change 的分组 / 提示 / 关闭 / 区域状态键 |
| `channel/web/static/js/i18n/personal-console.js` | 未改（新增文件） | 不修改（沿用既有「我的」名称键） |

`complete-database-capability-parity` 提案第 42 行登记的未来合并重点包含
`channel/web/chat.html`、`channel/web/static/js/console.js`，即与本 change 重叠。其未勾选任务
（7.8、7.10、8.7、8.8、11.3、12.3、12.4、R2）全部位于桌面协议、渠道类型验收与主规范合并，
未涉及账号菜单与工作台「我的」组结构，因此本 change 不等待其完成，也不覆盖其成果。

## 2. 与工作台 / 控制台菜单的关系

- `#sidebar-nav` 内有两个导航壳（`data-nav-shell="workbench"` / `"admin"`），账号卡片
  `#sidebar-account-footer` 位于两个壳之外，因此迁入账号面板的个人入口天然被两个区域共用，
  无需复制两份 DOM（本 change 保证只有一份）。
- 「控制台」入口（`#nav-open-admin`）与「工作台」返回入口（`#nav-return-workbench`）不动，
  仍在导航滚动区上方的固定条内。

## 3. 账号「六项」规范与当前实际入口的既有差异

`openspec/specs/sidebar-account-menu/spec.md` 的既有能力描述提到「六项菜单」及账号/租户入口；
当前 `chat.html` 实际呈现的常规账号操作为：`account-menu-profile`（个人资料）、
`account-menu-password`（账号安全）、`account-menu-prefs`（个人偏好）、`account-menu-about`
（帮助与关于）、`account-menu-logout`（退出登录）。差异登记如下：

| 项 | 现状 | 本 change 处理 |
| --- | --- | --- |
| 租户选择 | 不在账号菜单内，而是顶部 `#tenant-selector` + `openAccountTenant()` 的既有切换流程 | **保留**，不移入账号菜单、不删除 |
| 菜单条目数量 | 实际五个操作 + 品牌版本链接 | 不重写条目数量；只按 spec 的「身份区 / 我的资源 / 账号设置 / 帮助与退出 / 品牌版本」组织现有条目 |
| 账号操作语义 | profile/password/prefs/about/logout 各自独立可执行控件 | 保留原控件、原 id、原 onclick |

因此本 change 不扩展为「账号功能补建」，既不新增账号操作，也不移除既有租户选择路径与能力。
