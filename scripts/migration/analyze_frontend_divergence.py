#!/usr/bin/env python3
"""Inventory the fork's frontend customization and map it to upstream's split.

Phase 3 task 4.1. Upstream deleted ``console.js`` / ``console.css`` and split
them into ``static/js/{core,chat,views}/*`` + ``static/css/*``. The fork's
copies are modified versions of the *base* (merge-base) files, so the fork's
customization is the diff against that base -- not against upstream's new files,
which share no filenames.

To port the customization onto upstream's layout you have to know which upstream
module now owns each customized region. That is what this produces:

  1. index upstream's modules by normalized line (whitespace collapsed), so a
     base line can be looked up in the module that carries it;
  2. walk the base -> fork diff, and for every changed hunk attribute it to the
     upstream module(s) that own the surrounding base lines;
  3. report per-module customization volume, and how much of the base failed to
     map (upstream edited those regions too, so they need reading, not porting).

Read-only: works off ``git show`` and writes a JSON report to
``$FORK_MIGRATION_WORKDIR/frontend_divergence.json``.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Set, Tuple

WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")
BASE_REF = os.environ.get("FORK_BASE_REF", "e5e2a52d")
UPSTREAM_REF = os.environ.get("FORK_UPSTREAM_REF", "origin/master")
FORK_REF = os.environ.get("FORK_FORK_REF", "HEAD")

JS_DIRS = ("channel/web/static/js/core", "channel/web/static/js/chat",
           "channel/web/static/js/views")
CSS_DIR = "channel/web/static/css"


def git_show(rev_path: str) -> str:
    return subprocess.run(["git", "show", rev_path], capture_output=True,
                          text=True, check=True).stdout


def git_ls(rev: str, path: str) -> List[str]:
    out = subprocess.run(["git", "ls-tree", "-r", rev, "--name-only", "--", path],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.split("\n") if line.strip()]


def norm(line: str) -> str:
    """Whitespace-insensitive form of one line, or "" for a blank line."""
    s = re.sub(r"\s+", " ", line.strip())
    return s


def lines_of(text: str) -> List[str]:
    return text.splitlines()


def build_index(files: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """normalized line -> files carrying it, for *distinctive* lines only.

    Short, structural lines (``}``, ``});``, ``// ...``) appear in every module
    and would attribute every hunk to every file. Only lines that identify a
    location are useful as anchors, so anything carried by more than
    ``MAX_OWNERS`` modules -- or too short to be distinctive -- is dropped.
    """
    MAX_OWNERS = 3
    index: Dict[str, List[str]] = defaultdict(list)
    for path, lines in files.items():
        for line in set(norm(l) for l in lines):
            if line and len(line) >= 8:
                index[line].append(path)
    return {k: v for k, v in index.items() if len(v) <= MAX_OWNERS}


def owner_of(index: Dict[str, List[str]], line: str) -> List[str]:
    return index.get(norm(line), [])


def base_owner_map(base: List[str], index: Dict[str, List[str]]) -> List:
    """One upstream module per base line, or None where nothing anchors it.

    Anchored lines are matched directly; the gaps between anchors inherit the
    preceding anchor, because a customised region sits inside whatever module
    carried the code around it. A short gap at the very top takes the first
    following anchor instead.
    """
    owners: List = [None] * len(base)
    for i, line in enumerate(base):
        hits = owner_of(index, line)
        if len(hits) == 1:
            owners[i] = hits[0]
    # forward fill
    last = None
    for i in range(len(owners)):
        if owners[i] is not None:
            last = owners[i]
        elif last is not None:
            owners[i] = last
    # any leading None takes the first anchor seen
    first = next((o for o in owners if o is not None), None)
    for i in range(len(owners)):
        if owners[i] is None:
            owners[i] = first
    return owners


def analyse(name: str, base_text: str, fork_text: str,
            index: Dict[str, List[str]], upstream_files: Set[str]):
    base, fork = lines_of(base_text), lines_of(fork_text)
    owners = base_owner_map(base, index)
    sm = difflib.SequenceMatcher(None, [norm(l) for l in base],
                                 [norm(l) for l in fork], autojunk=False)

    per_module = defaultdict(lambda: {"added": 0, "removed": 0, "hunks": 0,
                                      "changed_base_lines": 0})
    hunks: List[dict] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        added, removed = j2 - j1, i2 - i1
        # The changed region replaces base lines [i1, i2); when it is a pure
        # insertion (i1 == i2) it lands in the module owning the line after it.
        span = [owners[i] for i in range(i1, max(i2, i1 + 1)) if i < len(owners)]
        affected = []
        for m in span:
            if m and m not in affected:
                affected.append(m)
        for m in affected:
            per_module[m]["added"] += added
            per_module[m]["removed"] += removed
            per_module[m]["hunks"] += 1
            per_module[m]["changed_base_lines"] += max(i2 - i1, 1)
        hunks.append({
            "tag": tag,
            "base_range": [i1 + 1, i2],
            "fork_range": [j1 + 1, j2],
            "added": added,
            "removed": removed,
            "affected_modules": affected or None,
            "base_sample": base[i1:i1 + 2],
            "fork_sample": fork[j1:j1 + 2],
        })

    return {
        "file": name,
        "base_lines": len(base),
        "fork_lines": len(fork),
        "base_statements": sum(1 for l in base if norm(l)),
        "fork_statements": sum(1 for l in fork if norm(l)),
        "similarity": round(sm.ratio(), 4),
        "hunks": len(hunks),
        "added": sum(h["added"] for h in hunks),
        "removed": sum(h["removed"] for h in hunks),
        "base_lines_without_anchor": sum(1 for o in owners if o is None),
        "modules_covered": len({o for o in owners if o}),
        "per_upstream_module": {
            k: v for k, v in sorted(per_module.items(),
                                    key=lambda kv: -kv[1]["added"])
        },
        "detail": hunks,
    }


def plan_port(name: str, base_text: str, fork_text: str,
              index: Dict[str, List[str]], files: Dict[str, List[str]],
              owner_map: List) -> dict:
    """Can each fork hunk be re-anchored onto the upstream module that owns it?

    A ``patch``-style port needs a landing site: the lines immediately before a
    hunk in the base file have to still exist, in order, in whichever upstream
    module inherited that region. If they do, a tool can find the offset and
    apply the hunk; if upstream rewrote the surrounding code, the hunk has to be
    read and re-expressed by hand.

    This measures that per hunk -- it is the difference between "run a porting
    pass and review the result" and "hand-port thousands of statements".
    """
    base, fork = lines_of(base_text), lines_of(fork_text)
    normed_files = {p: [norm(l) for l in ls] for p, ls in files.items()}
    sm = difflib.SequenceMatcher(None, [norm(l) for l in base],
                                 [norm(l) for l in fork], autojunk=False)

    def find_run(module: str, seq: List[str]) -> bool:
        seq = [s for s in seq if s]
        if not seq:
            return False
        hay = normed_files.get(module)
        if hay is None:
            return False
        n = len(seq)
        return any(hay[i:i + n] == seq for i in range(len(hay) - n + 1))

    per_module = defaultdict(lambda: {"hunks": 0, "anchored": 0})
    details = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        module = None
        for i in range(i1, max(i2, i1 + 1)):
            if i < len(owner_map) and owner_map[i]:
                module = owner_map[i]
                break
        if module is None:
            module = owner_map[i1 - 1] if i1 and owner_map[i1 - 1] else None
        pre = [norm(l) for l in base[max(0, i1 - 3):i1]]
        post = [norm(l) for l in base[i2:i2 + 3]]
        anchored = (find_run(module, pre) and find_run(module, post)) if module else False
        per_module[module]["hunks"] += 1
        per_module[module]["anchored"] += int(anchored)
        details.append({"base_range": [i1 + 1, i2], "fork_range": [j1 + 1, j2],
                        "module": module,
                        "pre_context_used": len([p for p in pre if p]),
                        "anchored": anchored})
    total = sum(v["hunks"] for v in per_module.values())
    ok = sum(v["anchored"] for v in per_module.values())
    return {
        "file": name,
        "hunks": total,
        "anchored": ok,
        "needs_hand_port": total - ok,
        "anchored_ratio": round(ok / total, 4) if total else 0.0,
        "per_module": {k: v for k, v in sorted(per_module.items(),
                                               key=lambda kv: -(kv[1]["hunks"] - kv[1]["anchored"]))},
        "detail": details,
    }


def main() -> int:
    upstream_js = {p: lines_of(git_show(f"{UPSTREAM_REF}:{p}"))
                   for p in sum((git_ls(UPSTREAM_REF, d) for d in JS_DIRS), [])}
    upstream_css = {p: lines_of(git_show(f"{UPSTREAM_REF}:{p}"))
                    for p in git_ls(UPSTREAM_REF, CSS_DIR)}

    report = {"base_ref": BASE_REF, "upstream_ref": UPSTREAM_REF,
              "fork_ref": FORK_REF, "files": {}}

    for name, path, idx_src in (
        ("console.js", "channel/web/static/js/console.js", upstream_js),
        ("console.css", "channel/web/static/css/console.css", upstream_css),
    ):
        try:
            base_text = git_show(f"{BASE_REF}:{path}")
            fork_text = git_show(f"{FORK_REF}:{path}")
        except subprocess.CalledProcessError:
            print(f"{name}: not present in one of the refs, skipped")
            continue
        index = build_index(idx_src)
        res = analyse(name, base_text, fork_text, index, set(idx_src))
        owner_map = base_owner_map(lines_of(base_text), index)
        port = plan_port(name, base_text, fork_text, index, idx_src, owner_map)
        res["portability"] = port
        report["files"][name] = res

        print(f"=== {name} ===")
        print(f"  statements    base={res['base_statements']}  fork={res['fork_statements']}"
              f"  similarity={res['similarity']}")
        print(f"  customization {res['hunks']} hunks, +{res['added']} -{res['removed']}")
        print(f"  base lines mapped to an upstream module: "
              f"{res['base_lines'] - res['base_lines_without_anchor']}/{res['base_lines']}"
              f"  across {res['modules_covered']} modules")
        print(f"  per upstream module:")
        for mod, vol in res["per_upstream_module"].items():
            print(f"    {vol['added']:>6}+ {vol['removed']:>6}- "
                  f"{vol['hunks']:>4} hunks  {mod.replace('channel/web/static/', '')}")
        print(f"  portability: {port['anchored']}/{port['hunks']} hunks "
              f"re-anchorable ({port['anchored_ratio']:.1%}), "
              f"{port['needs_hand_port']} need hand porting")
        print(f"  worst modules by hand-port volume:")
        for mod, vol in list(port["per_module"].items())[:8]:
            miss = vol["hunks"] - vol["anchored"]
            if miss:
                print(f"    {miss:>4} hand-port / {vol['hunks']:>4} hunks  "
                      f"{mod.replace('channel/web/static/', '') if mod else '(unowned)'}")
        print()

    json.dump(report, open(os.path.join(WORKDIR, "frontend_divergence.json"), "w"),
              indent=2)
    print(f"report -> {os.path.join(WORKDIR, 'frontend_divergence.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
