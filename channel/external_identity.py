# encoding:utf-8
"""External IM identity resolution for database mode (task 4.x).

Inbound IM messages have no web session: the author is authenticated by the
provider's own identity and must be bound to a local account via
``external_identities`` (admin pre-binding). This module is the single entry
point every IM channel uses to turn an inbound author into a tenant-scoped
``RequestContext`` before any model/tool execution.

Rules (mirroring the Web chat gates, decision 4 in open-database-runtime):
  * The channel's bound Agent only selects the *workspace/routing* — it never
    grants identity. Execution always runs as the resolved tenant member.
  * The resolved user must be an active member of the Agent's bound tenant and
    hold the same gates as Web chat: functional ``chat.use`` + the Agent's
    ``agent.use`` resource grant (platform/tenant-admin bypass unchanged).
  * No binding / inactive user / missing membership / forced password change /
    missing grant => a fixed, user-facing notice and **no** model call.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("external_identity")


#: Stable deny reasons the channel layer turns into fixed, localizable notices.
UNBOUND = "external_unbound"
NOT_MEMBER = "external_not_member"
TENANT_UNBOUND = "external_agent_not_tenant_bound"
PASSWORD_CHANGE_REQUIRED = "external_password_change_required"
PERMISSION_DENIED = "external_permission_denied"
UNSUPPORTED_CHANNEL = "external_channel_unsupported"


def is_database_mode() -> bool:
    """True when the deployment runs database identity mode."""
    from config import conf

    return str(conf().get("identity_mode", "legacy") or "legacy") == "database"


def deny_notice(reason: str) -> str:
    """Fixed, user-facing reply for a denied IM inbound message.

    Bilingual like the other channel notices; deliberately terse so it survives
    IM length limits and points the user at the administrator.
    """
    zh, en = {
        UNBOUND: (
            "你的账号尚未与系统绑定，请联系管理员完成绑定后重试。",
            "Your account is not bound yet. Ask an administrator to bind it, then retry.",
        ),
        NOT_MEMBER: (
            "你的账号不属于该助手所在的组织，请联系管理员。",
            "Your account does not belong to this assistant's organization. Contact an administrator.",
        ),
        TENANT_UNBOUND: (
            "该助手未绑定任何组织，请联系管理员。",
            "This assistant is not bound to an organization. Contact an administrator.",
        ),
        PASSWORD_CHANGE_REQUIRED: (
            "请先在网页端修改初始密码后，再通过消息渠道对话。",
            "Please set your password in the web console before chatting here.",
        ),
        PERMISSION_DENIED: (
            "你暂时没有使用该助手的权限，请联系管理员开通。",
            "You do not have permission to use this assistant yet. Ask an administrator to grant access.",
        ),
        UNSUPPORTED_CHANNEL: (
            "该消息渠道在数据库模式下尚未开放，请联系管理员。",
            "This channel is not open in database mode yet. Contact an administrator.",
        ),
    }.get(reason) or (
        "消息无法处理，请联系管理员。",
        "Message could not be processed. Contact an administrator.",
    )
    return f"{zh}\n{en}"


def stamp_external_identity(context: dict, *, provider: str, issuer: str,
                            subject: str) -> dict:
    """Attach the inbound author's identity triple to a context.

    Called by each IM channel right before ``produce``; ``issuer`` is the
    provider's corp/tenant identifier (e.g. a Feishu app id — empty string
    means "no issuer scope" for providers that are inherently single-tenant).
    """
    context["external_identity"] = {
        "provider": (provider or "").strip().lower(),
        "issuer": (issuer or "").strip(),
        "subject": (subject or "").strip(),
    }
    return context


def resolve_actor_for_context(context: dict, agent_id: Optional[str]):
    """Resolve a DB-mode inbound context to its tenant member ``RequestContext``.

    Returns ``(ctx, None)`` on success and ``(None, reason_code)`` on a deny.
    Every deny already implies "do not call the model". ``agent_id`` is the
    channel-routed Agent (its binding fixes the tenant the member must belong
    to). Only call this when ``is_database_mode()`` and the context carries a
    stamped ``external_identity``.
    """
    from auth.service import get_identity_service

    ext = context.get("external_identity")
    if not ext:
        return None, UNSUPPORTED_CHANNEL
    provider = str(ext.get("provider") or "").strip().lower()
    issuer = str(ext.get("issuer") or "").strip()
    subject = str(ext.get("subject") or "").strip()
    if not provider or not subject:
        logger.warning("[external_identity] incomplete triple provider=%s subject=%s", provider, subject)
        return None, UNBOUND

    svc = get_identity_service()
    user = svc.find_user_for_external_identity(provider, issuer, subject)
    if not user:
        return None, UNBOUND

    # The Agent's binding fixes the tenant the member must execute in.
    binding = svc.get_agent_binding(agent_id) if agent_id else None
    if not binding:
        return None, TENANT_UNBOUND
    tenant_id = binding["tenant_id"]

    try:
        from auth.runtime import IdentityContextError, member_context

        ctx = member_context(svc, user["id"], tenant_id)
    except IdentityContextError as error:
        if getattr(error, "code", "") == "password_change_required":
            return None, PASSWORD_CHANGE_REQUIRED
        return None, NOT_MEMBER
    if ctx.must_change_password:
        return None, PASSWORD_CHANGE_REQUIRED

    # Same gates as Web chat: functional chat.use and the agent.use resource grant.
    if not (ctx.is_platform_admin or ctx.is_tenant_admin
            or "chat.use" in (ctx.permissions or ())):
        return None, PERMISSION_DENIED
    if not svc.check_resource_action(
        ctx.user_id, ctx.tenant_id, "agent", f"agent:{agent_id}",
        "use", permission="agent.use",
    ):
        return None, PERMISSION_DENIED
    return ctx, None
