# encoding:utf-8
"""The member-facing surfaces behind the five personal console pages.

Change ``enable-member-personal-console`` gives members pages that read *their
own* objects. Two things must hold on the wire, and both are enforced in the
handler, not in the page:

* the payload contains only the caller's own objects, with per-object verbs
  derived from ownership facts (task 8.1/8.3) — never from a role name, and
  never widened because the caller can open the page;
* the personal resource surface writes *only* the caller's own parameters and
  never touches a tool definition, an MCP connection, a skill body, install
  state, or the tenant's grants (task 8.3).

These are unit tests over the projection/handler pairs; the request-level
transport (route policy, tenant selection) is covered by
``tests/test_personal_console_transport.py``.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from channel.web import web_channel
from channel.web.route_registry import ROUTES, derive_route_policy


def _ctx(tenant_id="t1", user_id="u1"):
    return SimpleNamespace(tenant_id=tenant_id, user_id=user_id)


class _FakeService:
    """Just enough of ``IdentityService`` for the projections under test."""

    def __init__(self, *, private=(), bindings=None, default=None):
        self._private = set(private)
        self._bindings = dict(bindings or {})
        self._default = default

    def private_agent_ids(self, tenant_id, user_id):
        return set(self._private)

    def get_agent_binding(self, agent_id):
        return self._bindings.get(agent_id)

    def member_default_agent_id(self, tenant_id, user_id):
        return self._default


def _profile(agent_id, **over):
    row = {"id": agent_id, "name": agent_id, "channel_instances": 0}
    row.update(over)
    return row


class PersonalAgentsProjectionTests(unittest.TestCase):
    """``/api/agents?view=personal`` — my Agents, my verbs."""

    def _project(self, service, rows):
        with patch("auth.service.get_identity_service", return_value=service), \
             patch.object(web_channel, "_tenant_agents_admin_projection",
                          return_value={"agents": rows,
                                        "default_agent_id": ""}):
            return web_channel._personal_agents_projection(_ctx())

    def test_only_my_agents_are_returned(self):
        service = _FakeService(
            private={"a-mine"},
            bindings={"a-mine": {"origin": "user_created"}})
        out = self._project(service, [_profile("a-mine"), _profile("a-other")])

        self.assertEqual([a["id"] for a in out["agents"]], ["a-mine"])
        self.assertEqual(out["scope"], "self")

    def test_an_agent_of_another_members_never_appears(self):
        """A tenant-wide read must not leak into the personal page."""
        service = _FakeService(private=set())
        out = self._project(service, [_profile("a-foreign")])
        self.assertEqual(out["agents"], [])

    def test_a_user_created_agent_offers_the_full_verb_set(self):
        service = _FakeService(
            private={"a-mine"},
            bindings={"a-mine": {"origin": "user_created"}})
        row = self._project(service, [_profile("a-mine")])["agents"][0]

        self.assertEqual(row["scope"], "private")
        self.assertFalse(row["is_system_assistant"])
        self.assertEqual(row["actions"],
                         {"edit": True, "enable": True, "delete": True,
                          "configure_personal": True})

    def test_the_system_assistant_is_not_deletable(self):
        """The provisioned assistant is not the member's to delete (task 4.5)."""
        service = _FakeService(
            private={"a-assistant"},
            bindings={"a-assistant": {"origin": "provisioned_assistant"}})
        row = self._project(service, [_profile("a-assistant")])["agents"][0]

        self.assertTrue(row["is_system_assistant"])
        self.assertFalse(row["actions"]["delete"])
        self.assertTrue(row["actions"]["edit"])

    def test_an_unknown_origin_is_treated_as_system_made(self):
        """Pre-column rows are not guessed into deletability."""
        service = _FakeService(private={"a-old"}, bindings={"a-old": {}})
        row = self._project(service, [_profile("a-old")])["agents"][0]

        self.assertEqual(row["origin"], "unknown")
        self.assertFalse(row["actions"]["delete"])

    def test_a_missing_binding_is_not_deletable(self):
        service = _FakeService(private={"a-ghost"}, bindings={})
        row = self._project(service, [_profile("a-ghost")])["agents"][0]

        self.assertFalse(row["actions"]["delete"])

    def test_the_default_agent_pointer_is_reported(self):
        service = _FakeService(private={"a-mine"}, bindings={}, default="a-mine")
        out = self._project(service, [_profile("a-mine")])
        self.assertEqual(out["default_agent_id"], "a-mine")

    def test_the_projection_does_not_expose_a_workspace_path(self):
        service = _FakeService(private={"a-mine"}, bindings={})
        row = self._project(service, [_profile("a-mine")])["agents"][0]
        self.assertNotIn("workspace", row)


class _WebHarness(unittest.TestCase):
    """Gives the handlers the response context ``web.header`` needs.

    Outside a real request ``web.ctx`` has no ``headers`` list, so any handler
    that sets a content type (or raises ``HTTPError``) blows up for a reason
    unrelated to the code under test. The transport test exercises the real
    request stack; here a minimal header sink is enough.
    """

    def setUp(self):
        self._had_headers = hasattr(web_channel.web.ctx, "headers")
        web_channel.web.ctx.headers = []
        self.addCleanup(self._restore_ctx)

    def _restore_ctx(self):
        if self._had_headers:
            web_channel.web.ctx.headers = []
        else:
            try:
                del web_channel.web.ctx.headers
            except (AttributeError, KeyError):
                pass

    def _scope(self, ctx):
        class _Scope:
            def __enter__(self_inner):
                return ctx

            def __exit__(self_inner, *exc):
                return False

        return _Scope()


class PersonalAgentsHandlerTests(_WebHarness):
    """``view=personal`` refuses rather than falling back to a tenant read."""

    def _get(self, ctx, params):
        class _Params(dict):
            def __getattr__(self, item):
                return self.get(item, "")

        with patch.object(web_channel.web, "input", return_value=_Params(params)), \
             patch.object(web_channel, "_require_read_permission"), \
             patch.object(web_channel, "_db_scope",
                          return_value=self._scope(ctx)):
            return json.loads(web_channel.AgentsHandler().GET())

    def test_view_personal_without_a_tenant_is_refused(self):
        out = self._get(_ctx(tenant_id=""), {"view": "personal"})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["code"], "no_tenant")

    def test_view_personal_without_a_context_is_refused(self):
        out = self._get(None, {"view": "personal"})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["code"], "no_tenant")

    def test_view_personal_with_a_tenant_returns_the_self_projection(self):
        service = _FakeService(private={"a-mine"}, bindings={})
        with patch("auth.service.get_identity_service", return_value=service), \
             patch.object(web_channel, "_tenant_agents_admin_projection",
                          return_value={"agents": [_profile("a-mine")],
                                        "default_agent_id": ""}):
            out = self._get(_ctx(), {"view": "personal"})

        self.assertEqual(out["status"], "success")
        self.assertEqual(out["scope"], "self")
        self.assertEqual([a["id"] for a in out["agents"]], ["a-mine"])


class _HandlerHarness(_WebHarness):
    """Drives the personal-parameter verbs on the shared 工具与技能 endpoints.

    The write no longer lives on a surface of its own: the member's parameters
    are edited inside the resource's detail component on the formal page, so the
    request it makes is the page's own endpoint (task 5.5). What is asserted here
    is unchanged from the retired handler — the owner is the session and nothing
    else, and no parameter of the request can reach another member's row or the
    shared definition.
    """

    def _post(self, handler_class, service, body, ctx=None):
        with patch.object(web_channel.web, "data",
                          return_value=json.dumps(body).encode("utf-8")), \
             patch.object(web_channel, "_db_scope",
                          return_value=self._scope(ctx or _ctx())), \
             patch.object(web_channel, "_require_catalog_read"), \
             patch.object(web_channel, "_require_read_permission"), \
             patch("auth.service.get_identity_service", return_value=service):
            return json.loads(handler_class().POST())


class PersonalResourceVerbTests(_HandlerHarness):
    """``POST /api/tools`` and ``POST /api/skills``: my own parameters only."""

    def test_saving_writes_only_the_callers_own_configuration(self):
        service = _RecordingService(saved={"resource_id": "builtin:echo",
                                           "params": {"timeout": 5}, "version": 1})
        out = self._post(web_channel.ToolsHandler, service,
                         {"action": "save-personal",
                          "resource_id": "builtin:echo",
                          "params": {"timeout": 5}})

        self.assertEqual(out["status"], "success")
        call = service.seen[0][1]
        self.assertEqual(call["actor_user_id"], "u1")
        self.assertEqual(call["tenant_id"], "t1")
        self.assertEqual(call["resource_kind"], "tool")
        self.assertEqual(call["params"], {"timeout": 5})

    def test_a_client_supplied_user_is_never_forwarded(self):
        """The owner is the session: a body field cannot name another member."""
        service = _RecordingService(saved={"resource_id": "builtin:echo"})
        self._post(web_channel.ToolsHandler, service,
                   {"action": "save-personal", "resource_id": "builtin:echo",
                    "params": {}, "user_id": "someone-else"})

        self.assertNotIn("user_id", service.seen[0][1])
        self.assertEqual(service.seen[0][1]["actor_user_id"], "u1")

    def test_a_secret_is_passed_through(self):
        service = _RecordingService(saved={"resource_id": "builtin:echo",
                                           "credential_id": "cred_1"})
        self._post(web_channel.ToolsHandler, service,
                   {"action": "save-personal", "resource_id": "builtin:echo",
                    "secret": "tok"})

        self.assertEqual(service.seen[0][1]["secret"], "tok")

    def test_a_skill_saves_under_the_resolved_authorization_object(self):
        """The grant is recorded as ``{source}:{name}``, so the write must be too."""
        service = _RecordingService(saved={"resource_id": "custom:tenant-note"})
        skill_service = _RecordingSkillService()

        with patch.object(web_channel, "_db_scope",
                          return_value=self._scope(_ctx())), \
             patch.object(web_channel, "_require_read_permission"), \
             patch.object(web_channel, "_skill_service",
                          return_value=skill_service), \
             patch.object(web_channel, "_personal_channel_service",
                          return_value=service), \
             patch.object(web_channel.web, "data",
                          return_value=json.dumps({
                              "action": "save-personal",
                              "name": "tenant-note",
                              "resource_id": "custom:tenant-note",
                              "params": {"lang": "zh"}}).encode("utf-8")):
            out = json.loads(web_channel.SkillsHandler().POST())

        self.assertEqual(out["status"], "success")
        call = service.seen[0][1]
        self.assertEqual(call["resource_kind"], "skill")
        self.assertEqual(call["resource_id"], "custom:tenant-note")
        self.assertEqual(call["actor_user_id"], "u1")

    def test_clearing_calls_the_owner_scoped_clear(self):
        service = _RecordingService(removed=True)
        out = self._post(web_channel.ToolsHandler, service,
                         {"action": "clear-personal",
                          "resource_id": "builtin:echo"})

        self.assertEqual(out["status"], "success")
        self.assertIsNone(out["config"])
        self.assertEqual(service.seen[0][0], "clear")
        self.assertEqual(service.seen[0][1]["actor_user_id"], "u1")

    def test_an_unknown_action_is_refused_without_touching_the_service(self):
        service = _RecordingService()
        out = self._post(web_channel.ToolsHandler, service,
                         {"action": "grant-everything",
                          "resource_id": "builtin:rm"})

        self.assertEqual(out["code"], "bad_request")
        self.assertEqual(service.seen, [])

    def test_a_service_refusal_is_rendered_with_its_code(self):
        from auth.service import IdentityServiceError

        service = _RecordingService(
            error=IdentityServiceError("resource is not available to this member",
                                       code="forbidden", status=403))
        with self.assertRaises(web_channel.web.HTTPError) as caught:
            self._post(web_channel.ToolsHandler, service,
                       {"action": "save-personal", "resource_id": "builtin:rm"})

        # web.py's HTTPError keeps the status in ``args[0]`` and the JSON body
        # on ``.data`` (it has no ``.status`` attribute in this version).
        self.assertEqual(caught.exception.args[0], "403 Forbidden")
        body = json.loads(caught.exception.data)
        self.assertEqual(body["code"], "forbidden")


class _RecordingSkillService:
    """The skill manager ``_resolved_skill`` resolves the object through.

    ``resolve`` returns the entry the write is keyed on, which is how the
    handler learns the ``{source}:{name}`` id the grant is recorded under.
    """

    def __init__(self, source="custom", name="tenant-note"):
        self.skill = SimpleNamespace(source=source, name=name)

    def resolve(self, resource_id=None, name=None):
        return SimpleNamespace(skill=self.skill)


class RetiredResourceSurfaceTests(unittest.TestCase):
    """The standalone personal resource surface is gone, its authority is not."""

    def test_the_standalone_route_is_no_longer_registered(self):
        paths = {entry.pattern for entry in ROUTES}
        self.assertNotIn("/api/personal/resources", paths,
                         "a retired surface must not come back as a second path "
                         "to the same configuration")
        self.assertNotIn("PersonalResourceHandler", vars(web_channel))

    def test_the_shared_endpoints_serve_the_personal_verbs(self):
        policy = derive_route_policy()
        for path in ("/api/tools", "/api/skills"):
            self.assertIn("POST", policy[path], path)
            self.assertEqual(policy[path]["POST"]["policy"], "tenant", path)
        self.assertNotIn("/api/personal/resources", policy)

    def test_the_public_tool_and_skill_surfaces_keep_their_own_gates(self):
        """Adding a personal verb must not relax a management one."""
        policy = derive_route_policy()
        self.assertEqual(policy["/api/tools"]["GET"]["policy"], "tenant")
        self.assertEqual(policy["/api/skills"]["GET"]["policy"], "tenant")
        self.assertEqual(policy["/api/skills"]["POST"]["policy"], "tenant")


class _RecordingService:
    def __init__(self, *, listing=(), saved=None, removed=False, error=None):
        self._listing = list(listing)
        self._saved = saved
        self._removed = removed
        self._error = error
        self.seen = []

    def list_personal_resource_configs(self, **kwargs):
        self.seen.append(("list", kwargs))
        return self._listing

    def save_personal_resource_config(self, **kwargs):
        self.seen.append(("save", kwargs))
        if self._error:
            raise self._error
        return self._saved

    def clear_personal_resource_config(self, **kwargs):
        self.seen.append(("clear", kwargs))
        return self._removed


if __name__ == "__main__":
    unittest.main()
