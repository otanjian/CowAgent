## MODIFIED Requirements

### Requirement: 租户与 Agent 工作区文件服务按作用域隔离

租户成员的文件服务访问 SHALL 限于其当前有效租户的共享根与获授权 Agent 的 workspace；缺少有效租户上下文时 SHALL 返回 403，请求其他租户资源时 SHALL 按不可见拒绝。系统 MUST NOT 因客户端自报路径、租户头或 Agent 标识扩大可见范围，MUST NOT 沿用单租户 instance-root 的全局可见语义。

浏览器原生发起、结构上无法携带凭据或租户头的文件读取（下载导航与子资源）SHALL 在被显式登记为「租户由被寻址资源派生」后，由服务端从被寻址资源解析租户，并仍校验调用者对该租户的有效成员资格；调用方自报的路径、租户头或 Agent 标识 MUST NOT 成为授权来源，只可用于冲突检测。

能力令牌预览（`GET /preview/<token>/<name>`）SHALL 以不可伪造的签名令牌为授权真值；其纵深根校验的允许根 SHALL 覆盖全部租户共享根与全部绑定 Agent 的 workspace（来源为服务端静态登记，不依赖请求身份），以便解析不到请求身份的公开预览仍能读取合法工作区文件；这些工作区之外的路径（含操作员主目录）MUST 仍被拒绝。该根校验 MUST NOT 通过构造「身份缺失」错误来实现，以免残留的状态或响应头污染成功响应。

控制台工作区面板的文件接口（`GET /api/workspace/tree|search|resolve|meta|read` 与 `POST /api/workspace/write`）SHALL 以调用者的有效租户为作用域并保持可用，MUST NOT 因身份模式本身返回整体不可用。其相对路径读取与写入 SHALL 限定于调用者的租户共享根、已绑定且获授权 Agent 的 workspace，或调用者私有项目目录。绝对路径的解析、读取与写入 SHALL 按**调用者租户作用域**授权（仅平台管理员可越出至平台根），MUST NOT 复用「覆盖全部租户与全部 Agent 工作区」的静态根列表作为授权依据。系统资产（`MEMORY.md`、`knowledge/` 等）的回落解析 SHALL 同样限定在调用者租户内，MUST NOT 回落到全局默认 Agent 的工作区。工作区写入 SHALL 在工作区边界校验之外经过统一管理写来源校验。

租户包含关系 MUST NOT 单独构成绝对路径或被寻址资源的授权依据：系统 SHALL 解析该资源**所属 Agent**，并对其执行与请求显式声明的 Agent 参数**相同**的私有/共享归属校验（属主本人或 `tenant_admin` 放行）；共享根与共享 Agent 资源按共享语义放行。该归属校验 SHALL 覆盖以绝对路径、相对路径（含租户共享根之下的目录嵌套）与服务端签发 URL 寻址的文件读写，MUST NOT 因「路径落在调用者租户共享根内」而跳过。目录列举（`tree`）与文本搜索（`search`）SHALL 同样按归属过滤，MUST NOT 返回无权读取的私有 Agent 目录名或路径存在性。归属 MUST 取**最具体**（最长）的命中根；当路径同时落在两个不同 Agent 的 workspace 之下、或归属无法解析时，系统 MUST 失败关闭并按不可见拒绝，MUST NOT 任选其一放行。请求自报的 `agent_id` MUST NOT 作为授权来源，只可用于与解析归属的冲突检测。

#### Scenario: 同租户成员访问本租户文件

- **WHEN** 有效租户成员请求本租户共享根或获授权 Agent workspace 内的文件
- **THEN** 系统在租户作用域内返回文件内容

#### Scenario: 跨租户或缺少租户上下文

- **WHEN** 请求指向其他租户的文件路径，或调用方没有有效租户上下文
- **THEN** 系统按不可见拒绝（404 或 403），MUST NOT 返回该文件内容或路径存在性

#### Scenario: 浏览器原生下载本租户工件

- **WHEN** 已登录且获权的成员以无法携带租户头的下载导航请求本租户 Agent 生成的文件
- **THEN** 系统由文件所属工作区派生租户并校验成员资格后返回内容，不因「未选择租户」拒绝

#### Scenario: 能力令牌预览合法工作区文件

- **WHEN** 持有服务端签发的合法预览令牌、但无法提供会话凭据的请求读取租户或 Agent 工作区内的文件
- **THEN** 系统返回文件内容，仍拒绝该工作区之外的路径

#### Scenario: 成员浏览本租户工作区面板

- **WHEN** 已登录且获权的租户成员打开控制台工作区面板，请求本租户工作区目录树、文件元数据或正文
- **THEN** 请求返回成功且根为本租户共享根、绑定 Agent workspace 或调用者私有项目目录，不因身份模式返回整体不可用

#### Scenario: 以绝对路径解析他租户文件

- **WHEN** A 租户成员以绝对路径请求解析、读取或写入属于 B 租户工作区（或平台根，且调用者非平台管理员）的文件
- **THEN** 系统按不可见拒绝，响应不含文件元数据、`preview_url` 或文件内容

#### Scenario: 系统资产回落不越出租户

- **WHEN** 非默认租户成员以相对路径请求 `MEMORY.md` 或 `knowledge/...` 等系统资产
- **THEN** 系统在调用者租户作用域内解析该资产，MUST NOT 读取全局默认 Agent 的工作区

#### Scenario: 同租户成员以绝对路径请求他人私有 Agent 文件

- **WHEN** 普通成员（非 `tenant_admin`、非该 Agent 属主）以绝对路径（或携带不带 `agent_id` 的服务端签发 URL）请求解析、读取或下载同租户内他人**私有** Agent workspace 中的文件，即使请求同时声明了一个共享 Agent
- **THEN** 系统按不可见拒绝（403 或 404），响应 MUST NOT 含文件内容、文件元数据、`preview_url` 或 `raw_url`

#### Scenario: 同租户成员以相对路径或目录嵌套请求他人私有 Agent 文件

- **WHEN** 普通成员（非 `tenant_admin`、非该 Agent 属主）以相对路径请求同租户内他人私有 Agent workspace 中的文件，包括该 workspace 恰位于调用者租户共享根之下（`agents/<private-agent>/…`）的情形
- **THEN** 系统按不可见拒绝，MUST NOT 返回文件内容或路径存在性

#### Scenario: 目录列举与搜索不暴露他人私有 Agent

- **WHEN** 普通成员（非 `tenant_admin`、非该 Agent 属主）列举本租户共享根下的目录，或执行文本搜索
- **THEN** 结果 MUST NOT 含他人私有 Agent 的目录名或路径

#### Scenario: 私有 Agent 的属主与租户管理员

- **WHEN** 私有 Agent 的属主本人，或同租户的 `tenant_admin`，请求该私有 Agent workspace 中的文件
- **THEN** 系统返回文件内容，不因私有属性被拒

#### Scenario: 共享资源不受属主判定影响

- **WHEN** 本租户成员请求租户共享根内文件，或请求本租户共享（非私有）Agent workspace 内文件
- **THEN** 系统返回文件内容，不引入额外属主门槛

#### Scenario: 归属无法唯一确定

- **WHEN** 被请求的路径同时落在同租户两个不同 Agent 的 workspace 之下（互相同构或嵌套的配置退化）
- **THEN** 系统失败关闭并按不可见拒绝，MUST NOT 任选其一放行
