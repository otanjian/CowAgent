## 1. 回归测试（RED）

- [x] 1.1 新增 `tests/test_console_upload_transport.py`：database 模式下 `POST /upload` 缺 `X-Tenant-ID` 返回 400（固化门禁契约，证明必须由前端带头）
- [x] 1.2 新增 `tests/test_console_upload_transport.py`：`POST /upload` 带 `X-Tenant-ID` 成功，且响应 `preview_url` 含 `?agent_id=<被授权智能体>`（当前失败：缺 `agent_id`）
- [x] 1.3 新增 `tests/test_console_upload_transport.py`：`GET /uploads/<name>?agent_id=<本租户智能体>` 仅带 cookie（无租户头）返回 200 与文件字节（当前 400 missing_tenant）
- [x] 1.4 新增 `tests/test_console_upload_transport.py`：非成员读取同一 `GET /uploads/...` 返回 403；无绑定智能体返回 404；带头与派生租户冲突返回 400；无凭据返回 401；路径穿越不 200
- [x] 1.5 新增前端断言（`tests/test_console_upload_frontend.cjs`）：`fetch` 包装对 `/upload` 注入 `X-Tenant-ID`，对 `/api/auth/*` 不注入，对 `/uploads/...` 不注入
- [x] 1.6 确认 1.3 与 1.5 在实现前失败（实测：7 个后端用例 + 1 个前端用例 RED）
- [x] 1.7 `tests/test_route_registry.py` 新增定点断言：`/uploads/(.*)` GET 策略仍为 `tenant` 且已声明资源派生豁免

## 2. 实现（GREEN）

- [x] 2.1 `console.js`：把租户头注入清单抽为 `_TENANT_TRANSPORT_PATHS` 常量并纳入 `/upload`，保留 `/api/*` 规则与 `/api/auth/*` 排除
- [x] 2.2 `web_channel.upload_file()`：`preview_url` 追加 `?agent_id=<被授权智能体>`（有值时）
- [x] 2.3 `web_channel`：新增 `_uploads_identity_scope()`（认证 → 由智能体绑定派生租户 → 冲突检查 → 成员校验 → 发布身份）
- [x] 2.4 `UploadsHandler.GET()` 改用新 scope，保留绑定/属主/`agent.read` 对象级校验与路径穿越防护
- [x] 2.5 `route_registry.py`：`/uploads/(.*)` GET 增加 `tenant_from_resource=True`
      （基线无需改动：`scripts/route-baseline.txt` 记录格式为 path/method/policy/permission，不含该标记，策略仍为 `tenant`）

## 3. 验证

- [x] 3.1 运行新增测试与 `tests/test_http_policy.py`、`tests/test_route_registry.py`、`tests/test_http_gate.py`（95 passed）
- [x] 3.2 运行 `tests/test_web_consumer_closure.py`、`tests/test_chat_identity_context.py`、`tests/test_platform_file_browsing.py`、`tests/test_session_idor_closure.py`、`tests/test_tenant_channel_console_scope.py`（35 passed）
- [x] 3.3 `openspec validate fix-console-attachment-upload --strict` 通过
- [x] 3.4 全量 Python 套件对照：把我的 hunk 反向 patch 后重跑，失败集合与含本 change 时**逐条一致**（`comm -13` 为空，即本 change 未新增任何失败）；基线 45 项失败中含本 change 新增的 7 项（代码回退后如期失败）
- [x] 3.5 全部 39 个前端 `.cjs` 套件运行完毕；改动涉及的 3 个套件（i18n parity、session history、sidebar account）在改动前后**失败集合完全相同**，均属既有失败
- [x] 3.6 重启服务后在真实实例验证（见 4.1）

## 4. 收尾

- [x] 4.1 记录验收证据（复现命令与结果）
- [x] 4.2 确认未放宽其他路由策略：`derive_route_policy()` 中仅 `/uploads/(.*)` GET 新增该标记，其余 `tenant` 路由仍 `tenant_from_resource=False`
