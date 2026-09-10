# encoding:utf-8
"""Tenant-configurable channel type gate (change tenant-owned-message-channels).

The WeCom App (``wechatcom_app``) slice was **deferred during implementation**:
its inbound path is a fixed-port, fixed-path webhook whose handler resolves the
channel through a process-wide singleton, so per-tenant instances would
silently collapse onto one object with one global credential set. These tests
pin the gate that keeps it out of the tenant-ownable set, and pin the concrete
failure the deferral avoids. See ``design.md`` §6.
"""

import unittest


class ChannelTypeConstantsTests(unittest.TestCase):
    def test_wechatcom_app_is_not_declared_multi_instance_ready(self):
        from channel.channel_instances import MULTI_INSTANCE_READY
        self.assertNotIn("wechatcom_app", MULTI_INSTANCE_READY)

    def test_wechatcom_app_has_no_declared_credential_keys(self):
        from channel.channel_instances import CREDENTIAL_KEYS
        self.assertNotIn("wechatcom_app", CREDENTIAL_KEYS)

    def test_the_delivered_channel_type_stays_reachable(self):
        """Feishu is the slice that ships, so the gate must not exclude it."""
        from channel.channel_instances import CREDENTIAL_KEYS, MULTI_INSTANCE_READY
        self.assertIn("feishu", MULTI_INSTANCE_READY)
        self.assertIn("feishu", CREDENTIAL_KEYS)


class FactorySingletonCollisionTests(unittest.TestCase):
    """Pins the failure the deferral avoids: no per-instance isolation.

    If ``wechatcom_app`` were added to ``MULTI_INSTANCE_READY``, the factory
    would hand out what looks like several instances while the singleton
    actually pools them. Two tenants would share one credential set and one
    ``instance_id`` — a cross-tenant leak that is worse than not shipping.

    The real channel class is never constructed: its client starts a
    background thread that calls the live WeCom API, which a unit test must not
    do. The channel classes are swapped for singleton-decorated stubs so the
    factory's *routing decision* is what is exercised.
    """

    def setUp(self):
        import channel.channel_factory as factory
        from common.singleton import singleton
        import channel.feishu.feishu_channel as feishu_module
        import channel.wechatcom.wechatcomapp_channel as wecom_module

        self._factory = factory
        calls = []

        def _make_stub():
            @singleton
            class _Stub:
                def __init__(self):
                    self.applied = {}

                def apply_instance(self, **kwargs):
                    self.applied.update(kwargs)

            def _new_instance(*args, **kwargs):
                calls.append("bypass")
                return _Stub.__wrapped__(*args, **kwargs)

            _Stub.new_instance = _new_instance
            return _Stub

        self.wecom_stub = _make_stub()
        self.feishu_stub = _make_stub()
        self._patched = {
            wecom_module: "WechatComAppChannel",
            feishu_module: "FeiShuChanel",
        }
        self._originals = {
            module: getattr(module, name) for module, name in self._patched.items()
        }
        self._calls = calls
        for module, name in self._patched.items():
            setattr(module, name, self.wecom_stub if name.startswith("Wechat")
                    else self.feishu_stub)
        self.addCleanup(self._restore)

    def _restore(self):
        for module, name in self._patched.items():
            setattr(module, name, self._originals[module])

    def test_wechatcom_app_has_no_per_instance_bypass(self):
        """The factory cannot build a distinct wechatcom_app instance."""
        self._factory.create_channel("wechatcom_app", instance_id="tenant-a")
        self.assertEqual(self._calls, [])
        # Contrast: a multi-instance-ready type does take the bypass, so the
        # assertion above is about wechatcom_app specifically, not the probe.
        self._factory.create_channel("feishu", instance_id="tenant-a")
        self.assertEqual(self._calls, ["bypass"])

    def test_two_tenants_collapse_onto_one_shared_object(self):
        first = self._factory.create_channel(
            "wechatcom_app", instance_id="tenant-a",
            credentials={"wechatcom_corp_id": "corp-tenant-a"})
        second = self._factory.create_channel(
            "wechatcom_app", instance_id="tenant-b",
            credentials={"wechatcom_corp_id": "corp-tenant-b"})
        # The same object backs both "instances"...
        self.assertIs(first, second)
        # ...and the second call overwrites the first one's routing identity,
        # so tenant-a's traffic would be routed as tenant-b's.
        self.assertEqual(second.applied["instance_id"], "tenant-b")


if __name__ == "__main__":
    unittest.main()
