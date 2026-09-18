# encoding:utf-8
"""MCP tool identity across the legacy-file → control-plane migration, task 5.4.

Why this file exists
--------------------
``mcp.json`` names its tools ``<tool_name_prefix><remote name>``, and that
composed name is what the identity layer records as the tool's resource id
(``mcp:<server>:<tool>``) and what an Agent's allow/deny list quotes. The
migration moved such a server into the control plane and dropped
``tool_name_prefix`` on the way: the key is in the known-field list, so it never
appeared as a dropped field, and it never reached the connection's config
either. Every migrated tool therefore registered under a shorter name than
before, and every grant recorded against the old name stopped matching. The spec
is explicit that this must not happen (``mcp-connection-integration``: 迁移工具
身份 SHALL 保留已有显式授权映射):

    连接管理权限、测试成功或工具名称的 mcp 前缀 MUST NOT 作为执行授权。

Two claims have to hold at once, and they are asserted separately here:

1. **identity is preserved** — the prefix is a real, validated connection field
   and the name a migrated tool registers under is byte-identical to the legacy
   one, so an existing grant and an existing Agent binding keep working;
2. **the prefix grants nothing** — carrying ``mcp:`` in a resource id is not
   evidence of anything, and a caller without a grant is refused whichever
   spelling of the name they use.

The third part is the read path itself: ``ToolManager`` must stop serving an
``mcp.json`` entry from the file once the migration ledger says that record was
imported into a scope that no longer reads the legacy store. Otherwise the same
server is registered twice — once from the file, once from the control plane —
under two identities, and the file copy is the one the authorization gate does
not run behind (``mcp.json`` is trusted as configuration).
"""

from __future__ import annotations

import json
import os

import pytest

from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
ENV_SECRET = "tok-123"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def identity(stack):
    return stack.service


@pytest.fixture
def migration():
    from integrations.external import migration as module
    return module


@pytest.fixture
def identity_module():
    from integrations.external import mcp_identity as module
    return module


@pytest.fixture
def legacy_conf(monkeypatch):
    """Pin the migration's view of ``conf()``; store_version is the subject."""
    from integrations.external import migration as module

    settings = {}
    monkeypatch.setattr(module, "conf", lambda: settings)
    return settings


def mcp_record(**overrides):
    record = {
        "name": "filesystem",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"API_TOKEN": ENV_SECRET},
        "tool_name_prefix": "fs_",
    }
    record.update(overrides)
    return record


def write_mcp_file(path, servers):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"mcpServers": servers}, handle, ensure_ascii=False)
    return path


def mcp_file(tmp_path):
    return str(tmp_path / "cow" / "mcp.json")


# -- 1. identity is preserved -----------------------------------------------

def test_a_legacy_prefix_survives_the_import_as_a_real_field(
        identity, stack, migration, legacy_conf, tmp_path):
    """The prefix reaches the connection's config instead of vanishing.

    It is not a secret and not a display field: it is what makes a migrated
    tool's name the name its grant was recorded under, so dropping it is a
    silent authorization change rather than a cosmetic one.
    """
    path = write_mcp_file(mcp_file(tmp_path), {"filesystem": mcp_record()})
    legacy_conf["external_connections"] = {"store_version": "legacy"}

    report = migration.preflight(identity, mcp_files=[(path, stack.tenant_id)])
    assert report["records"][0]["importable"] is True
    assert report["records"][0]["dropped_fields"] == []

    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True,
        mcp_files=[(path, stack.tenant_id)])

    row = identity._store.execute(
        "SELECT config_json FROM external_connections WHERE kind='mcp'"
        " AND deleted_at IS NULL")[0]
    assert json.loads(row["config_json"])["tool_name_prefix"] == "fs_"


def test_the_registered_tool_name_is_the_legacy_name_after_migration(
        identity, stack, migration, legacy_conf, tmp_path, identity_module):
    """The name the model calls is the same string before and after.

    ``McpTool`` composes ``name_prefix + remote name``, so reproducing the
    prefix reproduces the name — and with it ``mcp:<server>:<tool>``, the id a
    grant and an Agent allowlist quote.
    """
    path = write_mcp_file(mcp_file(tmp_path), {"filesystem": mcp_record()})
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True,
        mcp_files=[(path, stack.tenant_id)])

    config = json.loads(identity._store.execute(
        "SELECT config_json FROM external_connections WHERE kind='mcp'"
        " AND deleted_at IS NULL")[0]["config_json"])

    assert identity_module.registered_name(config, "read_file") == "fs_read_file"
    # ...and the id the identity layer records for it is unchanged too.
    assert identity_module.legacy_id("filesystem", "fs_read_file") == \
        "mcp:filesystem:fs_read_file"


def test_identity_aliases_name_both_the_stable_and_the_legacy_id(identity_module):
    """A grant may have been recorded under either spelling; both are offered.

    The stable id keys on the connection (the spec's 与连接稳定标识关联的身份, and
    what survives the connection being re-created), the legacy id keys on the
    server name the grant was written against. Offering only one would orphan the
    grants recorded under the other.
    """
    assert identity_module.aliases(
        connection_id="conn_abc", server_name="filesystem",
        config={"tool_name_prefix": "fs_"}, remote_name="read_file") == (
        "mcp:conn_abc:read_file", "mcp:filesystem:fs_read_file")


def test_an_empty_or_missing_prefix_registers_the_bare_remote_name(identity_module):
    assert identity_module.registered_name({}, "read_file") == "read_file"
    assert identity_module.registered_name(
        {"tool_name_prefix": ""}, "read_file") == "read_file"


@pytest.mark.parametrize("bad", [
    "has space", "a" * 65, "with/slash", "with:colon:here", "new\nline",
])
def test_an_unusable_prefix_is_refused_at_the_schema(bad):
    """A prefix becomes part of a tool name and of a grant id, so it is bounded.

    A prefix carrying whitespace, a ``:`` or a length no prompt can show would
    compose an id that cannot be quoted unambiguously — the same reason the
    composed external-tool name refuses a separator in a connection id.
    """
    from integrations.external import registry
    from integrations.external.errors import ExternalConnectionError

    with pytest.raises(ExternalConnectionError):
        registry.validate_config("mcp", {
            "transport": "stdio", "command": "npx",
            "tool_name_prefix": bad,
        })


def test_the_split_of_a_legacy_id_never_invents_a_connection(identity_module):
    """Malformed ids answer ``None`` rather than a guessed pair."""
    assert identity_module.split_legacy_id("mcp:server:tool") == ("server", "tool")
    assert identity_module.split_legacy_id("mcp:server:pre.tool") == \
        ("server", "pre.tool")
    assert identity_module.split_legacy_id("builtin:web_search") is None
    assert identity_module.split_legacy_id("mcp:server") is None
    assert identity_module.split_legacy_id("mcp:") is None
    assert identity_module.split_legacy_id("") is None


# -- 2. the read path stops serving migrated entries -------------------------

def test_the_file_still_serves_an_entry_that_was_never_imported(
        identity, migration, legacy_conf, tmp_path):
    """Nothing disappears just because the registry learned about a ledger."""
    path = write_mcp_file(mcp_file(tmp_path), {"filesystem": mcp_record()})
    legacy_conf["external_connections"] = {"store_version": "legacy"}

    view = migration.resolve_mcp_servers(identity, path=path)
    assert view["source"] == "legacy"
    assert [entry["name"] for entry in view["servers"]] == ["filesystem"]
    assert view["migrated"] == []
    # The prefix is handed to the loader, so the name is composed as before.
    assert view["servers"][0]["tool_name_prefix"] == "fs_"


def test_the_file_stops_serving_an_entry_once_its_scope_left_legacy(
        identity, stack, migration, legacy_conf, tmp_path):
    """One server, one identity: the control plane takes over the entry.

    After the import the record lives in the new store under a tenant scope, and
    those are delivered per tenant. The instance-wide registry must not keep a
    second copy from the file.
    """
    path = write_mcp_file(mcp_file(tmp_path), {"filesystem": mcp_record()})
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True,
        mcp_files=[(path, stack.tenant_id)])

    # Still legacy: the file is what the runtime reads.
    before = migration.resolve_mcp_servers(identity, path=path)
    assert [entry["name"] for entry in before["servers"]] == ["filesystem"]

    legacy_conf["external_connections"] = {"store_version": "new"}
    after = migration.resolve_mcp_servers(identity, path=path)
    assert after["source"] == "new"
    assert after["servers"] == []
    assert [item["name"] for item in after["migrated"]] == ["filesystem"]
    assert after["migrated"][0]["scope_key"] == "tenant:%s" % stack.tenant_id


def test_an_entry_that_was_not_importable_still_serves_from_the_file(
        identity, stack, migration, legacy_conf, tmp_path):
    """A record the migration refused is not silently deleted.

    It has no ledger ``imported`` row, so switching the scope must not make it
    disappear: an operator who could not import a record still gets it from the
    file — the "reported, not defaulted" rule.
    """
    path = write_mcp_file(mcp_file(tmp_path), {
        # Two env values cannot be split into the one ``env`` slot the control
        # plane models, so this record is reported rather than imported.
        "filesystem": mcp_record(env={"A": "1", "B": "2"}),
    })
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True,
        mcp_files=[(path, stack.tenant_id)])

    legacy_conf["external_connections"] = {"store_version": "new"}
    view = migration.resolve_mcp_servers(identity, path=path)
    assert [entry["name"] for entry in view["servers"]] == ["filesystem"]
    assert view["migrated"] == []


def test_the_marker_changes_when_the_scope_switches_so_the_refresh_notices(
        identity, stack, migration, legacy_conf, tmp_path):
    """``refresh_mcp_if_changed`` compares this marker, so it must move.

    The file's own ``(mtime, sha256)`` cannot see a store switch or a console
    edit of an imported connection, and a refresh that never fires would leave
    the file-derived tools registered for the rest of the process's life.
    """
    path = write_mcp_file(mcp_file(tmp_path), {"filesystem": mcp_record()})
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True,
        mcp_files=[(path, stack.tenant_id)])

    legacy = migration.resolve_mcp_servers(identity, path=path)["marker"]
    legacy_conf["external_connections"] = {"store_version": "new"}
    switched = migration.resolve_mcp_servers(identity, path=path)["marker"]
    assert legacy != switched

    # A console edit of the imported connection moves it again, with the file
    # untouched.
    from integrations.external.service import ExternalConnectionService
    service = ExternalConnectionService(identity)
    row = identity._store.execute(
        "SELECT * FROM external_connections WHERE kind='mcp'"
        " AND deleted_at IS NULL")[0]
    service.update_connection(
        actor_user_id=stack.root, connection_id=row["id"], scope=row["scope"],
        tenant_id=row["tenant_id"], expected_version=int(row["version"]),
        config={"transport": "stdio", "command": "npx",
                "args": ["-y", "other"], "tool_name_prefix": "fs_"})
    assert migration.resolve_mcp_servers(identity, path=path)["marker"] != switched


def test_a_missing_file_is_an_empty_view_not_an_error(
        identity, migration, legacy_conf, tmp_path):
    legacy_conf["external_connections"] = {"store_version": "new"}
    view = migration.resolve_mcp_servers(
        identity, path=str(tmp_path / "absent" / "mcp.json"))
    assert view["servers"] == []
    assert view["migrated"] == []


# -- 3. ToolManager reads through the resolver -------------------------------

@pytest.fixture
def manager(tmp_path, monkeypatch):
    from config import conf
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setitem(conf(), "agent_workspace", str(tmp_path))
    ToolManager.reset_instances()
    tm = ToolManager()
    tm.tool_classes = {}
    yield tm
    ToolManager.reset_instances()


def test_the_tool_manager_config_read_asks_the_resolver_first(manager, monkeypatch):
    """The file is not read in preference to the control plane.

    Pinned to the seam rather than to the outcome so the wiring itself is what
    is asserted: a resolver that is never called is the defect this task names.
    """
    path = manager._mcp_json_path()
    write_mcp_file(path, {"filesystem": mcp_record()})

    calls = []

    def _resolver(seen_path):
        calls.append(seen_path)
        return {"source": "new", "servers": [],
                "migrated": [{"name": "filesystem", "scope_key": "tenant:t1",
                              "connection_id": "conn_1"}],
                "marker": "m1"}

    monkeypatch.setattr("agent.tools.tool_manager._resolve_mcp_store", _resolver)
    assert manager._load_mcp_configs() == []
    assert calls == [path]
    assert manager._mcp_migrated_names() == {"filesystem"}


def test_a_failing_resolver_leaves_the_file_read_intact(manager, monkeypatch):
    """Fail *safe* here means the file: ``store_version`` unset is ``legacy``.

    An unreadable ledger must not be the reason a working server disappears.
    """
    path = manager._mcp_json_path()
    write_mcp_file(path, {"filesystem": mcp_record()})

    def _boom(seen_path):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("agent.tools.tool_manager._resolve_mcp_store", _boom)
    assert [cfg["name"] for cfg in manager._load_mcp_configs()] == ["filesystem"]
    assert manager._mcp_migrated_names() == set()


def test_the_refresh_signature_carries_the_store_marker(manager, monkeypatch):
    """A store switch alone must make the next agent creation reload MCP.

    The file is unchanged in this test, so only the resolver's marker can make
    the composite signature differ.
    """
    path = manager._mcp_json_path()
    write_mcp_file(path, {"filesystem": mcp_record()})

    state = {"marker": "before"}

    def _resolver(seen_path):
        return {"source": "new", "servers": [], "migrated": [],
                "marker": state["marker"]}

    monkeypatch.setattr("agent.tools.tool_manager._resolve_mcp_store", _resolver)
    manager._load_mcp_tools()
    assert manager._mcp_signature == manager._mcp_store_signature()
    assert manager._mcp_signature[-1] == "before"

    state["marker"] = "after"
    assert manager._mcp_store_signature() != manager._mcp_signature


# -- 4. the prefix is not an authorization -----------------------------------

def test_a_name_prefix_authorizes_nothing(identity, stack):
    """``mcp:`` in a resource id is a namespace, not a permission.

    A member with no grant is refused for the MCP tool whose name carries the
    legacy prefix, and the tenant-admin exemption is refused for them too when
    no Agent binds them to the tenant — which is the half that stops the id from
    being its own proof.
    """
    member = stack.member("carol", ["member"])

    rid = "mcp:filesystem:fs_read_file"
    assert identity.check_resource_action(
        member, stack.tenant_id, "tool", rid, "execute",
        permission="tool.execute") is False
    assert identity.tenant_admin_may_execute_tool(
        member, stack.tenant_id, rid, agent_id=None) is False
    assert identity.tenant_admin_may_execute_tool(
        member, stack.tenant_id, rid, agent_id="someone-elses-agent") is False
