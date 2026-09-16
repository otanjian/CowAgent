# Stage 9.1 — 独立能力开关的证据

任务：登记设计中的独立能力开关，接入后端入口、消费者启动与权威投影，保证关闭新能力不会
关闭已验收目录或撤去 owner 检查。

## 1. 登记与取值

| 位置 | 内容 |
| --- | --- |
| `auth/policy.py` | `PERSONAL_CAPABILITY_SWITCHES`（五个名字）、`PERSONAL_CAPABILITY_DEFAULTS`（出箱默认）、`PERSONAL_PAGE_CAPABILITIES`（页面→开关链）、`personal_capability_enabled` / `personal_page_capabilities` / `personal_page_enabled` |
| `config.py` | 五个键的默认值（`member_personal_console`、`user_private_agent_management`、`personal_memory_write`、`personal_channel_onboarding`、`personal_channel_runtime`），随既有配置目录一起加载 |
| `openspec/changes/.../specs/console-navigation-availability/spec.md` | 新增 requirement「个人能力开关只控制开放且不替代权限检查」 |

出箱姿态：四个已有真实证据的分片默认开启；`personal_channel_runtime` 默认关闭（尚无渠道
类型完成真实端到端验收，任务 7.5）。未登记名称一律按未开启处理；配置文件不可读时回落默认
值，因此配置损坏不会放开执行类能力。

```python
PERSONAL_CAPABILITY_DEFAULTS: Dict[str, bool] = {
    "member_personal_console": True,
    "user_private_agent_management": True,
    "personal_memory_write": True,
    "personal_channel_onboarding": True,
    "personal_channel_runtime": False,
}
```

## 2. 接入点

### 2.1 权威投影

- `auth/service.py:_console_pages_projection`：个人页面在开关链未全开时
  `available=False`、`reason=capability_disabled`，并随页面返回 `switches`（每个依赖开关的
  求值结果）。菜单未授权仍按 `menu_not_granted` 报告，两者不混用。
- `auth/service.py:_personal_channel_execution_open` 走 `personal_runtime_enabled`，因此
  「配置可保存、连接未开放」的执行状态与真实连接闸门只有一个事实来源。
- 前端 `channel/web/static/js/personal-console.js`：`personalPageDenial` 先判
  `capability_disabled`（**先于平台 `all` 短路**），拒绝页正文标明未启用的能力
  （`personal_denied_capability` + 开关文案），并写 `data-personal-denied-reason`。
- 前端 `channel/web/static/js/console.js`：侧栏按 `reason === 'capability_disabled'` 隐藏
  对应入口；当「我的」组内全部入口被隐藏时，组标题一并隐藏。直接 URL 仍可到达页面正文，
  由其说明能力状态（因此既不是「假装没授权」，也不启动消费者）。

### 2.2 后端入口（开启类写入）

| 入口 | 开关 | 拒绝行为 |
| --- | --- | --- |
| `PrivateAgentService.create_private_agent` / `bind_private_agent_with_quota` | `user_private_agent_management` | 403 `capability_disabled`；早期拒绝发生在 clone 之前，补偿逻辑仍然生效 |
| `create/update_personal_channel_instance`、`start_personal_channel_binding` | `personal_channel_onboarding` | 403 `capability_disabled`，事务未开启 |
| `set_personal_channel_instance_active(active=True)` | `personal_channel_onboarding` | 403；`active=False` 始终放行 |
| `save_personal_resource_config` | `member_personal_console` | 403；读取与清除保持可用 |
| `PersonalMemoryService.save` | `personal_memory_write` | 403 `capability_disabled`；读取、删除、清空与索引重试保持可用 |

关闭只做减法：owner、成员有效性、菜单授权与功能权限检查在开与关两种情况下都照常执行；
`revoke` / `unbind` / `disable` / `delete` / `clear` 不受开关影响，成员不会被困在收不回的
对象上（新增用例覆盖）。

### 2.3 消费者启动

- `channel/channel_instances.py:personal_runtime_enabled`：两个条件同时成立才放行 ——
  部署总开关 `personal_channel_runtime` + 该类型的真实验收记录
  （`PERSONAL_RUNTIME_ACCEPTED_TYPES`）。总开关先判，因此回滚是一次配置变更。
- `public_personal_ingress_ready`：共享实例承载个人路由同样受总开关约束。
- `load_tenant_channel_instances`（启动合成）：个人实例在类型未验收、或 owner 已非有效
  成员时**不合成、不启动**，只跳过该实例并记录原因；租户实例不受影响。
  `list_enabled_tenant_channel_instances` 因此补充返回 `scope` / `owner_user_id`，让启动
  路径自己做出决定，而不是在查询里隐式过滤。
- 请求期：`resolve_personal_channel_inbound` 与 `apply_tenant_instance_runtime` 各判一次，
  已在连接中的个人实例在撤回后不再继续服务。

## 3. 验证

新增 `tests/test_personal_capability_switches.py`（28 个用例）：

- 登记表：五个名字、出箱默认、未知名称不视为开启、配置不可读回落默认、字符串写法、
  页面→开关链，以及「任一开关关闭即页面关闭」。
- 投影：关总开关后五个个人页面均为 `capability_disabled` 且不报为菜单未授权；同一身份的
  `admin.*` 页面不受影响；受限菜单下关闭开关仍报 `menu_not_granted`；只关
  `personal_channel_runtime` 时目录仍可读可配置、执行状态关闭；执行状态与连接闸门同源。
- 写路径：五个入口分别在对应开关关闭时 403 且无部分写入（含启动合成前无 roster 残留）；
  owner 检查在开关关闭后仍拒绝他读他写；读取与收回类操作保持可用。

其他回归：

- `tests/test_personal_channel_inbound.py`：夹具显式抬高总开关（记录类型仍必要但不充分），
  新增「总开关压过已记录类型」用例；原有 8 项变异仍被拦截。
- `tests/test_personal_channel_console.py` / `tests/test_personal_console_acceptance.py`：
  `_personal_runtime_on` 与执行状态用例同时抬高总开关，并新增「只有类型记录、总开关关闭时
  执行仍为关闭」的反向控制。
- `tests/test_tenant_channel_startup_synthesis.py::PersonalInstanceStartupTests`：个人实例
  未验收时不随启动合成、验收后随启动合成、owner 失效时不启动。
- 前端：`tests/test_personal_console_frontend.cjs`（56 通过）覆盖能力撤回先于平台 `all`
  短路、拒绝原因与命名开关、菜单拒绝不被能力原因覆盖；i18n 快照与
  `tests/test_console_i18n_parity.cjs` 同步（新增 6 个键 × 3 语言）。
- 真实浏览器契约 `tests/test_personal_console_browser.cjs` 新增场景：撤回能力后入口隐藏、
  其余入口与组保持、直接链接落到命名能力的拒绝正文且不请求消费者。

## 4. 记录到的问题与处理

- 启动合成此前**未**判断个人执行闸门（只在热重启与请求期判断），意味着一个未完成真实验收
  的个人实例可能随进程启动而建立连接。任务 9.1 的「消费者启动」接入同时修掉了这一点，并
  用 owner 有效性一并约束。
- `tests/test_sidebar_account_frontend.cjs` 有 5 个用例在**基线提交**（HEAD）上即失败
  （`mode: 'legacy' / 'unknown'` 断言早于 legacy 身份模式退役），本任务的工作树与基线失败
  集合一致，未因本任务变化；已用 HEAD worktree 逐文件对比确认（见 9.3 证据）。
- `tests/test_console_view_registry.cjs`：`_loadRegisteredView` 在任务 8.4 的重构后新增
  `_activateViewContainer` 依赖，测试沙箱未注入该函数与 `document`/`currentView`。属本次
  变更引入的真实缺口，已修正测试沙箱（6/6 通过）。
