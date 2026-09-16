# 7 渠道类型可准入性：消息渠道页 8 个类型中 5 个的入站结构性不可达

本文件记录一次**只读**复核：成员/管理员的「消息渠道」类型目录给出 8 个类型，但其中 5 个的适配器
从不打外部身份戳，其入站在任何绑定之前即被固定拒绝，而控制台仍把它们报成「就绪」，并在公共归属下
报到「已连接」。复核方法与 7.1 一致：只读代码走查 + 只读进程内直调（不启服务、不发消息、不写库）。

> **时点声明**：写作期间 `channel/web/*`、`auth/*`、`channel/channel_instances.py`、`docs/design/*`
> 正被其它子代理并发编辑，下述行号为**读取时刻**快照，会漂移。所有引用同时给出函数名/常量名，
> 并以 `evidence/7-1-runtime-preflight.md`（下称 7.1）为凭据与提供方清单的唯一出处，本文件不重复。

## 0. 结论摘要

1. **缺陷成立且可达**。「类型可选、可保存、可连接、入站恒拒、控制台无痕迹」五件事同时为真。
   最强可观测形态**不是**成员本人连接，而是**公共归属**（`scope='tenant'`）连接：它不受 7.1 §0.3
   的个人运行开关约束，会真的把适配器拉起来并把状态报成 `connected`（本文件 §3.2 实证），
   而入站从第一条消息起就被拒。
2. **类型清单的唯一真值**是 `channel/channel_instances.py` 的 `MULTI_INSTANCE_READY`（8 项，
   `:100`），成员面再经 `personal_channel_ready()`（`:1224`）的 `ready` 过滤。
3. **根因是一处「声明与代码不符」**，不是缺过滤：`personal_channel_ready` 的 docstring 自认
   「一个类型只有在**其入站路径能按实例证明发送者**时才就绪」，而实现只检查
   `ctype in MULTI_INSTANCE_READY`；`MULTI_INSTANCE_READY` 的事由是「能否同时跑多份实例」
   （`:97-99` 注释），与「能否认出发送者」不是同一件事。8 项中有 5 项从不调用
   `stamp_external_identity`。
4. **第一拒绝点**：`channel/chat_channel.py` 的 `_preflight_external_inbound`（`:424`）在
   `:511-519` 判 `not context.get("external_identity")` → `deny_notice(UNSUPPORTED_CHANNEL)`。
   该分支**早于**绑定码兑换、个人路由解析与 `resolve_actor_for_context`，因此**不落
   `external_identity_attempts`**，控制台待绑定列表里看不到任何「谁来敲过门」。
5. **不是模式差异**：`is_database_mode()` 恒为 `True`（`:79-86`），显式 `identity_mode=legacy`
   在启动时被拒（`common/startup_hooks.py::_identity_mode_consistency`），非 web 入站必过身份闸门。
   **今天不存在任何一个能让这 5 类提供方工作的模式**（含 roster/单机 `team.json` 名册，见 §4.3）。
6. **推荐修法**：把「入站身份戳能力」提升为**一处显式声明**，并让目录候选、创建、启用共用它
   （§6 的 (a)+(c) 合成，谓词写成 mode-aware 形式）。不推荐 (d)（真实成本见 §6.4）；
   不推荐纯 (c)（就是本 change 反复要消掉的「可点但被拒」形状）。
7. **规范未直接裁定，但给出了取向**：`tenant-channel-configuration` spec `:69`
   「界面不得展示**提交**必被拒绝的类型或目标」不覆盖本缺陷（这 5 类的**提交并不被拒**）；
   `console-navigation-availability` spec `:121`/`:135-137`「目录、配置和执行状态分别判定，
   运行未开放不得关闭已获权目录」指向 (b)。两处的分歧由「不是同一个事实」化解：
   那两条讲的是**执行状态（运行开关）**，本缺陷讲的是**目录准入性（能力缺席）**，见 §7.2。

## 1. 类型清单的来源（Q1）

**是服务端下发，不是前端硬编码；服务端的唯一真值是 `MULTI_INSTANCE_READY`。**

      58|CREDENTIAL_KEYS: Dict[str, tuple] = { ... }        # 可存字段白名单（8 类）
      97|# Channel types that actually support running more than one instance today.
     100|MULTI_INSTANCE_READY = frozenset({
     101|    const.FEISHU, const.DINGTALK, const.QQ, const.TELEGRAM,
     105|    const.SLACK, const.DISCORD, const.WEIXIN, const.WECOM_BOT,
     109|})                                                    # ← 8 项的唯一出处
    1187|PERSONAL_READY_CHANNEL_TYPES = frozenset(MULTI_INSTANCE_READY)

链路（成员面）：

| 环节 | 位置 | 事实 |
| --- | --- | --- |
| 声明 | `channel/channel_instances.py:100` `MULTI_INSTANCE_READY` | 8 个类型 |
| 目录构造 | 同文件 `:1133` `tenant_channel_types()` | 遍历 `MULTI_INSTANCE_READY`，跳过无 `CREDENTIAL_KEYS` 的类型，下发 `channel_type/label/icon/credential_fields`（**无** `ready` 字段） |
| 个人就绪 | 同文件 `:1224` `personal_channel_ready()` + `:1249` `personal_channel_types()` | 给每个 entry 追加 `ready`/`reason` |
| HTTP 下发 | `channel/web/admin_handlers.py:1060-1080` `TenantChannelsHandler.GET` | `types = personal_channel_types() if owner_only else tenant_channel_types()`（`:1074`），随 `GET /api/tenant/channels` 以 `channel_types` 下发（`:1076`） |
| 前端接收 | `channel/web/static/js/console.js:14794` | `tenantChannelTypes = data.channel_types || []` |
| 前端收窄 | 同文件 `:14496-14500` `tenantChannelTypeChoices()` | `tenantChannelSelfScope ? tenantChannelTypes.filter(spec => spec.ready) : tenantChannelTypes` |
| 渲染 | 同文件 `:14655-14660` | `<select id="tenant-channel-type">` 由 `tenantChannelTypeChoices()` 生成 |

本机只读直调（不启服务）的实际输出，确认「8 项且全部 ready=true」：

```
$ .venv/bin/python -c "..."
MULTI_INSTANCE_READY = ['dingtalk','discord','feishu','qq','slack','telegram','wecom_bot','weixin']
PERSONAL_READY_CHANNEL_TYPES = ['dingtalk','discord','feishu','qq','slack','telegram','wecom_bot','weixin']
tenant_channel_types -> 8 entries (dingtalk, discord, feishu, qq, slack, telegram, wecom_bot, weixin)
personal_channel_types -> 全部 (ready=True, reason='')
personal_runtime_enabled -> 全部 False
is_database_mode() = True
```

与 3.6 浏览器验收的现场记录一致（`evidence/3-6-browser-dual-role-acceptance.md:38-40`：成员面
`channel_types` 8 项且全部 `ready=true`）。**注意 3.6 当时把这一现象裁定为「不是缺陷」，理由是
「`ready` 与声明一致」——这是循环论证**：它只核对了「列表 == 声明」，没有核对「声明是否为真」。
本文件补上后一半，结论相反。

## 2. 有没有准入过滤？（Q2）

**有一处服务端过滤，但它筛的是「可拥有性」，不是「可准入性」；不存在按入站准入性的过滤。**

### 2.1 实际存在的类型闸门

| 闸门 | 位置 | 判据 | 挡下了谁 |
| --- | --- | --- | --- |
| 目录候选 | `channel/channel_instances.py:1133` `tenant_channel_types()` | `ctype in MULTI_INSTANCE_READY` 且有 `CREDENTIAL_KEYS` | `wechatmp` / `wechatcom_app` / `wechat_kf` / `web` / `terminal` |
| 写入 | `auth/service.py:7406` `_channel_credential_keys()`（被 `:7629` `_validated_channel_bundle` 调用） | 同上 | 同上 |
| 个人就绪 | `auth/service.py:7846`（`_enforce_personal_instance_policy`，创建**与**启用共用）与 `:8119`（创建前置，先于凭据校验） | `personal_channel_ready()` | 未就绪/被策略收窄的类型 |

三处判据同源、互不矛盾，这是 6.x 已经做对的部分。**但它们全都只看
`MULTI_INSTANCE_READY`**，而 `MULTI_INSTANCE_READY` 的语义（`:97-99` 注释）是
「今天真的能同时跑多份实例」。

### 2.2 与 6.2 目标候选的对比

| 维度 | 目标候选（6.2 已收口） | 类型候选（本缺陷） |
| --- | --- | --- |
| 真值处 | `auth/object_scope` / `_iter_tenant_agents(action=SCOPE_MANAGE)` | `MULTI_INSTANCE_READY` + `CREDENTIAL_KEYS` |
| 下发 | `web_channel._channel_target_candidates(ctx)` → `targets` | `channel_instances.tenant_channel_types()` → `channel_types` |
| 「候选与写入同源」 | 成立（6.2 §2） | 成立 |
| 「候选与**运行**同源」 | 目标由写路径 `channel_target_scope` 同一谓词派生 | **不成立**：候选基于「能跑多实例」，运行期要求「能打身份戳」 |

结论：6.2 的做法在这里被正确复用（候选由服务端下发、前后端无第二份清单），**缺陷不在下发机制，
而在被下发的那个判据偏了**。所以不是「没有过滤」，而是「过滤键错了事实」——
这也解释了为什么过滤存在却没有排除这 5 个提供方。

### 2.3 声明与代码的背离（根因）

```
# channel/channel_instances.py:1177-1187
#: Channel types whose **personal** (member-owned) onboarding boundary is
#: declared ready (change enable-member-personal-console, task 6.3).
#:
#: A type is eligible only when it truly runs several instances *and* its
#: inbound path can prove the sender per instance — the two are the same
#: requirement, which is why this is seeded from :data:`MULTI_INSTANCE_READY`
#: rather than guessed. ...
PERSONAL_READY_CHANNEL_TYPES = frozenset(MULTI_INSTANCE_READY)
```

「the two are the same requirement」不成立：`MULTI_INSTANCE_READY` 里 5 个成员从未调用
`stamp_external_identity`（7.1 §1.4）。docstring 描述的判据是对的，实现漏了它。
**这是本文件认定的最小可修点。**

## 3. 端到端：数据库模式下接一个 Slack 会怎样（Q3）

### 3.1 创建：会被接受

- `slack` 在 `MULTI_INSTANCE_READY`（`:100`）且有 `CREDENTIAL_KEYS`（`:58`），
  `REQUIRED_CREDENTIAL_KEYS[slack] = ("slack_bot_token","slack_app_token")`（`:525-528`）
  → `_validated_channel_bundle`（`auth/service.py:7611`）放行，行、凭据密文、凭据版本、两条审计
  在一个事务内提交（`create_tenant_channel_instance` `:8064`）。
- **成员面**：`allow_owner=True` 分支在 `:8114-8123` 先问 `personal_channel_ready('slack')`
  → 今天 `(True, "")` → 放行。
- **公共面**：无就绪检查（`tenant_channel_types()` 不含 `ready`），`_require_public_instance_agent`
  （`:7556`）只拒绝「指向他人私有 Agent」，空目标合法（回落租户公共缺省 Agent）
  → 管理员可以把 8 个类型中的任意一个建成公共连接。

### 3.2 连接：公共归属会真的连接，成员归属会停在「已保存未连接」

`_apply_channel_runtime`（`admin_handlers.py:957`）在创建/编辑/启停后调用
`apply_tenant_instance_runtime`（`channel/channel_instances.py:767`）：

| 归属 | 经过的闸门 | 结果 |
| --- | --- | --- |
| `scope='tenant'` | 无个人闸门（`:804-838` 的 owner/目标/运行开关判断全带 `scope == "user"` 守卫） | 解密凭据 → `mgr.restart(inst)`（`:872`）→ `applied=True` |
| `scope='user'` | `personal_runtime_enabled('slack')`（`:825-838`） | 7.1 §0.3 的阻塞 ③：总开关默认关 + 验收集为空 → `applied=False, pending=True`，`error="personal runtime is not enabled for this channel type"` |

进程内只读直调（伪 `_runtime_manager` 与伪 service，不写库、不连外网）实证公共归属：

```
$ .venv/bin/python -c "... apply_tenant_instance_runtime('chan_slack') ..."
[INFO][channel_instances.py:879] - [ChannelInstances] instance 'chan_slack' applied immediately
apply -> {'applied': True, 'pending': False, 'error': ''}
manager calls -> [('remove','chan_slack'), ('restart','slack','t1','chan_slack')]
console state -> {'saved': True, 'connected': True, 'state': 'connected', 'reason': ''}
```

即：**「已连接」是控制台会得到的答案，且它只依赖凭据与本进程，不依赖入站能否被接受。**
成员归属的 `instance_connection_state`（`:569`）会在 `:625-630` 报
`state='not_connected'` + 原因；两者语义被刻意拆开，这一点已由 7.3 做对。

启动合成路径同构：`load_tenant_channel_instances`（`:904`，由 `app.py:103` 消费）对
`scope='user'` 逐行过 `personal_runtime_enabled`（`:938-955`）等三道闸门，对 `scope='tenant'`
**不过**，直接解密凭据并组 `ChannelInstance`（`:971-990`）。

### 3.3 入站：第一条消息即被拒

```
# channel/chat_channel.py
   191|    def _handle(self, context: Context):
   197|        if self._needs_external_db_mapping(context):
   198|            if self._preflight_external_inbound(context):
   199|                return  # a deny notice was already queued
   ...
   217|    def _needs_external_db_mapping(self, context: Context) -> bool:
   223|        """Web messages are authorized and scoped synchronously ... Other channels
   224|        in database mode have no web session and must go through the external
   225|        identity binding — legacy mode and web never do."""
   229|        if not is_database_mode(): return False          # 恒不成立（恒 True）
   230|        if str(context.get("channel_type") or "") == "web": return False
   232|        if (context.get("runtime_identity") or {}).get("user_id"): return False
   233|        return True
   ...
   424|    def _preflight_external_inbound(self, context: Context) -> bool:
   511|
   512|        if not context.get("external_identity"):
   513|            logger.warning(
   514|                f"[chat_channel] db external inbound missing identity stamp, "
   515|                f"channel={context.get('channel_type')}, agent={agent_id}")
   517|                context, Reply(ReplyType.TEXT, ex.deny_notice(ex.UNSUPPORTED_CHANNEL)))
   519|            return True
```

同一拒绝码的第二处兜底：`channel/external_identity.py:429-431`
（`resolve_actor_for_context` 无三元组 → `UNSUPPORTED_CHANNEL`）；文案见
`channel/external_identity.py:115-118`（「该消息渠道在数据库模式下尚未开放，请联系管理员。」）。
成员归属实例的等价分支在 `channel/chat_channel.py:236` `_preflight_personal_inbound`
（`:250-254`）——同样早于 `resolve_personal_channel_inbound`。

进程内只读直调实证：

```
resolve_actor_for_context({}, "some-agent") -> (None, 'external_channel_unsupported')
deny_notice(UNSUPPORTED_CHANNEL) = 该消息渠道在数据库模式下尚未开放，请联系管理员。
$ pytest tests/test_external_im_gate.py -k "unstamped or needs_mapping"  → 2 passed
```

### 3.4 拒绝的可见性：对操作者**完全静默**

| 观察面 | 是否可见 | 证据 |
| --- | --- | --- |
| 控制台实例状态 | **不可见（且误导）** | `instance_connection_state`（`channel_instances.py:569`）只有 `saved/connected/state/reason`，无任何「入站可达性」事实；公共归属报 `connected`（§3.2 实证）；`_instance_projection`（`auth/service.py:7676`）不下发入站健康度 |
| 控制台 toast / 运行横幅 | **不可见** | 写后横幅只报 `applied/pending`（`admin_handlers.py:1116-1118` → `console.js:14816-14837`） |
| 待绑定/尝试列表 | **不可见** | 拒绝发生在 `:511-519`，而 `record_external_identity_attempt` 只在 `:548` 之后 `reason == UNBOUND` 的分支调用 → 这些消息**不落** `external_identity_attempts`，管理员在 `/api/tenant/external-identity-attempts`（`route_registry.py:151`）里看不到 |
| 服务端日志 | 可见 | `chat_channel.py:513-515` 一行 warning（要有人去看） |
| 发消息的用户 | 可见 | 收到固定文案「该消息渠道在数据库模式下尚未开放，请联系管理员。」 |

**这就是「静默且永久」的形状**：配置者看到「已连接」，发送者被拒，二者之间的唯一连接物是一条
日志。对成员归属的连接，今天还多一层「已保存未连接」（§3.2），所以更糟：连适配器都没起来。

## 4. 是否与模式相关（Q4）

### 4.1 `database` 是唯一模式，且是硬编码的

```
# channel/external_identity.py:79-86
def is_database_mode() -> bool:
    """True when the deployment runs database identity mode.
    After retire-legacy-identity-mode, database is the only mode. Explicit
    ``identity_mode=legacy`` is refused at boot; missing/other values run as
    database."""
    return True
```

`common/startup_hooks.py::_identity_mode_consistency` 在启动时拒绝 `identity_mode=legacy`
（`RuntimeError`）；`config.py:301-303` 亦注释「database only」。

### 4.2 非 web 入站**在任何模式取值下**都必须带身份戳

`_needs_external_db_mapping`（`channel/chat_channel.py:217-233`）的 `False` 出口只有三个：
非 database（恒不成立）、`channel_type == "web"`、上游已写 `runtime_identity`。
外部 IM 入站不满足后两者。因此：

**结论 1（对任务前提的更正）**：这些提供方在**非 database（单机/legacy）模式下可用**这一前提，
在**当前树里不成立**——那个模式已经不存在。仅测试会 monkeypatch `is_database_mode` 为 `False`
来走 legacy 分支（`tests/test_external_im_gate.py:556`）。

**结论 2**：因此过滤**不需要**「按模式区分功能是否可用」才能正确；但没有模式例外不等于可以省掉
模式谓词。推荐写法仍保留 `is_database_mode()`（§6.2），理由：① 判据的语义本就是「database 模式
才需要身份戳」，写出来才与拒绝点同源；② 一旦将来恢复任何非 database 路径，过滤会**自动关闭**，
不会把「无害类型」永久挡在门外。今天它与「`ctype in {feishu,dingtalk,wecom_bot}`」等价。

### 4.3 单机/名册（`team.json`）路径同样被拒

`resolve_channel_instances`（`channel/channel_instances.py:226`）在 database 模式下**拒绝**由
`channel_type` 合成 legacy 实例（`:244-255`），但 `channel_instances` 显式名册仍会启动；
这些实例没有 `tenant_channel_instances` 行，`instance_row()`（`external_identity.py:173`）返回
`None` → 仍走到 `_preflight_external_inbound` 的同一处 `:511-519` → 同样被拒。
故「保住单机能力」不构成本缺陷的约束。

## 5. 其余非戳类型与「部分戳」（Q5）

| 提供方 | 是否在 8 项目录 | 是否打戳 | 证据 |
| --- | --- | --- | --- |
| feishu | 是 | 是 | `channel/feishu/feishu_channel.py:825-831`（`provider="feishu"`, `issuer=feishu_app_id`） |
| dingtalk | 是 | 是（私聊 + 群聊两条 produce 路径各一次） | `channel/dingtalk/dingtalk_channel.py:700-707`、`:772-780`；全目录仅两处 `produce` |
| wecom_bot | 是 | 是（**带早退的分支**） | `channel/wecom_bot/wecom_bot_channel.py:599` → `:602-636`；早退在 `:621-628` |
| qq | 是 | **否** | 全目录 `rg external_identity` 零命中（7.1 §1.4） |
| telegram | 是 | **否** | 同上 |
| slack | 是 | **否** | 同上 |
| discord | 是 | **否** | 同上 |
| weixin | 是 | **否** | 同上 |

即：**8 项中 3 项能戳、5 项不能**；目录内没有第 9 个非戳类型
（`wechatmp` / `wechatcom_app` / `wechat_kf` 不在 `MULTI_INSTANCE_READY`，从不出现）。

「部分戳」的准确口径：

- **wecom_bot**：`_stamp_external_identity` 在 `issuer`（`to_user_id`/`aibotid` 或实例的
  `wecom_bot_id`）为空时**直接 return，不落 `external_identity`**，理由见 `:615-619`
  （两个 bot 合并成一行）。这条消息落到 `:511-519`，与非戳类型同一条拒绝路径。
  但 `REQUIRED_CREDENTIAL_KEYS[wecom_bot]` 强制 `wecom_bot_id` 非空（`:514-517`），
  因此该早退实战中只在「实例凭据未装配/读取失败」时命中——是防御分支而非活缺口。
  **未实测**（无真实 bot 调用）。
- **feishu / dingtalk**：不早退，但 `issuer` 取 `self.<app_id> or ""`。feishu 的
  `feishu_app_id` 受必填集约束（`:510-513`）→ 非空；**dingtalk 不在必填集内**（`:505-508`
  注释自认「最小集未核实」），因此理论上可以造出一个 `issuer=""` 的钉钉实例：此时消息**有戳但三元组不完整**，
  在 `resolve_actor_for_context`（`external_identity.py:432-436`）变成 `UNBOUND`——**这与非戳类型
  是不同的拒绝码**，且会落待绑定列表。属 7.1 §7 已登记的「dingtalk 最小必填集未覆盖」，本文件不重复判定。

## 6. 修复方案与推荐（Q6）

### 6.1 四个选项

| 选项 | 做法 | 优点 | 代价/风险 |
| --- | --- | --- | --- |
| **(a) 收窄目录** | 服务端目录只列出可准入类型（database 模式下即 3 个） | 最小；沿用既有模式（`wechatmp`/`wechat_kf` 本来就因不在集合里而不出现）；成员面前端**零改动**（`console.js:14498` 已按 `spec.ready` 收窄）；一次改动同时关掉创建与启用 | 5 个类型从目录消失；`tenant_channel_types()` 不能直接删条目（见 §6.2 的契约/候选双重职责） |
| **(b) 保留目录 + 标注不可用 + 给出原因** | 类型仍在列表里，但以只读/不可选形态呈现并说明原因（三语） | 最贴近 `console-navigation-availability:121/135-137` 的「保留目录并说明」取向；用户知道平台支持 Slack 只是当前不可用 | 前端要改 `<select>` 生成与表单禁用逻辑、新增 3 语言文案与 i18n 快照、服务端要在**两个**面各下发一个判决字段；比 (a) 大一圈，且「不可用但仍在选」与「不得展示提交必被拒的类型」需要额外措辞界定 |
| **(c) 只挡创建** | 目录照旧，只在写入时拒 | 改动最小 | 正是本 change 反复要消掉的「可点但被拒」形状（`specs/tenant-channel-configuration/spec.md:69`；`evidence/5-4-public-surface-authority.md:40-46` 对技能开关的同款处理）。不采纳 |
| **(d) 补齐身份戳** | 为 5 个适配器实现 `stamp_external_identity` | 唯一能真正恢复能力的方案 | **真实成本很高**：每个提供方都要定义 `(provider, issuer, subject)` 三元组语义并接进**每一条** produce 路径（issuer 必须取实例自身对外标识，见 `evidence/7-1-runtime-preflight.md` §2.4）；每类都要真实验收（7.6 要求「真实证据与登记同批」）。另外 telegram/discord 当前**出网不可达**（7.1 §1.3），即使写了代码本轮也无法验收。结论：应另立 change，不在本 change 内做 |

### 6.2 推荐：以 (a) 为目标形态，(c) 作写入兜底，共用**一处**声明

核心不是「加一个过滤」，而是**让判据与它自己的契约一致**：

1. **定义处**（唯一真值）`channel/channel_instances.py`，紧邻 `MULTI_INSTANCE_READY`：

   ```
   #: Channel types whose adapter actually stamps the inbound author's external
   #: identity triple (provider/issuer/subject) — the precondition for any
   #: non-Web inbound in database identity mode
   #: (``channel/chat_channel.py::_preflight_external_inbound`` refuses an
   #: unstamped context as UNSUPPORTED_CHANNEL *before* any binding lookup).
   #: Call sites: feishu_channel.py, dingtalk_channel.py, wecom_bot_channel.py.
   INBOUND_IDENTITY_STAMPING_TYPES = frozenset({const.FEISHU, const.DINGTALK,
                                                const.WECOM_BOT})

   def inbound_identity_admissible(channel_type: str) -> bool:
       """Whether this type's inbound can ever be accepted on this deployment.

       Mode-aware in form: only database identity mode needs the stamp, so a
       non-database path (should one ever return) is not narrowed by it.
       """
       from channel.external_identity import is_database_mode
       if not is_database_mode():
           return True
       return _normalize_type(channel_type) in INBOUND_IDENTITY_STAMPING_TYPES
   ```

2. **个人就绪**`personal_channel_ready()`（`:1224`）：并入该谓词，新增原因码
   `no_inbound_identity`（加入 `PERSONAL_NOT_READY_REASONS` `:1192`）。这一处自动同时关掉：
   - 成员面创建与**启用**（`auth/service.py:7846` `_enforce_personal_instance_policy`，
     创建前置在 `:8119`）；
   - `personal_channel_types()` 的 `ready` → 前端 `console.js:14498` **无需改动**即收窄；
   - `public_personal_ingress_ready()`（`:1340`，其首句就是 `personal_channel_ready`）— 共享实例上的
     个人路由本就关闭，语义自洽。

3. **公共/管理面目录**：`tenant_channel_types()`（`:1133`）**不能**把 5 类直接剔除——它同时承担
   「新建候选」和「已存在实例的字段契约」两个职责（前端 `tenantChannelType()` `:14477` 从
   `tenantChannelTypes` 全量列表查字段；`console.js:14490-14499` 的注释明确「full list is kept for
   looking up the contract of an instance that already exists」）。若直接删条目，一个既存的
   Slack 行编辑时 `spec` 为 null → `fields=[]`，表单掉字段。
   正确做法是给 entry 追加一个与个人 `ready` **分离**的判决字段（如
   `admissible`: bool + `admissible_reason`），前端两个分支都按它收窄候选，契约查找仍走全量。
   分离的理由与 `personal-channel-configuration` spec 的「目录、配置和执行状态分别判定」同源：
   个人 `ready` 含「租户策略收窄」等个人专属含义，不应决定管理员的公共目录。

4. **写入兜底**：让 `_channel_credential_keys()`（`auth/service.py:7406`）或
   `create_tenant_channel_instance` 复用同一谓词，使手工构造的请求也拒绝
   （沿用既有 `bad_request: channel type is not available for tenant configuration`，
   避免新增错误码）。**注意副作用**：`_channel_credential_keys` 同时服务凭据轮换
   （`:7629`），因此在它里面收紧会连带禁止**既存**非戳实例的凭据轮换。
   这在本缺陷的语义下是合理的（不可能生效的凭据不必维护），但它会新增一扇「单向门」；
   若 change 想保留对既存行的轮换能力，就把兜底放在**创建**分支而不是这个共用函数里。
   二选一，不要两处都写。

**为什么这是「最小」**：成员面（用户报告的现场）**前端零改动**；服务端只动一个函数族
（`channel_instances.py` 的声明与谓词）+ 两处调用点（`auth/service.py` 的写入闸门、
`admin_handlers.py` 下发的目录字段）；不新增 i18n 文案（若走 (a) 隐藏）；不触碰运行期与入站期。

**如果 change 选 (b)**：把 `ready=false` 的类型从「隐藏」改成「渲染为不可选 + 显示原因」，
则需新增 `tenant_channel_not_admissible` 之类三语文案、改 `console.js` 的 `<select>` 生成与
`i18n` 快照，并且两个面都要下发同一个原因。声明与谓词与 (a) **完全共用**，所以 (a)→(b)
是一次「表现层升级」，不是返工。

### 6.3 会碰到的文件与既有测试

| 文件 | 改动性质 |
| --- | --- |
| `channel/channel_instances.py` | 新增声明 + 谓词；`personal_channel_ready`/`PERSONAL_NOT_READY_REASONS` 加原因码；`tenant_channel_types()` 加判决字段（若走 (b) 或需要管理面收窄） |
| `auth/service.py` | 创建（或共用闸门）复用谓词 |
| `channel/web/admin_handlers.py` | 仅在选 (b)/需要新增字段时 |
| `channel/web/static/js/console.js` | 选 (a) 时**零改动**；选 (b) 时改 `<select>` 渲染 |
| `channel/web/static/js/i18n/tenant-channel.js` + `tests/fixtures/console_i18n_snapshot.json` | 仅选 (b) |
| `tests/test_personal_channel_console.py` | **必改**：`:499-514` `test_configuration_can_narrow_the_ready_set_but_never_widen_it` 用 `telegram` 作为「被配置收窄」的样例并断言 `personal_channel_ready("telegram") == (True, "")`；收窄后 telegram 恒不就绪，须改用两个**戳类**类型（如把 `personal_channel_ready_types` 设为 `{"dingtalk"}`，断言 dingtalk 就绪、feishu `not_allowed`） |
| `tests/test_tenant_channel_required_credentials.py` | 复核：`:68` 遍历 `tenant_channel_types()`，`:58` 断言必填集覆盖 6 类——若只加判决字段/只改就绪谓词则不受影响；若动 `CREDENTIAL_KEYS` 会碰 |
| `tests/test_channel_instances_partition.py` `:103-119` | 逐类断言 `CREDENTIAL_KEYS`/`REQUIRED_CREDENTIAL_KEYS` 完整性——**不要**通过删 `CREDENTIAL_KEYS` 条目来实现收窄，否则这里会红且契约丢失 |
| 新增测试 | 应钉住：① `tenant_channel_types()`/`personal_channel_types()` 对 5 类给出不可准入判决；② 创建这 5 类被拒且**不留下**行/凭据/审计；③ 3 个戳类仍可创建（负向对照，防止「一律拒绝」通过）；④ 既存非戳行的编辑表单仍拿到字段契约 |

### 6.4 关于 (d) 的真实成本

5 个适配器 × (每条 produce 路径的戳 + issuer 语义 + 单测) + 每类一次真实入站往返验收
（7.6 要求与 `PERSONAL_RUNTIME_ACCEPTED_TYPES`/`PUBLIC_PERSONAL_INGRESS_TYPES` 登记同批提交）。
其中 telegram/discord 本轮出网不可达，weixin 需扫码且厂商侧是否仍开放未知（7.1 §7）。
**不建议在本 change 内做**；本缺陷的最小修法与 (d) 不冲突——将来补上戳的类型把该类型加进
`INBOUND_IDENTITY_STAMPING_TYPES` 即自动恢复入口。

## 7. 与既有规范的关系（是否已被裁定）

### 7.1 明确相关、但不覆盖本缺陷的条款

`openspec/changes/unify-console-by-data-scope/specs/tenant-channel-configuration/spec.md:69`
（ADDED「共用接入表单按目标派生连接归属」末段）：

> …本人范围内的目标选择 SHALL 只列出当前调用者本人、启用且可用的私有智能体，且不提供空目标选项；
> 管理范围内的类型目录 SHALL 只列出服务端声明为本人可配置的就绪类型。**界面不得展示提交必被拒绝的类型或目标。**

只在「**提交**必被拒绝」这一口径上有约束力；而 Slack 的**提交并不被拒**（§3.1 实证）。
所以该条**没有裁定**本缺陷，但它决定了判据该落在**目录**这一层（「类型目录 … 只列出服务端声明为就绪的类型」）
——这正是 §6.2 把准入性并入就绪声明、而不是只加写入拒绝的依据。

### 7.2 看似相反的两条，其实讲的是另一个事实

`.../specs/console-navigation-availability/spec.md:121`：

> …目录、配置和执行状态**分别判定**，运行未开放不得关闭已获权目录…

同文件 `:135-137`：

> #### Scenario: 个人渠道执行关闭但目录已开放
> - **WHEN** 某渠道运行条件关闭而目录和配置仍可用
> - **THEN** 现有消息渠道页**保留目录与配置并说明运行关闭**，账号菜单不提供「我的渠道」，不会因换入口启动连接

这两条讲的是 **执行状态**（`personal_channel_runtime` 开关 / `PERSONAL_RUNTIME_ACCEPTED_TYPES` 验收集，
即 7.1 阻塞 ③），主张「运行没开也要让人看见目录、说明原因」。本缺陷讲的是 **目录准入性**：
不是「这个类型还没验收」，而是「这个适配器根本没有认发送者的能力」，属**能力缺席**，
与已经在 `tenant_channel_types()` 里因不在 `MULTI_INSTANCE_READY` 而不出现的
`wechatmp`/`wechat_kf` 同类。因此：

- 选 (a) **不违反** `:121`/`:137`——被收窄的类型从未获得过「已获权目录」资格；
- 选 (b) 同样合规，且对「说明原因」更友好。

**但这条分歧需要在实现时显式写进证据**，否则下一个复核者会像 3.6 那样把「8 项」当成声明正确的证据。

### 7.3 一并相关的既有取向

- `.../specs/personal-channel-configuration/spec.md`（ADDED「相同渠道提供方使用统一接入和运行流程」）
  末段「**保存成功、连接成功和实际可对话状态 SHALL 真实区分**」及其 Scenario「仅配置成功」：
  本缺陷下控制台对公共 Slack 连「已保存/已连接」都报对了，却**无法**表达「实际可对话=false」——
  该 requirement 要求的三态在当前投影里只有两态（§3.4）。修 (a) 能避免把用户带进这个三态缺口；
  若要真正满足该要求，还需在状态投影里加入「入站可达性」事实（本文件不推荐在本 change 内做，
  因为最小修法已让不可能可达的类型不再可配）。
- `evidence/5-4-public-surface-authority.md:40-46`：技能面已用「读取载荷携带 `actions.*`，
  由写入路径的同一对权威算出」消除「页面不广告会被拒绝的动作」——本缺陷是同一形状在渠道类型目录上的
  未完成项，修法应沿用「候选与写入同源」的手法。
- `evidence/6-2-channel-target-derivation.md`：目标候选已经做到「候选与写入不可能互相矛盾」；
  类型候选缺的是「候选与**运行期准入**同源」，本修法补齐的正是这一半。
- `evidence/3-6-browser-dual-role-acceptance.md:38-40`：把 8 项裁定为非缺陷的理由是循环的（§1），
  本文件为它对同一现象的裁定做修订。

## 8. 未覆盖

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 公共 Slack 连接**真实**拉起 Socket Mode 连接 | **未覆盖** | 无真实 `slack_bot_token`/`slack_app_token`；本轮只证到「`apply_tenant_instance_runtime` 在所有闸门之后调用 `mgr.restart`」（进程内直调，§3.2），未证适配器内部握手成功 |
| 5 类提供方入站的**真实现场**拒绝 | **未覆盖** | 约束禁止发消息；结论由「适配器全目录无 `external_identity` 命中」+「拒绝点不依赖 channel_type」两段代码走查得出，并有既有用例 `tests/test_external_im_gate.py::test_preflight_unstamped_sends_unsupported` 作旁证 |
| 平台实例页 `/api/channels`（`ChannelDesktop`/`ChannelsHandler`，`CHANNEL_DEFS`）是否也提供同类不可准入选项 | **部分覆盖／未裁定** | 读代码可见它按 `CHANNEL_DEFS` 列出全部类型（`web_channel.py:7221-7290`）、名册展开按 `MULTI_INSTANCE_READY`（`:7158-7179`），入站同样被拒（§4.3）；但该面是平台域、守卫与展示不同，本轮未逐项核对，**未覆盖**其是否需要一并收窄 |
| `wecom_bot` 早退分支（issuer 为空）真实命中率 | **未覆盖** | 需真实 bot 且需制造「实例无 `wecom_bot_id`」的异常状态，违背只读约束 |
| `dingtalk` 最小必填集（`issuer` 可否为空） | **未覆盖** | 7.1 §7 已登记；本文件 §5 只指出其后果是 `UNBOUND` 而非 `UNSUPPORTED_CHANNEL` |
| 选 (a) 后既存非戳实例的编辑/启用/轮换完整回归 | **未覆盖** | 需先落地改动再跑带；本文件只登记了「`tenant_channel_types()` 不能删条目」与「`_channel_credential_keys` 收紧会连带禁轮换」两个已知风险点 |
| 行号在并发编辑下的稳定性 | **部分失效风险** | 写作期间 `channel/channel_instances.py`、`channel/web/*`、`auth/service.py` 被并发编辑；已按函数名/常量名锚定，引用前请以名复核 |

## 9. 可复跑证据

```bash
# 1) 目录来源与就绪判决（不启服务、不写库）
.venv/bin/python -c "
import channel.channel_instances as ci
print(sorted(ci.MULTI_INSTANCE_READY))
print([(t['channel_type'], t['ready'], t['reason']) for t in ci.personal_channel_types()])
print({t: ci.personal_runtime_enabled(t) for t in sorted(ci.MULTI_INSTANCE_READY)})
"
# → 8 项；全部 (True, '')；personal_runtime_enabled 全部 False

# 2) 身份戳覆盖（只有 3 个适配器命中）
rg -n "stamp_external_identity" channel/feishu channel/dingtalk channel/wecom_bot
rg -n "external_identity" channel/{qq,telegram,slack,discord,weixin}/     # → 无输出

# 3) 拒绝点与模式常量
rg -n "missing identity stamp" channel/chat_channel.py
rg -n "def is_database_mode" -A 6 channel/external_identity.py

# 4) 拒绝不落待绑定（记录点在门槛之后）
rg -n "record_external_identity_attempt|reason == ex.UNBOUND" channel/chat_channel.py

# 5) 公共归属会真的 restart（进程内，伪 manager，无外网、无写库）
.venv/bin/python -c "
import channel.channel_instances as ci
..."   # 见 §3.2 输出

# 6) 既有用例旁证
.venv/bin/python -m pytest tests/test_external_im_gate.py -k "unstamped or needs_mapping" -q -p no:randomly
# → 2 passed
```

（以上均已在 2026-09-16 本轮实际执行；除本文件外无任何写入。）
