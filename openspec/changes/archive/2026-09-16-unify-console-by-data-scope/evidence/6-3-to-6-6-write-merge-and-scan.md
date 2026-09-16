# 6.3–6.6 写入合流、扫码共用、修复能力与配置面验收

6.1/6.2 把页面和归属判定的入口收成一条（见
`evidence/6-1-shared-channel-surface.md`、`evidence/6-2-channel-target-derivation.md`）。
本文件记录余下四项：写入是否真的只有一份、扫码是否接在同一条写上、存量坏的连接是否
还能修，以及这一段的验收结果。

**声明边界**：以下证据是**配置面**的，不含任何真实厂商运行。真实提供方凭据下的
收发验证属于段 7，不由此文件充抵（`tasks.md` 6.6 明示「不能据此标记真实运行通过」）。

## 6.3 四类写入合入同一实例服务

| 切入点 | 落到哪 |
| --- | --- |
| 本人创建 | `create_personal_channel_instance` → `create_tenant_channel_instance(allow_owner=True)`（`auth/service.py:9167`） |
| 本人编辑 | `update_personal_channel_instance` → 同函数（`:9211`） |
| 本人启停 | `set_personal_channel_instance_active` → 同函数（`:9241`） |
| 租户公共 | 直接走 `create_tenant_channel_instance` / `update_*` / `set_*` |

`allow_owner=True` 的语义是**替换门禁**而非旁路：租户控制检查换成有效成员资格检查，
`scope/owner` 对被强制为 `("user", actor)`，请求体里的同名字段不参与。其余——类型就绪、
凭据加密、凭据版本、配额、审计、运行态对账——是同一段代码，因此「双工作台」不可能在
校验强度上分叉。

**owner/scope 不因普通编辑转移**：`UPDATE tenant_channel_instances SET display_name=?,
agent_id=?, app_fingerprint=?, version=version+1, ...`（`:8477`）里**没有** `scope` 与
`owner_user_id`。这是结构性保证，不是一条可能被漏写的检查——普通编辑没有可写这两列的
语句。改绑改的是 `agent_id`，且本人行只接受本人私有的、启用的目标
（`_require_personal_instance_agent` → `personal_agent_required` 400 /
`personal_agent_forbidden` 403 / `personal_agent_disabled` 403）。

同时**公共行不得被改成私有、私有行不得被改成公共**：`_resolve_instance_scope` 在授权前
就把 scope 定死，编辑路径不再接受 scope 入参。

## 6.4 扫码接在同一条写上

`channel/weixin_scan_adapter.py:create_instance_callable` 只做两件事：重查
`personal_channel_onboarding` 开关（`personal` 作用域时），以及行落地后
`reconcile_instance_runtime`。写入本身是 `create_tenant_channel_instance`
（`allow_owner` 由 `scan_onboarding._commit_locked` 按 `session.scope` 传入，`:1935`），
因此行、加密凭据、凭据版本、配额/策略事务、创建审计都是交付的那一份，没有第二条写路径。

授权绑定在 `auth/scan_authorization` 的 `mint/verify/claim/consume` 上，绑定
`actor_user_id`（发起用户）、`tenant_id`、`channel_type`、`scope`（归属）、`agent_id`
（目标）、`auth_session_id`（发起会话）。因此**目标或上下文改变即拒绝复用**旧授权：
会话里记下的 `(scope, agent_id)` 与提交时请求的一致才放行（
`channel/web/scan_onboarding.py:_grant_binding`）。

消费时机的选择值得记下：授权在**行提交之后**才核销，另外配额是**先预留后写、拒绝即归还**
（`_reserve_quota` / `_release_quota_reservation`）。两者合起来使「因后续原因被拒的创建
不消耗成员的扫码机会、也不留下半个实例」。

## 6.5 存量坏连接可修，非法写入不留痕

* **空私有目标** → 400 `personal_agent_required`（写入格式不对，界面必须选目标）；
* **他人 / 跨租户 / 不存在的目标** → 403 `personal_agent_forbidden`，**同一个**消息，
  不让探测读出别人的目标是否存在、叫什么；
* **共享 Agent 误绑到本人连接** → 同上，被同一条谓词拒绝；
* **停用的自有目标** → 403 `personal_agent_disabled`（成员自己知道这个对象，所以可以
  明确告知他要先把它启回来）；
* **存量坏目标可修**：纯改名不重查目标（`:8358` 起），否则一行坏数据会连改名都做不到
  而永久卡死；改绑则要求新目标合法。停用/撤销/解绑各有其面：治理停用
  （`governance_disabled*`）与本人解绑（`unlink_personal_channel_instance`）互不混淆。

失败不留部分写入由事务边界承担：行 + 凭据 + 版本 + 审计同事务，配额预留失败即回滚会话
到可重提状态。

## 6.6 验收

```
.venv/bin/python -m pytest tests/ -q -p no:randomly -k "channel or scan"
→ 637 passed, 3826 deselected, 13 subtests passed in 255.70s
```

```
.venv/bin/python -m pytest tests/test_tenant_channel_mutations.py -q -p no:randomly
→ 1 passed, 5 subtests passed        （连跑三次稳定）
```

覆盖面与要求的对应：

| 要求 | 由谁钉住 |
| --- | --- |
| 双角色同类连接的配置一致性 | `test_tenant_channel_member_access.py`（卡表单/接口/投影） |
| 扫码重放 | `test_scan_onboarding_state.py`、`test_personal_scan_scope.py` |
| 提供方幂等 | `test_tenant_channel_hot_restart.py`、`test_channel_instances.py` |
| 并发修改 | `test_tenant_channel_instances_service.py`（`expected_version` → 409） |
| 配额 / 外部应用竞争 | 同上（`quota_exceeded` / `app_conflict`） |
| 治理限制 | `test_personal_channel_console.py`、`test_plan_3_1_joint_acceptance.py` |
| 旧 API 转接 | `test_personal_channel_console.py`、`test_personal_channel_binding.py` |

**未覆盖**：真实厂商凭据下的扫码与收发（段 7）；浏览器内双角色实操（任务 3.6，需
Playwright，本机不可运行）。
