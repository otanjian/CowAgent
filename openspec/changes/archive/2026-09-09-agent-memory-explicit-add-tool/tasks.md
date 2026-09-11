## 1. 工具实现（已实现）

- [x] 1.1 新增 `agent/tools/memory/memory_add.py` 的 `MemoryAddTool`（工具名 `memory_add`），接受 `content`（必填）、`scope`（默认 `user`）、`path`（可选）参数。
- [x] 1.2 实现 `content` 去空白与必填校验、`scope` 合法性校验，非法值返回明确错误。
- [x] 1.3 在 `agent/tools/__init__.py` 注册并导出 `MemoryAddTool`，加入 `__all__`。
- [x] 1.4 在 `bridge/agent_initializer.py` 的内存工具集 `memory_tools` 中追加 `MemoryAddTool(memory_manager)`，随内存系统注入。
- [x] 1.5 成功写入时返回含 `scope`（及 `user` 作用域的 `user` 标识）的确认，失败返回错误信息。

## 2. 行为验证

- [ ] 2.1 验证 `content` 缺失或空白时返回失败，不写入记忆。
- [ ] 2.2 验证 `scope` 为 `shared`/`user`/`session` 时分别写入对应作用域，`user` 作用域绑定当前用户。
- [ ] 2.3 验证写入后可通过 `memory_search` 检索到新写入内容。
- [ ] 2.4 验证记忆存储或 embedding 异常时返回明确错误，不影响既有检索能力。

## 3. 测试与交付

- [ ] 3.1 补充 `memory_add` 单元测试，覆盖 `content` 必填/空白、`scope` 校验、各作用域写入与错误兜底。
- [x] 3.2 运行 OpenSpec 严格校验，确认 `agent-memory-explicit-add-tool` 的 spec 各 requirement 均含 SHALL/MUST 与 WHEN/THEN 场景。
- [ ] 3.3 在真实智能体运行中验证 `memory_add` 注入并可用，不回归既有 `memory_search`/`memory_get`。
