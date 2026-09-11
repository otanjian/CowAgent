## 1. 品牌标识替换

- [x] 1.1 将「容大AI」字标光栅化为透明 PNG，替换 `channel/web/static/logo.jpg`
- [x] 1.2 替换桌面端品牌标 `desktop/src/renderer/src/assets/logo.png`
- [x] 1.3 替换文档标识 `docs/images/logo.jpg`

## 2. 一级菜单重构（Web 控制台）

- [x] 2.1 将「对话」分组改名为「工作台」（`nav_chat` 三语）
- [x] 2.2 工作台分组下加入：智能体、待办、场景应用
- [x] 2.3 将「定时」从「管理」移入「工作台」，置于「待办」之后（与待办同级）
- [x] 2.4 管理分组移除智能体、定时，保留配置/技能/记忆/知识/通道
- [x] 2.5 更新 `VIEW_META` 分组映射（agents/tasks 归入工作台，新增 todo/scenarios）

## 3. 占位菜单项（功能不实现）

- [x] 3.1 待办、场景应用菜单项仅展示，点击不跳转
- [x] 3.2 在 `navigateTo` 增加占位守卫，避免无容器视图报错
- [x] 3.3 新增 `menu_todo`、`menu_scenarios` 三语翻译键

## 4. 系统设置一级分组

- [x] 4.1 侧边栏最底部新增「系统设置」分组（`nav_system`）
- [x] 4.2 系统设置下加入九个子项：平台设置、租户、用户、角色权限、组织架构、品牌设置、审计、备份升级、开放 API
- [x] 4.3 系统设置各子项作为占位项，点击不跳转
- [x] 4.4 新增系统设置相关翻译键（`nav_system` 及九个子项 `menu_*` 三语）

## 5. 登录账号体系（账号 + 密码）

- [ ] 5.1 `config.py` 新增 `web_username` 默认配置项（默认空）
- [ ] 5.2 `web_channel.py` `AuthLoginHandler` 同时校验 `web_username` 与 `web_password`
- [ ] 5.3 `AuthCheckHandler` 返回登录账号信息供前端展示
- [ ] 5.4 Web 登录表单增加账号输入框（`login-form`）
- [ ] 5.5 侧边栏底部由版本号改为展示当前登录账号（未启用登录时回退版本号）
- [ ] 5.6 桌面端登录（`LoginGate`）同步支持账号输入

## 6. 验证与回归

- [ ] 6.1 校验 `console.js` 语法（`node --check`）
- [ ] 6.2 运行 `tests/test_agent_web_management.py` 相关断言确认未破坏
- [ ] 6.3 手动验证四分组菜单展示、占位项点击不跳转、面包屑联动
- [ ] 6.4 手动验证账号密码登录、侧边栏登录账号展示、未启用登录回退
- [ ] 6.5 `openspec validate prd-02-platform-navigation --type change` 通过
