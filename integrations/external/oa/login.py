# encoding:utf-8
"""Adaptive account/password login for 泛微 E-cology (E9) and E10.

This is a port of OneAgent's ``channel/web/handlers/oa_connection.py``
(``OaConnectionTestHandler._test_login`` and its helpers) with two changes:

* the HTTP calls go through :class:`~integrations.external.oa.transport.PolicyTransport`
  instead of a bare ``requests.Session``, so an admin-configured site URL is
  still subject to the deployment's SSRF/redirect/TLS policy;
* every diagnostic string is redacted before it is returned.

The adaptive behaviour is deliberately preserved rather than "cleaned up",
because it encodes what real deployments do:

1. warm up a login page and read the real ``<form action>`` (customised OA
   behind Apache often refuses a bare POST);
2. POST the standard ``VerifyLogin.jsp``; parse the E9 array form
   (``["1", …]``) *and* the integration ``{"code":"0"}`` form;
3. if the standard endpoints answer ``403/404/405``, try the documented
   candidates (``/Login/VerifyLogin.jsp``, ``loginCheck``, Passport
   ``checkLogin``) and the third-party ``/api/hrm/login/checkLogin``;
4. if those are refused too, fetch a CAS/unified-auth login page and submit its
   real form;
5. treat "password checked" and "session established" as different facts:
   a Passport ``checkLogin`` that returns success but sets no session is not a
   usable OA connection, so ``verify_oa_session`` checks the cookie jar and
   ``/api/`` before declaring success;
6. when everything fails, fetch the page's JS bundles to report candidate
   API paths so the operator can see what the site actually exposes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urljoin

from integrations.external.adapters.base import (
    STAGE_AUTH,
    STAGE_NETWORK,
    STAGE_POLICY,
    STAGE_PROTOCOL,
    STAGE_TIMEOUT,
    STAGE_TLS,
)
from integrations.external.oa.redact import Redactor
from integrations.external.oa.transport import HttpResult, TransportError

LOGIN_FORM_PATH = "/login/Login.jsp"
LOGIN_SUBMIT_PATHS = ("/login/VerifyLogin.jsp", "/Login/VerifyLogin.jsp")
LOGIN_DISCOVERY_PAGES = (LOGIN_FORM_PATH, "/Login.jsp", "/login.jsp", "/")
THIRD_PARTY_CHECK_LOGIN = "/api/hrm/login/checkLogin"
SESSION_COOKIE_NAMES = ("loginidweaver", "extloginid", "testid", "JSESSIONID")

#: HTTP statuses that mean "this endpoint is not the login endpoint here", as
#: opposed to "the credentials were refused".
_NOT_FOUND_STATUSES = (403, 404, 405)
_REDIRECT_STATUSES = (301, 302)


@dataclass
class LoginResult:
    """The outcome of one adaptive login attempt."""

    ok: bool
    message: str = ""
    #: Password verified *and* a usable OA session established. This is the
    #: distinction the spec insists on for Passport environments.
    session_valid: bool = False
    #: Precise failure stage (``network``/``tls``/``auth``/``protocol``/…).
    stage: str = ""
    code: str = ""
    #: Redacted diagnostic chain; safe to place in a stage detail.
    diagnostics: List[str] = field(default_factory=list)
    #: Non-secret discoveries (JS-bundle API paths, login-config hints).
    discovered: Dict[str, Any] = field(default_factory=dict)

    @property
    def interface_hint(self) -> str:
        """``e10`` when Passport/E10 markers were seen, else ``e9``."""

        if self.discovered.get("passport_seen"):
            return "e10"
        return "e9"


def normalize_base_url(base_url: str) -> str:
    """Accept a site root or a pasted login-page URL; return the site root."""

    value = str(base_url or "").strip().rstrip("/")
    lower = value.lower()
    if lower.endswith("/login/login.jsp"):
        value = value[: -len("/login/Login.jsp")]
    elif lower.endswith("/login.jsp"):
        value = value[: value.rindex("/")]
    return value.rstrip("/")


def _login_payload(username: str, password: str, langid: str) -> Dict[str, str]:
    return {
        "loginid": username,
        "userpassword": password,
        "islanguid": str(langid or "7"),
        "loginbeans": "[]",
    }


def parse_login_response(result: HttpResult) -> tuple:
    """Parse a login response; returns ``(ok, message, kind)``.

    Mirrors OneAgent's ``_parse_login_response`` including the E9 array form
    (first element ``"1"``) and the integration ``{"code":"0"}`` form.
    """

    text = (result.text or "").strip()
    if result.status_code in _REDIRECT_STATUSES:
        location = result.header("location")
        if "/login/login.jsp" not in location.lower():
            return True, "连接成功（已重定向）", "redirect"
        return False, "登录失败，重定向回登录页", "redirect"
    try:
        data = json.loads(text)
        if isinstance(data, list):
            if data and str(data[0]) == "1":
                return True, "登录成功", "array"
            return False, "登录失败（账号或密码被拒绝）", "array"
        if isinstance(data, dict) and "code" in data:
            code = str(data.get("code"))
            if code in ("0", "200"):
                return True, "登录成功", "code"
            return False, "登录失败（账号或密码被拒绝）", "code"
        if isinstance(data, dict):
            status = data.get("status")
            if status is True or str(status).lower() in ("true", "1"):
                return True, "登录成功", "status"
            return False, "登录失败（账号或密码被拒绝）", "status"
    except (ValueError, TypeError):
        pass
    if text in ("1", "true", "ok"):
        return True, "登录成功", "text"
    return False, "登录失败(HTTP %d)" % result.status_code, "text"


def verify_oa_session(transport, root: str, *,
                      io_timeout: float = 10.0) -> bool:
    """Confirm a real OA session exists, not just a password check.

    Passport's ``checkLogin`` validates the account without creating a session,
    so the cookie jar and ``/api/`` are checked independently. Cookie values are
    never logged.
    """

    cookies = transport.cookies()
    if any(name in cookies and cookies[name] for name in SESSION_COOKIE_NAMES):
        return True
    try:
        response = transport.get("%s/api/" % root, timeout=io_timeout,
                                 allow_redirects=False)
    except TransportError:
        return False
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except (ValueError, TypeError):
        return False
    if isinstance(body, Mapping):
        status = body.get("status")
        if status is True or str(status).lower() in ("true", "1"):
            return True
        if body.get("errorCode") == "002":
            return False
    lowered = response.text.lower()
    return ("sessionkey" in response.text or "loginid" in response.text
            or "logintype" in lowered)


def extract_login_form(page_text: str, page_url: str) -> Optional[Dict[str, Any]]:
    """Extract one ``<form>`` structure (action / method / fields)."""

    match = re.search(r"<form\b[^>]*>", page_text or "", re.I)
    if not match:
        return None
    form_tag = match.group(0)
    action_m = re.search(r"action\s*=\s*[\"']([^\"']*)[\"']", form_tag, re.I)
    method_m = re.search(r"method\s*=\s*[\"']([^\"']*)[\"']", form_tag, re.I)
    action = action_m.group(1).strip() if action_m else ""
    method = (method_m.group(1).lower()
              if method_m and method_m.group(1) else "post") or "post"
    action = urljoin(page_url, action) if action else page_url
    end_idx = match.end()
    form_end = page_text.find("</form>", end_idx)
    if form_end < 0:
        form_end = len(page_text)
    fields: List[Dict[str, Any]] = []
    for tag in re.findall(r"<input\b[^>]*>", page_text[end_idx:form_end], re.I):
        name_m = re.search(r"name\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        value_m = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        type_m = re.search(r"type\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        fields.append({
            "name": name_m.group(1) if name_m else None,
            "value": value_m.group(1) if value_m else "",
            "type": (type_m.group(1).lower() if type_m else "text"),
        })
    return {"action": action, "method": method, "fields": fields,
            "page_url": page_url}


_USER_FIELD_KEYWORDS = (
    "loginid", "loginname", "username", "userid", "account",
    "j_username", "userloginid", "login",
)


def build_form_payload(form: Mapping[str, Any], username: str,
                       password: str) -> tuple:
    """Fill account/password into a discovered form, keeping hidden values."""

    data: Dict[str, str] = {}
    username_field = None
    has_password_field = False
    for field in form.get("fields") or ():
        name = field.get("name")
        if not name:
            continue
        ftype = str(field.get("type") or "text").lower()
        lowered = str(name).lower()
        if ftype == "password":
            has_password_field = True
            data[name] = password
        elif ftype == "hidden":
            data[name] = str(field.get("value") or "")
        elif ftype in ("text", "tel", "email"):
            if username_field is None and lowered in _USER_FIELD_KEYWORDS:
                username_field = name
                data[name] = username
            else:
                data[name] = str(field.get("value") or "")
        else:
            data[name] = str(field.get("value") or "")
    if username_field is None:
        for field in form.get("fields") or ():
            name = field.get("name")
            if name and str(field.get("type") or "").lower() == "text" \
                    and name not in data:
                data[name] = username
                break
    return data, has_password_field


def form_adaptive_login(transport, root: str, username: str, password: str,
                        diag: List[str], *, io_timeout: float = 15.0,
                        redactor: Optional[Redactor] = None
                        ) -> Optional[HttpResult]:
    """Fetch a CAS/unified-auth login page and submit its real form."""

    redact = redactor or Redactor([password])
    service = "%s/" % root
    pages = ((("%s/login" % root), {"service": service}),
             ("%s%s" % (root, LOGIN_FORM_PATH), None))
    for page_url, params in pages:
        try:
            page = transport.get(page_url, params=params, timeout=io_timeout,
                                 allow_redirects=True)
        except TransportError as exc:
            diag.append(redact("GET %s -> %s" % (page_url, exc.detail)))
            continue
        diag.append(redact("GET %s -> HTTP %d" % (page_url, page.status_code)))
        form = extract_login_form(page.text or "", page.url)
        if not form:
            continue
        data, has_password = build_form_payload(form, username, password)
        if not has_password:
            diag.append(redact("  表单无密码框，action=%s" % form["action"]))
            continue
        headers = {"Referer": page.url, "Origin": root}
        try:
            if form["method"] == "get":
                resp = transport.get(form["action"], params=data,
                                     headers=headers, timeout=io_timeout,
                                     allow_redirects=False)
            else:
                resp = transport.post_form(
                    form["action"], data,
                    headers={"Content-Type": "application/x-www-form-urlencoded; "
                                            "charset=utf-8", **headers},
                    timeout=io_timeout, allow_redirects=False)
        except TransportError as exc:
            diag.append(redact("POST %s -> %s" % (form["action"], exc.detail)))
            continue
        diag.append(redact("POST %s -> HTTP %d" % (form["action"],
                                                   resp.status_code)))
        if resp.status_code not in _NOT_FOUND_STATUSES:
            return resp
    return None


def analyze_login_js(transport, page_url: str, page_text: str,
                     diag: List[str], *, io_timeout: float = 15.0,
                     redactor: Optional[Redactor] = None) -> List[str]:
    """Fetch login-page JS bundles and extract login-related API paths."""

    redact = redactor or Redactor()
    scripts = re.findall(
        r"<script[^>]+src\s*=\s*[\"']([^\"']+)[\"']", page_text or "", re.I)
    pattern = re.compile(
        r"[\"'](/papi/passport/login/[^\"']+|/api/login/[^\"']+|"
        r"/papi/[a-zA-Z0-9/_.-]*login[a-zA-Z0-9/_.-]*)[\"']", re.I)
    hits = set()
    for src in scripts[:8]:
        if src.startswith("data:"):
            continue
        try:
            js = transport.get(urljoin(page_url, src), timeout=io_timeout,
                               allow_redirects=True).text
        except TransportError:
            continue
        for match in pattern.finditer(js or ""):
            hit = match.group(1).strip()
            if len(hit) > 4 and not hit.endswith(
                    (".js", ".css", ".png", ".jpg", ".gif", ".woff")):
                hits.add(hit)
    if hits:
        diag.append("  JS 中发现登录相关 API: %s" % ", ".join(sorted(hits)[:50]))
    return sorted(hits)[:50]


def probe_login_config(transport, root: str, diag: List[str], *,
                       io_timeout: float = 10.0,
                       redactor: Optional[Redactor] = None) -> Dict[str, Any]:
    """Probe E9 Passport login-config endpoints for diagnostics only.

    Returns non-secret hints (which endpoint answered and whether it looked like
    a Passport/E10 deployment). The raw body is never returned: it can contain a
    public key or a token field.
    """

    candidates = (
        "/papi/passport/login/getLoginTypes",
        "/papi/passport/login/getLoginConfig",
        "/papi/passport/login/getLoginForm",
        "/papi/passport/login/config",
        "/papi/passport/login/preLogin",
        "/api/hrm/login/getLoginForm",
    )
    markers = ("tenantKey", "publicKey", "rsaKey", "sm2", "cipherPublicKey",
               "token", "customPageConfigId", "loginType", "encryptType")
    found: List[str] = []
    passport_seen = False
    for path in candidates:
        try:
            response = transport.get("%s%s" % (root, path), timeout=io_timeout,
                                     allow_redirects=False)
        except TransportError:
            continue
        if response.status_code != 200:
            continue
        text = response.text[:4000]
        if any(marker in text for marker in markers):
            found.append(path)
            passport_seen = passport_seen or path.startswith("/papi/passport/")
    if found:
        diag.append("  疑似登录配置接口: %s" % ", ".join(found))
    return {"passport_seen": passport_seen, "login_config_paths": found}


def perform_login(transport, *, base_url: str, username: str, password: str,
                  langid: str = "7", io_timeout: float = 15.0,
                  diagnostics: bool = True,
                  redact_values: Sequence[str] = ()) -> LoginResult:
    """Attempt an adaptive account/password login and return a classified result."""

    redactor = Redactor([password, *redact_values])
    root = normalize_base_url(base_url)
    if not root or not username or not password:
        return LoginResult(
            ok=False, message="站点地址、用户名和密码均不能为空",
            stage=STAGE_PROTOCOL, code="config_incomplete")
    diag: List[str] = []
    messages: List[str] = []
    handled = False
    password_verified = False

    def _failure(stage: str, code: str, message: str,
                 discovered: Optional[Dict[str, Any]] = None) -> LoginResult:
        return LoginResult(
            ok=False, message=redactor(message), stage=stage, code=code,
            diagnostics=[redactor(line) for line in diag],
            discovered=dict(discovered or {}))

    def _transport_failure(exc: TransportError) -> LoginResult:
        return _failure(exc.stage, exc.code, exc.detail)

    # 1) discover the real <form action> from a login page ------------------
    submit_url = None
    page_seen = None
    for page_path in LOGIN_DISCOVERY_PAGES:
        try:
            page = transport.get("%s%s" % (root, page_path), timeout=io_timeout,
                                 allow_redirects=True)
        except TransportError as exc:
            diag.append(redactor("GET %s -> %s" % (page_path, exc.detail)))
            if exc.stage in (STAGE_NETWORK, STAGE_TLS, STAGE_POLICY,
                             STAGE_TIMEOUT):
                return _transport_failure(exc)
            continue
        diag.append(redactor("GET %s -> HTTP %d" % (page_path,
                                                    page.status_code)))
        if page_seen is None:
            page_seen = page
        match = re.search(r"<form[^>]*action\s*=\s*[\"']([^\"']+)[\"']",
                          page.text or "", re.I)
        if match:
            action = match.group(1).strip()
            if action and not action.lower().lstrip().startswith(
                    ("javascript", "#")):
                submit_url = urljoin(page.url, action)
                diag.append(redactor("  登录页表单 action -> %s" % submit_url))
                break

    # 2) candidate submit endpoints ----------------------------------------
    candidates: List[str] = []
    if submit_url:
        candidates.append(submit_url)
    candidates.extend("%s%s" % (root, path) for path in LOGIN_SUBMIT_PATHS)
    candidates.append("%s/api/login/VerifyLogin/loginCheck" % root)
    candidates.append("%s/papi/passport/login/checkLogin" % root)
    payload = _login_payload(username, password, langid)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Referer": "%s%s" % (root, LOGIN_FORM_PATH),
        "Origin": root,
    }
    for url in candidates:
        try:
            response = transport.post_form(url, payload, headers=headers,
                                           timeout=io_timeout,
                                           allow_redirects=False)
        except TransportError as exc:
            # A transport failure on the *first* candidate is a genuine
            # connectivity failure; later ones are recorded and skipped so a
            # single broken candidate cannot end the adaptive flow.
            diag.append(redactor("POST %s -> %s" % (url, exc.detail)))
            if not candidates or url == candidates[0]:
                return _transport_failure(exc)
            continue
        diag.append(redactor("POST %s -> HTTP %d" % (url, response.status_code)))
        if response.status_code in _REDIRECT_STATUSES:
            handled = True
            ok, message, _kind = parse_login_response(response)
            if ok and verify_oa_session(transport, root, io_timeout=io_timeout):
                return LoginResult(ok=True, message=message, session_valid=True,
                                   diagnostics=[redactor(x) for x in diag],
                                   discovered={"interface": "e9"})
            messages.append(message)
            continue
        if response.status_code not in _NOT_FOUND_STATUSES:
            handled = True
            ok, message, _kind = parse_login_response(response)
            if ok:
                if verify_oa_session(transport, root, io_timeout=io_timeout):
                    return LoginResult(
                        ok=True, message=message, session_valid=True,
                        diagnostics=[redactor(x) for x in diag],
                        discovered={"interface": "e9"})
                password_verified = True
                diag.append(redactor(
                    "  账号密码校验通过，但未建立有效会话"))
                break
            messages.append(message)
            diag.append(redactor("  %s" % message))

    # 2.5) third-party integration login (plaintext account/password) -------
    check_url = "%s%s" % (root, THIRD_PARTY_CHECK_LOGIN)
    try:
        response = transport.post_form(
            check_url, {"loginid": username, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded; "
                                    "charset=utf-8",
                     "Referer": "%s/" % root, "Origin": root},
            timeout=io_timeout, allow_redirects=False)
    except TransportError as exc:
        diag.append(redactor("POST %s -> %s" % (check_url, exc.detail)))
    else:
        diag.append(redactor("POST %s -> HTTP %d" % (check_url,
                                                     response.status_code)))
        if response.status_code not in _NOT_FOUND_STATUSES:
            handled = True
            ok, message, _kind = parse_login_response(response)
            if ok and verify_oa_session(transport, root, io_timeout=io_timeout):
                return LoginResult(ok=True, message=message, session_valid=True,
                                   diagnostics=[redactor(x) for x in diag],
                                   discovered={"interface": "e9"})
            if ok:
                password_verified = True
            messages.append(message)

    # 3) CAS / unified-auth form fallback ---------------------------------
    form_response = form_adaptive_login(
        transport, root, username, password, diag, io_timeout=io_timeout,
        redactor=redactor)
    if form_response is not None:
        handled = True
        ok, message, _kind = parse_login_response(form_response)
        if ok and verify_oa_session(transport, root, io_timeout=io_timeout):
            return LoginResult(ok=True, message=message, session_valid=True,
                               diagnostics=[redactor(x) for x in diag],
                               discovered={"interface": "e9"})
        if ok:
            password_verified = True
        messages.append(message)
        diag.append(redactor("  %s" % message))

    # 4) diagnostics: JS bundles + Passport login-config endpoints ---------
    discovered: Dict[str, Any] = {}
    if diagnostics:
        if page_seen is not None:
            paths = analyze_login_js(transport, page_seen.url, page_seen.text,
                                     diag, io_timeout=io_timeout,
                                     redactor=redactor)
            if paths:
                discovered["api_paths"] = paths
        discovered.update(probe_login_config(
            transport, root, diag, io_timeout=io_timeout, redactor=redactor))

    detail = messages[-1] if messages else "所有候选登录端点均被拒绝"
    if not handled:
        return _failure(
            STAGE_PROTOCOL, "login_endpoint_not_recognised",
            "登录失败：登录页结构无法识别，所有候选端点均被拒绝（%s）" % detail,
            discovered)
    if password_verified:
        # The account/password was accepted but no usable session exists. This
        # is the Passport/E10 shape: the credential is right, the interface is
        # not the one this connection is configured for.
        return _failure(
            STAGE_AUTH, "session_not_established",
            "账号密码校验通过，但未建立有效 OA 会话（该站点可能是 E10/Passport，"
            "需配置 OpenAPI 凭据后使用）",
            discovered)
    return _failure(STAGE_AUTH, "login_rejected",
                    "登录失败：账号或密码被拒绝（%s）" % detail, discovered)
