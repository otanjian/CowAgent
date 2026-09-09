# encoding:utf-8
"""Control-plane slices (open-database-runtime 7.x-9.x).

credentials: encrypted storage, masked listing, controller-only lifecycle,
  use-point decryption, rotation invalidating old values, immediate revoke.
approvals:  high-risk side effects wait for a qualified non-requester
  decision; pending/approved/denied/expired; secret payloads stripped.
quotas:     tenant+user windowed hard limits, controller-only configuration,
  non-member consumption refused.
"""

import sqlite3

import pytest

MASTER = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER)


@pytest.fixture
def svc(tmp_path):
    from auth.service import IdentityService

    service = IdentityService(str(tmp_path / "identity.db"))
    tenant = service.bootstrap(
        tenant_code="acme", tenant_name="Acme", admin_username="root",
        admin_display="Root", admin_password="Str0ngAdminPass",
        shared_root=str(tmp_path / "shared"), allow_weak=True,
    )["id"]
    root = service.list_platform_users()[0]["id"]
    member = service.create_member(
        actor_user_id=root, tenant_id=tenant, operation="create-new",
        username="member", display_name="Member", temporary_password="TempPass123!",
        roles=["member"],
    )["user_id"]
    token = service.login("member", "TempPass123!").token
    service.change_password(token, "TempPass123!", "MemberPass123!")
    return Simple(svc=service, tenant=tenant, root=root, member=member,
                  db_path=str(tmp_path / "identity.db"))


class Simple:
    def __init__(self, svc, tenant, root, member, db_path):
        self.svc, self.tenant, self.root, self.member = svc, tenant, root, member
        self.db_path = db_path


# --- crypto ----------------------------------------------------------------

def test_encrypt_decrypt_round_trip_and_form():
    from auth.crypto import decrypt_secret, encrypt_secret
    token = encrypt_secret("SAP-Passw0rd-x")
    assert token.startswith("v1.") and token.count(".") == 2
    assert token != "SAP-Passw0rd-x"
    assert decrypt_secret(token) == "SAP-Passw0rd-x"


def test_ciphertext_key_bound(monkeypatch):
    from auth.crypto import decrypt_secret, encrypt_secret
    token = encrypt_secret("hunter2")
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY",
                       "ff" * 32)  # different key
    with pytest.raises(Exception):
        decrypt_secret(token)


def test_no_master_key_refuses_encryption(monkeypatch):
    monkeypatch.delenv("COW_CREDENTIAL_MASTER_KEY", raising=False)
    from auth.crypto import CredentialCryptoError, encrypt_secret
    with pytest.raises(CredentialCryptoError):
        encrypt_secret("anything")


def test_mask_never_leaks_body():
    from auth.crypto import mask_secret
    out = mask_secret("sap_prod", "supersecretvalue")
    assert "supersecret" not in out
    assert out.startswith("sap_prod")


# --- credentials -----------------------------------------------------------

def test_non_controller_cannot_manage_credentials(svc):
    s = svc.svc
    with pytest.raises(Exception) as err:
        s.create_credential(actor_user_id=svc.member, tenant_id=svc.tenant,
                            name="sap", secret="x")
    assert err.value.status == 403


def test_controller_create_list_masked_and_resolve(svc):
    s = svc.svc
    created = s.create_credential(
        actor_user_id=svc.root, tenant_id=svc.tenant, name="sap-prod",
        secret="ZmVybmV0!2026", resource_kind="sap", resource_id="PROD",
    )
    assert created["active"] is True and created["version"] == 1

    listing = s.list_credentials(actor_user_id=svc.root, tenant_id=svc.tenant)
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["name"] == "sap-prod"
    assert "ZmVybmV0!2026" not in str(item)          # no plaintext in list
    assert item["masked"].startswith("sap-prod")

    plain = s.resolve_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                                 name="sap-prod", resource_kind="sap")
    assert plain == "ZmVybmV0!2026"


def test_duplicate_credential_name_conflicts(svc):
    s = svc.svc
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="dup", secret="one")
    with pytest.raises(Exception) as err:
        s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                            name="dup", secret="two")
    assert err.value.status == 409


def test_rotate_switches_value_and_bumps_version(svc):
    s = svc.svc
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="api", secret="old-secret")
    rotated = s.rotate_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                                  name="api", new_secret="new-secret")
    assert rotated["version"] == 2
    assert s.resolve_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                                name="api") == "new-secret"
    # History preserved (version 1 exists in the audit trail table).
    with sqlite3.connect(svc.db_path) as con:
        kinds = [r[0] for r in con.execute(
            "SELECT action FROM credential_versions WHERE credential_id=?",
            (rotated["id"],)).fetchall()]
    assert kinds == ["create", "rotated"]


def test_revoke_then_use_fails(svc):
    s = svc.svc
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="smtp", secret="p")
    assert s.revoke_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                               name="smtp") is True
    with pytest.raises(Exception) as err:
        s.resolve_credential(actor_user_id=svc.root, tenant_id=svc.tenant, name="smtp")
    assert err.value.status == 404


def test_credential_lifecycle_is_audited(svc):
    s = svc.svc
    secret = "SuperSecret!2026-not-in-audit"
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="sap", secret=secret)
    s.rotate_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="sap", new_secret="NextSecret!2027-not-in-audit")
    s.revoke_credential(actor_user_id=svc.root, tenant_id=svc.tenant, name="sap")
    actions = [e["action"] for e in s.list_audit(tenant_id=svc.tenant)]
    assert "credential.create" in actions
    assert "credential.rotate" in actions
    assert "credential.revoke" in actions
    for event in s.list_audit(tenant_id=svc.tenant):
        assert secret not in str(event)  # never logs plaintext
        assert "NextSecret!2027-not-in-audit" not in str(event)


def test_cross_tenant_credential_use_refused(svc):
    """A member of tenant B cannot resolve tenant A's credential even with a
    controller on B: the store is scoped by owner tenant and the eligibility
    check requires membership in the owning tenant."""
    s = svc.svc
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="sap", secret="acme-only")
    other = s.create_tenant(
        actor_user_id=svc.root, code="other", name="Other", shared_root="",
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    admins = s.list_members(tenant_id=other, role="tenant_admin")["items"]
    other_root = next(m["user_id"] for m in admins if m.get("active", True))
    assert other_root
    # Tenant B's admin is a controller *of B* only — resolving A's credential 403.
    with pytest.raises(Exception) as err:
        s.resolve_credential(actor_user_id=other_root, tenant_id=svc.tenant, name="sap")
    assert err.value.status == 403


def test_member_without_grant_cannot_read_or_resolve(svc):
    """A plain member (no credential.use, not a controller) gets 403 on both
    list and use-point resolution even though they are an active member."""
    s = svc.svc
    s.create_credential(actor_user_id=svc.root, tenant_id=svc.tenant,
                        name="sap", secret="x")
    with pytest.raises(Exception) as err:
        s.list_credentials(actor_user_id=svc.member, tenant_id=svc.tenant)
    assert err.value.status == 403
    with pytest.raises(Exception) as err:
        s.resolve_credential(actor_user_id=svc.member, tenant_id=svc.tenant, name="sap")
    assert err.value.status == 403


# --- approvals -------------------------------------------------------------

def test_member_requests_approval_pending(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="shared-agent", action="sap.production_release",
                             payload={"order": "SO-100", "note": "ship"},
                             expires_in_s=600)
    assert req["status"] == "pending" and req["action"] == "sap.production_release"
    rows = s.list_approvals(actor_user_id=svc.member, tenant_id=svc.tenant)
    assert len(rows) == 1 and rows[0]["status"] == "pending"


def test_requester_cannot_self_approve(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="ir.payment_release")
    with pytest.raises(Exception) as err:
        s.decide_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                          approval_id=req["id"], approve=True)
    assert err.value.status == 403


def test_admin_approval_then_no_double_decision(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="pay.run",
                             payload={"amount": 100, "token": "should-strip"})
    import json
    stored = [r for r in s.list_approvals(actor_user_id=svc.root, tenant_id=svc.tenant)
              if r["id"] == req["id"]][0]
    assert "should-strip" not in json.loads(stored["payload_json"])

    decided = s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                                approval_id=req["id"], approve=True, note="ok")
    assert decided["status"] == "approved"
    with pytest.raises(Exception) as err:
        s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                          approval_id=req["id"], approve=True)
    assert err.value.status == 409


def test_deny_and_expired_paths(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="erp.post")
    assert s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                             approval_id=req["id"], approve=False).get("status") == "denied"

    req2 = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                              agent_id="a1", action="erp.post")
    with sqlite3.connect(svc.db_path) as con:
        con.execute("UPDATE approvals SET expires_at=1 WHERE id=?", (req2["id"],))
        con.commit()
    with pytest.raises(Exception) as err:
        s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                          approval_id=req2["id"], approve=True)
    assert err.value.status == 409 and err.value.code == "expired"


def test_member_cannot_decide_others_approval(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="pay.run")
    with pytest.raises(Exception) as err:
        s.decide_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                          approval_id=req["id"], approve=True)
    assert err.value.status == 403


def test_requester_cancels_own_pending(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="pay.run")
    out = s.cancel_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                            approval_id=req["id"])
    assert out["status"] == "revoked"
    with pytest.raises(Exception) as err:   # decided approval is immutable
        s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                          approval_id=req["id"], approve=True)
    assert err.value.status == 409


def test_cancel_by_non_requester_forbidden(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="pay.run")
    with pytest.raises(Exception) as err:
        s.cancel_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                          approval_id=req["id"])
    assert err.value.status == 403


def test_controller_revokes_approved_before_effect(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="sap.production_release")
    s.decide_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                      approval_id=req["id"], approve=True, note="ok")
    # A second qualified user discovers the action is unsafe and revokes it
    # before the executor fires.
    out = s.revoke_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                            approval_id=req["id"], note="run cancelled")
    assert out["status"] == "revoked"
    stored = [r for r in s.list_approvals(actor_user_id=svc.root, tenant_id=svc.tenant)
              if r["id"] == req["id"]][0]
    assert stored["status"] == "revoked" and stored["version"] >= 3
    actions = [e["action"] for e in s.list_audit(tenant_id=svc.tenant)]
    assert "approval.revoke" in actions


def test_revoke_only_approved(svc):
    s = svc.svc
    req = s.request_approval(actor_user_id=svc.member, tenant_id=svc.tenant,
                             agent_id="a1", action="pay.run")
    with pytest.raises(Exception) as err:   # pending cannot be revoked directly
        s.revoke_approval(actor_user_id=svc.root, tenant_id=svc.tenant,
                          approval_id=req["id"])
    assert err.value.status == 409


# --- quotas ----------------------------------------------------------------

def test_quota_requires_controller(svc):
    s = svc.svc
    with pytest.raises(Exception) as err:
        s.set_quota(actor_user_id=svc.member, tenant_id=svc.tenant,
                    metric="tokens", hard_limit=100)
    assert err.value.status == 403


def test_tenant_quota_hard_limit_windows(svc):
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=2)
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tool_calls") is True
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tool_calls") is True
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tool_calls") is False  # 3rd refused
    status = s.quota_status(actor_user_id=svc.root, tenant_id=svc.tenant)
    usage = [u for u in status["usage"] if u["metric"] == "tool_calls"]
    assert usage and usage[0]["used"] == 2


def test_user_bucket_over_tenant_bucket(svc):
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="messages", hard_limit=100)          # generous tenant
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="messages", hard_limit=1, user_id=svc.member)
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="messages") is True
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="messages") is False


def test_zero_limit_is_unlimited_and_non_member_refused(svc):
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tokens", hard_limit=0)              # unlimited
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tokens", amount=10) is True
    assert s.consume_quota(user_id="ghost", tenant_id=svc.tenant,
                           metric="tokens") is False


def test_quota_denials_audited(svc):
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=1)
    s.consume_quota(user_id=svc.member, tenant_id=svc.tenant, metric="tool_calls")
    s.consume_quota(user_id=svc.member, tenant_id=svc.tenant, metric="tool_calls")
    actions = [e["action"] for e in s.list_audit(tenant_id=svc.tenant)]
    assert "quota.deny" in actions and "quota.set" in actions


def test_token_metric_over_limit_refused(svc):
    """The same windowed meter drives tokens/storage — an over-limit consume on
    any metric is refused, not just tool_calls."""
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tokens", hard_limit=100)
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tokens", amount=60) is True
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tokens", amount=60) is False  # 120 > 100


def test_cross_tenant_member_cannot_borrow_quota(svc):
    """A member of tenant B cannot consume tenant A's quota bucket: consume is
    keyed to the tenant the user is actually a member of."""
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=5)
    other = s.create_tenant(
        actor_user_id=svc.root, code="other", name="Other", shared_root="",
        admin_username="other", admin_display="Other", admin_password="OtherPass123!",
        recent_password="Str0ngAdminPass",
    )["id"]
    member2 = s.create_member(
        actor_user_id=svc.root, tenant_id=other, operation="create-new",
        username="member2", display_name="Member2", temporary_password="TempPass123!",
        roles=["member"],
    )["user_id"]
    # member2 is not a member of svc.tenant => cannot charge its bucket.
    assert s.consume_quota(user_id=member2, tenant_id=svc.tenant,
                           metric="tool_calls") is False
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tool_calls") is True


def test_lowered_limit_applies_to_next_consumption(svc):
    """9.3 instant effect: lowering a limit denies the very next call, so an
    already-scheduled/queued task that fires later hits the new ceiling."""
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=100)
    s.consume_quota(user_id=svc.member, tenant_id=svc.tenant, metric="tool_calls")
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=1)
    # The stored usage is 1, the new ceiling is 1 => the second call is refused.
    assert s.consume_quota(user_id=svc.member, tenant_id=svc.tenant,
                           metric="tool_calls") is False
    status = s.quota_status(actor_user_id=svc.root, tenant_id=svc.tenant)
    usage = [u for u in status["usage"] if u["metric"] == "tool_calls"
             and not u.get("user_id")]
    assert usage and usage[0]["used"] == 1


def test_audit_same_tx_rolls_back_on_failure(svc):
    """10.3 strong consistency: when the surrounding mutation fails, the audit
    event recorded in the same transaction is rolled back too — no orphan
    'success' audit for an operation that did not happen."""
    s = svc.svc
    with pytest.raises(sqlite3.IntegrityError):
        with s._tx() as con:
            s._audit_in_tx(
                con, actor_user_id=svc.root, tenant_id=svc.tenant,
                target_tenant_id=svc.tenant, action="quota.set",
                target="quota:boom", redacted_changes={}, result="success")
            con.execute("INSERT INTO quota_limits(tenant_id,user_id,metric,hard_limit)"
                        " VALUES ('t', '', 'tokens', 1)")  # dup PK triggers on 2nd
            con.execute("INSERT INTO quota_limits(tenant_id,user_id,metric,hard_limit)"
                        " VALUES ('t', '', 'tokens', 1)")
    actions = [e["action"] for e in s.list_audit(tenant_id=svc.tenant)]
    assert "quota.set" not in actions


# --- tool-call meter at the agent gate --------------------------------------

def _member_identity():
    from common.runtime_identity import RuntimeIdentity
    return RuntimeIdentity(agent_id="a", user_id="u", tenant_id="t")


def _stream_stub():
    from agent.protocol import agent_stream
    return agent_stream.AgentStreamExecutor.__new__(agent_stream.AgentStreamExecutor)


def test_tool_quota_gate_denies_after_limit(monkeypatch, svc):
    """The agent-stream permission gate meters DB-mode tool calls; the third
    call past a hard limit is denied with a bilingual reason     before execution."""
    s = svc.svc
    s.set_quota(actor_user_id=svc.root, tenant_id=svc.tenant,
                metric="tool_calls", hard_limit=2)
    monkeypatch.setattr("auth.service.get_identity_service", lambda: s)
    from common.runtime_identity import RuntimeIdentity
    monkeypatch.setattr(
        "common.runtime_identity.current_identity",
        lambda: RuntimeIdentity(agent_id="a", user_id=svc.member, tenant_id=svc.tenant),
    )
    stub = _stream_stub()
    assert stub._quota_tool_denial("bash") is None
    assert stub._quota_tool_denial("read") is None
    denial = stub._quota_tool_denial("write")
    assert denial and "配额" in denial and "quota" in denial


def test_tool_quota_gate_passthrough_when_unmetered(monkeypatch, svc):
    """No configured limit => no denial and no DB write for every call."""
    s = svc.svc
    monkeypatch.setattr("auth.service.get_identity_service", lambda: s)
    from common.runtime_identity import RuntimeIdentity
    monkeypatch.setattr(
        "common.runtime_identity.current_identity",
        lambda: RuntimeIdentity(agent_id="a", user_id=svc.member, tenant_id=svc.tenant),
    )
    stub = _stream_stub()
    for _ in range(5):
        assert stub._quota_tool_denial("bash") is None
    assert all(u["used"] == 0 for u in
               s.quota_status(actor_user_id=svc.root, tenant_id=svc.tenant)["usage"])
