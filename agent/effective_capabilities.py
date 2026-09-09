"""Effective digital-employee capability resolution.

Resolves the *effective* capabilities of an Agent after applying its optional
scene binding, and provides the shared tool filter used by both tool assembly
and tool dispatch so that allow/deny cannot be bypassed.

The resolution model (mirrors OneAgent's EmployeeConfig.merge_with_scene):

- A non-empty Agent field wins over the scene.
- An empty Agent field inherits the scene value when one exists.
- ``tools_denylist`` = Agent denylist ∪ scene denylist.
- ``tools_allowlist``: if the Agent has a non-empty allowlist it wins over the
  scene tools; only when the Agent has no allowlist do we inherit scene tools.
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from agent.registry import AgentProfile


@dataclass(frozen=True)
class EffectiveCapabilities:
    """The merged, effective capabilities for one Agent run."""

    persona_summary: Optional[str] = None
    greeting: Optional[str] = None
    scene_id: Optional[str] = None
    skills: Optional[Tuple[str, ...]] = None
    knowledge_ids: Optional[Tuple[str, ...]] = None
    sops: Tuple[str, ...] = ()
    tools_allowlist: Optional[Tuple[str, ...]] = None
    tools_denylist: Tuple[str, ...] = ()


def _scene_field(scene: Optional[dict], key: str):
    """Return a scene value only when it is a usable (non-empty) value."""
    if not scene:
        return None
    value = scene.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _scene_list(scene: Optional[dict], key: str) -> Tuple[str, ...]:
    if not scene:
        return ()
    value = scene.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(x).strip() for x in value if str(x).strip())


def _merge_denylist(profile_list: Sequence[str], scene_list: Sequence[str]) -> Tuple[str, ...]:
    seen = []
    for v in list(profile_list) + list(scene_list):
        v = v.strip()
        if v and v not in seen:
            seen.append(v)
    return tuple(seen)


def resolve_effective_capabilities(
    profile: AgentProfile, scene: Optional[dict] = None
) -> EffectiveCapabilities:
    """Compute effective capabilities for ``profile`` given an optional ``scene``.

    ``scene`` may be ``None`` (no binding / scene missing). The caller resolves
    the scene dict by ``profile.scene_id`` beforehand.
    """
    # persona_summary: non-empty Agent wins, else inherit scene system_prompt.
    persona = profile.persona_summary or _scene_field(scene, "system_prompt")
    greeting = profile.greeting or _scene_field(scene, "greeting")

    # knowledge_ids: non-empty Agent wins, else inherit scene knowledge if any.
    if profile.knowledge_ids is not None and len(profile.knowledge_ids) > 0:
        knowledge_ids = tuple(profile.knowledge_ids)
    else:
        knowledge_ids = None

    # sops: non-empty Agent wins, else inherit scene sops.
    sops = tuple(profile.sops) if profile.sops else _scene_list(scene, "sops")

    # tools_allowlist: Agent non-empty wins; else inherit scene tools (if any).
    if profile.tools_allowlist and len(profile.tools_allowlist) > 0:
        allowlist = tuple(profile.tools_allowlist)
    else:
        scene_tools = _scene_list(scene, "tools")
        allowlist = tuple(scene_tools) if scene_tools else None

    # tools_denylist: union of Agent and scene.
    denylist = _merge_denylist(profile.tools_denylist, _scene_list(scene, "tools_denylist"))

    # skills: keep the profile's explicit selection (None meaning "all").
    skills = profile.skills

    return EffectiveCapabilities(
        persona_summary=persona,
        greeting=greeting,
        scene_id=profile.scene_id,
        skills=skills,
        knowledge_ids=knowledge_ids,
        sops=sops,
        tools_allowlist=allowlist,
        tools_denylist=denylist,
    )


def filter_tools(tools: Iterable[str], effective: EffectiveCapabilities) -> List[str]:
    """Filter a tool-name list by the effective allow/deny lists.

    Both a non-empty allowlist and the denylist apply. When there is no
    allowlist, only the denylist constrains the set.
    """
    result = []
    for name in tools:
        if is_tool_allowed(name, effective):
            result.append(name)
    return result


def is_tool_allowed(name: str, effective: EffectiveCapabilities) -> bool:
    """Return True if ``name`` may be used under ``effective``.

    A non-empty allowlist requires the tool to be listed; otherwise default
    tools are all allowed. In either case a denylisted tool is rejected.
    """
    name = name.strip()
    if name in effective.tools_denylist:
        return False
    if effective.tools_allowlist:
        return name in effective.tools_allowlist
    return True
