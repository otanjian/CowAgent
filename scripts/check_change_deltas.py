#!/usr/bin/env python3
# encoding:utf-8
"""Archive-time consistency checks for an OpenSpec change (tasks 10.5 / 10.6).

Two things are easy to get wrong when a change is written by hand and then
archived, and both are silent: a ``MODIFIED`` block whose requirement title no
longer matches the baseline (so the delta applies to nothing), and a conflicted
file recorded in the sync baseline that no part of the change actually covers.

The change is checked in one of two modes, chosen from where it lives, because
the same delta means opposite things either side of an archive:

- **active** (``changes/<name>/``) -- the delta is a *proposal*. Its MODIFIED
  titles must match the baseline verbatim and keep its scenarios, and its ADDED
  titles must not already exist (that would be a restatement).
- **archived** (``changes/archive/<date>-<name>/``) -- the delta is a *record of
  what was applied*, and the ADDED requirements exist in the baseline precisely
  because the sync worked. So the question inverts: every ADDED and MODIFIED
  requirement, and every scenario, must now be *present* in the main spec. That
  is what catches a sync which silently dropped a requirement.

An operation block the checker does not understand is reported, never skipped:
a gate that passes by ignoring input is worse than no gate.

Also checks that every conflicted file in the upstream-sync baseline carries a
recognised disposition, and that every ``seam:`` row is named by the change.

Usage:
    .venv/bin/python scripts/check_change_deltas.py [change-name]
    .venv/bin/python scripts/check_change_deltas.py --conflict-baseline

Exits non-zero on any finding, so it can gate an archive step.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

DEFAULT_CHANGE = "fork-decoupling-and-tenant-hardening"
#: Dispositions ``scripts/sync_report.py`` understands; anything else is a typo
#: that would silently drop the file from the coverage report. The vocabulary was
#: extended with the sync baseline (D7 of ``adopt-upstream-web-split``) to cover
#: the cases the original three could not name:
#:
#: * ``merge``         both sides' changes are carried in one file, so neither
#:                     "keep one side" nor "docs only" describes the result;
#: * ``retarget``      the file is a fork test the merge silently re-pointed at
#:                     upstream's split modules; the fork's stack is the target;
#: * ``take-deletion`` upstream deleted the file and the fork's edit is moot --
#:                     the mirror of ``keep-deletion``, and kept distinct from it
#:                     precisely so it does *not* join DELIBERATE_REMOVALS.
KNOWN_DISPOSITIONS = {"keep-deletion", "keep-fork", "merge-docs",
                      "merge", "retarget", "take-deletion"}
#: A seam reference names the module that owns the seam (``seam:scheduler``),
#: or -- in baselines predating the module naming -- the task number that does
#: (``seam:8.3``). Both forms are accepted; only a bare ``seam:`` is malformed.
SEAM_REFERENCE = re.compile(r"seam:[A-Za-z0-9][A-Za-z0-9._-]*$")
#: Delta operations this checker knows how to verify.
KNOWN_OPERATIONS = {"ADDED", "MODIFIED", "REMOVED"}


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


def _resolve_change_dir(root: pathlib.Path, change: str) -> tuple[pathlib.Path, bool] | None:
    """Find a change, active or archived, and say which it is.

    A change's deltas are worth re-checking *after* it is archived too -- that is
    when they were applied, so that is when a mis-synced main spec is most likely
    and most expensive. ``openspec archive`` moves the change under
    ``changes/archive/<date>-<name>/``, so accept either location (and accept the
    dated name) instead of only the pre-archive path.
    """
    if (root / "changes" / change).is_dir():
        return root / "changes" / change, False
    archive = root / "changes" / "archive"
    if archive.is_dir():
        matches = sorted(path for path in archive.iterdir()
                         if path.is_dir() and path.name.endswith(change))
        if matches:
            return matches[-1], True
    return None


def check_deltas(root: pathlib.Path, change: str) -> list[str]:
    """Active-change check: the delta must be *applicable* to the baseline."""
    resolved = _resolve_change_dir(root, change)
    if resolved is None:
        return [f"no change named {change!r} under {root / 'changes'}"]
    change_dir_root, archived = resolved
    if archived:
        return [f"{change!r} is archived; use check_applied() for the "
                f"post-archive contract"]
    change_dir = change_dir_root / "specs"
    if not change_dir.is_dir():
        return [f"no delta specs at {change_dir}"]

    baseline = {spec.parent.name: _requirements(spec.read_text(encoding="utf-8"))
                for spec in (root / "specs").glob("*/spec.md")}
    # Every requirement title in every capability, for the cross-domain check.
    everywhere = {title for titles in baseline.values() for title in titles}

    problems: list[str] = []
    for delta in sorted(change_dir.glob("*/spec.md")):
        capability = delta.parent.name
        existing = baseline.get(capability, {})
        for operation, body in _deltas(delta.read_text(encoding="utf-8")):
            if operation not in KNOWN_OPERATIONS:
                problems.append(
                    f"{capability}: {operation} block is not verified by this "
                    f"checker -- handle it explicitly rather than skipping it")
                continue
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


def check_applied(root: pathlib.Path, change: str) -> list[str]:
    """Archived-change check: the delta must be *present* in the baseline.

    Inverts ``check_deltas``: after a sync the main spec is expected to contain
    what the delta said. A missing title or scenario means the sync dropped it.
    """
    resolved = _resolve_change_dir(root, change)
    if resolved is None:
        return [f"no change named {change!r} under {root / 'changes'}"]
    change_dir_root, _archived = resolved
    change_dir = change_dir_root / "specs"
    if not change_dir.is_dir():
        return [f"no delta specs at {change_dir}"]

    baseline = {spec.parent.name: _requirements(spec.read_text(encoding="utf-8"))
                for spec in (root / "specs").glob("*/spec.md")}

    problems: list[str] = []
    for delta in sorted(change_dir.glob("*/spec.md")):
        capability = delta.parent.name
        applied = baseline.get(capability)
        if applied is None:
            problems.append(f"{capability}: capability spec is missing from "
                            f"{root / 'specs'}")
            continue
        for operation, body in _deltas(delta.read_text(encoding="utf-8")):
            if operation not in KNOWN_OPERATIONS:
                problems.append(
                    f"{capability}: {operation} block is not verified by this "
                    f"checker -- handle it explicitly rather than skipping it")
                continue
            for title, scenarios in _requirements(body).items():
                if operation == "REMOVED":
                    if title in applied:
                        problems.append(
                            f"{capability}: REMOVED {title!r} is still in the "
                            f"main spec")
                    continue
                if title not in applied:
                    problems.append(
                        f"{capability}: {operation} {title!r} was not applied to "
                        f"the main spec")
                    continue
                missing = [s for s in scenarios if s not in applied[title]]
                if missing:
                    problems.append(
                        f"{capability}: {operation} {title!r} is missing applied "
                        f"scenarios: {missing}")
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

    resolved = _resolve_change_dir(root, change)
    if resolved is None:
        return [f"no change named {change!r} under {root / 'changes'}"]
    change_dir, _archived = resolved
    body = "\n".join(path.read_text(encoding="utf-8")
                     for path in change_dir.glob("*.md"))

    problems: list[str] = []
    for _hunks, _status, path, disposition in ((r[0], r[1], r[2], r[3])
                                               for r in rows):
        if disposition.startswith("seam:"):
            if not SEAM_REFERENCE.match(disposition):
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
    resolved = _resolve_change_dir(root, args.change)
    archived = bool(resolved and resolved[1])
    problems = (check_applied(root, args.change) if archived
                else check_deltas(root, args.change))
    problems += check_conflict_coverage(root, pathlib.Path(args.conflict_baseline),
                                       args.change)

    mode = "applied" if archived else "proposed"
    if problems:
        print(f"FAIL ({mode}): {len(problems)} problem(s)")
        for problem in problems:
            print("  -", problem)
        return 1
    print(f"OK ({mode}): {args.change} — deltas consistent with the baseline, "
          f"every conflicted file covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
