# encoding:utf-8
"""One Agent lifecycle for every role — task 4.3 of ``unify-console-by-data-scope``.

Before this change, ``agent.admin``'s delete quietly cleared any channel
instance bound to the Agent it was erasing, "so the channel falls back to the
default Agent". That is an automatic rebind of live traffic to an Agent nobody
chose, and the spec forbids it (存在运行或有效渠道引用时 SHALL 返回冲突，不自动终止、
改绑或回落). The rule now lives once, in :mod:`agent.deletion_guard`, and both the
member's own-object path and the shared console path ask it.

What this file drives through the real handler and a real ``IdentityService``:

* **the same maintenance rules for both owner roles** — an ordinary member
  maintaining their own private Agent and an administrator maintaining theirs
  get the same fields, the same enable/disable behaviour and the same refusals
  for the objects that are *not* theirs;
* **provenance protection** — a supplied assistant stays undeletable for its
  owner, whichever role that owner holds: the predicate is the *binding's*
  origin, not the caller's rank;
* **reference protection** — a channel instance that still routes to the Agent
  is a 409 that names it, and the refusal leaves both stores untouched;
* **clean detachment** — a delete that does pass releases the binding and every
  default pointer, so the object cannot be resolved afterwards.

The console's Agent service is built from the *instance* root
(``get_data_root()/config.json``), so the harness pins that root at a temp
directory: without it the handler would read and write the developer's real
roster, and a test about deletion would be a test about somebody's live Agents.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import web

from auth.service import IdentityService
from channel.web import web_channel, auth_handlers, admin_handlers


class _Base(unittest.TestCase):
    """One tenant with an administrator, two members and private objects."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "identity.db")
        self.data_root = os.path.join(self.tmp, "data")
        self.instance = os.path.join(self.tmp, "instance")
        self.tenant_root = os.path.join(self.tmp, "tenants", "acme")
        for path in (self.data_root, self.instance, self.tenant_root):
            os.makedirs(path)

        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.tenant_root, allow_weak=True)
        self.tenant_id = self.svc.list_tenants()[0]["id"]
        self.platform_admin = self.svc.list_platform_users()[0]

        self.admin_id = self._member("acmeadmin", ["tenant_admin"], "Str0ngTa1",
                                     final="Str0ngTaFinal")
        self.admin_token = self.svc.login("acmeadmin", "Str0ngTaFinal").token
        self.alice = self._member("alice", ["member"], "Str0ngAl1",
                                  final="Str0ngAlice9")
        self.alice_token = self.svc.login("alice", "Str0ngAlice9").token
        self.bob = self._member("bob", ["member"], "Str0ngBo1",
                                final="Str0ngBob99")
        self.bob_token = self.svc.login("bob", "Str0ngBob99").token

        # The instance's shared default template plus each owner's private
        # object, written where the handler reads them.
        self._seed_roster(["primary", "alice-agent", "admin-agent", "shared-agent"])
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="shared-agent")
        self._bind_private("alice-agent", self.alice)
        self._bind_private("admin-agent", self.admin_id)

        with open(os.path.join(self.data_root, "config.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"agent_workspace": self.instance}, handle)

    # -- fixture helpers -----------------------------------------------------

    def _member(self, username, roles, temporary, *, final):
        user_id = self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username=username, display_name=username,
            temporary_password=temporary, roles=list(roles))["user_id"]
        self.svc.change_password(
            self.svc.login(username, temporary).token, temporary, final)
        return user_id

    def _bind_private(self, agent_id, user_id, *, origin="user_created"):
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id=agent_id,
                            private_owner_user_id=user_id, origin=origin)
        return agent_id

    def _seed_roster(self, agent_ids, *, default="primary"):
        path = os.path.join(self.instance, "agents", "team.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        profiles = []
        for index, agent_id in enumerate(agent_ids):
            entry = {"id": agent_id, "name": agent_id}
            if index:
                entry["workspace"] = os.path.join(self.instance, "agents", agent_id)
            profiles.append(entry)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"agents": profiles, "default_agent_id": default}, handle)
        return path

    def add_roster_entry(self, agent_id):
        """Put ``agent_id`` in the roster without touching its identity binding.

        A provenance test needs both halves: the binding (where ``origin``
        lives) and the roster row (what the delete actually removes). Splitting
        them lets a test write any origin, including the historical ``unknown``
        a pre-column row carries.
        """
        path = os.path.join(self.instance, "agents", "team.json")
        with open(path, encoding="utf-8") as handle:
            roster = json.load(handle)
        roster["agents"].append({
            "id": agent_id, "name": agent_id,
            "workspace": os.path.join(self.instance, "agents", agent_id),
        })
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(roster, handle)
        return agent_id

    def supplied_assistant(self, agent_id, user_id):
        """An object the *system* handed out, with the roster entry to match.

        The provisioner's origin is what the delete protection reads, so this is
        the shape under test rather than a flag a test can set on a request.
        """
        self.add_roster_entry(agent_id)
        return self._bind_private(agent_id, user_id, origin="provisioned_assistant")

    def route_channel_to(self, agent_id, *, instance_id="feishu-ops"):
        """Bind a roster channel instance to ``agent_id`` (the live-route case)."""
        path = os.path.join(self.instance, "agents", "team.json")
        with open(path, encoding="utf-8") as handle:
            roster = json.load(handle)
        roster["channel_instances"] = [{
            "instance_id": instance_id, "channel_type": "feishu",
            "agent_id": agent_id,
        }]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(roster, handle)
        return instance_id

    def unlink_channels(self):
        """Do what the console's "disconnect" does: drop the Agent binding."""
        path = os.path.join(self.instance, "agents", "team.json")
        with open(path, encoding="utf-8") as handle:
            roster = json.load(handle)
        for inst in roster.get("channel_instances") or []:
            inst["agent_id"] = ""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(roster, handle)

    def route_tenant_channel_to(self, agent_id, *, instance_id="tenant-ops",
                                channel_type="feishu", owner=None):
        """A tenant channel row in the *identity* store (task 4.7 C).

        The other half of the reference check: the roster's
        ``channel_instances`` is instance-level routing, while these are the
        per-tenant rows the business console writes. A delete has to respect
        both, and they live in different stores — which is why the guard reads
        each one separately.
        """
        owner = owner or self.alice
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
                " display_name, agent_id, scope, owner_user_id, created_by)"
                " VALUES(?,?,?,?,?,'user',?,?)",
                (instance_id, self.tenant_id, channel_type, "Tenant Bot",
                 agent_id, owner, owner))
            con.commit()
        return instance_id

    def drop_tenant_channel(self, instance_id):
        with self.svc._tx() as con:
            con.execute("DELETE FROM tenant_channel_instances WHERE id=?",
                        (instance_id,))
            con.commit()

    def channel_bindings(self):
        path = os.path.join(self.instance, "agents", "team.json")
        with open(path, encoding="utf-8") as handle:
            roster = json.load(handle)
        return [i.get("agent_id") for i in roster.get("channel_instances") or []]

    def binding(self, agent_id):
        return self.svc.get_agent_binding(agent_id)

    def roster_ids(self):
        path = os.path.join(self.instance, "agents", "team.json")
        with open(path, encoding="utf-8") as handle:
            return [a["id"] for a in json.load(handle)["agents"]]

    def stored_user_default(self, user_id):
        rows = self.svc._store.execute(
            "SELECT default_agent_id FROM memberships WHERE tenant_id=?"
            " AND user_id=?", (self.tenant_id, user_id))
        return (rows[0]["default_agent_id"] if rows else None) or None

    # -- requests ------------------------------------------------------------

    def _request(self, path, method="GET", payload=None, token=None):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        kwargs["headers"] = {
            "Content-Type": "application/json",
            "Cookie": f"cow_session={token}",
            "X-Tenant-ID": self.tenant_id,
        }
        settings = {"identity_mode": "database", "identity_db_path": self.db,
                    "agent_workspace": self.instance}
        with patch.object(web_channel, "conf", return_value=settings), \
                patch("config.conf", return_value=settings), \
                patch.object(web_channel, "get_data_root",
                             return_value=self.data_root), \
                patch.object(web_channel, "_reload_agent_runtime",
                             lambda *a, **k: None), \
                patch.object(auth_handlers, "_get_service", lambda: self.svc), \
                patch.object(admin_handlers, "_get_service", lambda: self.svc), \
                patch("auth.service.get_identity_service", lambda: self.svc):
            app = web.application(("/api/agents", "AgentsHandler"),
                                  vars(web_channel), autoreload=False)
            return app.request(path, **kwargs)

    @staticmethod
    def _json(response):
        return json.loads(response.data.decode("utf-8"))

    def post(self, token, payload):
        return self._request("/api/agents", method="POST", payload=payload,
                             token=token)

    def update(self, token, agent_id, **fields):
        payload = {"action": "update", "id": agent_id}
        payload.update(fields)
        return self.post(token, payload)

    def delete(self, token, agent_id):
        return self.post(token, {"action": "delete", "id": agent_id})

    def listing(self, token):
        response = self._request("/api/agents", token=token)
        assert response.status == "200 OK", response.data
        return self._json(response)

    def row(self, token, agent_id):
        for agent in self.listing(token).get("agents", []):
            if agent.get("id") == agent_id:
                return agent
        return None


# --- editing: same act, same rules, both owner roles ------------------------

class MaintenanceParityTests(_Base):
    def test_both_owner_roles_edit_their_own_object_the_same_way(self):
        for token, agent_id, new_name in (
                (self.alice_token, "alice-agent", "Alice Own"),
                (self.admin_token, "admin-agent", "Admin Own")):
            response = self.update(token, agent_id, name=new_name)

            self.assertEqual(response.status, "200 OK", response.data)
            body = self._json(response)
            self.assertEqual(body.get("status"), "success", body)
            self.assertEqual(body["result"]["name"], new_name)

    def test_the_owner_needs_no_resource_grant_for_their_own_object(self):
        """Ownership *is* the qualification (task 4.3).

        The default ``member`` role carries no ``agent:<id>`` row, so a success
        here is the ownership short-circuit answering — the same fact that makes
        the provisioned assistant usable and maintainable.
        """
        member_role = [r for r in self.svc.list_roles(self.tenant_id)
                       if r["code"] == "member"][0]
        self.assertEqual(
            [g for g in member_role["resource_grants"]
             if g["resource_kind"] == "agent"], [],
            "precondition: the member role holds no Agent grant")

        response = self.update(self.alice_token, "alice-agent",
                               description="mine")

        self.assertEqual(response.status, "200 OK", response.data)
        self.assertEqual(self._json(response).get("status"), "success")

    def test_another_members_private_object_stays_closed_to_an_admin(self):
        """Being an administrator is not a licence to edit somebody's private Agent."""
        response = self.update(self.admin_token, "alice-agent",
                               name="not mine to rename")

        self.assertEqual(response.status, "403 Forbidden", response.data)
        self.assertEqual(self._json(response)["code"], "forbidden")
        self.assertIsNotNone(self.binding("alice-agent"), (
            "a refused write leaves the object exactly as it was"))

    def test_a_third_member_cannot_edit_an_unrelated_private_object(self):
        response = self.update(self.bob_token, "alice-agent", name="bob was here")

        self.assertEqual(response.status, "403 Forbidden", response.data)
        self.assertIsNotNone(self.binding("alice-agent"))

    def test_both_owner_roles_see_the_same_fields_for_their_own_object(self):
        """Task 4.7 A: "same fields" is the projection, not a role branch.

        The console renders one form from this payload, so a key that appeared
        for an administrator and not for a member would be a second,
        differently-shaped path — exactly what design D1 forbids. The assertion
        is on the *shape*, since the two rows describe different objects.
        """
        alice_row = self.row(self.alice_token, "alice-agent")
        admin_row = self.row(self.admin_token, "admin-agent")

        self.assertIsNotNone(alice_row)
        self.assertIsNotNone(admin_row)
        self.assertEqual(set(alice_row), set(admin_row), (
            "the management projection must not change shape with the caller's"
            " role"))

    def test_the_every_object_predicate_is_ownership_first_for_both_roles(self):
        """Task 4.7 A: the one predicate both owner roles' paths consume.

        ``MaintenanceParityTests`` drives the same acts through the handler for a
        member and an administrator, which is the observable half. This pins the
        decision underneath, because "the same fields and the same predicates"
        is exactly the claim that a role branch would break: for a *private*
        object the answer may not change when ``is_admin`` flips, and for a
        *shared* one it may only change the ``manage`` half (a member may use the
        tenant's Agent, never rewrite it).
        """
        from auth.object_scope import MANAGE, USE, ObjectScope

        private = {"tenant_id": self.tenant_id,
                   "private_owner_user_id": self.alice}
        shared = {"tenant_id": self.tenant_id, "private_owner_user_id": None}

        for action in (MANAGE, USE):
            # The owner gets the same answer whatever rank they hold...
            for is_admin in (False, True):
                owner = ObjectScope(self.tenant_id, self.alice, is_admin=is_admin)
                self.assertTrue(owner.allows_agent(private, action=action), (
                    "the owner of a private object must pass, admin or not"))
                # ...and a non-owner is refused whatever rank they hold.
                other = ObjectScope(self.tenant_id, self.bob, is_admin=is_admin)
                self.assertFalse(other.allows_agent(private, action=action), (
                    "an administrator who is not the owner must be refused"
                    " exactly like any other non-owner"))

        member = ObjectScope(self.tenant_id, self.alice, is_admin=False)
        admin = ObjectScope(self.tenant_id, self.alice, is_admin=True)
        self.assertTrue(member.allows_agent(shared, action=USE))
        self.assertFalse(member.allows_agent(shared, action=MANAGE))
        self.assertTrue(admin.allows_agent(shared, action=MANAGE))

    def test_enable_toggle_is_symmetric_and_keeps_the_object_manageable(self):
        """A stopped object is still found and re-enabled — for either owner."""
        for token, agent_id in ((self.alice_token, "alice-agent"),
                                (self.admin_token, "admin-agent")):
            stopped = self.update(token, agent_id, enabled=False)
            self.assertEqual(stopped.status, "200 OK", stopped.data)

            row = self.row(token, agent_id)
            self.assertIsNotNone(row, (
                "the management list keeps disabled objects so they can be"
                " re-enabled from the same page"))
            self.assertFalse(row["enabled"])
            # Task 4.7 A: findable in the *management* list, but not advertised
            # as a chat entry point. The reason is the stable code the console
            # renders, and asserting it distinguishes this rule from the
            # unrelated permission gate (which would say ``permission_denied``).
            self.assertFalse(row["can_chat"], (
                "a stopped object is not chat-able for either owner role"))
            self.assertEqual(row["unavailable_reason"], "agent_disabled")

            started = self.update(token, agent_id, enabled=True)
            self.assertEqual(started.status, "200 OK", started.data)
            self.assertTrue(self.row(token, agent_id)["enabled"])


# --- provenance: the supplied assistant is not the owner's to erase ---------

class ProvenanceProtectionTests(_Base):
    def test_a_members_supplied_assistant_survives_their_delete(self):
        self.supplied_assistant("alice-assistant", self.alice)

        response = self.delete(self.alice_token, "alice-assistant")

        self.assertEqual(response.status, "403 Forbidden", response.data)
        self.assertEqual(self._json(response)["code"], "forbidden")
        self.assertIsNotNone(self.binding("alice-assistant"))
        self.assertIn("alice-assistant", self.roster_ids())

    def test_an_admins_supplied_assistant_is_protected_the_same_way(self):
        """Rank is not the predicate — provenance is."""
        self.supplied_assistant("admin-assistant", self.admin_id)

        response = self.delete(self.admin_token, "admin-assistant")

        self.assertEqual(response.status, "403 Forbidden", response.data)
        self.assertIsNotNone(self.binding("admin-assistant"))

    def test_an_unknown_origin_object_is_supplied_for_deletion_too(self):
        """Task 4.7 B: a row that predates the column counts as system-made.

        ``unknown`` is the origin every binding written before the column
        existed carries, and those were overwhelmingly the provisioner's own. So
        it must be refused rather than guessed at: guessing "user made it" hands
        the member a way to erase an assistant the tenant handed them, with no
        path back. The refusal is a real one — code, surviving binding, surviving
        roster row — not a silent no-op.
        """
        from auth.service import SUPPLIED_ASSISTANT_ORIGINS
        self.assertIn("unknown", SUPPLIED_ASSISTANT_ORIGINS)

        self.add_roster_entry("legacy-assistant")
        self._bind_private("legacy-assistant", self.alice, origin="unknown")

        response = self.delete(self.alice_token, "legacy-assistant")

        self.assertEqual(response.status, "403 Forbidden", response.data)
        self.assertEqual(self._json(response)["code"], "forbidden")
        self.assertIsNotNone(self.binding("legacy-assistant"))
        self.assertIn("legacy-assistant", self.roster_ids())

    def test_a_self_created_object_is_deletable_by_its_owner(self):
        """The protection is narrow: it is not a general freeze on deletion."""
        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "200 OK", response.data)
        self.assertEqual(self._json(response).get("status"), "success")
        self.assertIsNone(self.binding("alice-agent"))
        self.assertNotIn("alice-agent", self.roster_ids())


# --- reference: a live channel route is a conflict, not a rebind ------------

class ReferenceProtectionTests(_Base):
    def test_a_channel_route_refuses_the_delete_and_names_it(self):
        self.route_channel_to("alice-agent")

        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "409 Conflict", response.data)
        body = self._json(response)
        self.assertEqual(body["code"], "conflict")
        self.assertIn("feishu-ops", body["message"], (
            "the refusal names the dependency so the operator knows what to"
            " unlink"))
        self.assertTrue(body["conflicts"], "the reasons are machine-readable too")

    def test_the_refusal_leaves_both_stores_untouched(self):
        """No auto-stop, no auto-unbind, no fallback — the delete simply waits."""
        self.route_channel_to("alice-agent")

        self.delete(self.alice_token, "alice-agent")

        self.assertIsNotNone(self.binding("alice-agent"))
        self.assertIn("alice-agent", self.roster_ids())
        self.assertEqual(self.channel_bindings(), ["alice-agent"], (
            "the channel must still point where it pointed before"))

    def test_an_administrators_object_meets_the_same_conflict(self):
        self.route_channel_to("admin-agent", instance_id="feishu-admin")

        response = self.delete(self.admin_token, "admin-agent")

        self.assertEqual(response.status, "409 Conflict", response.data)
        self.assertIsNotNone(self.binding("admin-agent"))

    def test_unlinking_first_lets_the_same_delete_through(self):
        """The conflict is a "not yet", and the second attempt is the proof."""
        self.route_channel_to("alice-agent")
        first = self.delete(self.alice_token, "alice-agent")
        self.assertEqual(first.status, "409 Conflict", first.data)

        self.unlink_channels()
        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "200 OK", response.data)
        self.assertIsNone(self.binding("alice-agent"))

    def test_a_tenant_channel_row_refuses_the_delete_too(self):
        """Task 4.7 C: the identity-store half of the reference check.

        The roster's ``channel_instances`` is not the only place a live route
        lives; the business console writes per-tenant rows into
        ``tenant_channel_instances``. Both must be refusals, and the same
        request must pass once the row is gone — so the conflict is a "not yet"
        for this store as well.
        """
        self.route_tenant_channel_to("alice-agent")

        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "409 Conflict", response.data)
        body = self._json(response)
        self.assertEqual(body["code"], "conflict")
        self.assertTrue(any("Tenant Bot" in reason and "feishu" in reason
                            for reason in body["conflicts"]), body["conflicts"])
        self.assertIsNotNone(self.binding("alice-agent"))

        self.drop_tenant_channel("tenant-ops")
        response = self.delete(self.alice_token, "alice-agent")
        self.assertEqual(response.status, "200 OK", response.data)
        self.assertIsNone(self.binding("alice-agent"))

    def test_a_channel_routing_to_another_agent_does_not_block(self):
        self.route_channel_to("admin-agent")

        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "200 OK", response.data)

    def test_the_agent_service_refuses_the_same_delete_on_its_own(self):
        """The route gate is not the only line: the shared service refuses too.

        The console answers 409 before it reaches the roster service, but the
        platform API and the personal maintenance path call that service
        directly — so the rule has to hold there as well, or one of them becomes
        the way around the conflict.
        """
        from agent.admin import AgentInUseError, AgentAdminService

        self.route_channel_to("alice-agent")
        service = AgentAdminService(os.path.join(self.data_root, "config.json"))

        with self.assertRaises(AgentInUseError) as exc:
            service.delete_agent("alice-agent")

        self.assertEqual(exc.exception.code, "conflict")
        self.assertIn("feishu-ops", str(exc.exception))
        self.assertIn("alice-agent", self.roster_ids())

    def test_a_running_task_refuses_the_delete_and_names_it(self):
        """Task 4.7 C: the runtime half of the same conflict, at the route.

        ``test_private_agent_delete_conflicts`` pins the guard's fail-closed
        probe; this pins what the *console* sees when a task is live, and that
        the runtime dependency is a "not yet" too — clearing it lets the very
        same request through.
        """
        with patch("agent.deletion_guard.bridge_has_live_agent",
                   return_value=True):
            response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "409 Conflict", response.data)
        body = self._json(response)
        self.assertEqual(body["code"], "conflict")
        self.assertTrue(body["conflicts"], "the reasons are machine-readable")
        self.assertTrue(any("running" in reason for reason in body["conflicts"]),
                        body["conflicts"])
        self.assertIsNotNone(self.binding("alice-agent"))
        self.assertIn("alice-agent", self.roster_ids())

        # Nothing is running any more, and the same delete now passes.
        response = self.delete(self.alice_token, "alice-agent")
        self.assertEqual(response.status, "200 OK", response.data)

    def test_the_roster_file_survives_a_refused_delete(self):
        """A conflict is not resolved by rewriting the channel's binding away."""
        self.route_channel_to("alice-agent")

        self.delete(self.alice_token, "alice-agent")
        self.delete(self.admin_token, "alice-agent")

        self.assertEqual(self.channel_bindings(), ["alice-agent"])


# --- detachment: a passed delete leaves nothing resolving the object --------

class DetachmentTests(_Base):
    def test_the_delete_clears_the_binding_and_the_default_pointers(self):
        # Make the object the member's own preference first, so the cleanup has
        # a pointer to release rather than only a binding.
        chosen = self.post(self.alice_token,
                           {"action": "set_user_default", "id": "alice-agent"})
        self.assertEqual(chosen.status, "200 OK", chosen.data)
        self.assertEqual(self.stored_user_default(self.alice), "alice-agent")

        response = self.delete(self.alice_token, "alice-agent")

        self.assertEqual(response.status, "200 OK", response.data)
        self.assertIsNone(self.binding("alice-agent"))
        self.assertIsNone(self.stored_user_default(self.alice), (
            "a released preference is empty, not a dangling id"))
        self.assertNotEqual(
            self.svc.resolved_default_agent_id(self.tenant_id, self.alice),
            "alice-agent")

    def test_deleting_one_object_does_not_disturb_its_neighbours(self):
        kept = self.post(self.admin_token,
                         {"action": "set_user_default", "id": "admin-agent"})
        self.assertEqual(kept.status, "200 OK", kept.data)

        response = self.delete(self.alice_token, "alice-agent")
        self.assertEqual(response.status, "200 OK", response.data)

        self.assertIsNotNone(self.binding("admin-agent"))
        self.assertIsNotNone(self.binding("shared-agent"))
        self.assertEqual(self.stored_user_default(self.admin_id), "admin-agent")


# --- the instance template --------------------------------------------------

class InstanceTemplateTests(_Base):
    def test_the_instance_template_cannot_be_deleted(self):
        """The default Agent *is* the instance root; every other one is built on it.

        The object is bound to the tenant first, on purpose: an *unbound* Agent
        is refused earlier by the ordinary per-resource ``agent.edit`` gate, and
        that refusal also renders as ``status: "error"`` — so the original
        version of this test could pass without ever reaching the rule it names.
        The message assertion is what makes the difference observable.
        """
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="primary")

        response = self.delete(self.admin_token, "primary")

        body = self._json(response)
        self.assertEqual(body.get("status"), "error", response.data)
        self.assertIn("default agent cannot be deleted", body.get("message", ""))
        self.assertIn("primary", self.roster_ids())

    def test_the_instance_template_refusal_ends_when_it_stops_being_one(self):
        """Task 4.7 D: the template rule is a *role*, not a permanent freeze.

        The predicate is ``agent_id == registry.default_agent_id`` in
        ``AgentAdminService.delete_agent`` (``agent/admin.py``); nothing else in
        the repository blocks a delete because "something was cloned from this"
        — the clone-source mapping (``cloned_from_agent_id``) is provenance for
        idempotence, not a dependency. Appointing a different Agent as the
        instance default is what removes the dependency, so the previously
        protected object becomes deletable and the refusal above is recoverable
        rather than terminal.
        """
        from agent.admin import AgentAdminService

        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="primary")
        service = AgentAdminService(os.path.join(self.data_root, "config.json"))
        service.update_agent("shared-agent", make_default=True)
        self.assertEqual(service.snapshot()["default_agent_id"], "shared-agent")

        response = self.delete(self.admin_token, "primary")

        self.assertEqual(response.status, "200 OK", response.data)
        self.assertEqual(self._json(response).get("status"), "success")
        self.assertNotIn("primary", self.roster_ids())
