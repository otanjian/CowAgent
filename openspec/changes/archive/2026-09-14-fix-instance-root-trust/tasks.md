## 1. 回归测试（RED）

- [x] 1.1 在 `tests/test_state_dir_tenant_containment.py` 新增测试：默认智能体拥有私有工作区（`<实例根>/agents/<id>`）时，`shared_root` 为实例根的租户仍能 `state_dir.shared_root()` 解析成功（`test_instance_root_trusted_when_default_agent_has_private_workspace`）
- [x] 1.2 新增测试：实例根已可信时，家目录下未配置基目录的 `~/Documents` 仍被拒绝（`test_instance_root_trust_does_not_widen_to_other_home_dirs`）
- [x] 1.3 确认 1.1 在修复前失败（复现 `home/global workspace root` 拒绝），1.2 在修复后仍通过

## 2. 实现（GREEN）

- [x] 2.1 `common/state_dir.py` 新增 `_instance_root()`：读取 `agent_workspace`（缺省 `~/cow`），`expand_path` + `realpath`，异常时返回 `None`
- [x] 2.2 新增 `_trusted_roots()`：合并实例根、默认智能体工作区、`_tenant_base_real()`，去重且忽略空值
- [x] 2.3 `_is_home_or_global_escape()` 接收可信根集合，逐项做「相等或在其中」判定
- [x] 2.4 移除 `_engineering_root()`，更新 `_assert_tenant_roots_do_not_contain()` 调用点与注释

## 3. 验证

- [x] 3.1 `tests/test_state_dir_tenant_containment.py` 全通过（9 passed）
- [x] 3.2 相关回归：`test_state_dir.py`、`test_state_dir_tenant.py`、`test_default_agent_tenant_shared.py`、`test_identity_web_handlers.py`（124 passed）与 `test_conversation_tenant_isolation.py`、`test_memory_storage_tenant_scope.py`、`test_scenes_tenant_scope.py`、`test_rbac_tenant_constraints.py`、`test_identity_agent_bindings.py`、`test_tenant_agent_creation.py`、`test_tenant_archive.py`（79 passed）
- [x] 3.3 `openspec validate fix-instance-root-trust --strict` 通过
- [x] 3.4 重启服务后确认 `_session_settings_state` 对默认租户返回 `source=global`、`model=deepseek-v4-flash`、`providers=[deepseek: [deepseek-v4-flash, deepseek-v4-pro]]`

## 4. 收尾

- [x] 4.1 路由基线无需变更：改动不新增/删除路由，`tests/test_route_registry.py` 通过（17 passed）
- [x] 4.2 记录验收证据（见下）

## 5. 执行隔离同源修复（RED → GREEN）

- [x] 5.1 在 `tests/test_execution_isolation.py` 新增测试：屏蔽区（home）是本租户合法根的祖先时，本租户根的 `read`/`write`/bash 访问仍放行；同一 home 内非合法根路径仍拒绝
- [x] 5.2 确认 5.1 在修复前失败（复现「位于隔离根之外」）
- [x] 5.3 `agent/permission/isolation.py`：屏蔽区判定改为「落在屏蔽区**且**不在本租户合法根内」才拒绝（读用 `read_roots`，写用 `write_roots`）；home 屏蔽加入不再依赖「默认智能体工作区」单一来源
- [x] 5.4 用真实身份库脚本复现：默认智能体拥有私有工作区时，本租户共享根经 `isolation_decision` 放行、`~/.ssh` 与数据根仍拒绝

## 6. 被拒提示按 kind 区分

- [x] 6.1 `tests/test_execution_permission_ui.cjs` 新增断言：`permission_denial_kind === 'isolation'` 时渲染隔离边界文案
- [x] 6.2 `channel/web/static/js/i18n/identity-admin.js` 与 `tests/fixtures/console_i18n_snapshot.json` 增加 `perm_denied_isolation_hint`（简/繁/英）
- [x] 6.3 `channel/web/static/js/console.js` 按 `kind` 选择文案
- [x] 6.4 Desktop `desktop/src/renderer/src/i18n.ts` + `MessageSteps.tsx` 同步（简/英对称）

## 7. 验证

- [x] 7.1 `tests/test_execution_isolation.py`、`tests/test_state_dir_tenant_containment.py`、`tests/test_user_personal_memory.py` 全通过（47 passed）
- [x] 7.2 `tests/test_execution_permission_ui.cjs` 通过；`perm_denied_isolation_hint` 在 i18n 字典与快照中一致（简/繁/英）
- [x] 7.3 `openspec validate fix-instance-root-trust --strict` 通过
- [x] 7.4 真实身份复现脚本确认本租户根放行、`~/.ssh` 与数据根仍拒绝

## 验收证据

复现（修复前，`common/state_dir.py` 旧实现）：

```
默认租户  -> FAIL StateDirError tenant 'tnt_xNHlQIA2XP-z6nQG' shared root
                     '/Users/jiantan/cow' resolves inside the home/global
                     workspace root; refusing to fall back
AI启航团队 -> OK /Users/jiantan/.cow/tenant-roots/tenants/test15
```

修复后同一调用：

```
默认租户  -> OK /Users/jiantan/cow
AI启航团队 -> OK /Users/jiantan/.cow/tenant-roots/tenants/test15
```

模型选择器数据源（`GET /api/sessions/<id>/settings` 的 handler 主体）在默认租户身份下：

```
model        : deepseek-v4-flash
source       : global
selection_req: False
providers    : [('deepseek', ['deepseek-v4-flash', 'deepseek-v4-pro'])]
```

服务端已 `launchctl kickstart -k gui/<uid>/com.cowagent.app` 重启，`GET /chat` 返回 200。

### 执行隔离同源修复（本次追加）

修复前真实身份库（`admin`，平台管理员）隔离判定：

```
TENANT 默认租户 /Users/jiantan/cow
  read_roots = ['/Users/jiantan/cow', '/Users/jiantan/cow/agents/my-assistant-admin', ...]
  blocked    = ['/Users/jiantan/.cow/tenant-roots/tenants/test15', '/Users/jiantan', '/Users/jiantan/ai_assistant/cowagent']
  ls   /Users/jiantan/cow            -> 拒绝 目标路径位于隔离根之外
  read /Users/jiantan/cow/AGENT.md   -> 拒绝
  bash 'ls /Users/jiantan/cow'       -> 拒绝
  bash 'cat ~/.ssh/id_rsa'           -> 拒绝（正确）
```

修复后（`agent/permission/isolation.py` 合法根从屏蔽区挖出）：

```
TENANT 默认租户 /Users/jiantan/cow
  ls   -> 放行    read -> 放行    bash -> 放行
  bash 'cat ~/.ssh/id_rsa' -> 仍拒绝
TENANT AI启航团队 /Users/jiantan/.cow/tenant-roots/tenants/test15
  ls   -> 放行    read -> 放行    bash -> 放行
  bash 'cat ~/.ssh/id_rsa' -> 仍拒绝
```

测试证据（RED → GREEN）：

```
tests/test_execution_isolation.py::test_blocked_home_does_not_shadow_nested_tenant_root
  修复前 -> FAILED（目标路径位于隔离根之外）
  修复后 -> passed
tests/test_execution_isolation.py + test_state_dir_tenant_containment.py + test_user_personal_memory.py -> 47 passed
tests/test_conversation_tenant_isolation.py + test_tenancy_isolation_acceptance.py +
tests/test_rbac_execution_permission.py + test_tenant_channel_inbound_isolation.py +
test_multi_agent_state_isolation.py + test_project_db_isolation.py -> 53 passed
tests/test_execution_permission_ui.cjs -> pass
openspec validate fix-instance-root-trust --strict -> valid
```

已知既有失败（与本 change 无关，`HEAD` 同样失败）：`tests/test_execution_authorization_fail_closed.py` 两个 legacy-mode 用例、`tests/test_console_i18n_parity.cjs`（`tasks_unavailable_desc` 快照漂移）、`tests/test_sidebar_account_frontend.cjs`。

**运行面提示**：服务端需重启进程后加载新代码，对话中原被隔离拒绝的本租户文件/bash 访问才会恢复。

