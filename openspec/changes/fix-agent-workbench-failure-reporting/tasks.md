## 1. 根因排查（先定位，再改）

- [x] 1.1 复现用户上下文：以受影响用户（`test15` /「AI启航团队」）的真实身份请求 `/api/agents?view=workbench`——cookie 与 Bearer 两种凭据均返回 200 与 4 个智能体
- [x] 1.2 用真实浏览器会话（mDNS 主机以隔离 cookie，账号 `test15`、租户选择器显示「AI启航团队」）进入该视图：卡片正常渲染 4 张；`_wbContext` 恢复视图的启动路径亦正常
- [x] 1.3 把控制台的校验谓词逐条跑在真实响应上：`!res.ok`、`status!=='success'`、`agents` 非数组、`channel_instances`/`revision` 存在、`id`/`can_chat`/`is_default` 类型，**零条命中**
- [x] 1.4 排除：缺租户头（会隐藏租户选择器，而截图显示其已解析）、403（该用户是本租户成员）、普通成员资源授权（返回 1 个智能体）、失效 `agent_id` 参数、旧后端结构、401（返回 JSON 并触发登录浮层）
- [x] 1.5 核对服务端日志与最近改动：无当日 `Agents API error`；`fetchAgentWorkbench` 只抛单一错误键、失败不落任何日志、渲染在 `.then()` 内被 `.catch` 兜住、瞬态失败无自动重试
- [x] 1.6 结论：服务端与该租户数据健康、截图那一份失败不可稳定复现；可确定的缺陷是**失败处置**——不可定位、不可自愈、且与渲染缺陷混淆

## 2. 回归测试（RED）

- [x] 2.1 `tests/test_agent_workbench_frontend.cjs`：瞬态读取失败（传输错误）自动重试一次并呈现列表（实现前失败：`1 !== 2`）
- [x] 2.2 同文件：持久的权限拒绝呈现 `agent_workbench_no_permission` 且不重试，并记录底因（实现前失败：`agent_workbench_failed`）
- [x] 2.3 同文件：未选择租户呈现 `agent_workbench_no_tenant`（实现前失败）
- [x] 2.4 同文件：成功读取后的渲染异常**不得**呈现为读取失败，且异常记入控制台（实现前失败：`error === true`，即渲染缺陷确实被当成读取失败）
- [x] 2.5 同文件：让测试语境捕获 `console`（返回 `logs`），使故意走失败路径的用例不污染测试输出

## 2b. 验证中发现并修复的缺陷（首帧绘制逃逸）

- [x] 2b.1 浏览器实测发现：`loadAgentWorkbench()` 进入时的首帧绘制（第 2291 行 `renderAgentWorkbench()`）**未**经由 `paintAgentWorkbench()` 隔离
- [x] 2b.2 后果确认：绘制「加载中」时抛出的异常**同步逃逸**，调用方（`onclick` 与测试语境）拿到异常而非 promise，异常既不落日志也不与读取失败区分（实测栈：`at renderAgentWorkbench → at loadAgentWorkbench`，`Uncaught (in promise) Error: card exploded`）
- [x] 2b.3 新增回归用例「a render fault while painting the loading state cannot escape the loader」，并确认在未修复代码上失败（RED：`a paint fault must not escape the loader: paint exploded`）
- [x] 2b.4 修复：首帧绘制同样经由 `paintAgentWorkbench()`（GREEN：该用例通过）

## 3. 实现（GREEN）

- [x] 3.1 `console.js`：新增 `_wbFailureKey()`（服务端 `code` 优先、HTTP 状态回落 → 可操作文案键）
- [x] 3.2 `console.js`：新增 `_wbFailure()`；`fetchAgentWorkbench()` 按「传输失败 / 非 JSON / 非 2xx / 结构不符」分别携带 `wbKey`、`wbTransient`、`wbDetail`
- [x] 3.3 `console.js`：`loadAgentWorkbench()` 对 `wbTransient` 失败在上下文未变时静默重试一次；失败时记录 `[agent-workbench] list read failed:` 与底层证据
- [x] 3.4 `console.js`：新增 `paintAgentWorkbench()`，把渲染异常挡在读取结果之外（记日志，不改写 `_wbLoadedError`）；首帧绘制亦经由该包装
- [x] 3.5 `console.js`：失败分支显示 `_wbErrorKey`，重试按钮与网格结构保持不变
- [x] 3.6 i18n：`agents.js` 三语系各补 3 键；`tests/fixtures/console_i18n_snapshot.json` 同步（已核对：脚本重序列化与既有格式逐字节一致，无格式漂移）

## 4. 验证

- [x] 4.1 验收证据：受影响租户的列表响应（200 + 4 智能体）、渲染结果（4 张卡片）、5 项新用例由 RED 转 GREEN 的失败信息与通过信息均已记录
- [x] 4.2 `node --test tests/test_agent_workbench_frontend.cjs`：20 passed（新增 5 项），输出无多余日志
- [x] 4.3 `node --test tests/test_console_i18n_parity.cjs`：5 passed（新增键与快照逐键一致）
- [x] 4.4 全量前端套件对照（同一批测试文件，仅替换 `console.js`）：改造前 486 项 / 438 passed / 48 failed；改造后 486 项 / **443 passed / 43 failed**；失败集合 `comm` 比对结果——**由本 change 修复 5 项**（恰为新增用例），**回归 0 项**，**43 项失败前后完全相同**（36 项 `queueMicrotask is not defined` 属测试语境缺该全局；6 项在 `test_sidebar_account_frontend.cjs`，期望 `legacy`/`unknown` 而工作树已退役 legacy 模式；1 项 `tests/test_appearance_browser.cjs` 需真实浏览器）
- [x] 4.5 `node --check channel/web/static/js/console.js` 通过
- [x] 4.6 `openspec validate fix-agent-workbench-failure-reporting --strict` 通过
- [x] 4.7 真实浏览器实测（受影响账号 `test15` /「AI启航团队」，`rockdemacbook-air-2.local:9899`）：
  - 正常路径：工作台渲染 4 张卡片，`_wbErrorKey` 为空、`_wbLoadedError` 为 `false`，无控制台错误（即截图的「加载失败」不可复现）
  - 403 确定性拒绝：请求 **1 次（不重试）**，呈现「暂无查看智能体的权限，请联系管理员」+「重试」，控制台记录 `[agent-workbench] list read failed: http 403 forbidden denied`
  - 503 后成功：请求 **2 次（重试一次）**，最终渲染 4 张卡片、无失败态、无错误日志
  - 渲染异常：`loadAgentWorkbench()` **不再同步抛出**（`escapedSynchronously: null`）且仍返回 promise，`_wbLoadedError` 保持 `false`，控制台记录 `[agent-workbench] render failed: Error: card exploded`，恢复后仍渲染 4 张卡片
  - 已验证测试用的 `fetch` 补丁与渲染补丁全部复原，页面回到正常状态

## 5. 收尾

- [x] 5.1 确认为未放宽任何授权：服务端投影、路由策略与对象级校验均未改动；新增文案仅描述已存在的拒绝原因
- [x] 5.2 确认为未改动既有并发语义：request-seq 与 `_wbContext()` 守卫原样保留，重试亦受该守卫约束

