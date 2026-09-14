## Context

见 `proposal.md - Why`（含实测 A–D 证据）。需要补充的接入点现状：

- `channel/web/static/js/console.js` 的 `window.fetch` 包装（约 4239–4290 行）以硬编码正则判定注入范围：`/^\/api\//` 或 `/^\/(message|stream|poll|cancel)\b/`。`/upload` 不在其中。
- `channel/web/web_channel.py` 的 `upload_file()` 由 `authorized_target().get("agent_id")` 得到写入目标，但 `preview_url` 只拼接 `/uploads/<name>`；同文件的 `VoiceAsrHandler` 已经拼了 `?agent_id=`，两处不一致。
- `UploadsHandler.GET()` 经 `_db_scope()` → `_require_context(require_tenant=True)` → 要求 `X-Tenant-ID`。同一文件已有 `_stream_identity_scope()` 处理「浏览器原生协议无法带头」的同类问题：先 `resolve_context(svc, token, None)` 认证，再依据被寻址请求的属主解析租户并 `resolve_context(svc, token, owner_tenant)` 校验成员资格。
- 门禁 `auth/http_policy.enforce_http_gate` 对 `tenant_from_resource` 的路由以 `require_tenant=False` 解析上下文并缓存；`/stream` 已用该标记。

## Goals / Non-Goals

**Goals:**
- 让已登录且获权的浏览器完成附件上传与回读（粘贴/选择文件/目录），并让缩略图正常显示。
- 让上传回读的租户解析回到「被寻址智能体」这一权威来源，而不是依赖浏览器能带头的假设。
- 保持门禁的确定性拒绝顺序与对象级授权不变；不新增路径特例。

**Non-Goals:**
- 不改动 `agent_workspace`、租户目录布局或数据库 schema。
- 不改动 `/message`、`/stream`、`/poll`、`/cancel`、`/api/*` 的既有租户头语义。
- 不为旧的无 `agent_id` 上传地址做兼容回读（database 模式下它们当前一律 400，本 change 后为 404，均不可用；新写入的地址总是带 `agent_id`）。
- 不改变前端失败分支的提示策略（静默修正为可见错误不在本次范围）。

## Decisions

**决策 1：前端以显式清单驱动租户头注入，而不是继续堆正则分支。**

把 `/^\/api\//` 与传输名单拆成常量：所有 `/api/*` 之外**需要租户选择**的控制台传输列在一处（`/message`、`/stream`、`/poll`、`/cancel`、`/upload`）。

- 备选：把注入条件放宽为「所有同源非 `/api/auth` 请求」。否决——会向 `/chat`、`/uploads`、静态资源等发送无谓的租户头，并且掩盖「该路由是否真的需要选择」这一契约，后续新增路由容易误判。
- 备选：把 `/upload` 也改成资源派生（省掉前端改动）。否决——`/upload` 由 `fetch` 发起，**可以**带头，保持显式选择语义更严格；资源派生只留给结构上无法带头的路由。

**决策 2：`preview_url` 带上被授权 `agent_id`。**

与 `VoiceAsrHandler` 的 `audio_url` 写法对齐（`?agent_id=<id>`）。理由：写入目标是被授权智能体，回读必须指向同一目标；否则非默认智能体的附件必然 404。

**决策 3：`/uploads/(.*)` 标记资源派生，handler 由智能体绑定解析租户。**

新增 `_uploads_identity_scope()`，仿 `_stream_identity_scope()`：
1. 取 `_session_token()`，无 token → 401；
2. `resolve_context(svc, token, None)` 认证调用者；
3. 从被寻址 `agent_id` 的绑定得到 `tenant_id`；无绑定 → 404（不泄漏存在性）；
4. 若请求同时带 `X-Tenant-ID` / `tenant_id` 且与派生租户不一致 → 400 conflicting；
5. `resolve_context(svc, token, tenant_id)` 校验有效成员 / 启用租户 / 启用账号（非成员 → 403）；
6. `must_change_password` → 403，保持与 `_db_scope` 一致；
7. `use_identity(to_runtime_identity(ctx))` 发布上下文。

handler 内既有的 `_require_tenant_agent_binding` / `_require_private_owner` / `_require_agent_action(..., "read", "agent.read")` 全部保留，对象级授权不放宽。

- 备选：让 handler 接受客户端 `?tenant_id=` 作为权威。否决——把租户真值交给客户端；仅在**冲突检测**里使用它是安全的，因为派生租户仍是权威。

**决策 4：路由策略仍为 `tenant`，只增加 `tenant_from_resource` 标记。**

复核清单与基线同步更新，不新增路径特例（符合 `console-route-lifecycle` 的既有要求）。

## Risks / Trade-offs

- [资源派生使 `/uploads` 不再要求显式租户选择，可能被用于探测] → 仍要求有效凭据；无绑定智能体一律 404（不区分「不存在」与「无权限」）；成员资格由 `resolve_context` 校验；实测覆盖非成员场景。
- [上传响应地址变化影响既有前端解析] → `preview_url` 只是新增查询参数，`<img src>` 与既有渲染路径无需改动；`?_probe` 已验证带参可 200。
- [前端清单与后端清单可能再次漂移] → 在 JS 常量处写明该清单必须与 `route_registry` 中带 `tenant` 策略且非 `/api` 的传输保持一致；本 change 的测试对 `/upload` 做定点断言，漂移会失败。

## Migration Plan

无数据迁移。部署后重启进程 + 浏览器强刷（`console.js` 无版本参数，需避免旧缓存）。回滚只需还原 `console.js`、`web_channel.py`、`route_registry.py`（及基线）；回滚后回到「附件链路 400」的既有故障状态。

## Open Questions

无。
