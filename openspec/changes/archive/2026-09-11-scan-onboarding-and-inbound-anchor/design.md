## Context

见 `proposal.md` 的 Why。本设计只记录塑造实现方式的当前代码事实与约束。

**当前代码接入点**

| 关注点 | 位置 | 现状 |
| --- | --- | --- |
| 飞书注册会话 | `channel/web/web_channel.py` 的 `FeishuRegisterHandler` | 进程级单例 `_state`；`POST` 明文回传 `app_id`/`app_secret`，返回后即清空（一次性已实现）；`GET` 启动新会话时会取消上一个会话；仅以 `_require_auth()` 判断「有人登录」，无归属校验 |
| 路由策略 | `auth/http_policy.py:216` | `/api/feishu/register` = `closed`，且表内**只登记 POST**；handler 同时实现 GET 与 POST，故 GET 被完整性闸门拦为 405，POST 在 database 模式下 503 |
| 入站身份解析 | `channel/external_identity.py` 的 `resolve_actor_for_context` | 租户取自 `get_agent_binding(agent_id)["tenant_id"]`；无绑定即 `TENANT_UNBOUND` |
| 实例绑定校验（写入侧） | `auth/service.py` 的 `_require_instance_agent` | 空 `agent_id` **已**被接受为「deliberately unbound」并返回 `""`；故写入侧无需改动 |
| 路由回落 | `agent/routing.py` 的 `AgentRouter.resolve_context` | `bound_agent_id` 为空时回落到**全局** `registry.default_agent_id` |
| 实例打戳 | `channel/channel.py` 的 `stamp_instance_context` | 只打 `bound_agent_id`/`instance_id`/`members`，**不带租户归属** |
| 凭据校验 | `auth/service.py` 的 `_validated_channel_bundle` | 只校验字段名合法 + 已提供字段非空，**不校验最小必填集**（`evidence.md` §10.2） |
| 热重启先例 | `channel/web/web_channel.py` 的 `_handle_instance_save` | 平台侧已按 `mgr.restart(inst)` 做到保存即生效 |
| 租户级写入 | `channel/web/admin_handlers.py` 的 `TenantChannelsHandler` | 纯落库，无热重启 |
| 租户视图 | `channel/web/static/js/console.js` 的 `loadTenantChannelsView` / `renderTenantChannels` | 简单行列表 + 通用凭据表单，无卡片、无 Tab、无扫码 |
| 平台视图 | 同文件的 `buildFeishuPanel` / `startWecomBotAuthInCard` | 已有扫码形态，可作对齐目标 |

**约束**：`identity_mode=database` 下 `closed` 路由 SHALL 返回 503；多 worker 部署在 database 模式被拒绝（`reject_multi_worker_identity`），因此注册会话状态可以留在进程内，只要**归属正确**。

## Goals / Non-Goals

**Goals:**

- 让租户级渠道接入的操作方式与既有 master 形态基本一致（卡片、Tab、扫码、保存即生效）。
- 消除注册会话的跨身份凭据泄漏，使开放扫码成为安全可交付的能力。
- 打通「不选智能体也能用」：入站租户锚点由实例归属决定，Agent 缺省时回落到该租户的默认智能体。

**Non-Goals:**

- 个人（用户自有）渠道层：`scope`/`owner_user_id` 列、`workbench.channels` 页面、owner-only 入站规则。已确认属后续批次。
- 微信扫码接入：登录态为进程级单实例，多用户会互相顶掉登录，保持平台专属并给出说明。
- 租户渠道的团队 `members`（`tenant_channel_instances` 无该列，需加列与迁移）。
- 把实例级 `team.json` 明文凭据迁入加密存储（既有 Non-Goal）。
- 新增功能权限或改动内置角色默认权限集。

## Decisions

**D1：注册会话绑定发起者，凭据只回发起者。** 每次注册生成不透明句柄，服务端状态以句柄为键并记录发起者身份与其作用域；查询时校验句柄归属，凭据返回一次后清除。

其中「凭据返回一次后清除」**已由现状实现**（`web_channel.py` 的 POST 在返回后即清空状态），本 change 的新增部分是**归属校验**与其必然连带的两点：其一，现状 `_start_register_thread` 在启动新注册时会取消上一个会话，多用户下会互相取消，会话必须按身份隔离而不是全局唯一；其二，现状是「谁先轮询谁取得」共享状态里的凭据，改后只有发起者能取得。

- 备选（否决）：保持单例并声明「仅单租户适用」——本 change 的目标正是多租户下开放该入口，前提不成立。
- 备选（否决）：改为独立会话存储/多 worker 共享状态——database 模式本身拒绝多 worker，引入该复杂度无收益。
- 备选（否决）：把注册会话纳入身份库事务——扫码是长时交互，不适合占用写事务；且凭据写入仍应走既有创建契约（D4）。

**D2：入站租户锚点改为渠道实例自身的登记归属。** 解析时按 `instance_id` **反查身份库**取得归属，MUST NOT 采信 context 中的自报租户。`ChannelInstance` 增加租户归属字段，`stamp_instance_context` 一并打戳，但打戳只用于路由与日志，授权判定以反查结果为准。

- 理由：这是「不选智能体也能用」与「跨租户不可越界」两条要求的共同前提；实例归属是可信来源，Agent 绑定不是。
- 备选（否决）：继续用 Agent 绑定推断租户并只把空值视为错误——无法表达「不选 Agent」，且 Agent 绑定被改绑时会把消息带向另一租户。

**D3：Agent 缺省时解析到该租户的默认智能体。** 复用既有 `IdentityService.resolved_default_agent_id(tenant_id)`（已实现「租户配置默认 → 租户共享 → 任一」的确定性顺序，且只读不写）。会话仍按发送者各自区分，因此语义上是「每个发送者各自的默认会话」。

现状边界：**写入侧已经允许空 `agent_id`**——`_require_instance_agent` 对空值直接返回 `""` 并注释为「deliberately unbound」，HTTP 层也原样透传。因此本项**不修改写入校验**，只补入站解析；实现时不应去「修」一个不存在的写入缺陷。

- 备选（否决）：新增「渠道默认智能体」配置表——与租户默认智能体语义重复，且会引入第二个需要维护的默认值。
- 备选（否决）：缺省时回落到全局默认 Agent——违反既有「不使用全局默认 Agent」的隔离要求。

**D4：扫码结果只预填表单，写入仍由创建动作完成。** 注册成功后凭据交给创建表单，实际落库走既有租户渠道写入契约（近期密码 + `expected_version` + 作用域校验 + 掩码）。

- 理由：避免为扫码开一条绕过校验的写路径；也让「扫码后放弃」天然不产生副作用。
- 备选（否决）：注册完成即直接落库——绕过近期密码与版本校验，且扫码失败/放弃需额外的补偿删除。

**D5：保存后即时生效复用既有热重启路径。** 用解密后的实例凭据构造 `ChannelInstance` 并调用 `ChannelManager.restart`，替换租户侧「需维护窗口重启」的表述。

- 理由：平台侧已有先例与实现，复用风险最低；这是与既有体验对齐的关键一步。
- 备选（否决）：维持维护窗口重启——与目标体验不符。
- 备选（否决）：新建并行启动通道——会与 `ChannelManager` 的既有幂等/去重逻辑冲突。

**D6：最小必填集以并列常量声明，并在创建与轮换时校验。** 在 `channel/channel_instances.py` 增加 `REQUIRED_CREDENTIAL_KEYS`，与既有 `CREDENTIAL_KEYS` 并列，服务层校验据此判断。

注意现状边界：`_validated_channel_bundle` 只校验「字段名在声明内」且「已提供字段非空」，因此缺 `feishu_app_secret` 只提交 `feishu_app_id` 也能落库（`evidence.md` §10.2）。本项是**新增校验**，不是修补既有校验的缺陷。

- 理由：校验点单一，前端表单可据同一声明推导必填标记，避免前后端各写一份。
- 兼容：既有不完整实例不被自动删除或改写，但其启动失败原因可诊断，且后续编辑必须补齐必填集。
- 备选（否决）：把必填性直接写进 `CREDENTIAL_KEYS` 结构——会改变既有字段契约与既有测试的语义。

**D7：未开放动作的呈现按原因分类。** 扩展前端的失败原因映射，区分「无权限」「尚未开放」「未适配/请求被拒」，扫码入口在不可用时直接给出说明并可禁用，而不是点下去只显示通用失败。该要求由既有 requirement「渠道页在消费者未开放时不进入持续加载状态」扩展而来，本 change 以 MODIFIED 方式修订该条，不另立新要求。

**D8：前端形态对齐通过抽取可复用卡片渲染实现。** 把平台视图已有的渠道卡片渲染抽成两处共用的函数，租户视图改为使用同一卡片形态（图标、状态点、断开、Tab、凭据表单、保存），差异只在作用域与可选动作。

- 理由：避免复制一份卡片逻辑后两端漂移；后续个人层页面可继续复用。
- 代价：触及平台视图的 DOM 结构，需以既有前端契约测试锁定不回归。
- 范围界定：本轮「企微扫码创建」指的是 `wecom_bot`（企微智能机器人，常量 `WECOM_BOT`，已在 `MULTI_INSTANCE_READY` 与 `CREDENTIAL_KEYS` 内）。被显式延后的 `wechatcom_app`（企微自建应用，无常量、无凭据声明）不在本轮范围——两者不可混为一谈。

## Risks / Trade-offs

- **[注册会话句柄被猜测]** → 句柄为不可预测的不透明值，一次性使用，且查询不反映会话是否存在（避免枚举与进度泄漏）。
- **[即时生效引入启停竞态]** → 按实例串行化重启；仅在实际变更时触发；失败时保留实例与凭据版本历史并给出可诊断原因。
- **[入站锚点变更破坏既有断言]** → 同批修订 `tests/test_external_im_gate.py`、`tests/test_tenant_channel_inbound_closure.py`、`tests/test_tenant_channel_inbound_isolation.py`、`tests/test_tenant_channel_http.py`，并在规范中显式修订。
- **[最小必填集拒绝既有不完整实例的编辑]** → 以兼容场景显式规定：不自动删除/改写，但编辑必须补齐。
- **[抽取卡片组件影响平台视图]** → 以既有前端契约测试（`.cjs`）作为回归护栏。
- **[切换 Agent 缺省语义后，租户没有可用 Agent]** → 返回固定提示且不调用模型，不回落全局默认。
- **[本 change 与在途 change 的顺序耦合]** → 继承 `tenant-owned-message-channels` 的归档约束与其 10.8 启用门槛，交付表述不得提前声明可用。

## Migration Plan

- **数据**：无表结构变更、无列语义变更、无数据迁移。
- **部署**：不需要维护窗口；既有实例在升级后继续按原凭据运行。
- **归档顺序前置（硬依赖）**：本 change 的 `tenant-channel-configuration` delta 使用 MODIFIED/RENAMED，而该规范**当前只存在于在途 change `tenant-owned-message-channels` 的 delta 中，主规范库 `openspec/specs/` 尚无该文件**。归档器要求 MODIFIED/RENAMED 的目标规范必须已存在（否则报 `target spec does not exist; only ADDED requirements are allowed for new specs`），且该错误**不会在 `openspec validate` 阶段暴露**。因此：
  - MUST 先归档 `tenant-owned-message-channels`，再归档本 change；
  - 顺序未满足时，本 change 只能停留在「已实现、已验证」状态，不得归档。
- **回滚**：
  - 关闭扫码：把 `/api/feishu/register` 在策略表中改回 `closed` 并回滚对应前端入口。
  - 回滚入站锚点：需回退代码（属行为变更，无数据回退路径）；因此建议先以租户渠道的入站端到端测试锁定行为再放开。
  - 即时生效：回退为仅落库不影响既有数据。
- **启用门槛**：继承前置 change 的顺序约束；在 `open-database-runtime` 归档前，交付表述为「已实现、已验证，启用待归档」。

## Open Questions

- 企微机器人扫码 SDK 在 database 模式下的 `source` 常量与可用性需在实施期核对（不影响本设计的接口形态）。
- 钉钉与微信的最小必填凭据集待核（`evidence.md` §10.2 已标注为待补），本 change 只对已核实类型声明必填集。
- 注册会话句柄的载体（请求体字段名、是否复用既有 `cancel` 动作语义）属实现参数，不影响本设计的归属与隔离要求。
