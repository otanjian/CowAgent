## ADDED Requirements

### Requirement: 场景数据接口

系统 SHALL 提供 `GET /api/scenes` 返回场景分类与场景列表。接口 SHALL 按当前登录身份做可见性过滤：v1 对认证用户默认返回全部场景，但 SHALL 在数据结构中保留 `required_permission` 与分类/场景字段，以便后续 RBAC 过滤。`scenes_config.json` 缺失或解析失败 SHALL 返回空结构（`scenes: []`, `categories: []`）而非 500。接口 SHALL 复用既有 `_require_auth()` 鉴权。

#### Scenario: 正常返回场景
- **WHEN** 认证用户请求 `GET /api/scenes` 且配置存在
- **THEN** 返回 `status: success`，含过滤后的分类与场景列表

#### Scenario: 配置缺失或解析失败
- **WHEN** `scenes/scenes_config.json` 不存在或 JSON 解析失败
- **THEN** 返回空结构且 `status: success`，不抛出未捕获异常

### Requirement: 场景激活接口

系统 SHALL 提供 `POST /api/scenes/activate`，接收 `scene_id` 与 `session_id`。接口 SHALL 查找场景（含在子场景中查找，并合并父场景元数据），将场景上下文写入该会话，并 SHALL 使该会话现有 Agent 实例失效以便下次构建时注入场景提示词。`scene_id` 或 `session_id` 缺失 SHALL 返回错误；未找到场景 SHALL 返回错误；激活成功 SHALL 返回场景上下文。v1 对认证用户不做场景级权限拒绝。

#### Scenario: 激活顶层场景
- **WHEN** 提交存在的顶层 `scene_id` 与 `session_id`
- **THEN** 场景上下文写入会话，现存 Agent 实例被移除，返回 `status: success` 与场景

#### Scenario: 激活子场景
- **WHEN** 提交某场景的 `sub_scene` id 且存在于配置
- **THEN** 合并父场景元数据（parent_id/parent_name/skill_name）后写入会话并返回

#### Scenario: 缺少参数或场景不存在
- **WHEN** 未提交 `scene_id`/`session_id`，或 `scene_id` 在配置中不存在
- **THEN** 返回错误信息，不写入会话状态

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
