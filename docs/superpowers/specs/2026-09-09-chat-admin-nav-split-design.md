# Design: Workbench `/chat` vs Admin `/admin` Navigation Split

Date: 2026-09-09  
Status: Approved for implementation

## Goal

Separate daily work from administration:

- `http://localhost:9899/chat` shows **only workbench** menus.
- Clicking **管理控制台** opens a **new browser tab** at `http://localhost:9899/admin` with the full admin console menus.
- Admin lands on a dedicated **overview home**; return to workbench **reuses/focuses** the existing `/chat` tab when possible.

## Decisions (locked)

| Topic | Choice |
| --- | --- |
| Open admin | New tab → `/admin` |
| Return to workbench | Prefer existing `/chat` tab (named window); else open `/chat` |
| `/admin` landing | New admin overview home page |
| Who sees「管理控制台」entry | Platform admin **or** current-tenant `tenant_admin` only (database mode) |
| Technical approach | Dual route, **same** `chat.html` / JS shell |

## Non-goals

- Rewriting the console into a new frontend framework
- Splitting `console.js` into separate admin/workbench bundles (can be a later optimization)
- Changing RBAC / API authorization semantics
- In-page area switch only (`web_navigation_mode=split` without `/admin` URL)

## Architecture

```text
GET /chat  → ChatHandler → chat.html  → nav area = workbench
GET /admin → ChatHandler → chat.html  → nav area = admin
```

Both paths serve the same HTML shell and assets. On boot, JS reads `location.pathname`:

- path starts with `/admin` → `data-nav-area="admin"`
- otherwise (including `/chat`) → `data-nav-area="workbench"`

Layout CSS/JS show only the menus for that area. Auth cookies, `/auth/me`, `/auth/context`, tenant selection, and all `/api/*` calls stay shared and unchanged.

### Named windows (tab reuse)

| From | Action | Window name |
| --- | --- | --- |
| Workbench → Admin | `window.open('/admin', 'cow-admin')` | `cow-admin` |
| Admin → Workbench | `window.open('/chat', 'cow-workbench')` | `cow-workbench` |

Browsers generally cannot focus an arbitrary existing tab; named targets **reuse** the same named window if still open. If absent, a new tab opens. Document this limitation in UI copy if needed (“在已有工作台标签中打开”).

## Navigation UX

### `/chat` (workbench)

- Sidebar: brand, 新建对话, **工作台** group items only (`chat`, `history`, `agent-workbench`, `todo`, `tasks`, `knowledge`, `scenes`).
- Replace the stacked admin groups with a single entry control **管理控制台** (visible only when qualified).
- Click → `window.open('/admin', 'cow-admin')` (does not navigate away from the chat tab).
- Account footer, language, appearance unchanged.

### `/admin` (management console)

- Sidebar: brand, **返回工作台** entry, then existing admin groups:
  - 智能体开发 / 模型与接入 / 组织与权限 / 平台运维（platform-scoped still platform-admin-only）
- Default view: new `admin-home` overview (cards or short links to permitted admin pages). No requirement for charts/metrics in v1 — a simple welcome + permitted shortcuts is enough.
- Clicking admin menu items uses existing `navigateTo(viewId)` within the admin tab.
- **返回工作台** → `window.open('/chat', 'cow-workbench')`.

### Cross-area deep links

- If code on `/chat` calls `navigateTo` for an `admin.*` view → open/reuse `cow-admin` at `/admin` and navigate there (or `location.assign` within admin window). Do not silently show admin views inside the workbench shell.
- If code on `/admin` calls `navigateTo('chat')` (or other workbench views) → open/reuse workbench window instead of rendering chat inside admin.
- Direct URL `/admin` while unqualified (database mode, not platform admin and not tenant_admin) → redirect to `/chat` with a short denial toast/banner.
- Legacy identity mode: keep admin entry visible (same as today’s full console for shared-password deployments).

## Permission rules

**Entry visibility (工作台「管理控制台」):**

- Database mode: `user.is_platform_admin === true` **OR** `/auth/context.is_tenant_admin === true`.
- Do **not** use “any readable `admin.*` page” alone for this entry (stricter than today’s “show admin area if any admin page readable”).
- Legacy mode: show the entry.

**Inside `/admin`:**

- Keep existing `_applySidebarPermissions` / `console_pages` filtering for each admin item and platform-only groups.
- Overview home only lists shortcuts the user can actually open.

## Relation to `web_navigation_mode`

Config today: `classic` | `split` (in-page area switch on `/chat`).

After this change:

- **Path is the source of truth** for which area is shown (`/chat` vs `/admin`).
- `web_navigation_mode=classic` stacked admin-under-workbench behavior is **retired** for the default product UX; both paths use the split-by-URL presentation.
- Keep the config key temporarily: treat `classic` and `split` the same as path-based split (or map both to the new behavior) to avoid breaking deploys; document deprecation. Do not reintroduce stacked admin menus on `/chat`.

## Files likely touched

| File | Role |
| --- | --- |
| `channel/web/web_channel.py` | Register `/admin`; same handler as `/chat` |
| `channel/web/chat.html` | Workbench-only sidebar markup; admin entry; admin-home view; return entry; area attributes |
| `channel/web/static/js/console.js` | Path→area, open/return helpers, gate entry, cross-area navigate, admin-home init, VIEW_META |
| `channel/web/static/css/console.css` | Area-scoped sidebar visibility; admin-home layout |
| `config.py` / i18n strings | Optional deprecation note; new i18n keys |
| `tests/test_web_navigation_mode.py` | `/admin` serves shell; injection still works |
| Frontend Node tests (e.g. `tests/test_identity_admin_frontend.cjs` or new nav test) | Entry visibility + open target |

## Testing

1. Backend: `GET /admin` returns HTML with same bust/injection as `/chat`.
2. Frontend unit/DOM: workbench area hides `.sidebar-hidden-admin-area` groups; shows admin entry when tenant/platform admin; hides for ordinary member.
3. Frontend: admin area hides workbench group; shows admin groups; default view `admin-home`.
4. Manual: new-tab open + named-window reuse; unauthorized `/admin` redirects to `/chat`.
5. Regression: existing admin pages still open via `navigateTo` inside `/admin`; workbench chat still works on `/chat`.

## Out of scope follow-ups

- Hash/History deep links per admin page (`/admin#/roles`) — desirable later; not required for v1 if `navigateTo` state is enough within the session.
- Separate lightweight `admin.html` bundle.
- Admin overview metrics/dashboards.
