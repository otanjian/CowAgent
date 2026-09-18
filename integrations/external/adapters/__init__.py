# encoding:utf-8
"""Adapter implementations and the shared contract they are built against.

Import surface for the rest of the control plane:

* :class:`ConnectionAdapter` / :class:`ExecutionContext` — the contract an
  implementation follows (``base``);
* :func:`adapter_for` / :func:`adapter_available` — resolution;
* :class:`NetworkPolicy` / :func:`current_policy` — the deployment's outbound
  policy, which every remote attempt is checked against (``netpolicy``);
* :func:`run_probe` / :func:`pool_for_deployment` — the bounded test pool
  (``pool``).

Kept as a re-export module on purpose: a consumer should not have to know
which of the three files a name lives in, and the adapter modules themselves
import ``base`` directly so registration cannot depend on this package's
import order.
"""

from __future__ import annotations

from integrations.external.adapters.base import (  # noqa: F401
    AdapterError,
    Cancelled,
    CapabilityReport,
    ConnectionAdapter,
    DeadlineExceeded,
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
    STAGES,
    StageResult,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    STATUSES,
    adapter_available,
    adapter_for,
    invoke_failed,
    invoke_ok,
    invoke_unknown,
    registered_kinds,
    register_adapter,
    stage_failed,
    stage_ok,
    stage_partial,
    stage_skipped,
)
from integrations.external.adapters.netpolicy import (  # noqa: F401
    FetchOutcome,
    NetworkPolicy,
    RedirectStep,
    ResolvedTarget,
    current_policy,
    policy_from_mapping,
    reset_policy_cache,
    safe_get,
)
from integrations.external.adapters.pool import (  # noqa: F401
    PoolBusy,
    PoolLimits,
    ProbeOutcome,
    TestPool,
    abandoned_snapshot,
    limits_from_mapping,
    pool_for_deployment,
    reset_abandoned,
    reset_pools,
    run_probe,
    run_probe_with_kill_switch,
)

__all__ = [
    "AdapterError", "Cancelled", "CapabilityReport", "ConnectionAdapter",
    "DeadlineExceeded", "ExecutionContext", "InvokeResult", "PolicyRefused",
    "ProbeResult", "StageResult", "STAGE_AUTH", "STAGE_CONFIG",
    "STAGE_DEPENDENCY", "STAGE_INTERNAL", "STAGE_NETWORK", "STAGE_POLICY",
    "STAGE_PROTOCOL", "STAGE_TIMEOUT", "STAGE_TLS", "STAGES", "STATUS_FAILED",
    "STATUS_OK", "STATUS_PARTIAL", "STATUS_SKIPPED", "STATUSES",
    "adapter_available", "adapter_for", "invoke_failed", "invoke_ok",
    "invoke_unknown", "registered_kinds", "register_adapter", "stage_failed",
    "stage_ok", "stage_partial", "stage_skipped",
    "FetchOutcome", "NetworkPolicy", "RedirectStep", "ResolvedTarget",
    "current_policy", "policy_from_mapping", "reset_policy_cache", "safe_get",
    "PoolBusy", "PoolLimits", "ProbeOutcome", "TestPool",
    "abandoned_snapshot", "limits_from_mapping", "pool_for_deployment",
    "reset_abandoned", "reset_pools", "run_probe",
    "run_probe_with_kill_switch",
]
