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

## Two concrete instances, found by auditing test drift

An audit of every test file the merge touched
(`scripts/migration/find_retargeted_tests.py`) turned two measurement rows into
named gaps in the fork's console. Both are **recorded here, not ported** — they
are feature ports into fork-owned handlers, not merge resolutions, and neither
has a failing test to anchor them (upstream's own tests assert upstream's
module, which is correct for upstream's code).

1. **Search providers.** `tests/test_web_search.py` used to assert that the
   fork console's `ModelsHandler._SEARCH_PROVIDERS` mirrors
   `agent/tools/web_search.PROVIDER_ORDER`; the merge took upstream's file,
   whose assertion imports upstream's `channel.web.api.models`. The tool module
   (shared, merged) now carries `tavily`, `searxng`, `keenable`, while the
   fork's `channel/web/fork/handlers/models.py` still lists the six original
   ids. So the fork's Search panel neither shows nor configures the three new
   backends — including `keenable`'s keyless tier (`keenable_anonymous`) and
   the `anonymous` badge upstream added to the capability card. The fork's
   invariant is currently untested, not merely unmet: retargeting the
   assertion at the fork's handler without porting the providers would fail.
2. **Chat-fallback chain.** Upstream replaced the single fallback entry
   (`model` + `max_switches`) with an ordered chain and migrates old configs
   (`config.py`, `_normalize…` drops the cap; `api/models.py` gains the chain
   handlers). The merge took the chain for the executor and the config layer —
   `tests/test_subagent_fallback_scope.py` was updated to chain vocabulary
   ("walk the chain", "chain position") and upstream's `api/models.py` chain
   code is unaffected. The fork's console handler still reads and writes
   `max_switches` (`fork/handlers/models.py: 731, 1569, 1718-1764`), so the cap
   the user sets there is dropped by the merged config normaliser, and the
   console cannot express a chain of more than one link.

The fork test files that lost coverage to the same refactors (fork's
`test_chat_model_fallback.py` assertions about `max_switches` clamping, and the
search-sync assertion above) are upstream's versions now; the fork's behaviour
is unchanged and untested rather than deleted. `tests/test_direct_addressing.py`
and `tests/test_conversation…`-style imports that the merge silently re-pointed
at `channel.web.api/core` were re-pointed back at the fork's stack — that is
task 3.10, and the audit keeps finding them (3 files this round: search,
direct addressing, and the fallback file above).
