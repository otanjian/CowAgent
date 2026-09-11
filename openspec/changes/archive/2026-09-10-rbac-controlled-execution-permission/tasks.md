# Tasks

## 1. 后端运行时执行授权

- [x] 1.1 `agent/protocol/agent_stream.py::_permission_denial` 仅在非 database 模式执行 `check_tool_call`；database 模式保留隔离、`tool.execute` 资源授权与配额
- [x] 1.2 被拒事件区分来源：legacy 模式拒绝保留 `permission_mode`；database 模式的角色/隔离/配额拒绝不再以会话模式为原因（新增 `permission_denial_kind`）
- [x] 1.3 确认 legacy 单租户的 `check_tool_call` 行为与顺序不变（无 database identity 时不受影响）

## 2. 后端接口语义

- [x] 2.1 `/api/sessions/{id}/settings`：database 模式返回 `permission.source="role"`、`modes=[]`，且 POST 忽略会话级 permission 覆盖
- [x] 2.2 `/api/config`：database 模式标记全局 `agent_permission_mode` 只读（返回 `permission_mode_source`/`permission_mode_editable`），POST 忽略该键；legacy 保持可编辑
- [x] 2.3 复用既有 `database_mode()` 判定，不新增身份模式探测

## 3. Web 控制台

- [x] 3.1 `chat.html` 移除 `#permission-selector-btn` 与 `#permission-selector-menu`
- [x] 3.2 `console.js` 移除 `togglePermissionSelector`/`renderPermissionMenu`/`selectSessionPermission`/`_renderPermissionChip`/`_permBtn`/`_permMenu` 及 permission 分支
- [x] 3.3 `console.js::_appendPermissionDeniedHint` 去掉「调整权限」按钮，按拒绝来源选择提示文案
- [x] 3.4 `console.css` 清理 `.perm-denied-btn` 等仅服务于已移除入口的样式
- [x] 3.5 设置页「默认权限」在 database 模式渲染为只读（`initDropdown({readOnly:true})` + `cfg-dropdown-readonly` + `#cfg-permission-role-desc` 说明）；legacy 保持可编辑
- [x] 3.6 清理 `perm_*` 文案（保留被拒提示所需的 `perm_denied_hint` / `perm_denied_role_hint`）

## 4. Desktop

- [x] 4.1 `ChatInput.tsx` 移除 `<PermissionSelector/>` 引用并删除 `PermissionSelector.tsx`
- [x] 4.2 `MessageSteps.tsx::PermissionDeniedHint` 去掉调整按钮，按拒绝来源选择文案（`permission_denial_kind`）
- [x] 4.3 `sessionSettingsStore.ts` 去掉 permission 菜单/覆盖写入（`ComposerMenu` 不再含 `permission`，`apply` 不再接受 `permission`）
- [x] 4.4 `i18n.ts` 清理已移除入口的 `perm_*` 文案（简/英对称），并新增 `perm_denied_role_hint`
- [x] 4.5 Desktop 设置页「默认权限」在 database 模式只读（`permission_mode_editable` → `Dropdown disabled` + 角色说明文案）

## 5. 测试与验收

- [x] 5.1 新增/更新后端用例：database 模式角色已授权 + 模式 read-only → 放行；缺 `tool.execute`/grant → 拒绝（`tests/test_rbac_execution_permission.py`）
- [x] 5.2 新增/更新后端用例：legacy 模式 read-only 仍拒绝写入类工具（`test_rbac_execution_permission.py::LegacyModeExecutionGateTest`）
- [x] 5.3 用例覆盖 `/api/config` 与 `/api/sessions/{id}/settings` 的 database 只读语义（`PermissionModeProjectionTest`、`tests/test_session_model_scope.py::SessionSettingsScopeTests`/`SessionSettingsPostScopeTests`）
- [x] 5.4 运行 `tests/test_execution_isolation.py`、`tests/test_self_authorized_tools.py`、`tests/test_identity_resource_authorization.py` 及新增用例（61 passed）
- [x] 5.5 更新受影响的文档描述（`docs/intro/architecture.mdx`、`docs/channels/web.mdx` 及中/日版本关于权限模式可会话调整的表述）
