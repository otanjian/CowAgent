# User-Private Projects in Database Identity Mode

**Date:** 2026-09-09  
**Status:** Approved for planning (approach 1, minimal slice A)  
**Problem:** In `identity_mode=database`, `/api/projects*` is `policy: closed`, so create/select return 503 `unavailable in database identity mode`. Handlers remain host-global (browse from `~`, select any path).

## 1. Goals and non-goals

### Goals (minimal A)

In database mode, an authenticated tenant member can:

- Create a project by bare name
- List / select / clear project for a session they own
- Reorder sidebar spaces
- Rename (display name) / forget a project binding

Projects are **user-private**: only visible and bindable by the owning user.

### Non-goals (this slice)

- Host filesystem browse (`/api/projects/browse` stays closed)
- Opening `/api/workspace/*` (file tree / `@` picker after select may stay degraded)
- Per-tenant shared projects catalog
- New permission codes (`project.*`)
- Tightening file-serve so same-tenant users cannot path-guess under `shared_root()/users/<other>/` (pre-existing residual; see §7)
- Desktop client parity beyond matching API contracts if already shared

## 2. Storage model

### Database mode (identity has `user_id`)

| Asset | Path |
|---|---|
| Metadata store | `user_root()/projects.json` |
| New project folders | `user_root()/projects/<name>/` |

`user_root()` already resolves to `shared_root()/users/<user_id>/` when `user_id` is set (`common/state_dir.py`), and is ambient via `current_identity()` under `_db_scope`.

### Legacy mode

Unchanged:

- `shared_root()/projects.json`
- `projects_root()` honors `project_workspace_root` when set, else `shared_root()/projects`
- Browse / open arbitrary directories remains allowed

### Database-mode constraints

1. **Ignore** `project_workspace_root` (must not escape the user root).
2. `create_project` accepts only a bare name; target must be a direct child of that user’s `projects/`.
3. `set_project_dir` / rename / delete / order entries that are real paths must resolve under that user’s `projects/` root (realpath + containment). Clearing to default (`None` / empty / `DEFAULT_SPACE_KEY`) remains allowed.
4. Selecting nothing still means “use Agent `state_root`”.

### Detection

`project_store` chooses roots by ambient identity:

- If `current_identity().user_id` is set → user-private roots (database path).
- Else → legacy shared roots.

This **requires modifying `_store_file()` and `projects_root()`** to resolve `user_root()` when `user_id` is present — today both use `shared_root()`, which under `_db_scope` resolves to the **tenant** shared root (not per-user):

```python
def _store_file() -> str:
    from common.state_dir import user_root
    ident = current_identity()
    return str((user_root(ident) if ident.user_id else shared_root()) / "projects.json")
```

Callers in database mode **must** run under `_db_scope()` (or equivalent `use_identity`) so `user_id` / `tenant_id` are present. Session-list annotations that already wrap `_db_scope` keep working and then read the **current user’s** store only (correct privacy).

## 3. HTTP policy and handlers

### Policy (`auth/http_policy.py`)

| Route | Method | New policy |
|---|---|---|
| `/api/projects` | GET | `tenant` |
| `/api/projects/create` | POST | `tenant` |
| `/api/projects/select` | POST | `tenant` |
| `/api/projects/order` | POST | `tenant` |
| `/api/projects/manage` | PUT, DELETE | `tenant` (fix today’s POST-only / method mismatch) |
| `/api/projects/browse` | GET | **`closed`** (unchanged) |

### Handler pattern (`channel/web/web_channel.py`)

For list / create / select / order / manage:

1. `_require_auth()`
2. `with _db_scope() as ctx:`
3. When `ctx is not None` (database):
   - Resolve `agent_id` via `_require_tenant_agent_binding` where an agent is involved
   - For select/create that touch a session: **verify true session ownership**. `_require_session_owner` only checks the agent is tenant-bound — it does **not** read `sessions.owner`. Replicate the inline check from `_workbench_chat_readiness` (line ~708): if the session row exists and `owner != ctx.user_id` (or `channel_type != "web"`), reject as 404 `session not found`. Do not rely on `_require_session_owner` for user ownership.
   - No new permission codes; membership + session ownership is enough
4. When `ctx is None` (legacy): keep existing `_require_auth()`-only behavior
5. Remove browse’s `_guard_not_database` only if browse is opened later; **do not** open browse in this slice
6. Update `_project_state` / default workspace resolution so database responses are identity-aware. Today `_project_state` uses `state_root_str(RuntimeIdentity(agent_id=…))` and `project_store.projects_root()`, which in DB mode is the **agent workspace / tenant-shared root** — not the user root. In database mode `default_workspace` should resolve against `user_root()` (per-user) plus the selected tenant shared root, so the frontend fallback "current project" points at the correct root, not another user's (or the host's) workspace.

### Path gate helper

Add a small helper used by select/manage (and optionally create return paths), e.g. `_require_user_project_path(path) -> str`:

- Normalize with realpath
- Require `path` is under `user_root()/projects` (or equal to an allowed default-space sentinel when clearing)
- Reject otherwise with a clear 400-style error payload

Prefer implementing containment inside `project_store` for database mode so CLI/bridge callers cannot bypass the web gate.

### Capability report

Add to `IdentityService._consumer_availability()`:

```python
"projects": {"available": True, "reason": ""},
```

Browse remains closed at the route level; the flag means “project workspace chip APIs for create/select/list/manage”, not host browse.

Update `_guard_not_database` docstring: project *create/select/manage* are no longer closed via that gate; knowledge write/import and **project browse** remain closed.

## 4. Frontend

File: `channel/web/static/js/console.js` (and any mirrored desktop API usage if trivial).

1. When `identity_mode === 'database'` (existing mode state):
   - Hide / omit the “打开项目…” (folder picker) menu entry that calls `/api/projects/browse`
   - Keep “新建项目”, recents, default space, rename/delete/reorder
2. On create/select/list errors:
   - Surface `message` (including any residual 503) via the existing toast/red-box path — no silent swallow for write actions
3. After create/select, existing `_wsSelRevealFiles()` may fail while workspace APIs stay closed — acceptable for this slice; do not block create success on file-panel refresh
4. Optionally gate the chip on `consumers.projects.available` when the auth/bootstrap payload already exposes consumers

## 5. Data flow

```
UI "确定" → POST /api/projects/create {session, name, agent?}
  → route policy tenant (not closed)
  → _db_scope → RuntimeIdentity(tenant, user, …)
  → session owner check (replicate `sessions.owner == ctx.user_id`; do NOT use _require_session_owner)
  → project_store.create_project(name)
       → mkdir user_root()/projects/<name>
  → project_store.set_project_dir(session, path, agent)
       → write user_root()/projects.json
  → optional agent.apply_project_dir(path)
  → JSON success + _project_state
```

Execution isolation already includes `user_root` in read/write roots (`agent/permission/isolation.py`), so tools can use the new project cwd once bound.

## 6. Error handling

| Case | Response |
|---|---|
| Database + browse | 503 `database_unavailable` (unchanged) |
| Empty / illegal name | 400-style error from handler / `ValueError` |
| Name already exists | error from `FileExistsError` |
| Select path outside user’s `projects/` | reject (do not bind) |
| Session not owned | replicate inline owner check → 404 `session not found` (same 403/401/404 pattern as other session APIs) |
| Legacy | unchanged success paths |

## 7. Risks and residual issues

1. **Naïve policy flip without `_db_scope` / containment** — would write host/shared store or bind arbitrary paths. Mitigated by this design.
2. **File panel still closed** — create works; tree/preview may not. Documented non-goal.
3. **Same-tenant file-serve under `shared_root`** — `user_root` lives under tenant `shared_root`, and `_db_file_serve_roots` allows the whole shared root. Path guessing across users is a pre-existing gap; out of scope unless a follow-up confines serve roots to `user_root` + agent workspaces.
4. **Admin session lists** — annotations under the viewer’s identity only show that viewer’s bindings; other users’ project labels may be absent (privacy-correct).
5. **Manage methods** — policy must list PUT/DELETE or the UI keeps getting 405 after opening.

## 8. Testing

1. **Policy:** database mode → create/select/list/order/manage (PUT/DELETE) are not 503; browse still 503; wrong manage method still 405 if unregistered.
2. **Containment:** under `_db_scope` for user A, create lands in A’s `user_root()/projects/`; user B’s scope cannot list/select/rename A’s path; select of `/etc` or B’s path fails.
3. **Legacy regression:** without database identity, shared_root behavior and browse still work as today.
4. **Session ownership:** cannot bind another user’s session to a project. Session rows are owned per-user in the `sessions` table; `_require_session_owner` alone is insufficient because it does not read `sessions.owner` — assert the inline replication returns 404 for a non-owner.
5. **Frontend fixture (optional):** database mode menu omits open-folder entry; create error toast still shows server message.
6. Reuse patterns from `tests/test_http_policy.py`, `tests/test_web_consumer_closure.py`, `tests/test_state_dir_tenant_containment.py`.

## 9. Implementation order

1. `project_store` root switch + database path containment  
2. Handlers + `_db_scope` / session owner  
3. HTTP policy flip + manage method fix  
4. Consumer availability + comment cleanup  
5. Frontend hide browse + error surfacing  
6. Tests  

## 10. Success criteria

- Creating project “test” in database mode returns success (no red 503).
- Project dir exists under the logged-in user’s `user_root()/projects/test`.
- Another user in the same tenant does not see it in `/api/projects`.
- “打开项目…” is unavailable in database UI; browse API still 503.
- Legacy mode unchanged.
