## Why

当前方案把普通成员导向独立个人页面、个人接口和部分独立运行开关，现有控制台入口又仅允许管理员，导致同类资源维护存在两套体验。用户已明确将方案改为：普通用户和租户管理员共用现有控制台功能，页面、字段、操作和业务流程一致，仅按当前租户与资源归属限定数据范围；组织与权限、公共配置及平台运维继续保留管理资格。

## What Changes

- **BREAKING**：向具有有效租户身份和页面授权的普通用户开放现有控制台；使用现有智能体管理、记忆管理、消息渠道、工具与技能、模型服务页面，不再建设或接入独立个人功能页面、个人页签或缩减版表单。
- 每项功能使用一个正式页面、一套接口契约和业务服务。列表、搜索、统计、详情和写入共用后端数据范围判定；相同对象状态和授权条件下，普通用户与管理员使用相同字段、动作、校验及反馈。
- 智能体管理中，普通用户仅管理当前租户本人私有智能体；租户管理员管理当前租户共享智能体及本人私有智能体，排除他人私有智能体。创建时由服务端确定归属。聊天工作台仍保留已授权共享智能体的使用能力。
- **BREAKING**：共用详情页的「设为默认」统一设置当前用户在当前租户的默认智能体。租户默认保留独立、明确命名的管理配置；任何默认设置均不得隐式将私有智能体转为共享。
- 记忆管理使用同一列表、内容与管理流程，普通用户只访问本人记忆及本人私有智能体记忆；管理员额外管理获准共享记忆，仍不能访问他人私有正文。
- 消息渠道共用现有卡片、渠道目录、手工/扫码接入及启停和运行链路。普通用户仅维护本人连接，新增、改绑和启用只能选择本人可用私有智能体；管理员管理租户公共连接和本人连接，不增加个人/租户两套产品入口。
- 工具、技能和模型共用既有获授权目录及配置组件；公共定义、公共凭据及系统配置仍由对应管理员维护，普通用户维护其获准个人数据。同一资源的使用授权不随页面开放扩大。
- 统一菜单、能力和运行状态；旧个人页面与 API 只保留限期兼容转接。既有数据、归属、凭据和记忆版本机制保留为同一业务链路的基础设施，不保留独立个人功能实现。
- 删除用户截图中账号菜单的“我的资源”标题及“我的智能体、我的渠道、我的记忆、我的工具、我的技能”五项入口，覆盖聊天页、控制台及桌面/移动形态；保留账号设置、帮助、退出与版本信息，刷新和身份/租户切换后不得复活。
- 更新相关方案文档，明确取代旧的独立个人入口、角色专属功能流程及管理员专属控制台决策；归档文件作为历史证据保留。

## Capabilities

### New Capabilities

- `unified-console-access`：普通用户和管理员共用控制台的功能一致性、统一对象范围、首页统计、迁移与启用契约。
- `user-default-agent-selection`：本人按租户设置默认智能体、并发与审计、候选校验、默认解析与失效处理。

### Modified Capabilities

- `console-information-architecture`：控制台对成员开放，五个个人入口合入既有业务页面。
- `console-navigation-availability`：统一页面授权和旧菜单映射，消除独立个人入口与角色专属能力开关契约。
- `member-personal-console`：退役独立个人功能要求，仅保留通向统一控制台的兼容迁移。
- `agent-workbench`：区分管理范围和聊天使用范围，统一智能体创建与维护入口。
- `user-private-agent-management`：私有对象走共用生命周期和一致的对象状态规则。
- `rbac-authorization`：共用功能授权和对象范围规则，所有者检查先于管理员例外。
- `tenant-default-agent-administration`：租户默认独立命名并仅选共享目标，默认展示区分用户与租户。
- `user-personal-agent-provisioning`：系统供应只初始化尚无选择的用户默认，不覆盖自助设置。
- `agent-chat-launch`：按当前用户解析新会话默认，排除无权私有回落并保护既有会话。
- `database-memory-console`：统一记忆读写界面与接口，按真实归属过滤全部操作。
- `user-personal-context`：本人记忆从现有记忆管理维护，保留唯一版本和检索协议。
- `tenant-channel-configuration`：现有渠道接口服务范围内成员和管理员，统一操作与目标校验。
- `personal-channel-configuration`：保留个人连接的数据隔离，接入和运行收敛到共用渠道流程。
- `channel-scan-onboarding`：共用扫码流程绑定可信作用域和目标，不因普通用户另走一套流程。
- `tenant-skills-tools-console`：合并个人目录与参数入口，公共维护仍受管理员及资源授权限制。
- `sidebar-account-menu`：账号菜单只承载账号操作，业务资源从控制台进入。

## Impact

- 前后端接入：`auth/policy.py`、`auth/service.py`、`auth/store.py`、`auth/capability_matrix.py`；`channel/web/web_channel.py`、`admin_handlers.py`、`admin_overview.py`、`memory_console.py`、`route_registry.py`；`chat.html`、`console.js`、`personal-console.js`、`channel-workbench.js` 与三语文案。扫码授权、渠道启动/入站及私有智能体生命周期一并收敛；原生 Desktop 未验收部分不因 Web 变更宣称通过。
- 数据唯一归属：智能体仍用 `agent_bindings`；用户默认仍用 `memberships.default_agent_id`，增加独立偏好版本；渠道仍用 `tenant_channel_instances.scope/owner_user_id`；个人记忆仍用可信 tenant/user 根；参数与凭据沿用现有身份域存储。不新建第二套资源表，不重新供应或搬运用户数据。
- 兼容影响：版本化菜单映射、旧个人 URL/API 转接、旧默认动作区分与旧功能开关迁移均需真实验证；本轮只生成方案和待实施任务，不修改产品代码、主规范或部署配置。
- 跨 change 协调：当前工作区已归档 `enable-member-personal-console`、`move-personal-menu-to-account`、`remove-account-personal-resources-menu`、`upgrade-personal-channel-workbench` 及 `complete-desktop-and-scan-real-acceptance`。以已合入的主规范为基线，复用可验证的数据隔离成果，替代其入口/流程决策；归档状态不等同于真实运行验收，未合入主规范的历史提案不充当现行需求。
- 新方案说明位于 `docs/design/unified-console-access-plan.md`；相关菜单、权限、个人记忆、旧交付说明和架构分析增加明确的方案变更及历史适用范围。
