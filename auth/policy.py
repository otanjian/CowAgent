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

from typing import Dict, Iterable, List, Optional, Set, Tuple

#: The fixed permission directory, in catalog order. Only these strings may be
#: assigned to a role (or granted by a built-in). Write privileges are *not*
#: enumerated here — they are conferred by the ``tenant_admin`` qualification.
PERMISSION_CATALOG: Tuple[str, ...] = (
    "tenant.info.read",  # view own tenant basics
    "tenant.members.read",  # list/read current-tenant members
    "tenant.org.read",  # read department tree + member org
    "agent.read",  # secure agent overview
    "history.read",  # read own/shared session history
    "knowledge.read",  # read knowledge content
    "memory.read",  # read personal memory
    "todo.read",  # read own personal todos
    "todo.write",  # create/update own personal todos
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
