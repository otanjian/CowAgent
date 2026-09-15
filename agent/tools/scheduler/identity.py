# encoding:utf-8
"""Creator identity snapshot + trigger-time revalidation for scheduled tasks.

Background scheduler threads carry no request identity of their own (the same
thread runs every Agent's tasks in a fleet). In database identity mode a task
is created *by a tenant member* — chat.use + ``agent.use`` on the routed Agent
— inside one conversation, then fires later on a timer. ``open-database-runtime``
5.x closes that gap:

* ``owner_snapshot`` — persisted at task creation (SchedulerTool), capturing the
  creating member (user/tenant) plus the target Agent and chat session so a
  later fire knows *who* asked and *under whose grants* the run may happen.
* ``revalidate_owner`` — run again at trigger time. If the member was removed,
  left the tenant, still owes a password change, or lost ``chat.use`` /
  ``agent.use`` on the target Agent, the fire is skipped and the reason is
  recorded on the task (``last_skip_reason``/``last_skip_at``); the stored
  schedule is untouched so a recurring task simply resumes when access returns.

Legacy tasks (no owner snapshot, or user/tenant empty) keep their historical
behaviour: no identity gate and no snapshot.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("scheduler_identity")

#: Stable machine reasons persisted to the task on a skipped fire.
NOT_MEMBER = "not_member"
PASSWORD_CHANGE_REQUIRED = "password_change_required"
CHAT_DENIED = "chat_denied"
AGENT_DENIED = "agent_denied"
AGENT_UNBOUND = "agent_unbound"
UNRESOLVED = "unresolved"
#: The task carries no member owner while the deployment is on database identity.
#: Nothing proves who asked for it, so executing it would run under the Agent's
#: own identity — the "default owner" path this change removes (task 4.2). The
#: migration quarantines these tasks; this is the runtime backstop for any that
#: were written afterwards.
UNATTRIBUTED = "unattributed"

_REASON_TEXT = {
    NOT_MEMBER: ("账号已停用或不属于该组织，任务已暂停执行。", "Account is inactive or not a member; task skipped."),
    PASSWORD_CHANGE_REQUIRED: ("账号尚未完成密码设置，任务已暂停执行。", "Password change pending; task skipped."),
    CHAT_DENIED: ("对话权限已收回，任务已暂停执行。", "Chat permission revoked; task skipped."),
    AGENT_DENIED: ("该助手的使用权限已收回，任务已暂停执行。", "Agent use permission revoked; task skipped."),
    AGENT_UNBOUND: ("助手已与组织解绑，任务已暂停执行。", "Agent is no longer bound to the tenant; task skipped."),
    UNRESOLVED: ("无法完成权限复核，任务已暂停执行。", "Revalidation unavailable; task skipped."),
    UNATTRIBUTED: (
        "任务缺少创建者归属，已暂停执行；请由本人在对话中重新创建。",
        "Task has no member owner; skipped. Ask the member to recreate it."),
}

#: Skip a task after this many consecutive denied fires (log noise / store
#: growth guard). The task is disabled so it stops scanning every tick; an
#: admin re-enables it after fixing the membership/grant.
MAX_CONSECUTIVE_SKIPS = 20


def _is_database_identity(owner: dict) -> bool:
    return bool(owner and owner.get("user_id") and owner.get("tenant_id"))


def database_identity_enforced() -> bool:
    """Whether this deployment runs the database identity mode.

    Always true today — database is the only identity mode — but kept as a
    function rather than a literal so the legacy branch stays expressible and
    both sides can be pinned by tests. ``identity_mode`` is the switch a
    deployment would flip; anything unreadable defaults to database, because
    failing towards "attributes the task" is the safe direction.
    """
    try:
        from config import conf
        return str(conf().get("identity_mode") or "database").strip().lower() == "database"
    except Exception:
        return True


def owner_snapshot(context) -> Optional[dict]:
    """Snapshot the creating member onto a task at creation time.

    Returns ``None`` in legacy mode / outside a tenant-member run (no identity,
    no user) so plain single-user installs are untouched. ``context`` is the
    SchedulerTool's current ``Context``.
    """
    from common.runtime_identity import current_identity

    rt = current_identity()
    if (context or {}).get("runtime_identity"):
        # Threads without an ambient identity still carry the request snapshot
        # the message handler put on the context.
        rt = rt.derive(
            user_id=(context["runtime_identity"].get("user_id") or rt.user_id),
            tenant_id=(context["runtime_identity"].get("tenant_id") or rt.tenant_id),
            agent_id=(context["runtime_identity"].get("agent_id") or rt.agent_id),
        )
    if not (rt.user_id and rt.tenant_id):
        return None
    return {
        "user_id": rt.user_id,
        "tenant_id": rt.tenant_id,
        "agent_id": (context or {}).get("agent_id") or rt.agent_id,
        "session_id": (context or {}).get("session_id") or rt.session_id,
        "created_at": datetime.now().isoformat(),
    }


def execution_identity(task: dict, agent_id: Optional[str] = None):
    """The identity a stored task fires under — the single resolver (8.15/8.16).

    A scheduled task has exactly two possible shapes, and this is the one place
    that decides which applies:

    * **tenant member** — the task carries an ``owner`` snapshot taken at
      creation. The fire re-runs as that member, so workspace, conversation
      state and memory resolve the way they did for the person who asked. The
      session is the task's notify session when it has one, so a recurring task
      keeps reporting into the conversation it came from;
    * **Agent-only** — a legacy task (no owner, or an owner without
      user/tenant). The historical Agent-scoped behaviour is kept exactly.

    Nothing else here: authorization (``revalidate_owner``) runs *before* this
    and is a policy over the snapshot, and delivery/run-recording are downstream
    of the fire. Keeping the decision in one function is what stops a second
    resolver appearing next to it — the failure mode this convergence exists to
    remove (8.16).

    Identity reaches the runtime only through ``common/runtime_identity``.
    """
    from common.runtime_identity import RuntimeIdentity

    owner = (task or {}).get("owner") or {}
    if owner.get("user_id") and owner.get("tenant_id"):
        return RuntimeIdentity(
            agent_id=agent_id,
            user_id=owner["user_id"],
            tenant_id=owner["tenant_id"],
            session_id=((task or {}).get("action") or {}).get("notify_session_id")
            or owner.get("session_id") or "",
        )
    return RuntimeIdentity(agent_id=agent_id)


def revalidate_owner(task) -> Optional[str]:
    """Re-check the task's creator before a fire.

    Returns ``None`` when the fire may proceed; a stable machine ``reason``
    (see module constants) when it must be skipped. A task with no member owner
    is skipped as :data:`UNATTRIBUTED` while the deployment is on database
    identity: the task would otherwise execute as the Agent itself, which is the
    default-owner path task 4.2 removes. In a legacy deployment the historical
    behaviour is kept exactly (no owner, no gate).

    Mirrors the Web chat gates (chat.use + the target Agent's ``agent.use``
    resource grant; platform/tenant-admin bypass unchanged).
    """
    owner = (task or {}).get("owner") or {}
    if not _is_database_identity(owner):
        return UNATTRIBUTED if database_identity_enforced() else None
    try:
        from auth.runtime import IdentityContextError, member_context
        from auth.service import get_identity_service

        svc = get_identity_service()
        binding = svc.get_agent_binding(owner.get("agent_id"))
        if not binding or binding.get("tenant_id") != owner.get("tenant_id"):
            return AGENT_UNBOUND
        try:
            ctx = member_context(svc, owner["user_id"], owner["tenant_id"])
        except IdentityContextError as error:
            if getattr(error, "code", "") == "password_change_required":
                return PASSWORD_CHANGE_REQUIRED
            return NOT_MEMBER
        if ctx.must_change_password:
            return PASSWORD_CHANGE_REQUIRED
        if not (ctx.is_platform_admin or ctx.is_tenant_admin
                or "chat.use" in (ctx.permissions or ())):
            return CHAT_DENIED
        if not svc.check_resource_action(
            ctx.user_id, ctx.tenant_id, "agent", f"agent:{owner.get('agent_id')}",
            "use", permission="agent.use",
        ):
            return AGENT_DENIED
        return None
    except Exception as error:  # fail closed: do not run unverifiable tasks
        logger.exception(
            "[scheduler_identity] revalidate owner failed owner=%s err=%s",
            {k: owner.get(k) for k in ("user_id", "tenant_id", "agent_id")},
            error,
        )
        return UNRESOLVED


def skip_record(reason: str) -> dict:
    """Task fields persisted when a fire is skipped."""
    now = datetime.now().isoformat()
    zh, en = _REASON_TEXT.get(reason, ("任务被跳过。", "Task skipped."))
    return {
        "last_error": f"[{reason}] {zh} {en}",
        "last_error_at": now,
        "last_skip_reason": reason,
        "last_skip_at": now,
    }
