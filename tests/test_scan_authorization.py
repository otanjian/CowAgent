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

import time

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


# --- the personal/public split (task 4.1) ---------------------------------
#
# The same member legitimately runs both consoles against the same provider, so
# the grant has to say *which* console it belongs to. These pin that the two
# surfaces cannot spend each other's grants: a scan started from the public page
# must not create a private instance, and a private scan must not create a shared
# one, even when user, tenant, type and login session all match.

def test_a_tenant_grant_cannot_be_spent_on_a_personal_create():
    ticket = _mint(scope=sa.SCOPE_TENANT)
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_PERSONAL,
                                        agent_id="alice-own")) is False
    # The refusal is a probe, not a redemption: the public create it was minted
    # for still works.
    assert sa.consume(ticket, **_binding(scope=sa.SCOPE_TENANT)) is True


def test_a_personal_grant_cannot_be_spent_on_a_tenant_create():
    ticket = _mint(scope=sa.SCOPE_PERSONAL, agent_id="alice-own")
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_TENANT)) is False


def test_a_personal_grant_is_bound_to_its_target_agent():
    ticket = _mint(scope=sa.SCOPE_PERSONAL, agent_id="alice-own")
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_PERSONAL,
                                        agent_id="alice-own")) is True
    # Another of the member's own private Agents is still another target: a scan
    # started for one Assistant must not provision the other.
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_PERSONAL,
                                        agent_id="alice-assistant")) is False
    # Omitting the target is not a way to spend a target-bound grant.
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_PERSONAL)) is False


def test_a_tenant_grant_ignores_the_target_field_of_the_write():
    # A shared instance *may* name an Agent, and that choice belongs to the
    # public console, not to the scan: the grant records no target at all, so a
    # public create is not refused for naming one...
    ticket = _mint(scope=sa.SCOPE_TENANT)
    assert sa.verify(ticket, **_binding(scope=sa.SCOPE_TENANT,
                                        agent_id="agent-a")) is True
    # ...while a target named at mint time on the tenant surface is dropped
    # rather than stored, so it can never be read back as a binding.
    other = _mint(scope=sa.SCOPE_TENANT, agent_id="agent-a")
    assert sa.verify(other, **_binding(scope=sa.SCOPE_TENANT,
                                       agent_id="agent-b")) is True


def test_a_create_grant_never_authorizes_another_purpose():
    ticket = _mint()
    # An edit, an enable or a future flow must describe itself as something other
    # than a create, and that alone disqualifies a create grant.
    for purpose in ("edit", "update", "enable", "revoke"):
        assert sa.verify(ticket, **_binding(purpose=purpose)) is False, purpose
    # Omitting the purpose is *not* a different purpose: legacy callers name
    # nothing and the create default applies, so the field can only ever be
    # widened by explicitly asking for something else.
    assert sa.verify(ticket, **_binding(purpose="")) is True
    assert sa.consume(ticket, **_binding()) is True


def test_a_grant_does_not_cross_login_sessions():
    ticket = _mint(auth_session_id="ses_1")
    assert sa.verify(ticket, **_binding(auth_session_id="ses_1")) is True
    assert sa.verify(ticket, **_binding(auth_session_id="ses_2")) is False
    # A caller that presents no session at all cannot redeem a session-bound
    # grant: the binding is a fact about the request, not a default.
    assert sa.verify(ticket, **_binding(auth_session_id="")) is False


def test_a_session_bound_grant_survives_a_refused_probe():
    ticket = _mint(auth_session_id="ses_1",
                   scope=sa.SCOPE_PERSONAL, agent_id="alice-own")
    assert sa.consume(ticket, **_binding(auth_session_id="ses_2")) is False
    assert sa.consume(ticket, **_binding(scope=sa.SCOPE_PERSONAL)) is False
    assert sa.consume(ticket, **_binding(scope=sa.SCOPE_PERSONAL,
                                         agent_id="alice-assistant")) is False
    assert sa.consume(ticket, **_binding(auth_session_id="ses_1",
                                         scope=sa.SCOPE_PERSONAL,
                                         agent_id="alice-own")) is True


def test_an_unbound_grant_keeps_working_for_the_legacy_path():
    # The WeChat scan mints without scope, session or target; those defaults have
    # to keep matching a caller that also names nothing, or that flow breaks.
    ticket = sa.mint(actor_user_id="usr_a", tenant_id="tnt_a",
                     channel_type="weixin")
    assert sa.verify(ticket, **_binding(channel_type="weixin")) is True
    assert sa.consume(ticket, **_binding(channel_type="weixin")) is True


def test_an_unknown_scope_or_purpose_is_refused_at_mint():
    # A typo must not mint a grant no verify will ever match: that would look to
    # the operator like a scan that silently did nothing.
    with pytest.raises(ValueError):
        _mint(scope="platform")
    with pytest.raises(ValueError):
        _mint(purpose="edit")


# --- claim / release: the atomic half of single use (task 4.2) ------------
#
# ``verify`` answers "may this write proceed". It cannot answer "and is this
# grant still unspent", because the two questions are asked either side of the
# row being written: two creates that present the same grant both get "yes".
# Claiming removes the grant from circulation in the same lock acquisition that
# checks it, so exactly one of them can hold it; releasing gives it back when
# the write it authorized was refused, so "one grant, one instance" and "a
# failed create costs no second scan" hold at the same time.

def test_a_claimed_grant_is_no_longer_available():
    ticket = _mint()
    assert sa.claim(ticket, **_binding()) is True
    assert sa.verify(ticket, **_binding()) is False
    assert sa.claim(ticket, **_binding()) is False


def test_claiming_is_a_reservation_and_consuming_is_the_redemption():
    # The create path claims before it writes and redeems after committing, so a
    # claimed grant must still be redeemable — by that reservation.
    ticket = _mint()
    assert sa.claim(ticket, **_binding()) is True
    assert sa.consume(ticket, **_binding()) is True
    assert sa.verify(ticket, **_binding()) is False
    assert sa.consume(ticket, **_binding()) is False


def test_a_released_grant_is_usable_again():
    ticket = _mint()
    assert sa.claim(ticket, **_binding()) is True
    sa.release(ticket)
    assert sa.verify(ticket, **_binding()) is True
    # ...and it is still single-use, not two grants.
    assert sa.claim(ticket, **_binding()) is True
    assert sa.claim(ticket, **_binding()) is False


def test_release_does_not_resurrect_a_redeemed_grant():
    ticket = _mint()
    assert sa.claim(ticket, **_binding()) is True
    assert sa.consume(ticket, **_binding()) is True
    sa.release(ticket)
    assert sa.verify(ticket, **_binding()) is False


def test_release_of_an_unclaimed_grant_is_a_no_op():
    ticket = _mint()
    sa.release(ticket)
    assert sa.verify(ticket, **_binding()) is True
    sa.release("not-a-ticket")


def test_a_foreign_claim_is_refused_and_leaves_the_grant_alone():
    # The claim names the write it is about to perform, exactly like ``verify``:
    # a probe with another tenant or session must not take the grant.
    ticket = _mint(auth_session_id="ses_1")
    assert sa.claim(ticket, **_binding(tenant_id="tnt_b")) is False
    assert sa.claim(ticket, **_binding(auth_session_id="ses_2")) is False
    assert sa.verify(ticket, **_binding(auth_session_id="ses_1")) is True
    assert sa.claim(ticket, **_binding(auth_session_id="ses_1")) is True


def test_an_unknown_or_expired_grant_cannot_be_claimed():
    assert sa.claim("", **_binding()) is False
    assert sa.claim("not-a-ticket", **_binding()) is False
    assert sa.claim(_mint(ttl_seconds=0), **_binding()) is False


def test_release_cannot_extend_a_grants_life():
    # A write slow enough to outlive its own grant releases a dead grant: the
    # reservation kept the original expiry rather than restarting the clock.
    ticket = _mint(ttl_seconds=1)
    assert sa.claim(ticket, **_binding()) is True
    time.sleep(1.1)
    sa.release(ticket)
    assert sa.verify(ticket, **_binding()) is False
    assert sa.claim(ticket, **_binding()) is False
