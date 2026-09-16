# encoding:utf-8
"""存量个人实例的无效目标：待修复投影、修复动词与关闭动词（tasks 5.1 / 5.2）。

阶段 2 的严格目标判据上线后，创建于它之前的 ``scope='user'`` 行可能停在一个已经不能用的目标上：
空目标、公共/共享目标、他人私有、跨租户、已被删除或已停用的目标。规范要求这些行
（``personal-channel-workbench``「存量个人实例可修复且公共路由保持兼容」）：

* 仍然**可见**，并以 ``target`` 状态与 ``reason`` 如实说明；
* 可以**修复**——由本人选择合法的私有目标，且修复不改归属、不换默认、不动凭据与版本历史；
* 仍然可以**关闭**——改名、停用、撤销凭据、解除身份关联都不要求先修好目标；
* 不自动重建：读取、列表、运行态的读取都不改行；
* 公共路径不受影响：无目标的公共实例继续解析租户公共默认智能体，公共管理资格不变。

本文件只覆盖 5.1/5.2。修复动作的投影仍在
``tests/test_personal_channel_console.py::TargetStatusAndRepairTests``（由其它工作流持有）。
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from auth.service import IdentityService, IdentityServiceError
from tests._helpers import (
    IdentityStack, WebAppHarness, personal_channel_target, personal_target_roster,
)

FEISHU_BUNDLE = {
    "feishu_app_id": "cli_personal_a",
    "feishu_app_secret": "s3cr3t-personal",
    "feishu_bot_name": "Alice Bot",
}

#: Every Agent the fixtures name. ``agent-a``/``agent-b`` stay **shared** (they are
#: the tenants' public defaults and must never be a personal target); the rest are
#: private targets, one of which the roster holds switched off so "the target was
#: disabled" is a fact of the registry rather than a patched method.
#: ``agent-gone`` is deliberately absent: it stands for a target whose binding was
#: removed, which the predicate must answer as "not yours" and not as "disabled".
ROSTER = ("agent-a", "agent-b", "alice-own", "alice-spare", "alice-off",
          "alice-doomed", "bob-own", "globex-own", "alice-globex")


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _expect_error(case, code, status, fn, *args, **kwargs):
    with case.assertRaises(IdentityServiceError) as caught:
        fn(*args, **kwargs)
    case.assertEqual(caught.exception.code, code, str(caught.exception))
    case.assertEqual(caught.exception.status, status)
    return caught.exception


class _Fixture(unittest.TestCase):
    """Acme with two members and their private Agents, plus a second tenant."""

    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    ROOT_PW = "Str0ngRootFinal"
    MEMBER_PW = "Str0ngMemberFinal"

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)

        provisioner = patch(
            "agent.personal_assistant.get_personal_assistant_provisioner",
            return_value=type("P", (), {"provision": lambda *a, **k: None})())
        provisioner.start()
        self.addCleanup(provisioner.stop)

        # ``personal_target_roster`` yields the settings it pinned, so a test can
        # switch one roster Agent off: the registry signature follows the roster
        # config, so unpinning makes the next lookup read the change.
        rosters = personal_target_roster(*ROSTER)
        self.roster_settings = rosters.__enter__()
        self.addCleanup(lambda: rosters.__exit__(None, None, None))

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", self.ROOT_PW)
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.svc.create_tenant(
            actor_user_id=self.root["id"], code="globex", name="Globex",
            admin_username="globexadmin", admin_display="Globex Admin",
            admin_password="Str0ngPass9", recent_password=self.ROOT_PW,
            shared_root="/s/globex")
        self.tb = [t for t in self.svc.list_tenants()
                   if t["code"] == "globex"][0]["id"]
        self.svc.change_password(
            self.svc.login("globexadmin", "Str0ngPass9").token,
            "Str0ngPass9", "Str0ngGlobexFinal")
        self.admin_b = [m for m in self.svc.list_members(self.tb)["items"]
                        if m["username"] == "globexadmin"][0]["user_id"]

        # The tenants' shared Agents: what a *public* instance with no target
        # falls back to (``resolved_public_default_agent_id``), and what a
        # personal row may never name.
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)
        self.svc.bind_agent(tenant_id=self.tb, agent_id="agent-b",
                            private_owner_user_id=None)

        self.alice = self._member("alice")
        self.bob = self._member("bob")

        self.alice_own = personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=self.alice, agent_id="alice-own")
        self.alice_spare = personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=self.alice, agent_id="alice-spare")
        self.bob_own = personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=self.bob, agent_id="bob-own")
        self.alice_off = personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=self.alice, agent_id="alice-off")
        self.alice_doomed = personal_channel_target(
            self.svc, tenant_id=self.ta, user_id=self.alice, agent_id="alice-doomed")
        # Cross-tenant shapes: another tenant's private Agent, and one of Alice's
        # own that is bound in the *other* tenant (same owner, wrong tenant).
        self.globex_own = personal_channel_target(
            self.svc, tenant_id=self.tb, user_id=self.admin_b, agent_id="globex-own")
        self.alice_globex = personal_channel_target(
            self.svc, tenant_id=self.tb, user_id=self.alice, agent_id="alice-globex")
        # Switched off *in the registry*, with the binding left intact: the
        # ownership half holds, the enablement half does not.
        self._set_agent_enabled(self.alice_off, False)

        self._sequence = 0

    # -- fixtures ---------------------------------------------------------

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _set_agent_enabled(self, agent_id, enabled):
        """Flip one roster entry and let the registry re-resolve it."""
        from agent.registry import set_agent_registry

        for entry in self.roster_settings["agents"]:
            if entry["id"] == agent_id:
                entry["enabled"] = bool(enabled)
        set_agent_registry(None)

    def _member(self, username):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            operation="create-new", username=username,
            display_name=username.title(), temporary_password="MemTempPass1",
            roles=["member"])
        user_id = [m for m in self.svc.list_members(self.ta)["items"]
                   if m["username"] == username][0]["user_id"]
        # A credential write proves presence with the account password, so the
        # member has to be a normal account before any of this is reachable.
        session = self.svc.login(username, "MemTempPass1")
        self.svc.change_password(session.token, "MemTempPass1", self.MEMBER_PW)
        return user_id

    def _legacy_row(self, owner, agent_id, *, display_name=None, active=1,
                    tenant_id=None):
        """Seed a row the way a client *before* the target rule could leave it.

        Written straight to storage on purpose: no current write path accepts a
        personal row with an empty, shared or foreign target, so a fixture that
        went through the service could not produce the rows 5.1/5.2 are about.
        """
        self._sequence += 1
        instance_id = "ci_legacy_%d" % self._sequence
        self._store_row(
            instance_id, owner, agent_id,
            display_name=display_name or "Legacy %d" % self._sequence,
            active=active, tenant_id=tenant_id)
        return instance_id

    def _store_row(self, instance_id, owner, agent_id, *, display_name, active,
                   tenant_id=None):
        self.svc._store.execute(
            "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
            " display_name, agent_id, active, scope, owner_user_id, created_by)"
            " VALUES(?,?,?,?,?,?,'user',?,?)",
            (instance_id, tenant_id or self.ta, "feishu", display_name, agent_id,
             active, owner, owner))
        return instance_id

    def _create(self, owner=None, *, agent_id=None, display_name="My Bot",
                bundle=None):
        return self.svc.create_personal_channel_instance(
            actor_user_id=owner or self.alice, tenant_id=self.ta,
            channel_type="feishu", display_name=display_name,
            agent_id=(self.alice_own if agent_id is None else agent_id),
            credentials=dict(bundle or FEISHU_BUNDLE),
            recent_password=self.MEMBER_PW)

    def _repair(self, instance_id, agent_id, *, actor=None, expected_version=1,
                password=None):
        """Repair by naming a new target on the ordinary personal edit door.

        This is deliberately *not* a bespoke ``repair_...`` verb: the projection
        advertises ``actions["repair_target"]`` and the console sends it as
        ``action="update"`` with an ``agent_id``, so the repair operation and the
        edit operation have to be one code path or the console's promise and the
        server's rules drift apart (task 5.1).
        """
        return self.svc.update_personal_channel_instance(
            actor_user_id=actor or self.alice, tenant_id=self.ta,
            instance_id=instance_id, agent_id=agent_id,
            expected_version=expected_version,
            recent_password=password or self.MEMBER_PW)

    def _link(self, instance_id, subject="ou_alice"):
        """A real link: a code minted for the instance, redeemed by its sender."""
        challenge = self.svc.start_personal_channel_binding(
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=instance_id)
        return self.svc.redeem_personal_channel_challenge(
            tenant_id=self.ta, instance_id=instance_id, code=challenge["code"],
            provider="feishu", issuer="cli_personal_a", subject=subject)

    # -- readers ----------------------------------------------------------

    def _row(self, instance_id):
        rows = self.svc._store.execute(
            "SELECT * FROM tenant_channel_instances WHERE id=?", (instance_id,))
        return dict(rows[0])

    def _credential(self, instance_id):
        rows = self.svc._store.execute(
            "SELECT id, ciphertext, active, version FROM credentials"
            " WHERE tenant_id=? AND name=?",
            (self.ta, "channel:%s" % instance_id))
        return dict(rows[0]) if rows else None

    def _credential_versions(self, instance_id):
        credential = self._credential(instance_id)
        if not credential:
            return []
        return [dict(r) for r in self.svc._store.execute(
            "SELECT version, ciphertext, action FROM credential_versions"
            " WHERE credential_id=? ORDER BY version", (credential["id"],))]

    def _view(self, instance_id):
        return self.svc.get_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=instance_id)


class UnusableTargetVisibilityTests(_Fixture):
    """5.1 — 存量无效目标仍然可见、被标记待修复，且不泄露非本人目标。"""

    def test_every_unusable_shape_is_visible_and_flagged_for_repair(self):
        shapes = [
            ("", "missing", "personal_agent_required"),
            (self.bob_own, "invalid", "personal_agent_forbidden"),
            (self.globex_own, "invalid", "personal_agent_forbidden"),
            (self.alice_globex, "invalid", "personal_agent_forbidden"),
            ("agent-a", "invalid", "personal_agent_forbidden"),
            ("agent-gone", "invalid", "personal_agent_forbidden"),
            (self.alice_off, "disabled", "personal_agent_disabled"),
        ]
        instances = {
            agent_id: self._legacy_row(
                self.alice, agent_id, display_name="Legacy %s" % (agent_id or "empty"))
            for agent_id, _, _ in shapes
        }

        listing = self.svc.list_personal_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)
        self.assertEqual(sorted(i["id"] for i in listing["items"]),
                         sorted(instances.values()),
                         "an unusable target must not hide the row")

        for agent_id, state, reason in shapes:
            with self.subTest(target=agent_id or "<empty>"):
                view = self._view(instances[agent_id])
                self.assertEqual(view["target"]["state"], state)
                self.assertEqual(view["target"]["reason"], reason)
                self.assertTrue(view["actions"]["repair_target"],
                                "a broken target has to be repairable")
                # Exactly the two verbs that would put it back into service are
                # withheld; every closing verb stays reachable (5.2).
                self.assertFalse(view["actions"]["enable"])
                self.assertFalse(view["actions"]["bind"])
                self.assertTrue(view["actions"]["edit"])
                if state != "disabled":
                    self.assertFalse(view["target"]["enabled"])
                # The stored row is read back exactly as written.
                self.assertEqual(self._row(instances[agent_id])["agent_id"], agent_id)

    def test_a_foreign_target_is_never_read_back(self):
        """归属不成立时连名称都不投影，拒绝也不能回读他人目标。"""
        for agent_id in (self.bob_own, self.globex_own, self.alice_globex,
                         "agent-gone"):
            with self.subTest(target=agent_id):
                view = self._view(self._legacy_row(self.alice, agent_id))
                self.assertEqual(view["target"]["name"], "")
                self.assertEqual(view["target"]["agent_id"], agent_id)

    def test_a_disabled_target_keeps_its_own_name_and_says_why(self):
        """本人被停用的目标没有可隐瞒的东西，"把它打开"是可执行的下一步。"""
        view = self._view(self._legacy_row(self.alice, self.alice_off))
        self.assertEqual(view["target"]["state"], "disabled")
        self.assertEqual(view["target"]["name"], self.alice_off)
        self.assertFalse(view["target"]["enabled"])
        _expect_error(
            self, "personal_agent_disabled", 403,
            self.svc.set_personal_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=self._legacy_row(self.alice, self.alice_off),
            active=True, expected_version=1, recent_password=self.MEMBER_PW)


class RepairOperationTests(_Fixture):
    """5.1 — 修复是一个真实动作，且只改目标。"""

    def test_a_repair_changes_the_target_and_nothing_else(self):
        instance_id = self._legacy_row(self.alice, "")
        before = self._row(instance_id)

        repaired = self._repair(instance_id, self.alice_spare)

        self.assertEqual(repaired["target"]["state"], "ok")
        self.assertEqual(repaired["target"]["agent_id"], self.alice_spare)
        after = self._row(instance_id)
        for field in ("id", "tenant_id", "channel_type", "display_name", "scope",
                      "owner_user_id", "created_by", "active"):
            self.assertEqual(after[field], before[field],
                             "%s must not change on a repair" % field)
        self.assertEqual(after["agent_id"], self.alice_spare)
        self.assertEqual(after["version"], before["version"] + 1)

    def test_a_repair_cannot_move_ownership(self):
        instance_id = self._legacy_row(self.alice, "")
        for actor, password in ((self.bob, self.MEMBER_PW),
                                (self.root["id"], self.ROOT_PW)):
            with self.subTest(actor=actor):
                _expect_error(self, "forbidden", 403, self._repair,
                              instance_id, self.bob_own, actor=actor,
                              password=password)
        # ...and Alice cannot repair Bob's row either.
        bobs = self._legacy_row(self.bob, "")
        _expect_error(self, "forbidden", 403, self._repair, bobs, self.alice_spare)
        self.assertEqual(self._row(instance_id)["agent_id"], "")
        self.assertEqual(self._row(bobs)["agent_id"], "")
        self.assertEqual(self._row(instance_id)["owner_user_id"], self.alice)
        self.assertEqual(self._row(bobs)["owner_user_id"], self.bob)

    def test_a_repair_cannot_take_over_a_public_instance(self):
        """个人入口只处理 ``scope='user'``：公共实例不属于本人，也不被改写。"""
        public = self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type="feishu", display_name="Tenant Bot", agent_id="",
            credentials=dict(FEISHU_BUNDLE, feishu_app_id="cli_tenant_shared"),
            recent_password=self.ROOT_PW, scope="tenant")
        _expect_error(self, "forbidden", 403, self._repair, public["id"],
                      self.alice_spare)
        self.assertEqual(self._row(public["id"])["agent_id"], "")
        self.assertEqual(self._row(public["id"])["scope"], "tenant")

    def test_a_repair_without_a_target_is_a_malformed_write(self):
        instance_id = self._legacy_row(self.alice, "")
        for label, agent_id in (("empty", ""), ("blank", "   ")):
            with self.subTest(target=label):
                # The repair door *is* the edit door, so there is one refusal to
                # prove: naming an empty target never silently keeps the stored
                # one, and a legacy empty row cannot be "repaired" back to empty.
                _expect_error(self, "personal_agent_required", 400, self._repair,
                              instance_id, agent_id)
        row = self._row(instance_id)
        self.assertEqual(row["agent_id"], "", "a refused repair writes nothing")
        self.assertEqual(row["version"], 1, "not even the version moved")

    def test_an_empty_target_is_refused_even_when_the_row_is_healthy(self):
        """命名空目标永远是畸形写入：不能读成"悄悄解绑一个健康行"。"""
        created = self._create()
        _expect_error(
            self, "personal_agent_required", 400,
            self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            agent_id="", recent_password=self.MEMBER_PW)
        row = self._row(created["id"])
        self.assertEqual(row["agent_id"], self.alice_own)
        self.assertEqual(row["version"], created["version"])

    def test_the_operator_door_cannot_rewrite_a_personal_row(self):
        """管理门不再能改写他人的本人连接（task 6.1）。

        共用接口后，管理员维护本租户公共连接与本人连接，另一成员的
        ``scope='user'`` 行不在其可编辑范围内：改名与"把它管理成一个空目标"都按范围
        拒绝（``forbidden``），治理停机仍只经 ``set_personal_instance_governance``
        这一独立治理动作，可编辑列表不成为治理入口。拒绝不得留下任何改动：整行的
        ``display_name``、``agent_id``、``version`` 与其它字段原样保留。

        The *operator* door used to reach this row (that is what
        ``test_the_operator_door_cannot_empty_a_personal_target_either`` asserted
        with ``personal_agent_required``); the unified surface closes it, so the
        assertion moved from "the operator cannot empty the target" to "the
        operator cannot rewrite the row at all".
        """
        instance_id = self._legacy_row(self.alice, "")
        before = self._row(instance_id)
        attempts = (
            ("rename", {"display_name": "Operator Rename"}),
            ("empty-target", {"display_name": "Operator Rename", "agent_id": ""}),
        )
        for label, body in attempts:
            with self.subTest(edit=label):
                _expect_error(
                    self, "forbidden", 403,
                    self.svc.update_tenant_channel_instance,
                    actor_user_id=self.root["id"], tenant_id=self.ta,
                    instance_id=instance_id, expected_version=1,
                    recent_password=self.ROOT_PW, **body)
        self.assertEqual(self._row(instance_id), before,
                         "a refused operator edit must leave the row untouched")

    def test_the_owner_can_still_rename_their_own_personal_row(self):
        """本人路径的既有能力不回退：改名照旧，且空目标仍被拒。

        Closing the operator door on a *colleague's* row does not freeze the
        owner's own door: an unusable row stays renameable by the member who owns
        it (5.2). Naming an empty target is still a malformed write on that door
        too, and the refusal changes neither the target nor the version.
        """
        instance_id = self._legacy_row(self.alice, "")
        renamed = self.svc.update_tenant_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=instance_id, expected_version=1,
            display_name="Owner Rename", recent_password=self.MEMBER_PW,
            allow_owner=True)
        self.assertEqual(renamed["display_name"], "Owner Rename")
        self.assertEqual(self._row(instance_id)["agent_id"], "",
                         "a rename is not a repair and must not touch the target")

        row = self._row(instance_id)
        _expect_error(
            self, "personal_agent_required", 400,
            self.svc.update_tenant_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=instance_id, expected_version=row["version"],
            agent_id="", display_name="Owner Rename",
            recent_password=self.MEMBER_PW, allow_owner=True)
        after = self._row(instance_id)
        self.assertEqual(after["agent_id"], "")
        self.assertEqual(after["version"], row["version"],
                         "the refusal must not version the row")

    def test_a_repair_refuses_every_illegal_target(self):
        instance_id = self._legacy_row(self.alice, "")
        illegal = [
            (self.bob_own, "personal_agent_forbidden"),
            (self.alice_globex, "personal_agent_forbidden"),
            (self.globex_own, "personal_agent_forbidden"),
            ("agent-a", "personal_agent_forbidden"),
            ("agent-gone", "personal_agent_forbidden"),
            (self.alice_off, "personal_agent_disabled"),
        ]
        for agent_id, code in illegal:
            with self.subTest(target=agent_id):
                error = _expect_error(self, code, 403, self._repair,
                                      instance_id, agent_id)
                self.assertNotIn(agent_id, str(error),
                                 "the refusal must not echo the target back")
                self.assertEqual(self._row(instance_id)["agent_id"], "")
                self.assertEqual(self._row(instance_id)["version"], 1)

    def test_a_repair_never_substitutes_the_default_or_a_public_agent(self):
        instance_id = self._legacy_row(self.alice, "")
        # Both refused shapes are "not selectable": neither the roster default
        # (``agent-a``) nor the stored (empty) value may be written instead.
        _expect_error(self, "personal_agent_forbidden", 403, self._repair,
                      instance_id, "agent-a")
        _expect_error(self, "personal_agent_required", 400, self._repair,
                      instance_id, "")
        self.assertEqual(self._row(instance_id)["agent_id"], "")
        repaired = self._repair(instance_id, self.alice_spare)
        self.assertEqual(self._row(instance_id)["agent_id"], self.alice_spare)
        self.assertNotEqual(repaired["target"]["agent_id"], "agent-a")

    def test_a_repair_is_re_decided_against_the_live_target(self):
        """选目标时的快照不能当授权：停用的目标先拒后准。"""
        instance_id = self._legacy_row(self.alice, "")
        _expect_error(self, "personal_agent_disabled", 403, self._repair,
                      instance_id, self.alice_off)
        self._set_agent_enabled(self.alice_off, True)
        repaired = self._repair(instance_id, self.alice_off)
        self.assertEqual(repaired["target"]["state"], "ok")
        self.assertTrue(repaired["target"]["enabled"])

    def test_a_stale_version_repair_is_refused(self):
        instance_id = self._legacy_row(self.alice, "")
        _expect_error(self, "conflict", 409, self._repair, instance_id,
                      self.alice_spare, expected_version=99)
        self.assertEqual(self._row(instance_id)["agent_id"], "")

    def test_a_repair_keeps_the_credential_and_its_version_history(self):
        created = self._create()
        rotated = self.svc.update_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=created["version"],
            credentials=dict(FEISHU_BUNDLE, feishu_app_secret="second-secret"),
            recent_password=self.MEMBER_PW)
        self.assertEqual(rotated["credential"]["version"], 2)
        credential_before = self._credential(created["id"])
        versions_before = self._credential_versions(created["id"])
        self.assertEqual(len(versions_before), 2)

        # The Agent Alice selected is turned tenant-shared afterwards: the row
        # is now broken exactly the way production breaks one.
        self.svc.make_agent_tenant_shared(agent_id=self.alice_own,
                                         actor_user_id=self.root["id"])
        broken = self._view(created["id"])
        self.assertEqual(broken["target"]["state"], "invalid")
        self.assertTrue(broken["actions"]["repair_target"])

        repaired = self._repair(created["id"], self.alice_spare,
                                expected_version=broken["version"])

        self.assertEqual(self._credential(created["id"]), credential_before,
                         "a repair must not rewrite the credential row")
        self.assertEqual(self._credential_versions(created["id"]), versions_before,
                         "a repair must not append a credential version")
        self.assertEqual(repaired["credential"], broken["credential"],
                         "the credential the console shows is unchanged")
        self.assertTrue(repaired["credential"]["configured"])
        self.assertEqual(
            self.svc.channel_instance_credentials(self.ta, created["id"])
                ["feishu_app_secret"], "second-secret",
            "the stored bundle survives the repair")


class ClosingVerbsOnAnUnusableRowTests(_Fixture):
    """5.2 — 目标无效的行仍然可改名、停用、撤销、解除关联。"""

    def test_a_broken_row_can_still_be_renamed(self):
        instance_id = self._legacy_row(self.alice, "")
        renamed = self.svc.update_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=instance_id, expected_version=1,
            display_name="Renamed", recent_password=self.MEMBER_PW)
        self.assertEqual(renamed["display_name"], "Renamed")
        row = self._row(instance_id)
        self.assertEqual(row["agent_id"], "",
                         "a rename is not a repair and must not touch the target")
        self.assertEqual(renamed["target"]["state"], "missing")

    def test_a_broken_row_can_still_be_disabled_but_not_enabled(self):
        instance_id = self._legacy_row(self.alice, "", active=1)
        disabled = self.svc.set_personal_channel_instance_active(
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=instance_id,
            active=False, expected_version=1, recent_password=self.MEMBER_PW)
        self.assertFalse(disabled["active"])
        # Re-opening is refused: the row has no target that could run.
        _expect_error(
            self, "personal_agent_required", 400,
            self.svc.set_personal_channel_instance_active,
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=instance_id,
            active=True, expected_version=disabled["version"],
            recent_password=self.MEMBER_PW)
        self.assertFalse(self._row(instance_id)["active"])

    def test_a_broken_row_cannot_rotate_its_credentials(self):
        """无效目标的行既能被撤销，也不能被"加固"：轮换是要恢复运行的那一半。"""
        created = self._create()
        self.svc.make_agent_tenant_shared(agent_id=self.alice_own,
                                         actor_user_id=self.root["id"])
        broken = self._view(created["id"])
        self.assertEqual(broken["target"]["state"], "invalid")
        credential_before = self._credential(created["id"])
        versions_before = self._credential_versions(created["id"])

        _expect_error(
            self, "personal_agent_forbidden", 403,
            self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=created["id"],
            expected_version=broken["version"],
            credentials=dict(FEISHU_BUNDLE, feishu_app_secret="rotated-anyway"),
            recent_password=self.MEMBER_PW)

        self.assertEqual(self._credential(created["id"]), credential_before,
                         "a refused rotation writes no ciphertext")
        self.assertEqual(self._credential_versions(created["id"]), versions_before,
                         "a refused rotation appends no version")
        # ...and the very shape 5.1 is about: a legacy row with no target at all.
        legacy = self._legacy_row(self.alice, "")
        _expect_error(
            self, "personal_agent_required", 400,
            self.svc.update_personal_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta, instance_id=legacy,
            expected_version=1, credentials=dict(FEISHU_BUNDLE),
            recent_password=self.MEMBER_PW)
        self.assertIsNone(self._credential(legacy),
                          "a refused rotation must not mint a credential")
        self.assertEqual(self._row(legacy)["version"], 1)

    def test_a_broken_row_can_still_have_its_credential_revoked(self):
        created = self._create()
        versions = self._credential_versions(created["id"])
        self.svc.make_agent_tenant_shared(agent_id=self.alice_own,
                                         actor_user_id=self.root["id"])
        row = self._row(created["id"])

        revoked = self.svc.revoke_personal_channel_credentials(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=row["version"],
            recent_password=self.MEMBER_PW)

        self.assertFalse(revoked["active"])
        self.assertFalse(revoked["credential"]["configured"])
        self.assertFalse(self._credential(created["id"])["active"])
        self.assertEqual(self._credential_versions(created["id"]), versions,
                         "revocation is a deactivation, not a delete")

    def test_a_link_can_still_be_unlinked_when_the_target_is_unusable(self):
        created = self._create()
        self._link(created["id"])
        self.assertIsNotNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"]))

        self.svc.make_agent_tenant_shared(agent_id=self.alice_own,
                                         actor_user_id=self.root["id"])
        broken = self._view(created["id"])
        self.assertEqual(broken["target"]["state"], "invalid")
        self.assertTrue(broken["actions"]["unbind"])
        self.assertFalse(broken["actions"]["bind"])

        self.svc.unlink_personal_channel_instance(
            actor_user_id=self.alice, tenant_id=self.ta,
            instance_id=created["id"], expected_version=broken["version"])

        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"]))
        self.assertEqual(self._row(created["id"])["agent_id"], self.alice_own,
                         "unlinking takes back the identity, not the target")

        repaired = self._repair(created["id"], self.alice_spare,
                                expected_version=self._row(created["id"])["version"])
        self.assertEqual(repaired["target"]["state"], "ok")
        self.assertIsNone(self.svc.personal_channel_link(
            tenant_id=self.ta, user_id=self.alice, instance_id=created["id"]),
            "a repair does not resurrect a link that was removed")

    def test_reading_or_listing_never_rewrites_a_row(self):
        healthy = self._create(display_name="Healthy")
        broken = self._legacy_row(self.alice, "", display_name="Broken")

        def snapshot(instance_id):
            return (self._row(instance_id), self._credential(instance_id),
                    self._credential_versions(instance_id))

        before = {i: snapshot(i) for i in (healthy["id"], broken)}
        for _ in range(2):
            self.svc.list_personal_channel_instances(
                actor_user_id=self.alice, tenant_id=self.ta)
            self.svc.personal_channel_workspace(
                actor_user_id=self.alice, tenant_id=self.ta)
            self._view(healthy["id"])
            self._view(broken)
        after = {i: snapshot(i) for i in (healthy["id"], broken)}

        self.assertEqual(after, before,
                         "a read must not rebuild, repair or re-version a row")
        self.assertEqual(self._view(healthy["id"])["target"]["state"], "ok")
        self.assertFalse(self._view(healthy["id"])["actions"]["repair_target"])
        self.assertEqual(self._view(broken)["target"]["state"], "missing")
        self.assertTrue(self._view(broken)["actions"]["repair_target"])


class PublicPathRegressionTests(_Fixture):
    """5.2 — 收紧个人路径不得改变公共实例的语义。"""

    def _public(self, display_name="Tenant Bot", agent_id="", app_id=None, **over):
        args = dict(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type="feishu", display_name=display_name, agent_id=agent_id,
            credentials=dict(FEISHU_BUNDLE,
                             feishu_app_id=app_id or "cli_public_%s" % display_name),
            recent_password=self.ROOT_PW, scope="tenant")
        args.update(over)
        return self.svc.create_tenant_channel_instance(**args)

    def test_a_public_instance_with_no_target_keeps_the_tenant_default_route(self):
        public = self._public(display_name="NoTarget")

        self.assertEqual(self._row(public["id"])["agent_id"], "")
        self.assertEqual(self._row(public["id"])["scope"], "tenant")
        # The empty target is not an error on the public path: it resolves to
        # the tenant's shared default, unchanged by the personal rule.
        self.assertEqual(
            self.svc.resolved_public_default_agent_id(self.ta), "agent-a")
        self.assertIn(public["id"], [i["id"] for i in self.svc
                                     .list_tenant_channel_instances(
                                         actor_user_id=self.root["id"],
                                         tenant_id=self.ta)["items"]])

    def test_a_public_target_is_still_optional_and_still_unbindable(self):
        """公共实例的空目标是"未指定"，显式写入空目标仍然是合法的解绑。"""
        named = self._public(display_name="Named", agent_id="agent-a",
                             app_id="cli_public_named")
        row = self._row(named["id"])

        unbound = self.svc.update_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=named["id"], expected_version=row["version"],
            agent_id="", recent_password=self.ROOT_PW)

        self.assertEqual(unbound["agent_id"], "")
        self.assertEqual(self._row(named["id"])["agent_id"], "")

    def test_a_public_instance_may_never_route_to_a_private_agent(self):
        _expect_error(
            self, "forbidden", 403, self._public,
            display_name="Private", agent_id=self.alice_own,
            app_id="cli_public_private")

    def test_public_management_eligibility_is_unchanged(self):
        """公共实例的管理资格不变：公共行既不可创建给成员，也不下发给成员。

        统一接口后列举不再是控制者专属（范围由服务端按对象判定，task 6.1）：普通成员
        可以调用同一接口，但只得到本人 ``scope='user'`` 行，本租户的公共行不会出现，
        创建公共实例仍是管理资格专属。所以这条回归断言从"成员列举被拒"改为"成员列举
        的公共半边仍被拒"，公共路径的资格本身没有变化。
        """
        public = self._public(display_name="Tenant Only")
        _expect_error(
            self, "forbidden", 403, self.svc.create_tenant_channel_instance,
            actor_user_id=self.alice, tenant_id=self.ta, channel_type="feishu",
            display_name="Sneaky", agent_id="agent-a",
            credentials=dict(FEISHU_BUNDLE), recent_password=self.MEMBER_PW,
            scope="tenant")
        # 成员的列举范围只有本人行：此处她还没有任何连接，公共行也不在其中。
        member_listing = self.svc.list_tenant_channel_instances(
            actor_user_id=self.alice, tenant_id=self.ta)["items"]
        self.assertNotIn(public["id"], [i["id"] for i in member_listing])
        self.assertEqual(member_listing, [])
        # The positive control: the same calls work for the tenant's controller.
        controller_listing = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)["items"]
        self.assertIn(public["id"], [i["id"] for i in controller_listing])
        self._public(display_name="Owned by the tenant")

    def test_the_two_surfaces_do_not_list_each_others_rows(self):
        public = self._public(display_name="Tenant Only")
        mine = self._create()
        self.assertNotIn(public["id"], [i["id"] for i in
                                        self.svc.list_personal_channel_instances(
                                            actor_user_id=self.alice,
                                            tenant_id=self.ta)["items"]])
        self.assertNotIn(mine["id"], [i["id"] for i in
                                      self.svc.list_tenant_channel_instances(
                                          actor_user_id=self.root["id"],
                                          tenant_id=self.ta)["items"]])


class RepairActionOverHttpTests(unittest.TestCase):
    """``actions["repair_target"]`` 必须是一个控制台真能调用的动作。

    The projection telling the console "you may repair this" is only honest if
    the same surface accepts the call. The console's repair *is* the ordinary
    edit — ``action="update"`` carrying the re-selected ``agent_id`` — so the
    action advertised in ``actions`` has no second, un-dispatched verb behind it,
    and an unusable target can be replaced over the real route.
    """

    def setUp(self):
        self.web = WebAppHarness(tempfile.mkdtemp(prefix="target-repair-http-"))
        self.addCleanup(self.web.close)
        self.web.add_agent("shared-agent", "alice-own", "alice-spare")
        self.alice = self.web.member("alice", ["member"])
        personal_channel_target(self.web.service, tenant_id=self.web.tenant_id,
                                user_id=self.alice, agent_id="alice-own")
        personal_channel_target(self.web.service, tenant_id=self.web.tenant_id,
                                user_id=self.alice, agent_id="alice-spare")
        self.instance_id = "ci_legacy_http"
        self.web.service._store.execute(
            "INSERT INTO tenant_channel_instances(id, tenant_id, channel_type,"
            " display_name, agent_id, active, scope, owner_user_id, created_by)"
            " VALUES(?,?,?,?,?,1,'user',?,?)",
            (self.instance_id, self.web.tenant_id, "feishu", "Legacy Bot", "",
             self.alice, self.alice))
        self.token = self.web.login("alice")
        self.path = "/api/personal/channels/" + self.instance_id

    def _post(self, **body):
        payload = {"expected_version": 1,
                   "recent_password": IdentityStack.MEMBER_PASSWORD}
        payload.update(body)
        return self.web.post(self.path, payload, token=self.token)

    def _stored_target(self):
        rows = self.web.service._store.execute(
            "SELECT agent_id, version FROM tenant_channel_instances WHERE id=?",
            (self.instance_id,))
        return dict(rows[0])

    def test_the_advertised_repair_action_is_callable(self):
        read = self.web.get(self.path, token=self.token)
        self.assertTrue(self.web.json(read)["instance"]["actions"]["repair_target"])

        # Exactly what the console sends for ``repair_target``.
        response = self._post(action="update", agent_id="alice-spare")

        body = self.web.json(response)
        self.assertEqual(body["status"], "success", body)
        self.assertEqual(body["instance"]["target"]["state"], "ok")
        self.assertEqual(body["instance"]["target"]["agent_id"], "alice-spare")
        self.assertEqual(self._stored_target()["agent_id"], "alice-spare")

    def test_the_repair_action_refuses_an_unnamed_target(self):
        response = self._post(action="update", agent_id="")

        body = self.web.json(response)
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["code"], "personal_agent_required")
        self.assertEqual(self._stored_target()["agent_id"], "")

    def test_the_repair_action_refuses_a_target_that_is_not_private(self):
        response = self._post(action="update", agent_id="shared-agent")

        self.assertNotIn("200", response.status)
        self.assertEqual(self._stored_target()["agent_id"], "")


if __name__ == "__main__":
    unittest.main()
