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


def _registered_agent_ids() -> list:
    """Every Agent in the registry, enabled or not (migration sees them all)."""
    ensure_sys_path()
    from agent.registry import get_agent_registry
    return [p.id for p in get_agent_registry().list(include_disabled=True)]


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

    The private owner is deliberately kept for the *non-default* Agents: before
    tenancy existed the content belonged to that one admin, so preserving the
    old visibility is the conservative migration. The tenant's **default** Agent
    is then shared (``private_owner_user_id`` cleared) — otherwise every other
    member would be locked out of the Agent a conversation resolves to when the
    user picks none (task 5b.9).
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
        if not svc.is_platform_admin_user(admin["id"]):
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

        # Enumerate every registry Agent and bind to the tenant. The private
        # owner here is deliberate, not inferred: pre-tenancy content belonged
        # to that admin. What it must never do is leave the tenant's *default*
        # private, which would lock every other member out — corrected below.
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        agent_ids = _registered_agent_ids()
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

        # Close the loop: if the Agent this tenant resolves as its default is
        # one of the private ones just registered, share it. Members can then
        # chat without picking an Agent, while their sessions stay private to
        # their owner (a different dimension from the Agent's binding).
        shared = svc.ensure_shared_default_agents()

        click.echo(click.style(
            f"Registered {summary['bound']} agent(s) (already bound: "
            f"{summary['already_registered']}); backfilled owner on "
            f"{backfilled} conversation(s).", fg="green"))
        if shared["updated"]:
            click.echo(click.style(
                f"Shared {shared['updated']} tenant default Agent(s) so members "
                f"can chat without selecting one.", fg="green"))
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()


@management.command("share-default-agents")
@click.option("--tenant-code", default=None,
              help="Limit to one tenant. Defaults to every tenant.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Report what would change without writing.")
def share_default_agents(tenant_code, dry_run):
    """Correct existing private defaults so the tenant default is shared.

    Earlier writes made an Agent private to whoever created or registered it
    (the initial admin, or the user who adopted it from the console). When such
    an Agent is also the tenant's default, every other member is refused — which
    is exactly the "conversation must pick an Agent" symptom. This is the
    operational half of that fix; the code path no longer introduces private
    defaults.

    Idempotent, audited, and scoped: only the Agent each tenant actually
    resolves as its default is touched, so unrelated private Agents keep their
    owner. Run from a stopped/maintenance window and back up ``identity.db``
    first.
    """
    ensure_sys_path()
    from auth.service import IdentityService, IdentityServiceError

    db_path = _identity_db_path()
    svc = IdentityService(db_path)

    try:
        if dry_run:
            report = _preview_shared_default_corrections(svc, tenant_code)
            if not report:
                click.echo("No private default Agent needs correcting.")
                return
            for tid, agent_id, owner in report:
                click.echo(
                    f"  tenant {tid}: default Agent '{agent_id}' is private to "
                    f"{owner} -> would become tenant-shared")
            click.echo(click.style(
                f"{len(report)} Agent(s) would become tenant-shared. "
                f"Re-run without --dry-run to apply.", fg="yellow"))
            return

        summary = svc.ensure_shared_default_agents()
        for tid, agent_id in sorted(summary["resolved"].items()):
            click.echo(f"  tenant {tid} resolves default Agent '{agent_id}'")
        click.echo(click.style(
            f"Updated {summary['updated']} Agent(s) to tenant-shared. "
            f"Safe to re-run (a second run reports 0).", fg="green"))
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()


def _preview_shared_default_corrections(svc, tenant_code=None):
    """(tenant_id, agent_id, owner) for defaults that are still private."""
    rows = []
    tenants = svc.list_tenants()
    if tenant_code:
        tenants = [t for t in tenants if t.get("code") == tenant_code]
        if not tenants:
            click.echo(click.style(
                f"Tenant '{tenant_code}' does not exist.", fg="red"))
            raise click.Abort()
    for tenant in tenants:
        tenant_id = tenant["id"]
        target = (svc.tenant_default_agent_id(tenant_id)
                  or svc.resolved_default_agent_id(tenant_id))
        if not target:
            continue
        binding = svc.get_agent_binding(target)
        if binding and binding.get("private_owner_user_id") is not None:
            rows.append((tenant_id, target, binding["private_owner_user_id"]))
    return rows


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
