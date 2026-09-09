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

#: Route method → policy. The key is the raw URL pattern (without ``^``/``$``);
#: the value maps an HTTP method to a policy entry. ``None`` in the method map
#: means "all methods share this policy" (used for closed/static routes).
#
#: ``policy`` is one of: public, personal, platform, tenant, closed.
#: ``permission`` is the required business permission for a ``tenant`` route
#: (ignored otherwise).
#: ``comment`` documents the entry for maintainers.
#:
#: This table is the single source of route-authorization truth used by the
#: shared app factory. It is intentionally exhaustive: a business method that is
#: implemented but not listed here MUST NOT be silently reachable.
ROUTE_POLICY: Dict[str, Dict[str, dict]] = {
    "/": {"GET": {"policy": "public", "comment": "console root"}},
    "/api/health": {"GET": {"policy": "public", "comment": "health probe"}},
    "/assets/(.*)": {"GET": {"policy": "public", "comment": "static assets"}},
    "/mcp/oauth/callback": {"GET": {"policy": "public", "comment": "MCP oauth callback"}},
    # --- auth (database & legacy) ---
    "/auth/login": {"POST": {"policy": "public", "comment": "login (db + legacy)"}},
    "/auth/check": {"GET": {"policy": "public", "comment": "auth state probe"}},
    "/auth/logout": {"POST": {"policy": "public", "comment": "logout"}},
    "/auth/password": {"POST": {"policy": "personal", "comment": "self password change"}},
    "/auth/me": {"GET": {"policy": "personal", "comment": "self projection"}},
    "/auth/profile": {"PATCH": {"policy": "personal", "comment": "self profile edit"}},
    "/auth/profile/avatar": {
        "GET": {"policy": "personal", "comment": "fetch self avatar"},
        "POST": {"policy": "personal", "comment": "upload self avatar"},
    },
    "/auth/context": {"GET": {"policy": "tenant", "comment": "current-tenant capability"}},
    # --- platform admin control plane ---
    "/api/platform/users": {"GET": {"policy": "platform", "comment": "list accounts"}},
    "/api/platform/users/([^/]+)/password": {
        "POST": {"policy": "platform", "comment": "reset platform account password"},
    },
    "/api/platform/users/([^/]+)/external-identities": {
        "GET": {"policy": "platform", "comment": "list external identity bindings for a user"},
        "POST": {"policy": "platform", "comment": "bind external identity to a user"},
    },
    "/api/platform/users/([^/]+)/external-identities/([^/]+)": {
        "DELETE": {"policy": "platform", "comment": "delete an external identity binding"},
    },
    "/api/platform/users/([^/]+)": {
        # PATCH is the only supported method (enable/disable / admin flag).
        # A GET detail is not part of the platform account contract; leaving it
        # unregistered means the completeness gate returns 405 rather than
        # invoking the handler (which has no GET(user_id) method).
        "PATCH": {"policy": "platform", "comment": "enable/disable or set platform-admin flag"},
    },
    "/api/platform/tenants": {
        "GET": {"policy": "platform", "comment": "list tenants"},
        "POST": {"policy": "platform", "comment": "create tenant"},
    },
    "/api/platform/tenants/([^/]+)/admins": {
        "POST": {"policy": "platform", "comment": "configure tenant admin"},
    },
    "/api/platform/tenants/([^/]+)": {
        "GET": {"policy": "platform", "comment": "tenant detail"},
        "POST": {"policy": "platform", "comment": "edit tenant status"},
    },
    # --- current tenant management ---
    "/api/tenant": {"GET": {"policy": "tenant", "permission": "tenant.info.read",
                            "comment": "current tenant info"}},
    "/api/tenant/members": {
        "GET": {"policy": "tenant", "permission": "tenant.members.read", "comment": "list members"},
        "POST": {"policy": "tenant", "comment": "create/bind member (tenant_admin)"},
    },
    "/api/tenant/members/([^/]+)": {
        "POST": {"policy": "tenant", "comment": "update member (tenant_admin)"},
    },
    "/api/tenant/roles": {
        "GET": {"policy": "tenant", "permission": "tenant.members.read", "comment": "list roles"},
        "POST": {"policy": "tenant", "comment": "create role (tenant_admin)"},
    },
    "/api/tenant/roles/([^/]+)": {
        "POST": {"policy": "tenant", "comment": "update role (tenant_admin)"},
        "DELETE": {"policy": "tenant", "comment": "delete role (tenant_admin)"},
    },
    "/api/tenant/authorization/catalog": {
        "GET": {"policy": "tenant", "comment": "authorization resource catalog (assign/use)"},
    },
    "/api/platform/tenants/([^/]+)/roles": {
        "GET": {"policy": "platform", "comment": "target-tenant role list (platform)"},
        "POST": {"policy": "platform", "comment": "target-tenant role create (platform)"},
    },
    "/api/platform/tenants/([^/]+)/roles/([^/]+)": {
        "POST": {"policy": "platform", "comment": "target-tenant role update (platform)"},
        "DELETE": {"policy": "platform", "comment": "target-tenant role delete (platform)"},
    },
    "/api/platform/tenants/([^/]+)/authorization/catalog": {
        "GET": {"policy": "platform", "comment": "target-tenant auth catalog (platform)"},
    },
    "/api/platform/tenants/([^/]+)/resources": {
        "GET": {"policy": "platform", "comment": "tenant global resource grants (platform)"},
        "PUT": {"policy": "platform", "comment": "set tenant global resource grants (platform)"},
    },
    "/api/tenant/permissions": {"GET": {"policy": "tenant", "comment": "permission catalog"}},
    "/api/tenant/departments": {
        "GET": {"policy": "tenant", "permission": "tenant.org.read", "comment": "list departments"},
        "POST": {"policy": "tenant", "comment": "create department (tenant_admin)"},
    },
    "/api/tenant/departments/([^/]+)": {
        "PUT": {"policy": "tenant", "comment": "update/move department (tenant_admin)"},
        "DELETE": {"policy": "tenant", "comment": "delete department (tenant_admin)"},
    },
    "/api/identity/audit": {"GET": {"policy": "tenant", "comment": "identity audit (platform/tenant_admin)"}},
    "/api/admin/overview": {"GET": {"policy": "tenant", "comment": "admin console KPI overview (platform/tenant_admin)"}},
    # --- chat transport (database: tenant, legacy: public-ish) ---
    "/message": {"POST": {"policy": "tenant", "comment": "send message"}},
    "/stream": {"GET": {"policy": "tenant", "comment": "SSE stream"}},
    "/poll": {"POST": {"policy": "tenant", "comment": "poll response"}},
    "/cancel": {"POST": {"policy": "tenant", "comment": "cancel request"}},
    "/chat": {"GET": {"policy": "tenant", "comment": "chat page"}},
    "/v1/chat/completions": {"POST": {"policy": "tenant", "comment": "OpenAI-compatible chat"}},
    # --- open file/voice/scheduler consumers (database: tenant-scoped) ---
    # These consumers are now identity+permission revalidated inside their
    # handlers (open-database-runtime task 2.4). /preview stays public because
    # the sandboxed preview iframe (opaque origin) cannot send the session
    # cookie; it is capability-authorized by its HMAC directory token.
    "/upload": {"POST": {"policy": "tenant", "comment": "file upload"}},
    "/uploads/(.*)": {"GET": {"policy": "tenant", "comment": "serve upload"}},
    "/api/file": {"GET": {"policy": "tenant", "comment": "file serve"}},
    "/preview/(.+)": {"GET": {"policy": "public", "comment": "preview (capability token)"}},
    "/api/voice/asr": {"POST": {"policy": "tenant", "comment": "voice ASR"}},
    "/api/voice/tts": {"POST": {"policy": "tenant", "comment": "voice TTS"}},
    # --- scheduler management stays closed until the scheduler slice (group 5)
    # adds trigger-time identity snapshots + revalidation (open-database-runtime
    # task 5.x); opening it now would expose a consumer with no tenant boundary.
    "/api/scheduler": {"GET": {"policy": "closed", "comment": "scheduler (group 5)"}},
    "/api/scheduler/run": {"POST": {"policy": "closed", "comment": "scheduler run (group 5)"}},
    "/api/scheduler/toggle": {"POST": {"policy": "closed", "comment": "scheduler toggle (group 5)"}},
    "/api/scheduler/update": {"POST": {"policy": "closed", "comment": "scheduler update (group 5)"}},
    "/api/scheduler/delete": {"POST": {"policy": "closed", "comment": "scheduler delete (group 5)"}},
    # --- deferred consumers (still closed in database mode) ---
    "/api/workspace/tree": {"GET": {"policy": "closed", "comment": "workspace tree (deferred)"}},
    "/api/workspace/search": {"GET": {"policy": "closed", "comment": "workspace search (deferred)"}},
    "/api/workspace/resolve": {"GET": {"policy": "closed", "comment": "workspace resolve (deferred)"}},
    "/api/workspace/meta": {"GET": {"policy": "closed", "comment": "workspace meta (deferred)"}},
    "/api/workspace/read": {"GET": {"policy": "closed", "comment": "workspace read (deferred)"}},
    "/api/workspace/write": {"POST": {"policy": "closed", "comment": "workspace write (deferred)"}},
    # --- project workspace (user-private in database mode) ---
    "/api/projects": {"GET": {"policy": "tenant", "comment": "projects"}},
    "/api/projects/select": {"POST": {"policy": "tenant", "comment": "project select"}},
    "/api/projects/create": {"POST": {"policy": "tenant", "comment": "project create"}},
    "/api/projects/browse": {"GET": {"policy": "closed", "comment": "project browse (deferred)"}},
    "/api/projects/order": {"POST": {"policy": "tenant", "comment": "project order"}},
    "/api/projects/manage": {"PUT": {"policy": "tenant", "comment": "project rename"},
                             "DELETE": {"policy": "tenant", "comment": "project delete"}},
    "/config": {"GET": {"policy": "platform", "comment": "platform config"},
                "POST": {"policy": "platform", "comment": "platform config save"}},
    "/api/models": {"GET": {"policy": "platform", "comment": "models (platform admin)"},
                    "POST": {"policy": "platform", "comment": "models save (platform admin)"}},
    "/api/channels": {"GET": {"policy": "closed", "comment": "channels (deferred)"}},
    "/api/weixin/qrlogin": {"GET": {"policy": "closed", "comment": "weixin qr (deferred)"}},
    "/api/feishu/register": {"POST": {"policy": "closed", "comment": "feishu register (deferred)"}},
    "/api/tools": {"GET": {"policy": "tenant", "comment": "tools"}},
    "/api/skills": {"GET": {"policy": "tenant", "comment": "skills"},
                    "POST": {"policy": "tenant", "comment": "skills toggle"}},
    "/api/skills/content": {"GET": {"policy": "tenant", "comment": "skill content"},
                            "POST": {"policy": "tenant", "comment": "skill content write"}},
    "/api/memory": {"GET": {"policy": "closed", "comment": "memory (deferred)"}},
    "/api/memory/content": {"GET": {"policy": "closed", "comment": "memory content (deferred)"}},
    "/api/knowledge/list": {"GET": {"policy": "closed", "comment": "knowledge (deferred)"}},
    "/api/knowledge/read": {"GET": {"policy": "closed", "comment": "knowledge read (deferred)"}},
    "/api/knowledge/graph": {"GET": {"policy": "closed", "comment": "knowledge graph (deferred)"}},
    "/api/knowledge/action": {"POST": {"policy": "closed", "comment": "knowledge action (deferred)"}},
    "/api/knowledge/import": {"POST": {"policy": "closed", "comment": "knowledge import (deferred)"}},
    "/api/todos": {"GET": {"policy": "tenant", "comment": "todos (own)"}},
    "/api/todos/summary": {"GET": {"policy": "tenant", "comment": "todo summary"}},
    "/api/todos/(.*)/events": {"GET": {"policy": "tenant", "comment": "todo events"}},
    "/api/todos/(.*)/source": {"GET": {"policy": "tenant", "comment": "todo source"}},
    "/api/todos/(.*)": {"GET": {"policy": "tenant", "comment": "todo detail"}},
    "/api/agents": {"GET": {"policy": "tenant", "comment": "agents"}},
    "/api/agents/([^/]+)/avatar": {"GET": {"policy": "tenant", "comment": "agent avatar"}},
    "/api/agents/([^/]+)/files/([^/]+)": {"GET": {"policy": "tenant", "comment": "agent core file"}},
    "/api/sessions": {"GET": {"policy": "tenant", "comment": "sessions"}},
    "/api/sessions/(.*)/generate_title": {"POST": {"policy": "tenant", "comment": "session title"}},
    "/api/prompt/optimize": {"POST": {"policy": "tenant", "comment": "prompt optimize"}},
    "/api/sessions/(.*)/clear_context": {"POST": {"policy": "tenant", "comment": "clear context"}},
    "/api/sessions/(.*)/settings": {
        "GET": {"policy": "tenant", "comment": "session settings (read effective model/permission)"},
        "POST": {"policy": "tenant", "comment": "session settings"},
    },
    "/api/sessions/(.*)": {"GET": {"policy": "tenant", "comment": "session detail"}},
    "/api/history": {"GET": {"policy": "tenant", "comment": "history"}},
    "/api/messages/delete": {"POST": {"policy": "tenant", "comment": "delete message"}},
    "/api/logs/download": {"GET": {"policy": "tenant", "comment": "logs download"}},
    "/api/logs": {"GET": {"policy": "tenant", "comment": "logs"}},
    "/api/version": {"GET": {"policy": "public", "comment": "version"}},
    "/api/branding/public": {"GET": {"policy": "public", "comment": "public branding"}},
    "/api/branding": {"GET": {"policy": "tenant", "comment": "branding manage"}},
    "/api/branding/reset": {"POST": {"policy": "tenant", "comment": "branding reset"}},
    "/api/branding/assets/(.*)": {"GET": {"policy": "public", "comment": "branding asset"}},
}


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
