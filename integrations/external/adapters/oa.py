# encoding:utf-8
"""The OA (泛微 E-cology / E10) connection adapter.

Behavioural reference: OneAgent's ``channel/web/handlers/oa_connection.py`` for
the adaptive login/probe and ``skills/oa-audit-manager`` + ``skills/Weaver E10
Api`` for the business actions. The protocol logic lives in
:mod:`integrations.external.oa`; this module is the translation layer onto the
adapter contract, and it is where the product's rules are enforced:

* **One site per tenant.** The service enforces the singleton; the adapter
  assumes at most one connection and never invents a second.
* **Login and OpenAPI are separate capabilities.** A working account/password
  login whose OpenAPI credentials are absent is ``stage_partial`` on the
  OpenAPI stage — the spec forbids rounding "登录成功" up to "全部接口可用".
* **Precise failure stages.** Unreachable host → ``network``, certificate
  failure → ``tls``, wrong credentials → ``auth``, an unhandled login page →
  ``protocol``, a blocked target → ``policy``, a timeout → ``timeout``.
* **Unsupported interface is refused.** An action the discovered interface
  cannot serve (the E9-only forward/circulate/add-sign on an OpenAPI site, or
  an OpenAPI-only ``request.create`` on a login-only site) returns
  ``unsupported_interface`` rather than silently doing something else.
* **Target ambiguity is refused.** A forward/circulate/add-sign whose recipient
  keyword matches zero or several people returns ``target_not_found`` /
  ``target_ambiguous`` with the candidate list; the adapter never picks one.
* **A lost write response is unknown, not failed.** A submit/reject whose
  request was written but whose response was lost returns ``invoke_unknown``
  with a reconciliation hint and is never resent.
* **No secret escapes.** Every stage detail and every metadata value is run
  through :class:`~integrations.external.oa.redact.Redactor`, so the password,
  the app_secret, cookies and session ids cannot be echoed back.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from integrations.external import registry
from integrations.external.adapters.base import (
    AdapterError,
    Cancelled,
    CapabilityReport,
    ConnectionAdapter,
    DeadlineExceeded,
    ExecutionContext,
    InvokeResult,
    ProbeResult,
    STAGE_AUTH,
    STAGE_CONFIG,
    STAGE_INTERNAL,
    STAGE_POLICY,
    STAGE_PROTOCOL,
    STAGE_TIMEOUT,
    StageResult,
    invoke_failed,
    invoke_ok,
    invoke_unknown,
    register_adapter,
    stage_failed,
    stage_ok,
    stage_partial,
)
from integrations.external.errors import invalid
from integrations.external.oa import login as oa_login
from integrations.external.oa.client import OaActionError, OaClient
from integrations.external.oa.redact import Redactor, redact_list

ACTION_TODOS_LIST = "todos.list"
ACTION_REQUEST_READ = "request.read"
#: 审批流转记录 (the approval trail), 关联流程 (a related application the form
#: links to) and 附件 (the form's file fields). All three are reads, and all
#: three are E9-only: the E10 OpenAPI surface implemented here exposes
#: ``getInfoByID`` and nothing that carries a log page or a ``filedatas`` block.
ACTION_REQUEST_FLOWLOG = "request.flowlog"
ACTION_REQUEST_RELATED = "request.related"
ACTION_REQUEST_ATTACHMENTS = "request.attachments"
#: 抄送标记已读. A *write*: it removes the request from the user's 待阅 list,
#: which the user can see. Kept separate from every read so that reading a
#: 抄送 detail can never mark it read behind the caller's back.
ACTION_CC_MARK_READ = "cc.mark_read"
ACTION_REQUEST_CREATE = "request.create"
ACTION_REQUEST_SUBMIT = "request.submit"
ACTION_REQUEST_REJECT = "request.reject"
ACTION_REQUEST_FORWARD = "request.forward"
ACTION_REQUEST_CIRCULATE = "request.circulate"
ACTION_REQUEST_ADDSIGN = "request.addsign"

READ_ACTIONS = frozenset({
    ACTION_TODOS_LIST, ACTION_REQUEST_READ, ACTION_REQUEST_FLOWLOG,
    ACTION_REQUEST_RELATED, ACTION_REQUEST_ATTACHMENTS,
})
WRITE_ACTIONS = frozenset({
    ACTION_CC_MARK_READ, ACTION_REQUEST_CREATE, ACTION_REQUEST_SUBMIT,
    ACTION_REQUEST_REJECT, ACTION_REQUEST_FORWARD, ACTION_REQUEST_CIRCULATE,
    ACTION_REQUEST_ADDSIGN,
})
ACTIONS = READ_ACTIONS | WRITE_ACTIONS

#: Actions the agent tools may offer. Writes are offered only when the
#: deployment has opened the write class; unlike ERP, OA's write class *is*
#: openable, so the list is computed rather than fixed.
_TOOL_READ_ACTIONS = (ACTION_TODOS_LIST, ACTION_REQUEST_READ,
                      ACTION_REQUEST_FLOWLOG, ACTION_REQUEST_RELATED,
                      ACTION_REQUEST_ATTACHMENTS)

_PROBE_DETAIL_LIMIT = 700


def _result(ctx: ExecutionContext, stages: List[StageResult], *,
            metadata: Optional[Mapping[str, Any]] = None) -> ProbeResult:
    return ProbeResult(
        stages=tuple(stages), metadata=dict(metadata or {}),
        config_version=ctx.config_version,
        secret_versions=dict(ctx.secret_versions))


def _detail(redactor: Redactor, lines: List[str]) -> str:
    """A bounded, redacted diagnostic chain for a stage detail."""

    text = " | ".join(str(line) for line in lines if str(line).strip())
    return redactor(text)[:_PROBE_DETAIL_LIMIT]


def _site_host(base_url: str) -> str:
    return (urlparse(str(base_url or "")).hostname or "").lower()


@register_adapter
class OaAdapter(ConnectionAdapter):
    """Account/password login plus the workflow read/write actions."""

    kind = registry.KIND_OA
    actions = ACTIONS
    write_actions = WRITE_ACTIONS

    # -- offline ------------------------------------------------------------

    def validate_config(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Offline checks the registry's schema cannot express.

        :func:`integrations.external.registry.validate_config` has already
        refused unknown keys, secret-shaped keys, a non-URL ``base_url`` and a
        missing account. What is left is the *pairing* between the optional
        OpenAPI fields and the shape of a pasted site address.
        """
        normalized = dict(config or {})
        base_url = str(normalized.get("base_url") or "").strip()
        # Operators paste the login page's address; store the site root so the
        # probe and every action agree on one origin. This is offline string
        # work — no request is made here.
        root = oa_login.normalize_base_url(base_url)
        if not root:
            raise invalid("base_url must be an http(s) URL",
                          code="field_invalid", fields={"base_url": "invalid"})
        parsed = urlparse(root)
        if parsed.query or parsed.fragment:
            raise invalid("base_url must not carry a query string or fragment",
                          code="field_invalid", fields={"base_url": "invalid"})
        normalized["base_url"] = root
        app_key = str(normalized.get("app_key") or "").strip()
        corp_id = str(normalized.get("corp_id") or "").strip()
        if app_key and not corp_id:
            raise invalid("corp_id is required when app_key is configured",
                          code="field_invalid", fields={"corp_id": "required"})
        if corp_id and not app_key:
            raise invalid("app_key is required when corp_id is configured",
                          code="field_invalid", fields={"app_key": "required"})
        # ``attachment_dirs`` is where a fetched attachment may be written. The
        # paths are checked offline and relative for the same reason the mail
        # adapter's are: a directory that escapes the workspace would turn an
        # attachment download into an arbitrary write.
        raw_dirs = normalized.get("attachment_dirs")
        if raw_dirs not in (None, "", [], {}):
            if not isinstance(raw_dirs, (list, tuple)):
                raise invalid("attachment_dirs must be a list",
                              code="field_type",
                              fields={"attachment_dirs": "type"})
            seen: set = set()
            cleaned: List[str] = []
            for entry in raw_dirs:
                text = str(entry or "").strip()
                if not text:
                    continue
                if text.startswith(("/", "\\")) or text.startswith("~"):
                    raise invalid(
                        "attachment_dirs must be paths relative to the workspace",
                        code="field_invalid",
                        fields={"attachment_dirs": "invalid"})
                if ".." in text.replace("\\", "/").split("/"):
                    raise invalid(
                        "attachment_dirs must not escape the workspace",
                        code="field_invalid",
                        fields={"attachment_dirs": "invalid"})
                if text in seen:
                    raise invalid("attachment_dirs lists %r twice" % text,
                                  code="field_invalid",
                                  fields={"attachment_dirs": "duplicate"})
                seen.add(text)
                cleaned.append(text)
            normalized["attachment_dirs"] = cleaned
        return normalized

    # -- probe --------------------------------------------------------------

    def probe(self, ctx: ExecutionContext) -> ProbeResult:
        started = time.monotonic()
        try:
            password = ctx.secret("password")
        except AdapterError as exc:
            return _result(ctx, [stage_failed(
                "secret", exc.code or "secret_unavailable", stage=exc.stage,
                detail="the OA account password is not configured")])
        redactor = Redactor([password, ctx.optional_secret("app_secret")])
        ctx.check_alive()

        client: Optional[OaClient] = None
        try:
            client = OaClient(ctx)
            result = client.login(diagnostics=True)
        except AdapterError as exc:
            return _result(ctx, [stage_failed(
                "login", exc.code, stage=exc.stage,
                detail=redactor(str(exc)))])
        except Exception as exc:  # noqa: BLE001 - a bug is a stage, not a crash
            return _result(ctx, [stage_failed(
                "login", "adapter_error", stage=STAGE_INTERNAL,
                detail=redactor(type(exc).__name__))])

        discovered = dict(getattr(result, "discovered", {}) or {})
        metadata: Dict[str, Any] = {
            "site_host": _site_host(ctx.config.get("base_url")
                                    if isinstance(ctx.config, Mapping) else ""),
            "interface": result.interface_hint,
            "openapi_configured": client.openapi_configured(),
            "discovered_api_paths": redact_list(
                redactor, discovered.get("api_paths") or []),
            # Returned as an unconfirmed discovery, never persisted as config
            # (spec: 发现的站点参数仅作为待确认值返回).
            "discovered_login_config_paths": redact_list(
                redactor, discovered.get("login_config_paths") or []),
        }

        if not result.ok or not result.session_valid:
            detail = _detail(redactor, list(getattr(result, "diagnostics", [])
                                            or [])[-6:]) or result.message
            return _result(ctx, [stage_failed(
                "login", result.code or "login_failed",
                stage=result.stage or STAGE_AUTH, detail=detail)],
                metadata=metadata)

        stages: List[StageResult] = [stage_ok(
            "login", stage=STAGE_AUTH,
            detail="the OA account login succeeded and a session was verified",
            duration_ms=int((time.monotonic() - started) * 1000))]

        if not client.openapi_configured():
            # Login works, OpenAPI does not exist yet: partial, not ok and not
            # failed. The dependency-needing actions stay closed with the
            # reason attached.
            metadata["openapi_ready"] = False
            stages.append(stage_partial(
                "openapi", "openapi_not_configured", stage=STAGE_CONFIG,
                detail="登录可用；未配置 app_key/app_secret/corp_id，"
                       "依赖 OpenAPI 的动作保持关闭"))
            return _result(ctx, stages, metadata=metadata)

        ctx.check_alive()
        try:
            client.ensure_openapi()
        except AdapterError as exc:
            metadata["openapi_ready"] = False
            stages.append(stage_failed(
                "openapi", exc.code, stage=exc.stage,
                detail=redactor(str(exc))))
            return _result(ctx, stages, metadata=metadata)
        metadata["openapi_ready"] = True
        metadata["interface"] = "e10"
        stages.append(stage_ok("openapi", stage=STAGE_AUTH,
                               detail="OpenAPI 授权与令牌获取成功"))
        return _result(ctx, stages, metadata=metadata)

    # -- capabilities -------------------------------------------------------

    def describe_capabilities(self, ctx: ExecutionContext) -> CapabilityReport:
        """Login class vs OpenAPI class, each with its own reason.

        The write actions are reported available only when the deployment has
        opened ``write_execute``: a capability the runtime would always refuse
        is not a capability.
        """
        try:
            has_password = bool(ctx.has_secret("password"))
        except Exception:  # noqa: BLE001 - an unreadable secret is "missing"
            has_password = False
        has_app_secret = False
        try:
            has_app_secret = bool(ctx.has_secret("app_secret"))
        except Exception:  # noqa: BLE001
            has_app_secret = False
        config = ctx.config if isinstance(ctx.config, Mapping) else {}
        app_key = str(config.get("app_key") or "").strip()
        corp_id = str(config.get("corp_id") or "").strip()
        openapi_ready = bool(app_key and corp_id and has_app_secret)
        opened = registry.open_classes(self.kind)

        classes = {"configure"}
        actions: Dict[str, bool] = {action: False for action in sorted(ACTIONS)}
        reasons: Dict[str, str] = {}
        if has_password:
            classes.update({"test", "read_execute"})
            actions[ACTION_TODOS_LIST] = True
            actions[ACTION_REQUEST_READ] = True
        else:
            reasons["password"] = "secret_missing"
        if not openapi_ready:
            reasons["openapi"] = "openapi_not_configured"

        if "write_execute" in opened and has_password:
            classes.add("write_execute")
            for action in sorted(WRITE_ACTIONS):
                if action == ACTION_REQUEST_CREATE and not openapi_ready:
                    reasons[action] = "openapi_not_configured"
                    continue
                actions[action] = True
        else:
            reason = registry.unavailable_reason(self.kind, "write_execute") \
                if has_password else "secret_missing"
            reasons["write_execute"] = reason
            for action in sorted(WRITE_ACTIONS):
                reasons[action] = reason

        metadata: Dict[str, Any] = {
            "site_host": _site_host(config.get("base_url") or ""),
            "login_ready": has_password,
            "openapi_ready": openapi_ready,
            "interface": "e10" if openapi_ready else "e9",
            "open_classes": sorted(opened),
            "readiness_reason": registry.unavailable_reason(self.kind, "test"),
        }
        return CapabilityReport(classes=frozenset(classes), actions=actions,
                                reasons=reasons, metadata=metadata)

    # -- invoke -------------------------------------------------------------

    def invoke(self, ctx: ExecutionContext, action: str,
               params: Mapping[str, Any]) -> InvokeResult:
        """Run one declared action. Unknown actions are refused here too.

        The write class and the approval binding are checked by the runtime
        before this method is reached (:func:`integrations.external.risk.check_invocation`);
        repeating the action-list check here means a direct adapter call cannot
        skip the declaration gate, but approval verification is deliberately not
        duplicated.
        """
        self.guard_action(action)
        started = time.monotonic()
        client: Optional[OaClient] = None
        try:
            client = OaClient(ctx)
            data = client.invoke(action, params)
        except OaActionError as exc:
            duration = int((time.monotonic() - started) * 1000)
            if exc.unknown:
                return invoke_unknown(
                    "outcome_unknown", stage=exc.stage or STAGE_TIMEOUT,
                    message=self._unknown_message(exc), duration_ms=duration)
            if exc.data:
                # Refusals that carry facts the caller needs — the candidate
                # recipients of an ambiguous target, the actions a node does
                # allow — return them instead of only a message.
                return InvokeResult(ok=False, code=exc.code, stage=exc.stage,
                                    message=str(exc), data=dict(exc.data),
                                    duration_ms=duration)
            return invoke_failed(exc.code, stage=exc.stage,
                                 message=str(exc), duration_ms=duration)
        except TransportError as exc:  # pragma: no cover - normalised in client
            return invoke_failed(exc.code, stage=exc.stage, message=exc.detail)
        except (DeadlineExceeded, Cancelled) as exc:
            duration = int((time.monotonic() - started) * 1000)
            if action in self.write_actions:
                # The call was abandoned mid-flight; the remote may have
                # accepted it. Reporting "failed" would invite a resend.
                return invoke_unknown(
                    "outcome_unknown", stage=exc.stage,
                    message="提交结果未知：远端可能已受理，系统不会自动重发，"
                            "请先重新读取流程状态核对",
                    duration_ms=duration)
            return invoke_failed(exc.code, stage=exc.stage,
                                 message=str(exc), duration_ms=duration)
        except AdapterError as exc:
            return invoke_failed(exc.code, stage=exc.stage, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a bug is reported, not leaked
            return invoke_failed("adapter_error", stage=STAGE_INTERNAL,
                                 message=type(exc).__name__)
        return invoke_ok(data, duration_ms=int((time.monotonic() - started) * 1000))

    @staticmethod
    def _unknown_message(exc: OaActionError) -> str:
        message = str(exc)
        reconcile = (exc.data or {}).get("reconcile") or {}
        if reconcile:
            message = "%s（核对建议：%s %s）" % (
                message, reconcile.get("action") or ACTION_REQUEST_READ,
                reconcile.get("params") or {})
        return message


# -- tool provider and dispatcher -------------------------------------------

def _action_description(action: str) -> str:
    if action == ACTION_TODOS_LIST:
        return "查询 OA 待办/已办/我发起/抄送列表（只读）"
    if action == ACTION_REQUEST_READ:
        return "读取 OA 流程详情（只读）"
    if action == ACTION_REQUEST_FLOWLOG:
        return "读取 OA 流程审批流转记录（只读，不改变已读状态）"
    if action == ACTION_REQUEST_RELATED:
        return "读取 OA 流程关联的事前申请等关联流程（只读，受远端权限约束）"
    if action == ACTION_REQUEST_ATTACHMENTS:
        return "列出 OA 流程附件；指定序号/文件名时下载到工作区（只取本站链接）"
    if action == ACTION_CC_MARK_READ:
        return "将 OA 抄送标记为已读（写动作，会改变待阅状态）"
    return "执行 OA 写动作 %s" % action


def oa_tool_provider(tenant_id: Optional[str],
                     actor_user_id: str) -> List[Any]:
    """Bind the OA tools for the tenant's single OA connection.

    Listing is not authorization: every call re-derives the connection, the
    tenant and the deployment's open classes in ``ConnectionRuntime.invoke``.
    Writes appear only when ``write_execute`` is open, so the model is never
    offered something the runtime always refuses.
    """
    from integrations.external import risk as risk_module
    from integrations.external.oa.store import OaStoreError, list_oa_connections
    from integrations.external.tools import ExternalTool, ToolBinding

    del actor_user_id  # call-time re-authorization happens in the runtime
    tenant = str(tenant_id or "").strip()
    if not tenant:
        return []
    opened = registry.open_classes(registry.KIND_OA)
    if "read_execute" not in opened:
        return []
    try:
        rows = list_oa_connections(tenant, enabled_only=True)
    except OaStoreError:
        return []
    except Exception:  # noqa: BLE001 - an unreadable store offers nothing
        return []

    offered: List[str] = list(_TOOL_READ_ACTIONS)
    if "write_execute" in opened:
        offered.extend(sorted(WRITE_ACTIONS))

    out: List[Any] = []
    for row in rows:
        name = str(row["name"] or row["id"])
        for action in offered:
            is_write = action in WRITE_ACTIONS
            tool = ExternalTool(
                name="oa.%s.%s" % (action.replace(".", "_"), row["id"]),
                kind=registry.KIND_OA, action=action, write=is_write,
                description="%s（连接 %s）" % (_action_description(action), name),
                # The risk level travels with the declaration so the console and
                # the caller see what the action will require.
                metadata={
                    "connection_name": name,
                    "risk": risk_module.action_projection(
                        registry.KIND_OA, action, write=is_write),
                })
            out.append(ToolBinding(tool=tool, connection_id=str(row["id"]),
                                   connection_name=name, scope="tenant"))
    return out


def oa_dispatcher(service, binding, params: Mapping[str, Any], *,
                  tenant_id: Optional[str], actor_user_id: str,
                  agent_id: str = "", run_id: str = "",
                  approval: Optional[Mapping[str, Any]] = None):
    """Run one OA tool against the connection the binding named."""
    return service.invoke_action(
        binding.connection_id, binding.tool.action, params,
        actor_user_id=actor_user_id, tenant_id=tenant_id,
        agent_id=agent_id, run_id=run_id, approval=approval)


def register_tools() -> None:
    from integrations.external.tools import register_dispatcher, register_tool_provider
    register_tool_provider(registry.KIND_OA, oa_tool_provider)
    register_dispatcher(registry.KIND_OA, oa_dispatcher)


register_tools()
