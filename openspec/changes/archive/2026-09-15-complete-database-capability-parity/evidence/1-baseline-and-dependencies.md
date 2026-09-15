# 1.1-1.2 基线与依赖切片登记

- 变更：`complete-database-capability-parity`
- 记录日期：2026-09-15
- 结论：实施基线、10 个缺口方法、Desktop 传输基线已记录；兄弟 change
  `enable-member-personal-console` 的已交付面按接口/证据逐项登记，本 change 只承接其
  未完成的补强项。

## 1. 真实代码版本与 schema（任务 1.1）

| 项目 | 取值 | 取得方式 |
| --- | --- | --- |
| 实施起点 commit | `e6f1e2a7cb4a6147cd125e7007a1c5982521d76c`（2026-09-14T19:04:28+08:00） | `git log -1 --format='%H %cI'` |
| 工作区状态 | 上一轮个人控制台交付的未提交改动（`git status`） | `git status --porcelain` |
| 冲突基线（历史冻结，不作当前版本） | `origin/master@9ad944dd x origin/rdai@617abfae` | `scripts/conflict-baseline.txt` 头部 |
| 身份库 | 数据库身份为唯一模式（legacy 身份模式已由 `retire-legacy-identity-mode` 退役）；`identity.db` 及其备份未纳入本 change 的可写路径 | `common/state_dir.py`、`auth/store.py` |

`scripts/conflict-baseline.txt` 的冻结哈希只说明当时的合并基线，本 change 的合并与漂移
演练在任务 10.x 重新记录源/目标/merge-base，不引用上述历史哈希作为当前版本证明。

## 2. 十个缺口方法（任务 1.1）

按 `scripts/route-baseline.txt` 的冻结行逐方法登记（下表 `冻结策略` 为实施起点值）：

| # | 方法 | 冻结策略 | 冻结行 | 起点实现情况 |
| --- | --- | --- | --- | --- |
| 1 | `GET /api/scheduler` | closed | :76 | 5 个方法关闭；后台已有 owner snapshot 与触发重验，Web 管理仍是按全部 Agent 聚合的旧路径 |
| 2 | `POST /api/scheduler/run` | closed | :78 | 同上 |
| 3 | `POST /api/scheduler/toggle` | closed | :79 | 同上 |
| 4 | `POST /api/scheduler/update` | closed | :80 | 同上 |
| 5 | `POST /api/scheduler/delete` | closed | :77 | 同上 |
| 6 | `GET /api/memory` | closed | :41 | handler 存在，但个人域/私有 Agent 域解析未做真实归属校验 |
| 7 | `GET /api/memory/content` | closed | :42 | 同上，正文读取按可替换路径打开 |
| 8 | `GET /api/weixin/qrlogin` | closed | :124 | 旧 `_qr_state` 为进程全局单槽，凭据写全局 `conf()` |
| 9 | `POST /api/weixin/qrlogin` | closed | :180 | 同上，且无一次性授权消费与幂等回执 |
| 10 | `GET /api/projects/browse` | closed | :69 | 路由与 handler 双重关闭；个人项目创建/选择已接入 |

`POST /api/projects/manage`、`/api/projects/create`、`/api/projects/select`、
`/api/projects/order`、`/api/memory/personal*` 等相邻入口在起点即为 `tenant`/`personal`，
不属于缺口方法，本 change 只做回归，不重开。

## 3. Desktop 传输基线（任务 1.1）

| 面 | 起点状态 | 证据位置 |
| --- | --- | --- |
| 登录门 | `desktop/src/renderer/src/components/LoginGate.tsx` 使用用户名密码，把会话放在 renderer 的 `localStorage` | 组件源码 |
| 业务请求 | `desktop/src/renderer/src/api/client.ts` 给公共请求层加 Bearer，缺少有效 tenant/epoch | 源码 |
| `/auth/login` | 兼容字段中含 `result.token`，与 identity-session 规范要求的“只设 Cookie”不一致 | `channel/web/auth_handlers.py` |

## 4. 依赖切片登记（任务 1.2）

登记对象是兄弟 change `enable-member-personal-console` 在 2026-09-14 已交付的成果。
引用其证据文件，不复制其 schema、服务或已完成开发任务；53/53 勾选不作为个人执行、
记忆路径安全或索引并发的通过证明（其 9-6 记录明确包含“缺少真实渠道条件时保持关闭”的
分支）。

| 依赖切片 | 实现位置（接口） | 交付证据 | 本 change 的接入方式 | 本 change 是否重复开发 |
| --- | --- | --- | --- | --- |
| 个人目录/菜单迁移 | `auth/policy.py`（`PERSONAL_PAGE_CAPABILITIES`、`personal_page_enabled`）、`channel/web/static/js/personal-console.js` | `9-1-capability-switches.md`、`9-6-final-acceptance.md` | 个人页沿用同一投影，定时/记忆/项目/扫码只增加独立模块与挂载点 | 否 |
| 私有 Agent 生命周期与自建 | `agent/private_agent.py`、`channel/web/web_channel.py` 私有 Agent 管理面 | `9-6-final-acceptance.md`、`3-6-private-maintenance-acceptance.md` | 11.1 联测停用/删除边界；调度不因默认入口复活停用 Agent | 否 |
| 个人记忆 CRUD 与清空 generation | `agent/memory/personal.py`（`PersonalMemoryService`）、`/api/memory/personal*` | `5-5-personal-memory-evidence.md` | 记忆补强（路径隔离、正文/索引/清空发布协议）**由本 change 承接**；旧读接口复用同一服务 | 补强，不重建 |
| 个人渠道配置、密文 owner、配额、绑定挑战、治理停用 | `channel/channel_instances.py`、`auth/service.py` 实例/凭据/配额方法、`channel/web/scan_onboarding.py` 上游之外的绑定服务 | `6-8-personal-channel-evidence.md`、`7-personal-channel-execution.md` | 微信 QR 作为提供方适配接入同一实例与绑定服务；真实执行验收由本 change 承接 | 适配，不重建 |
| 个人工具/技能参数与个人凭据引用 | 个人控制台工具/技能配置面 | `9-6-final-acceptance.md` | 11.2 复用并验收，不改公共配置 | 否 |
| 能力开关 | `auth/policy.py` 五个开关、`config.py` 五个键 | `9-1-capability-switches.md` | 复用同一条事实链（`personal_runtime_enabled()`、部署总开关）；不新增第二份开关 | 否 |

### 4.1 本 change 明确承接的补强项

| 补强项 | 归属任务 | 现状 |
| --- | --- | --- |
| 共享记忆服务真实路径隔离（根/中间目录/条目，含检查后替换） | 5.6 | 由本 change 在 `agent/memory/personal.py` 共同文件访问层实现 |
| 正文/索引/清空的操作版本与 generation 协调 | 5.7 | 由本 change 在 `agent/memory/personal.py`、`agent/memory/manager.py` 实现 |
| 所承诺微信类型及 scope 的真实执行与适用审批消费方 | 7.8-7.10 | 由本 change 承接；缺外部条件时保持未完成，不沿用“关闭即结项” |
| Desktop 身份/租户上下文与原生授权协议 | 8.x | 由本 change 新增接缝实现 |

### 4.2 共享文件与归档顺序

| 共享文件 | 本 change 的改动性质 | 归档顺序约束 |
| --- | --- | --- |
| `auth/capability_matrix.py` | 新增（唯一切片登记） | 本 change 新增，兄弟 change 不引用 |
| `channel/web/route_registry.py` | 逐路由换用切片策略 | 兄弟 change 的规范先合并；本 change 只改 10 个缺口方法的策略来源 |
| `channel/web/web_channel.py` | handler 授权/范围适配 | 不整文件覆盖，保留上游签名与 `_import_local_file` |
| `agent/memory/personal.py`、`manager.py`、`summarizer.py` | 路径与并发补强 | 增量保留兄弟 change 的既有场景，不预占迁移号 |
| `common/startup_hooks.py`、`app.py` | 新增调度任务迁移 hook | `app.py` 只转调 hook |
| `scripts/route-baseline.txt`、`conflict-baseline.txt` | 追加可追溯变化 | 历史条目不删除 |

迁移号分配：`auth/store.py` 的迁移在实施起点已到 `_migration_24`（20 内置角色菜单、
21 个人访问租户策略与实例治理停用、22 成员自建私有 Agent 策略、23 渠道实例外部应用指纹、
24 共享实例的个人路由目标），全部属兄弟 change `enable-member-personal-console` 的
已交付 schema，本 change **不新增也不改写**这些迁移号。调度任务的 owner/scope/revision
是既有 `tasks.json` 的兼容扩展字段，记忆的作用域与操作版本存在于既有个人记忆存储的
兼容字段中，个人项目浏览复用既有项目根解析。
