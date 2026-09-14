# execution-isolation Specification

## Purpose
定义 database 多租户模式下任意代码执行（技能脚本、bash/子进程等工具）的隔离边界，防止跨租户读取、越权写入与权限提升，并在隔离未验收时对相关工具默认拒绝执行。
## Requirements
### Requirement: 多租户代码执行必须隔离

database 模式下，Agent 发起的任意代码执行（技能脚本、bash 及任何派生子进程的工具）SHALL 在隔离边界内运行，MUST NOT 直接读取其他租户的 workspace、identity 数据、凭据或系统敏感路径。隔离边界 SHALL 以租户目录与白名单根为限，路径穿越、软链接逃逸与用户提供的任意目录 MUST 被拒绝。隔离策略 SHALL 在服务端强制，不能仅依赖提示词或前端隐藏。

屏蔽区（用户 home、全局数据根、其他租户共享根）SHALL NOT 遮蔽本租户任一合法读/写根：路径仅在「落在屏蔽区**且**不属于本租户任一合法根」时才拒绝。部署实例根（`agent_workspace`，缺省 `~/cow`）与 operator 配置的租户基目录（`tenant_shared_base` / `COW_TENANT_BASE`）位于 home 之下时，本租户对其自身根的访问 MUST 被放行，而同一 home 中未被登记为合法根的路径（如凭据目录）MUST 继续被拒绝。屏蔽区的加入 MUST NOT 再依赖「默认智能体工作区」这一单一来源。

#### Scenario: 跨租户路径读取被拒绝
- **WHEN** 某租户的 Agent 尝试让技能或 bash 读取另一租户目录或 identity 数据
- **THEN** 执行前被拒绝，不返回其他租户内容，也不因同进程或共享凭据放行

#### Scenario: 路径穿越或软链接逃逸
- **WHEN** 代码执行请求的路径经 `..`、符号链接或绝对路径指向隔离根之外
- **THEN** 实际执行前拒绝该引用，不把已登记根当作所有子路径安全

#### Scenario: 本租户根位于 home 之下
- **WHEN** 默认智能体拥有位于实例根之下的私有工作区（例如 `<实例根>/agents/<id>`），且租户共享根为实例根或 operator 配置的租户基目录（均在 home 之下）
- **THEN** 隔离门禁放行该租户对自身共享根、Agent 工作区与用户根的文件读取、写入与 bash 访问，不再报「位于隔离根之外」

#### Scenario: home 内非本租户路径仍被拒绝
- **WHEN** 某租户的 Agent 尝试读取同一 home 下未被登记为合法根的路径（例如 `~/.ssh/id_rsa`）或全局数据根
- **THEN** 执行前被拒绝，不因本租户根位于 home 之下而放宽

### Requirement: 隔离未验收时默认拒绝任意代码

当执行隔离切片尚未验收或未启用时，database 模式下涉及任意代码执行的工具/技能 SHALL 默认拒绝执行，MUST NOT 因对话开放、权限授予或管理员旁路而放行。纯 LLM 对话与只读工具不视为任意代码执行。

#### Scenario: 隔离关闭时调用 bash
- **WHEN** 隔离未验收，而租户成员在对话中触发 bash 或技能脚本执行
- **THEN** 执行被拒绝并给出明确原因，纯文本 LLM 回复不受影响

#### Scenario: 隔离验收后获权执行
- **WHEN** 隔离切片验收通过，且调用者具备该工具/技能的 `tool.execute`/`skill.use` 授权
- **THEN** 代码在隔离边界内执行，超界动作被拒绝

### Requirement: 权限模式在租户语境下收紧

现有会话权限模式（read-only / workspace-write / full-access）在 database 多租户下 SHALL 按租户隔离根收紧解释：`workspace-write` 只允许在该租户工作根与 Agent 状态目录内写入，`full-access` MUST NOT 突破租户隔离边界或访问其他租户/全局敏感凭据。

#### Scenario: full-access 不突破租户边界
- **WHEN** 某租户会话以 `full-access` 运行
- **THEN** 仍受租户隔离根约束，不能读取其他租户目录或全局明文凭据

### Requirement: 隔离与授权逐次重验

每次代码执行 SHALL 在副作用前重验调用者权限、资源 grant 与隔离边界；授权撤销或隔离配置变化后，下一次执行 MUST 立即拒绝，不沿用缓存或已入队时的旧结论。

#### Scenario: 撤权后代码继续排队
- **WHEN** 代码执行任务入队后，调用者的工具执行权限被撤销
- **THEN** 队列执行前重验并拒绝，不因入队时有权限而执行

