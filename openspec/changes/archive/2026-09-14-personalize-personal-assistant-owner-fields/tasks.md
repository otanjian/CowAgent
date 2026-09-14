## 1. 归属短语生成（服务端）

- [x] 1.1 在 `config.py` 新增两个模板键并给出默认值：`personal_assistant_description_template` = `{name}的专属办公助理：{source_tail}`，`personal_assistant_persona_summary_template` = `{name}的私人智能办公助理：{source_tail}`；补注释说明占位符语义与「留空即关闭」。
- [x] 1.2 在 `agent/personal_assistant.py` 新增可复用的字段生成函数 `render_owner_field()` / `source_tail()`：按 `{name}`（显示名，缺省回落用户名）与 `{source_tail}`（源字段第一个 `：` 或 `:` 之后的内容，无分隔符则为空）渲染模板；未知占位符保持字面、不做 `str.format` 解析，避免模板中的花括号抛错。
- [x] 1.3 在 `_personalise()` 中先渲染归属短语、再由既有别名替换收尾，保证 `{source_tail}` 取自源文而其中的 `admin` 仍被换成显示名。
- [x] 1.4 模板留空时该字段不生成、保持源文；字段在源中不存在时 MUST NOT 凭空写入。仅对 `description` 与 `persona_summary` 生效，`greeting` / `position` 与名称不变。
- [x] 1.5 只在副本上写入：来源智能体的名称、描述、人设摘要与文件 MUST NOT 被改动。

## 2. 存量回填入口（CLI）

- [x] 2.1 在 `cli/commands/management.py` 新增 `personalize-personal-agents`，支持 `--tenant-code` 与 `--dry-run`，形态与输出照 `share-default-agents`。
- [x] 2.2 `PersonalAssistantProvisioner.personalize_existing()` 只取**带私属归属的 Agent 绑定**（`private_owner_user_id` 非空）；MUST NOT 触碰来源智能体、租户共享默认智能体或其它普通智能体。
- [x] 2.3 所有者显示名经 `get_membership().display_name` 解析，与创建路径同源；缺成员关系记 `owner_has_no_display_name`，绑定无 roster 条目记 `agent_not_in_roster`。
- [x] 2.4 幂等：目标文案与现值相同即记 `up_to_date`、不写盘；重复执行不产生进一步变更。
- [x] 2.5 `--dry-run` MUST NOT 写入任何字段（含不写审计）；报告变更/跳过/失败条数。
- [x] 2.6 复用 `record_personal_agent_event`，以 `member.personal_agent.personalize` 记录本次回填及其改动的字段，使一次回填可事后追溯。

## 3. 测试

- [x] 3.1 生成：源描述「管理员专属智能办公助理：人设与长期记忆跟随 admin 本人，不进租户共享目录」+ 显示名 Rock → 副本描述逐字等于「Rock的专属办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录」。
- [x] 3.2 生成：源人设摘要「管理员的私人智能办公助理：结论先行，…」→ 副本为「Rock的私人智能办公助理：结论先行，…」（源文实质内容保留）。
- [x] 3.3 隔离：来源智能体的 `description`/`persona_summary`/名称与文件均未改变。
- [x] 3.4 边界：源字段无分隔符时 `{source_tail}` 为空且不报错；ASCII `:` 同样分隔；源字段缺失时不写入该字段；模板含未知花括号不抛错。
- [x] 3.5 退出：模板留空时该字段保持源文（仅受别名替换影响）；两个模板都留空时行为与本 change 之前一致。
- [x] 3.6 名称：个人助理的名称仍为「智能办公助理」，未被模板影响。
- [x] 3.7 回填：对既有副本执行后字段被修正；`--dry-run` 不改盘且不写审计；连续两次第二次全部跳过；指定租户不影响其它租户。
- [x] 3.8 回填范围：来源智能体、租户共享默认智能体与其它普通智能体在回填后字段不变。
- [x] 3.9 回填的模板留空字段不被改写；新建即正确的副本不被回填触碰。
- [x] 3.10 CLI：`personalize-personal-agents` 的 dry-run 预览、apply 后 `Updated 1`、重跑 `Updated 0`、未知租户干净失败、按租户限定范围。

## 4. 验证与归档

- [x] 4.1 用真实身份库 + 真实 roster 走一遍生成路径，记录修复前后对照证据（含 Rock 的实际字段值）。
- [x] 4.2 以 `--dry-run` 对真实环境预览回填范围，确认只命中个人助理（命中 1 条，来源智能体被正确排除）。
- [x] 4.3 确认无 schema 迁移、无列变更；两个模板留空即为回滚路径。
- [x] 4.4 与未归档 change 的 requirement 块不争用：`fix-private-agent-owner-reachability`（可达性与空态）与本 change（文案生成）作用在不同 requirement 块。
- [x] 4.5 已核查 `doc/` 与 `docs/` 中无其它文档引用该文案，无需同步。
- [x] 4.6 已对真实环境执行回填（先备份 `agents/team.json` 与 `identity.db`），Rock 副本两个字段已修正；复跑报 0 验证幂等；随后 `launchctl kickstart -k gui/$(id -u)/com.cowagent.app` 重启后端，`/chat` 返回 200，进程读到的 roster 即新文案。
