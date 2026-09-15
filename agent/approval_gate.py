# encoding:utf-8
"""Which actions need a single-action approval, and the consumer that enforces it.

``auth.service`` already owns the approval *engine* — request, decide, cancel,
revoke, expire, list and the audit trail behind each. This module owns the two
things the engine deliberately does not (task 7.9):

1. **Applicability, decided server-side.** An action needs an approval when the
   *deployment* declares it in ``approval_required_actions``. Nothing a client
   sends can make an action applicable (that would be a way to skip it) or
   inapplicable (that would be a way to skip the approval); the declaration is
   read here, from configuration, and every refusal this module produces comes
   from that decision plus the delivered engine's own state.

2. **Consumption, at the real dispatch seam.** An approval is a *single*
   action's authority: it is bound to the tenant, the requesting user, the
   action identity, the target resource and the canonical digest of the
   parameters, and it is consumed exactly once, atomically, immediately before
   the side effect runs. Pending, denied, revoked, expired, mismatched and
   already-consumed approvals all refuse, each with its own code, so the caller
   can say "still waiting" rather than "not allowed".

Both seams that dispatch an action out of the process call
:func:`approval_decision`: the Agent's tool dispatch
(``agent/protocol/agent_stream.py``) and the scheduler's outbound delivery
(``agent/tools/scheduler/integration.py``). Neither re-implements the check.

Why "not applicable" is *recorded* rather than omitted: "this action needs no
approval" is a decision with a basis, and the basis is what a later reviewer
reads when they ask whether the gate was skipped or never required. A class whose
basis is only in a code comment is indistinguishable from a missing gate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, NamedTuple, Optional

#: The argument a caller uses to point at its approval. Double-underscored
#: because it is *transport*, not a parameter: it is stripped before the tool
#: runs and excluded from the digest, so a tool can never see it and the model
#: cannot change what the approval covers by repeating it.
APPROVAL_ARGUMENT = "__approval_id"

#: Configuration key holding the comma-separated action ids the deployment
#: requires an approval for. Absent or empty means "none declared" — the state
#: the delivered behaviour already has, so shipping this consumer does not
#: silently start gating actions that were never declared.
REQUIRED_ACTIONS_KEY = "approval_required_actions"

#: The action classes this consumer knows how to describe. An id is
#: ``<class>.<name>``; the class decides which seam can dispatch it, and the
#: basis text says so out loud.
ACTION_CLASSES: Dict[str, str] = {
    "tool": "a tool call dispatched by an Agent run (id ``tool:<tool name>``)",
    "scheduler": "a scheduled task action dispatched by the scheduler "
                 "(id ``scheduler:<action type>``)",
    "channel": "a channel-instance execution action (id ``channel:<action>``)",
}

#: Actions whose *non*-applicability is a recorded decision rather than an
#: omission, with the basis. Every entry is a claim a reviewer can check against
#: the code: if the code stops matching the basis, the basis is wrong and the
#: action belongs in the deployment's required list instead.
NOT_APPLICABLE_ACTIONS: Dict[str, str] = {
    "scheduler.agent_task":
        "it runs the member's own Agent in the member's own session: the "
        "outcome is a conversation turn in the member's own history, not an "
        "external side effect on anyone else's system",
    "scheduler.tool_call":
        "the dispatched tool is governed by its own authorization gates "
        "(tool.execute + resource grant + isolation + quota) at the same "
        "dispatch seam, so an approval here would be a second answer to a "
        "question already answered",
    "scheduler.skill_call":
        "assembling and loading a skill is an in-process read of an "
        "already-authorized resource; nothing leaves the process",
    "channel.tenant.connect":
        "a tenant-shared instance is tenant-level configuration whose authority "
        "is the tenant's management qualification; it has no per-member owner "
        "and no single member whose action it is",
}

#: Basis for an action nobody declared anything about. The default is *not*
#: applicable, but it is a policy statement, not silence: the per-tool grant is
#: what governs an ordinary tool call.
UNDECLARED_BASIS = (
    "not declared in the deployment's ``%s``, and the action is not one of the "
    "recorded non-applicable cases: the interactive dispatch of a tool the "
    "caller already holds ``tool.execute`` plus a resource grant for is the "
    "caller's own authorized conversation" % REQUIRED_ACTIONS_KEY
)

#: Stable refusal codes. A caller maps these to its own presentation; the code
#: never changes meaning, and every "not yet" case is distinct from every "no".
CODE_REQUIRED = "approval_required"
CODE_UNKNOWN = "approval_unknown"
CODE_PENDING = "approval_pending"
CODE_DENIED = "approval_denied"
CODE_REVOKED = "approval_revoked"
CODE_EXPIRED = "approval_expired"
CODE_CONSUMED = "approval_consumed"
CODE_MISMATCH = "approval_mismatch"
CODE_UNVERIFIED = "approval_unverified"


class ApprovalDecision(NamedTuple):
    """The gate's answer for one dispatch attempt.

    ``allowed`` is the only thing a caller may branch on for execution;
    ``required`` says whether the policy asked for an approval at all (so a
    caller can log "not applicable" without re-deriving the policy), and
    ``code``/``reason`` explain a refusal. ``parameters`` is the call's
    arguments with the approval reference stripped, so a refused call leaks
    nothing and an allowed one never forwards transport to the tool.
    """

    allowed: bool
    required: bool
    code: str = ""
    reason: str = ""
    basis: str = ""
    parameters: Optional[Dict[str, Any]] = None


def _config(config: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    """The live configuration mapping, or ``{}`` when it cannot be read."""
    if config is not None:
        return config
    try:
        from config import conf
        return conf() or {}
    except Exception:  # noqa: BLE001 - policy must stay importable without config
        return {}


def required_actions(config: Optional[Mapping[str, Any]] = None) -> frozenset:
    """The action ids this deployment requires an approval for.

    Accepts a comma/space separated string or a list, because the value arrives
    from a configuration file that is written by hand. Unreadable configuration
    means *none declared*: the consumer's job is to enforce a declared policy,
    not to invent one, and inventing one would gate work that was never
    declared.
    """
    raw = _config(config).get(REQUIRED_ACTIONS_KEY, ())
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        items = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = list(raw)
    else:
        return frozenset()
    return frozenset(str(item).strip() for item in items if str(item).strip())


def is_required(action_id: str, config: Optional[Mapping[str, Any]] = None) -> bool:
    """Whether the deployment declared this action as needing an approval."""
    return str(action_id or "") in required_actions(config)


def action_basis(action_id: str, config: Optional[Mapping[str, Any]] = None) -> str:
    """Why this action does or does not need an approval (never empty).

    A required action's basis names the declaration; a recorded non-applicable
    action's basis is the recorded one; anything else falls back to
    :data:`UNDECLARED_BASIS`. Returning text for every id is the point — the
    answer to "why did this run without an approval?" has to exist.
    """
    action_id = str(action_id or "")
    if is_required(action_id, config):
        return ("declared in the deployment's ``%s``: the action leaves the "
                "process on someone else's behalf, so one decision by a second "
                "qualified user is required first"
                % REQUIRED_ACTIONS_KEY)
    recorded = NOT_APPLICABLE_ACTIONS.get(action_id)
    if recorded:
        return recorded
    return UNDECLARED_BASIS


def tool_action_id(tool_name: str) -> str:
    """The action id of one tool call (``tool:<name>``)."""
    return "tool:%s" % str(tool_name or "").strip()


def scheduler_action_id(action_type: str) -> str:
    """The action id of one scheduled task action (``scheduler:<type>``)."""
    return "scheduler:%s" % str(action_type or "").strip()


def channel_action_id(action: str) -> str:
    """The action id of one channel-instance execution action."""
    return "channel:%s" % str(action or "").strip()


def canonical_parameters(parameters: Optional[Mapping[str, Any]]) -> str:
    """The parameters exactly as the digest sees them.

    Sorted keys, no insignificant whitespace, no floats (an ``int``/``float``
    difference would otherwise silently change the digest of the same request),
    and **without** :data:`APPROVAL_ARGUMENT`: the reference to an approval is
    transport, and if it were digested then supplying an approval would change
    the request it is supposed to authorise.
    """
    plain = {
        str(key): value for key, value in (parameters or {}).items()
        if str(key) != APPROVAL_ARGUMENT
    }
    return json.dumps(plain, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def request_digest(action_id: str, target: str, parameters: Optional[Mapping[str, Any]]) -> str:
    """The canonical digest one approval is issued for and checked against.

    Computed here, server-side, on both sides — at request time from the
    parameters the requester declared, and at execution time from the parameters
    the executor is actually about to use. They are the same function of the same
    inputs, so "the parameters changed after approval" is not a heuristic: it is
    a different digest, and the approval does not match it.
    """
    payload = "\n".join((
        str(action_id or ""),
        str(target or ""),
        canonical_parameters(parameters),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity_triple(identity: Any = None) -> tuple:
    if identity is None:
        try:
            from common.runtime_identity import current_identity
            identity = current_identity()
        except Exception:  # noqa: BLE001 - no identity is an answer of its own
            return "", "", ""
    return (str(getattr(identity, "user_id", "") or ""),
            str(getattr(identity, "tenant_id", "") or ""),
            str(getattr(identity, "agent_id", "") or ""))


def _refusal(code: str, text: str, required: bool, basis: str,
             parameters: Optional[Dict[str, Any]]) -> ApprovalDecision:
    return ApprovalDecision(False, required, code, text, basis, parameters)


def approval_decision(
    action_id: str,
    *,
    parameters: Optional[Mapping[str, Any]] = None,
    target: str = "",
    identity: Any = None,
    config: Optional[Mapping[str, Any]] = None,
    service: Any = None,
) -> ApprovalDecision:
    """Decide one action's dispatch, consuming its approval when required.

    The order is the requirement's order: identity first (an unresolvable caller
    has nothing to bind an approval to, and is refused rather than read as "no
    user dimension"), then applicability, then the approval itself — status,
    owner, action, target and digest — consumed atomically just before the
    caller performs the side effect.

    Never raises: a broken check must not take a conversation down, and every
    unexpected error fails closed. The caller's own gates (permission, isolation,
    quota) have already run by the time an approval is consumed, which is what
    "re-verify identity, resource and quota before executing" means in practice:
    those checks are ahead of this one, not behind it.
    """
    call_parameters = dict(parameters or {})
    stripped = {k: v for k, v in call_parameters.items()
                if str(k) != APPROVAL_ARGUMENT}
    action_id = str(action_id or "")

    try:
        required = is_required(action_id, config)
        basis = action_basis(action_id, config)
        if not required:
            # Not applicable: an approval reference, if one was supplied anyway,
            # is still stripped rather than forwarded to the action.
            return ApprovalDecision(True, False, "", "", basis, stripped)

        user_id, tenant_id, agent_id = _identity_triple(identity)
        if not user_id or not tenant_id:
            return _refusal(
                CODE_UNVERIFIED,
                "执行授权校验异常，动作已拒绝。\n\nAction refused: the caller "
                "identity could not be resolved, so no approval can be bound to "
                "it.", required, basis, stripped)

        approval_id = str(call_parameters.get(APPROVAL_ARGUMENT) or "").strip()
        if not approval_id:
            return _refusal(
                CODE_REQUIRED,
                "该动作需要审批后才能执行，请先取得批准。\n\nThis action "
                "requires an approved request before it can run: no approval "
                "was supplied.", required, basis, stripped)

        if service is None:
            from auth.service import get_identity_service
            service = get_identity_service()
        try:
            service.consume_action_approval(
                actor_user_id=user_id, tenant_id=tenant_id,
                approval_id=approval_id, action=action_id, agent_id=agent_id,
                target=target,
                digest=request_digest(action_id, target, call_parameters),
            )
        except Exception as error:  # noqa: BLE001 - engine refusal or outage
            code = str(getattr(error, "code", "") or "") or CODE_UNVERIFIED
            return _refusal(
                code,
                "该动作的审批校验未通过（%s），动作已拒绝且没有产生副作用。\n\n"
                "Action refused and no side effect was produced: its approval "
                "did not verify (%s)." % (code, _MESSAGES.get(code, code)),
                required, basis, stripped)
        return ApprovalDecision(True, True, "", "", basis, stripped)
    except Exception as error:  # noqa: BLE001 - fail closed, never raise
        return _refusal(
            CODE_UNVERIFIED,
            "执行授权校验异常，动作已拒绝。\n\nAction refused: the approval "
            "check could not be completed (%s)." % (error,),
            is_required(action_id, config),
            action_basis(action_id, config), stripped)


#: What each refusal code means, for the message the model reads. Kept apart
#: from the codes so the engine's code and the explanation cannot drift.
_MESSAGES: Dict[str, str] = {
    CODE_REQUIRED: "no approval was supplied",
    CODE_UNKNOWN: "no such approval in this tenant",
    CODE_PENDING: "the approval is still pending a decision",
    CODE_DENIED: "the approval was denied",
    CODE_REVOKED: "the approval was revoked before it ran",
    CODE_EXPIRED: "the approval expired",
    CODE_CONSUMED: "the approval was already used for another action; a single "
                   "approval authorises a single action",
    CODE_MISMATCH: "the approval was issued for a different user, action, "
                   "target or parameter digest",
    CODE_UNVERIFIED: "the approval could not be verified",
}
