## Why

租户管理员（`test15` /「AI启航团队」）使用页「智能体」整页显示 **加载失败**，界面上除一个「重试」按钮外没有任何可判断的信息：既看不出是没权限、没选租户、还是服务端一时不可用，也无法事后定位。

排查结论（详见 Impact 与 4.1 的证据）：

1. **服务端与该租户的数据是健康的**。用该用户的真实身份（cookie 与 Bearer 两种凭据）请求 `/api/agents?view=workbench`，返回 200 与完整 4 个智能体；把控制台那份校验谓词逐条跑在真实响应上，**零条命中**；用真实浏览器会话（同一账号、同一租户、租户选择器同样显示「AI启航团队」）进入该视图，卡片正常渲染 4 张。因此**截图那一份失败无法稳定复现**。
2. **失败处置本身有缺陷，这正是它不可诊断的原因**：

   - `fetchAgentWorkbench()` 对所有失败只抛一个 `agent_workbench_failed`，把「未选择租户（400 `missing_tenant`）」「无读取权限（403）」「登录失效（401）」「非 JSON 响应体」「服务端 5xx」「传输失败」压成同一句「加载失败」，用户无从判断能不能自救。
   - 失败原因**不写任何日志**，所以一份「加载失败」的报告事后无法回溯到状态码、错误码或连接错误。
   - `loadAgentWorkbench()` 的 `.catch` 同时兜住了**成功分支的渲染异常**：`renderAgentWorkbench()` 在 `.then()` 里执行，任何渲染期异常都会被当成「读取失败」显示。即一个真实的界面缺陷会被上报成另一种缺陷——这既是错误的归因，也正是「服务端明明正常却显示加载失败」这一现象的成因之一。
   - 瞬态失败（服务重启中、身份库短暂不可用导致的 5xx、连接中断）**没有自动重试**：一次抖动就会把页面留在死状态，只能靠用户手动点「重试」。

3. 这与既有规范已规定的「加载中 / 空态 / 失败三态可区分」并不矛盾，而是**要求不够**：三态可分，但失败态本身不可定位、不可自愈、且会与渲染缺陷混淆。

## What Changes

- **失败原因随异常携带**：新增 `_wbFailureKey()`，把服务端 `code`（优先）与 HTTP 状态映射为稳定的可操作文案键——`agent_workbench_no_tenant`（未选择租户）、`agent_workbench_no_permission`（无读取权限）、`agent_workbench_signed_out`（登录失效），无法归类时仍回落 `agent_workbench_failed`。
- **瞬态失败自动重试一次**：无响应（传输错误）、5xx、非 JSON 响应体视为 `wbTransient`，在**当前上下文未变**（沿用既有 request-seq + `_wbContext()` 守卫）的前提下静默重试一次；确定性失败（权限拒绝、缺租户选择、投影结构不符）**不重试**，避免用第二次请求拖延报告。
- **错误证据落日志**：`console.error('[agent-workbench] list read failed:', <http 状态/服务端 code+message 或传输错误>)`，使后续同类报告可直接定位。
- **渲染与读取解耦**：新增 `paintAgentWorkbench()` 包住渲染；渲染异常记入控制台，**不再**改写读取结果。成功读取后发生渲染异常时，`_wbLoadedError` 保持 `false`。
- **首帧绘制同样受隔离**：`loadAgentWorkbench()` 进入时的首帧绘制原先直接调用 `renderAgentWorkbench()`，绕过上面的隔离——绘制「加载中」时抛出的异常会**同步逃逸**出 `loadAgentWorkbench()`，使调用方（`onclick="loadAgentWorkbench(true)"` 与各测试语境）拿到异常而非 promise，也让该异常既不落日志、也无法与读取失败区分。改为同样经由 `paintAgentWorkbench()`（此为本次验证中实测发现并修复的缺陷，见 4.7）。
- **失败态使用真实原因**：`renderAgentWorkbench()` 的失败分支显示 `_wbErrorKey`（而非固定文案），重试按钮与网格结构不变。
- i18n 三个语系补齐 3 个新键；`tests/fixtures/console_i18n_snapshot.json` 同步（parity 契约要求逐键一致）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-workbench`: 「加载空态与失败可区分」补充失败态的**可定位性、可自愈性**与**与渲染缺陷的可区分性**——可操作原因须各自呈现、可能因重试而改变的失败须自动重试一次后仍失败才呈现、底层证据须落日志、渲染异常不得呈现为读取失败。

## Impact

- 前端：`channel/web/static/js/console.js`（工作台读取/渲染/失败分支）。
- i18n：`channel/web/static/js/i18n/agents.js`（zh / zh-Hant / en 各 +3 键）。
- 测试：`tests/test_agent_workbench_frontend.cjs`（+5 用例，并让测试语境捕获 `console` 以保持输出整洁）、`tests/fixtures/console_i18n_snapshot.json`。
- 不改动：`/api/agents?view=workbench` 的服务端投影与授权（已验证其对该租户返回正确数据）、路由策略、既有 request-seq/上下文守卫语义、卡片与空态结构。

### 明确不在范围内

- **共享重绘路径**：`rerenderDynamicViews()` 对 `agent-workbench` 的调用（`console.js:983`）与身份刷新处的若干 `renderAgentWorkbench()` 调用（`3499` / `3532` / `3569`）仍未做异常隔离。这与既有 `_renderSessionList()`、`_renderHistoryStatus()` 等分支同样是**未被隔离的既有模式**，且会同时影响历史/配置等其他视图，故不在本次按「读取失败上报」缺陷范围的改动内，避免顺带改变无关视图行为。本 change 的规范文本只约束**列表读取**的绘制路径。
