from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_web_backend_exposes_agent_and_core_file_routes():
    # The URL table is derived from the single route registry (change group 2),
    # so assert the *binding* instead of grepping a source literal -- this also
    # proves the pattern actually reaches its handler.
    from channel.web import web_channel

    urls = list(web_channel._WEB_URLS)
    for pattern, handler in (
        ("/api/agents", "AgentsHandler"),
        ("/api/agents/([^/]+)/files/([^/]+)", "AgentCoreFileHandler"),
        ("/api/agents/([^/]+)/avatar", "AgentAvatarHandler"),
    ):
        assert pattern in urls, pattern
        assert urls[urls.index(pattern) + 1] == handler, pattern

    source = _read("channel/web/web_channel.py")
    assert "class AgentsHandler:" in source
    assert "class AgentCoreFileHandler:" in source
    assert "scope" in source and "_list_sessions_across_agents" in source


def test_console_has_agent_cards_not_a_tenant_switcher():
    html = _read("channel/web/chat.html")
    assert 'id="agent-selector"' not in html
    # The team is a top-level view of its own now, not a Settings panel: it is
    # where you compose and manage the Agents you work with.
    assert 'data-view="agents"' in html
    assert 'id="view-agents"' in html
    assert 'id="agents-grid"' in html
    assert 'id="agent-core-editor"' in html
    assert 'id="composer-agent-btn"' in html
    # The core-file picker is the same dropdown component used elsewhere in the
    # console, not a native <select>; its options are built in JS from
    # _agentCoreFileOptions() rather than hardcoded <option> tags in markup.
    assert 'id="agent-core-file" class="cfg-dropdown cfg-dropdown-xs"' in html
    js = _read("channel/web/static/js/console.js")
    for filename in ("AGENT.md", "USER.md", "RULE.md", "MEMORY.md"):
        assert f"value: '{filename}'" in js
    # BOOTSTRAP.md is internal and deliberately left out of the hand-editable
    # picker.
    assert "value: 'BOOTSTRAP.md'" not in js


def test_console_carries_agent_id_through_existing_feature_requests():
    source = _read("channel/web/static/js/console.js")
    assert "body.agent_id = activeAgentId" in source
    assert "agent_id=${encodeURIComponent(activeAgentId)}" in source
    assert "function runtimeSessionKey" in source
    assert "scope=all" in source
    assert "function startChatWithAgent" in source
    assert "function bindChannelAgent" in source


def test_workspace_scoped_web_services_resolve_selected_agent():
    import re

    source = _read("channel/web/web_channel.py")
    assert "def _get_workspace_root(session_id: str = None, agent_id: str = None)" in source
    assert "project_store.get_project_dir(session_id, agent_id)" in source
    assert "get_agent_registry().get(agent_id).workspace" in source
    assert "_get_workspace_root(agent_id=agent_id)" in source  # file panel / preview
    assert "get_scheduler_service(agent_id=agent_id)" in source


def test_session_scoped_stores_resolve_the_addressed_agent():
    """Sessions live one database per Agent workspace.

    The conversation store must therefore be opened from that Agent's
    workspace, never from a workspace root: in database mode the root is the
    caller's tenant shared root, so writes would miss the sessions the list is
    showing and answer ``session not found``.
    """
    import re

    source = _read("channel/web/web_channel.py")
    assert "def _conversation_store_for(agent_id: Optional[str])" in source
    assert "get_agent_registry().get(agent_id or None).workspace" in source
    assert "store = _conversation_store_for(agent_id)" in source
    assert not re.search(r"get_conversation_store\(\s*_get_workspace_root\(", source), (
        "a session store is resolved from the workspace root again"
    )
