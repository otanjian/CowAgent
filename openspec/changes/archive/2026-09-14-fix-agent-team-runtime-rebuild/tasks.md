## 1. 回归测试（先行）

- [x] 1.1 新增 `tests/test_session_team_runtime.py`：真实 `AgentBridge` 缓存 + 假构建，观察重建次数
- [x] 1.2 用例：会话创建后邀请同事 -> 运行时被退休并在下一轮重建
- [x] 1.3 用例：移除最后一名成员 -> 运行时按单人重新装配
- [x] 1.4 用例：重复保存同一份名册 -> 不重建，热运行时保留
- [x] 1.5 用例：不含 `members` 的设置写入 -> 不退休任何运行时
- [x] 1.6 核对失败原因正确：临时禁用驱逐逻辑后 3 条用例失败于 `1 == 2`

## 2. 实现

- [x] 2.1 `SessionSettingsHandler.POST` 在写入前读取旧名册，并按集合比较判定名册是否变化
- [x] 2.2 名册变化时驱逐该会话全部参与者（owner + 旧成员 ∪ 新成员）的缓存运行时
- [x] 2.3 名册未变化或写入不含 `members` 时不驱逐
- [x] 2.4 无法解析的成员 id 逐个跳过并记 debug 日志，不影响设置写入成功

## 3. 验证

- [x] 3.1 `tests/test_session_team_runtime.py` 全绿
- [x] 3.2 既有会话/团队相关套件无回归（`test_direct_addressing`、`test_team_addressing`、`test_session_model_scope`、`test_route_registry`）
- [x] 3.3 更广回归（`test_agent_delegation`、`test_multi_agent_runtime`、`test_identity_web_handlers`、`test_session_idor_closure`、`test_session_history_search`、`test_channel_instances`、`test_agent_web_management`）通过
- [x] 3.4 前端套件 `tests/test_composer_agents_frontend.cjs` 通过

## 4. 后续（本 change 不实施）

- [ ] 4.1 另开 change：控制台会话设置请求携带当前智能体，或把团队名册改为按会话智能体寻址，消除非默认智能体会话的名册错位
