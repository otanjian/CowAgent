## Purpose

定义控制台的登录账号体系，覆盖登录方式（账号+密码）、账号配置来源、登录态会话复用与侧边栏当前登录账号展示，为后续租户/用户/权限功能提供身份前置。

## ADDED Requirements

### Requirement: 账号密码登录
系统 SHALL 在启用密码保护时，要求用户同时提供账号与密码才能登录控制台；账号或密码任一不匹配 SHALL 拒绝访问。

#### Scenario: 账号密码校验通过
- **WHEN** 用户提交正确的账号与密码
- **THEN** 系统建立登录态（设置会话令牌），允许访问控制台

#### Scenario: 账号或密码错误
- **WHEN** 用户提交错误账号或错误密码
- **THEN** 系统拒绝登录，并提示认证失败信息

### Requirement: 登录账号配置
系统 SHALL 通过 `web_username` 配置项定义被允许登录的账号；`web_username` 与既有 `web_password` 一同决定登录是否被启用。

#### Scenario: 配置了账号
- **WHEN** `web_username` 配置非空
- **THEN** 登录页面要求输入账号与密码

#### Scenario: 未配置账号
- **WHEN** `web_username` 配置为空
- **THEN** 系统沿用既有行为，不强制展示账号输入

### Requirement: 登录态会话
系统 SHALL 复用既有会话机制（`cow_auth_token`）承载登录态，账号确认与授权在每次请求时解析，不将身份持久化到业务 session。

#### Scenario: 会话令牌生效
- **WHEN** 用户已成功登录且请求携带有效会话令牌
- **THEN** 系统允许访问受保护页面，且无需重复登录

### Requirement: 侧边栏展示登录账号
系统 SHALL 在侧边栏底部原展示版本号的位置显示当前登录账号。

#### Scenario: 展示登录账号
- **WHEN** 用户已登录并查看侧边栏底部
- **THEN** 显示当前登录账号（而非版本号）

### Requirement: 未启用登录时的展示
系统 SHALL 在未启用账号密码保护时，侧边栏底部保留版本号展示，不展示登录账号。

#### Scenario: 未启用登录
- **WHEN** 未配置 `web_username`/`web_password`
- **THEN** 侧边栏底部展示版本号而非登录账号
