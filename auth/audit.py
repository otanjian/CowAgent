# encoding:utf-8
"""Append-only identity audit stored in the same ``identity.db`` transaction.

Every identity change is committed together with its audit event, so there is
no distinct "did the audit land?" window. Secrets (passwords, hashes, tokens)
are stripped before storage via ``sanitize_payload``. Denied requests also
record a sanitized ``denied`` event. This is the single audit source of truth
for identity management in this change; it is *not* a general business audit and
uses no outbox / cross-service delivery.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional

from auth.store import IdentityStore

#: Fields that must never be persisted. Recursively dropped.
_SECRET_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "credential",
}


class AuditError(RuntimeError):
    """Raised when an audit event cannot be recorded."""


def sanitize_payload(payload: Any) -> Any:
    """Return a deep copy of ``payload`` with secret fields removed."""
    if isinstance(payload, dict):
        return {
            k: sanitize_payload(v)
            for k, v in payload.items()
            if str(k).lower() not in _SECRET_KEYS and _looks_safe(k)
        }
    if isinstance(payload, (list, tuple)):
        return [sanitize_payload(v) for v in payload]
    if isinstance(payload, str):
        return _truncate(payload)
    return payload


def _looks_safe(key: str) -> bool:
    low = str(key).lower()
    return not any(tok in low for tok in ("password", "secret", "token", "api_key"))


def _truncate(value: str, limit: int = 512) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def audit_event(
    actor_username: Optional[str],
    tenant_id: Optional[str],
    target_tenant_id: Optional[str],
    action: str,
    target: str,
    redacted_changes: Dict[str, Any],
    result: str = "success",
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a sanitized audit event dict (not yet persisted)."""
    return {
        "id": str(uuid.uuid4()),
        "time": int(time.time()),
        "actor_user_id": actor_user_id,
        "actor_username": actor_username,
        "tenant_id": tenant_id,
        "target_tenant_id": target_tenant_id,
        "action": action,
        "target": target,
        "redacted_changes": json.dumps(sanitize_payload(redacted_changes), ensure_ascii=False),
        "result": result,
    }


def denied_event(
    actor_username: Optional[str],
    tenant_id: Optional[str],
    action: str,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a sanitized 'denied' audit event for an authorization rejection."""
    return audit_event(
        actor_username=actor_username,
        tenant_id=tenant_id,
        target_tenant_id=tenant_id,
        action=action,
        target=target or "n/a",
        redacted_changes={},
        result="denied",
    )


#: Fields that may be filtered on in ``query_tenant`` (safe, non-secret).
QUERYABLE_FIELDS = ("tenant_id", "target_tenant_id", "action", "result")


class AuditStore:
    """Append-only audit events, scoped per tenant for authorized queries."""

    def __init__(self, db_path: str):
        self._store = IdentityStore(db_path)

    def record(
        self,
        *,
        actor_username: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        target_tenant_id: Optional[str] = None,
        action: str,
        target: str,
        redacted_changes: Optional[Dict[str, Any]] = None,
        result: str = "success",
        con=None,
    ) -> Dict[str, Any]:
        """Record an audit event, optionally within an existing transaction.

        When ``con`` is supplied the event is inserted on that connection so the
        caller can commit it atomically with the identity change. Otherwise a
        fresh connection is used and committed here.
        """
        evt = audit_event(
            actor_username=actor_username,
            actor_user_id=actor_user_id,
            tenant_id=tenant_id,
            target_tenant_id=target_tenant_id,
            action=action,
            target=target,
            redacted_changes=redacted_changes or {},
            result=result,
        )
        sql = (
            "INSERT INTO audit_events"
            " (id, time, actor_user_id, actor_username, tenant_id,"
            "  target_tenant_id, action, target, redacted_changes, result)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
        )
        params = (
            evt["id"], evt["time"], evt["actor_user_id"], evt["actor_username"],
            evt["tenant_id"], evt["target_tenant_id"], evt["action"], evt["target"],
            evt["redacted_changes"], evt["result"],
        )
        if con is not None:
            con.execute(sql, params)
        else:
            with self._store.connect() as c:
                c.execute(sql, params)
                c.commit()
        return evt

    def query_tenant(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Return sanitized events for one tenant, newest first."""
        rows = self._store.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY time DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [dict(r) for r in rows]

    def query_platform(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return platform-scoped events (tenant_id is NULL) newest first."""
        rows = self._store.execute(
            "SELECT * FROM audit_events WHERE tenant_id IS NULL ORDER BY time DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
