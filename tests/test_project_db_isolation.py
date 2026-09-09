# encoding:utf-8
"""Two-user isolation for user-private projects (database mode)."""

import os
import tempfile
from pathlib import Path

import pytest

from common.runtime_identity import RuntimeIdentity, use_identity
from agent.workspace import project_store


def _user_root(base: Path, user_id: str) -> Path:
    return base / "users" / user_id


def test_user_b_cannot_bind_user_a_project(monkeypatch, tmp_path):
    # User A creates a project under A's user_root. User B (same tenant) must
    # NOT be able to bind a path under A's root even if B knows the path
    # (store-level containment rejects it via _require_within_user_root).
    import common.state_dir as sd
    user_a = "u_a"
    user_b = "u_b"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)

    a_root = _user_root(fake_shared, user_a) / "projects"
    a_root.mkdir(parents=True)
    a_proj = a_root / "alpha"
    a_proj.mkdir()

    # As user A, creation/binding works.
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_a)):
        bound = project_store.set_project_dir("sess-a", str(a_proj), "alpha")
        assert bound == os.path.realpath(str(a_proj))

    # As user B, binding A's path must be rejected.
    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_b)):
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.set_project_dir("sess-b", str(a_proj), "alpha")


def test_user_b_cannot_rename_or_delete_user_a_project(monkeypatch, tmp_path):
    import common.state_dir as sd
    user_a = "u_a"
    user_b = "u_b"
    fake_shared = tmp_path / "shared"
    monkeypatch.setattr(sd, "shared_root", lambda: fake_shared)

    a_root = _user_root(fake_shared, user_a) / "projects"
    a_root.mkdir(parents=True)
    a_proj = a_root / "beta"
    a_proj.mkdir()

    with use_identity(RuntimeIdentity(tenant_id="t1", user_id=user_b)):
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.rename_project(str(a_proj), "betanew")
        with pytest.raises(ValueError, match="user's projects root"):
            project_store.delete_project(str(a_proj), "alpha")
