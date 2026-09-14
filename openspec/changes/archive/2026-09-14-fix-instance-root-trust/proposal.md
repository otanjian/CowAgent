## Why

默认租户的 `shared_root` 就是部署实例根（例如 `~/cow`）。`tenant-resource-isolation` 的 home/global 逃逸校验为此保留了一个「工程根豁免」，但该豁免当前取自**默认智能体自己的工作区**。当默认智能体被显式赋予私有工作区（`<实例根>/agents/<id>`）后，豁免根从实例根**缩小**为该私有子目录，于是一个合法安装反而被判为逃逸：

```
tenant 'tnt_xNHlQIA2XP-z6nQG' shared root '/Users/jiantan/cow'
resolves inside the home/global workspace root; refusing to fall back
```

后果是默认租户的共享根**永远无法解析**，所有依赖它的读取全部失败：聊天模型选择器为空（`GET /api/sessions/<id>/settings` 报错，前端静默保留空目录）、侧边栏会话历史「加载失败」、项目与会话设置不可用。同实例下 `shared_root` 位于租户基目录下的租户不受影响，说明这是豁免根选取错误，而非配置错误。

**同源第二处缺陷（执行隔离门禁）**：`agent/permission/isolation.py` 的 `resolve_boundary()` 用完全相同的「默认智能体工作区」单一来源决定是否把 home 加入 `blocked`。默认智能体迁入私有工作区后 home 被整体屏蔽，而 `blocked` 判定是纯包含式的——实例根 `~/cow` 与租户基目录 `~/.cow/tenant-roots/...` 都在 home 之下，于是**本租户自己的共享根被 home 屏蔽区覆盖**，`bash`/`ls`/`read` 对本租户工作区的访问全部被拒（提示「位于隔离根之外」）：

```
blocked = ['/Users/jiantan/.cow/tenant-roots/tenants/test15', '/Users/jiantan', '/Users/jiantan/ai_assistant/cowagent']
ls /Users/jiantan/cow          -> 拒绝 目标路径位于隔离根之外
read /Users/jiantan/cow/AGENT.md -> 拒绝
cat ~/.ssh/id_rsa              -> 拒绝（正确）
```

已用真实身份库复现：`admin`（平台管理员，`authorization_mode=all`，角色门禁放行）在对话中无法读取本租户工作区。这是本次对话「未被授权」报错的真实根因，与上面的 `state_dir` 缺陷同源，故并入本 change 一并修复。

## What Changes

- 把 home/global 逃逸校验的**可信根**从「默认智能体工作区」单一来源，改为三个**已验证**来源的并集：部署实例根（`agent_workspace`，缺省 `~/cow`）、默认智能体工作区、operator 配置的租户数据基目录（`tenant_shared_base` / `COW_TENANT_BASE`）。
- 实例根在缺省智能体布局下与默认智能体工作区重合，行为不变；仅在默认智能体迁入私有工作区后补回被误缩的实例根豁免。
- **执行隔离门禁同源修复**：`agent/permission/isolation.py` 的屏蔽区判定改为「路径落在屏蔽区**且**不在本租户合法根内」才拒绝——合法根（读/写根）从屏蔽区中挖出。屏蔽 home/数据根**不再遮蔽**位于其下的实例根与租户基目录。
- **被拒提示按 kind 区分**：Web `console.js` 与 Desktop `MessageSteps.tsx` 对隔离（`isolation`）拒绝展示隔离边界原因，不再一律套用「执行权限由您的角色决定」的角色文案。
- 不改动跨租户规范目录相等/包含校验、身份库落点校验、路径穿越与软链接逃逸拒绝，也不放宽任何用户可控路径（`~/.ssh`、`identity.db`、其他租户根仍被拒绝）。
- 新增回归测试：默认智能体拥有私有工作区时，默认租户共享根仍可解析、执行隔离仍放行本租户根，而 `~/Documents`、home 凭据路径仍被拒绝。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `tenant-resource-isolation`: 「路径解析不回退全局存储」要求补充「实例根恒为可信根」的约束——即使默认智能体拥有私有工作区，默认租户的实例根 `shared_root` 也必须能解析，不得被判为 home/global 逃逸。
- `execution-isolation`: 「多租户代码执行必须隔离」要求补充「屏蔽区不得遮蔽本租户合法根」——home/global 屏蔽区只拒绝本租户读/写根之外的路径。
- `execution-permission-console`: 「被拒提示仅说明角色授权原因」要求补充「隔离拒绝展示隔离边界原因」。

## Impact

- 代码：`common/state_dir.py`（`_engineering_root` → 多来源 `_trusted_roots`，`_is_home_or_global_escape` 接受可信根集合）。
- 代码：`agent/permission/isolation.py`（屏蔽区判定加入「合法根挖出」语义；home 屏蔽不再依赖单一默认智能体工作区）。
- 代码：`channel/web/static/js/console.js`、`channel/web/static/js/i18n/identity-admin.js`、`desktop/src/renderer/src/components/MessageSteps.tsx`、`desktop/src/renderer/src/i18n.ts`（隔离拒绝专用提示文案）。
- 测试：`tests/test_state_dir_tenant_containment.py`（新增默认智能体私有工作区场景）、`tests/test_execution_isolation.py`（屏蔽区不遮蔽本租户根）、`tests/test_execution_permission_ui.cjs`（提示按 kind 区分）。
- 运行面：恢复 database 模式下**默认租户**的会话设置、模型选择、会话/项目列表读取；恢复租户成员在对话中对**本租户工作区**的文件/bash 访问。
- 不改动：数据库 schema、租户目录布局、`agent_workspace` 配置语义、任何租户数据。
