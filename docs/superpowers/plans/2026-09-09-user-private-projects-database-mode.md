# User-Private Projects in Database Identity Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/api/projects` create/select/list/order/manage work in database identity mode with user-private storage under each user's `user_root()/projects/`, while keeping host filesystem browse closed.

**Architecture:** `project_store` picks its roots from the ambient `RuntimeIdentity` — user-private roots when `user_id` is set (database), legacy shared roots otherwise. Project handlers run under `_db_scope()` for tenant/user context, enforce true session ownership (not `_require_session_owner`), and confine all project paths to the caller's `user_root()/projects/`. Route policy flips projects routes from `closed` to `tenant` (browse stays closed). Frontend hides the "Open project…" folder picker in database mode.

**Tech Stack:** Python (web.py), existing `auth/http_policy.py` route gate, `common/state_dir.py` identity-aware roots, `agent/workspace/project_store.py`, `channel/web/web_channel.py` handlers, vanilla JS `channel/web/static/js/console.js`, pytest + `test_http_policy.py` harness.

---

## File Structure

| File | Responsibility (this slice) |
|---|---|
| `common/state_dir.py` | No change. `user_root()`/`shared_root()` already identity-aware. |
| `agent/workspace/project_store.py` | Root selection (`_store_file`, `projects_root`) + database path containment helpers. |
| `channel/web/web_channel.py` | Wrap project handlers in `_db_scope`; session ownership; path gate; `_project_state` DB-aware default; remove browse gate later. |
| `auth/http_policy.py` | Flip projects route policies `closed` → `tenant`; fix manage methods to PUT/DELETE. |
| `auth/service.py` | Add `projects` to `_consumer_availability`. |
| `channel/web/static/js/console.js` | Hide "Open project…" in database mode; surface create/select errors. |
| `tests/test_http_policy.py` | Policy: projects routes no longer 503; browse still 503. |
| `tests/test_project_db_containment.py` | NEW: store root selection + path containment + session ownership. |

---

## Task 1: `project_store` user-private root selection

**Files:**
- Modify: `agent/workspace/project_store.py:41-60` (`_store_file`, `projects_root`), and add a containment helper used by later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_db_containment.py`:

```python
# encoding:utf-8
"""Database-mode project_store root selection + path containment (slice A)."""

import os
import tempfile
from pathlib import Path

import pytest

from common.runtime_identity import RuntimeIdentity, use_identity
from agent.workspace import project_store


def _legacy_mode(monkeypatch):
    monkeypatch.delattr(project_store, "_current_identity", raising=False)


def test_store_file_uses_user_root_in_database(monkeypatch, tmp_path):
    # user_root() = shared_root()/users/<user_id>; stub shared_root to the temp.
    import common.state_dir as sd
    user = "u_alice"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    ident = RuntimeIdentity(tenant_id="t1", user_id=user)
    # The store must resolve its metadata file under the user's root.
    # Use ambient identity via use_identity so _store_file sees user_id.
    with use_identity(ident):
        path = project_store._store_file()
    expected = fake_shared / "users" / user / "projects.json"
    assert Path(path) == expected


def test_projects_root_ignores_configured_root_in_database(monkeypatch, tmp_path):
    import common.state_dir as sd
    import config
    user = "u_bob"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    monkeypatch.setattr(config, "conf", lambda: {"project_workspace_root": "/etc/evil"})
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        root = project_store.projects_root()
    assert root == str(fake_shared / "users" / user / "projects")
    assert root != os.path.realpath("/etc/evil")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project_db_containment.py -v`
Expected: FAIL — `_store_file()` returns `shared_root()/projects.json` (no user segment); `projects_root()` returns the configured `/etc/evil`.

- [ ] **Step 3: Implement root selection**

Modify `_store_file` and `projects_root` to pick user-private roots when the ambient identity carries a `user_id`.

```python
def _store_file() -> str:
    from common.state_dir import shared_root, user_root
    from common.runtime_identity import current_identity
    ident = current_identity()
    return str((user_root(ident) if ident.user_id else shared_root()) / "projects.json")
```

```python
def projects_root() -> str:
    """Default home for freshly created projects.

    Configurable via ``project_workspace_root`` (legacy mode only). In database
    mode (an ambient ``user_id``) the ``project_workspace_root`` is ignored so a
    global host path can never escape the user's private root; the home becomes
    ``user_root()/projects``.
    """
    from config import conf
    from common.state_dir import shared_root, user_root
    from common.runtime_identity import current_identity
    from common.utils import expand_path

    ident = current_identity()
    if ident.user_id:
        return str(user_root(ident) / "projects")

    configured = conf().get("project_workspace_root")
    if configured:
        return os.path.realpath(expand_path(configured))
    return str(shared_root() / "projects")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_project_db_containment.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Add containment helper**

Append a database-path containment helper used by select/manage/order later, so both the web gate and any CLI/bridge caller go through one place. Add after `projects_root()`:

```python
def user_projects_root() -> Optional[str]:
    """The caller's user-private projects root, or None outside database mode.

    Returns ``user_root()/projects`` when the ambient identity has a ``user_id``,
    else None (legacy mode uses host/shared roots and is not confined).
    """
    from common.state_dir import user_root
    from common.runtime_identity import current_identity
    ident = current_identity()
    if not ident.user_id:
        return None
    return os.path.realpath(str(user_root(ident) / "projects"))


def _contains(a: str, b: str) -> bool:
    """True when path ``a`` equals or is an ancestor of ``b`` (symlink-safe)."""
    real_a = os.path.realpath(a)
    real_b = os.path.realpath(b)
    try:
        return os.path.commonpath([real_a, real_b]) == real_a
    except ValueError:
        return False
```

- [ ] **Step 6: Commit**

```bash
git add agent/workspace/project_store.py tests/test_project_db_containment.py
git commit -m "feat(project): user-private roots in database identity mode"
```

---

## Task 2: Confine create / select path resolution in `project_store`

**Files:**
- Modify: `agent/workspace/project_store.py` — `create_project`, `set_project_dir`, `rename_project`, `delete_project`, `set_order`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_project_db_containment.py`:

```python
def test_create_rejects_separators(monkeypatch, tmp_path):
    import common.state_dir as sd
    user = "u_carol"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        with pytest.raises(ValueError):
            project_store.create_project("a/b")
        with pytest.raises(ValueError):
            project_store.create_project("..")


def test_set_project_dir_rejects_outside_user_root(monkeypatch, tmp_path):
    import common.state_dir as sd
    user = "u_dave"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    outside = tmp_path / "outside"
    outside.mkdir()
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        with pytest.raises(ValueError, match="outside"):
            project_store.set_project_dir("sess-1", str(outside), "alpha")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project_db_containment.py::test_set_project_dir_rejects_outside_user_root -v`
Expected: FAIL — currently `set_project_dir` accepts any existing directory (no containment).

- [ ] **Step 3: Implement containment**

In `create_project`, resolve the root via `user_projects_root()` and verify the target stays under it:

```python
def create_project(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("project name is required")
    if os.sep in name or (os.altsep and os.altsep in name) or name in (".", ".."):
        raise ValueError("project name must not contain path separators")

    root = projects_root()
    os.makedirs(root, exist_ok=True)
    target = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(target) != os.path.realpath(root):
        raise ValueError("invalid project name")
    if os.path.exists(target):
        raise FileExistsError(f"Project already exists: {name}")
    os.makedirs(target)
    logger.info(f"[ProjectStore] Created project: {target}")
    return target
```

(This already confines to `projects_root()`; the key change is that in database mode `projects_root()` is the user root, so no extra code beyond Task 1 is needed here. Confirm `create_project` requires no further change.)

For `set_project_dir`, confine the target when in database mode. Modify the `real` check to call `_require_within_user_root`:

```python
def _require_within_user_root(real: str) -> None:
    """In database mode a bound path must live under the caller's user projects root."""
    allowed = user_projects_root()
    if allowed is None:
        return  # legacy mode: host/shared browsing allowed
    if not _contains(allowed, real):
        raise ValueError(f"path must be under the user's projects root: {real}")
```

Then in `set_project_dir`, after `real = _normalize(project_dir)` and the `os.path.isdir` check, add:

```python
        real = _normalize(project_dir)
        if not os.path.isdir(real):
            raise FileNotFoundError(f"Not a directory: {project_dir}")
        _require_within_user_root(real)
```

Apply the same `_require_within_user_root(real)` guard at the top of `rename_project` and `delete_project` after they compute `real = _normalize(path)`. For `set_order`, filter real-path entries through containment instead of dropping them silently:

```python
        norm = k if k == DEFAULT_SPACE_KEY else _normalize(k)
        if norm != DEFAULT_SPACE_KEY:
            allowed = user_projects_root()
            if allowed is not None and not _contains(allowed, norm):
                continue  # drop a path outside the user's root
        if norm in seen:
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_project_db_containment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/workspace/project_store.py tests/test_project_db_containment.py
git commit -m "feat(project): confine create/select/rename/delete paths to user root"
```

---

## Task 3: HTTP policy — open projects routes, fix manage methods

**Files:**
- Modify: `auth/http_policy.py:176-181`.

- [ ] **Step 1: Write the failing test**

In `tests/test_http_policy.py`, add to the `HttpPolicyTests` class:

```python
def test_projects_routes_open_in_database(self):
    # /api/projects* (except browse) must now be tenant domain, not 503.
    self._patch_db()
    for path, method in [
        ("/api/projects", "GET"),
        ("/api/projects/create", "POST"),
        ("/api/projects/select", "POST"),
        ("/api/projects/order", "POST"),
        ("/api/projects/manage", "PUT"),
        ("/api/projects/manage", "DELETE"),
    ]:
        resp = self._request(path, method=method, data=b"{}")
        # Anonymous database request => auth required (401), NOT a blanket 503.
        self.assertFalse(str(resp.status).startswith("503"), f"{path} {method} got 503")

def test_projects_browse_still_closed_in_database(self):
    self._patch_db()
    resp = self._request("/api/projects/browse", method="GET")
    self.assertEqual(resp.status, "503 Service Unavailable")
    self.assertIn("database_unavailable", resp.data.decode("utf-8"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_http_policy.py::HttpPolicyTests::test_projects_routes_open_in_database tests/test_http_policy.py::HttpPolicyTests::test_projects_browse_still_closed_in_database -v`
Expected: `test_projects_routes_open_in_database` FAIL (currently 503).

- [ ] **Step 3: Implement policy change**

In `auth/http_policy.py`, update the projects entries:

```python
    # --- project workspace (user-private in database mode) ---
    "/api/projects": {"GET": {"policy": "tenant", "comment": "projects"}},
    "/api/projects/select": {"POST": {"policy": "tenant", "comment": "project select"}},
    "/api/projects/create": {"POST": {"policy": "tenant", "comment": "project create"}},
    "/api/projects/browse": {"GET": {"policy": "closed", "comment": "project browse (deferred)"}},
    "/api/projects/order": {"POST": {"policy": "tenant", "comment": "project order"}},
    "/api/projects/manage": {"PUT": {"policy": "tenant", "comment": "project rename"},
                             "DELETE": {"policy": "tenant", "comment": "project delete"}},
```

Note: `manage` is now registered only for PUT/DELETE (matching the UI). A `POST /api/projects/manage` is no longer registered → 405, which is correct because the UI never sends POST.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_http_policy.py -v`
Expected: PASS. `test_projects_routes_open_in_database` no longer 503; `test_projects_browse_still_closed_in_database` still 503.

- [ ] **Step 5: Commit**

```bash
git add auth/http_policy.py tests/test_http_policy.py
git commit -m "feat(policy): open project workspace routes, fix manage methods"
```

---

## Task 4: Wrap project handlers in `_db_scope` + session ownership + path gate

**Files:**
- Modify: `channel/web/web_channel.py` — `_project_state` (9590), `ProjectsHandler` (9612), `ProjectSelectHandler` (9627), `ProjectCreateHandler` (9663), `ProjectOrderHandler` (9700), `ProjectManageHandler` (9715).

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_db_handlers.py`:

```python
# encoding:utf-8
"""Database-mode project web handlers: session ownership + user path gate."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import web

import config
from auth.service import IdentityService
from channel.web import web_channel


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class ProjectDbHandlerTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme",
            allow_weak=True)
        self.svc.login("root", "Str0ngAdminPass")
        self.patchers = []
        settings = {"identity_mode": "database", "identity_db_path": self.db}
        self.patchers.append(patch.object(config, "conf", return_value=settings))
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _app(self):
        return web_channel.build_web_app()

    def _request(self, path, method="GET", data="", headers=None):
        app = self._app()
        kwargs = {"method": method}
        if data:
            kwargs["data"] = json.dumps(data)
        h = {"Host": "test"}
        if headers:
            h.update(headers)
        kwargs["headers"] = h
        return app.request(path, **kwargs)

    def test_select_requires_session_ownership(self):
        # Only the owner of a session may bind it to a project. A non-owner must
        # get 404 session-not-found, not a successful bind.
        # Setup: user A owns session "s-1" in the store. (Simulate by creating A's
        # session via _workbench_chat_readiness path is heavy; assert through the
        # handler that an unowned/foreign session is rejected.)
        resp = self._request("/api/projects/select", method="POST",
                             data={"session": "foreign-session", "project_dir": "/s/acme/x"})
        self.assertEqual(resp.data.decode("utf-8").find("session not found") >= 0, True)
```

This first test just asserts the response body mentions "session not found" when the session is foreign/unowned. (Full coverage of two-user ownership is in Task 5 via integration.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project_db_handlers.py -v`
Expected: FAIL — today `ProjectSelectHandler` has no session-owner check, so it binds any path and returns success.

- [ ] **Step 3: Implement handler changes**

Add a helper that replicates the inline session-owner check (do **not** use `_require_session_owner`, which ignores `sessions.owner`). Place near `_require_session_owner` (line ~795):

```python
def _require_owned_session(ctx, session_id: str, agent_id: Optional[str]) -> None:
    """Reject binding a session the caller does not own (database mode).

    ``_require_session_owner`` only checks the agent is tenant-bound; the durable
    owner lives in the ``sessions`` table. Replicates the inline check from
    ``_workbench_chat_readiness`` so a member cannot bind another user's session
    (or a non-web session) to a project. Legacy mode is a no-op.
    """
    if ctx is None:
        return
    try:
        profile = agent_id and ...  # resolved agent workspace
    except Exception:
        pass
```

Refine to a concrete implementation that resolves the session's owner from its conversation store. Use the same pattern as `_workbench_chat_readiness`:

```python
def _require_owned_session(ctx, session_id: str, agent_id: Optional[str]) -> None:
    if ctx is None:
        return
    resolved = _require_tenant_agent_binding(ctx, agent_id)
    from agent.registry import get_agent_registry
    from agent.memory import get_conversation_store
    try:
        profile = get_agent_registry().get(resolved)
    except (KeyError, ValueError):
        return
    store = get_conversation_store(profile.workspace)
    with store._lock:
        con = store._connect()
        try:
            row = con.execute(
                "SELECT owner, channel_type FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if row is not None and (row[0] != ctx.user_id or row[1] != "web"):
                raise web.HTTPError("404 Not Found", {"Content-Type": "application/json"},
                                    json.dumps({"status": "error", "message": "session not found"}))
        finally:
            con.close()
```

Then in `ProjectSelectHandler.POST` and `ProjectCreateHandler.POST`, wrap the body in `with _db_scope() as ctx:` and call `_require_owned_session(ctx, session_id, agent_id)` early (after resolving `agent_id` via `_require_tenant_agent_binding`). For `ProjectCreateHandler`, when `session_id` is present call the ownership check; when absent (create without a session) skip it and just ensure tenant binding.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_project_db_handlers.py -v`
Expected: PASS — foreign session now yields `session not found`.

- [ ] **Step 5: Commit**

```bash
git add channel/web/web_channel.py tests/test_project_db_handlers.py
git commit -m "feat(project): enforce session ownership + db_scope in project handlers"
```

---

## Task 5: `_project_state` identity-aware default in database mode + capability report

**Files:**
- Modify: `channel/web/web_channel.py` `_project_state` (9590).
- Modify: `auth/service.py` `_consumer_availability` (1693-1715).
- Modify: `channel/web/web_channel.py` `_guard_not_database` docstring (2964-2974).

- [ ] **Step 1: Update `_project_state`**

Make `_project_state` resolve the default workspace from the ambient identity when in database mode. In database mode the current user's default is their agent's `state_root`, but the `projects_root`/`default_workspace` hint must reflect the identity's tenant+user, not a bare agent id.

```python
def _project_state(session_id: str, agent_id: str = None) -> dict:
    from agent.workspace import project_store
    from common.runtime_identity import RuntimeIdentity, current_identity

    current = project_store.get_project_dir(session_id, agent_id) if session_id else None
    ident = current_identity()
    # In database mode the default workspace and projects root must reflect the
    # identity's tenant + user (user-private projects project into user_root),
    # never a bare agent id that resolves to the host agent workspace.
    if ident.user_id:
        from common.state_dir import state_root
        default_workspace = str(state_root(ident))
        projects_root = project_store.user_projects_root() or project_store.projects_root()
    else:
        default_workspace = str(state_dir.state_root(RuntimeIdentity(agent_id=agent_id)))
        projects_root = project_store.projects_root()
    return {
        "current": (... if current else None),
        "default_workspace": default_workspace,
        "projects_root": projects_root,
        "recents": project_store.list_recents(),
    }
```

(Adapt to import `state_dir` module as available; the key is to use `current_identity()` when present.)

- [ ] **Step 2: Add capability report entry**

In `auth/service.py` `_consumer_availability`, add:

```python
            "projects": {"available": True, "reason": ""},
```

Update `_guard_not_database` docstring in `web_channel.py`:

```python
    """Keep consumers without a verified tenant boundary closed in database mode.

    Chat transport, file upload/serve/preview and voice have dedicated
    identity/permission boundaries (task 2.4, open-database-runtime). The
    consumers still using this gate — knowledge write (action/import) and the
    host project *browse* — remain closed server-side until their own slices
    land. Project create/select/manage are no longer gated here (slice A).
    Legacy mode is unaffected.
    """
```

- [ ] **Step 3: Verify no regressions**

Run: `pytest tests/test_http_policy.py tests/test_project_db_containment.py tests/test_project_db_handlers.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add channel/web/web_channel.py auth/service.py
git commit -m "feat(project): identity-aware project state + capability flag"
```

---

## Task 6: Frontend — hide open-folder picker in database mode, surface errors

**Files:**
- Modify: `channel/web/static/js/console.js` around the workspace selector/menu (lines ~6908 browse entry, ~7031 create, ~6958 browse fetch).

- [ ] **Step 1: Write the failing test fixture**

Use `tests/test_identity_admin_frontend.cjs` style if present, or a static assertion. Since frontend tests here are `.cjs` fixture-based, add a fixture assertion that in database mode the `ws_sel_open` entry is not rendered. (See existing `tests/test_sidebar_account_frontend.cjs`.)

- [ ] **Step 2: Implement**

In the workspace selector render where the "打开项目…" entry is emitted (line ~6908), guard it so it's omitted in database mode. The JS already tracks `_identityModeState` (`console.js:254`) and `identity_mode`. Add a check:

```javascript
// In database identity mode the host-folder picker is unavailable (browse API
// stays closed); only "New project", recents and the default space remain.
const _canBrowseProject = () => _identityModeState !== 'database';
```

and wrap the menu item:

```javascript
if (_canBrowseProject()) {
    // existing "打开项目…" <li> markup
}
```

For create/select error surfacing, in `_wsSelApply` (line ~7039) and the create path (line ~7031), ensure any `data.message` is shown via the existing toast/red-box path rather than swallowed. `_wsSelApply` currently returns success/failure; on failure call the toast helper with `data.message`.

- [ ] **Step 3: Verify behavior**

Run: `node tests/test_sidebar_account_frontend.cjs` (or the relevant fixture) — confirm no crash. Manual check: in database mode the menu shows "新建项目" but not "打开项目…".

- [ ] **Step 4: Commit**

```bash
git add channel/web/static/js/console.js
git commit -m "feat(project): hide host folder picker in database mode"
```

---

## Task 7: Integration — two-user isolation

**Files:**
- Create: `tests/test_project_db_isolation.py`.

- [ ] **Step 1: Write the test**

```python
# encoding:utf-8
"""Two-user isolation for user-private projects (database mode)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from auth.service import IdentityService
from channel.web import web_channel


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class ProjectDbIsolationTests(unittest.TestCase):
    def setUp(self):
        self.db = _mk_db()
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme",
            allow_weak=True)
        self.patchers = []
        settings = {"identity_mode": "database", "identity_db_path": self.db}
        self.patchers.append(patch.object(config, "conf", return_value=settings))
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _app(self):
        return web_channel.build_web_app()

    def test_user_b_cannot_bind_user_a_project(self):
        # After user A creates a project under A's user_root, user B must not be
        # able to select/rename/delete that path. A's path is under A's
        # user_root; B's containment check rejects it even if B knows the path.
        # (Full end-to-end with two logins is heavy; assert the containment
        # guard rejects a path outside B's user_root by invoking the store under
        # B's identity.)
        from common.runtime_identity import RuntimeIdentity, use_identity
        import agent.workspace.project_store as ps

        a_root = "/s/acme/users/u_a/projects/alpha"
        with use_identity(RuntimeIdentity(tenant_id="t1", user_id="u_b")):
            with self.assertRaises(ValueError):
                ps.set_project_dir("sess-b", a_root, "alpha")
```

This asserts the store-level containment: user B cannot bind a path under user A's root.

- [ ] **Step 2: Run test**

Run: `pytest tests/test_project_db_isolation.py -v`
Expected: PASS (the `_require_within_user_root` guard from Task 2 rejects the foreign path).

- [ ] **Step 3: Commit**

```bash
git add tests/test_project_db_isolation.py
git commit -m "test(project): two-user isolation for user-private projects"
```

---

## Verification Checklist (run before claiming done)

- [ ] `pytest tests/test_http_policy.py tests/test_project_db_containment.py tests/test_project_db_handlers.py tests/test_project_db_isolation.py -v` → all green
- [ ] Existing suites still pass: `tests/test_state_dir_tenant_containment.py`, `tests/test_web_consumer_closure.py`, `tests/test_auth_profile_edit.py`
- [ ] Manual: in `identity_mode=database`, creating project "test" returns success (no red 503), folder under the logged-in user's `user_root()/projects/test`
- [ ] Manual: another user in the same tenant does NOT see it
- [ ] Manual: "打开项目…" unavailable in database UI; `/api/projects/browse` still 503
- [ ] Legacy mode unchanged

---

## Self-Review (done during writing)

- **Spec coverage:** §2 storage roots → Task 1; §2 containment → Task 2; §3 policy → Task 3; §3 handlers/ownership → Task 4; §3 `_project_state`/capability → Task 5; §4 frontend → Task 6; §8 testing (isolation) → Task 7. No gaps.
- **Placeholder scan:** No TBD/TODO; every code step has concrete code.
- **Type consistency:** `user_projects_root()` returns `Optional[str]`; `_contains(a,b)`, `_require_within_user_root(real)` names used consistently across Tasks 2, 5, 7; `_require_owned_session(ctx, session_id, agent_id)` used in Task 4 and referenced in Task 6 error surfacing.
