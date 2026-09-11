## Why

database 模式下，「消息渠道」页对租户管理员只呈现一个纯手工凭据表单：既没有 master 分支的扫码接入形态，也没有飞书一键创建与企微扫码创建；同时平台视图的扫码入口被策略表判为 `closed`，点击只会 503 并显示「创建失败」。此外，渠道实例绑定的 Agent 一旦留空，入站消息会回落到全局默认智能体并以 `external_agent_not_tenant_bound` 被拒，使「不选智能体」成为一个能选但不工作的选项。

根因有三层：①飞书一键创建接口的注册会话是进程级单例且明文回传 `app_secret`，多租户下构成跨用户凭据泄漏，故被有意关闭；②入站身份解析以 Agent 绑定为租户锚点，渠道实例自身的归属未被使用；③租户渠道创建期不校验最小必填凭据集，且凭据落库后需维护窗口重启才生效，与既有即时接入体验不一致。

## What Changes

- 飞书一键创建：注册会话改为**发起者绑定**（不透明句柄 + `(发起者 user, tenant)`），各方会话互不取消、凭据只回发起者；`/api/feishu/register` 由 `closed` 开放为可用策略（含补登记当前缺失的 GET 方法）。
- 渠道接入 UI 与既有 master 形态对齐：租户视图改为渠道卡片（图标、状态点、断开），飞书「扫码创建 / 手动填写」Tab，企微「扫码创建」；扫码结果自动填入创建表单。
- 扫码或保存后**即时生效**：复用既有实例热重启路径，替换「需维护窗口重启」的表述。
- 入站锚点由渠道实例决定：租户取自实例归属（DB 反查，不采信 context 自报值）；`agent_id` 可缺省，缺省时解析到该租户的默认智能体；会话仍按发送者各自区分。
- 创建期校验最小必填凭据集，避免「缺 secret 的实例」留到启动期才失败。
- 未开放的接入动作按原因给出明确说明（扩展既有「渠道页在消费者未开放时不进入持续加载状态」要求），不再只显示「创建失败」。
- 外部身份绑定（渠道 + 应用 + open_id → 账号）改为**平台管理员与租户管理员共同维护**：租户侧以成员标识在本租户内解析，跨租户一律按不存在拒绝；两条入口复用同一弹窗，始终显示「是谁 / 哪个渠道 / 哪个 open_id」。绑定写入沿用既有校验、唯一性与审计。
- 被拒的未绑定入站登记为**待绑定尝试**（含投递实例的租户），使管理员可点选 open_id 而不必抄日志；重复拒绝合并计数，绑定成功即清除。此项为落地过程中出现的真实阻塞所必需：没有绑定入口，入站锚点无法由真人验证。
- **BREAKING**：入站租户锚点由「渠道实例所绑定 Agent 的租户」改为「渠道实例自身的租户」。既有依赖 Agent 绑定推断租户的行为与相关规范断言随之修订。

## Capabilities

### New Capabilities

- `channel-scan-onboarding`: 渠道接入的交互式扫码能力——飞书一键创建与企微扫码创建、注册会话按发起者绑定与身份隔离（互不取消且凭据只回发起者）、扫码结果落入创建表单、扫码与手工填写两入口等价、扫码不绕过既有写入契约。
- `external-identity-binding`: 外部 IM 身份到账号的绑定维护——平台与租户双入口、租户侧以成员标识在租户内解析且跨租户按不存在拒绝、待绑定尝试的点选绑定与可见范围、绑定写入沿用既有校验/唯一性/审计。

### Modified Capabilities

- `tenant-channel-configuration`: 渠道实例归属以实例自身为准；绑定 Agent 可缺省并回落该租户默认智能体（写入侧现已允许空绑定，本变更只补入站解析）；创建期最小必填凭据集；接入形态与热生效（含对该能力「凭据轮换」要求的标题 RENAMED 与「渠道页未开放时」要求的修订）。该能力的主规范由在途 change `tenant-owned-message-channels` 归档后进入，本 change 声明归档顺序前置。
- `tenant-resource-isolation`: 修订「首期消费者按明确入口允许或关闭」中「外部通道关闭」的边界——开放渠道接入与入站；并明确入站租户锚点 MUST NOT 回退到全局默认智能体。

## Impact

- **代码**：`channel/web/web_channel.py`（`FeishuRegisterHandler` 会话绑定、实例热重启复用、外部身份路由注册）、`channel/web/admin_handlers.py`（创建期必填集校验、租户侧绑定处理器、待绑定列表处理器）、`channel/channel_instances.py`（`ChannelInstance` 增加实例归属、最小必填集声明）、`channel/external_identity.py` 与 `channel/chat_channel.py`（入站锚点与预检、未绑定尝试登记）、`agent/routing.py`（缺省回落到租户默认）、`auth/service.py`（租户侧绑定 API、待绑定尝试存取）、`auth/http_policy.py`（策略表）、`channel/web/static/js/console.js` 与 `chat.html`、`channel/web/static/js/identity-admin.js`、`channel/web/static/css/console.css`（租户视图重做与绑定弹窗）。
- **规范/测试**：`tests/test_external_im_gate.py`、`tests/test_tenant_channel_inbound_closure.py`、`tests/test_tenant_channel_inbound_isolation.py`、`tests/test_tenant_channel_http.py` 的入站断言随之修订；新增注册会话安全测试、外部身份绑定服务/HTTP 测试（`tests/test_external_identity_binding_admin.py`、`tests/test_external_identity_http.py`）、飞书发送者昵称解析测试（`tests/test_feishu_sender_name.py`）与前端 DOM 契约测试（`tests/test_external_identity_frontend.cjs`、`tests/test_i18n_external_identity_keys.cjs`）。
- **数据**：新增一张表 `external_identity_attempts`（迁移 9，主键为三元组）用于承载「待绑定尝试」，其行在绑定成功时删除、无外部引用；迁移 10 为该表增列发送者昵称、消息预览与群聊标记（`ALTER TABLE ... DEFAULT`，旧行可读），使尝试可被辨认而非只留一个 `open_id`。除此以外不新增表、不改变既有列语义、不改动既有数据。**这是对前置约束「不新增表」的一处有意例外**：尝试记录在解析到账号之前就已产生（这正是它存在的理由），既有任何表都无法承载「无 user_id 的三元组」而不改变其列语义。
- **API**：`/api/feishu/register` 由 `closed` 改为可用；新增 `/api/tenant/members/:id/external-identities`（GET/POST/DELETE）与 `/api/tenant/external-identity-attempts`（GET），平台侧新增 `/api/platform/external-identity-attempts`（GET）；`/api/tenant/channels*` 的请求/响应字段不变；`/api/my/channels*` 属后续批次，不在本 change。
- **依赖**：`tenant-owned-message-channels`（已实现，待归档）与 `open-database-runtime`（仍 active）。本 change 继承其 10.8 启用门槛，MUST NOT 提前声明能力「已可用」。
