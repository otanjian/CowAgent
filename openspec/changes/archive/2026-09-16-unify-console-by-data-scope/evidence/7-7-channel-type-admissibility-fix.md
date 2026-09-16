# 7.7 渠道类型目录准入性：修复与验证

本文件记录 `tasks.md` 7.7 的**实现与验证**结果。缺陷本身（可达路径、根因、候选修法与权衡）见
`evidence/7-channel-type-admissibility.md`（下称 7.0）；本文件只写**落地了什么、怎么证明它生效、
以及哪些还没验**。

**范围与声明**

* 只改「类型目录准入性」这一条链路：声明、谓词、个人就绪判决、目录判决字段、写入兜底、以及
  控制台类型选择器。**不改**入站、运行期、凭据契约集合、配额与治理。
* 不做真实厂商往返（提供方凭据与运行进程仍缺，见 `evidence/7-1-runtime-preflight.md`）。
  本文件的全部结论都是**进程内**证据：真实服务 + 真实加密 + 真实 HTTP/服务调用。
* `auth/service.py`、`channel/web/web_channel.py`、`channel/web/static/js/console.js` 在本轮写作期间
  仍被其它 agent 并发编辑。下文行号是**读取时刻**快照，所有引用同时给出函数名/常量名。
  本轮所有编辑都是**定点替换**（无整文件重写），并在编辑前后复核了邻接区域。
* 判定基准是**代码强制**。凡「只在注释里成立」的一律不记为已修。

## 0. 结论摘要

| 项 | 结论 |
| --- | --- |
| 缺陷是否关闭 | **是（创建与启用两条路）**。成员面由 `personal_channel_ready` 关闭；公共面由创建分支兜底关闭；控制台不再把不可准入类型列为新连接候选 |
| 根因 | `personal_channel_ready` 的 docstring 自称按「入站可按实例证明发送者」判定，实现只查 `MULTI_INSTANCE_READY`（含义是「可跑多实例」）。**过滤存在，键错了事实** |
| 修法 | 新增共用声明 `INBOUND_IDENTITY_STAMPING_TYPES` + mode-aware 谓词 `inbound_identity_admissible`，并入个人就绪判决（新原因码 `no_inbound_identity`），目录另加**与个人 `ready` 分离**的判决字段 `inbound_admissible`，写入侧在**创建分支**兜底 |
| 兜底位置 | **创建分支**（`create_tenant_channel_instance`），**不放** `_channel_credential_keys`。理由见 §3 |
| 字段契约 | 已保住：不可准入类型的目录条目**不删**，`credential_fields` 逐字段仍在（§4、§5 控制用例） |
| 前端 | 已改 1 行（`console.js` 的 `tenantChannelTypeChoices`），成员面本就无需改动 |
| 变异验证 | 4 个守卫逐个关闭：4 / 1 / 3 / 2 条用例转红，恢复后全绿（§7） |
| 未覆盖 | 不可准入**既存行**的重新启用未被本修法阻断；真实入站现场未验；平台面 `/api/channels` 未一并收窄（§9） |

## 1. 缺陷与可达路径（复核）

与 7.0 一致，此处只列复核后的关键事实：

1. 目录的 8 项全部来自 `MULTI_INSTANCE_READY`（`channel/channel_instances.py:100`）；其中
   **只有 3 个适配器**（feishu / dingtalk / wecom_bot）调用 `stamp_external_identity`。
2. 其余 5 项（telegram / slack / discord / qq / weixin）的入站在
   `channel/chat_channel.py::_preflight_external_inbound` 的 `if not context.get("external_identity")`
   处被判 `UNSUPPORTED_CHANNEL`，**早于**绑定码兑换与 `resolve_actor_for_context`，因此
   `external_identity_attempts` 不落行、控制台待绑定列表看不到任何「谁来敲过门」。
3. 最强可达形态是**公共归属**：`scope='tenant'` 不受个人运行开关约束，会真的 `mgr.restart`，并被
   `instance_connection_state` 报成 `connected`。
4. 根因是**声明与实现不符**，不是缺过滤：`personal_channel_ready` 只查 `MULTI_INSTANCE_READY`。

复跑（本机只读直调，修复前）：

```
$ .venv/bin/python -c "
import channel.channel_instances as ci
print([(t['channel_type'], t['ready'], t['reason']) for t in ci.personal_channel_types()])"
# 修复前：8 项全部 (True, '')
# 修复后：3 项 (True, '')、5 项 (False, 'no_inbound_identity')
```

## 2. 修复：声明、谓词、判决、原因码

### 2.1 唯一真值：`INBOUND_IDENTITY_STAMPING_TYPES`（`channel/channel_instances.py:122-140`）

紧邻 `MULTI_INSTANCE_READY`（`:100`）新增（集合在 `:122`、谓词在 `:129`）：

```python
INBOUND_IDENTITY_STAMPING_TYPES = frozenset({
    const.FEISHU, const.DINGTALK, const.WECOM_BOT,
})

def inbound_identity_admissible(channel_type: str) -> bool:
    from channel.external_identity import is_database_mode
    if not is_database_mode():
        return True
    return _normalize_type((channel_type or "").strip()) in INBOUND_IDENTITY_STAMPING_TYPES
```

* 注释写明「为什么」：拒绝点是 `chat_channel._preflight_external_inbound`，它**先于任何绑定查询**
  拒绝无戳上下文，所以集合外类型**无论怎么配都不可能被搭话**；并写明「加类型必须与适配器打戳同批提交」。
* **写成 mode-aware 形式**（即使 `is_database_mode()` 恒为 `True`）：判据的语义是「database 模式才需要
  证明发送者」，写进谓词才与拒绝点同源；将来若恢复任何非 database 路径，过滤会**自动关闭**，不会把
  在那里无害的类型永久挡在门外。这一点由 `test_the_predicate_is_mode_aware_so_a_non_database_path_is_not_narrowed`
  钉住（含「默认仍是收窄的」反证，防止用例因谓词根本不看模式而假过）。
* 惰性 import `channel.external_identity`，与既有的 `channel` ⇄ `auth` 惰性关系一致；该模块对
  `channel_instances` 无模块级依赖，无环。

### 2.2 并入个人就绪判决（`:1279` `personal_channel_ready`，新原因码在 `:1243`）

```
1243|PERSONAL_NOT_READY_REASONS = frozenset({..., "no_inbound_identity", ...})
1299|    if not inbound_identity_admissible(ctype):
1304|        return False, "no_inbound_identity"
```

* 该判断**放在 `PERSONAL_READY_CHANNEL_TYPES` 检查之前**：否则实际挡住 telegram 的事实会被
  `not_declared`（部署收窄）抢先回答，把「能力缺席」误报成「配置未开放」。
* `PERSONAL_READY_CHANNEL_TYPES` 的取值（仍 `frozenset(MULTI_INSTANCE_READY)`）**保留**，
  但其 docstring 被改写：原来的「the two are the same requirement」正是根因，现在明确写
  「多实例就绪只是两个条件中的**第一个**，第二个由 `personal_channel_ready` 用
  `inbound_identity_admissible` 叠加」。声明与实现自此一致。
* `personal_channel_ready` 的 docstring 同步写明「声明有两半」。
* 自动连带的关闭（无需额外代码）：`auth/service.py::_enforce_personal_instance_policy`（`:7889`）同时被
  **成员创建**与**重新启用**调用，因此成员面两条路一起关；`personal_channel_types()` 的
  `ready` 传成 `false`，成员面前端 `console.js` 既有的 `spec.ready` 收窄**零改动**即生效；
  `public_personal_ingress_ready()`（`:1405`）首句即 `personal_channel_ready`，共享实例上的个人路由
  一并关闭。本轮**未改动**该函数；复核时其它 agent 的
  `require_personal_capability("member_personal_console")` 与
  `require_personal_capability("personal_channel_onboarding")`（`:7911-7912`）仍在，我的
  `personal_channel_ready` 调用在其后（`:7918-7920`），两者叠加而非互相覆盖。

### 2.3 目录判决字段：与个人 `ready` 分离（`:1167` `tenant_channel_types`，字段在 `:1197`）

```python
"inbound_admissible": inbound_identity_admissible(channel_type),
```

* **不删条目**：`tenant_channel_types()` 同时承担「新连接候选」和「已存在行的字段契约」两个职责
  （前端 `tenantChannelType()` 从全量列表查 `credential_fields`）。删条目 → 既存 Slack 行编辑时
  `spec` 为 null → 表单掉字段。docstring 现在把这个双重职责和「为什么必须有独立判决字段」写清楚。
* 为什么不复用个人 `ready`：`ready` 还叠加了部署收窄（`personal_channel_ready_types`）与租户策略
  （`allowed_types`），那些是**成员面**含义，不应用来决定管理员的公共面；两者分离与
  `personal-channel-configuration` spec 的「目录、配置和执行状态分别判定」同源。

### 2.4 控制台选择器（`channel/web/static/js/console.js:14908-14911`）

```js
function tenantChannelTypeChoices() {
    return tenantChannelSelfScope
        ? tenantChannelTypes.filter(spec => spec.ready)
        : tenantChannelTypes.filter(spec => spec.inbound_admissible !== false);
}
```

* 共享面（管理员/公共连接）此前**无过滤**，会列出 5 个「可点但必被拒」的类型——正是
  `tenant-channel-configuration` spec `:69`「界面不得展示提交必被拒绝的类型或目标」要消掉的形状。
* `!== false`：缺字段（老部署载荷）**不**被收窄，避免把「字段尚未下发」误读成「不可准入」。
* 契约查找（`tenantChannelType()`）仍读**全量**列表，因此既存行照常渲染。

## 3. 写入兜底：位置选择与代价（**这是本轮的主要权衡**）

**选择：只放在创建分支**——`auth/service.py::create_tenant_channel_instance`（`:8137`），
在 `allow_owner` 预检之后新增（`:8196-8213`，判定在 `:8210`）：

```python
else:
    from channel.channel_instances import inbound_identity_admissible
    if not inbound_identity_admissible(ctype):
        raise IdentityServiceError(
            "channel type is not available for tenant configuration",
            code="bad_request", status=400)
```

沿用既有错误码/文案（`_channel_credential_keys` 返回 `None` 时同一句），不新增错误码。

**为什么不放进 `_channel_credential_keys`**（7.0 的两个选项中的另一个），三条理由：

1. **它不是创建闸门，而是「该类型能否持有 per-tenant 凭据契约」的判定**，被三处共用：
   创建（经 `_validated_channel_bundle`）、**凭据轮换**（`update_tenant_channel_instance`
   `:8488`）、以及 `set_tenant_channel_policy` 的 `allowed_types` 校验（`:7808`）。
2. **会造出真正的单向门**：既存非戳行的密文将永远不可轮换——凭据过期/吊销后连「改成新值」都做不到，
   而这些行本身仍需要「可修、可停、可删」（task 6.5 的要求）。这是 7.0 已点名的代价。
3. **会外溢到一个与本缺陷无关的面**：某租户策略若已把该类类型写进 `allowed_types`，收紧后**整份策略
   无法再次保存**（同一份列表会被判 `bad_request`），操作者必须同时删掉类型才能改配额之类的字段。
   这是 7.0 未记录、但由代码结构决定的连带回归。

因此本轮回落到「创建分支」：本缺陷的伤害是**新连接不可能工作**；既存行已经存在，其补救路径必须留着。
该决定以注释形式写在两处，防止后来者「顺手收紧」：`create_tenant_channel_instance` 的 `else` 分支
（`:8196-8209`）与 `_channel_credential_keys` 的 docstring（`:7456` 起）。

**代价（已记录，未消除）**：

* 既存不可准入行仍可改名、轮换、停用，**也仍可重新启用**。重新启用不是本修法阻断的动作，
  因此一个**修复前就已存在**的该类行，仍可能被控制台报成 `connected` 而入站恒拒。
  本轮不阻断它的理由：`tasks.md` 7.7 把兜底范围界定在「创建」，且阻断启用会与「无效实例仍可修可停」
  的既有取向冲突；若要一并关掉，最小落点是 `set_tenant_channel_instance_active`（`:8698`）中公共归属的
  重新启用分支——即 `if row["scope"] == "user":` 块（`:8751`）之后、共用 `if active:` 块（`:8781`）
  之前，按 `row["scope"] == "tenant"` 判定，属一次性行为变更，应由变更负责人决定。
* 成员面（`scope='user'`）**已经**被阻断：`_enforce_personal_instance_policy` 在创建与启用两处都调用，
  所以成员侧的启用是被关掉的，公共侧的启用不是。这个不对称是本轮唯一的行为缺口，登记在 §9。

## 4. 保住的字段契约

| 面 | 事实 | 证据 |
| --- | --- | --- |
| 服务端目录 | `tenant_channel_types()` 仍返回 8 项；不可准入类型携带 `inbound_admissible=false` **且** `credential_fields` 完整（slack = `slack_bot_token`/`slack_app_token`，含 label/secret/required） | `tests/test_channel_type_admissibility.py::CatalogueTests` |
| 个人目录 | `personal_channel_types()` 仍返回 8 项；5 项 `ready=false, reason="no_inbound_identity"`，凭证字段仍在 | 同上 |
| 前端契约查找 | `tenantChannelType('slack')` 仍解析出字段；被收窄的只是**候选**（`tenantChannelTypeChoices`） | `tests/test_channel_type_admissibility_frontend.cjs` |
| 必填集声明 | `CREDENTIAL_KEYS` / `REQUIRED_CREDENTIAL_KEYS` **未改动**，故 `test_channel_instances_partition.py`、`test_tenant_channel_required_credentials.py` 的完整性断言不受影响 | §6 运行结果 |

## 5. 控制用例（防「一律拒绝」通过）

| 控制用例 | 断言 | 位置 |
| --- | --- | --- |
| 3 个打戳类型仍可创建 | 循环创建 feishu/dingtalk/wecom_bot 全部成功，且库里正好这 3 行 | `test_every_unstamped_type_is_refused_and_every_stamping_type_is_not` |
| 3 个打戳类型仍 `ready` | `personal_channel_ready(t) == (True, "")` | `test_the_personal_verdict_names_the_real_blocker` |
| 成员写入闸门不拒一切 | 同一个闸门对 `slack` 抬 `channel_type_not_ready`，对 `feishu` **不**抛错 | `MemberWriteGateTests` |
| 目录判决不是常量 false | 3 个打戳类型条目 `inbound_admissible is True` | `CatalogueTests` |
| 谓词在非 database 模式下不误伤 | `is_database_mode=False` 时 slack 准入；默认仍不准入 | `DeclarationTests` |
| 页面候选不因收窄而消失为空 | 共享面仍给出打戳类型；缺字段载荷不被收窄 | `test_channel_type_admissibility_frontend.cjs` |

## 6. 测试与运行结果

### 6.1 新增/改动的测试

| 文件 | 改动 |
| --- | --- |
| `tests/test_channel_type_admissibility.py` | **新增**（13 例）：声明、谓词、mode-aware、原因码、目录判决、字段契约、成员闸门、创建兜底、既存行仍可轮换 |
| `tests/test_channel_type_admissibility_frontend.cjs` | **新增**（4 例）：共享面候选收窄、自有面仍按 `ready` 收窄、契约查找存活、缺字段不误收窄 |
| `tests/test_personal_channel_console.py:499-517` | 定点替换：`telegram` → `dingtalk`（2 处），保持原意「配置只能收窄、不能放宽」 |
| `tests/test_personal_channel_console.py:1276-1298` | **同文件第二处定点替换**（原任务只点了 499-514）：该用例（`test_the_type_list_is_the_declarations_narrowed_by_the_tenant`）同样用 `telegram` 作为「租户策略收窄后仍就绪」的样例，收窄后 `set(ready)` 会变空，故同样改为 `const.DINGTALK` |
| `tests/test_tenant_channel_http.py:170-203` | 定点改造 `test_every_offered_type_is_actually_creatable`：条目仍必须携带字段契约；只对 `inbound_admissible is not False` 的类型尝试创建，并加「至少尝试成功过 feishu」的控制断言，防止用例因空循环假过 |

> 说明：后两处超出 `tasks.md` 7.7 点名的行范围，但**都是同一次行为变更的必然后果**，且都是定点替换
> （无整文件重写），原意均保留。若不同步改，`tests/test_tenant_channel_http.py` 会红——它的断言正是
> 「目录里出现的类型都必须可创建」，而本修法刻意让「非候选」的条目留在目录里（字段契约）。

### 6.2 命令与结果

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
    tests/test_channel_type_admissibility.py \
    tests/test_personal_channel_console.py \
    tests/test_tenant_channel_member_access.py \
    tests/test_personal_instance_policy.py \
    tests/test_tenant_channel_instances_service.py \
    tests/test_capability_matrix.py -q -p no:randomly
→ 233 passed, 5 subtests passed        # 含新增 13 例
```

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_personal_console_acceptance.py \
    tests/test_tenant_channel_http.py tests/test_tenant_channel_required_credentials.py \
    tests/test_channel_instances_partition.py -q -p no:randomly
→ 99 passed                    # 见 §8.1（写作初期此处曾有 4 例瞬态失败）
```

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_tenant_channel_http.py \
    tests/test_tenant_channel_required_credentials.py tests/test_channel_instances_partition.py \
    -q -p no:randomly
→ 76 passed
```

```
$ node --test tests/test_channel_type_admissibility_frontend.cjs \
    tests/test_personal_console_frontend.cjs tests/test_channel_workbench_frontend.cjs \
    tests/test_tenant_channel_frontend.cjs tests/test_tenant_channel_card_frontend.cjs \
    tests/test_channels_page_header_frontend.cjs tests/test_channel_scope_nav_frontend.cjs \
    tests/test_console_workspace_frontend.cjs
→ 157 passed, 0 failed        # 单次运行合并汇总，含新增 4 例
```

## 7. 变异验证（守卫逐个关闭 → 转红 → 恢复 → 转绿）

| # | 守卫 | 变异方式 | 结果 |
| --- | --- | --- | --- |
| G1 | `personal_channel_ready` 的准入检查（`channel_instances.py:1299`） | `if not inbound_identity_admissible(ctype):` → `if False:` | **4 例转红**：`CatalogueTests` 3 例（个人判决/原因码/个人路由）+ `MemberWriteGateTests` 1 例。恢复后 13/13 绿 |
| G2 | 目录判决字段（`:1197`） | 值改为常量 `True` | **1 例转红**：`CatalogueTests::test_the_tenant_catalogue_keeps_the_field_contract_of_an_inadmissible_type`；另经 §6.2 的 HTTP 用例（它会把 slack 当候选去创建 → 被兜底拒绝）。恢复后绿 |
| G3 | 创建分支兜底（`auth/service.py:8210`） | `if not inbound_identity_admissible(ctype):` → `if False:` | **3 例转红**：创建兜底 3 例（含「不落行/不落凭据/不落审计」与既存行轮换对照）。恢复后绿 |
| G4 | 控制台候选收窄（`console.js:14910`） | `.filter(spec => spec.inbound_admissible !== false)` → `.filter(spec => true)` | **2 例转红**：共享面候选、契约查找对照组。恢复后 4/4 绿 |

（G2 的常量变异只让 1 例 Python 用例转红：见上，另有 HTTP 用例兜住——它的循环依赖判决字段。）

## 8. 判为**非本改动**的失败（已证明）

### 8.1 Python：写作初期见过的 4 例失败，最终复核已不复现

写作初期 `tests/test_personal_console_acceptance.py` 有 4 例失败（`/api/personal/resources` 返回 404
而非 403/200）。当时判为非本改动的证据两条：

1. 结构证明：本轮对 `channel/web/route_registry.py` 零改动（该文件正被其它 agent 修改：旧个人资源读写
   端点被退役），而失败断言的是该地址的状态码；本改动只影响渠道**类型准入**，不可能移除一条路由。
2. 反证实验：把本轮两个 Python 守卫（G1 与 G3）同时置为关闭后重跑这 4 例，**仍然全红**
   （`4 failed, 5 passed`）。

**最终复核（写作后）该 4 例已全绿**——同一组命令给出 `99 passed`：

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_personal_console_acceptance.py \
    tests/test_tenant_channel_http.py tests/test_tenant_channel_required_credentials.py \
    tests/test_channel_instances_partition.py -q -p no:randomly
→ 99 passed
```

即那 4 例失败属于**并发编辑期间的瞬态**（路由退役由其它 agent 完成、我方读到中间态），不是本改动留下
的残留，也无需本 change 处置。

### 8.2 前端：4 个 `.cjs` 文件仍红，与本改动无关

`test_appearance_browser`(1)、`test_console_i18n_parity`(2)、`test_session_history_frontend`(36)、
`test_sidebar_account_frontend`(5)——`tests=1/5/36/55, fail=1/2/36/5`。判为非本改动，证据两条：

1. 反证实验：把 `console.js` 的候选收窄改回原样（即 G4 变异反向）后重跑，失败数**逐字相同**
   （1/2/36/5）；本轮复核带收窄重跑，同样是 1/2/36/5。
2. 结构证明：这 4 个文件**都不引用**被收窄的 `tenantChannelTypeChoices`（`rg` 无命中），其中两个只在
   文本层面读取 `console.js`；失败主题分别是外观、i18n 快照、会话搜索同步、登录/偏好，与渠道类型准入
   无交集。i18n 快照的差异来自其它 agent 已改的 `tests/fixtures/console_i18n_snapshot.json`。

## 9. 未覆盖（含原因）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 既存不可准入行的**重新启用** | **未阻断**（已知缺口） | 本轮把兜底界定在创建（§3）。公共归属的启用路径 `set_tenant_channel_instance_active`（`:8698`）没有等价闸门，故修复前落库的该类行仍可被启用并报 `connected`。最小落点已点明（`:8751` 的 `scope=="user"` 块之后、`:8781` 的 `if active:` 之前，按 `scope=="tenant"` 判定），是否要一并关掉属变更负责人的取舍 |
| 5 类提供方入站的**真实现场**拒绝 | **未覆盖** | 约束禁止发消息；结论仍是「适配器全目录无 `external_identity` 命中」+「拒绝点不依赖 channel_type」的代码走查，与 7.0/7.1 同一证据，本轮未新增现场 |
| 公共不可准入实例**真实**被拉起后第一条消息被拒 | **未覆盖** | 无 `COW_CREDENTIAL_MASTER_KEY` 与真实凭据；本轮只证到「创建被拒、行不落库」，未证「不存在时也不会重启」（因为已不可能创建） |
| 平台面 `/api/channels`（`CHANNEL_DEFS`，`web_channel.py`）是否也需一并收窄 | **未覆盖** | 与 7.0 §8 同一未裁定项；该面是平台域、展示与守卫不同，本轮未改。它同样列出全部类型，但入站同被拒——若需要收窄，应作为同一谓词的第二个调用点 |
| dingtalk 最小必填集、wecom_bot 早退分支 | **未覆盖** | 7.1 §7 已登记，与本修法无关；本修法把 dingtalk/wecom_bot 判为**准入**（它们确实打戳），若其最小集不足，表现是 `UNBOUND`/启动失败而非本缺陷的 `UNSUPPORTED_CHANNEL` |
| 并发编辑下的行号稳定性 | **部分失效风险** | 写作期间 `auth/service.py`、`console.js`、`web_channel.py` 被并发编辑（`auth/service.py` 在本次写作中就整体位移 19→另一组偏移）。所有引用同时给出函数名/常量名，复核时请按名定位。§11 是收尾时刻的**逐名复核**快照 |
| 前端收窄的**浏览器实测** | **未覆盖** | 本文件只做静态/契约级验证（读源码 + 纯函数驱动）。真实渲染需浏览器会话，且 `console.js` 为并发编辑文件，本轮不在其上另起浏览器验证 |

## 10. 可复跑证据

```bash
# 1) 判决与原因码（不启服务、不写库）
.venv/bin/python -c "
import channel.channel_instances as ci
print(sorted(ci.INBOUND_IDENTITY_STAMPING_TYPES))
print({t: ci.inbound_identity_admissible(t) for t in sorted(ci.MULTI_INSTANCE_READY)})
print([(t['channel_type'], t['ready'], t['reason']) for t in ci.personal_channel_types()])
print([(t['channel_type'], t['inbound_admissible'], len(t['credential_fields'])) for t in ci.tenant_channel_types()])"
# → 3 项打戳集；5 项 False；5 项 (False,'no_inbound_identity')；8 项条目且字段数不变

# 2) 谓词是 mode-aware 形式（非 database 时自动关闭）
.venv/bin/python -c "
from unittest.mock import patch
import channel.channel_instances as ci
with patch('channel.external_identity.is_database_mode', lambda: False):
    print(ci.inbound_identity_admissible('slack'), ci.personal_channel_ready('slack'))"
# → True ('slack' 恢复准入，即过滤在非 database 路径下自动关闭)

# 3) 控制台候选收窄 + 契约查找存活
.venv/bin/python -c "
import re, pathlib
src = pathlib.Path('channel/web/static/js/console.js').read_text()
i = src.index('function tenantChannelTypeChoices')
print(src[i:i+240])"
rg -n "inbound_admissible" channel/web/static/js/console.js

# 4) 变异验证（逐个守卫，见 §7）
#    手动把 G1/G2/G3/G4 四处改回「无条件放行」，重跑 §6.1 的新用例应转红。

# 5) 本轮测试命令
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_channel_type_admissibility.py \
  tests/test_personal_channel_console.py tests/test_tenant_channel_member_access.py \
  tests/test_personal_instance_policy.py tests/test_tenant_channel_instances_service.py \
  tests/test_capability_matrix.py -q -p no:randomly
node --test tests/test_channel_type_admissibility_frontend.cjs
```

（以上均已在 2026-09-16 本轮实际执行。）

## 11. 并发编辑核查（收尾时刻逐名复核）

本轮只做**定点替换**（无整文件重写），编辑前后都重读了目标区域。收尾时按**符号名**（不按行号）复核
其它 agent 的产物是否仍在：

| 符号 | 归属文件 | 结果 |
| --- | --- | --- |
| `_require_skill_write_scope` / `_resolved_skill` / `_annotate_skill_actions` | `channel/web/web_channel.py` | **仍在**（`rg` 命中 9 处），本轮未编辑该文件 |
| `memoryEntryEditable` | `channel/web/static/js/console.js` | **仍在**（命中 3 处）；`console.js` 通过 `node --check` 语法门 |
| `require_personal_capability("member_personal_console")` / `("personal_channel_onboarding")` | `auth/service.py::_enforce_personal_instance_policy`（`:7889`） | **仍在** `:7911-7912`；我的 `personal_channel_ready` 调用在其后 `:7918-7920`，两者**叠加**不覆盖 |

收尾时的行号快照（供复核，仍建议按名定位）：

| 位置 | 行号 |
| --- | --- |
| `INBOUND_IDENTITY_STAMPING_TYPES` / `inbound_identity_admissible` | `channel/channel_instances.py:122` / `:129` |
| `PERSONAL_NOT_READY_REASONS` / `no_inbound_identity` | `:1243` / `:1249` |
| `personal_channel_ready` 内的准入检查 / 返回 | `:1299` / `:1304` |
| `tenant_channel_types` 的判决字段 | `:1197` |
| `public_personal_ingress_ready` | `:1405` |
| 创建分支兜底 / 判定 | `auth/service.py:8196-8213` / `:8210` |
| `_channel_credential_keys` docstring（说明为何不放闸门） | `:7456` 起 |
| `console.js` 候选收窄 | `:14908-14911`（过滤在 `:14910`） |

复核命令：

```bash
rg -n "_require_skill_write_scope|_resolved_skill|_annotate_skill_actions" channel/web/web_channel.py
rg -n "memoryEntryEditable" channel/web/static/js/console.js
rg -n "def _enforce_personal_instance_policy" -A 30 auth/service.py
node --check channel/web/static/js/console.js
```

**结论：本轮没有踩踏其它 agent 的改动。**

## 12. 7.8 续修：既存不适格公共行的重新启用已阻断（追加）

本节收口 §9 第一行登记的缺口（`tasks.md` 7.8）。只关这一条，不改 §2 已落地的任何判定。

**缺口**：§3 把写入兜底放在创建分支，代价是修复前已落库的不可准入**公共**行（`scope='tenant'`）
仍可被管理员重新启用——一旦启用就会被真的拉起并被控制台报成 `connected`，而入站仍在身份戳
前置检查处恒拒。本人侧（`scope='user'`）在 §2.2 已由 `_enforce_personal_instance_policy` 一并关闭，
故只剩公共侧这一条。

**可达性复核（确认只有这一条重新启用路）**：全仓只有一处把 `active` 置 1 的写入——
`set_tenant_channel_instance_active` 的 `UPDATE ... SET active=?`（`auth/service.py:8821`，
`rg "UPDATE tenant_channel_instances SET active"` 的全部生产命中）；成员包装
`set_personal_channel_instance_active`（`:9455`）与管理员处理器
`channel/web/admin_handlers.py:1175` 都汇入同一函数。另一处
`UPDATE ... SET active=0`（`:9526`，`revoke_personal_channel_credentials`）只关闭。
**故本修法即覆盖全部重新启用路径，无需第二个调用点。**

**插入点与守卫**（`auth/service.py:8780`，插入时刻快照；按函数名/谓词名定位）：

```python
            if active and row["scope"] != "user":
                # The public counterpart of the readiness re-decision above, and
                # the only door the create-time backstop cannot see: a row
                # written before that backstop existed is still switchable here.
                # Left open, such a row would start and be reported "connected"
                # while every inbound is refused at the identity-stamp gate
                # before any binding lookup — the 7.7 defect, for an old row.
                # Refused on the *enable* transition only, deliberately: an
                # operator must always be able to stop a running instance, and
                # disabling, repairing and rotating never reach this branch.
                from channel.channel_instances import inbound_identity_admissible

                if not inbound_identity_admissible(str(row["channel_type"] or "")):
                    raise IdentityServiceError(
                        "channel type is not available for tenant configuration",
                        code="channel_type_not_ready", status=403)
```

* 位置在 `if row["scope"] == "user":` 块（`:8749`）之后、共用 `if active:` 块（`:8794`）之前。
* 判据复用**同一**谓词 `inbound_identity_admissible`（§2.1），不新增第二真值。
* 作用域 `row["scope"] != "user"`：与函数自身对「公共」的既有判定（范围检查、应用冲突）
  同一把键；成员行不进此分支，其重新启用仍由 `_enforce_personal_instance_policy` 判定，
  两层**叠加不覆盖**。
* 错误形态沿用既有 `channel_type_not_ready` / 403 —— 与成员侧重新启用被拒是同一码，同一条
  事实在两个归属上作答一致；文案沿用创建分支同一句。**不静默 no-op**。

**启用/停用不对称（刻意，且最要紧）**：只有 `active=True` 的迁移被拒。若连停用也被挡，
一个正在运行的实例将无法关闭——比原缺陷更糟。删除与修复同样不经此分支：服务层本无
`DELETE FROM tenant_channel_instances`（`rg` 无生产命中），修复（改名、轮换）走
`update_tenant_channel_instance`，§5 的「既存行仍可轮换」用例继续为绿。

**控制用例**（`tests/test_channel_type_admissibility.py::ReEnableBackstopTests`，新增 3 例，
5 个不适格类型逐一取值）：

| 用例 | 断言（可观察效果） |
| --- | --- |
| `..._can_still_be_stopped` | 停用成功、`active` 转 0、新增一条 `channel.instance.disable` 审计 |
| `..._cannot_be_re_enabled` | 先停用，再启用被拒（`channel_type_not_ready`/403），且 `active` 仍 0、`version` 未变、`channel.instance.enable` 审计为 0 |
| `..._admissible_public_row_still_re_enables` | feishu 行停用后可再次启用（`active` 回 1，落一条 enable 审计）——反「一律拒绝」 |

**变异验证**：把守卫改为 `if False and active and row["scope"] != "user":`（失效）后重跑本文件 →
**1 failed, 15 passed**（仅 `..._cannot_be_re_enabled` 转红；两个控制用例**不受影响**，正证明
控制用例不依赖守卫）；恢复后 **16 passed**（原 13 例 + 新增 3 例）。

**测试命令与结果**：

```
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_channel_type_admissibility.py \
    tests/test_personal_channel_console.py tests/test_tenant_channel_member_access.py \
    tests/test_personal_instance_policy.py tests/test_tenant_channel_instances_service.py \
    tests/test_tenant_channel_http.py -q -p no:randomly
→ 258 passed, 5 subtests passed

$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_capability_matrix.py \
    tests/test_object_scope.py -q -p no:randomly
→ 40 passed, 3 subtests passed
```

**并发编辑核查（编辑后按名复核，全部存活）**：`_require_skill_write_scope` / `_resolved_skill` /
`_annotate_skill_actions`（`channel/web/web_channel.py`）、`memoryEntryEditable`
（`channel/web/static/js/console.js`）、`require_personal_capability("member_personal_console")` /
`("personal_channel_onboarding")`（`auth/service.py:7911-7912`）、`INBOUND_IDENTITY_STAMPING_TYPES` /
`inbound_identity_admissible`（`channel/channel_instances.py:122`/`:129`）均在。本轮只做**一次定点
替换 + 一次定点追加测试类**，无整文件重写。

**未验证**：

* 真实运行进程下的「已连接 → 停用 → 再启用被拒」现场未走（仍缺 `COW_CREDENTIAL_MASTER_KEY` 与
  真实提供方凭据，同 §9）；本节结论均为进程内证据（真实服务 + 真实加密 + 真实库）。
* 若存在绕过服务层的 `active` 直改（SQL 手工/外部导入），守卫不覆盖；服务层已核无此写入。
* 平台面 `/api/channels` 收窄仍为 §9 未裁定项，本节未动。
