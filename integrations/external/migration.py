# encoding:utf-8
"""Legacy connection migration: preflight, idempotent import, verification.

Why this module exists
----------------------
The control plane (``integrations/external/service.py``) is the authoritative
store for MCP / ERP / OA / email connections, but a deployment that is upgrading
already has connection data in two *legacy* places:

* the file-based ERP store read by ``Scene/_shared/backend/
  ErpConnectionsHandler.py`` — ``<tenant shared root>/.one/erp_connections.json``,
  a JSON list of connection dicts whose fields are ``id`` / ``name`` /
  ``system`` / ``provider`` / ``ashost`` / ``sysnr`` / ``client`` / ``lang`` /
  ``username`` / ``password`` / ``base_url`` / ``verify_ssl`` / ``is_default``
  (see ``Scene/sap_data_analysis/backend/api.py`` for the reader that shows the
  accepted provider shapes); and
* the MCP configuration loaded by ``agent/tools/tool_manager.py`` — ``~/cow``
  style ``mcp.json`` (``mcpServers`` dict, ``mcp_servers`` list, or the raw
  dict) with a fallback to ``conf()``'s ``mcp_servers`` list. Entries carry
  ``name`` / ``type`` / ``command`` / ``args`` / ``env`` / ``url`` / ``headers``
  / ``timeout`` / ``tool_name_prefix``.

This module is the *only* thing that writes new connection rows for that legacy
data. It is deliberately not imported from any request path: the migration is an
explicit operator action (CLI ``cow external-connections import``), and the
runtime keeps reading whatever store the deployment switch names.

Three facts the specs make non-negotiable, and how they are held here
---------------------------------------------------------------------
* **Nothing is written by a check.** :func:`preflight` and :func:`verify` only
  read. They scan the legacy sources, hash each record and report what *would*
  happen; they never touch the legacy files and never create a connection.
* **Secrets never leave the process in a reportable shape.** A record's public
  projection contains slot *names* and presence, never a value; the plaintext is
  held in a private side table keyed by source hash and passed straight into
  ``ExternalConnectionService.create_connection`` (which encrypts it through the
  existing ``auth.crypto`` path). The content hash mixes a keyed HMAC of each
  secret into a plain SHA-256, so a leaked ledger cannot be brute-forced back to
  a password.
* **Import is idempotent, including under concurrency.** The unique index on
  ``external_connection_migrations(source_hash, scope_key)`` is used as a
  claim: exactly one importer may hold a record in ``pending`` and create the
  connection; every other importer (concurrent or a later retry) reads the
  winner's mapping. A retry after a partial failure reclaims ``failed`` /
  ``skipped_conflict`` rows by compare-and-swap, so it resumes rather than
  starting over. Creates additionally carry the source hash as the service's
  create-idempotency key, which closes the window between "row created" and
  "ledger updated". Migration 28 declares ``batch_id`` as the row PRIMARY KEY,
  so the claim id is derived per record (:func:`_claim_id`) and the operator's
  run batch is recorded in ``detail_json`` instead.

Store version switch (task 12.3)
--------------------------------
:func:`active_store_version` is the single resolution point the runtime reads:
``conf()["external_connections"]["store_version"]`` — ``legacy`` (default),
``dual`` or ``new`` — re-read on every call so opening a scope needs no restart.
It defaults to ``legacy`` for anything unset or unrecognised, so a deployment
that has not migrated never starts reading an empty new store. :func:`verify`
plus :func:`validate_store_version` / :func:`assert_store_version_safe` refuse
``new`` while importable legacy records are still unimported, with a machine
readable reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from config import conf

from integrations.external import registry
from integrations.external.errors import ExternalConnectionError
from integrations.external.service import reusable_connection_id

# -- store version vocabulary ------------------------------------------------

#: The runtime reads the legacy file/config stores only.
STORE_LEGACY = "legacy"
#: Both stores are readable; the new store wins when it has an answer.
STORE_DUAL = "dual"
#: The new control plane is the only source.
STORE_NEW = "new"

STORE_VERSIONS: Tuple[str, ...] = (STORE_LEGACY, STORE_DUAL, STORE_NEW)

#: What an unset / unknown / unreadable switch resolves to. Reading an empty
#: new store because a key was missing would show every connection as "gone",
#: so the safe direction is always the legacy store.
SAFE_STORE_VERSION = STORE_LEGACY

# -- legacy source formats ---------------------------------------------------

ERP_FORMAT = "erp_json"
MCP_FORMAT = "mcp_json"
#: The pseudo-locator for ``conf()["mcp_servers"]`` (no file to point at).
CONFIG_MCP_LOCATOR = "config:mcp_servers"

# -- non-importable reasons --------------------------------------------------

#: Ownership cannot be derived from the source (no tenant binding, no explicit
#: mapping). The record is reported, never defaulted to the default tenant.
REASON_OWNERSHIP_UNKNOWN = "ownership_unknown"
#: ``u9`` / ``kingdee`` / ``k3`` / ``eas``: the registry has no production
#: adapter, so the record stays where it is instead of becoming a fake SAP row.
REASON_UNSUPPORTED_PROVIDER = "unsupported_provider"
#: The new validator refused the mapped configuration.
REASON_INVALID_CONFIG = "invalid_config"
#: A required secret slot has no value in the legacy record.
REASON_SECRET_MISSING = "secret_missing"
#: A secret exists but cannot be carried (e.g. keyed material the new store has
#: no slot for).
REASON_SECRET_UNDECRYPTABLE = "secret_undecryptable"
#: A live connection in the target scope already has this name and was not
#: created by this migration.
REASON_NAME_COLLISION = "name_collision"
#: A singleton in the target scope is already occupied.
REASON_SINGLETON_CONFLICT = "singleton_conflict"
#: Another record in the same run has the same content hash.
REASON_DUPLICATE_SOURCE = "duplicate_source"
#: The legacy record carries a non-empty field the new type cannot express
#: (e.g. an ERP ``saprouter``), so importing it would silently change routing.
REASON_CONFIG_NOT_REPRESENTABLE = "config_not_representable"
#: Several secret values map onto one new slot and cannot be split.
REASON_MULTIPLE_SECRET_VALUES = "multiple_secret_values"
#: The source file/entry itself could not be read as the expected shape.
REASON_LEGACY_UNREADABLE = "legacy_unreadable"

#: Reasons that block a scope from switching to ``new``. A migration whose
#: ownership is unknown or whose representable fields would change must not be
#: declared done. ``unsupported_provider`` is deliberately *not* here: a U9 /
#: 金蝶 row has no production adapter at all, is reported, and stays in the
#: legacy file as an explicitly disabled legacy record (design §6.6).
BLOCKING_REASONS = frozenset({
    REASON_OWNERSHIP_UNKNOWN,
    REASON_INVALID_CONFIG,
    REASON_SECRET_MISSING,
    REASON_SECRET_UNDECRYPTABLE,
    REASON_NAME_COLLISION,
    REASON_SINGLETON_CONFLICT,
    REASON_CONFIG_NOT_REPRESENTABLE,
    REASON_MULTIPLE_SECRET_VALUES,
    REASON_LEGACY_UNREADABLE,
})

#: Ledger result values. ``skipped_unimportable`` is an addition to the four the
#: task asks for, so a permanent reason is not confused with a retryable one.
RESULT_IMPORTED = "imported"
RESULT_SKIPPED_IDENTICAL = "skipped_identical"
RESULT_SKIPPED_CONFLICT = "skipped_conflict"
RESULT_FAILED = "failed"
RESULT_SKIPPED_UNIMPORTABLE = "skipped_unimportable"
#: In-flight claim. A row in this state belongs to exactly one importer.
RESULT_PENDING = "pending"

#: How long a ``pending`` claim may sit before a retry is allowed to take it
#: over. Long enough that a slow create is not stolen; the create-idempotency
#: key still protects the stolen case.
CLAIM_STALE_SECONDS = 300

#: How long a losing importer waits for the winner to finish before reporting
#: the record as still in flight.
DEFAULT_WAIT_SECONDS = 5.0


class MigrationError(RuntimeError):
    """A migration precondition failed (e.g. an unsafe store switch)."""


# -- deployment switch -------------------------------------------------------


def _config_block() -> Mapping[str, Any]:
    """The ``external_connections`` config block, read fresh on every call.

    A misconfigured block is treated as empty (everything at the safe default)
    rather than raising: an unreadable policy must never become an open one.
    """
    try:
        raw = (conf() or {}).get("external_connections") or {}
        return raw if isinstance(raw, Mapping) else {}
    except Exception:  # noqa: BLE001 - never fail open
        return {}


def _resolve_store_version(raw: Any, scope_key: Optional[str]) -> str:
    if isinstance(raw, str):
        value = raw.strip().lower()
        return value if value in STORE_VERSIONS else SAFE_STORE_VERSION
    if isinstance(raw, Mapping):
        for key in (scope_key, "default", "global", "*"):
            if key and key in raw:
                value = str(raw[key]).strip().lower()
                return value if value in STORE_VERSIONS else SAFE_STORE_VERSION
    return SAFE_STORE_VERSION


def active_store_version(scope_key: Optional[str] = None) -> str:
    """The store the runtime must read for ``scope_key``.

    ``conf()["external_connections"]["store_version"]`` may be a single value
    or a mapping keyed by scope (``"default"`` / ``"global"`` / ``"*"`` as the
    fallback). ``store_versions`` is accepted as an alias so a deployment can
    keep ``store_version`` as the global default and add per-scope overrides.
    Re-read on every call, so changing the value takes effect without a
    restart. Unknown values and read failures resolve to ``legacy``.
    """
    block = _config_block()
    raw = block.get("store_version")
    if raw is None:
        raw = block.get("store_versions")
    return _resolve_store_version(raw, scope_key)


def scope_key(scope: str, tenant_id: Optional[str]) -> str:
    """The migration ledger / config scope key for a connection scope."""
    if scope == registry.SCOPE_PLATFORM:
        return "platform"
    return "%s:%s" % (scope, tenant_id or "?")


def pending_legacy_records(identity, *, sources=None, erp_files=(),
                           mcp_files=()) -> List[Dict[str, Any]]:
    """Importable legacy records with no ``imported`` ledger row yet."""
    report = preflight(identity, sources=sources, erp_files=erp_files,
                       mcp_files=mcp_files)
    return [
        record for record in report["records"]
        if record["importable"] and not record["imported"]
    ]


def validate_store_version(identity, *, sources=None, erp_files=(),
                           mcp_files=()) -> Dict[str, Any]:
    """Check the active switch against the legacy state.

    ``new`` is refused while any importable legacy record is unimported, and
    while any record has a blocking reason (unknown ownership, name collision,
    unrepresentable config). ``dual`` is safe by construction but still reports
    the pending set so an operator can see what the disagreements will be.
    The legacy set is scanned once.
    """
    version = active_store_version()
    report = preflight(identity, sources=sources, erp_files=erp_files,
                       mcp_files=mcp_files)
    pending = [
        record for record in report["records"]
        if record["importable"] and not record["imported"]
    ]
    blocking = [
        record for record in report["records"]
        if record["reason"] in BLOCKING_REASONS
    ]
    reason = ""
    if version == STORE_NEW and (pending or blocking):
        reason = "new_store_with_unimported_legacy"
    return {
        "store_version": version,
        "ok": not reason,
        "reason": reason,
        "pending": pending,
        "blocking": blocking,
    }


def assert_store_version_safe(identity, *, sources=None, erp_files=(),
                              mcp_files=()) -> Dict[str, Any]:
    """``validate_store_version`` that raises with a clear reason when unsafe.

    This is the startup check: call it once during boot (or before a scope is
    declared switched) and let the raised message name what has to be imported
    first. Wiring the call into the web/agent startup path lives outside this
    module; the check itself is here so it is testable and single-sourced.
    """
    report = validate_store_version(
        identity, sources=sources, erp_files=erp_files, mcp_files=mcp_files)
    if not report["ok"]:
        raise MigrationError(
            "store_version=%r is unsafe: %d legacy record(s) are unimported "
            "and %d have a blocking reason (%s)"
            % (report["store_version"], len(report["pending"]),
               len(report["blocking"]), report["reason"]))
    return report


# -- legacy sources ----------------------------------------------------------


@dataclass(frozen=True)
class LegacySource:
    """One place legacy connection data lives."""

    locator: str
    format: str
    tenant_id: Optional[str] = None
    agent_id: str = ""
    scope: str = registry.SCOPE_TENANT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "locator": self.locator,
            "format": self.format,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "scope": self.scope,
        }


def _as_source(item: Any, fmt: str) -> Optional[LegacySource]:
    """Accept a ``LegacySource``, a ``path`` or ``(path, tenant_id)``."""
    if isinstance(item, LegacySource):
        if item.format == fmt:
            return item
        return LegacySource(locator=item.locator, format=fmt,
                            tenant_id=item.tenant_id, agent_id=item.agent_id,
                            scope=item.scope)
    if isinstance(item, (tuple, list)):
        locator = str(item[0])
        tenant_id = str(item[1]) if len(item) > 1 and item[1] else None
        return LegacySource(locator=locator, format=fmt, tenant_id=tenant_id)
    return LegacySource(locator=str(item), format=fmt)


def _add_source(seen: set, out: List[LegacySource],
                source: LegacySource) -> None:
    key = (os.path.realpath(source.locator), source.tenant_id, source.scope)
    if key in seen:
        return
    seen.add(key)
    out.append(source)


def _agent_mcp_candidates(identity, agent_id: str,
                          tenant_id: Optional[str]) -> List[str]:
    """Where ``mcp.json`` lives for one bound Agent.

    The loader resolves ``<workspace>/mcp.json`` first and the tenant shared
    copy second (``common.state_dir._shared_or_own``). Both are candidates:
    which one exists is the deployment's choice and must not be assumed.
    """
    try:
        from common import state_dir
        from common.runtime_identity import RuntimeIdentity

        workspace = state_dir.state_root(
            RuntimeIdentity(agent_id=agent_id, tenant_id=tenant_id))
        candidates = [os.path.join(str(workspace), "mcp.json")]
        if tenant_id:
            root = identity.tenant_shared_root(tenant_id)
            if root:
                candidates.append(os.path.join(root, "mcp.json"))
        return candidates
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return []


def discover_legacy_sources(identity, *, erp_files=(), mcp_files=()) -> List[LegacySource]:
    """Every legacy store this deployment plausibly has, plus explicit ones.

    Discovery is *read-only* and deliberately conservative: it only walks
    tenant shared roots (for the ERP file) and bound Agent workspaces (for
    ``mcp.json``). The instance-wide ``conf()["mcp_servers"]`` fallback is
    included as a pseudo-locator with no derivable tenant, which surfaces it as
    ``ownership_unknown`` until an operator maps it.
    """
    sources: List[LegacySource] = []
    seen: set = set()

    try:
        roots = identity.tenant_shared_roots()
    except Exception:  # noqa: BLE001
        roots = []
    for row in roots:
        root = (row or {}).get("shared_root")
        if not root:
            continue
        path = os.path.join(root, ".one", "erp_connections.json")
        if os.path.exists(path):
            _add_source(seen, sources, LegacySource(
                locator=path, format=ERP_FORMAT, tenant_id=row["id"]))

    try:
        bindings = identity.list_agent_bindings()
    except Exception:  # noqa: BLE001
        bindings = []
    for binding in bindings:
        agent_id = (binding or {}).get("agent_id") or ""
        tenant_id = (binding or {}).get("tenant_id")
        for path in _agent_mcp_candidates(identity, agent_id, tenant_id):
            if os.path.exists(path):
                _add_source(seen, sources, LegacySource(
                    locator=path, format=MCP_FORMAT, tenant_id=tenant_id,
                    agent_id=agent_id))

    try:
        fallback = conf().get("mcp_servers") or []
    except Exception:  # noqa: BLE001
        fallback = []
    if fallback:
        _add_source(seen, sources, LegacySource(
            locator=CONFIG_MCP_LOCATOR, format=MCP_FORMAT, tenant_id=None))

    for item in erp_files:
        source = _as_source(item, ERP_FORMAT)
        if source is not None:
            _add_source(seen, sources, source)
    for item in mcp_files:
        source = _as_source(item, MCP_FORMAT)
        if source is not None:
            _add_source(seen, sources, source)
    return sources


# -- reading legacy records --------------------------------------------------


def _normalize_mcp_entries(raw: Any) -> List[Dict[str, Any]]:
    """Mirror ``agent.tools.tool_manager._normalize_mcp_configs``.

    Duplicated here (six lines) rather than imported, so the migration tool does
    not import the tool manager and its heavy transitive imports just to read a
    JSON shape.
    """
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, Mapping)]
    if isinstance(raw, Mapping):
        result: List[Dict[str, Any]] = []
        for name, cfg in raw.items():
            if not isinstance(cfg, Mapping):
                continue
            entry = {"name": name, **cfg}
            if "type" not in entry and "transport" not in entry:
                entry["type"] = "sse" if "url" in entry else "stdio"
            result.append(entry)
        return result
    return []


def _read_source(source: LegacySource) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``(raw_records, error)``; never raises."""
    try:
        if source.format == ERP_FORMAT:
            data = _load_json_file(source.locator)
            if not isinstance(data, list):
                return [], "erp store is not a JSON list"
            return [item for item in data if isinstance(item, Mapping)], ""
        if source.format == MCP_FORMAT:
            if source.locator == CONFIG_MCP_LOCATOR:
                return _normalize_mcp_entries(conf().get("mcp_servers") or []), ""
            data = _load_json_file(source.locator)
            if isinstance(data, Mapping):
                raw = (data.get("mcpServers") or data.get("mcp_servers")
                       or data)
            else:
                raw = data
            return _normalize_mcp_entries(raw), ""
        return [], "unknown legacy format %r" % source.format
    except Exception as error:  # noqa: BLE001 - a bad file is a report, not a crash
        return [], "%s: %s" % (type(error).__name__, error)


def _load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# -- hashing -----------------------------------------------------------------


def _secret_digest(value: str) -> str:
    """A keyed, non-reversible digest of one secret value.

    ``auth.crypto.fingerprint`` is HMAC'd with the deployment's credential key,
    so the ledger cannot be brute-forced. When no key is configured the preflight
    must still work (it writes nothing); it then falls back to a plain hash and
    labels it, and the import refuses anyway because encryption is unavailable.
    """
    try:
        from auth.crypto import fingerprint
        return "hmac:" + fingerprint(str(value))
    except Exception:  # noqa: BLE001 - keyless preflight
        return "sha256:" + hashlib.sha256(
            str(value).encode("utf-8")).hexdigest()


def _source_hash(*, kind: str, scope: str, tenant_id: Optional[str], name: str,
                 legacy_id: str, config: Mapping[str, Any],
                 secrets: Mapping[str, str]) -> str:
    """Stable content hash: ordering-independent, secret-safe.

    The secret values enter only as keyed digests, so the hash changes when a
    password changes (a changed record is re-imported, not skipped as
    identical) without storing anything reversible.
    """
    payload = {
        "kind": kind,
        "scope": scope,
        "tenant_id": tenant_id,
        "name": name,
        "legacy_id": legacy_id,
        "config": dict(config),
        "secrets": {slot: _secret_digest(value)
                    for slot, value in sorted(secrets.items())},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# -- record normalization ----------------------------------------------------


_ERP_KNOWN_FIELDS = frozenset({
    "id", "name", "system", "provider", "is_default", "ashost", "sysnr",
    "saprouter", "base_url", "client", "lang", "verify_ssl", "timeout",
    "username", "user", "password", "passwd",
})

_MCP_KNOWN_FIELDS = frozenset({
    "name", "type", "transport", "command", "args", "env", "url", "headers",
    "timeout", "tool_name_prefix", "inherit_full_env", "auth", "oauth_provider",
})

_MCP_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "sse": "sse",
    "streamable-http": "streamable_http",
    "streamable_http": "streamable_http",
    "streamablehttp": "streamable_http",
    "http": "streamable_http",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _validate(kind: str, config: Mapping[str, Any]
              ) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    try:
        return registry.validate_config(kind, config), "", {}
    except ExternalConnectionError as error:
        fields = sorted(str(name) for name in (error.fields or {}))
        if error.code == "unsupported_provider":
            return None, REASON_UNSUPPORTED_PROVIDER, {
                "code": error.code, "fields": fields}
        return None, REASON_INVALID_CONFIG, {
            "code": error.code, "fields": fields}


def _base_record(source: LegacySource, index: int, raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "source_locator": "%s#%d" % (source.locator, index),
        "source_format": source.format,
        "source_tenant_id": source.tenant_id,
        "agent_id": source.agent_id,
        "kind": "",
        "scope": source.scope,
        "tenant_id": source.tenant_id,
        "name": "",
        "legacy_id": "",
        "is_default": False,
        "config_keys": [],
        "secret_slots": [],
        "secret_present": {},
        "dropped_fields": [],
        "source_hash": "",
        "scope_key": scope_key(source.scope, source.tenant_id),
        "importable": False,
        "reason": "",
        "reason_detail": {},
    }


def _normalize_erp(source: LegacySource, index: int, raw: Mapping[str, Any]
                   ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    record = _base_record(source, index, raw)
    record["kind"] = registry.KIND_ERP
    record["scope"] = registry.SCOPE_TENANT
    record["legacy_id"] = _text(raw.get("id"))
    record["name"] = _text(raw.get("name") or raw.get("system")
                           or raw.get("id"))
    record["is_default"] = bool(raw.get("is_default"))

    if not record["tenant_id"]:
        record["reason"] = REASON_OWNERSHIP_UNKNOWN
        record["reason_detail"] = {"needed": "explicit tenant mapping"}
        return _finish_record(record), {}

    provider = _text(raw.get("provider") or "rfc").lower() or "rfc"
    if provider in registry.UNSUPPORTED_ERP_PROVIDERS:
        record["reason"] = REASON_UNSUPPORTED_PROVIDER
        record["reason_detail"] = {"provider": provider}
        return _finish_record(record), {}

    user = _text(raw.get("username") or raw.get("user"))
    password = raw.get("password", raw.get("passwd"))
    secret_payload: Dict[str, str] = {}
    if password is not None and str(password) != "":
        secret_payload["password"] = str(password)

    config: Dict[str, Any] = {"provider": provider}
    if provider == "rfc":
        config.update({
            "ashost": _text(raw.get("ashost")),
            "sysnr": _text(raw.get("sysnr")),
            "client": _text(raw.get("client")),
            "user": user,
            "lang": _text(raw.get("lang")) or "EN",
        })
    else:
        config.update({
            "base_url": _text(raw.get("base_url")),
            "client": _text(raw.get("client")),
            "user": user,
        })
        if raw.get("verify_ssl") is not None:
            config["verify_ssl"] = bool(raw.get("verify_ssl"))
        timeout = raw.get("timeout")
        if timeout not in (None, ""):
            try:
                config["timeout"] = int(str(timeout).strip())
            except (TypeError, ValueError):
                record["reason"] = REASON_INVALID_CONFIG
                record["reason_detail"] = {"code": "field_type",
                                           "fields": ["timeout"]}
                return _finish_record(record), {}

    dropped = [name for name in raw
               if str(name) not in _ERP_KNOWN_FIELDS
               and _text(raw.get(name)) not in ("", "[]", "{}")]
    if _text(raw.get("saprouter")):
        # Routing is connectivity, not a display field: dropping it silently
        # would point the connection at a different network path.
        record["reason"] = REASON_CONFIG_NOT_REPRESENTABLE
        record["reason_detail"] = {"fields": ["saprouter"]}
        return _finish_record(record), {}
    record["dropped_fields"] = sorted(str(name) for name in dropped)

    if not record["name"]:
        record["reason"] = REASON_INVALID_CONFIG
        record["reason_detail"] = {"code": "field_required", "fields": ["name"]}
        return _finish_record(record), {}
    if not secret_payload.get("password"):
        record["reason"] = REASON_SECRET_MISSING
        record["reason_detail"] = {"slots": ["password"]}
        record["secret_slots"] = ["password"]
        return _finish_record(record), {}

    normalized, reason, detail = _validate(registry.KIND_ERP, config)
    if reason:
        record["reason"] = reason
        record["reason_detail"] = detail
        return _finish_record(record), {}
    # The legacy id is reused as the new row's primary key (design §166: ERP 复用
    # 原 ID). Anything already stored against that id — a scene's saved default,
    # an operator's runbook — keeps resolving, which is the difference between a
    # cutover that is invisible to consumers and one that quietly breaks them
    # while the ledger reports success. A legacy id that is not usable as a key
    # is not an error: the row is created under a generated id and the ledger's
    # ``legacy_id`` keeps the mapping, so the record is never left behind.
    record["reuse_id"] = (record["legacy_id"]
                          if reusable_connection_id(record["legacy_id"]) else "")
    record["config_keys"] = sorted(normalized)
    record["secret_slots"] = ["password"]
    record["secret_present"] = {"password": True}
    record["importable"] = True
    record["reason"] = ""
    record["source_hash"] = _source_hash(
        kind=registry.KIND_ERP, scope=record["scope"],
        tenant_id=record["tenant_id"], name=record["name"],
        legacy_id=record["legacy_id"], config=normalized,
        secrets=secret_payload)
    record["_config"] = normalized
    return record, secret_payload


def _normalize_mcp(source: LegacySource, index: int, raw: Mapping[str, Any]
                   ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    record = _base_record(source, index, raw)
    record["kind"] = registry.KIND_MCP
    record["name"] = _text(raw.get("name"))
    record["legacy_id"] = _text(raw.get("name"))

    if not record["tenant_id"]:
        record["reason"] = REASON_OWNERSHIP_UNKNOWN
        record["reason_detail"] = {"needed": "explicit tenant mapping"}
        return _finish_record(record), {}

    raw_transport_raw = raw.get("type", raw.get("transport", "stdio"))
    transport = _MCP_TRANSPORT_ALIASES.get(
        _text(raw_transport_raw).lower(), "stdio")
    secret_payload: Dict[str, str] = {}
    config: Dict[str, Any] = {"transport": transport}

    # The prefix composes every tool name this server contributes, and the
    # composed name is the resource id an existing grant was filed under. It is
    # carried for the same reason the connection name is: dropping it would
    # re-name every tool and orphan exactly the grants the spec says the
    # migration must preserve. Validated with the rest of the config below.
    prefix = _text(raw.get("tool_name_prefix"))
    if prefix:
        config["tool_name_prefix"] = prefix

    if transport == "stdio":
        env = raw.get("env") or {}
        if not isinstance(env, Mapping):
            record["reason"] = REASON_INVALID_CONFIG
            record["reason_detail"] = {"code": "field_type", "fields": ["env"]}
            return _finish_record(record), {}
        env = {str(name): value for name, value in env.items()}
        raw_args = raw.get("args")
        if raw_args is not None and not isinstance(raw_args, (list, tuple)):
            record["reason"] = REASON_INVALID_CONFIG
            record["reason_detail"] = {"code": "field_type", "fields": ["args"]}
            return _finish_record(record), {}
        config["command"] = _text(raw.get("command"))
        config["args"] = [str(arg) for arg in (raw_args or [])]
        config["env_keys"] = sorted(env)
        if len(env) > 1:
            # The new control plane models one ``env`` secret slot; several
            # values cannot be split without inventing slots.
            record["reason"] = REASON_MULTIPLE_SECRET_VALUES
            record["reason_detail"] = {"slots": ["env"],
                                       "count": len(env)}
            return _finish_record(record), {}
        if env:
            name, value = next(iter(env.items()))
            if value in (None, ""):
                record["reason"] = REASON_SECRET_MISSING
                record["reason_detail"] = {"slots": ["env"]}
                record["secret_slots"] = ["env"]
                return _finish_record(record), {}
            secret_payload["env"] = str(value)
    else:
        headers = raw.get("headers") or {}
        if not isinstance(headers, Mapping):
            record["reason"] = REASON_INVALID_CONFIG
            record["reason_detail"] = {"code": "field_type",
                                       "fields": ["headers"]}
            return _finish_record(record), {}
        headers = {str(name): value for name, value in headers.items()}
        config["url"] = _text(raw.get("url"))
        if len(headers) > 1:
            record["reason"] = REASON_MULTIPLE_SECRET_VALUES
            record["reason_detail"] = {"slots": ["header"],
                                       "headers": sorted(headers)}
            return _finish_record(record), {}
        if headers:
            header_name, value = next(iter(headers.items()))
            if value in (None, ""):
                record["reason"] = REASON_SECRET_MISSING
                record["reason_detail"] = {"slots": ["header"]}
                record["secret_slots"] = ["header"]
                return _finish_record(record), {}
            config["auth"] = "header"
            config["header_name"] = header_name
            secret_payload["header"] = str(value)
        else:
            # No static secret: preserve an explicit legacy auth mode (OAuth)
            # when the record named one, otherwise "none". The adapter still
            # decides at call time; this only keeps the declared intent.
            legacy_auth = _text(raw.get("auth")).lower()
            if legacy_auth in ("oauth", "header", "none"):
                config["auth"] = legacy_auth
                provider = _text(raw.get("oauth_provider"))
                if legacy_auth == "oauth" and provider:
                    config["oauth_provider"] = provider
            else:
                config["auth"] = "none"

    dropped = [name for name in raw
               if str(name) not in _MCP_KNOWN_FIELDS
               and _text(raw.get(name)) not in ("", "[]", "{}")]
    record["dropped_fields"] = sorted(str(name) for name in dropped)

    if not record["name"]:
        record["reason"] = REASON_INVALID_CONFIG
        record["reason_detail"] = {"code": "field_required", "fields": ["name"]}
        return _finish_record(record), {}

    normalized, reason, detail = _validate(registry.KIND_MCP, config)
    if reason:
        record["reason"] = reason
        record["reason_detail"] = detail
        return _finish_record(record), {}
    record["config_keys"] = sorted(normalized)
    record["secret_slots"] = sorted(secret_payload)
    record["secret_present"] = {slot: True for slot in secret_payload}
    record["importable"] = True
    record["source_hash"] = _source_hash(
        kind=registry.KIND_MCP, scope=record["scope"],
        tenant_id=record["tenant_id"], name=record["name"],
        legacy_id=record["legacy_id"], config=normalized,
        secrets=secret_payload)
    record["_config"] = normalized
    return record, secret_payload


def _finish_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the hash for a non-importable record too.

    Its hash is what lets the report say "this exact record is the unimportable
    one" and keeps the report stable across runs.
    """
    if not record["source_hash"]:
        record["source_hash"] = _source_hash(
            kind=record["kind"] or "unknown", scope=record["scope"],
            tenant_id=record["tenant_id"], name=record["name"],
            legacy_id=record["legacy_id"], config={}, secrets={})
    record["scope_key"] = scope_key(record["scope"], record["tenant_id"])
    return record


def _public_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    """The reportable projection: slot names, never slot values."""
    return {
        "source_locator": record["source_locator"],
        "source_format": record["source_format"],
        "agent_id": record["agent_id"],
        "kind": record["kind"],
        "scope": record["scope"],
        "tenant_id": record["tenant_id"],
        "name": record["name"],
        "legacy_id": record["legacy_id"],
        "is_default": record["is_default"],
        "source_hash": record["source_hash"],
        "scope_key": record["scope_key"],
        "config_keys": list(record["config_keys"]),
        "secret_slots": list(record["secret_slots"]),
        "secret_present": dict(record["secret_present"]),
        "dropped_fields": list(record["dropped_fields"]),
        "importable": record["importable"],
        "reason": record["reason"],
        "reason_detail": dict(record["reason_detail"]),
    }


# -- scanning ----------------------------------------------------------------


def _scan(identity, *, sources=None, erp_files=(), mcp_files=(), actor_user_id=None
          ) -> Dict[str, Any]:
    """Read every legacy source and return secret-free records + a side table.

    The returned ``secrets`` mapping is keyed by ``(source_hash, scope_key)``
    and is the only place a plaintext legacy secret lives. It is never part of
    a report; :func:`_public_record` has no access to it.
    """
    if sources is None:
        source_list = discover_legacy_sources(
            identity, erp_files=erp_files, mcp_files=mcp_files)
    else:
        # An explicit ``sources`` list is normally ``LegacySource`` objects; a
        # bare path is accepted as an ERP file rather than being dropped, but it
        # then carries no tenant and is reported as ``ownership_unknown``.
        source_list = []
        for item in sources:
            source = (item if isinstance(item, LegacySource)
                      else _as_source(item, ERP_FORMAT))
            if source is not None:
                source_list.append(source)
        for item in erp_files:
            source = _as_source(item, ERP_FORMAT)
            if source is not None:
                source_list.append(source)
        for item in mcp_files:
            source = _as_source(item, MCP_FORMAT)
            if source is not None:
                source_list.append(source)

    records: List[Dict[str, Any]] = []
    secrets: Dict[Tuple[str, str], Dict[str, str]] = {}
    source_reports: List[Dict[str, Any]] = []
    for source in source_list:
        raw_records, error = _read_source(source)
        entry = source.to_dict()
        entry["records"] = len(raw_records)
        entry["error"] = error
        source_reports.append(entry)
        if error:
            record = _base_record(source, 0, {})
            record["kind"] = "unknown"
            record["name"] = ""
            record["reason"] = REASON_LEGACY_UNREADABLE
            record["reason_detail"] = {"error": error}
            records.append(_finish_record(record))
            continue
        for index, raw in enumerate(raw_records):
            if source.format == ERP_FORMAT:
                record, payload = _normalize_erp(source, index, raw)
            else:
                record, payload = _normalize_mcp(source, index, raw)
            records.append(record)
            if payload:
                secrets[(record["source_hash"],
                         record["scope_key"])] = payload

    _annotate_existing(identity, records)
    return {"sources": source_reports, "records": records, "secrets": secrets}


def _annotate_existing(identity, records: List[Dict[str, Any]]) -> None:
    """Duplicate / name-collision / singleton checks against the new store.

    A live row created by an earlier migration run is not a collision: the
    ledger mapping names it. A row with the same name that no ledger entry
    claims is one, because importing would produce two indistinguishable cards.
    """
    try:
        claimed = _claimed_connection_ids(identity._store)
    except Exception:  # noqa: BLE001
        claimed = set()
    existing: Dict[Tuple[str, str, Optional[str]], Dict[str, set]] = {}
    try:
        rows = identity._store.execute(
            "SELECT id, kind, scope, tenant_id, name, deleted_at FROM"
            " external_connections WHERE deleted_at IS NULL")
    except Exception:  # noqa: BLE001
        rows = []
    for row in rows:
        key = (row["kind"], row["scope"], row["tenant_id"])
        existing.setdefault(key, {}).setdefault(row["name"], set()).add(row["id"])

    by_target_hash: Dict[Tuple[str, str, Optional[str], str], int] = {}
    by_target_name: Dict[Tuple[str, str, Optional[str], str], int] = {}
    for record in records:
        if not record["importable"]:
            continue
        target = (record["kind"], record["scope"], record["tenant_id"])
        hash_key = (record["kind"], record["scope"], record["tenant_id"],
                    record["source_hash"])
        if by_target_hash.get(hash_key):
            by_target_hash[hash_key] += 1
            record["importable"] = False
            record["reason"] = REASON_DUPLICATE_SOURCE
            record["reason_detail"] = {"of": record["source_locator"]}
            continue
        by_target_hash[hash_key] = 1
        name_key = (record["kind"], record["scope"], record["tenant_id"],
                    record["name"])
        if by_target_name.get(name_key):
            by_target_name[name_key] += 1
            record["importable"] = False
            record["reason"] = REASON_NAME_COLLISION
            record["reason_detail"] = {"name": record["name"],
                                       "scope": record["scope"]}
            continue
        by_target_name[name_key] = 1
        colliding = [
            connection_id
            for connection_id in existing.get(target, {}).get(record["name"], set())
            if connection_id not in claimed
        ]
        if colliding:
            record["importable"] = False
            record["reason"] = REASON_NAME_COLLISION
            record["reason_detail"] = {"name": record["name"],
                                       "scope": record["scope"]}
        if record["kind"] == registry.KIND_OA and _live_row_named(
                existing, target, record["name"], claimed):
            record["importable"] = False
            record["reason"] = REASON_SINGLETON_CONFLICT
            record["reason_detail"] = {"kind": record["kind"],
                                       "scope": record["scope"]}


def _live_row_named(existing, target, name, claimed) -> bool:
    return any(connection_id not in claimed
               for connection_id in existing.get(target, {}).get(name, set()))


# -- preflight ---------------------------------------------------------------


def inventory(identity, *, sources=None, erp_files=(), mcp_files=(),
              actor_user_id=None) -> List[Dict[str, Any]]:
    """The scanned records *with* their secret slot names and locators.

    :func:`preflight` answers with a reportable projection: slot names and
    presence only, never which literal fields in which file hold a value. The
    cutover step that redacts the legacy plaintext needs exactly that one extra
    fact -- which fields to blank in which file -- and nothing else, so it gets
    its own accessor instead of the report growing a field the operator does not
    need. Secret *values* are still not returned: the redaction blanks by field
    name, so it never has to read a value to remove it.
    """
    scan = _scan(identity, sources=sources, erp_files=erp_files,
                 mcp_files=mcp_files, actor_user_id=actor_user_id)
    return list(scan["records"])


def carried_secret_slots(store, connection_id: str) -> set:
    """Public form of the "is this slot really in the new store" check."""
    return _carried_secret_slots(store, connection_id)


def ledger_entry(store, source_hash: str, scope_key: str):
    """The ledger row for one record, or ``None``."""
    return _ledger_index(store).get((source_hash, scope_key))


def ledger_by_legacy_id(store, legacy_id: str, scope_key: str):
    """The ledger row for a record whose *content* has changed since import.

    Redacting a legacy file changes it, so the record no longer hashes to what
    the ledger recorded and a hash-only lookup would report a finished migration
    as unmigrated. The ``(legacy_id, scope_key)`` pair is the identity that does
    survive -- it is what the import itself records in ``mapping_json``.

    Callers must still treat a hit here as *weaker* evidence than a hash match:
    it proves a record with this identity was imported, not that this content
    was.
    """
    legacy_id = str(legacy_id or "")
    if not legacy_id:
        return None
    for (source_hash, key), row in _ledger_index(store).items():
        if key != scope_key:
            continue
        try:
            mapping = json.loads(row["mapping_json"] or "{}")
        except ValueError:
            continue
        if str(mapping.get("legacy_id") or "") == legacy_id:
            return row
    return None


def preflight(identity, *, sources=None, erp_files=(), mcp_files=(),
              actor_user_id=None) -> Dict[str, Any]:
    """A read-only report of every legacy record and whether it can be imported.

    Writes nothing — not to the legacy store, not to the control plane. Safe to
    run repeatedly; the same inputs produce the same ``source_hash`` for each
    record (key ordering does not matter) and therefore a stable report.
    """
    scan = _scan(identity, sources=sources, erp_files=erp_files,
                 mcp_files=mcp_files, actor_user_id=actor_user_id)
    store = identity._store
    ledger = _ledger_index(store)
    public_records: List[Dict[str, Any]] = []
    for record in scan["records"]:
        entry = _public_record(record)
        row = ledger.get((record["source_hash"], record["scope_key"]))
        entry["imported"] = bool(row and row["result"] == RESULT_IMPORTED)
        entry["ledger_result"] = row["result"] if row else ""
        public_records.append(entry)

    by_reason: Dict[str, int] = {}
    for record in public_records:
        if not record["importable"]:
            by_reason[record["reason"]] = by_reason.get(record["reason"], 0) + 1
    return {
        "writable": False,
        "store_version": active_store_version(),
        "sources": scan["sources"],
        "records": public_records,
        "summary": {
            "total": len(public_records),
            "importable": sum(1 for r in public_records if r["importable"]),
            "imported": sum(1 for r in public_records if r["imported"]),
            "unimportable": sum(1 for r in public_records
                                if not r["importable"]),
            "by_reason": by_reason,
        },
    }


# -- ledger ------------------------------------------------------------------


def _ledger_index(store) -> Dict[Tuple[str, str], Mapping[str, Any]]:
    try:
        rows = store.execute(
            "SELECT source_hash, scope_key, result, mapping_json, detail_json,"
            " batch_id, source_locator FROM external_connection_migrations")
    except Exception:  # noqa: BLE001 - a missing table is an empty ledger
        return {}
    return {(row["source_hash"], row["scope_key"]): row for row in rows}


def _ledger_row(store, record) -> Optional[Mapping[str, Any]]:
    rows = store.execute(
        "SELECT * FROM external_connection_migrations WHERE source_hash=?"
        " AND scope_key=?", (record["source_hash"], record["scope_key"]))
    return rows[0] if rows else None


def _claimed_connection_ids(store) -> set:
    ids = set()
    for row in store.execute(
            "SELECT mapping_json FROM external_connection_migrations"):
        try:
            mapping = json.loads(row["mapping_json"] or "{}")
        except ValueError:
            continue
        connection_id = mapping.get("connection_id")
        if connection_id:
            ids.add(connection_id)
    return ids


def _failure_reason(error: BaseException) -> str:
    """A reportable reason for an unexpected create failure.

    The credential master key is read through ``auth.crypto`` in more than one
    place on the create path (the idempotency fingerprint as well as the secret
    write), so a deployment with no key configured can surface a bare
    ``CredentialCryptoError`` before the service's own wrapping runs. Both doors
    mean the same thing to the operator: the secret exists but cannot be carried.
    """
    if type(error).__name__ == "CredentialCryptoError":
        return REASON_SECRET_UNDECRYPTABLE
    return type(error).__name__


def _claim_id(record, batch_id: str) -> str:
    """The ledger row's primary key for one record.

    ``external_connection_migrations.batch_id`` is declared ``PRIMARY KEY`` in
    migration 28, so it must be unique per *row*, not per run. Deriving it from
    the record instead of the run makes the claim id stable across runs (a retry
    updates the same row) and unique per record; the operator's run
    ``batch_id`` is carried in ``detail_json`` so the run is still traceable.
    """
    seed = "%s|%s|%s" % (batch_id, record["source_hash"], record["scope_key"])
    return "mig_%s" % hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _try_claim(store, record, batch_id: str) -> bool:
    """Insert a ``pending`` claim; False when the row already exists."""
    claim_id = _claim_id(record, batch_id)
    try:
        with store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT INTO external_connection_migrations"
                " (batch_id, source_locator, source_hash, scope_key,"
                "  mapping_json, result, detail_json)"
                " VALUES (?,?,?,?,'{}',?,'{}')",
                (claim_id, record["source_locator"], record["source_hash"],
                 record["scope_key"], RESULT_PENDING))
            con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except sqlite3.OperationalError:
        # A busy database is not an "already claimed"; returning False sends the
        # caller to the read loop, which retries within its bounded budget.
        return False


def _reclaim(store, record, batch_id: str, expected_result: str) -> bool:
    """Compare-and-swap a non-``imported`` row to ``pending``.

    The ``result = ?`` predicate is what makes a retry race safe: two importers
    that both read ``failed`` cannot both reclaim it.
    """
    claim_id = _claim_id(record, batch_id)
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        cursor = con.execute(
            "UPDATE external_connection_migrations SET batch_id=?,"
            " source_locator=?, result=?, mapping_json='{}', detail_json='{}',"
            " created_at=unixepoch() WHERE source_hash=? AND scope_key=?"
            " AND result=?",
            (claim_id, record["source_locator"], RESULT_PENDING,
             record["source_hash"], record["scope_key"], expected_result))
        con.commit()
        return cursor.rowcount == 1


def _finish(store, record, result: str, *, mapping=None, detail=None) -> None:
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE external_connection_migrations SET result=?, mapping_json=?,"
            " detail_json=?, created_at=unixepoch() WHERE source_hash=? AND"
            " scope_key=?",
            (result, json.dumps(dict(mapping or {}), ensure_ascii=False),
             json.dumps(dict(detail or {}), ensure_ascii=False, default=str),
             record["source_hash"], record["scope_key"]))
        con.commit()


def _claim_or_read(store, record, batch_id: str, wait_seconds: float
                   ) -> Dict[str, Any]:
    """Acquire the right to create one record, or read the winner's outcome.

    Returns ``{"status": "claimed"}``, ``{"status": "already_imported",
    "mapping": {...}}`` or ``{"status": "in_progress"}``.
    """
    if _try_claim(store, record, batch_id):
        return {"status": "claimed"}
    deadline = time.monotonic() + max(0.0, wait_seconds)
    for _ in range(200):
        row = _ledger_row(store, record)
        if row is None:
            if _try_claim(store, record, batch_id):
                return {"status": "claimed"}
            continue
        result = row["result"]
        if result == RESULT_IMPORTED:
            try:
                mapping = json.loads(row["mapping_json"] or "{}")
            except ValueError:
                mapping = {}
            return {"status": "already_imported", "mapping": mapping}
        if result == RESULT_PENDING:
            stale = (time.time() - int(row["created_at"] or 0)
                     > CLAIM_STALE_SECONDS)
            if not stale and time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            if stale and _reclaim(store, record, batch_id, RESULT_PENDING):
                return {"status": "claimed"}
            if not stale:
                return {"status": "in_progress"}
            continue
        # failed / skipped_conflict: a retry resumes by taking the row over.
        if _reclaim(store, record, batch_id, result):
            return {"status": "claimed"}
    return {"status": "in_progress"}


# -- import ------------------------------------------------------------------


def import_connections(identity, *, actor_user_id: str, sources=None,
                       erp_files=(), mcp_files=(), batch_id: Optional[str] = None,
                       confirm: bool = False,
                       wait_seconds: float = DEFAULT_WAIT_SECONDS,
                       service=None) -> Dict[str, Any]:
    """Import every importable legacy record, exactly once.

    ``confirm`` must be true: this is an explicit operator action and the CLI
    passes it only for an explicit ``--yes``. Each connection is created through
    :class:`~integrations.external.service.ExternalConnectionService` in its own
    transaction (so the new validation, secret referencing, versioning and audit
    all apply), then the outcome is recorded in ``external_connection_migrations``.
    A record that cannot be imported is reported with its reason and left alone;
    nothing in the legacy store is modified.
    """
    if not confirm:
        raise MigrationError(
            "import requires explicit confirmation (confirm=True / --yes)")
    batch_id = batch_id or "mig_%s" % hashlib.sha256(
        ("%s:%s" % (actor_user_id, time.time())).encode("utf-8")
    ).hexdigest()[:24]
    if service is None:
        from integrations.external.service import ExternalConnectionService
        service = ExternalConnectionService(identity)

    scan = _scan(identity, sources=sources, erp_files=erp_files,
                 mcp_files=mcp_files, actor_user_id=actor_user_id)
    store = identity._store
    results: List[Dict[str, Any]] = []
    for record in scan["records"]:
        base = {
            "source_locator": record["source_locator"],
            "kind": record["kind"],
            "scope": record["scope"],
            "tenant_id": record["tenant_id"],
            "name": record["name"],
            "legacy_id": record["legacy_id"],
            "source_hash": record["source_hash"],
        }
        if not record["importable"]:
            results.append(dict(base, result=RESULT_SKIPPED_UNIMPORTABLE,
                                reason=record["reason"],
                                detail=record["reason_detail"]))
            continue
        claim = _claim_or_read(store, record, batch_id, wait_seconds)
        if claim["status"] == "already_imported":
            results.append(dict(base, result=RESULT_SKIPPED_IDENTICAL,
                                reason="", connection_id=claim["mapping"]
                                .get("connection_id", "")))
            continue
        if claim["status"] == "in_progress":
            results.append(dict(base, result=RESULT_FAILED,
                                reason="concurrent_import_in_progress"))
            continue
        payload = scan["secrets"].get(
            (record["source_hash"], record["scope_key"]), {})
        try:
            created = service.create_connection(
                actor_user_id=actor_user_id, scope=record["scope"],
                kind=record["kind"], name=record["name"],
                config=record["_config"], tenant_id=record["tenant_id"],
                secrets=payload or None,
                idempotency_key="migration:%s" % record["source_hash"],
                connection_id=record.get("reuse_id") or None)
        except ExternalConnectionError as error:
            result = (RESULT_SKIPPED_CONFLICT if error.status == 409
                      else RESULT_FAILED)
            # A secret that exists but cannot be written (no master key, say) is
            # the "undecryptable" case the report vocabulary names: it must read
            # differently from a generic failure so the operator knows a
            # credential, not a config, is what stopped the record.
            reason = (REASON_SECRET_UNDECRYPTABLE
                      if error.code == "credential_crypto" else error.code)
            _finish(store, record, result,
                    detail={"code": reason, "batch_id": batch_id})
            results.append(dict(base, result=result, reason=reason))
            continue
        except Exception as error:  # noqa: BLE001 - record and keep going
            reason = _failure_reason(error)
            _finish(store, record, RESULT_FAILED,
                    detail={"error": reason, "batch_id": batch_id})
            results.append(dict(base, result=RESULT_FAILED, reason=reason))
            continue
        mapping = {"legacy_id": record["legacy_id"],
                   "connection_id": created["id"],
                   "version": created.get("version"),
                   # Whether the legacy id survived as the new primary key.
                   # Reported per record because a legacy id that could not be
                   # reused is a reference that will need re-pointing, and that
                   # is a fact the operator has to be able to see.
                   "reused_id": bool(record.get("reuse_id"))
                   and str(created["id"]) == str(record["reuse_id"])}
        _finish(store, record, RESULT_IMPORTED, mapping=mapping,
                detail={"name": record["name"], "batch_id": batch_id,
                        "legacy_id": record["legacy_id"],
                        "reused_id": mapping["reused_id"]})
        results.append(dict(base, result=RESULT_IMPORTED, reason="",
                            connection_id=created["id"],
                            reused_id=mapping["reused_id"]))

    defaults = _reconcile_erp_defaults(
        store, service=service, actor_user_id=actor_user_id,
        records=scan["records"])
    summary: Dict[str, int] = {}
    for item in results:
        summary[item["result"]] = summary.get(item["result"], 0) + 1
    return {"batch_id": batch_id, "store_version": active_store_version(),
            "results": results, "summary": summary, "erp_defaults": defaults}


def _reconcile_erp_defaults(store, *, service, actor_user_id, records
                            ) -> List[Dict[str, Any]]:
    """Apply a legacy ``is_default`` only when exactly one record claims it.

    A legacy set with zero or several defaults is reported, not guessed: the
    design leaves the choice to the administrator ("默认项冲突交由管理员明确选择").
    """
    mappings = _ledger_index(store)
    decisions: List[Dict[str, Any]] = []
    per_scope: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        if record["kind"] != registry.KIND_ERP:
            continue
        per_scope.setdefault(record["scope_key"], []).append(record)
    for key, scope_records in sorted(per_scope.items()):
        defaults = [r for r in scope_records if r["is_default"]]
        tenant_id = scope_records[0]["tenant_id"]
        if not tenant_id:
            continue
        if len(defaults) != 1:
            decisions.append({
                "scope_key": key, "tenant_id": tenant_id, "applied": False,
                "reason": "legacy_default_conflict" if defaults
                else "no_legacy_default", "count": len(defaults)})
            continue
        record = defaults[0]
        row = mappings.get((record["source_hash"], record["scope_key"]))
        if not row or row["result"] != RESULT_IMPORTED:
            decisions.append({"scope_key": key, "tenant_id": tenant_id,
                              "applied": False, "reason": "not_imported"})
            continue
        try:
            mapping = json.loads(row["mapping_json"] or "{}")
            connection_id = mapping.get("connection_id")
            current = service.get_erp_default(
                actor_user_id=actor_user_id, tenant_id=tenant_id)
            if current.get("connection_id") == connection_id:
                decisions.append({"scope_key": key, "tenant_id": tenant_id,
                                  "connection_id": connection_id,
                                  "applied": False, "reason": "unchanged"})
                continue
            if current.get("connection_id"):
                decisions.append({"scope_key": key, "tenant_id": tenant_id,
                                  "connection_id": connection_id,
                                  "applied": False, "reason": "existing_default"})
                continue
            service.set_erp_default(
                actor_user_id=actor_user_id, tenant_id=tenant_id,
                connection_id=connection_id,
                expected_revision=int(current.get("revision") or 0))
            decisions.append({"scope_key": key, "tenant_id": tenant_id,
                              "connection_id": connection_id,
                              "applied": True, "reason": "imported"})
        except ExternalConnectionError as error:
            if error.code == "catalog_version_conflict":
                # A concurrent importer won the CAS; only benign when the
                # default it set is the same connection.
                try:
                    after = service.get_erp_default(
                        actor_user_id=actor_user_id, tenant_id=tenant_id)
                except Exception:  # noqa: BLE001
                    after = {}
                applied = after.get("connection_id") == mapping.get(
                    "connection_id")
                decisions.append({"scope_key": key, "tenant_id": tenant_id,
                                  "applied": applied,
                                  "reason": "concurrent_default"})
            else:
                decisions.append({"scope_key": key, "tenant_id": tenant_id,
                                  "applied": False, "reason": error.code})
        except Exception as error:  # noqa: BLE001
            decisions.append({"scope_key": key, "tenant_id": tenant_id,
                              "applied": False, "reason": type(error).__name__})
    return decisions


# -- verification ------------------------------------------------------------


def verify(identity, *, actor_user_id: Optional[str] = None, sources=None,
           erp_files=(), mcp_files=()) -> Dict[str, Any]:
    """Compare the legacy set with what the new store actually holds.

    Reports counts by ``(kind, scope)``, every unmapped record with its reason,
    every secret whose presence could not be carried over, and a summary
    boolean. ``ok`` is true only when every legacy record is either imported or
    *permanently* unimportable (an unsupported U9 provider, say): an unmapped
    record that could still be imported, or one whose reason blocks the switch
    (:data:`BLOCKING_REASONS` — a missing secret, unknown ownership, a name
    collision), makes ``ok`` false. That keeps this boolean in step with
    :func:`validate_store_version`, so "verify says OK" and "the switch is safe"
    never disagree.
    """
    scan = _scan(identity, sources=sources, erp_files=erp_files,
                 mcp_files=mcp_files, actor_user_id=actor_user_id)
    store = identity._store
    ledger = _ledger_index(store)
    counts: Dict[str, Dict[str, int]] = {}
    unmapped: List[Dict[str, Any]] = []
    secret_gaps: List[Dict[str, Any]] = []
    imported = 0
    for record in scan["records"]:
        kind_scope = counts.setdefault(record["kind"], {})
        kind_scope[record["scope"]] = kind_scope.get(record["scope"], 0) + 1
        row = ledger.get((record["source_hash"], record["scope_key"]))
        if row and row["result"] == RESULT_IMPORTED:
            imported += 1
            try:
                mapping = json.loads(row["mapping_json"] or "{}")
            except ValueError:
                mapping = {}
            carried = _carried_secret_slots(
                store, mapping.get("connection_id", ""))
            for slot in record["secret_present"]:
                if slot not in carried:
                    secret_gaps.append({
                        "source_locator": record["source_locator"],
                        "tenant_id": record["tenant_id"],
                        "slot": slot,
                        "reason": "secret_not_referenced",
                    })
            continue
        reason = record["reason"]
        if not reason and row is not None:
            # The import run recorded why it stopped (a 409, or a secret that
            # could not be written); that is more actionable than "not_imported".
            try:
                detail = json.loads(row["detail_json"] or "{}")
            except ValueError:
                detail = {}
            reason = detail.get("code") or detail.get("error") or "not_imported"
        if not reason:
            reason = "not_imported"
        unmapped.append({
            "source_locator": record["source_locator"],
            "kind": record["kind"],
            "scope": record["scope"],
            "tenant_id": record["tenant_id"],
            "name": record["name"],
            "legacy_id": record["legacy_id"],
            "source_hash": record["source_hash"],
            "importable": record["importable"],
            "reason": reason,
            "reason_detail": dict(record["reason_detail"]),
        })
        for slot in record["secret_present"]:
            secret_gaps.append({
                "source_locator": record["source_locator"],
                "tenant_id": record["tenant_id"],
                "slot": slot,
                "reason": reason,
            })
    ok = not any(item["importable"] or item["reason"] in BLOCKING_REASONS
                 for item in unmapped)
    return {
        "store_version": active_store_version(),
        "counts_by_kind_scope": counts,
        "total": len(scan["records"]),
        "imported": imported,
        "unmapped": unmapped,
        "secret_gaps": secret_gaps,
        "ok": ok,
        "summary": {
            "legacy_total": len(scan["records"]),
            "imported": imported,
            "unmapped": len(unmapped),
            "unimportable": sum(1 for r in unmapped if not r["importable"]),
            "secret_gaps": len(secret_gaps),
        },
    }


def _carried_secret_slots(store, connection_id: str) -> set:
    if not connection_id:
        return set()
    try:
        rows = store.execute(
            "SELECT slot FROM external_connection_secret_refs WHERE"
            " connection_id=?", (connection_id,))
    except Exception:  # noqa: BLE001
        return set()
    return {row["slot"] for row in rows}


# -- runtime read resolution -------------------------------------------------


def _mcp_file_entries(path: str) -> List[Dict[str, Any]]:
    """The normalized entries in one ``mcp.json``, and nothing else.

    Mirrors ``agent.tools.tool_manager``'s shape rules (``mcpServers`` dict,
    ``mcp_servers`` list, or the raw dict) so the resolver and the loader cannot
    disagree about what an entry is — which would make the "did the control plane
    take this entry over?" answer name a server the loader never had.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, Mapping):
        return []
    raw = data.get("mcpServers") or data.get("mcp_servers") or data
    return _normalize_mcp_entries(raw)


def _file_digest(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def _tenant_mappings(store, locator: str) -> List[str]:
    """The tenant ids an operator mapped ``locator`` to, from the ledger.

    The ledger is the only record of the mapping: an ``mcp.json`` lives in one
    workspace and names no tenant itself, so the migration required an explicit
    tenant before it would import anything out of it (``ownership_unknown``
    otherwise). Deriving the tenant from the ledger is therefore not a guess —
    it is reading back the decision that was already made.
    """
    try:
        rows = store.execute(
            "SELECT DISTINCT scope_key FROM external_connection_migrations"
            " WHERE source_locator LIKE ?", ("%s#%%" % locator,))
    except Exception:  # noqa: BLE001 - a missing ledger is an empty mapping
        return []
    tenants = []
    for row in rows:
        key = str(row["scope_key"] or "")
        prefix = "%s:" % registry.SCOPE_TENANT
        if key.startswith(prefix) and key[len(prefix):] not in ("", "?"):
            tenants.append(key[len(prefix):])
    return sorted(set(tenants))


def _connection_head(store, connection_id: str) -> Optional[Mapping[str, Any]]:
    if not connection_id:
        return None
    try:
        rows = store.execute(
            "SELECT id, version, enabled, deleted_at FROM external_connections"
            " WHERE id=?", (connection_id,))
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None


def resolve_mcp_servers(identity, *, path: str) -> Dict[str, Any]:
    """Which ``mcp.json`` entries the runtime should still register from the file.

    The scope-aware half of ``ToolManager``'s configuration read. An ``mcp.json``
    entry was trusted as configuration; after the migration the same server is a
    control-plane row reached through the actor's grants, and it must stop being
    registered from the file at the moment the *scope it was imported into* stops
    reading the legacy store. Two live identities for one server is not a cache
    problem: the file copy is the one with no authorization gate behind it.

    Returns:

    * ``servers`` — the entries still served from the file, in file order, with
      every original key (notably ``tool_name_prefix``) intact;
    * ``migrated`` — one item per entry taken over, naming the ``scope_key``, the
      connection and the store version that did it, so a caller can say *why* a
      server that is still in ``mcp.json`` is not in the tool list;
    * ``source`` — ``"legacy"`` when the file is still the whole answer, ``"new"``
      when at least one entry's answer now comes from the control plane;
    * ``marker`` — a string that changes when any of the inputs to that decision
      changes (the file's bytes, a scope's store version, an owning connection's
      version or enabled flag). ``ToolManager`` folds it into the signature
      ``refresh_mcp_if_changed`` compares, so a store switch or a console edit is
      noticed without the file changing at all.

    An entry imported into *any* owning scope is dropped: with several tenants
    mapped to one file there is no per-tenant answer to give, and keeping a copy
    for the tenant that has not switched yet would keep the ungated identity
    alive for the tenant that has.
    """
    entries = _mcp_file_entries(path)
    marker_parts = [_file_digest(path)]
    store = identity._store
    tenants = _tenant_mappings(store, path)
    ledger = _ledger_index(store)
    migrated: List[Dict[str, Any]] = []
    taken: set = set()

    for tenant_id in tenants:
        source = LegacySource(locator=path, format=MCP_FORMAT,
                              tenant_id=tenant_id)
        key = scope_key(source.scope, tenant_id)
        marker_parts.append("%s=%s" % (key, active_store_version(key)))
        for index, raw in enumerate(entries):
            record, _ = _normalize_mcp(source, index, raw)
            if not record["importable"]:
                continue
            row = ledger.get((record["source_hash"], key))
            if row is None or row["result"] != RESULT_IMPORTED:
                continue
            if active_store_version(key) == STORE_LEGACY:
                continue
            try:
                mapping = json.loads(row["mapping_json"] or "{}")
            except ValueError:
                mapping = {}
            connection_id = str(mapping.get("connection_id") or "")
            head = _connection_head(store, connection_id)
            version = int(head["version"]) if head else 0
            enabled = bool(head["enabled"]) if head else False
            marker_parts.append("%s/%s=%s:%s:%s" % (
                key, record["name"], connection_id, version, enabled))
            if record["name"] in taken:
                continue
            taken.add(record["name"])
            migrated.append({
                "name": record["name"],
                "scope_key": key,
                "tenant_id": tenant_id,
                "connection_id": connection_id,
                "connection_version": version,
                "store_version": active_store_version(key),
            })

    servers = [entry for entry in entries
               if str(entry.get("name") or "") not in taken]
    return {
        "source": "new" if migrated else "legacy",
        "servers": servers,
        "migrated": migrated,
        "marker": hashlib.sha256(
            "|".join(marker_parts).encode("utf-8")).hexdigest()[:32],
        "file_digest": marker_parts[0],
    }


def _new_store_connections(identity, *, kind: str, scope: str,
                           tenant_id: Optional[str]) -> List[Dict[str, Any]]:
    """Every non-deleted control-plane row for this scope.

    Disabled rows are included on purpose: a named resolve must be able to
    answer ``connection_disabled`` rather than ``connection_not_found``, and a
    management listing with ``enabled_only=False`` must still show them. Runtime
    consumers that only want live connections filter on ``enabled`` themselves
    (see ``erp_scene.list_erp_connections``).
    """
    rows = identity._store.execute(
        "SELECT id, kind, scope, tenant_id, name, config_json, enabled, version"
        " FROM external_connections WHERE kind=? AND scope=? AND deleted_at IS"
        " NULL" + (" AND tenant_id=?" if tenant_id else ""),
        (kind, scope, tenant_id) if tenant_id else (kind, scope))
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row["config_json"] or "{}")
        except ValueError:
            config = {}
        out.append({
            "origin": "new",
            "connection_id": row["id"],
            "kind": row["kind"],
            "scope": row["scope"],
            "tenant_id": row["tenant_id"],
            "name": row["name"],
            "config": config,
            "version": int(row["version"]),
            "enabled": bool(row["enabled"]),
        })
    return out


def read_effective_connections(identity, *, kind: str, scope: str,
                               tenant_id: Optional[str] = None,
                               sources=None, erp_files=(),
                               mcp_files=()) -> Dict[str, Any]:
    """The connections a runtime consumer should see for one scope.

    The single place ``store_version`` is applied:

    * ``legacy`` — the legacy records are returned; the new control plane is
      readable in the console but not authoritative for the runtime.
    * ``dual`` — the new store wins when it has an answer; a no-answer new
      store falls back to the legacy records so nothing silently disappears,
      and the disagreement is reported.
    * ``new`` — only the new store. Taking this value while importable legacy
      records are unimported is refused up front by
      :func:`assert_store_version_safe`; here the honest empty answer is
      returned rather than falling back to the legacy file (a failure must
      fail closed, it must not silently read JSON).

    The runtime layer consumes this; it deliberately does not import the
    migration tool from a request path, it calls this resolver.
    """
    key = scope_key(scope, tenant_id)
    version = active_store_version(key)
    new_rows = _new_store_connections(
        identity, kind=kind, scope=scope, tenant_id=tenant_id)
    legacy = _legacy_records_for(identity, kind=kind, scope=scope,
                                 tenant_id=tenant_id, sources=sources,
                                 erp_files=erp_files, mcp_files=mcp_files)
    ledger = _ledger_index(identity._store)
    pending = [
        record for record in legacy
        if record["importable"]
        and not (ledger.get((record["source_hash"], record["scope_key"]))
                 and ledger[(record["source_hash"], record["scope_key"])]
                 ["result"] == RESULT_IMPORTED)
    ]

    def _legacy_view() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for record in legacy:
            config = dict(record.get("_config", {}))
            # ``is_default`` lives on the record, not in the validated config
            # (the registry has no such field). Carry it into the view so a
            # legacy-mode runtime can still answer "what is the default?" the
            # same way the JSON file did.
            if record.get("is_default"):
                config["is_default"] = True
            out.append({
                "origin": "legacy",
                "connection_id": "",
                "legacy_id": record["legacy_id"],
                "kind": record["kind"],
                "scope": record["scope"],
                "tenant_id": record["tenant_id"],
                "name": record["name"],
                "config": config,
                "version": 0,
                "enabled": True,
            })
        return out

    result = {
        "store_version": version,
        "scope_key": key,
        "connections": [],
        "disagreements": [],
    }
    if version == STORE_LEGACY:
        result["source"] = "legacy"
        result["connections"] = _legacy_view()
    elif version == STORE_NEW:
        result["source"] = "new"
        result["connections"] = new_rows
        if not new_rows and pending:
            result["disagreements"] = [
                _disagreement(record, "new_store_empty") for record in pending]
    else:
        if new_rows:
            result["source"] = "new"
            result["connections"] = new_rows
            result["disagreements"] = [
                _disagreement(record, "not_imported") for record in pending]
        else:
            result["source"] = "legacy"
            result["connections"] = _legacy_view()
            result["disagreements"] = [
                _disagreement(record, "new_store_empty") for record in pending]
    return result


def _disagreement(record, reason: str) -> Dict[str, Any]:
    return {
        "source_locator": record["source_locator"],
        "kind": record["kind"],
        "tenant_id": record["tenant_id"],
        "name": record["name"],
        "legacy_id": record["legacy_id"],
        "source_hash": record["source_hash"],
        "reason": reason,
    }


def _legacy_records_for(identity, *, kind: str, scope: str,
                        tenant_id: Optional[str], sources=None,
                        erp_files=(), mcp_files=()) -> List[Dict[str, Any]]:
    if sources is None:
        sources = discover_legacy_sources(
            identity, erp_files=erp_files, mcp_files=mcp_files)
    scan = _scan(identity, sources=sources)
    return [
        record for record in scan["records"]
        if record["kind"] == kind and record["scope"] == scope
        and record["tenant_id"] == tenant_id
    ]


# -- operator-facing state ---------------------------------------------------


def store_version_state(identity=None, *, sources=None, erp_files=(),
                        mcp_files=()) -> Dict[str, Any]:
    """An operator-readable snapshot of the switch and what is pending.

    ``pending`` is empty when ``identity`` is omitted (the switch value alone is
    still reportable without a database).
    """
    block = _config_block()
    configured = (block.get("store_version") is not None
                  or block.get("store_versions") is not None)
    state: Dict[str, Any] = {
        "store_version": active_store_version(),
        "safe_default": SAFE_STORE_VERSION,
        "configured": configured,
        "allowed": list(STORE_VERSIONS),
        "unsafe_reason": "",
        "pending": [],
        "blocking": [],
        "ok": True,
    }
    if identity is None:
        return state
    report = validate_store_version(
        identity, sources=sources, erp_files=erp_files, mcp_files=mcp_files)
    state["unsafe_reason"] = report["reason"]
    state["pending"] = report["pending"]
    state["blocking"] = report["blocking"]
    state["ok"] = report["ok"]
    return state
