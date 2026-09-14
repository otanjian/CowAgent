## 1. 结构与标签渲染

- [x] 1.1 `channel/web/chat.html`：个人资料头像外层增加 `.account-profile-avatar-wrap`，并在头像右下角加 `.account-profile-avatar-edit` 编辑指示图标（不写入头像按钮 `innerHTML`）
- [x] 1.2 `channel/web/static/js/console.js`：新增 `_accountChips(elId, values)`，空值回退 `account_profile_empty` 纯文本，值用 `escapeHtml` 转义后渲染为 `.account-profile-chip`
- [x] 1.3 `renderAccountProfile`：平台身份为平台管理员时用标签渲染，否则保持 `account_public_mode` 纯文本；实际角色逐项用标签渲染

## 2. 头像双击更换

- [x] 2.1 `channel/web/chat.html`：把 `#account-profile-avatar-input` 移出 `#account-profile-avatar`，作为 `.account-profile-avatar-wrap` 的兄弟节点（修复重绘删除输入框导致的更换失效）
- [x] 2.2 `channel/web/static/js/console.js`：新增 `handleAccountProfileAvatarActivate(event)`，`detail === 1` 不触发、`detail >= 2` 与键盘（`detail === 0`）触发；新增 `openAccountProfileAvatarPicker()` 并先清空 `value` 以支持重复选择同一文件
- [x] 2.3 i18n `account_profile_avatar_hint` 三语改为「双击可更换头像」，同步 `tests/fixtures/console_i18n_snapshot.json`
- [x] 2.4 `console.css`：头像区域 `user-select: none`、按钮 `:focus-visible` 外框、悬停/聚焦时加深编辑指示

## 3. 视觉层级与主题配色

- [x] 2.1 `channel/web/static/css/console.css`：`.account-profile-grid` 改为分组公用网格（标签列固定 96px），`.account-profile-row` 用 `display: contents`，标签右对齐、值左对齐
- [x] 2.2 标签/值/分组标题/分割线改为「浅色基准 + `.dark` 覆盖」，修复浅色主题下值文本近白不可读的问题
- [x] 2.3 hero：放大头像、展示名最大字号加粗、用户名次级色；标题栏加粗并在底部加浅分割线（限定 `#account-profile-drawer` 作用域）
- [x] 2.4 分组间距大于组内行距（组间松、组内紧）；新增 `.account-profile-chip` 标签样式（浅底色 + 圆角，含 `.dark` 覆盖）
- [x] 2.5 窄视口媒体查询下调标签列宽，避免小屏溢出

## 4. 测试与校验

- [x] 4.1 `tests/test_sidebar_account_frontend.cjs`：覆盖平台身份/实际角色渲染为标签、空值回退为纯文本
- [x] 4.2 `tests/test_sidebar_account_frontend.cjs`：覆盖双击首个单击不触发、双击触发、键盘触发、`value` 重置
- [x] 4.3 断言 `chat.html` 中文件输入不在头像按钮内部（锁定重绘删除输入框的回归）
- [x] 4.4 运行 `node --test tests/test_sidebar_account_frontend.cjs` 及相关前端测试，确认通过
- [x] 4.5 `openspec validate polish-account-profile-dialog --strict` 通过
