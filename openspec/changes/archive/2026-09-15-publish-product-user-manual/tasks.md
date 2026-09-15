# Tasks

## 1. 站点结构与文案

- [x] 1.1 在 `webhelp/includes/content.php` 增加手册结构：`manual_parts`（三组：工作台（日常使用）/ 管理控制台（配置与治理）/ 个人与参考）、`manual_sections`（id、`part` 分组、图标、`blocks` 块清单、本章引用的文档 slug 与站点页面 id）与 `manual_page_links`。
- [x] 1.2 章节按应用操作手册组织（16 章），顺序为**先工作台、后管理控制台**：工作台（开始使用 / 如何对话 / 选择智能体 / 设置知识库 / 待办与定时任务）→ 管理控制台（创建与配置智能体 / 记忆管理 / 模型服务 / 消息渠道 / 权限与角色设置 / 成员与组织 / 租户与审计）→ 个人与参考（个人账号设置 / 命令速查 / 故障排查 / 深入阅读）；不再包含安装与初始化章节，安装只在需要时引用既有快速开始页。
- [x] 1.3 同一主题跨区域时按界面归属拆章：`agents`（工作台「智能体」页：浏览与开始对话）与 `agents-admin`（控制台「智能体管理」：创建与配置）为两章。
- [x] 1.4 手册正文不出现网址：登录章节改为「打开控制台，用管理员分配给你的账号与密码登录」，不写主机名、端口与登录路径。
- [x] 1.5 在 `webhelp/lang/zh.php` 增加 `manual.parts.*`、`manual.sections.<章节>.{nav,title,goal}` 与 `manual.sections.<章节>.blocks.<块>.{title,steps,fields,items,note}` 中文本案。
- [x] 1.6 在 `webhelp/lang/en.php` 增加与 1.5 同名同结构的英文文案。
- [x] 1.7 在 `webhelp/lang/{zh,en}.php` 增加 `cta.manual` 文案键（「查看使用手册」/「Read the user manual」）。
- [x] 1.8 修正 `webhelp/lang/{zh,en}.php` 的 `quickstart.port_note`：去掉已退役的共享访问密码要求，改为数据库账号 + 强口令、监听地址与防火墙放行口径（design D5）。

## 2. 手册页面与渲染助手

- [x] 2.1 新建 `webhelp/manual.php`：引入 `bootstrap.php`、`page_hero()` 页头，逐章渲染章节标题（含序号）、本节目标、各内容块与章节末尾深链；侧栏导航复用 `.doc-aside`。
- [x] 2.2 在 `webhelp/includes/bootstrap.php` 增加渲染助手：`manual_nav()`（按 `manual_parts` 分组渲染章节导航）、`manual_block()`（单块：标题 + 步骤 + 字段说明 + 排查条目 + 注意事项）、`manual_issues()`、`manual_refs()`、`manual_commands()`、`manual_all_docs()`。
- [x] 2.3 支持块内三类操作内容：`steps`（编号步骤）、`fields`（界面字段名 + 填写口径的二元组）、`items`（现象 → 处理的排查条目），以及 `note` 注意事项。
- [x] 2.4 每章末渲染「相关文档 / 相关页面」深链：slug 经 `doc_exists()` 过滤后用 `doc_url()` + `doc_title()` 生成；站点页面链接走 `url()`，不使用绝对外链。
- [x] 2.5 命令速查章节复用 `content.php` 的 `cli_commands` 与 `slash_commands` 渲染两张表，不新增第二份命令清单。
- [x] 2.6 深入阅读章节用 `doc_grouped()` 按既有分组列出全部文档入口，并给出既有页（安装命令 / 企业级管控 / 系统架构 / 核心能力）链接。
- [x] 2.7 在 `webhelp/assets/css/style.css` 末尾的 `.manual-*` 一节补章节序号徽标、内容块间距与字段说明列表样式，复用既有设计令牌，不修改既有选择器。

## 3. 首页入口

- [x] 3.1 将 `webhelp/index.php` Hero 主按钮改为 `url('manual.php')` 并使用 `cta.manual` 文案。
- [x] 3.2 确认 `cta.primary` 与首页底部行动区、`features.php`、`enterprise.php` 的 CTA 目标未改变。

## 4. 文档同步

- [x] 4.1 更新 `webhelp/README.md`：页面清单加入 `manual.php`（描述为应用操作手册与十五章结构）、导航说明补充「手册由首页 Hero 进入、不在主导航内」、目录树加入校验脚本、二次开发段补充 `manual_parts` / `manual_sections` / `blocks` 与 `manual.*` 文案的维护位置及校验命令。

## 5. 结构与文案一致性校验

- [x] 5.1 新增 `webhelp/tools/check-manual.php`：递归展开 `lang/{zh,en}.php` 的 `manual.*` 键路径并断言两包键集合相同。
- [x] 5.2 校验脚本断言 `content.php` 声明的每个章节与内容块在两种语言下都有 `title` 与非空内容，且每个章节的 `part` 已在 `manual_parts` 中声明。
- [x] 5.3 校验脚本断言章节引用的文档 slug 已登记、站内页面 id 已在 `manual_page_links` 中映射；不一致时以非零状态退出并列出问题项。

## 6. 验证与验收

- [x] 6.1 `php -l` 通过 `manual.php`、`index.php`、`includes/{bootstrap,content,config}.php`、`lang/{zh,en}.php` 与 `tools/check-manual.php`。
- [x] 6.2 `php tools/check-manual.php` 输出 `OK  手册结构与双语文案一致` 且退出码为 0。
- [x] 6.3 `GET /manual.php` 与 `GET /manual.php?lang=en` 均返回 200；两侧渲染出的章节数、内容块数、字段数、分组数与排查条目数一致（16 / 51 / 54 / 3 / 16），英文页不出现中文回退或 `manual.` 键名。
- [x] 6.4 顺序验收：渲染出的章节顺序为 `start, chat, agents, knowledge, todo`（工作台）→ `agents-admin, memory, models, channels, roles, members, tenant`（管理控制台）→ `account, commands, troubleshoot, further`（个人与参考）；第一条工作台章节排在控制台章节之前。
- [x] 6.5 无网址验收：断言渲染后正文不匹配 `http://` / `https://` 与 `主机:端口` 形式的内容，两种语言结果均为 0 条；登录章节不含控制台地址、端口与登录路径。
- [x] 6.6 站内链接可达：抓取 `manual.php` 渲染结果中全部 `href`，逐个请求断言 200，或为页面内存在的锚点（锚点缺失 0，两种语言各 16 个锚点）。
- [x] 6.7 离线自包含：断言页面 HTML 中不存在指向站外的绝对 URL（`http://` / `https://` 非本站地址），结果 0 条。
- [x] 6.8 入口验收：首页 Hero 主按钮指向 `/manual.php`；首页底部行动区、核心能力页、企业级管控页的 CTA 仍指向各自原目标。
- [x] 6.9 口径验收：手册与站点内均不再出现「设置访问密码」类表述；手册只描述 `database` 身份模式；首次登录章节包含受限会话与强制改密。
- [x] 6.10 内容口径验收：手册描述的操作路径与界面实际控件一致，覆盖对话、选择智能体、创建智能体、设置知识库与配置权限五个主题；对界面没有入口的能力（如定时任务的新增）如实说明触发方式。
- [x] 6.11 浏览器实测中英双语手册页：章节导航跳转、置顶侧栏、窄屏下的字段列表折行、深浅主题下排版正常。
