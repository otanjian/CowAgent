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

from scripts.check_change_deltas import check_conflict_coverage, check_deltas

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
