# Admin Console Home Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/admin` landing with a real-KPI dashboard (4 cards + permission-filtered shortcut grid) matching the approved design.

**Architecture:** Add `ConversationStore.count_messages_between`, expose `GET /api/admin/overview` that aggregates agent count / today’s messages / member count / system status, then redesign `view-admin-home` + `initAdminHomeView` to render KPIs and rich navigable cards.

**Tech Stack:** Python (web.py handlers), SQLite conversation store, vanilla JS + CSS in `channel/web`, pytest + node assert tests.

**Spec:** `docs/superpowers/specs/2026-09-09-admin-home-dashboard-design.md`

---

## File map

| File | Responsibility |
| --- | --- |
| `agent/memory/conversation_store.py` | `count_messages_between(start_ts, end_ts) -> int` + optional index |
| `tests/test_conversation_message_counts.py` | Day-boundary message count tests |
| `channel/web/admin_overview.py` | Pure builder `build_admin_overview(ctx, …)` + `AdminOverviewHandler` |
| `channel/web/web_channel.py` | Register `/api/admin/overview` URL + import handler |
| `tests/test_admin_overview.py` | Auth + KPI aggregation tests |
| `channel/web/chat.html` | KPI + shortcut markup hooks in `#view-admin-home` |
| `channel/web/static/css/console.css` | KPI row + rich shortcut card styles |
| `channel/web/static/js/console.js` | Fetch overview, render KPIs/shortcuts, i18n |
| `tests/test_admin_home_frontend.cjs` | Markup + i18n key presence checks |

---

### Task 1: ConversationStore message day count

**Files:**
- Modify: `agent/memory/conversation_store.py`
- Create: `tests/test_conversation_message_counts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_conversation_message_counts.py
import tempfile
import time
from pathlib import Path

from agent.memory.conversation_store import ConversationStore


def _store():
    return ConversationStore(Path(tempfile.mkdtemp()) / "index.db")


def test_count_messages_between_respects_bounds():
    store = _store()
    store.append_messages("s1", [
        {"role": "user", "content": "old"},
    ], channel_type="web")
    # Force created_at on the inserted row to a known past second.
    with store._lock:
        conn = store._connect()
        try:
            conn.execute("UPDATE messages SET created_at = ?", (1_700_000_000,))
            conn.commit()
        finally:
            conn.close()

    store.append_messages("s1", [
        {"role": "assistant", "content": "new"},
    ], channel_type="web")

    start = 1_700_000_100
    end = int(time.time()) + 10
    assert store.count_messages_between(start, end) == 1
    assert store.count_messages_between(1_700_000_000, 1_700_000_001) == 1
    assert store.count_messages_between(0, 1) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_conversation_message_counts.py -v`  
Expected: FAIL (`count_messages_between` missing)

- [ ] **Step 3: Implement `count_messages_between`**

In `agent/memory/conversation_store.py`, next to `get_stats`:

```python
def count_messages_between(self, start_ts: int, end_ts: int) -> int:
    """Count messages with created_at in [start_ts, end_ts)."""
    start_ts = int(start_ts)
    end_ts = int(end_ts)
    if end_ts <= start_ts:
        return 0
    with self._lock:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at < ?",
                (start_ts, end_ts),
            ).fetchone()
            return int(row[0] or 0)
        finally:
            conn.close()
```

Also add to `_DDL` (and ensure migration creates it if `_migrate` pattern requires):

```sql
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages (created_at);
```

If indexes are only in `_DDL`, new DBs get it; for existing DBs, add a guarded `CREATE INDEX IF NOT EXISTS` in `_migrate` the same way other indexes are added in this file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_conversation_message_counts.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/memory/conversation_store.py tests/test_conversation_message_counts.py
git commit -m "feat(memory): count messages in a time range"
```

---

### Task 2: `build_admin_overview` + HTTP handler

**Files:**
- Create: `channel/web/admin_overview.py`
- Modify: `channel/web/web_channel.py` (import + `_WEB_URLS` entry)
- Create: `tests/test_admin_overview.py`

- [ ] **Step 1: Write failing API / builder tests**

```python
# tests/test_admin_overview.py
"""Admin overview KPI aggregation."""
from channel.web.admin_overview import (
    build_admin_overview,
    local_day_bounds,
)


def test_local_day_bounds_are_half_open_24h():
    start, end = local_day_bounds(1_700_000_000)  # fixed epoch
    assert end - start == 86400
    assert start <= 1_700_000_000 < end


def test_build_overview_legacy_member_unavailable():
    # ctx=None simulates legacy mode path used by the handler.
    payload = build_admin_overview(
        ctx=None,
        agent_count=2,
        messages_today=5,
        member_count=None,
        system_status="ok",
        day_start=100,
        timezone_label="local",
    )
    assert payload["kpis"]["agent_count"] == 2
    assert payload["kpis"]["messages_today"] == 5
    assert payload["kpis"]["member_count"] is None
    assert payload["meta"]["member_count_scope"] == "unavailable"
    assert payload["kpis"]["system_status"] == "ok"
```

Add an integration-style test that hits the handler with web.py app fixture **if** the repo already has a pattern (search `tests/test_web_navigation_mode.py` / admin handler tests). Prefer calling `build_admin_overview` + a small `_gather_*` unit with mocks; if an HTTP fixture exists, also assert:

- Unauthorized → 401/403
- Database tenant_admin with tenant → `member_count_scope: "tenant"`

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_admin_overview.py -v`  
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `channel/web/admin_overview.py`**

```python
# encoding:utf-8
"""Admin console overview KPIs (GET /api/admin/overview)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import web

from channel.web.auth_handlers import (
    _error,
    _is_database,
    _json,
    _require_context,
)


def local_day_bounds(now_ts: Optional[int] = None) -> Tuple[int, int]:
    """Return [start, end) unix seconds for the server's local calendar day."""
    now = int(now_ts if now_ts is not None else time.time())
    local = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    start = int(local.timestamp())
    end = int((local + timedelta(days=1)).timestamp())
    return start, end


def build_admin_overview(
    *,
    ctx,
    agent_count: int,
    messages_today: int,
    member_count: Optional[int],
    system_status: str,
    day_start: int,
    timezone_label: str = "local",
) -> Dict[str, Any]:
    if member_count is None:
        scope = "unavailable"
    elif ctx is None or not getattr(ctx, "tenant_id", None):
        scope = "none"
    else:
        scope = "tenant"
    return {
        "status": "ok",
        "kpis": {
            "agent_count": int(agent_count),
            "messages_today": int(messages_today),
            "member_count": member_count,
            "system_status": system_status if system_status in ("ok", "degraded") else "degraded",
        },
        "meta": {
            "day_start": int(day_start),
            "timezone": timezone_label,
            "member_count_scope": scope,
        },
    }


def _require_admin_console_access(ctx) -> None:
    """Same gate as opening /admin: platform admin or tenant_admin."""
    if not _is_database():
        return
    if ctx.is_platform_admin:
        return
    if ctx.is_tenant_admin and ctx.tenant_id:
        return
    raise web.HTTPError(
        "403 Forbidden",
        {"Content-Type": "application/json"},
        _error("forbidden", 403, "forbidden"),
    )


def _gather_agent_count(ctx) -> int:
    # Import lazily to avoid circular imports at module load.
    from channel.web.web_channel import _tenant_agents_projection, _agent_admin_service
    if ctx is not None:
        return len((_tenant_agents_projection(ctx).get("agents") or []))
    snap = _agent_admin_service().snapshot()
    agents = snap.get("agents") or []
    return len(agents)


def _gather_messages_today(ctx, start_ts: int, end_ts: int) -> int:
    from agent.memory import get_conversation_store
    from agent.registry import get_agent_registry
    from channel.web.web_channel import _tenant_ids_for_context

    total = 0
    visible = _tenant_ids_for_context(ctx) if ctx is not None else None
    for profile in get_agent_registry().list(include_disabled=False):
        if visible is not None and profile.id not in visible:
            continue
        try:
            store = get_conversation_store(profile.workspace)
            total += store.count_messages_between(start_ts, end_ts)
        except Exception:
            continue
    return total


def _gather_member_count(ctx) -> Optional[int]:
    if not _is_database():
        return None
    if ctx is None or not ctx.tenant_id:
        return 0
    from channel.web.auth_handlers import _get_service
    svc = _get_service()
    result = svc.list_members(ctx.tenant_id, status="active", page=1, page_size=1)
    return int(result.get("total") or 0)


class AdminOverviewHandler:
    def GET(self):
        from channel.web.web_channel import _require_auth, _db_scope
        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        try:
            with _db_scope() as ctx:
                if _is_database():
                    ctx = _require_context(require_tenant=False)
                    _require_admin_console_access(ctx)
                day_start, day_end = local_day_bounds()
                try:
                    agent_count = _gather_agent_count(ctx)
                    messages_today = _gather_messages_today(ctx, day_start, day_end)
                    member_count = _gather_member_count(ctx)
                    system_status = "ok"
                except Exception:
                    agent_count = 0
                    messages_today = 0
                    member_count = None if not _is_database() else 0
                    system_status = "degraded"
                payload = build_admin_overview(
                    ctx=ctx,
                    agent_count=agent_count,
                    messages_today=messages_today,
                    member_count=member_count,
                    system_status=system_status,
                    day_start=day_start,
                )
                return _json(payload)
        except web.HTTPError:
            raise
        except Exception as e:
            return _error(str(e), 500, "internal_error")
```

Wire in `web_channel.py`:

1. Add to imports from `channel.web.admin_overview` (or import only the handler class at URL map time).
2. Add URL near other admin routes:

```python
'/api/admin/overview', 'AdminOverviewHandler',
```

Ensure the class is importable by web.py (same pattern as `TenantMembersHandler` — either import into `web_channel` module namespace or keep handler defined where URLs resolve). Follow existing pattern: handlers referenced in `_WEB_URLS` are attributes of the module that constructs the app — typically imported into `web_channel.py`:

```python
from channel.web.admin_overview import AdminOverviewHandler
```

- [ ] **Step 4: Fix circular import if needed**

If importing `_tenant_agents_projection` from `web_channel` inside `admin_overview` fails at import time, keep gathers **lazy** (as shown). Do not import `admin_overview` from the top of functions that `web_channel` calls during import.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_admin_overview.py tests/test_conversation_message_counts.py -v`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add channel/web/admin_overview.py channel/web/web_channel.py tests/test_admin_overview.py
git commit -m "feat(admin): add /api/admin/overview KPI endpoint"
```

---

### Task 3: Admin-home HTML + CSS layout

**Files:**
- Modify: `channel/web/chat.html` (`#view-admin-home`)
- Modify: `channel/web/static/css/console.css`

- [ ] **Step 1: Replace admin-home body markup**

Replace the current `#view-admin-home` inner content with:

```html
<div id="view-admin-home" class="view">
    <div class="p-6 h-full overflow-y-auto">
        <div class="admin-home">
            <h1 class="admin-home-title" data-i18n="admin_home_title">管理控制台</h1>
            <p class="admin-home-hint" data-i18n="admin_home_hint">选择左侧菜单管理智能体、组织与平台配置。</p>
            <div id="admin-home-kpis" class="admin-home-kpis" aria-live="polite"></div>
            <div id="admin-home-shortcuts" class="admin-home-shortcuts"></div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Add CSS** (append near existing `.admin-home-shortcuts` rules)

```css
.admin-home { max-width: 72rem; margin: 0 auto; }
.admin-home-title {
    font-size: 1.5rem; font-weight: 600;
    color: #1e293b;
}
.dark .admin-home-title { color: #f1f5f9; }
.admin-home-hint { margin-top: 0.5rem; font-size: 0.875rem; color: #64748b; }
.admin-home-kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 1.5rem 0 1.25rem;
}
@media (max-width: 900px) {
    .admin-home-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
    .admin-home-kpis { grid-template-columns: 1fr; }
}
.admin-home-kpi {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem;
    padding: 1rem 1.1rem;
    border-radius: 0.9rem;
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: #fff;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.dark .admin-home-kpi {
    background: rgba(255,255,255,0.04);
    border-color: rgba(255,255,255,0.08);
}
.admin-home-kpi-label { font-size: 0.8rem; color: #64748b; }
.admin-home-kpi-value {
    margin-top: 0.35rem; font-size: 1.6rem; font-weight: 700;
    color: #0f172a; line-height: 1.15;
}
.dark .admin-home-kpi-value { color: #f8fafc; }
.admin-home-kpi-sub { margin-top: 0.25rem; font-size: 0.75rem; color: #94a3b8; }
.admin-home-kpi-value.is-ok { color: #16a34a; font-size: 1.25rem; }
.admin-home-kpi-value.is-bad { color: #dc2626; font-size: 1.25rem; }
.admin-home-kpi-icon {
    width: 2.25rem; height: 2.25rem; border-radius: 0.7rem;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; font-size: 0.95rem;
}
.admin-home-shortcuts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
}
@media (max-width: 720px) {
    .admin-home-shortcuts { grid-template-columns: 1fr; }
}
.admin-home-shortcuts .admin-home-shortcut {
    display: flex; align-items: flex-start; gap: 0.85rem;
    padding: 1rem 1.1rem;
    border-radius: 0.9rem;
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: #fff;
    cursor: pointer; text-align: left; width: 100%; color: inherit;
    transition: border-color .15s ease, box-shadow .15s ease;
}
.dark .admin-home-shortcuts .admin-home-shortcut {
    background: rgba(255,255,255,0.04);
    border-color: rgba(255,255,255,0.08);
}
.admin-home-shortcuts .admin-home-shortcut:hover {
    border-color: rgba(74, 190, 110, 0.45);
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.admin-home-shortcut-icon {
    width: 2.4rem; height: 2.4rem; border-radius: 0.75rem;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; color: #fff; font-size: 0.95rem;
}
.admin-home-shortcut-copy { min-width: 0; }
.admin-home-shortcut-title {
    display: block; font-size: 0.95rem; font-weight: 600; color: #0f172a;
}
.dark .admin-home-shortcut-title { color: #f1f5f9; }
.admin-home-shortcut-desc {
    display: block; margin-top: 0.25rem; font-size: 0.8rem;
    color: #64748b; line-height: 1.4;
}
```

Remove or override the older `.admin-home-shortcuts` max-width/`sm:grid-cols-2` Tailwind dependency on the container (`max-w-3xl` removed).

- [ ] **Step 3: Commit**

```bash
git add channel/web/chat.html channel/web/static/css/console.css
git commit -m "style(admin): layout KPI row and rich shortcut cards"
```

---

### Task 4: Frontend render + i18n + navigation

**Files:**
- Modify: `channel/web/static/js/console.js`
- Create: `tests/test_admin_home_frontend.cjs`

- [ ] **Step 1: Add i18n keys** (zh / zh-TW / en blocks where sibling keys live)

```javascript
admin_home_kpi_agents: '智能体总数',
admin_home_kpi_messages_today: '今日消息数',
admin_home_kpi_members: '成员数',
admin_home_kpi_system: '系统状态',
admin_home_kpi_system_ok: '正常运行',
admin_home_kpi_system_bad: '异常',
admin_home_kpi_loading: '加载中…',
admin_home_kpi_dash: '—',
admin_home_desc_agents: '创建、配置和管理所有智能体，设置对话能力与权限',
admin_home_desc_skills: '配置插件工具与自定义技能，扩展智能体能力边界',
admin_home_desc_memory: '管理智能体的知识库与长期记忆，优化对话准确性',
admin_home_desc_config: '管理接入的大语言模型，配置调用配额与参数',
admin_home_desc_channels: '接入网页、微信、企业微信等多渠道消息入口',
admin_home_desc_system_user: '添加与管理平台成员，分配对应角色与权限',
admin_home_desc_org: '维护部门与人员架构，同步组织层级关系',
admin_home_desc_roles: '配置角色与权限策略，控制平台功能访问范围',
admin_home_desc_tenant: '多租户体系管理，实现数据与资源的隔离配置',
admin_home_desc_platform: '配置平台基础参数、安全策略与集成选项',
admin_home_desc_branding: '自定义平台Logo、名称与主题风格，打造专属品牌',
admin_home_desc_logs: '实时查看系统运行日志，排查问题与性能监控',
admin_home_desc_audit: '查看操作日志与安全审计记录，追溯所有行为',
```

Mirror zh-TW / EN with equivalent meaning (EN examples: `Agents`, `Messages today`, `Members`, `System status`, `Running normally`, `Degraded`).

- [ ] **Step 2: Replace `initAdminHomeView`**

```javascript
const ADMIN_HOME_DESC_KEYS = {
    agents: 'admin_home_desc_agents',
    skills: 'admin_home_desc_skills',
    memory: 'admin_home_desc_memory',
    config: 'admin_home_desc_config',
    channels: 'admin_home_desc_channels',
    system_user: 'admin_home_desc_system_user',
    org: 'admin_home_desc_org',
    roles: 'admin_home_desc_roles',
    tenant: 'admin_home_desc_tenant',
    platform: 'admin_home_desc_platform',
    branding: 'admin_home_desc_branding',
    logs: 'admin_home_desc_logs',
    audit: 'admin_home_desc_audit',
};

const ADMIN_HOME_SHORTCUT_COLORS = {
    agents: '#3b82f6', skills: '#eab308', memory: '#8b5cf6',
    config: '#14b8a6', channels: '#ef4444', system_user: '#22c55e',
    org: '#f43f5e', roles: '#f97316', tenant: '#64748b',
    platform: '#3b82f6', branding: '#a855f7', logs: '#10b981',
    audit: '#64748b',
};

function _adminHomeFormatInt(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return t('admin_home_kpi_dash');
    return x.toLocaleString();
}

function _renderAdminHomeKpis(kpis, meta) {
    const box = document.getElementById('admin-home-kpis');
    if (!box) return;
    const status = (kpis && kpis.system_status) || 'degraded';
    const member = kpis && kpis.member_count;
    const memberText = (member == null || (meta && meta.member_count_scope === 'unavailable'))
        ? t('admin_home_kpi_dash')
        : _adminHomeFormatInt(member);
    const statusOk = status === 'ok';
    box.innerHTML = `
      <div class="admin-home-kpi">
        <div>
          <div class="admin-home-kpi-label">${escapeHtml(t('admin_home_kpi_agents'))}</div>
          <div class="admin-home-kpi-value">${escapeHtml(_adminHomeFormatInt(kpis && kpis.agent_count))}</div>
        </div>
        <div class="admin-home-kpi-icon" style="background:#dbeafe;color:#2563eb"><i class="fas fa-cube"></i></div>
      </div>
      <div class="admin-home-kpi">
        <div>
          <div class="admin-home-kpi-label">${escapeHtml(t('admin_home_kpi_messages_today'))}</div>
          <div class="admin-home-kpi-value">${escapeHtml(_adminHomeFormatInt(kpis && kpis.messages_today))}</div>
        </div>
        <div class="admin-home-kpi-icon" style="background:#ccfbf1;color:#0d9488"><i class="fas fa-comment"></i></div>
      </div>
      <div class="admin-home-kpi">
        <div>
          <div class="admin-home-kpi-label">${escapeHtml(t('admin_home_kpi_members'))}</div>
          <div class="admin-home-kpi-value">${escapeHtml(memberText)}</div>
        </div>
        <div class="admin-home-kpi-icon" style="background:#ede9fe;color:#7c3aed"><i class="fas fa-user"></i></div>
      </div>
      <div class="admin-home-kpi">
        <div>
          <div class="admin-home-kpi-label">${escapeHtml(t('admin_home_kpi_system'))}</div>
          <div class="admin-home-kpi-value ${statusOk ? 'is-ok' : 'is-bad'}">${escapeHtml(statusOk ? t('admin_home_kpi_system_ok') : t('admin_home_kpi_system_bad'))}</div>
        </div>
        <div class="admin-home-kpi-icon" style="background:#dcfce7;color:#16a34a"><i class="fas fa-check"></i></div>
      </div>`;
}

function _renderAdminHomeShortcuts() {
    const box = document.getElementById('admin-home-shortcuts');
    if (!box) return;
    box.innerHTML = '';
    document.querySelectorAll('[data-nav-shell="admin"] .sidebar-item[data-view]').forEach(item => {
        if (item.classList.contains('hidden')) return;
        if (item.id === 'nav-admin-home') return;
        const viewId = item.dataset.view;
        if (!viewId || viewId === 'admin-home' || !VIEW_META[viewId]) return;
        const label = item.querySelector('[data-i18n]');
        const text = label ? label.textContent : t(VIEW_META[viewId].page);
        const icon = item.querySelector('i');
        const iconClass = icon ? icon.className : 'fas fa-circle';
        const descKey = ADMIN_HOME_DESC_KEYS[viewId];
        const desc = descKey ? t(descKey) : '';
        const color = ADMIN_HOME_SHORTCUT_COLORS[viewId] || '#64748b';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'admin-home-shortcut';
        btn.innerHTML =
            `<span class="admin-home-shortcut-icon" style="background:${color}" aria-hidden="true"><i class="${iconClass}"></i></span>` +
            `<span class="admin-home-shortcut-copy">` +
            `<span class="admin-home-shortcut-title"></span>` +
            `<span class="admin-home-shortcut-desc"></span></span>`;
        btn.querySelector('.admin-home-shortcut-title').textContent = text;
        btn.querySelector('.admin-home-shortcut-desc').textContent = desc;
        btn.addEventListener('click', () => navigateTo(viewId));
        box.appendChild(btn);
    });
}

function initAdminHomeView() {
    const kpiBox = document.getElementById('admin-home-kpis');
    if (kpiBox) {
        kpiBox.innerHTML = `<div class="admin-home-kpi"><div class="admin-home-kpi-label">${escapeHtml(t('admin_home_kpi_loading'))}</div></div>`;
    }
    _renderAdminHomeShortcuts();
    fetch('/api/admin/overview', { credentials: 'same-origin' })
        .then(r => r.json().then(body => ({ ok: r.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'ok') {
                _renderAdminHomeKpis({ system_status: 'degraded' }, {});
                return;
            }
            _renderAdminHomeKpis(body.kpis || {}, body.meta || {});
        })
        .catch(() => _renderAdminHomeKpis({ system_status: 'degraded' }, {}));
}
```

Ensure `escapeHtml` already exists in `console.js` (it does). Do **not** render any percent-delta elements.

- [ ] **Step 3: Frontend static test**

```javascript
// tests/test_admin_home_frontend.cjs
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { test } = require('node:test');

test('admin-home markup has KPI and shortcut hooks', () => {
    const html = fs.readFileSync(path.join(__dirname, '../channel/web/chat.html'), 'utf8');
    assert.match(html, /id="admin-home-kpis"/);
    assert.match(html, /id="admin-home-shortcuts"/);
    assert.match(html, /id="view-admin-home"/);
});

test('console.js has honest KPI labels and overview fetch', () => {
    const js = fs.readFileSync(path.join(__dirname, '../channel/web/static/js/console.js'), 'utf8');
    assert.match(js, /admin_home_kpi_messages_today/);
    assert.match(js, /admin_home_kpi_members/);
    assert.match(js, /\/api\/admin\/overview/);
    assert.doesNotMatch(js, /较上月|较昨日|99\.98%/);
});
```

- [ ] **Step 4: Run tests**

```bash
node --test tests/test_admin_home_frontend.cjs
.venv/bin/python -m pytest tests/test_admin_overview.py tests/test_conversation_message_counts.py -v
```

Expected: all PASS

- [ ] **Step 5: Manual smoke**

1. Open `http://localhost:9899/admin` (hard refresh).
2. Confirm four KPI cards show real numbers (not mock 128/8642).
3. Confirm no % delta text.
4. Click **智能体管理** shortcut → agents view; sidebar highlights correctly.
5. Hidden platform-only items stay absent for tenant_admin.

- [ ] **Step 6: Commit**

```bash
git add channel/web/static/js/console.js tests/test_admin_home_frontend.cjs
git commit -m "feat(admin): render overview KPIs and navigable shortcut cards"
```

---

### Task 5: Verification + polish

- [ ] **Step 1: Full related regression**

```bash
.venv/bin/python -m pytest tests/test_admin_overview.py tests/test_conversation_message_counts.py tests/test_nav_area_frontend.cjs -q
node --test tests/test_admin_home_frontend.cjs tests/test_nav_area_frontend.cjs
```

Note: run node tests with `node --test …`; do not pass `.cjs` to pytest.

- [ ] **Step 2: Fix any failures from circular imports / auth**

Common fixes:
- Lazy-import `web_channel` helpers inside gather functions only.
- Database mode without tenant: `member_count=0`, `member_count_scope="none"`.
- Platform item `data-view="platform"` remains `perm-only-platform` filtered by existing sidebar class.

- [ ] **Step 3: Final commit if polish needed**

```bash
git add -u
git commit -m "fix(admin): polish overview edge cases"
```

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| `GET /api/admin/overview` | Task 2 |
| Real agent_count | Task 2 `_gather_agent_count` |
| 今日消息数 via day bounds | Task 1 + Task 2 |
| 成员数 / unavailable in legacy | Task 2 |
| system_status ok/degraded, no fake % | Task 2 + Task 4 |
| Shortcut cards + navigateTo | Task 4 |
| Permission filter via sidebar visibility | Task 4 |
| No MoM/DoD deltas | Task 4 test `doesNotMatch` |
| Tests | Tasks 1–5 |

## Placeholder scan

Plan contains concrete code, commands, and file paths; no TBD/TODO implementation holes.
