# 5.1 后半（智能体域写入）：实现前的设计裁定

对应 tasks.md 5.1 的后半与 5.2。本文件记录**动手前**的勘查结论与选定形态，避免按错误
的形状先写一版再返工。尚未开放任何写入路由——原因见 §4。

## 1. 现状（已核）

读面已就位（5.1 前半）：`GET /api/memory`、`GET /api/memory/content` 经
`channel/web/memory_console.py` 解析成 `personal | private_agent | shared` 三个显式目标。

写面在智能体域**不存在**：

| 目标 | 编辑 | 删除 | 清空 |
| --- | --- | --- | --- |
| `personal` | 有：`POST /api/memory/personal`（`save`/`delete`/`clear`/`retry_index`） | 同左 | 同左 |
| `private_agent` | 无领域写入；控制台走**工作区文件接口**（`docWriteFile(relPath, content, mtime)`） | 无 | 无 |
| `shared` | 同上 | 无 | 无 |

`MemoryService.dispatch` 只认 `list` / `content`（`agent/memory/service.py:152`）；
`/api/memory` 路由只登记 `GET`（`channel/web/route_registry.py:205`）。

## 2. 关键约束：不许出现第二套 CRUD

`channel/web/memory_console.py` 的模块 docstring 自己写着：这个接缝
**"is a *delegation* seam — no second memory CRUD, no second index, no second route table"**。

而 `agent/memory/personal.py` 里的写入已经是一整套契约，不只是写文件：

* `_entry_lock` 临界区把「版本校验 + 正文提交 + 索引发布」当作一个原子段（注释里记着
  这正是「正文已存 → clear 删了正文并清了索引 → 原 save 把旧索引写回」的成因）；
* `revision_required` / `stale_revision`（409）、`invalid_content`（400）、`unsafe_path`（403）、
  `capability_disabled`（403）；
* `safe_fs.write_text_atomic` 原子落盘；
* `_begin_mutation` / `_finish_mutation` / `_abort_mutation` + `bump_generation`；
* `index_state == "pending"` 的「内容成功、索引待重试」真实中间态，以及 `retry_pending_index`；
* 清空用集合版本（`_collection_revision`）而不是逐条版本。

所以：**若在 `MemoryService` 里照抄一套 save/delete/clear，就直接违反 5.2**
（「收敛原记忆读写到同一领域服务」）与本模块 docstring 的既有约定。5.1 后半与 5.2 是
**同一次改造**，不能拆成两次实现。

## 3. 选定形态

沿用上一轮已确认的「扩展 `memory_browse` 切片」方向，但实现落点改为**抽共享的领域写入核**：

1. 从 `PersonalMemoryService` 抽出可复用的写入核（版本判定、原子落盘、未完成发布的恢复、
   索引发布/屏蔽与 pending 重试），由**个人域与智能体域共用**；
2. `MemoryService` 只保留「按文件系统定位条目」这一职责，写入委托给共用核——它仍然是
   委派接缝，不新增第二套 CRUD；
3. 授权不新增权限 id：**沿用管理资格**（与 `knowledge.write` 被移除后的既定方向一致），
   在目标范围内**每次写入重验**：
   * `personal` → 本人（既有 owner 规则）；
   * `private_agent` → 私有归属人（`_require_private_owner` 先于任何检查）；
   * `shared` → 租户管理资格（`ObjectScope.allows_agent_memory`）；
   * **写入额外要求「对该记忆根目录有资格的每一个范围都成立」**——数据库形态下智能体的
     记忆根目录**就是租户共享根**（`channel/web/tenant_workspace.py`），私有智能体与共享
     智能体读写的是同一批字节。读面可以容忍（从自己的智能体读到这批字节并不额外授予什么），
     写面不行：见 §6，这条在验收时被实测证明是提权。
4. 只读类别：`dream` / `evolution` 是智能体自己写出来的产物，**两种角色都只读**——
   手工编辑会在下一轮被覆盖，且不是用户意图。写入核按类别拒绝（稳定 code）。
5. 路由：`/api/memory` 增加 `POST`，body 里带 `action`（`save`/`delete`/`clear`），
   与 `POST /api/memory/personal` 的既有形状一致；能力矩阵里把这三个动词以
   `ACCESS_EXECUTE` 登记进 `memory_browse` 的 `open`，并修正该切片「两个方法都是读」的注释。

## 4. 为什么现在没有先写一版

写写入核要动 `personal.py` 的临界区与索引发布逻辑，这是**当前唯一能让个人记忆写入保持
一致性的地方**。先按智能体域的形状写一版、再回来抽共享核，等于把 5.2 的返工提前做一遍，
而且中途会留下一个**已开放但未验证的变更型 HTTP 面**（`POST /api/memory`）——
这比不开放更危险。故本文件只记录裁定，代码按第 3 节的顺序落地，且路由开放与
服务端实现**同一步**完成。

## 5. 已核实：索引屏蔽不会自动发生（这是实现的关键约束）

原来的疑点现在有了确定答案，而且是**不利**的那个：

* `MemoryManager.sync()` 对每个待同步文件做的是
  `storage.delete_by_path(rel_path)` + `save_chunks_batch(...)`，即**按文件替换**；
* 全仓只有一处 `storage.delete_by_path` 调用（`manager.py:484`），**没有任何「扫掉磁盘上
  已删除文件」的清理过程**；
* 因此**从磁盘删掉一个记忆文件，它的索引行仍然留着、仍然可被检索到**；
* 目前唯一阻止这件事的是检索出口处的屏蔽：`manager.py:174-187` 在结果变得可见的唯一位置
  减去 `pending_index_labels()` 里的标签（注释写明「Content deleted through the member
  console must not come back from a stale index row」）；
* 而 `pending_index_labels()` 来自 `agent/memory/personal.py`，是**个人域专属**的待办日志。

结论：智能体域的 `delete`/`clear` **必须**像个人域一样登记待办标签（tombstone），
并且 `manager.py` 的检索出口要读**同一个**共享日志，而不是只读个人域那份。
所以第 3 节的「抽共享写入核」不只是为了不重复 CRUD，也是**唯一能让屏蔽覆盖智能体域**的
做法——另写一套智能体域写入，删除后的内容会继续从旧索引行被检索出来。
这一点必须在写入核实现时一并落地，并由任务 5.3 用「删除后检索不到」的用例验收。

## 6. 落地后的裁定更正：按目标范围的写入规则是提权

第 3 节第 3 条原本按「目标范围」逐条判定（私有智能体 → 归属人可写）。实现完成后按
「先实测、后宣布」复核，实测结果是**这条规则提权**，已改为根目录级别更强的规则。

### 实测（改动前，用真实 `build_web_app()`，成员 alice 对自己的私有智能体 `alice-agent`）

```
SHARED SCOPE SEES: "MEMBER WROTE THIS\n"
member delete status: 200
file exists after member delete: False
shared scope read after delete: 404
```

即：成员经「自己私有智能体」这个范围保存的正文，**共享范围读到的就是它**；成员对该范围
执行 `delete`，**删掉的是共享智能体正在读的那个文件**。原因是 §3 提到的根目录事实——
`_get_workspace_root` 把两种范围都解析到租户共享根，因此
「我的私有智能体的记忆」与「本租户共享智能体的记忆」是同一批字节，「只有归属人可写」
并不构成隔离，反而把租户共享资源的写权限交给了任意成员（可注入共享智能体随后会检索到的
文本）。

### 更正后的规则（已实现）

写入要求「对该根目录有资格的**每一个**范围都成立」——即
`channel/web/memory_console.py:_root_is_writable` 直接要求
`ObjectScope.is_admin`（租户管理资格），因为当前唯一共享该根目录的范围就是租户共享范围，
而它本来就要管理资格。**读过、写不过**：成员的读取路径一行未动。

落在三处，规则只写一次：

* 写入口拒绝：`_require_root_writable`（`write_response` 在类别判定之后、任何变更之前）；
* 读载荷不广告会被拒绝的动词：`_entry_actions(ctx, target)` → `actions.edit/delete`，
  控制台据此渲染按钮（`memoryEntryEditable` / `memorySyncDocButtons`，`clear` 也跟随
  同一个信号，不再自己判一次）；
* 验收双向锁定：`tests/test_memory_console_write.py`
  `test_a_member_write_cannot_be_observed_on_the_shared_scope` 同时断言
  「保存没有落到共享范围」与「删除没有删掉共享文件」——两条失败不同，只测其一抓不到另一条。

### 对 5.1 需求口径的影响（需回写 spec）

5.1 的「本人私有智能体记忆」这一项，在**当前存储形态下不可单独成立**：只要智能体的记忆根
是租户共享根，「本人私有」就不是一个可独立授权的对象。因此本轮交付的是
**读面按范围（含本人私有）+ 写面限管理资格**；让成员能写自己私有智能体的记忆，
前置条件是**每个智能体有独立记忆根**（`_get_workspace_root` 不再一律解析到共享根）。
在那之前放宽为按目标范围判定，等价于重新打开本节实测的提权，不做。
