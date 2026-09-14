# workbench-appearance-preferences Specification

## Purpose
为 Web 控制台公共框架与对话首页提供可选择、可恢复的背景和界面配色，使用户在当前浏览器内获得一致且可读的外观。此能力明确配色与明暗模式的关系、旧偏好兼容、本地存储失败和多标签同步行为，同时保持品牌设置及业务功能的原有归属。
## Requirements
### Requirement: Selectable color presets

系统 SHALL 提供商务青蓝、深蓝侧栏和经典配色三套内置方案；每套方案 SHALL 可用于浅色与深色，且明暗模式选择 SHALL 不改变选中的配色。配色仅使用随应用交付的资源。

#### Scenario: Select a preset
- **WHEN** 用户在外观面板中选择商务青蓝或深蓝侧栏
- **THEN** 当前公共框架和对话首页立即显示该方案，名称、预览选中标识与页面实际外观一致
- **AND** 当前明暗偏好保持不变，经典配色仍可选

#### Scenario: Use classic appearance
- **WHEN** 用户选择经典配色
- **THEN** 页面恢复现有黑色侧栏、绿色交互及相应明暗内容的视觉方向，品牌图源与业务内容保持原值

### Requirement: Explicit and system appearance modes

系统 SHALL 支持浅色、深色和跟随系统三个明暗选项，将用户偏好与当前解析出的明暗分开；仅在跟随系统时 SHALL 响应系统变化。无法检测系统明暗时 SHALL 使用浅色，同时保留跟随系统的选择。

#### Scenario: Follow system changes
- **WHEN** 用户已选择跟随系统且系统由浅色切换为深色
- **THEN** 当前页面及代码高亮同步变为深色，配色方案不变，面板仍选中跟随系统

#### Scenario: Keep an explicit choice
- **WHEN** 用户选择浅色或深色后系统改变明暗
- **THEN** 页面保持用户显式选择，不自动改回跟随系统

#### Scenario: Missing system detection
- **WHEN** 浏览器不提供系统明暗检测且用户选择跟随系统
- **THEN** 页面采用浅色，不报错，面板保留跟随系统选项

### Requirement: One consistent appearance panel

系统 SHALL 从顶部现有主题位置提供外观选择面板，包含有名称的配色预览、可辨认的选中标记、明暗选项和恢复默认。账号菜单的界面偏好入口已接入时 SHALL 使用同一外观状态和面板。关闭面板 SHALL 保留已选外观。

#### Scenario: Open and reopen preferences
- **WHEN** 用户从顶部打开面板、选择外观、关闭后再次打开
- **THEN** 页面与面板保留已选值，无需额外保存或发布

#### Scenario: Use the account entry
- **WHEN** 账号界面偏好入口已可用，用户通过该入口打开外观设置
- **THEN** 看到与顶部入口相同的当前选择，修改后两个入口同步，不出现两个外观面板或重复切换

#### Scenario: Account expansion is not yet available
- **WHEN** 资料、改密或租户切换等账号扩展功能尚未交付
- **THEN** 顶部外观入口仍能独立使用，不因这些无关功能缺失而不可选择配色

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

### Requirement: Legacy preference migration and invalid value fallback

系统 SHALL 保留已有合法的明暗选择：未曾选择新配色而已有浅色或深色偏好的浏览器使用经典配色及原明暗；全新浏览器使用商务青蓝与跟随系统。非法字段 SHALL 独立回退：配色回退商务青蓝，明暗回退跟随系统，另一合法字段保持不变；无配色而已保存跟随系统时 SHALL 使用商务青蓝。初始化 SHALL 不擅自持久化为用户显式选择。

#### Scenario: Upgrade an existing browser
- **WHEN** 浏览器只有旧版浅色或深色偏好，没有新配色选择
- **THEN** 升级后使用经典配色与原明暗，刷新仍能恢复该视觉方向

#### Scenario: First use
- **WHEN** 两项外观偏好均未保存
- **THEN** 使用商务青蓝与跟随系统，不因初始化覆盖其他偏好或创建虚假的显式选择

#### Scenario: Change mode after legacy restoration
- **WHEN** 旧浏览器恢复经典配色后，用户只修改明暗且保存成功
- **THEN** 刷新后仍使用经典配色和新明暗，不因未保存配色而误用新默认

#### Scenario: Unknown values
- **WHEN** 配色或明暗的已存储值无法识别
- **THEN** 非法字段按默认规则回退，合法字段保持不变，页面能显示且用户可重新选择

### Requirement: Storage failures and tab synchronization

外观偏好的读写失败 SHALL 在外观处理路径内降级为本页状态，不因新增外观逻辑阻断既有页面；写入任一字段失败 SHALL 告知本页生效但刷新后可能恢复旧设置，不能宣称保存成功。支持存储的同源标签 SHALL 同步最新有效持久化选择，且处理同步事件 MUST 不触发循环写入。

#### Scenario: Persistence unavailable or partly failed
- **WHEN** 浏览器拒绝读取或写入，或仅一项偏好成功写入
- **THEN** 当前页仍可选择并显示完整外观，面板提示保存限制；刷新按届时有效存储状态恢复，不虚报持久化成功

#### Scenario: Change from another tab
- **WHEN** 同源另一标签保存了新的配色或明暗选择
- **THEN** 本标签读取并应用最新有效值，更新面板和页面而不刷新、不反写产生循环

#### Scenario: Concurrent tab choices
- **WHEN** 两个可使用存储的标签近同时修改外观
- **THEN** 处理相关事件后两个标签收敛到浏览器最终存储的配色与明暗组合，不承诺跨字段事务

### Requirement: Restore only appearance defaults

系统 SHALL 提供恢复默认操作，将配色恢复商务青蓝、明暗恢复跟随系统并按正常选择尝试保存；MUST 不重置语言、账号、租户、Agent、业务会话、草稿、品牌或其他浏览器偏好。

#### Scenario: Reset appearance
- **WHEN** 用户选择恢复默认且存储可用
- **THEN** 页面和选择项立即变为商务青蓝与跟随系统，刷新后保持默认组合，语言与其他状态不变

#### Scenario: Remove saved appearance
- **WHEN** 两项已保存外观偏好被清除，页面刷新或收到可用的跨标签存储清除事件
- **THEN** 外观采用新浏览器默认规则，不读取旧账号指针或修改服务器默认值

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

欢迎介绍与输入框组成的一组 SHALL 作为一个整体在可视区纵向居中，桌面与窄屏 SHALL 使用同一居中口径。快捷入口与提示 SHALL 保持在该组下方，允许首页在高度不足时整体滚动，MUST NOT 为满足居中而裁剪、隐藏或缩小快捷入口或输入框。居中偏移 SHALL 依据顶栏与输入组的实际高度计算，MUST NOT 写死为与内容无关的固定距离。输入组下方的折叠菜单高度 SHALL 收敛到该组下方仍可显示的空间并在内部滚动，MUST NOT 越出可视区或被首页容器裁剪至无法到达。

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

#### Scenario: Hero group is vertically centered
- **WHEN** 空白首页在桌面视口或窄屏视口显示
- **THEN** 欢迎介绍与输入框组成的一组的纵向中心与可视区中心一致（允许 1px 舍入误差），顶栏、输入组或视口高度变化后按实际高度重新居中，不出现与内容无关的固定偏移

#### Scenario: Centering keeps the task entries intact
- **WHEN** 可视区高度不足以同时容纳居中后的输入组与全部快捷入口
- **THEN** 首页整体可滚动，六个快捷入口仍完整位于输入组下方并可滚动到达，不被裁剪、隐藏或缩小

#### Scenario: Composer menus stay reachable below the centered input
- **WHEN** 用户在空白首页展开指令、附件、工作空间、模型或智能体折叠菜单
- **THEN** 菜单高度收敛到输入组下方仍可显示的空间并在内部滚动，菜单底边不出可视区，末项可滚动到达

### Requirement: Compact navigation with preserved functionality

系统 SHALL 提供单顶栏与可折叠侧栏，顶栏保留当前页面和对话 Agent 身份；侧栏 SHALL 提供新建对话及可展开的低频资源管理，保留全部既有菜单路由及权限。侧栏当前项 SHALL 使用非颜色视觉标记与程序可识别的当前项状态。

#### Scenario: Collapse navigation
- **WHEN** 用户在桌面收起侧栏或在窄屏打开导航
- **THEN** 桌面使用可操作的图标列和名称提示，窄屏使用可关闭抽屉，菜单仍可键盘操作且保持当前项

#### Scenario: Start a new chat from navigation
- **WHEN** 用户点击侧栏新建对话
- **THEN** 复用既有会话新建行为，先执行现有未保存编辑保护，保持原有草稿/附件继承及其他会话流式任务

#### Scenario: Navigate to a resource page
- **WHEN** 用户进入资源管理或系统设置内的页面
- **THEN** 对应分组展开并标记当前项，单顶栏显示正确页面名，非对话页不残留 Agent 上下文

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

