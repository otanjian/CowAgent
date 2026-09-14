## 1. 后端：租户默认任命

- [x] 1.1 在 `channel/web/web_channel.py` 的 `POST /api/agents` 写操作分支新增 `action == "set_default"`：调用既有 `appoint_tenant_default_agent(tenant_id=ctx.tenant_id, agent_id=agent_id, actor_user_id=ctx.user_id)`，成功后返回当前租户的最新 `default_agent_id`，不新增路由条目
- [x] 1.2 保持既有错误语义：非租户管理员/平台管理员 → 403；目标未绑定当前租户 → 拒绝且不改变任何默认；目标已是当前默认 → 幂等成功
- [x] 1.3 确认任命副作用沿用既有实现，不重复实现：清除目标 `private_owner_user_id`、写审计 `tenant.set_default_agent`、只作用于被任命者
- [x] 1.4 在 `auth/service.py` 新增「释放已删除智能体」的服务方法：单事务内把 `tenants.default_agent_id` 等于该 `agent_id` 的行置空、删除 `agent_bindings` 中该 `agent_id` 的行，并写审计；方法只作用于该智能体，不改动其它绑定与其它租户默认。清理按 `agent_id` 定位而**不按调用方租户限定**（`agent_bindings.agent_id` 是主键，但操作者可能是平台管理员、当前租户与绑定所属租户不同）
- [x] 1.5 在 `channel/web/web_channel.py` 的 `action == "delete"` 成功分支调用 1.4，使删除后既无失效默认指针也无残留绑定（残留绑定会让 `_agent_is_usable` 把已删智能体当可用，解析仍返回它）；调用不再以 `ctx.tenant_id` 为前置条件；`agent/admin.py` 保持不引入租户依赖
- [x] 1.6 复核 `resolved_default_agent_id` 无需改动即可在绑定被释放后按「租户共享最小 id → 任意可用绑定」回落，无剩余绑定时按既有失败关闭规则拒绝

## 2. 前端：智能体配置页的「设为默认」

- [x] 2.1 在 `channel/web/static/js/console.js` 的 `renderAgentDetail` 操作区新增「设为默认」，`isDefault` 为真时不显示（与既有删除按钮的隐藏口径一致）
- [x] 2.2 提交 `set_default` 成功后强制重载智能体目录，用服务端 `default_agent_id` 覆盖客户端缓存的 `cow_default_agent`，并给出成功提示；失败给出可读原因（无权限、目标不可用）
- [x] 2.3 在 `channel/web/static/js/i18n/agents.js` 补齐按钮、成功、失败、无权限文案的简体/繁体/英文三语 key
- [x] 2.4 复核配置页徽标与工作台排序继续取自 `/api/agents` 的 `default_agent_id`，不新增客户端自算默认的分支

## 3. 测试

- [x] 3.1 新增 `tests/test_tenant_default_agent_selection.py`：覆盖任命成功并改变解析结果、响应体回带最新 `default_agent_id`、普通成员 403、目标未绑定本租户被拒、重复任命幂等、审计 `tenant.set_default_agent` 落库
- [x] 3.2 同文件覆盖共享转换边界：任命带私有归属的智能体后被转为租户共享、普通成员经 `_require_private_owner` 可到达（含「任命前该成员被 403 拒绝」的负对照）；另一个非默认私有智能体的归属不被波及
- [x] 3.3 同文件覆盖删除：删除当前租户默认后指针被清空且绑定被释放、缺省解析不再返回该已删标识、回落到其它可用绑定、其它租户默认与其它智能体归属不受影响、被删智能体为该租户唯一绑定时解析失败关闭（`resolved_default_agent_id` 为 None 且 `_require_session_owner` 抛 403，并含「仍有绑定时不拒绝」的正对照）
- [x] 3.4 补充前端断言（既有 `.cjs` 用例）：已是默认时不渲染「设为默认」、点击后提交 `set_default` 并在成功后重载目录
- [x] 3.5 运行相关 Python 与 `.cjs` 测试并确认通过

> 证据：`tests/test_tenant_default_agent_selection.py`（13）、`tests/test_tenant_default_agent.py`（既有 `_tenant_agents_projection` 用例）、`tests/test_default_agent_tenant_shared.py`、`tests/test_default_agent_fail_closed.py`、`tests/test_tenant_agent_copy_http.py`、`tests/test_route_registry.py`、`tests/test_agent_web_management.py`、`tests/test_management_share_default_agents.py` 等 11 个文件共 **142 项通过**；`tests/test_tenant_default_agent_frontend.cjs` 4 项通过。
>
> 变异核验（证明用例非空转，非仅「跑绿」）：抽掉 `web_channel.py` 删除分支的 `release_deleted_agent` 调用 → `test_deleting_the_default_releases_the_binding_and_the_pointer` 变红（绑定残留）；让 `set_default` 响应体丢掉 `default_agent_id` → `test_set_default_changes_the_tenant_default_and_the_resolution` 变红（KeyError）。两次变异后文件均按字节还原（`cmp` 校验一致）。
>
> 复核中曾发现两处**空转测试**并已修正：3.2 的成员可达性原先未打桩 `get_identity_service`，`_require_private_owner` 查的是真实库、找不到绑定即静默返回，断言恒过；3.3 原先完全缺失唯一绑定失败关闭的断言。前者靠新增负对照暴露，现已把 patch 作用域覆盖到全部调用点。
>
> **实机使用时发现并修正一处范围缺陷（回归已补测）**：1.4 初版把绑定清理写成 `DELETE FROM agent_bindings WHERE agent_id=? AND tenant_id=?`，即按**调用方当前租户**限定。但 `agent_bindings.agent_id` 是主键——一个智能体只属于一个租户，而操作者不必属于那个租户：平台管理员在一个租户的控制台下删除绑定在另一个租户的智能体时，该语句匹配零行，绑定残留，`_agent_is_usable` 仍把已删智能体当可用，`resolved_default_agent_id` 继续返回它。现改为按 `agent_id` 全量定位（不清空调用方租户之外的范围，只按被删智能体收敛），并新增 `test_release_detaches_an_agent_owned_by_another_tenant`。变异核验：把删除语句改回按调用方租户限定 → 该用例变红（`the binding survived because it belongs to another tenant`），随后按字节还原。
>
> 与本 change 无关的既有失败（改动前即存在，已用 `git stash` 对照确认）：`tests/test_agent_workbench.py::test_readiness_defaults_to_runnable_in_legacy`；`tests/test_console_i18n_parity.cjs` 的 deep-equal 快照（缺 5 个 `config_password*` key、`tasks_unavailable_desc` 文案漂移，漂移明细为 `+0 extra -5 missing ~1 changed`，与改动前逐字节一致）；`.cjs` 全量串跑时的 `test_appearance_browser.cjs` / `test_session_history_frontend.cjs` / `test_sidebar_account_frontend.cjs` 顺序相关失败（单跑通过）。本 change 已把自己新增的 4 个 i18n key 同步进快照，快照剩余漂移不属于本 change。

## 4. 校验与边界确认

- [x] 4.1 `openspec validate add-tenant-default-agent-selection --strict` 通过
- [x] 4.2 确认未改动：模板默认（`registry.default_agent_id`）的模型/知识库/不可删除语义、`agent_bindings` 表结构、平台管理租户编辑页的复制与默认承接流程
- [x] 4.3 确认因未新增路由，`scripts/route-baseline.txt` 无需为本 change 变更
- [x] 4.4 记录已知取舍：客户端仍隐藏默认智能体的删除按钮，UI 上需先改默认再删除（服务端已放行）

> 4.2 说明：删除时新增的是「删除该智能体的绑定**行**」，不是表结构变更；模板默认的不可删除判定（`agent/admin.py`）与会话入口的解析优先级均未改动。`scripts/route-baseline.txt` 在工作区确有改动，但来自并行的头像路由（`/api/users/([^/]+)/avatar`），与本 change 无关。
