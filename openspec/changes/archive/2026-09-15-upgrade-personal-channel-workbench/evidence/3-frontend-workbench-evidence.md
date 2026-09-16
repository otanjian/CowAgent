# 阶段 3：个人渠道工作台与共用展示

本文件记录 `tasks.md` 3.1 / 3.2 / 3.3 / 3.4 / 3.5 / 3.6 的实现与可复跑证据。
结论均来自当前工作区代码实读与命令输出，不以「测试通过」代替对需求条目的逐条核对。

## 3.1 共用展示模块与装载顺序

新增 `channel/web/static/js/channel-workbench.js`，把两份渠道界面共用的展示收在一处。三条自我约束写在文件头并在实现中成立：

| 约束 | 实现 | 核对方式 |
|---|---|---|
| 无环境态 | 无一处读 `channelScope()`、`tenantChannelDraft`、`agentCatalog`、`tenantChannelTypes`、`tenantChannelInstances`；所有输入为形参 | 通读模块；`cardShell`/`fieldInput`/`fieldsHtml` 全部取 `opts` |
| 无固定 DOM id | `data-<prefix>-field` / `-required` / `-held` / `-mode`，`prefix` 由调用方给（`tenant-channel` / `personal-channel`） | `fieldAttr`/`requiredAttr`/`heldAttr` 三处成对定义，收集与渲染同源 |
| 无请求、无草稿 | 模块不 fetch、不持久化、不判定动词许可 | 动词仍只来自服务端 `actions` 投影 |

文案不硬编码进模块：`fieldInput` 的「已取得、保存时生效」提示与 `manualPane` 的密钥说明改为
`opts.heldHintKey` / `opts.secretNoteKey`，默认值仍是租户页原有键，因此租户渲染逐字节不变，
个人页换用自己的键即可，不会出现「个人页显示公共页文案」。

装载顺序（`channel/web/chat.html`，均为 `defer`，按文档序执行）：

```
2800: fragments.js
2805: channel-workbench.js      <- 本次新增
2806: console.js
2810: personal-console.js
```

`console.js` 已改为消费该模块：`channelTypeLabel` / `channelFieldLabel` / `tenantChannelAppearance` /
`tenantChannelSupportsScan` / `tenantChannelScanCopy` / `buildTenantChannelForm` 的模式页签、
扫码面板与字段区都转调 `window.ChannelWorkbench.*`，且把 `escapeHtml`、`t`、`currentLang` 显式注入，
内联 `switchTenantChannelMode(...)` 的调用形态保持不变。

## 3.2 专用控制器

`personal-channels` 视图在 `PERSONAL_VIEWS` 上标记 `controller: 'channels'`，从通用行列表切出专用控制器：
标题、当前租户范围、搜索、卡片、接入入口与可新增的空状态齐全。页面壳只在首次渲染，搜索框元素因此
不在每次刷新时被替换，输入与监听保持；`_makeLoader` 对 `controller` 视图改绑 `_bindChannelShell`，
通用 `_bindShell` 的搜索处理不再与专用搜索抢同一元素。

## 3.3 新增/编辑表单

- 目标必选：`personalChannelTargetFieldFrom` 生成的选项里没有空值项；创建时服务端 `agent_options`
  已收敛到「本人可用的私有智能体」，停用项从选择器剔除（仍以徽标形式在卡片上说明）。
- 无可用目标：`personal_channels_no_target` + `personal_channels_no_target_hint` + 跳转「我的智能体」。
- 凭据按后端声明渲染：走 `ChannelWorkbench.credentialDescriptors`，与租户页同一份 `credential_fields` 派生。
- 空密钥保留：编辑时 `keep: true`，`required` 全部为假并附 `personal_channels_credential_keep` 说明。
- 错误保留草稿：保存失败不清 `_channels[viewId].draft`，只有关闭表单或切租户才 `clearChannelDraft`。
- 保存与维护只调个人接口（`CHANNEL_PREFIX`），不触碰公共渠道接口。

## 3.4 动作、状态与身份关联

- 动词由服务端 `actions` 决定，前端不自行推断；`repair_target` 作为 `update` 并显式带 `agent_id`。
- 四个事实四种状态：保存（`status_saved`）、连接（`status_connected`）、身份关联、可对话
  （`talkable` / `not_talkable`）分开渲染；治理停用单独读作「已被管理员停用」，不写成「本人关闭」。
- 目标失效/停用/缺失分别给出 `target_invalid` / `target_disabled` / `target_missing`，并给出修复入口；
  修复选择器不会把「正在修复的那个目标」列进候选。
- 身份关联：展示一次性 `challenge` 与有效期（`_channelExpiryText`），轮询刷新状态，解除关联的文案
  承诺「保留实例目标」，不写成清空智能体。

## 3.5 代次与租户隔离

`_channels[viewId]` 按视图隔离；`channelEpoch(viewId)` + `_channelTenant` 记录「本次渲染属于哪个租户、第几代」，
页面读取、表单、扫码、关联轮询统一校验代次，迟到响应与旧回调不落地。切租户 / 退出 / 关表单 / 权限失效
时 `clearChannelDraft` 丢弃秘密并 `stopChannelBindingPoll` 停止消费者。

## 3.6 文案、深浅色与回归

三语 116 键，无缺、无空值、无跨命名空间重叠：

```
node --test tests/test_console_i18n_parity.cjs
→ 5 passed（含「合并表与拆分前快照深度相等」）
```

`tests/fixtures/console_i18n_snapshot.json` 同步登记本次新增的 37 个 `personal_channels_*` 键
（zh / zh-Hant / en 各 37）。该快照是**全键全语严格等同**的黄金表（`deepStrictEqual(merged, fixture)`），
新增控制台文案时必须同步，否则 parity 测试失败。同步后逐项核对：

```
zh      : head=1279  now=1316  added=37  dropped=0  changed=0  同语内重复=0
zh-Hant : head=1272  now=1309  added=37  dropped=0  changed=0  同语内重复=0
en      : head=1279  now=1316  added=37  dropped=0  changed=0  同语内重复=0

新增键非 personal_channels_ 前缀的: 无
既有键被改值的: 无
既有键被删除的: 无
```

zh-Hant 相对 zh/en 少 7 键的既有差异被原样保留（1279−1272 = 1316−1309 = 7），未借本次同步补齐——
该差异是快照既有事实，parity 测试按语言各自对齐快照，不由本 change 背书修改。

注意 diff 形态：新键按 ASCII 序插入原有 `personal_channels_*` 连续段，因此除 135 行新增外，
还有 24 行「删除+重新加入」——即该段内 8 个既有键的位置移动，非内容变更（上表 `changed=0`/`dropped=0` 已证）。
为免混淆，此处不声称 diff 只有新增行。

## 可复跑证据

```
node --test tests/test_personal_console_frontend.cjs
→ tests 73, pass 73, fail 0

node --test tests/test_tenant_channel_card_frontend.cjs
→ tests 28, pass 28, fail 0

node --test tests/test_console_i18n_parity.cjs
→ tests 5, pass 5, fail 0
```

相邻前端回归（公共渠道、渠道页头、导航、视图注册、扫码注册、会话/账号菜单）：

```
tests/test_i18n_tenant_channel_keys.cjs          pass=4  fail=0
tests/test_personal_console_browser.cjs          pass=1  fail=0
tests/test_channels_page_header_frontend.cjs     pass=6  fail=0
tests/test_channel_scope_nav_frontend.cjs        pass=8  fail=0
tests/test_console_view_registry.cjs             pass=6  fail=0
tests/test_feishu_register_frontend.cjs          pass=4  fail=0
tests/test_401_recent_password_no_logout.cjs     pass=6  fail=0
tests/test_fork_fragments.cjs                    pass=6  fail=0
tests/test_nav_area_frontend.cjs                 pass=5  fail=0
```

全套前端契约与基线逐文件对齐，本 change 未新增失败（`git worktree` 取 `HEAD` 为基线，同一命令对比）：

```
node --test tests/*.cjs
→ tests 621, pass 578, fail 43

逐文件失败分布（工作区 与 HEAD 基线 完全一致）：
  36  tests/test_session_history_frontend.cjs
   5  tests/test_sidebar_account_frontend.cjs
   1  tests/test_appearance_browser.cjs
   1  tests/_tmp_repro_modeldefaults.cjs

基线命令：
  git worktree add -f /tmp/wb-baseline HEAD && (cd /tmp/wb-baseline && node --test tests/*.cjs)
→ tests 621, pass 578, fail 43（分布同上）
```

即：43 个失败是既有基线，与本 change 无关；渠道相关前端全绿。

## 未在本阶段覆盖

3.1～3.6 不包含真实扫码与真实运行：飞书/企微个人扫码见阶段 4，浏览器与真实运行验收见阶段 5/6。
本阶段只声明「展示模块已共用、个人工作台可用、契约测试通过」。

## 记录一处未处理的既有文案问题（不属本阶段改动）

`personal_channels_active`（"已连接"/"已連線"/"Connected"）与 `personal_channels_inactive`
（"未启用"/"Not enabled"）语义误导：它们描述的是**启用开关**，字面却写成连接状态，与本次新增的
`personal_channels_status_connected`（真正的连接）并列时会被误读。

核对结论：这两键在 `HEAD` 上**已经零引用**（`git show HEAD:channel/web/static/js/personal-console.js`
中无引用，仅存于快照），即早先变更遗留的死文案，并非本 change 孤立。

处理决定：**保留不改**。理由——删除属未纳入本 change 范围的清理，且会改动黄金快照中既有键
（违反该测试「不得增/删/改任一既有翻译」的初衷）；本 change 新增的四种状态键已让工作台显示准确，
误导风险由「不再使用」消除。后续若要做文案清理，应单开 change 连同快照一并处理。

`personal_channels_governance_disabled`（"已被管理员停用"）**保留且仍在使用**：由通用行渲染器的
`personalRowBadges`（`personal-console.js:192`）承载，是渠道页回退到通用列表时的治理徽标，
并被 `test_personal_console_frontend.cjs:373` 固定；它与新键 `personal_channels_status_governance`
在各自渲染路径上服务不同场景，非重复。
