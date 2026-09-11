# 账户菜单与紧凑入口验收补记

记录日期：2026-09-09。范围为本会话已完成的菜单视觉重设计及随后单行底部按钮优化；本次补记阶段仅新增 change 文档和证据，没有继续修改产品代码。

## 最终实现

- 身份区配有头像，设置组包含资料、安全和偏好；帮助、退出及低强调版本入口按当前适用状态显示。所有操作采用装饰性线性图标，文字独立接收翻译和退出状态更新。
- 最终按钮为单行「头像＋姓名＋更多」，默认最小高 40px、头像 28px、footer 上下各 4px。用户名及状态副标题保留在 title 和可访问内容中，数据库完整姓名/用户名仍在展开菜单中可见。
- 主题层不再重复覆盖高度，桌面折叠态继续保留头像入口和右侧弹层。粗指针账户入口、菜单操作按钮至少 44px；版本链接维持紧凑次级样式，未声明其达到 44px。
- 保留现有账号状态、操作权限、偏好控制器、品牌/版本来源和菜单关闭协议。减少动态效果规则覆盖普通进入动画与交互过渡。

## 证据与结果

| 检查 | 实际结果与范围 |
| --- | --- |
| 本次补记重跑的前端回归 | 账户 37 项＋外观 29 项，共 66 项通过、0 失败；完整输出见 `evidence/frontend-tests.txt` |
| JavaScript 语法 | `node --check channel/web/static/js/console.js` 通过 |
| 菜单视觉阶段的矩阵 | 1440×900、1280×720、375×812，三配色×明暗共 18 组完成采集；0 页面脚本错误、0 抽样文字对比度失败；扣除下述首页数量旧断言后，原几何检查无失败 |
| 矩阵中的版本文字对比度 | 六组账户版本文字采样约 6.15:1～7.56:1；只代表该采样区域和阶段 |
| 偏好入口浏览器流程 | `one preferences dialog serves` 场景通过，0 页面脚本错误；账号与顶部复用既有弹窗、键盘与关闭行为正常 |
| 最终单行按钮实测 | 本会话 CUA 只读 DOM 测量：普通入口 40px、footer 49px、入口头像 28px；收起侧栏后入口仍为 40px、头像仍为 28px |
| 完整身份与菜单头像 | 最终单行入口的 title 保留姓名与 `@admin`；打开菜单后完整姓名/用户名可读，菜单头像保持 30px；Escape 和折叠侧栏操作正常 |
| 三语与移动端 | 菜单视觉阶段检查了三语外观标签；完整数据库菜单在英文切换后仍保留 5 个适用操作图标；375px 窄屏深色菜单可见可操作 |
| 运行中资源交付 | 最终按钮调整后读取 `localhost:9899/assets/css/console.css` 已包含 `min-height: 40px`；此前 `/chat` 已返回新分组、头像和退出标签结构 |
| OpenSpec | `openspec validate polish-sidebar-account-menu --strict` 通过；结果保存在 `evidence/openspec-validation.txt` |

矩阵和交互原始运行报告在本会话临时目录中；已从这些 JSON 提取关键结果、各组合几何和失败范围到 `evidence/browser-summary.json`。该摘要明确标记是菜单视觉阶段，发生在最后一轮单行按钮调整之前。

## 保留的失败与验证边界

视觉矩阵命令整体退出失败：既有测试要求首页恰好 3 张建议卡片，当前实际为 6 张；通过 `git show HEAD:channel/web/chat.html` 核对，修改前已存在 6 张。没有删除或改写该断言来制造通过结果。矩阵报告中的页面/对比度/几何采样仍单独记录，但不能称整条测试通过。

浏览器加载的是实际生产前端资源，后端由隔离 fixture 提供，未操作真实用户账号或调用外部模型。上述结果不作为后端鉴权、租户隔离或真实认证服务的验收。

最后一轮是 CSS 密度调整，只对最终单行按钮做了桌面、折叠、菜单身份和资源交付核验；没有再次执行完整主题矩阵，也未声称物理触屏、屏幕阅读器或文字缩放专项验收已完成。44px 粗指针和减少动态效果属于已实现且经代码复核的规则，不等同于物理设备实测。

## 任务核对与交付

- 1.1～1.2：对照已有三个 capability 和归档依赖完成范围确认，见 proposal/design；不涉及在途角色资源授权。
- 2.1～2.4：对应本会话现有 HTML、CSS 与退出文字目标修改；独立复核确认头像作用域、主题高度优先级和隐藏副标题处理符合实现。
- 3.1～3.3：分别对应持久化测试日志、浏览器摘要和上表的最终 CUA 实测记录；验证阶段与限制明确区分。
- 4.1～4.2：方案、设计、5 项 MODIFIED 与 1 项 ADDED、验收和回退说明已补齐，严格验证通过后将任务标为已完成。

没有数据迁移或新增 feature flag。交付与回退须同步账户 HTML、样式和退出文字节点目标，不回退其他任务修改。本 change 保留在 `openspec/changes/polish-sidebar-account-menu/` 供 review/后续归档；旧归档与主规范暂不改写。项目已有 `.gitignore` 忽略 `/openspec/`，本次沿用该规则，记录保存于本地工作区。

## 复验命令

```sh
node --test tests/test_sidebar_account_frontend.cjs tests/test_appearance_frontend.cjs
node --check channel/web/static/js/console.js
openspec validate polish-sidebar-account-menu --strict
```

浏览器已有脚本需要 Playwright 与 Chrome。可通过 `COW_APPEARANCE_BROWSER_FILTER='one preferences dialog serves'` 重跑交互场景，或 `COW_APPEARANCE_BROWSER_FILTER='all localized labels'` 重跑矩阵；后者当前仍会命中已记录的首页卡片数量旧断言。
