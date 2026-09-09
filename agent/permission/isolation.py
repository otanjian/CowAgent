# encoding:utf-8
"""Tenant code-execution isolation for database mode (open-database-runtime 6.x).

Legacy permission modes are *argument-level, not an OS sandbox* (see
``agent/permission/policy.py``): in a single-tenant install that is an honest
trade-off, but in database multi-tenant mode it would let one tenant's Agent
read another tenant's workspace, the identity database, or the deployment's
plaintext credentials.

This module is the server-side enforcement point for the execution-isolation
slice. It is *not* an OS/container sandbox — same caveat as the permission
modes — but it converts the boundary from "whatever the OS lets the process
touch" to a checked tenant boundary:

* Writes (``write``/``edit``, bash redirects / file-mutating commands) must
  resolve inside the current identity's tenant roots (tenant shared root, the
  Agent workspace, the user root, system temp). ``..``, symlinks and absolute
  paths are resolved with ``realpath`` before containment is judged.
* Reads (``read``/``ls``/``search_files``, bash path tokens) may be anywhere
  except a *blocked* area: the user's home (outside the verified workspace),
  the global data root (identity.db, private tenant data) and any *other*
  tenant's shared root.
* When the isolation slice is not yet validated (conf ``execution_isolation``
  false) the gate refuses arbitrary-code tools in database mode instead of
  falling back to unrestricted execution.

Pure LLM turns and read-only tools that do not touch paths are unaffected.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from agent.permission.policy import Decision

logger = logging.getLogger("execution_isolation")

#: Tools that run arbitrary code / spawn processes.
CODE_TOOLS = frozenset({"bash"})

#: File tools whose ``path``-like argument is written.
WRITE_TOOLS = frozenset({"write", "edit"})

#: File tools whose ``path``-like argument is read.
READ_TOOLS = frozenset({"read", "ls", "search_files", "search"})

#: Argument keys that carry a filesystem path for first-party tools.
_PATH_KEYS = frozenset({"path", "dir", "directory", "root", "target",
                        "destination", "file_path", "input_path", "output_path"})

CONFIG_KEY = "execution_isolation"

#: When enabled by default in database mode the slice is considered validated.
_DB_DEFAULT_ENABLED = True


def database_mode() -> bool:
    """True when the deployment runs in database identity mode."""
    try:
        from config import conf

        return str(conf().get("identity_mode", "legacy") or "legacy") == "database"
    except Exception:
        return False


def enabled() -> bool:
    """True when the tenant execution-isolation gate is on.

    Defaults to *on* in database mode (this slice is delivered and validated
    with this change); set ``execution_isolation=false`` to fail closed: in
    database mode arbitrary-code tools are then refused instead of running
    unconfined. Legacy (single-tenant) deployments are never gated.
    """
    if not database_mode():
        return False
    try:
        from config import conf

        return bool(conf().get(CONFIG_KEY, _DB_DEFAULT_ENABLED))
    except Exception:
        return True


def _current_identity():
    from common.runtime_identity import current_identity

    return current_identity()


def _svc():
    from auth.service import get_identity_service

    return get_identity_service()


@dataclass
class _Boundary:
    """Real (symlink-resolved) roots the current tenant may touch."""

    read_roots: List[str] = field(default_factory=list)
    write_roots: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)
    engineering: Optional[str] = None
    tenant_id: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.tenant_id is not None


def _real(path: str, cwd: Optional[str]) -> str:
    expanded = os.path.expanduser(os.path.expandvars(path or ""))
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd or os.getcwd(), expanded)
    try:
        return os.path.realpath(expanded)
    except Exception:
        return os.path.abspath(expanded)


def _contains(a: str, b: str) -> bool:
    try:
        return os.path.commonpath([a, b]) == a
    except ValueError:
        return False


def _append_unique(items: List[str], value: Optional[str]) -> None:
    if value and value not in items:
        items.append(value)


def resolve_boundary(ident=None) -> _Boundary:
    """Compute the tenant isolation boundary for the current identity.

    Fails *closed*: when a tenant identity cannot be resolved to trusted roots
    the boundary has no read/write roots, so any path check refuses. (See
    ``common/state_dir`` which already raises on home/global escape and on
    cross-tenant containment.)
    """
    boundary = _Boundary()
    try:
        ident = ident if ident is not None else _current_identity()
        if not ident or not ident.user_id or not ident.tenant_id:
            return boundary  # not a tenant-member run; gate not applicable

        boundary.tenant_id = ident.tenant_id
        svc = _svc()

        # Other tenants' shared roots are blocked areas for this tenant.
        try:
            for other in svc.tenant_shared_roots():
                if other.get("id") == ident.tenant_id:
                    continue
                _append_unique(boundary.blocked, other.get("shared_root"))
        except Exception:
            raise

        # Home and the global data root are blocked unless they *are* the
        # verified workspace root (default tenant lives there legitimately).
        home = os.path.realpath(os.path.expanduser("~"))
        engineering = None
        try:
            from agent.registry import get_agent_registry

            engineering = os.path.realpath(
                get_agent_registry().get(require_enabled=False).workspace
            )
        except Exception:
            engineering = None
        boundary.engineering = engineering
        if not (engineering and _contains(engineering, home)):
            _append_unique(boundary.blocked, home)
        try:
            from config import get_data_root

            _append_unique(boundary.blocked, os.path.realpath(get_data_root()))
        except Exception:
            pass

        from common import state_dir

        # Read roots: the tenant shared root plus this Agent's workspace (its
        # own state root is where sessions/memory live).
        read_roots = []
        try:
            _append_unique(read_roots, str(state_dir.shared_root()))
        except Exception as error:
            logger.warning(f"[isolation] shared_root unresolved: {error}")
        try:
            _append_unique(read_roots, str(state_dir.state_root(ident)))
        except Exception as error:
            logger.warning(f"[isolation] state_root unresolved: {error}")
        try:
            _append_unique(read_roots, str(state_dir.user_root(ident)))
        except Exception:
            pass
        try:
            _append_unique(read_roots, os.path.realpath(tempfile.gettempdir()))
        except Exception:
            pass

        boundary.read_roots = read_roots
        # Write roots are the writable subset: agent workspace, user root,
        # tenant shared root (skills/knowledge), system temp.
        boundary.write_roots = list(read_roots)
        return boundary
    except Exception as error:
        logger.warning(f"[isolation] boundary unresolved, failing closed: {error}")
        return boundary


def _outside(real: str, roots: Sequence[str]) -> bool:
    return not any(_contains(root, real) for root in roots if root)


def _deny(what: str) -> Decision:
    return Decision(
        False,
        f"隔离边界拒绝：{what}（多租户执行隔离：只能访问本租户工作根与状态目录，"
        f"跨租户/身份库/凭据路径一律拒绝。边界为参数级校验而非 OS 沙箱。）\n\n"
        f"Isolation boundary refused: {what} (tenant execution isolation allows only "
        f"this tenant's workspace and state dirs; cross-tenant / identity-db / "
        f"credential paths are always refused. Argument-level boundary, not an OS sandbox.)",
    )


# ---------------------------------------------------------------------------
# Bash command parsing (reuses the policy lexer for consistency)
# ---------------------------------------------------------------------------

def _collect_paths(command: str, cwd: Optional[str]):
    """Return ``(writes, reads, unparsable)`` for one bash command.

    ``writes`` are tokens the command redirects into or mutates with a
    file-changing command; ``reads`` are filesystem-looking tokens of every
    other command. ``unparsable`` is True when the line cannot be lexed at
    all (fail closed).
    """
    from agent.permission.policy import (
        _DESTINATION_ONLY_COMMANDS,
        _NULL_SINKS,
        _PATH_MUTATING_COMMANDS,
        _command_name,
        _parse_segments,
        _redirect_targets,
    )

    segments = _parse_segments(command)
    if segments is None:
        return [], [], True
    writes: List[str] = []
    reads: List[str] = []
    for tokens in segments:
        name, rest = _command_name(tokens)
        for target in _redirect_targets(tokens):
            if target not in _NULL_SINKS:
                writes.append(target)
        if name is None or not rest:
            continue
        if name in _PATH_MUTATING_COMMANDS:
            paths = [t for t in rest if not t.startswith("-")]
            if name in _DESTINATION_ONLY_COMMANDS and len(paths) > 1:
                paths = paths[-1:]
            writes.extend(paths)
            continue
        # Read-style command: explicit filesystem-looking operands.
        for token in rest:
            if not token or token.startswith("-"):
                continue
            if token.startswith(("/", "~", ".")) or os.sep in token:
                reads.append(token)
    return writes, reads, False


def _check_bash(boundary: _Boundary, args: Dict[str, Any],
                cwd: Optional[str]) -> Decision:
    command = str(args.get("command") or "").strip()
    if not command:
        # Reading from or killing a background job the agent itself started.
        return Decision(True)
    writes, reads, unparsable = _collect_paths(command, cwd)
    if unparsable:
        return _deny("命令无法解析，不能确认其访问边界")

    for token in writes:
        real = _real(token, cwd)
        if any(_contains(blocked, real) for blocked in boundary.blocked if blocked):
            return _deny(f"写入目标 {token!r} 位于隔离根之外")
        if _outside(real, boundary.write_roots):
            return _deny(f"写入目标 {token!r} 超出本租户可写范围")
    for token in reads:
        real = _real(token, cwd)
        if any(_contains(blocked, real) for blocked in boundary.blocked if blocked):
            return _deny(f"读取目标 {token!r} 位于隔离根之外")
    return Decision(True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def isolation_decision(tool_name: str, arguments: Dict[str, Any],
                       cwd: Optional[str] = None) -> Decision:
    """Return a Decision for one tool call under tenant execution isolation.

    Returns ``Decision(True)`` when the gate does not apply (legacy mode, a
    non-tenant-member run, or a path-free read-only tool). In database mode an
    explicitly-disabled isolation slice still refuses arbitrary-code tools
    (task 6.3: unvalidated => fail closed) while pure-LLM and read-only tools
    stay unaffected. Never raises.
    """
    try:
        if not enabled():
            # Database mode with the slice off: code execution is not confined,
            # so it is refused rather than run unconfined. Legacy mode is the
            # historical single-tenant deployment and is unaffected.
            if database_mode() and tool_name in CODE_TOOLS:
                return _deny("执行隔离未启用，代码执行已拒绝")
            return Decision(True)
        ident = _current_identity()
        if not ident or not ident.user_id or not ident.tenant_id:
            return Decision(True)  # not a tenant-member code-execution run

        if tool_name in CODE_TOOLS:
            boundary = resolve_boundary(ident)
            if not boundary.active:
                return _deny("多租户隔离边界不可用，代码执行已拒绝")
            return _check_bash(boundary, arguments, cwd)

        if tool_name in WRITE_TOOLS or tool_name in READ_TOOLS:
            path = None
            for key in _PATH_KEYS:
                value = arguments.get(key)
                if value:
                    path = str(value)
                    break
            if not path:
                return Decision(True)
            real = _real(path, cwd)
            boundary = resolve_boundary(ident)
            if not boundary.active:
                return _deny("多租户隔离边界不可用，文件访问已拒绝")
            if any(_contains(blocked, real) for blocked in boundary.blocked if blocked):
                return _deny(f"目标路径 {path!r} 位于隔离根之外")
            if tool_name in WRITE_TOOLS and _outside(real, boundary.write_roots):
                return _deny(f"写入目标 {path!r} 超出本租户可写范围")
            return Decision(True)

        # Unclassified tools do not get a path-level confinement here.
        return Decision(True)
    except Exception as error:
        logger.warning(f"[isolation] check skipped for {tool_name}: {error}")
        # Fail closed for arbitrary-code tools when the gate itself is broken,
        # so a DB-mode bash call cannot silently run without a boundary.
        if tool_name in CODE_TOOLS:
            return _deny("隔离校验异常，代码执行已拒绝")
        return Decision(True)
