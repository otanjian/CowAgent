## MODIFIED Requirements

### Requirement: 权限目录有限且具有稳定元数据

系统 SHALL 保留原九项权限ID并增加本次明确登记的 skill.read/use/edit/enable、tool.read/execute/configure、model.read/use、agent.use/edit/enable、chat.use 十三项功能动作，以及知识库写入所需的 knowledge.write，提供稳定id、group、label、description、scope和assignable元数据。自定义角色 SHALL 仅能保存允许分配的目录项，未知ID、客户端通配符、平台标识或身份管理员资格不得保存。平台all由权威系统资格派生，不写普通角色；不按前缀推导权限，不预登记SSO或未适配客户端权限。knowledge.write SHALL 归类为租户作用域且可分配，MUST NOT 被解释为读取能力的替代或包含关系——读取仍由 knowledge.read 独立判定。

#### Scenario: 展示并选择登记权限

- **WHEN** 租户管理员为自定义角色查看和选择可分配权限
- **THEN** 服务端返回分组、用途及作用域明确的目录，包含「知识 → 编辑知识」的 knowledge.write，保存后仅授予所选择且允许分配的稳定权限 ID

#### Scenario: 授予知识写入权限

- **WHEN** 租户管理员为自定义角色选择 knowledge.write 并保存
- **THEN** 该角色成员在后续请求中取得 knowledge.write，可执行知识库写入；未选择该权限的角色成员不因此获得写入能力

#### Scenario: 提交未知或不可分配权限

- **WHEN** 角色写请求包含尚未登记 ID、通配符、平台管理员标识或身份审计权限
- **THEN** 系统拒绝整次变更，不保存部分权限或将输入解释为隐式管理员资格
