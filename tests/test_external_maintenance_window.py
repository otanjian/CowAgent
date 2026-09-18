# encoding:utf-8
"""Per-scope maintenance windows and the encrypted cutover backup.

Task 12.2 (change ``add-external-system-access``) asks for
"分范围维护窗口迁移：暂停目标范围配置写入和新执行，生成加密受控备份". The tests below
pin the properties that make those words mean something:

* the window holds **one scope** — a tenant that finished is not held by a
  tenant that has not;
* it pauses **configuration writes and new executions**, and only those: reads
  and probes keep working, because the operator needs to test what they just
  imported;
* it is **not permanent by construction** — an expired window resumes on its
  own, so a crashed operator session is not an outage;
* enforcement is a **refusal**, not a silent queue, and it reports a stable
  code so a client can tell "paused" from "not authorized";
* a mutation that is refused **changed nothing** — verified by reading the
  state back rather than by trusting the error;
* the backup is **encrypted with the deployment's credential key**, carries the
  legacy stores byte-for-byte, writes **no plaintext to disk**, and cannot be
  produced at all when the deployment has no key (the "encrypted" in
  "encrypted backup" is not allowed to degrade into "plaintext");
* the destination is **controlled** — a symlinked directory or file is refused
  rather than followed;
* a verify pass with the key and **without** it both work, and a tampered
  ciphertext is caught.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from integrations.external import backup, maintenance, registry
from integrations.external.errors import ExternalConnectionError

from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
SECRET = "Sup3rSecret!Value"
RFC = {"provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
       "client": "100", "user": "sapuser", "lang": "EN"}


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def plane(stack):
    """The control-plane service over this test's identity store.

    ``stack.service`` is the *identity* service; the plane's service is the one
    with the connection methods and the authority helpers. The maintenance API
    accepts either (it wraps an identity service), which is what lets the CLI
    and the request path share one function.
    """
    from integrations.external.service import ExternalConnectionService

    return ExternalConnectionService(stack.service)


def _erp(plane, stack, name="SAP A"):
    return plane.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        kind=registry.KIND_ERP, name=name, config=dict(RFC),
        tenant_id=stack.tenant_id, secrets={"password": SECRET})


def _tenant_window(stack, *, actor=None, tenant_id=None, **kwargs):
    """Open a tenant-scope window through the stack's own service."""
    return maintenance.begin_window(
        actor_user_id=actor or stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=tenant_id or stack.tenant_id, service=stack.service, **kwargs)


# -- the scope is the unit ---------------------------------------------------

def test_a_window_holds_one_scope_and_not_the_others(stack, plane):
    """A tenant mid-cutover must not pause the platform or another tenant.

    This is the difference between 分范围维护窗口迁移 and an instance-wide freeze:
    the design lets tenants be cut over one at a time, so a tenant that is done
    has to be able to serve while another is still importing.
    """
    other = stack.other_tenant("other")["tenant_id"]
    _tenant_window(stack)

    assert maintenance.paused(scope=registry.SCOPE_TENANT,
                              tenant_id=stack.tenant_id, identity=stack.service)
    assert not maintenance.paused(scope=registry.SCOPE_TENANT,
                                  tenant_id=other, identity=stack.service)
    assert not maintenance.paused(scope=registry.SCOPE_PLATFORM,
                                  tenant_id=None, identity=stack.service)


def test_a_platform_window_does_not_hold_a_tenant_and_vice_versa(stack):
    maintenance.begin_window(actor_user_id=stack.root,
                             scope=registry.SCOPE_PLATFORM, service=stack.service)
    assert maintenance.paused(scope=registry.SCOPE_PLATFORM,
                              identity=stack.service)
    assert not maintenance.paused(scope=registry.SCOPE_TENANT,
                                  tenant_id=stack.tenant_id,
                                  identity=stack.service)


# -- opening the window ------------------------------------------------------

def test_opening_a_window_twice_returns_the_first_one(stack):
    """Two open rows for one scope would make "when was this paused" ambiguous."""
    first = _tenant_window(stack, reason="first")
    second = _tenant_window(stack, reason="second")

    assert second["window_id"] == first["window_id"]
    assert second["reason"] == "first"
    assert len(maintenance.list_windows(identity=stack.service)) == 1


def test_a_tenant_window_needs_the_tenant_management_authority(stack):
    """A plain member cannot pause a tenant, and cannot pause another tenant."""
    member = stack.member("paused-member", ["member"])
    with pytest.raises(Exception) as caught:
        maintenance.begin_window(actor_user_id=member,
                                 scope=registry.SCOPE_TENANT,
                                 tenant_id=stack.tenant_id,
                                 service=stack.service)
    assert getattr(caught.value, "status", None) == 403
    assert not maintenance.paused(scope=registry.SCOPE_TENANT,
                                  tenant_id=stack.tenant_id,
                                  identity=stack.service)


def test_a_window_for_a_tenant_the_caller_does_not_manage_is_refused(stack):
    """Authority is per scope, and a tenant admin's authority stops at their
    own tenant — otherwise one tenant could pause another's cutover.

    A platform admin is not the case under test: it manages every tenant, so it
    may legitimately open any tenant's window.
    """
    other = stack.other_tenant("other")["tenant_id"]
    local_admin = stack.tenant_admin("local-admin")
    with pytest.raises(Exception) as caught:
        maintenance.begin_window(actor_user_id=local_admin,
                                 scope=registry.SCOPE_TENANT,
                                 tenant_id=other,
                                 service=stack.service)
    assert getattr(caught.value, "status", None) == 403
    assert not maintenance.paused(scope=registry.SCOPE_TENANT, tenant_id=other,
                                  identity=stack.service)


def test_a_platform_window_needs_a_platform_admin(stack):
    """Pausing the platform scope is not something a tenant admin may do."""
    local_admin = stack.tenant_admin("tenant-admin-pause")
    with pytest.raises(Exception) as caught:
        maintenance.begin_window(actor_user_id=local_admin,
                                 scope=registry.SCOPE_PLATFORM,
                                 service=stack.service)
    assert getattr(caught.value, "status", None) == 403
    assert not maintenance.paused(scope=registry.SCOPE_PLATFORM,
                                  identity=stack.service)


def test_a_window_cannot_be_opened_without_a_bound(stack):
    """'Always expires' is only real if the bound is enforced."""
    with pytest.raises(ExternalConnectionError) as caught:
        _tenant_window(stack, ttl_seconds=7 * 24 * 60 * 60)
    assert caught.value.code == "invalid_ttl"
    with pytest.raises(ExternalConnectionError):
        _tenant_window(stack, ttl_seconds=1)
    assert not maintenance.paused(scope=registry.SCOPE_TENANT,
                                  tenant_id=stack.tenant_id,
                                  identity=stack.service)


def test_an_unknown_scope_is_refused_rather_than_defaulted(stack):
    with pytest.raises(ExternalConnectionError) as caught:
        maintenance.begin_window(actor_user_id=stack.root, scope="galaxy",
                                 service=stack.service)
    assert caught.value.code == "unsupported_scope"


def test_opening_and_closing_are_audited(stack, plane):
    window = _tenant_window(stack, reason="cutover")
    maintenance.end_window(actor_user_id=stack.root,
                           scope=registry.SCOPE_TENANT,
                           tenant_id=stack.tenant_id, service=stack.service)
    rows = plane._store.execute(
        "SELECT action, target, redacted_changes FROM audit_events"
        " WHERE action LIKE 'external_connections.window%'"
        " ORDER BY time, rowid")
    actions = [row["action"] for row in rows]
    assert actions == ["external_connections.window_open",
                       "external_connections.window_close"]
    assert window["window_id"] in json.dumps(rows[1]["redacted_changes"])


# -- expiry ------------------------------------------------------------------

def test_an_expired_window_stops_being_active(stack, plane, monkeypatch):
    """A crash must not leave a scope paused forever.

    The row stays readable — it is the record of what happened — but it no
    longer refuses anything, so no operating procedure is needed to recover
    from an interrupted cutover.
    """
    _tenant_window(stack, ttl_seconds=maintenance.MIN_TTL_SECONDS)
    assert maintenance.paused(scope=registry.SCOPE_TENANT,
                              tenant_id=stack.tenant_id,
                              identity=stack.service)

    real_time = time.time
    monkeypatch.setattr(maintenance.time, "time",
                        lambda: real_time() + maintenance.MIN_TTL_SECONDS + 1)
    assert not maintenance.paused(scope=registry.SCOPE_TENANT,
                                  tenant_id=stack.tenant_id,
                                  identity=stack.service)
    # History is kept: still listed, marked inactive.
    rows = maintenance.list_windows(identity=stack.service)
    assert len(rows) == 1 and rows[0]["active"] is False


def test_closing_a_window_that_is_not_open_is_a_no_op(stack):
    result = maintenance.end_window(actor_user_id=stack.root,
                                    scope=registry.SCOPE_TENANT,
                                    tenant_id=stack.tenant_id,
                                    service=stack.service)
    assert result["closed"] is False
    assert result["window"] is None


# -- configuration writes are paused ----------------------------------------

def test_a_paused_scope_refuses_every_configuration_write(stack, plane):
    """Each write the design names must refuse, and must change nothing.

    Checked by reading the state back after every refusal: a handler that
    raised after writing would pass an error-only assertion while leaving the
    exact half-applied state the window exists to prevent.
    """
    connection = _erp(plane, stack, "Before")
    before = plane._store.execute(
        "SELECT COUNT(*) c FROM external_connections WHERE kind='erp'")[0]["c"]
    _tenant_window(stack, reason="cutover")

    refused = []
    for label, call in [
        ("create", lambda: plane.create_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
            kind=registry.KIND_ERP, name="During", config=dict(RFC),
            tenant_id=stack.tenant_id, secrets={"password": SECRET})),
        ("update", lambda: plane.update_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
            connection_id=connection["id"], tenant_id=stack.tenant_id,
            expected_version=1, name="Renamed")),
        ("delete", lambda: plane.delete_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
            connection_id=connection["id"], tenant_id=stack.tenant_id,
            expected_version=1)),
        ("erp_default", lambda: plane.set_erp_default(
            actor_user_id=stack.root, tenant_id=stack.tenant_id,
            connection_id=connection["id"], expected_revision=1)),
    ]:
        with pytest.raises(ExternalConnectionError) as caught:
            call()
        assert caught.value.code == maintenance.PAUSED, label
        refused.append(label)

    assert refused == ["create", "update", "delete", "erp_default"]
    after = plane._store.execute(
        "SELECT COUNT(*) c FROM external_connections WHERE kind='erp'")[0]["c"]
    assert after == before
    row = plane.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        connection_id=connection["id"], tenant_id=stack.tenant_id)
    assert row["name"] == "Before"
    assert int(row["version"]) == 1
    assert plane.get_erp_default(
        actor_user_id=stack.root, tenant_id=stack.tenant_id)["connection_id"] is None


def test_the_refusal_names_the_reason_and_not_the_connections(stack, plane):
    _tenant_window(stack, reason="importing 2026-09")
    with pytest.raises(ExternalConnectionError) as caught:
        plane.create_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
            kind=registry.KIND_ERP, name="During", config=dict(RFC),
            tenant_id=stack.tenant_id)
    message = str(caught.value)
    assert "importing 2026-09" in message
    # Nothing about what is being imported: a paused caller learns the reason,
    # not the inventory.
    assert "SAP" not in message and "conn_" not in message


def test_an_unauthorized_caller_still_gets_the_authority_refusal(stack, plane):
    """The pause is not a disclosure channel for a scope the caller cannot see.

    Checked in this order deliberately: reporting "paused" to someone who is not
    allowed to manage the scope would tell them a cutover is running there.
    """
    other = stack.other_tenant("other")["tenant_id"]
    maintenance.begin_window(actor_user_id=stack.root,
                             scope=registry.SCOPE_TENANT, tenant_id=other,
                             service=stack.service)
    member = stack.members.get("other") or stack.member("outsider", ["member"])
    with pytest.raises(Exception) as caught:
        plane.create_connection(
            actor_user_id=member, scope=registry.SCOPE_TENANT,
            kind=registry.KIND_ERP, name="Sneaky", config=dict(RFC),
            tenant_id=other)
    assert getattr(caught.value, "status", None) == 403
    assert getattr(caught.value, "code", "") != maintenance.PAUSED


def test_closing_the_window_lets_the_write_through(stack, plane):
    """The window is a pause, not a removal: the same call succeeds after."""
    _tenant_window(stack)
    with pytest.raises(ExternalConnectionError):
        _erp(plane, stack, "Blocked")
    maintenance.end_window(actor_user_id=stack.root,
                           scope=registry.SCOPE_TENANT,
                           tenant_id=stack.tenant_id, service=stack.service)
    created = _erp(plane, stack, "Allowed")
    assert created["id"]


# -- reads and probes are not paused ----------------------------------------

def test_reads_and_probes_still_work_during_a_window(stack, plane):
    """The operator has to be able to look at, and test, what it imported.

    Pausing the probe would make the window the one time a connection cannot be
    verified, which is the opposite of what the window is for.
    """
    connection = _erp(plane, stack, "SAP A")
    _tenant_window(stack)

    listed = plane.list_catalog(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id)
    assert [card["id"] for card in listed["items"]] == [connection["id"]]
    detail = plane.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        connection_id=connection["id"], tenant_id=stack.tenant_id)
    assert detail["name"] == "SAP A"
    described = plane.runtime().describe(connection["id"],
                                         tenant_id=stack.tenant_id)
    assert described["connection_id"] == connection["id"]
    # A probe is a diagnostic, and its result is a fact about the remote rather
    # than about the configuration being imported, so the window does not refuse
    # it. This deployment has not opened the ERP test class, so the probe is
    # refused for *that* reason -- which is the assertion: the window adds no
    # refusal of its own.
    with pytest.raises(ExternalConnectionError) as caught:
        plane.runtime().probe(connection["id"], actor_user_id=stack.root,
                              tenant_id=stack.tenant_id)
    assert caught.value.code == "test_not_available"


def test_the_type_projection_reports_a_paused_scope(stack, plane):
    """The console must be able to say why, instead of offering a failing save."""
    _tenant_window(stack, reason="cutover")
    projection = plane.types_projection(actor_user_id=stack.root,
                                                tenant_id=stack.tenant_id)
    assert projection["maintenance"][registry.SCOPE_TENANT]["paused"] is True
    assert projection["maintenance"][registry.SCOPE_TENANT]["reason"] == "cutover"
    assert projection["maintenance"][registry.SCOPE_PLATFORM]["paused"] is False
    for item in projection["types"]:
        scopes = {entry["scope"]: entry for entry in item["scopes"]}
        tenant = scopes.get(registry.SCOPE_TENANT)
        if tenant is None:
            continue
        assert tenant["available"] is False
        assert tenant["reason"] == "maintenance_window"


def test_the_pause_explains_itself_before_the_singleton_rule(stack, plane):
    """A paused scope is paused for every type, so it must not report
    ``singleton_exists`` — that would send the operator looking for the wrong
    thing while the cutover runs."""
    plane.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        kind=registry.KIND_OA, name="OA", tenant_id=stack.tenant_id,
        config={"base_url": "https://oa.example.com", "username": "svc"},
        secrets={"password": SECRET})
    _tenant_window(stack)
    projection = plane.types_projection(actor_user_id=stack.root,
                                                tenant_id=stack.tenant_id)
    oa = next(item for item in projection["types"]
              if item["kind"] == registry.KIND_OA)
    tenant = next(s for s in oa["scopes"] if s["scope"] == registry.SCOPE_TENANT)
    assert tenant["reason"] == "maintenance_window"


# -- new executions are paused ----------------------------------------------

def test_new_executions_are_paused_with_a_stable_code(stack, plane, monkeypatch):
    """The execution half of the window.

    ``maintenance.PAUSED`` rather than ``execution_not_available``: the second
    would send a caller to ask for a grant that would not help, because the
    pause is not about their authorization.
    """
    monkeypatch.setattr(
        "integrations.external.service.get_external_connection_service",
        lambda: plane)
    connection = _erp(plane, stack, "SAP A")
    _tenant_window(stack)

    result = plane.runtime().invoke(
        connection["id"], "material.read", {"materials": ["1"]},
        actor_user_id=stack.root, tenant_id=stack.tenant_id)
    assert result.ok is False
    assert result.code == maintenance.PAUSED
    assert result.stage == "policy"


def test_reading_a_snapshot_is_not_paused(stack, plane, monkeypatch):
    """Only *executions* pause; describing a connection still works."""
    monkeypatch.setattr(
        "integrations.external.service.get_external_connection_service",
        lambda: plane)
    connection = _erp(plane, stack, "SAP A")
    _tenant_window(stack)
    described = plane.runtime().describe(connection["id"],
                                         tenant_id=stack.tenant_id)
    assert described["connection_id"] == connection["id"]


# -- the encrypted backup ----------------------------------------------------

def _legacy_erp_file(tmp_path):
    path = tmp_path / "erp_connections.json"
    path.write_text(json.dumps({
        "connections": [dict(RFC, id="legacy-erp-1", name="Legacy SAP",
                             password=SECRET, is_default=True)],
        "revision": 3,
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_a_backup_is_encrypted_and_never_plaintext_on_disk(stack, tmp_path):
    """The whole point: the legacy passwords exist in the backup, and not in
    the clear anywhere on disk."""
    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(
        actor_user_id=stack.root, out_dir=str(tmp_path / "out"),
        identity=stack.service, sources=[source], scope_keys=["tenant:%s"
                                                              % stack.tenant_id],
        create_dir=True, note="cutover 1")

    raw = open(report["backup_file"], encoding="utf-8").read()
    assert SECRET not in raw
    assert "legacy-erp-1" not in raw
    assert raw.startswith("v1.")
    # The sidecar names the file and the digest, and nothing else's content.
    manifest = json.load(open(report["manifest_file"], encoding="utf-8"))
    assert SECRET not in json.dumps(manifest)
    assert manifest["plaintext_sha256"] == report["plaintext_sha256"]
    assert manifest["legacy_files"][0]["path"] == source
    # No temp file was left behind; the plaintext document never touched disk.
    leftovers = [name for name in os.listdir(os.path.dirname(report["backup_file"]))
                 if not name.endswith((".enc", ".json"))]
    assert leftovers == []


def test_the_backup_is_readable_with_the_deployments_own_key(stack, plane, tmp_path):
    """Verified by decrypting: an "encrypted" file nobody can open is a
    deletion with extra steps."""
    from auth.crypto import decrypt_secret

    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(
        actor_user_id=stack.root, out_dir=str(tmp_path / "out"),
        identity=stack.service, sources=[source], create_dir=True)
    document = json.loads(decrypt_secret(
        open(report["backup_file"], encoding="utf-8").read()))
    assert document["format"] == backup.BACKUP_FORMAT
    assert document["legacy_files"][0]["sha256"] == report["legacy_files"][0]["sha256"]
    # The password is recoverable from the backup — which is the entire reason
    # task 12.5 may then delete it from the legacy file.
    recovered = json.loads(
        __import__("base64").b64decode(
            document["legacy_files"][0]["content_b64"]).decode("utf-8"))
    assert recovered["connections"][0]["password"] == SECRET


def test_the_backup_records_the_control_plane_rows_for_its_scopes(stack, tmp_path, plane):
    """A restore needs the connection rows and the secret *references*."""
    source = _legacy_erp_file(tmp_path)
    connection = _erp(plane, stack, "SAP A")
    other = stack.other_tenant("other")["tenant_id"]
    foreign = plane.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        kind=registry.KIND_ERP, name="Other SAP", config=dict(RFC),
        tenant_id=other, secrets={"password": SECRET})

    report = backup.create_backup(
        actor_user_id=stack.root, out_dir=str(tmp_path / "out"),
        identity=stack.service, sources=[source], create_dir=True,
        scope_keys=["tenant:%s" % stack.tenant_id])
    from auth.crypto import decrypt_secret

    document = json.loads(decrypt_secret(
        open(report["backup_file"], encoding="utf-8").read()))
    ids = {row["id"] for row in document["control_plane"]["external_connections"]}
    assert connection["id"] in ids
    assert foreign["id"] not in ids
    refs = document["control_plane"]["external_connection_secret_refs"]
    assert {row["connection_id"] for row in refs} == {connection["id"]}
    # The reference carries id + version; the credential material itself is not
    # duplicated into the backup.
    assert SECRET not in json.dumps(document["control_plane"])


def test_a_backup_without_a_key_writes_nothing(stack, tmp_path, monkeypatch):
    """A deployment that cannot encrypt must not silently write plaintext.

    The assertion is on the filesystem, not on the exception: a partial or
    plaintext artifact left behind by a failed attempt is the failure mode.
    """
    from auth.crypto import CredentialCryptoError

    monkeypatch.delenv("COW_CREDENTIAL_MASTER_KEY", raising=False)
    source = _legacy_erp_file(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(CredentialCryptoError):
        backup.create_backup(actor_user_id=stack.root, out_dir=str(out),
                             identity=stack.service, sources=[source],
                             create_dir=True)
    assert not out.exists() or os.listdir(out) == []


def test_a_backup_needs_a_platform_admin(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    member = stack.member("backup-member", ["member"])
    with pytest.raises(Exception) as caught:
        backup.create_backup(actor_user_id=member,
                             out_dir=str(tmp_path / "out"),
                             identity=stack.service, sources=[source],
                             create_dir=True)
    assert getattr(caught.value, "status", None) == 403
    assert not (tmp_path / "out").exists()


def test_a_symlinked_destination_is_refused(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    with pytest.raises(ExternalConnectionError) as caught:
        backup.create_backup(actor_user_id=stack.root, out_dir=str(link),
                             identity=stack.service, sources=[source])
    assert caught.value.code == backup.BAD_DESTINATION
    assert os.listdir(real) == []


def test_a_symlinked_legacy_store_is_refused(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    link = tmp_path / "aliased.json"
    os.symlink(source, link)
    with pytest.raises(ExternalConnectionError) as caught:
        backup.create_backup(actor_user_id=stack.root,
                             out_dir=str(tmp_path / "out"),
                             identity=stack.service, sources=[str(link)],
                             create_dir=True)
    assert caught.value.code == backup.BACKUP_UNREADABLE


def test_the_artifacts_are_not_world_readable(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(actor_user_id=stack.root,
                                  out_dir=str(tmp_path / "out"),
                                  identity=stack.service, sources=[source],
                                  create_dir=True)
    for path in (report["backup_file"], report["manifest_file"]):
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode & 0o077 == 0, oct(mode)
    assert stat.S_IMODE(os.stat(str(tmp_path / "out")).st_mode) & 0o077 == 0


# -- verify ------------------------------------------------------------------

def test_verify_checks_the_manifest_without_the_key(stack, tmp_path,
                                                    monkeypatch):
    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(actor_user_id=stack.root,
                                  out_dir=str(tmp_path / "out"),
                                  identity=stack.service, sources=[source],
                                  create_dir=True)
    monkeypatch.delenv("COW_CREDENTIAL_MASTER_KEY", raising=False)
    checked = backup.verify_backup(report["backup_file"], with_key=False)
    assert checked["decrypted"] is False
    assert checked["plaintext_sha256"] == report["plaintext_sha256"]
    assert checked["legacy_files"][0]["path"] == source


def test_verify_recomputes_the_plaintext_digest_with_the_key(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(actor_user_id=stack.root,
                                  out_dir=str(tmp_path / "out"),
                                  identity=stack.service, sources=[source],
                                  create_dir=True)
    checked = backup.verify_backup(report["backup_file"])
    assert checked["decrypted"] is True
    assert checked["file_count"] == 1


def test_a_tampered_backup_is_caught(stack, tmp_path):
    source = _legacy_erp_file(tmp_path)
    report = backup.create_backup(actor_user_id=stack.root,
                                  out_dir=str(tmp_path / "out"),
                                  identity=stack.service, sources=[source],
                                  create_dir=True)
    text = open(report["backup_file"], encoding="utf-8").read()
    # Flip one character of the ciphertext body, leaving the format prefix.
    head, sep, tail = text.partition(".")
    body = tail[:-1] + ("A" if tail[-1] != "A" else "B")
    open(report["backup_file"], "w", encoding="utf-8").write(head + sep + body)
    with pytest.raises(ExternalConnectionError) as caught:
        backup.verify_backup(report["backup_file"])
    assert caught.value.code == backup.BACKUP_DIGEST_MISMATCH


def test_a_missing_backup_is_a_not_found(stack, tmp_path):
    with pytest.raises(ExternalConnectionError) as caught:
        backup.verify_backup(str(tmp_path / "nope.json.enc"))
    assert caught.value.code == backup.BACKUP_UNREADABLE


# -- the window and the import are used together -----------------------------

def test_a_window_binds_to_the_import_batch_it_was_opened_for(stack):
    """``batch_id`` is how the window report and the import report are read
    together, so a cutover can be explained after the fact."""
    window = _tenant_window(stack, batch_id="mig_abc", reason="cutover")
    assert window["batch_id"] == "mig_abc"
    listing = maintenance.list_windows(identity=stack.service)
    assert listing[0]["batch_id"] == "mig_abc"
    assert listing[0]["active"] is True
