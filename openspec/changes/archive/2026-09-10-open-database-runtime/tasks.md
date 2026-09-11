## 1. 身份库与外部绑定

- [x] 1.1 为 `identity.db` 增加版本化迁移：创建 `external_identities`（`provider + issuer/corp_id + subject` 唯一 → `user_id`，含时间戳字段）
- [x] 1.2 在 IdentityService/Store 实现绑定的创建/列表/删除，强制唯一约束与冲突 409
- [x] 1.3 增加仅管理员可调用的管理 HTTP API（平台或租户管理资格），普通成员 403
- [x] 1.4 单测：绑定冲突、解绑立即失效、非管理员拒绝

## 2. 拆除 database 运行关闭门

- [x] 2.1 修改 `_workbench_chat_readiness`：按 `chat.use` + 目标 `agent.use` 计算 `can_chat`，取消 database 固定 `runtime_not_enabled`
- [x] 2.2 修改 `AgentBridge`：database 模式下完整初始化 registry/router/实例表（与 legacy 同路径）
- [x] 2.3 修改 `app` 启动：去掉仅保留 web 的通道过滤；恢复 MCP warmup/调度相关 database 跳过逻辑（在授权具备时）
- [x] 2.4 去掉聊天/文件/上传/语音等路径上不当的 `_guard_not_database`；将 `auth/http_policy` 中调度等 `closed` 改为正确策略并在 handler 重验（文件/语音已开；调度保持 closed 移交 5.x）
- [x] 2.5 开放 OpenAI 兼容 API 的 database 关闭短路，改为身份 + 权限校验（已拍板：DB 会话 token/cookie + X-Tenant-ID + 可选 X-Agent-ID，租户默认 Agent 兜底；裸 external_api_token 仅 legacy；见 design 决策 11）
- [x] 2.6 更新 `_consumer_availability`：已开放消费者不再 `deferred`/`consumer_closed`（chat/tools/files/openai_api/mcp/channels 已开；scheduler 留 5.x）；页面能力投影（workbench.chat 可用性/动作）随 3.3 落地

## 3. Web 对话授权闭环

- [x] 3.1 在 `_authorize_chat_session`（及创建/恢复路径）接入 `_require_chat_use` 与目标 `agent.use` 资源校验（恢复自有会话每次重验；他人会话 404 掩蔽先行；speaker_agent_id 转交同样验 `agent.use`）
- [x] 3.2 模型选择/发送路径的 `model.use`：会话设置选择即验；运行期由 `AgentLLMModel.call` 依据环境身份逐调用重验（授权集含 catalog `provider:{pid}:{code}` 尾段匹配）
- [x] 3.3 前端工作台：`permission_denied` 卡片禁用 + 权限文案/通知（`agent_permission_denied` 各语言），不再把 database 缺权当作「当前版本尚未开放对话」
- [x] 3.4 测试：获权可 chat（可 mock 模型/transport）、缺权 403、恢复被拒 403、legacy 回归；改写 `test_web_chat_boundary`（新增 3 个 RBAC 用例 + member 授 chat-op 角色）、`test_agent_workbench` 前端与 cjs 期望对齐（nav 新结构、permission label）

## 4. IM 入站映射执行

- [x] 4.1 抽取统一「外部身份 → User → RequestContext」解析模块供各通道复用（`channel/external_identity.py`；测试 `tests/test_external_im_gate.py`）
- [x] 4.2 为已启用 IM 通道（至少飞书；钉钉/企微等按现配置覆盖）在入站路径接入解析；未绑定/停用/无权则固定提示且不调模型（飞书 + 钉钉单/群聊已 stamp 三元组；其余未 stamp 通道在 database 下 `external_channel_unsupported` fail-closed）
- [x] 4.3 确保执行身份为映射用户权限并集，通道 `agent_id` 仅路由工作区（`_preflight_external_inbound` 落 member 快照，`_identity_for` 重建全身份）
- [x] 4.4 测试：绑定成功执行、未绑定拒绝、解绑后拒绝、缺 `chat.use`/`agent.use` 拒绝（全绿）

## 5. 调度与长任务重验

- [x] 5.1 任务创建持久化 `user_id`/`tenant_id`/目标资源标识（`scheduler_identity.owner_snapshot` 于 `SchedulerTool._create_task`）
- [x] 5.2 触发前重验成员与 grants；失败跳过并记录原因（`revalidate_owner` + `skip_record`；连续跳过达阈值自动停用）
- [x] 5.3 测试：撤权后触发不执行（`tests/test_scheduler_identity_revalidation.py` 全绿）

## 6. 执行隔离

- [x] 6.1 在工具/技能代码执行入口落地租户工作根白名单与路径解析（拒绝穿越/软链/绝对越界；`isolation.py` realpath 归一 + 读写候选拆分）
- [x] 6.2 收紧 `agent/permission/policy.py` 的 `workspace-write`/`full-access` 到租户隔离根，并明示非 OS 沙箱边界（实现于 `agent_stream._permission_denial` 先于 legacy mode 的 `isolation_decision`，等效收紧且 full-access 不能跨租户；`policy.py` 未直接改动，边界明示非 OS 沙箱）
- [x] 6.3 隔离未验收/未启用时，database 下任意代码执行工具默认拒绝；纯 LLM 与只读工具不受影响（已修复：DB + `execution_isolation=false` → CODE_TOOLS fail-closed）
- [x] 6.4 测试：跨租户读取拒绝、路径穿越拒绝、隔离关闭时 bash 拒绝、隔离验收后获权执行（15 例全绿）

## 7. 凭据（08S）

- [x] 7.1 凭据加密存储 + 掩码投影 + 日志/审计脱敏（`auth/crypto.py` AES-256-GCM，未配主密钥拒绝落盘；`mask_secret`；审计不含明文，测试断言）
- [x] 7.2 凭据按租户/资源授权，使用点重验身份与 grant，按需注入（`resolve_credential` 使用点重验：租户归属 + resource_kind/id 匹配 + controller/`credential.use`；注入到真实工具的消费方仍关闭，随对应消费方 change）
- [x] 7.3 撤权/轮换即时失效，轮换保留版本并审计（revoke→active=0 立即 404；rotate 追加版本仅新值可解；生命周期入审计）
- [x] 7.4 测试：跨租户使用拒绝、无 grant 读取拒绝、撤权后注入失败、轮换后旧版本失效（新增用例全绿）

## 8. 审批（09G）

- [x] 8.1 高风险外部动作白名单 + 待审批状态机（待审批/通过/拒绝/过期/撤销；以 action 字符串注册代替静态白名单；撤销 = requester 撤 pending 或 controller 撤 approved）
- [x] 8.2 审批资格与职责分离（非发起人、具资格），未审批不执行（decide 仅 controller 且非 requester；无执行器故未审批不执行天然成立，执行器接入须先验 approved）
- [x] 8.3 审批全链路审计（approval.create/approve/deny/cancel/revoke 均落库）
- [x] 8.4 测试：未审批不执行、自我审批拒绝、无资格审批 403、过期不执行（撤销/取消路径用例已补）

## 9. 配额（10B）

- [ ] 9.1 租户级硬上限计量（token/工具调用/并发/存储），达限拒绝（部分交付：token/工具调用/messages/storage_bytes 窗口计量 + `tool_calls` 已接入 agent 执行闸达限双语拒绝；**并发计量未落地**，需会话/连接生命周期消费方）
- [x] 9.2 配额按租户与身份隔离，跨租户不借用（consume 键到真实成员租户，非成员拒绝，跨租户借用测试全绿）
- [ ] 9.3 配额变更/撤权即时生效，入队任务触发前重验（变更/降额即时生效已测；调度触发前已重验成员/grants，配额由执行闸按调用实时拒绝，尚无入队任务配额预检）
- [x] 9.4 测试：超 token 配额拒绝、跨租户借用拒绝、降配额后下一次消费拒绝、达限拒绝审计（`test_control_plane.py` 全绿；超并发用例随 9.1 并发计量未落地而缺）

## 10. 审计（10A）

- [ ] 10.1 敏感操作追加式防篡改审计（授权变更/凭据/审批/配额/代码执行/跨租户访问），敏感字段脱敏（授权变更/凭据/审批/配额事件 + `quota.deny` 已落库且 DB 层防篡改触发器生效、明文脱敏有测试；**代码执行与隔离拒绝为高频逐调用事件，未逐条落审计**，需定降噪/采样策略后随消费方落地）
- [x] 10.2 审计查询分级：平台管理员全量、租户管理员本租户、普通成员 403（handler 层分级授权 + scope-gate/audit 测试）
- [x] 10.3 强一致动作审计失败回滚（凭据/审批/配额均与动作同事务提交；失败回滚用例 `test_audit_same_tx_rolls_back_on_failure` 全绿）
- [x] 10.4 测试：敏感动作落审计、无明文凭据泄漏、强一致失败回滚（新增用例全绿；普通成员读取拒绝由 10.2 handler 层测试覆盖）

## 11. 文档与规范对齐验收

- [ ] 11.1 更新 `docs/design/role-resource-authorization-delivery.md` 等交付表述：线上运行不再标为延期；Desktop 企业等仍单列未交付
- [ ] 11.2 跑相关 pytest/前端契约测试并记录证据；至少一条 database Web 获权发消息闭环（mock 模型可）
- [ ] 11.3 确认内置 member 默认权限未自动获得 `chat.use`/`agent.use`；交付说明写明管理员授角步骤
- [ ] 11.4 逐切片验收记录：对话/IM/调度/隔离/凭据/审批/配额/审计各切片证据，未验收切片保持对应消费者关闭
