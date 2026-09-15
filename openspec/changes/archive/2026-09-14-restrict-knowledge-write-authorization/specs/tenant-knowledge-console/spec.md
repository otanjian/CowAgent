## REMOVED Requirements

### Requirement: 知识库写入受权限或租户管理员资格约束

**Reason**: 该口径只按「功能权限或管理员资格」授权，与该智能体的归属和实际数据根无关：普通成员无法维护自己私有智能体的知识库，而持有 `knowledge.write` 的成员却能改写整个租户共享库。本 change 以「按数据根与智能体归属授权」取代它，并移除 `knowledge.write`。

**Migration**: 从权限目录、内置角色显式默认集合与角色存量中移除 `knowledge.write`（存量由一次性迁移剥离）。知识库写入口改由 Agent 管理投影的 `can_write_knowledge` 驱动；直接请求仍由服务端按新规则判定，无写权返回 `403`。

## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: 控制台知识库页面不因消费者关闭而无限加载

控制台「知识库」视图 SHALL 在读取入口返回非成功响应时清除「加载知识库中...」占位并呈现可读原因，MUST NOT 停留在永久加载态。当响应表示该消费者在当前身份模式下未开放时，页面 SHALL 说明功能不可用；当响应表示缺少读取权限时，页面 SHALL 说明权限不足。「新建」及其他写入口（重命名、删除、移动、导入）SHALL 按当前所选智能体的写能力投影渲染，MUST NOT 向无写权限的成员呈现必然失败的操作。

#### Scenario: 消费者关闭时呈现原因

- **WHEN** `/api/knowledge/list` 返回 `503 database_unavailable`
- **THEN** 页面显示功能未开放的可读提示并隐藏加载占位，不无限转圈

#### Scenario: 无写权限不展示新建入口

- **WHEN** 当前所选智能体对当前成员不可写（租户级智能体的非管理员成员、或其他成员拥有的私有智能体）
- **THEN** 页面隐藏「新建」入口与文件级写操作，且直接请求写接口仍返回 `403`

### Requirement: 知识库页按智能体说明共享与独立

知识库页面 SHALL 用中性文案说明知识库是按智能体「共享」或「独立」的，并指向「智能体管理」的设置入口；该文案 SHALL 同时说明维护责任——租户级智能体的知识库由本租户管理员维护，私有智能体的知识库由该 owner 本人维护。MUST NOT 声称知识库默认对全员共享。该文案 SHALL 在简体、繁体、英文三种语言下均提供，且与 i18n 目录及快照保持一致。

#### Scenario: 页面说明文案

- **WHEN** 打开知识库页面
- **THEN** 说明文案表示知识库按智能体共享或独立、可在「智能体管理」中设置，并标明租户级由租户管理员维护、私有智能体由本人维护，不再出现"默认全员共享"的表述

#### Scenario: 三语一致

- **WHEN** 依次以简体、繁体、英文渲染知识库页面
- **THEN** 三种语言都提供该说明文案，且 i18n 目录与快照中该键的取值与页面一致
