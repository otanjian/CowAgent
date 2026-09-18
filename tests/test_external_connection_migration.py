# encoding:utf-8
"""Legacy connection migration, task group 12.

The migration has three jobs, and each is asserted here against the *real*
identity store and the real :class:`ExternalConnectionService`:

* **preflight** finds every legacy record (file-based ERP store, legacy MCP
  configuration), reports its source locator, a content hash, the target
  ``(kind, scope, tenant)`` and whether it can be imported — and writes nothing,
  ever, in any of the read-only entry points;
* **import** is idempotent — it creates exactly one connection per importable
  record even when two importers race or a partial failure is retried, records
  the outcome in ``external_connection_migrations``, reports what it could not
  import, and never mutates or deletes the legacy store;
* the **``store_version`` switch** decides which store the runtime reads,
  defaults to the legacy-safe value, is re-read without a restart, and refuses
  ``new`` while importable legacy records are unmigrated.

Ownership is derived, never guessed: a legacy record that does not name a tenant
is reported as ``ownership_unknown`` instead of being quietly assigned to the
default tenant.  A report never contains a secret value, only slot names and
presence.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
SECRET = "S3cretPassw0rd!"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def migration():
    from integrations.external import migration as module
    return module


@pytest.fixture
def identity(stack):
    """The migration talks to the identity service, not the test harness."""
    return stack.service


@pytest.fixture
def legacy_conf(monkeypatch):
    """Pin ``conf()`` as seen by the migration module.

    The module binds ``config.conf`` at import time, so patching it there is what
    changes ``store_version`` for the tests — and keeping it off the developer's
    real config makes the switch tests deterministic.
    """
    from integrations.external import migration as module

    settings = {}
    monkeypatch.setattr(module, "conf", lambda: settings)
    return settings


# -- legacy fixtures ---------------------------------------------------------

def erp_path(stack):
    return os.path.join(stack.shared_root, ".one", "erp_connections.json")


def write_erp(stack, records):
    path = erp_path(stack)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False)
    return path


def write_mcp(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def erp_record(**overrides):
    """A shape the file-based store really holds (``api.py`` reads these keys)."""
    record = {
        "id": "erp-1",
        "name": "SAP 生产",
        "system": "PRD",
        "provider": "rfc",
        "ashost": "sap.example.com",
        "sysnr": "00",
        "client": "100",
        "lang": "EN",
        "username": "sapuser",
        "password": SECRET,
        "is_default": False,
    }
    record.update(overrides)
    return record


def mcp_stdio_record(**overrides):
    record = {
        "name": "filesystem",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"API_TOKEN": "tok-123"},
    }
    record.update(overrides)
    return record


def mcp_remote_record(**overrides):
    record = {
        "name": "remote-tools",
        "type": "sse",
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer abc"},
    }
    record.update(overrides)
    return record


def db_count(identity, table):
    return identity._store.execute("SELECT COUNT(*) c FROM %s" % table)[0]["c"]


def client_rows(identity):
    return identity._store.execute(
        "SELECT id, kind, scope, tenant_id, name, config_json FROM"
        " external_connections WHERE deleted_at IS NULL ORDER BY name")


def ledger_rows(identity):
    return identity._store.execute(
        "SELECT source_hash, scope_key, result, mapping_json, detail_json,"
        " batch_id FROM external_connection_migrations ORDER BY source_hash")


def audit_creates(identity):
    return identity._store.execute(
        "SELECT COUNT(*) c FROM audit_events WHERE"
        " action='external_connection.create'")[0]["c"]


def by_name(records):
    return {record["name"]: record for record in records}


# -- preflight is read-only --------------------------------------------------

def test_preflight_finds_erp_and_mcp_records_and_writes_nothing(
        identity, stack, migration):
    path = write_erp(stack, [erp_record()])
    mcp = write_mcp(os.path.join(str(stack.shared_root), "mcp.json"),
                    {"mcpServers": {"filesystem": mcp_stdio_record()}})
    before = open(path, "rb").read()
    mcp_before = open(mcp, "rb").read()

    report = migration.preflight(
        identity, mcp_files=[(mcp, stack.tenant_id)])

    assert report["writable"] is False
    assert {source["locator"] for source in report["sources"]} >= {path, mcp}
    records = by_name(report["records"])
    assert set(records) == {"SAP 生产", "filesystem"}

    erp = records["SAP 生产"]
    assert erp["kind"] == "erp" and erp["scope"] == "tenant"
    assert erp["tenant_id"] == stack.tenant_id
    assert erp["importable"] is True and erp["reason"] == ""
    assert erp["secret_slots"] == ["password"]
    assert erp["secret_present"] == {"password": True}

    mcp_record = records["filesystem"]
    assert mcp_record["kind"] == "mcp"
    assert mcp_record["secret_slots"] == ["env"]
    assert mcp_record["source_format"] == migration.MCP_FORMAT

    # A report is not a place a secret can appear, in any field.
    assert SECRET not in json.dumps(report, default=str)
    assert "tok-123" not in json.dumps(report, default=str)

    # Nothing was written — not the legacy files, not the control plane.
    assert open(path, "rb").read() == before
    assert open(mcp, "rb").read() == mcp_before
    assert db_count(identity, "external_connections") == 0
    assert db_count(identity, "external_connection_migrations") == 0
    assert db_count(identity, "external_connection_idempotency") == 0
    assert audit_creates(identity) == 0


def test_preflight_is_repeatable_and_hash_is_key_order_independent(
        identity, stack, migration):
    path = write_erp(stack, [erp_record()])
    first = migration.preflight(identity)
    second = migration.preflight(identity)
    assert [r["source_hash"] for r in first["records"]] == \
        [r["source_hash"] for r in second["records"]]

    # Same values, keys written in a different order: same record, same hash.
    reordered = erp_record(**{})
    shuffled = {key: reordered[key] for key in sorted(reordered, reverse=True)}
    write_erp(stack, [shuffled])
    third = migration.preflight(identity)
    assert third["records"][0]["source_hash"] == first["records"][0]["source_hash"]

    # A changed secret is a changed record, so a retry re-imports rather than
    # treating the old row as identical.
    write_erp(stack, [erp_record(password="An0therPass!")])
    fourth = migration.preflight(identity)
    assert fourth["records"][0]["source_hash"] != first["records"][0]["source_hash"]
    assert os.path.exists(path)


# -- records that must not be imported ---------------------------------------

def test_unsupported_provider_is_reported_not_imported(
        identity, stack, migration):
    write_erp(stack, [erp_record(provider="u9", name="用友 U9")])

    report = migration.preflight(identity)
    record = report["records"][0]
    assert record["importable"] is False
    assert record["reason"] == migration.REASON_UNSUPPORTED_PROVIDER
    assert record["reason_detail"]["provider"] == "u9"

    result = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert result["summary"] == {migration.RESULT_SKIPPED_UNIMPORTABLE: 1}
    assert db_count(identity, "external_connections") == 0
    assert ledger_rows(identity) == []

    verification = migration.verify(identity)
    assert verification["ok"] is True  # permanently unimportable, never imported
    assert verification["unmapped"][0]["reason"] == \
        migration.REASON_UNSUPPORTED_PROVIDER


def test_missing_secret_is_reported_not_imported(identity, stack, migration):
    write_erp(stack, [erp_record(password="", name="无密码")])
    mcp = write_mcp(os.path.join(str(stack.shared_root), "mcp.json"),
                    [mcp_stdio_record(name="no-token", env={"API_TOKEN": ""})])

    report = migration.preflight(identity, mcp_files=[(mcp, stack.tenant_id)])
    records = by_name(report["records"])
    assert records["无密码"]["reason"] == migration.REASON_SECRET_MISSING
    assert records["no-token"]["reason"] == migration.REASON_SECRET_MISSING
    assert SECRET not in json.dumps(report, default=str)

    result = migration.import_connections(
        identity, actor_user_id=stack.root, mcp_files=[(mcp, stack.tenant_id)],
        confirm=True)
    assert result["summary"] == {migration.RESULT_SKIPPED_UNIMPORTABLE: 2}
    assert db_count(identity, "external_connections") == 0

    verification = migration.verify(
        identity, mcp_files=[(mcp, stack.tenant_id)])
    assert verification["ok"] is False  # a required secret is a real blocker
    assert {item["reason"] for item in verification["unmapped"]} == {
        migration.REASON_SECRET_MISSING}
    # No secret *presence* was lost — these records never had a value to carry —
    # so the gap list is empty while the records themselves are still unmapped.
    assert verification["secret_gaps"] == []


def test_ownership_that_cannot_be_derived_is_reported_not_defaulted(
        identity, stack, migration):
    mcp = write_mcp(os.path.join(str(stack.shared_root), "orphan.json"),
                    [mcp_stdio_record(name="orphan-mcp")])

    report = migration.preflight(identity, mcp_files=[mcp])
    record = report["records"][0]
    assert record["tenant_id"] is None
    assert record["importable"] is False
    assert record["reason"] == migration.REASON_OWNERSHIP_UNKNOWN

    result = migration.import_connections(
        identity, actor_user_id=stack.root, mcp_files=[mcp], confirm=True)
    assert result["summary"] == {migration.RESULT_SKIPPED_UNIMPORTABLE: 1}
    assert db_count(identity, "external_connections") == 0


def test_name_collision_with_a_live_row_is_reported(identity, stack, migration):
    from integrations.external.service import ExternalConnectionService

    write_erp(stack, [erp_record(name="SAP 生产")])
    service = ExternalConnectionService(identity)
    service.create_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=stack.tenant_id,
        kind="erp", name="SAP 生产",
        config={"provider": "rfc", "ashost": "other.example.com", "sysnr": "01",
                "client": "200", "user": "other", "lang": "EN"},
        secrets={"password": "An0therPass!"})

    report = migration.preflight(identity)
    record = report["records"][0]
    assert record["importable"] is False
    assert record["reason"] == migration.REASON_NAME_COLLISION


# -- import ------------------------------------------------------------------

def test_import_creates_exactly_one_connection_per_record(
        identity, stack, migration):
    path = write_erp(stack, [erp_record(is_default=True)])
    mcp = write_mcp(os.path.join(str(stack.shared_root), "mcp.json"),
                    [mcp_stdio_record(), mcp_remote_record()])
    before = open(path, "rb").read()
    mcp_before = open(mcp, "rb").read()

    report = migration.import_connections(
        identity, actor_user_id=stack.root,
        mcp_files=[(mcp, stack.tenant_id)], confirm=True)

    assert report["summary"].get(migration.RESULT_IMPORTED) == 3
    assert not [item for item in report["results"]
                if item["result"] not in (migration.RESULT_IMPORTED,)]

    rows = client_rows(identity)
    assert len(rows) == 3
    assert {row["kind"] for row in rows} == {"erp", "mcp"}
    erp_row = [row for row in rows if row["kind"] == "erp"][0]
    assert erp_row["tenant_id"] == stack.tenant_id
    assert erp_row["scope"] == "tenant"
    # The non-secret projection carries the mapped config and no secret.
    config = json.loads(erp_row["config_json"])
    assert config["provider"] == "rfc" and config["ashost"] == "sap.example.com"
    assert SECRET not in erp_row["config_json"]

    ledger = ledger_rows(identity)
    assert len(ledger) == 3
    assert {row["result"] for row in ledger} == {migration.RESULT_IMPORTED}
    mappings = {json.loads(row["mapping_json"])["legacy_id"] for row in ledger}
    assert mappings == {"erp-1", "filesystem", "remote-tools"}

    assert audit_creates(identity) == 3

    # Secrets were carried by reference and are still recoverable.
    assert identity.__class__ is not None  # identity is the real service
    from integrations.external.service import ExternalConnectionService
    service = ExternalConnectionService(identity)
    assert service.resolve_secret(
        connection_id=erp_row["id"], slot="password", scope="tenant",
        tenant_id=stack.tenant_id) == SECRET

    # The legacy store is untouched.
    assert open(path, "rb").read() == before
    assert open(mcp, "rb").read() == mcp_before


def test_legacy_default_is_applied_once(identity, stack, migration):
    write_erp(stack, [erp_record(is_default=True)])
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    from integrations.external.service import ExternalConnectionService

    service = ExternalConnectionService(identity)
    current = service.get_erp_default(
        actor_user_id=stack.root, tenant_id=stack.tenant_id)
    connection = [
        row for row in client_rows(identity) if row["kind"] == "erp"][0]
    assert current["connection_id"] == connection["id"]

    # A second run must not churn the default.
    again = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert again["erp_defaults"][0]["reason"] == "unchanged"
    assert service.get_erp_default(
        actor_user_id=stack.root, tenant_id=stack.tenant_id
    )["connection_id"] == connection["id"]


def test_second_import_is_a_no_op(identity, stack, migration):
    write_erp(stack, [erp_record()])
    first = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert first["summary"] == {migration.RESULT_IMPORTED: 1}

    second = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert second["summary"] == {migration.RESULT_SKIPPED_IDENTICAL: 1}
    assert db_count(identity, "external_connections") == 1
    assert len(ledger_rows(identity)) == 1
    # No duplicate secret reference, no duplicate audit event.
    assert audit_creates(identity) == 1
    assert db_count(identity, "external_connection_secret_refs") >= 1
    refs = identity._store.execute(
        "SELECT COUNT(*) c FROM credentials WHERE resource_kind='external_connection'"
    )[0]["c"]
    assert refs == 1


def test_concurrent_double_import_produces_one_connection(
        identity, stack, migration):
    write_erp(stack, [erp_record()])

    def run():
        return migration.import_connections(
            identity, actor_user_id=stack.root, confirm=True,
            wait_seconds=20.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = [f.result() for f in [pool.submit(run), pool.submit(run)]]

    assert db_count(identity, "external_connections") == 1
    assert len(ledger_rows(identity)) == 1
    assert audit_creates(identity) == 1

    results = [item["result"] for report in reports
               for item in report["results"]]
    # One importer wins and creates; the other reads the winner's mapping and
    # reports success rather than a failure or a second connection.
    assert results.count(migration.RESULT_IMPORTED) == 1
    assert results.count(migration.RESULT_SKIPPED_IDENTICAL) == 1
    assert migration.RESULT_FAILED not in results

    winner = [item for report in reports for item in report["results"]
              if item["result"] == migration.RESULT_IMPORTED][0]
    ones = client_rows(identity)
    assert winner["connection_id"] == ones[0]["id"]


def test_partial_failure_then_retry_resumes(identity, stack, migration):
    from integrations.external.service import ExternalConnectionService

    write_erp(stack, [erp_record(id="erp-1", name="第一个"),
                      erp_record(id="erp-2", name="第二个")])

    class FlakyService:
        """Fails the first create, then behaves like the real service."""

        def __init__(self, real):
            self._real = real
            self.calls = 0

        def create_connection(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated partial failure")
            return self._real.create_connection(**kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    flaky = FlakyService(ExternalConnectionService(identity))
    first = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True, service=flaky)
    assert first["summary"].get(migration.RESULT_IMPORTED) == 1
    assert first["summary"].get(migration.RESULT_FAILED) == 1
    assert db_count(identity, "external_connections") == 1
    ledger = {row["result"] for row in ledger_rows(identity)}
    assert ledger == {migration.RESULT_IMPORTED, migration.RESULT_FAILED}

    # The retry reclaims the failed row and imports only that one.
    second = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert second["summary"] == {migration.RESULT_IMPORTED: 1,
                                 migration.RESULT_SKIPPED_IDENTICAL: 1}
    assert db_count(identity, "external_connections") == 2
    assert len(ledger_rows(identity)) == 2
    assert {row["result"] for row in ledger_rows(identity)} == {
        migration.RESULT_IMPORTED}


def test_import_requires_explicit_confirmation(identity, stack, migration):
    write_erp(stack, [erp_record()])
    with pytest.raises(migration.MigrationError):
        migration.import_connections(identity, actor_user_id=stack.root)
    assert db_count(identity, "external_connections") == 0


def test_imported_connection_keeps_legacy_record_readable(
        identity, stack, migration):
    path = write_erp(stack, [erp_record()])
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    # A later preflight still sees the legacy record, flagged as imported.
    report = migration.preflight(identity)
    assert report["records"][0]["imported"] is True
    assert report["records"][0]["importable"] is True
    assert json.load(open(path, encoding="utf-8"))[0]["id"] == "erp-1"


# -- the legacy id survives as the new key (task 12.2) ----------------------
#
# Design §166: "ERP 复用原 ID，MCP 保留旧工具别名与 Agent 绑定". Reusing the id is
# what keeps a stored reference — a scene's saved default, an operator's runbook,
# an incident note — pointing at the same connection after the cutover. A
# migration that generated new ids would report success while every one of those
# references silently resolved to nothing.

def test_an_erp_record_keeps_its_legacy_id_as_the_new_primary_key(
        migration, identity, stack, legacy_conf):
    write_erp(stack, [erp_record(id="erp_1788335871905_w3pnj7")])
    report = migration.import_connections(identity,
                                          actor_user_id=stack.root,
                                          confirm=True)

    assert report["summary"].get("imported") == 1, report
    assert report["results"][0]["reused_id"] is True
    ids = {row["id"] for row in identity._store.execute(
        "SELECT id FROM external_connections")}
    assert ids == {"erp_1788335871905_w3pnj7"}
    # The ledger names both, so the mapping is readable even when they are equal.
    ledger = identity._store.execute(
        "SELECT mapping_json FROM external_connection_migrations")[0]
    mapping = json.loads(ledger["mapping_json"])
    assert mapping["legacy_id"] == "erp_1788335871905_w3pnj7"
    assert mapping["connection_id"] == "erp_1788335871905_w3pnj7"
    assert mapping["reused_id"] is True


def test_a_legacy_id_that_cannot_be_a_key_is_reported_not_refused(
        migration, identity, stack, legacy_conf):
    """An unusable legacy id must not cost the record its migration.

    Refusing the import over an id shape would leave the connection behind in a
    file the cutover is about to stop reading — worse than the reference churn
    the reuse avoids. So a generated id is used and the report says so.
    """
    write_erp(stack, [erp_record(id="not a key: spaces/and:slashes")])
    report = migration.import_connections(identity,
                                          actor_user_id=stack.root,
                                          confirm=True)

    assert report["summary"].get("imported") == 1, report
    assert report["results"][0]["reused_id"] is False
    assert report["results"][0]["connection_id"].startswith("conn_")
    ledger = identity._store.execute(
        "SELECT mapping_json FROM external_connection_migrations")[0]
    mapping = json.loads(ledger["mapping_json"])
    # The original id is still recorded: what was reused is a detail, what the
    # record came from is not.
    assert mapping["legacy_id"] == "not a key: spaces/and:slashes"
    assert mapping["connection_id"] != mapping["legacy_id"]


def test_reusing_an_id_that_is_already_taken_is_reported_as_a_conflict(
        migration, identity, stack, legacy_conf):
    """A collision must not be able to overwrite an existing connection."""
    from integrations.external.service import ExternalConnectionService

    service = ExternalConnectionService(identity)
    existing = service.create_connection(
        actor_user_id=stack.root, scope="tenant", kind="erp",
        tenant_id=stack.tenant_id, name="Already here",
        config={"provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
                "client": "100", "user": "sapuser", "lang": "EN"},
        secrets={"password": SECRET}, connection_id="erp-taken")
    write_erp(stack, [erp_record(id="erp-taken")])

    report = migration.import_connections(identity,
                                          actor_user_id=stack.root,
                                          confirm=True)
    assert report["results"][0]["result"] != "imported", report
    still = service.get_connection(actor_user_id=stack.root, scope="tenant",
                                   connection_id=existing["id"],
                                   tenant_id=stack.tenant_id)
    assert still["name"] == "Already here"


def test_the_console_and_the_api_never_choose_their_own_primary_key():
    """``connection_id`` is a migration-only parameter.

    The reuse is deliberately not a field a client can set: a caller that could
    name its own key could aim at another tenant's id and turn a create into an
    overwrite. It is read off the *record* by the import, which is the only
    caller that knows a legacy id and is already trusted with the store.
    """
    import inspect

    from integrations.external import backup as backup_module  # noqa: F401
    from integrations.external import service as service_module

    params = inspect.signature(
        service_module.ExternalConnectionService.create_connection).parameters
    assert params["connection_id"].default is None
    assert params["connection_id"].kind is inspect.Parameter.KEYWORD_ONLY

    # No HTTP handler passes it: parsed rather than grepped, because the string
    # ``connection_id=connection_id`` occurs legitimately where a handler
    # forwards an id it was *given*, which is a different thing from choosing
    # the id of a row that does not exist yet.
    import ast

    from channel.web import external_connection_handlers as handlers

    source = open(handlers.__file__, encoding="utf-8").read()
    assert "reuse_id" not in source
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", "") or getattr(func, "id", "")
        if name != "create_connection":
            continue
        keywords = {kw.arg for kw in node.keywords}
        assert "connection_id" not in keywords


def test_the_mcp_alias_and_binding_identifiers_are_carried_through(
        migration, identity, stack, legacy_conf):
    """MCP keeps the prefix that composes every tool name it contributes.

    The prefix *is* the resource id an existing grant was filed under, so
    dropping it renames every tool and orphans exactly the grants the spec says
    the migration must preserve. The source's Agent binding is recorded on the
    report for the same reason: it is what the operator checks the grants
    against.
    """
    path = os.path.join(stack.shared_root, "mcp.json")
    write_mcp(path, [mcp_stdio_record(name="filesystem",
                                      tool_name_prefix="fs")])
    sources = [(path, stack.tenant_id)]
    report = migration.preflight(identity, mcp_files=sources)

    assert report["summary"]["importable"] == 1, report
    record = next(r for r in report["records"] if r["importable"])
    assert record["kind"] == "mcp"
    assert "tool_name_prefix" in record["config_keys"]

    imported = migration.import_connections(identity, actor_user_id=stack.root,
                                           mcp_files=sources, confirm=True)
    assert imported["summary"].get("imported") == 1, imported
    row = identity._store.execute(
        "SELECT config_json FROM external_connections WHERE kind='mcp'")[0]
    config = json.loads(row["config_json"])
    assert config["tool_name_prefix"] == "fs"

    # The point of preserving the prefix: the resource id a grant written before
    # the migration names is still one of the ids the migrated tool answers to.
    # Without it the prefix would be gone and every such grant would be an
    # orphan — and 不能批量放权 forbids re-granting them as the fix.
    from integrations.external import mcp_identity

    aliases = mcp_identity.aliases(
        connection_id=imported["results"][0]["connection_id"],
        server_name="filesystem", config=config, remote_name="read_file")
    assert "mcp:filesystem:fsread_file" in aliases, aliases
    assert mcp_identity.split_legacy_id("mcp:filesystem:fsread_file") == (
        "filesystem", "fsread_file")


# -- store_version switch ----------------------------------------------------

def test_store_version_defaults_to_legacy_and_ignores_garbage(
        identity, legacy_conf, migration):
    assert migration.active_store_version() == migration.STORE_LEGACY
    legacy_conf["external_connections"] = {}
    assert migration.active_store_version() == migration.STORE_LEGACY
    legacy_conf["external_connections"] = {"store_version": "banana"}
    assert migration.active_store_version() == migration.STORE_LEGACY
    legacy_conf["external_connections"] = {"store_version": [1, 2]}
    assert migration.active_store_version() == migration.STORE_LEGACY


def test_store_version_is_reread_without_restart(legacy_conf, migration):
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    assert migration.active_store_version() == "legacy"
    legacy_conf["external_connections"] = {"store_version": "dual"}
    assert migration.active_store_version() == "dual"
    legacy_conf["external_connections"] = {"store_version": "new"}
    assert migration.active_store_version() == "new"
    # Per-scope overrides resolve too.
    legacy_conf["external_connections"] = {
        "store_version": {"default": "legacy", "tenant:t1": "new"}}
    assert migration.active_store_version() == "legacy"
    assert migration.active_store_version("tenant:t1") == "new"


def test_read_effective_connections_switches_between_stores(
        identity, stack, legacy_conf, migration):
    path = write_erp(stack, [erp_record()])
    legacy = migration.LegacySource(
        locator=path, format=migration.ERP_FORMAT, tenant_id=stack.tenant_id)

    legacy_conf["external_connections"] = {"store_version": "legacy"}
    before = migration.read_effective_connections(
        identity, kind="erp", scope="tenant", tenant_id=stack.tenant_id,
        sources=[legacy])
    assert before["source"] == "legacy"
    assert [item["origin"] for item in before["connections"]] == ["legacy"]

    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)

    legacy_conf["external_connections"] = {"store_version": "new"}
    after = migration.read_effective_connections(
        identity, kind="erp", scope="tenant", tenant_id=stack.tenant_id,
        sources=[legacy])
    assert after["source"] == "new"
    assert len(after["connections"]) == 1  # a migrated deployment is not "empty"
    assert after["connections"][0]["origin"] == "new"


def test_new_with_unimported_legacy_reads_closed_not_empty_json(
        identity, stack, legacy_conf, migration):
    path = write_erp(stack, [erp_record()])
    legacy_conf["external_connections"] = {"store_version": "new"}

    # The check refuses the switch with a machine-readable reason...
    report = migration.validate_store_version(identity)
    assert report["ok"] is False
    assert report["reason"] == "new_store_with_unimported_legacy"
    assert len(report["pending"]) == 1
    with pytest.raises(migration.MigrationError) as caught:
        migration.assert_store_version_safe(identity)
    assert "new_store_with_unimported_legacy" in str(caught.value)

    # ...and a runtime read fails closed rather than silently reading JSON.
    legacy = migration.LegacySource(
        locator=path, format=migration.ERP_FORMAT, tenant_id=stack.tenant_id)
    view = migration.read_effective_connections(
        identity, kind="erp", scope="tenant", tenant_id=stack.tenant_id,
        sources=[legacy])
    assert view["source"] == "new"
    assert view["connections"] == []
    assert [item["reason"] for item in view["disagreements"]] == \
        ["new_store_empty"]

    # After the import the switch is safe.
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert migration.validate_store_version(identity)["ok"] is True


def test_dual_prefers_the_new_store_and_reports_the_disagreement(
        identity, stack, legacy_conf, migration):
    path = write_erp(stack, [erp_record()])
    legacy = migration.LegacySource(
        locator=path, format=migration.ERP_FORMAT, tenant_id=stack.tenant_id)
    legacy_conf["external_connections"] = {"store_version": "dual"}

    # Before the import the new store has no answer, so legacy still serves —
    # nothing disappears just because the switch moved to dual.
    before = migration.read_effective_connections(
        identity, kind="erp", scope="tenant", tenant_id=stack.tenant_id,
        sources=[legacy])
    assert before["source"] == "legacy"
    assert before["disagreements"][0]["reason"] == "new_store_empty"

    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    after = migration.read_effective_connections(
        identity, kind="erp", scope="tenant", tenant_id=stack.tenant_id,
        sources=[legacy])
    assert after["source"] == "new"
    assert after["disagreements"] == []


def test_store_version_state_is_observable(identity, stack, legacy_conf,
                                           migration):
    write_erp(stack, [erp_record()])
    legacy_conf["external_connections"] = {"store_version": "dual"}
    state = migration.store_version_state(identity)
    assert state["store_version"] == "dual"
    assert state["configured"] is True
    assert state["safe_default"] == "legacy"
    assert state["allowed"] == ["legacy", "dual", "new"]
    assert len(state["pending"]) == 1
    assert state["ok"] is True

    bare = migration.store_version_state()
    assert bare["store_version"] == "dual" and bare["pending"] == []


# -- verification ------------------------------------------------------------

def test_verify_reports_counts_reasons_and_secret_gaps(
        identity, stack, migration):
    write_erp(stack, [
        erp_record(id="erp-1", name="好的"),
        erp_record(id="erp-2", name="U9", provider="u9"),
        erp_record(id="erp-3", name="缺密码", password=""),
    ])
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)

    report = migration.verify(identity)
    assert report["counts_by_kind_scope"] == {"erp": {"tenant": 3}}
    assert report["summary"]["legacy_total"] == 3
    assert report["summary"]["imported"] == 1
    assert report["summary"]["unmapped"] == 2
    assert report["summary"]["unimportable"] == 2
    reasons = {item["legacy_id"]: item["reason"] for item in report["unmapped"]}
    assert reasons == {"erp-2": migration.REASON_UNSUPPORTED_PROVIDER,
                       "erp-3": migration.REASON_SECRET_MISSING}
    # No *present* secret was lost: erp-3 never had a value to carry, and the
    # imported record's slot is verified as referenced.
    assert report["secret_gaps"] == []
    # erp-3 is *fixable* (add the password) and blocks the switch, so the
    # migration is not complete even though erp-2 never can be imported.
    assert report["ok"] is False


def test_verify_reports_a_secret_that_could_not_be_carried(
        identity, stack, migration, monkeypatch):
    """A secret that exists in the legacy record but cannot be written is a gap."""
    write_erp(stack, [erp_record()])
    monkeypatch.delenv("COW_CREDENTIAL_MASTER_KEY", raising=False)

    result = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert result["summary"] == {migration.RESULT_FAILED: 1}
    assert result["results"][0]["reason"] == migration.REASON_SECRET_UNDECRYPTABLE
    assert db_count(identity, "external_connections") == 0

    report = migration.verify(identity)
    assert report["ok"] is False
    assert report["unmapped"][0]["reason"] == \
        migration.REASON_SECRET_UNDECRYPTABLE
    assert [item["slot"] for item in report["secret_gaps"]] == ["password"]
    assert report["secret_gaps"][0]["reason"] == \
        migration.REASON_SECRET_UNDECRYPTABLE

    # Restoring the key lets a retry finish the migration (the failed row is
    # reclaimed, not duplicated).
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    retry = migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    assert retry["summary"] == {migration.RESULT_IMPORTED: 1}
    assert db_count(identity, "external_connections") == 1
    assert migration.verify(identity)["ok"] is True


def test_verify_is_ok_once_every_importable_record_is_mapped(
        identity, stack, migration):
    write_erp(stack, [erp_record()])
    assert migration.verify(identity)["ok"] is False
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)
    report = migration.verify(identity)
    assert report["ok"] is True
    assert report["unmapped"] == []
    assert report["secret_gaps"] == []


def test_verify_reports_a_secret_that_could_not_be_referenced(
        identity, stack, migration, monkeypatch):
    """A slot the legacy record has but the new row does not is a gap."""
    write_erp(stack, [erp_record()])
    migration.import_connections(
        identity, actor_user_id=stack.root, confirm=True)

    real = migration._carried_secret_slots
    monkeypatch.setattr(migration, "_carried_secret_slots",
                        lambda store, connection_id: set())
    report = migration.verify(identity)
    assert report["ok"] is True  # mapped, but the slot is flagged
    assert [item["slot"] for item in report["secret_gaps"]] == ["password"]
    assert report["secret_gaps"][0]["reason"] == "secret_not_referenced"
    monkeypatch.setattr(migration, "_carried_secret_slots", real)


# -- CLI ---------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, identity, legacy_conf):
    from click.testing import CliRunner
    from cli.commands import external_connections as command

    monkeypatch.setattr(command, "_identity", lambda: identity)
    legacy_conf["external_connections"] = {"store_version": "legacy"}
    return command, CliRunner()


def test_cli_preflight_is_read_only_json(cli, identity, stack):
    command, runner = cli
    write_erp(stack, [erp_record()])

    result = runner.invoke(command.external_connections, ["preflight", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["writable"] is False
    assert report["summary"]["importable"] == 1
    assert db_count(identity, "external_connections") == 0
    assert SECRET not in result.output


def test_cli_import_dry_run_then_yes(cli, identity, stack):
    command, runner = cli
    write_erp(stack, [erp_record()])

    dry = runner.invoke(command.external_connections,
                        ["import", "--actor", stack.root])
    assert dry.exit_code == 0, dry.output
    assert "Dry run" in dry.output
    assert db_count(identity, "external_connections") == 0

    real = runner.invoke(command.external_connections,
                         ["import", "--actor", stack.root, "--yes"])
    assert real.exit_code == 0, real.output
    assert db_count(identity, "external_connections") == 1
    assert SECRET not in real.output

    verify = runner.invoke(command.external_connections, ["verify"])
    assert verify.exit_code == 0, verify.output
    assert "Verification: OK" in verify.output
