## REMOVED Requirements

### Requirement: 无用户身份时保持既有行为

**Reason**: 该要求在无 `user_id`（legacy 或单实例）时要求保持旧行为并 MUST NOT 新增拒绝路径；legacy 删除后无 `user_id` 属于身份解析失败，必须失败关闭而非放行。

**Migration**: 改由新增 Requirement「无用户身份时不得放行」承载。

## ADDED Requirements

### Requirement: 无用户身份时不得放行

当运行时身份没有 `user_id` 时，系统 SHALL 拒绝写入与检索个人域，MUST NOT 将用户域坍缩到智能体状态根，MUST NOT 伪造用户标识或沿用共享可见范围。缺少有效用户或租户上下文 SHALL 记录可诊断告警并按失败关闭处理。

#### Scenario: 无 user_id 身份读写记忆
- **WHEN** 身份未能解析出 `user_id` 而请求读写记忆
- **THEN** 系统拒绝该操作并记录告警，不写入或返回任何可能跨用户可见的记忆内容

#### Scenario: 无 user_id 的 user 作用域
- **WHEN** `scope=user` 但当前身份无 `user_id`
- **THEN** 系统拒绝该作用域访问，不伪造 `user_id`，也不泄漏到其它用户标识
