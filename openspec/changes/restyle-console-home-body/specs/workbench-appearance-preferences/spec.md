## MODIFIED Requirements

### Requirement: Coherent and accessible surfaces

每套配色 SHALL 覆盖公共侧栏、品牌文案前景、顶部栏、账号卡片与菜单、对话首页背景和快捷卡片、输入框及新外观面板；交互的选中、悬停、焦点与禁用状态 SHALL 保持可区分。正常正文与其背景对比度 SHALL 不低于 4.5:1，关键控件边界和焦点指示 SHALL 清晰可见。颜色 SHALL 不作为唯一的选中提示。快捷卡片上的分类色调 SHALL 仅作装饰，MUST NOT 编码选中、告警、权限或可用性状态，也 MUST NOT 取代品牌色在主操作与关键图标上的用法；配色切换 SHALL 不改变卡片的分类色调归属。

#### Scenario: Light sidebar with account menu
- **WHEN** 商务青蓝以浅色显示且用户展开账号菜单
- **THEN** 侧栏、品牌字、菜单、选中与焦点状态均可读，不出现固定白字叠在浅色背景或孤立黑色底块

#### Scenario: Narrow screen and keyboard
- **WHEN** 用户在 375px 宽窄屏或 1280×720 桌面视口中打开面板，或使用键盘操作
- **THEN** 所有配色、明暗、恢复与关闭操作可达，无横向溢出；单选可用键盘切换，Esc 关闭后焦点返回触发入口

#### Scenario: Localized labels
- **WHEN** 界面使用简体、繁体或英文
- **THEN** 外观名称、选项、保存限制和浏览器范围提示跟随当前语言，长标签不遮挡关键操作

#### Scenario: Decorative card hues under every palette
- **WHEN** 用户在浅色或深色下分别切换商务青蓝、深蓝侧栏与经典配色，并查看空态首页的六个快捷卡片
- **THEN** 每个分类色调在不同配色下保持同一归属且图标可辨，卡片标题与说明文字对比度不低于 4.5:1；分类色调不随配色改变，也不出现以颜色单独表达卡片状态的情形

### Requirement: Preserve application behavior and brand ownership

配色切换 MUST 仅更新视觉与本地偏好，不重载页面、不重置认证或会话、不清除输入、不打断正在显示的流式回复。系统 SHALL 保留既有业务菜单和权限归属，将页面与 Agent 信息合并为单顶栏、空态首页提供六个快捷入口和介绍下方的输入框；已有会话继续使用底部输入，其他业务页面沿用既有深浅适配。用户品牌来源与品牌设置中的局部深浅预览 SHALL 保持独立，Desktop 主题选择 SHALL 不受本能力修改。

#### Scenario: Switch during a conversation
- **WHEN** 用户已输入草稿或正在接收流式回复时切换配色或明暗
- **THEN** 草稿、附件、选中 Agent、业务会话与流式展示连续保留，代码高亮跟随解析后的明暗

#### Scenario: Choose multiple tenant-visible Agents
- **WHEN** 租户接口返回多个可聊天智能体，或当前会话已加入其他智能体
- **THEN** 输入区模型选择右侧的智能体入口保持可用，兼容租户接口字段与旧版启用字段，仅提供当前租户可聊天的切换或邀请候选
- **AND** 用户可在同一会话连续添加、移除成员而不丢失草稿；375px 窄屏菜单不超出视口，已有成员的移除操作不因候选列表缩减而消失

#### Scenario: Keep brand preview separate
- **WHEN** 用户切换个人配色或在品牌设置中切换局部预览明暗
- **THEN** 个人外观不写品牌名称、Logo 或描述，品牌局部预览也不覆盖个人外观选择

#### Scenario: Deliver the explicitly requested homepage layout
- **WHEN** 用户追加授权的首页布局交付
- **THEN** 页面使用单顶栏、侧栏新建对话及六个快捷入口（桌面三列两行、窄屏单列），品牌和全部业务菜单来源保持原归属
- **AND** 配色切换不切换页面结构，经典配色保留颜色方向而不恢复旧首页布局

### Requirement: Responsive task-oriented homepage

空白首页 SHALL 依次显示居中的欢迎介绍、真实输入框、六个快捷入口及简短提示；每条快捷入口 SHALL 提供一个分类色调图标、标题与一句价值说明。已有消息的会话 SHALL 使用原消息滚动与底部输入。系统 MUST 复用同一输入框及其附件、事件监听和会话状态，不因布局切换创建第二套输入框。三语与三配色明暗组合 SHALL 可用。

#### Scenario: Start from a suggested task
- **WHEN** 用户点击任意首页快捷入口
- **THEN** 本地化提示填入唯一输入框并可继续编辑，不自动发送或触发服务端任务

#### Scenario: Transition between empty and active chat
- **WHEN** 首条消息发送、历史会话恢复，或用户新建对话
- **THEN** 空态与消息态布局相应切换，输入框节点与附件操作保持可用，新建行为复用现有会话管理而不关闭其他会话的后台流

#### Scenario: Desktop grid and narrow column fallback
- **WHEN** 首页在桌面视口与 560px 以下窄屏分别显示
- **THEN** 桌面按三列两行等宽排布六个快捷入口，窄屏收敛为单列且卡片内容不截断，两种宽度均无横向溢出

#### Scenario: Short or narrow viewport
- **WHEN** 首页显示在 375px 窄屏或 1280×720 视口
- **THEN** 首页整体可滚动，输入、快捷入口与关闭操作可达，无横向溢出；输入区提示在放不下时独占一行完整可读，消息态保留独立消息滚动

## ADDED Requirements

### Requirement: Legible sidebar status and identity details

侧栏待办角标与账号卡片 SHALL 在既有三套配色与明暗组合下保持可读。待办角标 SHALL 呈现在 `todo-workbench` 中已定义的本人未完成数量文本（数量为 0 时隐藏，超过 99 时显示 `99+`），其数字与自身背景的前景对比度 SHALL 不低于 4.5:1，并 SHALL 以环形描边与侧栏背景分离。默认状态与逾期状态 SHALL 可区分，且逾期 MUST NOT 仅依靠颜色表达。角标在桌面侧栏收起为图标列时 SHALL 不遮挡或挤压导航图标。

账号卡片 SHALL 保持 `sidebar-account-menu` 已定义的单行紧凑契约：普通指针设备最小高度为 40px，头像保持 28px 圆角，姓名保持单行省略。`@用户名` SHALL 继续保留在悬停提示、辅助技术可读内容及展开菜单中，MUST NOT 因本能力在按钮内新增第二行。信息层级 SHALL 通过姓名文字权重与头像描边等视觉手段建立，头像描边 SHALL 取当前配色下的侧栏边界色，MUST NOT 使用固定颜色。

#### Scenario: Read the todo count under every palette
- **WHEN** 用户在商务青蓝、深蓝侧栏或经典配色的浅色与深色下存在未完成待办
- **THEN** 侧栏待办项显示未完成数量文本，数字与角标背景对比度不低于 4.5:1，且数字清晰可辨

#### Scenario: Overdue count is not expressed by color alone
- **WHEN** 存在逾期未完成待办
- **THEN** 角标除使用危险色提示外仍以数量文本表达，不出现仅靠颜色区分逾期与正常的情形

#### Scenario: Account card stays single-line
- **WHEN** 账号显示名很长，或用户在粗指针设备与折叠侧栏下查看账号区
- **THEN** 按钮保持紧凑单行且姓名省略显示，头像保持 28px 与配色描边，完整账号信息仍可在悬停提示与展开菜单中读取

#### Scenario: Collapsed sidebar keeps the count out of the way
- **WHEN** 桌面侧栏收起为图标列
- **THEN** 待办数量角标不覆盖或挤压导航图标，账号区保留居中的头像入口
