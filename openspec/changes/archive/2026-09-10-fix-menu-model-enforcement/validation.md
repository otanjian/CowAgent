# 验收记录：fix-menu-model-enforcement

日期：2026-09-10

## 1. 自动化测试

### 新增（先 RED 后 GREEN）

| 文件 | 覆盖 | 结果 |
| --- | --- | --- |
| `tests/test_menu_grant_enforcement.py` | D1：有 menu grant 集合则强制（workbench.history / workbench.knowledge / admin.members / admin.organization）；无 grant 兼容回退；平台 `all` 不受限 | 5 passed |
| `tests/test_session_model_scope.py` | D3/D4：database 缺身份 fail-closed、legacy 返回 `None`、只列授权模型、会话 pin 与全局继承越权时 `selection_required`、平台 `all` 全量 | 7 passed |
| `tests/test_workbench_menu_grant_frontend.cjs` | D2：`menu_denied` 拒绝/隐藏 `#sidebar-recent`、无 grant / `all` / 未知投影 / legacy 不误拒、不可达不发请求、chat.html 无 `data-view="history"`、CSS `.sidebar-recent.hidden` | 9 passed |

初始 RED：`6 failed`（后端 3 条菜单 + 3 条模型），实现后 `12 passed`。

### 回归（本变更相关子集）

全绿：`test_identity_resource_authorization.py`、`test_identity_service.py`、`test_identity_service_writes.py`、`test_identity_web_handlers.py`、`test_session_model_catalog.py`、`test_http_policy.py`、`test_tenant_channel_console_scope.py`（合计 187 + 22 passed）。

前端全绿：`test_sidebar_account_frontend.cjs`、`test_nav_area_frontend.cjs`、`test_channel_scope_nav_frontend.cjs`、`test_tenant_admin_account_picker.cjs`、`test_identity_admin_frontend.cjs` 等（71 passed）。

### 既有失败（与本变更无关，已核验）

- `tests/test_session_history_frontend.cjs`：HEAD 与工作树均为 12 passed / 11 failed，计数一致。
- `tests/test_identity_self_context.py`：`self_context` 新增 `avatar` 字段而测试白名单未更新（用户在进行中的改动）。
- `tests/test_session_history_search.py`：搜索响应结构（`query` 缺失）与 `SimpleNamespace` 桩不匹配。
- `tests/branding_frontend.test.cjs`：品牌预览 `el.closest` 桩缺失。

## 2. 运行实例端到端复现（端口 9899）

被测身份：`test15-2`（`usr_EMjtqQ_5s9oey1y1`），租户 `tnt_EA3qM-lHPLD8ZPwW`，角色 `test15-1`。
该角色有 11 条显式 `menu` grant，**不含** `nav:workbench.history`；模型仅授予 `provider:deepseek:deepseek-v4-flash`（use/read）。

### 修复前

```
workbench.history  available=False read_allowed=True  reason=consumer_closed menu_denied=None
admin.members      available=True  read_allowed=True  reason=''
workbench.knowledge available=False read_allowed=True reason=consumer_closed

GET /api/sessions/<id>/settings
  effective: deepseek-v4-flash-vision-exp | source: global | selection_required: None
  offered: ['deepseek-v4-flash-vision-exp', 'deepseek-v4-flash', 'deepseek-v4-pro']
```

即：未授权菜单仍可读、模型选择器泄漏 3 个模型且生效值为未授权的全局默认。

### 修复后（重启新构建）

```
workbench.history        available=False read_allowed=False reason=menu_not_granted menu_denied=True
workbench.chat           available=False read_allowed=False reason=consumer_closed menu_denied=None
workbench.knowledge      available=False read_allowed=True  reason=consumer_closed menu_denied=None
admin.members            available=False read_allowed=False reason=menu_not_granted menu_denied=True
admin.roles              available=False read_allowed=False reason=menu_not_granted menu_denied=True
admin.organization       available=False read_allowed=False reason=menu_not_granted menu_denied=True

GET /api/sessions/<id>/settings
  effective: '' | source: unset | selection_required: True
  offered: ['deepseek-v4-flash']
```

- 「会话历史」入口 / 直达被拒（`menu_denied=True`）。✅
- `workbench.knowledge`（已授权菜单）未被 menu 规则误拒。✅
- 模型选择器只列 1 个授权模型；继承的全局默认越权 → 不作为生效模型并要求重选。✅
- 已授权菜单但功能权限为空的 `workbench.chat` 仍可导航（不因 `read_allowed=False` 被误拒）。✅

静态资源已确认服务端输出新逻辑：`/assets/js/console.js` 含 `menu_denied`（2 处）与 `_sidebarRecentDenied`（4 处），`/assets/css/console.css` 含 `.sidebar-recent.hidden`。

## 3. 实现偏离说明

- 任务 3.4 原计划给 `#sidebar-recent` 加 `data-view="history"`。既有契约 `tests/test_nav_area_frontend.cjs` 断言 `data-view="history"` 必须不存在（顶栏历史入口已折叠进该区块），故改为按元素 id 显式门控，效果等价。
- 任务 1.4 由"逐分支接入 `menu_view_ok`"改为"统一后置过滤 + `menu_denied` 标记"，覆盖更全且便于前端消费。

## 4. 遗留（超出本次范围）

- 运行时的每次模型调用守卫沿用既有 `resource-execution-authorization`；会话设置读取/展示层面已收口。
- 修复后 `test15-2` 的 `/api/sessions` 数据接口仍受 `history.read` 功能权限保护（菜单是展示层概念），符合规范第 1 条 Scenario。
