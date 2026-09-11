# 真实认证接口验收切片

2026-09-08 在隔离本机 HTTP 环境完成 47 项检查；结构化脱敏结果见 [auth-contract.json](auth-contract.json)。另运行 `tests/test_identity_web_handlers.py`，11 个测试通过。

- 使用项目 `.venv/bin/python`（Python 3.14.3），`web_channel.build_web_app()` 的完整生产路由，真实 `AuthCheckHandler`、`AuthLoginHandler`、`AuthLogoutHandler` 以及数据库 `IdentityService` / SQLite；不存在认证或业务 API mock/stub。
- 仅创建临时 fixture 账号、测试配置、日志、工作区及身份数据库。`COW_DATA_DIR` 指向 `/tmp/cow-sidebar-account-acceptance/ready/{mode}/`；不使用既有账号、不修改运行中的 9899 或用户 config/identity.db。
- HTTP 19891 / 19892 / 19893 分别覆盖 database、legacy 密码保护、legacy 免登录。19894 在独立临时目录验证生产 `/config` 修改密码保护开关；开关改动只涉及测试实例。
- database 覆盖管理员和普通零/单/多租户账号登录、HTTP Cookie 检查、退出后 Cookie 检查、撤销后的 Bearer 检查。包含长姓名、`<script>` 与 `&` 文本；此切片确认原始资料契约，浏览器文本转义需由 UI 验收确认。
- 真实响应确认：database 明确返回 `identity_mode`、`authenticated`、本人 `username/display_name`；legacy 密码检查不返回 `identity_mode`；免登录仅返回 `status:success, auth_required:false`。legacy 登录的本人状态需使用此前确认模式或再次检查。
- 带无效 `X-Tenant-ID` 的数据库本人检查确实返回 `authenticated:false`；不带租户头的 Cookie 检查正常，所以本次前端本人 `/auth/check` 必须独立于租户 API 封装。
- 密码开关真实切片确认：免登录启用密码后未持有有效 Cookie 的检查进入未认证；登录后检查成功；清空密码后检查明确回到 `auth_required:false`。
- 三种模式均验证生产品牌公开读取、版本读取与 `ChatHandler` 返回当前工作区 HTML。数据库执行能力等其他模块仍遵守生产门槛，本记录不代表聊天/租户管理/其他 change 的整体验收通过。

临时启动脚本：`/tmp/cow-sidebar-account-acceptance/server.py`。示例：`.venv/bin/python /tmp/cow-sidebar-account-acceptance/server.py --mode database --port 19891 --root /tmp/cow-sidebar-account-acceptance/ready`。测试凭据只写入对应目录 `credentials.json`（0600），不写入本 change。检查脚本和会话失效控制脚本同在临时根目录。

运行环境已具备 bundled Node 的 Playwright 包和 `/Applications/Google Chrome.app`。浏览器验收可使用独立 Playwright Chromium context 与 `channel: 'chrome'`，避免控制用户浏览器。UI 结果由实施阶段另行记录，不能由本 HTTP 切片推定通过。

## 追加：真实 HTTP 401 与既有数据库路由限制

浏览器验收发现，撤销数据库测试账号后，`GET /api/platform/tenants` 在此隔离 WSGI 环境返回 HTTP 500。只读定位到 `channel/web/auth_handlers.py` 的 `_require_context()`：`web.HTTPError(str(e.status), ...)` 传入裸状态字符串 `"401"`，不满足 WSGI 状态必须包含 reason phrase 的格式，容器抛出 `AssertionError: Status must be at least 4 characters`。`/api/tenant`、数据库 `/api/branding` 复用相同路径。另实际观察到 `/api/agents` 捕获身份异常并返回 HTTP 200 错误 JSON，`/api/todos` 将状态写入 `web.status` 而非 `web.ctx.status`，同样返回 HTTP 200 错误 JSON。本 change 未改动这些既有后端行为，也不能宣称这些路径已经支持真实 HTTP 401 全局拦截。

可用于本次全局 401 验收的真实生产路径是 **legacy 密码模式的 `GET /api/agents`**（`GET /config` 同样可用）。已在 19892 用独立 HTTP Cookie 会话依次调用生产登录、生产 `/auth/logout`、`GET /api/agents`，确认最后返回 **HTTP 401 + `{"status":"error","message":"Unauthorized"}`**。浏览器可在已展示账号的页面直接调用 `/auth/logout` 清 Cookie（不调用前端 `handleLogout`），随后调用 `/api/agents`，验证现有全局 fetch 拦截器的失效副作用；这不是模拟 HTTP 状态。

数据库模式的真实失效路径则通过撤销 fixture 的 SQLite 会话，再调用本人 `/auth/check`，确认 HTTP 200 且 `authenticated:false`；前端对此明确认证结论的处理应单独验收。旧/新身份并发 401 的精确顺序仍由确定性前端测试复现，与上述真实接口证据区分。
