# encoding:utf-8
"""Task 5.4: a bare skill name that means two definitions is refused, not resolved.

The registry is keyed by skill *name*, and the loader gave a workspace skill
precedence over a builtin one by overwriting the map entry. Precedence alone is
enough to *pick* a definition, but it also destroyed the evidence that a choice
was being made: after the load, a bare ``name`` looked exactly like a unique
resource, even when two files answered to it.

That matters because a skill is authorized as ``{source}:{name}``
(``specs/tenant-skills-tools-console``, 同名技能要求明确来源): the grant recorded
for ``builtin:knowledge-wiki`` is not a grant on a tenant's skill of the same
name, and a request that carries only a name cannot say which one it means. The
spec refuses it — 400, nothing executed, pass ``resource_id`` — while a request
that does name a source must authorize and touch exactly that source.

The ``…is_not_ambiguous`` tests are the controls, and one of them is not
decoration: ``app.py`` copies every builtin skill directory into the workspace
at startup (``_sync_builtin_skills``), so for a builtin the winner of a name
collision is normally that copy of *the same* skill, which the product already
reads as read-only installation content (``SkillManager.ships_with_install``). A
guard that treated every same-name pair as ambiguous would refuse every builtin
in the product, and that control is what fails when it does.
"""

from __future__ import annotations

import json
import os

import pytest

from tests._helpers import WebAppHarness

from agent.skills.manager import SkillManager, SkillNameAmbiguous

#: A builtin that really ships with the installation, so the ambiguity below is
#: not synthetic: the tenant authors its own skill under the same name.
BUILTIN_NAME = "knowledge-wiki"
#: A tenant-authored skill nobody else uses — the control that a name which
#: resolves to exactly one definition still works by name.
UNIQUE_NAME = "tenant-note"


def _md(name, description, body):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


def _write_skill(root, dirname, name, description, body):
    """Write ``<root>/<dirname>/SKILL.md`` and return the skill's directory."""
    directory = os.path.join(root, dirname)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(_md(name, description, body))
    return directory


def _manager(tmp_path, *, builtins=(), customs=()):
    builtin_dir = os.path.join(str(tmp_path), "builtin")
    custom_dir = os.path.join(str(tmp_path), "custom")
    for dirname, name, description, body in builtins:
        _write_skill(builtin_dir, dirname, name, description, body)
    for dirname, name, description, body in customs:
        _write_skill(custom_dir, dirname, name, description, body)
    return SkillManager(builtin_dir=builtin_dir, custom_dir=custom_dir)


# --- the definition that lost is still visible ------------------------------

def test_a_shadowed_definition_is_recorded_rather_than_lost(tmp_path):
    """The override must stay distinguishable, or nothing downstream can refuse.

    Asserted on the entry, not on a status code: "the name is ambiguous" is a
    claim about the loader having kept both definitions in view.
    """
    manager = _manager(
        tmp_path,
        builtins=[("dup", "dup", "Builtin dup.", "BUILTIN BODY")],
        customs=[("tenant-dup", "dup", "Tenant dup.", "TENANT BODY")],
    )
    entry = manager.get_skill("dup")
    assert entry is not None
    assert entry.shadowed is not None, "the replaced definition was dropped again"
    assert entry.shadowed.skill.source == "builtin"
    assert manager.ambiguous_sources(entry) == ["builtin"]


def test_the_ambiguous_name_is_refused_rather_than_resolved_by_override_order(tmp_path):
    """A bare name is refused, and the refusal says how to be unambiguous."""
    manager = _manager(
        tmp_path,
        builtins=[("dup", "dup", "Builtin dup.", "BUILTIN BODY")],
        customs=[("tenant-dup", "dup", "Tenant dup.", "TENANT BODY")],
    )
    with pytest.raises(SkillNameAmbiguous) as raised:
        manager.resolve_skill(name="dup")
    assert "resource_id" in str(raised.value)
    assert raised.value.name == "dup"


def test_each_definition_is_still_addressable_by_resource_id(tmp_path):
    """The remedy the 400 asks for has to reach the source the caller named.

    Both directions, because serving the winner for *both* ids would look like a
    working fix while still spending one definition's request on the other.
    """
    manager = _manager(
        tmp_path,
        builtins=[("dup", "dup", "Builtin dup.", "BUILTIN BODY")],
        customs=[("tenant-dup", "dup", "Tenant dup.", "TENANT BODY")],
    )
    builtin_entry = manager.resolve_skill(resource_id="builtin:dup")
    custom_entry = manager.resolve_skill(resource_id="custom:dup")
    assert builtin_entry is not None and custom_entry is not None
    assert builtin_entry.skill.source == "builtin"
    assert custom_entry.skill.source == "custom"
    assert builtin_entry.skill.base_dir != custom_entry.skill.base_dir
    assert "BUILTIN BODY" in builtin_entry.skill.content
    assert "TENANT BODY" in custom_entry.skill.content


# --- controls: a name that identifies one definition is untouched -----------

def test_a_unique_name_resolves_by_name(tmp_path):
    manager = _manager(
        tmp_path,
        builtins=[("solo", "solo", "Builtin solo.", "SOLO BODY")],
        customs=[("mine", "mine", "Tenant mine.", "MINE BODY")],
    )
    assert manager.ambiguous_sources(manager.get_skill("solo")) == []
    assert manager.resolve_skill(name="solo").skill.source == "builtin"
    assert manager.resolve_skill(name="mine").skill.source == "custom"


def test_the_installations_own_copy_of_a_builtin_is_not_ambiguous(tmp_path):
    """``_sync_builtin_skills`` copies builtins into the workspace by directory.

    That copy is the installation's content by a workspace path — the product
    already says so on the read/write path (``ships_with_install``) — so it is
    one resource, not two, and its bare name keeps working. Asserted with a
    *differing* body on purpose: an edited copy is still the installation's
    skill, and a guard that keyed on the files differing would refuse it (and
    with it every builtin whose copy anyone ever touched).
    """
    manager = _manager(
        tmp_path,
        builtins=[("solo", "solo", "Builtin solo.", "SOLO BODY")],
        customs=[("solo", "solo", "Builtin solo, edited.", "EDITED SOLO BODY")],
    )
    entry = manager.resolve_skill(name="solo")
    assert entry is not None, "the installation's own copy made its name unusable"
    assert entry.shadowed is not None
    assert manager.ambiguous_sources(entry) == []
    assert manager.ships_with_install(entry.skill) is True


# --- the wire: 400 for the ambiguous name, the named source for the id -------

@pytest.fixture
def world(tmp_path):
    """A real console over a tenant that authors its own ``knowledge-wiki``.

    The tenant's file sits in a directory of its own (``tenant-wiki``) but names
    itself after the builtin, which is what makes the name ambiguous on the wire
    without being the installation's copy. The unique skill rides along so every
    refusal below has a neighbour that must still succeed.
    """
    harness = WebAppHarness(tmp_path)
    skills_root = os.path.join(harness.shared_root, "skills")
    _write_skill(skills_root, "tenant-wiki", BUILTIN_NAME,
                 "Tenant knowledge wiki.", "TENANT KNOWLEDGE BODY")
    _write_skill(skills_root, UNIQUE_NAME, UNIQUE_NAME,
                 "Tenant authored note.", "TENANT NOTE BODY")
    harness.root_token = harness.login("root")
    yield harness
    harness.close()


def _status(response) -> int:
    return int(str(response.status).split()[0])


def _enabled(world, resource_id) -> bool:
    """The stored enable state of one skill, read from the catalog."""
    response = world.get("/api/skills", token=world.root_token)
    assert _status(response) == 200, response.data
    rows = [s for s in WebAppHarness.json(response).get("skills") or []
            if s.get("resource_id") == resource_id]
    assert len(rows) == 1, f"expected one row for {resource_id}: {rows}"
    return bool(rows[0].get("enabled", True))


def test_a_bare_name_that_means_two_definitions_is_refused(world):
    """400, and the refused request left the stored state alone."""
    before = _enabled(world, f"custom:{BUILTIN_NAME}")
    response = world.post("/api/skills", {"action": "close", "name": BUILTIN_NAME},
                          token=world.root_token)
    assert _status(response) == 400, response.data
    body = WebAppHarness.json(response)
    assert body["code"] == "skill_name_ambiguous"
    assert "resource_id" in body["message"]
    assert _enabled(world, f"custom:{BUILTIN_NAME}") == before, \
        "the refused request moved the enable state"


def test_the_same_toggle_lands_when_the_caller_names_the_source(world):
    """The remedy the 400 asks for, with the effect as the witness."""
    assert _enabled(world, f"custom:{BUILTIN_NAME}") is True
    response = world.post("/api/skills",
                          {"action": "close",
                           "resource_id": f"custom:{BUILTIN_NAME}"},
                          token=world.root_token)
    assert _status(response) == 200, response.data
    assert _enabled(world, f"custom:{BUILTIN_NAME}") is False


def test_a_content_read_by_name_is_refused_and_by_id_reaches_one_source(world):
    """The read direction of the same rule, asserted on the text itself.

    ``builtin:`` must serve the installation's definition and ``custom:`` the
    tenant's — a resolver that answered both ids with the winner's file would
    pass a status-code test and still borrow one source for the other.
    """
    ambiguous = world.get(f"/api/skills/content?name={BUILTIN_NAME}",
                          token=world.root_token)
    assert _status(ambiguous) == 400, ambiguous.data
    assert WebAppHarness.json(ambiguous)["code"] == "skill_name_ambiguous"

    custom = world.get(f"/api/skills/content?resource_id=custom:{BUILTIN_NAME}",
                       token=world.root_token)
    assert _status(custom) == 200, custom.data
    assert "TENANT KNOWLEDGE BODY" in WebAppHarness.json(custom)["content"]

    builtin = world.get(f"/api/skills/content?resource_id=builtin:{BUILTIN_NAME}",
                        token=world.root_token)
    assert _status(builtin) == 200, builtin.data
    body = WebAppHarness.json(builtin)
    assert "TENANT KNOWLEDGE BODY" not in body["content"]
    assert "knowledge" in body["content"].lower()


def test_a_unique_name_still_toggles_and_reads_by_name(world):
    """Non-vacuity: the refusals above must not have become "refuse everything"."""
    response = world.get(f"/api/skills/content?name={UNIQUE_NAME}",
                         token=world.root_token)
    assert _status(response) == 200, response.data
    assert "TENANT NOTE BODY" in WebAppHarness.json(response)["content"]

    assert _enabled(world, f"custom:{UNIQUE_NAME}") is True
    toggled = world.post("/api/skills",
                         {"action": "close", "name": UNIQUE_NAME},
                         token=world.root_token)
    assert _status(toggled) == 200, toggled.data
    assert _enabled(world, f"custom:{UNIQUE_NAME}") is False
