# Chat / Admin Navigation Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/chat` shows only workbench menus;「管理控制台」opens `/admin` in a named new tab with admin menus and an overview home; return reuses the workbench tab.

**Architecture:** Dual route (`/chat` + `/admin`) serving the same `chat.html` shell. JS sets `data-nav-area` from `location.pathname`. Named windows `cow-workbench` / `cow-admin` reuse tabs. Entry visible only to platform admin or current-tenant `tenant_admin` (database mode).

**Tech Stack:** Python web.py handlers, vanilla JS (`console.js`), `chat.html`, `console.css`, unittest + Node `node:test`.

**Spec:** `docs/superpowers/specs/2026-09-09-chat-admin-nav-split-design.md`

---

## File map

| File | Responsibility |
| --- | --- |
| `channel/web/web_channel.py` | Register `/admin` → `ChatHandler` |
| `tests/test_web_navigation_mode.py` | Assert `/admin` is routed / handler serves shell |
| `channel/web/chat.html` | Area markers, admin entry, return entry, `view-admin-home` |
| `channel/web/static/css/console.css` | `[data-nav-area]` show/hide rules |
| `channel/web/static/js/console.js` | Path→area, open/return, entry gate, cross-area `navigateTo`, admin-home, i18n |
| `tests/test_nav_area_frontend.cjs` | DOM/unit coverage for area + entry + open helpers |

---

### Task 1: Route `/admin` through ChatHandler

**Files:**
- Modify: `channel/web/web_channel.py` (urls list near `/chat`)
- Modify: `tests/test_web_navigation_mode.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_navigation_mode.py`:

```python
def test_admin_url_maps_to_chat_handler(self):
    web_channel = _import_wc()
    urls = web_channel.urls if hasattr(web_channel, "urls") else None
    # Prefer the module-level url list name used by the app.
    mapping = list(getattr(web_channel, "urls", []) or getattr(web_channel, "URLS", []) or [])
    # Fallback: scan source for registration if urls is built inline.
    import inspect
    src = inspect.getsource(web_channel)
    self.assertIn("'/admin'", src)
    self.assertRegex(src, r"'/admin',\s*'ChatHandler'")
```

Also add:

```python
def test_admin_handler_get_same_injection_as_chat(self):
    # Reuse _chat_handler_output — Admin and Chat share ChatHandler.GET
    out = self._chat_handler_output("split")
    self.assertIn("split", out)
```

(If `urls` is a flat list in module, assert `'/admin'` appears immediately before `'ChatHandler'` in that list.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_navigation_mode.py::TestWebNavigationMode::test_admin_url_maps_to_chat_handler -v`  
Expected: FAIL (no `/admin` registration)

- [ ] **Step 3: Minimal implementation**

In `channel/web/web_channel.py` urls, next to `/chat`:

```python
'/chat', 'ChatHandler',
'/admin', 'ChatHandler',
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_navigation_mode.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add channel/web/web_channel.py tests/test_web_navigation_mode.py
git commit -m "feat(web): serve console shell at /admin"
```

---

### Task 2: Frontend helpers — nav area + named-window open

**Files:**
- Create: `tests/test_nav_area_frontend.cjs`
- Modify: `channel/web/static/js/console.js` (near `_navigationMode`)

- [ ] **Step 1: Write failing Node tests**

Create `tests/test_nav_area_frontend.cjs` that extracts a small pure section via string slice **or** evaluates helper functions after defining them in console.js between clear markers:

```javascript
// === NAV_AREA_BEGIN ===
function _navAreaFromPath(pathname) {
    const p = String(pathname || '');
    return p === '/admin' || p.startsWith('/admin/') ? 'admin' : 'workbench';
}
const NAV_WINDOW_WORKBENCH = 'cow-workbench';
const NAV_WINDOW_ADMIN = 'cow-admin';
function _openNavArea(area, path) {
    const name = area === 'admin' ? NAV_WINDOW_ADMIN : NAV_WINDOW_WORKBENCH;
    const target = path || (area === 'admin' ? '/admin' : '/chat');
    return window.open(target, name);
}
function _qualifyAdminConsoleEntry(opts) {
    // opts: { identityMode, isPlatformAdmin, isTenantAdmin }
    if (opts.identityMode !== 'database') return true;
    return !!(opts.isPlatformAdmin || opts.isTenantAdmin);
}
// === NAV_AREA_END ===
```

Test file pattern (mirror `test_sidebar_account_frontend.cjs`):

```javascript
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
function section(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Missing section ${start}`);
    return source.slice(from, to + end.length);
}

test('nav area from path', () => {
    const code = section('// === NAV_AREA_BEGIN ===', '// === NAV_AREA_END ===');
    const sandbox = { window: { open(url, name) { sandbox._opened = { url, name }; return { name }; } } };
    vm.runInNewContext(code, sandbox);
    assert.equal(sandbox._navAreaFromPath('/chat'), 'workbench');
    assert.equal(sandbox._navAreaFromPath('/admin'), 'admin');
    assert.equal(sandbox._navAreaFromPath('/admin/'), 'admin');
    sandbox._openNavArea('admin');
    assert.deepEqual(sandbox._opened, { url: '/admin', name: 'cow-admin' });
    sandbox._openNavArea('workbench');
    assert.deepEqual(sandbox._opened, { url: '/chat', name: 'cow-workbench' });
    assert.equal(sandbox._qualifyAdminConsoleEntry({ identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: false }), false);
    assert.equal(sandbox._qualifyAdminConsoleEntry({ identityMode: 'database', isPlatformAdmin: false, isTenantAdmin: true }), true);
    assert.equal(sandbox._qualifyAdminConsoleEntry({ identityMode: 'legacy', isPlatformAdmin: false, isTenantAdmin: false }), true);
});
```

- [ ] **Step 2: Run test — expect FAIL** (markers missing)

Run: `node --test tests/test_nav_area_frontend.cjs`

- [ ] **Step 3: Add helpers to `console.js`** (exact markers above near `_navigationMode`)

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add channel/web/static/js/console.js tests/test_nav_area_frontend.cjs
git commit -m "feat(web): add nav area helpers for chat/admin split"
```

---

### Task 3: HTML shell — area chrome + admin-home view

**Files:**
- Modify: `channel/web/chat.html`
- Modify: `channel/web/static/css/console.css`
- Modify: `tests/test_nav_area_frontend.cjs` (HTML structure asserts)

- [ ] **Step 1: Failing HTML structure test**

```javascript
test('chat.html has area markers and admin home', () => {
    const html = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    assert.match(html, /id="nav-open-admin"/);
    assert.match(html, /id="nav-return-workbench"/);
    assert.match(html, /id="view-admin-home"/);
    assert.match(html, /data-nav-shell="workbench"/);
    assert.match(html, /data-nav-shell="admin"/);
});
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: HTML + CSS**

In `chat.html` sidebar:

1. Wrap workbench group (新建对话 button may stay global on workbench only) with `data-nav-shell="workbench"`.
2. Replace the static「管理控制台」label + keep admin groups under `data-nav-shell="admin"`.
3. On workbench shell, add:

```html
<a id="nav-open-admin" class="sidebar-item ..." role="link" href="/admin" target="cow-admin" rel="noopener">
  <i class="fas fa-gauge-high ..."></i>
  <span data-i18n="nav_admin_console">管理控制台</span>
</a>
```

4. On admin shell, add at top of admin nav:

```html
<a id="nav-return-workbench" class="sidebar-item ..." href="/chat" target="cow-workbench" rel="noopener">
  <i class="fas fa-arrow-left ..."></i>
  <span data-i18n="nav_return_workbench">返回工作台</span>
</a>
```

5. Add view (near other views):

```html
<div id="view-admin-home" class="view">
  <div class="admin-home">
    <h1 data-i18n="admin_home_title">管理控制台</h1>
    <p data-i18n="admin_home_hint">选择左侧菜单管理智能体、组织与平台配置。</p>
    <div id="admin-home-shortcuts" class="admin-home-shortcuts"></div>
  </div>
</div>
```

CSS (`console.css`):

```css
/* Path-based nav areas: hide the other shell's sidebar blocks */
#app[data-nav-area="workbench"] [data-nav-shell="admin"] { display: none !important; }
#app[data-nav-area="admin"] [data-nav-shell="workbench"] { display: none !important; }
#app[data-nav-area="admin"] #sidebar-new-chat { display: none !important; }
```

- [ ] **Step 4: Run HTML test — PASS**

- [ ] **Step 5: Commit**

```bash
git add channel/web/chat.html channel/web/static/css/console.css tests/test_nav_area_frontend.cjs
git commit -m "feat(web): mark workbench/admin shells and admin home view"
```

---

### Task 4: Wire boot, permissions, navigateTo, i18n

**Files:**
- Modify: `channel/web/static/js/console.js`
- Modify: `tests/test_nav_area_frontend.cjs`

- [ ] **Step 1: Extend tests for qualification wiring expectations** (string presence / behavior notes):

Assert console.js contains:

- `data-nav-area` assignment from `_navAreaFromPath`
- `VIEW_META.admin-home`
- `openAdminConsole` / click handler for `#nav-open-admin` using `_openNavArea('admin')`
- `_qualifyAdminConsoleEntry` used when toggling `#nav-open-admin` visibility
- unauthorized admin boot redirects or `navigate` to `/chat`

- [ ] **Step 2: Implement**

1. On `initApp` / where `data-nav-mode` is set:

```javascript
const area = _navAreaFromPath(location.pathname);
if (appEl) {
    appEl.setAttribute('data-nav-mode', _navigationMode());
    appEl.setAttribute('data-nav-area', area);
}
if (area === 'admin' && currentView === 'chat') {
    // defer actual navigate until after permission gate; default intent
    window.__COW_ADMIN_DEFAULT_VIEW__ = 'admin-home';
}
```

2. `VIEW_META`:

```javascript
'admin-home': { group: 'nav_admin_console', page: 'admin_home_title', console: null },
```

3. `_applySidebarPermissions`: after computing `isPlatformAdmin` / `isTenantAdmin`, set:

```javascript
const showAdminEntry = _qualifyAdminConsoleEntry({
    identityMode: _identityMode(),
    isPlatformAdmin,
    isTenantAdmin,
});
document.getElementById('nav-open-admin')?.classList.toggle('hidden', !showAdminEntry);
```

For admin area boot in database mode: if `!showAdminEntry`, `location.replace('/chat')` (or assign) once context known.

Keep existing per-item admin page hiding. On workbench, admin groups are already CSS-hidden via `data-nav-shell`.

4. Click handlers:

```javascript
document.getElementById('nav-open-admin')?.addEventListener('click', (e) => {
    e.preventDefault();
    _openNavArea('admin');
});
document.getElementById('nav-return-workbench')?.addEventListener('click', (e) => {
    e.preventDefault();
    _openNavArea('workbench');
});
```

5. `navigateTo` cross-area:

```javascript
const targetArea = (VIEW_META[viewId]?.console || '').indexOf('admin.') === 0 || viewId === 'admin-home'
    ? 'admin' : 'workbench';
const here = _navAreaFromPath(location.pathname);
if (targetArea !== here && viewId !== 'admin-home' /* handled */) {
    if (targetArea === 'admin') {
        _openNavArea('admin', '/admin');
        // optional: sessionStorage.setItem('cow_admin_pending_view', viewId);
        return;
    }
    _openNavArea('workbench', '/chat');
    return;
}
```

On admin boot, if `sessionStorage.cow_admin_pending_view`, `navigateTo` it then clear; else `navigateTo('admin-home')`.

6. `initAdminHomeView`: fill `#admin-home-shortcuts` from visible admin `.sidebar-item[data-view]` links.

7. i18n keys (zh/zh-TW/en): `nav_return_workbench`, `admin_home_title`, `admin_home_hint`, `nav_admin_denied`.

- [ ] **Step 3: Run** `node --test tests/test_nav_area_frontend.cjs` and relevant pytest — PASS

- [ ] **Step 4: Manual smoke** (if server up): `/chat` workbench-only; admin entry opens `/admin`; overview shows; return reuses chat tab.

- [ ] **Step 5: Commit**

```bash
git add channel/web/static/js/console.js tests/test_nav_area_frontend.cjs
git commit -m "feat(web): wire path-based workbench/admin navigation"
```

---

### Task 5: Deprecate stacked classic sidebar + verification

**Files:**
- Modify: `config.py` comment for `web_navigation_mode`
- Modify: `docs/superpowers/specs/2026-09-09-chat-admin-nav-split-design.md` only if behavior note needed (optional)

- [ ] **Step 1:** Update `config.py` comment: path (`/chat`|`/admin`) is source of truth; `classic`/`split` no longer stack admin under workbench.

- [ ] **Step 2:** Ensure CSS path rules win over any leftover classic stacked display (no admin groups on `/chat`).

- [ ] **Step 3:** Run full related tests:

```bash
python -m pytest tests/test_web_navigation_mode.py -v
node --test tests/test_nav_area_frontend.cjs
node --test tests/test_sidebar_account_frontend.cjs
```

- [ ] **Step 4: Commit**

```bash
git add config.py channel/web/static/css/console.css
git commit -m "chore(web): document path-based nav; retire stacked classic sidebar"
```

---

## Self-review checklist

1. Spec coverage: route, new tab, return focus, admin-home, tenant/platform entry gate, same shell, unauthorized redirect — each has a task.
2. No placeholders in steps.
3. Helpers `_navAreaFromPath` / `_openNavArea` / `_qualifyAdminConsoleEntry` names consistent across tasks.
