# encoding:utf-8
"""SQLite identity store backing the multi-tenant IAM system.

This is the single source of truth for identity, tenancy, membership, RBAC,
department/organization, agent binding and the append-only identity audit. It
owns ``identity.db``. Agent workspaces and business content remain owned by the
existing Agent Registry and per-agent business databases respectively.

Design constraints observed here (repeated from the design.md boundary):

* One database, foreign keys + unique constraints enforced.
* Every mutable object carries an optimistic-concurrency ``version``.
* Password is never stored anywhere but the ``users.password_hash`` column
  (and never in audit).
* The ``schema_migrations`` table drives idempotent, versioned migrations.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Callable, List, Sequence

#: Ordered list of schema migrations. Appending a migration and bumping
#: ``__schema_version__`` (the final entry) is how the store evolves.
_migrations: List[Callable[[sqlite3.Connection], None]] = []


def migration_versions() -> List[int]:
    """Return the ordered list of migration version identifiers."""
    return [index + 1 for index in range(len(_migrations))]


def has_migration_signature(db_path: str) -> bool:
    """Return True when ``db_path`` already carries a valid IAM migration marker.

    A database is considered "already migrated" (new-mode data) when the
    ``schema_migrations`` table exists and has at least one recorded version.
    Used at startup to refuse silently booting legacy mode over new-mode data
    (task 4.6 / isolation spec: "已有迁移标识或新模式数据时 MUST 阻止直接启动 legacy").
    """
    if not db_path or not os.path.exists(db_path):
        return False
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM schema_migrations"
            ).fetchone()
            return bool(row and row[0] > 0)
        finally:
            con.close()
    except (sqlite3.Error, Exception):
        return False


def refuse_legacy_after_migration(identity_mode: str, db_path: str) -> bool:
    """Return True (refuse) when legacy mode would read already-migrated data.

    This is the server-side startup guard: if the config still says ``legacy``
    but ``identity.db`` already carries a valid migration signature (new-mode
    data), booting legacy would silently read data it was never meant to
    consume. The caller SHOULD abort startup and tell the operator to either
    switch ``identity_mode`` to ``database`` (the data is authoritative) or
    restore a pre-migration snapshot (the data is disposable).
    """
    mode = str(identity_mode or "").lower()
    if mode == "database":
        return False
    return has_migration_signature(db_path)


__schema_version__ = 3


def _migration_1(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        -- Global accounts. One row per human principal.
        CREATE TABLE users (
            id            TEXT PRIMARY KEY,
            username      TEXT NOT NULL COLLATE NOCASE,
            display_name  TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active        INTEGER NOT NULL DEFAULT 1,
            is_platform_admin INTEGER NOT NULL DEFAULT 0,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            temp_password_expires_at INTEGER,
            version       INTEGER NOT NULL DEFAULT 1,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_users_username ON users(username);

        -- Tenants. Stable, server-generated identity.
        CREATE TABLE tenants (
            id             TEXT PRIMARY KEY,
            code           TEXT NOT NULL COLLATE NOCASE,
            name           TEXT NOT NULL,
            active         INTEGER NOT NULL DEFAULT 1,
            shared_root    TEXT NOT NULL,
            default_agent_id TEXT,
            version        INTEGER NOT NULL DEFAULT 1,
            created_at     INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at     INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_tenants_code ON tenants(code);

        -- Memberships: (tenant, user) edges, each with its own display/active/
        -- department/position. A user may belong to many tenants.
        CREATE TABLE memberships (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            user_id       TEXT NOT NULL REFERENCES users(id),
            display_name  TEXT NOT NULL,
            active        INTEGER NOT NULL DEFAULT 1,
            department_id TEXT,
            position_text TEXT,
            version       INTEGER NOT NULL DEFAULT 1,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at    INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_memberships_tenant_user ON memberships(tenant_id, user_id);
        CREATE INDEX idx_memberships_tenant ON memberships(tenant_id);

        -- Roles are tenant-scoped. Built-ins (tenant_admin/member) are flagged.
        CREATE TABLE roles (
            id               TEXT PRIMARY KEY,
            tenant_id        TEXT NOT NULL REFERENCES tenants(id),
            code             TEXT NOT NULL,
            name             TEXT NOT NULL,
            builtin          INTEGER NOT NULL DEFAULT 0,
            permissions_json TEXT NOT NULL,
            version          INTEGER NOT NULL DEFAULT 1,
            created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at       INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_roles_tenant_code ON roles(tenant_id, code);

        -- Membership <-> Role many-to-many. The membership_id alone is the true
        -- foreign key into memberships; tenant scoping stays explicit at the
        -- application layer.
        CREATE TABLE membership_roles (
            membership_id TEXT NOT NULL REFERENCES memberships(id),
            role_id       TEXT NOT NULL REFERENCES roles(id),
            PRIMARY KEY (membership_id, role_id)
        );

        -- Departments: tenant-scoped tree. The virtual root is flagged by
        -- code='__root__' (not deletable, movable or disabled).
        CREATE TABLE departments (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL REFERENCES tenants(id),
            parent_id   TEXT,
            code        TEXT NOT NULL,
            name        TEXT NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            version     INTEGER NOT NULL DEFAULT 1,
            created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
            updated_at  INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX idx_depts_tenant_code ON departments(tenant_id, code);

        -- Database-backed sessions. Only a digest of the opaque token is stored;
        -- no current-tenant or permission snapshot lives here.
        CREATE TABLE auth_sessions (
            id         TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            user_id    TEXT NOT NULL REFERENCES users(id),
            created_at INTEGER NOT NULL DEFAULT (unixepoch()),
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            restricted INTEGER NOT NULL DEFAULT 0
        );

        -- Agent -> tenant binding. A global, unique agent_id maps to exactly one
        -- tenant. NULL private_owner_user_id means "explicitly tenant-shared";
        -- a non-null value records the private owner.
        CREATE TABLE agent_bindings (
            agent_id              TEXT PRIMARY KEY,
            tenant_id             TEXT NOT NULL REFERENCES tenants(id),
            private_owner_user_id TEXT REFERENCES users(id),
            created_at            INTEGER NOT NULL DEFAULT (unixepoch())
        );

        -- Append-only identity audit. Same-transaction commit as the identity
        -- change it describes. redacted_changes never contains secrets.
        CREATE TABLE audit_events (
            id               TEXT PRIMARY KEY,
            time             INTEGER NOT NULL DEFAULT (unixepoch()),
            actor_user_id    TEXT,
            actor_username   TEXT,
            tenant_id        TEXT,
            target_tenant_id TEXT,
            action           TEXT NOT NULL,
            target           TEXT NOT NULL,
            redacted_changes TEXT NOT NULL,
            result           TEXT NOT NULL
        );
        CREATE INDEX idx_audit_tenant ON audit_events(tenant_id);
        CREATE INDEX idx_audit_time ON audit_events(time);

        -- Audit is append-only: block DELETE and UPDATE at the database layer.
        CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
        CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

        """
    )


_migrations.append(_migration_1)


def _migration_2(con: sqlite3.Connection) -> None:
    """Resource-authorization schema (task 1.2).

    Two grant tables plus a per-role default-model field. No separate resource
    catalog or model policy table is created: resource identity is projected from
    the live sources; model defaults are a role column reusing the role version.
    The tenant-level "what may this tenant allocate" limit lives in
    ``tenant_resource_grants``; the per-role "what may a member use" lives in
    ``role_resource_grants``. Both reuse the owning role/tenant ``version`` for
    optimistic concurrency and audit in the same transaction.
    """
    con.executescript(
        """
        -- Platform-to-tenant global resource limits. Only global/model/tool/MCP
        -- resources need an explicit platform grant; tenant-owned resources are
        -- derived from their origin (agent_bindings, skills dir, tools registry).
        CREATE TABLE tenant_resource_grants (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            resource_kind TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            action        TEXT NOT NULL,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            UNIQUE (tenant_id, resource_kind, resource_id, action)
        );
        CREATE INDEX idx_tenant_resource_grants_tenant ON tenant_resource_grants(tenant_id);

        -- Per-role resource grants. The role's tenant_id (via roles) is the true
        -- tenant scope; the application layer validates the resource belongs to
        -- that tenant before insert. A role's full grant set is versioned with
        -- the parent roles.version.
        CREATE TABLE role_resource_grants (
            id            TEXT PRIMARY KEY,
            role_id       TEXT NOT NULL REFERENCES roles(id),
            resource_kind TEXT NOT NULL,
            resource_id   TEXT NOT NULL,
            action        TEXT NOT NULL,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            UNIQUE (role_id, resource_kind, resource_id, action)
        );
        CREATE INDEX idx_role_resource_grants_role ON role_resource_grants(role_id);

        -- Per-role optional default model per capability: {capability: model_id}.
        -- Stored on the role so it shares the role's version/audit transaction.
        ALTER TABLE roles ADD COLUMN model_defaults_json TEXT;
        """
    )


_migrations.append(_migration_2)


def _migration_3(con: sqlite3.Connection) -> None:
    """External identity bindings (task 1.1, open-database-runtime).

    Maps an external IM identity triple ``(provider, issuer/corp_id, subject)``
    to exactly one global User. Only administrators create bindings; inbound
    channel messages resolve through here before any execution so the runtime
    identity is always a real account, never a channel service principal.
    ``issuer`` is the provider's corp/tenant identifier (empty string means a
    provider without a corp scope, e.g. a personal consumer bot); uniqueness is
    still enforced on the triple as stored.
    """
    con.executescript(
        """
        CREATE TABLE external_identities (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(id),
            provider      TEXT NOT NULL,
            issuer        TEXT NOT NULL DEFAULT '',
            subject       TEXT NOT NULL,
            created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
            last_used_at  INTEGER
        );
        CREATE UNIQUE INDEX idx_external_identities_triple
            ON external_identities(provider, issuer, subject);
        CREATE INDEX idx_external_identities_user
            ON external_identities(user_id);
        """
    )


_migrations.append(_migration_3)


class IdentityStoreError(RuntimeError):
    """Raised when the identity store cannot be opened or migrated."""


class ConnGuard:
    """Context-manager wrapper that owns a ``sqlite3.Connection``.

    A bare ``sqlite3.Connection`` used as a context manager only commits or
    rolls back on ``__exit__``; it does **not** close the connection. That means
    every ``with conn:`` block leaks a file descriptor until the process runs
    out of open handles, which shows up as a wedge (``identity_db_unavailable``
    / 503) after a few hundred requests.

    ``ConnGuard`` mirrors the wrapped connection for normal use (``execute``,
    ``commit``, ``row_factory``, ``executescript``, ``fetchone``, ...) and
    closes the underlying connection in ``__exit__``, so connection lifetime is
    bounded. On error it rolls back before closing to avoid leaving a dangling
    write lock.
    """

    def __init__(self, con: sqlite3.Connection):
        self._con = con

    def __getattr__(self, name):
        # Delegate any sqlite3.Connection attribute/method transparently.
        return getattr(self._con, name)

    def __enter__(self) -> "ConnGuard":
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._con is None:
                return False
            try:
                # Match the semantics of a bare ``sqlite3.Connection`` used as a
                # context manager: commit on success, rollback on error. We
                # additionally CLOSE the connection. (Most callers commit
                # explicitly, but some rely on the default-commit behaviour, so
                # we must not change it.)
                if exc_type is None:
                    self._con.commit()
                else:
                    self._con.rollback()
            except sqlite3.Error:
                pass
        finally:
            self._con.close()
            self._con = None
        return False


class IdentityStore:
    """Thin, thread-safe wrapper over a SQLite ``identity.db``.

    Ownership of the connection and of transaction boundaries is left to the
    caller via ``connect()`` (a context manager) and ``execute()``. The store
    never opens its own long-lived connection so the service layer can bundle
    identity writes + audit in a single transaction.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._migrate()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _migrate(self) -> None:
        if not self._db_path:
            raise IdentityStoreError("identity.db path is empty")
        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self.connect() as con:
            cursor = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            exists = {row["name"] for row in cursor.fetchall()}
            if "schema_migrations" not in exists:
                con.executescript(
                    "CREATE TABLE schema_migrations("
                    " version INTEGER NOT NULL,"
                    " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))"
                )
            applied = {
                row["version"]
                for row in con.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for version in migration_versions():
                if version not in applied:
                    con.execute("BEGIN")
                    _migrations[version - 1](con)
                    con.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)",
                        (version,),
                    )
                    con.commit()

    def connect(self) -> "ConnGuard":
        """Open a connection with foreign keys enabled. Use as a context manager.

        SQLite connections are cheap here and thread-local usage is the norm;
        a fresh connection is created per call. The caller controls the
        transaction with explicit BEGIN/COMMIT.

        Returns a :class:`ConnGuard` (a context manager) instead of a raw
        ``sqlite3.Connection``. A raw ``sqlite3.Connection`` only commits or
        rolls back on ``__exit__`` and NEVER closes the connection, so using it
        with ``with`` leaks one connection per call. ``ConnGuard`` closes the
        underlying connection on exit, preventing unbounded connection growth
        (which would otherwise wedge the identity store under sustained load).
        """
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        return ConnGuard(con)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self.connect() as con:
            cursor = con.execute(sql, params)
            rows = cursor.fetchall()
            con.commit()  # read-only SELECTs commit as a no-op
            return rows
