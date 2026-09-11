## 1. 范围与不在范围内

**Context。** `identity_mode=database` 下，渠道配置是**实例级**的：凭据以明文存于 `team.json` 的 `channel_instances[].credentials`，配置接口 `/api/channels` 在策略表里登记为 `closed`（实测 503 `database_unavailable`），且策略表只登记了 GET 而 `ChannelsHandler` 的 `save`/`connect`/`disconnect` 三种动作用的是 POST。前端 `loadChannelsView()` 在 `status !== 'success'` 时直接 `return`，页面永久停在 Loading。同时 `_SIGNED_CONSOLE_PAGES` 把 `admin.channels` 标为 `scope: "platform"`，租户管理员在侧栏看不到该入口。

在途 change `open-database-runtime` 已交付入站身份绑定（`external_identities` + 入站按 `agent_bindings.tenant_id` 决定执行租户）与租户级加密凭据（`credentials` 表自带 `tenant_id`、AES-256-GCM、掩码、版本、轮换、审计），但其任务 7.2 明确把「注入到真实工具的消费方」留给后续 change。

**Goals**

- 租户管理员可在「消息渠道」中维护**本租户自有**的渠道实例与凭据。
- 租户渠道凭据按租户加密存放于身份域，**不落** `team.json`、日志、审计或响应明文。
- 入站消息只在该渠道实例所属租户内解析与执行，跨租户一律拒绝。
- 实例级（平台）与租户级两种作用域并存、分别标示、互不越界。
- 平台管理员的实例级渠道配置接口由不可达变为可用，未开放的交互式接入动作给出明确说明而非持续加载。
- 第一刀支持的渠道类型：飞书（企微自建应用因入站无法按实例分派而延后，见 §6）。

**Non-Goals**

- 不把 `/api/channels` 租户化，也不改变实例级渠道的既有契约与 `team.json` 形态。
- 不把实例级明文的 `channel_instances[].credentials` 迁入加密存储（既有现状，另立 change）。
- **不实现企微自建应用（`wechatcom_app`）的租户归属**：其入站是固定端口 + 固定路径的 webhook 且经单例取通道，按实例分派需要独立的入站路由设计，见 §6。
- 不为其他 provider 补入站身份标识；钉钉等维持既有边界。
- 不开放 `/api/feishu/register`、`/api/weixin/qrlogin` 等交互式接入流程。
- 不新增业务功能权限，不改变内置角色默认权限集合。
- 不引入运行开关（feature flag）。

## 2. 当前代码接入点

| 关注点 | 位置 |
| --- | --- |
| 页面登记与作用域 | `auth/service.py` 的 `_SIGNED_CONSOLE_PAGES`（`admin.channels` 现为 `scope: "platform"`） |
| 能力投影 | `auth/service.py` 的 `_console_pages_projection`（未被显式处理的页落入末尾 `available: False` 分支） |
| 凭据存储与授权 | `auth/service.py` 的 `_is_control` / `_credential_eligible` / `create_credential` / `list_credentials` / `resolve_credential`；加解密在 `auth/crypto.py` |
| 路由策略 | `auth/http_policy.py`：`/api/channels` = `closed`（仅 GET）；`/api/weixin/qrlogin`、`/api/feishu/register` = `closed` |
| 渠道配置接口 | `channel/web/web_channel.py`：路由注册、`ChannelsHandler.GET/POST`（`save`/`connect`/`disconnect` 与实例分支） |
| 渠道实例与凭据字段 | `channel/channel_instances.py`：`CREDENTIAL_KEYS`、`MULTI_INSTANCE_READY`、`ChannelInstance`、`resolve_channel_instances`、`upsert_instance` |
| 企微自建应用（**本切片不修改**，见 §6） | `channel/wechatcom/wechatcomapp_channel.py`（`@singleton`、固定端口 webhook）；消息侧 `channel/wechatcom/wechatcomapp_message.py` |
| 入站身份解析 | `channel/external_identity.py` 的 `resolve_actor_for_context`（租户取自 `svc.get_agent_binding(agent_id)["tenant_id"]`）；仅飞书与钉钉调用 `stamp_external_identity` |
| 启动解析 | `app.py` 的启动通道解析（经 `resolve_channel_instances`） |
| 前端 | `channel/web/static/js/console.js` 的 `loadChannelsView`、`_consolePageForView`、`_viewNavDenied`、`_applySidebarPermissions`；`channel/web/chat.html` 的 `data-view="channels"` 与 `#channels-content` |

## 3. 数据模型与凭据绑定

**D1：租户渠道实例落身份库。** 新增 `identity.db` 表 `tenant_channel_instances(id, tenant_id, channel_type, display_name, agent_id, active, version, created_by, created_at, updated_at)`，随 `auth/store.py` 的版本化迁移建立。

- 备选 A（否决）：在 `team.json` 的 `channel_instances` 上加 `tenant_id`。改动最小，但租户级数据落在跨租户共享文件里，多租户管理员并发写无事务与版本控制，且文件中的 `tenant_id` 不是可信授权来源。
- 备选 B（否决）：每租户独立 roster。当前无该概念，需重构 `agent.team`，改动面远大于收益。

**D2：一个实例一份凭据 bundle。** 每个实例的凭据以 **JSON bundle** 存为**一条** `credentials` 记录：`tenant_id` = 实例租户，`resource_kind = "channel"`，`resource_id = <instance_id>`，`name = "channel:<instance_id>"`，`ciphertext` = 该类型 `CREDENTIAL_KEYS` 子集的 JSON。

- 理由：`create_credential` 只接受单个 `secret` 字符串，而飞书需 2 个字段、企微自建应用需 5 个；一次解密即得整包，避免 5 行记录与名称唯一性的纠缠。
- `resolve_credential` 已校验 `resource_kind`/`resource_id` 匹配，凭据与实例的绑定完整性由既有代码保证，不额外加外键。
- 不新增凭据表、不新增加密实现；加密、掩码、版本、轮换、审计全部复用既有凭据能力。

**D3：授权复用 `_is_control`。** 平台管理员或该租户有效 `tenant_admin` 才可读写；列表只出掩码投影。**不新增功能权限**，故不改内置角色默认权限集合。

**唯一性：同租户允许同类型多实例**，仅要求显示名在同一租户内唯一（见 §11 已定稿）。因此约束是 `(tenant_id, display_name)` 而非 `(tenant_id, channel_type)`。

## 4. 系统侧凭据注入路径

**D4：新增内部注入方法，不复用带 actor 的 `resolve_credential`。** 渠道启动是无人交互路径，而 `resolve_credential` 要求 `actor_user_id` 并执行 `credential.use`/`_is_control` 校验。因此新增内部方法按 `tenant_id + instance_id` 解密本实例凭据：

- **不经 HTTP 暴露**，仅由启动与重载路径调用。
- 仍按 `tenant_id + instance_id` 双重取值，不提供「按 name 任意取」的入口。
- 明文只在调用栈内短暂持有，MUST NOT 打印、MUST NOT 缓存于模块级状态。
- 这正是 `open-database-runtime` 任务 7.2 预留的「消费方注入」；本 change MUST NOT 放宽 `resolve_credential` 的既有校验。

## 5. 启动合成与运行期

**D5：在启动解析处合成，`channel/` 层不硬依赖身份库。** database 模式下先取本实例所有启用中的租户渠道实例（含解密后的凭据），与 `team.json` 的实例级/legacy 实例合成为统一的 `ChannelInstance` 列表，再交给既有启动路径。

- `resolve_channel_instances` 接受可选的租户实例入参；身份库访问放在薄的适配层（沿用 `channel/external_identity.py` 的做法：函数内惰性 import，避免启动期循环依赖）。
- 身份库不可用或某实例凭据解密失败时：**不阻止 Web 控制台启动**，该实例不启动并记录可诊断原因（与 `database-runtime-consumers`「单通道启动失败不得阻止 Web 控制台启动」一致）。

## 6. 企微自建应用：延后（实现期修正）

原设计把 `wechatcom_app` 纳入可租户配置的渠道类型。**实现期核对代码后确认该设计不成立**，故本切片不实现，留给后续切片。保留本节作为结论与后续设计的输入。

**阻塞事实**

| 事实 | 位置 |
| --- | --- |
| `WechatComAppChannel` 是 `@singleton`，且仅实现 webhook 传输：固定路径 `/wxcomapp/`、固定端口 `wechatcomapp_port`（默认 9898） | `channel/wechatcom/wechatcomapp_channel.py` |
| 入站处理器 `Query.GET/POST` 通过 `WechatComAppChannel()` 取通道，恒为**单例**，无法把请求分派到某个租户实例 | 同上 |
| `create_channel` 先执行 `__init__`、**之后**才 `apply_instance()` 注入实例凭据，故凭据必须在 `startup()` 中读取（飞书即如此） | `channel/channel_factory.py`、`channel/feishu/feishu_channel.py` |
| 项目已有先例：`wecom_bot` 的 webhook 模式**明确拒绝**第二个实例，理由是固定端口；只有 websocket 传输声明为多实例就绪 | `channel/wecom_bot/wecom_bot_channel.py` |
| `wechatcom_app` 在本代码库没有长连接/websocket 传输 | 该文件仅实现 webhook + 出站 client |

**后果。** 若按原设计把 `wechatcom_app` 加入 `MULTI_INSTANCE_READY`，`create_channel` 会为每个租户实例构造实例对象，但它们抢同一端口与同一回调路径，而 webhook 又只会命中单例 —— 两个租户的企业应用会互相串。这是「看起来能用、实际路由到错租户」，比不实现更危险。

**本切片的替代做法（D9）。** 可租户配置的渠道类型**以「已就绪多实例」为闸门**：服务层校验 `channel_type ∈ MULTI_INSTANCE_READY`，未就绪的类型以 `bad_request`（400）拒绝并给出可操作原因，且不落任何实例或凭据行。因此本切片**不修改** `MULTI_INSTANCE_READY` / `CREDENTIAL_KEYS`。

**后续切片需先解决的设计问题**

1. 入站如何按实例分派：每实例独立端口（端口基数 + 冲突策略），还是共享端口 + 按回调路径/`corp_id` 分派。
2. 该分派对部署的要求：防火墙、反向代理、以及租户侧回调 URL 的配置方式。
3. 相应地需要在 `startup()` 中按实例读凭据，并把 `Query` 从单例取通道改为按实例解析。
4. 入站三元组 `stamp_external_identity(provider='wechatcom_app', issuer=<实例 corp_id>, subject=<来源成员标识>)`，其中 `issuer` MUST 取实例自身配置。

## 7. HTTP 边界与作用域投影

**D6：新增租户作用域端点，实例级接口保持平台专属。**

- 新增当前租户渠道端点（列表/创建/编辑/启停，含 `expected_version` 与近期密码校验），策略为 `tenant` + 权限。
- `/api/channels` 由 `closed` 改 `platform`，并**同时登记 GET 与 POST**（现仅登记 GET，POST 会被路由完整性闸门拒绝）。生效后平台管理员的实例级页面由 503 变为可用。
- `/api/feishu/register`、`/api/weixin/qrlogin` 维持 `closed`；前端对未开放动作显示明确说明。

**D7：单一页面键，投影按操作者报相对作用域。** 保留 `admin.channels` 一个键，不新增第二个页面键（页面本就同一个，且既有 `scope` 字段无法表达双作用域）。投影对该键显式处理：平台管理员报 `scope: "platform"`，租户管理员报 `scope: "tenant"`，两者在各自作用域内可用。前端据 `scope` 渲染不同形态。

- 这同时修正 `_consumer_availability` 把 `channels` 报为可用、而配置接口实际 503 的错位：开放后二者一致；未开放的交互式动作走 D8。

**D8：未开放呈现。** `loadChannelsView` 失败分支 MUST 渲染明确文案并区分「无权限」与「尚未开放」，MUST NOT 以持续加载状态代替。

## 8. 前端与 i18n

- 按 `scope` 渲染两种形态：平台管理员沿用实例级视图（列表 + 保存/启停 + 未开放的接入动作说明）；租户管理员渲染本租户实例列表与新建/编辑表单（渠道类型、显示名、绑定 Agent、凭据字段、启用开关），凭据**只写不读**，列表显示掩码。
- 新建/编辑表单需对「写入被服务端拒绝」给出可操作原因（密码强度、租户内同名冲突、Agent 不属于本租户、版本冲突 409），并保留操作者已填字段。
- 三语（zh / zh-Hant / en）补齐键，纳入既有 i18n parity 测试。

## 9. 与既有规范及在途 change 的关系

- **只新增能力，不修改既有规范。** 经复核，`open-database-runtime` 的三个 delta 已分别把通道入口的可达性、按权限真实执行、验收后开放写入规范；`console-navigation-availability` 禁止的是租户管理员取得**全局**凭据，与租户自有凭据无冲突。若本 change 再改这些需求，会与在途 delta 互相覆盖。
- **归档顺序强制**：`open-database-runtime` MUST 先归档，本 change 依赖其 `credential-management` 与 `external-identity-binding` 进入主规范。
- 本 change 的 `admin.channels` 作用域调整属投影实现，不改规范文本。

## 10. 迁移与恢复

- **迁移**：新增一次性版本化迁移建 `tenant_channel_instances`；不回填数据，不触碰 `team.json`、`credentials`、`credential_versions` 的既有行。legacy 模式不读取该表。
- **恢复**：需要回退时停用租户渠道实例即可（表与凭据版本历史保留）；已启动的租户渠道随维护窗口重启退出。
- **身份库故障**：租户渠道不启动，Web 控制台保持可用；不因故障回退 legacy 或以明文配置替代。
- **凭据轮换**：轮换后旧版本 MUST 不可解密（沿用既有凭据能力）；运行中的渠道在下一次启动（含维护窗口重启）以当前有效凭据运行，本切片不实现热重载（见 §11 已定稿）。

## 11. 实施参数

**已定稿**

1. **轮换生效方式 = 重启。** 凭据轮换/撤权后旧版本即不可解密；渠道在下一次启动（含维护窗口重启）时以当前有效凭据运行。**不**在本切片实现「显式重载渠道」动作（含并发控制与失败回退），该动作列为后续切片。
2. **租户内唯一性 = 允许同类型多实例。** 同租户可存在多个同类型实例（如同一租户两个飞书机器人）；仅要求**显示名在同一租户内唯一**，不要求「一租户一应用」。既有同名停用实例不影响新建。

**未决**（实现时定稿，且不得宣称已实现）

1. **凭据 bundle 的字段集合**：哪些字段参与加密（`app_id`/`corp_id` 等非机密字段是否一并加密）。
2. **租户渠道数量上限**及是否纳入既有配额能力。
3. **近期密码校验的时间窗**与复用既有实现的接入点。

**实现期已结论**

1. **`@singleton` 的绕过机制** = `common/singleton.py` 暴露的 `new_instance()`，由 `channel/channel_factory.py` 的 `_fresh()` 调用；仅对声明多实例就绪的类型启用。企微自建应用不使用该机制（其入站无法按实例分派，§6）。
2. **可租户配置的类型闸门** = `channel_type ∈ MULTI_INSTANCE_READY`；未就绪类型以 400 拒绝，`MULTI_INSTANCE_READY` / `CREDENTIAL_KEYS` 本身不改（§6 D9）。

## 12. 测试策略

- **服务层单测**：创建/编辑/启停、版本 409、掩码不含明文、租户内同名冲突、停用后重建同名、未就绪多实例类型被拒。
- **跨租户隔离专项**：A 租户管理员读不到/改不了 B 租户实例；绑定他租户 Agent 被拒；请求他租户实例不返回存在性。
- **凭据落点断言**：写入后 `team.json` 不新增任何租户密钥字段；响应与日志不含明文。
- **入站端到端**：飞书一条能解析到本租户成员并执行（模型可 mock）；未绑定发送者、他租户成员、受限账号均被拒且不调用模型。
- **HTTP/策略**：`/api/channels` 由 503 变为平台可用且 POST 可达；未开放动作返回明确说明。
- **前端契约与 i18n parity**：作用域渲染、表单校验、失败原因保留字段；三语键齐全。
- **mutation 检查**：对作用域判定与租户一致性校验做变异，确认测试能捕获。
- **证据**：`evidence.md` 记录命令、输出与结论；含一条本租户渠道的端到端闭环证据。

## 13. 未验收声明

本 change 的产物完成**不等于**实现完成。启用门槛为：`open-database-runtime` 已归档，且 §12 的跨租户隔离专项与入站端到端证据齐备。在此之前，租户渠道能力 MUST NOT 被表述为「已可用」；平台级 `/api/channels` 的开放也仅在其自身测试与未开放呈现验证通过后成立。
