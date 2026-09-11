## Why

`legacy` 身份模式（共享控制台密码 + HMAC token + 单租户 instance-root + 免登录）与多租户账号体系（`database`）长期并存，导致每个消费点都要维护两套分支：授权路径存在「无身份即放行」兜底、认证存在「无密码即免登录」旁路、Desktop/外部 API/渠道/文件服务各自停留在旧凭据语义，无法统一到可信身份。项目决定不保留 legacy，把其能力全部沉淀到多租户/账号体系的 `database` 模式，使 `identity.db` 成为唯一身份真值、`cow_session` 成为唯一可信凭据。

## What Changes

- **BREAKING**：`identity_mode` 退场。系统仅支持 `database`；显式配置 `identity_mode=legacy` 时启动拒绝并给出可操作提示，不再提供双模式。
- **BREAKING**：删除共享密码 `web_password` 认证与免登录模式，删除 `cow_auth_token` HMAC token 与 URL query token。Web 只认 `cow_session`（HttpOnly Cookie），Desktop/程序化只认同值的 `Authorization: Bearer`。
- 删除所有「无身份即放行」路径（`ctx is None => 放行`）：无身份一律 401，无权/无成员 403，缺租户选择 400，身份库不可用 503。
- 新增首启自动初始化：`identity.db` 无平台管理员时自动创建默认租户与平台管理员，一次性输出随机初始密码并强制改密，使单机/个人版在 database 下「装上即用」。
- Desktop 迁移到 database 登录 + 会话 Bearer，废弃 `cow_auth_token` localStorage。
- 外部 OpenAI 兼容 API（原 `external_api_token`）迁移为**服务账号真实 User**：加密 API 密钥经凭据能力存储/掩码/轮换/撤权，请求以该用户身份执行，不伪造 Membership。
- 文件服务改为**平台级只读文件根**（默认数据目录）+ 租户成员限本租户/Agent workspace，均审计。
- 渠道实例必须显式登记（平台级/租户级），删除 `channel_type` 的隐式 legacy 合成；入站继续按 `external-identity-binding` 解析真实用户。
- 品牌写入仅走 `database` 平台管理员门，删除密码派生 Brand CSRF；待办统一挂租户作用域，owner 为空的历史待办归属初始化管理员。
- 删除迁移守卫 `refuse_legacy_after_migration` 与「legacy 启动行为不变」类场景；同步清理前端、Desktop 与三语文档中的 legacy 分支与文案。

## Capabilities

### New Capabilities
- `database-bootstrap`: 首启自动初始化默认租户与平台管理员、一次性初始密码与强制改密、`identity_mode=legacy` 拒绝启动、无身份不得进入业务。
- `service-account-api-access`: 外部 OpenAI 兼容 API 的服务账号真实 User、加密 API 密钥凭据（沿用凭据能力规则）、请求身份解析与撤权失效。
- `platform-file-browsing`: 平台级只读文件根、平台管理员浏览边界、租户/Agent workspace 作用域与审计。

### Modified Capabilities
- `identity-session`: 从「显式区分 legacy 与 database 双模式」改为 database 唯一模式；删除 legacy 兼容接口、免登录与旧 token 场景。
- `database-runtime-consumers`: 删除 legacy 启动场景；Desktop 由「未适配保持关闭」改为「已适配、按 database 身份运行」。
- `tenant-channel-configuration`: 渠道实例必须显式登记，禁止由 `channel_type` 隐式合成单租户实例。
- `branding-settings`: 品牌写入仅平台管理员；删除共享密码/免登录品牌写入与密码派生 CSRF。
- `todo-management`: 删除 legacy local-owner 语义，待办统一租户作用域，空 owner 历史待办归属初始化管理员。
- `todo-conversation-integration`: 删除 legacy local-owner 映射，工具主体一律取已验证 user/tenant。
- `todo-workbench`: 删除 legacy 共享登录主体呈现，页面一律按 database 身份与租户代次隔离。
- `tenant-skills-tools-console`: 删除「legacy 模式访问控制保持不变」要求，技能/工具一律按 database 租户权限。
- `platform-config-console`: 删除「legacy 模式访问控制保持不变」要求，`/config`、`/api/models` 一律按平台管理员资格。
- `scene-activation`: 删除 legacy `_require_auth()` 分支，场景目录一律要求有效租户与 `chat.use`。
- `execution-permission-console`: 删除 legacy 可编辑与会话权限模式分支，执行权限只由角色资源授权决定。
- `resource-execution-authorization`: 删除 legacy 会话权限模式参与放行/拒绝的场景。
- `user-personal-context`: 删除「无 user_id（legacy）坍缩到 Agent 根」语义，用户域一律按真实 user/tenant。
- `self-account-context`: 删除「legacy 模式拒绝 `/auth/me`」要求，改为统一 database 本人上下文。
- `sidebar-account-menu`: 删除共享密码/免登录侧栏呈现，统一按 database 账号与租户菜单。
- `workbench-appearance-preferences`: 删除 legacy/免登录可用性条款与 legacy 恢复场景。
- `console-settings-organization`: 删除「legacy 共享访问密码仅出现在系统设置」条款。
- `tenant-resource-isolation`: 删除「阻止启动 legacy 读取已迁移数据」条款，改为启用前置与不变量。
- `agent-chat-launch`: 删除 legacy 部署允许聊天与「不得降级到 legacy」之类的双模式表述。
- `agent-workbench`: 删除「legacy 模式不得进入租户归属」条款，工作区一律租户作用域。
- `business-permission-catalog`: Desktop 不再属于「未适配保持关闭」；删除回退 legacy 表述。

## Impact

- **代码范围**：`config.py`、`channel/web/web_channel.py`、`channel/web/auth_handlers.py`、`channel/web/{tenant_workspace,todo_handlers,admin_handlers,openai_api,branding}.py`、`auth/{credential,session,http_policy,ratelimit,store}.py`、`common/startup_hooks.py`、`app.py`、`agent/permission/isolation.py`、`channel/{channel_instances,chat_channel,external_identity}.py`、`agent/todo/*`、`agent/protocol/agent_stream.py`、前端 `channel/web/static/js/*`、`desktop/**`、`cli/commands/management.py`。
- **数据唯一归属**：`identity.db` 保有用户/租户/成员/角色/grants/凭据/服务账号与外部身份绑定；API 密钥与渠道凭据只以密文落身份库；Agent 工作区与业务会话仍在各自 workspace。
- **API / 客户端**：旧 `/auth/*` 共享密码登录、`cow_auth_token`、URL query token、`external_api_token` 全部失效；Desktop 与外部集成必须改用 database 登录或服务账号密钥。
- **兼容与恢复**：无存量安装，破坏性切换；不提供双模式回退。显式 `identity_mode=legacy` 拒绝启动，避免读已迁移库。
- **测试与文档**：重写清点报告列出的 legacy 断言测试，新增首启初始化/强制改密、Desktop Bearer、服务账号密钥生命周期、平台级文件根隔离、渠道显式登记、旧 `/auth/*` 拒绝；同步三语 `channels/web.mdx` 等文档。
- **依赖**：复用 `credential-management`、`external-identity-binding`、`tenant-channel-configuration`、`rbac-authorization`、`audit-log`、`execution-isolation` 既有能力，不新建并行真值。
