# encoding:utf-8
"""A personal assistant introduces itself as *its own owner's*, not the template's.

The clone inherits the source template's prose, which was written about whoever
the source was set up for -- in the stock 智能办公助理 that is the "管理员", and
the alias pass only rewrites the configured marker (default ``admin``). A Chinese
role word therefore survives cloning, and a plain member's card ends up claiming
to be an administrator's exclusive assistant. See
``openspec/changes/personalize-personal-assistant-owner-fields``.

The fix is not a wider alias table: the source template's wording is
deployment-variable (``resolve_source`` matches a configurable name), so the
*owner-facing lead* is generated from the member's display name by a configurable
template, while the substantive remainder of the source field is preserved.
"""

import json
import os
import unittest
from unittest.mock import patch

import config as config_module

# Captured before any patch so a test can widen ``config.conf`` for just the
# template keys instead of replacing the process configuration wholesale.
_REAL_CONF = config_module.conf

from agent.personal_assistant import (  # noqa: E402
    DEFAULT_DESCRIPTION_TEMPLATE,
    DEFAULT_PERSONA_SUMMARY_TEMPLATE,
    render_owner_field,
    source_tail,
)
from tests.test_user_personal_agent_provisioning import (  # noqa: E402
    SOURCE_ID,
    SOURCE_NAME,
    _Base,
)

#: The stock description an operator writes for the shared 智能办公助理.
SOURCE_DESCRIPTION = (
    "管理员专属智能办公助理：人设与长期记忆跟随 admin 本人，不进租户共享目录")
SOURCE_PERSONA = "管理员的私人智能办公助理：结论先行，能代办就直接办成；严守隐私边界。"


def _templates(**overrides):
    """Patch ``config.conf`` so only these template keys change.

    ``_setting`` reads the process configuration, which is shared with the rest
    of the run; merging over the real values keeps every other setting intact.
    """
    def fake_conf():
        merged = dict(_REAL_CONF() or {})
        merged.update(overrides)
        return merged
    return patch("config.conf", fake_conf)


BLANK_TEMPLATES = {
    "personal_assistant_description_template": "",
    "personal_assistant_persona_summary_template": "",
}


# --- the pure half -------------------------------------------------------

class OwnerFieldRenderingTests(unittest.TestCase):
    """``render_owner_field`` turns a template + display name + source text."""

    def test_the_lead_is_the_members_name_and_the_description_survives(self):
        rendered = render_owner_field(
            DEFAULT_DESCRIPTION_TEMPLATE, name="Rock",
            source_text=SOURCE_DESCRIPTION)

        self.assertEqual(
            rendered,
            "Rock的专属办公助理：人设与长期记忆跟随 admin 本人，不进租户共享目录")

    def test_the_persona_summary_keeps_its_substance(self):
        rendered = render_owner_field(
            DEFAULT_PERSONA_SUMMARY_TEMPLATE, name="Rock",
            source_text=SOURCE_PERSONA)

        self.assertEqual(
            rendered, "Rock的私人智能办公助理：结论先行，能代办就直接办成；严守隐私边界。")

    def test_a_source_field_without_a_separator_has_an_empty_tail(self):
        self.assertEqual(source_tail("管理员的私人智能办公助理"), "")

        rendered = render_owner_field(
            "{name}的专属办公助理{source_tail}", name="Rock",
            source_text="管理员的私人智能办公助理")

        self.assertEqual(rendered, "Rock的专属办公助理")

    def test_an_ascii_colon_also_separates(self):
        self.assertEqual(
            source_tail("admin assistant: keeps memory private"),
            "keeps memory private")

    def test_a_blank_template_opts_the_field_out(self):
        for blank in ("", "   "):
            self.assertIsNone(render_owner_field(
                blank, name="Rock", source_text=SOURCE_DESCRIPTION))

    def test_a_field_the_source_does_not_have_is_not_invented(self):
        for missing in (None, ""):
            self.assertIsNone(render_owner_field(
                DEFAULT_DESCRIPTION_TEMPLATE, name="Rock", source_text=missing))

    def test_unknown_braces_stay_literal(self):
        rendered = render_owner_field(
            "{name}的助理 {not_a_placeholder}", name="Rock", source_text="x")

        self.assertEqual(rendered, "Rock的助理 {not_a_placeholder}")


# --- the provisioning path -----------------------------------------------

class _OwnerFieldsBase(_Base):
    """The shared 智能办公助理 carrying the stock administrator wording."""

    def setUp(self):
        super().setUp()
        # ``update_agent`` takes a partial update and rebuilds the roster entry,
        # so the fixture ends up with exactly the fields under test.
        self.admin.update_agent(SOURCE_ID, description=SOURCE_DESCRIPTION,
                                persona_summary=SOURCE_PERSONA)
        self._reload_registry()

    # --- harness ---------------------------------------------------------

    def _add_shared_agent(self, agent_id, *, name=None, description=None,
                          tenant_id=None):
        """An ordinary tenant-shared Agent with its own persona, bound to a tenant.

        Built through the admin service rather than by editing files: placing an
        Agent relocates the roster into ``team.json``, so hand-writing a config
        entry would target a file the service no longer reads.
        """
        self.admin.clone_agent(
            SOURCE_ID, agent_id, name=name or agent_id,
            workspace=os.path.join(self.instance, "agents", agent_id))
        if description is not None:
            self.admin.update_agent(agent_id, description=description)
        self._reload_registry()
        self.svc.bind_agent(tenant_id=tenant_id or self.tenant_id, agent_id=agent_id)
        return agent_id

    def _pre_fix_copy(self, username, display_name):
        """A personal assistant as the code before this change left it."""
        with _templates(**BLANK_TEMPLATES):
            return self._add_member(username, display_name)

    def _changed_ids(self, result):
        return [row[1] for row in result["changed"]]

    def _skipped_ids(self, result):
        return [row[1] for row in result["skipped"]]


class GeneratedWordingTests(_OwnerFieldsBase):
    def test_the_description_names_the_member(self):
        member = self._add_member("RC001", "Rock")

        profile = self._profile(member["personal_agent"]["agent_id"])

        self.assertEqual(
            profile["description"],
            "Rock的专属办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")

    def test_the_persona_summary_names_the_member(self):
        member = self._add_member("RC001", "Rock")

        profile = self._profile(member["personal_agent"]["agent_id"])

        self.assertEqual(
            profile["persona_summary"],
            "Rock的私人智能办公助理：结论先行，能代办就直接办成；严守隐私边界。")

    def test_the_source_template_keeps_its_wording(self):
        self._add_member("RC001", "Rock")

        source = self._profile(SOURCE_ID)

        self.assertEqual(source["description"], SOURCE_DESCRIPTION)
        self.assertEqual(source["persona_summary"], SOURCE_PERSONA)

    def test_the_name_is_still_the_shared_assistant_name(self):
        member = self._add_member("RC001", "Rock")

        self.assertEqual(self._profile(member["personal_agent"]["agent_id"])["name"],
                         SOURCE_NAME)

    def test_a_role_word_outside_the_lead_is_not_guessed_at(self):
        """The boundary the spec keeps: only the lead is authored."""
        self.admin.update_agent(
            SOURCE_ID,
            description="管理员专属智能办公助理：管理员可随时查阅 admin 的日程")
        self._reload_registry()

        member = self._add_member("RC001", "Rock")

        self.assertEqual(
            self._profile(member["personal_agent"]["agent_id"])["description"],
            "Rock的专属办公助理：管理员可随时查阅 Rock 的日程")

    def test_a_deployment_can_override_the_template(self):
        with _templates(personal_assistant_description_template="{name} 的工作助理 — {source_tail}",
                        personal_assistant_persona_summary_template=""):
            member = self._add_member("RC001", "Rock")

        profile = self._profile(member["personal_agent"]["agent_id"])

        self.assertEqual(
            profile["description"],
            "Rock 的工作助理 — 人设与长期记忆跟随 Rock 本人，不进租户共享目录")
        # The blank persona template preserves the source wording.
        self.assertEqual(profile["persona_summary"], SOURCE_PERSONA)

    def test_blank_templates_restore_the_previous_behaviour(self):
        with _templates(**BLANK_TEMPLATES):
            member = self._add_member("RC001", "Rock")

        profile = self._profile(member["personal_agent"]["agent_id"])

        self.assertEqual(
            profile["description"],
            "管理员专属智能办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")
        self.assertEqual(profile["persona_summary"], SOURCE_PERSONA)


# --- backfilling copies that predate the change --------------------------

class PersonalizeExistingTests(_OwnerFieldsBase):
    def test_an_existing_copy_is_corrected(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        result = self._provisioner().personalize_existing()

        profile = self._profile(agent_id)
        self.assertIn(agent_id, self._changed_ids(result))
        self.assertEqual(
            profile["description"],
            "Rock的专属办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")
        self.assertEqual(
            profile["persona_summary"],
            "Rock的私人智能办公助理：结论先行，能代办就直接办成；严守隐私边界。")

    def test_dry_run_reports_without_writing(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        result = self._provisioner().personalize_existing(dry_run=True)

        self.assertIn(agent_id, self._changed_ids(result))
        self.assertEqual(self._profile(agent_id)["description"],
                         "管理员专属智能办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")

    def test_a_second_run_is_a_no_op(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        self._provisioner().personalize_existing()
        second = self._provisioner().personalize_existing()

        self.assertEqual(second["changed"], [])
        self.assertIn(agent_id, self._skipped_ids(second))

    def test_an_up_to_date_copy_is_never_touched(self):
        """A copy created by the new code needs no backfill at all."""
        member = self._add_member("RC001", "Rock")
        before = self._profile(member["personal_agent"]["agent_id"])

        result = self._provisioner().personalize_existing()

        self.assertEqual(result["changed"], [])
        self.assertEqual(self._profile(member["personal_agent"]["agent_id"]),
                         before)

    def test_the_source_and_ordinary_agents_keep_their_wording(self):
        self._add_shared_agent("shared-report", description="全租户共享的经营分析参谋")
        self._pre_fix_copy("RC001", "Rock")

        result = self._provisioner().personalize_existing()

        self.assertEqual(result["failed"], [])
        self.assertEqual(self._profile(SOURCE_ID)["description"], SOURCE_DESCRIPTION)
        self.assertEqual(self._profile(SOURCE_ID)["persona_summary"], SOURCE_PERSONA)
        self.assertEqual(self._profile("shared-report")["description"],
                         "全租户共享的经营分析参谋")

    def test_a_tenant_filter_spares_other_tenants(self):
        with _templates(**BLANK_TEMPLATES):
            here = self._add_member("RC001", "Rock")
            other_tenant = self._new_tenant("globex")
            # A binding belongs to exactly one tenant, so another tenant needs
            # its own 智能办公助理 to clone from.
            self._add_shared_agent("assistant-globex", name=SOURCE_NAME,
                                   tenant_id=other_tenant["id"])
            there = self.svc.create_member(
                actor_user_id=self.root_id, tenant_id=other_tenant["id"],
                operation="create-new", username="GB001", display_name="Grace",
                temporary_password="TempPass123!", roles=[])
        here_id = here["personal_agent"]["agent_id"]
        there_id = there["personal_agent"]["agent_id"]

        result = self._provisioner().personalize_existing(
            tenant_id=self.tenant_id)

        self.assertIn(here_id, self._changed_ids(result))
        self.assertNotIn(there_id, self._changed_ids(result))
        self.assertEqual(
            self._profile(there_id)["description"],
            "管理员专属智能办公助理：人设与长期记忆跟随 Grace 本人，不进租户共享目录")

    def test_blank_templates_leave_every_field_alone(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        with _templates(**BLANK_TEMPLATES):
            result = self._provisioner().personalize_existing()

        self.assertEqual(result["changed"], [])
        self.assertEqual(self._profile(agent_id)["description"],
                         "管理员专属智能办公助理：人设与长期记忆跟随 Rock 本人，不进租户共享目录")

    def test_a_member_made_agent_is_not_re_authored(self):
        """The backfill repairs *system-supplied* copies. An agent the member
        made for themselves is theirs, so the pass must leave it alone."""
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]
        before = self._profile(agent_id)["description"]
        self.svc._store.execute(
            "UPDATE agent_bindings SET origin='user_created' WHERE agent_id=?",
            (agent_id,))

        result = self._provisioner().personalize_existing()

        self.assertNotIn(agent_id, self._changed_ids(result))
        self.assertEqual(self._profile(agent_id)["description"], before)

    def test_a_backfill_is_audited(self):
        member = self._pre_fix_copy("RC001", "Rock")
        agent_id = member["personal_agent"]["agent_id"]

        self._provisioner().personalize_existing()

        events = self._audit("member.personal_agent.personalize")
        self.assertEqual(len(events), 1, "one event per corrected assistant")
        self.assertEqual(events[0]["result"], "success")
        self.assertIn(agent_id, events[0]["redacted_changes"] or "")
        self.assertIn("description", events[0]["redacted_changes"] or "")

    def test_a_dry_run_writes_no_audit(self):
        self._pre_fix_copy("RC001", "Rock")

        self._provisioner().personalize_existing(dry_run=True)

        self.assertEqual(self._audit("member.personal_agent.personalize"), [])


if __name__ == "__main__":
    unittest.main()
