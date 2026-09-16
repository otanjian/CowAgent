# 任务 4.8 证据：默认修改的作用范围（未锚定新会话、旧会话、显式目标、后台任务、渠道绑定、拒绝与来源）

对应 `openspec/changes/unify-console-by-data-scope/tasks.md` 的任务 4.8：

> 验证默认修改只影响未锚定新会话，旧会话、显式目标、后台任务和渠道绑定不改变；无合法候选拒绝且绝不回落他人私有对象。通过后登记智能体/默认切片证据。

本任务是**验证**任务：实现（任务 4.4/4.6 的 `set_user_default_agent` / `initialize_member_default_agent` / `resolve_default_agent`、控制台 `set_user_default` 动作、渠道入站与后台任务的既有实现）已在位，本任务只补测试与证据。

## 0. 结论一览

| # | 性质 | 钉住它的测试 | 结果 |
| --- | --- | --- | --- |
| A | 默认修改只影响未锚定新会话 | `test_default_change_session_anchor.py::test_a_default_change_moves_the_next_agent_less_session`、`::test_a_later_agent_less_session_uses_the_new_default` | 通过 |
| B | 旧会话不改变（含重启） | `test_default_change_session_anchor.py::test_an_existing_session_keeps_its_agent_across_a_default_change`、`::test_the_anchor_survives_a_restart` | 通过 |
| C | 显式目标不改变 | `test_default_change_session_anchor.py::test_an_explicit_target_is_not_replaced_by_the_default`、`::test_an_explicit_target_wins_even_for_a_fallback_anchor` | 通过 |
| D | 后台任务不改变 | `test_scheduler_identity_revalidation.py::test_a_default_change_does_not_re_point_an_existing_task` | 通过 |
| E | 渠道绑定不改变（个人实例 / 已绑定共享实例） | `test_personal_channel_inbound.py::test_changing_a_members_default_does_not_re_route_their_personal_instance`、`::test_a_bound_instance_keeps_its_target_when_the_tenant_default_changes` | 通过 |
| F | 无合法候选拒绝（含无主体调用） | `test_user_default_initialisation.py::test_nothing_usable_reports_no_target_and_no_source`、`test_default_agent_fail_closed.py::test_a_subject_less_caller_is_never_handed_a_private_fallback`、`::test_an_agent_less_tenant_is_refused_rather_than_defaulted` | 通过 |
| G | 绝不回落他人私有对象 | `test_default_agent_fail_closed.py::test_a_tenant_default_that_points_at_a_private_agent_is_refused`、`test_user_default_initialisation.py::test_a_private_only_tenant_never_anchors_a_colleague`、`::test_a_stale_preference_is_filtered_out_of_the_candidates` | 通过（实现已修，见 §7） |
| H | 来源标注诚实 | `test_user_default_initialisation.py::test_every_reported_source_agrees_with_the_state_it_names`、`::test_the_fallback_pools_are_never_reported_as_a_choice`、`::test_an_unusable_preference_is_reported_as_the_fallback_it_fell_to`、`::test_the_source_honesty_check_is_not_vacuous` | 通过 |

**未通过/缺口：无。** 详见 §8。

## 1. 性质 A/B/C：会话锚点不随偏好移动

新增 `tests/test_default_change_session_anchor.py`（6 个用例）。锚点在实现里不是一个「记得去更新的指针」，而是**行落在哪个 Agent 的会话库里**（`<workspace>/memory/long-term/index.db`），所以测试走真实 WSGI 应用：`POST /api/message` 的路径由 `_authorize_chat_session` 认领 (Agent, session) 键并在**该 Agent 的库**里写下第一轮消息。偏好改变无法把行从一个文件搬到另一个文件——这正是该性质值得用「文件归属」而不是「指针值」来钉的原因。

- A：`anchor()` 前态是兜底 `{"agent_id": "shared-agent", "source": "shared"}`（没人选过），`choose("alice-first")` 之后是 `{"agent_id": "alice-first", "source": "user"}`，且无显式目标的 `s-new` 真的落在 `alice-first` 的库里。
- B：`s-old` 在 `alice-first` 为偏好时创建，改偏好为 `alice-second` 后：`s-old` 的 owner 仍在 `alice-first` 库、`alice-second` 库对 `s-old` 无 owner、`GET /api/history?agent_id=alice-first` 仍读得到原文、用新默认读返回空、用**新默认**改名 `404 not found`。另有 `test_the_anchor_survives_a_restart`：清空会话库缓存并重开 `identity.db` 后，偏好仍是 `alice-second`、`s-old` 仍属 `alice-first`。
- C：`alice-third` 不是任何意义上的默认（非租户默认、非成员偏好、非兜底），显式选它后会话落在它的库里、`alice-first` 库里没有该会话，且偏好没有被改写成 `alice-third`；另一种形态（成员完全没有偏好、锚点是 `shared` 兜底时选显式目标）同样不被兜底接管。

命令与结果：

```
$ .venv/bin/python -m pytest tests/test_default_change_session_anchor.py -q -p no:randomly
......                                                                   [100%]
6 passed in 2.95s
```

## 2. 性质 D：后台任务不改变

先查清绑定方式（任务要求先弄清再断言）：任务不是「每次触发时再按偏好解析」的。

- `agent/tools/scheduler/identity.py::owner_snapshot` 在**创建时**把创建者与**目标 Agent** 一并快照进任务（`owner.agent_id`），
- `identity.py::execution_identity(task, agent_id)` 是触发时唯一的身份裁决点，Agent 来自任务的快照/调度器实例，**不读偏好**；
- `identity.py::revalidate_owner(task)` 的授权复核是对**任务自己那个 Agent** 的 `agent.use` 提问（`agent:{owner.agent_id}`）。

新增 `tests/test_scheduler_identity_revalidation.py::test_a_default_change_does_not_re_point_an_existing_task`：

1. 在已锚定 `shared-agent` 的会话里快照出任务（`owner.agent_id == "shared-agent"`）；
2. 把成员默认改到 `member-private`（`resolved_default_agent_id` 断言偏好**确实**变了，否则该用例什么也没证明）；
3. 断言 `execution_identity(task, "shared-agent").agent_id == "shared-agent"`、且身份仍是该成员（不因偏好而换成别人），并显式断言触发 Agent **不等于** `resolved_default_agent_id`——任务存放在 Agent 自己的任务库里，调度器实例也绑定该 Agent，偏好从未参与；
4. 撤掉**任务那个 Agent** 的 `agent.use` 后触发被跳过并记 `AGENT_DENIED`，尽管成员的新默认完全可用——授权是对任务自己的 Agent 提问，不是对偏好；
5. 落库的任务字段没有被「重新指向」：`owner.agent_id` 仍是 `shared-agent`、动作里没有被塞进 `agent_id`、`enabled` 仍为 `True`（可再触发）；
6. 反向可证伪：用 spy 断言整个触发过程**从未调用** `resolve_default_agent`——若哪天改成按偏好解析，`consulted` 会非空、第 3 步会变成 `member-private`。

```
$ .venv/bin/python -m pytest tests/test_scheduler_identity_revalidation.py -q -p no:randomly
.............                                                            [100%]
13 passed, 1 warning in 3.27s
```

## 3. 性质 E：渠道绑定不改变

渠道绑定在两条路径上都是**显式值**，不是偏好：个人实例的目标存在实例行的 `agent_id`（`resolve_personal_channel_inbound` 读它），租户自有实例在加载时由 `channel/channel_instances.py::load_tenant_channel_instances` 把行里的 `agent_id` 作为 `bound_agent_id` 传入，`chat_channel.py` 的 `_preflight_external_inbound` 用它**抢先**于租户默认（`context.get("bound_agent_id") or resolved_public_default_agent_id(...)`）。

在既有文件 `tests/test_personal_channel_inbound.py` 新增两个用例：

- `test_changing_a_members_default_does_not_re_route_their_personal_instance`：alice 手工把机器人绑到 `agent-alice-private`，随后把**本人默认**改到 `agent-alice-second`（断言偏好确实改了）。下一条入站消息仍以 `agent-alice-private` 运行（`runtime_identity.agent_id`），实例行目标未被改写（所以重启后还绑同一个）。并且用 spy 断言这条路径**根本没有询问偏好**（`consulted == []`）——个人路由的 Agent 只能来自它自己的目标。
- `test_a_bound_instance_keeps_its_target_when_the_tenant_default_changes`：把**租户默认**改到 `agent-shared-second`（断言租户条目确实改了），已绑定 `agent-shared` 的共享实例入站仍然路由到 `agent-shared`，且 spy 证明此时**完全没读**租户默认。反向同证：同一租户里**没有绑定**的实例（行目标为空）确实跟着新默认走 → 说明上面两条断言不是因为「apply_instance 是空操作」而侥幸通过；绑定被保留才有意义。

```
$ .venv/bin/python -m pytest tests/test_personal_channel_inbound.py -q -p no:randomly
.........................................                                [100%]
41 passed in 18.61s
```

## 4. 性质 F：无合法候选拒绝（含无主体调用）

- 无可用候选：`resolve_default_agent` 返回 `{"agent_id": None, "source": None}`（`test_nothing_usable_reports_no_target_and_no_source`）；这是**被报告出来的结果**，不是「碰巧看起来是空的兜底」——`resolved_default_agent_id` 单值视图同步为 `None`，Web 侧 `_require_session_owner` 因此对无候选租户直接 `403`（`test_default_agent_fail_closed.py::test_an_agent_less_tenant_is_refused_rather_than_defaulted`），而可用租户不被误拒（`::test_a_tenant_with_a_usable_agent_is_not_refused`）。
- 无主体调用：`user_id=None` 时**没有**私有池可回落，所以一个只剩私有 Agent 的租户必须被拒绝，而不是把最小 id 的私有 Agent 交出去（`test_a_subject_less_caller_is_never_handed_a_private_fallback`，断言整个字典等于 `{"agent_id": None, "source": None}`）。

## 5. 性质 G：绝不回落他人私有对象

- 直接构造「租户里只剩另一个成员的私有 Agent」：`test_a_private_only_tenant_never_anchors_a_colleague`——bob 得到 `None`，且断言锚点不等于 alice 的私有 Agent。
- 租户默认也不行：`test_a_tenant_default_that_points_at_a_private_agent_is_refused` 用**产品路径**造出非法行（先 `appoint_tenant_default_agent` 指向当时无主的 Agent，再 `bind_agent` 补上 owner——`bind_agent` 只修缺失、不清空；旧构建或重新署名回填同样会留下这一行），断言普通成员、**该 Agent 的 owner 本人**、以及无主体调用者三者都不被送进该私有 Agent，而是落到剩下的共享候选 `shared`。
- 成员偏好行写成别人的私有 Agent 时同样拒绝，且不得把被拒的行报成「本人的选择」：`test_a_stale_preference_is_filtered_out_of_the_candidates`（`source != "user"`）。

实现侧：`resolve_default_agent` 对租户配置默认额外要求 `private_owner_user_id is None`，对成员偏好要求 owner 为空或为本人；都不满足时跳过并 warning。这处 `auth/service.py` 的修改见 §7。

## 6. 性质 H：来源标注诚实

实现的文档化取值（`auth/service.py::resolve_default_agent` docstring）：`user`＝本人已登记的偏好（可达且可用）；`tenant`＝租户配置的 `default_agent_id`（且必须是**无主**的可用绑定）；`shared`＝共享池内兜底；`own`＝**调用者本人**私有池内兜底；`None`＝无可用候选。

在 `tests/test_user_default_initialisation.py` 新增 4 个用例，核心是 `_assert_source_is_honest`：把 `{agent_id, source}` 与它**声称**的那个状态逐项交叉核对——是不是本人 `memberships.default_agent_id`、是不是租户配置项、绑定是否无主、owner 是否就是调用者、以及某个「兜底」是否被误报成「有人做过决定」。

- `test_every_reported_source_agrees_with_the_state_it_names`：`user`（alice 的供应偏好，锚点＝她的存储偏好）、`tenant`（bob 没选过 → 租户条目，无主）、`shared`（租户条目被删除后 bob 落共享池：既不是他的偏好也不是配置项，正是「没人做过的决定」）。
- `test_the_fallback_pools_are_never_reported_as_a_choice`：`own`（alice 有私有 Agent 但未登记为偏好）与「无候选 → `None`」两种兜底。
- `test_an_unusable_preference_is_reported_as_the_fallback_it_fell_to`：库里写着「bob 选了 alice 的 Agent」这条不可用偏好；锚点落到租户条目，来源必须报 `tenant` 而**不是** `user`——报告不得替 bob 认领一个他没做过的决定。
- `test_the_source_honesty_check_is_not_vacuous`（守守卫）：把真实载荷的 `source` 分别改错成 `tenant`/`shared`/`own`，以及「有锚点无来源」「无锚点有来源」两种组合，要求检查**必须失败**。这条在开发中真的抓到过一次漏洞：早先的检查对 `own` 只验证「私有且归本人」，而 alice 的**偏好**恰好也是她本人的私有 Agent，于是把 `user` 误标成 `own` 也能通过；补上「`own` 不得等于存储偏好」后该分支才真正可证伪。

```
$ .venv/bin/python -m pytest tests/test_user_default_initialisation.py -q -p no:randomly
...........................                                              [100%]
27 passed in 3.24s
```

### 与 4.6 证据的用词差异（说明，非缺口）

4.6 的证据表格把「兜底落在非共享池」的 source 记为 `any`；当前实现与前端用的是 `own`（`auth/service.py` 的 docstring 与 `agents_anchor_source_own`），语义相同且更准确（它只可能是**调用者本人**的私有池）。两者不一致之处以本文件为准。

## 7. 为实现缺陷所做的产品改动（醒目说明）

`auth/service.py::resolve_default_agent`：租户配置的 `default_agent_id` 原先只要「是可用绑定」就会被采纳，因此一行**私有**的租户默认会把所有成员（以及无主体的公开消费者）锚进某个人的私有工作区。现在该分支额外要求 `private_owner_user_id is None`，否则跳过并 warning、继续在共享池里回落（性质 G 的直接要求）。`appoint_tenant_default_agent` 本来就拒绝私有目标，但 `bind_agent` 只修缺失owner 的语义、以及旧构建写入的行，都能留下这种状态，「写路径拒绝」不能代替「读路径不能服务」。该修复在上一阶段完成，对应测试 `test_a_tenant_default_that_points_at_a_private_agent_is_refused` 在修复前失败、修复后通过。

本任务（4.8）本身只新增/修改 `tests/**` 与 `openspec/changes/unify-console-by-data-scope/evidence/**`；未改动 `agent/personal_assistant.py`、`channel/web/web_channel.py`、`channel/web/static/js/console.js` 与任何 spec。

## 8. 未通过/缺口

**无。** A–H 每一条都有会在该性质被移除时失败的测试（§2、§3 的 spy 与「反向同证」用于防止「因错误原因通过」）。

需要说明的范围边界（不是缺口）：

- **D 的「后台任务」概念确实存在**（`agent/tools/scheduler/{identity,integration}.py`，任务级快照 + 触发前复核），因此 D 是按实现断言而不是按假设断言；触发时的 Agent 来自任务快照/调度器实例，偏好从未参与，故「默认修改不改变后台任务」对已命名 Agent 的任务成立。红-绿突变见 §10。
- **E 只对「显式绑定」成立**：没有绑定值、只靠租户默认兜底的渠道实例，会跟随租户默认改变——这是设计 D4 的既有语义（兜底不是决定），不是本任务要钉的性质。测试里用同一租户的无绑定实例把这个边界显式断言出来（并在 §10 的突变中作为反向证据）。

## 9. 本次交付的测试清单

| 文件 | 新增 | 钉住的性质 |
| --- | --- | --- |
| `tests/test_default_change_session_anchor.py` | 新文件 6 例 | A：`test_a_default_change_moves_the_next_agent_less_session`、`test_a_later_agent_less_session_uses_the_new_default`；B：`test_an_existing_session_keeps_its_agent_across_a_default_change`、`test_the_anchor_survives_a_restart`；C：`test_an_explicit_target_is_not_replaced_by_the_default`、`test_an_explicit_target_wins_even_for_a_fallback_anchor` |
| `tests/test_default_agent_fail_closed.py` | +2 例 | F：`test_a_subject_less_caller_is_never_handed_a_private_fallback`；G：`test_a_tenant_default_that_points_at_a_private_agent_is_refused` |
| `tests/test_scheduler_identity_revalidation.py` | +1 例 | D：`test_a_default_change_does_not_re_point_an_existing_task` |
| `tests/test_personal_channel_inbound.py` | +2 例 | E：`test_changing_a_members_default_does_not_re_route_their_personal_instance`、`test_a_bound_instance_keeps_its_target_when_the_tenant_default_changes` |
| `tests/test_user_default_initialisation.py` | +4 例 | H：`test_every_reported_source_agrees_with_the_state_it_names`、`test_the_fallback_pools_are_never_reported_as_a_choice`、`test_an_unusable_preference_is_reported_as_the_fallback_it_fell_to`、`test_the_source_honesty_check_is_not_vacuous` |

## 10. 反向验证（红-绿）：把性质从实现里拿掉，测试必须失败

除 spy、「反向同证」与守守卫用例之外，对两条最「结构性」的性质做了产品代码的临时突变（跑完立即还原，`git status`/`git diff --stat` 对该文件为空）：

1. **E（渠道绑定）**：把 `channel/chat_channel.py` 的 `pinned = context["bound_agent_id"] or <租户默认>` 改成只用租户默认（即「绑定不再优先」）：
   ```
   >       assert consulted == [], (
               "a bound instance must not read the tenant default at all")
   E       AssertionError: a bound instance must not read the tenant default at all
   E       assert [('tnt_IHwOFzoP2_mbRLsN',)] == []
   ...
   [INFO][chat_channel.py:599] - [chat_channel] db external inbound mapped
       user=usr_BTnTxFxrNf2hYHWR tenant=tnt_IHwOFzoP2_mbRLsN agent=agent-shared-second
   tests/test_personal_channel_inbound.py:1118: AssertionError
   1 failed, 40 deselected in 0.67s
   ```
   日志里 `agent=agent-shared-second` 正是「租户默认被改动后，手工配置的连接被静默改道」这一缺陷现象。
2. **D（后台任务）**：把 `agent/tools/scheduler/identity.py::revalidate_owner` 的 `svc.get_agent_binding(owner["agent_id"])` 改成先按偏好解析（即「授权复核按偏好」）：
   ```
   E         Use -v to get more diff
   tests/test_scheduler_identity_revalidation.py:320: AssertionError
   [INFO][integration.py:573] - [Scheduler] Task keeps-its-agent executed: sent message to member
   1 failed, 12 deselected, 1 warning in 0.39s
   ```
   按偏好解析后任务被放行（成员的新默认可用），于是 `last_skip_reason == AGENT_DENIED` 的断言失败。
3. **H（来源诚实）**：`test_the_source_honesty_check_is_not_vacuous` 本身就是红-绿证据——它要求把载荷的 `source` 改错时必须检查失败。开发中它真的抓到过一处过松：早先 `own` 分支只验「私有且归本人」，而 alice 的**偏好**恰好也是她本人的私有 Agent，把 `user` 误标成 `own` 也能通过；补上「`own` 不得等于存储偏好」后该分支才可证伪。
4. **G（不回落他人私有）**：对应测试在修复前为红（`resolve_default_agent` 服务私有租户默认），修复后转绿，见 §7。
5. **A/B/C**：没有对产品代码做突变（`channel/web/web_channel.py` 当时正被其他未提交改动占用，不做临时改写）。其可证伪性来自断言本身：会话落在**哪个 Agent 的库**、该库的 owner 行、`GET /api/history` 能否读到、以及**用新默认地址访问旧会话必须 `not found`**——若锚点改为按偏好重新推导，这些断言都会失败；`anchor()` 与 `start_agent_less_session()` 走的又正是 `POST /api/message` 的同一函数。

## 11. 复跑命令与最终结果（逐字）

```
$ .venv/bin/python -m pytest tests/test_user_default_agent_selection.py tests/test_user_default_initialisation.py -q -p no:randomly
....................................................                     [100%]
52 passed in 20.93s
```

```
$ .venv/bin/python -m pytest tests/test_default_change_session_anchor.py -q -p no:randomly
......                                                                   [100%]
6 passed in 2.95s
$ .venv/bin/python -m pytest tests/test_default_agent_fail_closed.py -q -p no:randomly
..........                                                               [100%]
10 passed in 0.97s
$ .venv/bin/python -m pytest tests/test_scheduler_identity_revalidation.py -q -p no:randomly
.............                                                            [100%]
13 passed, 1 warning in 3.27s
$ .venv/bin/python -m pytest tests/test_personal_channel_inbound.py -q -p no:randomly
.........................................                                [100%]
41 passed in 18.61s
$ .venv/bin/python -m pytest tests/test_user_default_agent_selection.py -q -p no:randomly
.........................                                                [100%]
25 passed in 20.73s
```

前端未被本次改动触及；作为回归顺带复跑（默认来源文案与锚点提示）：

```
$ node --test tests/test_user_default_agent_frontend.cjs tests/test_tenant_default_agent_frontend.cjs
ℹ tests 19
ℹ suites 0
ℹ pass 19
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 37.9335
```

## 12. 与 `cow management register` 的交互（收口时发现并修复）

性质 G（无主体调用者没有「自己的私有池」，绝不回落他人私有）与 `register` 的**显式共享**步骤相撞。

原实现（本 change 早期）在 register 末尾这样找「本租户解析为其默认的那个 Agent」：

```python
default_agent_id = (svc.tenant_default_agent_id(tid)
                    or svc.resolved_default_agent_id(tid))
```

`register` 只做绑定（`register_default_tenancy` 让每个 Agent 私有归初始管理员），**不写**
`tenants.default_agent_id`；实测该租户在 register 之后 `tenant_default_agent_id` 为 `None`、
`resolve_default_agent(tid)` 为 `{'agent_id': None, 'source': None}`。于是性质 G 生效后
`resolved_default_agent_id(tid)` 恒为 `None`，共享分支永不触发：租户带着一个**私有** Agent
作为可解析默认，成员仍需自行选择（正是 `repair-tenant-defaults` 文档描述的旧症状），
`tests/test_management_repair_tenant_defaults.py` 的两条用例因此失败
（`register left the tenant default private to the admin`、缺 `agent.make_tenant_shared` 审计行）。

修复：**按发送路径的同一问法提问**，把主体显式给出——

```python
default_agent_id = (svc.tenant_default_agent_id(tid)
                    or svc.resolved_default_agent_id(tid, admin_id))
```

`admin_id` 是 register 指定的私有 owner，因此这一步问的是「该成员发消息会落到哪个 Agent」，
即解析真正会服务的那个；随后由 `make_agent_tenant_shared` 具名、审计地共享它。性质 G 未被放宽：
无主体调用者仍然拿不到他人的私有对象，本处是**有主体**的正常解析，且共享仍是显式动作。

```
$ .venv/bin/python -m pytest tests/test_management_repair_tenant_defaults.py -q -p no:randomly
........                                                                 [100%]
8 passed in 0.49s

$ .venv/bin/python -m pytest tests/ -q -p no:randomly -k "register or tenant_default or default_agent or management_repair or cli"
237 passed, 4270 deselected in 42.57s
```
