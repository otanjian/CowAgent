# encoding:utf-8
"""End-to-end in-place tenancy registration (tasks 4.1 + part of 4.2).

Demonstrates the migration loop the CLI ``cow management register`` runs:
bootstrap a default tenant + admin, register existing registry agents (with the
admin as default private owner), backfill owner-less conversations to that admin,
then verify the admin sees them while a newly created member does not.
"""

import os
import tempfile

from auth.service import IdentityService
from agent.memory.conversation_store import ConversationStore, get_conversation_store
from agent.registry import AgentProfile, AgentRegistry, set_agent_registry
from common.runtime_identity import RuntimeIdentity, use_identity


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _build_registry(tmp_path):
    reg = AgentRegistry(
        [AgentProfile(id="alpha", name="Alpha", workspace=str(tmp_path / "alpha"))],
        "alpha",
    )
    set_agent_registry(reg)
    return reg


def test_inplace_registration_closes_isolation_loop(tmp_path):
    reg = _build_registry(tmp_path)
    try:
        svc = IdentityService(_db())
        svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass", shared_root=str(tmp_path))
        tid = svc.list_tenants()[0]["id"]
        admin = [u for u in svc.list_platform_users() if u.get("is_platform_admin")][0]

        # Seed a pre-isolation conversation with no owner.
        store = get_conversation_store(tmp_path / "alpha")
        with use_identity(RuntimeIdentity()):
            store.append_messages("s1", [{"role": "user", "content": "legacy"}] * 1)

        # Migration loop: register agents (private owner = admin) + backfill owner.
        agent_ids = [p.id for p in reg.list(include_disabled=True)]
        summary = svc.register_default_tenancy(
            tenant_id=tid, private_owner_user_id=admin["id"], agent_ids=agent_ids)
        backfilled = store.backfill_owner(admin["id"])

        assert summary["bound"] == 1
        assert backfilled == 1  # the owner-less session got registered

        # Admin (the private owner) now sees the registered conversation.
        with use_identity(RuntimeIdentity(agent_id="alpha", user_id=admin["id"])):
            assert store.list_session_ids(user_id=admin["id"]) == ["s1"]

        # A brand-new member (different user) must NOT see it.
        with use_identity(RuntimeIdentity(agent_id="alpha", user_id="member-1")):
            assert store.list_session_ids(user_id="member-1") == []

        # Idempotent: re-running the migration does not duplicate and does not
        # clobber a later owner.
        summary2 = svc.register_default_tenancy(
            tenant_id=tid, private_owner_user_id=admin["id"], agent_ids=agent_ids)
        assert summary2["bound"] == 0
        assert summary2["already_registered"] == 1
    finally:
        set_agent_registry(None)
