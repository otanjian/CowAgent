# encoding:utf-8
"""The operational half of the owner-wording fix.

A copy created before the owner-facing templates existed keeps describing its
owner as the role the *source* template was written for, and nothing re-runs
provisioning for an existing member. ``personalize-personal-agents`` is how an
operator corrects those copies: previewable, scoped, and safe to re-run.

The roster and identity store are the same throwaway ones
``test_personal_assistant_owner_fields`` drives, so these tests exercise the
command against real services rather than doubles -- only the database path the
CLI resolves is redirected, which is what ``share-default-agents`` does too.
"""

import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from cli.commands.management import management
from tests.test_personal_assistant_owner_fields import (
    BLANK_TEMPLATES,
    SOURCE_DESCRIPTION,
    SOURCE_PERSONA,
    _OwnerFieldsBase,
    _templates,
)
from tests.test_user_personal_agent_provisioning import SOURCE_ID

PRE_FIX_DESCRIPTION = (
    "管理员专属智能办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")


class PersonalizePersonalAgentsCommandTests(_OwnerFieldsBase):
    def setUp(self):
        super().setUp()
        self.runner = CliRunner()
        self._patch_db = patch("cli.commands.management._identity_db_path",
                               lambda: os.path.join(self.tmp, "identity.db"))
        self._patch_db.start()
        self.addCleanup(self._patch_db.stop)

    def _invoke(self, *args):
        return self.runner.invoke(management, list(args))

    def test_dry_run_reports_without_writing(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        result = self._invoke("personalize-personal-agents", "--dry-run")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(agent_id, result.output)
        self.assertIn("Rock", result.output)
        # The point of --dry-run: the roster is untouched.
        self.assertEqual(self._profile(agent_id)["description"], PRE_FIX_DESCRIPTION)

    def test_apply_then_rerun_is_idempotent(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        first = self._invoke("personalize-personal-agents")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("Updated 1", first.output)
        self.assertEqual(
            self._profile(agent_id)["description"],
            "Rock的专属办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")
        self.assertEqual(
            self._profile(agent_id)["persona_summary"],
            "Rock的私人智能办公助理：结论先行，能代办就直接办成；严守隐私边界。")

        second = self._invoke("personalize-personal-agents")
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("Updated 0", second.output)

    def test_nothing_to_do_is_reported_cleanly(self):
        self._add_member("RC001", "Rock")

        result = self._invoke("personalize-personal-agents")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Updated 0", result.output)

    def test_the_source_template_keeps_its_wording(self):
        self._pre_fix_copy("RC001", "Rock")

        result = self._invoke("personalize-personal-agents")

        self.assertEqual(result.exit_code, 0, result.output)
        source = self._profile(SOURCE_ID)
        self.assertEqual(source["description"], SOURCE_DESCRIPTION)
        self.assertEqual(source["persona_summary"], SOURCE_PERSONA)

    def test_dry_run_unknown_tenant_fails_cleanly(self):
        result = self._invoke("personalize-personal-agents", "--dry-run",
                              "--tenant-code", "nope")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("nope", result.output)

    def test_a_tenant_scoped_run_leaves_other_tenants_alone(self):
        with _templates(**BLANK_TEMPLATES):
            here = self._add_member("RC001", "Rock")
            other_tenant = self._new_tenant("globex")
            # A binding belongs to exactly one tenant, so another tenant needs
            # its own 智能办公助理 to clone from.
            self._add_shared_agent("assistant-globex", name="智能办公助理",
                                   tenant_id=other_tenant["id"])
            there = self.svc.create_member(
                actor_user_id=self.root_id, tenant_id=other_tenant["id"],
                operation="create-new", username="GB001", display_name="Grace",
                temporary_password="TempPass123!", roles=[])
        other_agent = there["personal_agent"]["agent_id"]

        result = self._invoke("personalize-personal-agents", "--tenant-code", "acme")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(here["personal_agent"]["agent_id"], result.output)
        self.assertEqual(
            self._profile(other_agent)["description"],
            "管理员专属智能办公助理：人设与长期记忆跟随 Grace 本人，不进租户共享目录")


if __name__ == "__main__":
    unittest.main()
