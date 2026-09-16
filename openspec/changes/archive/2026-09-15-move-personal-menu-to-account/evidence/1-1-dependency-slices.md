# 任务 1.1 — 依赖切片确认（`enable-member-personal-console`）

任务要求：核对 `enable-member-personal-console` 五个页面、页面注册、菜单投影、能力开关及
上下文隔离的实际实现与相关验收证据，记录其尚未归档的增量要求和归档顺序；未满足的页面
继续沿用关闭状态。

## 1. 依赖 change 状态

| 事实 | 证据 |
| --- | --- |
| `enable-member-personal-console` 任务全部勾选 | `openspec list` 输出 `enable-member-personal-console  ✓ Complete` |
| 仍未归档（目录仍在 `openspec/changes/`，不在 `archive/`） | `ls openspec/changes/` |
| 归档顺序 | 依赖 change 先归档；本 change 只增加「入口宿主」增量，归档时不得覆盖其 `console-navigation-availability` / `console-information-architecture` 要求 |

## 2. 五个页面的实际实现（本 change 复用，不重建）

| 入口 | view ID | 页面能力键 | 菜单授权编号 | 实现位置 |
| --- | --- | --- | --- | --- |
| 我的智能体 | `personal-agents` | `personal.agents` | `nav:personal.agents` | `channel/web/static/js/personal-console.js` `PERSONAL_VIEWS[0]` |
| 我的渠道 | `personal-channels` | `personal.channels` | `nav:personal.channels` | 同上 `PERSONAL_VIEWS[1]` |
| 我的记忆 | `personal-memory` | `personal.memory` | `nav:personal.memory` | 同上 `PERSONAL_VIEWS[2]` |
| 我的工具 | `personal-tools` | `personal.tools` | `nav:personal.tools` | 同上 `PERSONAL_VIEWS[3]` |
| 我的技能 | `personal-skills` | `personal.skills` | `nav:personal.skills` | 同上 `PERSONAL_VIEWS[4]` |

- 页面注册：`personal-console.js` 末尾通过 `window.registerConsoleView({id, label, load, repaint})`
  为五个 view ID 注册加载器，`console.js` 的 `CONSOLE_VIEW_REGISTRY` 统一分发（不再在
  `console.js` 内硬编码分支）。
- 页面能力映射：`console.js` 的 `VIEW_META` 五个条目带 `console: 'personal.*'`，
  `_consolePageForView` 据此读取 `/auth/context` 的 `console_pages` 投影。
- 深链接：`_bootAreaDefaultView` 识别 `/chat#view-personal-*`，经 `navigateTo` 与原身份/页面
  检查进入对应页面（`console.js` 中 `hashView && VIEW_META[hashView]` 分支）。
- 能力开关：`auth/policy.py` 的 `PERSONAL_CAPABILITY_SWITCHES` / `PERSONAL_PAGE_CAPABILITIES`，
  投影在开关链未全开时给出 `available=false, reason='capability_disabled'` 并附 `switches`
  （见 `enable-member-personal-console/evidence/9-1-capability-switches.md`）。
- 上下文隔离：`personal-console.js` 的 `_generation` 代际计数在租户切换（`invalidatePersonalViews`）
  与每次重载时递增，晚到响应被丢弃；请求带 `X-Tenant-ID`，服务端按 `(tenant_id, user_id)`
  过滤。
- 已通过的验收证据：`enable-member-personal-console/evidence/8-personal-console-evidence.md`、
  `9-1-capability-switches.md`、`9-6-final-acceptance.md`；测试
  `tests/test_personal_console_frontend.cjs`（56/56）与
  `tests/test_personal_console_browser.cjs`（11 场景）在本 change 开始前全部通过。

## 3. 尚未归档的增量要求（本 change 必须保留）

`enable-member-personal-console` 已在其 delta 中写入、尚未合并进主规范的要求：

1. 「显式菜单授权限制页面可见性」——撤掉 `nav:personal.*` 菜单授权时个人入口隐藏、直接地址
   落到拒绝说明且不启动消费者。
2. 「个人能力开关只控制开放且不替代权限检查」——`capability_disabled` 先于平台 `all` 短路，
   关闭执行类开关不关闭目录。
3. 个人页面的三态（可读 / 可配置 / 可执行）在页头分离呈现。

本 change 的 delta 不重写上述要求，只规定入口宿主、当前项标记与宿主迁移后的可达性；实施时
若与上述要求冲突以依赖 change 为准。

## 4. 未满足项与保持关闭状态的页面

本次核对未发现五个页面中被标记为「未满足」而需要保持关闭的项：依赖 change 的 24 个任务
已全部勾选，前端契约与浏览器契约在基线上通过。因此本 change 不新增「保持关闭」名单；
`personal_channel_runtime`（渠道执行）按既有默认保持关闭，其目录与配置入口照常展示。
