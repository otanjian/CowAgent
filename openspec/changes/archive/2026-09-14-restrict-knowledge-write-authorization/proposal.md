## Why

知识库写入目前按「功能权限或管理员资格」粗粒度授权，与该智能体的归属和实际数据根无关：`member` 内置角色没有 `knowledge.write`，私有智能体 owner 又只能 `read`/`use`，于是普通用户**无法维护自己私有智能体的知识库**；反过来，任何被授予 `knowledge.write` 的自定义角色成员都能改写整个租户的共享知识库。同时个人助理克隆时继承模板的 shared 模式，使「私有智能体有自己的知识库」在数据侧也不成立。

## What Changes

- **BREAKING**：`/api/knowledge/action` 与 `/api/knowledge/import` 的写授权由「`knowledge.write` 或管理员资格」改为按**数据根归属 + 智能体归属**判定：
  - 写入落在**租户共享库**（该智能体 shared 模式）→ 仅平台管理员、本租户租户管理员可写；私有 owner 也不例外。
  - 写入落在该智能体**自有 `knowledge/`**（own 模式）→ 平台管理员、本租户租户管理员可写；若为私有智能体，额外允许其 owner 本人写入。
  - 普通成员 MUST NOT 写任何租户级智能体的知识库。
- **BREAKING**：从功能权限目录移除 `knowledge.write`（`PERMISSION_CATALOG`、`PERMISSION_METADATA`、`tenant_admin` 显式默认集合、前后端门禁、路由注释与基线、i18n、测试）。迁移：从所有已存角色的 `permissions_json` 中剥离该 id。
- 私有智能体（个人助理）供应时默认 `knowledge_mode="own"`，获得自己的空知识库，使 owner 有可维护对象且绝不会误写租户共享库；存量私有智能体**不回填**，保持现状。
- Agent 管理投影为每个智能体增加 `can_write_knowledge`；控制台知识库页据此按所选智能体渲染写入口（无写权时隐藏新建/重命名/删除/移动/导入）。
- 知识库页说明文案改为说明维护责任（租户级由租户管理员维护、私有智能体由本人维护），三语一致。
- 读取路径不变：仍为 `knowledge.read` + 私有归属裁剪。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `tenant-knowledge-console`: 写入授权由「权限/管理员资格」改为「数据根与智能体归属」；新增「写能力按智能体投影」需求；页面写入口按所选智能体渲染；说明文案补维护责任。
- `business-permission-catalog`: 权限目录移除 `knowledge.write`，并相应更新目录展示与场景。
- `user-personal-agent-provisioning`: 私有智能体默认使用独立（自有）知识库。

## Impact

- 后端：`channel/web/web_channel.py`（`_require_knowledge_write` 改为按 `agent_id` 判定数据根与归属，新增 `_knowledge_write_authorized`；`_tenant_agents_admin_projection` 增加 `can_write_knowledge`；`KnowledgeActionHandler`/`KnowledgeImportHandler` 传入 `agent_id`）。
- 权限：`auth/policy.py`（`PERMISSION_CATALOG`/`PERMISSION_METADATA`/`TENANT_ADMIN_DEFAULT_PERMISSIONS` 移除 `knowledge.write`）、`auth/store.py`（新增迁移剥离已存角色中的该 id）。
- 供应：`agent/personal_assistant.py`（克隆私有智能体时以 `knowledge_mode="own"` 创建）。
- 前端：`channel/web/static/js/console.js`（`canWriteKnowledge` 改为按所选智能体读 `can_write_knowledge`，文件级动作同门禁）、`channel/web/static/js/i18n/core.js` 与 `channel/web/chat.html`（说明文案三语）、`scripts/route-baseline.txt`（注释）。
- 测试：`tests/test_knowledge_console_database.py`（授权矩阵）、`tests/test_knowledge_console_frontend.cjs`、`tests/test_builtin_role_editing.py`、`tests/test_identity_web_handlers.py`、`tests/test_identity_policy.py`、`tests/test_user_personal_agent_provisioning.py`、`tests/fixtures/console_i18n_snapshot.json`。
- 数据：`identity.db` 中角色权限集合的 id 剥离（一次性迁移）；无 schema 变更，无租户数据回填。
- 依赖：`tenant-knowledge-console`（已归档，本 change 修改其写入与页面需求）、`agent-knowledge-mode`（数据根归属判定口径，本 change 复用不重定义）。
