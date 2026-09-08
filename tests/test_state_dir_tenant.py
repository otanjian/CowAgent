# encoding:utf-8
"""Tenant-scoped state_dir tests (task 3.9).

Verifies that when a ``RuntimeIdentity`` carries a ``tenant_id``, the shared
root resolves to that tenant's configured root rather than the default Agent's
workspace, and that a tenant with no configured root raises instead of falling
back to another tenant's assets.
"""

import os
import tempfile
from pathlib import Path

import pytest

from auth.service import IdentityService
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from common import state_dir
from common.runtime_identity import RuntimeIdentity, use_identity


def _svc():
    db = os.path.join(tempfile.mkdtemp(), "identity.db")
    svc = IdentityService(db)
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass", shared_root="/s/acme")
    return svc


@pytest.fixture
def registry(tmp_path):
    reg = AgentRegistry(
        [
            AgentProfile(id="alpha", name="Alpha", workspace=str(tmp_path / "alpha")),
            AgentProfile(id="beta", name="Beta", workspace=str(tmp_path / "beta")),
        ],
        "alpha",
    )
    set_agent_registry(reg)
    yield reg
    set_agent_registry(None)


def test_tenant_scoped_shared_root(registry, tmp_path, monkeypatch):
    svc = _svc()
    tid = svc.list_tenants()[0]["id"]
    monkeypatch.setattr("auth.service.identity_db_path", lambda: svc._store.db_path)
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="beta")):
        assert state_dir.shared_root() == Path("/s/acme")


def test_tenant_without_shared_root_raises(registry, monkeypatch):
    svc = _svc()
    tid = svc.list_tenants()[0]["id"]
    monkeypatch.setattr("auth.service.identity_db_path", lambda: svc._store.db_path)
    with svc._tx() as con:
        con.execute("UPDATE tenants SET shared_root='' WHERE id=?", (tid,))
    with use_identity(RuntimeIdentity(tenant_id=tid, agent_id="beta")):
        with pytest.raises(state_dir.StateDirError, match="no configured shared root"):
            state_dir.shared_root()


def test_legacy_mode_unaffected(registry, tmp_path):
    with use_identity(RuntimeIdentity(agent_id="beta")):
        assert state_dir.shared_root() == tmp_path / "alpha"
