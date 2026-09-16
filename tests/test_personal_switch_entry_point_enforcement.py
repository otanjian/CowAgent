# encoding:utf-8
"""An explicit "off" switch is not bypassed by the newer shared entry point
(change ``unify-console-by-data-scope``, task 7.3, defects R1–R2).

The measured defect was **one surface giving two answers**: the legacy member
wrapper read the deployment switch and refused, while the shared entry point the
console actually calls now — ``/api/tenant/channels`` for a connection,
``bind_private_agent_with_quota`` for a private Agent — did not. The same page
therefore advertised, or accepted, what its own switch had withdrawn.

This file pins the *one answer*:

* the shared channel door refuses a **create** and a **re-enable** when the
  member's onboarding switch (or the console-wide switch behind the same page) is
  off, and leaves no row behind;
* the **closing** direction stays reachable — ``off`` refuses opening, never
  disabling — because a deployment must not strand a member with a connection
  they can no longer switch off;
* private-Agent creation through the shared bind is refused too, API clients
  included: that is the intended consequence of "off means off, not merely that a
  page is hidden";
* the projection stops advertising the ``create`` its write path now refuses, so
  the page and the interface agree in both switch states.

Every assertion is on an **observable effect** — the row that does or does not
exist, the column's value, the refusal code — because a status code alone would
also be satisfied by refusing a caller the switch never named. Each rule carries
its **control**: the same call with the switch on has to land, so "refuse
everything" cannot pass.

The channel writes are driven through the real ``build_web_app()`` application
(``web_app`` fixture), so the route policy, the handler, the service and the
store are all in the path.
"""

from __future__ import annotations

import json

import pytest

import config
from auth.service import IdentityServiceError
from tests._helpers import IdentityStack

MEMBER_PASSWORD = IdentityStack.MEMBER_PASSWORD

#: Personal parameters and channel credentials are encrypted at rest, so the
#: deployment's master key has to be present for a create to get as far as the
#: gate under test.
MASTER_KEY = "00112233445566778899aabbccddeeff"

#: The member's own private Agent — the only target their connection may route to.
MEMBER_AGENT = "alice-private-agent"


def _status(response) -> int:
    return int(response.status.split()[0])


def _body(response) -> dict:
    return json.loads(response.data.decode("utf-8"))


class _RosterRecorder:
    """The roster half of ``PrivateAgentService``.

    Records what a create wrote and what it had to delete again, so "the refusal
    happened before anything was created" is an observable fact rather than an
    inference from a status code.
    """

    def __init__(self):
        self.agents = []
        self.deleted = []

    def snapshot(self):
        return {"agents": list(self.agents)}

    def create_agent(self, agent_id, name, workspace=None, **fields):
        self.agents.append({"id": agent_id, "name": name, "workspace": workspace})
        return {"id": agent_id}

    def clone_agent(self, source_agent_id, agent_id, name=None, workspace=None,
                    revision=None, knowledge_mode=None):
        return self.create_agent(agent_id, name or agent_id, workspace=workspace)

    def update_agent(self, agent_id, **fields):
        return {"id": agent_id}

    def delete_agent(self, agent_id, revision=None):
        self.deleted.append(agent_id)
        self.agents = [row for row in self.agents if row["id"] != agent_id]
        return {"id": agent_id}


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


class _World:
    """One tenant, a plain member, and their own private Agent.

    The switches are withdrawn by mutating the very configuration mapping the
    application reads per request (``config.conf()`` is the harness's own), so no
    restart and no restart-only caching can make the test disagree with the code
    path a running deployment would take.
    """

    def __init__(self, harness):
        self.h = harness
        self.tenant_id = harness.tenant_id
        harness.add_agent("shared-agent")
        self.member = harness.member("alice", ["member"])
        harness.private_agent(self.member, MEMBER_AGENT)
        self.token = harness.login("alice")

    def switches(self, **over):
        config.conf().update(over)

    # -- acting --------------------------------------------------------------

    def create(self, *, name="Member Bot", app_id="cli_switch_entry"):
        return self.h.post("/api/tenant/channels", {
            "channel_type": "feishu",
            "display_name": name,
            "agent_id": MEMBER_AGENT,
            "credentials": {"feishu_app_id": app_id,
                            "feishu_app_secret": "member-app-secret",
                            "feishu_bot_name": "Bot"},
            "recent_password": MEMBER_PASSWORD,
        }, token=self.token)

    def set_active(self, instance_id, active, version):
        return self.h.post("/api/tenant/channels/%s/active" % instance_id, {
            "active": active, "expected_version": version,
            "recent_password": MEMBER_PASSWORD,
        }, token=self.token)

    def edit(self, instance_id, version, *, name=None, agent_id=None,
             app_secret=None, token=None):
        """The shared *edit* door — ``POST /api/tenant/channels/<id>``.

        Only the fields the caller supplies are sent, so "the rename did not
        happen" is read off the stored row rather than off a request that
        happened to blank it. ``app_secret`` rotates the bundle (a partial
        rotation is merged over what is stored, which is the console's shape).
        """
        body = {"expected_version": version,
                "recent_password": MEMBER_PASSWORD}
        if name is not None:
            body["display_name"] = name
        if agent_id is not None:
            body["agent_id"] = agent_id
        if app_secret is not None:
            body["credentials"] = {"feishu_app_id": "cli_switch_entry",
                                   "feishu_app_secret": app_secret,
                                   "feishu_bot_name": "Bot"}
        return self.h.post("/api/tenant/channels/%s" % instance_id, body,
                           token=token or self.token)

    # -- observing -----------------------------------------------------------

    def rows(self):
        return [dict(row) for row in self.h.service._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE tenant_id=?"
            " AND scope='user' AND owner_user_id=? ORDER BY display_name",
            (self.tenant_id, self.member))]

    def tenant_rows(self):
        return [dict(row) for row in self.h.service._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE tenant_id=?"
            " AND scope='tenant' ORDER BY display_name", (self.tenant_id,))]

    def row(self, instance_id):
        matches = [row for row in self.rows() if row["id"] == instance_id]
        assert matches, "the instance row disappeared"
        return matches[0]

    def credential_versions(self, instance_id):
        """How many versions the instance's one credential has.

        Read from storage, so "the rotation did not happen" is a fact about the
        bundle rather than about the response.
        """
        rows = self.h.service._store.execute(
            "SELECT COUNT(*) c FROM credential_versions v"
            " JOIN credentials c ON c.id = v.credential_id"
            " WHERE c.name=?", ("channel:%s" % instance_id,))
        return int(rows[0]["c"])

    def colleague_instance(self):
        """A *second* member's own personal connection, for the range cases.

        Created through the same shared door with the switches on, so its row
        is indistinguishable from the member's except for its owner.
        """
        token = self.h.login(self.colleague())
        response = self.h.post("/api/tenant/channels", {
            "channel_type": "feishu",
            "display_name": "Colleague Bot",
            "agent_id": "colleague-private-agent",
            "credentials": {"feishu_app_id": "cli_switch_colleague",
                            "feishu_app_secret": "colleague-secret",
                            "feishu_bot_name": "Bot"},
            "recent_password": MEMBER_PASSWORD,
        }, token=token)
        assert _status(response) == 200, response.data
        return _body(response)["instance"]

    def colleague(self):
        """``bob``: the username of a plain member of the same tenant with a
        private Agent, created once per test."""
        if not getattr(self, "_colleague", None):
            bob = self.h.member("bob", ["member"])
            self.h.private_agent(bob, "colleague-private-agent")
            self._colleague = "bob"
        return self._colleague

    def create_landed(self, **kwargs):
        """A create with the switch on — the control the refusals are read against."""
        response = self.create(**kwargs)
        assert _status(response) == 200, response.data
        rows = self.rows()
        assert len(rows) == 1, rows
        return rows[0]

    def channels_page(self):
        pages = self.h.service.context_for_tenant(
            self.token, self.tenant_id)["console_pages"]
        return pages["admin.channels"]

    def admin_token(self):
        if not getattr(self, "_admin_created", False):
            self.h.member("tadmin", ["tenant_admin"])
            self._admin_created = True
        return self.h.login("tadmin")

    def private_bindings(self):
        return sorted(row["agent_id"] for row in
                      self.h.service.agents_for_tenant(self.tenant_id)
                      if row.get("private_owner_user_id") == self.member)

    def private_agent_service(self):
        from agent.private_agent import PrivateAgentService

        roster = _RosterRecorder()
        return PrivateAgentService(self.h.service, admin_service=roster), roster


@pytest.fixture
def world(web_app):
    return _World(web_app("switch-entry-point"))


# --- R1: the shared channel door reads the switch ---------------------------

def test_the_shared_create_is_refused_when_onboarding_is_withdrawn(world):
    world.switches(personal_channel_onboarding=False)
    response = world.create()
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    assert world.rows() == [], "a refused create must not leave an instance row"


def test_the_shared_create_lands_when_onboarding_is_on(world):
    """The control: the refusal above is the switch, not a blanket denial."""
    assert world.rows() == []
    row = world.create_landed()
    assert row["active"] == 1
    assert row["display_name"] == "Member Bot"
    assert row["owner_user_id"] == world.member
    assert row["scope"] == "user"


def test_the_legacy_wrapper_and_the_shared_door_give_one_answer(world):
    """One surface, one answer — this is the defect's own shape.

    The wrapper worked; only the shared door was open. Both are asserted in the
    same withdrawn state, on the same refusal code, so a fix that closed the
    legacy door instead of the shared one could not pass.
    """
    world.switches(personal_channel_onboarding=False)
    with pytest.raises(IdentityServiceError) as caught:
        world.h.service.create_personal_channel_instance(
            actor_user_id=world.member, tenant_id=world.tenant_id,
            channel_type="feishu", display_name="Legacy Bot",
            agent_id=MEMBER_AGENT,
            credentials={"feishu_app_id": "cli_legacy_switch",
                         "feishu_app_secret": "s", "feishu_bot_name": "B"},
            recent_password=MEMBER_PASSWORD)
    assert (caught.value.code, caught.value.status) == ("capability_disabled", 403)

    shared = world.create()
    assert _status(shared) == 403, shared.data
    assert _body(shared)["code"] == caught.value.code
    assert world.rows() == []


def test_the_shared_re_enable_is_refused_when_onboarding_is_withdrawn(world):
    """Re-enabling is an *opening* write: it decides what the deployment serves."""
    created = world.create_landed()
    assert _status(world.set_active(created["id"], False,
                                    created["version"])) == 200
    disabled = world.row(created["id"])
    assert disabled["active"] == 0

    world.switches(personal_channel_onboarding=False)
    response = world.set_active(created["id"], True, disabled["version"])
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    after = world.row(created["id"])
    assert after["active"] == 0, "a refused enable must not open the connection"
    assert after["version"] == disabled["version"], "and must not bump the row"


def test_the_shared_re_enable_lands_when_onboarding_is_on(world):
    """Control: the same call opens the same row with the switch on."""
    created = world.create_landed()
    assert _status(world.set_active(created["id"], False,
                                    created["version"])) == 200
    disabled = world.row(created["id"])
    enabled = world.set_active(created["id"], True, disabled["version"])
    assert _status(enabled) == 200, enabled.data
    assert world.row(created["id"])["active"] == 1


def test_disabling_stays_reachable_when_onboarding_is_withdrawn(world):
    """The other direction of the asymmetry, deliberately.

    ``off`` refuses opening, never closing: a deployment that withdrew the
    capability must not strand a member with a live connection they can no longer
    turn off. Same rule the legacy wrapper states in its own docstring, asserted
    here against the shared door.
    """
    created = world.create_landed()
    world.switches(personal_channel_onboarding=False)
    response = world.set_active(created["id"], False, created["version"])
    assert _status(response) == 200, response.data
    assert world.row(created["id"])["active"] == 0


# --- R2: the console-wide switch is enforced on the shared write path -------

def test_private_agent_creation_is_refused_when_the_console_is_withdrawn(world):
    """``bind_private_agent_with_quota`` is the shared landing point of every
    creation, the console's own included.

    Refusing here is what makes "off" mean the capability is off rather than the
    page being hidden — API clients included, which is intended.
    """
    world.switches(member_personal_console=False)
    with pytest.raises(IdentityServiceError) as caught:
        world.h.service.bind_private_agent_with_quota(
            tenant_id=world.tenant_id, agent_id="api-private-agent",
            user_id=world.member, origin="user_created",
            actor_user_id=world.member)
    assert (caught.value.code, caught.value.status) == ("capability_disabled", 403)
    assert world.h.service.get_agent_binding("api-private-agent") is None, \
        "a refused bind must not leave a binding row"


def test_the_private_agent_bind_lands_when_the_console_is_on(world):
    """Control: the same bind with the switch on produces the owned object."""
    binding = world.h.service.bind_private_agent_with_quota(
        tenant_id=world.tenant_id, agent_id="api-private-agent",
        user_id=world.member, origin="user_created", actor_user_id=world.member)
    assert binding["private_owner_user_id"] == world.member
    assert binding["origin"] == "user_created"
    assert "api-private-agent" in world.private_bindings()


def test_the_console_create_path_refuses_before_it_clones_anything(world):
    """The console's own create happens *through* the bind above.

    Asserted on the roster recorder rather than the refusal code alone: a create
    that cloned a workspace and then leaned on ``_compensate`` to delete it would
    also answer 403, and that is not what "the write is refused" should mean.
    """
    service, roster = world.private_agent_service()
    world.switches(member_personal_console=False)
    with pytest.raises(IdentityServiceError) as caught:
        service.create_private_agent(user_id=world.member,
                                     tenant_id=world.tenant_id, name="mine")
    assert (caught.value.code, caught.value.status) == ("capability_disabled", 403)
    assert (roster.agents, roster.deleted) == ([], []), (
        "the refusal has to precede the clone, not be compensated after it")
    assert world.private_bindings() == [MEMBER_AGENT]


def test_the_console_create_path_lands_when_the_console_is_on(world):
    """Control: the same call, switch on, produces the object and its binding."""
    service, roster = world.private_agent_service()
    created = service.create_private_agent(user_id=world.member,
                                           tenant_id=world.tenant_id, name="mine")
    assert [row["id"] for row in roster.agents] == [created["agent_id"]]
    binding = world.h.service.get_agent_binding(created["agent_id"])
    assert binding["private_owner_user_id"] == world.member
    assert binding["origin"] == "user_created"


def test_the_members_own_channel_write_is_refused_by_the_console_switch(world):
    """The console-wide switch governs the member's whole personal surface.

    Its page reports the switch *and* the slice switch, so the write has to read
    both or the page and the interface disagree again — in the other direction.
    """
    world.switches(member_personal_console=False)
    response = world.create()
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    assert world.rows() == []


def test_the_members_own_re_enable_is_refused_by_the_console_switch(world):
    created = world.create_landed()
    assert _status(world.set_active(created["id"], False,
                                    created["version"])) == 200
    disabled = world.row(created["id"])

    world.switches(member_personal_console=False)
    response = world.set_active(created["id"], True, disabled["version"])
    assert _status(response) == 403, response.data
    assert world.row(created["id"])["active"] == 0


def test_the_console_switch_never_reaches_a_tenant_connection(world):
    """Only the member's own range is withdrawn.

    A public connection is not the member's console capability, so the
    administrator's surface must not narrow with the withdrawal — and the write
    has to prove it, not merely the projection.
    """
    world.switches(member_personal_console=False)
    token = world.admin_token()
    response = world.h.post("/api/tenant/channels", {
        "channel_type": "feishu", "display_name": "Tenant Bot",
        "agent_id": "shared-agent",
        "credentials": {"feishu_app_id": "cli_public_switch",
                        "feishu_app_secret": "public-app-secret",
                        "feishu_bot_name": "Bot"},
        "recent_password": MEMBER_PASSWORD,
    }, token=token)
    assert _status(response) == 200, response.data
    rows = world.tenant_rows()
    assert [row["scope"] for row in rows] == ["tenant"]
    assert rows[0]["display_name"] == "Tenant Bot"


# --- R2: the projection agrees with the write path --------------------------

def test_the_members_channel_page_does_not_advertise_a_refused_create(world):
    world.switches(member_personal_console=False)
    page = world.channels_page()
    assert page["switches"]["member_personal_console"] is False
    assert page["actions"]["create"] is False, (
        "the page must not offer the create its own write path now refuses")


def test_a_withdrawn_onboarding_switch_withdraws_the_advertised_create(world):
    world.switches(personal_channel_onboarding=False)
    page = world.channels_page()
    assert page["switches"]["personal_channel_onboarding"] is False
    assert page["actions"]["create"] is False


def test_the_page_offers_create_again_with_the_switches_on(world):
    """Control for both projection tests: the verbs come back, and maintenance —
    which is a *closing*/*editing* verb — was never withdrawn."""
    page = world.channels_page()
    assert page["switches"] == {"member_personal_console": True,
                                "personal_channel_onboarding": True}
    assert page["actions"]["create"] is True
    assert page["actions"]["update"] is True


def test_the_withdrawal_does_not_narrow_the_administrators_page(world):
    """The switches are scoped to the member's own surface, both ways.

    The administrator's page reports the tenant scope and carries no switch
    block; gating its ``create`` on the member's switch would take away the
    public connection they may still open — which the test above proves on the
    write path.
    """
    world.switches(member_personal_console=False,
                   personal_channel_onboarding=False)
    pages = world.h.service.context_for_tenant(
        world.admin_token(), world.tenant_id)["console_pages"]
    page = pages["admin.channels"]
    assert page["scope"] == "tenant"
    assert "switches" not in page
    assert page["actions"]["create"] is True


# --- 7.9: the shared *edit* door reads the same switches --------------------
#
# The last door of the same defect class. ``update_tenant_channel_instance`` is
# what the console actually calls to rename, re-target or re-key a personal
# instance, and it did not consult the switches — while the legacy wrapper
# ``update_personal_channel_instance`` refused the whole operation and the spec
# names 编辑个人渠道实例 as an opening-type write (spec
# ``console-navigation-availability``: 开启类写入 … SHALL 在对应开关关闭时以
# ``capability_disabled`` 拒绝且不产生部分写入).
#
# The actor split is the part that could be got wrong: the same function serves
# the member on their own console (``allow_owner=True``) and the operator, whose
# edits are the governance surface task 6.1 split out (``allow_owner=False``).
# Only the former is the member's surface, so only the former is gated — the
# tests below assert both directions, because a suite where everything is
# refused must not be able to pass.


def test_the_members_own_edit_is_refused_when_onboarding_is_withdrawn(world):
    """A rename through the shared door, switch off: refused, and nothing moved.

    The refusal is asserted on the stored row as well as the code, so a write
    that renamed the row and then answered 403 would not pass.
    """
    created = world.create_landed()
    world.switches(personal_channel_onboarding=False)
    response = world.edit(created["id"], created["version"], name="Renamed")
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    row = world.row(created["id"])
    assert row["display_name"] == "Member Bot", "a refused edit must not rename"
    assert row["version"] == created["version"], "and must not bump the version"
    assert row["agent_id"] == MEMBER_AGENT


def test_the_members_own_edit_lands_when_onboarding_is_on(world):
    """Control: the same request with the switch on does rename the row."""
    created = world.create_landed()
    response = world.edit(created["id"], created["version"], name="Renamed")
    assert _status(response) == 200, response.data
    row = world.row(created["id"])
    assert row["display_name"] == "Renamed"
    assert row["version"] == created["version"] + 1


def test_the_members_own_edit_is_refused_by_the_console_switch(world):
    """The console-wide switch withdraws the whole member surface, not a slice.

    The member's page reports both switches and its create/enable guard already
    reads both; the edit beside them has to give the same answer or the same
    page would accept an opening write its own switch had withdrawn.
    """
    created = world.create_landed()
    world.switches(member_personal_console=False)
    response = world.edit(created["id"], created["version"], name="Renamed")
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    row = world.row(created["id"])
    assert row["display_name"] == "Member Bot"
    assert row["version"] == created["version"]


def test_the_members_own_retarget_is_refused_when_onboarding_is_withdrawn(world):
    """换绑 is the same opening write: it decides where conversations route."""
    world.h.private_agent(world.member, "alice-second-agent")
    created = world.create_landed()
    world.switches(personal_channel_onboarding=False)
    response = world.edit(created["id"], created["version"],
                          agent_id="alice-second-agent")
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    assert world.row(created["id"])["agent_id"] == MEMBER_AGENT, \
        "a refused re-target must not move the route"


def test_the_members_own_retarget_lands_when_onboarding_is_on(world):
    """Control for the re-target: switch on, the route moves."""
    world.h.private_agent(world.member, "alice-second-agent")
    created = world.create_landed()
    response = world.edit(created["id"], created["version"],
                          agent_id="alice-second-agent")
    assert _status(response) == 200, response.data
    assert world.row(created["id"])["agent_id"] == "alice-second-agent"


def test_the_members_own_rotation_is_refused_when_onboarding_is_withdrawn(world):
    """换密钥 is the opening write the member's own docstring names first.

    Asserted on the credential's version history as well: a refusal that had
    already appended a version would be the "部分写入" the spec forbids.
    """
    created = world.create_landed()
    assert world.credential_versions(created["id"]) == 1
    world.switches(personal_channel_onboarding=False)
    response = world.edit(created["id"], created["version"],
                          app_secret="rotated-member-secret")
    assert _status(response) == 403, response.data
    assert _body(response)["code"] == "capability_disabled"
    assert world.credential_versions(created["id"]) == 1, \
        "a refused rotation must not append a credential version"


def test_the_members_own_rotation_lands_when_onboarding_is_on(world):
    """Control: switch on, the rotation really does land as a new version."""
    created = world.create_landed()
    response = world.edit(created["id"], created["version"],
                          app_secret="rotated-member-secret")
    assert _status(response) == 200, response.data
    assert world.credential_versions(created["id"]) == 2


def test_the_operators_edit_still_lands_when_the_switches_are_withdrawn(world):
    """The anti-over-block control that matters most.

    ``allow_owner`` is the member's own console and only that (task 6.1): the
    operator edits the tenant's public connection through the *same* function
    with it unset. Task 6.1 split that governance surface out deliberately, and
    the deployment switch is about the member's console, not an administrator's
    duty — so with every switch off the operator still creates and renames.
    """
    world.switches(member_personal_console=False,
                   personal_channel_onboarding=False)
    token = world.admin_token()
    created = world.h.post("/api/tenant/channels", {
        "channel_type": "feishu", "display_name": "Tenant Bot",
        "agent_id": "shared-agent",
        "credentials": {"feishu_app_id": "cli_public_switch",
                        "feishu_app_secret": "public-app-secret",
                        "feishu_bot_name": "Bot"},
        "recent_password": MEMBER_PASSWORD,
    }, token=token)
    assert _status(created) == 200, created.data
    instance = _body(created)["instance"]
    renamed = world.edit(instance["id"], instance["version"],
                         name="Tenant Bot Renamed", token=token)
    assert _status(renamed) == 200, renamed.data
    assert _body(renamed)["instance"]["display_name"] == "Tenant Bot Renamed"
    rows = world.tenant_rows()
    assert [row["scope"] for row in rows] == ["tenant"]
    assert rows[0]["display_name"] == "Tenant Bot Renamed"


def test_the_owner_rule_still_answers_a_foreign_id_when_withdrawn(world):
    """The switch removes authority; it never replaces the owner check.

    ``关闭开关不撤去 owner 检查``: with the switch off, a colleague's instance is
    still refused for what it is and an unknown id is still "not found" — the
    withdrawal must not become an oracle that explains both as a capability
    fact. This pins *where* the guard sits: after the ownership proof, which is
    also why an operator's own branch is untouched by it.
    """
    colleague = world.colleague_instance()
    created = world.create_landed()
    world.switches(personal_channel_onboarding=False)

    foreign = world.edit(colleague["id"], colleague["version"], name="Hijacked")
    assert _status(foreign) == 403, foreign.data
    assert _body(foreign)["code"] == "forbidden"

    missing = world.edit("chan_absent", 1, name="Ghost")
    assert _status(missing) == 404, missing.data
    assert _body(missing)["code"] == "not_found"

    # The member's own row is the one the switch answers, and the colleague's
    # row is untouched by any of the three attempts.
    assert world.row(created["id"])["display_name"] == "Member Bot"


def test_unlinking_stays_reachable_when_the_switch_is_withdrawn(world):
    """"解绑" is not this function's business — verified, not assumed.

    The spec's guaranteed withdrawal list stays available with the switch off.
    Unlinking has its own service method and its own route
    (``/api/personal/channels/<id>`` ``action=unlink``), and it never reaches
    ``update_tenant_channel_instance``, so the guard above cannot take it away.
    Asserted on the effect: it still succeeds and does not move the instance.
    """
    created = world.create_landed()
    world.switches(member_personal_console=False,
                   personal_channel_onboarding=False)
    result = world.h.service.unlink_personal_channel_instance(
        actor_user_id=world.member, tenant_id=world.tenant_id,
        instance_id=created["id"], expected_version=created["version"])
    assert result["instance_id"] == created["id"]
    assert result["link"] is None
    row = world.row(created["id"])
    assert row["active"] == 1, "unlinking is not a disable"
    assert row["display_name"] == "Member Bot"


def test_disabling_the_members_own_instance_is_still_reachable(world):
    """The same guarantee on the verb that shares the member's surface."""
    created = world.create_landed()
    world.switches(member_personal_console=False,
                   personal_channel_onboarding=False)
    response = world.set_active(created["id"], False, created["version"])
    assert _status(response) == 200, response.data
    assert world.row(created["id"])["active"] == 0
