#!/usr/bin/env python3
"""Emit the fork web package from the fork monolith (change tasks 2.2-2.6).

Two properties matter and drive this design:

1. **Verbatim** - every symbol's source is sliced from the original file by its
   AST line range, so behaviour is preserved by construction.
2. **Import-safe** - the fork web layer is one tightly coupled cluster (helpers
   call each other across view boundaries), so naive module-level imports form
   cycles. Edges *inside* an SCC are emitted as function-local imports instead,
   which keeps per-view modules without changing behaviour.

Entry module (channel/web/web_channel.py) is rewritten to the upstream shape:
URL table + build_web_app, importing every handler so web.py's ``globals()``
lookup and ``check_route_coverage(vars(web_channel))`` keep working.
"""
from __future__ import annotations

import ast
import builtins
import json
import os
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Set, Tuple

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)

sys.setrecursionlimit(10000)

SRC = "channel/web/web_channel.py"
SOURCE_REF = os.environ.get("FORK_SOURCE_REF", "rdai")  # pre-migration monolith
FORK_PKG = "channel/web/fork"
ENTRY_KEEP = {"_WEB_URLS", "build_web_app"}
BUILTINS: Set[str] = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "self", "cls",
}


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def parse_imports(source: str):
    per_name: Dict[str, str] = {}
    stars: List[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            stmt = ast.get_source_segment(source, node)
            for a in node.names:
                per_name[a.asname or a.name.split(".")[0]] = stmt
        elif isinstance(node, ast.ImportFrom):
            stmt = ast.get_source_segment(source, node)
            for a in node.names:
                if a.name == "*":
                    stars.append(stmt)
                else:
                    per_name[a.asname or a.name] = stmt
    return per_name, stars, set(per_name)


def node_span(node) -> Tuple[int, int]:
    """1-based (start, end) line span, INCLUDING any decorators.

    ``node.lineno`` points at the ``def``/``class`` line, so slicing from it
    silently drops ``@contextmanager`` / ``@singleton`` / ``@dataclass`` and
    changes behaviour.
    """
    starts = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
    return min(starts), node.end_lineno


def compute_hub_surface(entry_symbols: Set[str]) -> Set[str]:
    """Names the codebase already resolves through ``channel.web.web_channel``.

    Other modules (scenes, admin_overview, auth/service, project_import, ...) and
    the tests lazily import or monkeypatch shared console helpers *through the
    entry module*. That makes the entry module this codebase's established
    internal seam, so fork modules must resolve these names through it too --
    otherwise a patch of ``web_channel.<name>`` stops intercepting the code that
    uses it.

    Anything referenced as ``web_channel.<name>`` or imported from
    ``channel.web.web_channel`` outside the fork package counts as hub surface.
    """
    import re
    pat_attr = re.compile(r'\bweb_channel\.([A-Za-z_]\w*)')
    pat_from = re.compile(r'from\s+channel\.web\.web_channel\s+import\s+\(?([^)\n]+)')
    # ``patch.object(web_channel, "x")`` / ``setattr(web_channel, "x", ...)``:
    # the name is a string argument, not an attribute access, so the two
    # patterns above miss it. This is how most of the suite stubs console
    # helpers, and a missed name silently stops being patchable.
    pat_arg = re.compile(r'\bweb_channel\s*,\s*["\']([A-Za-z_]\w*)["\']')
    # Deliberately conservative: the target may be a loop variable that is
    # sometimes ``web_channel`` (e.g. ``for target in (config, web_channel)``).
    # Treating any ``patch.object(<obj>, "x")`` / ``setattr(<obj>, "x"`` name as
    # hub surface can only route a call through the entry module -- which is
    # exactly what the monolith did -- so a false positive costs a lookup and
    # never changes which implementation wins. Same-module references stay
    # local, so no shadowing is introduced.
    pat_obj = re.compile(
        r'\b(?:patch\.object|setattr)\s*\(\s*[A-Za-z_][\w.]*\s*,\s*["\']([A-Za-z_]\w*)["\']')
    found: Set[str] = set()
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in
                   {".git", ".venv", "node_modules", "__pycache__", "channel"}]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            try:
                src = open(p, encoding="utf-8").read()
            except OSError:
                continue
            found.update(pat_attr.findall(src))
            found.update(pat_arg.findall(src))
            found.update(pat_obj.findall(src))
            for grp in pat_from.findall(src):
                for part in grp.split(","):
                    part = part.strip()
                    if " as " in part:
                        part = part.split(" as ")[0].strip()
                    if part.isidentifier():
                        found.add(part)
    # also scan channel/ (production siblings like admin_handlers, memory_console)
    for root, dirs, files in os.walk("channel"):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        if "fork" in root.split(os.sep):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            if os.path.abspath(p) == os.path.abspath(SRC):
                continue
            src = open(p, encoding="utf-8").read()
            found.update(pat_attr.findall(src))
            found.update(pat_arg.findall(src))
            found.update(pat_obj.findall(src))
            for grp in pat_from.findall(src):
                for part in grp.split(","):
                    part = part.strip()
                    if " as " in part:
                        part = part.split(" as ")[0].strip()
                    if part.isidentifier():
                        found.add(part)
    found |= entry_symbols
    return found


def module_level(source: str):
    syms = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            syms[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    syms.setdefault(t.id, node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            syms.setdefault(node.target.id, node)
    return syms


def free_names(node) -> Set[str]:
    used: Set[str] = set()
    bound: Set[str] = set()

    class V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            (used if isinstance(n.ctx, ast.Load) else bound).add(n.id)

        def visit_arg(self, n: ast.arg):
            bound.add(n.arg)

        def visit_ExceptHandler(self, n: ast.ExceptHandler):
            if n.name:
                bound.add(n.name)
            if n.type:
                self.visit(n.type)
            for stmt in n.body:
                self.visit(stmt)

        def visit_With(self, n: ast.With):
            for item in n.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self.visit(item.optional_vars)
            for stmt in n.body:
                self.visit(stmt)

        visit_AsyncWith = visit_With

        def visit_Global(self, n: ast.Global):
            bound.update(n.names)

        def visit_Nonlocal(self, n: ast.Nonlocal):
            bound.update(n.names)

        def _fn(self, n):
            bound.add(n.name)
            for d in n.decorator_list:
                self.visit(d)
            a = n.args
            for default in list(a.defaults) + [x for x in a.kw_defaults if x]:
                self.visit(default)
            for b in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                bound.add(b.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
            for stmt in n.body:
                self.visit(stmt)

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

        def visit_ClassDef(self, n: ast.ClassDef):
            bound.add(n.name)
            for d in n.decorator_list:
                self.visit(d)
            for b in n.bases:
                self.visit(b)
            for k in n.keywords:
                self.visit(k.value)
            for stmt in n.body:
                self.visit(stmt)

        def visit_Lambda(self, n: ast.Lambda):
            a = n.args
            for default in list(a.defaults) + [x for x in a.kw_defaults if x]:
                self.visit(default)
            for b in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                bound.add(b.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
            self.visit(n.body)

        def visit_comprehension(self, n: ast.comprehension):
            self.visit(n.iter)
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)

    V().visit(node)
    return used - bound


def first_referencing_bodies(node):
    """Bodies that need a lazy import: the node itself, or each of its methods.

    A bare name is not visible through class scope, so classes get one insertion
    per method that references the name.
    """
    if isinstance(node, ast.ClassDef):
        out = [s for s in node.body
               if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return out
    return [node]


def insert_before_first_statement(lines: List[str], node, stmts: List[str]) -> int:
    """Index at which to insert stmts so they run first in node's body."""
    target = None
    for st in node.body:
        if (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                and isinstance(st.value.value, str)):
            continue
        target = st
        break
    if target is None:
        target = node.body[-1]
    return target.lineno - 1, " " * target.col_offset


# --------------------------------------------------------------------------- #
def main() -> int:
    dom = json.load(open(_work("fork_domain_map.json")))
    handler_domain: Dict[str, str] = dom["handler_domain"]
    helper_domain: Dict[str, str] = dom["helper_domain"]

    source = subprocess.run(
        ["git", "show", f"{SOURCE_REF}:{SRC}"],
        capture_output=True, text=True, check=True,
    ).stdout
    lines = source.splitlines()
    per_name_import, star_imports, _ = parse_imports(source)
    syms = module_level(source)

    domain_of: Dict[str, str] = {}
    for name in syms:
        if name in ENTRY_KEEP:
            continue
        if name in handler_domain:
            domain_of[name] = handler_domain[name]
        elif name in helper_domain:
            domain_of[name] = helper_domain[name]
        else:
            domain_of[name] = "runtime"

    def module_path(name: str) -> str:
        d = domain_of[name]
        if d in ("authorization", "common", "runtime"):
            return f"{FORK_PKG}/{d}.py"
        return f"{FORK_PKG}/handlers/{d}.py"

    mod_of = {n: module_path(n) for n in domain_of}
    by_mod: Dict[str, List[str]] = defaultdict(list)
    for name in domain_of:
        by_mod[mod_of[name]].append(name)
    for m in by_mod:
        by_mod[m].sort(key=lambda n: (syms[n].lineno,))
    mod_names = {m: set(v) for m, v in by_mod.items()}

    def dotted(mod: str) -> str:
        return mod.replace("/", ".").removesuffix(".py")

    union_names = set(mod_of) | set(per_name_import) | ENTRY_KEEP
    module_import_names = {
        a.asname or a.name.split(".")[0]
        for node in ast.parse(source).body if isinstance(node, ast.Import)
        for a in node.names
    }
    HUB_SURFACE = compute_hub_surface(set(ENTRY_KEEP)) - module_import_names
    HUB = dotted(SRC)

    # ---- dependency edges -------------------------------------------------
    edges: Dict[str, Set[str]] = defaultdict(set)
    needed_by_mod: Dict[str, Set[str]] = {}
    for mod, names in by_mod.items():
        need: Set[str] = set()
        for n in names:
            need |= free_names(syms[n])
        need -= BUILTINS
        # A same-module helper needs no import -- unless it is hub surface, in
        # which case the reference must still go through the entry module so
        # patches of ``web_channel.<name>`` keep intercepting it.
        need = {nm for nm in need
                if nm not in mod_names[mod] or nm in HUB_SURFACE}
        needed_by_mod[mod] = need
        for nm in need:
            if nm in mod_of and mod_of[nm] != mod:
                edges[mod].add(mod_of[nm])
    # ---- SCCs (Tarjan) ----------------------------------------------------
    index, low, on, stack, counter, sccs = {}, {}, {}, [], [0], []

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on[v] = True
        for w in edges[v]:
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif on.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on[w] = False
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in list(by_mod):
        if v not in index:
            strong(v)
    scc_of = {m: i for i, c in enumerate(sccs) for m in c}
    cyclic = {m for c in sccs if len(c) > 1 for m in c}

    written, unresolved, lazy_report = [], {}, {}

    for mod, names in sorted(by_mod.items()):
        top_imports: List[str] = []
        lazy: Dict[str, List[str]] = defaultdict(list)   # name -> modules it comes from
        external: Dict[str, str] = {}

        for nm in sorted(needed_by_mod[mod]):
            if nm in HUB_SURFACE and nm in union_names:
                # The entry module is this codebase's established internal seam:
                # other modules and the tests resolve these helpers through it.
                # Going through it (lazily) keeps one resolution point, keeps
                # monkeypatching effective, and avoids import cycles.
                lazy[HUB].append(nm)
            elif nm in mod_of:
                lazy[HUB].append(nm)
            elif nm in mod_names[mod]:
                continue
            elif nm in per_name_import:
                external[nm] = per_name_import[nm]
            elif star_imports:
                continue
            else:
                unresolved.setdefault(mod, []).append(nm)

        for stmt in sorted(set(external.values())):
            top_imports.append(stmt)
        top_imports.extend(star_imports)

        # ---- build the module body, tracking offsets for insertion --------
        body_lines: List[str] = []
        sym_base: Dict[str, int] = {}
        sym_start: Dict[str, int] = {}
        for n in names:
            node = syms[n]
            start, end = node_span(node)
            sym_base[n] = len(body_lines)
            sym_start[n] = start
            body_lines.extend(lines[start - 1:end])
            body_lines.append("")
            body_lines.append("")

        # group lazy imports per insertion point inside the reassembled body
        per_point: Dict[int, Tuple[str, Set[str]]] = {}
        for dep_mod, dep_names in sorted(lazy.items()):
            for nm in sorted(set(dep_names)):
                for owner in names:
                    node = syms[owner]
                    if nm not in free_names(node):
                        continue
                    for body in first_referencing_bodies(node):
                        if nm not in free_names(body):
                            continue
                        # land after any docstring, at the statement's indent
                        target = None
                        for st in body.body:
                            if (isinstance(st, ast.Expr)
                                    and isinstance(st.value, ast.Constant)
                                    and isinstance(st.value.value, str)):
                                continue
                            target = st
                            break
                        if target is None:
                            target = body.body[-1]
                        idx = sym_base[owner] + (target.lineno - sym_start[owner])
                        indent = " " * target.col_offset
                        prev_indent, prev_names = per_point.get(idx, (indent, set()))
                        prev_names.add(f"from {dep_mod} import {nm}")
                        per_point[idx] = (prev_indent, prev_names)
        for idx, (indent, stmts) in sorted(per_point.items(), key=lambda kv: -kv[0]):
            # ``stmts`` is a set: sort so the emitter is byte-deterministic and a
            # re-run against an unchanged monolith produces no diff. The names in
            # one merged block are independent bindings from the same module, so
            # the order carries no meaning.
            body_lines.insert(idx, "\n".join(indent + s for s in sorted(stmts)))
        lazy_report[mod] = len(per_point)

        body = "\n".join(body_lines)

        # ---- the one non-verbatim adaptation ------------------------------
        # Moving a symbol out of channel/web/web_channel.py changes what
        # ``__file__`` resolves to, so asset paths that were relative to the
        # monolith's own directory must be re-anchored at the web package root
        # (``channel/web`` -- home of chat.html and static/). Depth comes from
        # the module's own path, so both ``fork/*.py`` and ``fork/handlers/*.py``
        # land on the same root.
        depth = 3 if mod.startswith(FORK_PKG + "/handlers/") else 2
        anchor = ("os.path.dirname(" * depth
                  + "os.path.abspath(__file__)" + ")" * depth)
        if "os.path.dirname(" in body:
            body = body.replace("os.path.dirname(os.path.abspath(__file__))", "_WEB_ROOT")
            body = body.replace("os.path.dirname(__file__)", "_WEB_ROOT")
            body = (
                "# Adapted on move (not verbatim, see the emitter): ``__file__``\n"
                "# now points at the fork package, so asset paths anchor at the web\n"
                "# package root instead of the monolith's own directory.\n"
                f"_WEB_ROOT = {anchor}\n\n\n" + body
            )
        header = (
            '"""Fork web layer (change adopt-upstream-web-split, design D2).\n'
            "\n"
            "Fork-owned implementation, moved verbatim out of the former\n"
            "channel/web/web_channel.py monolith. Upstream's api/ modules are not\n"
            "edited. Imports inside function bodies are lazy so these modules can\n"
            "reference each other without import cycles.\n"
            '"""\n'
            "\n"
            "from __future__ import annotations\n"
        )
        content = header + "".join(i + "\n" for i in sorted(set(top_imports))) + "\n\n" + body + "\n"
        os.makedirs(os.path.dirname(mod), exist_ok=True)
        open(mod, "w", encoding="utf-8").write(content)
        written.append(mod)

    # ---- entry module imports --------------------------------------------
    entry_groups: Dict[str, List[str]] = defaultdict(list)
    for h in sorted(n for n in syms if n.endswith("Handler")):
        entry_groups[mod_of[h]].append(h)
    json.dump(
        {"entry_handler_imports": {k: sorted(v) for k, v in entry_groups.items()},
         "module_symbols": {m: sorted(v) for m, v in by_mod.items()},
         "modules": sorted(by_mod), "unresolved": unresolved,
         "lazy_statements": lazy_report,
         "cyclic_modules": sorted(cyclic)},
        open(_work("emit_fork_web_report.json"), "w"), indent=2,
    )

    print("=== modules ===")
    for m in sorted(by_mod):
        print(f"  {m:<50} symbols={len(by_mod[m]):<4} lazy={lazy_report.get(m, 0):<4} "
              f"unresolved={len(unresolved.get(m, []))}")
    print()
    print(f"=== modules needing lazy imports (in a dependency cycle): {len(cyclic)} ===")
    for m in sorted(cyclic):
        print(f"  {m}")
    print()
    if unresolved:
        print("=== unresolved ===")
        for m, v in sorted(unresolved.items()):
            print(f"  {m}: {sorted(set(v))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
