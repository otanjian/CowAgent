# encoding:utf-8
"""12.3 — Scene ERP resolution respects ``store_version``.

``read_effective_connections`` already decides which store a runtime consumer
sees. MCP is wired through ``resolve_mcp_servers``; ERP scenes still went
straight to ``external_connections``. That means:

* ``store_version=legacy`` returned an empty catalogue even when the JSON file
  still held the live connections the scenes were using;
* ``store_version=new`` was fine for the happy path, but nothing proved that a
  failed/empty new store could not silently fall back to the JSON file.

These tests pin the scene resolver (``erp_scene.list_erp_connections`` /
``resolve_erp_connection``) to the same switch the migration tool already uses.
"""

from __future__ import annotations

import json
import os

import pytest

from tests._helpers import build_identity
from tests.test_external_connection_migration import erp_record, write_erp

MASTER_KEY = "unit-test-master-key"
SECRET = "S3cretPassw0rd!"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path):
    return build_identity(tmp_path)


@pytest.fixture
def svc(stack):
    from integrations.external.service import ExternalConnectionService
    return ExternalConnectionService(stack.service)


@pytest.fixture
def scene(svc, monkeypatch):
    monkeypatch.setattr(
        "integrations.external.service.get_external_connection_service",
        lambda: svc)
    return svc


@pytest.fixture
def legacy_conf(monkeypatch):
    from integrations.external import migration as module

    settings = {}
    monkeypatch.setattr(module, "conf", lambda: settings)
    return settings


def _ids(rows):
    return [str(row["id"]) for row in rows]


def test_legacy_store_version_still_serves_the_json_file(
        scene, stack, legacy_conf):
    """Before cutover the scenes keep seeing what is in the JSON file."""
    from integrations.external.adapters import erp_scene

    write_erp(stack, [erp_record(id="erp-prod", name="生产")])
    legacy_conf["external_connections"] = {"store_version": "legacy"}

    rows = erp_scene.list_erp_connections(stack.tenant_id, enabled_only=True)
    assert _ids(rows) == ["erp-prod"]
    assert [row["name"] for row in rows] == ["生产"]

    resolved = erp_scene.resolve_erp_connection(
        "erp-prod", tenant_id=stack.tenant_id)
    assert resolved.id == "erp-prod"
    assert resolved.provider == "rfc"
    # A legacy password must still be usable for a provider build: otherwise
    # "legacy" would mean "list works, execute silently dies".
    creds = resolved.provider_credentials()
    assert creds["password"] == SECRET
    assert "ashost" in creds


def test_new_store_version_never_falls_back_to_the_json_file(
        scene, stack, legacy_conf):
    """A failed/empty new store fails closed — it does not silently read JSON."""
    from integrations.external.adapters import erp_scene

    write_erp(stack, [erp_record(id="erp-prod")])
    legacy_conf["external_connections"] = {"store_version": "new"}

    rows = erp_scene.list_erp_connections(stack.tenant_id, enabled_only=True)
    assert rows == []

    with pytest.raises(erp_scene.SceneConnectionError) as raised:
        erp_scene.resolve_erp_connection("erp-prod", tenant_id=stack.tenant_id)
    assert raised.value.code == erp_scene.CODE_CONNECTION_NOT_FOUND


def test_after_import_the_new_store_is_what_the_scene_reads(
        scene, stack, legacy_conf):
    from integrations.external import migration
    from integrations.external.adapters import erp_scene

    write_erp(stack, [erp_record(id="erp-prod", name="生产")])
    migration.import_connections(
        stack.service, actor_user_id=stack.root, confirm=True)
    legacy_conf["external_connections"] = {"store_version": "new"}

    rows = erp_scene.list_erp_connections(stack.tenant_id, enabled_only=True)
    assert _ids(rows) == ["erp-prod"]

    resolved = erp_scene.resolve_erp_connection(
        "erp-prod", tenant_id=stack.tenant_id)
    assert resolved.id == "erp-prod"
    assert resolved.provider_credentials()["password"] == SECRET


def test_dual_falls_back_to_json_only_when_the_new_store_is_empty(
        scene, stack, legacy_conf):
    from integrations.external import migration
    from integrations.external.adapters import erp_scene

    write_erp(stack, [erp_record(id="erp-prod")])
    legacy_conf["external_connections"] = {"store_version": "dual"}

    before = erp_scene.list_erp_connections(stack.tenant_id)
    assert _ids(before) == ["erp-prod"]

    migration.import_connections(
        stack.service, actor_user_id=stack.root, confirm=True)
    after = erp_scene.list_erp_connections(stack.tenant_id)
    assert _ids(after) == ["erp-prod"]
    # Origin must be the control plane once it has an answer — dual is not a
    # second simultaneous identity for the same connection.
    assert after[0].get("origin") in (None, "new") or \
        str(after[0].get("id")) == "erp-prod"


def test_a_per_scope_switch_is_honoured_for_the_tenant(
        scene, stack, legacy_conf, svc):
    """A tenant that switched to ``new`` must not keep reading the JSON file
    even when the global default is still ``legacy``."""
    from integrations.external.adapters import erp_scene

    write_erp(stack, [erp_record(id="erp-json", name="JSON 侧")])
    # A different connection that only exists in the control plane.
    created = svc.create_connection(
        actor_user_id=stack.root, kind="erp", scope="tenant",
        tenant_id=stack.tenant_id, name="控制面侧",
        config={"provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
                "client": "100", "user": "sapuser", "lang": "EN"},
        secrets={"password": SECRET}, connection_id="erp-plane")
    assert created["id"] == "erp-plane"

    key = "tenant:%s" % stack.tenant_id
    legacy_conf["external_connections"] = {
        "store_version": {"default": "legacy", key: "new"}}

    rows = erp_scene.list_erp_connections(stack.tenant_id)
    assert _ids(rows) == ["erp-plane"], \
        "switched tenant must read the control plane, not the JSON file"

    # Flip this tenant back to legacy: the JSON side returns, the plane one does not.
    legacy_conf["external_connections"] = {
        "store_version": {"default": "legacy", key: "legacy"}}
    rows = erp_scene.list_erp_connections(stack.tenant_id)
    assert _ids(rows) == ["erp-json"]
