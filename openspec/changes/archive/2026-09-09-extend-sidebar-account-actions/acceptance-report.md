# 侧栏账号菜单扩展验收报告

验收日期：2026-09-08。范围：`extend-sidebar-account-actions` 六项菜单（个人资料、修改密码、界面偏好、切换租户、关于、退出登录）及本人改密/强制改密最小完整流程。

## 已执行的验证

- 后端：`.venv/bin/python -m pytest` 运行身份/账号/品牌/历史相关回归及针对性新增测试，全部通过。
- 前端：`.cjs` 交互测试与 `node --check channel/web/static/js/console.js` 语法检查通过。
- 浏览器：在隔离实例（独立临时数据目录、`identity_mode=database`、端口 9898）上使用真实 `build_web_app()`、真实登录与 Cookie 会话，逐项验证六项功能、普通/强制改密、多租户切换。
- `openspec validate extend-sidebar-account-actions --type change --strict`：通过（见文末校验命令）。

## 浏览器验证记录

使用两个测试账号、两个租户：

- `admin / <改后密码>`：平台管理员，`acme`、`beta` 两租户成员。
- `badmin`：`beta` 租户管理员，初始 `must_change_password=true`。

| 功能 | 结果 |
| --- | --- |
| 个人资料 | 只读抽屉展示全局账号（姓名、登录账号、平台身份）与当前租户成员（当前租户、成员姓名、实际角色、部门、岗位），空值显示「未设置」 |
| 修改密码 | 弹窗含原/新/确认密码；错误原密码弹出「原密码不正确」；正确修改后撤销会话并回到登录页 |
| 界面偏好 | 浅色/深色、简体/繁体/英文可切换并同步顶部；本地生效，不写实例默认 |
| 切换租户 | 列出 acme/beta 与当前项标记，选中后通过重载页面进入目标租户 |
| 关于当前品牌 | 展示品牌「容大AI」与版本「v2.1.7」，提供「查看更新日志」入口 |
| 退出登录 | 结束当前登录并回到登录页（保留失败重试与单次提交） |

强制改密门禁：`badmin` 登录后以「必须修改密码」门禁展示（隐藏右上角关闭、取消改为「退出登录」、z-index 提升到登录层之上），非可关闭；完成改密后门禁解除可正常进入。Storage 分区验证：`cow_active_agent`、`cow_default_agent` 等选择键以 `::u=<user>::t=<tenant>` 分区存储，`cow_theme` 保持浏览器内共享。

## 结论

P0～P3 的全部任务（1.1–4.4）已勾选。功能满足方案：本人改密/强制改密最小完整流程、六项菜单、刷新式租户切换、仅对现有 Agent/会话选择键做 user/tenant 分区。未新增数据库字段、未做结构迁移、未开放新的功能开关，沿用 `identity_mode`；legacy 模式按「本地访问」显示适用菜单（密码保护：偏好/关于/退出；免登录：偏好/关于），无个人改密入口、不改系统共享访问密码。

正式 spec 为空，本 change 沿用三份 ADDED（`account-menu-actions`、`self-account-context`、`self-password-flow`），本次在实现上扩展了上期「仅展示」的范围，并保留授权与会话真值归属；不提前修改或归档上期账号菜单与身份 change。

校验命令：

```sh
.venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_identity_service.py tests/test_identity_frontend.py -q --tb=short
node --test tests/test_sidebar_account_frontend.cjs
node --check channel/web/static/js/console.js
openspec validate extend-sidebar-account-actions --type change --strict
```
