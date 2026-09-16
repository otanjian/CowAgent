# encoding:utf-8
"""Task 5.4/5.5: a functional grant never decides the tenant's shared surface.

The 工具与技能 page is shared by members and administrators, so the *page* must
not be the thing that separates them — the object range is. Two writes on that
page are the whole risk, and both used to land in the tenant's shared surface on
a member's functional grant alone:

* the **global enable/disable** (``POST /api/skills``) writes the shared
  ``skills_config.json`` row every member and Agent reads;
* the **body write** (``POST /api/skills/content``) rewrites the shared skill
  definition — the installation-shipped builtins are additionally protected by
  their read-only provenance, which is why the body case needs a tenant-authored
  skill to be observable at all.

The gate that was supposed to stop this returned early whenever the request named
no ``agent_id``, and the console never sends one, so both writes were decided by
the functional grant alone. These tests measure the *effect*, not the status
code: the shared file and the shared enable state are compared before and after,
and the administrator's own read is the witness. A refused request that still
moved the shared state would pass a status-only test.

The control tests at the end are not decoration: the same calls must succeed for
a caller whose scope *does* cover the shared surface, and the page's own request
shape (``{action, name}``, no ``resource_id``) must work there, because that is
the payload the console actually sends.
"""

from __future__ import annotations

import json
import os
import tempfile

from unittest.mock import patch

import web

import config
from auth.service import IdentityService
from channel.web import web_channel

#: A tenant-authored skill, so it lives in the tenant shared root and carries no
#: installation-shipped read-only provenance.
NAME = "tenant-note"
RID = "custom:tenant-note"
ORIGINAL = "SHARED ORIGINAL\n"
#: The whole seeded file (front matter included): the writes below are compared
#: against this, not against :data:`ORIGINAL`, because "unchanged" is a claim
#: about the file rather than about the body line.
SEEDED = ("---\nname: %s\ndescription: Tenant authored note.\n---\n%s"
          % (NAME, ORIGINAL))
REWRITTEN = "MEMBER REWROTE THE SHARED DEFINITION\n"


def _mk_db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _status(response) -> int:
    return int(str(response.status).split()[0])


def _body(response) -> dict:
    return json.loads(response.data.decode("utf-8"))


class _World:
    """acme with a platform admin, a tenant admin, and a member holding grants.

    The member's role carries the *functional* public-surface rights
    (``skill.read/use/edit/enable``) plus an explicit grant on the skill under
    test — the strongest a member can hold without administration, and exactly
    the combination task 2.2 says must still not decide the shared surface.
    """

    def __init__(self):
        self.db = _mk_db()
        self.shared_root = os.path.join(tempfile.mkdtemp(), "acme")
        self.svc = IdentityService(self.db)
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared_root)
        self.svc.change_password(self.svc.login("root", "Str0ngAdminPass").token,
                                 "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.tenant_id = self.svc.list_tenants()[0]["id"]

        settings = {"identity_mode": "database", "identity_db_path": self.db}
        for target in (config, web_channel):
            patcher = patch.object(target, "conf", return_value=settings)
            patcher.start()
            self._patchers = getattr(self, "_patchers", []) + [patcher]

        self._seed_skill()
        self.admin_token = self._member("acmeadmin", ["tenant_admin"])
        # The two callers the matrix turns on: a member holding every functional
        # right plus a grant on the skill, and an administrator holding the same
        # rights (so the difference is the scope, not the grant).
        self.member_token = self._member("acmemember",
                                         [self._grants_role("acmemember")])
        self.editor_token = self._member("acmeeditor",
                                         ["tenant_admin",
                                          self._grants_role("acmeeditor")])

    def stop(self):
        for patcher in getattr(self, "_patchers", []):
            patcher.stop()

    # -- setup ---------------------------------------------------------------

    @property
    def skills_root(self) -> str:
        """The skills root the application itself resolves.

        ``_skill_service('')`` asks ``state_dir.skills_dir(base=<tenant root>)``,
        which returns the tenant's own ``skills/`` **when that directory
        exists** and otherwise falls back to the installation's shared copy.
        Creating it is therefore part of the setup, not an optimization: without
        it the write under test would be aimed at the installation root, which is
        not the surface this file claims to protect.
        """
        return os.path.join(self.shared_root, "skills")

    def _seed_skill(self) -> None:
        skill = os.path.join(self.skills_root, NAME)
        os.makedirs(skill, exist_ok=True)
        with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(SEEDED)

    def _grants_role(self, username) -> str:
        """A role carrying the functional rights plus grants on the test skill.

        Deliberately *not* an administration role: this is the strongest a plain
        member can hold, which is the combination under test. An administrator
        gets the same role alongside ``tenant_admin`` so the two differ only in
        scope.
        """
        return self.svc.create_role(
            actor_user_id=self.root["id"], tenant_id=self.tenant_id,
            code=f"{username}-grants", name=username,
            permissions=["skill.read", "skill.use", "skill.edit", "skill.enable"],
            resource_grants=[
                {"resource_kind": "skill", "resource_id": RID, "action": action}
                for action in ("read", "use", "edit", "enable")],
        )["code"]

    def _member(self, username, codes):
        """Create a member holding exactly ``codes``, returning a fresh token."""
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tenant_id,
            operation="create-new", username=username, display_name=username,
            temporary_password="MemTempPass1", roles=list(codes))
        token = self.svc.login(username, "MemTempPass1").token
        self.svc.change_password(token, "MemTempPass1", "MemPassFinal1")
        return self.svc.login(username, "MemPassFinal1").token

    # -- acting --------------------------------------------------------------

    def call(self, path, method="GET", token=None, body=None):
        headers = {"Host": "test", "Cookie": f"cow_session={token}",
                   "X-Tenant-ID": self.tenant_id}
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["data"] = json.dumps(body)
        return web_channel.build_web_app().request(path, method=method, **kwargs)

    # -- observing -----------------------------------------------------------

    def catalog(self, token) -> list:
        response = self.call("/api/skills", token=token)
        assert _status(response) == 200, response.data
        return _body(response).get("skills") or []

    def row(self, token, resource_id=RID):
        for skill in self.catalog(token):
            if skill.get("resource_id") == resource_id:
                return skill
        return None

    def body_on_disk(self) -> str:
        with open(os.path.join(self.skills_root, NAME, "SKILL.md"),
                  encoding="utf-8") as fh:
            return fh.read()

    def enabled(self, token) -> bool:
        row = self.row(token)
        assert row is not None, "the skill under test is missing from the catalog"
        return bool(row.get("enabled", True))


# --- fixtures ---------------------------------------------------------------

import pytest


@pytest.fixture
def world():
    built = _World()
    yield built
    built.stop()


# --- the member's grant does not decide the shared surface ------------------

def test_a_member_cannot_rewrite_the_shared_skill_definition(world):
    """The body write is the shared definition, not the caller's own copy.

    Asserted with the file's bytes and the administrator's own read, because the
    claim is "the tenant's definition did not move" — a 403 alone would not say
    that, and the write this closes did not return a 403 at all when the
    installation-shipped read-only provenance did not apply.
    """
    response = world.call("/api/skills/content", method="POST",
                          token=world.member_token,
                          body={"resource_id": RID, "content": REWRITTEN})
    assert _status(response) == 403, response.data
    assert world.body_on_disk() == SEEDED, "the shared definition moved"
    assert ORIGINAL in _body(world.call(
        "/api/skills/content?resource_id=" + RID, token=world.admin_token))["content"]


def test_a_member_cannot_rewrite_it_by_name_either(world):
    """The console addresses skills by name; the name is not a second door."""
    response = world.call("/api/skills/content", method="POST",
                          token=world.member_token,
                          body={"name": NAME, "content": REWRITTEN})
    assert _status(response) == 403, response.data
    assert world.body_on_disk() == SEEDED


def test_a_member_cannot_flip_the_shared_enable_state(world):
    """``skill.enable`` authorizes a resource; it does not authorize the tenant.

    Measured before the scope gate covered the unnamed anchor: this exact call
    returned 200 and the administrator's next catalog read reported the skill
    disabled for the whole tenant.
    """
    before = world.enabled(world.admin_token)
    response = world.call("/api/skills", method="POST", token=world.member_token,
                          body={"action": "close", "resource_id": RID})
    assert _status(response) == 403, response.data
    assert world.enabled(world.admin_token) == before, "the shared state moved"


def test_a_member_cannot_flip_it_by_name_either(world):
    before = world.enabled(world.admin_token)
    response = world.call("/api/skills", method="POST", token=world.member_token,
                          body={"action": "close", "name": NAME})
    assert _status(response) == 403, response.data
    assert world.enabled(world.admin_token) == before


# --- the controls: the same calls, from a caller whose scope covers it -------

def test_the_administrator_flips_the_state_with_the_payload_the_console_sends(
        world):
    """Non-vacuity, in the page's own request shape.

    The console sends ``{action, name}`` with no ``resource_id``. The grant is
    recorded as ``{source}:{name}``, so the toggle used to compare a bare name
    against a normalised id and refuse a caller who held the grant — this pins
    that the shared page is actually usable by the caller who may maintain it.
    """
    assert world.enabled(world.admin_token) is True
    response = world.call("/api/skills", method="POST", token=world.editor_token,
                          body={"action": "close", "name": NAME})
    assert _status(response) == 200, response.data
    assert _body(response)["status"] == "success"
    assert world.enabled(world.admin_token) is False, "the toggle did not land"


def test_the_administrator_rewrites_the_shared_skill_definition(world):
    """The other direction of the same rule: maintenance stays possible.

    A guard that refused everyone would satisfy every test above and still be
    broken, so the permitted path is asserted against the file itself.
    """
    response = world.call("/api/skills/content", method="POST",
                          token=world.editor_token,
                          body={"name": NAME, "content": REWRITTEN})
    assert _status(response) == 200, response.data
    assert REWRITTEN in world.body_on_disk()


def test_an_unknown_action_changes_nothing(world):
    before = world.enabled(world.admin_token)
    response = world.call("/api/skills", method="POST", token=world.editor_token,
                          body={"action": "toggle", "name": NAME})
    assert _status(response) == 200, response.data
    assert _body(response)["status"] == "error"
    assert world.enabled(world.admin_token) == before


def test_the_row_reports_whether_the_caller_may_move_the_global_state(world):
    """The page renders the switch from this flag, so the flag carries the rule.

    Both directions, because a flag that is always false would pass "the member
    is not offered the control" while hiding it from the caller who may use it.
    The flag is the write path's two authorities — management qualification and
    the per-resource ``enable`` grant — so an administrator *without* the grant
    is also reported unavailable, which is what keeps the page from offering a
    request that would be refused for the other reason.
    """
    assert world.row(world.member_token)["actions"] == {"enable": False}
    assert world.row(world.admin_token)["actions"] == {"enable": False}
    assert world.row(world.editor_token)["actions"] == {"enable": True}

