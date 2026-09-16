# 5.1 后半（智能体域写入）与 5.2：实现与验收

对应 `tasks.md` 5.1 后半、5.2、5.3。设计裁定见 `5-1b-write-path-design.md`（含落地后
第 6 节的规则更正）。本文件记录**实现落点**与**验收结果**。

## 1. 实现落点

| 关注点 | 位置 |
| --- | --- |
| 共享写入核（不写第二套 CRUD） | `agent/memory/personal.py`：`_index_label(scope=...)` 参数化；新增 `_entry_id_pattern` / `_chunk_identity` / `pending_index_labels_for_root` 三个钩子；`pending_index_labels()` 改为调用它 |
| 智能体域写入核复用 | `agent/memory/service.py`：`MemoryService` 继承 `PersonalMemoryService`，只覆盖存储根、地址文法、索引标签、`_chunk_identity`（`(None, "shared")`）、索引库集合五个钩子；`_require_writable_entry` 拒绝 `dreams`/`evolution` |
| 删除后的索引屏蔽 | `agent/memory/manager.py`：检索出口除 `pending_index_labels()` 外同时读取 `pending_index_labels_for_root(workspace)`，智能体域删除不再从旧索引行回来 |
| 写入口与授权 | `channel/web/memory_console.py`：`write_response(ctx, params, action)` → `resolve_target`（拒绝 `scope=personal`）→ 类别只读判定 → `_require_root_writable` → `MemoryService` |
| 读载荷不广告被拒动词 | `channel/web/memory_console.py`：`_entry_actions(ctx, target)` → 列表行与正文载荷的 `actions.edit/delete`；`read_only` 仍按类别 |
| HTTP 面 | `channel/web/route_registry.py`：`POST /api/memory/save|delete|clear`，门为 `S("memory_browse", "<action>")`；`channel/web/web_channel.py`：`_MemoryWriteHandler` 基类 + 三个 handler |
| 能力矩阵 | `auth/capability_matrix.py`：三个动词以 `ACCESS_CONFIG` 登记进 `memory_browse`（写资源用 `config`，不是 `execute`；不新增权限 id） |
| 前端接线 | `channel/web/static/js/console.js`：`memoryDocRead`/`memoryDocWrite` 取代 `docReadFile`/`docWriteFile`；`memoryEntryEditable` 把「类别只读」与「范围只读」折算成一个标志；`memorySyncDocButtons` 的 `delete`/`clear` 跟随服务端 `actions`；`chat.html` 增加两个按钮 |
| 三语文案 | `channel/web/static/js/i18n/core.js`：`memory_delete*` / `memory_clear*` / `memory_index_pending` |

## 2. 验收结果

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 写入验收（后端） | `pytest tests/test_memory_console_write.py` | 19 passed |
| 写入验收（前端） | `node tests/test_memory_write_frontend.cjs` | 14 passed |
| 读面/能力矩阵/个人域协议/菜单门禁回归 | `pytest tests/test_memory_console_scope.py tests/test_capability_matrix.py tests/test_personal_memory_scope_protocol.py tests/test_menu_grant_enforcement.py` | 79 passed |

覆盖到的事实（每条都有对应断言，不是状态码同义反复）：

* 授权先于变更：四种拒绝路径（非归属人、管理员对他人私有、成员对自己私有、成员对共享）
  在 tripwire 下均未触达 `MemoryService.save/delete` 与索引维护；
* 版本条件：`revision_required`（盲写既有条目）、`stale_revision`（两页面竞争，后者败）；
* 删除／清空：删除后磁盘无文件、列表无行；清空只清 `memory` 类别，`memory/dreams/**` 不动；
* 只读类别：`dream`/`evolution` 两种角色同拒（类别判定先于范围判定，故理由一致）；
* `scope=personal` 打这份接口被拒（个人域保留自己的写入面）；
* 索引真实中间态：purge 失败时响应 `pending`/`index_pending`，`pending_index_labels_for_root`
  记录该标签，且 `MemoryManager.search` **实测不返回**已删正文；purge 成功后不留永久屏蔽；
* 页面不广告被拒动词：成员拿到的正文与列表行的 `actions` 全为 `false`，管理员为 `true`
  （两个方向都断言，避免「恒为 false」也能过）。

## 3. 验收抓到并修掉的问题：按目标范围的写入规则是提权

实现完成后按「先实测、后宣布」复核「成员写自己私有智能体的记忆」，实测结论与设计预期相反：

```
SHARED SCOPE SEES: "MEMBER WROTE THIS\n"     # 成员经私有范围写入的正文，共享范围读到
member delete status: 200
file exists after member delete: False       # 成员删掉了共享智能体正在读的文件
shared scope read after delete: 404
```

因为数据库形态下智能体记忆根**就是租户共享根**，私有范围与共享范围是同一批字节，
「只有归属人可写」不构成隔离，反而把租户共享记忆的写权限交给任意成员（可注入共享智能体
随后会检索到的文本）。**该规则未交付**，改为根目录级别的更强规则（
`_root_is_writable`：要求管理资格），并把这条路径固化成回归用例
`test_a_member_write_cannot_be_observed_on_the_shared_scope`（保存与删除两个方向同时断言）。
规则更正记在 `5-1b-write-path-design.md` §6。

## 4. 未交付项与前置条件（需回写 spec）

5.1 原口径中的「本人私有智能体记忆可写」**未交付**，且当前存储形态下不可单独成立：
只要智能体的记忆根是租户共享根，「本人私有」就不是可独立授权的对象。已交付的是
**读面按范围（含本人私有，一行未改）+ 写面限管理资格**。

前置条件：**每个智能体有独立记忆根**（`_get_workspace_root` 不再一律解析到共享根）。
届时把 `_root_is_writable` 放宽为按目标范围判定才是安全的；在那之前放宽等于重新打开
§3 的提权。成员的记忆写入能力并未缺失——本人的用户记忆走既有的
`POST /api/memory/personal`（版本化、原子、索引发布同一套契约）。
