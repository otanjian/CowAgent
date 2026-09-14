## Why

database 身份模式下，文件服务的**绝对路径**授权只证明「该路径落在调用者租户的某个允许根之内」，不校验该路径**所属 Agent** 的私有/共享归属。于是同租户的普通成员可以跨成员读取他人**私有** Agent 的文件。

根因在两处代码，都是「只按租户过滤」：

- `_db_file_serve_roots()`（`channel/web/web_channel.py:3406`）把本租户**全部**绑定 Agent 的 workspace 都列为允许根，不区分该 Agent 是共享还是私有；
- `_require_private_owner()`（`channel/web/web_channel.py:1069`）只作用于请求里**显式声明的** `agent` 参数，从不作用于「路径实际落在谁的 workspace」。`_workspace_request_scope()` 校验的是 `agent` 参数，而不是被寻址文件。

实测复现（普通成员 `bob`，非 `tenant_admin`；同租户内 `private-agent` 的 `private_owner_user_id` 为 root）：

```sh
# 声明一个共享 Agent 通过绑定/属主校验，路径却指向同事的私有 Agent workspace
$ curl -s "$HOST/api/workspace/resolve?agent=shared-agent&path=/…/private-agent/carols-secret.txt"
HTTP/1.1 200 OK
{"status": "success", "file": {"path": "../private-agent/carols-secret.txt",
 "raw_url": "/api/file?path=/…/private-agent/carols-secret.txt", "preview_url": "/preview/…"}}

# 响应里的 URL 不带 agent_id，FileServeHandler 因此完全跳过属主校验
$ curl -s "$HOST/api/file?path=/…/private-agent/carols-secret.txt"
HTTP/1.1 200 OK
hello-private-carol
```

`read`/`write` 目前未被同样利用，但那只是「顺带」被 `_editable_target()` 的 `(svc, system)` 回落循环挡住（绝对路径必须能 `to_workspace_rel` 到两者之一，否则 404），并非做了属主校验；`_workspace_system_service()` 的回落在「本租户无共享 Agent」时也会解析到他人私有 Agent。

这不是新增需求：`tenant-resource-isolation` 既有要求已写明「共享回退 SHALL 继续校验实际资产来源的私有/共享归属，不能只凭消费 Agent 的共享属性开放另一 Agent 的私有资产；来源归属缺失 MUST 拒绝读取」。本 change 是让**文件服务数据面**对齐这条既有规范。

## What Changes

- **授权携带归属**：被寻址资源的授权在证明租户包含关系之后，SHALL 解析该资源**所属 Agent**，并对它执行与显式 `agent` 参数**相同**的私有属主校验（属主本人或 `tenant_admin` 放行）。该校验 SHALL 覆盖**绝对路径、相对路径（含租户共享根之下的目录嵌套）与服务端签发 URL** 三种寻址方式，MUST NOT 因「路径落在调用者租户共享根内」而跳过。
- **目录列举与搜索按归属过滤**：`tree`/`search` SHALL NOT 返回无权读取的私有 Agent 目录名或路径存在性，避免「面板能直接走进他人私有 Agent 目录」。
- **归属唯一性失败关闭**：路径同时落在多个不同 Agent 的 workspace 之下（嵌套或配置退化）时，系统 MUST 拒绝，不得任选其一放行；归属无法解析时 MUST 拒绝。
- **`/api/file` 由被寻址资源派生归属**：不依赖 URL 是否携带 `agent_id`，从路径解析所属 Agent 执行属主校验；请求自报的 `agent_id` 只作冲突检测，不作为授权来源。
- **工作区面板一致化**：`resolve`/`read`/`write` 的绝对与相对分支统一使用同一归属校验，消除「靠回落循环顺带挡住」的不一致；`_editable_target()` 的 state_root 回落目标 SHALL 与显式 `agent` 参数同属主校验一致。
- 平台根语义不变（仍仅平台管理员，保留 `platform.file.read` 审计）；`/preview` 令牌语义不变（令牌是能力授权，但其签发路径已由上面的 resolve 归属校验收敛）。
- 保持既有测试接缝：`_db_file_serve_roots(ctx)` 的扁平根列表契约不变（`tests/test_platform_file_browsing.py` 会 patch 它）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `platform-file-browsing`: 绝对路径与被寻址资源的文件服务授权 SHALL 按「所属 Agent」校验私有/共享归属，MUST NOT 仅凭调用者对租户的成员资格放行同租户其他成员的私有 Agent 资产；归属不唯一时 MUST 失败关闭。
- `tenant-resource-isolation`: 「个人与共享资源有明确边界」的归属校验扩展到文件服务数据面——来源归属校验 SHALL 覆盖路径/资源授权，与列表、搜索、记忆读取使用同一规则。

## Impact

- 后端：`channel/web/web_channel.py`（新增根→所属 Agent 的归属解析；`_authorize_db_file_path` 在租户判定后执行属主判定；`FileServeHandler` 与 `WorkspaceResolveHandler`/`WorkspaceReadHandler`/`WorkspaceWriteHandler`/`_editable_target` 统一接入）。
- 测试：新增跨成员私有 Agent 的绝对路径用例（`resolve` / `read` / `write` / `/api/file`，以及 owner 与 `tenant_admin` 仍可读、共享 Agent 与租户共享根不受影响、归属不唯一时失败关闭）；扩展 `tests/test_platform_file_browsing.py`、`tests/test_console_workspace_transport.py`。
- 不改动：`channel/web/route_registry.py` 的路由策略（本 change 不新增 route-level `permission`，租户仍是隔离边界）、`WorkspaceService` 的布局与冲突语义、`/preview` 能力令牌、平台根审计动作名。
- 相关但不在本 change 范围：同一 `shared_root` 下**用户私有项目目录**之间的路径猜测（`docs/superpowers/specs/2026-09-09-user-private-projects-database-mode-design.md` 已记为 pre-existing gap），需另行收敛 serve roots，本 change 不改变其行为。
