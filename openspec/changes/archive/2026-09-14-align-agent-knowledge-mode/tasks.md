## 1. 判定与守卫

- [x] 1.1 新增 `AgentAdminService._shared_knowledge_base()`：以 `state_dir.shared_root()/knowledge` 为准（租户感知、与运行时同源），无法解析时回落实例根，再失败返回 `None`
- [x] 1.2 新增 `AgentAdminService._is_shared_base()`：`os.path.realpath` 比较，容忍符号链接与未规范化路径；基准为 `None` 时恒为 `False`
- [x] 1.3 `_knowledge_mode_of(profile, shared_base)` 改为按数据根归属判定：符号链接、目录不存在、或目录就是共享库本身 → `shared`；其余实体目录 → `own`
- [x] 1.4 `set_knowledge_mode` 的拒绝条件由「是默认智能体」改为「`knowledge/` 就是共享库」，并在共享库无法解析时报错；拒绝路径不写盘
- [x] 1.5 `snapshot()` / `knowledge_mode()` / `clone_agent()` 改用 `_shared_knowledge_base(settings)`，不再传 roster 的 `default_agent_id`
- [x] 1.6 `channel/web/web_channel.py::_tenant_agents_admin_projection` 改用共享库基准，去掉对 `get_agent_registry().default_agent_id` 的依赖

## 2. 测试

- [x] 2.1 默认智能体在实例根：报 `shared`、切 `own` 被拒、共享库未被移动/改名/变成符号链接
- [x] 2.2 默认智能体被移入私有工作区并持有实体 `knowledge/`：报 `own`，可切 `shared`（自有库暂存到 `knowledge.own`）再切回 `own`（内容原样恢复）
- [x] 2.3 非默认智能体落在实例根：报 `shared` 且拒绝切换（旧规则会误报 `own` 并在切换时把共享库移走）
- [x] 2.4 `snapshot()` 投影的 `knowledge_mode` 与上述判定一致
- [x] 2.5 既有 `test_default_agent_cannot_switch_knowledge_mode` 按新语义重写为 2.1 / 2.2 两条

## 3. 收尾

- [x] 3.1 `openspec validate align-agent-knowledge-mode --strict` 通过
- [x] 3.2 回归：`tests/test_agent_admin.py`、`tests/test_tenant_default_agent.py` 全绿（`test_agent_workbench.py` 中 `test_readiness_defaults_to_runnable_in_legacy` 为既有失败，与本 change 无关，已用 stash 对照确认）
