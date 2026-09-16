# desktop-tenant-context Specification

## Purpose
补齐桌面客户端在数据库身份体系中的完整使用链路，使登录、强制改密、租户选择、业务请求和个人页面都使用同一可信上下文。跨租户切换、不同传输及身份失效不能产生错误归属、权限回退或私有数据残留。

本 capability 由 `complete-database-capability-parity` 部分归档时移交本 change：实现（后端授权码加 PKCE 会话、主进程 broker 与窄化 IPC、租户上下文传递、租户切换隔离、页面动作契约）已交付并有协议级证据，但真实打包 Desktop 客户端在两个租户、两个成员、失效身份和重连条件下的验收尚未执行，因此 `desktop_tenant_context` 切片保持未验收；本条移交 MUST NOT 被沿用为已验收的证明。

## Requirements
### Requirement: Desktop 使用浏览器授权码与 PKCE 建立原生会话

Desktop SHALL 对本地及远程后端使用系统浏览器授权码加 PKCE S256 协议，由主进程生成随机 verifier、state 及临时 loopback 回调路径。公开 client_id SHALL 固定为 `cowagent-desktop`；服务端 SHALL 仅接受登记的字面量 loopback 回调形式及本次准确 redirect_uri。远程后端 MUST 使用用户明确配置的精确 HTTPS origin；本地例外 MUST 限于主进程启动并登记的 loopback 后端，MUST NOT 将任意 HTTP 主机作为本地例外。

`/auth/login` SHALL 遵循既有 identity-session 的 Web 契约，仅设置受保护 Cookie，兼容 `token` 字段始终为空。`/auth/desktop/authorize` SHALL 在正常 Web 登录、必要改密及明确用户确认后签发授权码，确认写入 SHALL 校验 CSRF、来源和当前会话；仅 GET、已有 Cookie、User-Agent、自报 desktop 或 client_id MUST NOT 自动授权。该协议证明已确认授权及 verifier 持有，不得将公开客户端或 PKCE 视为客户端二进制身份认证。

授权码 SHALL 仅保存摘要，有效期 60 秒，绑定用户、发起 Web AuthSession、client_id、redirect_uri、PKCE challenge 和授权事务，并使用多 worker 一致的受保护存储。主进程 SHALL 校验回调路径、state 和可信后端上下文，只向同一 origin 的 `/auth/desktop/token` 提交 code、verifier、client_id、redirect_uri。服务端 SHALL 重验发起会话及账号状态，原子消费授权码并创建独立原生 AuthSession；重放或响应丢失 SHALL 重新授权，不得再次发放会话。缺失原生协议 SHALL 显示不支持或升级要求，MUST NOT 降级到普通登录取令牌或旧共享密码。

#### Scenario: 本地与远程使用同一授权协议
- **WHEN** 用户分别连接主进程登记的本地后端或明确配置的远程 HTTPS 后端并确认 Desktop 授权
- **THEN** 主进程通过受约束 loopback 回调和 PKCE 兑换独立会话，浏览器登录响应及 renderer 均不取得可重用 Bearer

#### Scenario: 已登录浏览器仍需确认
- **WHEN** 请求仅打开授权页，或账号仍处于强制改密状态
- **THEN** 系统不签发授权码，必须完成必要改密、重新登录及防 CSRF 的明确授权确认

#### Scenario: 拒绝授权码窃取和重放
- **WHEN** code 过期、已使用、verifier/redirect_uri 不匹配、发起会话失效，或主进程收到错误 state 的回调
- **THEN** 对应阶段拒绝完成授权，不创建额外会话，不接受 Cookie 或自报原生标记作为替代证明

#### Scenario: Web 冒充原生客户端
- **WHEN** 普通登录请求携带 Desktop User-Agent、自报客户端字段或公开 client_id
- **THEN** 登录成功也仅返回空 token 与受保护 Cookie，不能绕过授权码和 PKCE 换取原生会话

#### Scenario: 远程 HTTPS 形态与响应丢失
- **WHEN** 后端为明确配置的远程 HTTPS origin，或授权码兑换的响应在客户端丢失后重试
- **THEN** 远程形态与本地 loopback 遵守同一授权协议，重试不产生第二个原生会话，只能重新授权

#### Scenario: 旧后端拒绝降级
- **WHEN** 直连的后端不提供原生授权码与 PKCE 端点
- **THEN** 主进程显示不支持或升级要求，不退回普通登录令牌、旧共享密码或本地伪造会话

### Requirement: Desktop 主进程独占会话并约束 IPC 传输

Bearer SHALL 仅保存于主进程内存，并由统一 broker 为 JSON、上传、流、语音、预览、下载及本机导入附加到准确后端 origin；MUST NOT 通过 renderer、preload 返回值、浏览器存储、URL、日志或遥测交付令牌。应用重启 SHALL 重新授权，后端 origin 改变 SHALL 清除旧上下文并重新授权。回调 code/state/verifier 及完整回调 URL SHALL 脱离日志和遥测，授权响应与回调页面 SHALL 禁止缓存，回调页面 SHALL 禁止泄露引用来源。

preload SHALL 仅暴露窄化认证与业务 IPC，主进程 SHALL 验证 sender 是受信窗口的主 frame 及明确登记的页面入口，只接受经允许列表解析的业务 action/相对路径，不接受调用者设置认证头、任意绝对 URL 或覆盖后端 origin。broker SHALL 拒绝携带凭据的重定向。通用 `httpRelay` MUST NOT 访问原生认证端点、loopback 回调或已认证后端，也不得复用 broker 凭据。业务授权仍 SHALL 在服务端逐请求完成。

在线退出 SHALL 先撤销原生 AuthSession 再清理主进程会话；撤销失败 SHALL 停止业务请求并明确告知未完成服务端撤销，不得仅清理界面即宣称已注销。取消、超时与失败 SHALL 关闭临时监听并清理临时秘密。

#### Scenario: 不受信页面调用 IPC
- **WHEN** 子 frame、未登记页面或非受信窗口请求令牌、覆盖认证头或访问任意地址
- **THEN** 主进程拒绝调用且不暴露会话，通用转发接口不能作为旁路

#### Scenario: 携带凭据的请求跳转
- **WHEN** 已认证请求返回重定向，或用户切换到另一个后端 origin
- **THEN** broker 不转发原凭据；后端切换需要新的授权事务

#### Scenario: 各传输路径保持主进程令牌边界
- **WHEN** Desktop 上传、重连流、预览或下载文件
- **THEN** 请求使用同一 broker 的当前会话及租户上下文，renderer 仅取得业务数据或短生命周期 blob，不读取 Bearer

#### Scenario: 在线退出或撤销失败
- **WHEN** 用户退出且服务端成功撤销，或撤销请求失败
- **THEN** 成功时服务端拒绝旧会话并清理本地状态；失败时停止业务请求并显示撤销未完成，不宣称旧会话已失效

### Requirement: Desktop 先建立有效身份和租户再进入业务

Desktop SHALL 复用数据库账号及独立 AuthSession，完成适用强制改密后按目标域应用 gate。租户业务（包括个人记忆、个人渠道及私有智能体）SHALL 从本人权威信息选择有效租户，多租户提供选择，零租户不得初始化这些消费者；MUST NOT 从用户名、默认值或智能体推断租户。

完成改密的零租户账号 SHALL 仍可访问 `/auth/me`、账号设置及退出，具有当前平台身份者 SHALL 仍可进入获权平台管理，不要求租户选择或伪造 Membership。平台身份 MUST NOT 豁免租户业务资格，平台管理目标不得替换业务 tenant。强制改密期间仍 SHALL 仅允许改密和退出，不提供平台例外。程序化令牌发放 MUST 验证客户端协议，不能因请求自称 Desktop 向普通网页暴露可复用会话。

#### Scenario: 首次登录需要改密
- **WHEN** 用户以初始密码登录且服务端要求强制改密
- **THEN** Desktop 只提供允许的改密或退出操作，成功前不初始化聊天、任务或文件消费者

#### Scenario: 无租户或多个租户
- **WHEN** 本人信息返回零个或多个有效租户
- **THEN** 零租户保留账号身份域及获权平台入口，多租户为租户业务提供选择；均不生成假 Membership 或加载未确认租户的数据

#### Scenario: 零租户平台管理员进入平台管理
- **WHEN** 已完成改密的有效平台管理员没有任何租户 Membership
- **THEN** Desktop 仍显示获权平台入口并可调用平台接口，不发送伪造的租户选择；聊天、个人记忆及其他租户业务保持不可用

#### Scenario: 零租户普通账号及受限平台账号
- **WHEN** 普通账号没有有效租户，或平台管理员仍须强制改密
- **THEN** 普通账号仅保留允许的账号设置及退出且无平台入口；受限平台账号仅可改密或退出，不能借平台身份越过改密 gate

#### Scenario: 两个租户两个成员与失效身份
- **WHEN** 同一客户端依次以两个租户的两个成员登录，其间身份失效或被撤销
- **THEN** 每个成员只看到本人所属租户的业务数据，失效身份停止业务并引导重新授权，不残留前一租户的缓存或权限

### Requirement: Desktop 各类传输携带一致的已验证上下文

JSON、上传、语音、流式连接及重连、预览、下载和本机导入 SHALL 使用同一 broker 的有效会话，租户业务同时使用经验证的目标租户。可加头的租户请求 SHALL 携带 Bearer 和 `X-Tenant-ID`；账号个人身份域及平台请求不要求租户选择，不能因为缺少 tenant 被客户端拦截。不能携带头的传输 SHALL 通过受保护的资源派生上下文或带头读取完成，不得把 AuthSession 放入 URL。平台管理目标 SHALL 不替换当前业务租户，个人业务不得仅因 URL 含 personal 而免去租户授权。

#### Scenario: 登录后上传并回读附件
- **WHEN** 成员在 Desktop 当前租户的获准会话上传附件并打开预览
- **THEN** 写入与回读归属一致，不因遗漏租户上下文返回 missing_tenant，也不落入默认智能体空间

#### Scenario: 服务重连及新增业务方法
- **WHEN** 流重新连接或客户端调用一个使用公共传输入口的新业务方法
- **THEN** 请求仍按当前可信上下文鉴权，不沿用旧租户，不因新方法未单独配置而漏掉授权信息

### Requirement: Desktop 租户切换隔离请求响应和本地业务状态

切换账号或租户 SHALL 停止旧作用域的连接和业务轮询，隔离旧缓存、上传和未完成操作，并使旧上下文响应失效。MUST NOT 将旧写请求改成新租户后自动重发，MUST NOT 因迟到响应恢复旧租户数据或权限。

#### Scenario: 旧租户请求迟到
- **WHEN** 用户已切换租户后，旧任务列表或记忆请求返回
- **THEN** 响应不更新新页面，旧数据不进入当前租户缓存

#### Scenario: 保存期间成员资格被撤销
- **WHEN** Desktop 提交修改时服务端确认其当前租户资格失效
- **THEN** 停止依赖该资格的操作并引导恢复，不换用其他租户重试原修改

### Requirement: Desktop 消费同一页面动作与错误契约

Desktop SHALL 与 Web 共享权威页面、对象动作及消费者状态，向成员提供获准工作台及个人控制台入口，不据此开放公共管理。401、强制改密、租户失效、403、冲突和503 SHALL 分别呈现可操作状态，不转为空列表、假成功或 legacy 认证。

#### Scenario: 成员管理个人资源
- **WHEN** 成员进入已开放的个人智能体、个人渠道、个人记忆或获权工具技能页面
- **THEN** 可见动作与 Web 和后端当前 owner 授权一致，不能访问其他成员数据或公共管理动作

#### Scenario: 服务暂不可用
- **WHEN** 当前能力或业务接口返回503
- **THEN** 显示暂不可用及恢复入口，不把失败当成成功登录、空数据或操作完成
