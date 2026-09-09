# encoding:utf-8
"""Fixed permission catalog, built-in roles and authorization primitives.

This is the code-level policy table referenced by the RBAC spec. It is *not* a
configurable readiness service: the directory is fixed in code, membership is
the only way to acquire permissions, and grant/qualification decisions are made
here so that the same rules gate the four admin views and the API routes.

The catalog is intentionally read-only for non-admin roles. Identity *write*
privileges are gated by the built-in ``tenant_admin`` qualification, not by a
composable permission point, so a custom role can never be crafted into an
administrator.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

#: The fixed permission directory, in catalog order. Only these strings may be
#: assigned to a role (or granted by a built-in). Write privileges are *not*
#: enumerated here — they are conferred by the ``tenant_admin`` qualification.
#:
#: The catalogue is intentionally finite. The original nine business ids are
#: retained verbatim; the resource-authorization milestone adds the thirteen
#: explicit resource actions (skill/tool/model/agent/chat) so that custom roles
#: can grant access to specific skills, tools, models, agents and the chat
#: consumer without relying on ``agent.read`` as a blanket write privilege.
PERMISSION_CATALOG: Tuple[str, ...] = (
    # -- original nine (unchanged) ---------------------------------------
    "tenant.info.read",  # view own tenant basics
    "tenant.members.read",  # list/read current-tenant members
    "tenant.org.read",  # read department tree + member org
    "agent.read",  # secure agent overview
    "history.read",  # read own/shared session history
    "knowledge.read",  # read knowledge content
    "memory.read",  # read personal memory
    "todo.read",  # read own personal todos
    "todo.write",  # create/update own personal todos
    # -- resource-authorization additions (task 1.4) ---------------------
    "skill.read",  # read skill directory / content
    "skill.use",  # assemble/load a skill at runtime
    "skill.edit",  # write content back to a skill
    "skill.enable",  # toggle a skill's enabled state
    "tool.read",  # read tool directory / schema
    "tool.execute",  # actually run a tool
    "tool.configure",  # manage tool configuration
    "model.read",  # read allowed model metadata
    "model.use",  # select/use a model at runtime
    "agent.use",  # launch/restore a chat with an agent
    "agent.edit",  # edit agent configuration
    "agent.enable",  # enable/disable an agent
    "chat.use",  # use the chat consumer with a chosen model
)

#: Stable metadata for the nine permission ids. ``group`` / ``label`` /
#: ``description`` drive the admin UI display; ``scope`` records whether the
#: permission applies to the current tenant or to the requesting user's own
#: personal data; ``assignable`` records whether a custom role may select it.
PERMISSION_METADATA: Dict[str, Dict[str, object]] = {
    "tenant.info.read": {
        "group": "租户", "label": "查看租户信息",
        "description": "查看当前租户的基本信息",
        "scope": "tenant", "assignable": True,
    },
    "tenant.members.read": {
        "group": "租户", "label": "查看成员",
        "description": "查看当前租户的成员列表",
        "scope": "tenant", "assignable": True,
    },
    "tenant.org.read": {
        "group": "租户", "label": "查看组织",
        "description": "查看当前租户的部门组织树",
        "scope": "tenant", "assignable": True,
    },
    "agent.read": {
        "group": "资产", "label": "查看智能体",
        "description": "查看已绑定智能体的概览",
        "scope": "tenant", "assignable": True,
    },
    "history.read": {
        "group": "会话", "label": "查看历史会话",
        "description": "读取本人或合法共享的会话历史",
        "scope": "personal", "assignable": True,
    },
    "knowledge.read": {
        "group": "知识", "label": "查看知识",
        "description": "读取知识库内容",
        "scope": "tenant", "assignable": True,
    },
    "memory.read": {
        "group": "记忆", "label": "查看记忆",
        "description": "读取个人记忆内容",
        "scope": "personal", "assignable": True,
    },
    "todo.read": {
        "group": "待办", "label": "查看待办",
        "description": "读取本人个人待办",
        "scope": "personal", "assignable": True,
    },
    "todo.write": {
        "group": "待办", "label": "管理待办",
        "description": "创建/更新本人个人待办",
        "scope": "personal", "assignable": True,
    },
    "skill.read": {
        "group": "技能", "label": "查看技能",
        "description": "读取技能目录与正文",
        "scope": "tenant", "assignable": True,
    },
    "skill.use": {
        "group": "技能", "label": "使用技能",
        "description": "装配并加载已授权技能",
        "scope": "tenant", "assignable": True,
    },
    "skill.edit": {
        "group": "技能", "label": "编辑技能",
        "description": "写回技能正文内容",
        "scope": "tenant", "assignable": True,
    },
    "skill.enable": {
        "group": "技能", "label": "启停技能",
        "description": "切换技能启用状态",
        "scope": "tenant", "assignable": True,
    },
    "tool.read": {
        "group": "工具", "label": "查看工具",
        "description": "读取工具目录与描述",
        "scope": "tenant", "assignable": True,
    },
    "tool.execute": {
        "group": "工具", "label": "执行工具",
        "description": "实际运行获准工具",
        "scope": "tenant", "assignable": True,
    },
    "tool.configure": {
        "group": "工具", "label": "配置工具",
        "description": "管理工具配置",
        "scope": "tenant", "assignable": True,
    },
    "model.read": {
        "group": "模型", "label": "查看模型",
        "description": "读取获准模型元数据",
        "scope": "tenant", "assignable": True,
    },
    "model.use": {
        "group": "模型", "label": "使用模型",
        "description": "选择并使用获准模型",
        "scope": "tenant", "assignable": True,
    },
    "agent.use": {
        "group": "智能体", "label": "使用智能体",
        "description": "启动/恢复智能体会话",
        "scope": "tenant", "assignable": True,
    },
    "agent.edit": {
        "group": "智能体", "label": "编辑智能体",
        "description": "编辑智能体配置",
        "scope": "tenant", "assignable": True,
    },
    "agent.enable": {
        "group": "智能体", "label": "启停智能体",
        "description": "启用/停用智能体",
        "scope": "tenant", "assignable": True,
    },
    "chat.use": {
        "group": "对话", "label": "使用对话",
        "description": "在对话中使用获准模型",
        "scope": "tenant", "assignable": True,
    },
}

#: Business-read permissions a ``member`` (and thus a custom role on top of
#: member) gets by default. Namespace/member/org listing requires explicit grant.
MEMBER_DEFAULT_PERMISSIONS: Tuple[str, ...] = (
    "tenant.info.read",
    "agent.read",
    "history.read",
    "knowledge.read",
    "memory.read",
    "todo.read",
    "todo.write",
)

#: Explicit default set for the built-in ``tenant_admin``. This is a *fixed*
#: list of the nine catalogue ids — NOT the whole catalogue, so that adding a
#: future permission to the catalogue never auto-grants it to an existing admin.
TENANT_ADMIN_DEFAULT_PERMISSIONS: Tuple[str, ...] = (
    "tenant.info.read",
    "tenant.members.read",
    "tenant.org.read",
    "agent.read",
    "history.read",
    "knowledge.read",
    "memory.read",
    "todo.read",
    "todo.write",
)

#: Built-in role codes. These cannot be modified or deleted.
TENANT_ADMIN_CODE = "tenant_admin"
MEMBER_CODE = "member"

#: Built-in role display definitions, keyed by code.
BUILTIN_ROLES: Dict[str, str] = {
    TENANT_ADMIN_CODE: "租户管理员",
    MEMBER_CODE: "成员",
}


class PermissionError(ValueError):
    """Raised when a requested/assigned permission is outside the catalog."""


def normalize_permissions(permissions: Iterable[str]) -> Set[str]:
    """Validate a list of permissions and return the deduplicated set.

    Rejects anything outside the fixed catalog, including platform/admin qualifiers,
    wildcards and data-range suffixes.
    """
    result: Set[str] = set()
    for raw in permissions:
        p = str(raw).strip()
        if p not in PERMISSION_CATALOG:
            raise PermissionError(f"unknown permission: {p!r}")
        result.add(p)
    return result


def default_permissions_for(role_code: str) -> Set[str]:
    """Return the default permissions a role code receives out of the box.

    Uses the explicit default sets, not the live catalogue, so that future
    catalogue expansions do not silently widen an already-provisioned role.
    """
    if role_code == TENANT_ADMIN_CODE:
        return set(TENANT_ADMIN_DEFAULT_PERMISSIONS)
    return set(MEMBER_DEFAULT_PERMISSIONS)


def _admin_permissions() -> Set[str]:
    # Admin qualification is no longer expressed as "the whole catalogue". The
    # built-in tenant_admin role carries the explicit nine-id default set. This
    # helper is retained for backwards-compatible callers that need the admin
    # default when constructing a fresh role's stored permissions.
    return set(TENANT_ADMIN_DEFAULT_PERMISSIONS)


def permissions_for_roles(
    role_codes: Iterable[str], role_permissions: Dict[str, Set[str]]
) -> Set[str]:
    """Union the permissions of the given role codes.

    ``role_permissions`` maps a role code to its explicitly stored permissions
    for custom roles. Built-ins use their explicit default set; tenant_admin is
    treated as holding its nine-id default set. The union is the effective
    permission set for a membership. Admin *qualification* stays independent of
    this union (it is decided by ``is_admin_role`` / the tenant_admin membership
    role), so a custom role can never be crafted into an administrator here.
    """
    result: Set[str] = set()
    for code in role_codes:
        if code == TENANT_ADMIN_CODE:
            result |= default_permissions_for(TENANT_ADMIN_CODE)
            continue
        if code == MEMBER_CODE:
            result |= default_permissions_for(MEMBER_CODE)
            continue
        result |= set(role_permissions.get(code, set()))
    return result


def is_admin_role(role_code: str) -> bool:
    """True when the role code is the built-in tenant-admin qualification."""
    return role_code == TENANT_ADMIN_CODE


def permission_catalog_with_metadata() -> List[Dict[str, object]]:
    """Return the catalogue as an ordered list of id + metadata dicts.

    Used by the admin UI to render groups/labels and by the permission endpoint.
    Comes from ``PERMISSION_CATALOG`` order so the display order is stable.
    """
    return [
        {"id": pid, **PERMISSION_METADATA[pid]}
        for pid in PERMISSION_CATALOG
    ]


#: The five resource kinds a role may be granted against.
RESOURCE_KINDS: Tuple[str, ...] = (
    "menu", "skill", "tool", "model", "agent",
)

#: Resource-kind -> the set of *enabled actions* that may be granted. ``configure``
#: and ``edit``/``enable`` are maintenance actions; they never imply ``execute``/
#: ``use``. A resource may be granted multiple actions (each is independent).
RESOURCE_ACTIONS: Dict[str, Tuple[str, ...]] = {
    "menu": ("view",),
    "skill": ("read", "use", "edit", "enable"),
    "tool": ("read", "execute", "configure"),
    "model": ("read", "use"),
    "agent": ("read", "use", "edit", "enable"),
}

#: A stable resource_id namespace marks the origin/source of a resource so a
#: rename never loses an authorization and two same-name resources from different
#: sources never collide.
RESOURCE_NAMESPACES: Dict[str, str] = {
    "menu": "nav",        # navigation-registered pages/tabs
    "skill": "builtin",   # builtin vs custom are distinct namespaces
    "tool": "builtin",    # builtin vs mcp:<connection-id> are distinct
    "model": "provider",  # provider:<config-id>
    "agent": "agent",     # agent:<agent-id>
}

#: Namespaced skill origins. A same-named skill in ``custom`` shadows ``builtin``
#: for display, but remains a distinct authorization object.
SKILL_SOURCE_NAMESPACES: Tuple[str, ...] = ("builtin", "custom")


def normalize_resource_grants(grants: Iterable[Dict[str, object]]) -> List[Dict[str, str]]:
    """Validate and canonicalize a list of grant dicts.

    Each grant is ``{resource_kind, resource_id, action}``. Rejects unknown kinds,
    unknown actions for the kind, empty ids and duplicated (kind,id,action). Raises
    :class:`PermissionError` on invalid input. The returned list is sorted for
    deterministic storage.
    """
    seen: Set[Tuple[str, str, str]] = set()
    out: List[Dict[str, str]] = []
    for g in grants:
        if not isinstance(g, dict):
            raise PermissionError("grant must be an object")
        kind = str(g.get("resource_kind", "") or "").strip()
        rid = str(g.get("resource_id", "") or "").strip()
        action = str(g.get("action", "") or "").strip()
        if kind not in RESOURCE_ACTIONS:
            raise PermissionError(f"unknown resource kind: {kind!r}")
        if action not in RESOURCE_ACTIONS[kind]:
            raise PermissionError(
                f"unknown action {action!r} for resource kind {kind!r}")
        if not rid:
            raise PermissionError(f"empty resource_id for kind {kind!r}")
        if not (kind, rid, action) in seen:
            seen.add((kind, rid, action))
            out.append({"resource_kind": kind, "resource_id": rid, "action": action})
    return sorted(out, key=lambda x: (x["resource_kind"], x["resource_id"], x["action"]))


def validate_model_defaults(defaults: Optional[Mapping[str, str]]) -> Dict[str, str]:
    """Validate an optional ``{capability: model_resource_id}`` default map.

    Rejects unknown capabilities and empty model ids. Returns a normalized dict
    (or ``{}`` when ``defaults`` is falsy). Applies the same finite capability
    set used by the model policy: ``chat``, ``chat_fallback``, ``vision``,
    ``asr``, ``tts``, ``embedding``, ``image``, ``search``.
    """
    KNOWN_CAPABILITIES = {
        "chat", "chat_fallback", "vision", "asr", "tts",
        "embedding", "image", "search",
    }
    if not defaults:
        return {}
    out: Dict[str, str] = {}
    for cap, model in defaults.items():
        cap = str(cap).strip()
        if cap not in KNOWN_CAPABILITIES:
            raise PermissionError(f"unknown model capability: {cap!r}")
        model = str(model or "").strip()
        if not model:
            raise PermissionError(f"empty model resource_id for capability {cap!r}")
        out[cap] = model
    return {k: out[k] for k in sorted(out)}


def resource_granted(grants: Iterable[Dict[str, str]], kind: str, rid: str, action: str) -> bool:
    """True when a normalized grant list contains the (kind, resource_id, action)."""
    return any(
        g["resource_kind"] == kind and g["resource_id"] == rid and g["action"] == action
        for g in grants
    )


def resource_ids_for(grants: Iterable[Dict[str, str]], kind: str, action: str) -> Set[str]:
    """Return the set of resource ids granted for a given kind+action."""
    return {
        g["resource_id"] for g in grants
        if g["resource_kind"] == kind and g["action"] == action
    }
