# 4.3 统一维护、来源保护与引用冲突

本文件记录 `tasks.md` 4.3：编辑 / 配置 / 调试 / 启停 / 删除对「管理员 owner」与
「普通 owner」的**同类对象**使用同一套规则，并补上来源保护与引用冲突。

## 1. 维护权 = 所有权，不依赖逐资源 grant

管理范围由 `auth/object_scope.py:allows_agent(binding, action=MANAGE)` 判定：

* 私有对象 → **只看 owner**，`is_admin` 不参与（非 owner 管理员与任何非 owner 一样被拒）；
* 共享对象 → 需管理资格。

两个 owner 角色对自己的私有对象因此走同一条路径：`_iter_tenant_agents(action=SCOPE_MANAGE)`
读同一名册、`_require_agent_action` 校验同一谓词，页面不因角色切换成另一套字段或表单。

## 2. 来源保护

`_require_deletable_provenance`（`channel/web/web_channel.py:552`）按绑定的 `origin`
拒绝删除系统供应对象（`SUPPLIED_ASSISTANT_ORIGINS`）。`unknown`（迁移前无该列的行）
按「系统制造」处理而不是猜测——猜错的代价是把别人给的助理删掉。

## 3. 引用冲突不再静默解绑（本任务的主要行为变更）

变更前 `AgentAdminService.delete_agent` 会**静默解绑**指向该对象的渠道实例后删除。
规范禁止这种自动改绑，因为它把「删智能体」变成了「悄悄改掉别人渠道的路由」。

现在：

1. 新增 `agent/deletion_guard.py`——唯一一份依赖判定。汇总
   `roster_channel_references`（`team.json`）、`_tenant_channel_references`（身份库
   `tenant_channel_instances`）、运行中任务探针；探针失败**fail-closed**（宁可拒绝删除）。
   `_tenant_channel_references` 支持 `tenant_id=None`（平台级删除跨租户查）。
2. `AgentAdminService.delete_agent(..., require_unreferenced=True)` 在默认路径调用
   `deletion_conflicts`；有冲突则抛新增的 `AgentInUseError`（`code="conflict"`, `status=409`）。
3. 控制台路径 `_require_agent_deletable`（`:590`）把冲突转成 409 并在响应里带上机器可读的
   `conflicts` 列表，界面能指名是哪个依赖挡住了。
4. 补偿路径传 `require_unreferenced=False`，回滚不受此限。

删除成功后由 `release_deleted_agent` 清理默认指针（见 4.6 证据），
`memberships.default_agent_origin` 一并清空。

## 4. 证据

```
.venv/bin/python -m pytest tests/test_agent_lifecycle_unified.py -q -p no:randomly
→ 18 passed
```

`tests/test_agent_lifecycle_unified.py` 是一套走真实 WSGI 应用的端到端回归，按类分组：

* `MaintenanceParityTests`——两种 owner 角色用同样方式编辑/启停自己的对象；所有权本身
  就是授权（无需逐资源 grant）；管理员不能编辑他人私有对象。
* `ProvenanceProtectionTests`——成员与管理员获配的助理都不可删；自建对象 owner 可删。
* `ReferenceProtectionTests`——有活跃渠道引用时删除返回 **409 并指名依赖**，拒绝**不改变**
  绑定与名册；解绑后才放行；管理员对象面对同一规则；`AgentAdminService` 本身也执行该冲突检查。
* `DetachmentTests`——删除后绑定、指向它的用户默认指针都被清除，邻居对象及其默认不受影响。
* `InstanceTemplateTests`——实例默认 `primary` 模板不可删。

配套改动：`tests/test_agent_admin.py` 中旧的「删除即解绑渠道」用例改为两个用例
（`...refuses_while_a_channel_instance_routes_to_it` / `...proceeds_once_the_channel_is_unlinked`）。
