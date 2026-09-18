# encoding:utf-8
"""The personal-mailbox adapter, task group 8 and the email parts of group 9.

Subject under test: ``integrations/external/adapters/email.py`` and the
``integrations/external/mail/**`` protocol package it delegates to.

The specs make these non-negotiable, and each has a section below:

* IMAP and SMTP are configured, probed and gated independently, and "one side
  works" is reported as ``partial`` — neither a success nor a plain failure
  (``personal-email-integration``: 分协议报告 / 部分成功);
* a read never marks a message, and marking is a separately authorized write;
* the attachment directories are a real boundary: traversal, absolute paths and
  symlink escapes are refused at read *and* write time;
* a send is bounded (recipients, sizes), properly encoded (non-ASCII subject,
  content types, ``From`` from the configuration), never leaks a Bcc into the
  headers, needs an approval, and is **never retried**: when the final answer is
  lost the result is ``outcome_unknown``, and a duplicate submit is refused;
* the mailbox tools are bound to the caller's own connection only.

No test contacts a real mail server or the network: the protocol seams
(``open_connection``) are replaced with scripted connections, and the two tests
that exercise the *real* ``imaplib``/``smtplib`` path do it over an in-memory
socket.
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import socket
import smtplib
import time
from email.message import EmailMessage
from typing import Any, Mapping, Optional

import pytest

from integrations.external import registry, risk
from integrations.external.adapters import email as email_adapter
from integrations.external.adapters.base import ExecutionContext
from integrations.external.adapters.netpolicy import NetworkPolicy, reset_policy_cache
from integrations.external.mail import guard
from integrations.external.mail import imap as mail_imap
from integrations.external.mail import mime as mail_mime
from integrations.external.mail import smtp as mail_smtp
from integrations.external.mail.guard import MailError
from tests._helpers import build_identity

MASTER_KEY = "unit-test-master-key"
PASSWORD = "S3cretPassw0rd!"
ALLOWED = ("imap.example.com", "smtp.example.com")

IMAP_SIDE: Mapping[str, Any] = {
    "enabled": True, "host": "imap.example.com", "port": 993,
    "user": "alice@example.com", "reject_unauthorized": True, "tls": True,
    "mailbox": "INBOX",
}
SMTP_SIDE: Mapping[str, Any] = {
    "enabled": True, "host": "smtp.example.com", "port": 465,
    "user": "alice@example.com", "reject_unauthorized": True,
    "from_addr": "alice@example.com", "tls_mode": "implicit_tls",
}
CONFIG: Mapping[str, Any] = {"imap": IMAP_SIDE, "smtp": SMTP_SIDE,
                             "attachment_dirs": ["mail"]}


# -- fixtures and helpers ----------------------------------------------------

@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture(autouse=True)
def _clean_submit_guard():
    guard.submit_guard().clear()
    try:
        yield
    finally:
        guard.submit_guard().clear()


@pytest.fixture
def acknowledged(monkeypatch):
    """A deployment that accepted the critical risk ``messages.send`` carries.

    Sending is critical *and* leaves the boundary, so a check refuses it until an
    operator acknowledges that level. Tests that want to reach the send itself
    have to say so.
    """
    from config import conf
    block = dict(conf().get("external_connections") or {})
    block["risk"] = {"acknowledged_levels": ["critical"]}
    monkeypatch.setitem(conf(), "external_connections", block)
    return block


def _policy(**overrides) -> NetworkPolicy:
    values: dict = {"allow_hosts": frozenset(ALLOWED)}
    values.update(overrides)
    return NetworkPolicy(**values)


def _ctx(config: Optional[Mapping[str, Any]] = None, *,
         secrets: Optional[Mapping[str, str]] = None,
         limits: Optional[Mapping[str, Any]] = None,
         extra: Optional[Mapping[str, Any]] = None,
         policy: Optional[Any] = None,
         workspace: Optional[Any] = None,
         actor_user_id: str = "u-alice",
         connection_id: str = "conn_mail_1") -> ExecutionContext:
    values = {"imap_password": PASSWORD, "smtp_password": PASSWORD}
    values.update(dict(secrets or {}))
    merged: dict = {"policy": policy if policy is not None else _policy()}
    if workspace is not None:
        merged["workspace_root"] = str(workspace)
    merged.update(dict(limits or {}))

    def _resolver(slot: str) -> str:
        if slot not in values:
            raise MailError("secret %r is not configured" % slot,
                            code="secret_unavailable", stage="config")
        return values[slot]

    return ExecutionContext(
        kind=registry.KIND_EMAIL, scope=registry.SCOPE_PERSONAL,
        tenant_id="tenant-1", owner_user_id=actor_user_id,
        connection_id=connection_id, config=dict(config or CONFIG),
        secret_resolver=_resolver, config_version=7,
        secret_versions={"imap_password": 1, "smtp_password": 2},
        actor_user_id=actor_user_id, limits=merged, extra=dict(extra or {}))


def _approval(**overrides) -> Mapping[str, Any]:
    now = int(time.time())
    value = {"approved": True, "approver_user_id": "u-approver",
             "digest": "a" * 64, "issued_at": now, "expires_at": now + 600,
             "revoked": False, "scope": "once"}
    value.update(overrides)
    return value


def _raw_message(*, subject: str = "hello", body: str = "the body",
                 attachments=()) -> bytes:
    message = EmailMessage(policy=email.policy.SMTP.clone(cte_type="7bit"))
    message["From"] = "sender@example.com"
    message["To"] = "alice@example.com"
    message["Subject"] = subject
    message.set_content(body)
    for name, data, content_type in attachments:
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=name)
    return message.as_bytes()


def _dns_ok(monkeypatch, address: str = "93.184.216.34"):
    """Make name resolution succeed offline, returning ``address``."""
    import integrations.external.adapters.netpolicy as netpolicy

    def _fake(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (address, int(port or 0)))]

    monkeypatch.setattr(netpolicy.socket, "getaddrinfo", _fake)


def _dns_failure(monkeypatch):
    import integrations.external.adapters.netpolicy as netpolicy

    def _fake(host, port, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(netpolicy.socket, "getaddrinfo", _fake)


# -- fake protocol connections ----------------------------------------------

class FakeImap:
    """An ``imaplib``-shaped stub for the adapter's own seam.

    The adapter wraps a connection in :class:`~integrations.external.mail.imap
    .ImapSession` and that wrapper speaks the stdlib surface (``login``,
    ``select``, ``uid``), so the stub implements exactly that. What the wrapper
    *asks* is recorded, which is how the read-only/no-mark properties are
    asserted.
    """

    def __init__(self, *, messages: Optional[Mapping[str, bytes]] = None,
                 login_error: Optional[BaseException] = None,
                 mailbox_count: int = 1):
        self.calls = []
        self.passwords = []
        self.messages = dict(messages or {})
        self.login_error = login_error
        self.mailbox_count = mailbox_count

    def login(self, user, password):
        self.calls.append(("login", user))
        self.passwords.append(password)
        if self.login_error is not None:
            raise self.login_error
        return ("OK", [b"LOGIN completed"])

    def logout(self):
        self.calls.append(("logout",))

    def select(self, mailbox, readonly=True):
        self.calls.append(("select", mailbox, readonly))
        return ("OK", [str(self.mailbox_count).encode()])

    def uid(self, command, *args):
        verb = str(command or "").upper()
        self.calls.append(("uid", verb, args))
        if verb == "SEARCH":
            return ("OK", [b" ".join(str(uid).encode() for uid in self.messages)])
        if verb == "FETCH":
            uid = str(args[0])
            if uid not in self.messages:
                return ("NO", [b"no such message"])
            raw = self.messages[uid]
            if "HEADER.FIELDS" in " ".join(str(part) for part in args[1:]):
                return ("OK", [(b"1 (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] "
                                b"{%d}" % len(raw), raw),
                               b" FLAGS (\\Seen)"])
            return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw)])
        if verb == "STORE":
            return ("OK", [b"1"])
        return ("BAD", [b"unsupported"])

    # -- convenience for assertions -----------------------------------------

    def searched(self):
        return [call for call in self.calls if call[0] == "uid"
                and call[1] == "SEARCH"]

    def stored(self):
        return [call for call in self.calls if call[0] == "uid"
                and call[1] == "STORE"]


class FakeSmtp:
    """An ``smtplib``-shaped stub for the adapter's own seam.

    ``reusable`` refills the scripted replies after a completed transaction, so
    one stub can serve several sends; a stub that must *not* answer (the lost
    final response) is built with ``reusable=False``.
    """

    DEFAULT_REPLIES = [(354, b"go ahead"), (250, b"queued as 42")]

    def __init__(self, *, replies=None, send_error: Optional[BaseException] = None,
                 mail_reply=(250, b"OK"), rcpt_reply=(250, b"OK"),
                 login_error: Optional[BaseException] = None,
                 reusable: bool = True):
        self.calls = []
        self.passwords = []
        self.recipients = []
        self.payloads = []
        self.reusable = reusable
        self._template = list(replies if replies is not None
                              else self.DEFAULT_REPLIES)
        self.replies = list(self._template)
        self.send_error = send_error
        self.mail_reply = mail_reply
        self.rcpt_reply = rcpt_reply
        self.login_error = login_error

    def ehlo_or_helo_if_needed(self):
        self.calls.append(("ehlo",))

    def login(self, user, password):
        self.calls.append(("login", user))
        self.passwords.append(password)
        if self.login_error is not None:
            raise self.login_error

    def mail(self, sender, options=()):
        self.calls.append(("mail", sender))
        return self.mail_reply

    def rcpt(self, recipient):
        self.calls.append(("rcpt", recipient))
        self.recipients.append(recipient)
        return self.rcpt_reply

    def putcmd(self, command):
        self.calls.append(("putcmd", command))

    def getreply(self):
        self.calls.append(("getreply",))
        if not self.replies:
            if self.reusable:
                self.replies = list(self._template)
            else:
                raise smtplib.SMTPServerDisconnected("connection closed")
        return self.replies.pop(0)

    def send(self, data):
        self.calls.append(("send", len(bytes(data))))
        if self.send_error is not None:
            raise self.send_error
        self.payloads.append(bytes(data))

    def quit(self):
        self.calls.append(("quit",))

    def close(self):
        self.calls.append(("close",))


def _install_imap(monkeypatch, fake: FakeImap, *, verification: str = "verified"):
    monkeypatch.setattr(
        mail_imap, "open_connection",
        lambda ctx, side: mail_imap.ImapSession(fake, verification=verification))
    return fake


def _install_smtp(monkeypatch, fake: FakeSmtp, *, verification: str = "verified"):
    monkeypatch.setattr(
        mail_smtp, "open_connection",
        lambda ctx, side: mail_smtp.SmtpSession(fake, verification=verification))
    return fake


def _message_set() -> Mapping[str, bytes]:
    return {"1": _raw_message(subject="first", body="one"),
            "2": _raw_message(subject="second", body="two")}


# ===========================================================================
# 1. registration and the risk catalogue
# ===========================================================================

def test_adapter_is_registered_and_its_actions_match_the_catalogue_exactly():
    from integrations.external.adapters.base import adapter_for

    adapter = adapter_for("email")
    assert isinstance(adapter, email_adapter.EmailAdapter)
    assert adapter.kind == "email"
    declared = {action for (kind, action) in risk.RISK_CATALOGUE
                if kind == "email"}
    assert set(adapter.actions) == declared
    assert set(adapter.write_actions) == {"messages.mark", "messages.send"}
    # ``attachments.save`` writes to the caller's workspace, not to the mailbox,
    # so it runs under the read class and needs no approval — the catalogue says
    # so and the adapter's declaration has to agree.
    assert risk.lookup("email", "attachments.save", write=False).level == "medium"
    assert risk.lookup("email", "messages.mark", write=True).level == "medium"
    send = risk.lookup("email", "messages.send", write=True)
    assert (send.level, send.leaves_boundary, send.write) == ("critical", True, True)


def test_the_adapter_refuses_an_action_it_does_not_declare():
    from integrations.external.adapters.base import AdapterError

    with pytest.raises(AdapterError) as caught:
        email_adapter.EmailAdapter().guard_action("messages.delete")
    assert caught.value.code == "unsupported_action"


# ===========================================================================
# 2. validate_config — offline
# ===========================================================================

def test_validate_config_accepts_the_registry_shape_unchanged():
    adapter = email_adapter.EmailAdapter()
    assert adapter.validate_config(dict(CONFIG)) == dict(CONFIG)


@pytest.mark.parametrize("from_addr", [
    "alice@localhost",          # not fully qualified
    "alice@x..y",               # empty label
    "alice@-bad.example.com",   # label starts with a hyphen
    "alice@example.c",          # one-letter suffix
    "alice@example.123",        # numeric suffix
])
def test_validate_config_refuses_an_unusable_sender_domain(from_addr):
    from integrations.external.errors import ExternalConnectionError
    adapter = email_adapter.EmailAdapter()
    config = {"imap": dict(IMAP_SIDE), "smtp": dict(SMTP_SIDE, from_addr=from_addr)}
    with pytest.raises(ExternalConnectionError) as caught:
        adapter.validate_config(config)
    assert caught.value.code == "field_invalid"


def test_validate_config_accepts_a_non_ascii_sender_domain():
    adapter = email_adapter.EmailAdapter()
    config = {"imap": dict(IMAP_SIDE),
              "smtp": dict(SMTP_SIDE, from_addr="alice@例え.jp")}
    assert adapter.validate_config(config)["smtp"]["from_addr"] == "alice@例え.jp"


@pytest.mark.parametrize("dirs", [
    ["mail", "mail/"], ["mail", "./mail"], ["mail", r"mail\\"],
])
def test_validate_config_refuses_a_duplicate_attachment_directory(dirs):
    from integrations.external.errors import ExternalConnectionError
    adapter = email_adapter.EmailAdapter()
    config = {"imap": dict(IMAP_SIDE), "smtp": dict(SMTP_SIDE),
              "attachment_dirs": dirs}
    with pytest.raises(ExternalConnectionError) as caught:
        adapter.validate_config(config)
    assert caught.value.code == "field_invalid"


def test_validate_config_refuses_a_connection_with_no_protocol_enabled():
    from integrations.external.errors import ExternalConnectionError
    adapter = email_adapter.EmailAdapter()
    with pytest.raises(ExternalConnectionError) as caught:
        adapter.validate_config({"imap": {"enabled": False},
                                 "smtp": {"enabled": False}})
    assert caught.value.code == "no_protocol"


# ===========================================================================
# 3. the policy boundary applied to raw TCP+TLS
# ===========================================================================

def test_the_policy_refuses_a_host_that_is_not_allowed():
    ctx = _ctx(policy=_policy(allow_hosts=frozenset()))
    with pytest.raises(MailError) as caught:
        guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                             verify_requested=True)
    assert caught.value.code == "target_not_allowed"
    assert caught.value.stage == "policy"


def test_the_policy_refuses_a_port_that_is_not_allowed():
    ctx = _ctx(policy=_policy(allow_ports=frozenset({143})))
    with pytest.raises(MailError) as caught:
        guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                             verify_requested=True)
    assert caught.value.code == "target_not_allowed"


def test_skipping_certificate_verification_is_refused_when_the_deployment_forbids_it():
    ctx = _ctx(policy=_policy(allow_verify_ssl_off=False))
    with pytest.raises(MailError) as caught:
        guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                             verify_requested=False)
    assert caught.value.code == "tls_verification_refused"
    assert caught.value.stage == "policy"


def test_skipping_certificate_verification_is_permitted_only_with_the_risk_recorded(
        monkeypatch):
    _dns_ok(monkeypatch)
    ctx = _ctx(policy=_policy(allow_verify_ssl_off=True))
    target = guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                                  verify_requested=False)
    assert target.verify is False
    assert target.verification == "unverified"
    # And with verification requested, the mode is the honest one.
    target = guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                                  verify_requested=True)
    assert target.verify is True and target.verification == "verified"


def test_disabling_tls_entirely_is_refused_unless_the_deployment_allows_plain_transport(
        monkeypatch):
    ctx = _ctx(policy=_policy(allow_insecure_http=False))
    with pytest.raises(MailError) as caught:
        guard.resolve_target(ctx, "imap.example.com", 143, tls=False,
                             verify_requested=True)
    assert caught.value.code == "tls_required"
    _dns_ok(monkeypatch)
    ctx = _ctx(policy=_policy(allow_insecure_http=True))
    target = guard.resolve_target(ctx, "imap.example.com", 143, tls=False,
                                  verify_requested=True)
    assert target.verification == "none"


def test_the_connection_uses_a_resolved_permitted_address_not_the_name(monkeypatch):
    """The policy filters resolved *addresses*, and only permitted ones dial."""
    import integrations.external.adapters.netpolicy as netpolicy

    def _fake(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             ("93.184.216.34", int(port or 0))),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             ("10.0.0.9", int(port or 0))),
        ]

    monkeypatch.setattr(netpolicy.socket, "getaddrinfo", _fake)
    ctx = _ctx(policy=_policy(allow_private=False))
    target = guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                                  verify_requested=True)
    assert target.addresses == ("93.184.216.34",)
    # A deployment that permits private ranges keeps both, but the address the
    # policy approved is still what is dialled.
    ctx = _ctx(policy=_policy(allow_private=True))
    target = guard.resolve_target(ctx, "imap.example.com", 993, tls=True,
                                  verify_requested=True)
    assert target.addresses == ("93.184.216.34", "10.0.0.9")


def test_redaction_removes_a_password_that_the_server_echoed():
    text = guard.redact("LOGIN failed for user alice with password %s" % PASSWORD,
                        [PASSWORD])
    assert PASSWORD not in text and "***" in text


# ===========================================================================
# 4. probe — per protocol, with partial success
# ===========================================================================

def test_probe_reports_ok_when_both_protocols_authenticate(monkeypatch):
    imap_fake = _install_imap(monkeypatch, FakeImap())
    smtp_fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "ok" and result.ok is True
    assert [stage.name for stage in result.stages] == ["imap", "smtp"]
    assert all(stage.status == "ok" for stage in result.stages)
    # The verification mode the attempt actually ran under is reported, so a
    # downgrade cannot hide inside a green badge.
    assert result.metadata["verification"] == {"imap": "verified",
                                              "smtp": "verified"}
    # Neither probe may send a message or touch a flag.
    assert not [call for call in smtp_fake.calls if call[0] == "mail"]
    assert not [call for call in imap_fake.calls if call[0] == "store"]


def test_probe_is_partial_when_smtp_fails_and_imap_passes(monkeypatch):
    _install_imap(monkeypatch, FakeImap())
    _install_smtp(monkeypatch, FakeSmtp(
        login_error=smtplib.SMTPAuthenticationError(535, b"bad credentials")))

    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "partial"
    assert result.ok is False
    by_name = {stage.name: stage for stage in result.stages}
    assert by_name["imap"].status == "ok"
    assert by_name["smtp"].status == "partial"
    assert by_name["smtp"].code == "auth_failed"
    assert by_name["smtp"].stage == "auth"
    # The per-side truth is kept alongside the partial verdict.
    assert result.metadata["imap"]["outcome"] == "ok"
    assert result.metadata["smtp"]["outcome"] == "failed"
    assert result.metadata["failed"] == ["smtp"]


def test_probe_is_partial_when_imap_fails_and_smtp_passes(monkeypatch):
    _install_imap(monkeypatch, FakeImap(
        login_error=mail_imap.imaplib.IMAP4.error(
            "LOGIN failed: [AUTHENTICATIONFAILED] Invalid credentials")))
    _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "partial"
    by_name = {stage.name: stage for stage in result.stages}
    assert by_name["imap"].status == "partial"
    assert by_name["imap"].code == "auth_failed"
    assert by_name["smtp"].status == "ok"


def test_probe_fails_when_every_enabled_protocol_fails(monkeypatch):
    _install_imap(monkeypatch, FakeImap(
        login_error=mail_imap.imaplib.IMAP4.error("LOGIN failed: nope")))
    _install_smtp(monkeypatch, FakeSmtp(
        login_error=smtplib.SMTPAuthenticationError(535, b"bad")))
    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "failed"
    assert [stage.status for stage in result.stages] == ["failed", "failed"]


def test_an_unenabled_protocol_is_skipped_not_failed(monkeypatch):
    _install_imap(monkeypatch, FakeImap())
    config = {"imap": dict(IMAP_SIDE),
              "smtp": {"enabled": False}, "attachment_dirs": []}
    result = email_adapter.EmailAdapter().probe(_ctx(config))
    assert result.outcome == "ok"
    by_name = {stage.name: stage for stage in result.stages}
    assert by_name["imap"].status == "ok"
    assert by_name["smtp"].status == "skipped"
    assert result.metadata["smtp"]["outcome"] == "skipped"


def test_only_smtp_configured_probes_only_smtp(monkeypatch):
    _install_smtp(monkeypatch, FakeSmtp())
    config = {"imap": {"enabled": False}, "smtp": dict(SMTP_SIDE),
              "attachment_dirs": []}
    result = email_adapter.EmailAdapter().probe(_ctx(config))
    assert result.outcome == "ok"
    assert result.metadata["enabled"] == ["smtp"]
    assert {stage.name for stage in result.stages} == {"imap", "smtp"}


def test_probe_reports_a_clear_dns_failure_with_the_host_port_and_tls(monkeypatch):
    _dns_failure(monkeypatch)
    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "failed"
    failed = result.failed_stage()
    assert failed.code == "dns_failure" and failed.stage == "network"
    assert "imap.example.com" in failed.detail
    assert "993" in failed.detail and "True" in failed.detail
    assert "gaierror" not in failed.detail


def test_probe_reports_an_unreachable_server_without_a_raw_socket_error(monkeypatch):
    _dns_ok(monkeypatch)
    monkeypatch.setattr(guard.socket, "create_connection",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            ConnectionRefusedError(61, "Connection refused")))
    result = email_adapter.EmailAdapter().probe(_ctx())
    assert result.outcome == "failed"
    failed = result.failed_stage()
    assert failed.code == "network_unreachable" and failed.stage == "network"
    assert PASSWORD not in json.dumps(result.as_dict())


def test_probe_refuses_to_skip_verification_without_connecting(monkeypatch):
    def _explode(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("the probe connected despite the policy refusal")

    monkeypatch.setattr(guard.socket, "create_connection", _explode)
    config = {"imap": dict(IMAP_SIDE, reject_unauthorized=False),
              "smtp": {"enabled": False}, "attachment_dirs": []}
    result = email_adapter.EmailAdapter().probe(
        _ctx(config, policy=_policy(allow_verify_ssl_off=False)))
    assert result.outcome == "failed"
    failed = result.failed_stage()
    assert failed.code == "tls_verification_refused" and failed.stage == "policy"
    assert result.metadata["imap"]["verification"] == "unverified"


def test_probe_never_persists_a_password_even_when_the_server_echoes_it(monkeypatch):
    echo = "535 Authentication failed for %s" % PASSWORD
    _install_imap(monkeypatch, FakeImap())
    _install_smtp(monkeypatch, FakeSmtp(
        login_error=smtplib.SMTPAuthenticationError(535, echo.encode())))
    result = email_adapter.EmailAdapter().probe(_ctx())
    raw = json.dumps(result.as_dict())
    assert PASSWORD not in raw
    assert "535 Authentication failed" not in raw
    # The remote text itself is never surfaced: the detail is the fixed sentence,
    # so there is nothing for a later layer to leak either.
    assert result.metadata["smtp"]["detail"] == (
        "the server rejected the username or password")


def test_the_password_is_passed_to_the_protocols_and_nothing_else(monkeypatch):
    imap_fake = _install_imap(monkeypatch, FakeImap())
    smtp_fake = _install_smtp(monkeypatch, FakeSmtp())
    email_adapter.EmailAdapter().probe(_ctx())
    assert imap_fake.passwords == [PASSWORD]
    assert smtp_fake.passwords == [PASSWORD]


# ===========================================================================
# 5. read actions
# ===========================================================================

def test_search_reads_the_mailbox_read_only_and_does_not_mark_anything(monkeypatch):
    fake = _install_imap(monkeypatch, FakeImap(messages=_message_set()))
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.search",
                                                {"limit": 10})
    assert result.ok is True
    assert result.data["count"] == 2
    assert result.data["uids"] == ["2", "1"]
    assert result.data["messages"][0]["uid"] == "2"
    # Selecting read-only is what keeps ``\Seen`` from being set implicitly, and
    # no STORE is issued by a read.
    assert ("select", "INBOX", True) in fake.calls
    assert fake.stored() == []


def test_search_builds_the_expected_imap_criteria(monkeypatch):
    fake = _install_imap(monkeypatch, FakeImap(messages={}))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(), "messages.search",
        {"unseen": True, "from": "boss@example.com", "subject": "urgent",
         "since": "2026-01-02"})
    assert result.ok is True, result.message
    assert fake.searched() == [
        ("uid", "SEARCH",
         (None, "UNSEEN", "FROM", "boss@example.com", "SUBJECT", "urgent",
          "SINCE", "2-Jan-2026"))]


def test_search_refuses_a_bad_date_without_opening_a_connection(monkeypatch):
    fake = _install_imap(monkeypatch, FakeImap())
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.search",
                                                {"since": "02/01/2026"})
    assert result.ok is False and result.code == "invalid_date"
    assert fake.calls == []


def test_read_returns_the_body_and_the_attachment_metadata(monkeypatch):
    raw = _raw_message(subject="report", body="see attached",
                       attachments=[("notes.txt", b"payload", "text/plain")])
    _install_imap(monkeypatch, FakeImap(messages={"5": raw}))
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.read",
                                                {"uid": 5})
    assert result.ok is True, result.message
    assert result.data["subject"] == "report"
    assert result.data["body"].strip() == "see attached"
    assert result.data["attachments"][0]["filename"] == "notes.txt"
    assert result.data["attachments"][0]["size"] == len(b"payload")


def test_read_strips_html_and_never_returns_live_markup(monkeypatch):
    message = EmailMessage(policy=email.policy.SMTP.clone(cte_type="7bit"))
    message["From"] = "sender@example.com"
    message["To"] = "alice@example.com"
    message["Subject"] = "html"
    message.set_content('<html><body><script>steal()</script>'
                        '<p>Read <b>this</b></p>'
                        '<img src="http://tracker.example.com/x"></body></html>',
                        subtype="html")
    _install_imap(monkeypatch, FakeImap(messages={"1": message.as_bytes()}))
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.read",
                                                {"uid": 1})
    assert result.ok is True, result.message
    assert "<script>" not in result.data["body"]
    assert "steal()" not in result.data["body"]
    assert "Read" in result.data["body"] and "this" in result.data["body"]
    assert "tracker.example.com" not in result.data["body"]
    assert result.data["html_found"] is True


def test_read_refuses_a_non_numeric_uid(monkeypatch):
    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("uid validation happened after connecting")

    monkeypatch.setattr(mail_imap, "open_connection", _explode)
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.read",
                                                {"uid": "1,*"})
    assert result.ok is False and result.code == "invalid_uid"


def test_mark_is_a_separate_write_action(monkeypatch):
    fake = _install_imap(monkeypatch, FakeImap(messages=_message_set()))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(), "messages.mark", {"uids": ["1", "2"], "seen": False})
    assert result.ok is True, result.message
    assert result.data["uids"] == ["1", "2"] and result.data["seen"] is False
    # Marking selects the mailbox writable; reading never does.
    assert ("select", "INBOX", False) in fake.calls
    assert fake.stored() == [("uid", "STORE", ("1,2", "-FLAGS", "(\\Seen)"))]


# ===========================================================================
# 6. attachments.save — the real path boundary
# ===========================================================================

def _attachment_message(name: str = "notes.txt", data: bytes = b"payload") -> bytes:
    return _raw_message(attachments=[(name, data, "text/plain")])


def test_attachment_is_saved_inside_the_configured_directory(tmp_path, monkeypatch):
    raw = _attachment_message()
    _install_imap(monkeypatch, FakeImap(messages={"1": raw}))
    workspace = tmp_path / "workspace"
    allowed = workspace / "mail"
    allowed.mkdir(parents=True)
    result = email_adapter.EmailAdapter().invoke(
        _ctx(workspace=workspace), "attachments.save",
        {"uid": "1", "dir": "mail"})
    assert result.ok is True, result.message
    saved = result.data["saved"][0]
    assert saved["filename"] == "notes.txt"
    assert os.path.isfile(saved["path"])
    with open(saved["path"], "rb") as handle:
        assert handle.read() == b"payload"
    assert os.path.dirname(saved["path"]) == os.path.realpath(str(allowed))


def test_attachment_save_refuses_a_traversal_directory(tmp_path, monkeypatch):
    _install_imap(monkeypatch, FakeImap(messages={"1": _attachment_message()}))
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = email_adapter.EmailAdapter().invoke(
        _ctx(workspace=workspace), "attachments.save",
        {"uid": "1", "dir": "../outside"})
    assert result.ok is False
    assert result.code in ("outside_attachment_dirs", "path_not_allowed")
    assert os.listdir(str(outside)) == []


def test_attachment_save_refuses_an_absolute_directory(tmp_path, monkeypatch):
    _install_imap(monkeypatch, FakeImap(messages={"1": _attachment_message()}))
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    outside = tmp_path / "absolute"
    outside.mkdir()
    result = email_adapter.EmailAdapter().invoke(
        _ctx(workspace=workspace), "attachments.save",
        {"uid": "1", "dir": str(outside)})
    assert result.ok is False and result.code == "outside_attachment_dirs"
    assert os.listdir(str(outside)) == []


def test_attachment_save_refuses_a_symlinked_directory(tmp_path, monkeypatch):
    _install_imap(monkeypatch, FakeImap(messages={"1": _attachment_message()}))
    workspace = tmp_path / "workspace"
    allowed = workspace / "mail"
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(allowed / "link"))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(workspace=workspace), "attachments.save",
        {"uid": "1", "dir": "mail/link"})
    assert result.ok is False and result.code == "outside_attachment_dirs"
    assert os.listdir(str(outside)) == []


def test_attachment_save_never_trusts_a_message_file_name(tmp_path, monkeypatch):
    raw = _attachment_message(name="../../escape.txt")
    _install_imap(monkeypatch, FakeImap(messages={"1": raw}))
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    result = email_adapter.EmailAdapter().invoke(
        _ctx(workspace=workspace), "attachments.save",
        {"uid": "1", "dir": "mail"})
    assert result.ok is True, result.message
    saved = result.data["saved"][0]
    # The name from the message is reduced to its base name; the write stays
    # inside the allowed directory.
    assert saved["filename"] == "escape.txt"
    assert os.path.dirname(saved["path"]) == os.path.realpath(
        str(workspace / "mail"))
    assert not (tmp_path / "escape.txt").exists()


def test_attachment_save_refuses_when_no_directory_is_configured(tmp_path,
                                                                monkeypatch):
    _install_imap(monkeypatch, FakeImap(messages={"1": _attachment_message()}))
    config = {"imap": dict(IMAP_SIDE), "smtp": dict(SMTP_SIDE),
              "attachment_dirs": []}
    result = email_adapter.EmailAdapter().invoke(
        _ctx(config, workspace=tmp_path), "attachments.save",
        {"uid": "1", "filename": "notes.txt"})
    assert result.ok is False and result.code == "no_attachment_dir"


def test_attachment_read_refuses_a_symlinked_file(tmp_path):
    workspace = tmp_path / "workspace"
    allowed = workspace / "mail"
    allowed.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"another tenant's file")
    os.symlink(str(secret), str(allowed / "link.txt"))
    ctx = _ctx(workspace=workspace)
    with pytest.raises(MailError) as caught:
        guard.read_attachment(ctx, "mail/link.txt", max_bytes=1024)
    assert caught.value.code == "outside_attachment_dirs"
    # A permitted file in the same directory still reads.
    (allowed / "ok.txt").write_bytes(b"fine")
    assert guard.read_attachment(ctx, "mail/ok.txt",
                                 max_bytes=1024)["data"] == b"fine"


def test_attachment_write_refuses_a_filename_that_is_a_path(tmp_path):
    ctx = _ctx(workspace=tmp_path)
    with pytest.raises(MailError) as caught:
        guard.write_attachment(ctx, directory="mail", filename="../x.txt",
                               data=b"x")
    assert caught.value.code == "invalid_filename"


# ===========================================================================
# 7. messages.send — bounds, MIME, approval
# ===========================================================================

def _send_params(**overrides) -> Mapping[str, Any]:
    value = {"to": ["boss@example.com"], "subject": "status",
             "body": "all good"}
    value.update(overrides)
    return value


def _wire_message(fake: FakeSmtp):
    """The delivered message parsed from the raw, dot-stuffed payload.

    ``compat32`` on purpose: the point is to check the *wire* form (RFC 2047
    subject, no Bcc header), not a decoded convenience view.
    """
    payload = fake.payloads[0]
    if payload.endswith(b"\r\n.\r\n"):
        payload = payload[:-len(b"\r\n.\r\n")]
    return email.message_from_bytes(payload)


def test_send_builds_a_correct_message_and_keeps_bcc_out_of_the_headers(
        monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send",
        _send_params(to=["boss@example.com"],
                     cc=["peer@example.com"],
                     bcc=["hidden@example.com"],
                     subject="季度报告 / Q3",
                     body="附件在此",
                     attachments=[{"filename": "notes.txt", "content": "hello"}]))
    assert result.ok is True, result.message
    message = _wire_message(fake)
    assert message["From"] == "alice@example.com"
    assert message["To"] == "boss@example.com"
    assert message["Cc"] == "peer@example.com"
    # The subject is RFC 2047 encoded, not raw 8-bit.
    assert message["Subject"].startswith("=?utf-8?")
    assert "季度报告" not in fake.payloads[0].decode("utf-8", "replace")
    # A Bcc is an envelope recipient only.
    assert message["Bcc"] is None
    assert b"hidden@example.com" not in fake.payloads[0]
    assert "hidden@example.com" in fake.recipients
    # The attachment carries the type its name implies.
    parts = [part for part in message.walk()
             if part.get_filename() == "notes.txt"]
    assert parts and parts[0].get_content_type() == "text/plain"
    assert parts[0].get_payload(decode=True) == b"hello"
    # And the body is transfer-encoded rather than sent as raw 8-bit bytes.
    assert b"=E9=99=84" in fake.payloads[0] or b"6ZmE5Lu2" in fake.payloads[0]


def test_send_never_retries_and_delivers_exactly_once(monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.ok is True
    assert [call for call in fake.calls if call[0] == "send"] == [
        ("send", len(fake.payloads[0]))]
    assert len(fake.payloads) == 1


def test_send_refuses_an_invalid_recipient_before_dialling(monkeypatch,
                                                           acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send",
        _send_params(to=["not-an-address"]))
    assert result.ok is False and result.code == "invalid_recipient"
    assert fake.calls == []


def test_send_bounds_the_number_of_recipients(monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    ctx = _ctx(limits={"mail_max_recipients": 2},
               extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(to=["a@example.com", "b@example.com", "c@example.com"]))
    assert result.ok is False and result.code == "too_many_recipients"
    assert fake.calls == []


def test_send_bounds_the_attachment_size(monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp())
    ctx = _ctx(limits={"mail_max_attachment_bytes": 4},
               extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(attachments=[{"filename": "big.bin",
                                   "content": "0123456789"}]))
    assert result.ok is False and result.code == "attachment_too_large"


def test_send_refuses_to_change_the_sender_from_the_configuration(
        monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send",
        _send_params(from_addr="someone@else.example.com"))
    assert result.ok is False and result.code == "from_addr_fixed"


def test_send_is_refused_without_an_approval(monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(_ctx(), "messages.send",
                                                _send_params())
    assert result.ok is False
    assert result.code == "approval_required" and result.stage == "policy"
    assert fake.calls == []


@pytest.mark.parametrize("approval,code", [
    (_approval(approved=False), "approval_required"),
    (_approval(revoked=True), "approval_revoked"),
    (_approval(approver_user_id=""), "approval_incomplete"),
    (_approval(approver_user_id="u-alice"), "approval_not_separated"),
    (_approval(digest=""), "approval_incomplete"),
    (_approval(expires_at=int(time.time()) - 10), "approval_expired"),
    (_approval(scope="always"), "approval_scope_refused"),
])
def test_send_refuses_a_defective_approval(monkeypatch, acknowledged,
                                           approval, code):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": approval}), "messages.send", _send_params())
    assert result.ok is False and result.code == code
    assert fake.calls == []


def test_send_is_refused_while_the_deployment_has_not_acknowledged_critical_risk(
        monkeypatch):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.ok is False and result.code == "risk_not_acknowledged"
    assert fake.calls == []


def test_send_requires_an_attachment_digest_so_the_approval_binds_the_content(
        tmp_path, monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp())
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    (workspace / "mail" / "notes.txt").write_bytes(b"approved content")
    ctx = _ctx(workspace=workspace, extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(attachments=[{"path": "mail/notes.txt"}]))
    assert result.ok is False and result.code == "attachment_digest_required"


def test_send_refuses_an_attachment_whose_content_changed_after_approval(
        tmp_path, monkeypatch, acknowledged):
    import hashlib
    _install_smtp(monkeypatch, FakeSmtp())
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    target = workspace / "mail" / "notes.txt"
    target.write_bytes(b"approved content")
    approved = hashlib.sha256(b"approved content").hexdigest()
    target.write_bytes(b"different content")
    ctx = _ctx(workspace=workspace, extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(attachments=[{"path": "mail/notes.txt",
                                   "sha256": approved}]))
    assert result.ok is False and result.code == "attachment_changed"


def test_send_attaches_a_permitted_workspace_file(tmp_path, monkeypatch,
                                                  acknowledged):
    import hashlib
    fake = _install_smtp(monkeypatch, FakeSmtp())
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    data = b"quarterly numbers"
    (workspace / "mail" / "report.csv").write_bytes(data)
    ctx = _ctx(workspace=workspace, extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(attachments=[{"path": "mail/report.csv",
                                   "sha256": hashlib.sha256(data).hexdigest()}]))
    assert result.ok is True, result.message
    message = _wire_message(fake)
    attached = [part for part in message.walk()
                if part.get_filename() == "report.csv"]
    assert attached and attached[0].get_content_type() == "text/csv"


def test_send_refuses_an_attachment_outside_the_allowed_directories(
        tmp_path, monkeypatch, acknowledged):
    import hashlib
    _install_smtp(monkeypatch, FakeSmtp())
    workspace = tmp_path / "workspace"
    (workspace / "mail").mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"not yours")
    ctx = _ctx(workspace=workspace, extra={"approval": _approval()})
    result = email_adapter.EmailAdapter().invoke(
        ctx, "messages.send",
        _send_params(attachments=[{
            "path": str(outside),
            "sha256": hashlib.sha256(b"not yours").hexdigest()}]))
    assert result.ok is False and result.code == "outside_attachment_dirs"


# ===========================================================================
# 8. outcome_unknown, no retry, duplicate-submit protection
# ===========================================================================

def test_a_lost_final_response_is_reported_unknown_and_not_retried(
        monkeypatch, acknowledged):
    # The server accepted the DATA command and then never answered the final
    # dot: the message may have been delivered.
    fake = _install_smtp(monkeypatch, FakeSmtp(replies=[(354, b"go ahead")],
                                               reusable=False))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.ok is False and result.outcome_unknown is True
    assert result.code == "send_unknown"
    assert len(fake.payloads) == 1
    # No second conversation was opened: nothing retries.
    assert [call for call in fake.calls if call[0] == "send"] == [
        ("send", len(fake.payloads[0]))]


def test_a_timeout_after_the_message_was_written_is_reported_unknown(
        monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp(
        send_error=socket.timeout("timed out")))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.outcome_unknown is True and result.code == "send_unknown"
    assert len(fake.payloads) == 0


def test_a_connection_lost_before_the_message_is_a_definitive_failure(
        monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp(mail_reply=(421, b"service unavailable")))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.ok is False and result.outcome_unknown is False
    assert result.code == "sender_refused"


def test_a_server_rejection_of_the_message_is_a_definitive_failure(
        monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp(
        replies=[(354, b"go ahead"), (554, b"rejected: spam")]))
    result = email_adapter.EmailAdapter().invoke(
        _ctx(extra={"approval": _approval()}), "messages.send", _send_params())
    assert result.ok is False and result.outcome_unknown is False
    assert result.code == "send_rejected"


def test_a_cancel_race_after_data_before_the_message_goes_out_is_clean(
        monkeypatch, acknowledged):
    import threading
    cancel = threading.Event()

    class _CancellingSmtp(FakeSmtp):
        def putcmd(self, command):
            super().putcmd(command)
            cancel.set()  # the user hit cancel while DATA was in flight

    fake = _install_smtp(monkeypatch, _CancellingSmtp())
    ctx = _ctx(extra={"approval": _approval()})
    ctx.cancel = cancel
    result = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                                _send_params())
    assert result.ok is False and result.code == "cancelled"
    assert result.outcome_unknown is False
    # Nothing of the message was written: the abort happened before the content.
    assert fake.payloads == []


def test_the_same_approved_message_is_not_sent_twice(monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    ctx = _ctx(extra={"approval": _approval()})
    first = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                               _send_params())
    second = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                                _send_params())
    assert first.ok is True
    assert second.ok is False and second.code == "duplicate_submit"
    assert len(fake.payloads) == 1


def test_a_definitive_failure_releases_the_guard_for_a_corrected_retry(
        monkeypatch, acknowledged):
    failing = _install_smtp(monkeypatch, FakeSmtp(
        replies=[(354, b"go"), (554, b"rejected")]))
    ctx = _ctx(extra={"approval": _approval()})
    first = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                               _send_params())
    assert first.code == "send_rejected"
    # A corrected attempt with the same parameters is allowed, because the first
    # one definitely did not leave the boundary.
    corrected = _install_smtp(monkeypatch, FakeSmtp())
    second = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                                _send_params())
    assert second.ok is True
    assert len(corrected.payloads) == 1
    # The rejected attempt was not retried on its own connection.
    assert len(failing.payloads) == 1


def test_an_unknown_send_keeps_the_guard_so_a_resent_message_is_refused(
        monkeypatch, acknowledged):
    _install_smtp(monkeypatch, FakeSmtp(replies=[(354, b"go")],
                                        reusable=False))
    ctx = _ctx(extra={"approval": _approval()})
    first = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                               _send_params())
    assert first.outcome_unknown is True
    _install_smtp(monkeypatch, FakeSmtp())
    second = email_adapter.EmailAdapter().invoke(ctx, "messages.send",
                                                _send_params())
    assert second.ok is False and second.code == "duplicate_submit"


def test_a_different_message_is_not_blocked_by_the_guard(monkeypatch, acknowledged):
    fake = _install_smtp(monkeypatch, FakeSmtp())
    adapter = email_adapter.EmailAdapter()
    first = adapter.invoke(_ctx(extra={"approval": _approval(digest="a" * 64)}),
                           "messages.send", _send_params(subject="one"))
    second = adapter.invoke(_ctx(extra={"approval": _approval(digest="b" * 64)}),
                            "messages.send", _send_params(subject="two"))
    assert first.ok is True and second.ok is True
    assert len(fake.payloads) == 2


# ===========================================================================
# 9. the real imaplib / smtplib path over an in-memory socket
# ===========================================================================

class ScriptedSocket:
    """An in-memory socket whose replies come from the commands written to it.

    This is what lets the tests below run the real ``imaplib``/``smtplib``
    conversation (and the pinned-socket connectors) without a server: replies are
    derived from each command line, so random IMAP tags and the SMTP dialogue are
    both handled.
    """

    def __init__(self, initial: bytes = b"", handler=None):
        self._inbound = bytearray(initial)
        self._handler = handler
        self.sent = bytearray()
        self.timeout = None
        self.closed = False

    def makefile(self, mode="rb", *args, **kwargs):
        return self

    def gettimeout(self):
        return self.timeout

    def readline(self, *args):
        index = self._inbound.find(b"\n")
        if index < 0:
            line = bytes(self._inbound)
            self._inbound.clear()
            return line
        line = bytes(self._inbound[:index + 1])
        del self._inbound[:index + 1]
        return line

    def read(self, size=-1):
        if size is None or size < 0:
            data = bytes(self._inbound)
            self._inbound.clear()
            return data
        data = bytes(self._inbound[:size])
        del self._inbound[:size]
        return data

    def sendall(self, data):
        data = bytes(data)
        self.sent += data
        if self._handler is None:
            return
        for line in data.split(b"\r\n"):
            if not line:
                continue
            self._inbound += self._handler(line)

    def send(self, data):
        self.sendall(data)
        return len(data)

    def recv(self, size):
        return self.read(size)

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True

    def shutdown(self, *args):
        pass

    def fileno(self):
        raise OSError("the scripted socket has no file descriptor")


def _imap_socket(*, login_ok: bool = True) -> ScriptedSocket:
    def handler(line):
        parts = line.split()
        tag, verb = parts[0], (parts[1].upper() if len(parts) > 1 else b"")
        if verb == b"CAPABILITY":
            return (b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag
                    + b" OK CAPABILITY completed\r\n")
        if verb == b"LOGIN":
            if login_ok:
                return tag + b" OK LOGIN completed\r\n"
            return tag + b" NO [AUTHENTICATIONFAILED] Invalid credentials\r\n"
        if verb == b"LOGOUT":
            return b"* BYE bye\r\n" + tag + b" OK LOGOUT completed\r\n"
        return tag + b" BAD unknown command\r\n"

    return ScriptedSocket(b"* OK [CAPABILITY IMAP4rev1] ready\r\n", handler)


def test_the_real_imap_connection_logs_in_and_out_over_the_pinned_socket(
        monkeypatch):
    _dns_ok(monkeypatch)
    config = {"imap": dict(IMAP_SIDE, tls=False, port=143),
              "smtp": {"enabled": False}, "attachment_dirs": []}
    scripted = _imap_socket()
    monkeypatch.setattr(guard, "connect_raw",
                        lambda ctx, target, timeout=None: scripted)
    result = email_adapter.EmailAdapter().probe(
        _ctx(config, policy=_policy(allow_insecure_http=True)))
    assert result.outcome == "ok", json.dumps(result.as_dict())
    assert result.metadata["imap"]["verification"] == "none"
    assert result.stages[0].status == "ok"
    assert b"LOGIN" in bytes(scripted.sent)
    assert b"alice@example.com" in bytes(scripted.sent)


def test_the_real_imap_connection_reports_a_rejected_login(monkeypatch):
    _dns_ok(monkeypatch)
    config = {"imap": dict(IMAP_SIDE, tls=False, port=143),
              "smtp": {"enabled": False}, "attachment_dirs": []}
    monkeypatch.setattr(guard, "connect_raw",
                        lambda ctx, target, timeout=None: _imap_socket(
                            login_ok=False))
    result = email_adapter.EmailAdapter().probe(
        _ctx(config, policy=_policy(allow_insecure_http=True)))
    assert result.outcome == "failed"
    failed = result.failed_stage()
    assert failed.code == "auth_failed" and failed.stage == "auth"
    assert PASSWORD not in json.dumps(result.as_dict())


def _smtp_socket(*, final_reply: bool = True, auth: bool = True) -> ScriptedSocket:
    state = {"data": False}

    def handler(line):
        if state["data"]:
            if line == b".":
                state["data"] = False
                if not final_reply:
                    return b""
                return b"250 OK queued as 42\r\n"
            return b""
        verb = line.split()[0].upper()
        if verb in (b"EHLO", b"HELO"):
            caps = b"250-fake.test hello\r\n250 SIZE 10485760\r\n"
            if auth:
                caps = (b"250-fake.test hello\r\n250-SIZE 10485760\r\n"
                        b"250 AUTH PLAIN\r\n")
            return caps
        if verb == b"AUTH":
            return b"235 Authentication successful\r\n"
        if verb == b"MAIL":
            return b"250 OK\r\n"
        if verb == b"RCPT":
            return b"250 OK\r\n"
        if verb == b"DATA":
            state["data"] = True
            return b"354 End data with <CR><LF>.<CR><LF>\r\n"
        if verb == b"QUIT":
            return b"221 Bye\r\n"
        return b"250 OK\r\n"

    return ScriptedSocket(b"220 fake.test ESMTP ready\r\n", handler)


def _plain_smtp_config(**overrides) -> Mapping[str, Any]:
    side = {"enabled": True, "host": "smtp.example.com", "port": 25,
            "user": "alice@example.com", "reject_unauthorized": True,
            "from_addr": "alice@example.com", "tls_mode": "none"}
    side.update(overrides)
    return {"imap": {"enabled": False}, "smtp": side, "attachment_dirs": []}


def test_the_real_smtp_connection_delivers_over_the_pinned_socket(
        monkeypatch, acknowledged):
    _dns_ok(monkeypatch)
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "client.test")
    scripted = _smtp_socket()
    monkeypatch.setattr(guard, "connect_raw",
                        lambda ctx, target, timeout=None: scripted)
    result = email_adapter.EmailAdapter().invoke(
        _ctx(_plain_smtp_config(), policy=_policy(allow_insecure_http=True),
             extra={"approval": _approval()}),
        "messages.send", _send_params(subject="hello", body="body"))
    assert result.ok is True, result.message
    wire = bytes(scripted.sent)
    # smtplib lower-cases the command verbs; the envelope is what matters.
    assert b"mail from:<alice@example.com>" in wire
    assert b"rcpt to:<boss@example.com>" in wire
    assert b"data" in wire
    # The message really was dot-terminated, which is what makes a definitive
    # failure and an unknown one distinguishable.
    assert b"\r\n.\r\n" in wire
    assert result.data["verification"] == "none"


def test_the_real_smtp_connection_reports_unknown_when_the_final_reply_is_lost(
        monkeypatch, acknowledged):
    _dns_ok(monkeypatch)
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "client.test")
    scripted = _smtp_socket(final_reply=False)
    monkeypatch.setattr(guard, "connect_raw",
                        lambda ctx, target, timeout=None: scripted)
    result = email_adapter.EmailAdapter().invoke(
        _ctx(_plain_smtp_config(), policy=_policy(allow_insecure_http=True),
             extra={"approval": _approval()}),
        "messages.send", _send_params())
    assert result.ok is False and result.outcome_unknown is True
    assert result.code == "send_unknown"


# ===========================================================================
# 10. the own-mailbox tool provider, dispatcher and service path
# ===========================================================================

@pytest.fixture
def mailbox(tmp_path, monkeypatch):
    """A tenant with two members, each owning a personal mailbox connection."""
    from config import conf
    stack = build_identity(tmp_path)
    db_path = str(tmp_path / "identity.db")
    monkeypatch.setitem(conf(), "identity_db_path", db_path)
    from integrations.external import service as service_module
    service_module._SERVICE_CACHE.clear()

    from integrations.external.service import ExternalConnectionService
    service = ExternalConnectionService(stack.service)
    members = {}
    for username in ("alice", "bob"):
        role = stack.service.create_role(
            actor_user_id=stack.root, tenant_id=stack.tenant_id,
            code="mail_%s" % username, name="Mail user %s" % username,
            permissions=["chat.use"])
        members[username] = stack.member(username, [role["code"]])
    connections = {}
    for username, user_id in members.items():
        card = service.create_connection(
            actor_user_id=user_id, scope="personal", tenant_id=stack.tenant_id,
            kind="email", name="%s mailbox" % username, config=dict(CONFIG),
            secrets={"imap_password": PASSWORD, "smtp_password": PASSWORD})
        connections[username] = card["id"]
    return {"stack": stack, "service": service, "members": members,
            "connections": connections, "tenant": stack.tenant_id}


def _open_email(monkeypatch, **overrides):
    from config import conf
    block = {
        "allow_hosts": list(ALLOWED),
        "allow_private": False,
        "risk": {"acknowledged_levels": ["critical"]},
        "readiness": {"email": {"test": True, "read_execute": True,
                                "write_execute": True}},
    }
    block.update(overrides)
    monkeypatch.setitem(conf(), "external_connections", block)
    reset_policy_cache()
    return block


def test_the_provider_offers_only_the_callers_own_mailbox(mailbox, monkeypatch):
    _open_email(monkeypatch)
    alice_tools = email_adapter._email_tool_provider(mailbox["tenant"],
                                                     mailbox["members"]["alice"])
    bob_tools = email_adapter._email_tool_provider(mailbox["tenant"],
                                                   mailbox["members"]["bob"])
    alice_ids = {binding.connection_id for binding in alice_tools}
    bob_ids = {binding.connection_id for binding in bob_tools}
    assert alice_ids and bob_ids
    assert alice_ids.isdisjoint(bob_ids)
    assert alice_ids == {mailbox["connections"]["alice"]}
    assert bob_ids == {mailbox["connections"]["bob"]}
    for binding in alice_tools:
        assert binding.scope == "personal"
        assert binding.tool.metadata["owner_user_id"] == mailbox["members"]["alice"]
        assert binding.connection_name == "alice mailbox"


def test_a_subject_without_a_tenant_or_user_gets_no_mailbox_tools(mailbox,
                                                                 monkeypatch):
    _open_email(monkeypatch)
    owner = mailbox["members"]["alice"]
    assert email_adapter._email_tool_provider(mailbox["tenant"], "") == []
    assert email_adapter._email_tool_provider("", owner) == []
    assert email_adapter._email_tool_provider(None, owner) == []
    # A user id from another tenant resolves to nothing, not to someone's mail.
    assert email_adapter._email_tool_provider("tenant-other", owner) == []


def test_write_actions_are_offered_only_when_the_write_class_is_open(mailbox,
                                                                    monkeypatch):
    block = _open_email(monkeypatch)
    owner = mailbox["members"]["alice"]

    block["readiness"]["email"] = {"read_execute": True}
    actions = {binding.tool.action
               for binding in email_adapter._email_tool_provider(
                   mailbox["tenant"], owner)}
    assert actions and actions.isdisjoint({"messages.mark", "messages.send"})

    block["readiness"]["email"] = {"read_execute": True, "write_execute": True}
    tools = email_adapter._email_tool_provider(mailbox["tenant"], owner)
    actions = {binding.tool.action for binding in tools}
    assert actions == set(email_adapter.ACTIONS)
    risk_of = {binding.tool.action: binding.tool.metadata["risk"]
               for binding in tools}
    assert risk_of["messages.send"]["level"] == "critical"
    assert risk_of["messages.send"]["approval_required"] is True
    assert risk_of["messages.send"]["leaves_boundary"] is True
    assert risk_of["messages.read"]["approval_required"] is False
    # The tool name carries the connection it is bound to, so the model cannot
    # pick someone else's mailbox by name.
    assert all(binding.connection_id == mailbox["connections"]["alice"]
               for binding in tools)


def test_no_action_is_offered_when_every_class_is_closed(mailbox, monkeypatch):
    _open_email(monkeypatch, readiness={})
    assert email_adapter._email_tool_provider(
        mailbox["tenant"], mailbox["members"]["alice"]) == []


def test_the_dispatcher_routes_the_call_back_through_the_service(mailbox):
    from integrations.external.tools import ExternalTool, ToolBinding

    recorded = {}

    class _Service:
        def invoke_action(self, connection_id, action, params, **kwargs):
            recorded.update({"connection_id": connection_id, "action": action,
                             "params": dict(params), "kwargs": kwargs})
            return "called"

    binding = ToolBinding(tool=ExternalTool(name="email.messages.read.x",
                                            kind="email",
                                            action="messages.read"),
                          connection_id="conn_local")
    result = email_adapter._email_dispatcher(
        _Service(), binding, {"uid": "1"}, tenant_id=mailbox["tenant"],
        actor_user_id=mailbox["members"]["alice"], agent_id="a1", run_id="r1")
    assert result == "called"
    assert recorded["connection_id"] == "conn_local"
    assert recorded["action"] == "messages.read"
    assert recorded["kwargs"]["actor_user_id"] == mailbox["members"]["alice"]
    assert recorded["kwargs"]["tenant_id"] == mailbox["tenant"]
    assert recorded["kwargs"]["agent_id"] == "a1"


def test_a_read_action_runs_end_to_end_through_the_service(mailbox, monkeypatch):
    _open_email(monkeypatch)
    fake = FakeImap(messages=_message_set())
    _install_imap(monkeypatch, fake)
    result = mailbox["service"].invoke_action(
        mailbox["connections"]["alice"], "messages.search", {"limit": 5},
        actor_user_id=mailbox["members"]["alice"], tenant_id=mailbox["tenant"])
    assert result.ok is True, result.message
    assert result.data["uids"] == ["2", "1"]


def test_a_send_through_the_service_is_refused_without_an_approval(mailbox,
                                                                  monkeypatch):
    _open_email(monkeypatch)
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = mailbox["service"].invoke_action(
        mailbox["connections"]["alice"], "messages.send", dict(_send_params()),
        actor_user_id=mailbox["members"]["alice"], tenant_id=mailbox["tenant"])
    assert result.ok is False
    assert result.code == "approval_required" and result.stage == "policy"
    assert fake.calls == []


def test_a_send_through_the_service_is_refused_when_critical_is_not_acknowledged(
        mailbox, monkeypatch):
    block = _open_email(monkeypatch)
    block["risk"] = {}
    fake = _install_smtp(monkeypatch, FakeSmtp())
    result = mailbox["service"].invoke_action(
        mailbox["connections"]["alice"], "messages.send", dict(_send_params()),
        actor_user_id=mailbox["members"]["alice"], tenant_id=mailbox["tenant"],
        approval=dict(_approval()))
    assert result.ok is False and result.code == "risk_not_acknowledged"
    assert fake.calls == []


def test_a_read_through_the_service_is_refused_when_read_execute_is_closed(
        mailbox, monkeypatch):
    _open_email(monkeypatch, readiness={"email": {"test": True}})
    fake = _install_imap(monkeypatch, FakeImap(messages=_message_set()))
    result = mailbox["service"].invoke_action(
        mailbox["connections"]["alice"], "messages.search", {"limit": 5},
        actor_user_id=mailbox["members"]["alice"], tenant_id=mailbox["tenant"])
    assert result.ok is False and result.code == "execution_not_available"
    assert fake.calls == []


def test_a_test_probe_through_the_service_reports_partial(mailbox, monkeypatch):
    _open_email(monkeypatch)
    _install_imap(monkeypatch, FakeImap())
    _install_smtp(monkeypatch, FakeSmtp(
        login_error=smtplib.SMTPAuthenticationError(535, b"nope")))
    payload = mailbox["service"].probe_connection(
        mailbox["connections"]["alice"],
        actor_user_id=mailbox["members"]["alice"], tenant_id=mailbox["tenant"])
    assert payload["outcome"] == "partial"
    assert payload["recorded"] is True
    stages = {stage["name"]: stage for stage in payload["stages"]}
    assert stages["imap"]["status"] == "ok"
    assert stages["smtp"]["status"] == "partial"
    assert payload["metadata"]["smtp"]["outcome"] == "failed"
