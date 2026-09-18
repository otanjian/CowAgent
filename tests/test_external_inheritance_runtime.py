# encoding:utf-8
"""A tenant reaches a platform MCP template without ever holding its secret.

Change ``add-external-system-access``, task 5.2's runtime half. The *data*
semantics of inheritance (create an override, restore inheritance, refuse to
delete a template a tenant still references) were built and tested with the
service; what was missing is the read path. Until it existed, a granted template
was unreachable at runtime — its row has ``tenant_id IS NULL``, so the
tenant-scoped lookup could not see it — and a tenant override ignored the
template's state entirely, so revoking a grant left the tenant running.

The spec sentences under test (``mcp-connection-integration``):

    平台撤销可用范围、停用或删除 SHALL 阻止该平台项及其覆盖继续执行

    #### Scenario: 平台撤销已有覆盖的可用性
    - **WHEN** 平台取消某租户使用某平台 MCP
    - **THEN** 该租户不能通过旧覆盖、旧连接池或恢复继承继续使用它

    覆盖 SHALL ... 使用租户自己的完整有效配置与凭据，MUST NOT 复制、暴露或借用平台秘密

Three properties, in the order they matter:

* **Reachable when granted.** The template resolves, and it resolves *as the
  platform row* so the platform's own secret is what the adapter would be given
  — the tenant must not need a copy to use it.
* **Closed when revoked.** Disabling the platform row, revoking the grant,
  disabling the grant, or soft-deleting the row all turn the effective snapshot
  ``enabled=False``, for a bare template *and* for a tenant override. The
  override is the case worth naming: it is the tenant's own row, and reading it
  alone would say "enabled".
* **Not a catalogue oracle.** A tenant that was never granted the template gets
  ``not_found``, the same answer a non-existent id gets, so it cannot enumerate
  the platform catalogue by guessing ids.

The store, the service and the secret tables are real; the adapter is never
reached, because every assertion is about which row wins and whether it is
usable.
"""

from __future__ import annotations

import pytest

from integrations.external import registry
from tests._helpers import build_identity

MASTER_KEY = "inheritance-master-key"
MCP_CONFIG = {"transport": "streamable_http",
              "url": "https://mcp.example.com/mcp"}
PLATFORM_SECRET = "platform-only-token"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture
def stack(tmp_path, monkeypatch):
    from config import conf
    from integrations.external import service as service_module

    stack = build_identity(tmp_path)
    monkeypatch.setitem(conf(), "identity_db_path", str(tmp_path / "identity.db"))
    service_module._SERVICE_CACHE.clear()
    return stack


@pytest.fixture
def svc(stack):
    from integrations.external.service import ExternalConnectionService
    return ExternalConnectionService(stack.service)


def _template(svc, stack, name="平台模板"):
    """A platform MCP template, with a platform-held credential."""
    return svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_PLATFORM, kind="mcp",
        name=name, config=dict(MCP_CONFIG),
        secrets={"header": PLATFORM_SECRET})


def _revision(svc, stack, template_id) -> int:
    """The tenant-access revision the console would have read."""
    return int(svc.list_tenant_access(
        actor_user_id=stack.root,
        platform_connection_id=template_id)["revision"])


def _grant(stack, svc, template_id, *, granted=True):
    """Grant or revoke this tenant's access to a platform template.

    The service replaces the whole tenant list rather than toggling one row,
    which is what makes a revocation atomic; ``expected_revision`` is the
    optimistic-lock value the caller read.
    """
    svc.set_tenant_access(
        actor_user_id=stack.root, platform_connection_id=template_id,
        tenant_ids=[stack.tenant_id] if granted else [],
        expected_revision=_revision(svc, stack, template_id))


def _disable_platform_row(svc, stack, template_id):
    """Disable the template itself, as a platform administrator would."""
    svc.update_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_PLATFORM,
        connection_id=template_id, enabled=False,
        expected_version=svc.get_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_PLATFORM,
            connection_id=template_id)["version"])


def _delete_platform_row(svc, stack, template_id):
    svc.delete_connection(
        actor_user_id=stack.root, connection_id=template_id,
        scope=registry.SCOPE_PLATFORM,
        expected_version=svc.get_connection(
            actor_user_id=stack.root, scope=registry.SCOPE_PLATFORM,
            connection_id=template_id)["version"])


def _runtime(svc):
    return svc.runtime()


# -- reachable when granted --------------------------------------------------

def test_a_granted_template_resolves_for_the_tenant_that_was_granted_it(
        svc, stack):
    """The template resolves, as itself, so its own secret is the effective one."""
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])

    snap = _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)

    assert snap.id == template["id"]
    assert snap.scope == registry.SCOPE_PLATFORM
    assert snap.enabled is True
    assert snap.source == "inherited"
    assert snap.source_connection_id == template["id"]
    assert snap.source_version == int(template["version"])
    # The config is the platform's, and the tenant holds no copy of it.
    assert snap.config.get("url") == MCP_CONFIG["url"]


def test_the_adapter_is_handed_the_platform_secret_not_a_tenant_copy(
        svc, stack):
    """借用平台秘密 would be a copy in the tenant's row; there is none.

    The resolver reads the platform row's reference, which is the only place the
    secret exists — so "the tenant can use it" and "the tenant can read it" stay
    different statements, which is the distinction the spec draws.
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    snap = _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)

    context = svc.runtime().build_context(snap, actor_user_id=stack.root)

    assert context.secret_resolver("header") == PLATFORM_SECRET
    # And nothing was copied into the tenant's own namespace on the way.
    assert svc._store.execute(
        "SELECT id FROM external_connections WHERE tenant_id=? AND"
        " kind='mcp'", (stack.tenant_id,)) == []


def test_a_tenant_override_wins_over_the_template_it_overrides(svc, stack):
    """One effective connection per logical connection.

    Naming either id resolves to the override, which is the same rule
    ``_cards`` publishes as ``effective_id``; if the two disagreed, the console
    would show one row and the runtime would execute another.
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config={"transport": "streamable_http",
                "url": "https://tenant.example.com/mcp"})

    by_template = _runtime(svc).snapshot(template["id"],
                                         tenant_id=stack.tenant_id)
    by_override = _runtime(svc).snapshot(override["id"],
                                         tenant_id=stack.tenant_id)

    assert by_template.id == override["id"] == by_override.id
    assert by_template.source == "override"
    assert by_template.source_connection_id == template["id"]
    assert by_template.source_version == int(template["version"])
    assert by_template.config.get("url") == "https://tenant.example.com/mcp"


# -- closed when revoked -----------------------------------------------------

def test_a_revoked_grant_closes_a_bare_template(svc, stack):
    """平台撤销可用范围 — the tenant cannot keep using what was taken away."""
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    assert _runtime(svc).snapshot(
        template["id"], tenant_id=stack.tenant_id).enabled is True

    _grant(stack, svc, template["id"], granted=False)

    with pytest.raises(Exception):
        _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)


def test_a_revoked_grant_closes_a_tenant_override_too(svc, stack):
    """The case reading only the tenant row gets wrong.

    The override is the tenant's own row and its own ``enabled`` flag is 1, so a
    lookup that never consulted the platform side would report a usable
    connection. 旧覆盖 in the spec's scenario is exactly this row.
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config=dict(MCP_CONFIG))
    assert _runtime(svc).snapshot(
        override["id"], tenant_id=stack.tenant_id).enabled is True

    _grant(stack, svc, template["id"], granted=False)

    snap = _runtime(svc).snapshot(override["id"], tenant_id=stack.tenant_id)
    assert snap.id == override["id"]
    assert snap.enabled is False
    assert snap.source == "override"


def test_disabling_the_template_closes_the_override(svc, stack):
    """平台停用 — the same rule, reached by the other lever."""
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config=dict(MCP_CONFIG))

    _disable_platform_row(svc, stack, template["id"])

    assert _runtime(svc).snapshot(
        override["id"], tenant_id=stack.tenant_id).enabled is False


def test_deleting_the_template_makes_it_unreachable(svc, stack):
    """平台删除 — the API-level path, through the reference rule.

    The service refuses to delete a template a tenant still references, so the
    honest sequence is: the tenant gives up its override, the grant is revoked,
    and only then is the template deleted — leaving the tenant with nothing to
    reach. Deleting first is refused rather than cascaded, which is the other
    half of the same requirement.
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config=dict(MCP_CONFIG))

    from integrations.external.errors import ExternalConnectionError

    # Deleted while referenced: refused, with the reference summary the console
    # needs to send the operator to the blocking item.
    with pytest.raises(ExternalConnectionError) as conflict_:
        _delete_platform_row(svc, stack, template["id"])
    assert conflict_.value.status == 409

    svc.restore_inheritance(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"],
        expected_version=override["version"])
    _grant(stack, svc, template["id"], granted=False)
    _delete_platform_row(svc, stack, template["id"])

    with pytest.raises(ExternalConnectionError) as absent:
        _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)
    assert absent.value.status == 404


def test_a_template_deleted_out_of_band_still_closes_a_live_override(
        svc, stack):
    """Defense in depth for the same rule.

    ``delete_connection`` refuses while a reference exists, so this state is not
    reachable through the API — but a row can disappear underneath a live
    override in other ways (a migration, a manual intervention, a future
    cascade). The runtime therefore fails closed on its own instead of relying
    on the delete guard having been the only way in.
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config=dict(MCP_CONFIG))
    assert _runtime(svc).snapshot(
        override["id"], tenant_id=stack.tenant_id).enabled is True

    svc._store.execute(  # noqa: SLF001 - deliberately bypassing the guard
        "UPDATE external_connections SET deleted_at=1 WHERE id=?",
        (template["id"],))

    assert _runtime(svc).snapshot(
        override["id"], tenant_id=stack.tenant_id).enabled is False


def test_restoring_inheritance_makes_the_template_effective_again(svc, stack):
    """恢复继承 — the override is gone, and the tenant reaches the template.

    Both halves matter: before the restore the override is effective, after it
    the template is, and the template's own secret is what the tenant gets
    (平台秘密不回显到租户表单 is satisfied by the tenant never holding it at all).
    """
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config={"transport": "streamable_http",
                "url": "https://tenant.example.com/mcp"})

    svc.restore_inheritance(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"],
        expected_version=override["version"])

    snap = _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)
    assert snap.id == template["id"]
    assert snap.source == "inherited"
    assert snap.config.get("url") == MCP_CONFIG["url"]
    assert svc.runtime().build_context(
        snap, actor_user_id=stack.root).secret_resolver("header") == (
            PLATFORM_SECRET)


# -- not a catalogue oracle --------------------------------------------------

def test_an_ungranted_template_is_not_found_not_forbidden(svc, stack):
    """A tenant must not learn the platform catalogue by id.

    ``not_found`` is the same answer an invented id gets, so the two are
    indistinguishable to a caller holding no grant. A ``forbidden`` here would
    confirm the id exists.
    """
    template = _template(svc, stack)

    from integrations.external.errors import ExternalConnectionError

    with pytest.raises(ExternalConnectionError) as refusal:
        _runtime(svc).snapshot(template["id"], tenant_id=stack.tenant_id)
    assert refusal.value.status == 404

    with pytest.raises(ExternalConnectionError) as absent:
        _runtime(svc).snapshot("conn_does_not_exist",
                               tenant_id=stack.tenant_id)
    assert absent.value.status == 404


def test_one_tenants_grant_does_not_open_another_tenants_view(svc, stack):
    """授权按 tenant_access 逐租户判定，不是"模板已获准"这一个全局事实."""
    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    stack.other_tenant()

    from integrations.external.errors import ExternalConnectionError

    with pytest.raises(ExternalConnectionError) as refusal:
        _runtime(svc).snapshot(template["id"], tenant_id="other")
    assert refusal.value.status == 404


# -- discovery agrees with the runtime ---------------------------------------

def test_the_listing_offers_a_granted_template_and_hides_an_ungranted_one(
        svc, stack):
    """Discovery must not offer what the runtime refuses, nor hide what it runs."""
    from integrations.external.adapters.mcp import list_mcp_connections

    template = _template(svc, stack, name="平台模板")
    assert [row["id"] for row in
            list_mcp_connections(stack.tenant_id)] == []

    _grant(stack, svc, template["id"])
    assert [row["id"] for row in
            list_mcp_connections(stack.tenant_id)] == [template["id"]]

    _grant(stack, svc, template["id"], granted=False)
    assert [row["id"] for row in
            list_mcp_connections(stack.tenant_id)] == []


def test_the_listing_offers_the_override_insted_of_the_template_it_hides(
        svc, stack):
    """Once overridden, only the effective row is offered — a template that is
    still listed would give the same capability two tool names."""
    from integrations.external.adapters.mcp import list_mcp_connections

    template = _template(svc, stack)
    _grant(stack, svc, template["id"])
    override = svc.create_override(
        actor_user_id=stack.root, tenant_id=stack.tenant_id,
        platform_connection_id=template["id"], name="本租户覆盖",
        config=dict(MCP_CONFIG))

    listed = [row["id"] for row in list_mcp_connections(stack.tenant_id)]
    assert listed == [override["id"]]
