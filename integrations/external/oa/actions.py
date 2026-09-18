# encoding:utf-8
"""OA endpoint vocabulary, payload builders and response parsers.

Endpoint paths are taken from OneAgent's ``oa-audit-manager`` CLI
(``cli/config.py`` and ``cli/e10_openapi.py``). They are declared here, not
scattered through the client, so the "unsupported interface" decision and the
real call cannot drift apart. A site whose interface differs is reported as
unsupported rather than guessed at; confirming the actual E9/E10 matrix is
task 7.6 and requires a controlled environment.

EA9 (browser/session API) and E10 (OpenAPI) are genuinely different surfaces:
E10 disables the E9 ``loadForm``/``requestOperation`` paths, and the forward /
circulate / add-sign calls are E9-only. :data:`INTERFACE_ACTIONS` encodes that.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

# -- E9 browser/session API --------------------------------------------------

E9_SPLIT_PAGE_KEY = "/api/workflow/reqlist/splitPageKey"
E9_TABLE_DATAS = "/api/ec/dev/table/datas"
E9_LOAD_FORM = "/api/workflow/reqform/loadForm"
E9_DETAIL_DATA = "/api/workflow/reqform/detailData"
E9_RIGHT_MENU = "/api/workflow/reqform/rightMenu"
E9_REQUEST_OPERATION = "/api/workflow/reqform/requestOperation"
E9_REMARK_OPERATE = "/api/workflow/reqform/remarkOperate"
E9_SIGN_INPUT = "/api/workflow/reqform/signInput"
E9_HRM_SEARCH = "/api/public/browser/complete/17"
E9_SESSION_PROBE = "/api/"
#: 审批流转记录 is two calls: the base info carries the paging keys, the list
#: carries the rows. Taken from OneAgent's ``workflow_flow_log``.
E9_REQUEST_LOG_BASE = "/api/workflow/reqform/getRequestLogBaseInfo"
E9_REQUEST_LOG_LIST = "/api/workflow/reqform/getRequestLogList"
#: Marking a 抄送 as read. A *write* on the remote mailbox-like surface, which
#: is why it is its own action and never a side effect of reading.
E9_CC_MARK_READ = "/api/workflow/reqlist/doReadIt"

# -- E10 OpenAPI -------------------------------------------------------------

E10_AUTHORIZE = "/papi/openapi/oauth2/authorize"
E10_TOKEN = "/papi/openapi/oauth2/access_token"
E10_TODO = "/papi/openapi/workflow/v2/getTodoData"
E10_DONE = "/papi/openapi/workflow/v2/getFinfishData"
E10_REQUEST_INFO = "/papi/openapi/workflow/v2/getInfoByID"
E10_CREATE = "/papi/openapi/workflow/v2/createRequest"
E10_SUBMIT = "/papi/openapi/api/workflow/core/paService/v1/submitRequest"
E10_REJECT = "/papi/openapi/api/workflow/core/paService/v1/rejectRequest"

#: Views of the ``todos.list`` action. The risk catalogue names one read action
#: for the four list surfaces, so the surface is a parameter, not an action.
VIEW_TABID = {"doing": 1, "done": 3, "mine": 6, "cc": 7}
VIEW_CONDITION = {"doing": 5, "done": 10, "mine": 10, "cc": 1}
DEFAULT_VIEW = "doing"

#: Which interface can serve each action. ``request.create`` is E10-only in the
#: documented reference; forward/circulate/addsign are E9-only.
INTERFACE_ACTIONS: Dict[str, frozenset] = {
    "todos.list": frozenset({"e9", "e10"}),
    "request.read": frozenset({"e9", "e10"}),
    # 审批流转记录 / 关联流程 / 附件 are read out of the browser-side form
    # (``loadForm`` + ``detailData`` + ``getRequestLog*``), all of which an E10
    # site disables in favour of OpenAPI — and the OpenAPI surface implemented
    # here exposes ``getInfoByID`` only. So these are E9-only and are *refused*
    # on an E10 site rather than approximated (spec: 某接口不支持 SHALL 明确说明).
    "request.flowlog": frozenset({"e9"}),
    "request.related": frozenset({"e9"}),
    "request.attachments": frozenset({"e9"}),
    # The 抄送 read mark is E9-only for the same reason.
    "cc.mark_read": frozenset({"e9"}),
    "request.create": frozenset({"e10"}),
    "request.submit": frozenset({"e9", "e10"}),
    "request.reject": frozenset({"e9", "e10"}),
    "request.forward": frozenset({"e9"}),
    "request.circulate": frozenset({"e9"}),
    "request.addsign": frozenset({"e9"}),
}

#: E9 forwardflags (OneAgent ``workflow_actions.FORWARD_FLAG_BY_ACTION``).
FORWARD_FLAGS = {"forward": "1", "turn": "3", "circulate": "4", "addsign": "2"}

#: ``request.forward`` covers both 转发 and 转办; the caller may choose.
FORWARD_KINDS = frozenset({"forward", "turn"})

#: E9 ``requestOperation`` src values.
E9_OPERATION_SRC = {"submit": "submit", "reject": "reject"}

#: E10 reject types (OneAgent ``e10_openapi.reject_request_v1``).
E10_REJECT_TYPES = {"free": 0, "step": 1, "range": 2, "exit": 3}
DEFAULT_E10_REJECT_TYPE = "step"

#: E10 ``submitRequest``/``rejectRequest`` treat these as success.
OPENAPI_OK_ERRCODES = ("0", "200")

_UNREAD_ICON = re.compile(r"BDNew_wev8\.png", re.I)
_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>", re.I)


def strip_html(value: Any) -> str:
    return _TAG.sub("", str(value or "")).strip()


def strip_rich_text(value: Any) -> str:
    """Plain text out of a rich-text field, keeping the line structure.

    Used for an approval remark, where the line breaks carry meaning: OneAgent
    renders ``<br>`` as a newline and collapses the blank lines it produces, and
    an opinion that arrives as one run-on line is harder to read than the HTML
    it came from.
    """
    text = _BREAK.sub("\n", str(value or ""))
    text = _TAG.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&") \
               .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{2,}", "\n", text).strip()


# -- E9 list ----------------------------------------------------------------


def e9_search_params(view: str, *, workflow_id: str = "",
                     request_name: str = "") -> Dict[str, Any]:
    """The queryFlowResult parameters for one list surface (E9)."""

    key = str(view or DEFAULT_VIEW).strip().lower()
    tabid = VIEW_TABID.get(key, VIEW_TABID[DEFAULT_VIEW])
    params: Dict[str, Any] = {
        "method": "all",
        "offical": "",
        "officalType": -1,
        "viewScope": "doing",
        "eid": 13,
        "tabid": tabid,
        "fromwhere": "jsonFilter",
        "viewcondition": VIEW_CONDITION.get(key, VIEW_CONDITION[DEFAULT_VIEW]),
        "synergyWorkflowid": tabid if key == "cc" else 1,
        "synergyRequestid": -1,
    }
    if workflow_id:
        params["workflowid"] = str(workflow_id).strip()
    if request_name:
        params["requestname"] = str(request_name).strip()
    return params


def resolve_session_key(body: Any) -> str:
    """E9/E10 differ in the ``splitPageKey`` field name; accept all of them."""

    if not isinstance(body, Mapping):
        return ""
    for key in ("sessionkey", "sessionKey", "dataKey", "session_key"):
        value = body.get(key)
        if value:
            return str(value)
    return ""


def normalize_e9_item(row: Any) -> Dict[str, Any]:
    """Normalise one ``ec/dev/table/datas`` row into stable fields."""

    if not isinstance(row, Mapping):
        return {}
    request_name_html = str(row.get("requestnamespan") or row.get("requestname")
                            or "")
    return {
        "requestId": str(row.get("requestid") or row.get("requestId") or ""),
        "requestName": strip_html(request_name_html),
        "requestMark": str(row.get("requestmark") or row.get("wfcode") or ""),
        "workflowName": str(row.get("workflowname") or row.get("workflowName") or ""),
        "creator": strip_html(row.get("creator") or row.get("creater") or ""),
        "receivedDate": strip_html(row.get("receivedate")
                                   or row.get("receivedatespan") or ""),
        "currentNode": strip_html(row.get("currentnodename")
                                  or row.get("nodename") or ""),
        "status": strip_html(row.get("status") or ""),
        "isUnread": bool(_UNREAD_ICON.search(request_name_html)),
    }


def normalize_e9_items(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, (list, tuple)):
        return []
    return [item for item in (normalize_e9_item(row) for row in rows) if item]


def page_info(body: Any, fallback_size: int) -> Dict[str, Any]:
    if not isinstance(body, Mapping):
        return {"page_size": fallback_size, "total": None}
    try:
        page_size = int(body.get("pageSize") or fallback_size)
    except (TypeError, ValueError):
        page_size = fallback_size
    total = body.get("total")
    try:
        total_value: Optional[int] = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_value = None
    return {"page_size": page_size, "total": total_value}


# -- E10 OpenAPI -------------------------------------------------------------


def parse_openapi_message(body: Any) -> tuple:
    """Returns ``(ok, errcode, message)`` for an OpenAPI envelope."""

    if not isinstance(body, Mapping):
        return False, "", "响应格式异常"
    message = body.get("message")
    if isinstance(message, Mapping):
        errcode = str(message.get("errcode"))
        message_text = str(message.get("errmsg") or "")
    else:
        errcode = str(body.get("errcode") or body.get("code") or "")
        message_text = str(body.get("errmsg") or body.get("message") or "")
    ok = errcode in OPENAPI_OK_ERRCODES if errcode else False
    return ok, errcode, message_text


def normalize_openapi_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, Mapping):
        return {}
    creator = item.get("creator") or {}
    return {
        "requestId": str(item.get("id") or ""),
        "requestName": str(item.get("name") or ""),
        "requestMark": str(item.get("serNum") or ""),
        "statusType": str(item.get("statusType") or ""),
        "creatorId": str((creator or {}).get("id") or ""),
        "creatorName": str((creator or {}).get("username") or ""),
        "requestTime": str(item.get("requestTime") or ""),
        "formId": item.get("formId"),
        "flowSequence": item.get("flowSequence"),
    }


def normalize_people(body: Any) -> List[Dict[str, str]]:
    """Normalise the human-resource browser response (E9 HRM search)."""

    if not isinstance(body, Mapping):
        return []
    resources = []
    for item in body.get("datas") or ():
        if not isinstance(item, Mapping):
            continue
        resources.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("lastname") or ""),
            "department": str(item.get("departmentname")
                              or item.get("departmentidspan") or ""),
            "jobTitle": str(item.get("jobtitlename") or ""),
            "email": str(item.get("email") or ""),
        })
    return resources


def e9_forward_payload(form_params: Mapping[str, Any], *, action: str,
                       resource_ids: List[str], remark: str,
                       sign_input: Mapping[str, Any]) -> Dict[str, Any]:
    flag = FORWARD_FLAGS.get(str(action or ""), "1")
    is_submitted = str((sign_input or {}).get("IsSubmitedOpinion") or "1")
    is_be_forward = str((sign_input or {}).get("IsBeForwardTodo") or "1")
    return {
        **dict(form_params or {}),
        "operate": "save",
        "field5": ",".join(resource_ids),
        "forwardflag": flag,
        "remark": remark or "",
        "IsSubmitedOpinion": is_submitted,
        "IsBeForwardTodo": is_be_forward,
        "IsBeForwardSubmitAlready": is_submitted,
        "IsBeForwardAlready": is_be_forward,
    }


def e9_operation_payload(form_params: Mapping[str, Any], *, action: str,
                         remark: str) -> Dict[str, Any]:
    return {
        **dict(form_params or {}),
        "src": E9_OPERATION_SRC.get(str(action or ""), "submit"),
        "actiontype": "requestOperation",
        "remark": remark or "",
    }


def e10_reject_payload(*, access_token: str, userid: str, request_id: str,
                       reject_type: str, node_id: str = "",
                       remark: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "access_token": access_token,
        "userid": userid,
        "requestId": str(request_id),
        "RejectToType": E10_REJECT_TYPES.get(str(reject_type or ""),
                                             E10_REJECT_TYPES[DEFAULT_E10_REJECT_TYPE]),
    }
    if node_id:
        payload["RejectToNodeid"] = str(node_id)
    if remark:
        payload["remark"] = remark
    return payload


def e10_submit_payload(*, access_token: str, userid: str, request_id: str,
                       remark: str = "") -> Dict[str, Any]:
    payload = {"access_token": access_token, "userid": userid,
               "requestId": str(request_id)}
    if remark:
        payload["remark"] = remark
    return payload


#: Common ``loadForm`` parameters E9 write paths echo back.
COMMON_API_PARAM_KEYS = (
    "requestid", "workflowid", "nodeid", "formid", "isbill",
    "f_weaver_belongto_userid", "f_weaver_belongto_usertype",
    "authStr", "authSignatureStr", "signatureSecretKey",
    "signatureAttributesStr", "nodetype", "iscreate", "isfromtab",
    "isviewonly", "isaffirmance", "isshared", "iswfshare", "ismode", "modeid",
    "isagent", "beagenter", "creater", "creatertype", "requestType",
    "isSelfAuth", "layouttype", "apiResultCacheKey", "agentType",
    "agentorByAgentId", "isremark",
)


def common_api_params(form_body: Any) -> Dict[str, Any]:
    params = (form_body or {}).get("params") if isinstance(form_body, Mapping) else {}
    if not isinstance(params, Mapping):
        return {}
    return {key: params[key] for key in COMMON_API_PARAM_KEYS if key in params}


# -- 审批流转记录 (getRequestLogBaseInfo + getRequestLogList) ----------------

#: ``getRequestLogList`` echoes the form's own parameters back, so the ones that
#: decide *which* log set is returned are listed here rather than copied from
#: whatever the form happened to contain. Taken from OneAgent's
#: ``workflow_flow_log``.
LOG_PARAM_KEYS = (
    "requestid", "requestType", "workflowid", "nodeid",
    "signatureAttributesStr", "signatureSecretKey",
    "f_weaver_belongto_userid", "f_weaver_belongto_usertype",
    "authStr", "authSignatureStr", "isprint", "wfTestStr", "isurger",
)

#: Keys the base-info call adds to the paging query (it owns them; the list call
#: only echoes them back).
LOG_BASE_EXTRA_KEYS = (
    "viewLogIds", "creatorNodeId", "isHideInput", "isFormSignature",
    "maxrequestlogid", "wfsignlddtcnt", "orderbytype", "logpagesize",
    "loadmethod",
)

#: Hard bound on how many log pages one call will follow. A site that always
#: reports more rows than it returns would otherwise loop until the deadline.
MAX_LOG_PAGES = 20

_LOG_ORDER = frozenset({"asc", "desc"})


def coerce_log_order(value: Any) -> str:
    """``"asc"`` unless the caller explicitly asked for newest-first."""
    return "desc" if str(value or "").strip().lower() == "desc" else "asc"


def build_request_log_params(form_params: Mapping[str, Any], *,
                             request_id: str) -> Dict[str, str]:
    """The base-info parameters, derived from the form's own params."""
    params: Dict[str, str] = {}
    form = dict(form_params or {})
    for key in LOG_PARAM_KEYS:
        raw = form.get(key)
        if key == "requestid":
            raw = raw or request_id
        params[key] = "" if raw is None else str(raw)
    if not params.get("f_weaver_belongto_userid"):
        params["f_weaver_belongto_userid"] = str(form.get("currentUserid") or "")
    params["submit"] = params.get("requestType") or ""
    params["pgnumber"] = "1"
    params["maxrequestlogid"] = "0"
    params["fromCommunication"] = str(form.get("fromCommunication") or "")
    return params


def normalize_flow_log_item(raw: Any) -> Dict[str, str]:
    """One ``loglist`` row, in stable fields.

    ``log_remarkHtml`` is stripped rather than returned: the opinion text is an
    approval comment, and the HTML around it is form layout that would only
    bloat the model's context with tags.
    """
    if not isinstance(raw, Mapping):
        return {}
    operation = str(raw.get("operationname") or "").strip()
    node_name = str(raw.get("log_nodename") or "").strip()
    node_label = "[%s/%s]" % (node_name, operation) if node_name and operation \
        else (node_name or operation)
    remark = strip_rich_text(raw.get("log_remarkHtml"))
    agent_name = str(raw.get("displaybyagentname") or "").strip()
    if agent_name:
        remark = ("%s\n（代 %s）" % (remark, agent_name)).strip()
    operated_at = " ".join(
        part for part in (str(raw.get("log_operatedate") or "").strip(),
                          str(raw.get("log_operatetime") or "").strip()) if part)
    return {
        "logId": str(raw.get("logid") or ""),
        "operatorName": str(raw.get("displayname") or "").strip(),
        "department": str(raw.get("displaydepname") or "").strip(),
        "action": operation,
        "recipients": str(raw.get("receiveUser") or "").strip(),
        "operatedAt": operated_at,
        "nodeName": node_name,
        "nodeLabel": node_label,
        "remark": remark,
    }


def normalize_flow_log_items(rows: Any) -> List[Dict[str, str]]:
    if not isinstance(rows, (list, tuple)):
        return []
    return [item for item in (normalize_flow_log_item(row) for row in rows)
            if item]


def sort_flow_log_items(items: List[Dict[str, str]], *, order: str = "asc"
                        ) -> List[Dict[str, str]]:
    """Chronological by default, because a flow reads forwards.

    Sorted on the *normalized* ``operatedAt`` rather than on the remote log id:
    the id is a database key whose order is not promised to match the clock, and
    an approval trail that reads out of order is worse than an unsorted one.
    """
    rows = list(items or [])
    if order not in _LOG_ORDER or order == "desc" or len(rows) <= 1:
        return rows
    return sorted(rows, key=lambda item: (item.get("operatedAt") or "",
                                          item.get("logId") or ""))


def flow_log_view(body: Any, *, order: str, item_count: int) -> Dict[str, Any]:
    """The paging facts of one ``getRequestLogList`` response."""
    page = body if isinstance(body, Mapping) else {}
    total = page.get("totalCount")
    try:
        total_value: Optional[int] = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_value = None
    return {
        "order": coerce_log_order(order),
        "totalCount": total_value,
        "itemCount": item_count,
        "hasMore": bool(page.get("maxrequestlogid")),
    }


# -- 附件 (form ``specialobj.filedatas``) -----------------------------------

#: Fields one attachment carries, so a caller can decide whether to fetch it
#: without the adapter having to send the bytes to answer the question.
ATTACHMENT_FIELDS = ("index", "fieldId", "fieldLabel", "filename", "filesize",
                     "fileExtendName", "uploaddate", "fileid", "downloadPath",
                     "viewPath")


def collect_attachments(maindata: Any, table_info: Any) -> List[Dict[str, Any]]:
    """Every attachment on a form's main table, in a stable, indexed order.

    Walks ``maindata.fieldN.specialobj.filedatas`` — the shape 泛微 uses for a
    file field — and numbers the results across fields so a caller can address
    one file ("序号 3") without deduplicating field ids. The ``fieldinfomap``
    gives each file its human label, which is what makes the list reviewable.
    """
    data = maindata if isinstance(maindata, Mapping) else {}
    info = table_info if isinstance(table_info, Mapping) else {}
    # Accept either the form's whole ``tableInfo`` or the main table's own block:
    # the main metadata is what gives each file field its label, and a caller
    # holding the outer object should not silently lose the labels.
    if "fieldinfomap" not in info and isinstance(info.get("main"), Mapping):
        info = info.get("main")
    field_info_map = info.get("fieldinfomap") or {}
    if not isinstance(field_info_map, Mapping):
        field_info_map = {}
    out: List[Dict[str, Any]] = []
    for field_key in sorted(data, key=lambda key: str(key)):
        if not str(field_key).startswith("field"):
            continue
        field_data = data.get(field_key)
        if not isinstance(field_data, Mapping):
            continue
        special = field_data.get("specialobj")
        files = special.get("filedatas") if isinstance(special, Mapping) else None
        if not isinstance(files, (list, tuple)):
            continue
        field_id = str(field_key).replace("field", "", 1)
        meta = field_info_map.get(field_id) or {}
        label = str((meta or {}).get("fieldlabel") or field_id)
        for item in files:
            if not isinstance(item, Mapping):
                continue
            out.append({
                "index": len(out) + 1,
                "fieldId": field_id,
                "fieldLabel": label,
                "filename": str(item.get("filename") or ""),
                "filesize": str(item.get("filesize") or ""),
                "fileExtendName": str(item.get("fileExtendName") or ""),
                "uploaddate": str(item.get("uploaddate") or ""),
                "fileid": str(item.get("fileid") or item.get("imagefileid") or ""),
                "downloadPath": str(item.get("loadlink") or ""),
                "viewPath": str(item.get("filelink") or ""),
            })
    return out


# -- 关联流程 (workflow browser fields) -------------------------------------

#: 泛微 renders a "workflow browser" with ``htmltype=3``. Which ``detailtype`` a
#: given form uses varies by form and version, so the type alone is never
#: sufficient — the label and field name carry the rest of the decision.
WORKFLOW_BROWSER_DETAIL_TYPES = frozenset({"16", "152", "161", "162"})

WORKFLOW_BROWSER_LABEL_KEYWORDS = (
    "费用事前流程", "事前申请流程", "关联事前申请", "关联审批", "事前申请单",
    "关联业务申请", "出差申请单", "专项团建费事前申请", "乐药临时工事前申请",
    "关联出差申请", "关联流程", "关联申请",
)

#: Field-name fragments seen in the wild for these links (pinyin abbreviations).
WORKFLOW_BROWSER_FIELD_NAME_PATTERN = re.compile(
    r"(?:^|_)(?:fysqlc|sqlc|glsqsq|glsplc|glsq|glsp|ccsqd|tshysqsq|lylsgsqsqlc"
    r"|sqsqlc|sqsq)(?:$|_)", re.I)

#: Workflow browsers that are *not* a related application (loans, HR org pickers).
WORKFLOW_BROWSER_EXCLUDED_LABEL_KEYWORDS = (
    "借款流程", "借款单号", "申请人", "申请部门", "申请日期", "申请所属公司",
    "申请人职位", "收款对象", "费用承担部门", "费用所属部门",
)

WORKFLOW_BROWSER_EXCLUDED_FIELD_NAMES = frozenset({
    "jklc", "sqr", "sqbm", "sqrq", "sqszgs", "sqrzw", "skr", "fycdbm", "fyssbm",
})


def _html_type(field_info: Any) -> str:
    if not isinstance(field_info, Mapping):
        return ""
    return str(field_info.get("htmltype")
               or field_info.get("fieldhtmltype") or "").strip()


def _detail_type(field_info: Any) -> str:
    if not isinstance(field_info, Mapping):
        return ""
    return str(field_info.get("detailtype") or "").strip()


def is_related_workflow_field(field_info: Any) -> bool:
    """Whether a form field is a *related application* link.

    Label keywords are checked first and the exclusions are absolute: a loan
    workflow browser is also a ``htmltype=3`` field, and treating "借款流程" as a
    related application would pull an unrelated request's detail into scope.
    """
    if _html_type(field_info) != "3":
        return False
    info = field_info if isinstance(field_info, Mapping) else {}
    label = str(info.get("fieldlabel") or "")
    field_name = str(info.get("fieldname") or "")
    if any(keyword in label
           for keyword in WORKFLOW_BROWSER_EXCLUDED_LABEL_KEYWORDS):
        return False
    if field_name.strip().lower() in WORKFLOW_BROWSER_EXCLUDED_FIELD_NAMES:
        return False
    if any(keyword in label for keyword in WORKFLOW_BROWSER_LABEL_KEYWORDS):
        return True
    if WORKFLOW_BROWSER_FIELD_NAME_PATTERN.search(field_name):
        return True
    if _detail_type(info) in WORKFLOW_BROWSER_DETAIL_TYPES:
        if "借款" in label:
            return False
        if "流程" in label and any(token in label for token in (
                "事前", "关联", "申请", "出差", "团建", "招待", "临时", "业务")):
            return True
        if any(token in label
               for token in ("事前", "关联审批", "关联申请", "申请单", "业务申请")):
            return True
    return False


def _linked_request_id(field_data: Any) -> str:
    """The request id a browser field points at, or ``""``.

    Accepts both shapes 泛微 uses: the linked id inside ``specialobj`` (current
    forms) and a bare numeric ``value`` (older ones). A non-numeric or
    non-positive value is not a request id and is refused rather than passed on.
    """
    if not isinstance(field_data, Mapping):
        return ""
    special = field_data.get("specialobj")
    if isinstance(special, list):
        items = [item for item in special if isinstance(item, Mapping)]
    elif isinstance(special, Mapping):
        items = [special]
    else:
        items = []
    candidate = ""
    if items:
        candidate = str(items[0].get("id") or "").strip()
    if not candidate:
        candidate = str(field_data.get("value") or "").strip()
    if not candidate.isdigit() or int(candidate) <= 0:
        return ""
    return candidate


def discover_related_workflow_refs(form_body: Any,
                                   detail_data: Any = None
                                   ) -> List[Dict[str, Any]]:
    """Every related-application link on a form, deduplicated by target id.

    Scans the main table and every detail table, because the link is often on a
    detail row (a contract's 事前申请 line) rather than on the main form. A field
    the caller may not see is skipped: the remote already decided the session
    cannot read that column, and following its link anyway would be reading
    around the remote's own permission decision.
    """
    body = form_body if isinstance(form_body, Mapping) else {}
    tables = body.get("tableInfo") or {}
    if not isinstance(tables, Mapping):
        tables = {}
    out: Dict[str, Dict[str, Any]] = {}

    def _scan(fields: Any, info_map: Any, source: str) -> None:
        if not isinstance(fields, Mapping):
            return
        if not isinstance(info_map, Mapping):
            info_map = {}
        for field_key in sorted(fields, key=lambda key: str(key)):
            if not str(field_key).startswith("field"):
                continue
            field_id = str(field_key).replace("field", "", 1)
            info = info_map.get(field_id)
            if not isinstance(info, Mapping):
                continue
            if not is_related_workflow_field(info):
                continue
            linked = _linked_request_id(fields.get(field_key))
            if not linked:
                continue
            out.setdefault(linked, {
                "linkedRequestId": linked,
                "fieldId": field_id,
                "fieldLabel": str(info.get("fieldlabel") or field_id),
                "fieldName": str(info.get("fieldname") or ""),
                "source": source,
            })

    main_table = tables.get("main") or {}
    _scan(body.get("maindata"),
          (main_table or {}).get("fieldinfomap"), "main")

    detail = detail_data if isinstance(detail_data, Mapping) else {}
    for mark in sorted(detail, key=lambda key: str(key)):
        block = detail.get(mark)
        if not str(mark).startswith("detail_") or not isinstance(block, Mapping):
            continue
        table = tables.get(mark) or {}
        info_map = (table or {}).get("fieldinfomap")
        for row in (block.get("rowDatas") or {}).values():
            if isinstance(row, Mapping):
                _scan(row, info_map, str(mark))
    return [out[key] for key in sorted(out)]


# -- attachment URL scope ---------------------------------------------------

def same_origin_path(candidate: Any, base_url: str) -> str:
    """A server-relative path for ``candidate``, or ``""`` when it is not ours.

    An attachment link is data taken from a remote form, so it must not be able
    to turn this adapter into a fetcher for an arbitrary host: only a relative
    path, or an absolute URL on the connection's *own* site, becomes something
    the client will request. Anything else answers ``""`` and the caller reports
    it as unfetchable rather than silently skipping it.
    """
    text = str(candidate or "").strip()
    if not text:
        return ""
    if "\\" in text or "\n" in text or "\r" in text:
        return ""
    base = urlparse(str(base_url or ""))
    if text.startswith(("http://", "https://", "//")):
        target = urlparse(text if "//" != text[:2] else "%s:%s" % (base.scheme, text))
        if not target.hostname or not base.hostname:
            return ""
        if target.hostname.lower() != base.hostname.lower():
            return ""
        if target.scheme.lower() != base.scheme.lower():
            return ""
        return target.path + (("?" + target.query) if target.query else "")
    if not text.startswith("/"):
        return ""
    return text


def discover_detail_marks(table_info: Any) -> List[str]:
    """The ``detail_N`` marks a form's ``tableInfo`` declares, in order.

    Every mark whose block carries its own ``fieldinfomap`` is a detail table;
    asking for them explicitly is what the ``detailData`` call requires, and
    deriving the list from the form (rather than from a profile) is what makes
    the read work on any workflow rather than only the ones with a hand-written
    profile.
    """
    if not isinstance(table_info, Mapping):
        return []
    marks = []
    for mark in table_info:
        text = str(mark)
        if not text.startswith("detail_"):
            continue
        block = table_info.get(mark)
        if isinstance(block, Mapping):
            marks.append(text)
    return sorted(marks)
