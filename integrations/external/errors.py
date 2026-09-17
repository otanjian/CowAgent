# encoding:utf-8
"""Errors for the external-connection control plane.

One exception type carrying the same ``(message, code, status)`` shape the rest
of the identity layer uses, so the HTTP layer can map a refusal onto the
project's unified error contract without knowing which check refused.

Codes are stable machine strings (never the message text) so the console can
branch on them and the tests can pin them:
``forbidden`` (403), ``not_found`` (404), ``conflict``/``version_conflict``/
``upgrade_required`` (409), ``invalid`` (400), ``unavailable`` (503).
"""

from __future__ import annotations


class ExternalConnectionError(RuntimeError):
    """A refusal with a stable machine code and an HTTP status class."""

    def __init__(self, message: str, code: str = "invalid", status: int = 400,
                 fields: dict = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.fields = dict(fields or {})


def invalid(message: str, *, code: str = "invalid", fields: dict = None):
    return ExternalConnectionError(message, code=code, status=400,
                                   fields=fields)


def forbidden(message: str = "forbidden", *, code: str = "forbidden"):
    return ExternalConnectionError(message, code=code, status=403)


def not_found(message: str = "connection not found"):
    return ExternalConnectionError(message, code="not_found", status=404)


def conflict(message: str, *, code: str = "conflict"):
    return ExternalConnectionError(message, code=code, status=409)


def unavailable(message: str, *, code: str = "unavailable"):
    return ExternalConnectionError(message, code=code, status=503)
