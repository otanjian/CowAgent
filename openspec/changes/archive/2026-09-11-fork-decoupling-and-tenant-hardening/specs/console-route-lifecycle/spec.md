## ADDED Requirements

### Requirement: 已注册路由均有授权策略且状态变更动作受来源校验

控制台 SHALL 保证每个已注册路由（含页面、API 与流式入口）在授权策略中均有明确条目，MUST NOT 存在绕过方法完整性门禁的未登记路由。所有会改变状态的动作（激活场景、导入工作台内容等）SHALL 在解析出的请求作用域内执行，并 SHALL 受到与其他管理写一致的来源与 CSRF 校验；MUST NOT 以「当前版本不做该级权限拒绝」为由跳过登记或作用域解析。

已实测确认的未登记路由 SHALL 在迁移时逐条补齐策略与权限，至少包括：`/admin`、`/api/identity/administered-tenants`、`/api/scenes`、`/api/scenes/activate`、`/api/scenes/workbench/import`。其中 `/api/identity/administered-tenants` 为身份域读接口，SHALL 明确其作用域（个人域，凭据主体自身可管理的租户集合）并纳入策略。

该接口的可选目标账号参数 SHALL 同样限定在凭据主体可管理的范围内：目标账号须为凭据主体所管理租户之一的有效成员（或主体自身），否则 SHALL 拒绝且 MUST NOT 返回任何成员状态字段；未知标识与越界标识的响应 MUST NOT 可区分，不得以「非成员」结果回答越界探测。

权威清单与授权策略的覆盖 SHALL 作为可验收不变量，在新增路由或合并上游后立即校验；该校验 MUST 以 handler 实际实现的方法集合为第三条比对来源，不得仅比对权威清单与派生策略表（二者同源，比对恒真）。

#### Scenario: 未登记路由被调用

- **WHEN** 客户端调用一个已注册但授权策略中无条目的路由
- **THEN** 不变量校验失败；运行时应按未登记方法拒绝，不允许请求直达 handler

#### Scenario: 已实测的未登记路由不再存在

- **WHEN** 运行路由覆盖不变量校验
- **THEN** `/admin`、`/api/identity/administered-tenants` 与三条 scenes 路由均在权威清单与策略表中有明确条目，校验通过

#### Scenario: 以越界账号标识探测成员状态

- **WHEN** 某租户管理员在 `administered-tenants` 接口上以不属于其所管理租户的账号标识请求成员状态
- **THEN** 请求以 403 拒绝，响应不含任何成员状态字段，且与未知账号标识的响应不可区分

#### Scenario: 以所管理租户内的账号标识探测成员状态

- **WHEN** 某租户管理员以属于其所管理租户的有效成员账号标识请求成员状态
- **THEN** 请求成功，且仅返回该管理员所管理租户的成员状态（不含其他租户）

#### Scenario: 仅同源比对不足以通过校验

- **WHEN** 某 handler 实现的方法集合与权威清单登记的方法集合不一致
- **THEN** 校验以「实现与登记不一致」失败，即使权威清单与其派生策略表彼此一致

#### Scenario: 跨租户读取或激活场景

- **WHEN** A 租户成员以 B 租户场景标识请求读取、激活或导入
- **THEN** 请求在本租户作用域内被拒绝或限定，不读写其他租户内容，也不写入全局工作区

#### Scenario: 状态变更缺少来源校验

- **WHEN** 场景激活或工作台导入请求来自不被允许的来源且无有效 CSRF 证明
- **THEN** 请求被拒绝且无状态变更或文件写入

### Requirement: 未解析作用域不得回落到全局默认工作区
依赖请求作用域解析工作目录的控制台接口 SHALL 在 database 模式下于作用域缺失或租户为空时拒绝请求，MUST NOT 回落到全局默认 Agent 的工作区。该约束 SHALL 对读取与写入一并适用，并在拒绝时给出明确错误而非静默降级。

#### Scenario: 作用域为空的写入请求

- **WHEN** 未解析到租户作用域的请求尝试经控制台接口写入工作区文件
- **THEN** 请求返回 403 类拒绝，不写入全局默认 Agent 的工作区

### Requirement: tenant/platform 路由在进入 handler 前解析并校验请求上下文

database 身份模式下，标记为 `tenant` 或 `platform` 的路由 SHALL 在调用 handler 之前完成统一上下文解析，并按固定顺序拒绝：缺租户选择为 400、缺失/无效凭据为 401、无该租户有效成员关系或非平台管理员为 403。MUST NOT 依赖 handler 自行解析后才产生拒绝，否则仅做认证检查的 handler 会在无租户选择时读取跨租户数据。

解析出的上下文 SHALL 在执行 handler 期间发布为请求作用域身份并由 handler 复用，使门禁与 handler 不会对同一请求得出不同身份，且不重复读取身份库。门禁职责 SHALL 限定为「上下文存在性 + 身份域 + 该路由声明的 permission」；对象级属主与资源校验 SHALL 仍由 handler 负责，其所需上下文由门禁保证存在。

`public`/`personal`/`closed` 路由 MUST NOT 触发上下文解析，legacy 身份模式下门禁 SHALL 保持空操作。租户确实无法由请求头给出的路由（如原生 `EventSource` 发起的流式重连）SHALL 在权威清单中以显式标记声明「租户由被寻址资源派生」，不得在门禁中以路径特例隐藏该豁免；此类路由仍 SHALL 要求有效凭据。

对身份解析的**确定性**失败（缺租户/缺凭据/无成员关系/非管理员/缺 permission）SHALL 立即拒绝。对**非预期**失败（身份库不可达等）SHALL 先告警并放行至 handler 观察，且 SHALL 提供显式开关切换为拒绝（fail-closed）；该观察窗口 MUST NOT 使确定性失败降级。

#### Scenario: 仅做认证检查的 tenant 路由收到无租户选择的请求

- **WHEN** 持有效凭据但未给出租户选择的请求访问 `tenant` 路由
- **THEN** 在进入 handler 前返回 400，不返回该路由的业务数据

#### Scenario: 无有效成员关系的请求访问 tenant 路由

- **WHEN** 持有效凭据但不属于所选租户的请求访问 `tenant` 路由
- **THEN** 在进入 handler 前返回 403

#### Scenario: 无有效凭据访问 platform 路由

- **WHEN** 未携带有效凭据的请求访问 `platform` 路由
- **THEN** 在进入 handler 前返回 401，而非由 handler 产生响应

#### Scenario: 由资源派生租户的流式重连

- **WHEN** 原生 `EventSource` 重连 `GET /stream` 且无法提供租户选择头
- **THEN** 门禁仍要求有效凭据并放行，由 handler 依据被寻址请求的属主与租户完成校验

#### Scenario: 门禁不替代对象级校验

- **WHEN** 持合法上下文与所需 permission 的请求访问不属于自己的资源
- **THEN** 门禁放行，handler 的属主/资源校验拒绝该请求

#### Scenario: 非预期解析失败

- **WHEN** 上下文解析因身份库不可达等非预期原因失败
- **THEN** 默认记录告警并放行至 handler 观察；开启显式开关时返回 503 且不进入 handler；确定性失败不受该开关影响
### Requirement: 进程级日志面限定平台域并对输出脱敏

日志读取面（`GET /api/logs` 流式与 `GET /api/logs/download`）读取的是进程级 `run.log`，其中包含全部租户的活动以及 handler 记录的内容（可能含厂商令牌、会话 cookie 等凭据）。因此其授权策略 SHALL 为 `platform`：database 身份模式下 SHALL 仅平台管理员可读，普通成员 SHALL 在进入 handler 前被拒绝；handler 内 SHALL 以同一平台控制面守卫复核，不得仅依赖门禁。legacy 模式沿用既有控制台口令。

返回内容 SHALL 脱敏：对已知凭据字段名（password/passwd/secret/token/api key/authorization/access key/refresh token/cookie/credential/client secret/session id）的 `key: value` 与 `key=value` 形式 SHALL 掩蔽其值（保留字段名以便定位），且 MUST NOT 因脱敏改变普通日志行的内容。

#### Scenario: 普通成员读取日志
- **WHEN** 已选择租户但非平台管理员的成员请求 `GET /api/logs` 或 `GET /api/logs/download`
- **THEN** 返回 403，不返回任何日志内容

#### Scenario: 平台管理员读取日志
- **WHEN** 平台管理员请求同一接口
- **THEN** 返回日志内容，且其中凭据类字段的值已被掩蔽

#### Scenario: 普通日志行不被改变
- **WHEN** 某行不含上述凭据字段
- **THEN** 该行内容原样返回

### Requirement: 管理写统一来源与 CSRF 校验

所有改变状态的管理写请求（平台控制台与租户控制台的写接口、场景激活/导入等）SHALL 经由同一处来源校验入口（`channel/web/auth_handlers.py` 的 `require_management_write`），MUST NOT 由各接口自行判定或默认放行。校验 SHALL 在任何上下文解析与写入之前执行。

规则：以 cookie 会话认证的写请求 SHALL 提供与请求 `Host` 同源的 `Origin` 或 `Referer`；缺失来源 SHALL 被拒绝（这些接口没有独立的 CSRF token 流程，因此以缺失即拒为默认）。以 bearer 凭据认证的写请求 SHALL 在 bearer 真实认证时豁免来源校验，以支持来源永不匹配的原生客户端；裸的、格式非法的或与 cookie 同值的重复凭据 MUST NOT 获得豁免。legacy 模式的写路径 SHALL 保持既有行为不变。

#### Scenario: 无来源的 cookie 写请求
- **WHEN** cookie 会话发起管理写请求但不带 `Origin`/`Referer`
- **THEN** 返回 403（`csrf_failed`），且不产生任何写入

#### Scenario: 跨来源的 cookie 写请求
- **WHEN** cookie 会话发起管理写请求，`Origin` 与请求 `Host` 不同源
- **THEN** 返回 403（`csrf_failed`），且不产生任何写入

#### Scenario: 同来源的 cookie 写请求
- **WHEN** cookie 会话发起管理写请求，`Origin` 或 `Referer` 与请求 `Host` 同源
- **THEN** 请求按既有业务规则继续处理

#### Scenario: bearer 客户端写请求
- **WHEN** 请求以真实有效的 bearer 凭据认证（无 cookie），即使来源与 `Host` 不同源
- **THEN** 来源校验豁免，请求按既有业务规则继续处理

#### Scenario: 伪造或不一致的凭据不获得豁免
- **WHEN** 请求同时携带 cookie 与无效 bearer，或 bearer 与 cookie 同值
- **THEN** 仍按 cookie 规则校验来源；不满足时返回 403 且不产生写入
