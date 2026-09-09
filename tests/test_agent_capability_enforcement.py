"""Agent capability enforcement tests (task 2.7).

Covers the digital-employee runtime enforcement matrix: tools_allowlist /
tools_denylist, forged call rejection, legacy-config backward compatibility,
and scene-based inheritance. These exercise the shared primitives in
``agent.effective_capabilities`` and the real ``initialize_agent`` / ``get_agent``
paths so an allow/deny policy cannot be bypassed at assembly or dispatch time.
"""
import os

import pytest

from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from agent.effective_capabilities import (
    EffectiveCapabilities,
    filter_tools,
    is_tool_allowed,
    resolve_effective_capabilities,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def profile(**overrides) -> AgentProfile:
    base = dict(id="p", name="P", workspace="/tmp/cow")
    base.update(overrides)
    return AgentProfile(**base)


def scene(**overrides) -> dict:
    s = {"id": "procurement", "name": "采购", "system_prompt": "采购专家"}
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# tools_allowlist
# ---------------------------------------------------------------------------
def test_allowlist_only_exposes_listed_tools():
    eff = resolve_effective_capabilities(profile(tools_allowlist=["read"]), scene=None)
    assert filter_tools(["read", "bash", "write"], eff) == ["read"]


def test_allowlist_absent_keeps_default_tools():
    eff = resolve_effective_capabilities(profile(), scene=None)
    assert filter_tools(["read", "bash", "write"], eff) == ["read", "bash", "write"]


# ---------------------------------------------------------------------------
# tools_denylist
# ---------------------------------------------------------------------------
def test_denylist_rejects_even_with_allowlist():
    eff = resolve_effective_capabilities(
        profile(tools_allowlist=["read", "bash"], tools_denylist=["bash"]), scene=None
    )
    assert filter_tools(["read", "bash"], eff) == ["read"]


def test_empty_denylist_is_noop():
    eff = resolve_effective_capabilities(profile(), scene=None)
    assert is_tool_allowed("bash", eff) is True


# ---------------------------------------------------------------------------
# Forged call (dispatch guard)
# ---------------------------------------------------------------------------
def test_is_tool_allowed_denies_forged_call():
    eff = resolve_effective_capabilities(profile(tools_allowlist=["read"]), scene=None)
    assert is_tool_allowed("bash", eff) is False


# ---------------------------------------------------------------------------
# Legacy-config backward compatibility
# ---------------------------------------------------------------------------
def test_legacy_config_defaults_to_unrestricted():
    # A config with none of the new fields must behave exactly as before.
    eff = resolve_effective_capabilities(profile(), scene=None)
    assert eff.tools_allowlist is None
    assert eff.tools_denylist == ()
    assert filter_tools(["read", "bash"], eff) == ["read", "bash"]


# ---------------------------------------------------------------------------
# Scene inheritance / override / merge
# ---------------------------------------------------------------------------
def test_scene_inherits_empty_persona():
    eff = resolve_effective_capabilities(profile(scene_id="s"), scene=scene())
    assert eff.persona_summary == "采购专家"


def test_profile_overrides_scene_persona():
    eff = resolve_effective_capabilities(
        profile(scene_id="s", persona_summary="员工人设"), scene=scene()
    )
    assert eff.persona_summary == "员工人设"


def test_denylist_merges_profile_and_scene():
    eff = resolve_effective_capabilities(
        profile(scene_id="s", tools_denylist=["bash"]),
        scene=scene(tools_denylist=["rm"]),
    )
    assert set(eff.tools_denylist) == {"bash", "rm"}


def test_no_scene_leaves_fields_default():
    eff = resolve_effective_capabilities(profile(), scene=None)
    assert eff.tools_allowlist is None
    assert eff.persona_summary is None


# ---------------------------------------------------------------------------
# Runtime: assembly filter + persona injection (real agent paths)
# ---------------------------------------------------------------------------
@pytest.fixture
def gated_registry(tmp_path):
    registry = AgentRegistry(
        [
            AgentProfile(
                "gated",
                "Gated",
                str(tmp_path / "gated"),
                tools_allowlist=["read"],
                tools_denylist=["bash"],
                persona_summary="我只做读取",
            ),
            AgentProfile("open", "Open", str(tmp_path / "open")),
        ],
        default_agent_id="gated",
    )
    set_agent_registry(registry)
    yield registry
    set_agent_registry(None)


def test_assembly_drops_denied_tool(gated_registry, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    initializer = AgentBridge(Bridge()).initializer
    with identity_scope(agent_id="gated"):
        agent = initializer.initialize_agent(session_id="s1")
    names = {t.name for t in agent.tools}
    assert "bash" not in names
    assert "read" in names


def test_assembly_unrestricted_keeps_default_tools(gated_registry, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    initializer = AgentBridge(Bridge()).initializer
    with identity_scope(agent_id="open"):
        agent = initializer.initialize_agent(session_id="s1")
    names = {t.name for t in agent.tools}
    assert "bash" in names


def test_get_agent_injects_persona_suffix(gated_registry, tmp_path):
    from bridge.agent_bridge import AgentBridge
    from bridge.bridge import Bridge
    from common.runtime_identity import identity_scope

    bridge = AgentBridge(Bridge())
    with identity_scope(agent_id="gated"):
        agent = bridge.get_agent(session_id="s1")
    suffix = getattr(agent, "extra_system_suffix", None) or ""
    assert "员工人设" in suffix
    assert "我只做读取" in suffix
