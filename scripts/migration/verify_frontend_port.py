#!/usr/bin/env python3
"""Verify the frontend port preserved the fork's customization (Phase 3 gate).

``port_frontend.py`` reporting "275 of 362 clusters applied" is the porter's own
count, and ``node --check`` only proves the output parses. Neither answers the
question that matters: did the fork's frontend behaviour actually survive?

The reliable signal is **fork-unique lines**: lines that exist in the fork's
monolith but in neither the merge base nor any upstream module. Those are the
fork's genuine additions -- its identity UI, authorization gates, branding. Most
fork lines are upstream lines (the fork changed ~40% of the file), so a naive
"is this fork line present?" check is dominated by coincidental matches and
reports false partial-porting. Fork-unique lines cannot match by accident.

Each one must be either:
  * present in the emitted fork modules, or
  * inside a cluster the porter marked ``skipped`` (i.e. on the hand-port list).

Anything else is a silent loss. That is the failure this phase is structured to
prevent: a missing authorization branch in the console is far worse than a
flagged worklist item.

Read-only; exits non-zero when a fork-unique line is unaccounted for.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_frontend_divergence import (  # noqa: E402
    git_ls, git_show, lines_of, norm,
)


def main() -> int:
    workdir = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")
    base_ref = os.environ.get("FORK_BASE_REF", "e5e2a52d")
    upstream_ref = os.environ.get("FORK_UPSTREAM_REF", "origin/master")
    fork_ref = os.environ.get("FORK_FORK_REF", "HEAD")

    js_dirs = ("channel/web/static/js/core", "channel/web/static/js/chat",
               "channel/web/static/js/views")
    root = os.path.join(workdir, "fork-frontend")
    if not os.path.isdir(root):
        print(f"no porter output under {workdir}; run port_frontend.py first")
        return 2
    cl_path = os.path.join(workdir, "port_frontend_clusters.json")
    if not os.path.isfile(cl_path):
        print(f"no cluster report at {cl_path}; re-run port_frontend.py")
        return 2
    with open(cl_path, encoding="utf-8") as f:
        clusters = json.load(f)

    emitted: set = set()
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith((".js", ".css")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    emitted.update(norm(l) for l in fh.read().splitlines() if norm(l))

    upstream_paths = sum((git_ls(upstream_ref, d) for d in js_dirs), []) + \
        git_ls(upstream_ref, "channel/web/static/css")
    upstream_lines: set = set()
    for p in upstream_paths:
        upstream_lines.update(norm(l) for l in lines_of(git_show(f"{upstream_ref}:{p}"))
                              if norm(l))

    total = lost_total = 0
    for name, path in (("console.js", "channel/web/static/js/console.js"),
                       ("console.css", "channel/web/static/css/console.css")):
        fork = lines_of(git_show(f"{fork_ref}:{path}"))
        base = lines_of(git_show(f"{base_ref}:{path}"))
        known = {norm(l) for l in base if norm(l)} | upstream_lines
        fork_unique = {norm(l) for l in fork if norm(l)} - known

        mine = [c for c in clusters if c["source"] == name]
        # Lines belonging to clusters the porter deferred: hand-port list.
        deferred: set = set()
        for c in mine:
            if c["status"] == "skipped":
                deferred.update(norm(l) for l in
                                fork[c["fork_range"][0] - 1:c["fork_range"][1]]
                                if norm(l))

        # Only fork-unique lines *inside a changed region* are this port's
        # responsibility. A fork-unique line in a region identical to the base
        # cannot exist, so the whole file is the right scope.
        lost = sorted(l for l in fork_unique
                      if l not in emitted and l not in deferred)
        total += len(fork_unique)
        lost_total += len(lost)

        accounted_in_output = len(fork_unique) - len(
            [l for l in fork_unique if l not in emitted])
        print(f"{name}: {len(fork_unique)} fork-unique lines")
        print(f"  {accounted_in_output} present in the emitted fork modules")
        print(f"  {len(fork_unique) - accounted_in_output - len(lost)} on the "
              f"hand-port list")
        print(f"  {len(lost)} UNACCOUNTED")
        if lost:
            print("  examples of unaccounted lines:")
            for l in lost[:12]:
                print(f"    {l[:110]}")

    # Independent parse check: the gate must not rely on the porter's claim.
    bad = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".js"):
                p = os.path.join(dirpath, name)
                if subprocess.run(["node", "--check", p],
                                  capture_output=True).returncode != 0:
                    bad.append(os.path.relpath(p, root))
    print(f"\nnode --check: {'all modules parse' if not bad else 'FAILED: ' + ', '.join(bad)}")

    ok = lost_total == 0 and not bad
    print(f"\n{'PASS' if ok else 'FAIL'}: {total - lost_total}/{total} fork-unique "
          f"lines accounted for")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
