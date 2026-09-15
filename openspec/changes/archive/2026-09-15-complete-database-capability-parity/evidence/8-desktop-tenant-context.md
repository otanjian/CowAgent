# 8 — Desktop 身份与租户上下文（evidence）

范围：task group 8（8.1–8.8），按 `design.md` D8/D9 实现 Desktop 请求接缝、主进程认证 broker、
系统浏览器授权码 + PKCE S256、租户 gate、epoch 与错误映射。本文件只记录**实际执行过的命令与结果**；
未执行的部分（8.7 真实客户端联调）明确标注为未验收，不做假验证。

---

## 8.1 唯一有效 tenant/epoch 身份状态 + 统一请求装饰器

**实现**

- 新增 `desktop/src/renderer/src/api/context.ts`：渲染进程唯一的权威状态机。
  - `ContextSnapshot`：`gate`（`checking`/`need_login`/`password_change`/`tenant_select`/`ready`/`blocked`）、
    `session`（脱敏投影）、`blockedReason`、`lastError`、`identityMode`、`probeFailed`。
  - `DesktopContext` 通过 `subscribe`/`getSnapshot` 暴露，`App.tsx` 用 `useSyncExternalStore` 订阅，
    不再有第二份 `authState`。
  - 单调递增 `epoch`，账号/租户切换即 `nextEpoch()`；快照携带 epoch，迟到响应按 epoch 丢弃。
- `desktop/src/renderer/src/api/client.ts` 全部业务方法改为经统一装饰器 `plan()`/`send()`/`sendForm()`/
  `assetUrl()`，**上游方法名与签名一律保留**（`getSessions`、`sendMessage`、`uploadFile`、
  `createSSEStream`、`getFileUrl`、`getServeFileUrl`、`agentAvatarUrl`、`createLogStream`、
  `getLogDownloadUrl`、`postFormData` 等），内部不再出现 per-method 分支。
- 渲染进程不再持久化会话：删除 `authToken`/`setAuthToken`/`withToken`；`client.ts` 不再出现
  `localStorage`/`sessionStorage`/`Authorization`/`Bearer`/`cow_session`（由 `.cjs` 测试静态断言）。

**验证**

```
$ node --test tests/test_desktop_context_frontend.cjs
✔ every request path goes through the one decorator seam
✔ the renderer never persists or rebuilds a session credential
✔ the renderer puts no session token in a URL
✔ a planned request carries the current epoch and no token
ℹ pass 14  ℹ fail 0
```

## 8.2 主进程 broker + 窄化 preload IPC + 授权码/PKCE S256

**后端（新增独立模块 `auth/desktop_auth.py`，仅 fork 侧）**

- `DesktopAuthService`：`begin` / `precheck` / `confirm` / `deny` / `exchange`。
  - 固定公共 client id `cowagent-desktop`；redirect_uri 只接受精确登记形式
    `http://127.0.0.1:<port><path>` 与 `http://[::1]:<port><path>`（`TestRedirectUriRule`）。
  - 只接受 `code_challenge_method=S256`，明文 PKCE 被拒。
  - 授权码 60 秒、单次使用、**以 digest 存储**；绑定 user + 发起方 Web AuthSession + client_id +
    精确 redirect_uri + code_challenge。
  - 兑换原子消费（重放被拒），并重新校验发起方 Web 会话仍然存活（撤销后失效）。
  - 拒绝强制改密会话铸造授权码。
- `channel/web/auth_handlers.py`：`/auth/login` 兼容字段保留但 `token` 恒为空串，只设置身份 Cookie；
  新增 `DesktopAuthorizeHandler`（GET 仅渲染确认页、POST 确认；CSRF/origin/活跃会话三重校验）、
  `DesktopTokenHandler`（同源、无缓存头、响应体不回显 code/verifier）。
- 路由（`channel/web/route_registry.py` 的 `/auth/...` 区域，最小 StrReplace）：
  `/auth/desktop/authorize`（GET/POST）、`/auth/desktop/token`（POST），均为 `public`——
  授权由「code + verifier 对」或确认页上的普通 Web Cookie 承担，服务端对请求实际抵达的每个资源逐次重新授权。

**桌面端（主进程）**

- `desktop/src/main/auth-broker.ts`：Bearer 只存在主进程内存；`redirect: 'error'` 拒绝被凭据化重定向；
  loopback 监听先于打开浏览器；`state` 校验；`timingSafeEqual` 比较；不写磁盘、不落日志。
- `desktop/src/main/asset-proxy.ts`：仅 `127.0.0.1` 临时端口，仅 GET，铸造不透明短时 id，
  带当前凭据回源取字节，`no-store`，按 epoch 作废。
- `desktop/src/main/preload.ts`：只暴露窄化通道
  （probe/status/begin/cancel/logout/refresh/tenantSelect/passwordChange/request/upload/assetUrl[/Sync]），
  不返回任何 token，不接受 header/origin/Authorization 参数。
- `desktop/src/main/index.ts`：backend origin 由主进程持有（`ready` 设置 / `lost` 清除），
  IPC 按可信窗口 + 主 frame 校验 sender。
- `desktop/src/main/http-relay.ts`：通用 httpRelay 收紧为**仅外部 https**，拒绝环回/私网主机、
  原生认证路径（`/auth/login`、`/auth/desktop/`、`/auth/logout`、`/auth/password`）与任何凭据头，
  无法再被用来访问后端或花掉 broker 的会话。

**验证**

```
$ .venv/bin/python -m pytest tests/test_desktop_auth_flow.py -q
35 passed in 8.89s
$ node --test tests/test_desktop_context_frontend.cjs
✔ the preload exposes only narrowed broker channels
✔ the broker keeps the credential in main memory only
✔ the generic relay cannot reach the backend, the callback or a credential
ℹ pass 14  ℹ fail 0
$ .venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_identity_auth_security.py -q
（包含在下方 202 passed 批量中）
```

## 8.3 强制改密 + 按目标域区分的租户 gate

**实现**

- `LoginGate.tsx` 拆成四个 gate：`LoginGate` / `PasswordChangeGate` / `TenantSelectGate` / `BlockedGate`；
  `App.tsx` 按 `context.gate` 渲染，强制改密期间只允许改密与退出。
- 零租户账号：`App.tsx` 放行 `/auth/me`、账号设置、日志与退出，其余路径给出明确说明而不是空白页；
  平台管理员的平台入口不被租户 gate 误挡，且不会伪造 tenant/Membership（broker 的
  `wantsTenantHeader()` 与 Web 控制台同一规则：`/api/**` 且非 `/api/auth/**` 才带租户）。
- 后端：强制改密会话在确认页与 token 端点两处都被拒绝铸造授权码。

**验证**

```
$ node --test tests/test_desktop_context_frontend.cjs
✔ a forced password change and a 503 stay actionable states
✔ 401 maps to the sign-in gate instead of an empty page
✔ an invalid tenant code re-opens tenant selection
ℹ pass 14  ℹ fail 0
$ .venv/bin/python -m pytest tests/test_desktop_auth_flow.py -q
TestAuthorizeEntry::test_forced_password_change_cannot_reach_the_consent_form PASSED
TestConsentConfirm::test_forced_password_change_session_cannot_mint_a_code PASSED
```

## 8.4 上下文传播（JSON/FormData/语音/流/重连/预览/下载/本机导入）

**实现**

- JSON：`context.send()` → `desktop-request` → 主进程附加 Bearer（与当前租户）。
- FormData/语音/本机导入：`context.sendForm()` → `desktop-upload`，主进程重建 multipart。
- 无法加头的传输（EventSource 流/重连、`<img>`/头像预览、下载、日志流）：`context.assetUrl()` →
  `desktopAssetUrl`/`desktopAssetUrlSync` 铸造不透明 loopback URL，由 `asset-proxy.ts` 带凭据回源。
- URL 中永不出现会话 token：`plan()` 剥离既有 `token`/`access_token` 查询参数，
  并断言 `client.ts` 不再有 `withToken`/`Bearer ${...}`。

**验证**

```
$ node --test tests/test_desktop_context_frontend.cjs
✔ the renderer puts no session token in a URL
✔ asset URLs are minted by the broker and never fall back to a token URL
✔ the asset proxy binds loopback, mints opaque ids and pipes upstream
ℹ pass 14  ℹ fail 0
```

## 8.5 租户切换：取消 / 断开 / 缓存隔离 / 迟到响应拒绝

**实现**

- broker `selectTenant()`：同一租户不 bump epoch；真实切换才递增，并清空旧 epoch 的资源 URL。
- `context.plan()` 把计划时的 epoch 带进请求；`send()`/`sendForm()` 回来后比对 `snapshot.epoch`，
  不一致直接丢弃（`discarded`），绝不把旧写在新租户下静默重发。
- `asset-proxy` 用 `dropEpochsExcept()` 作废旧 epoch 的铸造 URL（旧连接随之下线）。
- 退出/被封禁时 `gate=blocked`，业务请求一律短路。

**验证**

```
$ node --test tests/test_desktop_context_frontend.cjs
✔ a response from a previous context epoch is discarded
✔ asset URLs are minted by the broker and never fall back to a token URL
✔ a failed sign-out stops business requests and says so
ℹ pass 14  ℹ fail 0
```

## 8.6 错误映射（401 / 强制改密 / 租户失效 / 403 / 503）

**实现**

- `context.ts` 的 `fail()`/`mapBrokerFailure()`/`mapHttp()`/`describe()` 把
  401 → 登录 gate、强制改密 → 改密页、`*tenant*` → 重选租户、403 → 具体拒绝原因、
  503 → 可恢复状态（含 `lastError`），绝不返回空数据或伪成功。
- 后端可见的拒绝语义复用 Web 权威契约（`missing_tenant`、`invalid tenant`、
  `password change required`、`database_unavailable` 等），不退回共享密码（`auth_handlers.py`
  明确 “identity store unusable -> 503, never fall back to legacy auth”）。

**验证**

```
$ node --test tests/test_desktop_context_frontend.cjs
✔ 401 maps to the sign-in gate instead of an empty page
✔ an invalid tenant code re-opens tenant selection
✔ a forced password change and a 503 stay actionable states
✔ a failed sign-out stops business requests and says so
ℹ pass 14  ℹ fail 0
```

## 8.7 真实 Desktop 客户端联调 —— **未执行（本 change 内无法完成）**

未执行原因：约束要求不得运行浏览器/Electron 自动化，也不得 `npm install`；因此「真实打包客户端 ×
两个租户 × 两个成员 × 失效身份 × 重连」的交付级演练没有在本任务内执行，也没有被任何测试替代。
已完成的替代验证是**协议级**（真实 `build_web_app()` 后端 + 主进程/渲染进程模块的离线用例），
两者不等价，故 8.8 保持 honest 的 `accepted=False`。

## 8.8 开放矩阵投影（已按实际验证结果更新，非单向宣称）

`auth/capability_matrix.py` 的 `desktop_tenant_context` 切片（本任务 own 的区块，
只做最小 StrReplace，未触碰相邻 `memory_browse`/`scheduler`/`project_browse`/`weixin_scan` 区块）：

```python
Slice(
    "desktop_tenant_context",
    capability="desktop-tenant-context",
    consumer="desktop_enterprise",
    page=None,
    scope=frozenset({"personal", "tenant", "platform"}),
    open={},                     # 无自身业务动作：授权由 code+verifier 或确认页 Cookie 承担
    implemented=True,            # 后端协议 + 主进程 broker/IPC + 渲染进程接缝均已落地
    accepted=False,              # 8.7 真实客户端演练未执行
    reason="awaiting_acceptance",
)
```

实测投影（`open={}` ⇒ consumer 关闭，理由来自同一矩阵，不再有写死的 `deferred`）：

```
$ .venv/bin/python -c "..."
as_dict: {'id': 'desktop_tenant_context', 'capability': 'desktop-tenant-context',
          'implemented': True, 'accepted': False, 'open': {}, 'reason': 'awaiting_acceptance'}
consumer: {'available': False, 'reason': 'awaiting_acceptance'}
enabled: False   open: {}
consistency: []
```

`auth/service.py` 的 `desktop_enterprise` 兼容条目：**不需要单独改**。它已由
`_consumer_availability()` 里 `**consumer_availability()` 合并而来（`auth/service.py:3194-3217`），
因此取值恒等于上面的矩阵结果，不会与路由表/页面投影漂移，也不存在永久写死的 `deferred`。
仓库内没有任何活跃客户端读取 `desktop_enterprise`（`rg` 全仓仅命中矩阵、`auth/service.py`
的合并点以及 openspec 文档），所以 `available=False` 不会隐藏任何现行入口。

---

## 汇总：命令与真实结果

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_desktop_auth_flow.py -q` | **35 passed** in 8.89s |
| `.venv/bin/python -m pytest tests/test_identity_web_handlers.py tests/test_identity_auth_security.py tests/test_capability_matrix.py tests/test_auth_profile_edit.py tests/test_identity_policy.py tests/test_identity_service_writes.py tests/test_identity_resource_authorization.py -q` | 202 passed, **2 failed**（均在本任务范围外，见下） |
| `.venv/bin/python -m pytest tests/test_route_registry.py -q` | 22 passed, **1 failed**（`/api/projects/browse`，并行 agent 的在途改动） |
| `.venv/bin/python -m pytest tests/test_http_policy.py -q` | **1 failed**（`test_projects_browse_still_closed_in_database`：期望 503，实得 400 —— 同一并行改动） |
| `.venv/bin/python -m pytest tests/test_config_subagent_toggle.py tests/test_doc_edit.py tests/test_scheduler_web_update.py tests/test_sync_report.py tests/test_workspace_edit.py tests/test_tool_display.py -q` | **87 passed** in 6.59s |
| `node --test tests/test_desktop_context_frontend.cjs` | **14 passed / 0 failed** |
| `node --test tests/test_execution_permission_ui.cjs` | **5 passed / 0 failed** |
| `cd desktop && npm run build` | 成功：`build:renderer` vite ✓ built in 1.51s（2129 modules）+ `build:main` `tsc -p tsconfig.main.json` 退出码 0 |
| `cd desktop && ./node_modules/.bin/tsc -p tsconfig.json --noEmit` | 11 个错误，全部位于**未修改**的既有文件 `pages/ChannelsPage.tsx`(9，lucide `IconComponent` 类型) 与 `pages/settings/BasicSettings.tsx`(2，`ProductModels` 缺字段)；我新增/修改的文件零错误 |
| `openspec validate complete-database-capability-parity --strict` | `Change 'complete-database-capability-parity' is valid` |

未执行（按要求）：`npm install`、完整 Python 测试套件、浏览器/Electron 自动化、提交、归档。

**与本任务无关的既有/并发失败**（未修改其断言，交由对应 owner 处理）：

- `tests/test_auth_profile_edit.py::…test_avatar_flag_appears_in_list_projections_without_bytes_or_paths` /
  `…test_avatar_flag_is_null_before_any_upload`：`auth/service.py::list_members` 投影尚无 `avatar` 字段
  （`set_self_avatar` 已存在但列表投影未补），属个人控制台/头像 owner 在途工作。
- `tests/test_route_registry.py::FrozenBaselineEquivalenceTests::test_derived_policy_matches_frozen_baseline`
  与 `tests/test_http_policy.py::HttpPolicyTests::test_projects_browse_still_closed_in_database`：
  `/api/projects/browse` 已被并行 agent 从 `closed` 改为 `project_browse` 切片策略，冻结基线/断言尚未同步。

---

## 文件清单

**新增**

- `auth/desktop_auth.py` — Desktop 授权码 + PKCE S256 服务（begin/precheck/confirm/deny/exchange）。
- `desktop/src/main/auth-broker.ts` — 主进程认证/传输 broker（内存 Bearer、PKCE、租户选择、epoch、IPC 守卫）。
- `desktop/src/main/asset-proxy.ts` — 仅回环的短时资源代理（无头传输复用凭据且 URL 不含 token）。
- `desktop/src/main/broker-protocol.ts` — broker ↔ 渲染进程的 wire 类型。
- `desktop/src/renderer/src/api/context.ts` — 渲染进程唯一权威上下文与请求装饰器。
- `tests/test_desktop_auth_flow.py` — 后端协议测试（35 例）。
- `tests/test_desktop_context_frontend.cjs` — 桌面端静态/行为校验（14 例）。
- `openspec/changes/complete-database-capability-parity/evidence/8-desktop-tenant-context.md` — 本文件。

**修改**

- `channel/web/auth_handlers.py` — `/auth/login` token 恒空 + `DesktopAuthorizeHandler`/`DesktopTokenHandler`。
- `channel/web/route_registry.py` — 仅 `/auth/desktop/authorize`、`/auth/desktop/token` 两条路由。
- `channel/web/web_channel.py` — 导入新增 handler。
- `auth/capability_matrix.py` — 仅 `desktop_tenant_context` 切片区块。
- `desktop/src/main/index.ts` — backend origin 权威化 + broker/asset proxy/IPC 守卫接线。
- `desktop/src/main/preload.ts` — 窄化 broker IPC。
- `desktop/src/main/http-relay.ts` — 通用 relay 收紧（拒绝后端/认证路径/环回/凭据）。
- `desktop/src/renderer/src/api/client.ts` — 统一装饰器接入，移除 localStorage token 与 URL token。
- `desktop/src/renderer/src/App.tsx`、`components/LoginGate.tsx`、`i18n.ts`、`types.ts` — gate、i18n 与 electronAPI 类型。
- `tests/test_identity_web_handlers.py`、`tests/test_identity_auth_security.py` — `/auth/login` token 空串断言（按 D8 收紧，未削弱）。
