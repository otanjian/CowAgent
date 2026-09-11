## Why

CowAgent 在 `identity_mode=database`（数据库多租户）模式下，把系统配置（`/config`）和模型配置（`/api/models`）路由在 `auth/http_policy.py` 中登记为 `closed`（deferred），导致平台管理员无法在 Web 控制台打开"模型配置"与"基础配置"页面，页面返回 `503 database_unavailable`。这两类配置底层是全局单例（`config.json`），属于平台范围；本次把这两类控制台从 `closed` 调整为 `platform`，使平台管理员在保留 database 多租户模式的前提下可以管理全局配置，同时保持租户管理员与普通成员无权访问的边界。

## What Changes

- 将 `auth/http_policy.py` 中 `/config` 与 `/api/models` 的 GET/POST 从 `policy: closed` 调整为 `policy: platform`，使其在 database 模式下不再被 `enforce_http_policy` 短路成 503，而是进入 handler 做平台管理员授权。
- 在 `channel/web/web_channel.py` 新增 `_require_platform_console()` 守卫：database 模式解析当前请求上下文并要求 `is_platform_admin`（复用 `auth_handlers._require_context` + `admin_handlers._require_platform_admin`，与 `/api/platform/*` 范式一致）；legacy 模式沿用 `_require_auth()`（共享控制台密码），不改变旧行为。
- 将 `ConfigHandler.GET/POST` 与 `ModelsHandler.GET/POST` 四个入口的 `_require_auth()` 替换为 `_require_platform_console()`。
- 新增测试：`test_http_policy.py` 补 `test_models_and_config_console_classified_platform`（两类路由归类为 `platform`）与 `test_require_platform_console_rejects_non_admin_in_database`（非平台管理员被拒、平台管理员放行）。
- **BREAKING（database 模式）**：`/config` 与 `/api/models` 不再对匿名/普通成员返回 503，而是返回 401（未登录）或 403（已登录但非平台管理员）；该行为仅影响 database 模式，legacy 模式访问控制不变。
- 全局模型/系统配置仍为单例（`config.json`），归属平台管理员；**不**实现逐租户独立模型配置，避免与底层全局配置冲突。

## Capabilities

### New Capabilities

- `platform-config-console`: database 多租户模式下，平台管理员对全局系统配置（`/config`）与模型配置（`/api/models`）的读取与写入控制台，含鉴权边界与租户/管理员隔离语义。

### Modified Capabilities

无。当前 `openspec/specs/` 正式基线为空；本次使用一份 ADDED 能力补充既有产品代码的控制台访问闭环，不重建既有配置或身份模型。

## Impact

- 需求依据：`docs/design/menu-structure-audit-and-plan.md` 的"平台与租户范围分离"、"租户管理员不因身份获得平台全局模型凭据或全局配置权限"、"模型服务按真实资源归属标明平台配置"；`docs/design/user-role-permission-gap-and-plan.md` 的角色/权限边界。配置引用的 `doc/优化规划/PRD/PRD-00-总目录.md`、PRD-01～12 v1.2 原文当前缺失，记录缺口并在恢复后核对真实映射，不虚构编号或阻塞已明确修复。
- 数据唯一归属：`config.json` 保有全局模型凭据、`api_base`、`bot_type`、`model` 与系统参数，是全局单例，归属平台范围；`identity.db` 继续保有账号、租户、成员和会话，作为平台管理员资格与请求授权来源。本次不新增资源授权、模型策略或逐租户配置存储。
- 代码范围：`auth/http_policy.py`、`channel/web/{web_channel,auth_handlers,admin_handlers}.py`、现有 Web 模型/配置前端与 `tests/test_http_policy.py`、`tests/test_models_handler.py`。保留 Python/web.py、SQLite 与现有 Web 前端。
- 跨 change 依赖：复用 `complete-enterprise-identity-access-control` 的请求授权解析（`_require_context`/`_require_platform_admin`）与 `enterprise-access-enforcement` 策略；沿用 `branding`、`todos` 等已开放能力的只读/访问边界。模型配置与系统配置的写入仍走 `ConfigHandler`/`ModelsHandler` 既有 `config.json` 写路径与 Bridge 重置。
- 交付与验收：使用一份需求→测试→版本→结果的验收表复用证据；仅当代码变更影响既有结论时重跑相关检查。验证平台管理员可读写两类接口、普通成员/匿名 401、非平台管理员 403，以及 legacy 模式访问控制保持不变。
- 兼容与恢复：保持既有模型凭据、`api_base`、`bot_type`、`model` 与 `config.json` 结构不变；database 迁移标识保持后不能切 legacy 或删除标识绕过保护。归档前协调原配置/模型等重叠规范，本次不改其他 change 的任务勾选。
