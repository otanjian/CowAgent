## Why

控制台账号菜单的「帮助与关于」当前复用底部品牌版本行（`#sidebar-version`）的目标，两者指向同一个硬编码的运营方外部地址（`https://www.rsm.global/china/zh-hans`）。这使「帮助与关于」既不是本项目的帮助入口，也无法随部署环境变化：同一份构建在内网、本机与公网部署时会跳到同一个与当前实例无关的地址，用户拿不到本实例的产品介绍与使用手册站点。

## What Changes

- **「帮助与关于」改用本项目站点地址作为目标**：目标取产品介绍站点（`webhelp/`，即 `product-manual-site` 能力所描述的站点）在 `webhelp/includes/config.php` 中声明的对外地址 `site_url`；该地址是这一目标的**唯一来源**，不在前端或后端另外硬编码一个并行地址。
- **地址整体可配置，缺省落到本机开发地址**：协议、主机与端口全部来自 `site_url` 取值；当该值缺失、为空、或仍是脚手架占位值（`http://YOUR-SITE-DOMAIN`）时，目标回退为 `http://localhost:8080/`（本机以 `php -S 127.0.0.1:8080 -t webhelp` 提供站点时的地址）。
- **地址以非 http/https 取值拒绝**：`site_url` 取值不是 `http`/`https` 绝对地址时，按「未配置」处理并回退到缺省值；MUST NOT 把任意字符串拼接成可点击目标。
- **底部品牌版本行保持自身目标**：`#sidebar-version`（「容大AI v2.1.7」）继续指向既有运营方对外地址，不再与「帮助与关于」共用同一个目标；两个入口语义分离。
- **读取失败不阻断账号菜单**：站点地址读取失败或返回不可用值时，入口仍可用并落到缺省地址；MUST NOT 因读取失败隐藏入口、抛出脚本错误或臆造其它链接。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `console-entry-consistency`: 「关于产品和更新日志含义一致」要求由「入口复用品牌版本链接」改为「帮助与关于指向本项目站点地址（来源为站点 `site_url`，缺省 `http://localhost:8080/`），品牌版本行保留自身目标」，并补充读取失败回退与协议校验场景。
- `product-manual-site`: 新增要求，明确站点对外地址 `site_url` 是产品内「帮助与关于」目标的唯一来源，站点 SHALL 让该取值可被产品读取、可区分「未配置」与「已配置」两种状态。

## Impact

- 后端：`channel/web/`（新增站点地址解析模块）与公开版本接口 `/api/version`（追加站点地址字段，接口从「仅版本号」扩展为「版本号 + 帮助站点地址」）。
- 前端：`channel/web/static/js/console.js`（`openAccountAbout()` 不再复用 `#sidebar-version`）、`channel/web/chat.html`（账号菜单「帮助与关于」不再与版本行共享目标）。
- 配置：`webhelp/includes/config.php` 的 `site_url` 取值成为该入口目标的唯一来源；新增部署形态需要正确填写该值。
- 依赖：无新增第三方依赖；不新增 HTTP 路由。
- 兼容：`/api/version` 为追加字段，既有消费者（桌面端、浏览器测试 fixture）不受影响。
