#!/usr/bin/env python3
"""Map fork-only symbols in web_channel.py and their dependencies (change task 2.1).

Reads the fork working tree plus the pinned merge-base and upstream trees via
``git show``, so the fork/upstream classification is grounded in the same SHAs
the sync pins rather than in guesswork.
"""
from __future__ import annotations
import os

import ast
import json
import subprocess
import sys
from typing import Dict, Set, List

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)


def git_show(rev_path: str) -> str:
    return subprocess.run(
        ["git", "show", rev_path], capture_output=True, text=True, check=True
    ).stdout


def module_level_symbols(source: str) -> Dict[str, object]:
    """name -> ast node for every module-level def/class, plus assigned names."""
    tree = ast.parse(source)
    out: Dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, node)
    return out


def imported_names(source: str) -> Set[str]:
    tree = ast.parse(source)
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def referenced_globals(node: ast.AST) -> Set[str]:
    """Names loaded inside a def/class body, including nested scopes."""
    used: Set[str] = set()
    local: Set[str] = set()

    class V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            if isinstance(n.ctx, ast.Load):
                used.add(n.id)
            elif isinstance(n.ctx, (ast.Store, ast.Del)):
                local.add(n.id)

        def visit_arg(self, n: ast.arg):
            local.add(n.arg)

        def visit_FunctionDef(self, n: ast.FunctionDef):
            local.add(n.name)
            for d in n.decorator_list:
                self.visit(d)
            for default in n.args.defaults + [d for d in n.args.kw_defaults if d]:
                self.visit(default)
            for b in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                local.add(b.arg)
            if n.args.vararg:
                local.add(n.args.vararg.arg)
            if n.args.kwarg:
                local.add(n.args.kwarg.arg)
            for stmt in n.body:
                self.visit(stmt)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n: ast.ClassDef):
            local.add(n.name)
            for d in n.decorator_list:
                self.visit(d)
            for b in n.bases:
                self.visit(b)
            for stmt in n.body:
                self.visit(stmt)

        def visit_Lambda(self, n: ast.Lambda):
            for default in n.args.defaults + [d for d in n.args.kw_defaults if d]:
                self.visit(default)
            for b in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                local.add(b.arg)
            if n.args.vararg:
                local.add(n.args.vararg.arg)
            if n.args.kwarg:
                local.add(n.args.kwarg.arg)
            self.visit(n.body)

        def visit_comprehension(self, n: ast.comprehension):
            self.visit(n.iter)
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    local.add(t.id)

    V().visit(node)
    return used - local


def handler_names(source: str) -> Set[str]:
    return {
        n.name for n in ast.parse(source).body
        if isinstance(n, ast.ClassDef) and n.name.endswith("Handler")
    }


def main() -> int:
    fork_src = open("channel/web/web_channel.py", encoding="utf-8").read()
    fork = module_level_symbols(fork_src)
    fork_imports = imported_names(fork_src)
    fork_handlers = handler_names(fork_src)

    # Upstream handler names, from the current split modules and the merge-base monolith.
    up_handlers: Set[str] = set()
    for path in subprocess.run(
        ["git", "ls-tree", "-r", "origin/master", "--name-only", "--", "channel/web/api/"],
        capture_output=True, text=True, check=True,
    ).stdout.split():
        up_handlers |= handler_names(git_show(f"origin/master:{path}"))
    up_handlers |= handler_names(git_show("e5e2a52d:channel/web/web_channel.py"))

    # Upstream helper names available today (api/ + core/), to tell fork helpers from upstream ones.
    up_helpers: Set[str] = set()
    for path in subprocess.run(
        ["git", "ls-tree", "-r", "origin/master", "--name-only", "--",
         "channel/web/api/", "channel/web/core/"],
        capture_output=True, text=True, check=True,
    ).stdout.split():
        up_helpers |= set(module_level_symbols(git_show(f"origin/master:{path}")))
    up_helpers |= set(module_level_symbols(git_show("e5e2a52d:channel/web/web_channel.py")))

    fork_only_handlers = sorted(fork_handlers - up_handlers)
    fork_only_syms = sorted(
        s for s in fork
        if s not in up_helpers and not s.startswith("__")
        and isinstance(fork[s], (ast.FunctionDef, ast.ClassDef))
    )

    # Dependency edges among fork-only symbols.
    edges: Dict[str, List[str]] = {}
    for name in fork_only_syms:
        refs = referenced_globals(fork[name])
        edges[name] = sorted(r for r in refs if r in fork_only_syms)

    # What fork-only symbols need that is neither fork-only nor an import: stays in
    # web_channel (or is a module constant that must travel with them).
    residual: Dict[str, List[str]] = {}
    for name in fork_only_syms:
        refs = referenced_globals(fork[name])
        residual[name] = sorted(
            r for r in refs
            if r not in fork_only_syms and r not in fork_imports
            and r in fork  # defined at module level in web_channel
        )

    constants = sorted(
        s for s in fork
        if not isinstance(fork[s], (ast.FunctionDef, ast.ClassDef))
        and not s.startswith("__")
    )

    print(f"fork module-level symbols : {len(fork)}")
    print(f"fork handler classes      : {len(fork_handlers)}")
    print(f"upstream handler classes  : {len(up_handlers)}")
    print(f"FORK-ONLY handlers        : {len(fork_only_handlers)}")
    print(f"FORK-ONLY other symbols   : {len(fork_only_syms)}")
    print()
    print("== fork-only handlers ==")
    for h in fork_only_handlers:
        print(f"  {h}")
    print()
    print(f"== fork-only non-handler symbols: {len(fork_only_syms)} ==")
    for s in fork_only_syms[:400]:
        kind = "class" if isinstance(fork[s], ast.ClassDef) else "def"
        line = getattr(fork[s], "lineno", "?")
        print(f"  L{line:<6} {kind:<5} {s}  -> deps: {len(edges[s])}")
    print()
    print("== module constants referenced by fork-only symbols ==")
    referenced_consts: Set[str] = set()
    for name in fork_only_syms:
        referenced_consts |= {r for r in referenced_globals(fork[name]) if r in constants}
    for c in sorted(referenced_consts):
        line = getattr(fork[c], "lineno", "?")
        print(f"  L{line:<6} {c}")
    print()
    print("== symbols with widest dependency fan-out (extraction risk) ==")
    for name in sorted(edges, key=lambda n: -len(edges[n]))[:20]:
        print(f"  {name:<44} {len(edges[name])} fork deps")
    print()
    n_residual = sum(1 for v in residual.values() if v)
    print(f"== fork-only symbols depending on non-fork web_channel symbols: {n_residual} ==")
    seen: Dict[str, int] = {}
    for name, refs in residual.items():
        for r in refs:
            seen[r] = seen.get(r, 0) + 1
    for r, c in sorted(seen.items(), key=lambda kv: -kv[1])[:25]:
        kind = "class" if isinstance(fork.get(r), ast.ClassDef) else "def/const"
        print(f"  {r:<44} referenced by {c} fork symbols  ({kind})")

    json.dump(
        {
            "fork_only_handlers": fork_only_handlers,
            "fork_only_symbols": fork_only_syms,
            "edges": edges,
            "residual": {k: v for k, v in residual.items() if v},
            "constants": sorted(referenced_consts),
        },
        open(sys.argv[1] if len(sys.argv) > 1 else _work("fork_symbol_map.json"), "w"),
        indent=2,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
