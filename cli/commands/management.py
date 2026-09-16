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

        # Close the loop: the Agent this tenant resolves as its default is one of
        # the private ones just registered, so share *that* one explicitly.
        # Members can then chat without picking an Agent, while their sessions
        # stay private to their owner (a different dimension from the Agent's
        # binding). Sharing an Agent is a deliberate act, so this names its
        # target and is audited as an explicit share — no correction path may
        # publish a private Agent behind the operator's back (change
        # ``unify-console-by-data-scope``: 租户默认任命不改变私有归属).
        #
        # The question is asked *with* the register-designated owner as subject.
        # A subject-less caller has no private pool to fall back into by design
        # (task 4.8: 绝不回落他人私有对象), so the bare form answers ``None``
        # here — the tenant has no pointer yet and nothing shared to resolve to —
        # and the share would never fire. Naming the subject asks exactly what a
        # send from that member asks, so the Agent this command publishes is the
        # one resolution would actually have served.
        default_agent_id = (svc.tenant_default_agent_id(tid)
                            or svc.resolved_default_agent_id(tid, admin_id))
        binding = svc.get_agent_binding(default_agent_id) if default_agent_id else None
        if binding and binding.get("private_owner_user_id") is not None:
            svc.make_agent_tenant_shared(
                agent_id=default_agent_id, actor_user_id=admin_id)

        click.echo(click.style(
            f"Registered {summary['bound']} agent(s) (already bound: "
            f"{summary['already_registered']}); backfilled owner on "
            f"{backfilled} conversation(s).", fg="green"))
        if default_agent_id and binding and binding.get("private_owner_user_id") is not None:
            click.echo(click.style(
                f"Shared the tenant default Agent '{default_agent_id}' so members "
                f"can chat without selecting one.", fg="green"))
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()


@management.command("repair-tenant-defaults")
@click.option("--tenant-code", default=None,
              help="Limit to one tenant. Defaults to every tenant.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Report what would change without writing.")
def repair_tenant_defaults(tenant_code, dry_run):
    """Release tenant defaults that could never have resolved, keeping owners.

    An Agent may not be both private and the tenant's default: the default is the
    entry every member shares. The historical fix for that was to clear the
    private owner, which silently published somebody's Agent — and the change
    survived the default being moved away, so the Agent kept reading as a
    tenant-level one (it showed up in every member's picker). This command only
    releases the *pointer*; ownership is left exactly as it was, and sharing an
    Agent stays an explicit console action.

    Idempotent and audited (``tenant.default_agent.repaired``), and the store
    performs the same repair at open. Run it to preview or to re-check an
    installation; back up ``identity.db`` first.
    """
    ensure_sys_path()
    from auth.service import IdentityService, IdentityServiceError

    db_path = _identity_db_path()
    svc = IdentityService(db_path)

    try:
        if dry_run:
            report = _preview_illegal_tenant_defaults(svc, tenant_code)
            if not report:
                click.echo("No illegal tenant default needs releasing.")
                return
            for tid, agent_id, owner in report:
                click.echo(
                    f"  tenant {tid}: default Agent '{agent_id}' is private to "
                    f"{owner} -> would release the pointer (owner preserved)")
            click.echo(click.style(
                f"{len(report)} pointer(s) would be released. "
                f"Re-run without --dry-run to apply.", fg="yellow"))
            return

        summary = svc.release_illegal_tenant_defaults()
        for tid, agent_id, owner in summary["released"]:
            click.echo(
                f"  tenant {tid}: released default Agent '{agent_id}' "
                f"(still private to {owner})")
        for tid, agent_id in sorted(summary["resolved"].items()):
            click.echo(f"  tenant {tid} resolves default Agent '{agent_id}'")
        click.echo(click.style(
            f"Released {summary['updated']} illegal tenant default pointer(s); "
            f"no owner was changed. Safe to re-run (a second run reports 0).",
            fg="green"))
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()


management.add_command(
    click.Command(
        "share-default-agents",
        callback=repair_tenant_defaults.callback,
        params=repair_tenant_defaults.params,
        help="Deprecated alias of repair-tenant-defaults. It no longer shares"
             " anything: that repair released the pointer instead.",
    )
)


@management.command("restore-private-owner")
@click.option("--agent-id", required=True,
              help="The tenant-shared Agent to narrow back to one owner.")
@click.option("--owner-username", required=True,
              help="The member who owns the Agent (an active member of its tenant).")
@click.option("--actor-username", default=None,
              help="The tenant admin performing the repair, recorded on the"
                   " audit event. Omit for an operator run with direct database"
                   " access; when given, the account must actually qualify.")
@click.option("--reason", default=None,
              help="Recorded on the audit event, e.g. the incident or date.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Report what would change without writing.")
def restore_private_owner(agent_id, owner_username, actor_username, reason, dry_run):
    """Give a tenant-shared Agent its private owner back.

    An Agent can be shared without anyone deciding to share it: the appointment
    path used to clear ``private_owner_user_id`` to keep a private Agent usable
    as the tenant default, and the Agent then read as a tenant-level one — it
    appeared in every member's picker while its owner had never shared it.
    ``repair-tenant-defaults`` releases the illegal *pointer*; this command
    undoes the publication that already happened.

    Deliberately narrow, because it takes the Agent back out of other members'
    hands: only an unowned (shared) Agent is repaired, the owner must be an
    active member of the Agent's tenant, and a tenant default stays shared (move
    the default first). Idempotent and audited
    (``agent.restore_private_owner``). Run ``--dry-run`` first, and back up
    ``identity.db`` before applying.
    """
    ensure_sys_path()
    from auth.service import IdentityService, IdentityServiceError

    db_path = _identity_db_path()
    svc = IdentityService(db_path)

    owner = svc._find_user_by_username(owner_username)
    if not owner or not owner.get("active"):
        click.echo(click.style(
            f"Account '{owner_username}' not found or disabled.", fg="red"))
        raise click.Abort()
    actor_id = None
    if actor_username:
        actor = svc._find_user_by_username(actor_username)
        if not actor or not actor.get("active"):
            click.echo(click.style(
                f"Actor account '{actor_username}' not found or disabled.", fg="red"))
            raise click.Abort()
        actor_id = actor["id"]

    try:
        result = svc.restore_private_agent_owner(
            agent_id=agent_id, owner_user_id=owner["id"],
            actor_user_id=actor_id, reason=reason, dry_run=dry_run)
    except IdentityServiceError as e:
        # A refusal is the answer, not a crash: name the reason and the state
        # that has to move first.
        click.echo(click.style(f"Refused: {e}", fg="red"))
        raise click.Abort()

    if not result["changed"]:
        click.echo(click.style(
            f"Nothing to do: '{agent_id}' is already private to "
            f"{owner_username}.", fg="green"))
        return
    if dry_run:
        click.echo(
            f"  tenant {result['tenant_id']}: '{agent_id}' is shared "
            f"-> would become private to {owner_username} ({owner['id']})")
        click.echo(click.style(
            "1 repair would be applied. Re-run without --dry-run to apply.",
            fg="yellow"))
        return
    click.echo(click.style(
        f"Restored private ownership: '{agent_id}' now belongs to "
        f"{owner_username} ({owner['id']}). Other members no longer see it.",
        fg="green"))


def _preview_illegal_tenant_defaults(svc, tenant_code=None):
    """(tenant_id, agent_id, owner) for tenant defaults that are still private.

    Only a *stored* pointer can be illegal: a private Agent that merely resolves
    as the fallback is not a default, so there is nothing to release.
    """
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
        target = svc.tenant_default_agent_id(tenant_id)
        if not target:
            continue
        binding = svc.get_agent_binding(target)
        if binding and binding.get("private_owner_user_id") is not None:
            rows.append((tenant_id, target, binding["private_owner_user_id"]))
    return rows


@management.command("personalize-personal-agents")
@click.option("--tenant-code", default=None,
              help="Limit to one tenant. Defaults to every tenant.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Report what would change without writing.")
def personalize_personal_agents(tenant_code, dry_run):
    """Re-author the owner-facing wording of existing personal assistants.

    Each member's private assistant inherits the source template's prose, which
    describes whoever that template was written for. Copies made before the
    owner-facing templates existed therefore call their owner "管理员" even when
    the owner is an ordinary member -- and nothing re-runs provisioning for an
    existing member, so those copies keep the wrong wording until corrected.

    Scoped to Agents that a binding marks as privately owned, so the source
    template, the tenant's shared default and every ordinary Agent are out of
    reach. Idempotent: a second run reports 0. Back up ``identity.db`` and the
    roster first when running without ``--dry-run``.
    """
    ensure_sys_path()
    from auth.service import IdentityService, IdentityServiceError
    from agent.personal_assistant import get_personal_assistant_provisioner

    db_path = _identity_db_path()
    svc = IdentityService(db_path)

    tenant_id = None
    if tenant_code:
        matches = [t for t in svc.list_tenants(status="all")
                   if t.get("code") == tenant_code]
        if not matches:
            click.echo(click.style(
                f"Tenant '{tenant_code}' does not exist.", fg="red"))
            raise click.Abort()
        tenant_id = matches[0]["id"]

    try:
        report = get_personal_assistant_provisioner().personalize_existing(
            tenant_id=tenant_id, dry_run=dry_run)
    except IdentityServiceError as e:
        click.echo(click.style(f"Failed: {e}", fg="red"))
        raise click.Abort()

    verb = "would be updated" if dry_run else "updated"
    for tid, agent_id, name in report["changed"]:
        click.echo(f"  tenant {tid}: '{agent_id}' ({name}) -> {verb}")
    for tid, agent_id, reason in report["failed"]:
        click.echo(click.style(
            f"  tenant {tid}: '{agent_id}' -> skipped ({reason})", fg="yellow"))

    if report["failed"]:
        click.echo(click.style(
            f"{len(report['failed'])} assistant(s) could not be examined; "
            f"see the reasons above.", fg="yellow"))
    if dry_run and report["changed"]:
        click.echo(click.style(
            f"{len(report['changed'])} personal assistant(s) would be re-authored. "
            f"Re-run without --dry-run to apply.", fg="yellow"))
    elif dry_run:
        click.echo("No personal assistant needs re-authoring.")
    else:
        click.echo(click.style(
            f"Updated {len(report['changed'])} personal assistant(s). "
            f"Safe to re-run (a second run reports 0).", fg="green"))



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
