# 1.4 管理员 owner 旁路清单（Stage 1）

本文件是 `enable-member-personal-console` 任务 1.4 的证据产物：逐个登记「管理员可绕过私有归属」的实际位置，
并标注本 change 对每一处的处置。所有行号均已逐条复核。

## 1. 两种旁路机制

| 机制 | 判定 | 位置 | 特征 |
| --- | --- | --- | --- |
| **平台管理员 = `all`** | `authorization_mode(user_id, tenant_id) == "all"` | `auth/service.py:1237` | 在 `check_resource_action`/`resource_ids_for` 中**位于归属检查之前**直接 `return True`/`None` |
| **租户管理员 = `is_tenant_admin`** | 成员角色码含 `tenant_admin` | web 层直接判 `ctx.is_tenant_admin` | 在**归属谓词内部**豁免，owner 比较永不执行 |

## 2. 旁路总表（已复核）

| # | 区域 | 位置 | 条件 | 泄露范围 | 本 change 处置 |
| --- | --- | --- | --- | --- | --- |
| A1 | Agent 读/用/改/启停/删除 | `web_channel.py:452` `_tenant_admin_owns_agent` | 租户管理员 → 本租户**任意**绑定 Agent | **全部动作**（`tenant_agent_ids` 含私属） | 任务 3.1/3.2 收紧 |
| A2 | Agent 及**所有**资源类型 | `auth/service.py:1279` `check_resource_action` | 平台 `all` → `return True` | 全部 | 任务 3.2（owner 优先） |
| A3 | Agent 投影/ID 集合 | `auth/service.py:1439` `resource_ids_for` | 平台 `all` → `return None` | 全部 | 任务 3.2 |
| A4 | Agent 核心文件（prompt/人设/config） | `web_channel.py:8890` `AgentCoreFileHandler` | 经 A1 的 `edit` | **正文级配置** | 任务 3.2/3.4 |
| A5 | 工作台 Agent 投影 | `web_channel.py:8330` `_iter_tenant_agents` | 租户管理员 → `allowed_agent_ids=None` | 配置元数据（含 persona/model/bot_type，**超出治理元数据**） | 任务 3.3（治理投影与私有配置分离） |
| A6 | 经 `todo_tool` 运行他人私属 Agent | `agent/tools/todo/todo_tool.py:244-249` | `is_admin` 时豁免 `private_owner` 检查，并注入 `todo.read/write` | 该 Agent 的执行 | 任务 3.2/3.4 |
| M1 | 个人记忆/私有资产读取 | `web_channel.py:1114` `_db_path_owner_forbidden` | 租户管理员 → `return False` | **正文** | 任务 3.2/3.4（**核心单点**） |
| W1 | 文件读/列/下载/预览 | 同上，经 `_db_path_visible:3520`、`_authorize_db_file_path:3590` | 租户管理员 | **正文** | 任务 3.4 |
| W2 | 文件写/上传/语音 | `web_channel.py:3285/3304/3370/3609` `_require_private_owner` | 租户管理员 | **正文（写）** | 任务 3.4 |
| K1 | 知识 读/列/图谱 | `web_channel.py:1114`，经 `_require_private_owner` | 租户管理员 | **正文** | 任务 3.4/3.5 |
| K2 | 知识 写 | `web_channel.py:318` `_require_knowledge_write` | 租户管理员**或**平台管理员 → `return` | **正文（写）** | 任务 3.5 + 门槛 Q4 |
| C1 | 凭证（解密/列表/轮换/吊销） | `auth/service.py:4736` `_is_control` → `:4758` `_credential_eligible` | 平台管理员**或**租户管理员 | **明文**（`resolve_credential`） | 任务 4.x（个人凭证） |
| C2 | 租户渠道凭证 | `auth/service.py:5291/5436/5545` | `_is_control` | 真值 | 任务 4.x |
| G1 | HTTP 路由功能权限闸门 | `auth/http_policy.py:173` | `not ctx.is_platform_admin` 才校验 | 闸门 | 任务 3.2 |
| T1 | 工具执行 | `auth/service.py:1295` + `agent_stream.py:2151` | 租户管理员免 grant | 闸门 | 保留（本 change 不反转） |
| E1 | 外部 IM 入站 `chat.use` | `channel/external_identity.py:273` | 平台/租户管理员跳过 | 闸门 | 任务 7.x |
| O1 | OpenAI 兼容 API `chat.use` | `channel/web/openai_api.py:522` | 同上 | 闸门 | 任务 5.x |

## 3. 已核实**无**管理员旁路的区域

- **会话 / runs**：`_require_owned_session`（`web_channel.py:1004`）比较 `sessions.owner == ctx.user_id`，不读管理员状态；
  `_require_session_scope`（`:1016`）组合租户绑定 + 可见性 + 归属；`load_history_page(..., user_id=...)` 过滤
  `WHERE session_id=? AND owner=?`（`agent/memory/conversation_store.py:1426`）。runs 无 HTTP 路由、无管理员参数。
- **人设注入**：`bridge/agent_bridge.py:1021` `_session_speaker_user_id` 拒绝为他人会话注入个人人设。
- **记忆工具成员豁免**：`personal_memory_tool_may_execute`（`auth/service.py:1345`）是成员豁免，不是管理员旁路。
- **`memory_get`**：`agent/tools/memory/memory_get.py:66` 以**已验证身份**拒绝他人命名空间。

### ⚠️ 附带发现（非管理员旁路，但同属个人边界缺口）

`SessionSettingsHandler`（`web_channel.py:9855` GET / `:9872` POST）**只有 `_db_scope()`，完全没有会话归属校验**
（已复核 `:9855-9886`）。任何本租户成员可用任意 `session_id` 读/写会话偏好。它从不调用 `_require_session_scope`。
→ 归入本 change 的任务 3.x 一并收紧。

## 4. 可复用的正确模式（owner-only 已有先例）

| 助手 | 位置 | 作用 |
| --- | --- | --- |
| `is_private_agent_owner` | `auth/service.py:1408` | 单行绑定查询，归属 = `private_owner_user_id` + 租户匹配 |
| `private_agent_ids` | `auth/service.py:1391` | 归属派生的 ID 集合 |
| `PRIVATE_AGENT_OWNER_ACTIONS` | `auth/policy.py:377` | 仅 `read`/`use` |
| `_require_owned_session` / `_require_session_scope` | `web_channel.py:1004` / `:1016` | 以 `sessions.owner` 为唯一真相 |
| `_session_speaker_user_id` | `bridge/agent_bridge.py:1021` | 拒绝为他人会话个性化 |
| `_resolve_user_scoped` | `agent/tools/memory/memory_get.py:66` | 从已验证身份拒绝他人命名空间 |
| `_memory_write_stays_own_scope` | `auth/service.py:141` | `memory_add` 豁免仅限 `scope != shared` |
| `_require_tenant_agent_binding` | `web_channel.py:1060` | Agent 寻址的租户边界 |
| `owned_agent_id` | `agent/personal_assistant.py:289` | 幂等归属派生 |
| `_db_path_visible` / `_visible_entries` / `_workspace_request_scope` | `web_channel.py:3520` / `:10327` / `:10312` | 按**路径真实 owner** 而非自报 `agent` 参数 |

## 5. 本 change 的统一收口策略

**核心单点**：`_db_path_owner_forbidden`（`web_channel.py:1110-1117`）是全部分记忆/知识/文件读取的共享闸门，
删除其中 `:1114` 的 `is_tenant_admin` 早返回即可同时收紧 M1/W1/K1；写侧（W2/K2）需各自前置 owner 校验。

**平台 `all` 旁路**（A2/A3）不能只靠改 web 层：它发生在 `auth/service.py:1279`，位于归属检查之前。
须调整为「owner 判定优先于 `all` 短路」，或为私有内容引入不接受 `all` 的独立判定。

**治理投影**（A5）须从「配置文件全量」收敛为 spec 要求的「归属、数量、状态与用量」元数据。

## 6. 与 spec delta 的对应

| 旁路 | 对应本 change 的 requirement |
| --- | --- |
| A1/A2/A4/A6 | `rbac-authorization` 固定个人和租户共享资源策略（owner 优先、管理员身份不授予私有内容） |
| A5 | 同上（治理投影与私有配置分离） |
| M1/W1/W2 | `platform-file-browsing` + `tenant-resource-isolation`（管理员资格不形成私有读取旁路） |
| K1/K2 | `tenant-knowledge-console` 私有归属先于知识写入资格 |
| C1/C2 | `personal-channel-configuration`（管理员 MUST NOT 读取/替换/使用成员个人凭证） |
| E1/O1/G1 | `personal-channel-configuration` + 入站/执行侧 |

> 状态：1.4 **完成**。
