# encoding:utf-8
"""Message construction and parsing for the mail tools.

The two things this module has to get exactly right, because a person sees the
result and no later layer can fix it:

* a non-ASCII subject is RFC 2047 encoded, and every attachment carries the
  content type its file name implies (``application/octet-stream`` when the type
  cannot be determined);
* a Bcc recipient is an *envelope* recipient only — it is passed to ``RCPT TO``
  and never written into the message headers.

Parsing is deliberately defensive: a message is untrusted input, so HTML is
reduced to text (a mail body must not be rendered as live markup), sizes are
capped, and an attachment is exposed as bytes plus a sanitised name.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import html as _html
import mimetypes
import re
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from integrations.external.mail.guard import MailError, config_error

#: A pragmatic address check: one ``@``, a non-empty local part and a dotted
#: domain. The registry's looser check already ran for the *configuration*;
#: this one guards a per-call recipient, which is a different trust level.
_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")

_TAG = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_ANY_TAG = re.compile(r"(?s)<[^>]*>")
_BLANK_LINES = re.compile(r"\n{3,}")

#: CRLF line endings (required on the wire) and a 7-bit safe body: a non-ASCII
#: body is transferred as base64/quoted-printable rather than raw 8-bit bytes, so
#: the message does not depend on the server advertising 8BITMIME. Headers
#: (including a non-ASCII Subject) are RFC 2047 encoded by this policy.
_POLICY = email.policy.SMTP.clone(cte_type="7bit")


def is_valid_address(value: str) -> bool:
    return bool(_ADDRESS.match(str(value or "").strip()))


def parse_recipients(value: Any) -> List[str]:
    """Accept a string (comma/semicolon separated) or a sequence of strings."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;\s]+", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.extend(re.split(r"[,;]+", item))
            elif item in (None, ""):
                continue
            else:
                raise config_error("recipient entries must be strings",
                                   code="invalid_recipient")
    else:
        raise config_error("recipients must be a string or a list",
                           code="invalid_recipient")
    return [part.strip() for part in parts if part and part.strip()]


def validate_recipients(addresses: Iterable[str], *, field: str,
                        limit: int) -> List[str]:
    out: List[str] = []
    for address in addresses:
        text = str(address).strip()
        if not is_valid_address(text):
            raise config_error("invalid %s address" % field,
                               code="invalid_recipient")
        out.append(text)
    if len(out) > limit:
        raise config_error("too many recipients", code="too_many_recipients")
    return out


def guess_content_type(filename: str) -> Tuple[str, str]:
    guessed, _encoding = mimetypes.guess_type(str(filename or ""))
    if not guessed or "/" not in guessed:
        return "application", "octet-stream"
    maintype, subtype = guessed.split("/", 1)
    return maintype, subtype


def html_to_text(markup: str) -> str:
    """Reduce an HTML body to readable text.

    A mail body is data, never a document to execute: script/style content is
    dropped before any tag is removed, so no remote or inline script can reach a
    renderer even if a later layer forgets to escape it.
    """
    text = str(markup or "")
    text = _TAG.sub(" ", text)
    text = _COMMENT.sub(" ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = _ANY_TAG.sub(" ", text)
    text = _html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANK_LINES.sub("\n\n", text).strip()
    return text


def build_message(*, from_addr: str, to: Sequence[str], cc: Sequence[str] = (),
                  subject: str, body: str, html: bool = False,
                  attachments: Sequence[Mapping[str, Any]] = ()) -> bytes:
    """Build the message bytes. Bcc is deliberately not a parameter.

    The envelope recipients are the caller's business (see
    :mod:`integrations.external.mail.smtp`); the header set is built here and
    contains only From/To/Cc, so a Bcc cannot be leaked into the headers.
    """
    message = EmailMessage(policy=_POLICY)
    message["From"] = from_addr
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = email.utils.make_msgid(domain="external-connections")
    if html:
        text = html_to_text(body)
        message.set_content(text or " ")
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body or "")
    for attachment in attachments:
        data = bytes(attachment.get("data") or b"")
        filename = str(attachment.get("filename") or "attachment")
        maintype, subtype = guess_content_type(filename)
        message.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=filename)
    return message.as_bytes()


def _decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 - a malformed part is a label, not a crash
        return ""
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", "replace")


def _part_attachment(part) -> Optional[dict]:
    content_type = str(part.get_content_type() or "").lower()
    disposition = str(part.get("Content-Disposition") or "").lower()
    filename = part.get_filename()
    if "attachment" in disposition:
        return {"filename": filename, "content_type": content_type}
    if filename and content_type not in ("text/plain", "text/html"):
        return {"filename": filename, "content_type": content_type}
    return None


def read_message(raw: bytes, *, max_body_bytes: int = 1024 * 1024) -> dict:
    """Parse one message into headers, a text body and attachment metadata."""
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
    except Exception as exc:  # noqa: BLE001 - malformed mail is reported
        raise MailError("the message could not be parsed",
                        code="message_unreadable", stage="protocol") from exc
    headers = {
        key: str(message.get(key) or "")
        for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID")
        if message.get(key)
    }
    body_text = ""
    html_present = False
    attachments: List[dict] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        attachment = _part_attachment(part)
        if attachment is not None:
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": attachment["filename"],
                "content_type": attachment["content_type"],
                "size": len(payload),
            })
            continue
        content_type = str(part.get_content_type() or "").lower()
        if content_type == "text/plain" and not body_text:
            body_text = _decode_part(part)
        elif content_type == "text/html":
            html_present = True
            if not body_text:
                body_text = html_to_text(_decode_part(part))
    if len(body_text.encode("utf-8", "replace")) > int(max_body_bytes):
        body_text = body_text.encode("utf-8", "replace")[:int(max_body_bytes)] \
            .decode("utf-8", "replace")
    return {
        "headers": headers,
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "date": headers.get("Date", ""),
        "body": body_text,
        "html_found": html_present,
        "html_stripped": html_present,
        "attachments": attachments,
    }


def extract_attachments(raw: bytes) -> List[dict]:
    """Every attachment in a message, as ``{filename, content_type, data}``."""
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
    except Exception as exc:  # noqa: BLE001
        raise MailError("the message could not be parsed",
                        code="message_unreadable", stage="protocol") from exc
    out: List[dict] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        attachment = _part_attachment(part)
        if attachment is None:
            continue
        out.append({
            "filename": attachment["filename"],
            "content_type": attachment["content_type"],
            "data": part.get_payload(decode=True) or b"",
        })
    return out
