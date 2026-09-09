# encoding:utf-8
"""Domain service for RongAI multi-tenant identity & access management.

This orchestrates the identity store, password hashing, session lifecycle, the
fixed permission catalog and the append-only audit. It is the single entry point
for the four admin views and for the resource-isolation/authorization layer.

Key invariants enforced here (from the specs):

* Bootstrap creates a default tenant, a platform admin, real roles and the org
  root, all validated, with no common default passwords.
* Every mutable write is validated and, together with its audit event, committed
  in one ``identity.db`` transaction (``_tx``).
* Admin continuity: the instance always keeps a valid platform admin and every
  active tenant keeps a valid active ``tenant_admin`` membership.
* ``expected_version`` guards concurrent edits (409 at the API layer).
* Cross-tenant object access is rejected (404 at the API layer).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from auth.store import ConnGuard, IdentityStore
from auth.password import (
    hash_password,
    verify_password,
    generate_password,
    MIN_PASSWORD_LENGTH,
)

from auth.session import SessionStore, generate_token, hash_token, session_ttl_seconds
from auth.audit import AuditStore, sanitize_payload, denied_event
from auth.policy import (
    BUILTIN_ROLES,
    TENANT_ADMIN_CODE,
    MEMBER_CODE,
    PERMISSION_CATALOG,
    normalize_permissions,
    RESOURCE_KINDS,
    RESOURCE_ACTIONS,
    normalize_resource_grants,
    validate_model_defaults,
    resource_granted,
    resource_ids_for,
)


#: Which console pages/tabs this change actually signs for availability. Keys
#: are page/tab capability ids used by the front-end navigation availability
#: projection; values are the read permission that drives ``read_allowed`` and
#: the display scope. This is intentionally finite: pages whose consumer is not
#: yet adapted/accepted are signed here but reported unavailable until the
#: consumer opens. The list mirrors the console-information-architecture commit.
_SIGNED_CONSOLE_PAGES: Dict[str, Dict[str, object]] = {
    "workbench.chat": {"permission": "", "scope": "self", "label": "AI 对话"},
    "workbench.history": {"permission": "history.read", "scope": "self", "label": "历史对话"},
    "workbench.agents": {"permission": "agent.read", "scope": "tenant", "label": "智能体工作台"},
    "workbench.todos": {"permission": "todo.read", "scope": "self", "label": "我的待办"},
    "workbench.schedules": {"permission": "", "scope": "self", "label": "定时任务"},
    "workbench.knowledge": {"permission": "knowledge.read", "scope": "agent", "label": "知识库"},
    "workbench.scenes": {"permission": "", "scope": "tenant", "label": "场景应用"},
    "admin.agents": {"permission": "agent.read", "scope": "agent", "label": "智能体管理"},
    "admin.skills": {"permission": "", "scope": "agent", "label": "工具与技能"},
    "admin.memory": {"permission": "memory.read", "scope": "agent", "label": "记忆管理"},
    "admin.models": {"permission": "", "scope": "platform", "label": "模型与接入"},
    "admin.channels": {"permission": "", "scope": "platform", "label": "消息渠道"},
    "admin.logs": {"permission": "", "scope": "platform", "label": "运行日志"},
    "admin.members": {"permission": "tenant.members.read", "scope": "tenant", "label": "成员管理"},
    "admin.roles": {"permission": "tenant.members.read", "scope": "tenant", "label": "角色权限"},
    "admin.organization": {"permission": "tenant.org.read", "scope": "tenant", "label": "组织架构"},
    "admin.tenants": {"permission": "", "scope": "platform", "label": "租户管理"},
    "admin.branding": {"permission": "", "scope": "platform", "label": "品牌设置"},
    "admin.settings": {"permission": "", "scope": "platform", "label": "系统设置"},
}


#: Resource-kind + action -> the functional permission that must also be held.
#: The resource grant (stored in role_resource_grants) is the per-resource
#: gate; this mapping gives the kind-level functional permission the member must
#: also possess. A platform admin (all) skips both.
_RESOURCE_KIND_PERMISSION: Dict[str, Dict[str, str]] = {
    "menu": {"view": ""},
    "skill": {"read": "skill.read", "use": "skill.use", "edit": "skill.edit", "enable": "skill.enable"},
    "tool": {"read": "tool.read", "execute": "tool.execute", "configure": "tool.configure"},
    "model": {"read": "model.read", "use": "model.use"},
    "agent": {"read": "agent.read", "use": "agent.use", "edit": "agent.edit", "enable": "agent.enable"},
}


def _resource_permission(kind: str, action: str) -> str:
    """Return the functional permission required for a kind+action (or '')."""
    return _RESOURCE_KIND_PERMISSION.get(kind, {}).get(action, "")


class IdentityServiceError(RuntimeError):
    """Base exception for service-level failures (maps to 400/404/409/503)."""

    def __init__(self, message: str, code: str = "bad_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


#: Common default passwords rejected by bootstrap and password reset.
_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "admin", "admin123",
    "letmein", "welcome", "password1", "123456789", "1234567890",
}

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_TENANT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_ROLE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: External identity provider names (feishu/dingtalk/wecom/...). Lowercased at
#: bind time so inbound resolution can normalize identically.
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Default lifetime (seconds) of an initial/temporary password. A real bootstrap
#: (allow_weak=False) and every issued temp password carry a finite expiry so a
#: forced-password-change account is never left without a deadline.
TEMPORARY_PASSWORD_TTL_SECONDS = 86400  # 24 hours


def _now() -> int:
    return int(time.time())


#: The deployment-controlled base in which a tenant's shared root is generated
#: (task 4.4 / design §4). New tenants created from the web form must NOT accept
#: a client-supplied ``shared_root``; the service derives it here and fails
#: clearly if no controlled root is available. The CLI ``bootstrap``/``register``
#: still pass an explicit root for legitimate in-place registration.
def _deployment_shared_base(svc=None) -> Optional[str]:
    """Return the controlled base for new tenant shared roots, or None.

    Resolution order:
      1. The explicitly configured base (config ``tenant_shared_base`` or env
         ``COW_TENANT_BASE``) -- authoritative when set.
      2. The verified engineering/workspace root (the default Agent's workspace),
         but ONLY when that root is not itself an existing tenant's shared root.

    In database identity mode the default tenant registers the engineering
    workspace as its shared root, so candidate 2 is normally refused and an
    operator-configured base is required. A candidate that equals/contains/is
    contained by an existing tenant root can never produce a resolvable tenant
    (``common/state_dir`` containment) and raises ``config_error``. Never falls
    back to the user's home or the data/config tree -- tenant data is forbidden
    there.
    """
    base = None
    try:
        from config import get_tenant_shared_base
        base = get_tenant_shared_base()
    except Exception:
        pass
    if not base:
        try:
            from agent.registry import get_agent_registry
            root = get_agent_registry().get(require_enabled=False).workspace
            if root:
                base = os.path.realpath(str(root))
        except Exception:
            base = None
    if not base:
        return None
    if svc is not None:
        for other in svc.tenant_shared_roots():
            other_root = os.path.realpath(other["shared_root"])
            try:
                common = os.path.commonpath([base, other_root])
            except ValueError:  # different drives (Windows): never ancestors
                continue
            # Equal, or one contains the other -> deriving under it would
            # produce a tenant root that can never resolve.
            if common == base or common == other_root:
                raise IdentityServiceError(
                    "tenant shared root base %r overlaps tenant %r root %r; "
                    "configure 'tenant_shared_base' (or env COW_TENANT_BASE) "
                    "to a directory outside every existing tenant root"
                    % (base, other["id"], other_root),
                    code="config_error",
                    status=503,
                )
    return base


def _derive_tenant_shared_root(code: str, svc=None) -> str:
    """Generate a tenant shared root under the deployment-controlled base.

    Deriving a NEW tenant's root under an EXISTING tenant's root would trip the
    read-time cross-tenant containment guard for both tenants, so the controlled
    base must be outside every tenant root. Raises a 503 ``config_error`` when
    no usable base is available so a tenant is never created with an unusable/
    overlapping root (design §4).
    """
    base = _deployment_shared_base(svc)
    if not base:
        raise IdentityServiceError(
            "no configured tenant data base; the engineering/workspace root is "
            "already the default tenant's shared root, so configure "
            "'tenant_shared_base' (or env COW_TENANT_BASE) to a directory "
            "outside the workspace",
            code="config_error",
            status=503,
        )
    root = os.path.join(base, "tenants", code)
    # Ensure the derived root does not escape the controlled base via a
    # pre-existing symlink; refuse rather than silently re-rooting.
    real = os.path.realpath(root)
    if os.path.commonpath([real, base]) != base:
        raise IdentityServiceError(
            "tenant shared root escapes the deployment root",
            code="config_error",
            status=503,
        )
    return root


def _assert_new_tenant_root_clear(shared_root: str, svc) -> None:
    """Refuse a new tenant shared root that overlaps an existing tenant's root.

    Mirrors the read-time containment guard in ``common/state_dir`` so a root
    that can never resolve is rejected before any row is written -- otherwise a
    console-created tenant silently poisons every later ``shared_root()`` for
    both itself and the tenant it nests under (3.9 / design §4).
    """
    from common.state_dir import StateDirError, validate_tenant_shared_root
    try:
        validate_tenant_shared_root(shared_root, svc=svc)
    except StateDirError as e:
        raise IdentityServiceError(
            str(e), code="shared_root_conflict", status=409) from e


@dataclass
class LoginResult:
    user_id: str
    username: str
    display_name: str
    token: str
    must_change_password: bool
    restricted: bool
    tenants: List[Dict[str, Any]]
    is_platform_admin: bool


class IdentityService:
    """High-level identity operations. Thread-safe for independent calls."""

    def __init__(self, db_path: str):
        self._store = IdentityStore(db_path)
        self._sessions = SessionStore(db_path)
        self._audit = AuditStore(db_path)

    # --- internals ---------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(12)}"

    def _tx(self):
        """Open a transactional connection with an immediate write lock.

        ``BEGIN IMMEDIATE`` acquires the SQLite write lock up front, so the
        read-then-write sequences inside a transaction cannot interleave with
        another writer between the two statements. The caller commits/rolls back.
        """
        con = self._store.connect()
        con.execute("BEGIN IMMEDIATE")
        # ``con`` is a ConnGuard (a context manager that BOTH commits/rolls back
        # AND closes the underlying connection on exit). Returning a raw
        # sqlite3.Connection here would leak a handle per call, eventually
        # wedging the identity store under load.
        return con

    def _audit_in_tx(self, con, **kwargs) -> None:
        self._audit.record(con=con, **kwargs)

    # --- bootstrap (task 2.2) ---------------------------------------------

    def bootstrap(
        self,
        *,
        tenant_code: str,
        tenant_name: str,
        admin_username: str,
        admin_display: str,
        admin_password: str,
        shared_root: str,
        allow_weak: bool = False,
    ) -> Dict[str, Any]:
        """Create the default tenant + initial platform admin + built-in roles.

        Returns the created tenant dict. By default rejects common default
        passwords and invalid names/roots (only ``allow_weak=True`` permits a
        well-known password such as ``admin`` for local testing). Idempotent per
        tenant code (returns existing when present) so a re-run after partial
        failure does not duplicate.
        """
        if not allow_weak and (admin_password.lower() in _COMMON_PASSWORDS or not admin_password.strip()):
            raise IdentityServiceError(
                "management bootstrap requires a strong, non-default password",
                code="weak_password",
                status=400,
            )
        if not allow_weak and len(admin_password) < MIN_PASSWORD_LENGTH:
            raise IdentityServiceError("admin password is too short", code="weak_password")
        # allow_weak lets a short, well-known test password through (e.g. admin).
        _pw_min_length = 1 if allow_weak else MIN_PASSWORD_LENGTH

        tenant_code = tenant_code.strip().lower()
        if not _TENANT_CODE_RE.fullmatch(tenant_code):
            raise IdentityServiceError("invalid tenant code", code="invalid_code")

        # Idempotence: if the tenant already exists, return it (do not duplicate).
        existing = self._find_tenant_by_code(tenant_code)
        if existing:
            return existing

        tenant_id = self._new_id("tnt")
        role_admin_id = self._new_id("role")
        role_member_id = self._new_id("role")
        user_id = self._new_id("usr")
        membership_id = self._new_id("mem")
        dept_root_id = self._new_id("dept")
        # Compute the expensive admin-hash OUTSIDE the write lock (task 2.6).
        admin_hash = hash_password(admin_password, min_length=_pw_min_length)

        with self._tx() as con:
            con.execute(
                "INSERT INTO tenants(id, code, name, active, shared_root, version)"
                " VALUES (?,?,?,1,?,1)",
                (tenant_id, tenant_code, tenant_name.strip(), shared_root),
            )
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash,"
                " active, is_platform_admin, must_change_password,"
                " temp_password_expires_at, version)"
                " VALUES (?,?,?,?,1,1,?,?,1)",
                (user_id, admin_username, admin_display, admin_hash,
                 # A test/bootstrap admin (allow_weak) skips the forced first-login
                 # password change so it can be used immediately. A real bootstrap
                 # (allow_weak=False) MUST carry a valid temporary-password expiry
                 # so the account is never left "forced password change, no expiry".
                 0 if allow_weak else 1,
                 None if allow_weak else _now() + TEMPORARY_PASSWORD_TTL_SECONDS),
            )
            con.execute(
                "INSERT INTO memberships(id, tenant_id, user_id, display_name, active, version)"
                " VALUES (?,?,?,?,1,1)",
                (membership_id, tenant_id, user_id, admin_display),
            )
            # built-in roles
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json, version)"
                " VALUES (?,?,?,?,1,?,1)",
                (role_admin_id, tenant_id, TENANT_ADMIN_CODE, BUILTIN_ROLES[TENANT_ADMIN_CODE],
                 json.dumps(list(PERMISSION_CATALOG))),
            )
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json, version)"
                " VALUES (?,?,?,?,1,?,1)",
                (role_member_id, tenant_id, MEMBER_CODE, BUILTIN_ROLES[MEMBER_CODE],
                 json.dumps(["tenant.info.read", "agent.read", "history.read",
                             "knowledge.read", "memory.read", "todo.read", "todo.write"])),
            )
            con.execute(
                "INSERT INTO membership_roles(membership_id, role_id) VALUES (?,?)",
                (membership_id, role_admin_id),
            )
            # virtual organization root
            con.execute(
                "INSERT INTO departments(id, tenant_id, parent_id, code, name, sort_order, active, version)"
                " VALUES (?,?,NULL,'__root__','组织根',0,1,1)",
                (dept_root_id, tenant_id),
            )
            # audit
            self._audit_in_tx(
                con,
                actor_username=admin_username,
                actor_user_id=user_id,
                tenant_id=tenant_id,
                target_tenant_id=tenant_id,
                action="tenant.bootstrap",
                target=f"tenant:{tenant_id}",
                redacted_changes={"code": tenant_code, "name": tenant_name},
                result="success",
            )
            con.commit()

        return {
            "id": tenant_id,
            "code": tenant_code,
            "name": tenant_name,
            "active": True,
            "shared_root": shared_root,
            "version": 1,
        }

    # --- helpers -----------------------------------------------------------

    def _find_tenant_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT * FROM tenants WHERE code=?", (code.strip().lower(),)
        )
        return dict(rows[0]) if rows else None

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        rows = self._store.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,))
        return dict(rows[0]) if rows else None

    # --- agent binding projection (task 3.8) ------------------------------

    def get_agent_binding(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Return the tenant binding for a global agent_id, or None if unbound."""
        rows = self._store.execute(
            "SELECT * FROM agent_bindings WHERE agent_id=?", (agent_id,)
        )
        return dict(rows[0]) if rows else None

    def list_agent_bindings(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all agent bindings, optionally scoped to a tenant."""
        if tenant_id:
            rows = self._store.execute(
                "SELECT * FROM agent_bindings WHERE tenant_id=?", (tenant_id,)
            )
        else:
            rows = self._store.execute("SELECT * FROM agent_bindings")
        return [dict(r) for r in rows]

    def agents_for_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Agent ids bound to a tenant, ordered for a stable default selection."""
        rows = self._store.execute(
            "SELECT * FROM agent_bindings WHERE tenant_id=? ORDER BY created_at, agent_id",
            (tenant_id,),
        )
        return [dict(r) for r in rows]

    def tenant_agent_ids(self, tenant_id: str) -> List[str]:
        return [b["agent_id"] for b in self.agents_for_tenant(tenant_id)]

    def tenant_shared_root(self, tenant_id: str) -> Optional[str]:
        """Resolve the tenant's configured shared root, if the tenant exists."""
        tenant = self.get_tenant(tenant_id)
        return tenant["shared_root"] if tenant else None

    def tenant_shared_roots(self) -> List[Dict[str, str]]:
        """Return ``{id, shared_root}`` for every tenant (task 3.9).

        Unlike ``list_tenants`` this does not strip ``shared_root``: the
        resource-isolation layer needs each tenant's real root to prevent
        cross-tenant containment (one tenant's root inside another's).
        """
        rows = self._store.execute("SELECT id, shared_root FROM tenants")
        return [{"id": r["id"], "shared_root": r["shared_root"]} for r in rows]

    def tenant_default_agent_id(self, tenant_id: str) -> Optional[str]:
        tenant = self.get_tenant(tenant_id)
        return tenant.get("default_agent_id") if tenant else None

    def bind_agent(self, *, tenant_id: str, agent_id: str,
                   private_owner_user_id: Optional[str] = None) -> Dict[str, Any]:
        """Register/bind a global agent to a tenant (task 4.1 migration).

        Idempotent: re-running with the same ``agent_id``+``tenant_id`` does not
        duplicate or error. A non-null ``private_owner_user_id`` records the
        private owner (historically the initial admin); NULL means explicitly
        tenant-shared. The binding is the single source of truth for which
        tenant can see the agent.

        Cross-tenant re-binding is rejected: an agent_id already bound to a
        different tenant cannot be re-pointed here (no ordinary API may re-bind).
        """
        existing = self.get_agent_binding(agent_id)
        if existing:
            if existing["tenant_id"] == tenant_id:
                # Already bound to this tenant -> idempotent success.
                if private_owner_user_id is not None and existing.get("private_owner_user_id") is None:
                    with self._tx() as con:
                        con.execute(
                            "UPDATE agent_bindings SET private_owner_user_id=? WHERE agent_id=?",
                            (private_owner_user_id, agent_id))
                        con.commit()
                    existing = self.get_agent_binding(agent_id)
                return existing
            raise IdentityServiceError(
                f"agent {agent_id!r} is already bound to another tenant",
                code="conflict", status=409)
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise IdentityServiceError("tenant not found", code="not_found", status=404)
        with self._tx() as con:
            con.execute(
                "INSERT INTO agent_bindings(agent_id, tenant_id, private_owner_user_id)"
                " VALUES (?,?,?)", (agent_id, tenant_id, private_owner_user_id))
            self._audit_in_tx(
                con,
                actor_username=None, actor_user_id=None,
                tenant_id=tenant_id, target_tenant_id=tenant_id,
                action="agent.bind", target=f"agent:{agent_id}",
                redacted_changes={"tenant_id": tenant_id,
                                  "private_owner_user_id": private_owner_user_id},
                result="success")
            con.commit()
        return self.get_agent_binding(agent_id)

    def register_default_tenancy(
        self, *, tenant_id: str, private_owner_user_id: str, agent_ids: List[str],
    ) -> Dict[str, Any]:
        """In-place registration of existing content to a default tenant (task 4.1).

        Binds every ``agent_id`` to ``tenant_id`` with ``private_owner_user_id`` as
        the default private owner (historically the initial admin). Idempotent:
        already-bound agents are left as-is, so a re-run after a partial failure does
        not duplicate or re-point cross-tenant.

        Returns a summary dict of the number of agents newly bound vs already
        registered. Does not move/copy any content — existing directories, business
        ids and index state are preserved.
        """
        bound, already = 0, 0
        for agent_id in agent_ids:
            current = self.get_agent_binding(agent_id)
            if current:
                if current["tenant_id"] == tenant_id:
                    already += 1
                    # ensure the default private owner is recorded when missing
                    if current.get("private_owner_user_id") is None:
                        with self._tx() as con:
                            con.execute(
                                "UPDATE agent_bindings SET private_owner_user_id=? WHERE agent_id=?",
                                (private_owner_user_id, agent_id))
                            con.commit()
                        already += 0
                    continue
                raise IdentityServiceError(
                    f"agent {agent_id!r} is already bound to another tenant",
                    code="conflict", status=409)
            self.bind_agent(tenant_id=tenant_id, agent_id=agent_id,
                            private_owner_user_id=private_owner_user_id)
            bound += 1
        return {"bound": bound, "already_registered": already}

    def _find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT * FROM users WHERE username=?", (username.strip(),)
        )
        return dict(rows[0]) if rows else None

    def _membership(self, user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT m.*, u.active AS user_active FROM memberships m"
            " JOIN users u ON u.id=m.user_id"
            " WHERE m.user_id=? AND m.tenant_id=?",
            (user_id, tenant_id),
        )
        return dict(rows[0]) if rows else None

    def get_membership(self, user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        row = self._membership(user_id, tenant_id)
        return row

    def _role_codes_for_membership(self, membership_id: str) -> List[str]:
        rows = self._store.execute(
            "SELECT r.code FROM roles r JOIN membership_roles mr ON mr.role_id=r.id"
            " WHERE mr.membership_id=? ORDER BY r.code",
            (membership_id,),
        )
        return [r["code"] for r in rows]

    def _permissions_for_membership(self, membership_id: str) -> set:
        rows = self._store.execute(
            "SELECT r.code, r.permissions_json FROM roles r"
            " JOIN membership_roles mr ON mr.role_id=r.id"
            " WHERE mr.membership_id=?",
            (membership_id,),
        )
        role_permissions: Dict[str, set] = {}
        role_codes: List[str] = []
        for row in rows:
            if row["code"] in (TENANT_ADMIN_CODE, MEMBER_CODE):
                role_codes.append(row["code"])
            else:
                role_codes.append(row["code"])
                role_permissions[row["code"]] = set(json.loads(row["permissions_json"] or "[]"))
        from auth.policy import permissions_for_roles

        return permissions_for_roles(role_codes, role_permissions)

    def role_codes_for(self, user_id: str, tenant_id: str) -> List[str]:
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return []
        return self._role_codes_for_membership(membership["id"])

    def permissions_for(self, user_id: str, tenant_id: str) -> set:
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return set()
        return self._permissions_for_membership(membership["id"])

    # --- effective resources (the resource-authorization core) -----------

    @staticmethod
    def _grant_rows_to_list(rows) -> List[Dict[str, str]]:
        return [
            {"resource_kind": r["resource_kind"], "resource_id": r["resource_id"], "action": r["action"]}
            for r in rows
        ]

    def _role_grants_for_membership(self, membership_id: str) -> List[Dict[str, str]]:
        rows = self._store.execute(
            "SELECT g.resource_kind, g.resource_id, g.action FROM role_resource_grants g"
            " JOIN membership_roles mr ON mr.role_id=g.role_id"
            " WHERE mr.membership_id=? ORDER BY g.resource_kind, g.resource_id, g.action",
            (membership_id,),
        )
        return self._grant_rows_to_list(rows)

    def grants_for(self, user_id: str, tenant_id: str) -> List[Dict[str, str]]:
        """Union of resource grants across the user's effective roles in a tenant."""
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return []
        return self._role_grants_for_membership(membership["id"])

    def _membership_role_grants(self, membership_id: str) -> List[Dict[str, str]]:
        return self._role_grants_for_membership(membership_id)

    def _tenant_grants(self, tenant_id: str) -> List[Dict[str, str]]:
        rows = self._store.execute(
            "SELECT resource_kind, resource_id, action FROM tenant_resource_grants"
            " WHERE tenant_id=? ORDER BY resource_kind, resource_id, action",
            (tenant_id,),
        )
        return self._grant_rows_to_list(rows)

    def is_platform_admin_user(self, user_id: str) -> bool:
        """True when the user is an active platform admin (the ``all`` source)."""
        user = self._find_user_by_id(user_id)
        return bool(user and user["active"] and user["is_platform_admin"])

    def authorization_mode(self, user_id: str, tenant_id: Optional[str]) -> str:
        """Return ``all`` for an active platform admin, else ``role``.

        This is the server-side derivation of the dynamic platform all — it is
        recomputed on every call and never read from the front end or a cached
        copy. A platform admin still requires a real membership for tenant
        business access (checked by the caller), but the *authorization mode*
        for resource/policy decisions is ``all``.
        """
        if self.is_platform_admin_user(user_id):
            # A platform admin must still have a valid tenant to operate; the
            # caller (grant check / catalog) decides whether it has one for
            # business access. The mode itself is derived purely from identity.
            return "all"
        return "role"

    def check_resource_action(
        self,
        user_id: str,
        tenant_id: str,
        kind: str,
        resource_id: str,
        action: str,
        *,
        permission: Optional[str] = None,
    ) -> bool:
        """True when ``user`` may perform ``action`` on ``resource`` in ``tenant``.

        A platform admin returns True for any *known* resource-kind/action (all).
        For a normal member both a functional permission (when supplied) and an
        explicit resource grant must be present. Unknown resource kinds/actions
        are rejected by the caller via :func:`normalize_grants`; here an unknown
        kind/action is always False so ``all`` never turns an arbitrary name into
        a grant.
        """
        if kind not in RESOURCE_ACTIONS or action not in RESOURCE_ACTIONS[kind]:
            return False
        if self.authorization_mode(user_id, tenant_id) == "all":
            return True
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return False
        if permission is not None:
            if permission not in self._permissions_for_membership(membership["id"]):
                return False
        grants = self._role_grants_for_membership(membership["id"])
        return resource_granted(grants, kind, resource_id, action)

    def resource_ids_for(self, user_id: str, tenant_id: str, kind: str, action: str,
                         permission: Optional[str] = None) -> set:
        """Return the resource ids a member may operate on for kind+action.

        A platform admin is unrestricted (the caller projects the live catalog).
        For a normal member, returns the explicit set granted across roles,
        intersected with the functional permission when one is supplied.
        """
        if self.authorization_mode(user_id, tenant_id) == "all":
            return None  # sentinel: unrestricted (caller projects live catalog)
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return set()
        if permission is not None and permission not in self._permissions_for_membership(membership["id"]):
            return set()
        grants = self._role_grants_for_membership(membership["id"])
        return resource_ids_for(grants, kind, action)

    def grantable_resource_ids(self, tenant_id: str, kind: str, action: str,
                               owned_ids) -> set:
        """Ids a tenant may allocate to a role: tenant grants + own resources.

        ``owned_ids`` are tenant-owned resources whose allocatable scope comes
        from their origin (e.g. tenant-bound agents, tenant skills, tenant models).
        The result intersects tenant global grants with tenant-owned ids so a
        tenant admin cannot grant a globally-open resource it does not own.
        """
        tenant_grants = self._tenant_grants(tenant_id)
        global_ids = resource_ids_for(tenant_grants, kind, action)
        if owned_ids is None:
            return global_ids
        return global_ids | set(owned_ids)

    def role_model_defaults(self, role_id: str) -> Dict[str, str]:
        row = self._store.execute(
            "SELECT model_defaults_json FROM roles WHERE id=?", (role_id,)
        )
        if not row:
            return {}
        return dict(json.loads(row[0]["model_defaults_json"] or "{}"))

    def _role_model_defaults_for_membership(self, membership_id: str) -> List[Dict[str, str]]:
        """Collect each role's model defaults for a membership, role by role.

        Keeps the per-role source so a caller can detect *conflicting* defaults
        (two roles pinning the same capability to different models) without a
        caller-determined winner. An empty/absent default is skipped.
        """
        rows = self._store.execute(
            "SELECT r.id, r.model_defaults_json FROM roles r"
            " JOIN membership_roles mr ON mr.role_id=r.id"
            " WHERE mr.membership_id=? ORDER BY r.code",
            (membership_id,),
        )
        out = []
        for row in rows:
            defaults = json.loads(row["model_defaults_json"] or "{}")
            if defaults:
                out.append({"role_id": row["id"], "defaults": defaults})
        return out

    def model_defaults_for(self, user_id: str, tenant_id: str,
                           capability: str = "chat") -> Dict[str, Any]:
        """Resolve the effective default model for a capability for a member.

        Follows the spec 5.2 chain: a member's role defaults for the capability
        are collected across all their roles. If exactly one distinct valid model
        is configured, it is the effective default. Conflicting defaults are *not*
        silently ranked — they surface ``{"status": "conflict"}`` so the caller
        can require the user to pick. Platform all has no forced default.
        """
        if self.authorization_mode(user_id, tenant_id) == "all":
            return {"status": "unrestricted"}
        membership = self._membership(user_id, tenant_id)
        if not membership:
            return {"status": "none"}
        defaults = self._role_model_defaults_for_membership(membership["id"])
        candidates = {
            d["defaults"].get(capability)
            for d in defaults
            if d["defaults"].get(capability)
        }
        if not candidates:
            return {"status": "none"}
        if len(candidates) == 1:
            return {"status": "default", "model": next(iter(candidates))}
        return {"status": "conflict", "models": sorted(candidates)}

    # --- tenant global resource limits (platform-controlled) -------------

    def tenant_resource_grants(self, tenant_id: str) -> List[Dict[str, str]]:
        return self._tenant_grants(tenant_id)

    def set_tenant_resource_grants(self, *, actor_user_id: str, tenant_id: str,
                                   grants: Sequence[Dict[str, Any]],
                                   expected_version: int) -> List[Dict[str, str]]:
        """Replace the platform-wide global resource grants a tenant may allocate.

        The whole replacement is one transaction with the tenant version/audit.
        A platform admin may adjust a tenant's *limit*, but never uses its own
        ``all`` to skip the target tenant validation (there is no copy of the
        actor's grants onto the tenant here). Each referenced resource must
        resolve to an existing, enabled source (validated by the caller's
        catalog projection), so an unknown id is rejected rather than recorded.
        """
        if not self.is_platform_admin_user(actor_user_id):
            raise IdentityServiceError("forbidden", code="forbidden", status=403)
        normalized = normalize_resource_grants(grants or [])
        with self._tx() as con:
            tenant = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            if not tenant:
                raise IdentityServiceError("tenant not found", code="not_found", status=404)
            if tenant["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            con.execute("DELETE FROM tenant_resource_grants WHERE tenant_id=?", (tenant_id,))
            for g in normalized:
                con.execute(
                    "INSERT INTO tenant_resource_grants(id, tenant_id, resource_kind, resource_id, action)"
                    " VALUES (?,?,?,?,?)",
                    (self._new_id("tgrant"), tenant_id, g["resource_kind"], g["resource_id"], g["action"]),
                )
            con.execute("UPDATE tenants SET version=version+1 WHERE id=?", (tenant_id,))
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="tenant.resource_grants.set",
                target=f"tenant:{tenant_id}",
                redacted_changes={"resource_kind_ids": sorted({g["resource_kind"] for g in normalized})},
                result="success",
            )
            con.commit()
        return normalized

    # --- live catalog projection (task 1.3 / 2.4) ------------------------

    def _resource_source_projection(self, tenant_id: str, kind: str) -> List[Dict[str, str]]:
        """Project the live catalog of a resource kind from its real source.

        Returns non-sensitive ``{resource_id, name, capability}`` entries (plus a
        small set of kind-specific metadata for the assign UI). This is a
        projection of the existing sources (navigation registry, skills manager,
        tool manager, model config, agent registry) — it never stores a second
        resource index, copies configuration, or leaks credentials.
        """
        items: List[Dict[str, str]] = []
        if kind == "menu":
            for page_id, meta in _SIGNED_CONSOLE_PAGES.items():
                items.append({
                    "resource_id": f"nav:{page_id}",
                    "name": str(meta.get("label") or page_id),
                    "capability": str(meta.get("scope", "tenant")),
                    "action": "view",
                })
        elif kind == "skill":
            items = self._project_skills()
        elif kind == "tool":
            items = self._project_tools()
        elif kind == "model":
            items = self._project_models()
        elif kind == "agent":
            items = self._project_agents(tenant_id)
        return items

    def _project_skills(self) -> List[Dict[str, str]]:
        try:
            from agent.skills.manager import SkillManager
            from common import state_dir
            custom_dir = str(state_dir.skills_dir())
            mgr = SkillManager(custom_dir=custom_dir)
            mgr.refresh_skills()
            config = mgr.get_skills_config()
            out = []
            for name, meta in config.items():
                source = meta.get("source", "builtin")
                ns = source if source in ("builtin", "custom") else "builtin"
                out.append({
                    "resource_id": f"{ns}:{name}",
                    "name": name,
                    "capability": "skill",
                    "source": ns,
                    "enabled": bool(meta.get("enabled", True)),
                    "display_name": meta.get("display_name", name),
                })
            return out
        except Exception:
            return []

    def _project_tools(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        try:
            from agent.tools.tool_manager import ToolManager
            tm = ToolManager()
            for name, meta in tm.list_tools().items():
                out.append({
                    "resource_id": f"builtin:{name}",
                    "name": name,
                    "capability": "tool",
                    "source": "builtin",
                    "description": meta.get("description", ""),
                })
        except Exception:
            pass
        # MCP tools: namespaced by their connection/server name.
        try:
            from agent.tools.tool_manager import ToolManager
            tm = ToolManager()
            mcp_instances = getattr(tm, "_mcp_tool_instances", None) or {}
            for tname, mcp_tool in mcp_instances.items():
                conn = getattr(mcp_tool, "server_name", "default")
                out.append({
                    "resource_id": f"mcp:{conn}:{tname}",
                    "name": tname,
                    "capability": "tool",
                    "source": f"mcp:{conn}",
                    "description": getattr(mcp_tool, "description", "") or "",
                })
        except Exception:
            pass
        return out

    def _project_models(self) -> List[Dict[str, str]]:
        """Project the models a user may actually use, not every registered one.

        A model without an API key (or other live credential) on file is not
        usable: listing it here would let a platform admin "grant" a tenant a
        model that fails on the first message. We therefore reuse the runtime's
        own ``ModelsHandler._session_model_catalog`` (providers with a credential
        configured, plus the globally active one) so the authorization catalog
        stays in sync with what the chat picker would actually show.
        """
        out: List[Dict[str, str]] = []
        try:
            from channel.web.web_channel import _session_model_catalog
            by_provider: Dict[str, List[str]] = {}
            for entry in _session_model_catalog():
                pid = entry.get("id")
                for m in entry.get("models", []) or []:
                    by_provider.setdefault(pid, []).append(m)
            for provider_id, model_codes in by_provider.items():
                for model_code in model_codes:
                    out.append({
                        "resource_id": f"provider:{provider_id}:{model_code}",
                        "name": model_code,
                        "capability": "model",
                        "source": f"provider:{provider_id}",
                        "provider": provider_id,
                    })
        except Exception:
            pass

        return out

    def _project_agents(self, tenant_id: str) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        agent_ids = self.tenant_agent_ids(tenant_id)
        try:
            from agent.registry import get_agent_registry
            registry = get_agent_registry()
            for aid in agent_ids:
                try:
                    profile = registry.get(aid, require_enabled=False)
                except Exception:
                    continue
                out.append({
                    "resource_id": f"agent:{aid}",
                    "name": profile.name,
                    "capability": "agent",
                    "source": "agent",
                    "enabled": bool(profile.enabled),
                })
        except Exception:
            pass
        return out

    def _catalog_assignable_ids(self, tenant_id: str, kind: str, action: str) -> set:
        """Ids a tenant admin may assign for a kind+action (limit ∩ owned)."""
        owned = self._project_owned_ids(tenant_id, kind)
        return self.grantable_resource_ids(tenant_id, kind, action, owned)

    def _project_owned_ids(self, tenant_id: str, kind: str) -> Optional[set]:
        if kind == "agent":
            return set(self.tenant_agent_ids(tenant_id))
        if kind == "skill":
            try:
                return {e["resource_id"] for e in self._project_skills()}
            except Exception:
                return None
        if kind == "tool":
            return None  # global, controlled by tenant grants
        if kind == "model":
            return None  # global, controlled by tenant grants
        if kind == "menu":
            return None  # page-scope, platform cannot grant to normal roles
        return None

    def authorization_catalog(self, tenant_id: str, *, kind: str, q: Optional[str] = None,
                              page: int = 1, page_size: int = 100,
                              all_mode: bool = False, minimal: bool = False) -> Dict[str, Any]:
        """Return the catalog for ``kind`` scoped to the current tenant.

        ``all_mode`` (platform admin managing a target) shows the whole live
        directory. Otherwise only resources the tenant may allocate (its global
        grants + owned) are returned. ``minimal`` strips to id/name/capability.
        Pagination happens after authorization filtering so totals are exact.
        """
        if kind not in RESOURCE_ACTIONS:
            raise IdentityServiceError("unknown resource kind", code="invalid_kind")
        items = self._resource_source_projection(tenant_id, kind)
        allowed_ids: Optional[set] = None
        if not all_mode:
            # Only resources the tenant may allocate. For menu/agent the scope is
            # the page/owned set; else intersect with the tenant's global grants.
            allowed = set()
            for action in RESOURCE_ACTIONS[kind]:
                allowed |= self.grantable_resource_ids(
                    tenant_id, kind, action, self._project_owned_ids(tenant_id, kind))
            # ``grantable_resource_ids`` may return bare ids (e.g. tenant-owned
            # agents resolved from ``agent_id``, skills from ``name``) while the
            # catalog's ``resource_id`` carries a ``kind:`` prefix. Normalize both
            # forms so a resource whose owned/granted id is ``default`` still
            # matches a catalog entry of ``agent:default``.
            allowed_ids = set()
            for rid in allowed:
                allowed_ids.add(rid)
                if ":" not in rid:
                    allowed_ids.add(kind + ":" + rid)
                else:
                    allowed_ids.add(rid.split(":", 1)[1])
            items = [it for it in items if it["resource_id"] in allowed_ids or it["resource_id"].split(":")[0] == "nav"]
        if q:
            lq = q.lower()
            items = [it for it in items if lq in it["name"].lower() or lq in it.get("provider", "").lower()]
        total = len(items)
        start = (page - 1) * page_size
        page_items = items[start:start + page_size]
        if minimal:
            page_items = [{"resource_id": i["resource_id"], "name": i["name"], "capability": i["capability"]} for i in page_items]
        return {"kind": kind, "items": page_items, "total": total, "page": page,
                "resource_actions": list(RESOURCE_ACTIONS.get(kind, []))}

    def authorization_catalog_minimal(self, user_id: str, tenant_id: str, *,
                                      kind: str, q: Optional[str] = None,
                                      page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """purpose=use: the caller's own authorized resources, minimal projection."""
        if kind not in RESOURCE_ACTIONS:
            raise IdentityServiceError("unknown resource kind", code="invalid_kind")
        mode = self.authorization_mode(user_id, tenant_id)
        if mode == "all":
            return self.authorization_catalog(tenant_id, kind=kind, q=q, page=page,
                                              page_size=page_size, all_mode=True, minimal=True)
        items = self._resource_source_projection(tenant_id, kind)
        my_ids = set()
        for action in RESOURCE_ACTIONS[kind]:
            rid = self.resource_ids_for(user_id, tenant_id, kind, action,
                                        permission=_resource_permission(kind, action))
            if rid is not None:
                my_ids |= rid
        items = [it for it in items if it["resource_id"] in my_ids]
        if q:
            lq = q.lower()
            items = [it for it in items if lq in it["name"].lower()]
        total = len(items)
        start = (page - 1) * page_size
        page_items = [{"resource_id": i["resource_id"], "name": i["name"], "capability": i["capability"]}
                      for i in items[start:start + page_size]]
        return {"kind": kind, "items": page_items, "total": total, "page": page,
                "resource_actions": list(RESOURCE_ACTIONS.get(kind, []))}

    def is_member(self, user_id: str, tenant_id: str) -> bool:
        membership = self._membership(user_id, tenant_id)
        return bool(
            membership
            and membership["active"]
            and membership["user_active"]
        )

    def is_platform_admin(self, user_id: str) -> bool:
        user = self._find_user_by_id(user_id)
        return bool(user and user["active"] and user["is_platform_admin"])

    def _is_tenant_admin(self, user_id: str, tenant_id: str) -> bool:
        membership = self._membership(user_id, tenant_id)
        if not membership or not membership["active"] or not membership["user_active"]:
            return False
        return TENANT_ADMIN_CODE in self._role_codes_for_membership(membership["id"])

    def _count_valid_platform_admins(self, con=None) -> int:
        sql = "SELECT COUNT(*) AS c FROM users WHERE is_platform_admin=1 AND active=1"
        if con is not None:
            return con.execute(sql).fetchone()["c"]
        return self._store.execute(sql)[0]["c"]

    def _count_valid_tenant_admins(self, tenant_id: str, con=None) -> int:
        sql = (
            "SELECT COUNT(*) AS c FROM memberships m"
            " JOIN users u ON u.id=m.user_id"
            " JOIN membership_roles mr ON mr.membership_id=m.id"
            " JOIN roles r ON r.id=mr.role_id"
            " WHERE m.tenant_id=? AND m.active=1 AND u.active=1 AND r.code=?"
        )
        if con is not None:
            return con.execute(sql, (tenant_id, TENANT_ADMIN_CODE)).fetchone()["c"]
        return self._store.execute(sql, (tenant_id, TENANT_ADMIN_CODE))[0]["c"]

    # --- login / session (task 2.4) ---------------------------------------

    def login(self, username: str, password: str) -> LoginResult:
        # Fast path: a single read lets us uniformly reject unknown/password/weak
        # without enumeration. The authoritative re-check happens inside the
        # issuing transaction (BEGIN IMMEDIATE) on the same connection.
        user = self._find_user_by_username(username)
        if not user:
            raise IdentityServiceError("invalid login", code="invalid_login", status=401)
        if not verify_password(password, user["password_hash"]):
            raise IdentityServiceError("invalid login", code="invalid_login", status=401)

        token = generate_token()
        with self._tx() as con:
            # Re-read the account row on the SAME connection that issues the
            # session and re-verify the password hash/version, status and temp
            # deadline under the write lock. This closes the race where a
            # concurrent reset/disable/change lands between the read above and
            # the session issue, so a login cannot mint a session for a stale
            # credential.
            row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                raise IdentityServiceError("invalid login", code="invalid_login", status=401)
            current = dict(row)
            if not verify_password(password, current["password_hash"]):
                raise IdentityServiceError("invalid login", code="invalid_login", status=401)
            if not current["active"]:
                raise IdentityServiceError("account disabled", code="account_disabled", status=403)
            restricted = bool(current["must_change_password"])
            if restricted and self._unusable_temp(current):
                raise IdentityServiceError("invalid login", code="invalid_login", status=401)
            ttl = self._session_ttl_for(current)
            con.execute(
                "INSERT INTO auth_sessions"
                " (id, token_hash, user_id, expires_at, restricted)"
                " VALUES (?,?,?,?,?)",
                (secrets.token_urlsafe(18), hash_token(token), current["id"],
                 int(time.time()) + (ttl if ttl is not None else session_ttl_seconds(restricted)),
                 int(restricted)),
            )
            con.commit()
            # keep the authoritative values from the locked read
            user = current

        tenants = self._active_tenants_for(user["id"])
        return LoginResult(
            user_id=user["id"],
            username=user["username"],
            display_name=user["display_name"],
            token=token,
            must_change_password=restricted,
            restricted=restricted,
            tenants=tenants,
            is_platform_admin=bool(user["is_platform_admin"]),
        )

    def _session_ttl_for(self, user: Dict[str, Any]) -> Optional[int]:
        """Session TTL, capped by the temporary-credential deadline when needed.

        A restricted session must not outlive its temp-password expiry; an
        unrestricted session uses the normal (7-day) TTL.
        """
        if not user["must_change_password"]:
            return None
        normal_ttl = session_ttl_seconds(restricted=True)
        expires_at = user.get("temp_password_expires_at")
        if not expires_at:
            return normal_ttl
        remaining = int(expires_at) - _now()
        if remaining <= 0:
            return 0  # caller rejects before issuing
        return min(normal_ttl, remaining)

    def _unusable_temp(self, user: Dict[str, Any]) -> bool:
        """True when the account is forced-password-change and its temp has lapsed."""
        if not user["must_change_password"]:
            return False
        expires_at = user.get("temp_password_expires_at")
        if not expires_at:
            # A forced-password-change account with no deadline is unsafe: treat
            # as unusable so it is not silently usable forever.
            return True
        return int(expires_at) <= _now()

    def _active_tenants_for(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT t.* FROM tenants t JOIN memberships m ON m.tenant_id=t.id"
            " WHERE m.user_id=? AND t.active=1 AND m.active=1"
            " ORDER BY t.name",
            (user_id,),
        )
        return [dict(r) for r in rows]

    def verify_session(self, token: str) -> Optional[Dict[str, Any]]:
        if not self._sessions.validate_token(token):
            return None
        row = self._sessions.get_by_token(token)
        user = self._find_user_by_id(row["user_id"])
        if not user or not user["active"]:
            return None
        # A restricted session must not outlive its temporary-credential deadline,
        # even if the session's own TTL has not yet elapsed.
        if user["must_change_password"] and self._unusable_temp(user):
            return None
        return {"user": user, "session": row}

    def revoke_session(self, token: str) -> None:
        self._sessions.revoke(token)

    def audit_denied_login(self, account: str, source: str,
                           category: str, retry_after: Optional[int]) -> None:
        """Record one sanitized denied-login audit event (bounded by caller).

        Only the normalized account (never the password) and a window aggregate
        are stored; the caller deduplicates so this is at most one write per
        blocked account/source per window.
        """
        self._audit.record(
            actor_username=account,
            tenant_id=None,
            target_tenant_id=None,
            action="auth.login.denied",
            target=f"source:{source}",
            redacted_changes={"category": category,
                              "retry_after": retry_after,
                              "rate_limited": True},
            result="denied",
        )

    def _find_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        rows = self._store.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return dict(rows[0]) if rows else None

    def change_password(self, token: str, old_password: str, new_password: str) -> None:
        """Change password after verifying old, atomically revoke old sessions.

        The password update, forced-flag clear, audit event and revocation of
        every existing session for the account are committed in one transaction:
        a failure in any step leaves the account unchanged and the old sessions
        valid (no partial success).
        """
        session = self.verify_session(token)
        if not session:
            raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
        user = session["user"]
        if new_password.lower() in _COMMON_PASSWORDS or len(new_password) < MIN_PASSWORD_LENGTH:
            raise IdentityServiceError("weak password", code="weak_password", status=400)
        # Compute the expensive new-hash OUTSIDE the write lock (task 2.6).
        new_hash = hash_password(new_password)
        with self._tx() as con:
            # Re-read the account row on the SAME connection that performs the
            # update and re-verify the old password against the *current* hash +
            # version. This closes the race where a concurrent password change (or
            # reset/disable) lands between the session check above and the write,
            # so a stale front-door check can never commit an invalid change.
            row = con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if not row:
                raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
            current = dict(row)
            if current["must_change_password"] and self._unusable_temp(current):
                raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
            if not verify_password(old_password, current["password_hash"]):
                raise IdentityServiceError("invalid old password", code="invalid_old", status=401)
            con.execute(
                "UPDATE users SET password_hash=?, must_change_password=0,"
                " temp_password_expires_at=NULL, version=version+1 WHERE id=?",
                (new_hash, current["id"]),
            )
            # revoke every existing session for the account in the same tx
            con.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (int(time.time()), user["id"]),
            )
            self._audit_in_tx(
                con,
                actor_user_id=user["id"],
                actor_username=user["username"],
                action="user.password.change",
                target=f"user:{user['id']}",
                redacted_changes={},
                result="success",
            )
            con.commit()

    def _set_password_for_user(self, user_id: str, new_password: str, must_change: bool) -> None:
        if new_password.lower() in _COMMON_PASSWORDS or len(new_password) < MIN_PASSWORD_LENGTH:
            raise IdentityServiceError("weak password", code="weak_password", status=400)
        new_hash = hash_password(new_password)  # expensive, outside the lock
        with self._tx() as con:
            con.execute(
                "UPDATE users SET password_hash=?, must_change_password=?,"
                " temp_password_expires_at=?, version=version+1 WHERE id=?",
                (new_hash, int(must_change), None, user_id),
            )
            self._audit_in_tx(
                con,
                actor_user_id=user_id,
                actor_username=None,
                action="user.password.change",
                target=f"user:{user_id}",
                redacted_changes={},
                result="success",
            )
            con.commit()

    # --- self-account profile edit (PATCH /auth/profile) ------------------

    def update_self_profile(
        self,
        token: str,
        *,
        display_name: Optional[str] = None,
        member_display_name: Optional[str] = None,
        position_text: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Self-service edit of the caller's own profile fields.

        White-listed, self-scoped only: the caller may change their *global*
        display name and (for the tenant explicitly selected via ``tenant_id``)
        their member display name / position. The caller can NEVER change roles,
        department, tenant membership, username, platform-admin flag or another
        account. ``None`` leaves a field untouched; an empty string (for the text
        fields) clears it. Restricted (must_change_password) accounts may not
        edit, and a member edit is rejected unless the caller is an active member
        of ``tenant_id``.

        Returns the refreshed :meth:`self_context` so the client can re-render
        in place without a second round trip.
        """
        session = self.verify_session(token)
        if not session:
            raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
        user = session["user"]
        if user["must_change_password"]:
            raise IdentityServiceError(
                "password change required", code="password_change_required", status=403)

        with self._tx() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if not row or not row["active"]:
                raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
            current = dict(row)

            changes: Dict[str, Any] = {}

            if display_name is not None:
                name = (display_name or "").strip()
                if not name:
                    raise IdentityServiceError(
                        "display name required", code="invalid_display_name", status=400)
                if len(name) > 80:
                    raise IdentityServiceError(
                        "display name too long", code="invalid_display_name", status=400)
                if name != current["display_name"]:
                    con.execute(
                        "UPDATE users SET display_name=?, version=version+1 WHERE id=?",
                        (name, current["id"]),
                    )
                    changes["display_name"] = name

            # Tenant-scoped member fields (name/position) are applied only when
            # an explicit tenant is selected AND the caller is an active member
            # of it. Editing another tenant's membership is impossible by design.
            want_member = (member_display_name is not None or position_text is not None)
            if want_member and tenant_id:
                mem = con.execute(
                    "SELECT * FROM memberships WHERE user_id=? AND tenant_id=? AND active=1",
                    (user["id"], tenant_id),
                ).fetchone()
                if not mem:
                    raise IdentityServiceError(
                        "not a member of tenant", code="not_a_member", status=403)
                mem_current = dict(mem)
                new_member_name = mem_current.get("display_name") or ""
                if member_display_name is not None:
                    mn = (member_display_name or "").strip()
                    if not mn:
                        raise IdentityServiceError(
                            "member name required", code="invalid_member_name", status=400)
                    if len(mn) > 80:
                        raise IdentityServiceError(
                            "member name too long", code="invalid_member_name", status=400)
                    new_member_name = mn
                new_position = mem_current.get("position_text") or ""
                if position_text is not None:
                    pos = (position_text or "").strip()
                    if len(pos) > 120:
                        raise IdentityServiceError(
                            "position too long", code="invalid_position", status=400)
                    new_position = pos
                if (new_member_name != (mem_current.get("display_name") or "")
                        or new_position != (mem_current.get("position_text") or "")):
                    con.execute(
                        "UPDATE memberships SET display_name=?, position_text=?,"
                        " version=version+1 WHERE id=?",
                        (new_member_name, new_position, mem_current["id"]),
                    )
                    changes["member"] = {
                        "display_name": new_member_name,
                        "position_text": new_position,
                    }

            if not changes:
                con.commit()
                return self.self_context(token)

            self._audit_in_tx(
                con,
                actor_user_id=user["id"],
                actor_username=user["username"],
                action="user.profile.change",
                target=f"user:{user['id']}",
                redacted_changes=changes,
                result="success",
            )
            con.commit()
        return self.self_context(token)

    def set_self_avatar(self, token: str) -> Dict[str, Any]:
        """Mark the caller's account as having an uploaded avatar.

        Called after the avatar bytes are written to disk by the HTTP handler.
        Only the owning account may set its own avatar flag; the flag is a
        metadata token, never the image itself.
        """
        session = self.verify_session(token)
        if not session:
            raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
        user = session["user"]
        if user["must_change_password"]:
            raise IdentityServiceError(
                "password change required", code="password_change_required", status=403)
        with self._tx() as con:
            con.execute(
                "UPDATE users SET avatar=?, version=version+1 WHERE id=?",
                ("image", user["id"]),
            )
            self._audit_in_tx(
                con,
                actor_user_id=user["id"],
                actor_username=user["username"],
                action="user.profile.avatar",
                target=f"user:{user['id']}",
                redacted_changes={"avatar": "image"},
                result="success",
            )
            con.commit()
        return self.self_context(token)

    # --- self-account context (GET /auth/me) ------------------------------

    def self_context(self, token: str) -> Dict[str, Any]:
        """Return the single self-account projection for ``GET /auth/me``.

        The subject is always the account owning ``token``; the client cannot
        select another user or member. A restricted (must_change_password)
        account receives only the minimal ``user`` projection and an empty
        ``tenants`` list. White-listed fields only — never raw DB rows, hashes,
        temp-password expiry, tokens or internal paths.
        """
        session = self.verify_session(token)
        if not session:
            raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
        user = session["user"]
        restricted = bool(user["must_change_password"])
        user_projection = {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "is_platform_admin": bool(user["is_platform_admin"]),
            "avatar": user.get("avatar") or None,
        }
        tenants: List[Dict[str, Any]] = []
        if not restricted:
            for tenant in self._active_tenants_for(user["id"]):
                membership = self._self_membership_summary(user["id"], tenant["id"])
                if membership is None:
                    continue
                tenants.append({
                    "id": tenant["id"],
                    "code": tenant["code"],
                    "name": tenant["name"],
                    "membership": membership,
                })
        return {
            "status": "success",
            "user": user_projection,
            "must_change_password": restricted,
            "tenants": tenants,
        }

    def context_for_tenant(self, token: str, tenant_id: str) -> Dict[str, Any]:
        """Return the current tenant's effective capability summary for ``GET /auth/context``.

        Only returns the requesting user's own effective_permissions, admin
        qualification and the static consumer availability, scoped to the
        explicitly selected ``tenant_id``. It MUST NOT return tenant profile,
        directories, other members or platform tenant data, and it does NOT
        require ``tenant.info.read`` — a zero-permission effective member can
        still read their own empty capability summary.

        Raises 400 (missing/conflicting tenant … handled by the caller), 403
        (no valid membership), 503 (identity service unavailable) and 401
        (invalid session).
        """
        session = self.verify_session(token)
        if not session:
            raise IdentityServiceError("unauthorized", code="unauthorized", status=401)
        user = session["user"]
        if user["must_change_password"]:
            # A restricted (forced-password-change) account may not read tenant
            # capability — it can only read its minimal self info / change password.
            raise IdentityServiceError(
                "password change required", code="password_change_required", status=403)
        tenant = self.get_tenant(tenant_id)
        if not tenant or not tenant["active"]:
            raise IdentityServiceError("forbidden", code="forbidden", status=403)
        membership = self._membership(user["id"], tenant_id)
        if not membership or not membership["active"] or not membership["user_active"]:
            raise IdentityServiceError("forbidden", code="forbidden", status=403)
        permissions = self._permissions_for_membership(membership["id"])
        role_codes = self._role_codes_for_membership(membership["id"])
        is_admin = TENANT_ADMIN_CODE in role_codes
        grants = self._role_grants_for_membership(membership["id"])
        mode = self.authorization_mode(user["id"], tenant_id)
        return {
            "status": "success",
            "effective_permissions": sorted(permissions),
            "authorization_mode": mode,
            "resource_actions": self._effective_resource_actions(permissions, grants, mode),
            "is_tenant_admin": is_admin,
            "consumers": self._consumer_availability(),
            "console_pages": self._console_pages_projection(
                user, tenant, permissions, role_codes, is_admin, grants, mode),
        }

    def _effective_resource_actions(self, permissions, grants, mode) -> Dict[str, List[str]]:
        """Report which resource actions are available per kind.

        For a platform admin this reports the enabled actions for each known
        resource kind (all). For a member, a functional permission is required
        AND a grant must exist — the report is an intersection, not a grant.
        Known kinds/actions only: unknown names never appear, so ``all`` cannot
        be used to call arbitrary names.
        """
        out: Dict[str, List[str]] = {}
        for kind, actions in RESOURCE_ACTIONS.items():
            allowed = []
            for action in actions:
                perm = _resource_permission(kind, action)
                if mode == "all":
                    allowed.append(action)
                elif perm and perm in permissions and resource_ids_for(grants, kind, action):
                    allowed.append(action)
            if allowed:
                out[kind] = allowed
        return out

    def _console_pages_projection(self, user, tenant, permissions, role_codes, is_admin,
                                  grants=None, mode="role") -> Dict[str, Any]:
        """Minimal read-only projection for console page/tab availability.

        This is a *display* projection only: it is derived from the same
        authoritative permission + consumer state used to authorize the API, and
        is NOT itself a grant. Keys are limited to pages/tabs that this change
        actually signs (flagged below); unknown keys default to denied. Consumers
        that are still closed stay closed even if a page has a permission read.

        Each entry: ``available`` (register exists ∧ consumer open), ``read_allowed``
        (the identity allows reading it), ``scope`` (``tenant``/``platform``/``self``),
        ``reason`` (when not available) and ``actions`` (only existing finite
        booleans, e.g. ``create``/``update``/``execute``). Platform actions are
        derived from the verified platform identity, not tenant_admin.

        ``catalog``/``config``/``execution`` report the directory, configuration
        and runtime states separately so a closed execution consumer never hides
        an already-open catalog (task 2.5 / console-navigation-availability).
        """
        is_platform_admin = bool(user.get("is_platform_admin"))
        mode = mode if mode == "all" else (
            "all" if is_platform_admin else self.authorization_mode(user["id"], tenant["id"])
        )
        grants = grants or []

        def page_key(id_: str) -> bool:
            return id_ in _SIGNED_CONSOLE_PAGES

        # A page is only signed/available when its consumer is adapted+accepted.
        consumers = self._consumer_availability()
        identity_admin_open = bool(consumers.get("web_identity_admin", {}).get("available"))
        # Other business consumers are all "deferred" this milestone, so their
        # pages are signed but not available.
        signed = _SIGNED_CONSOLE_PAGES

        def read_ok(perms: set, pid: str) -> bool:
            return pid in perms

        def resource_state(kind: str, action: str, perm: str) -> bool:
            """True when the identity may read this resource kind (catalog open)."""
            if mode == "all":
                return True
            if perm and perm not in permissions:
                return False
            # A catalog read requires an explicit read grant (or kind grant).
            return resource_ids_for(grants, kind, action) != set()

        # Catalog/config/execution per resource kind, independent of consumer open.
        result: Dict[str, Any] = {}
        result["resources"] = {
            "skills": {
                "catalog": (mode == "all") or resource_ids_for(grants, "skill", "read") != set(),
                "config": (mode == "all") or resource_ids_for(grants, "skill", "edit") != set(),
                "execution": (mode == "all") or resource_ids_for(grants, "skill", "use") != set(),
            },
            "tools": {
                "catalog": (mode == "all") or resource_ids_for(grants, "tool", "read") != set(),
                "config": (mode == "all") or resource_ids_for(grants, "tool", "configure") != set(),
                "execution": (mode == "all") or resource_ids_for(grants, "tool", "execute") != set(),
            },
            "models": {
                "catalog": (mode == "all") or ("model.read" in permissions and resource_ids_for(grants, "model", "read") != set()),
                "config": (mode == "all") or is_platform_admin,
                "execution": (mode == "all") or ("model.use" in permissions and resource_ids_for(grants, "model", "use") != set()),
            },
            "agents": {
                "catalog": (mode == "all") or ("agent.read" in permissions and resource_ids_for(grants, "agent", "read") != set()),
                "config": (mode == "all") or ("agent.edit" in permissions and resource_ids_for(grants, "agent", "edit") != set()),
                "execution": (mode == "all") or ("agent.use" in permissions and resource_ids_for(grants, "agent", "use") != set()),
            },
        }

        # Identity-management pages (only open consumer this milestone).
        if identity_admin_open:
            result["admin.members"] = {
                "available": True,
                "read_allowed": read_ok(permissions, "tenant.members.read"),
                "scope": "tenant",
                "reason": "",
                "actions": {"update": is_admin, "create": is_admin},
            }
            result["admin.roles"] = {
                "available": True,
                "read_allowed": read_ok(permissions, "tenant.members.read"),
                "scope": "tenant",
                "reason": "",
                "actions": {"update": is_admin, "create": is_admin},
            }
            result["admin.organization"] = {
                "available": True,
                "read_allowed": read_ok(permissions, "tenant.org.read"),
                "scope": "tenant",
                "reason": "",
                "actions": {"update": is_admin, "create": is_admin},
            }
            result["admin.tenants"] = {
                "available": True,
                "read_allowed": is_platform_admin,
                "scope": "platform",
                "reason": "",
                "actions": {"update": is_platform_admin, "create": is_platform_admin},
            }
        # Signed-but-not-yet-available pages (business consumers still closed).
        for pid, meta in _SIGNED_CONSOLE_PAGES.items():
            if pid in result:
                continue
            # Catalog/read pages: report the directory open state separately even
            # when execution remains closed.
            if meta.get("scope") == "tenant" and pid in ("admin.skills", "admin.agents", "admin.models"):
                read_allowed = resource_state(
                    {"admin.skills": "skill", "admin.agents": "agent", "admin.models": "model"}[pid],
                    "read",
                    {"admin.skills": "skill.read", "admin.agents": "agent.read", "admin.models": "model.read"}[pid],
                )
                result[pid] = {
                    "available": read_allowed,
                    "read_allowed": read_allowed,
                    "scope": meta.get("scope", "tenant"),
                    "reason": "" if read_allowed else "no_resource_grant",
                    "actions": {},
                }
                continue
            result[pid] = {
                "available": False,
                "read_allowed": read_ok(permissions, meta.get("permission", "")),
                "scope": meta.get("scope", "tenant"),
                "reason": "consumer_closed" if identity_admin_open else "deferred",
                "actions": {},
            }
        return result

    def _consumer_availability(self) -> Dict[str, Dict[str, Any]]:
        """Static, server-side consumer availability labels (capability report).

        Consumers opened by the runtime-consumers work (chat transport, file
        upload/serve/preview, voice, tools/skills, OpenAI-compatible API, MCP
        warmup, external channels and the AgentBridge runtime) report
        ``available=true`` — authorization is always re-checked per request.
        Scheduler management stays deferred until its own slice adds trigger
        snapshots + revalidation; Desktop enterprise login remains closed.
        This is a static capability report, NOT a readiness/permission service,
        and never grants access on its own.
        """
        return {
            "web_identity_admin": {"available": True, "reason": ""},
            "chat": {"available": True, "reason": ""},
            "tools": {"available": True, "reason": ""},
            "files": {"available": True, "reason": ""},
            "projects": {"available": True, "reason": ""},
            "scheduler": {"available": False, "reason": "deferred"},
            "openai_api": {"available": True, "reason": ""},
            "desktop_enterprise": {"available": False, "reason": "deferred"},
            "mcp": {"available": True, "reason": ""},
            "channels": {"available": True, "reason": ""},
        }

    def _self_membership_summary(self, user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Member summary for the current user in one tenant (white-listed)."""
        membership = self._membership(user_id, tenant_id)
        if not membership or not membership["active"] or not membership["user_active"]:
            return None
        roles = [
            {"code": r["code"], "name": r["name"]}
            for r in self._store.execute(
                "SELECT r.code, r.name FROM roles r JOIN membership_roles mr ON mr.role_id=r.id"
                " WHERE mr.membership_id=? ORDER BY r.code",
                (membership["id"],),
            )
        ]
        department = None
        if membership.get("department_id"):
            dept_rows = self._store.execute(
                "SELECT id, name FROM departments WHERE id=? AND tenant_id=? AND active=1",
                (membership["department_id"], tenant_id),
            )
            if dept_rows:
                department = {"id": dept_rows[0]["id"], "name": dept_rows[0]["name"]}
        return {
            "display_name": membership["display_name"] or "",
            "roles": roles,
            "department": department,
            "position_text": membership["position_text"] or "",
        }

    def administered_tenants(
        self,
        actor_user_id: str,
        target_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List the tenants the actor administers (holds active ``tenant_admin``).

        Used by the member assignment UI to build the tenant multi-select:
        candidates are exactly the tenants where the actor is a ``tenant_admin``
        (a platform admin is NOT treated specially — the explicit platform
        management surface is separate). When ``target_user_id`` is given, each
        entry also reports that user's membership status within the *administered*
        tenant only (never other tenants), so a tenant admin can render the
        member's current tenant checkboxes without leaking other-tenant relations.
        """
        rows = self._store.execute(
            "SELECT DISTINCT t.id, t.code, t.name FROM tenants t"
            " JOIN memberships m ON m.tenant_id=t.id AND m.active=1"
            " JOIN users u ON u.id=m.user_id AND u.active=1"
            " JOIN membership_roles mr ON mr.membership_id=m.id"
            " JOIN roles r ON r.id=mr.role_id AND r.code=?"
            " WHERE m.user_id=? AND t.active=1 ORDER BY t.name",
            (TENANT_ADMIN_CODE, actor_user_id),
        )
        items: List[Dict[str, Any]] = []
        for row in rows:
            item: Dict[str, Any] = {
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
            }
            if target_user_id:
                mem = self._membership(target_user_id, row["id"])
                active = bool(mem and mem["active"] and mem["user_active"])
                item["member"] = active
                item["member_id"] = mem["id"] if active else None
                item["member_version"] = mem["version"] if active else None
            items.append(item)
        return items


    # --- tenant management (tasks 3.1/3.2) --------------------------------

    def list_tenants(self, q: Optional[str] = None) -> List[Dict[str, Any]]:
        if q:
            rows = self._store.execute(
                "SELECT * FROM tenants WHERE name LIKE ? OR code LIKE ?"
                " ORDER BY name",
                (f"%{q}%", f"%{q}%"),
            )
        else:
            rows = self._store.execute("SELECT * FROM tenants ORDER BY name")
        # never leak shared_root (host absolute path) to list output
        return [
            {k: v for k, v in dict(r).items() if k != "shared_root"}
            for r in rows
        ]

    def create_tenant(
        self,
        *,
        actor_user_id: str,
        code: str,
        name: str,
        admin_username: str,
        admin_display: str,
        admin_password: str,
        recent_password: str,
        shared_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Platform admin creates a tenant + initial admin, with a same-transaction audit.

        ``shared_root`` is optional. The web form must not accept a client-supplied
        path (design §4); when omitted/empty the service derives a controlled root
        under the deployment base and fails clearly (503 config_error) if none is
        available, so a tenant is never created with an unusable root. CLI
        ``bootstrap``/``register`` pass an explicit root for in-place registration.
        """
        self._require_platform_admin(actor_user_id)
        self._require_recent_password(actor_user_id, recent_password)
        code = code.strip().lower()
        if not _TENANT_CODE_RE.fullmatch(code):
            raise IdentityServiceError("invalid tenant code", code="invalid_code")
        if self._find_tenant_by_code(code):
            raise IdentityServiceError("tenant code already exists", code="conflict", status=409)
        if admin_password.lower() in _COMMON_PASSWORDS or len(admin_password) < MIN_PASSWORD_LENGTH:
            raise IdentityServiceError("weak admin password", code="weak_password")
        # Web form never supplies a shared root; derive a controlled one before
        # touching the DB so a missing/overlapping deployment root fails cleanly
        # (design §4).
        if not shared_root:
            shared_root = _derive_tenant_shared_root(code, svc=self)
        # Fail fast: a shared root that equals/contains/is contained by another
        # tenant's root can never resolve (state_dir containment) and would
        # poison the other tenant too (3.9 / design §4).
        _assert_new_tenant_root_clear(shared_root, self)

        tenant_id = self._new_id("tnt")
        user_id = self._new_id("usr")
        membership_id = self._new_id("mem")
        role_admin_id = self._new_id("role")
        role_member_id = self._new_id("role")
        dept_root_id = self._new_id("dept")
        admin_hash = hash_password(admin_password)  # expensive, outside the lock

        with self._tx() as con:
            con.execute(
                "INSERT INTO tenants(id, code, name, active, shared_root, version)"
                " VALUES (?,?,?,1,?,1)",
                (tenant_id, code, name.strip(), shared_root),
            )
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash,"
                " active, is_platform_admin, must_change_password, temp_password_expires_at, version)"
                " VALUES (?,?,?,?,1,0,1,?,1)",
                (user_id, admin_username, admin_display, admin_hash,
                 int(time.time()) + 86400 * 3),
            )
            con.execute(
                "INSERT INTO memberships(id, tenant_id, user_id, display_name, active, version)"
                " VALUES (?,?,?,?,1,1)",
                (membership_id, tenant_id, user_id, admin_display),
            )
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json, version)"
                " VALUES (?,?,?,?,1,?,1)",
                (role_admin_id, tenant_id, TENANT_ADMIN_CODE, BUILTIN_ROLES[TENANT_ADMIN_CODE],
                 json.dumps(list(PERMISSION_CATALOG))),
            )
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json, version)"
                " VALUES (?,?,?,?,1,?,1)",
                (role_member_id, tenant_id, MEMBER_CODE, BUILTIN_ROLES[MEMBER_CODE],
                 json.dumps(["tenant.info.read", "agent.read", "history.read",
                             "knowledge.read", "memory.read", "todo.read", "todo.write"])),
            )
            con.execute(
                "INSERT INTO membership_roles(membership_id, role_id) VALUES (?,?)",
                (membership_id, role_admin_id),
            )
            con.execute(
                "INSERT INTO departments(id, tenant_id, parent_id, code, name, sort_order, active, version)"
                " VALUES (?,?,NULL,'__root__','组织根',0,1,1)",
                (dept_root_id, tenant_id),
            )
            self._audit_in_tx(
                con,
                actor_user_id=actor_user_id,
                actor_username=None,
                tenant_id=None,
                target_tenant_id=tenant_id,
                action="tenant.create",
                target=f"tenant:{tenant_id}",
                redacted_changes={"code": code, "name": name},
                result="success",
            )
            con.commit()

        return {"id": tenant_id, "code": code, "name": name, "active": True, "version": 1}

    def set_tenant_status(self, actor_user_id: str, tenant_id: str, active: bool,
                          expected_version: int, recent_password: str) -> Dict[str, Any]:
        self._require_platform_admin(actor_user_id)
        self._require_recent_password(actor_user_id, recent_password)
        with self._tx() as con:
            row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            if not row:
                raise IdentityServiceError("tenant not found", code="not_found", status=404)
            if row["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            if active:
                # recovery requires a valid active tenant_admin
                if self._count_valid_tenant_admins(tenant_id, con) < 1:
                    raise IdentityServiceError(
                        "tenant has no valid admin", code="no_admin", status=409)
            # Active member→tenant continuity: deactivating a tenant must not
            # leave any of its enabled members with no other active tenant.
            affected_user_ids: List[str] = []
            if not active:
                rows = con.execute(
                    "SELECT DISTINCT m.user_id FROM memberships m"
                    " JOIN users u ON u.id=m.user_id"
                    " WHERE m.tenant_id=? AND m.active=1 AND u.active=1",
                    (tenant_id,),
                ).fetchall()
                affected_user_ids = [r["user_id"] for r in rows]
            con.execute(
                "UPDATE tenants SET active=?, version=version+1 WHERE id=?",
                (int(active), tenant_id),
            )
            if not active:
                self._check_all_affected_active_tenant_continuity(con, affected_user_ids)
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=None,
                target_tenant_id=tenant_id, action="tenant.set_status",
                target=f"tenant:{tenant_id}",
                redacted_changes={"active": active}, result="success")
            con.commit()
        return {"id": tenant_id, "active": active}

    def set_tenant_admin(self, actor_user_id: str, tenant_id: str, user_id: str,
                         display_name: str, recent_password: str) -> Dict[str, Any]:
        """Platform admin configures a tenant admin (bind existing or new user)."""
        self._require_platform_admin(actor_user_id)
        self._require_recent_password(actor_user_id, recent_password)
        with self._tx() as con:
            tenant = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            if not tenant:
                raise IdentityServiceError("tenant not found", code="not_found", status=404)
            user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not user["active"]:
                raise IdentityServiceError("user not found or disabled", code="not_found", status=404)
            admin_role = con.execute(
                "SELECT * FROM roles WHERE tenant_id=? AND code=?",
                (tenant_id, TENANT_ADMIN_CODE),
            ).fetchone()
            if not admin_role:
                raise IdentityServiceError("tenant has no admin role", code="missing_role", status=500)
            membership = con.execute(
                "SELECT * FROM memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            ).fetchone()
            if membership:
                if not membership["active"]:
                    # recovery of a disabled membership
                    con.execute(
                        "UPDATE memberships SET active=1, version=version+1 WHERE id=?",
                        (membership["id"],),
                    )
                    membership_id = membership["id"]
                else:
                    membership_id = membership["id"]
            else:
                membership_id = self._new_id("mem")
                con.execute(
                    "INSERT INTO memberships(id, tenant_id, user_id, display_name, active, version)"
                    " VALUES (?,?,?,?,1,1)",
                    (membership_id, tenant_id, user_id, display_name),
                )
            # ensure admin role binding
            bound = con.execute(
                "SELECT COUNT(*) c FROM membership_roles WHERE membership_id=? AND role_id=?",
                (membership_id, admin_role["id"]),
            ).fetchone()["c"]
            if not bound:
                con.execute(
                    "INSERT INTO membership_roles(membership_id, role_id) VALUES (?,?)",
                    (membership_id, admin_role["id"]),
                )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=None,
                target_tenant_id=tenant_id, action="tenant.set_admin",
                target=f"membership:{membership_id}",
                redacted_changes={"username": user["username"]}, result="success")
            con.commit()
        return {"membership_id": membership_id}

    def set_tenant_name(self, actor_user_id: str, tenant_id: str, name: str,
                        expected_version: int, recent_password: str) -> Dict[str, Any]:
        """Rename a tenant (platform admin). Name and version + audit commit in
        one transaction; the tenant's shared_root is never editable here."""
        self._require_platform_admin(actor_user_id)
        self._require_recent_password(actor_user_id, recent_password)
        name = name.strip()
        if not name:
            raise IdentityServiceError("tenant name is required", code="bad_request", status=400)
        with self._tx() as con:
            row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            if not row:
                raise IdentityServiceError("tenant not found", code="not_found", status=404)
            if row["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            con.execute(
                "UPDATE tenants SET name=?, version=version+1 WHERE id=?",
                (name, tenant_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=None,
                target_tenant_id=tenant_id, action="tenant.rename",
                target=f"tenant:{tenant_id}",
                redacted_changes={"name": name}, result="success")
            con.commit()
        return {"id": tenant_id, "name": name}

    def set_platform_user_status(
        self, *, actor_user_id: str, user_id: str, active: bool,
        is_platform_admin: bool, expected_version: int, recent_password: str,
    ) -> Dict[str, Any]:
        """Enable/disable a platform account and/or adjust its admin flag.

        Sensitive change: requires the current actor's ``recent_password`` (used
        only for this re-check, never persisted) and the target ``expected_version``.
        The expensive password verification happens BEFORE the write lock; inside
        the BEGIN IMMEDIATE transaction we re-verify the actor's session account,
        active status, platform-admin qualification, the target row and version,
        then apply a version-conditioned update + same-transaction audit.

        Guarantees: at least one valid (not forced-password-change) platform admin
        remains; disabling an account also verifies every enabled tenant that the
        user administers still has another valid admin, and revokes all of the
        target's sessions. Re-enabling never resurrects revoked sessions or a
        separately-disabled membership.
        """
        self._require_platform_admin(actor_user_id)
        # Expensive re-check happens inside the locked txn; the pre-flight below
        # only lets a wrong password fail fast without taking the write lock.
        self._require_recent_password(actor_user_id, recent_password)
        with self._tx() as con:
            # Re-verify actor on the SAME connection holding the write lock.
            actor = con.execute("SELECT * FROM users WHERE id=?", (actor_user_id,)).fetchone()
            if not actor or not actor["active"] or not actor["is_platform_admin"]:
                raise IdentityServiceError("forbidden", code="forbidden", status=403)
            if not verify_password(recent_password, actor["password_hash"]):
                raise IdentityServiceError("recent password required", code="invalid_old", status=401)
            target = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise IdentityServiceError("user not found", code="not_found", status=404)
            if target["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            target_active = bool(target["active"])
            target_admin = bool(target["is_platform_admin"])

            self._check_platform_admin_continuity(
                con, actor_user_id=actor_user_id, target=target,
                target_active=active, target_admin=is_platform_admin)

            # Global disable: every enabled tenant the target administers must
            # keep another valid admin; disable also revokes all sessions.
            if target_active and not active:
                self._check_all_tenant_admin_continuity_for_user(con, user_id)
            if not active:
                con.execute(
                    "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (int(time.time()), user_id),
                )
            # Re-enabling an account requires it to already keep an active
            # membership in an active tenant (member→tenant continuity).
            # NOTE: the account is still inactive in the DB here, so we must
            # check the membership directly rather than rely on the user flag.
            if not target_active and active and not self._user_has_active_tenant(con, user_id):
                raise IdentityServiceError(
                    "an enabled account must keep at least one active tenant",
                    code="last_active_tenant_required", status=409)

            con.execute(
                "UPDATE users SET active=?, is_platform_admin=?, version=version+1 WHERE id=?",
                (int(active), int(is_platform_admin), user_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, actor_username=actor["username"],
                tenant_id=None, target_tenant_id=None, action="user.set_status",
                target=f"user:{user_id}",
                redacted_changes={"active": active, "is_platform_admin": is_platform_admin},
                result="success")
            con.commit()
        return {"id": user_id, "active": active, "is_platform_admin": is_platform_admin}

    # --- member → tenant continuity (user-added constraint, 2026-09-08) ---

    def _user_has_active_tenant(self, con, user_id: str) -> bool:
        """True when the user has at least one active membership in an active tenant."""
        row = con.execute(
            "SELECT COUNT(*) c FROM memberships m"
            " JOIN tenants t ON t.id=m.tenant_id"
            " WHERE m.user_id=? AND m.active=1 AND t.active=1",
            (user_id,),
        ).fetchone()
        return int(row["c"] or 0) > 0

    def _require_active_tenant_for_enabled_user(self, con, user_id: str) -> None:
        """Reject when an enabled account would end up with no active tenant.

        Every enabled User (including platform admins) MUST keep at least one
        active Membership associated with an active Tenant. Called inside the
        same write transaction before commit so concurrent changes are seen.
        """
        user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return
        if user["active"] and not self._user_has_active_tenant(con, user_id):
            raise IdentityServiceError(
                "an enabled account must keep at least one active tenant",
                code="last_active_tenant_required", status=409)

    def _check_all_affected_active_tenant_continuity(self, con, user_ids: Sequence[str]) -> None:
        """Run the active-tenant continuity check for every affected enabled user."""
        for uid in set(user_ids):
            self._require_active_tenant_for_enabled_user(con, uid)

    def reset_platform_user_password(
        self, *, actor_user_id: str, user_id: str, expected_version: int,
        recent_password: str,
    ) -> Dict[str, Any]:
        """Reset a target platform account to a temporary password.

        The temp password is generated by the server, returned ONCE in the
        success response, and enforced as forced-password-change with a finite
        expiry. All of the target's sessions are revoked and a sanitized audit is
        committed in the same transaction. The actor must not target themselves
        (direct them to the self-change-password flow instead). The temp password
        is never persisted verbatim and never re-appears in queries/audit.
        """
        self._require_platform_admin(actor_user_id)
        self._require_recent_password(actor_user_id, recent_password)
        temp_password = generate_password()
        # Compute the expensive new-hash OUTSIDE the write lock (task 2.6).
        temp_hash = hash_password(temp_password)
        with self._tx() as con:
            actor = con.execute("SELECT * FROM users WHERE id=?", (actor_user_id,)).fetchone()
            if not actor or not actor["active"] or not actor["is_platform_admin"]:
                raise IdentityServiceError("forbidden", code="forbidden", status=403)
            if not verify_password(recent_password, actor["password_hash"]):
                raise IdentityServiceError("recent password required", code="invalid_old", status=401)
            target = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise IdentityServiceError("user not found", code="not_found", status=404)
            if target["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            if user_id == actor_user_id:
                raise IdentityServiceError(
                    "cannot reset your own account; use the change-password flow",
                    code="self_reset_forbidden", status=409)
            con.execute(
                "UPDATE users SET password_hash=?, must_change_password=1,"
                " temp_password_expires_at=?, version=version+1 WHERE id=?",
                (temp_hash, int(time.time()) + TEMPORARY_PASSWORD_TTL_SECONDS, user_id),
            )
            con.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (int(time.time()), user_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, actor_username=actor["username"],
                tenant_id=None, target_tenant_id=None, action="user.password.reset",
                target=f"user:{user_id}",
                redacted_changes={"must_change": True}, result="success")
            con.commit()
        # Only here, after commit, is the one-time temp password returned.
        return {"id": user_id, "temporary_password": temp_password,
                "must_change_password": True}

    def _check_platform_admin_continuity(self, con, *, actor_user_id, target,
                                         target_active, target_admin) -> None:
        """Ensure the instance keeps a valid, non-restricted platform admin.

        When the actor demotes or disables themselves, another *completed*
        password-change platform admin must remain; an admin who is still
        forced-password-change does not count as a usable fallback.
        """
        target_removes_admin = target["is_platform_admin"] and not target_admin
        actor_is_target = actor_user_id == target["id"]
        if not (target_removes_admin or (actor_is_target and not target_active)):
            return
        if target_removes_admin or (actor_is_target and not target_active):
            # Count other active, non-must_change platform admins after excluding
            # the target (and the actor if same).
            rows = con.execute(
                "SELECT id, active, is_platform_admin, must_change_password"
                " FROM users WHERE is_platform_admin=1 AND active=1"
            ).fetchall()
            valid_others = sum(
                1 for r in rows
                if r["id"] != target["id"] and not r["must_change_password"]
            )
            if valid_others < 1:
                raise IdentityServiceError(
                    "cannot remove the last completed platform admin",
                    code="last_admin", status=409)

    def _check_all_tenant_admin_continuity_for_user(self, con, user_id) -> None:
        """On global disable, every enabled tenant this user administers must
        retain another active tenant_admin."""
        rows = con.execute(
            "SELECT DISTINCT m.tenant_id FROM memberships m"
            " WHERE m.user_id=? AND m.active=1", (user_id,)
        ).fetchall()
        for r in rows:
            tid = r["tenant_id"]
            other = con.execute(
                "SELECT COUNT(*) c FROM memberships m"
                " JOIN users u ON u.id=m.user_id"
                " JOIN membership_roles mr ON mr.membership_id=m.id"
                " JOIN roles r2 ON r2.id=mr.role_id"
                " WHERE m.tenant_id=? AND m.active=1 AND u.active=1 AND r2.code=?"
                " AND m.user_id<>?", (tid, TENANT_ADMIN_CODE, user_id),
            ).fetchone()["c"]
            if other < 1:
                raise IdentityServiceError(
                    "disabling would leave a tenant without an admin",
                    code="last_admin", status=409)

    # --- platform users (task 3.2) ----------------------------------------

    def list_platform_users(self) -> List[Dict[str, Any]]:
        """List all platform accounts (whitelisted fields) for compatibility.

        Prefer ``list_platform_users_paged`` for UI/API uses that need search,
        status filtering and paging. This keeps the older list contract so the
        CLI and existing callers keep working unchanged.
        """
        rows = self._store.execute(
            "SELECT id, username, display_name, active, is_platform_admin,"
            " must_change_password, version FROM users ORDER BY username"
        )
        return [dict(r) for r in rows]

    def list_platform_users_paged(
        self,
        q: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """List platform accounts with search/status filter and paging.

        Only returns whitelisted management fields. ``status`` supports
        ``active`` / ``inactive`` / ``restricted`` (forced password change).
        Returns a filtered total and the requested page so the UI can page
        beyond 100 accounts.
        """
        if page_size > 100:
            page_size = 100
        where: List[str] = []
        params: List[Any] = []
        if q:
            where.append("(username LIKE ? OR display_name LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if status:
            if status == "active":
                where.append("active=1")
            elif status == "inactive":
                where.append("active=0")
            elif status == "restricted":
                where.append("must_change_password=1")
            else:
                raise IdentityServiceError(
                    "invalid status filter", code="bad_request", status=400)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        count_params = list(params)
        page_params = params + [page_size, (page - 1) * page_size]
        rows = self._store.execute(
            "SELECT id, username, display_name, active, is_platform_admin,"
            " must_change_password, version FROM users"
            f"{where_sql} ORDER BY username LIMIT ? OFFSET ?",
            page_params,
        )
        total = self._store.execute(
            f"SELECT COUNT(*) AS c FROM users{where_sql}", count_params
        )[0]["c"]
        return {"items": [dict(r) for r in rows], "total": total, "page": page}

    def _require_platform_admin(self, user_id: str) -> None:
        user = self._find_user_by_id(user_id)
        if not user or not user["active"] or not user["is_platform_admin"]:
            raise IdentityServiceError("forbidden", code="forbidden", status=403)

    def _require_recent_password(self, user_id: str, recent_password: str) -> None:
        user = self._find_user_by_id(user_id)
        if not user or not verify_password(recent_password, user["password_hash"]):
            raise IdentityServiceError("recent password required", code="invalid_old", status=401)

    # --- external identities (admin-bound IM -> account mapping) -----------

    def list_external_identities(
        self,
        *,
        user_id: Optional[str] = None,
        provider: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """List external identity bindings (admin-only, whitelisted fields)."""
        if page_size > 100:
            page_size = 100
        where: List[str] = []
        params: List[Any] = []
        if user_id:
            where.append("e.user_id=?")
            params.append(user_id)
        if provider:
            where.append("e.provider=?")
            params.append(provider)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        count_params = list(params)
        page_params = params + [page_size, (page - 1) * page_size]
        rows = self._store.execute(
            "SELECT e.id, e.user_id, e.provider, e.issuer, e.subject,"
            "       e.created_at, e.last_used_at,"
            "       u.username, u.display_name, u.active"
            " FROM external_identities e JOIN users u ON u.id = e.user_id"
            f"{where_sql}"
            " ORDER BY e.provider, e.issuer, e.subject LIMIT ? OFFSET ?",
            page_params,
        )
        total = self._store.execute(
            "SELECT COUNT(*) AS c FROM external_identities e" + where_sql,
            count_params,
        )[0]["c"]
        return {"items": [dict(r) for r in rows], "total": total, "page": page}

    def bind_external_identity(
        self,
        *,
        actor_user_id: str,
        user_id: str,
        provider: str,
        issuer: str,
        subject: str,
    ) -> Dict[str, Any]:
        """Bind an external identity triple to one active user (admin-only).

        The triple ``(provider, issuer, subject)`` is globally unique. The
        provider is normalized to lowercase; issuer/subject are trimmed but
        otherwise stored verbatim (subject may contain provider-specific id
        characters, including slashes). A second bind of the same triple raises
        a 409 ``conflict`` and never overwrites.
        """
        self._require_platform_admin(actor_user_id)
        provider = (provider or "").strip().lower()
        issuer = (issuer or "").strip()
        subject = (subject or "").strip()
        if not _PROVIDER_RE.fullmatch(provider):
            raise IdentityServiceError(
                "invalid provider", code="bad_request", status=400)
        if not subject or len(subject) > 512 or len(issuer) > 256:
            raise IdentityServiceError(
                "subject is required and issuer/subject are too long",
                code="bad_request", status=400)
        target = self._find_user_by_id(user_id)
        if not target:
            raise IdentityServiceError("user not found", code="not_found", status=404)
        if not target["active"]:
            raise IdentityServiceError("user is inactive", code="bad_request", status=400)
        actor = self._find_user_by_id(actor_user_id) or {}
        binding_id = self._new_id("ext")
        with self._tx() as con:
            try:
                con.execute(
                    "INSERT INTO external_identities"
                    " (id, user_id, provider, issuer, subject)"
                    " VALUES (?,?,?,?,?)",
                    (binding_id, user_id, provider, issuer, subject),
                )
            except sqlite3.IntegrityError:
                raise IdentityServiceError(
                    "external identity is already bound",
                    code="conflict", status=409)
            self._audit_in_tx(
                con,
                actor_user_id=actor_user_id,
                actor_username=actor.get("username"),
                action="external_identity.bind",
                target=f"user:{user_id}",
                redacted_changes={
                    "provider": provider, "issuer": issuer,
                    "subject": subject, "binding_id": binding_id,
                },
            )
        return {
            "id": binding_id, "user_id": user_id, "provider": provider,
            "issuer": issuer, "subject": subject,
        }

    def delete_external_identity(
        self, *, actor_user_id: str, binding_id: str
    ) -> None:
        """Delete a single external identity binding (admin-only).

        The binding disappears immediately; the next inbound resolve treats the
        triple as unbound.
        """
        self._require_platform_admin(actor_user_id)
        actor = self._find_user_by_id(actor_user_id) or {}
        with self._tx() as con:
            row = con.execute(
                "SELECT user_id, provider, issuer, subject"
                " FROM external_identities WHERE id=?",
                (binding_id,),
            ).fetchone()
            if not row:
                raise IdentityServiceError(
                    "external identity binding not found",
                    code="not_found", status=404)
            con.execute(
                "DELETE FROM external_identities WHERE id=?", (binding_id,))
            self._audit_in_tx(
                con,
                actor_user_id=actor_user_id,
                actor_username=actor.get("username"),
                action="external_identity.unbind",
                target=f"user:{row['user_id']}",
                redacted_changes={
                    "provider": row["provider"], "issuer": row["issuer"],
                    "subject": row["subject"], "binding_id": binding_id,
                },
            )

    def find_user_for_external_identity(
        self, provider: str, issuer: str, subject: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve an external identity triple to its bound active user.

        Read-only helper used by IM inbound paths: returns ``None`` when there
        is no binding or the bound user is inactive. The caller must still
        validate membership and permissions for the target tenant.
        """
        rows = self._store.execute(
            "SELECT u.id, u.username, u.display_name, u.active"
            " FROM external_identities e JOIN users u ON u.id = e.user_id"
            " WHERE e.provider=? AND e.issuer=? AND e.subject=?",
            ((provider or "").strip().lower(), (issuer or "").strip(),
             (subject or "").strip()),
        )
        if not rows:
            return None
        user = dict(rows[0])
        return user if user["active"] else None

    # --- memberships (task 3.3) -------------------------------------------

    def list_members(self, tenant_id: str, q: Optional[str] = None,
                     department_id: Optional[str] = None,
                     status: Optional[str] = None,
                     role: Optional[str] = None,
                     page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        if page_size > 100:
            page_size = 100
        where = ["m.tenant_id=?"]
        params: List[Any] = [tenant_id]
        if q:
            where.append("(u.username LIKE ? OR m.display_name LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if department_id:
            where.append("m.department_id=?")
            params.append(department_id)
        if status:
            if status == "active":
                where.append("m.active=1")
            elif status == "inactive":
                where.append("m.active=0")
            else:
                raise IdentityServiceError(
                    "invalid status filter", code="bad_request", status=400)
        if role:
            where.append(
                "m.id IN ("
                " SELECT am.membership_id FROM membership_roles am"
                " JOIN roles ar ON ar.id=am.role_id"
                " WHERE am.membership_id=m.id AND ar.code=?)"
            )
            params.append(role)
        count_where = " AND ".join(where)
        count_params = list(params)
        page_params = params + [page_size, (page - 1) * page_size]
        rows = self._store.execute(
            f"SELECT m.id, m.display_name, m.active, m.department_id, m.position_text,"
            f" m.version, u.username, u.id AS user_id"
            f" FROM memberships m JOIN users u ON u.id=m.user_id"
            f" WHERE {count_where} ORDER BY u.username LIMIT ? OFFSET ?",
            page_params,
        )
        total = self._store.execute(
            f"SELECT COUNT(*) AS c FROM memberships m JOIN users u ON u.id=m.user_id"
            f" WHERE {count_where}",
            count_params,
        )[0]["c"]
        # Attach role_codes so the edit form can echo the current bindings and
        # the frontend can detect "profile-only" edits without roles.
        items = []
        for r in rows:
            item = dict(r)
            item["role_codes"] = self._role_codes_for_membership(r["id"])
            items.append(item)
        return {"items": items, "total": total, "page": page}

    def create_member(
        self,
        *,
        actor_user_id: str,
        tenant_id: str,
        operation: str,
        username: str,
        display_name: str,
        temporary_password: str,
        roles: Sequence[str],
        department_id: Optional[str] = None,
        position_text: str = "",
    ) -> Dict[str, Any]:
        """Create a new account+membership or bind an existing user to this tenant."""
        self._require_tenant_admin(actor_user_id, tenant_id)
        if operation not in ("create-new", "bind-existing"):
            raise IdentityServiceError("invalid operation", code="invalid_operation")

        # Compute the expensive temp-password hash OUTSIDE the write lock.
        temp_hash = hash_password(temporary_password) if operation == "create-new" else None
        with self._tx() as con:
            existing_user = con.execute(
                "SELECT * FROM users WHERE username=?", (username.strip(),)
            ).fetchone()

            if operation == "bind-existing":
                if not existing_user or not existing_user["active"]:
                    raise IdentityServiceError("user not found", code="not_found", status=404)
                user_id = existing_user["id"]
                # must NOT change password or global status
            else:
                if existing_user:
                    raise IdentityServiceError("username already exists", code="conflict", status=409)
                if temporary_password.lower() in _COMMON_PASSWORDS or len(temporary_password) < MIN_PASSWORD_LENGTH:
                    raise IdentityServiceError("weak temporary password", code="weak_password")
                if not _USERNAME_RE.fullmatch(username.strip()):
                    raise IdentityServiceError("invalid username", code="invalid_username")
                user_id = self._new_id("usr")
                con.execute(
                    "INSERT INTO users(id, username, display_name, password_hash, active,"
                    " is_platform_admin, must_change_password, temp_password_expires_at, version)"
                    " VALUES (?,?,?,?,1,0,1,?,1)",
                    (user_id, username.strip(), display_name, temp_hash,
                     int(time.time()) + 86400 * 3),
                )

            # duplicate (tenant,user) - an inactive membership must be recovered
            membership = con.execute(
                "SELECT * FROM memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            ).fetchone()
            if membership:
                if membership["active"]:
                    raise IdentityServiceError("already a member", code="conflict", status=409)
                # recover inactive membership explicitly
                con.execute(
                    "UPDATE memberships SET active=1, display_name=?, version=version+1 WHERE id=?",
                    (display_name, membership["id"]),
                )
                membership_id = membership["id"]
            else:
                membership_id = self._new_id("mem")
                con.execute(
                    "INSERT INTO memberships(id, tenant_id, user_id, display_name, active,"
                    " department_id, position_text, version)"
                    " VALUES (?,?,?,?,1,?,?,1)",
                    (membership_id, tenant_id, user_id, display_name,
                     department_id, position_text),
                )

            # role bindings (default member unless specified)
            role_codes = roles or [MEMBER_CODE]
            con.execute("DELETE FROM membership_roles WHERE membership_id=?", (membership_id,))
            for code in role_codes:
                code = str(code).strip().lower()
                role = con.execute(
                    "SELECT * FROM roles WHERE tenant_id=? AND code=?",
                    (tenant_id, code),
                ).fetchone()
                if not role:
                    raise IdentityServiceError(f"unknown role: {code}", code="invalid_role", status=404)
                if code not in BUILTIN_ROLES and code not in BUILTIN_ROLES:
                    pass
                con.execute(
                    "INSERT OR REPLACE INTO membership_roles(membership_id, role_id) VALUES (?,?)",
                    (membership_id, role["id"]),
                )

            self._audit_in_tx(
                con, actor_user_id=actor_user_id, actor_username=None,
                tenant_id=tenant_id, target_tenant_id=tenant_id,
                action="member.create" if operation == "create-new" else "member.bind",
                target=f"membership:{membership_id}",
                redacted_changes={"username": username, "op": operation},
                result="success")
            con.commit()

        return {"membership_id": membership_id, "user_id": user_id}

    def update_member(
        self,
        *,
        actor_user_id: str,
        tenant_id: str,
        member_id: str,
        display_name: str,
        active: bool,
        roles: Optional[Sequence[str]] = None,
        department_id: Optional[str],
        position_text: str,
        expected_version: int,
    ) -> Dict[str, Any]:
        """Whole-object member update: name, status, roles, department, position.

        ``roles`` semantics: ``None`` preserves the existing role bindings
        (a profile-only edit), an explicit empty list is rejected (a member must
        hold at least one role), and a non-empty list replaces the bindings.
        """
        self._require_tenant_admin(actor_user_id, tenant_id)
        with self._tx() as con:
            membership = con.execute(
                "SELECT * FROM memberships WHERE id=? AND tenant_id=?",
                (member_id, tenant_id),
            ).fetchone()
            if not membership:
                raise IdentityServiceError("member not found", code="not_found", status=404)
            if membership["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)

            # department must be same-tenant active
            if department_id:
                dept = con.execute(
                    "SELECT * FROM departments WHERE id=? AND tenant_id=? AND active=1",
                    (department_id, tenant_id),
                ).fetchone()
                if not dept:
                    raise IdentityServiceError("invalid department", code="invalid_dept", status=404)

            con.execute(
                "UPDATE memberships SET display_name=?, active=?, department_id=?,"
                " position_text=?, version=version+1 WHERE id=?",
                (display_name, int(active), department_id, position_text, member_id),
            )
            # role bindings (None == preserve, [] == denied, non-empty == replace)
            admin_role_count = 0
            was_admin = con.execute(
                "SELECT COUNT(*) c FROM membership_roles mr JOIN roles r ON r.id=mr.role_id"
                " WHERE mr.membership_id=? AND r.code=?",
                (member_id, TENANT_ADMIN_CODE),
            ).fetchone()["c"] > 0
            if roles is not None:
                if len(roles) == 0:
                    raise IdentityServiceError(
                        "a member must hold at least one role", code="invalid_role", status=400)
                con.execute("DELETE FROM membership_roles WHERE membership_id=?", (member_id,))
                role_codes = [str(c).strip().lower() for c in roles]
                for code in role_codes:
                    role = con.execute(
                        "SELECT * FROM roles WHERE tenant_id=? AND code=?", (tenant_id, code)
                    ).fetchone()
                    if not role:
                        raise IdentityServiceError(f"unknown role: {code}", code="invalid_role", status=404)
                    if code == TENANT_ADMIN_CODE:
                        admin_role_count += 1
                    con.execute(
                        "INSERT OR REPLACE INTO membership_roles(membership_id, role_id) VALUES (?,?)",
                        (member_id, role["id"]),
                    )
            else:
                # Preserve existing bindings; recompute whether this member is an
                # admin from the current bindings.
                role_codes = self._role_codes_for_membership(member_id)
                admin_role_count = 1 if was_admin else 0

            # admin continuity: if this member is (or was) an admin, ensure at least one
            # active admin remains after this change.
            self._check_admin_continuity(con, tenant_id=tenant_id, exclude_membership_id=member_id,
                                         was_admin=was_admin, now_admin=admin_role_count > 0,
                                         member_active=active)
            # member→tenant continuity: disabling the last active membership of
            # an enabled account must be rejected (before the audit/commit).
            if not active:
                self._require_active_tenant_for_enabled_user(con, membership["user_id"])

            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="member.update",
                target=f"membership:{member_id}",
                redacted_changes={"display_name": display_name, "active": active,
                                  "roles": role_codes},
                result="success")
            con.commit()
        return {"id": member_id, "active": active, "version": membership["version"] + 1}

    def _check_admin_continuity(self, con, *, tenant_id, exclude_membership_id,
                                was_admin, now_admin, member_active) -> None:
        """Ensure no active tenant loses its last active tenant_admin."""
        is_admin_role_now = now_admin and member_active
        # Count active admins OTHER than this membership
        other_admin_count = con.execute(
            "SELECT COUNT(*) c FROM memberships m"
            " JOIN users u ON u.id=m.user_id"
            " JOIN membership_roles mr ON mr.membership_id=m.id"
            " JOIN roles r ON r.id=mr.role_id"
            " WHERE m.tenant_id=? AND m.active=1 AND u.active=1 AND r.code=?"
            " AND m.id<>?",
            (tenant_id, TENANT_ADMIN_CODE, exclude_membership_id),
        ).fetchone()["c"]
        if other_admin_count == 0 and not is_admin_role_now:
            raise IdentityServiceError(
                "cannot remove the last tenant admin", code="last_admin", status=409)

    # --- roles (task 3.4) --------------------------------------------------

    def list_roles(self, tenant_id: str) -> List[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT id, code, name, builtin, permissions_json, model_defaults_json, version FROM roles"
            " WHERE tenant_id=? ORDER BY builtin DESC, code",
            (tenant_id,),
        )
        return [
            {**dict(r), "permissions": json.loads(r["permissions_json"] or "[]"),
             "resource_grants": self._role_grants(r["id"]),
             "model_defaults": json.loads(r["model_defaults_json"] or "{}")}
            for r in rows
        ]

    def _role_grants(self, role_id: str) -> List[Dict[str, str]]:
        rows = self._store.execute(
            "SELECT resource_kind, resource_id, action FROM role_resource_grants"
            " WHERE role_id=? ORDER BY resource_kind, resource_id, action",
            (role_id,),
        )
        return [dict(r) for r in rows]

    #: A role's write payload is unified: permissions + resource grants + model
    #: defaults. ``None``/omitted field means *preserve*, but list/dict fields only
    #: accept an explicit value; an explicit empty value clears. This helper
    #: normalizes the optional fields so the caller can pass ``None`` to keep.
    @staticmethod
    def _coalesce_field(requested, current):
        return current if requested is None else requested

    def create_role(self, actor_user_id: str, tenant_id: str, code: str, name: str,
                    permissions: Sequence[str],
                    resource_grants: Sequence[Dict[str, Any]] = (),
                    model_defaults: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Create a custom role with permissions, resource grants and model defaults.

        Resource grants and model defaults are validated against the tenant's
        allocatable set in the same transaction; any invalid/missing/unallocatable
        reference rejects the whole create (no partial role). ``model_defaults``
        defaults to ``None`` (empty) and never grants model use by itself.
        """
        self._require_tenant_admin(actor_user_id, tenant_id)
        code = code.strip().lower()
        if not _ROLE_CODE_RE.fullmatch(code):
            raise IdentityServiceError("invalid role code", code="invalid_code")
        if code in BUILTIN_ROLES:
            raise IdentityServiceError("built-in role cannot be recreated", code="forbidden", status=403)
        perms = normalize_permissions(permissions)
        grants = normalize_resource_grants(resource_grants or [])
        defaults = validate_model_defaults(model_defaults or {})
        self._validate_model_defaults_against_grants(defaults, grants)
        for existing_code in BUILTIN_ROLES:
            if existing_code == code:
                raise IdentityServiceError("built-in role conflict", code="conflict", status=409)
        role_id = self._new_id("role")
        with self._tx() as con:
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json,"
                " model_defaults_json, version) VALUES (?,?,?,?,0,?,?,1)",
                (role_id, tenant_id, code, name, json.dumps(sorted(perms)),
                 json.dumps(defaults) if defaults else None),
            )
            self._insert_grants_tx(con, role_id, grants)
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="role.create",
                              target=f"role:{role_id}",
                              redacted_changes={"code": code, "permissions": sorted(perms),
                                                "resource_kind_ids": self._grant_kinds(grants)},
                              result="success")
            con.commit()
        return {"id": role_id, "code": code, "name": name, "permissions": sorted(perms),
                "resource_grants": grants, "model_defaults": defaults, "version": 1}

    def update_role(self, actor_user_id: str, tenant_id: str, role_id: str,
                    name: str, permissions: Sequence[str], expected_version: int,
                    resource_grants: Optional[Sequence[Dict[str, Any]]] = None,
                    model_defaults: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Update a custom role, optionally replacing grants/model defaults.

        ``resource_grants``/``model_defaults`` omitted (None) preserve existing;
        an explicit list/dict replaces (empty clears). The whole write is one
        transaction: permissions, grants and defaults either all land or none do.
        """
        self._require_tenant_admin(actor_user_id, tenant_id)
        perms = normalize_permissions(permissions)
        with self._tx() as con:
            row = con.execute("SELECT * FROM roles WHERE id=? AND tenant_id=?", (role_id, tenant_id)).fetchone()
            if not row:
                raise IdentityServiceError("role not found", code="not_found", status=404)
            if row["builtin"]:
                raise IdentityServiceError("built-in role cannot be modified", code="forbidden", status=403)
            if row["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)
            current_grants = self._role_grants(role_id)
            new_grants = current_grants if resource_grants is None else normalize_resource_grants(resource_grants or [])
            current_model_defaults = dict(row["model_defaults_json"] and json.loads(row["model_defaults_json"]) or {})
            new_defaults = current_model_defaults if model_defaults is None else validate_model_defaults(model_defaults or {})
            self._validate_model_defaults_against_grants(new_defaults, new_grants)
            con.execute(
                "UPDATE roles SET name=?, permissions_json=?, model_defaults_json=?, version=version+1 WHERE id=?",
                (name, json.dumps(sorted(perms)), json.dumps(new_defaults) if new_defaults else None, role_id),
            )
            self._replace_grants_tx(con, role_id, new_grants)
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="role.update",
                              target=f"role:{role_id}",
                              redacted_changes={"name": name, "resource_kind_ids": self._grant_kinds(new_grants)},
                              result="success")
            con.commit()
        return {"id": role_id, "name": name, "permissions": sorted(perms),
                "resource_grants": new_grants, "model_defaults": new_defaults}

    @staticmethod
    def _validate_model_defaults_against_grants(defaults, grants) -> None:
        """Each default model must already be granted ``model.use`` for the role.

        The default is a *preference* within the allowed set — it never grants
        model use by itself. A default that the role is not allowed to use is
        rejected here (spec 5.1), so a partial/contradictory save never lands.
        """
        if not defaults:
            return
        use_ids = resource_ids_for(grants, "model", "use")
        for cap, model_id in defaults.items():
            if model_id not in use_ids:
                raise IdentityServiceError(
                    f"default model for {cap!r} is not in the role's model.use set",
                    code="default_model_not_granted", status=409)

    @staticmethod
    def _grant_kinds(grants) -> List[str]:
        return sorted({g["resource_kind"] for g in grants})

    def _insert_grants_tx(self, con, role_id: str, grants: List[Dict[str, str]]) -> None:
        for g in grants:
            con.execute(
                "INSERT INTO role_resource_grants(id, role_id, resource_kind, resource_id, action)"
                " VALUES (?,?,?,?,?)",
                (self._new_id("grant"), role_id, g["resource_kind"], g["resource_id"], g["action"]),
            )

    def _replace_grants_tx(self, con, role_id: str, grants: List[Dict[str, str]]) -> None:
        con.execute("DELETE FROM role_resource_grants WHERE role_id=?", (role_id,))
        self._insert_grants_tx(con, role_id, grants)

    def delete_role(self, actor_user_id: str, tenant_id: str, role_id: str) -> Dict[str, Any]:
        self._require_tenant_admin(actor_user_id, tenant_id)
        with self._tx() as con:
            row = con.execute("SELECT * FROM roles WHERE id=? AND tenant_id=?", (role_id, tenant_id)).fetchone()
            if not row:
                raise IdentityServiceError("role not found", code="not_found", status=404)
            if row["builtin"]:
                raise IdentityServiceError("built-in role cannot be deleted", code="forbidden", status=403)
            in_use = con.execute(
                "SELECT COUNT(*) AS c FROM membership_roles WHERE role_id=?", (role_id,)
            ).fetchone()["c"]
            if in_use:
                raise IdentityServiceError("role is in use", code="in_use", status=409)
            con.execute("DELETE FROM roles WHERE id=?", (role_id,))
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="role.delete",
                              target=f"role:{role_id}", redacted_changes={}, result="success")
            con.commit()
        return {"id": role_id, "deleted": True}

    # --- departments (task 3.5) -------------------------------------------

    def _require_tenant_admin(self, actor_user_id: str, tenant_id: str) -> None:
        # A platform admin may manage a target tenant's roles/resource grants via
        # the explicit platform-management surface without being a member of that
        # tenant (task 2.3 / 3.2). No Membership is forged; the caller still holds
        # platform-admin qualification.
        if self.is_platform_admin(actor_user_id):
            return
        if not self._is_tenant_admin(actor_user_id, tenant_id):
            raise IdentityServiceError("forbidden", code="forbidden", status=403)

    def _assert_no_cycle(self, con, tenant_id: str, dept_id: str, new_parent_id: Optional[str]) -> None:
        # walk upward from new_parent; if we hit dept_id, it's a cycle
        current = new_parent_id
        seen = set()
        while current:
            if current == dept_id:
                raise IdentityServiceError("cycle detected", code="cycle", status=409)
            if current in seen:
                break
            seen.add(current)
            row = con.execute(
                "SELECT parent_id FROM departments WHERE id=? AND tenant_id=?",
                (current, tenant_id),
            ).fetchone()
            if not row:
                break
            current = row["parent_id"]

    def list_departments(self, tenant_id: str) -> List[Dict[str, Any]]:
        rows = self._store.execute(
            "SELECT * FROM departments WHERE tenant_id=? ORDER BY sort_order, name",
            (tenant_id,),
        )
        return [dict(r) for r in rows]

    def create_department(self, actor_user_id: str, tenant_id: str, code: str, name: str,
                          parent_id: Optional[str], sort_order: int = 0) -> Dict[str, Any]:
        self._require_tenant_admin(actor_user_id, tenant_id)
        dept_id = self._new_id("dept")
        with self._tx() as con:
            parent = con.execute(
                "SELECT * FROM departments WHERE id=? AND tenant_id=? AND active=1",
                (parent_id, tenant_id),
            ).fetchone() if parent_id else None
            if parent_id and not parent:
                raise IdentityServiceError("invalid parent", code="invalid_parent", status=404)
            con.execute(
                "INSERT INTO departments(id, tenant_id, parent_id, code, name, sort_order, active, version)"
                " VALUES (?,?,?,?,?,?,1,1)",
                (dept_id, tenant_id, parent_id, code, name, sort_order),
            )
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="department.create",
                              target=f"dept:{dept_id}",
                              redacted_changes={"code": code, "name": name}, result="success")
            con.commit()
        return {"id": dept_id, "code": code, "name": name, "version": 1}

    def update_department(
        self, actor_user_id: str, tenant_id: str, dept_id: str, *,
        name: Optional[str] = None, code: Optional[str] = None,
        sort_order: Optional[int] = None, active: Optional[bool] = None,
        parent_id: Optional[str] = None, expected_version: int,
    ) -> Dict[str, Any]:
        """Update a department (rename, re-code, re-sort, deactivate, or move).

        A move is validated in the same transaction against the tenant tree so
        no cycle is introduced and the new parent (when set) is a same-tenant
        active department. Deactivation is only allowed for a leaf that has no
        members; the virtual ``__root__`` node is immutable.
        """
        self._require_tenant_admin(actor_user_id, tenant_id)
        with self._tx() as con:
            dept = con.execute(
                "SELECT * FROM departments WHERE id=? AND tenant_id=?", (dept_id, tenant_id)
            ).fetchone()
            if not dept:
                raise IdentityServiceError("department not found", code="not_found", status=404)
            if dept["code"] == "__root__" and (name is not None or code is not None
                                               or sort_order is not None or active is not None
                                               or parent_id is not None):
                raise IdentityServiceError("virtual root is immutable", code="forbidden", status=403)
            if dept["version"] != expected_version:
                raise IdentityServiceError("version conflict", code="conflict", status=409)

            # Resolve the effective parent: moving to None means root, moving to a
            # value must reference a same-tenant active department (re-rooted trees
            # under __root__ have parent_id == __root__ id which is valid).
            eff_parent = dept["parent_id"] if parent_id is None else parent_id
            if parent_id is not None and parent_id != dept["parent_id"]:
                if parent_id:
                    new_parent = con.execute(
                        "SELECT * FROM departments WHERE id=? AND tenant_id=? AND active=1",
                        (parent_id, tenant_id),
                    ).fetchone()
                    if not new_parent:
                        raise IdentityServiceError("invalid parent", code="invalid_parent", status=404)
                self._assert_no_cycle(con, tenant_id, dept_id, parent_id)

            # Deactivation guard: an active dept with members or children cannot
            # be deactivated in place (caller must detach references first).
            if active is False and dept["active"]:
                members = con.execute(
                    "SELECT COUNT(*) c FROM memberships WHERE department_id=?", (dept_id,)
                ).fetchone()["c"]
                if members:
                    raise IdentityServiceError(
                        "department has members", code="in_use", status=409)

            new_name = dept["name"] if name is None else name
            new_code = dept["code"] if code is None else code
            new_sort = dept["sort_order"] if sort_order is None else sort_order
            new_active = dept["active"] if active is None else active
            con.execute(
                "UPDATE departments SET name=?, code=?, sort_order=?, active=?, parent_id=?,"
                " version=version+1 WHERE id=?",
                (new_name, new_code, new_sort, int(new_active), eff_parent, dept_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="department.update",
                target=f"dept:{dept_id}",
                redacted_changes={"name": new_name, "code": new_code,
                                  "sort_order": new_sort, "active": new_active},
                result="success")
            con.commit()
        return {"id": dept_id, "version": dept["version"] + 1,
                "name": new_name, "code": new_code, "sort_order": new_sort,
                "active": new_active, "parent_id": eff_parent}

    def delete_department(self, actor_user_id: str, tenant_id: str, dept_id: str) -> Dict[str, Any]:
        """Delete a department only if it has no children or members referencing it."""
        self._require_tenant_admin(actor_user_id, tenant_id)
        with self._tx() as con:
            dept = con.execute(
                "SELECT * FROM departments WHERE id=? AND tenant_id=?", (dept_id, tenant_id)
            ).fetchone()
            if not dept:
                raise IdentityServiceError("department not found", code="not_found", status=404)
            if dept["code"] == "__root__":
                raise IdentityServiceError("virtual root cannot be deleted", code="forbidden", status=403)
            children = con.execute(
                "SELECT COUNT(*) c FROM departments WHERE parent_id=?", (dept_id,)
            ).fetchone()["c"]
            members = con.execute(
                "SELECT COUNT(*) c FROM memberships WHERE department_id=?", (dept_id,)
            ).fetchone()["c"]
            if children or members:
                raise IdentityServiceError("department is in use", code="in_use", status=409)
            con.execute("DELETE FROM departments WHERE id=?", (dept_id,))
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="department.delete",
                target=f"dept:{dept_id}", redacted_changes={}, result="success")
            con.commit()
        return {"id": dept_id, "deleted": True}

    # --- audit query (task 2.6) -------------------------------------------

    def list_audit(self, tenant_id: Optional[str], actor_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query audit per tenant, or platform-wide when no tenant & no actor."""
        if tenant_id:
            return self._audit.query_tenant(tenant_id)
        if actor_id:
            rows = self._store.execute(
                "SELECT * FROM audit_events WHERE actor_user_id=? ORDER BY time DESC LIMIT 100",
                (actor_id,),
            )
            return [dict(r) for r in rows]
        # platform-wide (platform admin) sees every event, newest first.
        rows = self._store.execute(
            "SELECT * FROM audit_events ORDER BY time DESC LIMIT 100"
        )
        return [dict(r) for r in rows]

    def list_audit_paged(
        self,
        tenant_id: Optional[str],
        *,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        result: Optional[str] = None,
        actor_username: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Query audit per tenant (or platform-wide) with filters and paging.

        Authorization is enforced by the caller (platform admin sees all; a
        current tenant_admin is scoped to its tenant); this method only applies
        the scoping + filters + paging. ``action``/``result``/``actor_username``
        are safe, non-secret filters; ``since``/``until`` bound the event time
        (unix seconds). Returns a filtered total and the requested page.
        """
        if page_size > 100:
            page_size = 100
        where: List[str] = []
        params: List[Any] = []
        if tenant_id:
            where.append("tenant_id=?")
            params.append(tenant_id)
        elif actor_id:
            where.append("actor_user_id=?")
            params.append(actor_id)
        if action:
            where.append("action=?")
            params.append(action)
        if result:
            where.append("result=?")
            params.append(result)
        if actor_username:
            where.append("actor_username LIKE ?")
            params.append(f"%{actor_username}%")
        if since is not None:
            where.append("time>=?")
            params.append(since)
        if until is not None:
            where.append("time<=?")
            params.append(until)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        count_params = list(params)
        page_params = params + [page_size, (page - 1) * page_size]
        rows = self._store.execute(
            "SELECT * FROM audit_events" + where_sql + " ORDER BY time DESC LIMIT ? OFFSET ?",
            page_params,
        )
        total = self._store.execute(
            "SELECT COUNT(*) AS c FROM audit_events" + where_sql, count_params
        )[0]["c"]
        return {"items": [dict(r) for r in rows], "total": total, "page": page}

    # --- credentials (open-database-runtime 7.x) --------------------------

    def _is_control(self, actor_user_id: str, tenant_id: str) -> bool:
        """True when ``actor_user_id`` may manage this tenant's control plane
        (platform admin, or an active tenant_admin member of the tenant)."""
        user = self._store.execute(
            "SELECT is_platform_admin, active FROM users WHERE id=?",
            (actor_user_id,),
        )
        if not user or not user[0]["active"]:
            return False
        if user[0]["is_platform_admin"]:
            return True
        rows = self._store.execute(
            "SELECT COUNT(*) c FROM memberships m"
            " JOIN membership_roles mr ON mr.membership_id=m.id"
            " JOIN roles r ON r.id=mr.role_id"
            " WHERE m.user_id=? AND m.tenant_id=? AND m.active=1 AND r.code=?",
            (actor_user_id, tenant_id, TENANT_ADMIN_CODE),
        )
        return rows[0]["c"] > 0

    def _member_active(self, user_id: str, tenant_id: str) -> bool:
        rows = self._store.execute(
            "SELECT COUNT(*) c FROM memberships m JOIN users u ON u.id=m.user_id"
            " WHERE m.user_id=? AND m.tenant_id=? AND m.active=1 AND u.active=1",
            (user_id, tenant_id),
        )
        return rows[0]["c"] > 0

    def _credential_eligible(self, actor_user_id: str, tenant_id: str) -> bool:
        """Use-time eligibility: a controller, or an active member holding the
        functional ``credential.use`` permission."""
        if self._is_control(actor_user_id, tenant_id):
            return True
        if not self._member_active(actor_user_id, tenant_id):
            return False
        try:
            perms = self.permissions_for(actor_user_id, tenant_id)
        except Exception:
            return False
        return "credential.use" in (perms or ())

    def create_credential(
        self,
        *,
        actor_user_id: str,
        tenant_id: str,
        name: str,
        secret: str,
        resource_kind: str = "",
        resource_id: str = "",
    ) -> Dict[str, Any]:
        """Store an encrypted external credential for a tenant/resource."""
        from auth.crypto import encrypt_secret

        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("credential manage denied", code="forbidden", status=403)
        name = (name or "").strip()
        if not name:
            raise IdentityServiceError("credential name required", code="invalid", status=400)
        try:
            ciphertext = encrypt_secret(secret)
        except Exception as error:
            from common.log import logger
            logger.error(f"[Identity] credential encrypt unavailable: {error}")
            raise IdentityServiceError(
                "credential encryption unavailable", code="credential_crypto", status=500) from error
        credential_id = self._new_id("cred")
        with self._tx() as con:
            dup = con.execute(
                "SELECT 1 FROM credentials WHERE tenant_id=? AND name=? AND active=1",
                (tenant_id, name),
            ).fetchone()
            if dup:
                raise IdentityServiceError("credential name exists", code="conflict", status=409)
            con.execute(
                "INSERT INTO credentials(id, tenant_id, name, resource_kind, resource_id,"
                " ciphertext, active, version, created_by)"
                " VALUES (?,?,?,?,?,?,1,1,?)",
                (credential_id, tenant_id, name, resource_kind, resource_id, ciphertext,
                 actor_user_id),
            )
            con.execute(
                "INSERT INTO credential_versions(credential_id, version, ciphertext, action,"
                " changed_by) VALUES (?,1,?,?,?)",
                (credential_id, ciphertext, "create", actor_user_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="credential.create",
                target=f"credential:{credential_id}",
                redacted_changes={"name": name, "resource_kind": resource_kind,
                                  "resource_id": resource_id},
                result="success")
            con.commit()
        return {"id": credential_id, "name": name, "resource_kind": resource_kind,
                "resource_id": resource_id, "active": True, "version": 1}

    def list_credentials(
        self, *, actor_user_id: str, tenant_id: str, page: int = 1, page_size: int = 100
    ) -> Dict[str, Any]:
        """Masked projection of a tenant's credentials (never plaintext)."""
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("credential list denied", code="forbidden", status=403)
        if page_size > 100:
            page_size = 100
        rows = self._store.execute(
            "SELECT id, name, resource_kind, resource_id, active, version,"
            " created_at, updated_at FROM credentials WHERE tenant_id=?"
            " ORDER BY name LIMIT ? OFFSET ?",
            (tenant_id, page_size, (page - 1) * page_size),
        )
        total = self._store.execute(
            "SELECT COUNT(*) c FROM credentials WHERE tenant_id=?", (tenant_id,)
        )[0]["c"]
        items = []
        for row in rows:
            item = dict(row)
            # Display-only mask derived from the label; plaintext is never
            # produced here, so nothing to redact.
            item["masked"] = f"{row['name']}••••"
            item["active"] = bool(row["active"])
            items.append(item)
        return {"items": items, "total": total, "page": page}

    def resolve_credential(
        self,
        *,
        actor_user_id: str,
        tenant_id: str,
        name: str,
        resource_kind: str = "",
        resource_id: str = "",
    ) -> str:
        """Decrypt a credential at a use point after identity revalidation.

        The caller must independently hold the resource grant; this method
        verifies tenant ownership, active state, and the functional
        ``credential.use`` permission/controller bypass, then returns the
        plaintext exactly once (never logged, never cached here).
        """
        from auth.crypto import decrypt_secret

        if not self._credential_eligible(actor_user_id, tenant_id):
            raise IdentityServiceError("credential use denied", code="forbidden", status=403)
        rows = self._store.execute(
            "SELECT id, tenant_id, ciphertext, active, version, resource_kind, resource_id"
            " FROM credentials WHERE tenant_id=? AND name=?",
            (tenant_id, name),
        )
        if not rows or not rows[0]["active"]:
            raise IdentityServiceError("credential not found", code="not_found", status=404)
        row = rows[0]
        if resource_kind and row["resource_kind"] and row["resource_kind"] != resource_kind:
            raise IdentityServiceError("credential resource mismatch", code="forbidden", status=403)
        if resource_id and row["resource_id"] and row["resource_id"] != resource_id:
            raise IdentityServiceError("credential resource mismatch", code="forbidden", status=403)
        try:
            return decrypt_secret(row["ciphertext"])
        except Exception as error:
            from common.log import logger
            logger.error(f"[Identity] credential '{row['id']}' decrypt failed")
            raise IdentityServiceError("credential decrypt failed", code="credential_crypto",
                                       status=500) from error

    def rotate_credential(
        self, *, actor_user_id: str, tenant_id: str, name: str, new_secret: str
    ) -> Dict[str, Any]:
        """Rotate a credential: previous ciphertext versions are preserved for
        audit but the new value is the only decryptable one."""
        from auth.crypto import encrypt_secret

        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("credential rotate denied", code="forbidden", status=403)
        ciphertext = encrypt_secret(new_secret)
        with self._tx() as con:
            row = con.execute(
                "SELECT id, ciphertext, version FROM credentials"
                " WHERE tenant_id=? AND name=? AND active=1",
                (tenant_id, name),
            ).fetchone()
            if not row:
                raise IdentityServiceError("credential not found", code="not_found", status=404)
            old_version = row["version"]
            next_version = con.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 v FROM credential_versions"
                " WHERE credential_id=?", (row["id"],),
            ).fetchone()["v"]
            # History row: the retired value is no longer the decryptable one,
            # but stays for audit; only the credentials.ciphertext slot (now
            # holding the new value) is ever resolved.
            con.execute(
                "INSERT INTO credential_versions(credential_id, version, ciphertext,"
                " action, changed_by) VALUES (?,?,?,?,?)",
                (row["id"], next_version, ciphertext, "rotated", actor_user_id),
            )
            con.execute(
                "UPDATE credentials SET ciphertext=?, version=?,"
                " updated_at=unixepoch() WHERE id=?",
                (ciphertext, next_version, row["id"]),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="credential.rotate",
                target=f"credential:{row['id']}", redacted_changes={}, result="success")
            con.commit()
            version = next_version
        return {"id": row["id"], "version": version, "active": True}

    def revoke_credential(self, *, actor_user_id: str, tenant_id: str, name: str) -> bool:
        """Revoke a credential: next use fails immediately (active=0)."""
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("credential revoke denied", code="forbidden", status=403)
        with self._tx() as con:
            row = con.execute(
                "SELECT id, ciphertext, version FROM credentials"
                " WHERE tenant_id=? AND name=? AND active=1",
                (tenant_id, name),
            ).fetchone()
            if not row:
                raise IdentityServiceError("credential not found", code="not_found", status=404)
            next_version = con.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 v FROM credential_versions"
                " WHERE credential_id=?", (row["id"],),
            ).fetchone()["v"]
            con.execute(
                "INSERT INTO credential_versions(credential_id, version, ciphertext,"
                " action, changed_by) VALUES (?,?,?,?,?)",
                (row["id"], next_version, row["ciphertext"], "revoked", actor_user_id),
            )
            con.execute(
                "UPDATE credentials SET active=0, version=?, updated_at=unixepoch()"
                " WHERE id=?",
                (next_version, row["id"]),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="credential.revoke",
                target=f"credential:{row['id']}", redacted_changes={}, result="success")
            con.commit()
        return True

    # --- approvals (open-database-runtime 8.x) -----------------------------

    def request_approval(
        self,
        *,
        actor_user_id: str,
        tenant_id: str,
        agent_id: str,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        expires_in_s: int = 1800,
    ) -> Dict[str, Any]:
        """Register a high-risk external side-effect action as pending.

        No side effect runs from here: a decision by another qualified user is
        required first (see :meth:`decide_approval`).
        """
        if not self._member_active(actor_user_id, tenant_id):
            raise IdentityServiceError("not a tenant member", code="forbidden", status=403)
        approval_id = self._new_id("apr")
        action = (action or "").strip()[:64]
        if not action:
            raise IdentityServiceError("approval action required", code="invalid", status=400)
        import json
        import time as _time
        safe = {k: v for k, v in (payload or {}).items()
                if str(k).lower() not in {"token", "secret", "password", "authorization"}}
        payload_json = json.dumps(safe, ensure_ascii=False)
        now = int(_time.time())
        expires_at = now + max(60, min(86400, int(expires_in_s)))
        with self._tx() as con:
            con.execute(
                "INSERT INTO approvals(id, tenant_id, requester_user_id, agent_id, action,"
                " payload_json, status, expires_at, version)"
                " VALUES (?,?,?,?,?,?,'pending',?,1)",
                (approval_id, tenant_id, actor_user_id, agent_id or "", action,
                 payload_json, expires_at),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="approval.create",
                target=f"approval:{approval_id}",
                redacted_changes={"action": action, "agent_id": agent_id},
                result="success")
            con.commit()
        return {"id": approval_id, "status": "pending", "action": action,
                "expires_at": expires_at}

    def decide_approval(
        self, *, actor_user_id: str, tenant_id: str, approval_id: str,
        approve: bool, note: str = "",
    ) -> Dict[str, Any]:
        """Approve or deny a pending approval (qualified, non-requester only)."""
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("approval decide denied", code="forbidden", status=403)
        import time as _time
        now = int(_time.time())
        with self._tx() as con:
            row = con.execute(
                "SELECT * FROM approvals WHERE id=? AND tenant_id=?",
                (approval_id, tenant_id),
            ).fetchone()
            if not row:
                raise IdentityServiceError("approval not found", code="not_found", status=404)
            if str(row["requester_user_id"]) == actor_user_id:
                raise IdentityServiceError("self approval denied", code="forbidden", status=403)
            if row["status"] == "expired" or (row["expires_at"] and now > row["expires_at"]):
                con.execute(
                    "UPDATE approvals SET status='expired', decided_at=? WHERE id=?",
                    (now, approval_id),
                )
                con.commit()
                raise IdentityServiceError("approval expired", code="expired", status=409)
            if row["status"] != "pending":
                raise IdentityServiceError(
                    f"approval already {row['status']}", code="conflict", status=409)
            status = "approved" if approve else "denied"
            con.execute(
                "UPDATE approvals SET status=?, decision_by=?, decision_note=?,"
                " decided_at=?, version=version+1 WHERE id=?",
                (status, actor_user_id, (note or "")[:256], now, approval_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id,
                action=f"approval.{'approve' if approve else 'deny'}",
                target=f"approval:{approval_id}",
                redacted_changes={"note": (note or "")[:256]}, result="success")
            con.commit()
        return {"id": approval_id, "status": status}

    def cancel_approval(
        self, *, actor_user_id: str, tenant_id: str, approval_id: str,
    ) -> Dict[str, Any]:
        """A requester withdraws their own *pending* request (撤销).

        Only the requester may cancel, and only while the request is still
        pending — a decided/expired approval is immutable.
        """
        import time as _time
        now = int(_time.time())
        with self._tx() as con:
            row = con.execute(
                "SELECT requester_user_id, status FROM approvals"
                " WHERE id=? AND tenant_id=?",
                (approval_id, tenant_id),
            ).fetchone()
            if not row:
                raise IdentityServiceError("approval not found", code="not_found", status=404)
            if str(row["requester_user_id"]) != actor_user_id:
                raise IdentityServiceError("approval cancel denied", code="forbidden", status=403)
            if row["status"] != "pending":
                raise IdentityServiceError(
                    f"approval already {row['status']}", code="conflict", status=409)
            con.execute(
                "UPDATE approvals SET status='revoked', decision_by=?,"
                " decision_note='cancelled by requester', decided_at=?, version=version+1"
                " WHERE id=?",
                (actor_user_id, now, approval_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="approval.cancel",
                target=f"approval:{approval_id}", redacted_changes={}, result="success")
            con.commit()
        return {"id": approval_id, "status": "revoked"}

    def revoke_approval(
        self, *, actor_user_id: str, tenant_id: str, approval_id: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """A controller supersedes an *approved* approval before the side
        effect runs (撤销). The executor must re-check status right before the
        external action, so a revoked approval never fires.
        """
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("approval revoke denied", code="forbidden", status=403)
        import time as _time
        now = int(_time.time())
        with self._tx() as con:
            row = con.execute(
                "SELECT requester_user_id, status FROM approvals"
                " WHERE id=? AND tenant_id=?",
                (approval_id, tenant_id),
            ).fetchone()
            if not row:
                raise IdentityServiceError("approval not found", code="not_found", status=404)
            if row["status"] != "approved":
                raise IdentityServiceError(
                    f"only approved approvals can be revoked (status={row['status']})",
                    code="conflict", status=409)
            con.execute(
                "UPDATE approvals SET status='revoked', decision_by=?,"
                " decision_note=?, decided_at=?, version=version+1 WHERE id=?",
                (actor_user_id, (note or "")[:256], now, approval_id),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="approval.revoke",
                target=f"approval:{approval_id}",
                redacted_changes={"note": (note or "")[:256]}, result="success")
            con.commit()
        return {"id": approval_id, "status": "revoked"}

    def list_approvals(
        self, *, actor_user_id: str, tenant_id: str, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Qualified users see the tenant's approvals; members see only their
        own requests."""
        if self._is_control(actor_user_id, tenant_id):
            if status:
                rows = self._store.execute(
                    "SELECT * FROM approvals WHERE tenant_id=? AND status=? ORDER BY created_at DESC",
                    (tenant_id, status),
                )
            else:
                rows = self._store.execute(
                    "SELECT * FROM approvals WHERE tenant_id=? ORDER BY created_at DESC",
                    (tenant_id,),
                )
        else:
            rows = self._store.execute(
                "SELECT * FROM approvals WHERE tenant_id=? AND requester_user_id=?"
                " ORDER BY created_at DESC",
                (tenant_id, actor_user_id),
            )
        return [dict(r) for r in rows]

    def expire_approvals(self, tenant_id: str) -> int:
        """Mark overdue pending approvals expired. Returns how many flipped."""
        import time as _time
        now = int(_time.time())
        with self._tx() as con:
            rows = con.execute(
                "SELECT id FROM approvals WHERE tenant_id=? AND status='pending'"
                " AND expires_at IS NOT NULL AND expires_at<?",
                (tenant_id, now),
            ).fetchall()
            for row in rows:
                con.execute(
                    "UPDATE approvals SET status='expired', decided_at=?, version=version+1"
                    " WHERE id=?",
                    (now, row["id"]),
                )
            con.commit()
        return len(rows)

    # --- quotas (open-database-runtime 9.x) --------------------------------

    _QUOTA_METRICS = ("tokens", "tool_calls", "messages", "storage_bytes")

    def set_quota(
        self, *, actor_user_id: str, tenant_id: str, metric: str,
        hard_limit: int, user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set a tenant (or tenant-user) hard limit; applies to the next
        consumption immediately."""
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("quota set denied", code="forbidden", status=403)
        metric = (metric or "").strip()
        if metric not in self._QUOTA_METRICS:
            raise IdentityServiceError(f"unknown quota metric: {metric}", code="invalid", status=400)
        if int(hard_limit or 0) < 0:
            raise IdentityServiceError("invalid quota limit", code="invalid", status=400)
        uid = user_id or ""
        with self._tx() as con:
            con.execute(
                "INSERT INTO quota_limits(tenant_id, user_id, metric, hard_limit)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(tenant_id, user_id, metric)"
                " DO UPDATE SET hard_limit=excluded.hard_limit",
                (tenant_id, uid, metric, int(hard_limit)),
            )
            self._audit_in_tx(
                con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                target_tenant_id=tenant_id, action="quota.set",
                target=f"quota:{tenant_id}:{uid}:{metric}",
                redacted_changes={"hard_limit": int(hard_limit)}, result="success")
            con.commit()
        return {"tenant_id": tenant_id, "user_id": uid, "metric": metric,
                "hard_limit": int(hard_limit)}

    def quota_status(
        self, *, actor_user_id: str, tenant_id: str,
    ) -> Dict[str, Any]:
        """Per-tenant limit/usage (qualified users only)."""
        if not self._is_control(actor_user_id, tenant_id):
            raise IdentityServiceError("quota read denied", code="forbidden", status=403)
        import calendar
        import time as _time
        day_start = calendar.timegm(_time.gmtime())
        limits = self._store.execute(
            "SELECT * FROM quota_limits WHERE tenant_id=?", (tenant_id,)
        )
        usage = self._store.execute(
            "SELECT user_id, metric, SUM(used) used FROM quota_usage"
            " WHERE tenant_id=? AND window_start<=? GROUP BY user_id, metric",
            (tenant_id, day_start),
        )
        return {"tenant_id": tenant_id, "limits": [dict(r) for r in limits],
                "usage": [dict(r) for r in usage]}

    def consume_quota(
        self, *, user_id: str, tenant_id: str, metric: str, amount: int = 1,
        user_limit_only: bool = False,
    ) -> bool:
        """Record consumption against the tenant (and user) quota windows.

        Returns True when recorded, False when the limit is already exhausted
        (a denied consumption is audited, never partially counted). Fail-closed
        on storage errors so a broken meter cannot silently over-consume.
        """
        from common.log import logger
        import calendar
        import time as _time
        if metric not in self._QUOTA_METRICS:
            raise IdentityServiceError(f"unknown quota metric: {metric}", code="invalid", status=400)
        if not self._member_active(user_id, tenant_id):
            logger.warning(f"[quota] consume by non-member user={user_id} tenant={tenant_id}")
            return False
        window = calendar.timegm(_time.gmtime())
        amount = max(1, int(amount or 1))
        try:
            with self._tx() as con:
                # Fast path: no tenant or user limit is configured for this
                # metric — consume nothing and let the call through.
                configured = con.execute(
                    "SELECT 1 FROM quota_limits WHERE tenant_id=? AND"
                    " (user_id=? OR user_id='') AND metric=? AND hard_limit>0 LIMIT 1",
                    (tenant_id, user_id, metric),
                ).fetchone()
                if not configured:
                    con.commit()
                    return True
                # Tenant bucket first ('' user) unless user_limit_only.
                if not user_limit_only:
                    limit_row = con.execute(
                        "SELECT hard_limit FROM quota_limits WHERE tenant_id=? AND user_id=''"
                        " AND metric=?",
                        (tenant_id, metric),
                    ).fetchone()
                    if limit_row and limit_row["hard_limit"] > 0:
                        usage_row = con.execute(
                            "SELECT used FROM quota_usage WHERE tenant_id=? AND user_id=''"
                            " AND metric=? AND window_start=?",
                            (tenant_id, metric, window),
                        ).fetchone()
                        used = usage_row["used"] if usage_row else 0
                        if used + amount > limit_row["hard_limit"]:
                            self._audit_in_tx(
                                con, actor_user_id=user_id, tenant_id=tenant_id,
                                target_tenant_id=tenant_id, action="quota.deny",
                                target=f"quota:{tenant_id}:::{metric}",
                                redacted_changes={"limit": limit_row["hard_limit"],
                                                  "used": used, "amount": amount},
                                result="denied")
                            con.commit()
                            return False
                        con.execute(
                            "INSERT INTO quota_usage(tenant_id,user_id,metric,window_start,used)"
                            " VALUES (?,?,?,?,?)"
                            " ON CONFLICT(tenant_id,user_id,metric,window_start)"
                            " DO UPDATE SET used=quota_usage.used+excluded.used",
                            (tenant_id, "", metric, window, amount),
                        )
                # User bucket.
                user_limit = con.execute(
                    "SELECT hard_limit FROM quota_limits WHERE tenant_id=? AND user_id=?"
                    " AND metric=?",
                    (tenant_id, user_id, metric),
                ).fetchone()
                if user_limit and user_limit["hard_limit"] > 0:
                    usage_row = con.execute(
                        "SELECT used FROM quota_usage WHERE tenant_id=? AND user_id=?"
                        " AND metric=? AND window_start=?",
                        (tenant_id, user_id, metric, window),
                    ).fetchone()
                    used = usage_row["used"] if usage_row else 0
                    if used + amount > user_limit["hard_limit"]:
                        self._audit_in_tx(
                            con, actor_user_id=user_id, tenant_id=tenant_id,
                            target_tenant_id=tenant_id, action="quota.deny",
                            target=f"quota:{tenant_id}:{user_id}:{metric}",
                            redacted_changes={"limit": user_limit["hard_limit"],
                                              "used": used, "amount": amount},
                            result="denied")
                        con.commit()
                        return False
                    con.execute(
                        "INSERT INTO quota_usage(tenant_id,user_id,metric,window_start,used)"
                        " VALUES (?,?,?,?,?)"
                        " ON CONFLICT(tenant_id,user_id,metric,window_start)"
                        " DO UPDATE SET used=quota_usage.used+excluded.used",
                        (tenant_id, user_id, metric, window, amount),
                    )
                con.commit()
            return True
        except IdentityServiceError:
            raise
        except Exception as error:
            logger.error(f"[quota] consume failed for {tenant_id}/{user_id}/{metric}: {error}")
            raise IdentityServiceError("quota meter failed", code="quota_error", status=500) from error


def identity_db_path() -> str:
    """Resolve the on-disk path of ``identity.db`` from config.

    Centralised here so the web layer and the resource-isolation layer
    (``common/state_dir``) agree on the same database without either importing
    the other.
    """
    from config import conf, get_data_root
    import os
    configured = conf().get("identity_db_path")
    return configured or os.path.join(get_data_root(), "identity.db")


def get_identity_service() -> IdentityService:
    """Return a fresh IdentityService bound to the configured identity.db."""
    return IdentityService(identity_db_path())

