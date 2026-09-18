# encoding:utf-8
"""Non-external surfaces stay intact after external-connection work (task 11.5).

``add-external-system-access`` 落在同一张路由表、同一 capability 矩阵和同一工具
装载链上。本文件是**合并回归**：外部连接各自测过了，这里固定"没把别的面弄坏"的
接缝——核心路由仍在、三级覆盖仍绿、矩阵里 scheduler/memory 仍是独立切片、
ToolManager 仍能装载内建工具、AgentRegistry 仍可用，且 ``/api/agents`` 的 policy
没有被外部连接权限污染。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from channel.web import route_registry, web_channel
from channel.web.route_registry import ROUTES, check_route_coverage, derive_route_policy
from tests._helpers import build_identity, personal_target_roster

MASTER_KEY = "ext-nonext-regression-key"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


def _handler_namespace():
    return {name: getattr(web_channel, name)
            for name in dir(web_channel)
            if isinstance(getattr(web_channel, name), type)}


def test_route_registry_still_exposes_core_non_external_routes():
    """Agent/scheduler/memory/tools 路由仍在注册表上。"""
    patterns = {entry.pattern: entry for entry in route_registry.all_routes()}
    for pattern in (
        "/api/agents",
        "/api/scheduler",
        "/api/memory",
        "/api/tools",
    ):
        assert pattern in patterns, "missing core route %r" % pattern
    assert any(entry.pattern.startswith("/api/external-connections")
               for entry in route_registry.all_routes())


def test_route_coverage_has_no_violations_against_web_handlers():
    """三级覆盖不变式：handler 实现面与注册表仍一致。"""
    violations = check_route_coverage(_handler_namespace())
    assert violations == [], violations


def test_capability_matrix_keeps_non_external_slices_separate():
    """scheduler、memory_browse 等仍是独立切片；external_connections 单独存在。"""
    from auth import capability_matrix

    slices = {item.id: item for item in capability_matrix.SLICES}
    for slice_id in ("scheduler", "memory_browse"):
        assert slice_id in slices, "missing non-external slice %r" % slice_id
    assert "external_connections" in slices
    assert slices["external_connections"].id not in {s.consumer for s in slices.values()
                                                   if s.id in ("scheduler", "memory_browse")}


def test_tool_manager_still_loads_builtin_non_mcp_tools():
    """内建工具仍可装载；外部工具是增量的，不能替换 loader。"""
    from agent.tools.tool_manager import ToolManager

    ToolManager.reset_instances()
    try:
        manager = ToolManager()
        manager.load_tools()
        names = set(manager.tool_classes)
        assert names, "ToolManager must register at least one built-in tool"
        # 外部前缀工具不应出现在引擎级 class registry（它们 per-turn 绑定）。
        assert not any(name.startswith("external_") for name in names)
        assert "bash" in names or "read" in names or len(names) >= 3
    finally:
        ToolManager.reset_instances()


def test_agent_registry_still_resolves_agents_in_a_fresh_workspace(tmp_path):
    """AgentRegistry / set_agent_registry 在隔离 workspace 上仍可 smoke。"""
    from agent.registry import AgentRegistry, set_agent_registry

    settings = {
        "default_agent_id": "agent-a",
        "agent_workspace": str(tmp_path / "ws"),
        "agents": [{"id": "agent-a", "name": "Agent A", "enabled": True}],
    }
    Path(settings["agent_workspace"]).mkdir(parents=True, exist_ok=True)
    set_agent_registry(None)
    registry = AgentRegistry.from_config(settings)
    set_agent_registry(registry)
    try:
        profile = registry.get("agent-a")
        assert profile.id == "agent-a"
        assert profile.enabled is True
        assert "agent-a" in {p.id for p in registry.list()}
    finally:
        set_agent_registry(None)


def test_agents_route_policy_is_unchanged_while_external_routes_use_external_slice(
        monkeypatch, tmp_path):
    """/api/agents 仍是 tenant upstream；外部连接走 external_connections 切片。"""
    policy = derive_route_policy()
    agents_get = policy["/api/agents"]["GET"]
    assert agents_get["policy"] == "tenant"
    permission = str(agents_get.get("permission") or "")
    assert not permission.startswith("external.")

    ext_types = policy["/api/external-connections/types"]["GET"]
    assert ext_types.get("slice") == "external_connections" or \
        "external" in str(ext_types.get("permission") or "")

    stack = build_identity(tmp_path)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: stack.service)
    with personal_target_roster("agent-a"):
        # 冒烟：registry 与 identity 仍可协同，不被外部连接 bootstrap 破坏。
        from agent.registry import AgentRegistry, set_agent_registry
        from config import conf

        settings = dict(conf())
        settings["agent_workspace"] = str(tmp_path / "roster-ws")
        settings["agents"] = [{"id": "agent-a", "name": "Agent A", "enabled": True}]
        settings["default_agent_id"] = "agent-a"
        Path(settings["agent_workspace"]).mkdir(parents=True, exist_ok=True)
        set_agent_registry(AgentRegistry.from_config(settings))
        try:
            assert stack.service.list_tenants()
        finally:
            set_agent_registry(None)


def test_external_fork_routes_do_not_replace_upstream_route_sources():
    """外部连接是 fork 路由，不能挤掉 upstream 的 agents/scheduler/memory。"""
    sources = {entry.pattern: entry.source for entry in ROUTES}
    assert sources["/api/agents"] == "upstream"
    assert sources["/api/scheduler"] == "upstream"
    assert sources["/api/memory"] == "upstream"
    assert sources["/api/tools"] == "upstream"
    assert sources["/api/external-connections/types"] == "fork:external-connections"
