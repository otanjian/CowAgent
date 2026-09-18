# encoding:utf-8
"""Redaction for OA diagnostics and invocation messages.

The spec forbids a secret, a cookie value or a session id from appearing in a
``StageResult.detail`` or a ``ProbeResult.metadata`` ("测试 SHALL 检查无秘密回显",
"日志不含秘密"). OneAgent's diagnostic chain embeds raw remote response bodies,
which can echo back the very value that was submitted or the session id that was
issued. Every string that leaves this package therefore goes through
:class:`Redactor`.

Two layers, because either alone is insufficient:

* the *known* secrets of this attempt (the submitted password, the OpenAPI
  app_secret, the cookie values observed so far) are replaced literally;
* a shape-based pass masks anything that looks like a session token / access
  token assignment, which catches a value this attempt did not itself supply
  (a remote-issued session key, say) before it can be logged.
"""

from __future__ import annotations

import re
from typing import Iterable, List

_PLACEHOLDER = "•••"
_MAX_DETAIL = 300

#: ``token=abc`` / ``sessionkey: "abc"`` / ``JSESSIONID=abc`` … in a response
#: body or an exception message.
_TOKEN_ASSIGNMENT = re.compile(
    r"(?i)(sessionkey|session_key|sessionid|jsessionid|loginidweaver|extloginid"
    r"|testid|access_token|accesstoken|refreshtoken|refresh_token|token"
    r"|password|passwd|secret|app_secret|cookie)"
    r"\s*[=:]\s*[\"']?([A-Za-z0-9._~+/=-]{6,})"
)


class Redactor:
    """Replace known secret values and token-shaped text with a placeholder."""

    def __init__(self, values: Iterable[str] = (), *,
                 max_detail: int = _MAX_DETAIL) -> None:
        cleaned: List[str] = []
        for value in values or ():
            text = str(value or "")
            if len(text) >= 3:
                cleaned.append(text)
        # Longest first so a value that contains another is masked whole.
        self._values = sorted(set(cleaned), key=len, reverse=True)
        self._max_detail = int(max_detail)

    def add(self, *values: str) -> None:
        merged = set(self._values)
        for value in values or ():
            text = str(value or "")
            if len(text) >= 3:
                merged.add(text)
        self._values = sorted(merged, key=len, reverse=True)

    def __call__(self, text: object) -> str:
        out = str(text if text is not None else "")
        for value in self._values:
            if value and value in out:
                out = out.replace(value, _PLACEHOLDER)
        out = _TOKEN_ASSIGNMENT.sub(
            lambda match: "%s=%s" % (match.group(1), _PLACEHOLDER), out)
        out = " ".join(out.split())
        if len(out) > self._max_detail:
            out = out[: self._max_detail] + "…"
        return out

    def values(self) -> List[str]:
        return list(self._values)


def redact_list(redactor: Redactor, values: Iterable[object],
                *, limit: int = 50) -> List[str]:
    """A bounded, redacted list for metadata (e.g. discovered API paths)."""

    out: List[str] = []
    for value in list(values)[:limit]:
        text = redactor(value)
        if text:
            out.append(text)
    return out
