#!/usr/bin/env python3
"""Per-handler fork-helper usage analysis (change tasks 2.3/2.5 scoping).

For every handler class in the fork monolith, list which fork-only helpers it
calls, so we know (a) which handlers are fork-only and move wholesale, and
(b) which UPSTREAM handlers call fork authorization and therefore need a fork
wrapper subclass in the new layout.
"""
from __future__ import annotations
import os

import ast
import json
import subprocess
import sys
from typing import Dict, Set, List

import importlib.util

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)

spec = importlib.util.spec_from_file_location(
    "afw", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "analyze_fork_web_symbols.py")
)
afw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afw)


def handler_method_calls(source: str) -> Dict[str, Set[str]]:
    """handler class name -> set of global names its method bodies load."""
    tree = ast.parse(source)
    out: Dict[str, Set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Handler"):
            used: Set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    used |= afw.referenced_globals(stmt)
            out[node.name] = used
    return out


def main() -> int:
    fork_src = open("channel/web/web_channel.py", encoding="utf-8").read()
    fork_syms = afw.module_level_symbols(fork_src)
    fork_imports = afw.imported_names(fork_src)
    fork_handlers = afw.handler_names(fork_src)

    up_handlers: Set[str] = set()
    up_helpers: Set[str] = set()
    for path in subprocess.run(
        ["git", "ls-tree", "-r", "origin/master", "--name-only", "--",
         "channel/web/api/", "channel/web/core/"],
        capture_output=True, text=True, check=True,
    ).stdout.split():
        src = afw.git_show(f"origin/master:{path}")
        up_handlers |= afw.handler_names(src)
        up_helpers |= set(afw.module_level_symbols(src))
    base_src = afw.git_show("e5e2a52d:channel/web/web_channel.py")
    up_handlers |= afw.handler_names(base_src)
    up_helpers |= set(afw.module_level_symbols(base_src))

    fork_only = {
        s for s in fork_syms
        if s not in up_helpers and not s.startswith("__")
        and isinstance(fork_syms[s], (ast.FunctionDef, ast.ClassDef))
    }

    calls = handler_method_calls(fork_src)

    fork_only_handlers = sorted(h for h in fork_handlers if h not in up_handlers)
    upstream_handlers = sorted(h for h in fork_handlers if h in up_handlers)

    print(f"fork-only handlers   : {len(fork_only_handlers)}")
    print(f"upstream handlers    : {len(upstream_handlers)}")
    print()

    needed: List[tuple] = []
    clean: List[str] = []
    for h in upstream_handlers:
        deps = sorted(calls.get(h, set()) & fork_only)
        if deps:
            needed.append((h, deps))
        else:
            clean.append(h)

    print(f"== UPSTREAM handlers that call fork-only helpers (need a fork wrapper): {len(needed)} ==")
    for h, deps in sorted(needed, key=lambda kv: -len(kv[1])):
        print(f"  {h:<44} {len(deps)} fork deps: {', '.join(deps[:8])}"
              + (" ..." if len(deps) > 8 else ""))
    print()
    print(f"== upstream handlers with no fork-only helper calls: {len(clean)} ==")
    for h in clean:
        print(f"  {h}")
    print()
    print("== fork-only handlers and their fork deps ==")
    for h in fork_only_handlers:
        deps = sorted(calls.get(h, set()) & fork_only)
        print(f"  {h:<44} {len(deps)} deps")

    json.dump(
        {
            "fork_only_handlers": fork_only_handlers,
            "upstream_handlers_needing_wrapper": {h: d for h, d in needed},
            "upstream_handlers_clean": clean,
            "fork_only_symbols": sorted(fork_only),
        },
        open(_work("handler_wrapper_map.json"), "w"), indent=2,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
