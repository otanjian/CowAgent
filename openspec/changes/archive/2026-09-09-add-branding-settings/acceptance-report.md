# 品牌设置验收报告

验收日期：2026-09-08。范围：当前工作区实现、`add-branding-settings` 的单实例验收要求，以及企业能力未接入时必须关闭写入的边界。

**整改复验结论：报告中的 8 项问题已完成代码修复，针对性回归通过。** 最终结果为 77 项 Python 测试、7 项前端交互测试通过，隔离实例的浏览器基本流程与并发冲突处理通过。Windows 文件锁仅完成模拟分支验证，仍需实机回归；企业写入继续关闭，整个 change 尚未整体验收完成。

下文保留原始验收记录（当时结论为暂不通过），整改内容及复验范围见文末。

## 已执行的验证

- `.venv/bin/python -m pytest tests/test_branding.py tests/test_cli_backup.py tests/test_agent_web_management.py tests/test_identity_web_handlers.py -q`：63 passed。
- `node --check channel/web/static/js/console.js`：通过。
- `openspec validate add-branding-settings --type change --strict`：通过；仅证明 change 格式有效。
- 浏览器访问 `http://localhost:9899/chat`，进入品牌设置，检查菜单、名称输入及清空描述后的预览。
- 使用真实 web.py 路由、真实签名 token 校验和临时品牌数据目录，补查保存、过期 revision、缺失资源、跨源拒绝、仅 query token 写入，以及数据库身份模式下的旧 token 写入。没有替换 `_require_auth` 或 `_check_auth`。
- 独立 Node 环境执行源码中的品牌事件绑定、提交和页面初始化函数，检查上传后恢复默认的 FormData，以及取消丢弃后的重新加载行为。
- 临时目录复现主配置损坏后的管理读取与保存；在 macOS 上模拟 Windows 文件锁分支，验证异常。

本轮未修改业务实现、change 任务勾选状态或正式实例已保存品牌。浏览器确认框操作遇到自动化超时，因此取消丢弃问题以下述源码函数复现为证据，不计为完整浏览器通过。未进行真实 Windows 系统测试，也未完成多标签、会话过期、三语、移动布局和所有故障场景的浏览器回归。

## 原始问题记录

### 1. [P1] 数据库身份模式仍接受旧共享密码凭据写品牌

位置：[web_channel.py](/Users/jiantan/ai_assistant/cowagent/channel/web/web_channel.py:3199)，同类入口包括 reset。

写接口只检查 `web_password` 和旧 `_require_auth()`，没有按 `identity_mode` 关闭企业写入，也没有可信平台权限或审计门槛。临时配置 `identity_mode=database`、保留共享密码后，携带该密码派生的旧 Bearer token，实际返回 `200 success` 并提交 revision 1；该请求没有数据库会话、`branding.manage` 或审计上下文。

应在真实企业授权与审计接入前，服务端明确拒绝该模式下的品牌写入；不能回退共享密码。管理 GET 同步反映只读原因。

### 2. [P2] 仅 query token 可以保存品牌

位置：[web_channel.py](/Users/jiantan/ai_assistant/cowagent/channel/web/web_channel.py:3209)。

向 `/api/branding?token=<有效旧 token>` 发 POST，不带 Cookie、Authorization、Origin 或 Referer，仍返回 `200 success` 并发布新版本。原因是通用 `_require_auth()` 接受 query token，而 `_branding_origin_ok()` 在无来源头时直接通过。这违反 change 明确规定的新写接口凭据边界，也让用于 SSE 等地址的 URL 凭据获得写能力。

品牌保存与 reset 应明确选择并验证 Cookie 或 Authorization 凭据，拒绝仅查询参数认证；Cookie 写入还应满足规范规定的 CSRF 校验。

### 3. [P2] 错误 HTTP 状态码未生效，冲突恢复流程无法触发

位置：[web_channel.py](/Users/jiantan/ai_assistant/cowagent/channel/web/web_channel.py:3141)。

使用 `web.status = ...` 不会设置 web.py 当前请求的 HTTP 状态。实测旧 revision 返回 `200 OK`，JSON 才标记 `version_conflict`；跨源拒绝及不存在的品牌资产同样返回 200。前端以 `r.status === 409` 展示重新加载操作，因此正常的并发冲突会落入普通保存失败，缺少预期的恢复入口。

应通过 `web.ctx.status` 或 `web.HTTPError` 正确返回完整 HTTP 状态，并覆盖管理读写、reset、资源及异常分支。路由测试需要断言真实响应状态，不能只验证 JSON code。

### 4. [P2] 上传图片后再使用默认 Logo，保存仍附带旧文件

位置：[console.js](/Users/jiantan/ai_assistant/cowagent/channel/web/static/js/console.js:10309)。

复现顺序：选择图片 → 使用默认 Logo → 保存设置。上传保存的是 `brandingDraft.logoFile`，默认按钮清除的却是 `logo_file`。运行实际事件及提交函数，得到 `logo_action=default` 且 FormData 仍含 `logo`，服务端会按互斥输入规则拒绝。

默认按钮应清除实际文件字段；提交时也应只在 `replace` 动作下附带图片。取消文件选择不能仅清空原生 input。

### 5. [P2] 取消丢弃后仍重新加载，草稿会被覆盖

位置：[console.js](/Users/jiantan/ai_assistant/cowagent/channel/web/static/js/console.js:10212)。

已有未保存编辑时再次点击当前品牌菜单，`initBrandingView()` 弹出丢弃确认。取消分支只把 dirty 改为 false，随后仍调用 `_loadBrandingSetup()`；返回的已发布数据会替换当前草稿。函数级复现实测取消后 `reloads=1`、`dirty=false`。此外，conflict 状态还会跳过丢弃确认。

用户取消时应立即返回并保留草稿；冲突后重新加载也必须先确认是否丢弃。

### 6. [P2] Logo 描述留空，侧栏仍显示“控制台”

位置：[console.js](/Users/jiantan/ai_assistant/cowagent/channel/web/static/js/console.js:110)，预览的 caption 分支有相同行为。

浏览器将描述清空后，登录及欢迎预览的描述隐藏，但侧栏预览仍显示“控制台”。发布应用函数同样将空描述替换为 `t('console')` 并强制取消 hidden。规范要求空描述隐藏所有描述行，包括侧栏。

应统一空值处理；浏览器标题和登录操作指引可以继续独立显示“控制台”，不应回填为品牌描述。

### 7. [P2] 配置损坏后回退值仍可编辑保存，会覆盖损坏原件并复用版本号

位置：[branding.py](/Users/jiantan/ai_assistant/cowagent/channel/web/branding.py:535)。

临时目录保存至 revision 2 后破坏主 JSON。管理读取无异常提示，返回历史 revision 1 且 `can_manage=true`；按该版本正常保存成功，生成 revision 2 并覆盖损坏文件。展示回退被当作有效编辑基线，既破坏诊断原件，又复用了已经发布过的版本号。

需要区分“首次无配置”和“已有配置损坏”；公开读取可以降级，管理读取应明确提示并阻止普通保存，保留受保护恢复入口和损坏原件。

### 8. [P2] Windows 文件锁分支会在写入前抛 TypeError

位置：[branding.py](/Users/jiantan/ai_assistant/cowagent/channel/web/branding.py:136)。

锁文件以文本模式 `a+` 打开，Windows 分支却写入字节 `b'\0'`。模拟该分支直接得到 `TypeError: write() argument must be str, not bytes`，且异常不在当前捕获范围内。保存、reset 和涉及已配置品牌的备份/恢复都依赖此锁。

应使用与锁操作一致的文件模式，修正锁定及解锁的字节位置，并在真实 Windows 环境验证。

## 复验门槛

上述问题修复后，应补充对应的真实路由状态/凭据测试和前端交互回归，再执行上传、保存、刷新、清空描述及恢复默认的完整浏览器流程。现有测试虽然全绿，但不能支持 `tasks.md` 中权限、冲突、草稿保护、配置损坏和完整端到端场景已通过的结论。企业阶段继续保留未完成状态，直至真实依赖验收通过。

## 整改与复验（2026-09-08）

| 原问题 | 修复结果 | 验证证据 |
| --- | --- | --- |
| 1 企业模式回退旧凭据 | 数据库及未知身份模式拒绝保存、reset；管理 GET 使用真实数据库会话，返回只读原因 | 实际签名凭据、真实数据库登录会话及 HTTP 路由测试 |
| 2 query token 写入 | 新管理认证仅选择 Cookie / Authorization；Cookie 额外要求同源和会话绑定 CSRF | query-only 保存及 reset 均为 401；缺失、伪造或其他会话的 CSRF 均为 403；Bearer 正常写入 |
| 3 HTTP 状态未生效 | 使用 web.py 请求上下文设置完整状态，统一错误响应 | 400/401/403/404/409/413/415/500/503 路由断言；真实页面冲突恢复入口出现 |
| 4 默认 Logo 仍附旧文件 | 清除 `logoFile`，仅 replace 动作附带文件 | 实际 FormData 回归；浏览器「上传 → 使用默认 Logo → 保存」成功，文本保留 |
| 5 取消仍丢弃草稿 | 使用确认回调，重入、离开和冲突重新加载均在确认后操作 | 前端状态测试；浏览器取消重入/冲突重载保留草稿，确认后加载新版本 |
| 6 空描述不隐藏侧栏 | 侧栏及 caption/desc 预览统一隐藏；移除侧栏描述的翻译回填标记 | 浏览器清空、保存、刷新后说明行隐藏，Logo、标题和品牌名正常 |
| 7 损坏配置覆盖与版本复用 | 管理页只读且保留受保护 reset；复制损坏原件后恢复，持久版本上界防止复用 | 服务及真实路由测试覆盖损坏、缺失主配置、旧存储迁移、保留原件失败、旧草稿冲突 |
| 8 Windows 文件锁异常 | 二进制锁文件，锁定/解锁均定位第 0 字节，异常时关闭文件 | 模拟 msvcrt 分支，验证字节内容、重复加锁偏移、超时资源释放；未声称真实 Windows 通过 |

最终执行命令：

```sh
.venv/bin/python -m pytest tests/test_branding.py tests/test_cli_backup.py tests/test_agent_web_management.py tests/test_identity_web_handlers.py -q --tb=short
node --test tests/branding_frontend.test.cjs
node --check channel/web/static/js/console.js
openspec validate add-branding-settings --type change --strict
```

Python：77 passed。Node：7 passed。JavaScript 语法检查与 OpenSpec 严格验证通过。

浏览器使用实际 `build_web_app()`、实际登录及 Cookie/CSRF 路由，运行于独立临时数据目录。已验证：菜单与表单、未保存确认、图片上传后使用默认 Logo、上传自定义 PNG、编辑及清空描述、整组保存、图片加载、刷新持久化、取消与确认全量恢复。另通过第二个 API 操作者提交真实更新，验证页面的 409 提示、草稿保留及确认重载。窄屏菜单操作也已检查。本轮没有更改正式实例品牌配置或重启现有 9899 服务；后端修改需在该服务重启后加载。

新增回归位于 [test_branding.py](/Users/jiantan/ai_assistant/cowagent/tests/test_branding.py) 和 [branding_frontend.test.cjs](/Users/jiantan/ai_assistant/cowagent/tests/branding_frontend.test.cjs)。管理员文档已同步身份模式、Cookie CSRF 和损坏恢复行为。

任务 7.1～7.8 已完成。原先过早勾选的 3.6、4.3、5.6 重新置为待验：真实多进程中断、401 重登/响应丢失核对、完整多标签/语言/主题回归仍需补充。阶段 D 6.1～6.4 继续未完成，不将本次缺陷关闭等同于整个 change 发布验收。

## 后续简化：登录后直接编辑

按用户「不需要那么复杂的控制，可以随时修改」的调整，移除品牌启用开关与「品牌功能未开启」提示。旧配置中的 `branding_enabled=false` 不再影响编辑、保存、恢复或公开图片读取；品牌名称、Logo 和描述可在登录后直接修改。

本轮品牌及备份测试 63 passed（另含 2 个配置兼容子场景），前端 7 passed，JavaScript 语法及 OpenSpec 严格检查通过。已重启本地 9899 服务；在实际 Chrome 页面输入名称、描述，确认两字段可编辑、保存按钮启用、未开启提示不存在，再取消测试草稿恢复原值。正式实例品牌值未更改。

任务 8.1～8.2 完成，当前 change 为 37/44；此前保留的其他待验任务未在本轮扩大范围。
