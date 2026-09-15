# 3.6 私有维护开放门槛：真实 handler 与文件读写链路验收

日期：2026-09-14
用例：`tests/test_private_resource_acceptance.py`（13 项全绿）
相关：`tests/test_private_agent_file_scope.py`（22 项全绿，含 3.4 新增 11 项）

## 为什么需要这份验收

任务 3.1–3.4 的规则各自有单元测试，但都停在**辅助函数**层。本 change 之前几次
落地的失败模式恰恰是「辅助函数对了，handler 漏了」——`_require_private_owner`
长期只校验请求**自报**的 `agent` 参数，而实际寻址路径归属从未参与判定。
因此本门槛不复测规则本身，而是**经真实 app 打通四个入口**：
`GET /api/workspace/read`、`POST /api/workspace/write`、`GET /api/file`、
`GET /preview/<token>/<name>`。

## 四项必须成立的性质

### 1. 属主放行（读与写）

`test_the_owner_reads_and_writes_its_private_workspace`、
`test_the_owner_downloads_and_previews_its_private_file`

属主 carol 读到自己私有 Agent workspace 的正文（`content == "carols-private-body"`），
并能把改动**写回同一文件**并通过下载与预览两条链路取回。写路径经
`_authorize_db_file_path` 的绝对路径校验，因此「能读不能写」或「能写不能读」都不会被
这份验收漏掉。

### 2. 管理员对私有内容的四种入口全部拒绝

`test_an_administrator_cannot_read_write_download_or_preview_it`、
`test_another_member_is_refused_the_same_ways`、
`test_naming_a_shared_agent_does_not_reach_the_private_path`

| 入口 | 管理员结果 | 断言 |
|------|-----------|------|
| `workspace/read` | 403/404 | 响应不含正文 |
| `workspace/write` | 拒绝 | 磁盘内容逐字节未变 |
| `/api/file` | 403/404 | 响应不含正文 |
| `/preview/<token>` | 404 | 响应不含正文 |

另外两条覆盖绕过手法：换个普通成员结果相同；**声明共享 Agent 但寻址私有路径**
同样拒绝（自报参数只作冲突检测，不作授权来源）。

### 3. 共享资源既有授权不变

`test_a_shared_agent_stays_reachable_for_members_and_admins`、
`test_a_shared_download_and_preview_stay_open`、
`test_releasing_ownership_restores_the_ordinary_surface`

属主/其他成员/管理员三方读共享 Agent 文件均 200 且内容正确；共享文件的下载与
**匿名预览**仍可用（能力令牌对非私有内容仍是完整授权，匿名 iframe 场景未被误伤）；
归属解除（`make_agent_tenant_shared`）后管理员立即恢复普通访问——说明拒绝来自
**归属判定**，而非对私有库的一刀切。

### 4. 身份库故障必须拒绝（本门槛的关键）

`test_the_outage_is_actually_exercised`、
`test_an_identity_lookup_failure_denies_rather_than_allows`、
`test_a_broken_identity_store_does_not_widen_the_workspace_read`、
`test_a_broken_identity_store_does_not_widen_the_preview`

故障注入在 `auth.service.get_identity_service`（handler 真实取用点），而非夹具自有实例——
若注入点错了，管理员本就因「非属主」被拒，测试会**假通过**，故专门加
`test_the_outage_is_actually_exercised` 断言故障确实被触发（`flaky.raised`）。

**本门槛抓出一处真实缺陷**：`_static_path_private_owner` 原先把身份库异常吞掉并
`return None`，而 `None` 的语义是「非私有」——于是**身份库故障会把私有文件变成公开文件**，
预览链路在故障期间直接吐出正文（`b'carols-private-body'`）。已改为抛出
`_PrivateOwnerLookupFailed` 并在 `_preview_consumer_may_read` 中 fail closed：
「未知」与「不可达」是两个相反结论，必须分别报告。

## 判别力验证（变异测试）

对四条关键路径做了变异，逐个确认有用例失败，源码逐字节还原：

| 变异 | 结果 |
|------|------|
| 恢复 `tenant_admin` 文件旁路（3.4 的核心删除项） | CAUGHT |
| 移除 `/preview` 消费期 owner 复检 | CAUGHT |
| 预览归属查询失败改为放行（fail open） | CAUGHT |
| 预览接受任意可解析会话（非属主） | CAUGHT |
| 预览把不可解析身份当作充分条件 | CAUGHT |

## 结论与范围

私有维护（本人读/写/下载/预览）在真实 handler 链路上满足四项门槛，可对成员开放；
管理员对私有内容的四种入口全部拒绝，且拒绝对身份库故障保持 fail-closed。

**未覆盖**（留待对应阶段，不在本门槛内）：本人在**控制台 UI** 上的编辑交互（阶段 8）、
记忆与知识的同类链路（3.5 及阶段 5）、真实渠道入站的本人路由（阶段 7）。
