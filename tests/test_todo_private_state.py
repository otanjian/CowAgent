"""Private tenant storage stays separate from downloadable Agent workspaces."""

import pytest

from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from agent.todo.service import TodoActor, TodoService, TodoUnavailable
from auth.service import IdentityService
from common import state_dir
from common.runtime_identity import RuntimeIdentity, use_identity


@pytest.fixture
def private_state(tmp_path, monkeypatch):
    data_root = tmp_path / "application-data"
    workspace = tmp_path / "workspace"
    service = IdentityService(str(tmp_path / "identity.db"))
    tenant = service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(workspace), allow_weak=True,
    )
    registry = AgentRegistry([
        AgentProfile(id="alpha", name="Alpha", workspace=str(workspace)),
        AgentProfile(id="beta", name="Beta", workspace=str(tmp_path / "beta")),
    ], "alpha")
    set_agent_registry(registry)
    monkeypatch.setattr("config.get_data_root", lambda: str(data_root))
    yield service, tenant["id"], data_root, workspace
    set_agent_registry(None)


def test_private_root_is_stable_across_agents_and_does_not_create_files(private_state):
    service, tenant_id, data_root, workspace = private_state
    expected = data_root / "tenants" / tenant_id
    for agent_id in ("alpha", "beta", None):
        with use_identity(RuntimeIdentity(tenant_id=tenant_id, agent_id=agent_id)):
            assert state_dir.tenant_app_data_root(
                tenant_id, identity_service=service,
            ) == expected
    assert not expected.exists()
    assert not workspace.exists()


def test_real_tenants_have_distinct_private_roots(private_state, tmp_path):
    service, tenant_id, data_root, _ = private_state
    admin = service.list_platform_users()[0]
    other = service.create_tenant(
        actor_user_id=admin["id"], code="beta", name="Beta",
        shared_root=str(tmp_path / "other-workspace"), admin_username="betaadmin",
        admin_display="Beta", admin_password="Str0ngBetaPass",
        recent_password="Str0ngAdminPass",
    )
    first_root = state_dir.tenant_app_data_root(tenant_id, identity_service=service)
    second_root = state_dir.tenant_app_data_root(other["id"], identity_service=service)
    assert first_root != second_root
    assert first_root.parent == second_root.parent == data_root / "tenants"


@pytest.mark.parametrize("tenant_id", [None, "", ".", "..", "../other", "/tmp/root", "a/b", "a\\b"])
def test_unsafe_tenant_id_is_refused(private_state, tenant_id):
    service, _, data_root, _ = private_state
    with pytest.raises(state_dir.StateDirError, match="invalid tenant id"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)
    assert not data_root.exists()


def test_missing_or_inactive_tenant_is_refused(private_state):
    service, tenant_id, data_root, _ = private_state
    with pytest.raises(state_dir.StateDirError, match="active tenant"):
        state_dir.tenant_app_data_root("tnt_missing", identity_service=service)
    with service._tx() as con:
        con.execute("UPDATE tenants SET active=0 WHERE id=?", (tenant_id,))
    with pytest.raises(state_dir.StateDirError, match="active tenant"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)
    assert not data_root.exists()


@pytest.mark.parametrize("relation", ["ancestor", "equal", "descendant"])
def test_private_root_must_not_overlap_any_agent_workspace(private_state, relation):
    service, tenant_id, data_root, workspace = private_state
    root = data_root / "tenants" / tenant_id
    overlap = {"ancestor": data_root, "equal": root, "descendant": root / "agent"}[relation]
    # Disabled Agents remain relevant: their workspaces are retained on disk.
    set_agent_registry(AgentRegistry([
        AgentProfile(id="alpha", name="Alpha", workspace=str(workspace)),
        AgentProfile(id="disabled", name="Disabled", workspace=str(overlap), enabled=False),
    ], "alpha"))
    with pytest.raises(state_dir.StateDirError, match="overlaps a workspace"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)
    assert not root.exists()


def test_private_root_must_not_overlap_a_tenant_shared_root(private_state):
    service, tenant_id, data_root, _ = private_state
    with service._tx() as con:
        con.execute("UPDATE tenants SET shared_root=? WHERE id=?", (str(data_root), tenant_id))
    with pytest.raises(state_dir.StateDirError, match="overlaps a workspace"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)


@pytest.mark.parametrize("component", ["namespace", "tenant"])
@pytest.mark.parametrize("target_exists", [False, True])
def test_private_root_refuses_symlink_components(private_state, tmp_path, component, target_exists):
    service, tenant_id, data_root, _ = private_state
    target = tmp_path / "redirected"
    if target_exists:
        target.mkdir()
    link = data_root / "tenants"
    if component == "tenant":
        link = link / tenant_id
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(state_dir.StateDirError, match="cannot be a symlink"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)


def test_workspace_symlink_cannot_hide_an_overlap(private_state, tmp_path):
    service, tenant_id, data_root, _ = private_state
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(data_root, target_is_directory=True)
    set_agent_registry(AgentRegistry([
        AgentProfile(id="alpha", name="Alpha", workspace=str(workspace_link)),
    ], "alpha"))
    with pytest.raises(state_dir.StateDirError, match="overlaps a workspace"):
        state_dir.tenant_app_data_root(tenant_id, identity_service=service)


@pytest.mark.parametrize("component", ["directory", "database"])
def test_todo_store_refuses_symlink_below_private_root(private_state, tmp_path, component):
    service, tenant_id, _, _ = private_state
    root = state_dir.tenant_app_data_root(tenant_id, identity_service=service)
    target = tmp_path / "redirected"
    link = root / "todo"
    if component == "database":
        link = link / "todos.db"
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=component == "directory")
    actor = TodoActor(
        bound=True, scope_id=tenant_id, owner_id="test-owner", is_legacy=False,
        permissions={"todo.read", "todo.write"},
    )
    with pytest.raises(TodoUnavailable, match="存储路径不可用"):
        TodoService(actor, app_data_root=str(root), enabled_fn=lambda: True)
    assert not target.exists()
