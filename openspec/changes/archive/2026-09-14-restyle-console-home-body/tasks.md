## 1. 前置与门槛

- [x] 1.1 核对 `workbench-appearance-preferences`、`console-navigation-availability`、`sidebar-account-menu` 的既有规范，确认本次只动首页正文的视觉与排版，不触碰导航可达性、账号身份与外观存储规则
- [x] 1.2 记录基线与冲突：明确首页正文在规范中为「左对齐 + 三条建议」，实现为六个卡片；本 change 以附图 2 为准改规范，不引用 PRD 编号
- [x] 1.3 记录前置失败项以隔离回归：`tests/test_appearance_browser.cjs`（缺 Playwright）与 `tests/test_console_i18n_parity.cjs`（`config_password*` 键）在干净工作树同样各 1 例失败

## 2. 首页正文版式

- [x] 2.1 `appearance.css`：首页背景改用 `--web-content-bg`，删除浅色下硬编码白色与多余的深色覆盖
- [x] 2.2 `appearance.css`：欢迎介绍整组居中（眉标 flex 居中、主标题/副标题/品牌说明 `margin: auto`），主标题字号与字重提升到 `clamp(30px, 3vw, 38px)` / 700，副标题 16px
- [x] 2.3 `chat.html`：六个快捷入口补齐 `data-hue` 与 `example-card-icon`，`console.js` 的 `dataset.promptKey` 点击填充路径不变
- [x] 2.4 `appearance.css`：卡片改为纵向结构（40px 色块图标在上，标题与说明在下），桌面三列等宽、间距 14px、圆角 16px、分层阴影
- [x] 2.5 `appearance.css`：六种分类色调以 `--tile-h` 色相 + 固定饱和度/明度派生，浅色与深色各一套，不使用 `color-mix`；hover 上移 2px、阴影加深、图标放大 1.06
- [x] 2.6 `appearance.css`：页脚改为 `margin-top: auto` 贴底，并保留最小间距，避免内容溢出时挤压卡片
- [x] 2.7 `appearance.css`：`prefers-reduced-motion` 下关闭卡片与图标的位移与缩放过渡

## 3. 输入框视觉升级

- [x] 3.1 `chat.html`：控制区上方新增 `.composer-toolbar-rule`；提示元素移入控制区行内
- [x] 3.2 `appearance.css`：分隔线以内边距变量 `--composer-pad-x` 反算负边距实现通栏，深浅色分别 20px / 16px，提示在宽屏占工具组与模型组之间的空隙、窄屏独占一行不截断
- [x] 3.3 `appearance.css`：输入框圆角 20px、内边距 20px、最小高度 200px，默认浅灰边框与聚焦品牌色描边 + 光环过渡保留

## 4. 验证

- [x] 4.1 浏览器实测（业务运行实例 `:9899/chat`）：浅色 1920×1080 与深色渲染，主标题/副标题居中，控制区分隔线通栏、提示行内可见
- [x] 4.2 浏览器实测几何：三列各 297.33px、卡片 297×142、列间距 14px、行间距约 15px，卡片无重叠；`--tile-h` 生效、浅色与深色底色分别取到两套台阶
- [x] 4.3 浏览器实测消息态：移除 `chat-home` 后分隔线与提示均 `display:none`，卡片圆角 18px、最小高度 0、工具组间距 6px，即非首页输入框未受影响
- [x] 4.4 浏览器实测窄屏：375px 下为单列、`documentElement.scrollWidth === innerWidth === 375`、无越界元素，提示独占一行且未截断
- [x] 4.5 浏览器实测配色：商务青蓝浅色、深色、深蓝侧栏浅色三种组合下分类色调归属一致且可辨
- [x] 4.6 回归：`test_fork_fragments`、`test_execution_permission_ui`、`test_nav_area_frontend`、`test_channel_scope_nav_frontend`、`test_workbench_menu_grant_frontend`、`test_forced_password_gate`、`test_admin_home_frontend`、`test_composer_agents_frontend`、`test_agent_workbench_frontend` 与三个 i18n 键测试全部通过；两个既有失败项用 `git stash` 对照干净工作树确认失败数不变
- [x] 4.7 修正契约断言：`test_appearance_browser.cjs` 中三处 `button.example-card` 计数由 3 改为 6，与实现及本 change 后的规范一致；该文件在本环境仍因缺 Playwright 无法运行，已用 `node --check` 确认语法有效
- [x] 4.8 记录圆角决策：`交付附图 2` 的输入框 20px 圆角与 `视觉设计优化` brief 中「大容器统一 12px 圆角」不一致，本 change 按附图 2 取 20px，待统一圆角规范定稿后再收敛

## 5. 细节补全

- [x] 5.1 `appearance.css`：待办角标补齐数字呈现（`todos.js` 已写入 `summary.open`），数字与角标背景对比度不低于 4.5:1，并以环形描边与侧栏背景分离
- [x] 5.2 `appearance.css`：逾期角标除 `bg-red-500` 危险色外仍以数量文本表达，不使用颜色作为唯一区分
- [x] 5.3 `appearance.css`：账号卡片保持单行紧凑契约（最小高度 40px、28px 头像、姓名单行省略），层级改由姓名 600 字重与配色描边的头像建立；`@用户名` 继续留在悬停提示与展开菜单，不在按钮内新增第二行
- [x] 5.4 浏览器实测（`:9899/chat`）：商务青蓝/深蓝侧栏/经典 × 浅色/深色六种组合下账号卡片均为 40px 单行，28×28 头像带配色 inset 描边，姓名对比度 13.74 / 14.24 / 18.07 均高于 4.5:1
- [x] 5.5 浏览器实测：待办数量为 0 时角标 `0×0` 且文本为空（符合 `todo-workbench` 的 0 隐藏口径），逾期态 `bg-red-500` 与默认态前景对比分别为 12.53 / 11.07 / 14.61 且均可区分
- [x] 5.6 复核规范边界：账号卡片单行契约属 `sidebar-account-menu`、角标数量口径属 `todo-workbench`，两者既有 requirement 均未改动，本 change 只改视觉呈现，故 delta 仍收在 `workbench-appearance-preferences`

## 6. 欢迎组纵向居中

- [x] 6.1 `appearance.css`：`#chat-main.chat-home` 用 `padding-top` 把「欢迎介绍 + 输入框」整体推到可视区纵向正中，偏移取 `max(24px, (100dvh - 顶栏 - 输入组) / 2)`；`--home-header-h`、`--home-hero-h` 承载两个高度，其中偏移抽成 `--home-hero-offset` 供弹层复用；短视口以 24px 兜底，避免开屏即滚到输入组中间
- [x] 6.2 `appearance.css`：`.home-intro` 的 `margin-top`（含 640px 断点的 32px 覆盖）改为 0，把顶距交还给容器，避免两处偏移叠加使居中失效
- [x] 6.3 `console.js`：新增 `syncHomeHeroOffset()`，实测`.home-intro` 顶到 `#chat-input-area` 底的跨度写入 `--home-hero-h`（品牌说明行与窄屏字号梯度都会改变该高度，故不写死）；因偏移对输入组是刚性平移，测量与结果不构成回环
- [x] 6.8 触发方式修正：测量曾只在 `syncChatHomeLayout()`、`applyBrandToDocument()` 与 rAF 合并的 `resize` 上触发，实测存在两个静默失效——启动时控制台仍被认证遮罩藏在 `hidden` 后，`getBoundingClientRect()` 全为 0 故被跳过；而 rAF 与 `ResizeObserver` 在被遮挡的 webview 中不回调。改为：`resize` 同步测量（不再合并到 rAF），并用 `MutationObserver` 监听 `#app` 的 class（认证通过时 `_accountHidden('app', false)` 切换该 class）触发重测——mutation 回调走微任务队列，不依赖渲染；`.home-intro` 的 `ResizeObserver` 仅作内容高度变化（品牌行、字体替换、文案换行）的补充。实测刷新后无需任何手动调用即写入 388px 且 `offsetPx = 0`
- [x] 6.4 `appearance.css`：首页折叠菜单补 `max-height`，取输入组下方实际可显示空间并在内部滚动。居中后输入组上下余量相等（约 `(视口 - 顶栏 - 输入组) / 2`），不足以容纳 320px 的既有上限，必须收敛否则被 `#chat-main` 的 `overflow: hidden auto` 裁掉
- [x] 6.5 浏览器实测居中精度：`offsetPx = 0` 于 660×994、1440×900、1280×720、375×812 四个视口；`node --check` 通过
- [x] 6.6 浏览器实测弹层：四个视口下指令/附件/工作空间/模型/智能体菜单底边均不出可视区（如 1280×720 收敛至 108.5px 底部 703 < 720）；修复前 660×994 下指令菜单底部 1050 > 994 且 `#chat-main` 的 `scrollHeight` 不增长，即被裁掉而非可滚动
- [x] 6.7 浏览器实测无回归：非首页 `padding-top` 为 0px、消息态不受影响；`intro.bottom <= composer.top`、`composer.bottom <= suggestions.top` 成立；`documentElement.scrollWidth <= innerWidth` 于各视口成立
