# encoding:utf-8
"""Database-mode project_store root selection + path containment (slice A)."""

import os
import tempfile
from pathlib import Path

import pytest

from common.runtime_identity import RuntimeIdentity, use_identity
from agent.workspace import project_store


def test_store_file_uses_user_root_in_database(monkeypatch, tmp_path):
    # user_root() = shared_root()/users/<user_id>; stub shared_root to the temp.
    import common.state_dir as sd
    user = "u_alice"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    ident = RuntimeIdentity(tenant_id="t1", user_id=user)
    with use_identity(ident):
        path = project_store._store_file()
    expected = fake_shared / "users" / user / "projects.json"
    assert Path(path) == expected


def test_projects_root_ignores_configured_root_in_database(monkeypatch, tmp_path):
    import common.state_dir as sd
    import config
    user = "u_bob"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    monkeypatch.setattr(config, "conf", lambda: {"project_workspace_root": "/etc/evil"})
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        root = project_store.projects_root()
    assert root == str(fake_shared / "users" / user / "projects")
    assert root != os.path.realpath("/etc/evil")


def test_create_rejects_separators(monkeypatch, tmp_path):
    import common.state_dir as sd
    user = "u_carol"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        with pytest.raises(ValueError):
            project_store.create_project("a/b")
        with pytest.raises(ValueError):
            project_store.create_project("..")


def test_set_project_dir_rejects_outside_user_root(monkeypatch, tmp_path):
    import common.state_dir as sd
    user = "u_dave"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)
    outside = tmp_path / "outside"
    outside.mkdir()
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user)):
        with pytest.raises(ValueError, match="outside"):
            project_store.set_project_dir("sess-1", str(outside), "alpha")
