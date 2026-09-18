# encoding:utf-8
"""Tenant-scoped read access to the OA connection rows.

The tool provider needs the tenant's OA connection without going through the
console's authorization path (the runtime re-authorizes every call anyway, and
the provider is only a listing). This module is the read-only projection, and
it is deliberately the only place in the OA package that touches the store.

A blank tenant is a refusal rather than an ambient default: the same rule
``adapters/erp_scene.py`` enforces, because "the process tenant" is exactly how
a listing ends up showing another tenant's connection.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from integrations.external import registry


class OaStoreError(RuntimeError):
    """A refusal with a stable code, so a caller can render the reason."""

    def __init__(self, message: str, *, code: str = "oa_store_unavailable") -> None:
        super().__init__(message)
        self.code = str(code)


def _require_tenant(tenant_id: Optional[str]) -> str:
    value = str(tenant_id or "").strip()
    if not value:
        raise OaStoreError("a tenant is required to resolve an OA connection",
                           code="missing_tenant")
    return value


def _service():
    from integrations.external.service import get_external_connection_service
    try:
        return get_external_connection_service()
    except Exception as error:  # noqa: BLE001 - an unreadable store is a refusal
        raise OaStoreError("the OA connection store is unavailable",
                           code="oa_store_unavailable") from error


def parse_config(row: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        loaded = json.loads(row["config_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def list_oa_connections(tenant_id: str, *,
                        enabled_only: bool = True) -> List[Mapping[str, Any]]:
    """The tenant's OA rows (at most one, by the singleton constraint)."""
    tenant = _require_tenant(tenant_id)
    service = _service()
    sql = ("SELECT * FROM external_connections WHERE kind='oa'"
           " AND scope=? AND tenant_id=? AND deleted_at IS NULL")
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY name, id"
    return list(service._store.execute(  # noqa: SLF001 - same layer as runtime
        sql, (registry.SCOPE_TENANT, tenant)))
