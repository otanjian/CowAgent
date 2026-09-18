# encoding:utf-8
"""Tenant isolation in the conversation store (tasks 6.6 - 6.9).

The route/handler layer already refuses cross-tenant access, but that is one
line of defence. This file pins the second one: the store itself filters by the
ambient tenant, so a retrieval that reaches it with the wrong scope returns no
other tenant's rows even if a caller forgot to check.

Two details matter and are asserted here:

* rows whose tenant could not be established keep an empty tenant and are
  therefore unreadable to *every* tenant scope (task 6.8) -- attribution is
  never guessed from an Agent's current owner;
* the composite-key change is a table rebuild, so the rollback is a restore
  point: the retained pre-rebuild copy is swapped back and reads keep working
  (task 6.9).
"""

import sqlite3
import tempfile
import time
from pathlib import Path

from agent.memory.conversation_store import ConversationStore
from common.runtime_identity import RuntimeIdentity, use_identity


def _store():
    return ConversationStore(Path(tempfile.mkdtemp()) / "index.db")


def _write(store, session_id, text, *, tenant="", user="", agent=""):
    with use_identity(RuntimeIdentity(agent_id=agent, user_id=user,
                                      tenant_id=tenant)):
        store.append_messages(session_id, [{"role": "user", "content": text}])


def _read_texts(store, session_id, *, tenant="", user="", agent=""):
    with use_identity(RuntimeIdentity(agent_id=agent, user_id=user,
                                      tenant_id=tenant)):
        return [m["content"] for m in store.load_messages(session_id)]


# --- read isolation ------------------------------------------------------

def test_a_tenant_scoped_read_cannot_see_another_tenants_session():
    store = _store()
    _write(store, "s-acme", "acme secret", tenant="tnt_acme", user="u1")
    _write(store, "s-globex", "globex secret", tenant="tnt_globex", user="u2")

    assert _read_texts(store, "s-acme", tenant="tnt_acme", user="u1") == ["acme secret"]
    assert _read_texts(store, "s-globex", tenant="tnt_acme", user="u1") == []
    assert _read_texts(store, "s-acme", tenant="tnt_globex", user="u2") == []


def test_list_sessions_is_scoped_to_the_ambient_tenant():
    store = _store()
    _write(store, "s-acme", "acme", tenant="tnt_acme", user="u1")
    _write(store, "s-globex", "globex", tenant="tnt_globex", user="u2")

    with use_identity(RuntimeIdentity(tenant_id="tnt_acme", user_id="u1")):
        listing = store.list_sessions(user_id="u1")
        ids = [s["session_id"] for s in listing["sessions"]]
        assert ids == ["s-acme"]
        assert store.list_session_ids(user_id="u1") == ["s-acme"]


def test_history_page_is_scoped_to_the_ambient_tenant():
    store = _store()
    _write(store, "s-shared-id", "acme turn", tenant="tnt_acme", user="u1")
    _write(store, "s-shared-id", "globex turn", tenant="tnt_globex", user="u2")

    with use_identity(RuntimeIdentity(tenant_id="tnt_acme", user_id="u1")):
        page = store.load_history_page("s-shared-id", user_id="u1")
    assert [m["content"] for m in page["messages"]] == ["acme turn"]


def test_legacy_rows_are_unreadable_to_a_tenant_scope():
    """An unattributed row must not become visible by being asked for."""
    store = _store()
    _write(store, "legacy", "unattributed")  # no tenant/user in scope

    assert _read_texts(store, "legacy") == ["unattributed"]
    assert _read_texts(store, "legacy", tenant="tnt_acme", user="u1") == []


def test_the_agent_dimension_also_scopes_a_read():
    """Two handles on one file: the Agent dimension keeps their rows apart.

    Upstream's global store binds a handle to one Agent, so the dimension is
    exercised by two handles over the same database rather than by switching the
    ambient identity under a single handle. Both use ``s1`` on purpose -- the
    composite key is what lets one Agent's transcript of that session coexist
    with another's.
    """
    db = Path(tempfile.mkdtemp()) / "index.db"
    store_a = ConversationStore(db, agent_id="agent-a")
    store_b = ConversationStore(db, agent_id="agent-b")
    _write(store_a, "s1", "agent a")
    _write(store_b, "s1", "agent b")

    assert _read_texts(store_a, "s1") == ["agent a"]
    assert _read_texts(store_b, "s1") == ["agent b"]


# --- backfill (6.8) ------------------------------------------------------

def test_backfill_tenant_uses_the_owners_membership():
    store = _store()
    _write(store, "s1", "one", user="u1")
    _write(store, "s2", "two", user="u2")

    counts = store.backfill_tenant(lambda owner: {"u1": "tnt_acme"}.get(owner))

    assert counts["sessions"] == 1
    assert counts["messages"] == 1
    assert counts["unresolved"] == 1  # u2 maps to no tenant: never guessed
    assert _read_texts(store, "s1", tenant="tnt_acme", user="u1") == ["one"]
    assert _read_texts(store, "s2", tenant="tnt_acme", user="u1") == []
    # idempotent: a second pass re-attributes nothing. ``unresolved`` stays at 1
    # because u2 is still (correctly) unattributed -- the report is a fact about
    # the data, not a work queue.
    again = store.backfill_tenant(lambda owner: {"u1": "tnt_acme"}.get(owner))
    assert again["sessions"] == 0 and again["messages"] == 0
    assert _read_texts(store, "s1", tenant="tnt_acme", user="u1") == ["one"]


def test_an_ambiguous_owner_is_not_attributed():
    """``owner -> None`` covers "no membership" and "more than one tenant"."""
    store = _store()
    _write(store, "s1", "one", user="u1")

    counts = store.backfill_tenant(lambda owner: None)
    assert counts["unresolved"] == 1
    assert _read_texts(store, "s1", tenant="tnt_acme", user="u1") == []


# --- rollback (6.9) ------------------------------------------------------

def _legacy_db(path, sessions=1):
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            channel_type TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            context_start_seq INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            last_active INTEGER NOT NULL,
            msg_count INTEGER NOT NULL DEFAULT 0,
            pinned INTEGER NOT NULL DEFAULT 0,
            owner TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            extras TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            UNIQUE (session_id, seq)
        );
        """
    )
    for i in range(sessions):
        con.execute(
            "INSERT INTO sessions (session_id, created_at, last_active, title)"
            " VALUES (?, 1, 1, 'kept')", (f"legacy-{i}",))
        con.execute(
            "INSERT INTO messages (session_id, seq, role, content, created_at)"
            " VALUES (?, 0, 'user', '\"hi\"', 1)", (f"legacy-{i}",))
    con.commit()
    con.close()


def _pk_columns(path, table):
    con = sqlite3.connect(str(path))
    try:
        rows = [r for r in con.execute(f"PRAGMA table_info({table})") if r[5]]
        return tuple(r[1] for r in sorted(rows, key=lambda r: r[5]))
    finally:
        con.close()


def test_the_key_rebuild_keeps_every_row_and_can_be_rolled_back():
    db_path = Path(tempfile.mkdtemp()) / "index.db"
    _legacy_db(db_path, sessions=50)
    store = ConversationStore(db_path)
    assert _pk_columns(db_path, "sessions") == ("agent_id", "session_id")

    rows = store.load_messages("legacy-7")
    assert [m["content"] for m in rows] == ["hi"]

    assert store.rollback_key_constraints() is True
    assert _pk_columns(db_path, "sessions") == ("session_id",)
    # original semantics still work after the rollback
    assert [m["content"] for m in store.load_messages("legacy-7")] == ["hi"]
    with use_identity(RuntimeIdentity(user_id="u1", tenant_id="t1")):
        store.append_messages("after-rollback", [{"role": "user", "content": "ok"}])
    assert [m["content"] for m in store.load_messages("after-rollback")] == ["ok"]


def test_migrating_a_larger_database_preserves_the_row_count():
    """The documented upgrade rehearsal (task 6.5), at a modest scale."""
    db_path = Path(tempfile.mkdtemp()) / "index.db"
    legacy = 2000
    _legacy_db(db_path, sessions=legacy)

    started = time.monotonic()
    ConversationStore(db_path)
    elapsed = time.monotonic() - started

    con = sqlite3.connect(str(db_path))
    try:
        sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        con.close()
    assert (sessions, messages) == (legacy, legacy)
    assert _pk_columns(db_path, "sessions") == ("agent_id", "session_id")
    # Generous, but it does catch an accidental row-by-row rebuild.
    assert elapsed < 30, f"rebuild took {elapsed:.1f}s"

    store = ConversationStore(db_path)
    assert store.rollback_key_constraints() is True
    assert _pk_columns(db_path, "sessions") == ("session_id",)
