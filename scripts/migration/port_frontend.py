#!/usr/bin/env python3
"""Port the fork's console.js / console.css customization onto upstream's split.

Phase 3 tasks 4.4a/4.4b. Upstream deleted both monoliths and split them by
concern into ``static/js/{core,chat,views}/*.js`` and ``static/css/*.css``. The
fork's customization is a diff against the *merge base*, so it is re-anchored
onto whichever upstream module inherited each changed region:

  for every changed hunk, find the base lines immediately before and after it in
  the upstream module that owns that region, and splice the fork's exact text
  between them.

Hunks whose surrounding base code upstream rewrote have no landing site. They are
*not* dropped and *not* guessed at: they are written to the worklist with their
base and fork samples so they can be hand-ported (task 4.4b). Dropping them
silently is the failure mode this tool exists to prevent.

Emits ``static/js/fork/<subpath>`` (+ css) and ``port_frontend_worklist.json``.
Deterministic: the same inputs produce byte-identical output, so "did anything
drift?" stays answerable.
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_frontend_divergence import (  # noqa: E402
    base_owner_map, build_index, git_ls, git_show, lines_of, norm,
)

WORKDIR = os.environ.get("FORK_MIGRATION_WORKDIR", "/tmp")
BASE_REF = os.environ.get("FORK_BASE_REF", "e5e2a52d")
UPSTREAM_REF = os.environ.get("FORK_UPSTREAM_REF", "origin/master")
FORK_REF = os.environ.get("FORK_FORK_REF", "HEAD")
CONTEXT = 3

JS_DIRS = ("channel/web/static/js/core", "channel/web/static/js/chat",
           "channel/web/static/js/views")
CSS_DIR = "channel/web/static/css"


def locate(hay: List[str], needle: List[str], start: int = 0) -> int:
    """Index of the first ``needle`` run in ``hay`` at or after ``start``, or -1."""
    needle = [n for n in needle if n]
    if not needle:
        return -1
    n = len(needle)
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def merge_overlapping(hunks: List[dict]) -> List[dict]:
    """Merge hunks whose base ranges overlap or touch.

    Non-overlapping hunks do *not* need merging: each one is located in the
    original, unmutated module and then all splices are applied back-to-front,
    so one hunk's insertion cannot consume another's context. Overlapping hunks
    do need merging -- two splices cannot share a line.
    """
    order = sorted(hunks, key=lambda h: (h["i1"], h["i2"]))
    merged: List[dict] = []
    for h in order:
        if merged and h["i1"] <= merged[-1]["i2"]:
            last = merged[-1]
            last["i2"] = max(last["i2"], h["i2"])
            last["j2"] = max(last["j2"], h["j2"])
        else:
            merged.append(dict(h))
    return merged


def base_to_module_map(owners: List, module_rel: str,
                       base: List[str], module_lines: List[str],
                       margin: int = CONTEXT) -> Dict[int, List[int]]:
    """Exact base-line -> [module line, containing equal-block span] for this module.

    Free context search is not safe here: a short context window can match at a
    wrong offset and silently splice fork code into unrelated code (measured: 9
    of 25 emitted modules failed ``node --check``, with duplicate ``let``
    declarations and unbalanced braces). So position is *derived*, not searched:
    the base lines a module owns reproduce its content in order, so aligning that
    base slice against the module gives an exact mapping where upstream kept the
    code and no mapping where upstream rewrote it.

    Each mapped line carries the span of the equal block it came from. That span
    is what makes the mapping trustworthy: a coincidental match of a single
    structural line produces a block of length 1, and a hunk sitting in such a
    block is rejected rather than spliced on a guess. Only a hunk with real,
    verified unchanged code on both sides is ported -- ``margin`` lines each way.
    """
    runs, start = [], None
    for i, owner in enumerate(owners + [None]):
        if owner == module_rel and start is None:
            start = i
        elif owner != module_rel and start is not None:
            runs.append((start, i))
            start = None

    mapping: Dict[int, List[int]] = {}
    for a, b in runs:
        sm = difflib.SequenceMatcher(None, [norm(l) for l in base[a:b]],
                                     [norm(l) for l in module_lines],
                                     autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                # Spans are kept in *global* base coordinates: they are compared
                # against base indices when checking a hunk's context margin.
                for k in range(i2 - i1):
                    mapping[a + i1 + k] = [j1 + k, a + i1, a + i2]
    return mapping


def plan_site(mapping: Dict[int, List[int]], h: dict,
              margin: int = CONTEXT) -> Optional[dict]:
    """Module span for hunk ``h``, or None when it cannot be ported safely."""
    i1, i2, j1, j2 = h["i1"], h["i2"], h["j1"], h["j2"]
    if i1 == i2:
        # Insertion: lands before base line i1, which must be mapped, with
        # verified context on both sides.
        entry = mapping.get(i1)
        if entry is None:
            return None
        pos, b_start, b_end = entry
        if i1 - b_start < margin or b_end - i1 < margin:
            return None
        return {"start": pos, "end": pos, "j1": j1, "j2": j2}

    span = [mapping.get(i) for i in range(i1, i2)]
    if any(m is None for m in span) or any(span[k][0] + 1 != span[k + 1][0]
                                           for k in range(len(span) - 1)):
        return None
    b_start, b_end = span[0][1], span[0][2]
    if any(m[1] != b_start or m[2] != b_end for m in span):
        return None  # spans two equal blocks: something changed in between
    if i1 - b_start < margin or b_end - i2 < margin:
        return None  # too close to upstream's own edits to be trusted
    return {"start": span[0][0], "end": span[-1][0] + 1, "j1": j1, "j2": j2}


def parses(lines: List[str]) -> bool:
    """Does this JavaScript parse? The last line of defence for a splice."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
        tmp = fh.name
    try:
        return subprocess.run(["node", "--check", tmp],
                              capture_output=True).returncode == 0
    finally:
        os.unlink(tmp)


def port_module(module_rel: str, upstream_lines: List[str],
                hunks: List[dict], base: List[str], fork: List[str],
                owners: List) -> dict:
    """Upstream module text with every safely-locatable fork hunk spliced in.

    Positions are derived from the alignment, which rules out the gross
    mis-location that free context search produced -- but a splice can still
    produce code that does not parse when upstream restructured the statement
    around it. So for JavaScript each splice is *verified*: applied in order, and
    kept only if the module still parses. A splice that breaks the module is not
    emitted and not guessed at; it joins the hand-port list. That trade is
    deliberate -- a module that silently loses a fork authorization branch is
    far worse than one flagged for review.
    """
    mapping = base_to_module_map(owners, module_rel, base, upstream_lines)
    sites, skipped = [], []
    for h in merge_overlapping(hunks):
        site = plan_site(mapping, h)
        if site is None:
            reason = ("no verified context around the insertion point"
                      if h["i2"] == h["i1"]
                      else "no verified context: upstream rewrote the surrounding code")
            skipped.append({**h, "reason": reason})
        else:
            sites.append(site)

    # Sites must not overlap in the module: two splices cannot share a line.
    ordered: List[dict] = []
    for site in sorted(sites, key=lambda s: (s["start"], s["end"])):
        if ordered and site["start"] < ordered[-1]["end"]:
            skipped.append({"i1": -1, "i2": -1, "j1": site["j1"], "j2": site["j2"],
                            "reason": "maps onto another ported hunk in this module"})
        else:
            ordered.append(site)

    verify = module_rel.endswith(".js")
    out = list(upstream_lines)
    kept = 0
    offset = 0  # earlier splices shift the text that later sites were measured in
    for site in ordered:
        start, end = site["start"] + offset, site["end"] + offset
        inserted = fork[site["j1"]:site["j2"]]
        trial = out[:start] + inserted + out[end:]
        if verify and not parses(trial):
            skipped.append({"i1": -1, "i2": -1, "j1": site["j1"], "j2": site["j2"],
                            "reason": "splice left the module unparseable"})
            continue
        out = trial
        offset += len(inserted) - (end - start)
        kept += 1

    return {"lines": out, "applied": kept, "skipped": skipped}


def build_hunks(base: List[str], fork: List[str], owners: List):
    """Split the base->fork change into hunks, each wholly inside one module.

    A diff hunk is a change in the *whole file*; upstream then cut that file into
    modules, so one hunk can straddle a boundary. Such a hunk cannot be ported
    mechanically: the fork's replacement text is one edit, and slicing it to
    match the boundary cuts it mid-statement (measured: emitted modules with
    ``function f() { } }`` fragments and unbalanced braces). Straddling hunks are
    returned separately for hand porting instead of being cut up or dumped whole
    into whichever module happens to own the first line.
    """
    opcodes = difflib.SequenceMatcher(
        None, [norm(l) for l in base], [norm(l) for l in fork],
        autojunk=False).get_opcodes()

    def owner_of(i: int) -> Optional[str]:
        if i < len(owners) and owners[i]:
            return owners[i]
        if 0 <= i - 1 < len(owners) and owners[i - 1]:
            return owners[i - 1]
        return None

    by_module: Dict[str, List[dict]] = defaultdict(list)
    straddling: List[dict] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        if i1 == i2:  # insertion: belongs to the module owning the line after it
            owner = owner_of(i1)
            if owner is not None:
                by_module[owner].append({"i1": i1, "i2": i2, "j1": j1, "j2": j2})
            continue
        seen = []
        for i in range(i1, i2):
            o = owner_of(i)
            if o and o not in seen:
                seen.append(o)
        if len(seen) != 1:
            straddling.append({"i1": i1, "i2": i2, "j1": j1, "j2": j2,
                               "modules": seen})
            continue
        by_module[seen[0]].append({"i1": i1, "i2": i2, "j1": j1, "j2": j2})
    return by_module, straddling


def main() -> int:
    upstream_js = {p: git_show(f"{UPSTREAM_REF}:{p}") for p in
                   sum((git_ls(UPSTREAM_REF, d) for d in JS_DIRS), [])}
    upstream_css = {p: git_show(f"{UPSTREAM_REF}:{p}") for p in
                    git_ls(UPSTREAM_REF, CSS_DIR)}
    out_root = os.path.join(WORKDIR, "fork-frontend")
    worklist = {"applied": {}, "skipped": [], "unowned": []}
    summary = []

    for name, path, upstream in (
        ("console.js", "channel/web/static/js/console.js", upstream_js),
        ("console.css", "channel/web/static/css/console.css", upstream_css),
    ):
        base = lines_of(git_show(f"{BASE_REF}:{path}"))
        fork = lines_of(git_show(f"{FORK_REF}:{path}"))
        index = build_index({p: lines_of(t) for p, t in upstream.items()})
        owners = base_owner_map(base, index)
        by_module, straddling = build_hunks(base, fork, owners)
        for h in straddling:
            worklist["skipped"].append({
                "source": name, "upstream_module": None,
                "base_range": [h["i1"] + 1, h["i2"]],
                "added": h["j2"] - h["j1"], "removed": h["i2"] - h["i1"],
                "reason": "fork edit crosses an upstream module boundary",
                "modules": h["modules"],
                "base_sample": base[h["i1"]:h["i1"] + 3],
                "fork_sample": fork[h["j1"]:h["j1"] + 3],
            })

        applied_total = skipped_total = 0
        clusters_total = 0
        for module_rel, hunks in sorted(by_module.items()):
            clustered = merge_overlapping(hunks)
            clusters_total += len(clustered)
            res = port_module(module_rel, lines_of(upstream[module_rel]), clustered,
                              base, fork, owners)
            applied_total += res["applied"]
            skipped_total += len(res["skipped"])
            # fork module mirrors the upstream subpath under a fork/ root
            if "/static/js/" in module_rel:
                fork_rel = module_rel.replace("/static/js/", "/static/js/fork/", 1)
            else:
                fork_rel = module_rel.replace("/static/css/", "/static/css/fork/", 1)
            dest = os.path.join(out_root, fork_rel.replace("channel/web/", ""))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write("\n".join(res["lines"]) + "\n")
            worklist["applied"][fork_rel] = {
                "upstream_path": module_rel,
                "applied": res["applied"],
                "skipped": len(res["skipped"]),
            }
            for s in res["skipped"]:
                worklist["skipped"].append({
                    "source": name, "upstream_module": module_rel,
                    "base_range": [s["i1"] + 1, s["i2"]],
                    "added": s["j2"] - s["j1"], "removed": s["i2"] - s["i1"],
                    "reason": s["reason"],
                    "base_sample": base[s["i1"]:s["i1"] + 3],
                    "fork_sample": fork[s["j1"]:s["j1"] + 3],
                })

        summary.append((name, applied_total, skipped_total,
                        len(by_module), clusters_total, len(worklist["unowned"])))

    wl_path = os.path.join(WORKDIR, "port_frontend_worklist.json")
    json.dump(worklist, open(wl_path, "w"), indent=2)

    for name, applied, skipped, modules, clusters, unowned in summary:
        print(f"{name}: {applied} of {clusters} change clusters applied into "
              f"{modules} fork modules; {skipped} clusters need hand porting; "
              f"{unowned} hunks had no owning module")
    print(f"modules -> {os.path.join(out_root, 'js', 'fork')} , "
          f"{os.path.join(out_root, 'css', 'fork')}")
    print(f"worklist -> {wl_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
