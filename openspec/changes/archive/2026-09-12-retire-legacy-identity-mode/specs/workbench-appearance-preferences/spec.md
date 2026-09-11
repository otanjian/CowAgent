## MODIFIED Requirements

### Requirement: Browser preference scope and persistence

系统 SHALL 明确展示偏好仅在当前浏览器生效、此浏览器中的不同账号共用外观设置；存储可用时 SHALL 在刷新后恢复选择。外观切换 MUST 不写实例配置、品牌存储或身份资料，也不要求管理权限；已允许进入控制台的 database 成员均 SHALL 可使用该本地能力。

#### Scenario: Restore after refresh
- **WHEN** 用户选择配色和明暗且浏览器成功保存后刷新页面
- **THEN** 首次显示主要内容时恢复相同选择，不先显示另一套默认配色再被运行时覆盖

#### Scenario: Switch account or tenant
- **WHEN** 用户在同一浏览器中退出、换账号或切换租户
- **THEN** 外观偏好保留，品牌和授权仍来自各自既有来源，界面不宣称跨账号隔离或跨设备同步

#### Scenario: Ordinary user changes appearance
- **WHEN** 普通成员选择背景配色
- **THEN** 外观立即应用，不要求品牌管理权限，也不发起实例配置、品牌或身份资料写入
