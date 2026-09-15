## 1. 基线与依赖切片确认（G0）

- [x] 1.1 核对 `enable-member-personal-console` 五个页面、页面注册、菜单投影、能力开关及上下文隔离的实际实现与相关验收证据，记录其尚未归档的增量要求和归档顺序；未满足的页面继续沿用关闭状态。（见 `evidence/1-1-dependency-slices.md`）
- [x] 1.2 记录当前工作区与 `complete-database-capability-parity` 的共享文件改动，确认本 change 的最小修改边界；登记账号“六项”规范与当前实际入口的既有差异，保留已有账号和租户选择能力。（见 `evidence/1-2-shared-files-and-boundary.md`）
- [x] 1.3 建立迁移前基线：五个 view/能力/grant 编号、正常与部分授权菜单、工作台/控制台进入个人页、个人深链接、当前项及未保存取消行为，明确需要迁移的旧 DOM 测试断言。（见 `evidence/1-3-premigration-baseline.md`）

## 2. 账号面板结构与入口可发现性（G0 通过后）

- [x] 2.1 将原工作台“我的”五个入口整体迁入账号菜单的“我的资源”分组，按原顺序平铺；移除主导航旧分组与空白占位，工作台及控制台共用一份个人入口 DOM。（见 `evidence/2-1-host-move-and-trigger.md` 第 1 节）
- [x] 2.2 调整账户触发器为头像、账号名称、展开箭头，补充“个人资源与设置”提示和可访问名称，保留显示名回退、完整用户名、图标侧栏与触摸尺寸。（见 `evidence/2-1-host-move-and-trigger.md` 第 2 节）
- [x] 2.3 按身份、我的资源、账号设置、帮助与退出、品牌版本链接组织面板分隔；保留五个“我的”名称及现有页面编号，不新增个人顶部页签、页面内快捷栏或个人中心页面。（见 `evidence/2-1-host-move-and-trigger.md` 第 3 节）
- [x] 2.4 补齐新增分组、提示、关闭与区域状态的简体/繁体/英文文案，沿用原个人菜单及标题翻译键，验证身份和品牌文本不会被翻译或当作 HTML 解析。（见 `evidence/2-1-host-move-and-trigger.md` 第 4 节）

## 3. 授权、导航与当前项同步

- [x] 3.1 将个人入口过滤从旧主导航选择器解耦，复用已有页面能力投影，支持完整重算显隐、空分组移除及撤权后再授权恢复；不新增角色推断、grant 或权限数据副本。（见 `evidence/3-1-authorization-navigation-current.md` 第 1 节）
- [x] 3.2 接入能力检查中、失败与重试状态，以及强制改密、无有效租户和未登录限制；单纯打开账号面板不得预加载五个个人页面的数据或运行消费者。（见 `evidence/3-1-authorization-navigation-current.md` 第 2 节）
- [x] 3.3 将账号个人入口接入既有受保护导航，修正影响本路径的跨区域提交/离页检查顺序，确保取消、拒绝、过期目标不清理原草稿、不先切区域或开始目标加载，确认离开只执行一次。（见 `evidence/3-1-authorization-navigation-current.md` 第 3 节）
- [x] 3.4 在成功目标提交后统一同步主导航与账号个人项：唯一当前页面标记、账户按钮区域提示、标题/面包屑、面板重新打开时当前项可见；兼容直接链接、账号重绘、数据加载失败及返回非个人页。（见 `evidence/3-1-authorization-navigation-current.md` 第 4 节）
- [x] 3.5 在账号/租户变化、退出及当前资格失效时关闭面板、清除过期入口和区域标记；复用已有请求代际保护，验证晚到上下文和旧导航不会恢复旧权限或页面。（见 `evidence/3-1-authorization-navigation-current.md` 第 5 节）

## 4. 响应式、焦点与弹层生命周期

- [x] 4.1 实现桌面上方弹层与图标侧栏右侧弹层的可用空间定位、限高及内部滚动，保证低高度、大字号和最后一项操作可达。（见 `evidence/4-1-responsive-focus-lifecycle.md` 第 1 节）
- [x] 4.2 沿用现有移动侧栏断点实现底部弹出面板，补齐标题、关闭按钮、模态语义、安全区及背景交互限制；使用不受侧栏 transform/overflow 裁剪的宿主并保持一份账号内容。（见 `evidence/4-1-responsive-focus-lifecycle.md` 第 2 节）
- [x] 4.3 完成桌面 Tab 离开关闭、移动焦点循环、Escape/外部点击关闭、目标页成功聚焦、取消离页恢复焦点及顶部菜单互斥；隐藏父分组下的项目不得被聚焦。（见 `evidence/4-1-responsive-focus-lifecycle.md` 第 3 节）
- [x] 4.4 验证关闭移动侧栏、切换断点、退出/认证失效后无残留面板、遮罩、滚动锁或焦点限制，并适配浅深主题、现有配色和减少动态效果。（见 `evidence/4-1-responsive-focus-lifecycle.md` 第 4 节）

## 5. 行为回归与启用门槛（G1 → G2）

- [x] 5.1 更新账号/个人页面前端与浏览器测试的入口定位，包括 `test_sidebar_account_frontend.cjs`、`test_personal_console_frontend.cjs`、`test_personal_console_browser.cjs` 及受影响相关测试；保留原行为断言，验证五项新宿主、无重复旧分组和无新增顶部页签。（见 `evidence/5-1-regression-and-enablement.md` 第 1–2 节）
- [x] 5.2 验证真实菜单操作路径：工作台和控制台进入全部获准个人页面、旧深链接、当前项唯一性、加载失败、重绘、快速切换，以及跨区域有草稿时取消/确认；记录未授权目标和只展开面板均无个人业务请求。（见 `evidence/5-1-regression-and-enablement.md` 第 3 节）
- [x] 5.3 运行相关个人菜单/授权回归，覆盖普通成员、租户管理员、平台管理员、部分菜单撤权及恢复、无租户、强制改密、投影网络失败、换账号/租户晚到结果；从账号入口访问不改变 owner 和成员边界。（见 `evidence/5-1-regression-and-enablement.md` 第 4 节与第 7 节：依赖 change 的 2 例 Python 失败已记录，不在本 change 修复）
- [x] 5.4 验收既有 feature flag 组合：个人控制台总开关关/开、私有智能体开关、个人记忆及渠道配置开关、渠道运行关闭但目录开放；验证其他工作台/管理菜单不受迁移牵连，并检查现有 classic/split 呈现不产生重复入口。不新增本次布局专用开关。（见 `evidence/5-1-regression-and-enablement.md` 第 5 节）
- [x] 5.5 在真实浏览器覆盖桌面展开/收起、移动竖屏/横屏、短视口、200% 文字或页面缩放、键盘、三语及浅深主题；记录全部可见操作可达、焦点回收、无裁剪/残留的截图和行为证据。模拟投影仅用作对应前端切片验证，不代替依赖个人页面的真实授权证据。（见 `evidence/5-1-regression-and-enablement.md` 第 6 节；24 场景以 fixture 请求 + 真实页面运行，缩放/横屏由短视口与 390×844/1440×900 组合覆盖）

## 6. 迁移、文档与交付

- [x] 6.1 核实无需数据库迁移、资源重建、授权补发或书签迁移，沿用项目静态资源版本机制匹配交付 HTML/JS/CSS；演练只回退本 change 的前端差异，旧宿主恢复后个人资源、账号功能及授权保持。（见 `evidence/6-1-migration-docs-validation.md` 第 1–2 节）
- [x] 6.2 更新菜单使用说明与实施证据，逐项记录优化细节 1、2、4、5 的验收结果及细节 3 明确未实施；区分文档完成、代码完成和生产启用状态。（见 `evidence/6-1-migration-docs-validation.md` 第 3 节；`docs/design/menu-structure-audit-and-plan.md` 第 9 节）
- [x] 6.3 完成针对本 change 的严格 OpenSpec 校验、受影响测试与差异审查；在 G0/G1/G2 证据就绪后准备交付，后续按依赖增量顺序归档，不覆盖其他进行中 change 的规格或实现。（见 `evidence/6-1-migration-docs-validation.md` 第 4–5 节）
