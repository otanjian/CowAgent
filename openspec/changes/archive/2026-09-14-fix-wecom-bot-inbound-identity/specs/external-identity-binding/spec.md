## ADDED Requirements

### Requirement: 渠道入站必须携带外部身份三元组

database 模式下，非 Web 渠道 SHALL 在把入站消息交给执行链路前，在上下文上携带外部身份三元组 `(provider, issuer, subject)`：`provider` 为渠道类型，`issuer` 为该渠道实例可稳定识别的提供方标识（应用、企业或机器人标识），`subject` 为发送者在该提供方下的稳定标识。三元组 SHALL 由渠道自身在解析入站消息时写入，MUST NOT 由后续层按上下文中的会话、昵称或路由目标推断补齐。

`issuer` MUST NOT 为空：三元组是外部身份绑定的全局唯一键，空 `issuer` 会让不同提供方或不同实例下取值相同的 `subject` 归并为一个绑定，从而把消息解析到别的账号。渠道无法取得非空 `issuer` 时 MUST NOT 写入三元组，该消息 SHALL 按未携带三元组处理（拒绝且不调用模型），MUST NOT 落库任何空 `issuer` 的绑定。

缺少三元组的入站 MUST NOT 调用模型，SHALL 返回固定拒绝提示，且 MUST NOT 登记为待绑定尝试——重新绑定不能解决「渠道未携带身份」这一情形。渠道类型尚未迁移（该类型从未打戳）与「本条消息未携带三元组」SHALL 在日志与审计上可区分，且 SHALL 至少给出渠道类型、该渠道实例与路由目标，使管理员能据证据判断应当绑定身份还是需要改造渠道。

#### Scenario: 企业微信智能机器人入站携带三元组

- **WHEN** 已登记的 `wecom_bot` 渠道实例收到成员的私聊文本消息
- **THEN** 该消息在进入执行链路前携带 `(wecom_bot, 该实例的机器人标识, 发送者 userid)`，并据此按既有规则解析到租户成员或报未绑定

#### Scenario: 群聊与私聊同源

- **WHEN** 同一发送者在同一 `wecom_bot` 实例的群聊与私聊各发一条消息
- **THEN** 两条消息携带相同的 `provider`、`issuer` 与 `subject`，群聊不改变身份三元组

#### Scenario: 未绑定发送者按未绑定处理

- **WHEN** 已登记的 `wecom_bot` 租户实例收到一条三元组在库中无绑定记录的成员消息
- **THEN** 系统以 `external_unbound` 拒绝并登记待绑定尝试，MUST NOT 以「渠道未开放」拒绝该消息

#### Scenario: 无法取得稳定 issuer

- **WHEN** 渠道既无法从消息体、也无法从实例凭据取得非空 `issuer`
- **THEN** 上下文不携带三元组，消息被拒绝且不调用模型，日志给出该原因，且不产生任何空 `issuer` 的绑定行
