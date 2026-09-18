#!/usr/bin/env python3
"""Build the adjudication worklist for frontend regions both sides changed.

``port_frontend.py`` ports every fork change whose landing site in upstream's
split modules is unambiguous, and defers the rest. Those deferred regions are
where *both* sides changed the same code -- the fork's console edit and upstream's
refactor collide, so no mechanical rule can be right:

  * taking the fork's text silently drops upstream's change (possibly a fix or a
    security fix), and
  * taking upstream's text silently drops the fork's customization.

Auto-transplanting the fork's enclosing function is therefore explicitly rejected
even though it would mechanically apply to 56 of 87 deferred JS clusters. The
point of this worklist is to make each collision reviewable, with both sides and
the enclosing symbol named, so the decision is recorded rather than guessed.

Emits ``frontend_adjudication.json`` (machine-readable) and
``frontend_adjudication.md`` (reviewable), grouped by upstream module.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_frontend_divergence import git_ls, git_show, lines_of  # noqa: E402

WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")
FORK_REF = os.environ.get("FORK_FORK_REF", "HEAD")
UPSTREAM_REF = os.environ.get("FORK_UPSTREAM_REF", "origin/master")

JS_DIRS = ("channel/web/static/js/core", "channel/web/static/js/chat",
           "channel/web/static/js/views")
CSS_DIR = "channel/web/static/css"

DECL = re.compile(
    r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()"
    r"|^\s*([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()")

UPSTREAM_WINDOW = 45  # lines of upstream context shown from the symbol's start


def enclosing_symbol(lines: List[str], lineno: int) -> Optional[str]:
    for i in range(lineno, max(0, lineno - 500), -1):
        m = DECL.match(lines[i])
        if m:
            return m.group(1) or m.group(2) or m.group(3)
    return None


def main() -> int:
    cl_path = os.path.join(WORKDIR, "port_frontend_clusters.json")
    if not os.path.isfile(cl_path):
        print(f"no cluster report at {cl_path}; run port_frontend.py first")
        return 2
    with open(cl_path, encoding="utf-8") as f:
        clusters = json.load(f)

    upstream = {}
    for p in sum((git_ls(UPSTREAM_REF, d) for d in JS_DIRS), []) + \
            git_ls(UPSTREAM_REF, CSS_DIR):
        upstream[p] = lines_of(git_show(f"{UPSTREAM_REF}:{p}"))

    forks = {
        "console.js": lines_of(git_show(f"{FORK_REF}:channel/web/static/js/console.js")),
        "console.css": lines_of(git_show(f"{FORK_REF}:channel/web/static/css/console.css")),
    }

    items: List[dict] = []
    for c in clusters:
        if c["status"] != "skipped":
            continue
        src = c["source"]
        fork = forks[src]
        j1, j2 = c["fork_range"]
        region = fork[j1 - 1:j2]
        symbol = enclosing_symbol(fork, j1 - 1)
        up_body, up_path = None, c.get("module")
        if up_path and up_path in upstream:
            for i, line in enumerate(upstream[up_path]):
                m = DECL.match(line)
                if m and (m.group(1) or m.group(2) or m.group(3)) == symbol:
                    up_body = upstream[up_path][i:i + UPSTREAM_WINDOW]
                    break
        items.append({
            "source": src,
            "upstream_module": up_path,
            "base_range": c["base_range"],
            "fork_range": c["fork_range"],
            "reason": c["reason"],
            "symbol": symbol,
            "fork_region": region,
            "upstream_symbol_excerpt": up_body,
            "decision": None,   # to be filled in: fork | upstream | merged
            "rationale": None,
        })

    json.dump(items, open(os.path.join(WORKDIR, "frontend_adjudication.json"), "w"),
              indent=2)

    by_module: Dict[str, List[dict]] = defaultdict(list)
    for it in items:
        by_module[it["upstream_module"] or "(no owning module)"].append(it)

    md = ["# Frontend adjudication worklist",
          "",
          f"{len(items)} regions where the fork's console edit and upstream's refactor",
          "collide. For each: choose `fork`, `upstream`, or `merged`, and record why.",
          "Auto-transplanting the fork's function is not used here on purpose: it would",
          "silently drop upstream's change to the same code.",
          ""]
    for module in sorted(by_module, key=lambda m: -len(by_module[m])):
        group = by_module[module]
        md.append(f"## `{module or '(no owning module)'}` — {len(group)} region(s)")
        md.append("")
        for it in group:
            md.append(f"### symbol `{it['symbol'] or '?'}` "
                      f"(console lines {it['fork_range'][0]}–{it['fork_range'][1]}, "
                      f"base lines {it['base_range'][0]}–{it['base_range'][1]})")
            md.append("")
            md.append(f"- reason deferred: {it['reason']}")
            md.append(f"- decision: `{it['decision'] or 'TBD'}`")
            md.append("")
            md.append("<details><summary>fork's version</summary>")
            md.append("")
            md.append("```javascript")
            md.extend(it["fork_region"][:40])
            md.append("```")
            md.append("</details>")
            md.append("")
            if it["upstream_symbol_excerpt"]:
                md.append("<details><summary>upstream's version of "
                          f"`{it['symbol']}`</summary>")
                md.append("")
                md.append("```javascript")
                md.extend(it["upstream_symbol_excerpt"])
                md.append("```")
                md.append("</details>")
                md.append("")

    md_path = os.path.join(WORKDIR, "frontend_adjudication.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(f"{len(items)} regions to adjudicate across {len(by_module)} modules")
    for module in sorted(by_module, key=lambda m: -len(by_module[m]))[:12]:
        with_upstream = sum(1 for i in by_module[module] if i["upstream_symbol_excerpt"])
        print(f"  {len(by_module[module]):>3} regions ({with_upstream:>3} with upstream "
              f"side shown)  {(module or '(none)').replace('channel/web/static/', '')}")
    print(f"\njson -> {os.path.join(WORKDIR, 'frontend_adjudication.json')}")
    print(f"md   -> {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
