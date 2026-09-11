# Evidence — fork-decoupling-and-tenant-hardening

Every entry below is a claim that was **executed and observed**, not a design
intention. Commands are copy-pasteable from the repository root; the project's
interpreter is `.venv/bin/python`.

Convention: `PYTHONPATH=.` (or the `scripts/` entry point that inserts it) is
required because `channel/` is a namespace package resolved from the repo root.

---

## 0. Gate status

- **In-flight change archived**: `scan-onboarding-and-inbound-anchor` was
  archived as `openspec/changes/archive/2026-09-11-scan-onboarding-and-inbound-anchor`.
  Its `external-identity-binding` delta carried a `MODIFIED` block that belonged
  to `tenant-resource-isolation`; the archiver failed with
  `external-identity-binding MODIFIED failed for header "### Requirement: 首期消费者按明确入口允许或关闭" - not found`.
  The block was moved to the correct capability and the archive completed, so
  `auth/http_policy.py` and the tenant-channel write surface were free before
  this change started editing them.
- **Requirement baseline**: `openspec/specs/` is authoritative. No PRD number is
  used as a live baseline; historical PRD references in
  `openspec/changes/archive/**` are left untouched.
- **Import-time check**: `openspec validate fork-decoupling-and-tenant-hardening --strict`.

---

## 1. Group 5 — execution authorization and isolation are fail-closed (P0)

Audit finding S6: two paths failed *open*.

| Path | Before | After |
|---|---|---|
| `agent/protocol/agent_stream.py::_resource_tool_denial` — identity service raised | logged a warning, continued (`return None`) | denies, warns, records |
| `agent/permission/isolation.py::isolation_decision` — identity unresolvable | `Decision(True)` for non-`CODE_TOOLS` | denies for every tool class |

Guard mechanism: `common/security_events.py` (`record_denial`) increments a
process counter, emits a structured warning, and best-effort persists through
`auth.audit.AuditStore`; a failing audit store never changes the denial outcome.

```console
$ PYTHONPATH=. .venv/bin/python -m pytest \
    tests/test_execution_authorization_fail_closed.py \
    tests/test_execution_isolation.py \
    tests/test_identity_boundary_contract.py -q
```

Observed: all pass. The same files were RED before the implementation (8 new
failures), which is the reproduction of the fail-open behaviour.

Identity propagation across the boundaries that could lose it was verified
rather than assumed: `runtime_identity.submit`/`wrap` use `copy_context()`,
`ChatChannel._handle` rebuilds the identity with `use_identity`, and the
parallel tool runner and subagent runner both use `copy_context()`.

## 2. Group 4 (core) — session/message and Agent file IDOR closure (P0)

- New `_require_session_scope()` in `channel/web/web_channel.py` unifies the
  three checks (tenant binding, visibility, durable ownership).
- `SessionDetailHandler.DELETE/PUT`, `SessionTitleHandler.POST`,
  `SessionClearContextHandler.POST`, `MessageDeleteHandler.POST`,
  `AgentCoreFileHandler.GET/PUT`, `AgentAvatarHandler.GET/POST` now run inside
  `_db_scope()` and go through the guards.
- The handlers swallowed guard rejections: `except Exception` returned a
  `200 OK` JSON body, so a `403` never reached the client. `except web.HTTPError: raise`
  was added first, matching the 20 existing occurrences of that pattern.
- `_get_workspace_root` (decision D3) now answers 403 in database mode when the
  resolved `tenant_id` is empty instead of falling back to the global default
  Agent workspace.

```console
$ PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_idor_closure.py -q
```

## 3. Group 2 — one route source of truth + three-leg coverage invariant

### 3.1 The measured defect (before)

```console
$ git show origin/rdai~1:channel/web/web_channel.py | grep -c "'Handler',"   # 115 patterns
$ git show origin/rdai~1:auth/http_policy.py | grep -c '^    "/'            # 110 patterns
```

The 5 patterns present only in the URL table were **not** merely unpoliced.
`_match_policy` returns `(None, False)` — "unknown URL" — for a path absent from
`ROUTE_POLICY`, and `enforce_http_policy` then calls `handler()` unconditionally
so that web.py's own 404 handling is preserved. Because `web.application` *did*
have the route, the request reached the handler with only its own
`_require_auth()`:

| Pattern | Handler | Reached via |
|---|---|---|
| `/admin` | `ChatHandler` | `_WEB_URLS` only |
| `/api/identity/administered-tenants` | `IdentityAdministeredTenantsHandler` | `_WEB_URLS` only |
| `/api/scenes` | `ScenesHandler` | `_WEB_URLS` only |
| `/api/scenes/activate` | `SceneActivateHandler` | `_WEB_URLS` only |
| `/api/scenes/workbench/import` | `SceneWorkbenchImportHandler` | `_WEB_URLS` only |

The same mechanism hid two more defect classes, found by the third leg
(handler introspection) rather than by reading the tables:

| Defect | Detail |
|---|---|
| dead registration | `ROUTE_POLICY` declared `GET /api/sessions/(.*)`; `SessionDetailHandler` implements only `PUT`/`DELETE` (upstream `origin/master` is identical). The gate let the GET through and web.py answered 405. The frontend only ever uses `PUT` on that path. |
| unregistered implementation | `POST /api/weixin/qrlogin` (documented status poll), `POST /api/todos` (create), `PATCH /api/todos/{id}` (version-cas edit) are implemented and documented but were unregistered, so the completeness gate answered 405. |

### 3.2 Reproduction and result

```console
$ .venv/bin/python scripts/check-route-coverage.py
route-coverage: 115 routes (65 upstream, 50 fork), 147 method entries
OK
```

Negative control (the gate must fail, not warn):

```console
$ PYTHONPATH=. .venv/bin/python - <<'PY'
import sys, importlib.util
sys.path.insert(0, '.')
from channel.web import route_registry
bad = route_registry.RouteEntry("/probe", "HealthHandler", "fork:test",
                                {"POST": route_registry.P("tenant")})
orig = route_registry.check_route_coverage
route_registry.check_route_coverage = lambda ns, routes=None: orig(ns, [bad])
spec = importlib.util.spec_from_file_location("crc", "scripts/check-route-coverage.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sys.argv = ["crc"]
raise SystemExit(m.main())
PY
route-coverage: 115 routes (65 upstream, 50 fork), 147 method entries
FAILED with 2 violation(s):
  - route '/probe' registers POST but HealthHandler does not implement it
  - handler HealthHandler implements GET but no route registers it
# exit status 1
```

### 3.3 Equivalence with the frozen baseline (migration is behavior-preserving)

`scripts/route-baseline.txt` was frozen before the migration, extended
append-only with a `RESOLVED` section for the 5 gaps plus the 4 third-leg
findings, and is parsed by
`tests/test_route_registry.py::FrozenBaselineEquivalenceTests`. The test asserts,
for every historical entry that is not `UNREGISTERED`/`REMOVED`:

- the derived policy equals the recorded policy, and
- the recorded permission equals the derived permission.

Method-entry arithmetic: 140 (frozen) + 8 new − 1 removed = **147**, matching the
gate script output above.

```console
$ PYTHONPATH=. .venv/bin/python -m pytest tests/test_route_registry.py \
    tests/test_http_policy.py tests/test_web_navigation_mode.py -q
56 passed
```

First-match semantics are pinned too: `OrderingTests` builds a concrete sample
path from every pattern and asserts `_match_policy` resolves it to that route's
own entry, so an accidental reorder (e.g. `/api/todos/(.*)` before
`/api/todos/summary`) fails instead of silently changing authorization.

### 3.4 D1's benefit boundary (task 2.12)

**D1 does not reduce the measured conflict count in `channel/web/web_channel.py`.**
The 3 measured hunks there are the forked `upload_file`/`post_message` signatures
and upstream's new `_import_local_file`, none of which involve the URL table.
D1's benefit is:

1. **security** — the 5 gate-bypassing routes are gated, and an unlisted route
   can no longer be reached through the "unknown URL" path;
2. **maintainability** — the two hand-maintained lists cannot drift, and a
   lost/mis-bound route or an unregistered handler method fails the gate at
   merge time instead of at runtime.

Any claim that D1 improves mergeability of `web_channel.py` must not be made.

### 3.5 Upstream routes the fork currently lacks (recorded, not fixed here)

Diffing the registry against `origin/master`'s URL table shows **8 upstream
routes absent from the fork**, which a future merge will (re)introduce:

```
/api/scheduler/create, /api/scheduler/instances, /api/scheduler/recipients,
/api/scheduler/runs, /api/scheduler/runs/delete, /api/scheduler/runs/detail,
/api/sessions/(.*)/compact_context, /api/sessions/(.*)/context_usage
```

They are deliberately **not** added by this change: they belong to upstream work
the fork has not taken, and adding them would require their handler classes.
They are recorded here so the group 9 sync report treats their reappearance as
expected drift rather than a regression.

---

## 4. Group 3 — the route policy table became a real gate (P0)

### 4.1 The measured defect (before)

`enforce_http_policy` classified a route and then called `handler()`. For
`tenant`/`platform` routes it did **no** context resolution of its own, so the
rest of the gate was only as strong as each handler's own guard. `/api/logs`
(`LogsHandler.GET`) called `_require_auth()` and nothing else: in database mode a
request with a valid session cookie but **no** `X-Tenant-ID` reached the handler
and began streaming `run.log` — tenant-scoped operational data served without a
tenant selection. The same shape applies to `/api/logs/download`.

Ordering was also wrong at the API boundary: an anonymous `tenant` request was
answered 401 by the handler, although the documented contract is "no selection is
a 400" (selection is resolved before the session).

### 4.2 Reproduction and result

`tests/test_http_gate.py` is the executable claim. Unit level (spy handler proves
the handler is never entered) and integration level (the real `build_web_app`
processor):

```console
$ PYTHONPATH=. .venv/bin/python -m pytest tests/test_http_gate.py -q -p no:randomly
23 passed
```

The integration case that flips: `/api/logs` with a valid session cookie and no
`X-Tenant-ID`.

| | before | after |
|---|---|---|
| `GET /api/logs` (session, no tenant) | handler reached, SSE stream | `400 Bad Request`, `{"code":"missing_tenant"}` |
| `GET /api/logs` (tenant header, no session) | 401 | `401 Unauthorized` |
| `GET /api/platform/users` (anonymous) | 401 from handler | `401 Unauthorized` from the gate |
| `GET /api/tenant` (valid context) | 200 | `200 OK` (gate cache reused by the handler) |

### 4.3 What the gate is allowed to decide (task 3.5/3.8)

The gate decides **context existence + identity domain + the route's declared
permission**, and nothing else. Object-level ownership stays in the handler, and
the handler now runs on exactly the context the gate resolved (published through
`auth/runtime.py::gate_context_scope`, keyed by `require_tenant`, reset by token
so a pooled worker thread cannot leak one request's identity into the next).
`GateIntegrationTests::test_gate_does_not_replace_object_level_ownership_checks`
spies on `_require_session_scope`: the gate admits the request, the handler still
refuses, and the context it refuses on *is* the gate's context (identity check,
not equality of a re-read).

### 4.4 The one explicit exemption: `GET /stream`

`/stream` is a `tenant` route, but a native `EventSource` reconnect cannot send
`X-Tenant-ID`; the handler derives the tenant from the recorded owned request
(`web_channel._stream_identity_scope`, which also rejects a conflicting selection).
Rather than hiding a path special-case in the gate, the registry declares it:
`P("tenant", ..., tenant_from_resource=True)`. The gate then authenticates the
caller without demanding a selection. Pinned by
`test_route_deriving_its_tenant_from_the_resource_skips_selection`,
`test_resource_derived_tenant_still_requires_a_session`,
`test_route_registry.py::test_stream_declares_its_tenant_is_resource_derived`, and
the pre-existing `test_web_chat_boundary.py` reconnect suite.

### 4.5 Staged rollout (task 3.7)

Deterministic failures (400/401/403) are enforced immediately — that is the
security hole. Only an **unexpected** resolution failure (identity store
unreachable) is deferred: logged, counted, and passed to the handler, with
`config.http_policy_gate_fail_closed=true` switching it to 503 without entering
the handler. `GateStagedRolloutTests` pins all three states, including the
inverse control that the switch must not soften a deterministic 403.

### 4.6 Intentional behaviour changes to existing tests

Three `test_http_policy.py` assertions encoded the old ordering ("anonymous
database request to a tenant route is 401"). They were rewritten to assert **both**
steps of the new, documented order — 400 with no selection, 401 with a selection
and no session — which is stricter than the single `startswith("401")` they had.
No assertion was relaxed.

---

## 5. Regression evidence (cumulative)

Baseline for group 3 is the group-2/4/5 checkpoint (`24321b90`) **with this
machine's `./config.json` present**. The comparison is done on the sorted
`FAILED` set, not on the counts, so a swap of one failure for another cannot be
hidden.

| Point in time | failed | passed | new failures vs baseline |
|---|---|---|---|
| baseline `24321b90` (+ real `config.json`) | 33 | 2533 | — |
| after group 3 | 31 | 2542 | **none** |

Two baseline failures are *fixed* by this work rather than swapped:
`test_consumer_closure_acceptance::test_helper_defaults_to_legacy_when_key_missing`
(by the config-restore fix in §5.1.2) and
`test_subagent.py::test_the_repo_ships_a_guide_that_documents_the_real_format`,
which only fails inside a `git worktree` (it reads a gitignored asset directory)
and passes in the real checkout — an artefact of the baseline method, not a
result of this change.

```console
$ git worktree add -f /tmp/cow-baseline 24321b90 && cp config.json /tmp/cow-baseline/
$ cd /tmp/cow-baseline && PYTHONPATH=. .venv/bin/python -m pytest tests -q -p no:randomly
$ cd - && PYTHONPATH=. .venv/bin/python -m pytest tests -q -p no:randomly
$ rg -o '^FAILED .*' <each run> | sed 's/ - .*//' | sort -u > /tmp/{base,cur}.txt
$ comm -13 /tmp/base.txt /tmp/cur.txt   # new failures against baseline -> empty
```

### 5.1 Two baseline mistakes this comparison had to correct

The first baseline run was wrong twice, and both mistakes are worth recording
because they are the kind that makes a regression check lie.

1. **The baseline worktree lacked `./config.json`.** `config.json` is
   gitignored, so a worktree does not carry it, and `load_config()` then reads
   the bundled template (`identity_mode: legacy`) instead of this machine's file
   (`identity_mode: database`). Four order-dependent tests
   (`test_consumer_closure_acceptance.py` and `test_todo_service.py` ×2,
   `test_tenant_create_containment.py`) looked like new group-3 regressions and
   were not. Copying `config.json` into the baseline worktree made the sets
   comparable.
2. **A test was rebinding the process-global config.** The root cause of those
   four is `tests/test_chunker_version.py::TestSyncStampsVersion._make`, which
   calls `config.load_config()`; that rebinds the global `config` from the
   developer's `./config.json`, so every later test in the session inherits
   `identity_mode=database` in addition to whatever it intended to test. It is
   pre-existing, but group 3 makes ambient mode *behavioural* — the gate now
   reads it — so an unseen global flip can change unrelated test outcomes. That
   class now snapshots and restores `config.config` in `setUp`/`tearDown`, which
   removes the cross-test leak and makes the local suite match CI. With the leak
   removed, all four pass on their own terms (and `test_scenes_api.py` needed its
   own fix, below).

### 5.2 Behaviour changes to existing tests, all strictly stronger

- `tests/test_http_policy.py`: three assertions encoded the old ordering
  ("anonymous tenant request is 401"). Rewritten to assert both steps of the new
  order — 400 without a selection, 401 with a selection and no session.
- `tests/test_scenes_api.py`: these handler tests stub the legacy console
  password and predate multi-tenancy, so they now pin the *gate's* mode to
  legacy; previously they passed only because the ambient mode happened to be
  legacy. `web_channel.conf` is deliberately untouched, so the handlers behave
  exactly as before and only the gate is neutralised. The tenant-gate contract
  for these routes is asserted in `tests/test_http_gate.py`.
- `tests/test_agent_web_management.py` and `tests/test_scheduler_web_update.py`
  (from group 2): rewritten from source-text greps to assertions on the derived
  `_WEB_URLS` binding, a stronger contract.

No assertion was relaxed anywhere; the two source-grep rewrites and the
`test_http_policy` rewrite each assert more than before.

## 6. Group 6 — the conversation store's schema is composed, not literal

The store's merge conflict was structural: upstream widens the primary key to
``(agent_id, …)`` while the fork adds an ``owner`` tenancy column, and neither
side can be applied to the other without editing the same literal. Decision 0.1
solved it by composing the table instead: each dimension contributes columns,
key columns, unique sets and indexes, and `agent/memory/conversation_schema.py`
composes them mechanically.

What the code now guarantees:

- **DDL is derived.** `agent/memory/conversation_store.py` no longer holds a
  `CREATE TABLE …` literal for the conversation tables; `_DDL` is
  `conversation_schema.build_ddl()`. A merge that changes one dimension's spec
  cannot silently drop the other dimension's columns, because there is no
  literal to conflict on.
- **Migration compares the live schema, not a literal.** Column additions come
  from `schema_seam.plan_column_migrations()` and the key change from
  `schema_seam.rebuild_key_constraints()`, so an existing single-key database is
  rebuilt in place and the pre-rebuild table is kept under a documented suffix
  and restorable via `rollback_key_constraints()`.
- **Scoping is one clause, not four near-identical ones.**
  `dimension_clause()` is the only place a read filter is built, so a new query
  path cannot forget the tenant dimension.

Evidence, `tests/test_conversation_schema_seam.py` +
`tests/test_conversation_tenant_isolation.py` + `tests/test_memory_storage_tenant_scope.py`:

```console
$ .venv/bin/python -m pytest tests/test_conversation_schema_seam.py \
    tests/test_conversation_tenant_isolation.py \
    tests/test_memory_storage_tenant_scope.py -q
# a fresh store gets the composed (composite) key; an existing single-key store
# is migrated in place with every row preserved and can be rolled back; a
# tenant-scoped read cannot see another tenant's session, message or chunk; a
# legacy (unattributed) row is invisible to a tenant scope and readable by an
# unscoped read; backfill attributes owner-known rows from membership and never
# guesses for an ambiguous owner.
```

## 7. Group 7 — default Agent, RBAC and quota are fail-closed

| Requirement | Before | After | Evidence |
|---|---|---|---|
| A tenant's default Agent must be usable | A dangling `default_agent_id` (unbound or disabled) was returned as-is | logged and skipped, then a deterministic fallback inside the same tenant; nothing usable ⇒ 403, never the process global | `tests/test_default_agent_fail_closed.py` |
| A role binding cannot cross tenants | application-layer convention only | `membership_roles.tenant_id` + composite FKs `(tenant_id, membership_id)`/`(tenant_id, role_id)`; `roles`/`memberships` gain `UNIQUE(tenant_id, id)` | `tests/test_rbac_tenant_constraints.py::CrossTenantEdgeTests` |
| A resource grant cannot name another tenant's role | application-layer validation | `role_resource_grants.tenant_id` + composite FK; the writer derives the tenant from the role rather than taking it as a parameter | same file, `CrossTenantGrantTests` |
| Quota rows must name a real tenant | no FK | `quota_limits`/`quota_usage` reference `tenants(id)`; rows for an unknown tenant are rejected | same file, `QuotaTenantTests` |
| A broken meter must not become free usage | storage error surfaced as 500 (fail-closed) but with no test | unchanged default, now pinned by test; an explicit `quota_fail_open: true` is the only relaxation and it logs the bypass | same file, `ConsumeQuotaFailClosedTests` |
| Tenant bootstrap logic must not drift | two near-duplicate seeding blocks (`bootstrap`, `create_tenant`) | one `_seed_tenant_defaults()` used by both | `auth/service.py` |

The migration (`auth/store.py::_migration_11`) is deliberately *deriving*: the
tenant of an existing edge comes from its parents, an edge whose parents
disagree is dropped rather than re-attributed (it is the corruption the
constraint exists to prevent), and `PRAGMA foreign_key_check` on the four
rebuilt tables is the post-condition — a database that somehow ends up
inconsistent fails the migration instead of booting with the invariant broken.

RED then GREEN, measured on the same machine and selection (see §5 for the
method):

```console
$ .venv/bin/python -m pytest tests -q -k "identity or tenan or rbac or member or role or quota or grant or admin" \
    --deselect tests/test_identity_self_context.py
# before the implementation: 11 failed (the 13 new constraint tests, minus two
# that assert service behaviour the old code already had) + the pre-existing
# order-dependent test_session_idor_closure failure
# after:                      1 failed (that same pre-existing order-dependent
# failure, reproduced identically with the change stashed)
```

## 8. Group 8 — fork logic out of upstream core files

### 8.1 Inventory (task 8.1)

Every fork-specific branch that still lives in an upstream-owned file, with the
seam that removes it. "Upstream-owned" means the file exists in `master` with
its own history; fork-only capabilities (`auth/**`, `scenes/**`,
`channel/web/identity_admin*`) are not listed because upstream never edits them.

| File | Fork content | Lines (pre-seam) | Seam | State |
|---|---|---|---|---|
| `channel/web/web_channel.py` | `upload_file` / `post_message` / `cancel_request` / `poll_response` signatures widened with fork kwargs | `2153`, `2283`, `2692`, `2771` | request-scoped authorized target (`auth/runtime.py::authorized_target_scope`), published by the handler that already authorized it | **done** (8.2/8.3) |
| `channel/web/web_channel.py` | tenant scope + refusal inside `_get_workspace_root` | `1100–1121` | `channel/web/tenant_workspace.py::resolve_tenant_workspace_root(database_mode=…)`, called once | **done** (8.1/8.3) |
| `channel/web/web_channel.py` | additive authorization helpers (`_db_scope`, `_require_*`, `_authorize_chat_session`, …) | `343–1075` | additive functions only (no upstream body edited); move to a fork module at the next upstream touch of that region | open, recorded |
| `app.py` | identity-mode guard inline in the boot sequence | `595–630` | `common/startup_hooks.py` + `run_startup_hook` | **done** (8.9/6.11) |
| `agent/memory/conversation_store.py` | hardcoded DDL + tenancy/agent scoping | whole `_DDL`/`_migrate`/query builders | `agent/memory/conversation_schema.py` composition; `dimension_clause()` | **done** (6.1–6.11) |
| `agent/memory/storage.py` | `tenant_id` column + filter in the memory index | `97`, `114`, `328–345`, `_scope_filter` | `ambient_tenant()` + a single `_scope_filter`; no new filter sites | **done** (6.10) |
| `agent/tools/scheduler/integration.py` | task fire identity resolved inline | `110–129`, `180` | `agent/tools/scheduler/identity.py::execution_identity` (one resolver) | **done** (8.15/8.16) |
| `channel/channel_instances.py` | fork credential sets next to upstream type labels | whole registry | partitioned blocks, human discipline (D4b) | **done** (8.12/8.13) |
| `channel/web/static/js/console.js` | fork view dispatch + all language dictionaries | i18n blocks, ~13 dispatch sites | `static/js/i18n/*.js` namespaces merged at load; view registry | **done** (8.5/8.6/8.7) |
| `channel/web/chat.html` | fork-only markup and script tags | script block | mount points (`[data-fork-fragment]` + `fragments.js`) | **done, scoped** (8.8) |
| `tests/test_scheduler_web_update.py` | asserts the old (pre-fork) response contract | — | revised to the derived `_WEB_URLS` binding | **done** (8.17, intentional behaviour change) |

### 8.2 The signature seam (8.2/8.3)

`tests/test_channel_signature_seam.py` asserts both halves of the contract:
the four channel methods carry upstream's signature (`(self)`), and the
target published by the handler is what the method consumes. With nothing
published — legacy mode — the upstream router fallback runs, and the test
proves the two paths differ only by the seam.

### 8.3 Upstream features the fork removed locally (8.4)

`scripts/conflict-baseline.txt` records `keep upstream _import_local_file` for
`channel/web/web_channel.py`. `tests/test_upstream_core_seams.py` turns that
into a live obligation: the moment the upstream symbol lands, the test requires
the loopback and per-boot-token checks to be intact (today it skips with that
reason, while still asserting the obligation is recorded). The same file
asserts the other seams are real — `app.py` runs hooks instead of containing the
fork guard, route literals come from the registry, the store's schema is
composed, and a future local-path route must be policy-registered.

### 8.4 Scheduler identity (8.14–8.17)

The resolver now exists once, in `agent/tools/scheduler/identity.py`, with the
two possible inputs stated explicitly (owner snapshot ⇒ tenant member, no owner
⇒ Agent-only) and revalidation/delivery left downstream. `integration.py` calls
it and constructs no `RuntimeIdentity` of its own, so an upstream edit to the
callback cannot conflict with fork identity logic. Evidence:
`tests/test_scheduler_identity_seam.py` (the convergence, plus the two input
shapes) and the pre-existing `tests/test_scheduler_identity_revalidation.py`
(revoked grants skip the fire with a recorded reason).

### 8.5 Intentional behaviour change (8.17)

`tests/test_scheduler_web_update.py` was rewritten in group 2 from a source-text
grep to assertions on the derived `_WEB_URLS`/policy tables. This is a
deliberate, strictly stronger contract: the old test would have passed even if
the handler and the registry disagreed about a route, which is the defect group
2 closed.

### 8.6 Seams are optional: the upstream path still works (8.10)

The point of a seam is that removing the fork half leaves upstream behaviour
intact, not a half-wired state. Each seam's "absent" branch is asserted
directly, not by inspection:

| Seam absent | Observed behaviour | Test |
|---|---|---|
| no authorized target published | `post_message` takes the upstream routing path | `test_post_message_falls_back_to_upstream_routing_without_a_target` |
| no target in this thread | the target is not shared across threads | `test_a_published_target_is_not_shared_across_threads` |
| hook never registered | `run_startup_hook` is a no-op | `test_an_unregistered_hook_is_a_no_op` |
| legacy identity mode | `resolve_tenant_workspace_root` returns `None`, so the upstream root resolution runs | `test_legacy_mode_returns_none_so_upstream_fallback_runs` |
| a workspace with no store | the backfill hook does not create one | `test_a_workspace_without_a_store_is_not_created` |

Command and result:

```
$ .venv/bin/python -m pytest tests/test_upstream_core_seams.py \
    tests/test_channel_signature_seam.py tests/test_startup_hook_seam.py \
    tests/test_scheduler_identity_seam.py -q -p no:randomly
40 passed, 1 skipped in 0.14s
```

The one skip is `test_local_file_import_is_loopback_and_token_guarded_when_present`
— the upstream `_import_local_file` symbol does not exist in the current tree,
so there is nothing to guard yet; the test still asserts the obligation is
recorded in `scripts/conflict-baseline.txt`, so it cannot be silently dropped.

### 8.7 Post-condition: no fork definitions left in upstream files (8.11)

The four upstream-owned surfaces named by the task are checked mechanically:

- **route literals** — `web_channel.py` builds `_WEB_URLS` from
  `channel/web/route_registry.py` (`test_web_channel_derives_its_urls_from_the_registry`);
  `auth/http_policy.py` derives `ROUTE_POLICY` the same way.
- **boot sequence** — `app.py` calls `run_startup_hook(...)` and contains no
  fork guard body (`test_app_py_has_no_inlined_fork_guard`); the bodies live in
  `common/startup_hooks.py` (`test_the_guards_exist_in_the_hook_module`).
- **public method signatures** — the four channel methods take `(self)` only
  (`test_upstream_channel_methods_take_no_fork_parameters`) and no
  database-only keyword survives (`test_no_database_only_keyword_survives_on_the_channel`).
- **store schema** — `conversation_store.py` has no literal `_DDL`
  (`test_the_conversation_store_uses_the_composable_schema`).

The two remaining "open, recorded" rows in the 8.1 inventory are deliberate:
`web_channel.py`'s additive `_require_*` helpers are new functions rather than
edits to upstream bodies, so they cannot conflict in place, and
`channel_instances.py` is partitioned by convention (D4b) rather than moved.
Both carry a trigger condition in §10.3.

### 8.8 Frontend seams (8.5–8.8)

The console's merge cost was concentrated in one 800 KB file: `console.js` held
one giant `I18N` literal extended by `Object.assign` blocks (every fork key
insertion landed on the same lines as upstream's) and hard-coded fork view
loaders in navigation dispatch. Both are now seams:

| Before | After | Proof it is lossless |
|---|---|---|
| `const I18N = {…}` + `Object.assign(I18N, …)` in `console.js` | 14 `static/js/i18n/*.js` namespaces registering `window.__cowI18N__`, merged at load | `tests/test_console_i18n_parity.cjs`: the merged table **deep-equals** the pre-split snapshot fixture; no key has two owners; `console.js` defines no domain key |
| 13 fork loader branches in `navigateTo`/`rerenderDynamicViews` | `CONSOLE_VIEW_REGISTRY` + `registerConsoleView({id,label,load,repaint})` | `tests/test_console_view_registry.cjs`: the registry drives load/repaint, all 6 admin + 1 todo view register, and an **empty** registry is a silent no-op (an upstream build still navigates) |
| fork markup inline in `chat.html` | `[data-fork-fragment]` mount point + `static/js/fragments.js` fetcher | `tests/test_fork_fragments.cjs`: mount point kept, inline markup gone, fragment preserves the DOM contract |

Two details worth recording, because they were found by running it rather than
by reading it:

- **Cache-busting had a hole.** `ChatHandler.GET` busted the scripts and the
  i18n namespaces, but the fragment is fetched *at runtime* by `fragments.js`,
  so both the loader and the fragment would have been served from the browser
  cache after an upgrade. Both are now discovered and busted the same way.
- **The split is deliberately scoped.** `chat.html` also holds ~10 fork view
  containers (`view-todo`, `view-tenant`, `view-platform`, …). Those are asserted
  to exist in `chat.html` by `test_nav_area_frontend.cjs`,
  `test_admin_home_frontend.cjs` and `test_tenant_tabbed_editor_frontend.cjs`,
  and the measured `chat.html` conflict with upstream is a *block* collision at
  the appearance dialog plus line-level composer edits — not the view
  containers. Moving them would change DOM timing for no conflict reduction, so
  the mechanism plus one migrated block is the deliverable; the rest follow the
  same mechanism when they actually conflict.

`.cjs` suites were compared the only reliable way (per file,
`node --test --test-timeout=15000`; the glob form cross-contaminates and hangs
pre-existing files): clean `HEAD` worktree 372 pass / 21 fail, after the split
385 pass / 21 fail — the delta is exactly the 13 assertions in the three new
tests, and every failing file fails identically at `HEAD`.

## 9. Upstream-sync infrastructure

| Task | Artifact | What it makes routine |
|---|---|---|
| 9.1 | `git config --local rerere.enabled true` + the CONTRIBUTING section | a resolution recorded once is replayed on later syncs, so the same seam is not re-resolved by hand |
| 9.2 | `.gitattributes` | only the fork's own binary artwork is declared `binary`; no merge strategy that could discard upstream edits |
| 9.3/9.4 | `scripts/sync-from-master.sh` + `scripts/sync_report.py` | fetch + rehearsal merge + conflict report; never commits, never pushes |
| 9.5 | `CONTRIBUTING.md` (fork section) | cadence (every upstream release, at least weekly) and the four-point human review checklist |
| 9.6 | `.gitignore` (fork section, last) | the `/openspec/` ignore block cannot be overwritten by, or overwrite, upstream's rules |
| 9.7 | `scripts/conflict-baseline.txt` | the five deliberate removals carry their reasons and are re-decided, not re-applied blindly |

The split matters: the *judgement* (expected conflict, drift, removals) is in
`scripts/sync_report.py` and unit-tested (`tests/test_sync_report.py`, 9
cases); the shell script only does the one thing that cannot be unit-tested —
fetch and attempt a merge — and aborts it afterwards. A rehearsal that leaves
`HEAD` moved is itself an error (the script checks and exits 2).

## 10. Verification

### 10.1 Gate status (task 10.3)

The prerequisite change `scan-onboarding-and-inbound-anchor` is **archived**
(`openspec/changes/archive/2026-09-11-scan-onboarding-and-inbound-anchor`), so
the enterprise-production gate is satisfied, not pending. `openspec validate
fork-decoupling-and-tenant-hardening --strict` reports the change valid.

### 10.2 Conflict coverage (task 10.6)

Every one of the 20 measured conflict files is covered by this change, by
construction of the baseline's disposition column:

| Disposition | Files | Covered by |
|---|---|---|
| `seam:*` | 8 | the seam named in the row (groups 2–8) |
| `keep-fork` | 1 (`.gitignore`) | labelled fork section, task 9.6 |
| `keep-deletion` | 5 | recorded decision + re-decision prompt, task 9.7 |
| `merge-docs` | 6 | **ordinary documentation merge — Non-Goal** |

The six `merge-docs` files are the three `docs/{,ja,zh}/intro/architecture.mdx`
/ `index.mdx` pairs. They conflict because both sides edited prose in the same
paragraphs; there is no code contract in them and no seam would help, so they
are explicitly out of scope (a human merge, once, at sync time).

### 10.3 Deferred work (task 10.7)

Recorded here so the next change starts from a list rather than from memory:

- **User-level resource grants** (`membership_resource_grants`) — the model
  currently grants resources at the tenant-role level only; a membership-level
  exception is designed but not implemented.
- **Agent metadata single source of truth** (outbox) — agent metadata is still
  projected from more than one store on some read paths.
- **Approval/quota full lifecycle** — approvals and quotas are enforced; their
  administrative lifecycle (expiry sweeps, reporting) is not complete.
- **Tenant deletion closure** — deleting a tenant does not yet prove every
  satellite (conversations, memory chunks, scheduler state) is unreachable.
- **Scheduler after revalidation** — a skipped fire is recorded and (after
  `MAX_CONSECUTIVE_SKIPS`) disables the task; there is no operator-facing
  "re-open after fixing the grant" flow yet.
- **Evolution background-thread identity** — background identity completion
  outside the request path is not covered.
- **`channel/channel_instances.py`: mechanised partition** — today the split is
  human discipline (D4b). Convert it to a moved/mechanised module when the file
  conflicts in place again, or when `auth/service.py`'s credential-validation
  path changes.

### 10.4 Documentation口径 (task 10.8)

`doc/权限管控体系.html` previously described platform qualification as derived
from `users.is_platform_admin`. It now states the source of truth is the
`user_platform_roles` → built-in `platform_admin` binding, with
`users.is_platform_admin` as a derived mirror column written only through the
binding path (audit F10).

### 10.5 Final regression comparison (task 10.1)

Baseline is the pre-implementation checkpoint `dc760766` in a `git worktree`,
**with this machine's gitignored `./config.json` copied in** (see §5.1.1 — without
it the baseline runs in `legacy` mode and the comparison lies). The comparison is
on the sorted `FAILED` set, never on the counts.

| Point in time | failed | passed | new failures vs baseline |
|---|---|---|---|
| baseline `dc760766` (+ real `config.json`) | 33 | 2470 | — |
| after groups 2–10 | 28 | 2692 | **none** |

The current 28 are a **strict subset** of the baseline's 33 — no failure was
merely swapped for another. Five baseline failures are fixed rather than
displaced:

- `test_tenant_create_containment::test_create_without_base_rejected_when_workspace_is_default_root`
  — the victim of the same process-global `config` rebinding §5.1.2 describes:
  once any test calls `load_config()`, `conf()["tenant_shared_base"]` holds this
  machine's `~/.cow/tenant-roots`, so the test no longer exercised the
  "nothing configured" path. It now neutralises **both** channels (env and
  config key), which is what its own comment already intended.
- `test_consumer_closure_acceptance::test_helper_defaults_to_legacy_when_key_missing`,
  `test_todo_service::test_disabled_feature_refuses_legacy`,
  `test_todo_service::test_legacy_no_login_requires_password` — the config-restore
  fix from §5.1.2.
- `test_subagent::test_the_repo_ships_a_guide_that_documents_the_real_format` —
  a `git worktree` artefact (it reads a gitignored asset directory absent from
  the worktree), not a result of this change.

```console
$ cp config.json /tmp/cow-baseline/            # gitignored, so a worktree lacks it
$ cd /tmp/cow-baseline && PYTHONPATH=/tmp/cow-baseline \
      /path/to/main/.venv/bin/python -m pytest tests -q -p no:randomly --tb=line
$ cd /Users/jiantan/ai_assistant/cowagent && .venv/bin/python -m pytest tests -q -p no:randomly --tb=line
$ rg '^FAILED' <each log> | sed 's/^FAILED //' | sort > /tmp/{base2,cur2}_fail.txt
$ comm -13 /tmp/base2_fail.txt /tmp/cur2_fail.txt   # new failures -> empty
```

### 10.6 Browser verification (task 10.1, the walk that found a P0)

The suite says the handlers behave; it cannot say the *console renders*. A
throwaway instance (`COW_DATA_DIR` in a temp dir, `agent_workspace` also in
temp so the developer's real roster and workspaces stay untouched, one platform
admin + one tenant member seeded) was driven by a real headless Chrome over CDP
— navigate, type, click, screenshot, and record every response ≥ 400 plus every
console error.

**This walk found a P0 regression that the whole Python suite missed.** Routes
`/chat` and `/admin` had been given the `tenant` policy when the registry became
authoritative (group 2) and the gate became real (group 3). A top-level browser
navigation cannot send `X-Tenant-ID` — the header is injected by the console's
`fetch` wrapper only *after* login — so every visit to `/` (which redirects to
`/chat`) answered `400 missing_tenant` **before the handler ran**, and the login
UI never rendered. The gate was working exactly as specified; the policy on the
row was wrong. The shell is a document, not tenant data: it carries no tenant
rows, and every data API it loads keeps its own `tenant`/`platform` policy, so
`public` is both the correct and the pre-change effective behaviour. Fixed in
`channel/web/route_registry.py`, pinned by
`tests/test_http_gate.py::test_a_browser_navigation_can_still_load_the_console_shell`
and `::test_the_shell_is_still_served_to_an_authenticated_browser`, and the
revision is recorded in `scripts/route-baseline.txt`.

Verified after the fix, with the tenant already stored (`VERIFY OK`):

- shell loads at `/chat` and `/admin`; login form present and interactive;
- login against the database identity store succeeds;
- **i18n**: zero `data-i18n` elements still showing a raw key; sample label
  localised (`我的待办`);
- **view registry**: `window.registerConsoleView` is exposed and the `tenant` /
  `todo` views are registered by fork modules (not dispatched in `console.js`);
- **fragments**: `#appearance-dialog` is mounted from `appearance-dialog.html`
  with content;
- **registry views render real data**: `view-tenant` (租户管理, the tenant row),
  `view-todo` (待办事项), `view-system_user` (用户管理, both members listed);
- **sidebar**: lists the seeded conversation (`Seed turn`) — not an empty or
  error placeholder, which is what makes the "no sessions" state meaningful;
- **platform-only log surface**: `GET /api/logs` → 200 for the platform admin,
  streamed (read one chunk and aborted) with no secret in the payload.

The only non-2xx responses left are *expected* and are asserted as such by the
driver rather than ignored:

| Response | Meaning |
|---|---|
| `404 /api/todos` | `todo_disabled` — the todo feature flag is off |
| `503 /api/knowledge/list` | the knowledge consumer is closed in database identity mode |
| `404 /api/history`, `404 /poll` | the console's boot-time probes for a client-generated session that does not exist yet |

One transient is recorded rather than fixed: on a **fresh browser profile** the
first `/api/sessions?scope=all` fires before `cow_tenant_id` reaches
`sessionStorage`, so the gate answers 400 once. It is self-healing and
user-invisible — the same run proved the sidebar still lists the seeded
conversation afterwards — so no client change was made. It is noted here so a
future reader does not have to re-derive whether that 400 matters.

### 10.7 Delta and baseline-consistency re-check (tasks 10.5/10.6)

Both checks are mechanical, so they are a script rather than prose — re-run it at
archive time instead of trusting this document:
`scripts/check_change_deltas.py` (unit-tested by `tests/test_change_delta_check.py`,
18 cases covering each failure mode, so the gate itself cannot silently pass on
broken input).

The script checks a change in one of **two modes**, picked from where the change
lives, because the same delta means opposite things either side of an archive:

| Mode | Change lives in | The delta is | Contract verified |
|---|---|---|---|
| `proposed` | `changes/<name>/` | a proposal | MODIFIED titles match the baseline verbatim and keep its scenarios; ADDED titles restate nothing |
| `applied` | `changes/archive/<date>-<name>/` | a record of what was applied | every ADDED/MODIFIED requirement and scenario is **present** in the main spec; REMOVED ones are gone |

The `applied` mode was added *because archiving broke the `proposed` mode*: the
script could no longer find the change, and — once it could — would have flagged
all 23 ADDED requirements as restatements, since after a successful sync those
requirements are supposed to exist. A checker that is only correct before the
operation it guards is not a gate, so the script now resolves archived changes and
inverts its expectations. Any operation block it does not understand (e.g.
`RENAMED`) is **reported, never skipped**: a gate that passes by ignoring input is
worse than no gate.

```console
$ openspec validate --specs --strict
Totals: 61 passed, 0 failed (61 items)
$ .venv/bin/python scripts/check_change_deltas.py
OK (applied): fork-decoupling-and-tenant-hardening — deltas consistent with the
    baseline, every conflicted file covered
$ .venv/bin/python -m pytest tests/test_change_delta_check.py -q -p no:randomly
18 passed
```

It asserts three things about the deltas and one about the sync baseline: all 20
conflicted files carry a recognised disposition, with every `seam:` row named
somewhere in this change. Current split: `seam` 8, `keep-deletion` 5,
`merge-docs` 6, `keep-fork` 1.

### 10.8 Archive (task 10.4)

`openspec archive fork-decoupling-and-tenant-hardening --yes` applied the deltas
to the main specs and moved the change; `openspec list` reports **no active
changes** left. Applied totals: **+23 added, ~5 modified, 0 removed**.

| Capability | Applied |
|---|---|
| `audit-log` | +1 |
| `console-route-lifecycle` | +5 |
| `enterprise-access-enforcement` | +3, ~1 |
| `fork-upstream-decoupling` | +9 (**new capability**) |
| `resource-execution-authorization` | +2 |
| `resource-quota` | ~1 |
| `scene-activation` | +1, ~2 |
| `tenant-channel-configuration` | ~1 |
| `tenant-resource-isolation` | +2 |

Post-sync integrity: **0 duplicate requirement titles** across all 61 specs (the
characteristic MODIFIED failure — an edited requirement appended rather than
replaced), and `openspec validate --specs --strict` passes 61/61. The archived
`tasks.md` reads **102/102**.
