# 任务

## 1. 结构与渲染

- [x] 1.1 `includes/content.php`：`manual_sections` 改为 `manual_topics`，每项含 `id` / `part` / `icon` / `videos`（`id`、`duration`、可选 `src` / `poster`）/ `docs` / `links`；主题与分组沿用原章节 id 与分组顺序。
- [x] 1.2 `includes/bootstrap.php`：`manual_block()` 替换为 `manual_video()`——未声明 `src` 渲染 16:9 占位（播放图标 + 时长 + 待录制），声明 `src` 渲染 `<video controls preload="metadata">`。
- [x] 1.3 `includes/bootstrap.php`：`manual_nav()` 改为按主题渲染（分组不变），编号与锚点沿用 `manual-<主题 id>`。
- [x] 1.4 `manual.php`：改为遍历主题与视频；`commands` 主题保留命令表，`troubleshoot` 主题保留速查表，主题末尾仍渲染相关文档与相关页面。
- [x] 1.5 `assets/css/style.css`：新增视频占位、时长徽标、要点列表样式；窄屏下要点与标题正常回流。

## 2. 文案

- [x] 2.1 `lang/zh.php`：`manual.sections` 替换为 `manual.topics`（`nav` / `title` / `lead` / `videos.<视频 id>.{title,covers}`），删除 `steps` / `fields` / `note` 长文。
- [x] 2.2 `lang/en.php`：与中文逐键对齐，B 段视频要点不出现中文回退。
- [x] 2.3 要点写作口径复核：写「这个视频会讲到什么」，不写「先点这里再点那里」；控件名与页面路径以界面为准。

## 3. 校验

- [x] 3.1 `tools/check-manual.php`：断言两级语言键路径集合一致。
- [x] 3.2 断言每个主题在两种语言下都有 `nav` / `title` / `lead`；每个视频都有 `title` 且 `covers` 至少一条。
- [x] 3.3 断言主题 `part` 已在 `manual_parts` 声明、引用的 doc slug 与页面 id 均已登记。
- [x] 3.4 断言声明了 `src` 的视频同时具备标题与时长。
- [x] 3.5 缺项时以非零状态退出并列出问题项。

## 4. 文档与验收

- [x] 4.1 `webhelp/README.md`：更新手册章节结构说明（主题 / 视频 / 占位约定 / 如何补片）与校验命令。
- [x] 4.2 验收：`php -l` 全绿；中英双语 `manual.php` 返回 200；渲染结果无键名泄漏、无 `http(s)://` 与 `主机:端口`。
- [x] 4.3 验收：占位→播放器分支以本地样例视频各验一次（含移动端视口）。
- [x] 4.4 验收：全部站内链接（主题锚点、视频锚点、文档深链、页面链接）可达。
- [x] 4.5 `openspec validate convert-manual-to-video --strict` 通过后归档。
