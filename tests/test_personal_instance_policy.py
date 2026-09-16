# encoding:utf-8
"""Personal channel instances under tenant policy, quota and governance.

Change task 2.5 wires three tenant controls into the channel-instance service:

* **allowed types** — a tenant may narrow which channel types personal access
  may use ("在组织允许的范围内自行接入");
* **quota** — creation and enable must check the owner's and the tenant's
  personal allowance *atomically*, so two simultaneous requests cannot both take
  the last slot ("并发超额创建 → 至多允许一个提交，失败请求不留下占用额度的半成品");
* **governance disable** — an administrator can stop a member's personal access,
  and the owner must not be able to clear that restriction by re-saving or
  re-enabling ("owner MUST NOT 通过重新保存或启用解除治理限制").

Readiness of individual channel types is *not* here: that is task 6.3. This slice
is the tenant policy and the enforcement points it feeds.

Since task 6.1 the personal rows below are driven through the **owner**
self-service branch (``allow_owner=True`` with the member as the actor): the
unified interface refuses an administrator who would edit another member's
``scope='user'`` instance, so the policy, quota and switch checks a member
triggers can only be reached by the member. The administrator keeps the
separate governance action (``set_personal_instance_governance``) used by
:class:`GovernanceDisableTests`, which is deliberately *not* routed through the
owner path.
"""

import os
import sqlite3
import tempfile
import threading
import unittest

from auth.service import IdentityService, IdentityServiceError
from tests._helpers import install_personal_target_roster, personal_channel_target

FEISHU_BUNDLE = {
    "feishu_app_id": "cli_personal_a",
    "feishu_app_secret": "s3cr3t-app-secret",
    "feishu_bot_name": "Personal Bot",
}
DINGTALK_BUNDLE = {
    "dingtalk_client_id": "ding_personal_a",
    "dingtalk_client_secret": "s3cr3t-ding-secret",
    "dingtalk_robot_code": "robot-code-1",
}


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _expect_error(test, code, status, fn, *args, **kwargs):
    with test.assertRaises(IdentityServiceError) as caught:
        fn(*args, **kwargs)
    test.assertEqual(caught.exception.code, code)
    test.assertEqual(caught.exception.status, status)
    return caught.exception


class _PolicyFixture(unittest.TestCase):
    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    ROOT_PASSWORD = "Str0ngRootFinal"
    #: Members are created with this temporary password and never change it, so
    #: it is their current password: the owner-path writes prove presence with it.
    MEMBER_PASSWORD = "MemTempPass1"

    #: The Agents this suite's fixtures may name. ``agent-a`` stays shared so the
    #: *public* path keeps a candidate of its own; the ``target-*`` ids are the
    #: members' private ones, and the roster below is the registry half of the
    #: personal target predicate.
    ROSTER = ("agent-a", "target-alice", "target-bob")

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)
        install_personal_target_roster(self, *self.ROSTER)
        self._app_seq = 0
        self._app_seq_lock = threading.Lock()

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", self.ROOT_PASSWORD)
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)
        self.member = self._member("alice")
        self.other = self._member("bob")
        # Each member owns the private Agent their personal instance routes to.
        self.target = {
            self.member: personal_channel_target(
                self.svc, tenant_id=self.ta, user_id=self.member,
                agent_id="target-alice"),
            self.other: personal_channel_target(
                self.svc, tenant_id=self.ta, user_id=self.other,
                agent_id="target-bob"),
        }

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _member(self, username):
        from unittest.mock import patch
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.ta,
                operation="create-new", username=username, display_name=username,
                temporary_password=self.MEMBER_PASSWORD, roles=["member"])
        return [m for m in self.svc.list_members(self.ta)["items"]
                if m["username"] == username][0]["user_id"]

    def _bundle(self, channel_type="feishu"):
        """A bundle whose app identity is unique per call.

        Two channel instances may not connect to the same external application
        at once (task 6.3), and these tests are not about that rule: a repeated
        ``cli_personal_a`` would trip the app-conflict check before reaching the
        policy under test. Each create therefore gets its own app. The counter is
        lock-guarded because the concurrency tests below call this from two
        threads at once, and a shared id would turn "who wins the last quota
        slot" into "which request is refused for a second, unrelated reason".
        """
        with self._app_seq_lock:
            self._app_seq += 1
            seq = self._app_seq
        if channel_type == "dingtalk":
            return dict(DINGTALK_BUNDLE,
                        dingtalk_client_id="ding_personal_%d" % seq)
        return dict(FEISHU_BUNDLE, feishu_app_id="cli_personal_%d" % seq)

    def _create(self, *, owner=None, name="我的飞书", channel_type="feishu",
                bundle=None, agent_id=None, scope="user"):
        # A personal row is created by its **owner** through the self-service
        # branch (``allow_owner=True``), which forces the scope/owner pair from
        # the verified actor instead of from the request; a public row keeps the
        # tenant-control actor. Routing a personal create through the
        # administrator would now be refused before the policy under test runs
        # (task 6.1: the editable list is not a second owner of somebody's row).
        #
        # The default target follows the scope: a personal instance must name its
        # own owner's private Agent, a shared one must not name a private Agent at
        # all. Passing a shared target here would be refused before the policy
        # under test is ever consulted.
        credentials = bundle or self._bundle(channel_type)
        if scope == "user":
            owner = owner or self.member
            return self.svc.create_tenant_channel_instance(
                actor_user_id=owner, tenant_id=self.ta,
                channel_type=channel_type, display_name=name,
                recent_password=self.MEMBER_PASSWORD,
                agent_id=agent_id or self.target[owner],
                credentials=credentials, allow_owner=True)
        return self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type=channel_type, display_name=name,
            recent_password=self.ROOT_PASSWORD,
            agent_id=agent_id or "agent-a",
            credentials=credentials, scope="tenant")

    def _set_policy(self, **kwargs):
        return self.svc.set_tenant_channel_policy(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            recent_password=self.ROOT_PASSWORD, **kwargs)

    def _count(self, table, where="1=1", args=()):
        return self.svc._store.execute(
            "SELECT COUNT(*) c FROM %s WHERE %s" % (table, where), args)[0]["c"]


class PolicySchemaTests(_PolicyFixture):
    def test_the_policy_table_exists_with_its_columns(self):
        con = sqlite3.connect(self.svc._store.db_path)
        try:
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(tenant_channel_policies)")}
        finally:
            con.close()
        self.assertEqual(cols, {"tenant_id", "personal_enabled", "allowed_types_json",
                                "personal_instance_limit",
                                "tenant_personal_instance_limit",
                                "updated_by", "updated_at"})

    def test_instances_gain_governance_columns(self):
        con = sqlite3.connect(self.svc._store.db_path)
        try:
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(tenant_channel_instances)")}
        finally:
            con.close()
        self.assertIn("governance_disabled_at", cols)
        self.assertIn("governance_disabled_by", cols)

    def test_an_existing_instance_is_not_governance_disabled_by_the_migration(self):
        instance = self._create()

        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        self.assertIsNone(row["governance_disabled_at"])
        self.assertIsNone(row["governance_disabled_by"])
        self.assertTrue(row["active"], "the upgrade must not stop anyone")

    def test_the_policy_table_starts_empty_for_an_existing_tenant(self):
        self.assertEqual(self._count("tenant_channel_policies"), 0)


class PolicyDefaultsTests(_PolicyFixture):
    """No policy row means "nothing narrowed yet" — never "denied"."""

    def test_the_default_policy_is_unrestricted(self):
        policy = self.svc.get_tenant_channel_policy(
            actor_user_id=self.root["id"], tenant_id=self.ta)

        self.assertTrue(policy["personal_enabled"])
        self.assertEqual(policy["allowed_types"], [])
        self.assertEqual(policy["personal_instance_limit"], -1)
        self.assertEqual(policy["tenant_personal_instance_limit"], -1)

    def test_an_unrestricted_tenant_can_create_personal_instances(self):
        self._create(channel_type="feishu")
        self._create(channel_type="dingtalk", name="我的钉钉")

        self.assertEqual(self._count("tenant_channel_instances", "scope='user'"), 2)

    def test_the_policy_is_control_only(self):
        _expect_error(self, "forbidden", 403, self.svc.get_tenant_channel_policy,
                      actor_user_id=self.member, tenant_id=self.ta)

    def test_setting_the_policy_requires_control(self):
        _expect_error(self, "forbidden", 403, self.svc.set_tenant_channel_policy,
                      actor_user_id=self.member, tenant_id=self.ta,
                      recent_password=self.ROOT_PASSWORD, personal_enabled=False)

    def test_setting_the_policy_requires_the_recent_password(self):
        _expect_error(self, "invalid_old", 401, self.svc.set_tenant_channel_policy,
                      actor_user_id=self.root["id"], tenant_id=self.ta,
                      recent_password="not-the-password", personal_enabled=False)

    def test_the_policy_change_is_audited_without_private_material(self):
        self._set_policy(personal_enabled=False, allowed_types=["feishu"])

        events = self.svc._store.execute(
            "SELECT action, redacted_changes FROM audit_events"
            " WHERE action='channel.policy.update'")
        self.assertEqual(len(events), 1)
        self.assertNotIn("secret", events[0]["redacted_changes"].lower())

    def test_unknown_allowed_types_are_rejected(self):
        _expect_error(self, "bad_request", 400, self._set_policy,
                      allowed_types=["not_a_channel"])

    def test_an_impossible_limit_is_rejected(self):
        _expect_error(self, "bad_request", 400, self._set_policy,
                      personal_instance_limit=-2)


class AllowedTypesTests(_PolicyFixture):
    def test_a_type_outside_the_tenant_policy_is_refused(self):
        self._set_policy(allowed_types=["feishu"])

        _expect_error(self, "channel_type_not_allowed", 403, self._create,
                      channel_type="dingtalk", name="我的钉钉")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 0)
        self.assertEqual(self._count("credentials"), 0,
                         "a refused type must leave no credential")

    def test_a_type_inside_the_policy_is_accepted(self):
        self._set_policy(allowed_types=["feishu"])

        instance = self._create(channel_type="feishu")

        self.assertEqual(instance["channel_type"], "feishu")

    def test_the_policy_does_not_constrain_public_instances(self):
        """治理/允许类型针对个人接入；租户公共接入走原有管理门槛。"""
        self._set_policy(allowed_types=["feishu"])

        self._create(channel_type="dingtalk", name="公共钉钉",
                     scope="tenant")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='tenant'"), 1)

    def test_an_empty_allow_list_means_no_restriction(self):
        self._set_policy(allowed_types=[])

        self._create(channel_type="dingtalk", name="我的钉钉")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 1)

    def test_the_enable_path_rechecks_the_policy(self):
        instance = self._create(channel_type="feishu", name="我的飞书")
        # Both the disable and the refused re-enable are the *owner's* writes:
        # the policy must be re-decided on the path a member actually takes.
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.member, tenant_id=self.ta,
            instance_id=instance["id"], active=False,
            expected_version=instance["version"],
            recent_password=self.MEMBER_PASSWORD, allow_owner=True)
        self._set_policy(allowed_types=["dingtalk"])
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        _expect_error(self, "channel_type_not_allowed", 403,
                      self.svc.set_tenant_channel_instance_active,
                      actor_user_id=self.member, tenant_id=self.ta,
                      instance_id=instance["id"], active=True,
                      expected_version=row["version"],
                      recent_password=self.MEMBER_PASSWORD, allow_owner=True)


class PersonalQuotaTests(_PolicyFixture):
    def test_the_owner_limit_is_enforced(self):
        self._set_policy(personal_instance_limit=1)

        self._create(name="第一个")
        _expect_error(self, "quota_exceeded", 403, self._create, name="第二个")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 1)

    def test_a_zero_limit_denies_every_new_instance(self):
        self._set_policy(personal_instance_limit=0)

        _expect_error(self, "quota_exceeded", 403, self._create)

        self.assertEqual(self._count("credentials"), 0)

    def test_the_limit_is_per_owner(self):
        self._set_policy(personal_instance_limit=1)

        self._create(owner=self.member, name="A的")
        self._create(owner=self.other, name="B的")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 2)

    def test_the_tenant_wide_limit_is_enforced_across_owners(self):
        self._set_policy(tenant_personal_instance_limit=1)

        self._create(owner=self.member, name="A的")
        _expect_error(self, "quota_exceeded", 403, self._create,
                      owner=self.other, name="B的")

    def test_a_disabled_instance_still_occupies_its_slot(self):
        """停用不能成为无限创建对象的配额绕过。"""
        self._set_policy(personal_instance_limit=1)
        instance = self._create(name="第一个")
        # 本人自助停用（不是治理停机）：停用后名额仍被占用。
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.member, tenant_id=self.ta,
            instance_id=instance["id"], active=False,
            expected_version=instance["version"],
            recent_password=self.MEMBER_PASSWORD, allow_owner=True)

        _expect_error(self, "quota_exceeded", 403, self._create, name="第二个")

    def test_a_refused_create_leaves_no_half_built_instance(self):
        self._set_policy(personal_instance_limit=1)
        self._create(name="第一个")
        creds_before = self._count("credentials")
        versions_before = self._count("credential_versions")

        _expect_error(self, "quota_exceeded", 403, self._create, name="第二个")

        self.assertEqual(self._count("credentials"), creds_before)
        self.assertEqual(self._count("credential_versions"), versions_before)
        self.assertEqual(
            self._count("tenant_channel_instances", "display_name='第二个'"), 0)

    def test_public_instances_do_not_consume_the_personal_quota(self):
        self._set_policy(personal_instance_limit=1)
        self._create(name="公共的", scope="tenant")

        self._create(name="个人的")

        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 1)

    def test_concurrent_creation_cannot_take_the_last_slot_twice(self):
        """The race the spec calls out: at most one submit may win."""
        self._set_policy(personal_instance_limit=1)
        barrier = threading.Barrier(2)
        outcomes = {}

        def attempt(index):
            barrier.wait()
            try:
                self._create(name="并发%d" % index)
                outcomes[index] = "created"
            except IdentityServiceError as error:
                outcomes[index] = error.code

        threads = [threading.Thread(target=attempt, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(outcomes.values()),
                         ["created", "quota_exceeded"], outcomes)
        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 1,
            "the refused request must not have left a row behind")
        # Exactly one credential bundle: the loser wrote nothing.
        self.assertEqual(self._count("credentials"), 1)

    def test_two_concurrent_requests_both_fit_under_a_limit_of_two(self):
        self._set_policy(personal_instance_limit=2)
        barrier = threading.Barrier(2)
        outcomes = {}

        def attempt(index):
            barrier.wait()
            try:
                self._create(name="并发%d" % index)
                outcomes[index] = "created"
            except IdentityServiceError as error:
                outcomes[index] = error.code

        threads = [threading.Thread(target=attempt, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(outcomes.values()), ["created", "created"],
                         outcomes)
        self.assertEqual(
            self._count("tenant_channel_instances", "scope='user'"), 2)


class PersonalAccessSwitchTests(_PolicyFixture):
    def test_disabling_personal_access_refuses_new_instances(self):
        self._set_policy(personal_enabled=False)

        _expect_error(self, "personal_access_disabled", 403, self._create)

        self.assertEqual(self._count("credentials"), 0)

    def test_disabling_personal_access_does_not_touch_existing_instances(self):
        instance = self._create()

        self._set_policy(personal_enabled=False)

        row = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertTrue(row["active"], "已有实例不被治理开关直接改写")

    def test_the_enable_path_respects_the_switch(self):
        instance = self._create()
        # The switch is the tenant's policy, but the write that must obey it is
        # the owner's own: the disable and the refused enable both go through
        # the self-service branch.
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.member, tenant_id=self.ta,
            instance_id=instance["id"], active=False,
            expected_version=instance["version"],
            recent_password=self.MEMBER_PASSWORD, allow_owner=True)
        self._set_policy(personal_enabled=False)
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        _expect_error(self, "personal_access_disabled", 403,
                      self.svc.set_tenant_channel_instance_active,
                      actor_user_id=self.member, tenant_id=self.ta,
                      instance_id=instance["id"], active=True,
                      expected_version=row["version"],
                      recent_password=self.MEMBER_PASSWORD, allow_owner=True)


class GovernanceDisableTests(_PolicyFixture):
    def _disable(self, instance_id, reason="违规"):
        return self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance_id, disabled=True, reason=reason,
            recent_password=self.ROOT_PASSWORD)

    def test_disabling_records_who_and_when(self):
        instance = self._create()

        result = self._disable(instance["id"])

        self.assertTrue(result["governance_disabled"])
        row = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertIsNotNone(row["governance_disabled_at"])
        self.assertEqual(row["governance_disabled_by"], self.root["id"])

    def test_disabling_stops_the_instance_immediately(self):
        instance = self._create()

        self._disable(instance["id"])

        row = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertFalse(row["active"], "治理停用必须在后续调用前生效")

    def test_a_governance_disabled_instance_is_not_started(self):
        instance = self._create()
        self._disable(instance["id"])

        enabled_ids = {i["id"] for i in
                       self.svc.list_enabled_tenant_channel_instances()}

        self.assertNotIn(instance["id"], enabled_ids)

    def test_a_governance_stopped_row_is_never_started_even_if_marked_active(self):
        """The startup filter is a second, independent guard.

        The disable path also clears ``active``, so the two overlap; but the
        startup list must not trust ``active`` alone — a row stopped by governance
        may not be started on the strength of the owner's switch, whatever state
        that switch is in.
        """
        instance = self._create()
        with self.svc._store.connect() as con:
            con.execute(
                "UPDATE tenant_channel_instances SET governance_disabled_at="
                "unixepoch(), governance_disabled_by=? WHERE id=?",
                (self.root["id"], instance["id"]))
            con.commit()

        enabled_ids = {i["id"] for i in
                       self.svc.list_enabled_tenant_channel_instances()}

        self.assertNotIn(instance["id"], enabled_ids)

    def test_the_governance_event_is_audited_without_private_configuration(self):
        instance = self._create()

        self._disable(instance["id"])

        events = self.svc._store.execute(
            "SELECT redacted_changes FROM audit_events"
            " WHERE action='channel.personal.governance_disable'")
        self.assertEqual(len(events), 1)
        blob = events[0]["redacted_changes"]
        for leak in ("s3cr3t", "cli_personal_a", FEISHU_BUNDLE["feishu_bot_name"]):
            self.assertNotIn(leak, blob, leak)

    def test_governance_disable_applies_only_to_personal_instances(self):
        public = self._create(scope="tenant", name="公共的")

        _expect_error(self, "bad_request", 400, self._disable, public["id"])

    def test_governance_disable_requires_control(self):
        instance = self._create()

        _expect_error(self, "forbidden", 403,
                      self.svc.set_personal_instance_governance,
                      actor_user_id=self.member, tenant_id=self.ta,
                      instance_id=instance["id"], disabled=True, reason="x",
                      recent_password=self.ROOT_PASSWORD)

    def test_the_owner_cannot_clear_the_restriction_by_editing(self):
        """owner MUST NOT 通过重新保存或启用解除治理限制。"""
        instance = self._create()
        # 治理停机仍只经独立治理动作（管理员），不由可编辑列表下发。
        self._disable(instance["id"])
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        # ...而本人的"重新保存"走本人自助分支：改名可以，但治理标记不许被清掉。
        self.svc.update_tenant_channel_instance(
            actor_user_id=self.member, tenant_id=self.ta,
            instance_id=instance["id"], expected_version=row["version"],
            recent_password=self.MEMBER_PASSWORD, display_name="改个名",
            allow_owner=True)

        after = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertIsNotNone(after["governance_disabled_at"])
        self.assertEqual(after["governance_disabled_by"], self.root["id"])

    def test_the_owner_cannot_clear_the_restriction_by_enabling(self):
        instance = self._create()
        self._disable(instance["id"])
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        _expect_error(self, "governance_disabled", 403,
                      self.svc.set_tenant_channel_instance_active,
                      actor_user_id=self.member, tenant_id=self.ta,
                      instance_id=instance["id"], active=True,
                      expected_version=row["version"],
                      recent_password=self.MEMBER_PASSWORD, allow_owner=True)

        after = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertFalse(after["active"])

    def test_the_owner_cannot_rotate_credentials_to_escape_the_restriction(self):
        instance = self._create()
        self._disable(instance["id"])
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        _expect_error(self, "governance_disabled", 403,
                      self.svc.update_tenant_channel_instance,
                      actor_user_id=self.member, tenant_id=self.ta,
                      instance_id=instance["id"], expected_version=row["version"],
                      recent_password=self.MEMBER_PASSWORD,
                      credentials=FEISHU_BUNDLE, allow_owner=True)

        after = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertIsNotNone(after["governance_disabled_at"])
        cred = self.svc._store.execute(
            "SELECT version FROM credentials WHERE name=?",
            ("channel:%s" % instance["id"],))
        self.assertEqual(cred[0]["version"], 1, "不得借轮换绕过治理停用")

    def test_lifting_the_restriction_does_not_auto_restore_the_instance(self):
        instance = self._create()
        self._disable(instance["id"])

        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance["id"], disabled=False,
            recent_password=self.ROOT_PASSWORD)

        row = self.svc.get_tenant_channel_instance_row(instance["id"])
        self.assertIsNone(row["governance_disabled_at"])
        self.assertFalse(row["active"], "解除限制后须由本人明确启用")

    def test_after_lifting_the_owner_may_explicitly_enable_again(self):
        instance = self._create()
        self._disable(instance["id"])
        self.svc.set_personal_instance_governance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=instance["id"], disabled=False,
            recent_password=self.ROOT_PASSWORD)
        row = self.svc.get_tenant_channel_instance_row(instance["id"])

        enabled = self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.member, tenant_id=self.ta,
            instance_id=instance["id"], active=True,
            expected_version=row["version"],
            recent_password=self.MEMBER_PASSWORD, allow_owner=True)

        self.assertTrue(enabled["active"])


if __name__ == "__main__":
    unittest.main()
