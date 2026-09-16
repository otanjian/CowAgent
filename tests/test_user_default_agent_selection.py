# encoding:utf-8
"""The per-user default Agent — task 4.4 of ``unify-console-by-data-scope``.

The console's Agent detail offers "设为默认" to *every* user (design D4), so the
action it calls has to answer four questions that the old tenant-level
``set_default`` never had to:

* **who** — the subject is the verified session's tenant and user. A request body
  may *name a target Agent*, but it can never name the person whose preference is
  written, and an administrator cannot move another member's default;
* **which candidate** — the target must be one the caller may both *manage* and
  *use*: a member's own private Agent, or (for an administrator) a tenant-shared
  one. The rule is asked once, through :mod:`auth.object_scope`, so it cannot
  disagree with the management list;
* **may it still** — the target is re-validated at submit time (bound to this
  tenant, enabled, in scope). A refusal leaves the stored preference untouched,
  because a write that fails must not be half-applied;
* **against what** — ``memberships.default_agent_revision`` is an independent
  optimistic lock (its own column, deliberately not ``memberships.version``,
  which the tenant editor owns). A stale request is refused; two concurrent
  choices cannot both win; retrying the *same* target is idempotent.

Driven through the real WSGI app for the same reason as
``test_scope_consistency_acceptance.py``: the gate, the identity service and the
storage have to agree, and a predicate that is right in isolation still leaks if
one caller asks a different question.
"""

from __future__ import annotations

import json

import pytest

from auth.service import IdentityServiceError


def _status(response) -> int:
    return int(response.status.split()[0])


def _body(response) -> dict:
    return json.loads(response.data.decode("utf-8"))


def _result(response) -> dict:
    return _body(response).get("result") or {}


class _World:
    """One tenant, three Agents and three callers.

    ``alice`` owns two private Agents (needed for the concurrency test, which
    races one choice against another), ``bob`` owns one, ``admin`` administers
    the tenant, and ``shared-agent`` is the tenant's shared Agent.
    """

    def __init__(self, harness):
        self.h = harness
        self.tenant_id = harness.tenant_id
        harness.add_agent("shared-agent", "alice-agent", "alice-second", "bob-agent")
        self.alice = harness.member("alice", ["member"])
        self.bob = harness.member("bob", ["member"])
        self.admin = harness.member("admin", ["tenant_admin"])
        self.alice_token = harness.login("alice")
        self.bob_token = harness.login("bob")
        self.admin_token = harness.login("admin")
        service = harness.service
        for agent_id, user_id in (("alice-agent", self.alice),
                                  ("alice-second", self.alice),
                                  ("bob-agent", self.bob)):
            service.bind_agent(tenant_id=self.tenant_id, agent_id=agent_id,
                               private_owner_user_id=user_id)
        other = harness.stack.other_tenant(code="globex", username="glenda")
        self.other_tenant = other["tenant_id"]
        self.other_member = other["user_id"]
        self.foreign_agent = other["agent_id"]

    # -- requests ------------------------------------------------------------

    def set_user_default(self, token, agent_id, **extra):
        payload = {"action": "set_user_default", "id": agent_id}
        payload.update(extra)
        return self.h.post("/api/agents", payload, token=token)

    def set_default(self, token, agent_id):
        """The *tenant* action, kept here to prove it is a different question."""
        return self.h.post("/api/agents",
                           {"action": "set_default", "id": agent_id}, token=token)

    def choose(self, token, agent_id, *, revision=None):
        """One successful choice, or a hard assertion that it was not one."""
        response = self.set_user_default(token, agent_id, default_revision=revision)
        assert _status(response) == 200, response.data
        assert _body(response).get("status") == "success", response.data
        return _result(response)

    def grant_agent_edit(self, agent_id, *, role_code="member"):
        """Give a role an explicit ``agent:<id>`` edit grant.

        Used to prove a refusal comes from the *object scope* and not from a
        missing functional grant: with the grant in place, scope alone is left
        to answer, and it must still refuse.
        """
        row = [r for r in self.h.service.list_roles(self.tenant_id)
               if r["code"] == role_code][0]
        grants = list(row["resource_grants"])
        for action in ("read", "use", "edit"):
            entry = {"resource_kind": "agent", "resource_id": f"agent:{agent_id}",
                     "action": action}
            if entry not in grants:
                grants.append(entry)
        return self.h.service.update_role(
            actor_user_id=self.h.admin_id, tenant_id=self.tenant_id,
            role_id=row["id"], name=row["name"], permissions=row["permissions"],
            expected_version=row["version"], resource_grants=grants)

    # -- stored state --------------------------------------------------------

    def stored(self, user_id, tenant_id=None):
        """The stored preference, read straight from the membership row."""
        rows = self.h.service._store.execute(
            "SELECT default_agent_id, default_agent_revision, default_agent_origin"
            " FROM memberships WHERE tenant_id=? AND user_id=?",
            (tenant_id or self.tenant_id, user_id))
        return dict(rows[0]) if rows else None

    def stored_agent(self, user_id):
        row = self.stored(user_id)
        return row["default_agent_id"] if row else None

    def revision(self, user_id):
        row = self.stored(user_id)
        return row["default_agent_revision"] if row else None

    def audit(self, action):
        return [dict(r) for r in self.h.service._store.execute(
            "SELECT * FROM audit_events WHERE action=?", (action,))]

    def disable(self, agent_id):
        """Report ``agent_id`` as stopped, for the duration of a ``with`` block.

        ``IdentityService._agent_is_usable`` is what the write path consults, and
        it is a one-line read of the registry profile's ``enabled`` flag — which
        the default-*resolution* tests already cover. What this file is
        accountable for is that the write path re-validates *usable* at submit
        time and refuses with a business code without half-applying, so the
        predicate is stubbed rather than the whole path being rebuilt around a
        process-global registry that the request scope rebuilds underneath us.
        """
        from unittest.mock import patch

        from auth.service import IdentityService

        return patch.object(IdentityService, "_agent_is_usable",
                            staticmethod(lambda target: target != agent_id))


@pytest.fixture
def world(web_app):
    return _World(web_app("user-default"))


# --- the happy path ---------------------------------------------------------

def test_a_member_sets_their_own_private_agent_as_default(world):
    """The ordinary case: 设为默认 on an Agent the member owns."""
    result = world.choose(world.alice_token, "alice-agent", revision=1)
    assert result["default_agent_id"] == "alice-agent"
    assert world.stored_agent(world.alice) == "alice-agent"


def test_the_preference_is_recorded_as_the_members_own_choice(world):
    """Origin and revision are what let provisioning and the UI behave later.

    ``origin='user'`` is what tells system provisioning "a human chose this, do
    not compete" (task 4.6), and the revision is what the console round-trips so
    a stale form cannot overwrite a newer choice.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    row = world.stored(world.alice)
    assert row["default_agent_origin"] == "user"
    assert row["default_agent_revision"] == 2, "the revision must advance"
    assert world.revision(world.alice) == 2


def test_the_write_carries_its_own_audit_event(world):
    """The preference and its audit event commit together, as one act."""
    assert not world.audit("member.set_default_agent")
    world.choose(world.alice_token, "alice-agent", revision=1)
    events = [e for e in world.audit("member.set_default_agent")
              if e["actor_user_id"] == world.alice]
    assert events, "no audit event was written for the member's own choice"
    assert "alice-agent" in json.dumps(events[0]["redacted_changes"])
    assert events[0]["result"] == "success"


def test_the_tenant_administrator_may_choose_even_though_they_own_nothing(world):
    """An administrator's candidate set is the shared Agents (design D4).

    Without this the feature would be member-only: the tenant's administrator
    usually owns no private Agent at all.
    """
    result = world.choose(world.admin_token, "shared-agent", revision=1)
    assert result["default_agent_id"] == "shared-agent"
    assert world.stored_agent(world.admin) == "shared-agent"


# --- the subject is fixed ---------------------------------------------------

def test_a_body_cannot_name_the_subject(world):
    """``user_id``/``tenant_id`` in the body are not inputs.

    The one write the action performs is the caller's own preference, so a body
    that names somebody else must not move that somebody's default — this is the
    ``伪造归属`` invariant from the task's acceptance list, applied to the
    preference pointer instead of to the Agent's owner.
    """
    response = world.set_user_default(
        world.admin_token, "alice-agent",
        default_revision=1, user_id=world.bob, tenant_id=world.other_tenant)
    # ``alice-agent`` is private to alice, so the *admin* is refused: the body
    # cannot launder a private object into scope by naming its owner either.
    assert _status(response) == 403, response.data
    assert world.stored_agent(world.bob) is None
    assert world.stored(world.admin)["default_agent_id"] in (None, "")


def test_an_administrator_cannot_move_another_members_default(world):
    """Naming a *shared* Agent must not let an admin write into bob's row."""
    response = world.set_user_default(world.admin_token, "shared-agent",
                                      default_revision=1, user_id=world.bob)
    assert _status(response) == 200, response.data
    assert world.stored_agent(world.bob) is None, "bob's preference was written"
    assert world.stored_agent(world.admin) == "shared-agent"


def test_the_preference_is_per_user(world):
    """Two members of one tenant hold independent defaults."""
    world.choose(world.alice_token, "alice-agent", revision=1)
    world.choose(world.bob_token, "bob-agent", revision=1)
    assert world.stored_agent(world.alice) == "alice-agent"
    assert world.stored_agent(world.bob) == "bob-agent"


def test_two_tenants_resolve_their_own_defaults_and_preferences(world):
    """Task 4.7 E: two tenants, two members, two independent answers.

    The literal scenario "the same Agent bound to two tenants" is not
    representable here — ``agent_bindings.agent_id`` is the primary key, and a
    second bind is refused (asserted at the end) — so what this pins is the
    property that *can* hold and is the point of the design: resolving a default
    is a per-(tenant, user) question. Tenant A's tenant entry and A's member
    preference must both be invisible to tenant B, and a member of A must not be
    able to write B's preference at all.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    other = world.other_tenant

    # Tenant B has its own private object and its own member preference...
    world.h.service.bind_agent(
        tenant_id=other, agent_id="glenda-agent",
        private_owner_user_id=world.other_member, origin="user_created")
    world.h.service.set_user_default_agent(
        tenant_id=other, user_id=world.other_member, agent_id="glenda-agent",
        actor_user_id=world.other_member)
    # ...while tenant A appoints the entry every A member shares.
    world.h.service.appoint_tenant_default_agent(
        tenant_id=world.tenant_id, agent_id="shared-agent",
        actor_user_id=world.admin)

    assert world.h.service.resolve_default_agent(
        world.tenant_id, world.alice) == {"agent_id": "alice-agent",
                                          "source": "user"}
    assert world.h.service.resolve_default_agent(
        other, world.other_member) == {"agent_id": "glenda-agent",
                                       "source": "user"}
    assert world.h.service.tenant_default_agent_id(other) is None, (
        "A's appointment must not become B's tenant default")
    assert world.stored(world.other_member, other)["default_agent_id"] \
        == "glenda-agent"
    assert world.stored(world.alice)["default_agent_id"] == "alice-agent"

    # A member of A cannot influence B: the subject is (tenant, user), and alice
    # is not a member of B, so there is no preference of hers to write there.
    with pytest.raises(IdentityServiceError) as refused:
        world.h.service.set_user_default_agent(
            tenant_id=other, user_id=world.alice, agent_id=world.foreign_agent,
            actor_user_id=world.alice)
    assert refused.value.code == "not_found"
    assert world.stored(world.other_member, other)["default_agent_id"] \
        == "glenda-agent"

    # And the "same Agent in two tenants" shape cannot even be created, which is
    # why it cannot be default for two tenants either.
    with pytest.raises(IdentityServiceError) as conflict:
        world.h.service.bind_agent(tenant_id=other, agent_id="alice-agent")
    assert conflict.value.code == "conflict"


# --- candidate qualification ------------------------------------------------

def test_the_tenant_shared_agent_is_not_a_members_candidate(world):
    """管理范围 is ``tenant=T AND owner=U``, so a shared Agent is out of it.

    The member is given an explicit ``agent:shared-agent`` **edit** grant first,
    so the refusal cannot be explained by a missing functional grant: with the
    resource grant in hand, the object scope is the only thing left to say no.
    """
    world.grant_agent_edit("shared-agent")
    response = world.set_user_default(world.alice_token, "shared-agent",
                                      default_revision=1)
    assert _status(response) == 403, response.data
    assert _body(response).get("code") == "forbidden"
    assert world.stored(world.alice)["default_agent_id"] in (None, "")


def test_another_members_private_agent_is_refused_for_member_and_admin(world):
    """Ownership precedes the administrator exception, for this write too.

    Neither bob nor the tenant's administrator may point alice's preference (or
    their own) at alice's private Agent, and nothing about alice's Agent leaks
    into the refusal beyond the refusal itself.
    """
    for token in (world.bob_token, world.admin_token):
        response = world.set_user_default(token, "alice-agent", default_revision=1)
        assert _status(response) == 403, response.data
        assert _body(response).get("code") == "forbidden"
    assert world.stored_agent(world.bob) is None


def test_an_agent_of_another_tenant_is_unknown_here(world):
    """A foreign Agent is not a refusal-with-a-reason; it is out of the world."""
    response = world.set_user_default(world.alice_token, world.foreign_agent,
                                      default_revision=1)
    assert _status(response) == 404, response.data
    assert _body(response).get("code") == "not_found"


def test_a_disabled_agent_is_not_a_candidate(world):
    """停用 is a deliberate act, so it must not become anyone's default.

    The refusal is a business code, not a generic failure, and the stored value
    is untouched: a refused write never half-applies.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    with world.disable("alice-second"):
        response = world.set_user_default(world.alice_token, "alice-second",
                                          default_revision=2)
    assert _status(response) == 409, response.data
    assert _body(response).get("code") == "agent_not_usable"
    assert world.stored_agent(world.alice) == "alice-agent"
    assert world.revision(world.alice) == 2


def test_the_tenant_default_action_stays_a_separate_question(world):
    """``set_default`` is the tenant's, not a member's own preference.

    The two actions must not be merged into one endpoint with a mode flag: the
    tenant default is a shared entry point and a member has no say in it, while
    a user default is theirs alone. Setting one must leave the other alone.
    """
    member_attempt = world.set_default(world.alice_token, "alice-agent")
    assert _status(member_attempt) == 403, member_attempt.data
    world.choose(world.alice_token, "alice-agent", revision=1)
    assert world.h.service.tenant_default_agent_id(world.tenant_id) is None


# --- the independent optimistic lock ---------------------------------------

def test_a_stale_revision_is_refused(world):
    """A form holding an old revision must not overwrite a newer choice."""
    world.choose(world.alice_token, "alice-agent", revision=1)
    stale = world.set_user_default(world.alice_token, "alice-second",
                                   default_revision=1)
    assert _status(stale) == 409, stale.data
    assert _body(stale).get("code") == "version_conflict"
    assert world.stored_agent(world.alice) == "alice-agent", "the newer value lost"


def test_retrying_the_same_target_is_idempotent(world):
    """Same target, current revision: a retry is not a second change.

    The console retries on a flaky network, so this must not be an error and
    must not bump the revision — bumping it would invalidate the client's own
    freshly-read value for no reason. The authorization is re-checked, which is
    what keeps idempotence from becoming a bypass.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    before = len(world.audit("member.set_default_agent"))
    again = world.choose(world.alice_token, "alice-agent", revision=2)
    assert again["default_agent_id"] == "alice-agent"
    assert again.get("changed") is False
    assert world.revision(world.alice) == 2, "an idempotent retry must not bump"
    assert len(world.audit("member.set_default_agent")) == before


def test_a_lost_race_has_exactly_one_winner(world):
    """Two concurrent choices to *different* targets cannot both succeed.

    Both requests were composed from the same read (revision 1), which is the
    real shape of the race: two tabs, or a double click. The lock is what makes
    the loser re-read instead of silently overwriting the winner.
    """
    first = world.set_user_default(world.alice_token, "alice-agent",
                                   default_revision=1)
    second = world.set_user_default(world.alice_token, "alice-second",
                                    default_revision=1)
    statuses = sorted([_status(first), _status(second)])
    assert statuses == [200, 409], (first.data, second.data)
    winner = ("alice-agent" if _status(first) == 200 else "alice-second")
    assert world.stored_agent(world.alice) == winner
    assert world.revision(world.alice) == 2


def test_two_overlapping_choices_cannot_both_win(world):
    """Task 4.7 G: the optimistic lock, with two writers actually overlapping.

    ``test_a_lost_race_has_exactly_one_winner`` replays the two requests one
    after the other, so the *pre-check* (the second read already sees revision 2)
    is what answers. Here both calls start from the same read revision on real
    threads, so the writes genuinely overlap and the decision is
    ``BEGIN IMMEDIATE`` plus the guarded UPDATE: exactly one commits, the other
    must come back ``version_conflict`` rather than silently overwriting.
    """
    import threading

    outcomes = {}
    barrier = threading.Barrier(2)

    def attempt(agent_id):
        barrier.wait()
        try:
            world.h.service.set_user_default_agent(
                tenant_id=world.tenant_id, user_id=world.alice,
                agent_id=agent_id, expected_revision=1,
                actor_user_id=world.alice)
            outcomes[agent_id] = "ok"
        except IdentityServiceError as exc:
            outcomes[agent_id] = exc.code

    threads = [threading.Thread(target=attempt, args=(agent_id,))
               for agent_id in ("alice-agent", "alice-second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes.values()) == ["ok", "version_conflict"], outcomes
    winner = [a for a, outcome in outcomes.items() if outcome == "ok"][0]
    assert world.stored_agent(world.alice) == winner
    assert world.revision(world.alice) == 2, (
        "exactly one write landed, so the pointer advanced exactly once")


def test_a_choice_without_a_revision_is_a_first_write(world):
    """A client that has never read the pointer may still make the first choice.

    Provisioning leaves ``default_agent_revision`` at its column default, so an
    absent ``default_revision`` means "I hold nothing", which is only safe for a
    pointer nobody has registered yet.
    """
    result = world.choose(world.alice_token, "alice-agent")
    assert result["default_agent_revision"] == 2


def test_a_choice_without_a_revision_cannot_overwrite_a_registered_one(world):
    """Once a preference exists, "no revision" stops meaning "first write".

    Otherwise an old or careless client would be a lost-update machine: it could
    replace a newer choice without ever having read it, which is precisely what
    the lock exists to prevent.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    blind = world.set_user_default(world.alice_token, "alice-second")
    assert _status(blind) == 409, blind.data
    assert _body(blind).get("code") == "version_conflict"
    assert world.stored_agent(world.alice) == "alice-agent"


def test_provisioning_marks_its_own_origin_so_a_later_choice_can_tell(world):
    """The origin vocabulary is shared with the provisioning writer (4.6).

    ``provisioned`` and ``user`` are the two values the migration documents;
    this pins that the user action does not write ``provisioned``.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    assert world.stored(world.alice)["default_agent_origin"] == "user"
    assert world.stored(world.bob)["default_agent_origin"] is None


# --- the anchor the console reads back (task 4.6) ---------------------------

def _anchor(payload):
    return payload["default_resolution"]


def test_a_fallback_is_reported_as_a_fallback_not_as_the_tenants_choice(world):
    """The whole reason the source exists (task 4.6).

    This tenant never appointed a default, so bob's anchor is the smallest shared
    Agent — nobody's decision. Reporting that as ``tenant`` would lend the
    tenant's authority to a guess, and the console could not tell an operator
    that no one ever chose this.
    """
    payload = _body(world.h.get("/api/agents", token=world.bob_token))

    assert _anchor(payload) == {"agent_id": "shared-agent", "source": "shared"}


def test_an_appointed_default_is_reported_as_the_tenants_choice(world):
    """Once somebody does appoint one, the source says so."""
    world.h.service.appoint_tenant_default_agent(
        tenant_id=world.tenant_id, agent_id="shared-agent",
        actor_user_id=world.h.admin_id)

    payload = _body(world.h.get("/api/agents", token=world.bob_token))

    assert _anchor(payload) == {"agent_id": "shared-agent", "source": "tenant"}


def test_a_members_own_choice_becomes_the_reported_source(world):
    """After choosing, the anchor is the member's own — and says so."""
    world.choose(world.alice_token, "alice-agent", revision=1)

    payload = _body(world.h.get("/api/agents", token=world.alice_token))

    assert _anchor(payload) == {"agent_id": "alice-agent", "source": "user"}


def test_the_anchor_is_always_inside_the_callers_reachable_range(world):
    """A source is only meaningful if the target is somewhere the caller can go.

    Note this is the **use** range, not the management list: an ordinary member
    cannot *manage* the tenant's shared Agent, yet that is exactly where their
    new conversations anchor when they have no preference of their own. Asserting
    the invariant against the management list would fail on correct behaviour,
    so it is asserted where it belongs.
    """
    world.choose(world.alice_token, "alice-agent", revision=1)
    bindings = {b["agent_id"]: b for b in
                world.h.service.agents_for_tenant(world.tenant_id)}

    for token, user_id in ((world.bob_token, world.bob),
                           (world.alice_token, world.alice),
                           (world.admin_token, world.admin)):
        payload = _body(world.h.get("/api/agents", token=token))
        anchor = _anchor(payload)
        assert (anchor["agent_id"] is None) == (anchor["source"] is None)
        assert anchor["agent_id"] in bindings, (
            "the anchor must be an Agent this tenant holds")
        owner = bindings[anchor["agent_id"]].get("private_owner_user_id")
        if owner is not None and anchor["source"] == "user":
            assert owner == user_id, (
                "an anchor reported as a member's own choice must be their own")


def test_the_anchor_is_reported_per_caller_not_per_tenant(world):
    """Two members, one tenant, two different answers — the point of task 4.4."""
    world.choose(world.alice_token, "alice-agent", revision=1)

    alice = _body(world.h.get("/api/agents", token=world.alice_token))
    bob = _body(world.h.get("/api/agents", token=world.bob_token))

    assert _anchor(alice)["agent_id"] == "alice-agent"
    assert _anchor(bob)["agent_id"] != "alice-agent", (
        "bob must never be anchored to alice's private workspace")
