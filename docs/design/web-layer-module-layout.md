# Web 层模块布局与接缝

> 依据：change `adopt-upstream-web-split`（主规范 capability `web-console-module-seams`）。
> 本文说明控制台 Web 后端的模块职责、fork 模块边界与接缝归属，以及「上游模块零 fork 分支」
> 的判据与校验入口。前端布局见 change `adopt-upstream-web-frontend-split`。

## 1. 为什么要拆分

上游对控制台做过一次整体重构（`refactor/split-web-channel`）：`console.js`（17,315 行）与
`console.css`（4,039 行）被删除，改为按视图划分的模块树。重构前，fork 的业务实现与上游实现
同处 `channel/web/web_channel.py` 一个巨型文件，于是**每一次上游重构都在同一批文件上正面相撞**
——本轮 46 处冲突里有 25 处是新增漂移，无法靠既有接缝自动取舍。

拆分的目标是可度量的：上游再次重构同一批视图模块时，冲突应限于 fork 自有模块与上游模块的
登记边界，不再出现「fork 业务实现与上游实现同处一个巨型文件」的整文件冲突。

## 2. 模块职责

```
channel/web/
  web_channel.py         入口模块：URL 表 + 两个应用工厂 + 公开符号转出
  route_registry.py      路由权威清单：路由存在性与授权策略的唯一事实源
  README.md              上游对入口模块的说明（上游文件，保持原样）

  api/                   上游视图 handler（上游文件，逐字节保持）
    agents.py chat.py channels.py config.py files.py knowledge.py
    models.py pages.py scheduler.py sessions.py skills.py …

  core/                  上游共享管道（上游文件，逐字节保持）
    channel.py _common.py template.py …
    ↑ desktop 专用的本地路径导入路由在这里，由 _is_loopback_request()
      与 _desktop_token_matches() 双重把关

  fork/                  fork 自有模块（上游不存在这些路径）
    __init__.py
    authorization.py      授权 helper：@property 式 owner/tenant 解析与 _require_* 判定
    common.py             共享管道：请求上下文、作用域辅助
    runtime.py            WebChannel 运行时（SERVING 事件、SSE/polling、媒体改写）
    handlers/             按上游 api/ 的视图划分的平行实现
      agents.py chat.py channels.py config.py files.py knowledge.py
      models.py pages.py scheduler.py sessions.py skills.py …
    static/               fork 前端资源（见前端 change）
```

`fork/` 之外的 fork 专有模块（`admin_handlers.py`、`branding.py`、`help_site.py`、
`memory_console.py`、`openai_api.py`、`project_import.py`、`scan_onboarding.py`、
`tenant_workspace.py`、`todo_handlers.py`、`auth_handlers.py`、
`external_connection_handlers.py`、`admin_overview.py`）保持在原位与新模块并列：搬迁它们会
产生大量路径 churn 并波及测试引用，而校验只需要知道「哪些文件是上游的」。

## 3. 两个应用工厂（D8）

`web_channel.py` 同时提供两套应用工厂，**各以自己的命名空间解析 handler**：

| 工厂 | URL 表 | handler 命名空间 | 用途 |
| --- | --- | --- | --- |
| `build_app()` | `URLS` | `channel/web/api/**` | 独立上游形态；上游 `core/channel.py` 调用它 |
| `build_web_app()` | `_WEB_URLS`（由 `route_registry` 派生） | `channel/web/fork/**` | 完整 rdai 形态 |

分命名空间不是风格选择，而是正确性要求：上游 `api/` 与 fork 各有 76 / 79 个 handler 类，
其中 **64 个同名**。若两套同类导入同一 `globals()`，后导入者静默取胜，必有一套 URL 表解析到
另一栈的 handler——这不是崩溃，而是**静默的错误授权**。

`WebChannel` 与 `SERVING` 必须仍是 fork 的实现（`fork/runtime.py`）：`channel_factory` 按名解析
`channel.web.web_channel.WebChannel`，只有 fork 的实现会置位 fork 等待的那个 `SERVING` 事件。

## 4. 路由权威清单

`channel/web/route_registry.py` 是路由存在性与授权策略的唯一事实源。每条路由登记
`pattern` / `handler` / `source` / 每方法策略，`source` 为 `upstream` 或 `fork:<area>`，使 fork
新增路由的合并不要求编辑上游模块的路由字面量。

三腿覆盖不变量以 **handler 实现为第三腿**：内省实际实现的 HTTP 方法，与清单比对，任一不一致
即失败。上游拆分引入新视图模块或调整 handler 归属时，该校验在合并后立即暴露未登记或错位条目。

## 5. 「上游模块零 fork 分支」的判据与校验入口

### 判据

1. **上游模块集合显式声明**，不靠目录约定推断：`channel/web/api/**`、`channel/web/core/**`、
   `channel/web/web_channel.py`、`channel/web/README.md`。声明集合之外的模块出现在上游区域
   即为发现——上游新增模块时该集合必须被更新，而不是被静默吸收。
2. **fork 专有符号集合是推导出来的**：`route_registry.py` 中 `fork:*` 行的 handler 名，加上
   `fork/{authorization,common,runtime}.py` 与 `fork/handlers/**` 的顶层符号，**再减去上游视图
   与管道模块已定义的同名符号**。
3. **入口模块的例外**：它可以 import 并转出 fork 符号（这是它的职责），但**不得定义**它们。

### 为什么不用关键字

上游自身大量使用 `tenant` 语义命名（调用方租户、租户成员、租户范围资源），以关键字为判据会
误伤上游的正当代码。一个会对上游代码报警的门禁，最终会被关掉而不是被修好。判据只认 fork 专有
符号、注册块与模块归属。

同名减法同理不是便利而是必要条件：`_live_channel_manager` 是上游 `core/_common.py` 的符号
（fork 只是同名再包装），`ChatHandler` 是 64 个同名 handler 之一——**同名是共同拥有，不是
fork 分支**。

入口模块不参与同名减法：它是两套栈名字唯一同时在场之处，若让它投票，入口模块内重定义的函数体
就会把自己洗进「同名」集合而检查永不触发。

### 校验入口

```bash
.venv/bin/python scripts/check-web-module-seams.py          # 结构不变量（可 --root 指向任意树）
.venv/bin/python scripts/check-route-coverage.py            # 三腿路由覆盖不变量
.venv/bin/python scripts/check_change_deltas.py adopt-upstream-web-split
```

`scripts/check-web-module-seams.py` 独立运行，违反时非零退出，可接入 CI。注入违规的行为由
`tests/test_web_module_seams.py` 覆盖：上游模块内引用 fork 授权符号 / fork 路由 handler /
出现 fork 注册块，以及入口模块内重新定义 handler，四类全部失败；无 fork 包时通过（独立上游
形态不误报）。

## 6. 上游 handler 变更的移植义务

当某路由的活跃实现由 fork 承载时，上游对该 handler 的功能改进与安全修复**须经人工评估后移植**，
不得因校验未覆盖而默认视为无需处理。漂移守护以固定上游提交记录 fork 已平行实现的 handler 清单
与其上游来源；上游改动时校验失败并指出需移植的路径与提交。

同一机制也用于前端 fork 模块（见前端 change 的 `manifest.json`）：上游模块变更即失败并指出需
人工重新应用的路径。**代价是明确接受的**——fork 拥有更多模块，意味着上游演进时的人工成本集中
在这里；对冲是把「静默丢失」变成「必须处理」。
