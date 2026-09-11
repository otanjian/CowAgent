# Change方案复核与优化记录

复核日期：2026-09-08。对象：`complete-enterprise-identity-access-control`。结论：原方案的身份安全方向合理，但将用户/角色管理与资源授权、模型选择、所有运行消费者、SSO、Desktop传输及CLI扩展绑定交付，明显超过本次管理闭环需要。已直接优化文档，未修改产品实现，也未将任何实现任务标记完成。

## 1. 本次范围决策

本次保留统一鉴权、账号安全、管理员事务、租户/成员/角色/组织四页、身份审计，以及当前九权限目录治理。原来的八份能力规范收口为四份，需求从53项收口为27项，场景从125个收口为73个；实现任务从61项收口为32项，全部未勾选。

移出的能力是**明确延期**，不是已经解决或等价替代：角色/成员资源grants、模型策略、完整运行消费者、SSO、Desktop企业登录/传输，以及新CLI账号命令。它们的后续起点和必要安全约束见design延期表；对应四份spec移出当前change，不提前创建五张表或占位接口。原始OneAgent差距报告保留长期方向，但不再作为本次A～D全部交付的承诺。

## 2. 详细发现与处理

下表优先级表示问题对原方案的影响，处理状态均为“方案已修改”，不表示代码缺口已修复。

| 问题 | 原方案的不足与证据 | 已采用的优化 |
| --- | --- | --- |
| P1：事务描述不能防止旧密码并发签发 | `_tx()`只返回连接；`login()`先查用户验密码，再由独立SessionStore创建会话。重置可能先撤销旧会话，随后旧密码登录又签发新会话。见[service.py](/Users/jiantan/ai_assistant/cowagent/auth/service.py:459)。 | 明确短BEGIN IMMEDIATE、同连接重验版本/hash/账号与适用会话、条件更新、会话签发/撤销和审计。密码计算在锁外，提交顺序必须通过受控并发验收。 |
| P1：直接执行期限检查可能锁住初始化管理员 | bootstrap设置must_change_password但不写期限；旧测试allow_weak跳过受限流程。唯一管理员自我重置也可能因响应丢失或过期而失去管理入口。见[bootstrap](/Users/jiantan/ai_assistant/cowagent/auth/service.py:159)。 | 新bootstrap写期限并做真实首登改密验收；升级前须有已完成改密的有效管理员，旧NULL期限账号先在旧流程改密。禁止管理接口自我重置，自我停用/降级须另有非受限平台管理员，不增加恢复CLI。 |
| P1：现有重验及管理员短路不能兑现撤权 | `revalidate_context`不验证AuthSession并复制旧平台/受限状态；通用guard存在tenant_admin放行。只改默认权限集合无法修复。见[runtime.py](/Users/jiantan/ai_assistant/cowagent/auth/runtime.py:138)、[auth_handlers.py](/Users/jiantan/ai_assistant/cowagent/channel/web/auth_handlers.py:157)。 | 每请求从权威入口读取当前session/user/tenant/member/角色，写前同事务再验；移除通用功能权限短路，身份管理资格与资源owner仍各有独立检查。 |
| P1：真实入口与管理安全不能被精简掉 | legacy鉴权先行，租户资料/审计读权限和管理写来源存在缺口，原方案修复方向正确。见[web_channel.py](/Users/jiantan/ai_assistant/cowagent/channel/web/web_channel.py:253)、[admin_handlers.py](/Users/jiantan/ai_assistant/cowagent/channel/web/admin_handlers.py)。 | 保留统一生产/测试应用工厂及方法策略、默认拒绝、字段投影、CSRF、受限会话、临时期限、审计事务；没有为了任务少而删掉安全验收。 |
| P2：一个change绑住多条产品线 | 原61项任务包含文件预览、离线调度委托、OpenAI人类认证、SSO全生命周期、Desktop全传输、未点名外部通道；仅调度当前任务存储就没有方案所需委托模型。 | 收口A/B，其他能力显式延期，当前管理闭环不等待凭据/配额/执行沙箱全项目；后续消费者按实际适用切片验收。 |
| P2：通用grants早于资源标识 | 技能主要按name/key、模型按全局供应商配置识别，方案预设统一租户resource_id并不成立。角色/成员双授权来源与四层模型priority又反向耦合成员新增/恢复/撤权。 | 本次不建grants/model_policies。后续先选真实消费者，明确技能来源和provider/model键；模型默认只作偏好，不阻止身份维护，按需增加层级。 |
| P2：技能读取与执行权限矛盾 | 原文“目录、装配、执行同条件”会让有skill.read、无执行grant的审阅者看不到技能，与read/use区分冲突。 | 延期设计明确同一判定器按action检查，目录read与装配/执行use分开；不把文件复制当授权。 |
| P2：Desktop企业登录变成传输层重写 | 当前客户端分别处理JSON、FormData、EventSource和资源URL；http-relay只有HTTPS/整包文本/10秒/8MB边界，不能直接携带企业会话代替全业务传输。见[client.ts](/Users/jiantan/ai_assistant/cowagent/desktop/src/renderer/src/api/client.ts)、[http-relay.ts](/Users/jiantan/ai_assistant/cowagent/desktop/src/main/http-relay.ts)。 | 原生登录与Desktop成套延期；后续先固定后端的窄JSON IPC，其他传输逐项适配。当前明确不可用且不回退legacy，不把renderer内存token偷换成已满足主进程隔离。 |
| P2：SSO与消息通道关联没有证明 | 解绑撤销“关联会话”缺绑定来源存储；钉钉消息sender与SSO subject不是已证明的同一标识。 | 后续SSO限定provider/流程并补会话来源或明确全账号撤销代价；消息通道须单独证明映射，不以SSO完成推导通道可用。 |
| P2：双凭据一律拒绝扩大兼容代价 | 现Web登录同时发Cookie和token，Desktop请求同时带Cookie/Bearer。完全拒绝会把合法重复凭据也变成故障。 | 仅同值允许并按Cookie校验来源，不同值400且不回退；新Web只发Cookie。既有合法Bearer至到期/撤销仍可验证，Desktop企业新登录延期必须明确提示。 |
| P2：重复接口和高级UI增加维护面 | q/department/page、create-new/bind-existing早已实现，原计划又列完整建设；角色复制/成员关联可复用；“近期验证”容易引入票据状态机，“比较草稿”暗含diff/合并。 | 只补status/role筛选与role_codes，复制读取+create、关联共用成员列表、树由平面部门生成；recent_password每次请求复核，409提示并重载，不做比较/合并/草稿存储。 |
| P2：限流、逐事件重验与重复验收成本未限定 | 每请求创建IdentityService会重置对象内计数；逐token完整权限解析会放大多连接查询；阶段间重复全量验收和纯UI等待全部安全实现不必要。 | 应用生命周期共享有界计数，单进程部署基线、拒绝事件聚合；长流优化延期，保留可测撤权检查点要求；开发按真实依赖并行，发布仍联合验收，一张证据表只重跑受影响项。 |
| P2：沿用旧聊天基线会重复实施或误开运行 | 首次63/6时聊天在首行503；本次message/poll/stream/cancel已有owner检查，37项相关测试通过，但Bridge仍在database模式提前返回。 | 更新基线并区分局部handler进展与真实运行验收。不撤关闭保护、不以mock成功宣布聊天可用；其他change后续有真实证据时按消费者协调。 |

## 3. 保留的简单设计

- 继续使用Python/web.py、SQLite、User→Membership→租户角色和现有AuthSession，不增加认证服务器、策略DSL或事件总线。
- 当前九权限补说明、显式默认集及功能guard，管理员身份写资格继续独立；个人待办、私有owner及合法共享范围不变。
- 保留一个小型 `/auth/context`，仅返回本人所选租户权限/资格/消费者状态；不依赖tenant.info.read，不把所有租户的权限计算塞进/me，也不另建权限同步平台。
- 用户全局停用、平台管理员调整和重置密码的三个接口是实际缺口，保留近期密码的当前请求复核、版本控制、最后管理员和会话撤销。
- 四页基于现有Web组件修复，三语/主题/移动按代表组合验证。安全发布门槛保留，实现工作不人为串行。

## 4. 复核证据与未完成项

本次使用隔离测试库运行以下既有测试，未对真实账号/业务执行修改：

| 命令 | 结果与边界 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_chat_identity_context.py tests/test_web_chat_boundary.py tests/test_web_consumer_closure.py` | 37 passed，1条既有setDaemon弃用警告；证明选定身份/归属/关闭单元边界，不是生产聊天运行验收。 |
| `.venv/bin/python -m pytest -q tests/test_identity_auth_security.py tests/test_identity_policy.py tests/test_identity_service_writes.py tests/test_identity_self_context.py tests/test_tenant_read_scoping.py tests/test_revocation_takes_effect.py` | 55 passed；既有回归通过不覆盖新增登录/重置竞态、全部来源分支和新增平台账号页面。 |

新增要求的实现任务全部未勾选。后续必须新增能实际重现并发提交、跨来源绕过、管理员连续性和故障回滚的行为验收；不能通过修改旧测试预期代替修复。本次仅运行与复核有关的选定测试，不声称全仓回归、浏览器验收或部署完成。

最终检查：四份spec共27项需求、73个场景；32项实现任务编号唯一且全部未勾选。`openspec validate complete-enterprise-identity-access-control --strict --no-interactive`通过，`openspec status`显示四项规划产物齐全。文档格式通过不作为任何功能启用证明。
