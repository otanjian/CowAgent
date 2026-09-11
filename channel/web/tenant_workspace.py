"""Fork seam: the tenant scope of a request's working root (tasks 8.1/8.3).

``channel/web/web_channel.py::_get_workspace_root`` is upstream's function: it
resolves a session's project directory, then the Agent's workspace, and that is
all upstream needs. The fork adds a third case — in database mode the root must
come from the caller's *tenant*, and when no tenant is in scope the request is
refused instead of silently falling back to a process-global workspace that may
belong to somebody else.

That fork case used to sit inline in the middle of upstream's function, which
made every upstream edit to it a conflict. It lives here instead, behind one
call. The contract is deliberately three-valued so the caller stays upstream
shaped:

* a string — use this root (database mode, tenant resolved);
* ``None`` — no tenant dimension applies; carry on with upstream's own
  fallback (legacy mode, or database mode where the caller will be refused by
  the identity layer anyway);
* an ``HTTPError`` raised — this must not fall back (database mode with no
  tenant in scope, or a tenant without a trusted shared root).

Keeping the refusal *here* is the point: the decision "refuse rather than fall
back globally" is a fork invariant, and it must not be expressed as a branch the
fork has to re-add after every upstream merge.
"""
from __future__ import annotations

import json
from typing import Optional

import web

from common.log import logger


def is_database_identity() -> bool:
    """Database is the only identity mode after retire-legacy-identity-mode."""
    return True


def _refuse(message: str, code: str = "") -> "web.HTTPError":
    payload = {"status": "error", "message": message}
    if code:
        payload["code"] = code
    return web.HTTPError(
        "403 Forbidden", {"Content-Type": "application/json"}, json.dumps(payload)
    )


def resolve_tenant_workspace_root(*, database_mode: bool = True) -> Optional[str]:
    """The caller's tenant shared root (database identity only).

    Raises rather than inventing a process-global fallback. ``database_mode``
    is retained for call-site compatibility but ignored — database is the only
    mode.
    """
    from common.runtime_identity import current_identity

    identity = current_identity()
    if identity.tenant_id:
        from auth.service import get_identity_service

        root = get_identity_service().tenant_shared_root(identity.tenant_id)
        if root:
            return root
        logger.warning(
            "[WebChannel] tenant %s has no shared root; refusing workspace resolution",
            identity.tenant_id,
        )
        raise _refuse("tenant has no shared root")

    raise _refuse("tenant scope required", code="missing_tenant")
