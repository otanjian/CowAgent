## 1. 前置检查与顺序约束

- [x] 1.1 确认前置 change `tenant-owned-message-channels` 的实现与证据已齐备（其 65 个任务已勾选），并记录其归档顺序约束：本 change 的启用门槛继承其 10.8 条款，`open-database-runtime` 归档前不得声明能力「已可用」
- [x] 1.2 确认 `open-database-runtime` 的切片 4.x（入站身份绑定）与 7.x（凭据存储与注入）验收证据仍有效；未满足时在本 change 的 `evidence.md` 中显式记录为未满足
- [x] 1.3 核对 `identity_mode=database` 下多 worker 被拒绝（`reject_multi_worker_identity`）仍成立，据此确认注册会话可留在进程内、只需正确绑定归属
- [x] 1.4 登记**归档顺序硬依赖**：本 change 的 `tenant-channel-configuration` delta 使用 MODIFIED/RENAMED，其目标规范当前只存在于在途 change `tenant-owned-message-channels`，主规范库 `openspec/specs/` 尚无该文件；归档器要求 MODIFIED/RENAMED 目标必须已存在，且该错误**不会在 `openspec validate` 阶段暴露**。确认「先归档 `tenant-owned-message-channels`，再归档本 change」的顺序，否则本 change 不得归档

## 2. 注册会话绑定发起者（P0）

- [x] 2.1 RED：注册会话归属的单测——以句柄为键记录发起者身份与作用域；非发起者查询同一句柄不得取得凭据，且响应不指示该会话是否存在
- [x] 2.2 RED：注册会话**互不取消**的单测——身份 A 的进行中会话在身份 B 启动新注册后仍可继续推进；B 的启动 MUST NOT 取消或覆盖 A 的会话（现状 `_start_register_thread` 会取消上一个会话，需改为按身份隔离）
- [x] 2.3 RED：凭据一次性返回的单测——首次查询返回 `app_id`/`app_secret`，再次查询与之后查询均不返回凭据（此为现状已有行为，测试用于锁定不回归）
- [x] 2.4 RED：终态单测——取消、过期、失败三种终态各自返回明确结果且不含凭据
- [x] 2.5 改造 `channel/web/web_channel.py` 的 `FeishuRegisterHandler`：把进程级 `_state` 改为以不透明句柄为键、携带发起者身份与作用域的会话记录，并使启动新会话不再取消他人会话，使 2.1–2.4 转 GREEN
- [x] 2.6 在 `auth/http_policy.py` 中把 `/api/feishu/register` 由 `closed` 改为可用策略，并补登记**当前缺失的 `GET` 方法**（表内只登记了 POST，而 handler 同时实现 GET 与 POST，故 GET 现被完整性闸门拦为 405），满足路由完整性闸门
- [x] 2.7 验证 database 模式下未登记方法与未开放路由仍分别返回 405 与 503（不因本次开放而放宽）
- [x] 2.8 前端改造：扫码流程携带并使用会话句柄；未开放或不可用时按原因给出说明且不发起请求或轮询
- [x] 2.9 确认凭据不进入 URL、浏览器本地存储、日志与审计明细；补充一次针对响应体与日志的明文扫描断言

## 3. 渠道接入 UI 与既有形态对齐（P1）

- [x] 3.1 抽取可复用的渠道卡片渲染（图标、状态点、断开、Tab、凭据表单、保存），平台视图改为调用该函数并保持既有 DOM 契约不变
- [x] 3.2 以既有前端契约测试（`.cjs`）锁定平台视图不回归：卡片结构、状态文案、扫码 Tab 行为
- [x] 3.3 RED：租户视图的 DOM 契约测试——卡片形态、飞书扫码/手工两入口、企微扫码入口、保存按钮存在性
- [x] 3.4 把 `loadTenantChannelsView` / `renderTenantChannels` 改为使用共享卡片与 Tab 形态，使 3.3 转 GREEN
- [x] 3.5 实现扫码结果预填创建表单：只预填、不落库；取消或离开表单后客户端不再保留凭据
- [x] 3.6 前端失败原因分类：区分「无权限」「尚未开放」「请求被拒」，替换通用失败文案。本项实现的是对既有 requirement「渠道页在消费者未开放时不进入持续加载状态」的 MODIFIED 修订，不新增独立要求
- [x] 3.7 三语（zh / zh-Hant / en）补齐**本次新增**的文案键（失败原因分类、归属提示等）；既有 `feishu_mode_scan` / `feishu_mode_manual` / `wecom_scan_btn` 已三语齐备且可复用，无需新增，纳入既有 i18n parity 测试即可
- [x] 3.8 核对企微机器人扫码 SDK 的 `source` 常量与 database 模式可用性；不可用时保留手工填写入口并给出说明（design.md 未决参数）。取值范围限于 `wecom_bot`（企微智能机器人）；被显式延后的 `wechatcom_app`（企微自建应用）不在本项范围内
- [x] 3.9 （实施后发现并修复）扫码结果在提交时被抹掉的缺陷：密钥输入框按策略恒为空，而 `submitTenantChannel` 以输入框取值**替换**草稿凭据，导致扫码得到的一次性密钥被丢弃、创建被「缺少必填项」挡下（扫码显示成功却永远不落库）。改为在草稿之上**合并**，并在密钥框下显示「已通过扫码获取」提示；补齐 `tests/test_tenant_channel_card_frontend.cjs` 的两条 RED 测试（扫码密钥必须随提交送达、编辑时不重填不得清空已存密钥）
- [x] 3.10 （实施后再次发现并修复）「扫码显示成功但列表仍无记录」的第二重原因：创建时**渠道名称**未标必填、前端不校验，空名称被服务端以 400 `bad_request` 拒绝，而前端把该码统一渲染为笼统的「提交内容不合法」，操作员无从知道是名称缺失——表现为「扫码成功却什么也没发生」。名称在创建时标为必填并在提交前本地校验，给出 `tenant_channel_error_display_required` 的三语提示
- [x] 3.11 （实施后新增）把「扫码 → 保存」整条链路钉成一条前端集成测试：扫码句柄写入草稿、App ID 输入框回读、密钥框留空、提交后请求体必须同时带上 `feishu_app_id` 与一次性 `feishu_app_secret` 及名称
- [x] 3.12 （实施后新增）租户渠道写入被拒时在服务端留痕：`_reject_channel_write` 以 WARNING 记录 action/tenant/user/原因/状态码。被拒的写入既不落库也不入审计，此前与「请求根本没发出」在日志上无法区分
- [x] 3.13 扫码成功即自动落库：服务端 `FeishuRegisterHandler._poll_payload` 在 `done` 时签发一次性授权凭据（新模块 `auth/scan_authorization.py`，绑定发起者/作用域/渠道类型，短时效、一次性、校验与消费分离），前端 `autoPersistScannedTenantChannel` 据此免密码完成创建；渠道名称由 `tenantChannelAutoName` 从渠道类型与应用标识末四位派生，不再要求操作者输入；扫码触发的重渲染前先把名称与智能体选择回读进草稿，使「先选智能体再扫码」不被静默重置为缺省
- [x] 3.14 服务端授权分支：`create_tenant_channel_instance(scan_ticket=...)` 经 `_require_channel_write_authorization` 接受「近期密码或该次扫码凭据」二者之一；凭据在事务提交后才消费，写入因名称冲突等被拒时不消耗凭据；管理权限校验独立于凭据，持凭据的非管理员仍被 403
- [x] 3.15 密码收集改用页面内真实控件：`askRecentPassword` + `recentPasswordDialogHtml` 取代 `submitTenantChannel` / `toggleTenantChannel` 中的 `window.prompt`。原生对话框可被浏览器抑制并静默返回空值，此前表现为「扫码成功却毫无反应」的 401；取消与空密码现在可区分，空密码在本地即被拦下，被拒的凭据会被丢弃以便重试时改用密码
- [x] 3.16 失败文案区分「输入有误」与「部署未就绪」：`tenantChannelWriteErrorKey` 新增 `credential_crypto` 映射，传输失败改用独立的 `tenant_channel_error_network`。此前 500 `credential_crypto` 被渲染为笼统的「提交内容不合法」，使「服务端未配置主密钥」这类重试永远不会成功的故障看起来像操作者填错了值
- [x] 3.17 部署侧补齐 `COW_CREDENTIAL_MASTER_KEY`：本机服务由 launchd（`~/Library/LaunchAgents/com.cowagent.app.plist`，`KeepAlive`）托管，其 `EnvironmentVariables` 此前只设 `PYTHONUNBUFFERED`，故 `auth/crypto.py` 按设计「无密钥即拒绝」使凭据写入必然失败。已生成 32 字节随机会话密钥写入 plist（权限 600，原文件已备份），并在 `~/.cowagent/credential_master_key`（600）留一份备份以防主密钥丢失导致已存凭据不可解密；`launchctl bootout` + `bootstrap` 重新加载后以真实密钥验证了加密往返与「票据→创建→加密落库→掩码列表」整链

## 4. 保存后即时生效（P1）

- [x] 4.1 RED：实例热生效的单测——创建或编辑启用中的实例后，运行中的通道按新凭据重建，且未变更时不触发重启
- [x] 4.2 复用既有热重启路径（按解密后的实例凭据构造 `ChannelInstance` 并调用 `ChannelManager.restart`），使 4.1 转 GREEN
- [x] 4.3 按实例串行化重启，避免同一实例并发保存导致的重复启停
- [x] 4.4 RED：启动失败的单测——凭据无效或网络失败时保留实例与凭据版本历史，返回可诊断原因
- [x] 4.5 前端把「需维护窗口重启」的表述改为即时生效语义，并在启动失败时标明「尚未生效」与原因
- [x] 4.6 验证回滚路径：停用实例即可行为回退，实例记录与凭据版本历史均保留
- [x] 4.7 （实施期新增）把即时生效接到租户侧三条写路径（创建、编辑、启停）并把运行结果随响应返回（`runtime`），使 4.5 的「尚未生效 + 原因」有数据来源；补齐 `tests/test_tenant_channel_http.py` 的写路径 RED 测试

## 5. 创建期最小必填凭据集（P1）

- [x] 5.1 在 `channel/channel_instances.py` 声明 `REQUIRED_CREDENTIAL_KEYS`，与既有 `CREDENTIAL_KEYS` 并列；先只覆盖已核实类型（飞书、QQ、Telegram、Slack、Discord、企微机器人）
- [x] 5.2 RED：最小必填集校验的单测——缺少任一必填键时创建与轮换均被拒且不落库实例或凭据
- [x] 5.3 在 `auth/service.py` 的凭据校验路径接入最小必填集判定，使 5.2 转 GREEN；保留既有「已提供字段非空」校验
- [x] 5.4 RED：兼容场景单测——既有不完整实例不被自动删除或改写，但其启动失败原因可诊断，且后续编辑必须补齐必填集
- [x] 5.5 前端据同一声明标注必填字段并给出缺项提示，避免前后端各写一份必填列表
- [x] 5.6 前端测试：缺失必填项时提交被拒且已填内容不被清空
- [x] 5.7 （实施期新增）轮换改为在已存凭据包之上合并后再校验：控制台承诺「密钥留空表示保持原值」，而原实现整体替换凭据包，只重填密钥会静默丢掉 App ID；最小必填集因此按「合并后的生效集合」判定

## 6. 入站租户锚点与 Agent 缺省（P2）

- [x] 6.1 `ChannelInstance` 增加实例归属字段，并在启动合成（`load_tenant_channel_instances`）时从实例登记行带入；`stamp_instance_context` 一并打戳
- [x] 6.2 新增按 `instance_id` 反查实例归属的内部查询；声明其只供启动与入站解析使用、不经 HTTP 暴露
- [x] 6.3 RED：入站锚点单测——租户取自实例归属而非所绑定 Agent 的绑定；实例绑定与实例归属不一致时以实例归属为准
- [x] 6.4 改造 `channel/external_identity.py` 的 `resolve_actor_for_context` 接受实例归属参数，使 6.3 转 GREEN；MUST NOT 采信 context 中的自报租户
- [x] 6.5 RED：Agent 缺省单测——`agent_id` 为空时解析到该实例所属租户的默认智能体；该租户无可用 Agent 时拒绝且不调用模型
- [x] 6.6 接入既有 `IdentityService.resolved_default_agent_id`，使 6.5 转 GREEN；确认入站路径不回退到全局默认 Agent
- [x] 6.7 RED：跨租户与成员校验回归单测——他租户成员、未绑定发送者、受限账号仍被拒且不调用模型
- [x] 6.8 修订既有断言：`tests/test_external_im_gate.py`（`TENANT_UNBOUND` 语义）、`tests/test_tenant_channel_inbound_closure.py`、`tests/test_tenant_channel_inbound_isolation.py`、`tests/test_tenant_channel_http.py`
- [x] 6.9 验证会话仍按发送者区分：同一实例下两个发送者各自独立会话，互不串扰
- [x] 6.10 端到端闭环：未绑定 Agent 的实例收到消息后，在本租户内以解析到的默认智能体执行（模型边界以记录器替代）

## 7. 验证、证据与文档

- [x] 7.1 运行受影响的既有测试套件（身份、渠道、前端契约、i18n parity）并记录结果
- [x] 7.2 编写 `evidence.md`：注册会话绑定、即时生效、最小必填集、入站锚点四组证据各自给出可复现命令与实际输出
- [x] 7.3 在 `evidence.md` 中显式记录启用门槛状态（`open-database-runtime` 与 `tenant-owned-message-channels` 的归档情况），未满足时明确写「已实现、待归档」
- [ ] 7.4 更新受影响的既有规范正文（`tenant-channel-configuration`、`tenant-resource-isolation`）并在归档时合并本 change 的 delta；`tenant-resource-isolation` 已在主规范库可直接合并，`tenant-channel-configuration` 须先由 `tenant-owned-message-channels` 归档生成主规范文件后才可合并
- [x] 7.5 归档前复核 delta 与基线的标题一致性：确认 `凭据轮换与撤权…` 的 RENAMED 指令（FROM 旧标题 / TO 新标题）与基线标题逐字匹配，且各 MODIFIED 块已含基线全部 scenario（否则归档器会报 header 不匹配或拒绝丢弃 scenario）
- [x] 7.6 复核不应改动的内容未被改动：不改既有列语义、不改内置角色默认权限集、不触碰 `team.json` 既有形态。**例外（已在 proposal 中声明）**：第 8 组新增了 `external_identity_attempts` 一张表（迁移 9），因为它承载的是「尚未解析到账号的三元组」——这正是它存在的理由，既有任何表若承载它都必须改变列语义；该表行在绑定成功时即删除，无外部引用，回滚只需停用登记调用
- [x] 7.7 记录显式延后项与后续切片入口：个人渠道层、微信扫码、团队 `members`、钉钉/微信最小必填集、`wechatcom_app` 多实例化

## 8. 外部身份绑定：平台与租户双入口（落地阻塞补齐）

来源：3.17 之后渠道已能接入，但真人发消息被拒为 `external_unbound`——系统缺少把外部 open_id 绑定到账号的任何入口，入站锚点因此无法由真人验证。需求原文：**「这个功能，平台管理员和租户管理员都能维护」「绑定必须显示『是谁』『哪个渠道』『哪个 open_id』」**。

- [x] 8.1 迁移 9：新增 `external_identity_attempts`（主键为三元组，含投递实例的租户/渠道/实例与累计次数），并声明该表与「不新增表」约束的关系
- [x] 8.2 RED：服务层测试（`tests/test_external_identity_binding_admin.py`）——租户管理员绑定/列出/解绑本租户成员、跨租户读写按不存在拒绝、传他租户 tenant 标识不得越权、普通成员被拒、平台路径未被放宽、三元组校验与冲突语义不变
- [x] 8.3 服务层：`_member_user_id_in_tenant`（租户内解析成员，NotFound 而非 Forbidden）、`bind/list/delete_external_identity_for_tenant`、`record_external_identity_attempt`、`list_external_identity_attempts`；删除路径从绑定行反查归属并复校租户
- [x] 8.4 服务层：绑定成功（平台侧与租户侧共用 `_bind_external_identity_row`）在同一事务内清除待绑定尝试，防止引导重复绑定
- [x] 8.5 RED：入站拒绝登记测试（`tests/test_external_im_gate.py`）——`external_unbound` 被登记且带投递实例；已授权入站不登记；非绑定类拒绝不登记
- [x] 8.6 接入 `chat_channel._preflight_external_inbound`：仅在 `external_unbound` 时登记，携带投递实例的租户；登记失败仅记日志，不影响拒绝提示送达
- [x] 8.7 HTTP：新增租户侧 `TenantMemberExternalIdentitiesHandler`（GET/POST）、`TenantMemberExternalIdentityHandler`（DELETE）、`ExternalIdentityAttemptsHandler` 与平台侧 `PlatformExternalIdentityAttemptsHandler`；租户处理器先经 `_require_tenant_admin`
- [x] 8.8 HTTP：`ROUTE_POLICY` 登记 4 条租户路由与 1 条平台路由；新增策略测试同时**固定既有的 `/api/tenant/members/{id}` 更新策略仍在**（新增嵌套子路由正是可能遮蔽或覆盖它的编辑）
- [x] 8.9 RED：HTTP 测试（`tests/test_external_identity_http.py`）——租户管理员绑定/列出/解绑本租户成员、跨租户 404、普通成员 403、缺租户选择 400、三元组校验与冲突、平台管理员仍可维护任意账号、纯租户管理员在平台路由 403、待绑定列表的可见范围与绑定后消失
- [x] 8.10 前端：`identity-admin.js` 新增共享弹窗（标题为账号名 · 用户名；已绑定列表显示渠道/应用/open_id；新增表单三个字段；待绑定列表点选填入）并暴露 `externalIdentitiesModalHtml`/`externalIdentityRoutes`/`externalIdentityAccountLabel` 供契约测试
- [x] 8.11 前端：成员行与平台账号行各加一个「外部身份」入口，两处调用同一 `openExternalIdentities`；可用路由面按**能力**判定（`externalIdentitySurface`：平台账号行恒为 platform；成员行按调用者是否为平台管理员分流），使平台管理员与租户管理员各有可用路径——按入口直接绑定路由面会让「平台管理员查看他租户成员列表」这一入口以 403 失败
- [x] 8.12 i18n：三语（zh / zh-Hant / en）补齐 25 个键，provider 标签按渠道代码映射并在未知代码时回落为原始代码而非空白
- [x] 8.13 前端契约测试（`tests/test_external_identity_frontend.cjs`，26 条）：入口与路由族对应且租户管理员身份下成员入口不触平台路由、**平台管理员看成员行改走平台路由**（否则该入口对平台管理员 403，需求「两者都能维护」不成立）、渲染含渠道/应用/open_id、标题为「是谁」、待绑定点选不发请求、待绑定行以昵称+消息预览+群聊标签呈现且缺证据时仍可读、空 open_id 本地拦截、解绑复用同一路由族、冲突给出专属文案、无账号标识时不打开空弹窗
- [x] 8.14 i18n 契约测试（`tests/test_i18n_external_identity_keys.cjs`，5 条）：代码调用的每个 `extid_*` 键在三语均存在、三语键集一致、provider 回落规则存在
- [x] 8.15 RED：待绑定行的可识别性测试——服务层（`tests/test_external_identity_binding_admin.py`）断言尝试记录带 `sender_name`/`message_preview`/`is_group`、重复尝试刷新为最新消息且空昵称不得覆盖已知昵称、证据可缺省；入站层（`tests/test_external_im_gate.py`）断言证据取自标准 `ChatMessage` 字段（渠道无关）、群聊被标记、非文本消息按类型摘要而非落库本地文件路径、超长消息截断、证据读取失败仍完成拒绝；飞书侧（`tests/test_feishu_sender_name.py`，7 条）断言 open_id→昵称查询成功/失败/无 token/已有昵称短路/失败不重试
- [x] 8.16 迁移 10：为 `external_identity_attempts` 增列 `sender_name`/`message_preview`/`is_group`（默认值保证旧行可读）；实测 v9→v10 升级与重复打开均幂等
- [x] 8.17 服务层：`record_external_identity_attempt` 接收并持久化证据，重复尝试用 `CASE WHEN excluded.x != ''` 刷新预览与补全昵称
- [x] 8.18 渠道无关提取：`channel/external_identity.attempt_evidence(context)` 从标准 `ChatMessage` 字段取昵称/预览/群聊标记，字段间**独立降级**（昵称失败不丢预览）；可选钩子 `resolve_sender_name()` 供只知道不透明 id 的渠道惰性补名
- [x] 8.19 飞书：`FeishuMessage.resolve_sender_name()` 经 contact API 取昵称，按作者进程内记忆化（含负结果，避免无 contact scope 时每条消息一次失败往返），失败/无 token 均回落空名
- [x] 8.20 前端：待绑定行以**昵称**领衔、附消息预览（单行折叠、HTML 转义）、群聊标签、累计次数；无昵称时回落「未识别昵称」，无预览时不渲染该行；open_id 仍保留为被绑定对象
- [x] 8.21 i18n：三语补齐 `extid_group_tag`/`extid_unnamed_sender`（不引入未使用的键）


> 7.4 的阻塞说明：正文合并必须由 `openspec archive` 完成，而归档顺序硬依赖（先 `tenant-owned-message-channels`，再本 change）尚未满足（见 `evidence.md` §1.4、§7）。当前已把 delta 收敛到可直接归档的形态：标题逐字匹配、MODIFIED 覆盖基线全部 scenario（`evidence.md` §7 给出实测输出）。
