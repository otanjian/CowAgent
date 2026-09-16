# 7.1 运行改造前复核：凭据、身份绑定、实际提供方适配与审批/隔离切片

本文件记录 `tasks.md` 7.1 的**只读**复核结果：在把连接管理/启动合成/热更新/入站分发合并到共用流程（7.2）
之前，先把「跑一条真实渠道到底需要什么」查清，并逐提供方列出可进行**真实验收**的资源条件，供 7.4 / 7.5 / 7.6
解除封锁。

方法：只读代码走查 + 只读查询开发用 `identity.db` + 出站可达性探测（`curl -I` / TCP 443 连接，短时且无副作用）。
未启动任何服务、未发任何真实消息、未写入任何文件（本文件是本任务唯一产出）。

> **时点声明**：本文件写作期间 `channel/web/*`、`auth/*`、`docs/design/*` 正被其它子代理并发编辑，下述行号
> 为**读取时刻**的快照，随编辑会漂移。所有引用都同时给出函数/常量名，漂移后可按名定位。

## 0. 结论摘要

1. **现在（本轮环境）任何提供方都无法完成真实验收**。三条独立阻断中任意一条成立即足以阻塞：
   - **主密钥缺失**：`COW_CREDENTIAL_MASTER_KEY` 未在当前环境配置，`identity.db` 内既有渠道密文**无法解密**，
     新建渠道的凭据也**无法加密写入**（`auth/crypto.py:22` `KEY_ENV`、`:31` `_key()`，无密钥即抛
     `CredentialCryptoError`）。证据：`env | grep COW_CREDENTIAL` 为空；`~/.zshrc`/`.zprofile`/`.zshenv`/
     `.profile` 均无该变量。
   - **入站身份戳只在 3 个提供方实现**：仅 `feishu` / `dingtalk` / `wecom_bot` 会调用
     `stamp_external_identity`。数据库模式下其余提供方的入站消息会被**身份闸门**以
     `external_channel_unsupported` 拒绝（`channel/chat_channel.py:511-519`）。这意味着
     telegram / slack / discord / qq / weixin / wechatmp / wechatcom_app / wechat_kf 的
     **入站往返在结构上不可能通过**，与是否有凭据无关。
   - **本人连接（`scope='user'`）被双重关闭**：总开关 `personal_channel_runtime` 默认 `False`
     （`auth/policy.py:571`、`config.py:290`），且按类型记录 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 为空
     （`channel/channel_instances.py:1276`）。
2. **`scope='tenant'`（公共连接）不受第 3 条限制**——`apply_tenant_instance_runtime` 与启动合成里的
   「个人执行」判断都由 `scope == "user"` 守卫（`channel/channel_instances.py:804-838`、`:937-970`）。
   因此拿到第 1、2 条后，**feishu / dingtalk / wecom_bot 三个提供方的公共连接可以做真实入站往返验收**。
3. 本仓已有一条**真实的 wecom_bot 入站记录**（`openspec/changes/archive/2026-09-14-fix-wecom-bot-inbound-identity/tasks.md:17-18`，
   2026-09-14 17:25/17:27，真实租户 `tnt_EA3qM-lHPLD8ZPwW` 的实例 `chan_dESCeabBWsskMEvi`），
   但**当时的会话环境未留痕**，无法据此认定今天可复现（见 §7 未覆盖）。
4. 7.4 / 7.5 / 7.6 需要的**不是**「更多测试」，而是 §6 三组输入：主密钥、真实提供方账号与绑定、以及
   （若要验本人连接）打开运行开关并登记按类型验收记录。

## 1. 提供方清单

### 1.1 总表

判定栏口径：(a) 现在就能真实验收；(b) 提供指定凭据后可真实验收；(c) 当前环境**不可能**，并给原因。
「多实例就绪」= 在 `MULTI_INSTANCE_READY`（`channel/channel_instances.py:100`）中，决定该类型能否作为
租户自有实例运行多份。

| 提供方 | 适配器 file:line（startup / 传输 / 凭据读取） | 工厂入口 | 凭据字段（精确名） | 传输与网络要求 | 多实例就绪 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| feishu 飞书 | `channel/feishu/feishu_channel.py:306`（startup）、`:403`（websocket）、`:386`（webhook）、凭据 `:312-315` | `channel_factory.py:80` | `feishu_app_id`、`feishu_app_secret`（必填）；`feishu_token`（可选，webhook 校验）；`feishu_event_mode`（默认 `websocket`） | 出站 WSS `open.feishu.cn`（**已探测可达**）；`webhook` 模式需公网 IP | 是 | **(b)** 提供真实自建应用 app_id/app_secret |
| dingtalk 钉钉 | `channel/dingtalk/dingtalk_channel.py:152`（startup）、`:224`（ws 连接）、凭据 `:154-155`、`:797`（robot_code） | `channel_factory.py:83` | `dingtalk_client_id`、`dingtalk_client_secret`、`dingtalk_robot_code` | 出站 WSS `api.dingtalk.com`（**已探测可达**） | 是 | **(b)** 提供企业内部应用机器人凭据 |
| wecom_bot 企微智能机器人 | `channel/wecom_bot/wecom_bot_channel.py:121`（startup）、`:204`（ws）、凭据 `:146-147` | `channel_factory.py:86` | websocket 模式必填 `wecom_bot_id`、`wecom_bot_secret`；webhook 模式另需 `wecom_bot_token`、`wecom_bot_encoding_aes_key` | 出站 WSS `qyapi.weixin.qq.com`（**已探测可达**）；webhook 模式绑固定端口且**显式拒绝多实例**（`:127-138`） | 是 | **(b)** 提供智能机器人 id/secret；本仓已有一次真实记录 |
| weixin 微信（个人） | `channel/weixin/weixin_channel.py:131`（startup）、`:427`（长轮询）、凭据 `:134-136`；客户端 `channel/weixin/weixin_api.py:25-26`（`DEFAULT_BASE_URL=https://ilinkai.weixin.qq.com`） | `channel_factory.py:102` | `weixin_token`、`weixin_base_url`（扫码后由厂商回填；`APP_IDENTITY_KEYS` 取 `weixin_token`） | 出站长轮询 `ilinkai.weixin.qq.com`（**已探测可达**）+ 手机扫码 | 是 | **(c)** 结构性：入站无身份戳 → `external_channel_unsupported` |
| qq QQ 机器人 | `channel/qq/qq_channel.py:92`（startup）、`:201`（gateway）、凭据 `:99-100` | `channel_factory.py:89` | `qq_app_id`、`qq_app_secret` | 出站 WSS `api.sgroup.qq.com`（**已探测可达**） | 是 | **(c)** 结构性：入站无身份戳 |
| telegram | `channel/telegram/telegram_channel.py:77`（startup）、`:187`（长轮询）、凭据 `:78`、代理 `:130` | `channel_factory.py:92` | `telegram_token`；可选 `telegram_proxy` | 出站 HTTPS `api.telegram.org` —— **本次探测超时（不可达）** | 是 | **(c)** 双重：入站无身份戳 + 网络不可达 |
| slack | `channel/slack/slack_channel.py:59`（startup）、`:108`（`SocketModeHandler`）、凭据 `:60-61` | `channel_factory.py:95` | `slack_bot_token`（`xoxb-`）、`slack_app_token`（`xapp-`） | 出站 WSS `wss-primary.slack.com`（TCP 443 **已探测可达**） | 是 | **(c)** 结构性：入站无身份戳 |
| discord | `channel/discord/discord_channel.py:61`（startup）、凭据 `:62` | `channel_factory.py:98` | `discord_token` | 出站 WSS `gateway.discord.gg` —— **本次探测超时（不可达）** | 是 | **(c)** 双重：入站无身份戳 + 网络不可达 |
| wechatmp 公众号 | `channel/wechatmp/wechatmp_channel.py:66`（startup）、`:76`（HTTP 服务）、凭据 `:45-48` | `channel_factory.py:68`/`:71` | `wechatmp_app_id`、`wechatmp_app_secret`、`wechatmp_token`、`wechatmp_aes_key`、`wechatmp_port` | **入站 webhook**：需公网 HTTPS + 固定端口 | 否 | **(c)** 需公网回调且非多实例 |
| wechatcom_app 企微自建应用 | `channel/wechatcom/wechatcomapp_channel.py:65`（startup）、凭据 `:34-38` | `channel_factory.py:74` | `wechatcom_corp_id`、`wechatcomapp_secret`、`wechatcomapp_agent_id`、`wechatcomapp_token`、`wechatcomapp_aes_key`、`wechatcomapp_port` | **入站 webhook**：需公网 HTTPS + 固定端口 | 否 | **(c)** 同上 |
| wechat_kf 微信客服 | `channel/wechat_kf/wechat_kf_channel.py:109`（startup）、凭据 `:67-70` | `channel_factory.py:77` | `wechat_kf_corp_id`、`wechat_kf_secret`、`wechat_kf_token`、`wechat_kf_aes_key`、`wechat_kf_port` | **入站 webhook**：需公网 HTTPS + 固定端口 | 否 | **(c)** 同上 |
| web 控制台 | `channel/web/web_channel.py`（`WebChannel`） | `channel_factory.py:65` | 无厂商凭据（`web_password` + 会话 Cookie） | 本地 HTTP | n/a | **(a)** 本地可测，但**已由 3.6 浏览器验收覆盖**，不属段 7 的提供方往返范围 |
| terminal | `channel/terminal/terminal_channel.py` | `channel_factory.py:62` | 无 | 本地 stdin | n/a | **(a)** 本地可测，无提供方语义 |

### 1.2 凭据契约是「声明式」的，必填集并未覆盖全部类型

- 可存字段（写入白名单）：`CREDENTIAL_KEYS`（`channel/channel_instances.py:58`，8 个租户类型）。
- 必填字段：`REQUIRED_CREDENTIAL_KEYS`（`:509`），**只收录 6 个类型**（feishu / wecom_bot / qq / telegram /
  slack / discord），每项都被注释说明「来自该 channel 类自己的 startup 守卫」；`dingtalk` 与 `weixin`
  **刻意不在其中**（`:505-508` 注释），仍沿用旧规则「至少一个已声明字段非空」（`required_credential_keys`
  `:535-541`）。
- 「同一个外部应用」占用判定：`APP_IDENTITY_KEYS`（`:1063`），feishu=`feishu_app_id`、dingtalk=`dingtalk_client_id`、
  wecom_bot=`wecom_bot_id`、weixin=`weixin_token`（`:1069-1073` 记录了从 `weixin_base_url` 改为 token 的原因）。
- **未覆盖**：`dingtalk` 与 `weixin` 的**最小必填集**未核实（库内注释自认）。因此 7.4 若用空 secret 的钉钉凭据，
  会被服务接受但在 startup 失败——验收前必须先定死这一最小集，否则「保存成功」与「连接成功」会被混为一谈。

### 1.3 网络可达性（本次实测，2026-09-16）

```
$ curl -sS -o /dev/null -m 6 -w "http=%{http_code} ip=%{remote_ip}\n" https://<host>/
open.feishu.cn          http=404 ip=112.48.187.157     ← 可达
api.dingtalk.com        http=200                        ← 可达
ilinkai.weixin.qq.com   http=404                        ← 可达
slack.com               http=200                        ← 可达
qq.com                  http=302                        ← 可达
api.telegram.org        curl: (28) Connection timed out ← 不可达
discord.com             curl: (28) Connection timed out ← 不可达

$ python -c "socket.connect((host,443))"
wss-primary.slack.com   tcp443=OK
api.sgroup.qq.com       tcp443=OK
open.feishu.cn          tcp443=OK
api.dingtalk.com        tcp443=OK
ilinkai.weixin.qq.com   tcp443=OK
qyapi.weixin.qq.com     tcp443=OK
api.telegram.org        tcp443=FAIL TimeoutError
gateway.discord.gg      tcp443=FAIL TimeoutError
```

SDK 依赖齐备（`lark_oapi` / `slack_bolt` / `discord` / `websocket` / `websockets` / `Crypto` / `requests` 均可 import）。
`telegram_proxy`（`config.py:207`）与 `proxy`（`config.py:35`）为空 → telegram/discord 若要走真实往返必须先给代理。
**未覆盖**：Slack Socket Mode 只探测到 `wss-primary.slack.com:443` TCP 可达，**未做 app-level token 握手**，
故「slack 网络可用」仅为传输层结论。

### 1.4 结构性阻断：入站身份戳只有 3 个提供方实现

数据库模式下每一条非 web 入站都要先解析到租户成员（`channel/chat_channel.py:217-233`
`_needs_external_db_mapping`；`external_identity.is_database_mode()` 恒为 `True`，`channel/external_identity.py:79-86`）。

| 提供方 | 是否 stamp 外部身份 | 证据 |
| --- | --- | --- |
| feishu | 是（provider=`feishu`，issuer=`feishu_app_id`，subject=`actual_user_id`/`from_user_id`） | `channel/feishu/feishu_channel.py:825-831` |
| wecom_bot | 是（issuer=`to_user_id`/`bot_id`；**issuer 为空时不戳**，注释给出「两个 bot 会合并成一行」的理由） | `channel/wecom_bot/wecom_bot_channel.py:602-635` |
| dingtalk | 是（私聊 `:702-707`、群聊 `:775-780`，issuer=`dingtalk_client_id`） | `channel/dingtalk/dingtalk_channel.py:702-707`、`:775-780` |
| telegram / slack / discord / qq / weixin / wechatmp / wechatcom_app / wechat_kf | **否**（全目录 grep `external_identity` 零命中） | `rg -n "external_identity" channel/{telegram,slack,discord,qq,weixin,wechatmp,wechatcom,wechat_kf}/` → 无输出 |

后果：这些类型的入站走到 `channel/chat_channel.py:511-519` 时因 `context["external_identity"]` 为空被拒，
发固定文案 `UNSUPPORTED_CHANNEL`（`channel/external_identity.py:115-118` 的 `deny_notice`），
**且不会进入任何绑定查找**——即「绑了用户也没用」。
这解释了 archive 里「微信个人执行」长期未通过：它不只是缺提供方账号，**入站身份链路本身缺失**。

> 这是**当前树**的结论；若 7.2 的合并顺带补齐这些提供方的 stamp，本条即失效，须重跑本节 grep 复核。

## 2. 身份绑定

### 2.1 三要素与存储

- 绑定键是 `(provider, issuer, subject)` 三元组，**全局唯一**：`external_identities`（`auth/store.py:281-293`，
  唯一索引 `idx_external_identities_triple`）。
- `provider` 归一化为小写并受 `^[a-z0-9][a-z0-9_-]{0,31}$` 约束（`auth/service.py:367` `_PROVIDER_RE`）；
  `issuer`/`subject` 仅 trim（`issuer` ≤256、`subject` ≤512）。
- 绑定入口：平台 `bind_external_identity`（`auth/service.py:4954`，**仅平台管理员**）、租户
  `bind_external_identity_for_tenant`；两者  共用 `_bind_external_identity_row`，同一事务内审计
  `external_identity.bind` **并清空该三元的待绑定尝试**（`auth/service.py:5039-5046`，`DELETE FROM
  external_identity_attempts`）。
- 解绑：`delete_external_identity`（`:5048`，审计 `external_identity.unbind`，并级联删除
  `personal_channel_links`（`:5040`）——即「解绑即时断掉本人路由」）。
- 解析：`find_user_for_external_identity`（`:5305`），只返回 **active 用户**。

### 2.2 入站如何落到正确的成员

`resolve_actor_for_context`（`channel/external_identity.py:410-473`）是唯一决策点，顺序为：

1. 三元组不完整 → `UNBOUND`；
2. `find_user_for_external_identity` 无命中 → `UNBOUND`（`:441-443`）；
3. 租户锚点：**优先实例行的 `tenant_id`**，回落到被路由 Agent 的绑定；两者皆无 → `TENANT_UNBOUND`
   （`:445-451`；注释明确「实例归属于唯一租户，伪造 `bound_agent_id` 不得把会话挪到别的组织」）；
4. 成员资格 + `must_change_password` → `NOT_MEMBER` / `PASSWORD_CHANGE_REQUIRED`（`:453-461`）；
5. 功能闸门 `chat.use` 与资源闸门 `agent:<id>` 的 `use`（管理员豁免）→ `PERMISSION_DENIED`（`:466-472`）。

拒绝原因码全集见 `channel/external_identity.py:28-47`，文案见 `deny_notice`（`:88-157`）。
被拒消息会落 `external_identity_attempts`（`record_external_identity_attempt` `auth/service.py:5173`；
表 `auth/store.py:558`），供管理员在待绑定列表里认出「谁来敲过门」；挑战码形状的消息**不落正文**
（`looks_like_binding_code` / `binding_code_token` `channel/external_identity.py:340-381`，长度 8，
`auth/service.py:307` `_CHALLENGE_CODE_LENGTH`）。

### 2.3 本人连接的两条绑定路径

- **本人实例**（`scope='user'`）：实例属于成员，入站只服务其 owner。决策
  `resolve_personal_channel_inbound`（`auth/service.py:9767-9840`）：`not_found` / `not_personal` /
  `channel_type_not_ready` / `instance_disabled` / `governance_disabled` / `group_not_personal` /
  `credential_revoked` / `not_linked` / `sender_not_owner` / `identity_unavailable` / `member_inactive` /
  `no_target_agent` / `target_not_owned` / `target_not_authorized`。
- **共享实例上的本人路由**：`personal_channel_links`（`auth/store.py:989`）+ `link_personal_channel`
  （`auth/service.py:5556`）+ `resolve_shared_personal_route`（`:9858`）。此路径额外受三段闸门
  `_require_public_personal_ingress`（`:9448`）：类型能按实例证明发送者 → 该类型已登记验收
  → 租户仍允许个人访问（`_channel_policy_for` `:9438` 读 `tenant_channel_policies.personal_enabled`）。
- 绑定码是「控制权证明」：浏览器只拿码，码必须**从 IM 账号发回**，入站时用消息戳的三元组兑换
  （`start_personal_channel_binding` `:9489`、`create_binding_challenge` `:5388`、
  `consume_binding_challenge` `:5459`、入站兑换 `channel/chat_channel.py:383-422`）。

### 2.4 真实验收必须搭建的身份现场

1. 一个 active 成员账号，在该租户有 active 成员资格（`member_context`）；
2. 该成员持 `chat.use`，且对**目标智能体**持 `use` 资源授权（`resolve_actor_for_context` 第 5 步）；
3. `external_identities` 一行，其 `issuer` 必须**恰好等于**该实例真正对外呈现的应用标识
   （feishu=app_id、dingtalk=client_id、wecom_bot=bot_id/aibotid——注意 wecom_bot 的 issuer 每消息取 `aibotid`，
   与实例配置同值）；
4. 若要验「他人拒绝」：同一实例上第二个真实账号（未绑定，或已绑到别人）需能真实发出一条消息。

### 2.5 开发库现状（只读查询 `identity.db`）

```
tenant_channel_instances : 2 行，均 scope='tenant'、active=1
  chan_FBlxbCbCv0EZajdZ | tnt_EA3qM-lHPLD8ZPwW | feishu    | erp                       | agent=business-analysis-test15
  chan_dESCeabBWsskMEvi | tnt_EA3qM-lHPLD8ZPwW | wecom_bot | 税务健康体检              | agent=tax-health-check-test15
external_identities      : 2 行
  feishu    / cli_aa2817b7ac381cb4 / ou_1bbcd135da090af83e652c087676d1ea → usr_EMjtqQ_5s9oey1y1
  wecom_bot / aibeJrsnNgs8WBmu2BgW4YaHGD2LNjxjBnV / TanJian             → usr_9ZxVPz7M2FuOro1q
personal_channel_links   : 0 行
binding_challenges       : 0 行
tenant_channel_policies  : 0 行（缺行 = 未收窄，`personal_enabled` 视为 1）
credentials              : 2 行 `channel:<instance_id>`，密文长度 172 / 204 字节
```

**未覆盖**：上述两条密文对应的真实凭据是否仍然有效；本环境无主密钥，无法解密，也不应尝试。

## 3. 运行路径（逐阶段）

### 3.1 连接管理：创建 / 修改 / 启停

| 阶段 | 处理器 | 业务服务 | 运行时副作用 |
| --- | --- | --- | --- |
| 创建 | `TenantChannelsHandler.POST`（`channel/web/admin_handlers.py:1036` 类、POST 起于 `:1084`） | `create_tenant_channel_instance`（`auth/service.py:8064`） | `_apply_channel_runtime`（`admin_handlers.py:957`）→ `apply_tenant_instance_runtime`（`channel/channel_instances.py:767`） |
| 修改 | `TenantChannelHandler.POST`（`admin_handlers.py:1122`） | `update_tenant_channel_instance`（`auth/service.py:8398`） | 同上 |
| 启停 | `TenantChannelActiveHandler.POST`（`admin_handlers.py:1157`） | `set_tenant_channel_instance_active`（`auth/service.py:8608`） | 同上 |

- 归属**不读请求体**：`_requested_channel_scope`（`admin_handlers.py:990`）按调用者范围与目标派生，
  服务侧再做 `channel_target_scope`（`auth/service.py:8347`）+ `_resolve_instance_scope`；写锁内由
  `_require_channel_instance_in_range`（`:8372`）按行复判——同一行同时服务管理员与普通 owner 两种范围。
- 凭据：`_validated_channel_bundle`（`auth/service.py:7611`）→ `encrypt_secret`（`auth/crypto.py:48`）→
  `credentials` 表一行 + `credential_versions` 版本，读回走 `channel_instance_credentials`（`auth/service.py:8754`）。
- 授权材料：`_require_channel_write_authorization`（`:4837`）接受「近期密码」或扫码授予的 `scan_ticket`；
  ticket 与提交面/目标绑定并**原子领取**（`create_tenant_channel_instance` 内 `scan_authorization.claim`）。

### 3.2 启动合成（进程启动时拉起哪条连接）

`app.py:103` `resolve_channel_instances(settings, tenant_instances)` → `app.py:189` `ChannelManager.start`
（每条渠道一个 daemon 线程，`app.py:279` `_run_channel`）。租户实例来自
`load_tenant_channel_instances`（`channel/channel_instances.py:904`），它逐行：
`scope='user'` 时依次过「本人执行开关（`:946`）→ owner 仍是 active 成员（`:953`）→ 目标智能体仍可用（`:960`）」，
然后才解密凭据（`:972`）并组 `ChannelInstance`。**`scope='tenant'` 不走这三道个人闸门**。
凭据不可解密的实例**只跳过自己**，不阻断进程（`:973-978`）。

### 3.3 热更新（保存即生效）

`apply_tenant_instance_runtime`（`channel/channel_instances.py:767`）按实例加锁（`_instance_restart_lock` `:664`），
顺序为：读行 → 未启用/已删则停 → （本人）owner/目标/运行开关 → 解密凭据 → **先停旧的**
（`_stop_instance_runtime` `:703`，「旧凭据绝不留在服务中」）→ `mgr.restart(inst)`（`:872`）。
`ChannelManager.restart` / `add_channel` / `remove_channel` 见 `app.py:354` / `:392` / `:417`，
配 `_clear_singleton_cache`（`app.py:430`）清单例闭包缓存。

**关键限制**：`_runtime_manager()`（`:691`）从 `sys.modules["__main__"]` / `app` 取 `_channel_mgr`；
若本进程不跑渠道（例如只跑 console / 测试 harness），一律记 `applied=False, pending=True`（`:861-866`）。
因此 **7.4 的「可对话」判定必须在真正跑渠道的应用进程里观察**，否则永远只能得到 pending。

状态语义被刻意拆开：`instance_connection_state`（`:569`）把 `saved`（有行）与 `connected`（本进程内有连接）
分开报，并在本人实例上把开关关闭报成 `not_connected` + 原因（`:626-630`），不会两者混说。

### 3.4 入站分发

`produce`（`channel/chat_channel.py:887`，投递到会话队列）→ `_handle`（`:191`）→
`_needs_external_db_mapping`（`:217`，web 豁免见 `:230`）→ 一分为二：

- 实例 `scope='user'` → `_preflight_personal_inbound`（`:236`）：先兑换绑定码（`:260-268`），
  再 `resolve_personal_channel_inbound`，任何不允许都**发通知并终止**，**不回落**；
- 否则 → `_preflight_external_inbound`（`:424`）：租户锚定与 Agent 钉死（`:450-509`，路由回落即拒），
  身份戳缺失即拒（`:511-519`），共享实例上的本人路由优先于公共身份映射（`:539-544`），
  最后 `resolve_actor_for_context`。通过后 `_scope_to_member`（`:319`）重建成员上下文并把
  `runtime_identity` 钉在 context 上（`:367-378`）。

## 4. 执行审批 / 隔离 / 配额 / 治理切片

| 切片 | 对「运行一条渠道」是否适用 | 证据 | 真实验收是否必须演练 |
| --- | --- | --- | --- |
| `action-approval` | **不适用**。`approval_required_actions` 默认空串（`config.py:300`）；id 方案虽声明了 `channel:<action>`（`agent/approval_gate.py:56-60`、`channel_action_id` `:194-196`），但该函数**全仓无消费者**；真实消费者只有工具分发（`agent/protocol/agent_stream.py:2100`）与调度投递（`agent/tools/scheduler/integration.py:499`） | 同左 | 只能验证「未声明即不拦」；若要验证「拦得住」，须人工声明一个 `channel:*` 动作——但**没有消费者可拦**，故本切片对本任务不构成前置（与 `evidence/1-4-prerequisite-slices.md` 结论一致） |
| `resource-quota` | **部分适用**。渠道创建走专用计数器而非 `quota_limits`：`_enforce_personal_instance_policy`（`auth/service.py:7830`）+ `tenant_channel_policies`（`auth/store.py:1110-1120`，`personal_instance_limit` / `tenant_personal_instance_limit`，缺行 = 未收窄） | 同左 | 是（7.4「配额/应用竞争」）——需在**同一租户**准备两条本人连接以触发上限；开发库当前 `tenant_channel_policies` 为 0 行，不够 |
| 治理停机 | **适用**：`set_personal_instance_governance`（`auth/service.py:8000`）写 `governance_disabled_at/by` 并同时置 `active=0`（`:8000-8008`），入站再独立判一次（`resolve_personal_channel_inbound` 的 `governance_disabled`）；治理只对 `scope='user'` 生效（`:7996`） | 同左 | 是（7.5「治理停用」） |
| 执行隔离 | **适用**：`agent/permission/isolation.py:61-74`（数据库模式下切片关闭时仍拒绝任意代码工具）、`:351 isolation_decision` | 同左 | 是（7.4/7.5 只要真实跑工具就必须观察一次越界拒绝，见 `evidence/1-3` 的 Q3 门槛） |
| 凭据加密 | **硬前置**：`auth/crypto.py:22/31`，密钥未配置则加解密都不可用 | 同左 | 是（见 §6） |
| 审计 | **适用**：渠道实例创建/更新/启停/治理/绑定均写 `audit_events`（`channel.instance.*`、`channel.personal.governance_*`、`external_identity.*`） | `auth/service.py:8013-8016` 等 | 是（7.5 轮换失败/撤权要能对上审计） |


## 更正（由 `7-2-7-3-runtime-merge-audit.md` 复核后修订）

本文件原判定 `personal_channel_onboarding` 在**写入路径**上生效，依据是
`create_tenant_channel_instance` 的本人分支（旧入口）。该判定**有误**：核实后确认

* 旧入口包装确实正确拒绝（`legacy create → capability_disabled 403`）；
* **共用写入入口不读该开关**——`/api/tenant/channels` 的新建与重新启用仍返回 200 并落库，
  而同页面的扫码门（`weixin_scan_adapter.py`）读它，形成「同一面两个答案」；
* `personal_channel_ready` 只读声明集与部署收窄键，**不读该开关**，故不能作为「已生效」的依据。

因此本文件第 3 节把该开关列为「已生效」是不成立的，正确状态为**部分**。修复与复核见
`7-2-7-3-runtime-merge-audit.md` 的 R1；阻塞 7.4–7.6 的三条结论（主密钥缺失、仅三个适配器打
身份戳、本人连接双关闭）不依赖此条，不受影响。
## 5. 旧个人开关（是否仍存在、是否仍被运行期读取）

| 开关 | 位置与默认 | 运行期是否读取 |
| --- | --- | --- |
| `personal_channel_onboarding` | `auth/policy.py:557`（声明）、`:570`（默认 **True**）、`config.py:289` | **部分**：（原判「是」有误，见下方更正行）页面投影读它（`_PERSONAL_PAGE_META` `auth/service.py:173`，`personal.channels` 条目 `:174-176`）；旧入口包装会正确 403。但**共用写入入口不读它**——`/api/tenant/channels` 的新建与重新启用仍成功（探针：`legacy create → capability_disabled 403`，而 `shared create → 200 OK`）。见 `evidence/7-2-7-3-runtime-merge-audit.md` R1 |
| `personal_channel_runtime` | `auth/policy.py:558`、`:571`（默认 **False**）、`config.py:290` | **是**，三处：热更新（`channel/channel_instances.py:825-838`）、启动合成（`:946`）、入站（`auth/service.py:9764` 的 `channel_type_not_ready`）。判定函数 `personal_runtime_enabled`（`channel/channel_instances.py:1320`）= 总开关 **且** 该类型在 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 中 |
| 按类型验收记录 `PERSONAL_RUNTIME_ACCEPTED_TYPES` / `PUBLIC_PERSONAL_INGRESS_TYPES` | `channel/channel_instances.py:1276` / `:1282`，**均为空 frozenset** | **是**（`:1338` / `:1355`） |
| 部署收窄键 `personal_channel_ready_types` / `personal_channel_runtime_types` / `public_personal_ingress_types` | 读于 `:1210` / `:1338` / `:1355` | **是**，且只允许**收窄**（`_configured_subset` `:1285-1303`：配置里点名集合外的类型无法加回来） |
| 租户级 `tenant_channel_policies.personal_enabled` | `auth/store.py:1113` | **是**——共享实例的个人路由（`_require_public_personal_ingress` `auth/service.py:9448`，经 `_channel_policy_for` `:9438`） |
| 旧个人页面 consumer `personal.channels` / 执行态 `personal_channel_execution` | `auth/service.py:169-170`（页面元数据）、`:3884` `_personal_channel_execution_open` | **是，但无第二份真值**：它由 `personal_runtime_enabled` 现算（`:3884-3894`），不是独立开关 |
| 用户级开关 | `users` 表**无**渠道相关列（`auth/store.py:79-91`） | 每用户开关即实例行 `active`（owner 自己启停）+ `personal_channel_links.active`（路由级） |

**结论**：不存在「已废弃但仍被读取」的旁路开关；现存开关都收敛到 `personal_runtime_enabled` 一处判定。
风险方向相反：开关**默认全关**，7.4 若不显式打开，本人连接只会被报成「已保存、未连接」。

## 6. 7.4 / 7.5 / 7.6 必须提供的最小清单

### A. 环境级（对所有提供方，缺一即无法真实验收）

1. **`COW_CREDENTIAL_MASTER_KEY`**——与 `identity.db` 内既有密文同一把（64 hex 或同一 passphrase）。
   缺它则：既有实例无法解密启动（`channel/channel_instances.py:973-978` 跳过并报错），新凭据无法加密写入
   （`auth/crypto.py:31-39` 直接抛）。**目前缺失**（§0.1）。
2. **一个真正运行渠道的应用进程**（`app.py`，`_channel_mgr` 已装配），而非只跑 console / 测试进程；
   否则 `apply_tenant_instance_runtime` 永远返回 `pending`（`channel/channel_instances.py:861-866`）。
3. **出站网络**：feishu / dingtalk / wecom_bot / weixin 已实测可达；telegram / discord 需先给
   `telegram_proxy` / `proxy`。
4. **身份现场**：至少两个 active 成员（一个绑定、一个不绑定或绑到别人），成员持 `chat.use` 且对目标智能体持
   `use`；`external_identities` 行的 `issuer` 必须等于该实例对外的应用标识。
5. **目标智能体**：必须是该租户下 active、可路由的智能体（租户公共实例还要求租户公共缺省 Agent 可解析，
   否则 `AGENT_UNAVAILABLE`，`channel/chat_channel.py:454-465`）。

### B. 逐提供方：能做什么、要什么

| 提供方 | 现在能做吗 | 需要你提供 |
| --- | --- | --- |
| **feishu** | 可以（给凭据后） | 一个**真实飞书自建应用**的 `feishu_app_id` + `feishu_app_secret`，并开启**长连接事件订阅**（默认 `feishu_event_mode=websocket`，不需要公网回调）；至少两个真实飞书账号（一个绑定、一个不绑定用于验拒绝）；可选 `feishu_bot_name` |
| **dingtalk** | 可以（给凭据后） | 企业内部应用机器人 `dingtalk_client_id` + `dingtalk_client_secret` + `dingtalk_robot_code`，开启 **Stream 模式**；至少两个真实钉钉账号。**另需**先定死钉钉最小必填集（§1.2 未覆盖） |
| **wecom_bot** | 可以（给凭据后） | 企微**智能机器人** `wecom_bot_id` + `wecom_bot_secret`（websocket 模式）；至少两个真实企微成员。本仓已有一次真实记录（`chan_dESCeabBWsskMEvi`），若该机器人仍在，可优先复现 |
| **weixin（个人微信）** | **不能**（结构性） | 即使提供扫码手机与 `weixin_token`，入站无身份戳 → 恒拒。需先补 `stamp_external_identity`（§1.4） |
| **telegram** | **不能**（双重） | 需 `telegram_token` + 可达网络（当前超时）+ 补身份戳 |
| **slack** | **不能**（结构性） | 需 `slack_bot_token`/`slack_app_token` + 补身份戳（网络层可达） |
| **discord** | **不能**（双重） | 需 `discord_token` + 可达网络（当前超时）+ 补身份戳 |
| **qq** | **不能**（结构性） | 需 `qq_app_id`/`qq_app_secret` + 补身份戳（网络层可达） |
| **wechatmp / wechatcom_app / wechat_kf** | **不能**（结构性 + 形态） | 需公网 HTTPS 回调 + 固定端口，且三者都不在 `MULTI_INSTANCE_READY`（不接受租户自有实例），另需补身份戳 |
| **web / terminal** | 现在就能，但无提供方语义 | 不加条件；已由 3.6 覆盖，不作为 7.4 的提供方结论 |

### C. 打开「本人连接」运行（属 7.5/7.6 动作，不是凭据）

按 7.6「取得真实证据后按共用配置显式启用」，顺序应为：

1. 先取得至少一个类型的**真实入站往返证据**（7.4）；
2. 把该类型加入 `PERSONAL_RUNTIME_ACCEPTED_TYPES`（`channel/channel_instances.py:1276`）；
   若验的是共享实例上的本人路由，再加 `PUBLIC_PERSONAL_INGRESS_TYPES`（`:1282`）；
3. 打开总开关 `personal_channel_runtime`（`config.json` 或策略配置）；
4. 可选：用 `personal_channel_runtime_types` / `public_personal_ingress_types` 把集合收窄到已验收类型。

**注意**：第 2 步是**代码常量**编辑，必须与 7.4 的真实证据同时提交；在证据出现前修改它，
就是把未验证边界打开（`:1265-1275` 的注释已明确禁止）。

### D. 结论：7.4 能覆盖的与不能覆盖的

- **可覆盖**：feishu / dingtalk / wecom_bot 的**公共连接**（`scope='tenant'`）真实入站往返，
  含「未绑定被拒」与「绑定后落到正确成员」；`scope='tenant'` 不受个人运行开关影响。
- **不可覆盖（本轮）**：任何**本人连接**（`scope='user'`）往返（需 §C 步骤 1-3）；
  以及 §1.4 列出的 8 个提供方（需先补身份戳，属代码改造）。
- 7.6 的「显式启用」必须等 7.4 的真实证据，且**不得**用 web/Terminal 通过或合成测试通过代替。

## 7. 未覆盖清单（含原因）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| `identity.db` 内两条渠道密文的真实凭据是否有效 | **未覆盖** | 无 `COW_CREDENTIAL_MASTER_KEY`，不可解密；且不应尝试解密开发者数据 |
| 2026-09-14 wecom_bot 真实往返能否复现 | **未覆盖** | 归档只留文字结论；当时主密钥/会话未留痕，机器人是否仍在线未知 |
| dingtalk / weixin 的最小必填凭据集 | **未覆盖** | 库内注释自认未核实（`channel/channel_instances.py:505-508`），未做真实 startup 对照 |
| Slack Socket Mode 是否可真实握手 | **未覆盖** | 仅探测 `wss-primary.slack.com:443` TCP 可达，未做 app-level token 握手 |
| telegram / discord 是否可经代理解除 | **未覆盖** | 未探测任何代理；`telegram_proxy` / `proxy` 当前为空 |
| 各提供方「他人/群聊拒绝」的真实观察 | **未覆盖** | 需真实第二个账号与真实群；本轮只读预检不发送消息 |
| weixin 扫码（`ilinkai`）厂商侧是否仍开放 | **未覆盖** | 仅为传输层可达结论；未发起扫码会话 |
| 并发保存/双请求配额竞争的真实行为 | **未覆盖** | 属 7.4 现场演练，本轮不改状态、不写库 |
| 本文件行号在并发编辑下的稳定性 | **部分失效风险** | 写作期间 `channel/web/*`、`auth/*` 被并发编辑；已按函数名锚定，引用前请以名复核 |

## 8. 可复跑证据

```bash
# 1) 主密钥缺失（阻断项 1）
env | grep COW_CREDENTIAL || echo "(COW_CREDENTIAL_MASTER_KEY not in env)"
# → (COW_CREDENTIAL_MASTER_KEY not in env)

# 2) 入站身份戳覆盖（阻断项 2）
rg -n "external_identity|stamp_external" channel/{telegram,slack,discord,qq,weixin,wechatmp,wechatcom,wechat_kf}/
# → 无输出（仅 feishu / dingtalk / wecom_bot 命中）

# 3) 本人运行开关（阻断项 3）
rg -n "PERSONAL_RUNTIME_ACCEPTED_TYPES|PUBLIC_PERSONAL_INGRESS_TYPES" channel/channel_instances.py
# → 1276: PERSONAL_RUNTIME_ACCEPTED_TYPES: frozenset = frozenset()
# → 1282: PUBLIC_PERSONAL_INGRESS_TYPES: frozenset = frozenset()
rg -n '"personal_channel_runtime"' auth/policy.py config.py
# → auth/policy.py:571:  "personal_channel_runtime": False,
# → config.py:290:       "personal_channel_runtime": False,

# 4) 提供方清单与凭据契约
rg -n "^CREDENTIAL_KEYS|^REQUIRED_CREDENTIAL_KEYS|^MULTI_INSTANCE_READY|^APP_IDENTITY_KEYS" \
   channel/channel_instances.py

# 5) 网络可达性
for h in open.feishu.cn api.dingtalk.com ilinkai.weixin.qq.com api.telegram.org discord.com; do
  printf "%-24s " "$h"; curl -sS -o /dev/null -m 6 -w "http=%{http_code}\n" "https://$h/" || true; done

# 6) 开发库现状（只读）
sqlite3 "file:identity.db?mode=ro" \
  "SELECT id,tenant_id,scope,channel_type,display_name,active FROM tenant_channel_instances;"
sqlite3 "file:identity.db?mode=ro" \
  "SELECT provider,issuer,subject,user_id FROM external_identities;"
```

（以上均已在 2026-09-16 本轮实际执行；无任何写入。）
