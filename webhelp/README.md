# 容大AI · 产品介绍站点（PHP）

`webhelp/` 是 **容大AI** 的产品介绍网站，使用原生 PHP 静态渲染，**零第三方依赖**（无 Composer、无数据库、无构建步骤）。可直接用 PHP 内置服务器本地预览，也可整目录拷贝到任意支持 PHP 的主机。

站点视觉与内容结构对齐上游 cowagent.ai：本产品基于开源项目 **CowAgent**，并在其助理能力之上增加了多租户、细粒度权限、审计、审批等企业级管控。

> **完全离线自包含**：站点不引用任何外部网络资源。演示视频、部署脚本与全部能力文档的配图均已下载/转存到 `assets/` 本地，页面中不存在任何指向外网站的链接。

## 运行

```bash
# 在仓库根目录执行
php -S 127.0.0.1:8080 -t webhelp

# 打开 http://127.0.0.1:8080
```

要求：PHP 8.0+（开发验证于 PHP 8.5）。

## 页面

| 页面 | 说明 |
| --- | --- |
| `index.php` | 首页：Hero + 演示视频、九项核心能力、企业级管控概览、模型与通道、CTA（部署命令已移至 `quickstart.php`，首页仅保留 Hero 与底部 CTA 两处入口按钮） |
| `features.php` | 核心能力：九项能力详解、内置工具、终端/斜杠命令、FAQ |
| `enterprise.php` | 企业级管控：页头承载权限体系标语与 4 张亮点卡、核心设计原则、三级权限管控架构、资源授权流转机制、关键规则、平台角色与职责、管控链路、九大管控模块、企业管控台、管控保障机制 |
| `quickstart.php` | 快速开始：部署命令（Linux/macOS、Windows、Docker）、配置示例、企业身份初始化、命令速查 |
| `manual.php` | 产品使用手册（应用操作手册）：按「先工作台、后管理控制台」分为三组共 16 章——工作台（开始使用 / 如何对话 / 选择智能体 / 设置知识库 / 待办与定时任务）、管理控制台（创建与配置智能体 / 记忆管理 / 模型服务 / 消息渠道 / 权限与角色设置 / 成员与组织 / 租户与审计）、个人与参考（个人账号设置 / 命令速查 / 故障排查 / 深入阅读）；按「界面字段怎么填、步骤怎么走」组织，原理细节深链既有能力文档 |
| `architecture.php` | 系统架构：五层结构、一次请求的流转、设计原则 |
| `doc.php` | 能力文档阅读页：`?p=<slug>` 渲染本地化能力文档（正文片段见 `docs/`） |
| `about.php` | 关于：项目理念、与上游 CowAgent 的关系、免责声明 |

> 导航栏保留 5 项（首页 / 核心能力 / 企业级管控 / 架构 / 关于）；`quickstart.php`、`manual.php` 与 `doc.php` 不在导航内，分别由各页 CTA（以及首页 Hero 的「查看使用手册」按钮）、九张能力卡片进入。首页**不再内嵌部署命令区块**，部署命令只在 `quickstart.php` 出现，首页通过 Hero 按钮与底部 CTA 两处引导过去。

### 产品使用手册

`manual.php` 是面向使用者的**应用操作手册**——讲「怎么用」，不讲「怎么装」（安装部署见 `quickstart.php`）；服务对象是控制台的使用者，重点覆盖如何对话、如何选择与创建智能体、如何设置知识库、如何配置权限等日常操作。

章节顺序即阅读顺序：**先工作台的日常使用，再管理控制台的配置与治理**。手册正文不出现网址——需要指引时写界面路径（如「管理控制台 →『智能体管理』」）或链接站内页面，不写主机名、端口与 URL。

结构分三层，都在 `includes/content.php`：

- `manual_parts`：手册分组（顺序即侧栏分组顺序）：工作台（日常使用）/ 管理控制台（配置与治理）/ 个人与参考。
- `manual_sections`：章节（id、`part` 分组、图标、`blocks` 块清单、引用的文档 slug 与站内页面 id）。**数组顺序即页面与侧栏顺序**；语言包中按 id 索引，与顺序无关。
- `manual_page_links`：章节可引用的站内页面（id → 文件）。

章节由若干「块」组成（`blocks` 里声明块 id，如 `manual_sections.chat.blocks = ['new','compose','attach','session','command']`），块内可有四种内容：编号步骤 `steps`、字段说明 `fields`（`['name' => 界面字段名, 'desc' => 填写口径]`）、排查条目 `items`（`['ask' => 现象, 'answer' => 处理]`）与注意事项 `note`。全部文案在 `lang/*.php` 的 `manual.sections.<章节>.blocks.<块>.*`，与 `content.php` 的 id 一一对应。

同一个主题跨工作台与控制台时按界面归属拆章：例如 `agents`（工作台「智能体」页：浏览与开始对话）与 `agents-admin`（控制台「智能体管理」：创建与配置）是两章。

渲染助手在 `includes/bootstrap.php`：`manual_nav()`（按分组渲染侧栏导航）、`manual_block()`（单块：标题 + 步骤 + 字段 + 注意事项）、`manual_refs()`（章节末尾的文档与页面深链）、`manual_commands()`（复用 `cli_commands` / `slash_commands` 的命令表）、`manual_all_docs()`（深入阅读章的全部文档入口）。

手册只写操作指引，不复制既有文档正文：章节末尾的「相关文档 / 相关页面」由 slug 经 `doc_exists()` 过滤后渲染，因此文档增删不会产生死链；命令速查复用 `content.php` 的既有命令清单，不维护第二份。入口为首页 Hero 主按钮（文案键 `cta.manual`，与其它页面的 `cta.primary` 相互独立）。

**改手册内容**：在 `content.php` 增删章节或块，再到 `lang/zh.php` 与 `lang/en.php` 补齐同名 id 的文案即可，无需改 HTML。改完跑一次 `php tools/check-manual.php` 校验「结构 ↔ 双语文案」是否对齐（缺章节、缺块、空块、未登记 slug 会直接报错）。

### 本地能力文档

`features.php` 与首页的九张能力卡片均可点击，指向 `doc.php?p=<slug>`。九个 slug 与能力一一对应：

| 卡片 | slug | 正文来源 |
| --- | --- | --- |
| 任务规划 | `architecture` | 项目架构 |
| 长期记忆 | `memory` | 长期记忆 |
| 本地知识库 | `knowledge` | 个人知识库 |
| 技能系统 | `skills` | 技能概览 |
| 工具系统 | `tools` | 工具概览 |
| 自主进化 | `evolution` | 自主进化 |
| 多模型支持 | `models` | 模型概览 |
| 多通道接入 | `channels` | 微信 |
| 多智能体团队 | `multiagent` | Agent 团队 |

文档集并不止这 9 篇：构建脚本从这 9 个入口出发**沿正文链接做 BFS 发现**，把正文引用到的其它页面也一并本地化，因此正文里的站内链接同样可以点击。当前共 **31 篇**，按上游目录分组：

| 分组 | 篇数 | 内容 |
| --- | --- | --- |
| 架构 | 1 | 项目架构 |
| 记忆 | 3 | 长期记忆、自主进化、梦境蒸馏（Deep Dream） |
| 知识库 | 1 | 个人知识库 |
| 技能 | 3 | 技能概览、创造技能、安装技能 |
| 工具 | 4 | 工具概览、委派、定时任务、子 Agent |
| 模型 | 14 | 模型概览 + Claude / OpenAI / Gemini / DeepSeek / Qwen / GLM / Kimi / MiniMax / 豆包 / 千帆 / MiMo / LinkAI / 自定义 |
| 通道 | 2 | 微信、Web 控制台 |
| 多智能体 | 2 | Agent 团队、子 Agent |
| 命令 | 1 | 技能管理 CLI |

正文是上游官方文档的**本地化归档**：构建阶段已剥离全部站外链接（外链解包为纯文本）、转存全部配图，并保留标题锚点供页内目录跳转。因此阅读过程完全离线。文档清单（标题、分组、顺序、导语）由构建脚本写入 `docs/manifest.php`，供 `doc_registry()` / `doc_grouped()` 读取，页面不生成任何外链。

## 目录结构

```
webhelp/
├── index.php / features.php / enterprise.php / quickstart.php / manual.php / architecture.php / about.php
├── doc.php             # 能力文档阅读页（?p=<slug>，slug 白名单校验）
├── tools/
│   ├── build-docs.php  # 文档本地化构建脚本（开发期联网工具，非站点运行时）
│   └── check-manual.php # 手册结构与双语文案一致性校验（改手册后运行）
├── includes/
│   ├── bootstrap.php   # 统一入口：配置、语言、内容、视图辅助函数 e()/t()/url()/asset()/icon()
│   ├── config.php      # 品牌、站点地址、导航、部署命令、配置示例
│   ├── content.php     # 结构化内容（顺序、图标、命令、能力卡片的 doc 指向）
│   ├── i18n.php        # 语言解析与点号取值（t() / t_list()）
│   ├── icons.php       # 内联 SVG 图标集（核心能力图标沿用上游路径）
│   ├── header.php      # <head>、固定导航、主题与语言切换
│   └── footer.php      # 页脚（一行布局：品牌标识靠左下角，版权行靠右）
├── docs/               # 构建产物：31 篇净化后的正文片段 + manifest.php（清单）
├── lang/{zh,en}.php    # 双语语言包（结构必须一一对应）
└── assets/
    ├── css / js / img  # 样式、交互脚本与品牌/上游素材
    ├── img/docs/       # 文档配图（构建时转存并缩放到最大宽 1600px）
    ├── video/          # 本地演示视频
    └── deploy/         # 本地部署脚本（run.sh / run.ps1 / docker-compose.yml）
```

## 设计基线

深色为默认主题，令牌与上游保持一致，集中在 `assets/css/style.css` 顶部：

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--color-primary` | `#4abe6e` | 主色（按钮、图标、强调） |
| `--color-bg` | `#0a0a0a` | 页面背景 |
| `--color-bg-card` | `#161616` | 卡片背景 |
| `--color-border` | `#222222` | 分隔线 / 卡片描边 |
| `--color-text` / `--color-text-secondary` | `#e5e5e5` / `#999999` | 正文 / 次要文本 |
| `--max-width` | `1100px` | 内容最大宽度 |

浅色主题通过 `[data-theme="light"]` 覆盖同名令牌实现。

## 双语与主题

- **语言**：通过 `?lang=zh` / `?lang=en` 切换，选择写入 cookie（`webhelp_lang`）全站生效；语言参数经白名单校验，非法值回退中文。站内链接由 `url()` 自动携带语言参数。
- **主题**：站点默认深色（与上游一致），右上角可切换浅色 / 深色，选择保存在 `localStorage`（`webhelp-theme`）；`<head>` 内联脚本在样式生效前应用主题，避免首屏闪烁。
- **无障碍**：提供跳到主要内容链接、语义化标签、`aria-*` 属性，并支持 `prefers-reduced-motion`。

## 素材说明

- `assets/img/cow-logo.png`：上游 CowAgent 官方 logo，用于**关于页**「与上游项目的关系」区块的署名，属上游项目素材。
- `assets/img/rongda-ai-logo{,-dark}.svg`：容大AI 品牌字标，深色 / 浅色两版按主题自动切换。
- `assets/video/cow-demo-zh-v1.mp4`：演示视频，**已下载到本地**（约 37MB）。上游英文版资源已 404，故中英文站点共用该视频。
- `assets/deploy/{run.sh,run.ps1,docker-compose.yml}`：部署脚本，**已下载到本地**。
- `assets/img/docs/`：能力文档配图，共 35 张（约 9.4MB），**构建时从上游 CDN 转存到本地**并缩放到最大宽 1600px。

### 站点地址配置

部署脚本需要通过绝对地址访问，因此 `config.php` 提供了 `site_url`：

```php
'site_url' => 'http://YOUR-SITE-DOMAIN',
```

部署时改成实际域名或 IP。部署命令里的 `{site_url}` 占位符会在渲染时由 `resolve_site_url()` 替换为该值。

> 注意：`assets/deploy/` 下的脚本是产品安装器，**运行时本身需要联网**（下载 Python 依赖、PyPI 包、ripgrep 等）——这是安装器的固有行为，与站点是否引用外网无关。

## 二次开发

- **改文案**：编辑 `lang/zh.php` 与 `lang/en.php`，两者结构必须一致（键名一一对应）。
- **改品牌 / 站点地址 / 部署命令 / 配置示例**：编辑 `includes/config.php`。
- **增删能力卡片、企业模块、命令、FAQ**：编辑 `includes/content.php`（结构与图标）+ 两个语言包（文案）；新增能力卡片时补上 `capabilities[].doc`，指向 `docs/manifest.php` 里的某个 slug。
- **改文档分组标题**：编辑 `lang/*.php` 的 `doc.sections.*`（键为上游目录名：`intro` / `memory` / `knowledge` / `skills` / `tools` / `cli` / `models` / `channels` / `multi-agent`）。
- **改样式**：编辑 `assets/css/style.css`，设计令牌集中在文件顶部的 `:root` 与 `[data-theme="light"]`；文档正文排版在「本地能力文档」段落，企业级权限管控区块在「核心功能一」段落，产品使用手册的 `.manual-*` 段在文件末尾。
- **改产品使用手册**：分组在 `includes/content.php` 的 `manual_parts`，章节在 `manual_sections`（id、`part`、图标、`blocks` 块清单、`docs` / `links` 深链，**数组顺序即页面顺序**），块内文案在 `lang/*.php` 的 `manual.sections.<章节>.blocks.<块>`（`title` / `steps` / `fields` / `items` / `note`），分组与章节名在 `manual.parts.<id>` 与 `manual.sections.<id>.{nav,title,goal}`。增删章节或块只改 `content.php` 与两个语言包，无需动 HTML；`fields` 是「界面字段名 + 填写口径」的二元组，用于讲清表单怎么填。手册只写操作指引，原理细节一律深链既有文档，不要在手册里复制文档正文；正文不写网址（主机名、端口、URL 一律不出现），要指路就写界面路径或链站内页面。改完运行 `php tools/check-manual.php`。
- **改权限管控区块**：结构在 `includes/content.php` 的 `perm_*` 键（`perm_principles` 四张原则卡、`perm_tiers` 三级架构、`perm_roles` 三类角色）；文案在 `lang/*.php` 的 `perm.*`。渲染由四个助手完成——`perm_stats()`（概览亮点卡）、`perm_tiers()`（三张并列卡 + 连接线）、`perm_flow_panel()`（深色授权流转面板）、`perm_roles()`（头部 + 三列短要点）。增删层级、原则或角色只需改 `content.php` 的条目并在 `lang/*.php` 补齐同名 id 的文案，无需改 HTML。
  - **区块概要提升为页头**：`perm.title` / `perm.lead` 与 `perm_stats()` 通过 `page_hero()` 的 `$opts` 注入页头，页内不再有独立的区块大标题（避免与页头重复）。`page_hero()` 的 `$opts` 支持 `breadcrumb`（面包屑末项，默认同标题；**传空字符串则不渲染面包屑**）、`eyebrow` + `eyebrow_icon`、`title_accent`（标题高亮词，语言键）、`extra`（导语下方的追加 HTML）；留空 `extra` 时不加 `page-hero--rich`，其余内页的页头不受影响。企业级管控页的页头**不使用面包屑与眉标**，标语直接作为首个元素；若日后要恢复「核心功能一 · 企业级权限管控」眉标，在 `lang/*.php` 的 `perm` 下加回 `eyebrow` 键并在 `page_hero()` 调用里补 `eyebrow` / `eyebrow_icon` 即可（`.perm-eyebrow` 样式仍在）。
  - **页头标题（标语）**：`perm.title` 就是标语，用 `\n` 分行渲染为 `<br>`（如 `"分级可控的\n企业级资源权限体系"`）；`perm.title_accent` 指定其中要高亮为品牌绿渐变的词（`hero_title_html()` 会包一层 `.hero-accent`，找不到该子串时按普通文本渲染）。英文分行时记得在上一行末尾留一个空格，否则两行拼起来会粘成一个词。
  - 页头内的亮点卡、以及各区块标题的层级是 h1（页头）→ h2（区块）→ h3 → h4；调整标题标签时请保持不跳级。
  - **CSS 规则顺序**：`.page-hero--rich` / `.hero-accent` 的基础规则必须留在 `.page-hero` 附近（约 520 行），**不要**搬进文末的权限区块段——那里位于各 `@media` 覆盖之后，同特异度的基础规则会反过来盖掉响应式字号。
  - **核心设计原则（2×2 大卡）**：`.perm-principles` 是两列网格，配 `grid-auto-rows: 1fr` 让四张卡等高（中英文行数不同时也不会一高一矮）。卡片用 `:nth-child(even)` 交错两档绿调底纹与边框（奇数字绿边框 + 重底纹、偶数字中性边框 + 近乎素底），呼应参考稿的交替节奏；色相仍锁在品牌绿，不引入蓝／青。字号与内边距见 `.perm-principle` / `-title` / `-desc`，≤560px 收成单列并下调字号。
  - 概览亮点（`perm.stats`）与关键规则（`perm.rules`）是 `lang/*.php` 中的「映射列表」（每项含 `value`/`label` 或 `title`/`desc`），用 `t_list()` 读取——注意 `t_list()` 会丢弃键名，需要保留键名时用 `t_map()`。
  - 深色流转面板的底色由 CSS 变量控制：`--perm-panel-from` / `--perm-panel-to` / `--perm-panel-border`，在 `:root`（深色主题）与 `[data-theme="light"]` 中各定义一份。面板在两种主题下都是深色，因此内部文字固定用白色，不要改用主题文字色。

### 同步能力文档

`docs/*.html` 与 `docs/manifest.php` 由脚本生成，**不要手改**。需要同步上游更新时：

```bash
php tools/build-docs.php
```

脚本行为要点：

- 从 9 个能力入口出发，沿正文里的 `/zh/` 站内链接 **BFS 发现**全部可达页面（默认上限 120 页 / 4 层），当前得到 31 篇。
- 每篇抓取上游中文正文 → 抽取 `#content` → 只保留安全标签与属性（剥离 `class`/`style`/事件属性）。
- 站外链接与未本地化的站内链接**解包为纯文本**；已本地化的站内链接改写成 `doc.php?p=<slug>`，所以正文里的链接可以继续点击。
- 图片转存到 `assets/img/docs/`，按「slug + 源地址哈希」命名；已存在的图片直接复用，因此重新构建不会重复下载，未被引用的旧图在成功后清理。
- jsdelivr 主域偶发 301/超时，脚本会自动回退到 `gcore` / `testingcf` 镜像。
- 抓取失败会重试（404/410 除外）：发现阶段遇到 404 视为**上游死链**直接跳过（不纳入文档集）；抓取阶段若某篇仍失败，或某张图片既抓不到、本地也没有留存，脚本以退出码 1 中止且**不写入任何片段**，避免一次网络抖动把已有文档静默抹掉。
- `docs/manifest.php` 记录每篇的 slug / 分组 / 顺序 / 中英标题 / 导语 / 上游来源路径；页面只读取它，不发起任何请求。
- `$aliases` 表可登记「上游已 404 但正文仍在引用」的死链，映射到存活的等价页面，让这些链接不至于退化为纯文本。

> 这是本目录中**唯一需要联网**的组件，且只在手动执行时联网；站点运行时（所有页面）不产生任何外部请求。

企业级管控页的文案取自本仓库 `openspec/specs/` 下的规范（`rbac-authorization`、`tenant-management`、`audit-log`、`action-approval`、`credential-management`、`resource-quota`、`execution-isolation`、`enterprise-access-enforcement`、`user-membership` 等）；调整口径时请同步对应 spec。

## 安全说明

这是纯展示型站点：不接受用户输入、不写文件、不连数据库。所有输出统一经 `e()`（`htmlspecialchars`）转义；仅 `lang` 查询参数会被读取且经白名单校验。

`doc.php` 的 `?p=` 参数同样经**白名单校验**（仅接受 `docs/manifest.php` 中登记过的 slug，非法值返回 404），因此不存在路径穿越读取任意文件的风险。`docs/*.html` 是构建期产物，已剥离脚本、样式、事件属性与全部站外引用；`doc_body()` 仅把它们作为可信静态片段输出。

**站点不引用任何外部网络资源**，适合部署在无外网的内网环境。唯一会联网的是开发期构建脚本 `tools/build-docs.php`，它不在运行时被调用。如需新增外链，请先确认部署环境的网络策略。

## 与主项目的关系

本目录为独立静态站点，不影响主项目运行（Python 服务不包含这些页面）。助理能力文案与上游 CowAgent 一致，品牌信息取自 `branding/branding.json`，企业能力口径以 `openspec/specs/` 为准。
