# Tasks

## 1. 归属判据提取（复用既有守卫）

- [x] 1.1 在 `bridge/agent_bridge.py` 提取 `_session_speaker_user_id(agent, session_id) -> Optional[str]`：封装 `current_user_id()` 与 `store.get_session_owner()` 的归属判定，归属他人或不可验证返回 `None`。
- [x] 1.2 让 `_apply_user_persona_context` 改用该助手，保持既有行为不变（含「无档案不注入」）。

## 2. 身份段注入（TDD：先红后绿）

- [x] 2.1 在 `tests/test_user_persona_injection.py` 先加失败用例：归属本人的会话注入账号与显示名；无个人人设档案仍注入；他人会话不注入；无 `user_id` 不注入；成员关系失效不注入（失败关闭）；身份段不改变任何权限判定。
- [x] 2.2 运行用例确认全部因「未实现」失败，记录失败输出。
- [x] 2.3 实现 `_apply_user_identity_context(agent, session_id)`：经 `member_context` 解析 `username` / `display_name` / 成员显示名，追加 `## 👤 当前用户身份` 段到 `extra_system_suffix`（场景 → 员工 → 个人 → 身份顺序稳定）。
- [x] 2.4 解析链任一环失败（无 `user_id`/无 `tenant_id`/非有效成员/身份服务异常）时静默跳过并记 `logger.debug`，不注入空段、不阻断会话构建。
- [x] 2.5 段内加入「仅用于如实记录，不改变任何权限判定」说明。

## 3. 接入会话构建调用点

- [x] 3.1 在 `_apply_user_persona_context` 的调用处旁同步调用 `_apply_user_identity_context`，确认每次 agent 构建均执行（含新会话与已归属会话）。
- [x] 3.2 确认 legacy（无 `user_id`）与共享/不可验证归属会话的行为与改动前逐字一致。

## 4. 测试与回归

- [x] 4.1 通过：`python -m pytest tests/test_user_persona_injection.py -q`。
- [x] 4.2 回归：`python -m pytest tests/test_scene_activation.py tests/test_subagent.py -q`（同用 `extra_system_suffix` 的相邻路径）。
- [x] 4.3 回归：身份与授权相关既有用例（`tests/` 中覆盖 `/auth/me`、资源授权、会话归属的用例）不受影响。

## 5. 消费方落地（数据侧，验证本 change 的目标）

- [x] 5.1 更新租户 `test15` 的「BUG 管家」`RULE.md`：`提出人` 取注入的账号；无身份段时回落为「本会话用户」，仍不追问。
- [x] 5.2 端到端验证：以 `test15` 登录，在「BUG 管家」下发一句缺陷描述，确认台账「提出人」落为该真实账号。

## 6. 校验

- [x] 6.1 `openspec validate add-current-user-identity-context --strict` 通过。
- [x] 6.2 复核本 change 未扩大任何权限面：身份段不进入授权判定，逐请求授权仍由既有身份服务执行。
