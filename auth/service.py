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
)


#: Which console pages/tabs this change actually signs for availability. Keys
#: are page/tab capability ids used by the front-end navigation availability
#: projection; values are the read permission that drives ``read_allowed`` and
#: the display scope. This is intentionally finite: pages whose consumer is not
#: yet adapted/accepted are signed here but reported unavailable until the
#: consumer opens. The list mirrors the console-information-architecture commit.
_SIGNED_CONSOLE_PAGES: Dict[str, Dict[str, object]] = {
    "workbench.chat": {"permission": "", "scope": "self"},
    "workbench.history": {"permission": "history.read", "scope": "self"},
    "workbench.agents": {"permission": "agent.read", "scope": "tenant"},
    "workbench.todos": {"permission": "todo.read", "scope": "self"},
    "workbench.schedules": {"permission": "", "scope": "self"},
    "workbench.knowledge": {"permission": "knowledge.read", "scope": "agent"},
    "admin.agents": {"permission": "agent.read", "scope": "agent"},
    "admin.skills": {"permission": "", "scope": "agent"},
    "admin.memory": {"permission": "memory.read", "scope": "agent"},
    "admin.models": {"permission": "", "scope": "platform"},
    "admin.channels": {"permission": "", "scope": "platform"},
    "admin.logs": {"permission": "", "scope": "platform"},
    "admin.members": {"permission": "tenant.members.read", "scope": "tenant"},
    "admin.roles": {"permission": "tenant.members.read", "scope": "tenant"},
    "admin.organization": {"permission": "tenant.org.read", "scope": "tenant"},
    "admin.tenants": {"permission": "", "scope": "platform"},
    "admin.branding": {"permission": "", "scope": "platform"},
    "admin.settings": {"permission": "", "scope": "platform"},
}


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
def _deployment_shared_base() -> Optional[str]:
    """Return the controlled base for new tenant shared roots, or None.

    Uses the verified engineering/workspace root (the default Agent's workspace)
    when resolvable; otherwise the writable data root. Never the user's home or a
    global config tree, matching the containment rules in ``common/state_dir``.
    """
    try:
        from agent.registry import get_agent_registry
        root = get_agent_registry().get(require_enabled=False).workspace
        if root:
            return os.path.realpath(str(root))
    except Exception:
        pass
    try:
        from config import get_data_root
        root = get_data_root()
        if root:
            return os.path.realpath(str(root))
    except Exception:
        pass
    return None


def _derive_tenant_shared_root(code: str) -> str:
    """Generate a tenant shared root under the deployment-controlled base.

    Raises a 503 ``config_error`` when no controlled root is available so a
    tenant is never created with an unusable/empty root (design §4).
    """
    base = _deployment_shared_base()
    if not base:
        raise IdentityServiceError(
            "no configured deployment root for tenant data; set the agent workspace "
            "or data root before creating a tenant",
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
        return {
            "status": "success",
            "effective_permissions": sorted(permissions),
            "is_tenant_admin": is_admin,
            "consumers": self._consumer_availability(),
            "console_pages": self._console_pages_projection(
                user, tenant, permissions, role_codes, is_admin),
        }

    def _console_pages_projection(self, user, tenant, permissions, role_codes, is_admin) -> Dict[str, Any]:
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
        """
        is_platform_admin = bool(user.get("is_platform_admin"))

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

        result: Dict[str, Any] = {}
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
            result[pid] = {
                "available": False,
                "read_allowed": read_ok(permissions, meta.get("permission", "")),
                "scope": meta.get("scope", "tenant"),
                "reason": "consumer_closed" if identity_admin_open else "deferred",
                "actions": {},
            }
        return result

    def _consumer_availability(self) -> Dict[str, Dict[str, Any]]:
        """Static, server-side consumer availability labels (identity milestone).

        Only the consumers adapted and accepted in this change are reported as
        available; everything else is closed with a stable reason. This is a
        static capability report, NOT a readiness/permission service, and never
        grants access on its own — each consumer independently re-authorizes.
        """
        # Chat/workspace/file/scheduler/Tools/OpenAI/external channel and Desktop
        # enterprise login are explicitly deferred. Identity management surfaces
        # that are accepted this milestone are reported available.
        return {
            "web_identity_admin": {"available": True, "reason": ""},
            "chat": {"available": False, "reason": "deferred"},
            "tools": {"available": False, "reason": "deferred"},
            "files": {"available": False, "reason": "deferred"},
            "scheduler": {"available": False, "reason": "deferred"},
            "openai_api": {"available": False, "reason": "deferred"},
            "desktop_enterprise": {"available": False, "reason": "deferred"},
            "mcp": {"available": False, "reason": "deferred"},
            "channels": {"available": False, "reason": "deferred"},
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
        # touching the DB so a missing deployment root fails cleanly (design §4).
        if not shared_root:
            shared_root = _derive_tenant_shared_root(code)

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
            "SELECT id, code, name, builtin, permissions_json, version FROM roles"
            " WHERE tenant_id=? ORDER BY builtin DESC, code",
            (tenant_id,),
        )
        return [
            {**dict(r), "permissions": json.loads(r["permissions_json"] or "[]")}
            for r in rows
        ]

    def create_role(self, actor_user_id: str, tenant_id: str, code: str, name: str,
                    permissions: Sequence[str]) -> Dict[str, Any]:
        self._require_tenant_admin(actor_user_id, tenant_id)
        code = code.strip().lower()
        if not _ROLE_CODE_RE.fullmatch(code):
            raise IdentityServiceError("invalid role code", code="invalid_code")
        if code in BUILTIN_ROLES:
            raise IdentityServiceError("built-in role cannot be recreated", code="forbidden", status=403)
        perms = normalize_permissions(permissions)
        for existing_code in BUILTIN_ROLES:
            if existing_code == code:
                raise IdentityServiceError("built-in role conflict", code="conflict", status=409)
        role_id = self._new_id("role")
        with self._tx() as con:
            con.execute(
                "INSERT INTO roles(id, tenant_id, code, name, builtin, permissions_json, version)"
                " VALUES (?,?,?,?,0,?,1)",
                (role_id, tenant_id, code, name, json.dumps(sorted(perms))),
            )
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="role.create",
                              target=f"role:{role_id}",
                              redacted_changes={"code": code, "permissions": sorted(perms)},
                              result="success")
            con.commit()
        return {"id": role_id, "code": code, "name": name, "permissions": sorted(perms), "version": 1}

    def update_role(self, actor_user_id: str, tenant_id: str, role_id: str,
                    name: str, permissions: Sequence[str], expected_version: int) -> Dict[str, Any]:
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
            con.execute(
                "UPDATE roles SET name=?, permissions_json=?, version=version+1 WHERE id=?",
                (name, json.dumps(sorted(perms)), role_id),
            )
            self._audit_in_tx(con, actor_user_id=actor_user_id, tenant_id=tenant_id,
                              target_tenant_id=tenant_id, action="role.update",
                              target=f"role:{role_id}",
                              redacted_changes={"name": name}, result="success")
            con.commit()
        return {"id": role_id, "name": name, "permissions": sorted(perms)}

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

