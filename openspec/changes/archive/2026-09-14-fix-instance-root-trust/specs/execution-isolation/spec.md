## MODIFIED Requirements

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
