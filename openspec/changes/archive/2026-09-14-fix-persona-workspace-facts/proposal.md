## Why

租户 `test15`（AI启航团队）的三个智能体（`my-assistant-admin-test15`、`business-analysis-test15`、`knowledge-qa-test15`）的 `RULE.md` 里，「工作空间目录结构」段落描述的是**单机布局**：

```
~/cow/
├── AGENT.md
├── USER.md
├── RULE.md
├── MEMORY.md
├── memory/
├── knowledge/
├── skills/
├── websites/
└── tmp/
```

而这三个智能体实际工作在租户共享根之下：工作区是 `~/.cow/tenant-roots/tenants/test15/agents/<id>/`，`skills/`、`websites/`、知识库属于**租户共享层**，每位用户的私有数据在 `users/<user_id>/`，会话与长期记忆索引在 `<workspace>/memory/long-term/index.db`。文件里因此少了一层、错了一层，模型据此判断写入位置会写错地方。

同一段落里还有第二类错误：团队分工表把同事写成 `business-analysis` / `knowledge-qa` / `my-assistant-admin`，而本租户实际的智能体标识是 `*-test15`。`agent_delegate` 只接受「团队会话」段落里列出的精确标识，短标识在本租户不存在，交办会直接失败。

两个根因都在仓库侧，属于行为而不只是这一份数据：

1. **模板硬编码单机布局**：`agent/prompt/workspace.py` 的 `_RULE_TEMPLATE_ZH/_EN` 把布局段落写死成 `~/cow/`，而 `ensure_workspace` 对**任何**布局都套用同一份模板——包括 `<共享根>/agents/<id>` 下的多智能体工作区与租户工作区。新建的智能体都会继承这段错误描述。
2. **克隆原样搬运人设**：`AgentAdminService.clone_agent` 通过 `CLONED_FILES` 逐文件复制 `AGENT.md` / `USER.md` / `RULE.md` / `BOOTSTRAP.md`，`tenant_provisioning` 再按来源租户逐个克隆到目标租户，但**不重写**人设里指向来源租户的标识与路径。`tenant-agent-provisioning` 已有要求「MUST NOT 复制任何指向来源租户宿主布局的路径」，当前实现并未满足：默认租户的 `AGENT.md` 写着 `agents/my-assistant-admin/`，克隆到 test15 后该路径仍指默认租户的工作区；同事标识也仍是默认租户的 `business-analysis`。

## What Changes

- **`RULE.md` 的布局段落按工作区实际路径生成**：新增按工作区路径推导布局的能力（工作区是否形如 `<共享根>/agents/<id>`），在创建新工作区时把生成结果注入模板，替代写死的 `~/cow/` 树。段落改为说明三层归属：本智能体私有（工作区）、共享层（`skills/`、`websites/`、知识库）、每位用户私有（`users/<user_id>/`），并给出 `memory/long-term/index.db`、`scheduler/`、`tmp/` 的位置。
- **跨租户克隆重写人设中的租户内引用**：`tenant_provisioning.copy()` 在克隆完成后、绑定之前，对本次成功克隆的人设文件做一次有界改写——把来源租户共享根（绝对路径与 `~` 简写两种形态）改写为目标租户共享根，把来源智能体标识（本次选中的，以及目标租户已有克隆的）改写为各自在目标租户的克隆标识（整词匹配）。
- 不改动：`CORE_FILES`/`CLONED_FILES` 集合、`tenant-agent-provisioning` 的来源解析与幂等语义、`agent-knowledge-mode` 的模式判定与切换守卫、任何租户的共享根或工作区位置、任何租户数据。
- 顺带修正既有部署中已存在同类错误的文件：本实例默认租户 `~/cow/agents/{my-assistant-admin,knowledge-qa,business-analysis}` 与租户 test15 的三个智能体（后者已在本 change 之前按同样事实手工修正）。

## Capabilities

### New Capabilities

- `agent-workspace-scaffold`: 新智能体工作区的核心文件脚手架——`RULE.md` 的「工作空间目录结构」段落必须描述**本部署实际解析的**布局（工作区、共享层、用户层三档归属），不得写入其它部署的根路径。

### Modified Capabilities

- `tenant-agent-provisioning`: 「复制内容边界」要求补充「克隆人设中的租户内引用必须重写」——来源租户共享根路径改写为目标租户路径，本次选中的、以及目标租户已有克隆的来源智能体标识改写为其克隆标识；不得把来源租户的宿主布局路径或来源标识留作目标租户中的可寻址引用。

## Impact

- 代码：`agent/prompt/workspace.py`（新增按工作区路径推导布局段落的函数；`_RULE_TEMPLATE_ZH/_EN` 的布局块改为占位符；`ensure_workspace` 传入工作区路径）。
- 代码：`agent/tenant_provisioning.py`（克隆完成后的人设有界改写；`tenant-agent-provisioning` 的响应与审计字段不变）。
- 测试：`tests/test_workspace_layout_template.py`（新增，两种布局 + 中英）、`tests/test_tenant_agent_provisioning.py`（新增人设改写用例，含「只改写本次映射」「来源路径不残留」「克隆标识不被重复改写」）。
- 数据：不影响任何租户的共享根、工作区位置或既有绑定；不新增 schema 迁移。模板改动只影响**此后新建**的工作区，既有文件按同样事实手工修正，不做批量重写。
- 运行面：无需重启进程即可让新建工作区获得正确模板；既有工作区文件在会话启动时被读取，修正后立即生效。
