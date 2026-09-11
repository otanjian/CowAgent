# 相邻规范衔接表

复核日期：2026-09-08。当前 `openspec/specs/` 没有正式规范；下表中的来源均为未归档 change，不将其完成状态等同于生产可用。实施阶段 0 核对实际版本和交付证据，归档前再次核对；本次只维护当前 change，不改其他 change 的任务状态。

| 来源 change / capability / Requirement 原标题 | 本次处理 | 必须保留的合同 |
| --- | --- | --- |
| `prd-02-platform-navigation` / `platform-navigation` / 一级菜单分组、工作台分组菜单归属、定时菜单搬移、管理分组菜单归属、监控分组菜单归属、系统设置分组菜单 | 用本次双区域与22项迁移表替代旧位置、名称；classic 采用新职责在单侧栏排列 | 已实现功能可达、业务视图及原访问边界 |
| 同上 / 占位菜单项、面包屑联动 | 用移除无反馈占位、未开放目标说明、路由统一面包屑替代 | 原业务调用不被伪装成新功能；品牌展示仍沿原品牌合同 |
| `add-workbench-appearance-preferences` / `workbench-appearance-preferences` / Compact navigation with preserved functionality、One consistent appearance panel | 调整资源分组和顶栏位置，偏好统一由账号进入 | 响应式、焦点、外观完整能力 |
| 同上 / Browser preference scope and persistence、Storage failures and tab synchronization、Legacy preference migration and invalid value fallback | 保留；本次纠正“按账号/租户隔离外观”的错误描述 | 当前浏览器共用原键、合法旧偏好恢复、存储失败及多标签同步 |
| `move-session-history-to-workbench` / `session-history-workbench` / 历史会话位于工作台对话菜单之后、从历史页打开与新建对话、导航保持进行中状态和离页保护 | 名称改历史对话并增加 hash；将同步导航调用适配为可等待事务 | 位于对话之后、历史搜索/分组/恢复、不把返回当新建、草稿及后台运行 |
| `split-agent-configuration-and-workbench` / `agent-workbench` / 卡片展示职责并提供使用动作；`agent-chat-launch` / 点击卡片进入指定智能体的新聊天、切换取消与重复操作、目标失效不得切换为其他智能体 | 仅调整管理入口和导航提交时机，补取消/过期及同地址动作的事务约束 | 使用与维护分离、原发起会话语义、Agent/项目/租户归属、运行门槛 |
| `add-sidebar-account-menu` / `sidebar-account-menu` / 品牌版本入口迁移与动态同步、展示能力保持既有认证与数据边界 | 替代顶部语言/主题/退出必须保留的布局条款，关于与上游更新日志分开 | 本人权威信息、真实认证/退出结果、不在业务 session 存身份、迟到响应隔离 |
| `extend-sidebar-account-actions` / `account-menu-actions` / 六项菜单和只读个人资料完整保留、本人租户选择通过重新加载页面生效、界面偏好本地生效 | 账号主菜单合并为五类，租户切换主入口在顶栏，旧调用仍适配；本地偏好与刷新式切换保留 | 本人资料只读、原账号安全、改密前置、一次性切换目标、旧账号不能恢复 |
| `complete-enterprise-identity-access-control` / `identity-management-workbench` / 四管理菜单展示当前有效能力、用户页面支持可检索的完整成员生命周期、组织树和审计查询可完成实际操作 | 调整四菜单名称/分组；已交付全局账号和身份审计保留，分别登记范围与动作能力 | `/auth/me` 与当前租户 `/auth/context` 分工、原CRUD/过滤/分页/审计；不能借移除空 audit 入口删真实身份审计 |
| 同上 / `business-permission-catalog` / 有效权限上下文只补当前租户展示能力、平台控制面与租户身份管理资格独立 | 当前接口可追加有限页面/页签只读投影，平台动作由本人平台资格派生，租户动作由当前租户资格派生 | 原权限 ID、只读摘要不是授权凭证、平台身份不能替代租户成员或管理员 |
| `add-tenant-identity-access-management` / `user-membership` / 全局账号与租户成员独立、管理员创建账号或直接绑定已有账号、独立状态与最后管理员连续性 | 新增启用账号至少一条有效租户归属、创建原子分配、最后有效归属保护；收紧原允许停用唯一成员而账号仍启用的情况 | User/Membership 分离、多租户名片与权限隔离、绑定不改密码、原最后管理员保护、审计及版本 |
| 同上 / `identity-session` / 请求显式选择并验证租户；`extend-sidebar-account-actions` / `self-account-context` / 有效租户与成员摘要使用真实关系 | 无有效归属由正常使用改为受限恢复；平台操作仍不要求以 tenant_admin 授权，个人/恢复接口保留最小范围 | 真实 Membership 校验、无假租户、每标签上下文、AuthSession 不保存当前租户；平台接口无需把选择租户作为其授权凭证 |
| `add-workbench-todos`、`add-branding-settings` | 待办只改入口/筛选呈现并接入编辑保护，品牌只移入口 | 本人待办 owner、状态计数和来源验证；品牌唯一存储、版本发布与访问边界 |

## 交付顺序和入口补充

1. 阶段 0 将表内涉及的位置、页签、调用方式逐项映射到当前源码版本。全局账号放在成员管理的独立平台范围页签；身份审计保持已交付子视图的原宿主，对该宿主执行菜单迁移。补充记录原入口、新宿主/hash/tab、能力键、真实服务与验收路径。未交付则不登记为可用，不能为满足表格临时创建业务页。
2. 每个入口的范围按目标页签决定，不能让成员管理父页的租户范围盖住平台账号页签；审计仅沿原服务实际授权范围，不扩为任意租户审计。
3. 新租户约束由当前 change 在原身份模块落实，是用户补充要求而非旧规范已有保证。依赖事务/审计/真实写切片未完成时先交付依赖；旧的无 Membership 平台直达测试须按新行为改成受限恢复，并增加正常平台管理员具有有效租户的用例。
4. 若相邻 change 先归档，在其正式规范上按上表最小修改冲突条款；若后归档，则在其归档输入中保留本次已生效替代。不能整份覆盖或只靠归档时间决定有效要求。保留原安全和业务验收断言，新增约束另加场景。
