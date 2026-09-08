"""SQLite storage for personal todo items and their processing history.

Two business tables only (per openspec/changes/add-workbench-todos/design.md):

``todo_items``
    One row per todo. Carries the current editable state plus the immutable
    create-dedup info (``create_key`` / ``create_payload_hash``) that must never
    change once a row exists.

``todo_events``
    Append-only processing history. Each row records a single state change or
    field edit for one todo: the new ``version``, the operator (``operator_id``
    / ``operator_kind``), the action, the changed fields and an optional note.
    It is written in the same transaction as the item update, so it also serves
    as the minimal internal audit for this slice.

Concurrency model
-----------------
* Version-casual updates: every write carries the ``expected_version`` the
  caller based its edit on; the item's ``version`` is advanced only when it
  matches. Two different concurrent updates can never both succeed.
* SQLite in WAL with a bounded busy timeout. Schema version tracked via
  ``PRAGMA user_version`` and applied from an ordered migration list.

Ownership and scope are never enforced here; the service layer resolves the
``scope_id`` / ``owner_id`` from a trusted request context and only ever calls
in here with those. This module raises domain errors (``TodoConflict``,
``TodoNotFound``, ``TodoFieldError``) that the service maps to HTTP semantics.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from common.log import logger


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = 1

KIND_VALUES = ("general", "input_required", "confirmation", "review")
PRIORITY_VALUES = ("low", "normal", "high")
STATUS_VALUES = ("pending", "in_progress", "completed", "cancelled")
SOURCE_VALUES = ("manual", "conversation")

TITLE_MIN = 1
TITLE_MAX = 120
DESCRIPTION_MAX = 8000
NOTE_MAX = 2000
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100


def _now() -> int:
    return int(time.time())


# Migration list is ordered by user_version. Each entry is a SQL script that
# advances the schema from the previous version to the next.
_MIGRATIONS: Dict[int, str] = {
    1: """
CREATE TABLE IF NOT EXISTS todo_items (
    id                   TEXT    PRIMARY KEY,
    scope_id             TEXT    NOT NULL,
    owner_id             TEXT    NOT NULL,
    title                TEXT    NOT NULL,
    description          TEXT    NOT NULL DEFAULT '',
    kind                 TEXT    NOT NULL DEFAULT 'general',
    priority             TEXT    NOT NULL DEFAULT 'normal',
    status               TEXT    NOT NULL DEFAULT 'pending',
    due_at               INTEGER,
    timezone             TEXT    NOT NULL DEFAULT '',
    source               TEXT    NOT NULL DEFAULT 'manual',
    agent_id             TEXT    NOT NULL DEFAULT '',
    session_id           TEXT    NOT NULL DEFAULT '',
    message_seq          INTEGER,
    created_by           TEXT    NOT NULL DEFAULT 'human',
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    completed_at         INTEGER,
    version              INTEGER NOT NULL DEFAULT 1,
    create_key           TEXT    NOT NULL,
    create_payload_hash  TEXT    NOT NULL,
    UNIQUE (scope_id, owner_id, create_key)
);

CREATE INDEX IF NOT EXISTS idx_todo_items_owner_status
    ON todo_items (scope_id, owner_id, status);

CREATE INDEX IF NOT EXISTS idx_todo_items_owner_due
    ON todo_items (scope_id, owner_id, due_at);

CREATE TABLE IF NOT EXISTS todo_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        TEXT    NOT NULL,
    scope_id       TEXT    NOT NULL,
    owner_id       TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    operator_id    TEXT    NOT NULL DEFAULT '',
    operator_kind  TEXT    NOT NULL DEFAULT 'human',
    action         TEXT    NOT NULL,
    changed        TEXT    NOT NULL DEFAULT '',
    note           TEXT    NOT NULL DEFAULT '',
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_todo_events_item
    ON todo_events (item_id, version);

CREATE INDEX IF NOT EXISTS idx_todo_events_owner
    ON todo_events (scope_id, owner_id, created_at);
""",
}


class TodoStoreError(RuntimeError):
    """Base storage error."""


class TodoNotFound(TodoStoreError):
    """Requested todo does not exist (or is not visible under the scope/owner)."""


class TodoConflict(TodoStoreError):
    """Version / create-key / state conflict."""


class TodoFieldError(TodoStoreError):
    """A field failed validation at the storage boundary."""


class TodoStore:
    """Thread-safe SQLite access to the two todo tables for one scope/owner."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------ #
    # Schema / migrations
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._raw_connect()
        try:
            self._migrate(conn)
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version in sorted(_MIGRATIONS):
            if version <= current:
                continue
            conn.executescript(_MIGRATIONS[version])
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
        if current < SCHEMA_VERSION:
            logger.info(f"[TodoStore] migrated todo db to user_version={SCHEMA_VERSION}")

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    def _raw_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            return self._raw_connect()

    def snapshot_backup(self, dest: Path) -> None:
        """Write a consistent on-line backup to ``dest`` (SQLite backup API).

        Exposed for the backup/restore slice. Must be called while the store is
        still writable; SQLite guarantees the snapshot is internally consistent.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self._raw_connect()
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    # ------------------------------------------------------------------ #
    # Item row <-> dict
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["due_at"] = item.get("due_at")
        item["message_seq"] = item.get("message_seq")
        return item

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def get_item(self, scope_id: str, owner_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM todo_items WHERE id=? AND scope_id=? AND owner_id=?",
                (item_id, scope_id, owner_id),
            ).fetchone()
            return self._row_to_item(row) if row else None
        finally:
            conn.close()

    def get_item_by_create_key(
        self, scope_id: str, owner_id: str, create_key: str
    ) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM todo_items WHERE scope_id=? AND owner_id=? AND create_key=?",
                (scope_id, owner_id, create_key),
            ).fetchone()
            return self._row_to_item(row) if row else None
        finally:
            conn.close()

    def list_items(
        self,
        scope_id: str,
        owner_id: str,
        *,
        status: Optional[str] = None,
        q: Optional[str] = None,
        overdue: bool = False,
        page: int = 1,
        page_size: int = PAGE_SIZE_DEFAULT,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return ``(items, total)`` with a fixed ordering.

        Ordering (per design): overdue first, then priority desc, due asc with
        nulls last, created desc, id asc. ``overdue`` only includes active
        (pending/in_progress) items whose due_at is in the past; it is applied
        as a filter, and the combined stable sort keeps the ordering.

        ``status`` accepts a single exact status OR the pseudo-values
        ``open`` (pending+in_progress) / ``all``.
        """
        page = max(1, page)
        page_size = max(1, min(int(page_size), PAGE_SIZE_MAX))

        where = ["scope_id=? AND owner_id=?"]
        params: List[Any] = [scope_id, owner_id]

        if status in (None, "", "open"):
            if status == "open":
                where.append("status IN ('pending','in_progress')")
            # None / "" => open (未完成) is the default per design; but a caller
            # that explicitly wants everything passes "all".
            if status is None or status == "":
                where.append("status IN ('pending','in_progress')")
        elif status == "all":
            pass
        elif status in STATUS_VALUES:
            where.append("status = ?")
            params.append(status)
        else:
            # Unknown status: return nothing rather than leaking other rows.
            return [], 0

        if q:
            where.append("(title LIKE ? OR description LIKE ?)")
            like = f"%{q}%"
            params += [like, like]

        if overdue:
            where.append("status IN ('pending','in_progress')")
            where.append("due_at IS NOT NULL AND due_at < ?")
            params.append(_now())

        where_sql = " AND ".join(where)

        conn = self._connect()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM todo_items WHERE {where_sql}", tuple(params)
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT * FROM todo_items
                WHERE {where_sql}
                ORDER BY
                    (status IN ('pending','in_progress') AND due_at IS NOT NULL AND due_at < {_now()}) DESC,
                    priority DESC,
                    CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,
                    due_at ASC,
                    created_at DESC,
                    id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
            return [self._row_to_item(r) for r in rows], total
        finally:
            conn.close()

    def count_open(self, scope_id: str, owner_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM todo_items"
                " WHERE scope_id=? AND owner_id=? AND status IN ('pending','in_progress')",
                (scope_id, owner_id),
            ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    def count_overdue(self, scope_id: str, owner_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM todo_items"
                " WHERE scope_id=? AND owner_id=? AND status IN ('pending','in_progress')"
                " AND due_at IS NOT NULL AND due_at < ?",
                (scope_id, owner_id, _now()),
            ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #
    def create_item(
        self,
        *,
        scope_id: str,
        owner_id: str,
        title: str,
        description: str,
        kind: str,
        priority: str,
        due_at: Optional[int],
        timezone: str,
        source: str,
        agent_id: str,
        session_id: str,
        message_seq: Optional[int],
        created_by: str,
        operator_id: str,
        create_key: str,
        create_payload_hash: str,
    ) -> Dict[str, Any]:
        """Insert a new todo. Rejects a duplicate create_key with a conflict.

        On a ``create_key`` repeat the caller should call ``create_item`` again
        ONLY when it intends to produce the same row (idempotent retry); a
        content mismatch is surfaced as ``TodoConflict``.
        """
        item_id = str(uuid.uuid4())
        now = _now()
        conn = self._connect()
        try:
            with conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO todo_items (
                            id, scope_id, owner_id, title, description, kind,
                            priority, status, due_at, timezone, source, agent_id,
                            session_id, message_seq, created_by, created_at,
                            updated_at, completed_at, version, create_key,
                            create_payload_hash
                        ) VALUES (?,?,?,?,?,?,?, 'pending', ?,?,?,?,?,?,?,?,?,NULL,1,?,?)
                        """,
                        (
                            item_id, scope_id, owner_id, title, description, kind,
                            priority, due_at, timezone, source, agent_id, session_id,
                            message_seq, created_by, now, now, create_key,
                            create_payload_hash,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise TodoConflict("create_key already used for another todo") from None
                conn.execute(
                    """
                    INSERT INTO todo_events (
                        item_id, scope_id, owner_id, version, operator_id,
                        operator_kind, action, changed, note, created_at
                    ) VALUES (?,?,?,1,?,?, 'create', '', '', ?)
                    """,
                    (item_id, scope_id, owner_id, operator_id, created_by, now),
                )
            return self.get_item(scope_id, owner_id, item_id)  # type: ignore[return-value]
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Update (version-cas)
    # ------------------------------------------------------------------ #
    def update_item(
        self,
        scope_id: str,
        owner_id: str,
        item_id: str,
        *,
        expected_version: int,
        fields: Dict[str, Any],
        operator_id: str,
        operator_kind: str,
        action: str,
        note: str = "",
        changed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply an edit or state transition with a version compare.

        ``fields`` are the columns to write (never owner/source/audit columns).
        The item's ``version`` is advanced, the current time bump applied and an
        event appended inside the same transaction. Returns the fresh item.

        Raises ``TodoNotFound`` / ``TodoConflict`` (version or status) without
        writing anything.
        """
        allowed = {
            "title", "description", "kind", "priority", "due_at", "timezone",
            "status", "completed_at",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            raise TodoFieldError("no editable fields provided")

        now = _now()
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM todo_items WHERE id=? AND scope_id=? AND owner_id=?",
                    (item_id, scope_id, owner_id),
                ).fetchone()
                if row is None:
                    raise TodoNotFound("todo not found")
                if row["version"] != expected_version:
                    raise TodoConflict("stale version")

                # If the edit touches status, honour the four-state transition
                # guard so a terminal item cannot be edited directly.
                if "status" in clean and clean["status"] != row["status"]:
                    self._assert_status_transition(row["status"], clean["status"], with_edit=bool(
                        set(clean) - {"status", "completed_at"}))

                set_sql = ", ".join(f"{k}=?" for k in clean)
                values = list(clean.values())
                # Terminal -> pending (reopen) clears completed_at; completing
                # sets it. Editing never surprises the completion timestamp.
                new_status = clean.get("status", row["status"])
                if new_status == "pending":
                    clean["completed_at"] = None
                    set_sql += ", completed_at=?"
                    values.append(None)
                elif new_status == "completed":
                    clean["completed_at"] = now
                    set_sql += ", completed_at=?"
                    values.append(now)

                values += [row["version"] + 1, now, item_id]
                conn.execute(
                    f"UPDATE todo_items SET {set_sql}, version=?, updated_at=? "
                    "WHERE id=? AND scope_id=? AND owner_id=?",
                    tuple(values + [scope_id, owner_id]),
                )
                conn.execute(
                    """
                    INSERT INTO todo_events (
                        item_id, scope_id, owner_id, version, operator_id,
                        operator_kind, action, changed, note, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item_id, scope_id, owner_id, row["version"] + 1,
                        operator_id, operator_kind, action,
                        json.dumps(changed or clean, ensure_ascii=False),
                        note, now,
                    ),
                )
            return self.get_item(scope_id, owner_id, item_id)  # type: ignore[return-value]
        finally:
            conn.close()

    @staticmethod
    def _assert_status_transition(current: str, target: str, with_edit: bool) -> None:
        allowed_next = {
            "pending": {"in_progress", "completed", "cancelled"},
            "in_progress": {"completed", "cancelled"},
            "completed": {"pending"},
            "cancelled": {"pending"},
        }
        if target not in allowed_next.get(current, set()):
            if current in ("completed", "cancelled") and with_edit:
                raise TodoConflict("terminal todos must be reopened before editing")
            raise TodoConflict(f"invalid status transition {current} -> {target}")

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    def list_events(
        self,
        scope_id: str,
        owner_id: str,
        item_id: str,
        *,
        page: int = 1,
        page_size: int = PAGE_SIZE_DEFAULT,
    ) -> Tuple[List[Dict[str, Any]], int]:
        page = max(1, page)
        page_size = max(1, min(int(page_size), PAGE_SIZE_MAX))
        conn = self._connect()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM todo_events WHERE scope_id=? AND owner_id=? AND item_id=?",
                (scope_id, owner_id, item_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM todo_events"
                " WHERE scope_id=? AND owner_id=? AND item_id=?"
                " ORDER BY version DESC, id DESC LIMIT ? OFFSET ?",
                (scope_id, owner_id, item_id, page_size, (page - 1) * page_size),
            ).fetchall()
            events = []
            for r in rows:
                ev = dict(r)
                try:
                    ev["changed"] = json.loads(ev["changed"] or "{}")
                except (ValueError, TypeError):
                    ev["changed"] = {}
                events.append(ev)
            return events, total
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Database file helpers
    # ------------------------------------------------------------------ #
    @property
    def db_path(self) -> Path:
        return self._db_path


# --------------------------------------------------------------------------- #
# Canonical initial create-payload hash
# --------------------------------------------------------------------------- #

def compute_create_key(seed: str = "") -> str:
    """Generate a stable, high-entropy create key.

    The front-end form and the agent tool both generate a UUID per creation; the
    key is persisted so a retry of the *same* logical creation returns the same
    id. Never derived from the model's local call id alone.
    """
    return str(uuid.uuid4())


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a stable subset of an initial-creation payload for hashing.

    Only the user-supplied business fields are included, never server-generated
    id / timestamps / version. Keys are sorted so semantic-equivalent payloads
    hash the same.
    """
    keys = ("title", "description", "kind", "priority", "due_at", "timezone",
            "source", "agent_id", "session_id", "message_seq")
    trimmed = {k: payload.get(k) for k in keys if payload.get(k) is not None}
    return trimmed


def hash_create_payload(payload: Dict[str, Any]) -> str:
    """Return the sha256 of the normalized initial creation payload."""
    norm = normalize_payload(payload)
    return hashlib.sha256(
        json.dumps(norm, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def is_active_status(status: str) -> bool:
    return status in ("pending", "in_progress")
