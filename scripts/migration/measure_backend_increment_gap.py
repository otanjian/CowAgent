"""Measure upstream's handler increments the fork's parallel web layer lacks.

The fork implements 64 upstream handlers in ``channel/web/fork/handlers/**``
rather than calling upstream's ``channel/web/api/**`` (evidence 03: the fork's
authorization is interwoven with the method bodies, so parallelism was the only
route that does not edit upstream files).  A consequence is that upstream's
later changes to those handlers do **not** arrive with a merge -- they have to
be ported by hand.

This measures the size of that obligation: for every handler method upstream
has, compare upstream's body against the merge base's body (the monolith the
fork forked from).  A method whose upstream body differs from the base body has
carried an upstream change since the fork point; if the fork's parallel body
does not contain it, it is an unported increment.

    python scripts/migration/measure_backend_increment_gap.py \
        [--base <sha>] [--upstream upstream/master] [--fg upstream]
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from typing import Dict, List, Optional

BASE_DEFAULT = "e5e2a52d"
UPSTREAM_DEFAULT = "upstream/master"
API_DIR = "channel/web/api"
FORK_DIR = "channel/web/fork/handlers"


def git_show(rev: str, path: str) -> Optional[str]:
    out = subprocess.run(["git", "show", f"{rev}:{path}"],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def git_ls(rev: str, path: str) -> List[str]:
    out = subprocess.run(["git", "ls-tree", "-r", rev, "--name-only", "--", path],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.endswith(".py")]


def methods(source: str) -> Dict[str, str]:
    """``{"Class.method": exact source text}`` for every method in a module."""
    tree = ast.parse(source)
    found: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                piece = ast.get_source_segment(source, item)
                if piece:
                    found[f"{node.name}.{item.name}"] = piece
    return found


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument("--upstream", default=UPSTREAM_DEFAULT)
    ap.add_argument("--fork-dir", default=FORK_DIR)
    args = ap.parse_args()

    base_src = git_show(args.base, "channel/web/web_channel.py")
    if base_src is None:
        print(f"merge base {args.base} has no web_channel.py", file=sys.stderr)
        return 2
    base_methods = methods(base_src)

    # The fork's parallel handlers, keyed by class.method across its modules.
    fork_methods: Dict[str, str] = {}
    for path in git_ls("HEAD", args.fork_dir):
        src = git_show("HEAD", path)
        if src:
            fork_methods.update(methods(src))

    changed, missing, unported = [], [], []
    added_lines = 0
    for path in git_ls(args.upstream, API_DIR):
        src = git_show(args.upstream, path)
        if not src:
            continue
        for name, body in methods(src).items():
            base_body = base_methods.get(name)
            if base_body is None:
                missing.append((path, name))
                continue
            if normalize(body) == normalize(base_body):
                continue  # upstream did not touch this method since the fork point
            changed.append((path, name))
            delta = sum(1 for line in normalize(body).splitlines()
                        if line not in normalize(base_body).splitlines())
            added_lines += delta
            fork_body = fork_methods.get(name)
            if fork_body is None:
                unported.append((path, name, delta, "no parallel implementation"))
            elif not _contains(fork_body, body, base_body):
                unported.append((path, name, delta, "upstream change absent"))

    print(f"upstream handler methods:            {len(changed) + len(missing)}")
    print(f"  new since the fork point:          {len(missing)}")
    print(f"  changed since the fork point:      {len(changed)}")
    print(f"  of those, unported into the fork:  {len(unported)}")
    print(f"  upstream lines not seen in fork:   {added_lines}")
    print()
    print("unported (path, method, new lines, why):")
    for path, name, delta, why in sorted(unported, key=lambda r: -r[2]):
        print(f"  {delta:4d}  {path:34s} {name:38s} {why}")
    return 0


def _contains(fork_body: str, upstream_body: str, base_body: str) -> bool:
    """True when the fork's body already carries upstream's change.

    Upstream's added lines (relative to the base) are the increment; if the
    fork's body already contains them all, nothing was dropped.
    """
    base_lines = set(normalize(base_body).splitlines())
    new_upstream = [l for l in normalize(upstream_body).splitlines()
                    if l not in base_lines]
    fork_lines = set(normalize(fork_body).splitlines())
    return all(l in fork_lines for l in new_upstream)


if __name__ == "__main__":
    sys.exit(main())
