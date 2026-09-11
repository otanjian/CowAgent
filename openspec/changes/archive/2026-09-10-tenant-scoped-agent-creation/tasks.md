## 1. 失败测试（先写）

- [x] 1.1 新增 `tests/test_tenant_agent_creation.py`：以真实租户管理员（非平台管理员）身份、经 `AgentsHandler.POST` 走 `create` 动作；夹具固定临时 data root（`config.json` 的 `agent_workspace`）与临时租户根，避免触到开发者本机 `~/cow`。
- [x] 1.2 断言工作区落在 `<tenant root>/agents/<id>`，且**不**写入实例根。
- [x] 1.3 断言创建后 `tenant_agent_ids(tenant)` 含新 id，且 `GET /api/agents` 对该租户返回该智能体。
- [x] 1.4 断言该租户首个智能体成为其租户默认；第二个智能体不改动既有默认。
- [x] 1.5 断言租户管理员可编辑自己租户的智能体。
- [x] 1.6 断言跨租户：另一个租户创建的智能体在本租户列表中不出现，且 `update` 返回 403。
- [x] 1.7 断言构造 id（`../../escape`）被拒绝且不在临时目录之外创建任何目录。
- [x] 1.8 运行 `tests/test_tenant_agent_creation.py`，确认修复前按预期失败（RED）。

## 2. 身份域：租户默认智能体的租户侧入口

- [x] 2.1 `auth/service.py`：把 `set_tenant_default_agent` 拆为门禁 + `_appoint_tenant_default_agent`（无门禁，只校验租户存在且智能体已绑定该租户 + 审计），保持平台管理员路径行为与审计动作不变。
- [x] 2.2 新增 `appoint_tenant_default_agent`，门禁走 `_require_tenant_admin`（平台管理员或该租户管理员）。
- [x] 2.3 回归 `tests/test_tenant_default_agent.py`（含平台侧默认设置与两个租户各自默认）。

## 3. 写入路径：创建即租户作用域并绑定

- [x] 3.1 `channel/web/web_channel.py` 新增 `_tenant_agent_workspace(ctx, agent_id)`：返回 `<shared_root>/agents/<id>`；无共享根返回 None。
- [x] 3.2 `AgentsHandler.POST` 的 `create` 分支：database 模式且客户端未给 `workspace` 时，先用 `agent.registry._AGENT_ID_RE` 校验 id（非法即返回错误、不落盘），再传入租户作用域 workspace；显式 `workspace` 仍优先。
- [x] 3.3 新增 `_adopt_created_agent_for_tenant(ctx, agent_id)`：记原绑定集合 → `bind_agent` → 若此前无绑定且无默认则 `appoint_tenant_default_agent`。
- [x] 3.4 create 成功且存在 `ctx.tenant_id` 时调用 3.3；legacy（`ctx is None`）不进入该分支。

## 4. 读取与执行路径：租户绑定为管理员边界

- [x] 4.1 新增 `_tenant_admin_owns_agent(ctx, agent_id)`（`is_tenant_admin` 且 `agent_id ∈ tenant_agent_ids`）。
- [x] 4.2 `_tenant_agents_projection`：租户管理员跳过 `allowed_agent_ids` 过滤（`visible` 仍限制在本租户绑定内）；成员路径不变。
- [x] 4.3 `_workbench_chat_readiness`：`agent.use` 判定加入同一判据，确保 `can_chat` 与发送路径一致。
- [x] 4.4 `_require_agent_action`：命中判据放行，否则回落原 `_require_resource_action`（跨租户仍 403）。

## 5. 回归与验收

- [x] 5.1 目标套件（`test_tenant_agent_creation`、`test_identity_agent_bindings`、`test_tenant_default_agent`、`test_tenant_read_scoping`、`test_identity_resource_authorization`、`test_agent_workbench`、`test_menu_grant_enforcement`、`test_http_policy`、`test_web_chat_boundary`、`test_tenancy_isolation_acceptance`）全绿（149 passed）。
- [x] 5.2 全量 Python 套件对比既有基线：32 项失败均为既有/环境类（缺 `pypdf`、未安装浏览器引擎、供应商模型清单漂移、`avatar` 字段投影、以及此前已记录的 `test_identity_self_context`/`test_session_history_search`/`test_tenant_create_containment`），无与本 change 相关的回归（2201 passed）。
- [x] 5.3 重启实例并以 `test15`（`test123456`）经浏览器真实操作验收：`POST /api/agents` 返回 200，工作区为 `/Users/jiantan/.cow/tenant-roots/tenants/test15/agents/test15-verify`，控制台列表显示该智能体（`is_default: true`、`can_chat: true`）。
- [x] 5.4 落盘复核：`agent_bindings` 新增 `test15-verify → tnt_EA3qM-lHPLD8ZPwW`（owner = test15 用户），`tenants.default_agent_id = test15-verify`。
- [x] 5.5 清理本轮之前的测试残留（`verify-t15-agent`、`probe-t15-agent`），保留 `test15-1`。
