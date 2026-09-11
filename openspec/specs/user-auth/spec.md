# user-auth Specification

## Purpose
定义控制台的登录账号体系：数据库账号密码登录、会话复用与侧边栏当前登录账号展示。共享密码 / `web_username`+`web_password` / `cow_auth_token` 已退役。

## Requirements
### Requirement: 账号密码登录
系统 SHALL 要求用户同时提供数据库账号与密码才能登录控制台；账号或密码任一不匹配 SHALL 拒绝访问。

#### Scenario: 账号密码校验通过
- **WHEN** 用户提交正确的账号与密码
- **THEN** 系统建立登录态（设置 `cow_session`），允许访问控制台

#### Scenario: 账号或密码错误
- **WHEN** 用户提交错误账号或错误密码
- **THEN** 系统拒绝登录，并提示认证失败信息

### Requirement: 登录账号来源于身份库
系统 SHALL 以身份库中的用户账号作为唯一登录主体，MUST NOT 使用配置项 `web_username` / `web_password` 决定登录是否启用。

#### Scenario: 身份库账号可登录
- **WHEN** 身份库中存在可用账号且密码正确
- **THEN** 登录页面接受用户名与密码并建立会话

### Requirement: 登录态会话
系统 SHALL 使用数据库 `AuthSession`（`cow_session` Cookie 或 Bearer）承载登录态，账号确认与授权在每次请求时解析，不将身份持久化到业务 session。MUST NOT 使用 `cow_auth_token`。

#### Scenario: 会话令牌生效
- **WHEN** 用户已成功登录且请求携带有效会话令牌
- **THEN** 系统允许访问受保护页面，且无需重复登录

### Requirement: 侧边栏展示登录账号
系统 SHALL 在侧边栏底部显示当前登录账号。

#### Scenario: 展示登录账号
- **WHEN** 用户已登录并查看侧边栏底部
- **THEN** 显示当前登录账号
