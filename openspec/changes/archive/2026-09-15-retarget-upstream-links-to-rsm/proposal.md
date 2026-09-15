## Why

产品已按 `branding-product-rename` 统一为「容大AI」品牌，但各产品面残留的对外引用仍指向上游 `cowagent.ai` 及其 `docs.` / `skills.` / `cdn.` 子域：用户从「探索技能广场」「文档」「官网」「更新提示」等入口会被带到上游开源项目站点，而不是运营方（容诚 / RSM）站点，出现「品牌是容大AI、引用目标却是上游」的口径不一致。

这一不一致在既有实现中已有相反先例：账号菜单的品牌版本入口（`channel/web/chat.html` 的 `sidebar-version`）与前端测试 fixture（`tests/test_sidebar_account_frontend.cjs`）已指向运营方对外地址 `https://www.rsm.global/china/zh-hans`，`openAccountAbout()` 也复用它作为「关于 / 原更新日志」入口。本次将这一口径从单点扩展到其余全部上游外链，使产品对外引用与品牌归属一致，并把改动过程、范围与后果固化为可验收的规范。

## What Changes

- **用户可见外链统一指向运营方对外地址**：Web 控制台（技能管理「探索技能广场」、侧栏文档入口、账号菜单版本入口）、桌面端（侧栏官网 / 文档 / 技能广场、原生菜单、更新提示中的文档入口）、文档站点（`docs/docs.json` 导航、简繁英发布说明与技能 / 安装 / 桌面 / 升级页）、CLI 与插件输出提示、`.github` 议题模板与发布工作流日志中的链接，目标统一为 `https://www.rsm.global/china/zh-hans`。
- **目标一律取运营方对外地址，不保留原路径与语言后缀**：`https://docs.cowagent.ai/zh/guide/upgrade`、`https://cowagent.ai/zh/download/`、`https://skills.cowagent.ai/submit` 等均收敛为同一地址；`?lang=zh`、`#workspace`、`${key}`、`{version}.zip` 等后缀与路径不再附加，三种界面语言得到同一目标，不再派生 `/zh`、`/ja`、`/en`。该口径已落实在**配置与源码中的取值**层面（字面量、常量、配置项）；运行期在取值之后追加的 `legacy/`、`?lang=zh`、`/releases/v<版本>` 与文档构建页面路径**按已确认决定保持现状、不在本 change 范围**。
- **功能端点一并收敛，其可用性后果记录为显式契约**：技能广场接口（`cli/utils.py` 的 `SKILL_HUB_API`）、桌面端更新源（`desktop/src/main/updater.ts` 的 `FEED_BASE`）与发布目标（`desktop/package.json` 的 `publish.url`）、文档站点构建抓取基址（`webhelp/tools/build-docs.php`）、演示素材地址（`docs/intro/index.mdx`、`docs/ja/intro/index.mdx` 的 `<video src>`）改为运营方地址后，依赖端点的能力（技能广场在线安装与远程列表、桌面端自动更新、文档站点抓取、演示素材播放）SHALL 以明确、可诊断的失败呈现，MUST NOT 静默失败、伪装成功或无休止重试。当前实现尚未满足该口径（更新源仍按既有「两个来源互相回退」逻辑重试），该缺口作为任务列出，不视为已实现。
- **不触非目标域名与既有显示文字**：`api.link-ai.tech`、`cdn.link-ai.tech`、`link-ai.tech` 等第三方功能域名与 `github.com/zhayujie/CowAgent` 仓库归属地址保持原样；25 处仅作正文或注释说明的域名提及（如「视觉基线对齐上游 cowagent.ai/zh/」）不改写为目标地址，避免文字与指向不符。
- **交付边界**：本次交付为提案、设计、增量规格、任务与实施证据。代码改动已在本机工作区落地并通过文件与语法校验，但**尚未归档**；功能性端点的失效后果见 Impact，`G1` / `G2` 门槛未通过前不视为功能验收完成。

## Capabilities

### New Capabilities

- `upstream-link-retargeting`：规定产品各面对外引用目标统一指向运营方对外地址的口径、跳转链接与功能端点必须分别处置且端点收敛后的失败必须显式、目标地址经可配置接缝承载，以及非目标域名与既有显示文字不得被改动。

### Modified Capabilities

- `console-entry-consistency`：将「关于产品和更新日志含义一致」的目标口径扩展到运营方对外地址分支，明确该入口打开运营方对外地址时的名义与来源标注，并要求不臆造未验证的链接与联系方式。

## Impact

- 预期实现接入点：`channel/web/chat.html`、`channel/web/static/js/console.js`、`desktop/src/renderer/src/layout/NavRail.tsx`、`desktop/src/renderer/src/pages/SkillsPage.tsx`、`desktop/src/renderer/src/components/UpdateBanner.tsx`、`desktop/src/main/menu.ts`、`desktop/src/main/updater.ts`、`desktop/package.json`、`cli/utils.py`、`cli/commands/skill.py`、`plugins/cow_cli/cow_cli.py`、`models/linkai/link_ai_bot.py`、`channel/feishu/lark_install.py`、`webhelp/tools/build-docs.php`、`.github/**`、`docs/**`。
- 已落地事实（见 `evidence/1-1-inventory-and-execution.md`）：**86 个受版本控制文件、327 行、359 处 URL 目标**已改指向运营方地址；被 `.gitignore` 排除的 `skills/README.md` 另有 2 处；工作区中指向运营方地址的 URL 共 361 处（含 2 处改动前既有的同址引用）。
- **已知功能影响**（按已确认的「含功能端点」替换范围）：`SKILL_HUB_API` 收敛后技能广场在线安装与 `list --remote` 不再可达上游服务；`desktop` 更新源与发布目标收敛后桌面端自动更新不再可达上游更新服务；`build-docs.php` 抓取基址收敛后文档站点构建无法取得页面内容；`docs*/intro/index.mdx` 的 `<video src>` 收敛后演示素材不可播放。`lark_install.py` 的第一个镜像 `cdn.link-ai.tech` 未改动，飞书依赖包安装路径仍然可用。上述影响 MUST 在验收中按「明确失败、不静默」核实；是否恢复端点由任务 4.1 决定。
- 数据唯一归属保持现状：不新增数据库字段、配置键、路由或授权数据，不改动会话 / 身份模型与既有资源归属。
- 构建产物滞后：`desktop/dist/**`（4 个被忽略的构建产物文件）仍含旧目标，需重新构建打包后改动才在桌面端生效。
- 与既有能力的关系：`branding-product-rename` 已定品牌名与网络标识，本改动只补其未覆盖的对外引用目标，不改品牌名、User-Agent 与品牌资产；`console-entry-consistency` 的版本 / 关于入口口径按本改动更新；本改动在上游核心文件中就地改写 URL 字面量，须按 `fork-upstream-decoupling` 的冲突基线要求登记处置决策，并以下一阶段的接缝化（配置项）替代字面量，减少上游合并冲突。
- 验证现状：`tests/` 下当前无任何断言这些 URL 的用例（`tests/test_sidebar_account_frontend.cjs` 仅为 fixture 赋值），因此本次替换不产生既有测试失败，但也意味着**尚无回归保护**，需在任务 3 补充目标断言。
