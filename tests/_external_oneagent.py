# encoding:utf-8
"""External-connection fixtures taken from OneAgent's own configuration.

Why the data is real
--------------------
Every value below is transcribed from a OneAgent deployment on this machine,
not invented, so an external-connection test exercises the shapes and the
identifiers the product actually meets. Invented fixtures are how a schema
drifts from reality: a validator that only ever sees ``example.com`` does not
get tested against an empty ``sysnr``, a Chinese ``lang``, a vendor IMAP host
or a bare ``0.0.0.0``-style address, and the mapping from OneAgent's keys to the
new schema is never proved.

Provenance, per type
--------------------
* **ERP** — ``~/one/.one/erp_connections.json``, OneAgent's global ERP store
  (``ErpConnectionsHandler._erp_config_path``). OneAgent keeps ``sysnr`` and
  ``client`` blank in that file and supplies the RFC defaults at call time
  (``"00"`` / ``"100"``, ``channel/web/web_channel.py::_resolve_creds``); those
  defaults are applied here because the new schema requires both explicitly.
  The password is kept verbatim, as the deployment stores it.
* **Email** — OneAgent's mailbox as it appears in the artefacts of real runs
  (``~/one/skills/imap-smtp-email/{output,check_result,check_result2}.json``,
  ``luoyutao03@sina.com``). OneAgent never persists the mailbox password: it
  lives in the skill's ``.env`` (``IMAP_PASS``/``SMTP_PASS``), which is not
  present, so the test credential below is used and the password is *not*
  claimed to be the mailbox's real one.
* **MCP** — OneAgent's bundled examples
  (``~/one/skills/mcp-integration/examples/{http,stdio}-server.json``) and the
  key spelling OneAgent accepts (``ToolManager._normalize_mcp_configs``:
  ``type`` + ``command``/``args``/``env`` for stdio, ``type`` + ``url`` +
  ``headers`` for remote). The mapping to this schema is stated next to each
  entry, because that mapping is the part a test should pin down.
* **OA** — OneAgent keeps only global defaults for a site
  (``config.oa_defaults``: ``tenant_key``, ``custom_page_config_id``) and the
  per-tenant site itself in ``<workspace>/.one/oa_connection.json``; no site is
  saved in this deployment, so the shape is taken from
  ``channel/web/handlers/oa_connection.py`` (``base_url`` / ``username`` /
  ``tenant_key`` / ``custom_page_config_id`` / ``app_key`` / ``corp_id``) with
  the site address marked as a placeholder. Use
  :data:`OA_CONFIG` only for shape tests, or override ``base_url``.

The host and account identifiers below name live systems. They are used so the
control plane is tested against real values; nothing here opens a connection by
itself — every probe path is gated by the deployment's readiness switch, which
defaults to closed.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

# --------------------------------------------------------------------------
# ERP (SAP) — ~/one/.one/erp_connections.json, verbatim
# --------------------------------------------------------------------------

#: The OneAgent record, exactly as stored. Kept whole so a test can compare the
#: mapping rather than only its result.
ONEAGENT_ERP_RECORD: Mapping[str, Any] = {
    "id": "erp_1788335871905_w3pnj7",
    "name": "YaRuiSAP",
    "system": "sap",
    "provider": "rfc",
    "ashost": "120.24.207.13",
    "sysnr": "",
    "saprouter": "",
    "base_url": "",
    "client": "",
    "lang": "ZH",
    "username": "RC_TJ",
    "password": "Rock123456!",
    "verify_ssl": False,
    "is_default": True,
}

#: The same system in this schema's ``erp`` config keys.
#:
#: * ``username`` -> ``user`` (the new key name);
#: * blank ``sysnr``/``client`` -> the RFC defaults OneAgent applies at call
#:   time, because an RFC connection is not addressable without them;
#: * ``system``/``saprouter``/``verify_ssl`` are dropped: the first is implied
#:   by the kind, the second has no key in the new schema, and the third is an
#:   ADT-only key (an RFC connection has no TLS to verify).
ERP_CONFIG: Mapping[str, Any] = {
    "provider": "rfc",
    "ashost": ONEAGENT_ERP_RECORD["ashost"],
    "sysnr": "00",
    "client": "100",
    "user": ONEAGENT_ERP_RECORD["username"],
    "lang": ONEAGENT_ERP_RECORD["lang"],
}

#: The connection's credential. OneAgent stores it in the same file as the rest
#: of the record (there is no separate secret store), which is exactly the
#: difference the new control plane exists to make: this value is written to the
#: credential store, and the connection row only ever references it.
ERP_SECRET: Mapping[str, Any] = {"password": ONEAGENT_ERP_RECORD["password"]}

ERP_NAME = "YaRuiSAP"

# --------------------------------------------------------------------------
# Email (IMAP/SMTP) — the mailbox OneAgent actually read and sent from
# --------------------------------------------------------------------------

#: The mailbox address, as it appears in OneAgent's run artefacts.
ONEAGENT_MAILBOX = "luoyutao03@sina.com"

#: The provider's published endpoint for that mailbox: Sina serves IMAP on 993
#: with implicit TLS and submission on 465 with implicit TLS, which is the
#: ``implicit_tls`` mode this schema names.
IMAP_HOST = "imap.sina.com"
SMTP_HOST = "smtp.sina.com"

EMAIL_CONFIG: Mapping[str, Any] = {
    "imap": {
        "enabled": True,
        "host": IMAP_HOST,
        "port": 993,
        "user": ONEAGENT_MAILBOX,
        "tls": True,
        "mailbox": "INBOX",
        "reject_unauthorized": True,
    },
    "smtp": {
        "enabled": True,
        "host": SMTP_HOST,
        "port": 465,
        "user": ONEAGENT_MAILBOX,
        "from_addr": ONEAGENT_MAILBOX,
        "tls_mode": "implicit_tls",
        "reject_unauthorized": True,
    },
    "attachment_dirs": [],
}

#: OneAgent holds the mailbox password only in the skill's ``.env``
#: (``IMAP_PASS``/``SMTP_PASS``), and that file is not in this workspace. The
#: test credential named in the change's acceptance instructions is used
#: instead; it is not claimed to be the mailbox's real password.
EMAIL_SECRET: Mapping[str, Any] = {
    "imap_password": "test123456",
    "smtp_password": "test123456",
}

# --------------------------------------------------------------------------
# MCP — OneAgent's bundled examples, mapped onto this schema's transports
# --------------------------------------------------------------------------

#: From ``examples/http-server.json``: a remote server with a bearer header.
#: OneAgent's ``type: "http"`` + ``headers`` becomes this schema's
#: ``transport: "streamable_http"`` + ``auth: "header"`` + one ``header_name``,
#: because a connection here carries exactly one secret slot rather than an
#: arbitrary header map — the header name is what selects the slot.
MCP_REMOTE_CONFIG: Mapping[str, Any] = {
    "transport": "streamable_http",
    "url": "https://api.example.com/mcp",
    "auth": "header",
    "header_name": "Authorization",
}
#: The header's value. OneAgent's example spells it ``Bearer ${API_TOKEN}``:
#: the ``Bearer `` prefix belongs to the value, not to the header name.
MCP_REMOTE_SECRET: Mapping[str, Any] = {"header": "Bearer example-api-token"}

#: From ``examples/stdio-server.json``: the local filesystem server. ``env``
#: becomes ``env_keys`` — this schema records *which* environment variables the
#: server is allowed to receive and resolves their values from the credential
#: store, rather than storing a literal map in the config.
MCP_STDIO_CONFIG: Mapping[str, Any] = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
    "env_keys": ["LOG_LEVEL"],
}
MCP_STDIO_SECRET: Mapping[str, Any] = {"env": "info"}

# --------------------------------------------------------------------------
# OA — OneAgent's site shape; no site is saved in this deployment
# --------------------------------------------------------------------------

#: OneAgent's global site constants (``config.oa_defaults``). Both are blank in
#: this deployment, so they are carried as blanks rather than invented: a test
#: that needs them should set them, and a test that checks the schema should
#: assert they are optional.
OA_DEFAULTS: Mapping[str, str] = {
    "tenant_key": "",
    "custom_page_config_id": "",
}

#: The per-tenant site, in the fields OneAgent's handler accepts. ``base_url``
#: is a placeholder: this deployment has no OA site, and pointing a fixture at
#: a host nobody owns would be worse than an obviously fake one.
OA_CONFIG: Mapping[str, Any] = {
    "base_url": "https://oa.example.com",
    "username": "RC001",
    "tenant_key": OA_DEFAULTS["tenant_key"],
    "custom_page_config_id": OA_DEFAULTS["custom_page_config_id"],
}
OA_SECRET: Mapping[str, Any] = {"password": "test123456"}

# --------------------------------------------------------------------------
# The test identity named in the change's acceptance instructions
# --------------------------------------------------------------------------

#: The member these fixtures are exercised as.
MEMBER_USERNAME = "RC001"
MEMBER_DISPLAY_NAME = "Rock"
MEMBER_PASSWORD = "test123456"


def by_kind() -> Dict[str, Dict[str, Any]]:
    """The four fixture sets, keyed by connection kind.

    One place for a seeder or a shape test to iterate, so adding a type cannot
    leave one of the two behind.
    """
    return {
        "erp": {"name": ERP_NAME, "config": dict(ERP_CONFIG),
                "secrets": dict(ERP_SECRET)},
        "email": {"name": ONEAGENT_MAILBOX, "config": dict(EMAIL_CONFIG),
                  "secrets": dict(EMAIL_SECRET)},
        "mcp": {"name": "OneAgent HTTP MCP", "config": dict(MCP_REMOTE_CONFIG),
                "secrets": dict(MCP_REMOTE_SECRET)},
        "oa": {"name": "OneAgent OA", "config": dict(OA_CONFIG),
               "secrets": dict(OA_SECRET)},
    }
