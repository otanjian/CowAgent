# 18 — Upstream's web-handler increments the fork's parallel layer has not absorbed

Task 3.5/3.6 asks whether upstream's *no-conflict* increments were silently
dropped. For the web layer the answer cannot be "the merge carried them": since
Phase 1 the fork implements 64 upstream handlers in
`channel/web/fork/handlers/**` (evidence 03 explains why parallelism was the only
route that does not edit upstream files), so upstream's changes to
`channel/web/api/**` do **not** reach the fork's implementation through a merge.

Tool: `scripts/migration/measure_backend_increment_gap.py` (`ast` on
`upstream/master` = `8f1b19f1` vs the fork point `e5e2a52d`, exact method source
compared, upstream's post-fork-point lines looked up in the fork's parallel
body).

## Measured on this merge

| | methods |
| --- | --- |
| upstream handler methods | 67 |
| **new** since the fork point | **22** |
| changed since the fork point | 45 |
| of those changed, absent from the fork's parallel body | 45 |

Upstream lines with no counterpart in the fork's body: **≈450**, concentrated in
`channel/web/api/models.py` (search/chat-fallback/provider/capability cards,
~240), `scheduler.py`, `pages.py` (asset serving), `config.py`, `sessions.py`,
`channels.py` and `update.py`.

The 22 brand-new methods are features with no fork equivalent at all (search
credentials, chat-fallback chain, capability predictions, the update check
endpoint surface).

## Consequence, stated plainly

This is a **capability gap, not a test failure**: the fork's console keeps
working, but it does not expose what upstream added. Closing it is task 3.5/3.6,
one method at a time, into the fork's parallel handlers and through the fork's
authorization (`_require_*` / `_db_scope`) rather than by copying upstream's
authentication model. The measurement is repeatable, so progress is countable
rather than asserted.

Ported so far (this merge, where the gap was already visible in a failing test):

- the `tool_retrieval` SSE event → `channel/web/fork/runtime.py`;
- channel manager resolution via `common.channel_registry`
  (`_live_channel_manager()`), instead of a `__main__` module lookup →
  `channel/web/fork/handlers/channels.py`, `fork/runtime.py`;
- the global scheduler store model (`AgentScopedTaskStore`) →
  `channel/web/fork/handlers/scheduler.py`;
- the agent dimension of the conversation store →
  `agent/memory/conversation_store.py` (evidence 14).
