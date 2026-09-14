## 1. 身份域：个人默认登记（迁移与读写）

- [x] 1.1 新增迁移 14：`ALTER TABLE memberships ADD COLUMN default_agent_id TEXT`（可空、无回填），并追加进 `_migrations`；确认在既有 `identity.db` 上幂等重跑不报错
- [x] 1.2 在 `IdentityService` 新增 `member_default_agent_id(tenant_id, user_id)`（只读）与 `set_member_default_agent(tenant_id, user_id, agent_id)`（幂等、不 bump `memberships.version`、写审计、校验目标已绑定本租户；可到达性交给解析层，避免两处判定漂移）
- [x] 1.3 `release_deleted_agent` 同一事务内清空 `memberships.default_agent_id` 等于被删 agent 的行，并把清理数量计入既有审计的 `redacted_changes`
- [x] 1.4 确认 `update_member` / 租户编辑器批量提交不因新列改变响应与 version 语义

## 2. 身份域：缺省解析的用户级优先层

- [x] 2.1 `resolved_default_agent_id(tenant_id, user_id=None)` 增加第一层：读该用户在**本租户**的登记，须仍为本租户可用绑定；`user_id` 为空时行为与现状逐字节一致
- [x] 2.2 该层加入可到达性判定：绑定 `private_owner_user_id` 为空（租户共享）或等于 `user_id` 才生效；否则跳过并告警，MUST NOT 返回**他人**私有智能体
- [x] 2.3 失效（解绑/停用/跨租户/不可达）只跳过、不清理、不拒绝；保持解析路径只读
- [x] 2.4 Web 层 `_resolve_tenant_default_agent` / `_tenant_default_agent_id` 透传 `ctx.user_id`，复核 6 处调用点（`_require_tenant_agent_binding`、`_require_session_owner`、工作台投影、资产解析等）语义一致

## 3. 智能体域：个人助理编排

- [x] 3.1 `config.py` 新增配置项：来源名称（默认「智能办公助理」）、来源显式 id、源所属人别名表（默认 `["admin"]`），全部有内置默认值
- [x] 3.2 新增 `agent/personal_assistant.py`：`resolve_source(tenant_id)` 按「显式 id → 名称」在**本租户已绑定**的 roster 中解析启用来源，多个取稳定标识最小；无来源或唯一来源停用时返回可诊断的跳过原因
- [x] 3.3 同模块实现 id/工作区规划：id 派生自来源 id + username（净化、≤64、冲突加 `-2/-3`），工作区固定在 `<tenant_shared_root>/agents/<新 id>`；复用 `TenantAgentProvisioner` 已验证的规划口径
- [x] 3.4 同模块实现创建流水线：`clone_agent` → 人设个人化 → `bind_agent(private_owner_user_id=新用户)` → `set_member_default_agent` → 审计；**不写** `cloned_from_agent_id`（偏唯一索引会把同租户第二个用户挡成 409）
- [x] 3.5 同模块实现幂等与补偿：该用户在本租户已有私属绑定时直接跳过；任一步失败则删除本次产生的 roster 条目与工作区，返回失败明细，MUST NOT 上抛异常给成员创建
- [x] 3.6 同模块实现审计：成功/跳过/失败三态各写脱敏事件（用户、租户、个人助理标识、来源标识、原因），不含任何凭据明文

## 4. 智能体域：人设个人化原语

- [x] 4.1 `PersonalAssistantProvisioner._personalise` 把 `USER.md` 覆写为新成员资料（显示名/用户名/岗位/部门）
- [x] 4.2 同一方法按别名表在 `AGENT.md`/`RULE.md`/`BOOTSTRAP.md` 与描述类字段（`description`/`persona_summary`/`greeting`/`position`）做整词替换为新成员显示名，只改克隆件；别名未命中保持原文，不猜测
- [x] 4.3 确认 `clone_agent` 的 `USER.md` 播种被个人化步骤稳定覆盖（顺序固定：克隆 → 个人化），并在注释中说明原因

## 5. 接线：成员创建触发

- [x] 5.1 `IdentityService.create_member` 在身份事务提交后调用注入式编排钩子（默认 lazy import），把结果放进返回值 `personal_agent`；`create-new` 与 `bind-existing` 都触发
- [x] 5.2 编排异常/跳过 MUST NOT 改变成员创建的成功状态；成员创建原本会失败的情形（重名、非管理员、非法角色）保持原样，且不得触发智能体创建
- [x] 5.3 `channel/web/admin_handlers.py` 的 `TenantMembersHandler.POST` 经 `{"member": result}` 原样回带 `personal_agent`（状态 + 个人智能体 id 或原因）；未新增路由，`scripts/route-baseline.txt` 不变（`test_route_registry.py` 通过）
- [x] 5.4 前端确认新增字段不破坏既有渲染（全仓库 `.js/.cjs/.html` 无 `personal_agent` 引用，纯增量字段）；本 change 不新增 UI

## 6. 测试与验收

- [x] 6.1 新增 `tests/test_user_personal_agent_provisioning.py`，先 RED 后 GREEN，覆盖：来源按名称解析、配置显式 id 覆盖、来源停用/缺失跳过且成员创建成功、同租户第二个用户独立成功（不撞偏唯一索引）、幂等（已有私属不重复建）、失败补偿后重试可续跑、人设 `USER.md` 归属本人、别名替换只改克隆件且源不变
- [x] 6.2 同文件覆盖权限边界：所有者本人可读可用、同租户普通成员被拒、本租户管理员可读、跨租户被拒、租户默认指针不变
- [x] 6.3 同文件覆盖解析优先级：个人默认优先于租户默认且只对该用户生效、他人私有不被锚定、失效（解绑/停用/不可达）回落且请求成功、删除个人助理后登记被清且不再返回该标识、`user_id=None` 行为与现状一致
- [x] 6.4 覆盖审计三态落库（`member.personal_agent.*`）且不含凭据明文
- [x] 6.5 复核既有测试不回归：`test_tenant_default_agent.py`、`test_default_agent_tenant_shared.py`、`test_default_agent_fail_closed.py`、`test_comprehensive_chat_entry.py`、`test_tenant_agent_copy_*.py`、`test_identity_agent_bindings.py`、`test_agent_admin.py`、`test_agent_web_management.py`、`test_route_registry.py`、`test_management_share_default_agents.py`、`test_tenant_default_agent_selection.py` —— 连同本 change 新测共 **195 passed**
- [x] 6.6 变异核验（证明用例非空转）：
  - 抽掉解析的用户级层（`if False and user_id`）→ `test_the_personal_assistant_wins_for_its_owner_only`、`test_another_users_private_assistant_is_never_anchored` 变红；
  - 去掉可到达性判定（`if True:`）→ `test_another_users_private_assistant_is_never_anchored` 变红；
  - 抽掉补偿（移除 `self._compensate(agent_id)`）→ `test_a_failure_is_compensated_and_reported` 变红；
  - 三次改动均以 `cp` 原文件按字节还原（SHA-256 复核一致），还原后 38 项全绿
- [x] 6.7 迁移核验：在真实 `identity.db` 的**副本**上回退到 13 条迁移后执行升级，确认：升级到 14 条、成员行与租户默认逐字节不变、存量成员 `default_agent_id` 全为 NULL（无回填）、连续重跑幂等

## 7. 校验与文档

- [x] 7.1 `openspec validate add-user-personal-agent-provisioning --strict` 通过
- [x] 7.2 确认未改动：`agent_bindings` 表结构与偏唯一索引 `idx_agent_bindings_clone_source`、`cloned_from_agent_id` 语义、租户默认的共享转换规则、平台管理员创建租户管理员的路径、`make_agent_tenant_shared` 语义
- [x] 7.3 已记录已知取舍（见 `design.md`「Risks / Trade-offs」）：同租户共享来源与个人助理同名导致列表出现两份「智能办公助理」、无 UI 编辑个人默认、无存量回填、别名替换的整词边界；「未决实施参数」一节已改写为已确认参数
