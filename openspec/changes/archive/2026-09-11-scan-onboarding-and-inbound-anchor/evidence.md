# 验收证据：scan-onboarding-and-inbound-anchor

本文件给出四组能力的可复现命令与实际输出：注册会话绑定、即时生效、最小必填集、入站锚点，另含全量回归与基线对照。
产物完成不等于实现完成；启用门槛见第 7 节。

## 1. 前置检查与顺序约束

### 1.1 前置 change `tenant-owned-message-channels`

```
$ rg -c "^- \[x\]" openspec/changes/tenant-owned-message-channels/tasks.md
65
$ rg -c "^- \[ \]" openspec/changes/tenant-owned-message-channels/tasks.md
（无输出，即 0 项未完成）
$ openspec list | grep tenant-owned-message-channels
tenant-owned-message-channels                   ✓ Complete    3h ago
```

- 实现与任务：**已齐备**。
- 归档状态：**尚未归档**（`openspec/changes/archive/` 中无对应目录）。其 10.8 启用门槛未满足：`open-database-runtime` 仍为 active。

### 1.2 前置 change `open-database-runtime` 的 4.x 与 7.x

```
$ rg -n "^- \[[ x]\] (4|7)\." openspec/changes/open-database-runtime/tasks.md
4.1 [x] 4.2 [x] 4.3 [x] 4.4 [x]
7.1 [x] 7.2 [x] 7.3 [x] 7.4 [x]
$ openspec list | grep open-database-runtime
open-database-runtime                          38/45 tasks   1d ago
```

- 切片 4.x（入站身份绑定）与 7.x（凭据存储与注入）：**验收证据齐备（全绿）**。
- 整份 change：**仍 active（38/45）**，故其归档门槛未满足。

### 1.3 多 worker 拒绝仍成立

```
$ rg -n "def reject_multi_worker_identity" -A 15 auth/ratelimit.py
230:def reject_multi_worker_identity():
     ...
244:    if mode != "database":
245:        return
```

- 结论：`identity_mode=database` 下多 worker 部署被拒绝，故注册会话状态可留在进程内，只需**归属正确**。

### 1.4 归档顺序硬依赖（实证）

本 change 的 `tenant-channel-configuration` delta 使用 MODIFIED/RENAMED，目标规范当前只存在于在途 change `tenant-owned-message-channels`。

```
$ ls openspec/specs/ | grep -i tenant-channel
（无输出：主规范库尚无该文件）
```

归档演练（隔离副本，未触碰仓库）：

```
# 反例：先归档本 change（基线未归档）
$ openspec archive scan-onboarding-and-inbound-anchor -y
error archive_spec_update_failed:
  tenant-channel-configuration: target spec does not exist;
  only ADDED requirements are allowed for new specs.

# 正例：先归档基线，再归档本 change
$ openspec archive tenant-owned-message-channels -y
  archivedAs: 2026-09-10-tenant-owned-message-channels  added: 8
$ openspec archive scan-onboarding-and-inbound-anchor -y
  archivedAs: 2026-09-10-scan-onboarding-and-inbound-anchor
  totals: { added: 5, modified: 6, removed: 0, renamed: 1 }
```

- **顺序约束**：MUST 先归档 `tenant-owned-message-channels`，再归档本 change。
- 该错误**不会在 `openspec validate` 阶段暴露**，只在归档时失败。

---

## 2. 注册会话绑定发起者（P0）

关键实现：`channel/web/web_channel.py` 的 `FeishuRegisterHandler` 把进程级单例 `_state` 换成按不透明句柄索引的 `_sessions`，每个会话绑定 `(owner_user_id, owner_tenant_id)`（`_create_session` / `_session_for` / `_set_status` / `_poll_payload` / `_purge_expired_locked`）；`_register_owner_scope()` 从已认证调用者与所选租户决定归属；`_start_register_thread` 不再在启动新会话时取消其他身份的会话。`auth/http_policy.py` 把 `/api/feishu/register` 的 `GET`（启动）与 `POST`（轮询）都登记为 `personal`，此前只登记 POST，故启动请求被完整性闸门拦成 405。

```
$ .venv/bin/python -m pytest tests/test_feishu_register_session.py -q
...........                                                              [100%]
11 passed in 0.14s

$ .venv/bin/python -m pytest tests/test_http_policy.py -q
31 passed, 1 warning in 2.40s

$ node --test tests/test_feishu_register_frontend.cjs
ℹ tests 4
ℹ pass 4
ℹ fail 0
```

覆盖：只有发起者能读到自己的会话（他人句柄与不存在句柄返回同一结果，不泄漏会话是否存在）；归属含租户维度；句柄不透明且互不相同；新会话不取消其他身份的会话，但同一身份的新会话会取代其旧会话；凭据只回传一次、终态（error/expired/denied）不含凭据、进行中只报进度不含凭据；他人的轮询响应体与注册相关日志行都不出现明文密钥。

## 3. 保存后即时生效（P1）

关键实现：`channel/channel_instances.py` 的 `apply_tenant_instance_runtime`（按实例串行化，先停旧run再按解密后的凭据启动，失败只报告不抛出）、`channel/web/admin_handlers.py` 的 `_apply_channel_runtime`（三条写路径接入，结果随响应返回 `runtime`）、`channel/web/static/js/console.js` 的运行时提示（`tenantChannelRuntimeNoticeFrom` / `tenantChannelRuntimeNoticeHtml`）。

运行时协调**不按身份模式短路**（design D5 与平台侧先例 `_handle_instance_save` 均无此判断）：该函数只从租户渠道写路径进入，行查询本身已能如实回答「有没有要跑的东西」，模式判断反而会把已落库的实例报成已生效。

```
$ .venv/bin/python -m pytest tests/test_tenant_channel_hot_restart.py -q
.......                                                                  [100%]
7 passed in 1.54s
```

覆盖：创建/编辑后立即按新凭据重建（解密后的凭据而非掩码投影、且带 `tenant_id`）；凭据解密失败时旧 run 不停留在服务中（`stopped` 命中且 `restarted` 为空）；同一实例 4 线程并发保存的 `max_concurrent == 1`；启动失败保留实例与版本号并返回可诊断原因；本进程无 `ChannelManager` 时报告 `pending`（`applied=false`）而非假装已生效；停用即回退（停止、`applied=true`、实例记录与版本历史保留）。

写路径接线（4.7）：

```
$ .venv/bin/python -m pytest tests/test_tenant_channel_http.py -q
...............................                                          [100%]
31 passed in 15.09s
```

新增的 5 项断言写路径行为：创建即把实例投入服务；应用失败时保存仍返回 200 且 `runtime.error` 可诊断；运行时异常不会把已提交的保存变成失败；编辑使轮换后的凭据到达运行中的通道；停用即生效。

前端契约：

```
$ node --test tests/test_tenant_channel_frontend.cjs
ℹ tests 18
ℹ pass 18
ℹ fail 0
```

覆盖：未生效时提示含原因、已生效时不出现「尚未生效」；无 `runtime` 字段时不产生提示；提交与启停两条路径都记录运行结果；列表渲染该提示；三语 `tenant_channel_secret_note` 均已改为即时生效语义（不再出现「维护窗口 / maintenance」字样）。

## 4. 最小必填凭据集（P1）

关键实现：`channel/channel_instances.py` 的 `REQUIRED_CREDENTIAL_KEYS` / `required_credential_keys()`（与 `CREDENTIAL_KEYS` 并列，且 `tenant_channel_types()` 把 `required` 一并下发给前端，前后端无第二份清单）、`auth/service.py` 的 `_validated_channel_bundle`（最小必填集判定）与 `_merge_rotation_bundle`（轮换合并）。

必填集逐条来自渠道类自身的启动守卫，不是从字段名猜的：

| 类型 | 依据 | 必填键 |
| --- | --- | --- |
| feishu | `feishu_channel.py` 的 app_id/app_secret 守卫 | `feishu_app_id`, `feishu_app_secret` |
| wecom_bot | `wecom_bot_channel.py` 的 id/secret 守卫（websocket 模式；webhook 模式为单实例且拒绝额外实例，故 token/aes 不在租户侧必填） | `wecom_bot_id`, `wecom_bot_secret` |
| qq | `qq_channel.py` | `qq_app_id`, `qq_app_secret` |
| telegram | `telegram_channel.py` | `telegram_token` |
| slack | `slack_channel.py` | `slack_bot_token`, `slack_app_token` |
| discord | `discord_channel.py` | `discord_token` |

`dingtalk` / `weixin` 显式不在集合内（尚未核实），仍沿用「至少一个已声明字段非空」的旧规则，避免凭猜测拒绝今天可用的写入。

```
$ .venv/bin/python -m pytest tests/test_tenant_channel_required_credentials.py -q
................                                                         [100%]
16 passed in 3.67s
```

覆盖：必填键必须是已声明键的子集、已核实集合恰为六类且未核实的两类为空、表单契约的 `required` 标注与声明一致；缺少任一必填键（含只提交可选键、必填键只有空白）时创建被拒且不落库实例或凭据、覆盖全部六类；完整凭据仍可创建；无已核实集合的类型沿用旧规则。

**实施期发现并修复的缺陷（5.7）**：轮换原为整体替换凭据包，而控制台承诺「密钥类字段留空表示保持原值不变」。实测只重填密钥会把 App ID 丢掉：

```
$ .venv/bin/python /tmp/probe_rotation.py     # 修复前
after create: {'feishu_app_id': 'cli_acme', 'feishu_app_secret': 's3cr3t'}
after partial rotate: {'feishu_app_id': 'cli_acme'}      # ← secret 被静默丢弃
```

修复后轮换在已存凭据包之上合并，最小必填集按「合并后的生效集合」判定：只重填密钥保留 App ID；会留下缺项的轮换被拒且不改动已存凭据；对既有的不完整实例，普通列表既不会删除也不会改写它，但下一次编辑必须补齐必填集（兼容路径单测）。

前端契约：

```
$ node --test tests/test_tenant_channel_frontend.cjs
ℹ tests 23
ℹ pass 23
ℹ fail 0
```

覆盖：必填性来自服务端声明（`tenantChannelMissingRequiredFields` 只认声明、未知类型不擅自推断）、必填字段带标记而可选字段不带、创建缺失必填项时不发起请求且保留已填草稿、且拒绝文案会列出缺失字段名。

### 4.1 实施后发现并修复的缺陷（3.9）：扫码结果在提交时被抹掉

现场症状：租户管理员在「消息渠道」页扫码接入飞书，页面显示扫码成功，但列表始终为空（「暂还没有为本租户配置任何渠道」）。服务端日志证明扫码本身成功、飞书应用确实被创建，而 `tenant_channel_instances`、`credentials`、`credential_versions` 三张表全空，且历史上**没有任何** `channel.instance.*` 审计记录。

```
$ rg -n "FeishuRegister" nohup.out | tail -2
[INFO][2026-09-10 20:12:29][web_channel.py:7458] - [FeishuRegister] QR ready, expire_in=3600s
[INFO][2026-09-10 20:12:55][web_channel.py:7480] - [FeishuRegister] App created: app_id=cli_aa2822a03a78dd23

$ .venv/bin/python -c "import sqlite3;c=sqlite3.connect('identity.db');
print(c.execute('select count(*) from tenant_channel_instances').fetchone()[0],
      c.execute(\"select count(*) from audit_events where action like 'channel%'\").fetchone()[0])"
0 0
```

缺陷链路：密钥输入框按「编辑不得回显已存密钥」的策略**恒渲染为空**（`value=""`），而扫码得到的明文只存在于前端草稿 `tenantChannelDraft.credentials` 中；`submitTenantChannel` 却以输入框取值**替换**草稿：

```
draft.credentials = collectTenantChannelFields();   // 修复前：替换 → 空框把扫码密钥抹掉
```

`collectTenantChannelFields` 跳过空值，于是 `feishu_app_secret` 从草稿中消失；紧接着创建前的必填校验判定其缺失，**请求根本不发出**，界面只留一行错误。由于注册会话的密钥「只回传一次」，这次扫码得到的密钥就此永久丢失。

RED（修复前，先由契约测试固化）：

```
$ node --test tests/test_tenant_channel_card_frontend.cjs
✖ the write was refused before it was ever sent
  + [ { detail: 'App Secret', key: 'tenant_channel_error_required' } ]
  - []
1 failed, 10 passed
```

修复：改为在草稿之上**合并**，并在密钥框下显示「已通过扫码获取，留空即使用该值」（提示不写入明文，密钥仍不进 DOM）。同时用第二条测试钉住既有承诺——编辑时不重填密钥，请求体里不得出现该键，以免清空已存密钥。

```
draft.credentials = Object.assign({}, draft.credentials, collectTenantChannelFields());

$ node --test tests/test_tenant_channel_card_frontend.cjs
ℹ tests 11  ℹ pass 11  ℹ fail 0

$ .venv/bin/python -m pytest tests/test_tenant_channel_required_credentials.py \
    tests/test_tenant_channel_http.py tests/test_tenant_channel_hot_restart.py \
    tests/test_tenant_channel_instances_service.py tests/test_tenant_channel_mutations.py \
    tests/test_feishu_register_session.py -q
103 passed, 3 subtests passed
```

### 4.2 修复后仍未落库的第二重原因（3.10–3.12）：名称为空被拒，却只显示一句笼统错误

3.9 修复上线后，现场复现仍在：同一租户管理员再次扫码（服务端日志显示 `App created: app_id=cli_aa281cc7e0389cc6`），列表依旧为空。逐层排除后确认**扫码链路本身已是正确的**——服务端 `_poll_payload` 按 `app_id`/`app_secret` 一次性回传，SDK 返回的正是 `{"client_id", "client_secret"}`，用真实代码做的单元探针证明密钥能完整走到轮询应答：

```
$ .venv/bin/python -c "from channel.web.web_channel import FeishuRegisterHandler as H;
H._reset_sessions(); h=H._create_session('u1','t1');
H._set_status(h,'done',app_id='cli_probe',app_secret='secret_probe');
print(H._poll_payload(h,'u1','t1'))"
{'status': 'success', 'register_status': 'done', 'app_id': 'cli_probe', 'app_secret': 'secret_probe'}
```

于是把「扫码 → 保存」整条链路（3.11）钉成一条集成测试：走真实的 `applyFeishuScanToTenantForm`（扫码写入草稿）→ 回读 App ID 输入框 → 密钥框保持为空 → 提交，断言请求体同时携带 `feishu_app_id`、一次性 `feishu_app_secret` 与名称。该测试**通过**，证明 3.9 的合并修复有效，链路不再是缺陷所在。

真正的第二重原因在创建前的**必填校验缺口**：`create_tenant_channel_instance` 要求名称非空，否则 400 `bad_request`：

```
display_name = (display_name or "").strip()
if not display_name:
    raise IdentityServiceError("display name is required", code="bad_request", status=400)
```

而前端既不标必填也不本地校验，并把该码统一渲染为笼统的 `tenant_channel_error_invalid`（「提交内容不合法，请检查后重试」）。操作员扫码成功、点保存，得到的只是一句无从下手的错误，看上去就是「什么都没发生」。RED：

```
$ node --test tests/test_tenant_channel_card_frontend.cjs
✖ a create without a display name says so instead of failing generically
  AssertionError: a nameless instance was sent to the server   1 !== 0
✖ the display name is marked required on a create form
  AssertionError: nothing tells the operator the name is mandatory
2 failed, 12 passed
```

修复：创建时名称标为必填（`data-tenant-channel-display-required="1"` + 标签红星号），并在提交前本地校验，命中即给出 `tenant_channel_error_display_required` 的三语提示。

同时补上（3.12）被拒写入的服务端留痕。被拒的写入既不落库也不入审计，此前「服务端拒绝」与「请求根本没发出」在日志上**无法区分**，这正是本轮定位反复的根源；`_reject_channel_write` 以 WARNING 记录 action/tenant/user/原因/状态码：

```
$ node --test tests/test_tenant_channel_card_frontend.cjs
ℹ tests 14  ℹ pass 14  ℹ fail 0
```

结论与操作提示：代码链路自 3.9 起已是正确的，现场失败的直接原因是**浏览器仍执行着修复前加载的 `console.js`**（该页签在修复前就已打开）。静态资源无 `Cache-Control`/`ETag`/`Last-Modified`，服务端逐请求从磁盘读取，**刷新即可拿到新脚本**。

### 4.3 第三重原因（3.12–3.15）：写入确实发出了，被二次验证以「空密码」挡下

3.10 修复上线、硬刷新后仍然没有记录。3.12 新增的服务端留痕**立刻**给出了答案：

```
$ rg -n "TenantChannels" nohup.out | tail -1
[WARNING][2026-09-10 20:35:38][admin_handlers.py:838] - [TenantChannels] rejected create for
    tenant=tnt_EA3qM-lHPLD8ZPwW user=usr_9ZxVPz7M2FuOro1q:
    recent password required (401/invalid_old)
```

注意是 `recent password required`（密码**为空**）而非 `invalid old password`（密码错误）。前端收集密码的方式是浏览器原生模态框：

```
const recent = window.prompt(t('tenant_password_prompt') || '');   // 修复前
```

`window.prompt` 一旦被取消、或被 Chrome 的「阻止此页面创建更多对话框」抑制，就**静默返回 null**，于是 `recent_password` 为空 → 401。这同时解释了「中间要点两次保存」：每次点击都弹（或弹不出来）一次密码框，看不到有效反馈，只能再点一次，结果相同。

修复（3.13–3.15）：

1. **扫码成功即自动落库**。服务端 `_poll_payload` 在 `done` 时签发一次性授权凭据，前端据此自动创建，不再需要第二次点击：

```
$ .venv/bin/python -c "from channel.web.web_channel import FeishuRegisterHandler as H;
H._reset_sessions(); h=H._create_session('user-a','tenant-1');
H._set_status(h,'done',app_id='cli_abc',app_secret='s3cr3t');
p=H._poll_payload(h,'user-a','tenant-1');
from auth import scan_authorization as sa;
print(bool(p['scan_ticket']),
      sa.verify(p['scan_ticket'],actor_user_id='user-a',tenant_id='tenant-1',channel_type='feishu'),
      sa.verify(p['scan_ticket'],actor_user_id='user-b',tenant_id='tenant-1',channel_type='feishu'))"
True True False
```

凭据边界由 `auth/scan_authorization.py` 的 10 条单测钉住：一次性、短时效、不跨发起者/作用域/渠道类型，且**被拒的出示不消耗**凭据（错误作用域的探测不得夺走真正持有者的授权），校验（`verify`）与消费（`consume`）分离，消费发生在事务提交之后——写入因名称冲突被拒时凭据仍可用：

```
$ .venv/bin/python -m pytest tests/test_scan_authorization.py -q
10 passed

$ .venv/bin/python -m pytest tests/test_tenant_channel_http.py -q   # 含 ScanGrantCreateTests 7 条
38 passed

$ .venv/bin/python -m pytest tests/test_feishu_register_session.py -q
14 passed
```

2. **渠道名称自动派生**。扫码结果里没有可读名字（SDK 只回 `open_id` 与 `tenant_brand`），故由渠道类型标签 + 应用标识末四位生成（`飞书 · 5cda`），不再要求操作者输入，也不会因名称为空被 400 拒绝。

3. **密码收集改用页面内真实控件**。`askRecentPassword` + `recentPasswordDialogHtml` 取代 `submitTenantChannel` 与 `toggleTenantChannel` 中的 `window.prompt`：取消与空密码现在可区分，空密码在本地即被拦下并给出 `tenant_channel_error_password_required`，被拒（401）的凭据会被丢弃，使重试退化为「用密码」而不是「永远同样失败」。手写接入与启停路径同样受此修复覆盖。

前端契约（`tests/test_tenant_channel_card_frontend.cjs`，23 条）新增 9 条覆盖自动落库整链：凭据随写入送达且不弹密码、名称由 app id 派生、无凭据时不自动落库、手写保存走页内对话框、取消不发请求、空密码本地拦截、被拒凭据被丢弃、写入路径不含 `window.prompt`、密码对话框字段名可被代码读取。测试桩把 `window.prompt` 设为**抛异常**，因此任何回退到原生弹窗都会立刻失败而不是表现为莫名的 401。

```
$ node --test tests/test_tenant_channel_card_frontend.cjs
ℹ tests 23  ℹ pass 23  ℹ fail 0
```

规范同步：原「扫码结果进入接入表单而非直接落库」改写为「扫码成功即完成接入，凭据不得旁路留存」，新增「一次性扫码授权凭据可替代近期密码且仅限创建」，并在「扫码接入不绕过既有渠道写入契约」中把「必须近期密码」改写为「近期密码或该次扫码凭据之一」，另加一条「密码收集不得依赖可被抑制的原生弹窗」的 scenario。

### 4.4 第四重原因（3.16–3.17）：授权已通过，卡在「部署未配置凭据加密主密钥」

3.13–3.15 上线、硬刷新后扫码，界面回了一句「提交内容不合法，请检查后重试」。3.12 的留痕再次直接给出答案——这次失败点已经推进到了**授权通过之后**：

```
[ERROR][2026-09-10 21:01:40][service.py:4168] - [Identity] channel credential encrypt unavailable:
    COW_CREDENTIAL_MASTER_KEY is not configured; refusing to store credentials with a fallback key
[WARNING][2026-09-10 21:01:40][admin_handlers.py:838] - [TenantChannels] rejected create for
    tenant=tnt_EA3qM-lHPLD8ZPwW user=usr_9ZxVPz7M2FuOro1q:
    credential encryption unavailable (500/credential_crypto)
```

`auth/crypto.py` 的设计是刻意的「无主密钥即拒绝，绝不退回众所周知的兜底密钥」，而本机服务由 launchd 托管，其 `EnvironmentVariables` 只设了 `PYTHONUNBUFFERED`——这个部署从未配过主密钥。租户渠道凭据是**第一个需要它的功能**（此前 `credentials` 表 0 行），所以这个缺失一直没暴露：前几次失败都发生在更早的关卡（凭据丢失、名称空、密码空），只有到本次才走到加密这一步。

两处修复：

1. **部署侧**（3.17）。生成 32 字节随机会话密钥写入 launchd plist（权限 600，原文件已备份到 `~/.cowagent/`），并留一份 600 权限备份于 `~/.cowagent/credential_master_key`——主密钥丢失会使已存凭据永久不可解密。`launchctl bootout` + `bootstrap` 重新加载后确认服务进程环境已带上该变量：

```
$ launchctl print gui/$(id -u)/com.cowagent.app | rg "COW_CREDENTIAL_MASTER_KEY =>"
COW_CREDENTIAL_MASTER_KEY => fb2c          # 仅显示前 4 位

$ ps eww -p <pid> | tr ' ' '\n' | rg "^COW_CREDENTIAL_MASTER_KEY=" | wc -c
present: True | hex length: 64
```

以该真实密钥跑通「票据 → 创建 → 加密落库 → 掩码列表」整链（临时库，不动现场数据）：

```
roundtrip ok : True | ciphertext prefix: v1.
created      : 飞书 · 5cda | version: 1
grant spent  : True
stored bundle: ['feishu_app_id', 'feishu_app_secret']
secret intact: True
masked list  : {'channel_type': 'feishu', 'display_name': '飞书 · 5cda', 'version': 1, ...}
```

2. **文案侧**（3.16）。500 `credential_crypto` 此前落到 `tenantChannelWriteErrorKey` 的兜底分支，被渲染为「提交内容不合法，请检查后重试」；传输失败同样如此。前者是**重试永远不会成功**的部署故障，却被表述成操作者填错了值。现在 `credential_crypto` 有独立文案，网络失败也有独立文案，二者都不再冒充输入错误。前端契约 25 条（新增一条钉住该映射）。

## 5. 入站锚点与 Agent 缺省（P2）关键实现：`channel/channel_instances.py` 的 `ChannelInstance.tenant_id` 是**唯一权威锚点**，来自实例登记行（启动合成时带入）；`channel/channel.py` 的 `stamp_instance_context` 只把 `instance_tenant_id` 打戳供观测，**不参与授权**；`channel/external_identity.py` 的 `instance_tenant_id(context)` 按 `instance_id` 回查登记行（查不到即返回空，退回旧的 Agent 绑定语义，平台名单实例不受影响），`resolve_actor_for_context(context, agent_id, instance_tenant_id="")` 在锚点存在时以实例归属为准；`channel/chat_channel.py` 的 `_preflight_external_inbound` 把路由**钉死在该租户自己的 Agent**（实例绑定，或该租户的 `resolved_default_agent_id`），租户无可用 Agent 时拒绝，路由若回落到全局默认则拒绝而不是代答；`channel_factory` 与 `app.py` 把 `tenant_id` 带进实例。

```
$ .venv/bin/python -m pytest tests/test_tenant_channel_inbound_anchor.py -q
..........                                                               [100%]
10 passed in 16.27s

$ .venv/bin/python -m pytest tests/test_tenant_channel_inbound_isolation.py -q
.................                                                        [100%]
17 passed in 11.48s

$ .venv/bin/python -m pytest tests/test_external_im_gate.py -q
................                                                         [100%]
16 passed in 4.88s

$ .venv/bin/python -m pytest tests/test_tenant_channel_inbound_closure.py -q
....                                                                     [100%]
4 passed in 2.73s
```

覆盖（6.2/6.3 锚点）：全局路由把某租户实例的消息路由到**另一租户**的 Agent 时，授权仍按实例归属判定（同一发送者从「非成员拒绝」变为「在本人租户内被授权」）；context 里被伪造成他租的 `instance_tenant_id` 不被采信，租户取自登记行；未绑定 Agent 的实例同样在本租户内作答。

覆盖（6.5/6.6 缺省与不回退）：未绑定 Agent 的实例解析到该租户的默认 Agent，且**不等于**进程全局默认 Agent；租户没有任何 Agent 时拒绝并回固定提示，`runtime_identity` 不落任何身份，模型边界未被触碰。

覆盖（6.7 既有闸门）、（6.9 会话按发送者区分）：他租户成员、未绑定发送者、在该 Agent 上无 `agent.use` 授权的成员仍被拒且不调用模型；同一实例下两个发送者各自解析到自己的成员身份，互不串扰。

覆盖（6.10 端到端闭环）：未绑定 Agent 的实例如常服务——启动合成带入 `tenant_id`（`agent_id` 为空），入站后在本租户内以该租户默认 Agent 执行，路由记录显示用的是本租户 Agent 而非进程全局默认 Agent（模型边界以记录器替代）。

## 6. 全量回归与基线对照（7.1）

基线 = 仓库 `HEAD` 的独立 worktree（`git worktree add /tmp/cow-baseline HEAD`），并把 `config.json`、`identity.db`、`tenants/` 复制进去使环境一致；两边用同一解释器与同一命令。

```
# 本次改动（工作区）
$ .venv/bin/python -m pytest tests/ -q
32 failed, 2380 passed, 3 skipped, 24 subtests passed in 290.28s

# 基线（HEAD + 同环境，无本次改动）
$ .venv/bin/python -m pytest tests/ -q
34 failed, 1928 passed, 3 skipped, 18 subtests passed in 119.16s
```

- 本次的 32 项失败**全部**是基线的子集：基线的 34 项失败 = 本次的 32 项 + `test_subagent.py::test_the_repo_ships_a_guide_that_documents_the_real_format` + `test_tenant_default_agent.py::TenantDefaultAgentProjectionTests::test_two_tenants_each_mark_their_own_default`（后两项在本次工作区为通过）。即：**本次改动没有引入任何新的失败**。
- 这 32 项自身分为两类：其一是与本次无关的既有失败，单文件可复现（SSRF 12 项、PDF 读取窗口 3 项、dashscope 1 项、claude thinking 1 项、身份自助投影 2 项、会话历史检索 5 项）；其二是仅在整套运行时出现的顺序相关失败，单文件运行全绿（`test_todo_service` 8、`test_consumer_closure_acceptance` 7、`test_tenant_create_containment` 6、`test_feishu_progress_card` 5），且在基线整套运行时同样失败。
- 受本 change 影响的定向套件全绿（见第 2–5 节；另有既有 313 项 `-k "channel or instance or isolation or routing or external_identity or inbound"` 选择集全绿）。

**外部并发改动提示（非本 change）**：本次全量运行期间，另一在途 change `rbac-controlled-execution-permission` 正在改写 `channel/web/static/js/console.js`（其 3.2 明确要求移除 `_renderPermissionChip`，文件 mtime 20:00 晚于上述运行）。该改动会令 `tests/test_agent_workbench.py` 失败，与本次渠道接入工作无关，本 change 不予处理、也不得代为修改。

## 7. 启用门槛状态

**未满足。** `open-database-runtime` 与 `tenant-owned-message-channels` 均未归档，故交付表述为
「已实现、已验证，启用待归档」。本 change 亦不得在两者归档前归档（见 1.4）。

归档顺序与 delta 一致性已在本轮复核：

```
$ ls openspec/specs/ | grep -i tenant-channel
（无输出：主规范库尚无该文件，故 tenant-channel-configuration 的 MODIFIED/RENAMED 只能在基线归档后才成立）

$ # 基线 delta 的标题（RENAMED FROM 必须逐字匹配）
$ rg -n "^### Requirement: 凭据轮换" openspec/changes/tenant-owned-message-channels/specs/tenant-channel-configuration/spec.md
75:### Requirement: 凭据轮换与撤权使旧版本不可用并在重启后生效

$ # 逐条比对 MODIFIED 块的 scenario 是否覆盖基线全部 scenario
租户渠道实例有明确归属与版本: baseline 3 scenarios, ours 4, missing=[]
渠道实例绑定的 Agent 必须属于同一租户: baseline 2 scenarios, ours 4, missing=[]
入站消息只在渠道实例所属租户内执行: baseline 2 scenarios, ours 3, missing=[]
凭据轮换与撤权…（RENAMED，按 FROM/TO 比对）: baseline 3 scenarios, ours 5, missing=[]
渠道页在消费者未开放时不进入持续加载状态: baseline 1 scenarios, ours 2, missing=[]
```

约束复核（7.6）：`team.json` 未被改动（`git status --porcelain team.json` 无输出）；工作区里出现的 `CREATE TABLE platform_roles` / `user_platform_roles` / `tenant_channel_instances` 与 `agent_bindings` 加列分别属于平台角色、`tenant-owned-message-channels`、Agent 克隆等在途 change；`auth/service.py` 的改动未触碰内置角色默认权限集（diff 中无相关行）。第 8 组新增了一张表 `external_identity_attempts`（见 §7），是对前置「不新增表」约束的**有意例外**，已在 `proposal.md` 的「数据」段声明。

## 8. 外部身份绑定的双角色维护（第 8 组）

### 8.1 服务层：租户侧绑定与跨租户拒绝

```
$ .venv/bin/python -m pytest tests/test_external_identity_binding_admin.py -q
20 passed
```

覆盖并固定的行为：租户管理员绑定/列出/解绑本租户成员；**跨租户成员标识按 `not_found` 拒绝**（不是 `forbidden`，避免成为跨租户成员标识的探测器）；传他租户的 `tenant_id` 参数不能获得访问（`_is_tenant_admin` 对该租户为假）；解绑时从绑定行反查归属账号并复校租户，猜中的绑定标识也拿不走他租户的绑定；普通成员被拒；平台路径未被放宽；待绑定尝试的证据字段（见 §8.6）。

**测试暴露的一个真实事实**（已写入测试注释与断言）：`bootstrap` 创建的管理员**同时**持有平台角色与本租户管理员角色，而 `create_tenant` 创建的租户管理员是**纯**租户管理员。因此「租户管理员不得使用平台接口」这类断言只能用后者证明——第一版用例用 bootstrap 账号断言 `forbidden`，结果为「未抛出」，正是因为它本身就是平台管理员。修正后用例显式断言夹具中确实存在一个无平台角色的租户管理员。

### 8.2 入站拒绝登记待绑定尝试

```
$ .venv/bin/python -m pytest tests/test_external_im_gate.py -q
27 passed
```

新增 3 条：未绑定入站被登记且携带投递实例（provider/issuer/channel_type/instance_id）；已授权入站**不**登记；非绑定类拒绝（已知账号但不属于该组织 → `NOT_MEMBER`）**不**登记——重新绑定不能解决它，列出只会把管理员引向死路。可识别性相关的新增 5 条见 §8.6。

### 8.3 HTTP：双作用域路由与策略

```
$ .venv/bin/python -m pytest tests/test_external_identity_http.py -q
13 passed in 7.13s

$ .venv/bin/python -m pytest tests/test_http_policy.py -q
34 passed in 1.36s
```

HTTP 用例覆盖：租户管理员绑定/列出/解绑本租户成员（列表行返回 `username`，界面据此回答「是谁」）；跨租户 404 且绑定保持原状；普通成员 403；缺租户选择 400；三元组校验 400 与冲突 409；平台管理员仍可维护任意账号；纯租户管理员在平台路由 403；待绑定列表按投递实例的租户过滤（平台管理员另见无所属租户的尝试）；绑定成功后该尝试从列表消失。

策略用例中特别固定了一条**回归护栏**：新增两条嵌套子路由之后，`/api/tenant/members/{id}` 的更新策略必须仍在。该路径由**更短**的模式匹配，新增嵌套路由正是可能遮蔽或覆盖它的编辑——本轮实现过程中确实一度覆盖掉它（编辑时替换掉了该条目），护栏用例存在的意义即在此。

### 8.4 前端：一个弹窗、两处入口

```
$ node --test tests/test_external_identity_frontend.cjs
ℹ tests 26  ℹ pass 26  ℹ fail 0

$ node --test tests/test_i18n_external_identity_keys.cjs
ℹ tests 5  ℹ pass 5  ℹ fail 0
```

前端契约覆盖：路由族由**入口与身份共同**决定（成员行 → 租户路由；平台账号行 → 平台路由；**平台管理员看成员行 → 平台路由**），并断言成员入口在租户管理员身份下不会触达任何 `/api/platform/` 路由（那会跳过租户隔离）；渲染同时含渠道、应用与 open_id；弹窗标题为「账号名 · 用户名」；待绑定点选只填表不发请求；空 open_id 在本地被拦下；解绑复用与列表相同的路由族；冲突给出专属文案而非「参数不合法」；账号无 `user_id` 时不开空弹窗。

**一处需求覆盖缺口（复核时发现并修掉）**：最初把路由族直接绑定在入口上（成员行 ⇒ 租户路由）。但平台管理员可能查看某租户的成员列表而不持有**该租户**的管理员角色——租户路由会以 403 拒绝他，于是「平台管理员也能维护」在成员列表这一入口上不成立。改为按能力判定：`externalIdentitySurface(kind, isPlatformAdmin)` 为纯函数（平台账号行恒为 platform；成员行按调用者是否为平台管理员分流），平台管理员看成员行时走平台路由（平台路由可达任意账号），租户管理员仍走租户路由（服务端据此施加租户隔离）。

实现中由测试发现并修掉的三个问题：

1. **账号名为空时标签重复**：`{name:'', username:'acmemember'}` 渲染为 `acmemember · acmemember`（先取 username 兜底，又追加了一次 username）。改为仅在用户名确实补充信息时追加。
2. **弹窗无法从外部注入账号**：`openExternalIdentities(kind, id)` 依赖闭包中的 `_memberById`/`_platformUsers`，测试无法装配。拆为「查找账号」与 `extidOpen(kind, account, opts)` 两段——这既是可测的接缝，也让弹窗不再关心调用方如何找到该行。
3. **成员标识被当成账号标识发往平台路由**（引入能力分流后由测试立即捕获）：成员行的 `account.id` 是 membership id，而平台路由需要 user id。修正为「标识取自**行来自哪个列表**，与可用哪个面无关」：成员行取 `account.user_id` 作为账号、`account.id` 作为成员标识；平台账号行两者同为 `account.id`。若不修，平台管理员从成员列表进入会绑定到错误的地址上。

测试桩的一处改进：模拟 `<select>` 的 `value` 取其 `selected` 选项（浏览器行为），否则「点选待绑定后渠道被正确选中」这一断言在桩上无法成立。

### 8.5 provider 标签的回落

未知渠道代码（例如本构建未收录的类型）回落为**原始代码**而非空白，否则该绑定在界面上无法被识别。契约测试固定了该回落分支（`label === key ? raw : label`）。

### 8.6 待绑定尝试的可识别性（复核发现的可用性缺陷）

**缺陷**：待绑定列表最初只显示「渠道 · 应用 + open_id」。而 `open_id` 不指认任何人——管理员被要求凭它判断「该绑给谁」，等于要求他们背下每个成员的 open_id。这是把系统的内部标识当成了人类的识别依据。

**修正**：为尝试登记「发送者昵称 + 最新消息预览 + 是否群聊」，并在行内以昵称领衔、消息预览居次呈现。

```
$ .venv/bin/python -m pytest tests/test_external_identity_binding_admin.py -q
20 passed

$ .venv/bin/python -m pytest tests/test_external_im_gate.py -q
27 passed

$ .venv/bin/python -m pytest tests/test_feishu_sender_name.py -q
7 passed
```

```
$ node --test tests/test_external_identity_frontend.cjs
ℹ tests 26  ℹ pass 26  ℹ fail 0
```

设计要点与实测固定项：

1. **渠道无关**。证据从各渠道共用的标准 `ChatMessage` 字段读取（`channel/external_identity.attempt_evidence`），因此新增渠道无需专门适配，只有 Feishu 需要补一件事（见第 4 点）。
2. **字段独立降级**。昵称解析失败 MUST NOT 连带丢掉消息预览。初版把三个字段放在同一个 `try` 里，昵称抛错时整条证据退化为空——测试 `test_a_name_lookup_that_fails_leaves_the_attempt_bindable` 抓到了它，改为每字段各自兜底。
3. **非文本消息不留本地路径**。图片/语音消息的 `content` 是下载后的服务器文件路径（Feishu 即为如此）。直接落库会同时造成「路径泄露」与「指认无效」，故非文本消息按类型摘要（`[图片]`/`[语音]`/`[文件]`…）。测试直接断言预览中不含 `/var/folders`。
4. **Feishu 的昵称来源**。`actual_user_nickname` 此前被声明但从未赋值（`channel/feishu/feishu_message.py`），事件的 `sender` 只有 `open_id`，昵称在 contact API 之后。新增 `FeishuMessage.resolve_sender_name()`：惰性（仅在拒绝路径、且仅在标准字段为空时调用）、按作者进程内记忆化、**失败也记忆**（无 contact 权限时不得每条消息都付一次失败往返）、无 token 或不返回 `code==0` 时回落空名。它是 `attempt_evidence` 的可选钩子，因此不具该能力的渠道无需实现。
5. **重复尝试刷新为最新消息，但空昵称不覆盖已知昵称**。`sender_name = CASE WHEN excluded.sender_name != '' THEN excluded.sender_name ELSE sender_name END`：最近说的话是最有效的辨认线索，而昵称可能首次取不到、后续才取到（反之亦然）。
6. **旧行可读**。迁移 10 以 `ALTER TABLE ... DEFAULT` 增列，实测 v9→v10 升级后旧行昵称/预览为空、`is_group` 为 0，仍可绑定；重复打开不重放 DDL（幂等）。

前端行的实际渲染（含注入防护实测）：

```
<span class="truncate">李四</span>
<span class="extid_group_tag ...">群聊</span>
<span class="text-[10px] ...">×3</span>
<div class="text-xs ...">飞书 · cli_aa2817b7ac381cb4</div>
<div class="text-xs ... line-clamp-2">&lt;img onerror=alert(1)&gt; 这条要防注入</div>
<div class="text-[10px] ...">open_id: ou_group</div>
```

无昵称时回落「未识别昵称」而消息预览照常显示；两者皆无时行仍可点选绑定。

**仍未解决（承认）**：待绑定列表仍只能经「成员管理 / 平台账号」的行内「外部身份」按钮进入，**没有顶层菜单项**；且从列表完成绑定需要先知道该开哪个成员的弹窗——昵称预览缓解了「不知道是谁」，但「知道是谁之后如何直达」仍缺一步。顶层菜单页（列表内选择成员直接绑定）已确认要做，尚未实施。

## 9. 已知缺口与显式延后项

- **待绑定列表的顶层入口（已确认要做，未实施）**：目前只能从「成员管理 / 平台账号」的行内「外部身份」按钮进入；列表本身也还不能直接选择成员完成绑定（需先找到该成员的弹窗）。已确定要做成顶层菜单页，在列表内直接选成员绑定。
- **跨文件测试污染（既有，未修）**：`tests/test_feishu_progress_card.py` 在模块导入时调用 `i18n.set_language("en")`，且其 `finally` 恢复的是 `"en"` 而非进入前的语言，因此**任何依赖默认中文的用例若在该文件之后运行都会失败**（本轮验证时以自定义文件顺序跑全套即触发 `test_external_im_gate.py::test_preflight_disabled_agent_sends_disabled` 的中文断言失败，按字母序运行不触发）。与本次改动无关，未修以免扩大改动面，但建议后续用 fixture 保存并恢复原语言。
- 个人（用户自有）渠道层：`scope`/`owner_user_id` 列、`workbench.channels` 页面、owner-only 入站规则。
- 微信扫码接入：登录态为进程级单实例，保持平台专属。
- 租户渠道的团队 `members`：`tenant_channel_instances` 无该列，需加列与迁移。
- 钉钉/微信的最小必填凭据集：待核实（`tenant-owned-message-channels/evidence.md` §10.2）。
- `wechatcom_app`（企微自建应用）多实例化：本轮不开放。
- 群会话的默认共享会话（`group_shared_session=true`）下，同一群内不同发送者共享对话历史，但每条消息各自按发送者解析身份并单独过闸（6.9 覆盖的是身份与授权隔离）。是否按发送者拆分群会话属于既有产品行为，不在本 change 范围。
- 轮换合并语义（5.7）的规范表述：当前以「最小必填集 + 留空保持原值」的控制台承诺为准，`tenant-channel-configuration` 的 ADDED 要求已覆盖「轮换缺必填键须拒绝」，但「留空即保持」未单独成为一条 scenario；归档前可考虑补一条，本轮以既有要求与前端承诺一致实现。
- 登录时的租户选择仍使用 `window.prompt`（`console.js` 的 `account_tenant_title` 处）。它不是凭据写入路径，被浏览器抑制时只会导致选择为空而非误判密码，故未纳入本轮修复；同类脆弱性建议后续一并换成页面内控件。
- 凭据加密主密钥的部署要求（`COW_CREDENTIAL_MASTER_KEY`，无则拒绝写入）在 `docs/` 中**未被记载**，只在 `auth/crypto.py` 的模块 docstring 里说明。本轮仅在 `evidence.md` 记录，建议后续补进部署文档：漏配的后果是所有凭据类写入静默不可用，而非启动即失败。
