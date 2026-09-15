# encoding:utf-8
"""Small shared helpers for web.py route tests.

Keeps cookie extraction in one place: since Web login returns no token in the
JSON body (only the HttpOnly session Cookie), tests that need a session token to
send subsequent requests read it from the ``Set-Cookie`` response header.
"""

import json
import os
import re
from pathlib import Path


def cookie_value(response, name):
    """Return the value of the cookie ``name`` from a response's Set-Cookie.

    ``response`` is a ``web.storage`` as returned by ``app.request(...)``; it
    exposes ``header_items`` (list of ``(name, value)``). Returns "" when absent.
    """
    items = getattr(response, "header_items", None) or []
    for header_name, value in items:
        if header_name != "Set-Cookie":
            continue
        for part in value.split("; "):
            if part.startswith(name + "="):
                return part[len(name) + 1:]
    return ""


def has_cookie(response, name):
    """True when the response sets a cookie named ``name``."""
    return bool(cookie_value(response, name))


class IdentityStack:
    """A real identity database with the actors a scheduler test needs.

    Built from the same service calls the CLI makes rather than by hand-inserting
    rows: the scheduler's authorization reads memberships, roles, resource grants
    and bindings through the service, so a fixture that poked rows directly would
    let a broken query pass. See ``tests/test_scheduler_identity_revalidation.py``
    for the usage pattern this generalises.
    """

    ROOT_PASSWORD = "Str0ngAdminPass"
    MEMBER_PASSWORD = "MemberPass123!"
    TEMP_PASSWORD = "TempPass123!"

    def __init__(self, service, tenant_id, root_user_id, shared_root):
        self.service = service
        self.tenant_id = tenant_id
        self.root = root_user_id
        self.shared_root = shared_root
        self.members = {}

    # -- population --------------------------------------------------------

    def agent_role(self, code, agents, *, permissions=None):
        """A role carrying ``chat.use`` + ``agent.use`` on the listed Agents."""
        permissions = list(permissions or ("chat.use", "agent.use", "agent.read"))
        return self.service.create_role(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            code=code, name=code.title(), permissions=permissions,
            resource_grants=[
                {"resource_kind": "agent", "resource_id": f"agent:{agent}",
                 "action": "use"} for agent in agents
            ],
        )

    def member(self, username, role_codes, *, password=None):
        """Create a member who already completed the first password change."""
        created = self.service.create_member(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password=self.TEMP_PASSWORD,
            roles=list(role_codes),
        )
        token = self.service.login(username, self.TEMP_PASSWORD).token
        self.service.change_password(token, self.TEMP_PASSWORD,
                                     password or self.MEMBER_PASSWORD)
        self.members[username] = created["user_id"]
        return created["user_id"]

    def tenant_admin(self, username="tenant-admin"):
        """A tenant administrator: manages public tasks, not private ones."""
        created = self.service.create_member(
            actor_user_id=self.root, tenant_id=self.tenant_id,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password=self.TEMP_PASSWORD,
            roles=["tenant_admin"],
        )
        token = self.service.login(username, self.TEMP_PASSWORD).token
        self.service.change_password(token, self.TEMP_PASSWORD,
                                     self.MEMBER_PASSWORD)
        self.members[username] = created["user_id"]
        return created["user_id"]

    def other_tenant(self, code="other", *, agent="other-agent",
                     username="foreign"):
        """A second tenant with one ready member, for cross-tenant tests."""
        other = self.service.create_tenant(
            actor_user_id=self.root, code=code, name=code.title(),
            shared_root="", admin_username=f"{code}-root",
            admin_display=f"{code} Root", admin_password=self.ROOT_PASSWORD,
            recent_password=self.ROOT_PASSWORD,
        )["id"]
        self.service.bind_agent(tenant_id=other, agent_id=agent)
        role = self.service.create_role(
            actor_user_id=self.root, tenant_id=other, code=f"{code}-role",
            name=f"{code} role", permissions=["chat.use", "agent.use"],
            resource_grants=[{"resource_kind": "agent",
                              "resource_id": f"agent:{agent}", "action": "use"}],
        )
        user = self.service.create_member(
            actor_user_id=self.root, tenant_id=other, operation="create-new",
            username=username, display_name=username.title(),
            temporary_password=self.TEMP_PASSWORD, roles=[role["code"]],
        )["user_id"]
        token = self.service.login(username, self.TEMP_PASSWORD).token
        self.service.change_password(token, self.TEMP_PASSWORD,
                                     self.MEMBER_PASSWORD)
        self.members[username] = user
        return {"tenant_id": other, "user_id": user, "agent_id": agent}


def build_identity(path, *, agents=("agent-a",), tenant_code="acme"):
    """Bootstrap a tenant with an identity database at ``path``.

    Split from :func:`bootstrap_identity` so a ``unittest.TestCase`` (which has
    no ``monkeypatch`` fixture) can build the same real stack and patch
    ``get_identity_service`` with :func:`unittest.mock.patch` itself.
    """
    from auth.service import IdentityService

    root_dir = Path(path)
    shared = root_dir / "shared"
    service = IdentityService(str(root_dir / "identity.db"))
    tenant = service.bootstrap(
        tenant_code=tenant_code, tenant_name=tenant_code.title(),
        admin_username="root", admin_display="Root",
        admin_password=IdentityStack.ROOT_PASSWORD,
        shared_root=str(shared), allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    for agent in agents:
        service.bind_agent(tenant_id=tenant, agent_id=agent)
    return IdentityStack(service, tenant, root, str(shared))


def bootstrap_identity(tmp_path, monkeypatch, *, agents=("agent-a",),
                       tenant_code="acme"):
    """Bootstrap a tenant with a ready member; return an :class:`IdentityStack`.

    ``get_identity_service`` is patched to this database for the duration of the
    test, which is what makes the ambient-identity tool path transparently use
    it — the same wiring the application has in database identity mode.
    """
    stack = build_identity(tmp_path, agents=agents, tenant_code=tenant_code)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: stack.service)
    return stack


class WebAppHarness:
    """A real ``build_web_app()`` over a private identity database.

    Handler-level tests with a patched ``web`` module prove the handler's own
    logic but say nothing about who is allowed to reach it; once a surface is
    guarded by the route policy and by an owner check, that question can only be
    answered by driving the actual WSGI app. This harness provides the legal
    identity fixture — a bootstrapped tenant, members with roles and grants, and
    a session cookie from a real login — so a test can assert both the happy path
    and the refusals on the wire.

    Usage (pytest)::

        web = WebAppHarness(tmp_path)
        web.add_agent("shared-agent")
        web.member("alice", ["member"], grants=...)
        response = web.post("/api/scheduler/delete", {"task_id": "t1"},
                            token=web.login("alice"))
    """

    BASE = "http://localhost:9899"
    ADMIN_PASSWORD = IdentityStack.ROOT_PASSWORD
    ADMIN_FINAL = "Str0ngRootFinal"
    HOST = "localhost:9899"

    def __init__(self, root, *, tenant_code="acme", settings=None,
                 stack_factory=None):
        from unittest.mock import patch

        from auth.service import IdentityService
        import config as config_module

        self.root = str(root)
        os.makedirs(self.root, exist_ok=True)
        self.db_path = os.path.join(self.root, "identity.db")
        # The deployment layout the console expects: tenants are siblings under
        # ``<instance root>/tenants/<code>``, so a second tenant can be created
        # without its root overlapping the first one's.
        self.shared_root = os.path.join(self.root, "tenants", tenant_code)
        self.service = IdentityService(self.db_path)
        self.stack = (stack_factory or _bootstrap_tenant)(
            self.service, self.shared_root, tenant_code=tenant_code)
        self.tenant_id = self.stack.tenant_id
        self.admin_id = self.stack.root
        self._agents = []
        self._passwords = {}
        self._ids = {}
        self._patchers = []

        token = self.service.login("root", self.ADMIN_PASSWORD).token
        self._passwords["root"] = self.ADMIN_PASSWORD
        self._ids["root"] = self.admin_id

        # Start from the settings the suite already resolved (the session fixture
        # redirects ``agent_workspace`` out of the developer's real workspace) and
        # override only what this tenant owns, so the app is built from a complete
        # configuration rather than the three keys a scheduler test happens to
        # care about.
        base = dict(config_module.conf())
        base.update({
            "identity_mode": "database",
            "identity_db_path": self.db_path,
            "agent_workspace": self.root,
            "tenant_shared_base": os.path.join(self.root, "tenants"),
        })
        base.update(settings or {})
        self._settings = base

        for target in (config_module, __import__("channel.web.web_channel",
                                                 fromlist=["conf"])):
            patcher = patch.object(target, "conf", self._conf)
            patcher.start()
            self._patchers.append(patcher)
        self.app = __import__("channel.web.web_channel",
                              fromlist=["build_web_app"]).build_web_app()

    def _conf(self):
        return self._settings

    def close(self):
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers = []

    # -- population --------------------------------------------------------

    def write_roster(self, agent_ids, *, default=None):
        """Write the Agent roster (``<instance>/agents/team.json``).

        The registry reads the roster, and its cache is keyed on the file's
        stamped mtime, so writing here is what makes a second Agent visible to
        the app that was already built.
        """
        from agent import team

        default = default or agent_ids[0]
        profiles = []
        for index, agent_id in enumerate(agent_ids):
            if index == 0:
                profiles.append({"id": agent_id, "name": agent_id})
            else:
                profiles.append({
                    "id": agent_id, "name": agent_id,
                    "workspace": os.path.join(self.shared_root, "agents", agent_id),
                })
        path = team.team_file(self._settings)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        path.write_text(json.dumps({
            "agents": profiles, "default_agent_id": default,
        }), encoding="utf-8")
        self._roster = [p["id"] for p in profiles]
        return path

    def add_agent(self, *agent_ids):
        """Bind Agents to the tenant and put them in the roster."""
        existing = list(getattr(self, "_roster", []))
        for agent_id in agent_ids:
            if agent_id not in existing:
                existing.append(agent_id)
            self.service.bind_agent(tenant_id=self.tenant_id, agent_id=agent_id)
            if agent_id not in self._agents:
                self._agents.append(agent_id)
        self.write_roster(existing)
        return list(agent_ids)

    def bind_agent_to_tenant(self, agent_id, tenant_id):
        self.service.bind_agent(tenant_id=tenant_id, agent_id=agent_id)

    @property
    def agents(self):
        return list(self._agents)

    def role(self, code, permissions, grants=()):
        return self.service.create_role(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            code=code, name=code.title(), permissions=list(permissions),
            resource_grants=[
                {"resource_kind": g[0], "resource_id": g[1], "action": g[2]}
                for g in grants])

    def revoke_grants(self, role):
        """Take the role's resource grants away, keeping its permissions.

        This is the "grant revoked after the task exists" case: the member is
        still a member, so their own tasks remain theirs to pause or delete, but
        they can no longer use the Agent the task runs on.
        """
        current = self.service.list_roles(self.tenant_id)
        row = [r for r in current if r["code"] == role["code"]][0]
        return self.service.update_role(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            role_id=row["id"], name=row["name"],
            permissions=row["permissions"], expected_version=row["version"],
            resource_grants=[])

    def member(self, username, roles, *, password=None):
        """Create a member who already completed the first password change."""
        created = self.service.create_member(
            actor_user_id=self.admin_id, tenant_id=self.tenant_id,
            operation="create-new", username=username,
            display_name=username.title(),
            temporary_password=IdentityStack.TEMP_PASSWORD, roles=list(roles))
        self.service.change_password(
            self.service.login(username, IdentityStack.TEMP_PASSWORD).token,
            IdentityStack.TEMP_PASSWORD, password or IdentityStack.MEMBER_PASSWORD)
        self._passwords[username] = password or IdentityStack.MEMBER_PASSWORD
        self._ids[username] = created["user_id"]
        return created["user_id"]

    def user_id(self, username):
        return self._ids[username]

    def login(self, username, password=None):
        """A real session cookie value for ``username``."""
        token = self.service.login(
            username, password or self._passwords[username]).token
        return token

    # -- requests ----------------------------------------------------------

    def headers(self, token=None, *, tenant=True, json_body=False):
        headers = {"Host": self.HOST, "Origin": self.BASE}
        if token:
            headers["Cookie"] = "cow_session=" + token
        if tenant:
            headers["X-Tenant-ID"] = self.tenant_id
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, path, method="GET", body=None, token=None, *,
                tenant=True, headers=None):
        payload = None
        if body is not None:
            payload = body if isinstance(body, (bytes, str)) else json.dumps(body)
        merged = self.headers(token, tenant=tenant, json_body=body is not None)
        merged.update(headers or {})
        return self.app.request(path, method=method, headers=merged, data=payload)

    def get(self, path, token=None, **kwargs):
        return self.request(path, "GET", token=token, **kwargs)

    def post(self, path, body, token=None, **kwargs):
        return self.request(path, "POST", body=body, token=token, **kwargs)

    @staticmethod
    def json(response):
        return json.loads(response.data.decode("utf-8"))

    # -- workspace ---------------------------------------------------------

    def agent_workspace(self, agent_id):
        """The Agent's workspace root as the web layer resolves it."""
        from agent.registry import get_agent_registry
        return get_agent_registry().get(agent_id, require_enabled=False).workspace

    def scheduler_store(self, agent_id):
        """The one ``TaskStore`` for an Agent's schedule (same path as runtime)."""
        from agent.tools.scheduler.task_store import TaskStore
        from common import state_dir
        from common.runtime_identity import RuntimeIdentity

        identity = RuntimeIdentity(agent_id=agent_id, tenant_id=self.tenant_id)
        return TaskStore(str(state_dir.scheduler_file(identity)))

    def seed_task(self, agent_id, **overrides):
        """Put a task in an Agent's schedule, with owner and scope explicit."""
        from datetime import datetime, timedelta

        now = datetime.now()
        task = {
            "id": "task-1",
            "name": "task-1",
            "enabled": True,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "next_run_at": (now + timedelta(hours=1)).isoformat(),
            "schedule": {"type": "interval", "seconds": 3600},
            "action": {"type": "agent_task", "task_description": "x",
                       "receiver": "user-1", "channel_type": "web"},
            "scope": "public",
        }
        task.update(overrides)
        store = self.scheduler_store(agent_id)
        store.add_task(task)
        return store

    def personal_task(self, agent_id, user_id, **overrides):
        """A task owned by ``user_id`` (created by them, for them)."""
        task = {
            "scope": "personal",
            "owner": {"user_id": user_id, "tenant_id": self.tenant_id,
                      "agent_id": agent_id},
        }
        task.update(overrides)
        return self.seed_task(agent_id, **task)


def _bootstrap_tenant(service, shared_root, *, tenant_code="acme"):
    root_dir = os.path.dirname(shared_root.rstrip("/")) or shared_root
    tenant = service.bootstrap(
        tenant_code=tenant_code, tenant_name=tenant_code.title(),
        admin_username="root", admin_display="Root",
        admin_password=IdentityStack.ROOT_PASSWORD,
        shared_root=shared_root, allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    return IdentityStack(service, tenant, root, shared_root)
