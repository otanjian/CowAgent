## Why

个人助理卡片上写着**原所属人**的角色措辞，而不是所有者本人的名字。

实测（租户 `test15`，成员 `RC001`／显示名 `Rock`，普通 `member` 角色）：

| 字段 | 源模板「智能办公助理」 | Rock 的副本 |
| --- | --- | --- |
| `description` | 管理员专属智能办公助理：人设与长期记忆跟随 **admin** 本人，不进租户共享目录 | 管理员专属智能办公助理：人设与长期记忆跟随 **Rock** 本人，不进租户共享目录 |
| `persona_summary` | **管理员**的私人智能办公助理：结论先行，… | **管理员**的私人智能办公助理：结论先行，… |

成因：现有个人化（`_personalise`）只按**可配置别名表**做整词替换，默认别名只有 `admin`。`admin 本人` 因此被正确换成 `Rock 本人`，但 `管理员` 是**中文角色词、不在别名表内**，于是原样留在副本上。Rock 是普通成员，卡片却声称「管理员专属」，人设摘要也自称「管理员的私人智能办公助理」——两处都对该所有者的事实描述错误。

需要说明这**不是实现缺陷而是既有规范的既定行为**：`user-personal-agent-provisioning` 的「人设个人化归属新用户」明确要求「别名表未命中时 SHALL 保持原文，MUST NOT 猜测或改写无关文本」。所以本 change 是**需求变更**：把面向所有者的**归属短语**改为按成员显示名**生成**，同时保留「不得猜测、不得改写无关文本」的边界。

另有一个约束决定了方案不能简单化：克隆来源由 `resolve_source()` 按**可配置名称**在本租户内解析，源模板的文案措辞是**部署可变**的。因此不得把当前这段中文写死为唯一可识别的形态。

## What Changes

- 新增两个可配置模板，在既有别名替换**之后**套用，且只作用于个人助理副本：
  - `personal_assistant_description_template`（默认 `{name}的专属办公助理：{source_tail}`）
  - `personal_assistant_persona_summary_template`（默认 `{name}的私人智能办公助理：{source_tail}`）
- 占位符语义：`{name}` 为该成员**显示名**（缺省回落用户名）；`{source_tail}` 为源字段中**第一个 `：` 或 `:` 之后**的内容（已先经别名替换），用于保留源文实质描述。源字段无分隔符时 `{source_tail}` 为空。
- 模板**留空即关闭**该字段的生成，退回源文——给运维一个不改变现状的退出通道，也保证既有部署升级后行为可预期。
- 新增幂等回填入口 `cow management personalize-personal-agents [--tenant CODE] [--dry-run]`，把存量副本的两个字段按本规范重算，使已创建的个人助理不再残留原所属人措辞。
- 不改动个人助理的**名称**（保持「智能办公助理」）；不改动来源智能体；不改 `greeting` / `position`（无原所属人措辞）。

## Capabilities

### Modified Capabilities

- `user-personal-agent-provisioning`：
  - 「人设个人化归属新用户」补入「面向所有者的归属短语按显示名由模板生成」及其边界；并同步收紧原「别名未命中则保持原文」场景的表述，使其与新的生成行为不矛盾。
  - 新增「存量个人助理的归属短语回填」：回填范围（仅带私属归属的绑定）、dry-run、幂等、不触碰非个人助理。

## Impact

- 后端：`config.py`（新增 2 个模板默认值）、`agent/personal_assistant.py`（`_personalise` 套模板；新增可复用的字段生成函数）、`cli/commands/management.py`（新增回填命令，形态照 `share-default-agents`）。
- 测试：新增 `tests/test_personal_assistant_owner_fields.py`（生成文案逐字命中、源智能体不变、`{source_tail}` 保留、模板留空退出、回填幂等与 dry-run、不触碰非个人助理），并新增回填命令的 CLI 测试。
- 不改动：`agent_bindings` 表结构、无 schema 迁移、无数据回填以外的写路径；个人助理的名称、绑定、默认登记与权限判定（含 `fix-private-agent-owner-reachability` 的所有权授权）均不变。
- 跨 change 依赖：与 `fix-private-agent-owner-reachability` 作用在**不同 requirement 块**（后者改可达性与空态，本 change 改文案生成），不争用；两者可独立归档。
