## Context

当前 `identity_mode=database` 下，`auth/http_policy.py` 把 `/config` 与 `/api/models` 登记为 `policy: closed`（deferred）。`enforce_http_policy` 处理器在 database 模式下对任何 `closed` 消费者提前返回 `503 database_unavailable`，从不进入 `ConfigHandler`/`ModelsHandler`，导致模型配置与控制台页面空载。两类的写路径（`config.json`、Bridge 重置）与读投影早已在 `web_channel.py` 实现，只差入口授权这一层。参见 proposal.md - Why。

`docs/design/menu-structure-audit-and-plan.md` 已明确：全局模型凭据/全局配置属平台范围，租户管理员不因身份获得；模型服务"按真实资源归属标明平台配置"。这确立了"平台管理员 → 平台读写、租户/成员 → 拒绝"的边界。现有 `/api/platform/*` 已提供完整范式：`_guard_database()` + `_require_context()` + `_require_platform_admin(ctx)`。

## Goals / Non-Goals

**Goals:**
- 在保留 database 多租户模式前提下，让平台管理员可读/写 `/config` 与 `/api/models`。
- 复用既有 `admin_handlers` 的请求解析与平台授权，不新增 IAM 机制。
- 保持 `config.json` 字段结构与写路径不变，仅改变入口授权边界。
- 通过可测的政策分类测试与鉴权测试锁定行为。

**Non-Goals:**
- 不实现逐租户独立模型/系统配置（底层 `config.json` 为全局单例，无法真正隔离）。
- 不开放 `/config`、`/api/models` 之外的其它 deferred 消费者（`/upload`、`/api/workspace/*`、`/api/channels`、`/api/tools`、`/api/skills`、`/api/knowledge/*`、`/api/scheduler/*` 等保持 `closed`）。
- 不新增资源授权/模型策略/凭据隔离语义。
- 不改变 legacy 模式的共享控制台密码鉴权。

## Decisions

### 1. 路由策略归类为 `platform`，而不是 `tenant` 或 `personal`

`/config` 与 `/api/models` 读写的是全局 `config.json`（模型凭据、api_base、bot_type、model、系统参数），是部署级单例。若归类为 `tenant` 会要求显式租户选择并按租户隔离，但底层只有一份全局配置，会产生"不同租户配置互相覆盖"的矛盾。`platform` 与需求一致：**平台管理员 → 全局**、租户管理员/成员无权。

- 备选：`tenant`（逐租户模型）→ 被否，底层无按租户存储，语义冲突。
- 备选：`platform_write_tenant_read`（平台写、租户读）→ 记录为后续可选方向；本次不做，因为租户读会暴露全局厂商/凭据状态，且前端模型页当前仅面向平台管理员。

### 2. 双层校验：`http_policy` 分类 + handler 内鉴权

遵循项目既有的"策略分类 + 实际授权"两层：
- **层 1（policy）**：`ROUTE_POLICY` 把 `/config`、`/api/models` 的 GET/POST 标为 `platform`。这样 database 模式下不再被短路 503，未登录请求由处理器链正确返回 401。
- **层 2（handler）**：`ConfigHandler`/`ModelsHandler` 的 GET/POST 入口调用 `_require_platform_console()`，真正解析会话并校验 `is_platform_admin`，返回精确的 401/403。

这与现有 `/api/platform/*` 的处理一致：policy 负责身份域路由，handler 负责授权判定，避免 policy 层重复 handler 的会话/权限细节。

### 3. `_require_platform_console()` 复合守卫

新增一个单一守卫函数，按身份模式分派：
- `identity_mode=database`：`_require_context()`（解析权威会话，缺失 → 401，`must_change_password` → 403，身份库故障 → 503）+ `_require_platform_admin(ctx)`（非平台管理员 → 403）。
- `identity_mode=legacy`：`_require_auth()`（共享控制台密码），保持旧行为。

- 备选：在 `ConfigHandler`/`ModelsHandler` 内各自内联 `if _is_database_identity(): ... else _require_auth()` → 被否，四处重复；单一守卫更易测试与维护。
- 备选：直接改 `_require_auth()` 内部 → 会被所有其它 legacy 鉴权入口共享，改动面过大且会改变其它 handler 的语义；本次不采用。

`_require_context()` 已在 `auth_handlers.py` 中内置了受限会话（`must_change_password`）拒绝逻辑，因此平台管理员在强制改密状态下也无法访问这两类控制台，符合受控最小访问原则。

### 4. 复用现有 `_require_context` 与 `_require_platform_admin`，不复制授权逻辑

`_require_context()` 处理无会话 401、缺租户选择 400、无权限 403、身份库 503；`_require_platform_admin()` 处理非平台管理员 403。均在 `admin_handlers`/`auth_handlers` 中已实现且被既有测试覆盖。守卫只做组合调用，不重复实现。

返回的 JSON envelope 沿用现有 `admin_handlers` 的 `_error(message, status, code)` 约定（含 `status/message/code`），与前端错误处理兼容。

## Risks / Trade-offs

- [配置控制台从 503 改为 401/403，改变了既有前端错误语义] → 前端模型/配置页原有对 `status !== 'success'` 的错误展示已能容纳 message/code；未登录会引导重新登录，符合 database 模式预期。已在规格中记录该行为变更（BREAKING）。
- ["platform" 归类会暴露全局厂商/凭据状态给平台管理员] → 这是需求本身（平台管理员应管理全局配置）；返回的 api_key 仍为掩码形式，`web_password` 在非 Desktop（`COW_DESKTOP!=1`）环境只返回掩码，避免泄漏明文。
- [过度授权风险：平台管理员可改全局模型进而影响所有租户的默认模型] → 这是平台范围的既定语义；写入仍受 `ConfigHandler` 的 `EDITABLE_KEYS` 与 `ModelsHandler` 的各 action 校验约束，仅允许既定字段更新。
- [legacy 回归] → 守卫在 legacy 分支仍调用 `_require_auth()`，改动仅影响 database 分支；已通过 `test_models_handler`（legacy 桩通过 `patch _require_auth`）与真实服务回归验证。
- [测试隔离污染导致批量运行出错] → 既有 `test_models_handler.py` 的 `web` 桩会污染同进程其它测试；单模块运行时均通过。本 change 不新增此类桩，仅在 `test_http_policy` 增加纯策略/守卫测试。

## Migration Plan

1. 更新 `auth/http_policy.py` 将 `/config`、`/api/models` GET/POST 改为 `platform`。
2. 在 `channel/web/web_channel.py` 新增 `_require_platform_console()`，并把 `ConfigHandler`/`ModelsHandler` 的 GET/POST 入口替换为该守卫。
3. 重启 `app.py`（launchd `com.cowagent.app`）使新策略生效。
4. 回归：`test_http_policy`、`test_models_handler`、`test_platform_user_admin`、`test_identity_web_handlers`、`test_branding`、`test_custom_provider_handlers` 单模块均通过；真实服务 curl 验证平台管理员 200、匿名 401。
5. 回滚：将两处 policy 改回 `closed` 并恢复 `_require_auth()` 即可回到原先 503 行为，无需数据结构迁移。

## Open Questions

- "平台写、租户读"（租户管理员可只读全局配置）是否需要在后续版本引入？当前不确定，且会引入暴露全局厂商/凭据状态的取舍；在真有租户只读需求、且前端模型页支持租户视图时再单独设计。本 change 不依赖该决策。
