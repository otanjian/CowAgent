# encoding:utf-8
"""The memory index is tenant-scoped too (task 6.10).

``chunks``/``files`` back retrieval, so a tenant that can see another tenant's
chunks has the same leak as one that can read another tenant's conversations --
the defence has to exist at the index as well, not only in the conversation
store. Everything routes through the single ``_scope_filter`` choke point, so
this file asserts the tenant predicate is part of it for both backends and that
historical, unattributed rows do not become visible to a tenant scope.
"""

import tempfile
from pathlib import Path

from agent.memory.storage import MemoryChunk, MemoryStorage
from common.runtime_identity import RuntimeIdentity, use_identity


def _storage():
    return MemoryStorage(Path(tempfile.mkdtemp()) / "memory.db")


def _chunk(text, *, user="", scope="shared", path=None, embedding=None):
    return MemoryChunk(
        id=f"{text}-{user}-{scope}",
        user_id=user or None,
        scope=scope,
        source="memory",
        path=path or f"/notes/{text}.md",
        start_line=1,
        end_line=1,
        text=text,
        embedding=embedding,
        hash=text,
    )


def _save(chunk, *, tenant=""):
    with use_identity(RuntimeIdentity(tenant_id=tenant)):
        _storage().save_chunk(chunk)


def test_the_chunks_table_carries_a_tenant_column():
    store = _storage()
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(chunks)")}
    assert "tenant_id" in cols


def test_a_tenant_scoped_keyword_search_cannot_see_another_tenants_chunk():
    db = Path(tempfile.mkdtemp()) / "memory.db"
    store = MemoryStorage(db)
    with use_identity(RuntimeIdentity(tenant_id="tnt_acme")):
        store.save_chunk(_chunk("acme launch plan", user="u1", scope="user"))
    with use_identity(RuntimeIdentity(tenant_id="tnt_globex")):
        store.save_chunk(_chunk("globex launch plan", user="u1", scope="user"))

    with use_identity(RuntimeIdentity(tenant_id="tnt_acme")):
        hits = store.search_keyword("launch", user_id="u1", limit=10)
    assert [h.snippet for h in hits] == ["acme launch plan"]

    with use_identity(RuntimeIdentity(tenant_id="tnt_globex")):
        hits = store.search_keyword("launch", user_id="u1", limit=10)
    assert [h.snippet for h in hits] == ["globex launch plan"]


def test_a_tenant_scoped_vector_search_does_not_match_another_tenants_chunk():
    """The vector backend is filtered by metadata, a second code path."""
    store = _storage()
    vector = [1.0, 0.0, 0.0]
    for tenant, text in (("tnt_acme", "acme chunk"), ("tnt_globex", "globex chunk")):
        with use_identity(RuntimeIdentity(tenant_id=tenant)):
            store.save_chunk(_chunk(text, user="u1", scope="user",
                                    path=f"/{text}", embedding=vector))

    with use_identity(RuntimeIdentity(tenant_id="tnt_acme")):
        hits = store.search_vector(vector, user_id="u1", limit=10)
    assert [h.path for h in hits] == ["/acme chunk"]


def test_an_unattributed_chunk_is_unreadable_to_a_tenant_scope():
    store = _storage()
    store.save_chunk(_chunk("legacy note", user="u1", scope="user"))  # no tenant

    assert store.search_keyword("legacy", user_id="u1", limit=10)
    with use_identity(RuntimeIdentity(tenant_id="tnt_acme")):
        assert store.search_keyword("legacy", user_id="u1", limit=10) == []


def test_an_existing_index_gets_the_column_without_losing_rows():
    db = Path(tempfile.mkdtemp()) / "memory.db"
    store = MemoryStorage(db)
    store.save_chunk(_chunk("kept", user="u1", scope="user"))
    # Simulate a pre-upgrade index: drop the column (and the index that names
    # it, which SQLite requires) the way the old code declared the table, then
    # re-open the store and check the column comes back without losing rows.
    store.conn.execute("DROP INDEX IF EXISTS idx_chunks_tenant")
    store.conn.execute("ALTER TABLE chunks DROP COLUMN tenant_id")
    store.conn.commit()
    store.close()

    store = MemoryStorage(db)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(chunks)")}
    assert "tenant_id" in cols
    rows = store.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert rows == 1
