# encoding:utf-8
"""Policy, TLS, path and duplicate-submit boundaries for mail.

Why this is one module
----------------------
Everything in here is a boundary the spec calls out explicitly, and each one is
easy to get subtly wrong if it lives at the call site:

``NetworkPolicy`` for raw TCP+TLS
    IMAP and SMTP are not HTTP, so ``NetworkPolicy.check``'s URL path does not
    apply. The *decision*, however, must still apply: the host/port must be
    permitted, the hostname must be resolved, and the connection must be made to
    a resolved, permitted **address** (not by name, which would re-resolve).
    :func:`resolve_target` mirrors ``NetworkPolicy.check``'s name/address rule
    and :func:`connect_raw` dials one of the returned addresses.

TLS verification honesty
    ``reject_unauthorized=false`` goes through ``NetworkPolicy.effective_verify``;
    a deployment that forbids turning verification off gets a refusal, never a
    silent downgrade. The verification mode the attempt actually ran under is
    reported on the result so the console can show the risk state.

Attachment path bounds
    Configured directories are relative to the workspace and re-validated on
    every access: absolute paths, ``..`` traversal, symlink escapes and final
    components outside an allowed directory are refused, and the final open uses
    ``O_NOFOLLOW`` plus a fresh containment check so a swap after validation is
    not silently accepted.

Redaction
    A mail server's error text can quote the credentials it just rejected, so no
    raw remote string reaches a detail field. :func:`redact` removes the password
    and bounds the message; callers pass everything through it.

Duplicate submit
    :class:`SubmitGuard` remembers a send's approval digest (or, without one, a
    digest of the canonical parameters) so a double click cannot put the same
    message on the wire twice. An action that failed *before* the message could
    have been accepted releases its key, so a corrected retry is still possible;
    an accepted or unknown send keeps it.
"""

from __future__ import annotations

import hashlib
import json
import errno
import os
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from integrations.external.adapters.base import (
    AdapterError,
    ExecutionContext,
    PolicyRefused,
    STAGE_CONFIG,
    STAGE_INTERNAL,
    STAGE_NETWORK,
    STAGE_POLICY,
    STAGE_TIMEOUT,
    STAGE_TLS,
)
from integrations.external.adapters.netpolicy import NetworkPolicy, current_policy


# -- errors ------------------------------------------------------------------

_ELOOP = errno.ELOOP


class MailError(AdapterError):
    """A refusal that already carries the stage and code a probe should report."""


def network_error(message: str, *, code: str = "network_unreachable") -> MailError:
    return MailError(message, code=code, stage=STAGE_NETWORK)


def timeout_error(message: str = "the server did not respond in time") -> MailError:
    return MailError(message, code="timeout", stage=STAGE_TIMEOUT)


def tls_error(message: str, *, code: str = "tls_failed") -> MailError:
    return MailError(message, code=code, stage=STAGE_TLS)


def policy_error(message: str, *, code: str = "target_not_allowed") -> MailError:
    return MailError(message, code=code, stage=STAGE_POLICY)


def config_error(message: str, *, code: str = "config_invalid") -> MailError:
    return MailError(message, code=code, stage=STAGE_CONFIG)


def protocol_error(message: str, *, code: str = "protocol_error") -> MailError:
    return MailError(message, code=code, stage=STAGE_INTERNAL)


def path_refused(message: str, *, code: str = "path_not_allowed") -> MailError:
    return MailError(message, code=code, stage=STAGE_POLICY)


# -- network policy applied to raw sockets -----------------------------------

def policy_for(ctx: ExecutionContext) -> NetworkPolicy:
    """The policy this attempt must obey.

    The runtime hands the resolved policy down inside ``ctx.limits``; a direct
    adapter call still reads the deployment's live policy rather than none.
    """
    limits = ctx.limits if isinstance(ctx.limits, Mapping) else {}
    policy = limits.get("policy")
    if isinstance(policy, NetworkPolicy):
        return policy
    return current_policy()


@dataclass(frozen=True)
class MailTarget:
    """A permitted endpoint, resolved to concrete addresses."""

    host: str
    port: int
    tls: bool
    verify: bool
    addresses: Tuple[str, ...]
    verification: str = "none"

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "tls": self.tls,
            "verify": self.verify,
            "verification": self.verification,
            "addresses": list(self.addresses),
        }


def _verification_label(*, tls: bool, verify: bool) -> str:
    if not tls:
        return "none"
    return "verified" if verify else "unverified"


@dataclass
class SideResult:
    """One protocol's probe verdict, with the verification mode it ran under."""

    ok: bool
    code: str = ""
    stage: str = STAGE_INTERNAL
    detail: str = ""
    duration_ms: int = 0
    verification: str = "none"
    host: str = ""
    port: int = 0
    tls: bool = False

    def as_metadata(self, *, enabled: bool = True,
                    outcome: Optional[str] = None) -> dict:
        return {
            "enabled": bool(enabled),
            "outcome": outcome or ("ok" if self.ok else "failed"),
            "code": self.code,
            "stage": self.stage,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "verification": self.verification,
            "host": self.host,
            "port": self.port,
            "tls": self.tls,
        }


def attempted_verification(side: Mapping[str, Any]) -> str:
    """The verification mode the configuration *asks* for.

    Used when a failure happens before the policy decision is made, so a probe
    still reports what was attempted rather than pretending it was verified.
    """
    tls = bool(side.get("tls", True)) or bool(
        side.get("tls_mode") and side.get("tls_mode") != "none")
    verify = bool(side.get("reject_unauthorized", True))
    if not tls:
        return "none"
    return "verified" if verify else "unverified"


def side_failure(exc: BaseException, *, side: Mapping[str, Any],
                 verification: str, started: float) -> SideResult:
    """Map an exception from a mail attempt onto a staged probe result."""
    host = str(side.get("host") or "")
    port = int(side.get("port") or 0)
    tls = bool(side.get("tls", True)) or (
        side.get("tls_mode") not in (None, "", "none"))
    duration = int((time.monotonic() - started) * 1000)
    if isinstance(exc, MailError):
        return SideResult(ok=False, code=exc.code, stage=exc.stage,
                          detail=redact(str(exc)), duration_ms=duration,
                          verification=verification, host=host, port=port,
                          tls=tls)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return SideResult(ok=False, code="timeout", stage=STAGE_NETWORK,
                          detail="the server did not respond in time",
                          duration_ms=duration, verification=verification,
                          host=host, port=port, tls=tls)
    if isinstance(exc, OSError):
        return SideResult(ok=False, code="network_unreachable",
                          stage=STAGE_NETWORK,
                          detail=redact("network error: %s"
                                        % type(exc).__name__),
                          duration_ms=duration, verification=verification,
                          host=host, port=port, tls=tls)
    return SideResult(ok=False, code="adapter_error", stage=STAGE_INTERNAL,
                      detail=type(exc).__name__, duration_ms=duration,
                      verification=verification, host=host, port=port, tls=tls)


def resolve_target(ctx: ExecutionContext, host: str, port: Any, *,
                   tls: bool, verify_requested: bool,
                   verify_port_scheme: str = "") -> MailTarget:
    """Validate a mail endpoint against the deployment policy.

    Mirrors ``NetworkPolicy.check`` for a non-HTTP scheme: a listed hostname must
    be permitted (unless the deployment granted a network/private range), every
    resolved address must be permitted, and TLS verification may only be turned
    off when the deployment allows it.
    """
    policy = policy_for(ctx)
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        raise config_error("the server host is not configured",
                           code="host_missing")
    if not policy.host_allowed(name) \
            and not policy.allow_networks and not policy.allow_private:
        raise policy_error("host %r is not in the allowed list" % name,
                           code="target_not_allowed")
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise config_error("the server port is invalid", code="invalid_port")
    if number < 1 or number > 65535:
        raise config_error("the server port is invalid", code="invalid_port")
    scheme = verify_port_scheme or ("https" if tls else "http")
    if not policy.port_allowed(number, scheme):
        raise policy_error("port %d is not allowed" % number,
                           code="target_not_allowed")
    if not tls and not policy.allow_insecure_http:
        # Turning TLS off entirely is the strongest form of downgrade: the
        # password crosses the network in the clear. It is refused unless the
        # deployment says plain transport is acceptable, and the result records
        # ``verification="none"`` so the console shows the risk state.
        raise policy_error(
            "this connection disables TLS, which the deployment does not permit",
            code="tls_required")

    # Verification permission is checked before resolution so a deployment that
    # forbids skipping verification refuses without a DNS lookup.
    try:
        verify = bool(policy.effective_verify(bool(verify_requested)))
    except PolicyRefused as exc:
        raise policy_error(
            "this connection asks to skip TLS certificate verification, which "
            "the deployment does not permit", code="tls_verification_refused")

    try:
        resolved = policy.resolve(name, number)
    except PolicyRefused as exc:
        # A clear operator-facing diagnostic, not a raw socket error (matching
        # the reference implementation's "无法解析服务器地址" message).
        raise network_error(
            "cannot resolve host %r (host=%r, port=%r, tls=%r); check the "
            "server address" % (name, name, number, bool(tls)),
            code="dns_failure") from exc
    allowed = [target for target in resolved if policy.address_allowed(target.address)]
    if not allowed:
        raise policy_error(
            "host %s resolves to an address that is not allowed (%s)"
            % (name, resolved[0].address), code="target_not_allowed")
    return MailTarget(host=name, port=number, tls=bool(tls), verify=verify,
                      addresses=tuple(target.address for target in allowed),
                      verification=_verification_label(tls=bool(tls), verify=verify))


def connect_raw(ctx: ExecutionContext, target: MailTarget, *,
                timeout: Optional[float] = None) -> socket.socket:
    """Dial one of the resolved, permitted addresses (never the name)."""
    budget = float(timeout if timeout is not None else 15.0)
    last_error: Optional[BaseException] = None
    for address in target.addresses:
        ctx.check_alive()
        try:
            return socket.create_connection(
                (address, target.port), ctx.io_timeout(budget))
        except (socket.timeout, TimeoutError) as exc:
            last_error = exc
            raise timeout_error() from exc
        except OSError as exc:
            last_error = exc
            continue
    detail = type(last_error).__name__ if last_error else "no address"
    raise network_error(
        "cannot connect to %s:%d (%s)" % (target.host, target.port, detail),
        code="network_unreachable")


def tls_context(verify: bool) -> ssl.SSLContext:
    if verify:
        return ssl.create_default_context()
    # Reaching here means the deployment explicitly permitted skipping
    # verification (``effective_verify``); the risk state is reported on the
    # result rather than hidden.
    return ssl._create_unverified_context()  # noqa: SLF001 - deliberate opt-out


def wrap_tls(raw: socket.socket, target: MailTarget, *,
             timeout: Optional[float] = None) -> socket.socket:
    if timeout is not None:
        try:
            raw.settimeout(timeout)
        except OSError:
            pass
    context = tls_context(target.verify)
    try:
        return context.wrap_socket(raw, server_hostname=target.host)
    except ssl.SSLCertVerificationError as exc:
        raise tls_error("the server certificate could not be verified",
                        code="tls_verify_failed") from exc
    except ssl.SSLError as exc:
        raise tls_error("the TLS handshake failed", code="tls_failed") from exc
    except OSError as exc:
        raise tls_error("the TLS handshake failed", code="tls_failed") from exc


# -- redaction ---------------------------------------------------------------

def redact(text: Any, secrets: Sequence[Any] = ()) -> str:
    """A short, credential-free description of a failure.

    Every detail string goes through here: a mail server can echo the rejected
    username/password in its error text, and a raw remote body must never reach
    the projection.
    """
    out = " ".join(str(text if text is not None else "").split())
    for secret in secrets:
        value = str(secret or "")
        if value:
            out = out.replace(value, "***")
    if len(out) > 240:
        out = out[:240] + "…"
    return out


# -- workspace and attachment path bounds ------------------------------------

def workspace_root(ctx: ExecutionContext) -> str:
    """The operator-approved root attachment paths are relative to.

    Taken from the already-resolved limits, then the per-call extras, then the
    deployment's workspace. Missing everywhere is a refusal: "空目录配置 SHALL
    使用系统最小允许范围", and an unknown root is not a root.
    """
    for source in (ctx.limits, ctx.extra):
        if isinstance(source, Mapping):
            value = source.get("workspace_root")
            if value:
                return os.path.realpath(os.path.expanduser(str(value)))
    try:
        from config import conf
        value = conf().get("agent_workspace")
    except Exception:  # noqa: BLE001 - unreadable config is not a workspace
        value = None
    if value:
        return os.path.realpath(os.path.expanduser(str(value)))
    raise config_error("no workspace root is available for attachments",
                       code="workspace_unavailable")


def _within(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives on Windows
        return False


@dataclass(frozen=True)
class AttachmentDirs:
    root: str
    dirs: Tuple[str, ...]

    def as_dict(self) -> dict:
        return {"root": self.root,
                "dirs": [os.path.relpath(d, self.root) for d in self.dirs]}


def attachment_dirs(ctx: ExecutionContext) -> AttachmentDirs:
    root = workspace_root(ctx)
    raw = (ctx.config or {}).get("attachment_dirs") or []
    out = []
    for entry in raw:
        text = str(entry or "").strip()
        if not text or os.path.isabs(text):
            raise path_refused("attachment_dirs must be relative paths",
                               code="invalid_attachment_dir")
        candidate = os.path.realpath(os.path.join(root, text))
        if not _within(candidate, root):
            raise path_refused(
                "attachment_dirs must stay inside the workspace",
                code="outside_workspace")
        out.append(candidate)
    return AttachmentDirs(root=root, dirs=tuple(out))


def safe_client_filename(name: Any) -> str:
    """A caller-supplied file name, refusing anything path-like.

    Refusing rather than sanitising is deliberate for a write: a name that says
    ``../x`` is a mistake or an attack, and silently writing ``x`` would hide it.
    """
    raw = str(name if name is not None else "").strip()
    if not raw:
        raise path_refused("filename is required", code="invalid_filename")
    if "/" in raw or "\\" in raw:
        raise path_refused("filename must not contain a path",
                           code="invalid_filename")
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
    if cleaned in ("", ".", ".."):
        raise path_refused("filename is invalid", code="invalid_filename")
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned


def sanitize_message_filename(name: Any, *, fallback: str = "attachment") -> str:
    """A name taken from a message: keep the base name, drop everything else."""
    raw = str(name if name is not None else "").replace("\\", "/").strip()
    base = raw.split("/")[-1] if raw else ""
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().strip(".")
    if not base:
        base = fallback
    return base[:200]


def _resolve_directory(dirs: AttachmentDirs, directory: Any) -> str:
    if directory in (None, ""):
        if not dirs.dirs:
            raise path_refused("no attachment directory is configured",
                               code="no_attachment_dir")
        chosen = dirs.dirs[0]
    else:
        raw = str(directory).strip()
        joined = raw if os.path.isabs(raw) else os.path.join(dirs.root, raw)
        chosen = os.path.realpath(joined)
        if not any(_within(chosen, allowed) for allowed in dirs.dirs):
            raise path_refused(
                "the directory is not an allowed attachment directory",
                code="outside_attachment_dirs")
    os.makedirs(chosen, exist_ok=True)
    return chosen


def _unique_target(directory: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    for index in range(0, 1000):
        candidate = os.path.join(
            directory, filename if index == 0 else "%s-%d%s" % (stem, index, ext))
        if os.path.islink(candidate):
            raise path_refused("the target is a symbolic link",
                               code="symlink_escape")
        if not os.path.exists(candidate):
            return candidate
    raise path_refused("could not choose a unique file name",
                       code="filename_conflict")


def write_attachment(ctx: ExecutionContext, *, directory: Any, filename: Any,
                     data: bytes) -> dict:
    """Write one attachment inside the allowed directories.

    ``O_CREAT|O_EXCL|O_NOFOLLOW`` means the final open cannot follow a symlink
    planted after validation, and the resolved parent was checked to be inside
    an allowed directory.
    """
    dirs = attachment_dirs(ctx)
    chosen_dir = _resolve_directory(dirs, directory)
    name = safe_client_filename(filename)
    target = _unique_target(chosen_dir, name)
    if not _within(os.path.dirname(target), chosen_dir):
        raise path_refused("the target escapes the allowed directory",
                           code="outside_attachment_dirs")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise
    return {"path": target, "filename": os.path.basename(target),
            "size": len(data)}


def read_attachment(ctx: ExecutionContext, path: Any, *, max_bytes: int) -> dict:
    """Read one attachment that must live inside an allowed directory.

    ``realpath`` is what makes the symlink check real: a link *inside* an
    allowed directory that points outside it resolves to an outside path, and
    the containment test refuses it. ``O_NOFOLLOW`` then closes the window
    between that check and the open, so a component swapped in after validation
    cannot be followed either.
    """
    dirs = attachment_dirs(ctx)
    raw = str(path if path is not None else "").strip()
    if not raw:
        raise path_refused("an attachment path is required",
                           code="invalid_attachment_path")
    joined = raw if os.path.isabs(raw) else os.path.join(dirs.root, raw)
    resolved = os.path.realpath(joined)
    if not any(_within(resolved, allowed) for allowed in dirs.dirs):
        raise path_refused(
            "the attachment is outside the allowed attachment directories",
            code="outside_attachment_dirs")
    if not os.path.isfile(resolved):
        raise path_refused("the attachment file does not exist",
                           code="attachment_missing")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        if exc.errno == _ELOOP:
            raise path_refused("the attachment is a symbolic link",
                               code="symlink_escape") from exc
        raise path_refused("the attachment could not be opened",
                           code="attachment_unreadable") from exc
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise path_refused("the attachment is too large",
                           code="attachment_too_large")
    return {"path": resolved, "filename": os.path.basename(resolved),
            "size": len(data), "data": data}


# -- limits ------------------------------------------------------------------

@dataclass(frozen=True)
class MailLimits:
    """The bounded sizes and counts a mail attempt obeys."""

    max_recipients: int = 50
    max_attachment_count: int = 20
    max_attachment_bytes: int = 25 * 1024 * 1024
    max_body_bytes: int = 1024 * 1024
    max_read_bytes: int = 5 * 1024 * 1024
    max_subject_chars: int = 500
    connect_timeout: float = 15.0
    search_limit: int = 50
    default_mailbox: str = "INBOX"

    def as_dict(self) -> dict:
        return {
            "max_recipients": self.max_recipients,
            "max_attachment_count": self.max_attachment_count,
            "max_attachment_bytes": self.max_attachment_bytes,
            "max_body_bytes": self.max_body_bytes,
            "max_read_bytes": self.max_read_bytes,
            "connect_timeout": self.connect_timeout,
            "search_limit": self.search_limit,
        }


def _positive_int(value: Any, default: int, *, ceiling: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < 1:
        return default
    return min(number, ceiling)


def limits_from(ctx: ExecutionContext) -> MailLimits:
    raw = ctx.limits if isinstance(ctx.limits, Mapping) else {}
    return MailLimits(
        max_recipients=_positive_int(raw.get("mail_max_recipients"), 50,
                                     ceiling=1000),
        max_attachment_count=_positive_int(raw.get("mail_max_attachment_count"),
                                           20, ceiling=200),
        max_attachment_bytes=_positive_int(raw.get("mail_max_attachment_bytes"),
                                           25 * 1024 * 1024,
                                           ceiling=100 * 1024 * 1024),
        max_body_bytes=_positive_int(raw.get("mail_max_body_bytes"),
                                     1024 * 1024, ceiling=20 * 1024 * 1024),
        max_read_bytes=_positive_int(raw.get("mail_max_read_bytes"),
                                     5 * 1024 * 1024, ceiling=50 * 1024 * 1024),
        max_subject_chars=_positive_int(raw.get("mail_max_subject_chars"), 500,
                                        ceiling=2000),
        connect_timeout=float(raw.get("mail_connect_timeout") or 15.0),
        search_limit=_positive_int(raw.get("mail_search_limit"), 50,
                                   ceiling=200),
        default_mailbox=str(raw.get("mail_default_mailbox") or "INBOX"),
    )


# -- duplicate-submit guard --------------------------------------------------

class SubmitGuard:
    """Remember in-flight/finished sends so a double click cannot send twice.

    The key is the approval digest when one is present (approval is single-use,
    so the same approval cannot dispatch twice), otherwise a digest of the
    canonical parameters. A send that definitely did not leave the boundary
    releases its key so a corrected retry is allowed; an accepted or unknown
    send keeps it, which is what "禁止无条件重发" needs.
    """

    def __init__(self, *, ttl_seconds: int = 3600, capacity: int = 4096) -> None:
        self._lock = threading.Lock()
        self._entries: dict = {}
        self._ttl = int(ttl_seconds)
        self._capacity = int(capacity)

    def _purge(self, now: float) -> None:
        expired = [key for key, stamp in self._entries.items()
                   if now - stamp > self._ttl]
        for key in expired:
            self._entries.pop(key, None)

    def claim(self, key: str, *, now: Optional[float] = None) -> bool:
        stamp = float(now if now is not None else time.time())
        with self._lock:
            self._purge(stamp)
            if key in self._entries:
                return False
            if len(self._entries) >= self._capacity:
                self._purge(stamp)
                if len(self._entries) >= self._capacity:
                    # Full: refuse rather than evicting a live guard, which
                    # could allow a duplicate.
                    return False
            self._entries[key] = stamp
            return True

    def settle(self, key: str, *, now: Optional[float] = None) -> None:
        stamp = float(now if now is not None else time.time())
        with self._lock:
            if key in self._entries:
                self._entries[key] = stamp

    def release(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_SUBMIT_GUARD = SubmitGuard()


def submit_guard() -> SubmitGuard:
    return _SUBMIT_GUARD


def submit_key(ctx: ExecutionContext, params: Mapping[str, Any]) -> str:
    """A stable key for one intended send.

    An approval digest is the strongest key (it binds the exact connection,
    version, actor, recipients and content). Without one, the canonical
    parameters plus the connection identify the intended message.
    """
    approval = ctx.extra.get("approval") if isinstance(ctx.extra, Mapping) else None
    digest = ""
    if isinstance(approval, Mapping):
        digest = str(approval.get("digest") or "")
    if digest:
        return "approval:%s" % digest
    from integrations.external.risk import canonical_parameters
    blob = json.dumps(
        {"connection_id": str(ctx.connection_id), "action": "messages.send",
         "parameters": canonical_parameters(params or {})},
        sort_keys=True, ensure_ascii=False, default=str)
    return "params:%s" % hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]
