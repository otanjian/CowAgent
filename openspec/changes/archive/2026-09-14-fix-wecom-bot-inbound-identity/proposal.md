## Why

企业微信智能机器人（`wecom_bot`）是租户可自行接入的渠道类型：控制台提供企微扫码接入，`channel/channel_instances.py` 的 `MULTI_INSTANCE_READY` 与最小必填凭据集都把 `wecom_bot` 列为一级渠道，身份管理弹窗的下拉也把它作为可绑定渠道（中文名「企业微信智能机器人」）。但它的入站路径**从未打外部身份三元组**，于是 database 模式下每条消息都在身份闸门处被判为「渠道未开放」而拒绝：

- `channel/chat_channel.py` 在上下文没有 `external_identity` 时直接回固定提示 `external_channel_unsupported`，**发生在查询绑定之前**——管理员在控制台怎么绑定都无效。
- 全仓库只有 feishu 与 dingtalk 调用了 `stamp_external_identity`。
- 真实日志（2026-09-14 16:57:07）：`db external inbound missing identity stamp, channel=wecom_bot, agent=tax-health-check-test15`，与租户实例 `chan_dESCeabBWsskMEvi`（`wecom_bot` / tnt_EA3qM-lHPLD8ZPwW）完全对应。

结果是：实例能创建、机器人能连上、能收消息、能回提示，但**永远无法进入执行链路**——一条「能跑却永远说不出话」的死渠道。提示文案本身也不成立（问题不是渠道没开放，而是这条消息没带身份信息），会把管理员引向「去开通渠道」的错方向。

## What Changes

- 在 `wecom_bot` 通道的共享上下文构建点（websocket 长连接与 webhook 回调两条入站路径共用）打外部身份三元组：`provider=wecom_bot`、`issuer=机器人标识`（消息体 `aibotid`，缺省回落到实例凭据中的 `wecom_bot_id`）、`subject=发送者 userid`。
- `issuer` 取不到非空值时 MUST NOT 打戳（空 `issuer` 会让不同机器人下取值相同的 `subject` 撞成同一绑定），保持既有「缺三元组即拒绝」的行为，并留下可区分原因。
- 打戳后企微未绑定发送者走既有 `external_unbound` 路径：登记待绑定尝试（企微私聊取不到昵称时按既有降级规则保留消息预览；群聊另带发送者标识），管理员在身份管理弹窗一键绑定后即可对话。
- 补渠道级回归测试：企微私聊与群聊入站必须携带同一三元组；未绑定发送者在已登记租户实例上 MUST 被判为未绑定而非渠道未开放。

## Capabilities

### New Capabilities
<!-- 无新增能力 -->

### Modified Capabilities
- `external-identity-binding`: 新增「渠道入站必须携带外部身份三元组」要求，明确 `issuer` 的稳定性与不得为空，并要求「渠道未迁移」与「本条消息缺三元组」在证据上可区分。

## Impact

- 后端：`channel/wecom_bot/wecom_bot_channel.py`（共享的 `_build_context`，覆盖 websocket 与 webhook 两条入站路径）。
- 测试：新增 `tests/test_wecom_bot_inbound_identity.py`（渠道级三元组 + 闸门归属）。
- 不改动：闸门本身（`channel/chat_channel.py`、`channel/external_identity.py`）、feishu/dingtalk 既有打戳、渠道实例与凭据存储、控制台与 i18n、绑定与待绑定列表的既有契约。
- 范围外（本 change 不处理，留待后续判断）：`weixin` / `wechatcom_app` / `qq` / `telegram` / `slack` / `discord` 在 database 模式下同样缺少三元组，会落到同一个提示；是否为其补齐、以及是否在渠道类型层面声明「入站就绪」，需要单独决策（见 design.md 的 Non-Goals）。
