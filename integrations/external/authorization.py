# encoding:utf-8
"""Who may execute an external capability, and where it may be reached from.

Why this module exists
----------------------
The control plane declared capabilities long before anything checked them. A
tool was *listed* per tenant and *dispatched* per connection, and the only gates
in between were the deployment's open classes, the risk catalogue and the
approval binding — none of which is a statement about the *caller*. So an
``external:<kind>:<kind>.<action>`` id could be granted in the catalogue and the
grant was never read: the console could show a role as holding an external
capability while every call succeeded without it.

That is the gap this module closes, and the spec states it directly
(``mcp-connection-integration``: 工具发现与执行保留现有授权链):

    工具可见与实际调用 SHALL 遵循既有资源执行授权、绑定智能体范围、配额和风险
    策略。连接管理权限、测试成功或工具名称的 mcp 前缀 MUST NOT 作为执行授权。

Three of those four live here or next to here:

``资源执行授权`` — :func:`may_execute`, the same decision for the listing and for
the call. It reuses the identity layer's existing resource-execution gate
(``check_resource_action(..., "tool", rid, "execute", permission="tool.execute")``)
rather than inventing a second one, so an external capability behaves like every
other tool instead of like a special case.

``绑定智能体范围`` — :func:`agent_in_scope`. A capability reached *through* an Agent
must be reached through an Agent bound to the tenant that owns the connection.
Without this the tenant-admin exemption in :func:`may_execute` would accept any
``external:`` id a tenant admin could spell, which is the failure the archived
``tighten-tenant-admin-mcp-exemption`` change fixed for ``mcp:`` ids.

``配置/管理权限不是执行授权`` — by construction. ``external.connections.manage``, a
passing connection test, and the ``mcp.`` name prefix appear nowhere in this
file. A tenant admin who may fully configure a connection still needs the
execution grant for it, which is what the spec sentence above requires.

What is deliberately *not* here: readiness (the deployment's open classes), the
risk catalogue and the approval binding. Those are properties of the action and
the deployment, not of the caller, and they are already enforced in
``ConnectionRuntime.invoke``. Folding them in would make one function answer two
questions and make both harder to test.
"""

from __future__ import annotations

from typing import Any, Optional

#: The stable refusal code an unauthorized external call comes back with. Named
#: rather than inline so the adapter-facing result, the agent-side rendering and
#: the tests all branch on one string.
NOT_AUTHORIZED = "tool_not_authorized"

#: The functional permission the resource-execution gate is paired with. The
#: same one built-in tools use (``auth.policy``): holding the resource grant
#: without it is not enough, which is what keeps a grant from being a capability
#: in its own right.
EXECUTE_PERMISSION = "tool.execute"


def resource_id_for(kind: str, action: str) -> str:
    """The grantable id of one external capability.

    ``external:<kind>:<kind>.<action>`` — the shape
    ``integrations.external.tools.declared_tool_projection`` publishes and an
    administrator grants. Deliberately keyed on the ``(kind, action)`` pair and
    **not** on a connection id: a grant says "this member may use this kind of
    capability", and it must stay valid when the specific connection is
    replaced, re-created or inherited from a platform template. Keying it on a
    connection would make every migration a re-grant, and the spec forbids the
    migration from bulk-granting (不能批量放权).

    This function is the single definition. The projection and the gate both
    call it, so a catalogue entry can never name a resource the runtime does not
    check — which is the drift that made the original gap invisible.
    """
    kind = str(kind or "").strip()
    action = str(action or "").strip()
    return "external:%s:%s.%s" % (kind, kind, action)


def agent_in_scope(identity: Any, *, tenant_id: Optional[str],
                   agent_id: Optional[str]) -> bool:
    """Whether ``agent_id`` is an Agent bound to ``tenant_id``.

    The binding row is the same isolation boundary the scheduler's
    ``revalidate_owner`` and the console's ``_tenant_admin_owns_agent`` rely on,
    so a connection reached through another tenant's Agent is refused by all
    three for the same reason rather than by three similar-looking checks.

    An unbound or unknown Agent is refused, not assumed: ``get_agent_binding``
    returning nothing means nothing proved tenancy, and "no proof" must not be
    "allowed" for the branch that exists to skip a grant.
    """
    tenant = str(tenant_id or "").strip()
    agent = str(agent_id or "").strip()
    if not tenant or not agent:
        return False
    try:
        binding = identity.get_agent_binding(agent)
    except Exception:  # noqa: BLE001 - an unreadable binding is not a proof
        return False
    if not binding:
        return False
    return str(binding.get("tenant_id") or "") == tenant


def may_execute(identity: Any, *, actor_user_id: str,
                tenant_id: Optional[str], kind: str, action: str,
                scope: str = "", agent_id: str = "") -> bool:
    """Whether this actor may run this capability right now.

    Three ways in, and only three:

    0. **A personal connection needs no grant.** It is its owner's own
       resource; ownership is the authorization, and the caller checks it
       (``owner_user_id == actor_user_id``) before this function is consulted.
       The personal-email spec says exactly that: 动作 SHALL 在派发前重校本人、
       租户、智能体范围、连接和秘密版本 — 本人, not a role grant. Requiring one
       would mean a member could connect their own mailbox and then be unable to
       read it until an administrator granted them a capability they already
       hold by owning it.

    1. **The ordinary resource-execution gate**, for a tenant or platform
       connection. The functional ``tool.execute`` permission *and* an explicit
       ``external:...`` grant. A platform admin passes here by
       ``authorization_mode == "all"``, exactly as it does for every other tool
       — the platform's all is not re-implemented here.

    2. **The tenant-admin exemption**, for the tenant that manages the
       capability. Narrow on purpose: the tenant must be able to allocate the id
       at all (``tenant_admin_may_execute_tool`` checks it against the tenant's
       own grant set, so a capability the platform never opened stays closed),
       *and* the call must arrive through an Agent bound to that tenant
       (:func:`agent_in_scope`). The second half is what stops the exemption
       from being a way to reach another tenant's connection by naming its id.

    Everything is read per call and nothing is cached, so a revoked role, a
    tightened tenant limit or a re-bound Agent denies the very next invocation.
    Failures are refusals rather than exceptions: a store that cannot answer has
    not said "allowed", and this is the function that decides whether to skip a
    grant.
    """
    from integrations.external import registry

    if not actor_user_id or not tenant_id:
        return False
    if str(scope or "") == registry.SCOPE_PERSONAL:
        # Ownership was already established by the caller. A second gate here
        # would be a grant check on the member's own mailbox.
        return True
    rid = resource_id_for(kind, action)
    try:
        if identity.check_resource_action(
                actor_user_id, tenant_id, "tool", rid, "execute",
                permission=EXECUTE_PERMISSION):
            return True
    except Exception:  # noqa: BLE001 - unreadable => not granted
        return False

    try:
        if not agent_in_scope(identity, tenant_id=tenant_id, agent_id=agent_id):
            return False
        return bool(identity.tenant_admin_may_execute_tool(
            actor_user_id, tenant_id, rid, agent_id))
    except Exception:  # noqa: BLE001
        return False


def describe_refusal(kind: str, action: str) -> str:
    """The message a refused caller gets.

    It names the capability and the permission to ask for, and it says nothing
    about which connections exist — a refusal that listed them would turn the
    gate into a discovery oracle for a caller who holds no grant.
    """
    return ("%s is not authorized for this caller; %s on %s is required"
            % (resource_id_for(kind, action), EXECUTE_PERMISSION,
               resource_id_for(kind, action)))
