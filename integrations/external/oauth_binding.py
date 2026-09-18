# encoding:utf-8
"""What an OAuth authorization is bound to, and how a callback re-proves it.

Why this is its own module
--------------------------
The existing MCP OAuth chain (``agent/tools/mcp/mcp_oauth.py``) already had the
two things a flow needs to be safe against *replay*: a cryptographically random
``state`` and a single-use ``pop`` of the pending record. What it did not have is
any record of *who and what* the authorization was started for, so a callback
could only be answered with "the state was live", never with "the request that
completes is the request that started".

The spec asks for the second answer (``mcp-connection-integration``):

    OAuth 授权、回调、令牌更新与撤销 MUST 绑定发起主体、连接、租户或平台范围及配置
    版本，并验证一次性状态。

    #### Scenario: 回调时已切换租户
    - **WHEN** OAuth 回调与发起时的主体、范围或连接版本不再匹配
    - **THEN** 授权结果不绑定到当前连接，也不改变新租户配置

The callback cannot be authenticated — it is a browser redirect from a remote
authorization server — so the binding cannot be "compare with the current
session". It has to be re-proved against the *store*: the connection must still
be the same connection, at the same version, in the same scope, for a tenant the
initiating subject is still an active member of. That is what
:func:`verify_callback` answers, and putting it here rather than inline in the
HTTP handler is what makes it testable without a browser, a server, or a real
token endpoint.

What this module deliberately does not do
-----------------------------------------
It does not touch tokens, does not talk to the network and does not decide
whether OAuth is the right mechanism. It answers exactly one question — "is the
completion of this flow still the one that was started?" — and the caller
decides what to do with a "no".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: The fields a binding must carry to be checkable. Missing any of them means
#: the flow was started by code that could not bind it, and an unbound flow is
#: refused rather than accepted: "no proof" must not read as "everything
#: matched", which is the failure mode this whole module exists to close.
REQUIRED_FIELDS = (
    "actor_user_id", "scope", "connection_id", "config_version",
    # Required except for a platform-scope authorization, which has no tenant:
    # see :func:`is_bound`.
    "tenant_id",
)


def binding(*, actor_user_id: str, tenant_id: str, scope: str,
            connection_id: str, config_version: int) -> Dict[str, Any]:
    """The record a pending authorization carries from initiate to callback."""
    return {
        "actor_user_id": str(actor_user_id or ""),
        "tenant_id": str(tenant_id or ""),
        "scope": str(scope or ""),
        "connection_id": str(connection_id or ""),
        "config_version": int(config_version or 0),
    }


def is_bound(record: Optional[Mapping[str, Any]]) -> bool:
    """Whether ``record`` carries enough to be verified later.

    A platform-scope authorization has no tenant — the platform owns the
    connection — so ``tenant_id`` is required only where a tenant is the thing
    being bound. Demanding it unconditionally would have made every platform
    binding unverifiable, which reads as "refused" everywhere and would have
    hidden the platform branch as dead code.
    """
    if not record:
        return False
    try:
        if not str(record.get("actor_user_id", "")).strip():
            return False
        scope = str(record.get("scope", "")).strip()
        if not scope:
            return False
        if not str(record.get("connection_id", "")).strip():
            return False
        if int(record.get("config_version", 0) or 0) <= 0:
            return False
        if scope != "platform" and not str(record.get("tenant_id", "")).strip():
            return False
    except (TypeError, ValueError):
        return False
    return True


def verify_callback(record: Optional[Mapping[str, Any]], *,
                    service: Any = None, identity: Any = None
                    ) -> Tuple[bool, str]:
    """Whether the flow this callback completes is still the one that started.

    Returns ``(ok, reason)``. The reason is logged and never rendered: it names
    which check failed, which is operational information, and the browser is
    only ever told that the authorization was refused.

    The checks, and why each is here rather than at the call site:

    * **Bound at all.** A pending record with no binding cannot be checked, and
      an unverifiable flow is refused (see :data:`REQUIRED_FIELDS`).
    * **Still the same connection.** The row is re-read now, so an id that was
      deleted, or whose scope moved, is refused. This is the 授权结果不绑定到当前
      连接 half of the scenario: the result is bound to nothing rather than to
      whatever now answers that id.
    * **Still the same tenant and scope.** The scenario's literal case
      (回调时已切换租户). A completion carrying another tenant is refused, and a
      token issued for a tenant-owned connection is not accepted for one that
      has since become a platform template, or the other way round — the two
      have different owners and different secret stores.
    * **Still enabled.** A connection disabled while the user was consenting
      must not come back online by completing the flow; that would undo the
      console's disable without the permission to do so. Checked before the
      version because every edit bumps the version, and "it was switched off" is
      the more useful answer for a disable.
    * **Still the same version.** The connection was edited between the
      authorization URL and the redirect, so a token minted against the old
      configuration must not attach to the new one (配置版本).
    * **Subject still valid for the scope.** A tenant member who was removed, or
      a platform administrator who was demoted, cannot complete a flow they
      started with authority they no longer hold.
    """
    if not is_bound(record):
        return False, "the authorization was not bound to a subject and connection"
    connection_id = str(record["connection_id"])
    tenant_id = str(record["tenant_id"])
    scope = str(record["scope"])
    version = int(record["config_version"])
    actor = str(record["actor_user_id"])

    service = service or _service()
    identity = identity or getattr(service, "_identity", None)
    if service is None or identity is None:
        return False, "the connection store is unavailable"

    row = _connection_row(service, connection_id)
    if row is None:
        return False, "the connection no longer exists"
    if str(row["scope"]) != scope:
        return False, "the connection changed scope"
    if scope != "platform" and str(row["tenant_id"] or "") != tenant_id:
        return False, "the connection changed tenant"
    if not int(row["enabled"]):
        return False, "the connection was disabled after the authorization started"
    if int(row["version"]) != version:
        return False, "the connection was modified after the authorization started"

    if scope == "platform":
        try:
            if not identity.is_platform_admin_user(actor):
                return False, "the authorizing administrator is no longer active"
        except Exception:  # noqa: BLE001 - unreadable is not a proof
            return False, "the authorizing administrator could not be verified"
    else:
        try:
            if not identity.is_member(actor, tenant_id):
                return False, "the authorizing member is no longer active"
        except Exception:  # noqa: BLE001
            return False, "the authorizing member could not be verified"
    return True, ""


def _service():
    try:
        from integrations.external.service import (
            get_external_connection_service)
        return get_external_connection_service()
    except Exception:  # noqa: BLE001 - no store, no proof
        logger.debug("[oauth-binding] connection service unavailable")
        return None


def _connection_row(service: Any, connection_id: str):
    """The row as it stands now, or ``None``.

    Read through the service's own store rather than through ``snapshot``: a
    snapshot resolves inheritance and reports *effective* availability, and this
    check is about the row the authorization was started against. Conflating the
    two would let a platform template's version stand in for a tenant override's.
    """
    try:
        rows = service._store.execute(  # noqa: SLF001 - same layer
            "SELECT * FROM external_connections WHERE id=? AND deleted_at IS NULL",
            (connection_id,))
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None
