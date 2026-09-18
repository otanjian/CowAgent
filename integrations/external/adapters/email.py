# encoding:utf-8
"""The personal-mailbox adapter: configure / probe / describe / invoke.

Scope
-----
This adapter is the only place the control plane learns *how* to talk to a
personal mailbox. It is deliberately thin: the IMAP, SMTP, MIME and boundary
logic lives in :mod:`integrations.external.mail`, and what remains here is the
part that needs the platform's vocabulary — the action names, the risk
catalogue, the tool provider and the staged results the console renders.

The properties this file is responsible for, and where they come from:

``本人邮箱`` (the caller's own mailbox)
    The tool provider binds only rows whose ``owner_user_id`` is the caller and
    whose scope is ``personal`` (:func:`_own_connections`). Another member's
    mailbox is not filtered out of a list — it is never selected, so there is no
    list for a bug to leak from. The connection id a tool carries is then
    re-authorized per call by ``ConnectionRuntime``.

``IMAP 与 SMTP 独立配置``
    Each side is probed, reported and gated on its own. A side that is not
    enabled is *skipped*, never failed, and "one side works" is reported as
    ``partial`` rather than rounded to success or failure
    (:meth:`EmailAdapter.probe`).

``只读获取不隐式标记`` / ``独立授权的标记``
    ``messages.search`` and ``messages.read`` select the mailbox read-only and
    fetch with ``BODY.PEEK``; ``messages.mark`` is a declared write action with
    its own execution class (``write_execute``), so opening reading does not
    open marking.

``发送``
    ``messages.send`` is critical, leaves the boundary, needs an approval and is
    never retried. A send whose final answer was lost returns
    ``invoke_unknown``, and a duplicate-submit guard stops a double dispatch of
    the same message (:meth:`EmailAdapter._send`).

``附件路径限制真实生效``
    Attachments are read and written only through
    :mod:`integrations.external.mail.guard`, which resolves every path inside an
    operator-configured, workspace-relative directory and refuses traversal,
    absolute paths and symlink escapes.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from integrations.external import registry, risk
from integrations.external.adapters.base import (
    AdapterError,
    CapabilityReport,
    ConnectionAdapter,
    ExecutionContext,
    InvokeResult,
    ProbeResult,
    STAGE_AUTH,
    STAGE_CONFIG,
    STAGE_INTERNAL,
    STAGE_POLICY,
    invoke_failed,
    invoke_ok,
    invoke_unknown,
    register_adapter,
    stage_failed,
    stage_ok,
    stage_partial,
    stage_skipped,
)
from integrations.external.errors import invalid
from integrations.external.mail import guard, imap, mime, smtp

#: Every action this adapter serves. The names are the risk catalogue's, so the
#: catalogue, the tool listing and the dispatch all speak one vocabulary.
ACTIONS: FrozenSet[str] = frozenset({
    "messages.search", "messages.read", "attachments.save", "messages.mark",
    "messages.send",
})

#: Actions that change the remote mailbox. ``attachments.save`` writes to the
#: caller's workspace, which the risk catalogue deliberately does not count as a
#: remote write (and therefore runs under ``read_execute``).
WRITE_ACTIONS: FrozenSet[str] = frozenset({"messages.mark", "messages.send"})

#: Actions that need the IMAP side. ``messages.send`` needs SMTP.
_IMAP_ACTIONS: FrozenSet[str] = frozenset({
    "messages.search", "messages.read", "attachments.save", "messages.mark",
})

#: One message in flight is bounded by these unless the operator tightens them.
_MAX_UID_LENGTH = 64
_ADDR_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_ADDR_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_ADDR_TLD = re.compile(r"^[A-Za-z]{2,}$")


# -- small helpers -----------------------------------------------------------

def _side(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """One protocol's configuration, treating anything unreadable as disabled."""
    raw = (config or {}).get(name)
    return raw if isinstance(raw, Mapping) else {"enabled": False}


def _result(ctx: ExecutionContext, stages: List[Any], *,
            metadata: Optional[Mapping[str, Any]] = None) -> ProbeResult:
    return ProbeResult(
        stages=tuple(stages), metadata=dict(metadata or {}),
        config_version=ctx.config_version,
        secret_versions=dict(ctx.secret_versions))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _check_from_addr(value: str) -> None:
    """Online-independent sanity for the SMTP 发件地址.

    The registry has already refused a value that is not ``x@y.z`` and refused
    the field entirely when SMTP is off. What it cannot express is that the
    *domain* has to be a usable, fully-qualified name — ``a@localhost`` or
    ``a@x..y`` passes the registry's regex and then fails at the server, after
    the operator has already saved and tested the connection.
    """
    text = str(value or "").strip()
    local, _, domain = text.rpartition("@")
    if not local or not domain:
        raise invalid("from_addr must be an email address", code="field_invalid",
                      fields={"smtp.from_addr": "invalid"})
    if len(text) > 254 or len(local) > 64:
        raise invalid("from_addr is too long", code="field_invalid",
                      fields={"smtp.from_addr": "invalid"})
    if not _ADDR_LOCAL.match(local) or local.startswith(".") \
            or local.endswith(".") or ".." in local:
        raise invalid("from_addr has an invalid local part",
                      code="field_invalid", fields={"smtp.from_addr": "invalid"})
    ascii_domain = domain
    if not domain.isascii():
        try:
            ascii_domain = domain.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            raise invalid("from_addr has an invalid domain",
                          code="field_invalid",
                          fields={"smtp.from_addr": "invalid"})
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not label or len(label) > 63
                              or not _ADDR_LABEL.match(label)
                              for label in labels):
        raise invalid("from_addr must use a fully-qualified domain",
                      code="field_invalid", fields={"smtp.from_addr": "invalid"})
    if not _ADDR_TLD.match(labels[-1]):
        raise invalid("from_addr has an invalid domain suffix",
                      code="field_invalid", fields={"smtp.from_addr": "invalid"})


def _check_attachment_dirs(dirs: Sequence[Any]) -> None:
    """Refuse duplicates the registry's per-entry check cannot see.

    ``["mail", "mail/"]`` and ``["mail", "./mail"]`` are the same directory
    written twice, and a directory listed twice makes "which rule applies?" a
    question again. Duplicates are reported rather than collapsed so the
    operator's form shows the mistake.
    """
    seen: Dict[str, str] = {}
    for entry in dirs:
        text = str(entry or "").strip().replace("\\", "/").rstrip("/")
        normalized = os.path.normpath(text or ".")
        if normalized in seen:
            raise invalid(
                "attachment_dirs lists %r twice" % str(entry),
                code="field_invalid", fields={"attachment_dirs": "duplicate"})
        seen[normalized] = str(entry)


def _validated_uid(value: Any) -> str:
    """An IMAP UID is a server-assigned number; anything else is refused.

    Validated *before* a connection is opened: an unchecked UID is interpolated
    into an IMAP command, so refusing here removes the injection shape entirely
    rather than relying on the library to quote it.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        raise guard.config_error("uid is required", code="uid_required")
    if len(text) > _MAX_UID_LENGTH or not text.isdigit():
        raise guard.config_error("uid must be a message number",
                                 code="invalid_uid")
    return text


def _validated_uids(params: Mapping[str, Any]) -> List[str]:
    """The UID set of a mark request, each one a positive message number."""
    raw = params.get("uids")
    if raw in (None, ""):
        single = params.get("uid")
        raw = [single] if single not in (None, "") else []
    if isinstance(raw, (str, bytes)):
        raw = [part for part in str(raw).split(",") if part.strip()]
    if isinstance(raw, Mapping) or not isinstance(raw, (list, tuple)):
        raise guard.config_error("uids must be a list of message numbers",
                                 code="invalid_uid")
    out = [_validated_uid(item) for item in raw if item not in (None, "")]
    if not out:
        raise guard.config_error("uid is required", code="uid_required")
    return out


def _validated_params(action: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(params or {})
    if action in ("messages.read", "attachments.save"):
        out["uid"] = _validated_uid(out.get("uid"))
    elif action == "messages.mark":
        out["uids"] = _validated_uids(out)
    return out


@register_adapter
class EmailAdapter(ConnectionAdapter):
    """One personal mailbox, IMAP and SMTP configured and gated separately."""

    kind = registry.KIND_EMAIL
    actions = ACTIONS
    write_actions = WRITE_ACTIONS

    # -- offline ------------------------------------------------------------

    def validate_config(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Checks the registry's shape validation cannot express, offline.

        :func:`integrations.external.registry.validate_config` has already
        refused unknown keys, a secret-shaped key, a side with no host or user,
        an out-of-range port, a bad ``tls_mode`` and a traversal-shaped
        ``attachment_dirs`` entry. What is left is the meaning of the values:
        the sender's domain has to be usable, the attachment directories must be
        distinct, and at least one protocol must be enabled.
        """
        normalized = dict(config or {})
        imap_cfg = _side(normalized, "imap")
        smtp_cfg = _side(normalized, "smtp")
        if not imap_cfg.get("enabled") and not smtp_cfg.get("enabled"):
            raise invalid("at least one of imap or smtp must be enabled",
                          code="no_protocol",
                          fields={"imap": "required", "smtp": "required"})
        if smtp_cfg.get("enabled"):
            _check_from_addr(str(smtp_cfg.get("from_addr") or ""))
        raw_dirs = normalized.get("attachment_dirs")
        if raw_dirs in (None, ""):
            raw_dirs = []
        if not isinstance(raw_dirs, (list, tuple)):
            raise invalid("attachment_dirs must be a list", code="field_type",
                          fields={"attachment_dirs": "type"})
        _check_attachment_dirs(list(raw_dirs))
        return normalized

    # -- probe --------------------------------------------------------------

    def probe(self, ctx: ExecutionContext) -> ProbeResult:
        """Probe each enabled protocol independently, reporting ``partial``.

        IMAP and SMTP are separate services that fail separately, so the result
        keeps them separate. Neither probe sends a message, marks a message or
        downloads a body: IMAP logs in and out, SMTP logs in only.

        How the overall verdict is produced matters. A stage that merely failed
        would make "IMAP works, SMTP does not" a plain failure, which the spec
        forbids ("任一协议失败不得报告整体成功" and the partial-success
        requirement). ``ProbeResult.outcome`` treats an all-``partial`` set as
        ``partial`` and any hard failure as ``failed``, so a side that failed
        *while the other passed* is reported as ``stage_partial`` carrying its
        own code and detail, and the exact per-side verdict is kept in
        ``metadata``. Two failed sides stay two hard failures.
        """
        imap_cfg = _side(ctx.config, "imap")
        smtp_cfg = _side(ctx.config, "smtp")
        enabled = [name for name, cfg in (("imap", imap_cfg), ("smtp", smtp_cfg))
                   if cfg.get("enabled")]
        if not enabled:
            return _result(ctx, [stage_failed(
                "configuration", "no_protocol", stage=STAGE_CONFIG,
                detail="neither imap nor smtp is enabled")])

        metadata: Dict[str, Any] = {"enabled": list(enabled)}
        results: Dict[str, Any] = {}
        if "imap" in enabled:
            ctx.check_alive()
            results["imap"] = imap.probe(ctx, imap_cfg)
        if "smtp" in enabled:
            ctx.check_alive()
            results["smtp"] = smtp.probe(ctx, smtp_cfg)

        failed = [name for name in enabled if not results[name].ok]
        is_partial = bool(failed) and len(failed) < len(enabled)

        stages: List[Any] = []
        for name, cfg, side_stage in (("imap", imap_cfg, STAGE_AUTH),
                                      ("smtp", smtp_cfg, STAGE_AUTH)):
            if not cfg.get("enabled"):
                stages.append(stage_skipped(
                    name, stage=STAGE_CONFIG, detail="not enabled"))
                metadata[name] = {"enabled": False, "outcome": "skipped",
                                  "code": "", "stage": "", "detail": "",
                                  "verification": "none"}
                continue
            outcome = results[name]
            metadata[name] = outcome.as_metadata()
            if outcome.ok:
                stages.append(stage_ok(name, stage=side_stage,
                                       detail=outcome.detail,
                                       duration_ms=outcome.duration_ms))
            elif is_partial:
                stages.append(stage_partial(name, outcome.code,
                                            stage=outcome.stage,
                                            detail=outcome.detail,
                                            duration_ms=outcome.duration_ms))
            else:
                stages.append(stage_failed(name, outcome.code,
                                           stage=outcome.stage,
                                           detail=outcome.detail,
                                           duration_ms=outcome.duration_ms))

        metadata["failed"] = list(failed)
        # The verification mode each attempt actually ran under, kept at the top
        # level so the console can show the risk state without reading per-side
        # metadata ("不能静默降级").
        metadata["verification"] = {
            name: metadata[name].get("verification", "none")
            for name in enabled}
        if failed and not is_partial:
            metadata["outcome_detail"] = "all enabled protocols failed"
        elif is_partial:
            metadata["outcome_detail"] = (
                "some protocols succeeded: " + ", ".join(failed) + " failed")
        return _result(ctx, stages, metadata=metadata)

    # -- capabilities -------------------------------------------------------

    def describe_capabilities(self, ctx: ExecutionContext) -> CapabilityReport:
        """Per-side configuration readiness, plus each action's own reason.

        Deployment readiness is deliberately not folded in here: the registry
        intersects this report with the classes the deployment opened, and mixing
        the two would make "the deployment has not opened sending" and "SMTP is
        not configured" the same sentence. Which of the two it is decides what
        the reader does next, so the deployment side is reported separately in
        ``metadata``.
        """
        imap_cfg = _side(ctx.config, "imap")
        smtp_cfg = _side(ctx.config, "smtp")
        reasons: Dict[str, str] = {}
        metadata: Dict[str, Any] = {}
        classes = {"configure"}

        imap_enabled = bool(imap_cfg.get("enabled"))
        smtp_enabled = bool(smtp_cfg.get("enabled"))
        imap_secret = bool(imap_enabled) and _has_secret(ctx, "imap_password")
        smtp_secret = bool(smtp_enabled) and _has_secret(ctx, "smtp_password")
        imap_ready = imap_enabled and imap_secret
        smtp_ready = smtp_enabled and smtp_secret

        for name, enabled, ready in (("imap", imap_enabled, imap_ready),
                                     ("smtp", smtp_enabled, smtp_ready)):
            metadata[name] = {"enabled": enabled, "ready": ready}
            if not enabled:
                reasons[name] = "protocol_disabled"
            elif not ready:
                reasons[name] = "secret_missing"

        if imap_ready or smtp_ready:
            classes.add("test")
        if imap_ready:
            classes.add("read_execute")
        if imap_ready or smtp_ready:
            classes.add("write_execute")

        actions: Dict[str, bool] = {}
        for action in sorted(ACTIONS):
            needs_imap = action in _IMAP_ACTIONS
            ready = imap_ready if needs_imap else smtp_ready
            actions[action] = ready
            if ready:
                continue
            if needs_imap:
                reasons[action] = ("secret_missing" if imap_enabled
                                   else "protocol_disabled")
            else:
                reasons[action] = ("secret_missing" if smtp_enabled
                                   else "protocol_disabled")

        opened = registry.open_classes(registry.KIND_EMAIL)
        metadata["deployment"] = {
            "test_open": "test" in opened,
            "read_execute_open": "read_execute" in opened,
            "write_execute_open": "write_execute" in opened,
        }
        metadata["sides"] = {"imap_enabled": imap_enabled,
                             "smtp_enabled": smtp_enabled}
        return CapabilityReport(classes=frozenset(classes), actions=actions,
                                reasons=reasons, metadata=metadata)

    # -- invoke -------------------------------------------------------------

    def invoke(self, ctx: ExecutionContext, action: str,
               params: Mapping[str, Any]) -> InvokeResult:
        self.guard_action(action)
        refusal = _pre_dispatch(ctx, action)
        if refusal is not None:
            return refusal
        started = time.monotonic()
        try:
            parameters = _validated_params(action, params)
            if action == "messages.search":
                data = imap.search(ctx, _side(ctx.config, "imap"), parameters)
            elif action == "messages.read":
                data = imap.read(ctx, _side(ctx.config, "imap"), parameters)
            elif action == "attachments.save":
                data = imap.save_attachments(ctx, _side(ctx.config, "imap"),
                                             parameters)
            elif action == "messages.mark":
                data = imap.mark(ctx, _side(ctx.config, "imap"), parameters)
            else:
                data = self._send(ctx, parameters)
            return invoke_ok(data, duration_ms=_elapsed_ms(started))
        except smtp.SendUnknown as exc:
            # The message may be on its way. This is not a failure to retry; it
            # is a state to reconcile.
            return invoke_unknown(exc.code, stage=exc.stage,
                                  message=guard.redact(str(exc)),
                                  duration_ms=_elapsed_ms(started))
        except AdapterError as exc:
            return invoke_failed(exc.code, stage=exc.stage,
                                 message=guard.redact(str(exc)),
                                 duration_ms=_elapsed_ms(started))
        except BaseException as exc:  # noqa: BLE001 - a bug is reported, not leaked
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return invoke_failed("adapter_error", stage=STAGE_INTERNAL,
                                 message=type(exc).__name__,
                                 duration_ms=_elapsed_ms(started))

    # -- send ---------------------------------------------------------------

    def _send(self, ctx: ExecutionContext,
              params: Mapping[str, Any]) -> Dict[str, Any]:
        """Build, bound, approve-check and deliver one message.

        The order below is the security property: recipients and attachments are
        validated and *loaded* (so a symlink escape is refused before anything is
        sent), the submit guard is claimed before the socket is touched, and only
        then does the SMTP conversation start.
        """
        smtp_cfg = _side(ctx.config, "smtp")
        if not smtp_cfg.get("enabled"):
            raise guard.config_error(
                "sending is not available: SMTP is not enabled for this "
                "connection", code="protocol_disabled")
        limits = guard.limits_from(ctx)

        declared_from = str(smtp_cfg.get("from_addr") or "").strip()
        requested_from = str(params.get("from_addr") or "").strip()
        if requested_from and requested_from != declared_from:
            # The From header is a configuration fact. A per-call override would
            # be a spoofing primitive that no approval binds.
            raise guard.config_error(
                "the sender address is fixed by the connection configuration",
                code="from_addr_fixed")
        if not mime.is_valid_address(declared_from):
            raise guard.config_error("the configured sender address is invalid",
                                     code="invalid_from_addr")

        to = mime.validate_recipients(
            mime.parse_recipients(params.get("to")), field="to",
            limit=limits.max_recipients)
        if not to:
            raise guard.config_error("at least one recipient is required",
                                     code="recipients_required")
        cc = mime.validate_recipients(
            mime.parse_recipients(params.get("cc")), field="cc",
            limit=limits.max_recipients)
        bcc = mime.validate_recipients(
            mime.parse_recipients(params.get("bcc")), field="bcc",
            limit=limits.max_recipients)
        envelope = list(dict.fromkeys(to + cc + bcc))
        if len(envelope) > limits.max_recipients:
            raise guard.config_error("too many recipients",
                                     code="too_many_recipients")

        subject = str(params.get("subject") or "").strip()
        if len(subject) > limits.max_subject_chars:
            raise guard.config_error("the subject is too long",
                                     code="subject_too_long")
        html = bool(params.get("html"))
        body = params.get("body")
        if body in (None, "") and params.get("body_html") not in (None, ""):
            body, html = params.get("body_html"), True
        body = str(body or "")
        if not subject and not body.strip():
            raise guard.config_error("the message has no subject and no body",
                                     code="empty_message")
        if len(body.encode("utf-8", "replace")) > limits.max_body_bytes:
            raise guard.config_error("the message body is too large",
                                     code="body_too_large")

        attachments = _load_attachments(ctx, params.get("attachments"), limits)

        # The approval the risk catalogue requires. ``check_invocation`` in the
        # runtime performs this with the deployment key; this is the same gate's
        # configuration facts, so a direct adapter call cannot send unapproved.
        _require_send_approval(ctx)

        key = guard.submit_key(ctx, {
            "to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body,
            "html": html,
            "attachments": [{"filename": item["filename"],
                             "sha256": item["sha256"],
                             "size": len(item["data"])}
                            for item in attachments],
        })
        if not guard.submit_guard().claim(key):
            # The same approved message is already in flight (or was delivered).
            # Refusing is the only safe answer: a second delivery cannot be
            # recalled.
            raise guard.policy_error(
                "this exact message was already submitted and is not sent "
                "again; reconcile the previous attempt instead",
                code="duplicate_submit")
        try:
            data = mime.build_message(from_addr=declared_from, to=to, cc=cc,
                                      subject=subject, body=body, html=html,
                                      attachments=attachments)
            outcome = smtp.send(ctx, smtp_cfg, from_addr=declared_from,
                                recipients=envelope, data=data)
        except smtp.SendUnknown:
            # The message may have been accepted: keep the guard so a retry of
            # the same message is refused, and let the caller reconcile.
            raise
        except BaseException:
            # A definitive refusal: nothing left the boundary, so the guard is
            # released and a corrected attempt may proceed.
            guard.submit_guard().release(key)
            raise
        return {
            "accepted": outcome["accepted"],
            "refused": outcome["refused"],
            "recipients": len(envelope),
            "subject": subject,
            "attachments": [{"filename": item["filename"],
                             "sha256": item["sha256"],
                             "size": len(item["data"])}
                            for item in attachments],
            "verification": str(outcome.get("verification") or ""),
        }


# -- send-time helpers -------------------------------------------------------

def _load_attachments(ctx: ExecutionContext,
                      declared: Any,
                      limits: guard.MailLimits) -> List[Dict[str, Any]]:
    """Resolve the caller's attachment list into bytes, or refuse.

    Two shapes are accepted:

    ``{"path": "...", "sha256": "...", "filename": "..."}``
        A file inside an operator-configured attachment directory. The digest is
        **required** and verified: the approval binds the request parameters, so
        declaring the content digest is what makes "the attachment changed after
        approval" detectable instead of a silent substitution of bytes at the
        approved path.
    ``{"filename": "...", "content": "..."}``
        Inline content, which the parameters already bind in full.

    Directories come from the connection's own ``attachment_dirs``, and the file
    is read through :mod:`integrations.external.mail.guard`, so traversal, an
    absolute path and a symlink escape are refusals rather than reads.
    """
    raw = declared
    if raw in (None, "", []):
        return []
    if isinstance(raw, Mapping):
        raw = [raw]
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise guard.config_error("attachments must be a list",
                                 code="invalid_attachments")
    if len(raw) > limits.max_attachment_count:
        raise guard.config_error("too many attachments",
                                 code="too_many_attachments")
    out: List[Dict[str, Any]] = []
    total = 0
    for entry in raw:
        if isinstance(entry, Mapping):
            path = entry.get("path")
            filename = entry.get("filename")
            declared_digest = str(entry.get("sha256") or "").strip().lower()
            content = entry.get("content")
        else:
            raise guard.config_error("each attachment must be an object",
                                     code="invalid_attachments")
        if path:
            if not declared_digest:
                raise guard.config_error(
                    "an attachment given by path must declare its sha256 so the "
                    "approval binds its content", code="attachment_digest_required")
            loaded = guard.read_attachment(ctx, path,
                                           max_bytes=limits.max_attachment_bytes)
            actual = _sha256_hex(loaded["data"])
            if actual != declared_digest:
                raise guard.path_refused(
                    "the attachment's content no longer matches the approved "
                    "digest", code="attachment_changed")
            data = loaded["data"]
            name = str(filename or loaded["filename"])
            digest = actual
        elif content is not None:
            data = str(content).encode("utf-8")
            name = str(filename or "attachment.txt")
            digest = _sha256_hex(data)
            if declared_digest and declared_digest != digest:
                raise guard.path_refused(
                    "the inline attachment does not match its declared digest",
                    code="attachment_changed")
        else:
            raise guard.config_error("an attachment needs a path or content",
                                     code="invalid_attachments")
        total += len(data)
        if total > limits.max_attachment_bytes:
            raise guard.config_error("the attachments exceed the size limit",
                                     code="attachment_too_large")
        out.append({"filename": guard.safe_client_filename(name),
                    "data": data, "sha256": digest})
    return out


def _sha256_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(bytes(data or b"")).hexdigest()


def _has_secret(ctx: ExecutionContext, slot: str) -> bool:
    try:
        return bool(ctx.has_secret(slot))
    except Exception:  # noqa: BLE001 - an unreadable secret is "missing"
        return False


def _pre_dispatch(ctx: ExecutionContext,
                  action: str) -> Optional[InvokeResult]:
    """The action's own gates, applied before any I/O.

    The runtime has already refused an undeclared action and a closed execution
    class; repeating the *risk* half here means a direct adapter call cannot
    skip it. The digest comparison itself stays in ``risk.check_invocation``
    because that is where the deployment's digest key lives — an adapter cannot
    recompute a keyed digest, and guessing one would turn a missing approval
    into a crash rather than a refusal.
    """
    write = action in WRITE_ACTIONS
    entry = risk.lookup(registry.KIND_EMAIL, action, write=write)
    if entry.level in risk.ACKNOWLEDGEMENT_REQUIRED \
            and entry.level not in risk.acknowledged_levels():
        return invoke_failed(
            "risk_not_acknowledged", stage=STAGE_POLICY,
            message="%s is a %s-risk action and this deployment has not "
                    "acknowledged that risk" % (action, entry.level))
    return None


def _require_send_approval(ctx: ExecutionContext) -> None:
    """Refuse a send without the approval ``check_invocation`` will require.

    ``messages.send`` is critical and leaves the boundary, so the presence and
    shape of an approval are checked here as well as at the runtime. The checks
    mirror ``risk.verify_approval`` up to (not including) the digest comparison:
    approved, named approver, separated from the requester, unexpired,
    unrevoked, single-use and digest-bearing.
    """
    approval = ctx.extra.get("approval") if isinstance(ctx.extra, Mapping) else None
    if not isinstance(approval, Mapping) or not approval.get("approved"):
        raise guard.policy_error(
            "sending mail requires an approval bound to this message",
            code="approval_required")
    decision = risk.ApprovalDecision.parse(approval)
    if decision.revoked:
        raise guard.policy_error("the approval was revoked",
                                 code="approval_revoked")
    if not decision.approver_user_id:
        raise guard.policy_error("the approval does not name an approver",
                                 code="approval_incomplete")
    if decision.approver_user_id == str(ctx.actor_user_id or ""):
        raise guard.policy_error(
            "the approver and the requester are the same user",
            code="approval_not_separated")
    if not decision.digest:
        raise guard.policy_error("the approval does not bind this action",
                                 code="approval_incomplete")
    now = int(time.time())
    if decision.expires_at and now > decision.expires_at:
        raise guard.policy_error("the approval expired", code="approval_expired")
    if decision.scope not in ("once", "single_use"):
        raise guard.policy_error(
            "the approval's scope is not accepted for a write action",
            code="approval_scope_refused")


# -- own-mailbox tool provider and dispatcher --------------------------------

def _config_of_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    import json
    try:
        loaded = json.loads(row["config_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def _own_connections(tenant_id: str, owner_user_id: str
                     ) -> List[Mapping[str, Any]]:
    """The caller's own personal mailbox rows, and nothing else.

    ``owner_user_id`` is a WHERE clause, not a filter applied afterwards: a
    connection belonging to another member is never selected, so there is no
    intermediate list from which it could leak. The row is also required to be
    enabled, so a disabled mailbox is not offered.
    """
    try:
        from integrations.external.service import get_external_connection_service
        service = get_external_connection_service()
    except Exception:  # noqa: BLE001 - an unreadable store offers nothing
        return []
    try:
        return list(service._store.execute(  # noqa: SLF001 - same layer
            "SELECT * FROM external_connections WHERE kind=? AND scope=?"
            " AND tenant_id=? AND owner_user_id=? AND deleted_at IS NULL"
            " AND enabled=1 ORDER BY id",
            (registry.KIND_EMAIL, registry.SCOPE_PERSONAL, tenant_id,
             owner_user_id)))
    except Exception:  # noqa: BLE001
        return []


def _email_tool_provider(tenant_id: Optional[str],
                         actor_user_id: str) -> List[Any]:
    """Bind the caller's own mailbox tools, gated by the deployment classes.

    Read actions are offered when reading is open; write actions only when
    ``write_execute`` is open, and each carries its risk projection so a listing
    consumer can show the level and whether an approval will be required. Listing
    is not authorization: every call re-derives the connection and the classes
    through ``ConnectionRuntime``.
    """
    from integrations.external.tools import ExternalTool, ToolBinding

    tenant = str(tenant_id or "").strip()
    owner = str(actor_user_id or "").strip()
    if not tenant or not owner:
        # A background or machine subject with no delegated user gets nothing:
        # a personal mailbox is never reached on someone else's behalf.
        return []
    opened = registry.open_classes(registry.KIND_EMAIL)
    read_open = "read_execute" in opened
    write_open = "write_execute" in opened
    if not read_open and not write_open:
        return []

    out: List[Any] = []
    for row in _own_connections(tenant, owner):
        config = _config_of_row(row)
        imap_enabled = bool(_side(config, "imap").get("enabled"))
        smtp_enabled = bool(_side(config, "smtp").get("enabled"))
        if not imap_enabled and not smtp_enabled:
            continue
        connection_id = str(row["id"])
        name = str(row["name"] or connection_id)
        for action in sorted(ACTIONS):
            needs_imap = action in _IMAP_ACTIONS
            if needs_imap and not imap_enabled:
                continue
            if not needs_imap and not smtp_enabled:
                continue
            write = action in WRITE_ACTIONS
            if write and not write_open:
                continue
            if not write and not read_open:
                continue
            projection = risk.action_projection(
                registry.KIND_EMAIL, action, write=write)
            tool = ExternalTool(
                name="%s.%s.%s" % (registry.KIND_EMAIL, action, connection_id),
                kind=registry.KIND_EMAIL, action=action, write=write,
                description="%s on the caller's own mailbox %r"
                            % (action, name),
                metadata={
                    "connection_name": name,
                    "scope": registry.SCOPE_PERSONAL,
                    "owner_user_id": owner,
                    "risk": projection,
                })
            out.append(ToolBinding(tool=tool, connection_id=connection_id,
                                   connection_name=name,
                                   scope=registry.SCOPE_PERSONAL))
    return out


def _email_dispatcher(service, binding, params: Mapping[str, Any], *,
                      tenant_id: Optional[str], actor_user_id: str,
                      agent_id: str = "", run_id: str = "",
                      approval: Optional[Mapping[str, Any]] = None):
    """Run one mailbox action against the connection the binding named."""
    return service.invoke_action(
        binding.connection_id, binding.tool.action, params,
        actor_user_id=actor_user_id, tenant_id=tenant_id,
        agent_id=agent_id, run_id=run_id, approval=approval)


def register_tools() -> None:
    from integrations.external.tools import register_dispatcher, \
        register_tool_provider
    register_tool_provider(registry.KIND_EMAIL, _email_tool_provider)
    register_dispatcher(registry.KIND_EMAIL, _email_dispatcher)


register_tools()
