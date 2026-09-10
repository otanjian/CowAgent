"""The one-time grant a completed scan hands the console.

A channel credential is a sensitive write: the console normally demands a fresh
password to prove the operator is present. A completed scan proves the same
thing by other means — it needs the operator's own phone and their vendor
account — so the server mints a grant when a scan finishes and accepts it in
place of the password exactly once.

These tests pin the boundaries that make that substitution safe: the grant is
single-use, it never crosses a user, a tenant or a channel type, and a refused
presentation does not destroy the grant its rightful holder still needs.
"""

import pytest

from auth import scan_authorization as sa


@pytest.fixture(autouse=True)
def _isolate():
    sa._reset()
    yield
    sa._reset()


def _mint(**overrides):
    payload = {"actor_user_id": "usr_a", "tenant_id": "tnt_a",
               "channel_type": "feishu"}
    payload.update(overrides)
    return sa.mint(**payload)


def _binding(**overrides):
    payload = {"actor_user_id": "usr_a", "tenant_id": "tnt_a",
               "channel_type": "feishu"}
    payload.update(overrides)
    return payload


def test_a_grant_is_accepted_for_the_binding_it_was_minted_for():
    ticket = _mint()
    assert sa.verify(ticket, **_binding()) is True


def test_a_grant_is_single_use():
    ticket = _mint()
    assert sa.consume(ticket, **_binding()) is True
    assert sa.consume(ticket, **_binding()) is False
    assert sa.verify(ticket, **_binding()) is False


def test_a_grant_does_not_cross_users():
    ticket = _mint()
    assert sa.verify(ticket, **_binding(actor_user_id="usr_b")) is False


def test_a_grant_does_not_cross_tenants():
    ticket = _mint()
    assert sa.verify(ticket, **_binding(tenant_id="tnt_b")) is False


def test_a_grant_does_not_cross_channel_types():
    # A grant minted for one vendor must not authorize creating another's
    # instance, or a scan of the cheapest type would unlock every type.
    ticket = _mint()
    assert sa.verify(ticket, **_binding(channel_type="wecom_bot")) is False


def test_a_refused_presentation_leaves_the_grant_usable():
    # A wrong binding is a probe, not a redemption: the probe must not burn the
    # grant out from under the operator who actually completed the scan.
    ticket = _mint()
    assert sa.consume(ticket, **_binding(tenant_id="tnt_b")) is False
    assert sa.consume(ticket, **_binding()) is True


def test_an_expired_grant_is_refused():
    ticket = _mint(ttl_seconds=0)
    assert sa.verify(ticket, **_binding()) is False
    assert sa.consume(ticket, **_binding()) is False


def test_an_unknown_or_empty_grant_is_refused():
    assert sa.verify("", **_binding()) is False
    assert sa.verify("not-a-ticket", **_binding()) is False
    assert sa.consume("", **_binding()) is False
    assert sa.consume("not-a-ticket", **_binding()) is False


def test_verifying_does_not_consume():
    # The create gate verifies before the row commits and consumes only after,
    # so a write refused for some other reason stays retryable.
    ticket = _mint()
    assert sa.verify(ticket, **_binding()) is True
    assert sa.verify(ticket, **_binding()) is True
    assert sa.consume(ticket, **_binding()) is True


def test_each_mint_is_distinct():
    assert _mint() != _mint()
