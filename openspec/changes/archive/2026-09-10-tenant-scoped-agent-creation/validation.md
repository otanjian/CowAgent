# 验收证据：tenant-scoped-agent-creation

验收日期：2026-09-10。环境：本机实例（launchd `com.cowagent.app`），database 身份模式，`identity.db` 为仓库内实例库。

## 1. 缺陷复现（修复前）

以租户管理员 `test15`（`test123456`）在真实控制台操作：

- 「创建智能体」→ 填表提交后，`POST /api/agents` 返回 200 并创建成功，但控制台仍显示「还没有智能体」。
- 落盘复核：roster `team.json` 出现该智能体，工作区落在**实例根** `/Users/jiantan/cow/agents/<id>`；`agent_bindings` 中租户 `tnt_EA3qM-lHPLD8ZPwW`（test15）**0 行**。
- 根因：写路径不区分租户且不写绑定；读路径 `_tenant_agents_projection` 按 `tenant_agent_ids` 过滤，故新建对象对该租户不可见。

## 2. 单元验收

```
.venv/bin/python -m pytest tests/test_tenant_agent_creation.py -q
9 passed
```

覆盖：租户根工作区 / 不落实例根 / 绑定 / 列表可见 / 首个默认 / 后续不改默认 / 非法 id 不落盘 / 本租户可编辑 / 跨租户不可见且编辑 403。

目标回归套件（149 项）全绿：

```
.venv/bin/python -m pytest tests/test_tenant_agent_creation.py \
  tests/test_identity_agent_bindings.py tests/test_tenant_default_agent.py \
  tests/test_tenant_read_scoping.py tests/test_identity_resource_authorization.py \
  tests/test_agent_workbench.py tests/test_menu_grant_enforcement.py \
  tests/test_http_policy.py tests/test_web_chat_boundary.py \
  tests/test_tenancy_isolation_acceptance.py -q
149 passed
```

全量 Python 套件：`2201 passed, 32 failed`。32 项失败均为既有/环境类，与本 change 无关：
缺 `pypdf`（`test_read_edit_improvements`）、未安装浏览器引擎（`test_security_ssrf_browser_navigate` 12 项，报错为 `Browser tool not ready`）、供应商模型清单漂移（`test_dashscope_provider`、`test_claude_thinking`）、`avatar` 字段投影（`test_identity_self_context`）、以及此前已记录的 `test_session_history_search` / `test_tenant_create_containment` / `test_feishu_progress_card` / `test_todo_service` / `test_consumer_closure_acceptance`。

## 3. 浏览器端到端验收（修复后）

重启实例后，以 `test15` 在真实控制台经 UI 路径（侧栏「智能体管理」→「创建智能体」→ 填表 → 提交）操作，插桩记录页面自身的网络调用：

```
POST /api/agents  -> 200
resp: {"status":"success","result":{
        "id":"test15-verify",
        "workspace":"/Users/jiantan/.cow/tenant-roots/tenants/test15/agents/test15-verify",
        ...},"revision":"01457a6a..."}

GET  /api/agents  -> 200
resp: {"status":"success","agents":[
        {"id":"test15-verify","name":"test15验证智能体","description":"浏览器端到端验证",
         "avatar":null,"is_default":true,"can_chat":true,"unavailable_reason":null}]}
```

- 页面正确渲染该智能体并显示「默认」标记，详情编辑器可打开核心文件；页面残留的 `加载失败，请重试` 属于隐藏的陈旧 toast（`#ws-sel-toast`，`offsetParent === null`），非活动错误。
- 断网/刷新后重新加载，列表仍显示该智能体（绑定与默认已持久化）。

落盘复核：

```
agent_bindings:
  test15-verify -> tenant tnt_EA3qM-lHPLD8ZPwW, private_owner_user_id usr_9ZxVPz7M2FuOro1q
tenants:
  test15 -> default_agent_id = test15-verify
tenant root:
  /Users/jiantan/.cow/tenant-roots/tenants/test15/agents/test15-verify/{AGENT.md,USER.md,RULE.md,BOOTSTRAP.md,MEMORY.md,memory/,scheduler/}
```

## 4. 清理

删除本轮之前的测试残留智能体 `verify-t15-agent`（roster + 工作区）与 `probe-t15-agent`（残留目录），按用户要求保留 `test15-1`。二者在 `agent_bindings` 中从无绑定行，无悬挂引用。

浏览器验收产物 `test15-verify` 保留在 test15 租户下，作为该租户当前唯一的默认智能体（如需移除可随时删除）。

## 5. 未覆盖与后续

- 不回溯迁移历史「已创建但未绑定」的存量智能体（见 design 的 Migration Plan）。
- 未做平台侧的租户智能体只读审计视图（design 的 Open Questions）。
- 前端无改动，故未重跑 cjs 前端用例；已知 `test_session_history_frontend.cjs` 存在与本 change 无关的既有不稳定。
