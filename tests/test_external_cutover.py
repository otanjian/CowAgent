# encoding:utf-8
"""The steps after a successful import: legacy plaintext, retention, caches.

Task group 12's tail. The migration itself never edits the legacy store -- until
the switch is flipped that store is the *live* one, so a run that cleaned up
after itself while the runtime still read it would be data loss dressed up as a
successful run. The consequence is settled here, in three separately-authorized
operations, and each of the tests below pins one of the rules that make doing it
late safe:

* **redaction** only blanks a value the new store provably holds a reference to,
  never touches a record that was not imported, and is idempotent;
* **retention** never deletes the last backup that still verifies, and never
  considers a file it did not write;
* **cache refresh** drops exactly the discovery memos a restart would have
  dropped, and says so honestly when there is no such cache in this process.
"""

from __future__ import annotations

import json
import os

import pytest

from integrations.external.errors import ExternalConnectionError

from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
SECRET = "S3cretPassw0rd!"
OTHER_SECRET = "An0therS3cret!"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def cutover():
    from integrations.external import cutover as module
    return module


@pytest.fixture
def backup():
    from integrations.external import backup as module
    return module


@pytest.fixture
def migration():
    from integrations.external import migration as module
    return module


@pytest.fixture(autouse=True)
def legacy_conf(monkeypatch):
    """Pin ``conf()`` in the migration module, as the other migration tests do."""
    from integrations.external import migration as module

    settings = {}
    monkeypatch.setattr(module, "conf", lambda: settings)
    return settings


@pytest.fixture
def service(stack):
    from integrations.external.service import ExternalConnectionService

    return ExternalConnectionService(stack.service)


# -- legacy fixtures ---------------------------------------------------------

def erp_path(stack):
    return os.path.join(stack.shared_root, ".one", "erp_connections.json")


def mcp_path(stack):
    return os.path.join(stack.shared_root, "mcp.json")


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def erp_record(**overrides):
    record = {
        "id": "erp-1",
        "name": "SAP 生产",
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


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def import_all(migration, stack, *, erp=(), mcp=()):
    return migration.import_connections(
        stack.service, actor_user_id=stack.root, confirm=True,
        erp_files=tuple((path, stack.tenant_id) for path in erp),
        mcp_files=tuple((path, stack.tenant_id) for path in mcp))


# -- redacting the legacy plaintext ------------------------------------------

def test_the_dry_run_reports_and_writes_nothing(cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)])

    assert report["confirmed"] is False
    assert report["counts"]["redacted"] == 1
    assert report["files"][0]["written"] is False
    assert read_json(path)[0]["password"] == SECRET


def test_a_confirmed_run_blanks_only_the_field_the_new_store_holds(
        cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    # The value really is in the control plane before anything is removed: this
    # is the whole reason the redaction is allowed to run.
    assert stack.service._store.execute(
        "SELECT COUNT(*) c FROM external_connection_secret_refs"
    )[0]["c"] == 1

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)

    assert report["files"][0]["written"] is True
    entry = read_json(path)[0]
    assert entry["password"] == ""
    # Everything that is not a secret survives, so the file is still the record
    # of what the connection was.
    assert entry["ashost"] == "sap.example.com"
    assert entry["username"] == "sapuser"
    assert entry["id"] == "erp-1"


def test_a_record_that_was_never_imported_keeps_its_password(
        cutover, migration, stack):
    """The only copy of a credential must not be deleted to tidy a file up.

    The second record names a ``saprouter`` hop the control plane cannot
    represent, so it is reported as unrepresentable and never written anywhere.
    Its password is then the only one in existence.
    """
    path = write_json(erp_path(stack), [
        erp_record(id="erp-imported", password=SECRET),
        erp_record(id="erp-untouched", name="SAP 测试", password=OTHER_SECRET,
                   saprouter="/H/router/S/3299/H/"),
    ])
    migrated = migration.import_connections(
        stack.service, actor_user_id=stack.root, confirm=True,
        erp_files=[(path, stack.tenant_id)])
    assert migrated["summary"].get("imported") == 1, migrated

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service,
        erp_files=[(path, stack.tenant_id)], confirm=True)

    by_id = {entry["id"]: entry for entry in read_json(path)}
    assert by_id["erp-imported"]["password"] == ""
    assert by_id["erp-untouched"]["password"] == OTHER_SECRET
    assert [item["legacy_id"] for item in report["skipped"]] == ["erp-untouched"]
    assert report["skipped"][0]["reason"] == cutover.NOT_IMPORTED


def test_a_slot_the_new_store_does_not_reference_is_left_alone(
        cutover, migration, stack):
    """A recorded import is not enough: the slot has to be there.

    The case this covers is the dangerous one — the connection row was written,
    the ledger says ``imported``, and the secret write did not happen. Blanking
    the file then would destroy the only copy of a live credential.
    """
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    connection = stack.service._store.execute(
        "SELECT id FROM external_connections")[0]["id"]
    # Simulate the failed secret write: the row stays, the reference goes.
    with stack.service._store.connect() as con:
        con.execute("DELETE FROM external_connection_secret_refs"
                    " WHERE connection_id=?", (connection,))
        con.commit()

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)

    assert report["counts"]["redacted"] == 0
    assert [item["reason"] for item in report["skipped"]] == [
        cutover.SECRET_NOT_CARRIED]
    assert read_json(path)[0]["password"] == SECRET


def test_a_second_run_is_visibly_a_no_op(cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    first = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)
    assert first["counts"]["redacted"] == 1

    second = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)

    assert second["counts"]["redacted"] == 0
    assert second["counts"]["already_redacted"] == 1
    assert second["files"][0]["written"] is False
    assert read_json(path)[0]["password"] == ""


def test_an_mcp_env_value_is_blanked_without_losing_the_variable_name(
        cutover, migration, stack):
    """``env`` maps a variable name to the secret; only the value is a secret."""
    path = write_json(mcp_path(stack), [{
        "name": "filesystem", "type": "stdio", "command": "npx",
        "args": ["-y", "server-filesystem", "/tmp"],
        "env": {"API_TOKEN": SECRET},
    }])
    import_all(migration, stack, mcp=[path])

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service,
        mcp_files=[(path, stack.tenant_id)], confirm=True)

    assert report["counts"]["redacted"] == 1
    entry = read_json(path)[0]
    assert entry["env"] == {"API_TOKEN": ""}
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "server-filesystem", "/tmp"]


def test_a_password_rotated_after_the_import_is_not_destroyed(
        cutover, migration, stack):
    """A value the control plane never received must survive the cleanup.

    After an import, the operator rotates the password in the legacy file but
    the connection has not been re-imported. The ledger still claims the record,
    so a match on identity alone would blank the *new* password -- the only copy
    of a credential the runtime does not have. The content no longer hashes to
    what was imported, so it is reported, and kept.
    """
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    write_json(path, [erp_record(password=OTHER_SECRET)])

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service,
        erp_files=[(path, stack.tenant_id)], confirm=True)

    assert report["counts"]["redacted"] == 0
    assert report["skipped"][0]["reason"] == cutover.CHANGED_AFTER_IMPORT
    assert read_json(path)[0]["password"] == OTHER_SECRET


def test_the_redaction_never_prints_a_secret(cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)
    assert SECRET not in json.dumps(report, ensure_ascii=False, default=str)
    assert OTHER_SECRET not in json.dumps(report, ensure_ascii=False, default=str)


def test_a_malformed_file_is_reported_and_left_alone(cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    report = cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)

    assert report["counts"]["redacted"] == 0
    # Named, not silently absent: the plaintext is still in that file.
    assert [item["error"] for item in report["files"] if item.get("error")]
    assert not any(item["written"] for item in report["files"])
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "{ this is not json"


def test_the_redaction_keeps_the_files_permissions(cutover, migration, stack):
    path = write_json(erp_path(stack), [erp_record()])
    os.chmod(path, 0o600)
    import_all(migration, stack, erp=[path])
    cutover.redact_legacy_secrets(
        actor_user_id=stack.root, identity=stack.service, erp_files=[(path, stack.tenant_id)],
        confirm=True)
    assert os.stat(path).st_mode & 0o777 == 0o600
    # No temporary file is left behind next to it.
    assert not os.path.exists(path + ".redact.tmp")


def test_redacting_requires_a_platform_admin(cutover, migration, stack):
    from integrations.external.errors import ExternalConnectionError

    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])
    member = _member(stack, "plain-member")

    # The refusal happens before the file is even read, so a member cannot learn
    # from this endpoint whether a maintenance-style cleanup is pending.
    with pytest.raises(ExternalConnectionError) as raised:
        cutover.redact_legacy_secrets(
            actor_user_id=member, identity=stack.service, erp_files=[(path, stack.tenant_id)],
            confirm=True)
    assert raised.value.status == 403
    assert read_json(path)[0]["password"] == SECRET


def _member(stack, name: str, role: str = "member") -> str:
    """A tenant member with no manage authority for the tenant."""
    return stack.member(name, [role])


# -- backup retention --------------------------------------------------------

@pytest.fixture
def plane(stack):
    from integrations.external.service import ExternalConnectionService

    return ExternalConnectionService(stack.service)


def _erp(plane, stack, **overrides):
    config = {"provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
              "client": "100", "user": "sapuser", "lang": "EN"}
    config.update(overrides.pop("config", {}))
    return plane.create_connection(
        actor_user_id=stack.root, scope="tenant", kind="erp",
        tenant_id=stack.tenant_id, name=overrides.pop("name", "SAP"),
        config=config, secrets={"password": SECRET})


def _make_backups(backup, plane, stack, out_dir, count, *, note=""):
    paths = []
    for index in range(count):
        report = backup.create_backup(
            actor_user_id=stack.root, out_dir=out_dir, service=plane,
            scope_keys=["tenant:%s" % stack.tenant_id],
            create_dir=True, note="%s %d" % (note, index))
        paths.append(report["backup_file"])
        # Distinct mtimes give the retention pass a real order to work with.
        os.utime(report["backup_file"], (1000 + index, 1000 + index))
    return paths


def test_pruning_is_a_dry_run_until_confirmed(backup, plane, stack, tmp_path):
    out_dir = str(tmp_path / "backups")
    paths = _make_backups(backup, plane, stack, out_dir, 3, note="run")

    report = backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                                  keep=1, max_age_days=0, service=plane)

    assert report["confirmed"] is False
    assert len(report["to_remove"]) == 2
    assert report["removed"] == []
    assert all(os.path.exists(path) for path in paths)


def test_a_confirmed_prune_removes_the_old_ones_and_their_manifests(
        backup, plane, stack, tmp_path):
    out_dir = str(tmp_path / "backups")
    paths = _make_backups(backup, plane, stack, out_dir, 3, note="run")

    report = backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                                  keep=1, max_age_days=0, service=plane,
                                  confirm=True)

    assert len(report["removed"]) == 2
    survivors = [path for path in paths if os.path.exists(path)]
    assert len(survivors) == 1
    # The manifest goes with its backup: a manifest without a payload is a
    # record of a backup that no longer exists, which is worse than neither.
    for path in paths:
        if path not in survivors:
            assert not os.path.exists(path + ".manifest.json")


def test_the_last_backup_that_verifies_is_never_deleted(
        backup, plane, stack, tmp_path):
    """A broken *newer* backup must not push out the last good one.

    This is the failure the ordering exists for: keeping "the newest N" would
    delete the only restorable artifact in favour of a file that only looks like
    a backup.
    """
    out_dir = str(tmp_path / "backups")
    paths = _make_backups(backup, plane, stack, out_dir, 3, note="run")
    # Newest is the broken one: its manifest is gone, so nothing proves what it
    # holds.
    os.unlink(paths[-1] + ".manifest.json")
    os.utime(paths[-1], None)

    report = backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                                  keep=1, max_age_days=0, service=plane,
                                  confirm=True)

    # The newest usable backup (index -2) is protected and survives; the broken
    # newest one is inside the policy so it is kept, not counted, and the oldest
    # usable one goes because a newer usable one supersedes it.
    assert report["protected"] == 1
    assert report["removed"] == [os.path.basename(paths[0])]
    assert os.path.exists(paths[-2])
    assert os.path.exists(paths[-2] + ".manifest.json")
    assert not os.path.exists(paths[0])


def test_a_prune_that_would_empty_the_directory_refuses(
        backup, plane, stack, tmp_path):
    """Every candidate is broken: stop *before* unlinking, not after."""
    out_dir = str(tmp_path / "backups")
    paths = _make_backups(backup, plane, stack, out_dir, 2, note="run")
    for path in paths:
        os.unlink(path + ".manifest.json")

    with pytest.raises(ExternalConnectionError) as raised:
        backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                             keep=1, max_age_days=0, service=plane, confirm=True)
    assert raised.value.code == backup.NO_USABLE_BACKUP
    assert all(os.path.exists(path) for path in paths)


def test_retention_refuses_a_policy_that_keeps_nothing(backup, plane, stack,
                                                      tmp_path):
    out_dir = str(tmp_path / "backups")
    _make_backups(backup, plane, stack, out_dir, 2, note="run")
    for keep, age in ((0, 30), (1, -1)):
        with pytest.raises(ExternalConnectionError) as raised:
            backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                                 keep=keep, max_age_days=age, service=plane,
                                 confirm=True)
        assert raised.value.code == backup.BAD_RETENTION


def test_files_this_module_did_not_write_are_never_candidates(
        backup, plane, stack, tmp_path):
    out_dir = str(tmp_path / "backups")
    _make_backups(backup, plane, stack, out_dir, 2, note="run")
    stray = os.path.join(out_dir, "important-sql-dump.sql")
    with open(stray, "w", encoding="utf-8") as handle:
        handle.write("-- not a backup")
    # A file that *looks* like a backup but is a symlink is not ours either: it
    # may point at the live store.
    link = os.path.join(out_dir, "external-connections-20200101T000000Z-"
                        "0123456789abcdef.json.enc")
    os.symlink(stray, link)

    report = backup.prune_backups(actor_user_id=stack.root, out_dir=out_dir,
                                  keep=1, max_age_days=0, service=plane,
                                  confirm=True)

    assert os.path.exists(stray)
    assert os.path.islink(link)
    assert os.path.basename(stray) not in report["to_remove"]


def test_retention_requires_a_platform_admin(backup, plane, stack, tmp_path):
    from integrations.external.errors import ExternalConnectionError

    out_dir = str(tmp_path / "backups")
    _make_backups(backup, plane, stack, out_dir, 2, note="run")
    with pytest.raises(ExternalConnectionError) as raised:
        backup.prune_backups(actor_user_id=_member(stack, "member-b"),
                             out_dir=out_dir, keep=1, service=plane,
                             confirm=True)
    assert raised.value.status == 403


# -- restoring for a rollback drill (12.6) -----------------------------------

def test_restoring_writes_the_files_where_it_is_told(cutover, backup, plane,
                                                    stack, tmp_path):
    source = write_json(erp_path(stack), [erp_record()])
    created = backup.create_backup(actor_user_id=stack.root,
                                   out_dir=str(tmp_path / "backups"),
                                   erp_files=[source], service=plane,
                                   create_dir=True)
    out_dir = str(tmp_path / "restored")

    dry = cutover.restore_files(created["backup_file"], out_dir=out_dir,
                                service=plane, actor_user_id=stack.root)
    assert dry["dry_run"] is True
    assert not os.path.exists(out_dir)

    done = cutover.restore_files(created["backup_file"], out_dir=out_dir,
                                 service=plane, actor_user_id=stack.root,
                                 confirm=True)
    assert done["dry_run"] is False
    restored = os.path.join(out_dir, os.path.basename(source))
    assert os.path.exists(restored)
    # Byte-for-byte: a restore that changed the store would defeat the point.
    assert read_json(restored) == read_json(source)


# -- dropping caches filled before the import --------------------------------

@pytest.fixture
def mcp_external():
    from agent.tools.mcp import external as module

    module._reset_for_tests()
    yield module
    module._reset_for_tests()


def test_the_refresh_drops_the_discovery_memos_of_mcp_tenants(
        cutover, plane, stack, mcp_external):
    _erp(plane, stack)
    plane.create_connection(
        actor_user_id=stack.root, scope="tenant", kind="mcp",
        tenant_id=stack.tenant_id, name="filesystem",
        config={"transport": "stdio", "command": "npx", "args": ["-y", "x"]})
    mcp_external.remember_tools(
        tenant_id=stack.tenant_id, connection_id="conn_a", version=1,
        tools=[{"name": "read_file"}], secret_marker=())
    mcp_external.remember_tools(
        tenant_id="other-tenant", connection_id="conn_b", version=1,
        tools=[{"name": "read_file"}], secret_marker=())

    report = cutover.refresh_caches(actor_user_id=stack.root, service=plane)

    assert report["tenants"] == [stack.tenant_id]
    assert report["memo_entries_dropped"] == 1
    # A tenant that was not named keeps its memo: the refresh is scoped, because
    # clearing everything would be an outage in the tenants that did not change.
    assert ("other-tenant", "conn_b") in mcp_external._MEMO


def test_the_refresh_reports_honestly_when_there_is_no_cache(
        cutover, plane, stack, mcp_external, monkeypatch):
    """A CLI process has no agent memo; that is a fact, not a failure."""
    _erp(plane, stack)
    plane.create_connection(
        actor_user_id=stack.root, scope="tenant", kind="mcp",
        tenant_id=stack.tenant_id, name="filesystem",
        config={"transport": "stdio", "command": "npx", "args": ["-y", "x"]})

    def _boom(tenant_id):
        raise RuntimeError("the agent history is not importable here")

    monkeypatch.setattr(mcp_external, "forget_tenant", _boom)

    report = cutover.refresh_caches(actor_user_id=stack.root, service=plane)

    assert report["cache_unavailable"]
    assert report["tenants"] == []


def test_the_refresh_records_who_triggered_it(cutover, plane, stack,
                                             mcp_external):
    _erp(plane, stack)
    report = cutover.refresh_caches(actor_user_id=stack.root, service=plane)
    assert report["actor_user_id"] == stack.root


# -- CLI ---------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, stack):
    from click.testing import CliRunner
    from cli.commands import external_connections as command

    monkeypatch.setattr(command, "_identity", lambda: stack.service)
    return command, CliRunner()


def test_cli_redact_is_a_dry_run_until_yes(cli, migration, stack):
    command, runner = cli
    path = write_json(erp_path(stack), [erp_record()])
    import_all(migration, stack, erp=[path])

    dry = runner.invoke(command.external_connections,
                        ["redact", "--actor", stack.root])
    assert dry.exit_code == 0, dry.output
    assert "Dry run" in dry.output
    assert SECRET not in dry.output
    assert read_json(path)[0]["password"] == SECRET

    real = runner.invoke(command.external_connections,
                         ["redact", "--actor", stack.root, "--yes"])
    assert real.exit_code == 0, real.output
    assert read_json(path)[0]["password"] == ""
    assert SECRET not in real.output


def test_cli_prune_backups_needs_yes_and_a_directory(cli, backup, plane, stack,
                                                    tmp_path):
    command, runner = cli
    out_dir = str(tmp_path / "backups")
    paths = _make_backups(backup, plane, stack, out_dir, 3, note="cli")

    dry = runner.invoke(command.external_connections,
                        ["prune-backups", "--dir", out_dir, "--keep", "1",
                         "--max-age-days", "0", "--actor", stack.root])
    assert dry.exit_code == 0, dry.output
    assert "Dry run" in dry.output
    assert all(os.path.exists(path) for path in paths)

    real = runner.invoke(command.external_connections,
                         ["prune-backups", "--dir", out_dir, "--keep", "1",
                          "--max-age-days", "0", "--actor", stack.root, "--yes"])
    assert real.exit_code == 0, real.output
    assert sum(1 for path in paths if os.path.exists(path)) == 1


def test_cli_refresh_caches_reports_no_cache_honestly(cli, plane, stack):
    command, runner = cli
    _erp(plane, stack)

    result = runner.invoke(command.external_connections, ["refresh-caches"])

    assert result.exit_code == 0, result.output
    assert "tenants refreshed" in result.output


def test_cli_restore_files_writes_only_with_yes(cli, backup, plane, stack,
                                               tmp_path):
    command, runner = cli
    source = write_json(erp_path(stack), [erp_record()])
    created = backup.create_backup(actor_user_id=stack.root,
                                   out_dir=str(tmp_path / "backups"),
                                   erp_files=[source], service=plane,
                                   create_dir=True)
    out_dir = str(tmp_path / "restored")

    dry = runner.invoke(command.external_connections,
                        ["restore-files", "--backup", created["backup_file"],
                         "--dir", out_dir, "--actor", stack.root])
    assert dry.exit_code == 0, dry.output
    assert "Dry run" in dry.output
    assert not os.path.exists(out_dir)

    real = runner.invoke(command.external_connections,
                         ["restore-files", "--backup", created["backup_file"],
                          "--dir", out_dir, "--actor", stack.root, "--yes"])
    assert real.exit_code == 0, real.output
    assert os.path.exists(os.path.join(out_dir, os.path.basename(source)))
