"""Agent digital-employee capability resolution tests.

Covers AgentProfile new-field parsing, effective-capability resolution (with
scene inheritance / override / denylist merge), and the tool filter used by
both assembly and dispatch. Written test-first per TDD: the production code in
``agent/effective_capabilities.py`` does not exist yet.
"""
import pytest

from agent.registry import AgentProfile, AgentRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_profile(**overrides) -> AgentProfile:
    base = dict(
        id="proc",
        name="采购专员",
        workspace="/tmp/cow",
        description="负责采购",
    )
    base.update(overrides)
    return AgentProfile(**base)


def sample_scene(**overrides) -> dict:
    scene = {
        "id": "procurement",
        "name": "采购执行",
        "category": "procurement",
        "system_prompt": "你是采购专家",
        "skill_name": "procurement-supplier-risk",
        "required_permission": "",
    }
    scene.update(overrides)
    return scene


# ---------------------------------------------------------------------------
# 1. Profile field parsing (TDD requirement 1.2)
# ---------------------------------------------------------------------------
def test_profile_parses_digital_employee_fields():
    profile = make_profile(
        position="采购专员",
        category="procurement",
        tags=["供应商", "招标"],
        greeting="你好，我是采购专员。",
        persona_summary="语气专业，先确认需求。",
        scene_id="procurement",
        tools_allowlist=["web_search", "web_fetch"],
        tools_denylist=["bash"],
        sops=["check-prd"],
    )
    assert profile.position == "采购专员"
    assert profile.category == "procurement"
    assert profile.tags == ["供应商", "招标"]
    assert profile.greeting == "你好，我是采购专员。"
    assert profile.persona_summary == "语气专业，先确认需求。"
    assert profile.scene_id == "procurement"
    assert profile.tools_allowlist == ["web_search", "web_fetch"]
    assert profile.tools_denylist == ["bash"]
    assert profile.sops == ["check-prd"]


def test_profile_defaults_are_empty_and_backward_compatible():
    profile = make_profile()
    assert profile.position is None
    assert profile.category is None
    assert profile.tags == ()
    assert profile.greeting is None
    assert profile.persona_summary is None
    assert profile.scene_id is None
    assert profile.tools_allowlist is None  # None = no allowlist (default behavior)
    assert profile.tools_denylist == ()
    assert profile.sops == ()


def test_registry_builds_profile_with_digital_employee_fields(tmp_path):
    registry = AgentRegistry.from_config(
        {
            "agent_workspace": str(tmp_path),
            "default_agent_id": "proc",
            "agents": [
                {
                    "id": "proc",
                    "name": "采购专员",
                    "position": "采购专员",
                    "category": "procurement",
                    "tags": ["供应商"],
                    "persona_summary": "语气专业",
                    "scene_id": "procurement",
                    "tools_allowlist": ["web_search"],
                    "tools_denylist": ["bash"],
                    "sops": ["check-prd"],
                    "knowledge_ids": ["docs/kb1"],
                },
            ],
        }
    )
    p = registry.get("proc")
    assert p.position == "采购专员"
    assert p.category == "procurement"
    assert p.tags == ("供应商",)
    assert p.persona_summary == "语气专业"
    assert p.scene_id == "procurement"
    assert p.tools_allowlist == ("web_search",)
    assert p.tools_denylist == ("bash",)
    assert p.sops == ("check-prd",)
    assert p.knowledge_ids == ("docs/kb1",)


# ---------------------------------------------------------------------------
# 2. Effective capability resolution (scene inheritance / override / merge)
#    (TDD requirement 1.4). Production code lives in agent/effective_capabilities.py.
# ---------------------------------------------------------------------------
def test_resolution_inherits_empty_fields_from_scene():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile(scene_id="procurement")
    effective = resolve_effective_capabilities(profile, scene=sample_scene())
    assert effective.persona_summary
    assert effective.scene_id == "procurement"


def test_resolution_nonempty_profile_overrides_scene():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile(
        scene_id="procurement",
        persona_summary="员工人设",
    )
    effective = resolve_effective_capabilities(profile, scene=sample_scene())
    assert effective.persona_summary == "员工人设"


def test_resolution_denylist_merges_profile_and_scene():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile(
        scene_id="procurement",
        tools_denylist=["bash", "ssh"],
        tools_allowlist=["web_search"],
    )
    scene = sample_scene(tools_denylist=["bash", "rm"])
    effective = resolve_effective_capabilities(profile, scene=scene)
    assert set(effective.tools_denylist) == {"bash", "ssh", "rm"}
    assert effective.tools_allowlist == ("web_search",)


def test_resolution_no_allowlist_uses_scene_tools_when_present():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile(scene_id="procurement")  # no allowlist
    scene = sample_scene(tools=["web_search", "web_fetch"])
    effective = resolve_effective_capabilities(profile, scene=scene)
    assert effective.tools_allowlist == ("web_search", "web_fetch")


def test_resolution_no_scene_leaves_fields_empty():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile()  # no scene_id
    effective = resolve_effective_capabilities(profile, scene=None)
    assert effective.tools_allowlist is None
    assert effective.tools_denylist == ()
    assert effective.persona_summary is None


def test_resolution_skills_selection_tracks_profile():
    from agent.effective_capabilities import resolve_effective_capabilities

    profile = make_profile(skills=("xlsx", "docx"))
    effective = resolve_effective_capabilities(profile, scene=None)
    assert effective.skills == ("xlsx", "docx")


# ---------------------------------------------------------------------------
# 3. Tool filtering (TDD requirement 2.1 / 2.2). Production code lives in
#    agent/effective_capabilities.py (filter_tools_allowed) so it can be shared
#    by assembly and dispatch.
# ---------------------------------------------------------------------------
def test_filter_tools_allowlist_removes_disallowed():
    from agent.effective_capabilities import filter_tools

    effective = make_profile(tools_allowlist=["web_search"])
    allowed = filter_tools(["web_search", "bash", "read"], effective)
    assert allowed == ["web_search"]


def test_filter_tools_allowlist_none_keeps_all_and_applies_denylist():
    from agent.effective_capabilities import filter_tools

    effective = make_profile(tools_denylist=["bash"])
    allowed = filter_tools(["web_search", "bash", "read"], effective)
    assert allowed == ["web_search", "read"]


def test_filter_tools_denylist_excludes_even_with_allowlist():
    from agent.effective_capabilities import filter_tools

    effective = make_profile(
        tools_allowlist=["web_search", "bash"],
        tools_denylist=["bash"],
    )
    allowed = filter_tools(["web_search", "bash", "read"], effective)
    assert allowed == ["web_search"]


def test_is_tool_allowed_denies_forged_call():
    from agent.effective_capabilities import is_tool_allowed

    effective = make_profile(tools_allowlist=["web_search"], tools_denylist=[])
    assert is_tool_allowed("web_search", effective) is True
    assert is_tool_allowed("bash", effective) is False
    assert is_tool_allowed("read", effective) is False


# ---------------------------------------------------------------------------
# 4. Admin persistence: new fields survive update_agent / create_agent round-trip
#    (TDD requirement 1.3). Uses the real AgentAdminService.
# ---------------------------------------------------------------------------
def test_admin_update_agent_persists_digital_employee_fields(tmp_path, monkeypatch):
    import json
    from agent.admin import AgentAdminService

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"agent_workspace": str(tmp_path)}), encoding="utf-8")
    svc = AgentAdminService(str(config_path))
    created = svc.create_agent("proc", "采购专员")
    assert created["id"] == "proc"

    # A real scene may not be registered in the test env; treat "procurement"
    # as valid so the persistence round-trip (not scene lookup) is under test.
    monkeypatch.setattr("agent.admin._scene_exists", lambda _id: True)

    # Put the roster file on disk so _load() reads it back. create_agent writes it.
    updated = svc.update_agent(
        "proc",
        position="采购专员",
        category="procurement",
        tags=["供应商"],
        persona_summary="语气专业",
        scene_id="procurement",
        tools_allowlist=["web_search"],
        tools_denylist=["bash"],
    )
    assert updated["position"] == "采购专员"
    assert updated["category"] == "procurement"
    assert updated["tags"] == ["供应商"]
    assert updated["persona_summary"] == "语气专业"
    assert updated["scene_id"] == "procurement"
    assert updated["tools_allowlist"] == ["web_search"]
    assert updated["tools_denylist"] == ["bash"]

    # Round-trip: reload from disk and confirm fields survive.
    svc2 = AgentAdminService(str(config_path))
    reloaded = next(a for a in svc2.snapshot()["agents"] if a["id"] == "proc")
    assert reloaded["position"] == "采购专员"
    assert reloaded["persona_summary"] == "语气专业"
    assert reloaded["tools_allowlist"] == ["web_search"]


# ---------------------------------------------------------------------------
# 5. Admin rejects an invalid scene_id (TDD requirement 1.3)
# ---------------------------------------------------------------------------
def test_admin_rejects_invalid_scene_id(tmp_path, monkeypatch):
    import json
    from agent.admin import AgentAdminService, AgentAdminError

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"agent_workspace": str(tmp_path)}), encoding="utf-8")
    svc = AgentAdminService(str(config_path))
    svc.create_agent("proc", "采购专员")

    monkeypatch.setattr("agent.admin._scene_exists", lambda _id: False)
    with pytest.raises(AgentAdminError):
        svc.update_agent("proc", scene_id="nope")


# ---------------------------------------------------------------------------
# 6. Runtime wiring: assembly filter + dispatch guard + persona injection.
#    Uses the real initialize_agent body like test_agent_initializer_routing.
# ---------------------------------------------------------------------------
@pytest.fixture
def restricted_agent(tmp_path):
    from agent.registry import AgentRegistry, set_agent_registry
    import os

    def realpath(p):
        return os.path.realpath(str(p))

    registry = AgentRegistry(
        [
            AgentProfile(
                "gated",
                "Gated",
                str(tmp_path / "gated"),
                tools_allowlist=["read"],
                tools_denylist=["bash"],
                persona_summary="我只做读取",
                sops=["check-po", "check-prd"],
            ),
            AgentProfile("open", "Open", str(tmp_path / "open")),
        ],
        default_agent_id="gated",
    )
    set_agent_registry(registry)
    yield registry
    set_agent_registry(None)


def test_initialize_agent_filters_disallowed_tools(restricted_agent, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    initializer = AgentBridge(Bridge()).initializer
    with identity_scope(agent_id="gated"):
        agent = initializer.initialize_agent(session_id="s1")

    names = set()
    for t in agent.tools:
        names.add(t.name)
    # bash is denied; read is allowlisted. Neither must leak a forbidden
    # tool into the living tool set even if the assembly order differs.
    assert "bash" not in names
    assert "read" in names


def test_initialize_agent_unfiltered_agent_keeps_tools(restricted_agent, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    initializer = AgentBridge(Bridge()).initializer
    with identity_scope(agent_id="open"):
        agent = initializer.initialize_agent(session_id="s1")

    names = {t.name for t in agent.tools}
    # No policy: default tools remain, including the filesystem ones.
    assert "bash" in names


def test_initialize_agent_injects_persona_into_suffix(restricted_agent, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    bridge = AgentBridge(Bridge())
    with identity_scope(agent_id="gated"):
        # get_agent applies _apply_employee_context after building the agent.
        agent = bridge.get_agent(session_id="s1")

    suffix = getattr(agent, "extra_system_suffix", None) or ""
    assert "员工人设" in suffix
    assert "我只做读取" in suffix


def test_initialize_agent_injects_sops_into_suffix(restricted_agent, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    bridge = AgentBridge(Bridge())
    with identity_scope(agent_id="gated"):
        agent = bridge.get_agent(session_id="s1")

    suffix = getattr(agent, "extra_system_suffix", None) or ""
    assert "SOP" in suffix
    assert "check-po" in suffix


def test_dispatch_guard_rejects_denied_tool(restricted_agent):
    from agent.effective_capabilities import resolve_effective_capabilities, is_tool_allowed

    profile = restricted_agent.get("gated")
    effective = resolve_effective_capabilities(profile, scene=None)
    assert is_tool_allowed("bash", effective) is False
    assert is_tool_allowed("read", effective) is True


# ---------------------------------------------------------------------------
# 7. Roster round-trip: new fields survive team.compact (which drops only
#    implied workspace) so they persist to team.json unchanged.
# ---------------------------------------------------------------------------
def test_team_compact_preserves_digital_employee_fields(tmp_path):
    from agent import team

    settings = {"agent_workspace": str(tmp_path), "default_agent_id": "other"}
    profile = {
        "id": "proc",
        "name": "采购专员",
        "workspace": str(tmp_path / "agents" / "proc"),
        "position": "采购专员",
        "scene_id": "procurement",
        "tools_allowlist": ["read"],
        "tools_denylist": ["bash"],
        "sops": ["check-prd"],
        "persona_summary": "语气专业",
    }
    stored = team.compact([profile], settings, "other")
    assert stored[0]["position"] == "采购专员"
    assert stored[0]["scene_id"] == "procurement"
    assert stored[0]["tools_allowlist"] == ["read"]
    assert stored[0]["tools_denylist"] == ["bash"]
    assert stored[0]["sops"] == ["check-prd"]
    assert stored[0]["persona_summary"] == "语气专业"
    # Only the implied non-default workspace is dropped.
    assert "workspace" not in stored[0]
