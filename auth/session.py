# encoding:utf-8
"""Database-backed AuthSession store (opaque, digest-only, expiring, revocable).

Sessions are independent of the business chat session and carry *no* current
tenant or permission snapshot — the requester resolves those per request from
the current user/membership/role. Only a high-entropy token is issued to the
client; the database stores only its SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional

from auth.store import IdentityStore

#: Default lifetime (days) of a normal login session.
SESSION_DAYS = 7

#: Default lifetime (days) of a restricted (temp-password) session.
RESTRICTED_DAYS = 0.5


def session_ttl_seconds(restricted: bool) -> int:
    """Seconds until a session expires, based on whether it is restricted."""
    days = RESTRICTED_DAYS if restricted else SESSION_DAYS
    return int(days * 86400)


def generate_token() -> str:
    """Generate a high-entropy opaque session token."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token (the only form stored)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionError(RuntimeError):
    """Raised when a session cannot be created or looked up."""


class SessionStore:
    """Read/write the ``auth_sessions`` table in ``identity.db``."""

    def __init__(self, db_path: str):
        self._store = IdentityStore(db_path)

    def _rows_to_dicts(self, rows) -> list:
        return [dict(row) for row in rows]

    def create(self, user_id: str, token: str, restricted: bool = False, ttl: Optional[int] = None) -> dict:
        ttl = ttl if ttl is not None else session_ttl_seconds(restricted)
        session_id = secrets.token_urlsafe(18)
        with self._store.connect() as con:
            con.execute(
                "INSERT INTO auth_sessions"
                " (id, token_hash, user_id, expires_at, restricted)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, hash_token(token), user_id, int(time.time()) + ttl, int(restricted)),
            )
            con.commit()
        return self._row_for_token(token)

    def _row_for_token(self, token: str) -> Optional[dict]:
        rows = self._store.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=?", (hash_token(token),)
        )
        return rows[0] if rows else None

    def get_by_token(self, token: str) -> Optional[dict]:
        return self._row_for_token(token)

    def get_by_owner(self, user_id: str) -> list:
        return self._rows_to_dicts(
            self._store.execute(
                "SELECT * FROM auth_sessions WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            )
        )

    def validate_token(self, token: str) -> bool:
        """True when the token exists, matches its digest and is unexpired/active."""
        if not token:
            return False
        row = self._row_for_token(token)
        if row is None:
            return False
        if row["revoked_at"]:
            return False
        return row["expires_at"] > int(time.time())

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._store.connect() as con:
            con.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                (int(time.time()), hash_token(token)),
            )
            con.commit()

    def revoke_all_for_user(self, user_id: str) -> None:
        with self._store.connect() as con:
            con.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=?",
                (int(time.time()), user_id),
            )
            con.commit()

    def close(self) -> None:
        pass
