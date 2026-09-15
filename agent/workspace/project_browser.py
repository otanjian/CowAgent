# encoding:utf-8
"""Scoped project browser: a member browses and selects directories strictly
inside their own private project root.

Why this module exists
----------------------
``GET /api/projects/browse`` used to be a *global filesystem* walker. It took
the ``path`` query parameter, resolved it with ``os.path.realpath`` and listed
whatever came back, defaulting to the operator's home directory. That was
tolerable while one operator drove one console. Under database identity mode it
is not: every tenant and every member share one server, so the same code let any
signed-in member enumerate the host -- other tenants' shared assets, other
members' ``users/<id>`` trees, the service's own data directory -- and there is
no containment check that can be bolted onto "list this path" after the fact.

The interim fix closed the whole picker in database mode
(``channel.web.web_channel._guard_not_database``), even though creating a
project and binding a session to one inside the member's own root already
worked. Members could create a project but never re-open one. That is the
failure this module removes: the capability comes back *scoped to the member's
own root*, instead of being either globally closed or globally open.

What it guarantees
------------------
1. **The root comes from the identity.** :func:`trusted_root` resolves the
   caller's private projects root (``state_dir.user_root(ident)/projects``, the
   layout ``project_store.user_projects_root()`` already owns). An identity
   without a verified ``user_id`` is refused outright (``no_identity``), never
   silently rewritten into a global path.
2. **Every access is anchored.** Reads, listings and writes inside the root go
   through ``common.safe_fs``, which resolves each component relative to an
   already-open directory descriptor with ``O_NOFOLLOW``. ``..``, absolute
   paths, drive letters, symlinked components (including a symlinked root) and a
   component swapped for a link between validation and use are refused rather
   than resolved. This module deliberately does not roll its own
   ``realpath`` + ``startswith`` check: that idiom answers "inside right now"
   and then hands the *string* back to the filesystem.
3. **Identifiers are relative.** :func:`browse` keeps the response shape the
   existing console picker consumes (``{status, path, parent, dirs}``, ``dirs``
   entries as ``{"name", "path"}``), but ``path`` is a *relative identifier*
   and never a host path. A relative id that tries to escape is refused, so a
   response cannot be used to probe the filesystem.
4. **Legacy values are translated in exactly one place.**
   :func:`normalize_selection` is the only function that accepts a stored
   absolute path (from before this change) and turns it into the relative
   identifier; it does so only after proving the path is inside the member's own
   current root, and refuses anything else with a stable code instead of
   dropping or rewriting it.
5. **Selection re-validates; a browse result is not an authorization.**
   :func:`resolve_selection` re-checks the session's owner, the recorded
   tenant/user and the real path *at selection time*, so a directory replaced by
   a symlink after browsing refuses the selection instead of binding the session
   to the new target.
6. **Import is controlled, previewed and atomic.** :func:`preview_import` and
   :func:`import_directory` copy a selected directory into the member's project
   area through a staging directory in the same root, publish with a single
   ``os.rename``, never overwrite an existing project, and roll the staging
   directory back on cancel, failure or a refused quota reservation.

Platform/tenant-root nesting
----------------------------
``state_dir.user_root`` puts every member's private tree under one
``<shared_root>/users/<user_id>`` directory, so the tenant shared root *contains*
private trees while a private tree never contains the shared root. When the root
being browsed is such an outer root, this module (a) refuses any relative
identifier that descends into another member's private tree and (b) omits those
trees from the listing as well. It refuses rather than hiding-and-continuing: the
``users/`` container itself is refused too, because its only entries are private
trees. A root that is itself *inside* another member's private tree is refused
outright (:func:`trusted_root`).

Import boundary
---------------
The desktop native picker hands the server an absolute host path. This module
treats it as untrusted input, resolves it and requires the *resolved* path to be
inside an allowed source root: by default the member's own trusted root, or one
of the extra roots the caller passes through ``source_roots`` (which MUST come
from an already-verified policy, never from the request). A link out of the
allowed roots therefore fails containment instead of smuggling a copy.

A *remote* backend never sees a client path at all: :func:`preview_upload` and
:func:`import_upload` take the tree's bytes as a manifest and have no parameter
that would accept a server path, so "the browser named a path" cannot become
"the server read it". The caller decides which transport a request may use (the
Web seam requires loopback + the per-start token for the path transport and the
session alone for the upload), and this module keeps both on the same staging,
publish, rollback and quota code.

Quota is injected. ``quota_reserve`` is called once, before anything is staged,
with the plan; raising aborts with ``quota_refused`` and creates nothing. The
reservation object it returns may expose ``commit()`` (called after the
published project is in place) and ``release()`` (called when the import rolls
back); neither is required. Concurrency ("two imports race for the last of the
quota") is the injected service's decision: it must make the reservation atomic
per member, because this module can only guarantee that it never publishes
without one.
"""

from __future__ import annotations

import os
import re
import shutil
import stat as _stat
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from common import safe_fs
from common.log import logger
from common.safe_fs import UnsafePathError

__all__ = [
    "ProjectBrowserError",
    "ImportCancelled",
    "CODE_NO_IDENTITY",
    "CODE_ROOT_UNAVAILABLE",
    "CODE_UNSAFE_PATH",
    "CODE_OUTSIDE_ROOT",
    "CODE_NOT_FOUND",
    "CODE_NOT_OWNER",
    "CODE_STALE_SELECTION",
    "CODE_INVALID_REQUEST",
    "CODE_INVALID_NAME",
    "CODE_CONFLICT",
    "CODE_QUOTA_REFUSED",
    "CODE_CANCELLED",
    "CODE_IMPORT_FAILED",
    "trusted_root",
    "browse",
    "breadcrumbs",
    "normalize_selection",
    "resolve_selection",
    "preview_import",
    "import_directory",
    "preview_upload",
    "import_upload",
]

# --- refusal codes -----------------------------------------------------------
#
# Stable, machine-readable reasons. The console branches on these, and tests
# pin them, so they are constants rather than inline strings.

#: No verified ``user_id``: the scoped browser has no scope to work in. Legacy
#: (non-database) mode reports this instead of falling back to a host path.
CODE_NO_IDENTITY = "no_identity"
#: The identity is usable but its private root cannot be resolved (e.g. the
#: tenant has no trusted shared root). Reported as unavailable, not as empty.
CODE_ROOT_UNAVAILABLE = "root_unavailable"
#: The identifier is not a plain downward path inside the root, or a component
#: is (or became) a symlink. Never resolved and never partially followed.
CODE_UNSAFE_PATH = "unsafe_path"
#: A well-formed absolute path that is not inside the member's own root.
CODE_OUTSIDE_ROOT = "outside_root"
#: The identifier names nothing inside the root.
CODE_NOT_FOUND = "not_found"
#: The session being bound is owned by another member.
CODE_NOT_OWNER = "not_owner"
#: The value was recorded for a different tenant/user than the current one.
CODE_STALE_SELECTION = "stale_selection"
#: Bad caller input (missing required seam, non-absolute import source, ...).
CODE_INVALID_REQUEST = "invalid_request"
#: The requested project name is not a single visible folder name.
CODE_INVALID_NAME = "invalid_name"
#: An entry with that name already exists; imports never overwrite.
CODE_CONFLICT = "conflict"
#: The injected quota service refused the reservation.
CODE_QUOTA_REFUSED = "quota_refused"
#: A ``stage_hook`` cancelled the import before publish.
CODE_CANCELLED = "cancelled"
#: Copying or publishing failed; nothing was published.
CODE_IMPORT_FAILED = "import_failed"

#: The directory ``state_dir.user_root`` builds private trees in
#: (``<shared_root>/users/<user_id>``). Used to recognise another member's tree.
_USERS_LAYOUT = "users"

#: Staging directories are hidden, so ``browse`` never offers a half-imported
#: project and ``_target_name`` can never collide with one.
_STAGING_PREFIX = ".staging-"

#: A Windows drive designator. Refused on every platform: the picker's relative
#: identifiers are never drive-qualified, so accepting one could only widen the
#: reachable set (the reason the legacy handler's ``__DRIVES__`` sentinel is not
#: honoured here).
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: The label of the first breadcrumb: the member's own projects root.
_ROOT_LABEL = "/"

# Mirrors ``common.safe_fs``: descriptor-relative access where the platform
# supports it, validated-path access (with link checks) where it does not.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FD_OK = bool(
    _O_NOFOLLOW
    and _O_DIRECTORY
    and os.open in getattr(os, "supports_dir_fd", set())
    and os.stat in getattr(os, "supports_dir_fd", set())
)

_CHUNK = 1 << 20


class ProjectBrowserError(Exception):
    """A refused project-browser operation.

    ``code`` is the machine-readable reason the handler maps to a response and
    the console branches on; ``status`` is the HTTP status it should answer
    with. Messages name the offending *identifier*, never a resolved host path,
    so a refusal cannot be used to probe the filesystem.
    """

    def __init__(self, message: str, *, code: str = "error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class ImportCancelled(Exception):
    """Raised by a ``stage_hook`` to abandon an import before it is published.

    A ``stage_hook`` may raise this when the user cancels; any other exception
    from the hook is treated as a failure. Either way the staging directory is
    removed, so no half-imported project is left behind.
    """


def _refuse(message: str, code: str = CODE_UNSAFE_PATH,
            status: int = 403) -> ProjectBrowserError:
    return ProjectBrowserError(message, code=code, status=status)


# --- identity / root resolution ---------------------------------------------


def _require_identity(identity=None):
    """The verified identity this call acts for, or a refusal.

    ``None`` means "the ambient runtime identity". A missing ``user_id`` is the
    legacy/non-database signal: there is no private root to scope to, and
    guessing a global one is exactly the behaviour this module exists to remove.
    """
    from common.runtime_identity import current_identity

    ident = identity if identity is not None else current_identity()
    user_id = getattr(ident, "user_id", None) if ident is not None else None
    if not user_id:
        raise ProjectBrowserError(
            "项目浏览仅在数据库身份模式下可用（缺少已验证的用户身份）",
            code=CODE_NO_IDENTITY, status=403)
    # The user id becomes a path component under ``users/``. A value carrying a
    # separator could name a path outside the member's own tree, so an
    # unverifiable shape is refused rather than interpolated.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(user_id)):
        raise ProjectBrowserError(
            "无法验证当前用户标识，已拒绝项目浏览",
            code=CODE_NO_IDENTITY, status=403)
    return ident


def _layout_dir_on_path(path: str, user_id: str) -> Optional[str]:
    """The ``users/`` layout directory ``path`` sits under, when it is confirmed.

    Confirmed means the directory actually holds this member's own tree
    (``<users>/<user_id>``). That is what distinguishes the real layout from a
    project that happens to contain a folder named ``users`` -- mistaking the
    latter for the layout would refuse legitimate browsing.
    """
    parts = str(path).split(os.sep)
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] != _USERS_LAYOUT:
            continue
        layout = os.sep.join(parts[:index + 1]) or os.sep
        if os.path.isdir(layout) and os.path.isdir(os.path.join(layout, user_id)):
            return layout
    return None


def _foreign_tree_of(path: str, user_id: str) -> Optional[str]:
    """``path`` when it lies in *another* member's private tree, else ``None``.

    Uses the confirmed layout directory (see :func:`_layout_dir_on_path`). When
    no layout is confirmed this returns ``None``: an ordinary folder named
    ``users`` inside a project is not another member's private data.
    """
    layout = _layout_dir_on_path(path, user_id)
    if layout is None:
        return None
    if safe_fs.contains(os.path.join(layout, user_id), path):
        return None
    return str(path)


def trusted_root(identity=None, *, root: Optional[str] = None) -> str:
    """The member's private projects root, symlink-resolved.

    ``root`` is an *internal* seam for tests and for an embedder that already
    resolved the member's root from a verified identity. It is never a request
    value: it is the trusted root, so passing anything a client influenced would
    hand away exactly the containment this module provides.

    Raises :class:`ProjectBrowserError` with ``no_identity`` when there is no
    verified member, ``root_unavailable`` when the identity's root cannot be
    resolved, and ``unsafe_path`` when ``root`` is a symlink or lies inside
    another member's private tree.
    """
    ident = _require_identity(identity)
    user_id = str(ident.user_id)

    if root is not None:
        text = str(root)
        if not os.path.isabs(text):
            raise _refuse("项目根必须是绝对路径", CODE_INVALID_REQUEST, 400)
        if os.path.islink(text):
            raise _refuse("项目根目录是符号链接，已拒绝", CODE_UNSAFE_PATH, 403)
        real = os.path.realpath(text)
        if _foreign_tree_of(real, user_id) is not None:
            raise _refuse("项目根目录位于其他成员的私有范围内，已拒绝",
                          CODE_UNSAFE_PATH, 403)
        return real

    from common.runtime_identity import current_identity
    from agent.workspace import project_store

    try:
        resolved = None
        if ident == current_identity():
            # Compose with the store's resolver rather than re-deriving the
            # layout: it is the single place that knows where projects live.
            resolved = project_store.user_projects_root()
        if resolved:
            path = resolved
        else:
            from common import state_dir
            path = os.path.realpath(str(state_dir.user_root(ident) / "projects"))
    except ProjectBrowserError:
        raise
    except Exception as e:  # noqa: BLE001 - fail closed, never guess a root
        raise ProjectBrowserError(
            "无法解析当前成员的项目根目录",
            code=CODE_ROOT_UNAVAILABLE, status=503) from e
    if _foreign_tree_of(path, user_id) is not None:
        raise _refuse("项目根目录位于其他成员的私有范围内，已拒绝",
                      CODE_UNSAFE_PATH, 403)
    return path


# --- relative identifiers ----------------------------------------------------


def _clean_relative(value: Any) -> str:
    """Validate a relative identifier, returning its canonical form (``""`` = root).

    Refusals happen *before* any syscall and are textual on purpose. Only the
    downward grammar is accepted: no ``..``, no ``.``, no empty component, no
    absolute path, no backslash, no drive designator.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _refuse("路径标识必须是字符串", CODE_UNSAFE_PATH, 403)
    text = value.strip()
    if text in ("", ".", "./"):
        return ""
    if "\\" in text:
        raise _refuse("路径标识不能包含反斜杠", CODE_UNSAFE_PATH, 403)
    if _DRIVE_RE.match(text):
        raise _refuse("路径标识不能包含盘符", CODE_UNSAFE_PATH, 403)
    try:
        parts = safe_fs.split_relative(text)
    except UnsafePathError as e:
        raise _refuse("路径标识不是本人项目根内的相对路径",
                      CODE_UNSAFE_PATH, 403) from e
    return "/".join(parts)


def _parent_id(relative: str) -> Optional[str]:
    """The identifier of the parent directory; ``None`` at the root."""
    if not relative:
        return None
    head, _sep, _tail = relative.rpartition("/")
    return head


def _absolute_of(root: str, relative: str) -> str:
    return os.path.join(root, *relative.split("/")) if relative else root


def _assert_scoped(ident, root: str, relative: str) -> None:
    """Refuse an identifier that is not a plain directory inside ``root``.

    ``safe_fs.resolve_within`` is the seam: it link-checks every component,
    requires presence, and re-checks containment, so a symlinked component (or
    the final entry itself) is an :class:`UnsafePathError` rather than a
    redirect. The nesting guard is applied afterwards, because "exists inside the
    root" and "is allowed to be visited" are different questions.
    """
    try:
        safe_fs.resolve_within(root, relative)
    except UnsafePathError as e:
        raise _refuse("路径标识包含软链接或非法分量，已拒绝",
                      CODE_UNSAFE_PATH, 403) from e
    except FileNotFoundError as e:
        raise _refuse("目录不存在", CODE_NOT_FOUND, 404) from e
    except NotADirectoryError as e:
        raise _refuse("目录不存在", CODE_NOT_FOUND, 404) from e
    if _is_foreign(ident, root, relative):
        raise _refuse("该目录位于其他成员的私有范围内，已拒绝",
                      CODE_UNSAFE_PATH, 403)


def _is_foreign(ident, root: str, relative: str) -> bool:
    """True when ``relative`` under ``root`` is another member's private tree."""
    return _foreign_tree_of(_absolute_of(root, relative),
                            str(ident.user_id)) is not None


# --- browsing ----------------------------------------------------------------


def browse(identity=None, relative: Any = "", *,
           root: Optional[str] = None) -> Dict[str, Any]:
    """List the directories inside the member's own root.

    Returns the shape the console picker already consumes --
    ``{"status": "success", "path": <relative id>, "parent": <relative id or
    None>, "dirs": [{"name", "path"}]}`` -- with relative identifiers in place
    of host paths, hidden entries and symlinked entries skipped, and the result
    sorted case-insensitively. ``path`` is ``""`` at the root and ``parent`` is
    ``None`` there.

    A missing root is not an error: a fresh member has no ``projects/`` yet and
    an empty list is the honest answer. A relative identifier that escapes, is
    not a directory, or descends into another member's private tree is refused.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    entry = _clean_relative(relative)
    if entry:
        _assert_scoped(ident, trusted, entry)

    try:
        names = safe_fs.list_names(trusted, entry or None, skip_dotfiles=True)
        dirs: List[Dict[str, str]] = []
        for name in names:
            child = _join(entry, name)
            if _is_foreign(ident, trusted, child):
                # Another member's private tree: not offered, and not descended
                # into. Omitting the entry (rather than listing it and refusing on
                # click) is what keeps the enumeration from disclosing whose trees
                # exist under a shared root.
                continue
            if safe_fs.is_dir(trusted, child):
                dirs.append({"name": name, "path": child})
    except UnsafePathError as e:
        # A component swapped for a link after validation: the listing refuses
        # instead of enumerating whatever it now points at.
        raise _refuse("目录已被替换，已拒绝浏览", CODE_UNSAFE_PATH, 403) from e
    dirs.sort(key=lambda item: (item["name"].lower(), item["name"]))
    return {"status": "success", "path": entry, "parent": _parent_id(entry),
            "dirs": dirs, "breadcrumbs": breadcrumbs(entry)}


def _join(relative: str, name: str) -> str:
    return f"{relative}/{name}" if relative else name


def breadcrumbs(relative: Any = "") -> List[Dict[str, str]]:
    """The bounded path from the projects root down to ``relative``.

    Every entry carries a *relative* identifier (``""`` is the root itself), so
    a breadcrumb can be sent back as the ``path`` parameter but never names a
    host path. The first crumb is the root, and the list is bounded by the
    identifier's own depth -- there is no way to ask for the parent of the root,
    which is why the console cannot climb out of the member's scope.
    """
    entry = _clean_relative(relative)
    crumbs: List[Dict[str, str]] = [{"name": _ROOT_LABEL, "path": ""}]
    if not entry:
        return crumbs
    parts = entry.split("/")
    for index, part in enumerate(parts):
        crumbs.append({"name": part, "path": "/".join(parts[:index + 1])})
    return crumbs


# --- selection ---------------------------------------------------------------


def normalize_selection(identity, value: Any, *,
                        root: Optional[str] = None) -> str:
    """Translate any stored selection value into its relative identifier.

    This is the *single* place legacy values are interpreted. A value may be:

    * a relative identifier (``""`` = the root itself) as returned by
      :func:`browse`;
    * an absolute path recorded before this change, which is accepted only when
      it resolves inside the member's own current root.

    Anything else -- a path outside the root, a path that no longer exists, a
    component that has since become a symlink -- is refused with a stable code.
    Nothing is silently dropped or rewritten into the root.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _refuse("项目标识必须是字符串", CODE_UNSAFE_PATH, 403)
    text = value.strip()
    if not text:
        return ""
    if text.startswith(("/", "\\")) or os.path.isabs(text):
        return _relative_from_absolute(ident, trusted, text)
    entry = _clean_relative(text)
    if entry:
        _assert_scoped(ident, trusted, entry)
    return entry


def _relative_from_absolute(ident, trusted: str, value: str) -> str:
    """Map an absolute path inside the member's root onto a relative identifier."""
    real = os.path.realpath(value)
    if not safe_fs.contains(trusted, real):
        raise _refuse("该路径不在本人项目根内，已拒绝",
                      CODE_OUTSIDE_ROOT, 403)
    relative = os.path.relpath(real, trusted).replace(os.sep, "/")
    if relative in (".", ""):
        return ""
    entry = _clean_relative(relative)
    # The legacy value is not trusted just because it was stored: the chain is
    # re-validated through the same anchored seam browsing uses, so a component
    # replaced by a symlink in the meantime refuses the value.
    _assert_scoped(ident, trusted, entry)
    return entry


def resolve_selection(identity, value: Any, *, root: Optional[str] = None,
                      recorded: Any = None, session_id: Optional[str] = None,
                      session_owner: Optional[Callable[[str], Optional[str]]] = None
                      ) -> str:
    """Re-validate a project selection and return the absolute path to bind.

    A previously browsed identifier is not an authorization, so this re-checks,
    at selection time:

    * the session's durable owner (``session_owner``, the injected equivalent of
      the web layer's ``_require_owned_session``) still names the caller;
    * the value was recorded for the *current* tenant/user (``recorded``: an
      identity-like object, a mapping, or a ``(tenant_id, user_id)`` pair);
    * the real path still resolves inside the member's own root through
      ``safe_fs`` -- a directory swapped for a symlink after browsing is refused
      here rather than followed.

    Returns the absolute, symlink-resolved path that
    ``project_store.set_project_dir`` stores (``global::``/``agent::`` scoping is
    the store's concern, not this module's).

    Passing ``session_id`` makes ``session_owner`` required: an ownership check
    that is merely assumed is the bug this parameter exists to prevent.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    _assert_recorded_scope(ident, recorded)
    if session_id:
        if session_owner is None:
            raise _refuse("选择项目需要重新校验会话归属（缺少归属校验接缝）",
                          CODE_INVALID_REQUEST, 400)
        try:
            owner = session_owner(session_id)
        except Exception as e:  # noqa: BLE001 - fail closed
            raise _refuse("无法校验会话归属，已拒绝选择",
                          CODE_NOT_OWNER, 403) from e
        if str(owner or "") != str(ident.user_id):
            raise _refuse("该会话不属于当前成员", CODE_NOT_OWNER, 403)
    entry = normalize_selection(ident, value, root=trusted)
    if not entry:
        return trusted
    try:
        return safe_fs.resolve_within(trusted, entry)
    except UnsafePathError as e:
        raise _refuse("项目目录已被替换，已拒绝选择",
                      CODE_UNSAFE_PATH, 403) from e
    except FileNotFoundError as e:
        raise _refuse("项目目录不存在", CODE_NOT_FOUND, 404) from e


def _recorded_scope(recorded: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(tenant_id, user_id)`` from an identity, a mapping or a pair."""
    if isinstance(recorded, Mapping):
        return (recorded.get("tenant_id"), recorded.get("user_id"))
    if isinstance(recorded, (tuple, list)) and len(recorded) == 2:
        return (recorded[0], recorded[1])
    return (getattr(recorded, "tenant_id", None),
            getattr(recorded, "user_id", None))


def _assert_recorded_scope(ident, recorded: Any) -> None:
    """Refuse when the value was recorded for a different tenant/user."""
    if recorded is None:
        return
    tenant_id, user_id = _recorded_scope(recorded)
    if user_id and str(user_id) != str(ident.user_id):
        raise _refuse("该项目是在其他成员的身份下记录的，已拒绝",
                      CODE_STALE_SELECTION, 403)
    # Same account in another tenant is still a different scope: the shared root
    # differs, so the identifier must not be replayed.
    if tenant_id is not None and str(tenant_id or "") != str(
            getattr(ident, "tenant_id", None) or ""):
        raise _refuse("该项目是在其他租户下记录的，已拒绝",
                      CODE_STALE_SELECTION, 403)


# --- controlled import -------------------------------------------------------


def preview_import(identity, source: Any, *, name: Optional[str] = None,
                   root: Optional[str] = None,
                   source_roots: Optional[Sequence[str]] = None
                   ) -> Dict[str, Any]:
    """Describe what importing ``source`` would create. Read-only.

    Reports the target name and identifier, the entry/file/directory counts and
    the byte total, the entries that would be skipped (symlinks and special
    files), and a conflict report when something already exists under the target
    name. It creates nothing and reserves no quota.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    anchor, source_rel = _source_scope(ident, trusted, source, source_roots)
    plan = _scan_source(anchor, source_rel)
    target_name = _target_name(name, source_rel)
    conflict = _conflict_of(trusted, target_name)
    return {
        "status": "success",
        "name": target_name,
        "target": target_name,
        "source_name": source_rel.rsplit("/", 1)[-1],
        "files": plan["files"],
        "dirs": plan["dirs"],
        "entries": plan["files"] + plan["dirs"],
        "bytes": plan["bytes"],
        "excluded": plan["excluded"],
        "conflict": conflict,
        "ready": conflict is None,
    }


def import_directory(identity, source: Any, *, name: Optional[str] = None,
                     root: Optional[str] = None,
                     source_roots: Optional[Sequence[str]] = None,
                     quota_reserve: Optional[Callable[[Dict[str, Any]], Any]] = None,
                     stage_hook: Optional[Callable[[Dict[str, Any]], Any]] = None
                     ) -> Dict[str, Any]:
    """Copy ``source`` into the member's project area as a new project.

    The copy is staged in a hidden directory inside the same root and published
    with one ``os.rename``, so:

    * a cancel or a failure leaves no half-imported project (the staging
      directory is removed, as is the empty name placeholder if one was already
      claimed);
    * an existing project directory is never overwritten -- the target name is
      claimed with an exclusive ``os.mkdir``, which reports ``conflict`` instead
      of replacing content;
    * only a published project counts as success.

    ``stage_hook`` is called once after the copy is complete and before publish,
    with the plan (counts, target, staging path); it may raise
    :class:`ImportCancelled` to cancel, and its only other effect should be
    reporting/auditing. ``quota_reserve`` is called once *before* anything is
    staged, with the plan; raising aborts with ``quota_refused`` and creates
    nothing.

    Precondition: this is the *local* trusted-backend path. A remote backend
    cannot see a browser's host path at all and must use a protected upload
    transport instead of calling this function.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    anchor, source_rel = _source_scope(ident, trusted, source, source_roots)
    target_name = _target_name(name, source_rel)
    target_abs = os.path.join(trusted, target_name)
    source_abs = _absolute_of(anchor, source_rel)
    if safe_fs.contains(source_abs, target_abs) or safe_fs.contains(
            target_abs, source_abs):
        raise _refuse("导入目标与源目录相互嵌套，已拒绝",
                      CODE_INVALID_REQUEST, 400)

    # The preview the caller may have shown is not an authorization, and the
    # source can change between the two calls, so the plan is recomputed here
    # and the counts returned are the ones actually copied.
    plan = _scan_source(anchor, source_rel)
    conflict = _conflict_of(trusted, target_name)
    if conflict is not None:
        raise _refuse("同名项目已存在，不会覆盖", CODE_CONFLICT, 409)

    reservation = _reserve(quota_reserve, {**plan, "name": target_name,
                                           "target": target_name})
    staging: Optional[str] = None
    try:
        _ensure_root(trusted)
        staging = tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=trusted)
        staging_rel = os.path.basename(staging)
        copied = _copy_tree(anchor, source_rel, trusted, staging_rel)
        if stage_hook is not None:
            stage_hook({
                **copied,
                "name": target_name,
                "target": target_name,
                "staging": staging,
            })
        _publish(staging, target_abs)
        staging = None
        path = _published_path(trusted, target_name)
    except ImportCancelled as e:
        _rollback(staging)
        _release(reservation)
        raise ProjectBrowserError("导入已取消，未发布任何项目",
                                  code=CODE_CANCELLED, status=409) from e
    except ProjectBrowserError:
        _rollback(staging)
        _release(reservation)
        raise
    except BaseException as e:
        _rollback(staging)
        _release(reservation)
        raise ProjectBrowserError("导入失败，未发布任何项目",
                                  code=CODE_IMPORT_FAILED, status=500) from e

    quota_state = _commit(reservation)
    logger.info("[ProjectBrowser] imported project %r (%s files, %s bytes)",
                target_name, copied["files"], copied["bytes"])
    return {
        "status": "success",
        "name": target_name,
        "target": target_name,
        "path": path,
        "source_name": source_rel.rsplit("/", 1)[-1],
        "files": copied["files"],
        "dirs": copied["dirs"],
        "entries": copied["files"] + copied["dirs"],
        "bytes": copied["bytes"],
        "excluded": copied["excluded"],
        "quota_state": quota_state,
    }


def preview_upload(identity, entries, *, name: Optional[str] = None,
                   root: Optional[str] = None) -> Dict[str, Any]:
    """Describe what uploading ``entries`` would publish. Read-only.

    The remote transport's half of :func:`preview_import`: a browser that cannot
    see a server path uploads the tree it wants, so the plan is computed from the
    manifest (``(relative_path, data)`` pairs) instead of from a host directory.
    Nothing is staged and no quota is reserved.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    cleaned = _manifest_entries(entries)
    plan = _upload_plan(cleaned)
    target_name = _upload_target_name(name, cleaned)
    conflict = _conflict_of(trusted, target_name)
    return {
        "status": "success",
        "name": target_name,
        "target": target_name,
        "files": plan["files"],
        "dirs": plan["dirs"],
        "entries": plan["files"] + plan["dirs"],
        "bytes": plan["bytes"],
        "excluded": [],
        "conflict": conflict,
        "ready": conflict is None,
    }


def import_upload(identity, entries, *, name: Optional[str] = None,
                  root: Optional[str] = None,
                  quota_reserve: Optional[Callable[[Dict[str, Any]], Any]] = None,
                  stage_hook: Optional[Callable[[Dict[str, Any]], Any]] = None
                  ) -> Dict[str, Any]:
    """Publish an uploaded tree as a new project (the remote transport).

    Same guarantees as :func:`import_directory` -- staged inside the member's own
    root, published with one ``os.rename``, never overwriting an existing
    project, quota reserved first, rolled back on cancel or failure -- but the
    content arrives as ``entries`` (``(relative_path, data)``) instead of being
    read from a path the *client* named. A remote browser has no server path to
    name, and this function has no parameter that would accept one: the boundary
    is the upload itself, plus the session that carried it.
    """
    ident = _require_identity(identity)
    trusted = trusted_root(ident, root=root)
    cleaned = _manifest_entries(entries)
    plan = _upload_plan(cleaned)
    target_name = _upload_target_name(name, cleaned)
    target_abs = os.path.join(trusted, target_name)
    conflict = _conflict_of(trusted, target_name)
    if conflict is not None:
        raise _refuse("同名项目已存在，不会覆盖", CODE_CONFLICT, 409)

    reservation = _reserve(quota_reserve, {**plan, "name": target_name,
                                           "target": target_name})
    staging: Optional[str] = None
    try:
        _ensure_root(trusted)
        staging = tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=trusted)
        staging_rel = os.path.basename(staging)
        for relative, data in cleaned:
            # ``safe_fs`` creates the missing parents inside the staging tree and
            # refuses a symlinked component, so an uploaded entry cannot write
            # through a link someone planted in the meantime.
            safe_fs.write_bytes_atomic(trusted, _join(staging_rel, relative),
                                       data)
        if stage_hook is not None:
            stage_hook({**plan, "name": target_name, "target": target_name,
                        "staging": staging})
        _publish(staging, target_abs)
        staging = None
        path = _published_path(trusted, target_name)
    except ImportCancelled as e:
        _rollback(staging)
        _release(reservation)
        raise ProjectBrowserError("导入已取消，未发布任何项目",
                                  code=CODE_CANCELLED, status=409) from e
    except ProjectBrowserError:
        _rollback(staging)
        _release(reservation)
        raise
    except BaseException as e:
        _rollback(staging)
        _release(reservation)
        raise ProjectBrowserError("导入失败，未发布任何项目",
                                  code=CODE_IMPORT_FAILED, status=500) from e

    quota_state = _commit(reservation)
    logger.info("[ProjectBrowser] imported uploaded project %r (%s files, %s bytes)",
                target_name, plan["files"], plan["bytes"])
    return {
        "status": "success",
        "name": target_name,
        "target": target_name,
        "path": path,
        "files": plan["files"],
        "dirs": plan["dirs"],
        "entries": plan["files"] + plan["dirs"],
        "bytes": plan["bytes"],
        "excluded": [],
        "quota_state": quota_state,
    }


def _manifest_entries(entries: Any) -> List[Tuple[str, bytes]]:
    """Validate an upload manifest into ``(relative_path, data)`` pairs.

    The client controls both halves, so both are checked *before* anything is
    staged: a path must be a plain downward relative path (``safe_fs`` refuses
    ``..``, absolute paths, drive letters and empty components textually, before
    any syscall), and the payload must be bytes. Duplicate names are refused
    rather than merged, because "which copy won" is not a question a member
    should have to ask about their own import.
    """
    if not entries:
        raise _refuse("上传目录为空，已拒绝", CODE_INVALID_REQUEST, 400)
    cleaned: List[Tuple[str, bytes]] = []
    seen = set()
    for item in entries:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise _refuse("上传清单条目无效，已拒绝", CODE_INVALID_REQUEST, 400)
        raw_path, data = item
        if isinstance(data, bytearray):
            data = bytes(data)
        if not isinstance(data, bytes):
            raise _refuse("上传内容必须是字节，已拒绝", CODE_INVALID_REQUEST, 400)
        try:
            parts = safe_fs.split_relative(str(raw_path or ""))
        except UnsafePathError as e:
            raise _refuse("上传清单包含非法路径，已拒绝",
                          CODE_UNSAFE_PATH, 403) from e
        relative = "/".join(parts)
        if relative in seen:
            raise _refuse("上传清单包含重复路径，已拒绝",
                          CODE_INVALID_REQUEST, 400)
        seen.add(relative)
        cleaned.append((relative, data))
    return cleaned


def _upload_plan(entries: Sequence[Tuple[str, bytes]]) -> Dict[str, Any]:
    """Count files, directories and bytes of an upload manifest."""
    dirs = set()
    total = 0
    for relative, data in entries:
        total += len(data)
        parts = relative.split("/")[:-1]
        for index in range(len(parts)):
            dirs.add("/".join(parts[:index + 1]))
    return {"files": len(entries), "dirs": len(dirs), "bytes": total}


def _upload_target_name(name: Optional[str],
                        entries: Sequence[Tuple[str, bytes]]) -> str:
    """The project name an upload publishes under.

    An explicit name wins (validated like any other target). Otherwise the
    manifest must agree on a single top-level folder, which is then the name --
    the same rule the console's existing directory upload uses. A manifest with
    several top-level entries has no honest name to infer, so it must name one.
    """
    if name is not None and str(name).strip():
        return _target_name(name, "")
    roots = sorted({relative.split("/", 1)[0] for relative, _data in entries})
    if len(roots) == 1:
        return _target_name(roots[0], "")
    raise _refuse("上传导入必须给出项目名称，已拒绝", CODE_INVALID_NAME, 400)


def _source_scope(ident, trusted: str, source: Any,
                  source_roots: Optional[Sequence[str]]
                  ) -> Tuple[str, str]:
    """``(anchor root, relative id)`` for the directory being imported.

    The picker's absolute path is untrusted input: it is resolved, then required
    to land inside the member's own root or one of the explicitly allowed extra
    roots. Resolution is what makes a link out of those roots fail containment
    instead of smuggling a copy.
    """
    if not isinstance(source, str) or not source.strip():
        raise _refuse("导入源必须是绝对路径", CODE_INVALID_REQUEST, 400)
    text = source.strip()
    if not (text.startswith("/") or text.startswith("\\")
            or os.path.isabs(text)):
        raise _refuse("导入源必须是绝对路径", CODE_INVALID_REQUEST, 400)

    real = os.path.realpath(text)
    anchors: List[str] = [trusted]
    for extra in source_roots or ():
        if not isinstance(extra, str) or not os.path.isabs(extra):
            raise _refuse("允许的导入源根必须是绝对路径",
                          CODE_INVALID_REQUEST, 400)
        resolved = os.path.realpath(extra)
        if resolved not in anchors:
            anchors.append(resolved)

    anchor = next((item for item in anchors
                   if safe_fs.contains(item, real)), None)
    if anchor is None:
        raise _refuse("导入源不在本人项目根内，已拒绝",
                      CODE_OUTSIDE_ROOT, 403)
    user_id = str(ident.user_id)
    if _foreign_tree_of(real, user_id) is not None:
        raise _refuse("导入源位于其他成员的私有范围内，已拒绝",
                      CODE_OUTSIDE_ROOT, 403)
    if _inside_platform_data(real, trusted):
        # Run records, credentials and the identity database live here; copying
        # them into a browsable project would leak secrets into the console.
        raise _refuse("导入源位于平台数据目录内，已拒绝",
                      CODE_OUTSIDE_ROOT, 403)
    if os.path.islink(real):
        raise _refuse("导入源是符号链接，已拒绝", CODE_UNSAFE_PATH, 403)
    if not os.path.isdir(real):
        raise _refuse("导入源不是目录", CODE_NOT_FOUND, 404)

    relative = os.path.relpath(real, anchor).replace(os.sep, "/")
    if relative in (".", ""):
        raise _refuse("导入源不能是允许根目录本身，请选择其子目录",
                      CODE_INVALID_REQUEST, 400)
    entry = _clean_relative(relative)
    if not entry:
        raise _refuse("导入源必须是允许根目录下的子目录",
                      CODE_INVALID_REQUEST, 400)
    try:
        if safe_fs.is_symlink(anchor, entry) or not safe_fs.is_dir(anchor, entry):
            raise _refuse("导入源不可访问或已被替换，已拒绝",
                          CODE_UNSAFE_PATH, 403)
    except UnsafePathError as e:
        raise _refuse("导入源包含软链接分量，已拒绝",
                      CODE_UNSAFE_PATH, 403) from e
    return anchor, entry


def _inside_platform_data(path: str, trusted: str) -> bool:
    """True when ``path`` is inside the platform data directory (and it matters).

    ``config.get_data_root()`` holds the identity database and other private
    application data. The check is skipped when the member's own root already
    lives inside that directory (some deployments place the workspace there), in
    which case it cannot discriminate and the identity/owner checks are the
    boundary instead.
    """
    try:
        from config import get_data_root
        data_root = os.path.realpath(str(get_data_root()))
    except Exception:  # noqa: BLE001 - no configured data root is not a refusal
        return False
    if not data_root or data_root == os.sep:
        return False
    if safe_fs.contains(data_root, trusted):
        return False
    return safe_fs.contains(data_root, path)


def _target_name(name: Optional[str], source_rel: str) -> str:
    """The single visible folder name a project will be published under."""
    candidate = (name or "").strip() or source_rel.rsplit("/", 1)[-1]
    candidate = candidate.strip()
    if not candidate or candidate in (".", ".."):
        raise _refuse("项目名称无效", CODE_INVALID_NAME, 400)
    if "\x00" in candidate:
        raise _refuse("项目名称不能包含 NUL 字符", CODE_INVALID_NAME, 400)
    if "/" in candidate or "\\" in candidate:
        raise _refuse("项目名称不能包含路径分隔符", CODE_INVALID_NAME, 400)
    if candidate.startswith("."):
        # Hidden names would collide with the staging directories (and hide the
        # published project from the picker that just browsed it).
        raise _refuse("项目名称不能以点开头", CODE_INVALID_NAME, 400)
    if _DRIVE_RE.match(candidate):
        raise _refuse("项目名称不能包含盘符", CODE_INVALID_NAME, 400)
    return candidate


def _conflict_of(root: str, name: str) -> Optional[Dict[str, str]]:
    """A conflict report when something already holds the target name.

    Any existing entry counts -- a directory, a file, even a symlink -- because
    an import must never replace what is already there. The report carries no
    host path: only the name the member chose.
    """
    if not os.path.isdir(root):
        return None
    try:
        info = safe_fs.stat(root, name)
    except UnsafePathError as e:
        raise _refuse("目标名称已被占用，已拒绝", CODE_CONFLICT, 409) from e
    if info is None:
        return None
    return {"code": "name_taken", "name": name,
            "message": "同名项目已存在，不会覆盖"}


# --- source planning / copying ----------------------------------------------


def _children(anchor: str, relative: str
              ) -> Tuple[List[Tuple[str, str, os.stat_result]],
                         List[Tuple[str, str, os.stat_result]], List[str]]:
    """Classify one directory's entries into (dirs, files, excluded).

    Enumeration uses the validated path returned by ``safe_fs.resolve_within``
    purely to *name* what is there; every entry that is then recursed into or
    copied is re-checked through ``safe_fs`` (``is_symlink``/``stat``), so a name
    that races in cannot make the copy follow a link. Symlinked entries and
    special files (fifos, sockets, devices) are reported as excluded and never
    opened.
    """
    try:
        base = safe_fs.resolve_within(anchor, relative)
        with os.scandir(base) as entries:
            names = sorted(item.name for item in entries)
    except UnsafePathError as e:
        raise _refuse("导入源包含软链接分量，已拒绝", CODE_UNSAFE_PATH, 403) from e
    except OSError as e:
        raise ProjectBrowserError("无法读取导入源目录",
                                  code=CODE_IMPORT_FAILED, status=500) from e
    dirs: List[Tuple[str, str, os.stat_result]] = []
    files: List[Tuple[str, str, os.stat_result]] = []
    excluded: List[str] = []
    for name in names:
        child = _join(relative, name)
        try:
            if safe_fs.is_symlink(anchor, child):
                excluded.append(child)
                continue
            info = safe_fs.stat(anchor, child)
        except UnsafePathError as e:
            raise _refuse("导入源包含软链接分量，已拒绝",
                          CODE_UNSAFE_PATH, 403) from e
        if info is None:
            excluded.append(child)
            continue
        if _stat.S_ISDIR(info.st_mode):
            dirs.append((name, child, info))
        elif _stat.S_ISREG(info.st_mode):
            files.append((name, child, info))
        else:
            excluded.append(child)
    return dirs, files, excluded


def _scan_source(anchor: str, relative: str) -> Dict[str, Any]:
    """Count what an import would copy and list what it would skip."""
    files = dirs = 0
    total = 0
    excluded: List[str] = []
    stack = [relative]
    while stack:
        current = stack.pop()
        sub_dirs, sub_files, sub_excluded = _children(anchor, current)
        excluded.extend(sub_excluded)
        for _name, _child, info in sub_files:
            files += 1
            total += info.st_size
        for _name, child, _info in sub_dirs:
            dirs += 1
            stack.append(child)
    return {"files": files, "dirs": dirs, "bytes": total,
            "excluded": sorted(excluded)}


def _copy_tree(anchor: str, source_rel: str, root: str,
               staging_rel: str) -> Dict[str, Any]:
    """Copy the source into the staging directory, excluding links.

    Destination directories are created through ``safe_fs.mkdir`` and files
    through :func:`_copy_file`, which re-validates both sides with
    ``safe_fs.resolve_within`` before touching them.
    """
    counts: Dict[str, Any] = {"files": 0, "dirs": 0, "bytes": 0,
                              "excluded": []}
    stack = [(source_rel, staging_rel)]
    while stack:
        source_dir, dest_dir = stack.pop()
        sub_dirs, sub_files, sub_excluded = _children(anchor, source_dir)
        counts["excluded"].extend(sub_excluded)
        for name, child, info in sub_files:
            _copy_file(anchor, child, root, _join(dest_dir, name))
            counts["files"] += 1
            counts["bytes"] += info.st_size
        for name, child, _info in sub_dirs:
            new_dest = _join(dest_dir, name)
            safe_fs.mkdir(root, new_dest)
            counts["dirs"] += 1
            stack.append((child, new_dest))
    counts["excluded"] = sorted(counts["excluded"])
    return counts


def _copy_file(anchor: str, source_rel: str, root: str, dest_rel: str) -> None:
    """Stream one file from the source into the staging tree.

    Neither side can be opened by descriptor chain here (the source lives under
    a different anchor, and the destination file does not exist yet), so the
    *parent* directory on each side goes through ``safe_fs.resolve_within`` --
    which link-checks every component and re-checks containment -- the entry is
    confirmed to be a plain file with ``safe_fs.stat`` (an ``lstat``, so a
    symlink reports as a link and is refused), and both opens use
    ``O_NOFOLLOW``/``O_EXCL``. The residual window is the one ``safe_fs``
    documents for path-based access: a component replaced between validation and
    open. It is bounded here, because the destination parent is a directory this
    process just created inside its own staging tree, and the source is inside an
    allowed root.
    """
    source_parent_rel, _sep, source_name = source_rel.rpartition("/")
    dest_parent_rel, _dsep, dest_name = dest_rel.rpartition("/")
    if not source_name or not dest_name or not source_parent_rel:
        raise ProjectBrowserError("导入源路径无效",
                                  code=CODE_INVALID_REQUEST, status=400)
    source_dir_abs = safe_fs.resolve_within(anchor, source_parent_rel)
    dest_dir_abs = safe_fs.resolve_within(root, dest_parent_rel)
    info = safe_fs.stat(anchor, source_rel)
    if info is None or not _stat.S_ISREG(info.st_mode):
        raise _refuse("导入源条目已不可用，已拒绝", CODE_UNSAFE_PATH, 403)
    source_abs = os.path.join(source_dir_abs, source_name)
    source_fd = os.open(source_abs, os.O_RDONLY | _O_NOFOLLOW)
    try:
        if _DIR_FD_OK:
            dir_fd = os.open(dest_dir_abs, os.O_RDONLY | _O_DIRECTORY)
            try:
                dest_fd = os.open(dest_name,
                                  os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                                  0o644, dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
        else:
            dest_abs = os.path.join(dest_dir_abs, dest_name)
            if os.path.lexists(dest_abs):
                raise FileExistsError(dest_abs)
            dest_fd = os.open(dest_abs,
                              os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                              0o644)
        try:
            while True:
                block = os.read(source_fd, _CHUNK)
                if not block:
                    break
                os.write(dest_fd, block)
            os.fsync(dest_fd)
        finally:
            os.close(dest_fd)
    finally:
        os.close(source_fd)


# --- publish / rollback / quota ---------------------------------------------


def _ensure_root(root: str) -> None:
    if os.path.islink(root):
        raise _refuse("项目根目录是符号链接，已拒绝", CODE_UNSAFE_PATH, 403)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        raise ProjectBrowserError("无法准备项目根目录",
                                  code=CODE_ROOT_UNAVAILABLE,
                                  status=503) from e


def _publish(staging_abs: str, target_abs: str) -> None:
    """Move the staged tree onto the target name, never replacing content.

    ``os.mkdir`` claims the name exclusively, so two imports racing for it
    resolve to one winner and one ``conflict``. The claim is then replaced by the
    staged tree with a single ``os.rename``, which is atomic on POSIX. On
    platforms where a directory cannot be renamed onto an existing (empty)
    directory the placeholder is removed first -- the weaker window ``safe_fs``
    documents for Windows, with the platform ACL as the outer boundary.
    """
    try:
        os.mkdir(target_abs)
    except FileExistsError as e:
        raise _refuse("同名项目已存在，不会覆盖", CODE_CONFLICT, 409) from e
    try:
        try:
            os.rename(staging_abs, target_abs)
        except OSError:
            os.rmdir(target_abs)
            os.rename(staging_abs, target_abs)
    except BaseException:
        try:
            os.rmdir(target_abs)
        except OSError:
            pass
        raise


def _published_path(root: str, name: str) -> str:
    """The published project's absolute path, re-validated through ``safe_fs``."""
    try:
        return safe_fs.resolve_within(root, name)
    except (UnsafePathError, FileNotFoundError) as e:
        raise ProjectBrowserError("导入已发布但无法重新验证目标目录",
                                  code=CODE_IMPORT_FAILED, status=500) from e


def _rollback(staging: Optional[str]) -> None:
    """Remove an unpublished staging tree. It contains no links by construction."""
    if not staging or not os.path.lexists(staging):
        return
    try:
        shutil.rmtree(staging)
    except OSError as e:
        logger.warning("[ProjectBrowser] staging rollback failed: %s", e)


def _reserve(quota_reserve: Optional[Callable[[Dict[str, Any]], Any]],
             plan: Dict[str, Any]) -> Any:
    """Take the quota reservation before anything is created."""
    if quota_reserve is None:
        return None
    try:
        return quota_reserve(plan)
    except ProjectBrowserError:
        raise
    except Exception as e:  # noqa: BLE001 - a refused reservation is not success
        raise ProjectBrowserError("配额预占失败，未发布任何项目",
                                  code=CODE_QUOTA_REFUSED, status=409) from e


def _release(reservation: Any) -> None:
    """Hand a reservation back after a rollback, when it supports that."""
    release = getattr(reservation, "release", None)
    if not callable(release):
        return
    try:
        release()
    except Exception as e:  # noqa: BLE001 - rollback must not mask the cause
        logger.warning("[ProjectBrowser] quota release failed: %s", e)


def _commit(reservation: Any) -> str:
    """Confirm a reservation once the project is published.

    Reported rather than assumed: a quota service that fails to record the
    committed import leaves the project in place but the accounting behind, and
    saying "ok" would hide that. ``commit_failed`` is the caller's signal to
    reconcile.
    """
    if reservation is None:
        return "none"
    commit = getattr(reservation, "commit", None)
    if not callable(commit):
        return "reserved"
    try:
        commit()
        return "ok"
    except Exception as e:  # noqa: BLE001 - published project must be reported
        logger.error("[ProjectBrowser] quota commit failed after publish: %s", e)
        return "commit_failed"
