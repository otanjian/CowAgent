## MODIFIED Requirements

### Requirement: 创建租户一次建立可用身份关系

租户 SHALL 使用服务端生成的稳定 ID、全局唯一且不可修改的 code、name、active 标识及 version，不提供 provisioning、archived 或邀请状态。创建 SHALL 先生成不可被租户接口发现的独立目录，再在同一身份事务内建立租户、内置角色和虚拟组织根；任何步骤失败 MUST NOT 出现可用租户。目录 SHALL 满足 `tenant-resource-isolation` 的跨租户边界，不接受客户端指定目录；没有 Agent 的新租户 SHALL 返回空 Agent 列表。完整首期验收后启用 database SHALL 正常允许创建多个租户，不另设第二租户专用开关。

创建 SHALL NOT 强制建立管理员账号或成员关系。请求显式提供有效初始管理员信息（账号、显示名、密码）时，系统 SHALL 在同一身份事务内一并建立该账号的 user、active membership、tenant_admin 角色绑定及成功审计，并沿用 `user-membership` 的临时密码与首次改密契约；未提供时系统 SHALL 只提交租户、内置角色与虚拟组织根，不建立任何 user、membership 或 membership_role 行，也 MUST NOT 生成或回显临时密码。管理员配置 SHALL 通过事后独立的「平台直接配置管理员」操作完成。

未提供初始管理员的租户 SHALL 以 active=true 创建，并允许在绑定首个有效 tenant_admin 之前保持无有效管理员；该窗口 MUST NOT 放宽「启用状态变更保护租户边界」中恢复停用租户前的管理员连续性校验。

#### Scenario: 正常创建租户
- **WHEN** 平台管理员通过近期密码验证，仅以未占用编码和名称创建租户
- **THEN** 独立目录就绪后租户、内置角色和虚拟组织根同时提交，返回稳定 ID、active=true 和版本，响应不含任何临时密码或账号字段，且该租户尚无有效 tenant_admin

#### Scenario: 创建租户时显式指定初始管理员
- **WHEN** 平台管理员通过近期密码验证创建租户并显式提供有效初始管理员账号、显示名与密码
- **THEN** 租户与该管理员的 user、active membership、tenant_admin 角色绑定和成功审计在同一事务提交，临时密码仅在生成响应展示一次

#### Scenario: 为新建租户绑定首个管理员
- **WHEN** 租户创建成功后平台管理员通过近期密码验证，将该租户尚未绑定的已有有效 User 配置为管理员
- **THEN** 系统建立该租户的管理员成员与角色关系并审计，已有账号密码及其他租户资料保持不变，平台操作者不因此获得该租户 Membership

#### Scenario: 创建过程中失败
- **WHEN** 创建目录、建立身份关系或写入成功审计失败
- **THEN** 系统返回错误，不提交可用租户；未被数据库引用的目录不通过扫描成为租户，可由受信运维清理

#### Scenario: 创建响应丢失后查询结果
- **WHEN** 租户创建已提交但响应丢失，管理员按同一租户编码查询或再次创建
- **THEN** 查询可定位已创建对象，重复创建由唯一约束拒绝，不建立第二份租户关系，也不再次回显临时密码

#### Scenario: 创建的租户等待管理员配置
- **WHEN** 平台管理员查询刚创建、尚未配置有效 tenant_admin 的租户并将其停用
- **THEN** 停用成功；此时尝试恢复该租户 SHALL 被拒绝并保持 active=false，要求先完成管理员配置
