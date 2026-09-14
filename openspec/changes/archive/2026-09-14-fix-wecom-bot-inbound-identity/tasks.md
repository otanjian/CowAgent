## 1. 实现：企微入站打外部身份三元组

- [x] 1.1 `channel/wecom_bot/wecom_bot_channel.py` 的 `_build_context` 在返回 `(context, wecom_msg)` 前调用新增的 `_stamp_external_identity` 打戳：`provider="wecom_bot"`、`issuer=wecom_msg.to_user_id or self.bot_id`、`subject=wecom_msg.actual_user_id or wecom_msg.from_user_id`
- [x] 1.2 `issuer` 为空时不打戳，并以 `[WecomBot] cannot stamp external identity ...` 记录实例与消息标识（与既有 `missing identity stamp` 告警可区分），确认不会写入空 `issuer` 的绑定
- [x] 1.3 确认 websocket（`_handle_msg_callback`）与 webhook（`_callback_handle_message`）两条入站路径均经该构建点，无需各自补戳

## 2. 测试

- [x] 2.1 新增 `tests/test_wecom_bot_inbound_identity.py`：私聊文本消息携带 `(wecom_bot, aibotid, from.userid)`
- [x] 2.2 群聊消息与私聊携带同一 `provider`/`issuer`/`subject`
- [x] 2.3 缺 `aibotid` 且实例无 `bot_id` 时不写 `external_identity`（不产生空 `issuer`）
- [x] 2.4 已登记 `wecom_bot` 租户实例上的未绑定发送者按 `external_unbound` 拒绝并登记待绑定尝试，MUST NOT 按渠道未开放拒绝
- [x] 2.5 运行 `tests/test_wecom_bot_inbound_identity.py`、`tests/test_external_im_gate.py`、`tests/test_external_identity_binding*.py`、`tests/test_external_identity_http.py`、`tests/test_tenant_channel_inbound_*.py`、`tests/test_tenant_channel_http.py`、`tests/test_tenant_channel_required_credentials.py`、`tests/test_channel_instances_partition.py`、`tests/test_scan_authorization.py`：187 passed

## 3. 真实链路验证

- [x] 3.1 在真实租户的企微机器人实例上由未绑定成员发一条消息，确认日志出现 `reason=external_unbound` 且 `identity=(wecom_bot, <机器人标识>, <userid>)`，并在身份管理弹窗看到待绑定条目（私聊至少含消息预览）—— 2026-09-14 17:25:42 实测通过：`reason=external_unbound channel=wecom_bot, agent=tax-health-check-test15, identity=(wecom_bot, aibeJrsnNgs8WBmu2BgW4YaHGD2LNjxjBnV, TanJian)`，待绑定尝试落库 `tenant=tnt_EA3qM-lHPLD8ZPwW, instance=chan_dESCeabBWsskMEvi, attempts=1`
- [x] 3.2 由租户管理员完成绑定后重发，确认日志出现 `db external inbound mapped user=... tenant=...`，且不再出现 `missing identity stamp` —— 2026-09-14 17:27:15 实测通过：绑定行 `wecom_bot / aibeJrsnNgs8WBmu2BgW4YaHGD2LNjxjBnV / TanJian → usr_9ZxVPz7M2FuOro1q`，待绑定尝试清零，日志 `mapped user=usr_9ZxVPz7M2FuOro1q tenant=tnt_EA3qM-lHPLD8ZPwW agent=tax-health-check-test15` 并完成 `[Agent] Turn 1` → `🏁 Done`；中途 17:26:33 一条 `external_permission_denied` 如实反映绑定后账号尚无 `chat.use`/`agent.use` 的状态，授予后放行

## 4. 规范与校验

- [x] 4.1 `openspec validate fix-wecom-bot-inbound-identity --strict` 通过
- [x] 4.2 确认本 change 未改动闸门、绑定存储、控制台与 i18n，也未放宽任何既有拒绝条件
