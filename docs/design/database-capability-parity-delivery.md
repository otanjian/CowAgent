# 数据库能力补齐——交付、迁移与运维说明

日期：2026-09-15。对应 OpenSpec change：`complete-database-capability-parity`（已于 2026-09-15 **部分归档**）。
本文是**交付/运维层面**的说明：哪些切片已经开放、凭什么开放、如何撤下、如何恢复、哪些仍未通过。
已归档切片的**行为契约以主规范 `openspec/specs/` 为准**；未取得验收的 Desktop 与微信执行验收以
`openspec/changes/complete-desktop-and-scan-real-acceptance/specs/` 为准。
本文只作操作与证据索引（证据见
`openspec/changes/archive/2026-09-15-complete-database-capability-parity/evidence/` 与
`openspec/changes/complete-desktop-and-scan-real-acceptance/evidence/`）。

---

## 1 一句话结论

database 身份模式下原先整体返回 503 的 **10 个方法**已经恢复，且**逐方法**有真实路由、真实授权、
真实对象范围与真实客户端行为；另外补齐了共享记忆服务的文件归属与并发、个人项目的受控本机导入、
微信扫码的会话绑定状态机以及 Desktop 的身份/租户上下文接缝。

**唯一未通过的切片是 `desktop_tenant_context`**：其后端协议已实现并有测试，但任务 8.7 需要真实打包
Desktop 客户端跑完两租户两成员全链路，本轮未执行，因此在注册表中保持 `accepted=false`、
`open={}`（路由仍关闭，理由 `awaiting_acceptance`，不是永久 `deferred`）。

同一条外部依赖也压住微信的**执行面**：任务 7.8 需要真实提供方扫码/连接/收发，
`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 因而已验为空集
（**配置面**已开放，见第 4 节）。适用动作审批消费者（任务 7.9）本轮**已交付**，
部署开关 `approval_required_actions` 出厂为空，见第 10 节。

本轮同时修掉三处产品缺陷（微信扫码 handler 崩溃、记忆页拒绝无终态、成员可经删除入口抹除系统供应助理），
修复与复验记录见 `evidence/9-route-and-ui-availability.md` §6 与 `evidence/11-plan-3-1-joint-acceptance.md` §4。

**归档状态（2026-09-15，部分归档）**：本 change 按“已验收切片”归档——`database-scheduler-console`、
`database-memory-console`、`scoped-project-browser`、`channel-scan-onboarding`（配置面与适用审批消费方）、
`database-runtime-consumers`、`console-navigation-availability`、`fork-upstream-decoupling` 共 7 个 capability
增量已合并进 `openspec/specs/`。`desktop-tenant-context` 全部 requirement 与「微信个人执行按实际类型与作用域
独立验收」两条增量因未取得真实客户端/提供方验收**未合并主规范**，连同任务 7.8/7.10/8.7/8.8/11.3/R2 整体移交
`complete-desktop-and-scan-real-acceptance`。第 2、10 节的“未通过”项因此仍以未通过为准，主规范中不含它们的
已实现声明。

## 2 开放登记（唯一切片来源）

开放与否**只由** `auth/capability_matrix.py` 声明，三个消费者读同一份声明：

| 消费者 | 读法 |
| --- | --- |
| 路由与 HTTP 闸门 | `channel/web/route_registry.py` 经 `S(slice_id, action)` → `Slice.route()`；未开放动作得到 `closed`，闸门在 handler 之前就 503 |
| 消费者可用性（`/auth/context`） | `IdentityService._consumer_availability()` 合并 `capability_matrix.consumer_availability()` |
| 页面投影（`console_pages`） | `IdentityService._console_pages_projection()` 读 `page_availability()` 与 `page_states()`，逐页报告 read/config/execute |

| 切片 | 能力 | 开放动作（访问类） | 页面 | 状态 |
| --- | --- | --- | --- | --- |
| `scheduler` | 定时管理 | `list`(read) `toggle`/`update`/`delete`(config) `run`(execute) | `workbench.schedules` | 已开放（R1 真实工具分发闭环） |
| `memory_browse` | 记忆浏览兼容入口 | `list`(read) `content`(read) | `admin.memory` | 已开放 |
| `project_browse` | 项目浏览与受控导入 | `browse`(read) `import`(execute) | 无（工作台内入口） | 已开放 |
| `weixin_scan` | 微信扫码接入 | `qr`(config) `poll`(config) | 无（渠道页内扫码入口） | 已开放（**仅配置**；执行另见第 4 节） |
| `desktop_tenant_context` | Desktop 数据库业务 | 无 | 无 | **未通过**（8.7 未演练） |

- **未开放动作 = 路由 `closed`**：闸门先于 handler 返回 503 `database_unavailable`，不依赖 handler 自觉。
- **开放不等于授权**：注册表只回答「这个功能是否被服务」。每次调用仍然跑会话/租户、owner、Agent、
  资源权限与配额检查（`evidence/1-3-authorization-matrix.md`）。
- **页面与接口不会互相打架**：`read/config/execute` 分开投影，`page_states()` 与路由取自同一 `open` 映射；
  已开放页面不再出现固定 `deferred`，未开放页面报告切片自己的理由（如 `awaiting_acceptance`）。

## 3 恢复的 10 个方法

| 方法 | 策略与权限 | handler / 服务 |
| --- | --- | --- |
| `GET /api/scheduler` | `tenant` | `channel/web/web_channel.py::SchedulerHandler` → `agent/tools/scheduler/authorization.py` |
| `POST /api/scheduler/run` | `tenant`（execute，需 R1 验收） | 同上（幂等 `run_key`） |
| `POST /api/scheduler/toggle`、`/update`、`/delete` | `tenant` | 同上（config 与 execute 分开：撤权后仍可暂停/删除本人任务） |
| `GET /api/memory`、`/api/memory/content` | `tenant` + `memory.read` | `channel/web/memory_console.py`（复用 `agent/memory/personal.py`） |
| `GET /api/projects/browse` | `tenant` | `channel/web/project_import.py` 与项目 handler（`common/safe_fs.py` 逐次重解析） |
| `GET|POST /api/weixin/qrlogin` | `tenant` | `channel/web/scan_onboarding.py` |

逐方法来源与历史值见 `scripts/route-baseline.txt` 的 append-only「evolution」段（旧 `closed` 行保留）。

## 4 支持范围、客户端与提供方

- **scope**：`personal`（本人对象）与 `tenant`（租户公共对象）分开判定；任务归属不因使用公共 Agent 改变，
  公共任务单独判定（`openspec/specs/database-scheduler-console`）。
- **客户端**：Web 控制台（含 Workbench 定时页、记忆页、渠道页、项目浏览）与 Desktop。
  Desktop 的 Web 侧业务面与 Web 共用同一套路由、页面动作与错误契约；Desktop 原生协议（授权码 + PKCE
  + 主进程 broker + 窄化 IPC）已实现，但 8.7 的真实客户端演练未执行（见第 7 节）。
- **Agent 工具**：`scheduler` 工具的 `create/list/get/delete/enable/disable` 与 Web、后台循环**共用同一个**
  `TaskAccessService`，所以三个入口不会给出不同答案（`evidence/3-scheduler-management.md`）。
- **微信提供方**：承诺的适配器是 `weixin`（`channel/channel_instances.py` 的个人接入目录与租户目录共用同一份声明）。
  本次交付的是**配置面**：扫码状态机按 actor/会话/tenant/scope/owner/provider/目标绑定，授权消费、实例与凭据写入、
  配额占用、审计、幂等回执在同一事务提交，微信兼容字段不回显长期密钥。
  **执行面仍未开放**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 仍为空集，
  且部署总开关 `personal_channel_runtime` 默认关闭——在真实提供方往返被观察到（任务 7.8）之前，
  「配置已保存」不等于「渠道已连接」。

## 5 个人项目导入与「只是浏览」的区别

- **浏览**（`GET /api/projects/browse`，read）：只列出本人当前 tenant+user 项目根内的目录，相对标识 + 面包屑 +
  有界父级；旧的绝对路径写法会被归一化而不是整体关闭；每次请求都由 `common/safe_fs.py` 针对已验证身份重解析，
  软链接与检查后替换都会被拒绝。
- **导入**（`POST /api/projects/import*`，execute）：由独立接缝实现，采用上游 `_import_local_file` 所声明的
  同一契约（loopback 限制与每启动令牌校验；该上游函数尚未落入本树，义务记于 `scripts/conflict-baseline.txt`），
  并叠加数据库身份与一次性用途：
  - `preview` 发一次性句柄（本机路径由 Desktop 原生选择器给出，服务端不解释任意服务器路径；远程后端改用受保护上传）；
  - `import` 消费同一句柄，先暂存再原子发布，配额**预占**，目标冲突直接拒绝，绝不静默覆盖已有项目；
  - `cancel` 只作用于本人句柄，取消后不可再用；失败有补偿并释放配额。

## 6 历史任务隔离与恢复

启动钩子 `HOOK_SCHEDULER_TASK_MIGRATION`（`common/startup_hooks.py`，由 `app.py` 转调）在**任何调度循环启动之前**
把存量任务分类一次：

- 已有 owner 的任务补 `scope=personal`，`id`/`schedule`/`action` 与上游字段原样保留（不会二次开跑）；
- 已有显式 `scope` 的任务不动；
- 既无 owner 又无显式公共 scope 的任务**隔离**：停用（`enabled=false`）+ `quarantine.reason` 与恢复提示，
  绝不归给当时执行启动的管理员；
- 幂等：第二次启动全部 `unchanged`；中断后直接重跑即可（未处理的任务仍是未分类且**不执行**状态）。

运行期还有一道后备：`agent/tools/scheduler/identity.py::revalidate_owner` 在 database 模式下把无归属任务判为
`unattributed` 并跳过，因此即使有人手工把隔离任务改回启用，它也不会执行。

**操作员恢复步骤**（把隔离任务交回某个成员）：

1. 读 `<agent state dir>/tasks.json`，任务上是 `quarantine.reason = "no_owner"`；
2. 与该成员核对任务内容确实属于他（审计与历史会话是判断依据，不要凭管理员印象）；
3. 写入该成员的 `owner`（`user_id`/`tenant_id`）与 `scope="personal"`，**删除** `quarantine` 字段，
   按需设 `enabled=true`；
4. 重启或等下一次启动，迁移会把它记为 `unchanged`（已有 owner 与显式 scope）。

**写前一致备份**：迁移在写入之前对将被修改的 store 做一次快照（`tasks.json.bak-migration-<stamp>`），
只保留最近 `TASK_STORE_BACKUP_KEEP = 5` 份；备份内容与写入前一致，
由 `tests/test_scheduler_task_migration.py::test_the_boot_hook_backs_up_the_store_before_it_writes` 证明。

## 7 部署与恢复

1. **升级 / 重启**：就地升级，无 schema 迁移（本 change **不新增** `auth/store.py` 迁移版本，沿用兄弟 change
   的 1–24）。直接重启即可；中断后直接重启续做。
2. **撤下某项能力**：改 `auth/capability_matrix.py` 里该切片的 `open`（把动作移出即回到 `closed`），
   或撤下个人侧开关（`auth/policy.py` 的 `PERSONAL_CAPABILITY_SWITCHES`，由 `config` 提供部署值）。
   撤下只会收窄：关掉动作后闸门先拒，页面投影同时改为报告切片自己的理由，不会出现「页面已开而接口 503」。
3. **停止微信连接**：撤 `personal_channel_runtime`（默认即关）。已登记的类型验收不能越过总开关，
   已保存的配置仍在、仍可读，成员仍能撤回自己的凭据。
4. **恢复旧版本的红线**：**不得**回退到会忽略 owner/scope 字段、会恢复「缺省 owner 直接执行」或
   「管理员读取成员私有内容」旁路的旧构建。若必须回退，只能在维护窗口按一致备份恢复，并明确处置：
   任务 store（`tasks.json` 及其 `quarantine` 字段）、个人记忆根与 `.memory-scope.json`、
   个人渠道实例与 `personal_channel_links`。恢复后的不变量：
   **无归属任务不执行**、**不复活 legacy 无身份执行路径**、**不复活管理员私有读取旁路**。
5. **审计**：调度动作（含拒绝）、记忆浏览拒绝、项目导入发布/取消、扫码授权的消费与回执、
   Desktop 会话建立与撤销都落 `audit_events`；敏感内容脱敏，审计失败不留下「有对象、无审计」的中间态。

## 8 证据索引

下表路径相对 `openspec/changes/archive/2026-09-15-complete-database-capability-parity/`；第 8 组（Desktop）
的后续验收证据见承接方 `openspec/changes/complete-desktop-and-scan-real-acceptance/evidence/`。

| 组 | 内容 | 证据 |
| --- | --- | --- |
| 1 | 基线、依赖登记、授权矩阵、前置证据 | `evidence/1-baseline-and-dependencies.md`、`1-3-authorization-matrix.md`、`1-4-prerequisite-evidence.md` |
| 2 | 接缝与唯一能力登记 | `evidence/2-seams-and-registry.md` |
| 3 | 本人定时任务管理（HTTP/工具/后台同一服务） | `evidence/3-scheduler-management.md` |
| 4 | 历史任务迁移与备份 | `evidence/4-scheduler-task-migration.md` |
| 5 | 记忆浏览与个人记忆路径/并发补强 | `evidence/5-memory-browse.md` |
| 6 | 项目浏览与受控本机导入 | `evidence/6-scoped-project-browser.md` |
| 7 | 微信扫码实例适配 | `evidence/7-scan-onboarding.md` |
| 8 | Desktop 身份与租户上下文 | `evidence/8-desktop-tenant-context.md` |
| 9 | 路由与界面开放一致性 | `evidence/9-route-and-ui-availability.md` |
| 10 | master → rdai 合并保全与回归 | `evidence/10-master-merge-preservation.md` |
| 11 | 产品规划 3.1 联合验收 | `evidence/11-plan-3-1-joint-acceptance.md` |
| 12 | 校验与归档 | `evidence/12-validation-and-archive.md` |
| 13 | 本轮审查补充验收门槛（R1-R5） | `evidence/13-review-supplementary-gates.md` |

## 9 适用动作审批（`approval_required_actions`）

`agent/approval_gate.py` 把"哪些动作需要一次单动作审批"做成**部署声明**，并在真实分发接缝上消费：

- 动作标识：`tool:<工具名>`、`scheduler:<动作类型>`、`channel:<动作>`；多个动作以逗号或空格分隔，
  写入 `config.py` 的 `approval_required_actions`（**出厂为空**，因此交付行为不变）。
- 消费位置：Agent 工具分发（`agent/protocol/agent_stream.py`，Bridge 创建的 Agent 走同一条）与定时消息投递
  （`agent/tools/scheduler/integration.py`）。两处都在**身份、隔离、资源授权与配额之后**判定；
  未带审批的调用被拒绝且**不执行**，参数里的审批引用（`__approval_id`）在调用前被剥离。
- 绑定：审批在请求时绑定目标与规范化参数摘要；执行时必须是同一请求人、同一租户、同一动作、同一目标、
  同一参数，且**单次消费**（并发只有一个能成功）。待批/拒绝/撤回/过期/已消费/换参/换目标/他人审批
  都有稳定的机器码，全部落审计（脱敏，不含任务内容）。
- 操作建议：仅对已确认需要人工二次确认的对外副作用动作声明；撤下声明即恢复原行为（只收窄，不加宽）。

## 10 尚未通过的切片与任务（不得声明已开放）

- **Desktop 数据库业务（`desktop_tenant_context`）**：后端协议已实现，`accepted=false`，路由保持关闭。
  需要真实打包客户端完成两租户、两成员、失效身份与重连演练（任务 8.7 / R2）后才能开放与更新
  `desktop_enterprise` 兼容投影。
- **个人渠道执行（含微信真实收发）**：`PERSONAL_RUNTIME_ACCEPTED_TYPES` 与 `PUBLIC_PERSONAL_INGRESS_TYPES` 为空，
  总开关默认关闭；需要有真实提供方的端到端验收（任务 7.8）后才按证据更新类型集合（任务 7.10）。
- **真实 Desktop 客户端演练（任务 8.7/8.8）与 R2**：远程 HTTPS 形态、兑换响应丢失后的重试、
  旧后端拒绝降级与实机全链路均未演练，因此不更新 `desktop_enterprise` 兼容投影。
- **归档（任务 12.3，已按“部分归档”执行）**：原前置是“全部必要依赖与验收完成”，按用户决策改为按已验收切片
  归档。已归档的 7 个 capability 增量已合并 `openspec/specs/`，并用
  `scripts/check_change_deltas.py complete-database-capability-parity`（applied 模式）验证主规范与增量一致；
  未验收的 Desktop 与微信执行增量**未合并**，留在承接方 change。
- **残留验收的承接方**：`desktop_tenant_context` 的真实客户端演练（前序 8.7/8.8/R2）与微信真实执行验收
  （前序 7.8/7.10）及其联测（前序 11.3）由 `complete-desktop-and-scan-real-acceptance` 的任务
  2.1-2.2、3.1-3.3、4.1 承接。在取得真实验收前，本文不声明这两块已开放，承接方也不得放宽判据。
