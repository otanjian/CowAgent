## Why

当前角色仅能选择九项功能权限，尚不能统一分配菜单、技能、工具、模型和智能体，技能写操作还复用agent.read。需要在现有角色体系补足资源分配，并使平台管理员默认具有动态all。

2026-09-09复核发现前稿引入独立目录索引、模型策略优先级、授权缓存版本及完整Web运行开放，超出此次需求。现按review.md精简，保留实际调用鉴权与租户边界；前期更大的运行和策略设计不再作为本change完成条件。

## What Changes

- 五类资源及动作按角色显式分配，多角色取并集；平台管理员从可信身份派生动态all，普通角色不自动获得新资源。
- 仅新增租户全局资源上限、角色资源授权两张关系表，默认模型作为角色字段，复用版本/事务/审计；目录从现有来源读取，不复制配置或索引库。
- 扩展现有角色CRUD和编辑，增加用途明确的资源目录、平台目标角色及资源上限入口；菜单/页签按授权和实际开放状态展示。
- 技能、工具、模型、Agent在现有装配及分发位置消费共用授权，空集合拒绝，后续调用重验。技能独立维护，同名技能用明确资源ID，模型fallback不得越权。
- 模型仅允许集合及可选默认值，沿用原选择链，不建设fixed/priority策略或独立成员权限预览中心。
- **BREAKING**：替代仅九项目录/不建设资源授权的阶段限制；agent.read不再授予技能编辑/启停。平台all调整有限权限约定，成员必须有有效租户，业务Membership/owner及未开放消费者约束保留。

## Capabilities

### New Capabilities

- `platform-all-authorization`: 动态平台all、明确目标租户授权管理、资格撤销及真实归属。
- `role-resource-authorization`: 五类资源标识、租户上限、允许集合、可靠保存与迁移。
- `role-model-assignment`: 允许模型、角色默认值、冲突处理和授权内回退。
- `resource-execution-authorization`: 现有技能/工具/Agent装配与执行守卫、空集合、委派及撤权。
- `role-authorization-console`: 现有角色页的资源/菜单配置及当前角色草稿校验。

### Modified Capabilities

- `business-permission-catalog`: 有限功能动作、平台all、资源授权及页面能力投影。
- `rbac-authorization`: 平台目标角色管理与功能/资源判断，保持身份和数据保护。
- `console-navigation-availability`: 菜单授权、目录/配置/执行状态和平台作用域。
- `tenant-skills-tools-console`: 独立技能维护、资源过滤及明确对象定位。
- `identity-management-workbench`: 角色资源选择、统一保存、复制与现有成员关联。

## Impact

- 依据：五类资源分配、平台all、每个成员必须分配租户及本次精简要求。参考docs/design/role-resource-authorization-plan.md与本change的review.md。PRD-02 v1.2原文缺失作为背景记录，不编造条款或作为无关阻塞项。
- 接入：auth策略/服务/存储、Web管理和资源handler、identity-admin.js、导航、技能管理、工具分发、Agent初始化/Bridge及模型解析。沿用Python/web.py、原生JS、SQLite和RuntimeIdentity。
- 存储：identity.db保存授权关系和角色默认模型引用，原资源来源持有内容/归属/凭据；复用角色/租户version和schema_migrations，不引入授权缓存revision或运行模式开关。
- 验收：角色配置、资源及菜单过滤、实际分发守卫、模型选择和迁移回归。用真实身份库和受控工具/模型替身验证现有组件，区分组件接入与线上运行开放。
- 延期：database Web聊天、流/轮询/取消及其他客户端开放，高级模型策略和独立成员权限预览。保留正式tenant-resource-isolation，不修改其关闭表；凭据、审批、配额及隔离要求继续适用于后续开放。
