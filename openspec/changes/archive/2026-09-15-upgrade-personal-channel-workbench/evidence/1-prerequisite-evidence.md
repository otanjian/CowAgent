# 阶段 1：依赖切片与实施基线核对

本文件记录 `tasks.md` 1.1 / 1.2 / 1.3 的核对结果。所有结论来自对当前工作区代码的实读与可复跑命令，
不以 `enable-member-personal-console` / `move-personal-menu-to-account` 的 change 勾选状态代替检查。

## 1.1 个人实例、所有权、身份挑战、治理、凭据、审计、配额与菜单切片

### 结论：切片可用，可作为实现基线

| 切片 | 真值位置 | 现状 |
|---|---|---|
| 个人实例 | `tenant_channel_instances`，`scope='user'`、`tenant_id`、`owner_user_id` | 存在；`_resolve_instance_scope`（`auth/service.py:6710`）校验 `(scope, owner)` 对，owner 必须是同租户活跃成员 |
| 私有目标所有权 | `agent_bindings.private_owner_user_id` | 存在；`is_private_agent_owner`（`auth/service.py:1857`）、`private_agent_ids`（`auth/service.py:1840`） |
| 身份挑战 | `binding_challenges` + `personal_channel_links` + `external_identities` | 存在；`start_personal_channel_binding`（`auth/service.py:8073`）、`redeem_personal_channel_challenge`（`auth/service.py:8142`）、`_claim_challenge`（`auth/service.py:8266`，条件 UPDATE 保证单次消费） |
| 治理停用 | `tenant_channel_instances.governance_disabled_at/by`，`set_personal_instance_governance`（`auth/service.py:7125`） | 存在；停用同时置 `active=0`，解除停用不自动启用 |
| 凭据 | `credentials` + `credential_versions`，`name='channel:<instance_id>'` | 存在；撤销为状态位而非删除，保留版本历史（`revoke_personal_channel_credentials`，`auth/service.py:7964`） |
| 审计 | `_audit_in_tx`，`channel.instance.create/update`、`credential.create/rotate/revoke`、`channel.personal.governance_*` | 存在；治理事件只记元数据，不含类型/名称/owner |
| 配额 / 应用占用 | `_enforce_personal_instance_policy`、`_assert_no_app_conflict`（在写事务内） | 存在；`create_tenant_channel_instance` 在 `BEGIN IMMEDIATE` 内计数与插入 |
| 菜单 | `chat.html:464` `data-view="personal-channels"`，`data-i18n="menu_personal_channels"`，`VIEW_META['personal-channels'].console='personal.channels'`（`console.js:1622`） | 存在，位于账号菜单 `#account-menu-resources` 组 |

### 已发现的缺口（本 change 要补）

1. **目标校验只到同租户**：`_require_instance_agent`（`auth/service.py:6675`）对空 `agent_id` 直接返回 `''`，
   且只校验 `binding.tenant_id == tenant_id`。个人实例因此可以落库为空目标、公共目标、他人私有目标或已停用目标。
   调用点：`create_tenant_channel_instance`（`auth/service.py:7257`）、`update_tenant_channel_instance`（`auth/service.py:7426`）。
   底层共用写入是「个人入口 + 扫码提交」的共同路径，因此判定必须放在服务层而非个人包装层。
2. **身份关联缺版本条件**：`start_personal_channel_binding` / `unlink_personal_channel_instance`
   （`auth/service.py:8073` / `8303`）不接收 `expected_version`，也不复检当前 owner。
3. **列表投影不足**：`list_personal_channel_instances`（`auth/service.py:7815`）只返回 `items` + `total`，
   没有 `agent_options`、`actions.create`、`create_unavailable_reason`，也不区分配置/连接/关联/可对话状态。
4. **前端未消费服务端动作与挑战**：`personal-console.js` 走通用行列表，`_dispatch` 丢弃响应体，
   `start_binding` 返回的一次性 `challenge` 从未展示；`personal_channels_binding_*` 文案已存在但零引用。

### 可复跑证据

```
.venv/bin/python -m pytest tests/test_personal_channel_console.py tests/test_personal_channel_binding.py \
  tests/test_tenant_channel_instances_service.py tests/test_personal_capability_switches.py -q
→ 162 passed

.venv/bin/python -m pytest tests/test_private_agent_quota.py tests/test_user_personal_agent_provisioning.py \
  tests/test_personal_console_web.py tests/test_personal_console_pages.py tests/test_personal_console_menu.py \
  tests/test_personal_console_multi_tenant_authorization.py tests/test_scan_onboarding_state.py \
  tests/test_tenant_channel_http.py tests/test_tenant_channel_mutations.py -q
→ 219 passed, 4 subtests passed

node --test tests/test_personal_console_frontend.cjs tests/test_console_i18n_parity.cjs
→ 62 pass / 0 fail
```

## 1.2 飞书注册、一次性授权与扫码状态存储形态

### 现状事实

| 项 | 事实 | 位置 |
|---|---|---|
| 注册会话 | 进程内 `_sessions` 字典，键为不透明 `handle`，按 `(owner_user_id, owner_tenant_id)` 归属校验 | `channel/web/web_channel.py:8065`、`:8135` |
| 会话替换粒度 | 同一 `(user, tenant)` 再次发起会取消并替换自己的上一个会话 | `_create_session` `:8108` |
| 一次性交付 | `done` 时把 `app_id` / `app_secret` / `scan_ticket` 交付一次并从会话移除 | `_poll_payload` `:8183` |
| 扫码授权存储 | 进程内 `_tickets` 字典 + `threading.Lock`，**无持久化** | `auth/scan_authorization.py:46` |
| 授权绑定维度 | 仅 `(actor_user_id, tenant_id, channel_type)` | `scan_authorization.mint` `:50`、`_matches` `:112` |
| 消费语义 | `verify` 不消费（创建前授权），`consume` 在行提交后调用，成功才消费 | `create_tenant_channel_instance` `:7341` |
| 失败原子性 | 拒绝发生在 `consume` 之前，因此不消耗仍有效的授权 | 同上 |
| 部署形态 | 单进程多线程 cheroot `WSGIServer`（`daemon_threads`、`requests.min/max=20/80`），**非多 worker** | `web_channel.py:3347-3356` |

### 本次个人扫码开放前必须补齐的缺口

1. **作用域与用途未绑定**：授权只有 `(user, tenant, channel_type)`，没有 `scope=personal`、`purpose=create`、
   `agent_id`、登录会话（AuthSession）。因此同一用户的公共扫码授权与个人扫码授权**不可区分**，
   「旧公共授权不得用于个人创建」当前无法成立。
2. **会话隔离粒度不足**：注册会话按 `(user, tenant)` 替换，同一用户打开公共页与个人页会互相取消对方的扫码会话。
3. **创建包装未接收 `scan_ticket`**：`PersonalChannelHandler.POST`（`web_channel.py:8843`）不读取 `scan_ticket`，
   个人创建因此只能走近期密码；`create_tenant_channel_instance` 已支持该参数并会在提交后消费。
4. **AuthSession 未参与**：`RequestContext`（`auth/runtime.py:28`）不含登录会话 id；可经
   `_get_service().verify_session(_session_token())["session"]["id"]` 取得（既有用法见 `web_channel.py:877-880`）。

### 存储形态结论

当前部署是**单进程**服务，进程内会话与授权与其生命周期一致，不构成既有缺陷。
但 `_sessions` / `_tickets` 都不跨进程，因此**多 worker 部署下扫码不成立**。
本次不扩展部署支持形态：个人扫码开放依赖「单进程」这一既有前提，且必须让 `AuthSession` 与
`scope/purpose/agent_id` 进入授权绑定，使跨作用域复用被服务端拒绝。若后续改为多 worker，
需先把扫码状态改为共享存储，否则不得开放个人扫码。

## 1.3 隔离夹具

### 结论：既有 `WebAppHarness` 已覆盖所需角色，缺「私有目标 + 停用/不存在目标」构造

`tests/_helpers.py:165` 的 `WebAppHarness` 提供：

- 真实 `build_web_app()` over 私有 identity 库（`web_app` fixture，`tests/conftest.py:60`）；
- `bootstrap` 出的租户 + `root` 管理员，`add_agent()` / `bind_agent_to_tenant()` 写 roster 与绑定；
- `member(username, roles, ...)` 创建已完成首登改密的普通成员，`login()` 取真实会话 cookie；
- `role(code, permissions, grants)` 建角色并授 `agent.use` 资源授权；
- `_bootstrap_tenant` 可经 `stack_factory` 扩展为第二租户。

已有可用的隔离用例先例：`tests/test_personal_console_multi_tenant_authorization.py`（两租户）、
`tests/test_tenant_channel_console_scope.py`（公共/个人范围）。

**缺口**：夹具目前只创建「公共」绑定（`bind_agent` 不带 `private_owner_user_id`），
需要补一个 `private_agent(user_id, agent_id, origin=...)` 辅助方法，覆盖：

| 目标类型 | 构造方式 |
|---|---|
| 系统专属助理 | `bind_agent(private_owner_user_id=user, origin=<SUPPLIED_ASSISTANT_ORIGINS 之一>)` |
| 自建私有 | `bind_agent(private_owner_user_id=user, origin='user_created')` |
| 公共目标 | `bind_agent(private_owner_user_id=None)` |
| 他人私有 | `bind_agent(private_owner_user_id=other_user)` |
| 停用目标 | roster 中置 `enabled=False` |
| 不存在目标 | 只给 id，不入 roster、不绑定 |
| 跨租户目标 | 绑到第二租户 |

初始运行开关：`personal_channel_runtime`（部署总开关，默认关闭）、`PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()`
（`channel/channel_instances.py:1224`）、`PUBLIC_PERSONAL_INGRESS_TYPES = frozenset()`（`:1230`）。
配置文件可分别用 `personal_channel_runtime_types` / `public_personal_ingress_types` 收窄，只能收窄不能放宽。
