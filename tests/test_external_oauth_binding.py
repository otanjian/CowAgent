# encoding:utf-8
"""An OAuth callback completes the flow that started, or it completes nothing.

Change ``add-external-system-access``, task 5.3. The existing MCP OAuth chain
already had a one-time ``state`` (a random value, popped on first use). That
defends against *replay* and against nothing else: it cannot tell whether the
request arriving with the state is still the request the state was issued for.
The spec asks for more (``mcp-connection-integration``):

    OAuth 授权、回调、令牌更新与撤销 MUST 绑定发起主体、连接、租户或平台范围及配置
    版本，并验证一次性状态。

    #### Scenario: 回调时已切换租户
    - **WHEN** OAuth 回调与发起时的主体、范围或连接版本不再匹配
    - **THEN** 授权结果不绑定到当前连接，也不改变新租户配置

The callback is unauthenticated, so the binding cannot be checked against a
session. It is checked against the *store*, which is what these tests exercise:
a real identity database, a real connection row, and the real verifier. No test
contacts an authorization server, because the question under test is only
whether the completion still fits the start.

The last section is about the parts that surround the decision — the pending
record's single-use property, the token's stored binding, and revocation —
because a verifier nobody consults, or a token that outlives its reason, would
satisfy the letter of the callback tests while leaving the flow open.
"""

from __future__ import annotations

import pytest

from integrations.external import oauth_binding, registry
from tests._helpers import build_identity

MASTER_KEY = "oauth-binding-master-key"
MCP_CONFIG = {"transport": "streamable_http",
              "url": "https://mcp.example.com/mcp"}


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


@pytest.fixture
def connection(svc, stack):
    return svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        tenant_id=stack.tenant_id, kind="mcp", name="团队 MCP",
        config=dict(MCP_CONFIG))


def _bound(stack, connection, *, version=None, scope=None, tenant=None,
           actor=None) -> dict:
    return oauth_binding.binding(
        actor_user_id=actor or stack.root,
        tenant_id=tenant if tenant is not None else stack.tenant_id,
        scope=scope or connection["scope"],
        connection_id=connection["id"],
        config_version=connection["version"] if version is None else version)


def _verify(record, svc, stack):
    return oauth_binding.verify_callback(record, service=svc,
                                         identity=stack.service)


# -- the happy path, so a refusal below means something ----------------------

def test_a_matching_callback_is_accepted(svc, stack, connection):
    ok, reason = _verify(_bound(stack, connection), svc, stack)
    assert ok, reason


# -- 回调时已切换租户 --------------------------------------------------------

def test_a_callback_for_another_tenant_is_refused(svc, stack, connection):
    """The scenario's literal case: the tenant changed between start and finish.

    The connection belongs to ``stack.tenant_id``; a completion carrying another
    tenant is refused, so the token is bound to neither tenant and the new one's
    configuration is untouched.
    """
    stack.other_tenant()

    ok, reason = _verify(_bound(stack, connection, tenant="other"), svc, stack)

    assert ok is False
    assert "tenant" in reason


def test_a_member_removed_before_the_callback_cannot_complete_it(
        svc, stack, connection):
    """主体绑定 is a live fact, so a revoked membership is not a proof.

    The membership row is deactivated directly, for the same reason the deleted
    template is: ``update_member`` enforces that an enabled account keeps at
    least one active tenant, so it refuses to leave a single-tenant member
    tenant-less. The check must not depend on that invariant holding — a future
    invite/removal path, an admin tool or a direct write can all produce this
    state, and accepting the callback would let it leave a live credential
    behind a member who can no longer use it.
    """
    member = stack.member("carol", ["member"])
    record = _bound(stack, connection, actor=member)
    assert _verify(record, svc, stack)[0] is True

    stack.service._store.execute(  # noqa: SLF001 - deliberately bypassing the guard
        "UPDATE memberships SET active=0 WHERE user_id=? AND tenant_id=?",
        (member, stack.tenant_id))

    ok, reason = _verify(record, svc, stack)
    assert ok is False
    assert "member" in reason


def test_a_platform_admin_demoted_before_the_callback_cannot_complete_it(
        svc, stack):
    """The platform-scope half of the same rule.

    A platform template has no tenant, so the proof is "the authorizing subject
    is still a platform administrator" rather than "still a member". An ordinary
    member is refused and the real administrator is accepted — both halves, so
    the branch cannot be dead code that refuses everything.
    """
    platform = svc.create_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_PLATFORM, kind="mcp",
        name="平台模板", config=dict(MCP_CONFIG))
    member = stack.member("carol", ["member"])

    refused, _ = _verify(_bound(stack, platform, scope="platform", tenant="",
                                actor=member), svc, stack)
    assert refused is False

    # ``stack.root`` is the platform administrator the bootstrap created.
    ok, reason = _verify(_bound(stack, platform, scope="platform", tenant="",
                                actor=stack.root), svc, stack)
    assert ok, reason


# -- 配置版本 ----------------------------------------------------------------

def test_a_callback_after_the_connection_was_edited_is_refused(
        svc, stack, connection):
    """配置版本 — a token minted for version n must not attach to version n+1.

    The operator may have changed the endpoint or the scope while the user was
    consenting, so the grant no longer describes the connection it would be
    stored against.
    """
    record = _bound(stack, connection)
    svc.update_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        connection_id=connection["id"], tenant_id=stack.tenant_id,
        expected_version=connection["version"],
        config={"transport": "streamable_http",
                "url": "https://elsewhere.example.com/mcp"})

    ok, reason = _verify(record, svc, stack)

    assert ok is False
    assert "modified" in reason


def test_a_callback_after_the_connection_was_disabled_is_refused(
        svc, stack, connection):
    """A connection switched off mid-consent must not come back online.

    Otherwise the console's disable is undone by completing a flow that was
    started before it, which is a way to re-enable without the permission.
    """
    record = _bound(stack, connection)
    svc.update_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        connection_id=connection["id"], tenant_id=stack.tenant_id,
        expected_version=connection["version"], enabled=False)

    ok, reason = _verify(record, svc, stack)

    assert ok is False
    assert "disabled" in reason


def test_a_callback_after_the_connection_was_deleted_is_refused(
        svc, stack, connection):
    """授权结果不绑定到当前连接 — a deleted connection binds nothing."""
    record = _bound(stack, connection)
    svc.delete_connection(
        actor_user_id=stack.root, scope=registry.SCOPE_TENANT,
        connection_id=connection["id"], tenant_id=stack.tenant_id,
        expected_version=connection["version"])

    ok, reason = _verify(record, svc, stack)

    assert ok is False
    assert "no longer exists" in reason


def test_a_callback_for_a_connection_that_changed_scope_is_refused(
        svc, stack, connection):
    """Scope is part of the binding, not just the tenant.

    A token issued for a connection configured as tenant-owned must not be
    accepted for one that has since become a platform template — the two have
    different owners and different secret stores.
    """
    record = _bound(stack, connection, scope="platform")

    ok, reason = _verify(record, svc, stack)

    assert ok is False
    assert "scope" in reason


# -- an unbound flow is not an accepted flow --------------------------------

def test_an_unbound_record_is_refused_rather_than_ignored():
    """The failure mode this module exists to close.

    A missing field must read as "cannot be proven", never as "nothing
    disagreed". Returning True here would make every check below vacuous the
    moment a caller forgot to populate one.
    """
    for record in ({}, None, {"actor_user_id": "u"}, {"actor_user_id": "u",
                                                     "tenant_id": "t",
                                                     "scope": "tenant",
                                                     "connection_id": "c",
                                                     "config_version": 0}):
        assert oauth_binding.is_bound(record) is False
        ok, _ = oauth_binding.verify_callback(record)
        assert ok is False


def test_a_broken_store_is_a_refusal_not_a_pass(stack):
    """Fail closed: an unreadable store has not said "allowed"."""
    record = oauth_binding.binding(
        actor_user_id=stack.root, tenant_id=stack.tenant_id, scope="tenant",
        connection_id="conn_x", config_version=1)

    class _Boom:
        _identity = stack.service

        class _store:
            @staticmethod
            def execute(*_a, **_k):
                raise RuntimeError("store down")

    ok, reason = oauth_binding.verify_callback(record, service=_Boom())

    assert ok is False
    assert ok is not True and reason


# -- the pending record and the stored token ---------------------------------

@pytest.fixture
def _isolated_oauth_store(tmp_path, monkeypatch):
    """Point the OAuth token store at this test's directory."""
    from agent.tools.mcp import mcp_oauth
    monkeypatch.setattr(mcp_oauth, "_store_path",
                        lambda: str(tmp_path / "mcp_oauth.json"))
    return mcp_oauth


def test_the_state_is_single_use_so_a_replay_finds_nothing(_isolated_oauth_store):
    """The property the chain already had, kept under test.

    It is easy to lose while adding the binding: a verifier that returned the
    handler without popping would make every callback replayable.
    """
    oauth = _isolated_oauth_store
    oauth._PENDING.clear()
    handler = oauth.OAuthHandler("srv", "https://mcp.example.com/mcp", "http://x/cb")
    oauth._register_pending("state-1", handler, binding={"a": 1})

    assert oauth.take_pending("state-1") is handler
    assert oauth.take_pending("state-1") is None


def test_a_refused_callback_consumes_its_state(
        _isolated_oauth_store, svc, stack, connection):
    """A refusal must not leave the state usable.

    Otherwise a refused completion could be retried — with corrected parameters,
    or against a connection that later matches — which would make the binding a
    speed bump rather than a gate.
    """
    oauth = _isolated_oauth_store
    oauth._PENDING.clear()
    handler = oauth.OAuthHandler("srv", "https://mcp.example.com/mcp", "http://x/cb")
    oauth._register_pending("state-2", handler,
                            binding=_bound(stack, connection, tenant="other"))

    assert oauth.take_pending("state-2",
                              verify=lambda b: _verify(b, svc, stack)) is None
    assert oauth.take_pending("state-2") is None


def test_the_recorded_binding_survives_into_the_stored_token(
        _isolated_oauth_store, stack, connection):
    """绑定发起主体 — the binding has to outlive the flow to be checkable later.

    The version is what makes a stale token refuse to be presented after an edit,
    so it is written with the token rather than held only in memory.
    """
    oauth = _isolated_oauth_store
    record = _bound(stack, connection)
    handler = oauth.OAuthHandler("srv", "https://mcp.example.com/mcp",
                                 "http://x/cb", binding=record)
    handler.access_token = "at"
    handler.refresh_token = "rt"
    handler._persist()

    reloaded = oauth.OAuthHandler("srv", "https://mcp.example.com/mcp",
                                  "http://x/cb")

    assert reloaded.bound_config_version == int(connection["version"])
    assert reloaded.is_current(int(connection["version"])) is True
    assert reloaded.is_current(int(connection["version"]) + 1) is False


def test_an_unversioned_authorization_is_current_only_because_it_is_unbound(
        _isolated_oauth_store):
    """A plain ``mcp.json`` server has no version to disagree with.

    Stated as a test so the exemption is deliberate: the check exists to stop a
    *stale* grant, and a server with no connection row cannot have one.
    """
    oauth = _isolated_oauth_store
    handler = oauth.OAuthHandler("plain", "https://mcp.example.com/mcp",
                                 "http://x/cb")

    assert handler.bound_config_version == 0
    assert handler.is_current(7) is True


def test_revoking_clears_the_stored_authorization(_isolated_oauth_store):
    """撤销 — the next call must re-authorize instead of reusing the grant."""
    oauth = _isolated_oauth_store
    handler = oauth.OAuthHandler("srv", "https://mcp.example.com/mcp", "http://x/cb")
    handler.access_token = "at"
    handler.refresh_token = "rt"
    handler._persist()
    assert oauth.load_server_record("srv").get("access_token") == "at"

    assert handler.revoke() is True

    assert oauth.load_server_record("srv") == {}
    assert handler.get_valid_access_token() is None
    # And a second revoke reports that there was nothing left to revoke, rather
    # than claiming a change it did not make.
    assert handler.revoke() is False
