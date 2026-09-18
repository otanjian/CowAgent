# encoding:utf-8
"""泛微 E-cology / E10 OA protocol package.

Kept out of :mod:`integrations.external.adapters.oa` so the adapter stays a thin
translation layer over a self-contained protocol client:

* :mod:`integrations.external.oa.transport` — a policy-checked HTTP session
  (no adapter may call ``requests`` outside this package: every outbound call
  goes through the deployment's :class:`~integrations.external.adapters.netpolicy.NetworkPolicy`);
* :mod:`integrations.external.oa.login` — the adaptive account/password login
  ported from OneAgent's ``oa_connection.py`` (standard ``VerifyLogin.jsp``,
  E9 array/``{"code":"0"}`` parsing, CAS form discovery, cookie + ``/api/``
  session verification, JS-bundle API-path discovery);
* :mod:`integrations.external.oa.actions` — endpoint vocabulary, payload
  builders and response parsers for the business actions;
* :mod:`integrations.external.oa.client` — the action orchestration and the
  E9-vs-E10 interface selection;
* :mod:`integrations.external.oa.redact` — the redaction used so no secret,
  cookie or session id can reach a stage detail or probe metadata;
* :mod:`integrations.external.oa.store` — the tenant-scoped OA connection
  projection the tool provider lists from.

Nothing in this package reads process-global tenant state: it is handed an
:class:`~integrations.external.adapters.base.ExecutionContext`.
"""

from __future__ import annotations

from integrations.external.oa.actions import INTERFACE_ACTIONS
from integrations.external.oa.client import OaActionError, OaClient
from integrations.external.oa.login import LoginResult, perform_login
from integrations.external.oa.redact import Redactor
from integrations.external.oa.store import list_oa_connections
from integrations.external.oa.transport import (
    HttpResult,
    PolicyTransport,
    TransportError,
    open_transport,
)

__all__ = [
    "HttpResult",
    "INTERFACE_ACTIONS",
    "LoginResult",
    "OaActionError",
    "OaClient",
    "PolicyTransport",
    "Redactor",
    "TransportError",
    "list_oa_connections",
    "open_transport",
    "perform_login",
]
