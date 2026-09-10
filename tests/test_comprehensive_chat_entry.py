# encoding:utf-8
"""Entering a conversation must not require choosing an Agent.

A *session* still has to be anchored to one Agent — it lives in that Agent's
workspace and carries its skills, tools and model. What must not happen is the
server refusing an Agent-less request just because the tenant owns several
Agents and never picked a default, which is what forced the picker in front of
every new chat.

The tenant's default Agent is therefore always resolvable: configured default
first, then the tenant's single bound Agent, then a deterministic choice among
several. The global default is never borrowed — it may belong to another tenant.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import web

from auth.runtime import RequestContext
from auth.service import IdentityService


@pytest.fixture()
def env(monkeypatch):
    """One tenant, two users, and a service the state_dir layer will resolve."""
    import types

    from channel.web import web_channel as wc
    from agent.registry import AgentProfile

    db = os.path.join(tempfile.mkdtemp(), "identity.db")
    svc = IdentityService(db)
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=tempfile.mkdtemp(prefix="shared-"), allow_weak=True)
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users()[0]

    monkeypatch.setattr("auth.service.get_identity_service", lambda: svc)

    # ``web.HTTPError`` writes a header, which needs a per-request ctx in scope.
    web.ctx.headers = []

    ctx = RequestContext(
        user_id=root["id"], username="root", display_name="Root",
        is_platform_admin=False, must_change_password=False,
        tenant_id=tid, membership=None, permissions=set(), is_tenant_admin=True)

    return types.SimpleNamespace(svc=svc, tid=tid, root=root, wc=wc, ctx=ctx)


# --- the resolver --------------------------------------------------------

def test_configured_default_wins(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-b")
    env.svc._appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-b", actor_user_id=env.root["id"])
    assert env.wc._tenant_default_agent_id(env.ctx) == "agent-b"


def test_single_bound_agent_is_the_implicit_default(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="only-one")
    assert env.wc._tenant_default_agent_id(env.ctx) == "only-one"


def test_several_agents_without_default_resolve_deterministically(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-b")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")

    first = env.wc._tenant_default_agent_id(env.ctx)
    assert first is not None, "a tenant with runnable Agents has no default to chat with"
    assert first == "agent-a", f"expected the stable identifier order, got {first!r}"
    # Stable across calls: the resolver must not depend on binding insert order.
    assert env.wc._tenant_default_agent_id(env.ctx) == first


def test_agent_less_request_is_accepted_for_a_multi_agent_tenant(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-b")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    # No explicit agent_id: this used to be a 403 "default agent ambiguous".
    assert env.wc._require_tenant_agent_binding(env.ctx, None) in ("agent-a", "agent-b")


def test_explicit_agent_still_wins(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-b")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    assert env.wc._require_tenant_agent_binding(env.ctx, "agent-b") == "agent-b"


# --- boundaries ----------------------------------------------------------

def test_tenant_without_agents_is_still_refused(env):
    with pytest.raises(web.HTTPError) as exc:
        env.wc._require_tenant_agent_binding(env.ctx, None)
    assert exc.value.args[0] == "403 Forbidden"


def test_no_cross_tenant_fallback(env):
    """Another tenant's Agent, or the global default, must never be borrowed."""
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    other = env.svc.create_tenant(
        actor_user_id=env.root["id"], code="beta", name="Beta",
        shared_root=tempfile.mkdtemp(prefix="shared-b-"), admin_username="betaadmin",
        admin_display="Beta", admin_password="Str0ngPass2",
        recent_password="Str0ngAdminPass")
    env.svc.bind_agent(tenant_id=other["id"], agent_id="agent-other")

    resolved = env.wc._tenant_default_agent_id(env.ctx)
    assert resolved == "agent-a"

    # An explicit request for another tenant's Agent is a 404, as before.
    with pytest.raises(web.HTTPError) as exc:
        env.wc._require_tenant_agent_binding(env.ctx, "agent-other")
    assert exc.value.args[0] == "404 Not Found"
