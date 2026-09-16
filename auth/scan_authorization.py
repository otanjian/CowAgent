"""One-time grants minted by a completed vendor scan.

A channel credential is a sensitive write. The console's rule is that the
operator proves they are present by re-entering their password, and
``IdentityService._require_recent_password`` enforces it. A completed scan
proves the same thing by a different route: the vendor's device-authorization
flow cannot finish without the operator's own phone and their vendor account.

So when a scan finishes the server mints a grant, and the create that follows
may present it **instead of** the password. The substitution is kept as narrow
as the proof it rests on:

* **single use** — one grant authorizes one create, so a leaked grant cannot be
  replayed. The redemption is a two-step *claim / release* pair rather than a
  check followed by a spend, so two concurrent creates that both passed the
  check cannot both commit, and a create refused after the check hands the
  grant back instead of burning it;
* **bound** — a grant is minted for one
  ``(user, tenant, login session, channel_type, scope, purpose, target)`` tuple
  and is refused for any other, so completing a scan for one vendor's channel
  does not unlock the rest, and a scan started from the *public* console cannot
  be spent on a *personal* instance (or on a different member's private Agent);
* **short lived** — a grant that was not redeemed quickly expires, because the
  console redeems it within seconds of the scan reporting success.

The **scope** and **target** halves exist because the same member legitimately
runs both flows against the same provider. The public console mints
``scope='tenant'`` with no target; the personal workbench mints
``scope='personal'`` naming the private Agent the member selected *before* the
scan started. The two are therefore not interchangeable, which is what makes
"a historical public authorization must not create a personal instance" a
property of the grant rather than of the caller remembering to check.

The **login session** half is the narrower of the two: a grant is only
redeemable by the same logged-in session that started the scan, so a handle
captured from a background tab cannot be redeemed after a re-login, and
switching accounts mid-scan costs a rescan rather than a cross-account write.

A *refused* presentation never consumes the grant. A probe with the wrong
tenant is a probe, not a redemption, and must not destroy the grant the real
operator still needs.

Storage is process-local, matching the registration session that mints it:
``FeishuRegisterHandler`` already keeps its sessions in memory, so a grant
cannot outlive them in any meaningful way. The window is seconds long, and a
service restart in that window costs the operator one rescan — the alternative,
a durable grant table, would outlive its purpose and add a sensitive artifact
to the identity database for no gain.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict

# The console redeems a grant immediately after the scan reports success. Ten
# minutes is generous enough for a slow render and short enough that a grant
# forgotten in a background tab is already dead.
DEFAULT_TTL_SECONDS = 600

#: The console surface a grant belongs to. A public scan and a personal scan are
#: two different writes even when they target the same provider and the same
#: member, so the scope is part of the binding rather than inferred from who
#: presents the ticket.
SCOPE_TENANT = "tenant"
SCOPE_PERSONAL = "personal"
SCOPES = frozenset({SCOPE_TENANT, SCOPE_PERSONAL})

#: What the grant authorizes. Only ``create`` exists today; it is an explicit
#: field so a future flow cannot be unlocked by presenting a create grant, and
#: so "a create grant never authorizes an edit" is checkable.
PURPOSE_CREATE = "create"
PURPOSES = frozenset({PURPOSE_CREATE})


# ticket -> {"actor_user_id", "tenant_id", "auth_session_id", "channel_type",
#            "scope", "purpose", "agent_id", "expires_at"}
_tickets: Dict[str, Dict[str, object]] = {}
# The subset of the above that an in-flight redemption has *reserved*: claimed
# but not yet committed, so not available to anyone else and not yet spent. A
# grant lives in exactly one of the two maps at any moment.
_claimed: Dict[str, Dict[str, object]] = {}
_lock = threading.Lock()


def mint(
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    scope: str = SCOPE_TENANT,
    purpose: str = PURPOSE_CREATE,
    agent_id: str = "",
    auth_session_id: str = "",
    ttl_seconds: int = None,
) -> str:
    """Issue a grant bound to one actor, tenant, surface and target.

    Unknown scopes/purposes are refused rather than stored: a typo must not mint
    a grant that no ``verify`` will ever match, which would look to the operator
    like a scan that silently did nothing.
    """
    scope = (scope or SCOPE_TENANT)
    purpose = (purpose or PURPOSE_CREATE)
    if scope not in SCOPES:
        raise ValueError(f"unknown scan scope: {scope}")
    if purpose not in PURPOSES:
        raise ValueError(f"unknown scan purpose: {purpose}")
    ticket = secrets.token_urlsafe(32)
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    with _lock:
        _purge_locked()
        _tickets[ticket] = {
            "actor_user_id": actor_user_id or "",
            "tenant_id": tenant_id or "",
            "auth_session_id": auth_session_id or "",
            "channel_type": channel_type or "",
            "scope": scope,
            "purpose": purpose,
            # Only meaningful when ``scope`` is personal; left empty otherwise so
            # a public grant can never be pointed at a private target by a later
            # request that happens to name one.
            "agent_id": (agent_id or "") if scope == SCOPE_PERSONAL else "",
            "expires_at": time.time() + ttl,
        }
    return ticket


def verify(
    ticket: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    scope: str = SCOPE_TENANT,
    purpose: str = PURPOSE_CREATE,
    agent_id: str = "",
    auth_session_id: str = "",
) -> bool:
    """Is this grant valid for this binding, without redeeming it?

    Split from :func:`consume` so the create path can authorize up front and
    redeem only once the row has committed: a write refused for a later reason
    (a duplicate name, say) must not leave the operator without an authorization
    they still need.

    Every argument is part of the binding, so the caller has to describe the
    write it is about to perform — a personal create that presents a public
    grant mismatches on ``scope``, and one that names a different Agent
    mismatches on ``agent_id``.

    A grant another redemption has already :func:`claim`\\ ed does not verify:
    it is no longer available, and answering "yes" here is what would let two
    concurrent creates both proceed.
    """
    with _lock:
        _purge_locked()
        record = _tickets.get(ticket or "")
        return bool(record) and _matches(
            record, actor_user_id=actor_user_id, tenant_id=tenant_id,
            channel_type=channel_type, scope=scope, purpose=purpose,
            agent_id=agent_id, auth_session_id=auth_session_id)


def claim(
    ticket: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    scope: str = SCOPE_TENANT,
    purpose: str = PURPOSE_CREATE,
    agent_id: str = "",
    auth_session_id: str = "",
) -> bool:
    """Reserve this grant for one redemption, atomically. ``False`` if taken.

    This is the half :func:`verify` cannot provide. ``verify`` answers "may this
    write proceed"; between that answer and the row being written, a second
    create presenting the same grant would get the same answer, and both would
    commit. Claiming removes the grant from circulation in the same lock
    acquisition that checks it, so exactly one caller can hold it.

    A claim is not a redemption: a write refused after this point must
    :func:`release` the grant, which is what keeps "a failed create does not
    cost the member another scan" true without making "one grant, one instance"
    false.
    """
    with _lock:
        _purge_locked()
        record = _tickets.get(ticket or "")
        if not record or not _matches(
                record, actor_user_id=actor_user_id, tenant_id=tenant_id,
                channel_type=channel_type, scope=scope, purpose=purpose,
                agent_id=agent_id, auth_session_id=auth_session_id):
            return False
        _tickets.pop(ticket, None)
        _claimed[ticket] = record
        return True


def release(ticket: str) -> None:
    """Hand a :func:`claim`\\ ed grant back, unspent, to its rightful caller.

    Called on the failure path of a write that had already taken the grant. The
    remaining lifetime is the *original* one, so releasing cannot extend a
    grant's life; a grant that expired while the write was in flight stays dead.
    """
    with _lock:
        record = _claimed.pop(ticket or "", None)
        if record is None:
            return
        if record["expires_at"] > time.time():
            _tickets[ticket] = record


def consume(
    ticket: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    scope: str = SCOPE_TENANT,
    purpose: str = PURPOSE_CREATE,
    agent_id: str = "",
    auth_session_id: str = "",
) -> bool:
    """Redeem this grant once, whether live or reserved. ``False`` otherwise."""
    with _lock:
        _purge_locked()
        for store in (_tickets, _claimed):
            record = store.get(ticket or "")
            if record and _matches(
                    record, actor_user_id=actor_user_id, tenant_id=tenant_id,
                    channel_type=channel_type, scope=scope, purpose=purpose,
                    agent_id=agent_id, auth_session_id=auth_session_id):
                store.pop(ticket, None)
                return True
        return False


def _matches(
    record: Dict[str, object],
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    scope: str = SCOPE_TENANT,
    purpose: str = PURPOSE_CREATE,
    agent_id: str = "",
    auth_session_id: str = "",
) -> bool:
    scope = scope or SCOPE_TENANT
    purpose = purpose or PURPOSE_CREATE
    if record["scope"] != scope or record["purpose"] != purpose:
        return False
    if record["actor_user_id"] != (actor_user_id or ""):
        return False
    if record["tenant_id"] != (tenant_id or ""):
        return False
    if record["channel_type"] != (channel_type or ""):
        return False
    if record["auth_session_id"] != (auth_session_id or ""):
        return False
    # The target is only compared for personal grants: a public grant stores an
    # empty one, and a caller that names an Agent while creating a *shared*
    # instance must not fail for a field that surface does not use.
    if scope == SCOPE_PERSONAL and record["agent_id"] != (agent_id or ""):
        return False
    return True


def _purge_locked() -> None:
    now = time.time()
    for store in (_tickets, _claimed):
        for key in [k for k, v in store.items() if v["expires_at"] <= now]:
            store.pop(key, None)


def _reset() -> None:
    """Drop every grant. Tests only: the process owns no durable state to clear."""
    with _lock:
        _tickets.clear()
        _claimed.clear()
