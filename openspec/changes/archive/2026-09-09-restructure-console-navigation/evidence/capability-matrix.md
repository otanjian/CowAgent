# 菜单 → 页面 → 接口 → 权限/作用域 → 运行模式 → 开放状态矩阵

复核日期：2026-09-08。此表是 navigation-baseline.md 的扩展，按 `legacy` / `database` 两种模式记录同一菜单条目的可用性依据。所有“开放状态”均为本 change 实施前的**静态消费者摘要**（`_consumer_availability`），不代表功能已验收；`database` 下未适配消费者仍关闭。

## 图例

- **消费者状态**：来自 `/auth/context` 的 `consumers`（`_consumer_availability`）。`web_identity_admin` 为本 change 依赖切片中唯一 `available=True` 的消费者。
- **权限/作用域**：`platform`=平台范围；`tenant`=当前租户；`self`=本人；`agent`=智能体相关；`none`=无专用权限键（页面静态或由消费者控制）。
- **database 页面可达性**：`登记存在 ∩ 消费者开放 ∩ 当前身份允许读取`。消费者未开放 → 默认关闭。

## 矩阵

| 菜单 (viewId) | 读取接口 | 写入接口 | 权限/作用域 | legacy | database 消费者 | database 页面状态 |
| --- | --- | --- | --- | --- | --- | --- |
| chat | `/chat`(session) | `/chat`(send) | self | 可用 | `chat: deferred` | 关闭 |
| history | 会话列表接口 | 无写 | self | 可用 | 同聊天/会话 | 关闭 |
| agent-workbench | `/agents` 目录 | `/chat`(launch) | agent | 可用 | `chat: deferred` | 关闭 |
| scenarios | 无 | 无 | none | 占位 | — | 移除 |
| todo | `/todos` | `/todos` | self | 可用 | `tools: deferred` | 关闭 |
| tasks | `/schedules` | `/schedules` | self | 可用 | `scheduler: deferred` | 关闭 |
| config | `/config` | `/config` | platform | 可用 | `web_identity_admin`(部分) | 按职责拆分 |
| agents | `/agents` 详情 | `/agents` | agent | 可用 | — | 关闭（未开放消费者） |
| skills | `/skills` | `/skills` | agent | 可用 | `tools: deferred` | 关闭 |
| memory | `/memory` | `/memory` | agent | 可用 | `tools: deferred` | 关闭 |
| knowledge | `/knowledge` | `/knowledge` | agent | 可用 | `files: deferred` | 关闭 |
| channels | `/channels` | `/channels` | platform | 可用 | `channels: deferred` | 关闭 |
| logs | `/logs` | 无写 | platform | 可用 | 未接入消费者 | 关闭 |
| platform | 无（占位） | 无 | — | 占位 | — | 移除（待系统设置验收） |
| tenant | `/auth` + 身份接口 | 身份写 | tenant/platform | 可用 | `web_identity_admin: available` | **可用** |
| system_user | `/auth` + 成员接口 | 身份写 | tenant/platform | 可用 | `web_identity_admin: available` | **可用** |
| roles | `/auth` + 角色接口 | 身份写 | tenant/platform | 可用 | `web_identity_admin: available` | **可用** |
| org | `/auth` + 组织接口 | 身份写 | tenant/platform | 可用 | `web_identity_admin: available` | **可用** |
| branding | `/branding` | `/branding` | platform | 可用 | 未见消费者登记 | 关闭（按品牌切片） |
| audit | 无（占位） | 无 | — | 占位 | — | 移除 |
| backup | 无（占位） | 无 | — | 占位 | — | 移除 |
| open_api | 无（占位） | 无 | — | 占位 | — | 移除 |

## 关键结论

1. **仅身份管理四页**（tenant/system_user/roles/org）在 database 模式下有 `web_identity_admin: available` 消费者摘要；其余业务消费者均 `deferred`。因此 B 阶段 `console_pages` 投影应优先覆盖身份页并保持其余关闭。
2. **无有效租户时**：`/auth/context` 依赖租户选择；当前 `_ensureTenantSelected` 允许平台管理员（无 Membership）返回 `platform` 直达 `tenant`。本 change 收紧为受限恢复。
3. **读取 ≠ 写**：`menu_config` 读取 `/config`，但 `web_password` / `agent_permission_mode` 写入受平台/模式限制；`#/admin/models` 子页需独立能力键。
4. 侧栏 5 个占位（`scenarios/platform/audit/backup/open_api`）无页面容器，删除后需为**已知未开放目标**提供说明与返回，且不得删除已验收的全局账号/身份审计子视图。
