# encoding:utf-8
"""External-connection management API over the real WSGI application.

Change ``add-external-system-access``, tasks 3.1-3.7. These tests drive
``build_web_app()`` — the real route table, the real HTTP policy gate, real
sessions and the real ``identity.db`` — because the properties that matter here
are not the handler's arithmetic:

* the scope lives in the path, so a payload cannot re-point a connection at
  another tenant or owner;
* the tenant/permission and platform gates refuse *before* the handler runs, and
  a plain member reaches only their own mailbox;
* the write origin guard applies to every state change;
* a concurrent writer loses on the version, and a still-referenced connection is
  refused with the summary of what references it;
* a secret crosses the wire once (in), and never comes back out — only its
  ``configured`` state does;
* a platform connection is invisible to a tenant until it is explicitly granted,
  and a tenant override is a real second connection with its own credentials.

Unit-level behaviour of the service and the store is covered separately in
``tests/test_external_connection_service.py`` and
``tests/test_external_connection_schema.py``.
"""

from __future__ import annotations

import json

import pytest

from tests._helpers import WebAppHarness

MASTER_KEY = "api-test-master-key"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


ERP_RFC = {
    "provider": "rfc", "ashost": "sap.example.com", "sysnr": "00",
    "client": "100", "user": "sapuser", "lang": "EN",
}
MCP_HEADER = {
    "transport": "streamable_http", "url": "https://mcp.example.com/mcp",
    "auth": "header", "header_name": "X-Api-Key",
}
EMAIL_BOTH = {
    "imap": {"enabled": True, "host": "imap.example.com",
             "user": "alice@example.com"},
    "smtp": {"enabled": True, "host": "smtp.example.com",
             "user": "alice@example.com", "from_addr": "alice@example.com"},
}
SECRET = "S3cretPassw0rd!"


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    harness = WebAppHarness(tmp_path_factory.mktemp("external-api"))
    harness.add_agent("shared-agent")
    harness.role("conn-reader", ["external.connections.read", "chat.use"])
    harness.role("conn-manager", ["external.connections.read",
                                  "external.connections.manage", "chat.use"])
    harness.member("reader", ["conn-reader"])
    harness.member("manager", ["conn-manager"])
    harness.member("plain", ["member"])
    harness.admin_token = harness.login("root")
    harness.reader_token = harness.login("reader")
    harness.manager_token = harness.login("manager")
    harness.plain_token = harness.login("plain")
    yield harness
    harness.close()


def _json(response):
    return json.loads(response.data.decode("utf-8"))


# -- catalogue reads -------------------------------------------------------

def test_a_member_can_read_the_type_catalogue(web):
    response = web.get("/api/external-connections/types", token=web.plain_token)
    assert response.status == "200 OK"
    body = _json(response)
    by_kind = {item["kind"]: item for item in body["types"]}
    assert set(by_kind) == {"mcp", "erp", "oa", "email"}
    # Mail is the one type a plain member may create, and only personally.
    assert [s["scope"] for s in by_kind["email"]["scopes"]] == ["personal"]
    assert by_kind["email"]["scopes"][0]["available"] is True
    assert by_kind["erp"]["scopes"][0]["scope"] == "tenant"
    assert by_kind["erp"]["scopes"][0]["available"] is False


def test_the_type_catalogue_reports_test_and_execute_as_closed(web):
    body = _json(web.get("/api/external-connections/types",
                         token=web.manager_token))
    for item in body["types"]:
        assert item["capabilities"]["test_available"] is False
        assert item["capabilities"]["execute_available"] is False
        assert item["capabilities"]["unavailable_reason"].startswith("awaiting_")


def test_a_platform_catalogue_read_is_refused_for_a_non_admin(web):
    response = web.get("/api/external-connections/catalog?scope=platform",
                       token=web.manager_token)
    assert response.status == "403 Forbidden"
    assert _json(response)["code"] == "forbidden"


def test_a_tenant_catalogue_read_needs_the_read_permission(web):
    refused = web.get("/api/external-connections/catalog?scope=tenant",
                      token=web.plain_token)
    assert refused.status == "403 Forbidden"
    allowed = web.get("/api/external-connections/catalog?scope=tenant",
                      token=web.reader_token)
    assert allowed.status == "200 OK"
    assert _json(allowed)["scope"] == "tenant"


# -- personal mailbox ------------------------------------------------------

def test_a_member_creates_and_reads_their_own_mailbox(web):
    created = web.post("/api/external-connections/personal", {
        "kind": "email", "name": "我的邮箱", "config": EMAIL_BOTH,
        "secrets": {"imap_password": SECRET, "smtp_password": SECRET},
    }, token=web.plain_token)
    assert created.status == "200 OK", created.data
    body = _json(created)
    assert body["status"] == "success"
    connection_id = body["id"]
    assert connection_id.startswith("conn_")

    detail = web.get("/api/external-connections/personal/%s" % connection_id,
                     token=web.plain_token)
    assert detail.status == "200 OK"
    payload = _json(detail)
    assert payload["secrets"]["imap_password"] == {"configured": True}
    # The plaintext never leaves the server, and neither does the ciphertext.
    assert SECRET not in detail.data.decode("utf-8")
    assert MASTER_KEY not in detail.data.decode("utf-8")

    # A second connection for the same member is refused: one mailbox each.
    again = web.post("/api/external-connections/personal", {
        "kind": "email", "name": "第二个", "config": EMAIL_BOTH,
    }, token=web.plain_token)
    assert again.status == "409 Conflict"
    assert _json(again)["code"] == "singleton_exists"


def test_another_member_cannot_read_someone_elses_mailbox(web):
    created = _json(web.post("/api/external-connections/personal", {
        "kind": "email", "name": "manager mail", "config": EMAIL_BOTH,
        "secrets": {"imap_password": SECRET, "smtp_password": SECRET},
    }, token=web.manager_token))
    connection_id = created["id"]

    refused = web.get("/api/external-connections/personal/%s" % connection_id,
                      token=web.plain_token)
    # Another member's mailbox answers exactly like a missing one.
    assert refused.status == "404 Not Found"
    assert _json(refused)["code"] == "not_found"


def test_personal_scope_ignores_a_body_owner(web):
    """Owner comes from the session; a body cannot name another member."""
    response = web.post("/api/external-connections/personal", {
        "kind": "email", "name": "reader mail", "config": EMAIL_BOTH,
        "owner_user_id": web.user_id("manager"),
        "tenant_id": "tenant-somewhere-else",
    }, token=web.reader_token)
    assert response.status == "200 OK"
    connection_id = _json(response)["id"]
    assert _json(web.get("/api/external-connections/personal/%s" % connection_id,
                         token=web.reader_token))["owner_user_id"] == \
        web.user_id("reader")


# -- tenant scope ----------------------------------------------------------

def test_a_plain_member_cannot_create_a_tenant_connection(web):
    response = web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "SAP", "config": ERP_RFC,
        "secrets": {"password": SECRET},
    }, token=web.plain_token)
    assert response.status == "403 Forbidden"


def _create_tenant_erp(web, name="SAP 生产"):
    created = web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": name, "config": ERP_RFC,
        "secrets": {"password": SECRET},
    }, token=web.manager_token)
    assert created.status == "200 OK", created.data
    return _json(created)


def test_a_manager_creates_a_tenant_erp_connection(web):
    connection = _create_tenant_erp(web, "SAP 生产")
    connection_id = connection["id"]
    assert connection["scope"] == "tenant"
    assert connection["secrets"]["password"] == {"configured": True}

    detail = _json(web.get("/api/external-connections/tenant/%s" % connection_id,
                           token=web.manager_token))
    assert detail["config"]["provider"] == "rfc"
    assert SECRET not in json.dumps(detail)


def test_a_read_only_role_cannot_update_but_can_read(web):
    connection_id = _create_tenant_erp(web, "SAP read-only")["id"]
    readable = web.get("/api/external-connections/tenant/%s" % connection_id,
                       token=web.reader_token)
    assert readable.status == "200 OK"
    assert _json(readable)["actions"] == ["read"]

    refused = web.post(
        "/api/external-connections/tenant/%s/update" % connection_id,
        {"expected_version": 1, "name": "renamed"}, token=web.reader_token)
    assert refused.status == "403 Forbidden"

    refused_delete = web.post(
        "/api/external-connections/tenant/%s/delete" % connection_id,
        {"expected_version": 1}, token=web.reader_token)
    assert refused_delete.status == "403 Forbidden"


def test_a_stale_version_over_http_is_a_409(web):
    connection_id = _create_tenant_erp(web, "SAP stale")["id"]
    first = web.post("/api/external-connections/tenant/%s/update" % connection_id,
                     {"expected_version": 1, "name": "first"},
                     token=web.manager_token)
    assert first.status == "200 OK"
    stale = web.post("/api/external-connections/tenant/%s/update" % connection_id,
                     {"expected_version": 1, "name": "second"},
                     token=web.manager_token)
    assert stale.status == "409 Conflict"
    assert _json(stale)["code"] == "version_conflict"


def test_secret_keep_replace_and_clear_over_http(web):
    created = _json(web.post("/api/external-connections/tenant", {
        "kind": "oa", "name": "OA", "config": {
            "base_url": "https://oa.example.com", "username": "alice"},
        "secrets": {"password": SECRET},
    }, token=web.manager_token))
    connection_id = created["id"]

    # A blank entry keeps the stored secret (a form re-submit is not a rotation).
    kept = _json(web.post(
        "/api/external-connections/tenant/%s/update" % connection_id,
        {"expected_version": 1, "name": "OA", "secrets": {"password": ""}},
        token=web.manager_token))
    assert kept["secrets"]["password"] == {"configured": True}

    # A masked value is refused rather than stored as the new credential.
    masked = web.post(
        "/api/external-connections/tenant/%s/update" % connection_id,
        {"expected_version": 2, "secrets": {"password": "OA •••• rd"}},
        token=web.manager_token)
    assert masked.status == "400 Bad Request"
    assert _json(masked)["code"] == "masked_secret"

    # A value replaces it, and the version moves on.
    replaced = _json(web.post(
        "/api/external-connections/tenant/%s/update" % connection_id,
        {"expected_version": 2, "secrets": {"password": "Replaced1!"}},
        token=web.manager_token))
    assert replaced["secrets"]["password"] == {"configured": True}

    # An explicit null clears it, and the projection says the slot is missing.
    cleared = _json(web.post(
        "/api/external-connections/tenant/%s/update" % connection_id,
        {"expected_version": 3, "secrets": {"password": None}},
        token=web.manager_token))
    assert cleared["secrets"]["password"] == {"configured": False}
    assert cleared["capabilities"]["missing_secret_slots"] == ["password"]


def test_a_tenant_write_without_a_tenant_header_is_refused(web):
    response = web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "SAP", "config": ERP_RFC,
    }, token=web.manager_token, tenant=False)
    assert response.status in ("400 Bad Request", "403 Forbidden")


def test_a_cross_origin_write_is_refused(web):
    response = web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "SAP", "config": ERP_RFC,
    }, token=web.manager_token,
        headers={"Origin": "http://evil.example.com"})
    assert response.status == "403 Forbidden"
    assert _json(response)["code"] == "csrf_failed"


def test_the_erp_default_round_trips_and_blocks_deletion(web):
    first = _json(web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "ERP A", "config": ERP_RFC,
    }, token=web.manager_token))
    second = _json(web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "ERP B", "config": ERP_RFC,
    }, token=web.manager_token))

    before = _json(web.get("/api/external-connections/tenant/erp-default",
                           token=web.manager_token))
    assert before == {"status": "success", "connection_id": None, "revision": 1}

    set_default = web.post("/api/external-connections/tenant/erp-default", {
        "connection_id": first["id"], "expected_revision": before["revision"],
    }, token=web.manager_token)
    assert set_default.status == "200 OK", set_default.data
    assert _json(set_default)["revision"] == 2

    refused = web.post(
        "/api/external-connections/tenant/%s/delete" % first["id"],
        {"expected_version": 1}, token=web.manager_token)
    assert refused.status == "409 Conflict"
    body = _json(refused)
    assert body["code"] == "referenced"
    assert body["references"][0]["kind"] == "erp_default"
    assert body["references"][0]["next_action"] == "set_erp_default"

    swapped = web.post(
        "/api/external-connections/tenant/%s/delete" % first["id"],
        {"expected_version": 1,
         "default_handling": {"action": "replace",
                              "connection_id": second["id"]}},
        token=web.manager_token)
    assert swapped.status == "200 OK", swapped.data
    after = _json(web.get("/api/external-connections/tenant/erp-default",
                          token=web.manager_token))
    assert after["connection_id"] == second["id"]


# -- platform scope --------------------------------------------------------

def _create_platform(web, name="shared"):
    created = web.post("/api/external-connections/platform", {
        "kind": "mcp", "name": name, "config": MCP_HEADER,
        "secrets": {"header": SECRET},
    }, token=web.admin_token)
    assert created.status == "200 OK", created.data
    platform = _json(created)
    assert platform["scope"] == "platform"
    return platform["id"]


def _grant(web, platform_id, tenant_ids):
    current = _json(web.get(
        "/api/external-connections/platform/%s/tenant-access" % platform_id,
        token=web.admin_token))
    granted = web.post(
        "/api/external-connections/platform/%s/tenant-access" % platform_id,
        {"tenant_ids": list(tenant_ids),
         "expected_revision": current["revision"]}, token=web.admin_token)
    assert granted.status == "200 OK", granted.data
    return _json(granted)


def test_platform_scope_is_a_platform_admin_address(web):
    refused = web.post("/api/external-connections/platform", {
        "kind": "mcp", "name": "shared", "config": MCP_HEADER,
    }, token=web.manager_token)
    assert refused.status == "403 Forbidden"

    platform_id = _create_platform(web, "shared-admin-address")

    # A non-admin cannot read the platform detail either.
    assert web.get("/api/external-connections/platform/%s" % platform_id,
                   token=web.manager_token).status == "403 Forbidden"

    # The platform secret is never returned, only its presence.
    detail = _json(web.get("/api/external-connections/platform/%s" % platform_id,
                           token=web.admin_token))
    assert detail["secrets"]["header"] == {"configured": True}
    assert SECRET not in json.dumps(detail)


def test_a_platform_connection_is_invisible_until_granted(web):
    platform_id = _create_platform(web, "shared-invisible")

    before = _json(web.get("/api/external-connections/catalog?scope=tenant",
                           token=web.manager_token))
    assert platform_id not in [item["id"] for item in before["items"]]

    access = _json(web.get(
        "/api/external-connections/platform/%s/tenant-access" % platform_id,
        token=web.admin_token))
    assert access["tenants"] == []
    _grant(web, platform_id, [web.tenant_id])

    after = _json(web.get("/api/external-connections/catalog?scope=tenant",
                          token=web.manager_token))
    card = [item for item in after["items"] if item["id"] == platform_id][0]
    assert card["source"] == "inherited"
    assert card["effective_id"] == platform_id
    assert SECRET not in json.dumps(card)

    # The tenant reads the inherited detail through the tenant address.
    detail = web.get("/api/external-connections/tenant/%s" % platform_id,
                     token=web.manager_token)
    assert detail.status == "200 OK"
    assert _json(detail)["secrets"]["header"] == {"configured": True}

    # Revoking is atomic: the tenant's catalogue goes empty again.
    _grant(web, platform_id, [])
    revoked = _json(web.get("/api/external-connections/catalog?scope=tenant",
                            token=web.manager_token))
    assert platform_id not in [item["id"] for item in revoked["items"]]


def test_a_tenant_override_is_a_second_connection_with_its_own_secret(web):
    platform_id = _create_platform(web, "shared-override")
    _grant(web, platform_id, [web.tenant_id])

    override = web.post("/api/external-connections/tenant", {
        "kind": "mcp", "name": "tenant override", "config": MCP_HEADER,
        "secrets": {"header": "tenant-secret"},
        "base_connection_id": platform_id,
    }, token=web.manager_token)
    assert override.status == "200 OK", override.data
    override_id = _json(override)["id"]
    assert override_id != platform_id

    catalogue = _json(web.get("/api/external-connections/catalog?scope=tenant",
                              token=web.manager_token))
    by_id = {item["id"]: item for item in catalogue["items"]}
    assert by_id[platform_id]["source"] == "overridden"
    assert by_id[platform_id]["effective_id"] == override_id
    assert by_id[override_id]["source"] == "override"

    # The platform source cannot be deleted out from under the tenant's data.
    refused = web.post(
        "/api/external-connections/platform/%s/delete" % platform_id,
        {"expected_version": 1}, token=web.admin_token)
    assert refused.status == "409 Conflict"
    kinds = {ref["kind"] for ref in _json(refused)["references"]}
    assert kinds == {"tenant_overrides", "tenant_access"}

    # Dropping the override restores inheritance.
    restored = web.post(
        "/api/external-connections/tenant/%s/restore-inheritance" % platform_id,
        {"expected_version": 1}, token=web.manager_token)
    assert restored.status == "200 OK", restored.data
    after = _json(web.get("/api/external-connections/catalog?scope=tenant",
                          token=web.manager_token))
    assert [item["source"] for item in after["items"]
            if item["id"] == platform_id] == ["inherited"]


def test_idempotent_create_over_http(web):
    payload = {"kind": "erp", "name": "Idem", "config": ERP_RFC}
    first = web.post("/api/external-connections/tenant", payload,
                     token=web.manager_token,
                     headers={"Idempotency-Key": "http-key-1"})
    replay = web.post("/api/external-connections/tenant", payload,
                      token=web.manager_token,
                      headers={"Idempotency-Key": "http-key-1"})
    assert first.status == "200 OK" and replay.status == "200 OK"
    assert _json(first)["id"] == _json(replay)["id"]

    conflict = web.post("/api/external-connections/tenant",
                        dict(payload, name="Idem other"),
                        token=web.manager_token,
                        headers={"Idempotency-Key": "http-key-1"})
    assert conflict.status == "409 Conflict"
    assert _json(conflict)["code"] == "idempotency_conflict"


def test_an_invalid_body_is_refused_with_a_machine_code(web):
    response = web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "SAP",
        "config": dict(ERP_RFC, password="leaky"),
    }, token=web.manager_token)
    assert response.status == "400 Bad Request"
    body = _json(response)
    assert body["code"] == "secret_in_config"
    assert "password" in body["fields"]


def test_an_anonymous_request_is_refused(web):
    for path in ("/api/external-connections/types",
                 "/api/external-connections/catalog",
                 "/api/external-connections/personal"):
        response = web.get(path) if path != "/api/external-connections/personal" \
            else web.post(path, {"kind": "email", "name": "x", "config": EMAIL_BOTH})
        assert response.status.startswith(("401", "403")), (path, response.status)


def test_a_zero_tenant_platform_admin_can_manage_platform_connections(web):
    """A platform admin with no tenant selection still reaches the platform scope."""
    created = web.post("/api/external-connections/platform", {
        "kind": "mcp", "name": "zero-tenant", "config": MCP_HEADER,
    }, token=web.admin_token, tenant=False)
    assert created.status == "200 OK", created.data
    platform_id = _json(created)["id"]

    detail = web.get("/api/external-connections/platform/%s" % platform_id,
                     token=web.admin_token, tenant=False)
    assert detail.status == "200 OK"
    assert _json(detail)["scope"] == "platform"

    listing = web.get("/api/external-connections/catalog?scope=platform",
                      token=web.admin_token, tenant=False)
    assert listing.status == "200 OK"
    assert platform_id in [item["id"] for item in _json(listing)["items"]]

    # The same caller cannot reach a tenant address without selecting one.
    assert web.get("/api/external-connections/catalog?scope=tenant",
                   token=web.admin_token, tenant=False).status.startswith("400")


def test_a_closed_action_has_no_route_at_all(web):
    """``test``/``execute`` are undeclared, so no path serves them.

    The gate is the *only* refusal that cannot be bypassed by a handler
    forgetting its own check, so the proof is that the address does not exist.
    """
    connection_id = _json(web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "Closed action", "config": ERP_RFC,
    }, token=web.manager_token))["id"]
    response = web.post(
        "/api/external-connections/tenant/%s/test" % connection_id,
        {}, token=web.manager_token)
    assert response.status == "404 Not Found"


def test_the_service_and_the_http_layer_share_one_database(web):
    """A connection created over HTTP is visible to the service directly."""
    from integrations.external.service import get_external_connection_service

    connection = _json(web.post("/api/external-connections/tenant", {
        "kind": "erp", "name": "Shared DB", "config": ERP_RFC,
    }, token=web.manager_token))
    service = get_external_connection_service()
    assert service._store.db_path == web.db_path
    row = service._fetch_row(connection["id"], tenant_id=web.tenant_id)
    assert row is not None and row["name"] == "Shared DB"
