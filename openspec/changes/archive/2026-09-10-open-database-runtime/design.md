## Context

See `proposal.md` Why/What。当前 database 模式已具备登录、租户、RBAC 与资源 grant，且 Web `/message` 等传输已有 `_db_scope` 与会话 owner 校验；但 `_workbench_chat_readiness` 固定返回 `runtime_not_enabled`，`AgentBridge` 在 database 下提前 return 不初始化 registry，`app._db_only_entry` 启动时丢弃非 web 通道，部分文件/调度/OpenAI 仍 `_guard_not_database` 或 HTTP `closed`，`_consumer_availability` 将 chat 等标为 `deferred`。`_require_chat_use` / `_require_model_use` / `_require_agent_action` 已存在，但聊天授权链未完整接入。外部身份表未落地。

`tenant-resource-isolation` 要求高风险消费者在凭据（08S）、审批（09G）、配额（10B）、审计（10A）与执行隔离切片验收前关闭。这些切片当前**零实现**：工具执行走 `subprocess`，`agent/permission/policy.py` 明示权限是 *argument-level, not an OS sandbox*；无审批/配额/凭据/审计存储。PRD 08S/09G/10B/10A 原文本地缺失，行为定义以 `openspec/config.yaml` context 摘要为准并在 spec 记录缺口。

## Goals / Non-Goals

**Goals:**
- 一次性拆除 database 运行关闭门，并落地执行隔离、凭据、审批、配额、审计切片后再开放对应高风险消费者。
- Web 与 IM 统一按用户权限硬拦；IM 仅管理员预绑定 `external_identities`。
- 更新能力投影与相关规范，去掉「版本未开放对话」作为默认关闭原因。

**Non-Goals:**
- 用户自助绑定、SSO 扫码建号、昵称匹配。
- 给内置 member 默认补聊天权限。
- `database_runtime_enabled` 开关。
- 宣称 Desktop 企业登录已可用。
- 精确复刻 PRD 08S/09G/10B/10A 的未在场细节（记录缺口，PRD 恢复后核对）。

## Decisions

1. **单 change 大爆炸开放，分切片阶段验收**  
   与方案 1 一致，但高风险消费者（代码执行、外部凭据动作、调度写副作用）在各自切片阶段验收前保持拒绝。备选（配置开关、分 PR）已否决。

2. **聊天授权复用已有守卫并补齐调用点**  
   在 `_authorize_chat_session`（及模型选择）显式调用 `_require_chat_use`、`_require_agent_action(..., "use", "agent.use")`、`_require_model_use`。工作台 `can_chat` 改为权限/资源判定，不再看 `_is_database_identity()` 短路。备选：仅前端隐藏——否决，服务端必须独立拒绝。

3. **管理员旁路语义（精确化）**  
   - `chat.use`：沿用 `_require_chat_use` 对 platform/tenant admin 旁路；普通成员必须显式 grant。  
   - `agent.use` 资源 grant：仅 platform admin（`authorization_mode=="all"`）旁路；tenant_admin 仍须显式 `agent.use` 权限 + 资源 grant（`check_resource_action` 现行为）。  
   本 change **不**改变这两处既有旁路语义，只把它们显式写进 spec，避免实现者误以为 tenant_admin 全免检。

4. **IM：管理员预绑定 + 映射用户执行**  
   新表 `(provider, issuer, subject) UNIQUE → user_id`。入站先 resolve 再 `resolve_context`；无绑定不跑。通道 `agent_id` 只定路由工作区，不提供服务身份旁路。备选：通道服务身份代跑——否决。

5. **调度：创建快照 + 触发重验**  
   不信任入队时授权。备选：仅快照永久有效——否决（与资源执行规范冲突）。

6. **执行隔离：进程级隔离为最小可用门槛，容器为可演进目标**  
   首期以「租户工作根白名单 + 路径解析拒绝穿越/软链 + 子进程受限」实现最小隔离，配合 `agent/permission/policy.py` 收紧 `workspace-write`/`full-access` 到租户根；明确其非 OS 沙箱边界。真正 OS/容器隔离（nsjail/容器）作为后续切片。备选：首期上容器——否决，成本高且超出单 change 可验收范围。

7. **凭据（08S）：可逆加密 + 按需注入 + 掩码投影**  
   凭据加密落库，投影/日志/审计一律掩码；使用点按当前身份与资源 grant 重验后注入；撤权/轮换即时失效。

8. **审批（09G）：高风险动作白名单 + 待审批状态机**  
   登记高风险外部动作；执行前生成待审批请求，非发起人且具审批资格者通过后执行；过期/拒绝/撤销不执行。

9. **配额（10B）：租户级硬上限 + 触发前重验**  
   token/工具调用/并发/存储分项计量，租户隔离；达限拒绝；已入队任务触发前重验。

10. **审计（10A）：追加式防篡改 + 分级查询**  
    敏感操作落审计（时间/主体/租户/资源/动作/结果，敏感字段脱敏）；平台管理员全量、租户管理员本租户；强一致动作审计失败回滚。

11. **OpenAI 兼容 API（2.5，已拍板）：database 下与 Web 同栈身份**  
    `/v1/chat/completions` 在 database 模式不再允许裸 `external_api_token` 代跑；改走 DB 会话身份：`Authorization: Bearer` 为会话 token（或登录 cookie）+ `X-Tenant-ID`，必要时 `X-Agent-ID` 选 Agent，缺省取租户绑定默认 Agent（歧义即拒）。先 resolve_context 再按 `chat.use`/目标 `agent.use`/所选 `model.use` 校验后，以该用户身份 scope 内执行；未登录 401、无权 403。`external_api_token` 仅保留 legacy 语义。备选（服务账号 token 映射/本轮保持 closed）均否决。

11. **接入点（实施时对照，可因重构微调）**  
    - `channel/web/web_channel.py`: readiness、authorize、`_guard_not_database`、consumer handlers、审批/配额/审计/绑定管理 API  
    - `bridge/agent_bridge.py`: database 提前 return  
    - `app.py`: `_db_only_entry` / MCP warmup / scheduler  
    - `auth/http_policy.py`: scheduler 等 `closed`  
    - `auth/store.py` + `service.py`: 迁移、绑定/凭据/审批/配额/审计存储与 availability  
    - `agent/permission/policy.py` + 工具执行入口: 租户隔离根、代码执行门槛  
    - IM channels 入站: 绑定解析钩子  
    - `channel/web/static/js/console.js`: 文案与 `can_chat`  
    - tests + 交付文档表述

## Risks / Trade-offs

- **[Risk] 未绑定 IM 用户突然无法对话** → 绑定 CRUD + 固定提示引导联系管理员；开放前批量预绑。  
- **[Risk] 默认 member 仍不能聊，易被当成「还是没开放」** → 能力原因改为权限类文案；交付说明要求授 `chat.use`/`agent.use`/`model.use` 与资源 grant。  
- **[Risk] 外部通道历史非租户假设导致串租户** → 强制映射用户 + 租户成员校验；拒绝服务身份回退。  
- **[Risk] 进程级隔离非 OS 沙箱** → 明确边界并保持高风险动作默认拒绝；文档与 UI 如实说明；容器隔离另立后续切片。  
- **[Risk] 切片零实现导致工作量巨大** → tasks 按切片分组、逐切片验收，任一未验收切片保持对应消费者关闭，不整体阻塞纯对话开放。  
- **[Risk] PRD 08S/09G/10B/10A 细节缺失** → spec 记录缺口，行为以 context 摘要为准，PRD 恢复后核对。  
- **[Risk] 回归面大** → 改写关闭门测试为授权测试；至少一条 mock 模型的 Web 闭环；legacy 专项回归。  
- **[Trade-off] 无紧急关闭开关** → 回滚依赖版本回退或临时切库策略（不能 legacy 读迁移库）。

## Migration Plan

1. 备份 `identity.db`；应用 schema 迁移创建 `external_identities`、凭据、审批、配额、审计（或专用存储）。  
2. 部署含本 change 的构建；保持 `identity_mode=database`。  
3. 管理员：为 IM 账号绑定外部身份；为聊天角色授予 `chat.use`、目标 `agent.use`、`model.use` 及资源 grant；配置凭据/审批白名单/配额上限。  
4. 逐切片验收：对话闭环 → IM 映射 → 调度重验 → 执行隔离 → 凭据 → 审批 → 配额 → 审计。未验收切片保持对应消费者关闭。  
5. **Rollback**：部署上一相容构建并/或恢复库快照；不得为恢复运行而 `identity_mode=legacy` 直读已迁移库。

## Open Questions

- 各 IM provider 的 `issuer/corp_id` 与 `subject` 字段取自现有消息对象的精确键名——实施时按通道代码枚举。  
- 未绑定/无权的 IM 固定回复文案是否可配置——默认内置中文短句即可。  
- 凭据加密主密钥的托管方式（环境变量/KMS）——实施时选定，不影响 spec 行为契约。  
- 配额分项与默认上限数值——PRD 缺失，先以可配置默认值落地，PRD 恢复后核对。
