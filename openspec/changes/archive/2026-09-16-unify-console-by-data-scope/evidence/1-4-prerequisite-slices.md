# 1.4 依赖切片与未验收范围

本文件记录 `tasks.md` 1.4 的核对结果：进入阶段 2 前必须确认的前置切片（凭据、审计、配额、执行审批、
实际路径与记忆版本），并单列提供方 / 运行 / 原生 Desktop 的未通过范围。**归档状态不作为验收依据**。

## 1. 四个前置能力切片

| 能力 | 提供模块 | 存储 | 真实写入路径接线 | 结论 |
|---|---|---|---|---|
| `audit-log` | `auth/audit.py`（`audit_event` `:63`、`sanitize_payload` `:39`、`AuditStore` `:110`） | `audit_events`（`auth/store.py:174`，append-only） | `IdentityService._audit_in_tx`（`auth/service.py:505`）；已接入身份绑定/配额/默认/渠道写路径 | **切片可用（部分）**：`agent/admin.py` 无任何 audit 调用（grep 仅命中 `:28`/`:630` 的文档注释），因此 roster 创建/编辑/归档/删除与核心文件写入**未审计** |
| `credential-management` | `auth/crypto.py`、`auth/credential.py`（`select_credential` `:45`） | `credentials`（`auth/store.py:307`）、`credential_versions`（`:325`），`name='channel:<instance_id>'` | `update_tenant_channel_instance`（`auth/service.py:7735`）加密/轮换；`channel_instance_credentials`（`:8073`）解密；投影掩码 | **切片可用**：渠道凭据走密文 + 版本，响应不回显；智能体核心文件为磁盘明文（`agent/tools/utils/credentials.py:is_credential_path` `:31` 仅路径守卫） |
| `action-approval` | `agent/approval_gate.py`、`agent/protocol/agent_stream.py`、`agent/tools/scheduler/integration.py` | `approvals`（`auth/store.py:335`） | **仅**工具分发（`agent/protocol/agent_stream.py:2063-2089`）与调度（`agent/tools/scheduler/integration.py:499-505`） | **未接入智能体/渠道写路径**：创建/编辑/删除不查询审批门；`approval_required_actions` 默认空串（`config.py:300`），即「声明式」策略 |
| `resource-quota` | `IdentityService.consume_quota`（`auth/service.py:9660`）、`set_quota`（`:9609`）、`quota_status`（`:9640`） | `quota_limits`（`auth/store.py:353`）、`quota_usage`（`:360`） | 工具调用（`agent/protocol/agent_stream.py:2121`）、调度（`agent/tools/scheduler/scheduler_tool.py:134`）、项目导入（`channel/web/web_channel.py:12364`） | **未接入智能体/渠道创建**：这两条路径使用**独立计数器** `tenant_private_agent_policies`（`auth/store.py:1131`，`_enforce_private_agent_quota_in_tx` `auth/service.py:1156`）与 `tenant_channel_policies.personal_instance_limit`（`_enforce_personal_instance_policy`）。两者与 `quota_limits` 命名相近但**不共享真值** |

**阶段门槛结论**：审计与凭据切片可用；审批与硬配额**未接入**本 change 触及的写入路径。因此本 change 的
写入路径门槛采用既有专用计数器（私有智能体策略 / 个人实例策略），而**不**宣称 `resource-quota` 已覆盖
智能体与渠道创建；审批同理，不作为放行/拒绝条件写入 spec 断言。

## 2. 实际路径与记忆版本

| 依赖 | 真值 | 状态 |
|---|---|---|
| 租户共享根 | `common/state_dir.py:shared_root`（`:249`），含 `validate_tenant_shared_root`（`:168`）重叠校验 | 可用 |
| 用户根 | `common/state_dir.py:user_root`（`:310`）= `shared_root()/users/<user_id>` | 可用 |
| 个人记忆根 | `common/state_dir.py:memory_dir`（`:421`）、`memory_file`（`:440`） | 可用 |
| 智能体工作区 | `common/state_dir.py:state_root`（`:46`）；租户侧 `<shared_root>/agents/<id>`（`channel/web/web_channel.py:721-731`） | 可用 |
| 路径防逃逸 | `common/safe_fs`，经 `channel/web/memory_console.py:123` 统一返回 `CODE_UNSAFE_PATH` | 可用 |
| 记忆版本/generation | `agent/memory/personal.py`：`_revision_of` `:102`、`_scope_update` `:195`、`read_scope_generation` `:215`、`scope_publish_is_current` `:242`、`recover_incomplete_publish` `:283`、`_record_pending` `:306` | 可用 |
| 索引失败恢复 | `pending_index_labels` `:253`、`scope_incomplete` `:273`、`retry_pending_index` `:880` | 可用 |

## 3. 未验收范围（单列，不因归档或 Web 通过而转为通过）

| 范围 | 状态 | 依据 |
|---|---|---|
| 个人渠道运行 | **未验收** | `personal_channel_runtime` 默认 `False`（`auth/policy.py:546`、`config.py:290`） |
| 个人运行已验收类型集合 | **空** | `PERSONAL_RUNTIME_ACCEPTED_TYPES = frozenset()`（`channel/channel_instances.py:1276`） |
| 共享实例上个人私有入站路由 | **空** | `PUBLIC_PERSONAL_INGRESS_TYPES = frozenset()`（`:1282`） |
| 个人渠道接入声明 | 按类型就绪（`personal_channel_ready` `:1225`），**声明就绪 ≠ 运行就绪** | `channel/channel_instances.py:1187`（`PERSONAL_READY_CHANNEL_TYPES`） |
| 原生 Desktop 登录/传输 | **未验收** | 归档 `complete-desktop-and-scan-real-acceptance` 为文档提交，无真实运行证据；本 change 的 Web 通过不得等同 Desktop 通过 |
| 真实提供方往返 | **未验收** | 库内 2 条实例均 `scope='tenant'`、`active=1`，但无真实往返记录；`tests/test_tenant_channel_startup_synthesis.py` 等为合成验证 |

**结论**：阶段 7（渠道运行）**不能**在本轮声明通过；本 change 只能完成运行代码的合并与共用化，
并把「取得真实证据后显式启用」登记为剩余项（对应 7.6）。

## 4. 归档 ≠ 验收的显式登记

`openspec/changes/archive/2026-09-15-*` 六个 change 均以归档形式存在，但：

- 账号菜单「我的资源」五项**仍在** `channel/web/chat.html:451-483`（3.3 未实现）；
- 成员控制台入口**仍**要求 `isPlatformAdmin || isTenantAdmin`（`console.js:17543-17547`）；
- 用户默认 `set_user_default` **不存在**，详情「设为默认」仍走租户动作且**清除私有 owner**
  （`auth/service.py:1305-1311`）；
- 控制台内成员页面（`personal-console.js`）仍为独立实现。

## 5. 可复跑证据

```
.venv/bin/python -m pytest tests/test_personal_capability_switches.py tests/test_action_approval_consumer.py \
  tests/test_control_plane.py tests/test_stage2_migration_and_quota_evidence.py -q
→ 103 passed in 18.23s
（既有模拟/合成测试通过，不等于真实提供方或 Desktop 验收）

rg -n 'PERSONAL_RUNTIME_ACCEPTED_TYPES|PUBLIC_PERSONAL_INGRESS_TYPES' channel/channel_instances.py
→ 1276: PERSONAL_RUNTIME_ACCEPTED_TYPES: frozenset = frozenset()
→ 1282: PUBLIC_PERSONAL_INGRESS_TYPES: frozenset = frozenset()

rg -n '"personal_channel_runtime"' auth/policy.py config.py
→ auth/policy.py:546: "personal_channel_runtime": False,
→ config.py:290: "personal_channel_runtime": False,
```
