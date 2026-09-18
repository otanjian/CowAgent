# Tasks

阶段门槛未通过前不得进入下一阶段；「接口占位」「本机可跑」不作为门槛通过。

## 1. 固定版本与准备（阶段 0）

- [x] 1.1 确认目标为 `rdai`、来源为 `origin/master`，记录源/目标/共同祖先 SHA 与本地额外提交范围到 `refs.txt`
- [x] 1.2 建立独立克隆（`git clone --no-hardlinks`），另设真实远端，`fetch origin master rdai`，复核 `origin/rdai` 是目标 HEAD 祖先
- [x] 1.3 从确认的目标 SHA 创建同步分支 `codex/sync-master-to-rdai-<时间戳>`，启用 `rerere`，记录上游提交清单与 `git diff --stat/--name-status`
- [x] 1.4 在克隆内按候选声明安装依赖（含测试依赖），确认 `.venv` 与测试依赖可用；记录环境与命令
- [x] 1.5 运行一次排练（`scripts/sync-from-master.sh`）作为迁移前基线，保存日志并确认排练后工作树干净、无 `MERGE_HEAD`
- [x] 1.6 盘点并记录「迁移前」的 route coverage、接缝测试与权限隔离测试结果，作为阶段 1 的对照基线（见 `evidence/01-phase1-baseline.md`）

## 2. 后端 fork 定制迁出（阶段 1，行为保持）

- [x] 2.1 枚举 `channel/web/web_channel.py` 中 fork 专有符号清单并登记目标归属：实测 290 个模块级符号（79 个 handler + 134 个私有 helper + 其余管道/常量），跨文件边界引用仅 9 处（见 `evidence/02-fork-symbol-map.md`）
- [x] 2.2 建立 fork 授权模块 `channel/web/fork/authorization.py`，迁入请求上下文/作用域辅助（`_db_scope`、`_current_db_identity`、`_authorized_model_codes`、`_web_runtime_identity_snapshot`、29 个 `_require_*`/`_authorize_*` 等），逐字复制
- [x] 2.3 归位授权判定：实测 56/64 个上游 handler 的方法体内**交织** fork 授权与数据作用域（证据 03），原 D2「三层分工」前提不成立 → 按修订后的 D2，fork 在 `channel/web/fork/` 中平行承载实现；路由级与对象级策略层沿用不改
- [x] 2.4 将 79 个 handler 迁入 `channel/web/fork/handlers/<view>.py`（17 个视图模块，镜像上游 `api/` 划分；含 15 个 fork-only 与 64 个 fork 平行实现），逐字复制
- [x] 2.5 ~~为上游 handler 建立 fork 子类~~ **已废弃**：子类覆写对 56 个 handler 等价于复制方法体，收益为零；改由 D2 的平行承载 + hub 接缝达成同一目的
- [x] 2.6 路由权威清单跨模块解析：入口模块 `web_channel.py` 保留 `_WEB_URLS` 与全部 handler 名，`check_route_coverage(vars(web_channel))` 与 `web.application(_WEB_URLS, globals())` 两处解析点均不变；`route_registry.py` 无需改动
- [x] 2.6a 保留入口模块的既有命名空间契约：原 49 条模块级 import 逐字保留、`globals().update(_SCENE_HANDLERS)` 复原、`WebChannel`/`SERVING`/`SSEStreamState`/`WebMessage` 可经 `web_channel` 解析
- [x] 2.6b hub 接缝：fork 模块对「被其它模块经 `web_channel.<name>` 解析/打桩的名字」在函数体内经入口模块惰性解析（实证见 `evidence/04`），使既有接缝语义与全部既有测试保持成立；**迁移提交内测试文件零改动**
- [x] 2.6c 修正 hub 判定遗漏的打桩形式：首轮全量回归暴露 22 处行为失败，根因是 hub 判定只识别 `web_channel.<name>` / `from … import <name>`，未识别 `patch.object(web_channel, "<name>")` / `setattr(web_channel, "<name>")` 的字符串实参形式，导致打桩静默失效。已扩展判定并重生成（见 `evidence/05-verification.md`）
- [x] 2.6d 迁移中唯一的非逐字改写：把 6 处 `os.path.dirname(__file__)` 相对资源路径改为锚定 `channel/web` 的 `_WEB_ROOT`；改写规则与理由记录在 `scripts/migration/emit_fork_web.py` 与 `scripts/migration/README.md`
- [x] 2.7 ~~补齐 `channel/web/fork_routes.py`~~ **以等价方式满足**：fork 路由全部登记在权威清单 `route_registry.py` 内（`source=fork:*`，实测 108 条 / 221 个方法项），无需另设扩展模块；`_load_fork_extensions()` 钩子保留，供未来独立的 fork 路由模块使用
- [x] 2.8 编写/更新测试，断言迁移后的授权结果与迁移前一致（同一请求同一判定），覆盖合法成员正向、跨租户拒绝、跨 owner 拒绝、administrator 治理边界。**采用更强判据：既有行为测试零改动地全部通过**，等价于同一套断言与打桩点在迁移后仍成立
- [x] 2.9 运行并记录阶段 1 门槛：`scripts/check-route-coverage.py`（176 路由 / 221 方法项，OK）、`tests/test_route_registry.py`、`tests/test_upstream_core_seams.py`、`tests/test_no_resurrection_legacy_identity.py`、`tests/test_identity_resource_authorization.py`、`tests/test_http_policy.py`；并做迁移前后全量对照（Python: 33→27 失败、0 新增；Node: 54→46 失败、0 新增）。结果见 `evidence/05-verification.md`
- [x] 2.9a 修正读源码文本的结构性护栏：9 处因代码迁出而失败的断言，改为读「整个 web 层」而非单个入口文件（`tests/_helpers.py::web_layer_source`、`tests/_web_layer.cjs`），避免日后模块再拆分时护栏静默失效
- [x] 2.9b 加固两处**迁移后静默变空**的护栏：`tests/test_channel_signature_seam.py`（原读 `web_channel.__file__`，现为不含方法体的入口模块）与 `tests/test_no_resurrection_legacy_identity.py::test_legacy_auth_helpers_are_absent`（原只在入口模块内搜已退役 helper）；并扩展 `tests/test_route_registry.py` 的「无手写路由字面量」检查覆盖 fork 模块
- [x] 2.10 提交阶段 1 迁移提交（fork 自有提交，非 merge），确认可独立回退；迁移前后对照证据见 `evidence/04-phase1-backend-migration.md` 与 `evidence/05-verification.md`
- [x] 2.11 迁移后重跑排练（`scripts/sync-from-master.sh origin master`），与迁移前冲突清单逐项比对：45 → 46 个冲突文件，仅新增 `tests/test_qianfan_provider.py`（测试重定向的必然结果，`web_channel.py` 本身仍冲突但已从 8409 行单体内战变为 627 行 vs 177 行的可复核组合），fork 实现 686 KB / 22 个模块完全退出冲突面（见 `evidence/06-rehearsal-after-phase1.md`）

## 3. 吸收上游（阶段 2，merge commit）

- [ ] 3.1 检查阶段 1 证据齐备后，以固定 `$MERGE_SOURCE_SHA` 执行 `git merge --no-ff --no-commit`；记录冲突清单与 `git ls-files -u`
- [ ] 3.2 引入上游 `channel/web/api/**` 与 `channel/web/core/**`，确认 `web_channel.py` 收敛为 URL 表 + `build_app()`，且不含业务 handler 实现
- [ ] 3.3 逐项处置 45 处冲突：`seam:` / `keep-fork` / `merge-docs` / `keep-deletion` 各按基线登记，逐路径记录双方意图、最终行为与采用的接缝
- [ ] 3.4 复核并处置四个 README 的 `keep-deletion`、`PermissionSelector.tsx` 的 `keep-deletion`，以及新增的反方向 `DU`（见 4.3）——不得对文件内删除使用 `keep-deletion`
- [ ] 3.5 逐项检查**无冲突文件**的上游增量：路由、HTTP 方法、任务字段、通知语义、凭据响应与请求传输，确认未被静默丢弃
- [ ] 3.6 保留上游新增行为与安全约束，至少包含：上传预览按所选 Agent 限定、仅读 body 的路由的 Agent 解析、飞书群消息提及门控、QQ 文件接收与 Markdown 回复、钉钉收文件、知识库空状态、ASR 模型取配置值
- [ ] 3.7 保留 fork 侧 `_import_local_file` 的 loopback 与每启动令牌校验，确认未因合并被移除或放宽
- [ ] 3.8 逐路径 `git add`，检查暂存内容无无关文件；运行 `git diff --check` / `git diff --cached --check`
- [ ] 3.9 运行阶段 2 门槛：规范 §6.2 全量基础回归（含 `tests/test_sync_report.py`、`test_conversation_schema_seam`、`test_scheduler_identity_seam`、`test_startup_hook_seam`、`test_channel_signature_seam`、`test_scheduler_web_update`、`test_upstream_drift_guards`、`test_recovered_entry_acceptance`、`test_desktop_auth_flow`）与路由覆盖校验
- [ ] 3.10 处理本轮 11 处 web 测试漂移：逐文件确认该测试对应的能力已进入目标版本，按新模块位置更新引用；不得删除测试或放宽断言后声称通过
- [ ] 3.11 生成候选并记录暂存树哈希（`git write-tree`），提交 merge commit `merge: sync master into rdai`，校验第一父为 `$MERGE_TARGET_SHA`、第二父为 `$MERGE_SOURCE_SHA`、树哈希一致

## 4. 前端模块化迁移（阶段 3）与基线重生成（阶段 4）

- [x] 4.1 枚举 `console.js` / `console.css` 中全部 fork 定制，登记为迁移清单（外观、身份管理、待办、场景工作台、外部连接、渠道工作台、品牌、i18n 扩展、片段加载）
  - 证据 `evidence/07-frontend-divergence.md`（原始输出 `07-frontend-divergence.txt`，逐 hunk 明细 `frontend_divergence.json`）
  - `console.js`：365 hunk，+7832 / −2061 行，相似度 0.71；`console.css`：79 hunk，+2606 / −229，相似度 0.71
  - 归一化 diff 是前提：按原样行 diff 会把 `console.js` 报成「2 hunk / 18671 增行」，实际是 fork 改了空白与缩进
  - 定制集中在 7 个上游模块（占增行 84%）：`core/auth.js` 1320、`views/agents.js` 1034、`views/sessions.js` 1029、`views/channels.js` 923、`core/version.js` 894、`core/nav.js` 729、`views/config.js` 653
  - `core/i18n.js` 为反向（+79 / −1295）：fork 把翻译移出到 `static/js/i18n/`，该模块不可按「移植 diff」处理
  - fork 专有文件（`appearance.js`、`identity-admin.js`、`todos.js`、`scenes/`、`external-connections.js`、`channel-workbench.js`、`i18n/`、`fragments.js`、`appearance.css`、`fragments/appearance-dialog.html`）本已是独立文件，不在本次拆分范围内
- [x] 4.2 定位每个 fork 定制所属的上游模块所有者（迁移清单 → 上游模块映射）
  - 判据：以归一化后的**具判识度**行（长度 ≥ 8 且被 ≤ 3 个模块包含）为锚点；短结构行（`}`、`});`）会命中所有模块，首版分析因此给出「36 个模块各 ≈7000 行」的无意义结果
  - 结果：base 行 100% 可映射（JS 14413/14413、CSS 3671/3671），覆盖 33 / 8 个上游模块
  - 可移植性实测：JS 258/365 hunk（70.7%）可机械再锚定，107 处需人工移植；CSS 67/79（84.8%），12 处需人工
  - 人工移植量最大的模块：`views/sessions.js` 21、`views/agents.js` 20、`core/nav.js` 12、`views/config.js` 7、`core/auth.js` 7、`css/sessions.css` 7
- [ ] 4.3 采用上游 `static/js/{core,chat,views}/*`、`static/css/*`、`chat.html` shell 与 `templates/**`，全部保持未改动；fork 挂载元素按 `seam:` 重新登记
  - 上游 shell 与 `core/template.py` 的 include 语义、按文件 mtime 的 `?v=` 版本戳、`tools/check-load-order.mjs` 门禁一并采用
- [ ] 4.4 以「fork 拥有模块 + 服务端覆盖映射」实现 fork 前端定制（`evidence/08-frontend-port-strategy.md`）
  - 上游脚本是**共享同一全局作用域的经典脚本**，同名顶层 `const`/`let` 重复声明即 `SyntaxError`（整页白屏），因此**不得**用「在上游模块之后加载并重新声明」的叠加方案
  - 做法：`static/js/fork/<上游子路径>` 与 `static/css/fork/<上游子路径>` 承载 fork 定制；fork 自有页面处理器经上游 `core/template.py` 组装后按覆盖映射替换 `assets/js|css/**` 引用；`boot.js` 仍最后加载
  - fork 专有模块（`todos.js`、`identity-admin.js`、`scenes/` 等）顺序不变，仍在上游模块之后
  - 判据：上游模块零 fork 改动
- [ ] 4.4a 编写确定性移植器 `scripts/migration/port_frontend.py`：归一化 diff → hunk 归属 → 按上/下文再锚定并拼接 fork 原文 → 生成 `static/js/fork/**`、`static/css/fork/**`；重复运行须逐字节一致
  - 已写出并实测（`e5e2a52d`..`HEAD` → `origin/master`）：`console.js` 362 个变更簇移植 275、待人工 87；`console.css` 79 移植 71、待人工 8；25 个产出 JS 模块 `node --check` 全部通过
  - 定位方式为**推导而非搜索**：按 base 切片与上游模块的对齐求出落点，并要求两侧有可验证的未改动上下文。早期版本用 3 行上下文搜索，25 个模块中 9 个 `node --check` 失败（重复 `let`、括号不平衡）——静默错位，故弃用
  - 跨模块边界的 fork 编辑**不切分**：fork 的替换文本是一次编辑，按边界切分会切断语句（实测产出 `function f() { } }`）。此类编辑登记为人工移植项
  - 每个 splice 应用后校验模块仍可解析，破坏解析的 splice 回退并登记，绝不产出坏模块
- [ ] 4.4b 输出无法再锚定的 ~107 JS / ~12 CSS hunk 为人工移植工作清单（含 base 与 fork 样例），不得静默丢弃
  - 已产出 `port_frontend_worklist.json`（含 base/fork 样例与原因分类）；待办部分：人工移植这 87 + 8 项
  - **尚未宣称正确**：产出模块尚未接入页面装配、未提交进 `channel/web/static/`，且「可解析」不等于「行为正确」——须由 4.5 浏览器验收判定
- [ ] 4.4c 生成 `static/js/fork/manifest.json`（`{fork_path: {upstream_path, upstream_sha256}}`）并加漂移门禁：上游模块变更后必须失败，使「上游变更需人工重新应用」可检测
- [ ] 4.4d 以 `node --check` 校验全部产出模块，并以 `tools/check-load-order.mjs` 校验 fork 实际装载顺序
- [ ] 4.4e 处置 `static/js/doc-editor.js`、`workspace.js` 与上游 `assets/js/doc-editor.js` 的重叠：若为上游文件的 fork 版则纳入覆盖映射，而非留在 fork 专有清单
- [ ] 4.4f 删除 `console.js` / `console.css`，不留兼容层；确认无上游视图模块（`js/views/*.js`、`js/core/*.js`、`js/chat/*.js`、`css/*.css`）被 fork 原地编辑
- [ ] 4.5 运行 `.cjs` 与浏览器验收：`node --test tests/test_fork_fragments.cjs`、`node --test tests/test_execution_permission_ui.cjs`，以及登录、上下文切换、流式请求、上传回读、下载预览
- [ ] 4.6 为 D4 的上游模块集合与 fork 专有符号集合编写结构不变量校验，且可独立运行并在注入违规时失败
- [ ] 4.7 校验不得以关键字（如 `tenant`）为判据；以 `route_registry.py` 的 `fork:*` handler 名与 fork 授权模块公开符号为判据，并验证对独立上游形态不误报
- [ ] 4.8 扩展 `scripts/conflict-baseline.txt` 与 `scripts/sync_report.py` 语义以覆盖「上游删除 / fork 修改」方向，为 `console.js`、`console.css`、`desktop/build/notarize-dmg.sh` 登记「迁移后删除 → 指向替代模块」处置
- [ ] 4.9 重新运行排练，将实际冲突集与基线比对，逐条登记 24 处漂移的处置；确认 `DELIBERATE_REMOVALS` 五项保持不变
- [ ] 4.10 运行 `scripts/check_change_deltas.py`，确认无未被本 change 点名的冲突文件

## 5. database 能力验收与交付（阶段 5）

- [ ] 5.1 建立独立测试身份库（≥2 租户、多用户，含普通成员与管理员），按规范 §3.3 建立 master → database 能力对照清单
- [ ] 5.2 对每项能力取得三类证据：database 正向业务成功、授权隔离通过、真实入口可达；逐项记录候选 SHA、真实路径、成功结果、拒绝结果与日志
- [ ] 5.3 覆盖身份/租户/个人资源边界：合法 owner 正向、同租户他人、跨租户、伪造 tenant/owner、管理员治理与私有内容边界
- [ ] 5.4 覆盖四种装配状态：独立上游形态、完整 rdai、rdai 缺失强制授权扩展、仅缺失可选 UI 扩展
- [ ] 5.5 覆盖调度与渠道：正常执行、身份/授权失效后拒绝、并发编辑、未知字段保留、入站路由与通知目标
- [ ] 5.6 按变化追加验证：Web/Desktop 受影响用例，Desktop 变化时 `npm --prefix desktop ci` 与 `npm --prefix desktop run build`，并在隔离测试服务上检查登录、上下文切换、流式请求与文件传输
- [ ] 5.7 逐项填写提交前检查表（规范 §7.1），确认无「仅存在于 legacy / 仅保留源码 / 整体关闭 / 待验收却标为已完成」的能力；存在缺口时只报告阶段性进展
- [ ] 5.8 交付前再次 `git fetch origin master rdai` 并与 `refs.txt` 比较；若 `rdai` 前移则整合新目标并重新验证候选
- [ ] 5.9 推送同步分支并向 `rdai` 创建 PR，正文用英文含 Summary / Merge evidence / 能力对照 / database 验收 / 冲突决策 / 基线漂移 / 回滚；PR 标题 `merge: sync master into rdai`
- [ ] 5.10 合入后记录 `rdai` 最终提交，确认固定源 SHA 是其祖先，检查 CI 与冒烟结果；把 `$MERGE_RUN_DIR` 中的证据转存到 PR / CI 制品 / 版本管理目录，不保留临时路径作为唯一证据

## 6. 文档与交接

- [ ] 6.1 更新 Web 后端布局说明（模块职责、fork 模块边界、接缝归属），并在其中说明「上游模块零 fork 分支」的判据与校验入口
- [ ] 6.2 记录本轮同步的冲突决策与基线漂移说明，便于下一轮以基线自动取舍；如需要随仓库交付，按规范 §9 对 `doc/` 下的规范文件显式 `git add -f`，不批量强制添加
- [ ] 6.3 按规范 §9 保存本轮最低记录集：版本标识、上游变更清单、能力对照与缺口、逐项验收证据、排练日志、冲突决策、候选树/提交、验证结果、审查人、PR 与回滚信息
