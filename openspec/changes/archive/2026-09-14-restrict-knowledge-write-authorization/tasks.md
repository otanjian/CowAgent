## 1. 前置与规范

- [x] 1.1 归档已完成的基础 change：`openspec archive open-tenant-knowledge-console --yes`（物化 `openspec/specs/tenant-knowledge-console/`）与 `openspec archive make-builtin-roles-editable --yes`（使 `business-permission-catalog` 主规范与实现一致，本 change 在其之上改写）
- [x] 1.2 补齐归档留下的占位：把 `openspec/specs/tenant-knowledge-console/spec.md` 的 `Purpose` 从 `TBD ...` 改写为该能力的一句话说明（说明模式与维护责任按智能体解析）
- [x] 1.3 `openspec validate restrict-knowledge-write-authorization --strict` 通过

## 2. 写授权按数据根与归属判定

- [x] 2.1 `channel/web/web_channel.py` 新增 `_knowledge_write_authorized(ctx, agent_id) -> bool`：解析绑定（跨租户 False）→ 平台管理员/本租户 `tenant_admin` 直接放行 → 普通成员仅在「`private_owner_user_id` 等于自己」且「数据根为自有」时为 True
- [x] 2.2 新增数据根归属辅助（复用 `AgentAdminService._shared_knowledge_base()` + `_knowledge_mode_of()`），返回该智能体是否为 `own` 模式；不得复制第二套判定
- [x] 2.3 `_require_knowledge_write(ctx, agent_id)` 改为调用上述判定并抛 `403 forbidden`（`code` 保持 `forbidden`）；`ctx is None` 仍为 legacy 无门禁
- [x] 2.4 `KnowledgeActionHandler`/`KnowledgeImportHandler` 在完成 `_require_tenant_agent_binding` 后传入 resolved `agent_id` 做写授权，并保持既有 `_require_private_owner`、`index.md`/`log.md` 保护与导入限额不变
- [x] 2.5 先写失败测试再实现：`knowledge.write` 不再放行、私有智能体 owner 在 shared 模式下被拒、跨租户 404（RED → GREEN）

## 3. 移除 knowledge.write 与迁移

- [x] 3.1 `auth/policy.py`：从 `PERMISSION_CATALOG` 与 `PERMISSION_METADATA` 删除 `knowledge.write`；从 `TENANT_ADMIN_DEFAULT_PERMISSIONS` 删除该项（`MEMBER_DEFAULT_PERMISSIONS` 本就不含）
- [x] 3.2 `auth/store.py` 新增 `_migration_15`：把每个角色 `permissions_json` 中的 `knowledge.write` 剥离并 `version+1`；只删该 id、幂等、不触碰其它权限；同步 `_migrations` 登记
- [x] 3.3 测试：目录与元数据不含 `knowledge.write`；`tenant_admin` 默认集合不含它；迁移后内置与自定义角色集合中的该 id 被剥离且其它权限不变；`normalize_permissions` 对含该 id 的提交按未知 ID 拒绝
- [x] 3.4 更新受影响的既有断言：`tests/test_builtin_role_editing.py`、`tests/test_identity_web_handlers.py`、`tests/test_identity_policy.py`
- [x] 3.5 `channel/web/route_registry.py` 与 `scripts/route-baseline.txt` 的 knowledge 写注释改为「按数据根与智能体归属授权」，不再提 `knowledge.write`

## 4. 私有智能体默认独立知识库

- [x] 4.1 `agent/personal_assistant.py`：`provision()` 调用 `clone_agent(..., knowledge_mode="own")`（或等价地让新建私有智能体拥有空的 `knowledge/` 目录）
- [x] 4.2 测试（`tests/test_user_personal_agent_provisioning.py` 扩展）：新建私有智能体 `knowledge_mode == "own"`、工作区下存在空 `knowledge/`、来源与租户共享库不被改动、已存在的共享模式私有智能体不被回填

## 5. 写能力投影与前端

- [x] 5.1 `_tenant_agents_admin_projection` 为每个智能体增加 `can_write_knowledge`，取值复用 `_knowledge_write_authorized`；不暴露 owner 标识或宿主路径
- [x] 5.2 `channel/web/static/js/console.js`：`canWriteKnowledge()` 改为按当前所选智能体读 `can_write_knowledge`；字段缺失时回落到「平台管理员或本租户租户管理员」；写入口与 `_knowledgeFileActions` 同门禁
- [x] 5.3 `channel/web/static/js/i18n/core.js`（zh / zh-TW / en）与 `channel/web/chat.html` 静态兜底更新 `knowledge_shared_hint`，标明租户级由租户管理员维护、私有智能体由本人维护
- [x] 5.4 更新 `tests/fixtures/console_i18n_snapshot.json` 三语取值
- [x] 5.5 前端断言（`tests/test_knowledge_console_frontend.cjs`）：租户共享库对成员隐藏新建/文件级写操作；私有自有库对 owner 显示；切换智能体即时更新；`knowledge.write` 不再作为判据

## 6. 验收

- [x] 6.1 后端回归：`python -m pytest tests/test_knowledge_console_database.py tests/test_agent_admin.py tests/test_identity_policy.py tests/test_builtin_role_editing.py tests/test_user_personal_agent_provisioning.py -q` 全绿
- [x] 6.2 前端回归：`node tests/test_knowledge_console_frontend.cjs`、`node tests/test_console_i18n_parity.cjs`
- [x] 6.3 浏览器实测：以普通成员查看租户级共享库（可读、无写入口、直接 POST 得 403）；以其私有智能体（独立库）新建文档成功；以租户管理员对两者均可写
  - 环境：`test15` 租户（共享库 21 pages · 74.0 KB），成员 `RC001` 及其私有智能体 `my-assistant-admin-test15-RC001`（自有空 `knowledge/`，`knowledge_mode=own`），租户管理员 `test15`；控制台 `http://127.0.0.1:9899`（重启后加载本 change 代码）
  - 成员 · 共享库：`/api/knowledge/list` 200（22 pages 含探针）可读；`/api/knowledge/action` 直接 POST 探针文档 → 403；页面 `#knowledge-new-menu` 隐藏（`offsetParent === null`）、`_knowledgeFileActions`/`_knowledgeCategoryActions` 均返回空、`canWriteKnowledge()` 为 false
  - 成员 · 自有库：`#knowledge-new-menu` 可见；界面「新建分类」→「新建文档」创建 `member-own/member-written.md` 成功（状态条「文档已创建」，pages 2→3）
  - 成员 · 非本人私有智能体：以同租户另一成员 `test15-2` 读写 `my-assistant-admin-test15-RC001` 均 403
  - 租户管理员 · 两者均可写：`/api/agents` 投影对其全部 Agent 返回 `can_write_knowledge: true`；共享库与成员私有库 `#knowledge-new-menu` 均可见；对共享库与成员私有库各建文档均 200
  - 投影对照：成员 `RC001` 的 `/api/agents` 中只有 `my-assistant-admin-test15-RC001` 为 `true`，4 个租户级 Agent 均为 `false`；投影未出现 owner 标识或宿主路径
  - 复原：探针文档与分类经 API 删除（`delete_documents`/`delete_category`，索引同步），共享库回到 21 pages / 6 组、私有库回到 0 pages；验收期间为 3 个测试账号临时设置的密码已按 `identity.db.bak-acceptance-6-3-20260914-192036` 还原（复核该密码已 401）
- [x] 6.4 迁移实测：对含 `knowledge.write` 的既有库执行迁移，核对角色集合与 `version`，并确认目录接口不再返回该权限项
  - 证据：以 `identity.db.bak-grant-erpnext-tools-20260914-152856` 的副本执行迁移，3 个持有该 id 的角色（tenant_admin ×2、member ×1）各剥离 1 个 id、`version+1`，其余角色集合与版本不变；重复打开幂等；`PERMISSION_CATALOG`/`PERMISSION_METADATA` 均不再含该 id。
  - 真实库证据：控制台进程以本 change 代码启动后于 19:08:44 对 `identity.db` 应用 `schema_migrations.version=15`；核对全库 6 个角色的 `permissions_json` 均不再含 `knowledge.write`（`knowledge.read` 保留），`/api/permissions` 目录亦不再返回该项。
- [x] 6.5 归档本 change 前核对 `openspec validate --strict` 与 `scripts/route-baseline.txt` 无漂移
  - 证据：`openspec validate restrict-knowledge-write-authorization --strict` 通过；`tests/test_http_policy.py`+`tests/test_route_registry.py`（含 route-baseline 漂移检查）全绿。
