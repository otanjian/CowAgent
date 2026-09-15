## Why

当前仅支持 database 身份模式，但 legacy 可用的定时任务管理、记忆浏览、微信扫码接入和已有项目目录浏览仍有 10 个路由方法保持关闭，Desktop 也缺少完整租户上下文传递。2026-09-15 对照最新代码确认，`enable-member-personal-console` 已交付个人控制台、私有智能体生命周期、个人记忆 CRUD 和个人渠道配置；这些能力应复用，不再作为待从零实现的依赖。个人记忆的真实路径边界及保存/清空/索引并发仍需补强，个人渠道真实执行和适用审批消费方尚未交付。需要按 `doc/产品规划.md` 3.1 补齐剩余能力，并把 master 独立演进后合入 rdai 的兼容性作为交付条件。

## What Changes

本轮审查补充五项交付约束：SchedulerTool 与 HTTP/后台共用对象授权；Desktop 采用主进程持有会话的授权码加 PKCE 协议；微信不回传长期密钥的规则不扩展到现有飞书流程；扫码授权区分同一操作的幂等结果回读与新操作重放；上游独立运行与 rdai 强制授权接缝缺失使用不同的回退规则。这些约束均须落实到规范、实现任务及验收门槛，不能仅作为实现建议。

- 开放当前租户内本人定时任务的列表、运行、启停、编辑和删除，保留既有 scheduler 与任务格式，通过统一授权接缝补入 owner、资源授权、并发及运行重验。
- 恢复记忆列表与正文浏览，复用已交付的个人记忆管理服务；在原共享服务补齐真实路径归属、软链接使用时校验及正文/索引/清空的并发协调。本人记忆与私有智能体记忆明确分区，不复制 CRUD；新旧入口共同验收，不能只修新接口。
- 将微信扫码改为发起者、有效租户、实例作用域及用途绑定的独立注册会话；复用显式渠道实例、密文凭证、一次性创建授权及身份绑定服务，支持按职责接入公共或个人渠道。
- 恢复个人项目范围内的目录浏览、已有项目选择及受控本机目录导入。**BREAKING**：旧宿主机任意绝对路径不再是可授权资源，平台管理员也不能通过项目浏览访问成员私有内容；本机导入必须经过原生选择、loopback、每启动令牌及数据库身份边界。
- 补齐 Desktop 的账号登录、强制改密、有效租户选择、Bearer 与租户上下文传输、租户切换失效处理和个人入口，使上传、流、预览及下载同样遵守资源范围。租户 gate 仅阻断租户业务；完成改密的零租户账号仍可使用账号设置，获权平台管理员仍可进入平台管理，不伪造 Membership。
- 统一路由、消费者状态、页面及对象动作的开放依据；已适配功能不再因 database 模式整体拒绝，未适配切片、无权操作和运行条件不足分别报告。
- 将已交付的个人智能体生命周期、个人渠道配置及身份挑战、个人记忆 CRUD、个人工具技能配置登记为复用基线及联合验收项，不复制身份模型或接口。本 change 明确承接共享记忆服务补强、所承诺微信类型及 scope 的真实个人执行验收，以及该执行路径适用的动作审批消费方；不再把这些剩余工作悬挂到已结项的兄弟 change。
- 将 fork 逻辑放入独立授权、handler、前端与 Desktop 上下文模块，保留上游接口与行为；新增 master 到 rdai 的变更识别、合并接缝、冲突处置和双侧回归门槛。

## Capabilities

> **归档说明（2026-09-15，部分归档）**：本 change 按“已验收切片”归档。下表记录的是提案时的范围；
> 其中 `desktop-tenant-context` 未取得真实打包客户端验收，其规范增量**未合并主规范**，已连同
> `channel-scan-onboarding` 的「微信个人执行按实际类型与作用域独立验收」移交
> `complete-desktop-and-scan-real-acceptance`。详见 `evidence/12-validation-and-archive.md` §12.3。

### New Capabilities

- `database-scheduler-console`：本人任务管理、受控公共任务范围、并发控制、迁移隔离和触发前后授权。
- `database-memory-console`：记忆浏览兼容入口、真实个人归属、个人记忆服务衔接及全入口隐私边界。
- `scoped-project-browser`：个人项目目录浏览、选择和受控本机导入，路径与对象授权一致。
- `desktop-tenant-context`：Desktop 身份生命周期、租户与请求上下文、传输和页面能力适配。

### Modified Capabilities

- `database-runtime-consumers`：缺口路由逐项开放及与真实服务一致的消费者状态。
- `channel-scan-onboarding`：微信的多作用域注册、隔离会话、一次性接入、凭证事务和提供方能力验收。
- `console-navigation-availability`：缺口页面与动作恢复、database 唯一身份语义，以及个人控制台依赖的联合开放证据。
- `fork-upstream-decoupling`：恢复能力的独立接缝、上游语义保全、每次 master 更新后的合并与真实授权回归。

## Impact

权威依据为现有 `openspec/specs/`，本 change 以 delta 落实产品规划 3.1；不以历史任务勾选或本提案生成证明能力已经开放。

涉及 Web 路由与 handler、`auth` 授权和能力投影、scheduler 接缝、记忆服务、项目空间、微信适配器及 Desktop 认证和传输。保留 Web/Desktop/Channel、Agent Registry/Bridge、AuthSession、业务 session、ExecutionRun 与 scheduler 的既有职责。身份、成员与私有归属仍归身份域，任务正文仍归既有 TaskStore，记忆和项目仍归可信个人/智能体工作区，渠道密钥仍归既有密文凭据存储。

主要复用基线为 `enable-member-personal-console` 已交付的 owner 优先规则、个人记忆服务、个人渠道配置及身份挑战。其 53/53 勾选包含“个人执行保持关闭”的完成分支，不代表所有依赖通过；个人记忆安全/一致性补强及个人执行剩余工作由本 change 实现并取证。凭据、审计、硬配额、隔离和适用审批仍按真实消费路径验收，缺少证据的执行切片保持关闭，已交付配置与其他独立能力不因此退回未实现。

未来合并重点包括 `channel/web/web_channel.py`、`channel/web/static/js/console.js`、`channel/web/chat.html`、`desktop/src/renderer/src/api/client.ts`、`channel/channel_instances.py`、`agent/tools/scheduler/integration.py`。设计另登记 `app.py`、`agent/memory/conversation_store.py`、`tests/test_scheduler_web_update.py` 等既有接缝的保全义务。提案阶段只新增本 change 产物，不执行分支合并、数据库迁移或业务实现。
