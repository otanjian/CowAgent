# encoding:utf-8
"""Provisioning, resolution and cleanup of the per-user default — task 4.6 of
``unify-console-by-data-scope``.

Task 4.4 gave every user a way to register their *own* default Agent. That turns
``memberships.default_agent_id`` into a field two writers want:

* **the person**, through ``set_user_default_agent`` (task 4.4), and
* **system provisioning**, which registers the personal assistant it just made.

Four properties keep that from becoming a race, and they are what this file
pins:

1. **provisioning only initialises an *empty* preference.** It never competes
   with a choice a human already made — the window that mattered is a member who
   owns no personal assistant yet, because the old ``owned_agent_id`` guard did
   not stop provisioning there;
2. **the competition is decided inside one transaction**, on
   ``default_agent_origin``, so "did somebody already choose?" and "write mine"
   cannot interleave;
3. **resolution reports both the winner and where it came from**, so the console
   can tell "this is my choice" from "this is the tenant's entry" from "this is
   only a fallback";
4. **deleting clears the reference, disabling does not.** A stopped Agent is a
   temporary state, so the preference survives it and comes back with the Agent;
   a deleted one can never come back, so the pointer *and its origin* are
   released.

Driven against the real services for the same reason as
``test_user_personal_agent_provisioning.py``: the roster (``config.json``) and
``identity.db`` cannot commit together, and the invariant spans both.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent import team
from agent.admin import AgentAdminService
from agent.personal_assistant import PersonalAssistantProvisioner
from agent.registry import AgentRegistry, set_agent_registry
from auth.service import IdentityService, IdentityServiceError

SOURCE_ID = "my-assistant-admin"
SOURCE_NAME = "智能办公助理"
ADMIN_PASSWORD = "Str0ngAdminPass"

SOURCE_AGENT_MD = (
    "# AGENT.md\n\n"
    "- **名字**: 智能办公助理\n"
    "- **角色**: 管理员的专属智能办公助理（私人）\n\n"
    "只承办 admin 本人的日程、待办、材料撰写与信息整理。\n"
)
SOURCE_USER_MD = "# USER.md\n\n- 用户名: admin\n- 岗位: 系统管理员\n"


class _NoopProvisioner:
    """Stands in for the real provisioner when a test wants a bare member."""

    def provision(self, **_kwargs):
        return {"status": "skipped", "reason": "noop"}


class _Base(unittest.TestCase):
    """One instance root holding the tenant's shared 智能办公助理."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cow-userdefault-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.instance = os.path.join(self.tmp, "instance")
        self.tenant_root = os.path.join(self.tmp, "tenants", "acme")
        source_ws = os.path.join(self.instance, "agents", SOURCE_ID)
        os.makedirs(source_ws)
        os.makedirs(self.tenant_root)
        with open(os.path.join(source_ws, "AGENT.md"), "w", encoding="utf-8") as fh:
            fh.write(SOURCE_AGENT_MD)
        with open(os.path.join(source_ws, "USER.md"), "w", encoding="utf-8") as fh:
            fh.write(SOURCE_USER_MD)

        self.config_path = os.path.join(self.tmp, "config.json")
        # The instance default is its own Agent, so a test can stop the tenant's
        # shared assistant without asking the registry to disable its default.
        self.settings = {
            "agent_workspace": self.instance,
            "default_agent_id": "instance-default",
            "agents": [
                {"id": "instance-default", "name": "RongAI",
                 "workspace": self.instance, "enabled": True},
                {"id": SOURCE_ID, "name": SOURCE_NAME, "workspace": source_ws,
                 "enabled": True,
                 "description": "管理员专属智能办公助理：负责 admin 本人的日程"},
            ],
            "channel_instances": [],
        }
        self._write_config()
        self._pin(self.settings)

        self.svc = IdentityService(os.path.join(self.tmp, "identity.db"))
        self.tenant = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password=ADMIN_PASSWORD,
            shared_root=self.tenant_root, allow_weak=True)
        self.tenant_id = self.tenant["id"]
        self.root_id = self.svc.list_platform_users()[0]["id"]
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id=SOURCE_ID)
        self.svc.appoint_tenant_default_agent(
            tenant_id=self.tenant_id, agent_id=SOURCE_ID, actor_user_id=self.root_id)

        self.admin = AgentAdminService(self.config_path)
        patcher = patch("agent.personal_assistant.get_personal_assistant_provisioner",
                        side_effect=lambda: self._provisioner())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        set_agent_registry(None)

    # --- harness ---------------------------------------------------------

    def _write_config(self):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(self.settings, handle)

    def _pin(self, settings):
        set_agent_registry(AgentRegistry.from_config(team.resolve(settings)))

    def _reload_registry(self):
        """Re-read the roster from disk, as the runtime would after an edit.

        ``_agent_is_usable`` consults the process registry, not the admin
        service, so a test that stops an Agent has to publish that change or it
        would assert against a stale roster.
        """
        with open(self.config_path, encoding="utf-8") as handle:
            self._pin(json.load(handle))

    def _provisioner(self, **kwargs):
        return PersonalAssistantProvisioner(self.svc, self.admin, **kwargs)

    def _add_member(self, username, display_name="某人"):
        return self.svc.create_member(
            actor_user_id=self.root_id, tenant_id=self.tenant_id,
            operation="create-new", username=username, display_name=display_name,
            temporary_password="TempPass123!", roles=[])

    def _member_without_assistant(self, username, display_name="某人"):
        """A member with no personal assistant, to drive ``provision`` directly."""
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=_NoopProvisioner()):
            return self.svc.create_member(
                actor_user_id=self.root_id, tenant_id=self.tenant_id,
                operation="create-new", username=username,
                display_name=display_name, temporary_password="TempPass123!",
                roles=[])

    def _provision(self, user_id, username):
        return self._provisioner().provision(
            tenant_id=self.tenant_id, user_id=user_id, username=username,
            display_name=username.title(), actor_user_id=self.root_id)

    # --- state -----------------------------------------------------------

    def _row(self, user_id):
        rows = self.svc._store.execute(
            "SELECT default_agent_id, default_agent_revision, default_agent_origin"
            " FROM memberships WHERE tenant_id=? AND user_id=?",
            (self.tenant_id, user_id))
        return dict(rows[0]) if rows else None

    def _choose(self, user_id, agent_id, revision=None):
        return self.svc.set_user_default_agent(
            tenant_id=self.tenant_id, user_id=user_id, agent_id=agent_id,
            expected_revision=revision, actor_user_id=user_id)

    def _stop_agent(self, agent_id):
        self._set_enabled(agent_id, False)

    def _start_agent(self, agent_id):
        self._set_enabled(agent_id, True)

    def _set_enabled(self, agent_id, enabled):
        """Flip ``enabled`` in the roster and republish the registry.

        The roster is written where the product keeps it — the ``team.json``
        sidecar once it exists, ``config.json`` before that — so this composes
        with provisioning, which writes the same file. Editing ``config.json``
        directly would be editing a stale copy: ``team.read`` prefers the
        sidecar, so the new flag would never be seen.
        """
        path = team.team_file(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        roster = team.read(self.settings)
        for profile in roster.get("agents", []):
            if profile.get("id") == agent_id:
                profile["enabled"] = enabled
        path.write_text(json.dumps(roster), encoding="utf-8")
        self._reload_registry()

    def _clear_tenant_default(self):
        """Reach "no configured tenant default" through product paths only.

        There is no "unset" action on purpose: a tenant that could clear its
        default would leave new members with nothing to enter. The supported way
        a pointer becomes empty is a *delete*, so this appoints a throwaway Agent
        and deletes it — the same route ``release_deleted_agent`` serves in
        production, rather than an ``UPDATE`` a test invented.
        """
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="retired-entry")
        self.svc.appoint_tenant_default_agent(
            tenant_id=self.tenant_id, agent_id="retired-entry",
            actor_user_id=self.root_id)
        self.svc.release_deleted_agent(
            agent_id="retired-entry", actor_user_id=self.root_id)

    def _give_private_agent(self, user_id, agent_id):
        """Give ``user_id`` a second private Agent they may choose as default.

        A member's candidates are only their own private Agents (task 4.4), so a
        *different* target is what makes a user choice reachable — and a
        different target is exactly what turns ``origin`` from ``provisioned``
        into ``user``, which is the transition these tests need to observe.
        """
        path = team.team_file(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        roster = team.read(self.settings)
        roster.setdefault("agents", []).append({"id": agent_id, "name": agent_id})
        path.write_text(json.dumps(roster), encoding="utf-8")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id=agent_id,
                            private_owner_user_id=user_id, origin="user_created")
        self._reload_registry()
        return agent_id


# --- 1. provisioning only initialises an EMPTY preference -------------------

class ProvisioningInitialisesOnlyOnceTests(_Base):
    def test_provisioning_registers_the_assistant_it_just_made(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]

        result = self._provision(user_id, "alice")

        self.assertEqual(result["status"], "created")
        row = self._row(user_id)
        self.assertEqual(row["default_agent_id"], result["agent_id"])
        self.assertEqual(row["default_agent_origin"], "provisioned", (
            "provisioning records its own origin so a later choice can tell "
            "whether it is competing with a human"))
        self.assertEqual(row["default_agent_revision"], 2, (
            "the initialisation takes the pointer revision, so a console page "
            "read afterwards round-trips a value that is still current"))

    def test_the_member_can_still_overwrite_a_provisioned_default(self):
        """Task 4.7 H, the other direction: provisioning wins, then the member does.

        "First writer wins" must not harden into "provisioning is sticky". The
        initialisation deliberately leaves a current revision on the row, so the
        member's later choice is an ordinary optimistic-locked write — and it
        has to relabel ``default_agent_origin`` as ``user``, or the resolution
        would keep reporting a machine-made registration as the member's own.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        provisioned = self._provision(user_id, "alice")["agent_id"]
        self.assertEqual(self._row(user_id)["default_agent_origin"], "provisioned")
        self.assertEqual(
            self.svc.resolve_default_agent(self.tenant_id, user_id)["agent_id"],
            provisioned)

        chosen = self._give_private_agent(user_id, "alice-second")
        self._choose(user_id, chosen,
                     revision=self._row(user_id)["default_agent_revision"])

        row = self._row(user_id)
        self.assertEqual(row["default_agent_id"], chosen)
        self.assertNotEqual(row["default_agent_id"], provisioned)
        self.assertEqual(row["default_agent_origin"], "user",
                         "the member's own choice must be labelled as theirs")
        self.assertEqual(
            self.svc.resolve_default_agent(self.tenant_id, user_id),
            {"agent_id": chosen, "source": "user"})

        # And the machine side stays where it belongs: a provisioning rerun
        # cannot take the pointer back.
        result = self._provisioner().initialize_member_default(
            tenant_id=self.tenant_id, user_id=user_id, agent_id=provisioned,
            actor_user_id=self.root_id)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self._row(user_id)["default_agent_id"], chosen)

    def test_a_rerun_never_relabels_a_choice_the_member_made(self):
        """The window that mattered: a member who chose, then provisioning runs.

        A member who owns no assistant and has *already* chosen a default is the
        case the old ``owned_agent_id`` guard missed: provisioning ran and
        overwrote the choice, and with the origin column it would also have
        relabelled a human choice as ``provisioned``. The choice has to be a
        *different* Agent to be a change at all — re-picking the provisioned one
        is the idempotent retry task 4.4 already covers.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        first = self._provision(user_id, "alice")["agent_id"]
        second = self._give_private_agent(user_id, "alice-second")
        self._choose(user_id, second,
                     revision=self._row(user_id)["default_agent_revision"])
        self.assertEqual(self._row(user_id)["default_agent_origin"], "user")

        result = self._provisioner().initialize_member_default(
            tenant_id=self.tenant_id, user_id=user_id, agent_id=first,
            actor_user_id=self.root_id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_registered")
        row = self._row(user_id)
        self.assertEqual(row["default_agent_id"], second)
        self.assertEqual(row["default_agent_origin"], "user", (
            "a skipped initialisation must not relabel the member's own choice"))

    def test_a_provisioning_rerun_leaves_the_registration_alone(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        first = self._provision(user_id, "alice")
        before = self._row(user_id)

        result = self._provisioner().initialize_member_default(
            tenant_id=self.tenant_id, user_id=user_id,
            agent_id=first["agent_id"], actor_user_id=self.root_id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self._row(user_id), before, (
            "an initialisation that changes nothing must write nothing"))

    def test_initialisation_refuses_an_agent_of_another_tenant(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]

        with self.assertRaises(IdentityServiceError) as caught:
            self._provisioner().initialize_member_default(
                tenant_id=self.tenant_id, user_id=user_id,
                agent_id="not-bound-here", actor_user_id=self.root_id)

        self.assertEqual(caught.exception.status, 404)
        self.assertIsNone(self._row(user_id)["default_agent_id"])

    def test_the_initialisation_is_first_writer_wins_not_priority_ordered(self):
        """A user choice placed first also wins — the guard is "is it empty".

        If the rule were "provisioning may not overwrite a *user* choice", a
        provisioned pointer would be overwritable by a later provisioning run and
        the row would churn on every retry. Asking "is anything registered"
        makes the first writer the owner, whichever writer it was.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        first = self._provision(user_id, "alice")["agent_id"]

        result = self._provisioner().initialize_member_default(
            tenant_id=self.tenant_id, user_id=user_id, agent_id=SOURCE_ID,
            actor_user_id=self.root_id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self._row(user_id)["default_agent_id"], first)

    def test_the_initialisation_is_audited_only_when_it_writes(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        first = self._provision(user_id, "alice")

        self._provisioner().initialize_member_default(
            tenant_id=self.tenant_id, user_id=user_id,
            agent_id=first["agent_id"], actor_user_id=self.root_id)

        events = [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM audit_events WHERE action=?",
            ("member.default_agent.initialised",))]
        self.assertEqual(len(events), 1, (
            "only the write that changed the row carries an audit event"))


# --- 2. resolution reports the winner AND where it came from ----------------

class ResolutionSourceTests(_Base):
    def test_a_members_own_assistant_resolves_with_source_user(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertEqual(resolved["agent_id"], created["agent_id"])
        self.assertEqual(resolved["source"], "user", (
            "a registered preference is the member's own, whatever its origin"))

    def test_the_tenant_default_resolves_with_source_tenant(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertEqual(resolved["agent_id"], SOURCE_ID)
        self.assertEqual(resolved["source"], "tenant")

    def test_a_last_resort_reports_which_pool_it_came_from(self):
        """The console needs to say *why* it anchored where it did.

        With no registered preference and no configured default the answer is a
        guess, and reporting it as ``tenant`` would lend the tenant's authority
        to a fallback nobody chose.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        self._clear_tenant_default()

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertEqual(resolved["agent_id"], SOURCE_ID)
        self.assertEqual(resolved["source"], "shared")

    def test_a_private_only_pool_reports_the_private_fallback(self):
        """A tenant whose only candidate is private still gets an answer.

        The last resort is explicitly *not* the shared pool, so it must not be
        reported as one: an operator reading the payload has to be able to see
        that this anchor is somebody's private workspace.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")
        # Order matters: the shared source has to exist for provisioning to have
        # something to clone, so it is retired only once the private Assistant is
        # in place — which is also the real sequence, a tenant removing a shared
        # Agent long after its members got their copies.
        self._clear_tenant_default()
        self.svc.release_deleted_agent(agent_id=SOURCE_ID, actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertEqual(resolved["agent_id"], created["agent_id"])
        self.assertEqual(resolved["source"], "user", (
            "alice's own preference is the first and best answer"))

    def test_a_private_only_tenant_never_anchors_a_colleague(self):
        """Task 4.8: 绝不回落他人私有对象.

        With no shared Agent and no preference of their own, bob has nothing he
        may be anchored to. Returning alice's private workspace would put every
        message he sends into her persona, memory and file tree — so the answer
        is a refusal, and the payload says so rather than pointing at the only
        Agent left standing.
        """
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        private = self._provision(alice, "alice")["agent_id"]
        self._clear_tenant_default()
        self.svc.release_deleted_agent(agent_id=SOURCE_ID, actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)

        self.assertIsNone(resolved["agent_id"])
        self.assertIsNone(resolved["source"])
        self.assertNotEqual(resolved["agent_id"], private, (
            "another member's private Agent is not a candidate at all"))

    def test_a_member_with_their_own_private_agents_falls_back_to_those(self):
        """``own`` is the pool the caller may actually land in."""
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        # A private object alice owns but never registered as her default — the
        # state that reaches the last resort at all.
        private = self._give_private_agent(alice, "alice-own")
        self._clear_tenant_default()
        self.svc.release_deleted_agent(agent_id=SOURCE_ID, actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, alice)

        self.assertEqual(resolved["agent_id"], private)
        self.assertEqual(resolved["source"], "own", (
            "a private object of the caller's own is a different pool from the"
            " shared one, and the payload has to say which"))

    def test_nothing_usable_reports_no_target_and_no_source(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        self._clear_tenant_default()
        self.svc.release_deleted_agent(agent_id=SOURCE_ID, actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertIsNone(resolved["agent_id"])
        self.assertIsNone(resolved["source"], (
            "no candidate must be reported as no candidate, never as a source"))

    def test_a_subject_less_caller_reports_the_tenant_entry(self):
        """Administrators keep the tenant-wide answer, by design."""
        resolved = self.svc.resolve_default_agent(self.tenant_id)

        self.assertEqual(resolved["agent_id"], SOURCE_ID)
        self.assertEqual(resolved["source"], "tenant")

    def test_the_legacy_reader_returns_the_same_target(self):
        """``resolved_default_agent_id`` stays the same answer, one value wide."""
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")

        self.assertEqual(
            self.svc.resolved_default_agent_id(self.tenant_id, user_id),
            self.svc.resolve_default_agent(self.tenant_id, user_id)["agent_id"])
        self.assertEqual(
            created["agent_id"],
            self.svc.resolved_default_agent_id(self.tenant_id, user_id))

    def test_a_stale_preference_is_filtered_out_of_the_candidates(self):
        """A row naming another member's private Agent is not a candidate.

        Task 4.4 refuses to *write* that state; this pins that resolution also
        refuses to *honour* it, so a row written by the tenancy-only legacy
        writer (or by an older build) can never become an entry into someone
        else's workspace.
        """
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        alice_agent = self._provision(alice, "alice")["agent_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        self.svc.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=bob, agent_id=alice_agent,
            actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)

        self.assertNotEqual(resolved["agent_id"], alice_agent, (
            "another member's private workspace must never be the anchor"))
        self.assertNotEqual(resolved["source"], "user", (
            "the refused row must not be reported as bob's own choice"))

    def test_a_stopped_preference_is_not_the_reported_source(self):
        """The reported source must describe the answer, not the stored row."""
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")
        self._choose(user_id, created["agent_id"],
                     revision=self._row(user_id)["default_agent_revision"])
        self._stop_agent(created["agent_id"])

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)

        self.assertNotEqual(resolved["agent_id"], created["agent_id"])
        self.assertEqual(resolved["source"], "tenant", (
            "the anchor fell through to the tenant entry, so that is the source"))

    # --- 4.8 H: the reported source has to describe the answer ------------

    def _assert_source_is_honest(self, resolved, *, user_id=None,
                                 caller="the caller"):
        """The source must name the state that really produced this anchor.

        Task 4.8 (来源标注诚实): a badge is a claim about *why* this conversation
        landed here. So each value is cross-checked against the state it names —
        whose entry it is, whether it is owner-less, and whether a preference or
        a configured default exists that the payload would otherwise be
        silently contradicting.
        """
        agent_id, source = resolved["agent_id"], resolved["source"]
        bindings = {b["agent_id"]: b for b in self.svc.agents_for_tenant(self.tenant_id)}
        if source is None:
            self.assertIsNone(agent_id, "no source must mean no anchor")
            return
        self.assertIsNotNone(agent_id, f"source {source!r} without an anchor")
        owner = bindings[agent_id].get("private_owner_user_id")
        preference = self.svc.member_default_agent_id(self.tenant_id, user_id) \
            if user_id else None
        configured = self.svc.tenant_default_agent_id(self.tenant_id)
        if source == "user":
            self.assertEqual(agent_id, preference, (
                f"source 'user' must be {caller}'s stored preference"))
            self.assertIn(owner, (None, user_id), (
                "a member's own entry is shared or theirs, never a colleague's"))
        elif source == "tenant":
            self.assertEqual(agent_id, configured, (
                "source 'tenant' must be the tenant's configured entry"))
            self.assertIsNone(owner, (
                "a tenant entry is the one every member shares, so it is "
                "owner-less"))
        elif source == "shared":
            self.assertIsNone(owner, "the shared pool is owner-less")
            self.assertNotEqual(agent_id, configured, (
                "'shared' is the last resort, never the configured default"))
            self.assertNotEqual(agent_id, preference, (
                "'shared' is nobody's stored preference, so it must not be "
                "reported as a choice"))
        elif source == "own":
            self.assertEqual(owner, user_id, (
                "'own' means the caller's own private pool"))
            self.assertNotEqual(agent_id, configured)
            self.assertNotEqual(agent_id, preference, (
                "'own' is the last resort; a stored preference would be "
                "reported as 'user'"))
        else:
            self.fail(f"unknown source {source!r}")

    def test_the_source_honesty_check_is_not_vacuous(self):
        """Guards the guard: every branch above must reject a wrong label.

        A mislabelled payload is exactly what task 4.8 H calls a dishonest
        report, so the cross-check has to *fail* on one — otherwise the tests
        that use it would pass for any implementation at all.
        """
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        self._provision(alice, "alice")
        resolved = self.svc.resolve_default_agent(self.tenant_id, alice)
        self.assertEqual(resolved["source"], "user")

        for wrong in ("tenant", "shared", "own"):
            with self.assertRaises(AssertionError, msg=f"source={wrong!r} slipped"):
                self._assert_source_is_honest(dict(resolved, source=wrong),
                                              user_id=alice)
        # "no anchor" is not a source, and a source without an anchor is a lie.
        with self.assertRaises(AssertionError):
            self._assert_source_is_honest({"agent_id": SOURCE_ID, "source": None},
                                          user_id=alice)
        with self.assertRaises(AssertionError):
            self._assert_source_is_honest({"agent_id": None, "source": "shared"},
                                          user_id=alice)

    def test_every_reported_source_agrees_with_the_state_it_names(self):
        """Task 4.8 H over the chosen-entry sources: user, tenant, shared."""
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        own_assistant = self._provision(alice, "alice")["agent_id"]

        resolved = self.svc.resolve_default_agent(self.tenant_id, alice)
        self.assertEqual(resolved["source"], "user")
        self._assert_source_is_honest(resolved, user_id=alice, caller="alice")
        self.assertEqual(self.svc.member_default_agent_id(self.tenant_id, alice),
                         own_assistant)

        # bob chose nothing, so his answer is the tenant's entry — and the
        # payload says so instead of borrowing alice's preference.
        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)
        self.assertEqual(resolved["source"], "tenant")
        self._assert_source_is_honest(resolved, user_id=bob, caller="bob")
        self.assertIsNone(self.svc.member_default_agent_id(self.tenant_id, bob))
        self.assertEqual(resolved["agent_id"], SOURCE_ID)

        # With the tenant's entry gone, alice keeps her own preference (it
        # outranks every pool) while bob — who chose nothing — falls into the
        # shared pool. That is a fallback nobody chose and must be labelled so.
        self._clear_tenant_default()
        resolved = self.svc.resolve_default_agent(self.tenant_id, alice)
        self.assertEqual(resolved["source"], "user")
        self._assert_source_is_honest(resolved, user_id=alice, caller="alice")

        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)
        self.assertEqual(resolved["source"], "shared")
        self._assert_source_is_honest(resolved, user_id=bob, caller="bob")

    def test_the_fallback_pools_are_never_reported_as_a_choice(self):
        """Task 4.8 H for the last resorts: ``own`` and the empty answer.

        Both are answers nobody made a decision about. ``own`` must name the
        caller's own private pool (not the shared one, and not a colleague's),
        and an unusable preference must fall through to a *labelled* fallback
        rather than keep claiming ``user``.
        """
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        private = self._give_private_agent(alice, "alice-own")
        self._clear_tenant_default()
        self.svc.release_deleted_agent(agent_id=SOURCE_ID, actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, alice)
        self.assertEqual(resolved["source"], "own")
        self._assert_source_is_honest(resolved, user_id=alice, caller="alice")
        self.assertEqual(resolved["agent_id"], private)

        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)
        self.assertIsNone(resolved["source"])
        self._assert_source_is_honest(resolved, user_id=bob, caller="bob")

    def test_an_unusable_preference_is_reported_as_the_fallback_it_fell_to(self):
        """The row said "bob chose alice's Agent"; the payload must not agree.

        Resolution refuses the row (a colleague's private Agent is not a
        candidate), so the anchor is the tenant's entry. Reporting ``user``
        there would tell the console "bob picked this" about an Anchor bob never
        picked — the exact mislabelling task 4.8 forbids.
        """
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        alice_agent = self._provision(alice, "alice")["agent_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        self.svc.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=bob, agent_id=alice_agent,
            actor_user_id=self.root_id)

        resolved = self.svc.resolve_default_agent(self.tenant_id, bob)

        self.assertEqual(resolved["agent_id"], SOURCE_ID)
        self.assertEqual(resolved["source"], "tenant")
        self._assert_source_is_honest(resolved, user_id=bob, caller="bob")
        self.assertEqual(self.svc.member_default_agent_id(self.tenant_id, bob),
                         alice_agent, (
            "the stale row is still there; the honesty is in what is reported"))


# --- 3. delete clears the reference; disable does not ----------------------

class CleanupTests(_Base):
    def test_deleting_releases_the_pointer_and_its_origin(self):
        """A released pointer must read as "no preference" in every sense.

        Leaving ``default_agent_origin`` behind would make the row claim "a human
        chose this" while ``default_agent_id`` is NULL — and task 4.4 reads a
        non-NULL origin as "a registered preference exists", so the member would
        be locked out of their next choice without a revision they cannot read.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")

        self.svc.release_deleted_agent(
            agent_id=created["agent_id"], actor_user_id=self.root_id)

        row = self._row(user_id)
        self.assertIsNone(row["default_agent_id"])
        self.assertIsNone(row["default_agent_origin"], (
            "a released pointer is an empty preference, not a choice"))

    def test_a_released_pointer_can_be_replaced_without_a_revision(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        created = self._provision(user_id, "alice")
        replacement = self._give_private_agent(user_id, "alice-chosen")
        self.svc.release_deleted_agent(
            agent_id=created["agent_id"], actor_user_id=self.root_id)

        # No revision at all: legitimate, because nothing is registered any more.
        self._choose(user_id, replacement)

        self.assertEqual(self._row(user_id)["default_agent_id"], replacement)

    def test_deleting_one_members_assistant_leaves_the_others_alone(self):
        alice = self._member_without_assistant("alice", "Alice")["user_id"]
        bob = self._member_without_assistant("bob", "Bob")["user_id"]
        alice_agent = self._provision(alice, "alice")["agent_id"]
        bob_agent = self._provision(bob, "bob")["agent_id"]

        self.svc.release_deleted_agent(agent_id=alice_agent, actor_user_id=self.root_id)

        self.assertIsNone(self._row(alice)["default_agent_id"])
        self.assertEqual(self._row(bob)["default_agent_id"], bob_agent)

    def test_a_stopped_preference_is_preserved(self):
        """停用 is temporary, so the preference outlives it.

        Clearing on disable would silently discard a choice the member made and
        could not re-make (the user action refuses a stopped target), so the
        preference has to be *kept* and come back with the Agent.
        """
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        self._provision(user_id, "alice")
        chosen = self._give_private_agent(user_id, "alice-chosen")
        self._choose(user_id, chosen,
                     revision=self._row(user_id)["default_agent_revision"])

        self._stop_agent(chosen)

        row = self._row(user_id)
        self.assertEqual(row["default_agent_id"], chosen, (
            "stopping an Agent must not clear the member's preference"))
        self.assertEqual(row["default_agent_origin"], "user")

    def test_re_enabling_restores_the_preference_as_the_anchor(self):
        user_id = self._member_without_assistant("alice", "Alice")["user_id"]
        self._provision(user_id, "alice")
        chosen = self._give_private_agent(user_id, "alice-chosen")
        self._choose(user_id, chosen,
                     revision=self._row(user_id)["default_agent_revision"])
        self._stop_agent(chosen)
        self.assertNotEqual(
            self.svc.resolve_default_agent(self.tenant_id, user_id)["agent_id"],
            chosen)

        self._start_agent(chosen)

        resolved = self.svc.resolve_default_agent(self.tenant_id, user_id)
        self.assertEqual(resolved["agent_id"], chosen)
        self.assertEqual(resolved["source"], "user", (
            "the preference was never thrown away, so it is the answer again"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
