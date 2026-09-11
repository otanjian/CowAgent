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
    """Whether this instance runs the database identity mode.

    Kept for callers outside ``web_channel``; the workspace seam itself takes
    the answer as an argument so the caller's own notion of the mode stays the
    single one in play (a test may stub either one).
    """
    from config import conf
    return str(conf().get("identity_mode", "legacy") or "legacy") == "database"


def _refuse(message: str, code: str = "") -> "web.HTTPError":
    payload = {"status": "error", "message": message}
    if code:
        payload["code"] = code
    return web.HTTPError(
        "403 Forbidden", {"Content-Type": "application/json"}, json.dumps(payload)
    )


def resolve_tenant_workspace_root(*, database_mode: bool) -> Optional[str]:
    """The caller's tenant shared root, when the tenant dimension applies.

    ``database_mode`` is passed in by the caller rather than re-read here, so
    the request's mode is decided once, at the edge, and this seam only decides
    what that mode *means* for the working root.

    Read-only: it resolves the ambient ``RuntimeIdentity`` and the tenant's
    configured root, and raises rather than inventing a fallback (task 4.5/D3).
    """
    from common.runtime_identity import current_identity

    identity = current_identity()
    if identity.tenant_id:
        from auth.service import get_identity_service

        root = get_identity_service().tenant_shared_root(identity.tenant_id)
        if root:
            return root
        # A tenant without a configured root has nowhere to work; falling back
        # to the global default would place one tenant's files in another
        # tenant's workspace, so it is refused.
        logger.warning(
            "[WebChannel] tenant %s has no shared root; refusing workspace resolution",
            identity.tenant_id,
        )
        raise _refuse("tenant has no shared root")

    if database_mode:
        # No tenant in scope: refuse rather than fall back to the process-global
        # default Agent's workspace, which may belong to another tenant.
        raise _refuse("tenant scope required", code="missing_tenant")

    return None
