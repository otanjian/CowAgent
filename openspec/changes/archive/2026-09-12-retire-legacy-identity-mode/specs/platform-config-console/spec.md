## REMOVED Requirements

### Requirement: legacy 模式访问控制保持不变

**Reason**: `legacy` 身份模式不再存在，`/config` 与 `/api/models` 的共享控制台密码鉴权路径随之删除，本要求失去适用对象。

**Migration**: `/config` 与 `/api/models` 统一要求有效平台管理员资格，见本规范既有平台域访问控制要求。
