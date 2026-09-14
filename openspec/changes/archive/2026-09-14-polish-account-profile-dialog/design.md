## Context

个人资料弹窗由 `channel/web/chat.html`（`#account-profile-drawer` 结构）、`channel/web/static/js/console.js`（`openAccountProfile` / `renderAccountProfile` / `renderAccountProfileAvatar` / 编辑态切换）与 `channel/web/static/css/console.css`（`.account-profile-*`）三处共同实现。

现状约束：

- 每个 `.account-profile-row` 是独立的 `grid`，标签列用 `minmax(96px, auto)`，宽度随各行内容变化，所以值的起始位置逐行不同。
- `.account-profile-row dt/dd` 使用 `#969da5` / `#e8eee9` 等深色主题前景色但未加 `.dark` 限定；弹窗卡片浅色主题为 `#fff`（`.agent-modal-card`），只有在根节点带 `.dark` 时才是 `#1A1A1A`。因此浅色主题下值文本（近白）几乎不可见。
- 主题由 `appearance.js` 在根节点切换 `.dark` 类；`console.css` 的约定是「基准样式为浅色，`.dark <selector>` 覆盖为深色」。`console.css` 已有 `--history-*` 变量先例，但账号区未使用变量。
- `renderAccountProfileAvatar` 直接写 `#account-profile-avatar` 的 `innerHTML`，上传中也在该节点上加 `is-uploading`，因此头像编辑指示 MUST NOT 放在该节点的 `innerHTML` 内，否则会被覆盖。

## Goals / Non-Goals

**Goals:**

- 两列标签/值在同分组内严格对齐，并随语言（中/繁/英）自动选取合适标签列宽。
- 浅色与深色主题下标签与值均清晰可读。
- 头像、展示名、身份角色形成明确主次层级；标题栏与分组结构清晰。
- 头像可真正更换：双击与键盘都能打开文件选择，且不受头像重绘影响。

**Non-Goals:**

- 不改资料字段、鉴权、接口、数据来源与 i18n 键集合（`account_profile_avatar_hint` 的文案随手势调整，键名不变）。
- 不引入 CSS 预处理器、构建步骤或新运行时依赖。
- 不改动 `add-default-user-avatars` 正在调整的头像来源与 `user-avatar` 能力。

## Decisions

### D1. 行用 `display: contents`，分组公用一个网格

把 `.account-profile-grid` 改为 `grid-template-columns: var(--account-profile-label-w, 96px) minmax(0, 1fr)`，`.account-profile-row { display: contents }`，`dt` 右对齐、`dd` 左对齐。

- 备选 A：每行继续独立 grid，仅把标签列改成固定 `96px`。否决——固定宽度在英文较长标签（如 `Current tenant`）下会溢出或被迫缩小字号，且仍需为多语言手工调值。
- 备选 B：分组级 `auto` 列，无固定值。否决——`auto` 会退化为最大内容宽度，标签列不再有统一的右对齐边，视觉上仍不齐。

固定 `96px` 保证跨两个分组、跨语言的值列基线一致；中英文标签均可容纳（英文最长约 `Current tenant`）。

### D2. 标签与值颜色改为「浅色基准 + `.dark` 覆盖」

基准（浅色）：`dt` 用 `#666`、`dd` 用 `#333`、分组标题用 `#475569`、分割线用 `#eef1f4`；`.dark` 下覆盖为 `#969da5` / `#e8eee9` / `#969da5` / `rgba(255,255,255,.08)`，与既有深色口径一致。

- 备选：引入完整的主题 CSS 变量集。否决——超出本 change 范围，改动面更大；沿用仓库既有「浅色基准 + `.dark` 覆盖」约定即可。

### D3. 身份角色用 JS 渲染为标签元素

在 `console.js` 新增 `_renderAccountChips(elId, values)`：值列表非空时把每项渲染为 `<span class="account-profile-chip">`，用既有 `escapeHtml` 转义；为空时回退为 `account_profile_empty` 纯文本。平台身份为平台管理员时渲染标签，否则保持 `account_public_mode` 纯文本；实际角色把 `roles` 的 `name || code` 逐项渲染为标签。角色/平台身份不可编辑，因此不涉及编辑态。

- 备选：纯 CSS 美化 `dd`。否决——单值加底色可行，但多角色、空值回退、中英文混排无法仅靠 CSS 表达。

### D4. 头像编辑指示与文件入口都用兄弟节点，不写进头像按钮 `innerHTML`

在 `#account-profile-avatar` 外层加 `.account-profile-avatar-wrap`（`position: relative`），编辑指示 `.account-profile-avatar-edit` 与文件输入 `#account-profile-avatar-input` 都作为其兄弟节点：编辑指示绝对定位到右下角并 `pointer-events: none`，让点击落到头像按钮；文件输入保持隐藏。

这同时修掉一个现存缺陷：`renderAccountProfileAvatar` 会整体改写 `#account-profile-avatar` 的 `innerHTML`，而原文件输入是该按钮的子节点，首次渲染即被删除，原先的 `onclick="...getElementById(...).click()"` 因此总是取到 `null`，头像根本无法更换。把入口移出重绘范围后，重绘不再破坏它。

- 备选：只把编辑指示放外面、文件输入留在按钮内并每帧重建。否决——每次渲染都要重挂监听，且 `userAvatarHTML` 需要感知上传入口，耦合更重。
- 备选：编辑指示用 `::after` 伪元素。否决——需要硬编码 Font Awesome 字形与字体族，脆弱且多主题下不稳。

### D5. 更换手势用 `click` 的 `detail` 计数实现双击，并保留键盘可用

在头像按钮上挂 `click` 处理器 `handleAccountProfileAvatarActivate(event)`：`event.detail === 1`（指针的首次单击）直接返回，`detail >= 2`（双击的第二次）才打开文件选择；`detail === 0` 表示键盘激活，同样打开。打开前先清空输入框的 `value`，否则重复选择同一文件不会触发 `change`。

- 备选：改用 `ondblclick` 并移除 `onclick`。否决——按钮失去可点击语义后键盘 Enter/Space 将完全无效，牺牲可访问性。
- 备选：保留单击触发。否决——与用户明确要求的双击手势冲突，且单击即弹原生文件对话框对「顺手点一下头像」过于激进。
- 权衡：`detail` 在极少数合成事件下可能缺失，此时按 0 处理（等价键盘激活，仍可更换），不会把入口彻底关死。

## Risks / Trade-offs

- [`display: contents` 在个别旧浏览器的可访问性差异] → 保留 `dt`/`dd` 语义标签，只改布局；RongAI 控制台目标浏览器均为现代浏览器。
- [固定标签列宽在超长英文/小屏溢出] → 标签 `white-space: nowrap` 且宽度取可容纳最长文案的值；窄视口媒体查询下调小该变量。
- [标签渲染改动影响既有断言] → 仅新增 `_renderAccountChips` 与两处调用；`account-profile-empty` 回退仍为纯文本，避免破坏既有「空值显示未设置」口径。
- [浅色配色改动波及其它弹窗] → 仅修改 `.account-profile-*` 与 `#account-profile-drawer` 作用域内规则，`agent-modal-*` 共享样式不动。
- [双击手势不易发现] → 编辑指示图标在悬停/键盘聚焦时加深，并通过 `title` 与 `aria-label` 明示「双击可更换头像」。
- [双击被用户误读为「需要点两下才能点中」] → 保留键盘激活路径并在 title 中给出文案；若实测反馈不佳，可改为悬停时在头像上覆盖一层「更换」提示按钮（不改动 spec 的对齐与层级要求）。

## Migration Plan

- 纯前端呈现调整，无数据迁移、无接口变更。
- 回滚：还原 `chat.html`/`console.css`/`console.js` 三处改动即可恢复旧布局。
- 主题兼容：浅色为基准，深色通过既有 `.dark` 覆盖维持现状。

## Open Questions

无。
