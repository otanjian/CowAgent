# encoding:utf-8
"""Scoped project browsing, selection and controlled import over the real app.

Change ``complete-database-capability-parity``, tasks 6.1-6.6.

``GET /api/projects/browse`` used to walk the whole host filesystem, which is
why database identity mode had to close the entire picker
(``_guard_not_database`` -> ``503 database_unavailable``): a member could create
a project inside their own root but could never re-open one. These tests drive
the *real* ``build_web_app()`` -- a direct handler call would prove nothing about
the route gate, the tenant selection or the ambient identity -- and pin the
scoped replacement end to end:

* the entry point is the caller's own tenant+user projects root, identifiers are
  relative, and no host path comes back;
* traversal, drive/UNC shapes, another member's tree, a tenant admin's "public"
  permission and a cross-tenant historical path are all refused, and a directory
  swapped for a symlink between listing and selection is refused at selection
  time;
* the controlled import keeps the upstream loopback + per-start token contract,
  layers the database identity and a single-use handle on top, and never
  interprets a browser-supplied server path without one;
* preview, conflict, quota, cancel and failure compensation behave as documented.

``tests/test_project_browser.py`` covers the same guarantees at the module level
(roots passed explicitly); this file is the transport half: route policy, request
context, session ownership and the HTTP codes the console branches on.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from channel.web import project_import
from tests._helpers import IdentityStack, WebAppHarness


def _multipart(fields, files):
    """Build a multipart/form-data body web.py's test client can carry.

    ``web.application.request`` only accepts ``str`` and re-encodes it as UTF-8,
    so the parts stay ASCII by construction and round-trip byte for byte.
    """
    boundary = "----cowprojectimportboundary"
    parts = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{key}"\r\n\r\n{value}\r\n'.encode("ascii"))
    for field, filename, content in files:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode("ascii")
            + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return (b"".join(parts).decode("ascii"),
            f"multipart/form-data; boundary={boundary}")


class ScopedConsole:
    """The real console over one tenant with an owner, a peer and an admin."""

    def __init__(self, tmp_path, **settings):
        self.tmp_path = Path(tmp_path)
        self.token_file = self.tmp_path / "local-import.token"
        self.picks_root = str(self.tmp_path / "local-picks")
        os.makedirs(self.picks_root, exist_ok=True)
        resolved = {
            "local_import_token_file": str(self.token_file),
            "project_import_source_roots": self.picks_root,
        }
        resolved.update(settings)
        self.web = WebAppHarness(self.tmp_path / "app", settings=resolved)
        self.web.add_agent("agent-a")
        self.alice = self.web.member("alice", ["member"])
        self.bob = self.web.member("bob", ["member"])
        self.admin = self.web.member("tenant-admin", ["tenant_admin"])
        self.alice_token = self.web.login("alice")
        self.bob_token = self.web.login("bob")
        self.admin_token = self.web.login("tenant-admin")
        self.root_token = self.web.login("root")
        self.shared_root = os.path.realpath(self.web.shared_root)
        self.alice_root = os.path.join(self.shared_root, "users", self.alice,
                                       "projects")
        self.bob_root = os.path.join(self.shared_root, "users", self.bob,
                                     "projects")
        self.mkdir(self.alice_root, "alpha/inner")
        self.write(self.alice_root, "alpha/readme.md", "alpha")
        self.write(self.alice_root, "alpha/inner/note.txt", "note")
        self.mkdir(self.bob_root, "beta")
        self.write(self.bob_root, "beta/secret.txt", "bob-only")

    # -- fixtures ---------------------------------------------------------

    def close(self):
        self.web.close()

    @staticmethod
    def mkdir(root, relative):
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def write(root, relative, text):
        path = os.path.join(root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    @staticmethod
    def symlink(target, link):
        if os.path.lexists(link):
            if os.path.isdir(link) and not os.path.islink(link):
                shutil.rmtree(link)
            else:
                os.unlink(link)
        os.symlink(target, link)
        return link

    def local_token(self):
        """The per-start token, read the way a local client reads it."""
        project_import.local_import_token()
        return self.token_file.read_text(encoding="utf-8").strip()

    def seed_session(self, owner, session_id="sess-1", *, channel="web",
                     agent="agent-a"):
        from agent.memory.conversation_store import get_conversation_store
        from agent.registry import get_agent_registry
        profile = get_agent_registry().get(agent, require_enabled=False)
        store = get_conversation_store(profile.workspace)
        now = int(time.time())
        with store._lock:
            con = store._connect()
            try:
                with con:
                    con.execute(
                        "INSERT OR IGNORE INTO sessions"
                        " (session_id, channel_type, created_at, last_active,"
                        "  msg_count, owner) VALUES (?,?,?,?,0,?)",
                        (session_id, channel, now, now, owner))
            finally:
                con.close()
        return session_id

    def published_projects(self):
        return sorted(
            name for name in os.listdir(self.alice_root)
            if not name.startswith("."))

    def bound_project(self, session, *, agent="agent-a", user=None):
        """The session's bound project, read the way the server reads it.

        ``project_store`` resolves its file from the *ambient identity*, so a
        read outside a request has to enter the same scope the request ran in --
        otherwise the assertion would be comparing against an empty store and
        would pass no matter what the handler did.
        """
        from agent.workspace import project_store
        from common.runtime_identity import RuntimeIdentity, use_identity
        identity = RuntimeIdentity(tenant_id=self.web.tenant_id,
                                   user_id=user or self.alice)
        with use_identity(identity):
            return project_store.get_project_dir(session, agent)

    def staging_leftovers(self):
        return sorted(
            name for name in os.listdir(self.alice_root)
            if name.startswith(".staging-"))

    # -- requests ---------------------------------------------------------

    def call(self, path, method="GET", body=None, token=None, *, remote=None,
             tenant=True, headers=None, raw=False, content_type=None):
        payload = None
        if body is not None:
            payload = body if isinstance(body, (str, bytes)) else json.dumps(body)
        merged = self.web.headers(token, tenant=tenant,
                                  json_body=body is not None and not raw)
        if content_type:
            merged["Content-Type"] = content_type
        merged.update(headers or {})
        env = {"REMOTE_ADDR": remote} if remote is not None else {}
        return self.web.app.request(path, method=method, headers=merged,
                                    data=payload, env=env)

    def browse(self, token, path=None, *, remote=None):
        query = "" if path is None else "?path=" + path
        return self.call("/api/projects/browse" + query, token=token,
                         remote=remote)

    @staticmethod
    def body(response):
        return json.loads(response.data.decode("utf-8"))

    @staticmethod
    def status(response):
        return int(response.status.split()[0])

    def refused(self, response, code, status=None):
        payload = self.body(response)
        assert payload.get("code") == code, (response.status, payload)
        if status is not None:
            assert self.status(response) == status, (response.status, payload)
        return payload

    def select(self, token, project_dir, *, session=None, agent="agent-a"):
        return self.call("/api/projects/select", method="POST", token=token,
                         body={"session": session or "", "agent": agent,
                               "project_dir": project_dir})

    def preview(self, token, source, *, name=None, remote="127.0.0.1",
                local_token=None, session=None, extra=None):
        body = {"source": source}
        if name:
            body["name"] = name
        if session:
            body["session"] = session
        body.update(extra or {})
        if local_token is not None:
            body["token"] = local_token
        return self.call("/api/projects/import/preview", method="POST",
                         token=token, body=body, remote=remote)

    def import_request(self, token, body, *, remote="127.0.0.1"):
        return self.call("/api/projects/import", method="POST", token=token,
                         body=body, remote=remote)

    def cancel(self, token, handle, *, remote="127.0.0.1"):
        return self.call("/api/projects/import/cancel", method="POST",
                         token=token, body={"handle": handle}, remote=remote)

    @staticmethod
    def handle_of(response):
        return json.loads(response.data.decode("utf-8"))["handle"]


@pytest.fixture
def consoles(tmp_path):
    """Factory for one real console app per call, torn down afterwards."""
    built = []

    def factory(name="c1", **settings):
        console = ScopedConsole(tmp_path / name, **settings)
        built.append(console)
        return console

    yield factory
    for console in built:
        console.close()


@pytest.fixture
def console(consoles):
    return consoles()


def _token(console):
    """A valid loopback + per-start-token import body fragment."""
    return console.local_token()


# --- 6.1 / 6.6 the route is served at all ------------------------------------


def test_browse_is_open_and_refuses_identity_before_anything_else(console):
    """The double closure is gone: never 503, but still 401/400."""
    anonymous = console.browse(None)
    assert console.status(anonymous) == 401, anonymous.data
    no_tenant = console.call("/api/projects/browse", token=console.alice_token,
                             tenant=False)
    assert console.status(no_tenant) == 400, no_tenant.data
    assert console.body(no_tenant)["code"] == "missing_tenant"
    own = console.browse(console.alice_token)
    assert console.status(own) == 200, own.data


# --- 6.1 personal-root browsing ----------------------------------------------


def test_a_member_browses_their_own_root_with_relative_identifiers(console):
    response = console.browse(console.alice_token)
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["status"] == "success"
    assert payload["path"] == ""
    assert payload["parent"] is None
    assert payload["dirs"] == [{"name": "alpha", "path": "alpha"}]
    assert payload["breadcrumbs"] == [{"name": "/", "path": ""}]
    assert payload["scope"] == {"kind": "personal",
                                "tenant_id": console.web.tenant_id,
                                "user_id": console.alice}
    # No host path, and nothing about the peer's tree, comes back.
    text = response.data.decode("utf-8")
    assert console.alice_root not in text
    assert console.shared_root not in text
    assert "beta" not in text


def test_nested_browse_returns_a_bounded_parent_and_breadcrumbs(console):
    response = console.browse(console.alice_token, "alpha/inner")
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["path"] == "alpha/inner"
    assert payload["parent"] == "alpha"
    assert payload["breadcrumbs"] == [{"name": "/", "path": ""},
                                      {"name": "alpha", "path": "alpha"},
                                      {"name": "inner", "path": "alpha/inner"}]
    assert payload["dirs"] == []


def test_hidden_files_and_symlinked_entries_are_not_offered(console):
    console.mkdir(console.alice_root, ".hidden")
    console.write(console.alice_root, "notes.txt", "x")
    console.symlink(console.bob_root, os.path.join(console.alice_root, "linkdir"))
    payload = console.body(console.browse(console.alice_token))
    assert [entry["name"] for entry in payload["dirs"]] == ["alpha"]
    # ... and the link cannot be navigated into either.
    console.refused(console.browse(console.alice_token, "linkdir"),
                    "unsafe_path", 403)
    console.refused(console.browse(console.alice_token, "notes.txt"),
                    "unsafe_path", 403)


def test_a_missing_directory_is_a_404_not_the_root(console):
    console.refused(console.browse(console.alice_token, "nope"),
                    "not_found", 404)


def test_a_fresh_member_without_a_projects_directory_gets_an_empty_root(consoles):
    fresh = consoles("fresh")
    fresh.web.member("carol", ["member"])
    shutil.rmtree(fresh.bob_root, ignore_errors=True)
    shutil.rmtree(fresh.alice_root, ignore_errors=True)
    token = fresh.web.login("carol")
    response = fresh.browse(token)
    assert fresh.status(response) == 200, response.data
    assert fresh.body(response)["dirs"] == []


# --- 6.1 / 6.2 traversal and foreign-scope refusals ---------------------------


@pytest.mark.parametrize("attempt", [
    "..",
    "../..",
    "alpha/../..",
    "alpha/../../users",
    "..%2F..%2Fetc",
    "%2e%2e%2f%2e%2e",
    "..\\..\\windows",
    "C:\\Windows",
    "C:/Windows",
    "\\\\server\\share",
    "//etc",
    "/etc",
    "alpha//inner",
    "./alpha",
    "__DRIVES__",
])
def test_traversal_and_host_shaped_identifiers_are_refused(console, attempt):
    response = console.browse(console.alice_token, attempt)
    assert console.status(response) in (400, 403), (attempt, response.data)
    assert console.body(response)["status"] == "error"


def test_an_absolute_path_outside_the_root_is_refused(console):
    console.refused(console.browse(console.alice_token, "/etc"), "outside_root",
                    403)
    console.refused(console.browse(console.alice_token, console.bob_root),
                    "outside_root", 403)
    console.refused(console.browse(console.alice_token, console.shared_root),
                    "outside_root", 403)


def test_a_historical_absolute_path_inside_the_root_is_normalized(console):
    response = console.browse(console.alice_token,
                              os.path.join(console.alice_root, "alpha"))
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["path"] == "alpha"
    assert payload["dirs"] == [{"name": "inner", "path": "alpha/inner"}]
    assert console.alice_root not in response.data.decode("utf-8")


def test_another_members_tree_is_invisible_to_a_peer(console):
    console.refused(console.browse(console.bob_token, console.alice_root),
                    "outside_root", 403)
    console.refused(console.browse(console.bob_token, "alpha"), "not_found", 404)
    payload = console.body(console.browse(console.bob_token))
    assert [entry["name"] for entry in payload["dirs"]] == ["beta"]


def test_a_tenant_admin_does_not_inherit_a_members_private_tree(console):
    """A tenant admin manages public assets; a private tree is not one."""
    own = console.browse(console.admin_token)
    assert console.status(own) == 200, own.data
    assert console.body(own)["dirs"] == []
    response = console.browse(console.admin_token, console.alice_root)
    assert console.status(response) == 403, response.data
    assert "readme.md" not in response.data.decode("utf-8")
    refusal = console.body(response)
    assert refusal["code"] == "outside_root"


def test_a_platform_admin_does_not_inherit_a_members_private_tree(console):
    response = console.browse(console.root_token, console.alice_root)
    assert console.status(response) == 403, response.data
    assert "inner" not in response.data.decode("utf-8")


def test_a_cross_tenant_historical_path_is_refused(consoles):
    console = consoles("cross")
    # A second tenant, created the way the product creates one (platform actor),
    # so the caller's own root provably does not contain the historical path.
    other = console.web.stack.other_tenant("other")
    other_root = console.web.service.tenant_shared_root(other["tenant_id"])
    foreign = os.path.join(other_root, "users", "someone", "projects", "p")
    os.makedirs(foreign, exist_ok=True)
    response = console.browse(console.alice_token, foreign)
    console.refused(response, "outside_root", 403)
    assert other_root not in response.data.decode("utf-8")
    # A readable refusal for browsing is not enough: the same historical
    # identifier must not become a project binding either, and the session must
    # be left exactly as it was.
    session = console.seed_session(console.alice)
    select_response = console.select(console.alice_token, foreign,
                                     session=session)
    console.refused(select_response, "outside_root", 403)
    assert console.bound_project(session) in (None, "")


def test_a_cross_tenant_member_cannot_reach_this_tenants_root(consoles):
    """The other tenant's own member is refused on *our* member's tree.

    A valid session in another tenant is not a grant here: the entry point is the
    caller's own tenant+user root, so the peer's identifier resolves outside it.
    """
    console = consoles("cross-caller")
    other = console.web.stack.other_tenant("other")
    foreign_token = console.web.service.login(
        "foreign", IdentityStack.MEMBER_PASSWORD).token
    scope = {"X-Tenant-ID": other["tenant_id"]}
    response = console.call("/api/projects/browse", token=foreign_token,
                            headers=scope)
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["scope"]["tenant_id"] == other["tenant_id"]
    listed = json.dumps(payload)
    assert console.alice not in listed
    assert console.shared_root not in listed
    # An identifier that names our member's tree resolves outside the peer's own
    # root, so it is refused instead of listed.
    reached = console.call("/api/projects/browse?path=" + console.alice_root,
                           token=foreign_token, headers=scope)
    console.refused(reached, "outside_root", 403)
    assert b"alpha" not in reached.data


def test_private_directories_nested_in_an_outer_root_are_never_exposed(console):
    """The platform/tenant shared root *contains* member-private trees."""
    from agent.workspace import project_store
    with mock.patch.object(project_store, "user_projects_root",
                           return_value=console.shared_root):
        listing = console.browse(console.alice_token)
        assert console.status(listing) == 200, listing.data
        # ``users/`` holds only private trees, so it is omitted entirely: the
        # container would disclose whose trees exist.
        assert console.body(listing)["dirs"] == []
        console.refused(console.browse(console.alice_token, "users"),
                        "unsafe_path", 403)
        console.refused(console.browse(console.alice_token,
                                       "users/%s" % console.bob),
                        "unsafe_path", 403)
        console.refused(console.browse(console.alice_token,
                                       "users/%s/projects" % console.bob),
                        "unsafe_path", 403)
        own = console.browse(console.alice_token,
                             "users/%s/projects" % console.alice)
        assert console.status(own) == 200, own.data
        assert [entry["name"] for entry in console.body(own)["dirs"]] == ["alpha"]


# --- 6.2 selection re-validation ---------------------------------------------


def test_select_binds_the_callers_own_directory_by_relative_id(console):
    session = console.seed_session(console.alice)
    response = console.select(console.alice_token, "alpha", session=session)
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["status"] == "success"
    assert os.path.realpath(payload["current"]["path"]) == os.path.join(
        console.alice_root, "alpha")
    assert console.bound_project(session) == os.path.realpath(
        os.path.join(console.alice_root, "alpha"))


def test_select_normalizes_a_legacy_absolute_path_inside_the_root(console):
    session = console.seed_session(console.alice)
    legacy = os.path.join(console.alice_root, "alpha")
    response = console.select(console.alice_token, legacy, session=session)
    assert console.status(response) == 200, response.data


def test_select_refuses_a_directory_swapped_for_a_symlink(console):
    """A listing is not an authorization: re-resolve at selection time."""
    session = console.seed_session(console.alice)
    assert console.status(console.browse(console.alice_token, "alpha")) == 200
    console.symlink(console.bob_root, os.path.join(console.alice_root, "alpha"))
    response = console.select(console.alice_token, "alpha", session=session)
    console.refused(response, "unsafe_path", 403)
    assert console.bound_project(session) in (None, "")


def test_select_refuses_another_members_project(console):
    session = console.seed_session(console.alice)
    response = console.select(console.alice_token, console.bob_root,
                              session=session)
    console.refused(response, "outside_root", 403)


def test_select_refuses_an_escaping_identifier(console):
    session = console.seed_session(console.alice)
    for attempt in ("..", "../..", "alpha/../../..", "C:\\Windows"):
        response = console.select(console.alice_token, attempt, session=session)
        assert console.status(response) in (400, 403), (attempt, response.data)


def test_select_refuses_a_directory_that_disappeared(console):
    session = console.seed_session(console.alice)
    shutil.rmtree(os.path.join(console.alice_root, "alpha"))
    response = console.select(console.alice_token, "alpha", session=session)
    console.refused(response, "not_found", 404)
    assert console.bound_project(session) in (None, "")


def test_select_refuses_a_session_owned_by_another_member(console):
    foreign = console.seed_session(console.bob, session_id="sess-bob")
    response = console.select(console.alice_token, "alpha", session=foreign)
    assert console.status(response) in (403, 404), response.data
    assert console.bound_project(foreign, user=console.bob) in (None, "")


def test_select_refuses_a_non_web_session(console):
    console.seed_session(console.alice, session_id="sess-cli", channel="cli")
    response = console.select(console.alice_token, "alpha", session="sess-cli")
    assert console.status(response) in (403, 404), response.data
    assert console.bound_project("sess-cli") in (None, "")


# --- 6.3 the local transport keeps loopback + the per-start token -------------


def test_a_local_source_path_is_refused_from_a_remote_peer(console):
    console.write(console.picks_root, "myproj/main.py", "print(1)")
    response = console.preview(console.alice_token,
                               os.path.join(console.picks_root, "myproj"),
                               remote="203.0.113.7",
                               local_token=console.local_token())
    console.refused(response, "local_path_denied", 403)
    assert "handle" not in console.body(response)


def test_a_local_source_path_requires_the_per_start_token(console):
    console.write(console.picks_root, "myproj/main.py", "print(1)")
    source = os.path.join(console.picks_root, "myproj")
    console.refused(console.preview(console.alice_token, source),
                    "token_invalid", 403)
    console.refused(console.preview(console.alice_token, source,
                                    local_token="not-the-token"),
                    "token_invalid", 403)
    # The token is published for a local client and regenerated per start.
    first = console.local_token()
    with mock.patch.object(project_import, "local_import_token",
                           return_value=first):
        ok = console.preview(console.alice_token, source, local_token=first)
    assert console.status(ok) == 200, ok.data
    project_import.reset_local_import_token()
    second = console.local_token()
    assert first != second
    stale = console.preview(console.alice_token, source, local_token=first)
    console.refused(stale, "token_invalid", 403)


def test_a_forwarded_local_path_is_refused(console):
    """A reverse proxy must not turn a remote peer into a loopback one."""
    console.write(console.picks_root, "myproj/main.py", "print(1)")
    source = os.path.join(console.picks_root, "myproj")
    response = console.call(
        "/api/projects/import/preview", method="POST",
        token=console.alice_token,
        body={"source": source, "token": console.local_token()},
        remote="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.7"})
    console.refused(response, "local_path_denied", 403)


# --- 6.3 preview -------------------------------------------------------------


def test_preview_reports_the_target_scale_and_conflict(console):
    console.write(console.picks_root, "myproj/src/main.py", "a")
    console.write(console.picks_root, "myproj/README.md", "b")
    source = os.path.join(console.picks_root, "myproj")
    response = console.preview(console.alice_token, source,
                               local_token=console.local_token())
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["status"] == "success"
    assert payload["target"] == "myproj"
    assert payload["files"] == 2
    assert payload["dirs"] == 1
    assert payload["bytes"] == 2
    assert payload["conflict"] is None
    assert payload["ready"] is True
    assert payload["handle"]
    # Nothing was created by a preview.
    assert console.published_projects() == ["alpha"]


def test_preview_reports_an_existing_target_instead_of_overwriting(console):
    console.write(console.picks_root, "alpha/main.py", "a")
    source = os.path.join(console.picks_root, "alpha")
    payload = console.body(console.preview(console.alice_token, source,
                                           local_token=console.local_token()))
    assert payload["conflict"] is not None
    assert payload["conflict"]["code"] == "name_taken"
    assert payload["ready"] is False
    assert console.published_projects() == ["alpha"]


def test_preview_refuses_a_source_outside_the_allowed_roots(console):
    outside = console.mkdir(str(console.tmp_path / "elsewhere"), "proj")
    console.write(outside, "main.py", "x")
    console.refused(
        console.preview(console.alice_token, outside,
                        local_token=console.local_token()),
        "outside_root", 403)
    console.refused(
        console.preview(console.alice_token, console.alice_root,
                        local_token=console.local_token()),
        "invalid_request", 400)


def test_preview_can_be_bound_to_an_owned_session(console):
    session = console.seed_session(console.alice)
    console.write(console.picks_root, "myproj/main.py", "a")
    source = os.path.join(console.picks_root, "myproj")
    ok = console.preview(console.alice_token, source, session=session,
                         local_token=console.local_token())
    assert console.status(ok) == 200, ok.data
    foreign = console.seed_session(console.bob, session_id="sess-bob")
    refused = console.preview(console.alice_token, source, session=foreign,
                              local_token=console.local_token())
    assert console.status(refused) in (403, 404), refused.data


# --- 6.3 / 6.4 import: handle, publish, conflict, quota, cancel, compensation --


def _previewed(console, name="myproj", body="a"):
    console.write(console.picks_root, name + "/src/main.py", body)
    source = os.path.join(console.picks_root, name)
    response = console.preview(console.alice_token, source,
                               local_token=console.local_token())
    assert console.status(response) == 200, response.data
    return source, console.handle_of(response)


def test_import_refuses_a_browser_supplied_path_without_a_handle(console):
    """The dangerous transport: a server path with no single-use handle."""
    source, _handle = _previewed(console, "myproj")
    token = console.local_token()
    refusal = console.import_request(
        console.alice_token, {"source": source, "token": token, "name": "myproj"})
    console.refused(refusal, "handle_required", 403)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_import_refuses_an_unknown_or_foreign_handle(console):
    source, handle = _previewed(console, "myproj")
    token = console.local_token()
    unknown = console.import_request(
        console.alice_token,
        {"source": source, "token": token, "name": "myproj", "handle": "nope"})
    console.refused(unknown, "handle_invalid", 403)
    foreign = console.import_request(
        console.bob_token,
        {"source": source, "token": token, "name": "myproj", "handle": handle})
    console.refused(foreign, "handle_invalid", 403)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_import_publishes_atomically_and_the_handle_is_single_use(console):
    source, handle = _previewed(console, "myproj")
    token = console.local_token()
    response = console.import_request(
        console.alice_token,
        {"source": source, "token": token, "name": "myproj", "handle": handle})
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["status"] == "success"
    assert payload["target"] == "myproj"
    assert payload["files"] == 1
    assert os.path.isfile(os.path.join(console.alice_root, "myproj", "src",
                                       "main.py"))
    assert console.published_projects() == ["alpha", "myproj"]
    assert console.staging_leftovers() == []
    # The source is untouched and the handle cannot be replayed.
    assert os.path.isfile(os.path.join(source, "src", "main.py"))
    replay = console.import_request(
        console.alice_token,
        {"source": source, "token": token, "name": "myproj2", "handle": handle})
    console.refused(replay, "handle_invalid", 403)
    assert console.published_projects() == ["alpha", "myproj"]


def test_import_refuses_a_resubmitted_source_under_a_used_handle(console):
    source, handle = _previewed(console, "myproj")
    token = console.local_token()
    first = console.import_request(
        console.alice_token,
        {"source": source, "token": token, "name": "myproj", "handle": handle})
    assert console.status(first) == 200, first.data
    second = console.import_request(
        console.alice_token,
        {"source": os.path.join(console.picks_root, "alpha"), "token": token,
         "name": "other", "handle": handle})
    console.refused(second, "handle_invalid", 403)


def test_import_never_overwrites_an_existing_project(console):
    console.write(console.picks_root, "alpha/src/main.py", "incoming")
    source = os.path.join(console.picks_root, "alpha")
    preview = console.preview(console.alice_token, source,
                              local_token=console.local_token())
    assert console.status(preview) == 200, preview.data
    handle = console.handle_of(preview)
    response = console.import_request(
        console.alice_token,
        {"source": source, "token": console.local_token(), "name": "alpha",
         "handle": handle})
    console.refused(response, "conflict", 409)
    with open(os.path.join(console.alice_root, "alpha", "readme.md"),
              encoding="utf-8") as handle_file:
        assert handle_file.read() == "alpha"
    assert console.staging_leftovers() == []


def test_preview_refuses_a_source_inside_the_platform_data_directory(consoles):
    """Run records and the identity database are not importable material.

    The deployment may allow picking outside the member's root
    (``project_import_source_roots``), but the platform data directory holds run
    records, credentials and the identity database: copying it into a browsable
    project would leak secrets into the console, so it is refused even when an
    allowed root contains it.
    """
    data_root = pathlib.Path(tempfile.mkdtemp()) / "cow-data"
    console = consoles("data-root",
                       project_import_source_roots=str(data_root))
    run_records = console.mkdir(str(data_root), "run-records")
    console.write(run_records, "2026-09-15.log", "secret")
    with mock.patch.dict(os.environ, {"COW_DATA_DIR": str(data_root)}):
        refused = console.preview(
            console.alice_token, run_records,
            local_token=console.local_token())
    console.refused(refused, "outside_root", 403)
    assert b"run-records" not in refused.data
    assert console.published_projects() == ["alpha"]


def test_two_imports_racing_for_the_last_slot_resolve_to_one_winner(consoles):
    """The spec's quota-race scenario: at most one publishes, never both.

    ``alpha`` occupies one of two slots, so the two imports below contend for the
    last one. The count and the claim happen under one lock inside the seam, and
    the filesystem cannot show the winner yet while the loser is deciding, which
    is exactly why the in-process reservations are counted too.
    """
    from concurrent.futures import ThreadPoolExecutor

    console = consoles("race", project_import_max_projects=2)
    sources = []
    handles = []
    for name in ("one", "two"):
        console.write(console.picks_root, name + "/main.py", name)
        source = os.path.join(console.picks_root, name)
        preview = console.preview(console.alice_token, source,
                                  local_token=console.local_token())
        assert console.status(preview) == 200, preview.data
        sources.append(source)
        handles.append(console.handle_of(preview))

    def publish(index):
        name = os.path.basename(sources[index])
        return console.import_request(
            console.alice_token,
            {"source": sources[index], "token": console.local_token(),
             "name": name, "handle": handles[index]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(publish, (0, 1)))
    codes = [console.status(response) for response in responses]
    assert codes.count(200) == 1, [response.data for response in responses]
    loser = [response for response in responses
             if console.status(response) != 200][0]
    console.refused(loser, "quota_refused", 409)
    # Exactly one of the two became a project, and the loser staged nothing.
    published = console.published_projects()
    assert len(published) == 2 and published[0] == "alpha", published
    assert published[1] in ("one", "two")
    assert console.staging_leftovers() == []


def test_an_upload_never_overwrites_an_existing_project(console):
    """The remote transport goes through the same publish step, so the
    "no silent overwrite" rule holds there too."""
    body, content_type = _multipart(
        {"name": "alpha", "paths": json.dumps(["main.py"])},
        [("files", "0", b"incoming")])
    response = console.call("/api/projects/import", method="POST",
                            token=console.alice_token, body=body, raw=True,
                            content_type=content_type, remote="203.0.113.7")
    console.refused(response, "conflict", 409)
    with open(os.path.join(console.alice_root, "alpha", "readme.md"),
              encoding="utf-8") as handle_file:
        assert handle_file.read() == "alpha"
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_import_refuses_a_source_that_differs_from_the_preview(console):
    source, handle = _previewed(console, "myproj")
    other = os.path.join(console.picks_root, "alpha")
    console.write(console.picks_root, "alpha/main.py", "x")
    response = console.import_request(
        console.alice_token,
        {"source": other, "token": console.local_token(), "name": "myproj",
         "handle": handle})
    console.refused(response, "handle_invalid", 403)
    assert console.published_projects() == ["alpha"]


def test_import_excludes_symlinked_entries(console):
    source = _previewed(console, "myproj")[0]
    console.symlink(console.bob_root, os.path.join(source, "linked"))
    console.write(source, "real.txt", "kept")
    preview = console.preview(console.alice_token, source,
                              local_token=console.local_token())
    payload = console.body(preview)
    assert payload["files"] == 2  # src/main.py + real.txt
    assert any("linked" in item for item in payload["excluded"])
    published = console.import_request(
        console.alice_token,
        {"source": source, "token": console.local_token(), "name": "myproj",
         "handle": payload["handle"]})
    assert console.status(published) == 200, published.data
    assert not os.path.lexists(os.path.join(console.alice_root, "myproj",
                                            "linked"))
    assert os.path.isfile(os.path.join(console.alice_root, "myproj",
                                       "real.txt"))
    assert not os.path.exists(os.path.join(console.alice_root, "myproj",
                                           "secret.txt"))


def test_import_quota_reservation_refuses_before_staging(consoles):
    console = consoles("quota", project_import_max_projects=1)
    console.write(console.picks_root, "one/main.py", "1")
    console.write(console.picks_root, "two/main.py", "2")
    # ``alpha`` already occupies the single slot.
    preview = console.preview(console.alice_token,
                              os.path.join(console.picks_root, "one"),
                              local_token=console.local_token())
    assert console.status(preview) == 200, preview.data
    response = console.import_request(
        console.alice_token,
        {"source": os.path.join(console.picks_root, "one"),
         "token": console.local_token(), "name": "one",
         "handle": console.handle_of(preview)})
    console.refused(response, "quota_refused", 409)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_a_cancelled_handle_publishes_nothing(console):
    source, handle = _previewed(console, "myproj")
    token = console.local_token()
    cancelled = console.cancel(console.alice_token, handle)
    assert console.status(cancelled) == 200, cancelled.data
    assert console.body(cancelled)["cancelled"] is True
    response = console.import_request(
        console.alice_token,
        {"source": source, "token": token, "name": "myproj", "handle": handle})
    console.refused(response, "cancelled", 409)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_a_mid_flight_cancel_rolls_the_staging_tree_back(console):
    """Cancellation between the copy and the publish must leave nothing."""
    source, handle = _previewed(console, "myproj")
    with mock.patch.object(project_import, "is_cancelled", lambda _record: True):
        response = console.import_request(
            console.alice_token,
            {"source": source, "token": console.local_token(), "name": "myproj",
             "handle": handle})
    console.refused(response, "cancelled", 409)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_a_failed_publish_compensates_and_releases_the_slot(consoles):
    console = consoles("compensate", project_import_max_projects=2)
    console.write(console.picks_root, "one/main.py", "1")
    console.write(console.picks_root, "two/main.py", "2")
    first = console.preview(console.alice_token,
                            os.path.join(console.picks_root, "one"),
                            local_token=console.local_token())
    with mock.patch("agent.workspace.project_browser._publish",
                    side_effect=OSError("boom")):
        failed = console.import_request(
            console.alice_token,
            {"source": os.path.join(console.picks_root, "one"),
             "token": console.local_token(), "name": "one",
             "handle": console.handle_of(first)})
    console.refused(failed, "import_failed", 500)
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []
    # The reservation was released, so the other slot is still importable.
    second = console.preview(console.alice_token,
                             os.path.join(console.picks_root, "two"),
                             local_token=console.local_token())
    retried = console.import_request(
        console.alice_token,
        {"source": os.path.join(console.picks_root, "two"),
         "token": console.local_token(), "name": "two",
         "handle": console.handle_of(second)})
    assert console.status(retried) == 200, retried.data
    assert console.published_projects() == ["alpha", "two"]


def test_import_refuses_a_remote_peer_and_a_client_supplied_option(console):
    source, handle = _previewed(console, "myproj")
    remote = console.import_request(
        console.alice_token,
        {"source": source, "token": console.local_token(), "name": "myproj",
         "handle": handle},
        remote="203.0.113.7")
    console.refused(remote, "local_path_denied", 403)
    # A request cannot widen its own allowed source roots.
    widened = console.preview(
        console.alice_token, source, local_token=console.local_token(),
        extra={"source_roots": [str(console.tmp_path)]})
    assert console.status(widened) == 200, widened.data


# --- 6.4 the remote transport: an authorized upload, not a client path --------


def test_an_upload_manifest_is_validated_and_traversal_is_refused(console):
    fields = {"name": "uploaded", "paths": json.dumps(["src/main.py", "../escape.py"])}
    body, content_type = _multipart(
        fields, [("files", "0", b"first"), ("files", "1", b"second")])
    response = console.call("/api/projects/import", method="POST",
                            token=console.alice_token, body=body, raw=True,
                            content_type=content_type, remote="203.0.113.7")
    assert console.status(response) in (400, 403), response.data
    assert console.body(response)["status"] == "error"
    assert console.published_projects() == ["alpha"]
    assert console.staging_leftovers() == []


def test_an_upload_publishes_without_interpreting_a_server_path(console):
    paths = ["src/main.py", "README.md"]
    body, content_type = _multipart(
        {"name": "uploaded", "paths": json.dumps(paths)},
        [("files", "0", b"print(1)"), ("files", "1", b"readme")])
    response = console.call("/api/projects/import", method="POST",
                            token=console.alice_token, body=body, raw=True,
                            content_type=content_type, remote="203.0.113.7")
    assert console.status(response) == 200, response.data
    payload = console.body(response)
    assert payload["status"] == "success"
    assert payload["target"] == "uploaded"
    assert payload["files"] == 2
    assert os.path.isfile(os.path.join(console.alice_root, "uploaded", "src",
                                       "main.py"))
    assert console.published_projects() == ["alpha", "uploaded"]
    assert console.staging_leftovers() == []


def test_an_upload_still_requires_identity_and_origin(console):
    body, content_type = _multipart(
        {"name": "uploaded", "paths": json.dumps(["a.txt"])},
        [("files", "0", b"a")])
    anonymous = console.call("/api/projects/import", method="POST", body=body,
                             raw=True, content_type=content_type,
                             remote="203.0.113.7")
    assert console.status(anonymous) == 401, anonymous.data
    cross_origin = console.call(
        "/api/projects/import", method="POST", token=console.alice_token,
        body=body, raw=True, content_type=content_type, remote="203.0.113.7",
        headers={"Origin": "http://evil.example"})
    assert console.status(cross_origin) == 403, cross_origin.data
    assert console.body(cross_origin)["code"] == "csrf_failed"
    assert console.published_projects() == ["alpha"]
