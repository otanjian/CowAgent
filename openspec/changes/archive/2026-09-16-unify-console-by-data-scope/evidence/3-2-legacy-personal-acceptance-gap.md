# 任务 3.2 证据：旧个人页面验收用例与正式页面契约的落差

## 结论

阶段 2.4（`LEGACY_PERSONAL_MENU_MAP` + `BUILTIN_MENU_DEFAULTS` 改写 + `_migration_26`）
已经把 `personal.*` 页面从菜单契约中**退役**：内建 `member` / `tenant_admin` 角色
现在携带的是 `nav:admin.agents` / `nav:admin.channels` / `nav:admin.memory` /
`nav:admin.skills`。因此：

* 菜单门控（`menu_gated`）对内建成员角色**恒为真**；
* `personal.*` 页面在最终菜单批次中恒被标记 `menu_denied`（除非调用者无任何显式菜单
  grant，即兼容路径）；
* 这正是 `specs/unified-console-access/spec.md` 的要求：普通用户 MUST NOT 因操作者身份
  切换到个人页面，SHALL 使用与租户管理员相同的业务页面。

## 已修复的连带回归（本轮）

| 问题 | 处理 |
| --- | --- |
| 管理读包含停用对象后，`can_chat` 仍为真（「卡片可点但发送被拒」） | `_iter_tenant_agents` 对 `not profile.enabled` 显式置 `can_chat=False, reason="agent_disabled"`；前端新增 `agent_disabled` 文案（zh/zh-Hant/en）+ i18n 快照 |
| 菜单最终批次无条件把 `reason` 覆写为 `menu_not_granted`，抹掉更具体的 `capability_disabled` / `no_permission` / `consumer_closed` | 仅在原本无 reason 时写入 `menu_not_granted`；`menu_denied` 仍照常置位（控制台入口门控与逐项门控读的是该标志） |
| `tests/test_agent_workbench.py` 桩服务缺 `resolve_default_agent` | 补桩（task 4.6 的富返回形状） |
| `tests/test_plan_3_1_joint_acceptance.py::test_11_1` 断言停用对象从个人列表消失 | 改为按 `agent-workbench` 规范断言：停用对象仍在管理范围可见（可重新启用），但 `can_chat=False` |
| 跑全量时 `tests/test_weixin_qr_flow.py` 10 例失败（`personal_agent_disabled`），单跑全绿 | `WebAppHarness.close()` 未解绑进程级 Agent Registry。harness 替换了 `conf` 却把按该 `conf` 惰性构建的注册表留在全局，下一个测试首次查找就答的是**上一个 harness 的名册与临时目录**。`close()` 现在显式 `set_agent_registry(None)` |

复跑合并集（9 个阶段 3/4 验收文件）：`206 passed`。

## 剩余工作（任务 3.2 的本体）

以下 4 个验收文件仍按**已退役的个人页面**断言，需按 `unified-console-access`
改写到正式页面（`admin.agents` / `admin.memory` / `admin.channels` / `admin.skills`），
断言同页同字段、范围由对象范围决定：

1. `tests/test_personal_console_pages.py`（9 例）
   - 页面可用性、`states`/`actions` 投影断言对象应从 `personal.*` 改为 `admin.*`；
     范围（本人私有 vs 全租户）由 `agent-workbench` / `database-memory-console`
     的对象范围决定，而非页面 id。
2. `tests/test_personal_capability_switches.py`（6 例）
   - 开关撤销后 `reason == "capability_disabled"` 的部分已恢复；剩余失败源于
     `assertNotIn("menu_denied", entry)` 与「内建成员角色恒有菜单集」矛盾。
     改写方向：开关关断时以 `capability_disabled` 为**首选**原因（已在投影中保证），
     并断言 `switches` / `available` 而非「不得出现 `menu_denied`」。
3. `tests/test_personal_console_acceptance.py`（3 例）
   - 「目录可读、执行关闭」的三例：改读 `admin.channels` 的 `states.execution`。
4. `tests/test_tenant_admin_skills_menu.py::test_a_plain_member_gets_no_skills_administration`
   - 契约已变更：普通成员**共享** 工具与技能 页面（`nav:admin.skills` 已进入
     内建成员菜单默认值），管理范围由对象范围决定，不再有「个人页/管理页」之分。
     该例应改为断言普通成员在 工具与技能 页面只看到本人可见资源与本人动作。

> 注意：这些文件是 `enable-member-personal-console` 的验收件，其**行为约束**
> （owner 检查、开关语义、执行关闭可读）仍然有效，不应删除；需要更换的是**承载页面**。

## 复现

```bash
.venv/bin/python -m pytest tests/test_personal_console_pages.py \
  tests/test_personal_capability_switches.py \
  tests/test_personal_console_acceptance.py \
  tests/test_tenant_admin_skills_menu.py -q -p no:randomly
# 25 failed, 51 passed
```

对照基线（HEAD，`/tmp/cow-baseline`）同文件：全绿，确认落差由阶段 2.4 的契约变更引入。

## 改写目标契约（已实测）

内建 `member` 角色、开关全开时的 `console_pages` 实测值：

```
personal.*          available=false  reason=menu_not_granted  menu_denied=true   (已退役)
admin.agents        available=true   reason=""  scope=agent  actions={create:true,update:true}
admin.memory        available=true   reason=""  scope=agent  actions={}
admin.skills        available=true   reason=""  scope=agent  actions={}
admin.channels      available=false  reason=no_tenant_control  actions={create:false,update:false}
```

结论与改写方向：

* `admin.agents` / `admin.memory` / `admin.skills` 已对普通成员开放，**承载**原
  `personal.agents` / `personal.memory` / `personal.tools`+`personal.skills` 的
  `states` / `actions` 断言可以直接平移过来（同页同字段，范围由对象范围决定）。
* `admin.channels` 对普通成员仍报 `no_tenant_control`：**这是真正的产品缺口**，
  属于阶段 6（渠道配置收敛）——成员的「本人连接」承载面尚未落到 `admin.channels`。
  此文件的渠道相关断言在阶段 6 完成前不能简单改写成「成员可用」，应先按
  `no_tenant_control` 断言，并在阶段 6 完成后收紧为本人范围可用。
* `personal.*` 在开关关断时仍须报 `capability_disabled`（本 change 自己的
  `test_11_5` 已如此断言），但它**同时**是 `menu_denied`：开关语义与菜单契约
  是两件事，断言应分别检查 `switches`/`available` 与 `menu_denied`，不再断言
  「不得出现 `menu_denied`」。

