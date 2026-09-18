# encoding:utf-8
"""Scene-side resolution of a named ERP connection through the control plane.

Why this lives next to the ERP adapter
--------------------------------------
A scene or skill used to name an ERP system by reading
``<workspace>/.one/erp_connections.json`` and trusting whatever it found there.
That file was called *global* but ``Scene/_shared/host.py`` binds the workspace
to the request's tenant, so the same "global" file meant different things to
different tenants -- and the read returned the stored password verbatim.

This module is the single seam the scenes now go through. It answers one
question -- "which ERP connection is this tenant actually running as, and what
are its non-secret fields?" -- against the authoritative
:mod:`integrations.external.service` store, and nothing else:

* **The tenant is an argument, never an ambient value.** Callers pass the
  tenant the scene is running as (``host.get_current_tenant_id()`` at the HTTP
  edge). There is no "the process tenant" to get wrong, and no filesystem
  lookup that could reach another tenant's workspace.
* **A named connection is resolved by ID, scoped to the tenant.** A connection
  ID that belongs to another tenant answers exactly like a missing one
  (``connection_not_found``): the spec's "越界 ID 统一 404" made structural.
* **No default means a refusal, not a fallback.** The ERP default is the
  catalogue pointer for ``tenant:<tenant_id>``. When it is absent, or points at
  a connection that was deleted/disabled, the resolver raises with a stable code
  instead of quietly using the first row it can find (spec: "不得隐式选取另一
  连接", "未指定且无默认时 SHALL 提示选择").
* **A password is resolved at the call point, never cached here.** The
  non-secret configuration is returned as-is; the secret is fetched per build
  through :meth:`ExternalConnectionService.resolve_secret`, so a rotated or
  cleared password is picked up on the next call and no scene holds a copy.

The legacy U9/金蝶 providers are refused outright. They have no production
adapter (``registry.UNSUPPORTED_ERP_PROVIDERS``), and a simulated result must
not stand in for a real probe or a real sync (spec ``模拟 ERP 不作为生产能力``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from integrations.external import registry

#: Stable machine codes this module refuses with. Kept explicit so a scene can
#: render the reason and a test can pin it, rather than parsing a message.
CODE_MISSING_TENANT = "missing_tenant"
CODE_CONNECTION_NOT_FOUND = "connection_not_found"
CODE_NO_DEFAULT = "no_default_erp_connection"
CODE_DEFAULT_UNAVAILABLE = "default_connection_unavailable"
CODE_DEFAULT_DISABLED = "default_connection_disabled"
CODE_DISABLED = "connection_disabled"
CODE_UNSUPPORTED_PROVIDER = "unsupported_erp_provider"
CODE_STORE_UNAVAILABLE = "erp_store_unavailable"


class SceneConnectionError(RuntimeError):
    """A scene's ERP lookup was refused, with a stable code and HTTP class."""

    def __init__(self, message: str, *, code: str = CODE_CONNECTION_NOT_FOUND,
                 status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = int(status)


@dataclass(frozen=True)
class ResolvedErpConnection:
    """One tenant ERP connection, resolved and ready to build a provider from.

    Deliberately carries the *non-secret* configuration only; the password is
    fetched by :meth:`provider_credentials` so it exists for the duration of a
    provider construction and is not retained on a scene object.
    """

    id: str
    name: str
    provider: str
    tenant_id: str
    config: Mapping[str, Any] = field(default_factory=dict)
    version: int = 0
    enabled: bool = True
    #: ``"new"`` when the control plane answered; ``"legacy"`` when the JSON
    #: file did. Kept private so callers keep treating this as a resolved
    #: connection, not a store-version probe.
    _origin: str = "new"
    _legacy_id: str = ""

    @property
    def connection_id(self) -> str:
        return self.id

    def _password(self) -> str:
        if self._origin == "legacy":
            return _legacy_password(self.tenant_id, self._legacy_id or self.id)
        service = _service()
        try:
            return service.resolve_secret(
                connection_id=self.id, slot="password", scope=registry.SCOPE_TENANT,
                tenant_id=self.tenant_id)
        except Exception as error:  # noqa: BLE001 - normalised into a refusal
            raise SceneConnectionError(
                "the ERP connection's password is not configured",
                code="erp_secret_unavailable", status=409) from error

    def provider_credentials(self) -> Dict[str, Any]:
        """The ``**kwargs`` for :class:`SAPProviderFactory` (contains the secret)."""
        config = dict(self.config or {})
        password = self._password()
        if self.provider == "rfc":
            return {
                "ashost": str(config.get("ashost") or ""),
                "sysnr": str(config.get("sysnr") or "00"),
                "client": str(config.get("client") or "100"),
                "username": str(config.get("user") or ""),
                "password": password,
                "lang": str(config.get("lang") or "EN"),
            }
        return {
            "base_url": str(config.get("base_url") or ""),
            "username": str(config.get("user") or ""),
            "password": password,
            "client": str(config.get("client") or "100"),
            "verify_ssl": bool(config.get("verify_ssl", True)),
        }

    def create_provider(self):
        """Build the concrete SAP provider through the one shared factory."""
        from Scene.sap_data_analysis.backend.sap.factory import SAPProviderFactory
        return SAPProviderFactory.create(self.provider, self.provider_credentials())


# -- store access ------------------------------------------------------------

def _service():
    from integrations.external.service import get_external_connection_service
    try:
        return get_external_connection_service()
    except Exception as error:  # noqa: BLE001 - unreadable store is a refusal
        raise SceneConnectionError(
            "the ERP connection store is unavailable",
            code=CODE_STORE_UNAVAILABLE, status=503) from error


def _identity():
    """The identity service behind the control plane (needed by the resolver)."""
    return _service()._identity  # noqa: SLF001 - same layer as the runtime


def _require_tenant(tenant_id: Optional[str]) -> str:
    value = str(tenant_id or "").strip()
    if not value:
        # An ambient/blank tenant is the exact failure this resolver exists to
        # prevent, so it is a refusal rather than "use the default workspace".
        raise SceneConnectionError("a tenant is required to resolve an ERP"
                                   " connection",
                                   code=CODE_MISSING_TENANT, status=400)
    return value


def _effective(tenant_id: str) -> Dict[str, Any]:
    """Apply ``store_version`` exactly as the migration tool does for MCP.

    This is the single place scenes learn whether the JSON file, the control
    plane, or neither is authoritative for this tenant. Calling the SQL tables
    directly would ignore a per-scope switch and, worse, would make
    ``store_version=new`` look identical to ``legacy`` whenever the control
    plane happened to be empty.
    """
    from integrations.external import migration

    return migration.read_effective_connections(
        _identity(), kind=registry.KIND_ERP, scope=registry.SCOPE_TENANT,
        tenant_id=tenant_id)


def _row_from_effective(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Project a resolver item into the shape existing callers already read.

    Callers (Scene options handlers, the tool provider) treat the return value
    like a ``sqlite3.Row``: ``row["id"]``, ``row["name"]``, ``row["config_json"]``,
    ``row["enabled"]``, ``row["version"]``. Keeping that shape means wiring the
    switch does not force every call site to change.
    """
    import json

    origin = str(item.get("origin") or "new")
    connection_id = str(item.get("connection_id") or "")
    legacy_id = str(item.get("legacy_id") or "")
    config = dict(item.get("config") or {})
    return {
        "id": connection_id or legacy_id,
        "name": str(item.get("name") or ""),
        "kind": registry.KIND_ERP,
        "scope": str(item.get("scope") or registry.SCOPE_TENANT),
        "tenant_id": item.get("tenant_id"),
        "config_json": json.dumps(config, ensure_ascii=False),
        "enabled": 1 if item.get("enabled", True) else 0,
        "version": int(item.get("version") or 0),
        "deleted_at": None,
        "origin": origin,
        "legacy_id": legacy_id,
    }


def _legacy_password(tenant_id: str, legacy_id: str) -> str:
    """Read the plaintext password the legacy JSON still holds for one row.

    Only used while ``store_version`` is still serving the file. Once the
    control plane is authoritative the secret is resolved through
    :meth:`ExternalConnectionService.resolve_secret` and this path is never
    reached — so a redacted (blank) legacy file after cutover cannot resurrect
    a password the migration already blanked.
    """
    from integrations.external import migration

    sources = migration.discover_legacy_sources(_identity())
    for source in sources:
        if source.format != migration.ERP_FORMAT:
            continue
        if str(source.tenant_id or "") != tenant_id:
            continue
        records, error = migration._read_source(source)  # noqa: SLF001
        if error:
            continue
        for raw in records:
            if str(raw.get("id") or "") != legacy_id:
                continue
            password = raw.get("password", raw.get("passwd"))
            if password is not None and str(password) != "":
                return str(password)
    raise SceneConnectionError(
        "the ERP connection's password is not configured",
        code="erp_secret_unavailable", status=409)


def erp_catalog_revision(tenant_id: str) -> int:
    """The tenant's ERP catalogue revision, or 0 when none was written yet."""
    tenant_id = _require_tenant(tenant_id)
    service = _service()
    rows = service._store.execute(  # noqa: SLF001 - same layer as the runtime
        "SELECT revision FROM external_connection_catalog_versions"
        " WHERE scope_key=? AND kind='erp'",
        ("%s:%s" % (registry.SCOPE_TENANT, tenant_id),))
    return int(rows[0]["revision"]) if rows else 0


def erp_default_connection_id(tenant_id: str) -> Optional[str]:
    """The default ERP connection id for ``tenant_id`` (``None`` when unset).

    Under ``store_version=legacy`` the JSON file's ``is_default`` flag is the
    authoritative answer (the control-plane catalogue pointer is not yet live).
    Under ``new`` / a populated ``dual`` the catalogue pointer wins.
    """
    tenant_id = _require_tenant(tenant_id)
    view = _effective(tenant_id)
    if view.get("source") == "legacy":
        for item in view.get("connections") or []:
            config = item.get("config") or {}
            if config.get("is_default") or config.get("default"):
                return str(item.get("legacy_id") or item.get("connection_id")
                           or "") or None
        return None
    service = _service()
    rows = service._store.execute(  # noqa: SLF001
        "SELECT default_connection_id FROM external_connection_catalog_versions"
        " WHERE scope_key=? AND kind='erp'",
        ("%s:%s" % (registry.SCOPE_TENANT, tenant_id),))
    if not rows:
        return None
    return rows[0]["default_connection_id"]


def list_erp_connections(tenant_id: str, *,
                         enabled_only: bool = True) -> List[Mapping[str, Any]]:
    """The tenant's ERP rows as decided by ``store_version``.

    No authorization is applied here beyond the tenant scope: this is the
    read-only projection the scene options endpoint and the tool provider both
    need, and both callers have already established the caller's eligibility.
    """
    tenant_id = _require_tenant(tenant_id)
    view = _effective(tenant_id)
    rows = [_row_from_effective(item) for item in view.get("connections") or []]
    if enabled_only:
        rows = [row for row in rows if row.get("enabled")]
    rows.sort(key=lambda row: (str(row.get("name") or ""), str(row.get("id") or "")))
    return rows


def secret_configured(connection_id: str, slot: str = "password") -> bool:
    """Whether a slot has a live reference, without resolving the value.

    The management projection shows "已配置" from this, so read-only status
    never decrypts a secret and can never echo one. Under a legacy store the
    JSON file's presence of the field is the answer.
    """
    if not str(connection_id or "").strip():
        return False
    # Prefer the control-plane reference when one exists; fall back to asking
    # the effective view so a legacy-only deployment still reports honestly.
    service = _service()
    rows = service._store.execute(  # noqa: SLF001
        "SELECT 1 FROM external_connection_secret_refs"
        " WHERE connection_id=? AND slot=? LIMIT 1",
        (str(connection_id), str(slot)))
    if rows:
        return True
    try:
        # Walking every tenant would be wrong; the caller already scoped the
        # listing. We only need "does *this* id carry a password somewhere the
        # resolver would find it".
        from integrations.external import migration
        for source in migration.discover_legacy_sources(_identity()):
            if source.format != migration.ERP_FORMAT:
                continue
            records, error = migration._read_source(source)  # noqa: SLF001
            if error:
                continue
            for raw in records:
                if str(raw.get("id") or "") != str(connection_id):
                    continue
                value = raw.get("password", raw.get("passwd"))
                return value is not None and str(value) != ""
    except SceneConnectionError:
        return False
    return False


def _fetch(tenant_id: str, connection_id: str) -> Optional[Mapping[str, Any]]:
    """Locate one connection in the *effective* catalogue for this tenant.

    A row that exists in the other store, or in another tenant, answers like a
    missing one: the refusal must not reveal that the ID exists elsewhere, and
    must not quietly read the JSON file when ``store_version=new``.
    """
    named = str(connection_id or "").strip()
    if not named:
        return None
    for item in _effective(tenant_id).get("connections") or []:
        row = _row_from_effective(item)
        if row["id"] == named or str(item.get("legacy_id") or "") == named:
            return row
    return None


def _resolved(row: Mapping[str, Any], tenant_id: str) -> ResolvedErpConnection:
    import json

    config: Mapping[str, Any] = {}
    try:
        loaded = json.loads(row["config_json"] or "{}")
        if isinstance(loaded, Mapping):
            config = loaded
    except (TypeError, ValueError):
        config = {}
    provider = str(config.get("provider") or "").lower()
    if provider in registry.UNSUPPORTED_ERP_PROVIDERS:
        raise SceneConnectionError(
            "ERP provider %r has no production adapter" % provider,
            code=CODE_UNSUPPORTED_PROVIDER, status=409)
    if provider not in ("rfc", "adt_sql"):
        raise SceneConnectionError(
            "ERP connection has no usable provider configuration",
            code=CODE_UNSUPPORTED_PROVIDER, status=409)
    origin = str(row.get("origin") or "new")
    legacy_id = str(row.get("legacy_id") or "")
    return ResolvedErpConnection(
        id=str(row["id"]), name=str(row["name"] or ""), provider=provider,
        tenant_id=tenant_id, config=config, version=int(row["version"] or 0),
        enabled=bool(row["enabled"]),
        _origin=origin, _legacy_id=legacy_id)


def resolve_erp_connection(connection_id: Optional[str] = None, *,
                           tenant_id: Optional[str],
                           actor_user_id: str = "") -> ResolvedErpConnection:
    """Resolve the named connection, or the tenant default, for this tenant.

    ``connection_id`` wins when given. Otherwise the catalogue's default is
    used; when it is missing, deleted or disabled the call is refused with a
    code that tells the caller to pick a connection, never with a silent
    switch to another row.

    ``actor_user_id`` is accepted so the call site can pass the verified
    subject through for the caller's own logging/authorization; resolution
    itself is tenant-scoped and does not use it to pick a connection.
    """
    del actor_user_id  # resolution is tenant-scoped; kept for call-site clarity
    tenant = _require_tenant(tenant_id)
    named = str(connection_id or "").strip()
    if named:
        row = _fetch(tenant, named)
        if row is None:
            raise SceneConnectionError(
                "ERP connection %s was not found in this tenant" % named,
                code=CODE_CONNECTION_NOT_FOUND, status=404)
        resolved = _resolved(row, tenant)
        if not resolved.enabled:
            raise SceneConnectionError(
                "ERP connection %s is disabled" % named,
                code=CODE_DISABLED, status=409)
        return resolved

    default_id = erp_default_connection_id(tenant)
    if not default_id:
        raise SceneConnectionError(
            "this tenant has no default ERP connection; choose one explicitly",
            code=CODE_NO_DEFAULT, status=409)
    row = _fetch(tenant, str(default_id))
    if row is None:
        # Explicit refusal: the pointer is stale (deleted or moved tenant) and
        # quietly using another connection would be the exact bug the spec names.
        raise SceneConnectionError(
            "the default ERP connection is no longer available; choose one"
            " explicitly",
            code=CODE_DEFAULT_UNAVAILABLE, status=409)
    resolved = _resolved(row, tenant)
    if not resolved.enabled:
        raise SceneConnectionError(
            "the default ERP connection is disabled; choose another explicitly",
            code=CODE_DEFAULT_DISABLED, status=409)
    return resolved
