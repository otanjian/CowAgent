## Context

`rdai` 分支为智能体新增了 `memory_add` 工具，作为既有 `memory_search`/`memory_get` 的写入侧补充。既有记忆管理（`agent/memory/manager.py` 的 `MemoryManager.add_memory()`）已支持按 `shared`/`user`/`session` 作用域写入、基于 hash 生成记忆路径、分块与 embedding。本次仅新增一个直接封装 `add_memory()` 的工具，不改变存储结构。

本 change 代码已在 `rdai` 分支实现，本 design 记录实际实现范围与取舍，不声明未实现能力。

## Goals / Non-Goals

**Goals:**
- 提供 `memory_add` 工具，让智能体显式保存事实/经验/上下文到长期记忆。
- 支持 `content`（必填）、`scope`（`shared`/`user`/`session`）、`path`（可选）参数。
- 复用既有记忆存储与 embedding 管线，使写入内容可被 `memory_search` 检索。

**Non-Goals:**
- 不新增记忆存储结构、表或权限目录。
- 不引入新的权限 ID 或作用域之外的数据隔离机制。
- 不改变既有 `memory_search`/`memory_get` 行为。

## Decisions

### 决策 1：直接封装 MemoryManager.add_memory
`MemoryAddTool.execute()` 对 `content` 去空白并校验必填，对 `scope` 校验在 `shared`/`user`/`session` 内，`scope=user` 时传入 `user_id`，否则传 `None`，最终调用 `add_memory(content, user_id, scope, source="memory", path)`。这与既有 `memory_search`/`memory_get` 复用同一管理和存储管线，避免复制逻辑。

### 决策 2：scope 的 user 语义
`scope=user` 绑定时把工具持有的 `user_id` 传入 `add_memory`；`scope=shared`/`session` 时不传 `user_id`。这与 `MemoryManager.add_memory()` 的参数约定一致（`user_id and scope == "user"` 时生成用户作用域路径）。

### 决策 3：资源注册与注入
在 `agent/tools/__init__.py` 注册并导出 `MemoryAddTool`（进入 `__all__`），在 `bridge/agent_initializer.py` 的内存工具集 `memory_tools` 中追加 `MemoryAddTool(memory_manager)`。`MemoryAddTool` 是常驻工具（非可选依赖），在内存配置可用时注入。

### 决策 4：同步执行模型
工具内用 `asyncio.run(...)` 调用异步 `add_memory`，与既有工具的模型保持一致（`memory_search`/`memory_get` 同机制），不新增异步框架或运行上下文。

## Risks / Trade-offs

- **[`asyncio.run` 在已有事件循环中调用]** → 若工具在已有运行事件循环内被调用可能冲突；与既有记忆工具一致，本期沿用相同模型，后续如出现事件循环冲突再统一处理。
- **[scope=user 但工具无 user_id]** → 记为 `user` 作用域但无具体 `user_id`，按现有语义处理，不伪造标识。
- **[写入内容可能含敏感信息]** → 记忆内容按既有记忆库处理，本工具不新增脱敏，遵循既有记忆安全语义。
