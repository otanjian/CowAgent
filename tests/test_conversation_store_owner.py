# encoding:utf-8
"""Owner-scoping tests for the conversation store (task 3.8 / 4.1).

Verifies that content written under a user identity is only visible to reads
scoped to that user, and that legacy (owner-less) content stays visible when no
user is scoping the read.
"""

import os
import tempfile

from pathlib import Path

from agent.memory.conversation_store import ConversationStore
from common.runtime_identity import RuntimeIdentity, use_identity


def _store():
    return ConversationStore(Path(tempfile.mkdtemp()) / "index.db")


def test_legacy_content_visible_to_unscoped_read():
    store = _store()
    with use_identity(RuntimeIdentity()):
        store.append_messages("s1", [{"role": "user", "content": "hello"}])
    # unscoped read sees it (owner == '')
    assert store.list_sessions()["total"] == 1


def test_user_content_scoped_by_owner():
    store = _store()
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u1")):
        store.append_messages("s1", [{"role": "user", "content": "mine"}])
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u2")):
        store.append_messages("s2", [{"role": "user", "content": "theirs"}])

    # each user only sees their own
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u1")):
        ids = store.list_session_ids(user_id="u1")
        assert "s1" in ids and "s2" not in ids
        assert store.list_sessions(user_id="u1")["total"] == 1
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u2")):
        assert store.list_session_ids(user_id="u2") == ["s2"]


def test_history_scoped_by_owner():
    store = _store()
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u1")):
        store.append_messages("s1", [{"role": "user", "content": "mine"}])
    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u2")):
        store.append_messages("s2", [{"role": "user", "content": "theirs"}])

    with use_identity(RuntimeIdentity(agent_id="a1", user_id="u1")):
        page = store.load_history_page("s1", user_id="u1")
        assert page["total"] == 1
        # a different user's session must not be reachable
        empty = store.load_history_page("s2", user_id="u1")
        assert empty["total"] == 0


def test_backfill_owner_registers_legacy_content():
    store = _store()
    # legacy content written with no user identity
    with use_identity(RuntimeIdentity()):
        store.append_messages("s1", [{"role": "user", "content": "legacy"}])
    # backfill to the default admin
    store.backfill_owner("u-default")
    # now readable by u-default
    ids = store.list_session_ids(user_id="u-default")
    assert "s1" in ids
    # and re-running is idempotent (does not clobber later users)
    store.backfill_owner("u-default")
    assert store.list_session_ids(user_id="u-default") == ["s1"]
