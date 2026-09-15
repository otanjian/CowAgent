# Design — retarget-upstream-links-to-rsm

## 1. 当前代码接入点

改动前，指向上游 `cowagent.ai` 的对外引用分为两类，分布如下（完整清单见 `evidence/1-1-inventory-and-execution.md`）：

| 类别 | 接入点 | 改动前目标 | 语言/路径派生 |
| --- | --- | --- | --- |
| 用户可见外链 | `channel/web/chat.html`（技能管理「探索技能广场」） | `https://skills.cowagent.ai/` | 无 |
| 用户可见外链 | `channel/web/static/js/console.js`（侧栏 `docs-link`） | `https://docs.cowagent.ai[/zh]` | 按 `currentLang === 'zh'` 派生 |
| 用户可见外链 | `desktop/src/renderer/src/pages/SkillsPage.tsx`（`SKILL_HUB_URL`） | `https://skills.cowagent.ai/` | 无 |
| 用户可见外链 | `desktop/src/renderer/src/layout/NavRail.tsx`（`SKILL_HUB_URL`、`websiteUrl()`、`docsUrl()`） | `https://skills.cowagent.ai/`、`https://cowagent.ai[/zh]`、`https://docs.cowagent.ai[/zh]` | 按 `getLang() === 'zh'` 派生 |
| 用户可见外链 | `desktop/src/main/menu.ts`（原生菜单 `SKILL_HUB_URL`、`DOCS_URL`） | `https://skills.cowagent.ai/`、`https://docs.cowagent.ai` | 无 |
| 用户可见外链 | `desktop/src/renderer/src/components/UpdateBanner.tsx` | `https://docs.cowagent.ai[/zh]` | 按语言派生 |
| 用户可见外链 | `cli/commands/skill.py`、`plugins/cow_cli/cow_cli.py`（输出提示） | `https://skills.cowagent.ai` | 无 |
| 用户可见外链 | `.github/ISSUE_TEMPLATE/config.yml`（文档入口） | `https://docs.cowagent.ai` | 无 |
| 用户可见外链 | `docs/docs.json`（站点导航）、`docs/**` 发布说明与技能/安装/桌面/升级页 | `https://cowagent.ai/*`、`https://docs.cowagent.ai/*`、`https://skills.cowagent.ai/*` | 简繁英三套各自派生 |
| 功能端点 | `cli/utils.py` `SKILL_HUB_API` | `https://skills.cowagent.ai/api` | 无 |
| 功能端点 | `desktop/src/main/updater.ts` `FEED_BASE`（+`?lang=zh`、`/legacy/`） | `https://cowagent.ai/update/` | 追加查询串与路径段 |
| 功能端点 | `desktop/package.json` `build.publish.url` | `https://cowagent.ai/update/` | 无 |
| 功能端点 | `webhelp/tools/build-docs.php`（抓取基址，拼接 `$path`） | `https://docs.cowagent.ai` | 拼接路径 |
| 功能端点 | `channel/feishu/lark_install.py`（第 2 个镜像） | `https://cdn.cowagent.ai/desktop/vendor/feishu-vendor-{version}.zip` | 模板变量 |
| 功能端点 | `docs/intro/index.mdx`、`docs/ja/intro/index.mdx`（`<video src>`） | `https://cdn.cowagent.ai/desktop/video/cow-demo-en-v1.mp4` | 无 |

既有反向先例：`channel/web/chat.html` 的 `sidebar-version`、`openAccountAbout()` 复用的品牌版本入口，以及 `tests/test_sidebar_account_frontend.cjs` 的 fixture，改动前已指向 `https://www.rsm.global/china/zh-hans`。本次改动是把同一口径扩展到其余全部上游外链，而不是新引入一个目标地址。

## 2. 决策

### D1：目标地址统一取运营方对外地址，不保留原路径与语言后缀

已确认口径为「一律拉平」。取该口径的理由是外链语义从「去上游文档站某页」变为「去运营方站点」，`rsm.global` 上不存在与 `docs.cowagent.ai/zh/guide/upgrade` 对应的页面，保留路径只会得到 404 而不会得到等价内容；语言后缀同理，运营方地址本身即中文（`/china/zh-hans`），派生 `/zh`、`/ja`、`/en` 没有目标页面。

代价是**丢失按主题定位的能力**：改动前「桌面客户端文档」「升级指南」「技能广场提交页」各自可达对应页面，改动后全部落到同一个入口页，用户需要自行在运营方站点内找到对应内容。该代价是本次口径的直接结果，记录于此以便后续决定是否改为「按主题映射到运营方站点的对应页面」。

**字面量与运行时拼接的区分（已决定不在本 change 范围）**：本次替换只覆盖**字面量**。代码中仍有 6 处在运行期把后缀拼到该地址之后：

| 位置 | 拼接方式 | 运行期实际目标 |
| --- | --- | --- |
| `desktop/src/main/updater.ts:52` | `+ (isLegacyWindows() ? 'legacy/' : '')` | `.../china/zh-hans/legacy/` |
| `desktop/src/main/updater.ts:55` | `` `${FEED_BASE}?lang=zh` `` | `.../china/zh-hans?lang=zh`（legacy 时为 `.../legacy/?lang=zh`） |
| `desktop/src/renderer/src/components/UpdateBanner.tsx:15` | `` `${base}/releases/v${version}` `` | `.../china/zh-hans/releases/v<版本>` |
| `webhelp/tools/build-docs.php:279`、`:508` | `'…zh-hans' . $path` | `.../china/zh-hans/<页面路径>` |
| `webhelp/tools/build-docs.php:532` | `'…zh-hans' . substr($path, 3)` | `.../china/zh-hans/<英文页面路径>` |

**决策**：这 6 处拼接**保持现状**，不在本 change 范围内（已确认「拼接的不管」）。`upstream-link-retargeting` 规格据此只约束**配置与源码中的目标取值**（字面量、常量、配置项），不声明运行期拼接约束——否则归档后的基线会要求一件代码故意不做的事。`legacy/` 与 `?lang=zh` 对上游更新服务有语义（前者取 win-legacy 发布、后者 302 到国内镜像），消除它们属于更新机制改造，与本 change 的引用目标收敛是两件事。

### D2：跳转链接与功能端点分别处置

跳转链接的目标是展示，功能端点的目标是取数据。两者被同一批替换覆盖，但后果不同：跳转链接改变的是用户看到什么，功能端点改变的是能力是否还能工作。因此规格把两者分开声明，并要求端点收敛后以**明确失败**呈现，而不是让能力静默降级。

### D3：功能端点的失败必须先显式化，且该缺口尚未实现

按 D2，端点收敛后的正确行为是明确失败。**当前实现并非如此**：`desktop/src/main/updater.ts` 仍保留改动前「两个来源互相回退」的尝试逻辑（`feedUrlFor(china)` 在两个取值间切换），`cli` 侧也未新增端点不可用的显式提示。即规格第 2 条（功能端点收敛后果显式）在本 change 内**未实现**，只在任务中列出。

这里不把「已替换端点」当作「已满足该要求」：替换是本次已落地的事实，失败显式化是它的后续义务。在任务 3 完成前，该要求视为未通过。

### D4：目标地址应经接缝承载，而非就地字面量

本次替换按范围要求就地改写了上游核心文件中的字面量，共触及 86 个受版本控制文件。这带来两个成本：一是与 `fork-upstream-decoupling` 对「定制逻辑位于稳定接缝」的要求存在张力；二是上游后续修改同一批文件时会产生文本冲突。

已存在的接缝优先复用：`desktop/src/main/updater.ts` 已有 `loadAppConfig()?.updateFeedUrl` 配置项入口，语义上本可直接承载该地址，无需改写 `FEED_BASE` 字面量。后续阶段的方案是把各产品面的对外目标收敛为单一配置来源（实例配置项或共享常量模块），字面量只作缺省值。本次不引入与该接缝并列的第二套配置。

### D5：显示文字与来源说明不随目标改名

`docs/**` 中 19 处 Markdown 链接的**显示文字**（`[skills.cowagent.ai/submit](…)`）、`webhelp/**` 中 6 处正文与注释的来源说明（如「视觉基线对齐上游 cowagent.ai/zh/」）保持原样。理由是这些文字描述的是**来源**（上游项目视作基线），不是指向目标；把文字一并改成运营方地址会让文档声称运营方发布了从未发布的版本与页面，属于造假。规格据此要求「仅作说明的域名提及保持原意」。

同理，`api.link-ai.tech`、`cdn.link-ai.tech`、`link-ai.tech` 是模型与渠道的功能接口域名，`github.com/zhayujie/CowAgent` 是源码归属地址，均不属对外引用目标，不在本能力范围。

### D6：构建产物不在本 change 的交付物内

`desktop/dist/**` 是构建产物且被 `.gitignore` 排除，改动后仍含旧目标（4 个文件）。桌面端改动需重新构建打包才生效，该步骤列为任务；本 change 不提交构建产物。

## 3. 已知影响与回退

| 能力 | 收敛后后果 | 是否仍可用 |
| --- | --- | --- |
| 技能广场在线安装 / 远程列表 | `SKILL_HUB_API` 不再可达上游服务 | 否 |
| 桌面端自动更新 / 发布目标 | 更新源与 `publish.url` 不再可达上游更新服务 | 否 |
| 文档站点构建抓取 | 抓取基址不再返回各路径页面 | 否（构建阶段，不影响运行时） |
| 演示素材播放 | `<video src>` 不再返回视频 | 否 |
| 飞书依赖包下载 | 第一个镜像 `cdn.link-ai.tech` 未改动 | 是（逐镜像尝试） |

回退方式为按 `evidence/1-1-inventory-and-execution.md` 记录的文件清单整体还原：替换是纯文本的同形改动，不涉及数据、配置迁移或资源重建，还原后能力与改动前一致。

## 4. 未决实施参数

- 功能端点是否恢复为可用地址（保持运营方站点外链，同时为端点保留可用上游服务或替换为运营方托管服务），由任务 4.1 决定；在决定前端点维持当前已替换状态并需要失败显式化。
- 接缝形态（实例配置项 vs 共享常量模块）与缺省值取值，由任务 4.2 在实施接缝化时确定，本设计只规定「单一来源、复用既有接缝、不并列第二套配置」。
- 是否需要按主题把外链映射到运营方站点的对应页面（D1 的代价），由任务 4.3 评估。
