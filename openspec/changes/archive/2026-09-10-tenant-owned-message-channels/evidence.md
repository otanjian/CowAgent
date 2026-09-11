# 验证证据：tenant-owned-message-channels

本文件记录 `tenant-owned-message-channels` 变更的验证命令、实际输出与结论。
所有命令均在仓库根目录、`.venv` 解释器下执行。

**结论先行：本变更的实现与测试已完成，可复现验证全绿；但按 task 10.8 的启用门槛，
`open-database-runtime` 尚未归档，因此本能力不得对外声明为「已可用」。**

---

## 1. 交付范围

| 项 | 状态 |
| --- | --- |
| 租户侧渠道实例表 `tenant_channel_instances`（迁移 #7） | 完成 |
| 服务层生命周期与授权（创建/列表/更新/启停/凭据注入） | 完成 |
| 启动期合成（`identity.db` → `ChannelInstance` 合并进 roster 列表） | 完成 |
| 租户可选渠道类型闸门（飞书可用；企微自建应用延后） | 完成 |
| 飞书入站租户隔离（instance → 同租户 Agent → 租户成员） | 完成 |
| HTTP 端点与路由策略（`/api/tenant/channels`；`/api/channels` 由 `closed` 改 `platform`） | 完成 |
| 能力投影与菜单作用域（`admin.channels` 平台/租户双作用域） | 完成 |
| 前端视图、表单、失败分支与三语 i18n | 完成 |
| 跨租户隔离 / 凭据落点 / 变异 / 端到端闭环验证 | 完成 |

显式**未**交付（见第 7 节）：企微自建应用（`wechatcom_app`）的租户级多实例；
按渠道类型的最小必填凭据校验。

---

## 2. 定向测试（本变更新增）

命令：

```
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tenant_channel_*.py -q
```

结果：**133 passed, 6 subtests passed**（含变异检查的 3 个子测试）。

覆盖文件与职责：

| 文件 | 守护的性质 |
| --- | --- |
| `test_tenant_channel_instances_migration.py` | 迁移 #7 的 DDL、幂等重放、列约束 |
| `test_tenant_channel_instances_service.py` | 生命周期、掩码投影、版本冲突、租户一致性、近期密码、凭据注入 |
| `test_tenant_channel_type_gate.py` | `wechatcom_app` 不许多实例就绪 / 无凭据字段 / 单例碰撞 |
| `test_tenant_channel_inbound_isolation.py` | 入站解析到实例所属租户；跨租户身份不可执行 |
| `test_tenant_channel_inbound_closure.py` | **端到端闭环**：建实例 → 启动合成 → 入站 → Agent 调用 / 拒绝 |
| `test_tenant_channel_startup_synthesis.py` | 合并、降级、legacy 不读租户表、轮换后重启生效 |
| `test_tenant_channel_http.py` | 路由级行为、掩码、无权限、非 database 模式拒绝 |
| `test_tenant_channel_console_scope.py` | `admin.channels` 投影与作用域 |
| `test_tenant_channel_isolation_acceptance.py` | **跨租户隔离专项**（读/改/绑 Agent/不泄漏存在性） |
| `test_tenant_channel_credential_landing.py` | **凭据落点**：identity.db 加密、响应/审计/roster 无明文 |
| `test_tenant_channel_mutations.py` | **变异检查**：移除隔离守卫必须使测试失败 |

前端（`node --test`）：

```
node --test tests/test_tenant_channel_frontend.cjs            # 12 passed
node --test tests/test_i18n_tenant_channel_keys.cjs           # 4 passed
node --test tests/test_channel_scope_nav_frontend.cjs         # 7 passed
```

---

## 3. 跨租户隔离专项（10.1）

`tests/test_tenant_channel_isolation_acceptance.py` —— 7 passed。

四条性质各自的证据：

1. **读不到**：租户 B 列出 `/api/tenant/channels` 得到 `items == []`、`total == 0`；
   A 的列表不受 B 的行影响（只出现自己的显示名）。
2. **改不了**：B 对 A 的实例发编辑/启停请求，响应与「不存在的实例 id」**逐字段相同**
   （status、code、message 三元组相等）。这是关键的「不泄漏存在性」断言——
   若返回 403 而非常规 404，攻击者可枚举他租户实例 id。
   另有断言确认跨租户编辑后目标实例的 `display_name` 与 `version` 均未变。
3. **不能绑定他租户 Agent**：B 以 `agent-a`（属于 A）创建实例被拒（403），
   且列表仍为空 —— 证明整个事务回滚，未留下半成品实例。
4. **运行期注入按租户**：同一 `instance_id` 换租户查询 `channel_instance_credentials`
   抛 `not found`；属主租户正常解析出 bundle。

---

## 4. 凭据落点（10.2）

`tests/test_tenant_channel_credential_landing.py` —— 6 passed。

使用唯一哨兵值 `sk-sentinel-2f9c1d7a-should-never-appear-in-plaintext`，
对四个独立表面做子串扫描：

| 表面 | 断言 | 结果 |
| --- | --- | --- |
| `identity.db` 磁盘字节 | 哨兵不出现（密文存储）；同时确认表名存在，避免空扫描假通过 | 通过 |
| 创建响应 | 哨兵不出现 | 通过 |
| 列表响应 | 哨兵不出现 | 通过 |
| 服务层掩码投影 | 哨兵不出现 | 通过 |
| 审计流水 | 哨兵不出现，且记录了含 `channel` 的动作 | 通过 |
| `team.json`（roster 文件） | **创建前后文件都不存在**，且实例根目录全量扫描无哨兵 | 通过 |

最后一项是本设计的关键：租户渠道凭据**完全不经过** `team.json`。

---

## 5. 变异检查（10.3）

`tests/test_tenant_channel_mutations.py` —— 1 passed, 3 subtests passed。

对三处守卫做定点变异，确认对应测试**由通过转为失败**（已核实失败原因是
`AssertionError`，而非语法/收集错误），随后逐字节还原源文件：

| # | 变异 | 期望被捕获的测试 | 结果 |
| --- | --- | --- | --- |
| 1 | `_require_instance_agent` 去掉 `binding["tenant_id"] != tenant_id` | `test_agent_from_another_tenant_is_rejected`、`test_binding_another_tenants_agent_is_refused` | 捕获 |
| 2 | `list_tenant_channel_instances` 的 SQL 去掉 `WHERE tenant_id=?` | `test_list_never_returns_another_tenants_instances`、`test_b_tenant_cannot_see_a_tenants_instances` | 捕获 |
| 3 | 把 `wechatcom_app` 同时加入 `MULTI_INSTANCE_READY` 与 `CREDENTIAL_KEYS`（最现实的回归路径） | `test_wechatcom_app_is_not_declared_multi_instance_ready`、`test_the_form_contract_offers_feishu_and_withholds_wechatcom_app` | 捕获 |

变异 #3 的实测失败信息：

```
AssertionError: 'wechatcom_app' unexpectedly found in
frozenset({'wecom_bot', 'qq', 'dingtalk', 'weixin', 'slack', 'telegram',
           'wechatcom_app', 'feishu', 'discord'})
```

**实施期发现**：`tenant_channel_types()` 中的 `MULTI_INSTANCE_READY` 过滤与
`CREDENTIAL_KEYS` 成员判断当前**互为冗余**（两个集合的非空键集完全一致），
因此单独变异任一处都不会被发现。这正是变异 #3 需要同时改动两处的原因——
双重闸门是有意保留的防御（「多实例就绪」与「已声明凭据契约」是两个独立前提，
未来可能只满足其一），但需知悉当前它并非各自独立可观测。

---

## 6. 端到端闭环（10.5）

`tests/test_tenant_channel_inbound_closure.py` —— 3 passed。

单条链路串起全部真实组件，仅在模型边界替换为记录器：

```
租户管理员创建飞书实例（identity.db，加密）
  → 启动合成：load_tenant_channel_instances() 解密为 ChannelInstance（legacy=False）
  → 进入 ChannelManager 入口列表：resolve_channel_instances(...) 含该实例
  → 入站飞书事件：external_identity.resolve_actor_for_context((app_id, open_id), agent)
  → 命中租户成员：runtime_identity = {tenant_id: acme, user_id: acme 成员, agent_id: agent-acme}
  → preflight 返回 False（调用方继续执行 Agent）
```

同一文件中的两个拒绝半程证明「未授权就绝不执行」：

- 他租户（Globex）身份到达 Acme 的渠道实例（路由到 `agent-acme`）→
  `preflight` 返回 `True`、`runtime_identity` **未被设置**、`_generate_reply` **未被调用**、收到固定拒绝通知；
- 未绑定身份 → 同样被拦截，模型未被调用。

即：授权路径以租户身份抵达 Agent，未授权路径在 preflight 处终止。

---

## 7. 全量回归与基线对比（10.4）

### 7.1 后端

| 运行 | 结果 |
| --- | --- |
| 全量（含本变更新测试） | `33 failed, 2150 passed, 3 skipped` |
| 全量（`--ignore` 全部 `test_tenant_channel_*`） | `33 failed, 2022 passed, 3 skipped` |

**两次运行的失败集合逐条完全相同。** 因此这 33 个失败与本次变更无关，是既有的、
位于无关子系统的失败。为排除「仅在合并运行下才失败」的排序/状态泄漏，
对每个失败文件单独运行后归因如下：

| 类别 | 文件 | 单独运行 |
| --- | --- | --- |
| 单文件即失败（既有缺陷） | `test_security_ssrf_browser_navigate` (12)、`test_session_history_search` (5)、`test_read_edit_improvements` (3)、`test_identity_self_context` (2)、`test_claude_thinking` (1)、`test_dashscope_provider` (1)、`test_tenant_default_agent` (1) | 均失败 |
| 仅全量时失败（既有测试脆弱性，与本变更无关）： | `test_feishu_progress_card` (4)、`test_todo_service` (2)、`test_consumer_closure_acceptance` (1)、`test_tenant_create_containment` (1) | 均通过 |

合计 25 + 8 = 33，与全量失败数一致。第二类在移除本变更测试后**依然失败**，
故非本变更引入。

### 7.2 前端（逐文件运行，`node --test`）

本变更相关文件全部通过：

```
test_tenant_channel_frontend.cjs        pass 12  fail 0
test_i18n_tenant_channel_keys.cjs       pass  4  fail 0
test_channel_scope_nav_frontend.cjs     pass  7  fail 0
test_i18n_tenant_editor_keys.cjs        pass  4  fail 0
```

既有失败（无关，属其他在制品变更）：`branding_frontend.test.cjs` (6)、
`test_appearance_browser.cjs` (1)、`test_session_history_frontend.cjs` (13)、
`tests/_tmp_repro_modeldefaults.cjs` (1)。

**归因结论：本变更引入 0 个新回归。**

---

## 8. 模式与开关（10.7）

- **未引入任何 feature flag**：在 `auth/service.py`、`auth/http_policy.py`、
  `channel/web/admin_handlers.py`、`channel/channel_instances.py` 中检索
  `feature_flag` / `FEATURE_` / `tenant_channels_enabled` 均无命中。
- **仅 database 模式启用**：三个处理器的每个方法（`GET`/`POST` × 3 个类）
  首行即 `_guard_database()`；非 database 模式返回 `400 / code=not_database`。
  由 `DatabaseModeGateTests` 两个用例固定
  （列表与创建各一），防止能力在 legacy 模式下「半可用」。

---

## 9. 启用门槛（10.8）——**未满足**

task 10.8 要求：本能力对外声明可用前，必须同时满足
①`open-database-runtime` 已归档，②跨租户隔离与端到端证据齐备。

实测：

```
$ ls -d openspec/changes/open-database-runtime
openspec/changes/open-database-runtime        # 仍是「进行中」变更，未归档
$ ls openspec/changes/archive/ | grep -i database-runtime
（无输出）
```

- ②**已满足**：第 3、4、6 节分别给出跨租户隔离、凭据落点与端到端闭环证据。
- ①**未满足**：`open-database-runtime` 仍处于 active 状态。

**因此：不得声明本能力已可用。** 本变更的实现可以合入并随
`open-database-runtime` 一起进入维护窗口，但在后者归档前，交付表述应为
「已实现、已验证，启用待 `open-database-runtime` 归档」。

---

## 10. 已知缺口与显式延后项

### 10.1 企微自建应用（`wechatcom_app`）多租户 —— 显式延后

`wechatcom_app` 使用固定端口 + 固定路径的 webhook，并通过进程级 `@singleton`
解析渠道实例。若开放租户级多实例，多个租户的实例会在端口/路径上碰撞，
消息会被路由到同一个共享实例对象，导致**跨租户数据泄漏**。
这与本变更的隔离目标直接冲突，故本轮不开放该类型。

已由测试固定（`test_tenant_channel_type_gate.py`）：

- `wechatcom_app` 不在 `MULTI_INSTANCE_READY`；
- 无声明凭据字段；
- 无 per-instance 单例旁路，两个租户会坍缩到同一共享对象。

未来开放需先完成：webhook 按实例路由（端口/路径或子域）、去掉 `@singleton`、
补齐凭据契约与入站三元组 stamp。`MULTI_INSTANCE_READY` 在本变更中**未**为
`wechatcom_app` 做任何修改。

### 10.2 按类型的最小必填凭据集 —— 已知缺口（10.9）

服务端 `_validated_channel_bundle` 只校验两件事：字段名必须在
`CREDENTIAL_KEYS` 内、且**已提供的**字段非空。它**不**校验某类型的最小必填集。

后果：只提交 `feishu_app_id` 而缺 `feishu_app_secret` 的实例会被创建成功，
直到下次启动时才以「无法启动通道」的可诊断原因被跳过。
`test_a_blank_credential_field_is_rejected` 因此只断言「已提供字段不得为空」。

各通道启动守卫已核实的最小必填集：飞书 `feishu_app_id`+`feishu_app_secret`；
QQ `qq_app_id`+`qq_app_secret`；Telegram `telegram_token`；
Slack `slack_bot_token`+`slack_app_token`；Discord `discord_token`；
WeCom Bot `wecom_bot_id`+`wecom_bot_secret`；钉钉/微信待核。

本切片未新增 `REQUIRED_CREDENTIAL_KEYS`；建议作为后续切片（创建期即拒绝，
而非留到启动期）。
