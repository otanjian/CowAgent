# 3.1 控制台入口改为可信正式页面资格

本文件记录 `tasks.md` 3.1：`控制台` 入口不再只对管理员开放，改由权威页面投影准入；同时把
「组织与权限」「平台运维」「强制改密」「零租户恢复」四条边界留在原位。

## 1. 改动前的问题

`channel/web/static/js/console.js` 的入口闸门是纯客户端角色判定：

```
function _qualifyAdminConsoleEntry(opts) {
    if (!opts || opts.identityMode !== 'database') return true;
    return !!(opts.isPlatformAdmin || opts.isTenantAdmin);
}
```

服务端并不参与：`/admin` 在 `channel/web/route_registry.py:190` 注册为
`P("public", comment="admin console shell (same handler and reasoning as /chat)")`，即 `/admin`
只是与 `/chat` 同一个壳，真正鉴权在随后每个业务接口。因此「普通成员看不到控制台」完全由上面
三行客户端判定造成，且它用的是**角色**而不是**页面资格**——与
`console-information-architecture` 要求的「实际菜单 SHALL 仅展示……获准读取的页面」相反。

## 2. 新的准入谓词

`_qualifyAdminConsoleEntry({ identityMode, isPlatformAdmin, isTenantAdmin, mode, pages })`：

| 顺序 | 条件 | 结论 | 理由 |
|---|---|---|---|
| 1 | 非 `database`（legacy/public） | 放行 | 兼容模式无投影可依据，维持既有行为 |
| 2 | 平台管理员或当前租户 tenant_admin | 放行 | 管理资格本就可达 |
| 3 | 投影未知（`pages` 缺失） | 放行 | 与 `_viewNavDenied` 同一规则：不猜、不拦；服务端仍独立鉴权 |
| 4 | 存在 `admin.*` 页面满足 `available \|\| read_allowed` | 放行 | 正式页面资格 |
| 5 | 其余 | 隐藏入口 | 无管理区可访问页面 |

两条否决优先于第 4 条：`menu_denied === true`（菜单授权被撤）与
`reason === 'capability_disabled'`（部署收回了该能力）。二者都被逐项闸门隐藏，若仍算作准入
就会出现「有入口、无页面」的空壳。

判定只统计 **`admin.*`** 页面。`workbench.*` / `personal.*` 页面不在控制台壳内，不能成为进入
控制台的依据，否则同样产生空壳。`admin.` 正是同一个函数下方逐项闸门用来区分「壳内页面」与
「工作台页面」的标记（`if (key.indexOf('admin.') !== 0) return;`）。

## 3. 空分组

入口放开后普通成员会进入管理区，若只放开入口就会出现有标题、无子项的
「组织与权限」「模型与接入」。因此 `_applySidebarPermissions` 在逐项过滤之后按
`#sidebar-nav .menu-group.sidebar-hidden-admin-area` 重算分组可见性：分组内**没有任何**
未隐藏的 `.sidebar-item[data-view]` 时隐藏该分组。可见性每次重算、不做累积，撤权后分组会再次隐藏。

对未知页面的既有语义保持不动（「投影没签这个 key → 不隐藏」，见 `test_channel_scope_nav_frontend.cjs`
的 `a missing projection never hides or blocks the page`）：这类子项仍算可达，分组因此保留。这是
刻意的——不因投影缺项而隐藏用户原本可访问的页面。

## 4. 保留的四条边界

| 边界 | 载体 | 本 change 是否触碰 |
|---|---|---|
| 强制改密 | `_forcedPassword`（`console.js:764` 置位，`:672` 使鉴权回调失效，`:18583`/`:18594` 禁止关闭改密弹层） | 未触碰。改密是独立闸门，从不经入口可见性实现——tenant_admin 在改密期间一直可见该入口，故放开准入不构成放宽 |
| 零租户恢复 | 有效租户 gate；零租户平台身份保留平台职责入口 | 未触碰 |
| 组织与权限 | `admin.members` / `admin.roles` / `admin.organization` 读取资格（`auth/service.py:97-99`，`tenant.members.read` / `tenant.org.read`） | 收紧而非放宽：成员无读取资格时逐项隐藏，且分组一并隐藏；仅凭菜单 grant 不足以进入（`menu_denied` 否决 + 读取资格否决） |
| 平台运维 | `sidebar-hidden-platform-scope` 仍按 `isPlatformAdmin` 切换（`console.js:18181`）；`scope === 'platform'` 页面不构成准入 | 保留并复用为新增的否决条件 |

平台管理员与 tenant_admin 的可见集合不变：`authorization_mode === 'all'` 下逐项闸门视为全部
可用，分组全部保留。

## 5. 证据

```
node --test tests/test_nav_area_frontend.cjs tests/test_admin_area_group_gating.cjs
→ 12 passed, 0 failed

node --test tests/test_channel_scope_nav_frontend.cjs tests/test_workbench_menu_grant_frontend.cjs
→ 17 passed, 0 failed

node --test tests/*.cjs
→ 629 tests, 586 passed, 43 failed
```

新增/更新：

- `tests/test_admin_area_group_gating.cjs`（新）：成员拿到入口且只保留可达分组；tenant_admin 全保留；
  无可读管理页时入口与分组同时隐藏；合格成员直达 `/admin` 不再被弹回 `/chat`，不合格身份仍被弹回并
  置 `cow_nav_admin_denied`；分组可见性是重算而非累积。
- `tests/test_nav_area_frontend.cjs`：入口资格断言改为按投影判定（未知投影放行、`admin.*` 可读放行、
  全不可读拒绝、平台范围页面不构成准入、`menu_denied` / `capability_disabled` 拒绝、工作台与个人页面
  不构成准入）。
- `tests/test_channel_scope_nav_frontend.cjs`、`tests/test_workbench_menu_grant_frontend.cjs`：删掉
  各自对 `_qualifyAdminConsoleEntry` 的旧桩（`(p, t) => !!(p || t)`），改为抽取真实函数，避免留下
  与新契约不符的替身。

43 个失败全部为改动前既有失败，且集中在与本任务无关的 4 个文件（`_tmp_repro_modeldefaults.cjs` 1、
`test_appearance_browser.cjs` 1、`test_session_history_frontend.cjs` 36、`test_sidebar_account_frontend.cjs` 5）。
已在**回退本次三处改动后**重跑同样 4 个文件，失败数为 1 / 1 / 36 / 5，与改动后逐项一致。

## 6. 阶段边界

本任务只做「入口准入 + 空分组」。以下仍属后续任务，未在此声明通过：

- 3.2 普通用户接入 agents/memory/channels/skills/config 页面本体（共用组件、空列表创建资格）。
- 3.3 删除「我的资源」标题与五项个人入口。
- 3.5 概览数据源按本人可见范围下发。
- 3.6 真实浏览器双角色验收（本文件只有 Node 层证据，不含真实浏览器与真实租户运行证据）。
