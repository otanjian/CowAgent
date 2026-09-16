# 3.6 真实浏览器双角色验收

对 `http://127.0.0.1:9899` 做**只读**验收：只导航、只读取、截图留证，不改数据、不删对象、不改设置。
两个账号都取自真实身份库，非桩件：

| 角色 | 账号 | 密码 | 租户 |
| --- | --- | --- | --- |
| 普通成员 | `RC001`（Rock） | `test123456` | `test15` / AI启航团队（`tnt_EA3qM-lHPLD8ZPwW`） |
| 租户管理员 | `test15`（test15管理员） | `test123456` | 同上 |

登录都在首次尝试即成功，无验证码、无强制改密提示。

## 1. 成员侧（RC001）

- **控制台入口存在**：侧边栏 `控制台` → `/admin`。账号菜单为 `Rock · 账号设置`。
- **五项个人入口消失**：侧边栏可见项为 `新建对话 / 对话 / 智能体 / 我的待办 / 定时任务 / 知识库 / 场景应用 / 会话历史 / 控制台`，**没有**「我的资源」标题与五个个人入口（任务 3.3 的现场确认）。
- **组织与权限不可见**：`成员管理`、`角色权限`、`组织架构`、`平台账号` 均不在可见侧边栏中。
  这三项所在的 `组织与权限` 分组**仍存在于 DOM 但被 CSS 隐藏**（`class="... hidden"`，渲染高度 0）。这正是任务 3.1 收口要的形态：分组收口靠资格判定，不靠删 DOM；`平台运维` 分组同理。
- **控制台可用**：`智能体开发` → `智能体管理 / 工具与技能 / 记忆管理`；`模型与接入` → `消息渠道`；独立项 `工作台`。各页均可打开。
- **智能体管理**：只列出本人私有 Agent `智能办公助理`（`my-assistant-admin-test15-RC001`），描述为「Rock的专属办公助理……不进租户共享目录」——成员永远看不到他人私有对象。
- **消息渠道**：页描述为 `管理你本人的渠道连接；凭据加密存储且不会回显`（个人面文案），列表为空。
  `接入通道` → 类型选择器 8 项（钉钉 / Discord / 飞书 / QQ 机器人 / Slack / Telegram / 企微智能机器人 / 微信），目标选择器**只有 1 项**：`智能办公助理`。
  这与服务端实测一致（见 §3），即「目标候选按对象范围下发」（任务 6.2）在真实 UI 上成立。
- **记忆管理**：目标下拉**恰好两项**：`我的记忆` 与 `智能办公助理`（个人域显式可选，不是空选择），即任务 5.1 前半的服务端目标集合在真实 UI 上成立。
- **无任何报错**：全程无错误 toast / 横幅 / 拒绝提示。

## 2. 管理员侧（test15）

- 登录后直接落在 `/admin`，账号标签 `test15管理员` / `@test15`。
- `组织与权限` → `成员管理 / 组织架构 / 角色权限` **可见**；`成员管理` 打开真实成员列表（3 行：`RC001`、`test15`、`test15-2`，各带 `编辑 / 外部身份`）。
- `平台账号` 既不出现在导航，也不作为页面渲染（仅隐藏页标题留在 DOM）。
- **同页双面**：管理员的 `消息渠道` 描述为 `配置本租户的消息渠道，凭据加密存储且不会回显`，并列出 2 个租户渠道（`erp` 飞书、`税务健康体检` 企微智能机器人）；成员在同页看到的是个人面文案与空列表。这正是任务 6.1「同一页面按对象范围区分角色」的现场证据。

## 3. 两个「疑似缺陷」的实测裁定

浏览器只读观察出现两处可疑现象，逐一对服务端复核后裁定如下（第 1 项原判「非缺陷」**已于 2026-09-16 更正为真实缺陷**，原因见该条；第 2 项确为既有非缺陷）：

1. **成员的类型选择器给出 8 项**，一度怀疑超出 `PERSONAL_READY_CHANNEL_TYPES`。
   实测 `GET /api/tenant/channels`（带 `X-Tenant-ID`，以 RC001 身份）返回 `scope=self`、`channel_types` **8 项且全部 `ready=true`**，`targets` 仅 `('my-assistant-admin-test15-RC001', 'user')`。
   原因是 `MULTI_INSTANCE_READY`（`channel/channel_instances.py`）本身就含 `WEIXIN` 与 `WECOM_BOT` 共 8 项，`PERSONAL_READY_CHANNEL_TYPES` 直接取自该集合；前端 `tenantChannelTypeChoices()` 对自有面按 `spec.ready` 收窄。

   **本条原裁定「不是缺陷」是错的，于 2026-09-16 更正。** 原推理只核对了「列表 == 声明」
   （8 项都 `ready=true`，与 `MULTI_INSTANCE_READY` 一致），没有核对**该声明本身是否为真**：
   `MULTI_INSTANCE_READY` 的含义是「能跑多实例」，这 8 项里有 5 项**从不调用
   `stamp_external_identity`**，因此入站消息永远无法按实例证明发送者。于是目录给出一个
   提交会被接受、却能永久静默失败的选项。真实可达路径与根因见
   `7-channel-type-admissibility.md`：**公共归属**（`scope='tenant'`）连接今天就能被真正拉起并
   被控制台报为 `connected`，第一条消息在 `channel/chat_channel.py` 的身份戳前置检查处被拒，
   早于任何绑定查询、且不落待绑定列表，操作者只从服务端一行 warning 得知。

   教训（对本 change 的核对方法）：**核对一致性不等于核对真实性**。「列表与声明一致」只能证明
   两边读同一个常量，不能证明该常量描述的事实成立；裁定「非缺陷」必须落到事实来源上。
2. **管理员会话出现 `GET /config?agent_id=... → 403`**。裁定为**既有行为，非本次引入**：
   - `console.js` 的 `window.fetch` 包装会对所有同源请求补 `agent_id`（HEAD 第 4810 行，本次未改）；
   - `restoreChatState()` 调用 `GET /config`（HEAD 第 4865 行，本次未改），而 `/config` 在路由表是 `P("platform")`，租户管理员本就不该通过；
   - 失败被 `.catch` 吞掉，品牌标题另有 `fetchPublicBrand()` 公共快照通道，因此**无可见错误**、不影响页面。
   留作既有项，不在本 change 内改策略（改 `/config` 资格属于平台配置面，需要在别的 change 里谈）。

## 4. 顺带发现并修复：i18n 快照漂移（真实缺口）

只读验收后跑 `node --test tests/test_console_i18n_parity.cjs`，**未通过**。定位如下（以 HEAD 干净检出对比确认：HEAD 时该用例通过，故漂移是本工作区未提交改动造成的，不是既有失败）：

- 注册表比快照**多 55 键 × 3 语**：`admin_home_kpi_unavailable` / `admin_home_kpi_retry`（3.5）、`agents_set_my_default_*` 与 `agents_anchor_source_*`（4.4/4.6）、`agent_disabled`、`memory_target_personal`（5.1）、`personal_channels_*`（前序 change）、`tenant_channel_self_desc`（6.1）。
- 快照**残留 5 键 × 3 语**：`account_menu_resources*` / `account_menu_region_personal`——正是任务 3.3/3.4 删掉「我的资源」后应退役的文案。
- `account_menu_trigger_hint` 取值漂移：`个人资源与设置` → `账号设置`（任务 3.4 的口径）。

处理：**定向同步**，不整表重排。先验证快照可 `json.dumps(..., ensure_ascii=False, indent=2)` 逐字节回环（确认可安全重写），再按「新键插入到同名族既有键之后、删除退役键、更新漂移值」生成，diff 收敛为 `+174 / -21` 行，避免整文件重排（此前一次整表排序重写产生 `+3989 / -3836` 行噪声，已回退）。

`node --test tests/test_console_i18n_parity.cjs` → **5/5 通过**。

**2026-09-16 补记：上述同步后来在磁盘上不复存在，本节的「5/5 通过」当时为真、事后失效。**
`evidence/8-4c-i18n-snapshot-reconciliation.md` 在动手前实测该快照相对 HEAD 只有 `+54/−3`
（仅另一并发 worker 新增的 18 个 `resource_detail_*` / `models_catalog_*` 键），即本节所述的
`+174/−21` 已丢失；同一现象也见于 3.3 证据与已归档 `upgrade-personal-channel-workbench` §3.6
的同类声明。**根因判定为并发整文件覆写**：该快照是大 JSON，多名 agent 各自整文件重写时会互相
覆盖，与本 change 早前 `auth/service.py` 被静默覆写（见 `evidence/6-1-shared-channel-surface.md`）
同一机制；本文件当次即因此丢失，而非「报告不实」。因此：**该快照不得整文件重写**，只能做键级
定向插入/删除，且在全部 worker 停止后**必须重跑一次** `test_console_i18n_parity.cjs` 才算数。
本轮由 8-4c 重新收敛（`+207/−18`，相对 HEAD `261/21`，全键有归属、无不可解释项，5/5）。

## 5. 顺带修复：空态文案不分范围

现场看到成员的自由面空态写着「还没有为**本租户**配置任何渠道」，而同页描述已是「管理**你本人**的渠道连接」——描述分范围、空态不分范围。

- `i18n/tenant-channel.js` 三语新增 `tenant_channel_empty_desc_self`（简「还没有接入任何渠道」/ 繁「還沒有連接任何管道」/ 英「You have not connected any channel yet」）；
- `renderTenantChannels()` 改为按 `tenantChannelSelfScope` 选词；
- 快照同步该键；
- `tests/test_tenant_channel_frontend.cjs` 新增两条断言：空态必须按范围选词；`_self` 三语齐备且**不得**出现 `本租户/本租戶/this tenant`。
  → 该文件 **27/27 通过**。

## 6. 未覆盖

- 直达/返回/离页取消、账号切换、租户切换等在本次只读验收中未逐一走完（未强制导航到越权 URL，故拒绝路径未在现场触发）。
- 未做破坏性操作（不新增/删除渠道、不改设置），故失败态与取消态未在现场制造。
