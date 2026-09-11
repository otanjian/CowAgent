# service-account-api-access Specification

## Purpose
定义外部 OpenAI 兼容 API 在不使用共享密码或旧 token 的前提下，以真实服务账号用户身份访问的凭据模型：加密 API 密钥、掩码投影、轮换撤权即时失效与逐请求身份解析。
## Requirements
### Requirement: 外部 API 以服务账号真实用户身份执行

外部 OpenAI 兼容 API 的每次调用 SHALL 解析为身份库中的一个真实用户（服务账号），并验证该用户 active、目标租户成员 active 及其当前角色与资源 grants。系统 MUST NOT 以平台进程、通道服务身份或客户端自报身份伪造 Membership，MUST NOT 借平台 all 绕过租户与资源授权，MUST NOT 回退匿名或共享密码执行。

#### Scenario: 有效服务账号调用
- **WHEN** 请求携带有效服务账号凭据，且该用户为其目标租户的有效成员并持有执行所需权限与资源 grant
- **THEN** 系统以该服务账号用户身份进入既有对话执行路径

#### Scenario: 服务账号被停用或无权限
- **WHEN** 服务账号用户、成员或授权在调用前被停用或撤销
- **THEN** 系统拒绝执行，返回 401 或 403，不降级为匿名或平台进程身份

### Requirement: API 密钥加密存储、掩码与审计

服务账号 API 密钥 SHALL 按既有凭据能力（`credential-management`）的加密、掩码、版本与轮换规则存储于身份域，MUST NOT 以明文写入配置、日志、审计正文或任何接口响应。读取接口 MUST 仅返回掩码与存在状态，轮换后旧版本 MUST 立即失效，撤权后下一次使用 SHALL 失败。密钥的创建、轮换与撤权 SHALL 记录可归属审计。

#### Scenario: 创建后读取只出掩码
- **WHEN** 获权管理员创建或读取服务账号密钥
- **THEN** 响应仅含掩码标识与存在状态，不含可复用明文

#### Scenario: 轮换或撤权后旧密钥失效
- **WHEN** 某服务账号密钥被轮换或撤权
- **THEN** 旧密钥立即不可用，后续调用按未认证拒绝，且事件进入审计

### Requirement: OpenAI 兼容 API 身份解析与拒绝

OpenAI 兼容 API SHALL 从受验证的请求凭据解析服务账号并按其权限执行；凭据缺失、格式非法、无效或已撤权时 SHALL 返回 401，MUST NOT 以 URL 查询参数长效 token 授权，MUST NOT 回落共享密码或匿名身份，且 MUST NOT 因身份服务故障返回成功。

#### Scenario: 无效或缺失凭据
- **WHEN** 请求缺少凭据或携带无效、已撤权密钥
- **THEN** 系统返回 401，不执行模型调用，不返回任何业务数据

#### Scenario: 身份服务故障
- **WHEN** 解析服务账号所需身份库不可用
- **THEN** 系统返回 503，不降级为匿名执行

