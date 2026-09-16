# encoding:utf-8
"""The five independent capability switches (task 9.1).

``enable-member-personal-console`` ships a member-facing surface, so a
deployment needs a way to *withdraw* pieces of it without editing code: one
switch per slice, each turned on only after that slice's real evidence exists.

The rule this file fixes is narrow but load-bearing:

* a switch decides whether a capability is **offered** — never whether
  authorization is checked. Owner, membership and permission rules keep running
  behind a switch that is on, and are not replaced when one is switched off;
* withdrawing a switch must not close something **else** that was already
  accepted (the personal execution switch must not hide the accepted channel
  catalogue), and must not strand a member with an object they can no longer
  retract (revoke, unlink, disable, delete and clear stay reachable);
* an unevaluable switch is a **closed** switch, and an unknown name is not an
  enabled one.

Change ``unify-console-by-data-scope`` retired the ``personal.*`` pages from the
menu contract, so a built-in member now reaches their own surface through the
shared business pages (``admin.agents`` / ``admin.channels`` / ``admin.memory`` /
``admin.skills``). Task 8.8 closed the loop and stopped *issuing* the retired ids
entirely, so the switch semantics now show up in exactly one place: the shared
page that carries the slice.

* the retired ids are **gone from the projection** — a withdrawal cannot be read
  off a page that no longer exists, and a legacy grant's withdrawal is still a
  *write-path* refusal (``capability_disabled``, asserted below on each service);
* the page that carries the member's own channel surface (``admin.channels`` for
  a member, ``scope='self'``) reports the same switch block the retired
  ``personal.channels`` reported, so the console can name the capability that is
  off instead of inferring it from a missing grant;
* withdrawing one slice must not close another surface — the shared pages stay
  exactly as available as they were, which is asserted on the pages themselves.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import config
from auth.policy import (
    PERSONAL_CAPABILITY_DEFAULTS,
    PERSONAL_CAPABILITY_SWITCHES,
    PERSONAL_PAGE_CAPABILITIES,
    personal_capability_enabled,
    personal_page_capabilities,
    personal_page_enabled,
)
from auth.service import IdentityService, IdentityServiceError

from tests.test_personal_console_acceptance import (
    _Fixture as _AcceptanceFixture, _menu)

#: The five ids the personal console was projected on, and the keys of
#: :data:`PERSONAL_PAGE_CAPABILITIES`. Task 8.8 retired the pages but kept the
#: switch map, because the *carrier* pages read it; restating the ids here (as
#: plain data, not as an import from a registry that no longer exists) is what
#: lets these tests assert the switch map's key set and its carrier semantics.
PERSONAL_IDS = (
    "personal.agents",
    "personal.channels",
    "personal.memory",
    "personal.tools",
    "personal.skills",
)

TOOLS_BASE = "http://localhost:9899"


class SwitchRegistryTests(unittest.TestCase):
    """The registry itself: names, defaults, and fail-closed resolution."""

    def test_the_designs_five_switches_are_registered(self):
        self.assertEqual(
            set(PERSONAL_CAPABILITY_SWITCHES),
            {"member_personal_console", "user_private_agent_management",
             "personal_memory_write", "personal_channel_onboarding",
             "personal_channel_runtime"})

    def test_every_switch_ships_with_a_recorded_default(self):
        self.assertEqual(set(PERSONAL_CAPABILITY_DEFAULTS),
                         set(PERSONAL_CAPABILITY_SWITCHES))

    def test_the_runtime_switch_ships_closed_and_the_rest_ship_open(self):
        """The posture the design states: configuration may be collected, no
        connection may be started until a real acceptance is recorded."""
        self.assertFalse(PERSONAL_CAPABILITY_DEFAULTS["personal_channel_runtime"])
        for name in ("member_personal_console", "user_private_agent_management",
                     "personal_memory_write", "personal_channel_onboarding"):
            self.assertTrue(PERSONAL_CAPABILITY_DEFAULTS[name], name)

    def test_an_unknown_name_is_not_an_enabled_one(self):
        self.assertFalse(personal_capability_enabled(
            "member_personal_console_typo", config={}))

    def test_an_unreadable_configuration_falls_back_to_the_default(self):
        """A broken config must not silently enable what ships closed."""
        with patch("config.conf", side_effect=RuntimeError("boom")):
            self.assertFalse(
                personal_capability_enabled("personal_channel_runtime"))
            self.assertTrue(
                personal_capability_enabled("member_personal_console"))

    def test_a_configuration_value_wins_over_the_default(self):
        self.assertFalse(personal_capability_enabled(
            "member_personal_console", config={"member_personal_console": False}))
        self.assertTrue(personal_capability_enabled(
            "personal_channel_runtime", config={"personal_channel_runtime": True}))

    def test_a_string_value_is_read_the_way_a_config_file_spells_it(self):
        for raw in ("1", "true", "TRUE", "on", "yes", "enabled"):
            self.assertTrue(personal_capability_enabled(
                "member_personal_console", config={"member_personal_console": raw}),
                raw)
        for raw in ("0", "false", "no", "off", "disabled", ""):
            self.assertFalse(personal_capability_enabled(
                "member_personal_console", config={"member_personal_console": raw}),
                raw)

    def test_every_switch_map_entry_names_a_retired_page_and_its_master(self):
        """The map still keys the *retired* ids (its only vocabulary).

        Nothing projects ``personal.*`` any more (task 8.8), so the map is read
        by the carriers through the ids: keeping the key set pinned is what stops
        a carrier from silently losing the slice switch it consults.
        """
        self.assertEqual(set(PERSONAL_PAGE_CAPABILITIES), set(PERSONAL_IDS))
        for pid in PERSONAL_IDS:
            self.assertIn("member_personal_console",
                          PERSONAL_PAGE_CAPABILITIES[pid], pid)

    def test_a_page_is_open_only_when_every_switch_behind_it_is_on(self):
        off = {"member_personal_console": False}
        self.assertFalse(personal_page_enabled("personal.memory", config=off))
        self.assertEqual(
            personal_page_capabilities("personal.memory", config=off),
            {"member_personal_console": False, "personal_memory_write": True})

        only_console = {"member_personal_console": True,
                        "personal_memory_write": False}
        self.assertFalse(personal_page_enabled("personal.memory",
                                               config=only_console))


class _SwitchFixture(_AcceptanceFixture):
    """The Stage-8 harness, plus a way to withdraw one switch per test."""

    def switches(self, **over):
        settings = dict(self.settings)
        settings.update(over)
        patcher = patch.object(config, "conf", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        return settings

    def _member_id(self, username):
        return [m for m in self.service.list_members(self.tenant_id)["items"]
                if m["username"] == username][0]["user_id"]


class ProjectionTests(_SwitchFixture):
    """The authoritative projection is where a withdrawal becomes visible.

    The retired ``personal.*`` ids are **absent** from the projection (task 8.8),
    so the withdrawal is asserted where it is *observable* now: on the shared page
    that carries the member's own surface — ``admin.channels`` reports the
    ``switches`` block of the retired ``personal.channels``, and ``admin.agents``
    withdraws ``create``. Absence of the retired ids is asserted alongside, because
    "the page is gone" must never be what a withdrawal looks like: it is one
    deployment fact, read in two places, and neither may become the other.
    """

    #: The shared pages a member's own surface lives on — the successors of the
    #: five retired personal pages (change ``unify-console-by-data-scope``).
    MEMBER_PAGES = ("admin.agents", "admin.channels", "admin.memory",
                    "admin.skills")

    def test_the_shipped_configuration_opens_the_members_own_surface(self):
        _, token = self._member("plain", ["member"])
        pages = self._pages(token)
        for pid in self.MEMBER_PAGES:
            self.assertTrue(pages[pid]["available"], pid)
        # The retired ids are not issued at all, whether or not a switch is off.
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)
        # The carrier of the retired ``personal.channels`` switches: the same
        # block travels with the shared page.
        self.assertEqual(pages["admin.channels"]["switches"],
                         personal_page_capabilities("personal.channels"))
        self.assertTrue(all(pages["admin.channels"]["switches"].values()))

    def test_the_console_wide_switch_withdraws_the_members_surface(self):
        _, token = self._member("plain", ["member"])
        self.switches(member_personal_console=False)

        pages = self._pages(token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)
        # The page that carries the member's own channel surface reports the
        # withdrawal in the switch block it owns, and stops offering the create
        # its write path now refuses (the "clickable but refused" shape).
        self.assertFalse(
            pages["admin.channels"]["switches"]["member_personal_console"])
        self.assertFalse(pages["admin.channels"]["actions"]["create"])
        self.assertTrue(pages["admin.channels"]["available"],
                        "the page stays readable: revocation must stay reachable")

    def test_withdrawing_a_slice_leaves_the_carrier_pages_open(self):
        _, token = self._member("plain", ["member"])
        self.switches(personal_memory_write=False)

        pages = self._pages(token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, pages, pid)
        for pid in ("admin.agents", "admin.channels", "admin.skills"):
            self.assertTrue(pages[pid]["available"], pid)
            self.assertEqual(pages[pid]["reason"], "", pid)
        # Memory is the other carrier of the same withdrawal shape: its *slice*
        # switch gates the write path, not the page, so the page stays open and
        # the refusal is answered by `PersonalMemoryService.save` (asserted in
        # ``PersonalMemoryWriteTests`` below).
        self.assertTrue(pages["admin.memory"]["available"])

    def test_a_withdrawal_never_touches_the_admin_projection_of_the_same_actor(self):
        """The switch is scoped to the member slice, both ways: the actor keeps
        every page they hold as an operator, and the retired ids are absent for
        them too (nobody is handed one any more)."""
        before = self._pages(self.root_token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, before, pid)

        self.switches(member_personal_console=False)
        after = self._pages(self.root_token)
        for pid in PERSONAL_IDS:
            self.assertNotIn(pid, after, pid)
        for pid in self.MEMBER_PAGES:
            self.assertTrue(after[pid]["available"], pid)
        self.assertTrue(after["admin.members"]["available"])
        self.assertTrue(after["admin.roles"]["available"])

    def test_a_withdrawal_never_widens_a_page_that_was_denied(self):
        """Off is off: a member whose role carries a menu set that excludes the
        shared pages stays bound to it, and the reason they see is the one their
        own grants produced — a withdrawal must not look like a capability they
        were never offered."""
        self._role("one-page", ["memory.read"], [_menu("admin.memory")])
        _, token = self._member("narrow", ["one-page"])

        pages = self._pages(token)
        self.assertTrue(pages["admin.memory"]["available"])
        self.assertEqual(pages["admin.memory"]["scope"], "agent")
        self.assertTrue(pages["admin.skills"]["menu_denied"])
        self.assertFalse(pages["admin.skills"]["available"])
        withheld_reason = pages["admin.skills"]["reason"]

        self.switches(member_personal_console=False)
        after = self._pages(token)
        # The withheld page stays withheld, with the reason its own grants
        # produced: the withdrawal did not hand the member a new one.
        self.assertTrue(after["admin.skills"]["menu_denied"])
        self.assertFalse(after["admin.skills"]["available"])
        self.assertEqual(after["admin.skills"]["reason"], withheld_reason)
        # The granted page is neither withdrawn nor silently re-scoped by a
        # switch that was never about its range.
        self.assertTrue(after["admin.memory"]["available"])
        self.assertEqual(after["admin.memory"]["scope"], "agent")
        # The retired id is not an escape hatch under a withdrawal either: the
        # page does not exist, so "the capability answers separately" now means
        # the write paths still refuse while the projection simply has no entry.
        self.assertNotIn("personal.memory", after)
        self.assertNotIn("personal.memory", pages)

    def test_the_runtime_switch_closes_execution_without_hiding_the_catalogue(
            self):
        """目录可读、执行关闭 — and the *reverse* shape task 9.1 adds: turning
        the execution switch off must not take the accepted catalogue away."""
        _, token = self._member("plain", ["member"])
        entry = self._pages(token)["admin.channels"]
        self.assertTrue(entry["available"], "the catalogue stays readable")
        self.assertEqual(entry["states"]["read"], True)
        self.assertFalse(entry["states"]["execution"],
                         "no acceptance is recorded, so nothing is live")

    def test_the_runtime_switch_is_read_from_the_connection_gate(self):
        """One source of truth: the projection must not carry a second copy."""
        self.switches(personal_channel_runtime=True)
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})):
            self.assertTrue(self.service._personal_channel_execution_open())
            _, token = self._member("plain", ["member"])
            entry = self._pages(token)["admin.channels"]
            self.assertTrue(entry["states"]["execution"])


class PersonalResourceWriteTests(_SwitchFixture):
    """The console-wide switch also gates the resource-configuration write."""

    TOOL = "builtin:echo"
    MASTER_KEY = "00112233445566778899aabbccddeeff"

    def setUp(self):
        super().setUp()
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)
        self.alice = self._member("alice", ["member"])[0]
        self._role("personalizer", [], [
            {"resource_kind": "tool", "resource_id": self.TOOL,
             "action": "execute"}])
        self._attach_role("alice", "personalizer")

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _attach_role(self, username, code):
        membership = self.service._membership(
            self._member_id(username), self.tenant_id)
        role = [r for r in self.service.list_roles(self.tenant_id)
                if r["code"] == code][0]
        with self.service._tx() as con:
            con.execute(
                "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                " VALUES(?,?,?)",
                (self.tenant_id, membership["id"], role["id"]))
            con.commit()

    def _save(self, **over):
        args = dict(actor_user_id=self.alice, tenant_id=self.tenant_id,
                    resource_kind="tool", resource_id=self.TOOL,
                    params={"timeout": 30})
        args.update(over)
        return self.service.save_personal_resource_config(**args)

    def test_saving_is_refused_when_the_console_switch_is_withdrawn(self):
        self.switches(member_personal_console=False)
        with self.assertRaises(IdentityServiceError) as caught:
            self._save()
        self.assertEqual(caught.exception.code, "capability_disabled")
        self.assertEqual(caught.exception.status, 403)

    def test_a_withdrawn_switch_still_refuses_a_foreign_owner(self):
        """The switch removes authority; it never replaces the owner check."""
        self._save()
        self.switches(member_personal_console=False)
        # Reading someone else's configuration stays owner-scoped: an admin
        # cannot use the withdrawal (or the capability) as a read bypass.
        self.assertIsNone(self.service.get_personal_resource_config(
            actor_user_id=self.root_user_id, tenant_id=self.tenant_id,
            resource_kind="tool", resource_id=self.TOOL))
        self.assertIsNotNone(self.service.get_personal_resource_config(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            resource_kind="tool", resource_id=self.TOOL))

    def test_reading_and_clearing_stay_reachable_after_the_withdrawal(self):
        """Narrowing must not strand a secret the member already saved."""
        self._save(secret="owner-token")

        self.switches(member_personal_console=False)
        self.assertIsNotNone(self.service.get_personal_resource_config(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            resource_kind="tool", resource_id=self.TOOL))
        self.assertTrue(self.service.clear_personal_resource_config(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            resource_kind="tool", resource_id=self.TOOL))
        self.assertIsNone(self.service.get_personal_resource_config(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            resource_kind="tool", resource_id=self.TOOL))


class PrivateAgentCreationTests(_SwitchFixture):
    """``user_private_agent_management`` gates creation, not maintenance."""

    def setUp(self):
        super().setUp()
        self.service.bind_agent(tenant_id=self.tenant_id,
                                agent_id="template-agent")
        self.alice = self._member("alice", ["member"])[0]
        self.service.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=self.alice,
            agent_id="template-agent")

    def _service(self):
        from agent.private_agent import PrivateAgentService

        class _Recorder:
            def __init__(self):
                self.agents = []
                self.deleted = []

            def snapshot(self):
                return {"agents": list(self.agents)}

            def clone_agent(self, source_agent_id, agent_id, name=None,
                            workspace=None, revision=None, knowledge_mode=None):
                self.agents.append({"id": agent_id, "name": name or agent_id,
                                    "workspace": workspace, "enabled": True,
                                    "description": "", "persona_summary": ""})
                return {"id": agent_id}

            def create_agent(self, agent_id, name, workspace=None, **fields):
                self.agents.append({"id": agent_id, "name": name,
                                    "workspace": workspace, "enabled": True,
                                    "description": "", "persona_summary": ""})
                return {"id": agent_id}

            def delete_agent(self, agent_id, revision=None):
                self.deleted.append(agent_id)
                self.agents = [a for a in self.agents if a["id"] != agent_id]
                return {"id": agent_id}

            def update_agent(self, agent_id, **fields):
                return {"id": agent_id}

        recorder = _Recorder()
        return PrivateAgentService(self.service, admin_service=recorder), recorder

    def test_creation_is_refused_when_the_switch_is_withdrawn(self):
        service, recorder = self._service()
        self.switches(user_private_agent_management=False)

        with self.assertRaises(IdentityServiceError) as caught:
            service.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="mine")
        self.assertEqual(caught.exception.code, "capability_disabled")
        self.assertEqual(recorder.agents, [],
                         "a refused create must not leave a workspace or roster row")
        self.assertEqual([b for b in self.service.agents_for_tenant(self.tenant_id)
                          if b.get("private_owner_user_id") == self.alice], [])

    def test_an_existing_private_agent_is_still_maintainable(self):
        """Deleting what a member already owns must not need the create switch."""
        service, _ = self._service()
        created = service.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="mine")
        self.switches(user_private_agent_management=False)

        service.delete_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id,
            agent_id=created["agent_id"])
        self.assertIsNone(self.service.get_agent_binding(created["agent_id"]))


class PersonalMemoryWriteTests(_SwitchFixture):
    """``personal_memory_write`` gates adding content, not retracting it."""

    def setUp(self):
        super().setUp()
        self.alice = self._member("alice", ["member"])[0]
        self.state = tempfile.mkdtemp(prefix="personal-memory-switch-")
        self._state = patch("common.state_dir.user_root",
                            side_effect=lambda ident: os.path.join(
                                self.state, ident.tenant_id, ident.user_id))
        self._state.start()
        self.addCleanup(self._state.stop)

    def _service(self):
        from agent.memory.personal import PersonalMemoryService
        from common.runtime_identity import RuntimeIdentity

        return PersonalMemoryService(identity=RuntimeIdentity(
            agent_id="agent-x", user_id=self.alice, tenant_id=self.tenant_id))

    def test_saving_is_refused_when_the_switch_is_withdrawn(self):
        from agent.memory.personal import PersonalMemoryError

        service = self._service()
        self.switches(personal_memory_write=False)
        with self.assertRaises(PersonalMemoryError) as caught:
            service.save("MEMORY.md", "hello")
        self.assertEqual(caught.exception.code, "capability_disabled")
        self.assertEqual(caught.exception.status, 403)

    def test_listing_deleting_and_clearing_stay_reachable(self):
        from agent.memory.personal import PersonalMemoryError

        service = self._service()
        service.save("MEMORY.md", "hello")
        service.save("memory/notes.md", "note")

        self.switches(personal_memory_write=False)
        self.assertEqual(len(service.list_entries()), 2, "reading stays open")
        service.delete("memory/notes.md")
        self.assertEqual([e["id"] for e in service.list_entries()],
                         ["MEMORY.md"])
        service.clear()
        self.assertEqual(service.list_entries(), [])
        with self.assertRaises(PersonalMemoryError):
            service.save("MEMORY.md", "again")


class PersonalChannelOnboardingTests(_SwitchFixture):
    """``personal_channel_onboarding`` gates opening, never closing."""

    MASTER_KEY = "00112233445566778899aabbccddeeff"
    #: Alice's private Agent, from the roster the parent fixture installs. The
    #: personal create has to name a target she owns (task 2.6): ``agent-a`` used
    #: to stand in here and is exactly the *shared* shape the service must refuse.
    TARGET = "target-alice"

    def setUp(self):
        super().setUp()
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)
        self.alice = self._member("alice", ["member"])[0]
        self._private_agent(self.alice, self.TARGET)

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _row(self):
        rows = self.service._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE tenant_id=?"
            " AND scope='user' AND owner_user_id=?",
            (self.tenant_id, self.alice))
        return dict(rows[0]) if rows else None

    def _create(self):
        return self.service.create_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            channel_type="feishu", display_name="mine", agent_id=self.TARGET,
            credentials={"feishu_app_id": "cli_personal_a",
                         "feishu_app_secret": "s3cr3t-personal"},
            recent_password="MemPassFinal1")

    def test_creating_a_personal_instance_needs_the_switch(self):
        self.switches(personal_channel_onboarding=False)
        with self.assertRaises(IdentityServiceError) as caught:
            self._create()
        self.assertEqual(caught.exception.code, "capability_disabled")
        self.assertIsNone(self._row())

    def test_starting_a_binding_needs_the_switch(self):
        created = self._create()
        self.switches(personal_channel_onboarding=False)
        with self.assertRaises(IdentityServiceError) as caught:
            self.service.start_personal_channel_binding(
                actor_user_id=self.alice, tenant_id=self.tenant_id,
                instance_id=created["id"])
        self.assertEqual(caught.exception.code, "capability_disabled")

    def test_editing_a_personal_instance_needs_the_switch(self):
        created = self._create()
        self.switches(personal_channel_onboarding=False)
        with self.assertRaises(IdentityServiceError) as caught:
            self.service.update_personal_channel_instance(
                actor_user_id=self.alice, tenant_id=self.tenant_id,
                instance_id=created["id"],
                expected_version=created["version"], display_name="renamed",
                recent_password="MemPassFinal1")
        self.assertEqual(caught.exception.code, "capability_disabled")

    def test_disabling_and_revoking_stay_reachable_after_the_withdrawal(self):
        created = self._create()
        self.switches(personal_channel_onboarding=False)

        disabled = self.service.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            instance_id=created["id"], active=False,
            expected_version=created["version"],
            recent_password="MemPassFinal1")
        self.assertFalse(disabled["active"])

        revoked = self.service.revoke_personal_channel_credentials(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            instance_id=created["id"], expected_version=disabled["version"],
            recent_password="MemPassFinal1")
        self.assertFalse(revoked["active"])

    def test_re_enabling_a_disabled_instance_needs_the_switch_again(self):
        created = self._create()
        disabled = self.service.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.tenant_id,
            instance_id=created["id"], active=False,
            expected_version=created["version"],
            recent_password="MemPassFinal1")

        self.switches(personal_channel_onboarding=False)
        with self.assertRaises(IdentityServiceError) as caught:
            self.service.set_personal_channel_instance_active(
                actor_user_id=self.alice, tenant_id=self.tenant_id,
                instance_id=created["id"], active=True,
                expected_version=disabled["version"],
                recent_password="MemPassFinal1")
        self.assertEqual(caught.exception.code, "capability_disabled")


if __name__ == "__main__":
    unittest.main()
