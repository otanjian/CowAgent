# encoding:utf-8
"""External-connection control store: schema shape and the constraints the
service layer relies on (change ``add-external-system-access``, tasks 2.1/2.2).

The identity database is the single authority for a connection, so the
invariants that keep that true have to be structural rather than a convention
each caller remembers: which ``(kind, scope)`` combinations exist at all, the
one-OA-per-tenant / one-email-per-owner / one-override-per-platform-row
singletons, soft delete not occupying a singleton slot, and the
status/version/ownership columns every later group reads.

These tests drive the real migration through :class:`IdentityService` (the same
path the deployment takes) rather than creating tables by hand, because the
failure they exist to catch is "the migration never ran / ran twice".
"""

from __future__ import annotations

import sqlite3

import pytest

from tests._helpers import build_identity


def _raw(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


def _conn_row(store, **overrides):
    """Insert a minimal external connection; returns its id."""
    row = {
        "id": "ext-1",
        "kind": "mcp",
        "scope": "tenant",
        "tenant_id": None,
        "owner_user_id": None,
        "name": "n",
        "config_json": "{}",
        "enabled": 1,
        "version": 1,
        "base_connection_id": None,
        "source": "created",
        "deleted_at": None,
        "created_by": "u",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with store.connect() as con:
        con.execute(
            "INSERT INTO external_connections (%s) VALUES (%s)" % (cols, marks),
            tuple(row.values()),
        )
        con.commit()
    return row["id"]


def test_control_tables_exist_after_migration(stack):
    with _raw(stack.service._store.db_path) as con:
        names = {
            r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    for table in (
        "external_connections",
        "external_connection_secret_refs",
        "external_connection_catalog_versions",
        "external_connection_tenant_access",
        "external_connection_tests",
        "external_connection_migrations",
        "platform_connection_secrets",
        "platform_connection_secret_versions",
    ):
        assert table in names, table


def test_migration_is_idempotent(stack):
    from auth.service import IdentityService

    # Re-opening runs the migration set again; a second pass must be a no-op.
    again = IdentityService(stack.service._store.db_path)
    with _raw(again._store.db_path) as con:
        rows = con.execute(
            "SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions = [r["version"] for r in rows]
    assert len(versions) == len(set(versions))


@pytest.mark.parametrize("kind, scope, ok", [
    ("mcp", "platform", True),
    ("erp", "platform", False),
    ("oa", "platform", False),
    ("email", "platform", False),
    ("mcp", "tenant", True),
    ("erp", "tenant", True),
    ("oa", "tenant", True),
    ("email", "tenant", False),
    ("email", "personal", True),
    ("mcp", "personal", False),
    ("erp", "personal", False),
])
def test_scope_and_kind_combinations(stack, kind, scope, ok):
    tenant = stack.tenant_id
    owner = stack.root
    payload = {
        "platform": {"tenant_id": None, "owner_user_id": None},
        "tenant": {"tenant_id": tenant, "owner_user_id": None},
        "personal": {"tenant_id": tenant, "owner_user_id": owner},
    }[scope]
    if ok:
        _conn_row(stack.service._store, kind=kind, scope=scope, **payload)
    else:
        with pytest.raises(sqlite3.IntegrityError):
            _conn_row(stack.service._store, kind=kind, scope=scope, **payload)


def test_platform_row_cannot_name_a_tenant_or_owner(stack):
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, kind="mcp", scope="platform",
                  tenant_id=stack.tenant_id, owner_user_id=None)
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, kind="mcp", scope="platform",
                  tenant_id=None, owner_user_id=stack.root)


def test_oa_is_one_per_tenant(stack):
    _conn_row(stack.service._store, id="oa-1", kind="oa", scope="tenant",
              tenant_id=stack.tenant_id)
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, id="oa-2", kind="oa", scope="tenant",
                  tenant_id=stack.tenant_id)


def test_email_is_one_per_owner_and_tenant(stack):
    _conn_row(stack.service._store, id="mail-1", kind="email", scope="personal",
              tenant_id=stack.tenant_id, owner_user_id=stack.root)
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, id="mail-2", kind="email", scope="personal",
                  tenant_id=stack.tenant_id, owner_user_id=stack.root)
    # A different owner in the same tenant is a different singleton.
    other = stack.service.create_member(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        operation="create-new", username="other", display_name="Other",
        temporary_password="TempPass123!", roles=["member"])["user_id"]
    _conn_row(stack.service._store, id="mail-3", kind="email", scope="personal",
              tenant_id=stack.tenant_id, owner_user_id=other)


def test_soft_delete_frees_the_singleton_slot(stack):
    _conn_row(stack.service._store, id="oa-1", kind="oa", scope="tenant",
              tenant_id=stack.tenant_id, deleted_at=1234)
    _conn_row(stack.service._store, id="oa-2", kind="oa", scope="tenant",
              tenant_id=stack.tenant_id)


def test_override_is_one_per_platform_row_and_tenant(stack):
    platform = _conn_row(stack.service._store, id="mcp-platform", kind="mcp",
                         scope="platform", tenant_id=None, owner_user_id=None)
    _conn_row(stack.service._store, id="mcp-override", kind="mcp", scope="tenant",
              tenant_id=stack.tenant_id, base_connection_id=platform)
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, id="mcp-override-2", kind="mcp",
                  scope="tenant", tenant_id=stack.tenant_id,
                  base_connection_id=platform)
    # ...but the same tenant may still own a plain connection.
    _conn_row(stack.service._store, id="mcp-own", kind="mcp", scope="tenant",
              tenant_id=stack.tenant_id)


def test_override_must_be_a_tenant_mcp_row(stack):
    platform = _conn_row(stack.service._store, id="mcp-platform", kind="mcp",
                         scope="platform")
    with pytest.raises(sqlite3.IntegrityError):
        _conn_row(stack.service._store, id="erp-override", kind="erp",
                  scope="tenant", tenant_id=stack.tenant_id,
                  base_connection_id=platform)


def test_deleting_a_referenced_connection_is_refused(stack):
    platform = _conn_row(stack.service._store, id="mcp-platform", kind="mcp",
                         scope="platform")
    _conn_row(stack.service._store, id="mcp-override", kind="mcp", scope="tenant",
              tenant_id=stack.tenant_id, base_connection_id=platform)
    with pytest.raises(sqlite3.IntegrityError):
        with stack.service._store.connect() as con:
            con.execute("DELETE FROM external_connections WHERE id=?",
                        (platform,))
            con.commit()


def test_catalog_default_must_reference_a_real_connection(stack):
    with pytest.raises(sqlite3.IntegrityError):
        with stack.service._store.connect() as con:
            con.execute(
                "INSERT INTO external_connection_catalog_versions"
                " (scope_key, kind, revision, default_connection_id)"
                " VALUES (?, 'erp', 1, 'nope')", ("tenant:%s" % stack.tenant_id,))
            con.commit()


def test_secret_ref_ownership_is_exclusive(stack):
    conn_id = _conn_row(stack.service._store, id="mcp-1", kind="mcp",
                        scope="tenant", tenant_id=stack.tenant_id)
    with stack.service._store.connect() as con:
        con.execute(
            "INSERT INTO platform_connection_secrets(id, name, ciphertext,"
            " created_by) VALUES ('ps-1', 'ps', 'v1.a.b', 'u')")
        con.execute(
            "INSERT INTO credentials(id, tenant_id, name, ciphertext, created_by)"
            " VALUES ('cred-1', ?, 'c', 'v1.a.b', ?)",
            (stack.tenant_id, stack.root))
        con.commit()
    # A slot with neither owner is not a reference.
    with pytest.raises(sqlite3.IntegrityError):
        with stack.service._store.connect() as con:
            con.execute(
                "INSERT INTO external_connection_secret_refs"
                " (connection_id, slot, credential_id, platform_secret_id,"
                " secret_version) VALUES (?, 'token', NULL, NULL, 1)", (conn_id,))
            con.commit()
    # ...and neither is one with both.
    with pytest.raises(sqlite3.IntegrityError):
        with stack.service._store.connect() as con:
            con.execute(
                "INSERT INTO external_connection_secret_refs"
                " (connection_id, slot, credential_id, platform_secret_id,"
                " secret_version) VALUES (?, 'token', 'cred-1', 'ps-1', 1)",
                (conn_id,))
            con.commit()
    with stack.service._store.connect() as con:
        con.execute(
            "INSERT INTO external_connection_secret_refs"
            " (connection_id, slot, credential_id, platform_secret_id,"
            " secret_version) VALUES (?, 'token', 'cred-1', NULL, 1)", (conn_id,))
        con.commit()
