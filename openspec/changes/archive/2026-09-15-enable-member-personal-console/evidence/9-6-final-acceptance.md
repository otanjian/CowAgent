# 9.6 最终验收记录与可启用范围

- 变更：`enable-member-personal-console`
- 日期：2026-09-14
- 结论：**规格与实施验收记录完成**；任务按真实完成情况勾选（52/53 → 本文件落地后 53/53）。
  可启用范围见第 4 节；未通过切片见第 5 节，且**保持关闭/未声明开放**。

## 1 规范校验

| 命令 | 结果 |
| --- | --- |
| `openspec validate enable-member-personal-console --strict` | `Change 'enable-member-personal-console' is valid` |
| `openspec validate --specs --strict` | `Totals: 71 passed, 0 failed (71 items)` |
| `openspec spec validate rbac-authorization --strict` | `Specification 'rbac-authorization' is valid` |

9.5 同步后的规范口径（owner 正文唯一可达、管理员仅治理元数据）与变更内 delta 一致；
两处仍以「管理员可读」命名的 delta 场景已改名，归档时不会把旧口径带进主规范。

## 2 实施验收：后端（本 change 直接相关用例，最终一次全量）

```
.venv/bin/pytest <28 个文件> -q
→ 722 passed, 3 subtests passed（152.6s）
```

覆盖文件：`test_personal_capability_switches.py`、`test_personal_channel_binding.py`、
`test_personal_channel_console.py`、`test_personal_channel_inbound.py`、
`test_personal_console_menu.py`、`test_personal_console_multi_tenant_authorization.py`、
`test_personal_console_web.py`、`test_personal_console_acceptance.py`、
`test_personal_memory_console.py`、`test_personal_resource_config.py`、
`test_personal_instance_policy.py`、`test_personal_memory_tool_execution.py`、
`test_personal_assistant_owner_fields.py`、`test_private_agent_owner_actions.py`、
`test_private_agent_lifecycle.py`、`test_private_agent_file_scope.py`、
`test_private_agent_delete_conflicts.py`、`test_private_agent_quota.py`、
`test_private_agent_web_gate_ordering.py`、`test_private_resource_acceptance.py`、
`test_tenant_channel_instances_migration.py`、`test_tenant_channel_instances_service.py`、
`test_user_personal_agent_provisioning.py`、`test_agent_clone_binding.py`、
`test_personal_delivery_drill.py`、`test_route_registry.py`、`test_http_policy.py`、
`test_user_personal_agent_provisioning.py`。

更宽的公共面/记忆/渠道/智能体回归见 `9-3-regression-and-route-baseline.md`
（渠道 181 + 7 subtests、智能体/目录/RBAC 246、记忆/渠道/知识 284）。

## 3 实施验收：前端与真实浏览器

| 用例 | 结果 |
| --- | --- |
| `tests/test_personal_console_frontend.cjs` | pass 56 / fail 0 |
| `tests/test_console_i18n_parity.cjs` | pass 5 / fail 0 |
| `tests/test_console_view_registry.cjs` | pass 6 / fail 0 |
| `tests/test_tenant_channel_card_frontend.cjs` | pass 28 / fail 0 |
| `tests/test_execution_permission_ui.cjs` | pass 5 / fail 0 |
| `tests/test_personal_console_browser.cjs`（真实 Chromium 契约） | **11 scenarios passed**，无 page error、无未预期请求 |

环境说明（不属变更内容）：本机 Playwright 包与 `~/Library/Caches/ms-playwright` 中的浏览器构建号曾不一致
（测试所用包期望 `chromium_headless_shell-1237`，缓存中只有 `-1243`）。已改为**用该包自身的 CLI 安装匹配构建**：
`node <playwright-core>/cli.js install chromium-headless-shell`（落地 `chromium_headless_shell-1237`），
不再依赖任何符号链接，契约在默认缓存下复跑仍为 11 scenarios passed。
换机复现只需先跑一次上述安装命令。

## 4 可启用范围（真实结论）

| 切片 | 开关 | 默认 | 结论 |
| --- | --- | --- | --- |
| 个人入口与目录 | `member_personal_console` | 开 | 可启用 |
| 私有智能体生命周期 | `user_private_agent_management` | 开 | 可启用 |
| 个人记忆写入 | `personal_memory_write` | 开 | 可启用 |
| 个人渠道配置（凭据/绑定/配额/治理停用） | `personal_channel_onboarding` | 开 | 可启用 |
| 个人渠道执行 | `personal_channel_runtime` | **关** | 未通过，保持关闭 |

口径提醒：**「配置可保存」不等于「渠道已连接」**。成员保存个人渠道配置后，
运行时会明确报 `applied=false, pending=true`（已保存、未连接），不会谎报成功。

## 5 未通过 / 保持关闭的切片（不得声明已开放）

1. **个人渠道真实执行**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES`
   均为空，总开关默认关闭；需先完成至少一种渠道的真实端到端验收（任务 7.5 未验收分支已记录待验收范围：
   飞书 / 企业微信 / 钉钉）。
2. **动作审批（action approval）真实消费方**：开启个人执行前必须补齐（9.2 记录 Q2）。
3. **全仓单进程 `pytest` 收集**：`scenes/config.py` 与顶层 `config.py` 同名冲突导致 104 个收集错误，
   HEAD 上即存在，与本 change 无关；本 change 验证按文件/分组执行。
4. **`tests/test_sidebar_account_frontend.cjs` 的 5 个 sidebar account 旧断言**：HEAD 上即失败，非本 change 引入。

## 6 任务勾选说明（按真实完成情况）

- 阶段 1–8 全部完成并有证据；`7.5` 勾选的是其**「未提供真实渠道条件时保持个人执行开关关闭并准确记录待验收范围」**分支，
  并非声明已通过真机验收。
- 阶段 9.1–9.6 全部完成：能力开关、前置切片复核、双租户授权与路由基线回归、交付演练、
  文档与规范口径同步、最终验收记录。
- 未通过的部分以**能力开关关闭 + 第 5 节明确记录**的方式保留，不隐藏、不等同于「已覆盖」。

## 7 证据索引

`1-1`、`1-2`、`1-3`、`1-4`、`2-0`、`2-7`、`3-6`、`5-5`、`6-8`、`7`、`8`、`9-1`、`9-2`、`9-3`、`9-4`、`9-6`（本文件）；
交付/运维说明见 `docs/design/member-personal-console-delivery.md`。
