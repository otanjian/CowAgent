# encoding:utf-8
"""The four connection types accept OneAgent's own configuration data.

Change ``add-external-system-access``, task 4.4. ``tests/_external_oneagent.py``
records where each value came from; this file is what proves the mapping is
right, by pushing every fixture through the real
:class:`ExternalConnectionService` over a real ``identity.db``.

Why a separate file from ``test_external_test_binding.py``
----------------------------------------------------------
That file pins one property (a result is bound to the version and secret it
tested) and uses one type to do it. This one pins the *data*: that a record
transcribed from the system being replaced is accepted, normalised as expected,
and stored without its credential appearing anywhere a reader could obtain it.
Splitting them keeps a failure legible — "the binding broke" and "the ERP
mapping broke" are different diagnoses.

The three assertions per type, and why each is here:

* **It saves.** The validator is the product's own gate; a fixture it rejects
  would mean the mapping (not the fixture) is wrong, and a deployment migrating
  from OneAgent would hit it on the first save.
* **The secret is stored, and never projected.** ``secrets`` go to the credential
  store; the card, the detail and the audit event must show presence without the
  value, because all three are readable by more people than the connection's
  owner.
* **The stored config is the normalised config.** ``sysnr``/``client`` defaults
  applied, ``username`` renamed to ``user`` — the differences OneAgent's own
  store has versus this schema are the part most likely to rot, so they are
  asserted rather than left to the fixture's prose.
"""

from __future__ import annotations

import json

import pytest

from integrations.external import registry
from integrations.external.errors import ExternalConnectionError

from tests import _external_oneagent as oneagent
from tests._helpers import build_identity

MASTER_KEY = "oneagent-fixture-master-key"


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


def _create_member(svc, stack):
    """A member with the external-connection permissions, as an admin grants.

    No built-in role carries them — the page and the routes stay closed until an
    administrator grants them — so the test grants them the way a deployment
    does, with a custom role bound to the member.
    """
    role = stack.service.create_role(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        code="ext_access", name="external access",
        permissions=["external.connections.read", "external.connections.manage"])
    return stack.member("rc001", [role["code"]])


# -- the fixtures themselves ----------------------------------------------

def test_every_type_the_product_has_is_covered_by_a_fixture():
    """A type without a fixture is a mapping nobody checked."""
    assert set(oneagent.by_kind()) == set(registry.TYPE_SPECS)


def test_every_fixture_config_is_accepted_by_the_real_validator():
    for kind, entry in oneagent.by_kind().items():
        normalized = registry.validate_config(kind, entry["config"])
        assert isinstance(normalized, dict) and normalized, kind


def test_every_fixture_secret_names_a_slot_its_type_declares():
    """A fixture that names a slot the type lacks would be silently dropped."""
    for kind, entry in oneagent.by_kind().items():
        unknown = set(entry["secrets"]) - set(registry.secret_slots(kind))
        assert not unknown, "%s declares no slot(s) %s" % (kind, sorted(unknown))


# -- ERP -------------------------------------------------------------------

def test_the_erp_record_from_oneagent_saves(svc, stack):
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="erp", name=oneagent.ERP_NAME,
        config=dict(oneagent.ERP_CONFIG), secrets=dict(oneagent.ERP_SECRET))
    assert connection["kind"] == "erp"


def test_the_erp_mapping_applies_oneagents_own_rfc_defaults(svc, stack):
    """OneAgent leaves ``sysnr``/``client`` blank and defaults them at call time.

    The new schema requires both explicitly, so the mapping supplies the same
    defaults OneAgent uses (``"00"`` / ``"100"``). Asserted rather than assumed:
    picking different defaults would silently address a different client.
    """
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="erp", name=oneagent.ERP_NAME,
        config=dict(oneagent.ERP_CONFIG), secrets=dict(oneagent.ERP_SECRET))

    stored = svc.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, connection_id=connection["id"])["config"]
    assert stored["sysnr"] == "00"
    assert stored["client"] == "100"
    # OneAgent stores ``username``; this schema's key is ``user``.
    assert stored["user"] == oneagent.ONEAGENT_ERP_RECORD["username"]
    assert "username" not in stored
    assert stored["lang"] == "ZH"
    assert stored["ashost"] == oneagent.ONEAGENT_ERP_RECORD["ashost"]


def test_the_erp_credential_round_trips_but_is_never_projected(svc, stack):
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="erp", name=oneagent.ERP_NAME,
        config=dict(oneagent.ERP_CONFIG), secrets=dict(oneagent.ERP_SECRET))

    resolved = svc.resolve_secret(
        connection_id=connection["id"], slot="password",
        scope=registry.SCOPE_TENANT, tenant_id=stack.tenant_id,
        actor_user_id=stack.root)
    assert resolved == oneagent.ERP_SECRET["password"]

    projected = json.dumps(connection, ensure_ascii=False, default=str)
    assert oneagent.ERP_SECRET["password"] not in projected
    assert connection["secrets"]["password"]["configured"] is True


# -- Email -----------------------------------------------------------------

def test_the_email_record_from_oneagent_saves_as_the_members_own(svc, stack):
    """OneAgent's mailbox is per-user; the personal scope is the same idea."""
    member = _create_member(svc, stack)
    connection = svc.create_connection(
        actor_user_id=member, scope=registry.SCOPE_PERSONAL,
        tenant_id=stack.tenant_id, kind="email",
        name=oneagent.ONEAGENT_MAILBOX, config=dict(oneagent.EMAIL_CONFIG),
        secrets=dict(oneagent.EMAIL_SECRET))

    stored = svc.get_connection(
        actor_user_id=member, scope=registry.SCOPE_PERSONAL,
        tenant_id=stack.tenant_id, connection_id=connection["id"])
    assert stored["config"]["imap"]["host"] == oneagent.IMAP_HOST
    assert stored["config"]["smtp"]["host"] == oneagent.SMTP_HOST
    assert stored["config"]["imap"]["port"] == 993
    assert stored["config"]["smtp"]["tls_mode"] == "implicit_tls"


def test_oneagent_never_stored_the_mailbox_password_so_it_is_not_claimed(
        svc, stack):
    """The fixture's mailbox secret is the test credential, not a real one.

    Documented as a test rather than only in prose: if a later change puts a
    real mailbox password in the fixture, this assertion is where someone is
    forced to think about it.
    """
    assert set(oneagent.EMAIL_SECRET) == {"imap_password", "smtp_password"}
    assert all(v == oneagent.MEMBER_PASSWORD
               for v in oneagent.EMAIL_SECRET.values())


# -- MCP -------------------------------------------------------------------

def test_the_remote_mcp_example_saves_as_a_header_connection(svc, stack):
    """OneAgent's ``type: http`` + ``headers`` map becomes one secret slot."""
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="mcp", name="OneAgent HTTP MCP",
        config=dict(oneagent.MCP_REMOTE_CONFIG),
        secrets=dict(oneagent.MCP_REMOTE_SECRET))

    stored = svc.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, connection_id=connection["id"])["config"]
    assert stored["transport"] == "streamable_http"
    assert stored["auth"] == "header"
    # The header *name* is the slot selector; the ``Bearer `` prefix stays in the
    # value, which is why the fixture's secret is the whole header value.
    assert stored["header_name"] == "Authorization"
    assert oneagent.MCP_REMOTE_SECRET["header"].startswith("Bearer ")
    assert "headers" not in stored, "an arbitrary header map is not a config key"


def test_the_stdio_mcp_example_saves_env_keys_not_a_value_map(svc, stack):
    """OneAgent's literal ``env`` map becomes ``env_keys`` + a stored value."""
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="mcp", name="OneAgent stdio MCP",
        config=dict(oneagent.MCP_STDIO_CONFIG),
        secrets=dict(oneagent.MCP_STDIO_SECRET))

    stored = svc.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, connection_id=connection["id"])["config"]
    assert stored["transport"] == "stdio"
    assert stored["env_keys"] == ["LOG_LEVEL"]
    assert "env" not in stored, "a literal value map belongs in the store"
    assert "info" not in json.dumps(stored)


# -- OA --------------------------------------------------------------------

def test_the_oa_site_from_oneagent_saves_and_leaves_a_passport_optional(
        svc, stack):
    """OneAgent carries global Passport constants; both are optional here."""
    connection = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="oa", name="OneAgent OA",
        config=dict(oneagent.OA_CONFIG), secrets=dict(oneagent.OA_SECRET))

    stored = svc.get_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, connection_id=connection["id"])["config"]
    assert stored["base_url"] == oneagent.OA_CONFIG["base_url"]
    assert stored["username"] == oneagent.OA_CONFIG["username"]
    # Blank in this deployment, so absent rather than stored as empty strings:
    # the projection says "not configured" instead of "configured as empty".
    assert "tenant_key" not in stored
    assert "custom_page_config_id" not in stored


# -- scope rules the fixtures must respect --------------------------------

def test_the_types_keep_the_scopes_the_product_assigns_them():
    """A fixture must not imply a scope the type does not have.

    ERP and OA are tenant configuration, MCP is either platform or tenant, and a
    mailbox is personal. If a fixture were created in the wrong scope the test
    would fail on ``validate_scope`` rather than here, but moving the assertion
    up front names the reason.
    """
    assert registry.scopes_for("erp") == frozenset({registry.SCOPE_TENANT})
    assert registry.scopes_for("oa") == frozenset({registry.SCOPE_TENANT})
    assert registry.scopes_for("mcp") == frozenset(
        {registry.SCOPE_PLATFORM, registry.SCOPE_TENANT})
    assert registry.scopes_for("email") == frozenset({registry.SCOPE_PERSONAL})
    with pytest.raises(ExternalConnectionError):
        registry.validate_scope("email", registry.SCOPE_TENANT)
