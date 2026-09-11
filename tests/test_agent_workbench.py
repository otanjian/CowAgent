"""Workbench (use-Agents) projection behaviour.

The workbench page reads a minimal whitelisted projection from
``/api/agents?view=workbench``. These tests cover:

- the default snapshot is returned when `view` is omitted (old-client contract)
- the projection only carries card fields, never workspace/channels/files
- only saved + enabled Agents appear, and the default Agent is flagged
- a no-param read keeps the full management snapshot

No real registry is used: ``get_agent_registry`` is patched with a small stub
whose ``list()`` returns a couple of ``AgentProfile``-like objects.
"""

import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "web" not in sys.modules:
    web_stub = types.ModuleType("web")
    web_stub.HTTPError = type("HTTPError", (Exception,), {})
    web_stub.cookies = lambda: {}
    web_stub.header = lambda *args, **kwargs: None
    web_stub.data = lambda: b"{}"
    web_stub.input = lambda **kwargs: types.SimpleNamespace(**kwargs)
    web_stub.setcookie = lambda *args, **kwargs: None
    web_stub.storage = lambda **kwargs: types.SimpleNamespace(**kwargs)
    sys.modules["web"] = web_stub


class _Profile:
    def __init__(self, id, name, enabled=True, description=None, avatar=None,
                 position=None, category=None, tags=None):
        self.id = id
        self.name = name
        self.workspace = f"/tmp/{id}"
        self.enabled = enabled
        self.description = description
        self.avatar = avatar
        self.position = position
        self.category = category
        self.tags = tags or ()


class _Registry:
    default_agent_id = "primary"

    def __init__(self, profiles):
        self._profiles = profiles

    def list(self):
        return self._profiles


def _call_get(handler_cls, view=""):
    import channel.web.web_channel as web_channel
    with patch.object(web_channel, "_require_auth"), \
         patch.object(web_channel.web, "header"), \
         patch.object(web_channel.web, "input", return_value=web_channel.web.storage(view=view)):
        return json.loads(handler_cls().GET())


class TestWorkbenchProjection(unittest.TestCase):

    def test_view_workbench_returns_whitelisted_fields_only(self):
        from channel.web.web_channel import AgentsHandler
        profiles = [
            _Profile("primary", "Primary", enabled=True, description="main", avatar=None),
            _Profile("research", "Research", enabled=True, description="researcher"),
            _Profile("archived", "Archived", enabled=False),
        ]
        with patch("agent.registry.get_agent_registry",
                   return_value=_Registry(profiles)):
            data = _call_get(AgentsHandler, view="workbench")

        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["agents"]), 2)  # archived excluded
        fields = {"id", "name", "description", "avatar", "is_default", "can_chat",
                  "unavailable_reason", "position", "category", "tags"}
        for agent in data["agents"]:
            self.assertEqual(set(agent.keys()), fields,
                             "workbench projection must be a strict whitelist")
        # Digital-employee fields project onto the cards.
        for agent in data["agents"]:
            self.assertEqual(agent["position"], "")
            self.assertEqual(agent["category"], "")
            self.assertEqual(agent["tags"], [])
        # No management data leaks into the projection.
        self.assertNotIn("workspace", data)
        self.assertNotIn("channel_instances", data)
        self.assertNotIn("revision", data)
        # Default Agent is flagged.
        by_id = {a["id"]: a for a in data["agents"]}
        self.assertTrue(by_id["primary"]["is_default"])
        self.assertFalse(by_id["research"]["is_default"])
        # In legacy mode an enabled Agent can chat.
        self.assertTrue(all(a["can_chat"] for a in data["agents"]))

    def test_view_workbench_filters_disabled(self):
        from channel.web.web_channel import AgentsHandler
        profiles = [
            _Profile("primary", "Primary", enabled=True),
            _Profile("research", "Research", enabled=False),
        ]
        with patch("agent.registry.get_agent_registry",
                   return_value=_Registry(profiles)):
            data = _call_get(AgentsHandler, view="workbench")
        self.assertEqual([a["id"] for a in data["agents"]], ["primary"])

    def test_default_view_returns_full_snapshot(self):
        from channel.web.web_channel import AgentsHandler
        snapshot = {
            "default_agent_id": "primary",
            "agents": [{"id": "primary", "workspace": "/tmp/x"}],
            "channel_instances": [],
            "revision": "abc",
        }
        with patch("channel.web.web_channel._agent_admin_service") as svc:
            svc.return_value.snapshot.return_value = snapshot
            data = _call_get(AgentsHandler, view="")
        self.assertEqual(data, {"status": "success", **snapshot})

    def test_default_agent_is_first_even_when_registry_order_differs(self):
        from channel.web.web_channel import AgentsHandler
        registry = _Registry([_Profile("aaa", "Other"), _Profile("primary", "Default")])
        with patch("agent.registry.get_agent_registry", return_value=registry):
            data = _call_get(AgentsHandler, view="workbench")
        self.assertEqual([a["id"] for a in data["agents"]], ["primary", "aaa"])

    def test_readiness_defaults_to_runnable_in_legacy(self):
        from channel.web.web_channel import _workbench_chat_readiness
        self.assertEqual(_workbench_chat_readiness(None, "any-agent"), (True, None))

    def test_readiness_database_requires_permission(self):
        """Database mode: read-only caller gets a permission reason, never the
        old ``runtime_not_enabled`` version closure."""
        from auth.runtime import RequestContext
        from channel.web.web_channel import _workbench_chat_readiness

        def _ctx(**over):
            base = dict(user_id="u1", username="u1", display_name="U1",
                        is_platform_admin=False, must_change_password=False,
                        tenant_id="t1", membership=None,
                        permissions={"agent.read"}, is_tenant_admin=False)
            base.update(over)
            return RequestContext(**base)

        class _DenySvc:
            def check_resource_action(self, *a, **kw):
                return False

        with patch("auth.service.get_identity_service",
                   return_value=_DenySvc()):
            can_chat, reason = _workbench_chat_readiness(
                _ctx(), "any-agent")
        self.assertFalse(can_chat)
        self.assertEqual(reason, "permission_denied")

    def test_readiness_database_chat_use_gate(self):
        """Member with agent.use but without chat.use is still not runnable."""
        from auth.runtime import RequestContext
        from channel.web.web_channel import _workbench_chat_readiness

        ctx = RequestContext(
            user_id="u1", username="u1", display_name="U1",
            is_platform_admin=False, must_change_password=False,
            tenant_id="t1", membership=None,
            permissions={"agent.read", "agent.use"}, is_tenant_admin=False)

        class _AllowAgentSvc:
            def check_resource_action(self, *a, **kw):
                return True

            def resolved_default_agent_id(self, tenant_id):
                # This test pins the functional chat.use gate. There is no tenant
                # default to relax onto, so the Agent stays grant-gated here.
                return None

        with patch("auth.service.get_identity_service",
                   return_value=_AllowAgentSvc()):
            can_chat, reason = _workbench_chat_readiness(ctx, "any-agent")
        self.assertFalse(can_chat)
        self.assertEqual(reason, "permission_denied")

    def test_readiness_database_authorized_runnable(self):
        """Platform admin (and a member with both gates) is runnable."""
        from auth.runtime import RequestContext
        from channel.web.web_channel import _workbench_chat_readiness

        admin = RequestContext(
            user_id="root", username="root", display_name="Root",
            is_platform_admin=True, must_change_password=False,
            tenant_id="t1", membership=None,
            permissions={"agent.read"}, is_tenant_admin=False)
        member = RequestContext(
            user_id="u1", username="u1", display_name="U1",
            is_platform_admin=False, must_change_password=False,
            tenant_id="t1", membership=None,
            permissions={"agent.read", "chat.use", "agent.use"},
            is_tenant_admin=False)

        class _AllowSvc:
            def check_resource_action(self, *a, **kw):
                return True

            def resolved_default_agent_id(self, tenant_id):
                # This test pins the explicit-grant path; keep the shared-default
                # relaxation out of the way so the grant check is what passes.
                return None

        with patch("auth.service.get_identity_service", return_value=_AllowSvc()):
            self.assertEqual(_workbench_chat_readiness(admin, "any-agent"),
                             (True, None))
            self.assertEqual(_workbench_chat_readiness(member, "any-agent"),
                             (True, None))


class TestWorkbenchFrontEnd(unittest.TestCase):
    """Source-level checks that the console wires the new view + flow."""

    @staticmethod
    def _read(relative):
        return (Path(__file__).resolve().parents[1] / relative).read_text(encoding="utf-8")

    def _i18n_text(self):
        """The console's merged locale layer: console.js + i18n namespaces."""
        root = Path(__file__).resolve().parents[1] / "channel/web/static/js"
        parts = [self._read("channel/web/static/js/console.js")]
        parts += [p.read_text(encoding="utf-8")
                  for p in sorted((root / "i18n").glob("*.js"))]
        return "\n".join(parts)

    def test_console_reads_workbench_projection(self):
        js = self._read("channel/web/static/js/console.js")
        assert "fetch('/api/agents?view=workbench'," in js
        assert "function loadAgentWorkbench" in js

    def test_chat_has_workbench_view(self):
        html = self._read("channel/web/chat.html")
        assert 'data-view="agent-workbench"' in html
        assert 'id="view-agent-workbench"' in html
        assert 'id="agent-workbench-grid"' in html

    def test_view_meta_moves_agents_to_manage(self):
        js = self._read("channel/web/static/js/console.js")
        assert "agents:   { group: 'nav_group_agent_dev', page: 'menu_agent_config', console: 'admin.agents' }" in js
        assert "'agent-workbench': { group: 'nav_workbench', page: 'menu_agents', console: 'workbench.agents' }" in js

    def test_config_page_title_and_menu_label(self):
        html = self._read("channel/web/chat.html")
        assert 'menu_agent_config' in html
        assert 'data-i18n="menu_agent_config"' in html

    def test_start_flow_guards_unsaved_before_switch(self):
        js = self._read("channel/web/static/js/console.js")
        assert "function startChatWithAgent" in js
        assert "_agentStartInFlight" in js
        assert "wsGuardUnsaved(() => startChatWithAgent" in js
        assert "resetWorkspaceToAgentRoot" in js
        # No silent default fallback: reject an unknown/disabled target.
        assert "refreshWorkbenchAfterUnavailable" in js

    def test_workbench_i18n_keys_present(self):
        # Task 8.5 split the dictionaries into per-domain namespace files
        # (console.js merges window.__cowI18N__ at load time), so the keys are
        # asserted against the merged locale layer.
        js = self._i18n_text()
        for key in ("agent_workbench_title", "agent_workbench_refresh",
                    "agent_workbench_empty", "agent_workbench_failed",
                    "agent_target_unavailable", "agent_permission_denied",
                    "start_chat"):
            assert key in js

    def test_workbench_permission_denied_label_and_notice(self):
        js = self._read("channel/web/static/js/console.js")
        # Card label + start-failure notice distinguish the permission case from
        # the old version-closure message (task 3.3).
        assert "if (reason === 'permission_denied') return t('agent_permission_denied');" in js
        assert "reason === 'permission_denied'\n        ? 'agent_permission_denied'" in js

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for console behavior tests")
    def test_frontend_behavior(self):
        result = subprocess.run(
            [shutil.which("node"), "--test", str(Path(__file__).with_name("test_agent_workbench_frontend.cjs"))],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
