# encoding:utf-8
"""External identity bindings, maintainable by platform *and* tenant admins.

Binding an inbound IM author to a real account was platform-admin-only. A
tenant administrator is the person who actually knows which of their members
owns which IM account, so the capability is opened to them — under containment:
a tenant admin may only reach accounts that are members of *their own* tenant.

Two things make that containment hard to get wrong here:

* the tenant surface is keyed by **membership id**, and the membership lookup is
  scoped by the acting tenant, so another tenant's member is simply not found;
* ``delete`` re-derives the owning tenant from the binding's user, so a guessed
  binding id cannot be used to yank a binding out of another organization.

The second half of the feature is the operator's real problem: nobody knows a
member's ``open_id`` by heart. A denied inbound is therefore remembered as a
*pending attempt*, carrying the tenant and channel instance that delivered it —
which is what makes it visible to the right tenant admin. A successful bind
clears it.
"""

import pytest

from auth.service import IdentityService

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

ACME = {
    "tenant_code": "acme", "tenant_name": "Acme", "admin_username": "acmeadmin",
    "admin_display": "Acme Admin", "admin_password": "Str0ngAdminPass",
    "shared_root": "/s/acme",
}
GLOBEX = {
    "tenant_code": "globex", "tenant_name": "Globex", "admin_username": "globexadmin",
    "admin_display": "Globex Admin", "admin_password": "Str0ngGlobexPass",
    "shared_root": "/s/globex",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("COW_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    svc = IdentityService(str(tmp_path / "identity.db"))
    acme = svc.bootstrap(**ACME)
    acme_id = acme["id"]
    # The bootstrap account holds *both* the platform role and the acme tenant
    # admin role; ``globexadmin`` below is a pure tenant admin. Tests that must
    # prove "a tenant admin cannot do X on the platform surface" use the latter.
    platform = svc.list_platform_users()[0]
    assert svc.is_platform_admin_user(platform["id"])

    svc.change_password(
        svc.login("acmeadmin", ACME["admin_password"]).token,
        ACME["admin_password"], "Str0ngAcmeFinal")
    svc.create_tenant(
        actor_user_id=platform["id"], code="globex", name="Globex",
        admin_username="globexadmin", admin_display="Globex Admin",
        admin_password=GLOBEX["admin_password"], recent_password="Str0ngAcmeFinal",
        shared_root="/s/globex")
    globex_id = [t for t in svc.list_tenants() if t["code"] == "globex"][0]["id"]
    svc.change_password(
        svc.login("globexadmin", GLOBEX["admin_password"]).token,
        GLOBEX["admin_password"], "Str0ngGlobexFinal")

    acme_member = svc.create_member(
        actor_user_id=platform["id"], tenant_id=acme_id, operation="create-new",
        username="acmemember", display_name="Acme Member",
        temporary_password="Str0ngMember1", roles=["member"])["user_id"]
    globex_member = svc.create_member(
        actor_user_id=platform["id"], tenant_id=globex_id, operation="create-new",
        username="globexmember", display_name="Globex Member",
        temporary_password="Str0ngMember2", roles=["member"])["user_id"]
    # A member whose temporary password was never rotated cannot use IM at all,
    # so both are put past the forced change to isolate what is under test.
    for username, temporary, final in (
            ("acmemember", "Str0ngMember1", "Str0ngMemberFinal"),
            ("globexmember", "Str0ngMember2", "Str0ngGlobexMemberFinal")):
        svc.change_password(svc.login(username, temporary).token, temporary, final)

    acme_admin = [u for u in svc.list_platform_users()
                  if u["username"] == "acmeadmin"][0]
    globex_admin = [u for u in svc.list_platform_users()
                    if u["username"] == "globexadmin"][0]

    def membership(tenant_id, user_id):
        members = svc.list_members(tenant_id)["items"]
        return [m for m in members if m["user_id"] == user_id][0]

    return type("World", (), {
        "svc": svc, "acme": acme_id, "globex": globex_id,
        "platform": platform["id"], "acme_admin": acme_admin["id"],
        "globex_admin": globex_admin["id"],
        "acme_member": acme_member, "globex_member": globex_member,
        "acme_membership": membership(acme_id, acme_member),
        "globex_membership": membership(globex_id, globex_member),
        "membership": membership,
    })


# --- the tenant surface reaches only its own members ------------------------

def test_a_tenant_admin_binds_one_of_its_members(world):
    binding = world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"], provider="feishu",
        issuer="cli_acme", subject="ou_acme_1")
    assert binding["user_id"] == world.acme_member
    assert binding["subject"] == "ou_acme_1"


def test_a_tenant_admin_cannot_bind_another_tenants_member(world):
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.acme,
            member_id=world.globex_membership["id"], provider="feishu",
            issuer="cli_globex", subject="ou_globex_1")
    # Not found, not forbidden: the other tenant's membership does not exist as
    # far as this tenant is concerned, so nothing is revealed by the refusal.
    assert getattr(err.value, "code", "") == "not_found"


def test_the_tenant_id_argument_cannot_be_used_to_reach_sideways(world):
    # Passing the *other* tenant's id is not enough: the actor is not its admin.
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.globex,
            member_id=world.globex_membership["id"], provider="feishu",
            issuer="cli_globex", subject="ou_globex_2")
    assert getattr(err.value, "code", "") in ("forbidden", "not_found")


def test_a_plain_member_cannot_bind_anybody(world):
    token = world.svc.login("acmemember", "Str0ngMemberFinal").token
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity_for_tenant(
            actor_user_id=world.acme_member, tenant_id=world.acme,
            member_id=world.acme_membership["id"], provider="feishu",
            issuer="cli_acme", subject="ou_self")
    assert getattr(err.value, "code", "") == "forbidden"


def test_a_tenant_admin_lists_only_its_members_bindings(world):
    world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"], provider="feishu",
        issuer="cli_acme", subject="ou_acme_1")
    world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.globex_admin, tenant_id=world.globex,
        member_id=world.globex_membership["id"], provider="feishu",
        issuer="cli_globex", subject="ou_globex_1")

    items = world.svc.list_external_identities_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"])["items"]
    assert [i["subject"] for i in items] == ["ou_acme_1"]

    with pytest.raises(Exception):
        world.svc.list_external_identities_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.acme,
            member_id=world.globex_membership["id"])


def test_a_tenant_admin_unbinds_its_own_members_binding(world):
    binding = world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"], provider="feishu",
        issuer="cli_acme", subject="ou_acme_1")
    world.svc.delete_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        binding_id=binding["id"])
    items = world.svc.list_external_identities_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"])["items"]
    assert items == []


def test_a_tenant_admin_cannot_unbind_another_tenants_binding(world):
    foreign = world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.globex_admin, tenant_id=world.globex,
        member_id=world.globex_membership["id"], provider="feishu",
        issuer="cli_globex", subject="ou_globex_1")
    with pytest.raises(Exception) as err:
        world.svc.delete_external_identity_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.acme,
            binding_id=foreign["id"])
    assert getattr(err.value, "code", "") in ("not_found", "forbidden")
    # And it is still there.
    assert world.svc.list_external_identities(
        user_id=world.globex_member)["items"]


def test_the_platform_path_is_unchanged(world):
    # The global API keeps its platform-only contract and its ability to reach
    # any account; opening the tenant surface must not have widened it.
    assert world.svc.list_external_identities(
        user_id=world.acme_member)["items"] == []
    world.svc.bind_external_identity(
        actor_user_id=world.platform, user_id=world.acme_member,
        provider="feishu", issuer="cli_acme", subject="ou_by_platform")
    assert [i["subject"] for i in world.svc.list_external_identities(
        user_id=world.acme_member)["items"]] == ["ou_by_platform"]


def test_a_pure_tenant_admin_cannot_use_the_global_api(world):
    # ``globexadmin`` administers a tenant but holds no platform role, so the
    # global surface must refuse them. (The bootstrap account is deliberately
    # *both* roles, which is why it cannot be used to prove this.)
    assert not world.svc.is_platform_admin_user(world.globex_admin), (
        "the fixture is only meaningful with a platform-role-free tenant admin")
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity(
            actor_user_id=world.globex_admin, user_id=world.acme_member,
            provider="feishu", issuer="cli_acme", subject="ou_by_tenant_admin")
    assert getattr(err.value, "code", "") == "forbidden"
    with pytest.raises(Exception) as err:
        world.svc.delete_external_identity(
            actor_user_id=world.globex_admin, binding_id="ext_whatever")
    assert getattr(err.value, "code", "") == "forbidden"


def test_a_duplicate_triple_is_still_refused_on_the_tenant_surface(world):
    world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"], provider="feishu",
        issuer="cli_acme", subject="ou_acme_1")
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.acme,
            member_id=world.acme_membership["id"], provider="feishu",
            issuer="cli_acme", subject="ou_acme_1")
    assert getattr(err.value, "code", "") == "conflict"


def test_the_tenant_surface_still_validates_the_triple(world):
    with pytest.raises(Exception) as err:
        world.svc.bind_external_identity_for_tenant(
            actor_user_id=world.acme_admin, tenant_id=world.acme,
            member_id=world.acme_membership["id"], provider="not a provider",
            issuer="cli_acme", subject="ou_x")
    assert getattr(err.value, "code", "") == "bad_request"


# --- pending attempts: the open_id nobody knows ----------------------------

def test_a_denied_inbound_is_remembered_for_the_owning_tenant(world):
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1")

    mine = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"]
    assert [a["subject"] for a in mine] == ["ou_acme_1"]
    assert mine[0]["channel_type"] == "feishu"
    assert mine[0]["instance_id"] == "chan_1"

    # Another tenant's admin never sees it.
    assert world.svc.list_external_identity_attempts(
        actor_user_id=world.globex_admin, tenant_id=world.globex)["items"] == []

    # A platform admin sees everything, including attempts with no tenant.
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_platform", subject="ou_orphan",
        tenant_id="", channel_type="feishu", instance_id="")
    everything = world.svc.list_external_identity_attempts(
        actor_user_id=world.platform)["items"]
    assert {a["subject"] for a in everything} >= {"ou_acme_1", "ou_orphan"}


def test_repeated_denials_collapse_into_one_attempt(world):
    for _ in range(3):
        world.svc.record_external_identity_attempt(
            provider="feishu", issuer="cli_acme", subject="ou_acme_1",
            tenant_id=world.acme, channel_type="feishu", instance_id="chan_1")
    items = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"]
    assert len(items) == 1, "a chatty user must not flood the list"
    assert items[0]["attempts"] == 3


def test_binding_clears_the_pending_attempt(world):
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1")
    world.svc.bind_external_identity_for_tenant(
        actor_user_id=world.acme_admin, tenant_id=world.acme,
        member_id=world.acme_membership["id"], provider="feishu",
        issuer="cli_acme", subject="ou_acme_1")
    assert world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"] == []


def test_a_platform_bind_also_clears_the_attempt(world):
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1")
    world.svc.bind_external_identity(
        actor_user_id=world.platform, user_id=world.acme_member,
        provider="feishu", issuer="cli_acme", subject="ou_acme_1")
    assert world.svc.list_external_identity_attempts(
        actor_user_id=world.platform)["items"] == []


def test_a_plain_member_cannot_read_attempts(world):
    with pytest.raises(Exception) as err:
        world.svc.list_external_identity_attempts(
            actor_user_id=world.acme_member, tenant_id=world.acme)
    assert getattr(err.value, "code", "") == "forbidden"


def test_an_attempt_remembers_who_sent_it_and_what_was_said(world):
    """A裸 open_id 认不出人，so the attempt carries the identity evidence.

    "This open_id messaged the bot" cannot be acted on by an administrator who
    does not know their members' open_ids by heart — which is everyone. The
    sender's name and a preview of what they actually said are what turn the
    list into a decision.
    """
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1",
        sender_name="张三", message_preview="帮我查下上季度报销", is_group=False)

    item = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"][0]
    assert item["sender_name"] == "张三"
    assert item["message_preview"] == "帮我查下上季度报销"
    assert item["is_group"] == 0


def test_a_group_attempt_is_distinguishable_from_a_private_one(world):
    # In a group the open_id is one voice among many, so the administrator needs
    # to know the message came from a group chat before judging the preview.
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_group",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1",
        sender_name="李四", message_preview="机器人帮我总结一下", is_group=True)
    item = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"][0]
    assert item["is_group"] == 1


def test_a_repeat_refreshes_the_evidence_to_the_latest_message(world):
    """The newest message is the best clue; an old one would read as stale.

    A name that was unknown on the first attempt but resolved by the second must
    also be picked up, so an empty field is not allowed to overwrite a known one
    and vice versa.
    """
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1",
        sender_name="", message_preview="第一条", is_group=False)
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_acme_1",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1",
        sender_name="张三", message_preview="第二条", is_group=False)

    item = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"][0]
    assert item["attempts"] == 2
    assert item["message_preview"] == "第二条"
    assert item["sender_name"] == "张三", "a later resolution must fill the gap"


def test_evidence_is_optional_and_never_blocks_the_record(world):
    # The recording path runs on a message that is already being refused; an
    # attempt with nothing but a triple is still worth remembering.
    world.svc.record_external_identity_attempt(
        provider="feishu", issuer="cli_acme", subject="ou_bare",
        tenant_id=world.acme, channel_type="feishu", instance_id="chan_1")
    item = world.svc.list_external_identity_attempts(
        actor_user_id=world.acme_admin, tenant_id=world.acme)["items"][0]
    assert item["subject"] == "ou_bare"
    assert item["sender_name"] == ""
    assert item["message_preview"] == ""
