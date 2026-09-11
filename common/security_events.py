# encoding:utf-8
"""Observable recording of fail-closed security refusals.

The execution-authorization and isolation gates refuse to run when the caller's
identity cannot be resolved (see ``agent/permission/isolation.py`` and
``AgentStreamExecutor._resource_tool_denial``). A refusal that is only a log
line is invisible to operations: a deployment-wide identity outage looks like
"the tools stopped working", not like a security event.

This module gives those refusals the two things the audit capability needs:

* an in-process, queryable counter per refusal kind (``counters()``), so a
  regression that loses the identity context is visible as a metric;
* a best-effort, sanitized audit record, reusing :mod:`auth.audit` so the
  redaction rules stay in exactly one place.

Recording NEVER raises and NEVER changes the decision. A broken audit sink must
not turn a refusal into an allow, so every failure here is swallowed and logged;
the caller's refusal stands either way.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("security_events")

#: Longest reason fragment copied into an audit record. Reasons embed paths and
#: tool names, which are useful for diagnosis but must stay bounded.
_MAX_REASON = 200

_lock = threading.Lock()
_counters: Dict[str, int] = {}


def record_denial(
    kind: str,
    *,
    reason: str,
    action: Optional[str] = None,
    target: Optional[str] = None,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    """Count and best-effort audit one refusal. Returns None, never raises.

    ``kind`` is a short stable bucket name (``isolation``, ``tool-grant``,
    ``identity``) used for the counter; ``reason`` is the operator-facing
    explanation. Nothing here may contain a credential: the audit payload is
    built through :func:`auth.audit.audit_event`, which sanitizes by key.
    """
    try:
        with _lock:
            _counters[kind] = _counters.get(kind, 0) + 1
    except Exception:  # pragma: no cover - a counter must never break a refusal
        pass

    try:
        logger.warning(
            "[security] fail-closed refusal kind=%s target=%s tenant=%s reason=%s",
            kind, target or "-", tenant_id or "-", reason,
        )
    except Exception:  # pragma: no cover
        pass

    try:
        _persist(kind, reason, action, target, user_id, tenant_id)
    except Exception as error:  # pragma: no cover - sink is best effort
        try:
            logger.warning("[security] audit sink unavailable for %s: %s", kind, error)
        except Exception:
            pass


def _persist(
    kind: str,
    reason: str,
    action: Optional[str],
    target: Optional[str],
    user_id: Optional[str],
    tenant_id: Optional[str],
) -> None:
    """Write a sanitized ``denied`` audit event when an identity DB is configured."""
    from auth.service import identity_db_path

    path = identity_db_path()
    if not path:
        return
    from auth.audit import AuditStore

    AuditStore(path).record(
        actor_user_id=user_id,
        tenant_id=tenant_id,
        target_tenant_id=tenant_id,
        action=action or f"execution.{kind}.denied",
        target=target or "n/a",
        redacted_changes={"reason": (reason or "")[:_MAX_REASON]},
        result="denied",
    )


def counters() -> Dict[str, int]:
    """Snapshot of refusal counts by kind (for tests and ops diagnostics)."""
    with _lock:
        return dict(_counters)


def reset_counters() -> None:
    """Clear the counters. Intended for tests."""
    with _lock:
        _counters.clear()


def snapshot() -> Dict[str, Any]:
    """Counters plus their total, for a metrics endpoint to expose."""
    current = counters()
    return {"counters": current, "total": sum(current.values())}
