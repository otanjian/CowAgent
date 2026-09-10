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
  replayed;
* **bound** — a grant is minted for one ``(user, tenant, channel_type)`` triple
  and is refused for any other, so completing a scan for one vendor's channel
  does not unlock the rest;
* **short lived** — a grant that was not redeemed quickly expires, because the
  console redeems it within seconds of the scan reporting success.

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

# ticket -> {"actor_user_id", "tenant_id", "channel_type", "expires_at"}
_tickets: Dict[str, Dict[str, object]] = {}
_lock = threading.Lock()


def mint(
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
    ttl_seconds: int = None,
) -> str:
    """Issue a grant bound to one actor, tenant and channel type."""
    ticket = secrets.token_urlsafe(32)
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
    with _lock:
        _purge_locked()
        _tickets[ticket] = {
            "actor_user_id": actor_user_id or "",
            "tenant_id": tenant_id or "",
            "channel_type": channel_type or "",
            "expires_at": time.time() + ttl,
        }
    return ticket


def verify(
    ticket: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
) -> bool:
    """Is this grant valid for this binding, without redeeming it?

    Split from :func:`consume` so the create path can authorize up front and
    redeem only once the row has committed: a write refused for a later reason
    (a duplicate name, say) must not leave the operator without an authorization
    they still need.
    """
    with _lock:
        _purge_locked()
        record = _tickets.get(ticket or "")
        return bool(record) and _matches(
            record, actor_user_id=actor_user_id, tenant_id=tenant_id,
            channel_type=channel_type)


def consume(
    ticket: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
) -> bool:
    """Redeem this grant once. ``False`` for an unknown, expired or foreign one."""
    with _lock:
        _purge_locked()
        record = _tickets.get(ticket or "")
        if not record or not _matches(
                record, actor_user_id=actor_user_id, tenant_id=tenant_id,
                channel_type=channel_type):
            return False
        _tickets.pop(ticket, None)
        return True


def _matches(
    record: Dict[str, object],
    *,
    actor_user_id: str,
    tenant_id: str,
    channel_type: str,
) -> bool:
    return (record["actor_user_id"] == (actor_user_id or "")
            and record["tenant_id"] == (tenant_id or "")
            and record["channel_type"] == (channel_type or ""))


def _purge_locked() -> None:
    now = time.time()
    for key in [k for k, v in _tickets.items() if v["expires_at"] <= now]:
        _tickets.pop(key, None)


def _reset() -> None:
    """Drop every grant. Tests only: the process owns no durable state to clear."""
    with _lock:
        _tickets.clear()
