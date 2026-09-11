## Why

**一、租户管理员看不到「消息渠道」。** `admin.channels` 在 `auth/service.py` 的 `_SIGNED_CONSOLE_PAGES` 里登记为 `scope: "platform"`，`_console_pages_projection` 对租户管理员返回 `available=false` / `read_allowed=false`，`_applySidebarPermissions` 因此把该入口隐藏。这是既有设计的有意结果（渠道配置是实例级全局凭据），不是缺陷——但它挡住了「租户自带渠道」这个真实需求。

**二、该页在 database 模式下对所有人（含平台管理员）都打不开。** 三个事实叠加：

- `auth/http_policy.py` 把 `/api/channels` 登记为 `{"GET": {"policy": "closed"}}`，`closed` 的语义是「never reaches a downstream handler, regardless of admin status」。实测 `GET /api/channels` 返回 `503 {"code": "database_unavailable"}`。
- 策略表**只登记了 GET**，而 `ChannelsHandler` 的 `save` / `connect` / `disconnect` 三种动作用的是 **POST**（`web_channel.py` 的 `ChannelsHandler.POST`）。按「路由完整性」规则，POST 会被闸门直接拒绝，根本到不了 handler。
- 前端 `loadChannelsView()` 在 `data.status !== 'success'` 时直接 `return`，既不渲染也不报错，页面**永久停在 Loading**。

与此同时 `_consumer_availability()` 把 `channels` 报成 `available: true`，与上面三点的实际状态自相矛盾。这违反既有口径「未适配入口继续关闭并**返回可用的关闭原因**；`/auth/context` 不得将其标示为可用」。

**三、企业客户需要「各租户自带自有 IM 应用」。** 让本租户成员从**自有企业应用**（飞书应用）进来对话，消息只在本租户内以该成员权限执行。当前渠道配置是**实例级**的，凭据以**明文**存于 `team.json` 的 `channel_instances[].credentials`，既没有租户维度，也不符合已经交付的加密凭据设计。

> **企微自建应用延后（实现期核对代码后的修正）。** 原设计把 `wechatcom_app` 一并纳入租户配置。核对代码后确认该通道的入站是**固定端口 + 固定回调路径**的 webhook，且入站处理器通过单例取通道（`WechatComAppChannel()`），因此无法把请求分派到某个租户实例。项目自身已有先例：`wecom_bot` 的 webhook 模式明确拒绝第二个实例，理由是「固定端口」，只有 websocket 传输才多实例就绪。`wechatcom_app` 在本代码库没有长连接传输，故本切片**不**将其纳入可租户配置的渠道类型，改为以「已就绪多实例」作为闸门；企微的按实例入站分派（独立端口/路径）列为后续切片，需先补入站路由设计。飞书默认 `feishu_event_mode=websocket`，每实例各自建长连接，无此障碍。

**四、地基已就绪，只缺「消费方」。** 在途 change `open-database-runtime` 已交付租户级加密凭据（`credentials` 表自带 `tenant_id`、`resource_kind`/`resource_id`、AES-256-GCM、掩码投影、版本、轮换与审计）与入站身份绑定（`external_identities` + 由 `agent_bindings.tenant_id` 决定执行租户并强制发送者为本租户有效成员），但其任务 7.2 明确写着「注入到真实工具的消费方仍关闭，随对应消费方 change」。本 change 即该消费方。

## What Changes

- **新增租户级渠道实例表**：`identity.db` 新增 `tenant_channel_instances`（`id` / `tenant_id` / `channel_type` / `display_name` / `agent_id` / `active` / `version` / 审计字段），带版本化迁移。
- **凭据复用既有存储，不新建凭据体系**：每个租户渠道实例的密钥以 JSON bundle 形式存于既有 `credentials` 表，`resource_kind='channel'`、`resource_id=<instance_id>`、`name='channel:<instance_id>'`。加密、掩码、版本、轮换、审计全部复用 `open-database-runtime` 7.x。本 change MUST NOT 在 `team.json` 写入任何租户密钥。
- **服务层四个方法**（单事务：凭据 + 版本 + 实例 + 审计共同提交或回滚）：`create_tenant_channel_instance`、`update_tenant_channel_instance`、`set_tenant_channel_instance_active`、`list_tenant_channel_instances`。授权复用既有 `_is_control`（平台管理员，或该租户有效 `tenant_admin`），**不新增业务功能权限**。写入携带 `expected_version`，过期返回 409。列表只返回掩码投影，MUST NOT 返回明文。
- **新增系统侧凭据注入路径**：补上 7.2 预留的消费方——内部方法按 `tenant_id + instance_id` 解密本实例凭据，**不经 HTTP 暴露**，仅供渠道启动与热重载使用。
- **启动合成**：database 模式下把实例级/legacy 实例（`team.json`）与租户级实例（`identity.db`）合成为统一的 `ChannelInstance` 列表后再启动，`channel/` 层不引入对身份库的硬依赖（沿用惰性适配）。
- **租户可选渠道类型以「已就绪多实例」为闸门**：可创建的渠道类型限于项目内 `MULTI_INSTANCE_READY` 声明的类型。**不**把 `wechatcom_app` 加入该集合——它是固定端口 + 固定回调路径的 webhook 通道，入站无法按实例分派（理由见 Why 三）。尝试以该类类型创建会被拒并给出可操作原因。
- **HTTP 边界**：新增租户作用域端点（当前租户）；把 `/api/channels` 由 `closed` 改为 `platform` 并**同时登记 GET 与 POST**，使平台管理员的实例级页面真正可用。
- **交互式接入流程仍保持关闭**：`/api/feishu/register`、`/api/weixin/qrlogin` 维持 `closed`；前端 MUST 对未开放动作显示明确的「未开放」说明，MUST NOT 再出现永久 Loading。
- **投影与菜单按操作者相对作用域**：`admin.channels` 对平台管理员报 `scope: "platform"`（实例级视图），对租户管理员报 `scope: "tenant"`（本租户视图），两者都可用；能力投影与实际可用性 MUST 一致。
- **前端**：「消息渠道」页按作用域渲染两种形态；租户侧提供本租户渠道实例列表 + 新建/编辑表单（类型、显示名、绑定 Agent、凭据字段、启用开关），密钥**只写不读**（列表显示掩码）。三语 i18n 对齐。
- **近期密码校验**：租户侧渠道写入要求近期密码校验，与平台侧敏感写入口径一致。

### 与 PRD 的关系

- 需求基线按 `openspec/config.yaml` 指向的 PRD-01～12 v1.2。但仓库所称的 PRD 原文**当前仍缺失**（沿袭 `tenant-tabbed-editor` 与 `tenant-editor-batch-save-and-admin-create` 的记录），故本次行为基线取自 `openspec/specs/` 下既有规范与已交付的 `open-database-runtime` 切片 4.x/7.x。PRD 原文恢复后 MUST 复核两处口径：① 租户自带渠道是否要求「一租户一应用」唯一性约束；② 渠道实例是否允许租户自行选择绑定到非默认 Agent。
- **数据唯一归属**：租户渠道实例、其加密凭据、版本历史与审计**全部归属 `identity.db`**，由 `IdentityService` 在单事务内写入。`team.json` 只保留实例级/legacy 渠道的既有形态，MUST NOT 承载租户密钥。渠道所路由的 Agent 工作区仍归属该 Agent 的既有 workspace，本 change MUST NOT 触碰。
- **跨 change 依赖**：依赖**在途** change `open-database-runtime`（切片 4.x 入站身份绑定、7.x 凭据存储与注入、9.x 配额、10.x 审计）。该 change MUST 先归档：本 change 依赖其新增的 `credential-management`（加密存储、掩码、按租户与资源授权、按需注入与撤权失效）与 `external-identity-binding`（入站解析到有效成员）能力进入主规范，也依赖其已改写的三个边界需求作为可达性与开放依据。本 change MUST NOT 修改 `console-navigation-availability`、`tenant-resource-isolation`、`business-permission-catalog` 的既有需求，以免覆盖其 delta。若该 change 尚未归档，本 change 的实现可先行，但**归档与启用门槛**以其对应切片验收为前提，并在 `tasks.md` 中显式标注。

## Capabilities

### New Capabilities

- `tenant-channel-configuration`: 租户自有消息渠道实例的生命周期（创建/编辑/启停）、加密凭据绑定与掩码、实例与租户/Agent 一致性、入站消息按实例所属租户执行、平台级与租户级两种作用域并存互不越界，以及「租户可选渠道类型限于已就绪多实例的通道」这一闸门。

### Modified Capabilities

无。

不修改既有规范的理由（经复核在途 change `open-database-runtime` 后修正）：

- 该 change 的 `console-navigation-availability` delta 已把 `旧模式与未适配消费者维持边界` 改为「SHALL 允许聊天/文件/调度/**通道**等已适配入口按权限可达」，本 change 的导航可达性不需再改。
- 该 change 的 `tenant-resource-isolation` delta 已把外部通道改为「对应切片验收通过后 SHALL 按身份、资源授权、执行隔离、凭据、审批与配额开放」，本 change 正是该切片验收，不需再改。
- 该 change 的 `business-permission-catalog` delta 已明确「对已由独立运行开放规范验收的 Web 对话、文件、调度、**通道**与 OpenAI API，SHALL 允许按权限真实执行」，本 change 不需再改。
- `console-navigation-availability` 的 `平台租户和个人作用范围独立` 禁止的是租户管理员取得**全局**模型配置或凭据；本 change 交付的是**租户自有**凭据，不构成违反，故亦无需求变更。

若强行修改上述四个需求，会与在途 change 的 delta 互相覆盖并制造归档冲突，故本 change **只新增能力、不修改既有规范**。

## Impact

- **代码**
  - `auth/service.py`：新增租户渠道实例的四个服务方法 + 系统侧注入方法 + 迁移；复用 `_is_control`、`_tx`、`_audit_in_tx`、`auth/crypto.py`。
  - `auth/store.py`：新增 `tenant_channel_instances` 的版本化迁移。
  - `channel/web/admin_handlers.py`：新增租户作用域渠道端点（当前租户 + 近期密码校验）。
  - `auth/http_policy.py`：新增租户端点策略；`/api/channels` 由 `closed` 改 `platform` 并登记 GET+POST。
  - `channel/channel_instances.py`：支持传入租户实例；**不改** `MULTI_INSTANCE_READY` / `CREDENTIAL_KEYS`（企微自建应用延后，见 Why 三）。
  - `app.py`：database 模式下合成实例级与租户级实例。
  - `channel/web/static/js/identity-admin.js`、`console.js`、`console.css`：租户侧渠道表单、作用域渲染、未开放说明、i18n。
- **API**：新增租户作用域渠道端点（列表/创建/编辑/启停，含 `expected_version` 与近期密码校验）；`/api/channels` 由 503 变为平台可用（行为由「不可达」变为「可用」，属开放而非破坏）。
- **权限**：租户渠道配置限平台管理员与该租户有效 `tenant_admin`（沿用 `_is_control`）；MUST NOT 放宽既有成员/角色接口范围；MUST NOT 让租户管理员取得实例级全局凭据。
- **数据**：新增 `identity.db` 一张表 + 一次版本化迁移；复用既有 `credentials` / `credential_versions`。`team.json` 结构不变。
- **安全**：租户密钥 MUST 只以密文落 `identity.db`，MUST NOT 落 `team.json`、日志、审计或响应明文；列表只出掩码。跨租户访问 MUST 拒绝；实例的 `agent_id` MUST 属于同一租户。
- **兼容**：`team.json` 的实例级与 legacy 渠道行为 MUST 保持不变；`wechatcom_app` 等未就绪多实例的通道 MUST 维持其既有单实例行为，MUST NOT 因本 change 改变其凭据读取或入站路由。
- **测试**：单测（创建/轮换/启停/409/掩码不含明文/未就绪类型被拒）、**跨租户隔离专项**（读不到、改不了、不能绑他租户 Agent）、入站端到端（飞书一条，含未绑定发送者拒绝）、审计断言、前端契约与 i18n parity、mutation 检查、`evidence.md`。
- **feature flag**：不引入运行开关；能力随代码发布生效。database 模式下才启用租户渠道，legacy 行为不变。
