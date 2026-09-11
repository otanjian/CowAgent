# encoding:utf-8
"""HTTP method policy + shared app factory for the web console.

The processor is installed on the real ``build_web_app()`` application so the
running web channel and the test harness share the same gate. It classifies each
route (path pattern + HTTP method) into an identity domain:

* ``public``  — anonymous allowed (login, health, static assets, oauth callback).
* ``personal`` — any authenticated session; the handler decides owner-scoping.
* ``platform`` — requires the resolved context to be a valid platform admin.
* ``tenant``  — requires an authenticated session, explicit tenant selection and
  the listed business permission (or tenant_admin qualification).
* ``closed``  — deferred/not-yet-adapted consumer; returns 503 in database mode
  and never reaches a downstream handler, regardless of admin status.

Rules:

* A URL that matches the route table but is not listed for the requested HTTP
  method is rejected (registration completeness), so a handler cannot silently
  be reached through an unguarded method (e.g. a GET on a write-only endpoint).
* Unknown URLs stay 404 (web.py notfound is preserved).
* In legacy identity mode the shared-password path continues to work; the
  database-domain gateway only enforces the tenant/platform/personal/closed
  semantics when ``identity_mode == database``.

The policy is a *route-completeness* gate, not a replacement for the handler's
own session/tenant/owner checks: each handler still resolves its context and
enforces resource ownership, permission and field whitelisting.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

import web

from channel.web.route_registry import derive_route_policy

#: Route method -> policy entry, DERIVED from the single authoritative
#: route registry (``channel.web.route_registry``). Do not add entries here:
#: register a ``RouteEntry`` in the registry so this table and the
#: ``web.application`` URL table (``channel.web.web_channel._WEB_URLS``)
#: cannot drift apart. The registry also carries the three-leg coverage
#: invariant that cross-checks the registered method set against the handler
#: implementations.
ROUTE_POLICY: Dict[str, Dict[str, dict]] = derive_route_policy()


def _is_database_mode() -> bool:
    try:
        from config import conf
        return str(conf().get("identity_mode", "legacy") or "legacy") == "database"
    except Exception:
        return False


_REASON_PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def _json_error(message: str, status: int, code: str) -> str:
    body = json.dumps({"status": "error", "message": message, "code": code},
                      ensure_ascii=False)
    reason = _REASON_PHRASES.get(status, "")
    raise web.HTTPError(f"{status} {reason}".strip(),
                        {"Content-Type": "application/json; charset=utf-8"}, body)


def _match_policy(path: str, method: str) -> Tuple[Optional[dict], bool]:
    """Return ``(policy_entry, matched)`` for a request path+method.

    ``matched`` is True when the path matches any route in the table (regardless
    of method), so an unknown URL can keep returning 404 while a known URL with
    an unregistered method is rejected as not-allowed.
    """
    method = method.upper()
    for pattern, methods in ROUTE_POLICY.items():
        # exact string match first (fast path), then regex for parameterised URLs
        url_pattern = rf"^{pattern}\Z"
        import re
        if re.match(url_pattern, path):
            entry = methods.get(method)
            if entry is None:
                return None, True
            return entry, True
    return None, False


def enforce_http_policy(handler):
    """web.py processor enforcing the route/method policy.

    Installed by ``build_web_app`` so the production server and the test harness
    share the same gate. It runs before the matched handler, so a rejected method
    or a closed/deferred consumer never reaches downstream logic. ``handler`` is
    the rest-of-the-chain continuation; this function returns its result, or
    raises an HTTPError for rejected routes.

    NOTE (web.py contract): a processor is ``p(handler)`` and the *return value
    of the processor call* is the response. We therefore return ``handler()``
    (or an HTTPError) directly, never a closure.
    """
    path = web.ctx.path
    method = getattr(web.ctx, "method", None) or web.ctx.env.get("REQUEST_METHOD", "GET")
    entry, matched = _match_policy(path, method)
    if not matched:
        # genuine unknown URL -> 404 via web.py notfound
        return handler()
    if entry is None:
        # A real route exists but the requested method is not registered.
        # Reject before reaching the handler (completeness gate).
        return _json_error("method not allowed", 405, "method_not_allowed")
    policy = entry.get("policy", "closed")
    if policy == "closed" and _is_database_mode():
        # A deferred/not-yet-adapted consumer is closed in database mode and
        # must never reach a downstream handler, regardless of admin status.
        return _json_error("unavailable in database identity mode", 503,
                           "database_unavailable")
    # For public / personal / platform / tenant routes the handler resolves
    # the session, tenant and permissions and produces the precise 401/400/403.
    # This route-completeness gate only rejects unregistered methods and
    # short-circuits closed consumers; it does not duplicate handler auth so
    # response semantics stay owned by the handler.
    return handler()
