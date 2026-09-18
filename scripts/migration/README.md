# Fork web layer migration pipeline

One-shot tooling used to move the fork's web layer out of the
`channel/web/web_channel.py` monolith and into `channel/web/fork/`, as part of
the change `openspec/changes/adopt-upstream-web-split` (design D2).

It is kept in the tree because the split is a large mechanical transformation
whose correctness argument is "every symbol is sliced verbatim from the
monolith by its AST line range". That argument is only checkable if the slicing
can be re-run and diffed.

## Why it exists

A direct `master` → `rdai` merge could not resolve `channel/web/web_channel.py`:
upstream split its handlers into `channel/web/api/` + `channel/web/core/`, while
the fork had grown the same file into a monolith whose handler bodies carry the
fork's authorization and tenant scoping (56 of 64 upstream handlers, measured by
`analyze_handler_divergence.py`). The fork therefore owns its handler
implementations, and the merge re-anchors them onto upstream's shape instead of
re-applying a 650 KB file-level conflict.

See `evidence/02-fork-symbol-map.md` and `evidence/03-handler-divergence.md` in
the change for the measurements that drove this.

## Pipeline

Run from the repository root, in order. Intermediates land in
`$FORK_MIGRATION_WORKDIR` (default `/tmp`).

| # | Script | Reads | Writes |
|---|--------|-------|--------|
| 1 | `analyze_fork_web_symbols.py` | `channel/web/web_channel.py` + the pinned merge base and `origin/master` trees | `fork_symbol_map.json` — fork-only symbols and their dependency edges |
| 2 | `build_domain_map.py` | the symbol map | `fork_domain_map.json` — target module per symbol, mirroring upstream's `api/` layout |
| 3 | `emit_fork_web.py` | `git show <ref>:channel/web/web_channel.py` + the domain map | `channel/web/fork/**` and `emit_fork_web_report.json` |
| 4 | `emit_entry_module.py` | the emit report + the monolith | `channel/web/web_channel.py` (thin: URL table + handler imports) |

The ref the monolith is read from is `FORK_SOURCE_REF` (default `rdai`), so
re-running is safe after the entry module has already been rewritten.

Analysis-only, used to produce the change's evidence:

- `analyze_handler_wrappers.py` — which upstream handlers reach for fork-only helpers inside their bodies.
- `analyze_handler_divergence.py` — how far each fork handler's body diverges from upstream's (`difflib.SequenceMatcher` ratio).

## Two rules that are not verbatim

Everything else in the emitted modules is a byte-for-byte slice of the
monolith. Two things are adjusted, both in `emit_fork_web.py`:

1. **Asset root.** Moving a symbol changes what `__file__` resolves to, so
   `os.path.dirname(__file__)`-relative asset paths (`chat.html`, `static/`) are
   rewritten to a module-level `_WEB_ROOT` anchored at `channel/web`.
2. **Import routing.** References that the rest of the codebase already resolves
   through `channel.web.web_channel` (as `web_channel.<name>`, `from
   channel.web.web_channel import <name>`, or as the string argument of
   `patch.object(web_channel, "<name>")` / `setattr(web_channel, "<name>")`) stay
   routed through the entry module, lazily. That keeps one resolution point and
   keeps monkeypatching effective. Same-module references stay local, so no
   shadowing is introduced.

## Verifying a re-run

```sh
scripts/migration/emit_fork_web.py     # from the repo root
scripts/migration/emit_entry_module.py
git diff --stat channel/web/           # expect only intended changes
```

The emitter is deterministic: re-running it on an unchanged monolith and ref
must leave `channel/web/fork/**` and `channel/web/web_channel.py` untouched.

## Frontend: `console.js` / `console.css` -> `static/js/fork/**`, `static/css/fork/**`

Upstream deleted both frontend monoliths and split them by concern, so they are
`modify/delete` conflicts. Two scripts handle the fork's side:

- `analyze_frontend_divergence.py` — the migration inventory and the module
  mapping (Phase 3 task 4.1/4.2). Reports per-upstream-module customization
  volume and how much of it is mechanically re-anchorable. Output feeds
  `evidence/07-frontend-divergence.md`.
- `port_frontend.py` — emits the fork modules. For each fork change it derives
  the landing site from an alignment between the base file and the upstream
  module that inherited that region, splices the fork's text verbatim, and
  verifies the module still parses. Anything it cannot place safely (no verified
  context, an edit that crosses a module boundary, or a splice that breaks
  parsing) goes to `port_frontend_worklist.json` for hand porting.

Measured against `e5e2a52d`..`HEAD` onto `origin/master`:

| | change clusters | ported | hand port |
|---|---|---|---|
| `console.js` | 362 | 275 | 87 |
| `console.css` | 79 | 71 | 8 |

`node --check` passes on all 25 emitted JS modules.

**Two things this does not yet claim.** The emitted modules are not wired into
the page (no override map, see design D5) and are not committed to
`channel/web/static/`; and parsing is not correctness — the browser acceptance in
Phase 3 task 4.5 is what decides whether the ported behaviour is right.

Two rules the port obeys, both learned from failed runs:

1. **Position is derived, never searched.** A 3-line context search silently
   spliced fork code into unrelated code: 9 of 25 modules failed `node --check`
   with duplicate `let` declarations and unbalanced braces. The landing site
   comes from aligning the base slice against the module, and a hunk needs
   verified unchanged lines on both sides before it is ported at all.
2. **A fork edit that crosses an upstream module boundary is not split.** The
   fork's replacement text is one edit; cutting it at the boundary cut it
   mid-statement (`function f() { } }`). Straddling edits go to the worklist.
