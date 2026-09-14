## Context

见 `proposal.md - Why`（含实测证据）。需要补充的接入点现状：

- `channel/web/route_registry.py`：`/api/file` GET 记为 `tenant`，未带资源派生标记；`/uploads/(.*)` 已带 `tenant_from_resource=True`。
- `channel/web/web_channel.py::FileServeHandler.GET` 经 `_db_scope()` → `_require_context(require_tenant=True)` 要求 `X-Tenant-ID`；同文件已有 `_uploads_identity_scope()` / `_stream_identity_scope()` 处理「浏览器原生请求无法带头」。
- `channel/web/web_channel.py::PreviewHandler.GET` 用 `_is_path_allowed()` 做纵深根校验；`_is_path_allowed()` 现仅含平台根 + `_get_workspace_root()` + 已打开项目。`/preview` 是 `public` 路由，请求期间 `current_identity()` 为空，`_get_workspace_root()` 抛「tenant scope required」403 被 `except` 吞掉，租户根因此不在允许集。
- 工件地址构造：`_build_artifact_payload()` 产出 `raw_url=/api/file?path=...`、`preview_url=/preview/<token>/<name>`；`workspace.js` 的卡片下载用 `raw_url`，HTML/PDF 预览用 `preview_url`。

## Goals / Non-Goals

**Goals:**
- 让已登录且获权的浏览器完成本租户 Agent 生成工件的**下载**（`/api/file`）与**预览**（`/preview`）。
- 让 `/api/file` 的租户解析回到「被寻址文件」这一权威来源，而不是依赖浏览器能带头的假设。
- 保持门禁确定性拒绝顺序与对象级授权不变；不新增路径特例。

**Non-Goals:**
- 不放宽跨租户读取：非成员仍被拒，且不可见路径返回 404。
- 不改动数据库 schema、租户目录布局、`agent_workspace` 语义。
- 不改动 `/message`、`/stream`、`/poll`、`/cancel`、`/upload`、`/api/*`（`/api/file` 除外）的既有租户头语义。
- 不为旧的无令牌预览地址做兼容。

## Decisions

**决策 1：`/api/file` 采用「租户由被寻址文件派生」，与 `/uploads`、`/stream` 同形。**

新增 `_file_identity_scope()`：

1. 取 `_session_token()`，无 token → 401；
2. `resolve_context(svc, token, None)` 先认证调用者；
3. 由 `_tenant_owning_path(svc, realpath(path))` 解析文件所属租户——候选来源是服务端的租户共享根与绑定智能体 workspace，取最具体的包含根；无法归属 → 404（不泄漏存在性）；
4. 请求头 `X-Tenant-ID` / 查询 `tenant_id` 若与派生租户不一致 → 400 conflicting；
5. `resolve_context(svc, token, derived_tenant)` 校验有效成员资格（非成员 → 403）；
6. `must_change_password` → 403；
7. `use_identity(to_runtime_identity(ctx))` 发布上下文。

`FileServeHandler.GET` 内既有的 `_require_tenant_agent_binding` / `_require_private_owner` / `_require_agent_action(..., "read", "agent.read")` / `_authorize_db_file_path` 全部保留，对象级授权不放宽。

- 备选：前端改用 `fetch` 拿 blob 再下载。否决——只能修下载按钮，修不了消息里 `<img src=/api/file>` 的子资源读取，且把安全语义留在前端。
- 备选：让 `/api/file` 接受客户端 `?tenant_id=` 作为权威。否决——把租户真值交给客户端；仅用于**冲突检测**是安全的，因为派生租户仍是权威。

**决策 2：`/preview` 的纵深根校验改为静态可信工作区集合，并去除异常的副作用。**

`_is_path_allowed()` 的根集合纳入 `_tenant_workspace_roots()`（所有租户共享根 + 所有绑定智能体 workspace），这些来源**不依赖请求身份**，因此 public 的能力令牌预览也能通过；同时移除 `_get_workspace_root()` 调用——它会在无租户身份时构造 `web.HTTPError`，而 `HTTPError.__init__` 会写入 `web.ctx.status` 与响应头，即使异常被 `except` 吞掉也会把成功响应污染成 403。

- 为什么可以放宽到「全部租户根」：`/preview` 的授权真值是 HMAC 目录令牌，令牌只由服务端在解析工件路径时签发；此处的根集合是纵深防御（防密钥泄漏后任意文件读），仍拒绝平台根与租户/智能体工作区之外的路径（含操作员主目录）。
- 备选：直接信任令牌、删除根校验。否决——丢掉纵深防御，且违反 `platform-file-browsing` 的「不得以未声明的高风险根作为默认」。

## Risks / Trade-offs

- [路径→租户派生可能被用于探测] → 仍要求有效凭据；无法归属的路径一律 404（不区分「不存在」与「无权限」）；成员资格由 `resolve_context` 校验，非成员 403。
- [`/api/file` 的显式租户头与文件不符时由 404 变 400] → 仅影响陈旧/错误选择；同租户正常调用头值一致，行为不变。
- [预览允许根放宽到全部租户工作区] → 令牌不可伪造且仅服务端签发；跨租户仍需拿到对应令牌，而调用方作用域内解析不会产生他人的令牌。
- [前端缓存旧 `console.js`] → 本次无前端改动，无需强刷。

## Migration Plan

无数据迁移。部署后重启进程即生效。回滚只需还原 `web_channel.py` 与 `route_registry.py`（及本 change 新增测试）；回滚后回到「工件下载 400 / 预览 404」的既有故障状态。

## Open Questions

无。
