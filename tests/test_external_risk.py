# encoding:utf-8
"""Action risk catalogue, canonicalisation and approval binding (task group 4).

Subject under test: ``integrations/external/risk.py``.

The spec requires the risk classification to come from *our* catalogue and the
adapter's own write declaration, never from the remote server's description
("未知 MCP 工具默认高风险,不信任远端 readonly 声明"), the approval digest to bind
the connection/secret versions, actor, tenant, target and normalised parameters
without becoming a place a secret can be recovered from, and every refusal to
carry its own code so the console can say which separation-of-duties rule
failed.

The adapter used here is registered under a throwaway kind so no real adapter is
involved.
"""

from __future__ import annotations

import inspect
import json
import time

import pytest

from integrations.external import risk
from integrations.external.adapters import base as base_mod
from integrations.external.adapters.base import ConnectionAdapter, register_adapter
from integrations.external.errors import ExternalConnectionError

KIND = "risk_test_kind"
WRITE_ACTION = "risk.write"
READ_ACTION = "risk.read"

SECRET = "SUPER-SECRET-VALUE"


@pytest.fixture
def risk_kind():
    """Register an adapter that declares one write action, then remove it."""
    cls = type("RiskTestAdapter", (ConnectionAdapter,), {
        "kind": KIND,
        "actions": frozenset({WRITE_ACTION, READ_ACTION}),
        "write_actions": frozenset({WRITE_ACTION}),
    })
    register_adapter(cls)
    try:
        yield KIND
    finally:
        base_mod._ADAPTER_CLASSES.pop(KIND, None)
        base_mod._ADAPTERS.pop(KIND, None)


def _decision(**overrides) -> risk.ApprovalDecision:
    values = dict(approved=True, approver_user_id="approver", digest="match",
                  issued_at=0, expires_at=0, revoked=False, scope="once")
    values.update(overrides)
    return risk.ApprovalDecision(**values)


def _refusal(code: str, **kwargs) -> ExternalConnectionError:
    with pytest.raises(ExternalConnectionError) as caught:
        risk.verify_approval(**kwargs)
    assert caught.value.code == code
    return caught.value


# -- the catalogue -----------------------------------------------------------

def test_every_catalogue_entry_is_self_consistent():
    assert risk.RISK_CATALOGUE, "the catalogue must not be empty"
    for (kind, action), entry in risk.RISK_CATALOGUE.items():
        assert entry.kind == kind
        assert entry.action == action
        assert entry.level in risk.RISK_LEVELS
        assert entry.label_key
        assert isinstance(entry.write, bool)


def test_an_unclassified_write_defaults_to_high_and_a_read_to_medium():
    assert risk.lookup(KIND, "unclassified.write", write=True).level == "high"
    assert risk.lookup(KIND, "unclassified.read", write=False).level == "medium"


def test_an_unclassified_write_requires_approval():
    entry = risk.lookup(KIND, "unclassified.write", write=True)
    assert entry.level in risk.APPROVAL_REQUIRED


def test_no_remote_hint_can_lower_a_classified_level():
    # The catalogue is consulted first, so even a caller claiming this is a
    # read gets the catalogued critical level: the remote side's own
    # ``readOnlyHint`` is data, not authority.
    assert risk.lookup("email", "messages.send", write=False).level == "critical"
    assert risk.lookup("mcp", "tools.call", write=False).level == "high"


def test_lookup_takes_no_remote_hint_parameter():
    params = list(inspect.signature(risk.lookup).parameters)
    assert params == ["kind", "action", "write"]


# -- canonical parameters ----------------------------------------------------

def test_canonical_parameters_is_order_insensitive_for_mapping_keys():
    first = risk.canonical_parameters({"alpha": 1, "beta": {"x": 1, "y": 2}})
    second = risk.canonical_parameters({"beta": {"y": 2, "x": 1}, "alpha": 1})
    assert first == second


def test_canonical_parameters_normalises_string_whitespace():
    canonical = risk.canonical_parameters({"note": "  a   b\n\tc "})
    assert canonical["note"] == "a b c"


def test_canonical_parameters_masks_secret_named_keys():
    canonical = risk.canonical_parameters({
        "password": "hunter2",
        "authorization": "Bearer abc",
        "token": "tok-123",
        "nested": {"client_secret": "shh", "keep": "visible"},
    })
    blob = json.dumps(canonical)
    for secret in ("hunter2", "Bearer abc", "tok-123", "shh"):
        assert secret not in blob
    assert canonical["password"] == "<masked>"
    assert canonical["nested"]["keep"] == "visible"


def test_canonical_parameters_digests_bulk_keys_instead_of_including_them():
    canonical = risk.canonical_parameters(
        {"body": "the whole sensitive message", "subject": "hello"})
    blob = json.dumps(canonical)
    assert "the whole sensitive message" not in blob
    assert canonical["body"]["sha256"]
    assert canonical["body"]["length"] > 0
    assert canonical["subject"] == "hello"


def test_a_changed_parameter_produces_a_different_canonical_form():
    assert risk.canonical_parameters({"amount": 1}) != \
        risk.canonical_parameters({"amount": 2})
    assert risk.canonical_parameters({"amount": 1, "to": "a"}) != \
        risk.canonical_parameters({"amount": 1, "to": "b"})


# -- approval_binding --------------------------------------------------------

BASE_FACTS = dict(
    connection_id="conn-1", config_version=3,
    secret_versions={"password": 1}, actor_user_id="alice", tenant_id="t1",
    target={"id": "T-1"}, params={"amount": 10, "password": SECRET},
)


def _binding(**overrides) -> dict:
    values = dict(kind=KIND, action=WRITE_ACTION, **BASE_FACTS)
    values.update(overrides)
    return risk.approval_binding(**values)


def test_approval_binding_changes_when_any_bound_fact_changes():
    full = dict(kind=KIND, action=WRITE_ACTION, **BASE_FACTS)
    base = risk.approval_binding(**full)["digest"]
    variations = [
        dict(full, connection_id="conn-2"),
        dict(full, config_version=4),
        dict(full, secret_versions={"password": 2}),
        dict(full, actor_user_id="bob"),
        dict(full, tenant_id="t2"),
        dict(full, target={"id": "T-2"}),
        dict(full, params={"amount": 11, "password": SECRET}),
        dict(full, params={"amount": 10, "password": SECRET,
                           "attachments": [{"name": "a.pdf"}]}),
        dict(full, kind="other_kind"),
        dict(full, action="risk.other"),
    ]
    for variation in variations:
        assert risk.approval_binding(**variation)["digest"] != base, variation


def test_approval_binding_is_stable_when_only_unrelated_facts_change():
    base = _binding()["digest"]
    reordered = dict(BASE_FACTS,
                     params={"password": SECRET, "amount": 10},
                     target={"id": "T-1"})
    assert risk.approval_binding(kind=KIND, action=WRITE_ACTION,
                                 **reordered)["digest"] == base
    # A digest key changes the digest (it is keyed to the deployment) but the
    # binding itself carries no secret.
    assert risk.approval_binding(kind=KIND, action=WRITE_ACTION,
                                 key="deployment-key", **BASE_FACTS)["digest"] != base


def test_approval_binding_carries_no_plaintext_secret():
    binding = _binding()["digest"]
    payload = _binding()
    blob = json.dumps(payload, default=str)
    assert SECRET not in blob
    assert blob  # and the caller-facing digest is a hex string
    assert isinstance(binding, str) and len(binding) >= 32


def test_attachment_digest_tracks_the_set_without_the_bytes():
    first = risk.attachment_digest(
        {"attachments": [{"name": "a.pdf", "size": 1}]})
    same = risk.attachment_digest(
        {"attachments": [{"name": "a.pdf", "size": 1}]})
    different = risk.attachment_digest(
        {"attachments": [{"name": "a.pdf", "size": 2}]})
    none = risk.attachment_digest({})
    assert first == same
    assert first != different
    assert first != none
    assert isinstance(first, str) and len(first) >= 32


# -- verify_approval: one code per refusal -----------------------------------

def test_verify_approval_requires_an_approved_decision():
    _refusal("approval_required", decision=_decision(approved=False),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_a_revoked_approval():
    _refusal("approval_revoked", decision=_decision(revoked=True),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_a_self_approval_by_default():
    _refusal("approval_not_separated",
             decision=_decision(approver_user_id="alice"),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_permits_a_self_approval_when_allow_self_approval():
    risk.verify_approval(_decision(approver_user_id="alice"), digest="match",
                         actor_user_id="alice", now=2000,
                         allow_self_approval=True)


def test_verify_approval_refuses_a_missing_approver():
    _refusal("approval_incomplete", decision=_decision(approver_user_id=""),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_an_expired_approval_by_timestamp():
    _refusal("approval_expired", decision=_decision(expires_at=1500),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_an_expired_approval_by_age():
    _refusal("approval_expired",
             decision=_decision(issued_at=1000, expires_at=0),
             digest="match", actor_user_id="alice", now=5000,
             max_age_seconds=900)


def test_verify_approval_refuses_a_missing_digest():
    _refusal("approval_incomplete", decision=_decision(digest=""),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_a_changed_binding():
    _refusal("approval_binding_mismatch", decision=_decision(digest="other"),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_refuses_an_unsupported_scope():
    _refusal("approval_scope_refused", decision=_decision(scope="forever"),
             digest="match", actor_user_id="alice", now=2000)


def test_verify_approval_accepts_a_matching_separated_approval():
    risk.verify_approval(_decision(digest="match"), digest="match",
                         actor_user_id="alice", now=2000)


def test_approval_decision_parse_defaults_to_refused():
    assert risk.ApprovalDecision.parse(None).approved is False
    parsed = risk.ApprovalDecision.parse(
        {"approved": True, "approver_user_id": "bob", "digest": "d",
         "scope": "single_use"})
    assert parsed.approved is True
    assert parsed.approver_user_id == "bob"
    assert parsed.scope == "single_use"


# -- check_invocation: the single pre-dispatch gate --------------------------

def test_a_low_risk_read_runs_without_an_approval():
    # Catalogued low-risk, read-only: no acknowledgement and no approval.
    risk.check_invocation("mcp", "tools.list", {})


def test_an_uncatalogued_write_requires_an_approval(risk_kind):
    with pytest.raises(ExternalConnectionError) as caught:
        risk.check_invocation(risk_kind, WRITE_ACTION, {"amount": 1})
    assert caught.value.code == "approval_required"


def test_an_uncatalogued_read_does_not_require_an_approval(risk_kind):
    # The adapter did not declare this as a write, so it is a medium read and
    # the gate lets it through.
    risk.check_invocation(risk_kind, READ_ACTION, {})


def test_a_critical_action_stays_closed_until_the_level_is_acknowledged(monkeypatch):
    from config import conf

    monkeypatch.setitem(conf(), "external_connections",
                        {"risk": {"acknowledged_levels": []}})
    with pytest.raises(ExternalConnectionError) as caught:
        risk.check_invocation("email", "messages.send", {})
    assert caught.value.code == "risk_not_acknowledged"

    monkeypatch.setitem(conf(), "external_connections",
                        {"risk": {"acknowledged_levels": ["critical"]}})
    # Now the acknowledgement is satisfied and it is the approval that is
    # missing.
    with pytest.raises(ExternalConnectionError) as caught:
        risk.check_invocation("email", "messages.send", {})
    assert caught.value.code == "approval_required"


def test_a_matching_approval_passes_the_gate(risk_kind):
    now = int(time.time())
    params = {"amount": 42}
    gate = dict(connection_id="conn-1", config_version=3,
                secret_versions={"password": 1}, actor_user_id="alice",
                tenant_id="t1", target={"id": "T-1"})
    digest = risk.approval_binding(kind=risk_kind, action=WRITE_ACTION,
                                   params=params, **gate)["digest"]
    approval = {"approved": True, "approver_user_id": "approver",
                "digest": digest, "issued_at": now, "expires_at": now + 600,
                "scope": "once"}
    # No refusal: the approved action is exactly the action being dispatched.
    risk.check_invocation(risk_kind, WRITE_ACTION, params,
                          approval=approval, **gate)


def test_a_changed_parameter_fails_the_gate(risk_kind):
    now = int(time.time())
    gate = dict(connection_id="conn-1", config_version=3,
                secret_versions={"password": 1}, actor_user_id="alice",
                tenant_id="t1", target={"id": "T-1"})
    digest = risk.approval_binding(kind=risk_kind, action=WRITE_ACTION,
                                   params={"amount": 42}, **gate)["digest"]
    approval = {"approved": True, "approver_user_id": "approver",
                "digest": digest, "issued_at": now, "expires_at": now + 600,
                "scope": "once"}
    with pytest.raises(ExternalConnectionError) as caught:
        risk.check_invocation(risk_kind, WRITE_ACTION, {"amount": 43},
                              approval=approval, **gate)
    assert caught.value.code == "approval_binding_mismatch"


def test_acknowledged_levels_and_max_age_read_the_deployment_config(monkeypatch):
    from config import conf

    monkeypatch.setitem(conf(), "external_connections", {"risk": {
        "acknowledged_levels": ["Critical", "high"],
        "approval_max_age_seconds": 120}})
    assert risk.acknowledged_levels() == frozenset({"critical", "high"})
    assert risk.max_age_seconds() == 120


def test_action_projection_says_what_a_write_will_require():
    projection = risk.action_projection("email", "messages.send", write=True)
    assert projection["level"] == "critical"
    assert projection["approval_required"] is True
    read = risk.action_projection("mcp", "tools.list", write=False)
    assert read["approval_required"] is False
    assert read["acknowledged"] is True
