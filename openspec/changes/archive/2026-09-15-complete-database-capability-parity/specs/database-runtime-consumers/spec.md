## MODIFIED Requirements

### Requirement: 能力投影报告真实开放状态

`/auth/context` 及等价消费者报告 SHALL 将已开放的 chat/files/scheduler/openai_api/channels/mcp、记忆浏览、项目浏览和 Desktop 数据库业务能力按其实际验收范围标为可用，同时将页面和对象的读取、配置、执行分别按当前授权投影。支持范围、部署启用状态、外部连接就绪与当前用户许可 SHALL 分别报告；已开放功能不得继续以固定 `deferred`/`consumer_closed` 表示未实现。投影 MUST NOT 单独授权，每个入口 SHALL 独立重验。

#### Scenario: 能力摘要不再把聊天标为延期
- **WHEN** 有效成员请求 `/auth/context`
- **THEN** chat 及已经验收开放的其他消费者不以 `deferred`/`consumer_closed` 作为不可用原因；缺少权限以权限或资源原因说明

#### Scenario: 定时管理已开放但目标不可运行
- **WHEN** 本人可读取任务，但所选智能体已停用或执行资源未授权
- **THEN** 任务列表和适用的暂停/删除动作保持可用，运行动作说明目标或授权原因，不把整个调度能力标为未实现

#### Scenario: 桌面适配和提供方就绪不同
- **WHEN** Desktop 数据库业务已验收，但某一微信实例尚未连接
- **THEN** Desktop 能力保持真实开放状态，该实例单独报告连接状态，不用一个全局关闭值隐藏两者差异

## ADDED Requirements

### Requirement: 缺口消费者真实路由完成逐项恢复

database 缺口恢复 SHALL 包含 `GET /api/scheduler`、`POST /api/scheduler/run|toggle|update|delete`、`GET /api/memory`、`GET /api/memory/content`、`GET|POST /api/weixin/qrlogin` 和 `GET /api/projects/browse` 共 10 个方法。适用依赖通过后，合法获权请求 SHALL 进入受保护业务服务，不得仅因 database 身份模式返回整体关闭503。开放必须包含真实路由、授权、对象范围与客户端传输，不能仅改变注册策略或模拟 handler 成功。

#### Scenario: 获权用户经真实应用访问恢复入口
- **WHEN** 已完成相应身份、资源及部署条件的用户通过实际 Web 或 Desktop 入口使用上述功能
- **THEN** 请求进入正确作用域的业务路径，结果与对象动作投影一致，未授权调用仍被拒绝

#### Scenario: 只有组件测试通过
- **WHEN** 直接调用某个处理器成功，但实际应用路由仍关闭或客户端缺少必要上下文
- **THEN** 该恢复项验收不通过，不计为功能已覆盖
