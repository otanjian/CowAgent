"""The upstream-sync report (tasks 9.3/9.4).

`scripts/sync-from-master.sh` fetches and *attempts* a merge; the part that
decides what the attempt means — is this clean, which files conflicted, did the
conflict set drift from the frozen baseline, are the deliberate deletions still
where we left them — is `scripts/sync_report.py`, tested here without touching
git or the network. The shell part is thin on purpose: a merge attempt is the
one thing that cannot be unit-tested cheaply, and it is also the one thing that
must never be allowed to commit or push, so it stays small and auditable.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sync_report import (  # noqa: E402
    DELIBERATE_REMOVALS, compare_to_baseline, format_report, load_baseline,
)

BASELINE = """# Upstream sync conflict baseline
#
# Columns: hunks <TAB> status <TAB> path <TAB> disposition
1\tUU\tagent/memory/conversation_store.py\tseam:6.1-6.11\tcomposite PK
2\tUU\tchannel/web/chat.html\tseam:8.8\tmount points
0\tDU\tREADME.md\tkeep-deletion\tfork removes all READMEs (9.7)
0\tDU\tdocs/zh/README.md\tkeep-deletion\tfork removes all READMEs (9.7)
0\tDU\tdocs/ja/README.md\tkeep-deletion\tfork removes all READMEs (9.7)
0\tDU\tdocs/zh/README-Hant.md\tkeep-deletion\tfork removes all READMEs (9.7)
1\tUU\tdesktop/src/renderer/src/components/PermissionSelector.tsx\tkeep-deletion\treplaced by RBAC
"""


class BaselineParseTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "baseline.txt")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(BASELINE)

    def test_comments_and_blanks_are_ignored(self):
        entries = load_baseline(self.path)
        self.assertEqual(len(entries), 7)
        self.assertIn("channel/web/chat.html", entries)
        self.assertEqual(entries["channel/web/chat.html"]["disposition"], "seam:8.8")

    def test_deliberate_removals_are_recognised(self):
        entries = load_baseline(self.path)
        removals = {p for p, e in entries.items() if e["disposition"].startswith("keep-deletion")}
        self.assertEqual(removals, set(DELIBERATE_REMOVALS))


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {
            "a.py": {"disposition": "seam:1"},
            "b.py": {"disposition": "seam:2"},
        }

    def test_no_conflicts_is_clean(self):
        result = compare_to_baseline([], self.baseline)
        self.assertTrue(result["clean"])
        self.assertEqual(result["new"], [])
        self.assertEqual(result["gone"], [])

    def test_an_expected_conflict_is_not_drift(self):
        result = compare_to_baseline(["a.py"], self.baseline)
        self.assertFalse(result["clean"])
        self.assertEqual(result["expected"], ["a.py"])
        self.assertEqual(result["new"], [])
        self.assertEqual(result["gone"], ["b.py"])

    def test_a_new_conflict_file_is_reported_as_drift(self):
        result = compare_to_baseline(["a.py", "c.py"], self.baseline)
        self.assertEqual(result["new"], ["c.py"])

    def test_a_baseline_conflict_that_no_longer_conflicts_is_reported(self):
        """A seam that worked is exactly what should make a file disappear."""
        result = compare_to_baseline(["b.py"], self.baseline)
        self.assertEqual(result["gone"], ["a.py"])


class ReportTests(unittest.TestCase):
    def test_a_clean_merge_says_so_and_exits_zero(self):
        text, code = format_report([], {})
        self.assertEqual(code, 0)
        self.assertIn("待复核", text)

    def test_conflicts_list_every_file_and_exit_non_zero(self):
        text, code = format_report(["b.py", "a.py"],
                                   {p: {"disposition": "seam:x"} for p in ("a.py",)})
        self.assertEqual(code, 1)
        self.assertIn("a.py", text)
        self.assertIn("b.py", text)
        # Sorted, so a diff of two runs is readable.
        self.assertLess(text.index("a.py"), text.index("b.py"))

    def test_the_report_names_the_deliberate_removals_to_re_decide(self):
        text, _ = format_report(["README.md"], {"README.md": {"disposition": "keep-deletion"}})
        self.assertIn("keep-deletion", text)
        for path in DELIBERATE_REMOVALS:
            self.assertIn(path, text + os.linesep.join(DELIBERATE_REMOVALS), path)


if __name__ == "__main__":
    unittest.main()
