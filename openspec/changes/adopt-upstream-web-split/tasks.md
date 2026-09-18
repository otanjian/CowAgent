# Tasks

阶段门槛未通过前不得进入下一阶段；「接口占位」「本机可跑」不作为门槛通过。

## 1. 固定版本与准备（阶段 0）

- [ ] 1.1 确认目标为 `rdai`、来源为 `origin/master`，记录源/目标/共同祖先 SHA 与本地额外提交范围到 `refs.txt`
- [ ] 1.2 建立独立克隆（`git clone --no-hardlinks`），另设真实远端，`fetch origin master rdai`，复核 `origin/rdai` 是目标 HEAD 祖先
- [ ] 1.3 从确认的目标 SHA 创建同步分支 `codex/sync-master-to-rdai-<时间戳>`，启用 `rerere`，记录上游提交清单与 `git diff --stat/--name-status`
- [ ] 1.4 在克隆内按候选声明安装依赖（含测试依赖），确认 `.venv` 与测试依赖可用；记录环境与命令
- [ ] 1.5 运行一次排练（`scripts/sync-from-master.sh`）作为迁移前基线，保存日志并确认排练后工作树干净、无 `MERGE_HEAD`
- [ ] 1.6 盘点并记录「迁移前」的 route coverage、接缝测试与权限隔离测试结果，作为阶段 1 的对照基线

## 2. 后端 fork 定制迁出（阶段 1，行为保持）

- [ ] 2.1 枚举 `channel/web/web_channel.py` 中 fork 专有符号清单（15 个 fork-only handler、约 186 个私有 helper、29 个 `_require_*`/`_authorize_*`），登记为迁移清单并记录每项的目标归属
- [ ] 2.2 建立 fork 授权模块，迁入请求上下文/作用域辅助（`_db_scope`、`_current_db_identity`、`_authorized_model_codes`、`_web_runtime_identity_snapshot` 等）
- [ ] 2.3 按 D2 三层分工逐项归位授权判定：可由路由+方法+身份表达的并入 `route_registry.py` 策略；需被寻址资源的经 `auth/object_scope.py` 切片授权；确需 handler 局部状态的留在 fork 授权模块
- [ ] 2.4 将 15 个 fork-only handler 迁入对应 fork 模块（branding ×4 → `branding.py`；memory/personal memory ×5 → `memory_console.py`；personal channel ×2；project import ×3 → `project_import.py`；`_MemoryWriteHandler`）
- [ ] 2.5 为确需 handler 内部授权的上游 handler 建立 fork 子类（继承上游 handler、覆写相应方法），并在 `route_registry.py` 登记 fork 子类名
- [ ] 2.6 更新 `route_registry.py` 的 handler 解析，使其从拆分后的模块集合解析 handler 名称（含 fork 模块与上游模块），保留 `source` 登记与表序语义
- [ ] 2.7 补齐 `channel/web/fork_routes.py`（或等价 fork 扩展模块）以经既有 `_load_fork_extensions()` 钩子调用 `register_fork_routes()`
- [ ] 2.8 编写/更新测试，断言迁移后的授权结果与迁移前一致（同一请求同一判定），覆盖合法成员正向、跨租户拒绝、跨 owner 拒绝、administrator 治理边界
- [ ] 2.9 运行并记录阶段 1 门槛：`scripts/check-route-coverage.py`、`tests/test_route_registry.py`、`tests/test_upstream_core_seams.py`、`tests/test_no_resurrection_legacy_identity.py`、`tests/test_identity_resource_authorization.py`、`tests/test_http_policy.py`
- [ ] 2.10 提交阶段 1 迁移提交（fork 自有提交，非 merge），确认可独立回退；记录迁移前后对照证据

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
