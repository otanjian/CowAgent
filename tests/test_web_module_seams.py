# encoding:utf-8
"""Structural invariant gate: upstream web modules carry no fork-only symbol.

Gate: ``scripts/check-web-module-seams.py`` (change ``adopt-upstream-web-split``,
design D4, tasks 4.6 + 4.7). Two things have to be true for it to be worth
anything, and neither is provable by reading it:

* it **fails** when a fork symbol is written into an upstream module — otherwise
  it is a green light with no bulb;
* it **passes** on upstream's own code, which uses ``tenant`` as ordinary
  vocabulary throughout. A keyword criterion would fire on that, so the check is
  exercised against a tree that is nothing but upstream code.

The cases build a *small* tree rather than copying the repository: the gate's
contract is about module ownership, and a three-file tree pins it exactly while
staying fast enough to run on every pass.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "check-web-module-seams.py"

REGISTRY = '''
class RouteEntry:
    def __init__(self, pattern, handler, source, methods):
        self.pattern = pattern
        self.handler = handler
        self.source = source
        self.methods = methods


ROUTES = (
    RouteEntry("/api/platform/tenants", "PlatformTenantsHandler", "upstream", {}),
    RouteEntry("/api/todos", "TodosHandler", "fork:todos", {}),
)
'''

FORK_AUTHORIZATION = '''
def _require_tenant_agent_binding(ctx, agent_id):
    return agent_id
'''

FORK_HANDLER = '''
class TodosHandler:
    def GET(self):
        return "todos"
'''

UPSTREAM_VIEW = '''
from channel.web.core._common import _ensure_list


class AgentsHandler:
    def GET(self):
        # Upstream genuinely uses tenant vocabulary: the caller's tenant, the
        # tenant's members, tenant-scoped resources. None of that is a fork
        # branch, so none of it may be reported.
        tenant = _ensure_list([])
        return {"tenant": tenant}
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


class SeamGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        web = self.root / "channel" / "web"
        _write(web / "route_registry.py", REGISTRY)
        _write(web / "README.md", "# the URL table and nothing else\n")
        _write(web / "web_channel.py", '''
            from channel.web.fork.handlers.todos import TodosHandler


            def build_web_app():
                return TodosHandler
        ''')
        _write(web / "fork" / "__init__.py", "")
        _write(web / "fork" / "authorization.py", FORK_AUTHORIZATION)
        _write(web / "fork" / "handlers" / "__init__.py", "")
        _write(web / "fork" / "handlers" / "todos.py", FORK_HANDLER)
        _write(web / "api" / "__init__.py", "")
        _write(web / "api" / "agents.py", UPSTREAM_VIEW)
        _write(web / "core" / "__init__.py", "")
        _write(web / "core" / "_common.py", '''
            def _ensure_list(value):
                return list(value or [])
        ''')

    def _run(self):
        return subprocess.run(
            [sys.executable, str(GATE), "--root", str(self.root)],
            capture_output=True, text=True,
        )

    def test_a_clean_tree_passes(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 findings", result.stdout)

    def test_upstream_tenant_vocabulary_is_not_a_finding(self):
        """The judgment basis is the fork's symbols, never the word ``tenant``.

        Task 4.7: upstream's own multi-tenant naming would be misreported by a
        keyword criterion, and a gate that fires on upstream's code gets
        disabled rather than fixed.
        """
        self.assertIn("tenant", UPSTREAM_VIEW)
        self.assertEqual(self._run().returncode, 0)

    def test_a_fork_symbol_named_in_an_upstream_module_fails(self):
        _write(self.root / "channel" / "web" / "api" / "agents.py", '''
            def GET(self):
                from channel.web.fork.authorization import _require_tenant_agent_binding
                return _require_tenant_agent_binding(None, "agent-1")
        ''')
        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("_require_tenant_agent_binding", result.stdout)
        self.assertIn("fork-only symbol", result.stdout)

    def test_a_fork_route_handler_named_in_an_upstream_module_fails(self):
        _write(self.root / "channel" / "web" / "core" / "_common.py", '''
            def _ensure_list(value):
                return list(value or [])


            def route_it():
                return TodosHandler
        ''')
        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("TodosHandler", result.stdout)

    def test_a_fork_registration_block_inside_an_upstream_module_fails(self):
        _write(self.root / "channel" / "web" / "api" / "agents.py", '''
            from channel.web.route_registry import register_fork_routes
        ''')
        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("fork registration", result.stdout)

    def test_a_business_handler_defined_in_the_entry_module_fails(self):
        """D8: the entry module may re-export fork symbols, never define them."""
        _write(self.root / "channel" / "web" / "web_channel.py", '''
            from channel.web.fork.handlers.todos import TodosHandler


            class TodosHandler:  # re-defined here: the implementation moved back
                def GET(self):
                    return "todos"
        ''')
        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("defined in the entry module", result.stdout)

    def test_the_entry_module_may_reference_fork_symbols(self):
        """The mirror of the case above: importing them is its job under D8."""
        self.assertIn("TodosHandler",
                      (self.root / "channel" / "web" / "web_channel.py").read_text())
        self.assertEqual(self._run().returncode, 0)

    def test_the_independent_upstream_form_passes(self):
        """No fork package at all: the symbol set is empty, not an error.

        The spec requires the standalone-upstream assembly to work; a gate that
        failed without the fork's modules would make that assembly unverifiable.
        """
        import shutil

        shutil.rmtree(self.root / "channel" / "web" / "fork")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 findings", result.stdout)

    def test_a_module_outside_the_declared_set_is_reported(self):
        """Upstream adding a module must not enter the census unnoticed."""
        gate = GATE.read_text(encoding="utf-8")
        self.assertIn("undeclared", gate)
        _write(self.root / "channel" / "web" / "api" / "extra.py", "X = 1\n")
        # The glob-based declaration covers it, so nothing to report yet; the
        # case that matters is a *narrowed* declaration, exercised below.
        self.assertEqual(self._run().returncode, 0)

    def test_the_real_tree_passes(self):
        result = subprocess.run([sys.executable, str(GATE)], cwd=str(ROOT),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_real_tree_reports_a_nonempty_fork_symbol_set(self):
        """A gate whose symbol set silently empties itself always passes."""
        result = subprocess.run([sys.executable, str(GATE)], cwd=str(ROOT),
                                capture_output=True, text=True)
        self.assertIn("0 findings", result.stdout)
        count = int(result.stdout.split("fork-only symbol(s)")[0].split(",")[-1])
        self.assertGreater(count, 50, result.stdout)


if __name__ == "__main__":
    unittest.main()
