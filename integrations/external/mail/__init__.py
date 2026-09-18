# encoding:utf-8
"""Mail protocol client for the personal-email connection type.

Kept out of the adapter so ``integrations/external/adapters/email.py`` stays a
thin "configure / probe / describe / invoke" implementation, and so the pieces
that need their own tests are addressable on their own:

* :mod:`integrations.external.mail.guard` — the deployment's network policy
  applied to raw TCP+TLS, TLS verification honesty, attachment path bounds,
  redaction and the send duplicate-submit guard. It is the only place a mail
  socket, a mail path or a password leaves the process boundary.
* :mod:`integrations.external.mail.mime` — message construction (subject
  encoding, content types, recipients) and parsing.
* :mod:`integrations.external.mail.imap` — the IMAP side: login probe,
  read-only search/read/attachment extraction, and the separately authorized
  read/unread marker.
* :mod:`integrations.external.mail.smtp` — the SMTP side: login-only probe and
  the send that distinguishes "definitely not sent" from "may have been
  accepted" (``outcome_unknown``).

Nothing here resolves a secret, picks a tenant or reads deployment readiness:
the caller builds an :class:`~integrations.external.adapters.base.ExecutionContext`
and the adapter passes the already-resolved side configuration down.
"""

from __future__ import annotations

__all__ = ["guard", "imap", "mime", "smtp"]
