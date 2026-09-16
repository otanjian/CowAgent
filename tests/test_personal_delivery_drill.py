# encoding:utf-8
"""Stage 9.4 — delivery drill: upgrade, compensation, switches, recovery.

The change ships a member-facing surface plus new storage, so "it works on a
fresh database" is not the delivery question. The questions a rollout actually
asks are:

    D1  is the upgrade idempotent — a restart applies nothing and loses nothing?
    D2  does a crash *during* the upgrade leave a retryable database (no partial
        schema, no recorded version for the failed step)?
    D3  does an interrupted personal-channel create leave nothing behind, and
        can the retry succeed as if nothing had happened?
    D4  does an interrupted private-Agent create leave no half-bound object?
    D5  can a deployment withdraw the switches and keep the accepted catalogue,
        the owner checks and a working console?
    D6  does withdrawing the execution switch *stop* personal connections rather
        than leave a live route serving?
    D7  can a consistent backup be restored without losing new data, and without
        auto-starting personal instances or resurrecting an administrator's
        private read?
    D8  is the retired administrator bypass actually gone, so no "compatible"
        older process can rely on it?

Each test below performs the drill; the evidence file records the output.
"""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config
from auth.service import IdentityService
from auth.store import IdentityStore
from channel.channel_instances import (
    apply_tenant_instance_runtime, load_tenant_channel_instances,
    personal_runtime_enabled)

from tests.test_personal_console_multi_tenant_authorization import (
    MEMBER_PASSWORD, _TwoTenantFixture)

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


class _DrillFixture(_TwoTenantFixture):
    """Two tenants, a member-owned instance, and the startup loader wired up."""

    #: The private Agent tenant A's member drills with. It is in the roster the
    #: acceptance fixture installs, which is the registry half of the personal
    #: target predicate; the binding half is made where the instance is created.
    ALICE_TARGET = "target-alice"

    def setUp(self):
        super().setUp()
        self.alice_id, self.alice_token = self._plain_member(
            self.tenant_id, self.root_user_id, "alice")
        self.bob_id, self.bob_token = self._plain_member(
            self.tenant_id, self.root_user_id, "bob")
        self.carol_id, self.carol_token = self._plain_member(
            self.beta_id, self.beta_admin_id, "carol")
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_key)

    def _restore_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    # -- helpers ----------------------------------------------------------

    def _personal_instance(self, owner_id=None, app_id="cli_drill"):
        owner = owner_id or self.alice_id
        # A personal instance has to name a target its own owner holds privately
        # and that the roster has enabled; the parent fixture's helper binds the
        # identity half, and it is idempotent so the retries below do not consume
        # a second private-Agent slot.
        agent_id = self._private_agent(owner, self.tenant_id, self.ALICE_TARGET)
        return self.service.create_personal_channel_instance(
            actor_user_id=owner, tenant_id=self.tenant_id,
            channel_type="feishu", display_name="drill",
            agent_id=agent_id,
            credentials={"feishu_app_id": app_id,
                         "feishu_app_secret": "drill-secret",
                         "feishu_token": "drill-token",
                         "feishu_bot_name": "drill-bot"},
            recent_password=MEMBER_PASSWORD)

    def _switches(self, **over):
        settings = dict(self.settings)
        settings.update(over)
        patcher = patch.object(config, "conf", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        return settings

    def _load_startup(self, *, runtime, accepted=()):
        """Load exactly the way boot does, with the drill's own gates."""
        import auth.service as service_module
        import channel.external_identity as ext

        self._switches(personal_channel_runtime=runtime)
        original_mode = ext.is_database_mode
        ext.is_database_mode = lambda: True
        self.addCleanup(lambda: setattr(ext, "is_database_mode", original_mode))
        original_service = service_module.get_identity_service
        service_module.get_identity_service = lambda: self.service
        self.addCleanup(
            lambda: setattr(service_module, "get_identity_service", original_service))
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset(accepted)):
            with patch("channel.channel_instances.PUBLIC_PERSONAL_INGRESS_TYPES",
                       frozenset(accepted)):
                return load_tenant_channel_instances()

    def _count(self, table, where="", params=()):
        sql = "SELECT COUNT(*) c FROM " + table
        if where:
            sql += " WHERE " + where
        return self.service._store.execute(sql, params)[0]["c"]

    def _versions(self, path=None):
        store = IdentityStore(path or self.db_path)
        return [r["version"] for r in
                store.execute("SELECT version FROM schema_migrations ORDER BY version")]


class UpgradeIdempotenceDrill(_DrillFixture):
    """D1/D2 — the upgrade itself."""

    def test_d1_reopening_the_database_applies_nothing(self):
        from auth.store import migration_versions

        before = self._versions()
        # A restart in production opens the same file again; nothing may move.
        after = self._versions()
        self.assertEqual(before, after)
        self.assertEqual(len(set(before)), len(before), "no version recorded twice")
        self.assertEqual(before, migration_versions(),
                         "every migration in the code is recorded exactly once")

        # The personal columns are really present, so "no work to do" is not
        # hiding a database that never got the change.
        columns = {r["name"] for r in self.service._store.execute(
            "PRAGMA table_info(tenant_channel_instances)")}
        self.assertIn("scope", columns)
        self.assertIn("owner_user_id", columns)
        self.assertIn("governance_disabled_at", columns)

    def test_d2_an_interrupted_upgrade_rolls_back_and_retries(self):
        import auth.store as store_module

        db = os.path.join(self.root_dir, "drill-interrupted.db")
        IdentityStore(db)  # a second, fully upgraded database to drill on
        real = list(store_module._migrations)
        failed_version = len(real) + 1

        def broken(con):
            con.execute("CREATE TABLE drill_partial(x INTEGER)")
            raise RuntimeError("crash during the upgrade")

        with patch.object(store_module, "_migrations", real + [broken]):
            with self.assertRaises(RuntimeError):
                IdentityStore(db)

        versions = self._versions(db)
        self.assertNotIn(failed_version, versions,
                         "an interrupted version must not be recorded")
        names = {r["name"] for r in IdentityStore(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("drill_partial", names,
                         "the failed step's partial schema must be rolled back")

        # The retry is an ordinary restart: nothing to undo, nothing stranded.
        store = IdentityStore(db)
        self.assertEqual(self._versions(db), versions)
        self.assertEqual(len(versions), len(real))


class InterruptedWriteDrill(_DrillFixture):
    """D3/D4 — compensation when a write is interrupted mid-transaction."""

    def test_d3_an_interrupted_personal_channel_create_leaves_nothing(self):
        before_channels = self._count("tenant_channel_instances")
        before_credentials = self._count("credentials")

        # (a) Unreadable encryption material is the realistic "the write cannot
        # even start" shape (key missing/rotated away); it must leave no row.
        os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        with self.assertRaises(Exception):
            self._personal_instance(app_id="cli_drill_broken")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.assertEqual(self._count("tenant_channel_instances"), before_channels)
        self.assertEqual(self._count("credentials"), before_credentials)

        # (b) The harder case: the row and the encrypted bundle are already
        # written and the *audit* write fails. The whole write is one
        # transaction, so the half-created instance must roll back too — a
        # member must never be left owning a channel with no audit trail, and a
        # retry must not hit "display name exists".
        with patch.object(IdentityService, "_audit_in_tx",
                          side_effect=RuntimeError("audit store unavailable")):
            with self.assertRaises(Exception):
                self._personal_instance(app_id="cli_drill_broken_audit")

        self.assertEqual(self._count("tenant_channel_instances"), before_channels)
        self.assertEqual(self._count(
            "tenant_channel_instances", "scope='user'"), 0)
        self.assertEqual(self._count("credentials"), before_credentials)

        # The retry behaves as a first attempt: exactly one row, one credential.
        created = self._personal_instance(app_id="cli_drill_retry")
        self.assertTrue(created["id"])
        self.assertEqual(self._count(
            "tenant_channel_instances", "scope='user'"), 1)
        self.assertEqual(self._count("credentials"), before_credentials + 1)

    def test_d4_an_interrupted_private_agent_create_leaves_no_half_bound_object(self):
        from agent.private_agent import PrivateAgentService

        class _CrashingAdmin:
            def snapshot(self):
                return {"agents": []}

            def clone_agent(self, *a, **k):
                raise RuntimeError("workspace clone failed")

            def create_agent(self, *a, **k):
                raise RuntimeError("workspace create failed")

        self.service.bind_agent(tenant_id=self.tenant_id, agent_id="template-agent")
        self.service.set_member_default_agent(
            tenant_id=self.tenant_id, user_id=self.alice_id,
            agent_id="template-agent")

        service = PrivateAgentService(self.service, admin_service=_CrashingAdmin())
        with self.assertRaises(Exception):
            service.create_private_agent(
                user_id=self.alice_id, tenant_id=self.tenant_id, name="mine")

        self.assertEqual(
            [b for b in self.service.agents_for_tenant(self.tenant_id)
             if b.get("private_owner_user_id") == self.alice_id], [],
            "a failed clone must not leave a binding behind")
        pointer = [m for m in self.service.list_members(self.tenant_id)["items"]
                   if m["username"] == "alice"][0].get("default_agent_id")
        self.assertNotEqual(pointer, "mine")


class SwitchWithdrawalDrill(_DrillFixture):
    """D5/D6 — what a deployment sees after turning a slice off and restarting."""

    def test_d5_withdrawn_switches_keep_the_catalogue_and_the_owner_checks(self):
        instance = self._personal_instance()
        self._switches(member_personal_console=False,
                       user_private_agent_management=False,
                       personal_memory_write=False,
                       personal_channel_onboarding=False)

        pages = self.service.context_for_tenant(
            self.alice_token, self.tenant_id)["console_pages"]
        for pid in ("personal.agents", "personal.channels", "personal.memory",
                    "personal.tools", "personal.skills"):
            self.assertNotIn(pid, pages, pid)
        # The withdrawal is observable where the member's surface now lives: the
        # carrier page keeps its switch block and stops offering the create its
        # write path refuses (task 8.8 — the retired ids are not issued at all,
        # so "withdrawn" can no longer be read off them).
        self.assertFalse(
            pages["admin.channels"]["switches"]["member_personal_console"])
        self.assertFalse(pages["admin.channels"]["actions"]["create"])
        self.assertTrue(pages["admin.channels"]["available"])

        # The catalogue is still stated (it is not a private object), so an
        # operator can see what the deployment would offer if re-enabled.
        catalogue = self.service.personal_channel_types()
        self.assertTrue(catalogue, "the accepted catalogue must not disappear")
        self.assertTrue(all("channel_type" in row for row in catalogue))

        # Owner checks are unaffected by the withdrawal: another member and a
        # platform administrator still cannot read the member's row.
        self.assertEqual(self.service.list_personal_channel_instances(
            actor_user_id=self.bob_id, tenant_id=self.tenant_id)["items"], [])
        self.assertEqual(self.service.list_personal_channel_instances(
            actor_user_id=self.root_user_id, tenant_id=self.tenant_id)["items"],
            [])
        self.assertEqual(self.service.list_personal_channel_instances(
            actor_user_id=self.alice_id, tenant_id=self.tenant_id)["items"][0]["id"],
            instance["id"], "the owner keeps their own row")

    def test_d6_withdrawing_execution_stops_the_connection(self):
        instance = self._personal_instance()

        # First: the deployment that *has* recorded a real acceptance (this is
        # the only state in which a personal connection may exist at all).
        self._switches(personal_channel_runtime=True)
        with patch("channel.channel_instances.PERSONAL_RUNTIME_ACCEPTED_TYPES",
                   frozenset({"feishu"})):
            self.assertTrue(personal_runtime_enabled("feishu"))
            started = self._load_startup(runtime=True, accepted=("feishu",))
            self.assertIn(instance["id"], [i.instance_id for i in started])

        # Then: the switch is withdrawn. Serving must stop, and the stop has to
        # be observable at the runtime seam — not only in the console.
        self._switches(personal_channel_runtime=False)
        self.assertFalse(personal_runtime_enabled("feishu"))
        outcome = apply_tenant_instance_runtime(instance["id"])
        self.assertFalse(outcome["applied"])
        self.assertIn("personal runtime is not enabled", outcome["error"])
        self.assertTrue(outcome.get("pending"),
                        "reported as saved-but-not-connected, not as a failure")
        stopped = self._load_startup(runtime=False, accepted={"feishu"})
        self.assertEqual([i.instance_id for i in stopped
                          if i.instance_id == instance["id"]], [],
                         "a withdrawal must not be undone by a recorded acceptance")


class ConsistentRecoveryDrill(_DrillFixture):
    """D7/D8 — restore a consistent backup, and prove the old bypass is gone."""

    def test_d7_a_consistent_backup_restores_data_without_starting_personal_instances(self):
        self._personal_instance(app_id="cli_drill_backup")
        self.service.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id="mem-alice-backup",
            user_id=self.alice_id, origin="user_created",
            actor_user_id=self.alice_id)
        versions = self._versions()

        # "按一致备份恢复": a file-level copy of the upgraded database.
        restored_path = os.path.join(self.root_dir, "identity-restored.db")
        shutil.copyfile(self.db_path, restored_path)

        restored = IdentityService(restored_path)
        self.assertEqual(self._versions(restored_path), versions,
                         "the restore must need no further migration")
        self.assertEqual([r["id"] for r in restored._store.execute(
            "SELECT id FROM tenant_channel_instances WHERE scope='user'")].__len__(),
            1, "new data survives the restore")
        self.assertIsNotNone(restored.get_agent_binding("mem-alice-backup"))

        # The restore must not become a personal-connection start: the runtime
        # switch is off, so the restored rows are data, not routes.
        self.assertFalse(personal_runtime_enabled("feishu"))
        original_service = self.service
        try:
            self.service = restored
            loaded = self._load_startup(runtime=False, accepted=("feishu",))
            self.assertEqual([i.instance_id for i in loaded
                              if i.instance_id in {r["id"] for r in
                              restored._store.execute(
                                  "SELECT id FROM tenant_channel_instances"
                                  " WHERE scope='user'")}], [])
            # Cross-tenant and administrator refusal survive the restore.
            self.assertFalse(restored.check_resource_action(
                self.carol_id, self.beta_id, "agent", "mem-alice-backup", "read"))
            self.assertFalse(restored.check_resource_action(
                self.root_user_id, self.tenant_id, "agent",
                "mem-alice-backup", "read"))
            self.assertTrue(restored.check_resource_action(
                self.alice_id, self.tenant_id, "agent",
                "mem-alice-backup", "read"))
        finally:
            self.service = original_service

    def test_d8_the_retired_administrator_private_read_is_still_forbidden(self):
        from channel.web.web_channel import _db_path_owner_forbidden

        self.service.bind_private_agent_with_quota(
            tenant_id=self.tenant_id, agent_id="mem-alice-bypass",
            user_id=self.alice_id, origin="user_created",
            actor_user_id=self.alice_id)

        owner = SimpleNamespace(tenant_id=self.tenant_id, user_id=self.alice_id)
        peer = SimpleNamespace(tenant_id=self.tenant_id, user_id=self.bob_id)
        # The exact shape of the retired bypass: a tenant administrator asking
        # for a member's private assets. The predicate is role-agnostic, so even
        # a context that *claims* the tenant-admin flag is refused.
        tenant_admin = SimpleNamespace(tenant_id=self.tenant_id,
                                       user_id=self.bob_id,
                                       is_tenant_admin=True)
        platform_admin = SimpleNamespace(tenant_id=self.tenant_id,
                                         user_id=self.root_user_id,
                                         is_platform_admin=True)

        self.assertFalse(_db_path_owner_forbidden(owner, "mem-alice-bypass"))
        for ctx, label in ((peer, "peer"), (tenant_admin, "tenant admin"),
                           (platform_admin, "platform admin")):
            self.assertTrue(_db_path_owner_forbidden(ctx, "mem-alice-bypass"), label)

        # And the same three answers at the authorization seam the console and
        # the file paths share, so the predicate is not the only guard.
        for user_id, label in ((self.bob_id, "peer"),
                               (self.root_user_id, "platform admin")):
            self.assertFalse(self.service.check_resource_action(
                user_id, self.tenant_id, "agent", "mem-alice-bypass", "read"),
                label)


if __name__ == "__main__":
    unittest.main()
