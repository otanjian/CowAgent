# encoding:utf-8
"""Deleting a private Agent must respect what still depends on it (task 4.5).

Two records can make a delete the wrong thing to do at that moment: a channel
instance that still routes inbound traffic to the Agent, and a live runtime
instance a task is still using. Neither is a permission question — the owner is
allowed — so the answer is a *conflict* that names the dependency and tells the
member to deal with it first, not a silent success that pulls the ground from
under a running task or leaves a channel pointing at nothing.

The delete also has to actually detach the object. Removing the roster entry
alone leaves the ``agent_bindings`` row behind, and the read path deliberately
treats an Agent the registry does not know as usable — so a surviving binding
would keep resolving the deleted Agent as if it still existed. The binding, the
tenant default and the member's personal default all have to be released.
"""

import os
import tempfile
import unittest

from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError


class _Roster:
    """Stands in for ``AgentAdminService``; records roster operations."""

    def __init__(self):
        self.deleted = []

    def snapshot(self):
        return {"agents": []}

    def clone_agent(self, source_agent_id, agent_id, name=None, workspace=None,
                    revision=None, knowledge_mode=None):
        return {"id": agent_id}

    def create_agent(self, agent_id, name, workspace=None, **fields):
        return {"id": agent_id}

    def delete_agent(self, agent_id, revision=None):
        self.deleted.append(agent_id)
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
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="rock-assistant",
                            private_owner_user_id=self.alice,
                            origin="provisioned_assistant")
        self.roster = _Roster()
        self.created = self._create(name="我的助手")

    def _member(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant_id,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.tenant_id)["items"]
                if m["username"] == username][0]["user_id"]

    def _create(self, name):
        from agent.private_agent import PrivateAgentService

        svc = PrivateAgentService(self.svc, admin_service=self.roster)
        return svc.create_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, name=name)["agent_id"]

    def _service(self, runtime_probe=None):
        from agent.private_agent import PrivateAgentService

        return PrivateAgentService(self.svc, admin_service=self.roster,
                                   runtime_probe=runtime_probe)

    def _link_channel(self, agent_id, *, instance_id="chan1",
                      channel_type="feishu", display_name="Alice Bot"):
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
                " display_name, agent_id, scope, owner_user_id, created_by)"
                " VALUES(?,?,?,?,?,'user',?,?)",
                (instance_id, self.tenant_id, channel_type, display_name,
                 agent_id, self.alice, self.alice))
            con.commit()

    def _delete(self, agent_id, runtime_probe=None):
        return self._service(runtime_probe).delete_private_agent(
            user_id=self.alice, tenant_id=self.tenant_id, agent_id=agent_id)


class ChannelReferenceConflictTests(_Fixture):
    def test_a_channel_instance_reference_blocks_the_delete(self):
        self._link_channel(self.created)
        with self.assertRaises(IdentityServiceError) as exc:
            self._delete(self.created)
        self.assertEqual(exc.exception.code, "conflict")
        self.assertEqual(exc.exception.status, 409)
        self.assertEqual(self.roster.deleted, [],
                         "a conflicted delete must not touch the roster")
        self.assertIn("feishu", str(exc.exception))

    def test_the_conflict_names_the_referencing_instance(self):
        self._link_channel(self.created, display_name="Alice's Feishu")
        with self.assertRaises(IdentityServiceError) as exc:
            self._delete(self.created)
        self.assertIn("Alice's Feishu", str(exc.exception))

    def test_removing_the_reference_lets_the_delete_through(self):
        self._link_channel(self.created)
        with self.svc._tx() as con:
            con.execute("DELETE FROM tenant_channel_instances WHERE id='chan1'")
            con.commit()
        result = self._delete(self.created)
        self.assertEqual(result["status"], "deleted")
        self.assertEqual(self.roster.deleted, [self.created])

    def test_an_unreadable_channel_store_fails_closed(self):
        """"I could not check" must not be read as "nothing depends on it"."""
        with patch.object(self.svc, "channel_instances_referencing_agent",
                          side_effect=RuntimeError("store unavailable")):
            with self.assertRaises(IdentityServiceError) as exc:
                self._delete(self.created)
        self.assertEqual(exc.exception.code, "conflict")
        self.assertEqual(self.roster.deleted, [])

    def test_another_agents_channel_reference_does_not_block(self):
        self._link_channel("someone-elses-agent")
        result = self._delete(self.created)
        self.assertEqual(result["status"], "deleted")

    def test_a_stopped_channel_reference_does_not_block(self):
        """Only a live route is a reason to wait; a disabled instance is not."""
        self._link_channel(self.created)
        with self.svc._tx() as con:
            con.execute(
                "UPDATE tenant_channel_instances SET active=0 WHERE id='chan1'")
            con.commit()
        result = self._delete(self.created)
        self.assertEqual(result["status"], "deleted")


class RuntimeConflictTests(_Fixture):
    def test_a_live_runtime_blocks_the_delete(self):
        with self.assertRaises(IdentityServiceError) as exc:
            self._delete(self.created, runtime_probe=lambda agent_id: True)
        self.assertEqual(exc.exception.code, "conflict")
        self.assertEqual(self.roster.deleted, [])

    def test_a_stopped_runtime_does_not_block(self):
        result = self._delete(self.created, runtime_probe=lambda agent_id: False)
        self.assertEqual(result["status"], "deleted")

    def test_a_probe_failure_fails_closed(self):
        """An unanswerable "is it running?" must not be read as "no"."""
        def _boom(agent_id):
            raise RuntimeError("bridge unavailable")

        with self.assertRaises(IdentityServiceError) as exc:
            self._delete(self.created, runtime_probe=_boom)
        self.assertEqual(exc.exception.code, "conflict")
        self.assertEqual(self.roster.deleted, [])


class DetachmentTests(_Fixture):
    def test_the_delete_releases_the_agents_binding(self):
        """The roster entry is not the whole object; a surviving binding would
        keep resolving the deleted Agent on the read path."""
        self._delete(self.created)
        self.assertIsNone(self.svc.get_agent_binding(self.created))
        self.assertNotIn(self.created, self.svc.private_agent_ids(
            self.tenant_id, self.alice))

    def test_the_delete_clears_a_personal_default_that_names_it(self):
        self.svc.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=self.alice, agent_id=self.created)
        self._delete(self.created)
        self.assertNotEqual(
            self.svc.resolved_default_agent_id(self.tenant_id, self.alice),
            self.created)


class ProvenancePrecedesConflictTests(_Fixture):
    def test_a_supplied_assistant_is_refused_before_any_conflict_check(self):
        """Provenance is the first question; a dependency on the *system's*
        assistant must not turn the refusal into a conflict."""
        self._link_channel("rock-assistant")
        with self.assertRaises(IdentityServiceError) as exc:
            self._delete("rock-assistant", runtime_probe=lambda agent_id: True)
        self.assertEqual(exc.exception.code, "forbidden")
        self.assertEqual(self.roster.deleted, [])


if __name__ == "__main__":
    unittest.main()
