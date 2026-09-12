## Why

空态首页（`#chat-main.chat-home`）的产品口径与实现已经分叉：`workbench-appearance-preferences` 的 `Responsive task-oriented homepage` 写的是「左对齐欢迎介绍 + 三条任务建议」，而实现早已是六个快捷入口卡片，本次又按附图 2 把正文改为居中、图标色块、分层阴影的卡片版式。规范若不同步，后续任何改动都会以错误基线验收。

同时 `workbench-appearance-preferences` 的 `Coherent and accessible surfaces` 只约束了配色 token，没有说明首页卡片上的分类色调属于装饰而非状态，容易被后续改动误用为选中/告警语义。

## What Changes

- 首页正文改为附图 2 版式：欢迎介绍整组居中（眉标、主标题、副标题、品牌说明同一光学轴），输入框在其下方，快捷入口按桌面三列两行排布。
- 「欢迎介绍 + 输入框」作为一个整体在可视区纵向居中（产品明确选择此口径）。这会抬高内容总高，使首页由「恰好铺满一屏」变为「整页可滚动」：六个快捷入口保持在输入组下方、首屏通常只能看到第一行，必要时滚动到达。居中偏移按顶栏与输入组实测高度计算，不写死。
- 居中带来的连带修正：输入组下方余量随之减半，既有 320px 上限的指令/附件/工作空间/模型/智能体折叠菜单会被 `#chat-main` 的 `overflow: hidden auto` 裁掉且不产生可滚动高度（实测底部 1050 越出视口 994 而 `scrollHeight` 不变），故这些菜单改为收敛到实际可用高度并在内部滚动。
- 输入框卡片改为「文本区 / 控制区」两个纵向分带：控制区上方加一条通栏细分隔线，`/ 使用指令 · @ 引用智能体或文件` 提示从独立一行上移到控制区行内，占据工具组与模型组之间的空隙。
- 快捷入口卡片从「图标在左、文案在右」改为「色块图标在上、标题与价值说明在下」，并补齐 hover 上移、阴影加深、图标轻微放大；窄屏低于 560px 收敛为单列。
- 规范化六个分类色调：以 `--tile-h` 色相 + 固定饱和度/明度两级台阶派生浅底与深图标，浅色与深色各一套，使六个色块同属一族，不使用 `color-mix` 等新特性。
- 明确分类色调为纯装饰，不编码选中、告警或权限状态；品牌色仍只用于选中态、主操作与关键图标。
- 同色值语义收敛到既有 token：首页背景改用 `--web-content-bg`（不再硬编码白色），卡片与输入框阴影、分隔线沿用 `--web-border` / `--web-surface-bg`。
- 细节补全：侧栏「我的待办」角标补齐数字呈现（数量来源仍是 `todo-workbench` 既有 summary 口径），数字与角标背景对比度不低于 4.5:1 并以描边与侧栏分离；逾期态保留危险色但不用颜色作为唯一提示。账号卡片维持 `sidebar-account-menu` 的单行紧凑契约，层级改由姓名字重与配色描边的头像建立，`@用户名` 不进按钮第二行。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `workbench-appearance-preferences`: 首页正文对齐方式、快捷入口数量与卡片内部结构改为附图 2 口径；补充「欢迎介绍 + 输入框」整组纵向居中及其连带的可滚动与折叠菜单高度收敛；补充分类色调的装饰性约束与窄屏列数收敛；输入框控制区分隔与提示位置的视觉约定；补充侧栏待办角标与账号卡片的可读性与单行契约（不改变 `todo-workbench` 的数量口径与 `sidebar-account-menu` 的单行要求）。

## Impact

- 代码：`channel/web/chat.html`（快捷入口卡片加 `data-hue` 与 `example-card-icon`；提示移入控制区行；控制区上方新增分隔线元素）、`channel/web/static/css/appearance.css`（首页正文纵向居中、输入框、快捷入口与响应式断点；侧栏待办角标与账号卡片细节）、`channel/web/static/js/console.js`（`syncHomeHeroOffset()` 实测输入组高度并写入 `--home-hero-h`，由 `syncChatHomeLayout()`／`applyBrandToDocument()`／`resize` 触发）。
- 测试：`tests/test_appearance_browser.cjs` 的 `button.example-card` 计数断言由 3 改为 6；其 `intro.bottom <= composer.top`、`composer.bottom <= suggestions.top`、无横向溢出与 `mainScrolls` 断言在居中改动后仍成立。
- 不改动：输入框复用关系（仍只有 `#chat-input` 一个 textarea 与一套事件监听）、`console.js` 的 `.example-card` / `dataset.promptKey` 点击填充逻辑、`todos.js` 的 summary 数量口径、i18n 键、外观偏好的存储与迁移规则、侧栏与顶栏的结构与业务菜单归属。
- 未决：`交付附图 2` 的 20px 输入框圆角与 `视觉设计优化` brief 中「大容器统一 12px 圆角」不一致，本 change 按附图 2 取 20px，待统一圆角规范时再收敛。
- 待确认：纵向居中的代价是首屏通常只露快捷入口第一行、其余需滚动（产品已选择该口径）。若希望首屏同时看到两行卡片，需要改为「输入组与卡片整块居中」或收回居中，属另一轮取舍。
- 未决：`交付附图 2` 的 20px 输入框圆角与 `视觉设计优化` brief 中「大容器统一 12px 圆角」不一致，本 change 按附图 2 取 20px，待统一圆角规范时再收敛。
