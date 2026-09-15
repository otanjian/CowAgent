# 证据 1-1：范围清单与替换执行

对应任务：1.1、1.2、1.3、2.1–2.4、3.1–3.3。

## 1. 范围与口径

替换目标统一为 `https://www.rsm.global/china/zh-hans`，地址一律拉平：不保留原路径、查询串、锚点与语言后缀。已确认范围为**含功能端点**。

## 2. 已落地改动

### 2.1 总量

| 指标 | 数值 |
| --- | --- |
| 受版本控制文件被修改 | 86 |
| 改动的行 | 327 |
| 被改写的 URL 目标 | 359 |
| 被 `.gitignore` 排除另改的文件 | 1（`skills/README.md`，2 处） |
| 工作区中指向运营方地址的 URL 合计 | 361（含改动前既有的 2 处同址引用） |

统计方法：`git diff -U0` 的删除行与新增行中分别匹配 `https?://[A-Za-z0-9.-]*cowagent\.ai` 与 `https://www\.rsm\.global/china/zh-hans`，两者计数一致（327 行 / 359 处）。

### 2.2 按产品面

| 产品面 | 文件数 | 改写内容 |
| --- | --- | --- |
| `docs/**` | 69 | `docs/docs.json` 导航项（12 处）、简繁英三套发布说明（v2.0.2–v2.1.7）、技能/安装/桌面/升级/自进化页中的对外引用 |
| `webhelp/**` | 1 | `webhelp/tools/build-docs.php` 抓取基址（3 处，拼接 `$path` 与 `/en` 分支） |
| `cli/**` | 2 | `cli/utils.py` 的 `SKILL_HUB_API`；`cli/commands/skill.py` 的浏览地址提示 |
| `channel/**` | 3 | `chat.html` 技能广场入口；`console.js` 侧栏文档入口（取消 `/zh` 派生）；`feishu/lark_install.py` 第 2 个依赖镜像 |
| `desktop/**` | 6 | `NavRail.tsx`（技能广场/官网/文档，取消 `/zh` 派生）、`SkillsPage.tsx`、`UpdateBanner.tsx`（取消 `/zh` 派生）、`main/menu.ts`、`main/updater.ts`（`FEED_BASE`）、`package.json`（`publish.url`） |
| `models/**` | 1 | `models/linkai/link_ai_bot.py` 顶部文档注释 |
| `plugins/**` | 1 | `plugins/cow_cli/cow_cli.py` 技能列表输出提示（简英两处同串） |
| `.github/**` | 2 | 议题模板 `config.yml` 文档入口；`release-win7.yml` 下载地址日志（含 `${key}` 变量形态） |
| `CONTRIBUTING.md` | 1 | 源码安装文档入口 |

### 2.3 边界形态（已逐一复核被完整替换）

`https://cowagent.ai/?lang=zh`、`https://docs.cowagent.ai/zh/intro/architecture#workspace`、`https://cdn.cowagent.ai/${key}`、`https://cdn.cowagent.ai/desktop/vendor/feishu-vendor-{version}.zip`、`https://docs.cowagent.ai/`（尾部斜杠）、`https://skills.cowagent.ai\n`（行尾）均按「取运营方对外地址」处理，不残留后缀。

## 3. 明确未改动

### 3.1 非目标域名

| 域名 | 类型 | 出现位置（示例） |
| --- | --- | --- |
| `api.link-ai.tech` | LinkAI 模型/渠道功能接口 | `config.py`、`plugins/linkai/*`、`voice/linkai/*`、`models/linkai/*` |
| `cdn.link-ai.tech` | 依赖与资源下载 | `channel/feishu/lark_install.py`（第 1 个镜像）、`scripts/run.ps1`、`webhelp/assets/deploy/*` |
| `link-ai.tech` | 站点入口 | `run.sh`、`desktop/src/renderer/src/pages/settings/ModelsTab.tsx`、`OnboardingWizard.tsx` |
| `github.com/zhayujie/CowAgent` | 源码归属与反馈入口 | `NavRail.tsx`、`docs/docs.json` |

### 3.2 仅作说明的域名提及（共 25 处）

改动前存在 25 处域名提及不含 `http(s)://`，因此不在 URL 替换范围内，全部保持原样：

- Markdown 链接显示文字（19 处），如 `[skills.cowagent.ai/submit](https://www.rsm.global/china/zh-hans)`、`[cowagent.ai](https://www.rsm.global/china/zh-hans)`。
- 正文与注释中的来源说明（6 处）：`webhelp/README.md`、`webhelp/assets/css/style.css`、`webhelp/includes/icons.php`、`webhelp/lang/en.php`、`webhelp/lang/zh.php`、`webhelp/docs/skills-install.html`。
- `.github/workflows/release.yml` 的两处行尾注释（`cowagent.ai/download/...`、`cdn.cowagent.ai custom domain`）亦属此类，保持原样。

保持原样的理由见 design D5：这些文字陈述的是**来源**，改写会使文档声称运营方发布了从未发布的版本与页面。

## 4. 改动前已指向运营方地址的反向先例

| 位置 | 说明 |
| --- | --- |
| `channel/web/chat.html` 的 `#sidebar-version` | 品牌版本链接，改动前即为运营方对外地址，显示文字为「容大AI」 |
| `channel/web/static/js/console.js` 的 `openAccountAbout()` | 复用 `#sidebar-version` 作为「关于 / 原更新日志」入口 |
| `tests/test_sidebar_account_frontend.cjs:141` | fixture 将 `sidebar-version` 的 `href` 赋为运营方对外地址 |

## 5. 执行与回退

- 执行方式：以 `https?://[A-Za-z0-9.-]*cowagent\.ai[A-Za-z0-9/_\-.~?=#%&+{}:$]*` 匹配 URL 目标，整体替换为单一地址；替换前对 93 个候选文件做干跑计数（361 处命中，边界形态全部被完整吞掉）后执行。
- 回退方式：替换为同形纯文本改动，不涉及数据、配置键、路由或资源迁移；按上述文件清单整体还原即可回到改动前状态。
