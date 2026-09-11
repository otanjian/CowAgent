# encoding:utf-8
"""Report what an upstream merge attempt means (tasks 9.3/9.4).

`scripts/sync-from-master.sh` does the untestable part — fetch and try a merge —
and hands the result here. This module owns the judgement:

* which files conflicted (the caller reads them from git, so this stays pure);
* whether that set matches `scripts/conflict-baseline.txt`, i.e. whether a
  known conflict *disappeared* (a seam worked, or upstream changed shape) or a
  **new** conflict appeared (a seam has been outgrown);
* which files are deliberate removals the operator must re-decide, because
  "keep our deletion" is a standing decision that should be revisited on every
  sync rather than silently re-applied.

Exit code is the contract: 0 when the merge applied cleanly, 1 when it did not.
Nothing here writes to the repository — the script aborts the merge afterwards,
and neither the script nor this module ever commits or pushes.
"""

from __future__ import annotations

import argparse
import os
import sys

#: Files the fork intentionally deleted or replaced. Kept as a constant *and*
#: read back from the baseline, so a mismatch between the two is visible rather
#: than silently tolerated.
DELIBERATE_REMOVALS = (
    "README.md",
    "docs/zh/README.md",
    "docs/zh/README-Hant.md",
    "docs/ja/README.md",
    "desktop/src/renderer/src/components/PermissionSelector.tsx",
)

_DEFAULT_BASELINE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "conflict-baseline.txt"
)


def load_baseline(path: str = _DEFAULT_BASELINE) -> dict:
    """Parse the frozen baseline into ``{path: {status, disposition}}``.

    Comment and blank lines are ignored, so the file can explain itself. A
    malformed line is an error rather than a silent skip: a baseline nobody can
    read is worse than no baseline, because it would report every known
    conflict as drift.
    """
    entries: dict = {}
    with open(path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                raise ValueError(f"{path}:{number}: expected 4 tab-separated columns")
            _hunks, status, path_, disposition = parts[0], parts[1], parts[2], parts[3]
            entries[path_.strip()] = {
                "status": status.strip(),
                "disposition": disposition.strip(),
            }
    return entries


def compare_to_baseline(conflicted, baseline: dict) -> dict:
    """Classify the conflict set against the baseline.

    ``gone`` is only meaningful while something *is* conflicting: a merge with
    no conflicts would otherwise report all twenty baseline files as
    "disappeared", which is noise, not drift.
    """
    current = sorted(set(conflicted))
    known = set(baseline)
    return {
        "clean": not current,
        "expected": [p for p in current if p in known],
        "new": [p for p in current if p not in known],
        "gone": sorted(p for p in known if p not in current) if current else [],
    }


def _removal_lines(entries: dict) -> list:
    dispositions = {p: e["disposition"] for p, e in entries.items()}
    lines = []
    for path in DELIBERATE_REMOVALS:
        disposition = dispositions.get(path, "NOT IN BASELINE")
        lines.append(f"  {path}  [{disposition}]")
    # A `keep-deletion` entry that is not in the constant (or vice versa) means
    # the two lists have drifted; say so instead of reporting a tidy list.
    for path, disposition in sorted(dispositions.items()):
        if disposition.startswith("keep-deletion") and path not in DELIBERATE_REMOVALS:
            lines.append(f"  {path}  [{disposition}]  (not in DELIBERATE_REMOVALS)")
    return lines


def format_report(conflicted, baseline: dict) -> tuple:
    """``(text, exit_code)`` for a finished merge attempt."""
    result = compare_to_baseline(conflicted, baseline)
    out = []
    if result["clean"]:
        out.append("已合并、待复核：本次合并没有冲突。")
        out.append("提示：合并结果尚未提交，请复核 diff 后自行提交。")
        return os.linesep.join(out), 0

    out.append(f"合并冲突：{len(result['expected']) + len(result['new'])} 个文件")
    for path in sorted(set(result["expected"]) | set(result["new"])):
        disposition = baseline.get(path, {}).get("disposition", "")
        marker = "新增冲突" if path in result["new"] else "已知冲突"
        out.append(f"  [{marker}] {path}  {disposition}")
    if result["new"]:
        out.append("")
        out.append("相对基线的漂移（新增冲突）——接缝可能已被上游改动越过：")
        for path in result["new"]:
            out.append(f"  + {path}")
    if result["gone"]:
        out.append("")
        out.append("基线中不再冲突的文件——接缝生效或上游改动，请复核基线：")
        for path in result["gone"]:
            out.append(f"  - {path}")
    out.append("")
    out.append("需要逐次复核的删除/修改类决策：")
    out.extend(_removal_lines(baseline))
    out.append("")
    out.append("合并已被中止；不会自动提交或推送。")
    return os.linesep.join(out), 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Report an upstream merge attempt")
    parser.add_argument("--baseline", default=_DEFAULT_BASELINE)
    parser.add_argument(
        "--conflicts", nargs="*", default=[],
        help="conflicted paths (from git diff --name-only --diff-filter=U)",
    )
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as error:
        print(f"baseline unreadable: {error}", file=sys.stderr)
        return 2
    text, code = format_report(args.conflicts, baseline)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
