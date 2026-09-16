# 5.4 / 5.5（第二段）：成员模型目录、个人参数并入共用详情、同名歧义 400

对应 `tasks.md` 5.4 / 5.5 中 `evidence/5-4-public-surface-authority.md` §3 列出的四个缺口。本轮交付
前三项（成员模型目录、个人参数并入共用详情组件、退役独立个人资源面）与第四项（同名歧义 400）。
§5 逐条列出**未验证**与**主动停手**的部分。

## 1. 成员模型目录（缺口一）

### 缺陷（改动前实测）

`admin.models` 声明为 `scope: "platform"`（`auth/service.py:103`），而页面投影里成员目录的分支要求
`scope == "tenant"`，于是该分支**不可达**；成员拿到的是
`{"available": false, "read_allowed": false, "reason": "consumer_closed"}`。同一页面的管理形态又
只能走平台门（`/api/models`、`/config`，`channel/web/route_registry.py:193`），因此成员**没有任何
入口**看到自己被授权的模型。

### 形态

* 页面 scope 改为 `tenant`（`auth/service.py:103`）：`platform` 页面根本不是业务入口
  （`_qualifyAdminConsoleEntry` 会跳过），成员的目录页必须是 `tenant` 面。
* `model_catalog_open()`（`auth/service.py:3393`）：调用者是否**有任一**可 `read` **或** `use` 的
  模型。只看 `read` 会让「仅持 `use` 授权」的成员看到关闭的页面而其背后的目录端点明明有行
  （`kind` 行的 `catalog` 键也用它，`auth/service.py:3431`）。
* 页面投影（`auth/service.py:3737-3750`）：`available` / `read_allowed` 取 `model_catalog_open()`，
  `actions.manage` **只等于平台资格**（`mode == "all" or is_platform_admin`）——这正是 `/api/models`
  与 `/config` 的门，所以控制台只在写入会被接受的地方渲染厂商/密钥编辑器，其余情况渲染被授权目录。
  公共模型服务地址与密钥因此**从未**变成成员可达。
* 可达性：`admin.models` 加入 member / tenant_admin 的默认菜单（`auth/policy.py:518`、`:528`），
  并对存量租户补一次授权（`auth/store.py:1431` `_migration_27`）。什么都不放宽：没有模型授权的
  调用者页面仍报不可用，控制台也不显示入口。
* 前端（`channel/web/static/js/console.js`）：`memberCatalogState`（`:12855`）、
  `loadMemberCatalog`（`:12916`）读
  `GET /api/tenant/authorization/catalog?kind=model&purpose=use`、`renderMemberCatalog`（`:12938`）；
  `_modelsManageAllowed` 只读**服务端**的 `actions.manage`（含请求早于 `/auth/context` 的回落：
  数据库模式下仍取平台资格，与后端同一事实）；面板容器 `channel/web/chat.html:1399`。

### 验收（`tests/test_member_model_catalog.py`，15 项）

| 断言 | 用例 |
| --- | --- |
| `use` 授权也能打开页面 | `test_a_member_with_a_use_grant_opens_the_page` |
| 仅 `read` 授权同样打开（控制用例：不能只认 `use`） | `test_a_member_with_a_read_grant_alone_also_opens_the_page` |
| 无授权 = 关闭（控制用例：不能恒开） | `test_a_member_with_no_model_grant_gets_a_closed_page` |
| `actions.manage` 只随平台资格出现 | `test_only_the_platform_qualification_carries_the_management_action` |
| 页面与 `kind` 行两个答案一致 | `test_the_page_and_the_kind_report_agree` |
| 成员读到的正是被授权的那个模型 | `test_a_member_reads_exactly_the_model_they_are_granted` |
| 成员**不能**触达公共模型服务 | `test_a_member_still_cannot_reach_the_public_model_service` |
| 平台管理员仍然能维护（控制用例） | `test_the_public_model_service_answers_the_platform_admin` |
| 内置角色 / 存量租户 / 自定义角色三条可达性路径 | `ReachabilityTests` 6 项 |

## 2. 个人参数并入共用详情组件（缺口二）

### 形态

个人使用参数（工具的 `params`/`secret` 与技能的同一形状）现在写在**正式页面的同一对象**上：

* 后端：`POST /api/tools`、`POST /api/skills` 承接 `{action:"save-personal"|"clear-personal"}`
  （`channel/web/route_registry.py:197-198`；`channel/web/web_channel.py:8741` `ToolsHandler`、
  `:8952` `SkillsHandler`）。owner 固定取会话（`actor_user_id=ctx.user_id`），请求体里的
  `user_id`/`owner` 无法影响归属；写入仍走原来的 owner-scoped 存储
  （`save_personal_resource_config` / `clear_personal_resource_config`，**保留未动**）。
* 读取：目录行携带调用者自己的状态——`_attach_personal_states`
  （`channel/web/web_channel.py:8926`）→ `IdentityService.personal_resource_states`
  （`auth/service.py:5956`）：按资源 id 给出 `configured / params / has_credential / version` 与
  `actions.configure`、`actions.clear`。`configure` 被 `member_personal_console` 开关收窄（保存会被
  拒的行不再广告编辑器），`clear` 故意不受开关影响（已存的 secret 必须始终能撤）。
* 前端：共用详情组件（`channel/web/static/js/console.js:12017-12235`，容器
  `channel/web/chat.html:2361`），工具卡片与技能卡片都打开它。**组件不做任何授权判断**：行里没有
  `personal` 就不渲染参数区；`actions.configure` 为假但已有参数时按只读展示并只留 `clear`；
  技能开关只在 `actions.enable` 允许时画成控件，否则陈述状态。敏感值不回显：只下发
  `has_credential`，表单里是占位文案（`resource_detail_personal_secret_saved` / `_secret`），保存时
  **空 secret 字段表示「不改动已存凭据」**（否则一次静默清空与一次轮换无法区分）。
* i18n：`channel/web/static/js/i18n/resource-detail.js` 14 键 + `models-config.js` 3 键，三语齐备，
  快照已同步（见 §4）。

### 验收

`tests/test_personal_console_web.py`（`PersonalResourceVerbTests` 7 项 + `RetiredResourceSurfaceTests` 3 项）、
`tests/test_personal_console_transport.py`（真实 `build_web_app()` 上 16 项，其中个人参数 10 项：往返、他人不可见、
未授权资源被拒、clear 幂等、公共定义字段不被个人保存改动、跨租户被拒）、
`tests/test_resource_detail_frontend.cjs`（9 项，本轮新增，见 §4）。

## 3. 退役独立个人资源面（缺口三）

| 退役项 | 位置 | 结果 |
| --- | --- | --- |
| 个人视图注册 | `channel/web/static/js/personal-console.js:70-90`（`PERSONAL_VIEWS` 由 5 条减为 3 条） | 我的工具 / 我的技能不再注册（浏览器实测运行时注册表 = `[personal-agents, personal-channels, personal-memory]`，§5.1）；`personal-tools` / `personal-skills` 地址转接到 `skills` 且不再拉起旧消费者（§5.1 实测：`/api/personal/resources` 请求 0） |
| 独立写入路径 | `personalActionRequest` / `personalFormFields` 里 `kind === 'resource'` 两个分支 | 已删（`/api/personal/resources` 的调用者归零） |
| 独立端点 | `channel/web/route_registry.py`（原 `:204`）、`PersonalResourceHandler`（原 `channel/web/web_channel.py:9338`） | 路由与处理器均已删除，`web_channel.py` 中 `PersonalResourceHandler` 出现次数为 0；`rg personal/resources` 在 web 层与 JS 层命中 0 |
| 路由基线 | `scripts/route-baseline.txt:349-350` | `GET`/`POST /api/personal/resources` 记为 `REMOVED`；`POST /api/tools`、`POST /api/skills` 记为 `tenant`，并写明「路由级权限会是第二个更弱的权威，故 per-object 判定留在 handler」 |

**保留**（有意）：owner-scoped 存储与服务、`personal.agents/channels/memory` 三个个人页、
`member_personal_console` 开关。**偏差（见 §5）**：`personal.tools` / `personal.skills` 两个 id 仍作为
**legacy projection** 被签发。

## 4. 同名歧义返回 400（缺口四）

### 缺陷

`SkillManager.resolve_skill` 的歧义分支（`len(matches) > 1`）**不可达**：loader 以 skill name 为键、
custom 覆盖 builtin（`agent/skills/loader.py:249-267`），被覆盖的定义直接消失，裸 `name` 看起来与唯一
资源完全一样——于是「按覆盖顺序静默选一个」成为唯一行为，而授权是按 `{source}:{name}` 记录的，
一份定义的授权会被花在同名的另一份上。

### 形态

* loader 保留被覆盖的定义：`SkillEntry.shadowed`（`agent/skills/types.py:68`）在覆盖处记入
  （`agent/skills/loader.py:252-267`，赋值为 `:264`），因此「同名」从此**可区分**。
* `SkillManager`（`agent/skills/manager.py`）：`SkillNameAmbiguous(ValueError)`（`:17`）、
  `require_unique_name`（`:552`）、`ambiguous_sources`（`:563`）、`ships_with_install`（`:581`）。
  裸 `name` 请求命中歧义即抛错；`resource_id` 请求不受影响，且**被覆盖的那份定义也仍然可寻址**
  （`get_skill_by_resource_id:519-527` 的 shadowed 回退），这样 400 给出的补救（改传 `resource_id`）
  才真的落到调用者指名的那个来源。
* API 侧：`_raise_if_skill_name_ambiguous`（`channel/web/web_channel.py:492`）把该异常答成
  **400 + `code: "skill_name_ambiguous"`**；接入 `SkillsHandler.POST`（`:9036`）、
  `SkillContentHandler.GET`（`:9080`）与 `POST`（`:9124`）——在此之前歧义会落进
  `except (ValueError, FileNotFoundError)` 的 200 错误体。其余 `ValueError` 保持原状（消息与日志不变）。
* 判据（产品裁定）：**「安装自带的拷贝」不算第二个定义**。`ships_with_install` 是既有的
  「这份文件下次启动还会被安装覆盖」判定（`agent/skills/service.py:175`，现在委托到
  `SkillManager.ships_with_install`，读/写拒绝与歧义判定因此不可能各答一套）：启动时
  `_sync_builtin_skills` 会把每个内置技能目录复制进工作区并按目录名覆盖，所以内置技能的胜出者
  通常是**同一技能的副本**——若把这种同名一律判歧义，产品里每个内置技能的裸 `name` 都会 400。
  只有「安装并不分发、租户自己的那份定义」才歧义。这与既有产品决定一致（`tests/test_doc_edit.py`
  的「工作区副本仍然是只读的安装内容」被同一条判据覆盖，见 §4 变异验证）。

### 验收（`tests/test_skill_name_ambiguity.py`，9 项）

| 断言 | 用例 |
| --- | --- |
| 被覆盖定义可区分（不再丢失） | `test_a_shadowed_definition_is_recorded_rather_than_lost` |
| 裸 name 被拒而不是按覆盖顺序解析 | `test_the_ambiguous_name_is_refused_rather_than_resolved_by_override_order` |
| 两个来源各自可寻址（两个方向都断言内容） | `test_each_definition_is_still_addressable_by_resource_id` |
| 400 之后存储状态未变（`400不执行`） | `test_a_bare_name_that_means_two_definitions_is_refused` |
| 改传 `resource_id` 后同一请求落地（控制用例） | `test_the_same_toggle_lands_when_the_caller_names_the_source` |
| 正文读：裸 name 400，两个 id 各取各自文本 | `test_a_content_read_by_name_is_refused_and_by_id_reaches_one_source` |
| 唯一 name 不受影响（控制用例） | `test_a_unique_name_still_toggles_and_reads_by_name` |
| 安装自带拷贝不歧义（控制用例，防「一律拒绝」） | `test_the_installations_own_copy_of_a_builtin_is_not_ambiguous` |

## 5. 验收命令与结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 定向集（22 文件，含上述全部新增 + 既有回归） | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_skill_name_ambiguity.py tests/test_doc_edit.py tests/test_skill_public_surface_scope.py tests/test_skill_tar_security.py tests/test_scene_skills.py tests/test_scene_activation.py tests/test_personal_console_web.py tests/test_personal_console_transport.py tests/test_personal_console_acceptance.py tests/test_personal_console_pages.py tests/test_plan_3_1_joint_acceptance.py tests/test_route_registry.py tests/test_http_policy.py tests/test_compat_surface_closure.py tests/test_tenant_admin_skills_menu.py tests/test_member_model_catalog.py tests/test_identity_resource_authorization.py tests/test_identity_web_handlers.py tests/test_console_menu_mapping.py tests/test_personal_capability_switches.py tests/test_personal_delivery_drill.py tests/test_memory_console_scope.py -q -p no:randomly` | **469 passed** |
| 技能/个人面关联带（21 文件） | 同上去掉 `test_personal_console_pages.py`/`test_console_menu_mapping.py`/`test_personal_capability_switches.py`/`test_personal_delivery_drill.py`/`test_memory_console_scope.py` | **435 passed** |
| 新增后端用例 | `pytest tests/test_skill_name_ambiguity.py -q -p no:randomly` | **9 passed** |
| 新增模型目录用例 | `pytest tests/test_member_model_catalog.py -q -p no:randomly` | **15 passed** |
| 新增前端用例 | `node tests/test_resource_detail_frontend.cjs` | **9 passed** |
| 前端（既有，本轮改动过） | `node tests/test_personal_console_frontend.cjs` / `test_member_model_catalog_frontend.cjs` / `test_skill_public_surface_frontend.cjs` | 各 fail=0 |
| 前端全量 | `for f in tests/*.cjs; do node $f; done` | 仅既存失败集：`_tmp_repro_modeldefaults.cjs` 1、`test_session_history_frontend.cjs` 36、`test_sidebar_account_frontend.cjs` 5；**新增 0** |
| i18n | `node tests/test_console_i18n_parity.cjs` | 5 passed（三语字典与快照零差异；本轮 17 个键三语齐备） |
| **浏览器（真 Chromium，遗留地址转发）** | `NODE_PATH="$(npm root -g)" node /tmp/cow_forward_check.cjs`（`tests/test_personal_console_browser.cjs` 的临时副本，仅取本页那条断言；见 §5.1） | **PASS**：两条遗留地址各 1 次——`/api/personal/resources` 请求数 **0**、`#view-personal-tools\|skills` 容器数 **0**、`view-skills` 成为活动视图且 `/api/skills` 消费者启动 1 次；运行日志中 `personal/resources` 出现 **0** 次；运行时注册表 `PersonalConsole.PERSONAL_VIEWS` = `[personal-agents, personal-channels, personal-memory]` |
| **既有工作复验（并发改动之后，先清 `__pycache__`）** | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_member_model_catalog.py tests/test_skill_name_ambiguity.py tests/test_personal_console_web.py -q -p no:randomly` + `node` 四个前端文件 | **45 passed**；`test_resource_detail_frontend.cjs` 9、`test_member_model_catalog_frontend.cjs` 7、`test_personal_console_frontend.cjs` 57、`test_skill_public_surface_frontend.cjs` 4，fail 全 0 |

### 5.1 浏览器实测：遗留地址转发（原先未跑的一条）

`playwright` 现已可用（`NODE_PATH="$(npm root -g)"` 必需，缺它 `require('playwright')` 抛
`MODULE_NOT_FOUND`，而 `tests/test_personal_console_browser.py` 会**静默 skip 并记为 pass**）。
断言在 `tests/test_personal_console_browser.cjs:307-320`（`desktop: the retired resource addresses
forward instead of painting`），该文件正在被另一个 agent 重写，本轮**未改动它**。用它的临时副本
（`/tmp/cow_forward_check.cjs`，把 scenario 改成不中断 + 只跑本条 + 补网络/历史探针）实测：

| 打开地址 | 最终 URL | 活动视图 | `view-personal-*` 容器 | `/api/personal/resources` |
| --- | --- | --- | --- | --- |
| `/admin#view-personal-tools` | `/admin#view-skills` | `view-skills` | 0 | 0 |
| `/admin#view-personal-skills` | `/admin#view-skills` | `view-skills` | 0 | 0 |
| `/chat#view-personal-tools` | `/admin`（hash 空） | `view-skills` | 0 | 0 |
| `/chat#view-personal-skills` | `/admin`（hash 空） | `view-skills` | 0 | 0 |
| `/chat#view-chat`（对照，非遗留地址） | `/chat#view-chat` | `view-chat` | 0 | 0 |

`history` 调用轨迹证明转发是**同文档改写、非重载**：`replaceState #view-skills` 在 `~140ms`
一次到位；从工作台打开时紧接着还有一次 `pushState /admin`——那是**跨区切换**（`navigateTo:1869-1878`
把目标暂存为 `cow_admin_pending_view` 再 `_openNavArea('admin')`，`_openNavArea:18331` push 的是
不带 hash 的区路径），所以最终地址是「实际显示的区」而不是遗留 id。`/admin` 起点的对照
（第 1、2 行）说明 hash 期望本身没错：**只要在控制台区内打开遗留地址，`location.hash` 就是
`#view-skills`**。两条遗留地址都**没有** `.view` 重绘、没有文档重载、没有遗留端点的任何请求
（GET/POST 都按 pathname 计数）。

### 变异验证（逐个守卫回退后重跑）

后端（`tests/test_skill_name_ambiguity.py`，文件回退后确认字节一致）：

| 回退的守卫 | 失败用例 |
| --- | --- |
| loader 不记录 `shadowed` | 6 条（记录、400、两个来源可寻址、安装拷贝控制、wire 400、正文 400） |
| `resolve_skill` 不做唯一性检查 | 3 条（400 两条 + 正文 400） |
| 安装自带拷贝也算歧义 | `…is_not_ambiguous` + **既有** `tests/test_doc_edit.py::test_a_workspace_copy_of_a_builtin_skill_is_still_read_only` |
| shadowed 定义不可用 `resource_id` 寻址 | 2 条（两个来源可寻址、正文按 id 取各自文本） |
| `SkillsHandler.POST` 不映射 400（退回 200 错误体） | `test_a_bare_name_that_means_two_definitions_is_refused` |
| `SkillContentHandler.GET` 不映射 400 | `test_a_content_read_by_name_is_refused_and_by_id_reaches_one_source` |

前端（`tests/test_resource_detail_frontend.cjs`，回退 `console.js`）：

| 回退的守卫 | 失败用例 |
| --- | --- |
| 忽略 `actions.configure`（总是画编辑器） | `saved parameters without the configure verb are shown read-only` |
| 参数区**从不**渲染（「一律拒绝」反证） | 3 条正向用例（编辑器、只读、secret 占位） |
| 空 secret 字段照样下发（覆盖已存凭据） | `a save posts the caller-scoped verb to the row's own endpoint` |
| 参数不是 JSON 也照发 | `unparsable parameters never reach the wire` |
| 技能开关无视 `actions.enable` | `the skill switch appears only where the server allowed it` |

即：每条用例都在检测对应缺口，控制用例保证「一律拒绝」「一律放行」都不会通过。

## 6. 未验证 / 主动停手

1. **两个 legacy page id 未删**（对 §3 的字面要求的偏差）。`personal.tools` / `personal.skills` 仍是
   被签发的 page id（`auth/service.py:120-121`、`_PERSONAL_PAGE_META:183-188`、
   `auth/policy.py:478-479`），理由是它们同时被**兼容映射**（`LEGACY_PERSONAL_MENU_MAP:554-555` 把
   `nav:personal.tools|skills` 映射到 `nav:admin.skills`）、**能力开关表**（`:595-596`）与**多个既有
   用例**（`tests/test_console_menu_mapping.py:145` 断言 5 个 id 与 `PERSONAL_IDS` 相等、
   `tests/test_personal_capability_switches.py`、`tests/test_personal_delivery_drill.py:267`、
   `tests/test_personal_console_acceptance.py:489` 把该页的不投影当作「读权限被撤」的人证）使用。
   可观测的**面**已退役：没有视图消费它们、没有独立端点、没有第二条写入路径，旧地址转接到正式页。
   若要连投影一起删，代价是上述 4 个测试文件 + 兼容映射表 + 开关表，且 `test_personal_console_acceptance.py`
   需要换一个人证——这属于「删掉兼容层」，我停手等确认。
2. **浏览器用例本身仍未跑通，且它的归属文件正在重写**（结论已由 §5.1 的临时副本给出）。现状：
   * 该断言在 `tests/test_personal_console_browser.cjs:307-320`，属**另一个 agent 正在重写**的文件，
     我按约定没有改它；文件当前在更早的 `:296`（`desktop: every surviving personal entry renders
     its own page`，仍断言 `#view-personal-agents` 会被画出来——但在当前工作树里 `personal-agents`
     也已转发到 `agents`）就抛错，所以**我这条断言在文件里今天根本不会被执行**。
   * 即使可执行，它的 `location.hash` 期望在本文件用的 `/chat#view-personal-*` 起点下也**不成立**：
     跨区切换会把地址写成 `/admin`（见 §5.1 表格第 3、4 行）。它要变绿需要满足其一：
     (a) 起点改成控制台区 `#view-personal-*`（`/admin#view-personal-tools` 实测 `hash === '#view-skills'`，精确满足现有断言）；
     或 (b) 保留 `/chat#view-personal-*` 起点，把断言改成「目标页被真正打开」的可观测：`location.pathname === '/admin'`、
     `#view-skills` 处于活动、`#view-personal-*` 计数为 0、`/api/personal/resources` 请求数为 0。
     另外该 fixture 的 `/api/*` 兜底会把遗留端点也答成 200（`tests/test_personal_console_browser.cjs:159-161`），
     想让它作为「已消失」的人证更硬，应对该 path 直接 404（现有断言只数请求，仍有效）。
   * 另注：`tests/test_personal_console_browser.py` 在本环境下缺 `NODE_PATH` 会**静默 skip 并报 pass**
     （`SKIP personal console browser contract: playwright is not installed`），所以「wrapper 通过」
     不等于浏览器断言真的跑过。
3. **未做真实运行往返**：个人参数的真机读写（真实提供方/凭据/执行）不在本轮范围；本轮的「往返」
   都是在真实 `build_web_app()` + 真实身份库上、对真实文件的读写，不涉及外部提供方。
4. **未跑全量 Python 套件**：工作树里有其他并发改动（`docs/**`、多个 evidence、其他能力的代码），
   全量结果无法归因给本 change，故只跑定向集与关联带（469 / 435 全绿）。
5. **目录行未暴露「同名」标记**：目录对每个 name 只出一行（`skills_config` 以 name 为键），因此
   前端只会拿到一个 `resource_id` 并据此请求；没有向页面额外广告「这个名字还有另一份定义」。
   规范只要求歧义 name 返回 400，未要求广告，故未加。
