## Context

动机见 `proposal.md`。当前实现的相关接入点：

| 位置 | 现状 |
| --- | --- |
| `channel/web/chat.html:464` | `#account-menu-about` 是 `<button onclick="openAccountAbout()">`，本身不携带目标地址 |
| `channel/web/chat.html:474` | `#sidebar-version` 锚点的 `href` 为 `https://www.rsm.global/china/zh-hans`（品牌版本行） |
| `channel/web/static/js/console.js:19504` | `openAccountAbout()` 读取 `#sidebar-version` 的 `href` 并用 `window.open` 新标签打开——两个入口共用同一目标的原因 |
| `channel/web/static/js/console.js:4982` | `fetchPublicBrand()` 在启动与 `visibilitychange` 时读取公开品牌快照 `/api/branding/public` |
| `channel/web/web_channel.py:4890` | `BrandingPublicHandler`（路由来源 `fork:branding`）返回品牌公开投影；异常时回退到内置缺省 dict |
| `webhelp/includes/config.php:25` | 站点配置 `'site_url' => 'http://YOUR-SITE-DOMAIN'`，PHP 侧 `site_url()` 以 `rtrim(..., '/')` 消费该值 |

约束（来自既有规范）：`fork-upstream-decoupling` 要求 fork 定制逻辑走接缝，MUST NOT 在上游核心文件就地改写；`console-route-lifecycle` 与 `fork-upstream-decoupling` 要求路由与策略由权威清单派生，新增路由需同步登记策略与路由基线。

## Goals / Non-Goals

**Goals:**

- 「帮助与关于」的目标来自站点声明地址，单一来源、可随部署变化，缺省落到 `http://localhost:8080/`。
- 该取值不是合法 http/https 绝对地址时按「未配置」处理，不产生可点击的非法目标。
- 读取失败不阻断账号菜单，两个入口语义解耦（版本行保持自身目标）。

**Non-Goals:**

- 不改动 `webhelp/` 站点自身的渲染、导航与 `site_url` 在部署命令中的既有用法。
- 不改动桌面端（桌面端账号菜单无「帮助与关于」入口）。
- 不把该地址做成租户级或用户级可配置项（它是实例级事实）。
- 不新增 HTTP 路由，不改上游 `/api/version` 的响应形状。

## Decisions

### D1：目标地址取站点配置的 `site_url`，不新增并行配置

站点对外地址是站点自己的能力（见 `product-manual-site` 的 delta），产品侧不引入第二个配置键。备选方案（在 `config.json` 新增键、从浏览器 `location` 推导、取控制台 `web_host`/`web_port`）被否：前两者会把同一事实变成两处维护，第三者取到的是控制台端口而非站点端口，与「站点地址」语义不符。

### D2：`site_url` 只在服务端解析，前端只消费最终地址

新增 `channel/web/help_site.py`：读取 `webhelp/includes/config.php`，抽取 `site_url` 字面量，校验并归一化，返回**最终可点击地址**。判断规则集中在一处：

- 文件缺失 / 键缺失 / 取值为空 → 未配置；
- 取值含脚手架占位主机（`your-site-domain`，大小写无关）→ 未配置；
- 取值解析后 scheme 不是 `http`/`https`、或没有主机名 → 未配置；
- 其余 → 归一化为 `scheme://host[:port][/path]/`，丢弃 query 与 fragment（基址不应携带）。

未配置时返回缺省值 `http://localhost:8080/`。前端不再重复实现同一套判断，只保留一个可用的静态回退常量，避免「服务端说一套、前端猜一套」。

### D3：经公开品牌快照 `/api/branding/public` 追加 `help_url` 传输

该路由来源为 `fork:branding`，是 fork 拥有的公开面，且控制台已在启动与 `visibilitychange` 时读取它，追加一个字段即可，无需新路由、无需改 `route_registry` 与路由基线。备选方案（上游 `/api/version`、新建 `/api/site-info`）被否：前者违反「不在上游核心文件内叠加 fork 定制」，后者为一个只读事实引入新路由并需要同步策略表、路由基线与覆盖不变量测试，成本不成比例。

注入点在 handler（`BrandingPublicHandler.GET`）而非品牌存储服务：品牌存储只负责品牌快照，站点地址与品牌快照无关，handler 负责组装公开投影，因此两者都不越界。异常回退分支同样补上该字段，保持响应形状稳定。

### D4：前端把「帮助与关于」与品牌版本行解耦

`console.js` 新增模块级变量 `ACCOUNT_ABOUT_URL`，初值为缺省地址；`fetchPublicBrand()` 拿到合法 `help_url` 时更新它（与服务端同样只接受 http/https 绝对地址），`openAccountAbout()` 改为打开该变量所指地址，不再读 `#sidebar-version`。`chat.html` 中 `#sidebar-version` 的 `href` 保持不变，版本行行为不变。

### D5：不缓存解析结果

每次调用重新读取站点配置。配置文件极小，读取成本可忽略；不缓存换来「改站点地址后下一次页面加载即生效、无需重启控制台」，代价是每次控制台启动多一次文件读取。

## Risks / Trade-offs

- [站点站点配置与产品部署在同一台机器之外时无法读取] → 读取失败按「未配置」处理并落到缺省地址，入口仍可用；部署说明中登记「站点地址需与被部署的产品同机可读」，不在本 change 内引入远程获取。
- [占位值识别依赖 `your-site-domain` 字面量] → 识别规则写在单一模块并配单测；若脚手架占位值将来改名，只改一处常量并同步测试，不需要改前端。
- [域名校验放宽会引入开放重定向风险] → 目标只用于新标签打开，不参与服务端跳转；仍限制为 http/https 绝对地址并丢弃 query/fragment，避免 `javascript:` 等非期望 scheme。
- [`/api/branding/public` 的消费者（桌面端、浏览器测试 fixture）依赖字段集合] → 本次是纯追加字段，既有字段不动；受影响 fixture 只在断言了精确键集合时才需要更新，任务中显式核对。
