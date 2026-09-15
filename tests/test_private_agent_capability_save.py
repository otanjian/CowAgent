# encoding:utf-8
"""Saving an Agent config must not widen the owner's dependency authority (4.4).

Owning an Agent is authority over *that object*, not over the models, tools and
skills it is configured to use. "My Agent may use it" and "I may use it" are two
different questions, and a private Agent that silently granted the second would
be a privilege-escalation path: point your Agent at a model or tool you were
never authorised for, then run it through the object you do own.

The runtime already re-checks each tool call (``agent_stream._resource_tool_denial``)
and each model at send time. This gate is the *save* half of the same rule: the
request that writes the dependency is refused up front, with the resource named,
rather than accepting a configuration that could only ever fail at run time.

Only the fields the request actually names are judged. A private Agent cloned from
a template may legitimately carry assets the owner cannot use; re-validating an
untouched field on every unrelated save would make the object uneditable without
ever being a privilege it granted.
"""

import contextlib
import json
import os
import tempfile
import unittest
import uuid

from unittest.mock import patch

from auth.runtime import RequestContext
from auth.service import IdentityService
from channel.web import web_channel
import web


class _Fixture(unittest.TestCase):
    def setUp(self):
        web.ctx.headers = []
        web.ctx.status = "200 OK"
        self.svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
        boot = self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.ta = boot["id"]
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.alice = self._member("alice")
        self.bob = self._member("bob")
        self.svc.bind_agent(tenant_id=self.ta, agent_id="alice-agent",
                            private_owner_user_id=self.alice,
                            origin="user_created")
        self.svc.bind_agent(tenant_id=self.ta, agent_id="bob-agent",
                            private_owner_user_id=self.bob,
                            origin="user_created")
        self.svc.bind_agent(tenant_id=self.ta, agent_id="shared-agent")
        self.patcher = patch("auth.service.get_identity_service",
                             return_value=self.svc)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _member(self, username):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                operation="create-new", username=username, display_name=username,
                temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]

    def _grant(self, user_id, kind, resource_id, action):
        """Hand ``user_id`` a scoped role grant on one resource."""
        role = self.svc.create_role(
            self.root["id"], self.ta,
            "grant-%s" % uuid.uuid4().hex[:10], "scoped", ["agent.read"],
            resource_grants=[{"resource_kind": kind, "resource_id": resource_id,
                              "action": action}])
        membership = self.svc._membership(user_id, self.ta)
        with self.svc._tx() as con:
            con.execute(
                "INSERT INTO membership_roles(tenant_id, membership_id, role_id)"
                " VALUES(?,?,?)", (self.ta, membership["id"], role["id"]))
            con.commit()

    def _ctx(self, user_id, *, platform_admin=False, tenant_admin=False):
        perms = set(self.svc.permissions_for(user_id, self.ta))
        return RequestContext(
            user_id=user_id, username="u", display_name="U",
            is_platform_admin=platform_admin, must_change_password=False,
            tenant_id=self.ta, membership=None, permissions=perms,
            is_tenant_admin=tenant_admin)

    def _refusal(self, ctx, agent_id, body):
        with self.assertRaises(web_channel.web.HTTPError) as exc:
            web_channel._require_configured_capabilities(ctx, agent_id, body)
        return exc.exception

    def _refusal_message(self, ctx, agent_id, body):
        return self._refusal(ctx, agent_id, body).data


class ToolDependencyTests(_Fixture):
    def test_a_tool_the_owner_was_granted_passes(self):
        self._grant(self.alice, "tool", "builtin:search", "execute")
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"tools_allowlist": ["search"]})

    def test_a_tool_the_owner_was_not_granted_is_refused(self):
        self._grant(self.alice, "tool", "builtin:search", "execute")
        message = self._refusal_message(
            self._ctx(self.alice), "alice-agent",
            {"tools_allowlist": ["search", "danger"]})
        self.assertIn("danger", message)

    def test_an_mcp_tool_granted_by_its_connection_passes(self):
        self._grant(self.alice, "tool", "mcp:crm:lookup", "execute")
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"tools_allowlist": ["lookup"]})

    def test_an_untouched_field_is_not_re_validated(self):
        """The stored allowlist may carry assets the owner cannot use; saving
        an unrelated field must not turn that pre-existing state into a refusal."""
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"name": "renamed"})


class SkillDependencyTests(_Fixture):
    def test_a_skill_the_owner_was_granted_passes(self):
        self._grant(self.alice, "skill", "builtin:writer", "use")
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"skills": ["builtin:writer"]})

    def test_a_skill_the_owner_was_not_granted_is_refused(self):
        self._grant(self.alice, "skill", "builtin:writer", "use")
        message = self._refusal_message(
            self._ctx(self.alice), "alice-agent",
            {"skills": ["builtin:writer", "builtin:admin"]})
        self.assertIn("admin", message)

    def test_a_bare_skill_name_is_matched_against_the_namespaced_grant(self):
        self._grant(self.alice, "skill", "builtin:writer", "use")
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"skills": ["writer"]})


class ModelDependencyTests(_Fixture):
    def test_a_model_the_owner_was_granted_passes(self):
        self._grant(self.alice, "model", "provider:openai:gpt-x", "use")
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "alice-agent", {"model": "gpt-x"})

    def test_a_model_the_owner_was_not_granted_is_refused(self):
        self._grant(self.alice, "model", "provider:openai:gpt-x", "use")
        message = self._refusal_message(
            self._ctx(self.alice), "alice-agent", {"model": "secret-model"})
        self.assertIn("secret-model", message)


class HandlerWiringTests(_Fixture):
    """The gate must be *called* by the save path, not merely exist (4.4).

    A helper with a test of its own proves the policy; it does not prove the
    console's save endpoint consults it. This drives ``AgentsHandler.POST`` with
    a fake roster so a refused save can be shown to leave the roster untouched.
    """

    def setUp(self):
        super().setUp()
        self.updated = []
        recorder = self

        class _FakeAdmin:
            def update_agent(self, agent_id, **fields):
                recorder.updated.append((agent_id, fields))
                return {"id": agent_id}

            def snapshot(self):
                return {"revision": "rev-1"}

        self.fake_admin = _FakeAdmin()
        self._patches = [
            patch.object(web_channel, "_db_scope", self._scope),
            patch.object(web_channel, "_agent_admin_service",
                         lambda: self.fake_admin),
            patch.object(web_channel, "_reload_agent_runtime", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    @contextlib.contextmanager
    def _scope(self):
        yield self._ctx(self.alice)

    def _post(self, payload):
        with patch.object(web_channel.web, "data",
                          lambda: json.dumps(payload).encode()):
            return json.loads(web_channel.AgentsHandler().POST())

    def test_a_save_naming_an_unauthorized_tool_is_refused_and_writes_nothing(self):
        with self.assertRaises(web_channel.web.HTTPError) as exc:
            self._post({"action": "update", "id": "alice-agent",
                        "tools_allowlist": ["danger"]})
        self.assertIn("danger", exc.exception.data)
        self.assertEqual(self.updated, [], "a refused save must not reach the roster")

    def test_a_save_naming_an_authorized_tool_reaches_the_roster(self):
        self._grant(self.alice, "tool", "builtin:search", "execute")
        body = self._post({"action": "update", "id": "alice-agent",
                           "tools_allowlist": ["search"]})
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(len(self.updated), 1)
        self.assertEqual(self.updated[0][1].get("tools_allowlist"), ["search"])


class OwnerMaintenanceTests(_Fixture):
    """The owner's own config/core-file/enable path reaches the roster (4.4).

    Ownership carries ``edit``/``enable`` on the object (tasks 3.1/3.2); these
    drive the endpoints the personal console will call, so "can maintain my own
    Agent" is pinned at the handler and not only in the service.
    """

    def setUp(self):
        super().setUp()
        self.updated = []
        self.written = []
        recorder = self

        class _FakeAdmin:
            def update_agent(self, agent_id, **fields):
                recorder.updated.append((agent_id, fields))
                return {"id": agent_id}

            def write_core_file(self, agent_id, filename, content, revision):
                recorder.written.append((agent_id, filename, content))
                return {"filename": filename, "revision": "r2"}

            def snapshot(self):
                return {"revision": "rev-1"}

        self.fake_admin = _FakeAdmin()
        for p in (
            patch.object(web_channel, "_db_scope", self._scope),
            patch.object(web_channel, "_agent_admin_service",
                         lambda: self.fake_admin),
            patch.object(web_channel, "_reload_agent_runtime", lambda *a, **k: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    @contextlib.contextmanager
    def _scope(self):
        yield self._ctx(self.alice)

    def _post(self, payload):
        with patch.object(web_channel.web, "data",
                          lambda: json.dumps(payload).encode()):
            return json.loads(web_channel.AgentsHandler().POST())

    def test_the_owner_can_disable_its_own_private_agent(self):
        body = self._post({"action": "update", "id": "alice-agent",
                           "enabled": False})
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(self.updated[0][1].get("enabled"), False)

    def test_the_owner_can_write_a_core_file(self):
        with patch.object(web_channel.web, "data",
                          lambda: json.dumps({"content": "hi",
                                              "revision": "r1"}).encode()):
            body = json.loads(
                web_channel.AgentCoreFileHandler().PUT("alice-agent", "AGENT.md"))
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(self.written, [("alice-agent", "AGENT.md", "hi")])

    def test_an_administrator_cannot_write_the_core_file(self):
        ctx = self._ctx(self.root["id"], platform_admin=True)
        with patch.object(web_channel, "_db_scope", lambda: contextlib.nullcontext(ctx)), \
                patch.object(web_channel.web, "data",
                             lambda: json.dumps({"content": "hijack",
                                                 "revision": "r1"}).encode()):
            with self.assertRaises(web_channel.web.HTTPError) as exc:
                web_channel.AgentCoreFileHandler().PUT("alice-agent", "AGENT.md")
        self.assertEqual(exc.exception.args[0], "403 Forbidden")
        self.assertEqual(self.written, [])


class BoundaryTests(_Fixture):
    def test_another_members_agent_is_refused_before_any_dependency_question(self):
        exc = self._refusal(self._ctx(self.alice), "bob-agent",
                            {"tools_allowlist": ["search"]})
        self.assertEqual(exc.args[0], "403 Forbidden")

    def test_a_tenant_admin_is_unrestricted(self):
        web_channel._require_configured_capabilities(
            self._ctx(self.root["id"], tenant_admin=True), "alice-agent",
            {"tools_allowlist": ["anything"], "skills": ["whatever"],
             "model": "unheard-of"})

    def test_a_platform_admin_is_unrestricted(self):
        web_channel._require_configured_capabilities(
            self._ctx(self.root["id"], platform_admin=True), "alice-agent",
            {"tools_allowlist": ["anything"]})

    def test_legacy_mode_has_no_grants_to_check(self):
        web_channel._require_configured_capabilities(
            None, "alice-agent", {"tools_allowlist": ["anything"]})

    def test_a_shared_agent_is_left_to_its_existing_agent_edit_grant(self):
        """No private ownership, so this gate does not add a second answer;
        the per-resource ``agent.edit`` grant is the authority that was asked."""
        web_channel._require_configured_capabilities(
            self._ctx(self.alice), "shared-agent", {"tools_allowlist": ["search"]})


if __name__ == "__main__":
    unittest.main()
