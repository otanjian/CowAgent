## Why

database 模式下，控制台的附件链路对已登录且获权的用户整体失效：输入框**粘贴图片/选择文件后毫无反应**（缩略图也不显示）。

实测证据（隔离 database 实例，已登录 admin，默认智能体绑定本租户）：

```
A) POST /upload      无 X-Tenant-ID -> 400 {"code":"missing_tenant"}
B) POST /upload      带 X-Tenant-ID -> 200 {"status":"success", ..., "preview_url":"/uploads/web_7c54351c.png"}
C) GET  /uploads/x   无 X-Tenant-ID -> 400 {"code":"missing_tenant"}
D) GET  /uploads/x   带 X-Tenant-ID -> 200 (图片字节)
```

三处缺陷叠加：

1. 控制台的 `fetch` 包装只为 `/api/*` 与 `/(message|stream|poll|cancel)` 注入 `X-Tenant-ID`；`/upload` 不在其中，因此上传请求一律被门禁判为「未选择租户」而 400。前端 `handleFileSelect` 对失败分支**静默**（直接 `splice` 掉占位项、不提示），所以用户看到的是「粘贴无反应」。
2. `upload_file` 返回的 `preview_url` **不带 `agent_id`**，而上传写入的是被授权智能体的 `tmp/`。前端聊天预览用 `<img src=preview_url>` 读回时只能落到租户默认智能体，与上传目标不一致（语音路径已带 `agent_id`，此处遗漏）。
3. `/uploads/(.*)` 在权威清单中标为 `tenant`（要求 `X-Tenant-ID`），但它被浏览器当作 `<img>`/`<audio>` 子资源读取，**结构上无法携带自定义请求头**，因此即便修好 1，缩略图仍会 400。这正属于既有规范已规定的「租户确实无法由请求头给出」的情形，应当以「租户由被寻址资源派生」显式标记，而不是留作路径特例。

## What Changes

- **前端**：把 `X-Tenant-ID` 注入从硬编码正则改为一份显式的「非 `/api` 租户传输」清单，并纳入 `/upload`；新增传输时只需改这一处。
- **后端 `upload_file`**：`preview_url` 补上 `?agent_id=<被授权智能体>`，与已有语音 `audio_url` 的做法一致，使读取目标与写入目标同源。
- **权威清单 + handler**：`/uploads/(.*)` 的 GET 标记为「租户由被寻址资源派生」；`UploadsHandler` 改为先认证调用者，再由被寻址智能体的绑定解析租户并校验成员资格（复用 `resolve_context`），不再要求 `X-Tenant-ID`。智能体绑定、私有属主与 `agent.read` 校验保持在 handler，不放宽。
- 携带的 `X-Tenant-ID`（如有）仍须与资源派生租户一致，冲突时 400；无法定位到任何绑定智能体时 404（不泄漏存在性）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `console-route-lifecycle`: 把「租户由被寻址资源派生」的显式标记从仅 `/stream` 扩展到同样无法提供租户头的子资源读取 `/uploads/(.*)`。
- `database-runtime-consumers`: 「database 模式开放已适配运行消费者」补充浏览器传输契约——已登录且获权的浏览器必须能完成附件上传与上传回读，包括结构上无法携带租户头的子资源请求；不得以「未选择租户」400 拒绝，也不得静默丢弃。

## Impact

- 前端：`channel/web/static/js/console.js`（fetch 包装的租户头注入清单）。
- 后端：`channel/web/web_channel.py`（`upload_file` 的 `preview_url`；`UploadsHandler` 的身份作用域）。
- 路由：`channel/web/route_registry.py` 与 `scripts/route-baseline.txt`（`/uploads/(.*)` GET 增加资源派生标记；策略仍为 `tenant`）。
- 不改动：`agent_workspace`/租户目录布局、数据库 schema、门禁的确定性拒绝顺序、其他路由策略。
