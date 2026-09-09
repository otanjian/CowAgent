# Design: Admin Console Home Dashboard

Date: 2026-09-09  
Status: Approved for implementation (Approach A)

## Goal

Replace the minimal `/admin` landing (`admin-home`) with a dashboard matching the provided mock:

1. **Four KPI cards** at the top with **correct real numbers** (no fabricated values or fake % deltas).
2. **Two-column shortcut cards** below: icon + title + short description; click opens the corresponding admin menu view via existing `navigateTo`.
3. Cards respect the same **visibility / permission** rules as the admin sidebar.

## Decisions (locked)

| Topic | Choice |
| --- | --- |
| Data access | New aggregated API `GET /api/admin/overview` |
| Fake mock numbers (128 / 8642 / 99.98%) | **Forbidden** |
| Mock % deltas (较上月 / 较昨日) | **Out of scope** — no historical series today |
| 「今日调用量」label | **今日消息数** — count of messages with `created_at` in local calendar day |
| 「活跃用户」label | **成员数** — active tenant members (`status=active` total) |
| 系统状态 | Derived from `GET /api/health` semantics (ok → 正常运行; else 异常) |
| Shortcut list | Same views as admin sidebar items the caller can see |
| Auth for overview | Same gate as opening `/admin`: platform admin **or** current-tenant `tenant_admin` (database mode); legacy mode allowed when console is shared-password |

## Non-goals

- Historical MoM / DoD percentage comparisons
- Token / LLM call metering dashboard (no reliable day-scoped HTTP metric yet)
- Rewriting admin into a new SPA framework
- Changing RBAC semantics beyond reusing existing checks
- Charts / time-series graphs

## Architecture

```text
Browser (admin-home)
    │
    ├─ GET /api/admin/overview ──► AdminOverviewHandler
    │       ├─ agent_count          (same visibility as GET /api/agents)
    │       ├─ messages_today       (ConversationStore day count)
    │       ├─ member_count         (IdentityService list_members total, status=active)
    │       └─ system_status        (liveness: ok | degraded)
    │
    └─ shortcut cards (client)
            └─ from visible admin sidebar items + static description i18n
            └─ click → navigateTo(viewId)
```

### Why a new API (not frontend fan-out)

- Single permission gate and tenant scope
- Avoids loading full agent/member lists just to count
- Keeps “correctness” definitions in one server place, testable without browser

## KPI definitions (correctness)

| Card UI title | Field | Definition | Empty / failure |
| --- | --- | --- | --- |
| 智能体总数 | `agent_count` | Count of agents the caller would see from `GET /api/agents` (tenant projection in DB mode; enabled+visible). | `0` |
| 今日消息数 | `messages_today` | `COUNT(*)` on `messages` where `created_at` ∈ `[start_of_local_day, start_of_next_day)` (server local TZ, documented). Prefer index-friendly range query; add `idx_messages_created_at` if needed. | `0` |
| 成员数 | `member_count` | Active memberships in **current tenant** (`list_members` / equivalent with `status=active` → `total`). Platform admin without tenant context: use selected tenant from request context; if none, return `0` and `member_count_scope: "none"`. | `0` |
| 系统状态 | `system_status` | `"ok"` if process can answer the overview request and core health is ok; `"degraded"` otherwise. UI: `正常运行` / `异常`. Subtitle: show version from existing version helper **or omit**; **never** invent availability %. | show 异常 + short error |

Response shape (illustrative):

```json
{
  "status": "ok",
  "kpis": {
    "agent_count": 3,
    "messages_today": 42,
    "member_count": 12,
    "system_status": "ok"
  },
  "meta": {
    "day_start": 1757356800,
    "timezone": "local",
    "member_count_scope": "tenant"
  }
}
```

Legacy (non-database) identity mode:

- Still allow overview for whoever can open `/admin`.
- `member_count` may be `null` with `member_count_scope: "unavailable"`; UI shows `—` and keeps the card.

## Shortcut cards

### Layout

- Responsive grid: 2 columns on desktop (≥640px), 1 column on narrow screens.
- Each card: colored icon tile (left), bold title, muted one-line description.
- Hover / focus affordance consistent with existing console cards; clickable as `<button>` or role=button link.
- Order: follow admin sidebar order (智能体开发 → 模型与接入 → 组织与权限 → 平台运维), skipping hidden / denied items.
- Exclude `admin-home` itself from the shortcut list.

### Descriptions

Static i18n keys per view, e.g. `admin_home_desc_agents`, aligned with mock copy (Chinese / zh-TW / EN). Missing key falls back to empty description (title still works).

### Navigation

`click` → `navigateTo(viewId)` — same path as sidebar. Must update sidebar active state and open parent menu group if collapsed.

### Permission filtering

Build the list from the same visibility rules already applied to `[data-nav-shell="admin"] .sidebar-item[data-view]` (hidden classes / perm gates). Do not show a shortcut the sidebar would hide.

## UI structure (replace current admin-home body)

```text
#view-admin-home
  .admin-home (wider max-width than today, e.g. max-w-5xl / max-w-6xl)
    header: title + short hint (keep i18n keys; may retitle hint)
    #admin-home-kpis          → 4 KPI cards
    #admin-home-shortcuts     → 2-col shortcut grid
```

Loading: KPI skeletons or muted placeholders until overview returns.  
Error: toast or inline banner; shortcuts can still render from sidebar even if KPIs fail.

## Auth / route wiring

- Register `GET /api/admin/overview` next to other admin routes (prefer `admin_handlers.py` + URL map in `web_channel.py`).
- Guard:
  - Database mode: `_require_context` then platform admin **or** (`tenant_admin` ∧ `tenant_id`).
  - Non-database: allow authenticated console session (same as other management pages).
- Reuse existing identity helpers; do not invent a new permission string unless one already exists for “admin console entry”.

## Testing

1. **API unit/integration**: overview returns expected counts with fixture agents/messages/members; unauthorized → 403; legacy member_count null/unavailable.
2. **ConversationStore**: day-boundary count (messages just before/after midnight).
3. **Frontend**: shortcut click navigates; hidden sidebar item not listed; KPI labels use real i18n (今日消息数 / 成员数); no percent delta nodes in DOM.
4. Extend `tests/test_nav_area_frontend.cjs` or add a focused admin-home frontend test for markup hooks (`#admin-home-kpis`, desc attributes).

## Implementation sketch (for the plan)

1. `ConversationStore.count_messages_since(ts)` or `count_messages_between(start, end)`.
2. `AdminOverviewHandler` + route.
3. Redesign `view-admin-home` HTML/CSS; expand `initAdminHomeView` to fetch KPIs + render rich shortcuts.
4. i18n for KPI titles, status strings, and per-view descriptions.
5. Tests as above.

## Out of scope follow-ups (explicit)

- True “模型调用量” once metering emits day buckets.
- DAU / last-login “活跃用户”.
- Availability SLA %.
- Sparkline / chart row under KPIs.
