# encoding:utf-8
"""Explicitly choosing a tenant's default Agent, and releasing a deleted one.

A tenant's default Agent already existed as a pointer (``tenants.default_agent_id``)
but nothing could change it after creation: the only writer was "the tenant's
first Agent becomes its default". These tests pin the console-facing choice and
the cleanup that keeps a deleted Agent from staying resolvable.

The cleanup half is not optional. ``agent_bindings`` rows are never removed when
an Agent is deleted, and ``_agent_is_usable`` deliberately treats an Agent the
registry does not know as *usable*. So clearing only the pointer would leave
``resolved_default_agent_id`` returning an Agent that no longer exists.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import web

from agent import team
from agent.registry import AgentRegistry, set_agent_registry
from auth.runtime import RequestContext
from auth.service import IdentityService, IdentityServiceError
from channel.web import web_channel, auth_handlers, admin_handlers
from channel.web.web_channel import _require_session_owner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PLATFORM_PASSWORD = "Str0ngAdminPass"


@pytest.fixture()
def env():
    db = os.path.join(tempfile.mkdtemp(), "identity.db")
    svc = IdentityService(db)
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password=PLATFORM_PASSWORD,
        shared_root=tempfile.mkdtemp(prefix="shared-"), allow_weak=True)
    tid = svc.list_tenants()[0]["id"]
    root = svc.list_platform_users()[0]
    other = svc.create_tenant(
        actor_user_id=root["id"], code="globex", name="Globex",
        shared_root=tempfile.mkdtemp(prefix="globex-"),
        recent_password=PLATFORM_PASSWORD)
    return SimpleNamespace(
        svc=svc, tid=tid, other_tid=other["id"], root_user=root)


def _audit(svc, action):
    return [dict(r) for r in svc._store.execute(
        "SELECT * FROM audit_events WHERE action=?", (action,))]


# --- releasing a deleted Agent -------------------------------------------

def test_release_clears_the_default_pointer_and_the_binding(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-b")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-a", actor_user_id=env.root_user["id"])
    assert env.svc.tenant_default_agent_id(env.tid) == "agent-a"

    env.svc.release_deleted_agent(
        agent_id="agent-a", actor_user_id=env.root_user["id"])

    assert env.svc.tenant_default_agent_id(env.tid) is None
    assert "agent-a" not in env.svc.tenant_agent_ids(env.tid), (
        "a stale binding still makes a deleted Agent resolvable as the default")
    # Resolution now falls back to the tenant's remaining binding.
    assert env.svc.resolved_default_agent_id(env.tid) == "agent-b"


def test_release_detaches_an_agent_owned_by_another_tenant(env):
    """The caller's tenant is not necessarily the binding's tenant.

    ``agent_bindings.agent_id`` is the primary key, so an Agent belongs to
    exactly one tenant — but the operator deleting it need not be in that
    tenant. A platform admin browsing the console under the default tenant can
    delete a roster entry that is bound to a *different* tenant, which is the
    case this test pins. Scoping the release to the caller's tenant would match
    zero rows, leave the binding behind, and ``resolved_default_agent_id`` would
    keep returning an Agent that no longer exists.
    """
    env.svc.bind_agent(tenant_id=env.other_tid, agent_id="agent-shared")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.other_tid, agent_id="agent-shared",
        actor_user_id=env.root_user["id"])

    # Called from the *default* tenant, as the platform console would.
    env.svc.release_deleted_agent(
        agent_id="agent-shared", actor_user_id=env.root_user["id"])

    assert env.svc.get_agent_binding("agent-shared") is None, (
        "the binding survived because it belongs to another tenant")
    assert env.svc.tenant_default_agent_id(env.other_tid) is None, (
        "another tenant kept a default pointing at the deleted Agent")
    assert env.svc.resolved_default_agent_id(env.other_tid) is None, (
        "the deleted Agent is still resolvable from the other tenant")


def test_release_never_touches_other_agents_or_other_tenants(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-mine",
                       private_owner_user_id=env.root_user["id"])
    env.svc.bind_agent(tenant_id=env.other_tid, agent_id="agent-other")
    env.svc.set_tenant_default_agent(
        tenant_id=env.other_tid, agent_id="agent-other",
        actor_user_id=env.root_user["id"])

    env.svc.release_deleted_agent(
        agent_id="agent-a", actor_user_id=env.root_user["id"])

    # The unrelated private Agent keeps its owner and its binding.
    assert env.svc.get_agent_binding("agent-mine")["private_owner_user_id"] \
        == env.root_user["id"]
    # Another tenant's default and binding are untouched.
    assert env.svc.tenant_default_agent_id(env.other_tid) == "agent-other"
    assert "agent-other" in env.svc.tenant_agent_ids(env.other_tid)


def test_release_is_idempotent(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-a", actor_user_id=env.root_user["id"])

    env.svc.release_deleted_agent(
        agent_id="agent-a", actor_user_id=env.root_user["id"])
    env.svc.release_deleted_agent(
        agent_id="agent-a", actor_user_id=env.root_user["id"])

    assert env.svc.tenant_default_agent_id(env.tid) is None
    assert env.svc.get_agent_binding("agent-a") is None


def test_release_writes_an_audit_event(env):
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-a")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-a", actor_user_id=env.root_user["id"])

    env.svc.release_deleted_agent(
        agent_id="agent-a", actor_user_id=env.root_user["id"])

    events = _audit(env.svc, "tenant.release_deleted_agent")
    assert len(events) == 1
    assert events[0]["result"] == "success"


def test_deleting_the_only_binding_leaves_nothing_to_resolve(env):
    """A tenant emptied by a delete must refuse, never borrow another tenant."""
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-solo")
    env.svc.appoint_tenant_default_agent(
        tenant_id=env.tid, agent_id="agent-solo", actor_user_id=env.root_user["id"])

    env.svc.release_deleted_agent(
        agent_id="agent-solo", actor_user_id=env.root_user["id"])

    assert env.svc.resolved_default_agent_id(env.tid) is None, (
        "with no remaining binding there is no default to anchor to")
    # Another tenant still holds an Agent; it must not be borrowed.
    env.svc.bind_agent(tenant_id=env.other_tid, agent_id="agent-other")
    assert env.svc.resolved_default_agent_id(env.tid) is None

    # The user-visible consequence: anchoring an Agent-less session is refused.
    ctx = RequestContext(
        user_id=env.root_user["id"], username="root", display_name="Root",
        is_platform_admin=False, must_change_password=False,
        tenant_id=env.tid, membership=None, permissions=set(), is_tenant_admin=True)
    web.ctx.headers = []
    with patch("auth.service.get_identity_service", return_value=env.svc):
        with pytest.raises(web.HTTPError) as caught:
            _require_session_owner(ctx, "sess-1", None)
    assert "403" in str(caught.value)

    # Positive control: with a binding left, the same call is accepted — so the
    # refusal above really is caused by the emptied tenant.
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-spare")
    web.ctx.headers = []
    with patch("auth.service.get_identity_service", return_value=env.svc):
        _require_session_owner(ctx, "sess-1", None)


def test_an_appointed_agent_is_reachable_by_a_plain_member(env):
    """Appointing shares nothing by itself; the explicit share is what opens it.

    ``private_owner_user_id`` is an exclusive read gate on the chat authorize
    path, so appointing a private Agent would lock every other member out of the
    very entry the console promises them — the reason appointment used to clear
    the owner. Sharing is now the explicit, audited act that opens the Agent,
    and appointment only accepts a target that is already shared.
    """
    member_id = env.svc.create_member(
        actor_user_id=env.root_user["id"], tenant_id=env.tid,
        operation="create-new", username="plain", display_name="Plain",
        temporary_password="TmpPass123!", roles=[])["user_id"]
    env.svc.bind_agent(tenant_id=env.tid, agent_id="agent-p",
                       private_owner_user_id=env.root_user["id"])

    member_ctx = RequestContext(
        user_id=member_id, username="plain", display_name="Plain",
        is_platform_admin=False, must_change_password=False,
        tenant_id=env.tid, membership=None, permissions=set(), is_tenant_admin=False)
    web.ctx.headers = []
    # The gate resolves the service itself, so the patch must stay active for
    # every call — otherwise it reads the real store, finds no binding and
    # silently returns, and the test passes without exercising anything.
    with patch("auth.service.get_identity_service", return_value=env.svc):
        # Negative control: while it is private, the member is locked out.
        with pytest.raises(web.HTTPError) as refused:
            web_channel._require_private_owner(member_ctx, "agent-p")
        assert "403" in str(refused.value)

        # Appointment alone neither shares nor unlocks it (spec: 租户默认任命不
        # 改变私有归属), so the lockout is unchanged.
        with pytest.raises(IdentityServiceError):
            env.svc.appoint_tenant_default_agent(
                tenant_id=env.tid, agent_id="agent-p",
                actor_user_id=env.root_user["id"])
        with pytest.raises(web.HTTPError):
            web_channel._require_private_owner(member_ctx, "agent-p")

        # The explicit share is the act that admits members, and only then does
        # the appointment succeed.
        env.svc.make_agent_tenant_shared(
            agent_id="agent-p", actor_user_id=env.root_user["id"])
        env.svc.appoint_tenant_default_agent(
            tenant_id=env.tid, agent_id="agent-p",
            actor_user_id=env.root_user["id"])

        # Must not raise — the member can reach the newly-shared default.
        web_channel._require_private_owner(member_ctx, "agent-p")


# --- the console-facing action, over the real handler --------------------

AGENTS_ROUTE = "/api/agents"


class TenantDefaultAgentHttpTests(unittest.TestCase):
    """``POST /api/agents`` with ``action=set_default``, plus delete cleanup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.data_root = os.path.join(self.tmp, "data")
        self.instance = os.path.join(self.tmp, "instance")
        os.makedirs(self.data_root)
        os.makedirs(os.path.join(self.instance, "agents", "beta"))
        with open(os.path.join(self.instance, "agents", "beta", "AGENT.md"),
                  "w", encoding="utf-8") as handle:
            handle.write("# Beta persona")

        settings = {
            "agent_workspace": self.instance,
            "default_agent_id": "alpha",
            "agents": [
                {"id": "alpha", "name": "Alpha", "workspace": self.instance,
                 "enabled": True},
                {"id": "beta", "name": "Beta",
                 "workspace": os.path.join(self.instance, "agents", "beta"),
                 "enabled": True},
            ],
            "channel_instances": [],
        }
        with open(os.path.join(self.data_root, "config.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(settings, handle)
        set_agent_registry(AgentRegistry.from_config(team.resolve(settings)))
        self.addCleanup(set_agent_registry, None)

        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password=PLATFORM_PASSWORD,
            shared_root=self.instance, allow_weak=True)
        self.tenant_id = self.svc.list_tenants()[0]["id"]
        self.admin_id = self.svc.list_platform_users()[0]["id"]
        self.admin_token = self.svc.login("root", PLATFORM_PASSWORD).token

        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="alpha")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="beta")
        # "alpha" is the *template* default (config.json), which is undeletable;
        # the *tenant* default is "beta" so the delete path is reachable.
        self.svc.appoint_tenant_default_agent(
            tenant_id=self.tenant_id, agent_id="beta", actor_user_id=self.admin_id)

        self.svc.create_member(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            operation="create-new", username="ops", display_name="Ops",
            temporary_password="Str0ngTemp1", roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("ops", "Str0ngTemp1").token, "Str0ngTemp1", "Str0ngOpsFinal")
        self.admin_member_token = self.svc.login("ops", "Str0ngOpsFinal").token

        self.svc.create_member(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            operation="create-new", username="plain", display_name="Plain",
            temporary_password="Str0ngTemp2", roles=[])
        self.svc.change_password(
            self.svc.login("plain", "Str0ngTemp2").token, "Str0ngTemp2", "Str0ngPlain1")
        self.member_token = self.svc.login("plain", "Str0ngPlain1").token

    # --- harness ---------------------------------------------------------

    def _request(self, payload, token, method="POST"):
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": self.tenant_id,
        }
        if token:
            headers["Cookie"] = "cow_session=%s" % token
            headers["Host"] = "test"
            headers["Origin"] = "http://test"
        app = web.application((AGENTS_ROUTE, "AgentsHandler"),
                              vars(web_channel), autoreload=False)
        settings = {"identity_mode": "database", "identity_db_path": self.db,
                    "agent_workspace": self.instance}
        with patch.object(web_channel, "conf", return_value=settings), \
                patch("config.conf", return_value=settings), \
                patch("config.get_data_root", return_value=self.data_root), \
                patch.object(web_channel, "get_data_root",
                             return_value=self.data_root), \
                patch.object(web_channel, "_reload_agent_runtime",
                             return_value=None), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch.object(admin_handlers, "_get_service", lambda: self.svc), \
                patch("auth.service.get_identity_service", lambda: self.svc):
            response = app.request(AGENTS_ROUTE, method=method,
                                   data=json.dumps(payload), headers=headers)
        return response

    @staticmethod
    def _body(response):
        return json.loads(response.data.decode("utf-8"))

    @staticmethod
    def _status(response):
        return str(response.status).split()[0]

    def _set_default(self, agent_id, token=None):
        return self._request(
            {"action": "set_default", "id": agent_id},
            token=token or self.admin_token)

    def _membership_defaults(self):
        """Every member's stored preference, as one comparable snapshot."""
        rows = self.svc._store.execute(
            "SELECT user_id, default_agent_id, default_agent_revision,"
            " default_agent_origin FROM memberships WHERE tenant_id=?",
            (self.tenant_id,))
        return {row["user_id"]: (row["default_agent_id"],
                                 row["default_agent_revision"],
                                 row["default_agent_origin"]) for row in rows}

    # --- choosing a default ----------------------------------------------

    def test_set_default_changes_the_tenant_default_and_the_resolution(self):
        response = self._set_default("alpha")

        self.assertEqual(self._status(response), "200", response.data)
        body = self._body(response)
        self.assertEqual(body["status"], "success")
        # The caller gets the tenant's current default back, so the console can
        # reflect it without a second round trip.
        self.assertEqual(body["result"]["default_agent_id"], "alpha")
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "alpha")
        self.assertEqual(
            self.svc.resolved_default_agent_id(self.tenant_id), "alpha")

    def test_set_default_requires_an_administrator(self):
        response = self._set_default("alpha", token=self.member_token)

        self.assertEqual(self._status(response), "403", response.data)
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "beta")

    def test_set_default_rejects_an_agent_the_tenant_does_not_own(self):
        response = self._set_default("not-bound")

        self.assertEqual(self._status(response), "404", response.data)
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "beta")

    def test_set_default_is_idempotent(self):
        first = self._set_default("alpha")
        second = self._set_default("alpha")

        self.assertEqual(self._status(first), "200", first.data)
        self.assertEqual(self._status(second), "200", second.data)
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "alpha")

    def test_set_default_writes_the_tenant_entry_and_no_user_preference(self):
        """Task 4.7 I: the legacy action keeps its meaning for old clients.

        A client that only knows ``set_default`` must keep working, and its
        request must not be silently reinterpreted as a per-user preference
        (design D4: 不能把旧请求静默解释成用户偏好). The tenant pointer moves; every
        membership row — including the ones with no preference at all — stays
        exactly as it was, so a member's own registration is never overwritten by
        an administrative act.
        """
        before = self._membership_defaults()

        response = self._set_default("alpha")

        self.assertEqual(self._status(response), "200", response.data)
        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id), "alpha")
        self.assertEqual(self._membership_defaults(), before, (
            "a legacy tenant action must not write any member's preference"))

    def test_overlapping_tenant_appointments_leave_one_legal_audited_value(self):
        """Task 4.7 G, the **tenant** half — recorded as the code actually behaves.

        The *user* pointer has its own optimistic lock
        (``memberships.default_agent_revision``) and a lost race is a 409; that
        is pinned in ``test_user_default_agent_selection.py``. The tenant pointer
        deliberately has none, and this test does not pretend otherwise:
        ``appoint_tenant_default_agent`` takes no version argument and
        ``tenants.version`` belongs to the tenant editor's draft chain (see
        ``_appoint_tenant_default_agent``), while spec
        ``tenant-default-agent-administration`` asks only for an idempotent,
        admin-gated, bound target — no 并发 scenario. Two overlapping
        appointments therefore serialise and the later one wins.

        What the baseline *does* require, and what this pins, are the invariants
        that survive either behaviour: at least one appointment commits (the
        caller is qualified and the target is bound, so a lock is the only thing
        that could refuse it, and only after one has won), every outcome is
        either a commit or a 409, the pointer ends on exactly one legal bound
        target — never torn, never a foreign value — and every committed
        appointment left an audit event, so a lost update is reconstructible
        instead of invisible. The 4.7 evidence file records the asymmetry with
        the task text explicitly.
        """
        import threading

        before = len(_audit(self.svc, "tenant.set_default_agent"))
        outcomes = {}
        barrier = threading.Barrier(2)

        def appoint(agent_id):
            barrier.wait()
            try:
                self.svc.appoint_tenant_default_agent(
                    tenant_id=self.tenant_id, agent_id=agent_id,
                    actor_user_id=self.admin_id)
                outcomes[agent_id] = "ok"
            except Exception as exc:  # noqa: BLE001 - the recorded outcome
                outcomes[agent_id] = exc

        threads = [threading.Thread(target=appoint, args=(agent_id,))
                   for agent_id in ("alpha", "beta")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(outcomes), 2, outcomes)
        for outcome in outcomes.values():
            self.assertTrue(
                outcome == "ok" or getattr(outcome, "status", None) == 409,
                "an appointment may commit or lose a race, never fail otherwise:"
                " %r" % (outcome,))
        succeeded = sum(1 for outcome in outcomes.values() if outcome == "ok")
        self.assertGreaterEqual(succeeded, 1, outcomes)
        self.assertIn(self.svc.tenant_default_agent_id(self.tenant_id),
                      ("alpha", "beta"), "the pointer must hold one legal target")
        self.assertEqual(
            len(_audit(self.svc, "tenant.set_default_agent")) - before,
            succeeded,
            "every committed appointment must be audited, so a lost update is"
            " reconstructible")

    def test_set_default_is_audited(self):
        before = len(_audit(self.svc, "tenant.set_default_agent"))

        self._set_default("alpha")

        events = _audit(self.svc, "tenant.set_default_agent")
        self.assertEqual(len(events), before + 1, (
            "appointing a default must leave a tenant.set_default_agent event"))
        assert events, "no audit event was written"
        assert events[0]["result"] == "success"

    def test_set_default_refuses_a_private_agent_and_spares_others(self):
        """The console action cannot publish somebody's private Agent.

        Appointment used to clear ``private_owner_user_id``, which turned a
        member's private Agent into a tenant-level one that survived the
        appointment being reverted — and then showed up in every member's chat
        picker. The refusal is what keeps the picker's range honest.
        """
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="agent-mine",
                            private_owner_user_id=self.admin_id)
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="agent-other",
                            private_owner_user_id=self.admin_id)

        response = self._set_default("agent-mine")

        self.assertNotEqual(self._status(response), "200", response.data)
        body = self._body(response)
        self.assertEqual(body["code"], "private_agent_not_shareable")
        self.assertEqual(
            self.svc.get_agent_binding("agent-mine")["private_owner_user_id"],
            self.admin_id, "a refused appointment must not clear an owner")
        self.assertEqual(
            self.svc.tenant_default_agent_id(self.tenant_id), "beta")
        self.assertEqual(
            self.svc.get_agent_binding("agent-other")["private_owner_user_id"],
            self.admin_id)

    # --- deleting a default ----------------------------------------------

    def test_deleting_the_default_releases_the_binding_and_the_pointer(self):
        response = self._request({"action": "delete", "id": "beta"},
                                 token=self.admin_token)

        self.assertEqual(self._status(response), "200", response.data)
        self.assertIsNone(self.svc.get_agent_binding("beta"))
        self.assertIsNone(self.svc.tenant_default_agent_id(self.tenant_id))
        # The deleted Agent can no longer be resolved, even though the registry
        # would treat a stale binding as usable.
        self.assertEqual(
            self.svc.resolved_default_agent_id(self.tenant_id), "alpha")

