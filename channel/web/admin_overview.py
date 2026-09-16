# encoding:utf-8
"""Admin console overview KPIs (GET /api/admin/overview)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import web

logger = logging.getLogger(__name__)

from channel.web.auth_handlers import (
    _error,
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


def _overview_scope(ctx) -> str:
    """What this caller may be shown on the overview: ``full`` or ``self``.

    ``full`` — a controller of the selected tenant (platform admin, or its
    tenant administrator). ``self`` — any other active member: they reach the
    *same* page and the server answers with their own figures.

    The distinction is made here, before any aggregation, and not in the
    console. If the server returned the tenant's totals and the page merely hid
    them, the numbers would have travelled to a browser that is not entitled to
    them — reachable by anyone with the dev tools open. Withholding is a server
    fact, hiding is not (spec ``unified-console-access``: 不能先向成员返回全租户
    统计再在界面隐藏).
    """
    if ctx is None or not getattr(ctx, "tenant_id", None):
        return "none"
    if (getattr(ctx, "is_platform_admin", False)
            or getattr(ctx, "is_tenant_admin", False)):
        return "full"
    return "self"


def build_admin_overview(
    *,
    ctx,
    agent_count: Optional[int],
    messages_today: Optional[int],
    member_count: Optional[int],
    system_status: str,
    day_start: int,
    timezone_label: str = "local",
    scope: Optional[str] = None,
    unavailable: Optional[Any] = None,
) -> Dict[str, Any]:
    """Assemble the overview payload for one caller.

    ``agent_count`` and ``messages_today`` are ``Optional`` on purpose: a source
    that could not be read is reported as ``None`` **plus** an entry in
    ``meta.unavailable``, never as ``0``. Zero is a real answer ("you have no
    Agents yet") and a failure is not that answer — conflating them tells the
    operator their tenant is empty when in fact the read broke, which is the
    failure mode this task exists to remove (规范：数据不可读时 SHALL 报告真实
    失败，不用零值冒充成功).

    ``member_count`` is withheld rather than nulled for a ``self`` caller: a
    member has no member statistic, and ``member_count_scope`` says which of the
    two facts ``None`` means — ``"not_permitted"`` (not yours to read) or
    ``"unavailable"`` (yours to read, and the read failed).
    """
    resolved = scope if scope is not None else _overview_scope(ctx)
    if resolved == "none":
        member_count, member_scope = None, "unavailable"
    elif resolved == "full":
        member_scope = "unavailable" if member_count is None else "tenant"
    else:
        # Not merely hidden: the aggregation is not consulted for a member at
        # all, so the count never leaves the database.
        member_count, member_scope = None, "not_permitted"
    return {
        "status": "ok",
        "kpis": {
            "agent_count": None if agent_count is None else int(agent_count),
            "messages_today": (
                None if messages_today is None else int(messages_today)),
            "member_count": member_count,
            "system_status": (
                system_status if system_status in ("ok", "degraded") else "degraded"
            ),
        },
        "meta": {
            "day_start": int(day_start),
            "timezone": timezone_label,
            "member_count_scope": member_scope,
            "scope": resolved,
            # Names, not prose: the console localizes the region and the operator
            # can tell which number is missing instead of distrusting all four.
            "unavailable": sorted(str(name) for name in (unavailable or ())),
        },
    }


def _require_overview_access(ctx) -> None:
    """Who may open the overview, and with what content.

    The overview is the console's home, so it follows the console entry rather
    than the old administrator gate: a controller of the selected tenant, or an
    active member of it. A member is *not* refused — refusing them would close a
    page they can legitimately reach with their own figures — but they are never
    handed the tenant's.

    The membership half is asked of the identity store rather than inferred from
    the tenant header: a header is a selection, not a permission. A caller who
    names a tenant they do not belong to is refused, and ``_tenant_header``
    alone must never be what admits them.
    """
    if getattr(ctx, "is_platform_admin", False):
        return
    tenant_id = str(getattr(ctx, "tenant_id", "") or "")
    if getattr(ctx, "is_tenant_admin", False) and tenant_id:
        return
    if tenant_id:
        from channel.web.auth_handlers import _get_service

        if _get_service().is_member(ctx.user_id, tenant_id):
            return
    raise web.HTTPError(
        "403 Forbidden",
        {"Content-Type": "application/json"},
        _error("forbidden", 403, "forbidden"),
    )


def _gather_agent_count(ctx) -> int:
    """How many Agents this caller may manage.

    The *management* range, not the use range: a member's own private Agents, a
    controller's tenant-shared ones plus their own. Counting shared Agents for a
    member would report objects that are not theirs, and counting the tenant's
    roster for anyone would report another member's private Agents by their
    number alone — a count is still a disclosure (spec: 不能通过统计推断他人私有
    记录).
    """
    from channel.web.web_channel import _agent_admin_service, _iter_tenant_agents
    from auth.object_scope import MANAGE as SCOPE_MANAGE

    if ctx is not None:
        return sum(1 for _ in _iter_tenant_agents(ctx, action=SCOPE_MANAGE))
    snap = _agent_admin_service().snapshot()
    agents = snap.get("agents") or []
    return len(agents)


def _gather_messages_today(ctx, start_ts: int, end_ts: int, *, scope: str) -> int:
    """Messages today over the Agents whose conversations this caller may count.

    A controller counts the tenant's bound Agents. Anyone else counts only the
    Agents in **their own** management range — for a member that is their own
    private Agents. This is not a display choice: a shared Agent's conversation
    store holds every member's messages, so totalling it for a member would hand
    them the tenant's message volume, which the requirement forbids
    (「不收到其他用户消息量」).

    The store has no per-user dimension yet (``runs.user_id`` is reserved and
    empty), so "messages I may count" is expressed as "Agents that are mine".
    A narrower answer is the honest one while the finer fact does not exist.
    """
    from agent.memory import get_conversation_store
    from channel.web.web_channel import _iter_tenant_agents
    from auth.object_scope import MANAGE as SCOPE_MANAGE

    if ctx is not None and scope == "self":
        candidates = [profile for profile, *_rest in
                      _iter_tenant_agents(ctx, action=SCOPE_MANAGE)]
    else:
        from agent.registry import get_agent_registry
        from channel.web.web_channel import _tenant_ids_for_context

        visible = _tenant_ids_for_context(ctx) if ctx is not None else None
        candidates = [profile for profile in
                      get_agent_registry().list(include_disabled=False)
                      if visible is None or profile.id in visible]

    # No per-workspace ``except``: a partial total presented as the whole is a
    # wrong number, and the requirement forbids exactly that ("不用零值冒充成功"
    # — a silently short count is the same lie in a different shape). One
    # unreadable workspace therefore makes the figure *unavailable*, which
    # ``_overview_payload`` reports per region, rather than quietly small.
    total = 0
    for profile in candidates:
        store = get_conversation_store(profile.workspace)
        total += store.count_messages_between(start_ts, end_ts)
    return total


def _gather_member_count(ctx) -> Optional[int]:
    """The tenant's active member count, or ``None`` when it may not be read.

    Only a controller is asked for this at all. A member is not given the number
    and then trusted not to look — the aggregation is skipped, so there is
    nothing to withhold downstream.
    """
    if ctx is None or not ctx.tenant_id:
        return None
    from channel.web.auth_handlers import _get_service

    svc = _get_service()
    result = svc.list_members(ctx.tenant_id, status="active", page=1, page_size=1)
    return int(result.get("total") or 0)


def _overview_payload(ctx) -> str:
    day_start, day_end = local_day_bounds()
    scope = _overview_scope(ctx)
    unavailable: list = []

    def _read(name, gather, fallback=None):
        """One KPI, read independently so one failure cannot zero the others."""
        try:
            return gather()
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            logger.warning("[Overview] %s could not be read: %s",
                           name, type(error).__name__)
            unavailable.append(name)
            return fallback

    agent_count = _read("agent_count", lambda: _gather_agent_count(ctx))
    messages_today = _read(
        "messages_today",
        lambda: _gather_messages_today(ctx, day_start, day_end, scope=scope))
    member_count = None
    if scope == "full":
        member_count = _read("member_count", lambda: _gather_member_count(ctx))
    payload = build_admin_overview(
        ctx=ctx,
        agent_count=agent_count,
        messages_today=messages_today,
        member_count=member_count,
        # Deployment-wide health, and deliberately still reported to a member:
        # it carries no tenant, member, Agent or channel identity, and the card
        # is the console's own liveness ("can I trust what I am looking at").
        # The "公共运行详情" the requirement withholds is per-channel runtime
        # state, which this payload never carried.
        system_status="degraded" if unavailable else "ok",
        day_start=day_start,
        scope=scope,
        unavailable=unavailable,
    )
    return _json(payload)


class AdminOverviewHandler:
    def GET(self):
        from auth.runtime import to_runtime_identity
        from common.runtime_identity import use_identity

        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        try:
            # Honor X-Tenant-ID when present so a member (and tenant_admin)
            # qualifies and every figure is tenant-scoped. Platform admin may
            # omit tenant (member_count absent, scope "none"). Do not use
            # _db_scope here — it always requires a tenant.
            require_tenant = bool(_tenant_header())
            ctx = _require_context(require_tenant=require_tenant)
            _require_overview_access(ctx)
            with use_identity(to_runtime_identity(ctx)):
                return _overview_payload(ctx)
        except web.HTTPError:
            raise
        except Exception as e:
            return _error(str(e), 500, "internal_error")
