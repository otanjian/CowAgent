# encoding:utf-8
"""The one authorization service for scheduled tasks (HTTP, tool and scheduler).

Why this exists
---------------
A scheduled task is per-Agent data (``<workspace>/scheduler/tasks.json``) while
the callers are several: the Web console's five management methods, the
``scheduler`` Agent tool, the background fire loop, and migrations. Before this
module each caller decided for itself who could touch a task, and the measured
result was:

* the HTTP handlers addressed *any* Agent by id and then read/wrote the store
  with no owner check at all, so one member could toggle, edit or delete another
  member's task by naming its ``agent_id``;
* the Agent tool had no per-task authorization whatsoever — only the generic
  tool gate it explicitly opts out of via ``self_authorized = True``, so a model
  could list and delete tasks it was never granted;
* ``run`` (manual fire) reused neither the creation snapshot nor the trigger
  revalidation, so a revoked member could still make a task execute.

Three copies of "who is this" is exactly how a gate ends up enforced on one path
and forgotten on the next. This module is the single answer, and the tool, the
handlers, the manual-run path and the migration all call it.

The decision, not just the identity
-----------------------------------
Resolving the caller is not the interesting part. What a caller may do differs
per task, and the axes are:

``scope``
    A task is ``personal`` (created by a member for themselves, carrying that
    member as ``owner``) or ``public`` (the Agent's own schedule, no member
    owner — what ``scope=public`` marks and what every pre-existing task is).
    A personal task stays personal even when it runs on a shared public Agent:
    using a shared Agent does not make the task everyone's.

``action``
    ``view`` (list/read), ``manage`` (enable/disable, edit, delete) and ``run``
    (fire it now). They are deliberately not one ladder. A member whose
    ``agent.use`` grant was revoked must still be able to see their own task and
    **pause or delete it** — the alternative is a task nobody can switch off.
    ``run`` is therefore the only action that requires the execution gate, and
    it requires it *again* at fire time (``identity.revalidate_owner``).

The refusal rules in one sentence: you may fully manage a task you own; you may
see, but not change, public tasks on an Agent you may use; only a tenant or
platform administrator may manage public tasks; and neither of them may touch
another member's personal task. The last rule is the one a "tenant admin sees
everything" reflex gets wrong, so it is asserted directly in the tests.

What is NOT here
----------------
No storage. This module never opens ``tasks.json`` itself; it is handed a
``TaskStore`` per Agent by the caller, which is what keeps one task store in the
codebase instead of two (task 3.3). Revision, field whitelisting and the redacted
audit trail live in :mod:`agent.tools.scheduler.task_store` and
:meth:`TaskAccessService._audit`, so every entry point shares them.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("scheduler_authz")

# --- actions ---------------------------------------------------------------

#: Read a task (list + detail).
ACTION_VIEW = "view"
#: Change a task: enable/disable, edit, delete.
ACTION_MANAGE = "manage"
#: Fire a task now, or create one (creation is a run-class decision: it commits
#: the member to future executions on the Agent).
ACTION_RUN = "run"

#: Tasks a member creates for themselves.
SCOPE_PERSONAL = "personal"
#: The Agent's own schedule; manage requires a tenant/platform administrator.
SCOPE_PUBLIC = "public"

# --- stable refusal codes --------------------------------------------------

NOT_MEMBER = "not_member"
NOT_OWNER = "not_owner"
AGENT_NOT_BOUND = "agent_not_bound"
AGENT_DENIED = "agent_denied"
RUN_DENIED = "run_denied"
UNKNOWN_TASK = "unknown_task"
UNKNOWN_AGENT = "unknown_agent"
REVISION_CONFLICT = "revision_conflict"
QUOTA_EXCEEDED = "quota_exceeded"
QUARANTINED = "quarantined"
FORGED_FIELD = "forged_field"
#: A second manual run of a task that is already executing. Not an error in the
#: sense of a bad request: the desired outcome ("this task runs") is already
#: under way, and re-queueing it would run it twice.
ALREADY_RUNNING = "already_running"
#: The runtime that would execute the task is not up (or not for this Agent).
RUN_UNAVAILABLE = "run_unavailable"

_REASON_TEXT = {
    NOT_MEMBER: ("无法确认调用者身份或账号尚未就绪。", "Caller identity is not a ready tenant member."),
    NOT_OWNER: ("无权访问其他成员的个人任务。", "Only the owning member may access this personal task."),
    AGENT_NOT_BOUND: ("助手不属于当前组织。", "The Agent is not bound to this tenant."),
    AGENT_DENIED: ("缺少该助手的使用授权。", "The caller lacks the agent.use grant on this Agent."),
    RUN_DENIED: ("当前授权不允许立即执行该任务。", "The current grants do not allow running this task."),
    UNKNOWN_TASK: ("任务不存在。", "No such task."),
    UNKNOWN_AGENT: ("助手不存在或未启用。", "No such Agent."),
    REVISION_CONFLICT: ("任务已被其他入口修改，请刷新后重试。", "The task changed; reload and retry."),
    QUOTA_EXCEEDED: ("已达定时任务配额上限。", "The scheduled-task quota is exhausted."),
    QUARANTINED: ("任务缺少可信归属，已隔离停用。", "The task has no trustworthy owner and is quarantined."),
    FORGED_FIELD: ("请求包含不可由调用方指定的字段。", "The request carries a field the caller may not set."),
    ALREADY_RUNNING: ("该任务正在执行，未重复入队。", "The task is already running; not queued twice."),
    RUN_UNAVAILABLE: ("调度服务当前不可用。", "The scheduler runtime is not available right now."),
}


class TaskAuthorizationError(Exception):
    """An authorized-operation refusal.

    ``code`` is a stable machine value (the constants above) so the console, the
    tool result and the tests all branch on the same string, and ``status`` is
    the HTTP status the handler should use. The message is localized text for a
    human, never a hint about what exists (an unknown task and a foreign task
    are distinguished here *because* the caller already proved identity and the
    console must render different UI — a probe by an unauthenticated caller
    never gets this far).
    """

    def __init__(self, code: str, *, status: int = 403,
                 message: Optional[str] = None) -> None:
        self.code = code
        self.status = status
        zh, en = _REASON_TEXT.get(code, ("操作被拒绝。", "Operation denied."))
        super().__init__(message or f"[{code}] {zh} {en}")

    def payload(self) -> Dict[str, Any]:
        return {"status": "error", "code": self.code, "message": str(self)}


# --- the actor -------------------------------------------------------------


class TaskActor:
    """The verified caller, resolved once per request / tool call.

    Deliberately tiny: everything here is read from the identity service, never
    from the request body. ``source`` records which entry point built it
    (``http``/``tool``/``scheduler``/``migration``) so the audit trail and the
    write-coordinator field can name it.
    """

    __slots__ = ("user_id", "tenant_id", "session_id", "source", "permissions",
                 "is_platform_admin", "is_tenant_admin", "must_change_password",
                 "username")

    def __init__(self, *, user_id: str = "", tenant_id: str = "",
                 session_id: str = "", source: str = "unknown",
                 permissions: Iterable[str] = (),
                 is_platform_admin: bool = False, is_tenant_admin: bool = False,
                 must_change_password: bool = False, username: str = "") -> None:
        self.user_id = user_id or ""
        self.tenant_id = tenant_id or ""
        self.session_id = session_id or ""
        self.source = source or "unknown"
        self.permissions = frozenset(permissions or ())
        self.is_platform_admin = bool(is_platform_admin)
        self.is_tenant_admin = bool(is_tenant_admin)
        self.must_change_password = bool(must_change_password)
        self.username = username or ""

    @property
    def is_member(self) -> bool:
        """A caller that may own and manage tasks at all.

        A pending password change is *not* a member for task purposes: the same
        gate blocks the Web chat, so a task created under it would have an owner
        who cannot yet run anything (and ``revalidate_owner`` would skip every
        fire). Refusing at creation is the honest answer.
        """
        return bool(self.user_id and self.tenant_id) and not self.must_change_password

    @property
    def is_admin(self) -> bool:
        """Tenant-scoped administrator.

        A platform administrator acting inside a selected tenant has the same
        powers here as that tenant's admin, and no more: the "platform admin"
        scope is not a bypass for another member's private data (task 11.3).
        """
        return self.is_tenant_admin or self.is_platform_admin

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "TaskActor(user=%s tenant=%s source=%s admin=%s)" % (
            self.user_id, self.tenant_id, self.source, self.is_admin)


def actor_from_identity_context(ctx: Any, *, source: str = "http",
                                session_id: str = "") -> TaskActor:
    """Build an actor from an ``auth.runtime`` identity context."""
    if ctx is None:
        return TaskActor(source=source, session_id=session_id)
    return TaskActor(
        user_id=getattr(ctx, "user_id", "") or "",
        tenant_id=getattr(ctx, "tenant_id", "") or "",
        session_id=session_id or getattr(ctx, "session_id", "") or "",
        source=source,
        permissions=getattr(ctx, "permissions", ()) or (),
        is_platform_admin=bool(getattr(ctx, "is_platform_admin", False)),
        is_tenant_admin=bool(getattr(ctx, "is_tenant_admin", False)),
        must_change_password=bool(getattr(ctx, "must_change_password", False)),
        username=getattr(ctx, "username", "") or "",
    )


def actor_from_runtime(context: Any = None, *, source: str = "tool",
                       service: Any = None) -> TaskActor:
    """Build an actor from the ambient runtime identity (the Agent-tool path).

    ``context`` is the bridge context the SchedulerTool holds; it carries the
    request snapshot for threads that have no ambient identity. Only
    ``user_id``/``tenant_id``/``agent_id``/``session_id`` are read from it —
    never a claimed permission, which is always re-derived from the identity
    service below.
    """
    from common.runtime_identity import current_identity

    rt = current_identity()
    snapshot = (context or {}).get("runtime_identity") if isinstance(context, dict) else None
    if snapshot:
        rt = rt.derive(
            user_id=(snapshot.get("user_id") or rt.user_id),
            tenant_id=(snapshot.get("tenant_id") or rt.tenant_id),
            agent_id=(snapshot.get("agent_id") or rt.agent_id),
        )
    session_id = ""
    if isinstance(context, dict):
        session_id = context.get("session_id") or ""
    if not (rt.user_id and rt.tenant_id):
        return TaskActor(source=source, session_id=session_id or (rt.session_id or ""))

    svc = service or _identity_service()
    ctx = None
    if svc is not None:
        try:
            from auth.runtime import member_context
            ctx = member_context(svc, rt.user_id, rt.tenant_id)
        except Exception as error:
            # No verified context (removed member, pending password, service
            # failure): keep the ids for the audit trail but mark it unusable.
            logger.info("[scheduler_authz] member context unavailable for %s: %s",
                        rt.user_id, error)
            return TaskActor(user_id=rt.user_id, tenant_id=rt.tenant_id,
                             session_id=session_id or (rt.session_id or ""),
                             source=source, must_change_password=True)
    actor = actor_from_identity_context(ctx, source=source,
                                       session_id=session_id or (rt.session_id or ""))
    actor.session_id = session_id or (rt.session_id or "")
    return actor


def _identity_service():
    try:
        from auth.service import get_identity_service
        return get_identity_service()
    except Exception:
        return None


# --- task shape helpers ----------------------------------------------------


def task_scope(task: Any) -> str:
    """``personal`` or ``public`` for a stored task.

    An explicit ``scope`` wins; otherwise the presence of a member owner
    decides. Any task written before owner tracking existed has no owner and is
    therefore public — which is exactly why the migration quarantines it rather
    than letting it run (task 4.2).
    """
    explicit = (task or {}).get("scope")
    if explicit in (SCOPE_PERSONAL, SCOPE_PUBLIC):
        return explicit
    owner = (task or {}).get("owner") or {}
    if owner.get("user_id") and owner.get("tenant_id"):
        return SCOPE_PERSONAL
    return SCOPE_PUBLIC


def task_owner(task: Any) -> Dict[str, Any]:
    owner = (task or {}).get("owner") or {}
    return owner if isinstance(owner, dict) else {}


def is_owner(task: Any, actor: TaskActor) -> bool:
    """Is ``actor`` the member this task was created by?

    Both ids must match: the same ``user_id`` in another tenant is a different
    account, and a task that records only one of them is not attributable.
    """
    owner = task_owner(task)
    return bool(actor.user_id and owner.get("user_id") == actor.user_id
                and owner.get("tenant_id") == actor.tenant_id)


def task_is_quarantined(task: Any) -> bool:
    return bool(((task or {}).get("quarantine") or {}).get("reason"))


def task_agent_id(task: Any) -> str:
    return (task_owner(task).get("agent_id") or (task or {}).get("agent_id") or "")


# --- the service -----------------------------------------------------------

#: Fields a caller may never set. They are derived from the verified actor or
#: from the request path, so accepting them would let a caller forge ownership
#: (``owner``/``tenant_id``), retarget delivery (``receiver``/``channel_type``)
#: or point the task at another Agent's file (``agent_id``).
FORBIDDEN_PATCH_FIELDS = frozenset({
    "owner", "tenant_id", "user_id", "agent_id", "scope", "revision",
    "write_coordinator", "quarantine", "path", "store_path",
})

#: ``action`` fields the Web editor may change. The receiver identity is
#: channel-bound and cannot be re-derived from an edit, so retargeting delivery
#: is refused here for the same reason the handler refused it before — now in
#: one place instead of only on the HTTP path.
FORBIDDEN_ACTION_FIELDS = frozenset({"receiver", "channel_type"})


class TaskAccessService:
    """Authorize and perform every scheduled-task operation.

    ``store_resolver`` maps ``(actor, agent_id)`` to a ``TaskStore`` for that
    Agent. The Web layer supplies one backed by the Agent registry; the tool
    supplies its own store. Keeping it injectable is what lets this module own
    the policy without owning the layout.
    """

    def __init__(self, *, store_resolver: Callable[[TaskActor, str], Any],
                 agent_ids: Optional[Callable[[TaskActor], Sequence[str]]] = None,
                 scope_resolver: Optional[Callable[[TaskActor, str], Dict[str, Any]]] = None,
                 identity_service: Any = None,
                 run_hook: Optional[Callable[[TaskActor, str, str], Any]] = None,
                 quota_check: Optional[Callable[[TaskActor, str, int], None]] = None,
                 coordinator: str = "unknown",
                 audit: Optional[Callable[..., Any]] = None) -> None:
        self._store_resolver = store_resolver
        self._agent_ids = agent_ids
        self._scope_resolver = scope_resolver
        self._service = identity_service
        self._run_hook = run_hook
        self._quota_check = quota_check
        self.coordinator = coordinator or "unknown"
        self._audit_hook = audit

    # -- lazily-resolved collaborators -------------------------------------

    @property
    def service(self):
        if self._service is None:
            self._service = _identity_service()
        return self._service

    # -- agent scope --------------------------------------------------------

    def agent_scope(self, actor: TaskActor, agent_id: str) -> Dict[str, Any]:
        """What the service knows about one Agent for this actor.

        ``scope_resolver`` (the Web/tool layer) answers with the Agent's binding
        and whether the caller holds ``agent.use``; without one, only the
        resolved store is available, which is the tool's case: the tool is
        already running inside that Agent, so the Agent boundary is established
        by the caller's identity alone.
        """
        if self._scope_resolver is not None:
            return dict(self._scope_resolver(actor, agent_id) or {})
        return {"agent_id": agent_id}

    def store(self, actor: TaskActor, agent_id: str) -> Any:
        try:
            store = self._store_resolver(actor, agent_id)
        except TaskAuthorizationError:
            raise
        except Exception as error:
            logger.info("[scheduler_authz] no store for agent=%s: %s", agent_id, error)
            raise TaskAuthorizationError(UNKNOWN_AGENT, status=404) from None
        if store is None:
            raise TaskAuthorizationError(UNKNOWN_AGENT, status=404)
        return store

    def list_agent_ids(self, actor: TaskActor, requested: Optional[str] = None) -> List[str]:
        if requested:
            return [requested]
        if self._agent_ids is not None:
            return list(self._agent_ids(actor) or ())
        return []

    # -- decisions ---------------------------------------------------------

    def decide(self, task: Any, actor: TaskActor, agent_id: str,
               action: str) -> Tuple[bool, str]:
        """``(allowed, code)`` for one action on one task.

        Pure: no I/O, no mutation. Both the HTTP layer (to render which buttons
        to enable) and the mutating methods call it, so the buttons and the
        server cannot disagree.
        """
        if not actor.is_member:
            return False, NOT_MEMBER
        if task is None:
            return False, UNKNOWN_TASK
        if agent_id and task_agent_id(task) and task_agent_id(task) != agent_id:
            # The task was addressed through the wrong Agent's store: the store
            # it lives in *is* its Agent, so a mismatch is a forged path.
            return False, UNKNOWN_TASK

        scope = task_scope(task)
        if scope == SCOPE_PERSONAL:
            if not is_owner(task, actor):
                return False, NOT_OWNER
            if action == ACTION_RUN and task_is_quarantined(task):
                return False, QUARANTINED
            # Own task: view and manage are ownership rights, not uses of the
            # Agent, so a revoked agent.use does not strand the task.
            return True, ""

        # Public (Agent-owned) task.
        if actor.is_admin:
            if action == ACTION_RUN and task_is_quarantined(task):
                return False, QUARANTINED
            return True, ""
        if action in (ACTION_VIEW,):
            return True, ""
        return False, NOT_OWNER

    def capabilities(self, actor: TaskActor, agent_id: str,
                     task: Any = None) -> Dict[str, bool]:
        """The action projection the console renders, derived from :meth:`decide`."""
        actions = (ACTION_VIEW, ACTION_MANAGE, ACTION_RUN)
        if task is None:
            allowed = self._creation_capabilities(actor, agent_id)
            return {name: name in allowed for name in actions}
        return {name: self.decide(task, actor, agent_id, name)[0]
                for name in actions}

    def _creation_capabilities(self, actor: TaskActor, agent_id: str) -> Tuple[str, ...]:
        if not actor.is_member:
            return ()
        if self._can_use_agent(actor, agent_id):
            return (ACTION_VIEW, ACTION_MANAGE, ACTION_RUN)
        # Without agent.use a member may still read the Agent's public schedule
        # and their own tasks, but cannot add to it.
        return (ACTION_VIEW,)

    def _can_use_agent(self, actor: TaskActor, agent_id: str) -> bool:
        """``agent.use`` on this Agent for this actor (admins included)."""
        if actor.is_admin:
            return True
        scope = self.agent_scope(actor, agent_id)
        if "can_use" in scope:
            return bool(scope["can_use"])
        svc = self.service
        if svc is None or not actor.is_member:
            return False
        try:
            return bool(svc.check_resource_action(
                actor.user_id, actor.tenant_id, "agent", f"agent:{agent_id}",
                "use", permission="agent.use"))
        except Exception as error:
            logger.warning("[scheduler_authz] agent.use check failed: %s", error)
            return False

    def _require(self, task: Any, actor: TaskActor, agent_id: str,
                 action: str, *, label: Optional[str] = None) -> None:
        allowed, code = self.decide(task, actor, agent_id, action)
        if not allowed:
            self._audit(actor, agent_id, label or action, None, "denied", code)
            status = 404 if code in (UNKNOWN_TASK, UNKNOWN_AGENT) else 403
            raise TaskAuthorizationError(code, status=status)

    # -- reads -------------------------------------------------------------

    def list_tasks(self, actor: TaskActor, *, agent_id: Optional[str] = None,
                   page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """Authorize, then aggregate, then paginate (task 3.2).

        The order matters and is the point: the Agent is authorized *before* its
        store is read, so a refused Agent never has its file opened, let alone
        its task names returned. Filtering afterwards and returning a page of
        whatever survived is the pattern this replaces — it leaks the count of
        other members' tasks and turns pagination into a lie.
        """
        if not actor.is_member:
            raise TaskAuthorizationError(NOT_MEMBER)

        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 20), 200))

        visible: List[Dict[str, Any]] = []
        counts = {"personal": 0, "public": 0, "manageable": 0, "runnable": 0}
        per_agent: List[Dict[str, Any]] = []
        for candidate in self.list_agent_ids(actor, agent_id):
            scope = self.agent_scope(actor, candidate)
            if scope.get("bound_tenant") and scope.get("bound_tenant") != actor.tenant_id:
                if not actor.is_platform_admin:
                    continue
            try:
                store = self.store(actor, candidate)
                tasks = store.list_tasks()
            except TaskAuthorizationError:
                # An Agent this caller cannot address is skipped, not fatal:
                # the aggregate is over what they may see.
                continue
            agent_visible = 0
            for task in tasks:
                allowed, _code = self.decide(task, actor, candidate, ACTION_VIEW)
                if not allowed:
                    continue
                entry = dict(task)
                entry["agent_id"] = candidate
                entry.setdefault("scope", task_scope(task))
                entry["capabilities"] = self.capabilities(actor, candidate, task)
                visible.append(entry)
                agent_visible += 1
                counts[entry["scope"]] = counts.get(entry["scope"], 0) + 1
                if entry["capabilities"].get(ACTION_MANAGE):
                    counts["manageable"] += 1
                if entry["capabilities"].get(ACTION_RUN):
                    counts["runnable"] += 1
            per_agent.append({"agent_id": candidate, "tasks": agent_visible})

        visible.sort(key=lambda item: (not item.get("enabled", True),
                                      item.get("next_run_at") or "9999-12-31"))
        total = len(visible)
        start = (page - 1) * page_size
        return {
            "tasks": visible[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "counts": counts,
            "agents": per_agent,
        }

    def get_task(self, actor: TaskActor, agent_id: str, task_id: str) -> Dict[str, Any]:
        store = self.store(actor, agent_id)
        task = store.get_task(task_id)
        self._require(task, actor, agent_id, ACTION_VIEW)
        entry = dict(task)
        entry["agent_id"] = agent_id
        entry.setdefault("scope", task_scope(task))
        entry["capabilities"] = self.capabilities(actor, agent_id, task)
        return entry

    # -- writes ------------------------------------------------------------

    def create_task(self, actor: TaskActor, agent_id: str,
                    task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a new task with a trustworthy owner, or refuse.

        A task with no owner cannot be revalidated at fire time and would
        execute under the Agent's identity for ever, so an unresolvable owner is
        a refusal rather than a task with a missing field (task 3.2).
        """
        if not actor.is_member:
            self._audit(actor, agent_id, "create", None, "denied", NOT_MEMBER)
            raise TaskAuthorizationError(NOT_MEMBER)
        scope = (task_data.get("scope") or SCOPE_PERSONAL)
        if scope not in (SCOPE_PERSONAL, SCOPE_PUBLIC):
            raise TaskAuthorizationError(FORGED_FIELD, status=400)
        if scope == SCOPE_PUBLIC and not actor.is_admin:
            raise TaskAuthorizationError(NOT_OWNER)
        # Resolve the address before the grant check: "no such Agent" and "no
        # grant on that Agent" are different answers and the console renders
        # them differently.
        store = self.store(actor, agent_id)
        if not self._can_use_agent(actor, agent_id):
            self._audit(actor, agent_id, "create", None, "denied", AGENT_DENIED)
            raise TaskAuthorizationError(AGENT_DENIED)
        # ``scope`` is the only field a caller may set here: the owner, tenant,
        # Agent and revision are derived from the verified actor, so accepting
        # them would let a caller forge ownership (task 3.2).
        for field in FORBIDDEN_PATCH_FIELDS - {"scope"}:
            if field in task_data:
                raise TaskAuthorizationError(FORGED_FIELD, status=400)

        self._enforce_quota(actor, agent_id, store, adding=1)

        task = dict(task_data)
        task["agent_id"] = agent_id
        task["scope"] = scope
        if scope == SCOPE_PERSONAL:
            task["owner"] = {
                "user_id": actor.user_id,
                "tenant_id": actor.tenant_id,
                "agent_id": agent_id,
                "session_id": actor.session_id,
                "created_at": datetime.now().isoformat(),
            }
        else:
            task.pop("owner", None)
        task["revision"] = int(task.get("revision") or 0) + 1
        task["write_coordinator"] = self.coordinator
        task["created_by_source"] = actor.source
        store.add_task(task)
        self._audit(actor, agent_id, "create", task, "success", "")
        return task

    def update_task(self, actor: TaskActor, agent_id: str, task_id: str,
                    patch: Dict[str, Any], *, expected_revision: Optional[int] = None
                    ) -> Dict[str, Any]:
        """Edit a task, refusing forged fields and stale revisions."""
        store = self.store(actor, agent_id)
        original = store.get_task(task_id)
        self._require(original, actor, agent_id, ACTION_MANAGE, label="update")
        if not isinstance(patch, dict):
            raise TaskAuthorizationError(FORGED_FIELD, status=400)
        for field in patch:
            if field in FORBIDDEN_PATCH_FIELDS:
                self._audit(actor, agent_id, "update", original, "denied", FORGED_FIELD)
                raise TaskAuthorizationError(FORGED_FIELD, status=400)
        if "action" in patch:
            action_patch = patch["action"]
            if not isinstance(action_patch, dict):
                raise TaskAuthorizationError(FORGED_FIELD, status=400)
            contested = FORBIDDEN_ACTION_FIELDS & set(action_patch)
            if contested and any(
                    action_patch[field] != (original.get("action") or {}).get(field)
                    for field in contested):
                # Retargeting delivery from an edit is refused: the receiver is
                # channel-bound and cannot be re-derived here.
                self._audit(actor, agent_id, "update", original, "denied", FORGED_FIELD)
                raise TaskAuthorizationError(FORGED_FIELD, status=400)

        updates = merge_action_patch(original, patch)
        updates["write_coordinator"] = self.coordinator
        try:
            store.update_task(task_id, updates,
                              expected_revision=expected_revision)
        except TaskRevisionConflict as conflict:
            self._audit(actor, agent_id, "update", original, "conflict",
                        REVISION_CONFLICT)
            raise TaskAuthorizationError(REVISION_CONFLICT, status=409) from conflict
        task = store.get_task(task_id)
        self._audit(actor, agent_id, "update", task, "success", "")
        return task

    def delete_task(self, actor: TaskActor, agent_id: str, task_id: str) -> Dict[str, Any]:
        store = self.store(actor, agent_id)
        task = store.get_task(task_id)
        self._require(task, actor, agent_id, ACTION_MANAGE, label="delete")
        store.delete_task(task_id)
        self._audit(actor, agent_id, "delete", task, "success", "")
        return task

    def set_enabled(self, actor: TaskActor, agent_id: str, task_id: str,
                    enabled: bool) -> Dict[str, Any]:
        store = self.store(actor, agent_id)
        task = store.get_task(task_id)
        label = "enable" if enabled else "disable"
        self._require(task, actor, agent_id, ACTION_MANAGE, label=label)
        if enabled:
            # Enabling is what makes a stored task execute, so the quota gate
            # belongs here as much as on creation (task 3.3): otherwise a
            # member could keep a task disabled past the limit and enable it
            # afterwards.
            self._enforce_quota(actor, agent_id, store, adding=0)
        store.update_task(task_id,
                          {"enabled": bool(enabled),
                           "write_coordinator": self.coordinator})
        result = store.get_task(task_id)
        self._audit(actor, agent_id, label, result, "success", "")
        return result

    def run_task(self, actor: TaskActor, agent_id: str, task_id: str,
                 run_key: Optional[str] = None) -> Dict[str, Any]:
        """Fire a task now through the injected runner. Idempotent per key.

        The manual run reuses the *stored* task (so it fires under the recorded
        owner) and the caller's own grants are re-checked first; the runner is
        the same one the scheduler service uses, so a manual run cannot take a
        path a scheduled fire would refuse.

        ``run_key`` is the "same request" proof from the spec's retry clause: a
        client whose response was lost resends the *same* key and gets the
        recorded outcome back instead of a second enqueue. Without a key the
        only protection is the runner's own "already running" answer, which
        covers a concurrent double click but not a retry that arrives after the
        first fire finished -- so the console sends a key per click.
        """
        store = self.store(actor, agent_id)
        task = store.get_task(task_id)
        self._require(task, actor, agent_id, ACTION_RUN, label="run")
        if not self._can_use_agent(actor, agent_id):
            reason = _run_reason(task, agent_id)
            self._audit(actor, agent_id, "run", task, "denied", reason)
            raise TaskAuthorizationError(reason)
        # "Same request" means the same caller, the same task and the same
        # client key: a key minted for one task or one member cannot be replayed
        # against another.
        receipt_key = ""
        if run_key:
            receipt_key = "\x1f".join((actor.tenant_id or "", actor.user_id or "",
                                       agent_id, task_id, str(run_key)))
            remembered = _recall_run(receipt_key)
            if remembered is not None:
                self._audit(actor, agent_id, "run", task, "duplicate", "")
                return remembered
        if self._run_hook is None:
            raise TaskAuthorizationError(RUN_DENIED, status=503)
        try:
            self._run_hook(actor, agent_id, task_id)
        except TaskAuthorizationError:
            raise
        except Exception as error:
            message = str(error)
            if "already running" in message.lower():
                # A manual run is idempotent in effect: the task is already
                # executing, so the second click is refused instead of queueing a
                # duplicate fire.
                code, status = ALREADY_RUNNING, 409
            elif "not found" in message.lower():
                code, status = RUN_DENIED, 404
            else:
                code, status = "run_failed", 409
            self._audit(actor, agent_id, "run", task, "denied", code)
            raise TaskAuthorizationError(code, status=status, message=message) from None
        self._audit(actor, agent_id, "run", task, "success", "")
        if receipt_key:
            _remember_run(receipt_key, task)
        return task

    # -- migration ---------------------------------------------------------

    def migrate_tasks(self, actor: TaskActor, *, agent_ids: Optional[Sequence[str]] = None,
                      apply: bool = False) -> Dict[str, Any]:
        """Stamp scope onto legacy tasks and quarantine the unattributable ones.

        Runs per Agent store and is idempotent: a second pass finds everything
        already stamped and reports ``unchanged``. Quarantine disables the task
        and records a redacted reason plus a recovery hint; it never assigns the
        task to whoever is running the migration (task 4.2/4.3).

        Three-way classification per task, and the middle case is the one the
        database runtime cannot keep: a task with no member owner and no explicit
        ``public`` scope is neither attributable nor declared Agent-owned, so it
        is quarantined instead of executing under the Agent's identity.
        """
        report: Dict[str, Any] = {"agents": [], "stamped": 0, "quarantined": 0,
                                  "quarantined_tasks": [], "unchanged": 0,
                                  "apply": bool(apply)}
        for agent_id in (agent_ids or self.list_agent_ids(actor, None)):
            try:
                store = self.store(actor, agent_id)
                tasks = store.list_tasks()
            except TaskAuthorizationError:
                continue
            entry = {"agent_id": agent_id, "stamped": 0, "quarantined": 0,
                     "unchanged": 0}
            for task in tasks:
                owner = task_owner(task)
                has_member_owner = bool(owner.get("user_id") and owner.get("tenant_id"))
                declared = task.get("scope")
                if declared in (SCOPE_PERSONAL, SCOPE_PUBLIC):
                    entry["unchanged"] += 1
                    report["unchanged"] += 1
                    continue
                patch: Dict[str, Any] = {"write_coordinator": self.coordinator}
                if has_member_owner:
                    patch["scope"] = SCOPE_PERSONAL
                    entry["stamped"] += 1
                    report["stamped"] += 1
                else:
                    patch["scope"] = SCOPE_PUBLIC
                    patch.update(quarantine_fields("no_owner"))
                    entry["quarantined"] += 1
                    report["quarantined"] += 1
                    report["quarantined_tasks"].append(
                        {"agent_id": agent_id, "task_id": task.get("id")})
                if apply:
                    store.update_task(task["id"], patch)
            report["agents"].append(entry)
        return report

    # -- internals ---------------------------------------------------------

    def _enforce_quota(self, actor: TaskActor, agent_id: str, store: Any,
                       *, adding: int) -> None:
        """Refuse when the actor's own task count would exceed the limit.

        Counting the actor's own personal tasks is what makes the limit
        per-member rather than per-Agent: on a shared public Agent one member's
        schedule must not consume another's allowance. The gate is injected so
        the same check serves the HTTP, tool and enable paths.
        """
        if not actor.is_member or self._quota_check is None:
            return
        try:
            own = [t for t in store.list_tasks()
                   if task_scope(t) == SCOPE_PERSONAL and is_owner(t, actor)]
        except Exception as error:
            logger.warning("[scheduler_authz] quota count failed: %s", error)
            raise TaskAuthorizationError(QUOTA_EXCEEDED, status=409) from None
        try:
            self._quota_check(actor, agent_id, len(own) + adding)
        except TaskAuthorizationError:
            raise
        except Exception as error:
            raise _translate_quota_error(error) from None

    def _audit(self, actor: TaskActor, agent_id: str, action: str, task: Any,
               result: str, reason: str) -> None:
        """Record a redacted scheduler audit event.

        Redacted means the event names the task and the outcome and carries none
        of its content: no ``action.content``/``task_description`` (the member's
        own text, which may quote anything), no receiver, no notify session. The
        audit's purpose here is "who changed which task", which those identifiers
        answer completely.
        """
        payload = {
            "agent_id": agent_id,
            "task_id": (task or {}).get("id"),
            "scope": task_scope(task) if task else None,
            "revision": (task or {}).get("revision"),
            "reason": reason or None,
        }
        if self._audit_hook is not None:
            try:
                self._audit_hook(actor=actor, action=action, result=result,
                                 payload=payload)
                return
            except Exception as error:
                logger.warning("[scheduler_authz] audit hook failed: %s", error)
        svc = self.service
        if svc is None or not actor.is_member:
            return
        try:
            svc.record_business_audit(
                actor_user_id=actor.user_id, tenant_id=actor.tenant_id,
                action=f"scheduler.{action}", target=f"task:{payload['task_id']}",
                redacted_changes=payload, result=result,
            )
        except Exception as error:
            # An audit failure must not silently authorize: the caller already
            # performed the operation, so this is loud.
            logger.error("[scheduler_authz] audit write failed: %s", error)


def _quota_metric() -> str:
    """The quota metric scheduled-task creation is metered against."""
    return "scheduled_tasks"


def _translate_quota_error(error: Exception) -> TaskAuthorizationError:
    """Map an injected quota gate's failure onto the stable refusal code.

    Fail closed: a gate that cannot answer has not said "within limit", and a
    broken meter silently becoming free unlimited task creation is the failure
    mode this mapping exists to prevent.
    """
    if getattr(error, "code", "") not in ("quota_exceeded", QUOTA_EXCEEDED):
        logger.warning("[scheduler_authz] quota gate failed: %s", error)
    return TaskAuthorizationError(QUOTA_EXCEEDED, status=409)


def _run_reason(task: Any, agent_id: str) -> str:
    """The most specific run refusal for a task the caller may otherwise manage."""
    from agent.tools.scheduler.identity import revalidate_owner
    reason = revalidate_owner(task)
    if reason:
        return reason
    if not task_agent_id(task):
        return RUN_DENIED
    return AGENT_DENIED


# ---------------------------------------------------------------------------
# Manual-run receipts
# ---------------------------------------------------------------------------
#
# ``run_key`` closes the spec's retry clause: "the same idempotent run request
# forms at most one effective enqueue". The runner's own "already running" guard
# covers two clicks inside one execution; a retry that arrives *after* the first
# fire finished would otherwise enqueue a second one. Remembering the accepted
# outcome by (tenant, member, agent, task, client key) turns the resend into a
# read-back instead.
#
# Process-local, like ``auth.scan_authorization``'s grants: a multi-writer
# deployment must refuse this capability rather than let each worker keep its own
# ledger (``TaskWriteLease`` enforces the same rule for writes).

#: How long a run receipt stays readable: long enough for a client retry, short
#: enough that the ledger cannot grow without bound.
RUN_RECEIPT_TTL_SECONDS = 600.0
_RUN_RECEIPT_LIMIT = 512

_run_lock = threading.Lock()
_run_receipts: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()


def _recall_run(key: str) -> Optional[Dict[str, Any]]:
    """The task a previous accepted run with this key returned, if still fresh."""
    now = time.time()
    with _run_lock:
        for stale in [k for k, (at, _) in _run_receipts.items()
                      if now - at > RUN_RECEIPT_TTL_SECONDS]:
            _run_receipts.pop(stale, None)
        entry = _run_receipts.get(key)
        if entry is None:
            return None
        # A copy, so a caller mutating the returned task cannot edit the ledger.
        return dict(entry[1])


def _remember_run(key: str, task: Dict[str, Any]) -> None:
    with _run_lock:
        _run_receipts[key] = (time.time(), dict(task or {}))
        while len(_run_receipts) > _RUN_RECEIPT_LIMIT:
            _run_receipts.popitem(last=False)


def _reset_run_receipts() -> None:
    """Test hook: forget every manual-run receipt."""
    with _run_lock:
        _run_receipts.clear()


def quarantine_fields(reason: str) -> Dict[str, Any]:
    """The task fields written when a task is quarantined (task 4.2).

    ``enabled`` is forced false and the reason is a code, so the console can
    localize it and the stored task carries no free-form text.
    """
    return {
        "enabled": False,
        "quarantine": {
            "reason": reason,
            "at": datetime.now().isoformat(),
            "restore_hint": "ask_owner_to_recreate" if reason == "no_owner"
            else "review_and_reenable",
        },
    }


def merge_action_patch(original: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a caller's ``action`` patch into the stored action.

    The Web editor exposes a subset of an action's fields, and the stored action
    also holds scheduler metadata (``notify_session_id``, ``silent``,
    channel-specific delivery ids) that an unrelated edit must not drop. The
    mutually exclusive content fields are pruned by type, exactly as the handler
    did, so the merge cannot leave a task with both a fixed message and an
    AI task.
    """
    updates = {k: v for k, v in (patch or {}).items() if k != "action"}
    if "action" not in (patch or {}):
        return updates
    original_action = original.get("action") if isinstance(original.get("action"), dict) else {}
    action = dict(original_action)
    action.update(patch["action"])
    action_type = action.get("type")
    if action_type == "send_message":
        action.pop("task_description", None)
        action.pop("silent", None)
    elif action_type == "agent_task":
        action.pop("content", None)
    old_channel = original_action.get("channel_type", "web")
    action["channel_type"] = action.get("channel_type") or old_channel
    updates["action"] = action
    return updates


# Imported late: ``task_store`` imports this module's constants for its revision
# guard, so importing it at module scope would be circular.
from agent.tools.scheduler.task_store import TaskRevisionConflict  # noqa: E402
