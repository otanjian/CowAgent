## Context

动机见 proposal.md - Why，需求见 `specs/external-identity-binding/spec.md`。

当前代码接入点：

- 闸门：`channel/chat_channel.py` 的 `_preflight_external_inbound` 在 `context` 无 `external_identity` 时回 `deny_notice(UNSUPPORTED_CHANNEL)`，且该分支**早于** `resolve_actor_for_context`（也早于绑定查询），所以「去绑定」无法绕过；`channel/external_identity.py` 的 `resolve_actor_for_context` 亦以 `UNSUPPORTED_CHANNEL` 作为无三元组的兜底。
- 既有打戳范式：`channel/feishu/feishu_channel.py`（`provider=feishu`、`issuer=feishu_app_id`、`subject=actual_user_id`）、`channel/dingtalk/dingtalk_channel.py` 私聊与群聊各一处。两者都在「解析消息 → 组合上下文」处打戳，`wecom_bot` 缺少这一步。
- 企微入站两条路径共用构建点：websocket 长连接的 `_handle_msg_callback` 与 webhook 回调的 `_callback_handle_message`，都经 `_build_context` → `_compose_context` 后 `produce`。
- 身份来源：`channel/wecom_bot/wecom_bot_message.py` 从消息体取 `aibotid`（→ `to_user_id`）与 `from.userid`（→ `actual_user_id` / `from_user_id`）；websocket 模式下实例凭据 `wecom_bot_id` 亦为同一机器人标识（订阅报文以 `bot_id` 发送）。webhook 回调模式的启动分支不赋值 `self.bot_id`（只有 token / EncodingAESKey / 固定端口）。
- 渠道可用性声明：`channel/channel_instances.py` 的 `MULTI_INSTANCE_READY` 含 `wecom_bot`，`REQUIRED_CREDENTIAL_KEYS` 声明其最小必填集为 `wecom_bot_id` / `wecom_bot_secret`；控制台提供企微扫码接入，身份管理弹窗的渠道下拉含 `wecom_bot`（`extid_provider_wecom_bot` = 企业微信智能机器人）。

## Goals / Non-Goals

**Goals:**

- 让 `wecom_bot` 入站在 database 模式下走既有身份链路：打戳 → 绑定查询 → 成员/权限校验 → 执行或固定拒绝。
- 打戳位置只增一处，同时覆盖 websocket 与 webhook 两条入站路径。
- 不改变闸门、绑定存储、待绑定列表、控制台与权限模型的既有契约。
- 用渠道级回归测试锁住「租户可接入渠道必须携带三元组」，避免同类缺口再次静默发生。

**Non-Goals:**

- 不为 `weixin` / `wechatcom_app` / `qq` / `telegram` / `slack` / `discord` 补三元组（它们当前会落到同一提示，需单独决策；见 proposal.md 的 Impact）。
- 不引入「渠道类型入站就绪」的集中声明位或启动期校验（本 change 只治理 `wecom_bot`；集中声明属于后续 change）。
- 不改动提示文案（`deny_notice` 的双语文案保持稳定），也不放宽任何既有拒绝条件。
- 不新增身份绑定入口、不做成员自助绑定（属 `enable-member-personal-console` 的范围）。

## Decisions

### D1. 在 `_build_context` 打戳，而不是在各入站回调里各打一次

企微 websocket 与 webhook 两条路径都经 `_build_context` 产出 `(context, wecom_msg)`，在它返回前打戳即可一处覆盖两条路径，也避免两条路径将来分叉。feishu / dingtalk 的写法与此一致。

- 备选 A：在两个回调里各调一次 `stamp_external_identity`。否决——重复且易漏，新增入站路径时会再次出现同类缺口。
- 备选 B：在 `ChatChannel._handle` 里按渠道类型补戳。否决——渠道层是唯一知道「提供方标识与发送者标识分别取哪个字段」的地方，放到通用层会迫使它对每个渠道做类型分支。

### D2. 三元组取值：`provider=wecom_bot`、`issuer=机器人标识`、`subject=发送者 userid`

- `provider` 用渠道类型码 `wecom_bot`，与 `const.WECOM_BOT`、渠道实例类型、身份管理弹窗的 provider 选项一致；绑定记录因此可在控制台按渠道名呈现。
- `issuer` 取机器人自身标识，优先消息体 `aibotid`（每条消息自带，两种传输一致），缺省回落到实例凭据 `wecom_bot_id`。这与 feishu 用 `app_id`、dingtalk 用 `client_id` 的口径一致：`issuer` 是「机器人以谁的身份在运行」，且正好是管理员在渠道配置里能看到的 `Bot ID`，绑定时可照抄。
- `subject` 取 `actual_user_id`（缺省 `from_user_id`），即企微侧发送者的稳定标识。
- 备选：`issuer` 用企业 ID（corpid）。否决——企微机器人的 websocket 凭据里只有 `Bot ID` + `Secret`，webhook 启动分支同样取不到 corpid，取不到就会退化成空 `issuer`（见 D3 的安全约束）。

### D3. `issuer` 为空则不打戳（宁拒绝，不错绑）

三元组是全局唯一键（`external_identities` 以 `(provider, issuer_or_corp_id, subject)` 唯一）。若在取不到 `issuer` 时写入空串，不同机器人下取值相同的 `subject` 会归并成同一行绑定，消息可能被解析到另一个账号——这是越权，不是可用性问题。因此取不到非空 `issuer` 时：不写 `external_identity`，保持既有「缺三元组即拒绝」行为，并记录可区分原因（渠道、实例、路由目标），不落任何空 `issuer` 绑定行。

- 现实影响面：租户实例固定走 websocket 模式（webhook 传输单实例、拒绝额外实例），`self.bot_id` 必然已赋值；`aibotid` 在两种传输的消息体中均为标准字段。空 `issuer` 属于防御性分支。

### D4. 拒绝路径不动，只把「渠道未迁移」的证据补齐

打戳后，企微未绑定发送者自然进入既有 `external_unbound` 路径：回固定提示、登记待绑定尝试（证据按既有字段独立降级规则读取——企微私聊的 `actual_user_nickname` 为空，因此只保留消息预览；群聊另有发送者标识）、按实例租户决定管理员可见范围。这些都已实现，本 change 只保证消息能走到那里。

「缺三元组」分支的日志已有 `channel=` 与 `agent=`，本 change 不新增错误码，也不改文案：文案面向终端用户，保持稳定双语；可区分性由日志承担（新增的 `issuer` 缺失告警与既有 `missing identity stamp` 告警互相区分）。

### D5. 测试策略：渠道级断言三元组 + 闸门归属断言

现有测试只覆盖闸门本身（`tests/test_external_im_gate.py` 等构造带戳上下文），没有任何测试断言「租户可接入渠道在 `produce` 前必须打戳」——这正是缺口潜伏至今的原因。新增 `tests/test_wecom_bot_inbound_identity.py`：

1. 私聊文本消息经 `_build_context` 后携带 `(wecom_bot, aibotid, from.userid)`；
2. 群聊消息携带同一三元组（同一实例、同一发送者时私聊与群聊一致）；
3. 缺 `aibotid` 与实例 `bot_id` 时不写三元组（不留空 `issuer`）；
4. 已登记租户实例上的未绑定发送者被判为 `external_unbound`（登记待绑定尝试），MUST NOT 被判为渠道未开放。

测试用真实 `IdentityService` + 临时身份库、真实 `_build_context`、仅替换 `Bridge` 与 `get_identity_service` 的注入点，与被测行为同层，不 mock 被测逻辑本身。

## Migration / Rollback

无数据迁移：不新增表/列/权限，不改变既有绑定与待绑定记录格式。回滚只需移除打戳调用与新增测试；已按新三元组建立的绑定行在回滚后不再被读取（`find_user_for_external_identity` 不再被调用），不会影响其他渠道。

## Open Questions

- 是否将所有「租户可接入渠道」在渠道类型层面声明入站就绪，并在创建/启动时校验（而非等到第一条消息才暴露）——属后续 change，本 change 不预设结论。
- 企微外部联系人场景下 `from.userid` 的取值形态（成员 userid 与外部联系人标识的差异）需在真实租户上验证；绑定以实际观测值录入，不影响本 change 的三元组结构。
