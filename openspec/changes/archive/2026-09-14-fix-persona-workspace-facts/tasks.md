## 1. RULE.md 布局段落按工作区实际路径生成

- [x] 1.1 在 `agent/prompt/workspace.py` 新增按工作区路径推导布局段落的函数：区分「工作区位于 `<共享根>/agents/<id>`」与「工作区即共享根」两种布局，输出三层归属（本智能体私有、租户/实例共享层、每位用户私有）与 `memory/long-term/index.db`、`scheduler/`、`tmp/` 的位置
- [x] 1.2 `_RULE_TEMPLATE_ZH/_EN` 的布局块改为占位符，`ensure_workspace` 传入工作区路径渲染
- [x] 1.3 新增 `tests/test_workspace_layout_template.py`：两种布局 × 中英，断言段落含实际根路径且不含 `~/cow/`；另断言树形注释同列对齐

## 2. 跨租户克隆重写人设中的租户内引用

- [x] 2.1 `agent/tenant_provisioning.py` 在批次开始时一次性规划全部来源标识到克隆标识的映射（含批次内去重），使重写拿到完整映射
- [x] 2.2 每个克隆在工作区就绪后、`bind_agent` 之前，对有界人设文件（`AGENT.md`、`USER.md`、`RULE.md`、`BOOTSTRAP.md`）做整词重写：来源共享根（绝对路径与 `~` 简写）→ 目标共享根，映射内来源标识 → 克隆标识
- [x] 2.3 重写失败按该来源标识复制失败处理（`_compensate` 且不绑定）
- [x] 2.4 扩充 `tests/test_tenant_agent_provisioning.py`：来源路径不残留、映射内标识被改写、映射外标识与原长标识子串保持原样、已有克隆映射、`~` 形态覆盖

## 3. 既有部署的同类错误修正

- [x] 3.1 按同样的布局与标识事实核对并修正本实例默认租户 `~/cow/agents/{my-assistant-admin,knowledge-qa,business-analysis}` 的 `RULE.md`
- [x] 3.2 确认租户 test15 六个智能体的 `RULE.md` 已按同一事实修正；三个受管智能体保留各自的知识库模式说明

## 4. 验证

- [x] 4.1 `python -m pytest tests/test_workspace_layout_template.py tests/test_tenant_agent_provisioning.py tests/test_agent_admin.py tests/test_agent_web_management.py tests/test_state_dir.py tests/test_subagent.py tests/test_tenant_agent_copy_*.py tests/test_tenant_agent_creation.py tests/test_agent_clone_primitives.py -q`（201 passed）
- [x] 4.2 `openspec validate fix-persona-workspace-facts --strict`
