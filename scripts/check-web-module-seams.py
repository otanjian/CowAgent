#!/usr/bin/env python3
# encoding:utf-8
"""Structural invariant: upstream web modules carry no fork-only symbol.

Change ``adopt-upstream-web-split``, design D4 / tasks 4.6 + 4.7. The fork's
web customization was moved out of ``channel/web/web_channel.py`` into
``channel/web/fork/**``; this gate is what keeps it out. Without it, the next
hand edit that reaches into an upstream module passes review silently, and the
next upstream sync is a整-file conflict again — the failure mode the whole
change exists to remove.

Two properties are checked:

1. **The upstream module set is explicit, not inferred.** The set below mirrors
   ``channel/web/api/**``, ``channel/web/core/**``, the entry module and its
   README. A module present in the tree but absent from the set is a finding:
   upstream can add a module, and the seam census must be updated when it does.
2. **No fork-only symbol appears in an upstream module.** A definition *or* a
   call counts — a call is how a fork branch would actually reach in.

The judgment basis is the **fork's symbol set**, never a keyword. Upstream uses
``tenant`` as ordinary domain vocabulary throughout, so a keyword criterion
would fire on upstream's own legitimate code (design D4, task 4.7).

The fork symbol set is derived, not hand-listed:

* handler names of the ``fork:*`` rows in ``channel/web/route_registry.py``,
  minus the names upstream also defines (``ChatHandler`` collides — the fork
  serves its own under the same name, and a name collision is not a seam
  violation);
* every top-level name in the fork's authorization and shared-pipeline modules.

Deriving it matters: a hand-maintained list goes stale the moment someone adds
a fork helper, which is exactly when the gate needs to work.

Runs standalone (``python scripts/check-web-module-seams.py``) and against a
copy of the tree (``--root``), so a test can inject a violation and confirm the
gate fails. Exits non-zero on any finding.

An install with no fork package passes: the symbol set is then empty, which is
the "independent upstream form" the spec requires not to be misreported.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Iterable

#: The upstream module set, declared explicitly (design D4). Globs are relative
#: to the web root; ``web_channel.py`` and ``README.md`` are listed as files.
UPSTREAM_ROOTS = ("api", "core")
UPSTREAM_FILES = ("web_channel.py", "README.md")

#: Calls that would plant a fork registration inside an upstream module. Their
#: mere presence is a violation regardless of the symbol set, because that is
#: how a fork branch gets in without naming a fork helper.
FORK_REGISTRATION_MARKERS = ("register_fork_routes", "channel.web.fork",
                             "from channel.web.fork")

#: Names upstream defines and the fork also uses for its own handler. A shared
#: name is not a fork branch; the fork's build_web_app() resolves it from the
#: fork namespace (design D8), so the name says nothing about who owns the body.
NAME_COLLISIONS_ALLOWED = ("ChatHandler",)


def _web_root(root: pathlib.Path) -> pathlib.Path:
    return root / "channel" / "web"


def upstream_modules(root: pathlib.Path) -> list[pathlib.Path]:
    """The declared upstream module set, as existing paths."""
    web = _web_root(root)
    found = [web / name for name in UPSTREAM_FILES if (web / name).is_file()]
    for area in UPSTREAM_ROOTS:
        found.extend(sorted((web / area).rglob("*.py")))
    return sorted(set(found))


def undeclared_modules(root: pathlib.Path) -> list[pathlib.Path]:
    """Modules under an upstream area that the declared set does not cover.

    Today the set is glob-based, so this is empty by construction — which is the
    point: it stops being empty the moment someone changes the globs to a
    hand-written list and forgets a module, and it is the hook a future
    ``--upstream-tree`` comparison would use to notice a module upstream added.
    """
    web = _web_root(root)
    declared = {p.resolve() for p in upstream_modules(root)}
    live = []
    for area in UPSTREAM_ROOTS:
        live.extend((web / area).rglob("*.py"))
    return sorted(p for p in live if p.resolve() not in declared)


def _top_level_names(path: pathlib.Path) -> set[str]:
    """Every name a module defines at top level, or an empty set if unreadable."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _upstream_handler_names(root: pathlib.Path) -> set[str]:
    """Class names defined anywhere in the upstream module set."""
    names: set[str] = set()
    for path in upstream_modules(root):
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    return names


def upstream_defined_names(root: pathlib.Path) -> set[str]:
    """Top-level names defined in the upstream *view and pipeline* modules.

    Subtracted from the fork's names before scanning. The two trees legitimately
    share names — ``_live_channel_manager`` is upstream's in
    ``core/_common.py`` and re-wrapped by the fork, and 64 handler classes
    collide — and a shared name is evidence of *nothing* about who owns a body.
    Keeping it in the set would report upstream's own code as a violation, which
    is the false-positive failure mode task 4.7 exists to prevent.

    The entry module is deliberately **not** consulted here. It is the one place
    where both stacks' names are in scope, so letting it vote on what counts as
    "shared" would let a re-defined body launder itself into the shared set and
    the checkpoint below would never fire.
    """
    names: set[str] = set()
    web = _web_root(root)
    for area in UPSTREAM_ROOTS:
        for path in sorted((web / area).rglob("*.py")):
            names |= _top_level_names(path)
    return names


def _fork_route_handler_names(root: pathlib.Path) -> set[str]:
    """Handler names of the registry's ``fork:*`` rows, read from the source.

    Read from the source rather than by importing the registry so the gate can
    run against a copy of the tree, and so a registry that fails to import is
    reported as "no symbols found" instead of crashing the gate.
    """
    path = _web_root(root) / "route_registry.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    names: set[str] = set()
    for match in re.finditer(
            r'RouteEntry\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"(fork:[^"]*)"',
            text):
        names.add(match.group(2))
    return names


def fork_symbols(root: pathlib.Path) -> set[str]:
    """The fork's own symbols: handlers it routes plus its seam modules' names."""
    web = _web_root(root)
    symbols = _fork_route_handler_names(root)
    for module in ("authorization.py", "common.py", "runtime.py"):
        path = web / "fork" / module
        if path.is_file():
            symbols |= _top_level_names(path)
    handlers = web / "fork" / "handlers"
    if handlers.is_dir():
        for path in sorted(handlers.rglob("*.py")):
            symbols |= _top_level_names(path)
    return symbols - set(NAME_COLLISIONS_ALLOWED) - upstream_defined_names(root)


def _definitions_of(text: str, names: set[str]) -> set[str]:
    """Which of *names* this text defines as a function or class.

    The entry module may import and re-export fork symbols — that is its job
    under D8 — so for it only a *definition* is a violation: a body living in
    the entry module is exactly the "handler implementation outside its view
    module" the spec forbids.
    """
    found = set()
    for name in names:
        if re.search(r"^\s*(?:async\s+)?(?:def|class)\s+%s\b" % re.escape(name),
                     text, flags=re.M):
            found.add(name)
    return found


def scan(root: pathlib.Path, symbols: Iterable[str]):
    """Findings for one tree: ``(path, symbol, reason)`` tuples."""
    symbols = sorted(set(symbols), key=lambda name: (-len(name), name))
    patterns = [(name, re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"
                                  % re.escape(name)))
                for name in symbols]
    symbol_set = set(symbols)
    web = _web_root(root)
    entry = web / "web_channel.py"

    findings = []
    for path in undeclared_modules(root):
        findings.append((path, "-", "upstream-area module not in the declared set"))
    for path in upstream_modules(root):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.resolve() == entry.resolve():
            # Entry module: references are its contract, definitions are not.
            for name in sorted(_definitions_of(text, symbol_set)):
                findings.append((path, name,
                                 "fork-only symbol defined in the entry module"))
            continue
        for marker in FORK_REGISTRATION_MARKERS:
            if marker in text:
                findings.append((path, marker, "fork registration inside an upstream module"))
        for name, pattern in patterns:
            if pattern.search(text):
                findings.append((path, name, "fork-only symbol named in an upstream module"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".",
                        help="repository root (default: the current directory)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    web = _web_root(root)
    if not web.is_dir():
        print("FAIL: no channel/web under %s" % root)
        return 1

    symbols = fork_symbols(root)
    findings = scan(root, symbols)

    if findings:
        print("FAIL: %d finding(s)" % len(findings))
        for path, name, reason in findings:
            try:
                shown = path.relative_to(root)
            except ValueError:
                shown = path
            print("  - %s: %s (%s)" % (shown, name, reason))
        return 1

    if not args.quiet:
        modules = upstream_modules(root)
        print("OK: %d upstream module(s), %d fork-only symbol(s), 0 findings"
              % (len(modules), len(symbols)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
