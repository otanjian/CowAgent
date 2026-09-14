## Why

`AgentAdminService._knowledge_mode_of` 对**默认智能体**无条件返回 `shared`，其前提是"默认智能体拥有实例根"（源码注释：The default Agent owns the instance root, so its `knowledge/` *is* the shared one）。该前提在两种真实部署下不成立：

1. **默认智能体被移入私有工作区**（本实例：`team.json` 把默认智能体 `my-assistant-admin` 的 `workspace` 显式设为 `<instance_root>/agents/my-assistant-admin`）。它因此保留了一个实体 `knowledge/` 目录，运行时（`state_dir._shared_or_own` 按存在判定）与 `/api/knowledge/list`（见 `open-tenant-knowledge-console`）读的都是它自己的目录，而配置面仍显示「共享」。
2. **默认智能体确实在实例根**：此时它的 `knowledge/` 就是共享库本身，必须继续报「共享」——这是原有例外真正要覆盖的情形。

同时 `set_knowledge_mode` 的拒绝条件也写成"是默认智能体就拒绝"：于是情形 1 里用户在界面上**无法**把这个智能体切回共享库（会得到 "the default Agent owns the shared knowledge base"，但该智能体并不拥有共享库）；真正该拒绝的是"该智能体的 `knowledge/` 就是共享库本身"（否则切换会把整个团队的共享库 `rename` 成 `knowledge.own`）。

配套地，`open-tenant-knowledge-console` 已把知识库页的数据根改为按所选智能体解析（`_knowledge_workspace_root` → `state_dir.knowledge_dir(base=<agent workspace>)`，按存在判定）。本 change 让配置面投影与切换守卫和**同一个事实**对齐，消除"配置页说共享、知识库页是空的"这类自相矛盾。

## What Changes

- **判定改为按数据根归属**：智能体的知识库模式由它的知识库数据根落在哪里决定——是它自己工作区下的实体 `knowledge/` 目录 → 独立；是共享库本身（实例根下的 `knowledge/`、指向共享库的符号链接、或不存在而回落共享库）→ 共享。判定 MUST NOT 仅凭"是否为默认智能体"。
- **切换守卫改为按共享库归属**：`set_knowledge_mode` 仅在"该智能体的 `knowledge/` **就是**当前身份解析出的共享库"时拒绝，其余情况允许切换（含被移入私有工作区的默认智能体）。拒绝时磁盘不变。
- **默认智能体在实例根的既有行为保持不变**：报「共享」、拒绝切换、共享库不被移动。
- 顺带消除一个既有隐患：非默认智能体的 `workspace` 若被指向实例根，其 `knowledge/` 也是共享库，此前会被报成「独立」并在切换时把共享库移走；改造后报「共享」并拒绝切换。
- 不改动：共享库的落盘形态（`index.md`/`log.md` 保护、`knowledge.own` 暂存与恢复规则、符号链接指向的解析）、`knowledge_ids` 条目级绑定、租户共享根解析与知识库页的数据根规则（由 `open-tenant-knowledge-console` 承载）。

## Capabilities

### New Capabilities
- `agent-knowledge-mode`: 智能体知识库共享/独立模式的判定与切换——按数据根归属判定、按共享库归属守卫切换、配置面投影与运行时数据根一致。

## Impact

- 后端：`agent/admin.py`（新增 `_shared_knowledge_base()` 解析共享库基准——以 `state_dir.shared_root()` 为准、回落实例根；新增 `_is_shared_base()`；`_knowledge_mode_of` 改为按数据根归属判定；`set_knowledge_mode` 的拒绝条件由"默认智能体"改为"`knowledge/` 就是共享库"；`snapshot`/`knowledge_mode`/`clone_agent` 传入共享库基准）、`channel/web/web_channel.py`（`_tenant_agents_admin_projection` 改用同一基准，去掉对 roster 默认智能体 id 的依赖）。
- 测试：`tests/test_agent_admin.py` 新增判定与守卫用例；既有 `test_default_agent_cannot_switch_knowledge_mode` 按新语义重写（改为"在实例根的默认智能体"与"被移入私有工作区的默认智能体"两条）。
- 数据：无 schema 迁移、无配置迁移；判定与守卫都是读取时计算。
- 文案：知识库页顶部说明（`knowledge_shared_hint`）由"默认全员共享"改为中性表述，随本 change 一并落地，但其规范归属 `open-tenant-knowledge-console` 的 `tenant-knowledge-console` capability 而非本 capability。
- 依赖：`open-tenant-knowledge-console`（知识库页数据根按所选智能体解析）——本 change 只对齐配置面投影与切换守卫，不重复定义数据根解析规则；该 change 未落地前，知识库页仍固定返回租户共享根，本 change 的投影会与其不一致（因此本 change 的验收以该 change 已落地为前提）。
- 上行依赖：`agent-digital-employee-profile`（概况页保留知识库 shared/own 能力）与 `agent-capability-bindings`（与知识 shared/own 共存）仍成立，本 change 只细化"模式如何判定"。
