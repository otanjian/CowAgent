"""Fork web layer (change adopt-upstream-web-split, design D2).

Fork-owned implementation, moved verbatim out of the former
channel/web/web_channel.py monolith. Upstream's api/ modules are not
edited. Imports inside function bodies are lazy so these modules can
reference each other without import cycles.
"""

from __future__ import annotations
from bridge.context import *
from common.log import logger
from typing import Any, Dict, List, Tuple, Optional, Iterator, NoReturn
import datetime
import json
import os
import web


def _scheduler_task_store(agent_id: str):
    """The ``TaskStore`` for one Agent, resolved through the per-Agent layout.

    ``common/state_dir.scheduler_file`` is the one definition of "where an Agent's
    schedule lives" (``<agent workspace>/scheduler/tasks.json``), and it is keyed
    on ``agent_id`` rather than on the request's working root: in database mode
    the working root is the *tenant's* shared root, so resolving the store from it
    would make every Agent of a tenant share one file. The identity is derived
    with the Agent under test pinned, which is also what the tool path does.
    """
    from agent.tools.scheduler.task_store import TaskStore
    from common import state_dir
    from common.runtime_identity import current_identity

    identity = current_identity().derive(agent_id=agent_id)
    return TaskStore(str(state_dir.scheduler_file(identity)))


def _scheduler_agent_ids(actor) -> List[str]:
    """Every Agent whose schedule the acting member may be shown.

    Authoritative for the aggregate view: the caller's tenant decides the list
    (``tenant_agent_ids``), and an Agent that the registry does not have enabled
    is not opened at all - so an Agent of another tenant is never read and then
    filtered.
    """
    from agent.registry import get_agent_registry
    from auth.service import get_identity_service

    enabled = {p.id for p in get_agent_registry().list(include_disabled=False)}
    bound = get_identity_service().tenant_agent_ids(actor.tenant_id)
    return [agent_id for agent_id in bound if agent_id in enabled]


def _scheduler_scope_resolver(actor, agent_id: str) -> Dict:
    """Binding + ``agent.use`` for one Agent, for the authorization service."""
    from auth.service import get_identity_service

    service = get_identity_service()
    scope: Dict = {"agent_id": agent_id}
    try:
        binding = service.get_agent_binding(agent_id)
        scope["bound_tenant"] = (binding or {}).get("tenant_id") or ""
    except Exception as error:
        logger.debug("[WebChannel] scheduler binding lookup %r: %s", agent_id, error)
    if actor.is_admin:
        scope["can_use"] = True
    else:
        try:
            scope["can_use"] = bool(service.check_resource_action(
                actor.user_id, actor.tenant_id, "agent", f"agent:{agent_id}",
                "use", permission="agent.use"))
        except Exception:
            scope["can_use"] = False
    return scope


def _scheduler_service_for_web(agent_id: str):
    """The scheduler runtime for one Agent, for the manual-run path."""
    from agent.tools.scheduler.integration import get_scheduler_service
    return get_scheduler_service(agent_id=agent_id)


def _scheduler_access(ctx=None) -> "TaskAccessService":
    """Build the shared task authorization service for this request.

    One construction for all five handlers, so the HTTP path cannot diverge from
    the tool and background paths (tasks 3.1/3.3).
    """
    from channel.web.web_channel import _web_auth_session_id
    from agent.tools.scheduler.authorization import (
        TaskActor, TaskAccessService, actor_from_identity_context,
    )

    if ctx is not None:
        actor = actor_from_identity_context(ctx, source="http")
    else:
        from channel.web.auth_handlers import _require_context
        actor = actor_from_identity_context(_require_context(require_tenant=True),
                                            source="http")
    actor.session_id = _web_auth_session_id()

    def quota_gate(actor_, agent_id, count):
        from auth.service import get_identity_service
        get_identity_service().check_scheduled_task_quota(
            user_id=actor_.user_id, tenant_id=actor_.tenant_id,
            would_be_count=count)

    return TaskAccessService(
        store_resolver=lambda actor_, agent_id: _scheduler_task_store(agent_id),
        agent_ids=_scheduler_agent_ids,
        scope_resolver=_scheduler_scope_resolver,
        quota_check=quota_gate,
        run_hook=lambda actor_, agent_id, task_id: _scheduler_run_hook(
            actor_, agent_id, task_id),
        coordinator="http",
    )


def _scheduler_run_hook(actor, agent_id: str, task_id: str) -> None:
    """Fire one task now through the scheduler runtime.

    The runtime is the same object the timer uses, so a manual run cannot take a
    path a scheduled fire would refuse; the authorization service has already
    re-checked the caller's own grants before calling this. A runtime that is not
    up is reported as ``run_unavailable`` (503), not as a silent success: the
    console must not tell a member their task ran when nothing was queued.
    """
    from channel.web.web_channel import _scheduler_service_for_web
    from agent.tools.scheduler.authorization import (
        RUN_UNAVAILABLE, TaskAuthorizationError,
    )

    service = _scheduler_service_for_web(agent_id)
    if service is None:
        raise TaskAuthorizationError(
            RUN_UNAVAILABLE, status=503, message="Scheduler service is not running")
    service.run_task_now(task_id)


def _scheduler_error(error):
    """The HTTP response for a refused management call.

    Raised as a ``web.HTTPError`` rather than returned with ``web.status`` set:
    the status has to survive the handler's own ``except`` clauses and the
    request's context teardown, and raising is the only form that does. ``code``
    is the stable machine value the console branches on; the status distinguishes
    "not yours" (403) from "no such task" (404) and "someone else edited it
    first" (409).
    """
    from channel.web.web_channel import _SCHEDULER_STATUS_LINES
    status = int(getattr(error, "status", 403) or 403)
    raise web.HTTPError(
        _SCHEDULER_STATUS_LINES.get(status, "%d Error" % status),
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(error.payload(), ensure_ascii=False),
    )


class SchedulerHandler:
    def GET(self):
        from channel.web.web_channel import _db_scope
        from channel.web.web_channel import _int_param
        from channel.web.web_channel import _request_agent_id
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            params = web.input(agent_id='', page='1', page_size='20')
            requested = _request_agent_id(params)
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                page = service.list_tasks(
                    _scheduler_actor(ctx), agent_id=requested,
                    page=_int_param(params, "page", 1),
                    page_size=_int_param(params, "page_size", 20),
                )
            return json.dumps({"status": "success", **page}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _scheduler_actor(ctx):
    from channel.web.web_channel import _web_auth_session_id
    from agent.tools.scheduler.authorization import actor_from_identity_context
    actor = actor_from_identity_context(ctx, source="http")
    actor.session_id = _web_auth_session_id()
    return actor


class SchedulerRunHandler:
    def POST(self):
        from channel.web.web_channel import _db_scope
        from channel.web.web_channel import _request_agent_id
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            agent_id = _request_agent_id(body)
            task_id = body.get("task_id")
            # The client's "same request" proof (task 3.4). A retry after a lost
            # response resends the same key and is answered from the accepted
            # outcome instead of queueing a second fire; a click without a key
            # keeps the older behaviour (refused while the task is running).
            run_key = str(body.get("run_key") or "").strip()
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                service.run_task(_scheduler_actor(ctx), agent_id, task_id,
                                 run_key=run_key or None)
            return json.dumps({
                "status": "success",
                "message": f"Task '{task_id}' queued for immediate execution",
            }, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler manual run error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerToggleHandler:
    def POST(self):
        from channel.web.web_channel import _db_scope
        from channel.web.web_channel import _request_agent_id
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            enabled = body.get("enabled", True)
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                task = service.set_enabled(_scheduler_actor(ctx),
                                          _request_agent_id(body), task_id,
                                          bool(enabled))
            return json.dumps({"status": "success", "task": task}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler toggle error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerUpdateHandler:
    def POST(self):
        from channel.web.web_channel import _db_scope
        from channel.web.web_channel import _request_agent_id
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import (
            FORGED_FIELD, TaskAuthorizationError,
        )
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))

            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                actor = _scheduler_actor(ctx)
                agent_id = _request_agent_id(body)
                original = service.get_task(actor, agent_id, task_id)
                # Every other body key is handed to the service as a patch, so a
                # field the caller may not set (``owner``, ``scope``,
                # ``agent_id``, ...) is refused by name (400) instead of being
                # quietly dropped here — a silent drop would let a caller believe
                # a rename happened when the field they really wanted was ignored.
                patch = {k: v for k, v in body.items()
                         if k not in ("task_id", "agent_id", "revision")}
                if "schedule" in patch:
                    # Recompute next_run_at for the merged task, and refuse a
                    # schedule that cannot produce one (an unparseable cron, or a
                    # one-off time already past) — the same check the editor has
                    # always relied on, now before the write rather than after.
                    from agent.tools.scheduler.scheduler_service import SchedulerService
                    from agent.tools.scheduler.task_store import TaskStore
                    merged = dict(original)
                    merged.update(patch)
                    if "action" in patch and isinstance(patch["action"], dict):
                        action = dict(original.get("action") or {})
                        action.update(patch["action"])
                        merged["action"] = action
                    dry = SchedulerService(TaskStore(os.devnull), lambda t: None)
                    next_run = dry._calculate_next_run(merged, datetime.now())
                    if not next_run:
                        raise web.HTTPError(
                            "400 Bad Request",
                            {"Content-Type": "application/json; charset=utf-8"},
                            json.dumps({
                                "status": "error",
                                "message": "Cannot calculate next run time. Please check the schedule config (e.g., cron expression format, or whether the one-time task time has already passed).",
                            }, ensure_ascii=False))
                    patch["next_run_at"] = next_run.isoformat()
                elif "action" in patch and not original.get("next_run_at"):
                    from agent.tools.scheduler.scheduler_service import SchedulerService
                    from agent.tools.scheduler.task_store import TaskStore
                    merged = dict(original)
                    merged.update(patch)
                    dry = SchedulerService(TaskStore(os.devnull), lambda t: None)
                    next_run = dry._calculate_next_run(merged, datetime.now())
                    if next_run:
                        patch["next_run_at"] = next_run.isoformat()

                revision = body.get("revision")
                task = service.update_task(
                    actor, agent_id, task_id, patch,
                    expected_revision=int(revision) if revision is not None else None)
            return json.dumps({"status": "success", "task": task}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler update error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerDeleteHandler:
    def POST(self):
        from channel.web.web_channel import _db_scope
        from channel.web.web_channel import _request_agent_id
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                service.delete_task(_scheduler_actor(ctx),
                                    _request_agent_id(body), task_id)
            return json.dumps({"status": "success"}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler delete error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


