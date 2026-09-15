# encoding:utf-8
"""Per-initiator scan onboarding: QR session state, receipts and atomic commit.

Three failure modes are being removed from the WeChat scan flow, and this module
exists for exactly those three. Nothing else lives here.

**1. One global QR slot.** ``WeixinQrHandler._qr_state`` was a single
process-global dict. Two operators scanning at the same time overwrote each
other: the second scan's ``qrcode`` replaced the first's, so the first poller
then reported the *second* operator's login as its own, and whichever operator
finished last won ``conf()['weixin_token']``. A global slot also cannot answer
"is this handle yours?", so any console user could poll any scan. The session
registry below binds every scan to a verified ``(user, tenant, auth session)``
triple plus the provider/scope/purpose/target it was started for, and every read
is answered within that binding. A handle that belongs to somebody else is
refused with the *same* code and the same message as a handle that does not
exist, so a probe cannot even establish that a scan is in flight.

**2. A retried submit creating a second instance.** A scan-driven create is a
sensitive write that the operator cannot repeat cheaply (repeating it means
another scan with their phone). When the submit response is lost — a reload, a
dropped connection, a worker that died between the row commit and the reply —
the console submits the same request again. Without an idempotency ledger that
second submit either creates a duplicate channel instance or is refused as a
replayed single-use grant, leaving the operator with an instance they cannot
find and a grant they cannot use. Receipts fix both: the same binding + the same
normalized content inside the TTL reads back the *original* non-sensitive
result, while a different key or different content is refused as a replay of an
already-redeemed authorization.

**3. A refused write burning the authorization.** ``scan_authorization``
separates :func:`verify` from :func:`consume` precisely so that a write refused
for a later reason (duplicate name, quota, app conflict, audit failure) does not
cost the operator another scan. The commit path below keeps that ordering and
adds the one thing the old inline handler could not express: a *staged journal*
recorded the instant the instance row lands, so a failure **after** the write
(audit, receipt store) is resumable by the same key instead of duplicating the
instance on retry.

Why the ordering is not simply ``consume``-then-write: consuming first turns any
later refusal into "you must scan again", which is exactly the outcome the
one-time grant was designed to avoid. The grant is therefore verified up front
(so the write never starts unauthorized) and redeemed last (so a refusal leaves
it usable). The window between verify and consume is covered by the session
state machine: the QR session moves to ``committing`` before the write, and the
same key cannot start a second write while the first one's receipt is live.

**4. The vendor login result sitting in the clear.** A completed scan hands the
server a long-term ``bot_token``. It has to be held for the seconds between
"confirmed" and "the operator pressed submit", and it must not be readable from
a process dump or a log line while it is. :func:`attach_provider_result` stores
it **encrypted** (``auth.crypto``, the same keyed AES-GCM the credential store
uses) inside the session record, is refused outright when no key is available
(fail closed — never "store it anyway"), and the ciphertext is dropped the
moment the session reaches a terminal state. :func:`provider_result` is the only
reader; nothing else in this module, and nothing in its return values, ever
touches the plaintext.

Two honest limits, stated rather than papered over:

* the registry and the ledger are **process-local**, matching
  ``auth.scan_authorization``'s grants, which the minters already keep in memory.
  A multi-worker deployment needs a shared store behind :func:`store_receipt`'s
  sink seam. This module cannot enforce a deployment topology, so
  :func:`shared_state_ready` is the seam the wiring must consult before serving
  any scan: it reuses the existing deployment gate that already refuses to build
  the console multi-worker (``auth.ratelimit.reject_multi_worker_identity``) and
  answers ``(False, reason)`` when that gate is not satisfied, instead of letting
  each worker keep its own view of one scan.
* ``auth/scan_authorization``'s grants live for 600s while a receipt lives 24h,
  so a replay may arrive long after the QR session itself is gone. The read-back
  path is therefore resolvable from the receipt alone (indexed by the presented
  authorization handle), and the exact-content requirement is re-proved by
  recomputing the receipt key over the caller's request; the QR session is used
  when it is still alive, never required to still be alive.

No long-term credential ever passes through this module's return values, its
ledger or its log lines. A WeChat ``bot_token`` is written by the identity
service into the encrypted instance bundle; what comes back out is
``secret_present: True``. That rule is scoped to this new flow: the Feishu
register handler keeps handing ``app_id``/``app_secret``/``scan_ticket`` to the
verified initiator exactly as before, and nothing here assumes Feishu changed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from common.log import logger

__all__ = [
    "Actor",
    "Readback",
    "Session",
    "ScanOnboardingError",
    "ScanSessionError",
    "ScanReceiptError",
    "ScanCommitError",
    "SESSION_TTL_SECONDS",
    "RECEIPT_TTL_SECONDS",
    "SESSION_SCOPES",
    "SESSION_STATUSES",
    "TERMINAL_STATUSES",
    "NOT_OWNED_MESSAGE",
    "start_session",
    "attach_qr",
    "attach_provider_result",
    "provider_result",
    "open_grant",
    "mark_status",
    "latest_session",
    "get_session",
    "require_session",
    "purge_expired",
    "sweep",
    "shared_state_ready",
    "normalize_payload",
    "payload_digest",
    "receipt_key",
    "lookup_receipt",
    "store_receipt",
    "purge_receipts",
    "commit_scan_binding",
    "_reset",
]

# ---------------------------------------------------------------------------
# Lifetimes and vocabulary
# ---------------------------------------------------------------------------

#: How long one QR registration session stays readable. The vendor QR itself is
#: valid for ~600s; a session outliving it is what lets a late poll observe the
#: terminal state instead of reading "unknown" and starting a second scan.
SESSION_TTL_SECONDS: float = 900.0

#: How long a successful receipt can be read back. Deliberately much longer than
#: the session: the receipt is the answer to "my submit response was lost", and
#: that answer must survive a browser reload, a redeploy or a new auth session
#: that still belongs to the same initiator.
RECEIPT_TTL_SECONDS: float = 86400.0

SCOPE_PLATFORM = "platform"
SCOPE_TENANT = "tenant"
SCOPE_PERSONAL = "personal"
#: Scopes an onboarding session may be started for. Chosen by the *wiring* from
#: the verified request context, never from a client parameter.
SESSION_SCOPES = (SCOPE_PLATFORM, SCOPE_TENANT, SCOPE_PERSONAL)
#: The identity service's own name for a member-owned instance (see
#: ``IdentityService._resolve_instance_scope``). Only this translation is done
#: here; every other ownership decision stays in the service.
SERVICE_SCOPE_USER = "user"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_COMMITTING = "committing"
STATUS_COMMITTED = "committed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"

SESSION_STATUSES = frozenset({
    STATUS_PENDING, STATUS_CONFIRMED, STATUS_COMMITTING, STATUS_COMMITTED,
    STATUS_CANCELLED, STATUS_EXPIRED, STATUS_FAILED,
})

#: States a session never leaves. Entering one scrubs the temporary QR material:
#: a terminal session keeps only its status, so a late poll can learn the
#: outcome while nothing claimable is left behind.
TERMINAL_STATUSES = frozenset({
    STATUS_COMMITTED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_FAILED,
})

#: The only transitions the public API performs. Same-status is a no-op (a poll
#: and a retry both re-assert ``confirmed``), which keeps callers from having to
#: track the exact previous value. ``committing`` is reachable from ``pending``
#: as well as ``confirmed``: a submit can beat the poll that would have marked
#: the scan confirmed, and the authorization it presents is the proof that the
#: scan really finished. ``committing -> confirmed`` is the commit path's
#: rollback for a refusal that happened *before* the instance row landed
#: (authorization, quota, write): the operator still has a usable scan and must
#: be able to submit again.
_ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    STATUS_PENDING: frozenset({
        STATUS_PENDING, STATUS_CONFIRMED, STATUS_COMMITTING, STATUS_CANCELLED,
        STATUS_EXPIRED, STATUS_FAILED,
    }),
    STATUS_CONFIRMED: frozenset({
        STATUS_CONFIRMED, STATUS_COMMITTING, STATUS_CANCELLED, STATUS_EXPIRED,
        STATUS_FAILED,
    }),
    STATUS_COMMITTING: frozenset({
        STATUS_COMMITTING, STATUS_CONFIRMED, STATUS_COMMITTED, STATUS_CANCELLED,
        STATUS_FAILED,
    }),
}

#: Key names that name a secret *shape*. A result carrying one of these is
#: refused loudly rather than stored: silently dropping it would hide a caller
#: that is about to persist a vendor token in the ledger.
_SECRET_KEY_HINTS = (
    "secret", "token", "password", "passwd", "credential", "ciphertext",
    "aes_key", "private_key", "signing_key", "api_key", "app_key", "bot_key",
)

#: The non-sensitive result contract of a receipt. Nothing outside this tuple is
#: ever returned by :func:`lookup_receipt`, which is what makes "no token, no
#: secret" a property of the ledger rather than of every caller's discipline.
#: ``secret_present`` is the one hint-matching name that is *declared* here: it is
#: a boolean marker that a credential was stored, never the credential.
_RECEIPT_FIELDS = (
    "receipt_key",
    "instance_id",
    "channel_type",
    "display_name",
    "agent_id",
    "scope",
    "provider",
    "purpose",
    "target",
    "tenant_id",
    "owner_user_id",
    "saved",
    "connected",
    "secret_present",
    "committed_at",
)

#: Guard rail for :func:`normalize_payload`: a request-shaped mapping, not a
#: document tree. Beyond this the digest would be computed over something the
#: caller probably did not intend.
_MAX_PAYLOAD_DEPTH = 8

#: Above this many live sessions a new one triggers a sweep of expired entries
#: (and, if that frees nothing, of the oldest terminal ones). A cap keeps a
#: forgotten console tab from growing the registry without bound.
_MAX_SESSIONS = 1024

# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class ScanOnboardingError(RuntimeError):
    """Base refusal carrying a machine-readable ``code``.

    ``message`` is what the console may show and therefore never contains a
    handle, an owner, a tenant, progress or credential material — the whole
    point of the refusal is to answer the question and nothing else.
    """

    default_code = "scan_refused"

    def __init__(self, message: str = "", *, code: Optional[str] = None,
                 context: Optional[Mapping[str, Any]] = None) -> None:
        self.code = code or self.default_code
        #: Non-sensitive correlation only (a channel type, a scope). Never put
        #: credential material or another principal's identifiers here.
        self.context: Dict[str, Any] = dict(context or {})
        super().__init__(message or self.code)

    def as_payload(self) -> Dict[str, Any]:
        """The console-shaped refusal, for a handler that wants one line."""
        return {"status": "error", "code": self.code, "message": str(self)}


class ScanSessionError(ScanOnboardingError):
    """The QR session itself refuses: unknown, expired, foreign or mis-bound."""

    default_code = "not_owner"


class ScanReceiptError(ScanOnboardingError):
    """The idempotency ledger refuses: bad payload, or a would-be secret."""

    default_code = "bad_payload"


class ScanCommitError(ScanOnboardingError):
    """The commit path refuses: authorization, quota, audit, read-back."""

    default_code = "commit_refused"


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """The verified initiator of a scan, as resolved by the request layer.

    Both mutating and reading entry points take one of these instead of loose
    ids so a caller cannot accidentally pass a *client-supplied* tenant or
    session: the wiring builds it from the verified request context, and every
    ownership answer is computed against it.
    """

    user_id: str
    tenant_id: str = ""
    auth_session_id: str = ""

    @classmethod
    def coerce(cls, value: Any) -> "Actor":
        """Accept an ``Actor``, a mapping, or an object with the same fields.

        Raises for an actor with no user id: an unauthenticated probe must be
        refused, and refusing it here means no entry point has to remember to.
        """
        if isinstance(value, Actor):
            actor = value
        elif isinstance(value, Mapping):
            actor = cls(
                user_id=str(value.get("user_id") or ""),
                tenant_id=str(value.get("tenant_id") or ""),
                auth_session_id=str(
                    value.get("auth_session_id") or value.get("session_id") or ""),
            )
        elif value is not None:
            actor = cls(
                user_id=str(getattr(value, "user_id", "") or ""),
                tenant_id=str(getattr(value, "tenant_id", "") or ""),
                auth_session_id=str(
                    getattr(value, "auth_session_id", "")
                    or getattr(value, "session_id", "") or ""),
            )
        else:
            actor = cls(user_id="")
        if not actor.user_id:
            raise ScanOnboardingError(
                "an authenticated initiator is required", code="unauthenticated")
        return actor


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """An immutable snapshot of one onboarding session.

    Snapshots rather than live records: every mutation goes through the module's
    lock, so a caller holding a ``Session`` can never change registry state by
    accident, and two readers can never observe a half-applied update.
    """

    handle: str
    provider: str
    scope: str
    purpose: str
    target: str
    owner_user_id: str
    tenant_id: str
    auth_session_id: str
    status: str = STATUS_PENDING
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0
    version: int = 1
    qrcode: str = ""
    qrcode_url: str = ""
    base_url: str = ""
    error: str = ""
    #: Whether an encrypted vendor login result is held for this session. The
    #: ciphertext itself never leaves the registry: this boolean is the only
    #: thing a caller may learn about it, and it exists so the commit path can
    #: answer "there is nothing to bind yet" without decrypting anything.
    provider_result_present: bool = False
    #: Whether this session already opened its one-time create authorization.
    #: The ticket itself never leaves the registry either — it is a bearer
    #: capability for exactly one create, and nothing outside the server has any
    #: use for it. The boolean is what lets a wiring report "允许创建" separately
    #: from "已保存" and "已连接" (D9).
    grant_opened: bool = False
    #: The name the commit uses when the request names none: the wiring resolves
    #: it once, at scan start, so a retried submit presents the same content (and
    #: therefore the same idempotency key) as the attempt it is retrying.
    default_display_name: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        return self.expires_at <= _now(now)

    def owns(self, actor: Any) -> bool:
        """Whether *actor* is the initiator this session was bound to."""
        try:
            candidate = Actor.coerce(actor)
        except ScanOnboardingError:
            return False
        return (candidate.user_id == self.owner_user_id
                and candidate.tenant_id == self.tenant_id
                and candidate.auth_session_id == self.auth_session_id)

    def matches(self, *, provider: Optional[str] = None,
                scope: Optional[str] = None, purpose: Optional[str] = None,
                target: Optional[str] = None) -> bool:
        """Whether the given (non-``None``) dimensions agree with the session."""
        wanted = {"provider": provider, "scope": scope, "purpose": purpose,
                  "target": target}
        for name, value in wanted.items():
            if value is None:
                continue
            if str(value).strip() != getattr(self, name):
                return False
        return True

    def payload(self) -> Dict[str, Any]:
        """The client-facing projection.

        ``qrcode`` (the vendor polling token) is deliberately absent: only the
        server polls the vendor, and the rendered image is derived from
        ``qrcode_url``. Owner, tenant and auth-session ids are absent too — the
        initiator already knows them, and nobody else may.
        """
        return {
            "handle": self.handle,
            "provider": self.provider,
            "scope": self.scope,
            "purpose": self.purpose,
            "target": self.target,
            "status": self.status,
            "terminal": self.terminal,
            "expires_at": self.expires_at,
            "qrcode_url": self.qrcode_url,
            "base_url": self.base_url,
            "error": self.error,
            "version": self.version,
            # Non-sensitive and pre-fillable: the console shows the name the
            # instance will get so the operator can change it before submitting.
            "default_display_name": self.default_display_name,
        }


def _now(now: Optional[float] = None) -> float:
    return time.time() if now is None else float(now)


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ---------------------------------------------------------------------------
# Registry
#
# One lock guards every read and every mutation. The poll path is hit by several
# console tabs at once, so "check ownership, then decide" must be one atomic
# step; a read-modify-write split across two lock acquisitions would let a
# session be purged between them and answered as if it still existed.
# ---------------------------------------------------------------------------

_sessions: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _view(record: Mapping[str, Any]) -> Session:
    return Session(
        handle=record["handle"],
        provider=record["provider"],
        scope=record["scope"],
        purpose=record["purpose"],
        target=record["target"],
        owner_user_id=record["owner_user_id"],
        tenant_id=record["tenant_id"],
        auth_session_id=record["auth_session_id"],
        status=record["status"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        expires_at=record["expires_at"],
        version=record["version"],
        qrcode=record.get("qrcode", ""),
        qrcode_url=record.get("qrcode_url", ""),
        base_url=record.get("base_url", ""),
        error=record.get("error", ""),
        provider_result_present=bool(record.get("provider_result_ct")),
        grant_opened=bool(record.get("grant_ticket")),
        default_display_name=record.get("default_display_name", ""),
    )


def _scrub_qr(record: Dict[str, Any]) -> None:
    """Drop the temporary QR material from *record*.

    Called whenever the session leaves ``pending``. The vendor QR is a transient
    secret-ish artifact — it addresses a live login under the operator's account
    — so once it is scanned there is no reason to keep it addressable, and a
    refused commit is retried with the same authorization rather than a new scan.
    """
    record["qrcode"] = ""
    record["qrcode_url"] = ""
    record["base_url"] = ""


def _scrub_provider_result(record: Dict[str, Any]) -> None:
    """Drop the encrypted vendor login result from *record*.

    Called on the terminal transitions that have nothing left to bind —
    ``cancelled``, ``expired`` and ``failed``. A live ``confirmed`` session keeps
    it because that ciphertext is exactly what the commit path decrypts to write
    the credential bundle, and a ``committed`` session keeps it because that is
    the session a lost-response retry returns to (see
    :func:`_mark_status`). Nothing else reads it, and the session's own TTL
    bounds how long either copy can exist.
    """
    record["provider_result_ct"] = ""


def _purge_expired_locked(now_ts: float) -> int:
    expired = [handle for handle, record in _sessions.items()
               if record["expires_at"] <= now_ts]
    for handle in expired:
        _sessions.pop(handle, None)
    return len(expired)


def _drop_oldest_terminal_locked(count: int) -> int:
    """Retire the *count* oldest terminal sessions.

    Terminal sessions are the only ones discarded here: a live session belongs to
    an operator mid-scan, and losing it would cost them a rescan for no reason.
    """
    candidates = sorted(
        ((record["updated_at"], handle) for handle, record in _sessions.items()
         if record["status"] in TERMINAL_STATUSES),
        key=lambda item: item[0],
    )[:max(0, count)]
    for _updated, handle in candidates:
        _sessions.pop(handle, None)
    return len(candidates)


def _owned_record_locked(handle: str, actor: Actor, now_ts: float,
                         what: str = "scan session") -> Dict[str, Any]:
    """The live record for *handle* owned by *actor*, or a refusal.

    The refusal for "no such handle" is byte-for-byte the refusal for "somebody
    else's handle": same code, same message, no context. That is what makes the
    existence of another initiator's scan unobservable.
    """
    record = _sessions.get(handle)
    if record is None:
        raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
    if not _record_owned_by(record, actor):
        raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
    if record["expires_at"] <= now_ts:
        _sessions.pop(handle, None)
        raise ScanSessionError(f"{what} has expired", code="expired")
    return record


#: The single wording used for every "not yours" answer, including "no such
#: handle". Kept as a constant so the two can never drift apart.
_NOT_OWNED_MESSAGE = "scan session is not available to this actor"

#: Public alias, for a wiring that has to answer the *same* thing in a case the
#: module cannot raise for itself — most often "the caller has no session at all"
#: on a handle-less poll. A second copy of the text there would be a second
#: thing to keep in sync, and a drift would turn one of the two answers into an
#: existence oracle.
NOT_OWNED_MESSAGE = _NOT_OWNED_MESSAGE


def _record_owned_by(record: Mapping[str, Any], actor: Actor) -> bool:
    return (record["owner_user_id"] == actor.user_id
            and record["tenant_id"] == actor.tenant_id
            and record["auth_session_id"] == actor.auth_session_id)


def start_session(
    *,
    provider: str,
    scope: str,
    purpose: str = "bind",
    target: str,
    owner_user_id: str,
    tenant_id: str,
    auth_session_id: str,
    default_display_name: str = "",
    ttl_seconds: Optional[float] = None,
    now: Optional[float] = None,
) -> Session:
    """Open a session and return it, ``handle`` included.

    Every argument except ``ttl_seconds`` is server-bound: the wiring passes the
    verified initiator's ids and the *purpose it is asking for*, never a value a
    client sent. ``default_display_name`` is the one field a client's own words
    may reach (the console can pre-fill a name), and it is resolved by the
    wiring, at start, so that a retry presents the same content. Two initiators
    scanning at once get two handles and two
    independent records, so neither can overwrite, read or consume the other's.

    The same initiator calling twice gets two sessions on purpose: two console
    tabs are two scans, and silently replacing the first (the Feishu handler's
    rule, where one SDK thread polls one registration) would make the first tab
    poll a session that no longer exists.
    """
    owner = _clean(owner_user_id)
    if not owner:
        raise ScanOnboardingError(
            "an authenticated initiator is required", code="unauthenticated")
    provider = _clean(provider)
    scope = _clean(scope).lower()
    purpose = _clean(purpose)
    target = _clean(target)
    tenant = _clean(tenant_id)
    auth_session = _clean(auth_session_id)

    if not provider:
        raise ScanSessionError("provider is required", code="bad_binding")
    if scope not in SESSION_SCOPES:
        raise ScanSessionError(
            f"unknown scan scope: {scope!r}", code="bad_binding",
            context={"allowed": list(SESSION_SCOPES)})
    if not purpose:
        raise ScanSessionError("purpose is required", code="bad_binding")
    if not target:
        raise ScanSessionError("target is required", code="bad_binding")
    if not auth_session:
        # Without it the receipt key could not distinguish two logins of the
        # same user, and a session that cannot be told apart from a later one
        # cannot refuse a cross-session replay.
        raise ScanSessionError(
            "a verified auth session is required", code="bad_binding")
    if scope in (SCOPE_TENANT, SCOPE_PERSONAL) and not tenant:
        raise ScanSessionError(
            "a tenant is required for this scope", code="bad_binding")

    handle = secrets.token_urlsafe(32)
    ttl = SESSION_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
    now_ts = _now(now)
    record = {
        "handle": handle,
        "provider": provider,
        "scope": scope,
        "purpose": purpose,
        "target": target,
        "owner_user_id": owner,
        "tenant_id": tenant,
        "auth_session_id": auth_session,
        # The name a commit will use when the request carries none (a scan has
        # no name field). Bound to the session so every attempt of one scan
        # presents the same content and therefore the same idempotency key.
        "default_display_name": _clean(default_display_name),
        "status": STATUS_PENDING,
        "created_at": now_ts,
        "updated_at": now_ts,
        "expires_at": now_ts + ttl,
        "version": 1,
        "qrcode": "",
        "qrcode_url": "",
        "base_url": "",
        "provider_result_ct": "",
        "error": "",
    }
    with _lock:
        if len(_sessions) >= _MAX_SESSIONS:
            _purge_expired_locked(now_ts)
            _drop_oldest_terminal_locked(len(_sessions) - _MAX_SESSIONS + 1)
        _sessions[handle] = record
    logger.info(
        f"[ScanOnboarding] started {provider} scan session for {scope} scope"
        f" (purpose={purpose})"
    )
    return _view(record)


def require_session(
    handle: str,
    *,
    actor: Any,
    provider: Optional[str] = None,
    scope: Optional[str] = None,
    purpose: Optional[str] = None,
    target: Optional[str] = None,
    now: Optional[float] = None,
) -> Session:
    """The session for *handle* if *actor* owns it and the dimensions agree.

    Raises instead of returning ``None``, which is what every mutating caller
    needs: a missing handle and a foreign handle produce the same refusal, so an
    unknown handle is never a distinguishing answer.
    """
    actor = Actor.coerce(actor)
    cleaned = _clean(handle)
    now_ts = _now(now)
    with _lock:
        record = _owned_record_locked(cleaned, actor, now_ts)
        if not _record_matches(record, provider=provider, scope=scope,
                               purpose=purpose, target=target):
            # Reachable only for the owner (ownership was checked first), so
            # naming the mismatch leaks nothing the caller does not already know.
            raise ScanSessionError(
                "scan session is bound to a different binding",
                code="binding_mismatch")
        return _view(record)


def get_session(
    handle: str, *, actor: Any, now: Optional[float] = None
) -> Optional[Session]:
    """Read one session, or ``None``.

    ``None`` for a handle that is unknown or expired — and for those two only.
    A handle that *exists* but belongs to another user, tenant or auth session
    raises :class:`ScanSessionError` with ``code="not_owner"``. The split is
    deliberate: the probe path can stay branch-free for the two ordinary
    answers, while an ownership violation is loud enough to be audited. The
    refusal text is identical for all three mismatch dimensions, and a handler
    that must not disclose existence maps ``None`` and ``not_owner`` to the same
    client payload (the console convention is ``{"status": "success",
    "register_status": "expired"}``), which is what makes the wire answer
    indistinguishable.
    """
    actor = Actor.coerce(actor)
    cleaned = _clean(handle)
    if not cleaned:
        return None
    now_ts = _now(now)
    with _lock:
        record = _sessions.get(cleaned)
        if record is None:
            return None
        if not _record_owned_by(record, actor):
            raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
        if record["expires_at"] <= now_ts:
            # Expired on access: refused *and* dropped, so nothing can be read
            # out of it later and nothing is left to be resurrected.
            _sessions.pop(cleaned, None)
            return None
        return _view(record)


def attach_qr(
    handle: str,
    *,
    qrcode: str,
    qrcode_url: str = "",
    base_url: str = "",
    actor: Any,
    now: Optional[float] = None,
) -> Session:
    """Record the QR the provider just handed out for this session.

    Only valid while the session is ``pending``. That is the side-effect check
    the spec asks for: after a scan is confirmed the QR is spent, and letting a
    late refresh write new QR material into a confirmed session would let a
    second operator's scan be answered as this one's.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    handle = _clean(handle)
    with _lock:
        record = _owned_record_locked(handle, actor, now_ts)
        if record["status"] != STATUS_PENDING:
            raise ScanSessionError(
                "the QR can only be attached while the scan is pending",
                code="invalid_transition",
                context={"status": record["status"]})
        record["qrcode"] = _clean(qrcode)
        record["qrcode_url"] = _clean(qrcode_url)
        record["base_url"] = _clean(base_url)
        record["updated_at"] = now_ts
        record["version"] += 1
        return _view(record)


def mark_status(
    handle: str, status: str, *, actor: Any, error: str = "",
    now: Optional[float] = None,
) -> Session:
    """Advance the session's status along the documented state machine.

    ``pending -> confirmed -> committing -> committed`` plus the terminal
    ``cancelled`` / ``expired`` / ``failed``. Re-asserting the current status is
    a no-op, so a retried poll does not have to reason about what it last sent.
    ``committing -> confirmed`` exists for the commit path's own rollback.

    ``error`` is recorded verbatim, so the caller must pass a refusal *reason*
    (an error code, a service message), never provider credential material.
    """
    return _mark_status(
        handle, status, actor=actor, error=error, now=now, what="scan session")


def attach_provider_result(
    handle: str,
    *,
    result: Mapping[str, Any],
    sensitive_keys: Any,
    actor: Any,
    declared_keys: Any = None,
    now: Optional[float] = None,
) -> Session:
    """Hold the vendor's login result encrypted until the commit consumes it.

    ``result`` is the *narrowed* bundle the wiring built from the vendor answer:
    only the credential keys the channel type declares, never the whole vendor
    response and never a vendor-only field the identity service would reject
    (the contract itself is enforced by the caller's narrowing step too; this is
    the belt to that braces).

    ``declared_keys`` is that declaration — the channel type's full credential
    key set. It defaults to ``sensitive_keys`` for a bundle that is secret-only,
    but a real bundle is not: WeChat's carries the bot's callback base URL
    beside its token, and refusing the *declared* non-secret field as
    "undeclared" would make the whole scan unstoreable. ``sensitive_keys`` names
    which of those keys are secrets (the same declaration
    ``channel.channel_instances`` uses for its forms); everything else in the
    bundle is still stored inside the ciphertext, because the whole bundle is
    one credential and splitting it would mean a second, plaintext copy of half
    of it.

    Refuses rather than degrades when the deployment's credential key is absent:
    a session that cannot encrypt its vendor result must not hold one. The
    plaintext is never logged, never returned and never written to the receipt
    ledger.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    cleaned = _clean(handle)
    if not isinstance(result, Mapping) or not result:
        raise ScanSessionError(
            "a provider login result is required", code="bad_provider_result")
    named = {str(name) for name in (sensitive_keys or ())}
    if not named:
        # A caller that cannot say which fields are secret cannot be trusted to
        # hand us a bundle to encrypt: without the declaration this module would
        # have to guess, and guessing wrong means persisting a token in the
        # clear somewhere downstream.
        raise ScanSessionError(
            "the provider result needs a declared credential contract",
            code="bad_provider_result")
    declared = {str(name) for name in (declared_keys or ())} or set(named)
    if not named <= declared:
        raise ScanSessionError(
            "the declared credential contract does not contain every secret"
            " field", code="bad_provider_result",
            context={"undeclared_secrets": sorted(named - declared)[:8]})
    unknown = sorted(set(str(key) for key in result) - declared)
    if unknown:
        raise ScanSessionError(
            "the provider result carries undeclared fields",
            code="bad_provider_result", context={"unknown": unknown[:8]})
    try:
        from auth.crypto import encrypt_secret

        ciphertext = encrypt_secret(normalize_payload(result))
    except Exception as error:  # noqa: BLE001 - no key means no storage
        logger.error(
            "[ScanOnboarding] cannot encrypt the provider login result"
            f" ({type(error).__name__}); refusing to hold it"
        )
        raise ScanSessionError(
            "the provider result cannot be encrypted in this deployment",
            code="provider_result_crypto") from error
    with _lock:
        record = _owned_record_locked(cleaned, actor, now_ts)
        # Only a session that has actually finished its QR step may hold a login
        # result: storing one against a pending session would let a caller plant
        # a credential before the operator's phone ever approved anything.
        if record["status"] not in (STATUS_CONFIRMED, STATUS_COMMITTING):
            raise ScanSessionError(
                "the provider result can only be attached to a confirmed scan",
                code="invalid_transition", context={"status": record["status"]})
        record["provider_result_ct"] = ciphertext
        record["updated_at"] = now_ts
        record["version"] += 1
        return _view(record)


def provider_result(
    handle: str, *, actor: Any, now: Optional[float] = None
) -> Dict[str, Any]:
    """Decrypt this session's vendor login result. The only reader.

    The result is returned to the server-side caller only; nothing here is
    projected to a client, and a wiring that put it in a response body would be
    violating the module's contract rather than this function's.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    with _lock:
        record = _owned_record_locked(_clean(handle), actor, now_ts)
        ciphertext = record.get("provider_result_ct") or ""
    if not ciphertext:
        raise ScanSessionError(
            "no provider login result is held for this scan",
            code="no_provider_result")
    try:
        from auth.crypto import decrypt_secret

        payload = json.loads(decrypt_secret(ciphertext))
    except Exception as error:  # noqa: BLE001 - an unreadable bundle is a refusal
        raise ScanSessionError(
            "the provider login result cannot be read", code="provider_result_crypto"
        ) from error
    if not isinstance(payload, dict):
        raise ScanSessionError(
            "the provider login result is malformed", code="bad_provider_result")
    return payload


def open_grant(
    handle: str,
    *,
    actor: Any,
    channel_type: str,
    now: Optional[float] = None,
) -> str:
    """This session's one-time create authorization, minted on first use.

    The ticket is *session state*, and this is the only place it is minted:
    storing it against the session is what makes a retried submit and a
    response-loss read-back present the **same** authorization — and therefore
    the same idempotency key — instead of silently becoming a second authorized
    create. The value is never returned to a client by this module (see
    :meth:`Session.payload`); the wiring redeems it server-side.

    Minting is allowed only for a ``confirmed`` session: the grant exists to
    prove the operator was present at a *finished* vendor scan, so opening one
    before the provider confirmed would authorize a create with nothing behind
    it. A session that already holds a ticket keeps it — including after its
    grant has expired, because the receipt the ticket keys is read back on
    exactly that handle, and re-minting would turn "read my result back" into
    "authorize a new create" (which the module then refuses, correctly, as a new
    key on a spent operation).

    ``channel_type`` is the identity service's vocabulary for what is being
    created and is bound into the grant; a session that already opened a ticket
    for another channel type refuses rather than quietly widening it.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    ctype = _clean(channel_type)
    if not ctype:
        raise ScanSessionError(
            "a channel type is required for the authorization",
            code="bad_binding")
    cleaned = _clean(handle)
    with _lock:
        record = _owned_record_locked(cleaned, actor, now_ts, what="scan session")
        held = _clean(record.get("grant_ticket"))
        if held:
            if _clean(record.get("grant_channel_type")) not in ("", ctype):
                raise ScanSessionError(
                    "this scan is bound to a different channel type",
                    code="binding_mismatch",
                    context={"channel_type":
                             _clean(record.get("grant_channel_type"))})
            return held
        if record["status"] != STATUS_CONFIRMED:
            raise ScanSessionError(
                "a create authorization can only be opened for a confirmed scan",
                code="invalid_transition", context={"status": record["status"]})
        owner = record["owner_user_id"]
        tenant = record["tenant_id"]
    from auth import scan_authorization

    ticket = scan_authorization.mint(actor_user_id=owner, tenant_id=tenant,
                                    channel_type=ctype)
    with _lock:
        record = _sessions.get(cleaned)
        if record is None or not _record_owned_by(record, actor):
            # Swept or replaced while the grant was being minted. Drop the grant
            # rather than let an authorization outlive the scan that earns it.
            _discard_grant(ticket, owner=owner, tenant=tenant, channel_type=ctype)
            raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
        existing = _clean(record.get("grant_ticket"))
        if existing:
            # Two polls of one confirmed session raced. The loser's ticket is
            # dropped: one session authorizes one create, and the winner's is
            # already the one the receipt key will name.
            _discard_grant(ticket, owner=owner, tenant=tenant, channel_type=ctype)
            return existing
        record["grant_ticket"] = ticket
        record["grant_channel_type"] = ctype
        record["updated_at"] = now_ts
        record["version"] += 1
    logger.info("[ScanOnboarding] opened a create authorization for a scan session")
    return ticket


def _discard_grant(ticket: str, *, owner: str, tenant: str,
                   channel_type: str) -> None:
    """Burn an authorization this module minted and cannot attach to a session."""
    if not _clean(ticket):
        return
    try:
        _consume_authorization(ticket, owner=owner, tenant=tenant,
                               channel_type=channel_type)
    except Exception as error:  # noqa: BLE001 - best effort, never fatal
        logger.warning(
            "[ScanOnboarding] could not discard an unattached authorization"
            f" ({type(error).__name__})"
        )


def latest_session(
    *,
    actor: Any,
    provider: Optional[str] = None,
    purpose: Optional[str] = None,
    now: Optional[float] = None,
) -> Optional[Session]:
    """The caller's own most recent live session, or ``None``.

    This exists for one reason: the console's poll is a bare ``{action: "poll"}``
    and predates the handle. Rather than fall back to a global slot — the very
    thing this module removed — the lookup is scoped to the *verified actor*, so
    "the latest scan" can only ever mean "the latest scan *you* started". The
    ownership triple (user, tenant, auth session) is part of the filter, so this
    is not an existence oracle either: another initiator's session, a session
    started in a different login, and no session at all are all ``None``.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    newest: Optional[Mapping[str, Any]] = None
    with _lock:
        for record in _sessions.values():
            if record["expires_at"] <= now_ts:
                continue
            if record["status"] in TERMINAL_STATUSES:
                if record["status"] != STATUS_COMMITTED:
                    continue
                # A committed session is the one terminal state a client still
                # has to be able to reach: its receipt is what a submit whose
                # response was lost reads back from. Cancelled, expired and
                # failed sessions have nothing to read back, so they stay out.
            if not _record_owned_by(record, actor):
                continue
            if provider is not None and str(provider).strip() != record["provider"]:
                continue
            if purpose is not None and str(purpose).strip() != record["purpose"]:
                continue
            if newest is None or record["created_at"] > newest["created_at"]:
                newest = record
        return _view(newest) if newest is not None else None


def shared_state_ready() -> tuple:
    """``(ready, reason)`` for this deployment's registry/receipt sharing.

    The registry, the receipt ledger and ``auth.scan_authorization``'s grants are
    process-local. A multi-worker deployment would give each worker its own view
    of one scan — two handles for one QR, a receipt stored in the worker that
    answered the submit, a grant unknown to the worker that polls — which is
    exactly the failure mode the spec refuses to paper over. The module cannot
    detect a topology by itself, so it defers to the gate that already exists for
    the same reason: ``build_web_app`` calls
    ``auth.ratelimit.reject_multi_worker_identity`` and refuses to start at all
    on a multi-worker configuration. Consulting it here means the answer cannot
    drift from the one the deployment already enforced at startup.

    A caller that gets ``(False, reason)`` MUST refuse to serve a scan rather
    than serve it from one worker's private state.
    """
    try:
        from auth.ratelimit import reject_multi_worker_identity
    except Exception as error:  # noqa: BLE001 - an unavailable gate is not consent
        return False, f"state_gate_unavailable:{type(error).__name__}"
    try:
        reject_multi_worker_identity()
    except Exception as error:  # noqa: BLE001 - the gate itself refuses
        return False, f"process_local_state:{type(error).__name__}"
    return True, ""


def sweep(now: Optional[float] = None) -> Dict[str, int]:
    """Housekeeping for both registries: one call, two counters.

    Exposed so the wiring has a single place to bound growth — every expired
    session (and, with it, the encrypted provider result it held) and every
    expired receipt. It writes nothing else: a swept session cannot become live
    again and a swept receipt cannot revive a redeemed grant.
    """
    now_ts = _now(now)
    with _lock:
        sessions = _purge_expired_locked(now_ts)
        expired = [record for record in _receipts.values()
                   if record["expires_at"] <= now_ts]
        for record in expired:
            _drop_receipt_locked(record)
    return {"sessions": sessions, "receipts": len(expired)}


def _mark_status(
    handle: str, status: str, *, actor: Any, error: str,
    now: Optional[float], what: str,
) -> Session:
    """The one implementation behind :func:`mark_status`.

    Kept private so the public name cannot be shadowed by a second, subtly
    different transition path: every state change in this module goes through
    here, which is what makes "the QR material and the encrypted vendor result
    are dropped on the transition out of the states that need them" a property of
    the state machine rather than of each caller.
    """
    actor = Actor.coerce(actor)
    now_ts = _now(now)
    handle = _clean(handle)
    wanted = _clean(status)
    if wanted not in SESSION_STATUSES:
        raise ScanSessionError(
            f"unknown {what} status: {wanted!r}", code="invalid_status",
            context={"allowed": sorted(SESSION_STATUSES)})
    with _lock:
        record = _owned_record_locked(handle, actor, now_ts)
        current = record["status"]
        if current != wanted and wanted not in _ALLOWED_TRANSITIONS.get(
                current, frozenset()):
            raise ScanSessionError(
                f"cannot move a scan session from {current!r} to {wanted!r}",
                code="invalid_transition",
                context={"from": current, "to": wanted})
        record["status"] = wanted
        record["updated_at"] = now_ts
        record["version"] += 1
        if wanted != STATUS_PENDING:
            _scrub_qr(record)
        burn = ""
        owner = ""
        tenant = ""
        ctype = ""
        if wanted in TERMINAL_STATUSES:
            record["error"] = str(error or "")[:500]
            if wanted != STATUS_COMMITTED:
                # A cancelled, expired or failed session has nothing left to
                # bind, so the encrypted vendor result goes with the QR material.
                # ``committed`` is deliberately *not* scrubbed: a committed
                # session is exactly the one a retried submit comes back to, and
                # the retry carries only a handle — the bundle held here is what
                # lets it rebuild the identical normalized content and be
                # answered as a read-back instead of refused as "different
                # content". The ciphertext is already encrypted, is readable only
                # by the initiator's own (user, tenant, auth session), and dies
                # with the session's TTL.
                _scrub_provider_result(record)
                # An authorization that was never used would otherwise outlive
                # the scan that justifies it for the rest of its TTL.
                burn = _clean(record.get("grant_ticket"))
                owner = record["owner_user_id"]
                tenant = record["tenant_id"]
                ctype = _clean(record.get("grant_channel_type"))
                record["grant_ticket"] = ""
                record["grant_channel_type"] = ""
        elif error:
            record["error"] = str(error)[:500]
        view = _view(record)
    if burn:
        _discard_grant(burn, owner=owner, tenant=tenant, channel_type=ctype)
    return view


def _record_matches(
    record: Mapping[str, Any],
    *,
    provider: Optional[str] = None,
    scope: Optional[str] = None,
    purpose: Optional[str] = None,
    target: Optional[str] = None,
) -> bool:
    for name, value in (("provider", provider), ("scope", scope),
                        ("purpose", purpose), ("target", target)):
        if value is None:
            continue
        if str(value).strip() != record[name]:
            return False
    return True


def purge_expired(now: Optional[float] = None) -> int:
    """Drop every session whose TTL has elapsed; return how many.

    Purges only ever remove. Nothing here writes to ``auth.scan_authorization``,
    so a purge cannot revive a redeemed grant, and nothing here recreates a
    session, so a purge cannot convert "expired" back into "pending".
    """
    now_ts = _now(now)
    with _lock:
        return _purge_expired_locked(now_ts)


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------
#
# A receipt key is a keyed digest of everything that makes two submits "the same
# operation": the provider, scope, purpose, target, the initiator, the tenant,
# the initiator's auth session, the one-time authorization handle the scan
# minted, and a normalized digest of the sensitive payload (the credential
# bundle plus the display name, agent and channel type being created). Two
# submits collide only if all of those agree.
#
# The authorization handle is one of the digest inputs, and it is a 256-bit
# secret that never leaves the server, so the digest cannot be recomputed by
# anyone who does not already hold the grant — a leaked column would not be
# brute-forceable back to a credential bundle.
#
# The auth session is included even though a replay from a *new* login of the
# same user would then miss its own receipt. That is the required behaviour: a
# receipt is a replay of one submit inside one live session, and answering a
# different session would answer a request whose "operator present" proof has
# not been re-established.

_receipts: Dict[str, Dict[str, Any]] = {}
#: ticket -> receipt key. One redeemed authorization names at most one committed
#: operation, and indexing it lets a replay be resolved from the receipt alone
#: after the QR session that started it is long gone.
_receipts_by_ticket: Dict[str, str] = {}


def _normalize_value(value: Any, depth: int) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise ScanReceiptError(
            "payload is nested too deeply to normalize", code="bad_payload")
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # NFC + trim only. Internal whitespace is *not* collapsed: a token with a
        # meaningful space and one without must not fold into the same digest.
        return unicodedata.normalize("NFC", value).strip()
    if isinstance(value, (bytes, bytearray)):
        return "base64:" + base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            name = unicodedata.normalize("NFC", str(key)).strip()
            if not name:
                raise ScanReceiptError(
                    "payload contains an empty key", code="bad_payload")
            if item is None:
                # An omitted field and an explicit null are the same request.
                continue
            normalized[name] = _normalize_value(item, depth + 1)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_normalize_value(item, depth + 1) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, ensure_ascii=False, default=str))
    raise ScanReceiptError(
        f"unsupported payload value type: {type(value).__name__}",
        code="bad_payload")


def normalize_payload(payload: Any) -> str:
    """Canonical JSON for *payload*, so equal requests render equal strings.

    Exactly these rules, and nothing more:

    * mappings are ordered by key, keys are NFC-normalized and trimmed;
    * a ``None`` value is dropped, so "absent" and "null" are one request;
    * string values are NFC-normalized and trimmed, internal spacing preserved;
    * bytes become ``base64:<...>``; sets become a value-ordered list;
    * sequences keep their order (order can be meaningful in a request).
    """
    if payload is None:
        payload = {}
    return json.dumps(
        _normalize_value(payload, 0),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Any) -> str:
    """The digest half of a receipt key: see :func:`normalize_payload`."""
    return _digest(normalize_payload(payload))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


#: Domain separator, so a receipt key can never equal a digest computed for some
#: other purpose out of the same components.
_RECEIPT_KEY_TAG = "rongai-scan-receipt-v1"


def receipt_key(
    *,
    provider: str,
    scope: str,
    purpose: str,
    target: str,
    owner_user_id: str,
    tenant_id: str,
    auth_session_id: str,
    scan_ticket: str,
    payload: Any,
) -> str:
    """The idempotency key for one scan-driven create.

    Inputs, in order, each trimmed and NFC-normalized by ``str.strip`` semantics
    of the caller: provider, scope, purpose, target, owner user, tenant, auth
    session, the scan authorization handle, and the payload digest. The
    authorization handle is what salts the digest; see the section comment above
    for why nothing here is brute-forceable from the stored value.
    """
    ticket = _clean(scan_ticket)
    if not ticket:
        raise ScanReceiptError(
            "a scan authorization handle is required for a receipt key",
            code="bad_binding")
    parts = [
        _clean(provider), _clean(scope), _clean(purpose), _clean(target),
        _clean(owner_user_id), _clean(tenant_id), _clean(auth_session_id),
        ticket, payload_digest(payload),
    ]
    return _digest(_RECEIPT_KEY_TAG + "\x1f" + "\x1f".join(
        unicodedata.normalize("NFC", part) for part in parts))


def _looks_secret(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _project_receipt_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    """Narrow *result* to the declared non-sensitive fields.

    A key that names a secret shape and is not one of the declared fields is a
    programming error in the caller and raises: quietly dropping it would hide a
    token on its way into the ledger. A key outside the contract but harmless is
    dropped, which is what keeps the ledger's shape stable as the service
    projection grows.
    """
    if not isinstance(result, Mapping):
        raise ScanReceiptError(
            "a receipt result must be a mapping", code="bad_payload")
    projection: Dict[str, Any] = {}
    for key, value in result.items():
        name = str(key)
        if name in _RECEIPT_FIELDS:
            projection[name] = value
            continue
        if _looks_secret(name):
            raise ScanReceiptError(
                f"refusing to store receipt field {name!r}: it names a secret",
                code="secret_in_receipt")
    return projection


def _assert_values_absent(projection: Mapping[str, Any],
                          sensitive_values: List[str], *, label: str,
                          code: str, error_type: type = ScanReceiptError) -> None:
    """Refuse a projection that carries a known credential *value*.

    The field-name check next to this one cannot catch a secret that arrives
    under an innocent name (a display name set to a pasted token), so both the
    request fields and the create's answer are also checked against the values
    the caller handed us. ``label``/``code``/``error_type`` name which of the two
    refused, so the wiring can tell "your form had a token in it" (no write
    happened, a commit error) from "the service echoed the token back" (a write
    did happen, a receipt error).
    """
    wanted = [value for value in sensitive_values if value]
    if not wanted:
        return
    for key, value in projection.items():
        if not isinstance(value, str) or not value:
            continue
        for secret in wanted:
            if secret in value:
                raise error_type(
                    f"refusing {label} {str(key)!r}: it carries credential"
                    " material",
                    code=code)


def lookup_receipt(key: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The stored non-sensitive result for *key*, or ``None``.

    Returns only the declared fields plus ``receipt_state`` (``complete`` once
    the commit tail finished, ``staged`` when the instance row landed but a later
    step did not) and ``committed_at``. Never a token, never the binding's auth
    session: the record's ownership binding stays internal, because reading it
    back is not the same permission as reading the result out.
    """
    cleaned = _clean(key)
    if not cleaned:
        return None
    now_ts = _now(now)
    with _lock:
        record = _live_receipt_locked(cleaned, now_ts)
        if record is None:
            return None
        return _receipt_payload(record)


def _receipt_payload(record: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(record["result"])
    payload["receipt_key"] = record["key"]
    payload["receipt_state"] = record["receipt_state"]
    payload["committed_at"] = record["created_at"]
    return payload


def _live_receipt_locked(key: str, now_ts: float) -> Optional[Dict[str, Any]]:
    record = _receipts.get(key)
    if record is None:
        return None
    if record["expires_at"] <= now_ts:
        # An expired receipt is not readable and is not a tombstone: the durable
        # guarantees are elsewhere (the redeemed authorization and the instance
        # row's own uniqueness), which is why dropping it cannot re-create
        # anything.
        _drop_receipt_locked(record)
        return None
    return record


def _drop_receipt_locked(record: Mapping[str, Any]) -> None:
    _receipts.pop(record["key"], None)
    if _receipts_by_ticket.get(record["ticket"] or "") == record["key"]:
        _receipts_by_ticket.pop(record["ticket"], None)


def _receipt_record_for_ticket_locked(
    ticket: str, now_ts: float
) -> Optional[Dict[str, Any]]:
    key = _receipts_by_ticket.get(ticket)
    if not key:
        return None
    return _live_receipt_locked(key, now_ts)


def store_receipt(
    key: str,
    result: Mapping[str, Any],
    *,
    binding: Optional[Mapping[str, Any]] = None,
    ticket: str = "",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record a receipt with a 24h TTL; return the stored projection.

    ``binding`` is the server-side ownership tuple (owner, tenant, auth session)
    kept for the read-back check and never returned by :func:`lookup_receipt`.
    ``ticket`` is the authorization handle that names this operation; it is
    indexed so a replay can be resolved after the QR session expired.
    """
    cleaned = _clean(key)
    if not cleaned:
        raise ScanReceiptError("a receipt key is required", code="bad_binding")
    projection = _project_receipt_result(result)
    now_ts = _now(now)
    with _lock:
        existing = _receipts.get(cleaned)
        created_at = existing["created_at"] if existing else now_ts
        record = {
            "key": cleaned,
            "ticket": _clean(ticket) or (existing or {}).get("ticket", ""),
            "binding": dict(binding) if binding is not None
                       else dict((existing or {}).get("binding") or {}),
            "result": projection,
            "created_at": created_at,
            "expires_at": created_at + RECEIPT_TTL_SECONDS,
            "receipt_state": "complete",
            "authorization_consumed": bool(
                (existing or {}).get("authorization_consumed")),
        }
        _receipts[cleaned] = record
        if record["ticket"]:
            _receipts_by_ticket[record["ticket"]] = cleaned
        return _receipt_payload(record)


def purge_receipts(now: Optional[float] = None) -> int:
    """Drop every expired receipt; return how many.

    Touches the ledger only. Grants live in ``auth.scan_authorization`` and are
    not read, written or revived here, so purging receipts can neither restore a
    redeemed authorization nor redeem one that is still outstanding.
    """
    now_ts = _now(now)
    with _lock:
        expired = [record for record in _receipts.values()
                   if record["expires_at"] <= now_ts]
        for record in expired:
            _drop_receipt_locked(record)
        return len(expired)


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

#: Serializes commits process-wide. A scan is a human-paced operation, so there
#: is no throughput argument against one lock, and holding it makes "exactly one
#: write per receipt key" a property of this module rather than of whichever
#: storage engine happens to sit behind the create callable. Injected callables
#: must not re-enter :func:`commit_scan_binding`.
_commit_lock = threading.Lock()


@dataclass(frozen=True)
class Readback:
    """What a replay knows when it asks to read the original result back.

    Handed to the ``readback_guard`` callable so the wiring can re-establish the
    things this module cannot: that the auth session is still current, that the
    initiator is still an active member, and that the target instance binding has
    not been revoked or governance-disabled. ``result`` is the stored
    non-sensitive projection, so the guard can look the instance up by id.
    """

    key: str
    handle: str
    provider: str
    scope: str
    purpose: str
    target: str
    channel_type: str
    owner_user_id: str
    tenant_id: str
    auth_session_id: str
    result: Mapping[str, Any]
    committed_at: float


def _binding_from(record: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "provider": record["provider"],
        "scope": record["scope"],
        "purpose": record["purpose"],
        "target": record["target"],
        "owner_user_id": record["owner_user_id"],
        "tenant_id": record["tenant_id"],
        "auth_session_id": record["auth_session_id"],
    }


def _binding_from_session(session: Session) -> Dict[str, str]:
    return {
        "provider": session.provider,
        "scope": session.scope,
        "purpose": session.purpose,
        "target": session.target,
        "owner_user_id": session.owner_user_id,
        "tenant_id": session.tenant_id,
        "auth_session_id": session.auth_session_id,
    }


def _binding_matches_actor(binding: Mapping[str, Any], actor: Actor) -> bool:
    return (_clean(binding.get("owner_user_id")) == actor.user_id
            and _clean(binding.get("tenant_id")) == actor.tenant_id
            and _clean(binding.get("auth_session_id")) == actor.auth_session_id)


def _binding_matches_dims(
    binding: Mapping[str, Any], *, provider: Optional[str] = None,
    scope: Optional[str] = None, purpose: Optional[str] = None,
    target: Optional[str] = None,
) -> bool:
    for name, value in (("provider", provider), ("scope", scope),
                        ("purpose", purpose), ("target", target)):
        if value is None:
            continue
        if str(value).strip() != _clean(binding.get(name)):
            return False
    return True


def _key_for_binding(
    binding: Mapping[str, Any], *, ticket: str, payload: Mapping[str, Any]
) -> str:
    return receipt_key(
        provider=_clean(binding.get("provider")),
        scope=_clean(binding.get("scope")),
        purpose=_clean(binding.get("purpose")),
        target=_clean(binding.get("target")),
        owner_user_id=_clean(binding.get("owner_user_id")),
        tenant_id=_clean(binding.get("tenant_id")),
        auth_session_id=_clean(binding.get("auth_session_id")),
        scan_ticket=ticket,
        payload=payload,
    )


def _verify_authorization(ticket: str, *, owner: str, tenant: str,
                          channel_type: str) -> None:
    from auth import scan_authorization

    if scan_authorization.verify(ticket, actor_user_id=owner, tenant_id=tenant,
                                 channel_type=channel_type):
        return
    raise ScanCommitError(
        "the scan authorization is not valid for this create",
        code="authorization_refused",
        context={"channel_type": channel_type})


def _ticket_is_the_staged_one(record: Mapping[str, Any], ticket: str) -> bool:
    """Whether *ticket* is the handle this operation was staged with.

    The staged receipt keeps the handle in process memory only (it is this
    process's own short-lived grant id, never a long-term credential), and it was
    written *after* the receipt key matched — so an equal handle means the caller
    is presenting the same grant handle for the same operation, not a fresh
    authorization for a new one. That is what lets a tail failure resume even
    after the delivered create redeemed the grant in its own transaction, while a
    *different* handle still has to be a live authorization.
    """
    return bool(_clean(ticket)) and _clean(record.get("ticket")) == _clean(ticket)


def _consume_authorization(ticket: str, *, owner: str, tenant: str,
                           channel_type: str) -> bool:
    """Redeem the grant once. Called only after the row has committed."""
    from auth import scan_authorization

    return bool(scan_authorization.consume(
        ticket, actor_user_id=owner, tenant_id=tenant,
        channel_type=channel_type))


def _redeem_authorization(ticket: str, *, owner: str, tenant: str,
                          channel_type: str) -> bool:
    """Whether this operation's authorization is now *unusable*, not "did I win".

    ``IdentityService.create_*_channel_instance`` redeems the grant itself as its
    transaction commits — it is the same ``auth.scan_authorization`` either way —
    so a second ``consume`` from here answers ``False`` for a grant that is in
    fact spent. Reporting that as "not consumed" would be wrong in the one place
    it matters, the receipt the console reads back after a lost response.

    The honest question is not "did this call redeem it" but "can it still be
    redeemed". So: try to redeem; if that fails, the answer is whether the grant
    is *still valid*. Still valid means neither party spent it — the one case
    that must not be reported as consumed.
    """
    if _consume_authorization(ticket, owner=owner, tenant=tenant,
                              channel_type=channel_type):
        return True
    from auth import scan_authorization

    return not scan_authorization.verify(
        ticket, actor_user_id=owner, tenant_id=tenant, channel_type=channel_type)


def _secret_values(values: Mapping[str, Any]) -> List[str]:
    """The values of the secret-shaped fields in a credential bundle.

    Only hint-matching field *values* are collected. Every value would be both
    wrong and harmful: a bundle legitimately carries non-secret strings (a bot
    display name, a callback base URL) that may equal the instance's display
    name, and refusing that create would be a false alarm about a token that was
    never there.
    """
    out: List[str] = []
    for name, value in (values or {}).items():
        if isinstance(value, Mapping):
            out.extend(_secret_values(value))
            continue
        if not _looks_secret(str(name)):
            continue
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def _credential_secret_present(credentials: Mapping[str, Any]) -> bool:
    return any(_looks_secret(name) and _clean(value)
               for name, value in (credentials or {}).items())


def commit_scan_binding(
    *,
    handle: str,
    actor: Any,
    scan_ticket: str,
    channel_type: str,
    create_instance: Callable[..., Any],
    display_name: str = "",
    agent_id: str = "",
    credentials: Optional[Mapping[str, Any]] = None,
    provider: Optional[str] = None,
    scope: Optional[str] = None,
    purpose: Optional[str] = None,
    target: Optional[str] = None,
    reserve_quota: Optional[Callable[..., Any]] = None,
    release_quota: Optional[Callable[[Any], None]] = None,
    record_audit: Optional[Callable[..., Any]] = None,
    readback_guard: Optional[Callable[[Readback], bool]] = None,
    receipt_sink: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
    quota_required: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Commit the create a finished scan authorized. Idempotent per key.

    **Order, and why it is this order.**

    1. *Resolve the binding.* From the live QR session when there is one, else
       from the receipt named by the presented authorization handle. Ownership is
       checked before anything else, so a foreign handle is refused before any
       dimension mismatch could describe it.
    2. *Idempotency.* A live receipt for this exact key (same binding, same
       normalized content) short-circuits everything else: a complete one is read
       back behind ``readback_guard`` (the current session, membership and target
       permission re-check this module cannot do), a staged one resumes the tail
       of the commit that already wrote the row. Either way the create callable is
       not called again.
    3. *Authorization.* :func:`verify` up front, never :func:`consume`. A write
       refused for a later reason must leave the operator the authorization they
       still need.
    4. *Quota reservation.* Before the write, deliberately: a quota refusal that
       arrived after the row landed could only be repaired by deleting a
       committed instance, which this module must not do. The reservation is
       released if the write itself fails.
    5. *Instance and credential write*, through the injected ``create_instance``
       (the identity service's own entry point). Its transaction owns the row,
       the encrypted bundle, the credential version and the create audit, so an
       audit failure there rolls all of it back. This module never opens the
       identity database.
    6. *Stage the journal.* The instant the row exists, the non-sensitive result
       is recorded as ``staged``. This is what makes a failure in the tail —
       audit, receipt store — resumable by the same key instead of a duplicate
       instance on retry.
    7. *Audit* the scan binding itself, then *store the receipt* (``complete``).
    8. *Consume* the authorization, last. A ``False`` here is recorded, not
       raised: the write already committed, and refusing now would report a
       success as a failure.

    The create callable's exception is propagated unchanged, because the identity
    service's own codes and statuses are what the console maps; the session is
    rolled back to ``confirmed`` and the reservation released so the retry is
    possible. A refusal from step 7 or 8's ledger is raised as
    :class:`ScanReceiptError`, a refusal from the audit as
    :class:`ScanCommitError`; both leave the staged journal in place.

    No credential value ever reaches the return value, the receipt or a log
    line: a stored long-term secret is reported as ``secret_present: True``.
    """
    actor = Actor.coerce(actor)
    if not callable(create_instance):
        raise ScanCommitError(
            "a create_instance callable is required", code="bad_binding")
    ctype = _clean(channel_type)
    if not ctype:
        raise ScanCommitError("a channel type is required", code="bad_binding")
    ticket = _clean(scan_ticket)
    if not ticket:
        raise ScanCommitError(
            "a scan authorization is required", code="authorization_refused")
    credentials_map = dict(credentials or {})
    payload = {
        "channel_type": ctype,
        "display_name": _clean(display_name),
        "agent_id": _clean(agent_id),
        "credentials": credentials_map,
    }
    with _commit_lock:
        return _commit_locked(
            actor=actor, handle=_clean(handle), ticket=ticket, ctype=ctype,
            create_instance=create_instance, display_name=_clean(display_name),
            agent_id=_clean(agent_id), credentials=credentials_map,
            payload=payload, provider=provider, scope=scope, purpose=purpose,
            target=target, reserve_quota=reserve_quota,
            release_quota=release_quota, record_audit=record_audit,
            readback_guard=readback_guard, receipt_sink=receipt_sink,
            quota_required=quota_required, now=now)


def _commit_locked(
    *,
    actor: Actor,
    handle: str,
    ticket: str,
    ctype: str,
    create_instance: Callable[..., Any],
    display_name: str,
    agent_id: str,
    credentials: Dict[str, Any],
    payload: Dict[str, Any],
    provider: Optional[str],
    scope: Optional[str],
    purpose: Optional[str],
    target: Optional[str],
    reserve_quota: Optional[Callable[..., Any]],
    release_quota: Optional[Callable[[Any], None]],
    record_audit: Optional[Callable[..., Any]],
    readback_guard: Optional[Callable[[Readback], bool]],
    receipt_sink: Optional[Callable[[str, Mapping[str, Any]], Any]],
    quota_required: bool,
    now: Optional[float],
) -> Dict[str, Any]:
    now_ts = _now(now)

    with _lock:
        existing = _receipt_record_for_ticket_locked(ticket, now_ts)
        if existing is not None:
            existing = dict(existing)
            existing["result"] = dict(existing["result"])

    session = _resolve_session(
        handle, actor=actor, provider=provider, scope=scope, purpose=purpose,
        target=target, now=now_ts, required=existing is None)

    if existing is not None:
        # The authorization handle already names a committed operation in this
        # process. Answer that operation, or refuse — never create.
        return _resolve_existing(
            existing, session=session, actor=actor, ticket=ticket, ctype=ctype,
            payload=payload, provider=provider, scope=scope, purpose=purpose,
            target=target, readback_guard=readback_guard,
            receipt_sink=receipt_sink, record_audit=record_audit,
            display_name=display_name, agent_id=agent_id, handle=handle,
            credentials=credentials, now=now_ts)

    if session is None:
        raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
    if session.terminal:
        raise ScanCommitError(
            "this scan session has already finished; start a new scan",
            code="terminal", context={"status": session.status})

    binding = _binding_from_session(session)
    key = _key_for_binding(binding, ticket=ticket, payload=payload)
    # Refuse a request field that carries credential material *before* any side
    # effect: a create that would persist a token under a display name must not
    # happen at all, not merely be refused afterwards.
    _assert_values_absent({"display_name": display_name, "agent_id": agent_id},
                          _secret_values(credentials), label="the request field",
                          code="secret_in_request", error_type=ScanCommitError)

    _verify_authorization(ticket, owner=session.owner_user_id,
                          tenant=session.tenant_id, channel_type=ctype)
    _advance_status(handle, actor=actor, status=STATUS_COMMITTING, now=now_ts)

    service_scope = (SERVICE_SCOPE_USER if session.scope == SCOPE_PERSONAL
                     else SCOPE_TENANT)
    try:
        reservation = _reserve_quota(
            reserve_quota, quota_required=quota_required,
            actor_user_id=session.owner_user_id, tenant_id=session.tenant_id,
            owner_user_id=session.owner_user_id, scope=session.scope,
            channel_type=ctype)
    except Exception as error:
        # Reserve before write, and roll the session back on refusal: a quota
        # refusal must leave no instance, no receipt and a session the operator
        # can still submit with.
        _restore_status(handle, actor=actor, status=STATUS_CONFIRMED,
                        error=_refusal_tag(error), now=now_ts)
        raise

    create_kwargs = {
        "actor_user_id": session.owner_user_id,
        "tenant_id": session.tenant_id,
        "channel_type": ctype,
        "display_name": display_name,
        "agent_id": agent_id,
        "credentials": dict(credentials),
        "scan_ticket": ticket,
        # The scan path has no operator password to present — that is the whole
        # point of the raw scan still being fresh — so it presents the empty
        # password and lets the ticket above stand in for it, which is exactly
        # the contract ``IdentityService._require_channel_write_authorization``
        # documents for the auto-persist path. An explicit password always wins
        # there, so a caller that *does* have one keeps the manual behaviour.
        "recent_password": "",
        "scope": service_scope,
        "owner_user_id": (session.owner_user_id
                          if service_scope == SERVICE_SCOPE_USER else ""),
        "allow_owner": session.scope == SCOPE_PERSONAL,
    }
    try:
        created = create_instance(**create_kwargs)
    except Exception as error:
        # Nothing but the reservation may have changed, so undo that and give the
        # operator back a session they can submit again with the same grant.
        _release_quota_reservation(release_quota, reservation)
        _restore_status(handle, actor=actor, status=STATUS_CONFIRMED,
                        error=_refusal_tag(error), now=now_ts)
        logger.warning(
            f"[ScanOnboarding] {session.scope} create refused by the identity"
            f" service ({type(error).__name__}); authorization kept")
        raise

    # The journal goes down before anything that can refuse, so a failure in the
    # projection's own leak check still leaves a resumable commit rather than a
    # committed instance nothing remembers.
    _stage_receipt(
        key=key, ticket=ticket, binding=binding,
        projection=_journal_projection(key=key, ctype=ctype, created=created),
        now_ts=now_ts)
    projection = _projection(
        key=key, binding=binding, ctype=ctype,
        created=created, display_name=display_name, agent_id=agent_id,
        credentials=credentials, now_ts=now_ts)
    _stage_receipt(key=key, ticket=ticket, binding=binding,
                   projection=projection, now_ts=now_ts)
    outcome = "committed"
    _finish_commit(
        key=key, ticket=ticket, binding=binding, projection=projection,
        session=session, ctype=ctype, outcome=outcome,
        record_audit=record_audit, receipt_sink=receipt_sink,
        display_name=display_name, agent_id=agent_id, credentials=credentials,
        now_ts=now_ts)
    logger.info(
        f"[ScanOnboarding] committed {ctype} instance for {session.scope} scope"
        f" (outcome={outcome})"
    )
    return _result_payload(projection, outcome=outcome,
                           authorization_consumed=_authorization_consumed(key),
                           receipt_state="complete")


def _resolve_session(
    handle: str, *, actor: Actor, provider: Optional[str],
    scope: Optional[str], purpose: Optional[str], target: Optional[str],
    now: float, required: bool,
) -> Optional[Session]:
    """The caller's live session, or ``None`` when the receipt may stand alone.

    ``required`` is False exactly when a live receipt already names this
    operation: the QR session's 900s lifetime is far shorter than the receipt's
    24h one on purpose, and a replay inside the receipt window must still be
    answerable after the session that started it has been swept.
    """
    if not handle:
        if required:
            raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
        return None
    try:
        return require_session(
            handle, actor=actor, provider=provider, scope=scope,
            purpose=purpose, target=target, now=now)
    except ScanSessionError as error:
        if not required and error.code in ("not_owner", "expired"):
            return None
        raise


def _resolve_existing(
    record: Mapping[str, Any], *, session: Optional[Session], actor: Actor,
    ticket: str, ctype: str,
    payload: Mapping[str, Any], provider: Optional[str], scope: Optional[str],
    purpose: Optional[str], target: Optional[str],
    readback_guard: Optional[Callable[[Readback], bool]],
    receipt_sink: Optional[Callable[[str, Mapping[str, Any]], Any]],
    record_audit: Optional[Callable[..., Any]],
    display_name: str, agent_id: str, handle: str,
    credentials: Mapping[str, Any], now: float,
) -> Dict[str, Any]:
    binding = dict(record["binding"] or {})
    if not _binding_matches_actor(binding, actor):
        # Someone else's redeemed authorization handle: answer exactly as an
        # unknown handle does.
        raise ScanSessionError(_NOT_OWNED_MESSAGE, code="not_owner")
    if session is not None and not _binding_matches_dims(
            binding, provider=session.provider, scope=session.scope,
            purpose=session.purpose, target=session.target):
        # The caller holds their own live session, and it points somewhere other
        # than the operation this authorization handle redeemed. A receipt is
        # keyed off the *stored* binding, so without this check the requesting
        # session's target would be ignored and a second session could read back
        # the first one's result.
        raise ScanCommitError(
            "the authorization handle was redeemed for a different binding",
            code="binding_mismatch")
    if not _binding_matches_dims(binding, provider=provider, scope=scope,
                                 purpose=purpose, target=target):
        raise ScanCommitError(
            "the authorization handle was redeemed for a different binding",
            code="binding_mismatch")
    key = _key_for_binding(binding, ticket=ticket, payload=payload)
    if key != record["key"]:
        # Same handle, different content: this is not a replay of the original
        # operation, it is a second create trying to reuse a spent grant. A fresh
        # key needs a fresh authorization.
        raise ScanCommitError(
            "the authorization handle was redeemed for different content;"
            " change requires a new authorization handle",
            code="authorization_refused", context={"channel_type": ctype})

    if record["receipt_state"] == "complete":
        _authorize_readback(record, actor=actor, handle=handle, ctype=ctype,
                            readback_guard=readback_guard)
        # A crash between the receipt store and the consume can leave a complete
        # receipt with an outstanding redemption. Redeeming it here is not
        # "consuming again": it is finishing the same redemption, and the binding
        # just checked is the same binding the grant was minted for.
        _complete_outstanding_redemption(record, ticket=ticket, ctype=ctype)
        logger.info(
            f"[ScanOnboarding] replayed {ctype} receipt for {binding.get('scope')}"
            " scope"
        )
        return _result_payload(dict(record["result"]), outcome="replayed",
                               authorization_consumed=bool(
                                   record.get("authorization_consumed")),
                               receipt_state="complete")

    # Staged: the row landed, the tail did not. The original authorization must
    # still be valid — *unless it was already redeemed by this very operation*.
    # The delivered create redeems the grant as its own transaction commits
    # (``_redeem_authorization`` below is written for exactly that), so a tail
    # failure would otherwise strand a committed instance behind an
    # "authorization refused" answer that costs the operator a second scan.
    # Presenting the identical handle the record was staged with, for the
    # identical key that got us here, is what tells the two apart.
    if not _ticket_is_the_staged_one(record, ticket):
        _verify_authorization(ticket, owner=_clean(binding.get("owner_user_id")),
                              tenant=_clean(binding.get("tenant_id")),
                              channel_type=ctype)
    session = None
    if handle:
        try:
            session = require_session(handle, actor=actor, now=now)
        except ScanSessionError:
            session = None
    projection = dict(record["result"])
    outcome = "resumed"
    _finish_commit(
        key=record["key"], ticket=ticket, binding=binding, projection=projection,
        session=session, ctype=ctype, outcome=outcome, record_audit=record_audit,
        receipt_sink=receipt_sink, display_name=display_name, agent_id=agent_id,
        credentials=credentials, now_ts=now)
    logger.info(
        f"[ScanOnboarding] resumed staged {ctype} commit for"
        f" {binding.get('scope')} scope"
    )
    return _result_payload(projection, outcome=outcome,
                           authorization_consumed=_authorization_consumed(
                               record["key"]),
                           receipt_state="complete")


def _authorize_readback(
    record: Mapping[str, Any], *, actor: Actor, handle: str, ctype: str,
    readback_guard: Optional[Callable[[Readback], bool]],
) -> None:
    """Re-establish, before reading a result back, that it is still allowed.

    ``readback_guard`` is required rather than optional-defaulted to "allow":
    re-checking the *current* auth session, membership and target permission
    needs the identity service, and a middleware that forgot to wire it must not
    silently turn "the initiator may no longer see this" into an allowance.
    """
    if readback_guard is None:
        raise ScanCommitError(
            "reading a result back requires a read-back authorization check",
            code="readback_guard_missing")
    binding = dict(record["binding"] or {})
    readback = Readback(
        key=record["key"],
        handle=handle,
        provider=_clean(binding.get("provider")),
        scope=_clean(binding.get("scope")),
        purpose=_clean(binding.get("purpose")),
        target=_clean(binding.get("target")),
        channel_type=ctype,
        owner_user_id=_clean(binding.get("owner_user_id")),
        tenant_id=_clean(binding.get("tenant_id")),
        auth_session_id=_clean(binding.get("auth_session_id")),
        result=dict(record["result"]),
        committed_at=record["created_at"],
    )
    try:
        allowed = bool(readback_guard(readback))
    except Exception as error:  # noqa: BLE001 - an unevaluable check is a refusal
        logger.warning(
            "[ScanOnboarding] read-back check failed"
            f" ({type(error).__name__}); refusing the replay"
        )
        allowed = False
    if not allowed:
        raise ScanCommitError(
            "the original operation is no longer readable by this actor",
            code="readback_refused")


def _complete_outstanding_redemption(record: Mapping[str, Any], *, ticket: str,
                                     ctype: str) -> None:
    if record.get("authorization_consumed"):
        return
    binding = dict(record["binding"] or {})
    owner = _clean(binding.get("owner_user_id"))
    tenant = _clean(binding.get("tenant_id"))
    from auth import scan_authorization

    if not scan_authorization.verify(ticket, actor_user_id=owner,
                                     tenant_id=tenant, channel_type=ctype):
        return
    consumed = _redeem_authorization(ticket, owner=owner, tenant=tenant,
                                     channel_type=ctype)
    with _lock:
        live = _receipts.get(record["key"])
        if live is not None:
            live["authorization_consumed"] = bool(consumed)


def _advance_status(handle: str, *, actor: Actor, status: str,
                    now: float) -> None:
    """Move a session into ``committing``; a refusal here stops the commit.

    Nothing is swallowed on purpose: the state machine is the last guard before
    the write, and a session that cannot enter ``committing`` (swept, expired or
    already terminal) must refuse the create rather than proceed to it.
    """
    if not handle:
        raise ScanCommitError(
            "the scan session handle is required to commit", code="not_owner")
    mark_status(handle, status, actor=actor, now=now)


def _restore_status(handle: str, *, actor: Actor, status: str, error: str,
                    now: float) -> None:
    """Roll a ``committing`` session back so the operator can submit again."""
    if not handle:
        return
    with _lock:
        record = _sessions.get(handle)
        if record is None or not _record_owned_by(record, actor):
            return
        if record["status"] != STATUS_COMMITTING:
            return
        record["status"] = status
        record["error"] = str(error or "")[:500]
        record["updated_at"] = now
        record["version"] += 1


def _refusal_tag(error: BaseException) -> str:
    """A non-secret tag for a refused step: type plus service code, no message.

    An exception message from a lower layer is *usually* safe, but this module
    cannot prove it is, and the session's error text travels to the console and
    into poll payloads. The code is enough to act on.
    """
    code = _clean(getattr(error, "code", ""))
    name = type(error).__name__
    return f"{name}:{code}" if code else name


def _reserve_quota(
    reserve_quota: Optional[Callable[..., Any]], *, quota_required: bool,
    **kwargs: Any,
) -> Any:
    if reserve_quota is None:
        if quota_required:
            # Fail closed: an unwired quota meter is not permission to skip the
            # reservation, because the spec lists it as part of the commit.
            raise ScanCommitError(
                "no quota reservation is wired for this deployment",
                code="quota_not_wired")
        return None
    try:
        return reserve_quota(**kwargs)
    except Exception as error:
        if isinstance(error, ScanOnboardingError):
            raise
        raise ScanCommitError(
            "the quota reservation was refused", code="quota_refused"
        ) from error


def _release_quota_reservation(release_quota: Optional[Callable[[Any], None]],
                               reservation: Any) -> None:
    if reservation is None:
        return
    if release_quota is None:
        # A reservation exists and nothing can give it back. This is a wiring
        # gap, not a secret: say so, because the operator's next retry will be
        # refused for a quota the refusal itself did not consume.
        logger.warning(
            "[ScanOnboarding] a quota reservation was taken for a refused write"
            " but no release callable is wired; it may leak"
        )
        return
    try:
        release_quota(reservation)
    except Exception as error:  # noqa: BLE001 - the write refusal is the answer
        logger.warning(
            "[ScanOnboarding] quota release failed"
            f" ({type(error).__name__}); the reservation may leak"
        )


def _projection(
    *, key: str, binding: Mapping[str, Any], ctype: str,
    created: Any, display_name: str, agent_id: str,
    credentials: Mapping[str, Any], now_ts: float,
) -> Dict[str, Any]:
    """The non-sensitive result of one committed create.

    ``connected`` is taken from the create projection only when it is an actual
    boolean: an instance row existing says nothing about whether a vendor
    connection is up, and the two must never be conflated.
    """
    created_map = created if isinstance(created, Mapping) else {}
    instance_id = _clean(created_map.get("id") or created_map.get("instance_id"))
    connected = created_map.get("connected")
    if not isinstance(connected, bool):
        connected = None
    if not instance_id:
        logger.warning(
            f"[ScanOnboarding] {ctype} create returned no instance id; the"
            " receipt still prevents a duplicate"
        )
    projection = {
        "receipt_key": key,
        "instance_id": instance_id,
        "channel_type": ctype,
        "display_name": _clean(created_map.get("display_name")) or display_name,
        "agent_id": _clean(created_map.get("agent_id")) or agent_id,
        "scope": _clean(binding.get("scope")),
        "provider": _clean(binding.get("provider")),
        "purpose": _clean(binding.get("purpose")),
        "target": _clean(binding.get("target")),
        "tenant_id": _clean(binding.get("tenant_id")),
        "owner_user_id": _clean(binding.get("owner_user_id")),
        "saved": True,
        "connected": connected,
        "secret_present": _credential_secret_present(credentials),
        "committed_at": now_ts,
    }
    _assert_values_absent(projection, _secret_values(credentials),
                          label="the receipt field", code="secret_in_receipt")
    # The declared fields are the contract; a hint-matching name that is not one
    # of them is refused rather than dropped.
    return _project_receipt_result(projection)


def _stage_receipt(*, key: str, ticket: str, binding: Mapping[str, Any],
                   projection: Mapping[str, Any], now_ts: float) -> None:
    """Journal the committed write before any failure-prone tail step.

    Recorded even when an external sink will own the durable receipt: this is the
    process's own evidence that the row already exists, and it is what makes the
    same-key retry resume instead of duplicating. Calling it twice for one key
    (once with the facts the module already trusts, once with the sanitized
    projection) keeps the original 24h anchor and only upgrades the result.
    """
    with _lock:
        existing = _receipts.get(key)
        created_at = existing["created_at"] if existing else now_ts
        _receipts[key] = {
            "key": key,
            "ticket": ticket,
            "binding": dict(binding),
            "result": _project_receipt_result(projection),
            "created_at": created_at,
            "expires_at": created_at + RECEIPT_TTL_SECONDS,
            "receipt_state": "staged",
            "authorization_consumed": bool(
                (existing or {}).get("authorization_consumed")),
        }
        _receipts_by_ticket[ticket] = key


def _journal_projection(*, key: str, ctype: str, created: Any) -> Dict[str, Any]:
    """The facts the module can trust without interpreting the create's answer.

    Written the instant the row exists, so that *any* later failure — including
    one in the projection's own leak check — is resumable by the same key. It
    carries only the instance id and the channel type, so it cannot carry a
    secret even if the create returned something odd.
    """
    created_map = created if isinstance(created, Mapping) else {}
    return {
        "receipt_key": key,
        "instance_id": _clean(
            created_map.get("id") or created_map.get("instance_id")),
        "channel_type": ctype,
        "saved": True,
    }


def _finish_commit(
    *, key: str, ticket: str, binding: Mapping[str, Any],
    projection: Mapping[str, Any], session: Optional[Session], ctype: str,
    outcome: str, record_audit: Optional[Callable[..., Any]],
    receipt_sink: Optional[Callable[[str, Mapping[str, Any]], Any]],
    display_name: str, agent_id: str, credentials: Mapping[str, Any],
    now_ts: float,
) -> None:
    """Audit, store the receipt, then redeem the authorization — in that order."""
    _record_scan_audit(
        record_audit, binding=binding, ctype=ctype,
        instance_id=_clean(projection.get("instance_id")),
        display_name=display_name, agent_id=agent_id, credentials=credentials,
        outcome=outcome)
    _store_complete_receipt(key=key, ticket=ticket, binding=binding,
                            projection=projection, receipt_sink=receipt_sink)
    if session is not None and not session.terminal:
        try:
            mark_status(session.handle, STATUS_COMMITTED, actor=Actor(
                user_id=session.owner_user_id, tenant_id=session.tenant_id,
                auth_session_id=session.auth_session_id), now=now_ts)
        except ScanSessionError:
            # The session may have been swept or already committed; the receipt
            # is the durable answer either way.
            pass
    consumed = _redeem_authorization(
        ticket, owner=_clean(binding.get("owner_user_id")),
        tenant=_clean(binding.get("tenant_id")), channel_type=ctype)
    with _lock:
        record = _receipts.get(key)
        if record is not None:
            record["authorization_consumed"] = bool(consumed)


def _store_complete_receipt(
    *, key: str, ticket: str, binding: Mapping[str, Any],
    projection: Mapping[str, Any],
    receipt_sink: Optional[Callable[[str, Mapping[str, Any]], Any]],
) -> None:
    if receipt_sink is None:
        store_receipt(key, projection, binding=binding, ticket=ticket)
        return
    try:
        # A sink owns durability; it is expected to be keyed by the receipt key
        # and therefore idempotent, because a resumed commit calls it again.
        receipt_sink(key, dict(projection))
    except Exception as error:
        if isinstance(error, ScanOnboardingError):
            raise
        logger.error(
            "[ScanOnboarding] receipt sink failed"
            f" ({type(error).__name__}); the commit is staged and resumable"
        )
        raise ScanReceiptError(
            "the receipt could not be stored; retry the same request",
            code="receipt_store_failed") from error
    # The sink accepted it, so this process's own view of the operation must stop
    # saying "staged": otherwise every later call would take the resume path and
    # re-run the tail instead of answering the receipt that now exists.
    with _lock:
        record = _receipts.get(key)
        if record is not None:
            record["receipt_state"] = "complete"


def _record_scan_audit(
    record_audit: Optional[Callable[..., Any]], *, binding: Mapping[str, Any],
    ctype: str, instance_id: str, display_name: str, agent_id: str,
    credentials: Mapping[str, Any], outcome: str,
) -> None:
    if record_audit is None:
        return
    changes = {
        "provider": _clean(binding.get("provider")),
        "scope": _clean(binding.get("scope")),
        "purpose": _clean(binding.get("purpose")),
        "channel_type": ctype,
        "display_name": display_name,
        "agent_id": agent_id,
        "secret_present": _credential_secret_present(credentials),
        "outcome": outcome,
    }
    try:
        record_audit(
            actor_user_id=_clean(binding.get("owner_user_id")),
            tenant_id=_clean(binding.get("tenant_id")),
            action="channel.scan.bind",
            target=f"channel_instance:{instance_id}" if instance_id else "channel_scan",
            changes=changes,
        )
    except Exception as error:
        if isinstance(error, ScanOnboardingError):
            raise
        logger.error(
            "[ScanOnboarding] scan binding audit failed"
            f" ({type(error).__name__}); the commit is staged and resumable"
        )
        raise ScanCommitError(
            "the scan binding could not be audited; retry the same request",
            code="audit_failed") from error


def _authorization_consumed(key: str) -> bool:
    with _lock:
        record = _receipts.get(key)
        return bool(record.get("authorization_consumed")) if record else False


def _result_payload(projection: Mapping[str, Any], *, outcome: str,
                    authorization_consumed: bool,
                    receipt_state: str) -> Dict[str, Any]:
    payload = dict(projection)
    payload.update({
        "status": "success",
        "outcome": outcome,
        "receipt_state": receipt_state,
        "authorization_consumed": bool(authorization_consumed),
    })
    return payload


def _reset() -> None:
    """Drop every session and receipt. Tests only.

    The process owns no durable state here (the identity service owns the
    instances, ``auth.scan_authorization`` owns the grants), so there is nothing
    to reconcile — but this deliberately does *not* touch the grants: a test that
    wants them cleared calls their own reset, and a reset of this module can
    therefore never make a redeemed authorization usable again.
    """
    with _lock:
        _sessions.clear()
        _receipts.clear()
        _receipts_by_ticket.clear()
