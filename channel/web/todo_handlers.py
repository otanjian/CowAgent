# encoding:utf-8
"""Web-channel HTTP handlers for the personal todo (待办事项) workbench.

Seven endpoint operations (per openspec/changes/add-workbench-todos/design.md):

- GET  /api/todos            list (status / q / overdue / page)
- GET  /api/todos/summary    personal badge counts + capability state
- POST /api/todos            create
- GET  /api/todos/{id}       detail
- PATCH /api/todos/{id}      field-edit or target-state update (version-cas)
- GET  /api/todos/{id}/events  paginated processing history
- GET  /api/todos/{id}/source  re-authorized agent/session/message locator

Registered BEFORE the ``/api/todos/{id}`` wildcard (web.py matches in order), so
``/api/todos/summary`` is never swallowed by the id route.

Authorization is enforced here, not in the frontend:

* legacy mode: requires ``_require_auth()`` (a configured web_password + valid
  token). A verified login maps to a stable ``local-owner``.
* database mode: requires a resolved ``RequestContext`` with
  ``todo.read`` / ``todo.write`` (member default), a real tenant and membership.
* The service layer re-checks scope/owner and refuses when no trustworthy
  subject can be established.
* Writes go through the same CSRF/origin gate as other console writes.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import time
from http import HTTPStatus
from typing import Any, Optional

import web

from auth.runtime import resolve_context, IdentityContextError
from auth.service import IdentityServiceError, IdentityService
from agent.todo.service import (
    TodoService,
    TodoActor,
    TodoServiceError,
    TodoDisabled,
    TodoUnauthorized,
    TodoPermissionDenied,
    TodoNotFoundError,
    TodoConflictError,
    TodoFieldValidationError,
    TodoUnavailable,
    default_enabled,
)


# --------------------------------------------------------------------------- #
# Local auth helpers (kept independent of web_channel to avoid a circular
# import; see channel/web/branding.py for the shared origin/csrf rationale).
# --------------------------------------------------------------------------- #

def _get_web_password() -> str:
    from config import conf
    pwd = conf().get("web_password", "")
    return "" if pwd is None else str(pwd)


def _is_password_enabled() -> bool:
    return bool(_get_web_password())


def _session_expire_seconds() -> int:
    from config import conf
    return int(conf().get("web_session_expire_days", 30)) * 86400


def _verify_auth_token(token) -> bool:
    if not token or "." not in token:
        return False
    ts_hex, sig = token.split(".", 1)
    try:
        ts = int(ts_hex, 16)
    except ValueError:
        return False
    if time.time() - ts > _session_expire_seconds():
        return False
    expected = hmac.new(
        _get_web_password().encode(), ts_hex.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _get_bearer_token() -> str:
    auth = web.ctx.env.get("HTTP_AUTHORIZATION", "") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


def _require_auth() -> None:
    """Raise 401 if not authenticated (password set but token invalid)."""
    from config import conf
    if not _is_password_enabled():
        return
    if _verify_auth_token(web.cookies().get("cow_auth_token", "")):
        return
    if _verify_auth_token(_get_bearer_token()):
        return
    raise web.HTTPError(
        "401 Unauthorized",
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps({"status": "error", "message": "Unauthorized"}),
    )


def _origin_ok() -> bool:
    """Same-origin check for a cookie-authorized write (mirrors branding)."""
    origin = web.ctx.env.get("HTTP_ORIGIN", "") or web.ctx.env.get("HTTP_REFERER", "") or ""
    if not origin:
        return True
    from urllib.parse import urlparse
    try:
        origin_host = urlparse(origin).netloc
    except Exception:
        return False
    host = web.ctx.env.get("HTTP_HOST", "")
    return origin_host == host


def _csrf_ok() -> bool:
    """Bearer-authenticated writes bypass cookie-CSRF (desktop file:// origin)."""
    if _verify_auth_token(_get_bearer_token()):
        return True
    return _origin_ok()


def _identity_mode() -> str:
    from config import conf
    return str(conf().get("identity_mode", "legacy") or "legacy")


def _is_database() -> bool:
    return _identity_mode() == "database"


def _get_identity_service() -> IdentityService:
    from config import conf, get_data_root
    import os
    configured = conf().get("identity_db_path")
    db_path = configured or os.path.join(get_data_root(), "identity.db")
    return IdentityService(db_path)


def _database_session_token() -> str:
    token = web.cookies().get("cow_session", "")
    if token:
        return token
    auth = web.ctx.env.get("HTTP_AUTHORIZATION", "") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


def _database_tenant_header() -> str:
    return web.ctx.env.get("HTTP_X_TENANT_ID", "") or ""


def _resolve_database_actor() -> TodoActor:
    """Resolve a database-mode actor from a verified session + tenant header.

    Requires a real tenant + membership + the todo permission. Missing / stale
    membership, an inactive tenant or a disabled user all refuse (no silent
    fallback, no shared-tenant borrowing).
    """
    svc = _get_identity_service()
    token = _database_session_token()
    tenant = _database_tenant_header()
    if not token or not tenant:
        raise TodoUnauthorized("database 模式需要登录及租户选择")
    try:
        ctx = resolve_context(svc, token, tenant or None)
    except IdentityContextError as e:
        raise TodoServiceError(str(e), code=e.code, http_status=e.status) from e
    if ctx.must_change_password:
        raise TodoPermissionDenied("请先修改密码", code="password_change_required")
    if not ctx.tenant_id or not ctx.membership:
        raise TodoPermissionDenied("无有效租户成员身份")
    if not ctx.is_tenant_admin and not ctx.permissions:
        raise TodoPermissionDenied("无待办权限")
    # todo.read is a member default; write is checked per-operation below.
    return TodoActor(
        bound=True,
        scope_id=ctx.tenant_id,
        owner_id=ctx.user_id,
        username=ctx.username,
        is_legacy=False,
        permissions=set(ctx.permissions) | (
            {"todo.read", "todo.write", "tenant.info.read", "agent.read", "history.read",
             "knowledge.read", "memory.read"} if ctx.is_tenant_admin else set()
        ),
    )


def _resolve_actor() -> TodoActor:
    """Resolve the trusted actor for the current request.

    legacy: requires a configured web_password AND a verified token. No-password
    (compatibility) mode and unauthenticated requests are refused for todos,
    because the spec forbids a silent single/default-subject fallback.
    database: resolves a real user/tenant/membership with the todo permission.
    """
    if _is_database():
        return _resolve_database_actor()
    # Legacy path.
    if not _is_password_enabled():
        raise TodoUnauthorized("待办功能需要配置 web_password 并进行登录")
    _require_auth()
    return TodoActor.legacy()


def _build_service() -> TodoService:
    actor = _resolve_actor()
    app_data_root = None
    if not actor.is_legacy:
        from common.state_dir import StateDirError, tenant_app_data_root
        try:
            app_data_root = str(tenant_app_data_root(
                actor.scope_id, identity_service=_get_identity_service(),
            ))
        except StateDirError as e:
            from common.log import logger
            logger.error("[TodoService] private tenant data root unavailable: %s", e)
            raise TodoUnavailable("租户待办存储不可用") from e
    return TodoService(actor, enabled_fn=default_enabled, app_data_root=app_data_root)


def _json(data: Any) -> str:
    web.header("Content-Type", "application/json; charset=utf-8")
    web.header("Cache-Control", "no-store")
    return json.dumps(data, ensure_ascii=False)


def _error_response(err: TodoServiceError) -> str:
    web.ctx.status = f"{err.http_status} {HTTPStatus(err.http_status).phrase}"
    payload = {"status": "error", "code": err.code, "message": err.message}
    if isinstance(err, TodoFieldValidationError):
        payload["field"] = err.field
    return _json(payload)


# --------------------------------------------------------------------------- #
# Route helpers
# --------------------------------------------------------------------------- #

def _int_query(name: str, default: int) -> int:
    try:
        return int(web.input(**{name: str(default)}).get(name, default))
    except Exception:
        return default


def _read_json_body() -> dict:
    try:
        return json.loads(web.data())
    except Exception:
        raise TodoFieldValidationError("无效的请求体")


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

class TodosHandler:
    """GET /api/todos and POST /api/todos."""

    def GET(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            inp = web.input(status="open", q="", overdue="false", page="1", page_size="20")
            svc = _build_service()
            overdue = str(inp.overdue).lower() in ("1", "true", "yes")
            data = svc.list(
                status=str(inp.status) or "open",
                q=str(inp.q) or None,
                overdue=overdue,
                page=_int_query("page", 1),
                page_size=_int_query("page_size", 20),
            )
            result = dict(data)
            result["status"] = "success"
            return _json(result)
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))

    def POST(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        if not _csrf_ok():
            return _error_response(TodoPermissionDenied("来源校验失败"))
        try:
            body = _read_json_body()
            svc = _build_service()
            item = svc.create(
                title=body.get("title", ""),
                description=str(body.get("description", "") or ""),
                kind=str(body.get("kind", "general") or "general"),
                priority=str(body.get("priority", "normal") or "normal"),
                due_at=body.get("due_at"),
                timezone=str(body.get("timezone", "") or ""),
                source=str(body.get("source", "manual") or "manual"),
                agent_id=str(body.get("agent_id", "") or ""),
                session_id=str(body.get("session_id", "") or ""),
                message_seq=body.get("message_seq"),
                create_key=body.get("create_key"),
            )
            return _json({"status": "success", "item": item})
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))


class TodoSummaryHandler:
    """GET /api/todos/summary - registered before the {id} wildcard."""

    def GET(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            svc = _build_service()
            data = svc.summary()
            data["status"] = "success"
            return _json(data)
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))


class TodoDetailHandler:
    """GET /api/todos/{id}, PATCH /api/todos/{id}."""

    def GET(self, item_id: str = ""):
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            svc = _build_service()
            item = svc.get(item_id)
            return _json({"status": "success", "item": item})
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))

    def PATCH(self, item_id: str = ""):
        web.header("Content-Type", "application/json; charset=utf-8")
        if not _csrf_ok():
            return _error_response(TodoPermissionDenied("来源校验失败"))
        try:
            body = _read_json_body()
            svc = _build_service()
            expected_version = body.get("expected_version")
            if not isinstance(expected_version, int):
                return _error_response(TodoFieldValidationError("缺少 expected_version", field="expected_version"))
            fields = body.get("fields")
            status = body.get("status")
            item = svc.update(
                item_id,
                expected_version=expected_version,
                fields=fields,
                status=status,
                note=str(body.get("note", "") or ""),
            )
            return _json({"status": "success", "item": item})
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))


class TodoEventsHandler:
    """GET /api/todos/{id}/events."""

    def GET(self, item_id: str = ""):
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            svc = _build_service()
            data = svc.events(item_id, page=_int_query("page", 1), page_size=_int_query("page_size", 20))
            data["status"] = "success"
            return _json(data)
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))


class TodoSourceHandler:
    """GET /api/todos/{id}/source - re-auth the source locator."""

    def GET(self, item_id: str = ""):
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            svc = _build_service()
            data = svc.source(item_id)
            data["status"] = "success"
            return _json(data)
        except TodoServiceError as e:
            return _error_response(e)
        except web.HTTPError:
            raise
        except Exception:
            from common.log import logger
            logger.exception("[TodoService] request failed")
            return _error_response(TodoServiceError("待办服务暂时不可用", code="internal", http_status=500))
