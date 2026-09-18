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

- [ ] 4.1 枚举 `console.js` / `console.css` 中全部 fork 定制，登记为迁移清单（外观、身份管理、待办、场景工作台、外部连接、渠道工作台、品牌、i18n 扩展、片段加载）
- [ ] 4.2 采用上游 `static/js/{core,views,chat}/*` 与 `static/css/*`，确认 `chat.html` 采用上游结构且 fork 挂载元素按 `seam:` 重新登记
- [ ] 4.3 将 fork 前端定制实现为独立模块，经 fork 引导脚本在上游模块之后装载；`static/js/fragments.js` 的挂载语义保留并复用 `fork-fragment-mounted` 事件
- [ ] 4.4 删除 `console.js` / `console.css`，不留兼容层；确认无上游视图模块（`js/views/*.js`）被 fork 原地编辑
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
