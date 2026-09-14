## 1. 读取范围收敛（核心）

- [x] 1.1 先写失败测试：断言平台管理员在选中租户 A 时，`_tenant_ids_for_context(ctx)` 只返回绑定 A 的智能体；未选租户时返回空列表。
- [x] 1.2 修改 `channel/web/web_channel.py` 的 `_tenant_ids_for_context`：删除 `ctx.is_platform_admin` 的全量名单分支，database 模式一律返回 `tenant_agent_ids(ctx.tenant_id)`，未选租户返回 `[]`，`ctx is None` 保持不设限；同步更新函数 docstring。
- [x] 1.3 断言工作台投影（`_tenant_agents_projection`）与配置页投影（`_tenant_agents_admin_projection`）在平台管理员上下文下只含当前租户智能体，且不出现其它租户的 `is_default` 标识。

## 2. 卡片与发送判定一致

- [x] 2.1 先写失败测试：平台管理员上下文 + 绑定到其它租户的智能体，`_workbench_chat_readiness` 返回 `(False, "permission_denied")`。
- [x] 2.2 在 `_workbench_chat_readiness` 中前置"目标 Agent 必须绑定调用者租户"（复用 `get_identity_service().get_agent_binding`），失败返回既有稳定原因码 `permission_denied`；不新增面向用户的新文案。
- [x] 2.3 断言判定顺序：绑定校验先于 `agent.use` 与 `chat.use`，且对当前租户绑定的智能体不改变既有结果（含租户共享默认的放宽路径与租户管理员路径）。

## 3. 既有测试对齐新口径

- [x] 3.1 改写 `tests/test_tenant_default_agent.py::test_platform_admin_sees_all_agents_across_tenants`：改为断言平台管理员在选中租户下只看到该租户绑定，并更名以反映新语义。
- [x] 3.2 改写 `tests/test_tenant_default_agent.py::test_platform_admin_without_tenant_sees_all_agents`：改为断言未选租户时可见集为空。
- [x] 3.3 复核 `tests/test_tenant_agent_copy_acceptance.py`、`tests/test_session_idor_closure.py`、`tests/test_agent_workbench.py` 中依赖 `_tenant_ids_for_context` 或就绪判定的用例，确认无残留旧口径假设；如受影响按新语义修正并在任务中记录原因。
- [x] 3.4 记录 `tests/test_agent_workbench.py` 中 3 条本次改动前即失败的用例（stub 签名与 legacy 预期漂移），确认其失败原因与本次收敛无关，不将其计入本次回归结论。

## 4. 回归与验证

- [x] 4.1 运行 `tests/test_tenant_default_agent.py`、`tests/test_tenant_agent_creation.py`、`tests/test_session_idor_closure.py`、`tests/test_agent_workbench.py` 并记录结果。
- [x] 4.2 运行涉及会话读取与历史检索的既有用例（`tests/test_history_*`、`tests/test_tenant_agent_copy_acceptance.py`），确认收敛未引入回归。
- [x] 4.3 复现原始场景验收：以 `admin`（平台管理员 + 默认租户 tenant_admin）身份请求 `/api/agents?view=workbench`，确认不再返回 `*-test15` 三个智能体；并确认 `*-test15` 智能体在平台入口 `/api/platform/tenants/<test15>/agents` 仍可读取。
- [x] 4.4 运行 `openspec validate scope-console-reads-to-selected-tenant --strict` 并在实现完成后归档前复核 spec 与实现一致。

## 5. 档案化（归档前）

- [x] 5.1 确认 `agent-workbench` 与 `tenant-resource-isolation` 的 delta 已随实现生效，且未引入新的未决参数。
- [x] 5.2 若实现过程中发现 spec 与实际可达能力不符，先回到本 change 修改 delta，再继续实现，不以实现代替规范。
