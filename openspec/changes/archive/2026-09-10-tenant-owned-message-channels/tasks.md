## 1. 前置检查与迁移

- [x] 1.1 确认前置 change `open-database-runtime` 已归档，或其切片 4.x（入站身份绑定）与 7.x（凭据存储与注入）验收证据已齐备；未满足时不得声明本 change 的启用门槛通过，并记录归档顺序约束
- [x] 1.2 RED：为 `tenant_channel_instances` 迁移写失败测试（列与默认值、`NOT NULL` 约束、`(tenant_id, display_name)` 唯一、`version` 默认 1、审计字段）
- [x] 1.3 在 `auth/store.py` 增加版本化迁移建立该表与索引，使 1.2 转 GREEN
- [x] 1.4 验证迁移幂等且可重复执行，并断言不触碰 `team.json`、`credentials`、`credential_versions` 的既有行
- [x] 1.5 迁移回滚演练：停用租户渠道实例即可回退，表与凭据版本历史保留

## 2. 服务层：实例生命周期与授权

- [x] 2.1 RED：创建实例的单测——合法入参在单一事务内建立实例、一条 `credentials`（`resource_kind='channel'`、`resource_id=<instance_id>`、`name='channel:<instance_id>'`、JSON bundle 密文）、`credential_versions` 首版，以及 `credential.create` 与实例创建审计
- [x] 2.2 实现 `create_tenant_channel_instance`，使 2.1 转 GREEN
- [x] 2.3 RED：编辑实例与轮换凭据的单测——追加 `credential_versions`、递增实例 `version`、写审计、不新增重复凭据行
- [x] 2.4 实现 `update_tenant_channel_instance`，使 2.3 转 GREEN
- [x] 2.5 RED：启停实例的单测——只改 `active`、不动凭据与版本历史、写审计
- [x] 2.6 实现 `set_tenant_channel_instance_active`，使 2.5 转 GREEN
- [x] 2.7 RED：列表接口只出掩码投影，且响应、日志、审计均不含明文凭据
- [x] 2.8 实现 `list_tenant_channel_instances`，使 2.7 转 GREEN
- [x] 2.9 RED：版本冲突单测——过期 `expected_version` 返回 409 且不产生任何字段或凭据变化；实现校验使 GREEN
- [x] 2.10 RED：租户内显示名唯一冲突（409）与「同租户允许同类型多实例」「停用后重建同名允许」的单测；实现唯一性校验使 GREEN
- [x] 2.11 RED：授权单测——非本租户 tenant_admin 且非平台管理员读写一律 403；列表不返回他租户实例
- [x] 2.12 接入 `_is_control` 授权使 2.11 转 GREEN，并断言未新增功能权限、未改内置角色默认权限集合
- [x] 2.13 RED→GREEN：近期密码校验接入（缺失或错误的近期密码被拒，成功后不保留密码）

## 3. 系统侧凭据注入路径

- [x] 3.1 RED：按 `tenant_id + instance_id` 解密返回 bundle 的单测，含实例不存在、凭据已撤权、`resource_kind`/`resource_id` 不匹配三类错误
- [x] 3.2 实现内部注入方法：不做 actor 校验，按 tenant+instance 双重取值，不提供按 name 任意取值的入口，明文不打印不缓存
- [x] 3.3 断言该方法不经 HTTP 暴露（路由表与策略表均无对应端点）
- [x] 3.4 回归断言未放宽 `resolve_credential` 的既有校验（原有用例保持通过）

## 4. 租户可选渠道类型闸门（企微自建应用延后）

**实现期修正**：原「企微自建应用多实例适配」切片被证实不成立并延后，理由与后续设计见 `design.md` §6。`MULTI_INSTANCE_READY` / `CREDENTIAL_KEYS` / `channel_factory` / `wechatcomapp_channel.py` 均**不修改**。

- [x] 4.1 RED：断言 `wechatcom_app` **不在** `MULTI_INSTANCE_READY`、**不在** `CREDENTIAL_KEYS`，且 `channel_factory` 对 `wechatcom_app` 不启用 `new_instance` 绕过路径
- [x] 4.2 RED：以 `wechatcom_app` 调用 `create_tenant_channel_instance` 被拒（400），且不产生任何实例行与凭据行
- [x] 4.3 实现/确认闸门（服务层 `MULTI_INSTANCE_READY` 校验）使 4.1、4.2 转 GREEN，并断言无任何企微相关代码改动
- [x] 4.4 记录延后结论与后续切片必须解决的四个设计问题（入站分派方式、部署要求、`startup()` 读凭据、`issuer` 取实例配置）到 `design.md` §6，并在 `evidence.md` 标注为**未验收项**
- [x] 4.5 回归：`wechatcom_app` 的既有单实例行为不变（凭据仍读全局 `conf()`、入站仍走单例、`Query` 行为不变）

## 5. 飞书入站租户隔离端到端

原「企微自建应用入站身份」切片随 §6 延后；本组改为验证飞书（默认 websocket，天然按实例）这条真实路径。

- [x] 5.1 RED：`resolve_actor_for_context` 对租户渠道实例的入站解析到**该实例所属租户**（绑定 Agent 决定租户）的测试
- [x] 5.2 断言租户渠道实例的入站不会落到其他租户：他租户成员、未绑定发送者被封禁且不调用模型
- [x] 5.3 端到端：两个租户各一条飞书渠道实例并存，入站分别解析到各自租户（模型用替身）
- [x] 5.4 回归：实例级（`team.json`）飞书渠道的入站解析行为不变

## 6. 启动合成与运行期

- [x] 6.1 RED：`resolve_channel_instances` 接受租户实例并合成统一 `ChannelInstance` 列表的测试
- [x] 6.2 实现合成入参，`channel/` 层使用惰性 import 避免启动期循环依赖，使 6.1 转 GREEN
- [x] 6.3 RED：database 模式启动时取启用中的租户实例并注入解密凭据（通道与模型用替身）
- [x] 6.4 实现启动期合成与注入，使 6.3 转 GREEN
- [x] 6.5 RED：身份库故障或单实例凭据解密失败时不阻止 Web 控制台启动、该实例不启动并记录可诊断原因
- [x] 6.6 回归：legacy 模式不读取租户实例表，实例级与 legacy 渠道行为不变
- [x] 6.7 端到端：轮换凭据 → 重启 → 新凭据生效、旧值不可解密

## 7. HTTP 端点与路由策略

- [x] 7.1 RED：新增租户渠道端点的 handler 测试（列表/创建/编辑/启停，403、409、400 分支，`expected_version` 传递）
- [x] 7.2 实现 handler 并在 `auth/http_policy.py` 登记为 `tenant` 作用域加所需权限，使 7.1 转 GREEN
- [x] 7.3 RED：`/api/channels` 由 503 变为平台可用，且 **GET 与 POST 均可达**（POST 当前未登记，会被路由完整性闸门拒绝）
- [x] 7.4 更新策略表并验证平台管理员实例级读写可用，使 7.3 转 GREEN
- [x] 7.5 回归：`/api/feishu/register`、`/api/weixin/qrlogin` 维持 `closed`，未开放动作返回明确的不可用原因
- [x] 7.6 路由完整性回归：未登记方法仍被拒，未知路由仍 404

## 8. 能力投影、菜单与作用域

- [x] 8.1 RED：`admin.channels` 投影对平台管理员报 `scope="platform"` 且可用，对租户管理员报 `scope="tenant"` 且可用
- [x] 8.2 为该键实现投影显式分支（不再落入默认 `available: False`），使 8.1 转 GREEN
- [x] 8.3 断言租户管理员不获得实例级全局凭据——投影与接口双重拒绝
- [x] 8.4 断言能力投影与实际可用性一致（`channels` 报可用时配置接口确实可达，反之给出可用的关闭原因）
- [x] 8.5 前端导航按作用域的契约测试：`_consolePageForView`、`_viewNavDenied`、`_applySidebarPermissions`

## 9. 前端与 i18n（完成）

- [x] 9.1 RED：`loadChannelsView` 失败分支渲染明确文案并区分「无权限」与「尚未开放」，不再停留于持续加载
- [x] 9.2 实现失败分支与文案，使 9.1 转 GREEN
- [x] 9.3 租户侧实例列表（掩码展示）与新建/编辑表单（类型、显示名、绑定 Agent、凭据字段、启用开关）的契约测试
- [x] 9.4 实现租户侧视图与表单，使 9.3 转 GREEN
- [x] 9.5 写入被拒时展示可操作原因（密码强度、租户内同名、Agent 不属于本租户、409 版本冲突）并保留已填字段
- [x] 9.6 补齐 zh / zh-Hant / en 三语键，并纳入既有 i18n parity 测试
- [x] 9.7 按作用域切换渲染形态（平台管理员实例级视图 vs 租户管理员本租户视图）的契约测试

## 10. 验证、证据与文档（完成；10.8 门槛未满足，见 `evidence.md`）

- [x] 10.1 跨租户隔离专项全绿：读不到、改不了、不能绑定他租户 Agent、请求他租户实例不返回存在性
- [x] 10.2 凭据落点断言：写入后 `team.json` 无任何租户密钥字段；响应、日志、审计无明文
- [x] 10.3 mutation 检查：对作用域判定与租户一致性校验做变异，确认测试能捕获
- [x] 10.4 全量后端与前端 `.cjs` 回归，与基线对比并记录差异归因
- [x] 10.5 端到端证据：一条本租户飞书渠道从入站到执行的闭环
- [x] 10.6 编写 `evidence.md`：记录命令、输出、结论与未验收项（含企微自建应用延后项）
- [x] 10.7 文档与开关验收：确认不引入 feature flag，能力仅在 database 模式启用，更新交付表述
- [x] 10.8 启用门槛检查：**已执行，门槛未满足。** 跨租户隔离与端到端证据齐备（见 `evidence.md` §3/§4/§6），但 `open-database-runtime` 仍为 active 未归档（`evidence.md` §9）。因此本能力不得对外声明「已可用」；交付表述应为「已实现、已验证，启用待 `open-database-runtime` 归档」。
- [x] 10.9 已知缺口（已记录于 `evidence.md` §10.2，仍待用户裁量）：服务端只校验「未知凭据字段」与「空值」，**不校验按类型的最小必填集**。因此仅提交 `feishu_app_id`、缺 `feishu_app_secret` 的实例会被接受，直到下次启动时才以「无法启动通道」的可诊断原因拒绝。各通道启动守卫已核实最小必填集（飞书 `feishu_app_id`+`feishu_app_secret`；QQ `qq_app_id`+`qq_app_secret`；Telegram `telegram_token`；Slack `slack_bot_token`+`slack_app_token`；Discord `discord_token`；WeCom Bot `wecom_bot_id`+`wecom_bot_secret`；钉钉/微信待核），但在 `CREDENTIAL_KEYS` 中未区分必填与可选，本切片未新增 `REQUIRED_CREDENTIAL_KEYS`。
