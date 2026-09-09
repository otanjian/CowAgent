"""Admin overview KPI aggregation."""
from types import SimpleNamespace

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


def test_build_overview_tenant_member_scope():
    ctx = SimpleNamespace(tenant_id="tnt_1")
    payload = build_admin_overview(
        ctx=ctx,
        agent_count=1,
        messages_today=0,
        member_count=3,
        system_status="ok",
        day_start=100,
    )
    assert payload["kpis"]["member_count"] == 3
    assert payload["meta"]["member_count_scope"] == "tenant"


def test_build_overview_invalid_status_coerces_degraded():
    payload = build_admin_overview(
        ctx=None,
        agent_count=0,
        messages_today=0,
        member_count=None,
        system_status="weird",
        day_start=0,
    )
    assert payload["kpis"]["system_status"] == "degraded"
