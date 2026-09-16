# 手册重建为截图与步骤

## Why

手册上一版改成「视频为主」后，页面只剩主题定位与视频内容要点——使用者能知道「这个视频会讲什么」，但**照着做不了**。录制者要先产出 37 个视频，手册才真正可用；在补片之前，手册对使用者是一个 37 个占位框的目录。

同时主题集合过宽（16 个主题横跨工作台、管理控制台、个人与参考），把成员日常用不到的管理侧治理主题与日常操作混在一起，成员读起来要先跳过一半内容。

本次把手册收敛到**成员最高频的五项操作**，形态改为**真实界面截图 + 编号步骤**：截图取自当前版本界面，步骤说明每一步要达成的目标与操作要点。使用者在任何时刻打开都能照做，不依赖尚未产出的视频。

## What Changes

- **形态**：`manual_topics[].videos`（视频条目 + 内容要点 + 16:9 占位）→ `manual_topics[].steps`（编号步骤：目标、操作要点、真实界面截图）。撤掉视频位与 `manual_video()` 渲染助手。
- **主题集合收敛为 5 个**：发起对话（选择智能体、选择模型）、多智能体协同对话、设置本人的知识库、创建与配置本人的智能体、接入消息渠道。
- **分组由三组收敛为两组**：「对话与协作」（前两项）、「我的资源」（后三项）。删掉控制台治理类主题（记忆管理、模型服务、权限与角色、成员与组织、租户与审计等）与个人参考类主题（命令速查、故障排查、深入阅读）。
- **新增 17 张截图**，放在站点内 `assets/img/manual/`，由 `manual_step()` 渲染。
- **校验增强**：`tools/check-manual.php` 改为断言「主题 → 步骤 → 双语键」对齐，并**新增截图资源断言**（此前从未校验过图片资源）：截图存在且非空、不被两个步骤重复引用、文件名前缀与主题一致、目录中没有未被引用的孤儿截图、两张截图内容完全相同（多半是抓错屏）。
- **删除 `manual-video-scripts.md`**：录屏脚本的用途是给视频录制者提供开拍依据，手册不再有视频条目，脚本失去对应交付物。

## Impact

- 受影响的 capability：`product-manual-site`（`REMOVED` 四项、`ADDED` 两项、`MODIFIED` 一项）。
- 受影响的文件：
  - `webhelp/includes/content.php`：`manual_topics` 改为 5 主题的步骤结构；`manual_parts` 改为两组。
  - `webhelp/includes/bootstrap.php`：`manual_video()` → `manual_step()`；`manual_faq()`、`manual_issues()`、`manual_commands()`、`manual_all_docs()` 随查阅型主题一并移除。
  - `webhelp/includes/icons.php`：新增 `zoom` 图标（页头「点击截图可查看原图」提示用）。
  - `webhelp/manual.php`：按步骤结构渲染。
  - `webhelp/lang/zh.php`、`webhelp/lang/en.php`：`manual.topics.*.steps.*` 新文案；删除 11 个主题与全部视频文案。
  - `webhelp/assets/css/style.css`：新增截图卡片与编号步骤样式，移除视频占位样式。
  - `webhelp/assets/img/manual/`：新增 17 张截图。
  - `webhelp/tools/check-manual.php`：改为步骤结构校验 + 截图存在性校验。
  - `webhelp/manual-video-scripts.md`：删除。
  - `webhelp/README.md`：同步手册维护口径。
- 不涉及产品运行时代码、鉴权与数据模型，仅站点静态内容与校验脚本。
- 截图取自租户 `test15` 的成员账号 `RC001`（显示名 Rock）。截图中的会话标题、待办与知识库条目为演示数据。
