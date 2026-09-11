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
import secrets
from typing import Callable, Dict, List, Optional, Tuple

HOOK_IDENTITY_MODE_CONSISTENCY = "identity_mode_consistency"
HOOK_DATABASE_BOOTSTRAP = "database_bootstrap"
HOOK_TENANT_CONVERSATION_BACKFILL = "tenant_conversation_backfill"

#: One-shot initial admin password under the data root (mode 0600).
BOOTSTRAP_PASSWORD_FILENAME = ".bootstrap_admin_password"

_HOOKS: Dict[str, Tuple[int, Callable[[], None]]] = {}


def bootstrap_password_path(data_root: Optional[str] = None) -> str:
    from config import get_data_root
    root = data_root if data_root is not None else get_data_root()
    return os.path.join(root, BOOTSTRAP_PASSWORD_FILENAME)


def clear_bootstrap_password_file() -> None:
    """Remove the one-shot bootstrap password file after forced change."""
    path = bootstrap_password_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _write_bootstrap_password(path: str, password: str) -> None:
    """Write ``password`` to ``path`` with mode 0600 (best-effort on Windows)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(password)
            handle.write("\n")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
    """Refuse explicit legacy identity mode; database is the only mode.

    After retire-legacy-identity-mode, ``identity_mode=legacy`` MUST abort boot
    with an actionable message. Missing/other values run as database.
    """
    from common.log import logger
    try:
        from config import conf
        raw = conf().get("identity_mode", None)
        if raw is None or str(raw).strip() == "":
            return
        mode = str(raw).strip().lower()
        if mode == "legacy":
            logger.error(
                "[App] Refusing to start: identity_mode=legacy is no longer "
                "supported. Remove the key or set identity_mode=database, then "
                "restart. Shared-password / cow_auth_token auth has been retired."
            )
            raise RuntimeError(
                "identity_mode=legacy is no longer supported; use database identity"
            )
        if mode != "database":
            logger.error(
                f"[App] Refusing to start: unknown identity_mode={mode!r}. "
                f"Only database is supported."
            )
            raise RuntimeError(f"unsupported identity_mode={mode}")
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        logger.warning(f"[App] Identity-mode consistency check skipped: {e}")


def _database_bootstrap_auto_init() -> None:
    """Create default tenant + platform admin when identity.db has none.

    Failure aborts boot — never falls back to anonymous / shared-password
    access. The one-shot password is printed to the console and written to
    ``.bootstrap_admin_password`` (0600); it MUST NOT be logged.
    """
    from config import conf, get_data_root

    raw = conf().get("identity_mode", None)
    if raw is not None and str(raw).strip().lower() == "legacy":
        # Consistency guard raises first; skip as a safety net.
        return

    from auth.service import IdentityService, identity_db_path

    data_root = get_data_root()
    db_path = identity_db_path()
    try:
        svc = IdentityService(db_path)
        if svc.has_any_platform_admin():
            return

        password = secrets.token_urlsafe(24)
        shared_root = os.path.join(data_root, "tenants", "default")
        os.makedirs(shared_root, exist_ok=True)
        svc.bootstrap(
            tenant_code="default",
            tenant_name="Default",
            admin_username="admin",
            admin_display="Administrator",
            admin_password=password,
            shared_root=shared_root,
            allow_weak=False,
        )
        try:
            from agent.todo.service import reassign_empty_owner_todos
            admin = svc._find_user_by_username("admin")
            tenant = svc._find_tenant_by_code("default")
            if admin and tenant:
                reassign_empty_owner_todos(
                    scope_id=tenant["id"], owner_id=admin["id"],
                    app_data_root=shared_root,
                )
        except Exception:
            pass
        pw_path = bootstrap_password_path(data_root)
        _write_bootstrap_password(pw_path, password)
        # Console only — never logger (password must not enter log files).
        print(
            "[Bootstrap] Initial platform admin created.\n"
            f"  username: admin\n"
            f"  password: {password}\n"
            f"  password file: {pw_path} (mode 0600; deleted after first password change)\n"
            "  You MUST change this password on first login."
        )
    except Exception as e:
        raise RuntimeError(
            "database identity bootstrap failed; refusing to enter business "
            f"without a platform admin: {e}"
        ) from e


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
    register_startup_hook(HOOK_DATABASE_BOOTSTRAP,
                          _database_bootstrap_auto_init, order=15)
    register_startup_hook(HOOK_TENANT_CONVERSATION_BACKFILL,
                          _tenant_conversation_backfill, order=20)


register_fork_startup_hooks()
