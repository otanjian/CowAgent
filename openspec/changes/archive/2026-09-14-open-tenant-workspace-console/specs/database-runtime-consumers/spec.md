## MODIFIED Requirements

### Requirement: database 模式开放已适配运行消费者

当 `identity_mode=database` 时，系统 SHALL 开放已适配的运行消费者，包括 Web 消息/流/轮询/取消、文件上传与文件服务、语音 ASR（若部署启用）、调度管理与执行触发、OpenAI 兼容 API、MCP 预热以及 Agent Bridge 运行时初始化。上述入口 MUST NOT 再因「database 模式」本身返回 `503 database_unavailable` 或等价整体关闭码；未登录或无权请求 SHALL 分别返回 `401`/`403` 等身份与授权错误。

文件服务中的浏览器传输 SHALL 在已登录且获权的成员下可用，包括聊天附件上传与回读，以及 Agent 生成文件工件的下载与预览；其中浏览器原生发起、结构上无法携带租户头的请求（`<img>`/`<a download>` 等）SHALL 由被寻址资源派生租户，MUST NOT 以「未选择租户」400 拒绝，MUST NOT 静默失败。

控制台工作区面板的传输（`GET /api/workspace/tree|search|resolve|meta|read`、`POST /api/workspace/write`）SHALL 对已登录且获权的租户成员可用：浏览、预览与保存本租户工作区文件 MUST NOT 因 database 模式返回 `503`，MUST NOT 以「未选择租户」400 拒绝（控制台同源 `fetch` 会携带租户选择），也 MUST NOT 以成功状态静默吞掉拒绝。

#### Scenario: 已登录且获权用户可发起 Web 对话

- **WHEN** 有效租户成员持有 `chat.use` 与目标 Agent 的 `agent.use`，并对所选模型持有 `model.use`，向 Web 对话入口发送消息
- **THEN** 请求进入既有对话执行路径，不因 database 模式被 503 短路

#### Scenario: 匿名访问对话传输

- **WHEN** 匿名客户端请求 Web 对话消息或流式入口
- **THEN** 系统返回 `401`（或等价未认证），不返回 database 整体不可用 503，也不执行模型调用

#### Scenario: 已登录且获权成员下载或预览本租户 Agent 工件

- **WHEN** 有效租户成员在控制台点击其本租户 Agent 生成文件的下载按钮，或在预览面板打开该文件
- **THEN** 下载导航与预览请求分别返回文件内容，不因租户头缺失返回 400 missing_tenant，也不显示 not found

#### Scenario: 已登录成员使用工作区面板

- **WHEN** 有效租户成员在控制台对话页打开右侧工作区面板，浏览文件树、预览文件或保存编辑
- **THEN** 请求分别返回成功，不返回 `503 database_unavailable`；未登录时返回 `401`，无有效租户成员资格时返回 `403`
