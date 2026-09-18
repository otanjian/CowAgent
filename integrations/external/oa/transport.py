# encoding:utf-8
"""A policy-checked HTTP session for the OA protocol client.

Why this exists rather than ``safe_get``
---------------------------------------
:func:`integrations.external.adapters.netpolicy.safe_get` deliberately models a
single bounded GET. OA needs more than that:

* **a persistent session** — the standard login flow warms up the login page to
  collect cookies and then submits ``VerifyLogin.jsp`` with those same cookies,
  and later business calls reuse ``JSESSIONID`` / ``loginidweaver``;
* **POST** — every OA login, approval and forward is a form or JSON POST.

Neither is available from the frozen foundation modules, so this module provides
them *without* weakening anything the delegation requires: each hop is checked
against the deployment's :class:`NetworkPolicy` (scheme, host, port, DNS
resolved address, SSRF/metadata denies), each redirect is re-checked with
:meth:`NetworkPolicy.check_redirect`, credentials are withheld on a cross-origin
hop, the response is bounded and every I/O call is bounded by
:meth:`ExecutionContext.io_timeout`. ``requests`` is imported lazily inside the
helpers so importing this package never needs the SDK.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from integrations.external.adapters.base import (
    STAGE_NETWORK,
    STAGE_POLICY,
    STAGE_TIMEOUT,
    STAGE_TLS,
    PolicyRefused,
)
from integrations.external.adapters.netpolicy import NetworkPolicy, current_policy

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_MAX_DETAIL = 200


@dataclass(frozen=True)
class HttpResult:
    """A bounded HTTP response, with the session's cookie jar snapshot."""

    status_code: int
    text: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    #: The response body before decoding. Kept because an attachment download is
    #: bytes (a PDF, an xlsx) and ``text`` has already replaced what would not
    #: decode — saving that would write a corrupted file.
    body: bytes = b""

    @property
    def content_type(self) -> str:
        return str(self.headers.get("content-type") or "")

    def header(self, name: str, default: str = "") -> str:
        return str(self.headers.get(str(name).lower(), default) or default)

    def body_bytes(self) -> bytes:
        """The raw body, falling back to the decoded text when none was kept."""
        if self.body:
            return self.body
        return str(self.text or "").encode("utf-8")

    def json(self) -> Any:
        return _json.loads(self.text)


class TransportError(Exception):
    """A network/TLS/timeout/policy failure, already classified by stage."""

    def __init__(self, message: str, *, code: str = "network_unreachable",
                 stage: str = STAGE_NETWORK, detail: str = "") -> None:
        super().__init__(message)
        self.code = str(code)
        self.stage = str(stage)
        self.detail = str(detail or message)


def _origin_of(url: str) -> tuple:
    parsed = urlparse(str(url or "").strip())
    scheme = (parsed.scheme or "").lower()
    try:
        port = int(parsed.port or (443 if scheme == "https" else 80))
    except ValueError:
        port = 0
    return scheme, (parsed.hostname or "").lower(), port


def _new_session(user_agent: str):
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


class PolicyTransport:
    """A cookie-preserving HTTP session that every hop is policy-checked on.

    The transport owns exactly one remote session. It is never shared across
    tenants or connection versions: the service builds one context per call and
    the client builds one transport per context, which is what the spec means by
    "远端会话不跨租户/连接版本复用".
    """

    def __init__(self, ctx: Any, *, session: Any = None,
                 user_agent: str = DEFAULT_USER_AGENT) -> None:
        self._ctx = ctx
        self._user_agent = user_agent
        policy = None
        limits = getattr(ctx, "limits", None)
        if isinstance(limits, Mapping):
            candidate = limits.get("policy")
            if isinstance(candidate, NetworkPolicy):
                policy = candidate
        self._policy: NetworkPolicy = policy or current_policy()
        self._session = session if session is not None else _new_session(user_agent)

    # -- introspection -------------------------------------------------------

    @property
    def policy(self) -> NetworkPolicy:
        return self._policy

    def cookies(self) -> Mapping[str, str]:
        return {cookie.name: cookie.value for cookie in self._session.cookies}

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass

    # -- request verbs -------------------------------------------------------

    def get(self, url: str, *, headers: Optional[Mapping[str, str]] = None,
            params: Optional[Mapping[str, Any]] = None,
            secret_bearing: bool = False, timeout: Optional[float] = None,
            allow_redirects: bool = True) -> HttpResult:
        return self._request("GET", url, headers=headers, params=params,
                             data=None, json_body=None,
                             secret_bearing=secret_bearing, timeout=timeout,
                             allow_redirects=allow_redirects)

    def post_form(self, url: str, data: Mapping[str, Any], *,
                  headers: Optional[Mapping[str, str]] = None,
                  secret_bearing: bool = False, timeout: Optional[float] = None,
                  allow_redirects: bool = False) -> HttpResult:
        return self._request("POST", url, headers=headers, params=None,
                             data=dict(data or {}), json_body=None,
                             secret_bearing=secret_bearing, timeout=timeout,
                             allow_redirects=allow_redirects)

    def post_json(self, url: str, payload: Any, *,
                  headers: Optional[Mapping[str, str]] = None,
                  secret_bearing: bool = False, timeout: Optional[float] = None,
                  allow_redirects: bool = True) -> HttpResult:
        merged = {"Content-Type": "application/json; charset=utf-8"}
        merged.update(dict(headers or {}))
        return self._request("POST", url, headers=merged, params=None,
                             data=None, json_body=payload,
                             secret_bearing=secret_bearing, timeout=timeout,
                             allow_redirects=allow_redirects)

    # -- the policy-checked hop walk ----------------------------------------

    def _request(self, method: str, url: str, *, headers, params, data,
                 json_body, secret_bearing: bool, timeout: Optional[float],
                 allow_redirects: bool) -> HttpResult:
        import requests

        budget = timeout if timeout is not None else self._policy.default_timeout
        current = str(url or "")
        hop_headers = dict(headers or {})
        credential_origin = _origin_of(current) if secret_bearing else None
        first = True
        for _hop in range(self._policy.max_redirects + 1):
            self._ctx.check_alive()
            try:
                self._policy.check(current, secret_bearing=secret_bearing)
            except PolicyRefused as exc:
                raise TransportError(
                    str(exc), code=getattr(exc, "code", "target_not_allowed"),
                    stage=STAGE_POLICY, detail=str(exc)) from exc
            try:
                response = self._session.request(
                    method, current, headers=hop_headers,
                    params=params if first else None,
                    data=data if first else None,
                    json=json_body if first else None,
                    timeout=self._ctx.io_timeout(budget),
                    allow_redirects=False, stream=True)
            except requests.exceptions.Timeout as exc:
                raise TransportError(
                    "the target did not respond in time", code="timeout",
                    stage=STAGE_TIMEOUT, detail=_safe_detail(exc)) from exc
            except requests.exceptions.SSLError as exc:
                raise TransportError(
                    "the TLS certificate could not be verified",
                    code="tls_failed", stage=STAGE_TLS,
                    detail=_safe_detail(exc)) from exc
            except requests.exceptions.RequestException as exc:
                raise TransportError(
                    "the target could not be reached",
                    code="network_unreachable", stage=STAGE_NETWORK,
                    detail=_safe_detail(exc)) from exc

            location = response.headers.get("Location", "")
            if allow_redirects and response.status_code in (301, 302, 303, 307, 308) \
                    and location:
                response.close()
                from urllib.parse import urljoin
                target = urljoin(current, location)
                try:
                    self._policy.check_redirect(
                        current, target, secret_bearing=secret_bearing)
                except PolicyRefused as exc:
                    raise TransportError(
                        str(exc),
                        code=getattr(exc, "code", "target_not_allowed"),
                        stage=STAGE_POLICY, detail=str(exc)) from exc
                if response.status_code in (301, 302, 303) \
                        and method not in ("GET", "HEAD"):
                    method = "GET"
                    data = None
                    json_body = None
                if credential_origin is not None \
                        and _origin_of(target) != credential_origin:
                    hop_headers = {k: v for k, v in hop_headers.items()
                                   if k.lower() != "authorization"}
                current = target
                first = False
                continue

            body = _read_bounded(response, self._policy.max_response_bytes,
                                 self._ctx)
            cookies = {c.name: c.value for c in self._session.cookies}
            return HttpResult(
                status_code=int(response.status_code),
                text=body.decode("utf-8", "replace"),
                url=str(getattr(response, "url", current) or current),
                headers={str(k).lower(): str(v)
                         for k, v in response.headers.items()},
                cookies=cookies,
                body=body)
        raise TransportError("too many redirects", code="too_many_redirects",
                             stage=STAGE_NETWORK)


def _read_bounded(response, limit: int, ctx) -> bytes:
    body = b""
    try:
        for chunk in response.iter_content(8192):
            ctx.check_alive()
            body += chunk
            if len(body) > limit:
                body = body[:limit]
                break
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    return body


def _safe_detail(exc: Exception) -> str:
    """A short, non-secret description of a transport failure.

    A raw ``requests`` exception can embed the full URL (and therefore a query
    string that carries an ``access_token``). Only the exception class and a
    tightly truncated message survive here, and query strings are stripped.
    """

    text = " ".join(str(exc).split())
    for marker in ("?",):
        if marker in text:
            text = text.split(marker, 1)[0]
    if len(text) > _MAX_DETAIL:
        text = text[:_MAX_DETAIL] + "…"
    return "%s: %s" % (type(exc).__name__, text) if text else type(exc).__name__


def open_transport(ctx: Any) -> PolicyTransport:
    """Build the transport for one attempt.

    The only seam tests need to replace: monkeypatching this function lets the
    whole login/action logic run against canned responses.
    """

    return PolicyTransport(ctx)
