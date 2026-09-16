# 5.4 / 5.5：公共面写入的资格判定（本轮已交付部分与未交付部分）

对应 `tasks.md` 5.4、5.5。两项任务**部分交付**：公共技能定义/全局启停的资格判定已收口并有
验收；个人参数整合进共用详情组件、成员模型目录、退役旧个人资源页仍是缺口，见 §4。

## 1. 已交付：功能授权不决定租户公共面

### 缺陷（实测，改动前）

`_require_agent_management_scope` 在请求未带 `agent_id` 时直接返回，理由写在它的 docstring 里：
「未命名 Agent 时锚定的是调用者自己身份解析出的 Agent，请求体无可扩大之处」。这个前提对技能
不成立：控制台从不发送 `agent_id`，而 `_skill_service('')` 解析的是 **Agent-less 锚点**，
即租户共享根——于是写入落在共享面，却没有任何 Agent 范围被检查。

用真实 `build_web_app()` 实测（成员角色仅持功能权限 `skill.read/use/edit/enable` + 该技能资源授权）：

| 调用 | 改动前 | 改动后 |
| --- | --- | --- |
| `POST /api/skills/content {resource_id, content}` | 200（内置技能由只读来源保护挡下；租户自建技能无此保护） | 403 `forbidden` |
| `POST /api/skills {action:"close", resource_id}` | **200 + 全局状态翻为 disabled** | 403 `forbidden` + 状态不变 |
| `POST /api/skills {action:"close", name}` | 403（原因错误：见下） | 403（管理资格，原因正确） |

日志证据（去掉守卫的变异运行）：`[SkillService] close: skill 'tenant-note' disabled` ——
一个成员把共享技能对全租户关掉了。

### 形态（规则只写一次）

* `_require_skill_write_scope(ctx, agent_id)`：命名 Agent 走对象范围（`_require_agent_management_scope`，
  归属优先，私有 Agent 归其主人、共享需管理资格）；**未命名**则视为对租户共享面的写入，要求
  `ObjectScope.allows_public_configuration()`（即管理资格本身，功能授权不替代，task 2.2）。
  该谓词此前只在测试与证据文档里被调用，生产路径一次都没用过。
* 落点：`SkillsHandler.POST`（全局启停）与 `SkillContentHandler.POST`（正文写回）同一处规则。

### 顺带修掉的第二个缺陷：页面自己的载荷被拒

启停路径把 `resource_id or name` 直接交给资源授权检查，而授权记录与目录都使用
`{source}:{name}`。于是成员拿着该技能的 enable 授权，用控制台**实际发送**的
`{action, name}` 载荷请求得到 403，而同样的调用换成 `resource_id` 得到 200——页面自身不可用。
`_resolved_skill(service, name, resource_id)` 先解析再归一化，门禁与写入拿到同一个已解析对象。

### 第三处：页面不广告会被拒绝的动作

开关此前无条件写进卡片，成员看到的是一个点击必被拒的控件。读取载荷现在每行携带
`actions.enable`，由**写入路径的同一对权威**算出（管理资格 + 该资源 `enable` 授权），
前端 `renderSkillCard` 据此要么渲染开关、要么渲染只读状态（状态本身仍可见——知道一个技能
是启用状态并不是那个动作）。`skill_global_toggle_managed` 三语文案已加，i18n 快照同步。

## 2. 验收

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 后端（8 项） | `pytest tests/test_skill_public_surface_scope.py` | 8 passed |
| 前端（4 项） | `node tests/test_skill_public_surface_frontend.cjs` | 4 passed |
| 技能/授权/菜单回归带 | `pytest -k "skill or tool or scope or object_scope or resource_auth or http_policy or menu or console or i18n or channel"` | 1558 passed, 1 skipped |
| 前端全量 | `for f in tests/*.cjs` | 与 HEAD 基线一致（4 项既存失败，见 `8-4-frontend-baseline.md`） |

断言的是**效果**而非状态码：共享文件字节、共享启停状态、以及管理员自己的下一次读取作为人证；
两条控制用例（管理员成功启停、管理员成功改正文）保证「一律拒绝」不会通过。

**变异验证**（去掉 `_require_skill_write_scope` 的守卫后重跑）：
4 条安全用例全部失败、3 条控制用例仍通过；前端把 `canToggle` 固定为 `true` 时，2 条用例失败。
即这些用例确实在检测该缺陷，而不是恒真。

## 3. 未交付部分（保持 5.4/5.5 未勾选）

| 缺口 | 现状（file:line） | 前置条件 |
| --- | --- | --- |
| 成员模型目录 | `admin.models` 页 scope 为 `platform`（`auth/service.py:95`），成员目录分支要求 `scope == "tenant"`（`:3681-3694`）→ 死代码；`/api/models` 走平台门（`route_registry.py:193`）。成员实测 `available:false`。已存在可用替代：`GET /api/tenant/authorization/catalog?kind=model` | 把页面 scope 交给资源授权判定，或让页面改读授权目录端点 |
| 个人参数并入共用详情组件 | 工具卡片只读、无详情（`console.js:11761-11799`）；技能有正文查看/编辑与开关，但都不挂个人参数；个人参数只在 `/api/personal/resources` 与已退役的个人视图里（`personal-console.js:75-86`） | 详情组件读取调用者的 `resource_actions` 并写 `/api/personal/resources` |
| 退役旧个人资源页 | 导航转接已做，但个人视图仍注册并加载（`personal-console.js:821-829`、`chat.html:2780`），页 id 仍被后端签发（`auth/service.py:112`），`/api/personal/resources` 仍是活的个人业务端点 | 详情组件落地后删除这些视图与容器 |
| 同名歧义返回 400 | `SkillManager.resolve_skill` 的歧义分支不可达：loader 以 name 为键、custom 覆盖 builtin（`agent/skills/loader.py:239-254`） | 让同名条目可按 `{source}:{name}` 区分并保留多来源条目 |

## 4. 顺带核实：一个潜伏的跨根回退（未在本轮改动）

`common/state_dir.py:_shared_or_own`（`:345-360`）在 `<base>/<part>` 不存在时回退到
`shared_root(identity)`。调用方普遍只传 `base`、不传 `identity`，因此该回退指向的是
**安装根**而不是本租户。直接实测：

```
tenant root: /tmp/.../tmpwlpaen_m | has skills/: False
resolved skills dir: /Users/jiantan/cow/agents/my-assistant-admin/skills   ← 安装根
after creating skills/: /tmp/.../tmpwlpaen_m/skills                        ← 本租户
```

**未在本轮改动**，因为没能证明它经控制台可达：实测控制台 GET 路径解析到的
`skills_dir` 调用落在租户根内，且 `SkillManager.__init__`（`agent/skills/manager.py:130`）
会 `makedirs` 它拿到的目录、`agent/prompt/workspace.py:70-77` 在工作区引导时以
`ensure=True` 建好这些目录，因此常规租户不会触发回退。为此我一度加了 `ensure=True`，
但用一个**恒真的用例**为它背书是不诚实的，已回退该改动，仅在此登记。

建议后续一次性梳理（同一模式至少 8 处，只改一处反而不一致）：
`cli/utils.py:39,47`、`agent/skills/manager.py:49`、`agent/memory/config.py:74`、
`agent/memory/manager.py:383`、`agent/knowledge/service.py:47`、`agent/prompt/builder.py:547`、
`agent/tools/tool_manager.py:334`、`channel/web/web_channel.py:8783`——统一要求「这些目录恒在
租户根内」，或在 `_shared_or_own` 处收紧回退语义。
