## REMOVED Requirements

### Requirement: legacy 模式访问控制保持不变

**Reason**: `legacy` 身份模式不再存在，`/api/skills`、`/api/tools`、`/api/skills/content` 的共享控制台密码鉴权路径随之删除，本要求失去适用对象。

**Migration**: 技能与工具访问统一按 database 租户身份与资源权限校验，见本规范「缺少对应技能工具授权的成员被拒绝」与 `rbac-authorization`、`resource-execution-authorization`。
