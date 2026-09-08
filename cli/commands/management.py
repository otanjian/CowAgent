# encoding:utf-8
"""cow management - Maintenance-window identity bootstrap for the IAM service.

Runs from a stopped/quiet instance (maintenance window) and creates the default
tenant, initial platform admin, built-in roles and organization root in
``identity.db``. Refuses bland default passwords and aborts without committing
usable config when the target directory layout is invalid.

This is the migration-time command referenced by phase P1 (task 2.2): it is
expected to be run once per instance, near the point where you enable
``identity_mode=database``.
"""

import os
import sys

import click

from cli.utils import get_project_root, ensure_sys_path


def _identity_db_path() -> str:
    """Resolve the identity.db path from config, defaulting under the data root."""
    ensure_sys_path()
    from config import get_data_root, conf

    configured = conf().get("identity_db_path")
    if configured:
        return os.path.expanduser(str(configured))
    return os.path.join(get_data_root(), "identity.db")


def _bootstrap(**kwargs) -> None:
    from auth.service import IdentityService, IdentityServiceError

    kwargs.setdefault("allow_weak", False)
    db_path = kwargs.pop("db_path")
    svc = IdentityService(db_path)
    try:
        result = svc.bootstrap(**kwargs)
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()
    click.echo(click.style(f"Created/verified tenant '{result['code']}' (id={result['id']}).", fg="green"))


@click.group(invoke_without_command=True)
@click.pass_context
def management(ctx):
    """Manage the platform identity (maintenance window)."""
    if ctx.invoked_subcommand is None:
        click.echo("Management commands: bootstrap, register")


@management.command("register")
@click.option("--tenant-code", prompt="Tenant code", default="default")
@click.option("--admin-username", prompt="Initial admin username (private owner)", default="admin")
def register(tenant_code, admin_username):
    """In-place migration: register existing content to the default tenant (task 4.1).

    Runs from a stopped instance (maintenance window). Binds every registry Agent
    to the default tenant with the initial admin as private owner, and backfills
    the owner of existing owner-less conversations/runs to that admin. Idempotent:
    re-running does not duplicate bindings or overwrite later ownership splits.
    No content is moved or copied.
    """
    ensure_sys_path()
    from auth.service import IdentityService, IdentityServiceError

    db_path = _identity_db_path()
    svc = IdentityService(db_path)
    try:
        tenant = svc._find_tenant_by_code(tenant_code)
        if not tenant:
            click.echo(click.style(
                f"Tenant '{tenant_code}' does not exist. Run 'cow management bootstrap' first.",
                fg="red"))
            raise click.Abort()
        tid = tenant["id"]
        # initial admin = the normalized --admin-username account, resolved to an
        # exact user (not just the first platform admin). It must be an active
        # platform admin and hold a membership in the target tenant.
        admin = svc._find_user_by_username(admin_username)
        if not admin or not admin.get("active"):
            click.echo(click.style(
                f"Admin account '{admin_username}' not found or disabled.", fg="red"))
            raise click.Abort()
        if not admin.get("is_platform_admin"):
            click.echo(click.style(
                f"Admin account '{admin_username}' is not a platform admin.", fg="red"))
            raise click.Abort()
        in_tenant = svc.get_membership(admin["id"], tid)
        if not in_tenant or not in_tenant.get("active"):
            click.echo(click.style(
                f"Admin account '{admin_username}' is not an active member of tenant "
                f"'{tenant_code}'. Add it before registering.", fg="red"))
            raise click.Abort()
        admin_id = admin["id"]

        # Enumerate every registry Agent and bind to the tenant.
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        agent_ids = [p.id for p in registry.list(include_disabled=True)]
        summary = svc.register_default_tenancy(
            tenant_id=tid, private_owner_user_id=admin_id, agent_ids=agent_ids)

        # Backfill owner of existing conversations for each bound agent workspace.
        backfilled = 0
        for agent_id in agent_ids:
            try:
                profile = registry.get(agent_id, require_enabled=False)
                from agent.memory.conversation_store import get_conversation_store
                store = get_conversation_store(profile.workspace)
                backfilled += store.backfill_owner(admin_id)
            except Exception as e:
                click.echo(click.style(f"  skipped {agent_id}: {e}", fg="yellow"))

        click.echo(click.style(
            f"Registered {summary['bound']} agent(s) (already bound: "
            f"{summary['already_registered']}); backfilled owner on "
            f"{backfilled} conversation(s).", fg="green"))
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()


@management.command("bootstrap")
@click.option("--tenant-code", prompt="Tenant code", default="default")
@click.option("--tenant-name", prompt="Tenant name", default="默认租户")
@click.option("--admin-username", prompt="Platform admin username", default="admin")
@click.option("--admin-display", prompt="Admin display name", default="系统管理员")
@click.option("--shared-root", prompt="Tenant shared root", default="")
@click.option("--password", default=None, help="Admin password. Defaults to 'admin' with --allow-weak.")
@click.option("--allow-weak", is_flag=True, default=False,
              help="Permit a well-known password (e.g. 'admin') for local testing only. "
                   "Never use in production.")
def bootstrap(tenant_code, tenant_name, admin_username, admin_display, shared_root, password, allow_weak):
    """Create the default tenant, initial platform admin, and built-in roles.

    For local testing you may pass ``--allow-weak --password admin`` to create a
    well-known admin/admin account. Without ``--allow-weak`` a strong password is
    required.
    """
    ensure_sys_path()
    if not shared_root:
        from cli.utils import get_workspace_dir
        shared_root = get_workspace_dir()
    if allow_weak:
        password = password or "admin"
    elif password is None:
        password = click.prompt("Admin password (no default)", hide_input=True, confirmation_prompt=True)
    _bootstrap(
        tenant_code=tenant_code,
        tenant_name=tenant_name,
        admin_username=admin_username,
        admin_display=admin_display,
        admin_password=password,
        shared_root=shared_root,
        allow_weak=allow_weak,
        db_path=_identity_db_path(),
    )
