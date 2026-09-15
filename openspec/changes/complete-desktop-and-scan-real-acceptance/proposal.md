## Why

`complete-database-capability-parity` 已按“已验收切片”部分归档：定时任务管理、记忆浏览、伙伴项目浏览、微信扫码配置面、接缝登记（含适用动作审批消费方）以及路由/界面/消费者投影一致性已合并主规范，并取得真实 WSGI 应用上的授权与拒绝证据。

前序 change 另有 6 项交付任务因缺少外部条件无法在该环境完成，按原任务文字保持未勾选并移交本 change：

- 7.8 真实微信提供方的扫码、密文落库、连接与收发验收（公共与个人 scope 分别记录）。
- 7.10 按 7.8 证据更新 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES`。
- 8.7 真实打包 Desktop 客户端在两个租户、两个成员、失效身份与重连条件下的完整链路演练。
- 8.8 由统一开放矩阵更新 Desktop 与兼容 `desktop_enterprise` 投影并清理永久 deferred。
- R2 原生协议的远程 HTTPS 形态、兑换响应丢失重试与旧后端拒绝降级等协议级边界。
- 11.3 使用 7.8-7.10 实际验收类型的扫码到个人收发联合验收。

未完成的原因不是设计或实现缺口，而是本环境无法提供真实提供方账号与真实打包客户端。因此本 change 的目标是把“剩余验收”作为显式、可追踪的承接项，而不是让它们在归档时消失。

## What Changes

- 承接 `desktop-tenant-context` capability：实现（后端授权码与 PKCE、主进程 broker、窄化 IPC、租户上下文传递、切换隔离、页面动作契约）已交付并有协议级证据，但真实打包客户端演练未执行，因此该 capability 随本 change 保持 `desktop_tenant_context` 切片 `accepted=false`；规范内已补入远程 HTTPS 形态、兑换响应丢失、旧后端拒绝降级，以及两个租户两个成员与失效身份场景。
- 承接 `channel-scan-onboarding` 的「微信个人执行按实际类型与作用域独立验收」requirement：配置面已归档可用，个人执行与公共实例个人入口仍由 `personal_runtime_enabled()` 与逐类型记录共同关闭，未取得真实验收前不得打开。
- 完整承接 R2 的剩余协议边界，并保留其原门槛：未完成不得进入 8.7、8.8 的交付验收。
- 不重新打开任何已归档的已验收切片；不因移交改变任何授权、审计、配额、隔离或审批门槛。

## Capabilities

### New Capabilities

- `desktop-tenant-context`：Desktop 身份生命周期、租户与请求上下文、传输和页面能力适配。由 `complete-database-capability-parity` 移交，尚未取得真实客户端验收。

### Modified Capabilities

- `channel-scan-onboarding`：微信个人执行按实际类型与作用域独立验收。由 `complete-database-capability-parity` 移交真实执行验收部分。

## Impact

权威依据是 `openspec/specs/` 既有规范；移入的 requirement 只改变承接方，不改变任何 `SHALL`/`MUST`。

受影响实现位于 `desktop/src/main/**`（`auth-broker.ts`、`broker-protocol.ts`、`http-relay.ts`、`preload.ts`、`index.ts`）、`desktop/src/renderer/src/api/context.ts` 与 `client.ts`、`auth/desktop_auth.py`、`channel/channel_instances.py` 的逐类型运行判定，以及 `auth/capability_matrix.py` 中 `desktop_tenant_context` 切片的开放登记。

未取得真实验收前，`desktop_tenant_context` 消费者保持 `awaiting_acceptance` 关闭、无路由开放动作；微信个人执行保持关闭并报告“已保存未连接”。真实提供方与真实客户端验收完成后，才允许按证据更新切片、`PERSONAL_RUNTIME_ACCEPTED_TYPES`、`PUBLIC_PERSONAL_INGRESS_TYPES` 与桌面投影。
