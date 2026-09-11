## Why

侧栏账户菜单原有条目间距大、缺少图标和分组，底部两行账户按钮仍占据较高空间。用户连续两次要求优化截图中的菜单与账户按钮；现有 `sidebar-account-menu` 仍规定约 72px、圆形头像和两行身份，需要补充 change 记录已完成的视觉与展示行为调整。

本变更为已实施工作的补记。承接项目 PRD-02 v1.2 平台导航方向，以及已归档的 `add-sidebar-account-menu`、`extend-sidebar-account-actions`、`add-workbench-appearance-preferences`；具体验收依据是本次用户截图、当前代码及已有规范。仓库未找到配置中引用的 PRD v1.2 原文，不宣称已逐条核验原文。

## What Changes

- 将账户弹层改为紧凑身份区、带线性图标的设置分组、帮助和退出操作、低强调版本链接，统一圆角、间距与主题颜色。
- 将底部按钮改为单行「头像＋姓名＋更多」：普通指针下最小高度 40px、头像 28px，容器上下各 4px 留白；粗指针设备保留至少 44px 点击高度。
- 用户名及状态副标题保留为辅助技术可读内容和悬停提示，数据库完整身份仍在展开菜单中显示；长姓名保留省略及完整提示。
- 保留现有适用入口、认证状态、焦点与关闭行为、折叠侧栏右侧弹层、三语和主题兼容；图标不因翻译或退出状态重绘丢失。
- 补记实际验证结果及既有视觉测试的首页卡片数量断言过时问题，不将隔离前端验证表述为生产认证验收。

## Capabilities

### New Capabilities

无新增业务能力。

### Modified Capabilities

- `sidebar-account-menu`：更新固定底部入口尺寸、头像和单行身份呈现，明确副标题的读取方式、折叠态弹层位置、菜单分组、图标及减少动态效果的行为。

## Impact

- 接入文件：`channel/web/chat.html`、`channel/web/static/css/console.css`、`channel/web/static/css/appearance.css`、`channel/web/static/js/console.js`。
- 测试兼容：`tests/test_sidebar_account_frontend.cjs` 增加退出文字子节点测试替身，并补齐已有品牌渲染依赖的 `welcomeHeroDescription` 替身，保留原断言。
- 数据唯一归属保持：本人身份来自现有认证服务；品牌与版本来自现有品牌、版本接口；语言和外观继续由当前浏览器的既有偏好控制器管理。
- 依赖前述已归档账号入口及外观能力；不依赖在途 `add-role-resource-authorization`，不改变权限、租户、业务会话、Desktop 或 Channel 协议。
- 不新增接口、持久化字段、运行时依赖或 feature flag，无数据迁移。本补记不重新归档旧 change，也不改写其历史验收结论。
