## MODIFIED Requirements

### Requirement: 场景数据接口

系统 SHALL 提供 `GET /api/scenes` 返回场景分类与场景列表。接口 SHALL 在请求作用域内执行：SHALL 要求已选择有效租户（解析失败按门禁既有顺序拒绝），并 SHALL 要求调用者持有 `chat.use`（场景目录用于为会话选择场景，属 chat 消费者面；平台/租户管理员通过）。`scenes_config.json` 缺失或解析失败 SHALL 返回空结构（`scenes: []`, `categories: []`）而非 500。系统 MUST NOT 提供共享密码或免登录的旁路访问。

#### Scenario: 正常返回场景
- **WHEN** 已选择租户且持有 `chat.use` 的调用者请求 `GET /api/scenes` 且配置存在
- **THEN** 返回 `status: success`，含过滤后的分类与场景列表

#### Scenario: 缺少 chat.use
- **WHEN** 已选择租户但未持有 `chat.use` 的成员请求 `GET /api/scenes`
- **THEN** 返回 403，不返回目录数据

#### Scenario: 缺少有效租户
- **WHEN** 调用者没有任何有效租户上下文
- **THEN** 按门禁既有顺序拒绝，不返回目录数据

#### Scenario: 配置缺失或解析失败
- **WHEN** `scenes/scenes_config.json` 不存在或 JSON 解析失败
- **THEN** 返回空结构且 `status: success`，不抛出未捕获异常
