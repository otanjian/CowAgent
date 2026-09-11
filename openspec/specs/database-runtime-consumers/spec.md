# database-runtime-consumers Specification

## Purpose
定义 `identity_mode=database` 下 Web 对话、文件/上传、调度、OpenAI 兼容 API、MCP 预热、Agent Bridge 与外部通道启动等运行消费者的开放边界：拆除既有整体关闭门，并要求每次执行按当前身份重新授权。
## Requirements
### Requirement: database 模式开放已适配运行消费者

当 `identity_mode=database` 时，系统 SHALL 开放已适配的运行消费者，包括 Web 消息/流/轮询/取消、文件上传与文件服务、语音 ASR（若部署启用）、调度管理与执行触发、OpenAI 兼容 API、MCP 预热以及 Agent Bridge 运行时初始化。上述入口 MUST NOT 再因「database 模式」本身返回 `503 database_unavailable` 或等价整体关闭码；未登录或无权请求 SHALL 分别返回 `401`/`403` 等身份与授权错误。

#### Scenario: 已登录且获权用户可发起 Web 对话
- **WHEN** 有效租户成员持有 `chat.use` 与目标 Agent 的 `agent.use`，并对所选模型持有 `model.use`，向 Web 对话入口发送消息
- **THEN** 请求进入既有对话执行路径，不因 database 模式被 503 短路

#### Scenario: 匿名访问对话传输
- **WHEN** 匿名客户端请求 Web 对话消息或流式入口
- **THEN** 系统返回 `401`（或等价未认证），不返回 database 整体不可用 503，也不执行模型调用

### Requirement: 能力投影报告真实开放状态

`/auth/context`（及等价消费者可用性报告）SHALL 将已开放的 chat/files/scheduler/openai_api/channels/mcp 等消费者标为可用（`available=true`，无 `deferred`/`consumer_closed` 作为关闭原因），同时页面动作仍按功能权限与资源 grant 投影。投影 MUST NOT 单独授权执行；每个入口 SHALL 独立重验。

#### Scenario: 能力摘要不再把聊天标为延期
- **WHEN** 有效成员请求 `/auth/context`
- **THEN** chat（及本 change 已开放的其他消费者）不以 `deferred`/`consumer_closed` 作为不可用原因；缺少 `chat.use` 等权限时以权限/资源原因说明，而非「版本未开放」

### Requirement: 调度在触发前重验授权

调度任务 SHALL 在创建时记录触发所需的 `user_id`、`tenant_id` 与目标资源标识；每次触发执行前 SHALL 重新解析成员资格、功能权限与资源 grant。重验失败 MUST NOT 调用模型或产生新的工具副作用，并 SHALL 记录可诊断的失败原因。

#### Scenario: 撤权后到点触发
- **WHEN** 任务创建时用户有权，触发前其 `agent.use` 或 `chat.use` 已被撤销
- **THEN** 本次触发被跳过或标记失败，不执行 Agent 运行

### Requirement: 通道按显式实例启动且不限为仅 web

系统 SHALL 按配置与显式渠道实例记录解析并启动已启用的外部 IM 通道，MUST NOT 在启动阶段将通道列表强制过滤为仅 `web`。单通道启动失败 SHALL 记录错误且不得阻止 Web 控制台启动。通道实例 MUST 为显式登记记录，MUST NOT 由旧 `channel_type` 或单租户配置隐式合成。

#### Scenario: 配置含飞书实例时启动
- **WHEN** 部署的配置或实例记录包含已启用的飞书（或其他 IM）通道
- **THEN** 进程尝试启动该通道；失败时记录错误，Web 控制台仍可用

#### Scenario: 未显式登记实例
- **WHEN** 配置只有旧 `channel_type` 字段而没有显式渠道实例记录
- **THEN** 系统不隐式合成实例，不启动该通道

### Requirement: Desktop 按 database 身份适配

Desktop SHALL 通过数据库登录取得独立 AuthSession，并以同一会话值作为 `Authorization: Bearer` 访问业务接口，MUST NOT 继续使用共享密码或旧 `cow_auth_token`。系统 MUST NOT 对 Desktop 开放匿名或免登录入口；旧认证客户端 SHALL 被明确拒绝并提示需要重新登录，MUST NOT 回退 legacy 赋权。

#### Scenario: Desktop 使用数据库账号登录
- **WHEN** Desktop 用户以有效账号通过登录接口认证并携带会话 Bearer 请求业务接口
- **THEN** 系统按该会话解析身份与租户并正常提供服务

#### Scenario: Desktop 旧认证连接
- **WHEN** Desktop 使用共享密码或旧 token 连接服务
- **THEN** 系统拒绝或明确提示需要重新登录，不回退 legacy 赋权

