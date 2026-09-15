# encoding:utf-8
"""A member stands up their own private Agent (tasks 4.1 and 4.3).

The system already supplies every member one assistant (``agent.personal_assistant``).
What is missing is the *member's own* creation path: today only an administrator
can add an Agent, so a member who wants a second private object has to ask for
one — and any request that lets the client say "owner" is an ownership-forgery
hole by construction.

So the shape of the API **is** the control: there is no ``owner`` parameter and
no ``scope`` parameter. Tenant and owner come from the trusted caller context,
and a request that *tries* to specify either is refused rather than ignored,
because "ignored" would still mean the client believed it chose.

Task 4.3 is the counterpart: having one of each must not confuse the two. A
member who created their own private Agent is not thereby skipped by the
supplied-assistant provisioner, and neither flow may move the member's personal
default or the tenant's default.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError


class _DeleteRecorder:
    """Stands in for ``AgentAdminService``; records the roster operations."""

    def __init__(self):
        self.cloned = []
        self.deleted = []
        self.updated = []
        self.agents = []

    def snapshot(self):
        return {"agents": list(self.agents)}

    def clone_agent(self, source_agent_id, agent_id, name=None, workspace=None,
                    revision=None, knowledge_mode=None):
        self.cloned.append((source_agent_id, agent_id, workspace))
        self.agents.append({"id": agent_id, "name": name or agent_id,
                            "workspace": workspace, "enabled": True,
                            "description": "", "persona_summary": ""})
        return {"id": agent_id}

    def create_agent(self, agent_id, name, workspace=None, **fields):
        self.created = getattr(self, "created", [])
        self.created.append((agent_id, name, workspace))
        self.agents.append({"id": agent_id, "name": name, "workspace": workspace,
                            "enabled": True, "description": "",
                            "persona_summary": ""})
        return {"id": agent_id}

    def delete_agent(self, agent_id, revision=None):
        self.deleted.append(agent_id)
        self.agents = [a for a in self.agents if a["id"] != agent_id]
        return {"id": agent_id}

    def update_agent(self, agent_id, **fields):
        self.updated.append((agent_id, fields))
        return {"id": agent_id}


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = IdentityService(os.path.join(self.tmp, "identity.db"))
        boot = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=os.path.join(self.tmp, "acme"))
        self.tenant_id = boot["id"]
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.alice = self._member("alice")
        # A shared, member-usable template plus the members' own assistants.
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="template-agent")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="rock-assistant",
                            private_owner_user_id=self.alice,
                            origin="provisioned_assistant")
        self._grant(self.alice, "template-agent", ["use"])
        self.svc.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=self.alice,
            agent_id="rock-assistant")

    def _member(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant_id,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.tenant_id)["items"]
                if m["username"] == username][0]["user_id"]

    def _grant(self, user_id, resource_id, actions):
        """Hand ``user_id`` a scoped role grant on a *shared* resource."""
        import uuid

        role = self.svc.create_role(
            self.root["id"], self.tenant_id,
            "grant-%s" % uuid.uuid4().hex[:10], "scoped", ["agent.read"],
            resource_grants=[{"resource_kind": "agent",
                              "resource_id": resource_id, "action": action}
                             for action in actions])
        membership = self.svc._membership(user_id, self.tenant_id)
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                " VALUES(?,?,?)", (self.tenant_id, membership["id"], role["id"]))
            con.commit()

    def _service(self, recorder):
        from agent.private_agent import PrivateAgentService

        return PrivateAgentService(self.svc, admin_service=recorder)

    def _owned_bindings(self):
        """Bindings for alice, excluding the supplied assistant she started with."""
        return [b for b in self.svc.agents_for_tenant(self.tenant_id)
                if b.get("private_owner_user_id") == self.alice
                and b.get("origin") != "provisioned_assistant"]


class ServerFixedOwnershipTests(_Fixture):
    """4.1 — tenant and owner come from the context, never from the request."""

    def test_a_member_creates_a_private_agent_owned_by_themselves(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        result = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        binding = self.svc.get_agent_binding(result["agent_id"])
        self.assertEqual(binding["tenant_id"], self.tenant_id)
        self.assertEqual(binding["private_owner_user_id"], self.alice)
        self.assertEqual(binding["origin"], "user_created")

    def test_the_new_object_is_private_and_maintainable_by_its_owner(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        result = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        self.assertTrue(self.svc.check_resource_action(
            self.alice, self.tenant_id, "agent", result["agent_id"], "edit"))
        self.assertFalse(self.svc.check_resource_action(
            self.root["id"], self.tenant_id, "agent", result["agent_id"], "edit"))

    def test_the_service_exposes_no_owner_or_scope_parameter(self):
        """Guard against "fixing" forgery by validating a parameter that exists."""
        import inspect

        from agent.private_agent import PrivateAgentService

        params = set(inspect.signature(
            PrivateAgentService.create_private_agent).parameters)
        self.assertNotIn("owner_user_id", params)
        self.assertNotIn("scope", params)
        self.assertNotIn("private_owner_user_id", params)

    def test_a_request_that_names_an_owner_is_refused_not_ignored(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="伪造",
                requested_owner_user_id=self.root["id"])

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(recorder.cloned, [], "nothing may be created")

    def test_a_request_that_asks_for_a_shared_object_is_refused(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="共享",
                requested_scope="tenant")

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(recorder.cloned, [])

    def test_a_non_member_cannot_create(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id="usr_does_not_exist", tenant_id=self.tenant_id,
                name="越权")

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(recorder.cloned, [])

    def test_another_tenants_member_cannot_create_here(self):
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="gadmin", admin_display="G", admin_password="Str0ngPass9",
            recent_password="Str0ngAdminPass", shared_root=os.path.join(self.tmp, "globex"))
        other = [t for t in self.svc.list_tenants() if t["code"] == "globex"][0]["id"]
        outsider = [m for m in self.svc.list_members(other)["items"]
                    if m["username"] == "gadmin"][0]["user_id"]
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError):
            svc.create_private_agent(
                user_id=outsider, tenant_id=self.tenant_id, name="跨租户")

        self.assertEqual(recorder.cloned, [])


class TemplateSourceTests(_Fixture):
    """The template must be one the member may actually use."""

    def test_another_members_private_agent_is_not_a_template(self):
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="bob-assistant",
                            private_owner_user_id=self.root["id"],
                            origin="provisioned_assistant")
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="抄",
                source_agent_id="bob-assistant")

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(recorder.cloned, [])

    def test_an_unknown_template_is_refused(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="抄",
                source_agent_id="never-bound")

        self.assertEqual(exc.exception.code, "not_found")
        self.assertEqual(recorder.cloned, [])

    def test_a_shared_template_the_member_may_use_is_allowed(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        result = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="派生",
            source_agent_id="template-agent")

        self.assertEqual(recorder.cloned[0][0], "template-agent")
        self.assertEqual(
            self.svc.get_agent_binding(result["agent_id"])["private_owner_user_id"],
            self.alice)

    def test_creating_without_a_template_still_works(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        result = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="从零开始")

        self.assertEqual(recorder.cloned, [], "nothing to clone")
        self.assertIsNotNone(result["agent_id"])
        self.assertEqual(
            self.svc.get_agent_binding(result["agent_id"])["private_owner_user_id"],
            self.alice)


class DefaultsAreNotMovedTests(_Fixture):
    """4.3 — self-creation must not disturb either default registration."""

    def test_self_creation_leaves_the_personal_default_alone(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="第二个")

        self.assertEqual(
            self.svc.resolved_default_agent_id(self.tenant_id, self.alice),
            "rock-assistant")

    def test_self_creation_leaves_the_tenant_default_alone(self):
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="tenant-default")
        self.svc.set_tenant_default_agent(
            tenant_id=self.tenant_id, agent_id="tenant-default",
            actor_user_id=self.root["id"])
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="第三个")

        self.assertEqual(
            self.svc.tenant_default_agent_id(self.tenant_id), "tenant-default")

    def test_a_members_own_agent_does_not_block_the_supplied_assistant(self):
        """4.3: idempotency is by provenance, so the two flows coexist."""
        from agent.personal_assistant import PersonalAssistantProvisioner

        recorder = _DeleteRecorder()
        svc = self._service(recorder)
        svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="自建")

        # ``alice`` has only the supplied assistant, so a second provisioning
        # pass is still a no-op for her — but a member whose *only* private
        # Agent is self-made is not skipped just for owning one.
        bob = self._member("bob")
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="bob-own",
                            private_owner_user_id=bob, origin="user_created")
        provisioner = PersonalAssistantProvisioner(
            self.svc, admin_service=recorder)

        self.assertIsNone(
            provisioner.owned_agent_id(self.tenant_id, bob),
            "a self-made Agent is not the system's assistant")
        self.assertEqual(
            provisioner.owned_agent_id(self.tenant_id, self.alice),
            "rock-assistant")

    def test_the_supplied_assistant_is_still_not_deletable_by_the_member(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.delete_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id,
                agent_id="rock-assistant")

        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(recorder.deleted, [])


class IdempotentCreateTests(_Fixture):
    """A retry after a pre-bind crash adopts the orphan instead of duplicating."""

    def test_an_over_quota_request_does_no_roster_work(self):
        """The fail-fast check is not redundant: it prevents a wasted clone.

        The binding transaction is what *decides*, so removing this check is not a
        correctness hole — which is exactly why it needs its own test. Without it,
        an over-quota create would still clone an Agent and copy a workspace
        before being refused, and the member would pay for a template copy that
        could never be bound.
        """
        self.svc.set_private_agent_policy(
            actor_user_id=self.root["id"], tenant_id=self.tenant_id,
            member_agent_limit=2)  # rock-assistant already occupies one
        recorder = _DeleteRecorder()
        svc = self._service(recorder)
        svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="第一个")
        after_first = len(recorder.agents)

        with self.assertRaises(IdentityServiceError) as exc:
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="第二个",
                source_agent_id="template-agent")

        self.assertEqual(exc.exception.code, "quota_exceeded")
        self.assertEqual(len(recorder.agents), after_first,
                         "no clone may be started for an over-quota request")
        self.assertEqual(len(recorder.cloned), 0)

    def test_a_retry_adopts_the_orphaned_roster_entry(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)
        planned_id = svc.plan_agent_id(
            tenant_id=self.tenant_id, user_id=self.alice, name="我的助手")
        # Simulate: the roster entry and workspace were written, then the
        # process died before the binding.
        recorder.agents.append({
            "id": planned_id, "name": "我的助手",
            "workspace": os.path.join(self.svc.tenant_shared_root(self.tenant_id),
                                      "agents", planned_id),
            "enabled": True, "description": "", "persona_summary": ""})
        os.makedirs(os.path.join(self.svc.tenant_shared_root(self.tenant_id),
                                 "agents", planned_id), exist_ok=True)

        result = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        self.assertEqual(result["agent_id"], planned_id)
        self.assertEqual(recorder.cloned, [], "the orphan is adopted, not re-cloned")
        self.assertEqual(
            self.svc.get_agent_binding(planned_id)["private_owner_user_id"],
            self.alice)

    def test_a_repeated_create_after_success_is_a_new_object_not_a_merge(self):
        """Idempotency is retry-idempotency, not "same name means same object".

        Two objects may legitimately share a display name; silently returning the
        first one would make the second request a no-op the member never asked
        for. What must *not* happen is a third outcome — a half-merged object.
        """
        recorder = _DeleteRecorder()
        svc = self._service(recorder)

        first = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")
        second = svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        self.assertNotEqual(first["agent_id"], second["agent_id"])
        self.assertEqual(len(self._owned_bindings()), 2)
        for result in (first, second):
            self.assertEqual(
                self.svc.get_agent_binding(result["agent_id"])["origin"],
                "user_created")


class CompensationTests(_Fixture):
    """A failure mid-create must not leave a runnable half-object."""

    def test_a_bind_failure_removes_what_was_created(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)
        with patch.object(self.svc, "bind_private_agent_with_quota",
                          side_effect=IdentityServiceError("boom", code="error")):
            with self.assertRaises(IdentityServiceError):
                svc.create_private_agent(
                    user_id=self.alice, tenant_id=self.tenant_id, name="半成品")

        self.assertEqual(len(recorder.deleted), 1, "the roster entry is removed")
        self.assertEqual(self._owned_bindings(), [],
                         "no self-created object may remain bound")

    def test_a_clone_failure_creates_nothing(self):
        recorder = _DeleteRecorder()
        recorder.clone_agent = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("clone failed"))
        svc = self._service(recorder)

        with self.assertRaises(IdentityServiceError):
            svc.create_private_agent(
                user_id=self.alice, tenant_id=self.tenant_id, name="失败",
                source_agent_id="template-agent")

        self.assertEqual(self._owned_bindings(), [])

    def test_a_failed_create_is_not_retried_into_a_duplicate(self):
        recorder = _DeleteRecorder()
        svc = self._service(recorder)
        with patch.object(self.svc, "bind_private_agent_with_quota",
                          side_effect=IdentityServiceError("boom", code="error")):
            with self.assertRaises(IdentityServiceError):
                svc.create_private_agent(
                    user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        # The retry happens against the same name; the compensated roster entry
        # must not leave a second one behind.
        svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name="我的助手")

        mine = [b for b in self.svc.agents_for_tenant(self.tenant_id)
                if b.get("private_owner_user_id") == self.alice]
        self.assertEqual(len(mine), 2, "rock-assistant plus exactly one created")

if __name__ == "__main__":
    unittest.main()
