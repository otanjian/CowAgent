## 1. 前置与基线（G0）

- [x] 1.1 记录改动前基线：`#account-menu-about` 与 `#sidebar-version` 共用同一目标（`openAccountAbout()` 读取 `#sidebar-version.href`），站点配置 `webhelp/includes/config.php` 的 `site_url` 仍是占位值 `http://YOUR-SITE-DOMAIN`，写入 `evidence/1-baseline.md`。
- [x] 1.2 在 `product-manual-site` 的 delta 中确认 `site_url` 作为唯一来源的语义；核对站点自身对 `site_url` 的既有消费（`webhelp/includes/bootstrap.php` 的 `site_url()` / `resolve_site_url()`）不因本 change 改变。

## 2. 站点地址解析模块（`channel/web/help_site.py`）

- [x] 2.1 新增模块，暴露站点配置路径、`site_url` 抽取与最终地址解析三件事；对外只返回最终可点击地址，缺省 `http://localhost:8080/`。
- [x] 2.2 实现「未配置」判定：文件/键缺失、空值、脚手架占位主机（`your-site-domain`，大小写无关）。
- [x] 2.3 实现取值校验与归一化：仅接受 http/https 绝对地址、必须有主机名、丢弃 query 与 fragment、补尾斜杠（保留子目录部署路径）。
- [x] 2.4 单测覆盖：已配置（含子目录与显式端口）、文件缺失、键缺失、空值、占位值、非 http/https、无主机名、非法字符串；断言解析不抛异常且始终返回可用地址。

## 3. 公开投影传输（`channel/web/web_channel.py`）

- [x] 3.1 `BrandingPublicHandler.GET` 在成功路径的公开品牌投影上追加 `help_url`（取模块解析结果），既有字段与取值不变。
- [x] 3.2 异常回退分支同样追加 `help_url`，保持响应形状一致；确认回退分支不因解析失败而额外抛错。
- [x] 3.3 单测断言：`help_url` 出现在成功与回退两种响应中；解析异常时仍为缺省地址；既有字段集合不减少。

## 4. 前端接线（`channel/web/static/js/console.js`、`channel/web/chat.html`）

- [x] 4.1 新增模块级 `ACCOUNT_ABOUT_URL`，初值为缺省地址 `http://localhost:8080/`。
- [x] 4.2 `fetchPublicBrand()` 在拿到合法 `help_url` 时更新该变量；非法值/缺失时保留既有值，不打断品牌应用流程。
- [x] 4.3 `openAccountAbout()` 改为打开该变量所指地址，不再读取 `#sidebar-version`；保持「先关闭账号菜单、再新标签打开、随后复位面板」的既有行为。
- [x] 4.4 核对 `chat.html`：`#account-menu-about` 仍是同一按钮与文案，`#sidebar-version` 的 `href` 保持 `https://www.rsm.global/china/zh-hans`，账号菜单区域顺序不变。

## 5. 回归与兼容

- [x] 5.1 扩展 `tests/test_sidebar_account_frontend.cjs`：断言「帮助与关于」打开站点地址而非版本行地址、缺省与非法 `help_url` 两种回退、版本行自身目标不变；确认该文件既有断言（区域顺序、版本标签、焦点恢复）全部通过。
- [x] 5.2 核对受影响的前端 fixture 与浏览器测试（`tests/test_personal_console_browser.cjs`、`tests/test_appearance_browser.cjs` 的 `/api/version`、`/api/branding/public` stub），必要时补齐 `help_url`，确认不因新增字段失败。
- [x] 5.3 运行路由覆盖、策略表与 `scripts/route-baseline.txt` 相关回归，确认本 change 未新增路由、未改变既有路由策略。
- [x] 5.4 核对桌面端不消费该字段：确认 `/api/branding/public` 的桌面端调用点与类型定义不受追加字段影响。

## 6. 验收证据与交付

- [x] 6.1 以本机 `php -S 127.0.0.1:8080 -t webhelp` 场景验收：站点 `site_url` 未配置时「帮助与关于」打开 `http://localhost:8080/`；将其改为合法地址后下一次加载跟随新地址（写入 `evidence/`）。
- [x] 6.2 验收「版本行仍指向运营方地址」与「读取失败不阻断账号菜单」两个场景，记录实际观察结果。
- [x] 6.3 `openspec validate help-about-project-site-link --strict` 通过，且 spec delta 与 `openspec/specs/` 既有 requirement 名称一致。
