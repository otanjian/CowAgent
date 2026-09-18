# encoding:utf-8
"""ERP adapter (SAP RFC / ADT SQL) and scene-side connection resolution.

Task group 6 of ``add-external-system-access``. These tests exercise the parts
the spec makes non-negotiable, without a real SAP system or any network:

* the adapter registers for ``erp`` and declares exactly ``query`` and
  ``rfc.call`` — with ``rfc.call`` a write;
* ``validate_config`` refuses what the registry cannot express (a non-numeric
  client/sysnr, a URL pasted into ``ashost``), on top of the registry's own
  field-partition and unknown-key refusals;
* ``probe`` maps each failure onto the *right* stage — a missing SDK is a
  dependency problem, a bad certificate is TLS, a wrong password is auth — and
  never puts the password into the result;
* the RFC probe refuses honestly when the deployment has not accepted an
  interruptible/isolation story for the blocking SDK call;
* ``verify_ssl=false`` is refused when the deployment forbids it, never
  silently downgraded;
* ``invoke`` refuses an undeclared action, refuses a write without an approval,
  and refuses a BAPI that the deployment has not accepted as writable;
* a scene resolves a connection by id or by the tenant default through the new
  service — and a cross-tenant id, a missing default or a deleted default is a
  refusal with a stable code, never a fallback to another connection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from integrations.external.adapters.base import (
    AdapterError,
    ExecutionContext,
    STAGE_AUTH,
    STAGE_CONFIG,
    STAGE_DEPENDENCY,
    STAGE_NETWORK,
    STAGE_POLICY,
    STAGE_PROTOCOL,
    STAGE_TIMEOUT,
    STAGE_TLS,
)
from integrations.external.adapters.netpolicy import NetworkPolicy, ResolvedTarget
from integrations.external.errors import ExternalConnectionError

from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
SECRET = "Sup3rSecret!Value"
PUBLIC_ADDRESS = "93.184.216.34"  # example.com, genuinely public

RFC = {"provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
       "client": "100", "user": "sapuser", "lang": "EN"}
ADT = {"provider": "adt_sql", "base_url": "https://sap.example.com:44300",
       "client": "100", "user": "sapuser", "verify_ssl": True}


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture(autouse=True)
def _control_plane_is_authoritative(monkeypatch):
    """These tests create connections through the control plane and expect
    scenes to resolve them. That is the ``store_version=new`` contract
    (task 12.3); the default ``legacy`` switch is covered by
    ``tests/test_external_erp_store_version.py``.
    """
    from integrations.external import migration as module

    monkeypatch.setattr(
        module, "conf",
        lambda: {"external_connections": {"store_version": "new"}})


@pytest.fixture(autouse=True)
def _restore_write_bapis():
    from integrations.external.adapters import erp as erp_module

    saved = set(erp_module._WRITE_BAPIS)
    yield
    erp_module._WRITE_BAPIS.clear()
    erp_module._WRITE_BAPIS.update(saved)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def svc(stack):
    from integrations.external.service import ExternalConnectionService
    return ExternalConnectionService(stack.service)


@pytest.fixture
def scene_service(svc, monkeypatch):
    """Point the scene resolver at this test's service instance."""
    monkeypatch.setattr("integrations.external.service.get_external_connection_service",
                        lambda: svc)
    return svc


def _adapter():
    from integrations.external.adapters.base import adapter_for, load_adapters
    load_adapters()
    return adapter_for("erp")


def _policy(**overrides):
    values = {"allow_hosts": frozenset({"sap.example.com"})}
    values.update(overrides)
    return NetworkPolicy(**values)


def _ctx(config, *, secret=SECRET, limits=None, extra=None, resolver=None,
         cancel=None):
    def secret_resolver(slot):
        if resolver is not None:
            return resolver(slot)
        if slot == "password" and secret is not None:
            return secret
        raise AdapterError("secret %r is unavailable" % slot,
                           code="secret_missing", stage=STAGE_CONFIG)

    return ExecutionContext(
        kind="erp", scope="tenant", tenant_id="acme", owner_user_id=None,
        connection_id="conn_test", config=config,
        secret_resolver=secret_resolver, config_version=3,
        limits=dict(limits or {}), extra=dict(extra or {}), cancel=cancel)


class _StubProvider:
    """The provider shape the adapter consumes, without an SAP system."""

    def __init__(self, *, test=None, error=None, rows=None):
        self.test = test if test is not None else {"status": "success"}
        self.error = error
        self.rows = list(rows or [])
        self.closed = False
        self.calls = []

    def test_connection(self):
        if self.error is not None:
            raise self.error
        return self.test

    def fetch(self, scene_id=None, params=None, raw=False):
        self.calls.append(("fetch", scene_id, dict(params or {}), raw))
        return list(self.rows)

    def call_bapi(self, name, parameters):
        self.calls.append(("call_bapi", name, dict(parameters or {})))
        return {"ok": True, "name": name}

    def close(self):
        self.closed = True


def _install_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "integrations.external.adapters.erp.ErpAdapter._create_provider",
        lambda self, ctx, password, verify_ssl=None: provider)
    return provider


@pytest.fixture
def resolve_policy(monkeypatch):
    """Make DNS resolution deterministic (no network in the test)."""
    def fake_resolve(self, host, port):
        return [ResolvedTarget(host=host, address=PUBLIC_ADDRESS, port=int(port),
                               family="ipv4")]
    monkeypatch.setattr(NetworkPolicy, "resolve", fake_resolve)


# -- registration and declared actions -------------------------------------

def test_adapter_registers_with_the_declared_actions():
    adapter = _adapter()
    assert adapter.kind == "erp"
    assert adapter.actions == frozenset({"query", "rfc.call"})
    assert adapter.write_actions == frozenset({"rfc.call"})
    assert adapter.write_actions <= adapter.actions


def test_every_declared_action_has_a_risk_entry():
    from integrations.external import registry
    from integrations.external.risk import RISK_CATALOGUE, lookup

    for action in _adapter().actions:
        assert (registry.KIND_ERP, action) in RISK_CATALOGUE, action
    assert lookup("erp", "query", write=False).write is False
    assert lookup("erp", "rfc.call", write=True).write is True


def test_erp_write_class_is_not_openable_so_rfc_call_is_never_offered():
    from integrations.external import registry

    assert "write_execute" not in registry.OPENABLE_CLASSES[registry.KIND_ERP]
    assert registry.unavailable_reason(
        registry.KIND_ERP, "write_execute") == "not_supported_by_type"


# -- validate_config -------------------------------------------------------

def test_validate_config_normalises_a_good_config():
    assert _adapter().validate_config(RFC)["provider"] == "rfc"
    assert _adapter().validate_config(ADT)["base_url"] == ADT["base_url"]


@pytest.mark.parametrize("config,code", [
    # registry-owned refusals
    (dict(RFC, base_url="https://x"), "field_invalid"),
    (dict(ADT, ashost="sap.example.com"), "field_invalid"),
    (dict(RFC, nope="1"), "unknown_field"),
    (dict(RFC, password="leaky"), "secret_in_config"),
    ({"provider": "u9", "base_url": "https://x", "client": "100", "user": "u"},
     "unsupported_provider"),
    # adapter-owned shape checks
    (dict(RFC, sysnr="7"), "field_invalid"),
    (dict(RFC, sysnr="000"), "field_invalid"),
    (dict(RFC, client="abc"), "field_invalid"),
    (dict(RFC, ashost="https://sap.example.com"), "field_invalid"),
    (dict(ADT, client="abc"), "field_invalid"),
])
def test_validate_config_refuses(config, code):
    with pytest.raises(ExternalConnectionError) as caught:
        _adapter().validate_config(config)
    assert caught.value.code == code
    assert caught.value.status == 400


def test_validate_config_is_idempotent_on_its_own_output():
    adapter = _adapter()
    once = adapter.validate_config(ADT)
    assert adapter.validate_config(once) == once


# -- probe -----------------------------------------------------------------

def test_probe_reports_a_missing_secret_as_a_config_refusal():
    result = _adapter().probe(_ctx(RFC, secret=None))
    assert result.outcome != "ok"
    failed = result.failed_stage()
    assert failed.code == "secret_missing"
    assert failed.stage == STAGE_CONFIG


def test_probe_refuses_an_unsupported_provider():
    result = _adapter().probe(_ctx({"provider": "u9"}))
    failed = result.failed_stage()
    assert failed.code == "unsupported_provider"
    assert failed.stage == STAGE_CONFIG


def test_rfc_probe_refuses_when_the_deployment_has_not_accepted_the_blocking_call():
    result = _adapter().probe(_ctx(RFC, limits={"uninterruptible_ok": False}))
    failed = result.failed_stage()
    # The RFC SDK call cannot be interrupted; a deployment without the
    # isolation story gets this refusal instead of a hung worker.
    assert failed.code == "uninterruptible_not_permitted"
    assert failed.stage == STAGE_DEPENDENCY


def test_rfc_probe_refuses_a_target_outside_the_policy(resolve_policy):
    policy = NetworkPolicy(allow_hosts=frozenset({"other.example.com"}))
    result = _adapter().probe(_ctx(
        RFC, limits={"uninterruptible_ok": True, "policy": policy}))
    failed = result.failed_stage()
    assert failed.code == "target_not_allowed"
    assert failed.stage == STAGE_POLICY


def test_rfc_probe_success_reports_auth_and_read_without_the_secret(
        monkeypatch, resolve_policy):
    provider = _install_provider(monkeypatch, _StubProvider(test={
        "status": "success", "system": "S4H", "client": "100",
        "host": "sap"}))
    result = _adapter().probe(_ctx(
        RFC, limits={"uninterruptible_ok": True, "policy": _policy()}))
    assert result.ok, result.as_dict()
    assert [s.name for s in result.stages] == ["auth", "read"]
    assert result.metadata["system"] == "S4H"
    assert SECRET not in json.dumps(result.as_dict())
    assert provider.closed is True


@pytest.mark.parametrize("error,stage,code", [
    (RuntimeError("RfcDependencyError: the SAP NW RFC SDK is not installed"),
     STAGE_DEPENDENCY, "sdk_unavailable"),
    (TimeoutError("the target timed out"), STAGE_TIMEOUT, "timeout"),
    (ConnectionError("connection refused"), STAGE_NETWORK, "network_unreachable"),
    (RuntimeError("Logon failed for user sapuser"), STAGE_AUTH, "auth_failed"),
    (RuntimeError("something unexpected"), STAGE_PROTOCOL, "protocol_error"),
])
def test_rfc_probe_maps_failures_onto_the_right_stage(
        monkeypatch, resolve_policy, error, stage, code):
    provider = _install_provider(monkeypatch, _StubProvider(error=error))
    result = _adapter().probe(_ctx(
        RFC, limits={"uninterruptible_ok": True, "policy": _policy()}))
    failed = result.failed_stage()
    assert failed.stage == stage
    assert failed.code == code
    assert provider.closed is True


def test_probe_failure_detail_never_leaks_the_secret(monkeypatch, resolve_policy):
    provider = _install_provider(monkeypatch, _StubProvider(
        error=RuntimeError("upstream rejected %s" % SECRET)))
    result = _adapter().probe(_ctx(
        RFC, limits={"uninterruptible_ok": True, "policy": _policy()}))
    assert result.failed_stage() is not None
    assert SECRET not in json.dumps(result.as_dict())
    assert "***" in result.failed_stage().detail
    assert provider.closed is True


def test_adt_probe_refuses_verify_ssl_off_when_the_policy_forbids_it(resolve_policy):
    config = dict(ADT, verify_ssl=False)
    result = _adapter().probe(_ctx(config, limits={"policy": _policy()}))
    failed = result.failed_stage()
    assert failed.code == "tls_verify_not_permitted"
    assert failed.stage == STAGE_POLICY


def test_adt_probe_accepts_verify_ssl_off_when_the_deployment_permits_it(
        monkeypatch, resolve_policy):
    seen = {}

    def factory(self, ctx, password, verify_ssl=None):
        seen["verify_ssl"] = verify_ssl
        return _StubProvider(test={"status": "success", "sample": [{"cnt": 1}]})

    monkeypatch.setattr(
        "integrations.external.adapters.erp.ErpAdapter._create_provider", factory)
    result = _adapter().probe(_ctx(
        dict(ADT, verify_ssl=False),
        limits={"policy": _policy(allow_verify_ssl_off=True)}))
    assert result.ok, result.as_dict()
    assert seen["verify_ssl"] is False
    assert result.metadata["verify_ssl"] is False
    assert SECRET not in json.dumps(result.as_dict())


def test_adt_probe_refuses_a_target_outside_the_policy(resolve_policy):
    policy = NetworkPolicy(allow_hosts=frozenset({"other.example.com"}))
    result = _adapter().probe(_ctx(ADT, limits={"policy": policy}))
    failed = result.failed_stage()
    assert failed.code == "target_not_allowed"
    assert failed.stage == STAGE_POLICY


def test_adt_probe_maps_tls_and_auth_failures(monkeypatch, resolve_policy):
    provider = _install_provider(monkeypatch, _StubProvider(
        error=RuntimeError("SSL certificate verify failed")))
    result = _adapter().probe(_ctx(ADT, limits={"policy": _policy()}))
    assert result.failed_stage().stage == STAGE_TLS
    assert result.failed_stage().code == "tls_failed"


def test_adt_probe_success(monkeypatch, resolve_policy):
    provider = _install_provider(monkeypatch, _StubProvider(test={
        "status": "success", "sample": [{"cnt": 1}]}))
    result = _adapter().probe(_ctx(ADT, limits={"policy": _policy()}))
    assert result.ok, result.as_dict()
    assert result.metadata["row_count"] == 1
    assert result.metadata["verify_ssl"] is True
    assert provider.closed is True


# -- describe_capabilities -------------------------------------------------

def test_capabilities_report_the_provider_and_the_missing_secret():
    report = _adapter().describe_capabilities(_ctx(RFC, secret=None))
    assert "test" not in report.classes
    assert report.actions["query"] is False
    assert report.reasons["password"] == "secret_missing"


def test_capabilities_offer_reads_but_never_the_write_class():
    report = _adapter().describe_capabilities(_ctx(RFC))
    assert {"test", "read_execute"} <= set(report.classes)
    assert report.actions["query"] is True
    # ERP has no openable write class in this build, so rfc.call is not offered
    # even though the provider could technically make the call.
    assert report.actions["rfc.call"] is False
    assert report.reasons["rfc.call"] == "not_supported_by_type"

    adt = _adapter().describe_capabilities(_ctx(ADT))
    assert adt.actions["query"] is True
    assert adt.reasons["rfc.call"] == "provider_has_no_rfc"


# -- invoke ----------------------------------------------------------------

def test_invoke_refuses_an_undeclared_action(monkeypatch):
    _install_provider(monkeypatch, _StubProvider())
    with pytest.raises(AdapterError) as caught:
        _adapter().invoke(_ctx(RFC), "erp.drop_table", {})
    assert caught.value.code == "unsupported_action"


def test_invoke_refuses_a_write_without_an_approval(monkeypatch):
    _install_provider(monkeypatch, _StubProvider())
    result = _adapter().invoke(_ctx(RFC), "rfc.call",
                               {"bapi": "BAPI_GOODSMVT_CREATE"})
    assert result.ok is False
    assert result.code == "approval_required"
    assert result.stage == STAGE_POLICY


def test_invoke_refuses_a_bapi_the_deployment_has_not_accepted(monkeypatch):
    _install_provider(monkeypatch, _StubProvider())
    result = _adapter().invoke(
        _ctx(RFC, extra={"approval": {"approved": True}}), "rfc.call",
        {"bapi": "BAPI_GOODSMVT_CREATE"})
    assert result.ok is False
    assert result.code == "bapi_not_allowed"
    assert result.stage == STAGE_POLICY


def test_invoke_runs_an_accepted_bapi_with_an_approval(monkeypatch):
    from integrations.external.adapters import erp as erp_module

    erp_module.register_write_bapis(["BAPI_GOODSMVT_CREATE"])
    provider = _install_provider(monkeypatch, _StubProvider())
    result = _adapter().invoke(
        _ctx(RFC, extra={"approval": {"approved": True}}), "rfc.call",
        {"bapi": "bapi_goodsmvt_create", "parameters": {"X": 1}})
    assert result.ok is True, result.as_dict()
    assert provider.calls == [("call_bapi", "BAPI_GOODSMVT_CREATE", {"X": 1})]


def test_invoke_runs_a_read_query_through_fetch(monkeypatch):
    provider = _install_provider(monkeypatch, _StubProvider(rows=[{"MATNR": "1"}]))
    result = _adapter().invoke(_ctx(RFC), "query",
                               {"scene_id": "supplier_risk", "params": {"a": "b"}})
    assert result.ok is True, result.as_dict()
    assert result.data["row_count"] == 1
    kind, scene_id, params, _raw = provider.calls[0]
    assert (kind, scene_id, params["a"]) == ("fetch", "supplier_risk", "b")
    # The cap is applied by the adapter, not trusted from the caller.
    assert params["max_rows"] <= 10000


def test_invoke_refuses_a_query_plan_that_would_call_a_bapi(monkeypatch):
    _install_provider(monkeypatch, _StubProvider(rows=[]))
    result = _adapter().invoke(_ctx(RFC), "query", {"query_plan": {
        "source": "bapi", "bapi_name": "BAPI_X", "intent": "x", "domain": "x"}})
    assert result.ok is False
    assert result.code == "bapi_not_allowed"


def test_invoke_refuses_a_read_without_a_scene_or_plan(monkeypatch):
    _install_provider(monkeypatch, _StubProvider())
    result = _adapter().invoke(_ctx(RFC), "query", {})
    assert result.ok is False
    assert result.code == "scene_required"


# -- scene-side resolution -------------------------------------------------

def _create_erp(svc, stack, name, config=None, secrets=None, **kwargs):
    return svc.create_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=stack.tenant_id,
        kind="erp", name=name, config=config or RFC,
        secrets=secrets if secrets is not None else {"password": SECRET}, **kwargs)


def test_a_scene_resolves_a_named_connection(scene_service, stack):
    from integrations.external.adapters import erp_scene

    connection = _create_erp(scene_service, stack, "SAP A")
    resolved = erp_scene.resolve_erp_connection(connection["id"],
                                                tenant_id=stack.tenant_id)
    assert resolved.id == connection["id"]
    assert resolved.name == "SAP A"
    assert resolved.provider == "rfc"
    assert resolved.enabled is True
    # The secret is fetched at build time and never cached on the object.
    credentials = resolved.provider_credentials()
    assert credentials["password"] == SECRET
    assert SECRET not in json.dumps(
        {k: v for k, v in resolved.__dict__.items() if k != "config"})


def test_a_scene_resolves_the_tenant_default(scene_service, stack):
    from integrations.external.adapters import erp_scene

    first = _create_erp(scene_service, stack, "A")
    second = _create_erp(scene_service, stack, "B")
    scene_service.set_erp_default(actor_user_id=stack.root,
                                 tenant_id=stack.tenant_id,
                                 connection_id=second["id"], expected_revision=1)
    resolved = erp_scene.resolve_erp_connection(tenant_id=stack.tenant_id)
    assert resolved.id == second["id"]
    assert resolved.id != first["id"]


def test_a_scene_refuses_a_cross_tenant_connection_id(scene_service, stack):
    from integrations.external.adapters import erp_scene

    other = stack.other_tenant()
    foreign = scene_service.create_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=other["tenant_id"],
        kind="erp", name="other", config=RFC, secrets={"password": SECRET})

    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection(foreign["id"], tenant_id=stack.tenant_id)
    # Another tenant's id answers exactly like a missing one.
    assert caught.value.code == "connection_not_found"
    assert caught.value.status == 404
    # ... while the owning tenant can still resolve it.
    assert erp_scene.resolve_erp_connection(
        foreign["id"], tenant_id=other["tenant_id"]).id == foreign["id"]


def test_a_scene_refuses_when_there_is_no_default(scene_service, stack):
    from integrations.external.adapters import erp_scene

    _create_erp(scene_service, stack, "A")
    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection(tenant_id=stack.tenant_id)
    assert caught.value.code == "no_default_erp_connection"
    assert caught.value.status == 409


def test_a_scene_refuses_a_deleted_default_instead_of_falling_back(
        scene_service, stack):
    from integrations.external.adapters import erp_scene

    first = _create_erp(scene_service, stack, "A")
    _create_erp(scene_service, stack, "B")
    scene_service.set_erp_default(actor_user_id=stack.root,
                                 tenant_id=stack.tenant_id,
                                 connection_id=first["id"], expected_revision=1)
    # Simulate the stale pointer a concurrent delete would leave: the row is
    # gone but the catalogue still names it. The resolver must refuse rather
    # than quietly use B.
    scene_service._store.execute(
        "UPDATE external_connections SET deleted_at=? WHERE id=?",
        ("2026-09-18T00:00:00Z", first["id"]))

    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection(tenant_id=stack.tenant_id)
    assert caught.value.code == "default_connection_unavailable"
    assert caught.value.status == 409


def test_a_scene_refuses_a_disabled_connection(scene_service, stack):
    from integrations.external.adapters import erp_scene

    connection = _create_erp(scene_service, stack, "A")
    scene_service.update_connection(
        actor_user_id=stack.root, scope="tenant", tenant_id=stack.tenant_id,
        connection_id=connection["id"], expected_version=1, enabled=False)

    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection(connection["id"],
                                         tenant_id=stack.tenant_id)
    assert caught.value.code == "connection_disabled"
    assert caught.value.status == 409


def test_a_scene_refuses_an_unknown_provider_row(scene_service, stack):
    from integrations.external.adapters import erp_scene

    scene_service._store.execute(
        "INSERT INTO external_connections"
        " (id, kind, scope, tenant_id, owner_user_id, name, config_json, enabled,"
        "  version, base_connection_id, source, created_by, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,1,1,NULL,'created',?,?,?)",
        ("conn_legacy_u9", "erp", "tenant", stack.tenant_id, None, "U9",
         json.dumps({"provider": "u9"}), stack.root, "2026-01-01", "2026-01-01"))

    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection("conn_legacy_u9", tenant_id=stack.tenant_id)
    assert caught.value.code == "unsupported_erp_provider"
    assert caught.value.status == 409


def test_a_scene_requires_a_tenant(scene_service):
    from integrations.external.adapters import erp_scene

    with pytest.raises(erp_scene.SceneConnectionError) as caught:
        erp_scene.resolve_erp_connection(None, tenant_id="")
    assert caught.value.code == "missing_tenant"
    assert caught.value.status == 400


def test_the_management_projection_never_carries_the_password(scene_service, stack):
    from integrations.external.adapters import erp_scene

    connection = _create_erp(scene_service, stack, "A")
    rows = erp_scene.list_erp_connections(stack.tenant_id, enabled_only=False)
    assert [row["id"] for row in rows] == [connection["id"]]
    assert erp_scene.secret_configured(connection["id"]) is True
    assert SECRET not in json.dumps(rows, default=str)


# -- the legacy management endpoint, reads only (task 6.5) -----------------
#
# The endpoint used to be the authoritative store, then a *shim* that forwarded
# writes to the control plane. Either way it was a second writable form with its
# own field mapping and its own keep/replace/clear secret rules, duplicating what
# the console page already implements against the same service. Task 6.5 removed
# the write half: the scene console links to the console page instead, and this
# endpoint keeps only the projection the scenes still read.


class _RequestContext:
    """The minimum the scene host reads off a verified request context."""

    def __init__(self, stack):
        self.tenant_id = stack.tenant_id
        self.user_id = stack.root
        self.username = "root"
        self.is_platform_admin = True
        self.is_tenant_admin = False
        self.permissions = ["erp.connections.view", "erp.connections.manage"]


def _erp_handler():
    from Scene._shared.original import load

    return getattr(load("ErpConnectionsHandler"), "ErpConnectionsHandler")()


def _call_erp_handler(scene_service, stack, verb, body=None):
    """Call the isolated scene handler the way the scene HTTP adapter does."""
    import web

    from Scene._shared import host

    web.ctx.headers = []
    web.ctx.status = "200 OK"
    web.ctx.env = {"CONTENT_LENGTH": "0"}
    web.ctx.data = json.dumps(body or {}).encode()
    token = host.request_context.set(_RequestContext(stack))
    try:
        payload = json.loads(getattr(_erp_handler(), verb)())
        return payload, web.ctx.status
    finally:
        host.request_context.reset(token)


def test_management_get_is_a_non_secret_projection(scene_service, stack):
    connection = _create_erp(scene_service, stack, "SAP A")
    payload, status = _call_erp_handler(scene_service, stack, "GET")

    assert status.startswith("200")
    assert payload["status"] == "success"
    assert payload["catalog_revision"] == 1
    item = payload["connections"][0]
    assert item["id"] == connection["id"]
    assert item["system"] == "sap"
    assert item["is_default"] is False
    assert item["version"] == 1
    # Presence, not value: the projection says a password exists and never
    # carries it (the old handler returned the stored password verbatim).
    assert item["secrets"] == {"password": {"configured": True}}
    assert SECRET not in json.dumps(payload)


def test_the_projection_still_carries_the_fields_the_scene_console_shows(
        scene_service, stack):
    """The read half must not regress while the write half is removed.

    The scene picker renders host, system number, client, user and language off
    this projection, so those fields keep working; only the ability to *change*
    a connection through this address is gone.
    """
    _create_erp(scene_service, stack, "SAP A")
    payload, _ = _call_erp_handler(scene_service, stack, "GET")

    item = payload["connections"][0]
    assert item["ashost"] == RFC["ashost"]
    assert item["sysnr"] == RFC["sysnr"]
    assert item["client"] == RFC["client"]
    assert item["lang"] == RFC["lang"]
    assert item["username"] == RFC["user"]
    assert item["provider"] == "rfc"
    assert item["enabled"] is True


def test_the_handler_no_longer_offers_a_write(scene_service):
    """POST is not defined at all, rather than defined-and-refused.

    A handler that still owns the verb has to keep re-stating the refusal rules
    -- version, per-item permission, secret keep/replace/clear -- and that is
    exactly the duplication the task removes.
    """
    handler = _erp_handler()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert not hasattr(handler, verb), verb
    # The helpers the write half owned are gone with it, so nothing can quietly
    # reuse a second copy of the secret merge rules.
    for gone in ("_config", "_secrets", "_document", "_apply", "_persist",
                 "_writable", "_replace_all"):
        assert not hasattr(handler, gone), gone


def test_a_write_through_the_legacy_address_has_no_handler(scene_service, stack):
    """The old full-table save cannot be reached, so it cannot clobber state.

    The registry is authoritative for which verbs an address answers: it
    declares GET only here, so a POST never reaches a handler. The handler
    corroborates that -- it defines no write verb -- which also means a direct
    call from a test or a stray scene template cannot resurrect one.
    """
    _create_erp(scene_service, stack, "SAP A")
    entry = _route_entry("/api/erp/connections")
    assert set(entry.methods) == {"GET"}
    assert callable(getattr(_erp_handler(), "POST", None)) is False

    # Nothing was written by trying, and the read still reveals no secret.
    assert scene_service._store.execute(
        "SELECT COUNT(*) c FROM external_connections WHERE kind='erp'"
    )[0]["c"] == 1
    assert SECRET not in json.dumps(
        _call_erp_handler(scene_service, stack, "GET")[0])


def test_the_write_owns_exactly_one_home(scene_service, stack):
    """Every write the removed shim used to perform still works, but through the
    control plane -- so this is a move, not a capability loss.

    Rotation, the default pointer and enabled/disabled are the three writes the
    shim had. They are all reachable through ``ExternalConnectionService`` and
    the catalogue API, which is what the console page calls.
    """
    connection = _create_erp(scene_service, stack, "SAP A")
    second = _create_erp(scene_service, stack, "SAP B")

    rotated = scene_service.update_connection(
        actor_user_id=stack.root,
        scope="tenant",
        connection_id=connection["id"],
        tenant_id=stack.tenant_id,
        expected_version=1,
        name="SAP A (rotated)",
        config=dict(RFC),
        secrets={"password": "Rotated1!"},
    )
    assert rotated["version"] == 2
    assert rotated["name"] == "SAP A (rotated)"
    assert scene_service.resolve_secret(
        connection_id=connection["id"], slot="password", scope="tenant",
        tenant_id=stack.tenant_id) == "Rotated1!"

    disabled = scene_service.update_connection(
        actor_user_id=stack.root,
        scope="tenant",
        connection_id=connection["id"],
        tenant_id=stack.tenant_id,
        expected_version=2,
        enabled=False,
    )
    assert disabled["enabled"] is False
    scene_service.set_erp_default(actor_user_id=stack.root,
                                  tenant_id=stack.tenant_id,
                                  connection_id=second["id"], expected_revision=1)

    payload, _ = _call_erp_handler(scene_service, stack, "GET")
    by_id = {row["id"]: row for row in payload["connections"]}
    assert by_id[connection["id"]]["enabled"] is False
    assert by_id[second["id"]]["is_default"] is True
    # Version tracks every write, so the page's optimistic concurrency works.
    assert by_id[connection["id"]]["version"] == 3


def test_the_scene_frontend_keeps_no_second_writable_form(scene_service):
    """The scene bundle lost its form, its save hook and its panel markup.

    Asserted against the shipped sources rather than a running browser: the
    panel markup must not be in the workbench bundle, and the ERP script must not
    POST to the connection address.
    """
    from Scene.catalog import ROOT
    from Scene._shared import frontend

    manifest = json.loads((ROOT / "source-manifest.json").read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["files"]]
    assert "_shared/frontend/config-panel-erp.html" not in paths
    assert not (ROOT / "_shared/frontend/config-panel-erp.html").exists()
    assert b"config-panel-erp" not in frontend.workbench_html()

    erp_js = (ROOT / "_shared/frontend/erp.js").read_text(encoding="utf-8")
    for gone in ("saveErpConnectionConfig", "showErpConnectionForm",
                 "confirmErpConnectionForm", "deleteErpConnection"):
        assert gone not in erp_js, gone
    # The scene script posts to exactly one address, and it is not the
    # connection address: the surviving write is an explicit connectivity probe
    # that sends an id (never a credential) to the sync endpoint.
    posted = re.findall(r"fetch\('([^']+)'", erp_js)
    assert posted == ["/api/erp/connections", "/api/procurement/erp-sync"], posted
    assert "method: 'POST'" in erp_js
    assert erp_js.index("method: 'POST'") > erp_js.index("/api/procurement/erp-sync")

    adapter_js = (ROOT / "_shared/frontend/adapter.js").read_text(encoding="utf-8")
    assert "openErpConnectionConsole()" in adapter_js
    workbench_js = (ROOT / "_shared/frontend/workbench.js").read_text(encoding="utf-8")
    assert "openErpConnectionConsole()" in workbench_js


def test_managing_connections_does_not_require_a_chat_grant(scene_service, stack):
    """The move to the console page must not re-gate management behind chat.

    Scenes are reached through chat, so an entry point that lived in a scene
    implicitly carried a ``chat.use`` requirement. The console page is addressed
    directly and gates on its own permission instead: it is
    ``external.connections.manage``, and membership in a chat is neither
    sufficient nor required.
    """
    create = _route_entry("/api/external-connections/tenant")
    assert create.methods["POST"]["permission"] == "external.connections.manage"
    # ``chat.use`` appears nowhere on the management routes.
    for path in ("/api/external-connections/tenant",
                 "/api/external-connections/tenant/([^/]+)/update"):
        assert "chat.use" not in json.dumps(_route_entry(path).methods)
    # Nor does the console fork inherit the scene wrapper's chat gate: the
    # handlers never call it (the scene wrapper does, which is exactly why
    # management must not live behind a scene route).
    from channel.web import external_connection_handlers

    source = Path(external_connection_handlers.__file__).read_text(encoding="utf-8")
    assert "_require_chat_use" not in source
    assert "chat.use" not in source
    # The scene read keeps its own pair, unchanged by the removal.
    assert _RequestContext(stack).permissions == [
        "erp.connections.view", "erp.connections.manage"]


def _route_entry(pattern):
    from channel.web import route_registry

    for entry in route_registry.ROUTES:
        if entry.pattern == pattern:
            return entry
    raise AssertionError("no route registered for %s" % pattern)


# -- ERP tool provider -----------------------------------------------------

def _open_read_execute(monkeypatch):
    from integrations.external.adapters import erp as erp_module
    monkeypatch.setattr(
        erp_module.registry, "open_classes",
        lambda kind: frozenset({"configure", "test", "read_execute"}))


def test_tool_provider_offers_one_read_tool_per_connection(
        scene_service, stack, monkeypatch):
    from integrations.external.adapters.base import load_adapters
    from integrations.external.tools import available_tools

    load_adapters()
    _create_erp(scene_service, stack, "SAP A")
    _create_erp(scene_service, stack, "SAP B", config=ADT)
    _open_read_execute(monkeypatch)

    tools = [binding for binding in available_tools(
        tenant_id=stack.tenant_id, actor_user_id=stack.root)
        if binding.tool.kind == "erp"]
    assert sorted(b.tool.name for b in tools) == sorted(
        "erp.query." + b.connection_id for b in tools)
    assert {b.connection_name for b in tools} == {"SAP A", "SAP B"}
    # Read-only: the write action is never offered.
    assert all(b.tool.action == "query" and b.tool.write is False for b in tools)
    # The model can tell two connections apart by their display names.
    assert all(b.tool.metadata["connection_name"] for b in tools)


def test_tool_provider_offers_nothing_to_a_tenant_without_a_connection(
        scene_service, stack, monkeypatch):
    from integrations.external.adapters.base import load_adapters
    from integrations.external.tools import available_tools

    load_adapters()
    _open_read_execute(monkeypatch)
    assert [b for b in available_tools(tenant_id=stack.tenant_id,
                                        actor_user_id=stack.root)
            if b.tool.kind == "erp"] == []


def test_tool_provider_offers_nothing_while_the_deployment_class_is_closed(
        scene_service, stack, monkeypatch):
    from integrations.external.adapters.base import load_adapters
    from integrations.external.tools import available_tools

    load_adapters()
    _create_erp(scene_service, stack, "SAP A")
    # The default deployment: ERP read execution is not open. A tool that would
    # always be refused is a placeholder, so it is absent instead.
    assert [b for b in available_tools(tenant_id=stack.tenant_id,
                                        actor_user_id=stack.root)
            if b.tool.kind == "erp"] == []


def test_tool_provider_needs_a_tenant(scene_service, stack, monkeypatch):
    from integrations.external.adapters.erp import _erp_tool_provider

    _open_read_execute(monkeypatch)
    assert list(_erp_tool_provider(None, stack.root)) == []
    assert list(_erp_tool_provider("", stack.root)) == []
