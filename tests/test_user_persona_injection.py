# encoding:utf-8
"""Per-user context injection: persona (personal-conversation-and-memory) and
current-user identity (add-current-user-identity-context).

Both layers are *segments* appended to the Agent's system suffix, not new
Agents and not overwrites of ``AGENT.md``. What matters here is ordering
(scene -> employee -> personal -> identity), that an absent profile adds
nothing, and that ownership gates both: a session someone else owns must never
carry the current caller's persona *or* account, because ``get_agent()`` is
cached per ``(agent_id, session_id)``.

Identity is deliberately independent of the persona file (a fact every user has
vs. an opt-in profile) and non-authoritative: it must be injected from the
verified runtime identity only, read no resource grants, and fail closed.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth.service import IdentityService  # noqa: E402
from common import state_dir  # noqa: E402
from common.runtime_identity import RuntimeIdentity, use_identity  # noqa: E402


class _Agent:
    """The slice of an Agent the persona injection touches."""

    def __init__(self, workspace_dir, profile=None):
        self.workspace_dir = str(workspace_dir)
        self.extra_system_suffix = ""
        self.agent_profile = profile


class UserPersonaInjectionTestCase(unittest.TestCase):
    def setUp(self):
        from bridge.agent_bridge import AgentBridge

        self.db = os.path.join(tempfile.mkdtemp(), "identity.db")
        self.svc = IdentityService(self.db)
        self.shared = tempfile.mkdtemp(prefix="shared-")
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared, allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.alice = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            operation="create-new", username="alice", display_name="Alice",
            temporary_password="TmpPass123!", roles=[])["user_id"]
        self.bob = self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            operation="create-new", username="bob", display_name="Bob",
            temporary_password="TmpPass123!", roles=[])["user_id"]

        self.ws = tempfile.mkdtemp(prefix="agent-")
        self._svc_patch = patch("auth.service.get_identity_service", lambda: self.svc)
        self._svc_patch.start()
        # ``_apply_user_persona_context`` only reads module-level helpers, so an
        # uninitialized instance is enough to exercise it in isolation.
        self.bridge = AgentBridge.__new__(AgentBridge)

    def tearDown(self):
        self._svc_patch.stop()
        from agent.memory import clear_conversation_store_cache
        clear_conversation_store_cache()

    def _ident(self, user_id):
        return RuntimeIdentity(agent_id="agent-x", user_id=user_id,
                               tenant_id=self.tid)

    def _write_persona(self, text):
        path = state_dir.persona_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _own_session(self, user_id, session_id):
        """Create a session owned by ``user_id`` (as its first writer)."""
        from agent.memory import get_conversation_store
        store = get_conversation_store(self.ws)
        with use_identity(self._ident(user_id)):
            store.append_messages(
                session_id=session_id, channel_type="web",
                messages=[{"role": "user", "content": "hi"}])

    def _deactivate_membership(self, user_id):
        """Drop a member from the tenant, as an admin removal would."""
        with self.svc._tx() as con:
            con.execute(
                "UPDATE memberships SET active=0 WHERE user_id=? AND tenant_id=?",
                (user_id, self.tid),
            )

    # -- 3.2/3.3 ordering and coexistence -------------------------------

    def test_persona_is_appended_after_scene_and_employee(self):
        from bridge.agent_bridge import AgentBridge

        with use_identity(self._ident(self.alice)):
            self._write_persona("PERSONAL-MARKER: keep answers short")
            self._own_session(self.alice, "s-alice")

            agent = _Agent(self.ws, SimpleNamespace(
                position="Engineer", persona_summary="EMPLOYEE-MARKER",
                greeting=None, sops=None))
            # Simulate an already-applied scene segment.
            agent.extra_system_suffix = "SCENE-MARKER"

            self.bridge._apply_employee_context(agent)
            self.bridge._apply_user_persona_context(agent, "s-alice")

        suffix = agent.extra_system_suffix
        self.assertIn("SCENE-MARKER", suffix)
        self.assertIn("EMPLOYEE-MARKER", suffix)
        self.assertIn("PERSONAL-MARKER", suffix)
        self.assertLess(suffix.index("SCENE-MARKER"), suffix.index("EMPLOYEE-MARKER"))
        self.assertLess(suffix.index("EMPLOYEE-MARKER"), suffix.index("PERSONAL-MARKER"))

    def test_absent_profile_adds_nothing(self):
        with use_identity(self._ident(self.alice)):
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-alice")
        self.assertEqual(agent.extra_system_suffix, "",
                         "a missing profile produced a segment")

    def test_empty_profile_adds_nothing(self):
        with use_identity(self._ident(self.alice)):
            self._write_persona("   \n")
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-alice")
        self.assertEqual(agent.extra_system_suffix, "")

    # -- 3.4 ownership gate ---------------------------------------------

    def test_other_users_session_gets_no_persona(self):
        with use_identity(self._ident(self.alice)):
            self._write_persona("ALICE-PERSONA")
        self._own_session(self.alice, "s-alice")

        with use_identity(self._ident(self.bob)):
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-alice")
        self.assertEqual(agent.extra_system_suffix, "",
                         "Bob's caller got Alice's persona in her session")

    def test_brand_new_session_is_assumed_to_be_the_callers(self):
        with use_identity(self._ident(self.alice)):
            self._write_persona("ALICE-PERSONA")
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "never-written")
        self.assertIn("ALICE-PERSONA", agent.extra_system_suffix)

    def test_legacy_identity_adds_nothing(self):
        self._write_persona("ALICE-PERSONA")
        with use_identity(RuntimeIdentity()):
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-any")
        self.assertEqual(agent.extra_system_suffix, "")

    def test_persona_edit_applies_on_the_next_build(self):
        with use_identity(self._ident(self.alice)):
            self._own_session(self.alice, "s-alice")
            self._write_persona("FIRST-VERSION")
            first = _Agent(self.ws)
            self.bridge._apply_user_persona_context(first, "s-alice")

            self._write_persona("SECOND-VERSION")
            second = _Agent(self.ws)
            self.bridge._apply_user_persona_context(second, "s-alice")

        self.assertIn("FIRST-VERSION", first.extra_system_suffix)
        self.assertIn("SECOND-VERSION", second.extra_system_suffix)

    # -- current-user identity segment ----------------------------------

    def test_identity_segment_injected_for_own_session(self):
        with use_identity(self._ident(self.alice)):
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "s-alice")
        suffix = agent.extra_system_suffix
        self.assertIn("当前用户身份", suffix)
        self.assertIn("alice", suffix)
        self.assertIn("Alice", suffix)

    def test_identity_injected_without_persona_profile(self):
        """A user with no persona file still gets the identity segment."""
        with use_identity(self._ident(self.alice)):
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-alice")
            self.bridge._apply_user_identity_context(agent, "s-alice")
        self.assertNotIn("个人偏好", agent.extra_system_suffix,
                         "a missing profile produced a persona segment")
        self.assertIn("alice", agent.extra_system_suffix,
                      "identity was skipped along with the absent persona")

    def test_identity_appended_after_personal(self):
        with use_identity(self._ident(self.alice)):
            self._write_persona("PERSONAL-MARKER")
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_persona_context(agent, "s-alice")
            self.bridge._apply_user_identity_context(agent, "s-alice")
        suffix = agent.extra_system_suffix
        self.assertLess(suffix.index("PERSONAL-MARKER"), suffix.index("alice"))

    def test_brand_new_session_gets_callers_identity(self):
        with use_identity(self._ident(self.alice)):
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "never-written")
        self.assertIn("alice", agent.extra_system_suffix)

    def test_other_users_session_gets_no_identity(self):
        self._own_session(self.alice, "s-alice")
        with use_identity(self._ident(self.bob)):
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "s-alice")
        self.assertEqual(agent.extra_system_suffix, "",
                         "Bob's caller got Alice's account in her session")

    def test_legacy_identity_adds_no_identity_segment(self):
        with use_identity(RuntimeIdentity()):
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "s-any")
        self.assertEqual(agent.extra_system_suffix, "")

    def test_removed_member_is_not_injected(self):
        """Fail closed: a member dropped from the tenant stops being named."""
        self._own_session(self.alice, "s-alice")
        self._deactivate_membership(self.alice)
        with use_identity(self._ident(self.alice)):
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "s-alice")
        self.assertEqual(agent.extra_system_suffix, "",
                         "a non-member was still injected as the speaker")

    def test_identity_injection_consults_no_resource_grants(self):
        """A name in the prompt is a fact, never a grant: resolve no resources."""
        self._own_session(self.alice, "s-alice")
        calls = []
        original = self.svc.check_resource_action

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        self.svc.check_resource_action = spy
        try:
            with use_identity(self._ident(self.alice)):
                agent = _Agent(self.ws)
                self.bridge._apply_user_identity_context(agent, "s-alice")
        finally:
            self.svc.check_resource_action = original
        self.assertEqual(calls, [], "identity injection consulted resource grants")

    def test_identity_segment_disclaims_authority(self):
        with use_identity(self._ident(self.alice)):
            self._own_session(self.alice, "s-alice")
            agent = _Agent(self.ws)
            self.bridge._apply_user_identity_context(agent, "s-alice")
        self.assertIn("不改变任何权限判定", agent.extra_system_suffix)


if __name__ == "__main__":
    unittest.main()
