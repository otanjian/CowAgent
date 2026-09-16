# tenant-knowledge-console Specification

## Purpose

Console access to Agent knowledge bases: reads are scoped to the caller's
tenant, the addressed Agent's data root (shared base or the Agent's own
`knowledge/`) and private ownership; writes are authorized by data-root
ownership plus Agent ownership rather than by a functional permission, and the
page renders its write affordances from the per-Agent capability the server
projects.
## Requirements
### Requirement: 知识库接口归类为租户域

系统 SHALL 将 `/api/knowledge/list`、`/api/knowledge/read`、`/api/knowledge/graph` 的 GET 与 `/api/knowledge/action`、`/api/knowledge/import` 的 POST 在路由方法策略中归类为 `tenant`（租户），MUST NOT 再作为 `closed` deferred 消费者在 database 模式返回 `503 database_unavailable`。未登录或已登录但无有效租户上下文的请求 SHALL 返回 `401`（无有效会话），MUST NOT 进入下游 handler 产生读取或写入副作用。策略表 MUST 继续与注册路由的全集保持一致，未登记方法仍按既有完整性闸门返回拒绝。

#### Scenario: tenant 策略可匹配

- **WHEN** 对 `/api/knowledge/list`、`/api/knowledge/read`、`/api/knowledge/graph` 发起 GET，或对 `/api/knowledge/action`、`/api/knowledge/import` 发起 POST
- **THEN** 路由策略匹配到 `tenant` 且带 `knowledge` 语义，不再作为 `closed` 消费者被短路成 `503`

#### Scenario: 未登录访问

- **WHEN** 匿名请求访问 `/api/knowledge/list` 或 `/api/knowledge/action`
- **THEN** 系统返回 `401 unauthorized`，不返回任何知识条目，也不产生任何文件读写副作用

### Requirement: 租户成员可读取知识库内容与图谱

已认证且持有 knowledge.read 的租户成员 SHALL 能读取当前租户内其可见 Agent 的知识目录、正文与图谱。读取 SHALL 先校验 Agent 的真实租户绑定，再按实际私有归属裁剪：私有 Agent 的私有知识内容仅 owner 本人可见，非 owner 的 tenant_admin 和平台管理员 MUST NOT 因角色或 all 放行。明确公共知识继续按既有功能和资源授权读取；共享回退不得公开实际来自他人私有工作区的内容。响应 MUST NOT 暴露宿主绝对路径或其他租户内容。

#### Scenario: 租户成员读取知识库目录

- **WHEN** 具备 `knowledge.read` 的有效成员携带有效会话与所选租户 GET `/api/knowledge/list`
- **THEN** 系统返回 `200` 与租户可见知识库的树结构、根文件与统计，条目限于该租户可见 Agent

#### Scenario: 读取单篇知识正文

- **WHEN** 具备 `knowledge.read` 的成员 GET `/api/knowledge/read?path=<相对路径>`
- **THEN** 系统返回 `200` 与该文件正文、相对路径及可解析图片所需的目录信息，不返回宿主绝对路径

#### Scenario: 读取知识图谱

- **WHEN** 具备 `knowledge.read` 的成员 GET `/api/knowledge/graph`
- **THEN** 系统在与 list/read 相同的租户作用域与私有归属裁剪下返回节点与连线，不因图谱接口缺少门禁而越租户返回内容

#### Scenario: 无读取权限的成员被拒绝

- **WHEN** 已登录但当前租户上下文缺少 `knowledge.read` 的成员请求上述任一读取入口
- **THEN** 系统返回 `403 forbidden`，不返回任何知识内容

### Requirement: 跨租户与越权知识访问被拒绝

系统 SHALL 按可信租户绑定和实际资源私有归属统一裁剪知识读取与写入；其他租户 Agent 返回 404，成员私有 Agent 及其私有知识内容只对 owner 开放。授权 SHALL 每次请求按当前身份事实重新计算，MUST NOT 使用客户端权限、旧上下文或管理员资格绕过 private owner，也不得通过图谱、索引、文件别名和共享回退泄漏私有内容。

#### Scenario: 跨租户指定 Agent

- **WHEN** 租户 A 的成员携带其会话与租户头请求 `agent_id` 属于租户 B 的 `/api/knowledge/list`
- **THEN** 系统返回 `404`，不返回租户 B 的任何知识条目

#### Scenario: 非 owner 读取私有 Agent 知识库

- **WHEN** 非 owner 的普通成员、tenant_admin 或平台管理员请求一个私有归属给其他成员的 Agent 的 `/api/knowledge/read`
- **THEN** 系统拒绝该请求并返回 `404`/`403` 类拒绝，不返回私有内容

### Requirement: 控制台知识库页面不因消费者关闭而无限加载

控制台「知识库」视图 SHALL 在读取入口返回非成功响应时清除「加载知识库中...」占位并呈现可读原因，MUST NOT 停留在永久加载态。当响应表示该消费者在当前身份模式下未开放时，页面 SHALL 说明功能不可用；当响应表示缺少读取权限时，页面 SHALL 说明权限不足。「新建」及其他写入口（重命名、删除、移动、导入）SHALL 按当前所选智能体的写能力投影渲染，MUST NOT 向无写权限的成员呈现必然失败的操作。

#### Scenario: 消费者关闭时呈现原因

- **WHEN** `/api/knowledge/list` 返回 `503 database_unavailable`
- **THEN** 页面显示功能未开放的可读提示并隐藏加载占位，不无限转圈

#### Scenario: 无写权限不展示新建入口

- **WHEN** 当前所选智能体对当前成员不可写（租户级智能体的非管理员成员、或其他成员拥有的私有智能体）
- **THEN** 页面隐藏「新建」入口与文件级写操作，且直接请求写接口仍返回 `403`

### Requirement: 知识库数据根按所选智能体解析

系统 SHALL 按请求 `agent_id` 对应智能体的数据根解析知识库，MUST NOT 对同一租户的所有智能体固定返回租户共享根。解析规则 SHALL 与该智能体运行时实际读取的知识库一致（`state_dir` 的「按存在即独立」规则）：该智能体在自己的工作区下拥有 `knowledge/` 目录时以其为数据根；没有该目录时回落到调用者租户的共享知识库。数据根解析 MUST 在租户身份作用域内完成，使回落路径按调用者租户解析共享库，MUST NOT 落到进程默认工作区或其他租户。

#### Scenario: 独立知识库智能体只看到自己的条目

- **WHEN** 成员以某个 `agent_id` 请求 `/api/knowledge/list`，该智能体在自己的工作区下拥有 `knowledge/` 目录，且其内容与租户共享库不同
- **THEN** 系统返回该智能体自己目录下的树、根文件与统计，不返回租户共享库的条目

#### Scenario: 共享知识库智能体看到租户共享库

- **WHEN** 成员以某个 `agent_id` 请求 `/api/knowledge/list`，该智能体没有自己的 `knowledge/` 目录
- **THEN** 系统返回调用者租户共享知识库的内容，不返回其他租户或进程默认工作区的内容

#### Scenario: 切换智能体改变数据范围

- **WHEN** 同一成员先后以两个数据根不同的 `agent_id` 请求 `/api/knowledge/list`
- **THEN** 两次响应分别按各自智能体的数据根解析，目录集合不相同

#### Scenario: 指向共享库的符号链接视为共享

- **WHEN** 智能体的 `knowledge/` 是指向租户共享知识库的符号链接
- **THEN** 系统返回共享库内容，不因符号链接返回空或报错

#### Scenario: 目录、正文与图谱使用同一数据根

- **WHEN** 成员以同一 `agent_id` 依次请求 `/api/knowledge/list`、`/api/knowledge/read` 与 `/api/knowledge/graph`
- **THEN** 三者解析到同一数据根，正文与图谱内容与目录所列条目一致

### Requirement: 知识库页按智能体说明共享与独立

知识库页面 SHALL 用中性文案说明知识库是按智能体「共享」或「独立」的，并指向「智能体管理」的设置入口；该文案 SHALL 同时说明维护责任——租户级智能体的知识库由本租户管理员维护，私有智能体的知识库由该 owner 本人维护。MUST NOT 声称知识库默认对全员共享。该文案 SHALL 在简体、繁体、英文三种语言下均提供，且与 i18n 目录及快照保持一致。

#### Scenario: 页面说明文案

- **WHEN** 打开知识库页面
- **THEN** 说明文案表示知识库按智能体共享或独立、可在「智能体管理」中设置，并标明租户级由租户管理员维护、私有智能体由本人维护，不再出现"默认全员共享"的表述

#### Scenario: 三语一致

- **WHEN** 依次以简体、繁体、英文渲染知识库页面
- **THEN** 三种语言都提供该说明文案，且 i18n 目录与快照中该键的取值与页面一致

### Requirement: 知识库写入按数据根与智能体归属授权

系统 SHALL 按该智能体知识库「数据根归属」与「智能体归属」共同判定 `/api/knowledge/action` 与 `/api/knowledge/import` 的写授权，MUST NOT 以功能权限 `knowledge.write` 或任何客户端声明的能力作为授权依据。

写授权 SHALL 为：写入落在租户共享知识库（该智能体为共享模式）时，仅平台管理员（具备有效租户成员资格时）与本租户 `tenant_admin` 可写，私有智能体 owner MUST NOT 借此写入共享库；写入落在该智能体自有的 `knowledge/` 目录（独立模式）时，平台管理员与本租户 `tenant_admin` 可写，且该智能体为私有归属时其 owner 本人额外可写；其余成员 MUST NOT 写入任何租户级智能体的知识库。

数据根归属 SHALL 复用与读取路径同一事实（`state_dir` 的「按存在即独立」判定），MUST NOT 另建判定口径。写入 SHALL 先完成租户 Agent 绑定校验与私有归属裁剪，且 MUST NOT 越过 `KnowledgeService` 对 `index.md`/`log.md` 的保护与路径规范化；导入 SHALL 继续执行既有的总体积与文件数上限校验。

#### Scenario: 私有智能体 owner 写自有知识库

- **WHEN** 成员 U 以其私有智能体（独立模式，拥有自己的 `knowledge/` 目录）POST `/api/knowledge/action` 提交创建文档
- **THEN** 系统在 U 的租户作用域内创建文档并返回成功，文件落回该智能体自己的知识库目录

#### Scenario: 私有智能体 owner 不得写租户共享库

- **WHEN** 成员 U 以其私有智能体（共享模式，其数据根即租户共享知识库）POST `/api/knowledge/action` 或 `/api/knowledge/import`
- **THEN** 系统返回 `403 forbidden`，MUST NOT 创建、删除、移动或导入任何文件，租户共享知识库不变

#### Scenario: 普通成员不得写租户级智能体

- **WHEN** 已登录但不是 U 的私有智能体 owner、亦非本租户 `tenant_admin` 或平台管理员的成员，对任一租户级智能体 POST `/api/knowledge/action`
- **THEN** 系统返回 `403 forbidden`，MUST NOT 修改任何文件

#### Scenario: 持有 knowledge.write 不再构成写授权

- **WHEN** 某成员的权限集合（含历史遗留或直接构造的 `knowledge.write`）不满足上述归属条件，却 POST `/api/knowledge/action`
- **THEN** 系统仍返回 `403 forbidden`，授权判定只看数据根与智能体归属

#### Scenario: 租户管理员与平台管理员写入

- **WHEN** 本租户 `tenant_admin` 或具备有效租户成员资格的平台管理员对租户级或私有智能体 POST `/api/knowledge/action`
- **THEN** 系统按同一租户作用域与私有归属校验后执行写入并返回成功，与其数据根是共享库还是自有库无关

#### Scenario: 跨租户 agent_id 被拒绝

- **WHEN** 成员 U 携带自己的会话与会话租户请求 `agent_id` 属于其他租户的 `/api/knowledge/action`
- **THEN** 系统返回 `404`，不产生任何写入副作用

#### Scenario: 写入不得越过受保护文件

- **WHEN** 有权成员通过 `delete_documents` 或 `move_documents` 指向 `index.md` 或 `log.md`
- **THEN** 系统按既有保护规则拒绝该条目并返回可读原因，其余条目不受影响

### Requirement: 知识写入能力按智能体投影

系统 SHALL 在 Agent 管理投影（`/api/agents`）中为每个可见智能体返回 `can_write_knowledge`，其取值 SHALL 与知识库写路径的授权判定同源，对同一身份与同一智能体 MUST 保持一致；控制台知识库页 SHALL 按当前所选智能体的该取值渲染写入口（新建、重命名、删除、移动、导入），MUST NOT 向无写权的成员呈现必然失败的写操作。该投影 MUST NOT 暴露 owner 标识、宿主绝对路径或其他租户内容。

#### Scenario: 投影与写判定一致

- **WHEN** 对同一身份与同一智能体同时读取 Agent 管理投影的 `can_write_knowledge` 与该智能体写请求的授权结果
- **THEN** 两者对「该身份能否写入该智能体的知识库」的判断一致，不出现投影报可写而实际 `403`（或反之）

#### Scenario: 切换智能体更新写入口

- **WHEN** 成员在知识库页从一个自己有写权的私有智能体切换到一个租户共享库智能体
- **THEN** 页面按所选智能体的 `can_write_knowledge` 重新渲染，隐藏新建与文件级写操作，且读取仍正常展示该智能体的数据根内容

### Requirement: 私有归属先于知识写入资格

知识写入 SHALL 先按实际数据根判定私有与共享归属，再应用当前生效的写入规则。非 owner 的管理员 MUST NOT 修改成员私有自有知识库；私有智能体 owner 也 MUST NOT 因拥有智能体而取得租户共享库写权。对象写能力投影 SHALL 与实际写入使用同一结论，不把某个公共写资格投影为所有私有对象可写。

#### Scenario: 管理员写入他人私有自有知识库

- **WHEN** 管理员持有公共知识维护资格但目标实际属于成员私有 Agent 的自有知识
- **THEN** 在写入前拒绝，不能新建、删除、移动、导入或借文件服务改写该私有库

#### Scenario: 所有者通过私有 Agent 指向共享库

- **WHEN** 私有 Agent owner 请求修改该 Agent 当前引用的租户共享知识库
- **THEN** 仍按公共知识写规则判定，个人所有权不会扩大共享写权限

#### Scenario: 对象写能力不一致的旧页面

- **WHEN** 旧页面仍显示允许写入而当前真实归属或授权已不允许
- **THEN** 服务端拒绝并要求刷新能力，不执行写入，也不将失败显示为已保存

