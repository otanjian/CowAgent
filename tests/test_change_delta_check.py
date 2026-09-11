# encoding:utf-8
"""Tests for the archive-time consistency checker (tasks 10.5 / 10.6).

``scripts/check-change-deltas.py`` is a gate, so a gate that silently passes on
broken input is worse than no gate. These tests build a miniature spec tree and
assert that each failure mode is actually detected.
"""
import pathlib
import tempfile
import unittest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.check_change_deltas import (check_applied, check_conflict_coverage,
                                         check_deltas)

BASE_SPEC = """# capability

## Requirements

### Requirement: shared route policy

Text.

#### Scenario: one

#### Scenario: two

### Requirement: unrelated

Text.
"""


class DeltaCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "openspec"
        (self.root / "specs" / "cap").mkdir(parents=True)
        (self.root / "specs" / "cap" / "spec.md").write_text(
            BASE_SPEC, encoding="utf-8")
        (self.root / "specs" / "other").mkdir(parents=True)
        (self.root / "specs" / "other" / "spec.md").write_text(
            "### Requirement: others own\n", encoding="utf-8")
        self.delta = self.root / "changes" / "c" / "specs" / "cap"
        self.delta.mkdir(parents=True)

    def _delta(self, text):
        (self.delta / "spec.md").write_text(text, encoding="utf-8")
        return check_deltas(self.root, "c")

    def test_a_verbatim_modified_requirement_passes(self):
        self.assertEqual(self._delta(
            "## MODIFIED Requirements\n\n"
            "### Requirement: shared route policy\n\n"
            "#### Scenario: one\n\n#### Scenario: two\n\n"
            "#### Scenario: added by this change\n"), [])

    def test_a_reworded_modified_title_is_rejected(self):
        problems = self._delta(
            "## MODIFIED Requirements\n\n"
            "### Requirement: shared route policy!\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("not in the baseline verbatim", problems[0])

    def test_dropping_a_baseline_scenario_is_rejected(self):
        problems = self._delta(
            "## MODIFIED Requirements\n\n"
            "### Requirement: shared route policy\n\n"
            "#### Scenario: one\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("drops baseline scenarios", problems[0])
        self.assertIn("two", problems[0])

    def test_re_adding_an_existing_requirement_is_rejected(self):
        problems = self._delta(
            "## ADDED Requirements\n\n"
            "### Requirement: unrelated\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("restates an existing requirement", problems[0])

    def test_restating_another_capability_is_rejected(self):
        problems = self._delta(
            "## ADDED Requirements\n\n"
            "### Requirement: others own\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("restates another capability", problems[0])


class AppliedCheckTests(unittest.TestCase):
    """Post-archive the same delta means the opposite thing.

    This is the mode that was missing: the checker used to fail after archiving
    (it could not find the change) and, once fixed to find it, would have flagged
    every ADDED requirement as a restatement -- because after a successful sync
    those requirements are *supposed* to exist.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "openspec"
        self.main = self.root / "specs" / "cap"
        self.main.mkdir(parents=True)
        self.main_spec = self.main / "spec.md"
        self.main_spec.write_text(BASE_SPEC, encoding="utf-8")
        # Archived location, with the dated name `openspec archive` produces.
        self.archive = (self.root / "changes" / "archive"
                        / "2026-09-11-c" / "specs" / "cap")
        self.archive.mkdir(parents=True)

    def _delta(self, text):
        (self.archive / "spec.md").write_text(text, encoding="utf-8")
        return check_applied(self.root, "c")

    def _apply_main(self, text):
        self.main_spec.write_text(text, encoding="utf-8")

    def test_an_applied_delta_passes(self):
        # The baseline already carries what the delta proposed.
        self.assertEqual(self._delta(
            "## MODIFIED Requirements\n\n"
            "### Requirement: shared route policy\n\n"
            "#### Scenario: one\n\n#### Scenario: two\n"), [])

    def test_an_added_requirement_is_not_a_restatement_once_applied(self):
        self._apply_main(BASE_SPEC + "\n### Requirement: brand new\n")
        problems = self._delta(
            "## ADDED Requirements\n\n### Requirement: brand new\n")
        self.assertEqual(problems, [], problems)

    def test_a_dropped_requirement_is_reported(self):
        problems = self._delta(
            "## ADDED Requirements\n\n### Requirement: never landed\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("was not applied", problems[0])

    def test_a_dropped_scenario_is_reported(self):
        problems = self._delta(
            "## MODIFIED Requirements\n\n"
            "### Requirement: shared route policy\n\n"
            "#### Scenario: one\n\n#### Scenario: lost in the sync\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("missing applied scenarios", problems[0])
        self.assertIn("lost in the sync", problems[0])

    def test_a_removed_requirement_that_survived_is_reported(self):
        problems = self._delta(
            "## REMOVED Requirements\n\n### Requirement: unrelated\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("is still in the main spec", problems[0])

    def test_a_missing_capability_is_reported(self):
        self._delta("## ADDED Requirements\n\n### Requirement: x\n")
        (self.main_spec).unlink()
        problems = check_applied(self.root, "c")
        self.assertTrue(any("capability spec is missing" in p for p in problems),
                        problems)

    def test_check_deltas_refuses_to_run_on_an_archived_change(self):
        # Guards against silently checking the wrong contract.
        problems = check_deltas(self.root, "c")
        self.assertTrue(any("is archived" in p for p in problems), problems)

    def test_an_unverified_operation_is_reported_not_skipped(self):
        problems = self._delta(
            "## ADDED Requirements\n\n### Requirement: shared route policy\n")
        self.assertEqual(problems, [])
        # RENAMED is outside the checker's contract; it must say so.
        (self.archive / "spec.md").write_text(
            "## RENAMED Requirements\n\n### Requirement: whatever\n",
            encoding="utf-8")
        problems = check_applied(self.root, "c")
        self.assertTrue(any("not verified by this checker" in p
                            for p in problems), problems)


class ResolutionTests(unittest.TestCase):
    def test_an_unknown_change_name_is_reported_not_ignored(self):
        root = pathlib.Path(tempfile.mkdtemp()) / "openspec"
        (root / "specs").mkdir(parents=True)
        problems = check_applied(root, "no-such-change")
        self.assertTrue(any("no change named" in p for p in problems), problems)


class ConflictCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "openspec"
        change = self.root / "changes" / "c"
        change.mkdir(parents=True)
        (change / "tasks.md").write_text(
            "handle channel/web/web_channel.py here", encoding="utf-8")
        self.baseline = self.tmp / "conflict-baseline.txt"

    def _check(self, rows):
        self.baseline.write_text(rows, encoding="utf-8")
        return check_conflict_coverage(self.root, self.baseline, "c")

    def test_a_named_seam_file_passes(self):
        self.assertEqual(self._check(
            "1\tUU\tchannel/web/web_channel.py\tseam:8.3\twhy\n"), [])

    def test_an_unrecognised_disposition_is_rejected(self):
        problems = self._check("1\tUU\tsome/file.py\tkeep-both\twhy\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unrecognised disposition", problems[0])

    def test_a_seam_file_the_change_never_names_is_rejected(self):
        problems = self._check("1\tUU\tagent/other.py\tseam:6.1\twhy\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("never names it", problems[0])

    def test_a_known_disposition_needs_no_mention(self):
        self.assertEqual(self._check(
            "0\tDU\tREADME.md\tkeep-deletion\twhy\n"), [])


if __name__ == "__main__":
    unittest.main()
