#!/usr/bin/env python3
"""Rewrite channel/web/web_channel.py as the upstream-shaped entry module.

Keeps the contract the rest of the system reaches for through this module:

* ``_WEB_URLS`` and ``build_web_app()`` (the URL table and app factory),
* ``WebChannel`` / ``SERVING`` / ``SSEStreamState`` / ``WebMessage`` (app.py),
* every handler the URL table names, in this module's globals, and
* the monolith's own module-level imports, because other code patches and reads
  names through ``web_channel.<name>`` (e.g. ``web_channel.conf`` in tests).

Handler names come from two places and both must be re-exported here:
  * those now living in ``channel/web/fork/**`` (moved out of the monolith), and
  * those already defined in other fork modules and merely imported by the
    monolith (admin_handlers, help_site, tenant_workspace, scene/workbench, ...).
"""
from __future__ import annotations
import os

import ast
import json
import subprocess
from collections import defaultdict

# Scratch directory for the intermediate maps these steps hand to each other.
# The migration is a pipeline (symbol map -> domain map -> emit), and its
# outputs are large enough to keep out of the tree; override to run elsewhere.
WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")


def _work(name: str) -> str:
    return os.path.join(WORKDIR, name)

SOURCE_REF = "rdai"
SRC = "channel/web/web_channel.py"

report = json.load(open(_work("emit_fork_web_report.json")))
groups = report["entry_handler_imports"]

source = subprocess.run(["git", "show", f"{SOURCE_REF}:{SRC}"],
                        capture_output=True, text=True, check=True).stdout
tree = ast.parse(source)

ORIGINAL_IMPORTS: list[str] = []
per_name: dict[str, str] = {}
inject_stmts: list[str] = []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        stmt = ast.get_source_segment(source, node)
        ORIGINAL_IMPORTS.append(stmt)
        for a in node.names:
            if a.name != "*":
                per_name[a.asname or a.name.split(".")[0]] = stmt
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                           ast.Assign, ast.AnnAssign)):
        continue
    else:
        inject_stmts.append(ast.get_source_segment(source, node))

IMPORT_LINES: list[str] = []
for mod, names in sorted(groups.items()):
    target = mod.replace("/", ".").removesuffix(".py")
    IMPORT_LINES.append("from %s import (" % target)
    for n in sorted(names):
        IMPORT_LINES.append("    %s," % n)
    IMPORT_LINES.append(")")

# The monolith's namespace is a public surface: other modules and tests reach for
# helpers through ``web_channel.<name>`` (not just handlers), so every moved
# symbol is re-exported here. Fork symbols are imported after the monolith's own
# imports so the fork implementation wins for any shared name.
ALL_LINES: list[str] = []
for mod, names in sorted(report["module_symbols"].items()):
    target = mod.replace("/", ".").removesuffix(".py")
    ALL_LINES.append("from %s import (" % target)
    for n in sorted(names):
        ALL_LINES.append("    %s," % n)
    ALL_LINES.append(")")

DOC = '''"""The console's URL table.

Every route the web console answers on, and the web.py application built from
it. The handlers themselves live in ``channel/web/fork/`` -- the fork's
authorization and tenant scoping participate inside the handler bodies, so the
fork owns its handler implementations (see the change
``openspec/changes/adopt-upstream-web-split``, design D2). Upstream's
``channel/web/api/`` modules are not edited.

``build_web_app()`` resolves the names in ``_WEB_URLS`` against this module's
globals, so this module imports every handler the table names, and keeps the
imports other code reaches for through ``channel.web.web_channel``.
"""'''

TAIL = '''

# Full URL table for the Web console, DERIVED from the single authoritative
# route registry (``channel.web.route_registry``). Do not add routes here: add a
# ``RouteEntry`` to the registry so the URL table and the authorization policy
# table (``auth.http_policy.ROUTE_POLICY``) cannot drift apart again. Order is
# preserved from the registry and is behavior-significant (first match wins).
_WEB_URLS = _derive_web_urls()


def build_web_app():
    """Build the real web.py console application (used by dev server/testing).

    Installs the shared HTTP-method policy processor so the production server
    and the test harness enforce the same route/method authorization gate. In
    database identity mode, a multi-worker deployment is rejected because the
    in-process login limiter / identity state are single-process only.
    """
    from auth.http_policy import enforce_http_policy
    from auth.ratelimit import reject_multi_worker_identity
    reject_multi_worker_identity()
    app = web.application(_WEB_URLS, globals(), autoreload=False)
    app.add_processor(enforce_http_policy)
    return app
'''

HEAD = DOC + '''

from __future__ import annotations

# The monolith's imports, kept verbatim: other code reads and patches names
# through ``channel.web.web_channel`` (e.g. tests patch ``web_channel.conf``).
'''

body = HEAD
body += "\n".join(ORIGINAL_IMPORTS) + "\n"
body += "\n\n# Handlers that moved into the fork package; imported so web.py can resolve\n"
body += "# them against this module's globals.\n"
body += "\n".join(IMPORT_LINES) + "\n"
body += "\n\n# The rest of the moved symbols, re-exported because the former monolith's\n"
body += "# namespace is a public surface (other modules and tests reach helpers as\n"
body += "# ``web_channel.<name>``). Imported after the monolith's own imports so the\n"
body += "# fork implementation wins for any shared name. Includes WebChannel, SERVING,\n"
body += "# SSEStreamState and WebMessage that app.py resolves through this module.\n"
body += "\n".join(ALL_LINES) + "\n"
if inject_stmts:
    body += "\n\n" + "\n".join(inject_stmts) + "\n"
body += TAIL

open(SRC, "w", encoding="utf-8").write(body)
print("entry module written: %s" % SRC)
print("moved handlers: %d from %d modules" % (sum(len(v) for v in groups.values()), len(groups)))
print("original imports kept verbatim: %d" % len(ORIGINAL_IMPORTS))
print("reproduced namespace injections: %d" % len(inject_stmts))
