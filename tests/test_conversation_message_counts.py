import tempfile
import time
from pathlib import Path

from agent.memory.conversation_store import ConversationStore


def _store():
    return ConversationStore(Path(tempfile.mkdtemp()) / "index.db")


def test_count_messages_between_respects_bounds():
    store = _store()
    store.append_messages("s1", [
        {"role": "user", "content": "old"},
    ], channel_type="web")
    # Force created_at on the inserted row to a known past second.
    with store._lock:
        conn = store._connect()
        try:
            conn.execute("UPDATE messages SET created_at = ?", (1_700_000_000,))
            conn.commit()
        finally:
            conn.close()

    store.append_messages("s1", [
        {"role": "assistant", "content": "new"},
    ], channel_type="web")

    start = 1_700_000_100
    end = int(time.time()) + 10
    assert store.count_messages_between(start, end) == 1
    assert store.count_messages_between(1_700_000_000, 1_700_000_001) == 1
    assert store.count_messages_between(0, 1) == 0
