#!/usr/bin/env python3
"""Characterise how far fork handler bodies diverge from upstream (change design input).

For each shared handler class, compare the fork's method bodies against
origin/master's, so we can tell a thin authorization seam from a fork-owned
reimplementation.
"""
from __future__ import annotations
import os

import ast
import difflib
import json
import subprocess
from typing import Dict, Optional

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)


def class_sources(source: str) -> Dict[str, str]:
    """class name -> exact source text of the class."""
    tree = ast.parse(source)
    lines = source.splitlines()
    out: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Handler"):
            start = node.lineno - 1
            end = node.end_lineno
            out[node.name] = "\n".join(lines[start:end])
    return out


def upstream_handler_sources() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path in subprocess.run(
        ["git", "ls-tree", "-r", "origin/master", "--name-only", "--",
         "channel/web/api/", "channel/web/core/"],
        capture_output=True, text=True, check=True,
    ).stdout.split():
        src = subprocess.run(
            ["git", "show", f"origin/master:{path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        out.update(class_sources(src))
    base = subprocess.run(
        ["git", "show", "e5e2a52d:channel/web/web_channel.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    base_classes = class_sources(base)
    for k, v in base_classes.items():
        out.setdefault(k, v)
    return out, base_classes


def main() -> int:
    fork_src = open("channel/web/web_channel.py", encoding="utf-8").read()
    fork_classes = class_sources(fork_src)
    up_classes, base_classes = upstream_handler_sources()

    rows = []
    for name, fork_body in sorted(fork_classes.items()):
        up_body = up_classes.get(name)
        base_body = base_classes.get(name)
        if up_body is None:
            rows.append({
                "handler": name, "kind": "fork-only", "fork_lines": len(fork_body.splitlines()),
                "up_lines": 0, "base_lines": len(base_body.splitlines()) if base_body else 0,
                "ratio_vs_upstream": None, "ratio_vs_base": None,
            })
            continue
        fl = fork_body.splitlines()
        ul = up_body.splitlines()
        ratio = difflib.SequenceMatcher(None, fl, ul).quick_ratio()
        bratio = None
        if base_body is not None:
            bratio = difflib.SequenceMatcher(None, fl, base_body.splitlines()).quick_ratio()
        rows.append({
            "handler": name, "kind": "shared", "fork_lines": len(fl), "up_lines": len(ul),
            "base_lines": len(base_body.splitlines()) if base_body else 0,
            "ratio_vs_upstream": round(ratio, 3),
            "ratio_vs_base": round(bratio, 3) if bratio is not None else None,
        })

    print("=== divergence of the FORK handler body vs UPSTREAM (1.0 = identical) ===")
    print(f"{'handler':<44}{'fork':>6}{'up':>6}{'base':>6}{'~up':>7}{'~base':>7}")
    for r in sorted(rows, key=lambda r: (r["ratio_vs_upstream"] is None,
                                         -(r["ratio_vs_upstream"] or 0))):
        ru = f"{r['ratio_vs_upstream']:.3f}" if r["ratio_vs_upstream"] is not None else "  n/a"
        rb = f"{r['ratio_vs_base']:.3f}" if r["ratio_vs_base"] is not None else "  n/a"
        print(f"{r['handler']:<44}{r['fork_lines']:>6}{r['up_lines']:>6}{r['base_lines']:>6}{ru:>7}{rb:>7}")

    shared = [r for r in rows if r["kind"] == "shared"]
    near = [r for r in shared if (r["ratio_vs_upstream"] or 0) >= 0.85]
    mid = [r for r in shared if 0.6 <= (r["ratio_vs_upstream"] or 0) < 0.85]
    far = [r for r in shared if (r["ratio_vs_upstream"] or 0) < 0.6]
    print()
    print(f"shared handlers: {len(shared)}")
    print(f"  near-identical to upstream (>=0.85): {len(near)}  -> thin seam is plausible")
    print(f"  moderately diverged (0.6-0.85)    : {len(mid)}")
    print(f"  largely rewritten (<0.6)          : {len(far)}  -> fork owns the body")
    print()
    print("=== largely rewritten (<0.6) ===")
    for r in sorted(far, key=lambda r: r["ratio_vs_upstream"]):
        print(f"  {r['ratio_vs_upstream']:.3f}  {r['handler']}  (fork {r['fork_lines']} vs up {r['up_lines']})")
    print()
    print("=== near-identical (>=0.85) ===")
    for r in sorted(near, key=lambda r: -r["ratio_vs_upstream"]):
        print(f"  {r['ratio_vs_upstream']:.3f}  {r['handler']}  (fork {r['fork_lines']} vs up {r['up_lines']})")

    json.dump(rows, open(_work("handler_divergence.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
