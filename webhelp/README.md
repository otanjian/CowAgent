# 容大 AI 帮助站

帮助站与主控制台共用 **Python + web.py** 服务，通过 **`/help/`** 访问。页面使用 web.py HTML 模板、原生 JavaScript 和现有 CSS；内容、配置与中英文文案使用 JSON。无需 PHP、额外端口、Node 构建或独立数据库。

## 启动与访问

按项目原有方式启动主服务，并启用 `web` 渠道。例如在仓库根目录执行：

```bash
.venv/bin/python app.py
```

主服务使用默认端口时，访问：

- 首页：`http://127.0.0.1:9899/help/`
- 使用手册：`http://127.0.0.1:9899/help/manual`
- 核心能力：`http://127.0.0.1:9899/help/features`
- 企业级管控：`http://127.0.0.1:9899/help/enterprise`
- 快速开始：`http://127.0.0.1:9899/help/quickstart`
- 系统架构：`http://127.0.0.1:9899/help/architecture`
- 关于：`http://127.0.0.1:9899/help/about`
- 能力文档：`http://127.0.0.1:9899/help/doc?p=memory`

`/help` 会跳转到 `/help/`；`/help/index.php`、`/help/doc.php?p=...` 等迁移前页面名会重定向到对应的新地址。原独立 PHP 域名若仍有访问者，需要由部署方将其重定向到主服务的 `/help/`。

主控制台“帮助与关于”固定打开同源 `/help/`，不会受到品牌官网链接或旧独立站地址影响。帮助页及其公共资源允许匿名读取，页面不读取租户数据。

## 目录与维护

| 文件或目录 | 用途 |
| --- | --- |
| `site.py` | 请求级视图、双语解析、链接、文档及组件渲染 |
| `templates/*.html` | 页面模板；使用 web.py 自带模板引擎 |
| `config.json` | 品牌、导航、部署命令与展示配置 |
| `content.json` | 能力卡片、手册主题和步骤等结构数据 |
| `lang/zh.json`、`lang/en.json` | 双语文案 |
| `icons.json` | 原站内联 SVG 图标路径 |
| `docs/manifest.json` | 本地能力文档清单、分组和标题 |
| `docs/*.html` | 已净化的中文文档正文 |
| `assets/` | 原有 CSS、JS、图片、视频与公开部署脚本 |
| `tools/check_manual.py` | 离线检查手册、双语文案、截图和链接 |
| `tools/build_docs.py` | 显式执行时同步文档的开发工具 |

主服务路由与静态资源处理在 `channel/web/help_site.py`，通过 `channel/web/route_registry.py` 注册；不需要修改独立的权限路由表。

`config.json` 的 `site_url` 可留空，快速开始页会自动使用当前主服务的 `/help` 地址构造部署脚本 URL。如果需要指定公开域名，可填写完整帮助站地址，例如 `https://agent.example.com/help`。该配置只影响部署命令，不改变控制台的帮助入口。

语言通过 `?lang=zh` / `?lang=en` 切换，并由 `webhelp_lang` Cookie 记忆；Cookie 限定在 `/help` 路径。文档正文保持原有中文内容，导航与标题支持双语。页面保留深浅色主题、移动导航、代码标签页、复制按钮和手册原图链接。

新增手册步骤时，在 `content.json` 声明步骤 ID 与截图路径，并同步维护两份语言包的 `manual.topics.<主题>.steps.<步骤>.title/body`。截图位于 `assets/img/manual/`，每步一张，文件名前缀与主题 ID 一致。

HTML 模板默认转义普通变量；`$:` 仅用于经过视图转义的组件及已净化的本地文档正文。不要在这里放置用户上传的未经净化的 HTML。

## 校验

在仓库根目录执行：

```bash
.venv/bin/python webhelp/tools/check_manual.py
.venv/bin/python -m pytest tests/test_help_site.py tests/test_help_site_url.py tests/test_route_registry.py -q
node --test tests/test_sidebar_account_frontend.cjs
```

路由测试覆盖中英文页面、全部文档、站内链接与资源、语言 Cookie、旧地址重定向、404、路径穿越及软链接边界、资源缓存与范围读取。

## 文档同步

现有本地文档随仓库发布。站点运行时不会抓取外网。只有维护者显式执行以下命令才同步来源站：

```bash
.venv/bin/python webhelp/tools/build_docs.py --source-url https://docs.example.com
```

将示例地址换成真实文档源的根地址。来源需提供清单中登记的文档路径和 `#content` 正文容器。工具抓取已登记文档及正文中的关联页面，过滤脚本和外链、本地化配图，并生成 JSON 清单。所有页面抓取和校验完成后才替换现有正文；失败不会以空内容覆盖已有文档。

## 打包

Docker 本地构建包含仓库内容；桌面 PyInstaller 配置已加入帮助站模板、JSON、文档及静态资源。发布桌面安装包时需重新构建 Python 后端。修改 Python 或模板后重启主服务生效。
