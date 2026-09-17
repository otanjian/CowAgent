# encoding:utf-8
"""Connection types: the finite catalogue, scope rules and config validation.

Why this is not spread across the handlers
------------------------------------------
Every entry point — the console's type picker, the four CRUD surfaces, the
loader used by the (later) type adapters — has to agree on what a valid MCP /
ERP / OA / email configuration *is*. A per-handler copy is how "the picker
offers a field the save rejects" happens, and the spec makes the type
catalogue itself a server-side fact ("连接类型与归属创建后不可改").

What a connection's non-secret configuration may contain is therefore declared
here, once:

* :data:`TYPE_SPECS` — which ``(kind, scope)`` combinations exist, the config
  keys each accepts, and the secret slots a kind can carry;
* :func:`validate_config` — the normalizer/validator, returning the canonical
  config that is what actually gets stored. It rejects unknown keys, any key
  that looks like a secret (those must use the credential mechanism, not
  ``config_json``), malformed URLs (userinfo is refused, so a URL can never
  smuggle a credential) and out-of-range ports/timeouts.
* :func:`secret_slots` — the slots a kind may reference, so a client cannot
  invent an arbitrary slot name or point at an arbitrary credential id.

Readiness is deliberately **not** claimed here: :data:`READINESS` reports the
capability classes this build serves, and at this stage every type is
``configure`` only. Test and execute classes arrive with their own evidence
(design §7) — a page must never present a type as testable because a form
exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from integrations.external.errors import invalid

# -- kind / scope vocabulary -------------------------------------------------

KIND_MCP = "mcp"
KIND_ERP = "erp"
KIND_OA = "oa"
KIND_EMAIL = "email"

KINDS: Tuple[str, ...] = (KIND_MCP, KIND_ERP, KIND_OA, KIND_EMAIL)

SCOPE_PLATFORM = "platform"
SCOPE_TENANT = "tenant"
SCOPE_PERSONAL = "personal"

SCOPES: Tuple[str, ...] = (SCOPE_PLATFORM, SCOPE_TENANT, SCOPE_PERSONAL)

#: Secret slot names a kind may own. A slot is referenced by name (never by a
#: client-chosen credential id), and a kind that has no slot cannot be given
#: one — which is what stops a request pointing a platform connection at a
#: tenant credential.
SLOTS: Dict[str, FrozenSet[str]] = {
    KIND_MCP: frozenset({"header", "env", "oauth"}),
    KIND_ERP: frozenset({"password"}),
    KIND_OA: frozenset({"password", "app_secret"}),
    KIND_EMAIL: frozenset({"imap_password", "smtp_password"}),
}

#: Slots that must be present (non-empty) for a connection of this kind to be
#: usable. They are not required to *save* a draft configuration (the console
#: saves and tests separately), but a saved connection missing them reports
#: ``secret_missing`` in its capability projection.
REQUIRED_SLOTS: Dict[str, FrozenSet[str]] = {
    KIND_MCP: frozenset(),
    KIND_ERP: frozenset({"password"}),
    KIND_OA: frozenset({"password"}),
    KIND_EMAIL: frozenset(),
}

#: The capability classes the server currently serves per kind.
#: ``configure`` only: test/execute open with the G2–G4 evidence that this
#: repository has not produced (design §7). The projection reports the reason
#: rather than pretending the class is available.
READINESS_OPEN: Dict[str, FrozenSet[str]] = {
    KIND_MCP: frozenset({"configure"}),
    KIND_ERP: frozenset({"configure"}),
    KIND_OA: frozenset({"configure"}),
    KIND_EMAIL: frozenset({"configure"}),
}

READINESS_REASON = {
    KIND_MCP: "awaiting_mcp_test_environment",
    KIND_ERP: "awaiting_sap_test_environment",
    KIND_OA: "awaiting_oa_test_environment",
    KIND_EMAIL: "awaiting_mail_test_environment",
}

#: Kinds whose production adapter is not a real integration. Kept explicit so
#: the migration of a legacy U9/金蝶 row can be labelled and disabled instead of
#: being offered as a working connection type (spec ``erp-connection-integration``).
UNSUPPORTED_ERP_PROVIDERS = frozenset({"u9", "kingdee", "k3", "eas"})

_MAX_STRING = 512
_MAX_LIST = 64
_MAX_URL = 1024
_SECRETISH = re.compile(r"(pass|secret|token|key|credential|authorization)", re.I)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_EMAIL_ADDR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class TypeSpec:
    """One connection type: where it may live, what it accepts, what it owns."""

    kind: str
    label: str
    label_key: str
    scopes: FrozenSet[str]
    config_keys: FrozenSet[str]
    secret_slots: FrozenSet[str]
    #: Keys a client may never move after creation (ownership is server-derived).
    immutable_keys: FrozenSet[str] = field(default_factory=frozenset)


_SPECS: Tuple[TypeSpec, ...] = (
    TypeSpec(
        kind=KIND_MCP,
        label="MCP",
        label_key="ext_conn_type_mcp",
        scopes=frozenset({SCOPE_PLATFORM, SCOPE_TENANT}),
        config_keys=frozenset({
            "transport", "url", "auth", "header_name", "command", "args",
            "env_keys", "oauth_provider",
        }),
        secret_slots=SLOTS[KIND_MCP],
    ),
    TypeSpec(
        kind=KIND_ERP,
        label="ERP (SAP)",
        label_key="ext_conn_type_erp",
        scopes=frozenset({SCOPE_TENANT}),
        config_keys=frozenset({
            "provider", "ashost", "sysnr", "client", "user", "lang",
            "base_url", "verify_ssl", "timeout",
        }),
        secret_slots=SLOTS[KIND_ERP],
    ),
    TypeSpec(
        kind=KIND_OA,
        label="OA",
        label_key="ext_conn_type_oa",
        scopes=frozenset({SCOPE_TENANT}),
        config_keys=frozenset({
            "base_url", "username", "tenant_key", "custom_page_config_id",
            "app_key", "corp_id",
        }),
        secret_slots=SLOTS[KIND_OA],
    ),
    TypeSpec(
        kind=KIND_EMAIL,
        label="Email",
        label_key="ext_conn_type_email",
        scopes=frozenset({SCOPE_PERSONAL}),
        config_keys=frozenset({
            "imap", "smtp", "attachment_dirs",
        }),
        secret_slots=SLOTS[KIND_EMAIL],
    ),
)

TYPE_SPECS: Dict[str, TypeSpec] = {spec.kind: spec for spec in _SPECS}


def spec_for(kind: str) -> TypeSpec:
    spec = TYPE_SPECS.get(str(kind or "").strip())
    if spec is None:
        raise invalid("unknown connection type", code="unknown_type")
    return spec


def scopes_for(kind: str) -> FrozenSet[str]:
    return spec_for(kind).scopes


def secret_slots(kind: str) -> FrozenSet[str]:
    return spec_for(kind).secret_slots


def validate_scope(kind: str, scope: str) -> None:
    """Refuse a ``(kind, scope)`` pair the product does not have."""
    if scope not in spec_for(kind).scopes:
        raise invalid(
            "connection type %r is not available in scope %r" % (kind, scope),
            code="unsupported_scope")


# -- config normalization ----------------------------------------------------

def _string(config: Mapping[str, Any], key: str, *, required: bool = False,
            default: str = "", max_len: int = _MAX_STRING) -> str:
    raw = config.get(key, None)
    if raw is None:
        if required and not default:
            raise invalid("%s is required" % key, code="field_required",
                          fields={key: "required"})
        return default
    if not isinstance(raw, str):
        raise invalid("%s must be a string" % key, code="field_type",
                      fields={key: "type"})
    value = raw.strip()
    if len(value) > max_len:
        raise invalid("%s is too long" % key, code="field_too_long",
                      fields={key: "too_long"})
    if required and not value:
        raise invalid("%s is required" % key, code="field_required",
                      fields={key: "required"})
    return value


def _boolean(config: Mapping[str, Any], key: str, *, default: bool) -> bool:
    raw = config.get(key, None)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise invalid("%s must be a boolean" % key, code="field_type",
                  fields={key: "type"})


def _integer(config: Mapping[str, Any], key: str, *, default: int,
             low: int, high: int) -> int:
    raw = config.get(key, None)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise invalid("%s must be an integer" % key, code="field_type",
                      fields={key: "type"})
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise invalid("%s must be an integer" % key, code="field_type",
                      fields={key: "type"})
    if value < low or value > high:
        raise invalid("%s is out of range" % key, code="field_range",
                      fields={key: "range"})
    return value


def _string_list(config: Mapping[str, Any], key: str, *, default=(),
                 max_items: int = _MAX_LIST, pattern: Optional[re.Pattern] = None,
                 item_max: int = _MAX_STRING) -> List[str]:
    raw = config.get(key, None)
    if raw is None:
        return list(default)
    if not isinstance(raw, (list, tuple)):
        raise invalid("%s must be a list" % key, code="field_type",
                      fields={key: "type"})
    if len(raw) > max_items:
        raise invalid("%s has too many entries" % key, code="field_too_long",
                      fields={key: "too_long"})
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise invalid("%s entries must be strings" % key, code="field_type",
                          fields={key: "type"})
        value = item.strip()
        if not value or len(value) > item_max:
            raise invalid("%s entry is invalid" % key, code="field_invalid",
                          fields={key: "invalid"})
        if pattern is not None and not pattern.match(value):
            raise invalid("%s entry is invalid" % key, code="field_invalid",
                          fields={key: "invalid"})
        out.append(value)
    return out


def _http_url(value: str, key: str, *, allow_path: bool = True) -> str:
    """A remote address with no embedded credentials.

    ``userinfo`` is refused outright rather than stripped: a URL that carries
    ``user:pass@`` is a credential in a non-secret field, and the spec forbids
    exactly that ("源 URL 禁止 userinfo").
    """
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise invalid("%s must be an http(s) URL" % key, code="field_invalid",
                      fields={key: "invalid"})
    if parsed.username or parsed.password:
        raise invalid("%s must not embed credentials" % key,
                      code="field_invalid", fields={key: "invalid"})
    if not allow_path and parsed.path not in ("", "/"):
        raise invalid("%s must not carry a path" % key, code="field_invalid",
                      fields={key: "invalid"})
    return value


def _reject_unknown_config_keys(config: Mapping[str, Any], allowed: Iterable[str],
                                kind: str) -> None:
    allowed = set(allowed)
    for key in config:
        name = str(key)
        if name in allowed:
            continue
        if _SECRETISH.search(name):
            # A secret belongs in the credential mechanism, never in the
            # non-secret projection; refusing (rather than dropping) is what
            # makes a client's mistake visible instead of silently unset.
            raise invalid(
                "%s must be stored as a secret, not in the configuration" % name,
                code="secret_in_config", fields={name: "secret_in_config"})
        raise invalid("unknown configuration field %r for %s" % (name, kind),
                      code="unknown_field", fields={name: "unknown"})


def _validate_mcp(config: Mapping[str, Any]) -> Dict[str, Any]:
    spec = spec_for(KIND_MCP)
    _reject_unknown_config_keys(config, spec.config_keys, KIND_MCP)
    transport = _string(config, "transport", required=True)
    if transport not in ("stdio", "sse", "streamable_http"):
        raise invalid("transport must be stdio, sse or streamable_http",
                      code="field_invalid", fields={"transport": "invalid"})
    out: Dict[str, Any] = {"transport": transport}
    if transport == "stdio":
        for forbidden in ("url", "auth", "header_name", "oauth_provider"):
            if config.get(forbidden) not in (None, ""):
                raise invalid("%s is not used by a stdio connection" % forbidden,
                              code="field_invalid", fields={forbidden: "not_allowed"})
        out["command"] = _string(config, "command", required=True)
        out["args"] = _string_list(config, "args")
        out["env_keys"] = _string_list(config, "env_keys", pattern=_ENV_NAME)
        return out
    for forbidden in ("command", "args", "env_keys"):
        if config.get(forbidden) not in (None, "", []):
            raise invalid("%s is not used by a remote connection" % forbidden,
                          code="field_invalid", fields={forbidden: "not_allowed"})
    out["url"] = _http_url(_string(config, "url", required=True, max_len=_MAX_URL),
                           "url")
    auth = _string(config, "auth", default="none") or "none"
    if auth not in ("none", "header", "oauth"):
        raise invalid("auth must be none, header or oauth", code="field_invalid",
                      fields={"auth": "invalid"})
    out["auth"] = auth
    if auth == "header":
        out["header_name"] = _string(config, "header_name", required=True)
    elif config.get("header_name") not in (None, ""):
        raise invalid("header_name requires auth=header", code="field_invalid",
                      fields={"header_name": "not_allowed"})
    if auth == "oauth":
        out["oauth_provider"] = _string(config, "oauth_provider", default="generic")
    return out


def _validate_erp(config: Mapping[str, Any]) -> Dict[str, Any]:
    spec = spec_for(KIND_ERP)
    _reject_unknown_config_keys(config, spec.config_keys, KIND_ERP)
    provider = _string(config, "provider", required=True)
    if provider in UNSUPPORTED_ERP_PROVIDERS:
        raise invalid(
            "provider %r has no production adapter in this build" % provider,
            code="unsupported_provider", fields={"provider": "unsupported"})
    if provider not in ("rfc", "adt_sql"):
        raise invalid("provider must be rfc or adt_sql", code="field_invalid",
                      fields={"provider": "invalid"})
    out: Dict[str, Any] = {"provider": provider}
    if provider == "rfc":
        for key in ("base_url", "verify_ssl", "timeout"):
            if config.get(key) not in (None, ""):
                raise invalid("%s is not used by an RFC connection" % key,
                              code="field_invalid", fields={key: "not_allowed"})
        out["ashost"] = _string(config, "ashost", required=True)
        out["sysnr"] = _string(config, "sysnr", required=True)
        out["client"] = _string(config, "client", required=True)
        out["user"] = _string(config, "user", required=True)
        out["lang"] = _string(config, "lang", default="EN") or "EN"
        return out
    for key in ("ashost", "sysnr", "lang"):
        if config.get(key) not in (None, ""):
            raise invalid("%s is not used by an ADT SQL connection" % key,
                          code="field_invalid", fields={key: "not_allowed"})
    out["base_url"] = _http_url(
        _string(config, "base_url", required=True, max_len=_MAX_URL), "base_url")
    out["client"] = _string(config, "client", required=True)
    out["user"] = _string(config, "user", required=True)
    out["verify_ssl"] = _boolean(config, "verify_ssl", default=True)
    out["timeout"] = _integer(config, "timeout", default=30, low=1, high=300)
    return out


def _validate_oa(config: Mapping[str, Any]) -> Dict[str, Any]:
    spec = spec_for(KIND_OA)
    _reject_unknown_config_keys(config, spec.config_keys, KIND_OA)
    out: Dict[str, Any] = {
        "base_url": _http_url(
            _string(config, "base_url", required=True, max_len=_MAX_URL), "base_url"),
        "username": _string(config, "username", required=True),
    }
    for key in ("tenant_key", "custom_page_config_id", "app_key", "corp_id"):
        value = _string(config, key)
        if value:
            out[key] = value
    # A site URL and account are what "登录型" means; the OpenAPI fields stay
    # optional so a login-only connection can be saved, and the capability
    # projection reports the OpenAPI class as not ready rather than refusing the
    # save (spec: 登录正常但 OpenAPI 未配置).
    return out


def _validate_mail_side(config: Mapping[str, Any], key: str, *,
                        default_port: int, needs_from: bool) -> Dict[str, Any]:
    raw = config.get(key)
    if raw is None:
        return {"enabled": False}
    if not isinstance(raw, Mapping):
        raise invalid("%s must be an object" % key, code="field_type",
                      fields={key: "type"})
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise invalid("%s.enabled must be a boolean" % key, code="field_type",
                      fields={key: "type"})
    if not enabled:
        return {"enabled": False}
    out: Dict[str, Any] = {
        "enabled": True,
        "host": _string(raw, "host", required=True),
        "port": _integer(raw, "port", default=default_port, low=1, high=65535),
        "user": _string(raw, "user", required=True),
        "reject_unauthorized": _boolean(raw, "reject_unauthorized", default=True),
    }
    if needs_from:
        from_addr = _string(raw, "from_addr", required=True)
        if not _EMAIL_ADDR.match(from_addr):
            raise invalid("from_addr must be an email address",
                          code="field_invalid", fields={"from_addr": "invalid"})
        out["from_addr"] = from_addr
        tls_mode = _string(raw, "tls_mode", default="implicit_tls") or "implicit_tls"
        if tls_mode not in ("implicit_tls", "starttls", "none"):
            raise invalid("tls_mode is invalid", code="field_invalid",
                          fields={"tls_mode": "invalid"})
        out["tls_mode"] = tls_mode
    else:
        out["tls"] = _boolean(raw, "tls", default=True)
        out["mailbox"] = _string(raw, "mailbox", default="INBOX") or "INBOX"
    allowed = {"enabled", "host", "port", "user", "reject_unauthorized"} | (
        {"from_addr", "tls_mode"} if needs_from else {"tls", "mailbox"})
    for name in raw:
        if str(name) not in allowed:
            raise invalid("unknown %s field %r" % (key, name), code="unknown_field",
                          fields={str(name): "unknown"})
    return out


def _validate_email(config: Mapping[str, Any]) -> Dict[str, Any]:
    spec = spec_for(KIND_EMAIL)
    _reject_unknown_config_keys(config, spec.config_keys, KIND_EMAIL)
    imap = _validate_mail_side(config, "imap", default_port=993, needs_from=False)
    smtp = _validate_mail_side(config, "smtp", default_port=465, needs_from=True)
    if not imap.get("enabled") and not smtp.get("enabled"):
        raise invalid("at least one of imap or smtp must be enabled",
                      code="no_protocol", fields={"imap": "required",
                                                  "smtp": "required"})
    dirs = _string_list(config, "attachment_dirs")
    if len(dirs) > 8:
        raise invalid("attachment_dirs has too many entries", code="field_too_long",
                      fields={"attachment_dirs": "too_long"})
    for entry in dirs:
        if entry.startswith(("/", "\\")) or ".." in entry.replace("\\", "/").split("/"):
            raise invalid("attachment_dirs must be relative and inside the workspace",
                          code="field_invalid", fields={"attachment_dirs": "invalid"})
    return {"imap": imap, "smtp": smtp, "attachment_dirs": dirs}


_VALIDATORS = {
    KIND_MCP: _validate_mcp,
    KIND_ERP: _validate_erp,
    KIND_OA: _validate_oa,
    KIND_EMAIL: _validate_email,
}


def validate_config(kind: str, config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return the canonical non-secret configuration for ``kind``.

    Raises :class:`ExternalConnectionError` (400, code ``invalid`` and friends)
    on anything the type does not accept. The returned dict is what gets
    persisted, so a caller can never store an unvalidated key.
    """
    spec_for(kind)
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise invalid("config must be an object", code="field_type",
                      fields={"config": "type"})
    return _VALIDATORS[kind](config)


def required_slots(kind: str, config: Mapping[str, Any]) -> FrozenSet[str]:
    """Slots a *saved* connection of this kind needs to be usable.

    MCP needs a secret only for the auth mode it selected; email needs the
    password of each protocol it enabled; ERP/OA always need their account
    password. A missing slot is reported, not a save refusal — the console
    saves and tests separately.
    """
    kind = str(kind or "")
    if kind == KIND_MCP:
        auth = str((config or {}).get("auth") or "none")
        return frozenset({"header"}) if auth == "header" else frozenset()
    if kind == KIND_EMAIL:
        needed = set()
        imap = (config or {}).get("imap") or {}
        smtp = (config or {}).get("smtp") or {}
        if imap.get("enabled"):
            needed.add("imap_password")
        if smtp.get("enabled"):
            needed.add("smtp_password")
        return frozenset(needed)
    return REQUIRED_SLOTS.get(kind, frozenset())


def capability_projection(kind: str, *, config: Mapping[str, Any],
                          has_secret: Mapping[str, bool]) -> Dict[str, Any]:
    """What a connection of this kind can do right now, and why not.

    ``configure`` is served everywhere; ``test``/``execute`` are reported as
    unavailable with the deployment reason, so a console renders the real state
    instead of a button that always fails.
    """
    spec = spec_for(kind)
    open_classes = sorted(READINESS_OPEN.get(kind, frozenset()))
    missing = sorted(slot for slot in required_slots(kind, config or {})
                     if not has_secret.get(slot))
    return {
        "kind": kind,
        "scopes": sorted(spec.scopes),
        "open": open_classes,
        "test_available": "test" in open_classes,
        "execute_available": "execute" in open_classes,
        "unavailable_reason": READINESS_REASON.get(kind, "not_implemented"),
        "missing_secret_slots": missing,
    }
