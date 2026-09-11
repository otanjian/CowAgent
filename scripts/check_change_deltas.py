#!/usr/bin/env python3
# encoding:utf-8
"""Archive-time consistency checks for an OpenSpec change (tasks 10.5 / 10.6).

Two things are easy to get wrong when a change is written by hand and then
archived, and both are silent: a ``MODIFIED`` block whose requirement title no
longer matches the baseline (so the delta applies to nothing), and a conflicted
file recorded in the sync baseline that no part of the change actually covers.

    10|Usage:
    .venv/bin/python scripts/check-change-deltas.py [change-name]
    .venv/bin/python scripts/check-change-deltas.py --conflict-baseline

Exits non-zero on any finding, so it can gate an archive step.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

DEFAULT_CHANGE = "fork-decoupling-and-tenant-hardening"
#: Dispositions ``scripts/sync_report.py`` understands; anything else is a typo
#: that would silently drop the file from the coverage report.
KNOWN_DISPOSITIONS = {"keep-deletion", "keep-fork", "merge-docs"}


def _requirements(text: str) -> dict:
    """Requirement title -> list of scenario titles, in file order."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^### Requirement:\s*(.+)$", line)
        if match:
            current = match.group(1).strip()
            found[current] = []
            continue
        match = re.match(r"^#### Scenario:\s*(.+)$", line)
        if match and current is not None:
            found[current].append(match.group(1).strip())
    return found


def _deltas(text: str):
    """Yield ``(operation, body)`` for each delta block in a change spec."""
    parts = re.split(r"^## (MODIFIED|ADDED|REMOVED|RENAMED) Requirements\s*$",
                     text, flags=re.M)
    for index in range(1, len(parts), 2):
        yield parts[index], parts[index + 1]


def check_deltas(root: pathlib.Path, change: str) -> list[str]:
    specs = root / "specs"
    change_dir = root / "changes" / change / "specs"
    if not change_dir.is_dir():
        return [f"no delta specs at {change_dir}"]

    baseline = {spec.parent.name: _requirements(spec.read_text(encoding="utf-8"))
                for spec in specs.glob("*/spec.md")}
    # Every requirement title in every capability, for the cross-domain check.
    everywhere = {title for titles in baseline.values() for title in titles}

    problems: list[str] = []
    for delta in sorted(change_dir.glob("*/spec.md")):
        capability = delta.parent.name
        existing = baseline.get(capability, {})
        for operation, body in _deltas(delta.read_text(encoding="utf-8")):
            for title, scenarios in _requirements(body).items():
                if operation == "MODIFIED":
                    if title not in existing:
                        problems.append(
                            f"{capability}: MODIFIED header not in the baseline "
                            f"verbatim: {title!r}")
                        continue
                    dropped = [s for s in existing[title] if s not in scenarios]
                    if dropped:
                        problems.append(
                            f"{capability}: MODIFIED {title!r} drops baseline "
                            f"scenarios: {dropped}")
                elif operation == "ADDED":
                    if title in existing:
                        problems.append(
                            f"{capability}: ADDED restates an existing "
                            f"requirement: {title!r}")
                    elif title in everywhere:
                        problems.append(
                            f"{capability}: ADDED restates another capability's "
                            f"requirement: {title!r}")
    return problems


def check_conflict_coverage(root: pathlib.Path, baseline: pathlib.Path,
                            change: str) -> list[str]:
    if not baseline.is_file():
        return [f"no conflict baseline at {baseline}"]
    rows = []
    for line in baseline.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            rows.append(parts)

    change_dir = root / "changes" / change
    body = "\n".join(path.read_text(encoding="utf-8")
                     for path in change_dir.glob("*.md"))

    problems: list[str] = []
    for _hunks, _status, path, disposition in ((r[0], r[1], r[2], r[3])
                                               for r in rows):
        if disposition.startswith("seam:"):
            if not re.match(r"seam:[0-9]", disposition):
                problems.append(f"{path}: malformed seam reference {disposition!r}")
            elif path not in body:
                problems.append(
                    f"{path}: marked {disposition} but this change never names it")
        elif disposition not in KNOWN_DISPOSITIONS:
            problems.append(f"{path}: unrecognised disposition {disposition!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("change", nargs="?", default=DEFAULT_CHANGE)
    parser.add_argument("--root", default="openspec")
    parser.add_argument("--conflict-baseline",
                        default="scripts/conflict-baseline.txt")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    problems = check_deltas(root, args.change)
    problems += check_conflict_coverage(root, pathlib.Path(args.conflict_baseline),
                                       args.change)

    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for problem in problems:
            print("  -", problem)
        return 1
    print(f"OK: {args.change} — delta titles/scenarios consistent with the "
          f"baseline, every conflicted file covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
