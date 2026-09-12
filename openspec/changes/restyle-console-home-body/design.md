## 当前代码接入点

- 首页空态由 `console.js` 的 `renderWelcomeScreen()` 用启动时缓存的 `#welcome-screen` 片段重建，并按 `data-i18n` 覆写文本；真实输入框 `#composer-card` 始终是它的兄弟节点，靠 `#chat-main.chat-home` 下的 `display: contents` + `order` 重新排序成「介绍 / 输入框 / 快捷入口 / 页脚」。因此首页版式只在 CSS 层，新增/移动元素必须放在 `#composer-card` 内或 `#welcome-screen` 片段内，且不能给图标加 `data-i18n`。
- 全部首页样式集中在 `appearance.css` 的 `html[data-web-palette]` 作用域，配色 token 由 `appearance.js` 在首屏前写入 `data-web-palette` 与 `.dark`。

## 方案与取舍

1. **色块图标不新增 DOM 层级，直接复用既有 `<i>`**。卡片原本就是「`<i>` + 文案 `<div>`」，把 `<i>` 本身做成 40×40 圆角色块，避免新增包裹元素后同步改动 `.example-card > :is(i, ...)` 一类选择器，也不影响 `data-i18n` 覆写。
2. **分类色调用色相变量派生，而不是写死六组十六进制**。以 `--tile-h` 承载色相，浅色取 `hsl(H 82% 96%)` / `hsl(H 74% 46%)`，深色取 `hsl(H 42% 20%)` / `hsl(H 78% 68%)`。这样六个色块共用同一饱和度与明度台阶，视觉上同族，且深色只需换两条公式。代价是离开附图 2 的具体色值（附图 2 用的是 Tailwind 500 级六色，明度不齐），换成等明度后更统一。
3. **不使用 `color-mix`**。仓库内只有 Desktop 用了该特性，Web 控制台未使用，为避免扩大浏览器基线，分隔线与边框继续走 `--web-border`。
4. **提示行内化用 flex 而非绝对定位**。控制区行内把工具组改为 `flex: 0 1 auto`、提示改 `flex: 1 1 auto`，提示自然占据中间空隙；窄屏 `flex-basis: 100%` 独占一行。避免绝对定位在窄屏与换行时的重叠。
5. **分隔线通栏用内边距变量反算**。卡片横向内边距抽成 `--composer-pad-x`，分隔线取 `margin: 0 calc(-1 * var(--composer-pad-x))`，窄屏只改一处变量即可保持通栏对齐。
6. **账号卡片不改成两行，层级改由字重与头像描边承担**。`sidebar-account-menu` 明确要求按钮是「紧凑的单行」，并写明 `@用户名` 不要求在按钮里另占一行；因此把 `.sidebar-account-subtitle` 从视觉隐藏改为可见会直接反向修改该 requirement。本 change 改为在单行内建立层级：姓名提到 600 字重，28px 头像（规范固定值）加一层取 `--web-sidebar-border` 的 inset 描边，描边颜色随配色走而不是固定色。这样既满足 brief 的「优化排版层级」，又不触碰账号身份与菜单的既有契约。
7. **待办角标只改呈现，不改数量口径**。数量文本由 `todos.js` 的 `applySummaryBadge()` 依据 `todo-workbench` 既有 summary（0 隐藏、逾期走 `bg-red-500`）写入，本 change 只补 CSS：数字与背景对比度不低于 4.5:1，默认态加 `--web-sidebar-border` 描边以便在浅色侧栏上成形，逾期态保留危险色但持续以数字表达。因此不新增 `todo-workbench` delta。

## 分阶段门槛

本 change 为纯视觉与排版调整，不引入执行、凭据或配额相关能力，不设 feature flag。门槛是浏览器实测（浅色/深色、三种配色、1920 与 375 视口）与既有前端契约测试无新增失败。

## 迁移与恢复

无数据迁移。外观偏好存储格式与 `workbench-appearance-preferences` 的旧偏好迁移规则不变；回滚即回退 `chat.html` 与 `appearance.css` 两个文件。

## 未决实施参数

- 输入框圆角 20px 与「大容器统一 12px 圆角」的 brief 冲突，待圆角体系定稿。本 change 内按附图 2 取 20px。
- `test_appearance_browser.cjs` 的 `button.example-card` 计数断言已由 3 改为 6 以对齐实现与新规范，但该文件因缺 Playwright 无法在本环境运行，仅以 `node --check` 确认语法。
- 附图 2 的账号区含 `@用户名/租户` 副标题；因 `sidebar-account-menu` 明令单行，副标题未启用。若产品确需副标题，须先修改该 requirement 再实施。

## 冲突基线覆盖

`scripts/conflict-baseline.txt` 冻结于 `origin/master@9ad944dd × origin/rdai@617abfae`（21 冲突文件，其中 9 条 `seam:`）。本 change 只落在已登记的接缝文件与一个非冲突文件上，其余接缝保持不变：

| 基线接缝文件 | 处置 |
| --- | --- |
| `channel/web/chat.html` (`seam:8.8`) | **本 change 修改**。改动限于 fork 自有首页区域：快捷入口卡片加 `data-hue` / `example-card-icon`、提示移入控制区行、新增 `.composer-toolbar-rule`。不采用上游欢迎页标记，接缝方向不变。 |
| `channel/web/static/js/console.js` (`seam:4.13-4.16`) | 未修改。`renderWelcomeScreen()`、`bindWelcomeSuggestions()` 与 `dataset.promptKey` 填充路径按原样复用，仅由 CSS 承接新排版。 |
| `channel/web/web_channel.py` (`seam:4.1-4.12`) | 未修改。首页仍按请求读取 `chat.html` 并做 `?v=` 缓存击穿。 |
| `app.py` (`seam:6.11,8.9`) | 未修改。 |
| `agent/memory/conversation_store.py` (`seam:6.1-6.11`) | 未修改。 |
| `agent/tools/scheduler/integration.py` (`seam:8.15-8.16`) | 未修改。 |
| `channel/channel_instances.py` (`seam:8.13`) | 未修改。 |
| `desktop/src/renderer/src/api/client.ts` (`seam:2.11-2.12,4.19`) | 未修改。Desktop 外观与主题选择不受本能力影响。 |
| `tests/test_scheduler_web_update.py` (`seam:8.17`) | 未修改。 |

`channel/web/static/css/appearance.css` 不在冲突基线内（上游无对应文件），本次改动不产生新的同步冲突。

