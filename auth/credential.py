# encoding:utf-8
"""Unified credential selection for the database identity mode (task 2.2).

In database mode only two credential sources are trusted: the ``cow_session``
cookie and the ``Authorization: Bearer <token>`` header. Old shared-password
HMAC tokens, URL query tokens and an empty ``web_password`` never grant a
database identity.

The selection rules are fixed and deliberately non-fallback:

* only a cookie  -> cookie-authenticated; cookie-origin check applies on writes.
* only a bearer  -> bearer-authenticated; a *successful* bearer authentication
  is the only thing that exempts the cookie-origin check.
* cookie and bearer carry the SAME value -> authenticate once and treat it as a
  cookie request (the old client sent both); the cookie origin check still
  applies.
* cookie and bearer carry DIFFERENT values, or either is malformed, or the
  selected credential is invalid -> do NOT fall back to the other one. Different
  values yield ``400 mixed_credentials``; the rest map to a normal auth error.

The module is transport-agnostic about where headers live; the callers pass the
raw header strings (or an env-like mapping) so the same logic is exercised by
the real ``web.ctx.env`` path and by tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CredentialSelection:
    """The outcome of credential selection for one request."""

    #: The chosen session token to authenticate ("" if none was selected).
    token: str
    #: How the token was presented: "cookie", "bearer", or "".
    source: str
    #: True when both a cookie and a bearer were provided.
    both_present: bool
    #: True when the cookie and bearer values differed (a hard 400).
    mixed: bool


def select_credential(cookie: str, bearer: str) -> CredentialSelection:
    """Select the session credential from a cookie value and a bearer token.

    ``cookie`` is the raw ``cow_session`` cookie value ("" when absent).
    ``bearer`` is the raw token from ``Authorization: Bearer <token>`` ("" when
    absent or not a bearer form). The function never throws; callers translate
    the ``mixed`` flag into a 400 and an empty token/source into a 401.
    """
    cookie = (cookie or "").strip()
    bearer = (bearer or "").strip()
    both = bool(cookie) and bool(bearer)
    if both:
        if cookie == bearer:
            # Same value repeated: authenticate once, treat as a cookie request.
            return CredentialSelection(token=cookie, source="cookie",
                                       both_present=True, mixed=False)
        # Different values: do not fall back to either.
        return CredentialSelection(token="", source="", both_present=True, mixed=True)
    if cookie:
        return CredentialSelection(token=cookie, source="cookie",
                                   both_present=False, mixed=False)
    if bearer:
        return CredentialSelection(token=bearer, source="bearer",
                                   both_present=False, mixed=False)
    return CredentialSelection(token="", source="", both_present=False, mixed=False)


def bearer_token_from_header(authorization: str) -> str:
    """Return the token from an ``Authorization`` header value, or "".

    Only the ``Bearer <token>`` scheme is recognized; anything else (e.g. a bare
    token, ``Basic``, or a bearer-looking string that isn't well-formed) yields
    "" so a malformed credential never silently authenticates.
    """
    authorization = (authorization or "").strip()
    if not authorization.startswith("Bearer "):
        return ""
    token = authorization[len("Bearer "):].strip()
    # A well-formed bearer must be a non-empty token without inner whitespace.
    if not token or any(c.isspace() for c in token):
        return ""
    return token
