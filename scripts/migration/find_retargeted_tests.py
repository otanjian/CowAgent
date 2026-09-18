"""List test files the merge silently re-pointed at upstream's split web modules.

A fork test that imported ``channel.web.web_channel`` (the fork's stack) and now
imports ``channel.web.api.*`` / ``channel.web.core.*`` (upstream's) has been
retargeted by the merge even where there was no textual conflict: the tests then
exercise upstream's handlers while the fork serves its own.
"""
from __future__ import annotations

import re
import subprocess
import sys

UPSTREAM = re.compile(r"channel\.web\.(api|core)\b")
FORK = re.compile(r"channel\.web\.web_channel\b")


def show(rev: str, path: str) -> str:
    out = subprocess.run(["git", "show", f"{rev}:{path}"],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def main() -> int:
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD^1", "HEAD", "--", "tests/"],
        capture_output=True, text=True, check=True).stdout.split()
    suspicious = []
    for path in sorted(p for p in changed if p.endswith(".py")):
        old, new = show("HEAD^1", path), show("HEAD", path)
        if not new or not old:
            continue
        old_up, new_up = len(UPSTREAM.findall(old)), len(UPSTREAM.findall(new))
        old_fk, new_fk = len(FORK.findall(old)), len(FORK.findall(new))
        if new_up > old_up:
            suspicious.append((path, old_up, new_up, old_fk, new_fk))
    print(f"test files changed by the merge: "
          f"{sum(1 for p in changed if p.endswith('.py'))}")
    print(f"files whose upstream-module references grew: {len(suspicious)}")
    print()
    print(f"{'file':58s} {'api/core old->new':>18s} {'web_channel old->new':>22s}")
    for path, old_up, new_up, old_fk, new_fk in suspicious:
        print(f"{path:58s} {old_up:8d} -> {new_up:<7d} {old_fk:12d} -> {new_fk:<7d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
