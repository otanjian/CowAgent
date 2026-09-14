## Why

database 模式下，Agent 生成的**文件工件（artifact）**在控制台既**无法下载**，**预览面板也显示 `not found`**。实测（默认租户，已登录，工件位于非默认智能体工作区）：

```
artifact  : /Users/jiantan/cow/agents/business-analysis/websites/procurement-supplier-summary.html  (17452 B)
卡片      : raw_url     = /api/file?path=<abs>
            preview_url = /preview/<hmac-dir-token>/procurement-supplier-summary.html

GET /api/file?path=<abs>   无 X-Tenant-ID -> 400 {"code":"missing_tenant"}   # 下载按钮
GET /preview/<token>/<name> 无 cookie     -> 404 "not found"                 # 预览面板
```

两处缺陷同源，都是「浏览器原生请求无法携带租户/凭据」这一已被规范识别的情形没有覆盖到工件链路：

1. `/api/file` 在权威清单中标为 `tenant`，要求 `X-Tenant-ID`。但工件卡片用 `<a href=... download>` 直接导航下载（消息里的图片也是 `<img src>` 子资源），**结构上无法携带自定义请求头**，于是被门禁判为「未选择租户」400。`/uploads/(.*)` 与 `/stream` 已按「租户由被寻址资源派生」显式豁免，`/api/file` 是同类但被遗漏。
2. `/preview` 是能力令牌路由（HMAC 目录令牌即授权，沙箱 iframe 无法带 cookie）。其处理器额外用 `_is_path_allowed()` 做纵深根校验，而该函数在 **public** 请求里只拿得到平台根：`_get_workspace_root()` 因无租户身份抛出 403 并被 `except` 吞掉，租户/智能体工作区不在允许根内，于是合法令牌也 404。

## What Changes

- **`/api/file` GET 改为「租户由被寻址资源派生」**：权威清单加显式标记；新增 `_file_identity_scope()`，先认证调用者，再由**文件自身工作区**（租户共享根或绑定智能体 workspace，服务端读取）派生租户并 `resolve_context` 校验成员资格；显式租户选择仅做冲突校验。对象级 `_authorize_db_file_path`、私有属主与 `agent.read` 校验保持不变。
- **`/preview` 的纵深根校验覆盖全部可信工作区**：`_is_path_allowed()` 的允许根集合纳入「所有租户共享根 + 所有绑定智能体 workspace」（静态来源，不依赖请求身份），并移除会因无身份抛出 `web.HTTPError` 的 `_get_workspace_root()` 调用——构造该异常会改写 `web.ctx.status` 与响应头，即便 `except` 吞掉也会污染成功响应。
- 前端不改：工件卡片本就用 `raw_url` 下载、用 `preview_url` 预览；修好两端后端契约即可。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `console-route-lifecycle`：把「租户由被寻址资源派生」的显式标记从 `/stream`、`/uploads/(.*)` 扩展到同样是浏览器原生发起的文件读取 `/api/file`（下载导航与 `<img>` 子资源）。
- `platform-file-browsing`：补充能力令牌预览（`/preview`）的纵深根校验口径——允许根覆盖全部租户/智能体工作区，但仍拒绝这些之外（含操作员主目录）的路径，且不得因构造身份拒绝异常而污染响应。
- `database-runtime-consumers`：浏览器传输契约补充「已登录且获权的成员必须能下载与预览本租户 Agent 生成的工件」。

## Impact

- 后端：`channel/web/web_channel.py`（`_tenant_owning_path`、`_file_identity_scope`、`_tenant_workspace_roots`、`_is_path_allowed`、`FileServeHandler.GET`）。
- 路由：`channel/web/route_registry.py`（`/api/file` GET 增加 `tenant_from_resource` 标记；策略仍为 `tenant`，路由基线不变）。
- 测试：新增 `tests/test_console_file_transport.py`（7 用例：下载派生租户/跨租户 403/冲突 400/匿名 401/不可见 404；预览能力令牌 200/非可信目录 404）；更新 `tests/test_http_policy.py`、`tests/test_route_registry.py`。
- 不改动：数据库 schema、租户目录布局、`agent_workspace` 语义、其他路由策略、门禁确定性拒绝顺序。
