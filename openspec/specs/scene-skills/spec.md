# scene-skills Specification

## Purpose
TBD - created by archiving change port-scene-applications. Update Purpose after archive.
## Requirements
### Requirement: 场景关联技能发现

系统 SHALL 将场景关联的技能（OneAgent `skills/*` 按映射表适配搬入 `scenes/skills/`）注册到 SkillManager 的可发现技能集，使 Agent 在 `_build_skills_section` 中能看到并选用。技能 SHALL 采用 CowAgent 既有 SKILL.md 目录结构与 `skill_name` 命名键；外部依赖（网络/私有系统）不由本仓库提供，搬运只承载 SKILL 文案与本地可用能力，不复制 OneAgent 的运行配置、凭据或客户数据。

#### Scenario: 场景技能注册成功
- **WHEN** 某场景关联的技能已按映射表放入 `scenes/skills/` 且目录结构合法
- **THEN** 该技能出现在 SkillManager 可发现列表，Agent 提示词中列出该技能

#### Scenario: 技能目录结构不合法
- **WHEN** 某 SKILL 目录缺少 SKILL.md 或元数据不合法
- **THEN** 该技能被隔离，不阻断其它技能注册，也不报错阻断场景

#### Scenario: 技能依赖外部系统
- **WHEN** 技能描述依赖网络或私有系统的能力
- **THEN** 仅搬运 SKILL 文案与本地能力，实际网络访问由运行环境决定，不携带 OneAgent 凭据

### Requirement: skill_name 到技能目录的映射表

系统 SHALL 用 `scenes/skill_mapping.json` 记录 `skill_name`（场景配置语义键）到实际技能目录的映射。映射表 SHALL 覆盖全部 21 个去重 `skill_name`，每条 SHALL 含技能目录与 SKILL.md `name`。无法确定对应目录的 `skill_name` SHALL 标注「未映射」，v1 不强行搬运、不阻断场景。仅 3 个 `skill_name`（`pmc-scheduler-hmt-qd`、`quality-trace`、`sap-integration`）与目录同名，其余必须经映射表解析。

#### Scenario: 映射表覆盖全部场景技能键
- **WHEN** 建立技能映射表
- **THEN** 21 个去重 `skill_name` 每条都有映射条目或「未映射」标注

#### Scenario: 未映射技能键
- **WHEN** 某 `skill_name` 无法确定对应技能目录
- **THEN** 该键标注「未映射」，不搬运对应技能，也不阻断场景激活与对话

### Requirement: 场景-技能关联

系统 SHALL 在激活场景时按映射表把 `skill_name` 解析为技能目录，关联本场景的技能选择集，使 Agent 的可用技能与场景匹配。未配置 `skill_name` 或关联技能未安装时 SHALL 保持可用技能不受影响，对话正常进行。同一技能被多个场景引用时 SHALL 复用同一技能实例，不重复加载。

#### Scenario: 激活带技能的场景
- **WHEN** 场景配置了已安装的 `skill_name`
- **THEN** 该任务技能进入该会话 Agent 的可用技能选择集

#### Scenario: 场景无关联技能
- **WHEN** 场景未配置 `skill_name` 或技能未安装
- **THEN** 不注入该技能，可用技能集保持不变

#### Scenario: 多场景共享技能
- **WHEN** 多个场景引用同一个技能
- **THEN** 复用同一技能实例，不重复加载或写入

