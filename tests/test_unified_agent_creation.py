# encoding:utf-8
"""Creating an Agent resolves ownership from the caller's role — task 4.1.

``agent-chat-launch`` (spec) is explicit:

    新建普通用户对象 SHALL 自动归本人私有，即使它是租户首个智能体也不变为共享或租户默认。
    管理员明确创建首个共享对象时可沿用共享默认初始化。

The behaviour this replaces bound *every* console-created Agent to its tenant as
ownerless, regardless of who created it. For an administrator that is right. For
a member it meant three wrong things at once: the object was visible to every
colleague, its binding was recorded as ``origin='unknown'`` (which reads as
"supplied by the organization", so the object could not be told apart from one
the provisioner handed out), and the tenant's first object was handed the
tenant-default appointment — which the service then refused, because appointing
is an administrative act.

These tests drive the real handler against a real ``IdentityService``, because
the property under test is *which store says what*: the roster entry, the
binding's owner and origin, and the tenant default pointer.
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
    """One tenant (acme) with an admin, a member and a platform admin."""

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

        self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username="acmeadmin", display_name="Tenant Admin",
            temporary_password="Str0ngTemp1", roles=["tenant_admin"])
        self.svc.change_password(
            self.svc.login("acmeadmin", "Str0ngTemp1").token,
            "Str0ngTemp1", "Str0ngTaFinal")
        self.admin_token = self.svc.login("acmeadmin", "Str0ngTaFinal").token

        # A plain member who owns nothing yet: the caller the spec's scenario is
        # written about ("普通成员创建该租户首个智能体").
        self.member_id = self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username="alice", display_name="Alice",
            temporary_password="Str0ngTemp2", roles=["member"])["user_id"]
        self.svc.change_password(
            self.svc.login("alice", "Str0ngTemp2").token,
            "Str0ngTemp2", "Str0ngAlice9")
        self.member_token = self.svc.login("alice", "Str0ngAlice9").token

        with open(os.path.join(self.data_root, "config.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"agent_workspace": self.instance}, handle)

    def _app(self):
        return web.application(
            ("/api/agents", "AgentsHandler"), vars(web_channel), autoreload=False)

    def _request(self, path, method="GET", payload=None, token=None, tenant=None):
        kwargs = {"method": method}
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"cow_session={token}"
        headers["X-Tenant-ID"] = tenant or self.tenant_id
        kwargs["headers"] = headers

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
            return self._app().request(path, **kwargs)

    @staticmethod
    def _json(resp):
        return json.loads(resp.data.decode("utf-8"))

    def create(self, token, agent_id, **payload):
        body = {"action": "create", "id": agent_id, "name": agent_id}
        body.update(payload)
        return self._request("/api/agents", method="POST", token=token,
                             payload=body)

    def binding(self, agent_id):
        return self.svc.get_agent_binding(agent_id)

    def roster_ids(self):
        path = os.path.join(self.instance, "agents", "team.json")
        try:
            with open(path, encoding="utf-8") as handle:
                return {a["id"] for a in json.load(handle)["agents"]}
        except FileNotFoundError:
            # No roster file yet, i.e. no create has ever succeeded here — the
            # legacy roster would be in config.json, which this harness writes
            # without an ``agents`` key.
            return set()

    def seed_roster(self, agent_id):
        """Put an entry in the roster without going through the create API.

        Used to model an object that reached the roster some other way (a
        provisioner, a legacy instance), which is exactly what the provenance
        rules have to cope with.
        """
        path = os.path.join(self.instance, "agents", "team.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as handle:
                roster = json.load(handle)
        except FileNotFoundError:
            roster = {"agents": []}
        roster.setdefault("agents", []).append({
            "id": agent_id, "name": agent_id,
            "workspace": os.path.join(self.instance, "agents", agent_id),
        })
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(roster, handle)


# --- a member's object is theirs and nobody else's --------------------------

class MemberCreationTests(_Base):
    def test_a_member_creates_a_private_object(self):
        resp = self.create(self.member_token, "alice-agent")

        self.assertEqual(resp.status, "200 OK", resp.data)
        body = self._json(resp)
        self.assertEqual(body.get("status"), "success", body)
        self.assertEqual(body["result"]["scope"], "private", (
            "the console is told which scope the object landed in, rather than"
            " having to infer it from the caller's role"))

        binding = self.binding("alice-agent")
        self.assertEqual(binding["private_owner_user_id"], self.member_id, (
            "a member-created object belongs to that member"))
        self.assertEqual(binding["origin"], "user_created", (
            "and its provenance says so, instead of the historical 'unknown'"
            " that reads as 'supplied by the organization'"))

    def test_the_first_object_stays_private_and_does_not_become_the_default(self):
        """The spec's scenario, verbatim: 即使它是租户首个智能体也不变为租户默认."""
        self.assertEqual(self.svc.tenant_agent_ids(self.tenant_id), [],
                         "precondition: the tenant starts empty")

        self.create(self.member_token, "alice-agent")

        self.assertIsNone(self.svc.tenant_default_agent_id(self.tenant_id), (
            "a member's first object must not become the entry every colleague"
            " shares"))
        self.assertEqual(
            self.svc.resolved_default_agent_id(self.tenant_id, self.member_id),
            "alice-agent", "it is still what *this* member's new sessions use")
        self.assertIsNone(
            self.svc.resolved_default_agent_id(self.tenant_id, None), (
                "with nothing shared, a subject-less caller has no anchor — and"
                " must not borrow one member's private workspace"))

    def test_a_member_may_not_create_a_tenant_shared_object(self):
        """Asking for the wrong scope is refused, not silently downgraded.

        A request that means "make this visible to every colleague" must not come
        back as a success carrying an object nobody else can see.
        """
        resp = self.create(self.member_token, "alice-agent", scope="shared")

        self.assertEqual(resp.status, "403 Forbidden", resp.data)
        self.assertEqual(self._json(resp).get("code"), "forbidden")
        self.assertIsNone(self.binding("alice-agent"), (
            "a refused scope must not leave a half-made object behind"))
        self.assertNotIn("alice-agent", self.roster_ids())

    def test_a_member_created_object_is_invisible_to_a_colleague(self):
        self.create(self.member_token, "alice-agent")
        other = self.svc.create_member(
            actor_user_id=self.platform_admin["id"], tenant_id=self.tenant_id,
            operation="create-new", username="bob", display_name="Bob",
            temporary_password="Str0ngTemp3", roles=["member"])["user_id"]
        self.svc.change_password(
            self.svc.login("bob", "Str0ngTemp3").token, "Str0ngTemp3", "Str0ngBob999")
        bob_token = self.svc.login("bob", "Str0ngBob999").token

        resp = self._request("/api/agents", token=bob_token)

        visible = {a["id"] for a in self._json(resp)["agents"]}
        self.assertNotIn("alice-agent", visible)
        self.assertEqual(self.svc.resolved_default_agent_id(self.tenant_id, other),
                         None, "and bob inherits no anchor from it")

    def test_the_owner_may_ask_for_the_private_scope_explicitly(self):
        """The default and the explicit request agree: no surprise downgrade."""
        body = self._json(self.create(self.member_token, "alice-agent",
                                      scope="private"))

        self.assertEqual(body["result"]["scope"], "private")
        self.assertEqual(self.binding("alice-agent")["private_owner_user_id"],
                         self.member_id)

    def test_an_unknown_scope_is_refused_before_any_write(self):
        resp = self.create(self.member_token, "alice-agent", scope="everyone")

        self.assertEqual(resp.status, "400 Bad Request", resp.data)
        self.assertEqual(self._json(resp).get("code"), "invalid_scope")
        self.assertIsNone(self.binding("alice-agent"))
        self.assertNotIn("alice-agent", self.roster_ids())


# --- an administrator keeps today's behaviour -------------------------------

class AdminCreationTests(_Base):
    def test_an_admin_still_creates_a_shared_object_by_default(self):
        resp = self.create(self.admin_token, "shared-agent")

        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["result"]["scope"], "shared")
        binding = self.binding("shared-agent")
        self.assertIsNone(binding["private_owner_user_id"], (
            "an administrator's object is the tenant's, not its maker's"))
        self.assertEqual(binding["origin"], "admin_created", (
            "recorded as a console creation, not as 'unknown' — the historical"
            " label for it said 'the system supplied this'"))

    def test_the_first_shared_object_still_initialises_the_tenant_default(self):
        self.create(self.admin_token, "shared-agent")

        self.assertEqual(self.svc.tenant_default_agent_id(self.tenant_id),
                         "shared-agent", (
            "a tenant that starts empty still ends up with something to chat"
            " with — for an administrator's first object"))

    def test_an_admin_may_explicitly_create_a_private_object(self):
        resp = self.create(self.admin_token, "admin-private", scope="private")

        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp)["result"]["scope"], "private")
        binding = self.binding("admin-private")
        self.assertEqual(binding["private_owner_user_id"],
                         self.svc.login("acmeadmin", "Str0ngTaFinal").user_id,
                         "the owner is the caller, never a field in the body")

    def test_an_admin_private_object_does_not_become_the_tenant_default(self):
        self.create(self.admin_token, "admin-private", scope="private")

        self.assertIsNone(self.svc.tenant_default_agent_id(self.tenant_id), (
            "a private object cannot be the tenant's shared entry"))


# --- the object is labelled, not guessed ------------------------------------

class ProvenanceAtBirthTests(_Base):
    def test_a_console_creation_is_never_recorded_as_unknown(self):
        """``unknown`` means "a row from before the column existed".

        Writing it for a *new* object destroyed the only evidence that could
        distinguish a console creation from a system-supplied assistant, and put
        every new object inside ``SUPPLIED_ASSISTANT_ORIGINS``.
        """
        self.create(self.admin_token, "shared-agent")
        self.create(self.member_token, "alice-agent")

        from auth.service import SUPPLIED_ASSISTANT_ORIGINS
        for agent_id in ("shared-agent", "alice-agent"):
            origin = self.binding(agent_id)["origin"]
            self.assertNotEqual(origin, "unknown")
            self.assertNotIn(origin, SUPPLIED_ASSISTANT_ORIGINS, (
                f"{agent_id} was created from the console, so it is nobody's"
                " supplied assistant"))

    def test_an_admin_created_shared_object_can_still_be_retired(self):
        """Provenance must not make a tenant's own object undeletable."""
        self.create(self.admin_token, "shared-agent")

        resp = self._request("/api/agents", method="POST", token=self.admin_token,
                             payload={"action": "delete", "id": "shared-agent"})

        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertIsNone(self.binding("shared-agent"))
        self.assertNotIn("shared-agent", self.roster_ids())

    def test_a_members_supplied_assistant_is_still_protected(self):
        """The protection the labelling exists for has not been loosened.

        A provisioned assistant is bound to its member as ``provisioned``, so it
        stays inside ``SUPPLIED_ASSISTANT_ORIGINS`` and its owner still cannot
        erase it — the console creation path writes ``user_created`` for the same
        person, and the two must not be confused.
        """
        self.svc.bind_agent(tenant_id=self.tenant_id, agent_id="supplied",
                            private_owner_user_id=self.member_id,
                            origin="provisioned_assistant")
        self.seed_roster("supplied")

        resp = self._request("/api/agents", method="POST", token=self.member_token,
                             payload={"action": "delete", "id": "supplied"})

        self.assertEqual(resp.status, "403 Forbidden", resp.data)
        self.assertIsNotNone(self.binding("supplied"))


# --- a failed adoption leaves nothing behind --------------------------------

class CompensationTests(_Base):
    def test_a_refused_binding_rolls_the_object_back(self):
        """The roster and the binding are two commits; the first must not survive
        the second's refusal.

        Reproduced with a withdrawn deployment capability, which is the realistic
        way the private branch refuses *after* the roster write: the cheap check
        cannot run before the object exists, because the object's own id is what
        the atomic bind re-checks.
        """
        from auth.service import IdentityServiceError

        original = IdentityService.bind_private_agent_with_quota

        def refuse(self, **_kwargs):
            raise IdentityServiceError("private agents are withdrawn",
                                       code="capability_disabled", status=403)

        with patch.object(IdentityService, "bind_private_agent_with_quota", refuse):
            resp = self.create(self.member_token, "alice-agent")

        self.assertEqual(resp.status, "200 OK", resp.data)
        body = self._json(resp)
        self.assertEqual(body.get("status"), "error", body)
        self.assertEqual(body.get("code"), "capability_disabled", (
            "the refusal reaches the caller as itself, not as a generic failure"))
        self.assertIsNone(self.binding("alice-agent"), (
            "a binding that was never written must not be reported as written"))
        self.assertNotIn("alice-agent", self.roster_ids(), (
            "and the object must not survive as an orphan nothing can remove"))
        self.assertFalse(
            os.path.exists(os.path.join(self.tenant_root, "agents", "alice-agent")),
            "nor may its workspace be left behind")
        self.assertTrue(callable(original))

    def test_the_same_id_can_be_retried_after_a_rollback(self):
        """Why the rollback matters: the id stays usable."""
        from auth.service import IdentityServiceError

        def refuse(self, **_kwargs):
            raise IdentityServiceError("private agents are withdrawn",
                                       code="capability_disabled", status=403)

        with patch.object(IdentityService, "bind_private_agent_with_quota", refuse):
            self.create(self.member_token, "alice-agent")

        resp = self.create(self.member_token, "alice-agent")

        self.assertEqual(resp.status, "200 OK", resp.data)
        self.assertEqual(self._json(resp).get("status"), "success")
        self.assertEqual(self.binding("alice-agent")["private_owner_user_id"],
                         self.member_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
