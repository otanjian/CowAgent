# encoding:utf-8
"""Startup synthesis and runtime tests for tenant-owned channel instances.

Change ``tenant-owned-message-channels`` keeps ``team.json`` as the roster for
instance-level channels and puts *tenant-owned* instances in ``identity.db``.
At startup the two lists are merged into one uniform ``ChannelInstance`` list
(design D5): the tenant records are read through a thin adapter that imports
the identity layer lazily, so ``channel/`` never hard-depends on ``auth``.

Two properties matter more than the happy path, because this runs before the
Web console is serving:

* a database-mode install keeps coming up even when the identity store or a
  single instance's credential is unusable (that instance is skipped);
* a legacy, non-database install never reads the tenant table at all.
"""

import json
import os
import tempfile
import unittest

from auth.service import IdentityService
from channel.channel_instances import (
    ChannelInstance,
    load_tenant_channel_instances,
    resolve_channel_instances,
)

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

FEISHU_CREDS = {
    "feishu_app_id": "cli_startup",
    "feishu_app_secret": "startup-secret",
    "feishu_bot_name": "Startup Bot",
}


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _StartupFixture(unittest.TestCase):
    """One tenant with an administrator, a bound Agent and two channels."""

    def setUp(self):
        self._previous_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = MASTER_KEY
        self.addCleanup(self._restore_master_key)

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.ta = self.svc.list_tenants()[0]["id"]
        self.svc.bind_agent(tenant_id=self.ta, agent_id="agent-a",
                            private_owner_user_id=None)

    def _restore_master_key(self):
        if self._previous_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._previous_key

    def _new_instance(self, name, **creds):
        bundle = dict(FEISHU_CREDS)
        bundle.update(creds)
        return self.svc.create_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            channel_type="feishu", display_name=name, agent_id="agent-a",
            credentials=bundle, recent_password="Str0ngRootFinal")


# ---------------------------------------------------------------------------
# 6.1 resolve_channel_instances merges tenant instances into the roster list.
# ---------------------------------------------------------------------------

class ResolveMergeTests(_StartupFixture):

    def test_tenant_instances_are_appended_to_the_roster_list(self):
        tenant = [ChannelInstance(instance_id="chan_t", channel_type="feishu",
                                  agent_id="agent-a", credentials=dict(FEISHU_CREDS))]
        merged = resolve_channel_instances(
            {"channel_instances": [
                {"channel_type": "feishu", "instance_id": "chan_r",
                 "agent_id": "agent-a", "feishu_app_id": "cli_r",
                 "feishu_app_secret": "r", "feishu_bot_name": "R"}]},
            tenant_instances=tenant)
        ids = [i.instance_id for i in merged]
        assert ids == ["chan_r", "chan_t"]

    def test_tenant_instances_ride_along_with_explicit_roster_only(self):
        tenant = [ChannelInstance(instance_id="chan_t", channel_type="feishu",
                                  agent_id="agent-a", credentials=dict(FEISHU_CREDS))]
        # channel_type alone no longer synthesizes a web instance.
        merged = resolve_channel_instances(
            {"channel_type": "web"}, tenant_instances=tenant)
        ids = [i.instance_id for i in merged]
        assert ids == ["chan_t"]

    def test_a_duplicate_instance_id_is_not_started_twice(self):
        tenant = [ChannelInstance(instance_id="chan_r", channel_type="feishu",
                                  agent_id="agent-a")]
        merged = resolve_channel_instances(
            {"channel_instances": [
                {"channel_type": "feishu", "instance_id": "chan_r",
                 "agent_id": "agent-a"}]},
            tenant_instances=tenant)
        ids = [i.instance_id for i in merged]
        assert ids.count("chan_r") == 1
        assert len(merged) == 1

    def test_no_tenant_instances_leaves_the_roster_untouched(self):
        settings = {"channel_instances": [
            {"channel_type": "feishu", "instance_id": "chan_r", "agent_id": "agent-a"}]}
        assert [i.instance_id for i in resolve_channel_instances(settings)] == ["chan_r"]
        assert [i.instance_id
                for i in resolve_channel_instances(settings, tenant_instances=[])] == ["chan_r"]


# ---------------------------------------------------------------------------
# 6.3 / 6.5 the adapter reads enabled instances and degrades safely.
# ---------------------------------------------------------------------------

class AdapterTests(_StartupFixture):

    def _patch_database_mode(self, enabled=True):
        import channel.external_identity as ext
        original = ext.is_database_mode
        ext.is_database_mode = lambda: enabled
        self.addCleanup(lambda: setattr(ext, "is_database_mode", original))

    def _patch_service(self, service):
        import auth.service as service_module
        original = service_module.get_identity_service
        service_module.get_identity_service = lambda: service
        self.addCleanup(lambda: setattr(service_module, "get_identity_service", original))

    def test_enabled_instances_are_returned_with_decrypted_credentials(self):
        self._new_instance("Acme Bot")
        self._patch_database_mode()
        self._patch_service(self.svc)

        loaded = load_tenant_channel_instances()
        assert len(loaded) == 1
        instance = loaded[0]
        assert instance.channel_type == "feishu"
        assert instance.agent_id == "agent-a"
        assert instance.legacy is False
        assert instance.credentials["feishu_app_secret"] == "startup-secret"

    def test_a_disabled_instance_is_not_synthesized(self):
        created = self._new_instance("Acme Bot")
        self.svc.set_tenant_channel_instance_active(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"], active=False,
            expected_version=created["version"],
            recent_password="Str0ngRootFinal")
        self._patch_database_mode()
        self._patch_service(self.svc)
        assert load_tenant_channel_instances() == []

    def test_an_unreadable_identity_store_yields_no_instances_and_does_not_raise(self):
        class _Broken:
            def list_enabled_tenant_channel_instances(self):
                raise RuntimeError("identity.db is locked")

        self._patch_database_mode()
        self._patch_service(_Broken())
        assert load_tenant_channel_instances() == []

    def test_one_unusable_credential_skips_only_that_instance(self):
        good = self._new_instance("Good Bot")
        broken = self._new_instance("Broken Bot")

        real = self.svc.channel_instance_credentials

        def flaky(tenant_id, instance_id):
            if instance_id == broken["id"]:
                raise RuntimeError("credential ciphertext is corrupt")
            return real(tenant_id, instance_id)

        self.svc.channel_instance_credentials = flaky
        self._patch_database_mode()
        self._patch_service(self.svc)

        loaded = load_tenant_channel_instances()
        assert [i.instance_id for i in loaded] == [good["id"]]
        assert loaded[0].credentials["feishu_app_secret"] == "startup-secret"


# ---------------------------------------------------------------------------
# 6.6 legacy mode must not touch the tenant table.
# ---------------------------------------------------------------------------

class DatabaseOnlyResolutionTests(_StartupFixture):

    def test_database_mode_loads_tenant_instances(self):
        self._new_instance("Acme Bot")
        import channel.external_identity as ext
        import auth.service as service_module
        original_mode = ext.is_database_mode
        original_service = service_module.get_identity_service
        ext.is_database_mode = lambda: True
        service_module.get_identity_service = lambda: self.svc
        self.addCleanup(lambda: setattr(ext, "is_database_mode", original_mode))
        self.addCleanup(lambda: setattr(service_module, "get_identity_service",
                                       original_service))
        loaded = load_tenant_channel_instances()
        assert len(loaded) == 1

    def test_channel_type_alone_does_not_synthesize_instances(self):
        settings = {"channel_type": "web,dingtalk"}
        assert resolve_channel_instances(settings) == []


# ---------------------------------------------------------------------------
# 6.7 rotation is picked up on the next startup, and the retired value is gone.
# ---------------------------------------------------------------------------

class RotationOnRestartTests(_StartupFixture):

    def _patch_database_mode(self):
        import channel.external_identity as ext
        original = ext.is_database_mode
        ext.is_database_mode = lambda: True
        self.addCleanup(lambda: setattr(ext, "is_database_mode", original))

    def test_a_rotated_secret_is_the_one_a_restart_starts_with(self):
        created = self._new_instance("Acme Bot")
        self.svc.update_tenant_channel_instance(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            instance_id=created["id"],
            expected_version=created["version"],
            credentials=dict(FEISHU_CREDS, feishu_app_secret="rotated-secret"),
            recent_password="Str0ngRootFinal")

        import auth.service as service_module
        original = service_module.get_identity_service
        service_module.get_identity_service = lambda: self.svc
        self.addCleanup(lambda: setattr(service_module, "get_identity_service", original))
        self._patch_database_mode()

        loaded = load_tenant_channel_instances()
        assert len(loaded) == 1
        assert loaded[0].credentials["feishu_app_secret"] == "rotated-secret"

        # No read path may still hand back the retired value.
        resolved = self.svc.channel_instance_credentials(self.ta, created["id"])
        assert resolved["feishu_app_secret"] == "rotated-secret"
        # ...including the actor-checked use point, which resolves the current
        # version and never the retained audit history.
        at_use_point = self.svc.resolve_credential(
            actor_user_id=self.root["id"], tenant_id=self.ta,
            name=f"channel:{created['id']}", resource_kind="channel",
            resource_id=created["id"])
        resolved_bundle = json.loads(at_use_point)
        assert resolved_bundle["feishu_app_secret"] == "rotated-secret"
        assert "startup-secret" not in at_use_point
        listed = self.svc.list_tenant_channel_instances(
            actor_user_id=self.root["id"], tenant_id=self.ta)
        assert listed["total"] == 1
        assert "startup-secret" not in json.dumps(listed)


if __name__ == "__main__":
    unittest.main()
