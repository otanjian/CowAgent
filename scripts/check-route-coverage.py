#!/usr/bin/env python3
# encoding:utf-8
"""Route coverage gate: fail when the console route tables can silently drift.

Intended to run right after an upstream merge (see ``scripts/sync-from-master.sh``)
and in CI, so two failure modes become mechanical instead of tribal knowledge:

1. **A route was lost or mis-bound in a merge.** The registry is the single
   source of ``web_channel._WEB_URLS`` and ``auth.http_policy.ROUTE_POLICY``;
   this script also verifies both derived tables still match the registry.
2. **A handler implements a method nobody registered**, or a route registers a
   method no handler implements. This is the third leg of the coverage
   invariant -- the only one that is not tautological, because the first two
   legs compare two projections of the same registry.

Exit codes: 0 = ok, 1 = violations, 2 = could not import the app (environment).

Usage::

    python scripts/check-route-coverage.py            # human readable
    python scripts/check-route-coverage.py --quiet    # only exit status
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing unless there are violations")
    args = parser.parse_args()

    try:
        from auth import http_policy
        from channel.web import route_registry, web_channel
    except Exception as exc:  # pragma: no cover - environment problem
        print("route-coverage: cannot import the console app: %r" % (exc,),
              file=sys.stderr)
        return 2

    violations = list(route_registry.check_route_coverage(vars(web_channel)))

    # The two derived tables must still be exact projections of the registry.
    if tuple(web_channel._WEB_URLS) != route_registry.derive_web_urls():
        violations.append("web_channel._WEB_URLS is not the registry projection")
    if http_policy.ROUTE_POLICY != route_registry.derive_route_policy():
        violations.append("auth.http_policy.ROUTE_POLICY is not the registry projection")

    routes = route_registry.all_routes()
    fork_routes = sum(1 for r in routes if r.source.startswith("fork:"))
    summary = ("route-coverage: %d routes (%d upstream, %d fork), %d method entries"
               % (len(routes), len(routes) - fork_routes, fork_routes,
                  sum(len(r.methods) for r in routes)))

    if violations:
        print(summary + "\nFAILED with %d violation(s):" % len(violations))
        for item in violations:
            print("  - " + item)
        return 1

    if not args.quiet:
        print(summary + "\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
