## REMOVED Requirements

### Requirement: 个人范围与可信身份

**Reason**: 该要求包含「legacy SHALL 要求已配置登录并验证凭据，只映射一个稳定本地主体」及「免登录或主体失效」场景；legacy 与免登录删除后待办只有真实用户/租户主体。

**Migration**: 改由新增 Requirement「个人范围与可信身份仅真实账号主体」承载。

## ADDED Requirements

### Requirement: 个人范围与可信身份仅真实账号主体

系统 MUST 将所有列表、搜索、计数、详情、历史、来源和写入限制在当前可信空间及本人事项；所有者不可通过接口修改，本期不提供指派、转交、创建人旁观或管理员全量范围。待办 SHALL 使用真实有效用户、租户、成员及 `todo.read`/`todo.write` 权限；平台或租户管理员身份本身不获得他人待办访问权。系统 MUST NOT 提供免登录或匿名待办主体，MUST NOT 映射为本地单一所有者，MUST NOT 回退默认 Agent 或全局空间。

#### Scenario: 伪造所有者或跨租户请求
- **WHEN** 用户自报他人的 owner、空间或路径，或者直接访问非本人事项 ID
- **THEN** 系统拒绝扩大范围，不返回他人内容和计数；不可见 ID 与不存在 ID 返回一致结果

#### Scenario: 主体失效
- **WHEN** 用户、成员或租户失效，或身份解析不可用
- **THEN** 系统拒绝相应待办访问，不回退默认 Agent、全局空间或匿名主体
