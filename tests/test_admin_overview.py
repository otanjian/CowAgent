"""Admin overview KPI aggregation.

The overview is the console's home, so since ``unify-console-by-data-scope``
task 3.5 it follows the console entry rather than the old administrator gate: a
controller sees the tenant's figures, an ordinary member sees their own. These
lock the two properties that make the difference real — that the server
withholds rather than the page hiding, and that an unreadable source is never
reported as a zero.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import web

from channel.web.admin_overview import (
    _require_overview_access,
    build_admin_overview,
    local_day_bounds,
)


def _ctx(*, tenant_id="tnt_1", platform=False, tenant_admin=False, user="usr_1"):
    return SimpleNamespace(tenant_id=tenant_id, is_platform_admin=platform,
                           is_tenant_admin=tenant_admin, user_id=user)


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
    assert payload["meta"]["scope"] == "none"
    assert payload["kpis"]["system_status"] == "ok"


def test_build_overview_tenant_member_scope():
    payload = build_admin_overview(
        ctx=_ctx(tenant_admin=True),
        agent_count=1,
        messages_today=0,
        member_count=3,
        system_status="ok",
        day_start=100,
    )
    assert payload["kpis"]["member_count"] == 3
    assert payload["meta"]["member_count_scope"] == "tenant"
    assert payload["meta"]["scope"] == "full"


def test_a_member_is_never_handed_the_tenant_member_count():
    """Withholding is a server fact; hiding it in the page would not be.

    ``not_permitted`` is deliberately a different code from ``unavailable``:
    "you may not read this" and "the read broke" are different facts about the
    same null, and collapsing them would either claim a failure that did not
    happen or imply a permission the member does not hold.
    """
    payload = build_admin_overview(
        ctx=_ctx(),
        agent_count=1,
        messages_today=0,
        member_count=None,
        system_status="ok",
        day_start=100,
    )
    assert payload["kpis"]["member_count"] is None
    assert payload["meta"]["member_count_scope"] == "not_permitted"
    assert payload["meta"]["scope"] == "self"


def test_an_unreadable_kpi_is_reported_as_missing_not_as_zero():
    """A broken source must not read as "you have nothing"."""
    payload = build_admin_overview(
        ctx=_ctx(tenant_admin=True),
        agent_count=None,
        messages_today=None,
        member_count=None,
        system_status="degraded",
        day_start=100,
        unavailable=("agent_count", "messages_today"),
    )
    assert payload["kpis"]["agent_count"] is None
    assert payload["kpis"]["messages_today"] is None
    assert payload["kpis"]["agent_count"] != 0
    assert payload["meta"]["unavailable"] == ["agent_count", "messages_today"]


def test_a_readable_kpi_says_nothing_is_unavailable():
    payload = build_admin_overview(
        ctx=_ctx(tenant_admin=True),
        agent_count=0,
        messages_today=0,
        member_count=0,
        system_status="ok",
        day_start=100,
    )
    # A real zero stays a zero: the two must remain distinguishable.
    assert payload["kpis"]["agent_count"] == 0
    assert payload["meta"]["unavailable"] == []


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


# --- access ---------------------------------------------------------------

def _refusal(ctx):
    """Run the gate and return the ``HTTPError`` it raises.

    ``web.HTTPError`` installs its headers through ``web.webapi.header`` (the
    module-global the class calls, not ``web.header``), which needs a request
    context; the gate is asserted on its own here, so the header write is stubbed
    rather than standing up a whole request for "is this refused".
    """
    with patch("web.webapi.header"):
        try:
            _require_overview_access(ctx)
        except web.HTTPError as error:
            return error
    raise AssertionError("the gate admitted a caller it should have refused")


def test_a_platform_administrator_passes():
    _require_overview_access(_ctx(platform=True))
    # A platform admin with no tenant selected is still admitted: the overview
    # answers scope "none" rather than refusing the page.
    _require_overview_access(_ctx(platform=True, tenant_id=""))


def test_a_tenant_administrator_passes():
    _require_overview_access(_ctx(tenant_admin=True))


def test_an_active_member_passes():
    """The page follows the console entry, so a member reaches it too."""
    with patch("channel.web.auth_handlers._get_service") as svc:
        svc.return_value.is_member.return_value = True
        _require_overview_access(_ctx())


def test_a_non_member_is_refused():
    """A tenant header is a selection, not a permission."""
    with patch("channel.web.auth_handlers._get_service") as svc:
        svc.return_value.is_member.return_value = False
        assert str(_refusal(_ctx())).startswith("403")


def test_a_caller_with_no_tenant_is_refused():
    assert str(_refusal(_ctx(tenant_id=""))).startswith("403")
