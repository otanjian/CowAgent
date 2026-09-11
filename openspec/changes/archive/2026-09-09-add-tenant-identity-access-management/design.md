## Context

本次按用户要求优化尚未实施的 change，产品代码不在本轮修改范围。原稿将四个管理模块扩展成完整业务运行时改造，包含 59 项任务；主要复杂度来自自行引入的邀请、权限委派、部门数据范围、多端运行接入及全量搬迁，不能视为最简方案。

### 代码证据与可复用部分

| 位置 | 已有事实 | 本次处理 |
| --- | --- | --- |
| `channel/web/chat.html`；`static/js/console.js` 的 VIEW_META | 四菜单存在，导航仍为占位 | 接通视图及统一请求封装，沿用原生 JS/i18n |
| `channel/web/web_channel.py` 的 `_check_auth`、`AuthLoginHandler` | 当前仍是共享密码/HMAC、布尔鉴权 | database 模式接独立 AuthSession；legacy 路径保留 |
| `common/runtime_identity.py` | ContextVar 及 submit/wrap 已存在 | 增加 tenant_id、人工 user_id；不建立第二套上下文机制 |
| `agent/registry.py` 的 AgentRegistry | Agent ID 全局唯一，workspace 已去重 | 保持 ID 和索引，不改成租户内可重名 |
| `common/state_dir.py` | 路径统一解析，但 shared/base 会回默认 Agent | 基于已验证租户解析共享根，关闭跨租户和无上下文回退 |
| `agent/memory/conversation_store.py` | 每 Agent 独立库，runs 已有 user_id | 仅补必要 owner；不全表新增 tenant/department |
| `agent/team.py`、conversation store、Desktop Registry | 部分错误会回配置、默认 Agent 或 home | database 下读取失败即拒绝，不继续旧容错路径 |
| `bridge/agent_bridge.py`、scheduler/evolution、ChannelManager | 构造或启动会触发后台消费 | 在实际 start/init/restart 入口阻止未适配消费者 |
| `channel/web/branding.py`、品牌 handler | 品牌文件存储已出现，写鉴权仍需企业适配 | 已发布最小品牌读取可保留，database 品牌写暂时拒绝 |

OneAgent 仅作源码参考：`../oneagent/auth/models.py` 是单 tenant_id 用户、全局 Role 和 department 文本；`auth/rbac.py` 有集中目录但 admin 全放行；`auth/tenant_context.py` 有租户路径但缺上下文时会回全局。采用集中认证、RBAC、路径收口的做法，补齐 Membership 与后端边界，不复制其 JSON 读改写、单次 SHA256 密码、全局 admin 绕过或运行数据。

## Goals / Non-Goals

**Goals:** 四个 Web 管理页形成可用闭环，真实支持多个租户、同账号多租户和可信资源隔离；优先复用现有代码，采用一次维护窗口迁移；用明确测试验收最小边界。

**Non-Goals:** 一期不提供邀请/自助注册、SSO、多部门多岗位、部门数据权限、普通角色的授权委派、跨租户共享、组织业务数据迁移、租户归档/删除、通用能力编排平台，也不接通完整模型/通道/调度运行。

### 优化决策与功能边界

| 原稿设计 | 优化后 | 结果/延期条件 |
| --- | --- | --- |
| 三类邀请、个人接受、初始化/恢复邀请状态机 | 企业管理员按精确账号直接绑定成员；平台直接恢复管理员 | 删除邀请表/接口，不能修改已有账号密码或读取其他租户资料；需自助入驻再加邀请 |
| 服务端 current_tenant + context_version + 多标签广播 | 每标签选择租户，请求显式传入，逐请求验证 Membership | 删除会话租户状态与版本协议；保留本地 generation 防晚到响应 |
| 普通角色委派及授权谓词子集求解 | 仅 tenant_admin 可管理成员/角色/组织 | 删除委派算法，不能通过自定义角色成为管理员 |
| 5 类数据范围、多部门岗位、资源部门标签 | 部门树 + 成员单部门 + 岗位文本；固定个人/租户共享资源策略 | 部门不授权，删除跨库引用冻结/转移；部门数据权限后续明确业务需要再做 |
| 每资源加 tenant_id，租户内重复 Agent ID | Agent 全局 ID + 租户绑定，沿用每 Agent 独立库 | 少改存储和索引；个人数据仍校验 owner |
| 全量目录复制、索引重建、待认领流程 | 默认租户登记现有目录及初始管理员，异常项阻止切换 | 不移动正常数据；只补必要归属字段 |
| 审计 outbox、投递消费、积压治理 | 身份变更和 append-only 审计同 SQLite 事务 | 满足本期真实内部审计，不引入跨服务交付 |
| 流票据、预览 capability、Desktop/外部接入全改造 | 首期这些消费者在 database 模式关闭 | 删除本期实现任务；后续按消费者验收后放行，不能只隐藏菜单 |

这里既有实现简化，也有明确范围延期。首期是身份管理与受控读取版本，不能宣称已有完整企业聊天、自动执行或多端运行能力。

## Decisions

### 1. 一个身份库，少量独立模块

新增 `auth/store.py`、`auth/service.py`、`auth/policy.py`、`auth/session.py`，权限目录可放 policy 中；初期不拆出通用 repository、策略插件、事件总线。SQLite 使用事务、外键、唯一约束和有界锁等待，密码使用版本化 PBKDF2-HMAC-SHA256 与随机盐，沿用标准库，无新服务框架。

| 数据 | 必要字段/关系 | 唯一真值 |
| --- | --- | --- |
| users | id、规范化 username、display_name、password_hash、active、is_platform_admin、must_change_password、临时密码到期、version | 全局账号；平台仅一种管理员标识，不另建平台角色体系 |
| tenants | id、不可变 code、name、active、shared_root、default_agent_id 可空、version | 稳定租户和共享根；不持久化 provisioning/archived |
| memberships | id、tenant_id、user_id、display_name、active、department_id 可空、position_text、version | `(tenant,user)` 唯一；一个部门，不复制全局身份 |
| roles | id、tenant_id、code、name、builtin、permissions_json、version | 固定权限目录内的集合；不使用用户自定义通配符/数据范围 |
| membership_roles | tenant_id、membership_id、role_id | 同租户复合外键 |
| departments | id、tenant_id、parent_id、code、name、sort_order、active、version | 租户内编码、同级名称唯一；虚拟根不可移动/删除 |
| auth_sessions | token_hash、user_id、expires_at、revoked_at、restricted | 独立于业务 session，无当前租户或权限快照 |
| agent_bindings | 全局唯一 agent_id、tenant_id、private_owner_user_id 可空 | 唯一租户绑定；null 表示显式租户共享，历史绑定初始管理员 |
| audit_events | id、time、actor、tenant/target_tenant、action、target、redacted_changes、result | 本期内部审计唯一存储，应用接口仅追加/受限查询 |
| schema_migrations | version、applied_at | 小型数据库迁移记录，无文件批次搬迁 journal |

Agent workspace、模型等配置继续由既有 Registry 配置拥有，不能在身份库复制另一份。Registry 的租户属性从 agent_bindings 投影；未绑定 Agent 在 database 不可见。租户和 Agent ID 由服务端生成，禁止用户指定目录。普通成员无需平台角色；一个 User 可以拥有多个 Membership。

### 2. 管理员直接管理，取消邀请与委派

适用企业内部管理员维护账号的模式。tenant_admin 可创建全新账号及本租户成员，也可按完整账号名直接绑定已有 User；绑定仅生成当前租户关系，不修改全局账号、密码、平台标识或其他成员资格，不提供跨租户模糊用户搜索。已有 inactive Membership 返回冲突，必须通过独立状态更新恢复，不能通过再次添加偷偷启用。

平台管理员创建租户或恢复租户管理员走平台控制面，可绑定已有有效用户或明确创建新用户；操作需要近期密码验证并记录审计。平台身份本身不产生 Membership，不获得业务数据访问。停用租户仍允许平台调整管理员；至少一个有效管理员就绪后才能单独恢复租户，不需要恢复邀请例外。

只保留内置 tenant_admin/member，内置角色不可直接修改/删除。tenant_admin 是已登记租户权限的管理者，可管理本租户资源；member 及自定义角色只按固定业务权限访问。仅有效 tenant_admin 能写成员、角色、组织，自定义权限不能授予该管理资格。平台管理员标识只能经平台接口或受信引导维护。

首期目录固定为 tenant.info.read、tenant.members.read、tenant.org.read、agent.read、history.read、knowledge.read、memory.read。member 默认获得 tenant.info.read 及四个受控业务读取权限，仍受个人/共享归属过滤；成员/组织名录读取可由管理员额外授予。自定义角色只能从这些只读目录与业务权限中选择；身份写能力由内置 tenant_admin 资格决定，不做成可拼装权限点。

所有成员/角色/部门修改都带 expected_version，在事务内重验授权、引用与最后管理员条件。每个 active 租户至少有一个 active 用户的 active tenant_admin Membership，实例至少有一个 active platform_admin；约束覆盖全局用户停用、成员停用和角色降权及并发。停用租户可没有当前可用管理员，但恢复前必须补齐。

新账号提供短期临时密码，只在生成响应展示一次；到期前可反复取得仅改密/退出的受限会话，成功改密后临时密码及全部旧会话失效。已有全局密码仅本人验证旧密码后修改或由平台管理员重置。取消“首次登录即消费”的状态及恢复分支。

### 3. 登录与每标签租户选择

`identity_mode=legacy|database` 默认 legacy，database 必须账号密码登录；沿用 `/auth/login/check/logout` 的原字段，增量返回账号、有效租户和权限信息。数据库随机不透明会话仅存摘要，Web 用 HttpOnly/SameSite Cookie、HTTPS Secure、写请求 CSRF 和来源校验；不把密码/长效 token 放 URL。认证失败统一错误与限流；身份库失败返回 503，不回退旧共享密码。

当前租户保存在标签页 sessionStorage，每个请求在发起时固定 `X-Tenant-ID`，后端读取有效 User、Membership、Tenant 和当前角色再授权。该 header 只是选择参数；缺少、格式错误或多个来源不一致返回 400，非成员/停用租户返回 403。个人和平台接口不需要租户选择；平台请求仍验证平台身份，不能把任意请求自报租户当授权事实。

登录一个有效租户自动选择，多个供选择，零个只显示个人设置/退出；平台账号仍可进入平台管理。切换前处理未保存草稿，随后取消请求、清理当前页数据、关闭订阅并递增本地 generation；仅同 generation 响应可显示，缓存键含 tenant/user，覆盖 A→B→A 晚响应。各标签互不影响；已经提交到 A 的操作仍可能在 A 完成，切换不承诺服务端取消。

每请求直接解析权限，无权限缓存版本系统。退出撤销当前 AuthSession；改密/全局账号停用撤销该用户全部会话；成员停用只影响该租户。首期 database 不开放 SSE/poll/运行控制及 Desktop 运行入口，因此不建设 stream-ticket 或 fetch 流适配器；legacy 的既有 Desktop/Bearer 接口保持。后续客户端适配需采用 Cookie 或认证 header，并保留撤权检查，不能恢复 URL 长效 token。

### 4. 四个页面与最少接口

| 菜单 | 交互 | 权限 |
| --- | --- | --- |
| 系统设置 / 租户 | 平台列表、名称/编码/状态、创建、编辑、停用/恢复、管理员配置；普通成员只读当前租户 | 平台管理员操作生命周期；tenant.info.read 查看自身租户 |
| 系统设置 / 用户 | 当前成员查询、按部门筛选、新建/绑定、改名、停用/恢复、角色和部门/岗位配置 | tenant_admin 写；目录读取按 tenant.members.read |
| 系统设置 / 角色权限 | 内置标识、权限分组复选框、自定义角色创建/修改/复制/删除、成员关联 | tenant_admin 管理；不提供自定义授权委派或数据范围编辑 |
| 系统设置 / 组织架构 | 部门树、名称/编码/排序/状态、成员列表、移动部门；成员岗位是文本字段 | tenant.org.read 读取；tenant_admin 写 |

部门是纯组织信息。移动仅检查同租户、有效父级及无环；停用/删除前先移走下级及成员，存在引用就拒绝，不提供级联迁移。职位文本和部门都不自动增加角色/业务可见性，不新增业务 department_id，也不做负责人/审批人联动。

沿用 `VIEW_META` 与静态 view，新建一个 identity-admin.js 和公共请求函数即可；加载/空态/错误重试、403、409 重载及未保存提示复用组件，保留三语、深浅主题及移动布局。错误保存不更新为成功；不建设独立管理前端工程。

| 路径族 | 操作 |
| --- | --- |
| `/auth/login`、`/auth/check`、`/auth/logout`、`/auth/password` | 认证、个人租户列表、改密，无切换租户写接口 |
| `/api/platform/tenants[/{id}]`；`/{id}/admins` | 查询/创建/元数据和 active 更新、管理员配置 |
| `/api/platform/users[/{id}]`；`/{id}/reset-password` | 平台全局账号管理，近期重新认证保护敏感操作 |
| `/api/tenant` | 当前租户只读信息 |
| `/api/tenant/members[/{id}]` | 显式 create-new/bind-existing，名片、状态、角色与单部门/岗位整体更新 |
| `/api/tenant/roles[/{id}]`；`/api/tenant/permissions` | 角色及固定目录；分配角色使用成员整体更新 |
| `/api/tenant/departments[/{id}]` | 部门树和增改删；parent_id 更新即移动 |
| `/api/identity/audit` | 平台或本租户管理员按自身范围查询身份审计，不实现审计菜单全产品 |

列表用 q/status/page/page_size（上限 100），每种写请求只实现需要的 verb。创建依靠账号/租户编码/成员唯一约束；响应丢失后按相同唯一键查询已创建对象，不构建通用幂等键存储，也不再次返回临时密码。租户创建先生成不对外可见的独立目录，再事务建立租户/管理员/内置角色/组织根和审计；任一失败不出现 active 租户，无需持久化 provisioning。未被数据库引用的空目录可由运维清理，不能靠扫描目录发现租户。

### 5. 最小资源隔离，复用现有存储

数据路径取自可信租户绑定 + Registry，不能取客户端路径。默认租户登记现有 shared/workspace；新租户根与身份数据库放在默认租户可读树之外。跨租户 canonical root 不得相等或互相包含，允许同租户已有默认 workspace 与 Agent 子目录嵌套；shared/base 和直接 workspace 参数都执行归属验证。database 缺 tenant/Agent/映射或读取损坏立即拒绝，禁止回全局默认、配置兜底或 `~/cow`。

保留 Agent 全局 ID，租户过滤后才能列举/取默认 Agent；没有 Agent 的新租户返回空列表，不自动选平台默认 Agent。本期新 Agent 配置仍由受信运维维护，注册和绑定完成才可见；不增加租户 Agent 编辑器/跨库配置事务。租户绑定不可经普通 API 重分配。

| 资源 | 本期策略/最少改动 |
| --- | --- |
| Agent 概览 | 绑定 tenant；历史 Agent private_owner=初始管理员，新建绑定需显式选择个人/租户共享；只返回名称/描述等安全字段，不返回 workspace、配置或凭据 |
| 会话/消息/runs | 先校验 Agent 租户，再校验 owner；会话补 owner_user_id，runs 复用 user_id；普通成员只读本人，tenant_admin 固定可读本租户已登记记录，无可配置数据范围 |
| 知识/记忆 | 历史 Agent 资产默认归初始管理员私有，普通成员只有在该 Agent 明确共享且有权限时可读；个人记忆按 tenant/user 解析；tenant_admin 可读本租户已登记资产/个人记忆；限现有内容列表/读取和不调用模型的文本查询 |
| 文件/项目/预览/上传 | database 首期关闭通用文件及预览接口，不引入资源 capability 或任意目录选择；知识正文读取只能走已验证知识服务，不能退为任意路径下载 |
| runtime 缓存与控制 | 运行消费者关闭，保留原 Agent/session 缓存实现；后续开放时再验证主体授权、流、cancel/steer 与缓存边界 |

租户 shared 仅指本租户内明确可共享的业务资产，不包括模型密钥、MCP 配置或任意目录。每个列表/搜索/总数先授权再分页；不能因“每 Agent 一个库”省略同租户个人数据校验。

共享回退必须检查实际资产来源：shared_root 的可见性沿租户 default_agent_id 对应绑定判定；缺来源绑定时拒绝读取。消费 Agent 被设为共享，不能顺带公开默认 Agent 的私有 shared 资产；同租户嵌套 Agent 路径也须按实际来源检查，不能只校验最外层目录。历史私有规则限制普通成员，tenant_admin 对本租户内容的固定管理读取能力是明确例外，平台管理员不享有此例外。

### 6. 首期允许入口与延期边界

采用一份代码级路由分类表：public、identity、platform、tenant-read、disabled，未分类受保护路由默认拒绝。它是固定策略表，不是新建可配置 readiness 服务。不能按 GET 或前端菜单统一放行。

受控业务读取候选明确为 `GET /api/agents`、`GET /api/sessions`、`GET /api/history`、`GET /api/memory`、`GET /api/memory/content`、`GET /api/knowledge/list`、`GET /api/knowledge/read`，均需逐项完成上述租户/私有归属和安全响应裁剪后才能放行；同路径的写方法不随 GET 放行。其余 graph/action/import、Agent 核心文件/头像等仍关闭。历史序列化只返回已授权消息和安全字段，不调用运行时重建助手、签发 artifact 预览链接或暴露宿主绝对路径。

| database 首期允许 | database 首期拒绝 |
| --- | --- |
| 登录/个人设置、四模块管理与身份审计 | 模型调用、消息生成、SSE/poll/cancel/steer、工具执行 |
| 授权后的安全 Agent 概览、历史及知识/记忆内容读取（普通成员本人/共享，tenant_admin 本租户） | 文件下载/预览/上传、任意项目目录、向量 embedding 调用 |
| 健康检查、必要静态资源、已发布最小品牌内容 | 原始 config、全局日志、备份/升级、品牌管理写、凭据/插件/MCP 管理 |
| 已明确分类的同源 Web 页面 | OpenAI API、外部通道、scheduler/evolution、未适配 Desktop 运行入口 |

运行消费者同时在 HTTP/API 和实际后台启动/重启位置关闭；不能只停止页面入口或跳过一次 warmup。模式切换需停应用、排空工作、关闭消费者并重启。Web 启动页在 database 默认进入有权管理页，普通成员进入受控历史/内容页；关闭项标记“当前版本未开放”，不让客户端反复调用旧聊天流。运行时兼容承诺指 legacy 接口保持，不能把 database 首期说成全功能兼容。

后续业务 change 按实际消费者接入：模型用量需 10B，外部凭据需 08S，外部动作需 09G，任意代码需执行隔离；适用切片必须真实通过才可从 disabled 转为允许。完整 Desktop/通道/机器主体/调度和安全预览在各自消费者接入时实现，本次不新建这些 change 或隐含完成它们。

### 7. 最小真实审计与并发

PRD-10A 身份管理切片直接在 identity.db 的 audit_events 同事务追加成功事件，和账号、成员、角色、部门变更共同提交。包含操作人、租户/目标租户、动作、目标、脱敏前后差异、时间、结果；不含密码/哈希/令牌。数据库或审计插入失败全部回滚，API 返回 503。身份审计受限查询、权限拒绝记录、备份恢复和脱敏必须真实测试；不能以 mock/普通 run.log 当验收。

同库内没有待投递队列或第二审计真值。后续统一审计可读取/适配这份记录，但不得为了其他领域的文件写操作宣称已有跨存储事务。对象版本冲突 409；无有效登录 401、非成员/操作无权限 403、越租户对象与不存在一致 404。

## Risks / Trade-offs

- [首期业务范围较窄] → 保留原模式，database 明确关闭未适配功能；后续逐项恢复，不用减少测试替代隔离。
- [管理员直接绑定不包含用户加入确认] → 适用于内部受信管理员模式，精确账号绑定、不能接管密码、不可见其他租户、全程审计；需要自助入驻时另加邀请。
- [默认租户原地登记存在目录嵌套] → 跨租户根互含预检，不合格项阻止切换；不得用 home/父目录作为开放文件根。
- [部门不提供数据权限] → 界面仅表述组织归属，业务按固定个人/共享策略；需要部门访问控制时新增明确资源契约。
- [后台可能从构造函数启动] → 对实际启动位置和重启路径做拒绝测试，database 切换必须重启，无残留消费者。
- [PRD 原文缺失] → 以本轮明确范围记录当前依据，恢复原文后核对映射及归档冲突，不将虚构 PRD 编号作为实现前置。

## Migration Plan

1. 预检账号/目录/Agent/会话现状，选定默认租户和初始管理员；检测跨租户路径、损坏配置和无法判断的归属。异常项明确修复前阻止切换，不自动分享或搬迁。
2. 维护窗口停进程及全部消费者，备份配置、业务库和文件；通过本机命令创建身份库、初始平台/租户管理员和默认租户。
3. 在数据库迁移中登记现有 Agent、shared 根及私有归属，补会话 owner 和既有 run 的缺失 user 字段；保持 ID、目录和索引内容，不复制全量业务数据。重复执行依据版本/唯一约束确认，不重复建实体。
4. 验证默认租户读取和旧凭据失效；以两个租户、同用户多成员、相同 session 字符串和不同全局 Agent ID 验证隔离。通过后重启启用 database；新租户正常创建，不另建“第二租户特权开关”。
5. 原地登记前失败可恢复快照；database 已有新写入后保留新数据并恢复相容版本。身份库已有有效迁移标识时禁止启动 legacy 直接读取新数据；回到迁移前必须维护窗口完整快照恢复，不按当前租户数量猜测可降级。

## Operations & Deferred capabilities (任务 4.7)

### 版本操作 / 迁移说明（精简）

- `identity_mode=legacy|database`，默认 `legacy`。切换须维护窗口重启，不提供运行时热切换。
- 启用 database：
  1. 预检：同一 workspace 下的 Agent、会话 owner、shared/base 目录归属无歧义；否则阻止切换（不自动分享/搬迁）。
  2. 维护窗口停止全部通道与消费者，备份 `config.json`、业务库（`index.db` 等）与 workspace 文件。
  3. 执行 `cow management bootstrap`（创建默认租户、初始平台管理员、tenant_admin/member），随后 `cow management register` 原地登记现有 Agent、补会话 owner 与必要 `run.user_id`。
  4. 写 `config.json` 的 `identity_mode=database`，重启。合法迁移后不再改回 `legacy`。
- 回退：`database` 已有新写入后，保留新数据并恢复相容版本；回到迁移前必须用维护窗口的**完整快照**恢复，不能把 `identity_mode` 改回 `legacy` 直接读（见下方单点确认）。
- 迁移重复执行依据 `schema_migrations` 版本与唯一约束保持幂等，不重复建立租户/成员/归属。

### 单点确认：禁止静默启动 legacy

启动时 `app._guard_identity_mode_consistency()` 校验：当 `identity.db` 已带有效迁移标识（`schema_migrations` 有记录）而配置仍为 `legacy` 时，服务拒绝启动并提示改用 `database` 或恢复迁移前快照。此检查不因当前租户数量放宽。

### 延期能力清单（database 模式下仍关闭）

未适配的首期消费者在 database 模式下由服务端关闭（503 / 构造短路），后续按「凭据、审批、配额、执行隔离切片」验证后逐项开放：

- 模型执行/对话（`/message`、`/stream`、`/poll`、`/cancel`）
- 文件上传/服务（`/upload`、`/api/file`）
- OpenAI 兼容 API（`/v1/chat/completions`）
- scheduler / evolution / 其它后台消费者（`AgentBridge` 构造时短路，不初始化 registry/router/agent 实例）
- 品牌/凭据/插件/MCP 管理写路径（`/api/branding` 等写接口）

legacy 模式保留上述接口原行为；database 模式仅开放身份管理与受控读取接口（四个管理视图 + `/auth/*` + `/api/identity/audit` + 七个业务 GET 的隔离读取）。

## Open Questions

密码工作因子、限流阈值及会话/临时密码期限在底座任务的基准测试中固化，不改变本方案架构。PRD 编号待原文恢复核对；本次实现范围已经明确。
