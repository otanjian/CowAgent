"""One answer to "does anything still depend on this Agent?" (task 4.3).

Deleting an Agent is a roster removal plus a workspace removal, and neither can
be undone. Two kinds of dependency make that removal destructive rather than
tidy:

* **A channel instance still routes to it.** Inbound IM traffic addressed to
  that bot lands on the Agent; erasing the Agent under it either drops the
  messages or silently moves the bot to somebody else's Agent. The spec is
  explicit that a live reference is a **conflict**, never an automatic rebind or
  fallback (``user-private-agent-management``: 存在运行或有效渠道引用时 SHALL 返回
  冲突，不自动终止、改绑或回落).
* **A task is running in it.** A live run holds the workspace open; deleting it
  mid-flight leaves a half-erased object.

Both facts live outside the Agent — one in the identity store, one in the
running process — so the check cannot be a property of the roster entry. It is
also the *same* check for every operator: a member erasing their own object and
an administrator retiring a shared one have to get the same verdict, which is
why the rule lives here rather than in each entry point.

The answer is a **list** of reasons, not the first hit: the operator has to
clear all of them, and a second round trip to learn the second reason is pure
friction. Each string names what depends on the Agent so the message is
actionable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def bridge_has_live_agent(agent_id: str) -> bool:
    """Default runtime probe: ask the process's AgentBridge.

    A missing bridge (import error, or a process that never started channels)
    means there is no runtime to endanger, so it answers ``False``. Any other
    failure propagates to :func:`deletion_conflicts`, which fails closed.
    """
    try:
        from bridge.bridge import Bridge
    except Exception:  # pragma: no cover - defensive, import-time only
        return False
    bridge = getattr(Bridge(), "_agent_bridge", None)
    checker = getattr(bridge, "has_live_agent", None)
    if not callable(checker):
        return False
    return bool(checker(agent_id))


def runtime_is_live(agent_id: str, *, runtime_probe=None) -> bool:
    """Whether ``agent_id`` has something running, failing **closed**.

    "I could not ask" must never be read as "nothing is running": that is the
    one wrong answer that turns a conflict into a deletion racing a live task.
    """
    probe = runtime_probe or bridge_has_live_agent
    try:
        return bool(probe(agent_id))
    except Exception as exc:
        logger.warning("[DeletionGuard] runtime probe for %s failed: %s", agent_id, exc)
        return True


def roster_channel_references(agent_id: str, *, settings=None) -> List[Dict[str, Any]]:
    """Roster ``channel_instances`` records that still point at ``agent_id``.

    The roster (``team.json``) holds the instance-level channels — the ones a
    platform or tenant administrator binds to a bot. A record here is a channel
    the launcher will bring up, so unlike the tenant rows in the identity store
    there is no ``active`` flag to weigh: a bound record *is* a live route. The
    console's "disconnect" removes the record instead of flagging it.
    """
    from channel.channel_instances import resolve_channel_instances

    if settings is None:
        from agent import team
        from config import conf

        settings = team.resolve(conf())
    out: List[Dict[str, Any]] = []
    for inst in resolve_channel_instances(settings):
        if (inst.agent_id or "") != agent_id:
            continue
        out.append({
            "id": inst.instance_id,
            "channel_type": inst.channel_type,
            "display_name": inst.instance_id,
            "scope": "roster",
        })
    return out


def _tenant_channel_references(agent_id: str, *, tenant_id=None,
                               identity_service=None) -> Optional[List[Dict[str, Any]]]:
    """Active tenant/personal channel rows pointing at ``agent_id``.

    ``None`` means "could not be read", which the caller turns into a conflict.
    """
    if identity_service is None:
        try:
            from auth.service import get_identity_service

            identity_service = get_identity_service()
        except Exception as exc:
            logger.warning("[DeletionGuard] identity service unavailable: %s", exc)
            return None
    try:
        return list(identity_service.channel_instances_referencing_agent(
            tenant_id, agent_id))
    except Exception as exc:
        logger.warning(
            "[DeletionGuard] channel reference check for %s failed: %s",
            agent_id, exc)
        return None


def deletion_conflicts(agent_id: str, *, tenant_id: Optional[str] = None,
                       identity_service=None, runtime_probe=None,
                       settings=None) -> List[str]:
    """Reasons deleting ``agent_id`` right now would break something.

    Empty list = nothing depends on it, i.e. the delete may proceed. Every
    unreadable store contributes a reason rather than a pass, for the same
    fail-closed reason as the runtime probe.

    ``tenant_id`` narrows the identity-store read to the caller's tenant, which
    is what a business entry point must respect (普通业务入口 SHALL 遵守当前租户范围).
    Passing ``None`` asks the store for every tenant, which is what an
    instance-level platform entry needs: the binding it is about to erase may
    belong to a tenant the operator is not currently sitting in.
    """
    if not agent_id:
        return []

    reasons: List[str] = []

    try:
        roster_refs = roster_channel_references(agent_id, settings=settings)
    except Exception as exc:
        logger.warning(
            "[DeletionGuard] roster reference check for %s failed: %s", agent_id, exc)
        roster_refs = None
    if roster_refs is None:
        reasons.append("its channel references could not be checked")
    else:
        for ref in roster_refs:
            reasons.append(
                "channel instance '%s' (%s) still routes to this agent"
                % (ref.get("display_name") or ref.get("id"),
                   ref.get("channel_type") or "unknown"))

    tenant_refs = _tenant_channel_references(
        agent_id, tenant_id=tenant_id, identity_service=identity_service)
    if tenant_refs is None:
        reasons.append("its channel references could not be checked")
    else:
        for ref in tenant_refs:
            label = ref.get("display_name") or ref.get("id")
            scope = ref.get("scope") or "tenant"
            reasons.append(
                "channel instance '%s' (%s, %s) still routes to this agent"
                % (label, ref.get("channel_type") or "unknown", scope))

    if runtime_is_live(agent_id, runtime_probe=runtime_probe):
        reasons.append("the agent is currently running")

    return reasons


def conflict_message(reasons: List[str]) -> str:
    """The refusals as one actionable sentence."""
    return "the agent is still in use: " + "; ".join(reasons)
