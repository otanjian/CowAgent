## Why

多智能体会话里，**先建会话、后邀请同事**时，默认智能体在提示词里"看得见"团队，却拿不到 `agent_delegate` 交办工具，于是只能道歉并让用户自己点同事。

实测证据（控制台「公司请假政策」会话）：

```
20:33:12  会话创建，此时智能体运行时被构建（还是单人会话 -> 工具被跳过）
20:34:00  智能体回答"当前我们是单独对话"（当时确实还没有成员，回答正确）
20:34:12  设置写入 members = ['business-analysis']
20:34:14  设置写入 members = ['business-analysis','knowledge-qa']
20:35:32  智能体回答"我当前的工具集里并没有 agent_delegate"（成员已存在，工具却没跟上）
```

根因是两条读取时机的错配：

- `agent_delegate` 的开关判断在 `AgentInitializer._load_tools`，**只在会话运行时构建时执行一次**；
- 团队名册经 `_get_teammates` **每轮动态读取**。

`POST /api/sessions/<id>/settings` 写入 `members` 后只调用 `apply_session_prefs`（仅同步 model/permission），既不驱逐该会话的缓存运行时，也没有别的路径会因名册变化而失效缓存（缓存键是 `(agent_id, session_id)`）。所以运行时一旦在单人状态构建，后续无论怎么邀请，工具都不会出现，直到会话被其他原因重建。

已用真实代码路径验证：单人 -> 无 `agent_delegate`；名册存在 -> 有 `agent_delegate`（15 个工具）；在租户身份下该会话 `_is_shared_conversation == True`、teammates 解析正常。即"只要重建一次运行时，工具就会出现"。

## What Changes

- **会话设置写入时退休过期运行时**：`SessionSettingsHandler.POST` 在 `members` 真正发生变化（按集合比较，忽略邀请顺序）后，驱逐该会话全部参与者（owner + 旧成员 ∪ 新成员）的缓存运行时，使下一轮按新名册重建。持久化历史在重建时从会话存储恢复，不丢对话内容。
- **不做无谓重建**：名册未变化（重复保存同一份成员）或不含 `members` 的设置写入（例如只改模型）不驱逐任何运行时，保住热运行时的内存消息。
- **坏名册不阻断写入**：名册可能包含已归档/未知智能体 id，逐个驱逐失败只记 debug 日志，不影响设置保存成功。

## Capabilities

### New Capabilities

- `agent-team-conversation`: 会话运行时的装配必须跟随当前团队名册——名册变化后，参与者的工具集（含 `agent_delegate`）与提示词必须在下一轮反映新团队；名册未变化不得重建；重建不得丢失持久化历史。

### Modified Capabilities

（无）

## Impact

- 后端：`channel/web/web_channel.py`（`SessionSettingsHandler.POST` 的名册比较与 `_drop_team_runtimes` 驱逐；管理面/前端 API 契约不变）。
- 测试：新增 `tests/test_session_team_runtime.py`（真实 `AgentBridge` 缓存 + 假构建，观察重建次数）。
- 不改动：`AgentInitializer._load_tools` 的装配时机与门槛（仍然只在构建时判定，符合既有设计）；`session_prefs` 存储格式；`console.js` 的邀请/移除交互；control 台团队 UI 文案。
- 已知未覆盖（不在本 change 范围）：控制台的会话设置请求不携带 `agent`，因此团队名册固定写在**默认智能体**命名空间下；当用户在非默认智能体的会话里邀请同事时，名册与运行时仍会错位。需要时另开 change 处理。
