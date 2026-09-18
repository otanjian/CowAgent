# encoding:utf-8
"""The SMTP side: a login-only probe and a send that knows when it is unsure.

The distinction this module exists for is stated in the spec: a send is either

* a **definitive failure** — the conversation failed before the message could
  have been accepted (connect, greeting, ``EHLO``, ``MAIL FROM``, ``RCPT TO``,
  or ``DATA`` refused), which may be retried after the cause is fixed; or
* **unknown** — the message content was handed to the server and the final
  answer was lost (timeout, dropped connection, or a cancelled wait). The server
  may have accepted the mail, so the caller must not resend automatically.

The probe never sends a message: it connects, greets and logs in, nothing else,
matching the reference implementation's "只做登录验证".
"""

from __future__ import annotations

import smtplib
import socket
import time
from typing import Any, Mapping, Optional, Sequence

from integrations.external.adapters.base import STAGE_AUTH, STAGE_PROTOCOL
from integrations.external.mail import guard
from integrations.external.mail.guard import (
    MailError,
    SideResult,
    network_error,
    timeout_error,
)

_CRLF = b"\r\n"


class SendUnknown(Exception):
    """The message may have been accepted; resending could duplicate it."""

    def __init__(self, message: str, *, code: str = "send_unknown",
                 stage: str = STAGE_PROTOCOL) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


class _PinnedSMTP(smtplib.SMTP):
    """``smtplib.SMTP`` on a socket the network policy already permitted.

    The default ``SMTP._get_socket`` resolves the hostname again; pinning the
    socket keeps the connection on the address that passed the policy/address
    check.
    """

    def __init__(self, pinned: socket.socket, host: str, port: int,
                 timeout: float) -> None:
        self._pinned_socket: Optional[socket.socket] = pinned
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):  # noqa: D102 - see class doc
        sock = self._pinned_socket
        self._pinned_socket = None
        if sock is None:  # pragma: no cover - consumed exactly once
            raise MailError("the pinned SMTP socket was already consumed",
                            code="internal_error", stage=STAGE_PROTOCOL)
        return sock


def _map_exception(exc: BaseException, *, phase: str) -> BaseException:
    """Translate a transport/protocol error into a staged refusal or unknown.

    ``phase`` is ``"envelope"`` before any message content is written and
    ``"content"`` afterwards. Only the content phase may be ambiguous.
    """
    ambiguous = phase == "content"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError("the server rejected the username or password",
                         code="auth_failed", stage=STAGE_AUTH)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return MailError("the server refused the sender address",
                         code="sender_refused", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return MailError("every recipient was refused",
                         code="recipients_refused", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPDataError):
        return MailError("the server rejected the message",
                         code="send_rejected", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailError("the server does not support the required command",
                         code="smtp_unsupported", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPHeloError):
        return MailError("the server refused the greeting",
                         code="smtp_error", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPResponseException):
        return MailError("the server returned an error",
                         code="smtp_error", stage=STAGE_PROTOCOL)
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        if ambiguous:
            return SendUnknown(
                "the connection dropped after the message was sent; it may "
                "have been accepted", code="send_unknown")
        return MailError("the server closed the connection",
                         code="connection_lost", stage=STAGE_PROTOCOL)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        if ambiguous:
            return SendUnknown(
                "the server did not answer after the message was sent; it may "
                "have been accepted", code="send_unknown")
        return timeout_error()
    if isinstance(exc, OSError):
        if ambiguous:
            return SendUnknown(
                "the connection failed after the message was sent; it may "
                "have been accepted", code="send_unknown")
        return network_error("the connection to the mail server failed",
                             code="network_unreachable")
    return exc


def _prepare_data(data: bytes) -> bytes:
    """CRLF-terminate and dot-stuff the message for the DATA phase."""
    body = bytes(data or b"")
    body = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    body = body.replace(b"\n", b"\r\n")
    body = body.replace(b"\r\n.", b"\r\n..")
    if body.startswith(b"."):
        body = b"." + body
    if not body.endswith(_CRLF):
        body += _CRLF
    return body + b"." + _CRLF


class SmtpSession:
    """A thin wrapper over an ``smtplib``-shaped connection."""

    def __init__(self, connection: Any, *, verification: str = "none") -> None:
        self._conn = connection
        self.verification = verification

    # -- lifecycle -----------------------------------------------------------

    def greet(self) -> None:
        try:
            self._conn.ehlo_or_helo_if_needed()
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="envelope") from exc

    def login(self, user: str, password: str) -> None:
        try:
            self._conn.login(user, password)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="envelope") from exc

    def quit(self) -> None:
        try:
            self._conn.quit()
        except Exception:  # noqa: BLE001 - a failed QUIT must not mask a result
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    # -- delivery ------------------------------------------------------------

    def deliver(self, ctx, *, from_addr: str, recipients: Sequence[str],
                data: bytes) -> dict:
        """MAIL FROM / RCPT TO / DATA, with the ambiguity boundary marked.

        Raises :class:`MailError` for a definitive refusal and
        :class:`SendUnknown` once the message may have been accepted.
        """
        conn = self._conn
        self.greet()
        ctx.check_alive()

        # MAIL FROM
        try:
            code, _resp = conn.mail(from_addr, self._mail_options(data))
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="envelope") from exc
        if int(code) not in (250, 251):
            raise MailError("the server refused the sender address",
                            code="sender_refused", stage=STAGE_PROTOCOL)

        # RCPT TO, one transaction, several recipients (To/Cc/Bcc all here; a
        # Bcc is an envelope recipient only and never a header).
        accepted = []
        refused = {}
        for recipient in recipients:
            ctx.check_alive()
            try:
                code, resp = conn.rcpt(recipient)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise _map_exception(exc, phase="envelope") from exc
            if int(code) in (250, 251):
                accepted.append(recipient)
            else:
                refused[recipient] = (int(code), guard.redact(resp))
        if not accepted:
            raise MailError("every recipient was refused",
                            code="recipients_refused", stage=STAGE_PROTOCOL)

        # DATA. The server's 354 is the last point at which nothing of the
        # message has been written, so a cancellation before it is still clean.
        try:
            conn.putcmd("data")
            code, _resp = conn.getreply()
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="envelope") from exc
        if int(code) != 354:
            raise MailError("the server did not accept the message",
                            code="send_rejected", stage=STAGE_PROTOCOL)

        ctx.check_alive()  # a cancel here aborts before anything is written

        # From here on the outcome can be ambiguous: the content is on the wire.
        try:
            conn.send(_prepare_data(data))
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="content") from exc
        try:
            code, resp = conn.getreply()
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _map_exception(exc, phase="content") from exc
        if int(code) != 250:
            # The server answered the final dot with a rejection: the message
            # was definitively not accepted.
            raise MailError("the server rejected the message",
                            code="send_rejected", stage=STAGE_PROTOCOL,
                            )
        return {"accepted": list(accepted), "refused": refused}

    @staticmethod
    def _mail_options(data: bytes) -> tuple:
        # The message is built 7-bit safe, so 8BITMIME is only ever an
        # optimisation; never request it when the transport is plain ASCII.
        return ()


def _default_connector(ctx, side: Mapping[str, Any]) -> SmtpSession:
    limits = guard.limits_from(ctx)
    mode = str(side.get("tls_mode") or "").strip().lower()
    if not mode:
        mode = "starttls" if side.get("tls") else "none"
    tls_wanted = mode in ("implicit_tls", "starttls")
    target = guard.resolve_target(
        ctx, side.get("host"), side.get("port"), tls=tls_wanted,
        verify_requested=bool(side.get("reject_unauthorized", True)))
    raw = guard.connect_raw(ctx, target, timeout=limits.connect_timeout)
    if mode == "implicit_tls":
        raw = guard.wrap_tls(raw, target, timeout=limits.connect_timeout)
    connection = _PinnedSMTP(raw, target.host, target.port,
                             limits.connect_timeout)
    session = SmtpSession(connection, verification=target.verification)
    if mode == "starttls":
        try:
            connection.starttls(context=guard.tls_context(target.verify))
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error() from exc
        except OSError as exc:
            raise guard.tls_error("the STARTTLS handshake failed") from exc
        session.verification = target.verification
    return session


def open_connection(ctx, side: Mapping[str, Any]) -> SmtpSession:
    """Open (and not yet authenticate) the SMTP session.

    A module-level seam so a test can substitute a scripted connection without a
    network or a real server.
    """
    return _default_connector(ctx, side)


# -- probe -------------------------------------------------------------------

def probe(ctx, side: Mapping[str, Any]) -> SideResult:
    """Connect, greet and log in. Never sends a message."""
    started = time.monotonic()
    verification = guard.attempted_verification(side)
    try:
        session = open_connection(ctx, side)
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return guard.side_failure(exc, side=side, verification=verification,
                                  started=started)
    user = str(side.get("user") or "")
    password = ""
    try:
        session.greet()
        if user:
            password = ctx.secret("smtp_password")
            session.login(user, password)
        elif ctx.has_secret("smtp_password"):
            password = ctx.secret("smtp_password")
        ctx.check_alive()
        return SideResult(ok=True, detail="login succeeded",
                          duration_ms=int((time.monotonic() - started) * 1000),
                          verification=session.verification,
                          host=str(side.get("host") or ""),
                          port=int(side.get("port") or 0),
                          tls=bool(side.get("tls", True)) or (
                              str(side.get("tls_mode") or "") != "none"))
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = guard.side_failure(exc, side=side,
                                    verification=verification,
                                    started=started)
        result.detail = guard.redact(result.detail, [password])
        return result
    finally:
        session.quit()


# -- send --------------------------------------------------------------------

def send(ctx, side: Mapping[str, Any], *, from_addr: str,
         recipients: Sequence[str], data: bytes) -> dict:
    """Authenticate and deliver one message.

    Raises :class:`SendUnknown` when the message may have been accepted, and
    :class:`MailError` when it definitively was not.
    """
    session = open_connection(ctx, side)
    try:
        session.greet()
        user = str(side.get("user") or "")
        if user:
            session.login(user, ctx.secret("smtp_password"))
        outcome = session.deliver(ctx, from_addr=from_addr,
                                  recipients=recipients, data=data)
        outcome["verification"] = session.verification
        return outcome
    finally:
        session.quit()
