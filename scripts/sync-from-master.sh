#!/usr/bin/env bash
#
# Attempt to merge upstream master into this fork and report what it means.
#
# Tasks 9.3/9.4. This script is deliberately *diagnostic*:
#
#   * it fetches and tries a merge, then always aborts it — it MUST NOT commit
#     and MUST NOT push, because a conflict-resolving merge is a human decision
#     made file by file against the seams in
#     openspec/changes/fork-decoupling-and-tenant-hardening;
#   * the judgement (which conflicts are expected, which are drift, which
#     deletions need re-deciding) lives in scripts/sync_report.py, which is
#     unit-tested; this file only does the untestable part.
#
# Exit codes: 0 merged cleanly (review the diff, then commit yourself),
#             1 conflicts (listed), 2 could not run (fetch/baseline failure).
#
# Usage:  scripts/sync-from-master.sh [upstream-remote] [upstream-branch]
#         scripts/sync-from-master.sh --help

set -uo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-master}"

case "${REMOTE}" in
    -h|--help)
        sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
    echo "no python interpreter found (set PYTHON=...)" >&2
    exit 2
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "not a git repository" >&2
    exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "working tree is dirty; commit or stash before syncing" >&2
    exit 2
fi

echo "== fetch ${REMOTE}/${BRANCH}"
if ! git fetch "$REMOTE" "$BRANCH" --quiet; then
    echo "fetch failed" >&2
    exit 2
fi

BEFORE="$(git rev-parse HEAD)"
echo "== merge attempt ${REMOTE}/${BRANCH} into $(git rev-parse --abbrev-ref HEAD)"

# --no-commit/--no-ff so the result is inspectable and abortable. A failure here
# is expected whenever the fork and upstream touched the same file.
git merge --no-commit --no-ff "${REMOTE}/${BRANCH}" >/tmp/sync-merge.out 2>&1
MERGE_STATUS=$?
cat /tmp/sync-merge.out

# Conflicted paths, empty when the merge applied cleanly.
CONFLICTS="$(git diff --name-only --diff-filter=U || true)"

"$PYTHON" scripts/sync_report.py --conflicts $CONFLICTS
REPORT_STATUS=$?

# Always restore: the merge is a rehearsal, and the operator decides what to
# keep. `reset --merge` is safe on both the conflicted and the clean path.
if [ "$MERGE_STATUS" -ne 0 ]; then
    git merge --abort 2>/dev/null || git reset --merge >/dev/null 2>&1 || true
else
    git reset --merge >/dev/null 2>&1 || git reset >/dev/null 2>&1 || true
fi

if [ "$(git rev-parse HEAD)" != "$BEFORE" ]; then
    echo "WARNING: HEAD moved unexpectedly during a rehearsal merge" >&2
    exit 2
fi

exit "$REPORT_STATUS"
