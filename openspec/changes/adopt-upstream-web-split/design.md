## Context

动机见 `proposal.md`。本节只记录塑造方案所需的现状与约束。

**固定版本与实测规模**（源 `origin/master@8f1b19f1`，目标 `rdai@b5c5090f`，共同祖先 `e5e2a52d`）

| 对象 | fork（rdai） | 上游（master） |
| --- | --- | --- |
| `channel/web/web_channel.py` | 649 KB / 13,765 行 / 79 个 handler | 7.9 KB（URL 表 + `build_app()`） |
| 同文件在共同祖先 | 389 KB / 8,409 行 / 64 个 handler | — |
| `channel/web/static/js/console.js` | 932 KB / 20,184 行 | 已删除 |
| `channel/web/static/css/console.css` | 197 KB / 6,048 行 | 已删除 |
| 上游新布局 | — | `channel/web/api/*.py`（17 个）+ `channel/web/core/*.py`（4 个），前端 `static/js/{core,views,chat}/*` + `static/css/*` |

**冲突实测**：45 处（基线 21 处全部复现，新增漂移 24 处，0 处消失）。新增漂移中 11 处是 web 测试文件。

**现有可复用接缝**（本方案不重建，只补齐）

- `channel/web/route_registry.py`（447 行）已是路由存在性与授权策略的单一权威清单，条目带 `source`（`upstream` / `fork:<area>`）、`methods` 与逐方法 policy/permission；`derive_web_urls()` / `derive_route_policy()` 由它派生，`check_route_coverage(namespace)` 以 handler 内省为第三腿。
- `channel/web/route_registry.py::_load_fork_extensions()` 已提供 `channel/web/fork_routes.py` 导入钩子以调用 `register_fork_routes()`；该模块当前不存在。
- `auth/http_policy.py`、`auth/capability_matrix.py`、`auth/object_scope.py` 已承载策略与对象级授权。
- fork 已存在独立 handler 模块：`auth_handlers.py`(11)、`admin_handlers.py`(30)、`todo_handlers.py`(5)、`external_connection_handlers.py`(11)、`admin_overview.py`(1)；另有服务型模块 `memory_console.py`、`project_import.py`、`scan_onboarding.py`、`branding.py`、`help_site.py`。
- 前端已有 `static/js/fragments.js` + `data-fork-fragment` 挂载机制（`chat.html` 中登记挂载元素，运行时 fetch 片段并注入，再跑 i18n）。

**尚未迁出的 fork 资产**：15 个 fork 专有 handler 仍定义在 `web_channel.py` 内（branding ×4、memory/personal memory ×5、personal channel ×2、project import ×3、`_MemoryWriteHandler`），以及约 186 个 fork 专有私有 helper（含 29 个 `_require_*` / `_authorize_*`）同样原地留在该文件。

**关键约束**

- 上游 `chat.html` 不含任何 `data-fork-fragment` 挂载元素（实测 0 处）；fork 的挂载元素是 fork 侧新增，属基线已登记的 `seam:` 处置。
- `scripts/conflict-baseline.txt` 当前仅登记「fork 删除 / 上游修改」（`DU`）一个方向；本次出现反方向（上游删除 / fork 修改：`console.js`、`console.css`、`desktop/build/notarize-dmg.sh`）。
- `doc/master合并到rdai分支规范.md` §1.5 禁止对含双方业务逻辑的文件整文件取 `ours`/`theirs`；§1.6 要求授权缺失时关闭能力而非退回避鉴权。

## Goals / Non-Goals

**Goals:**

- 让 fork 的 Web 定制不再原地存在于上游核心文件内，使上游再次重构同一批模块时不再产生整文件级冲突。
- 采用上游 `api/`+`core/` 与 `static/js|css` 模块布局，并在该布局下保持 fork 的身份/租户/RBAC 语义完全不变。
- 恢复 master → rdai 同步的可执行性：本轮 45 处冲突全部有登记处置，新增 24 处漂移逐条点名。
- 建立可执行的结构不变量校验，防止 fork 分支被重新写回上游模块。

**Non-Goals:**

- 不改变任何对外 HTTP 路由、授权策略语义、响应格式或权限判定结果。
- 不重命名或搬迁现有 fork 模块（`auth_handlers.py`、`scan_onboarding.py`、`route_registry.py` 等保持原路径），以避免无谓 churn 与测试改动。
- 不引入新的身份/授权事实源；身份域、TaskStore、记忆工作区、密文凭据的归属不变。
- 不在本 change 内做上游前端功能移植以外的产品定制新增。

## Decisions

### D1 — fork 定制迁出采用「先迁出、后合并」两段式，而非在合并冲突中一次性解决

**决定**：先在 fork 自有分支上完成「把 fork 定制从上游文件迁入 fork 模块」的行为保持型重构并单独提交；随后再执行 `master` 合并。合并提交因此只承担「吸收上游」单一语义。

**理由**：若把迁移与吸收混在一个 merge commit 内，(a) 合并提交无法审阅（含数千行 relocate）；(b) 回滚粒度丢失——无法只回退上游吸收而保留迁移；(c) `doc/master合并到rdai分支规范.md` §7.2 要求 merge commit 第一父为 `rdai`、第二父为 `master`，其 diff 应可解释为「上游增量 + 接缝处置」。分段后：迁移提交是普通 fork 提交（可单独 PR / 回退），merge commit 的冲突面从 13,765 行降至 URL 表级。

**备选与否决**：在合并冲突中直接搬到 fork 模块（否决：审阅与回滚不可控）；保留单体并取 `ours`（否决：违反 §1.5 与新增 capability 的接缝要求，且下次同步重复失血）。

### D2 — fork 自有模块平行承载 handler 实现，上游改进经漂移守护检测后人工移植

**背景修正**（证据 `evidence/03-handler-divergence.md`）：原设计假设「上游 handler 保持不动、fork 授权经接缝附着」。实测 **56 / 64 个上游 handler 的方法体内交织 fork 专有调用**，该假设不成立：

- `_db_scope()` 的 `ctx` 在方法体中部继续被使用（后续校验与资源解析），预派发包装注入不进；
- 资源解析被替换为受租户/owner 限定的变体（`_knowledge_workspace_root` 取代上游 `_get_workspace_root`）。不替换则上游方法体会解析出**未受限路径 → 跨租户数据泄漏**，属安全失败；
- 该解析定义在上游 `core/_common.py`，替换它等于改写上游文件——正是本 change 要消除的行为。

64 个共享 handler 与上游的源码近似度：≥0.85 仅 6 个，0.6–0.85 有 9 个，**<0.6 有 49 个**。即 fork 的 Web 层事实上**平行实现**了上游多数 handler——这是既有状态，非本次合并造成。

**决定**：fork 的 handler 实现在 fork 自有模块中平行承载，镜像上游 `api/` 的视图划分，但位于 fork 命名空间（`channel/web/fork/**`），**不与上游路径重合**。上游模块与入口模块保持零 fork 分支。授权继续由三层承担：第 1 层（路由级策略）与第 2 层（对象级切片授权）沿用既有机制不改；第 3 层由 fork handler 自身实现。

**理由**：这是在不编辑上游核心文件、不削弱既有已验收授权语义的前提下，唯一可达成「按视图分离、按模块解析」的方案。它把既有事实状态显式化、结构化、可检测，**不新增任何损失**。

**代价与制衡**：上游对这批 handler 的业务改进不再自动流入，须人工移植。因此必须提供**漂移守护**（强制项，不得省略）：以固定上游 SHA 记录 fork 已平行实现的 handler 清单及其上游来源模块，上游改动这些 handler 时校验失败并指出需移植的路径与提交。该守护与既有 `tests/test_upstream_drift_guards.py` 同族。

**备选与否决**：

- 「上游 handler + 运行时 monkeypatch 作用域解析」（否决：隐式、难静态校验；需删除体内对象级校验后全部重证授权，风险高于本方案；上游重构即失效）。
- 「fork 子类覆写上游 handler」（否决：56 个 handler 需 `ctx` 参与方法体中部逻辑，覆写等价于复制上游方法体，收益为零）。
- 「向上游贡献可插拔 hook，使上游方法体原样可用」（**长期最优，保留为后续方向**：需上游接受 hook 约定，无法解决本轮同步；本 change 的漂移守护为其提供过渡期保护）。

### D3 — 受影响的授权判定逐项分层归位，不整块搬运

**决定**：约 186 个 helper 按上述三层逐项归位，不是整体搬到单个模块。可表达为策略的判定上移到 `route_registry.py` / `auth/object_scope.py`；确需 handler 上下文的留在 fork 授权模块并由 fork 子类调用。

**理由**：整块搬运只是把大文件换成另一个大文件，没有消除「fork 逻辑与上游实现同处」的耦合。逐项归位后，`_require_*` 的实际存量应显著下降；归位依据在实现时逐文件记录。

**落地归属**：纯粹供 fork handler 复用的请求上下文/作用域辅助（如 `_db_scope()`、`_current_db_identity()`）留在 fork 授权模块；`@property` 式的租户/owner 解析经 `auth` 域既有模块提供。

### D4 — 上游模块集合显式声明，不靠目录约定推断

**决定**：结构不变量校验以**显式声明的上游模块集合**为判据，该集合镜像上游树中的 `channel/web/api/**`、`channel/web/core/**`、`channel/web/web_channel.py`（以及上游 `README.md`）。fork 模块保持在 `channel/web/` 原位与新模块并列，不整体搬迁到 `channel/web/fork/`。

**理由**：搬迁全部现有 fork 模块（`scan_onboarding.py` 106 KB、`route_registry.py` 45 KB 等）会产生大量路径 churn 并波及测试引用，收益仅是目录美观；而不变量校验只需知道「哪些文件是上游的」。显式集合还能在 CI 中与上游树比对，发现上游新增模块未纳入集合（即新的接缝缺口）。

**校验判据**：以「上游模块内出现 fork 专有符号/注册块」为判据，而非「出现 tenant 等关键字」——上游自身也大量使用 tenant 语义命名，关键字判据会误伤。fork 专有符号取自 `route_registry.py` 的 `fork:*` 条目 handler 名与 fork 授权模块的公开符号集合。

### D5 — 前端 fork 定制为独立模块，经运行时装载而非改写上游视图

**决定**：采用上游 `static/js/{core,views,chat}/*` 与 `static/css/*`；fork 定制（外观、身份管理、待办、场景工作台、外部连接、渠道工作台、品牌、i18n 扩展）成为独立模块，由 fork 引导脚本在**上游模块之后**装载，通过 DOM 挂载点、事件以及上游已导出的注册表附着；`console.js` / `console.css` 迁移完成后删除，不留兼容层。

**理由**：兼容层会让单体文件以另一种形式复活，违背本次目标。上游视图模块（`js/views/*.js`）不被 fork 编辑，满足新增 capability 的前端要求。「上游模块之后装载」保证上游的初始化与渲染已就绪，fork 片段的挂载时机可控（复用既有 `fork-fragment-mounted` 事件语义）。

**挂载点与 shell**：`chat.html` 属已登记 `seam:` 处置，保留最小挂载元素是既有契约；挂载元素只声明「在此装载 fork 片段」，不含 fork 业务逻辑。若上游对 `chat.html` 的结构改动使挂载元素位置变化，按 `seam:` 重新登记处置。

**备选与否决**：fork 直接改写上游 `js/views/*.js`（否决：下次上游重构重复失血）；构建期合并出单一 bundle（否决：把冲突从源文件挪到产物，且上游无构建期扩展约定）。

### D6 — 前端与后端的迁移顺序：先后端（解冲突主体），再前端（解 `DU` 主体）

**决定**：先完成后端迁移与上游合并（消除 45 处冲突中的后端主体），再完成前端迁移并删除 `console.js` / `console.css`。

**理由**：后端冲突（含 `web_channel.py`、11 个 web 测试、`route_registry` 相关）是同步的硬阻塞，且 route coverage 校验可在后端迁移后立即给出门禁；前端 `console.js` 的冲突形态是「上游删除 / fork 修改」，不阻塞后端合并——可先按 D1 把 fork 前端逻辑迁出为独立模块，再删除单体。

**备选与否决**：前端先行（否决：后端阻塞未解，前端迁完仍无法合并，且前端片段依赖后端 route/策略不变，返工风险高）。

### D7 — 冲突基线扩展为双向 `DU` 并重新生成

**决定**：`scripts/conflict-baseline.txt` 与 `scripts/sync_report.py` 的 `DELIBERATE_REMOVALS` 语义扩展以覆盖「上游删除 / fork 修改」，并为该方向登记「迁移后删除 → 指向替代模块」。本轮排练后的实际冲突集重新登记，24 处漂移逐条给出 `seam:` / `keep-fork` / `merge-docs` 决策。

**理由**：既有 `DELIBERATE_REMOVALS` 五项（四个 README + `PermissionSelector.tsx`）是「fork 删、上游改」；`console.js`/`console.css` 是相反方向。规范 §5.2 的决策词表与 `fork-upstream-decoupling` 既有 requirement 只覆盖前者，需要显式扩展（对应本 change 的 MODIFIED delta）。`sync_report.py` 的 `DELIBERATE_REMOVALS` 不改（规范 §5.3 明确禁止为文件内删除改动它）。

## Risks / Trade-offs

- [迁移期间行为漂移（授权判定在移动中语义改变）] → 迁移提交必须保持既有接缝测试与权限隔离测试通过（`test_identity_resource_authorization.py`、`test_http_policy.py`、`test_route_registry.py`、`test_upstream_core_seams.py`），并以「迁移前后同一请求的授权结果一致」为验收，而非仅「测试仍绿」。
- [fork 子类覆写上游 handler 后，上游方法改名/重构导致覆写静默失效] → 第三腿不变量校验以 handler 内省比对登记方法；新增校验断言「每个 fork 子类的上游基类存在且被覆写的方法仍存在」，上游重构时立即失败而非静默丢失授权。
- [前端「之后装载」的时序在慢网络或上游异步初始化下不稳] → 复用既有 `fork-fragment-mounted` 事件与显式 ready 信号，不以固定延时挂载；浏览器验收覆盖冷启动与刷新两条路径。
- [`chat.html` 挂载元素被上游结构调整打散] → 属已登记 `seam:`，按基线重新登记；浏览器验收包含「shell 采用上游结构后 fork 片段仍装载」。
- [本 change 规模大，单轮交付周期长] → 按 D6 的阶段性门槛切分，每阶段有独立可执行门槛与可独立回退的提交；未过门槛不进入下一阶段。
- [上游可能在迁移期间再次移动] → 固定 `MERGE_SOURCE_SHA`，迁移期间新到的上游提交不纳入本轮，按规范 §7.2 明确「本轮仍同步固定 SHA」。
- [fork 平行承载 handler 后，上游对这些 handler 的业务修复/安全修复不再自动流入] → 漂移守护以固定上游 SHA 记录 fork 已平行实现的 handler 清单与其上游来源，上游改动时校验失败并指出需移植的路径与提交；移植属常规维护动作，与既有 `fork-upstream-decoupling` 的「上游新增功能与签名在合并中不丢失」义务衔接。
- [大规模搬移（约 5,500 行）引入转写错误] → 迁移以 AST 定位 + 按行区间**逐字复制**源码执行，不手工重写；每完成一个域立即运行该域相关测试与路由覆盖校验；跨边界引用仅 9 处（已实测），逐处显式登记归属。

## Migration Plan

**阶段 0 — 固定与准备**（已完成诊断，执行时重做）：固定源/目标/共同祖先 SHA，隔离克隆，装依赖，记录 `refs.txt`。

**阶段 1 — 后端 fork 定制迁出（行为保持，fork 自有提交）**
把 15 个 fork 专有 handler 与 fork 授权 helper 从 `web_channel.py` 迁入 fork 模块，按 D2/D3 分层归位；`web_channel.py` 保持可构建且对外行为不变。
门槛：`scripts/check-route-coverage.py` 通过；接缝与权限隔离测试通过；无新增未登记路由。

**阶段 2 — 吸收上游（merge commit）**
`git merge --no-ff --no-commit $MERGE_SOURCE_SHA`；`web_channel.py` 收敛为 URL 表 + `build_app()`，采用上游 `api/`+`core/`；逐项按基线处置冲突；`route_registry.py` 改为跨模块解析 handler 命名空间。
门槛：45 处冲突全部解决且有登记依据；基础回归（规范 §6.2 全量）+ 路由覆盖通过；`test_no_resurrection_legacy_identity.py` 与漂移守护通过。

**阶段 3 — 前端模块化迁移**
采用上游 `static/js/{core,views,chat}` 与 `static/css/*`；fork 定制成为独立模块经挂载点装载；删除 `console.js` / `console.css`；`static/js/fragments.js` 的挂载语义保留。
门槛：`node --test tests/test_fork_fragments.cjs`、`tests/test_execution_permission_ui.cjs` 通过；浏览器验收覆盖登录、上下文切换、流式请求与文件传输；`chat.html` 采用上游结构后 fork 片段仍装载。

**阶段 4 — 结构不变量落地与基线重生成**
新增上游模块零 fork 分支的可执行校验（D4）；重新生成 `scripts/conflict-baseline.txt`，登记 24 处漂移与新的双向 `DU`。
门槛：不变量校验可独立运行并在注入违规时失败；基线无未点名冲突（`scripts/check_change_deltas.py`）。

**阶段 5 — 同步收尾与交付**
按 `doc/master合并到rdai分支规范.md` §6.4 逐项完成 database 能力验收（≥2 租户、多用户、正向可用 + 越权拒绝 + 真实入口），§7.2 生成含两个父提交的 merge commit，推送同步分支并向 `rdai` 提 PR（英文正文，含能力对照与证据链接）。

**回滚**：任一门槛未过时，阶段 1–3 的 fork 自有提交可独立 revert 而不影响 `rdai`；阶段 2 的合并未提交前用 `git merge --abort`；已提交未交付时保留同步分支、从确认的 `rdai` SHA 重做；已合入按规范 §8 用 `git revert -m 1`，并注意被 revert 的源提交仍在祖先历史中。本 change 不涉及数据迁移，故无数据库回滚步骤。

## Open Questions

- fork 授权模块与新 fork handler 模块的**具体文件划分与命名**，在阶段 1 首个任务中按实际耦合度确定（不影响不变量判据——判据依赖 D4 的显式符号集合，而非文件划分）。**已定**：见 D2 —— `channel/web/fork/**` 包，`common.py`（共享管道）、`authorization.py`（授权 helper）、`handlers/<view>.py`（按上游 `api/` 视图划分的平行实现）。
- `desktop/build/notarize-dmg.sh`（上游删除 / fork 修改）的最终处置，在阶段 2 按基线复核该 fork 修改是否仍必要；属 `DU` 登记项，不改变本方案结构。
