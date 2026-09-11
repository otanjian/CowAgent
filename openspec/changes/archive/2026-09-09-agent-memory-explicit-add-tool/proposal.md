## Why

`rdai` 分支为智能体新增了 `memory_add` 工具，允许智能体在对话中显式保存事实、经验与上下文到长期记忆。既有记忆能力只有 `memory_search`（检索）与 `memory_get`（读取单条）两个工具，缺少「显式写入」通道——智能体无法在推理中主动记住用户明确要求记住的内容或非显而易见的经验。该功能改动目前未被任何已归档 change 覆盖，需要单独建模。

## What Changes

- 新增 `agent/tools/memory/memory_add.py` 中的 `MemoryAddTool`（工具名 `memory_add`），接受 `content`（必填）、`scope`（`shared`/`user`/`session`，默认 `user`）、`path`（可选相对路径）参数。
- 在 `agent/tools/__init__.py` 注册 `MemoryAddTool` 并导出至 `__all__`。
- 在 `bridge/agent_initializer.py` 的内存工具集（`memory_tools`）中追加 `MemoryAddTool(memory_manager)`，使其随内存系统注入。
- 工具内部对 `content` 去空白并校验必填，校验 `scope` 合法值，调用 `MemoryManager.add_memory()` 落库，成功返回带 `scope`（及 `user`）备注的确认，失败返回工具错误信息。
- 该工具复用既有内存存储与 embedding 管线，不新增存储结构或权限目录。

## Capabilities

### New Capabilities

- `agent-memory-explicit-add-tool`: 新增显式保存长期记忆的工具，允许智能体将事实、经验与上下文按 `shared`/`user`/`session` 作用域写入记忆库，供后续 `memory_search` 检索，作为既有 `memory_search`/`memory_get` 的写入侧补充。

### Modified Capabilities

无。本 change 为新增记忆写入能力，不修改既有 `memory_search`/`memory_get` 或其它任何 spec 的 requirement。

## Impact

- 代码范围：`agent/tools/memory/memory_add.py`（新增）、`agent/tools/__init__.py`（注册）、`bridge/agent_initializer.py`（注入内存工具集）。
- 数据唯一归属：复用既有记忆存储（与 `memory_search`/`memory_get` 同一 MemoryManager 与记忆文件/数据库），不新增存储结构、唯一约束或权限目录。
- 兼容与恢复：新增工具不影响既有 `memory_search`/`memory_get` 行为；工具未注册或依赖缺失时仅新工具不可用，不影响其它功能。
- 跨 change 依赖：依赖既有记忆管理与 embedding 管线（已有 `business-permission-catalog` 等覆盖记忆域）；本 change 不引入新权限。
- 验收：验证 `content` 必填与去空白、`scope` 校验（`shared`/`user`/`session`），校验写入各作用域后可被 `memory_search` 检索到，`user` 作用域绑定当前用户。
