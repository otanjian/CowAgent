## Context

系统提示由 `bridge/agent_bridge.py` 分三层叠加为 `agent.extra_system_suffix`，顺序为场景 → 员工 → 个人：

| 层 | 接入点 | 现有行为 |
| --- | --- | --- |
| 场景 | `_apply_scene_context` | 场景激活时写入 `system_prompt` |
| 员工 | `_apply_employee_context` | 追加员工人设摘要/问候策略 |
| 个人 | `_apply_user_persona_context` | 追加 `## 🪞 该用户的个人偏好`，**以会话归属为守卫**，无档案则不注入 |

`_apply_user_persona_context` 已经具备本 change 需要的两件事：**已验证运行时身份**（`current_user_id()`）与**会话归属校验**（`store.get_session_owner(session_id)`，归属他人即返回）。它缺的只是「身份」这一类内容——而且因为它在无个人人设档案时提前返回，身份不能挂在它内部。

另有一条未启用的路径：`agent/prompt/builder.py` 的 `build(user_identity=...)` 与 `_build_user_identity_section` 已实现但全仓库无调用点。本 change 不启用它（理由见「取舍」）。

## Goals / Non-Goals

**Goals**

- 让运行中的智能体在归属单个用户的会话里知道**当前登录用户是谁**（账号为准），使「提出人」等事实字段可自动如实记录。
- 不扩大授权面：身份段是事实描述，不是凭据。
- 不引入跨用户泄漏：沿用既有归属守卫，宁可缺失也不误指。

**Non-Goals**

- 不实现 `/auth/me` 或任何新的 HTTP 身份接口。
- 不实现「智能体主动查询用户目录」的工具。
- 不做账号↔姓名映射文件的落盘副本。
- 不改变个人人设、个人记忆、场景、员工上下文的既有语义与顺序。

## Decisions

### D1：在 `_apply_user_identity_context` 追加独立身份段，而非启用 `builder.user_identity`

选择在 bridge 层新增一个与个人人设并列的方法，追加到 `extra_system_suffix`：

```
## 👤 当前用户身份
- 账号: test15
- 显示名: test15管理员
- 成员显示名: test15管理员
```

理由：

1. **归属守卫已经在 bridge 层**。`user_identity` 结构化段在 `get_full_system_prompt()` 里填充，那里拿不到 `session_id`，更拿不到会话归属；要在那一层做对，必须把会话归属一路下钻到 prompt builder，侵入既有公共签名。
2. **无个人人设的用户也必须拿到身份**。`_apply_user_persona_context` 在档案缺失时提前返回，身份若挂在其中会被一并跳过，因此必须是独立方法。
3. **顺序与缓存天然正确**。`extra_system_suffix` 的 agent 实例按 `(agent_id, session_id)` 缓存，而会话归属单一用户，所以身份段不会在用户间串味；追加顺序固定为场景 → 员工 → 个人 → 身份。

取舍：`builder.py` 的结构化 `user_identity` 段继续闲置。若日后需要结构化段，应以本要求为契约把两层合并，而不是两处各写一份。

### D2：身份解析走 `member_context`，失败即不注入

解析链：`current_identity()` → 必须有 `user_id` + `tenant_id` → `auth.runtime.member_context(svc, user_id, tenant_id)` → 取 `username` / `display_name` / `membership.display_name`。

`member_context` 会在每次构建时重新校验「用户有效 + 租户有效 + 成员关系有效」，符合本 capability「无用户身份时不得放行」的失败关闭取向：成员被移除后下一轮就不再注入。

拒绝的替代方案：

- 直接 `svc._find_user_by_id()`：绕过租户成员校验，会把已退出该租户的用户身份注入进来。
- 从 `msg.from_user_nickname` / 会话消息取：web 通道未赋值，且属于未校验输入。
- 读取 `identity.db`：越权、脆弱、与隔离模型冲突。

### D3：归属判据提取为共享助手，两处复用

把 `_apply_user_persona_context` 里的归属校验提取为 `_session_speaker_user_id(agent, session_id) -> Optional[str]`（返回可注入的用户 id，或 None），个人人设与身份两处调用同一函数。这样「什么算可归属」只有一处定义，避免两个注入点对共享会话给出不一致的答案。

### D4：无 `user_id` / 解析失败时静默跳过并记日志

与个人人设注入一致：不注入空段、不阻断构建。legacy 单机部署（无 `user_id`）行为与现状完全一致。

### D5：身份段明确标注「不改变权限」

段内追加一行说明（如「以下身份仅用于如实记录，不改变任何权限判定」），降低模型把它当作授权依据、或在产出中据此替用户做决策的风险。

## Risks / Trade-offs

- **提示词随用户变化**：同一智能体的系统提示不再对所有用户逐字相同，会降低跨用户的前缀缓存命中。影响面可接受：会话本就按用户归属，agent 实例已按 `(agent_id, session_id)` 缓存。
- **账号 ≠ 人名**：`username`（如 `test15`）可能不如显示名友好。故同时注入显示名，消费方（如 BUG 管家）以账号为准、显示名作补充。
- **共享/团队会话不注入**：这是有意行为（无法归因就不猜），代价是共享会话里「提出人」仍需口头补充。
- **提示词注入面**：显示名是可控文本。它只进入提示词、不进入授权，且来源是身份库而非用户输入，风险与既有人设/记忆注入同级。

## Migration Plan

无 schema 迁移、无数据迁移、无 feature flag。改动随进程重启生效；`extra_system_suffix` 每次会话构建重算，成员关系变化在下一轮即反映。回滚即还原 bridge 改动，无残留状态。

## Open Questions

- 后续是否需要把「岗位」「部门」也纳入身份段？本 change 只做账号与显示名（YAGNI）；若确认需要，应在同一要求内扩展字段清单。
