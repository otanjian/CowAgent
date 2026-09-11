## 1. 入口路由策略分类

- [ ] 1.1 在 `auth/http_policy.py` 的 `ROUTE_POLICY` 中，将 `/config` 的 GET/POST 从 `policy: closed`（deferred）调整为 `policy: platform`，保留注释说明其平台配置语义。
- [ ] 1.2 将 `/api/models` 的 GET/POST 从 `policy: closed` 调整为 `policy: platform`，保留注释说明其为平台管理员模型配置。

## 2. 平台控制台鉴权守卫

- [ ] 2.1 在 `channel/web/web_channel.py` 新增 `_require_platform_console()`：database 模式复用 `auth_handlers._require_context()` + `admin_handlers._require_platform_admin(ctx)`；legacy 模式沿用 `_require_auth()`；不复制既有授权逻辑。
- [ ] 2.2 将 `ConfigHandler.GET` 入口的 `_require_auth()` 替换为 `_require_platform_console()`。
- [ ] 2.3 将 `ConfigHandler.POST` 入口的 `_require_auth()` 替换为 `_require_platform_console()`。
- [ ] 2.4 将 `ModelsHandler.GET` 入口的 `_require_auth()` 替换为 `_require_platform_console()`。
- [ ] 2.5 将 `ModelsHandler.POST` 入口的 `_require_auth()` 替换为 `_require_platform_console()`。

## 3. 测试覆盖

- [ ] 3.1 在 `tests/test_http_policy.py` 新增 `test_models_and_config_console_classified_platform`：验证 `/config`、`/api/models` 的 GET/POST 均归类为 `platform`（而非 `closed`）。
- [ ] 3.2 在 `tests/test_http_policy.py` 新增 `test_require_platform_console_rejects_non_admin_in_database`：验证 database 模式下非平台管理员被拒（403）、平台管理员放行，且 legacy 分支调用 `_require_auth`。
- [ ] 3.3 回归单模块：`test_models_handler`、`test_http_policy`、`test_platform_user_admin`、`test_identity_web_handlers`、`test_branding`、`test_custom_provider_handlers` 在 `.venv` 下单独运行均通过（避免 `web` 桩污染导致批跑误判）。

## 4. 迁移与验证

- [ ] 4.1 通过 launchd 重启 `com.cowagent.app`（`app.py`），使新策略与守卫生效。
- [ ] 4.2 真实服务 curl 验证：未登录访问 `/config`、`/api/models` 返回 `401`（不再是 503）；登录平台管理员后 GET 返回 `200`，POST `set_capability` 返回 `200 success`。
- [ ] 4.3 验证其它 deferred 消费者（`/upload`、`/api/workspace/*`、`/api/channels` 等）在 database 模式仍返回 `503`，未因本次改动被意外开放。
- [ ] 4.4 legacy 模式回归：确认 `/config`、`/api/models` 仍沿用共享控制台密码鉴权，行为与改动前一致。

## 5. 文档与验收

- [ ] 5.1 更新行为差异说明：记录 `/config`、`/api/models` 在 database 模式由 503 变为 401/403 的 BREAKING 语义。
- [ ] 5.2 运行 OpenSpec 严格校验并核对本变更的 specs 实现验收表，确认平台管理员读写、匿名/非平台管理员拒绝、legacy 复用共享密码均满足规格。
