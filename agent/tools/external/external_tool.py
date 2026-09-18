# encoding:utf-8
"""An external capability, offered to the model as one tool.

This is the *agent-side* end of ``integrations.external.tools``. The declaration
side already decided which capabilities exist and who may see them; this module
turns one :class:`~integrations.external.tools.ToolBinding` into a ``BaseTool``
and routes the call back through :func:`~integrations.external.tools.dispatch`.

Three properties the shape is built around:

1. **The tool holds no authority.** It carries a *binding*, not a credential and
   not a connection record. Every call re-resolves the caller from
   ``current_identity()`` and re-authorizes through ``ConnectionRuntime``, so a
   permission revoked between listing and calling refuses the call (spec: 工具发现
   不等于调用授权). Which is also why ``self_authorized`` is set: the coarse
   ``tool.execute`` grant would be a second, weaker answer to the same question,
   and ``agent``'s dispatch skips that grant for tools that refuse on their own.

2. **Identity comes from the runtime, never from the model.** ``tenant_id`` and
   ``user_id`` are read from the immutable runtime identity. A call with no
   trusted identity fails; there is no parameter that can supply one.

3. **Write actions are the runtime's decision.** This module does not decide
   whether a remote write is allowed — the risk catalogue and the approval
   binding do, one layer down. A refusal arrives as a stable code and is
   rendered for the model as a refusal, never as an empty success.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger

#: Name prefix for every external tool, so an external capability can never
#: shadow a built-in tool (or an MCP one) by choosing its name.
TOOL_PREFIX = "external_"


def _external_tool_name(binding_name: str) -> str:
    """The model-visible name of one external capability.

    The binding name is already ``<kind>.<action>``; the prefix makes the
    *origin* visible in the tool list, which is what lets an operator tell an
    external action apart from a built-in one when reading an audit trail.
    """
    return "%s%s" % (TOOL_PREFIX, str(binding_name or "").strip())


def _identity():
    """The immutable runtime identity, or ``None`` when there is none."""
    try:
        from common.runtime_identity import current_identity
        return current_identity()
    except Exception:  # noqa: BLE001 - an absent identity is an answer of its own
        return None


class ExternalConnectionTool(BaseTool):
    """One external capability, bound to the connection it will run against.

    Parameters are intentionally *not* declared by this class. Each action's
    parameters belong to the adapter that implements it, and inventing a schema
    here would let the advertised shape drift from the executed one — the exact
    failure the declaration/dispatch pair in ``integrations.external.tools``
    exists to prevent. The adapter validates what it receives.
    """

    #: The call authorizes itself through ``ConnectionRuntime`` on every
    #: invocation (permission, readiness, object scope, quota), so the coarse
    #: ``tool.execute`` grant is not also required -- and must not be, because
    #: keeping it would make "has the grant" the answer instead of "is allowed
    #: now". This mirrors the personal-todo and scheduler tools.
    self_authorized = True

    def __init__(self, binding):
        self.binding = binding
        # Kept for the projection; the model sees the prefixed name.
        self.binding_name = binding.tool.name
        self.name = _external_tool_name(binding.tool.name)
        self.description = self._describe(binding)
        self.params = self._schema(binding)
        self._write = bool(binding.tool.write)

    # -- description ------------------------------------------------------ #
    @staticmethod
    def _describe(binding) -> str:
        """The model-facing description: what it does and whether it writes.

        The write warning is in the text on purpose. A model that knows an
        action changes someone else's system asks a better question before
        calling it, and the approval gate refuses it anyway when the deployment
        declared it — two honest signals beat one.
        """
        tool = binding.tool
        parts: List[str] = []
        if tool.description:
            parts.append(str(tool.description))
        target = binding.connection_name or binding.connection_id or tool.kind
        parts.append("目标系统: %s (%s)." % (target, tool.kind))
        if tool.write:
            parts.append("此动作会修改远端系统,需要相应授权与审批。")
        else:
            parts.append("只读动作。")
        return " ".join(parts)

    @staticmethod
    def _schema(binding) -> Dict[str, Any]:
        """A permissive object schema, and honest about why.

        The action's real parameters live in its adapter. Advertising a
        fabricated closed schema would make the model omit required arguments it
        cannot see, so the schema says "an object" and lets the adapter produce
        the authoritative refusal.
        """
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
            "description": (
                "动作参数由目标系统的适配器校验;参数名与取值以 %s 的 "
                "``%s`` 动作为准。" % (binding.tool.kind, binding.tool.action)
            ),
        }

    # -- availability ----------------------------------------------------- #
    def is_available(self) -> bool:
        """Available whenever the binding could be resolved at all.

        The listing already dropped every connection the caller may not use, so
        re-checking here would only add a second, staler answer. Permissions are
        re-derived at call time, where they belong.
        """
        return True

    # -- execution -------------------------------------------------------- #
    def execute(self, params: Dict[str, Any]) -> ToolResult:
        ident = _identity()
        tenant_id = str(getattr(ident, "tenant_id", "") or "")
        user_id = str(getattr(ident, "user_id", "") or "")
        agent_id = str(getattr(ident, "agent_id", "") or "")
        run_id = str(getattr(ident, "run_id", "") or "")

        if not tenant_id or not user_id:
            logger.warning(
                "[ExternalTool] %s refused: no trusted identity", self.name)
            return ToolResult.fail(
                "调用被拒绝:无法解析可信身份。外部系统动作只能在具备可信"
                "身份上下文的会话中执行。\n\n"
                "Refused: the caller identity could not be resolved. An external "
                "action runs only under a trusted identity context."
            )

        # The connection is re-resolved from the binding's kind under *this*
        # actor, so a binding that outlived its connection, its grant or its
        # permission refuses rather than running against whatever is left.
        try:
            from auth.service import get_identity_service
            from integrations.external import tools as external_tools

            service = get_identity_service()
            # No ``approval`` is passed, deliberately. An external write is
            # gated twice — once by the deployment's declared approval actions
            # at ``agent``'s dispatch seam, and once by the risk catalogue
            # inside ``ConnectionRuntime.invoke``, which refuses a
            # write that carries no approval. There is nothing for this layer
            # to add: the approval is consumed (and its transport argument
            # stripped) before the tool is reached, so a value here could only
            # come from the model — which would be the way *around* the gate.
            result = external_tools.dispatch(
                service, self.binding_name, dict(params or {}),
                tenant_id=tenant_id, actor_user_id=user_id,
                agent_id=agent_id, run_id=run_id,
            )
        except Exception as error:  # noqa: BLE001 - a refusal is a result, not a crash
            return self._refusal(error)

        return self._success(result)

    def _success(self, result) -> ToolResult:
        """Render an adapter result, keeping the failure/pending distinction.

        ``outcome_unknown`` is deliberately *not* a success: the remote system
        may or may not have applied the change, and telling the model it worked
        would make it report a state nobody has confirmed. It comes back as an
        error carrying the adapter's own words, so the model asks rather than
        assumes.
        """
        payload = result.as_dict() if hasattr(result, "as_dict") else result
        if isinstance(payload, dict) and not payload.get("ok", True):
            return ToolResult.fail(payload)
        return ToolResult.success(payload)

    def _refusal(self, error: Exception) -> ToolResult:
        """Turn an authorization/policy refusal into words the model can act on.

        The stable code is forwarded verbatim because a client may branch on it,
        and the message is the *service's* message: a refusal this layer
        paraphrased would be a second, drifting explanation of a rule it does
        not own.
        """
        code = str(getattr(error, "code", "") or "")
        message = str(getattr(error, "message", "") or str(error))
        logger.info("[ExternalTool] %s refused: %s %s", self.name, code, message)
        return ToolResult.fail({
            "code": code or "unavailable",
            "message": message,
        })


def external_tools_for(*, tenant_id: Optional[str], actor_user_id: str,
                       agent_id: str = "") -> Dict[str, "ExternalConnectionTool"]:
    """Build one tool per capability this actor may use right now.

    Returns a fresh ``{name: instance}`` mapping and *stores nothing*. That is
    the point: ``ToolManager`` is a process-wide singleton while the set of
    external capabilities is per actor, so publishing them there would let one
    actor's tools sit in another actor's list until the next turn happened to
    overwrite them. The caller binds the result to the object whose lifetime it
    actually matches — the Agent session's own tool collection.

    A provider that raises is already skipped one layer down
    (``integrations.external.tools.available_tools``), so one unreachable type
    cannot empty the list.

    ``agent_id`` is the runtime Agent the turn runs under. It is what makes the
    tenant-admin exemption reachable at listing time: without it that branch of
    :func:`integrations.external.authorization.may_execute` is closed (it
    requires an Agent bound to the tenant), so a tenant admin who manages the
    connections but holds no personal grant would not even see them.
    """
    from integrations.external import tools as external_tools

    out: Dict[str, ExternalConnectionTool] = {}
    for binding in external_tools.authorized_tools(
            tenant_id=tenant_id, actor_user_id=actor_user_id,
            agent_id=agent_id):
        try:
            tool = ExternalConnectionTool(binding)
        except Exception as error:  # noqa: BLE001 - one bad declaration, not all
            logger.warning("[ExternalTool] %r could not be built: %s",
                           getattr(binding, "tool", None), error)
            continue
        out[tool.name] = tool
    return out


def reconcile_external_tools(existing_names, wanted, *,
                             allowed: Optional[Any] = None) -> tuple:
    """What this actor's external tool set should be, and the delta (pure).

    ``existing_names`` is the set of external tool names already bound to the
    caller's object; ``wanted`` is what the actor may use right now (from
    :func:`external_tools_for`). ``allowed`` is the caller's own allow/deny
    filter (or ``None`` for "no filter"), applied *here* rather than by the
    caller so there is one decision instead of two that can disagree.

    The delta is a set difference, so it describes what actually changes: a
    capability that is denied but was never present is not reported as a
    removal, and a re-listed one is not reported as an addition.
    """
    wanted = dict(wanted or {})
    if allowed is not None:
        wanted = {name: tool for name, tool in wanted.items()
                  if name in allowed}
    existing = {str(name) for name in (existing_names or ())}
    return (wanted,
            sorted(set(wanted) - existing),
            sorted(existing - set(wanted)))
