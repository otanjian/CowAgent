## 1. 后端：菜单授权门控（D1）

- [x] 1.1 写失败测试：`_console_pages_projection` 对"有 menu grant 集合但缺 `nav:workbench.history`"的角色返回 `workbench.history.read_allowed=false`（`tests/test_menu_grant_enforcement.py`）
- [x] 1.2 写失败测试：完全没有 menu grant 的角色（内置 `member`/`tenant_admin`）沿用功能权限，页面 `read_allowed` 不因缺 grant 变 false
- [x] 1.3 写失败测试：平台 `all` 身份忽略 menu grant 限制
- [x] 1.4 在 `auth/service.py::_console_pages_projection` 实现 menu 门槛（存在即强制 + 兼容回退）。实现方式为**统一后置过滤**：命中缺失 grant 的已登记页置 `read_allowed=False`/`available=False`/`reason="menu_not_granted"`/`actions={}` 并标记 `menu_denied=True`，覆盖工作台页、generic 页与 `admin.members/roles/organization`
- [x] 1.5 回归：`admin.tenants` 仍只由平台身份决定，未被 menu 规则改动（后置过滤不匹配平台页语义）
- [x] 1.6 跑 `tests/test_identity_resource_authorization.py`、`tests/test_tenant_channel_console_scope.py` 等既有授权测试并修复回归

## 2. 后端：会话模型作用域与允许集合（D3/D4）

- [x] 2.1 写失败测试：database 模式下 `_authorized_model_codes()` 在身份缺失时返回空集合（fail-closed），legacy 模式仍返回 `None`（`tests/test_session_model_scope.py`）
- [x] 2.2 写失败测试：`_session_settings_state`（GET 路径）在 database 模式下只返回被授权的模型
- [x] 2.3 写失败测试：继承到的全局/会话 pin 模型不在允许集合内时不作为生效模型，返回 `selection_required=True` 且候选仅含授权模型
- [x] 2.4 给 `SessionSettingsHandler.GET` 增加 `with _db_scope():` 包裹（legacy 下保持 no-op）
- [x] 2.5 修改 `_authorized_model_codes()`：区分 legacy `None` 与 database 缺失身份/异常 `set()`
- [x] 2.6 在 `_session_settings_state` 增加继承模型越权守卫与 `selection_required` 状态（含角色默认资源 id 取末段 code）
- [x] 2.7 跑 `tests/test_session_model_catalog.py`、`tests/test_identity_runtime.py`、`tests/test_session_history_search.py`（后者含 5 条既有失败，已确认与本变更无关）及相关会话设置测试

## 3. 前端：workbench 菜单按投影门控（D2）

- [x] 3.1 写失败前端测试：受限角色的 `workbench.history` 投影 `menu_denied` 时 `_viewNavDenied('history')` 返回拒绝（`tests/test_workbench_menu_grant_frontend.cjs`）
- [x] 3.2 写失败前端测试：无 menu grant / 平台 all / 投影未知 / legacy 时 `history` 不被误拒
- [x] 3.3 写失败前端测试：投影不可读时 `#sidebar-recent` 被隐藏且不发起 `/api/sessions` 请求
- [x] 3.4 ~~给 `chat.html` 的 `#sidebar-recent` 增加 `data-view="history"`~~ **偏离**：既有契约 `tests/test_nav_area_frontend.cjs` 要求 `data-view="history"` 不存在（顶栏历史入口已折叠进该区块）。改为按元素 id 显式门控（`_sidebarRecentDenied()`），效果一致且不破坏该契约
- [x] 3.5 修改 `_viewNavDenied`（先判 `menu_denied`）与 `_applySidebarPermissions`（workbench 页按 `menu_denied` 过滤，不再套 admin 门槛）
- [x] 3.6 修改 `loadSidebarRecentSessions`：不可达时隐藏区块并跳过请求；`console.css` 增加 `.sidebar-recent.hidden` 规则
- [x] 3.7 跑 `tests/test_sidebar_account_frontend.cjs`、`tests/test_nav_area_frontend.cjs`、`tests/test_channel_scope_nav_frontend.cjs`、`tests/test_session_history_frontend.cjs`（后者 11 条既有失败在 HEAD 上同样存在，已对齐计数）并修复回归

## 4. 验收与回归

- [x] 4.1 用隔离身份库构造"有 menu grant 集合且缺 history"的角色，验证菜单/直达/会话数据接口行为一致（`test_menu_grant_enforcement.py` + 运行实例）
- [x] 4.2 用内置角色与无 grant 的历史自定义角色验证兼容回退未误隐藏
- [x] 4.3 用平台管理员验证 `all` 不受 menu/model 限制
- [x] 4.4 在运行实例上复现原报告：`test15-2`（角色 `test15-1`）「会话历史」入口消失、模型选择器只列 1 个授权模型（见 `validation.md`）
- [x] 4.5 全量运行本次涉及的后端 pytest 与前端 node:test 子集，确认无回归（仅剩与本变更无关的既有失败）
- [x] 4.6 更新 `openspec/changes/fix-menu-model-enforcement/` 验收记录（`validation.md`）
