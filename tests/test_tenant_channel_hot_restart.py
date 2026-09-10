# encoding:utf-8
"""Saving a tenant channel takes effect now, not after a maintenance window.

``scan-onboarding-and-inbound-anchor`` group 4: creating or editing an enabled
tenant channel must bring it up on the new credentials immediately. When that
cannot happen — bad credentials, a network failure, a manager that is not
running in this process — the old run must NOT quietly keep serving the previous
credentials, and the operator must get a diagnosable reason plus an honest
"not applied yet" status. The instance row and its credential version history
always survive.
"""

import json
import os
import tempfile
import threading
import time
import unittest

from channel import channel_instances as ci

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


class _FakeManager:
    """Records the runtime calls the hot-restart path makes."""

    def __init__(self, fail_restart=None):
        self.restarted = []
        self.stopped = []
        self.fail_restart = fail_restart
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def restart(self, inst):
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            time.sleep(0.05)
            if self.fail_restart:
                raise self.fail_restart
            self.restarted.append(inst)
        finally:
            with self._lock:
                self.concurrent -= 1

    def stop(self, instance_id):
        self.stopped.append(instance_id)


class HotRestartTests(unittest.TestCase):
    MASTER_KEY = MASTER_KEY

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_master_key)

        from auth.service import IdentityService
        self.svc = IdentityService(os.path.join(tempfile.mkdtemp(), "identity.db"))
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.tenant = self.svc.list_tenants()[0]["id"]

        # Monkeypatch only where the module looks the service up, so the real
        # service (real store, real encryption) is what runs underneath.
        import auth.service
        original = auth.service.get_identity_service
        auth.service.get_identity_service = lambda: self.svc
        self.addCleanup(setattr, auth.service, "get_identity_service", original)

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _create(self, *, active=True):
        created = self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            channel_type="feishu", display_name="Support",
            agent_id="", credentials={
                "feishu_app_id": "cli_tenant_a",
                "feishu_app_secret": "s3cr3t-app-secret",
            },
            recent_password="Str0ngRootFinal",
        )
        iid = created["id"]
        if not active:
            self.svc.set_tenant_channel_instance_active(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                instance_id=iid, active=False,
                expected_version=created["version"],
                recent_password="Str0ngRootFinal")
        return iid

    def _use_manager(self, mgr):
        original = ci._runtime_manager
        ci._runtime_manager = lambda: mgr
        self.addCleanup(setattr, ci, "_runtime_manager", original)

    # --- 4.1 restart on create / edit -----------------------------------

    def test_creating_an_enabled_instance_starts_it_now(self):
        iid = self._create()
        mgr = _FakeManager()
        self._use_manager(mgr)

        outcome = ci.apply_tenant_instance_runtime(iid)

        self.assertTrue(outcome["applied"], outcome)
        self.assertEqual(len(mgr.restarted), 1)
        inst = mgr.restarted[0]
        self.assertEqual(inst.instance_id, iid)
        self.assertEqual(inst.channel_type, "feishu")
        self.assertEqual(inst.tenant_id, self.tenant)
        # The decrypted bundle — not the masked projection — is what runs.
        self.assertEqual(inst.credentials["feishu_app_id"], "cli_tenant_a")
        self.assertEqual(inst.credentials["feishu_app_secret"], "s3cr3t-app-secret")

    def test_a_decrypt_failure_does_not_leave_the_old_run_serving(self):
        iid = self._create()
        # Revoke the credential: the instance row stays, decryption no longer
        # resolves, and the running instance must not keep using it.
        self.svc.revoke_credential(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            name=f"channel:{iid}")
        mgr = _FakeManager()
        self._use_manager(mgr)

        outcome = ci.apply_tenant_instance_runtime(iid)

        self.assertFalse(outcome["applied"])
        self.assertTrue(outcome["error"], "a failure must carry a reason")
        self.assertIn(iid, mgr.stopped, "the old credential kept serving")
        self.assertEqual(mgr.restarted, [])

    # --- 4.3 per-instance serialization ---------------------------------

    def test_concurrent_applies_of_one_instance_do_not_interleave(self):
        iid = self._create()
        mgr = _FakeManager()
        self._use_manager(mgr)

        threads = [threading.Thread(target=ci.apply_tenant_instance_runtime, args=(iid,))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        self.assertEqual(mgr.max_concurrent, 1,
                         "the same instance was started/stopped concurrently")

    # --- 4.4 start failure is diagnosable -------------------------------

    def test_a_start_failure_keeps_the_row_and_reports_why(self):
        iid = self._create()
        before = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.tenant)["items"]
        mgr = _FakeManager(fail_restart=RuntimeError("invalid app secret"))
        self._use_manager(mgr)

        outcome = ci.apply_tenant_instance_runtime(iid)

        self.assertFalse(outcome["applied"])
        self.assertIn("invalid app secret", outcome["error"])
        self.assertIn(iid, mgr.stopped, "stopped before starting, per the requirement")
        # The instance record is untouched, so the operator can fix and retry.
        after = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.tenant)["items"]
        self.assertEqual(
            [i["id"] for i in after], [i["id"] for i in before])
        self.assertEqual(after[0]["version"], before[0]["version"])

    def test_the_last_outcome_is_readable_for_the_listing(self):
        iid = self._create()
        self._use_manager(_FakeManager(fail_restart=RuntimeError("boom")))
        ci.apply_tenant_instance_runtime(iid)
        state = ci.instance_runtime_state(iid)
        self.assertFalse(state["applied"])
        self.assertIn("boom", state["error"])

        self._use_manager(_FakeManager())
        ci.apply_tenant_instance_runtime(iid)
        self.assertTrue(ci.instance_runtime_state(iid)["applied"])

    def test_no_runtime_in_this_process_reports_pending_not_applied(self):
        iid = self._create()
        self._use_manager(None)

        outcome = ci.apply_tenant_instance_runtime(iid)

        self.assertFalse(outcome["applied"])
        self.assertTrue(outcome["pending"], "must say the effect is still pending")
        self.assertEqual(outcome["error"], "")

    # --- 4.6 disable is a rollback path ---------------------------------

    def test_disabling_stops_the_instance_and_keeps_its_record(self):
        iid = self._create()
        inst = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.tenant)["items"][0]
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            instance_id=iid, active=False, expected_version=inst["version"],
            recent_password="Str0ngRootFinal")
        mgr = _FakeManager()
        self._use_manager(mgr)

        outcome = ci.apply_tenant_instance_runtime(iid)

        self.assertTrue(outcome["applied"], outcome)
        self.assertIn(iid, mgr.stopped)
        self.assertEqual(mgr.restarted, [])
        # The record (and therefore its credential version history) survives.
        rows = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.tenant)["items"]
        self.assertEqual([r["id"] for r in rows], [iid])
        self.assertFalse(rows[0]["active"])


if __name__ == "__main__":
    unittest.main()
