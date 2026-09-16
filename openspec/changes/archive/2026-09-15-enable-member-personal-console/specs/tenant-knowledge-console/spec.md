## MODIFIED Requirements

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

## ADDED Requirements

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

