# encoding:utf-8
"""The IMAP side: login probe, read-only fetch, attachment extraction, marking.

Read-only means read-only at the protocol level, not by convention:

* the mailbox is selected with ``readonly=True``;
* the body is fetched with ``BODY.PEEK[]``, which does not set ``\\Seen``;
* only :func:`mark` opens the mailbox writable, and it is a declared write
  action with its own execution class, so "fetch" never implies "mark read".

The login probe mirrors the reference implementation: connect, log in and log
out, no body fetch, no flag change, no mailbox modification.
"""

from __future__ import annotations

import datetime
import email
import imaplib
import socket
import time
from typing import Any, List, Mapping, Optional, Sequence

from integrations.external.adapters.base import STAGE_AUTH
from integrations.external.mail import guard
from integrations.external.mail import mime as mail_mime
from integrations.external.mail.guard import (
    MailError,
    SideResult,
    config_error,
    network_error,
    protocol_error,
    timeout_error,
)

#: The common IMAP authenticator error; matched by text because imaplib raises
#: the same ``IMAP4.error`` for a rejected login and for other server errors.
_AUTH_HINTS = ("authentication", "auth", "login", "credential", "password",
               "invalid user", "authorization")


# -- connection --------------------------------------------------------------

class ImapSession:
    """A thin, testable wrapper over an ``imaplib``-shaped connection."""

    def __init__(self, connection: Any, *, verification: str = "none") -> None:
        self._conn = connection
        self.verification = verification

    # -- lifecycle -----------------------------------------------------------

    def login(self, user: str, password: str) -> None:
        try:
            typ, _data = self._conn.login(user, password)
        except imaplib.IMAP4.error as exc:
            text = str(exc).lower()
            if any(hint in text for hint in _AUTH_HINTS) or not text:
                raise MailError(
                    "the server rejected the username or password",
                    code="auth_failed", stage=STAGE_AUTH) from exc
            raise protocol_error("the server refused the IMAP login",
                                 code="imap_error") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the IMAP server closed the connection",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise MailError("the server rejected the username or password",
                            code="auth_failed", stage=STAGE_AUTH)

    def logout(self) -> None:
        try:
            self._conn.logout()
        except Exception:  # noqa: BLE001 - a failed logout must not mask a result
            pass

    # -- read-only access ----------------------------------------------------

    def select(self, mailbox: str, *, readonly: bool = True) -> int:
        try:
            typ, data = self._conn.select(str(mailbox or "INBOX"),
                                          readonly=readonly)
        except imaplib.IMAP4.error as exc:
            raise protocol_error("the mailbox could not be opened",
                                 code="mailbox_unavailable") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the connection was lost",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise protocol_error("the mailbox could not be opened",
                                 code="mailbox_unavailable")
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def search(self, criteria: Sequence[str]) -> List[bytes]:
        try:
            typ, data = self._conn.uid("SEARCH", None, *list(criteria))
        except imaplib.IMAP4.error as exc:
            raise protocol_error("the search was rejected by the server",
                                 code="search_failed") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the connection was lost",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise protocol_error("the search was rejected by the server",
                                 code="search_failed")
        raw = data[0] if data else b""
        if isinstance(raw, str):
            raw = raw.encode("ascii", "replace")
        return [uid for uid in (raw or b"").split() if uid]

    def fetch_raw(self, uid: Any) -> bytes:
        try:
            typ, data = self._conn.uid("FETCH", _uid_text(uid),
                                       "(BODY.PEEK[] FLAGS)")
        except imaplib.IMAP4.error as exc:
            raise protocol_error("the message could not be fetched",
                                 code="fetch_failed") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the connection was lost",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise protocol_error("the message could not be fetched",
                                 code="fetch_failed")
        body = b""
        for item in data or ():
            if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                body += item[1]
        if not body:
            raise protocol_error("the message does not exist",
                                 code="message_not_found")
        return body

    def fetch_summary(self, uid: Any) -> dict:
        try:
            typ, data = self._conn.uid(
                "FETCH", _uid_text(uid),
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS)")
        except imaplib.IMAP4.error as exc:
            raise protocol_error("the message list could not be read",
                                 code="fetch_failed") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the connection was lost",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise protocol_error("the message list could not be read",
                                 code="fetch_failed")
        headers = b""
        seen = False
        for item in data or ():
            if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                headers += item[1]
                if b"\\Seen" in (item[1] or b""):
                    seen = True
            elif isinstance(item, (bytes, bytearray)) and b"\\Seen" in item:
                seen = True
        parsed = email.message_from_bytes(headers) if headers else None
        return {
            "uid": _uid_text(uid),
            "from": str(parsed.get("From") or "") if parsed else "",
            "subject": str(parsed.get("Subject") or "") if parsed else "",
            "date": str(parsed.get("Date") or "") if parsed else "",
            "seen": bool(seen),
        }

    def store(self, uids: Sequence[Any], *, seen: bool) -> int:
        op = "+FLAGS" if seen else "-FLAGS"
        joined = ",".join(_uid_text(uid) for uid in uids)
        try:
            typ, _data = self._conn.uid("STORE", joined, op, "(\\Seen)")
        except imaplib.IMAP4.error as exc:
            raise protocol_error("the server refused to change the flags",
                                 code="mark_failed") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise network_error("the connection was lost",
                                code="connection_lost") from exc
        if str(typ).upper() != "OK":
            raise protocol_error("the server refused to change the flags",
                                 code="mark_failed")
        return len(list(uids))


class _PinnedIMAP4(imaplib.IMAP4):
    """``imaplib.IMAP4`` over a socket we already connected and permitted.

    The default implementation resolves the hostname again inside
    ``socket.create_connection``; that second resolution is exactly what the
    network policy forbids (a name can resolve differently the second time). The
    pinned socket is the address that was checked.
    """

    def __init__(self, pinned: socket.socket, host: str, port: int,
                 timeout: float) -> None:
        self._pinned_socket: Optional[socket.socket] = pinned
        super().__init__(host, port, timeout)

    def _create_socket(self, timeout: Any) -> socket.socket:
        sock = self._pinned_socket
        self._pinned_socket = None
        if sock is None:  # pragma: no cover - the socket is consumed once
            raise MailError("the pinned IMAP socket was already consumed",
                            code="internal_error", stage=STAGE_INTERNAL)
        return sock


def _default_connector(ctx, side: Mapping[str, Any]) -> ImapSession:
    limits = guard.limits_from(ctx)
    target = guard.resolve_target(
        ctx, side.get("host"), side.get("port"),
        tls=bool(side.get("tls", True)),
        verify_requested=bool(side.get("reject_unauthorized", True)))
    raw = guard.connect_raw(ctx, target, timeout=limits.connect_timeout)
    if target.tls:
        raw = guard.wrap_tls(raw, target, timeout=limits.connect_timeout)
    connection = _PinnedIMAP4(raw, target.host, target.port,
                              limits.connect_timeout)
    return ImapSession(connection, verification=target.verification)


def open_connection(ctx, side: Mapping[str, Any]) -> ImapSession:
    """Open (and not yet authenticate) the IMAP session.

    A module-level seam so a test can substitute a connection without a network
    or a real server, exactly as the reference implementation's stdlib calls can
    be stubbed.
    """
    return _default_connector(ctx, side)


# -- probe -------------------------------------------------------------------

def probe(ctx, side: Mapping[str, Any]) -> SideResult:
    """Log in and log out. Never fetches a body or changes a flag."""
    started = time.monotonic()
    verification = guard.attempted_verification(side)
    try:
        session = open_connection(ctx, side)
    except BaseException as exc:  # noqa: BLE001 - mapped to a staged result
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return guard.side_failure(exc, side=side, verification=verification,
                                  started=started)
    password = ""
    try:
        password = ctx.secret("imap_password")
        session.login(str(side.get("user") or ""), password)
        ctx.check_alive()
        return SideResult(ok=True, detail="login succeeded",
                          duration_ms=int((time.monotonic() - started) * 1000),
                          verification=session.verification,
                          host=str(side.get("host") or ""),
                          port=int(side.get("port") or 0),
                          tls=bool(side.get("tls", True)))
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = guard.side_failure(exc, side=side,
                                    verification=verification,
                                    started=started)
        result.detail = guard.redact(result.detail, [password])
        return result
    finally:
        session.logout()


# -- read actions ------------------------------------------------------------

def _imap_date(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise config_error("dates must be YYYY-MM-DD",
                           code="invalid_date") from exc
    return parsed.strftime("%d-%b-%Y").lstrip("0")


def _uid_text(uid: Any) -> str:
    if isinstance(uid, (bytes, bytearray)):
        return bytes(uid).decode("ascii", "replace")
    return str(uid)


def search_criteria(params: Mapping[str, Any]) -> List[str]:
    criteria: List[str] = []
    if params.get("unseen"):
        criteria += ["UNSEEN"]
    if params.get("seen"):
        criteria += ["SEEN"]
    if params.get("from"):
        criteria += ["FROM", str(params["from"])]
    if params.get("subject"):
        criteria += ["SUBJECT", str(params["subject"])]
    if params.get("since"):
        criteria += ["SINCE", _imap_date(params["since"])]
    if params.get("before"):
        criteria += ["BEFORE", _imap_date(params["before"])]
    return criteria or ["ALL"]


def _login(ctx, side: Mapping[str, Any], session: ImapSession) -> None:
    session.login(str(side.get("user") or ""), ctx.secret("imap_password"))


def search(ctx, side: Mapping[str, Any], params: Mapping[str, Any]) -> dict:
    limits = guard.limits_from(ctx)
    mailbox = str(params.get("mailbox") or side.get("mailbox")
                  or limits.default_mailbox)
    limit = min(int(params.get("limit") or limits.search_limit),
                limits.search_limit)
    criteria = search_criteria(params)
    session = open_connection(ctx, side)
    try:
        _login(ctx, side, session)
        ctx.check_alive()
        session.select(mailbox, readonly=True)
        uids = session.search(criteria)
        ctx.check_alive()
        page = list(reversed(uids[-limit:])) if limit > 0 else []
        messages = [session.fetch_summary(uid) for uid in page]
        return {
            "mailbox": mailbox,
            "criteria": criteria,
            "count": len(uids),
            "uids": [_uid_text(uid) for uid in page],
            "messages": messages,
        }
    finally:
        session.logout()


def read(ctx, side: Mapping[str, Any], params: Mapping[str, Any]) -> dict:
    limits = guard.limits_from(ctx)
    uid = params.get("uid")
    if uid in (None, ""):
        raise config_error("uid is required", code="uid_required")
    mailbox = str(params.get("mailbox") or side.get("mailbox")
                  or limits.default_mailbox)
    session = open_connection(ctx, side)
    try:
        _login(ctx, side, session)
        ctx.check_alive()
        session.select(mailbox, readonly=True)
        raw = session.fetch_raw(uid)
        parsed = mail_mime.read_message(raw, max_body_bytes=limits.max_read_bytes)
        return {"uid": _uid_text(uid), "mailbox": mailbox, **parsed}
    finally:
        session.logout()


def save_attachments(ctx, side: Mapping[str, Any],
                     params: Mapping[str, Any]) -> dict:
    limits = guard.limits_from(ctx)
    uid = params.get("uid")
    if uid in (None, ""):
        raise config_error("uid is required", code="uid_required")
    mailbox = str(params.get("mailbox") or side.get("mailbox")
                  or limits.default_mailbox)
    wanted = str(params.get("filename") or "").strip()
    index = params.get("index")
    directory = params.get("dir")
    session = open_connection(ctx, side)
    try:
        _login(ctx, side, session)
        ctx.check_alive()
        session.select(mailbox, readonly=True)
        raw = session.fetch_raw(uid)
        found = mail_mime.extract_attachments(raw)
        if wanted:
            found = [item for item in found
                     if str(item.get("filename") or "") == wanted]
        elif index not in (None, ""):
            try:
                found = [found[int(index)]]
            except (ValueError, IndexError):
                raise config_error("no attachment at that index",
                                   code="attachment_missing")
        if not found:
            raise config_error("the message has no matching attachment",
                               code="attachment_missing")
        if len(found) > limits.max_attachment_count:
            raise config_error("too many attachments",
                               code="too_many_attachments")
        saved = []
        total = 0
        for item in found:
            data = bytes(item.get("data") or b"")
            total += len(data)
            if total > limits.max_attachment_bytes:
                raise config_error("the attachments exceed the size limit",
                                   code="attachment_too_large")
            filename = guard.sanitize_message_filename(item.get("filename"))
            saved.append(guard.write_attachment(
                ctx, directory=directory, filename=filename, data=data))
        return {"uid": _uid_text(uid), "mailbox": mailbox,
                "saved": saved, "count": len(saved)}
    finally:
        session.logout()


def mark(ctx, side: Mapping[str, Any], params: Mapping[str, Any]) -> dict:
    limits = guard.limits_from(ctx)
    uids = params.get("uids")
    if uids in (None, ""):
        single = params.get("uid")
        uids = [single] if single not in (None, "") else []
    if isinstance(uids, (str, bytes)):
        uids = [part for part in str(uids).split(",") if part.strip()]
    if not isinstance(uids, (list, tuple)) or not uids:
        raise config_error("uids is required", code="uid_required")
    if len(uids) > 200:
        raise config_error("too many messages to mark",
                           code="too_many_messages")
    seen = bool(params.get("seen", True))
    mailbox = str(params.get("mailbox") or side.get("mailbox")
                  or limits.default_mailbox)
    session = open_connection(ctx, side)
    try:
        _login(ctx, side, session)
        ctx.check_alive()
        session.select(mailbox, readonly=False)
        count = session.store(list(uids), seen=seen)
        return {"mailbox": mailbox, "seen": seen,
                "uids": [_uid_text(uid) for uid in uids], "count": count}
    finally:
        session.logout()
