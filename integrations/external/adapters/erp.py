# encoding:utf-8
"""The ERP (SAP) connection adapter.

Two stored providers share this one adapter, because they are two ways to reach
the same system and the rest of the product must not care which was chosen:

``rfc``
    ``ashost`` / ``sysnr`` / ``client`` / ``user`` / ``lang`` and the
    ``password`` secret slot. Reached through the SAP NW RFC SDK (``pyrfc``).
``adt_sql``
    ``base_url`` / ``client`` / ``user`` / ``verify_ssl`` / ``timeout`` and the
    ``password`` secret slot. Reached over the ADT HTTP SQL endpoint.

Both providers already exist in this repository
(``Scene/sap_data_analysis/backend/sap/``) and are the ones the scenes use, so
this adapter **routes through that factory** rather than growing a second SAP
client: a fix to the provider fixes the scene and the tool at once.

What the adapter is careful about
---------------------------------
* **Probe only authenticates and reads.** RFC runs ``RFC_PING`` plus the
  connection attributes; ADT runs the same bounded ``SELECT COUNT(*)`` read the
  scene test uses. Neither path invokes a business write ("ERP/OA/邮箱仅认证与
  必要只读探测").
* **RFC cancellation is reported honestly.** A ``pyrfc`` call cannot be
  interrupted, so when the deployment has not accepted an isolation story that
  can abandon the worker, the probe refuses with
  ``uninterruptible_not_permitted`` before it ever blocks a web worker. It does
  not pretend a spinner is a cancellation.
* **A failure names its stage.** SDK missing, TLS refused, authentication
  rejected, network unreachable, policy refusal and protocol errors are distinct
  ``StageResult.stage`` values, because "connection failed" is not an answer an
  operator can act on.
* **A secret never reaches the result.** The password is resolved at the last
  moment, used to build the provider, and scrubbed from any error text; the
  probe metadata carries only the system/client/host the remote reported.
* **Read queries stay on the read path.** ``query`` runs configured scene
  templates (or an explicit table/multi-table plan) and refuses a ``bapi``
  source; ``rfc.call`` is the only write path, needs an approval, and only
  reaches BAPIs the scene catalogue already whitelists.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from integrations.external import registry
from integrations.external.adapters.base import (
    AdapterError,
    CapabilityReport,
    ConnectionAdapter,
    ExecutionContext,
    InvokeResult,
    PolicyRefused,
    ProbeResult,
    STAGE_AUTH,
    STAGE_CONFIG,
    STAGE_DEPENDENCY,
    STAGE_INTERNAL,
    STAGE_NETWORK,
    STAGE_POLICY,
    STAGE_PROTOCOL,
    STAGE_TIMEOUT,
    STAGE_TLS,
    StageResult,
    invoke_failed,
    invoke_ok,
    register_adapter,
    stage_failed,
    stage_ok,
)
from integrations.external.adapters.netpolicy import NetworkPolicy, current_policy
from integrations.external.errors import invalid

#: The actions this adapter serves. ``report.invoke`` refuses anything else, so
#: an unknown action cannot be smuggled past the caller.
ACTION_QUERY = "query"
ACTION_RFC_CALL = "rfc.call"
ACTIONS = frozenset({ACTION_QUERY, ACTION_RFC_CALL})
WRITE_ACTIONS = frozenset({ACTION_RFC_CALL})

#: Providers this adapter can actually reach. Anything else (the legacy U9 and
#: 金蝶 rows) is refused rather than mocked ("模拟 ERP 不作为生产能力").
SUPPORTED_PROVIDERS = frozenset({"rfc", "adt_sql"})

#: The bounded read the ADT probe performs, matching the scene test verbatim.
_ADT_PROBE_SQL = "SELECT COUNT(*) AS cnt FROM t000"

#: Default cap on rows a single adapter ``query`` may return. The deployment can
#: tighten it through the pool limits; a caller cannot raise it.
_DEFAULT_MAX_ROWS = 10000


# -- Scene provider loading --------------------------------------------------

def _scene_factory() -> Tuple[Any, type]:
    """The shared SAP factory and the RFC dependency error, imported lazily.

    Imported on use rather than at module import so a build without the scene
    tree (or a control-plane-only process) can still load the adapter registry.
    """
    from Scene.sap_data_analysis.backend.sap.factory import SAPProviderFactory
    from Scene.sap_data_analysis.backend.sap.rfc_provider import RfcDependencyError
    return SAPProviderFactory, RfcDependencyError


def _provider_of(config: Mapping[str, Any]) -> str:
    return str((config or {}).get("provider") or "").strip().lower()


# -- failure classification --------------------------------------------------

def _classify_failure(exc: BaseException) -> Tuple[str, str]:
    """Map a provider failure onto a precise ``(stage, code)`` pair.

    Kept as a pure function so the mapping can be tested without an SDK and so
    the same wording is used for probe and invoke failures.
    """
    if isinstance(exc, AdapterError):
        return exc.stage, exc.code
    name = type(exc).__name__.lower()
    text = ("%s %s" % (type(exc).__name__, exc)).lower()
    if ("dependency" in name or "rfc" in name or "sdk" in name
            or "nwrfc" in text or "pyrfc" in text or "sap nw rfc" in text):
        return STAGE_DEPENDENCY, "sdk_unavailable"
    if "timeout" in text or "timed out" in text:
        return STAGE_TIMEOUT, "timeout"
    if "ssl" in text or "certificate" in text or "tls" in text:
        return STAGE_TLS, "tls_failed"
    if any(word in text for word in ("logon", "password", "username",
                                     "user name", "authentication", "401",
                                     "403")):
        return STAGE_AUTH, "auth_failed"
    if any(word in text for word in ("connect", "network", "resolve", "host",
                                     "socket", "unreachable", "refused")):
        return STAGE_NETWORK, "network_unreachable"
    return STAGE_PROTOCOL, "protocol_error"


def _redact(text: Any, secrets: List[str]) -> str:
    value = " ".join(str(text or "").split())
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***")
    return value[:300]


def _result(ctx: ExecutionContext, stages: List[StageResult], *,
            metadata: Optional[Mapping[str, Any]] = None) -> ProbeResult:
    return ProbeResult(
        stages=tuple(stages), metadata=dict(metadata or {}),
        config_version=ctx.config_version,
        secret_versions=dict(ctx.secret_versions))


def _policy_of(ctx: ExecutionContext) -> NetworkPolicy:
    policy = ctx.limits.get("policy") if isinstance(ctx.limits, Mapping) else None
    return policy if isinstance(policy, NetworkPolicy) else current_policy()


def _rfc_sysnr(config: Mapping[str, Any]) -> str:
    value = str((config or {}).get("sysnr") or "00").strip()
    return value if value.isdigit() else "00"


def _close(provider: Any) -> None:
    closer = getattr(provider, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001 - a teardown failure is not a result
            pass


# -- the adapter -------------------------------------------------------------

@register_adapter
class ErpAdapter(ConnectionAdapter):
    """RFC and ADT SQL behind one action contract."""

    kind = registry.KIND_ERP
    actions = ACTIONS
    write_actions = WRITE_ACTIONS

    # -- offline ------------------------------------------------------------

    def validate_config(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Checks the registry's partitioning cannot express, still offline.

        Starts from :func:`integrations.external.registry.validate_config`, so
        called directly or with an already-normalized config the result is the
        same canonical dict: unknown keys, secret-shaped keys, a bad provider and
        the rfc/adt_sql field split are the registry's job. What is added here is
        the *shape* of the values: a SAP system number and client are numeric, and
        an application server is a bare name, not a URL someone pasted into the
        wrong field.
        """
        normalized = registry.validate_config(self.kind, config)
        provider = _provider_of(normalized)
        if provider not in SUPPORTED_PROVIDERS:
            # The registry normally rejects this first; kept as a second gate so
            # a caller reaching the adapter directly cannot open a mock path.
            raise invalid(
                "provider %r has no production adapter in this build" % provider,
                code="unsupported_provider", fields={"provider": "unsupported"})
        client = str(normalized.get("client") or "").strip()
        if not client.isdigit():
            raise invalid("client must be numeric", code="field_invalid",
                          fields={"client": "invalid"})
        if provider == "rfc":
            sysnr = str(normalized.get("sysnr") or "").strip()
            if not sysnr.isdigit() or len(sysnr) != 2:
                raise invalid("sysnr must be a two-digit system number",
                              code="field_invalid", fields={"sysnr": "invalid"})
            host = str(normalized.get("ashost") or "").strip()
            if not host or "/" in host or "://" in host or " " in host:
                raise invalid("ashost must be a SAP application server host",
                              code="field_invalid", fields={"ashost": "invalid"})
            return normalized
        url = str(normalized.get("base_url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise invalid("base_url must be an http(s) URL",
                          code="field_invalid", fields={"base_url": "invalid"})
        return normalized

    # -- provider construction ---------------------------------------------

    def _credentials(self, ctx: ExecutionContext, password: str, *,
                     verify_ssl: Optional[bool] = None) -> Dict[str, Any]:
        config = dict(ctx.config or {})
        if _provider_of(config) == "rfc":
            return {
                "ashost": str(config.get("ashost") or ""),
                "sysnr": _rfc_sysnr(config),
                "client": str(config.get("client") or "100"),
                "username": str(config.get("user") or ""),
                "password": password,
                "lang": str(config.get("lang") or "EN"),
            }
        return {
            "base_url": str(config.get("base_url") or ""),
            "username": str(config.get("user") or ""),
            "password": password,
            "client": str(config.get("client") or "100"),
            "verify_ssl": bool(config.get("verify_ssl", True))
            if verify_ssl is None else bool(verify_ssl),
        }

    def _create_provider(self, ctx: ExecutionContext, password: str, *,
                         verify_ssl: Optional[bool] = None):
        provider_type = _provider_of(ctx.config)
        factory, dependency_error = _scene_factory()
        credentials = self._credentials(ctx, password, verify_ssl=verify_ssl)
        try:
            return factory.create(provider_type, credentials)
        except dependency_error as exc:  # noqa: BLE001 - normalised below
            raise AdapterError(str(exc), code="sdk_unavailable",
                               stage=STAGE_DEPENDENCY) from exc

    # -- network policy -----------------------------------------------------

    def _check_rfc_target(self, ctx: ExecutionContext) -> None:
        """Gate the RFC application server by the deployment's policy.

        The RFC SDK makes its own TCP connection (standard SAP gateway port
        ``3300 + sysnr``); it cannot be routed through :func:`safe_get`. The
        address check is therefore performed here, with the same policy object,
        before the SDK is handed the host: a permitted *name* that resolves to a
        denied address is refused, which is the property that matters.
        """
        policy = _policy_of(ctx)
        host = str((ctx.config or {}).get("ashost") or "").strip()
        if not host:
            raise PolicyRefused("SAP application server host is not configured")
        if (not policy.host_allowed(host) and not policy.allow_networks
                and not policy.allow_private):
            raise PolicyRefused("host %r is not in the allowed list" % host)
        try:
            port = 3300 + int(_rfc_sysnr(ctx.config))
        except ValueError:  # pragma: no cover - _rfc_sysnr guarantees digits
            port = 3300
        targets = policy.resolve(host, port)
        denied = [t for t in targets if not policy.address_allowed(t.address)]
        if denied:
            raise PolicyRefused(
                "host %s resolves to an address that is not allowed (%s)"
                % (host, denied[0].address))

    # -- probe --------------------------------------------------------------

    def probe(self, ctx: ExecutionContext) -> ProbeResult:
        provider = _provider_of(ctx.config)
        if provider not in SUPPORTED_PROVIDERS:
            return _result(ctx, [stage_failed(
                "configuration", "unsupported_provider", stage=STAGE_CONFIG,
                detail="this connection has no production ERP provider")])
        ctx.check_alive()
        try:
            password = ctx.secret("password")
        except AdapterError as exc:
            return _result(ctx, [stage_failed(
                "secret", exc.code or "secret_unavailable", stage=exc.stage,
                detail=_redact(str(exc), []))])
        if provider == "rfc":
            return self._probe_rfc(ctx, password)
        return self._probe_adt(ctx, password)

    def _probe_rfc(self, ctx: ExecutionContext, password: str) -> ProbeResult:
        secrets = [password]
        if not bool(ctx.limits.get("uninterruptible_ok")):
            # Reported before the SDK is touched: the call cannot be stopped,
            # so a deployment that has not accepted the isolation story gets a
            # concrete refusal instead of a hung worker.
            return _result(ctx, [stage_failed(
                "isolation", "uninterruptible_not_permitted",
                stage=STAGE_DEPENDENCY,
                detail="the SAP RFC SDK call cannot be interrupted and this "
                       "deployment has not accepted the isolation "
                       "requirements for it")])
        try:
            self._check_rfc_target(ctx)
        except PolicyRefused as exc:
            return _result(ctx, [stage_failed(
                "target", "target_not_allowed", stage=STAGE_POLICY,
                detail=_redact(str(exc), secrets))])
        ctx.check_alive()
        provider = None
        try:
            provider = self._create_provider(ctx, password)
            info = provider.test_connection() or {}
            if str(info.get("status") or "").lower() != "success":
                return _result(ctx, [stage_failed(
                    "probe", "protocol_error", stage=STAGE_PROTOCOL,
                    detail="the SAP system did not report a successful RFC "
                           "ping")])
            metadata = {key: info.get(key) for key in ("system", "client", "host")
                        if info.get(key) not in (None, "")}
            return _result(ctx, [
                stage_ok("auth", stage=STAGE_AUTH,
                         detail="RFC_PING succeeded"),
                stage_ok("read", stage=STAGE_PROTOCOL,
                         detail="RFC connection attributes read"),
            ], metadata=metadata)
        except BaseException as exc:  # noqa: BLE001 - mapped, never leaked
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            stage, code = _classify_failure(exc)
            return _result(ctx, [stage_failed(
                "probe", code, stage=stage, detail=_redact(exc, secrets))])
        finally:
            _close(provider)

    def _probe_adt(self, ctx: ExecutionContext, password: str) -> ProbeResult:
        secrets = [password]
        policy = _policy_of(ctx)
        requested_verify = bool((ctx.config or {}).get("verify_ssl", True))
        try:
            # A connection asking to skip verification is refused unless the
            # deployment permitted it; it is never silently downgraded.
            effective_verify = policy.effective_verify(requested_verify)
        except PolicyRefused as exc:
            return _result(ctx, [stage_failed(
                "tls", "tls_verify_not_permitted", stage=STAGE_POLICY,
                detail=_redact(str(exc), secrets))])
        url = str((ctx.config or {}).get("base_url") or "")
        try:
            policy.check(url, secret_bearing=True)
        except PolicyRefused as exc:
            return _result(ctx, [stage_failed(
                "target", "target_not_allowed", stage=STAGE_POLICY,
                detail=_redact(str(exc), secrets))])
        ctx.check_alive()
        stages: List[StageResult] = [stage_ok(
            "policy", stage=STAGE_POLICY,
            detail="the target address is permitted by the deployment policy")]
        provider = None
        try:
            provider = self._create_provider(ctx, password,
                                             verify_ssl=effective_verify)
            outcome = provider.test_connection() or {}
            if str(outcome.get("status") or "").lower() != "success":
                return _result(ctx, stages + [stage_failed(
                    "probe", "protocol_error", stage=STAGE_PROTOCOL,
                    detail="the ADT endpoint did not report a successful read")])
            sample = outcome.get("sample")
            stages.append(stage_ok(
                "auth", stage=STAGE_AUTH,
                detail="ADT authentication succeeded"))
            stages.append(stage_ok(
                "read", stage=STAGE_PROTOCOL,
                detail="bounded read-only query succeeded"))
            metadata = {
                "verify_ssl": effective_verify,
                "row_count": len(sample) if isinstance(sample, (list, tuple)) else 0,
            }
            return _result(ctx, stages, metadata=metadata)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            stage, code = _classify_failure(exc)
            return _result(ctx, stages + [stage_failed(
                "probe", code, stage=stage, detail=_redact(exc, secrets))])
        finally:
            _close(provider)

    # -- capabilities -------------------------------------------------------

    def describe_capabilities(self, ctx: ExecutionContext) -> CapabilityReport:
        provider = _provider_of(ctx.config)
        reasons: Dict[str, str] = {}
        actions: Dict[str, bool] = {"query": False, ACTION_RFC_CALL: False}
        classes = {"configure"}
        metadata: Dict[str, Any] = {"provider": provider}
        if provider not in SUPPORTED_PROVIDERS:
            reasons["provider"] = "unsupported_provider"
            return CapabilityReport(classes=frozenset(classes), actions=actions,
                                    reasons=reasons, metadata=metadata)
        has_secret = False
        try:
            has_secret = bool(ctx.has_secret("password"))
        except Exception:  # noqa: BLE001 - an unreadable secret is "missing"
            has_secret = False
        if not has_secret:
            reasons["password"] = "secret_missing"
        else:
            classes.update({"test", "read_execute"})
            actions["query"] = True
        # ERP has no openable write class in this build, so a BAPI call is never
        # *offered* -- the reason comes from the registry so the adapter and the
        # catalogue projection cannot disagree about why.
        actions[ACTION_RFC_CALL] = False
        if provider == "adt_sql":
            reasons[ACTION_RFC_CALL] = "provider_has_no_rfc"
        else:
            reasons[ACTION_RFC_CALL] = (registry.unavailable_reason(
                registry.KIND_ERP, "write_execute") or "write_class_not_openable")
        metadata["write_class_openable"] = (
            "write_execute" in registry.OPENABLE_CLASSES.get(
                registry.KIND_ERP, frozenset()))
        return CapabilityReport(classes=frozenset(classes), actions=actions,
                                reasons=reasons, metadata=metadata)

    # -- invoke -------------------------------------------------------------

    def invoke(self, ctx: ExecutionContext, action: str,
               params: Mapping[str, Any]) -> InvokeResult:
        # The caller (runtime) already checked the action list and the open
        # class; repeating it here means a direct adapter call cannot skip it.
        self.guard_action(action)
        if action in self.write_actions:
            approval = dict(ctx.extra.get("approval") or {})
            if not approval.get("approved"):
                return invoke_failed(
                    "approval_required", stage=STAGE_POLICY,
                    message="this action changes the SAP system and needs an "
                            "approval bound to it")
        try:
            password = ctx.secret("password")
        except AdapterError as exc:
            return invoke_failed(exc.code or "secret_unavailable",
                                 stage=exc.stage, message=str(exc))
        started = time.monotonic()
        provider = None
        try:
            provider = self._create_provider(ctx, password)
            if action == ACTION_QUERY:
                result = self._invoke_query(ctx, provider, params)
            else:
                result = self._invoke_rfc(ctx, provider, params)
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            stage, code = _classify_failure(exc)
            return invoke_failed(code, stage=stage,
                                 message=_redact(exc, [password]))
        finally:
            _close(provider)
        if result.duration_ms == 0:
            result = InvokeResult(
                ok=result.ok, data=result.data, code=result.code,
                stage=result.stage, message=result.message,
                outcome_unknown=result.outcome_unknown,
                duration_ms=int((time.monotonic() - started) * 1000))
        return result

    def _invoke_query(self, ctx: ExecutionContext, provider: Any,
                      params: Mapping[str, Any]) -> InvokeResult:
        """Run a read-only query. No BAPI, no arbitrary SQL text."""
        data = dict(params or {})
        plan_dict = data.get("query_plan")
        if plan_dict:
            from Scene.sap_data_analysis.backend.sap.fetch_executor import FetchExecutor
            from Scene.sap_data_analysis.backend.sap.query_planner import QueryPlan
            plan = QueryPlan.from_dict(plan_dict)
            if str(getattr(plan, "source", "")) == "bapi":
                return invoke_failed(
                    "bapi_not_allowed", stage=STAGE_POLICY,
                    message="a read query may not invoke a BAPI; use rfc.call "
                            "with an approval")
            rows = FetchExecutor(provider).execute(plan)
        else:
            scene_id = str(data.get("scene_id") or "").strip()
            if not scene_id:
                return invoke_failed(
                    "scene_required", stage=STAGE_CONFIG,
                    message="a read query needs a scene_id or a query_plan")
            limit = self._row_limit(ctx, data)
            query_params = dict(data.get("params") or {})
            query_params["max_rows"] = limit
            try:
                rows = provider.fetch(scene_id=scene_id, params=query_params)
            except ValueError as exc:
                return invoke_failed("unknown_scene", stage=STAGE_CONFIG,
                                     message=str(exc))
        rows = list(rows or [])
        return invoke_ok({"rows": rows, "row_count": len(rows),
                          "provider": _provider_of(ctx.config)})

    def _invoke_rfc(self, ctx: ExecutionContext, provider: Any,
                    params: Mapping[str, Any]) -> InvokeResult:
        """The one write path: a whitelisted BAPI, with an approval already on."""
        data = dict(params or {})
        name = str(data.get("bapi") or data.get("bapi_name") or "").strip().upper()
        if not name:
            return invoke_failed("bapi_required", stage=STAGE_CONFIG,
                                 message="rfc.call needs the BAPI name")
        if _provider_of(ctx.config) != "rfc":
            return invoke_failed(
                "provider_has_no_rfc", stage=STAGE_POLICY,
                message="only an RFC connection can invoke a BAPI")
        allowed = _bapi_whitelist()
        if name not in allowed:
            return invoke_failed(
                "bapi_not_allowed", stage=STAGE_POLICY,
                message="BAPI %s is not in the scene catalogue's allow-list" % name)
        call = getattr(provider, "call_bapi", None)
        if not callable(call):
            return invoke_failed("provider_has_no_rfc", stage=STAGE_POLICY,
                                 message="the provider cannot invoke a BAPI")
        payload = dict(data.get("parameters") or {})
        result = call(name, payload)
        return invoke_ok({"bapi": name, "result": result})

    @staticmethod
    def _row_limit(ctx: ExecutionContext, data: Mapping[str, Any]) -> int:
        requested = data.get("max_rows")
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            requested = _DEFAULT_MAX_ROWS
        ceiling = ctx.limits.get("erp_max_rows") if isinstance(ctx.limits, Mapping) \
            else None
        try:
            ceiling = int(ceiling)
        except (TypeError, ValueError):
            ceiling = _DEFAULT_MAX_ROWS
        return max(1, min(requested, ceiling, _DEFAULT_MAX_ROWS))


#: Process-local additions used by tests and embedders; production should use
#: the deployment configuration so the decision is auditable.
_WRITE_BAPIS: set = set()


def register_write_bapis(names) -> None:
    """Allow ``names`` for ``rfc.call`` in this process (test/embedder hook)."""
    _WRITE_BAPIS.update(str(name).upper() for name in (names or ()) if name)


def _bapi_whitelist() -> frozenset:
    """The BAPIs this deployment has accepted as *writable*.

    Empty by default: the scene catalogue's ``BAPI_DOMAIN_MAP`` contains only
    read-only BAPIs, and a BAPI must not become writable merely because nobody
    blacklisted it. A deployment opts in explicitly through
    ``external_connections.erp.write_bapis`` (or :func:`register_write_bapis`).
    """
    names = set(_WRITE_BAPIS)
    try:
        from config import conf
        raw = (conf() or {}).get("external_connections") or {}
        if isinstance(raw, Mapping):
            erp = raw.get("erp") or {}
            configured = erp.get("write_bapis") if isinstance(erp, Mapping) else None
            if isinstance(configured, (list, tuple, set, frozenset)):
                names.update(str(name).upper() for name in configured if name)
    except Exception:  # noqa: BLE001 - no configuration means no writable BAPI
        pass
    return frozenset(names)




# -- tool provider and dispatcher -------------------------------------------

#: The ERP tools the agent may be offered. ``rfc.call`` is intentionally not
#: offered in this build: its write class is not openable for ERP, so offering
#: it would advertise something the runtime always refuses.
_TOOL_ACTIONS: Tuple[str, ...] = (ACTION_QUERY,)


def _erp_tool_provider(tenant_id: Optional[str],
                       actor_user_id: str) -> List[Any]:
    """Bind read-only ERP tools to each of the tenant's connections.

    The tool name carries the connection id so two ERP systems are
    distinguishable and ``dispatch`` can re-derive the exact connection; the
    binding also carries the connection's display name for the model. Listing
    is not authorization: every call re-checks the connection, the tenant and
    the deployment's open classes in ``ConnectionRuntime.invoke``.
    """
    from integrations.external.tools import ExternalTool, ToolBinding

    del actor_user_id  # call-time re-authorization happens in the runtime
    tenant = str(tenant_id or "").strip()
    if not tenant:
        return []
    if "read_execute" not in registry.open_classes(registry.KIND_ERP):
        # The deployment has not opened ERP read execution; showing a tool that
        # always fails would be a placeholder, not a capability.
        return []
    try:
        from integrations.external.adapters.erp_scene import list_erp_connections
        rows = list_erp_connections(tenant, enabled_only=True)
    except Exception:  # noqa: BLE001 - an unreadable store offers nothing
        return []
    opened = registry.open_classes(registry.KIND_ERP)
    out: List[Any] = []
    for row in rows:
        name = str(row["name"] or row["id"])
        for action in _TOOL_ACTIONS:
            if action in WRITE_ACTIONS and "write_execute" not in opened:
                continue
            tool = ExternalTool(
                name="erp.%s.%s" % (action, row["id"]),
                kind=registry.KIND_ERP, action=action, write=action in WRITE_ACTIONS,
                description="Run the read-only ERP query %r against %s"
                            % (action, name),
                metadata={"connection_name": name, "provider": _provider_from_row(row)})
            out.append(ToolBinding(tool=tool, connection_id=str(row["id"]),
                                   connection_name=name, scope="tenant"))
    return out


def _provider_from_row(row: Mapping[str, Any]) -> str:
    import json

    try:
        config = json.loads(row["config_json"] or "{}")
        if isinstance(config, Mapping):
            return str(config.get("provider") or "")
    except (TypeError, ValueError):
        pass
    return ""


def _erp_dispatcher(service, binding, params: Mapping[str, Any], *,
                    tenant_id: Optional[str], actor_user_id: str,
                    agent_id: str = "", run_id: str = "",
                    approval: Optional[Mapping[str, Any]] = None):
    """Run one ERP tool against the connection the binding named."""
    return service.invoke_action(
        binding.connection_id, binding.tool.action, params,
        actor_user_id=actor_user_id, tenant_id=tenant_id,
        agent_id=agent_id, run_id=run_id, approval=approval)


def register_tools() -> None:
    from integrations.external.tools import register_dispatcher, register_tool_provider
    register_tool_provider(registry.KIND_ERP, _erp_tool_provider)
    register_dispatcher(registry.KIND_ERP, _erp_dispatcher)


register_tools()
