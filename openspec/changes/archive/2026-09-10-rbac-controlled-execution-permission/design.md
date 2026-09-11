## Context

见 `proposal.md - Why`。当前 runtime 执行闸顺序（`agent/protocol/agent_stream.py::_permission_denial`）为：

1. `isolation_decision`：database 模式的租户执行隔离（始终生效）。
2. `check_tool_call(mode, ...)`：legacy 会话/全局权限模式（`agent.effective_permission_mode()`）。
3. `_resource_tool_denial`：database 模式下按角色 `tool.execute` + 资源 grant 授权。
4. `_quota_tool_denial`：配额。

角色执行授权已在真实分发路径生效（`resource-execution-authorization` 已交付），第 2 步因此在 database 模式下是多余且会造成"按角色可用却被模式拦下"的冲突。

前端入口：Web `channel/web/chat.html` 的 `#permission-selector-btn` 与 `console.js::_appendPermissionDeniedHint` 的「调整权限」按钮；Desktop `PermissionSelector.tsx` 与 `MessageSteps.tsx::PermissionDeniedHint`。会话设置经 `/api/sessions/{id}/settings`；全局默认经 `/api/config` 的 `agent_permission_mode`。

## Goals / Non-Goals

**Goals:**

- database 模式下工具执行只由 租户隔离 + 角色 `tool.execute`/资源 grant + 配额 决定。
- 对话界面（Web + Desktop）不再出现自行调整执行权限的入口。
- 被拒提示如实说明拒绝来源。
- 全局默认权限在 database 模式只读并说明由角色控制。
- legacy 单租户行为与配置编辑保持兼容。

**Non-Goals:**

- 不引入"角色 → 权限模式"映射，不给角色新增权限模式属性。
- 不移除 `agent/permission/policy.py`；legacy 安装继续使用。
- 不改动 database Web 运行消费者的开放/关闭边界（另立 change）。
- 不改动既有 `tool.execute` 授权、租户隔离与配额实现。

## Decisions

### 1. 在 database 模式下跳过 legacy 权限模式闸

`_permission_denial` 中把 `check_tool_call` 分支限定为 `not database_mode()`。`database_mode()` 复用 `agent/permission/isolation.py` 的既有判定（`conf().get("identity_mode") == "database"`）。

- 备选 A：给角色新增"权限模式"属性并在后端按角色解析。否决——用户明确选择复用现有 RBAC；`tool.execute` + 资源 grant 已表达"能用哪些工具"，隔离表达"能访问哪些路径"。
- 备选 B：保留模式闸但在 database 模式强制 `full-access`。否决——会隐式改变 legacy 语义且仍留下误导性配置面。

### 2. 拒绝原因区分模式与角色/隔离

`_permission_denial` 返回拒绝时携带来源（mode / role / isolation / quota）。legacy 模式拒绝保留 `permission_mode`；database 模式的角色/隔离/配额拒绝不再把会话模式当作原因，前端提示改用"当前角色未获授权"文案，不提供任何操作按钮。

- 备选：完全去掉被拒提示。否决——用户要求保留文字说明。

### 3. UI 入口无条件移除（Web + Desktop 一致）

即使 legacy 模式仍生效，也移除会话级权限选择器与被拒提示按钮；legacy 用户仍可在平台设置页修改全局默认权限。这样满足"所有对话不出现调整权限按钮"，且两端行为一致。

- 备选：仅 database 模式隐藏。否决——需要前端感知身份模式且留下两套界面分支，收益低。

### 4. 全局默认权限在 database 模式下只读

`/api/config` GET 增加只读标志（如 `permission_mode_source: "role"` / `permission_mode_editable: false`）；database 模式下前端渲染为只读说明，POST 忽略 `agent_permission_mode`。legacy 保持现状。

### 5. 会话设置接口在 database 模式不提供覆盖

`_session_settings_state` 在 database 模式返回 `permission.source = "role"` 且 `modes = []`；POST 忽略 `permission` 覆盖。已有会话 pin 保留在存储中但不再生效，无需迁移。

## Risks / Trade-offs

- [database 模式少了模式这层限制] → 由角色 `tool.execute`/资源 grant 决定可用工具、由 `isolation_decision` 限制路径、由配额限流；未授予 `bash`/`write` 的角色本就无法执行相应工具。测试覆盖"角色未授权即拒绝"。
- [legacy 用户失去会话级快速切换] → 全局默认权限仍可在设置页修改；提示文字说明当前模式。若后续需要，可另立 change 提供管理员侧配置。
- [已有会话权限 pin 变成死数据] → 不删除、不迁移，database 模式忽略即可；回退代码后仍可读。
- [前端 cjs 测试可能断言旧 DOM] → 已确认现有 `tests/*.cjs` 无 permission selector / 调整权限断言；实现后运行相关用例。

## Migration Plan

1. 后端先改（`agent_stream.py` 分模式、config/session settings 语义），保持 legacy 行为不变。
2. 前端移除入口（Web、Desktop）与文案/样式。
3. 无数据库 schema 迁移；无 feature flag。
4. 回退：还原提交即可；已保存的会话权限覆盖未删除，回退后 legacy/database 旧行为恢复。
