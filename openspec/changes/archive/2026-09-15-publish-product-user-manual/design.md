## Context

`webhelp/` 是零依赖的原生 PHP 站点：6 个内容页（`index` / `features` / `enterprise` / `quickstart` / `architecture` / `about`）+ 文档阅读页 `doc.php`（`?p=<slug>`，slug 走 `docs/manifest.php` 白名单）+ 31 篇本地化能力文档正文（`docs/*.html`）+ 双语语言包 `lang/{zh,en}.php`。结构（章节顺序、图标、命令、文档引用）集中在 `includes/content.php`，可翻译文案集中在语言包，视图辅助函数在 `includes/bootstrap.php`（`page_hero()` / `section_heading()` / `code_block()` / `deploy_block()` / `icon()` / `doc_url()` / `doc_title()`）。

现有复用资产：`deploy_block()` 已产出三平台安装命令标签页；`content.php` 已有 `cli_commands`、`slash_commands`、`capabilities[].doc`（能力卡片到文档 slug 的映射）；`doc_grouped()` 可按分组列出全部文档。因此手册无需新增安装命令、命令表与文档清单的第二份数据源。

约束：站点完全离线自包含、不引用外网；语言包 zh/en 结构必须一一对应；`docs/manifest.php` 由构建脚本产出，禁止手改。

## Goals / Non-Goals

**Goals**

- 让新使用者一条路径走通「装好 → 登录 → 认识控制台 → 日常使用 → 企业管控」，需要原理时可一键跳到既有文档。
- 手册内容与 `openspec/specs/` 的实际行为一致，并消除站点内自相矛盾的身份口径。
- 入口改动最小且可回退：只动首页 Hero 主按钮。

**Non-Goals**

- 不新增或改写 `docs/**` 能力文档正文，不改 `tools/build-docs.php`。
- 不把手册做成单页完整自包含长文（会与既有 31 篇重复），也不做成纯文档目录页（缺操作指引）。
- 不新增站点级依赖、构建步骤或对外请求；不改运行时后端、Web 控制台、桌面端与 CLI。
- 不改手册外的导航信息架构（主导航仍为 5 项）。

## Decisions

### D1：新建 `manual.php` 而非扩展现有页面

手册是「按使用顺序组织的操作指引」，与 `quickstart.php`（部署命令）、`features.php`（能力清单）、`doc.php`（单篇原理文档）职责都不同。放进任一既有页会让该页承担两种阅读路径，且 `quickstart.php` 的定位（命令速查）会被稀释。新增独立页面让入口、锚点与后续维护都清晰。

代价：站点从 6 个内容页变 7 个；README 的页面清单需要同步。

### D2：内容分两层，手册只写操作，原理深链既有文档

站点已把上游 31 篇文档本地化，再写一遍原理必然产生两份真相。手册每章的结构固定为「本节目标 → 编号步骤 → 注意事项 → 相关文档（slug 列表）」，其中 slug 列表来自 `content.php` 的结构声明，标题由 `doc_title(slug)` 取，链接由 `doc_url(slug)` 生成——因此文档改名或增删不会让手册出现死链（slug 不存在时 `doc_exists()` 为假，跳过渲染）。

### D3：结构与文案分离，沿用站点既有约定

章节顺序、图标、命令、文档 slug 放 `includes/content.php` 的 `manual_*` 键；全部可翻译文案放 `lang/{zh,en}.php` 的 `manual.*`。这样双语对齐只需按 id 补齐键，也符合 README 的「改文案改语言包、改结构改 content.php」约定。

### D4：入口用新增文案键，不动 `cta.primary`

`cta.primary` 被 4 处复用（首页 Hero、首页底部行动区、企业级管控页、核心能力页）。直接改它的目标会让另外 3 处一并跳到手册，超出「点击红框按钮跳转」的范围。方案：新增 `cta.manual` 键并只在首页 Hero 使用，`cta.primary` 与其余 CTA 保持原状。可回退性最好——撤销只改回一行 `href`。

### D5：手册承重事实以规范为准，并修正站点冲突口径

手册涉及的行为全部取自 `openspec/specs/**` 与实现代码，不引用营销口径。发现 `quickstart.php` 的 `quickstart.port_note` 仍要求「设置访问密码」，与 `user-auth` / `enterprise-access-enforcement`（共享密码已退役、`database` 为唯一模式）冲突：两页并存会让使用者按错的做法部署。本变更把该提示改为数据库账号 + 强口令口径（保留 0.0.0.0 与防火墙放行建议）。

**备选**：把修正拆成独立 change。否决——冲突正好由本变更引入的手册内容暴露，同一变更内改掉才自洽。

### D6：不承诺菜单中仍在迁移的个人页

工作区存在尚未归档的 `unify-console-by-data-scope` 与 `remove-account-personal-resources-menu`，个人页（我的智能体/我的记忆/我的渠道/我的工具与技能）正被正式页与数据范围取代。手册按**两个导航区域 + 稳定页名**描述（工作台：对话、智能体、我的待办、定时任务、知识库、场景应用；管理控制台：控制台概览，智能体开发〔智能体管理、工具与技能、记忆管理〕，模型与接入〔模型服务、消息渠道〕，组织与权限〔成员管理、组织架构、角色权限〕，平台运维〔租户管理、系统设置、品牌设置、运行日志、审计〕），并说明列表内容随调用者数据范围与授权而变，不写死个人分组是否存在。

### D7：命令速查复用既有数据源 + 补齐实际注册命令

`content.php` 已有 `cli_commands` / `slash_commands`。手册直接复用这两份数据渲染速查表，避免第二份命令清单。同时以代码为准确认清单边界：CLI 顶层为 `skill / start / stop / restart / self_restart / update / status / logs / context / knowledge / backup / restore / install-browser / management`；聊天内命令以 `KNOWN_COMMANDS` 为准（含 `tasks`、`clear`、`compact`），`/steer` 为 Web 渠道专有。手册只写使用者需要的最小集合（启停、日志、更新、技能、知识库、备份恢复、浏览器工具、管理初始化）。

## Risks / Trade-offs

- **手册与产品演进的漂移**：手册是手写文案，产品行为变化后可能过期。缓解：把承重的行为句集中在少数章节，并在每章末尾深链对应规范能力文档；后续产品变更应同步检查 `manual.*`。
- **双语维护成本**：两套文案必须同步补齐。缓解：键按 id 对齐，验证步骤包含 zh/en 键名一致性检查；缺键时 `t()` 回退到中文，验证脚本必须显式断言不存在回退。
- **页面数量增加**：README 与站点结构说明需同步更新，否则文档与实际不符。
- **个人页迁移未定**：D6 用「不写死」规避，但若迁移最终删除了某个个人入口，手册的「个人自助」表述需复核。

## Migration Plan

无数据迁移。上线即生效：新增页面 + 首页一个按钮目标变化。回退方式：把 Hero 主按钮 `href` 改回 `url('quickstart.php')` 并删除 `manual.php` 即可，不影响其它页面。

## Open Questions

- 手册是否需要加入主导航（第 6 项）？当前决定跟随「只改 Hero 按钮」的范围，保持 5 项导航；若后续需要，只需在 `config.php` 的 `nav` 增加一项。
- 是否需要在站点底部或页脚提供手册入口？当前不加，避免与 Hero 入口重复。
