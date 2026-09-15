import base64
import datetime
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import shutil
import sys
import threading
import time
import uuid
from queue import Queue, Empty
from typing import Dict, List, Tuple, Optional, Iterator, NoReturn
from urllib.parse import quote
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field

import web

from bridge.context import *
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel, check_prefix
from channel.chat_message import ChatMessage
from channel.web.route_registry import derive_web_urls as _derive_web_urls
from channel.web.auth_handlers import (
    DbAuthCheckHandler,
    DbAuthLoginHandler,
    DbAuthContextHandler,
    DbAuthLogoutHandler,
    DbAuthMeHandler,
    DbAuthPasswordHandler,
    DbSelfProfileHandler,
    DbSelfAvatarHandler,
    DbUserAvatarHandler,
    DesktopAuthorizeHandler,
    DesktopTokenHandler,
)
from channel.web.admin_handlers import (
    PlatformUsersHandler,
    PlatformUserPasswordHandler,
    PlatformUserExternalIdentitiesHandler,
    PlatformUserExternalIdentityHandler,
    PlatformTenantsHandler,
    PlatformTenantHandler,
    PlatformTenantAdminsHandler,
    PlatformTenantAgentsHandler,
    TenantInfoHandler,
    TenantMembersHandler,
    TenantMemberHandler,
    TenantMemberExternalIdentitiesHandler,
    TenantMemberExternalIdentityHandler,
    ExternalIdentityAttemptsHandler,
    PlatformExternalIdentityAttemptsHandler,
    TenantRolesHandler,
    TenantRoleHandler,
    TenantPermissionsHandler,
    TenantDepartmentsHandler,
    TenantDepartmentHandler,
    IdentityAuditHandler,
    IdentityAdministeredTenantsHandler,
    PlatformTenantRolesHandler,
    PlatformTenantRoleHandler,
    TenantAuthorizationCatalogHandler,
    PlatformTenantAuthorizationCatalogHandler,
    PlatformTenantResourcesHandler,
    TenantChannelsHandler,
    TenantChannelHandler,
    TenantChannelActiveHandler,
    _int_or_zero,
)
from channel.web.admin_overview import AdminOverviewHandler
from common import const
from common import i18n
from common.log import logger
from common.singleton import singleton
# Request-scoped authorized chat/upload target (seam, tasks 8.2/8.3). Imported at
# module level so every handler that needs to publish a target uses one name; it
# holds no state itself, so importing it cannot pull in the identity service.
from auth.runtime import authorized_target, authorized_target_scope
from config import (
    conf,
    get_data_root,
    get_weixin_credentials_path,
    read_config_template,
    sync_image_generation_custom_provider_env,
)
from models.reasoning_capabilities import provider_reasoning_metadata
from agent.permission import (
    MODES as PERMISSION_MODES,
    global_mode as permission_global_mode,
    normalize_mode as permission_normalize_mode,
)
from channel.web.openai_api import OpenAIChatCompletionsHandler
from channel.web.branding import (
    BrandingError,
    BrandingService,
    create_service as create_branding_service,
)
from channel.web.todo_handlers import (
    TodosHandler,
    TodoSummaryHandler,
    TodoDetailHandler,
    TodoEventsHandler,
    TodoSourceHandler,
)
from scenes.api import ScenesHandler, SceneActivateHandler
from scenes.api_workbench import SceneWorkbenchImportHandler

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".avi", ".mov", ".mkv"}


@dataclass
class SSEStreamState:
    """Bounded, replayable event log for one web request."""

    condition: threading.Condition = field(default_factory=threading.Condition)
    events: deque = field(default_factory=deque)
    next_seq: int = 1
    total_bytes: int = 0
    last_active: float = field(default_factory=time.time)
    main_done: bool = False
    main_done_at: Optional[float] = None
    stream_complete: bool = False
    completed_at: Optional[float] = None
    closed: bool = False


def _parse_sse_cursor(*values) -> int:
    cursors = []
    for value in values:
        try:
            cursors.append(max(0, int(value or 0)))
        except (TypeError, ValueError):
            cursors.append(0)
    return max(cursors, default=0)


def _read_config_file_for_write() -> dict:
    """Baseline dict for a partial write to config.json.

    When the file does not exist yet (fresh install), seed from
    config-template.json — the very config the running process loaded. Starting
    from an empty dict would persist a file missing every template default
    (model, agent limits, ...), silently changing behavior after a restart.
    """
    config_path = os.path.join(get_data_root(), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return read_config_template()


# Set once the console owns its socket. The desktop watchdog waits on this to
# tell "still starting" apart from "wedged and never going to answer".
SERVING = threading.Event()

_BIND_ERROR_CODE_RE = re.compile(r"\[(WinError|Errno) (\d+)\]")


def _bind_error_codes(err: OSError):
    """Return ``(winerror, errno)`` for a bind failure.

    cheroot swallows the original exception: it re-raises a bare
    ``socket.error(msg)`` with neither errno nor ``__cause__`` set, so on the
    path we actually care about the code only survives inside the message text.
    """
    winerror = getattr(err, "winerror", None)
    err_no = err.errno
    if winerror is None and err_no is None:
        for kind, code in _BIND_ERROR_CODE_RE.findall(str(err)):
            if kind == "WinError":
                winerror = int(code)
            else:
                err_no = int(code)
    return winerror, err_no


def _log_bind_failure(host: str, port: int, err: OSError):
    """Explain a failed bind in terms the user can act on.

    Windows needs its own branch: a port can be permanently unbindable because
    Hyper-V/WSL2/Docker reserved the range it falls in (WinError 10013), and
    nothing is listening on it, so the usual "kill the stale process" advice
    sends people looking for a process that doesn't exist.
    """
    winerror, err_no = _bind_error_codes(err)
    if winerror == 10013:
        logger.error(
            f"[WebChannel] 端口 {port} 被系统保留，无法绑定（WinError 10013）。"
            f"通常是 Hyper-V/WSL2/Docker 占用了该端口段，可执行 "
            f"`netsh interface ipv4 show excludedportrange protocol=tcp` 查看，"
            f"或在 config.json 中把 web_port 改成区间外的端口"
        )
    elif winerror == 10048 or err_no in (48, 98):  # WSAEADDRINUSE / macOS / Linux
        logger.error(
            f"[WebChannel] 端口 {port} 已被占用，可执行 `cow restart` 清理残留进程，"
            f"或在 config.json 中修改 web_port"
        )
    else:
        logger.error(f"[WebChannel] 无法在 {host}:{port} 上启动服务: {err}")


def _session_expire_seconds():
    return int(conf().get("web_session_expire_days", 30)) * 86400


def _require_platform_console():
    """Guard the platform-scoped config/model console (platform admin only)."""
    from channel.web.auth_handlers import _require_context
    from channel.web.admin_handlers import _require_platform_admin
    ctx = _require_context()
    _require_platform_admin(ctx)
    return ctx


# Localized text for /cancel system replies. Web is the only channel that
# honors a per-request `lang`; other channels reply in Chinese by default.
def _cancel_reply_text(cancelled: int, lang: str) -> str:
    en = lang.startswith("en")
    if cancelled > 0:
        return "🛑 Cancelled" if en else "🛑 已中止"
    return "Nothing to cancel." if en else "当前没有可中止的任务。"


def _steer_reply_text(status, lang: str) -> str:
    from agent.protocol import SteerStatus

    en = (lang or "").lower().startswith("en")
    messages = {
        SteerStatus.ACCEPTED: (
            "↪️ Active task redirected.", "↪️ 已引导当前任务。"
        ),
        SteerStatus.INACTIVE: (
            "No active task to steer.", "当前没有可引导的任务。"
        ),
        SteerStatus.CLOSING: (
            "The active task is already finishing.", "当前任务已结束，无法再引导。"
        ),
        SteerStatus.AMBIGUOUS: (
            "Multiple tasks are active in this session; the steering target is ambiguous.",
            "当前会话有多个任务在运行，无法确定引导目标。",
        ),
        SteerStatus.FULL: (
            "Too many steering updates are pending; try again after the agent processes them.",
            "引导指令过多，请等待当前任务处理后再试。",
        ),
        SteerStatus.INVALID: (
            "Usage: /steer <instruction>", "用法：/steer <引导指令>"
        ),
    }
    english, chinese = messages[status]
    return english if en else chinese


def _get_upload_dir(agent_id: str = None) -> str:
    from agent.registry import get_agent_registry

    workspace = get_agent_registry().get(agent_id).workspace
    upload_dir = os.path.join(workspace, "tmp")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


@contextmanager
def _db_scope() -> Iterator["RequestContext"]:
    """Yield a verified database request context (never None)."""
    from auth.runtime import to_runtime_identity
    from channel.web.auth_handlers import _require_context
    from common.runtime_identity import use_identity

    ctx = _require_context(require_tenant=True)
    if ctx.must_change_password:
        raise web.HTTPError(
            "403 Forbidden", {"Content-Type": "application/json"},
            json.dumps({"status": "error", "message": "password change required",
                        "code": "password_change_required"}))
    ident = to_runtime_identity(ctx)
    with use_identity(ident):
        yield ctx


def _require_read_permission(ctx: "Optional[RequestContext]", permission: str) -> None:
    """Enforce a business-read permission in database mode.

    Legacy mode has no per-user permissions, so this is a no-op. In database
    mode the caller must have selected a tenant and hold the permission. The
    permission is checked independently of tenant_admin qualification (the
    built-in tenant_admin grants the permission via its effective union); a
    custom role cannot be bypassed by admin status. A missing/invalid tenant was
    already rejected by ``_db_scope``.
    """
    if ctx is None:
        return
    if not ctx.tenant_id:
        raise web.HTTPError("400 Bad Request", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "tenant selection required",
                                        "code": "missing_tenant"}))
    # A platform admin (authorization_mode "all") is unrestricted for a
    # *functional read* gate, exactly as the resource helpers (check_resource_action,
    # resource_ids_for, _filter_tool_catalog/_filter_skill_catalog) already treat
    # them. Without this, /api/tools and /api/skills would 403 for a platform
    # admin whose role set happens not to carry skill.read/tool.read (the built-in
    # tenant_admin/member roles do not), leaving the 工具与技能 console empty.
    # is_platform_admin is re-resolved fresh on every request, so it is as
    # authoritative as a service round-trip and needs no extra store lookup.
    if ctx.is_platform_admin:
        return
    if permission not in ctx.permissions:
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))


def _knowledge_data_root_is_own(agent_id: Optional[str]) -> bool:
    """True when the addressed Agent reads a ``knowledge/`` of its own.

    Reuses the same two facts the Agent-admin projection derives
    ``knowledge_mode`` from — ``AgentAdminService._shared_knowledge_base()`` and
    ``_knowledge_mode_of()`` — so the write gate can never disagree with the mode
    the console shows or the directory ``KnowledgeService`` reads. This is
    deliberately not a second "is the data root shared?" rule.

    MUST be called inside ``_db_scope()``: the shared base resolves through
    ``state_dir.shared_root()`` and is therefore tenant-scoped. An Agent that
    cannot be resolved (a binding without a roster entry) is treated as shared,
    which fails closed for the only caller that consults it without admin
    qualification.
    """
    if not agent_id:
        return False
    from agent.admin import AgentAdminService
    from agent.registry import get_agent_registry

    try:
        profile = get_agent_registry().get(agent_id, require_enabled=False)
    except Exception as e:
        logger.debug("[WebChannel] knowledge mode for %r: %s", agent_id, e)
        return False
    return AgentAdminService._knowledge_mode_of(
        profile, AgentAdminService._shared_knowledge_base()) == "own"


def _private_agent_owned_by_another(ctx: "Optional[RequestContext]",
                                   agent_id: Optional[str]) -> bool:
    """True when ``agent_id`` is private to a member other than the caller.

    The one predicate that must run **before** any administrator shortcut. A
    private Agent's content — its prompt, memory, sessions and files — belongs to
    its owner; an admin reaching it through a gate that short-circuits on
    ``is_platform_admin``/``is_tenant_admin`` would be reading or rewriting a
    member's private workspace. An administrator's legitimate interest in a
    private Agent is governance metadata, answered by the governance surface, not
    by the content gates.

    ``False`` for an unbound Agent, a shared Agent, or the caller's own — so this
    only ever *narrows*, and only for the objects that are genuinely someone
    else's (task 3.2).
    """
    if ctx is None or not agent_id:
        return False
    from auth.service import get_identity_service

    binding = get_identity_service().get_agent_binding(agent_id)
    if not binding or binding.get("tenant_id") != ctx.tenant_id:
        return False
    owner = binding.get("private_owner_user_id")
    return bool(owner) and owner != getattr(ctx, "user_id", None)


def _knowledge_write_authorized(ctx: "Optional[RequestContext]",
                                agent_id: Optional[str]) -> bool:
    """Whether ``ctx`` may write ``agent_id``'s knowledge base.

    Authorization follows the *data root* and the *Agent's ownership*, not a
    functional permission (``knowledge.write`` was retired for this reason):

    * a private Agent is written by its owner only: the ownership refusal runs
      first, so an administrator cannot rewrite a member's private knowledge even
      though an administrator writes every *shared* data root;
    * a platform admin, and a ``tenant_admin`` of the Agent's own tenant, may
      write any shared data root;
    * an ordinary member may write only an Agent that is private to them *and*
      reads its own ``knowledge/`` — so a private Agent left on "shared" mode
      can never be turned into a write channel into the tenant's shared base;
    * a cross-tenant or unbound ``agent_id`` is refused (the caller's binding
      gate already answers 404 before this runs).

    Legacy mode (``ctx is None``) keeps its historical no-gate behaviour.
    """
    if ctx is None:
        return True
    if not agent_id:
        return False
    if _private_agent_owned_by_another(ctx, agent_id):
        return False
    from auth.service import get_identity_service

    binding = get_identity_service().get_agent_binding(agent_id)
    if not binding or binding.get("tenant_id") != ctx.tenant_id:
        return False
    if getattr(ctx, "is_platform_admin", False) or getattr(ctx, "is_tenant_admin", False):
        return True
    if binding.get("private_owner_user_id") != getattr(ctx, "user_id", None):
        return False
    return _knowledge_data_root_is_own(agent_id)


def _require_knowledge_write(ctx: "Optional[RequestContext]",
                             agent_id: Optional[str]) -> None:
    """Gate knowledge writes (create/rename/delete/move/import).

    The decision lives in :func:`_knowledge_write_authorized`; this wrapper only
    turns a refusal into the stable ``403 forbidden`` the console already reads.
    Cross-tenant and private-owner bounds are enforced by the caller via
    ``_require_tenant_agent_binding`` / ``_require_private_owner`` *before* this
    gate runs, so a refusal here is always a genuine write denial.
    """
    if _knowledge_write_authorized(ctx, agent_id):
        return
    raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                        json.dumps({"status": "error", "message": "forbidden",
                                    "code": "forbidden"}))


def _require_catalog_read(ctx: "Optional[RequestContext]", permission: str) -> None:
    """Gate the tenant skills/tools catalog read for the 工具与技能 console page.

    A platform admin is unrestricted. The built-in tenant_admin reads its own
    tenant's skills/tools catalog without a per-resource grant — the same read
    trust ``_tenant_admin_owns_agent`` extends to tenant-bound Agents. A plain
    member must hold the functional read permission. Read-only: the write paths
    (``skill.enable``/``skill.edit``) are untouched and still require an
    explicit grant, so this never widens what a tenant admin may change.
    """
    if ctx is not None and (ctx.is_platform_admin or ctx.is_tenant_admin):
        return
    _require_read_permission(ctx, permission)


def _resource_ids(ctx: "Optional[RequestContext]", kind: str, action: str,
                  permission: Optional[str] = None):
    """Return the resource ids a caller may act on for ``kind``+``action``.

    Returns ``None`` for a platform admin (unrestricted, the caller projects the
    live catalog), ``set()`` for a member with no grant, or an explicit set of
    ``{source}:{name}`` ids. Legacy mode (``ctx is None``) is unrestricted.
    """
    if ctx is None:
        return None
    from auth.service import get_identity_service
    return get_identity_service().resource_ids_for(
        ctx.user_id, ctx.tenant_id, kind, action, permission=permission)


def _require_resource_action(ctx: "Optional[RequestContext]", kind: str, resource_id: str,
                             action: str, permission: Optional[str] = None) -> None:
    """Enforce fine-grained resource authorization for a single resource.

    In database mode the caller must hold the functional ``permission`` (when
    supplied) and an explicit resource grant for ``kind``/``resource_id``/``action``
    — or be a platform admin. Legacy mode is a no-op.
    """
    if ctx is None:
        return
    from auth.service import get_identity_service
    if not get_identity_service().check_resource_action(
            ctx.user_id, ctx.tenant_id, kind, resource_id, action, permission=permission):
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))


def _raise_forbidden() -> NoReturn:
    """Refuse with the JSON shape every other authorization gate already uses."""
    raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                        json.dumps({"status": "error", "message": "forbidden",
                                    "code": "forbidden"}))


def _tenant_admin_owns_agent(ctx: "Optional[RequestContext]", agent_id: str) -> bool:
    """True when ``ctx`` administers the tenant that owns ``agent_id``.

    The tenant binding is the isolation boundary, so a tenant administrator
    administers its own tenant's Agents without a per-resource grant — the same
    trust ``_require_agent_create`` and ``_require_chat_use`` already extend to a
    tenant admin. The explicit binding lookup is what keeps it safe: an Agent
    bound to another tenant is never "owned", so this grants nothing across
    tenants (a platform admin is handled by ``check_resource_action`` instead).
    """
    if ctx is None or not ctx.is_tenant_admin or not ctx.tenant_id:
        return False
    from auth.service import get_identity_service
    return agent_id in get_identity_service().tenant_agent_ids(ctx.tenant_id)


def _tenant_shared_default_agent(ctx: "Optional[RequestContext]", agent_id: str,
                                 permission: Optional[str] = None,
                                 tenant_default: Optional[str] = None) -> bool:
    """True when ``agent_id`` is the caller's *shared* default Agent.

    The console promises a member can open the chat and just type: the server then
    anchors the session to the tenant's default Agent. That promise only holds if
    the member can reach that Agent. Demanding a hand-written ``agent:<id>`` grant
    for the one entry every member shares turns "no Agent selection" back into a
    locked door — the projection hides the Agent, the console invents a fallback
    id, and the send fails on an id that was never real.

    So the tenant's *resolved* default Agent is reachable with the functional
    permission alone. Three conditions keep this narrow:

    * **the caller's own tenant's default** — resolved by
      :func:`_resolve_tenant_default_agent`, so another tenant's default is never
      matched, and a tenant boundary is never crossed;
    * **tenant-shared** — a ``private_owner_user_id`` is an *exclusive* resource,
      and being the default must not leak it to other members;
    * **the functional permission** — ``permission``, when given, must be held, so
      this never hands out a resource the caller has no permission for.

    ``tenant_default`` lets a caller that already resolved the default reuse it
    instead of re-querying per Agent. Read-only, like every other gate here.
    """
    if ctx is None or not agent_id or not ctx.tenant_id:
        return False
    if permission is not None and permission not in (ctx.permissions or ()):
        return False
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        # These callers are already unrestricted; this predicate adds nothing.
        return False
    if tenant_default is None:
        tenant_default = _resolve_tenant_default_agent(ctx)
    if agent_id != tenant_default:
        return False
    from auth.service import get_identity_service
    binding = get_identity_service().get_agent_binding(agent_id)
    if not binding or binding.get("tenant_id") != ctx.tenant_id:
        return False
    return not binding.get("private_owner_user_id")


def _require_deletable_provenance(ctx: "Optional[RequestContext]",
                                  agent_id: Optional[str]) -> None:
    """Refuse erasing the caller's own **supplied** assistant through delete.

    Ownership answers *whose* object this is; provenance answers whether it can
    be erased at all. The system-provisioned assistant is bound to a member as
    theirs to use and to maintain, never as theirs to delete: it is the
    Agent-less entry point the tenant handed them, and only the provisioner ever
    creates one, so a member who deletes it has no way back to it.

    The predicate is deliberately the same fact ``_personal_agents_projection``
    advertises as ``actions.delete`` and ``PrivateAgentService.
    delete_private_agent`` enforces on the owner-facing maintenance path — the
    binding's ``origin`` for an object private to this caller — so the console's
    projection, this route and the member service cannot answer the same
    question three ways. ``unknown`` counts as supplied, exactly as
    ``SUPPLIED_ASSISTANT_ORIGINS`` documents; a shared Agent (nobody's private
    object) keeps the historical ``agent.edit``-only behaviour, so an operator
    can still retire a tenant's own Agent.
    """
    if ctx is None or not agent_id:
        return
    from auth.service import SUPPLIED_ASSISTANT_ORIGINS, get_identity_service

    binding = get_identity_service().get_agent_binding(agent_id) or {}
    if binding.get("tenant_id") != ctx.tenant_id:
        return
    if binding.get("private_owner_user_id") != getattr(ctx, "user_id", None):
        return
    if str(binding.get("origin") or "unknown") not in SUPPLIED_ASSISTANT_ORIGINS:
        return
    raise web.HTTPError(
        "403 Forbidden", {"Content-Type": "application/json"},
        json.dumps({"status": "error",
                    "message": "only a self-created agent can be deleted by its owner",
                    "code": "forbidden"}))


def _require_agent_action(ctx: "Optional[RequestContext]", agent_id: str, action: str,
                          permission: str) -> None:
    """Enforce fine-grained agent authorization for a single agent resource.

    ``agent_id`` addresses a tenant-bound agent. The tenant binding is validated
    separately by the caller; here we only require the resource grant for the
    action. An agent already bound to the tenant is checked by resource_id.
    """
    if ctx is None:
        return
    # Ownership precedes the administrator shortcut (task 3.2): a private Agent is
    # acted on by its owner, never by an admin who happens to pass an admin flag.
    if _private_agent_owned_by_another(ctx, agent_id):
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))
    if _tenant_admin_owns_agent(ctx, agent_id):
        return
    # The tenant's shared default Agent is the Agent-less entry point, so read and
    # use follow the functional permission instead of a per-resource grant. ``edit``
    # deliberately stays grant-only: being reachable must not imply being rewritable.
    if action in ("read", "use") and _tenant_shared_default_agent(ctx, agent_id, permission):
        return
    _require_resource_action(ctx, "agent", f"agent:{agent_id}", action, permission)


def _capability_name_list(value) -> list:
    """Normalise a config field's raw value into the dependency names to check.

    The console sends a list, but a save that names the field with ``None`` means
    "all shared assets" — which is not a set of *chosen* dependencies and so has
    nothing to authorize here. Anything else is coerced to trimmed strings; a
    non-list is treated as empty rather than raising, because the shape error is
    the agent-admin service's to report, not this gate's.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dependency_is_granted(name: str, allowed) -> bool:
    """Whether ``name`` is covered by a resource-id grant set.

    ``None`` means unrestricted (platform all / legacy). Otherwise the name must
    equal a granted id or be its trailing ``:``-segment — the id carries an origin
    namespace (``builtin:``, ``mcp:<conn>:``, ``provider:<pid>:``) that the config
    field does not, so "search" must match ``builtin:search`` and ``mcp:crm:search``
    without this gate inventing which connection the name came from.
    """
    if allowed is None:
        return True
    if name in allowed:
        return True
    tail = ":" + name
    return any(str(rid).endswith(tail) for rid in allowed)


def _refuse_unauthorized_dependency(kind: str, name: str) -> NoReturn:
    """Name the offending resource, so the refusal explains itself (4.4)."""
    raise web.HTTPError(
        "403 Forbidden", {"Content-Type": "application/json"},
        json.dumps({"status": "error",
                    "message": f"not authorized to use {kind} '{name}'",
                    "code": "forbidden"}, ensure_ascii=False))


def _require_configured_capabilities(ctx: "Optional[RequestContext]",
                                     agent_id: str, body) -> None:
    """Re-verify the owner's authority for every dependency a save names (4.4).

    Owning an Agent is authority over *that object* — not over the models, tools
    and skills it is pointed at. The runtime re-checks each of those per call
    (``agent_stream._resource_tool_denial``, the send-time model gate); this is
    the *save* half of the same rule, so a configuration that could only ever be
    refused at run time is refused when it is written, and the member is told
    which resource is the problem.

    Scope is deliberately narrow: only the caller's **own private** Agent. A
    shared Agent's edit authority is its explicit ``agent.edit`` grant, and a
    private Agent owned by someone else is refused before any dependency question
    is asked. Administrators and legacy mode are unrestricted, exactly as they
    are at run time.
    """
    if ctx is None:
        return
    if getattr(ctx, "is_platform_admin", False) or getattr(ctx, "is_tenant_admin", False):
        return
    if _private_agent_owned_by_another(ctx, agent_id):
        _raise_forbidden()
    if not agent_id or not isinstance(body, dict):
        return
    from auth.service import get_identity_service

    svc = get_identity_service()
    if not svc.is_private_agent_owner(ctx.tenant_id, ctx.user_id, agent_id):
        return  # a shared Agent keeps its existing agent.edit authority

    if "model" in body:
        model = str(body.get("model") or "").strip()
        if model:
            allowed = svc.resource_ids_for(
                ctx.user_id, ctx.tenant_id, "model", "use", permission="model.use")
            if not _dependency_is_granted(model, allowed):
                _refuse_unauthorized_dependency("model", model)
    if "skills" in body:
        allowed = svc.resource_ids_for(
            ctx.user_id, ctx.tenant_id, "skill", "use", permission="skill.use")
        for name in _capability_name_list(body.get("skills")):
            if not _dependency_is_granted(name, allowed):
                _refuse_unauthorized_dependency("skill", name)
    if "tools_allowlist" in body:
        allowed = svc.resource_ids_for(
            ctx.user_id, ctx.tenant_id, "tool", "execute", permission="tool.execute")
        for name in _capability_name_list(body.get("tools_allowlist")):
            if not _dependency_is_granted(name, allowed):
                _refuse_unauthorized_dependency("tool", name)


def _require_agent_create(ctx: "Optional[RequestContext]") -> None:
    """Require the caller to be able to create a new agent resource.

    There is no resource id yet, so the check is the functional ``agent.edit``
    permission (or platform/tenant admin), enforced via a synthetic grant on the
    agent kind. A tenant admin or platform admin passes; a member must hold the
    functional permission and any single agent grant to demonstrate the habit.
    """
    if ctx is None:
        return
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        return
    if "agent.edit" not in ctx.permissions:
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))


def _tenant_agent_workspace(ctx: "RequestContext", agent_id: str) -> Optional[str]:
    """The workspace a tenant-owned Agent must live in, or None to use the default.

    A tenant's Agents belong inside the tenant's own shared root
    (``<shared_root>/agents/<id>``), never in the instance root: the instance
    root holds the shared asset library and the other tenants' Agents, so an
    Agent created there is neither isolated nor visible to the tenant that made
    it. Returns None when the tenant has no resolved shared root, which leaves
    the legacy single-tenant instance-root layout untouched.
    """
    from auth.service import get_identity_service
    root = get_identity_service().tenant_shared_root(ctx.tenant_id)
    if not root:
        return None
    return os.path.join(root, "agents", agent_id)


def _adopt_created_agent_for_tenant(ctx: "RequestContext", agent_id: str) -> None:
    """Bind a freshly created Agent to the tenant that created it.

    The read path filters the roster by the tenant binding
    (``_tenant_agents_projection`` -> ``tenant_agent_ids``), so a write path that
    skips the binding produces an Agent the creating tenant can never see. The
    tenant's first Agent also becomes its default, so a tenant that starts empty
    ends up with something to chat with.
    """
    if not agent_id:
        return
    from auth.service import get_identity_service
    svc = get_identity_service()
    had_agents = bool(svc.tenant_agent_ids(ctx.tenant_id))
    # Bound tenant-shared: private ownership is an explicit act. Inferring it
    # from the creator would mark the tenant's first Agent — which the next line
    # makes the tenant *default* — as that one user's private asset, and
    # ``private_owner_user_id`` is an exclusive read gate on the chat path.
    svc.bind_agent(tenant_id=ctx.tenant_id, agent_id=agent_id)
    if not had_agents and not svc.tenant_default_agent_id(ctx.tenant_id):
        svc.appoint_tenant_default_agent(
            tenant_id=ctx.tenant_id, agent_id=agent_id, actor_user_id=ctx.user_id)


def _require_chat_use(ctx: "Optional[RequestContext]") -> None:
    """Require the functional ``chat.use`` permission to run a chat.

    The chat consumer is gated by ``chat.use``; a member holding ``agent.use``
    but not ``chat.use`` cannot start a conversation. Platform/tenant admins and
    legacy mode pass. Recomputed per request (never cached).
    """
    if ctx is None:
        return
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        return
    if "chat.use" not in ctx.permissions:
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))


def _require_model_use(ctx: "Optional[RequestContext]", model_code: str,
                       resource_ids: Optional[set] = None) -> None:
    """Require the caller to be allowed to use a specific model.

    The model is addressed by its catalog ``resource_id`` (``provider:{pid}:{code}``).
    A platform admin passes; a member must hold ``model.use`` and an explicit
    ``model`` grant whose resource_id ends with ``:{model_code}`` or equals the
    model code. Legacy mode (no ctx) passes. Recomputed per call (never cached).
    """
    if ctx is None:
        return
    if ctx.is_platform_admin or ctx.is_tenant_admin:
        return
    if resource_ids is None:
        from auth.service import get_identity_service
        resource_ids = get_identity_service().resource_ids_for(
            ctx.user_id, ctx.tenant_id, "model", "use", permission="model.use")
    if resource_ids is None:
        return  # unrestricted
    if model_code in resource_ids:
        return
    for rid in resource_ids:
        parts = rid.split(":")
        if parts and parts[-1] == model_code:
            return
    raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                        json.dumps({"status": "error", "message": "forbidden",
                                    "code": "forbidden"}))


def _current_db_identity():
    """Return the current RuntimeIdentity when running in database mode, else None.

    Used by console-side projections that need the caller's tenant for
    role-default resolution. In legacy mode there is no tenant, so returning
    None lets those projections fall back to the historical behaviour.
    """
    from common.runtime_identity import current_identity

    ident = current_identity()
    if not ident.user_id or not ident.tenant_id:
        return None
    return ident


def _authorized_model_codes() -> Optional[set]:
    """The ``model.use`` model-code set the current identity may select.

    Returns ``None`` when unrestricted (legacy mode, platform all, or legacy
    mode with no identity) so the session picker keeps the whole catalog.
    Otherwise returns the set of model codes granted across the identity's roles,
    derived from the catalog ``resource_id`` (``provider:{pid}:{code}``) trailing
    segment. In ``database`` mode a missing identity or a lookup error yields an
    empty set (fail closed) rather than widening to the whole catalog.
    Recomputed per call — never cached — so a grant change is reflected on the
    next render.
    """
    from common.runtime_identity import current_identity

    ident = current_identity()
    if not ident.user_id or not ident.tenant_id:
        # Legacy mode has no per-user grants: unrestricted. Database mode with no
        # resolved identity must fail closed instead of widening to the catalog.
        return set() if _is_database_identity() else None
    try:
        from auth.service import get_identity_service
        svc = get_identity_service()
        ids = svc.resource_ids_for(ident.user_id, ident.tenant_id, "model", "use",
                                   permission="model.use")
    except Exception:
        return set() if _is_database_identity() else None
    if ids is None:
        return None  # platform all / unrestricted
    codes: set = set()
    for rid in ids:
        parts = str(rid).split(":")
        if parts:
            codes.add(parts[-1])
    return codes


def _web_runtime_identity_snapshot() -> dict:
    """Carry verified Web delegation across the chat worker thread boundary.

    The database session row id is non-secret and lets tools recheck revocation
    without keeping a login token on an agent, tool, or persisted conversation.
    Never copy identity or delegation claims from the submitted JSON body.
    """
    from common.runtime_identity import current_identity
    ident = current_identity()
    snapshot = {
        "user_id": ident.user_id,
        "tenant_id": ident.tenant_id,
        "agent_id": ident.agent_id,
        "session_id": ident.session_id,
        "web_auth_session_id": None,
    }
    from channel.web.auth_handlers import _get_service, _session_token
    verified = _get_service().verify_session(_session_token())
    if not verified or verified["user"]["id"] != ident.user_id or not ident.tenant_id:
        raise PermissionError("Web 会话身份不可用")
    snapshot["web_auth_session_id"] = verified["session"]["id"]
    return snapshot


def _chat_error(message: str, status: str = "403 Forbidden", code: str = "forbidden"):
    raise web.HTTPError(status, {"Content-Type": "application/json; charset=utf-8"},
                        json.dumps({"status": "error", "message": message, "code": code}))


def _chat_body() -> dict:
    try:
        body = json.loads(web.data() or b"{}")
    except (TypeError, ValueError):
        _chat_error("invalid JSON", "400 Bad Request", "bad_request")
    if not isinstance(body, dict):
        _chat_error("JSON object required", "400 Bad Request", "bad_request")
    return body


def _require_chat_csrf() -> None:
    # Reuse the unified credential selection so a same-value repeated cookie+
    # bearer is still treated as a cookie request (origin check applies), and a
    # different-value pair is a hard 400 mixed_credentials rather than a bypass.
    from channel.web.auth_handlers import _csrf_ok
    if not _csrf_ok():
        _chat_error("invalid request origin", code="csrf_failed")


def _authorize_chat_session(ctx, session_id, agent_id, *, create=False) -> str:
    """Authorize and, for a new chat, atomically claim its durable owner.

    Runtime/queue/cancellation keys include Agent and session but not user. A
    SELECT followed by asynchronous persistence would let two users race for
    the same new key. Claim it before dispatch, preserving all existing owners
    (including legacy owner='') and never borrowing another Agent's workspace.

    Database-mode authorization mirrors ``_workbench_chat_readiness`` exactly
    (functional ``chat.use`` + the target Agent's ``agent.use`` resource grant,
    platform/tenant admin bypass), so a caller that only reads the card can
    never start/resume a conversation. Recomputed on every call — never cached —
    so a revoked grant applies to the next send/poll/steer. Legacy mode
    (``ctx is None``) stays open.
    """
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        _chat_error("valid session_id required", "400 Bad Request", "bad_request")
    agent_id = _require_tenant_agent_binding(ctx, agent_id)
    _require_private_owner(ctx, agent_id)
    from agent.registry import get_agent_registry
    from agent.memory import get_conversation_store
    try:
        profile = get_agent_registry().get(agent_id)
    except (KeyError, ValueError):
        _chat_error("agent not found", "404 Not Found", "not_found")
    store = get_conversation_store(profile.workspace)
    with store._lock:
        con = store._connect()
        try:
            with con:
                # A session that exists but is not *this caller's own* is a 404:
                # masking its existence keeps one member from probing another
                # member's conversation ids. Only after the session is confirmed
                # to be the caller's (or brand new) do the execution gates run.
                row = con.execute(
                    "SELECT owner, channel_type FROM sessions WHERE session_id=?", (session_id,),
                ).fetchone()
                if row is not None and (row[0] != ctx.user_id or row[1] != "web"):
                    _chat_error("session not found", "404 Not Found", "not_found")
                if row is None:
                    # Brand-new session: this caller is its first claimant. The
                    # usage gates still run before INSERT so a denied caller
                    # never leaves an orphaned session row behind.
                    _require_chat_use(ctx)
                    _require_agent_action(ctx, agent_id, "use", "agent.use")
                    if create:
                        now = int(time.time())
                        con.execute(
                            "INSERT OR IGNORE INTO sessions "
                            "(session_id, channel_type, owner, created_at, last_active, msg_count) "
                            "VALUES (?, 'web', ?, ?, ?, 0)",
                            (session_id, ctx.user_id, now, now),
                        )
                        # Two callers may race to claim the same new key; only
                        # the winner's owner survives the IGNORE above.
                        claimed = con.execute(
                            "SELECT owner FROM sessions WHERE session_id=?", (session_id,),
                        ).fetchone()
                        if not claimed or claimed[0] != ctx.user_id:
                            _chat_error("session not found", "404 Not Found", "not_found")
                else:
                    # Resuming the caller's own conversation re-validates the
                    # execution gates (never cached: a revoked grant blocks the
                    # very next send/poll/steer on this session).
                    _require_chat_use(ctx)
                    _require_agent_action(ctx, agent_id, "use", "agent.use")
        finally:
            con.close()
    return agent_id


def _owned_chat_request(channel, request_id):
    if not isinstance(request_id, str) or not request_id:
        _chat_error("request_id required", "400 Bad Request", "bad_request")
    # Tuples are captured by the authenticated /message route, never by the
    # event payload or client-supplied user/tenant/session identifiers.
    with channel._sse_streams_lock:
        owner = getattr(channel, "request_owners", {}).get(request_id)
    if owner is None:
        _chat_error("request not found", "404 Not Found", "not_found")
    return owner


def _authorize_chat_request(ctx, channel, request_id):
    tenant_id, user_id, agent_id, session_id = _owned_chat_request(channel, request_id)
    if tenant_id != ctx.tenant_id or user_id != ctx.user_id:
        _chat_error("request not found", "404 Not Found", "not_found")
    _authorize_chat_session(ctx, session_id, agent_id)
    return agent_id, session_id


@contextmanager
def _stream_identity_scope(channel, request_id):
    """Native EventSource sends cookies but cannot add X-Tenant-ID.

    Authenticate the login first, then resolve the recorded request's tenant
    and current membership. A supplied tenant selection must still agree.
    """
    from auth.runtime import resolve_context, to_runtime_identity, IdentityContextError
    from channel.web.auth_handlers import _get_service, _session_token
    from common.runtime_identity import use_identity
    svc, token = _get_service(), _session_token()
    try:
        personal = resolve_context(svc, token, None)
        owner = _owned_chat_request(channel, request_id)
        if personal.user_id != owner[1]:
            _chat_error("request not found", "404 Not Found", "not_found")
        query_tenant = web.input(tenant_id="").tenant_id
        for selected in (web.ctx.env.get("HTTP_X_TENANT_ID", ""), query_tenant):
            if selected and selected != owner[0]:
                _chat_error("conflicting tenant selection", "400 Bad Request", "conflicting_tenant")
        ctx = resolve_context(svc, token, owner[0])
    except IdentityContextError as e:
        from http import HTTPStatus
        _chat_error(str(e), f"{e.status} {HTTPStatus(e.status).phrase}", e.code)
    if ctx.must_change_password:
        _chat_error("password change required", code="password_change_required")
    with use_identity(to_runtime_identity(ctx)):
        yield ctx


@contextmanager
def _uploads_identity_scope():
    """Resolve an upload read-back's tenant from the addressed Agent.

    The console renders an uploaded thumbnail or clip as an ``<img>``/``<audio>``
    subresource, and a browser subresource request cannot carry ``X-Tenant-ID``.
    The route is therefore declared ``tenant_from_resource`` in the roster, the
    gate authenticates the caller without a tenant selection, and this scope
    supplies the tenant from the addressed Agent's binding — the same shape as
    ``_stream_identity_scope``, which derives it from the recorded request's
    owner.

    The *resource* decides the tenant, never the client: the binding is read
    server-side and membership in that tenant is resolved through
    ``resolve_context``. A caller-supplied selection is only cross-checked, and an
    Agent that resolves to no tenant is reported as not-found so an unauthenticated
    caller cannot probe which ids are bound. Object-level ownership and the
    authoritative ``agent.read`` check stay in the handler.
    """
    from auth.runtime import resolve_context, to_runtime_identity, IdentityContextError
    from channel.web.auth_handlers import _get_service, _session_token
    from common.runtime_identity import use_identity

    svc, token = _get_service(), _session_token()
    if not token:
        _chat_error("unauthorized", "401 Unauthorized", "unauthorized")
    params = web.input(agent_id='', agent='', tenant_id='')
    agent_id = _request_agent_id(params)

    def _fail(exc: "IdentityContextError"):
        from http import HTTPStatus
        _chat_error(str(exc), f"{exc.status} {HTTPStatus(exc.status).phrase}", exc.code)

    try:
        # Authenticate before anything else: an unbound-Agent 404 must not be
        # observable to a caller who has no valid session at all.
        resolve_context(svc, token, None)
    except IdentityContextError as e:
        _fail(e)

    binding = svc.get_agent_binding(agent_id) if agent_id else None
    tenant_id = binding["tenant_id"] if binding else None
    if not tenant_id:
        raise web.notfound()

    for selected in (web.ctx.env.get("HTTP_X_TENANT_ID", ""),
                     getattr(params, "tenant_id", "") or ""):
        if selected and selected != tenant_id:
            _chat_error("conflicting tenant selection", "400 Bad Request",
                        "conflicting_tenant")

    try:
        ctx = resolve_context(svc, token, tenant_id)
    except IdentityContextError as e:
        _fail(e)
    if ctx.must_change_password:
        _chat_error("password change required", code="password_change_required")

    with use_identity(to_runtime_identity(ctx)):
        yield ctx, agent_id


def _tenant_owning_path(svc, real_path: str) -> "Optional[str]":
    """The tenant whose workspace contains ``real_path`` (most specific root).

    A file's authoritative owner is the tenant that holds its workspace, read
    server-side from the store — never the client's claim. ``None`` when the
    path belongs to no known tenant/Agent workspace (invisible, not a grant).
    """
    candidates = []
    for rec in svc.tenant_shared_roots():
        root = (rec or {}).get("shared_root")
        if root:
            candidates.append((os.path.realpath(root), rec["id"]))
    try:
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        for binding in svc.list_agent_bindings():
            agent_id = (binding or {}).get("agent_id")
            if not agent_id:
                continue
            try:
                workspace = registry.get(agent_id).workspace
            except (KeyError, ValueError, TypeError):
                continue
            if workspace:
                candidates.append((os.path.realpath(workspace), binding["tenant_id"]))
    except Exception as e:
        logger.debug(f"[WebChannel] agent workspace lookup unavailable: {e}")

    best_tenant, best_len = None, -1
    for root, tenant_id in candidates:
        try:
            inside = os.path.commonpath([real_path, root]) == root
        except ValueError:
            continue
        if inside and len(root) > best_len:
            best_tenant, best_len = tenant_id, len(root)
    return best_tenant


@contextmanager
def _file_identity_scope():
    """Resolve a file read's tenant from the addressed file (its workspace).

    ``GET /api/file`` backs a plain ``<a download>`` navigation and ``<img>``
    subresources, so the browser issues it without ``X-Tenant-ID``. The route is
    declared ``tenant_from_resource`` in the roster, the gate authenticates the
    caller without a tenant selection, and this scope derives the tenant from
    the file's own workspace and verifies membership through ``resolve_context``
    — the same shape as ``_uploads_identity_scope``. A supplied selection is only
    cross-checked; the resource stays authoritative.
    """
    from auth.runtime import resolve_context, to_runtime_identity, IdentityContextError
    from channel.web.auth_handlers import _get_service, _session_token
    from common.runtime_identity import use_identity

    svc, token = _get_service(), _session_token()
    if not token:
        _chat_error("unauthorized", "401 Unauthorized", "unauthorized")

    def _fail(exc: "IdentityContextError"):
        from http import HTTPStatus
        _chat_error(str(exc), f"{exc.status} {HTTPStatus(exc.status).phrase}", exc.code)

    # Authenticate before anything else: an unresolvable path must not be
    # observable to a caller who has no valid session at all.
    try:
        resolve_context(svc, token, None)
    except IdentityContextError as e:
        _fail(e)

    params = web.input(path="", tenant_id="")
    raw = params.path
    real_path = os.path.realpath(raw) if raw else None
    tenant_id = _tenant_owning_path(svc, real_path) if real_path else None
    if not tenant_id:
        raise web.notfound()

    for selected in (web.ctx.env.get("HTTP_X_TENANT_ID", ""),
                     getattr(params, "tenant_id", "") or ""):
        if selected and selected != tenant_id:
            _chat_error("conflicting tenant selection", "400 Bad Request",
                        "conflicting_tenant")

    try:
        ctx = resolve_context(svc, token, tenant_id)
    except IdentityContextError as e:
        _fail(e)
    if ctx.must_change_password:
        _chat_error("password change required", code="password_change_required")

    with use_identity(to_runtime_identity(ctx)):
        yield ctx


def _require_session_owner(ctx: "Optional[RequestContext]", session_id: str,
                           agent_id: Optional[str]) -> None:
    """Reject reads of a session whose agent is not visible to the caller.

    In database mode the requested ``agent_id`` (defaulting to the global default
    when absent) must be one the caller's tenant is bound to. This stops one
    tenant from reading another tenant's conversation by naming its agent or by
    relying on the global default fallback. Legacy mode is a no-op.
    """
    if ctx is None:
        return
    if agent_id:
        visible = _tenant_ids_for_context(ctx) or []
        if agent_id not in visible:
            raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                                json.dumps({"status": "error", "message": "forbidden"}))
    else:
        # No agent selected in database mode: the tenant's *bound default* is
        # used (task 3.8), so a tenant that owns several Agents is no longer
        # blocked just because it never picked one. The global default is never
        # borrowed — it may belong to another tenant.
        if _resolve_tenant_default_agent(ctx):
            return
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "default agent ambiguous"}))


def _require_owned_session(ctx: "Optional[RequestContext]", session_id: str,
                           agent_id: Optional[str]) -> None:
    """Reject binding a session the caller does not own (database mode).

    ``_require_session_owner`` only checks the agent is tenant-bound; the durable
    owner lives in the ``sessions`` table. This replicates the inline owner check
    from ``_workbench_chat_readiness`` so a member cannot bind another user's
    session (or a non-web session) to a project. Legacy mode is a no-op.
    """
    if ctx is None:
        return
    resolved = _require_tenant_agent_binding(ctx, agent_id)
    from agent.registry import get_agent_registry
    from agent.memory import get_conversation_store
    try:
        profile = get_agent_registry().get(resolved)
    except (KeyError, ValueError):
        return
    store = get_conversation_store(profile.workspace)
    with store._lock:
        con = store._connect()
        try:
            row = con.execute(
                "SELECT owner, channel_type FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if row is not None and (row[0] != ctx.user_id or row[1] != "web"):
                raise web.HTTPError("404 Not Found", {"Content-Type": "application/json"},
                                    json.dumps({"status": "error", "message": "session not found"}))
        finally:
            con.close()


def _require_session_scope(ctx: "Optional[RequestContext]", session_id: str,
                           agent_id: Optional[str]) -> str:
    """Enforce the tenant-binding + visibility + durable-ownership triple.

    Every session-scoped read or mutation owes the same three checks, so they
    live here instead of being re-derived per handler:

    * the addressed (or default) Agent must be bound to the caller's tenant;
    * the session's Agent must be visible to the caller, and when no Agent was
      named the tenant's bound default must resolve unambiguously;
    * the durable ``sessions`` row must be owned by the caller (and be a web
      session).

    Returns the resolved agent id so the caller addresses the store under the
    tenant-bound Agent rather than the raw request parameter. Legacy mode
    (``ctx is None``) is a no-op.
    """
    resolved = _require_tenant_agent_binding(ctx, agent_id)
    _require_session_owner(ctx, session_id, agent_id)
    _require_owned_session(ctx, session_id, resolved)
    return resolved


def _conversation_store_for(agent_id: Optional[str]):
    """Open the conversation store of the addressed Agent.

    Conversations live one database per Agent workspace
    (``<workspace>/memory/long-term/index.db``): the history list merges those
    workspaces (``_list_sessions_across_agents``) and the ownership check reads
    the addressed Agent's workspace (``_require_owned_session``), so every
    read/write addressed by session id has to open the *same* file.

    ``_get_workspace_root`` must NOT be used for this. In database mode it
    resolves the caller's tenant *shared* root (that is its job: the file
    panel, preview and uploads are tenant-scoped), so a session write would
    land in a different database than the list it came from and answer
    ``session not found`` for a session the user can plainly see.
    """
    from agent.memory import get_conversation_store
    from agent.registry import get_agent_registry

    return get_conversation_store(get_agent_registry().get(agent_id or None).workspace)


def _require_tenant_agent_binding(ctx: "Optional[RequestContext]", agent_id: Optional[str]) -> str:
    """Validate that ``agent_id`` is bound to the caller's tenant (task 3.10).

    In database mode a resource read addressed by ``agent_id`` must belong to the
    caller's tenant — otherwise a tenant could read another tenant's memory or
    knowledge by naming that tenant's agent. Returns the resolved agent id. When
    the caller selected no agent, the tenant's bound default agent is used (it
    must resolve unambiguously, mirroring ``_require_session_owner``). Legacy
    mode (``ctx is None``) is a no-op and returns the id as-is.
    """
    if ctx is None:
        return agent_id
    from auth.service import get_identity_service
    svc = get_identity_service()
    if agent_id:
        binding = svc.get_agent_binding(agent_id)
        if not binding or binding["tenant_id"] != ctx.tenant_id:
            raise web.HTTPError("404 Not Found", {"Content-Type": "application/json"},
                                json.dumps({"status": "error", "message": "agent not found"}))
        return agent_id
    # An Agent-less request is anchored to the tenant's default Agent, which
    # always resolves when the tenant has any; only an Agent-less tenant is
    # refused, so entering a conversation never requires picking one.
    resolved = _resolve_tenant_default_agent(ctx)
    if resolved:
        return resolved
    raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                        json.dumps({"status": "error", "message": "default agent ambiguous"}))


def _db_path_owner_forbidden(ctx: "Optional[RequestContext]", agent_id: "Optional[str]") -> bool:
    """True when the caller may not read ``agent_id``'s assets.

    In database mode, if the agent is privately owned (``private_owner_user_id``
    set) only that owner may read its memory/knowledge/files. **An administrator
    is not an exception**: ``tenant_admin`` reaches the *governance* surface
    (ownership, usage, the stop action) but not the member's private content, and
    platform-admin status is already no exception at the tenant layer. Anything
    else would let "manage the tenant" double as "read every member's workspace".
    Legacy mode is a no-op.

    Pure predicate so both the raising gate (:func:`_require_private_owner`) and
    the file-path authorization (:func:`_authorize_db_file_path`) share one rule.
    """
    if ctx is None or not getattr(ctx, "tenant_id", None) or not agent_id:
        return False
    from auth.service import get_identity_service
    binding = get_identity_service().get_agent_binding(agent_id)
    if not binding:
        return False
    owner = binding.get("private_owner_user_id")
    if not owner:
        # tenant-shared asset: any permission-holder of the tenant may read.
        return False
    return owner != getattr(ctx, "user_id", None)


def _require_private_owner(ctx: "Optional[RequestContext]", agent_id: str) -> None:
    """Enforce private-owner read scoping for an agent's assets (task 3.10)."""
    if _db_path_owner_forbidden(ctx, agent_id):
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden"}))


def _get_workspace_root(session_id: str = None, agent_id: str = None) -> str:
    """Resolve the working directory for this request.

    When a session has opened a project directory, that project is the working
    directory the file panel / preview / ``@`` picker operate in. Otherwise it
    is the Agent's workspace (``state_root``, e.g. ``~/cow``). Memory and skills
    always stay in ``state_root`` regardless; only the working root moves.

    In database mode the workspace is derived from the request's ``RuntimeIdentity``:
    a selected tenant resolves its trusted shared root, and the agent must be
    bound to that tenant (no global default fallback). Legacy mode is unchanged.
    """
    if session_id:
        try:
            from agent.workspace import project_store
            project_dir = project_store.get_project_dir(session_id, agent_id)
            if project_dir:
                return project_dir
        except Exception as e:
            logger.debug(f"[WebChannel] project_dir resolve failed: {e}")
    # Fork seam (tasks 8.1/8.3): tenancy is resolved by
    # ``channel/web/tenant_workspace.py``, which returns None when the tenant
    # dimension does not apply (legacy mode) and raises when the request must be
    # refused rather than fall back to a global workspace.
    from channel.web.tenant_workspace import resolve_tenant_workspace_root

    scoped_root = resolve_tenant_workspace_root(database_mode=_is_database_identity())
    if scoped_root:
        return scoped_root
    from agent.registry import get_agent_registry

    return get_agent_registry().get(agent_id).workspace


_PREVIEW_SECRET = None
_PREVIEW_SECRET_LOCK = threading.Lock()


def _get_preview_secret() -> bytes:
    """
    Stable secret used to sign /preview directory tokens.

    Preview URLs can't rely on the auth cookie: the preview iframe is sandboxed
    without `allow-same-origin`, so its subresource requests come from an opaque
    origin and Chrome withholds the SameSite=Lax cookie. The signature in the
    URL is what authorizes the request instead, so it must survive restarts.
    """
    global _PREVIEW_SECRET
    if _PREVIEW_SECRET is not None:
        return _PREVIEW_SECRET
    with _PREVIEW_SECRET_LOCK:
        if _PREVIEW_SECRET is not None:
            return _PREVIEW_SECRET
        path = os.path.join(get_data_root(), ".preview_secret")
        secret = None
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    secret = (f.read() or "").strip() or None
        except Exception as e:
            logger.warning(f"[WebChannel] Could not read preview secret: {e}")
        if not secret:
            secret = uuid.uuid4().hex + uuid.uuid4().hex
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(secret)
                os.chmod(path, 0o600)
            except Exception as e:
                logger.warning(f"[WebChannel] Could not persist preview secret: {e}")
        _PREVIEW_SECRET = secret.encode()
        return _PREVIEW_SECRET


def _encode_dir_token(dir_path: str) -> str:
    """Encode a directory path into a signed, URL-safe token for /preview."""
    real = os.path.realpath(dir_path)
    body = base64.urlsafe_b64encode(real.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_get_preview_secret(), real.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{body}.{sig}"


def _decode_dir_token(token: str) -> str:
    """Verify and decode a /preview directory token. Raises ValueError if invalid."""
    body, _, sig = (token or "").partition(".")
    if not body or not sig:
        raise ValueError("Malformed preview token")
    padding = "=" * (-len(body) % 4)
    try:
        real = base64.urlsafe_b64decode(body + padding).decode("utf-8")
    except Exception:
        raise ValueError("Malformed preview token")
    expected = hmac.new(_get_preview_secret(), real.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Bad preview token signature")
    return real


def _platform_file_root() -> str:
    """Platform-admin read-only browse root; defaults to data root."""
    configured = (conf().get("platform_file_root") or "").strip()
    if configured:
        return os.path.realpath(os.path.expanduser(configured))
    return os.path.realpath(get_data_root())


def _serve_allowed_roots() -> list:
    """Roots that /api/file and /preview may read from (symlinks resolved).

    Includes the configured serve root, the Agent workspace, and any project
    directory a session has opened. Project dirs may live outside the serve
    root (e.g. ``/tmp/foo``), so previewing files in an opened project would
    otherwise be denied.

    Database-only default is the platform file root (never ``~`` or ``/``).
    """
    raw = conf().get("web_file_serve_root", None)
    if raw is None or str(raw).strip() == "":
        roots = [os.path.realpath(_platform_file_root())]
    else:
        roots = [os.path.realpath(os.path.expanduser(str(raw)))]
    try:
        roots.append(os.path.realpath(_get_workspace_root()))
    except Exception:
        pass
    try:
        from agent.workspace import project_store
        for rec in project_store.list_recents():
            roots.append(os.path.realpath(rec["path"]))
    except Exception:
        pass
    return roots


def _tenant_workspace_root_owners() -> list:
    """``[(realpath, agent_id_or_None)]`` every workspace the server may serve.

    Same static registration as :func:`_tenant_workspace_roots`, but keeping the
    owning Agent so the private-owner rule can be applied to a path *without* a
    request identity. ``None`` marks a tenant shared root (shared by definition).
    """
    owners = []
    try:
        from auth.service import get_identity_service
        svc = get_identity_service()
        for rec in svc.tenant_shared_roots():
            root = (rec or {}).get("shared_root")
            if root:
                owners.append((os.path.realpath(root), None))
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        for binding in svc.list_agent_bindings():
            agent_id = (binding or {}).get("agent_id")
            if not agent_id:
                continue
            try:
                workspace = registry.get(agent_id).workspace
            except (KeyError, ValueError, TypeError):
                continue
            if workspace:
                owners.append((os.path.realpath(workspace), agent_id))
    except Exception as e:
        logger.debug(f"[WebChannel] tenant workspace roots unavailable: {e}")
    return owners


def _tenant_workspace_roots() -> list:
    """Every tenant/Agent workspace the server may mint a capability for.

    ``/preview`` is capability-authorized: the HMAC directory token, not a
    request identity, authorizes the read (the sandboxed iframe cannot send the
    session cookie). This list backs the defense-in-depth root check for that
    path, so it has to cover every workspace ``_build_preview_url`` can be
    called with — each tenant's shared root and each bound Agent's workspace —
    independent of the request (a public preview request carries no identity).
    """
    return [root for root, _ in _tenant_workspace_root_owners()]


class _PrivateOwnerLookupFailed(Exception):
    """The identity store could not answer the private-owner lookup."""


def _static_path_private_owner(real_path: str) -> Optional[str]:
    """The owner of ``real_path`` when it sits in a *privately owned* workspace.

    Resolution uses the static workspace registration, so it works for a
    capability preview that carries no identity. ``None`` means "not privately
    owned" — a shared root, a shared Agent, a platform path, an unknown path, or
    an ambiguous one. Ambiguity is not resolved here; :func:`_is_path_allowed`
    already refuses what cannot be attributed, and a privately owned workspace is
    attributed the same way there.

    A lookup that *fails* is not an answer, and must not be read as one: raising
    :class:`_PrivateOwnerLookupFailed` keeps a store outage from converting a
    private file into a public one (task 3.6 property 4). "Unknown" and
    "unreachable" are opposite conclusions and are reported differently.

    This is the consumption-side half of the private-owner rule: ownership is
    re-derived from the path at read time, so a token minted while a member owned
    the workspace cannot outlive the ownership.
    """
    kind, agent_id = _db_path_owner(real_path, _tenant_workspace_root_owners())
    if kind != "agent" or not agent_id:
        return None
    from auth.service import get_identity_service
    binding = get_identity_service().get_agent_binding(agent_id)
    return (binding or {}).get("private_owner_user_id") or None


def _preview_consumer_may_read(real_path: str) -> bool:
    """Whether the *current* request may consume a capability preview of ``path``.

    Public workspace files need no identity: the HMAC token is the whole
    authorization, which is what lets an anonymous iframe render an Agent's
    generated page. A **privately owned** file is different — the token only
    proves the URL was once issued, so it must not survive as a bearer grant to
    someone else's workspace. For those the current session is resolved and must
    be the owner; no session, an expired session, or a different user all refuse.

    Anything that prevents an answer — the store failing, the session lookup
    failing — refuses too. Only a positive "this file is not private" allows the
    token through.
    """
    try:
        owner = _static_path_private_owner(real_path)
    except Exception as e:
        logger.warning(f"[WebChannel] preview ownership check failed closed: {e}")
        return False
    if owner is None:
        return True
    from channel.web.auth_handlers import _get_service, _session_token

    token = _session_token()
    if not token:
        return False
    try:
        from auth.runtime import resolve_context
        ctx = resolve_context(_get_service(), token, None)
    except Exception:
        return False
    return getattr(ctx, "user_id", None) == owner




def _is_path_allowed(real_path: str) -> bool:
    """True when ``real_path`` is under a database-safe browse root.

    Never trusts operator home / unrestricted serve roots: even if
    ``_serve_allowed_roots`` is widened (tests or misconfig), only the
    platform file root, tenant/Agent workspaces, and opened project dirs
    qualify.
    """
    roots = [os.path.realpath(_platform_file_root())]
    # Tenant/Agent roots come from the *static* workspace list rather than
    # ``_get_workspace_root()``: the latter refuses (raises ``web.HTTPError``)
    # when the request carries no tenant identity, and a public capability
    # preview has none. Constructing that error mutates ``web.ctx.status`` and
    # headers even when the exception is swallowed, corrupting the response.
    roots.extend(_tenant_workspace_roots())
    try:
        from agent.workspace import project_store
        for rec in project_store.list_recents():
            roots.append(os.path.realpath(rec["path"]))
    except Exception:
        pass
    for root in roots:
        try:
            if os.path.commonpath([real_path, root]) == root:
                return True
        except ValueError:
            continue
    return False


def _build_preview_url(abs_path: str) -> str:
    """
    Preview URL that mounts the file's *directory*, so relative assets
    referenced by an HTML page (./style.css, ./img/a.png) resolve correctly.
    """
    directory = os.path.dirname(abs_path)
    name = os.path.basename(abs_path)
    return f"/preview/{_encode_dir_token(directory)}/{quote(name)}"


def _build_artifact_payload(data: dict) -> dict:
    """Turn an agent `artifact` event into an SSE payload for the web clients."""
    file_path = data.get("path", "")
    if not file_path:
        return None
    return {
        "type": "artifact",
        "abs_path": file_path,
        "rel_path": data.get("rel_path") or os.path.basename(file_path),
        "file_name": data.get("file_name") or os.path.basename(file_path),
        "kind": data.get("kind", "file"),
        "previewable": bool(data.get("previewable")),
        "size": data.get("size", 0),
        "raw_url": f"/api/file?path={quote(file_path)}",
        "preview_url": _build_preview_url(file_path),
    }


def _paths_written_by_step(step: dict) -> list:
    """Files a persisted tool step produced, if any.

    `write`/`edit` name theirs in the arguments. A `subagent` step lists the
    ones its sub agents wrote in its result: those files never passed through
    a tool call of this agent's own, so nothing else records them.
    """
    name = step.get("name")
    if name in ("write", "edit"):
        args = step.get("arguments")
        path = str((args or {}).get("path") or "").strip() if isinstance(args, dict) else ""
        return [path] if path else []
    if name != "subagent":
        return []
    try:
        results = json.loads(step.get("result") or "{}").get("results") or []
    except (ValueError, TypeError, AttributeError):
        return []
    return [
        path
        for item in results if isinstance(item, dict)
        for path in (item.get("files") or [])
    ]


def _artifacts_from_steps(steps, session_id: str = None, agent_id: str = None) -> list:
    """
    Rebuild the artifact cards of a persisted assistant message.

    History replay has no SSE events, so the tool calls are the only record.
    Doing this server-side keeps one implementation of the workspace-internal
    filter — and lets absolute paths inside the workspace be recognised, which
    a client mirroring the rules can't do.

    ``session_id`` anchors detection to the session's working dir (the project
    dir when one is open), matching the live SSE path; otherwise state_root.
    """
    from agent.protocol.artifact import get_workspace_root, safe_build_artifact

    out = []
    seen = set()
    root = None
    for step in steps or []:
        if not isinstance(step, dict) or step.get("type") != "tool" or step.get("is_error"):
            continue
        for path in _paths_written_by_step(step):
            if root is None:
                root = _get_workspace_root(session_id, agent_id) if session_id else get_workspace_root()
            info = safe_build_artifact(path, root)
            if not info or info["path"] in seen:
                continue
            seen.add(info["path"])
            payload = _build_artifact_payload(info)
            if payload:
                out.append(payload)
    return out


def _add_subagent_displays(steps) -> None:
    """Give persisted `subagent` steps the same readable form they had live.

    `display` is deliberately kept out of the model's context, so it is not in
    the stored conversation either. Rebuilding it here means a reloaded page
    shows the sub agents' reports rather than the JSON the model was handed.
    """
    from agent.tools.subagent import format_results

    for step in steps or []:
        if not isinstance(step, dict) or step.get("name") != "subagent":
            continue
        try:
            results = json.loads(step.get("result") or "{}").get("results")
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(results, list) and results:
            step["display"] = format_results(results)


def _add_delegate_displays(steps) -> None:
    """Give persisted `agent_delegate` steps the readable form they had live.

    Same story as `_add_subagent_displays`: `display` is kept out of the model's
    context and so out of storage, so a reloaded page would otherwise show the
    JSON handed to the model rather than "who → whom" and the teammate's reply.
    """
    from agent.tools.agent_delegate.agent_delegate import format_delegate_result

    for step in steps or []:
        if not isinstance(step, dict) or step.get("name") != "agent_delegate":
            continue
        try:
            payload = json.loads(step.get("result") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or not payload.get("content"):
            continue
        source_id = payload.get("delegated_by") or ""
        source_name = source_id
        try:
            from bridge.bridge import Bridge

            source_name = (
                Bridge().get_agent_bridge().agent_registry.get(source_id).name
                or source_id
            )
        except Exception:
            pass
        step["display"] = format_delegate_result(
            source_name,
            payload.get("agent_name") or payload.get("agent_id") or "",
            payload.get("content") or "",
            status=payload.get("status") or "done",
        )


def _sanitize_upload_relative_path(relative_path: str) -> str:
    """Normalize relative upload path and reject escapes / absolute paths."""
    relative_path = (relative_path or "").replace("\\", "/").strip("/")
    if not relative_path:
        raise ValueError("Empty relative path")
    parts = []
    for part in relative_path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Invalid relative path")
        parts.append(part)
    if not parts:
        raise ValueError("Invalid relative path")
    norm_path = "/".join(parts)
    if os.path.isabs(norm_path):
        raise ValueError("Invalid relative path")
    return norm_path


def _sanitize_upload_id(upload_id: str) -> str:
    """Allow only simple batch ids for directory uploads."""
    sanitized = "".join(ch for ch in (upload_id or "") if ch.isalnum() or ch in ("-", "_"))
    if not sanitized:
        raise ValueError("Invalid upload id")
    return sanitized[:80]


def _is_within_directory(root_path: str, target_path: str) -> bool:
    try:
        return os.path.commonpath([root_path, target_path]) == root_path
    except ValueError:
        return False


def _resolve_upload_path(upload_root: str, relative_path: str) -> Tuple[str, str]:
    """Resolve a relative upload path under upload_root and reject escapes."""
    safe_rel_path = _sanitize_upload_relative_path(relative_path)
    upload_root_real = os.path.realpath(upload_root)
    save_path = os.path.realpath(os.path.join(upload_root_real, *safe_rel_path.split("/")))
    if not _is_within_directory(upload_root_real, save_path):
        raise ValueError("Invalid directory upload path")
    return safe_rel_path, save_path


def _read_uploaded_file_bytes(file_obj) -> bytes:
    """Return uploaded content as bytes across web.py upload object variants."""
    if isinstance(file_obj, bytes):
        return file_obj
    if isinstance(file_obj, str):
        return file_obj.encode("utf-8")

    content = None

    if hasattr(file_obj, "file") and hasattr(file_obj.file, "read"):
        content = file_obj.file.read()
    elif hasattr(file_obj, "read"):
        content = file_obj.read()
    elif hasattr(file_obj, "value"):
        content = file_obj.value

    if content is None:
        raise ValueError("Unable to read uploaded file content")
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError(f"Unsupported uploaded content type: {type(content).__name__}")


def _read_uploaded_file_bytes_limited(file_obj, max_bytes: int) -> bytes:
    """Read uploaded content and fail once it exceeds max_bytes."""
    if isinstance(file_obj, bytes):
        content = file_obj
    elif isinstance(file_obj, str):
        content = file_obj.encode("utf-8")
    elif hasattr(file_obj, "file") and hasattr(file_obj.file, "read"):
        content = file_obj.file.read(max_bytes + 1)
    elif hasattr(file_obj, "read"):
        content = file_obj.read(max_bytes + 1)
    elif hasattr(file_obj, "value"):
        content = file_obj.value
    else:
        raise ValueError("Unable to read uploaded file content")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not isinstance(content, bytes):
        raise TypeError(f"Unsupported uploaded content type: {type(content).__name__}")
    if len(content) > max_bytes:
        raise ValueError("file too large")
    return content


def _raw_web_input():
    """Return unprocessed multipart form data when web.py exposes rawinput."""
    rawinput = getattr(getattr(web, "webapi", None), "rawinput", None)
    if not callable(rawinput):
        raise RuntimeError("web.py rawinput is not available")
    try:
        return rawinput(method="post")
    except TypeError:
        return rawinput()


def _ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _generate_session_title(user_message: str, assistant_reply: str = "",
                            session_id: str = "") -> str:
    """Delegate to the shared SessionService implementation."""
    from agent.chat.session_service import generate_session_title
    return generate_session_title(user_message, assistant_reply, session_id)


class WebMessage(ChatMessage):
    def __init__(
            self,
            msg_id,
            content,
            ctype=ContextType.TEXT,
            from_user_id="User",
            to_user_id="Chatgpt",
            other_user_id="Chatgpt",
    ):
        self.msg_id = msg_id
        self.ctype = ctype
        self.content = content
        self.from_user_id = from_user_id
        self.to_user_id = to_user_id
        self.other_user_id = other_user_id


# Full URL table for the Web console, DERIVED from the single authoritative
# route registry (``channel.web.route_registry``). Do not add routes here: add a
# ``RouteEntry`` to the registry so the URL table and the authorization policy
# table (``auth.http_policy.ROUTE_POLICY``) cannot drift apart again. Order is
# preserved from the registry and is behavior-significant (first match wins).
_WEB_URLS = _derive_web_urls()


def build_web_app():
    """Build the real web.py console application (used by dev server/testing).

    Installs the shared HTTP-method policy processor so the production server
    and the test harness enforce the same route/method authorization gate. In
    database identity mode, a multi-worker deployment is rejected because the
    in-process login limiter / identity state are single-process only.
    """
    from auth.http_policy import enforce_http_policy
    from auth.ratelimit import reject_multi_worker_identity
    reject_multi_worker_identity()
    app = web.application(_WEB_URLS, globals(), autoreload=False)
    app.add_processor(enforce_http_policy)
    return app


@singleton
class WebChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = [ReplyType.VOICE]
    _instance = None
    SSE_REPLAY_MAX_EVENTS = 5000
    SSE_REPLAY_MAX_BYTES = 4 * 1024 * 1024
    SSE_POST_DONE_TAIL_SECONDS = 60
    SSE_COMPLETED_TTL_SECONDS = 60
    SSE_IDLE_TIMEOUT_SECONDS = 1800

    # def __new__(cls):
    #     if cls._instance is None:
    #         cls._instance = super(WebChannel, cls).__new__(cls)
    #     return cls._instance

    def __init__(self):
        super().__init__()
        self.msg_id_counter = 0
        self.session_queues = {}  # session_id -> Queue (fallback polling)
        self.request_to_session = {}  # request_id -> session_id
        self.request_to_agent = {}  # request_id -> agent_id
        self.request_owners = {}  # request_id -> immutable (tenant, user, agent, session)
        self.sse_streams = {}  # request_id -> SSEStreamState
        self._sse_streams_lock = threading.RLock()
        self._http_server = None
        self._sse_janitor_started = False

    def _generate_msg_id(self):
        """生成唯一的消息ID"""
        self.msg_id_counter += 1
        return str(int(time.time())) + str(self.msg_id_counter)

    def _generate_request_id(self):
        """生成唯一的请求ID"""
        return str(uuid.uuid4())

    def _publish_sse_event(self, request_id: str, event: dict) -> bool:
        """Append one sequenced event and wake every connected reader."""
        with self._sse_streams_lock:
            state = self.sse_streams.get(request_id)
        if state is None:
            logger.warning(
                f"[WebChannel] dropped SSE event for unknown request "
                f"{request_id}: type={event.get('type')}"
            )
            return False

        with state.condition:
            if state.closed or state.stream_complete:
                reason = "closed" if state.closed else "complete"
                logger.warning(
                    f"[WebChannel] dropped SSE event for {reason} stream "
                    f"{request_id}: type={event.get('type')}"
                )
                return False
            item = dict(event)
            item["seq"] = state.next_seq
            state.next_seq += 1
            encoded_size = len(json.dumps(
                item, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"))
            state.events.append((item, encoded_size))
            state.total_bytes += encoded_size
            state.last_active = time.time()

            # Keep at least the newest event even if it alone exceeds the byte
            # budget. Cursor expiry is reported explicitly by stream_response.
            while len(state.events) > 1 and (
                len(state.events) > self.SSE_REPLAY_MAX_EVENTS
                or state.total_bytes > self.SSE_REPLAY_MAX_BYTES
            ):
                _, removed_size = state.events.popleft()
                state.total_bytes -= removed_size

            event_type = item.get("type")
            if event_type == "done":
                state.main_done = True
                if state.main_done_at is None:
                    state.main_done_at = state.last_active
            elif event_type == "stream_end":
                state.stream_complete = True
                state.completed_at = state.last_active
            state.condition.notify_all()
        return True

    @staticmethod
    def _session_queue_key(session_id: str, agent_id: str = None) -> str:
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        resolved = registry.get(agent_id).id
        if resolved == registry.default_agent_id:
            return session_id
        return f"{resolved}::{session_id}"

    def has_session_queue(self, session_id: str, agent_id: str = None) -> bool:
        return self._session_queue_key(session_id, agent_id) in self.session_queues

    def _fetch_latest_pair_seqs(self, session_id: str, agent_id: str = None):
        """Query the conversation store for the latest user/bot message seqs.

        Returned as ``{"user_seq": int|None, "bot_seq": int|None}``; used to
        attach seq metadata onto the SSE ``done`` event so the frontend can
        wire edit / regenerate buttons for live-streamed bubbles without a
        page refresh.
        """
        try:
            from agent.registry import get_agent_registry
            from agent.memory import get_conversation_store
            profile = get_agent_registry().get(agent_id)
            return get_conversation_store(profile.workspace).get_latest_pair_seqs(
                session_id
            )
        except Exception as e:
            logger.debug(f"[WebChannel] _fetch_latest_pair_seqs failed: {e}")
            return {"user_seq": None, "bot_seq": None}

    def send(self, reply: Reply, context: Context):
        try:
            if reply.type in self.NOT_SUPPORT_REPLYTYPE:
                logger.warning(f"Web channel doesn't support {reply.type} yet")
                return

            if reply.type == ReplyType.IMAGE_URL:
                time.sleep(0.5)

            request_id = context.get("request_id", None)
            if not request_id:
                logger.error("No request_id found in context, cannot send message")
                return

            session_id = self.request_to_session.get(request_id)
            if not session_id:
                logger.error(f"No session_id found for request {request_id}")
                return
            agent_id = context.get("agent_id") or self.request_to_agent.get(request_id)
            session_queue_key = self._session_queue_key(session_id, agent_id)

            # SSE mode: append events to the replay log.
            if request_id in self.sse_streams:
                content = reply.content if reply.content is not None else ""

                # Intermediate status lines (e.g. /install-browser phases) must NOT use "done",
                # or the frontend closes EventSource and drops subsequent events.
                if getattr(reply, "sse_phase", False):
                    self._publish_sse_event(request_id, {
                        "type": "phase",
                        "content": content,
                        "request_id": request_id,
                        "timestamp": time.time(),
                    })
                    logger.debug(f"SSE phase for request {request_id}")
                    return

                # Files are already pushed via on_event (file_to_send) during agent execution.
                # Skip duplicate file pushes here; just let the done event through.
                if reply.type in (ReplyType.IMAGE_URL, ReplyType.FILE) and content.startswith("file://"):
                    text_content = getattr(reply, 'text_content', '')
                    with self._sse_streams_lock:
                        state = self.sse_streams.get(request_id)
                    already_done = False
                    if state is not None:
                        with state.condition:
                            already_done = state.main_done
                    # A preceding TEXT reply may already have published done
                    # and deliberately left the stream open for auto-TTS. In
                    # that case this duplicate media reply must not end it.
                    if text_content and not already_done:
                        seqs = self._fetch_latest_pair_seqs(
                            session_id, context.get("agent_id")
                        )
                        published = self._publish_sse_event(request_id, {
                            "type": "done",
                            "content": text_content,
                            "request_id": request_id,
                            "timestamp": time.time(),
                            "user_seq": seqs.get("user_seq"),
                            "bot_seq": seqs.get("bot_seq"),
                        })
                        if published:
                            self._publish_sse_event(
                                request_id, {"type": "stream_end"}
                            )
                    logger.debug(f"SSE skipped duplicate file for request {request_id}")
                    return

                # Skip http-URL FILE/IMAGE_URL replies produced by chat_channel's media extraction:
                # the text reply (already sent as "done") contains the URL and the frontend will
                # render it via renderMarkdown/injectVideoPlayers, so no separate SSE event needed.
                if reply.type in (ReplyType.FILE, ReplyType.IMAGE_URL) and content.startswith(("http://", "https://")):
                    logger.debug(f"SSE skipped http media reply for request {request_id}")
                    return

                seqs = self._fetch_latest_pair_seqs(
                    session_id, context.get("agent_id")
                )
                self._publish_sse_event(request_id, {
                    "type": "done",
                    "content": content,
                    "request_id": request_id,
                    "timestamp": time.time(),
                    "user_seq": seqs.get("user_seq"),
                    "bot_seq": seqs.get("bot_seq"),
                })
                logger.debug(f"SSE done sent for request {request_id}")
                # Auto-trigger TTS once the bot finishes its text reply. The
                # synthesis runs in the background so the chat stream is never
                # blocked; the resulting audio URL is pushed via a follow-up
                # `voice_attach` SSE event and persisted to messages.extras.
                tts_pending = False
                if reply.type == ReplyType.TEXT and content.strip():
                    tts_pending = self._maybe_dispatch_auto_tts(
                        request_id, session_id, content, context
                    )
                if not tts_pending:
                    self._publish_sse_event(request_id, {"type": "stream_end"})
                return

            # Fallback: polling mode
            if session_queue_key in self.session_queues:
                content = reply.content if reply.content is not None else ""
                # Skip file:// IMAGE_URL/FILE replies originating from an SSE-enabled
                # request: they were already pushed via the `file_to_send` event during
                # agent execution. By the time the chat_channel sends the IMAGE_URL reply,
                # the SSE stream has typically closed (after the text "done") and the
                # request_id is gone from sse_streams, so we'd otherwise duplicate the file
                # as a polling bubble. Scheduler/push tasks have no on_event and must
                # still go through polling normally.
                if (
                    reply.type in (ReplyType.IMAGE_URL, ReplyType.FILE)
                    and content.startswith("file://")
                    and context.get("on_event") is not None
                ):
                    logger.debug(f"Polling skipped duplicate file reply for session {session_id}")
                    return
                # SSE-enabled requests already stream the text reply to the
                # client. Do NOT also enqueue it for polling: if the user
                # switched away mid-run, the queued copy would resurface as a
                # duplicate bubble when they return and poll the session.
                if reply.type == ReplyType.TEXT and context.get("on_event") is not None:
                    logger.debug(f"Polling skipped SSE text reply for session {session_id}")
                    return
                response_data = {
                    "type": str(reply.type),
                    "content": content,
                    "timestamp": time.time(),
                    "request_id": request_id
                }
                self.session_queues[session_queue_key].put(response_data)
                logger.debug(f"Response sent to poll queue for session {session_id}, request {request_id}")
            else:
                logger.warning(f"No response queue found for session {session_id}, response dropped")

        except Exception as e:
            logger.error(f"Error in send method: {e}")

    def _make_sse_callback(self, request_id: str):
        """Build a callback that publishes agent events to the SSE replay log."""

        # Cap reasoning bytes pushed to the frontend per request to avoid
        # browser stalls / crashes on very long chains-of-thought. Anything
        # beyond the cap is dropped from the stream (DB still persists a
        # truncated copy via _truncate_reasoning_for_storage).
        # Keep aligned with frontend REASONING_RENDER_CAP and backend
        # MAX_STORED_REASONING_CHARS.
        MAX_REASONING_STREAM_CHARS = 4 * 1024  # 4 KB
        # A tool's human-readable outcome (ToolResult.display). Reasoning is a
        # trace worth capping hard; this is the deliverable, so it gets room.
        MAX_DISPLAY_STREAM_CHARS = 32 * 1024
        # Use a single-element list as a mutable counter accessible from closure.
        reasoning_chars_sent = [0]
        reasoning_capped_notified = [False]
        # Captures the first error message emitted by agent_stream so the
        # subsequent agent_end handler can skip its "empty final_response"
        # fallback (which would otherwise overwrite the real error).
        streamed_error: List[str] = []

        def on_event(event: dict):
            if request_id not in self.sse_streams:
                return
            publish = lambda item: self._publish_sse_event(request_id, item)
            event_type = event.get("type")
            data = event.get("data", {})

            if event_type == "reasoning_update":
                delta = data.get("delta", "")
                if not delta:
                    return
                remaining = MAX_REASONING_STREAM_CHARS - reasoning_chars_sent[0]
                if remaining <= 0:
                    if not reasoning_capped_notified[0]:
                        reasoning_capped_notified[0] = True
                        publish({
                            "type": "reasoning",
                            "content": "\n\n... [reasoning truncated for display] ...",
                        })
                    return
                if len(delta) > remaining:
                    delta = delta[:remaining]
                reasoning_chars_sent[0] += len(delta)
                publish({"type": "reasoning", "content": delta})

            elif event_type == "message_update":
                delta = data.get("delta", "")
                if delta:
                    publish({"type": "delta", "content": delta})

            elif event_type == "tool_execution_start":
                tool_name = data.get("tool_name", "tool")
                arguments = data.get("arguments", {})
                publish({"type": "tool_start", "tool_call_id": data.get("tool_call_id"), "tool": tool_name, "arguments": arguments})

            elif event_type == "tool_execution_progress":
                publish({
                    "type": "tool_progress",
                    "tool_call_id": data.get("tool_call_id"),
                    "tool": data.get("tool_name", "tool"),
                    "content": str(data.get("message", ""))[-4 * 1024:],
                })

            elif event_type == "tool_execution_end":
                tool_name = data.get("tool_name", "tool")
                status = data.get("status", "success")
                result = data.get("result", "")
                exec_time = data.get("execution_time", 0)
                # Truncate long results to avoid huge SSE payloads
                result_str = str(result)
                if len(result_str) > 2000:
                    result_str = result_str[:2000] + "…"
                payload = {
                    "type": "tool_end",
                    "tool_call_id": data.get("tool_call_id"),
                    "tool": tool_name,
                    "status": status,
                    "result": result_str,
                    "execution_time": round(exec_time, 2)
                }
                # Carry the permission-refusal marker so the UI can explain why
                # the call was refused. Only a legacy mode refusal carries the
                # mode; database-mode role/isolation refusals carry the kind.
                if data.get("permission_denied"):
                    payload["permission_denied"] = True
                    payload["permission_mode"] = data.get("permission_mode")
                    payload["permission_denial_kind"] = data.get("permission_denial_kind")
                # A tool that wrote its outcome for a person sends that
                # instead. It gets a far larger budget than `result`: this is
                # the report itself, not a trace of how it was produced.
                display = data.get("display")
                if display:
                    display = str(display)
                    if len(display) > MAX_DISPLAY_STREAM_CHARS:
                        display = display[:MAX_DISPLAY_STREAM_CHARS] + "…"
                    payload["display"] = display
                publish(payload)

            elif event_type == "subagent_step":
                # A tool call made by a sub agent, relayed so the card for
                # that sub agent can show what it is doing instead of
                # spinning for minutes.
                publish({
                    "type": "subagent_step",
                    "card_id": data.get("card_id"),
                    "step_id": data.get("step_id"),
                    "phase": data.get("phase"),
                    "tool": data.get("tool_name", "tool"),
                    "arguments": data.get("arguments") or {},
                    "status": data.get("status"),
                    "error": data.get("error"),
                    "execution_time": data.get("execution_time", 0),
                })

            elif event_type == "message_end":
                tool_calls = data.get("tool_calls", [])
                if tool_calls:
                    publish({"type": "message_end", "has_tool_calls": True})

            elif event_type == "error":
                # Agent raised an exception (LLM 401/timeout/etc). Surface the
                # real message instead of letting the empty-response fallback
                # below hide it as "(模型未返回任何内容)".
                err_msg = data.get("error") or "unknown error"
                logger.warning(
                    f"[WebChannel] agent_stream emitted error for "
                    f"request {request_id}: {err_msg}"
                )
                # Remember it so the agent_end handler below knows not to
                # rewrite the message into a generic empty-response notice.
                streamed_error.append(err_msg)
                publish({
                    "type": "done",
                    "content": f"❌ {err_msg}",
                    "request_id": request_id,
                    "timestamp": time.time(),
                })
                publish({"type": "stream_end"})

            elif event_type == "agent_cancelled":
                # Push an explicit cancelled SSE event so the frontend
                # marks the bubble as stopped. A trailing "done" still
                # arrives with the partial answer.
                final_response = data.get("final_response", "")
                publish({
                    "type": "cancelled",
                    "content": final_response,
                    "request_id": request_id,
                    "timestamp": time.time(),
                })

            elif event_type == "agent_end":
                # Safety net: if the agent finishes with an empty final_response,
                # chat_channel skips _send_reply (because reply.content is empty),
                # which means no "done" event is ever emitted and the SSE stream
                # would hang until the 10-min idle timeout. Push a fallback "done"
                # here so the frontend always gets closure.
                final_response = data.get("final_response", "")
                if not final_response or not str(final_response).strip():
                    if streamed_error:
                        # Error was already surfaced via the `error` event
                        # handler above; nothing more to do here.
                        pass
                    else:
                        logger.warning(
                            f"[WebChannel] agent_end with empty final_response for "
                            f"request {request_id}, sending fallback done"
                        )
                        publish({
                            "type": "done",
                            "content": i18n.t(
                                "(模型未返回任何内容，请重试或换一种方式描述你的需求)",
                                "(The model returned no content. Please retry or rephrase your request.)",
                            ),
                            "request_id": request_id,
                            "timestamp": time.time(),
                        })
                        publish({"type": "stream_end"})

            elif event_type == "file_to_send":
                file_path = data.get("path", "")
                file_name = data.get("file_name", os.path.basename(file_path))
                file_type = data.get("file_type", "file")
                # Remote URLs are passed through as-is; local files are served
                # via the backend /api/file endpoint.
                remote_url = data.get("url", "")
                is_remote = bool(remote_url) and remote_url.lower().startswith(("http://", "https://"))
                if is_remote:
                    web_url = remote_url
                else:
                    from urllib.parse import quote
                    web_url = f"/api/file?path={quote(file_path)}"
                is_image = file_type == "image"
                payload = {
                    "type": "image" if is_image else "file",
                    "content": web_url,
                    "file_name": file_name,
                    # Preserve the concrete media kind (image/video/audio/...)
                    # so richer clients can render an inline player.
                    "file_type": file_type,
                }
                # Expose the local absolute path so the desktop client can open
                # the file directly (Finder / default app) instead of the browser.
                if not is_remote and file_path:
                    payload["abs_path"] = file_path
                publish(payload)

            elif event_type == "artifact":
                payload = _build_artifact_payload(data)
                if payload:
                    publish(payload)

        return on_event

    # ------------------------------------------------------------------
    # TTS auto-dispatch
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_voice_reply_mode() -> str:
        """
        Decide the TTS auto-reply policy.

        Source of truth is the cross-channel pair
        (`always_reply_voice`, `voice_reply_voice`) which chat_channel
        also consults. The web UI presents these as a single three-state
        picker (off / voice_if_voice / always) via a lossless mapping.
        """
        if conf().get("always_reply_voice", False):
            return "always"
        if conf().get("voice_reply_voice", False):
            return "voice_if_voice"
        return "off"

    # Mirror of ModelsHandler._TTS_PROVIDERS. zhipu is intentionally omitted
    # from the UI (GLM-TTS prelude beep); pinning it in config.json still works.
    _TTS_PROVIDERS_SUGGEST_ORDER = ["openai", "minimax", "dashscope", "linkai"]

    @classmethod
    def _tts_provider_ready(cls) -> bool:
        """True if user picked a provider OR any suggested vendor has an API key."""
        if (conf().get("text_to_voice") or "").strip():
            return True
        for pid in cls._TTS_PROVIDERS_SUGGEST_ORDER:
            meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
            key_field = meta.get("api_key_field")
            if not key_field:
                continue
            val = (conf().get(key_field) or "").strip()
            if val and val not in ("YOUR API KEY", "YOUR_API_KEY"):
                return True
        return False

    def _maybe_dispatch_auto_tts(
        self,
        request_id: str,
        session_id: str,
        text: str,
        context: dict,
    ) -> bool:
        try:
            mode = self._resolve_voice_reply_mode()
            if mode == "off":
                return False
            if mode == "voice_if_voice" and not context.get("is_voice_input"):
                return False
            if not self._tts_provider_ready():
                return False
            threading.Thread(
                target=self._synthesize_tts_async,
                args=(request_id, session_id, text, context.get("agent_id")),
                daemon=True,
            ).start()
            return True
        except Exception as e:
            logger.debug(f"[WebChannel] auto-tts dispatch skipped: {e}")
            return False

    def _synthesize_tts_async(
        self,
        request_id: str,
        session_id: str,
        text: str,
        agent_id: str = None,
    ) -> None:
        try:
            from bridge.bridge import Bridge
            reply = Bridge().fetch_text_to_voice(text)
            if reply is None or reply.type != ReplyType.VOICE or not reply.content:
                logger.warning(
                    f"[WebChannel] TTS produced no audio for request {request_id}: "
                    f"reply={reply}"
                )
                return
            url = self._publish_tts_audio(reply.content, agent_id)
            if not url:
                logger.warning(f"[WebChannel] TTS publish failed for request {request_id}")
                return
            payload = {"audio": {"url": url, "kind": "tts"}}
            try:
                from agent.memory import get_conversation_store
                from agent.registry import get_agent_registry
                profile = get_agent_registry().get(agent_id)
                get_conversation_store(
                    profile.workspace
                ).attach_extras_to_last_assistant(session_id, payload)
            except Exception as e:
                logger.debug(f"[WebChannel] tts persist skipped: {e}")
            if request_id not in self.sse_streams:
                logger.warning(
                    f"[WebChannel] TTS ready but SSE stream already closed "
                    f"for request {request_id} (url={url})"
                )
                return
            self._publish_sse_event(request_id, {
                "type": "voice_attach",
                "url": url,
                "request_id": request_id,
                "timestamp": time.time(),
            })
            logger.info(f"[WebChannel] TTS voice_attach pushed for request {request_id}: {url}")
        except Exception as e:
            # TTS failures are intentionally silent (no user-facing error).
            logger.warning(f"[WebChannel] TTS synthesis failed: {e}")
        finally:
            self._publish_sse_event(request_id, {"type": "stream_end"})

    @staticmethod
    def _publish_tts_audio(src_path: str, agent_id: str = None) -> str:
        """Move a TTS file into uploads/ and return its public URL."""
        try:
            if not src_path or not os.path.isfile(src_path):
                logger.warning(f"[WebChannel] publish_tts_audio missing source: {src_path!r}")
                return ""
            ext = os.path.splitext(src_path)[1].lower() or ".mp3"
            upload_dir = _get_upload_dir(agent_id)
            os.makedirs(upload_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            dst_name = f"voice_reply_{ts}_{random.randint(0, 9999)}{ext}"
            dst_path = os.path.join(upload_dir, dst_name)
            shutil.move(src_path, dst_path)
            logger.debug(f"[WebChannel] publish_tts_audio moved {src_path} -> {dst_path}")
            suffix = f"?agent_id={agent_id}" if agent_id else ""
            return f"/uploads/{dst_name}{suffix}"
        except Exception as e:
            logger.warning(f"[WebChannel] publish_tts_audio failed: {e}")
            return ""

    @staticmethod
    def _cleanup_stale_voice_recordings(max_age_seconds: int = 3600) -> None:
        """Drop voice_input_* uploads older than max_age_seconds (run at startup)."""
        try:
            upload_dir = _get_upload_dir()
            if not os.path.isdir(upload_dir):
                return
            now = time.time()
            removed = 0
            for name in os.listdir(upload_dir):
                if not name.startswith("voice_input_"):
                    continue
                full = os.path.join(upload_dir, name)
                try:
                    if not os.path.isfile(full):
                        continue
                    if now - os.path.getmtime(full) > max_age_seconds:
                        os.remove(full)
                        removed += 1
                except OSError:
                    continue
            if removed:
                logger.info(f"[WebChannel] cleaned up {removed} stale voice recording(s) from {upload_dir}")
        except Exception as e:
            logger.warning(f"[WebChannel] voice cleanup failed: {e}")

    def upload_file(self):
        """Handle file or directory upload via multipart/form-data.

        The target Agent is not a parameter: the handler authorizes it (tenant
        binding, private-owner rule, ``agent.use``) and publishes it through the
        request-scoped authorized target (tasks 8.2/8.3), so a cross-tenant
        ``agent_id`` in the form field can never steer the write and upstream's
        signature stays exactly as upstream wrote it.
        """
        agent_id = authorized_target().get("agent_id")

        def _reject(message):
            logger.warning("[WebChannel] Upload rejected: %s", message)
            return json.dumps({"status": "error", "message": message})

        try:
            # Trace the request on arrival: it is the only way to tell a client
            # that never sent anything (file picker / drag-drop broken) apart
            # from a request the backend rejected.
            logger.info(
                "[WebChannel] Upload request received: %s bytes, content-type=%s",
                web.ctx.env.get("CONTENT_LENGTH") or "?",
                web.ctx.env.get("CONTENT_TYPE") or "?",
            )
            params = _raw_web_input()
            file_obj = params.get("file")
            file_objs = params.get("files")
            session_id = params.get("session_id", "")
            relative_path = params.get("relative_path", "")
            relative_paths = params.get("relative_paths")
            upload_id = params.get("upload_id", "")

            directory_files = _ensure_list(file_objs)

            # NOTE: cgi.FieldStorage raises TypeError on truthy checks for single-file
            # uploads (Python 3.9+). Always use `is not None` instead of `if file_obj`.
            if not directory_files and file_obj is not None and relative_path:
                directory_files = [file_obj]

            directory_rel_paths = _ensure_list(relative_paths)

            if not directory_rel_paths and relative_path:
                directory_rel_paths = [relative_path]

            is_directory_upload = bool(directory_files) or bool(directory_rel_paths) or bool(relative_path) or bool(upload_id)

            upload_dir = _get_upload_dir(agent_id or _request_agent_id(params))
            if is_directory_upload:
                if not upload_id:
                    return _reject("Missing upload_id for directory upload")
                if not directory_files:
                    return _reject("No files uploaded")
                if len(directory_files) != len(directory_rel_paths):
                    return _reject("Directory upload payload mismatch")

                safe_upload_id = _sanitize_upload_id(upload_id)
                upload_root = os.path.join(upload_dir, f"webdir_{safe_upload_id}")
                upload_root_real = os.path.realpath(upload_root)

                root_name = None
                saved_files = 0
                for file_obj, rel_path in zip(directory_files, directory_rel_paths):
                    if file_obj is None:
                        raise ValueError("Invalid uploaded file")
                    safe_rel_path, save_path = _resolve_upload_path(upload_root_real, rel_path)
                    current_root_name = safe_rel_path.split("/", 1)[0]
                    if root_name is None:
                        root_name = current_root_name
                    elif root_name != current_root_name:
                        raise ValueError("Directory upload must use a single root folder")
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    content_bytes = _read_uploaded_file_bytes(file_obj)
                    with open(save_path, "wb") as f:
                        f.write(content_bytes)
                    saved_files += 1

                if not root_name:
                    raise ValueError("Directory root path missing")

                root_path = os.path.realpath(os.path.join(upload_root_real, root_name))
                if not _is_within_directory(upload_root_real, root_path):
                    raise ValueError("Invalid directory upload path")

                logger.info(f"[WebChannel] Directory uploaded: {root_name} -> {root_path} ({saved_files} files)")
                return json.dumps({
                    "status": "success",
                    "file_path": root_path,
                    "file_name": root_name,
                    "file_type": "directory",
                    "file_count": saved_files,
                    "root_path": root_path,
                    "root_name": root_name,
                    "upload_type": "directory",
                }, ensure_ascii=False)

            if file_obj is None or not hasattr(file_obj, "filename") or not file_obj.filename:
                return _reject(f"No file uploaded (form fields: {sorted(params.keys())})")

            original_name = file_obj.filename
            ext = os.path.splitext(original_name)[1].lower()
            safe_name = f"web_{uuid.uuid4().hex[:8]}{ext}"
            save_path = os.path.join(upload_dir, safe_name)
            public_path = safe_name
            display_name = original_name

            content_bytes = _read_uploaded_file_bytes(file_obj)
            with open(save_path, "wb") as f:
                f.write(content_bytes)

            if ext in IMAGE_EXTENSIONS:
                file_type = "image"
            elif ext in VIDEO_EXTENSIONS:
                file_type = "video"
            else:
                file_type = "file"

            from urllib.parse import quote
            preview_url = f"/uploads/{quote(public_path, safe='/')}"
            # Name the writing Agent explicitly. The read-back route derives its
            # tenant from the addressed Agent's binding, and the browser loads
            # this URL directly as an <img>/<audio> subresource (so the console's
            # fetch wrapper never sees it). Without the id the read would fall
            # back to the tenant's *default* Agent and 404 for any other one.
            if agent_id:
                preview_url += f"?agent_id={quote(str(agent_id), safe='')}"

            logger.info(f"[WebChannel] File uploaded: {original_name} -> {save_path} ({file_type})")

            return json.dumps({
                "status": "success",
                "file_path": save_path,
                "file_name": display_name,
                "file_type": file_type,
                "preview_url": preview_url,
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"[WebChannel] File upload error: {e}", exc_info=True)
            return json.dumps({"status": "error", "message": str(e)})

    def post_message(self):
        """
        Handle incoming messages from users via POST request.
        Returns a request_id for tracking this specific request.
        Supports optional attachments (file paths from /upload).

        Any database-mode chat context (the resolved tenant membership and the
        already-authorized ``(agent_id, session_id)`` pair) arrives through the
        request-scoped authorized target rather than as parameters, so upstream's
        signature and body stay mergeable (tasks 8.2/8.3). With no target
        published this behaves exactly as upstream: the Agent is resolved by the
        router from the request body.
        """
        target = authorized_target()
        auth_context = target.get("auth_context")
        authorized_session = target.get("session")
        try:
            data = web.data()
            json_data = json.loads(data)
            session_id = json_data.get('session_id', f'session_{int(time.time())}')
            from bridge.bridge import Bridge
            agent_bridge = Bridge().get_agent_bridge()
            if authorized_session is not None:
                resolved_agent_id, session_id = authorized_session
            else:
                resolved_agent_id = agent_bridge.agent_router.resolve(
                    explicit_agent_id=json_data.get("agent_id"),
                )
            prompt = json_data.get('message', '')
            # Kept before any prefixing or attachment lines, so mention parsing
            # still sees what the user actually typed.
            typed_prompt = prompt
            use_sse = json_data.get('stream', True)
            attachments = json_data.get('attachments', [])
            # Tag the message as originating from voice input so the post-reply
            # TTS hook can honour the `voice_if_voice` policy (mirrors the
            # desire_rtype concept used by other channels).
            is_voice_input = bool(json_data.get('is_voice', False))

            # Fast path for /cancel: bypass the session queue and SSE setup.
            # Web frontend (stream=true) only listens to SSE, so we return an
            # inline_reply payload to be rendered synchronously.
            stripped_prompt = (prompt or "").strip().lower()
            if stripped_prompt == "/cancel":
                from agent.protocol import get_cancel_registry
                scoped_session_id = agent_bridge._cancel_key(
                    resolved_agent_id,
                    session_id,
                    agent_bridge.agent_registry.default_agent_id,
                )
                cancelled = get_cancel_registry().cancel_session(scoped_session_id)
                lang = (json_data.get('lang') or 'zh').lower()
                msg_text = _cancel_reply_text(cancelled, lang)
                logger.info(
                    f"[WebChannel] /cancel fast-path: session={session_id}, cancelled={cancelled}, lang={lang}"
                )
                return json.dumps({
                    "status": "success",
                    "request_id": "",
                    "stream": False,
                    "inline_reply": msg_text,
                })

            # Explicit steering also bypasses the normal session queue. The
            # Web button sends ``steer: true`` with raw input; typed /steer
            # commands use the same endpoint and semantics as IM channels.
            steer_requested = bool(json_data.get("steer", False))
            is_steer_command = (
                re.match(r"^/steer(?:\s|$)", stripped_prompt) is not None
            )
            if steer_requested or is_steer_command:
                instruction = (
                    (prompt or "").strip()[len("/steer"):].strip()
                    if is_steer_command
                    else (prompt or "").strip()
                )
                result = agent_bridge.steer_session(
                    session_id, instruction, resolved_agent_id
                )
                lang = (json_data.get("lang") or "zh").lower()
                msg_text = _steer_reply_text(result.status, lang)
                logger.info(
                    f"[WebChannel] steer fast-path: session={session_id}, "
                    f"status={result.status.value}, lang={lang}"
                )
                return json.dumps({
                    "status": "success",
                    "request_id": "",
                    "stream": False,
                    "steered": result.accepted,
                    "inline_reply": msg_text,
                }, ensure_ascii=False)

            # Append file references to the prompt (same format as QQ channel)
            if attachments:
                file_refs = []
                for att in attachments:
                    ftype = att.get("file_type", "file")
                    fpath = att.get("file_path", "")
                    if not fpath:
                        continue
                    if ftype == "workspace_ref":
                        # Already lives in the workspace (dragged from the file panel
                        # or picked with @); reference it in place so the agent opens
                        # the original instead of an uploaded copy. Naming the kind
                        # tells the agent whether to `read` it or `ls` into it.
                        # Resolve relative to the session's working root (project
                        # dir when opened, else the workspace).
                        is_dir = os.path.isdir(
                            os.path.join(
                                _get_workspace_root(session_id, resolved_agent_id), fpath
                            )
                        )
                        label = (
                            i18n.t('工作空间目录', 'Workspace directory') if is_dir
                            else i18n.t('工作空间文件', 'Workspace file')
                        )
                        file_refs.append(f"[{label}: {fpath}]")
                    elif ftype == "image":
                        file_refs.append(f"[{i18n.t('图片', 'Image')}: {fpath}]")
                    elif ftype == "video":
                        file_refs.append(f"[{i18n.t('视频', 'Video')}: {fpath}]")
                    elif ftype == "directory":
                        file_refs.append(f"[{i18n.t('目录', 'Directory')}: {fpath}]")
                    else:
                        file_refs.append(f"[{i18n.t('文件', 'File')}: {fpath}]")
                if file_refs:
                    prompt = prompt + "\n" + "\n".join(file_refs)
                    logger.info(f"[WebChannel] Attached {len(file_refs)} file(s) to message")

            request_id = self._generate_request_id()
            with self._sse_streams_lock:
                self.request_to_session[request_id] = session_id
                self.request_to_agent[request_id] = resolved_agent_id
                if auth_context is not None:
                    self.request_owners[request_id] = (
                        auth_context.tenant_id, auth_context.user_id, resolved_agent_id, session_id,
                    )

            session_queue_key = self._session_queue_key(
                session_id, resolved_agent_id
            )
            if session_queue_key not in self.session_queues:
                self.session_queues[session_queue_key] = Queue()

            if use_sse:
                with self._sse_streams_lock:
                    self.sse_streams[request_id] = SSEStreamState()

            trigger_prefixs = conf().get("single_chat_prefix", [""])
            if check_prefix(prompt, trigger_prefixs) is None:
                if trigger_prefixs:
                    prompt = trigger_prefixs[0] + prompt
                    logger.debug(f"[WebChannel] Added prefix to message: {prompt}")

            msg = WebMessage(self._generate_msg_id(), prompt)
            msg.from_user_id = session_id

            context = self._compose_context(ContextType.TEXT, prompt, msg=msg, isgroup=False)

            if context is None:
                logger.warning(f"[WebChannel] Context is None for session {session_id}, message may be filtered")
                self._drop_sse_request(request_id)
                return json.dumps({"status": "error", "message": "Message was filtered"})

            context["session_id"] = session_id
            context["receiver"] = session_id
            context["request_id"] = request_id
            context["agent_id"] = resolved_agent_id
            # Addressing a teammate hands them the turn. The conversation still
            # belongs to `resolved_agent_id`, so this only changes who answers.
            # The composer already knows who it wrote; parsing the text is the
            # fallback for a mention typed by hand or replayed from history.
            roster = _session_roster(session_id, resolved_agent_id)
            addressed = (json_data.get("speaker_agent_id") or "").strip()
            if not addressed or not any(item["id"] == addressed for item in roster):
                addressed = _addressed_agent_id(typed_prompt, roster)
            if addressed and addressed != resolved_agent_id:
                if auth_context is not None:
                    _require_tenant_agent_binding(auth_context, addressed)
                    _require_private_owner(auth_context, addressed)
                    # Handing the turn to a teammate still runs that Agent, so it
                    # needs the same execution grant as a direct dispatch.
                    _require_agent_action(auth_context, addressed, "use", "agent.use")
                context["speaker_agent_id"] = addressed
            if is_voice_input:
                # Web channel runs its own TTS post-pipeline via
                # _maybe_dispatch_auto_tts; don't set desire_rtype here or
                # chat_channel would synthesize a duplicate VOICE reply.
                context["is_voice_input"] = True

            if use_sse:
                context["on_event"] = self._make_sse_callback(request_id)

            # In database identity mode the ambient identity (tenant/user) is
            # scoped by the enclosing _db_scope. The run is dispatched on a
            # separate thread where ContextVars don't carry, so snapshot the
            # identity onto the context here and let _identity_for rebuild it.
            context["runtime_identity"] = _web_runtime_identity_snapshot()

            threading.Thread(target=self.produce, args=(context,)).start()

            return json.dumps({
                "status": "success",
                "request_id": request_id,
                "stream": use_sse,
                # Lets the live bubble carry the right name and face while the
                # reply streams, before any of it has been persisted.
                "speaker": context.get("speaker_agent_id") or "",
            })

        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def _drop_sse_request(self, request_id: str):
        """Reclaim all state tied to an SSE request."""
        with self._sse_streams_lock:
            state = self.sse_streams.pop(request_id, None)
            self.request_to_session.pop(request_id, None)
            self.request_to_agent.pop(request_id, None)
            getattr(self, "request_owners", {}).pop(request_id, None)
        if state is not None:
            with state.condition:
                state.closed = True
                state.condition.notify_all()

    def _sweep_sse_streams(self, now: Optional[float] = None) -> int:
        """Finalize overdue tails and reclaim expired SSE replay logs."""
        now = time.time() if now is None else now
        with self._sse_streams_lock:
            states = list(self.sse_streams.items())

        overdue = []
        for request_id, state in states:
            with state.condition:
                if (
                    state.main_done
                    and not state.stream_complete
                    and state.main_done_at is not None
                    and now - state.main_done_at
                    >= self.SSE_POST_DONE_TAIL_SECONDS
                ):
                    overdue.append(request_id)
        for request_id in overdue:
            self._publish_sse_event(request_id, {"type": "stream_end"})

        with self._sse_streams_lock:
            states = list(self.sse_streams.items())
        stale = []
        for request_id, state in states:
            with state.condition:
                if state.stream_complete and state.completed_at is not None:
                    expired = (
                        now - state.completed_at
                        >= self.SSE_COMPLETED_TTL_SECONDS
                    )
                else:
                    expired = (
                        now - state.last_active
                        >= self.SSE_IDLE_TIMEOUT_SECONDS
                    )
            if expired:
                stale.append(request_id)

        for request_id in stale:
            self._drop_sse_request(request_id)
        return len(stale)

    def _start_sse_janitor(self):
        """Start a background thread that reclaims orphaned SSE logs.

        Completed logs remain replayable for a short grace period. Abandoned
        unfinished logs use the longer idle timeout.
        """
        if self._sse_janitor_started:
            return
        self._sse_janitor_started = True

        SWEEP_INTERVAL = 60

        def _sweep():
            while True:
                time.sleep(SWEEP_INTERVAL)
                try:
                    reclaimed = self._sweep_sse_streams()
                    if reclaimed:
                        logger.info(
                            f"[WebChannel] SSE janitor reclaimed {reclaimed} "
                            f"idle stream(s)"
                        )
                except Exception as e:
                    logger.warning(f"[WebChannel] SSE janitor error: {e}")

        t = threading.Thread(target=_sweep, name="sse-janitor", daemon=True)
        t.start()

    def stream_response(self, request_id: str, after_seq: int = 0):
        """
        SSE generator for a given request_id.
        Yields UTF-8 encoded bytes to avoid WSGI Latin-1 mangling.
        Each connection reads the request's event log using its own cursor.
        """
        with self._sse_streams_lock:
            state = self.sse_streams.get(request_id)
        if state is None:
            yield b"data: {\"type\": \"error\", \"message\": \"invalid request_id\"}\n\n"
            return
        try:
            cursor = max(0, int(after_seq))
        except (TypeError, ValueError):
            cursor = 0
        idle_timeout = 600  # 10 minutes without any real event
        deadline = time.time() + idle_timeout
        # A cancel only takes effect at the agent's next checkpoint, so the run
        # keeps emitting events (tool results, the partial reply) for a while
        # after the user presses Stop. Stay open for them, just not for the
        # full idle timeout.
        CANCEL_GRACE_SECONDS = 60
        cancelled = False

        try:
            while time.time() < deadline:
                resync_payload = None
                force_stream_end = False
                with state.condition:
                    now = time.time()
                    state.last_active = now
                    force_stream_end = (
                        state.main_done
                        and not state.stream_complete
                        and state.main_done_at is not None
                        and now - state.main_done_at
                        >= self.SSE_POST_DONE_TAIL_SECONDS
                    )
                    if state.events:
                        first_seq = state.events[0][0]["seq"]
                        latest_seq = state.events[-1][0]["seq"]
                        if cursor < first_seq - 1:
                            resync_payload = {
                                "type": "resync_required",
                                "reason": "event_cursor_expired",
                                "after_seq": cursor,
                                "first_available_seq": first_seq,
                            }
                        elif cursor > latest_seq:
                            resync_payload = {
                                "type": "resync_required",
                                "reason": "event_cursor_ahead",
                                "after_seq": cursor,
                                "latest_available_seq": latest_seq,
                            }
                    pending = [
                        event for event, _ in state.events
                        if event["seq"] > cursor
                    ]
                    complete = state.stream_complete
                    closed = state.closed
                    if (
                        resync_payload is None
                        and not pending and not complete and not closed
                    ):
                        state.condition.wait(timeout=1)

                if force_stream_end:
                    self._publish_sse_event(
                        request_id, {"type": "stream_end"}
                    )
                    continue

                if resync_payload is not None:
                    payload = json.dumps(resync_payload, ensure_ascii=False)
                    yield f"data: {payload}\n\n".encode("utf-8")
                    return

                if not pending:
                    if complete or closed:
                        break
                    yield b": keepalive\n\n"
                    continue

                for item in pending:
                    deadline = time.time() + (
                        CANCEL_GRACE_SECONDS if cancelled else idle_timeout
                    )
                    payload = json.dumps(item, ensure_ascii=False)
                    yield (
                        f"id: {item['seq']}\n"
                        f"data: {payload}\n\n"
                    ).encode("utf-8")
                    cursor = item["seq"]
                    if item.get("type") == "cancelled":
                        cancelled = True
                        deadline = time.time() + CANCEL_GRACE_SECONDS
                    if item.get("type") == "stream_end":
                        return
        except GeneratorExit:
            # The event log is deliberately retained for reconnection.
            raise

    def cancel_request(self):
        """
        Cancel an in-flight agent run.

        Body: {"request_id": "...", "session_id": "..."}
        Either field is sufficient; request_id is preferred when known.
        Always returns success even when nothing was running, so the
        client's UX is idempotent.

        The authorized ``(agent_id, session_id)`` pair, when the handler
        resolved one, arrives through the request-scoped authorized target
        (tasks 8.2/8.3) instead of a rewritten signature.
        """
        authorized_session = authorized_target().get("session")
        try:
            from agent.protocol import get_cancel_registry

            data = web.data()
            try:
                json_data = json.loads(data) if data else {}
            except Exception:
                json_data = {}

            request_id = (json_data.get("request_id") or "").strip()
            session_id = (json_data.get("session_id") or "").strip()
            lang = (json_data.get("lang") or "zh").lower()
            from bridge.bridge import Bridge
            from agent.routing import AgentUnavailableError
            agent_bridge = Bridge().get_agent_bridge()
            agent_id = self.request_to_agent.get(request_id)
            if authorized_session is not None:
                agent_id, session_id = authorized_session
            if not agent_id:
                try:
                    agent_id = agent_bridge.agent_router.resolve(
                        explicit_agent_id=json_data.get("agent_id"),
                    )
                except AgentUnavailableError:
                    # Session pinned to a since-deleted Agent; nothing in flight
                    # for it to cancel. Report success with a zero count rather
                    # than raising on every cancel attempt.
                    return json.dumps({"status": "success", "cancelled": 0})

            registry = get_cancel_registry()
            cancelled = 0

            if request_id:
                if registry.cancel_request(request_id):
                    cancelled = 1

            if cancelled == 0 and session_id:
                scoped_session_id = agent_bridge._cancel_key(
                    agent_id,
                    session_id,
                    agent_bridge.agent_registry.default_agent_id,
                )
                cancelled = registry.cancel_session(scoped_session_id)

            if request_id and request_id in self.sse_streams:
                self._publish_sse_event(request_id, {
                    "type": "cancelled",
                    "content": "🛑 Cancelled" if lang.startswith("en") else "🛑 已中止",
                    "request_id": request_id,
                    "timestamp": time.time(),
                })

            logger.info(
                f"[WebChannel] cancel request: request_id={request_id!r}, "
                f"session_id={session_id!r}, cancelled={cancelled}"
            )
            return json.dumps({
                "status": "success",
                "cancelled": cancelled,
            })

        except Exception as e:
            logger.error(f"[WebChannel] cancel_request error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def poll_response(self):
        """
        Poll for responses using the session_id.

        The authorized ``(agent_id, session_id)`` pair, when the handler
        resolved one, arrives through the request-scoped authorized target
        (tasks 8.2/8.3) instead of a rewritten signature.
        """
        authorized_session = authorized_target().get("session")
        try:
            data = web.data()
            json_data = json.loads(data)
            session_id = json_data.get('session_id')
            from bridge.bridge import Bridge
            from agent.routing import AgentUnavailableError
            agent_bridge = Bridge().get_agent_bridge()
            try:
                if authorized_session is not None:
                    agent_id, session_id = authorized_session
                else:
                    agent_id = agent_bridge.agent_router.resolve(
                        explicit_agent_id=json_data.get("agent_id"),
                    )
            except AgentUnavailableError:
                # The session is pinned to an Agent that has since been deleted
                # or disabled (a stale client selection). Polling is read-only,
                # so there is nothing to answer - report no content instead of
                # raising every tick, which otherwise floods the log.
                return json.dumps({
                    "status": "success",
                    "has_content": False,
                    "agent_unavailable": True,
                })
            session_queue_key = self._session_queue_key(session_id, agent_id)

            if not session_id or session_queue_key not in self.session_queues:
                return json.dumps({"status": "error", "message": "Invalid session ID"})

            # 尝试从队列获取响应，不等待
            try:
                # 使用peek而不是get，这样如果前端没有成功处理，下次还能获取到
                response = self.session_queues[session_queue_key].get(block=False)

                # 返回响应，包含请求ID以区分不同请求
                return json.dumps({
                    "status": "success",
                    "has_content": True,
                    "content": response["content"],
                    "request_id": response["request_id"],
                    "timestamp": response["timestamp"]
                })

            except Empty:
                # 没有新响应
                return json.dumps({"status": "success", "has_content": False})

        except Exception as e:
            logger.error(f"Error polling response: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def chat_page(self):
        """Serve the chat HTML page."""
        file_path = os.path.join(os.path.dirname(__file__), 'chat.html')  # 使用绝对路径
        with open(file_path, 'r', encoding='utf-8') as f:
            html = f.read()
        # Inject the backend-resolved default language so the console can use
        # it on first load (when the user has no saved cow_lang preference).
        html = html.replace("{{COW_DEFAULT_LANG}}", i18n.get_language())
        return html.replace("{{COW_NAVIGATION_MODE}}", _web_navigation_mode())

    def startup(self):
        configured_host = conf().get("web_host", "")
        # Database identity is always required; default to loopback unless
        # the operator explicitly publishes via web_host.
        host = configured_host or "127.0.0.1"
        # The desktop app passes its chosen port via COW_WEB_PORT so its backend
        # never collides with a source-run web console (default 9899). This makes
        # the port a single source of truth owned by the Electron shell.
        port = int(os.environ.get("COW_WEB_PORT") or conf().get("web_port", 9899))
        is_public_bind = host in ("0.0.0.0", "::")

        self._cleanup_stale_voice_recordings()

        def _log_startup_banner():
            """Announce the console. Only called once the socket is actually
            bound — printing it up front made a failed bind look like a
            successful startup in the logs."""
            # Print available channel types (ordered by language: prioritize
            # locally-popular channels for the current UI language)
            logger.info(
                "[WebChannel] Available channels (edit `channel_type` in config.json to switch, separate multiple with commas):")
            zh_channels = [
                ("web", "Web"),
                ("terminal", "Terminal"),
                ("weixin", "WeChat"),
                ("feishu", "Feishu"),
                ("dingtalk", "DingTalk"),
                ("wecom_bot", "WeCom Bot"),
                ("wechatcom_app", "WeCom App"),
                ("wechat_kf", "WeChat Customer Service"),
                ("wechatmp", "WeChat Official Account"),
                ("wechatmp_service", "WeChat Official Account (Service)"),
                ("telegram", "Telegram"),
                ("slack", "Slack"),
                ("discord", "Discord"),
            ]
            en_channels = [
                ("web", "Web"),
                ("terminal", "Terminal"),
                ("telegram", "Telegram"),
                ("slack", "Slack"),
                ("discord", "Discord"),
                ("weixin", "WeChat"),
                ("feishu", "Feishu"),
                ("dingtalk", "DingTalk"),
                ("wecom_bot", "WeCom Bot"),
                ("wechatcom_app", "WeCom App"),
                ("wechat_kf", "WeChat Customer Service"),
                ("wechatmp", "WeChat Official Account"),
                ("wechatmp_service", "WeChat Official Account (Service)"),
            ]
            channels = en_channels if i18n.get_language() == "en" else zh_channels
            name_width = max(len(name) for name, _ in channels)
            for idx, (name, label) in enumerate(channels, 1):
                logger.info(f"[WebChannel]  {idx:>2}. {name:<{name_width}} - {label}")
            logger.info("[WebChannel] ✅ Web console is running")
            logger.info(f"[WebChannel] 🌐 Local access: http://localhost:{port}")
            if is_public_bind:
                logger.info(f"[WebChannel] 🌍 Server access: http://YOUR_IP:{port} (replace YOUR_IP with your server IP)")
                logger.info("[WebChannel] 🔒 Database identity is required for all console access")
            else:
                logger.info(f"[WebChannel] 🔒 Listening on {host} only (local access). For public access, set web_host to 0.0.0.0 (database login still required)")

            # In desktop mode the Electron shell renders the UI, so don't pop a
            # browser window (also avoids issues when running detached/headless).
            if os.environ.get("COW_DESKTOP") != "1":
                try:
                    import webbrowser
                    webbrowser.open(f"http://localhost:{port}")
                    logger.debug(f"[WebChannel] Opened browser at http://localhost:{port}")
                except Exception as e:
                    logger.debug(f"[WebChannel] Could not open browser: {e}")

        # Ensure the static dir exists. In a packaged build it ships read-only
        # inside the bundle, so swallow errors instead of failing startup.
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        if not os.path.exists(static_dir):
            try:
                os.makedirs(static_dir)
                logger.debug(f"[WebChannel] Created static directory: {static_dir}")
            except OSError as e:
                logger.debug(f"[WebChannel] Skipped creating static dir (read-only bundle?): {e}")

        urls = _WEB_URLS
        app = build_web_app()

        # 完全禁用web.py的HTTP日志输出
        web.httpserver.LogMiddleware.log = lambda self, status, environ: None

        # 配置web.py的日志级别为ERROR
        logging.getLogger("web").setLevel(logging.ERROR)
        logging.getLogger("web.httpserver").setLevel(logging.ERROR)

        # Build WSGI app with middleware (same as runsimple but without print)
        func = web.httpserver.StaticMiddleware(app.wsgifunc())
        func = web.httpserver.LogMiddleware(func)
        server = web.httpserver.WSGIServer((host, port), func)
        server.daemon_threads = True
        # Default request_queue_size(5) / timeout(10s) / numthreads(10) are
        # too small: when SSE streams occupy many threads, the backlog fills
        # and new connections get refused (ERR_CONNECTION_ABORTED).
        server.request_queue_size = 128
        server.timeout = 300
        server.requests.min = 20
        server.requests.max = 80
        # Allow large attachments (screenshots, PDFs, short videos). cheroot's
        # default is unlimited (0), but pin an explicit, generous cap so an
        # oversized body fails with a clean 413 instead of a connection reset
        # that surfaces in the client as an opaque "Failed to fetch".
        try:
            server.max_request_body_size = 512 * 1024 * 1024  # 512 MB
        except Exception:
            pass
        self._http_server = server
        # Reclaim orphaned SSE logs so disconnected clients don't leak memory.
        self._start_sse_janitor()
        # prepare() binds the socket, serve() runs the accept loop. Splitting
        # start() into the two lets us report a bind failure with the port in
        # hand, and keeps the "console is running" banner honest: it now only
        # prints once we really own the port.
        try:
            server.prepare()
        except OSError as e:
            _log_bind_failure(host, port, e)
            raise
        SERVING.set()
        _log_startup_banner()
        try:
            server.serve()
        except (KeyboardInterrupt, SystemExit):
            server.stop()

    def stop(self):
        if self._http_server:
            try:
                self._http_server.stop()
                logger.info("[WebChannel] HTTP server stopped")
            except Exception as e:
                logger.warning(f"[WebChannel] Error stopping HTTP server: {e}")
            self._http_server = None


class RootHandler:
    def GET(self):
        raise web.seeother('/chat')


class HealthHandler:
    # Unauthenticated liveness probe. The desktop shell polls this to know the
    # backend is up; it must never require a session. Returns no sensitive data.
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        return json.dumps({"status": "ok"})


class McpOAuthCallbackHandler:
    """OAuth redirect target for MCP servers requiring authorization.

    The browser lands here after the user authorizes a remote MCP server.
    We exchange the authorization code for tokens and bring the server
    online. Unauthenticated by design: the OAuth `state` param is the
    single-use secret that binds this request to a pending authorization.
    """

    def GET(self):
        web.header('Content-Type', 'text/html; charset=utf-8')
        params = web.input(code="", state="", error="", error_description="")

        def _page(title: str, message: str) -> str:
            return (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title></head>"
                "<body style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
                "max-width:520px;margin:64px auto;padding:0 20px;text-align:center;color:#1f2328'>"
                f"<h2>{title}</h2><p style='color:#57606a'>{message}</p></body></html>"
            )

        if params.error:
            logger.warning(f"[MCP-OAuth] callback error: {params.error} {params.error_description}")
            return _page("授权失败", f"{params.error}: {params.error_description or ''}")

        if not params.code or not params.state:
            return _page("参数缺失", "回调缺少 code 或 state 参数。")

        try:
            from agent.tools.mcp.mcp_oauth import pop_pending
            from agent.tools.mcp.mcp_client import notify_server_authorized
        except Exception as e:
            logger.warning(f"[MCP-OAuth] callback import failed: {e}")
            return _page("内部错误", "OAuth 模块不可用。")

        handler = pop_pending(params.state)
        if handler is None:
            return _page("会话已过期", "授权请求不存在或已过期，请重新触发授权。")

        try:
            ok = handler.finish_authorization(params.code)
        except Exception as e:
            logger.warning(f"[MCP-OAuth] token exchange crashed: {e}")
            ok = False

        if not ok:
            return _page("授权失败", "换取令牌失败，请重试。")

        notify_server_authorized(handler.server_name)
        logger.info(f"[MCP-OAuth] Server '{handler.server_name}' authorized via web callback")
        return _page(
            "授权成功",
            f"MCP 服务 “{handler.server_name}” 已授权，可以返回聊天继续使用了。",
        )


def _is_database_identity() -> bool:
    """Database is the only identity mode."""
    return True


def _permission_mode_projection() -> dict:
    """The global default-permission setting as the console should render it.

    A legacy install sets its own default and may edit it. In database mode the
    session permission mode is not what gates execution — the caller's role
    grants are — so the setting is shown read-only as an explanation, never as a
    knob that changes what a tenant user may run.
    """
    projection = {
        "agent_permission_mode": permission_global_mode(),
        "permission_modes": list(PERMISSION_MODES),
    }
    if _is_database_identity():
        projection["permission_mode_source"] = "role"
        projection["permission_mode_editable"] = False
    else:
        projection["permission_mode_source"] = "config"
        projection["permission_mode_editable"] = True
    return projection


# Console navigation presentation switch. Allowed values: "classic" | "split".
_NAVIGATION_MODES = ("classic", "split")


def _web_navigation_mode() -> str:
    """Return a validated ``web_navigation_mode`` value (defaults to "classic").

    This is a layout-only presentation switch. It does NOT change the identity
    mode, authentication, authorization, or any consumer open/closed state. An
    invalid or absent config value safely falls back to "classic".
    """
    raw = str(conf().get("web_navigation_mode", "classic") or "classic").strip().lower()
    return raw if raw in _NAVIGATION_MODES else "classic"


def _unavailable() -> str:
    """Stable 503 payload for a consumer closed in database identity mode."""
    web.status = 503
    web.header("Content-Type", "application/json; charset=utf-8")
    return json.dumps(
        {"status": "error", "message": "unavailable in database identity mode",
         "code": "database_unavailable"},
        ensure_ascii=False,
    )


def _guard_not_database() -> None:
    """Keep consumers without a verified tenant boundary closed in database mode.

    Chat transport, file upload/serve/preview and voice have dedicated
    identity/permission boundaries (task 2.4, open-database-runtime), and
    knowledge read/write now carries its own tenant scope + permission gate
    (``open-tenant-knowledge-console``). The consumer still using this gate is
    the host project browser, which remains closed server-side until its own
    slice lands.
    """
    if _is_database_identity():
        raise web.HTTPError("503 Service Unavailable",
                            {"Content-Type": "application/json; charset=utf-8"},
                            _unavailable())


class AuthCheckHandler:
    def GET(self):
        return DbAuthCheckHandler().GET()


class AuthLoginHandler:
    def POST(self):
        return DbAuthLoginHandler().POST()


class AuthLogoutHandler:
    def POST(self):
        return DbAuthLogoutHandler().POST()


class MessageHandler:
    # Chat is now a request-scoped *tenant* consumer in database mode: it runs
    # inside _db_scope (which resolves the DB session + tenant and applies the
    # ambient identity) rather than being blocked, so the runtime path works for
    # a logged-in DB user. The derived identity is snapshotted in post_message
    # for the worker thread.
    def POST(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        with _db_scope() as ctx:
            _require_chat_csrf()
            body = _chat_body()
            if not isinstance(body.get("message", ""), str):
                _chat_error("message must be text", "400 Bad Request", "bad_request")
            session_id = body.get("session_id") or ("session_" + uuid.uuid4().hex)
            # /cancel and /steer are dispatched by post_message's fast path;
            # ownership must be verified before reaching either one.
            command = (body.get("message") or "").strip().lower()
            creating = not (command == "/cancel" or body.get("steer")
                            or re.match(r"^/steer(?:\s|$)", command))
            agent_id = _authorize_chat_session(
                ctx, session_id, _request_agent_id(body), create=creating,
            )
            with authorized_target_scope(
                auth_context=ctx, session=(agent_id, session_id),
            ):
                return WebChannel().post_message()


class UploadHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        with _db_scope() as ctx:
            # Database mode: an upload writes into a tenant-bound agent's upload
            # dir, so the target agent must be bound to the caller's tenant and
            # execution-authorized (attachments belong to the chat workflow).
            _require_chat_csrf()
            params = _raw_web_input()
            agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
            _require_private_owner(ctx, agent_id)
            _require_agent_action(ctx, agent_id, "use", "agent.use")
            with authorized_target_scope(agent_id=agent_id):
                return WebChannel().upload_file()


class VoiceAsrHandler:
    """Receive a mic recording, persist it under uploads/ and run ASR.
    Returns {status, text, audio_url} so the UI can render a playback bubble."""
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')

        saved_path = None
        try:
            params = _raw_web_input()
            with _db_scope() as ctx:
                # Mic recording lands in a tenant-bound agent's upload dir;
                # voice input is part of the chat flow.
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_private_owner(ctx, agent_id)
                _require_agent_action(ctx, agent_id, "use", "agent.use")
                file_obj = params.get("file")
                if file_obj is None:
                    return json.dumps({"status": "error", "message": "no audio file"})

                filename = getattr(file_obj, "filename", "") or "recording.webm"
                ext = os.path.splitext(filename)[1].lower() or ".webm"
                if ext not in (".webm", ".ogg", ".opus", ".mp4", ".m4a", ".mp3", ".wav"):
                    ext = ".webm"

                upload_dir = _get_upload_dir(agent_id)
                os.makedirs(upload_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                saved_name = f"voice_input_{ts}_{random.randint(0, 9999)}{ext}"
                saved_path = os.path.join(upload_dir, saved_name)
                with open(saved_path, "wb") as f:
                    f.write(file_obj.file.read() if hasattr(file_obj, "file") else file_obj.value)

                suffix = f"?agent_id={agent_id}" if agent_id else ""
                audio_url = f"/uploads/{saved_name}{suffix}"

                from bridge.bridge import Bridge
                reply = Bridge().fetch_voice_to_text(saved_path)
                if reply is None:
                    return json.dumps({
                        "status": "error",
                        "message": "ASR returned no reply",
                        "audio_url": audio_url,
                    })

                from bridge.reply import ReplyType
                if reply.type == ReplyType.TEXT:
                    return json.dumps({
                        "status": "success",
                        "text": reply.content or "",
                        "audio_url": audio_url,
                    })
                return json.dumps({
                    "status": "error",
                    "message": reply.content or "ASR failed",
                    "audio_url": audio_url,
                })
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception(f"[VoiceAsrHandler] failed: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class VoiceTtsHandler:
    """On-demand TTS for the in-chat "read aloud" button. Returns the
    audio URL and (when session_id is given) persists it onto the message."""
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            data = json.loads(web.data() or b"{}")
            text = (data.get("text") or "").strip()
            session_id = (data.get("session_id") or "").strip()
            if not text:
                return json.dumps({"status": "error", "message": "empty text"})
            with _db_scope() as ctx:
                if ctx is not None:
                    # Database mode: TTS output attaches to a tenant-bound
                    # agent's session; the caller must be chat-authorized for it.
                    agent_id = _require_tenant_agent_binding(ctx, data.get("agent_id"))
                    _require_private_owner(ctx, agent_id)
                    _require_agent_action(ctx, agent_id, "use", "agent.use")
                else:
                    agent_id = data.get("agent_id")
                # `@singleton` makes WebChannel a factory function — go via instance.
                channel = WebChannel()
                if not channel._tts_provider_ready():
                    return json.dumps({"status": "error", "message": "tts not configured"})

                from bridge.bridge import Bridge
                reply = Bridge().fetch_text_to_voice(text)
                if reply is None or reply.type != ReplyType.VOICE or not reply.content:
                    msg = getattr(reply, "content", "") or "tts failed"
                    return json.dumps({"status": "error", "message": str(msg)})

                url = channel._publish_tts_audio(reply.content, agent_id)
                if not url:
                    return json.dumps({"status": "error", "message": "publish failed"})

                if session_id:
                    try:
                        from agent.memory import get_conversation_store
                        from agent.registry import get_agent_registry
                        profile = get_agent_registry().get(agent_id)
                        get_conversation_store(profile.workspace).attach_extras_to_last_assistant(
                            session_id, {"audio": {"url": url, "kind": "tts"}},
                        )
                    except Exception as e:
                        logger.debug(f"[VoiceTtsHandler] persist skipped: {e}")

                return json.dumps({"status": "success", "audio_url": url})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception(f"[VoiceTtsHandler] failed: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class UploadsHandler:
    def GET(self, file_name):
        # The tenant comes from the addressed Agent, not a header: the console
        # loads this as an <img>/<audio> subresource, which cannot send one.
        with _uploads_identity_scope() as (ctx, requested_agent_id):
            try:
                agent_id = _require_tenant_agent_binding(ctx, requested_agent_id)
                _require_private_owner(ctx, agent_id)
                _require_agent_action(ctx, agent_id, "read", "agent.read")
                upload_dir = _get_upload_dir(agent_id)
                full_path = os.path.normpath(os.path.join(upload_dir, file_name))
                if not os.path.abspath(full_path).startswith(os.path.abspath(upload_dir)):
                    raise web.notfound()
                if not os.path.isfile(full_path):
                    raise web.notfound()
                content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
                web.header('Content-Type', content_type)
                web.header('Cache-Control', 'public, max-age=86400')
                with open(full_path, 'rb') as f:
                    return f.read()
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Error serving upload: {e}")
                raise web.notfound()


def _db_file_root_owners(ctx) -> list:
    """``[(realpath, agent_id_or_None)]`` roots a caller may serve files from.

    Platform and tenant-shared roots carry ``None``; a bound Agent's workspace
    carries its ``agent_id`` so callers can apply the private/shared ownership
    rule to the root a path actually resolves into.
    """
    from auth.service import get_identity_service
    svc = get_identity_service()
    roots = []
    if getattr(ctx, "is_platform_admin", False):
        roots.append((os.path.realpath(_platform_file_root()), None))
    shared = svc.tenant_shared_root(ctx.tenant_id) if getattr(ctx, "tenant_id", None) else None
    if shared:
        roots.append((os.path.realpath(shared), None))
    from agent.registry import get_agent_registry
    registry = get_agent_registry()
    if getattr(ctx, "tenant_id", None):
        for agent_id in svc.tenant_agent_ids(ctx.tenant_id):
            try:
                profile = registry.get(agent_id)
                roots.append((os.path.realpath(profile.workspace), agent_id))
            except (KeyError, ValueError):
                continue
    return roots


def _db_file_serve_roots(ctx) -> list:
    """Roots a database-mode caller may serve files from.

    Flat projection of :func:`_db_file_root_owners`, kept as the seam existing
    callers and tests patch.
    """
    return [root for root, _ in _db_file_root_owners(ctx)]


def _db_path_owner(real_path: str, roots: list) -> tuple:
    """Which of ``roots`` owns ``real_path``: ``(kind, agent_id)``.

    ``kind`` is ``platform``, ``shared``, ``agent``, ``ambiguous`` or ``none``.
    The **most specific** (longest) matching root wins, so an Agent workspace
    nested inside the tenant shared root keeps its identity instead of being
    swallowed by the shared root.

    Two *distinct* Agent workspaces of equal specificity make the ownership
    ambiguous and the caller must fail closed. An Agent workspace that literally
    equals a shared root is not ambiguous: the directory is the tenant's shared
    root, whose contents are shared by definition.
    """
    real_path = os.path.realpath(real_path)
    platform_root = os.path.realpath(_platform_file_root())
    try:
        if os.path.commonpath([real_path, platform_root]) == platform_root:
            return "platform", None
    except ValueError:
        pass

    matches = []
    for root, agent_id in roots:
        root = os.path.realpath(root)
        if root == platform_root:
            continue
        try:
            if os.path.commonpath([real_path, root]) == root:
                matches.append((len(root), agent_id))
        except ValueError:
            continue
    if not matches:
        return "none", None

    longest = max(length for length, _ in matches)
    top = {agent_id for length, agent_id in matches if length == longest}
    agent_ids = {agent_id for agent_id in top if agent_id}
    if len(agent_ids) > 1:
        return "ambiguous", None
    if agent_ids:
        return "agent", next(iter(agent_ids))
    return "shared", None


def _owner_of_db_path(ctx, real_path: str) -> tuple:
    """Single-resource :func:`_db_path_owner` against the caller's roots."""
    return _db_path_owner(real_path, _db_file_root_owners(ctx))


def _db_path_visible(ctx, real_path: str, roots: list = None) -> bool:
    """False when ``real_path`` is ambiguous or another member's private Agent.

    The single ownership rule for the file surface: apply it to the path that was
    actually addressed, never to the Agent the request merely *declared*.
    ``roots`` lets a caller listing many entries resolve the tenant's roots once.
    """
    if roots is None:
        roots = _db_file_root_owners(ctx)
    kind, agent_id = _db_path_owner(real_path, roots)
    if kind == "ambiguous":
        return False
    if kind == "agent" and _db_path_owner_forbidden(ctx, agent_id):
        return False
    return True


def _authorize_db_file_path(ctx, real_path: str) -> tuple:
    """Authorize ``real_path`` against database file roots.

    Returns ``(allowed, via)`` where ``via`` is ``platform``, ``tenant``,
    ``forbidden``, or ``not_found``. Missing tenant context raises 403.
    Platform-root reads by a platform admin are audited as ``platform.file.read``.

    Tenant containment alone is not authority: the path's *owning Agent* must
    also pass the private-owner rule, so a member cannot name a shared Agent and
    then address another member's private Agent workspace (absolute or nested).
    """
    if not getattr(ctx, "tenant_id", None):
        raise web.HTTPError("403 Forbidden")

    from auth.service import get_identity_service

    svc = get_identity_service()
    real_path = os.path.realpath(real_path)
    platform_root = os.path.realpath(_platform_file_root())
    try:
        under_platform = os.path.commonpath([real_path, platform_root]) == platform_root
    except ValueError:
        under_platform = False

    if under_platform:
        if not getattr(ctx, "is_platform_admin", False):
            return False, "forbidden"
        try:
            svc.record_audit(
                action="platform.file.read",
                target=real_path,
                actor_user_id=getattr(ctx, "user_id", None),
                actor_username=getattr(ctx, "username", None),
                tenant_id=ctx.tenant_id,
            )
        except Exception as e:  # pragma: no cover - audit is best effort
            logger.warning(f"[WebChannel] platform.file.read audit unavailable: {e}")
        return True, "platform"

    contained = False
    for root in _db_file_serve_roots(ctx):
        root = os.path.realpath(root)
        if root == platform_root:
            continue
        try:
            if os.path.commonpath([real_path, root]) == root:
                contained = True
                break
        except ValueError:
            continue
    if not contained:
        return False, "not_found"

    kind, agent_id = _owner_of_db_path(ctx, real_path)
    if kind == "ambiguous":
        return False, "not_found"
    if kind == "agent" and _db_path_owner_forbidden(ctx, agent_id):
        return False, "forbidden"
    return True, "tenant"


class FileServeHandler:
    def GET(self):
        with _file_identity_scope() as ctx:
            try:
                params = web.input(path="", agent_id="")
                file_path = params.path
                if not file_path or not os.path.isabs(file_path):
                    raise web.notfound()
                # Resolve symlinks and confine access to tenant/platform roots;
                # never fall back to operator home or the whole filesystem.
                file_path = os.path.realpath(file_path)
                if params.agent_id:
                    agent_id = _require_tenant_agent_binding(ctx, params.agent_id)
                    _require_private_owner(ctx, agent_id)
                    _require_agent_action(ctx, agent_id, "read", "agent.read")
                    # A declared Agent is a cross-check, never the authority:
                    # the path's own owner is what authorizes the read, so a
                    # mismatch (shared Agent claimed for a private Agent's file)
                    # is refused rather than trusted.
                    owner_kind, owner_agent = _owner_of_db_path(ctx, file_path)
                    if owner_kind == "agent" and owner_agent != agent_id:
                        raise web.notfound()
                allowed, via = _authorize_db_file_path(ctx, file_path)
                if not allowed:
                    if via == "forbidden":
                        raise web.forbidden()
                    raise web.notfound()
                if not os.path.isfile(file_path):
                    raise web.notfound()
                content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                file_name = os.path.basename(file_path)
                from urllib.parse import quote
                web.header('Content-Type', content_type)
                web.header('Content-Disposition', f"inline; filename*=UTF-8''{quote(file_name)}")
                web.header('Cache-Control', 'public, max-age=3600')
                with open(file_path, 'rb') as f:
                    return f.read()
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Error serving file: {e}")
                raise web.notfound()


# Injected into previewed HTML so the iframe's scrollbars match the app chrome
# instead of falling back to the platform default (wide, opaque track).
# Placed at the top of <head> so a page that styles its own scrollbars still wins.
_PREVIEW_SCROLLBAR_CSS = (
    "<style>"
    "html{scrollbar-width:thin;scrollbar-color:rgba(128,128,128,.45) transparent}"
    "::-webkit-scrollbar{width:8px;height:8px}"
    "::-webkit-scrollbar-track{background:transparent}"
    "::-webkit-scrollbar-corner{background:transparent}"
    "::-webkit-scrollbar-thumb{background:rgba(128,128,128,.45);border-radius:4px;"
    "border:2px solid transparent;background-clip:padding-box}"
    "::-webkit-scrollbar-thumb:hover{background:rgba(128,128,128,.7);"
    "background-clip:padding-box}"
    "</style>"
)

_HEAD_OPEN_RE = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)
_HTML_OPEN_RE = re.compile(rb"<html\b[^>]*>", re.IGNORECASE)


def _inject_preview_chrome(raw: bytes) -> bytes:
    """Insert the scrollbar stylesheet into a previewed HTML document."""
    css = _PREVIEW_SCROLLBAR_CSS.encode("utf-8")
    for pattern in (_HEAD_OPEN_RE, _HTML_OPEN_RE):
        m = pattern.search(raw)
        if m:
            return raw[: m.end()] + css + raw[m.end():]
    return css + raw


class PreviewHandler:
    """
    Directory-mounted file server for the preview panel: /preview/<token>/<relpath>

    Unlike /api/file (single file, query param) this mounts the file's directory,
    so relative assets inside a generated HTML page resolve normally. The token is
    HMAC-signed, which is what authorizes the request - the sandboxed iframe can't
    send the auth cookie.
    """

    def GET(self, path_info):
        # Preview is capability-authorized: the URL carries an HMAC-signed
        # directory token because the sandboxed iframe (opaque origin) cannot
        # send the session cookie. This holds in database mode too, so the old
        # blanket 503 gate is gone (task 2.4, open-database-runtime).
        try:
            token, _, rel_path = (path_info or "").partition("/")
            if not token or not rel_path:
                raise web.notfound()

            from urllib.parse import unquote
            rel_path = unquote(rel_path)

            try:
                base_dir = _decode_dir_token(token)
            except ValueError:
                raise web.notfound()

            full_path = os.path.realpath(os.path.join(base_dir, rel_path))
            base_real = os.path.realpath(base_dir)
            # Confine to the mounted directory, then to the globally allowed roots.
            if os.path.commonpath([full_path, base_real]) != base_real:
                raise web.notfound()
            if not _is_path_allowed(full_path) or not os.path.isfile(full_path):
                raise web.notfound()
            # Ownership is re-derived at consumption: a token minted for a
            # private workspace is not a bearer grant to it. Public workspace
            # files keep working without identity (the token is the authority).
            if not _preview_consumer_may_read(full_path):
                raise web.notfound()

            content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
            web.header('Content-Type', content_type)
            web.header('Cache-Control', 'no-cache')
            web.header('X-Content-Type-Options', 'nosniff')
            is_html = content_type.startswith("text/html")
            if is_html:
                # Agent-generated pages are untrusted. The CSP sandbox forces an
                # opaque origin even when the page is opened as a top-level tab,
                # so it can't read the console's localStorage auth token; the
                # panel's iframe already applies the same flags.
                #
                # No frame-ancestors here: the desktop renderer is loaded from
                # file:// (or the Vite dev server), so 'self' would block its
                # preview iframe outright. The sandbox is what carries the
                # security guarantee; framing alone reveals nothing extra.
                web.header(
                    'Content-Security-Policy',
                    "sandbox allow-scripts allow-popups allow-forms allow-modals",
                )
            with open(full_path, 'rb') as f:
                data = f.read()
            return _inject_preview_chrome(data) if is_html else data
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Error serving preview: {e}")
            raise web.notfound()


class PollHandler:
    def POST(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        with _db_scope() as ctx:
            _require_chat_csrf()
            body = _chat_body()
            session_id = body.get("session_id")
            agent_id = _authorize_chat_session(ctx, session_id, _request_agent_id(body))
            with authorized_target_scope(session=(agent_id, session_id)):
                return WebChannel().poll_response()


class CancelHandler:
    def POST(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        with _db_scope() as ctx:
            _require_chat_csrf()
            body, channel = _chat_body(), WebChannel()
            request_id = body.get("request_id")
            if request_id:
                agent_id, session_id = _authorize_chat_request(ctx, channel, request_id)
                if (body.get("session_id") and body["session_id"] != session_id
                        or body.get("agent_id") and body["agent_id"] != agent_id):
                    _chat_error("request/session mismatch", "400 Bad Request", "bad_request")
            else:
                session_id = body.get("session_id")
                agent_id = _authorize_chat_session(ctx, session_id, _request_agent_id(body))
            with authorized_target_scope(session=(agent_id, session_id)):
                return channel.cancel_request()


class StreamHandler:
    def GET(self):
        # Native EventSource authenticates by cookie; its recorded request
        # supplies the tenant for fresh membership and personal-owner checks.
        params = web.input(request_id='', after_seq='')
        request_id = params.request_id
        if not request_id:
            raise web.badrequest()

        # Explicit query cursors are used by the frontend's manually-created
        # EventSource. Native EventSource reconnects remain compatible via the
        # standard Last-Event-ID request header.
        after_seq = _parse_sse_cursor(
            params.after_seq,
            web.ctx.env.get('HTTP_LAST_EVENT_ID', '0'),
        )

        channel = WebChannel()
        if _is_database_identity():
            with _stream_identity_scope(channel, request_id) as ctx:
                _authorize_chat_request(ctx, channel, request_id)

        web.header('Content-Type', 'text/event-stream; charset=utf-8')
        web.header('Cache-Control', 'no-cache')
        web.header('X-Accel-Buffering', 'no')
        if not _is_database_identity():
            web.header('Access-Control-Allow-Origin', '*')

        return channel.stream_response(request_id, after_seq)


class ChatHandler:
    def GET(self):
        # Content-Type must be explicit: behind a reverse proxy that sends
        # X-Content-Type-Options: nosniff, a missing type makes browsers
        # refuse to sniff and render the page as plain text source.
        web.header('Content-Type', 'text/html; charset=utf-8')
        web.header('Cache-Control', 'no-cache, no-store, must-revalidate')
        web.header('Pragma', 'no-cache')
        file_path = os.path.join(os.path.dirname(__file__), 'chat.html')
        with open(file_path, 'r', encoding='utf-8') as f:
            html = f.read()
        cache_bust = str(int(time.time()))
        # Every first-party asset the page pulls in, so an upgraded console is
        # never left running against a browser-cached copy of the old scripts.
        # identity-admin.js carries the tabbed editors: if it is missed here a
        # browser can keep rendering the previous (per-tab save) editor even
        # though the server already ships the unified-save one.
        assets = ['js/console.js', 'js/workspace.js', 'js/doc-editor.js',
                  'js/appearance.js', 'js/scenes/index.js',
                  'js/identity-admin.js', 'js/todos.js', 'js/fragments.js',
                  'css/console.css', 'css/appearance.css']
        # The per-domain i18n namespaces are discovered rather than listed: the
        # split (task 8.5) adds files over time, and a name missed here would
        # leave a browser rendering an upgraded console with a stale dictionary.
        try:
            i18n_dir = os.path.join(os.path.dirname(__file__), 'static', 'js', 'i18n')
            assets += [f'js/i18n/{name}' for name in sorted(os.listdir(i18n_dir))
                       if name.endswith('.js')]
        except OSError:
            pass
        # Fork fragments (task 8.8) are fetched at runtime by fragments.js, so
        # they need the same cache-busting as the scripts: a browser-cached copy
        # would keep mounting stale fork markup after an upgrade. Discovered
        # rather than listed so a new fragment needs no server edit.
        try:
            fragments_dir = os.path.join(os.path.dirname(__file__), 'static', 'fragments')
            assets += [f'fragments/{name}' for name in sorted(os.listdir(fragments_dir))
                       if name.endswith('.html')]
        except OSError:
            pass
        for asset in assets:
            html = html.replace(f'assets/{asset}', f'assets/{asset}?v={cache_bust}')
        # Inject the backend-resolved default language for first-load fallback.
        html = html.replace("{{COW_DEFAULT_LANG}}", i18n.get_language())
        # Inject the validated console navigation presentation switch (layout
        # only): "classic" (single sidebar) or "split" (area switch). Invalid
        # config falls back to "classic"; this never alters authorization or
        # consumer open/closed state.
        html = html.replace(
            "{{COW_NAVIGATION_MODE}}",
            _web_navigation_mode(),
        )
        return html


class ConfigHandler:

    _RECOMMENDED_MODELS = [
        const.DEEPSEEK_V4_FLASH, const.DEEPSEEK_V4_PRO,
        const.MINIMAX_M3, const.MINIMAX_M2_7_HIGHSPEED, const.MINIMAX_M2_7,
        # claude-opus-5 is the Claude default; claude-sonnet-5 / claude-fable-5 follow right after it.
        const.CLAUDE_OPUS_5, const.CLAUDE_SONNET_5, const.CLAUDE_FABLE_5_1, const.CLAUDE_FABLE_5, const.CLAUDE_4_8_OPUS, const.CLAUDE_4_7_OPUS, const.CLAUDE_4_6_SONNET, const.CLAUDE_4_6_OPUS,
        const.GEMINI_37_FLASH, const.GEMINI_36_FLASH, const.GEMINI_35_FLASH, const.GEMINI_31_FLASH_LITE_PRE, const.GEMINI_31_PRO_PRE, const.GEMINI_3_FLASH_PRE,
        const.GPT_56_LUNA, const.GPT_56_TERRA, const.GPT_56_SOL, const.GPT_55, const.GPT_54, const.GPT_54_MINI, const.GPT_54_NANO, const.GPT_5, const.GPT_41, const.GPT_4o,
        const.GLM_5_3_FLASH, const.GLM_5_3, const.GLM_5_2, const.GLM_5_1, const.GLM_5_TURBO, const.GLM_5, const.GLM_4_7,
        const.QWEN38_FLASH, const.QWEN38_MAX, const.QWEN37_PLUS, const.QWEN37_MAX, const.QWEN36_PLUS,
        const.DOUBAO_SEED_2_1_PRO, const.DOUBAO_SEED_2_1_TURBO, const.DOUBAO_SEED_2_CODE,
        const.KIMI_K3, const.KIMI_K2_7_CODE, const.KIMI_K2_7_CODE_HIGHSPEED, const.KIMI_K2_6, const.KIMI_K2_5, const.KIMI_K2,
        const.ERNIE_5_1, const.ERNIE_5, const.ERNIE_X1_1, const.ERNIE_45_TURBO_128K, const.ERNIE_45_TURBO_32K,
        const.MIMO_V2_5_PRO, const.MIMO_V2_5,
    ]

    # Generic placeholder hints surfaced in the web console. We deliberately
    # show the version-path tail (e.g. "/v1") so users are reminded to type
    # the full base URL. The form is intentionally vague (`...../v1`) so it
    # never looks like a real default a user might paste verbatim — and we
    # never auto-rewrite anything on the server side.
    _PLACEHOLDER_V1 = "https://...../v1"
    _PLACEHOLDER_QIANFAN = "https://...../v2"
    _PLACEHOLDER_ZHIPU = "https://...../api/paas/v4"
    _PLACEHOLDER_DOUBAO = "https://...../api/v3"
    _PLACEHOLDER_GEMINI = "https://....."

    PROVIDER_MODELS = OrderedDict([
        ("deepseek", {
            "label": "DeepSeek",
            "api_key_field": "deepseek_api_key",
            "api_base_key": "deepseek_api_base",
            "api_base_default": "https://api.deepseek.com/v1",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [const.DEEPSEEK_V4_FLASH, const.DEEPSEEK_V4_PRO],
        }),
        ("claudeAPI", {
            "label": "Claude",
            "api_key_field": "claude_api_key",
            "api_base_key": "claude_api_base",
            "api_base_default": "https://api.anthropic.com/v1",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [const.CLAUDE_OPUS_5, const.CLAUDE_SONNET_5, const.CLAUDE_FABLE_5_1, const.CLAUDE_FABLE_5, const.CLAUDE_4_8_OPUS, const.CLAUDE_4_7_OPUS, const.CLAUDE_4_6_SONNET, const.CLAUDE_4_6_OPUS],
        }),
        ("openai", {
            "label": "OpenAI",
            "api_key_field": "open_ai_api_key",
            "api_base_key": "open_ai_api_base",
            "api_base_default": "https://api.openai.com/v1",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [const.GPT_56_LUNA, const.GPT_56_TERRA, const.GPT_56_SOL, const.GPT_55, const.GPT_54, const.GPT_54_MINI, const.GPT_54_NANO, const.GPT_5, const.GPT_41, const.GPT_4o],
        }),
        ("gemini", {
            "label": "Gemini",
            "api_key_field": "gemini_api_key",
            "api_base_key": "gemini_api_base",
            "api_base_default": "https://generativelanguage.googleapis.com",
            "api_base_placeholder": _PLACEHOLDER_GEMINI,
            "models": [const.GEMINI_37_FLASH, const.GEMINI_36_FLASH, const.GEMINI_35_FLASH, const.GEMINI_31_FLASH_LITE_PRE, const.GEMINI_31_PRO_PRE, const.GEMINI_3_FLASH_PRE],
        }),
        ("minimax", {
            "label": "MiniMax",
            "api_key_field": "minimax_api_key",
            "api_base_key": None,
            "api_base_default": None,
            "api_base_placeholder": "",
            "models": [const.MINIMAX_M3, const.MINIMAX_M2_7, const.MINIMAX_M2_7_HIGHSPEED],
        }),
        ("zhipu", {
            "label": {"zh": "智谱AI", "en": "GLM"},
            "api_key_field": "zhipu_ai_api_key",
            "api_base_key": "zhipu_ai_api_base",
            "api_base_default": "https://open.bigmodel.cn/api/paas/v4",
            "api_base_placeholder": _PLACEHOLDER_ZHIPU,
            "models": [const.GLM_5_3_FLASH, const.GLM_5_3, const.GLM_5_2, const.GLM_5_1, const.GLM_5_TURBO, const.GLM_5, const.GLM_4_7],
        }),
        ("dashscope", {
            "label": {"zh": "通义千问", "en": "Qwen"},
            "api_key_field": "dashscope_api_key",
            "api_base_key": None,
            "api_base_default": None,
            "api_base_placeholder": "",
            "models": [const.QWEN38_FLASH, const.QWEN38_MAX, const.QWEN37_PLUS, const.QWEN37_MAX, const.QWEN36_PLUS],
        }),
        ("moonshot", {
            "label": "Kimi",
            "api_key_field": "moonshot_api_key",
            "api_base_key": "moonshot_base_url",
            "api_base_default": "https://api.moonshot.cn/v1",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [const.KIMI_K3, const.KIMI_K2_7_CODE, const.KIMI_K2_7_CODE_HIGHSPEED, const.KIMI_K2_6, const.KIMI_K2_5, const.KIMI_K2],
        }),
        ("doubao", {
            "label": {"zh": "豆包", "en": "Doubao"},
            "api_key_field": "ark_api_key",
            "api_base_key": "ark_base_url",
            "api_base_default": "https://ark.cn-beijing.volces.com/api/v3",
            "api_base_placeholder": _PLACEHOLDER_DOUBAO,
            "models": [const.DOUBAO_SEED_2_1_PRO, const.DOUBAO_SEED_2_1_TURBO, const.DOUBAO_SEED_2_PRO, const.DOUBAO_SEED_2_CODE],
        }),
        ("qianfan", {
            "label": {"zh": "百度千帆", "en": "ERNIE"},
            "api_key_field": "qianfan_api_key",
            "api_base_key": "qianfan_api_base",
            "api_base_default": "https://qianfan.baidubce.com/v2",
            "api_base_placeholder": _PLACEHOLDER_QIANFAN,
            "models": [const.ERNIE_5_1, const.ERNIE_5, const.ERNIE_X1_1, const.ERNIE_45_TURBO_128K, const.ERNIE_45_TURBO_32K],
        }),
        ("mimo", {
            "label": {"zh": "小米 MiMo", "en": "MiMo"},
            "api_key_field": "mimo_api_key",
            "api_base_key": "mimo_api_base",
            "api_base_default": "https://api.xiaomimimo.com/v1",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [const.MIMO_V2_5_PRO, const.MIMO_V2_5],
        }),
        ("linkai", {
            "label": "LinkAI",
            "api_key_field": "linkai_api_key",
            "api_base_key": None,
            "api_base_default": None,
            "api_base_placeholder": "",
            "models": _RECOMMENDED_MODELS,
        }),
        ("custom", {
            "label": {"zh": "自定义", "en": "Custom"},
            "api_key_field": "custom_api_key",
            "api_base_key": "custom_api_base",
            "api_base_default": "",
            "api_base_placeholder": _PLACEHOLDER_V1,
            "models": [],
        }),
    ])

    EDITABLE_KEYS = {
        "cow_lang",
        "model", "bot_type", "use_linkai",
        "open_ai_api_base", "deepseek_api_base", "qianfan_api_base", "claude_api_base", "gemini_api_base",
        "zhipu_ai_api_base", "moonshot_base_url", "ark_base_url", "custom_api_base", "mimo_api_base",
        "open_ai_api_key", "deepseek_api_key", "qianfan_api_key", "claude_api_key", "gemini_api_key",
        "zhipu_ai_api_key", "dashscope_api_key", "moonshot_api_key",
        "ark_api_key", "minimax_api_key", "linkai_api_key", "custom_api_key", "mimo_api_key",
        "custom_providers",
        "agent_max_context_tokens", "agent_max_context_turns", "agent_max_steps",
        "enable_thinking", "reasoning_effort", "reasoning_effort_by_model", "self_evolution_enabled",
        "agent_permission_mode",
    }

    # Switches the API exposes flat - one key, one control - while the config
    # file keeps a feature's settings together under one object.
    NESTED_BOOLS = {
        "subagent_enabled": ("subagent", "enabled"),
    }

    @staticmethod
    def _mask_key(value: str) -> str:
        """Mask the middle part of an API key for display."""
        if not value or len(value) <= 8:
            return value
        return value[:4] + "*" * (len(value) - 8) + value[-4:]

    def GET(self):
        _require_platform_console()
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.subagent import SubagentSettings
            from agent.evolution.config import get_evolution_config

            local_config = conf()
            use_agent = local_config.get("agent", True)
            title = _project_brand_name()

            api_bases = {}
            api_keys_masked = {}
            for pid, pinfo in self.PROVIDER_MODELS.items():
                base_key = pinfo.get("api_base_key")
                if base_key:
                    api_bases[base_key] = local_config.get(base_key, pinfo["api_base_default"])
                key_field = pinfo.get("api_key_field")
                if key_field and key_field not in api_keys_masked:
                    raw = local_config.get(key_field, "")
                    api_keys_masked[key_field] = self._mask_key(raw) if raw else ""

            providers = {}
            provider_model = local_config.get("model", "")
            for pid, p in self.PROVIDER_MODELS.items():
                reasoning_by_model = {
                    model: provider_reasoning_metadata(pid, model)
                    for model in p["models"]
                }
                providers[pid] = {
                    "label": p["label"],
                    "models": p["models"],
                    "api_base_key": p["api_base_key"],
                    "api_base_default": p["api_base_default"],
                    "api_base_placeholder": p.get("api_base_placeholder", ""),
                    "api_key_field": p.get("api_key_field"),
                    "reasoning": provider_reasoning_metadata(pid, provider_model),
                    "reasoning_by_model": reasoning_by_model,
                }

            # Expose user-defined custom providers as "custom:<id>" entries so
            # the legacy config page can display and select them. Credentials
            # are managed on the Models page, hence the null key/base fields.
            # Mirrors the Models page: when expanded entries exist, the bare
            # legacy "custom" entry is hidden — unless the flat single-provider
            # custom config is still active or filled in.
            try:
                from models.custom_provider import get_custom_providers
                custom_list = get_custom_providers()
                legacy_custom_in_use = ModelsHandler._legacy_custom_in_use(local_config)
                if custom_list and not legacy_custom_in_use:
                    providers.pop("custom", None)
                for cp in custom_list:
                    cid = f"custom:{cp.get('id')}"
                    cname = cp.get("name") or cp.get("id")
                    providers[cid] = {
                        "label": {"zh": cname, "en": cname},
                        "models": [cp["model"]] if cp.get("model") else [],
                        "api_base_key": None,
                        "api_base_default": None,
                        "api_base_placeholder": "",
                        "api_key_field": None,
                        "reasoning": provider_reasoning_metadata(cid, cp.get("model") or ""),
                        "reasoning_by_model": (
                            {cp["model"]: provider_reasoning_metadata(cid, cp["model"])}
                            if cp.get("model") else {}
                        ),
                    }
            except Exception as cp_err:
                logger.warning(f"[ConfigHandler] failed to expand custom providers: {cp_err}")

            result = {
                "status": "success",
                "use_agent": use_agent,
                "title": title,
                "model": local_config.get("model", ""),
                "bot_type": "openai" if local_config.get("bot_type") == "chatGPT" else local_config.get("bot_type", ""),
                "use_linkai": bool(local_config.get("use_linkai", False)),
                "channel_type": local_config.get("channel_type", ""),
                "agent_max_context_tokens": local_config.get("agent_max_context_tokens", 50000),
                "agent_max_context_turns": local_config.get("agent_max_context_turns", 20),
                "agent_max_steps": local_config.get("agent_max_steps", 20),
                "enable_thinking": bool(local_config.get("enable_thinking", False)),
                "reasoning_effort": local_config.get("reasoning_effort", "high"),
                "reasoning_effort_by_model": local_config.get("reasoning_effort_by_model", {}),
                # Read through the feature's own loader so the default it
                # applies to an absent setting is the one shown here.
                "self_evolution_enabled": get_evolution_config().enabled,
                "subagent_enabled": SubagentSettings.from_config().enabled,
                # Default permission mode for sessions that have not pinned one.
                # In database mode this is read-only (roles own execution).
                **_permission_mode_projection(),
                "api_bases": api_bases,
                "api_keys": api_keys_masked,
                "providers": providers,
            }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error getting config: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        _require_platform_console()
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            data = json.loads(web.data())
            updates = data.get("updates", {})
            if not updates:
                return json.dumps({"status": "error", "message": "no updates provided"})

            local_config = conf()
            applied = {}
            nested = {}
            for key, value in updates.items():
                if key in self.NESTED_BOOLS:
                    section, leaf = self.NESTED_BOOLS[key]
                    nested.setdefault(section, {})[leaf] = bool(value)
                    continue
                if key not in self.EDITABLE_KEYS:
                    continue
                if key == "agent_permission_mode" and _is_database_identity():
                    # database mode gates execution on role grants, not this
                    # setting; refuse to persist a change that has no effect.
                    continue
                if key in ("agent_max_context_tokens", "agent_max_context_turns", "agent_max_steps"):
                    value = int(value)
                if key in ("use_linkai", "enable_thinking", "self_evolution_enabled"):
                    value = bool(value)
                # Never persist an unknown mode: every later read would silently
                # fall back and the UI would show a setting that does nothing.
                if key == "agent_permission_mode":
                    value = permission_normalize_mode(value)
                # reasoning_effort_by_model is a dict that must be *merged* with
                # the persisted map, not replaced. A frontend submits only the
                # entries it changed (merged locally), so whole-key replacement
                # here would drop other models' saved efforts on a concurrent or
                # sequential save (or a second open settings page).
                if key == "reasoning_effort_by_model":
                    if not isinstance(value, dict):
                        # Reject malformed payloads explicitly instead of
                        # persisting a non-dict that the resolver would choke on.
                        return json.dumps({
                            "status": "error",
                            "message": "reasoning_effort_by_model must be a JSON object",
                        })
                    merged = dict(local_config.get("reasoning_effort_by_model") or {})
                    merged.update(value)
                    value = merged
                local_config[key] = value
                applied[key] = value

            if not applied and not nested:
                return json.dumps({"status": "error", "message": "no valid keys to update"})

            config_path = os.path.join(get_data_root(), "config.json")
            file_cfg = _read_config_file_for_write()
            file_cfg.update(applied)
            # Merged rather than assigned: the UI sends the one switch it owns,
            # and the rest of the section is the user's to keep.
            for section, values in nested.items():
                merged = dict(file_cfg.get(section) or {})
                merged.update(values)
                file_cfg[section] = merged
                local_config[section] = merged
                applied[section] = merged
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(file_cfg, f, indent=4, ensure_ascii=False)

            logger.info(f"[WebChannel] Config updated: {list(applied.keys())}")

            # Apply a language change immediately so backend logs, agent
            # replies and CLI output switch without a restart.
            if "cow_lang" in applied:
                try:
                    i18n.resolve_language(applied["cow_lang"])
                    logger.info(f"[WebChannel] Language switched to: {i18n.get_language()}")
                except Exception as lang_err:
                    logger.warning(f"[WebChannel] Failed to apply language: {lang_err}")

            # Reset Bridge so that bot routing reflects the new config.
            # Without this, Bridge keeps its cached bot instance (e.g. LinkAIBot)
            # even after the user switches bot_type / use_linkai / model in UI.
            bridge_routing_keys = {"bot_type", "use_linkai", "model"}
            if any(k in applied for k in bridge_routing_keys):
                try:
                    from bridge.bridge import Bridge
                    Bridge().reset_bot()
                    logger.info("[WebChannel] Bridge bot routing reset due to config change")
                except Exception as reset_err:
                    logger.warning(f"[WebChannel] Failed to reset bridge: {reset_err}")

            return json.dumps({"status": "success", "applied": applied}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return json.dumps({"status": "error", "message": str(e)})


# =========================================================================== #
# Branding API handlers
#
# The branding service owns the single source of truth for the instance brand.
# These handlers only translate HTTP <-> service calls and apply the auth /
# CSRF rules required by the spec. The service itself is a module import so it
# stays unit-testable.
# =========================================================================== #

def _branding_service() -> BrandingService:
    return create_branding_service()


def _project_brand_name() -> str:
    """Project the effective brand name for the legacy /config.title field.

    Returns the published brand name so the compatibility projection, browser
    title and welcome screen stay in sync. On failure it returns the default.
    This is a read-only projection: it must never become a write path.
    """
    try:
        svc = _branding_service()
        record = svc.get_published()
        return record.get("brand_name") or "容大AI"
    except Exception:
        pass
    return "容大AI"


def _branding_require_platform_admin() -> "RequestContext":
    """Resolve and authorize the context for a database-mode brand write.

    Returns the resolved ``RequestContext`` so callers can attribute the audit
    event to the acting platform admin. Raises 401/403 via ``_require_context``
    / ``_require_platform_admin`` for a missing session or a non-admin.
    """
    from channel.web.auth_handlers import _require_context
    from channel.web.admin_handlers import _require_platform_admin
    ctx = _require_context()
    _require_platform_admin(ctx)
    return ctx


def _branding_origin_ok() -> bool:
    """Verify the Origin / Referer is same-origin for a cookie-authorized write.

    The desktop client renders from a file:// origin and authenticates via the
    Authorization bearer header; it has no browser Origin, which is acceptable
    because bearer writes are not cookie-bound. Browsers send an Origin on
    POST; if present it must match the request host.
    """
    origin = web.ctx.env.get("HTTP_ORIGIN", "") or web.ctx.env.get("HTTP_REFERER", "") or ""
    if not origin:
        return False
    from urllib.parse import urlparse
    try:
        source = urlparse(origin)
        target = urlparse(web.ctx.env.get("wsgi.url_scheme", "http") + "://" + web.ctx.env.get("HTTP_HOST", ""))
        return (source.scheme in ("http", "https")
                and not source.username and not source.password
                and (source.scheme, source.hostname, source.port or (443 if source.scheme == "https" else 80))
                == (target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80)))
    except Exception:
        return False


def _branding_require_write():
    """Brand writes require a platform admin (database identity only)."""
    return _branding_require_platform_admin()



def _branding_record_audit(ctx, action: str, record: dict, *, reset: bool = False) -> None:
    """Record a sanitized brand audit event against ``identity.db`` (best-effort).

    Called only in database mode where ``ctx`` is the resolved platform admin;
    a non-None ``ctx`` is required. A failure to record must never roll back the
    committed brand, so it is logged and swallowed.
    """
    if ctx is None:
        return
    try:
        from channel.web.auth_handlers import _get_service
        changes = {
            "brand_name": record.get("brand_name"),
            "logo_description": record.get("logo_description"),
        }
        if reset:
            changes["reset_to_default"] = True
        _get_service()._audit.record(
            actor_user_id=ctx.user_id,
            actor_username=ctx.username,
            tenant_id=None,
            target_tenant_id=None,
            action=action,
            target="brand",
            redacted_changes=changes,
            result="success",
        )
    except Exception:
        logger.exception("[BrandingAudit] failed to record audit event")


def _branding_management_payload(service, record=None, ctx=None):
    allowed = ctx is not None
    payload = service.management_payload(allowed, "", record=record)
    payload["status"] = "success"
    return payload


def _branding_error_response(err: BrandingError) -> str:
    web.header('Content-Type', 'application/json; charset=utf-8')
    from http import HTTPStatus
    web.ctx.status = f"{err.http_status} {HTTPStatus(err.http_status).phrase}"
    web.header('Cache-Control', 'no-store')
    return json.dumps({
        "status": "error",
        "code": err.code,
        "message": err.message,
        "field": err.field,
    }, ensure_ascii=False)


class BrandingPublicHandler:
    """GET /api/branding/public - unauthenticated minimal brand read."""

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        try:
            payload = _branding_service().public_payload()
        except Exception as e:
            logger.exception(f"[BrandingPublicHandler] failed: {e}")
            # Fall back to the built-in default so login/nav never breaks.
            payload = {
                "enabled": False,
                "revision": 0,
                "brand_name": "容大AI",
                "logo_description": "工作台",
                "logo_url": "/assets/rongda-ai-mark.svg",
                "favicon_url": "/assets/favicon.ico",
            }
        return json.dumps(payload, ensure_ascii=False)


class BrandingManageHandler:
    """GET /api/branding and POST /api/branding (management read + save)."""

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        try:
            ctx = _branding_require_platform_admin()
            payload = _branding_management_payload(_branding_service(), ctx=ctx)
        except web.HTTPError:
            raise
        except BrandingError as e:
            return _branding_error_response(e)
        except Exception as e:
            logger.exception(f"[BrandingManageHandler] GET failed: {e}")
            return _branding_error_response(BrandingError("storage_error", "无法读取品牌设置", 500))
        return json.dumps(payload, ensure_ascii=False)

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        try:
            audit_ctx = _branding_require_write()
            params = _raw_web_input()

            def _scalar(value, default=""):
                # web.py merges the query string and form body, so a field
                # present in both arrives as a list. Collapse to a scalar.
                if isinstance(value, (list, tuple)):
                    return value[0] if value else default
                return value if value is not None else default

            expected = _scalar(params.get("expected_revision"))
            brand_name = _scalar(params.get("brand_name", ""))
            logo_description = _scalar(params.get("logo_description", ""))
            logo_action = _scalar(params.get("logo_action", ""), "keep")

            try:
                expected_revision = int(expected)
            except (TypeError, ValueError):
                raise BrandingError("missing_expected_revision", "缺少版本号", 400)

            file_obj = params.get("logo")
            logo_file = None
            if file_obj is not None:
                if isinstance(file_obj, (list, tuple)):
                    raise BrandingError("conflicting_logo_action", "只能上传一个 Logo", 400)
                filename = getattr(file_obj, "filename", "") or "logo.png"
                try:
                    data = _read_uploaded_file_bytes_limited(file_obj, 2 * 1024 * 1024 + 1)
                except ValueError as exc:
                    raise BrandingError("image_too_large", "图片不能超过 2 MiB", 413) from exc
                logo_file = (filename, data)

            service = _branding_service()
            operator = audit_ctx.username if audit_ctx is not None else "console"
            record = service.save(
                expected_revision=expected_revision,
                brand_name=brand_name,
                logo_description=logo_description,
                logo_action=logo_action,
                logo_file=logo_file,
                operator=operator,
            )
            _branding_record_audit(audit_ctx, "branding.update", record)
            payload = _branding_management_payload(service, record=record, ctx=audit_ctx)
            return json.dumps(payload, ensure_ascii=False)
        except BrandingError as e:
            logger.warning(f"[BrandingManageHandler] POST rejected: {e.code}: {e.message}")
            return _branding_error_response(e)
        except Exception as e:
            logger.exception(f"[BrandingManageHandler] POST failed: {e}")
            return _branding_error_response(BrandingError("storage_error", "品牌保存失败", 500))


class BrandingResetHandler:
    """POST /api/branding/reset - reset to built-in defaults (full confirm)."""

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Cache-Control', 'no-store')
        try:
            audit_ctx = _branding_require_write()
            try:
                data = json.loads(web.data() or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("expected object")
            except ValueError as exc:
                raise BrandingError("invalid_request", "请求 JSON 无效", 400) from exc
            expected = data.get("expected_revision")
            try:
                expected_revision = int(expected)
            except (TypeError, ValueError):
                raise BrandingError("missing_expected_revision", "缺少版本号", 400)
            service = _branding_service()
            operator = audit_ctx.username if audit_ctx is not None else "console"
            record = service.reset(expected_revision, operator=operator)
            _branding_record_audit(audit_ctx, "branding.reset", record, reset=True)
            payload = _branding_management_payload(service, record=record, ctx=audit_ctx)
            return json.dumps(payload, ensure_ascii=False)
        except BrandingError as e:
            logger.warning(f"[BrandingResetHandler] rejected: {e.code}: {e.message}")
            return _branding_error_response(e)
        except Exception as e:
            logger.exception(f"[BrandingResetHandler] failed: {e}")
            return _branding_error_response(BrandingError("storage_error", "品牌重置失败", 500))


class BrandingAssetHandler:
    """GET /api/branding/assets/<asset-id>.png - public referenced asset."""

    def GET(self, asset_id):
        try:
            mime, data = _branding_service().resolve_asset(asset_id)
        except BrandingError as e:
            # Disabled feature or unknown asset -> 404 + built-in fallback is the
            # client's job. Do not leak internals here.
            return _branding_error_response(e)
        except OSError:
            return _branding_error_response(BrandingError("storage_error", "无法读取品牌图片", 500))
        web.header('Content-Type', mime)
        web.header('X-Content-Type-Options', 'nosniff')
        web.header('Cache-Control', 'public, max-age=31536000, immutable')
        return data


class ModelsHandler:
    """API for the unified Models console.

    Layered model:
      Layer 1 (providers): vendor credentials shared across capabilities.
                            Stored as flat *_api_key / *_api_base fields in
                            config.json — the same fields ConfigHandler
                            already manages.
      Layer 2 (capabilities): which provider/model is used by chat / vision /
                            asr / tts / embedding / image / search.

    GET  /api/models           -> overview (providers + capabilities)
    POST /api/models/provider  -> upsert a vendor credential
    DELETE /api/models/provider -> clear a vendor credential
    POST /api/models/capability -> set provider/model for a capability
    """

    # Capability -> provider ids drawn from ConfigHandler.PROVIDER_MODELS.
    _ASR_PROVIDERS = ["openai", "dashscope", "zhipu", "linkai"]
    # Web-console white-list. Other vendors stay usable via direct config.
    _TTS_PROVIDERS = ["openai", "minimax", "dashscope", "mimo", "linkai"]

    # TTS engine catalog (speech models, not voice timbres). Entries are
    # either a bare code or {value, hint?} when a friendly label helps.
    _TTS_PROVIDER_MODELS = {
        "openai":    ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"],
        "minimax": [
            {"value": "speech-2.8-hd",    "hint": "情绪渲染融合语气词,自然听感"},
            {"value": "speech-2.8-turbo", "hint": "极致生成速度,更自然逼真"},
            {"value": "speech-2.6-hd",    "hint": "超低延时,归一化升级"},
            {"value": "speech-2.6-turbo", "hint": "更快更便宜,适合语音聊天/数字人"},
        ],
        "dashscope": [
            {"value": "qwen3-tts-flash", "hint": "覆盖普通话、方言与主流外语"},
        ],
        # 小米 MiMo TTS 系列，通过 chat completions 接口合成
        "mimo": [
            {"value": "mimo-v2.5-tts", "hint": "预置音色 · 支持唱歌模式"},
        ],
        # Aggregating gateway: a single endpoint multiplexes several
        # underlying TTS engines, selected via the `model` field.
        # Each engine exposes its own voice catalog (see _TTS_PROVIDER_VOICES).
        "linkai": [
            {"value": "tts-1",  "hint": "OpenAI · 多语种通用"},
            {"value": "doubao", "hint": "字节豆包 · 中文音色丰富"},
            {"value": "baidu",  "hint": "百度 · 中文主播音色"},
        ],
    }

    # ASR engine catalog per provider. The first entry of each list is the
    # runtime default (mirrors DEFAULT_ASR_MODEL in voice/*). Users can still
    # pick "custom" in the UI to send any other model id.
    _ASR_PROVIDER_MODELS = {
        "openai": [
            {"value": "gpt-4o-mini-transcribe", "hint": "默认 · 速度快"},
            {"value": "gpt-4o-transcribe",      "hint": "更高准确率"},
            {"value": "whisper-1",              "hint": "经典 Whisper"},
        ],
        "dashscope": [
            {"value": "qwen3-asr-flash", "hint": "覆盖普通话、方言与主流外语"},
        ],
        "zhipu": [
            {"value": "glm-asr-2512", "hint": "智谱语音识别"},
        ],
        # LinkAI gateway pins whisper-1 for ASR and ignores any other id,
        # so expose only that to avoid misleading the user.
        "linkai": [
            {"value": "whisper-1", "hint": "网关固定使用"},
        ],
    }

    # Per-provider voice timbres. Entries can be a bare code string
    # (label = code) or {value, hint?} when a friendly secondary label
    # helps recognition. We keep `value` as the raw API code so power
    # users can cross-reference config.json.
    _TTS_PROVIDER_VOICES = {
        "openai":    [
            "alloy", "echo", "fable", "onyx", "nova", "shimmer",
            "ash", "ballad", "coral", "sage", "verse",
        ],
        "minimax": [
            # Mandarin Chinese (full catalog)
            {"value": "male-qn-qingse",                           "hint": "中文 · 青涩青年（男）"},
            {"value": "male-qn-jingying",                         "hint": "中文 · 精英青年（男）"},
            {"value": "male-qn-badao",                            "hint": "中文 · 霸道青年（男）"},
            {"value": "male-qn-daxuesheng",                       "hint": "中文 · 青年大学生（男）"},
            {"value": "female-shaonv",                            "hint": "中文 · 少女（女）"},
            {"value": "female-yujie",                             "hint": "中文 · 御姐（女）"},
            {"value": "female-chengshu",                          "hint": "中文 · 成熟女性（女）"},
            {"value": "female-tianmei",                           "hint": "中文 · 甜美女性（女）"},
            {"value": "male-qn-qingse-jingpin",                   "hint": "中文 · 青涩青年-beta（男）"},
            {"value": "male-qn-jingying-jingpin",                 "hint": "中文 · 精英青年-beta（男）"},
            {"value": "male-qn-badao-jingpin",                    "hint": "中文 · 霸道青年-beta（男）"},
            {"value": "male-qn-daxuesheng-jingpin",               "hint": "中文 · 青年大学生-beta（男）"},
            {"value": "female-shaonv-jingpin",                    "hint": "中文 · 少女-beta（女）"},
            {"value": "female-yujie-jingpin",                     "hint": "中文 · 御姐-beta（女）"},
            {"value": "female-chengshu-jingpin",                  "hint": "中文 · 成熟女性-beta（女）"},
            {"value": "female-tianmei-jingpin",                   "hint": "中文 · 甜美女性-beta（女）"},
            {"value": "clever_boy",                               "hint": "中文 · 聪明男童"},
            {"value": "cute_boy",                                 "hint": "中文 · 可爱男童"},
            {"value": "lovely_girl",                              "hint": "中文 · 萌萌女童"},
            {"value": "cartoon_pig",                              "hint": "中文 · 卡通猪小琪"},
            {"value": "bingjiao_didi",                            "hint": "中文 · 病娇弟弟"},
            {"value": "junlang_nanyou",                           "hint": "中文 · 俊朗男友"},
            {"value": "chunzhen_xuedi",                           "hint": "中文 · 纯真学弟"},
            {"value": "lengdan_xiongzhang",                       "hint": "中文 · 冷淡学长"},
            {"value": "badao_shaoye",                             "hint": "中文 · 霸道少爷"},
            {"value": "tianxin_xiaoling",                         "hint": "中文 · 甜心小玲"},
            {"value": "qiaopi_mengmei",                           "hint": "中文 · 俏皮萌妹"},
            {"value": "wumei_yujie",                              "hint": "中文 · 妩媚御姐"},
            {"value": "diadia_xuemei",                            "hint": "中文 · 嗲嗲学妹"},
            {"value": "danya_xuejie",                             "hint": "中文 · 淡雅学姐"},
            {"value": "Chinese (Mandarin)_Reliable_Executive",    "hint": "中文 · 沉稳高管"},
            {"value": "Chinese (Mandarin)_News_Anchor",           "hint": "中文 · 新闻女声"},
            {"value": "Chinese (Mandarin)_Mature_Woman",          "hint": "中文 · 傲娇御姐"},
            {"value": "Chinese (Mandarin)_Unrestrained_Young_Man","hint": "中文 · 不羁青年"},
            {"value": "Arrogant_Miss",                            "hint": "中文 · 嚣张小姐"},
            {"value": "Robot_Armor",                              "hint": "中文 · 机械战甲"},
            {"value": "Chinese (Mandarin)_Kind-hearted_Antie",    "hint": "中文 · 热心大婶"},
            {"value": "Chinese (Mandarin)_HK_Flight_Attendant",   "hint": "中文 · 港普空姐"},
            {"value": "Chinese (Mandarin)_Humorous_Elder",        "hint": "中文 · 搞笑大爷"},
            {"value": "Chinese (Mandarin)_Gentleman",             "hint": "中文 · 温润男声"},
            {"value": "Chinese (Mandarin)_Warm_Bestie",           "hint": "中文 · 温暖闺蜜"},
            {"value": "Chinese (Mandarin)_Male_Announcer",        "hint": "中文 · 播报男声"},
            {"value": "Chinese (Mandarin)_Sweet_Lady",            "hint": "中文 · 甜美女声"},
            {"value": "Chinese (Mandarin)_Southern_Young_Man",    "hint": "中文 · 南方小哥"},
            {"value": "Chinese (Mandarin)_Wise_Women",            "hint": "中文 · 阅历姐姐"},
            {"value": "Chinese (Mandarin)_Gentle_Youth",          "hint": "中文 · 温润青年"},
            {"value": "Chinese (Mandarin)_Warm_Girl",             "hint": "中文 · 温暖少女"},
            {"value": "Chinese (Mandarin)_Kind-hearted_Elder",    "hint": "中文 · 花甲奶奶"},
            {"value": "Chinese (Mandarin)_Cute_Spirit",           "hint": "中文 · 憨憨萌兽"},
            {"value": "Chinese (Mandarin)_Radio_Host",            "hint": "中文 · 电台男主播"},
            {"value": "Chinese (Mandarin)_Lyrical_Voice",         "hint": "中文 · 抒情男声"},
            {"value": "Chinese (Mandarin)_Straightforward_Boy",   "hint": "中文 · 率真弟弟"},
            {"value": "Chinese (Mandarin)_Sincere_Adult",         "hint": "中文 · 真诚青年"},
            {"value": "Chinese (Mandarin)_Gentle_Senior",         "hint": "中文 · 温柔学姐"},
            {"value": "Chinese (Mandarin)_Stubborn_Friend",       "hint": "中文 · 嘴硬竹马"},
            {"value": "Chinese (Mandarin)_Crisp_Girl",            "hint": "中文 · 清脆少女"},
            {"value": "Chinese (Mandarin)_Pure-hearted_Boy",      "hint": "中文 · 清澈邻家弟弟"},
            {"value": "Chinese (Mandarin)_Soft_Girl",             "hint": "中文 · 柔和少女"},
            # Cantonese (full catalog)
            {"value": "Cantonese_ProfessionalHost（F)",            "hint": "粤语 · 专业女主持"},
            {"value": "Cantonese_GentleLady",                     "hint": "粤语 · 温柔女声"},
            {"value": "Cantonese_ProfessionalHost（M)",            "hint": "粤语 · 专业男主持"},
            {"value": "Cantonese_PlayfulMan",                     "hint": "粤语 · 活泼男声"},
            {"value": "Cantonese_CuteGirl",                       "hint": "粤语 · 可爱女孩"},
            {"value": "Cantonese_KindWoman",                      "hint": "粤语 · 善良女声"},
            # English (curated: 1F + 1M)
            {"value": "English_Graceful_Lady",                    "hint": "英文 · Graceful Lady（女）"},
            {"value": "English_Trustworthy_Man",                  "hint": "英文 · Trustworthy Man（男）"},
            # Japanese (curated: 1F + 1M)
            {"value": "Japanese_KindLady",                        "hint": "日文 · Kind Lady（女）"},
            {"value": "Japanese_LoyalKnight",                     "hint": "日文 · Loyal Knight（男）"},
            # Korean (curated: 1F + 1M)
            {"value": "Korean_SweetGirl",                         "hint": "韩文 · Sweet Girl（女）"},
            {"value": "Korean_CheerfulBoyfriend",                 "hint": "韩文 · Cheerful Boyfriend（男）"},
        ],
        "dashscope": [
            {"value": "Cherry",   "hint": "芊悦 · 阳光女声"},
            {"value": "Serena",   "hint": "苏瑶 · 温柔女声"},
            {"value": "Chelsie",  "hint": "千雪 · 二次元少女"},
            {"value": "Ethan",    "hint": "晨煦 · 阳光男声"},
            {"value": "Moon",     "hint": "月白 · 率性男声"},
            {"value": "Kai",      "hint": "凯 · 治愈男声"},
            {"value": "Nofish",   "hint": "不吃鱼 · 设计师男声"},
            {"value": "Bella",    "hint": "萌宝 · 小萝莉"},
            {"value": "Bunny",    "hint": "萌小姬 · 萌系少女"},
            {"value": "Stella",   "hint": "少女阿月 · 元气少女"},
            {"value": "Neil",     "hint": "阿闻 · 新闻主播"},
            {"value": "Seren",    "hint": "小婉 · 助眠女声"},
            {"value": "Jada",     "hint": "上海话 · 阿珍"},
            {"value": "Dylan",    "hint": "北京话 · 晓东"},
            {"value": "Sunny",    "hint": "四川话 · 晴儿"},
            {"value": "Eric",     "hint": "四川话 · 程川"},
            {"value": "Rocky",    "hint": "粤语 · 阿强"},
            {"value": "Kiki",     "hint": "粤语 · 阿清"},
            {"value": "Peter",    "hint": "天津话 · 李彼得"},
            {"value": "Marcus",   "hint": "陕西话 · 秦川"},
            {"value": "Roy",      "hint": "闽南语 · 阿杰"},
        ],
        # 小米 MiMo 预置音色列表（mimo-v2.5-tts），文档：
        # https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5
        "mimo": [
            {"value": "冰糖",   "hint": "中文 · 女声 · 冰糖"},
            {"value": "茉莉",   "hint": "中文 · 女声 · 茉莉"},
            {"value": "苏打",   "hint": "中文 · 男声 · 苏打"},
            {"value": "白桦",   "hint": "中文 · 男声 · 白桦"},
            {"value": "Mia",   "hint": "英文 · 女声 · Mia"},
            {"value": "Chloe", "hint": "英文 · 女声 · Chloe"},
            {"value": "Milo",  "hint": "英文 · 男声 · Milo"},
            {"value": "Dean",  "hint": "英文 · 男声 · Dean"},
        ],
        # Aggregating gateway: voices are scoped per engine model. The
        # frontend picks the correct list based on the selected model so
        # users don't see incompatible timbres for the active engine.
        "linkai": {
            "tts-1": [
                "alloy", "echo", "fable", "onyx", "nova", "shimmer",
            ],
            "doubao": [
                {"value": "zh_female_wanwanxiaohe_moon_bigtts",       "hint": "湾湾小何"},
                {"value": "BV007_streaming",                          "hint": "亲切女声"},
                {"value": "BV001_streaming",                          "hint": "通用女声"},
                {"value": "BV002_streaming",                          "hint": "通用男声"},
                {"value": "BV051_streaming",                          "hint": "奶气萌娃"},
                {"value": "zh_female_linjianvhai_moon_bigtts",        "hint": "邻家女孩"},
                {"value": "BV700_streaming",                          "hint": "灿灿"},
                {"value": "BV019_streaming",                          "hint": "重庆小伙"},
                {"value": "BV524_streaming",                          "hint": "日语男声"},
                {"value": "BV021_streaming",                          "hint": "东北老铁"},
                {"value": "BV701_streaming",                          "hint": "擎苍"},
                {"value": "BV113_streaming",                          "hint": "甜宠少御"},
                {"value": "BV056_streaming",                          "hint": "阳光男声"},
                {"value": "BV213_streaming",                          "hint": "广西表哥"},
                {"value": "BV119_streaming",                          "hint": "通用赘婿"},
                {"value": "BV705_streaming",                          "hint": "炀炀"},
                {"value": "BV033_streaming",                          "hint": "温柔小哥"},
                {"value": "BV102_streaming",                          "hint": "儒雅青年"},
                {"value": "BV522_streaming",                          "hint": "气质女生"},
                {"value": "BV034_streaming",                          "hint": "知性姐姐 · 双语"},
                {"value": "BV005_streaming",                          "hint": "活泼女声"},
                {"value": "zh_female_wanqudashu_moon_bigtts",         "hint": "湾区大叔"},
                {"value": "zh_female_daimengchuanmei_moon_bigtts",    "hint": "呆萌川妹"},
                {"value": "zh_male_guozhoudege_moon_bigtts",          "hint": "广州德哥"},
                {"value": "zh_male_beijingxiaoye_moon_bigtts",        "hint": "北京小爷"},
                {"value": "zh_male_shaonianzixin_moon_bigtts",        "hint": "少年梓辛 / Brayan"},
                {"value": "zh_female_meilinvyou_moon_bigtts",         "hint": "魅力女友"},
                {"value": "zh_male_shenyeboke_moon_bigtts",           "hint": "深夜播客"},
                {"value": "zh_female_sajiaonvyou_moon_bigtts",        "hint": "柔美女友"},
                {"value": "zh_female_yuanqinvyou_moon_bigtts",        "hint": "撒娇学妹"},
                {"value": "zh_male_haoyuxiaoge_moon_bigtts",          "hint": "浩宇小哥"},
                {"value": "zh_male_guangxiyuanzhou_moon_bigtts",      "hint": "广西远舟"},
                {"value": "zh_female_meituojieer_moon_bigtts",        "hint": "妹坨洁儿"},
                {"value": "zh_male_yuzhouzixuan_moon_bigtts",         "hint": "豫州子轩"},
                {"value": "BV115_streaming",                          "hint": "古风少御"},
                {"value": "zh_female_gaolengyujie_moon_bigtts",       "hint": "高冷御姐"},
                {"value": "zh_male_yuanboxiaoshu_moon_bigtts",        "hint": "渊博小叔"},
                {"value": "zh_male_yangguangqingnian_moon_bigtts",    "hint": "阳光青年"},
                {"value": "zh_male_aojiaobazong_moon_bigtts",         "hint": "傲娇霸总"},
                {"value": "zh_male_jingqiangkanye_moon_bigtts",       "hint": "京腔侃爷 / Harmony"},
                {"value": "zh_female_shuangkuaisisi_moon_bigtts",     "hint": "爽快思思 / Skye"},
                {"value": "zh_male_wennuanahu_moon_bigtts",           "hint": "温暖阿虎 / Alvin"},
                {"value": "multi_female_shuangkuaisisi_moon_bigtts",  "hint": "はるこ / Esmeralda"},
                {"value": "multi_male_jingqiangkanye_moon_bigtts",    "hint": "かずね / Javier or Álvaro"},
                {"value": "multi_female_gaolengyujie_moon_bigtts",    "hint": "あけみ"},
                {"value": "multi_male_wanqudashu_moon_bigtts",        "hint": "ひろし / Roberto"},
                {"value": "ICL_zh_female_bingruoshaonv_tob",          "hint": "病弱少女"},
                {"value": "ICL_zh_female_huoponvhai_tob",             "hint": "活泼女孩"},
                {"value": "ICL_zh_female_heainainai_tob",             "hint": "和蔼奶奶"},
                {"value": "ICL_zh_female_linjuayi_tob",               "hint": "邻居阿姨"},
                {"value": "zh_female_wenrouxiaoya_moon_bigtts",       "hint": "温柔小雅"},
                {"value": "zh_female_tianmeixiaoyuan_moon_bigtts",    "hint": "甜美小源"},
                {"value": "zh_female_qingchezizi_moon_bigtts",        "hint": "清澈梓梓"},
                {"value": "zh_male_dongfanghaoran_moon_bigtts",       "hint": "东方浩然"},
                {"value": "zh_male_jieshuoxiaoming_moon_bigtts",      "hint": "解说小明"},
                {"value": "zh_female_kailangjiejie_moon_bigtts",      "hint": "开朗姐姐"},
                {"value": "zh_male_linjiananhai_moon_bigtts",         "hint": "邻家男孩"},
                {"value": "zh_female_tianmeiyueyue_moon_bigtts",      "hint": "甜美悦悦"},
                {"value": "zh_female_xinlingjitang_moon_bigtts",      "hint": "心灵鸡汤"},
            ],
            "baidu": [
                {"value": "baidu_0",    "hint": "度小美 · 标准女主播"},
                {"value": "baidu_1",    "hint": "度小宇 · 亲切男声"},
                {"value": "baidu_3",    "hint": "度逍遥 · 情感男声"},
                {"value": "baidu_4",    "hint": "度丫丫 · 童声"},
                {"value": "baidu_5",    "hint": "度小娇 · 成熟女主播"},
                {"value": "baidu_5003", "hint": "度逍遥 · 情感男声"},
                {"value": "baidu_5118", "hint": "度小鹿 · 甜美女声"},
                {"value": "baidu_103",  "hint": "度米朵 · 可爱童声"},
                {"value": "baidu_106",  "hint": "度博文 · 专业男主播"},
                {"value": "baidu_110",  "hint": "度小童 · 童声主播"},
                {"value": "baidu_111",  "hint": "度小萌 · 软萌妹子"},
                {"value": "baidu_4003", "hint": "度逍遥 · 情感男声"},
                {"value": "baidu_4100", "hint": "度小雯 · 活力女主播"},
                {"value": "baidu_4103", "hint": "度米朵 · 可爱女声"},
                {"value": "baidu_4105", "hint": "度灵儿 · 清澈女声"},
                {"value": "baidu_4106", "hint": "度博文 · 专业男主播"},
                {"value": "baidu_4115", "hint": "度小贤 · 电台男主播"},
                {"value": "baidu_4117", "hint": "度小乔 · 活泼女声"},
                {"value": "baidu_4119", "hint": "度小鹿 · 甜美女声"},
                {"value": "baidu_4129", "hint": "度小彦 · 知识男主播"},
                {"value": "baidu_4140", "hint": "度小新 · 专业女主播"},
                {"value": "baidu_4143", "hint": "度清风 · 配音男声"},
                {"value": "baidu_4144", "hint": "度姗姗 · 娱乐女声"},
                {"value": "baidu_4149", "hint": "度星河 · 广告男声"},
                {"value": "baidu_4206", "hint": "度博文 · 综艺男声"},
                {"value": "baidu_4226", "hint": "南方 · 电台女主播"},
                {"value": "baidu_4254", "hint": "度小清 · 广告女声"},
                {"value": "baidu_4278", "hint": "度小贝 · 知识女主播"},
            ],
        },
    }
    _EMBEDDING_PROVIDERS = ["openai", "dashscope", "doubao", "zhipu", "linkai", "custom"]

    # Embedding model catalog per provider. Mirrors the default_model in
    # agent/memory/embedding/provider.py::EMBEDDING_VENDORS.
    # Custom providers have no preset list — model names vary per vendor,
    # so the user always types the model id manually.
    _EMBEDDING_PROVIDER_MODELS = {
        "openai":    ["text-embedding-3-small", "text-embedding-3-large"],
        "dashscope": ["text-embedding-v4"],
        "doubao":    ["doubao-embedding-vision-251215"],
        "zhipu":     ["embedding-3"],
        "linkai":    ["text-embedding-3-small"],
        "custom":    [],
    }

    # Capability-scoped model catalogs. The chat dropdown can reuse the
    # provider's generic model list, but vision and image generation are
    # served by a narrower subset that the runtime actually dispatches to —
    # see agent/tools/vision/vision.py and skills/image-generation/SKILL.md.
    # Anything not listed here intentionally hides the model dropdown so
    # users cannot pin a chat-only model and silently get a 4xx at runtime.
    _VISION_PROVIDER_MODELS = {
        # DeepSeek 视觉模型：V4 Flash vision（experimental, multimodal）。
        # Placed first so it's the default image-understanding vendor —
        # deepseek-v4-flash is the project's default main model, so a single
        # DeepSeek key covers both chat and vision.
        "deepseek":  [const.DEEPSEEK_V4_FLASH_VISION_EXP],
        # OpenAI ordering puts the GPT-5.6 family first, then GPT-5.5/5.4,
        # GPT-5 and the GPT-4.1/4o backstops.
        "openai":    [
            const.GPT_56_LUNA,
            const.GPT_56_TERRA,
            const.GPT_56_SOL,
            const.GPT_55,
            const.GPT_54,
            const.GPT_54_MINI,
            const.GPT_54_NANO,
            const.GPT_5,
            const.GPT_41,
            const.GPT_41_MINI,
            const.GPT_4o,
        ],
        "doubao":    [const.DOUBAO_SEED_2_1_PRO, const.DOUBAO_SEED_2_1_TURBO, const.DOUBAO_SEED_2_PRO],
        "moonshot":  [const.KIMI_K2_6],
        "dashscope": [const.QWEN38_FLASH, const.QWEN37_PLUS, const.QWEN36_PLUS],
        # claude-sonnet-5 stays first here (unlike the chat lists): the first
        # entry is the auto-picked vision model, and image understanding does
        # not justify the Opus price.
        "claudeAPI": [const.CLAUDE_SONNET_5, const.CLAUDE_OPUS_5, const.CLAUDE_FABLE_5_1, const.CLAUDE_FABLE_5, const.CLAUDE_4_8_OPUS, const.CLAUDE_4_7_OPUS, const.CLAUDE_4_6_SONNET, const.CLAUDE_4_6_OPUS],
        "gemini":    [const.GEMINI_37_FLASH, const.GEMINI_36_FLASH, const.GEMINI_35_FLASH, const.GEMINI_31_FLASH_LITE_PRE, const.GEMINI_31_PRO_PRE, const.GEMINI_3_FLASH_PRE],
        "qianfan":   [const.ERNIE_45_TURBO_VL],
        # glm-5.3-flash is natively multimodal and dispatched as-is; the
        # text-only chat models (glm-5.2, glm-5-turbo, etc.) fall back to the
        # dedicated glm-5v-turbo vision model (see
        # models/zhipuai/zhipuai_bot.py::call_vision).
        "zhipu":     [const.GLM_5_3_FLASH, const.GLM_5V_TURBO],
        # MiniMax's vision endpoint is similarly hard-coded to MiniMax-Text-01
        # (see models/minimax/minimax_bot.py::call_vision); the M2.x chat
        # family is text-only.
        "minimax":   [const.MINIMAX_TEXT_01],
        # MiMo 原生全模态模型：v2.5-pro / v2.5 支持图像/音频/视频输入
        "mimo":      [const.MIMO_V2_5_PRO, const.MIMO_V2_5],
        # LinkAI proxies the underlying vendor; surface a curated set of
        # multimodal models. Order: gpt-4.1-mini → gpt-5.4-mini as the
        # cross-vendor baselines, then each vendor's recommended default.
        "linkai":    [
            const.GPT_41_MINI,
            const.GPT_54_MINI,
            const.QWEN38_FLASH,
            const.QWEN37_PLUS,
            const.DOUBAO_SEED_2_1_PRO,
            const.KIMI_K2_6,
            const.CLAUDE_SONNET_5,
            const.CLAUDE_FABLE_5_1,
            const.CLAUDE_FABLE_5,
            const.GEMINI_31_FLASH_LITE_PRE,
        ],
        # Custom OpenAI-compatible providers have no preset list — model
        # names vary per vendor, so the user types the model id manually.
        "custom": [],
    }

    # Image-generation catalog. Source of truth: skills/image-generation/SKILL.md.
    # Listed verbatim (not via const.*) because these are skill-side names
    # the script forwards directly to the vendor's image endpoint.
    #
    # Two shapes are accepted per model entry:
    #   - bare string                           → the model id, no hint
    #   - {"value": ..., "hint": "..."}         → model id + dim secondary
    #                                             label rendered on the right
    #                                             of the dropdown row. Useful
    #                                             for surfacing brand names
    #                                             (e.g. "Nano Banana 2" next
    #                                             to gemini-3.1-flash-image-preview).
    # The skill itself maps either form to the real vendor endpoint, so the
    # hint is purely cosmetic.
    _IMAGE_PROVIDER_MODELS = {
        "openai":    ["gpt-image-2", "gpt-image-1"],
        "gemini": [
            {"value": "gemini-3.1-flash-image-preview", "hint": "Nano Banana 2"},
            {"value": "gemini-3-pro-image-preview",     "hint": "Nano Banana Pro"},
            {"value": "gemini-2.5-flash-image",         "hint": "Nano Banana"},
        ],
        "doubao":    ["seedream-5.0-lite", "seedream-4.5"],
        "dashscope": ["qwen-image-2.0-pro", "qwen-image-2.0"],
        "minimax":   ["image-01"],
        "linkai": [
            "gpt-image-2",
            {"value": "gemini-3.1-flash-image-preview", "hint": "Nano Banana 2"},
            {"value": "gemini-3-pro-image-preview",     "hint": "Nano Banana Pro"},
            "seedream-5.0-lite",
        ],
        "custom": [],
    }

    @staticmethod
    def _config_path() -> str:
        return os.path.join(get_data_root(), "config.json")

    @classmethod
    def _read_file_config(cls) -> dict:
        return _read_config_file_for_write()

    @classmethod
    def _write_file_config(cls, data: dict) -> None:
        with open(cls._config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    @staticmethod
    def _is_real_key(value: str) -> bool:
        return bool(value) and value not in ("", "YOUR API KEY", "YOUR_API_KEY")

    @classmethod
    def _custom_provider_cards(cls, local_config: dict) -> List[dict]:
        """Expand ``custom_providers`` into one card per provider.

        Each user-defined OpenAI-compatible provider becomes its own card with
        id ``custom:<id>`` so the frontend can render, edit, delete and
        activate them independently. The card carries ``is_custom=True`` and
        ``active`` flags that the UI uses to render the extra controls.

        Returns an empty list when no multi-providers are configured, in which
        case the caller keeps the single legacy ``custom`` card untouched —
        guaranteeing backward compatibility with the flat
        ``custom_api_key`` / ``custom_api_base`` config.
        """
        try:
            from models.custom_provider import get_custom_providers, parse_custom_bot_type
            providers = get_custom_providers()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[ModelsHandler] failed to load custom_providers: {e}")
            providers = []
        if not providers:
            return []

        # Determine the currently active provider id from bot_type.
        bot_type = local_config.get("bot_type") or ""
        _, active_id = parse_custom_bot_type(bot_type)

        meta = ConfigHandler.PROVIDER_MODELS.get("custom") or {}
        cards = []
        for p in providers:
            pid = p.get("id") or ""
            name = p.get("name") or pid
            raw_key = p.get("api_key") or ""
            raw_base = p.get("api_base") or ""
            # A custom (OpenAI-compatible) provider's API key is optional — some
            # self-hosted / gateway endpoints need no auth. Treat the provider
            # as configured once it has an api_base, so a keyless-but-valid
            # endpoint isn't shown as an unconfigured (greyed-out) vendor.
            configured = bool(raw_base) or cls._is_real_key(raw_key)
            cards.append({
                "id": f"custom:{pid}",
                "label": {"zh": name, "en": name},
                "configured": configured,
                "is_custom": True,
                "custom_id": pid,
                "custom_name": name,
                "active": (pid == active_id),
                "model": p.get("model") or "",
                # Custom cards are edited via the dedicated set_custom_provider
                # action, not the field-based set_provider flow, so the field
                # names are intentionally null.
                "api_key_field": None,
                "api_base_field": None,
                "api_key_masked": ConfigHandler._mask_key(raw_key) if cls._is_real_key(raw_key) else "",
                "api_base": raw_base,
                "api_base_default": "",
                "api_base_placeholder": meta.get("api_base_placeholder") or "",
                "models": [p.get("model")] if p.get("model") else [],
            })
        return cards

    @classmethod
    def _legacy_custom_in_use(cls, local_config: dict) -> bool:
        """True when the flat single-provider custom config is still relevant:
        either it is the active bot_type, or its key/base fields are filled.
        In that case the legacy "custom" card must stay visible even when
        multi ``custom_providers`` entries exist."""
        if (local_config.get("bot_type") or "") == "custom":
            return True
        return (cls._is_real_key(local_config.get("custom_api_key") or "")
                or bool(local_config.get("custom_api_base")))

    @classmethod
    def _provider_overview(cls) -> List[dict]:
        """All known providers (configured first, unconfigured after).
        Re-uses ConfigHandler.PROVIDER_MODELS for the canonical list.

        When the user has defined multiple custom (OpenAI-compatible)
        providers via ``custom_providers``, the single built-in ``custom``
        card is replaced by one card per provider (see
        ``_custom_provider_cards``). Otherwise the legacy single ``custom``
        card is shown unchanged.
        """
        local_config = conf()
        custom_cards = cls._custom_provider_cards(local_config)
        # Keep the legacy single "custom" card visible alongside the expanded
        # ones when the flat custom_api_key/base config is active or filled,
        # so existing single-provider setups never disappear from the UI.
        keep_legacy_custom = cls._legacy_custom_in_use(local_config)
        items = []
        for pid, p in ConfigHandler.PROVIDER_MODELS.items():
            if pid == "custom" and custom_cards:
                # Multi-provider mode: emit the expanded cards, plus the
                # legacy card when it is still in use.
                items.extend(custom_cards)
                if not keep_legacy_custom:
                    continue
            key_field = p.get("api_key_field")
            base_field = p.get("api_base_key")
            raw_key = local_config.get(key_field, "") if key_field else ""
            raw_base = local_config.get(base_field, "") if base_field else ""
            configured = cls._is_real_key(raw_key)
            items.append({
                "id": pid,
                "label": p["label"],
                "configured": configured,
                "is_custom": (pid == "custom"),
                "api_key_field": key_field,
                "api_base_field": base_field,
                "api_key_masked": ConfigHandler._mask_key(raw_key) if configured else "",
                "api_base": raw_base or (p.get("api_base_default") or ""),
                "api_base_default": p.get("api_base_default") or "",
                "api_base_placeholder": p.get("api_base_placeholder") or "",
                "models": list(p.get("models") or []),
            })

        def _sort_key(it):
            pid = it["id"]
            # Custom expanded cards share the sort weight of the base "custom"
            # entry so they cluster where the single custom card used to be.
            base_id = "custom" if it.get("is_custom") else pid
            try:
                order = list(ConfigHandler.PROVIDER_MODELS.keys()).index(base_id)
            except ValueError:
                order = len(ConfigHandler.PROVIDER_MODELS)
            return (0 if it["configured"] else 1, order)

        items.sort(key=_sort_key)
        return items

    # Map a chat `model` name to a provider id in PROVIDER_MODELS. Mirrors the
    # inference in bridge.py::Bridge.__init__ so that a config with an empty
    # `bot_type` (valid at runtime, since the bridge derives the provider from
    # `model`) is still recognized as "configured" by the models handler and
    # doesn't wrongly trigger the onboarding wizard. Prefix rules are ordered
    # most-specific first; the returned ids are the PROVIDER_MODELS keys.
    @staticmethod
    def _infer_provider_from_model(model: str) -> str:
        """Best-effort provider id from a model name. Returns "" when unknown.

        Kept deliberately tolerant: any unexpected input yields "" rather than
        raising, so callers can treat "no inference" and "bad input" the same.
        """
        try:
            if not model or not isinstance(model, str):
                return ""
            m = model.strip().lower()
            if not m:
                return ""
            # Exact matches first (models whose name isn't a clean prefix).
            exact = {
                "wenxin": "qianfan",
                "wenxin-4": "qianfan",
                "abab6.5": "minimax",
                "abab6.5-chat": "minimax",
            }
            if m in exact:
                return exact[m]
            # Prefix rules — order matters where prefixes could overlap.
            prefix_rules = (
                ("deepseek", "deepseek"),
                ("gemini", "gemini"),
                ("glm", "zhipu"),
                ("claude", "claudeAPI"),
                ("kimi", "moonshot"),
                ("moonshot", "moonshot"),
                ("doubao", "doubao"),
                ("mimo-", "mimo"),
                ("qwen", "dashscope"),
                ("qwq", "dashscope"),
                ("qvq", "dashscope"),
                ("ernie", "qianfan"),
                ("minimax", "minimax"),
                ("gpt", "openai"),
                ("o1", "openai"),
                ("o3", "openai"),
                ("o4", "openai"),
            )
            for prefix, pid in prefix_rules:
                if m.startswith(prefix):
                    return pid
            # `qianfan` is sometimes used directly as the model name.
            if m == "qianfan":
                return "qianfan"
            return ""
        except Exception:
            # Never let inference break the models endpoint / startup.
            return ""

    @classmethod
    def _chat_capability(cls, local_config: dict) -> dict:
        """Main chat model — drives the agent. bot_type maps to a provider id."""
        bot_type = local_config.get("bot_type") or ""
        provider_id = "openai" if bot_type == "chatGPT" else bot_type
        is_custom_id = provider_id.startswith("custom:")
        if (provider_id not in ConfigHandler.PROVIDER_MODELS and not is_custom_id
                and local_config.get("use_linkai")):
            provider_id = "linkai"
        # When `bot_type` doesn't resolve to a known provider (e.g. it was
        # left empty by a config edit, which the runtime bridge tolerates by
        # inferring from `model`), fall back to the same model-based inference
        # here. Otherwise the wizard would treat a working setup as unconfigured
        # and re-open on every launch. Guarded so a failure can't affect startup.
        if provider_id not in ConfigHandler.PROVIDER_MODELS and not is_custom_id:
            try:
                inferred = cls._infer_provider_from_model(local_config.get("model", ""))
                if inferred in ConfigHandler.PROVIDER_MODELS:
                    provider_id = inferred
            except Exception:
                pass
        # In multi-provider mode, replace the single "custom" entry with the
        # expanded "custom:<id>" ids so the chat dropdown matches the cards.
        # The legacy "custom" entry stays when its flat config is still used.
        provider_ids = []
        custom_cards = cls._custom_provider_cards(local_config)
        keep_legacy_custom = cls._legacy_custom_in_use(local_config)
        for pid in ConfigHandler.PROVIDER_MODELS.keys():
            if pid == "custom" and custom_cards:
                provider_ids.extend(c["id"] for c in custom_cards)
                if keep_legacy_custom:
                    provider_ids.append(pid)
            else:
                provider_ids.append(pid)
        return {
            "editable": True,
            "current_provider": provider_id,
            "current_model": local_config.get("model", ""),
            "providers": provider_ids,
            "use_linkai": bool(local_config.get("use_linkai", False)),
        }

    @staticmethod
    def _chat_provider_models() -> dict:
        """{provider_id: [model, ...]} for every chat-capable vendor.

        ``ConfigHandler.PROVIDER_MODELS`` carries per-vendor metadata
        (label, api_key_field, ...) around the model list; the console's model
        picker wants only the lists. Kept as its own helper so the two chat
        cards can never drift apart.
        """
        out = {}
        for pid, meta in ConfigHandler.PROVIDER_MODELS.items():
            models = (meta or {}).get("models")
            out[pid] = list(models) if isinstance(models, (list, tuple)) else []
        return out

    @classmethod
    def _chat_fallback_capability(cls, local_config: dict) -> dict:
        """The backup chat model, tried only after the primary one fails.

        Deliberately separate from ``_chat_capability``: the primary model is
        the one that answers, while this is a safety net that stays idle until
        an outage. It is opt-in (``enabled`` defaults to false) and needs both
        a provider and a model — a half-filled entry is treated as "off" so a
        partially configured fallback can never hijack a healthy setup.
        """
        cfg = local_config.get("chat_fallback") or {}
        if not isinstance(cfg, dict):
            cfg = {}
        provider_id = (cfg.get("provider") or "").strip()
        model = (cfg.get("model") or "").strip()
        # Same provider list as the primary chat card, so the two dropdowns
        # always offer identical choices (including expanded custom:<id>).
        primary = cls._chat_capability(local_config)
        return {
            "editable": True,
            "enabled": bool(cfg.get("enabled", False)),
            "current_provider": provider_id,
            "current_model": model,
            "providers": primary.get("providers", []),
            # The model picker expects {provider_id: [models]}. PROVIDER_MODELS
            # is richer ({provider_id: {label, models, ...}}), so reduce it to
            # just the lists — handing over the raw dict makes the web console
            # call .slice() on a mapping and throw.
            "provider_models": cls._chat_provider_models(),
            "max_switches": cfg.get("max_switches", 1),
            # Shown in the UI so it's obvious the fallback is inactive.
            "primary_provider": primary.get("current_provider", ""),
            "primary_model": primary.get("current_model", ""),
        }

    # Auto-fallback order for vision when no explicit model is pinned.
    # Mirrors agent/tools/vision/vision.py::_resolve_providers — DeepSeek and
    # other text-only chat bots are intentionally absent, since they cannot
    # actually serve a vision request. Each entry is
    #   (provider_id, api_key_field, default_vision_model)
    # and lookups are case-insensitive on the api_key_field. LinkAI and
    # OpenAI are handled separately below so use_linkai can promote LinkAI
    # to the front of the chain.
    _VISION_AUTO_ORDER = [
        ("moonshot",  "moonshot_api_key",  const.KIMI_K2_6),
        ("doubao",    "ark_api_key",       const.DOUBAO_SEED_2_PRO),
        ("dashscope", "dashscope_api_key", const.QWEN37_PLUS),
        ("claudeAPI", "claude_api_key",    const.CLAUDE_SONNET_5),
        ("gemini",    "gemini_api_key",    const.GEMINI_37_FLASH),
        ("qianfan",   "qianfan_api_key",   const.ERNIE_45_TURBO_VL),
        ("zhipu",     "zhipu_ai_api_key",  const.GLM_5V_TURBO),
        ("minimax",   "minimax_api_key",   const.MINIMAX_TEXT_01),
        ("mimo",      "mimo_api_key",      const.MIMO_V2_5_PRO),
    ]

    @classmethod
    def _predict_vision_auto(cls, local_config: dict) -> dict:
        """Predict which provider vision.py will actually dispatch to when
        no tools.vision.model is set. Mirrors the fallback order in
        agent/tools/vision/vision.py::_resolve_providers so the UI hint
        matches reality."""
        chat = cls._chat_capability(local_config)
        main_provider = chat["current_provider"]
        main_model = chat["current_model"]
        use_linkai_flag = bool(local_config.get("use_linkai", False))
        linkai_configured = cls._is_real_key(local_config.get("linkai_api_key", ""))

        def _try(pid: str, model_default: str):
            # Look up the api_key for this provider via the canonical
            # provider table so we don't hardcode field names here.
            meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
            key_field = meta.get("api_key_field")
            if not key_field:
                return None
            if not cls._is_real_key(local_config.get(key_field, "")):
                return None
            # Pick a model that the vision runtime can actually dispatch to
            # for this provider. Using `main_model` here is unsafe — for
            # vendors like Zhipu/MiniMax the bot hard-codes the vision model
            # name regardless of the chat-model name, so surfacing the chat
            # model name in the hint is misleading. Trust the curated
            # _VISION_PROVIDER_MODELS list: prefer the main model only if
            # it appears there; otherwise show the vendor's first vision-
            # capable model.
            allowed = cls._VISION_PROVIDER_MODELS.get(pid, [])
            if pid == main_provider and main_model and main_model in allowed:
                return {"provider": pid, "model": main_model}
            fallback = allowed[0] if allowed else model_default
            return {"provider": pid, "model": fallback}

        # 1. use_linkai → suppress the hint entirely. LinkAI is a proxy and
        #    we don't observe which underlying model it picks; surfacing
        #    "LinkAI" with no model would not tell the user anything useful.
        if use_linkai_flag and linkai_configured:
            return {"provider": "", "model": ""}

        # 2. Main bot — only when it natively supports vision. We approximate
        #    "natively supports" by membership in _VISION_PROVIDER_MODELS,
        #    which is the same set vision.py's _DISCOVERABLE_MODELS covers
        #    (the DeepSeek family is included since V4 Flash vision).
        if main_provider in cls._VISION_PROVIDER_MODELS:
            hit = _try(main_provider, main_model)
            if hit:
                return hit

        # 3. Other discoverable providers in declared order
        for pid, _key, default_model in cls._VISION_AUTO_ORDER:
            hit = _try(pid, default_model)
            if hit:
                return hit

        # 4. OpenAI raw HTTP
        if cls._is_real_key(local_config.get("open_ai_api_key", "")):
            return {"provider": "openai", "model": const.GPT_55}

        # 5. LinkAI as last resort (only reached when use_linkai is off)
        if linkai_configured:
            return {"provider": "linkai", "model": const.GPT_41_MINI}

        return {"provider": "", "model": ""}

    @classmethod
    def _vision_capability(cls, local_config: dict) -> dict:
        """Vision model. tools.vision.model is the explicit override; otherwise
        the runtime fallback chain in agent/tools/vision/vision.py decides."""
        tools_conf = local_config.get("tools") or local_config.get("tool") or {}
        if not isinstance(tools_conf, dict):
            tools_conf = {}
        vision_conf = tools_conf.get("vision") or {}
        if not isinstance(vision_conf, dict):
            vision_conf = {}
        user_specified = (vision_conf.get("model") or "").strip()
        explicit_provider = (vision_conf.get("provider") or "").strip()

        # Build provider list: built-in providers + expanded custom:<id> entries.
        # Same pattern as _embedding_capability — each user-created custom
        # provider gets its own dropdown entry showing the user-chosen name.
        providers = []
        custom_cards = cls._custom_provider_cards(local_config)
        for pid in cls._VISION_PROVIDER_MODELS:
            if pid == "custom":
                if custom_cards:
                    providers.extend(c["id"] for c in custom_cards)
            else:
                providers.append(pid)

        # Provider resolution priority:
        #   1. Explicit `tools.vision.provider` (persisted via UI; supports
        #      custom model names that prefix-inference can't recognize).
        #   2. Scan per-provider model lists by model name.
        # Empty provider keeps the dropdown on "auto" when we can't tell.
        inferred_provider = ""
        if explicit_provider and explicit_provider in providers:
            inferred_provider = explicit_provider
        elif user_specified:
            for pid, models in cls._VISION_PROVIDER_MODELS.items():
                if user_specified in models:
                    # For "custom" key, map to the first custom card
                    inferred_provider = custom_cards[0]["id"] if pid == "custom" and custom_cards else pid
                    break

        # In auto mode the hint should reflect what vision.py will actually
        # dispatch to — surface that prediction via fallback_* so the UI
        # shows e.g. "openai / gpt-4.1-mini" instead of the chat-model name.
        predicted = cls._predict_vision_auto(local_config)

        return {
            "editable": True,
            "strategy": "specified" if user_specified else "auto",
            "user_specified_model": user_specified,
            "current_provider": inferred_provider,
            "current_model": user_specified,
            "fallback_provider": predicted["provider"],
            "fallback_model": predicted["model"],
            "providers": providers,
            "provider_models": cls._VISION_PROVIDER_MODELS,
        }

    @classmethod
    def _asr_capability(cls, local_config: dict) -> dict:
        # "Pick or empty" — when voice_to_text is unset we don't show a
        # current selection. `suggested_provider` previews which vendor
        # the bridge auto-picker would land on (purely a UX hint, NOT
        # persisted). Once the user saves a vendor, we lock onto it.
        explicit = (local_config.get("voice_to_text") or "").strip().lower()
        suggested = ""
        if not explicit:
            for pid in cls._ASR_PROVIDERS:
                meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
                key_field = meta.get("api_key_field")
                if key_field and cls._is_real_key(local_config.get(key_field, "")):
                    suggested = pid
                    break
        # Custom (OpenAI-compatible) vendors are selectable too — same pattern
        # as _vision_capability: each expanded custom:<id> gets an entry.
        providers = list(cls._ASR_PROVIDERS)
        custom_cards = cls._custom_provider_cards(local_config)
        if custom_cards:
            providers.extend(c["id"] for c in custom_cards)
        return {
            "editable": True,
            "current_provider": explicit,
            "suggested_provider": suggested,
            "current_model": (local_config.get("voice_to_text_model") or "") if explicit else "",
            "providers": providers,
            "provider_models": cls._ASR_PROVIDER_MODELS,
        }

    @classmethod
    def _tts_capability(cls, local_config: dict) -> dict:
        explicit = (local_config.get("text_to_voice") or "").strip().lower()
        # Custom (OpenAI-compatible) vendors are selectable too; accept them
        # (expanded custom:<id> or legacy flat "custom") as the current
        # provider so the card shows the saved selection. Other providers
        # outside the white-list don't drive the picker, but their underlying
        # runtime config is preserved so bridge still routes them.
        is_custom_id = explicit.startswith("custom:") or explicit == "custom"
        ui_provider = explicit if (explicit in cls._TTS_PROVIDERS or is_custom_id) else ""
        suggested = ""
        if not ui_provider:
            for pid in cls._TTS_PROVIDERS:
                meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
                key_field = meta.get("api_key_field")
                if key_field and cls._is_real_key(local_config.get(key_field, "")):
                    suggested = pid
                    break
        providers = list(cls._TTS_PROVIDERS)
        custom_cards = cls._custom_provider_cards(local_config)
        if custom_cards:
            providers.extend(c["id"] for c in custom_cards)
        return {
            "editable": True,
            "current_provider": ui_provider,
            "suggested_provider": suggested,
            "current_model": (local_config.get("text_to_voice_model") or "") if ui_provider else "",
            "current_voice": (local_config.get("tts_voice_id") or "") if ui_provider else "",
            "providers": providers,
            "provider_models": cls._TTS_PROVIDER_MODELS,
            "provider_voices": cls._TTS_PROVIDER_VOICES,
            "reply_mode": cls._tts_reply_mode(local_config),
        }

    @staticmethod
    def _tts_reply_mode(local_config: dict) -> str:
        if local_config.get("always_reply_voice", False):
            return "always"
        if local_config.get("voice_reply_voice", False):
            return "voice_if_voice"
        return "off"

    @classmethod
    def _embedding_capability(cls, local_config: dict) -> dict:
        # Embedding is "pick or empty" — runtime's legacy openai/linkai
        # fallback is a safety net, not a UX-visible auto mode.
        # `suggested_provider` is a UI-only hint (NOT persisted) that
        # preselects the dropdown to whichever configured vendor we'd
        # recommend, so users don't have to expand the menu to find it.
        explicit = (local_config.get("embedding_provider") or "").strip().lower()
        suggested = ""
        if not explicit:
            for pid in cls._EMBEDDING_PROVIDERS:
                if pid == "custom":
                    continue
                meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
                key_field = meta.get("api_key_field")
                if key_field and cls._is_real_key(local_config.get(key_field, "")):
                    suggested = pid
                    break
            if not suggested:
                custom_cards = cls._custom_provider_cards(local_config)
                if custom_cards:
                    suggested = custom_cards[0]["id"]

        # Build provider list: built-in providers + expanded custom:<id> entries
        # Same pattern as _chat_capability — each user-created custom provider
        # gets its own dropdown entry showing the user-chosen name.
        providers = []
        custom_cards = cls._custom_provider_cards(local_config)
        for pid in cls._EMBEDDING_PROVIDERS:
            if pid == "custom":
                if custom_cards:
                    providers.extend(c["id"] for c in custom_cards)
                # No custom providers configured — skip the bare "custom" entry
                # since the runtime cannot resolve its credentials.
            else:
                providers.append(pid)

        return {
            "editable": True,
            "current_provider": explicit,
            "suggested_provider": suggested,
            "current_model": local_config.get("embedding_model", "") or "",
            "current_dim": int(local_config.get("embedding_dimensions") or 0) or None,
            "providers": providers,
            "provider_models": cls._EMBEDDING_PROVIDER_MODELS,
        }

    # Auto-fallback order for image generation. Mirrors the global priority
    # used inside skills/image-generation/scripts/generate.py
    # (`_DEFAULT_PROVIDER_ORDER`): OpenAI → Gemini → Seedream(Ark/doubao) →
    # Qwen(dashscope) → MiniMax → LinkAI. Each entry maps the
    # provider-card id to the script's per-provider DEFAULT_MODEL so the
    # hint matches what the runtime would actually request.
    _IMAGE_AUTO_ORDER = [
        ("openai",    "gpt-image-2"),
        ("gemini",    "gemini-3.1-flash-image-preview"),  # nano-banana-2
        ("doubao",    "seedream-5.0-lite"),
        ("dashscope", "qwen-image-2.0"),
        ("minimax",   "image-01"),
        ("linkai",    "gpt-image-2"),
    ]

    @classmethod
    def _predict_image_auto(cls, local_config: dict) -> dict:
        """Predict which provider/model the image-generation skill will hit
        when no SKILL_IMAGE_GENERATION_MODEL override is set. Mirrors
        skills/image-generation/scripts/generate.py::_build_providers so
        the UI hint matches reality. Chat-only providers (DeepSeek etc.)
        are absent by design — image generation never falls back to a chat
        bot regardless of the main model.

        When use_linkai is enabled the hint is suppressed entirely — LinkAI
        proxies to whichever backend it deems appropriate and surfacing
        "LinkAI" alone tells the user nothing actionable."""
        use_linkai_flag = bool(local_config.get("use_linkai", False))
        linkai_configured = cls._is_real_key(local_config.get("linkai_api_key", ""))
        if use_linkai_flag and linkai_configured:
            return {"provider": "", "model": ""}

        for pid, default_model in cls._IMAGE_AUTO_ORDER:
            meta = ConfigHandler.PROVIDER_MODELS.get(pid) or {}
            key_field = meta.get("api_key_field")
            if not key_field:
                continue
            if cls._is_real_key(local_config.get(key_field, "")):
                return {"provider": pid, "model": default_model}
        return {"provider": "", "model": ""}

    @classmethod
    def _image_capability(cls, local_config: dict) -> dict:
        """Image generation. Source of truth: config["skills"]["image-generation"]["model"]
        (mirrors the per-skill config schema documented in skills/image-generation).
        The runtime resolver in skills/image-generation/scripts/generate.py
        reads this via the SKILL_IMAGE_GENERATION_MODEL env var that the
        agent_initializer syncs at startup; provider is inferred from the
        model name prefix, mirroring vision.py's design.

        ``skill`` (singular) is still tolerated as a legacy fallback —
        config.load_config() folds it into ``skills`` at startup.
        """
        skills_node = local_config.get("skills") or local_config.get("skill") or {}
        if not isinstance(skills_node, dict):
            skills_node = {}
        img_node = skills_node.get("image-generation") or {}
        if not isinstance(img_node, dict):
            img_node = {}
        explicit_model = (img_node.get("model") or "").strip()
        explicit_provider = (img_node.get("provider") or "").strip()

        providers = []
        custom_cards = cls._custom_provider_cards(local_config)
        for provider_id in cls._IMAGE_PROVIDER_MODELS:
            if provider_id == "custom":
                providers.extend(
                    card["id"] for card in custom_cards
                )
            else:
                providers.append(provider_id)

        # Provider resolution priority:
        #   1. Explicit `skills.image-generation.provider` (persisted via UI;
        #      supports custom model names that prefix-inference can't catch).
        #   2. Scan per-provider model catalog by model name.
        # Empty provider keeps the dropdown on "auto" when we can't tell.
        inferred_provider = ""
        if explicit_provider and explicit_provider in providers:
            inferred_provider = explicit_provider
        elif explicit_model:
            for pid, models in cls._IMAGE_PROVIDER_MODELS.items():
                for entry in models:
                    val = entry if isinstance(entry, str) else (entry.get("value") or "")
                    if val == explicit_model:
                        inferred_provider = pid
                        break
                if inferred_provider:
                    break

        # In auto mode the hint should reflect what generate.py will actually
        # dispatch to — surface that prediction via fallback_* so the UI
        # never claims a chat-only bot (e.g. minimax/MiniMax-M2.7) "would
        # generate the image", which is impossible.
        predicted = cls._predict_image_auto(local_config)

        return {
            "editable": True,
            "strategy": "specified" if explicit_model else "auto",
            "current_provider": inferred_provider,
            "current_model": explicit_model,
            "fallback_provider": predicted["provider"],
            "fallback_model": predicted["model"],
            "providers": providers,
            "provider_models": cls._IMAGE_PROVIDER_MODELS,
            "runtime_active": True,
        }

    # Canonical search provider order. Mirrors PROVIDER_ORDER in
    # agent/tools/web_search/web_search.py — keep them in sync.
    _SEARCH_PROVIDERS = ("bocha", "qianfan", "zhipu", "linkai", "anysearch", "serply")

    _SEARCH_PROVIDER_LABELS = {
        "bocha":   {"zh": "博查", "en": "Bocha"},
        "zhipu":   {"zh": "智谱", "en": "GLM"},
        "qianfan": {"zh": "百度千帆", "en": "ERNIE"},
        "linkai":  {"zh": "LinkAI", "en": "LinkAI"},
        "anysearch": {"zh": "AnySearch", "en": "AnySearch"},
        "serply":  {"zh": "Serply", "en": "Serply"},
    }

    @classmethod
    def _search_provider_key(cls, provider: str, local_config: dict) -> str:
        """Resolve the (raw) key for a given search provider."""
        if provider == "bocha":
            tools_cfg = local_config.get("tools") or {}
            block = tools_cfg.get("web_search") or {} if isinstance(tools_cfg, dict) else {}
            return (block.get("bocha_api_key") if isinstance(block, dict) else "") or os.environ.get("BOCHA_API_KEY", "")
        if provider == "zhipu":
            return local_config.get("zhipu_ai_api_key") or os.environ.get("ZHIPUAI_API_KEY", "")
        if provider == "qianfan":
            return local_config.get("qianfan_api_key") or os.environ.get("QIANFAN_API_KEY", "")
        if provider == "linkai":
            return local_config.get("linkai_api_key") or os.environ.get("LINKAI_API_KEY", "")
        if provider == "anysearch":
            tools_cfg = local_config.get("tools") or {}
            block = tools_cfg.get("web_search") or {} if isinstance(tools_cfg, dict) else {}
            return (block.get("anysearch_api_key") if isinstance(block, dict) else "") or os.environ.get(
                "ANYSEARCH_API_KEY", "")
        if provider == "serply":
            tools_cfg = local_config.get("tools") or {}
            block = tools_cfg.get("web_search") or {} if isinstance(tools_cfg, dict) else {}
            return (block.get("serply_api_key") if isinstance(block, dict) else "") or os.environ.get(
                "SERPLY_API_KEY", "")
        return ""

    @classmethod
    def _search_capability(cls, local_config: dict) -> dict:
        """Search is editable: pick auto (default) or pin a specific backend.
        Providers reuse model-vendor keys (zhipu/qianfan/linkai) so they show
        up as configured once the user adds those vendors; bocha keeps its
        own key under tools.web_search."""
        tools_cfg = local_config.get("tools") or {}
        ws_cfg = tools_cfg.get("web_search") or {} if isinstance(tools_cfg, dict) else {}
        if not isinstance(ws_cfg, dict):
            ws_cfg = {}

        providers = []
        configured_ids = []
        for pid in cls._SEARCH_PROVIDERS:
            ok = cls._is_real_key(cls._search_provider_key(pid, local_config))
            raw_key = cls._search_provider_key(pid, local_config) if ok else ""
            providers.append({
                "id": pid,
                "label": cls._SEARCH_PROVIDER_LABELS.get(pid, pid),
                "configured": ok,
                # bocha owns its key under tools.web_search; the other three
                # piggy-back on a model-vendor credential. Frontend uses
                # this hint to decide which credential editor to surface.
                "needs_dedicated_key": pid in ("bocha", "anysearch", "serply"),
                "api_key_masked": ConfigHandler._mask_key(raw_key) if raw_key else "",
            })
            if ok:
                configured_ids.append(pid)

        strategy = (ws_cfg.get("strategy") or "auto").strip().lower()
        if strategy not in ("auto", "fixed"):
            strategy = "auto"
        fixed_provider = (ws_cfg.get("provider") or "").strip().lower()
        if fixed_provider and fixed_provider not in configured_ids:
            fixed_provider = ""

        # current_provider drives the chip in the header — show the actually
        # active backend (pinned or first auto-picked).
        if strategy == "fixed" and fixed_provider:
            current = fixed_provider
        else:
            current = configured_ids[0] if configured_ids else ""

        return {
            "editable": True,
            "strategy": strategy,
            "providers": providers,
            "configured_providers": configured_ids,
            "current_provider": current,
            "fixed_provider": fixed_provider,
            "available": bool(current),
        }

    @classmethod
    def _capabilities(cls, local_config: dict) -> dict:
        return {
            "chat":      cls._chat_capability(local_config),
            "chat_fallback": cls._chat_fallback_capability(local_config),
            "vision":    cls._vision_capability(local_config),
            "asr":       cls._asr_capability(local_config),
            "tts":       cls._tts_capability(local_config),
            "embedding": cls._embedding_capability(local_config),
            "image":     cls._image_capability(local_config),
            "search":    cls._search_capability(local_config),
        }

    def GET(self):
        _require_platform_console()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            local_config = conf()
            return json.dumps({
                "status": "success",
                "providers": self._provider_overview(),
                "capabilities": self._capabilities(local_config),
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[ModelsHandler] GET failed: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        _require_platform_console()
        web.header("Content-Type", "application/json; charset=utf-8")
        try:
            data = json.loads(web.data() or b"{}")
            action = data.get("action") or ""
            if action == "set_provider":
                return self._handle_set_provider(data)
            if action == "delete_provider":
                return self._handle_delete_provider(data)
            if action == "set_custom_provider":
                return self._handle_set_custom_provider(data)
            if action == "delete_custom_provider":
                return self._handle_delete_custom_provider(data)
            if action == "set_active_custom_provider":
                return self._handle_set_active_custom_provider(data)
            if action == "set_capability":
                return self._handle_set_capability(data)
            if action == "set_voice_reply_mode":
                return self._handle_set_voice_reply_mode(data)
            if action == "set_search_credential":
                return self._handle_set_search_credential(data)
            return json.dumps({"status": "error", "message": f"unknown action: {action!r}"})
        except Exception as e:
            logger.error(f"[ModelsHandler] POST failed: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def _handle_set_provider(self, data: dict) -> str:
        provider_id = (data.get("provider_id") or "").strip()
        meta = ConfigHandler.PROVIDER_MODELS.get(provider_id)
        if not meta:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        # api_key absent / empty / null => leave the existing key untouched
        # (used by the "edit only base url" flow). To clear the key, callers
        # must use action=delete_provider explicitly.
        api_key_raw = data.get("api_key")
        api_key = api_key_raw.strip() if isinstance(api_key_raw, str) else ""

        # api_base presence is significant: an explicit "" means "reset to
        # default", whereas a missing key means "no change".
        api_base_present = "api_base" in data
        api_base = (data.get("api_base") or "").strip() if api_base_present else None

        applied = {}
        local_config = conf()
        file_cfg = self._read_file_config()

        key_field = meta.get("api_key_field")
        if key_field and api_key:
            local_config[key_field] = api_key
            file_cfg[key_field] = api_key
            applied[key_field] = True
        base_field = meta.get("api_base_key")
        if base_field and api_base_present:
            local_config[base_field] = api_base
            file_cfg[base_field] = api_base
            applied[base_field] = True

        if not applied:
            # Nothing actually changed (e.g. user opened the modal and hit
            # save without editing). Treat as a successful no-op so the
            # frontend can show "Saved" instead of surfacing an error.
            return json.dumps({"status": "success", "provider": provider_id, "noop": True})

        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] provider {provider_id} updated: {sorted(applied.keys())}")

        # Vendor credentials affect bot routing for any capability that uses
        # them; safest to reset Bridge so the next request rebuilds bots.
        self._reset_bridge()
        return json.dumps({"status": "success", "provider": provider_id})

    def _handle_delete_provider(self, data: dict) -> str:
        provider_id = (data.get("provider_id") or "").strip()
        meta = ConfigHandler.PROVIDER_MODELS.get(provider_id)
        if not meta:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        local_config = conf()
        file_cfg = self._read_file_config()

        cleared = []
        for field_name in (meta.get("api_key_field"), meta.get("api_base_key")):
            if not field_name:
                continue
            # Always write the key — even if it was absent before — so the
            # in-memory conf() reflects the cleared state without needing a
            # restart. (`in local_config` was too strict: provider keys that
            # were ever set then deleted manually wouldn't get reset.)
            local_config[field_name] = ""
            file_cfg[field_name] = ""
            cleared.append(field_name)

        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] provider {provider_id} cleared: {cleared}")
        self._reset_bridge()
        return json.dumps({"status": "success", "provider": provider_id, "cleared": cleared})

    # ------------------------------------------------------------------
    # Multiple custom (OpenAI-compatible) providers
    # ------------------------------------------------------------------
    # These actions manage the ``custom_providers`` list.  Activation is done
    # by setting ``bot_type`` to ``"custom:<id>"``.  There is no separate
    # ``custom_active_provider`` field — a single source of truth.

    @staticmethod
    def _normalize_custom_providers(raw) -> List[dict]:
        """Return a clean list of provider dicts (drops malformed entries)."""
        if not isinstance(raw, list):
            return []
        out = []
        for p in raw:
            if isinstance(p, dict) and (p.get("id") or "").strip():
                out.append(p)
        return out

    def _persist_custom_providers(self, providers: List[dict], bot_type=None) -> None:
        """Write the providers list to both in-memory conf and the on-disk
        config, then reset the bridge so bots rebuild.

        If ``bot_type`` is given, also update ``bot_type``.  When activating a
        provider (bot_type is ``custom:<id>``), also write the provider's
        ``model`` into the global ``model`` field so that all paths (chat,
        agent, vision) automatically use the correct model."""
        from models.custom_provider import parse_custom_bot_type

        local_config = conf()
        file_cfg = self._read_file_config()
        local_config["custom_providers"] = providers
        file_cfg["custom_providers"] = providers
        if bot_type is not None:
            local_config["bot_type"] = bot_type
            file_cfg["bot_type"] = bot_type
            # Sync the provider's model into the global model field.
            _, pid = parse_custom_bot_type(bot_type)
            if pid:
                provider = next((p for p in providers if p.get("id") == pid), None)
                if provider and provider.get("model"):
                    local_config["model"] = provider["model"]
                    file_cfg["model"] = provider["model"]

        skills = local_config.get("skills") or {}
        image_config = (
            skills.get("image-generation")
            if isinstance(skills, dict)
            else {}
        )
        image_provider = (
            image_config.get("provider", "")
            if isinstance(image_config, dict)
            else ""
        )
        if image_provider.startswith("custom:"):
            image_provider_id = image_provider[len("custom:"):]
            if not any(
                provider.get("id") == image_provider_id
                for provider in providers
            ):
                for target in (local_config, file_cfg):
                    self._set_nested_namespace_value(
                        target,
                        "skills",
                        "image-generation",
                        "provider",
                        "",
                    )
                    self._set_nested_namespace_value(
                        target,
                        "skills",
                        "image-generation",
                        "model",
                        "",
                    )
                os.environ.pop(
                    "SKILL_IMAGE_GENERATION_PROVIDER",
                    None,
                )
                os.environ.pop(
                    "SKILL_IMAGE_GENERATION_MODEL",
                    None,
                )
        sync_image_generation_custom_provider_env(
            local_config,
            overwrite=True,
        )
        self._write_file_config(file_cfg)
        self._reset_bridge()

    def _handle_set_custom_provider(self, data: dict) -> str:
        """Add a new custom provider or update an existing one.

        Payload::

            {
              "action": "set_custom_provider",
              "id": "3f2a9c1b",             # required for edit; omit for create
              "name": "my-provider",         # required, display label
              "api_base": "https://...",     # required when creating
              "api_key": "sk-...",           # optional on edit (keep existing)
              "model": "model-name",         # optional default model
              "make_active": true            # optional, also activate it
            }
        """
        from models.custom_provider import generate_provider_id, parse_custom_bot_type

        name = (data.get("name") or "").strip()
        if not name:
            return json.dumps({"status": "error", "message": "name is required"})

        provider_id = (data.get("id") or "").strip()
        api_base = (data.get("api_base") or "").strip()
        # api_key omitted/empty on edit => keep the existing one.
        api_key_raw = data.get("api_key")
        api_key = api_key_raw.strip() if isinstance(api_key_raw, str) else ""
        model = (data.get("model") or "").strip()
        make_active = bool(data.get("make_active"))

        local_config = conf()
        providers = self._normalize_custom_providers(local_config.get("custom_providers"))

        existing = next((p for p in providers if p.get("id") == provider_id), None) if provider_id else None
        if existing is None:
            # Creating a new provider — api_base is mandatory.
            if not api_base:
                return json.dumps({"status": "error", "message": "api_base is required"})
            provider_id = generate_provider_id()
            entry = {"id": provider_id, "name": name, "api_key": api_key, "api_base": api_base}
            if model:
                entry["model"] = model
            providers.append(entry)
            created = True
        else:
            existing["name"] = name
            if api_base:
                existing["api_base"] = api_base
            # The API key is optional for custom providers. Distinguish "field
            # omitted => keep existing" from "explicit empty => clear it" by
            # presence of the key, mirroring the model handling below. A masked,
            # untouched value is omitted by the UI, so it never reaches here.
            if "api_key" in data:
                if api_key:
                    existing["api_key"] = api_key
                else:
                    existing.pop("api_key", None)
            # Only touch model when explicitly provided in the payload; an
            # explicit empty string clears it, a missing key keeps it (the
            # UI modal no longer sends model, so manual config survives edits).
            if "model" in data:
                if model:
                    existing["model"] = model
                else:
                    existing.pop("model", None)
            created = False

        # Decide bot_type — only switch when explicitly requested.
        new_bot_type = None
        if make_active:
            new_bot_type = f"custom:{provider_id}"

        self._persist_custom_providers(providers, new_bot_type)
        logger.info(
            f"[ModelsHandler] custom provider {name!r} (id={provider_id}) "
            f"{'created' if created else 'updated'}"
        )
        return json.dumps({
            "status": "success",
            "id": provider_id,
            "name": name,
            "created": created,
        })

    def _handle_delete_custom_provider(self, data: dict) -> str:
        """Remove a custom provider by id."""
        from models.custom_provider import parse_custom_bot_type

        provider_id = (data.get("id") or "").strip()
        if not provider_id:
            return json.dumps({"status": "error", "message": "id is required"})

        local_config = conf()
        providers = self._normalize_custom_providers(local_config.get("custom_providers"))
        remaining = [p for p in providers if p.get("id") != provider_id]
        if len(remaining) == len(providers):
            return json.dumps({"status": "error", "message": f"unknown custom provider id: {provider_id}"})

        # If the deleted provider was active, fall back to the first remaining.
        _, current_active_id = parse_custom_bot_type(local_config.get("bot_type") or "")
        new_bot_type = None
        if current_active_id == provider_id:
            if remaining:
                new_bot_type = f"custom:{remaining[0]['id']}"
            else:
                new_bot_type = "custom"  # revert to legacy

        self._persist_custom_providers(remaining, new_bot_type)
        logger.info(f"[ModelsHandler] custom provider id={provider_id} deleted")
        return json.dumps({"status": "success", "id": provider_id})

    def _handle_set_active_custom_provider(self, data: dict) -> str:
        """Activate a custom provider by setting bot_type to 'custom:<id>'."""
        provider_id = (data.get("id") or "").strip()
        if not provider_id:
            return json.dumps({"status": "error", "message": "id is required"})

        local_config = conf()
        providers = self._normalize_custom_providers(local_config.get("custom_providers"))
        if not any(p.get("id") == provider_id for p in providers):
            return json.dumps({"status": "error", "message": f"unknown custom provider id: {provider_id}"})

        new_bot_type = f"custom:{provider_id}"
        self._persist_custom_providers(providers, new_bot_type)
        logger.info(f"[ModelsHandler] active custom provider set to id={provider_id}")
        return json.dumps({"status": "success", "active_id": provider_id})

    def _handle_set_capability(self, data: dict) -> str:
        capability = (data.get("capability") or "").strip()
        provider_id = (data.get("provider_id") or "").strip()
        model = (data.get("model") or "").strip()

        if capability == "chat":
            return self._set_chat(provider_id, model)
        if capability == "chat_fallback":
            return self._set_chat_fallback(
                provider_id,
                model,
                bool(data.get("enabled")),
                data.get("max_switches"),
            )
        if capability == "vision":
            return self._set_vision(provider_id, model)
        if capability == "asr":
            return self._set_asr(provider_id, model)
        if capability == "tts":
            return self._set_tts(provider_id, model, (data.get("voice") or "").strip())
        if capability == "embedding":
            return self._set_embedding(provider_id, model)
        if capability == "image":
            return self._set_image(provider_id, model)
        if capability == "search":
            return self._set_search(
                (data.get("strategy") or "").strip().lower(),
                (data.get("provider") or "").strip().lower(),
            )
        return json.dumps({"status": "error", "message": f"capability not editable: {capability}"})

    def _set_image(self, provider_id: str, model: str) -> str:
        # Source of truth: skills.image-generation.{provider, model}. The
        # provider field is persisted so users picking a custom model under
        # a specific vendor still get routed there — runtime falls back to
        # model-name prefix inference only when provider is empty.
        local_config = conf()
        if provider_id.startswith("custom:"):
            custom_id = provider_id[len("custom:"):]
            providers = self._normalize_custom_providers(
                local_config.get("custom_providers")
            )
            custom_provider = next(
                (
                    provider
                    for provider in providers
                    if provider.get("id") == custom_id
                ),
                None,
            )
            if custom_provider is None:
                return json.dumps({
                    "status": "error",
                    "message": (
                        "unknown custom provider id: {}".format(custom_id)
                    ),
                })
            if not model:
                model = custom_provider.get("model") or ""
        elif (
            provider_id
            and provider_id not in self._IMAGE_PROVIDER_MODELS
        ):
            return json.dumps({
                "status": "error",
                "message": "unknown image provider: {}".format(provider_id),
            })

        if provider_id and not model:
            return json.dumps({
                "status": "error",
                "message": (
                    "image model is required when a provider is selected"
                ),
            })

        file_cfg = self._read_file_config()

        self._set_nested_namespace_value(local_config, "skills", "image-generation", "model", model or "")
        self._set_nested_namespace_value(file_cfg, "skills", "image-generation", "model", model or "")
        self._set_nested_namespace_value(local_config, "skills", "image-generation", "provider", provider_id or "")
        self._set_nested_namespace_value(file_cfg, "skills", "image-generation", "provider", provider_id or "")
        self._drop_legacy_namespace(local_config, "skill", "skills", child="image-generation")
        self._drop_legacy_namespace(file_cfg, "skill", "skills", child="image-generation")

        self._write_file_config(file_cfg)

        # The skill subprocess reads SKILL_IMAGE_GENERATION_{MODEL,PROVIDER}
        # from env at startup; mirror the change so live edits apply without
        # restart.
        model_env = "SKILL_IMAGE_GENERATION_MODEL"
        provider_env = "SKILL_IMAGE_GENERATION_PROVIDER"
        if model:
            os.environ[model_env] = model
        else:
            os.environ.pop(model_env, None)
        if provider_id:
            os.environ[provider_env] = provider_id
        else:
            os.environ.pop(provider_env, None)
        sync_image_generation_custom_provider_env(
            local_config,
            overwrite=True,
        )

        logger.info(f"[ModelsHandler] image updated: provider={provider_id!r} model={model!r}")
        return json.dumps({
            "status": "success",
            "provider": provider_id,
            "model": model,
        })

    def _set_chat(self, provider_id: str, model: str) -> str:
        # Accept expanded custom provider ids ("custom:<id>") as well as the
        # built-in vendors, so the chat capability card and the custom
        # providers section behave consistently.
        custom_provider = None
        if provider_id.startswith("custom:"):
            from models.custom_provider import parse_custom_bot_type
            _, custom_id = parse_custom_bot_type(provider_id)
            providers = self._normalize_custom_providers(conf().get("custom_providers"))
            custom_provider = next((p for p in providers if p.get("id") == custom_id), None)
            if custom_provider is None:
                return json.dumps({"status": "error", "message": f"unknown custom provider id: {custom_id}"})
        elif provider_id and provider_id not in ConfigHandler.PROVIDER_MODELS:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        applied = {}
        local_config = conf()
        file_cfg = self._read_file_config()

        # Fall back to the custom provider's default model when none is given.
        if not model and custom_provider:
            model = custom_provider.get("model") or ""

        if provider_id:
            bot_type_value = "chatGPT" if provider_id == "openai" else provider_id
            local_config["bot_type"] = bot_type_value
            file_cfg["bot_type"] = bot_type_value
            applied["bot_type"] = bot_type_value
            use_linkai = (provider_id == "linkai")
            local_config["use_linkai"] = use_linkai
            file_cfg["use_linkai"] = use_linkai
            applied["use_linkai"] = use_linkai
        if model:
            local_config["model"] = model
            file_cfg["model"] = model
            applied["model"] = model

        if not applied:
            return json.dumps({"status": "success", "applied": {}, "noop": True})

        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] chat updated: {applied}")
        self._reset_bridge()
        return json.dumps({"status": "success", "applied": applied})

    def _set_chat_fallback(self, provider_id: str, model: str, enabled: bool,
                           max_switches=None) -> str:
        """Persist the backup chat model under ``chat_fallback``.

        Validation mirrors ``_set_chat`` (custom:<id> ids included), with two
        differences: the entry is opt-in via ``enabled``, and turning it on
        requires both a provider and a model so a half-configured fallback can
        never hijack a healthy primary model.
        """
        custom_provider = None
        if provider_id.startswith("custom:"):
            from models.custom_provider import parse_custom_bot_type
            _, custom_id = parse_custom_bot_type(provider_id)
            providers = self._normalize_custom_providers(conf().get("custom_providers"))
            custom_provider = next((p for p in providers if p.get("id") == custom_id), None)
            if custom_provider is None:
                return json.dumps({"status": "error", "message": f"unknown custom provider id: {custom_id}"})
        elif provider_id and provider_id not in ConfigHandler.PROVIDER_MODELS:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        # Fall back to the custom provider's default model when none is given.
        if not model and custom_provider:
            model = custom_provider.get("model") or ""

        # Enabling is all-or-nothing; disabling is always allowed (it is the
        # safe direction, and lets a user clear a broken entry).
        if enabled and (not provider_id or not model):
            return json.dumps({
                "status": "error",
                "message": "both a provider and a model are required to enable the fallback",
            })

        try:
            switches = int(max_switches) if max_switches is not None else 1
        except (TypeError, ValueError):
            switches = 1
        # At least one switch, or the fallback could never engage at all.
        switches = max(1, min(switches, 5))

        local_config = conf()
        file_cfg = self._read_file_config()
        payload = {
            "enabled": bool(enabled),
            "provider": provider_id or "",
            "model": model or "",
            "max_switches": switches,
        }
        # Written as a whole so a stale key from an older shape can't survive.
        local_config["chat_fallback"] = dict(payload)
        file_cfg["chat_fallback"] = dict(payload)
        self._write_file_config(file_cfg)

        logger.info(f"[ModelsHandler] chat fallback updated: {payload}")
        return json.dumps({"status": "success", "applied": payload})

    def _set_vision(self, provider_id: str, model: str) -> str:
        # Source of truth: tools.vision.{provider, model}. The provider field
        # is persisted so users picking a custom model under a specific vendor
        # still get routed there — runtime falls back to model-name prefix
        # inference only when provider is empty.
        # Validate provider_id — mirrors _set_chat / _set_embedding pattern.
        if provider_id.startswith("custom:"):
            from models.custom_provider import parse_custom_bot_type
            _, custom_id = parse_custom_bot_type(provider_id)
            providers = self._normalize_custom_providers(conf().get("custom_providers"))
            custom_provider = next((p for p in providers if p.get("id") == custom_id), None)
            if custom_provider is None:
                return json.dumps({"status": "error", "message": f"unknown custom provider id: {custom_id}"})
            if not model:
                model = custom_provider.get("model") or ""
        elif provider_id and provider_id not in {k for k in ModelsHandler._VISION_PROVIDER_MODELS if k != "custom"}:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        if provider_id and not model:
            return json.dumps({
                "status": "error",
                "message": "vision model is required when a provider is selected",
            })

        local_config = conf()
        file_cfg = self._read_file_config()
        self._set_nested_namespace_value(file_cfg, "tools", "vision", "model", model)
        self._set_nested_namespace_value(local_config, "tools", "vision", "model", model)
        self._set_nested_namespace_value(file_cfg, "tools", "vision", "provider", provider_id or "")
        self._set_nested_namespace_value(local_config, "tools", "vision", "provider", provider_id or "")
        self._drop_legacy_namespace(file_cfg, "tool", "tools", child="vision")
        self._drop_legacy_namespace(local_config, "tool", "tools", child="vision")

        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] vision updated: provider={provider_id!r} model={model!r}")
        return json.dumps({"status": "success", "provider": provider_id, "model": model})

    @staticmethod
    def _set_nested_namespace_value(cfg, top: str, name: str, key: str, value):
        """Set ``cfg[top][name][key] = value``, creating missing dicts."""
        bucket = cfg.get(top)
        if not isinstance(bucket, dict):
            bucket = {}
        node = bucket.get(name)
        if not isinstance(node, dict):
            node = {}
        node[key] = value
        bucket[name] = node
        cfg[top] = bucket

    @staticmethod
    def _drop_legacy_namespace(cfg, legacy: str, canonical: str, child: str) -> None:
        """Strip the deprecated singular key so config.json stays single-source."""
        legacy_section = cfg.get(legacy)
        if not isinstance(legacy_section, dict):
            return
        legacy_section.pop(child, None)
        if legacy_section:
            cfg[legacy] = legacy_section
        else:
            cfg.pop(legacy, None)

    def _handle_set_voice_reply_mode(self, data: dict) -> str:
        # UI picker (off / voice_if_voice / always) maps to the legacy
        # always_reply_voice + voice_reply_voice pair that chat_channel.py
        # reads, so all channels (web/feishu/wecom/...) share the routing.
        mode = (data.get("mode") or "").strip().lower()
        if mode not in ("off", "voice_if_voice", "always"):
            return json.dumps({"status": "error", "message": f"invalid mode: {mode!r}"})
        always = (mode == "always")
        if_voice = (mode == "voice_if_voice")
        local_config = conf()
        file_cfg = self._read_file_config()
        local_config["always_reply_voice"] = always
        local_config["voice_reply_voice"] = if_voice
        file_cfg["always_reply_voice"] = always
        file_cfg["voice_reply_voice"] = if_voice
        self._write_file_config(file_cfg)
        logger.info(
            f"[ModelsHandler] voice reply mode set: {mode!r} "
            f"(always_reply_voice={always}, voice_reply_voice={if_voice})"
        )
        return json.dumps({"status": "success", "mode": mode})

    def _set_simple(self, key: str, value: str) -> str:
        local_config = conf()
        file_cfg = self._read_file_config()
        local_config[key] = value
        file_cfg[key] = value
        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] {key} set: {value!r}")
        # Hot-swap the cached voice bot so the change takes effect immediately.
        if key in ("voice_to_text", "text_to_voice"):
            self._refresh_voice_routing()
        return json.dumps({"status": "success", key: value})

    def _set_asr(self, provider_id: str, model: str) -> str:
        local_config = conf()
        file_cfg = self._read_file_config()
        local_config["voice_to_text"] = provider_id
        file_cfg["voice_to_text"] = provider_id
        # Only overwrite the model when one is supplied. An empty model means
        # "keep whatever is configured" so switching provider from the console
        # never wipes a user's hand-set voice_to_text_model (runtime falls back
        # to the engine default via `or DEFAULT_ASR_MODEL` regardless).
        if model:
            local_config["voice_to_text_model"] = model
            file_cfg["voice_to_text_model"] = model
        self._write_file_config(file_cfg)
        logger.info(
            f"[ModelsHandler] asr updated: provider={provider_id!r} "
            f"model={model!r}"
        )
        self._refresh_voice_routing()
        return json.dumps({
            "status": "success",
            "provider": provider_id,
            "model": local_config.get("voice_to_text_model", ""),
        })

    def _set_tts(self, provider_id: str, model: str, voice: str = "") -> str:
        local_config = conf()
        file_cfg = self._read_file_config()
        local_config["text_to_voice"] = provider_id
        file_cfg["text_to_voice"] = provider_id
        local_config["text_to_voice_model"] = model
        file_cfg["text_to_voice_model"] = model
        local_config["tts_voice_id"] = voice
        file_cfg["tts_voice_id"] = voice
        self._write_file_config(file_cfg)
        logger.info(
            f"[ModelsHandler] tts updated: provider={provider_id!r} "
            f"model={model!r} voice={voice!r}"
        )
        self._refresh_voice_routing()
        return json.dumps({
            "status": "success",
            "provider": provider_id, "model": model, "voice": voice,
        })

    @staticmethod
    def _refresh_voice_routing() -> None:
        try:
            from bridge.bridge import Bridge
            Bridge().refresh_voice()
        except Exception as e:
            logger.warning(f"[ModelsHandler] Bridge voice refresh failed: {e}")

    def _set_embedding(self, provider_id: str, model: str) -> str:
        # Validate provider_id — mirrors _set_chat's validation pattern.
        if provider_id.startswith("custom:"):
            from models.custom_provider import parse_custom_bot_type
            _, custom_id = parse_custom_bot_type(provider_id)
            providers = self._normalize_custom_providers(conf().get("custom_providers"))
            custom_provider = next((p for p in providers if p.get("id") == custom_id), None)
            if custom_provider is None:
                return json.dumps({"status": "error", "message": f"unknown custom provider id: {custom_id}"})
            # Fall back to the custom provider's default model when none is given.
            if not model:
                model = custom_provider.get("model") or ""
        elif provider_id and provider_id not in {p for p in ModelsHandler._EMBEDDING_PROVIDERS if p != "custom"}:
            return json.dumps({"status": "error", "message": f"unknown provider: {provider_id}"})

        # A provider without a model leaves the runtime in a broken half-state,
        # so reject that explicitly instead of silently writing it through.
        if provider_id and not model:
            return json.dumps({
                "status": "error",
                "message": "embedding model is required when a provider is selected",
            })
        local_config = conf()
        file_cfg = self._read_file_config()
        local_config["embedding_provider"] = provider_id
        file_cfg["embedding_provider"] = provider_id
        local_config["embedding_model"] = model
        file_cfg["embedding_model"] = model
        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] embedding updated: provider={provider_id!r} model={model!r}")
        # The next /memory rebuild-index command hot-swaps the provider onto
        # the running MemoryManager (see plugins/cow_cli). The dim may have
        # changed, so the frontend prompts the user to rebuild.
        return json.dumps({"status": "success", "provider": provider_id, "model": model})

    def _set_search(self, strategy: str, provider: str) -> str:
        """Persist search routing under tools.web_search.{strategy,provider}.

        strategy 'auto'  -> provider field is cleared (auto picks at call time)
        strategy 'fixed' -> provider must be in the canonical list; runtime
                            silently falls back to auto if its key is missing.
        """
        if strategy not in ("auto", "fixed"):
            return json.dumps({"status": "error", "message": f"invalid strategy: {strategy!r}"})
        if strategy == "fixed":
            if provider not in self._SEARCH_PROVIDERS:
                return json.dumps({"status": "error", "message": f"unknown provider: {provider!r}"})
        else:
            provider = ""

        local_config = conf()
        file_cfg = self._read_file_config()
        self._set_nested_namespace_value(local_config, "tools", "web_search", "strategy", strategy)
        self._set_nested_namespace_value(file_cfg,     "tools", "web_search", "strategy", strategy)
        self._set_nested_namespace_value(local_config, "tools", "web_search", "provider", provider)
        self._set_nested_namespace_value(file_cfg,     "tools", "web_search", "provider", provider)
        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] search updated: strategy={strategy!r} provider={provider!r}")
        return json.dumps({"status": "success", "strategy": strategy, "provider": provider})

    def _handle_set_search_credential(self, data: dict) -> str:
        """Persist a dedicated search-provider key under tools.web_search.

        bocha, anysearch and serply own their keys here; zhipu/qianfan/linkai
        reuse model-vendor credentials and go through set_provider instead.
        """
        provider = (data.get("provider") or "bocha").strip().lower()
        if provider not in ("bocha", "anysearch", "serply"):
            return json.dumps({"status": "error", "message": f"unsupported search provider: {provider!r}"})
        key_field = f"{provider}_api_key"
        api_key = (data.get("api_key") or "").strip() if isinstance(data.get("api_key"), str) else ""
        local_config = conf()
        file_cfg = self._read_file_config()
        self._set_nested_namespace_value(local_config, "tools", "web_search", key_field, api_key)
        self._set_nested_namespace_value(file_cfg, "tools", "web_search", key_field, api_key)
        self._write_file_config(file_cfg)
        logger.info(f"[ModelsHandler] search credential set: {key_field}={'***' if api_key else ''}")
        return json.dumps({"status": "success", "provider": provider})

    @staticmethod
    def _reset_bridge() -> None:
        try:
            from bridge.bridge import Bridge
            Bridge().reset_bot()
            logger.info("[ModelsHandler] Bridge bot routing reset")
        except Exception as e:
            logger.warning(f"[ModelsHandler] Bridge reset failed: {e}")


class ChannelsHandler:
    """API for managing external channel configurations (feishu, dingtalk, etc).

    This is the *instance-level* (global) configuration, owned by the platform
    admin control plane. In database mode ``_require_platform_console`` resolves
    the session and rejects a non-platform-admin: the HTTP-method policy
    processor classifies the route ``platform`` but deliberately does not
    duplicate handler authorization, so without this guard opening the route
    would let any authenticated member read or repoint the global channel
    credentials. A tenant admin configures its own channels through
    ``/api/tenant/channels`` instead.
    """

    CHANNEL_DEFS = OrderedDict([
        ("weixin", {
            "label": {"zh": "微信", "en": "WeChat"},
            "icon": "fa-comment",
            "color": "emerald",
            "fields": [],
        }),
        ("feishu", {
            "label": {"zh": "飞书", "en": "Feishu"},
            "icon": "fa-paper-plane",
            "color": "blue",
            "fields": [
                {"key": "feishu_app_id", "label": "App ID", "type": "text"},
                {"key": "feishu_app_secret", "label": "App Secret", "type": "secret"},
            ],
        }),
        ("dingtalk", {
            "label": {"zh": "钉钉", "en": "DingTalk"},
            "icon": "fa-comments",
            "color": "blue",
            "fields": [
                {"key": "dingtalk_client_id", "label": "Client ID", "type": "text"},
                {"key": "dingtalk_client_secret", "label": "Client Secret", "type": "secret"},
            ],
        }),
        ("wecom_bot", {
            "label": {"zh": "企微智能机器人", "en": "WeCom Bot"},
            "icon": "fa-robot",
            "color": "emerald",
            "fields": [
                {"key": "wecom_bot_id", "label": "Bot ID", "type": "text"},
                {"key": "wecom_bot_secret", "label": "Secret", "type": "secret"},
            ],
        }),
        ("qq", {
            "label": {"zh": "QQ 机器人", "en": "QQ Bot"},
            "icon": "fa-comment",
            "color": "blue",
            "fields": [
                {"key": "qq_app_id", "label": "App ID", "type": "text"},
                {"key": "qq_app_secret", "label": "App Secret", "type": "secret"},
            ],
        }),
        ("wechatcom_app", {
            "label": {"zh": "企微自建应用", "en": "WeCom App"},
            "icon": "fa-building",
            "color": "emerald",
            "fields": [
                {"key": "wechatcom_corp_id", "label": "Corp ID", "type": "text"},
                {"key": "wechatcomapp_agent_id", "label": "Agent ID", "type": "text"},
                {"key": "wechatcomapp_secret", "label": "Secret", "type": "secret"},
                {"key": "wechatcomapp_token", "label": "Token", "type": "secret"},
                {"key": "wechatcomapp_aes_key", "label": "AES Key", "type": "secret"},
                {"key": "wechatcomapp_port", "label": "Port", "type": "number", "default": 9898},
            ],
        }),
        ("wechat_kf", {
            "label": {"zh": "微信客服", "en": "WeChat Customer Service"},
            "icon": "fa-headset",
            "color": "emerald",
            "fields": [
                {"key": "wechat_kf_corp_id", "label": "Corp ID", "type": "text"},
                {"key": "wechat_kf_secret", "label": "Secret", "type": "secret"},
                {"key": "wechat_kf_token", "label": "Token", "type": "secret"},
                {"key": "wechat_kf_aes_key", "label": "AES Key", "type": "secret"},
                {"key": "wechat_kf_port", "label": "Port", "type": "number", "default": 9888},
            ],
        }),
        ("wechatmp", {
            "label": {"zh": "公众号", "en": "WeChat MP"},
            "icon": "fa-comment-dots",
            "color": "emerald",
            "fields": [
                {"key": "wechatmp_app_id", "label": "App ID", "type": "text"},
                {"key": "wechatmp_app_secret", "label": "App Secret", "type": "secret"},
                {"key": "wechatmp_token", "label": "Token", "type": "secret"},
                {"key": "wechatmp_aes_key", "label": "AES Key", "type": "secret"},
                {"key": "wechatmp_port", "label": "Port", "type": "number", "default": 8080},
            ],
        }),
        ("telegram", {
            "label": {"zh": "Telegram", "en": "Telegram"},
            "icon": "fa-paper-plane",
            "color": "sky",
            "fields": [
                {"key": "telegram_token", "label": "Bot Token", "type": "secret"},
            ],
        }),
        ("slack", {
            "label": {"zh": "Slack", "en": "Slack"},
            "icon": "fa-hashtag",
            "color": "purple",
            "fields": [
                {"key": "slack_bot_token", "label": "Bot Token (xoxb-)", "type": "secret"},
                {"key": "slack_app_token", "label": "App Token (xapp-)", "type": "secret"},
            ],
        }),
        ("discord", {
            "label": {"zh": "Discord", "en": "Discord"},
            "icon": "fa-discord",
            "color": "indigo",
            "fields": [
                {"key": "discord_token", "label": "Bot Token", "type": "secret"},
            ],
        }),
    ])

    # Channels that lead the list in English. Everything defined above them
    # needs a mainland-China account, so an English user scrolling past those
    # to reach Telegram is scrolling past options they cannot use.
    EN_FIRST_CHANNELS = ("telegram", "discord", "slack")

    @classmethod
    def _ordered_channel_defs(cls, lang=None):
        """
        Channel definitions ordered for `lang`, defaulting to the configured UI
        language. Callers pass the language of the interface they are drawing:
        the desktop client keeps its own language in localStorage, so the global
        setting is not always what the user is looking at.
        """
        from common import i18n
        if (lang or i18n.get_language()) != i18n.EN:
            return list(cls.CHANNEL_DEFS.items())
        lead = [(k, cls.CHANNEL_DEFS[k]) for k in cls.EN_FIRST_CHANNELS if k in cls.CHANNEL_DEFS]
        rest = [(k, v) for k, v in cls.CHANNEL_DEFS.items() if k not in cls.EN_FIRST_CHANNELS]
        return lead + rest

    @staticmethod
    def _get_weixin_login_status() -> str:
        try:
            import sys
            app_module = sys.modules.get('__main__') or sys.modules.get('app')
            mgr = getattr(app_module, '_channel_mgr', None) if app_module else None
            if mgr:
                ch = mgr.get_channel("weixin")
                if ch and hasattr(ch, 'login_status'):
                    return ch.login_status
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _mask_secret(value: str) -> str:
        if not value or len(value) <= 8:
            return value
        return value[:4] + "*" * (len(value) - 8) + value[-4:]

    @staticmethod
    def _parse_channel_list(raw) -> list:
        if isinstance(raw, list):
            return [ch.strip() for ch in raw if ch.strip()]
        if isinstance(raw, str):
            return [ch.strip() for ch in raw.split(",") if ch.strip()]
        return []

    @classmethod
    def _active_channel_set(cls) -> set:
        return set(cls._parse_channel_list(conf().get("channel_type", "")))

    @staticmethod
    def _multi_agent_mode() -> bool:
        """True once the install has crossed into multi-Agent territory.

        The team.json file only exists after a second Agent (or channel
        instance) is created; until then everything lives in config.json and the
        channels view stays single-instance, exactly as a legacy install expects.
        """
        from agent import team
        return team.team_file(conf()).exists()

    @classmethod
    def _channel_instances_view(cls) -> list:
        """Per-instance channel cards for every multi-instance-ready type.

        Expands ``channel_instances`` into one card each, carrying instance_id,
        the bound agent_id and masked credentials, so the console can show and
        edit each bot independently. Covers all MULTI_INSTANCE_READY types
        (feishu, dingtalk, qq, telegram, slack, discord), not just feishu.
        """
        from common import i18n
        from channel.channel_instances import (
            resolve_channel_instances,
            MULTI_INSTANCE_READY,
        )
        from agent import team

        settings = team.resolve(conf())
        is_hant = i18n.get_language() == i18n.ZH_HANT
        out = []
        for inst in resolve_channel_instances(settings):
            if inst.channel_type not in MULTI_INSTANCE_READY:
                continue
            ch_def = cls.CHANNEL_DEFS.get(inst.channel_type)
            if not ch_def:
                continue
            fields_out = []
            for f in ch_def["fields"]:
                raw_val = (inst.credentials or {}).get(f["key"], "")
                if f["type"] == "secret" and raw_val:
                    display_val = cls._mask_secret(str(raw_val))
                else:
                    display_val = raw_val
                label_val = f["label"]
                if is_hant and isinstance(label_val, str):
                    label_val = i18n.to_traditional(label_val)
                elif is_hant and isinstance(label_val, dict):
                    label_val = label_val.copy()
                    label_val["zh-Hant"] = i18n.to_traditional(label_val.get("zh", ""))
                fields_out.append({
                    "key": f["key"],
                    "label": label_val,
                    "type": f["type"],
                    "value": display_val,
                    "default": f.get("default", ""),
                })
            label_val = ch_def["label"]
            if is_hant and isinstance(label_val, str):
                label_val = i18n.to_traditional(label_val)
            elif is_hant and isinstance(label_val, dict):
                label_val = label_val.copy()
                label_val["zh-Hant"] = i18n.to_traditional(label_val.get("zh", ""))
            out.append({
                "name": inst.channel_type,
                "instance_id": inst.instance_id,
                "channel_type": inst.channel_type,
                "agent_id": inst.agent_id or "",
                "members": list(inst.members or []),
                "label": label_val,
                "icon": ch_def["icon"],
                "color": ch_def["color"],
                "active": True,
                "fields": fields_out,
            })
        return out

    def GET(self):
        _require_platform_console()
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from common import i18n
            local_config = conf()
            active_channels = self._active_channel_set()
            channels = []
            is_hant = i18n.get_language() == i18n.ZH_HANT
            # The caller may be rendering in a different language than the
            # global setting; honour it when it sends one.
            req_lang = web.input().get("lang") or None
            if req_lang not in (i18n.EN, i18n.ZH, i18n.ZH_HANT):
                req_lang = None
            for ch_name, ch_def in self._ordered_channel_defs(req_lang):
                fields_out = []
                for f in ch_def["fields"]:
                    raw_val = local_config.get(f["key"], f.get("default", ""))
                    if f["type"] == "secret" and raw_val:
                        display_val = self._mask_secret(str(raw_val))
                    else:
                        display_val = raw_val
                    
                    label_val = f["label"]
                    if is_hant and isinstance(label_val, str):
                        label_val = i18n.to_traditional(label_val)
                    elif is_hant and isinstance(label_val, dict):
                        label_val = label_val.copy()
                        label_val["zh-Hant"] = i18n.to_traditional(label_val.get("zh", ""))

                    fields_out.append({
                        "key": f["key"],
                        "label": label_val,
                        "type": f["type"],
                        "value": display_val,
                        "default": f.get("default", ""),
                    })
                
                label_val = ch_def["label"]
                if is_hant and isinstance(label_val, str):
                    label_val = i18n.to_traditional(label_val)
                elif is_hant and isinstance(label_val, dict):
                    label_val = label_val.copy()
                    label_val["zh-Hant"] = i18n.to_traditional(label_val.get("zh", ""))

                ch_info = {
                    "name": ch_name,
                    "label": label_val,
                    "icon": ch_def["icon"],
                    "color": ch_def["color"],
                    "active": ch_name in active_channels,
                    "fields": fields_out,
                }
                if ch_name == "weixin" and ch_name in active_channels:
                    ch_info["login_status"] = self._get_weixin_login_status()
                channels.append(ch_info)

            from channel.channel_instances import MULTI_INSTANCE_READY
            multi_agent = self._multi_agent_mode()
            payload = {
                "status": "success",
                "channels": channels,
                "multi_agent": multi_agent,
                "multi_instance_types": sorted(MULTI_INSTANCE_READY),
            }
            # In multi-Agent mode the multi-instance-ready types (feishu) render
            # one card per channel_instances record instead of one per type.
            if multi_agent:
                payload["instances"] = self._channel_instances_view()
            return json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Channels API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        _require_platform_console()
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data())
            action = body.get("action")
            channel_name = body.get("channel")

            if not action or not channel_name:
                return json.dumps({"status": "error", "message": "action and channel required"})

            if channel_name not in self.CHANNEL_DEFS:
                return json.dumps({"status": "error", "message": f"unknown channel: {channel_name}"})

            # Multi-Agent + a multi-instance-ready type (feishu) manages each bot
            # as its own channel_instances record in team.json rather than the
            # legacy flat config.json path. instance_id empty on connect means
            # "create a new instance".
            from channel.channel_instances import MULTI_INSTANCE_READY
            instance_id = (body.get("instance_id") or "").strip()
            if self._multi_agent_mode() and channel_name in MULTI_INSTANCE_READY:
                if action == "save":
                    return self._handle_instance_save(channel_name, instance_id, body.get("config", {}))
                elif action == "connect":
                    return self._handle_instance_connect(channel_name, instance_id, body.get("config", {}))
                elif action == "disconnect":
                    return self._handle_instance_disconnect(channel_name, instance_id)
                else:
                    return json.dumps({"status": "error", "message": f"unknown action: {action}"})

            if action == "save":
                return self._handle_save(channel_name, body.get("config", {}))
            elif action == "connect":
                return self._handle_connect(channel_name, body.get("config", {}))
            elif action == "disconnect":
                return self._handle_disconnect(channel_name)
            else:
                return json.dumps({"status": "error", "message": f"unknown action: {action}"})
        except Exception as e:
            logger.error(f"[WebChannel] Channels POST error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def _handle_save(self, channel_name: str, updates: dict):
        ch_def = self.CHANNEL_DEFS[channel_name]
        valid_keys = {f["key"] for f in ch_def["fields"]}
        secret_keys = {f["key"] for f in ch_def["fields"] if f["type"] == "secret"}

        local_config = conf()
        applied = {}
        # Track which applied keys actually changed value, so a save that leaves
        # every credential untouched (e.g. the user re-saved the form, or only
        # an unrelated setting moved) does not needlessly tear down and
        # reconnect a live channel.
        changed = {}
        for key, value in updates.items():
            if key not in valid_keys:
                continue
            if key in secret_keys:
                if not value or (len(value) > 8 and "*" * 4 in value):
                    continue
            field_def = next((f for f in ch_def["fields"] if f["key"] == key), None)
            if field_def:
                if field_def["type"] == "number":
                    value = int(value)
                elif field_def["type"] == "bool":
                    value = bool(value)
            if local_config.get(key) != value:
                changed[key] = value
            local_config[key] = value
            applied[key] = value

        if not applied:
            return json.dumps({"status": "error", "message": "no valid fields to update"})

        config_path = os.path.join(get_data_root(), "config.json")
        file_cfg = _read_config_file_for_write()
        file_cfg.update(applied)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file_cfg, f, indent=4, ensure_ascii=False)

        logger.info(
            f"[WebChannel] Channel '{channel_name}' config saved: {list(applied.keys())}, "
            f"changed: {list(changed.keys())}"
        )

        # Only a real change to this channel's config warrants a restart. An
        # idempotent save must not interrupt a connected channel.
        should_restart = False
        active_channels = self._active_channel_set()
        if channel_name in active_channels and changed:
            should_restart = True
            try:
                import sys
                app_module = sys.modules.get('__main__') or sys.modules.get('app')
                mgr = getattr(app_module, '_channel_mgr', None) if app_module else None
                if mgr:
                    threading.Thread(
                        target=mgr.restart,
                        args=(channel_name,),
                        daemon=True,
                    ).start()
                    logger.info(f"[WebChannel] Channel '{channel_name}' restart triggered")
            except Exception as e:
                logger.warning(f"[WebChannel] Failed to restart channel '{channel_name}': {e}")

        return json.dumps({
            "status": "success",
            "applied": list(applied.keys()),
            "restarted": should_restart,
        }, ensure_ascii=False)

    def _handle_connect(self, channel_name: str, updates: dict):
        """Save config fields, add channel to channel_type, and start it."""
        ch_def = self.CHANNEL_DEFS[channel_name]
        valid_keys = {f["key"] for f in ch_def["fields"]}
        secret_keys = {f["key"] for f in ch_def["fields"] if f["type"] == "secret"}

        # Feishu connected via web console must use websocket (long connection) mode
        if channel_name == "feishu":
            updates.setdefault("feishu_event_mode", "websocket")
            valid_keys.add("feishu_event_mode")

        local_config = conf()
        applied = {}
        for key, value in updates.items():
            if key not in valid_keys:
                continue
            if key in secret_keys:
                if not value or (len(value) > 8 and "*" * 4 in value):
                    continue
            field_def = next((f for f in ch_def["fields"] if f["key"] == key), None)
            if field_def:
                if field_def["type"] == "number":
                    value = int(value)
                elif field_def["type"] == "bool":
                    value = bool(value)
            local_config[key] = value
            applied[key] = value

        existing = self._parse_channel_list(conf().get("channel_type", ""))
        if channel_name not in existing:
            existing.append(channel_name)
        new_channel_type = ",".join(existing)
        local_config["channel_type"] = new_channel_type

        config_path = os.path.join(get_data_root(), "config.json")
        file_cfg = _read_config_file_for_write()
        file_cfg.update(applied)
        file_cfg["channel_type"] = new_channel_type
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file_cfg, f, indent=4, ensure_ascii=False)

        logger.info(f"[WebChannel] Channel '{channel_name}' connecting, channel_type={new_channel_type}")

        # Feishu pulls its SDK bundle on first use; tell the UI so it can warn
        # about the one-time wait rather than reporting an instant success.
        downloading = False
        if channel_name == "feishu":
            try:
                from channel.feishu import lark_install
                downloading = lark_install.needs_download()
            except Exception as e:
                logger.warning(f"[WebChannel] Could not check Feishu SDK state: {e}")

        def _do_start():
            try:
                import sys
                app_module = sys.modules.get('__main__') or sys.modules.get('app')
                clear_fn = getattr(app_module, '_clear_singleton_cache', None) if app_module else None
                mgr = getattr(app_module, '_channel_mgr', None) if app_module else None
                if mgr is None:
                    logger.warning(f"[WebChannel] ChannelManager not available, cannot start '{channel_name}'")
                    return
                # Stop existing instance first if still running (e.g. re-connect without disconnect)
                existing_ch = mgr.get_channel(channel_name)
                if existing_ch is not None:
                    logger.info(f"[WebChannel] Stopping existing '{channel_name}' before reconnect...")
                    mgr.stop(channel_name)
                # Always wait for the remote service to release the old connection before
                # establishing a new one (DingTalk drops callbacks on duplicate connections)
                logger.info(f"[WebChannel] Waiting for '{channel_name}' old connection to close...")
                time.sleep(5)
                if clear_fn:
                    clear_fn(channel_name)
                logger.info(f"[WebChannel] Starting channel '{channel_name}'...")
                mgr.start([channel_name], first_start=False)
                logger.info(f"[WebChannel] Channel '{channel_name}' start completed")
            except Exception as e:
                logger.error(f"[WebChannel] Failed to start channel '{channel_name}': {e}",
                             exc_info=True)

        threading.Thread(target=_do_start, daemon=True).start()

        return json.dumps({
            "status": "success",
            "channel_type": new_channel_type,
            "downloading": downloading,
        }, ensure_ascii=False)

    def _handle_disconnect(self, channel_name: str):
        existing = self._parse_channel_list(conf().get("channel_type", ""))
        existing = [ch for ch in existing if ch != channel_name]
        new_channel_type = ",".join(existing)

        local_config = conf()
        local_config["channel_type"] = new_channel_type

        config_path = os.path.join(get_data_root(), "config.json")
        file_cfg = _read_config_file_for_write()
        file_cfg["channel_type"] = new_channel_type
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file_cfg, f, indent=4, ensure_ascii=False)

        def _do_stop():
            try:
                import sys
                app_module = sys.modules.get('__main__') or sys.modules.get('app')
                mgr = getattr(app_module, '_channel_mgr', None) if app_module else None
                clear_fn = getattr(app_module, '_clear_singleton_cache', None) if app_module else None
                if mgr:
                    mgr.stop(channel_name)
                else:
                    logger.warning(f"[WebChannel] ChannelManager not found, cannot stop '{channel_name}'")
                if clear_fn:
                    clear_fn(channel_name)
                logger.info(f"[WebChannel] Channel '{channel_name}' disconnected, "
                            f"channel_type={new_channel_type}")
            except Exception as e:
                logger.warning(f"[WebChannel] Failed to stop channel '{channel_name}': {e}",
                               exc_info=True)

        threading.Thread(target=_do_stop, daemon=True).start()

        return json.dumps({
            "status": "success",
            "channel_type": new_channel_type,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Multi-instance channel management (team.json driven, e.g. feishu)
    # ------------------------------------------------------------------
    @staticmethod
    def _channel_mgr():
        import sys
        app_module = sys.modules.get('__main__') or sys.modules.get('app')
        return getattr(app_module, '_channel_mgr', None) if app_module else None

    def _clean_credentials(self, channel_name: str, updates: dict) -> dict:
        """Keep only real, unmasked credential values for this channel type."""
        ch_def = self.CHANNEL_DEFS[channel_name]
        valid_keys = {f["key"] for f in ch_def["fields"]}
        secret_keys = {f["key"] for f in ch_def["fields"] if f["type"] == "secret"}
        creds = {}
        for key, value in (updates or {}).items():
            if key not in valid_keys:
                continue
            if key in secret_keys:
                # Skip empty or still-masked secrets so a save that leaves the
                # secret untouched does not overwrite it with the mask.
                if not value or (len(str(value)) > 8 and "*" * 4 in str(value)):
                    continue
            creds[key] = value
        return creds

    def _handle_instance_connect(self, channel_name: str, instance_id: str, updates: dict):
        """Create (empty id) or reconnect a channel instance, stored in team.json."""
        from channel.channel_instances import upsert_instance

        creds = self._clean_credentials(channel_name, updates)
        # Weixin scans its token during the QR flow (before the instance exists),
        # which lands in the global config. Fold it into this instance's own
        # credentials so the instance is self-contained: it stays logged in
        # across restarts and never depends on the transient global value.
        if channel_name == "weixin" and not creds.get("weixin_token"):
            token = conf().get("weixin_token", "")
            if token:
                creds["weixin_token"] = token
                base_url = conf().get("weixin_base_url", "")
                if base_url:
                    creds["weixin_base_url"] = base_url
                # Consume the transient QR token so the *next* Weixin instance
                # created (a different account) does not inherit this one's
                # token from the global config.
                conf()["weixin_token"] = ""
        inst = upsert_instance(
            conf(),
            channel_type=channel_name,
            instance_id=instance_id,
            credentials=creds,
        )

        downloading = False
        if channel_name == "feishu":
            try:
                from channel.feishu import lark_install
                downloading = lark_install.needs_download()
            except Exception as e:
                logger.warning(f"[WebChannel] Could not check Feishu SDK state: {e}")

        def _do_start():
            try:
                mgr = self._channel_mgr()
                if mgr is None:
                    logger.warning(
                        f"[WebChannel] ChannelManager unavailable, cannot start '{inst.instance_id}'"
                    )
                    return
                mgr.add_channel(inst)
                logger.info(f"[WebChannel] Channel instance '{inst.instance_id}' start completed")
            except Exception as e:
                logger.error(
                    f"[WebChannel] Failed to start channel instance '{inst.instance_id}': {e}",
                    exc_info=True,
                )

        threading.Thread(target=_do_start, daemon=True).start()
        return json.dumps({
            "status": "success",
            "instance_id": inst.instance_id,
            "downloading": downloading,
        }, ensure_ascii=False)

    def _handle_instance_save(self, channel_name: str, instance_id: str, updates: dict):
        """Update one instance's credentials in team.json and restart it."""
        from channel.channel_instances import get_instance, upsert_instance

        if not instance_id:
            return json.dumps({"status": "error", "message": "instance_id is required"})
        before = get_instance(conf(), instance_id)
        creds = self._clean_credentials(channel_name, updates)
        inst = upsert_instance(
            conf(),
            channel_type=channel_name,
            instance_id=instance_id,
            credentials=creds,
        )
        # Only restart when a credential actually changed, so re-saving an
        # unchanged form does not tear down a live connection.
        changed = not before or (dict(before.credentials or {}) != dict(inst.credentials or {}))
        if changed:
            def _do_restart():
                try:
                    mgr = self._channel_mgr()
                    if mgr is None:
                        return
                    mgr.restart(inst)
                except Exception as e:
                    logger.error(
                        f"[WebChannel] Failed to restart instance '{inst.instance_id}': {e}",
                        exc_info=True,
                    )
            threading.Thread(target=_do_restart, daemon=True).start()
        logger.info(
            f"[WebChannel] Channel instance '{inst.instance_id}' saved, "
            f"restart={'yes' if changed else 'no'}"
        )
        return json.dumps({"status": "success", "instance_id": inst.instance_id}, ensure_ascii=False)

    def _handle_instance_disconnect(self, channel_name: str, instance_id: str):
        """Remove one instance record from team.json and stop its channel."""
        from channel.channel_instances import remove_instance

        if not instance_id:
            return json.dumps({"status": "error", "message": "instance_id is required"})
        remove_instance(conf(), instance_id)

        def _do_stop():
            try:
                mgr = self._channel_mgr()
                if mgr is None:
                    return
                remover = getattr(mgr, "remove_channel", None)
                if callable(remover):
                    remover(instance_id)
                else:
                    mgr.stop(instance_id)
                logger.info(f"[WebChannel] Channel instance '{instance_id}' disconnected")
            except Exception as e:
                logger.warning(
                    f"[WebChannel] Failed to stop instance '{instance_id}': {e}",
                    exc_info=True,
                )

        threading.Thread(target=_do_stop, daemon=True).start()
        return json.dumps({"status": "success", "instance_id": instance_id}, ensure_ascii=False)


#: HTTP reason phrases for the refusals this handler raises. Kept next to the
#: handler so a refusal never has to guess a status line.
_HTTP_STATUS_TEXT = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    410: "Gone",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}

#: Refusal code -> HTTP status for the scan pipeline. `not_owner` is 404 for
#: "unknown handle" and "somebody else's handle" alike on purpose: the status is
#: part of the answer two initiators must not be able to tell apart.
_SCAN_ERROR_STATUS = {
    "not_owner": 404,
    "expired": 410,
    "invalid_transition": 409,
    "binding_mismatch": 409,
    "terminal": 409,
    "no_provider_result": 409,
    "not_confirmed": 409,
    "no_active_scan": 409,
    "authorization_refused": 403,
    "readback_refused": 403,
    "quota_refused": 403,
    "quota_not_wired": 503,
    "audit_failed": 503,
    "provider_result_crypto": 503,
    "state_not_shared": 503,
    "bad_provider_result": 502,
    "provider_unavailable": 502,
    "secret_in_receipt": 500,
}


class WeixinQrHandler:
    """微信扫码接入：按发起者绑定会话、一次性落库（任务 7.1-7.5）。

    GET  /api/weixin/qrlogin  → 为当前已验证发起者开启一次扫码接入
    POST /api/weixin/qrlogin  → ``poll`` / ``refresh`` / ``commit`` / ``cancel``

    旧实现把二维码和"这个 token 是谁扫到的"放在进程级 ``_qr_state`` 里，并把扫到
    的长期 token 写进 ``conf()`` 和共用凭据文件：两个人同时扫码会互相覆盖，任何
    已登录身份都能轮询并接管别人的会话，实例凭据也不由实例自己持有。现在每次接入
    都是 ``channel.web.scan_onboarding`` 的会话，绑定 (user, AuthSession, tenant,
    scope, owner, provider, target, purpose)：别人的句柄与不存在的句柄得到同一个
    拒绝，长期密钥由身份服务加密写入实例自己的凭据，响应只回句柄、二维码与非敏感
    事实。进程级 ``_qr_state`` 已删除，并且没有任何回退槽位。

    四个事实分开报告、互不冒充：扫码完成（``qr_status``）、创建已授权（``authorized``）、
    实例已保存（``saved``）、外部连接（``connection`` / ``connected``）。个人渠道在
    真实执行验收之前，保存成功仍然报告"已保存未连接"（任务 7.4）。

    作用域由服务端按已验证上下文决定（``_scope_for``）：能管理本租户渠道的身份默认
    接入租户公共实例，其他成员默认接入本人实例，两类身份都可显式选择个人实例。平台
    作用域在本 change 不开放——没有服务路径会创建不属于租户的渠道实例——因此显式
    拒绝，而不是给平台管理员伪造一个租户归属。owner/tenant/目标实例一律不接受客户端
    指定。
    """

    PROVIDER = "weixin"
    CHANNEL_TYPE = "weixin"
    PURPOSE = "create"

    @staticmethod
    def _qr_to_data_uri(data: str) -> str:
        """Generate a QR code as a PNG data URI."""
        try:
            import qrcode as qr_lib
            import io
            import base64
            qr = qr_lib.QRCode(error_correction=qr_lib.constants.ERROR_CORRECT_L, box_size=6, border=2)
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except ImportError:
            return ""

    @staticmethod
    def _get_running_channel():
        try:
            import sys
            app_module = sys.modules.get('__main__') or sys.modules.get('app')
            mgr = getattr(app_module, '_channel_mgr', None) if app_module else None
            if mgr:
                return mgr.get_channel("weixin")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def GET(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        try:
            if not _is_database_identity():
                # 单用户 legacy 部署没有 AuthSession 可绑定。那里的受支持路径是渠道
                # 自己的登录循环（启动渠道，它渲染自己的二维码），即下面的
                # ``source: "channel"`` 分支；进程级二维码槽位正是本次删掉的东西，
                # 所以这里刻意没有第二条路。
                return self._channel_owned_qr()
            from channel.web.auth_handlers import require_management_write
            require_management_write()
            params = web.input(scope="", base_url="")
            return self._start(self._context(), {
                "scope": getattr(params, "scope", ""),
                "base_url": getattr(params, "base_url", ""),
            })
        except web.HTTPError:
            raise
        except Exception as error:
            logger.error(f"[WebChannel] WeixinQr GET error: {error}", exc_info=True)
            self._fail_from(error)

    def POST(self):
        web.header("Content-Type", "application/json; charset=utf-8")
        web.header("Cache-Control", "no-store")
        try:
            if not _is_database_identity():
                self._fail(
                    "database identity is required to bind a scan to an initiator",
                    status=409, code="identity_required")
            try:
                body = json.loads(web.data() or b"{}")
            except (TypeError, ValueError):
                self._fail("invalid JSON body", status=400, code="bad_request")
            if not isinstance(body, dict):
                self._fail("a JSON object is required", status=400,
                           code="bad_request")
            action = str(body.get("action") or "poll").strip().lower()
            if action not in ("poll", "refresh", "commit", "cancel"):
                self._fail(f"unknown action: {action}", status=400,
                           code="unknown_action")
            # 这里的每个动作都会推进会话或提交实例，全部是写操作，因此全部通过既有
            # 的统一来源/CSRF 门（与其他管理面写入同一个 helper）。
            from channel.web.auth_handlers import require_management_write
            require_management_write()
            ctx = self._context()
            if action == "cancel":
                return self._cancel(ctx, body)
            if action == "poll":
                return self._poll(ctx, body)
            if action == "refresh":
                # 一次刷新就是一次新的扫码：旧会话只能自行过期，永远不会被别人的
                # 扫码结果填充，也不会被这个请求改成别的绑定。
                return self._start(ctx, body)
            return self._commit(ctx, body)
        except web.HTTPError:
            raise
        except Exception as error:
            logger.error(f"[WebChannel] WeixinQr POST error: {error}", exc_info=True)
            self._fail_from(error)

    # ------------------------------------------------------------------
    # 请求上下文
    # ------------------------------------------------------------------

    @staticmethod
    def _context():
        """本次请求的已验证上下文；租户必须显式选中。

        渠道实例一律属于某个租户（公共或成员个人），所以会话绑定里的 tenant 就是
        请求选中的租户。没有选中租户的身份拿不到任何会话——这正是"归属不由客户端
        参数或全局配置推断"的落点。
        """
        from channel.web.auth_handlers import _require_context
        return _require_context(require_tenant=True)

    @staticmethod
    def _auth_session_id() -> str:
        """本次请求所属的 AuthSession **行 id**，而不是 bearer token。

        绑定需要"这次扫码是在哪次登录里发起的"，这样同一用户的下一次登录不能回读上
        一次的结果。token 是 bearer 凭据，绝不能进入会话记录、回执或日志；行 id 是
        不透明且非秘密的，正好是绑定需要的那个标识。
        """
        from channel.web.auth_handlers import _get_service, _session_token
        token = _session_token()
        session = _get_service().verify_session(token) if token else None
        # ``verify_session`` answers ``{"user": ..., "session": <store row>}``:
        # the row is a mapping but not a dict, so it is indexed, not ``.get``-ed.
        row = (session or {}).get("session") if isinstance(session, dict) else None
        try:
            ident = str(row["id"] or "") if row is not None else ""
        except Exception:  # noqa: BLE001 - an unreadable session is not a session
            ident = ""
        if not ident:
            WeixinQrHandler._fail("authentication required", status=401,
                                  code="unauthorized")
        return ident

    @classmethod
    def _actor(cls, ctx):
        from channel.web import scan_onboarding as so
        return so.Actor(user_id=ctx.user_id, tenant_id=ctx.tenant_id or "",
                        auth_session_id=cls._auth_session_id())

    # ------------------------------------------------------------------
    # 作用域与目标
    # ------------------------------------------------------------------

    @staticmethod
    def _manages_tenant_channels(ctx) -> bool:
        """*ctx* 是否可以管理本租户的渠道实例。

        问已交付的服务（列表接口要求的正是这个权限），而不是在扫码侧复制一份规则，
        否则扫码可能给出一个随后必然被拒的作用域。
        """
        tenant = str(getattr(ctx, "tenant_id", "") or "")
        if not tenant:
            return False
        try:
            from auth.service import get_identity_service
            get_identity_service().list_tenant_channel_instances(
                actor_user_id=ctx.user_id, tenant_id=tenant)
            return True
        except Exception:  # noqa: BLE001 - 无权限就是 False，不是错误
            return False

    @classmethod
    def _scope_for(cls, ctx, body) -> str:
        """服务端决定本次接入的 scope（客户端只能在允许范围内选择）。

        默认按能力推导：能管理本租户渠道的身份接入租户公共实例，其他成员接入本人
        实例。显式选择只是收窄/切换到自己本来就有权的位置，不能指定 owner、tenant
        或目标实例。
        """
        from channel.web import scan_onboarding as so
        wanted = str((body or {}).get("scope") or "").strip().lower()
        if wanted in ("", "auto"):
            return (so.SCOPE_TENANT if cls._manages_tenant_channels(ctx)
                    else so.SCOPE_PERSONAL)
        if wanted in ("personal", "user"):
            return so.SCOPE_PERSONAL
        if wanted == "tenant":
            if not cls._manages_tenant_channels(ctx):
                cls._fail("managing this tenant's channels is required",
                          status=403, code="forbidden")
            return so.SCOPE_TENANT
        if wanted == "platform":
            # 平台管理员在租户内的公共实例就是"租户公共实例"，而一个不属于任何租户
            # 的渠道实例没有任何服务路径可以创建。因此这里显式拒绝，而不是替平台身份
            # 伪造一个租户归属。
            cls._fail(
                "a channel instance always belongs to a tenant; select the"
                " tenant it should serve",
                status=400, code="scope_not_supported")
        cls._fail(f"unknown scan scope: {wanted}", status=400, code="bad_scope")

    @classmethod
    def _target(cls, scope: str) -> str:
        return f"{cls.CHANNEL_TYPE}:{scope}"

    # ------------------------------------------------------------------
    # 会话读写
    # ------------------------------------------------------------------

    @classmethod
    def _session(cls, actor, body):
        """调用者**自己**的会话：优先按句柄，其次按发起者。

        POST /api/weixin/qrlogin 的 ``{action: "poll"}`` 早于句柄存在（控制台与桌面
        都这样发），这里不回退到任何全局槽位，而是把"最新的会话"限定在已验证发起者
        自己的范围内——别人的会话与不存在完全一样。
        """
        from channel.web import scan_onboarding as so
        handle = str((body or {}).get("handle") or "").strip()
        if handle:
            return so.require_session(
                handle, actor=actor, provider=cls.PROVIDER, purpose=cls.PURPOSE)
        latest = so.latest_session(
            actor=actor, provider=cls.PROVIDER, purpose=cls.PURPOSE)
        if latest is None:
            # 与"句柄不存在"和"句柄是别人的"完全同一个拒绝：这一句是存在性不可观测
            # 的关键，三条路径共用同一个 code 与同一段文本。
            raise so.ScanSessionError(so.NOT_OWNED_MESSAGE, code="not_owner")
        return latest

    # ------------------------------------------------------------------
    # 开启一次接入
    # ------------------------------------------------------------------

    def _start(self, ctx, body):
        from channel.web import scan_onboarding as so
        from channel import weixin_scan_adapter as adapter
        from auth.service import get_identity_service

        ready, reason = so.shared_state_ready()
        if not ready:
            # 会话登记、回执账本与一次性授权都是进程内状态：多 worker 部署会让每个
            # worker 各自持有一份同一扫码的视图。这时必须拒绝开放，而不是让每个
            # worker 各答一半。
            self._fail(
                "this deployment cannot serve a scan safely: " + reason,
                status=503, code="state_not_shared")
        scope = self._scope_for(ctx, body)
        actor = self._actor(ctx)
        # 端点只由本次请求指定（或厂商默认），绝不读 ``conf()`` 的全局微信配置：
        # 扫码新建的实例不能因为部署里遗留的全局值被指到别处。
        base_url = (str((body or {}).get("base_url") or "").strip()
                    or adapter.default_base_url())
        # 扫码没有名字表单：这里一次性解析出默认名（类型标签 + 首个空序号），随会话绑
        # 定。同一次扫码的每次提交都提交同一份内容，幂等键与回执读回才对得上。
        default_name = adapter.default_display_name(
            get_identity_service(), scope=scope, tenant_id=ctx.tenant_id or "",
            actor_user_id=ctx.user_id)
        session = so.start_session(
            provider=self.PROVIDER, scope=scope, purpose=self.PURPOSE,
            target=self._target(scope), owner_user_id=ctx.user_id,
            tenant_id=ctx.tenant_id or "", auth_session_id=actor.auth_session_id,
            default_display_name=default_name)
        try:
            answer = adapter.fetch_qr(base_url=base_url)
        except Exception as error:
            so.mark_status(session.handle, so.STATUS_FAILED, actor=actor,
                           error=type(error).__name__)
            adapter.log_refusal("qr fetch", error)
            self._fail("the provider did not hand out a QR code", status=502,
                       code="provider_unavailable")
        qrcode = str((answer or {}).get("qrcode") or "")
        if not qrcode:
            so.mark_status(session.handle, so.STATUS_FAILED, actor=actor,
                           error="no_qrcode")
            self._fail("the provider returned no QR code", status=502,
                       code="provider_unavailable")
        session = so.attach_qr(
            session.handle, qrcode=qrcode,
            qrcode_url=str((answer or {}).get("qrcode_img_content") or ""),
            base_url=base_url, actor=actor)
        return self._scan_answer(session, qr_status="waiting")

    def _poll(self, ctx, body):
        from channel.web import scan_onboarding as so
        from channel import weixin_scan_adapter as adapter

        actor = self._actor(ctx)
        session = self._session(actor, body)
        if session.status in (so.STATUS_CANCELLED, so.STATUS_EXPIRED,
                              so.STATUS_FAILED):
            return self._scan_answer(session, qr_status=session.status)
        if session.status in (so.STATUS_CONFIRMED, so.STATUS_COMMITTING) or \
                session.grant_opened:
            # 厂商已经确认过这次扫码：之后每次轮询都是同一次提交，按回执幂等作答。
            return self._commit(ctx, body)
        if not session.qrcode:
            self._fail("this scan has no QR to poll", status=409,
                       code="no_active_scan")
        base_url = adapter.resolve_base_url(
            session_base_url=session.base_url,
            requested=str((body or {}).get("base_url") or ""))
        try:
            answer = adapter.poll_qr(qrcode=session.qrcode, base_url=base_url)
        except Exception as error:
            adapter.log_refusal("qr poll", error)
            self._fail("the provider could not be reached", status=502,
                       code="provider_unavailable")
        status = str((answer or {}).get("status")
                     or adapter.VENDOR_WAIT).strip().lower()
        if status == adapter.VENDOR_CONFIRMED:
            return self._confirm_and_commit(ctx, body, session, actor, answer)
        if status == adapter.VENDOR_EXPIRED:
            return self._reissue_qr(session, actor, base_url)
        if status == adapter.VENDOR_SCANED:
            # 厂商的"已扫待确认"不是状态机的一个状态：会话仍是 pending，直到厂商
            # 确认才进入 confirmed。控制台据此显示"已扫码"。
            return self._scan_answer(session, qr_status="scaned")
        return self._scan_answer(session, qr_status="waiting")

    def _reissue_qr(self, session, actor, base_url):
        """同一会话内重新签发一张二维码（厂商二维码过期，会话仍有效）。"""
        from channel.web import scan_onboarding as so
        from channel import weixin_scan_adapter as adapter

        try:
            answer = adapter.fetch_qr(base_url=base_url)
        except Exception as error:
            adapter.log_refusal("qr reissue", error)
            self._fail("the provider could not be reached", status=502,
                       code="provider_unavailable")
        qrcode = str((answer or {}).get("qrcode") or "")
        if not qrcode:
            self._fail("the provider returned no QR code", status=502,
                       code="provider_unavailable")
        session = so.attach_qr(
            session.handle, qrcode=qrcode,
            qrcode_url=str((answer or {}).get("qrcode_img_content") or ""),
            base_url=base_url, actor=actor)
        return self._scan_answer(session, qr_status="expired")

    def _confirm_and_commit(self, ctx, body, session, actor, answer):
        """厂商确认：收窄并加密临时结果，然后走同一个提交通道。"""
        from channel.web import scan_onboarding as so
        from channel import weixin_scan_adapter as adapter

        if not session.provider_result_present:
            # 只保留渠道类型声明的凭据字段；厂商的 ilink_bot_id / ilink_user_id 与
            # 其余应答字段既不落库也不回显。缺 token 是拒绝，不是空凭据包。
            result = adapter.provider_result(answer)
            session = so.mark_status(session.handle, so.STATUS_CONFIRMED,
                                     actor=actor)
            session = so.attach_provider_result(
                session.handle, result=result,
                sensitive_keys=adapter.provider_secret_keys(),
                declared_keys=adapter.provider_result_keys(), actor=actor)
        return self._commit(ctx, body)

    def _commit(self, ctx, body):
        from channel.web import scan_onboarding as so
        from channel import weixin_scan_adapter as adapter
        from auth.service import get_identity_service

        actor = self._actor(ctx)
        session = self._session(actor, body)
        if session.status in (so.STATUS_CANCELLED, so.STATUS_EXPIRED,
                              so.STATUS_FAILED):
            # 终态会话直接按终态拒绝。让模块在下一步回答"还没被服务商确认"会把操作
            # 者引向继续轮询一个已经结束的会话。已提交的会话不走这条路：那是回读回执
            # 的入口。
            self._fail("this scan has already finished; start a new scan",
                       status=409, code="terminal")
        if not session.provider_result_present:
            self._fail("this scan has not been confirmed by the provider yet",
                       status=409, code="not_confirmed")
        credentials = so.provider_result(session.handle, actor=actor)
        # 授权在提交前才"开启"，且同一会话只开启一次：重试与响应丢失回读用的因此
        # 是同一个授权句柄、同一个幂等键，而不是第二次创建。
        ticket = so.open_grant(session.handle, actor=actor,
                               channel_type=self.CHANNEL_TYPE)
        # 开户动作让会话多了"已授权"这个事实，重读一次视图，别拿开启前的旧快照回答。
        session = so.get_session(session.handle, actor=actor)
        service = get_identity_service()
        result = so.commit_scan_binding(
            handle=session.handle, actor=actor, scan_ticket=ticket,
            channel_type=self.CHANNEL_TYPE,
            create_instance=adapter.create_instance_callable(
                service, scope=session.scope),
            # 客户端可以改名；没改名就用扫码开始时绑定的默认名（类型标签 + 空序号）。
            # 两者都随会话固定，重试提交的内容不会漂移。
            display_name=(str((body or {}).get("display_name") or "").strip()
                          or session.default_display_name),
            agent_id=str((body or {}).get("agent_id") or ""),
            credentials=credentials,
            provider=self.PROVIDER, scope=session.scope, purpose=self.PURPOSE,
            target=session.target,
            # 配额由身份服务自己的 BEGIN IMMEDIATE 事务执行（那里才有实例名额），
            # 这里不预留另一个指标；审计走已交付的审计 API。
            reserve_quota=None, quota_required=False,
            record_audit=adapter.audit_hook(service),
            readback_guard=adapter.readback_guard(
                service, resolve_context=self._context),
        )
        instance_id = str(result.get("instance_id") or "")
        if (instance_id and session.scope == so.SCOPE_TENANT
                and result.get("outcome") in ("committed", "resumed")):
            # 提交后按实例去重、可恢复的连接工作项。成员个人实例由已交付的个人创建
            # 路径负责 reconcile，所以这里不重复施加；回读（replayed）也不重启连接。
            adapter.apply_connection(instance_id)
        return self._committed_answer(session, result, service)

    def _cancel(self, ctx, body):
        from channel.web import scan_onboarding as so

        actor = self._actor(ctx)
        session = self._session(actor, body)
        if session.terminal:
            return self._scan_answer(session, qr_status=session.status)
        session = so.mark_status(session.handle, so.STATUS_CANCELLED, actor=actor)
        return self._scan_answer(session, qr_status=session.status)

    # ------------------------------------------------------------------
    # 应答
    # ------------------------------------------------------------------

    def _channel_owned_qr(self):
        """legacy 单用户部署：观测渠道自己正在展示的二维码。"""
        running_ch = self._get_running_channel()
        qr_url = getattr(running_ch, "_current_qr_url", "") if running_ch else ""
        if qr_url:
            return json.dumps({
                "status": "success",
                "qrcode_url": qr_url,
                "qr_image": self._qr_to_data_uri(qr_url),
                "source": "channel",
            }, ensure_ascii=False)
        self._fail(
            "database identity is required to start a scan session",
            status=409, code="identity_required")

    def _scan_answer(self, session, *, qr_status: str, **extra):
        payload = dict(session.payload())
        payload.update({
            "status": "success",
            "source": "session",
            "qr_status": qr_status,
            "qr_image": (self._qr_to_data_uri(session.qrcode_url)
                         if session.qrcode_url else ""),
            "expires_in": max(0, int(session.expires_at - time.time())),
            # 四个事实分开：扫码（qr_status）/ 已授权（authorized）/ 已保存
            # （saved）/ 已连接（connection, connected）。
            "authorized": bool(session.grant_opened),
            "saved": False,
            "connected": False,
            "connection": "unsaved",
        })
        payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)

    def _committed_answer(self, session, result, service):
        from channel import weixin_scan_adapter as adapter

        instance_id = str(result.get("instance_id") or "")
        state = (adapter.connection_state(service, instance_id=instance_id)
                 if instance_id
                 else {"state": "unsaved", "connected": False, "reason": ""})
        payload = dict(session.payload())
        payload.update({
            "status": "success",
            "source": "session",
            "qr_status": "confirmed",
            "qr_image": "",
            "expires_in": max(0, int(session.expires_at - time.time())),
            "authorized": bool(session.grant_opened),
            "authorization_consumed": bool(result.get("authorization_consumed")),
            "saved": bool(result.get("saved")),
            "instance_id": instance_id,
            "channel_type": self.CHANNEL_TYPE,
            "display_name": str(result.get("display_name") or ""),
            "agent_id": str(result.get("agent_id") or ""),
            "secret_present": bool(result.get("secret_present")),
            "outcome": str(result.get("outcome") or ""),
            "receipt_state": str(result.get("receipt_state") or ""),
            "connected": bool(state.get("connected")),
            "connection": str(state.get("state") or "unknown"),
            "connection_reason": str(state.get("reason") or ""),
        })
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 拒绝
    # ------------------------------------------------------------------

    @staticmethod
    def _fail(message: str, *, status: int, code: str):
        raise web.HTTPError(
            f"{status} {_HTTP_STATUS_TEXT.get(status, 'Error')}",
            {"Content-Type": "application/json; charset=utf-8"},
            json.dumps({"status": "error", "message": message, "code": code},
                       ensure_ascii=False))

    @classmethod
    def _fail_from(cls, error):
        """把一次拒绝映射为 HTTP 应答，绝不带出凭据原文。"""
        code = str(getattr(error, "code", "") or "").strip()
        status = getattr(error, "status", None)
        if not isinstance(status, int):
            status = _SCAN_ERROR_STATUS.get(code, 400)
        if not code:
            # 没有 code 的异常是内部错误：日志里有堆栈，应答里只给一句可读的话。
            cls._fail("the scan request failed", status=status,
                      code="scan_failed")
        cls._fail(str(error), status=status, code=code)


def _register_owner_scope() -> Tuple[str, str]:
    """拥有本次注册会话的 ``(user_id, tenant_id)``。

    database 模式下绑定到已验证的调用者及其选中的租户；legacy 模式是单用户
    部署，任何通过认证的调用者都是同一所有者。
    """
    if not _is_database_identity():
        return ("", "")
    from channel.web.auth_handlers import _require_context
    ctx = _require_context(require_tenant=True)
    return (ctx.user_id, ctx.tenant_id or "")


class FeishuRegisterHandler:
    """飞书智能体应用一键创建（OAuth 设备授权流，基于 lark.register_app SDK）。

    GET  /api/feishu/register   → 为当前发起者启动一次注册，返回不透明句柄与二维码；
                                   后台线程继续轮询飞书侧直到用户扫码授权。
    POST /api/feishu/register   → 携带句柄轮询该会话状态（downloading / pending /
                                   done / error / expired）。桌面版首次启用时要先下载
                                   飞书 SDK 包，此时二维码尚不存在，改由轮询补发。
                                   注册成功后不直接写 config，由前端再调
                                   /api/tenant/channels 走标准启用流程。

    会话按发起者绑定：句柄不透明，读取一律限定在 ``(user_id, tenant_id)`` 之内，
    因此同一部署里的其他身份既拿不到凭据，也无法探测该会话是否存在。同一身份
    再次发起会替换自己的上一个会话（避免两个 SDK 线程轮询同一次注册），但不会
    影响他人正在进行的会话。
    """

    #: handle -> 会话记录（{handle, owner_user_id, owner_tenant_id, status,
    #: created_at, cancel_event, url, expire_in, qr_image, app_id, app_secret,
    #: error}）。凭据在交付一次后即从记录中移除。
    _sessions: Dict[str, dict] = {}
    _lock = threading.Lock()
    #: 超过此时长的会话被回收；SDK 自身二维码有效期为 600s。
    _SESSION_TTL = 900.0
    #: GET 等待 SDK 产出二维码的上限；超时由前端转轮询。
    _QR_WAIT_SECONDS = 10.0

    @staticmethod
    def _qr_to_data_uri(data: str) -> str:
        """复用 WeixinQrHandler 的二维码渲染。"""
        return WeixinQrHandler._qr_to_data_uri(data)

    @classmethod
    def _reset_sessions(cls):
        """丢弃全部会话（测试用，也用于干净停机）。"""
        from auth.scan_authorization import _reset as _reset_scan_grants

        with cls._lock:
            for session in cls._sessions.values():
                cancel = session.get("cancel_event")
                if cancel is not None:
                    cancel.set()
            cls._sessions = {}
        # The grants are minted from these sessions and are only meaningful
        # while one exists, so dropping the sessions drops them too.
        _reset_scan_grants()

    @classmethod
    def _purge_expired_locked(cls):
        """回收超时会话。调用方必须已持有 ``_lock``。"""
        now = time.time()
        for handle, session in list(cls._sessions.items()):
            created = float(session.get("created_at") or 0)
            if now - created > cls._SESSION_TTL:
                cancel = session.get("cancel_event")
                if cancel is not None:
                    cancel.set()
                cls._sessions.pop(handle, None)

    @classmethod
    def _create_session(cls, owner_user_id: str, owner_tenant_id: str) -> str:
        """为某一身份开启新会话并返回其不透明句柄。

        同一身份已有的会话会被取代（取消并移除），以保证同一次注册只有一个 SDK
        线程在轮询；其他身份的会话不受影响。
        """
        handle = secrets.token_urlsafe(32)
        owner = (owner_user_id or "", owner_tenant_id or "")
        with cls._lock:
            cls._purge_expired_locked()
            for existing, session in list(cls._sessions.items()):
                if (session.get("owner_user_id"), session.get("owner_tenant_id")) == owner:
                    previous = session.get("cancel_event")
                    if previous is not None:
                        previous.set()
                    cls._sessions.pop(existing, None)
            cls._sessions[handle] = {
                "handle": handle,
                "owner_user_id": owner[0],
                "owner_tenant_id": owner[1],
                "status": "starting",
                "created_at": time.time(),
                "cancel_event": threading.Event(),
            }
        return handle

    @classmethod
    def _session_for(cls, handle: str, owner_user_id: str, owner_tenant_id: str):
        """仅当调用者拥有该句柄时返回会话记录，否则返回 None。

        他人的句柄与不存在的句柄给出同一答案：调用者不得探测其他身份的会话。
        """
        if not handle:
            return None
        owner = (owner_user_id or "", owner_tenant_id or "")
        with cls._lock:
            session = cls._sessions.get(handle)
            if session is None:
                return None
            if (session.get("owner_user_id"), session.get("owner_tenant_id")) != owner:
                return None
            return session

    @classmethod
    def _mint_scan_grant(cls, owner_user_id: str, owner_tenant_id: str) -> str:
        """The one-time grant that lets this scan's create skip the password.

        Kept here, next to the session that justifies it, so the two cannot
        drift: the grant is bound to the same owner the session is bound to.
        """
        from auth.scan_authorization import mint

        return mint(actor_user_id=owner_user_id, tenant_id=owner_tenant_id,
                    channel_type="feishu")

    @classmethod
    def _set_status(cls, handle: str, status: str, **fields) -> bool:
        """推进某会话的状态（由 SDK 工作线程调用）。

        会话已被取代或回收时返回 ``False`` 且不写入，因此迟到的 SDK 回调不会
        覆盖更新的会话。
        """
        with cls._lock:
            session = cls._sessions.get(handle)
            if session is None:
                return False
            session["status"] = status
            session.update(fields)
            return True

    @classmethod
    def _poll_payload(cls, handle: str, owner_user_id: str, owner_tenant_id: str) -> dict:
        """某一身份的轮询应答；成功时消费凭据。

        未知句柄、他人句柄与已消费的会话一律读作 ``expired``，三者不可区分。
        """
        owner = (owner_user_id or "", owner_tenant_id or "")
        with cls._lock:
            session = cls._sessions.get(handle) if handle else None
            if session is not None and (
                    session.get("owner_user_id"), session.get("owner_tenant_id")) != owner:
                session = None
            if session is None:
                return {"status": "success", "register_status": "expired"}
            status = session.get("status") or "idle"
            if status == "done":
                payload = {
                    "status": "success",
                    "register_status": "done",
                    "app_id": session.get("app_id", ""),
                    "app_secret": session.get("app_secret", ""),
                    # The console redeems this to create the instance with no
                    # password prompt, so the successful scan does not become a
                    # "configured in the UI but never stored" channel.
                    "scan_ticket": cls._mint_scan_grant(owner_user_id, owner_tenant_id),
                }
                # 一次性交付：凭据随即从服务端状态中移除。
                cls._sessions.pop(handle, None)
                return payload
            if status in ("error", "expired", "denied"):
                return {"status": "success", "register_status": status,
                        "message": session.get("error", "")}
            if status in ("starting", "idle"):
                # 与旧行为一致：启动阶段对外表现为 pending，二维码由后续轮询补发。
                status = "pending"
            payload = {"status": "success", "register_status": status}
            if session.get("url"):
                payload["qrcode_url"] = session["url"]
                payload["qr_image"] = session.get("qr_image", "")
            return payload

    @classmethod
    def _start_register_thread(cls, handle: str):
        """为 *handle* 指向的会话运行一次 SDK 注册。"""
        with cls._lock:
            session = cls._sessions.get(handle)
            if session is None:
                return
            cancel_event = session["cancel_event"]

        def _worker():
            try:
                # Desktop builds don't bundle lark_oapi; fetch it on demand the
                # first time the user enables Feishu (requires network). Flag it
                # so the modal explains the wait instead of just spinning.
                from channel.feishu import lark_install
                if lark_install.needs_download():
                    cls._set_status(handle, "downloading")
                lark_install.ensure(allow_install=True)
                import lark_oapi as lark
            except ImportError as e:
                cls._set_status(handle, "error", error=(
                    "飞书 SDK 不可用，请联网后重试，"
                    "或手动执行 pip install -U 'lark-oapi>=1.5.5'（%s）" % e
                ))
                return

            def _on_qr(info):
                # SDK 拿到二维码 URL 后立即回调；写入会话让前端 GET 立刻能拿到
                cls._set_status(
                    handle, "pending",
                    url=info.get("url", ""),
                    expire_in=info.get("expire_in", 600),
                    qr_image=cls._qr_to_data_uri(info.get("url", "")),
                )
                logger.info(f"[FeishuRegister] QR ready, expire_in={info.get('expire_in')}s")

            def _on_status(info):
                # 过滤掉 polling 心跳（每 5 秒一次，纯噪音）；
                # 保留 slow_down / domain_switched 等真正的状态切换事件
                status = info.get("status")
                if status == "polling":
                    return
                logger.info(f"[FeishuRegister] SDK status: {info}")

            try:
                result = lark.register_app(
                    on_qr_code=_on_qr,
                    on_status_change=_on_status,
                    source="cowagent",
                    cancel_event=cancel_event,
                )
                cls._set_status(
                    handle, "done",
                    app_id=result.get("client_id", ""),
                    app_secret=result.get("client_secret", ""),
                )
                logger.info(f"[FeishuRegister] App created: app_id={result.get('client_id')}")
            except Exception as e:
                err_msg = str(e)
                err_cls = e.__class__.__name__
                # 飞书 SDK 抛出的 AppExpiredError / AppAccessDeniedError / RegisterAppError
                if "Expired" in err_cls:
                    status = "expired"
                elif "Denied" in err_cls:
                    status = "denied"
                elif "abort" in err_msg.lower() or "cancel" in err_msg.lower():
                    # 被同一身份的新一轮注册取代，保持安静
                    return
                else:
                    status = "error"
                # 会话已被取代或回收时 _set_status 不写入，避免覆盖更新的会话
                cls._set_status(handle, status, error=err_msg)
                logger.warning(f"[FeishuRegister] Register failed ({err_cls}): {err_msg}")

        threading.Thread(target=_worker, daemon=True, name="feishu-register").start()

    def GET(self):
        """为当前发起者启动一次注册会话，返回句柄与二维码。"""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            owner_user_id, owner_tenant_id = _register_owner_scope()
            handle = self._create_session(owner_user_id, owner_tenant_id)
            self._start_register_thread(handle)
            # 等待 SDK 拿到二维码 URL（最多 10s）。SDK 内部会马上回调 _on_qr。
            import time as _t
            deadline = time.time() + self._QR_WAIT_SECONDS
            while time.time() < deadline:
                session = self._session_for(handle, owner_user_id, owner_tenant_id)
                if session is None or session.get("url") or session.get("status") in (
                    "downloading", "error", "expired", "denied"
                ):
                    break
                _t.sleep(0.1)
            session = self._session_for(handle, owner_user_id, owner_tenant_id)
            if session is None:
                return json.dumps({
                    "status": "error",
                    "message": "注册会话已失效，请重试",
                })
            if session.get("status") in ("error", "expired", "denied"):
                return json.dumps({
                    "status": "error",
                    "handle": handle,
                    "message": session.get("error", "register failed"),
                })
            if session.get("status") == "downloading":
                # The SDK bundle is still coming down; the QR only exists
                # once it lands, so hand the frontend over to polling.
                return json.dumps({
                    "status": "success",
                    "handle": handle,
                    "register_status": "downloading",
                })
            if not session.get("url"):
                return json.dumps({
                    "status": "error",
                    "handle": handle,
                    "message": "等待飞书二维码超时，请重试",
                })
            return json.dumps({
                "status": "success",
                "handle": handle,
                "qrcode_url": session["url"],
                "qr_image": session.get("qr_image", ""),
                "expire_in": session.get("expire_in", 600),
            })
        except Exception as e:
            logger.error(f"[WebChannel] FeishuRegister GET error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        """轮询当前发起者自己的注册会话。"""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b"{}")
            action = body.get("action", "poll")
            if action != "poll":
                return json.dumps({"status": "error", "message": f"unknown action: {action}"})
            handle = str(body.get("handle") or "").strip()
            if not handle:
                # 句柄缺失不属于归属判定，给出可操作错误而非凭空「过期」。
                return json.dumps({
                    "status": "error",
                    "message": "register handle is required",
                    "code": "missing_handle",
                })
            owner_user_id, owner_tenant_id = _register_owner_scope()
            return json.dumps(
                self._poll_payload(handle, owner_user_id, owner_tenant_id))
        except Exception as e:
            logger.error(f"[WebChannel] FeishuRegister POST error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _request_agent_id(source) -> str:
    value = getattr(source, "agent_id", None)
    if value is None:
        value = getattr(source, "agent", None)
    if value is None and isinstance(source, dict):
        value = source.get("agent_id") or source.get("agent")
    # web.py merges query string and form body, so a field present in both
    # arrives as a list. Collapse it to a single id rather than letting an
    # unhashable list reach registry lookups.
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value or None


class ToolsHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.tools.tool_manager import ToolManager
            from common import i18n
            with _db_scope() as ctx:
                _require_catalog_read(ctx, "tool.read")
                tm = ToolManager()
                if not tm.tool_classes:
                    tm.load_tools()
                tools = []
                lang = i18n.get_language()
                for name, cls in tm.tool_classes.items():
                    try:
                        instance = cls()
                        desc = instance.description
                        if lang == i18n.ZH_HANT and desc:
                            desc = i18n.to_traditional(desc)
                        elif lang == "en" and name == "scheduler":
                            desc = (
                                "Create, query and manage scheduled tasks (reminders, periodic tasks, etc.).\n\n"
                                "⚠️ IMPORTANT: Only use this tool when delayed or periodic execution is needed."
                            )
                        tools.append({
                            "resource_id": f"builtin:{name}",
                            "name": name,
                            "description": desc,
                        })
                    except Exception:
                        tools.append({"resource_id": f"builtin:{name}", "name": name, "description": ""})
                # MCP tools: namespaced by their connection/server name, matching
                # the auth catalog projection convention.
                mcp_instances = getattr(tm, "_mcp_tool_instances", None) or {}
                for tname, mcp_tool in mcp_instances.items():
                    conn = getattr(mcp_tool, "server_name", "default")
                    tools.append({
                        "resource_id": f"mcp:{conn}:{tname}",
                        "name": tname,
                        "description": mcp_tool.description or "",
                    })
                tools = _filter_tool_catalog(ctx, tools, "read")
            return json.dumps({"status": "success", "tools": tools}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Tools API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _skill_service(agent_id: str = ''):
    """
    A SkillService over the skills the console manages.

    Skills stay anchored to the agent's state root even while a session has a
    project open, so this deliberately resolves the workspace without a session.
    ``agent_id`` selects which agent's skills to manage, so a multi-agent setup
    keeps each agent's library isolated.
    """
    from agent.skills.manager import SkillManager
    from agent.skills.service import SkillService
    from common import state_dir
    workspace_root = _get_workspace_root(agent_id=agent_id or None)
    custom_dir = str(state_dir.skills_dir(base=workspace_root))
    return SkillService(SkillManager(custom_dir=custom_dir))


def _filter_skill_catalog(ctx: "Optional[RequestContext]", skills: List[dict], action: str) -> List[dict]:
    """Narrow a skill list to those the caller may act on (``read``/``use``/...).

    A platform admin is unrestricted. The built-in tenant_admin reads its own
    tenant's whole catalog (the read-only management view); a database-mode
    member sees only skills explicitly granted for ``action`` (a skill's
    ``resource_id`` must be present; skills persisted before this field get one
    from their ``source``/``name``). Legacy mode is unrestricted.
    """
    if ctx is not None and ctx.is_tenant_admin:
        return skills
    allowed = _resource_ids(ctx, "skill", action, permission="skill.read" if action != "read" else None)
    if allowed is None:
        return skills
    out = []
    for skill in skills:
        rid = skill.get("resource_id")
        if not rid:
            rid = f"{skill.get('source', 'builtin')}:{skill.get('name', '')}"
        if rid in allowed:
            out.append(skill)
    return out


def _filter_tool_catalog(ctx: "Optional[RequestContext]", tools: List[dict], action: str) -> List[dict]:
    """Narrow a tool list to entries the caller may act on (``read``/``execute``/...).

    A platform admin is unrestricted. The built-in tenant_admin reads its own
    tenant's whole catalog (the read-only management view); a database-mode
    member sees only tools explicitly granted for ``action`` via their
    ``resource_id``. Legacy mode is unrestricted.
    """
    if ctx is not None and ctx.is_tenant_admin:
        return tools
    allowed = _resource_ids(ctx, "tool", action, permission="tool.read" if action != "read" else None)
    if allowed is None:
        return tools
    out = []
    for tool in tools:
        if tool.get("resource_id") in allowed:
            out.append(tool)
    return out


class SkillsHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from common import i18n
            with _db_scope() as ctx:
                _require_catalog_read(ctx, "skill.read")
                params = web.input(agent_id='')
                # The library page lists everything installed, unnarrowed by the
                # Agent's selection: a skill it has not selected still has to be
                # visible here for the selection to be editable at all.
                service = _skill_service(_request_agent_id(params))
                skills = service.query()
                skills = _filter_skill_catalog(ctx, skills, "read")
                if i18n.get_language() == i18n.ZH_HANT:
                    for skill in skills:
                        if isinstance(skill, dict):
                            for k, v in list(skill.items()):
                                if k in ("name", "description", "display_name") and isinstance(v, str):
                                    skill[k] = i18n.to_traditional(v)
            return json.dumps({"status": "success", "skills": skills}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Skills API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "skill.read")
                body = json.loads(web.data())
                action = body.get("action")
                name = body.get("name")
                resource_id = body.get("resource_id")
                if not action:
                    return json.dumps({"status": "error", "message": "action is required"})
                if not name and not resource_id:
                    return json.dumps({"status": "error", "message": "name or resource_id is required"})
                service = _skill_service(_request_agent_id(body))
                target = {"name": name} if name else {"resource_id": resource_id}
                if action == "open":
                    _require_resource_action(ctx, "skill", resource_id or name, "enable", "skill.enable")
                    service.open(target)
                elif action == "close":
                    _require_resource_action(ctx, "skill", resource_id or name, "enable", "skill.enable")
                    service.close(target)
                else:
                    return json.dumps({"status": "error", "message": f"unknown action: {action}"})
            return json.dumps({"status": "success"}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Skills POST error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SkillContentHandler:
    """
    A skill's definition file, for the console's viewer and editor.

    Addressed by skill name rather than by path, because the loader is what
    resolves a name to a file: a workspace skill shadows a builtin of the same
    name, and a builtin sits outside the workspace that the file APIs are
    confined to.

    Unlike the skill list, the text is served exactly as stored - no
    simplified-to-traditional conversion. What comes back here is what a save
    would write, and rewriting someone's file into another script because of
    the console's display language is not a conversion they asked for.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_catalog_read(ctx, "skill.read")
                params = web.input(name='', resource_id='', agent_id='')
                name = (getattr(params, 'name', '') or '').strip()
                resource_id = (getattr(params, 'resource_id', '') or '').strip()
                if not name and not resource_id:
                    return json.dumps({"status": "error", "message": "name or resource_id is required"})
                service = _skill_service(_request_agent_id(params))
                # Resolve to the exact authorization object, then check the grant.
                entry = service.resolve(resource_id=resource_id or None, name=name or None)
                rid = resource_id or f"{entry.skill.source}:{entry.skill.name}"
                # A tenant admin browses its own tenant's skills read-only; the
                # write path (POST) still requires ``skill.edit`` and a grant.
                if not (ctx is not None and ctx.is_tenant_admin):
                    _require_resource_action(ctx, "skill", rid, "read", "skill.read")
                result = service.read_content(entry.skill.name, resource_id=rid)
            return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"status": "error", "message": str(e)})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Skill content error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.workspace.service import WorkspaceConflictError

            with _db_scope() as ctx:
                _require_read_permission(ctx, "skill.read")
                body = json.loads(web.data() or b'{}')
                name = (body.get("name") or "").strip()
                resource_id = (body.get("resource_id") or "").strip()
                if not name and not resource_id:
                    return json.dumps({"status": "error", "message": "name or resource_id is required"})
                content = body.get("content")
                if not isinstance(content, str):
                    return json.dumps({"status": "error", "message": "content must be a string"})
                service = _skill_service(_request_agent_id(body))
                # Resolve to the exact authorization object, then enforce edit.
                entry = service.resolve(resource_id=resource_id or None, name=name or None)
                rid = resource_id or f"{entry.skill.source}:{entry.skill.name}"
                _require_resource_action(ctx, "skill", rid, "edit", "skill.edit")

                try:
                    result = service.write_content(
                        name or None, content, expected_mtime=body.get("expected_mtime"),
                        resource_id=rid,
                    )
                except WorkspaceConflictError as e:
                    return json.dumps({"status": "error", "code": "conflict", "message": str(e)})

                logger.info(f"[WebChannel] Skill saved: {name or resource_id} ({result['size']} bytes)")
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"status": "error", "message": str(e)})
        except PermissionError:
            return json.dumps({"status": "error", "message": "permission denied"})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Skill write error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class MemoryHandler:
    """``GET /api/memory``: a scope-adapted compatibility read (task 5).

    The target is resolved by ``channel/web/memory_console`` — the caller's own
    personal domain, a privately owned Agent's memory, or the tenant's shared
    Agent memory — and a request that names no single target is refused with a
    stable code instead of being answered from the tenant shared root. The
    legacy field names and pagination parameters are unchanged.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from channel.web import memory_console
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "memory.read")
                params = web.input(
                    page='1', page_size='20', category='memory', agent_id='',
                    scope='',
                )
                return memory_console.list_response(ctx, params)
        except web.HTTPError:
            raise
        except memory_console.MemoryScopeError as e:
            raise memory_console.http_error(e)
        except Exception as e:
            logger.error(f"[WebChannel] Memory API error: {e}")
            raise memory_console.http_error(memory_console.refusal_for(e))


class MemoryContentHandler:
    """``GET /api/memory/content``: the body of one entry of the same target."""

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from channel.web import memory_console
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "memory.read")
                params = web.input(filename='', category='memory', agent_id='',
                                   scope='')
                return memory_console.content_response(ctx, params)
        except web.HTTPError:
            raise
        except memory_console.MemoryScopeError as e:
            raise memory_console.http_error(e)
        except Exception as e:
            logger.error(f"[WebChannel] Memory content API error: {e}")
            raise memory_console.http_error(memory_console.refusal_for(e))


class PersonalMemoryHandler:
    """「我的记忆」：当前租户 + 当前用户的个人长期记忆（stage 5）.

    Distinct from ``MemoryHandler`` above, which lists **该私有 Agent 记忆**
    from an Agent workspace. The two share the owner check but not the storage
    root: this handler never accepts an ``agent_id``, and its entries are
    resolved inside the caller's own user domain.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "memory.read")
                service = _personal_memory_service(ctx)
                return json.dumps(
                    {"status": "success", "entries": service.list_entries(),
                     "scope": "personal"},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal memory list error: {e}")
            return _personal_memory_error(e)

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b'{}')
            action = (body.get("action") or "").strip()
            with _db_scope() as ctx:
                _require_read_permission(ctx, "memory.read")
                service = _personal_memory_service(ctx)
                if action == "save":
                    result = service.save(
                        body.get("id"), body.get("content"),
                        expected_revision=body.get("revision"))
                elif action == "delete":
                    result = service.delete(
                        body.get("id"),
                        expected_revision=body.get("revision"))
                elif action == "clear":
                    result = service.clear(expected_revision=body.get("revision"))
                elif action == "retry_index":
                    result = service.retry_pending_index()
                else:
                    return json.dumps(
                        {"status": "error", "message": f"unknown action: {action}"},
                        ensure_ascii=False)
                # A pending index is a real, recoverable not-done state: the
                # content operation succeeded but retrieval may still hold rows.
                # Reporting "success" here would claim a consistency we do not
                # have (task 5.3).
                if result.get("index_state") == "pending":
                    return json.dumps(
                        {"status": "pending", "code": "index_pending",
                         "message": "内容已更新，索引待重试",
                         "result": result},
                        ensure_ascii=False)
                return json.dumps({"status": "success", **result},
                                  ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal memory write error: {e}")
            return _personal_memory_error(e)


class PersonalMemoryContentHandler:
    """Read one personal memory entry by its relative id."""

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "memory.read")
                params = web.input(id='')
                entry_id = (getattr(params, 'id', '') or '').strip()
                if not entry_id:
                    return json.dumps(
                        {"status": "error", "code": "invalid_entry",
                         "message": "id is required"}, ensure_ascii=False)
                service = _personal_memory_service(ctx)
                return json.dumps({"status": "success", **service.read(entry_id)},
                                  ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal memory content error: {e}")
            return _personal_memory_error(e)


def _personal_memory_service(ctx):
    """A personal-memory service bound to the *verified request context*.

    Ownership comes from ``ctx``, never from the request body: there is no
    parameter a caller could set to name another user.
    """
    from agent.memory.personal import PersonalMemoryService
    from auth.runtime import to_runtime_identity
    if ctx is None or not ctx.user_id or not ctx.tenant_id:
        raise web.HTTPError("403 Forbidden", {"Content-Type": "application/json"},
                            json.dumps({"status": "error", "message": "forbidden",
                                        "code": "forbidden"}))
    return PersonalMemoryService(identity=to_runtime_identity(ctx))


def _personal_memory_error(exc):
    """Render a refusal with the status and code the console branches on."""
    from agent.memory.personal import PersonalMemoryError
    if isinstance(exc, PersonalMemoryError):
        if exc.status in (401, 403):
            raise web.HTTPError(
                f"{exc.status} Forbidden" if exc.status == 403 else "401 Unauthorized",
                {"Content-Type": "application/json"},
                json.dumps({"status": "error", "code": exc.code,
                            "message": str(exc)}, ensure_ascii=False))
        if exc.status == 409:
            web.ctx.status = "409 Conflict"
        return json.dumps({"status": "error", "code": exc.code,
                           "message": str(exc)}, ensure_ascii=False)
    return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)


class PersonalChannelHandler:
    """「我的渠道」：当前租户 + 当前用户的个人消息渠道（任务 6.1-6.7）.

    Every write reuses the tenant channel service with ``allow_owner``: the
    scope/owner pair is forced from the verified request context, so the client
    cannot name a tenant, an owner, or another member's instance. The public
    ``/api/tenant/channels`` surface keeps its administrator gate untouched —
    this is a *separate* registered surface, not a relaxation of that one.
    """

    def GET(self):
        """List my personal instances, plus the types open for onboarding."""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                service = _personal_channel_service()
                listing = service.list_personal_channel_instances(
                    actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id)
                return json.dumps(
                    {"status": "success",
                     "channel_types": service.personal_channel_types(),
                     **listing},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal channel list error: {e}")
            return _personal_channel_error(e)

    def POST(self):
        """Create one personal instance (``channel_type`` + credentials)."""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b'{}')
            with _db_scope() as ctx:
                service = _personal_channel_service()
                created = service.create_personal_channel_instance(
                    actor_user_id=ctx.user_id,
                    tenant_id=ctx.tenant_id,
                    channel_type=str(body.get("channel_type") or ""),
                    display_name=str(body.get("display_name") or ""),
                    agent_id=str(body.get("agent_id") or ""),
                    credentials=body.get("credentials"),
                    recent_password=str(body.get("recent_password") or ""),
                )
                return json.dumps(
                    {"status": "success", "instance": created,
                     "runtime": _apply_personal_channel_runtime(created["id"])},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal channel create error: {e}")
            return _personal_channel_error(e)


class PersonalChannelInstanceHandler:
    """One personal instance: read, edit, enable/disable, revoke, binding.

    The instance id is the only thing the client names; ownership, tenant and
    scope come from the verified context, so a foreign id is refused without
    disclosing whether it exists.
    """

    def GET(self, instance_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                service = _personal_channel_service()
                instance = service.get_personal_channel_instance(
                    actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                    instance_id=instance_id)
                status = service.personal_channel_binding_status(
                    actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                    instance_id=instance_id)
                return json.dumps(
                    {"status": "success", "instance": instance, **status},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal channel read error: {e}")
            return _personal_channel_error(e)

    def POST(self, instance_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b'{}')
            action = (body.get("action") or "").strip()
            with _db_scope() as ctx:
                service = _personal_channel_service()
                if action == "update":
                    result = service.update_personal_channel_instance(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        instance_id=instance_id,
                        expected_version=_int_or_zero(body.get("expected_version")),
                        display_name=body.get("display_name"),
                        agent_id=body.get("agent_id"),
                        credentials=body.get("credentials"),
                        recent_password=str(body.get("recent_password") or ""),
                    )
                elif action in ("enable", "disable"):
                    result = service.set_personal_channel_instance_active(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        instance_id=instance_id,
                        active=(action == "enable"),
                        expected_version=_int_or_zero(body.get("expected_version")),
                        recent_password=str(body.get("recent_password") or ""),
                    )
                elif action == "revoke":
                    result = service.revoke_personal_channel_credentials(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        instance_id=instance_id,
                        expected_version=_int_or_zero(body.get("expected_version")),
                        recent_password=str(body.get("recent_password") or ""),
                    )
                elif action == "start_binding":
                    # The code is returned exactly once: only its hash is stored,
                    # and the member has to be able to read it out of the browser
                    # to send it from their IM account.
                    challenge = service.start_personal_channel_binding(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        instance_id=instance_id)
                    return json.dumps({"status": "success", "challenge": challenge},
                                      ensure_ascii=False)
                elif action == "unlink":
                    result = service.unlink_personal_channel_instance(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        instance_id=instance_id)
                else:
                    return json.dumps(
                        {"status": "error", "code": "bad_request",
                         "message": f"unknown action: {action}"},
                        ensure_ascii=False)
                return json.dumps(
                    {"status": "success", "instance": result,
                     "runtime": _apply_personal_channel_runtime(instance_id)
                     if action in ("update", "enable", "disable", "revoke")
                     else None},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal channel write error: {e}")
            return _personal_channel_error(e)


class PersonalResourceHandler:
    """「我的工具 / 我的技能」：本人已获授权资源的个人参数（任务 2.4 / 8.3）.

    A *personal configuration* surface, not a public maintenance one. The list is
    the intersection of the caller's current per-resource grants and what they
    saved; saving writes only the caller's own row (``actor_user_id`` is the
    owner, there is no ``user_id`` parameter), and never touches a tool
    definition, an MCP connection, a skill body, install/enable state, or the
    tenant's grants. Those keep their own administrator gates.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            params = web.input(kind='')
            with _db_scope() as ctx:
                service = _personal_channel_service()
                kind = str(params.kind or "").strip()
                if kind and kind not in ("tool", "skill"):
                    return json.dumps(
                        {"status": "error", "code": "bad_request",
                         "message": f"unsupported resource kind: {kind}"},
                        ensure_ascii=False)
                return json.dumps(
                    {"status": "success", "scope": "personal",
                     "resources": service.list_personal_resource_configs(
                         actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                         resource_kind=kind)},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal resource list error: {e}")
            return _personal_channel_error(e)

    def POST(self):
        """Save or clear the caller's own parameters for one granted resource."""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b'{}')
            action = str(body.get("action") or "save").strip()
            with _db_scope() as ctx:
                service = _personal_channel_service()
                if action == "save":
                    saved = service.save_personal_resource_config(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        resource_kind=str(body.get("resource_kind") or ""),
                        resource_id=str(body.get("resource_id") or ""),
                        params=body.get("params") or {},
                        secret=body.get("secret"))
                    return json.dumps({"status": "success", "config": saved},
                                      ensure_ascii=False)
                if action == "clear":
                    service.clear_personal_resource_config(
                        actor_user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                        resource_kind=str(body.get("resource_kind") or ""),
                        resource_id=str(body.get("resource_id") or ""))
                    return json.dumps({"status": "success", "config": None},
                                      ensure_ascii=False)
                return json.dumps(
                    {"status": "error", "code": "bad_request",
                     "message": f"unknown action: {action}"},
                    ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Personal resource write error: {e}")
            return _personal_channel_error(e)


def _personal_channel_service():
    """The identity service, in whatever process serves the console."""
    from auth.service import get_identity_service
    return get_identity_service()


def _apply_personal_channel_runtime(instance_id: str):
    """Report the runtime consequence of a personal channel write (task 6.7).

    "Saved" and "connected" are different facts, and the console has to be able
    to tell them apart: the row is committed either way, so a failure to apply
    it is reported as ``pending`` with a reason rather than raised — turning it
    into a 5xx would invite a retry of a write that already succeeded.
    """
    from channel.channel_instances import apply_tenant_instance_runtime

    try:
        return apply_tenant_instance_runtime(instance_id)
    except Exception as e:  # pragma: no cover - defensive
        logger.error(
            f"[PersonalChannels] runtime apply failed for '{instance_id}': {e}")
        return {"applied": False, "pending": True,
                "error": f"runtime apply failed: {e}"}


def _personal_channel_error(exc):
    """Render a refusal with the status and code the console branches on."""
    status = getattr(exc, "status", 500)
    code = getattr(exc, "code", "") or "internal"
    message = (exc.args[0] if exc.args else None) or "request failed"
    if status in (401, 403):
        raise web.HTTPError(
            f"{status} Forbidden" if status == 403 else "401 Unauthorized",
            {"Content-Type": "application/json"},
            json.dumps({"status": "error", "code": code, "message": str(message)},
                       ensure_ascii=False))
    if status in (404, 409, 410, 429):
        web.ctx.status = {404: "404 Not Found", 409: "409 Conflict",
                          410: "410 Gone",
                          429: "429 Too Many Requests"}.get(status, "400 Bad Request")
    return json.dumps({"status": "error", "code": code, "message": str(message)},
                      ensure_ascii=False)


def _scheduler_task_store(agent_id: str):
    """The ``TaskStore`` for one Agent, resolved through the per-Agent layout.

    ``common/state_dir.scheduler_file`` is the one definition of "where an Agent's
    schedule lives" (``<agent workspace>/scheduler/tasks.json``), and it is keyed
    on ``agent_id`` rather than on the request's working root: in database mode
    the working root is the *tenant's* shared root, so resolving the store from it
    would make every Agent of a tenant share one file. The identity is derived
    with the Agent under test pinned, which is also what the tool path does.
    """
    from agent.tools.scheduler.task_store import TaskStore
    from common import state_dir
    from common.runtime_identity import current_identity

    identity = current_identity().derive(agent_id=agent_id)
    return TaskStore(str(state_dir.scheduler_file(identity)))


def _scheduler_agent_ids(actor) -> List[str]:
    """Every Agent whose schedule the acting member may be shown.

    Authoritative for the aggregate view: the caller's tenant decides the list
    (``tenant_agent_ids``), and an Agent that the registry does not have enabled
    is not opened at all - so an Agent of another tenant is never read and then
    filtered.
    """
    from agent.registry import get_agent_registry
    from auth.service import get_identity_service

    enabled = {p.id for p in get_agent_registry().list(include_disabled=False)}
    bound = get_identity_service().tenant_agent_ids(actor.tenant_id)
    return [agent_id for agent_id in bound if agent_id in enabled]


def _scheduler_scope_resolver(actor, agent_id: str) -> Dict:
    """Binding + ``agent.use`` for one Agent, for the authorization service."""
    from auth.service import get_identity_service

    service = get_identity_service()
    scope: Dict = {"agent_id": agent_id}
    try:
        binding = service.get_agent_binding(agent_id)
        scope["bound_tenant"] = (binding or {}).get("tenant_id") or ""
    except Exception as error:
        logger.debug("[WebChannel] scheduler binding lookup %r: %s", agent_id, error)
    if actor.is_admin:
        scope["can_use"] = True
    else:
        try:
            scope["can_use"] = bool(service.check_resource_action(
                actor.user_id, actor.tenant_id, "agent", f"agent:{agent_id}",
                "use", permission="agent.use"))
        except Exception:
            scope["can_use"] = False
    return scope


def _scheduler_service_for_web(agent_id: str):
    """The scheduler runtime for one Agent, for the manual-run path."""
    from agent.tools.scheduler.integration import get_scheduler_service
    return get_scheduler_service(agent_id=agent_id)


def _scheduler_access(ctx=None) -> "TaskAccessService":
    """Build the shared task authorization service for this request.

    One construction for all five handlers, so the HTTP path cannot diverge from
    the tool and background paths (tasks 3.1/3.3).
    """
    from agent.tools.scheduler.authorization import (
        TaskActor, TaskAccessService, actor_from_identity_context,
    )

    if ctx is not None:
        actor = actor_from_identity_context(ctx, source="http")
    else:
        from channel.web.auth_handlers import _require_context
        actor = actor_from_identity_context(_require_context(require_tenant=True),
                                            source="http")
    actor.session_id = _web_auth_session_id()

    def quota_gate(actor_, agent_id, count):
        from auth.service import get_identity_service
        get_identity_service().check_scheduled_task_quota(
            user_id=actor_.user_id, tenant_id=actor_.tenant_id,
            would_be_count=count)

    return TaskAccessService(
        store_resolver=lambda actor_, agent_id: _scheduler_task_store(agent_id),
        agent_ids=_scheduler_agent_ids,
        scope_resolver=_scheduler_scope_resolver,
        quota_check=quota_gate,
        run_hook=lambda actor_, agent_id, task_id: _scheduler_run_hook(
            actor_, agent_id, task_id),
        coordinator="http",
    )


def _scheduler_run_hook(actor, agent_id: str, task_id: str) -> None:
    """Fire one task now through the scheduler runtime.

    The runtime is the same object the timer uses, so a manual run cannot take a
    path a scheduled fire would refuse; the authorization service has already
    re-checked the caller's own grants before calling this. A runtime that is not
    up is reported as ``run_unavailable`` (503), not as a silent success: the
    console must not tell a member their task ran when nothing was queued.
    """
    from agent.tools.scheduler.authorization import (
        RUN_UNAVAILABLE, TaskAuthorizationError,
    )

    service = _scheduler_service_for_web(agent_id)
    if service is None:
        raise TaskAuthorizationError(
            RUN_UNAVAILABLE, status=503, message="Scheduler service is not running")
    service.run_task_now(task_id)


def _web_auth_session_id() -> str:
    """The caller's web auth session id, for the task owner snapshot."""
    try:
        from channel.web import auth_handlers
        session = auth_handlers._current_session() if hasattr(
            auth_handlers, "_current_session") else None
        if isinstance(session, dict):
            return session.get("id") or ""
    except Exception:
        pass
    return ""


_SCHEDULER_STATUS_LINES = {
    400: "400 Bad Request",
    401: "401 Unauthorized",
    403: "403 Forbidden",
    404: "404 Not Found",
    409: "409 Conflict",
    503: "503 Service Unavailable",
}


def _scheduler_error(error):
    """The HTTP response for a refused management call.

    Raised as a ``web.HTTPError`` rather than returned with ``web.status`` set:
    the status has to survive the handler's own ``except`` clauses and the
    request's context teardown, and raising is the only form that does. ``code``
    is the stable machine value the console branches on; the status distinguishes
    "not yours" (403) from "no such task" (404) and "someone else edited it
    first" (409).
    """
    status = int(getattr(error, "status", 403) or 403)
    raise web.HTTPError(
        _SCHEDULER_STATUS_LINES.get(status, "%d Error" % status),
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(error.payload(), ensure_ascii=False),
    )


class SchedulerHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            params = web.input(agent_id='', page='1', page_size='20')
            requested = _request_agent_id(params)
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                page = service.list_tasks(
                    _scheduler_actor(ctx), agent_id=requested,
                    page=_int_param(params, "page", 1),
                    page_size=_int_param(params, "page_size", 20),
                )
            return json.dumps({"status": "success", **page}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _int_param(params, name: str, default: int) -> int:
    try:
        return int(getattr(params, name, default) or default)
    except (TypeError, ValueError):
        return default


def _scheduler_actor(ctx):
    from agent.tools.scheduler.authorization import actor_from_identity_context
    actor = actor_from_identity_context(ctx, source="http")
    actor.session_id = _web_auth_session_id()
    return actor


class SchedulerRunHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            agent_id = _request_agent_id(body)
            task_id = body.get("task_id")
            # The client's "same request" proof (task 3.4). A retry after a lost
            # response resends the same key and is answered from the accepted
            # outcome instead of queueing a second fire; a click without a key
            # keeps the older behaviour (refused while the task is running).
            run_key = str(body.get("run_key") or "").strip()
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                service.run_task(_scheduler_actor(ctx), agent_id, task_id,
                                 run_key=run_key or None)
            return json.dumps({
                "status": "success",
                "message": f"Task '{task_id}' queued for immediate execution",
            }, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler manual run error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerToggleHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            enabled = body.get("enabled", True)
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                task = service.set_enabled(_scheduler_actor(ctx),
                                          _request_agent_id(body), task_id,
                                          bool(enabled))
            return json.dumps({"status": "success", "task": task}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler toggle error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerUpdateHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import (
            FORGED_FIELD, TaskAuthorizationError,
        )
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))

            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                actor = _scheduler_actor(ctx)
                agent_id = _request_agent_id(body)
                original = service.get_task(actor, agent_id, task_id)
                # Every other body key is handed to the service as a patch, so a
                # field the caller may not set (``owner``, ``scope``,
                # ``agent_id``, ...) is refused by name (400) instead of being
                # quietly dropped here — a silent drop would let a caller believe
                # a rename happened when the field they really wanted was ignored.
                patch = {k: v for k, v in body.items()
                         if k not in ("task_id", "agent_id", "revision")}
                if "schedule" in patch:
                    # Recompute next_run_at for the merged task, and refuse a
                    # schedule that cannot produce one (an unparseable cron, or a
                    # one-off time already past) — the same check the editor has
                    # always relied on, now before the write rather than after.
                    from agent.tools.scheduler.scheduler_service import SchedulerService
                    from agent.tools.scheduler.task_store import TaskStore
                    merged = dict(original)
                    merged.update(patch)
                    if "action" in patch and isinstance(patch["action"], dict):
                        action = dict(original.get("action") or {})
                        action.update(patch["action"])
                        merged["action"] = action
                    dry = SchedulerService(TaskStore(os.devnull), lambda t: None)
                    next_run = dry._calculate_next_run(merged, datetime.now())
                    if not next_run:
                        raise web.HTTPError(
                            "400 Bad Request",
                            {"Content-Type": "application/json; charset=utf-8"},
                            json.dumps({
                                "status": "error",
                                "message": "Cannot calculate next run time. Please check the schedule config (e.g., cron expression format, or whether the one-time task time has already passed).",
                            }, ensure_ascii=False))
                    patch["next_run_at"] = next_run.isoformat()
                elif "action" in patch and not original.get("next_run_at"):
                    from agent.tools.scheduler.scheduler_service import SchedulerService
                    from agent.tools.scheduler.task_store import TaskStore
                    merged = dict(original)
                    merged.update(patch)
                    dry = SchedulerService(TaskStore(os.devnull), lambda t: None)
                    next_run = dry._calculate_next_run(merged, datetime.now())
                    if next_run:
                        patch["next_run_at"] = next_run.isoformat()

                revision = body.get("revision")
                task = service.update_task(
                    actor, agent_id, task_id, patch,
                    expected_revision=int(revision) if revision is not None else None)
            return json.dumps({"status": "success", "task": task}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler update error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SchedulerDeleteHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from agent.tools.scheduler.authorization import TaskAuthorizationError
        try:
            body = json.loads(web.data())
            task_id = body.get("task_id")
            if not task_id:
                raise web.HTTPError(
                    "400 Bad Request",
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"status": "error", "message": "task_id required"}))
            with _db_scope() as ctx:
                service = _scheduler_access(ctx)
                service.delete_task(_scheduler_actor(ctx),
                                    _request_agent_id(body), task_id)
            return json.dumps({"status": "success"}, ensure_ascii=False)
        except TaskAuthorizationError as error:
            raise _scheduler_error(error)
        except Exception as e:
            logger.error(f"[WebChannel] Scheduler delete error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _agent_admin_service():
    from agent.admin import AgentAdminService
    return AgentAdminService(os.path.join(get_data_root(), "config.json"))


def _workbench_agents_projection() -> Dict:
    """Minimal read-only projection for the workbench (use-Agents) page.

    Returns only the fields the card gallery needs to render and decide whether
    a chat may start. It deliberately does NOT expose workspace paths, channel
    instances, core files or model credentials — a whitelist, not a client-side
    trim of the full management snapshot.

    The default Agent is computed from the snapshot and, if it is visible, is
    flagged. Read scope follows the current identity mode: in the legacy
    # Database identity is always required; callers go through _db_scope /
    # route policy rather than a shared-password helper.

    if the identity change lands, this must be gated by the real ``agent.read``
    permission and resource range. State is not mutated here: listing never
    changes the active Agent or any session.
    """
    from agent.registry import get_agent_registry

    registry = get_agent_registry()
    default_id = registry.default_agent_id
    agents = []
    for profile in sorted(registry.list(), key=lambda item: item.id != default_id):
        # Only saved + enabled + readable Agents appear. Archived/disabled
        # Agents stay in the config page only.
        if not profile.enabled:
            continue
        # can_chat means the current identity mode permits entering a chat. In
        # legacy mode enabled agents can run. If the database identity change is
        # active, its runtime closure must be honoured here: return a stable
        # localizable reason (e.g. ``runtime_not_enabled``) rather than silently
        # enabling the entry. Today the database mode is not validated, so this
        # never trips; the hook is left explicit for that consumer.
        can_chat, unavailable_reason = _workbench_chat_readiness(None, profile.id)
        agents.append({
            "id": profile.id,
            "name": profile.name,
            "description": profile.description or "",
            "avatar": profile.avatar or None,
            "is_default": bool(profile.id == default_id),
            "can_chat": can_chat,
            "unavailable_reason": unavailable_reason,
            # Digital-employee projection fields for the card gallery.
            "position": profile.position or "",
            "category": profile.category or "",
            "tags": list(profile.tags or []),
        })
    return {"agents": agents}


def _tenant_ids_for_context(ctx: "Optional[RequestContext]") -> Optional[list]:
    """Return the list of agent_ids visible to ``ctx``, or None if unscoped.

    In database mode, only agents bound to the caller's **currently selected
    tenant** are visible. Platform administrators are held to the same scope:
    platform ``all`` skips functional and resource grants, not the data scope,
    so the console's tenant selection decides which Agents are listed.
    Cross-tenant administration is a platform-entry concern
    (``/api/platform/tenants/<id>/agents``), never a wider business read. With no
    tenant selected the scope is empty rather than the whole roster. In legacy
    mode (``ctx is None``) every agent is visible.
    """
    if ctx is None:
        return None
    if not ctx.tenant_id:
        return []
    from auth.service import get_identity_service
    return get_identity_service().tenant_agent_ids(ctx.tenant_id)


def _resolve_tenant_default_agent(ctx: "Optional[RequestContext]") -> Optional[str]:
    """The Agent an Agent-less request from ``ctx`` should be anchored to.

    A session must still belong to one Agent, but the user must not have to pick
    it. The rule itself lives on the identity service
    (:meth:`IdentityService.resolved_default_agent_id`) so every read path
    agrees: the member's own registered default (when reachable), then the
    tenant's configured default, then the tenant-shared Agents by smallest
    stable id, then any. Read-only — it never writes a default, so a GET cannot
    mutate and the answer never flips with binding insert order. The global
    ``registry.default_agent_id`` is never borrowed — it may belong to another
    tenant. Returns None only when the tenant has no Agent at all.

    An ordinary member also anchors on their *own* private assistant when they
    have one, so the personal copy created with their membership is what "no
    Agent selected" means for them. Tenant and platform administrators are
    deliberately exempt: they are the ones who configure the tenant default and
    must keep seeing and managing the tenant-wide answer, not their own copy of
    it. Everything downstream (the workbench projection and the ``/api/agents``
    response) reads this same function, so the badge, the ordering and the
    anchoring can never disagree.
    """
    if ctx is None or not ctx.tenant_id:
        return None
    from auth.service import get_identity_service
    is_admin = bool(getattr(ctx, "is_platform_admin", False)
                    or getattr(ctx, "is_tenant_admin", False))
    subject = None if is_admin else getattr(ctx, "user_id", None)
    return get_identity_service().resolved_default_agent_id(ctx.tenant_id, subject)


def _tenant_default_agent_id(ctx: "Optional[RequestContext]") -> Optional[str]:
    """Resolve the tenant-bound default Agent for ``ctx`` (task 3.8).

    Returns the tenant's configured ``default_agent_id`` when set, then the
    deterministic fallback from :func:`_resolve_tenant_default_agent`. Legacy
    mode (``ctx is None``) has no tenant default, so callers fall back to the
    global registry default.
    """
    return _resolve_tenant_default_agent(ctx)


def _tenant_agent_candidates(ctx: "RequestContext"):
    """Yield ``(profile, tenant_default)`` for the tenant's bound, enabled Agents.

    Authorization is deliberately *not* applied here: this is the denominator the
    empty-state diagnosis needs, so "this tenant has no Agent" and "this tenant
    has nothing this caller may reach" stay distinguishable. Nothing about the
    withheld Agents is exposed — only the count of candidates is observed.

    ``is_default`` is the caller's *tenant-bound* default agent (task 3.8) —
    never the global default — so the same global Agent bound to two tenants is
    only marked default for the tenant that actually selected it.
    """
    from agent.registry import get_agent_registry
    registry = get_agent_registry()
    visible = _tenant_ids_for_context(ctx)
    tenant_default = _tenant_default_agent_id(ctx)
    for profile in sorted(registry.list(), key=lambda item: (item.id != tenant_default, item.id)):
        if visible is not None and profile.id not in visible:
            continue
        if not profile.enabled:
            continue
        yield profile, tenant_default


def _iter_tenant_agents(ctx: "RequestContext"):
    """Yield the Agents a database-mode caller may read, default-first.

    Each item is ``(profile, tenant_default, can_chat, unavailable_reason)``.
    Visibility and chat readiness live here so the workbench projection (a
    minimal whitelist) and the management projection (the editable fields) can
    never drift apart: both read the same roster through the same gates.
    """
    # Fine-grained resource grant: only show agents the caller may read. A tenant
    # admin administers its own tenant's Agents, and ``visible`` already holds it
    # to that tenant's bindings, so per-resource grants must not additionally
    # hide an Agent the tenant itself owns.
    if ctx.is_tenant_admin:
        allowed_agent_ids = None
    else:
        allowed_agent_ids = _resource_ids(ctx, "agent", "read", permission="agent.read")
    for profile, tenant_default in _tenant_agent_candidates(ctx):
        if (allowed_agent_ids is not None
                and f"agent:{profile.id}" not in allowed_agent_ids
                and not _tenant_shared_default_agent(
                    ctx, profile.id, "agent.read", tenant_default=tenant_default)):
            continue
        can_chat, unavailable_reason = _workbench_chat_readiness(
            ctx, profile.id, tenant_default=tenant_default)
        yield profile, tenant_default, can_chat, unavailable_reason


def _workbench_empty_reason(ctx: "RequestContext") -> str:
    """Why the workbench came back with no card: ``no_agents`` or ``no_reachable_agents``.

    An empty gallery used to read "暂无可用智能体" for both causes, which reports
    an authorization gap as "the system has no Agents". The distinction is a
    stable, localizable code and carries no identifiers, so the caller learns
    nothing about Agents it cannot read.
    """
    for _ in _tenant_agent_candidates(ctx):
        return "no_reachable_agents"
    return "no_agents"


def _tenant_agents_projection(ctx: "Optional[RequestContext]") -> Dict:
    """Minimal read-only agent projection for database mode.

    Filters to the tenant-bound agents the caller may see (task 3.8) and returns
    only the fields the workbench card gallery needs (never workspace paths /
    credentials). Uses the same whitelist as the legacy workbench projection.
    ``is_default`` reflects the caller's *tenant-bound* default agent (task 3.8)
    — never the global default — so the same global Agent bound to two tenants
    is only marked default for the tenant that actually selected it.

    An empty result carries ``empty_reason`` so the console can tell "there is
    no Agent" from "there is nothing *you* may reach". Empty is still a
    *successful* read: the reason is orthogonal to the loading/failed states.
    """
    if ctx is None:
        return {"agents": []}
    agents = []
    for profile, tenant_default, can_chat, unavailable_reason in _iter_tenant_agents(ctx):
        agents.append({
            "id": profile.id,
            "name": profile.name,
            "description": profile.description or "",
            "avatar": profile.avatar or None,
            "is_default": bool(profile.id == tenant_default),
            "can_chat": can_chat,
            "unavailable_reason": unavailable_reason,
        })
    data: Dict = {"agents": agents}
    if not agents:
        reason = _workbench_empty_reason(ctx)
        data["empty_reason"] = reason
        if reason == "no_reachable_agents":
            # The tenant admin exemption usually hides this from the only people
            # who can fix it, so the report has to come from the affected read.
            logger.warning(
                "[Workbench] user %s in tenant %s sees no Agent although the"
                " tenant has candidate Agents: every candidate is unreachable"
                " with the caller's grants",
                getattr(ctx, "user_id", None), getattr(ctx, "tenant_id", None),
            )
    return data


def _personal_agents_projection(ctx: "RequestContext") -> Dict:
    """The caller's **own** private Agents, with the verbs they may use on them.

    The filter runs on the server: the payload contains only Agents this member
    owns in the current tenant, so the console never receives another member's
    Agent and has nothing to filter out on the client. Each row carries finite
    action flags derived from the *same* ownership facts the write paths enforce
    (task 8.1/8.3):

    * ``edit``/``enable`` — ownership of the object (a private owner may always
      maintain their own Agent);
    * ``delete`` — only a ``user_created`` binding. The system-provisioned
      assistant is not the member's to delete, and ``unknown`` (pre-column rows)
      is treated as system-made rather than guessed at (task 4.5);
    * ``configure_personal`` — the personal parameter surface for this Agent.

    Never derived from a role name, a permission count or the caller's page
    availability: a caller who can open the page still gets ``delete: false`` for
    an object they do not own.
    """
    from auth.service import SUPPLIED_ASSISTANT_ORIGINS, get_identity_service

    service = get_identity_service()
    mine = service.private_agent_ids(ctx.tenant_id, ctx.user_id)
    out = []
    for data in _tenant_agents_admin_projection(ctx)["agents"]:
        agent_id = str(data.get("id") or "")
        if agent_id not in mine:
            continue
        binding = service.get_agent_binding(agent_id) or {}
        origin = str(binding.get("origin") or "unknown")
        row = dict(data)
        row["scope"] = "private"
        row["origin"] = origin
        row["is_system_assistant"] = origin in SUPPLIED_ASSISTANT_ORIGINS
        row["actions"] = {
            "edit": True,
            "enable": True,
            "delete": bool(origin == "user_created"),
            "configure_personal": True,
        }
        out.append(row)
    return {"agents": out, "scope": "self",
            "default_agent_id": service.member_default_agent_id(
                ctx.tenant_id, ctx.user_id) or ""}


def _tenant_agents_admin_projection(ctx: "Optional[RequestContext]") -> Dict:
    """Tenant-scoped management projection for the console's Agent pages.

    Same visibility and readiness as :func:`_tenant_agents_projection`, but keeps
    the editable Agent fields (``model``/``bot_type``, the digital-employee
    profile, asset selections) that the configuration pane round-trips on save.
    The console reads this projection, edits a field and writes the whole form
    back, so a read that dropped ``model`` would make the next save silently
    clear the pin back to "follow the global model".

    Workspace paths are still withheld — this is a tenant-facing read, not the
    local snapshot. ``revision`` and ``channel_instances`` are deliberately
    absent as well: both are instance-wide, so exposing them would leak other
    tenants' state and make one tenant's write invalidate another's revision.
    """
    from agent.admin import AgentAdminService

    shared_base = AgentAdminService._shared_knowledge_base()
    agents = []
    for profile, tenant_default, can_chat, unavailable_reason in _iter_tenant_agents(ctx):
        data = profile.to_dict()
        data.pop("workspace", None)
        data["is_default"] = bool(profile.id == tenant_default)
        data["can_chat"] = can_chat
        data["unavailable_reason"] = unavailable_reason
        # The console renders the shared/own knowledge toggle from this. Derived
        # from data-root ownership, so a default Agent moved into a workspace of
        # its own reports "own" — the mode the knowledge page actually renders.
        data["knowledge_mode"] = AgentAdminService._knowledge_mode_of(profile, shared_base)
        # Whether *this caller* may write this Agent's knowledge base, from the
        # same decision the write path enforces (data root + Agent ownership).
        # The console renders its write affordances from this, so it never
        # offers an action the request would 403.
        data["can_write_knowledge"] = _knowledge_write_authorized(ctx, profile.id)
        agents.append(data)
    return {"agents": agents, "default_agent_id": _tenant_default_agent_id(ctx)}


def _agent_bound_to_tenant(ctx: "Optional[RequestContext]", agent_id: str) -> bool:
    """True when ``agent_id`` is bound to the tenant ``ctx`` has selected.

    The tenant binding is the isolation boundary every business read and every
    chat target is checked against (``_require_tenant_agent_binding`` on the send
    path). A caller with no tenant selected owns no Agent here, so the answer is
    False rather than "unscoped".
    """
    if ctx is None or not getattr(ctx, "tenant_id", None) or not agent_id:
        return False
    from auth.service import get_identity_service
    binding = get_identity_service().get_agent_binding(agent_id)
    return bool(binding and binding.get("tenant_id") == ctx.tenant_id)


def _workbench_chat_readiness(ctx: "Optional[RequestContext]",
                              agent_id: str,
                              tenant_default: Optional[str] = None,
                              ) -> Tuple[bool, Optional[str]]:
    """Whether the caller may start a chat with ``agent_id`` right now.

    Returns ``(can_chat, unavailable_reason)``. ``unavailable_reason`` is a
    stable, localizable code (never an internal config leak); it is set only
    when the caller is *read* but not *execution*-authorized for the target.

    With no request context (``ctx is None``) readiness fails closed: the legacy
    consumer that used to be open was retired together with legacy identity mode,
    so no card is advertised as runnable.

    Database mode: mirrors the send-path gates exactly — the target must be bound
    to the caller's selected tenant, the caller must be authorized for the
    functional ``chat.use`` (platform/tenant admin bypass, matching
    ``_require_chat_use``) AND be authorized for ``agent.use`` on this agent
    (platform admin and the tenant admin that owns it bypass, matching
    ``_require_agent_action(..., "use", "agent.use")``). A caller that only has
    ``agent.read`` sees the card with ``can_chat=False`` and a permission reason
    — never the historical ``runtime_not_enabled`` version-closure message.
    """
    if ctx is None:
        return False, "unauthorized"
    # Binding first: the send path refuses an Agent another tenant owns
    # (``_require_tenant_agent_binding`` 404), and a platform admin passes
    # ``check_resource_action`` unconditionally, so this is the check that keeps
    # the card's promise and the send path from disagreeing.
    if not _agent_bound_to_tenant(ctx, agent_id):
        return False, "permission_denied"
    # agent.use: platform admin passes via ``check_resource_action``, and a
    # tenant admin passes for an Agent its own tenant owns — the same rule the
    # send path enforces, so the card never promises a chat the send would deny.
    from auth.service import get_identity_service
    svc = get_identity_service()
    allowed = (_tenant_admin_owns_agent(ctx, agent_id)
               or _tenant_shared_default_agent(
                   ctx, agent_id, "agent.use", tenant_default=tenant_default)
               or svc.check_resource_action(
                   ctx.user_id, ctx.tenant_id, "agent", f"agent:{agent_id}",
                   "use", permission="agent.use"))
    if not allowed:
        return False, "permission_denied"
    # Functional chat.use: platform/tenant admin bypass; members need the grant.
    if not (ctx.is_platform_admin or ctx.is_tenant_admin
            or "chat.use" in (ctx.permissions or ())):
        return False, "permission_denied"
    return True, None


def _audit_instance_roster_write(ctx, channel_type: str, result) -> None:
    """Audit a platform-level channel binding change (task 4.7).

    The instance roster lives in ``team.json``, so unlike the tenant-scoped
    writes it has no identity transaction to piggy-back on. Record the action in
    the identity audit trail explicitly so a platform admin rebinding the shared
    routing table is attributable.

    Best effort on purpose: the roster write has already happened and is live,
    so a broken audit sink must not turn a successful bind into a 500 that
    invites the operator to retry (and blind-rebind).
    identity database to write to (``ctx`` is ``None``) and is skipped.
    """
    if ctx is None:
        return
    try:
        from auth.service import get_identity_service

        get_identity_service().record_audit(
            action="channel.instance.bind",
            target="channel:%s" % (result.get("instance_id") or channel_type),
            actor_user_id=ctx.user_id,
            actor_username=ctx.username,
            tenant_id=ctx.tenant_id,
            redacted_changes={
                "channel_type": channel_type,
                "agent_id": result.get("agent_id") or "",
                "members": list(result.get("members") or []),
            },
        )
    except Exception as e:  # pragma: no cover - audit sink is best effort
        logger.warning(
            f"[WebChannel] Channel bind audit unavailable for "
            f"'{channel_type}': {e}"
        )


def _bind_channel_instance(channel_type: str, instance_id: str = "", agent_id: str = "", members=None):
    """Point one channel instance at an Agent (and team), hot-swapping without a restart.

    The binding lives on the channel instance itself (channel_instances[].agent_id
    in team.json), the single source of truth for routing. For a single-instance
    channel the instance id is just the channel type. An empty agent_id unbinds it
    (falls back to the default Agent).

    Rebinding only changes *which* Agent inbound messages route to — the
    credentials and connection are untouched — so there is no reason to tear
    down and re-establish the IM link. We persist the new binding and then set
    ``bound_agent_id`` live on the running channel; the next inbound message
    reads the updated value. This avoids the reconnect storm a restart caused
    when the user flipped the picker a few times.
    """
    from channel.channel_instances import upsert_instance

    ctype = (channel_type or "").strip().lower()
    if not ctype:
        raise ValueError("channel_type is required")
    target_id = (instance_id or "").strip() or ctype
    agent_id = (agent_id or "").strip()

    inst = upsert_instance(
        conf(),
        channel_type=ctype,
        instance_id=target_id,
        agent_id=agent_id,
        members=members,
    )

    try:
        import sys
        app_module = sys.modules.get("__main__") or sys.modules.get("app")
        mgr = getattr(app_module, "_channel_mgr", None) if app_module else None
        channel = mgr.get_channel(target_id) if mgr else None
        if channel is not None:
            # Live-update owner + team on the running instance. Empty owner means
            # "follow the default Agent". No restart: this only changes routing.
            channel.bound_agent_id = agent_id
            channel.members = list(inst.members or [])
            logger.info(
                f"[WebChannel] Channel '{target_id}' rebound to "
                f"'{agent_id or 'default'}' with team {inst.members or []} (no restart)"
            )
    except Exception as e:
        logger.error(
            f"[WebChannel] Failed to hot-rebind channel '{target_id}': {e}",
            exc_info=True,
        )

    return {
        "instance_id": inst.instance_id,
        "agent_id": inst.agent_id,
        "members": list(inst.members or []),
    }


def _reload_agent_runtime(service, changed_agent_ids=None) -> None:
    """Re-point the live runtime at a freshly loaded roster.

    This runs inside the roster-edit request, so it must stay cheap. The old
    implementation tore everything down - stop every scheduler, drop every
    cached session, then rebuild all of them - which grew linearly with the
    number of Agents (each rebuild reloads dozens of skills). Editing one
    Agent's name should not cost a full-fleet reload.

    Instead we reconcile incrementally:
      * swap the registry/router (always cheap),
      * start a scheduler only for Agents that gained one, stop those that
        disappeared, and leave already-running ones untouched,
      * evict only the sessions of the Agents that actually changed, so their
        next turn picks up the new name / model / persona. Everyone else keeps
        their warm cache.

    ``changed_agent_ids`` narrows the session eviction to just the edited
    Agents. When omitted we fall back to evicting nothing extra beyond the
    add/remove diff, since pure metadata edits without an id (e.g. binding
    changes) touch no cached runtime.
    """
    from agent.registry import set_agent_registry
    from agent.routing import AgentRouter, set_agent_router

    settings = service._load()
    registry = service._registry(settings)
    router = AgentRouter.from_config(settings, registry)
    set_agent_registry(registry)
    set_agent_router(router)

    from bridge.bridge import Bridge
    bridge = Bridge()
    agent_bridge = getattr(bridge, "_agent_bridge", None)
    if agent_bridge is None:
        return

    agent_bridge.agent_registry = registry
    agent_bridge.agent_router = router

    # Reconcile schedulers against what is already running, rather than
    # stopping and recreating the whole set.
    from agent.tools.scheduler.integration import init_scheduler, stop_scheduler
    live_ids = {p.id for p in registry.list(include_disabled=False)}
    previously = set(agent_bridge.scheduler_agent_ids)

    for agent_id in previously - live_ids:
        try:
            stop_scheduler(agent_id)
        except Exception as e:
            logger.warning(f"[WebChannel] stop_scheduler({agent_id}) failed: {e}")
        agent_bridge.scheduler_agent_ids.discard(agent_id)

    for profile in registry.list(include_disabled=False):
        if profile.id in previously:
            continue  # already has a running scheduler; init_scheduler is a no-op
        if init_scheduler(agent_bridge, profile.workspace, profile.id):
            agent_bridge.scheduler_agent_ids.add(profile.id)
    agent_bridge.scheduler_initialized = bool(agent_bridge.scheduler_agent_ids)

    # Drop cached runtimes only for the Agents whose definition changed, so the
    # edit takes effect on their next turn without wiping everyone's session.
    for agent_id in (changed_agent_ids or []):
        try:
            agent_bridge.clear_agent(agent_id)
        except Exception as e:
            logger.warning(f"[WebChannel] clear_agent({agent_id}) failed: {e}")


class AgentsHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            # The workbench (use-Agents) page asks for a minimal read-only
            # projection. Without the view param the default management
            # snapshot is returned intact so Desktop and existing pickers keep
            # their contract.
            params = web.input(view='')
            with _db_scope() as ctx:
                _require_read_permission(ctx, "agent.read")
                if params.view == 'workbench':
                    return json.dumps(
                        {"status": "success", **_tenant_agents_projection(ctx)},
                        ensure_ascii=False,
                    )
                if params.view == 'personal':
                    # 「我的智能体」: the caller's own private Agents only, with
                    # per-object verbs (task 8.1). A member with no tenant
                    # selected owns nothing here, so this refuses rather than
                    # falling back to a tenant-wide read.
                    if ctx is None or not ctx.tenant_id:
                        return json.dumps(
                            {"status": "error", "code": "no_tenant",
                             "message": "a tenant must be selected"},
                            ensure_ascii=False)
                    return json.dumps(
                        {"status": "success", **_personal_agents_projection(ctx)},
                        ensure_ascii=False,
                    )
                if ctx is not None:
                    # database mode: only the caller's tenant-bound agents, and
                    # never expose workspace paths. The management read keeps the
                    # editable fields the console's Agent pages round-trip on
                    # save; the workbench's minimal read is served above. Fall
                    # through to the snapshot path only in legacy mode.
                    return json.dumps(
                        {"status": "success", **_tenant_agents_admin_projection(ctx)},
                        ensure_ascii=False,
                    )
            return json.dumps(
                {"status": "success", **_agent_admin_service().snapshot()},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error(f"[WebChannel] Agents API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                body = json.loads(web.data())
                action = body.get("action")
                service = _agent_admin_service()
                revision = body.get("revision") or None
                agent_id = (body.get("id") or "").strip()

                if action == "create":
                    _require_agent_create(ctx)
                    # A tenant's Agent is tenant-scoped at birth: it gets a
                    # workspace inside the tenant's own root and a binding to the
                    # tenant, or the creating tenant could never see it. An
                    # explicit workspace from the client still wins.
                    workspace = body.get("workspace") or None
                    if ctx is not None and ctx.tenant_id and not workspace:
                        from agent.registry import _AGENT_ID_RE
                        if not _AGENT_ID_RE.fullmatch(agent_id):
                            return json.dumps({
                                "status": "error",
                                "message": f"invalid agent id: {agent_id!r}"
                            })
                        workspace = _tenant_agent_workspace(ctx, agent_id)
                    result = service.create_agent(
                        agent_id=agent_id,
                        name=body.get("name", ""),
                        # Blank means "put it where a new one goes", which is what
                        # the console sends: it asks for a name, not a path.
                        workspace=workspace,
                        clone_from=body.get("clone_from") or None,
                        avatar=body.get("avatar") or None,
                        description=body.get("description") or None,
                        skills=body.get("skills"),
                        knowledge=body.get("knowledge"),
                        knowledge_mode=body.get("knowledge_mode") or None,
                        revision=revision,
                        position=body.get("position"),
                        category=body.get("category"),
                        tags=body.get("tags"),
                        greeting=body.get("greeting"),
                        persona_summary=body.get("persona_summary"),
                        scene_id=body.get("scene_id"),
                        knowledge_ids=body.get("knowledge_ids"),
                        sops=body.get("sops"),
                        tools_allowlist=body.get("tools_allowlist"),
                        tools_denylist=body.get("tools_denylist"),
                    )
                    if ctx is not None and ctx.tenant_id:
                        _adopt_created_agent_for_tenant(
                            ctx, (result or {}).get("id") or agent_id)
                elif action == "update":
                    _require_agent_action(ctx, agent_id, "edit", "agent.edit")
                    # Owning an Agent does not authorise the dependencies the
                    # save points it at; re-verify the ones this request names
                    # (task 4.4) before the roster is touched.
                    _require_configured_capabilities(ctx, agent_id, body)
                    updates = {
                        "name": body.get("name"),
                        "enabled": body.get("enabled"),
                        "make_default": bool(body.get("make_default", False)),
                        "avatar": body.get("avatar"),
                        "description": body.get("description"),
                        "model": body.get("model"),
                        "bot_type": body.get("bot_type"),
                        "revision": revision,
                    }
                    if "skills" in body:
                        updates["skills"] = body.get("skills")
                    if "knowledge" in body:
                        updates["knowledge"] = body.get("knowledge")
                    for _field in ("position", "category", "tags", "greeting",
                                   "persona_summary", "scene_id", "knowledge_ids",
                                   "sops", "tools_allowlist", "tools_denylist"):
                        if _field in body:
                            updates[_field] = body.get(_field)
                    result = service.update_agent(agent_id, **updates)
                elif action == "archive":
                    _require_agent_action(ctx, agent_id, "edit", "agent.edit")
                    result = service.archive_agent(agent_id, revision=revision)
                elif action == "delete":
                    _require_agent_action(ctx, agent_id, "edit", "agent.edit")
                    _require_deletable_provenance(ctx, agent_id)
                    result = service.delete_agent(agent_id, revision=revision)
                    # The roster entry is gone, but two identity-side records can
                    # still point at it: the tenant's default pointer, and the
                    # tenant binding. The binding must go too — ``_agent_is_usable``
                    # deliberately treats an Agent the registry does not know as
                    # *usable*, so a surviving binding would keep
                    # ``resolved_default_agent_id`` returning a deleted Agent. The
                    # release is not scoped to the caller's tenant: the binding may
                    # belong to another one (a platform admin can delete a roster
                    # entry bound elsewhere), and a tenant-scoped delete would then
                    # match nothing.
                    if ctx is not None:
                        from auth.service import get_identity_service
                        get_identity_service().release_deleted_agent(
                            agent_id=agent_id, actor_user_id=ctx.user_id)
                elif action == "set_default":
                    # Choosing the tenant's default Agent is a tenant-level act:
                    # that Agent is the entry every member shares, so it does not
                    # belong to the per-Agent edit surface and must not follow an
                    # ``agent.edit`` resource grant. The service still enforces
                    # that the target belongs to the caller's tenant.
                    if ctx is None or not ctx.tenant_id:
                        _raise_forbidden()
                    if not (ctx.is_platform_admin or ctx.is_tenant_admin):
                        _raise_forbidden()
                    from auth.service import get_identity_service
                    identity = get_identity_service()
                    if agent_id not in identity.tenant_agent_ids(ctx.tenant_id):
                        raise web.HTTPError(
                            "404 Not Found", {"Content-Type": "application/json"},
                            json.dumps({"status": "error",
                                        "message": "agent is not bound to this tenant",
                                        "code": "not_found"}))
                    result = identity.appoint_tenant_default_agent(
                        tenant_id=ctx.tenant_id, agent_id=agent_id,
                        actor_user_id=ctx.user_id)
                elif action == "set_knowledge_mode":
                    # A filesystem toggle (symlink vs own dir), not a roster edit, so
                    # it doesn't participate in the roster revision guard.
                    _require_agent_action(ctx, agent_id, "edit", "agent.edit")
                    result = service.set_knowledge_mode(agent_id, body.get("mode", ""))
                elif action == "bind_channel_instance":
                    # The instance roster (channel_instances[].agent_id in the
                    # shared team.json) decides where *every* tenant's inbound IM
                    # traffic routes, so it is platform control-plane state, not a
                    # tenant-agent edit: a tenant-scoped ``agent.edit`` over its
                    # own Agent must not be able to rewrite it. The tenant's own
                    # channels live in the identity database and are managed
                    # through /api/tenant/channels. Platform console owns the
                    # shared team.json roster binding.
                    platform_ctx = _require_platform_console()
                    _require_agent_action(platform_ctx, agent_id, "edit", "agent.edit")
                    # members: list => set team; omitted/None => leave team untouched
                    raw_members = body.get("members", None)
                    members = raw_members if isinstance(raw_members, list) else None
                    result = _bind_channel_instance(
                        channel_type=body.get("channel_type", ""),
                        instance_id=body.get("instance_id", ""),
                        agent_id=agent_id,
                        members=members,
                    )
                    _audit_instance_roster_write(
                        platform_ctx, body.get("channel_type", ""), result)
                else:
                    return json.dumps({
                        "status": "error", "message": f"unknown action: {action}"
                    })
                # Only the edited Agent needs its cached runtime dropped; a create
                # has no live sessions yet. bind_channel_instance hot-updates the
                # running channel's binding in place (see _bind_channel_instance),
                # so it neither restarts a channel nor touches the roster runtime.
                if action == "bind_channel_instance":
                    return json.dumps(
                        {"status": "success", "result": result},
                        ensure_ascii=False,
                    )
                changed = None
                if action in ("update", "archive", "delete", "set_knowledge_mode"):
                    changed = [agent_id] if agent_id else None
                _reload_agent_runtime(service, changed_agent_ids=changed)
                # Hand back the fresh revision so a client making rapid successive
                # edits (e.g. ticking skill checkboxes) can chain them without a
                # full reload and without tripping the stale-roster guard.
                try:
                    revision_after = service.snapshot().get("revision")
                except Exception:
                    revision_after = None
                return json.dumps(
                    {"status": "success", "result": result, "revision": revision_after},
                    ensure_ascii=False,
                )
        except web.HTTPError:
            # A guard's structured refusal (403/404/409… with its machine-readable
            # ``code``) must reach the client as-is. The generic branch below
            # stringifies it to ``"403"`` and drops the code, which turns an
            # authorization refusal into an opaque handler error.
            raise
        except Exception as e:
            from agent.admin import StaleRosterError
            code = None
            if isinstance(e, StaleRosterError):
                web.ctx.status = "409 Conflict"
                code = "stale_roster"
            logger.error(f"[WebChannel] Agents POST error: {e}")
            return json.dumps({"status": "error", "message": str(e), "code": code})


class AgentCoreFileHandler:
    def GET(self, agent_id: str, filename: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                resolved = _require_tenant_agent_binding(ctx, agent_id)
                # Core files carry the Agent's prompt/model configuration, so
                # reading them owes the same edit grant as writing them: being
                # able to reach the Agent is not a licence to read its config.
                _require_agent_action(ctx, resolved, "edit", "agent.edit")
                result = _agent_admin_service().read_core_file(resolved, filename)
            return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def PUT(self, agent_id: str, filename: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data())
            with _db_scope() as ctx:
                resolved = _require_tenant_agent_binding(ctx, agent_id)
                _require_agent_action(ctx, resolved, "edit", "agent.edit")
                result = _agent_admin_service().write_core_file(
                    resolved,
                    filename,
                    body.get("content"),
                    body.get("revision", ""),
                )
                try:
                    from bridge.bridge import Bridge
                    agent_bridge = getattr(Bridge(), "_agent_bridge", None)
                    if agent_bridge is not None:
                        agent_bridge.clear_agent(resolved)
                except Exception as e:
                    logger.warning(
                        f"[WebChannel] Failed to evict edited agent={resolved}: {e}"
                    )
            return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            from agent.admin import StaleAgentFileError
            if isinstance(e, StaleAgentFileError):
                web.ctx.status = "409 Conflict"
            return json.dumps({"status": "error", "message": str(e)})


# An emoji costs nothing to store or serve, so it is the default way to tell
# Agents apart; an uploaded picture sets the field to this token instead and the
# bytes live beside the other shared assets.
AVATAR_IMAGE_TOKEN = "image"
AVATAR_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def _avatar_path(agent_id: str) -> Optional[str]:
    from common.state_dir import shared_root

    base = shared_root() / "avatars"
    for suffix in AVATAR_TYPES:
        candidate = base / f"{agent_id}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


class AgentAvatarHandler:
    def GET(self, agent_id: str):
        with _db_scope() as ctx:
            resolved = _require_tenant_agent_binding(ctx, agent_id)
            # Seeing an avatar is a read of the Agent, not a change to it, so a
            # member who can use the Agent can still see the roster image; an
            # unbound/foreign Agent is refused before any bytes are served.
            _require_agent_action(ctx, resolved, "read", "agent.read")
            path = _avatar_path(resolved)
        if not path:
            web.ctx.status = "404 Not Found"
            web.header('Content-Type', 'application/json; charset=utf-8')
            return json.dumps({"status": "error", "message": "no avatar"})
        with open(path, "rb") as handle:
            data = handle.read()
        web.header('Content-Type', AVATAR_TYPES[os.path.splitext(path)[1].lower()])
        # Content-addressed by the caller via ?v=, so it can be cached hard.
        web.header('Cache-Control', 'private, max-age=86400')
        return data

    def POST(self, agent_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from common.state_dir import shared_root
            from agent.registry import get_agent_registry

            with _db_scope() as ctx:
                resolved = _require_tenant_agent_binding(ctx, agent_id)
                _require_agent_action(ctx, resolved, "edit", "agent.edit")
                agent_id = resolved
                return self._store_avatar(agent_id, shared_root, get_agent_registry)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Agent avatar upload error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def _store_avatar(self, agent_id, shared_root, get_agent_registry):
        get_agent_registry().get(agent_id, require_enabled=False)
        # Read the multipart body raw. web.input() decodes it as UTF-8, which
        # dies on the first non-text byte of an image (a PNG starts with the
        # byte 0x89) with "utf-8 codec can't decode byte 0x89". rawinput hands
        # back the bytes untouched, the same path the knowledge upload uses.
        params = _raw_web_input()
        upload = params.get("avatar")
        if upload is None:
            return json.dumps({"status": "error", "message": "avatar file required"})
        filename = getattr(upload, "filename", "") or ""
        raw = _read_uploaded_file_bytes(upload)
        if not raw:
            return json.dumps({"status": "error", "message": "avatar file required"})
        if len(raw) > MAX_AVATAR_BYTES:
            return json.dumps({"status": "error", "message": "avatar exceeds 2 MiB"})
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in AVATAR_TYPES:
            return json.dumps({
                "status": "error",
                "message": f"unsupported image type: {suffix or 'unknown'}",
            })

        base = shared_root() / "avatars"
        base.mkdir(parents=True, exist_ok=True)
        # Drop any other extension first, so one Agent never ends up with
        # two avatar files and a resolution order deciding which one wins.
        for other in AVATAR_TYPES:
            stale = base / f"{agent_id}{other}"
            if other != suffix and stale.is_file():
                try:
                    stale.unlink()
                except OSError:
                    pass
        target = base / f"{agent_id}{suffix}"
        tmp = base / f".{agent_id}{suffix}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)

        service = _agent_admin_service()
        result = service.update_agent(agent_id, avatar=AVATAR_IMAGE_TOKEN)
        # An avatar is a file plus a metadata flag; it changes nothing about
        # routing, sessions or schedulers. Skipping the full runtime reload
        # keeps the upload instant instead of tearing everything down.
        # Hand back the fresh revision so the console can patch its roster in
        # place without a full reload and without going stale on the next edit.
        revision = service.snapshot().get("revision")
        return json.dumps(
            {"status": "success", "result": result, "revision": revision},
            ensure_ascii=False,
        )


def _annotate_sessions_with_projects(store, result: dict, agent_id: Optional[str],
                                    user_id: Optional[str] = None) -> None:
    """Attach each session's project space, and say how to group the list.

    ``group_mode`` is decided here rather than in the browser because the client
    only ever holds one page: whether more than one space is in play is a fact
    about all sessions, not about the fifty currently on screen.

    - ``time``    one space in use (the common case) - group by 今天/昨天/更早,
                  exactly as before projects existed.
    - ``project`` several spaces in use - group by project, so multi-project
                  users can find a conversation by where it belongs.
    """
    from agent.workspace import project_store
    from common.state_dir import state_root_str

    project_map = project_store.get_project_map(agent_id)
    default_workspace = state_root_str()

    for session in result.get("sessions") or []:
        path = project_map.get(session["session_id"])
        session["project"] = (
            {"path": path, "name": project_store.display_name_for(path)}
            if path else None
        )

    # Distinct spaces across every web session, default workspace included as
    # one space when any session is still using it.
    space_paths = set()
    uses_default = False
    for sid in store.list_session_ids(channel_type="web", user_id=user_id):
        path = project_map.get(sid)
        if path:
            space_paths.add(path)
        else:
            uses_default = True

    result["space_count"] = len(space_paths) + (1 if uses_default else 0)
    result["group_mode"] = "project" if result["space_count"] > 1 else "time"
    result["default_workspace"] = default_workspace
    # The user's chosen sidebar order of spaces (project paths + the default
    # sentinel). The client uses it to sort project groups; unspecified spaces
    # fall back after the ordered ones.
    result["project_order"] = project_store.get_order()


def _agent_badge(profile) -> dict:
    return {"id": profile.id, "name": profile.name, "avatar": profile.avatar or ""}


def _roster_from_members(host_agent_id: str, members) -> List[dict]:
    """Badge every reachable member of a conversation, host first."""
    from agent.registry import get_agent_registry

    if not members:
        return []
    registry = get_agent_registry()
    roster: List[dict] = []
    for agent_id in [host_agent_id, *members]:
        if any(item["id"] == agent_id for item in roster):
            continue
        try:
            roster.append(_agent_badge(registry.get(agent_id)))
        except Exception:
            continue
    return roster


def _session_roster(session_id: str, host_agent_id: str) -> List[dict]:
    """Everyone who can be addressed in this conversation, host included.

    Empty for a conversation nobody was invited into, which is every
    conversation until the user says otherwise.
    """
    from agent.workspace import session_prefs

    try:
        members = session_prefs.get_prefs(session_id, host_agent_id).get("members")
    except Exception as e:
        logger.debug(f"[WebChannel] roster lookup failed for {session_id}: {e}")
        return []
    return _roster_from_members(host_agent_id, members)


def _addressed_agent_id(text: str, roster: List[dict]) -> str:
    """The teammate this message names, or "" when it names nobody.

    Matching accepts the display name as well as the id, because the composer
    writes the name — nobody types ``@agent-17n3e8`` on purpose. Longer labels
    are tried first so that a name containing another name still resolves to
    the one actually written.

    Only a leading mention counts. Naming somebody mid-sentence is usually
    talking *about* them ("ask Ops to..."), not handing them the turn.
    """
    stripped = (text or "").lstrip()
    if not stripped.startswith("@"):
        return ""
    candidates = []
    for item in roster:
        for label in (item.get("name") or "", item.get("id") or ""):
            if label:
                candidates.append((label, item["id"]))
    for label, agent_id in sorted(candidates, key=lambda pair: -len(pair[0])):
        pattern = r"^@" + re.escape(label) + r"(?=[\s，,：:、]|$)"
        if re.match(pattern, stripped, re.IGNORECASE):
            return agent_id
    return ""


def _as_epoch(value) -> int:
    """Best-effort convert a session timestamp into a sortable epoch int.

    New databases store ``last_active``/``created_at`` as integer Unix
    timestamps, but a workspace carried over from an older build may still hold
    them as ``'YYYY-MM-DD HH:MM:SS'`` strings. Parsing those defensively keeps
    the merged session list from crashing the whole API (which would leave the
    web sidebar empty) just because one legacy row can't be ``int()``-ed.
    """
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    from datetime import datetime

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return 0


def _list_sessions_across_agents(page: int, page_size: int,
                                 ctx: "Optional[RequestContext]" = None,
                                 q: str = "",
                                 archived: bool = False) -> dict:
    """One page of every Agent's conversations, merged.

    Sessions are stored one database per Agent, so "all conversations" is a
    merge across files rather than a query. Each Agent is asked for as many rows
    as the requested page could possibly draw from it, because any of them can
    supply the row that sorts into that page.

    Presenting them in one list is what keeps a second Agent from feeling like a
    second account: the alternative, switching the whole console to look at
    another Agent's conversations, makes the roster a tenant selector.

    In database mode ``ctx`` limits the merge to the tenant-bound agents and,
    when a user is present, filters each Agent's sessions to that user.

    A title search gathers all matching summaries before deduplication and
    pagination, so duplicates outside an early candidate page cannot inflate
    the result count or leave later pages short. Message bodies are not read.
    """
    from agent.memory import get_conversation_store
    from agent.memory.conversation_store import normalize_session_search_query
    from agent.registry import get_agent_registry
    from agent.workspace import project_store, session_prefs
    from common.state_dir import state_root_str

    q = normalize_session_search_query(q)
    take = max(1, page) * page_size
    merged: List[dict] = []
    total = 0
    space_paths = set()
    uses_default = False
    user_id = ctx.user_id if ctx else None
    visible = _tenant_ids_for_context(ctx)
    try:
        members_index = session_prefs.members_index()
    except Exception as e:
        # Faces are decoration; losing them must not cost the user the list.
        logger.warning(f"[WebChannel] Could not read session rosters: {e}")
        members_index = {}

    for profile in get_agent_registry().list(include_disabled=False):
        if visible is not None and profile.id not in visible:
            continue
        try:
            store = get_conversation_store(profile.workspace)
            if q:
                matches = []
                search_page = 1
                while True:
                    batch = store.list_sessions(
                        channel_type="web", page=search_page, page_size=500,
                        user_id=user_id, q=q, archived=archived,
                    )
                    rows = batch.get("sessions") or []
                    matches.extend(rows)
                    if not batch.get("has_more") or not rows:
                        break
                    search_page += 1
                chunk = {"sessions": matches, "total": len(matches)}
            else:
                chunk = store.list_sessions(channel_type="web", page=1, page_size=take,
                                            user_id=user_id, archived=archived)
            project_map = project_store.get_project_map(profile.id)
            session_ids = store.list_session_ids(channel_type="web", user_id=user_id,
                                                 archived=archived)
        except Exception as e:
            # One unreadable workspace must not blank out the whole list; the
            # other Agents' conversations are still perfectly readable.
            logger.warning(
                f"[WebChannel] Skipping sessions for agent={profile.id}: {e}"
            )
            continue

        total += chunk.get("total", 0)
        badge = _agent_badge(profile)
        for session in chunk.get("sessions") or []:
            path = project_map.get(session["session_id"])
            session["agent"] = badge
            # Only a conversation with more than one Agent in it needs faces in
            # the list; a solo one reads better as a plain row, exactly as it
            # did before there was a roster.
            roster = _roster_from_members(
                profile.id, members_index.get((profile.id, session["session_id"]))
            )
            if len(roster) > 1:
                session["participants"] = roster
            session["project"] = (
                {"path": path, "name": project_store.display_name_for(path)}
                if path else None
            )
            merged.append(session)

        for sid in session_ids:
            path = project_map.get(sid)
            if path:
                space_paths.add(path)
            else:
                uses_default = True

    # One row per conversation. A session id can exist in more than one Agent's
    # store (an older client once let a conversation change hands mid-way, and
    # each side kept the turns it saw); showing it twice makes both rows light
    # up as "selected". Keep the copy holding the bulk of the conversation —
    # that's the one the user recognises — and let the newest break a tie.
    by_id: Dict[str, dict] = {}
    for session in merged:
        sid = session.get("session_id")
        kept = by_id.get(sid)
        if kept is None or (
            (int(session.get("msg_count") or 0), _as_epoch(session.get("last_active")))
            > (int(kept.get("msg_count") or 0), _as_epoch(kept.get("last_active")))
        ):
            by_id[sid] = session
    total -= len(merged) - len(by_id)
    merged = list(by_id.values())

    # Same ordering the per-Agent query applies, so a merged page looks exactly
    # like a single Agent's page does.
    merged.sort(
        key=lambda s: (
            0 if s.get("pinned") else 1,
            -_as_epoch(s.get("last_active")),
        )
    )
    offset = (max(1, page) - 1) * page_size
    result = {
        "sessions": merged[offset:offset + page_size],
        "total": total,
        "page": max(1, page),
        "page_size": page_size,
        "has_more": total > offset + page_size,
        "space_count": len(space_paths) + (1 if uses_default else 0),
        "default_workspace": state_root_str(),
        "project_order": project_store.get_order(),
    }
    result["group_mode"] = "project" if result["space_count"] > 1 else "time"
    return result


class SessionsHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                params = web.input(
                    page='1', page_size='50', agent_id='', agent='', scope='', q='',
                    archived=''
                )
                from agent.memory.conversation_store import normalize_session_search_query
                try:
                    q = normalize_session_search_query(params.q)
                except ValueError as e:
                    web.ctx.status = '400 Bad Request'
                    return json.dumps({"status": "error", "code": "invalid_query",
                                       "message": str(e)}, ensure_ascii=False)
                page = int(params.page)
                page_size = int(params.page_size)
                archived = str(params.archived or '').strip().lower() in ('1', 'true', 'yes')
                if (params.scope or '').strip() == 'all':
                    if q:
                        result = _list_sessions_across_agents(page, page_size, ctx, q=q,
                                                              archived=archived)
                    elif ctx is not None:
                        result = _list_sessions_across_agents(page, page_size, ctx,
                                                              archived=archived)
                    else:
                        result = _list_sessions_across_agents(page, page_size,
                                                              archived=archived)
                    if q:
                        result["query"] = q
                    return json.dumps({"status": "success", **result}, ensure_ascii=False)

                agent_id = _request_agent_id(params)
                from agent.registry import get_agent_registry
                store = _conversation_store_for(agent_id)
                search_args = {"q": q} if q else {}
                result = store.list_sessions(
                    channel_type="web",
                    page=page,
                    page_size=page_size,
                    user_id=ctx.user_id if ctx else None,
                    archived=archived,
                    **search_args,
                )
                _annotate_sessions_with_projects(
                    store, result, agent_id, user_id=ctx.user_id if ctx else None,
                )
                badge = _agent_badge(
                    get_agent_registry().get(agent_id or None, require_enabled=False)
                )
                for session in result.get("sessions") or []:
                    session["agent"] = badge
                if q:
                    result["query"] = q
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Sessions API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SessionDetailHandler:
    def DELETE(self, session_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        logger.info(f"[WebChannel] DELETE session request: {session_id}")
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})
            params = web.input(agent_id='')
            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_session_scope(
                    ctx, session_id, _request_agent_id(params))

                # Stop any in-flight run first: a reply that lands after the delete
                # would otherwise keep burning tokens for a session nobody can see.
                try:
                    from agent.protocol import get_cancel_registry
                    from bridge.bridge import Bridge
                    scoped = Bridge().get_agent_bridge().scoped_session_key(session_id)
                    cancelled = get_cancel_registry().cancel_session(scoped)
                    if cancelled:
                        logger.info(
                            f"[WebChannel] Cancelled {cancelled} in-flight request(s) "
                            f"for deleted session {session_id}"
                        )
                except Exception as e:
                    logger.warning(f"[WebChannel] Cancel on delete failed: {e}")

                from agent.memory import get_conversation_store
                store = _conversation_store_for(agent_id)
                store.clear_session(session_id)

                # Drop the session's side stores too. Left behind, a stale project
                # binding would keep inflating the "how many spaces are in use"
                # count that decides how the session list is grouped.
                try:
                    from agent.workspace import project_store, session_prefs
                    project_store.forget_session(session_id)
                    session_prefs.forget_session(session_id)
                except Exception as e:
                    logger.debug(f"[WebChannel] Session side-store cleanup skipped: {e}")

                # Also remove the Agent instance from AgentBridge if exists
                try:
                    from bridge.bridge import Bridge
                    ab = Bridge().get_agent_bridge()
                    ab.clear_session(session_id, agent_id=agent_id)
                except Exception:
                    pass

                channel = WebChannel()
                # Drop messages still waiting in the channel queue: processing them
                # after the delete would recreate the session from scratch.
                try:
                    channel.cancel_session(session_id)
                except Exception as e:
                    logger.warning(f"[WebChannel] Failed to drain queue on delete: {e}")
                channel.session_queues.pop(
                    channel._session_queue_key(session_id, agent_id), None
                )

                logger.info(f"[WebChannel] Session deleted: {session_id}")
                return json.dumps({"status": "success"})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Session delete error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def PUT(self, session_id: str):
        """Update a session's title, pinned flag and/or archived flag."""
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})
            body = json.loads(web.data())
            title = (body.get("title") or "").strip()
            pinned = body.get("pinned")
            archived = body.get("archived")
            if not title and pinned is None and archived is None:
                return json.dumps({"status": "error",
                                   "message": "title, pinned or archived required"})

            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_session_scope(
                    ctx, session_id, _request_agent_id(body))

                from agent.memory import get_conversation_store
                store = _conversation_store_for(agent_id)

                found = True
                if title:
                    found = store.rename_session(session_id, title)
                if pinned is not None:
                    found = store.set_pinned(session_id, bool(pinned)) and found
                if archived is not None:
                    found = store.set_archived(session_id, bool(archived)) and found
                if not found:
                    # A session only gets a row once its first message is stored, so
                    # this is also what a pin on a brand-new empty chat looks like.
                    return json.dumps({"status": "error", "message": "session not found"})
                return json.dumps({"status": "success"})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Session update error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


def _session_model_catalog() -> List[dict]:
    """Providers a session may switch to, newest-first within each provider.

    Only providers with a credential on file are offered: listing one without an
    API key would let the user pick a model that fails on the next message.
    The globally active provider is always included, even if its key lives in
    the environment rather than in config.json.
    """
    local_config = conf()
    active_bot_type = local_config.get("bot_type") or ""
    active_provider = "openai" if active_bot_type == const.CHATGPT else active_bot_type
    if local_config.get("use_linkai") and local_config.get("linkai_api_key"):
        active_provider = "linkai"
    active_model = str(local_config.get("model") or "").strip()

    catalog: List[dict] = []
    for pid, pinfo in ConfigHandler.PROVIDER_MODELS.items():
        if pid == "custom" or not pinfo.get("models"):
            continue
        key_field = pinfo.get("api_key_field")
        has_key = bool(key_field and str(local_config.get(key_field) or "").strip())
        if not has_key and pid != active_provider:
            continue
        models = list(pinfo["models"])
        # The user can pin a custom model name to a built-in provider (via the
        # global config / capability "custom model" field). That model won't be
        # in the preset list, so surface it here for the active provider so the
        # chat picker can both display and re-select it.
        if pid == active_provider and active_model and active_model not in models:
            models.insert(0, active_model)
        catalog.append({
            "id": pid,
            "label": pinfo["label"],
            "models": models,
        })

    # User-defined OpenAI-compatible providers carry their own credentials, so
    # offer any that have a key on file (or are the active provider). Their model
    # list combines the provider's configured default with the globally active
    # model when this custom provider is the one in use — otherwise a custom
    # provider added without a preset model would be unselectable in chat.
    try:
        from models.custom_provider import get_custom_providers
        for cp in get_custom_providers():
            cid = cp.get("id")
            if not cid:
                continue
            pid = f"custom:{cid}"
            is_active = pid == active_provider
            has_key = bool(str(cp.get("api_key") or "").strip())
            if not has_key and not is_active:
                continue
            models = []
            cp_model = str(cp.get("model") or "").strip()
            if cp_model:
                models.append(cp_model)
            if is_active and active_model and active_model not in models:
                models.insert(0, active_model)
            if not models:
                # Nothing concrete to select yet (no default model and not the
                # active provider) — skip rather than render an empty group.
                continue
            name = cp.get("name") or cid
            catalog.append({
                "id": pid,
                "label": {"zh": name, "en": name},
                "models": models,
            })
    except Exception as e:
        logger.debug(f"[WebChannel] custom providers unavailable: {e}")

    return catalog


def _session_settings_state(session_id: str, agent_id: Optional[str]) -> dict:
    """Effective model + permission for a session, and what it can be changed to.

    ``source`` tells the UI whether a value is this conversation's own choice or
    inherited, so it can show "follow global" as a real, selectable state instead
    of silently duplicating the global value onto every session.

    The model resolves the same way the runtime does (see AgentLLMModel.model):
    the conversation's pin, else the owning Agent's own default model, else the
    global config. ``source`` is ``session`` / ``agent`` / ``global`` accordingly,
    and ``agent`` carries the Agent's default when it has one, so a fresh chat
    with a specialist Agent shows the model it will really answer with.
    """
    from agent.workspace import session_prefs

    local_config = conf()
    prefs = session_prefs.get_prefs(session_id, agent_id)

    global_bot_type = local_config.get("bot_type") or ""
    global_provider = "openai" if global_bot_type == const.CHATGPT else global_bot_type
    if local_config.get("use_linkai") and local_config.get("linkai_api_key"):
        global_provider = "linkai"
    global_model = local_config.get("model") or ""
    global_permission = permission_global_mode()

    # The default Agent never has a model of its own: it *is* the global choice.
    agent_default = None
    try:
        from agent.registry import get_agent_registry
        registry = get_agent_registry()
        profile = registry.get(agent_id or None, require_enabled=False)
        if profile.id != registry.default_agent_id and profile.model:
            agent_default = {
                "model": profile.model,
                "provider": profile.bot_type or global_provider,
            }
    except Exception as e:
        logger.debug(f"[WebChannel] agent default model unavailable: {e}")

    # Role-default model resolution (spec 5.2): when the conversation has no
    # pin and the owning Agent has no model, a unique role default for the chat
    # capability is a valid "no source" fallback. A role-default *conflict*
    # (two roles pinning chat to different models) is surfaced, not ranked.
    role_default = None
    role_default_state = None
    ident = _current_db_identity()
    if ident:
        try:
            from auth.service import get_identity_service
            svc = get_identity_service()
            resolved = svc.model_defaults_for(ident.user_id, ident.tenant_id, "chat")
            if resolved["status"] == "default":
                role_default = {
                    "model": resolved["model"],
                    "provider": (str(resolved["model"]).split(":", 1)[0])
                    if ":" in str(resolved["model"]) else global_provider,
                }
                role_default_state = "default"
            elif resolved["status"] == "conflict":
                role_default_state = "conflict"
        except Exception:
            role_default_state = None

    if prefs.get("model"):
        effective_model, effective_provider, source = prefs["model"], prefs.get("provider"), "session"
    elif agent_default:
        effective_model, effective_provider, source = agent_default["model"], agent_default["provider"], "agent"
    elif role_default:
        effective_model, effective_provider, source = role_default["model"], role_default["provider"], "role"
    else:
        effective_model, effective_provider, source = global_model, global_provider, "global"

    catalog = _session_model_catalog()
    allowed = _authorized_model_codes()
    selection_required = False
    if allowed is not None:
        # Restrict the offered models to the caller's model.use grant set. Keep
        # the provider groups but drop models the caller may not select, so the
        # picker cannot offer a model the runtime gate would reject.
        filtered = []
        for group in catalog:
            models = [m for m in group.get("models", []) if m in allowed]
            if not models:
                continue
            kept = dict(group)
            kept["models"] = models
            filtered.append(kept)
        catalog = filtered
        # An inherited default (session pin / Agent / role / global) that is not
        # in the grant set must not be reported as the effective model: the
        # runtime gate would reject it. Surface "choose one" instead. A role
        # default is stored as a full ``provider:{pid}:{code}`` resource id, so
        # compare on the trailing code.
        candidate = str(effective_model or "")
        candidate_code = candidate.rsplit(":", 1)[-1] if candidate else ""
        if not allowed or (candidate and candidate_code not in allowed):
            effective_model, effective_provider, source = "", "", "unset"
            selection_required = True

    return {
        "model": {
            "model": effective_model,
            "provider": effective_provider or global_provider,
            "source": source,
            "selection_required": selection_required,
            "global": {"model": global_model, "provider": global_provider},
            "agent": agent_default,
            "role_default": role_default,
            "role_default_state": role_default_state,
            "providers": catalog,
        },
        "permission": (
            {
                # database mode: execution is owned by the caller's role grants
                # (tool.execute + resource grant) and tenant isolation, not a
                # session-level mode. Offer nothing to pin and say so.
                "mode": global_permission,
                "source": "role",
                "global": global_permission,
                "modes": [],
            }
            if _is_database_identity() else
            {
                "mode": (
                    permission_normalize_mode(prefs["permission"], global_permission)
                    if prefs.get("permission") else global_permission
                ),
                "source": "session" if prefs.get("permission") else "global",
                "global": global_permission,
                "modes": list(PERMISSION_MODES),
            }
        ),
        "team": _session_team_state(prefs, agent_id),
    }


def _session_team_state(prefs: dict, agent_id: Optional[str]) -> dict:
    """Who else is on this conversation, and who could be added.

    An archived member is reported but marked unavailable rather than dropped,
    so the roster the user set is what the roster page shows.
    """
    from agent.registry import get_agent_registry

    registry = get_agent_registry()
    owner_id = registry.get(agent_id or None, require_enabled=False).id
    members = []
    for member_id in prefs.get("members") or []:
        try:
            profile = registry.get(member_id, require_enabled=False)
        except Exception:
            members.append({"id": member_id, "name": member_id, "available": False})
            continue
        members.append({
            **_agent_badge(profile),
            "available": profile.enabled and profile.id != owner_id,
        })
    return {
        "owner": _agent_badge(registry.get(owner_id, require_enabled=False)),
        "members": members,
        "candidates": [
            _agent_badge(profile)
            for profile in registry.list(include_disabled=False)
            if profile.id != owner_id
        ],
    }


def _drop_team_runtimes(
    session_id: str, owner_agent_id: Optional[str], *rosters
) -> None:
    """Drop the runtimes a changed roster invalidates, so they rebuild.

    An Agent's toolset is assembled once, when its runtime is built: the
    ``agent_delegate`` gate in ``AgentInitializer._load_tools`` reads the team at
    that moment. The roster itself is read per turn, so an Agent invited after
    the first message would see the team in its prompt while holding no tool to
    hand work to it. Evicting the participants makes the next turn rebuild
    against the new roster; persisted history is restored on rebuild, so no
    conversation content is lost.

    Every participant goes, not just the owner: a teammate cached in this
    session carries a roster in its own prompt too. Unknown or archived ids are
    skipped — the roster may name an Agent that no longer resolves.
    """
    from bridge.bridge import Bridge

    bridge = Bridge().get_agent_bridge()
    # The owner may be unnamed: the console stores a session under the default
    # Agent, so an empty id has to reach clear_session and be resolved there.
    victims = [owner_agent_id]
    for roster in rosters:
        victims.extend(roster if isinstance(roster, (list, tuple)) else [roster])
    seen = set()
    for agent_id in victims:
        if agent_id is not None:
            agent_id = str(agent_id).strip()
            if not agent_id:
                continue
        if agent_id in seen:
            continue
        seen.add(agent_id)
        try:
            bridge.clear_session(session_id, agent_id)
        except Exception as e:  # a bad roster must not fail the settings write
            logger.debug(
                f"[WebChannel] Could not drop runtime for '{agent_id}': {e}"
            )


class SessionSettingsHandler:
    """Per-session model and permission overrides.

    Both are stored outside the sessions table (see session_prefs) so they can be
    set before the conversation has its first message, and both fall back to the
    global config when unset.
    """

    def GET(self, session_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})
            params = web.input(agent='', agent_id='')
            # Apply the DB scope so the model projection sees the caller's
            # model.use grants. Legacy mode is a no-op.
            with _db_scope():
                state = _session_settings_state(
                    session_id, params.agent or params.agent_id or None
                )
            return json.dumps({"status": "success", **state}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Session settings read error: {e}")
            return json.dumps({"status": "error", "message": str(e)})

    def POST(self, session_id: str):
        """Set or clear this session's model / permission.

        Send ``null`` for a field to drop the override and follow the global
        setting again. ``model`` and ``provider`` move together: a model without
        its provider would be routed by the global bot type.
        """
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})

            from agent.workspace import session_prefs
            body = json.loads(web.data() or b"{}")
            agent_id = body.get("agent") or body.get("agent_id")

            # One DB scope for the whole mutation: ``set_prefs`` resolves the
            # tenant's shared root and the echoed state projects the caller's
            # ``model.use`` grants, so both need the request identity active.
            # Running them outside the scope writes the pin to the default
            # agent's workspace and echoes an empty, unselectable state.
            with _db_scope() as ctx:
                updates = {}
                # database mode owns execution through the caller's role grants,
                # so a session permission override is not accepted (storing one
                # that does nothing would be a dead, misleading knob).
                if "permission" in body and not _is_database_identity():
                    mode = body.get("permission")
                    updates["permission"] = (
                        permission_normalize_mode(mode) if mode else None
                    )
                if "model" in body or "provider" in body:
                    model = (body.get("model") or "").strip() or None
                    provider = (body.get("provider") or "").strip() or None
                    # Fine-grained model authorization: an explicit session model
                    # must be within the caller's model.use grant set (platform all
                    # or legacy mode pass). An out-of-scope explicit choice is
                    # rejected here rather than silently rerouted at the call site.
                    if model:
                        _require_model_use(ctx, model)
                    # Clearing the model clears its provider too: a pinned provider
                    # with no model would route the global model to the wrong vendor.
                    updates["model"] = model
                    updates["provider"] = provider if model else None
                previous_members: list = []
                roster_changed = False
                if "members" in body:
                    previous_members = list(
                        session_prefs.get_prefs(session_id, agent_id).get("members") or []
                    )
                    raw = body.get("members")
                    if raw is None:
                        updates["members"] = None
                    elif isinstance(raw, list):
                        updates["members"] = [
                            str(item).strip() for item in raw if str(item).strip()
                        ]
                    else:
                        return json.dumps({
                            "status": "error",
                            "message": "members must be a list of agent ids",
                        })
                    # Compared as sets: the invite order is only the console's
                    # business, and a reorder must not cost every participant a
                    # rebuild.
                    roster_changed = (
                        set(updates["members"] or []) != set(previous_members)
                    )

                if not updates:
                    return json.dumps({
                        "status": "error",
                        "message": "permission, model, provider or members required",
                    })

                session_prefs.set_prefs(session_id, agent_id, **updates)

                if roster_changed:
                    # The team is part of what a runtime is assembled against, so
                    # a changed roster has to retire the old runtimes: otherwise
                    # the next turn reads the team but has no `agent_delegate` to
                    # hand work to it (see _drop_team_runtimes).
                    _drop_team_runtimes(
                        session_id,
                        agent_id,
                        previous_members,
                        updates.get("members") or [],
                    )

                # Retarget the live agent so the change lands on the next message
                # without waiting for a fresh get_agent.
                try:
                    from bridge.bridge import Bridge
                    ab = Bridge().get_agent_bridge()
                    agent = ab.get_cached_agent(session_id, agent_id)
                    if agent is not None:
                        ab.apply_session_prefs(agent, session_id, agent_id)
                except Exception as e:
                    logger.debug(f"[WebChannel] session prefs apply-to-agent skipped: {e}")

                logger.info(
                    f"[WebChannel] Session settings updated: sid={session_id}, {updates}"
                )
                state = _session_settings_state(session_id, agent_id)
            return json.dumps({"status": "success", **state}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Session settings update error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SessionTitleHandler:
    def POST(self, session_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})

            body = json.loads(web.data())
            user_message = body.get("user_message", "")
            assistant_reply = body.get("assistant_reply", "")
            if not user_message:
                return json.dumps({"status": "error", "message": "user_message required"})

            title = _generate_session_title(user_message, assistant_reply, session_id)

            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_session_scope(
                    ctx, session_id, _request_agent_id(body))

                from agent.memory import get_conversation_store
                store = _conversation_store_for(agent_id)
                updated = store.rename_session(session_id, title)
                logger.info(f"[WebChannel] Session title set: sid={session_id}, title='{title}', db_updated={updated}")

                return json.dumps({"status": "success", "title": title}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Title generation error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class PromptOptimizeHandler:
    """Optimize a colloquial user prompt into a structured AI-ready instruction."""

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b"{}")
            user_input = (body.get("input") or "").strip()
            if not user_input:
                return json.dumps({"status": "error", "message": "input required"})

            context_messages = body.get("context_messages", None)

            from agent.chat.session_service import optimize_prompt
            optimized = optimize_prompt(user_input, context_messages)

            return json.dumps(
                {"status": "success", "optimized": optimized},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error(f"[WebChannel] Prompt optimization error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class SessionClearContextHandler:
    def POST(self, session_id: str):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})
            params = web.input(agent_id='')
            raw_body = web.data()
            body = json.loads(raw_body) if raw_body else {}
            requested = _request_agent_id(body) or _request_agent_id(params)

            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_session_scope(ctx, session_id, requested)

                from agent.memory import get_conversation_store
                store = _conversation_store_for(agent_id)
                new_seq = store.clear_context(session_id)

                # Delete the agent instance so a fresh one is created on the next message
                try:
                    from bridge.bridge import Bridge
                    bridge = Bridge()
                    ab = bridge.get_agent_bridge()
                    ab.clear_session(session_id, agent_id=agent_id)
                except Exception:
                    pass

                return json.dumps({"status": "success", "context_start_seq": new_seq})
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Clear context error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class HistoryHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Access-Control-Allow-Origin', '*')
        try:
            params = web.input(session_id='', page='1', page_size='20', agent_id='')
            session_id = params.session_id.strip()
            if not session_id:
                return json.dumps({"status": "error", "message": "session_id required"})

            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_session_owner(ctx, session_id, agent_id)
                from agent.memory import get_conversation_store
                from agent.registry import get_agent_registry
                # Conversation persistence and the cross-Agent history list
                # use each Agent's own workspace. The tenant's shared working
                # directory can differ and contains no history for this Agent.
                store = get_conversation_store(
                    get_agent_registry().get(agent_id, require_enabled=False).workspace
                )
                result = store.load_history_page(
                    session_id=session_id,
                    page=int(params.page),
                    page_size=int(params.page_size),
                    user_id=ctx.user_id if ctx else None,
                )
                for msg in result.get("messages") or []:
                    if msg.get("role") != "assistant":
                        continue
                    _add_subagent_displays(msg.get("steps"))
                    _add_delegate_displays(msg.get("steps"))
                    artifacts = _artifacts_from_steps(msg.get("steps"), session_id)
                    if artifacts:
                        msg["artifacts"] = artifacts
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] History API error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class MessageDeleteHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        web.header('Access-Control-Allow-Origin', '*')
        try:
            data = json.loads(web.data())
            session_id = data.get('session_id', '').strip()
            user_seq = data.get('user_seq')
            delete_user = data.get('delete_user', True)
            cascade = data.get('cascade', False)
            
            if not session_id or user_seq is None:
                return json.dumps({"status": "error", "message": "session_id and user_seq required"})
            
            with _db_scope() as ctx:
                _require_read_permission(ctx, "history.read")
                agent_id = _require_session_scope(
                    ctx, session_id, _request_agent_id(data))

                # 1. Delete from database
                from agent.memory import get_conversation_store
                store = _conversation_store_for(agent_id)
                deleted = store.delete_message_pair(session_id, int(user_seq), delete_user=delete_user, cascade=cascade)

                # 2. Sync agent's in-memory context so its next turn sees the
                # same history as the DB. Handled by the agent_bridge helper.
                try:
                    from bridge.bridge import Bridge
                    Bridge().get_agent_bridge().sync_session_messages_from_store(
                        session_id, agent_id=agent_id
                    )
                except Exception as sync_err:
                    logger.warning(f"[WebChannel] Failed to sync agent memory: {sync_err}")

                return json.dumps({"status": "success", "deleted": deleted}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Message delete error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


#: ``key = value`` forms whose value must never reach a log consumer. ``run.log``
#: is the process-global log, so it carries whatever every handler logged —
#: including request bodies and headers that may hold a vendor token or a
#: session cookie. The key is kept so a line stays diagnosable.
_LOG_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|apikey|authorization|"
    r"access[_-]?key|refresh[_-]?token|cookie|credential|client[_-]?secret|"
    r"session[_-]?id)\b(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|(?:bearer|basic|token|apikey)\s+[^\s,;]+|[^\s,;]+)"
)


def _redact_log_line(line: str) -> str:
    """Mask credential-looking values in one log line (task 4.11).

    The console log view is a debugging aid, not an excuse to hand out
    credentials: the obvious ``key: value`` / ``key=value`` forms are replaced
    with ``key=***``. Deliberately conservative — it masks the value of a small
    set of well-known secret names rather than trying to detect entropy, so a
    normal log line is left byte-identical.
    """
    return _LOG_SECRET_RE.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), line)


def _redact_log_text(text: str) -> str:
    return "\n".join(_redact_log_line(line) for line in text.split("\n"))


class LogsHandler:
    def GET(self):
        # ``run.log`` is the process-global log and mixes every tenant's
        # activity (plus whatever secrets a handler happened to log), so it is
        # platform control-plane data: in database mode only a platform admin
        # may read it. Legacy mode keeps the shared console password.
        _require_platform_console()
        web.header('Content-Type', 'text/event-stream; charset=utf-8')
        web.header('Cache-Control', 'no-cache')
        web.header('X-Accel-Buffering', 'no')

        log_path = os.path.join(get_data_root(), "run.log")

        def generate():
            if not os.path.isfile(log_path):
                yield b"data: {\"type\": \"error\", \"message\": \"run.log not found\"}\n\n"
                return

            # Read last 200 lines for initial display
            try:
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                tail_lines = lines[-200:]
                chunk = _redact_log_text(''.join(tail_lines))
                payload = json.dumps({"type": "init", "content": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode('utf-8')
            except Exception as e:
                yield f"data: {{\"type\": \"error\", \"message\": \"{e}\"}}\n\n".encode('utf-8')
                return

            # Tail new lines
            try:
                with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(0, 2)  # seek to end
                    deadline = time.time() + 600  # 10 min max
                    while time.time() < deadline:
                        line = f.readline()
                        if line:
                            payload = json.dumps({"type": "line",
                                                  "content": _redact_log_line(line)},
                                                 ensure_ascii=False)
                            yield f"data: {payload}\n\n".encode('utf-8')
                        else:
                            yield b": keepalive\n\n"
                            time.sleep(1)
            except GeneratorExit:
                return
            except Exception:
                return

        return generate()


class LogsDownloadHandler:
    """Serve the full run.log as a file download for offline troubleshooting.

    The /api/logs stream only replays the last 200 lines; this returns the whole
    file so users can attach it to a bug report. Like the stream, ``run.log`` is
    process-global data (every tenant's activity), so this is a platform-admin
    surface in database mode; the response is redacted line by line.
    """

    def GET(self):
        _require_platform_console()
        log_path = os.path.join(get_data_root(), "run.log")
        if not os.path.isfile(log_path):
            raise web.notfound()

        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                text = _redact_log_text(f.read())
            data = text.encode('utf-8')
        except Exception as e:
            logger.error(f"[WebChannel] Log download error: {e}")
            raise web.internalerror()

        # Timestamped name so multiple downloads don't overwrite each other.
        fname = f"rongda-ai-{time.strftime('%Y%m%d-%H%M%S')}.log"
        web.header('Content-Type', 'text/plain; charset=utf-8')
        web.header('Content-Disposition', f'attachment; filename="{fname}"')
        web.header('Content-Length', str(len(data)))
        web.header('Cache-Control', 'no-store')
        return data


class AssetsHandler:
    def GET(self, file_path):  # 修改默认参数
        try:
            # 如果请求是/static/，需要处理
            if file_path == '':
                # 返回目录列表...
                pass

            # 获取当前文件的绝对路径
            current_dir = os.path.dirname(os.path.abspath(__file__))
            static_dir = os.path.join(current_dir, 'static')

            full_path = os.path.normpath(os.path.join(static_dir, file_path))

            # 安全检查：确保请求的文件在static目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(static_dir)):
                logger.error(f"Security check failed for path: {full_path}")
                raise web.notfound()

            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                # Browsers routinely probe optional asset variants (e.g. a
                # .ttf fallback declared alongside .woff2 in @font-face);
                # logging these as errors floods the console with harmless
                # noise. Keep it at debug level — real misconfigurations
                # will still surface via the network panel.
                logger.debug(f"Static file not found: {full_path}")
                raise web.notfound()

            # 设置正确的Content-Type
            content_type = mimetypes.guess_type(full_path)[0]
            if content_type:
                web.header('Content-Type', content_type)
            else:
                # 默认为二进制流
                web.header('Content-Type', 'application/octet-stream')

            # 读取并返回文件内容
            with open(full_path, 'rb') as f:
                return f.read()

        except web.HTTPError:
            # The 404 path above already logged at debug; re-raise as-is so
            # web.py returns the original status to the client.
            raise
        except Exception as e:
            logger.error(f"Error serving static file: {e}", exc_info=True)
            raise web.notfound()


def _workspace_service(session_id: str = None, agent_id: str = None):
    from agent.workspace.service import WorkspaceService
    return WorkspaceService(_get_workspace_root(session_id, agent_id))


# System assets (memory / knowledge / persona files) always live in state_root,
# never in a project dir. When a session has a project open, a relative ref to
# one of these resolves against the project and misses; we fall back to the
# system directory so preview/@ still work.
_SYSTEM_ASSET_PREFIXES = ("memory/", "memory\\", "knowledge/", "knowledge\\")
_SYSTEM_ASSET_FILES = ("MEMORY.md", "AGENT.md", "USER.md", "RULE.md")


def _is_system_asset_rel(rel_path: str) -> bool:
    """True if a relative path points at a state_root-anchored system asset."""
    p = (rel_path or "").lstrip("./")
    return p in _SYSTEM_ASSET_FILES or p.startswith(_SYSTEM_ASSET_PREFIXES)


def _system_workspace_service():
    from agent.workspace.service import WorkspaceService
    from common.state_dir import state_root_str
    return WorkspaceService(state_root_str())


def _workspace_system_service(ctx, svc):
    """State-root workspace for system assets, confined to the caller's tenant.

    ``_system_workspace_service()`` resolves ``state_root()``, which with no
    ``agent_id`` in the ambient identity falls back to the *global default*
    Agent. In database mode that would let a non-default tenant read another
    tenant's ``MEMORY.md``/``knowledge/`` through the preview editor's
    state-root fallback. Anchor to the caller's tenant-bound default Agent
    instead; when the tenant has no Agent at all, reuse the session workspace
    root (already tenant-scoped) rather than a global directory.
    """
    if ctx is None:
        return _system_workspace_service()
    from agent.workspace.service import WorkspaceService
    resolved = _resolve_tenant_default_agent(ctx)
    # The default must also pass the private-owner rule: when a tenant has no
    # shared Agent the resolution order can land on a private one, which the
    # caller is not allowed to read. Reuse the tenant-scoped session root then.
    if resolved and not _db_path_owner_forbidden(ctx, resolved):
        try:
            from agent.registry import get_agent_registry
            return WorkspaceService(get_agent_registry().get(resolved).workspace)
        except (KeyError, ValueError, TypeError):
            pass
    return svc


def _workspace_request_scope(ctx, session_id: str, agent_id: Optional[str]) -> str:
    """Validate the file panel's ``agent``/``session`` selectors.

    Returns the resolved (tenant-bound) agent id. An Agent bound to another
    tenant or privately owned by someone else is refused (404/403), and a
    session the caller does not own is refused too, mirroring the other
    session-scoped console routes.
    """
    resolved = _require_tenant_agent_binding(ctx, agent_id)
    _require_private_owner(ctx, resolved)
    if session_id:
        _require_owned_session(ctx, session_id, resolved)
    return resolved


def _visible_entries(ctx, svc, entries: list) -> list:
    """Drop entries the caller may not read (private-Agent ownership).

    Applied before decorating so a hidden entry never gets `raw_url` /
    `preview_url` minted for it, and directory names do not leak either.
    """
    if ctx is None or not getattr(ctx, "tenant_id", None):
        return entries
    roots = _db_file_root_owners(ctx)
    visible = []
    for entry in entries:
        abs_path = entry.get("abs_path") or os.path.join(svc.root, entry.get("path") or "")
        try:
            real = os.path.realpath(abs_path)
        except (TypeError, ValueError):
            continue
        if _db_path_visible(ctx, real, roots):
            visible.append(entry)
    return visible


def _decorate_entry(svc, entry: dict) -> dict:
    """Attach the URLs the frontend needs to preview or download an entry."""
    if entry.get("is_dir"):
        return entry
    abs_path = entry.get("abs_path") or os.path.join(svc.root, entry["path"])
    entry["abs_path"] = abs_path
    entry["raw_url"] = f"/api/file?path={quote(abs_path)}"
    entry["preview_url"] = _build_preview_url(abs_path)
    return entry


class WorkspaceTreeHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(path='', show_hidden='', session='', agent='')
                agent_id = _workspace_request_scope(
                    ctx, params.session or None, params.agent or None)
                svc = _workspace_service(params.session or None, agent_id)
                result = svc.list_dir(params.path, show_hidden=params.show_hidden == '1')
                result["entries"] = [
                    _decorate_entry(svc, e)
                    for e in _visible_entries(ctx, svc, result["entries"])
                ]
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except (ValueError, FileNotFoundError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except Exception as e:
                logger.error(f"[WebChannel] Workspace tree error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class WorkspaceSearchHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(q='', limit='30', session='', agent='')
                try:
                    limit = max(1, min(100, int(params.limit)))
                except (TypeError, ValueError):
                    limit = 30
                agent_id = _workspace_request_scope(
                    ctx, params.session or None, params.agent or None)
                svc = _workspace_service(params.session or None, agent_id)
                result = svc.search(params.q, limit=limit)
                result["results"] = [
                    _decorate_entry(svc, e)
                    for e in _visible_entries(ctx, svc, result["results"])
                ]
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Workspace search error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class WorkspaceResolveHandler:
    """
    Metadata + preview/raw URLs for one entry, given a relative or absolute path.

    Directories resolve as well (the client then browses instead of previewing),
    just without the file URLs.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.protocol.artifact import classify_kind, is_previewable
                params = web.input(path='', session='', agent='')
                raw_path = (params.path or '').strip()
                if not raw_path:
                    return json.dumps({"status": "error", "message": "path is required"})

                agent_id = _workspace_request_scope(
                    ctx, params.session or None, params.agent or None)
                svc = _workspace_service(params.session or None, agent_id)
                if os.path.isabs(os.path.expanduser(raw_path)):
                    abs_path = os.path.realpath(os.path.expanduser(raw_path))
                    # Absolute paths are authorized against the *caller's*
                    # tenant roots, never the static all-tenant list behind
                    # /preview (which has no request identity to scope by).
                    allowed, via = _authorize_db_file_path(ctx, abs_path)
                    if not allowed:
                        if via == "forbidden":
                            raise web.forbidden()
                        raise web.notfound()
                    is_dir = os.path.isdir(abs_path)
                    if not is_dir and not os.path.isfile(abs_path):
                        return json.dumps({"status": "error", "message": "File not found"})
                    kind = "directory" if is_dir else classify_kind(abs_path)
                    entry = {
                        "name": os.path.basename(abs_path),
                        "path": svc.to_rel(abs_path),
                        "abs_path": abs_path,
                        "is_dir": is_dir,
                        "kind": kind,
                        "previewable": (not is_dir) and is_previewable(kind),
                        "size": 0 if is_dir else os.path.getsize(abs_path),
                        "mtime": os.path.getmtime(abs_path),
                    }
                else:
                    try:
                        entry = svc.stat_file(raw_path)
                    except FileNotFoundError:
                        # Memory/knowledge live in the Agent's workspace, not the
                        # project. Retry there so their cards still preview when
                        # a project is open — scoped to the caller's tenant.
                        if _is_system_asset_rel(raw_path):
                            entry = _workspace_system_service(ctx, svc).stat_file(raw_path)
                        else:
                            raise
                    # Relative addressing is not an ownership bypass: the
                    # resolved abs_path decides, so a private Agent workspace
                    # nested under the shared root stays invisible.
                    if not _db_path_visible(ctx, entry["abs_path"]):
                        raise web.notfound()

                # A directory has nothing to serve; the client browses into it.
                if not entry["is_dir"]:
                    entry["raw_url"] = f"/api/file?path={quote(entry['abs_path'])}"
                    entry["preview_url"] = _build_preview_url(entry["abs_path"])
                return json.dumps({"status": "success", "file": entry}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except (ValueError, FileNotFoundError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except Exception as e:
                logger.error(f"[WebChannel] Workspace resolve error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class WorkspaceMetaHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(session='', agent='')
                agent_id = _workspace_request_scope(
                    ctx, params.session or None, params.agent or None)
                svc = _workspace_service(params.session or None, agent_id)
                return json.dumps({"status": "success", **svc.meta()}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Workspace meta error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


def _editable_target(raw_path: str, session_id: str = None, agent_id: str = None,
                     ctx=None):
    """
    Locate a file for the preview panel's text editor: (service, rel_path).

    Narrower than `/api/workspace/resolve`, which only has to serve bytes and so
    accepts anything under the configured serve roots. Reading and writing text
    stay inside the session's workspace (its project dir or the tenant shared
    root), with a tenant-scoped state-root fallback for the memory / knowledge /
    persona assets that live in the Agent's workspace even while a project is
    open.

    An absolute path outside both roots is invisible (404): it must never fall
    back to the global default Agent's workspace or an arbitrary host path.
    """
    svc = _workspace_service(session_id, agent_id)
    system = _workspace_system_service(ctx, svc)
    raw = (raw_path or "").strip()
    expanded = os.path.expanduser(raw)
    if os.path.isabs(expanded):
        real = os.path.realpath(expanded)
        # Second barrier: even if the fallback loop below ever widened, an
        # absolute path must stay inside the caller's tenant roots (platform
        # root only for a platform admin).
        if ctx is not None and getattr(ctx, "tenant_id", None):
            allowed, via = _authorize_db_file_path(ctx, real)
            if not allowed:
                if via == "forbidden":
                    raise web.forbidden()
                raise web.notfound()
        for candidate in (svc, system):
            try:
                return candidate, candidate.to_workspace_rel(real)
            except ValueError:
                continue
        raise web.notfound()
    rel = svc.to_workspace_rel(raw)
    target = svc
    if svc.root != system.root and _is_system_asset_rel(rel) \
            and not os.path.isfile(svc.resolve(rel)):
        target = system
    # Ownership follows the resolved target, never the declared Agent: a private
    # Agent's workspace nested under the tenant shared root must not become
    # readable through a relative path (or via the state-root fallback).
    if ctx is not None and getattr(ctx, "tenant_id", None):
        try:
            visible = _db_path_visible(ctx, target.resolve(rel))
        except ValueError:
            visible = False
        if not visible:
            raise web.notfound()
    return target, rel


def _is_memory_rel(rel_path: str) -> bool:
    """True if a workspace-relative path points at a memory file backed by the
    vector index (so an edit has to be re-embedded, not just written)."""
    p = (rel_path or "").lstrip("./")
    return p == "MEMORY.md" or p.startswith(("memory/", "memory\\"))


def _mark_memory_dirty(agent_id: str = None) -> None:
    """Flag the agent's memory index stale after a console edit to a memory file.

    The index is built from the file contents, so a human edit here must be
    re-embedded the same way an agent's write/edit tool triggers it — otherwise
    semantic search keeps returning the pre-edit text until something else marks
    the store dirty. Best-effort: a failure here must not fail the save.
    """
    try:
        from bridge.bridge import Bridge
        agent = Bridge().get_agent_bridge().get_agent(agent_id=agent_id or None)
        mm = getattr(agent, "memory_manager", None)
        if mm:
            mm.mark_dirty()
    except Exception as e:
        logger.warning(f"[WebChannel] Failed to mark memory index dirty: {e}")


class WorkspaceReadHandler:
    """
    Text content of one workspace file, for the preview panel's editor.

    Returns the `mtime` the client passes back on save and an `editable` flag,
    so the editor never opens a file it would be unable to write back.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(path='', session='', agent='')
                raw_path = (params.path or '').strip()
                if not raw_path:
                    return json.dumps({"status": "error", "message": "path is required"})
                agent_id = _workspace_request_scope(
                    ctx, params.session or None, params.agent or None)
                svc, rel = _editable_target(
                    raw_path, params.session or None, agent_id, ctx=ctx)
                return json.dumps({"status": "success", **svc.read_text(rel)}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except (ValueError, FileNotFoundError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except Exception as e:
                logger.error(f"[WebChannel] Workspace read error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class WorkspaceWriteHandler:
    """
    Save edited text back to a workspace file.

    A human editing a file in the console is not an agent tool call, so the
    session's agent permission mode does not apply here; the guard is the
    workspace boundary enforced by `_editable_target`.

    `expected_mtime` carries the timestamp the editor loaded. When it no longer
    matches, the response is `code: "conflict"` so the client can offer to
    reload or overwrite rather than silently discarding the newer content -
    which the agent may well have written mid-edit.
    """

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            # A console write funnels through the unified origin/CSRF gate before
            # any identity or filesystem work, like every other management write.
            from channel.web.auth_handlers import require_management_write
            require_management_write()
            try:
                from agent.workspace.service import WorkspaceConflictError

                body = json.loads(web.data() or b'{}')
                raw_path = (body.get("path") or "").strip()
                if not raw_path:
                    return json.dumps({"status": "error", "message": "path is required"})
                content = body.get("content")
                if not isinstance(content, str):
                    return json.dumps({"status": "error", "message": "content must be a string"})

                agent_id = _workspace_request_scope(
                    ctx, body.get("session") or None, body.get("agent") or None)
                svc, rel = _editable_target(
                    raw_path, body.get("session") or None, agent_id, ctx=ctx)
                try:
                    result = svc.write_text(rel, content, expected_mtime=body.get("expected_mtime"))
                except WorkspaceConflictError as e:
                    return json.dumps({"status": "error", "code": "conflict", "message": str(e)})

                # A memory file feeds the vector index; re-embed it on edit so search
                # doesn't keep returning the stale pre-edit text.
                if _is_memory_rel(rel):
                    _mark_memory_dirty(agent_id)

                logger.info(f"[WebChannel] Workspace file saved: {result['path']} ({result['size']} bytes)")
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except (ValueError, FileNotFoundError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except PermissionError:
                return json.dumps({"status": "error", "message": "permission denied"})
            except Exception as e:
                logger.error(f"[WebChannel] Workspace write error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


def _project_state(session_id: str, agent_id: str = None) -> dict:
    """Assemble the project picker state: current selection + recents + root."""
    from agent.workspace import project_store
    from common.runtime_identity import RuntimeIdentity, current_identity
    from common import state_dir

    current = project_store.get_project_dir(session_id, agent_id) if session_id else None
    ident = current_identity()
    if ident.user_id and ident.tenant_id:
        # Database mode: resolve against the tenant's trusted shared root (same
        # rule as _get_workspace_root), so the hint points at the caller's
        # tenant root rather than a bare agent id (or host default workspace).
        from auth.service import get_identity_service
        shared = get_identity_service().tenant_shared_root(ident.tenant_id)
        default_workspace = shared or state_dir.state_root_str(ident)
        projects_root = project_store.user_projects_root() or project_store.projects_root()
    else:
        # Legacy mode: default to the Agent this session belongs to so the
        # selector hint matches the file panel's real root in multi-Agent setups.
        default_workspace = state_dir.state_root_str(RuntimeIdentity(agent_id=agent_id))
        projects_root = project_store.projects_root()
    return {
        "current": (
            {"path": current, "name": os.path.basename(current) or current}
            if current else None
        ),
        "default_workspace": default_workspace,
        "projects_root": projects_root,
        "recents": project_store.list_recents(),
    }


class ProjectsHandler:
    """List the project picker state for a session (current + recents)."""

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                params = web.input(session='', agent='')
                state = _project_state(params.session or None, params.agent or None)
                return json.dumps({"status": "success", **state}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Projects list error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class ProjectSelectHandler:
    """Bind a session to a project directory, or clear it (project_dir=null).

    The value may be a relative identifier as returned by the scoped browser or an
    absolute path recorded before this change. Either way it is re-resolved here,
    at *selection* time (task 6.2): the session's durable owner is re-checked
    through the module's own seam, and the path is re-validated through
    ``common.safe_fs``, so a directory replaced by a symlink after the listing --
    or a path that left the caller's root -- refuses instead of binding the
    session to the new target.
    """

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.workspace import project_browser
                from agent.workspace import project_store
                body = json.loads(web.data() or b"{}")
                session_id = (body.get("session") or body.get("session_id") or "").strip()
                agent_id = body.get("agent") or body.get("agent_id")
                if not session_id:
                    return json.dumps({"status": "error", "message": "session is required"})
                _require_owned_session(ctx, session_id, agent_id)
                raw = body.get("project_dir")
                if raw in (None, ""):
                    applied = project_store.set_project_dir(session_id, None, agent_id)
                else:
                    resolved = project_browser.resolve_selection(
                        _project_identity(), raw, session_id=session_id,
                        session_owner=_project_session_owner(ctx, agent_id))
                    applied = project_store.set_project_dir(
                        session_id, resolved, agent_id
                    )
                # Retarget an already-instantiated session agent immediately, so the
                # change takes effect on the next message without a fresh get_agent.
                try:
                    from bridge.bridge import Bridge
                    ab = Bridge().get_agent_bridge()
                    agent = ab.get_cached_agent(session_id, agent_id)
                    if agent is not None and getattr(agent, "apply_project_dir", None):
                        agent.apply_project_dir(applied)
                except Exception as e:
                    logger.debug(f"[WebChannel] project apply-to-agent skipped: {e}")
                state = _project_state(session_id, agent_id)
                return json.dumps({"status": "success", **state}, ensure_ascii=False)
            except (ValueError, FileNotFoundError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Project select error: {e}")
                return _render_project_refusal(e)


class ProjectCreateHandler:
    """Create a new project folder under the projects root and select it."""

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.workspace import project_store
                body = json.loads(web.data() or b"{}")
                session_id = (body.get("session") or body.get("session_id") or "").strip()
                agent_id = body.get("agent") or body.get("agent_id")
                name = (body.get("name") or "").strip()
                if not name:
                    return json.dumps({"status": "error", "message": "name is required"})
                if session_id:
                    _require_owned_session(ctx, session_id, agent_id)
                path = project_store.create_project(name)
                if session_id:
                    project_store.set_project_dir(session_id, path, agent_id)
                    try:
                        from bridge.bridge import Bridge
                        ab = Bridge().get_agent_bridge()
                        agent = ab.get_cached_agent(session_id, agent_id)
                        if agent is not None and getattr(agent, "apply_project_dir", None):
                            agent.apply_project_dir(path)
                    except Exception as e:
                        logger.debug(f"[WebChannel] project apply-to-agent skipped: {e}")
                state = _project_state(session_id or None, agent_id)
                return json.dumps({"status": "success", "path": path, **state}, ensure_ascii=False)
            except (ValueError, FileExistsError) as e:
                return json.dumps({"status": "error", "message": str(e)})
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Project create error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class ProjectOrderHandler:
    """Persist the user's chosen sidebar order of project spaces."""

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.workspace import project_store
                body = json.loads(web.data() or b"{}")
                order = body.get("order")
                if not isinstance(order, list):
                    return json.dumps({"status": "error", "message": "order must be a list"})
                saved = project_store.set_order(order)
                return json.dumps({"status": "success", "order": saved}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Project order error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


class ProjectManageHandler:
    """Rename (PUT) or delete (DELETE) a project record.

    Neither touches the folder on disk: a rename only sets a display name, and a
    delete only forgets the RongAI record and unbinds any sessions (they revert
    to the default workspace). The files stay exactly where they are.
    """

    def PUT(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.workspace import project_store
                body = json.loads(web.data() or b"{}")
                path = (body.get("path") or "").strip()
                if not path:
                    return json.dumps({"status": "error", "message": "path is required"})
                name = project_store.rename_project(path, body.get("name") or "")
                return json.dumps({"status": "success", "name": name}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Project rename error: {e}")
                return json.dumps({"status": "error", "message": str(e)})

    def DELETE(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        with _db_scope() as ctx:
            try:
                from agent.workspace import project_store
                body = json.loads(web.data() or b"{}")
                path = (body.get("path") or "").strip()
                agent_id = body.get("agent") or body.get("agent_id")
                if not path:
                    return json.dumps({"status": "error", "message": "path is required"})
                unbound = project_store.delete_project(path, agent_id)
                return json.dumps({"status": "success", "unbound": unbound}, ensure_ascii=False)
            except web.HTTPError:
                raise
            except Exception as e:
                logger.error(f"[WebChannel] Project delete error: {e}")
                return json.dumps({"status": "error", "message": str(e)})


# Virtual path (Windows only) the legacy picker used to hop between drives. It is
# refused rather than expanded here: a drive list has nothing to do with the
# caller's own projects root, so honouring it could only widen what the picker
# can see (task 6.1).
_DRIVES_SENTINEL = "__DRIVES__"


def _render_project_refusal(exc) -> str:
    """Render a project-browser/import refusal with its status and stable code.

    The console branches on ``code``, so every refusal carries one, and the
    message names the offending *identifier*, never a resolved host path.
    Statuses web.py renders itself (401/403/404) are raised so the body is not
    replaced with an error page; the rest are returned with ``web.ctx.status``
    set.
    """
    from http import HTTPStatus

    from agent.workspace.project_browser import ProjectBrowserError
    from channel.web.project_import import ImportSeamError

    if isinstance(exc, (ProjectBrowserError, ImportSeamError)):
        status = int(getattr(exc, "status", 400) or 400)
        code = str(getattr(exc, "code", "error") or "error")
        message = str(exc)
    else:
        status, code, message = 500, "internal_error", "项目请求处理失败"
    payload = json.dumps({"status": "error", "code": code, "message": message},
                         ensure_ascii=False)
    if status in (401, 403, 404):
        raise web.HTTPError(f"{status} {HTTPStatus(status).phrase}",
                            {"Content-Type": "application/json; charset=utf-8"},
                            payload)
    web.ctx.status = f"{status} {HTTPStatus(status).phrase}"
    return payload


def _project_identity():
    """The verified ambient identity of this request.

    Never a request value: ``_db_scope`` established it from the session and the
    tenant selection, and every project-browser/import call is scoped by it.
    """
    from common.runtime_identity import current_identity
    return current_identity()


def _project_quota_reserve(identity):
    """A quota-reservation callback for one import, bound to the caller's root."""
    from agent.workspace import project_browser
    from channel.web import project_import

    def reserve(plan):
        return project_import.reserve_project_slot(
            identity, plan, project_browser.trusted_root(identity))

    return reserve


def _project_field(params, *names) -> str:
    """The first non-empty field of ``params`` (a dict or a ``web.storage``)."""
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _project_import_binding(source, name) -> Dict[str, Any]:
    """What a local import handle is bound to.

    ``source`` is the grant's identity: the resolved directory the user selected
    and the preview read, compared again at redemption so a handle cannot be
    replayed for a different directory (``_matches_payload`` compares that key).

    ``_name`` is the target name the preview showed. It is recorded under a
    private key because the import must publish *that* name, not whatever the
    publishing request carries: retargeting a confirmed import is a change to
    what the user agreed to, and the value is read back from the handle record
    instead of being re-trusted from the body. The preview's own normalization
    is applied here, so an honest request and its preview cannot disagree on
    spelling.
    """
    return {"source": os.path.realpath(source), "_name": str(name or "").strip()}


def _project_session_id(params) -> str:
    return _project_field(params, "session", "session_id")


def _project_require_session(ctx, params) -> str:
    """Verify the optional session binding an import was requested for.

    An import may name the chat session it is for; when it does, the session must
    be the caller's own and a web session. The binding is checked *before* any
    path is read, and it is re-checked at redemption, so a handle issued for one
    session cannot be redeemed for another.
    """
    session_id = _project_session_id(params)
    if session_id:
        _require_owned_session(ctx, session_id,
                               _project_field(params, "agent", "agent_id") or None)
    return session_id


def _project_session_owner(ctx, agent_id):
    """A ``session_owner`` seam for ``project_browser.resolve_selection``.

    Reports the durable owner of the addressed session the way the module needs
    it, so the re-verification happens inside the selection path instead of being
    assumed from an earlier check. ``_require_owned_session`` already refused
    another member's session and a non-web one; this re-reads the same row. A
    session with no row yet has no owner to conflict with -- the same rule the
    select/create handlers already apply, because a brand-new chat may be bound --
    while a row recorded for another channel type reports a value that can never
    equal a user id, so the module refuses it.
    """

    def lookup(session_id: str):
        resolved = _require_tenant_agent_binding(ctx, agent_id)
        from agent.registry import get_agent_registry
        from agent.memory import get_conversation_store
        try:
            profile = get_agent_registry().get(resolved)
        except (KeyError, ValueError):
            return ""
        store = get_conversation_store(profile.workspace)
        with store._lock:
            con = store._connect()
            try:
                row = con.execute(
                    "SELECT owner, channel_type FROM sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return str(getattr(ctx, "user_id", "") or "")
        owner, channel = row[0], row[1]
        if channel != "web":
            return "\x00not-a-web-session"
        return str(owner or "")

    return lookup


class ProjectBrowseHandler:
    """List the directories inside the caller's own project root (task 6.1).

    Response compatibility is deliberate. ``status``, ``path``, ``parent`` and
    ``dirs`` keep the names the console picker reads today and ``dirs`` entries
    keep ``{name, path}``; what changed is what the values *are* -- relative
    identifiers inside the caller's own root, where ``""`` is the root itself --
    plus three deliberate additions:

    * ``breadcrumbs`` -- the bounded path from the root to ``path`` in the same
      identifiers, ready to be sent back as a ``path`` parameter, so the console
      can render a trail without ever holding a host path;
    * ``scope`` -- the tenant/user the listing was resolved for, taken from the
      verified request context, not from a parameter;
    * ``parent`` -- the parent *inside* the root and ``None`` at the root, so "go
      up" can never climb above the member's private tree.

    A ``path`` recorded before this change arrives as an absolute host path; it is
    accepted only after the server proves it resolves inside the caller's own
    root (the single place that translation happens), and anything else is
    refused with a stable code. Every component is re-resolved per request through
    ``common.safe_fs`` (``O_NOFOLLOW``), so a directory swapped for a symlink
    refuses instead of enumerating whatever it now points at, and a member's
    private tree nested under an outer root is never offered. There is no
    ``agent.read`` or platform-``all`` bypass: ownership is the boundary.
    """

    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                from agent.workspace import project_browser

                params = web.input(path='')
                raw = (getattr(params, 'path', '') or '').strip()
                if raw == _DRIVES_SENTINEL:
                    raise project_browser.ProjectBrowserError(
                        "驱动器列表不在本人项目根内，已拒绝",
                        code=project_browser.CODE_UNSAFE_PATH, status=403)
                identity = _project_identity()
                entry = project_browser.normalize_selection(identity, raw)
                result = project_browser.browse(identity, entry)
                return json.dumps({
                    "status": "success",
                    "path": result["path"],
                    "parent": result["parent"],
                    "dirs": result["dirs"],
                    "breadcrumbs": result["breadcrumbs"],
                    "scope": {"kind": "personal",
                              "tenant_id": ctx.tenant_id,
                              "user_id": ctx.user_id},
                }, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Project browse error: {e}")
            return _render_project_refusal(e)


class ProjectImportPreviewHandler:
    """Preview what a controlled project import would create (tasks 6.3/6.4).

    Two transports, and the transport is what decides the boundary:

    * a **local** (desktop) selection posts ``source`` as an absolute host path
      and must be a loopback request carrying the per-start token
      (``channel.web.project_import``). The preview reports the target, the entry
      and byte counts, what would be skipped and whether something already holds
      the name, and issues the single-use handle the import must present;
    * a **remote** browser cannot see a server path at all: it posts multipart
      ``files`` + ``paths`` and the manifest is previewed from the bytes. The
      session is the grant, and no path is resolved.
    """

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                from agent.workspace import project_browser
                from channel.web import project_import

                if _project_request_is_upload():
                    params = project_import.upload_params()
                    _project_require_session(ctx, params)
                    payload = project_import.preview_upload_request(None, params)
                    return json.dumps({"status": "success",
                                       "transport": "upload", **payload},
                                      ensure_ascii=False)
                body = _chat_body()
                _project_require_session(ctx, body)
                project_import.require_local_transport(body)
                source = _project_field(body, "source", "path", "dir")
                if not source:
                    _chat_error("source is required", "400 Bad Request",
                                "invalid_request")
                identity = _project_identity()
                payload = project_browser.preview_import(
                    identity, source, name=body.get("name"),
                    source_roots=project_import.source_roots())
                handle = project_import.issue_handle(
                    identity, purpose=project_import.PURPOSE_PROJECT_IMPORT,
                    payload=_project_import_binding(source, body.get("name")),
                    session_id=_project_session_id(body))
                return json.dumps({
                    "status": "success",
                    "transport": "local",
                    "handle": handle,
                    "expires_in": project_import.HANDLE_TTL_SECONDS,
                    **payload,
                }, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Project import preview error: {e}")
            return _render_project_refusal(e)


class ProjectImportHandler:
    """Publish a previewed local directory, or an uploaded one (tasks 6.3/6.4).

    The local transport is the dangerous one, because it asks the *server* to
    read a path the *client* named. Four conditions are required before that path
    is resolved at all: loopback, the per-start token, the verified database
    identity, and the single-use handle the preview issued for that exact source.
    A request that merely supplies a path has no handle and is refused with
    ``handle_required``; a handle issued to another member, already consumed, or
    for a different source is refused with ``handle_invalid``.

    The upload transport names no server path, so the session is the grant: the
    manifest is validated as plain downward relative paths, staged inside the
    member's own root and published with one rename -- the same staging, publish,
    rollback and quota code the local copy uses.

    Either way the import never overwrites an existing project, never targets the
    source directory, and leaves nothing behind on cancel or failure.
    """

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_chat_csrf()
                from agent.workspace import project_browser
                from channel.web import project_import

                if _project_request_is_upload():
                    params = project_import.upload_params()
                    _project_require_session(ctx, params)
                    identity = _project_identity()
                    result = project_import.import_upload_request(
                        identity, params,
                        quota_reserve=_project_quota_reserve(identity))
                    return json.dumps({"status": "success",
                                       "transport": "upload", **result},
                                      ensure_ascii=False)

                body = _chat_body()
                source = _project_field(body, "source", "path", "dir")
                handle = _project_field(body, "handle")
                if not source:
                    _chat_error("source is required", "400 Bad Request",
                                "invalid_request")
                # Transport first: a request that is not allowed to name a server
                # path must not consume the capability that would let it.
                project_import.require_local_transport(body)
                _project_require_session(ctx, body)
                if not handle:
                    raise project_import.refuse(
                        "导入必须携带预览返回的一次性句柄",
                        code=project_import.CODE_HANDLE_REQUIRED, status=403)
                identity = _project_identity()
                record = project_import.consume_handle(
                    identity, handle,
                    purpose=project_import.PURPOSE_PROJECT_IMPORT,
                    session_id=_project_session_id(body) or None,
                    payload=_project_import_binding(source, body.get("name")))
                # Publish the target the preview showed, not whatever this
                # request happens to say: the handle's binding has already
                # refused a different source, so the recorded name is the same
                # value the user confirmed and the preview stays the contract.
                recorded = record.get("payload") or {}
                result = project_browser.import_directory(
                    identity, source, name=recorded.get("_name") or None,
                    source_roots=project_import.source_roots(),
                    quota_reserve=_project_quota_reserve(identity),
                    stage_hook=project_import.stage_hook(identity, record))
                project_import.purge_handles()
                return json.dumps({"status": "success",
                                   "transport": "local", **result},
                                  ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Project import error: {e}")
            return _render_project_refusal(e)


class ProjectImportCancelHandler:
    """Cancel an issued import handle (task 6.4).

    Cancelling is the member's decision about their own handle: nothing is
    published, a copy already in flight is abandoned at its publish step (the
    stage hook removes the staging tree and releases the reservation), and the
    handle is no longer redeemable. A handle that is not the caller's is reported
    as invalid, so the endpoint cannot be used to probe or cancel someone else's
    import.
    """

    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            with _db_scope() as ctx:
                _require_chat_csrf()
                from channel.web import project_import

                body = _chat_body()
                handle = _project_field(body, "handle")
                identity = _project_identity()
                if not handle or not project_import.cancel_handle(
                        identity, handle,
                        purpose=project_import.PURPOSE_PROJECT_IMPORT):
                    raise project_import.refuse(
                        "导入句柄无效，已拒绝",
                        code=project_import.CODE_HANDLE_INVALID, status=403)
                return json.dumps({"status": "success", "cancelled": True},
                                  ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.error(f"[WebChannel] Project import cancel error: {e}")
            return _render_project_refusal(e)


def _project_request_is_upload() -> bool:
    """Whether this request is the multipart upload transport.

    Decided by the request's content type, before any body is read, so the two
    transports cannot be confused and a JSON body is never parsed as a manifest.
    """
    content_type = web.ctx.env.get("CONTENT_TYPE") or ""
    return content_type.lower().startswith("multipart/form-data")


def _knowledge_workspace_root(agent_id: Optional[str]) -> str:
    """The workspace whose ``knowledge/`` the addressed Agent actually reads.

    ``_get_workspace_root`` cannot serve here: in database mode it resolves the
    caller's tenant shared root and returns early, so ``agent_id`` never reaches
    the Agent fallback and every Agent rendered the same shared tree -- while
    the Agent itself (``agent/prompt/builder.py``, ``KnowledgeService``) and the
    CLI (``cli/utils.get_knowledge_dir``) resolve
    ``state_dir.knowledge_dir(base=<agent workspace>)``: the Agent's own
    ``knowledge/`` when it has one, the shared copy otherwise.

    Returning the workspace rather than a directory keeps that decision in
    ``state_dir``, the single place that owns the layout: ``KnowledgeService``
    applies the same "own by presence" rule, which also treats a ``knowledge/``
    symlink at the shared copy as shared. Callers MUST build the service inside
    ``_db_scope()``, because the shared fallback resolves through
    ``state_dir.shared_root()`` and must use the caller's tenant.

    A bound Agent missing from the roster degrades to ``_get_workspace_root``
    (the tenant shared root in database mode) instead of failing the request.
    """
    if agent_id:
        try:
            from agent.registry import get_agent_registry
            workspace = get_agent_registry().get(
                agent_id, require_enabled=False).workspace
            if workspace:
                return str(workspace)
        except Exception as e:
            logger.debug("[WebChannel] knowledge root for %r: %s", agent_id, e)
    return _get_workspace_root(agent_id=agent_id)


class KnowledgeListHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.knowledge.service import KnowledgeService
            with _db_scope() as ctx:
                _require_read_permission(ctx, "knowledge.read")
                params = web.input(agent_id='')
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_private_owner(ctx, agent_id)
                svc = KnowledgeService(
                    _knowledge_workspace_root(agent_id)
                )
                result = svc.list_tree()
                return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Knowledge list error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class KnowledgeReadHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from pathlib import Path
            from agent.knowledge.service import KnowledgeService
            with _db_scope() as ctx:
                _require_read_permission(ctx, "knowledge.read")
                params = web.input(path='', agent_id='')
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_private_owner(ctx, agent_id)
                svc = KnowledgeService(
                    _knowledge_workspace_root(agent_id)
                )
            result = svc.read_file(params.path)
            # Absolute directory of the doc (posix separators), so clients can
            # resolve image srcs that are relative to the doc into /api/file
            # URLs. Additive field; read_file itself stays untouched.
            rel = str(result["path"]).replace("\\", "/")
            result["dir"] = Path(svc.knowledge_dir, *rel.split("/")).parent.as_posix()
            return json.dumps({"status": "success", **result}, ensure_ascii=False)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as e:
            logger.error(f"[WebChannel] Knowledge read error: {e}")
            return json.dumps({"status": "error", "message": str(e)})


class KnowledgeGraphHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.knowledge.service import KnowledgeService
            with _db_scope() as ctx:
                _require_read_permission(ctx, "knowledge.read")
                params = web.input(agent_id='')
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_private_owner(ctx, agent_id)
                svc = KnowledgeService(
                    _knowledge_workspace_root(agent_id)
                )
            return json.dumps(svc.build_graph(), ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Knowledge graph error: {e}")
            return json.dumps({"nodes": [], "links": []})


class KnowledgeActionHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(web.data() or b"{}")
            action = body.get("action", "")
            payload = body.get("payload") or {}
            from agent.knowledge.service import KnowledgeService
            with _db_scope() as ctx:
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(body))
                _require_private_owner(ctx, agent_id)
                _require_knowledge_write(ctx, agent_id)
                svc = KnowledgeService(
                    _knowledge_workspace_root(agent_id)
                )
            result = svc.dispatch(action, payload)
            return json.dumps({
                "status": "success" if result["code"] < 300 else "error",
                **result,
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Knowledge action error: {e}")
            return json.dumps({"status": "error", "code": 500, "message": str(e), "payload": None})


class KnowledgeImportHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            from agent.knowledge.service import KnowledgeService
            content_length = int(getattr(web.ctx, "env", {}).get("CONTENT_LENGTH") or 0)
            if content_length > KnowledgeService.MAX_IMPORT_TOTAL_SIZE:
                return json.dumps({
                    "status": "error",
                    "code": 413,
                    "message": "import batch too large",
                    "payload": None,
                })
            with _db_scope() as ctx:
                params = _raw_web_input()
                agent_id = _require_tenant_agent_binding(ctx, _request_agent_id(params))
                _require_private_owner(ctx, agent_id)
                _require_knowledge_write(ctx, agent_id)
                root = _knowledge_workspace_root(agent_id)
                target_category = params.get("target_category", "")
                conflict_strategy = params.get("conflict_strategy", "skip")
                uploaded = _ensure_list(params.get("files"))
                single = params.get("file")
                if single is not None:
                    uploaded.append(single)
                # Build the service inside the identity scope: an Agent with no
                # ``knowledge/`` of its own falls back to ``state_dir.shared_root()``,
                # which resolves through the current identity and must therefore
                # see the caller's tenant, not the process default workspace.
                svc = KnowledgeService(root)
            if not uploaded:
                return json.dumps({"status": "error", "code": 400, "message": "No files uploaded", "payload": None})
            if len(uploaded) > KnowledgeService.MAX_IMPORT_FILES:
                return json.dumps({
                    "status": "error",
                    "code": 400,
                    "message": f"too many files: max {KnowledgeService.MAX_IMPORT_FILES}",
                    "payload": None,
                })

            files = []
            total_size = 0
            for file_obj in uploaded:
                if file_obj is None:
                    continue
                filename = getattr(file_obj, "filename", "") or getattr(file_obj, "name", "")
                content = _read_uploaded_file_bytes_limited(file_obj, KnowledgeService.MAX_IMPORT_FILE_SIZE)
                total_size += len(content)
                if total_size > KnowledgeService.MAX_IMPORT_TOTAL_SIZE:
                    return json.dumps({
                        "status": "error",
                        "code": 413,
                        "message": "import batch too large",
                        "payload": None,
                    })
                files.append({
                    "filename": filename,
                    "content": content,
                })

            result = svc.dispatch("import_documents", {
                "target_category": target_category,
                "conflict_strategy": conflict_strategy,
                "files": files,
            })
            return json.dumps({
                "status": "success" if result["code"] < 300 else "error",
                **result,
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Knowledge import error: {e}", exc_info=True)
            return json.dumps({"status": "error", "code": 500, "message": str(e), "payload": None})


class VersionHandler:
    def GET(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        from cli import __version__
        return json.dumps({"version": __version__})
