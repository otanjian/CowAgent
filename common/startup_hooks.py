# encoding:utf-8
"""Fork-owned startup hooks (tasks 6.11 and 8.9).

``app.py`` is an upstream file and its boot sequence is upstream's to change.
This fork used to answer that with inline branches in the sequence --
``_guard_identity_mode_consistency()`` aborting the boot, a tenancy backfill
added straight into the startup path -- and every upstream edit to ``run()``
then had to be merged against them.

The seam instead is a tiny registry. The fork registers a named hook here, and
``app.py`` keeps a named entry point that runs whatever is registered:

    app.py:      def _guard_identity_mode_consistency() -> bool:
                     return run_startup_hook(HOOK_IDENTITY_MODE_CONSISTENCY)

    this module: register(HOOK_IDENTITY_MODE_CONSISTENCY, _identity_mode_consistency)

Adding a guard, or removing the fork entirely, is then a change *here*, not in
``app.py``: with nothing registered ``run_startup_hook`` returns False and the
upstream boot is exactly upstream's (task 8.10). The hooks are registered when
this module is imported, and ``run_startup_hook`` lives here, so importing the
seam is enough to arm them -- ``app.py`` cannot end up calling a seam whose
implementation was never registered.

Hooks run in the calling thread, in the boot's single-threaded phase, and are
*not* individually swallowed: a hook that raises propagates, because the
identity-mode guard exists precisely to abort a boot that would read the wrong
identity. A hook that is merely best-effort catches its own errors.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Tuple

HOOK_IDENTITY_MODE_CONSISTENCY = "identity_mode_consistency"
HOOK_TENANT_CONVERSATION_BACKFILL = "tenant_conversation_backfill"

_HOOKS: Dict[str, Tuple[int, Callable[[], None]]] = {}


def register_startup_hook(name: str, fn: Callable[[], None],
                          order: int = 100) -> None:
    """Register ``fn`` under ``name``; re-registering replaces it."""
    _HOOKS[name] = (order, fn)


def is_registered(name: str) -> bool:
    return name in _HOOKS


def registered_hooks() -> List[str]:
    return [name for name, _ in sorted(_HOOKS.items(),
                                       key=lambda item: (item[1][0], item[0]))]


def run_startup_hook(name: str) -> bool:
    """Run the hook registered under ``name``; False when there is none."""
    entry = _HOOKS.get(name)
    if entry is None:
        return False
    _, fn = entry
    fn()
    return True


# ---------------------------------------------------------------------------
# This fork's hooks
# ---------------------------------------------------------------------------

def _identity_mode_consistency() -> None:
    """Abort startup if legacy mode would read already-migrated identity data.

    ``identity_mode=legacy`` must never silently open ``identity.db`` that was
    already migrated to the new IAM mode (task 4.6, isolation spec). When that
    happens the database holds authoritative new-mode data; continuing in
    legacy would read it through the wrong (shared-password) path.
    """
    from common.log import logger
    try:
        from config import conf, get_data_root
        from auth.store import refuse_legacy_after_migration
        mode = str(conf().get("identity_mode", "legacy") or "legacy")
        configured = conf().get("identity_db_path")
        db_path = configured or os.path.join(get_data_root(), "identity.db")
        if refuse_legacy_after_migration(mode, db_path):
            logger.error(
                f"[App] Refusing to start: identity.db at {db_path} has already "
                f"been migrated to the 'database' identity mode, but config "
                f"'identity_mode={mode}'. Either set identity_mode=database "
                f"(the identity data is authoritative) or restore a pre-migration "
                f"snapshot before booting legacy. Refusing to read new-mode data "
                f"in legacy mode."
            )
            # Desktop shell treats non-zero as a real startup failure; servers
            # should not limp along reading the wrong identity either.
            raise RuntimeError("refusing to boot legacy over migrated identity.db")
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        logger.warning(f"[App] Identity-mode consistency check skipped: {e}")


def _tenant_conversation_backfill() -> None:
    """Attribute pre-isolation conversations to their tenant (task 6.11).

    It runs here rather than in ``conversation_store`` because it is the one
    moment the boot is single-threaded, before any channel has opened a store.
    The assumption it shares with upstream's own conversation migration is
    design D10: conversations live in one store keyed by ``agent_id``, and this
    fork's tenancy is a *filter* column on that store (``owner``/``tenant_id``),
    never a per-Agent file. The backfill therefore only fills columns; it does
    not move or split files, so it stays compatible with
    ``migrate_conversations_to_global`` running alongside it.
    """
    from common.log import logger
    try:
        from config import conf
        mode = str(conf().get("identity_mode", "legacy") or "legacy")
        if mode != "database":
            return

        from auth.service import get_identity_service
        from agent.memory.conversation_store import (
            conversation_store_path,
            get_conversation_store,
        )

        svc = get_identity_service()
        tenants = svc.tenant_shared_roots()

        def tenants_for_owner(user_id: str):
            """The single tenant this user belongs to, or None when ambiguous.

            Multiple memberships are deliberately *not* resolved to one: a
            conversation whose owner belongs to two tenants has no derivable
            tenant, and guessing would leak it into whichever was picked.
            """
            matches = []
            for tenant in tenants:
                membership = svc.get_membership(user_id, tenant["id"])
                if membership and membership.get("active") \
                        and membership.get("user_active"):
                    matches.append(tenant["id"])
            return matches[0] if len(matches) == 1 else None

        roots = [None] + [
            t["shared_root"] for t in tenants if t.get("shared_root")
        ]
        for root in roots:
            path = conversation_store_path(root)
            if not path.exists():
                # Never manufacture an empty store for a workspace that has
                # none: opening one creates the file.
                continue
            counts = get_conversation_store(root).backfill_tenant(
                tenants_for_owner)
            if counts["sessions"] or counts["messages"] or counts["unresolved"]:
                logger.info(
                    "[App] Conversation tenancy backfill on %s: "
                    "%(sessions)s sessions, %(messages)s messages attributed, "
                    "%(unresolved)s owners left unattributed" % counts
                )
    except Exception as e:
        # A failed backfill leaves rows unattributed, which is the safe state
        # (unreadable to a tenant scope) -- it must not stop the boot.
        logger.warning(f"[App] Conversation tenancy backfill skipped: {e}")


def register_fork_startup_hooks() -> None:
    """Idempotently arm this fork's hooks."""
    register_startup_hook(HOOK_IDENTITY_MODE_CONSISTENCY,
                          _identity_mode_consistency, order=10)
    register_startup_hook(HOOK_TENANT_CONVERSATION_BACKFILL,
                          _tenant_conversation_backfill, order=20)


register_fork_startup_hooks()
