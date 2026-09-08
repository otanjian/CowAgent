# encoding:utf-8
"""Tenant shared-root containment tests (task 3.9).

Verifies that ``state_dir.shared_root()`` rejects a tenant root that:
  * is/falls inside the home directory or a global data/config root (home
    fallback escape), unless it is the verified engineering workspace root;
  * equals, contains, or is contained by another tenant's shared root
    (cross-tenant containment);
and that a legitimate same-tenant nested/engineering root is allowed.
"""

import os
import tempfile
from pathlib import Path

import pytest

from auth.service import IdentityService
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from common import state_dir
from common.runtime_identity import RuntimeIdentity, use_identity


def _svc(db):
    svc = IdentityService(db)
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=os.path.join(tempfile.mkdtemp(), "placeholder", "root"),
        allow_weak=True)
    return svc


def _set_root(svc, tenant_id, root):
    with svc._tx() as con:
        con.execute("UPDATE tenants SET shared_root=? WHERE id=?", (root, tenant_id))
        con.commit()


@pytest.fixture
def registry(tmp_path):
    # The engineering/workspace root the default Agent lives in.
    eng = tmp_path / "eng"
    eng.mkdir(parents=True)
    reg = AgentRegistry(
        [AgentProfile(id="alpha", name="Alpha", workspace=str(eng))], "alpha")
    set_agent_registry(reg)
    yield reg
    set_agent_registry(None)


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _bind_svc_to_db(svc, db, monkeypatch):
    import auth.service as asvc
    monkeypatch.setattr(asvc, "identity_db_path", lambda: db)


def test_engineering_root_is_allowed(registry, tmp_path, monkeypatch):
    """A tenant whose root IS the verified engineering/workspace root passes."""
    db = _db()
    svc = _svc(db)
    tid = svc.list_tenants()[0]["id"]
    _set_root(svc, tid, str(tmp_path / "eng"))
    _bind_svc_to_db(svc, db, monkeypatch)
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="alpha")):
        assert state_dir.shared_root() == (tmp_path / "eng").resolve()


def test_home_fallback_escape_rejected(registry, monkeypatch):
    """A tenant root inside the user's home dir (e.g. ~/Documents) is rejected."""
    db = _db()
    svc = _svc(db)
    tid = svc.list_tenants()[0]["id"]
    _set_root(svc, tid, os.path.expanduser("~/Documents"))
    _bind_svc_to_db(svc, db, monkeypatch)
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="alpha")):
        with pytest.raises(state_dir.StateDirError, match="home/global workspace root"):
            state_dir.shared_root()


def test_cross_tenant_containment_rejected(registry, tmp_path, monkeypatch):
    """Tenant A's root contains tenant B's root -> rejected (cross-tenant)."""
    db = _db()
    svc = _svc(db)
    acme = [t for t in svc.list_tenants() if t["code"] == "acme"][0]
    tid = acme["id"]
    a_root = str(tmp_path / "tenantA")
    _set_root(svc, tid, a_root)
    # Create a second tenant whose root is nested inside tenantA.
    root = svc.list_platform_users()[0]
    svc.create_tenant(
        actor_user_id=root["id"], code="beta", name="Beta",
        shared_root=os.path.join(a_root, "sub"), admin_username="betaadmin",
        admin_display="Beta", admin_password="Str0ngPass2",
        recent_password="Str0ngAdminPass")
    _bind_svc_to_db(svc, db, monkeypatch)
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="alpha")):
        with pytest.raises(state_dir.StateDirError, match="overlaps tenant"):
            state_dir.shared_root()


def test_cross_tenant_equal_root_rejected(registry, tmp_path, monkeypatch):
    """Two tenants sharing the exact same root -> rejected (equal containment)."""
    db = _db()
    svc = _svc(db)
    tid = svc.list_tenants()[0]["id"]
    shared = str(tmp_path / "shared")
    _set_root(svc, tid, shared)
    root = svc.list_platform_users()[0]
    svc.create_tenant(
        actor_user_id=root["id"], code="beta", name="Beta",
        shared_root=shared, admin_username="betaadmin",
        admin_display="Beta", admin_password="Str0ngPass2",
        recent_password="Str0ngAdminPass")
    _bind_svc_to_db(svc, db, monkeypatch)
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="alpha")):
        with pytest.raises(state_dir.StateDirError, match="overlaps tenant"):
            state_dir.shared_root()


def test_legacy_mode_unaffected(registry, tmp_path):
    """Legacy (no tenant) resolves the default Agent's workspace, no guard trip."""
    with use_identity(RuntimeIdentity(agent_id="alpha")):
        assert state_dir.shared_root() == (tmp_path / "eng").resolve()
