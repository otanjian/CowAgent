# encoding:utf-8
"""OA action orchestration and E9/E10 interface selection.

The client is constructed from one :class:`ExecutionContext` and one
:class:`~integrations.external.oa.transport.PolicyTransport`, so a session is
never shared across tenants, connections or versions. It exposes the exact
action names the risk catalogue declares and nothing else; the adapter's
``invoke`` is the only caller.

Two behavioural rules from the spec are enforced here rather than left to the
caller:

* **unsupported interface is refused, not approximated** — a site whose
  discovered interface cannot serve an action (E10 has no forward/circulate/
  add-sign surface) gets ``unsupported_interface``;
* **target ambiguity is refused, never guessed** — a forward/ circulate whose
  recipient keyword matches zero people fails ``target_not_found`` and one that
  matches several fails ``target_ambiguous``, both returning the candidates for
  the caller to choose from.

A write whose response is lost raises :class:`OaActionError` with
``unknown=True`` (the adapter turns that into ``invoke_unknown``): the remote
may already have accepted it, so the caller must re-read, never resend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from integrations.external.adapters.base import (
    AdapterError,
    Cancelled,
    DeadlineExceeded,
    STAGE_AUTH,
    STAGE_CONFIG,
    STAGE_INTERNAL,
    STAGE_POLICY,
    STAGE_PROTOCOL,
    STAGE_TIMEOUT,
)
from integrations.external.oa import actions as actions_module
from integrations.external.oa import transport as transport_module
from integrations.external.oa.login import LoginResult, perform_login
from integrations.external.oa.redact import Redactor
from integrations.external.oa.transport import HttpResult, TransportError

#: E9 right-menu aliases, keyed by the *operation* the client performs
#: (``submit``/``reject``/``forward``/``turn``/``circulate``/``addsign``), which
#: is what ``requestOperation`` receives.
_ACTION_MENU_ALIASES: Dict[str, Sequence[Sequence[str]]] = {
    "submit": (("APPROVE", "BTN_SUBMIT", "同意"),),
    "reject": (("REJECT", "BTN_REJECTNAME", "驳回"),),
    "forward": (("FORWARD", "BTN_FORWARD", "转发"),),
    "turn": (("TURN_TO", "BTN_TURNTODO", "转办"),),
    "circulate": (("CHUANYUE", "BTN_CHUANYUE", "传阅"),),
    "addsign": (("REMARKADVICE", "BTN_REMARKADVICE", "加签"),),
}

#: ``status`` values that mean the request can no longer be acted on. A remote
#: object in one of these states is refused with ``not_actionable``.
_TERMINAL_STATUSES = frozenset({"3", "9", "finished", "rejected", "archived",
                                "recovered", "withdrawn"})


class OaActionError(AdapterError):
    """A refusal or failure classified for the adapter's result builders.

    An :class:`~integrations.external.adapters.base.AdapterError` subclass so a
    refusal raised deep in the client is caught by the adapter's normal
    ``except AdapterError`` handling and mapped onto ``invoke_failed`` rather
    than escaping as an unclassified crash.
    """

    def __init__(self, message: str, *, code: str = "action_failed",
                 stage: str = STAGE_INTERNAL, unknown: bool = False,
                 data: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, code=code, stage=stage)
        self.unknown = bool(unknown)
        self.data = dict(data or {})


@dataclass
class SiteSession:
    """The mutable state of one remote OA session (never reused across calls)."""

    transport: Any
    root: str
    username: str
    logged_in: bool = False
    userid: str = ""
    interface: str = "e9"
    access_token: str = ""
    passport_seen: bool = False


class OaClient:
    """One attempted call's OA client."""

    def __init__(self, ctx: Any, *, transport: Any = None) -> None:
        self._ctx = ctx
        config = dict(getattr(ctx, "config", {}) or {})
        self._config = config
        self._root = self._normalize(str(config.get("base_url") or ""))
        self._username = str(config.get("username") or "")
        self._app_key = str(config.get("app_key") or "")
        self._corp_id = str(config.get("corp_id") or "")
        self._tenant_key = str(config.get("tenant_key") or "")
        self._page_config_id = str(config.get("custom_page_config_id") or "")
        self._password = ""
        self._app_secret = ""
        self._interface: Optional[str] = None
        self._openapi_ok = False
        self._interface_note = ""
        self._transport = transport if transport is not None \
            else transport_module.open_transport(ctx)
        self._session = SiteSession(transport=self._transport, root=self._root,
                                    username=self._username)
        self._redactor = Redactor()

    # -- configuration helpers ----------------------------------------------

    @staticmethod
    def _normalize(base_url: str) -> str:
        from integrations.external.oa.login import normalize_base_url
        return normalize_base_url(base_url)

    def _password_value(self) -> str:
        if not self._password:
            self._password = self._ctx.optional_secret("password")
        return self._password

    def _app_secret_value(self) -> str:
        if not self._app_secret:
            self._app_secret = self._ctx.optional_secret("app_secret")
        return self._app_secret

    @property
    def root(self) -> str:
        return self._root

    @property
    def session(self) -> SiteSession:
        return self._session

    def redactor(self) -> Redactor:
        self._redactor.add(self._password_value(), self._app_secret_value(),
                           *self._session.transport.cookies().values())
        return self._redactor

    def openapi_configured(self) -> bool:
        return bool(self._app_key) and bool(self._corp_id) \
            and bool(self._ctx.has_secret("app_secret"))

    # -- interface selection -------------------------------------------------

    def select_interface(self) -> Dict[str, Any]:
        """Discover which interface this site actually serves.

        Preference order is documented rather than guessed: OpenAPI credentials
        that actually yield a token mean E10; anything else is the account-based
        E9 surface. The choice is reported so the caller sees which one ran.
        """

        if self._interface is not None:
            return {"interface": self._interface, "openapi": self._openapi_ok,
                    "note": self._interface_note}
        if not self.openapi_configured():
            self._interface, self._openapi_ok = "e9", False
            self._interface_note = "openapi_not_configured"
            return {"interface": "e9", "openapi": False,
                    "note": self._interface_note}
        try:
            self._openapi_token()
            self._interface, self._openapi_ok = "e10", True
            self._interface_note = "openapi_ready"
        except OaActionError as exc:
            self._interface, self._openapi_ok = "e9", False
            self._interface_note = exc.code
        return {"interface": self._interface, "openapi": self._openapi_ok,
                "note": self._interface_note}

    def _require_interface(self, action: str) -> str:
        allowed = actions_module.INTERFACE_ACTIONS.get(action, frozenset())
        selected = self.select_interface()
        interface = str(selected["interface"])
        if not self._openapi_ok and interface == "e10":
            interface = "e9"
        if interface not in allowed:
            raise OaActionError(
                "该站点发现的接口不支持 %s（发现接口=%s，支持=%s），已明确关闭而不以其他方式代替"
                % (action, interface, ",".join(sorted(allowed))),
                code="unsupported_interface", stage=STAGE_POLICY,
                data={"interface": interface,
                      "supported_interfaces": sorted(allowed)})
        return interface

    # -- login / openapi -----------------------------------------------------

    def login(self, *, diagnostics: bool = False) -> LoginResult:
        result = perform_login(
            self._transport, base_url=self._root, username=self._username,
            password=self._password_value(),
            io_timeout=self._test_timeout(), diagnostics=diagnostics,
            redact_values=[self._app_secret_value()])
        if result.ok:
            self._session.logged_in = True
            self._session.interface = result.interface_hint
            self._session.passport_seen = bool(
                result.discovered.get("passport_seen"))
        return result

    def _test_timeout(self) -> float:
        limits = getattr(self._ctx, "limits", None)
        if isinstance(limits, Mapping):
            try:
                return float(limits.get("test_timeout") or 15.0)
            except (TypeError, ValueError):
                return 15.0
        return 15.0

    def _ensure_e9_session(self) -> None:
        if self._session.logged_in:
            return
        result = self.login()
        if not result.ok or not result.session_valid:
            raise OaActionError(result.message or "登录失败",
                                code=result.code or "login_failed",
                                stage=result.stage or STAGE_AUTH)

    def _openapi_token(self) -> str:
        if self._session.access_token:
            return self._session.access_token
        app_secret = self._app_secret_value()
        if not (self._app_key and app_secret and self._corp_id):
            raise OaActionError(
                "OpenAPI 应用凭据未配置（app_key/app_secret/corp_id）",
                code="openapi_not_configured", stage=STAGE_CONFIG)
        try:
            authorize = self._transport.get(
                "%s%s" % (self._root, actions_module.E10_AUTHORIZE),
                params={"app_key": self._app_key, "corpid": self._corp_id,
                        "response_type": "code", "state": "rsmagent"},
                secret_bearing=True,
                timeout=self._io_timeout(20.0),
                allow_redirects=False)
            body = self._json(authorize)
            # ``code`` on the authorize response is the OAuth grant code, not an
            # error, so only ``errcode`` is treated as one.
            errcode = str(body.get("errcode") or "")
            if errcode and errcode not in actions_module.OPENAPI_OK_ERRCODES:
                raise OaActionError("OpenAPI authorize 失败",
                                    code="openapi_authorize_failed",
                                    stage=STAGE_AUTH)
            code = str(body.get("code") or body.get("authCode") or "")
            if not code:
                raise OaActionError("OpenAPI authorize 未返回 code",
                                    code="openapi_authorize_failed",
                                    stage=STAGE_PROTOCOL)
            token_response = self._transport.post_json(
                "%s%s" % (self._root, actions_module.E10_TOKEN),
                {"app_key": self._app_key, "app_secret": app_secret,
                 "grant_type": "authorization_code", "code": code},
                secret_bearing=True, timeout=self._io_timeout(20.0))
            token_body = self._json(token_response)
            token = str(token_body.get("accessToken")
                        or token_body.get("acessToken") or "")
            if not token:
                errcode = str(token_body.get("errcode")
                              or token_body.get("code") or "")
                raise OaActionError("OpenAPI 换取 access_token 失败",
                                    code="openapi_token_failed",
                                    stage=STAGE_AUTH if errcode else STAGE_PROTOCOL)
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code, stage=exc.stage) from exc
        self._session.access_token = token
        return token

    def ensure_openapi(self) -> str:
        """Public entry point for the probe's OpenAPI readiness check."""

        return self._openapi_token()

    def _e10_userid(self) -> str:
        if self._session.userid:
            return self._session.userid
        try:
            response = self._transport.get(
                "%s/api/baseserver/layout/baseTeams" % self._root,
                headers={"Accept": "application/json", "langType": "zh_CN"},
                timeout=self._io_timeout(20.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc
        body = self._json(response)
        current = (body.get("data") or {}).get("currentUser") or {}
        userid = str(current.get("id") or "")
        if not userid:
            raise OaActionError("未能从 baseTeams 接口获取 userid",
                                code="userid_unavailable",
                                stage=STAGE_PROTOCOL)
        self._session.userid = userid
        return userid

    # -- action dispatch -----------------------------------------------------

    def invoke(self, action: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        handlers = {
            "todos.list": self._todos_list,
            "request.read": self._request_read,
            "request.flowlog": self._request_flowlog,
            "request.related": self._request_related,
            "request.attachments": self._request_attachments,
            "cc.mark_read": self._cc_mark_read,
            "request.create": self._request_create,
            "request.submit": self._request_submit,
            "request.reject": self._request_reject,
            "request.forward": self._request_forward,
            "request.circulate": self._request_circulate,
            "request.addsign": self._request_addsign,
        }
        handler = handlers.get(str(action or ""))
        if handler is None:
            raise OaActionError("unsupported action %r" % action,
                                code="unsupported_action", stage=STAGE_CONFIG)
        return handler(dict(params or {}))

    # -- reads ---------------------------------------------------------------

    def _todos_list(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        view = str(params.get("view") or actions_module.DEFAULT_VIEW).lower()
        if view not in actions_module.VIEW_TABID:
            raise OaActionError("unknown list view %r" % view,
                                code="invalid_view", stage=STAGE_CONFIG)
        page = self._positive_int(params.get("page"), 1)
        page_size = min(200, self._positive_int(params.get("page_size"), 20))
        interface = self._require_interface("todos.list")
        if interface == "e10":
            # The OpenAPI surface implemented here exposes the todo and done
            # lists. 我发起/抄送 have no confirmed OpenAPI path, so they are
            # refused explicitly rather than probed against E9 endpoints that an
            # E10 site disables (confirming the matrix is task 7.6).
            if self._openapi_ok and view in ("doing", "done"):
                return self._openapi_list(view, page, page_size)
            raise OaActionError(
                "OpenAPI(E10) 接口不支持 %s 列表视图，已明确关闭" % view,
                code="unsupported_interface", stage=STAGE_POLICY,
                data={"interface": "e10", "supported_views": ["doing", "done"]})
        return self._e9_list(view, page, page_size,
                             workflow_id=str(params.get("workflow_id") or ""),
                             request_name=str(params.get("request_name") or ""))

    def _e9_list(self, view: str, page: int, page_size: int, *,
                 workflow_id: str = "", request_name: str = "") -> Dict[str, Any]:
        self._ensure_e9_session()
        search = actions_module.e9_search_params(
            view, workflow_id=workflow_id, request_name=request_name)
        headers = self._e9_headers()
        key_response = self._post_form(
            actions_module.E9_SPLIT_PAGE_KEY,
            {**search, "actiontype": "splitPageKey"}, headers)
        key_body = self._json(key_response)
        session_key = actions_module.resolve_session_key(key_body)
        if not session_key:
            raise OaActionError(
                "列表接口未返回会话键（该站点可能是 E10，需配置 OpenAPI 凭据）",
                code="e9_list_unsupported", stage=STAGE_PROTOCOL)
        table_response = self._post_form(
            actions_module.E9_TABLE_DATAS,
            {"dataKey": session_key, "current": str(page), "sortParams": "[]",
             "pageSize": str(page_size)}, headers)
        body = self._json(table_response)
        items = actions_module.normalize_e9_items(body.get("datas"))
        info = actions_module.page_info(body, page_size)
        return {
            "interface": "e9", "view": view, "page": page,
            "page_size": info["page_size"], "total": info["total"],
            "items": items, "has_more": len(items) >= info["page_size"],
        }

    def _openapi_list(self, view: str, page: int,
                      page_size: int) -> Dict[str, Any]:
        token = self._openapi_token()
        userid = self._e10_userid()
        path = actions_module.E10_TODO if view == "doing" \
            else actions_module.E10_DONE
        try:
            response = self._transport.get(
                "%s%s" % (self._root, path),
                params={"access_token": token, "userid": userid,
                        "pageNo": page, "pageSize": page_size},
                secret_bearing=True, timeout=self._io_timeout(30.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc
        body = self._json(response)
        ok, errcode, message = actions_module.parse_openapi_message(body)
        if not ok:
            raise OaActionError(message or "OpenAPI 列表查询失败",
                                code="openapi_error", stage=STAGE_PROTOCOL,
                                data={"errcode": errcode})
        items = [actions_module.normalize_openapi_item(item)
                 for item in body.get("requests") or []]
        return {
            "interface": "e10", "view": view, "page": page,
            "page_size": page_size, "total": body.get("count"),
            "items": [item for item in items if item],
            "has_more": bool(body.get("nextPage")),
        }

    def _request_read(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        request_id = self._request_id(params, required=True)
        interface = self._require_interface("request.read")
        if interface == "e10" and self._openapi_ok:
            return self._openapi_request_info(request_id)
        return self._e9_request_read(request_id)

    def _openapi_request_info(self, request_id: str) -> Dict[str, Any]:
        token = self._openapi_token()
        userid = self._e10_userid()
        try:
            response = self._transport.get(
                "%s%s" % (self._root, actions_module.E10_REQUEST_INFO),
                params={"access_token": token, "userid": userid,
                        "id": request_id},
                secret_bearing=True, timeout=self._io_timeout(30.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc
        body = self._json(response)
        ok, errcode, message = actions_module.parse_openapi_message(body)
        if not ok:
            raise OaActionError(message or "流程详情查询失败",
                                code="openapi_error", stage=STAGE_PROTOCOL,
                                data={"errcode": errcode})
        return {"interface": "e10", "request_id": request_id,
                "detail": body.get("data") or body}

    def _e9_request_read(self, request_id: str) -> Dict[str, Any]:
        self._ensure_e9_session()
        body = self._load_form(request_id)
        return {"interface": "e9", "request_id": request_id,
                "form": body,
                "params": actions_module.common_api_params(body)}

    # -- reads: 审批记录 / 关联流程 / 附件 -------------------------------------

    #: How many related applications one call will follow. A form can reference
    #: many; each fetched detail is a full remote read, so the bound is stated
    #: and the remainder is *reported* rather than silently dropped.
    MAX_RELATED_DETAILS = 5
    #: How many attachments one call may fetch. The download is a file written
    #: into the caller's workspace, so it stays a deliberately small number.
    MAX_ATTACHMENT_FETCH = 3

    def _request_flowlog(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """审批流转记录, paged to exhaustion and returned oldest-first.

        The paging key is the remote's own ``maxrequestlogid`` — the log table's
        cursor — so following it is the only way to get rows past the first page
        without guessing offsets. ``MAX_LOG_PAGES`` bounds the walk: a site whose
        response always claims more rows must terminate, and reporting
        ``hasMore`` is more honest than looping until the deadline.
        """
        request_id = self._request_id(params, required=True)
        self._require_interface("request.flowlog")
        order = actions_module.coerce_log_order(params.get("order"))
        self._ensure_e9_session()
        form = self._load_form(request_id)
        form_params = form.get("params") if isinstance(form, Mapping) else {}
        query = actions_module.build_request_log_params(
            form_params if isinstance(form_params, Mapping) else {},
            request_id=request_id)
        base = self._post_e9_json(
            actions_module.E9_REQUEST_LOG_BASE, query, request_id=request_id)
        for key in actions_module.LOG_BASE_EXTRA_KEYS:
            if isinstance(base, Mapping) and base.get(key) not in (None, ""):
                query[key] = str(base[key])
        query["isFromWfForm"] = "1"

        rows: List[Any] = []
        pages = 0
        last: Any = {}
        while pages < actions_module.MAX_LOG_PAGES:
            pages += 1
            last = self._post_e9_json(
                actions_module.E9_REQUEST_LOG_LIST, query, request_id=request_id)
            batch = last.get("loglist") if isinstance(last, Mapping) else None
            if not isinstance(batch, (list, tuple)) or not batch:
                break
            rows.extend(batch)
            total = self._int(last.get("totalCount"))
            next_key = str((last or {}).get("maxrequestlogid") or "") \
                if isinstance(last, Mapping) else ""
            if not next_key or (total is not None and len(rows) >= total):
                break
            query["maxrequestlogid"] = next_key
            query["pgnumber"] = str(self._positive_int(query.get("pgnumber"), 1) + 1)

        items = actions_module.sort_flow_log_items(
            actions_module.normalize_flow_log_items(rows), order=order)
        view = actions_module.flow_log_view(last, order=order,
                                            item_count=len(items))
        view["pages"] = pages
        return {"interface": "e9", "request_id": request_id,
                "flow_log": items, **view}

    def _request_related(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """关联事前申请 etc. — discovered from the form, then read in scope.

        Discovery is a scan of the *visible* browser fields; a field the remote
        left out of the form is not a link this session may follow. Each
        followed detail goes through the same session and the same
        ``loadForm`` call as any other read, so the remote's own permission
        decides what comes back — and a refusal for one target is recorded on
        that item instead of failing the whole list, because "this related
        request is not yours to read" is an answer, not an error.
        """
        request_id = self._request_id(params, required=True)
        self._require_interface("request.related")
        self._ensure_e9_session()
        form = self._load_form(request_id)
        table_info = form.get("tableInfo") if isinstance(form, Mapping) else {}
        detail: Any = {}
        marks = actions_module.discover_detail_marks(table_info)
        if marks and params.get("include_detail", True):
            form_params = form.get("params") if isinstance(form, Mapping) else {}
            detail = self._load_detail_data(
                request_id, form_params if isinstance(form_params, Mapping) else {},
                marks)
        refs = actions_module.discover_related_workflow_refs(form, detail)

        with_detail = bool(params.get("read_detail"))
        limit = min(self.MAX_RELATED_DETAILS,
                    max(1, self._positive_int(params.get("max_items"),
                                              self.MAX_RELATED_DETAILS)))
        items: List[Dict[str, Any]] = []
        for index, ref in enumerate(refs):
            entry = dict(ref)
            entry["withinLimit"] = index < limit
            entry["detail"] = None
            entry["detailRead"] = None
            if index < limit and with_detail:
                try:
                    entry["detail"] = actions_module.common_api_params(
                        self._load_form(ref["linkedRequestId"]))
                    entry["detailRead"] = "read"
                except OaActionError as exc:
                    # The remote refused this one target. Recorded per item so
                    # the caller sees which links are reachable, not a blanket
                    # failure that hides the ones that worked.
                    entry["detailRead"] = "refused"
                    entry["detailReason"] = str(exc) or exc.code
            items.append(entry)
        return {
            "interface": "e9", "request_id": request_id,
            "related": items, "count": len(items),
            "detailRequested": with_detail, "detailLimit": limit,
            "truncated": len(items) > limit,
        }

    def _request_attachments(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """Attachment metadata, plus a bounded fetch of the named ones.

        Only a link the connection's *own* site serves is fetchable
        (:func:`actions_module.same_origin_path`), so a form field cannot turn
        this into a fetcher for another host. A fetch writes into the caller's
        workspace through the same guard the mail adapter uses, which refuses a
        path outside the configured directories rather than sanitising it.
        """
        request_id = self._request_id(params, required=True)
        self._require_interface("request.attachments")
        self._ensure_e9_session()
        form = self._load_form(request_id)
        table_info = form.get("tableInfo") if isinstance(form, Mapping) else {}
        items = actions_module.collect_attachments(
            form.get("maindata") if isinstance(form, Mapping) else {},
            table_info)
        for item in items:
            path = item.get("downloadPath") or item.get("viewPath")
            item["fetchPath"] = actions_module.same_origin_path(path, self._root)
            item["fetchable"] = bool(item["fetchPath"])
            item["originRefused"] = bool(path) and not item["fetchPath"]
            item["downloadPath"] = str(path or "")

        selected = self._selected_attachments(items, params)
        saved: List[Dict[str, Any]] = []
        if selected:
            for item in selected:
                saved.append(self._fetch_attachment(item))
        return {
            "interface": "e9", "request_id": request_id,
            "attachments": items, "count": len(items),
            "requested": [item["index"] for item in selected],
            "saved": saved,
        }

    def _selected_attachments(self, items: List[Dict[str, Any]],
                              params: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """The attachments the caller explicitly asked to fetch, or none.

        Default is *nothing fetched*: listing is a read, saving a file is a
        write into the workspace, and a read action that quietly wrote files
        would be a side effect nobody approved.
        """
        index = params.get("index")
        filename = str(params.get("filename") or "").strip()
        if index in (None, "") and not filename:
            return []
        if index not in (None, ""):
            try:
                want = int(str(index))
            except (TypeError, ValueError):
                raise OaActionError("index must be a number",
                                    code="invalid_index", stage=STAGE_CONFIG)
            chosen = [item for item in items if item["index"] == want]
            if not chosen:
                raise OaActionError("no attachment at that index",
                                    code="attachment_missing",
                                    stage=STAGE_POLICY)
        else:
            chosen = [item for item in items if item["filename"] == filename]
            if not chosen:
                raise OaActionError("no attachment with that filename",
                                    code="attachment_missing",
                                    stage=STAGE_POLICY)
        if len(chosen) > self.MAX_ATTACHMENT_FETCH:
            raise OaActionError(
                "too many attachments in one call (max %d)"
                % self.MAX_ATTACHMENT_FETCH, code="too_many_attachments",
                stage=STAGE_POLICY)
        for item in chosen:
            if not item["fetchable"]:
                raise OaActionError(
                    "the attachment link is not served by this OA site, so it "
                    "is refused rather than fetched from another host",
                    code="attachment_out_of_scope", stage=STAGE_POLICY)
        return chosen

    def _fetch_attachment(self, item: Mapping[str, Any]) -> Dict[str, Any]:
        """Download one in-scope attachment into the caller's workspace.

        The directory is checked *before* the request: a refused write must not
        make a remote call, and "there is nowhere to put this" is a fact known
        without asking the site.
        """
        from integrations.external.mail import guard

        limits = guard.limits_from(self._ctx)
        directories = guard.attachment_dirs(self._ctx)
        if not directories.dirs:
            raise OaActionError(
                "no attachment directory is configured for this connection, so "
                "nothing may be written", code="no_attachment_dir",
                stage=STAGE_POLICY)
        path = str(item.get("fetchPath") or "")
        try:
            response = self._transport.get(
                "%s%s" % (self._root, path), headers=self._e9_headers(),
                timeout=self._io_timeout(60.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc
        if response.status_code >= 400:
            raise OaActionError("附件下载失败（HTTP %d）" % response.status_code,
                                code="attachment_fetch_failed",
                                stage=STAGE_PROTOCOL)
        data = response.body_bytes() if hasattr(response, "body_bytes") \
            else str(getattr(response, "text", "")).encode("utf-8")
        if len(data) > limits.max_attachment_bytes:
            raise OaActionError("附件超过大小上限", code="attachment_too_large",
                                stage=STAGE_POLICY)
        written = guard.write_attachment(
            self._ctx, directory=None,
            filename=guard.sanitize_message_filename(
                item.get("filename"), fallback="oa-attachment"),
            data=data)
        return {"index": item.get("index"),
                "filename": item.get("filename"),
                "bytes": len(data), **written}

    # -- writes: 抄送标记已读 --------------------------------------------------

    def _cc_mark_read(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """Mark a 抄送 as read — a declared side effect, never implicit.

        The spec is explicit that reading a 抄送 detail must not mark it read
        behind the caller's back: the marker removes the request from the user's
        待阅 list, which is a change they can see. So it is reached only through
        this action, which is declared as a write.
        """
        request_id = self._request_id(params, required=True)
        if not request_id.isdigit():
            raise OaActionError("requestId must be numeric",
                                code="invalid_request_id", stage=STAGE_CONFIG)
        self._require_interface("cc.mark_read")
        self._ensure_e9_session()
        belong_user_id = str(params.get("belong_user_id") or "").strip()
        if not belong_user_id:
            form = self._load_form(request_id)
            form_params = form.get("params") if isinstance(form, Mapping) else {}
            if isinstance(form_params, Mapping):
                belong_user_id = str(
                    form_params.get("f_weaver_belongto_userid")
                    or form_params.get("currentUserid") or "").strip()
        body = self._post_e9_json(
            actions_module.E9_CC_MARK_READ,
            {"opertype": "single", "requestid": request_id,
             "f_weaver_belongto_userid": belong_user_id,
             "f_weaver_belongto_usertype": "0"},
            request_id=request_id)
        confirmed = bool(isinstance(body, Mapping) and (
            body.get("api_status") is True or body.get("submitReqIds")))
        if not confirmed:
            raise OaActionError(
                str((body or {}).get("msg") if isinstance(body, Mapping) else "")
                or "标记抄送已读失败", code="mark_read_unconfirmed",
                stage=STAGE_PROTOCOL)
        return {"interface": "e9", "request_id": request_id,
                "markedAsRead": True}

    # -- shared E9 plumbing for the new reads --------------------------------

    def _post_e9_json(self, path: str, data: Mapping[str, Any], *,
                      request_id: str = "") -> Any:
        """POST a form and parse JSON, treating ``api_status: false`` as an error.

        The E9 endpoints answer HTTP 200 with ``api_status: false`` for a
        permission refusal, so a status code alone would report "success" for a
        request the remote rejected.
        """
        response = self._post_form(path, data,
                                   self._e9_headers(request_id=request_id),
                                   timeout=self._io_timeout(60.0))
        body = self._json(response)
        if isinstance(body, Mapping) and body.get("api_status") is False:
            raise OaActionError(str(body.get("msg") or "远端拒绝该操作"),
                                code="remote_refused", stage=STAGE_POLICY)
        return body

    def _load_detail_data(self, request_id: str,
                          form_params: Mapping[str, Any],
                          marks: List[str]) -> Any:
        """``detailData`` — the detail tables' rows, which hold most links."""
        if not marks:
            return {}
        return self._post_e9_json(
            actions_module.E9_DETAIL_DATA,
            {"requestid": request_id, "detailmark": ",".join(marks),
             "reqParams": json.dumps(actions_module.common_api_params(form_params),
                                     ensure_ascii=False)},
            request_id=request_id)

    # -- writes --------------------------------------------------------------

    def _request_create(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        interface = self._require_interface("request.create")
        if interface != "e10":
            raise OaActionError("当前接口不支持流程创建",
                                code="unsupported_interface",
                                stage=STAGE_POLICY)
        workflow_id = str(params.get("workflow_id") or "").strip()
        title = str(params.get("title") or params.get("request_name") or "").strip()
        if not workflow_id or not title:
            raise OaActionError("workflow_id 和 title 均为必填",
                                code="field_required", stage=STAGE_CONFIG)
        token = self._openapi_token()
        userid = self._e10_userid()
        payload = {
            "access_token": token, "userid": userid,
            "workflowId": workflow_id, "requestName": title,
            "formData": params.get("form_data") or params.get("formData") or {},
        }
        if params.get("next_flow") is not None:
            payload["nextFlow"] = bool(params.get("next_flow"))
        if params.get("next_node_id"):
            payload["nextNodeId"] = str(params.get("next_node_id"))
        return self._write(
            lambda: self._openapi_post(actions_module.E10_CREATE, payload,
                                       code="create_failed",
                                       success_message="流程已创建"),
            reconcile_action="request.read",
            reconcile_params={"request_id": ""})

    def _request_submit(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        request_id = self._request_id(params, required=True)
        remark = self._remark(params)
        interface = self._require_interface("request.submit")
        if interface == "e10" and self._openapi_ok:
            return self._openapi_submit(request_id, remark)
        return self._e9_operation("submit", request_id, remark)

    def _request_reject(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        request_id = self._request_id(params, required=True)
        remark = self._remark(params)
        interface = self._require_interface("request.reject")
        if interface == "e10" and self._openapi_ok:
            return self._openapi_reject(request_id, remark, params)
        return self._e9_operation("reject", request_id, remark)

    def _openapi_submit(self, request_id: str, remark: str) -> Dict[str, Any]:
        self._assert_e10_actionable(request_id, action="submit")
        payload = actions_module.e10_submit_payload(
            access_token=self._openapi_token(), userid=self._e10_userid(),
            request_id=request_id, remark=remark)
        return self._write(
            lambda: self._openapi_post(actions_module.E10_SUBMIT, payload,
                                       code="submit_failed",
                                       success_message="审批已提交"),
            reconcile_action="request.read",
            reconcile_params={"request_id": request_id})

    def _openapi_reject(self, request_id: str, remark: str,
                        params: Mapping[str, Any]) -> Dict[str, Any]:
        self._assert_e10_actionable(request_id, action="reject")
        payload = actions_module.e10_reject_payload(
            access_token=self._openapi_token(), userid=self._e10_userid(),
            request_id=request_id,
            reject_type=str(params.get("reject_type") or ""),
            node_id=str(params.get("reject_node_id") or ""), remark=remark)
        return self._write(
            lambda: self._openapi_post(actions_module.E10_REJECT, payload,
                                       code="reject_failed",
                                       success_message="流程已退回"),
            reconcile_action="request.read",
            reconcile_params={"request_id": request_id})

    def _e9_operation(self, operation: str, request_id: str,
                      remark: str) -> Dict[str, Any]:
        self._ensure_e9_session()
        form = self._load_form(request_id)
        form_params = actions_module.common_api_params(form)
        self._assert_e9_actionable(request_id, operation, form_params)
        payload = actions_module.e9_operation_payload(
            form_params, action=operation, remark=remark)
        headers = self._e9_headers(request_id=request_id)

        def _call() -> Dict[str, Any]:
            response = self._post_form(actions_module.E9_REQUEST_OPERATION,
                                       payload, headers, timeout=self._io_timeout(120.0))
            body = self._json(response)
            data = body.get("data") if isinstance(body, Mapping) else None
            data = data if isinstance(data, Mapping) else body
            op_type = str((data or {}).get("type") or "").upper()
            if op_type and op_type not in ("SUCCESS", "ASYNC_SUBMIT"):
                message_info = (data or {}).get("messageInfo") or {}
                raise OaActionError(
                    str(message_info.get("title") or message_info.get("detail")
                        or "远端拒绝该操作"),
                    code="operation_refused", stage=STAGE_POLICY)
            return {"interface": "e9", "request_id": request_id,
                    "operation": operation, "operation_type": op_type,
                    "message": "操作已提交"}

        return self._write(_call, reconcile_action="request.read",
                           reconcile_params={"request_id": request_id})

    # -- forward / circulate / add-sign (E9 only) ---------------------------

    def _request_forward(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        delegate = bool(params.get("delegate"))
        return self._forward("request.forward", params,
                             forward_kind="turn" if delegate else "forward")

    def _request_circulate(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        return self._forward("request.circulate", params,
                             forward_kind="circulate")

    def _request_addsign(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        return self._forward("request.addsign", params, forward_kind="addsign")

    def _forward(self, action: str, params: Mapping[str, Any], *,
                 forward_kind: str) -> Dict[str, Any]:
        interface = self._require_interface(action)
        if interface != "e9":
            raise OaActionError(
                "该站点发现的接口不支持 %s（仅 E9 浏览器接口支持）" % action,
                code="unsupported_interface", stage=STAGE_POLICY,
                data={"interface": interface})
        request_id = self._request_id(params, required=True)
        remark = self._remark(params)
        if not remark:
            raise OaActionError("转发/转办/传阅/加签须填写 remark 说明",
                                code="remark_required", stage=STAGE_CONFIG)
        self._ensure_e9_session()
        form = self._load_form(request_id)
        form_params = actions_module.common_api_params(form)

        def _call() -> Dict[str, Any]:
            self._assert_e9_actionable(request_id, forward_kind, form_params)
            keyword = str(params.get("recipient")
                          or params.get("search_keyword") or "").strip()
            resource_ids = self._resolve_targets(keyword, params, form_params)
            sign_input = self._sign_input(request_id, form_params, forward_kind)
            payload = actions_module.e9_forward_payload(
                form_params, action=forward_kind, resource_ids=resource_ids,
                remark=remark, sign_input=sign_input)
            response = self._post_form(
                actions_module.E9_REMARK_OPERATE, payload,
                self._e9_headers(request_id=request_id),
                timeout=self._io_timeout(120.0))
            body = self._json(response)
            success = bool(body.get("success")) if isinstance(body, Mapping) else False
            if not success:
                message = ""
                if isinstance(body, Mapping):
                    message = str(body.get("msg") or body.get("message") or "")
                raise OaActionError(message or "远端拒绝该操作",
                                    code="operation_refused", stage=STAGE_POLICY)
            return {"interface": "e9", "request_id": request_id,
                    "action": action, "forward_kind": forward_kind,
                    "recipients": resource_ids,
                    "message": str(body.get("message") or body.get("msg") or "")}

        return self._write(_call, reconcile_action="request.read",
                           reconcile_params={"request_id": request_id})

    # -- target resolution (ambiguity refused) ------------------------------

    def _resolve_targets(self, keyword: str, params: Mapping[str, Any],
                         form_params: Mapping[str, Any]) -> List[str]:
        explicit = self._as_id_list(params.get("recipient_ids")
                                    or params.get("resource_ids"))
        resources: List[Dict[str, str]] = []
        if keyword:
            resources = self._search_people(keyword, form_params)
        if explicit:
            if resources:
                known = {str(item.get("id") or "") for item in resources}
                missing = [rid for rid in explicit if rid not in known]
                if missing:
                    raise OaActionError(
                        "指定的目标人不在搜索结果内：%s" % ", ".join(missing),
                        code="target_not_found", stage=STAGE_POLICY,
                        data={"candidates": resources})
            return explicit
        if not keyword:
            raise OaActionError("未提供目标人（recipient 或 recipient_ids）",
                                code="target_not_found", stage=STAGE_POLICY,
                                data={"candidates": []})
        if not resources:
            raise OaActionError("未搜索到目标人：%s" % keyword,
                                code="target_not_found", stage=STAGE_POLICY,
                                data={"candidates": []})
        if len(resources) > 1:
            raise OaActionError(
                "目标人歧义：匹配到 %d 名候选，请指定 recipient_ids" % len(resources),
                code="target_ambiguous", stage=STAGE_POLICY,
                data={"candidates": resources})
        return [str(resources[0].get("id") or "")]

    def _search_people(self, keyword: str,
                       form_params: Mapping[str, Any]) -> List[Dict[str, str]]:
        payload: Dict[str, Any] = {"q": keyword, "pageSize": 20}
        if form_params:
            payload.update({
                "workflowid": form_params.get("workflowid") or "",
                "nodeid": form_params.get("nodeid") or "",
                "bdf_wfid": form_params.get("workflowid") or "",
                "f_weaver_belongto_userid":
                    form_params.get("f_weaver_belongto_userid") or "",
                "f_weaver_belongto_usertype":
                    form_params.get("f_weaver_belongto_usertype") or "0",
            })
        response = self._post_form(actions_module.E9_HRM_SEARCH, payload,
                                   self._e9_headers())
        return actions_module.normalize_people(self._json(response))

    # -- remote-actionable verification -------------------------------------

    def _assert_e9_actionable(self, request_id: str, action: str,
                              form_params: Mapping[str, Any]) -> None:
        response = self._post_form(actions_module.E9_RIGHT_MENU,
                                   dict(form_params),
                                   self._e9_headers(request_id=request_id),
                                   timeout=self._io_timeout(120.0))
        body = self._json(response)
        menus = body.get("rightMenus") if isinstance(body, Mapping) else None
        menus = menus if isinstance(menus, (list, tuple)) else []
        aliases = _ACTION_MENU_ALIASES.get(action, ())
        for item in menus:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("menuName") or "")
            system_type = str(item.get("systemMenuType") or "")
            button_type = str(item.get("type") or "")
            for alias in aliases:
                if system_type == alias[0] or button_type == alias[1] \
                        or name == alias[2]:
                    return
        available = [str((item or {}).get("menuName") or "")
                     for item in menus if isinstance(item, Mapping)]
        raise OaActionError(
            "远端当前节点不可执行该操作（%s）" % action,
            code="not_actionable", stage=STAGE_POLICY,
            data={"available_actions": available})

    def _assert_e10_actionable(self, request_id: str, *, action: str) -> None:
        info = self._openapi_request_info(request_id)
        detail = info.get("detail") or {}
        if not isinstance(detail, Mapping) or not detail:
            raise OaActionError("远端流程不存在或不可读",
                                code="not_actionable", stage=STAGE_POLICY)
        status = str(detail.get("status") or detail.get("statusType") or "").lower()
        if status in _TERMINAL_STATUSES:
            raise OaActionError(
                "远端流程已处于不可操作状态（status=%s）" % status,
                code="not_actionable", stage=STAGE_POLICY)

    # -- low-level helpers ---------------------------------------------------

    def _load_form(self, request_id: str) -> Dict[str, Any]:
        response = self._post_form(
            actions_module.E9_LOAD_FORM,
            {"requestid": request_id, "ismonitor": "1"},
            self._e9_headers(request_id=request_id),
            timeout=self._io_timeout(120.0))
        body = self._json(response)
        if isinstance(body, Mapping) and body.get("status") is False:
            raise OaActionError(str(body.get("msg") or "loadForm 失败"),
                                code="form_unavailable", stage=STAGE_POLICY)
        if not isinstance(body, Mapping):
            raise OaActionError("loadForm 响应格式异常",
                                code="protocol_error", stage=STAGE_PROTOCOL)
        return body

    def _sign_input(self, request_id: str, form_params: Mapping[str, Any],
                    forward_kind: str) -> Dict[str, Any]:
        flag = actions_module.FORWARD_FLAGS.get(forward_kind, "1")
        payload = {**dict(form_params or {}), "forwardflag": flag,
                   "IsSubmitedOpinion": "1", "IsBeForwardTodo": "1",
                   "field5": ""}
        response = self._post_form(actions_module.E9_SIGN_INPUT, payload,
                                   self._e9_headers(request_id=request_id),
                                   timeout=self._io_timeout(120.0))
        body = self._json(response)
        return body if isinstance(body, Mapping) else {}

    def _openapi_post(self, path: str, payload: Mapping[str, Any], *,
                      code: str, success_message: str) -> Dict[str, Any]:
        try:
            response = self._transport.post_json(
                "%s%s" % (self._root, path), dict(payload),
                secret_bearing=True, timeout=self._io_timeout(30.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc
        body = self._json(response)
        ok, errcode, message = actions_module.parse_openapi_message(body)
        if not ok:
            raise OaActionError(message or "OpenAPI 操作失败", code=code,
                                stage=STAGE_POLICY, data={"errcode": errcode})
        return {"interface": "e10", "message": success_message, "result": body}

    def _write(self, call, *, reconcile_action: str,
               reconcile_params: Mapping[str, Any]) -> Dict[str, Any]:
        """Run a write; a lost response becomes an explicit unknown, never a retry."""

        self._ctx.check_alive()
        try:
            return call()
        except OaActionError as exc:
            # ``_post_form``/``_openapi_post`` normalise a transport failure into
            # an OaActionError, so the "the request may have landed" codes have to
            # be recognised here too — otherwise a lost response would be
            # reported as a plain failure and could be retried.
            if exc.unknown:
                raise
            if exc.code in ("timeout", "network_unreachable"):
                raise self._unknown(exc, reconcile_action,
                                    reconcile_params) from exc
            raise
        except (DeadlineExceeded, Cancelled) as exc:
            raise self._unknown(exc, reconcile_action, reconcile_params) from exc
        except TransportError as exc:
            if exc.stage == STAGE_TIMEOUT or exc.code in ("timeout",
                                                          "network_unreachable"):
                raise self._unknown(exc, reconcile_action,
                                    reconcile_params) from exc
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc

    def _unknown(self, exc: Exception, reconcile_action: str,
                 reconcile_params: Mapping[str, Any]) -> OaActionError:
        return OaActionError(
            "提交结果未知：远端可能已受理，系统不会自动重发，请先重新读取流程状态核对",
            code="outcome_unknown", stage=getattr(exc, "stage", STAGE_TIMEOUT),
            unknown=True,
            data={"reconcile": {"action": reconcile_action,
                                "params": dict(reconcile_params or {})}})

    def _post_form(self, path: str, data: Mapping[str, Any], headers, *,
                   timeout: Optional[float] = None) -> HttpResult:
        try:
            return self._transport.post_form(
                "%s%s" % (self._root, path), data, headers=headers,
                timeout=timeout if timeout is not None
                else self._io_timeout(30.0))
        except TransportError as exc:
            raise OaActionError(exc.detail, code=exc.code,
                                stage=exc.stage) from exc

    def _json(self, response: HttpResult) -> Any:
        if response.status_code >= 400:
            raise OaActionError("远端返回 HTTP %d" % response.status_code,
                                code="remote_http_error",
                                stage=STAGE_PROTOCOL)
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise OaActionError("远端响应不是合法 JSON",
                                code="protocol_error",
                                stage=STAGE_PROTOCOL) from exc

    def _e9_headers(self, *, request_id: str = "") -> Dict[str, str]:
        referer = "%s/spa/workflow/static4form/index.html" % self._root
        if request_id:
            referer += "#/main/workflow/req?requestid=%s" % request_id
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": self._root,
            "Accept": "*/*",
        }

    def _io_timeout(self, default: float) -> float:
        return float(self._ctx.io_timeout(default))

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return default
        return number if number > 0 else default

    @staticmethod
    def _int(value: Any) -> Optional[int]:
        """An optional integer, ``None`` when the remote did not give one.

        Distinct from :meth:`_positive_int`: a missing total is *unknown*, and
        substituting a default there would silently truncate a log walk.
        """
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _request_id(params: Mapping[str, Any], *, required: bool) -> str:
        value = str(params.get("request_id") or params.get("requestId")
                    or params.get("id") or "").strip()
        if required and not value:
            raise OaActionError("request_id 为必填",
                                code="field_required", stage=STAGE_CONFIG)
        return value

    @staticmethod
    def _remark(params: Mapping[str, Any]) -> str:
        return str(params.get("remark") or params.get("comment") or "").strip()

    @staticmethod
    def _as_id_list(value: Any) -> List[str]:
        if value in (None, "", [], {}):
            return []
        if isinstance(value, str):
            parts = value.replace(";", ",").split(",")
        elif isinstance(value, (list, tuple)):
            parts = list(value)
        else:
            parts = [value]
        return [str(part).strip() for part in parts if str(part).strip()]
