# scene-activation Specification

## Purpose
TBD - created by archiving change port-scene-applications. Update Purpose after archive.
## Requirements
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

### Requirement: 场景激活接口

系统 SHALL 提供 `POST /api/scenes/activate`，接收 `scene_id` 与 `session_id`。接口 SHALL 在请求作用域内执行：database 模式下 SHALL 要求已选择有效租户并持有 `chat.use`，写操作 SHALL 经统一的「管理写」来源/CSRF 校验（cookie 凭据须来源匹配；bearer 凭据须真实通过认证方可豁免）。接口 SHALL 查找场景（含在子场景中查找，并合并父场景元数据），将场景上下文写入该会话，并 SHALL 使该会话现有 Agent 实例失效以便下次构建时注入场景提示词。

会话场景上下文 SHALL 按租户命名空间存放与读取：`session_id` 由客户端生成，跨租户持有同一字符串 MUST NOT 读取或覆盖他人租户的场景上下文。`scene_id` 或 `session_id` 缺失 SHALL 返回错误；未找到场景 SHALL 返回错误；激活成功 SHALL 返回场景上下文。

#### Scenario: 激活顶层场景
- **WHEN** 提交存在的顶层 `scene_id` 与 `session_id`
- **THEN** 场景上下文写入本租户命名空间，现存 Agent 实例被移除，返回 `status: success` 与场景

#### Scenario: 激活子场景
- **WHEN** 提交某场景的 `sub_scene` id 且存在于配置
- **THEN** 合并父场景元数据（parent_id/parent_name/skill_name）后写入会话并返回

#### Scenario: 相同 session_id 的跨租户隔离
- **WHEN** A 租户为某个客户端生成的 `session_id` 激活场景
- **THEN** B 租户以同一 `session_id` 读取场景上下文得到未激活状态，且 B 租户既有的同类激活不被 A 租户覆盖

#### Scenario: 缺少参数或场景不存在
- **WHEN** 未提交 `scene_id`/`session_id`，或 `scene_id` 在配置中不存在
- **THEN** 返回错误信息，不写入会话状态

#### Scenario: 跨来源激活被拒绝
- **WHEN** cookie 凭据的激活请求来源与请求 Host 不匹配
- **THEN** 返回 403（`csrf_failed`），且不写入任何会话场景状态

### Requirement: 场景提示词注入

系统 SHALL 在会话激活场景后，把场景的 `system_prompt` 作为额外指令注入到该会话 Agent 的完整系统提示词末尾，使对话以场景身份进行。注入 SHALL 通过已有 `Agent.extra_system_suffix` 实现，不改变既有系统提示词构建顺序。场景关联的 `skill_name` 技能 SHALL 通过 `skill_manager` 的选择集进入技能提示词段，技能未安装或未启用 SHALL 不阻断对话，仅不注入对应技能。

#### Scenario: 激活场景后新消息携带场景提示词
- **WHEN** 场景激活后用户发送新消息
- **THEN** 重建该会话 Agent 时在完整系统提示词末尾追加场景 `system_prompt`

#### Scenario: 场景技能已安装
- **WHEN** 场景关联的 `skill_name` 技能已注册进 SkillManager
- **THEN** 该技能进入 `_build_skills_section` 的可用技能列表，供 Agent 选择

#### Scenario: 场景技能未安装
- **WHEN** 场景关联技能不存在或未启用
- **THEN** 对话正常进行，仅不注入该技能，不报错

#### Scenario: 未激活场景的会话
- **WHEN** 会话未激活任何场景
- **THEN** 使用既有完整系统提示词，不追加场景内容，行为与未引入本功能前一致

### Requirement: 场景工作台导入在租户作用域内落盘

系统 SHALL 提供 `POST /api/scenes/workbench/import` 并在请求作用域内执行：database 模式下 SHALL 要求已选择有效租户并持有 `chat.use`，写操作 SHALL 经统一的「管理写」来源/CSRF 校验；工作区根目录 SHALL 在作用域内解析，因此 SHALL 只写入本租户的共享根目录，MUST NOT 回落到进程全局默认 Agent 的工作区。作用域无法解析时 SHALL 拒绝且不写入任何文件。

#### Scenario: 导入写入本租户共享根目录
- **WHEN** 已选择租户且持有 `chat.use` 的调用者导入允许扩展名的文件
- **THEN** 文件写入该租户共享根目录下的 `tmp/scenes/`，全局默认工作区无任何写入

#### Scenario: 作用域缺失时拒绝
- **WHEN** database 模式下来自无租户选择请求的导入
- **THEN** 返回 4xx 且不解析工作区、不写入任何文件

#### Scenario: 跨来源导入被拒绝
- **WHEN** cookie 凭据的导入请求来源与请求 Host 不匹配
- **THEN** 返回 403（`csrf_failed`）且不写入任何文件

