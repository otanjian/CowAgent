# encoding:utf-8
"""Mutation check for the tenant-owned channel isolation guards (10.3).

A test that passes is only meaningful if it would FAIL when the guard it claims
to protect is removed. This harness applies a small, surgical mutation to each
isolation guard, runs the tests that claim to protect it, and asserts that they
fail. It then restores the source exactly.

Run directly (``python -m tests.test_tenant_channel_mutations``) or under
unittest. It writes no files and leaves the tree as it found it — a failure
restores the source before raising.
"""

import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")


def _run(test_ids):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "-q", *test_ids],
        cwd=ROOT, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


class MutationTests(unittest.TestCase):
    """Each case: a file plus one or more (old, new) edits, and the tests that
    must flip to failing once it is applied."""

    MUTATIONS = [
        # 1. Drop the tenant predicate from the Agent check: a tenant could then
        #    bind another tenant's Agent and route inbound traffic into it.
        (
            "auth/service.py",
            [('if not binding or binding["tenant_id"] != tenant_id:',
              'if not binding:')],
            ["tests/test_tenant_channel_instances_service.py::"
             "CreateInstanceTests::test_agent_from_another_tenant_is_rejected",
             "tests/test_tenant_channel_isolation_acceptance.py::"
             "CrossTenantIsolationAcceptance::"
             "test_binding_another_tenants_agent_is_refused"],
        ),
        # 2. Drop the tenant predicate from the list query: one tenant's instances
        #    would appear in another tenant's console.
        (
            "auth/service.py",
            [('            " WHERE tenant_id=? AND scope=\'tenant\' ORDER BY display_name",\n'
              '            (tenant_id,))',
              '            " WHERE scope=\'tenant\' ORDER BY display_name",\n'
              '            ())')],
            ["tests/test_tenant_channel_instances_service.py::"
             "ListInstanceTests::test_list_never_returns_another_tenants_instances",
             "tests/test_tenant_channel_isolation_acceptance.py::"
             "CrossTenantIsolationAcceptance::"
             "test_b_tenant_cannot_see_a_tenants_instances"],
        ),
        # 3. The realistic regression for the deferred 企微自建应用: someone marks
        #    the type multi-instance ready and declares its credential bundle.
        #    Its fixed-port singleton webhook cannot be tenant-scoped, so it must
        #    stay out of the tenant-offerable set (and off the console form).
        #
        #    The credential-bundle edit anchors on the 微信 entry, which only the
        #    full CREDENTIAL_KEYS list declares: REQUIRED_CREDENTIAL_KEYS repeats
        #    several entries verbatim, so the tail of the dict is not unique.
        (
            "channel/channel_instances.py",
            [("MULTI_INSTANCE_READY = frozenset({\n    const.FEISHU,",
              "MULTI_INSTANCE_READY = frozenset({\n    'wechatcom_app',\n    const.FEISHU,"),
             ('    const.WEIXIN: (\n        "weixin_token",\n        "weixin_base_url",\n    ),',
              '    const.WEIXIN: (\n        "weixin_token",\n        "weixin_base_url",\n    ),\n'
              '    "wechatcom_app": (\n'
              '        "wechatcom_corp_id",\n'
              '        "wechatcom_agent_id",\n'
              '        "wechatcom_secret",\n'
              '    ),')],
            ["tests/test_tenant_channel_type_gate.py::ChannelTypeConstantsTests::"
             "test_wechatcom_app_is_not_declared_multi_instance_ready",
             "tests/test_tenant_channel_http.py::GetTenantChannelTypesTests::"
             "test_the_form_contract_offers_feishu_and_withholds_wechatcom_app"],
        ),
        # 4. Drop the scope predicate from the list query: a member's personal
        #    instance would surface in the tenant's administration list, which is
        #    not the tenant's to administer.
        (
            "auth/service.py",
            [('            " WHERE tenant_id=? AND scope=\'tenant\' ORDER BY display_name",\n'
              '            (tenant_id,))',
              '            " WHERE tenant_id=? ORDER BY display_name",\n'
              '            (tenant_id,))')],
            ["tests/test_tenant_channel_instances_service.py::"
             "InstanceScopeTests::"
             "test_the_public_list_does_not_expose_personal_instances"],
        ),
    ]

    def _assert_mutation_caught(self, relpath, edits, test_ids):
        target = os.path.join(ROOT, relpath)
        backup_dir = tempfile.mkdtemp()
        backup = os.path.join(backup_dir, os.path.basename(relpath))
        shutil.copy2(target, backup)
        try:
            with open(target, encoding="utf-8") as fh:
                src = fh.read()
            mutated = src
            for old, new in edits:
                self.assertEqual(mutated.count(old), 1,
                                 f"mutation anchor not unique in {relpath}: {old!r}")
                mutated = mutated.replace(old, new)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(mutated)

            code, output = _run(test_ids)
            self.assertNotEqual(
                code, 0,
                f"the mutated guard survived — these tests did not catch it:\n{output}")
        finally:
            shutil.copy2(backup, target)
            shutil.rmtree(backup_dir, ignore_errors=True)

        # The restore must be exact, or the next run would start from a mutant.
        with open(target, encoding="utf-8") as fh:
            restored = fh.read()
        self.assertEqual(restored, src, f"{relpath} was not restored exactly")

    def test_each_isolation_guard_is_pinned_by_a_failing_test(self):
        if not os.path.exists(PYTHON):
            self.skipTest("no .venv interpreter")
        for relpath, edits, test_ids in self.MUTATIONS:
            with self.subTest(mutation=relpath):
                self._assert_mutation_caught(relpath, edits, test_ids)


if __name__ == "__main__":
    unittest.main()
