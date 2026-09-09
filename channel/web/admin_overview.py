# encoding:utf-8
"""Admin console overview KPIs (GET /api/admin/overview)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import web

from channel.web.auth_handlers import (
    _error,
    _is_database,
    _json,
    _require_context,
    _tenant_header,
)


def local_day_bounds(now_ts: Optional[int] = None) -> Tuple[int, int]:
    """Return [start, end) unix seconds for the server's local calendar day."""
    now = int(now_ts if now_ts is not None else time.time())
    local = datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
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
            "system_status": (
                system_status if system_status in ("ok", "degraded") else "degraded"
            ),
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


def _overview_payload(ctx) -> str:
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


class AdminOverviewHandler:
    def GET(self):
        from auth.runtime import to_runtime_identity
        from channel.web.web_channel import _require_auth
        from common.runtime_identity import use_identity

        _require_auth()
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        try:
            if _is_database():
                # Honor X-Tenant-ID when present so tenant_admin qualifies and
                # member_count is tenant-scoped. Platform admin may omit tenant
                # (member_count 0, scope "none"). Do not use _db_scope here —
                # it always requires a tenant.
                require_tenant = bool(_tenant_header())
                ctx = _require_context(require_tenant=require_tenant)
                _require_admin_console_access(ctx)
                with use_identity(to_runtime_identity(ctx)):
                    return _overview_payload(ctx)
            return _overview_payload(None)
        except web.HTTPError:
            raise
        except Exception as e:
            return _error(str(e), 500, "internal_error")
