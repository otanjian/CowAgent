## Why

`add-role-resource-authorization` 已交付角色五类资源授权并归档，但复核实测发现两条读路径没有真正消费授权数据，导致"配置了却不生效"：

- 角色的 `menu` grant（`nav:<page>` / `view`）只在角色编辑器的目录投影里被读取，从未参与页面可见性判断。实测 `test15-2` 的角色 `test15-1` 未授予 `nav:workbench.history`，但侧栏「会话历史」仍显示（`/auth/context` 的 `console_pages["workbench.history"].read_allowed=true`），且前端对 workbench 页一律不做 menu 门控。
- `GET /api/sessions/<id>/settings` 未建立 database 请求作用域（`_db_scope()`），`_authorized_model_codes()` 读到空身份后返回 `None` 被当作"无限制"，把整个供应商的 3 个模型都列给只被授权 1 个模型的成员；同时继承到的全局默认模型也可能落在授权集合之外。

## What Changes

- **菜单可见性消费 menu grant**：`_console_pages_projection` 对已登记页面同时计算读取资格与 `menu`/`nav:<page>`/`view` 授权。采用兼容式强制：仅当成员的有效角色中存在**至少一条**显式 `menu` grant 时，才按该授权集合限制页面；完全没有任何 `menu` grant 的角色（内置 `tenant_admin`/`member` 以及历史遗留的自定义角色）沿用现有功能权限行为，避免迁移期误隐藏。
- **前端按投影门控 workbench 页**：「会话历史」侧栏区块与 `workbench.*` 视图不再无条件显示；可达性统一读取 `/auth/context` 的 `console_pages`，直接地址/hash 进入同样按投影拒绝。
- **会话设置读取纳入 identity 作用域**：`SessionSettingsHandler.GET` 与写入路径一致地使用 `_db_scope()`；`_authorized_model_codes()` 在 database 模式下身份缺失时 fail-closed（返回空集合），不再把"作用域缺失"解释为全部允许。
- **继承模型收口到授权集合**：当会话/Agent/角色/全局默认链解析出的模型不在授权集合内时，不作为可用的生效模型；按规范"要求重选/明确拒绝"处理，不把未授权模型展示为当前选择。
- 不新增任何线上运行入口，不改变平台管理员 `all` 语义，不改动解析/授予的存储结构。

## Capabilities

### New Capabilities
<!-- 无新增能力，均为既有能力的守卫补齐 -->

### Modified Capabilities

- `console-navigation-availability`: 明确"菜单/页签 view 授权"必须被页面可见性与直达判定消费，并定义无任何 menu grant 时的兼容回退规则。
- `role-model-assignment`: 明确会话模型选择读取须在已解析身份作用域内进行，且继承到的生效模型必须落在允许集合内，否则要求重选/明确拒绝。

## Impact

- 代码：`auth/service.py`（`_console_pages_projection`、`_authorized_model_codes` 相关投影）、`channel/web/web_channel.py`（`SessionSettingsHandler.GET`、`_session_settings_state`、`_authorized_model_codes`）、`channel/web/static/js/console.js`、`channel/web/chat.html`。
- 行为：仅对"存在显式 menu grant 的自定义角色"收紧菜单可见性；内置角色与无 menu grant 的历史角色不变。模型选择器对受限角色只列出授权模型。
- 数据/接口：无 schema 变更，无新 API；`/auth/context` 的 `console_pages` 字段语义细化（`read_allowed` 纳入 menu 授权），前端消费方式扩展。
- 依赖：无新增依赖。承接已归档的 `add-role-resource-authorization`；不改变 `platform-all-authorization`、`resource-execution-authorization` 的既有约束。
