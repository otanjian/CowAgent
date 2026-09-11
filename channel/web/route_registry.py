# encoding:utf-8
"""Authoritative route registry: the single source of the console URL table
(``_WEB_URLS``) and the HTTP authorization policy (``ROUTE_POLICY``).

Why this module exists
----------------------
``channel/web/web_channel.py`` (URL table) and ``auth/http_policy.py`` (policy
table) used to be two hand-maintained literals with no machine check between
them. Measured drift: 115 patterns in the URL table vs 110 in the policy table,
and the 5 unregistered paths were not merely unpoliced -- an unlisted path made
``_match_policy`` report "unknown URL", so the request bypassed the gate entirely
and reached a handler that only did ``_require_auth()``.

Every route is therefore registered **once**, here, with:

* ``pattern``  -- the raw web.py pattern (no ``^``/``$``);
* ``handler``  -- the class name resolved from ``web_channel`` globals;
* ``source``   -- ``upstream`` or ``fork:<area>``, so fork additions stay
  identifiable and can migrate to :func:`register_fork_routes` over time;
* ``methods``  -- HTTP method -> policy entry (``policy``, optional
  ``permission``, ``comment``).

Registration is deliberate: policies are transcribed from the previously
verified ``ROUTE_POLICY`` entries, and every method added, dropped or newly
registered during this migration is recorded in the change's ``evidence.md``
with its rationale. There is intentionally NO "transcribe and default"
bootstrap: an unverified policy is a bug, not a default.

Coverage invariant
------------------
:func:`check_route_coverage` cross-checks three legs, and any mismatch fails:

1. every registered method has a policy entry (the two tables are derived from
   this module, so this leg is structural);
2. every policy entry belongs to a registered route;
3. **the registered method set matches what the handler classes actually
   implement** (introspected). This is the only non-tautological leg: it is what
   catches "handler implements POST but only GET was registered" and dead
   registrations such as the removed ``GET /api/sessions/{id}``.

Ordering
--------
Table order is behavior: ``web.application`` and ``_match_policy`` both take the
first match, so specific patterns stay before their generic siblings
(``/api/todos/summary`` before ``/api/todos/(.*)``). New routes MUST be inserted
at the right position, not appended blindly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: HTTP methods the registry may declare.
HTTP_METHODS: Tuple[str, ...] = ("GET", "POST", "PUT", "DELETE", "PATCH")

#: Authorization domains a policy entry may use.
POLICIES = frozenset({"public", "personal", "platform", "tenant", "closed"})


class RouteEntry:
    """One authoritative route: pattern, handler, owner, per-method policy."""

    __slots__ = ("pattern", "handler", "source", "methods")

    def __init__(self, pattern: str, handler: str, source: str,
                 methods: Mapping[str, dict]) -> None:
        self.pattern = pattern
        self.handler = handler
        self.source = source
        self.methods = dict(methods)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "RouteEntry(%r, %r, %r, %r)" % (
            self.pattern, self.handler, self.source, sorted(self.methods))


def P(policy: str, permission: str = "", comment: str = "",
      tenant_from_resource: bool = False) -> dict:
    """Build one method's policy entry in the historical ``ROUTE_POLICY`` shape.

    ``tenant_from_resource`` marks a ``tenant`` route whose tenant is *derived
    from the addressed resource* rather than from an explicit selection. The
    gate then authenticates the caller without demanding ``X-Tenant-ID``, and
    the handler re-derives the tenant from the owned resource. ``GET /stream``
    is the measured case: native ``EventSource`` can send the session cookie but
    cannot add a header, so the reconnect resolves the tenant from the recorded
    request (``web_channel._stream_identity_scope``).
    """
    entry: Dict[str, Any] = {"policy": policy}
    if permission:
        entry["permission"] = permission
    if tenant_from_resource:
        entry["tenant_from_resource"] = True
    if comment:
        entry["comment"] = comment
    return entry


#: The authoritative list. Order is significant (see module docstring).
ROUTES: Tuple[RouteEntry, ...] = (
    RouteEntry("/", "RootHandler", "upstream", {"GET": P("public", comment="console root")}),
    RouteEntry("/api/health", "HealthHandler", "upstream", {"GET": P("public", comment="health probe")}),
    RouteEntry("/auth/login", "AuthLoginHandler", "upstream", {"POST": P("public", comment="login (db + legacy)")}),
    RouteEntry("/auth/check", "AuthCheckHandler", "upstream", {"GET": P("public", comment="auth state probe")}),
    RouteEntry("/auth/logout", "AuthLogoutHandler", "upstream", {"POST": P("public", comment="logout")}),
    RouteEntry("/auth/password", "DbAuthPasswordHandler", "fork:self-account", {"POST": P("personal", comment="self password change")}),
    RouteEntry("/auth/me", "DbAuthMeHandler", "fork:self-account", {"GET": P("personal", comment="self projection")}),
    RouteEntry("/auth/context", "DbAuthContextHandler", "fork:self-account", {"GET": P("tenant", comment="current-tenant capability")}),
    RouteEntry("/auth/profile", "DbSelfProfileHandler", "fork:self-account", {"PATCH": P("personal", comment="self profile edit")}),
    RouteEntry("/auth/profile/avatar", "DbSelfAvatarHandler", "fork:self-account", {"GET": P("personal", comment="fetch self avatar"), "POST": P("personal", comment="upload self avatar")}),
    RouteEntry("/api/platform/users", "PlatformUsersHandler", "fork:platform-console", {"GET": P("platform", comment="list accounts")}),
    RouteEntry("/api/platform/users/([^/]+)/password", "PlatformUserPasswordHandler", "fork:platform-console", {"POST": P("platform", comment="reset platform account password")}),
    RouteEntry("/api/platform/users/([^/]+)/external-identities", "PlatformUserExternalIdentitiesHandler", "fork:platform-console", {"GET": P("platform", comment="list external identity bindings for a user"), "POST": P("platform", comment="bind external identity to a user")}),
    RouteEntry("/api/platform/users/([^/]+)/external-identities/([^/]+)", "PlatformUserExternalIdentityHandler", "fork:platform-console", {"DELETE": P("platform", comment="delete an external identity binding")}),
    RouteEntry("/api/platform/external-identity-attempts", "PlatformExternalIdentityAttemptsHandler", "fork:platform-console", {"GET": P("platform", comment="unbound inbound authors (all tenants)")}),
    RouteEntry("/api/platform/users/([^/]+)", "PlatformUsersHandler", "fork:platform-console", {"PATCH": P("platform", comment="enable/disable or set platform-admin flag")}),
    RouteEntry("/api/platform/tenants", "PlatformTenantsHandler", "fork:platform-console", {"GET": P("platform", comment="list tenants"), "POST": P("platform", comment="create tenant")}),
    RouteEntry("/api/platform/tenants/([^/]+)/admins", "PlatformTenantAdminsHandler", "fork:platform-console", {"GET": P("platform", comment="read current tenant admins"), "POST": P("platform", comment="configure tenant admin")}),
    RouteEntry("/api/platform/tenants/([^/]+)/agents", "PlatformTenantAgentsHandler", "fork:platform-console", {"GET": P("platform", comment="list tenant agents and copy candidates"), "POST": P("platform", comment="copy agents into the tenant")}),
    RouteEntry("/api/platform/tenants/([^/]+)/roles", "PlatformTenantRolesHandler", "fork:platform-console", {"GET": P("platform", comment="target-tenant role list (platform)"), "POST": P("platform", comment="target-tenant role create (platform)")}),
    RouteEntry("/api/platform/tenants/([^/]+)/roles/([^/]+)", "PlatformTenantRoleHandler", "fork:platform-console", {"POST": P("platform", comment="target-tenant role update (platform)"), "DELETE": P("platform", comment="target-tenant role delete (platform)")}),
    RouteEntry("/api/platform/tenants/([^/]+)/authorization/catalog", "PlatformTenantAuthorizationCatalogHandler", "fork:platform-console", {"GET": P("platform", comment="target-tenant auth catalog (platform)")}),
    RouteEntry("/api/platform/tenants/([^/]+)/resources", "PlatformTenantResourcesHandler", "fork:platform-console", {"GET": P("platform", comment="tenant global resource grants (platform)"), "PUT": P("platform", comment="set tenant global resource grants (platform)")}),
    RouteEntry("/api/platform/tenants/([^/]+)", "PlatformTenantHandler", "fork:platform-console", {"GET": P("platform", comment="tenant detail"), "POST": P("platform", comment="edit tenant status")}),
    RouteEntry("/api/tenant", "TenantInfoHandler", "fork:tenant-console", {"GET": P("tenant", "tenant.info.read", comment="current tenant info")}),
    RouteEntry("/api/tenant/members", "TenantMembersHandler", "fork:tenant-console", {"GET": P("tenant", "tenant.members.read", comment="list members"), "POST": P("tenant", comment="create/bind member (tenant_admin)")}),
    RouteEntry("/api/tenant/members/([^/]+)/external-identities", "TenantMemberExternalIdentitiesHandler", "fork:tenant-console", {"GET": P("tenant", "tenant.members.read", comment="list a member's external identity bindings"), "POST": P("tenant", comment="bind external identity to a member")}),
    RouteEntry("/api/tenant/members/([^/]+)/external-identities/([^/]+)", "TenantMemberExternalIdentityHandler", "fork:tenant-console", {"DELETE": P("tenant", comment="delete a member's external identity binding")}),
    RouteEntry("/api/tenant/members/([^/]+)", "TenantMemberHandler", "fork:tenant-console", {"POST": P("tenant", comment="update member (tenant_admin)")}),
    RouteEntry("/api/tenant/external-identity-attempts", "ExternalIdentityAttemptsHandler", "fork:tenant-console", {"GET": P("tenant", comment="unbound inbound authors for this tenant")}),
    RouteEntry("/api/tenant/roles/([^/]+)", "TenantRoleHandler", "fork:tenant-console", {"POST": P("tenant", comment="update role (tenant_admin)"), "DELETE": P("tenant", comment="delete role (tenant_admin)")}),
    RouteEntry("/api/tenant/roles", "TenantRolesHandler", "fork:tenant-console", {"GET": P("tenant", "tenant.members.read", comment="list roles"), "POST": P("tenant", comment="create role (tenant_admin)")}),
    RouteEntry("/api/tenant/authorization/catalog", "TenantAuthorizationCatalogHandler", "fork:tenant-console", {"GET": P("tenant", comment="authorization resource catalog (assign/use)")}),
    RouteEntry("/api/tenant/permissions", "TenantPermissionsHandler", "fork:tenant-console", {"GET": P("tenant", comment="permission catalog")}),
    RouteEntry("/api/tenant/departments", "TenantDepartmentsHandler", "fork:tenant-console", {"GET": P("tenant", "tenant.org.read", comment="list departments"), "POST": P("tenant", comment="create department (tenant_admin)")}),
    RouteEntry("/api/tenant/departments/([^/]+)", "TenantDepartmentHandler", "fork:tenant-console", {"PUT": P("tenant", comment="update/move department (tenant_admin)"), "DELETE": P("tenant", comment="delete department (tenant_admin)")}),
    RouteEntry("/api/identity/audit", "IdentityAuditHandler", "fork:identity-console", {"GET": P("tenant", comment="identity audit (platform/tenant_admin)")}),
    RouteEntry("/api/identity/administered-tenants", "IdentityAdministeredTenantsHandler", "fork:identity-console", {"GET": P("personal", comment="tenants the actor administers (personal scope)")}),
    RouteEntry("/api/tenant/channels", "TenantChannelsHandler", "fork:tenant-console", {"GET": P("tenant", comment="list this tenant's channel instances (tenant_admin)"), "POST": P("tenant", comment="create this tenant's channel instance (tenant_admin)")}),
    RouteEntry("/api/tenant/channels/([^/]+)/active", "TenantChannelActiveHandler", "fork:tenant-console", {"POST": P("tenant", comment="enable/disable this tenant's channel instance (tenant_admin)")}),
    RouteEntry("/api/tenant/channels/([^/]+)", "TenantChannelHandler", "fork:tenant-console", {"POST": P("tenant", comment="edit this tenant's channel instance (tenant_admin)")}),
    RouteEntry("/api/admin/overview", "AdminOverviewHandler", "fork:admin-console", {"GET": P("tenant", comment="admin console KPI overview (platform/tenant_admin)")}),
    RouteEntry("/message", "MessageHandler", "upstream", {"POST": P("tenant", comment="send message")}),
    RouteEntry("/upload", "UploadHandler", "upstream", {"POST": P("tenant", comment="file upload")}),
    RouteEntry("/uploads/(.*)", "UploadsHandler", "upstream", {"GET": P("tenant", comment="serve upload")}),
    RouteEntry("/api/file", "FileServeHandler", "upstream", {"GET": P("tenant", comment="file serve")}),
    RouteEntry("/preview/(.+)", "PreviewHandler", "upstream", {"GET": P("public", comment="preview (capability token)")}),
    RouteEntry("/api/workspace/tree", "WorkspaceTreeHandler", "upstream", {"GET": P("closed", comment="workspace tree (deferred)")}),
    RouteEntry("/api/workspace/search", "WorkspaceSearchHandler", "upstream", {"GET": P("closed", comment="workspace search (deferred)")}),
    RouteEntry("/api/workspace/resolve", "WorkspaceResolveHandler", "upstream", {"GET": P("closed", comment="workspace resolve (deferred)")}),
    RouteEntry("/api/workspace/meta", "WorkspaceMetaHandler", "upstream", {"GET": P("closed", comment="workspace meta (deferred)")}),
    RouteEntry("/api/workspace/read", "WorkspaceReadHandler", "upstream", {"GET": P("closed", comment="workspace read (deferred)")}),
    RouteEntry("/api/workspace/write", "WorkspaceWriteHandler", "upstream", {"POST": P("closed", comment="workspace write (deferred)")}),
    RouteEntry("/api/projects", "ProjectsHandler", "upstream", {"GET": P("tenant", comment="projects")}),
    RouteEntry("/api/projects/select", "ProjectSelectHandler", "upstream", {"POST": P("tenant", comment="project select")}),
    RouteEntry("/api/projects/create", "ProjectCreateHandler", "upstream", {"POST": P("tenant", comment="project create")}),
    RouteEntry("/api/projects/browse", "ProjectBrowseHandler", "upstream", {"GET": P("closed", comment="project browse (deferred)")}),
    RouteEntry("/api/projects/order", "ProjectOrderHandler", "upstream", {"POST": P("tenant", comment="project order")}),
    RouteEntry("/api/projects/manage", "ProjectManageHandler", "upstream", {"PUT": P("tenant", comment="project rename"), "DELETE": P("tenant", comment="project delete")}),
    RouteEntry("/api/voice/asr", "VoiceAsrHandler", "upstream", {"POST": P("tenant", comment="voice ASR")}),
    RouteEntry("/api/voice/tts", "VoiceTtsHandler", "upstream", {"POST": P("tenant", comment="voice TTS")}),
    RouteEntry("/poll", "PollHandler", "upstream", {"POST": P("tenant", comment="poll response")}),
    RouteEntry("/stream", "StreamHandler", "upstream", {"GET": P("tenant", comment="SSE stream (tenant derived from the owned request)", tenant_from_resource=True)}),
    RouteEntry("/cancel", "CancelHandler", "upstream", {"POST": P("tenant", comment="cancel request")}),
    RouteEntry("/chat", "ChatHandler", "upstream", {"GET": P("tenant", comment="chat page")}),
    RouteEntry("/admin", "ChatHandler", "fork:admin-console", {"GET": P("tenant", comment="admin console shell (same handler as /chat)")}),
    RouteEntry("/v1/chat/completions", "OpenAIChatCompletionsHandler", "upstream", {"POST": P("tenant", comment="OpenAI-compatible chat")}),
    RouteEntry("/config", "ConfigHandler", "upstream", {"GET": P("platform", comment="platform config"), "POST": P("platform", comment="platform config save")}),
    RouteEntry("/api/models", "ModelsHandler", "upstream", {"GET": P("platform", comment="models (platform admin)"), "POST": P("platform", comment="models save (platform admin)")}),
    RouteEntry("/api/channels", "ChannelsHandler", "upstream", {"GET": P("platform", comment="instance-level channels (platform admin)"), "POST": P("platform", comment="instance-level channels save/connect")}),
    RouteEntry("/api/weixin/qrlogin", "WeixinQrHandler", "upstream", {"GET": P("closed", comment="weixin qr (deferred)"), "POST": P("closed", comment="poll QR status / start channel after login")}),
    RouteEntry("/api/feishu/register", "FeishuRegisterHandler", "upstream", {"GET": P("personal", comment="start a feishu register session"), "POST": P("personal", comment="poll the caller's own register session")}),
    RouteEntry("/api/tools", "ToolsHandler", "upstream", {"GET": P("tenant", comment="tools")}),
    RouteEntry("/api/skills", "SkillsHandler", "upstream", {"GET": P("tenant", comment="skills"), "POST": P("tenant", comment="skills toggle")}),
    RouteEntry("/api/skills/content", "SkillContentHandler", "upstream", {"GET": P("tenant", comment="skill content"), "POST": P("tenant", comment="skill content write")}),
    RouteEntry("/api/memory", "MemoryHandler", "upstream", {"GET": P("closed", comment="memory (deferred)")}),
    RouteEntry("/api/memory/content", "MemoryContentHandler", "upstream", {"GET": P("closed", comment="memory content (deferred)")}),
    RouteEntry("/api/knowledge/list", "KnowledgeListHandler", "upstream", {"GET": P("closed", comment="knowledge (deferred)")}),
    RouteEntry("/api/knowledge/read", "KnowledgeReadHandler", "upstream", {"GET": P("closed", comment="knowledge read (deferred)")}),
    RouteEntry("/api/knowledge/graph", "KnowledgeGraphHandler", "upstream", {"GET": P("closed", comment="knowledge graph (deferred)")}),
    RouteEntry("/api/knowledge/action", "KnowledgeActionHandler", "upstream", {"POST": P("closed", comment="knowledge action (deferred)")}),
    RouteEntry("/api/knowledge/import", "KnowledgeImportHandler", "upstream", {"POST": P("closed", comment="knowledge import (deferred)")}),
    RouteEntry("/api/scheduler", "SchedulerHandler", "upstream", {"GET": P("closed", comment="scheduler (group 5)")}),
    RouteEntry("/api/scheduler/run", "SchedulerRunHandler", "upstream", {"POST": P("closed", comment="scheduler run (group 5)")}),
    RouteEntry("/api/scheduler/toggle", "SchedulerToggleHandler", "upstream", {"POST": P("closed", comment="scheduler toggle (group 5)")}),
    RouteEntry("/api/scheduler/update", "SchedulerUpdateHandler", "upstream", {"POST": P("closed", comment="scheduler update (group 5)")}),
    RouteEntry("/api/scheduler/delete", "SchedulerDeleteHandler", "upstream", {"POST": P("closed", comment="scheduler delete (group 5)")}),
    RouteEntry("/api/todos", "TodosHandler", "fork:todos", {"GET": P("tenant", comment="todos (own)"), "POST": P("tenant", comment="create (auth + CSRF enforced in handler)")}),
    RouteEntry("/api/todos/summary", "TodoSummaryHandler", "fork:todos", {"GET": P("tenant", comment="todo summary")}),
    RouteEntry("/api/todos/(.*)/events", "TodoEventsHandler", "fork:todos", {"GET": P("tenant", comment="todo events")}),
    RouteEntry("/api/todos/(.*)/source", "TodoSourceHandler", "fork:todos", {"GET": P("tenant", comment="todo source")}),
    RouteEntry("/api/todos/(.*)", "TodoDetailHandler", "fork:todos", {"GET": P("tenant", comment="todo detail"), "PATCH": P("tenant", comment="field-edit / target-state update (version-cas)")}),
    RouteEntry("/api/agents", "AgentsHandler", "upstream", {"GET": P("tenant", comment="agents"), "POST": P("tenant", comment="agent create/update/archive/delete/team-bind")}),
    RouteEntry("/api/agents/([^/]+)/avatar", "AgentAvatarHandler", "upstream", {"GET": P("tenant", comment="agent avatar"), "POST": P("tenant", comment="agent avatar upload")}),
    RouteEntry("/api/agents/([^/]+)/files/([^/]+)", "AgentCoreFileHandler", "upstream", {"GET": P("tenant", comment="agent core file"), "PUT": P("tenant", comment="agent core file save")}),
    RouteEntry("/api/sessions", "SessionsHandler", "upstream", {"GET": P("tenant", comment="sessions")}),
    RouteEntry("/api/sessions/(.*)/generate_title", "SessionTitleHandler", "upstream", {"POST": P("tenant", comment="session title")}),
    RouteEntry("/api/prompt/optimize", "PromptOptimizeHandler", "upstream", {"POST": P("tenant", comment="prompt optimize")}),
    RouteEntry("/api/sessions/(.*)/clear_context", "SessionClearContextHandler", "upstream", {"POST": P("tenant", comment="clear context")}),
    RouteEntry("/api/sessions/(.*)/settings", "SessionSettingsHandler", "upstream", {"GET": P("tenant", comment="session settings (read effective model/permission)"), "POST": P("tenant", comment="session settings")}),
    RouteEntry("/api/sessions/(.*)", "SessionDetailHandler", "upstream", {"PUT": P("tenant", comment="session rename / pin"), "DELETE": P("tenant", comment="delete session")}),
    RouteEntry("/api/history", "HistoryHandler", "upstream", {"GET": P("tenant", comment="history")}),
    RouteEntry("/api/messages/delete", "MessageDeleteHandler", "upstream", {"POST": P("tenant", comment="delete message")}),
    RouteEntry("/api/logs/download", "LogsDownloadHandler", "upstream", {"GET": P("platform", comment="logs download (process-global run.log; platform control plane)")}),
    RouteEntry("/api/logs", "LogsHandler", "upstream", {"GET": P("platform", comment="logs (process-global run.log; platform control plane)")}),
    RouteEntry("/api/version", "VersionHandler", "upstream", {"GET": P("public", comment="version")}),
    RouteEntry("/api/branding/public", "BrandingPublicHandler", "fork:branding", {"GET": P("public", comment="public branding")}),
    RouteEntry("/api/branding", "BrandingManageHandler", "fork:branding", {"GET": P("tenant", comment="branding manage"), "POST": P("tenant", comment="branding save")}),
    RouteEntry("/api/branding/reset", "BrandingResetHandler", "fork:branding", {"POST": P("tenant", comment="branding reset")}),
    RouteEntry("/api/branding/assets/(.*)", "BrandingAssetHandler", "fork:branding", {"GET": P("public", comment="branding asset")}),
    RouteEntry("/api/scenes", "ScenesHandler", "fork:scenes", {"GET": P("tenant", "chat.use", comment="scene catalog for the current tenant (chat consumer)")}),
    RouteEntry("/api/scenes/activate", "SceneActivateHandler", "fork:scenes", {"POST": P("tenant", "chat.use", comment="activate a scene in this tenant (state change; origin+CSRF in handler)")}),
    RouteEntry("/api/scenes/workbench/import", "SceneWorkbenchImportHandler", "fork:scenes", {"POST": P("tenant", "chat.use", comment="import workbench content into this tenant's shared root (state change; origin+CSRF in handler)")}),
    RouteEntry("/mcp/oauth/callback", "McpOAuthCallbackHandler", "upstream", {"GET": P("public", comment="MCP oauth callback")}),
    RouteEntry("/assets/(.*)", "AssetsHandler", "upstream", {"GET": P("public", comment="static assets")}),
)


#: Routes added at runtime by fork modules through :func:`register_fork_routes`.
_FORK_ROUTES: List[RouteEntry] = []


def register_fork_routes(*entries: RouteEntry) -> None:
    """Register fork-owned routes without editing the core literal above.

    Fork modules that add routes MUST be imported before the derived tables are
    first built (``web_channel._WEB_URLS`` / ``http_policy.ROUTE_POLICY`` at
    import time). :func:`_load_fork_extensions` provides that hook for a
    ``channel/web/fork_routes.py`` module, which may call this function at its
    own import.
    """
    for entry in entries:
        _validate_entry(entry)
    _FORK_ROUTES.extend(entries)


def all_routes() -> List[RouteEntry]:
    """The core registry plus any fork routes registered so far."""
    return list(ROUTES) + list(_FORK_ROUTES)


def derive_web_urls(routes: Optional[Sequence[RouteEntry]] = None) -> Tuple[str, ...]:
    """Flatten entries into the ``web.application`` (pattern, handler) tuple."""
    out: List[str] = []
    for entry in all_routes() if routes is None else routes:
        out.append(entry.pattern)
        out.append(entry.handler)
    return tuple(out)


def derive_route_policy(routes: Optional[Sequence[RouteEntry]] = None) -> Dict[str, Dict[str, dict]]:
    """Build the ``{pattern: {METHOD: entry}}`` policy mapping."""
    policy: Dict[str, Dict[str, dict]] = {}
    for entry in all_routes() if routes is None else routes:
        if entry.pattern in policy:
            raise ValueError("duplicate route pattern in registry: %r" % entry.pattern)
        policy[entry.pattern] = {m: dict(e) for m, e in entry.methods.items()}
    return policy


class CoverageViolation(Exception):
    """A three-leg coverage invariant failure (see module docstring)."""


def _validate_entry(entry: RouteEntry) -> None:
    if not entry.pattern.startswith("/"):
        raise CoverageViolation("route pattern must start with '/': %r" % entry.pattern)
    if not entry.handler:
        raise CoverageViolation("route %r has no handler" % entry.pattern)
    if not entry.source:
        raise CoverageViolation("route %r has no source tag" % entry.pattern)
    if not entry.methods:
        raise CoverageViolation("route %r registers no method" % entry.pattern)
    for method, policy_entry in entry.methods.items():
        if method not in HTTP_METHODS:
            raise CoverageViolation("route %r declares unsupported method %r"
                                    % (entry.pattern, method))
        policy = (policy_entry or {}).get("policy")
        if policy not in POLICIES:
            raise CoverageViolation("route %r method %s has unknown policy %r"
                                    % (entry.pattern, method, policy))


def implemented_methods(handler_cls) -> List[str]:
    """HTTP methods a handler class actually serves (third leg)."""
    return [m for m in HTTP_METHODS if callable(getattr(handler_cls, m, None))]


def check_route_coverage(namespace: Mapping[str, object],
                         routes: Optional[Sequence[RouteEntry]] = None) -> List[str]:
    """Cross-check the three legs; return human-readable violations (empty = ok).

    ``namespace`` maps handler class *names* to the resolved classes (the same
    mapping ``web.application`` uses, i.e. ``web_channel`` globals).
    """
    entries = list(all_routes() if routes is None else routes)
    violations: List[str] = []

    # leg 1 + registry self-consistency
    for entry in entries:
        try:
            _validate_entry(entry)
        except CoverageViolation as exc:
            violations.append(str(exc))
            continue
        cls = namespace.get(entry.handler)
        if cls is None:
            violations.append("route %r binds unknown handler %r"
                              % (entry.pattern, entry.handler))
            continue
        impl = set(implemented_methods(cls))
        # leg 3a (soundness): a declared method must exist on the handler,
        # otherwise the gate lets the request through and web.py answers 405.
        for method in entry.methods:
            if method not in impl:
                violations.append(
                    "route %r registers %s but %s does not implement it"
                    % (entry.pattern, method, entry.handler))

    # leg 3b (completeness): every method a handler implements must be
    # registered for at least one pattern bound to that handler, or the
    # functionality is silently unreachable (405) after the table is derived.
    by_handler: Dict[str, set] = {}
    for entry in entries:
        by_handler.setdefault(entry.handler, set()).update(entry.methods)
    for handler_name, registered in sorted(by_handler.items()):
        cls = namespace.get(handler_name)
        if cls is None:
            continue
        impl = set(implemented_methods(cls))
        for method in sorted(impl - registered):
            violations.append(
                "handler %s implements %s but no route registers it"
                % (handler_name, method))
    return violations


def _load_fork_extensions() -> None:
    """Import ``channel/web/fork_routes.py`` if a fork adds one.

    Importing the module lets it call :func:`register_fork_routes` before the
    derived tables are built. Absence is the normal state for this repository.
    """
    try:
        from channel.web import fork_routes  # noqa: F401
    except ImportError:
        return


_load_fork_extensions()
