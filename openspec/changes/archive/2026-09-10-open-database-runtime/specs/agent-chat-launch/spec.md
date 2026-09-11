## MODIFIED Requirements

### Requirement: 遵守现有模式的运行门槛
系统 SHALL 仅在调用者满足当前身份模式的运行授权时启动聊天。在 `identity_mode=database` 下，启动与发送 SHALL 要求有效租户成员具备 `chat.use`，并对目标智能体持有 `agent.use` 资源授权，对所选模型持有 `model.use`；平台管理员与租户管理员按既有旁路规则处理。可读但不可用时，卡片 SHALL 提示稳定原因并禁用启动；服务端仍独立拒绝，不得静默替换为默认智能体，也不得降级到 legacy 身份。

#### Scenario: 可读但尚未开放运行
- **WHEN** 用户可读取某智能体（`agent.read`），但缺少 `chat.use` 或该智能体的 `agent.use`（执行未授权）
- **THEN** 用户能够查看卡片介绍，不能开始运行，界面说明权限限制，且不降级到 legacy 身份

#### Scenario: 现有运行模式允许聊天
- **WHEN** 当前部署允许 Web 聊天（legacy，或 database 且用户具备 `chat.use`、目标 `agent.use` 与所需 `model.use`），且目标启用并可访问
- **THEN** 卡片入口可以完成正常聊天闭环，无需额外发布步骤或新增运行开关
