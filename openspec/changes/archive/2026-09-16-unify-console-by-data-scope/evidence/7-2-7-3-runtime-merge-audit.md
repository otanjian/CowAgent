# 7.2 / 7.3 运行面合流审计：归属判定、单一实现、状态来源与旧开关

本文件回答 `tasks.md` 7.2 与 7.3 的两件事：**运行实现是否只按实例实际归属选择**、
**目录/配置/执行状态是否各自只有一个来源，且旧个人开关的显式关闭不被新入口绕过**。

**范围与声明**

* 只审**运行面**（连接管理、启动合成、热更新、入站分发）与旧开关的**读取点**。
  控制台投影、写入合并（6.1–6.6）只作为对照引用，不重复举证。
* 本节不含任何真实厂商往返。`evidence/7-1-runtime-preflight.md` 已记录提供方与凭据
  清单（三处独立阻塞），此处不重复盘点；凡涉及"真实验收"的结论一律标为**受阻**。
* 行号取自工作区修订 `cbdb9884`（未提交工作树）。`auth/service.py`、
  `channel/web/web_channel.py` 正被其他 agent 并发修改，行号可能漂移；
  每条结论都同时给出**函数名**与可复核的 `rg` 命令，漂移后按名字定位。
* 判定基准是**代码强制**，不是注释或规范声明。凡发现"文档如此说、代码不如此做"，
  单列在 §8。

## 0. 结论摘要

| 问题 | 结论 |
| --- | --- |
| 1. 按创建者角色选择运行实现 | **无**。运行面三条路径（`channel_instances.py`、`agent/routing.py`、`chat_channel.py`）**一处角色判断都没有**；分支键是行自身的 `scope` / `owner_user_id` |
| 2. 重复运行实现 | **无**。一条 `apply_tenant_instance_runtime` + 一条 `load_tenant_channel_instances`，按行 `scope` 参数化；入站是一条 preflight + 按**行归属**分派的解析器，不是两套流程 |
| 3. 入站访问校验输入 | 存储行的归属（tenant/owner）、存储行的目标 Agent、观测到的发送者三元组；请求体字段**不能**影响判定（有反例测试） |
| 4. 状态来源 | 目录、配置、执行各自**单一**来源；"配置面验收"与"执行面验收"是**两个被显式命名**的不同事实，不是互相竞争的两套状态 |
| 5. 旧个人开关 | `user_private_agent_management`、`personal_memory_write`、`personal_channel_runtime` 在共用写/运行路径上**强制**；`personal_channel_onboarding`、`member_personal_console` **只在旧入口与页面投影上读**，共用写入口**绕过**（见 §5.1、§5.3） |
| 6. 管理员本人连接 | **同一条件**。写入口按**解析后的行 scope** 复判，运行/入站按行 scope 复判；探针验证管理员本人行与成员行给出逐字相同的状态与原因 |

**7.2 实质已满足**（无角色选择实现、无重复实现、入站按存储归属校验），
**7.3 部分满足**：状态来源已合一、`personal_channel_runtime` 的显式关闭不可绕过，
但 `personal_channel_onboarding` 与 `member_personal_console` 的显式关闭**可被新入口绕过**。

## 1. 问题一：运行面是否存在"按创建者角色选择实现"

### 1.1 直接证据：运行面文件不含任何角色判断

```
$ rg -n "is_admin|is_tenant_admin|is_platform_admin|role|admin" channel/channel_instances.py
(none)
$ rg -n "is_admin|is_tenant_admin|is_platform_admin|role" agent/routing.py
(none)
$ rg -n "is_admin|is_tenant_admin|is_platform_admin|\badmin\b" channel/chat_channel.py
(none)
```

连接管理/启动合成/热更新（`channel/channel_instances.py`）、路由决策
（`agent/routing.py`）、入站分发（`channel/chat_channel.py`）三个文件里**零个**角色
标记。这不是"没找到"，是这三个文件根本没有可读的角色事实——它们的输入只有行、
上下文与观测到的发送者。

### 1.2 分支键是行自身，不是调用者

写入侧解析器（`auth/service.py`）里也没有按角色选实现的分支；分支键是**解析后的
`scope`**：

* `create_tenant_channel_instance`（`:8064`）：`if scope == "tenant": _require_public_instance_agent(...) else: _require_personal_instance_agent(...)`
  （`:8131-8141`）——按**行归属**选目标校验规则，两条路都先过同一个
  `_require_channel_write_authorization`（`:8150`）。
* `_enforce_personal_instance_policy`（`:7830`）：只在 `scope == "user"` 时被调用
  （调用点仅两处：`:8221` 创建、`:8675` 启用）。
* `update_tenant_channel_instance` 的 `UPDATE` 语句**不含** `scope` / `owner_user_id`
  两列，见 `evidence/6-3-to-6-6-write-merge-and-scan.md` §6.3 的引证——普通编辑在
  结构上无法转移归属。

运行侧同理，`apply_tenant_instance_runtime`（`channel/channel_instances.py:767`）的
三个分支全部写成 `str(row.get("scope") or "tenant") == "user"`：

* `:803-811`：owner 是否仍为有效成员（`_owner_is_active_member`，`:717`）；
* `:813-823`：目标是否仍可用（`_personal_target_usable`，`:739`）；
* `:825-838`：该类型是否已记录执行验收（`personal_runtime_enabled`，`:1320`）。

启动合成 `load_tenant_channel_instances`（`:904`）用**同一组** gate（`:937-970`）
——启动路径与热路径没有各自的一套规则。

### 1.3 唯一读角色的运行相邻代码，及其对称性

入站分发里确实有一处读角色标记，但它是**功能权限**的等效，且两侧同形，不构成
"按角色选实现"：

```python
# channel/external_identity.py:404-408 与 :465-469（两个入口逐字相同）
if ctx.is_platform_admin or ctx.is_tenant_admin or "chat.use" in permissions:
    return ctx, None
```

它表达的是"管理员自身具备 `chat.use` 这一功能权限"，作用于**发送者**的准入，
不改变"消息落到哪个实例 / 哪个目标"。两条入站路径（本人实例、共享实例上的本人路由）
走的是同一个判定，因此该分支不可能产生"个人一套、租户一套"。

### 1.4 运行面之外的调用者派生分支（登记，非违规）

这些分支出现在**授权点或投影**，最终都归到 `ObjectScope` 的同一个谓词上，登记备查：

| 位置 | 判断 | 与"按角色选实现"的关系 |
| --- | --- | --- |
| `auth/object_scope.py:159 allows_channel_instance` | 私有行只看 `owner == self.user_id`；`is_admin` **只**用于 `scope='tenant'` 的公共行 | 这是那个"唯一判定点"本身；角色对私有行无效 |
| `auth/service.py:8372 _require_channel_instance_in_range` | `ObjectScope(is_admin=is_control)` 后调用上者 | 私有行的非 owner 管理员被拒 |
| `channel/web/admin_handlers.py:986 _is_channel_owner_only` | `not (is_tenant_admin or is_platform_admin)` | 折成一个 `allow_owner` 布尔，交给**同一个**服务函数；服务内部仍按行复判 |
| `channel/web/admin_handlers.py:990 _requested_channel_scope` | 仅 owner 范围者被钉成 `("user", 自己)` | 角色决定**派生**，不由请求体决定；请求体里的 `scope`/`owner_user_id` 不读 |
| `channel/web/web_channel.py:7897 _manages_tenant_channels` / `:7916 _scope_for` | 治理资格决定扫码会话的 `scope` | 显式选 `tenant` 时仍拒绝无资格者（`:7930-7934`），写入时再被服务复判 |
| `channel/web/web_channel.py:885`（Agent 创建 scope 派生） | 非管理员请求 `shared` → 403 | 同上：角色只收窄可提名范围，不选实现 |
| `channel/web/web_channel.py:10120`（管理读范围） | 在 `scope.allows_agent` **之后**才给管理员全集 | 顺序保证非 owner 管理员读不到同事私有对象 |
| `channel/web/static/js/console.js:14377 channelPageScope()` | 键是服务端投影的 `page.scope`（`self`/`tenant`/`platform`），**不是** `is_tenant_admin` | 前端也不按角色分派 |

## 2. 问题二：是否存在两套并行运行实现

**没有。** 逐个运行 seam 说明"一份代码被行 scope 参数化"，而不是"两条代码路径"：

| seam | 事实 |
| --- | --- |
| 启动合成 | 只有 `load_tenant_channel_instances`（`channel/channel_instances.py:904`）一个入口；遍历同一批行，`scope='user'` 的行多过三关（`:937-970`），公共行直接进连接。没有第二个"个人启动"函数 |
| 热更新 | 只有 `apply_tenant_instance_runtime`（`:767`）；`reconcile_instance_runtime`（`:883`）只是它的"永不抛出"包装。`auth/service.py:9469 _reconcile_personal_runtime` **委托**到同一个 `reconcile_instance_runtime`（`:9481-9483`），不是第二个写入者 |
| 连接管理 | 只有一个 runtime 记录表 `_runtime_state`（`:656`）与一个管理器访问器 `_runtime_manager`（`:691` → `app._channel_mgr`）。在 `channel/` 下检索 `personal_runtime` / `personal_channel_state` / `personal_connection` 找不到任何"个人运行态"存储 |
| 入站分发 | 一个入口 `_preflight_external_inbound`（`channel/chat_channel.py:424`），按 `ex.is_personal_instance(instance)`（`external_identity.py:195`，读行的 `scope`）分派到 `_preflight_personal_inbound`（`chat_channel.py:236`）或共享路径；共享路径上的本人路由由 `_serve_personal_route`（`:295`）执行。三个函数是**一条流程的三段**，都调同一个 `_scope_to_member`（`:319`）来钉住成员身份 |
| 入站判定 | 本人实例一个判定点 `resolve_personal_channel_inbound`（`auth/service.py:9767`，docstring 自称 "The single decision point for a member-owned instance's inbound"）；共享实例上的本人路由一个判定点 `resolve_shared_personal_route`（`:9858`）。二者是**同一对象的两种承载**（成员连接 vs 公共连接上的成员路由），共用 `personal_runtime_enabled` / `_same_external_identity` / `_identity_still_resolves` / `_member_active` / `is_private_agent_owner` / `_member_can_use_agent` 同一批谓词 |

**边界说明（诚实区分）**：`_preflight_personal_inbound` 与 `_serve_personal_route`
确实是两个函数。但它们不是"个人实现 vs 租户实现"——共享实例（`scope='tenant'`）上的
成员私有聊天**必须**与公共 Agent 区分，否则会出现 `resolve_shared_personal_route`
docstring 明说的那种替换：把成员显式选的 persona 悄悄换成公共 Agent。该规范的
"不回落"要求本身就要求这条分支存在。两者共享全部底层校验与同一个成员钉住函数。

## 3. 问题三：入站分发的访问校验输入

### 3.1 本人实例（`scope='user'`，成员自己的连接）

判定函数 `IdentityService.resolve_personal_channel_inbound`（`auth/service.py:9767`），
按顺序校验（全部读**存储**）：

| # | 校验 | 行 | 输入来源 |
| --- | --- | --- | --- |
| 1 | 行存在且有 tenant | `:9787-9791` | 存储（`get_tenant_channel_instance_row`） |
| 2 | 行 `scope == 'user'` | `:9794-9798` | **存储**（不是调用者身份） |
| 3 | `personal_runtime_enabled(行的 channel_type)` | `:9799-9805` | 部署开关 + 验收集 |
| 4 | 行 `active` | `:9806` | 存储 |
| 5 | `governance_disabled_at is None` | `:9809` | 存储（治理停机优先于 owner 意愿） |
| 6 | 非群聊 | `:9811-9815` | 消息属性（拒绝方向 fail-closed） |
| 7 | `_instance_credential_active(instance_id)` | `:9820` | 存储 |
| 8 | 该 owner 在本实例上存在已验证路由 `personal_channel_link` | `:9822-9825` | 存储 |
| 9 | `_same_external_identity(route, provider, issuer, subject)` | `:9827-9831` | **观测三元组 vs 存储三元组** |
| 10 | `_identity_still_resolves(...)` | `:9832-9837` | 存储 |
| 11 | `_member_active(owner, tenant)` | `:9839-9841` | 存储 |
| 12 | 行有目标 `agent_id` | `:9842-9844` | 存储 |
| 13 | `is_private_agent_owner(tenant, owner, agent)` | `:9845-9850` | 存储（目标必须仍是**该成员自己的**私有 Agent） |
| 14 | `_member_can_use_agent(...)` | `:9851-9852` | 存储（授权） |

owner 与目标全部来自行：`:9791-9793` 的
`owner_user_id = instance["owner_user_id"]` / `agent_id = instance["agent_id"]`。

### 3.2 共享实例上的本人路由

`resolve_shared_personal_route`（`:9858`）先判 `scope == 'tenant'`（`:9871-9872`），
再由**观测三元组**反查是谁：`personal_route_for_sender`（`:5520`）按
`(tenant, instance, provider, issuer, subject)` SQL 反查（`:5531-5539`），
即"只有这条路由当初被验证过的那个三元组能命中"。命中但不可用 → 拒绝，
**不回落**到公共 Agent（docstring `:9865-9870`）。

### 3.3 请求体字段能否影响判定

* `scope` / `owner_user_id` / `agent_id`：入站判定**完全不读请求体**，读的是行。
* `instance_id`：来自运行期通道对象（`chat_channel.py:54 stamp_instance_context`），
  不是消息字段；且租户**从行**取，不从上下文取——`external_identity.py:160
  instance_tenant_id` 显式以存储为源。反例测试：
  `tests/test_tenant_channel_inbound_anchor.py:203 test_the_anchor_is_read_from_the_store_not_from_the_context`
  在上下文里塞一个**假 tenant**，断言仍按实例行的租户授权；
  `tests/test_tenant_channel_inbound_isolation.py:262 test_the_store_is_the_source_of_truth_for_the_instance_tenant`。
* `bound_agent_id`（路由结果）：仅在**行不存在**时作为 tenant 锚点回落
  （`channel/external_identity.py:410-426`，`:420-424` 明示"伪造的 `bound_agent_id`
  不得把会话移到另一个组织"；测试组见
  `tests/test_tenant_channel_inbound_isolation.py:256-293`，含"缺参保持 Agent 锚点"
  与"存储查询失败不授予锚点"两例）。
  行存在时以行为准：`tests/test_tenant_channel_inbound_anchor.py:188
  test_a_globally_routed_agent_cannot_move_the_message_to_its_tenant`。
* `isgroup` / 观测三元组：是**关于发送者的观测**，方向 fail-closed
  （群聊拒绝、三元组必须与存储一致），不可能被用来放宽。

结论：不存在"请求字段影响入站授权"的路径。

## 4. 问题四：目录 / 配置 / 执行状态的来源

| 状态 | 单一来源 | 反证 |
| --- | --- | --- |
| **目录**（有哪些类型、字段、是否就绪） | `tenant_channel_types()`（`channel/channel_instances.py:1133`）是唯一声明；`personal_channel_types()`（`:1249`）**从它派生**并把 `ready/reason` 附加上去（`:1258-1262`），不维护第二份列表 | 前端不再硬编码类型表：`console.js` 里 `feishu`/`wecom_bot` 只出现在**按类型选扫码 UI 的映射**（`:14551-14552`）与字段回填里，类型清单来自接口载荷 |
| **配置** | `tenant_channel_instances` 表（+ 单条 `credentials` 密文）。个人与公共是**同一张表的 `scope` 列**，不是两张表 | 检索 `personal_runtime` / `personal_channel_state` 在 `channel/` 下无第二配置存储 |
| **执行状态** | `channel/channel_instances.py:656 _runtime_state`（单进程记录）+ `:691 _runtime_manager()`（`app._channel_mgr`）。对外读是 `instance_connection_state`（`:569`），它把 `saved`（存储）与 `connected`（运行态）**分成两个字段**报出（`:572-580`） | 没有任何"个人运行态"表或第二管理器 |

### 4.1 两套"验收"不是竞争来源，是被显式命名的两件事

`auth/capability_matrix.py:297-334` 的 `weixin_scan` 切片 `accepted=True`，但
注释 `:318-330` 明确声明：该验收只覆盖**配置类**（`open={"qr": ACCESS_CONFIG,
"poll": ACCESS_CONFIG}`），**不含执行类**，并点名"vendor connection 由
`personal_runtime_enabled()`（默认关闭）加空的 `PERSONAL_RUNTIME_ACCEPTED_TYPES`
约束，7.8/7.10 未验证"。即：扫码路由的可用性（配置面）与本人连接能否建立
（执行面）是两个被分别声明的维度，不是可以互相矛盾的两套状态。

`auth/policy.py:606 personal_capability_enabled` 是部署开关的**唯一**读取口，
运行面（`channel_instances.py:1315 _personal_runtime_capability_enabled`）与投影都
经它读，因此"投影说能、运行说不能"这种不一致在结构上不可能出现。
`channel_instances.py:1210 _configured_personal_types` / `:1285 _configured_subset`
进一步只允许配置**收窄**，不允许把未验收类型加回来（`:1203-1208`、`:1288-1291`）。

## 5. 问题五：旧个人开关在哪里被写、在哪里被读、关闭能否被绕过

五个开关（`auth/policy.py:554-558` 声明，`:567-571` 默认值；`config.py:285-290`
部署默认）逐个列出**生产代码**的读取点（检索排除 `tests/`、`openspec/`、`*.md`）：

| 开关 | 默认 | 运行/写入路径上的读取点 | 是否在共用路径强制 |
| --- | --- | --- | --- |
| `user_private_agent_management` | True | `auth/service.py:1456`（`bind_private_agent_with_quota`，私有 Agent 创建的共用落点）、`agent/private_agent.py:205` | **是** |
| `personal_memory_write` | True | `agent/memory/personal.py:582`（`PersonalMemoryService` 是个人记忆写入的**唯一**实例化点，`channel/web/web_channel.py:9245`） | **是** |
| `personal_channel_runtime` | **False** | 仅经 `channel/channel_instances.py:1315` → 热更新 `:825-838`、启动合成 `:946-950`、入站 `auth/service.py:9799-9805` | **是**（见 §5.2） |
| `personal_channel_onboarding` | True | 旧个人入口包装 `auth/service.py:9301`（创建）、`:9332`（编辑）、`:9365`（重新启用）、`:9512`（绑定码）；扫码 `channel/weixin_scan_adapter.py:316`；页面投影 `auth/policy.py:582` | **否**（见 §5.1） |
| `member_personal_console` | True | `auth/service.py:5761`（`save_personal_resource_config` 的"保存新配置"）；页面投影 `auth/policy.py:579-585` | **否**（见 §5.3） |

`personal_channel_onboarding` 另有一处**只读展示**用：`auth/service.py:9207
_create_unavailable_reason`（旧入口的能力面板文案）。

### 5.1 已确认的绕过：`personal_channel_onboarding` 的关闭不被新入口尊重

新入口 `/api/tenant/channels`（`channel/web/route_registry.py:160` →
`channel/web/admin_handlers.py:1085 POST`）**不读**该开关，它直接调
`svc.create_tenant_channel_instance(...)`（`:1095`，`allow_owner=_is_channel_owner_only(ctx)`，
`:1114`）；`/api/tenant/channels/<id>/active` 同理（`:1175`、`:1184`）。
服务函数里 `allow_owner=True` 的分支只校验 `personal_channel_ready(ctype)`
（`auth/service.py:8117-8122`），而 `personal_channel_ready`
（`channel/channel_instances.py:1224-1245`）**只读声明集与配置收窄**
（`MULTI_INSTANCE_READY`、`PERSONAL_READY_CHANNEL_TYPES`、`personal_channel_ready_types`），
**不含** `personal_channel_onboarding`。`_enforce_personal_instance_policy`
（`:7830`，创建 `:8221` / 启用 `:8675` 两个调用点）也不读它。

以测试夹具直接调用服务（不启动服务器、不写开发者数据根），开关置 False：

```
$ .venv/bin/python - <<'PY'
from tests.test_tenant_channel_member_access import MemberCreateTests as F
c = F("test_a_member_creates_their_own_connection"); c.setUp()
c.roster_settings["personal_channel_onboarding"] = False
try:
    c.svc.create_personal_channel_instance(...)      # 旧入口包装
    print("legacy create SUCCEEDED")
except Exception as e:
    print("legacy create", getattr(e, "code", ""), getattr(e, "status", ""))
print("shared create   :", c.create_personal(name="SwitchOff Bot").status)  # POST /api/tenant/channels
print("shared re-enable:", c.set_active(...).status)
PY

legacy create    capability_disabled 403      ← 旧入口：关闭生效
shared create    -> 200 OK (row created)      ← 新入口：关闭被绕过
shared re-enable -> 200 OK (row active=1)     ← 新入口：关闭被绕过
```

同一批探针里，`member_personal_console=False` 时共用创建同样成功，且页面投影
`admin.channels.actions.create` 仍为 `True`、`admin.channels.switches` 为
`{'member_personal_console': False, 'personal_channel_onboarding': True}`——即
**投影已经知道总开关是关的，通道创建动作却仍报可用**。

**同一面上自相矛盾**：扫码门读这个开关（`channel/weixin_scan_adapter.py:316`），
手工表单门不读。同一个 `/api/tenant/channels` 页面，两个入口对同一个关闭值给出两个答案。

**影响范围有限但真实**：因为执行面由 `personal_channel_runtime`（默认 False）独立
关闭，绕过的后果**不是**立即产生一条活连接（见 §5.2 的探针），而是**在关闭期内仍可
落库/启用本人连接配置**；等 7.6 把某类型加入 `PERSONAL_RUNTIME_ACCEPTED_TYPES` 后，
这些在"关闭期"写下的行会被启动合成直接接上，而它们从未经过关闭期的复核。
这与 `console-navigation-availability` 的"旧关闭意图不被新入口绕过"场景直接冲突，
也与 `design.md:107` 的"显式关闭值先保留为对应对象范围的部署限制"冲突。

### 5.2 未发现的绕过：`personal_channel_runtime` 的关闭不可被任何入口绕过

同一批探针（默认 `personal_channel_runtime=False`）：

```
create runtime:    {'applied': False, 'pending': True,  'error': 'personal runtime is not enabled for this channel type'}
disable runtime:   {'applied': True,  'pending': False, 'error': ''}
re-enable runtime: {'applied': False, 'pending': True,  'error': 'personal runtime is not enabled for this channel type'}
```

即新入口的 create/enable 都会被"行落库但连接不建立"诚实报出（`pending`），
三个运行 seam 都读同一判定（`channel_instances.py:825-838`、`:946-950`、
`auth/service.py:9799-9805`），且判定函数是"总开关 **且** 按类型验收"（`:1320-1338`）。
这条关闭是三处独立阻塞之一，且**没有**被任何新入口绕过。

### 5.3 同类绕过：`member_personal_console` 未被共用写路径读取

`member_personal_console` 是 `design.md:107` 点名的旧开关之一。它当前只在
`save_personal_resource_config`（`auth/service.py:5761`）与页面投影里读。
私有 Agent 创建的共用落点 `bind_private_agent_with_quota`（`:1416`）只读
`user_private_agent_management`（`:1456`）。探针：

```
$ c.roster_settings["member_personal_console"] = False
member_personal_console=False        -> bind_private_agent_with_quota SUCCEEDED
user_private_agent_management=False  -> bind_private_agent_with_quota capability_disabled 403
```

即：总开关关闭时，私有 Agent 创建照旧成功（投影把 `admin.agents.actions.create`
报成 `False`，接口却不拒）。与 §5.1 同一形状。

判据强度上要诚实：`member_personal_console` 的字面语义偏"控制台页面可用性"，
"对应对象范围"不如 `personal_channel_onboarding` 明确。因此本条是**待裁决**项：
要么把它作为对象范围限制补齐（与 §5.1 同一处修法），要么在迁移报告里显式写明
它只约束页面可用性并移除其运行含义——`design.md:107` 要求的是**二者之一必须被
明确执行**，而不是"投影关、接口开"。

### 5.4 不构成绕过的说明

* `personal_channel_onboarding` 在**禁用/撤销/解绑**方向**故意不读**
  （`auth/service.py:9360-9362` 的注释与实现）：成员永不被困在一条自己关不掉的连接上。
  这是设计选择，不是绕过——它只放宽"关掉已有对象"，不放宽"新建/启用"。
* `user_private_agent_management` 在**幂等重试**之前不读（`:1443-1456` 的顺序）：
  重试已有绑定不算新对象。同样是收窄读写方向，不是绕过。

## 6. 问题六：管理员本人连接是否与成员走同一条件

**是**，判定键是**解析后的行 `scope`**，与调用者是成员还是管理员无关：

* 写入口：管理员点名自己的私有 Agent 时，`_requested_channel_scope`
  （`channel/web/admin_handlers.py:990`）经 `channel_target_scope`
  （`auth/service.py:8347`）派生 `("user", 管理员本人)`；随后
  `create_tenant_channel_instance` 按 `scope == "user"` 走
  `_require_personal_instance_agent`（`:8136-8141`）与
  `_enforce_personal_instance_policy`（`:8221`）——与成员**同一段代码**。
  管理员走的授权分支是 `_is_control`（`allow_owner=False`），成员走的是
  active-membership（`allow_owner=True`），但**对象范围校验完全相同**，
  因为后者按行 scope 复判，而不是按 `allow_owner` 布尔。
* 运行/入站：三处 gate 与 §3.1 的十四项校验读的都是行的 `scope`/`owner_user_id`，
  代码里没有任何"管理员例外"分支（§1.1 的检索已证 `channel_instances.py` 与
  `chat_channel.py` 零角色标记）。

探针（同一夹具，成员行与管理员本人行各一条，默认运行开关关闭）：

```
member row scope/owner: user usr_2e3Z67AQ8tuYl-hp
admin  row scope/owner: user usr_It5E4_DcYUHbVs4l
shipped runtime switch off:
  member {'saved': True, 'connected': False, 'state': 'not_connected', 'reason': 'personal runtime is not enabled for this channel type'}
  admin  {'saved': True, 'connected': False, 'state': 'not_connected', 'reason': 'personal runtime is not enabled for this channel type'}
  inbound member {'allowed': False, 'reason': 'channel_type_not_ready'}
  inbound admin  {'allowed': False, 'reason': 'channel_type_not_ready'}
```

两个角色给出**逐字相同**的 `state`/`reason`（`instance_connection_state`，
`channel/channel_instances.py:569`）与相同的入站判决。另需注意管理员**不能**管理
成员的本人行：`auth/object_scope.py:167-172` 对私有行只认 owner，
`evidence/6-1-shared-channel-surface.md` §3 与
`tests/test_tenant_channel_member_access.py::test_the_administrator_reaches_both_ranges`
已举证。

## 7. 角色键控 / 调用者派生分支总表

| # | 位置 | 键 | 种类 | 是否违规 |
| --- | --- | --- | --- | --- |
| 1 | `channel/channel_instances.py`（全文件） | — | 无角色标记 | 否 |
| 2 | `agent/routing.py`（全文件） | — | 无角色标记 | 否 |
| 3 | `channel/chat_channel.py`（全文件） | — | 无角色标记 | 否 |
| 4 | `channel/external_identity.py:404,465` | `is_platform_admin or is_tenant_admin or chat.use` | 功能权限等效，两侧同形 | 否 |
| 5 | `auth/object_scope.py:159-179` | 行的 `scope`/`owner`；`is_admin` 仅公共行 | 唯一授权谓词 | 否 |
| 6 | `auth/service.py:8372` | `is_control` → `is_admin` | 同上谓词的构造 | 否 |
| 7 | `channel/web/admin_handlers.py:986` | `is_tenant_admin`/`is_platform_admin` | 折算 `allow_owner` | 否（服务内按行复判） |
| 8 | `channel/web/admin_handlers.py:1007-1016` | 同上 | 派生 `(scope, owner)` | 否（请求体不参与） |
| 9 | `channel/web/web_channel.py:7912` | 同上 | 扫码会话 scope | 否（写入再复判） |
| 10 | `channel/web/web_channel.py:885` | 同上 | Agent 创建 scope 派生 | 否（非管理员请求 shared 即 403） |
| 11 | `channel/web/web_channel.py:10120` | `is_tenant_admin` | 管理读范围 | 否（在 `allows_agent` 之后） |
| 12 | `channel/web/static/js/console.js:14377` | 服务端投影 `page.scope` | 前端页面选择 | 否（不读角色） |

**运行实现按角色选择的分支：0 处。**

## 8. 残留工作清单

| # | 缺口 | 最小修复 | 阻塞 |
| --- | --- | --- | --- |
| R1 | `personal_channel_onboarding` 的显式关闭被共用写入口绕过（§5.1）：新建与**重新启用**经 `/api/tenant/channels` 均成功；同一面上的扫码门却读它 | 在 `_enforce_personal_instance_policy`（`auth/service.py:7830`）内加一处"个人 onboarding 开关"检查。该函数**只**在 `scope='user'` 的两条开启写路径上被调用（`:8221` 创建、`:8675` 启用），因此不影响公共连接；语义与旧包装一致：**关闭只拒开启，不拒禁用/撤销** | 无（纯代码，无需凭据） |
| R2 | `member_personal_console` 关闭时共用私有 Agent 创建不被拒（§5.3） | 二选一，须明确落地其一：① 在私有对象创建的共用写路径（`bind_private_agent_with_quota`，`auth/service.py:1416`）加 `require_personal_capability("member_personal_console")`；② 在迁移报告里写明它**只**约束页面可用性并移除其运行含义 | 无代码阻塞；属**待裁决**（需产品口径确认，不涉凭据） |
| R3 | `evidence/7-1-runtime-preflight.md:263` 称 `personal_channel_onboarding` 经 `create_tenant_channel_instance` 的本人分支生效（引 `auth/service.py:8117-8122`）——**与代码不符**：该处读的是 `personal_channel_ready`，它只读声明集与配置收窄（`channel/channel_instances.py:1224-1245`） | 更正该行陈述；§5.1 的探针是反例 | 无 |
| R4 | 真实提供方往返验收（`scope='tenant'` 公共连接与 `scope='user'` 本人连接）：7.4/7.5/7.6 | 属 7.1 已盘点的三处阻塞；本人连接另需同一提交内加 `PERSONAL_RUNTIME_ACCEPTED_TYPES`（`:1276`，当前空）与打开总开关（`:1320-1338`） | **是**——`COW_CREDENTIAL_MASTER_KEY`、真实 `*_app_id`/`*_secret` 与 ≥2 真实账号、装配 `_channel_mgr` 的运行进程 |

R1/R2/R3 是纯代码或纯文档，**不依赖任何真实凭据**；R4 才受凭据阻塞。

## 9. 7.2 / 7.3 已满足的部分

**7.2（合并连接管理、启动合成、热更新、入站分发；按实例实际归属/目标/发送者验证；
不按创建者角色选实现）——实质已满足：**

* 连接管理 / 启动合成 / 热更新 / 入站分发各自**只有一个实现**，按行 `scope`
  参数化（§2 表）；`_reconcile_personal_runtime` 委托同一个 `reconcile_instance_runtime`。
* 运行面三文件**零角色标记**（§1.1）；分支键是 `scope`/`owner_user_id`（§1.2）。
* 入站校验读**行归属 + 行目标 + 观测发送者三元组**，共 14 项（§3.1），
  请求体字段不能影响判定（§3.3，含两个反例测试）。
* 管理员本人连接与成员同条件（§6，探针给出逐字相同的 state/reason）。

**7.3（合并目录/配置/执行状态来源；迁移旧开关为共用功能与真实资源条件；
显式关闭不被新入口绕过；同条件对管理员本人连接一致）——部分满足：**

* **已满足**：目录单一来源（`tenant_channel_types` → `personal_channel_types` 派生）、
  配置单一来源（同一表的 `scope` 列）、执行状态单一来源（`_runtime_state` +
  `_channel_mgr`），"saved" 与 "connected" 被分成两个字段诚实报出（§4）。
* **已满足**：`personal_channel_runtime` 的显式关闭在三处运行 seam 均成立，
  且新入口的 create/enable **不可**绕过（§5.2 探针）；
  `user_private_agent_management`、`personal_memory_write` 在共用写路径强制。
* **已满足**：同条件对管理员本人连接一致（§6）。
* **未满足**：`personal_channel_onboarding`（R1）与 `member_personal_console`（R2）
  的显式关闭可被共用写入口绕过；且前者在同一页面上的扫码门与手工门给出两个答案。
* **未完成**：真实资源条件下的验收记录（R4），受凭据阻塞，与
  `evidence/7-1-runtime-preflight.md` 的结论一致。

按此，7.2 可在 R1–R3 之外独立标记为满足；7.3 建议在 R1 落地、
R2 明确裁决口径之后再行关闭。
