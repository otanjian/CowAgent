# encoding:utf-8
"""``GET /api/memory`` / ``GET /api/memory/content``: one compatibility read
surface, three explicit targets (change ``complete-database-capability-parity``,
task group 5).

Why a separate module
---------------------
The two endpoints above are upstream's *Agent workspace memory* read. After the
fork the same question has three different answers, and only one of them is
"the Agent's workspace":

``personal``       the caller's own ``/api/memory/personal`` domain — keyed by
                   *(tenant, user)*, never by an Agent. Delegated to the
                   delivered :class:`PersonalMemoryService`.
``private_agent``  a privately owned Agent's memory. Tenant-wide reach is not
                   enough: the owner check (``_require_private_owner``) runs
                   *before* anything is read, and an administrator is not an
                   exception.
``shared``         the tenant's shared Agent memory, which any member of that
                   tenant with ``memory.read`` may read.

The handler keeps its upstream shape (one ``try``, one ``except``), and this
module owns the two things that must not be spread across handlers: resolving a
request into exactly one target, and refusing when there is no single correct
target. It is a *delegation* seam — no second memory CRUD, no second index, no
second route table.

Deliberately refused, never guessed
-----------------------------------
* an unknown ``scope`` (``unknown_scope``) — including an empty tenant root:
  the personal scope never falls back to the tenant shared root (design D5), so
  a caller who asks for "my memory" and has none gets an empty *personal* list,
  not the tenant's files;
* ``scope=personal`` together with ``agent_id`` (``ambiguous_target``): the
  personal domain is not an Agent's, so accepting both would mean silently
  picking one;
* an unknown ``category`` (``unknown_category``), and any category other than
  ``memory`` for the personal domain;
* an address that does not resolve (``unknown_agent`` / ``unknown_entry``);
* a target owned by someone else (``not_owner``) — checked before the read, so
  a refusal cannot be a filtered read;
* a path that is not a plain file inside the root (``unsafe_path``).

Codes are stable strings in the JSON body (``{"status": "error", "code": ...,
"message": ...}``); the HTTP status is the class of refusal (400 malformed,
403 not-yours, 404 unknown, 503 unavailable), which is what the console
branches on and what the route gate already answers for a foreign tenant.

Cross-tenant note
-----------------
There is deliberately **no** ``cross_tenant`` code here. A target that belongs
to another tenant is answered as ``unknown_agent`` with 404 — the same
non-disclosing answer ``_require_tenant_agent_binding`` has always given (pinned
by ``tests/test_tenant_read_scoping.py``), because distinguishing "exists in
another tenant" from "does not exist" is exactly the existence leak the change
forbids. The 403 a foreign tenant's *member* receives comes from the HTTP gate,
which refuses the tenant selection itself (no membership), not from here.

Read-only stage
---------------
Both methods are reads. The personal scope's payload carries
``"read_only": true`` because this surface serves no write verb for it: writes
go to ``POST /api/memory/personal`` (which the response's ``id``/``revision``
address). The Agent scopes' edit path is the workspace file API, and their
payloads make no claim about it.

Client contract (for the console page, task 9.1)
------------------------------------------------
``personal`` rows are addressed by ``id`` (= ``filename`` = the entry id
relative to the *personal root*, which is ``<tenant>/users/<user_id>``, **not**
the workspace root). A page that edits must route those rows to
``/api/memory/personal``; this surface is a read and reports ``read_only``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import web

from common.log import logger
from common import safe_fs

# --- scope vocabulary -------------------------------------------------------

SCOPE_PERSONAL = "personal"
SCOPE_PRIVATE_AGENT = "private_agent"
SCOPE_SHARED = "shared"

#: The scope names a caller may pass. Anything else is ``unknown_scope``: a
#: typo must not silently read the caller's own memory, and an omitted scope is
#: *not* a synonym for "whatever is convenient".
SCOPES = (SCOPE_PERSONAL, SCOPE_PRIVATE_AGENT, SCOPE_SHARED)

#: Categories the Agent workspace memory reader knows (upstream's three tabs).
CATEGORY_MEMORY = "memory"
CATEGORY_DREAM = "dream"
CATEGORY_EVOLUTION = "evolution"
CATEGORIES = (CATEGORY_MEMORY, CATEGORY_DREAM, CATEGORY_EVOLUTION)

#: Categories addressable in the personal domain. Personal memory is one list;
#: dream diaries and evolution logs are Agent artefacts, not the member's own
#: memory, and answering the personal scope from an Agent's directory is the
#: scope widening this surface exists to prevent.
PERSONAL_CATEGORIES = (CATEGORY_MEMORY,)

MAIN_ENTRY = "MEMORY.md"

#: Upper bound on one compatibility page, so a client cannot ask the console to
#: render an unbounded domain in a single response.
MAX_PAGE_SIZE = 200

# --- stable machine codes ---------------------------------------------------

CODE_UNKNOWN_SCOPE = "unknown_scope"
CODE_AMBIGUOUS_TARGET = "ambiguous_target"
CODE_UNKNOWN_CATEGORY = "unknown_category"
CODE_UNKNOWN_AGENT = "unknown_agent"
CODE_UNKNOWN_ENTRY = "unknown_entry"
CODE_ENTRY_REQUIRED = "entry_required"
CODE_INVALID_ENTRY = "invalid_entry"
CODE_INVALID_PAGING = "invalid_paging"
CODE_NOT_OWNER = "not_owner"
CODE_UNSAFE_PATH = "unsafe_path"
CODE_MEMORY_UNAVAILABLE = "memory_unavailable"

_STATUS_LINES = {
    400: "400 Bad Request",
    401: "401 Unauthorized",
    403: "403 Forbidden",
    404: "404 Not Found",
    409: "409 Conflict",
    503: "503 Service Unavailable",
}

#: Directories the Agent memory reader lists per category. Guarded as a whole:
#: they live under the tenant shared root, so a symlinked one would redirect the
#: read into another member's personal directory.
_AGENT_LIST_DIRS = {
    CATEGORY_MEMORY: ("memory",),
    CATEGORY_DREAM: ("memory/dreams",),
    CATEGORY_EVOLUTION: ("memory/evolution", "memory/dreams"),
}

_ROW_SUBDIRS = {
    "daily": "memory",
    "dream": "memory/dreams",
    "evolution": "memory/evolution",
}


class MemoryScopeError(Exception):
    """A refused compatibility read, with the code the console branches on."""

    def __init__(self, code: str, message: str = "", *, status: int = 400):
        super().__init__(message or code)
        self.code = code
        self.status = int(status)


class MemoryTarget:
    """One resolved read target: scope, Agent, category and page."""

    __slots__ = ("scope", "agent_id", "category", "page", "page_size", "entry")

    def __init__(self, scope: str, agent_id: Optional[str], category: str,
                 page: int, page_size: int, entry: str = ""):
        self.scope = scope
        self.agent_id = agent_id
        self.category = category
        self.page = page
        self.page_size = page_size
        self.entry = entry

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return ("MemoryTarget(scope=%r, agent_id=%r, category=%r, page=%r, "
                "page_size=%r, entry=%r)" % (self.scope, self.agent_id,
                                             self.category, self.page,
                                             self.page_size, self.entry))


def http_error(error: MemoryScopeError) -> "web.HTTPError":
    """The ``web.HTTPError`` a refusal is answered with (status + JSON body)."""
    body = json.dumps({"status": "error", "code": error.code,
                       "message": str(error)}, ensure_ascii=False)
    return web.HTTPError(
        _STATUS_LINES.get(error.status, "%d Error" % error.status),
        {"Content-Type": "application/json; charset=utf-8"}, body)


def unavailable(_exc: BaseException = None) -> MemoryScopeError:
    """The one refusal for "the memory store could not answer at all"."""
    return MemoryScopeError(CODE_MEMORY_UNAVAILABLE, "记忆暂时不可用", status=503)


# --- parameter reading ------------------------------------------------------

def _web_channel():
    from channel.web import web_channel
    return web_channel


def _first(value):
    """``web.input`` merges query and body, so a repeated field arrives as a list."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _text_param(params, *names: str) -> str:
    for name in names:
        value = _first(getattr(params, name, None))
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _read_scope(params) -> Optional[str]:
    raw = _first(getattr(params, "scope", None))
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text not in SCOPES:
        raise MemoryScopeError(CODE_UNKNOWN_SCOPE,
                               "未知的记忆作用域: %s" % text, status=400)
    return text


def _read_category(params, scope: str) -> str:
    raw = _first(getattr(params, "category", None))
    text = str(raw).strip() if raw is not None else ""
    if not text:
        text = CATEGORY_MEMORY
    if text not in CATEGORIES:
        raise MemoryScopeError(CODE_UNKNOWN_CATEGORY,
                               "未知的记忆分类: %s" % text, status=400)
    if scope == SCOPE_PERSONAL and text not in PERSONAL_CATEGORIES:
        raise MemoryScopeError(
            CODE_UNKNOWN_CATEGORY,
            "个人记忆不提供该分类: %s" % text, status=400)
    return text


def _read_paging(params) -> "tuple[int, int]":
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise MemoryScopeError(
            CODE_INVALID_PAGING,
            "分页参数无效（page>=1，1<=page_size<=%d）" % MAX_PAGE_SIZE, status=400)
    return page, page_size


def _int_param(params, name: str, default: int) -> int:
    raw = _first(getattr(params, name, None))
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise MemoryScopeError(CODE_INVALID_PAGING,
                               "分页参数无效: %s" % name, status=400) from None


# --- target resolution ------------------------------------------------------

def resolve_target(ctx, params, *, entry: bool = False) -> MemoryTarget:
    """Turn one request into exactly one read target, or refuse.

    ``ctx`` is the verified :class:`auth.runtime.RequestContext`; every
    ownership fact comes from it, never from a parameter.
    """
    wc = _web_channel()
    requested = _read_scope(params)
    agent_id = wc._request_agent_id(params)
    entry_name = _text_param(params, "filename", "entry", "id") if entry else ""
    if entry and not entry_name:
        raise MemoryScopeError(CODE_ENTRY_REQUIRED, "缺少 filename", status=400)

    if requested == SCOPE_PERSONAL or (requested is None and not agent_id):
        if agent_id:
            raise MemoryScopeError(
                CODE_AMBIGUOUS_TARGET,
                "个人记忆不接受 agent_id：个人记忆属于成员，不属于某个智能体", status=400)
        category = _read_category(params, SCOPE_PERSONAL)
        page, page_size = _read_paging(params)
        return MemoryTarget(SCOPE_PERSONAL, None, category, page, page_size,
                            entry_name)

    if not agent_id:
        raise MemoryScopeError(CODE_AMBIGUOUS_TARGET,
                               "缺少目标：需要 agent_id 或 scope=personal", status=400)

    category = _read_category(params, requested or SCOPE_SHARED)
    page, page_size = _read_paging(params)
    scope, agent_id = _resolve_agent_target(ctx, agent_id, requested)
    return MemoryTarget(scope, agent_id, category, page, page_size, entry_name)


def _resolve_agent_target(ctx, agent_id: str, requested: Optional[str]):
    """``(scope, agent_id)`` for an Agent-addressed read, or a refusal."""
    wc = _web_channel()
    try:
        wc._require_tenant_agent_binding(ctx, agent_id)
    except web.HTTPError as error:
        if _status_of(error) == 404:
            raise MemoryScopeError(CODE_UNKNOWN_AGENT, "该智能体不属于当前租户",
                                   status=404) from error
        raise
    try:
        wc._require_private_owner(ctx, agent_id)
    except web.HTTPError as error:
        raise MemoryScopeError(CODE_NOT_OWNER, "该记忆属于其他成员", status=403) from error

    binding = _agent_binding(agent_id)
    owner = binding.get("private_owner_user_id") if binding else None
    kind = SCOPE_PRIVATE_AGENT if owner else SCOPE_SHARED
    if kind == SCOPE_PRIVATE_AGENT and owner != getattr(ctx, "user_id", None):
        # ``_require_private_owner`` refuses this already; repeated here so the
        # scope decision can never depend on a check someone reorders later.
        raise MemoryScopeError(CODE_NOT_OWNER, "该记忆属于其他成员", status=403)

    if requested and requested != kind:
        declared = "私有智能体" if requested == SCOPE_PRIVATE_AGENT else "共享智能体"
        raise MemoryScopeError(CODE_AMBIGUOUS_TARGET,
                               "该智能体不是%s" % declared, status=400)
    return kind, agent_id


def _agent_binding(agent_id: str) -> Dict[str, Any]:
    """The Agent's binding row, or ``{}`` when the store cannot answer.

    A missing row is read as "not privately owned", the same rule
    ``web_channel._db_path_owner_forbidden`` already applies to this binding:
    ``_require_tenant_agent_binding`` refused every Agent that is not bound to
    the caller's tenant just above, so an absent row here can only be a store
    failure — and reading it as *not* privately owned never grants a read the
    owner check would have refused.
    """
    try:
        from auth.service import get_identity_service
        return get_identity_service().get_agent_binding(agent_id) or {}
    except MemoryScopeError:
        raise
    except Exception as error:  # noqa: BLE001 - unavailable is not "shared"
        logger.warning("[MemoryConsole] agent binding read failed: %s", error)
        raise unavailable(error) from error


def _status_of(error: "web.HTTPError") -> int:
    try:
        return int(str(error.args[0]).split()[0])
    except (IndexError, TypeError, ValueError):
        return 0


# --- delegation: list -------------------------------------------------------

def list_response(ctx, params) -> str:
    """The JSON body of ``GET /api/memory`` for the resolved target."""
    target = resolve_target(ctx, params)
    if target.scope == SCOPE_PERSONAL:
        service = _personal_service(ctx)
        payload = _personal_list(service, target.page, target.page_size)
        payload["read_only"] = True
    else:
        payload = _agent_list(ctx, target)
    return _envelope(target, payload)


def _personal_list(service, page: int, page_size: int) -> Dict[str, Any]:
    entries = service.list_entries()
    start = (page - 1) * page_size
    return {
        "page": page,
        "page_size": page_size,
        "total": len(entries),
        "list": [_personal_row(entry) for entry in entries[start:start + page_size]],
    }


def _personal_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One personal entry in the legacy row shape.

    ``filename`` is the entry id (``MEMORY.md`` / ``memory/<name>.md``) — the
    value the legacy viewer sends straight back as ``filename`` — and ``id``
    repeats it because that is the parameter ``/api/memory/personal`` takes.
    ``actions`` is reported empty: this surface serves no write verb, so it
    must not advertise one.
    """
    entry_id = str(entry.get("id") or "")
    return {
        "filename": entry_id,
        "type": "global" if entry_id == MAIN_ENTRY else "daily",
        "size": entry.get("size", 0),
        "updated_at": entry.get("updated_at", ""),
        "id": entry_id,
        "revision": entry.get("revision"),
        "actions": {"edit": False, "delete": False},
    }


def _agent_list(ctx, target: MemoryTarget) -> Dict[str, Any]:
    from agent.memory.service import MemoryService

    root = _agent_root(ctx, target.agent_id)
    for relative in _AGENT_LIST_DIRS.get(target.category, ()):
        _assert_plain_dir(root, relative)

    service = MemoryService(root)
    try:
        result = service.list_files(page=target.page, page_size=target.page_size,
                                    category=target.category)
    except (ValueError, FileNotFoundError) as error:
        raise MemoryScopeError(CODE_UNKNOWN_ENTRY, str(error), status=404) from error

    rows, dropped = _plain_rows(root, list(result.get("list") or []))
    result["list"] = rows
    if dropped:
        # A symlinked entry is invisible everywhere it is observable, so it is
        # removed from the page *and* from the total it was counted in.
        result["total"] = max(0, int(result.get("total", 0)) - dropped)
    return result


def _agent_root(ctx, agent_id: str) -> str:
    """The Agent memory root, as the delivered fork resolves it per request."""
    wc = _web_channel()
    return wc._get_workspace_root(agent_id=agent_id)


def _plain_rows(root: str, rows: List[Dict[str, Any]]):
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for row in rows:
        relative = _row_relative(row)
        if relative and _is_symlink(root, relative):
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


def _row_relative(row: Dict[str, Any]) -> str:
    name = str(row.get("filename") or "")
    if not name:
        return ""
    if row.get("type") == "global" or name == MAIN_ENTRY:
        return name
    sub = _ROW_SUBDIRS.get(str(row.get("type") or ""))
    return "%s/%s" % (sub, name) if sub else ""


# --- delegation: content ----------------------------------------------------

def content_response(ctx, params) -> str:
    """The JSON body of ``GET /api/memory/content`` for the resolved target."""
    target = resolve_target(ctx, params, entry=True)
    if target.scope == SCOPE_PERSONAL:
        service = _personal_service(ctx)
        read = service.read(target.entry)
        payload = {
            "filename": target.entry,
            # Relative to the *personal* root; the write address for this entry
            # is /api/memory/personal, which is why ``read_only`` travels with
            # the payload.
            "rel_path": target.entry,
            "content": read.get("content") or "",
            "id": target.entry,
            "revision": read.get("revision"),
            "read_only": True,
        }
    else:
        payload = _agent_content(ctx, target)
    return _envelope(target, payload)


def _envelope(target: MemoryTarget, payload: Dict[str, Any]) -> str:
    """The response envelope: ``status`` first, then the legacy fields.

    ``agent_id`` is omitted for the personal scope rather than sent as ``null``:
    the personal domain is not an Agent's, and echoing an empty target may read
    as "the caller's default Agent", which is a scope this surface does not have.
    """
    body: Dict[str, Any] = {"status": "success", "scope": target.scope}
    if target.agent_id:
        body["agent_id"] = target.agent_id
    body.update(payload)
    return json.dumps(body, ensure_ascii=False)


def _agent_content(ctx, target: MemoryTarget) -> Dict[str, Any]:
    from agent.memory.service import MemoryService

    root = _agent_root(ctx, target.agent_id)
    relative = _entry_relative(target.category, target.entry)
    if not _assert_plain_entry(root, relative):
        raise MemoryScopeError(CODE_UNKNOWN_ENTRY, "记忆条目不存在", status=404)

    service = MemoryService(root)
    try:
        return service.get_content(target.entry, category=target.category)
    except FileNotFoundError as error:
        raise MemoryScopeError(CODE_UNKNOWN_ENTRY, "记忆条目不存在",
                               status=404) from error
    except ValueError as error:
        raise MemoryScopeError(CODE_INVALID_ENTRY, str(error), status=400) from error


def _entry_relative(category: str, filename: str) -> str:
    """Where ``MemoryService._resolve_path`` will look for ``filename``.

    Kept in step with that method on purpose: the guard has to check the *same*
    path the delegate is about to read, or it guards nothing.
    """
    if filename == MAIN_ENTRY:
        return MAIN_ENTRY
    if category == CATEGORY_DREAM:
        return "memory/dreams/%s" % filename
    if category == CATEGORY_EVOLUTION:
        return "memory/evolution/%s" % filename
    return "memory/%s" % filename


# --- path guards ------------------------------------------------------------

def _assert_plain_dir(root: str, relative: str) -> None:
    """Refuse a directory whose path is (or crosses) a symlink.

    ``MemoryService`` enumerates with ``os.listdir``/``os.path.isfile``, which
    follow links: a ``memory`` symlink inside the tenant shared root would make
    the shared scope list — and, through ``get_content``, serve — another
    member's personal directory. Absence is not an error (a fresh scope has no
    ``memory/`` yet); a link is.
    """
    try:
        safe_fs.resolve_within(root, relative)
    except safe_fs.UnsafePathError as error:
        raise _unsafe(error) from error
    except (FileNotFoundError, NotADirectoryError, OSError):
        return


def _assert_plain_entry(root: str, relative: str) -> bool:
    """``False`` when the entry is absent; refuse a symlinked entry or parent."""
    try:
        if safe_fs.is_symlink(root, relative):
            raise _unsafe("symbolic link entry %r" % relative)
        return safe_fs.is_file(root, relative)
    except safe_fs.UnsafePathError as error:
        raise _unsafe(error) from error


def _is_symlink(root: str, relative: str) -> bool:
    try:
        return safe_fs.is_symlink(root, relative)
    except safe_fs.UnsafePathError as error:
        raise _unsafe(error) from error


def _unsafe(error) -> MemoryScopeError:
    return MemoryScopeError(CODE_UNSAFE_PATH, "记忆路径被替换，已拒绝访问", status=403)


# --- the personal service, bound to the verified context ---------------------

def _personal_service(ctx):
    """The *delivered* personal memory service for this request's identity.

    Same binding the ``/api/memory/personal`` handlers use: ownership comes
    from ``ctx``, so there is no parameter a caller could set to name another
    user.
    """
    return _web_channel()._personal_memory_service(ctx)


def refusal_for(exc: BaseException) -> MemoryScopeError:
    """Translate a delegate failure into a :class:`MemoryScopeError`."""
    from agent.memory.personal import PersonalMemoryError
    if isinstance(exc, PersonalMemoryError):
        return MemoryScopeError(exc.code, str(exc), status=exc.status)
    return unavailable(exc)
