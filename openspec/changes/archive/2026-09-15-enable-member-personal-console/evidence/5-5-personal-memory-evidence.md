# 5.5 个人记忆写入启用证据

对应 `user-personal-context` 的「成员可管理本人记忆」「个人记忆修改与清空保持索引和任务一致」
与「个人记忆的读写边界与检索入口唯一」三组 requirement。用例文件：
`tests/test_personal_memory_console.py`（30 项，全绿）。

## 1. 落地实现

| 关注点 | 位置 | 说明 |
| --- | --- | --- |
| 个人记忆服务 | `agent/memory/personal.py` | `PersonalMemoryService`：list/read/save/delete/clear + 索引维护 + 作用域版本 |
| 作用域标记 | `agent/memory/personal.py` | `<user_root>/.memory-scope.json`：`generation`、`cleared_at`、`pending_index` |
| 入口 | `channel/web/web_channel.py` | `PersonalMemoryHandler`（GET/POST）、`PersonalMemoryContentHandler`（GET） |
| 路由 | `channel/web/route_registry.py` | `/api/memory/personal`、`/api/memory/personal/content`，策略 `personal` |
| 检索屏蔽 | `agent/memory/manager.py` | `MemoryManager.search` 末尾减去 `pending_index_labels()` |
| 固化版本闸门 | `agent/memory/summarizer.py` | `flush_from_messages` 派发时捕获 generation，`write_daily_summary` 复核 |

存储根与「该私有 Agent 记忆」分离：前者是 `state_dir.user_root()`（当前租户共享根下
`users/<user_id>`），后者仍由 `MemoryService(workspace_root)` 从 Agent 工作区读取。
本入口不接受 `agent_id`，因此两者不是同一个地址空间。

## 2. 需求 → 用例

| 需求场景 | 用例 |
| --- | --- |
| 列表/读取/编辑/删除本人记忆 | `ListingTests`（round trip、只写用户域、Agent 入口不含个人记忆） |
| 管理员或其他租户请求个人记忆 | `IdentifierAndIsolationTests`（路径形状标识被拒、他人不可寻址、同账号跨租户为空、平台管理员看不到他人正文） |
| 两个页面编辑同一条目 | `EditingConflictTests::test_a_stale_revision_is_refused_without_overwriting`、`test_two_pages_saving_the_same_version_produce_one_winner` |
| 删除后检索不返回旧正文 | `IndexConsistencyTests::test_deleted_content_is_not_returned_by_search` |
| 索引清理失败 | `test_a_failed_index_purge_is_reported_and_still_hidden_from_search`、`test_clear_reports_pending_instead_of_success_when_the_index_fails` |
| 清空后旧任务尝试回写 | `ScopeVersionTests::test_a_queued_flush_cannot_restore_cleared_content`、`test_a_flush_after_the_clear_still_persists` |
| 跨智能体可见 | `IndexConsistencyTests::test_the_personal_memory_is_visible_from_another_agent` |
| 入口接线 | `HandlerWiringTests`（list/read/save/conflict/clear/非法标识） |

已有 `tests/test_user_personal_memory.py` 继续固定用户域归属、跨智能体检索过滤与跨租户
结构隔离；本文件在其之上固定**写路径**的新增要求。

## 3. 变异检查（8 项，全部被捕获）

逐个移除一处判决后重跑本文件；仍全绿即为「用例没有钉住该行为」。

| # | 移除的判决 | 结果 |
| --- | --- | --- |
| 1 | `_require_scope` 的缺用户/租户拒绝 | caught |
| 2 | 标识文法 `^(MEMORY\.md\|memory/<name>\.md)$` | caught |
| 3 | `save` 的版本比较 | caught |
| 4 | `delete` 的版本比较 | caught |
| 5 | `clear` 推进作用域版本 | caught |
| 6 | `write_daily_summary` 的 generation 闸门 | caught |
| 7 | `_after_remove` 失败即 pending（fail-closed） | caught |
| 8 | `MemoryManager.search` 减去待重试标签 | caught |

## 4. 边界与已知取舍

- **并发边界**：版本条件在单进程内由 `_entry_lock` 串行化（控制台由单进程服务）。
  跨进程同时写同一用户域不在本切片范围内；文件写入本身是原子替换，不会出现半截正文。
- **嵌入向量**：控制台保存走定向重建索引（`delete_by_path` + 重新分块），不合成向量，
  也不写 `files` 元数据行，因此该 Agent 的下一次 `sync()` 会重新读取该文件补齐向量；
  关键词检索（FTS5/LIKE）当场即可命中新内容。
- **索引清理失败**：内容删除是权威动作，失败的是索引清理；此时返回 `pending`（不是
  success），受影响标签进入待重试记录并在检索入口继续屏蔽，`retry_index` 只重试目标标签。
- **不做的事**：个人记忆的 dreams/evolution 日志不是用户撰写的条目，清空不动它们；
  租户知识与共享 Agent 记忆不在本入口的写入范围。
