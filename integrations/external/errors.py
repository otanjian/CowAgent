# encoding:utf-8
"""Errors for the external-connection control plane.

One exception type carrying the same ``(message, code, status)`` shape the rest
of the identity layer uses, so the HTTP layer can map a refusal onto the
project's unified error contract without knowing which check refused.

It is a **subclass of the adapter contract's error** rather than a sibling,
because the two are one refusal seen at two layers: an adapter needs a
``stage`` to report where the attempt failed, the API needs a ``status`` to
answer with. Unifying them means every seam that catches one of the two
catches both — the failure mode of two parallel hierarchies is an adapter
raising a type the runtime's handler does not name, which turns a refusal into
a 500.

Codes are stable machine strings (never the message text) so the console can
branch on them and the tests can pin them:
``forbidden`` (403), ``not_found`` (404), ``conflict``/``version_conflict``/
``upgrade_required`` (409), ``invalid`` (400), ``unavailable`` (503).
"""

from __future__ import annotations

from integrations.external.adapters.base import (
    AdapterError,
    STAGE_CONFIG,
    STAGE_INTERNAL,
    STAGE_POLICY,
)


class ExternalConnectionError(AdapterError):
    """A refusal with a stable machine code, an HTTP status and an adapter stage."""

    def __init__(self, message: str, code: str = "invalid", status: int = 400,
                 fields: dict = None, stage: str = STAGE_INTERNAL):
        super().__init__(message, code=code, stage=stage)
        self.status = status
        self.fields = dict(fields or {})


def invalid(message: str, *, code: str = "invalid", fields: dict = None,
            stage: str = STAGE_CONFIG):
    """A 400: the caller's request or configuration is not acceptable.

    Defaults to the ``config`` stage because that is what a 400 means in the
    staged vocabulary a probe uses — the request never got as far as a target.
    """
    return ExternalConnectionError(message, code=code, status=400,
                                   fields=fields, stage=stage)


def forbidden(message: str = "forbidden", *, code: str = "forbidden",
              stage: str = STAGE_POLICY):
    """A 403: an authorization or deployment-policy decision said no.

    The ``policy`` stage is the default because that is what a 403 means to a
    caller rendering a staged attempt: nothing was attempted, the deployment
    refused. Carrying it here (rather than letting a caller default it) is what
    keeps an approval refusal and an open-class refusal indistinguishable in
    vocabulary from a network-policy refusal — they are the same answer.
    """
    return ExternalConnectionError(message, code=code, status=403, stage=stage)


def not_found(message: str = "connection not found", *,
              stage: str = STAGE_INTERNAL):
    return ExternalConnectionError(message, code="not_found", status=404,
                                   stage=stage)


def conflict(message: str, *, code: str = "conflict",
             stage: str = STAGE_CONFIG):
    return ExternalConnectionError(message, code=code, status=409, stage=stage)


def unavailable(message: str, *, code: str = "unavailable",
                stage: str = STAGE_POLICY):
    return ExternalConnectionError(message, code=code, status=503, stage=stage)
