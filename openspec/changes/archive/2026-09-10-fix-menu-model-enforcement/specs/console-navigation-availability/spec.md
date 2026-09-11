## ADDED Requirements

### Requirement: 显式菜单授权限制页面可见性

当普通成员的有效角色集合中存在至少一条 `menu` 资源 grant（`nav:<页面>` / `view`）时，页面与页签可见性及直达判定 SHALL 同时满足该显式菜单授权集合与页面读取资格；未列入授权的已登记页面 SHALL 不显示，直接地址、hash、页内入口和侧栏快捷区块 SHALL 按同一投影拒绝或隐藏。当有效角色集合中完全没有任何 `menu` grant 时，系统 SHALL 沿用基于功能权限的兼容行为，不因缺少 grant 隐藏全部页面，以兼容内置角色和历史遗留自定义角色。平台 `all` SHALL 不受菜单 grant 限制。

#### Scenario: 未获授权的会话历史入口

- **WHEN** 成员角色已配置菜单授权集合但其中不含 `nav:workbench.history`，成员仍有 `history.read` 功能权限
- **THEN** 侧栏「会话历史」区块和对应视图入口不可见且不可直达，历史数据接口仍按功能权限与身份独立授权，不把菜单可见性当作会话数据的授权依据

#### Scenario: 内置角色没有菜单授权

- **WHEN** 成员只持有内置 `tenant_admin` 或 `member` 角色，其角色没有任何 `menu` grant
- **THEN** 页面可达性沿用既有功能权限行为，不因缺少菜单 grant 隐藏其原本可访问的页面

#### Scenario: 平台管理员

- **WHEN** 当前身份 `authorization_mode` 为 `all`
- **THEN** 菜单授权限制被跳过，页面可达性只受页面存在、读取资格与消费者开放状态约束

#### Scenario: 客户端伪造菜单可见性

- **WHEN** 受限成员通过改写本地 DOM、URL 或缓存尝试显示未获授权的页面
- **THEN** 权威投影仍报告该页面不可用，路由进入被拒绝状态，服务端接口继续独立鉴权
