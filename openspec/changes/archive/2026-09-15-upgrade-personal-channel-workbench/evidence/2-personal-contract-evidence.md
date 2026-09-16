# 阶段 2：个人服务契约与目标边界

本文件记录 `tasks.md` 2.1–2.6 的落地位置与可复跑证据。结论来自当前工作区的实际改动与命令输出。

## 2.1 共用的个人目标判定

`auth/service.py`：

- 新增 `_require_personal_instance_agent`：一次判定「非空 → 同租户 → 本人私有 → 注册表内存在且启用 → owner 持有 `agent.use`」，
  分别以 `personal_agent_required` / `personal_agent_forbidden` / `personal_agent_disabled` 拒绝，全部在写入前抛错。
- 新增 `_agent_enabled`：从 Agent 注册表读启用状态，使「目标存在且启用」不是只看绑定表。
- `_require_instance_agent` 改为对空目标与跨租户绑定早退，个人实例的目标判定统一走上面这个谓词；公共实例仍用 `_require_public_instance_agent`。
- 调用点：`create_tenant_channel_instance`、`update_tenant_channel_instance`、`set_tenant_channel_instance_active`，
  即底层 `scope='user'` 创建/更新/启用与凭据轮换路径，而非只在个人包装层。

## 2.2 写事务内复检与越权测试

- 新增 `_member_active_in_tx`、`_personal_target_owned_in_tx`、`_require_personal_owner_in_tx`、`_require_personal_target_in_tx`：
  在写事务内复检成员活跃、实例归属与目标可用，覆盖「读取后被移交/停用」的竞态。
- `tests/test_personal_channel_console.py::WriteBoundaryTargetTests` 覆盖空、公共、他人、跨租户、不存在、停用目标；
  跨租户与不存在目标只断言 403 与「不泄露对方信息」，不依赖内部错误码。
- `tests/test_personal_channel_console.py::TargetStatusAndRepairTests` 覆盖目标失效后的启用/绑定/修复动作与运行态投影。

## 2.3 个人列表与工作台投影

- `_personal_channel_projection` 增加 `target` / `runtime` 投影，并按 `target_ok` 动态决定 `actions`（`enable` / `bind` / `repair_target`）。
- 新增 `personal_channel_workspace`（聚合 `agent_options`、按租户策略收窄的 `channel_types`、`quota`、`create_unavailable_reason`），
  辅以 `_personal_types_for`、`_personal_type_runtime_open`、`_personal_quota_projection`、`_create_unavailable_reason`、`_personal_agent_label`。
- `channel/web/web_channel.py` 的 `PersonalChannelHandler.GET` 改为返回 `personal_channel_workspace`。
- `tests/test_personal_channel_console.py::WorkbenchProjectionTests` 覆盖候选只含本人私有目标、停用目标仍可见、
  创建裁决、租户策略收窄与配额投影。

## 2.4 关联/解绑的版本与 owner 条件

- 新增 `_claim_personal_instance_version`，作为 `start_personal_channel_binding` / `unlink_personal_channel_instance` 的公共前置：
  个人实例上校验当前 owner 并比对 `expected_version`（0 表示未声明版本），版本不符抛 `conflict`/409；
  共享实例上的个人路由（成员只是路由参与者，不是实例 owner）直接放行，不把租户的实例行变成成员的竞态对象。
- `start_personal_channel_binding` 在铸码前追加目标可用性复检，避免为不可运行的目标发放一次性码。
- 解绑只删本人路由，不动实例目标（spec：「解除关联 SHALL 移除本人消息身份路由而不清空目标智能体」）。
- `tests/test_personal_channel_inbound.py::test_a_shared_route_is_per_member` 等共享实例路由用例证明上述分支未破坏共享路径。

## 2.5 启动/入站复检与脱敏状态投影

- 新增 `personal_target_state`（不抛错的公开形态）与 `check_personal_channel_target`（扫码发起前的预校验）。
- `channel/channel_instances.py` 新增 `_personal_target_usable`，接入 `apply_tenant_instance_runtime` 与
  `load_tenant_channel_instances`：目标不可用的个人实例不启动，运行态如实报 `applied=False` 而非静默成功。
- `_personal_runtime_projection` 分开表达 `saved` / `enabled` / `connected` / `talkable`，来源是配置、观测运行态、目标可用性与关联状态。

## 2.6 测试证据

```
.venv/bin/python -m pytest tests/test_personal_channel_console.py tests/test_personal_channel_inbound.py \
  tests/test_personal_console_acceptance.py tests/test_personal_console_multi_tenant_authorization.py \
  tests/test_personal_instance_policy.py tests/test_tenant_channel_instances_service.py -q
→ 250 passed, 5 subtests passed

.venv/bin/python -m pytest tests/test_identity_credential.py tests/test_tenant_channel_required_credentials.py \
  tests/test_tenant_channel_credential_landing.py tests/test_weixin_credentials_path.py -q
→ 42 passed
```

夹具修正（保留公共路径正向对照）：

- `tests/_helpers.py` 新增 `personal_target_roster`、`install_personal_target_roster`、`personal_channel_target`。
- `tests/test_personal_instance_policy.py`：新增 AGENT 名册夹具，`_create` 按 scope 推导默认目标
  （个人 → owner 的私有 Agent，公共 → 共享 Agent），原「默认用公共目标建个人实例」的写法被移除。
- `tests/test_personal_channel_inbound.py`：夹具显式声明 `agents` 名册，使「目标存在且启用」成为夹具属性而非开发者本机状态的函数。
- `tests/test_personal_console_acceptance.py` / `tests/test_personal_console_multi_tenant_authorization.py` /
  `tests/test_tenant_channel_instances_service.py`：个人实例一律显式传入私有目标。

## 阶段 4 的代码级前置（未勾选，待专项测试）

按 1.2 记录的缺口，`auth/scan_authorization.py` 与 `channel/web/web_channel.py` 已落地以下代码级改动，
但 4.1–4.5 的专项验证尚未执行，因此 `tasks.md` 中第 4 阶段保持未勾选：

- `mint` / `verify` / `consume` 增加 `auth_session_id`、`scope`、`purpose`、`agent_id` 绑定维度；
- `FeishuRegisterHandler` 增加 `_start_scope` 预校验，`_create_session` / `_session_for` / `_poll_payload` 纳入登录会话与 scope，
  避免同一用户的公共扫码取消其个人扫码；
- `create_personal_channel_instance` 与底层 `create_tenant_channel_instance` 接通 `scan_ticket` 与 `auth_session_id`。
