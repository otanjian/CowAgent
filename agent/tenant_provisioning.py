# encoding:utf-8
"""Copy the default tenant's Agents into another tenant.

A new tenant starts empty, so an operator who has already curated a set of
Agents in the *source* tenant (the tenant that holds the global default Agent)
has to rebuild all of them by hand. This module is the orchestration that copies
a chosen subset over as **independent** Agents: each copy gets its own id and its
own workspace inside the target tenant's shared root, so the two sides evolve
separately afterwards.

It is deliberately a thin layer over two existing owners rather than a new store:

* :class:`~agent.admin.AgentAdminService` owns the roster and the workspace. The
  per-agent copy itself is its ``clone_agent`` primitive.
* :class:`~auth.service.IdentityService` owns tenancy. The binding — and with it
  the clone provenance that makes a re-run idempotent — lives in ``identity.db``.

Those two stores cannot commit together, so the design compensates per agent
instead of pretending to be atomic: each agent is copied and bound one at a
time, a failure cleans up whatever that agent created, and the response reports
per-source success, skip and failure so the caller never sees a false "all
good". A crash in between leaves an unbound roster entry, which the id planner
adopts on the next run rather than duplicating.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: The roster's own id contract (kept in step with ``agent.registry``).
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_AGENT_ID_LEN = 64


class TenantProvisioningError(ValueError):
    """A copy request that cannot be carried out, with an HTTP-shaped reason."""

    def __init__(self, message: str, *, code: str = "bad_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class CopySource:
    """The tenant a copy reads from, and how it was chosen."""

    tenant_id: str
    code: str
    name: str
    #: ``default_agent_binding`` (the anchor resolved) or ``most_bindings``.
    resolved_by: str
    #: Which of the source's Agents is its default, when one can be told.
    default_agent_id: Optional[str] = None


def _sanitise_agent_id(value: str) -> str:
    """Fold an arbitrary string into the roster's id alphabet."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", value or "")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.strip("-_")


class TenantAgentProvisioner:
    """Read the copyable candidates and copy a selection into a target tenant."""

    def __init__(self, identity_service, admin_service=None):
        self._svc = identity_service
        self._admin = admin_service

    @property
    def admin(self):
        if self._admin is None:
            from agent.admin import get_agent_admin_service
            self._admin = get_agent_admin_service()
        return self._admin

    # --- reads ---------------------------------------------------------------

    def _roster(self) -> Dict[str, Any]:
        return self.admin.snapshot()

    def _roster_by_id(self) -> Dict[str, Dict[str, Any]]:
        return {agent["id"]: agent for agent in self._roster().get("agents", [])}

    def _source_default_agent_id(self, tenant_id: str) -> Optional[str]:
        """Which Agent of ``tenant_id`` should be treated as its default.

        The global default Agent is the authority when it is actually bound to
        this tenant. A tenant resolved by the "most bindings" fallback has no
        such anchor, so its own configured default (if any) is used instead —
        and otherwise there simply isn't one, which the takeover rule handles.
        """
        global_default = self._roster().get("default_agent_id")
        if global_default:
            binding = self._svc.get_agent_binding(global_default)
            if binding and binding["tenant_id"] == tenant_id:
                return global_default
        return self._svc.tenant_default_agent_id(tenant_id) or None

    def resolve_source(self, target_tenant_id: str) -> CopySource:
        """Resolve the copy source for ``target_tenant_id``.

        Anchored on the tenant the global default Agent is bound to, falling
        back to the tenant with the most bindings. The target tenant is never a
        candidate — a tenant cannot be its own copy source.
        """
        anchor = self._roster().get("default_agent_id")
        if anchor:
            binding = self._svc.get_agent_binding(anchor)
            if binding and binding["tenant_id"] == target_tenant_id:
                # The target *is* the source tenant. Falling through to the
                # "most bindings" fallback would silently pick a third tenant,
                # which is not what an operator looking at this tenant asked for.
                raise TenantProvisioningError(
                    "a tenant cannot copy agents from itself",
                    code="source_is_target", status=409)
            if binding:
                tenant = self._svc.get_tenant(binding["tenant_id"])
                if tenant:
                    return CopySource(
                        tenant_id=tenant["id"], code=tenant["code"], name=tenant["name"],
                        resolved_by="default_agent_binding",
                        default_agent_id=self._source_default_agent_id(tenant["id"]),
                    )

        counts: Dict[str, int] = {}
        for binding in self._svc.list_agent_bindings():
            tenant_id = binding["tenant_id"]
            if tenant_id == target_tenant_id:
                continue
            counts[tenant_id] = counts.get(tenant_id, 0) + 1
        for tenant_id in sorted(counts, key=lambda tid: (-counts[tid], tid)):
            tenant = self._svc.get_tenant(tenant_id)
            if tenant:
                return CopySource(
                    tenant_id=tenant["id"], code=tenant["code"], name=tenant["name"],
                    resolved_by="most_bindings",
                    default_agent_id=self._source_default_agent_id(tenant["id"]),
                )

        raise TenantProvisioningError(
            "no tenant holds any agent to copy from",
            code="no_source", status=409,
        )

    def _candidates(self, source: CopySource, target_tenant_id: str) -> List[Dict[str, Any]]:
        """The source's copyable Agents, with their already-copied status.

        Derived from the source's *bindings* joined to the live roster, so a
        binding whose Agent no longer exists is not offered. Host paths are
        deliberately absent: this is a projection for the console.
        """
        roster = self._roster_by_id()
        out: List[Dict[str, Any]] = []
        for binding in self._svc.agents_for_tenant(source.tenant_id):
            agent_id = binding["agent_id"]
            profile = roster.get(agent_id)
            if profile is None:
                continue
            clone = self._svc.clone_of(target_tenant_id, agent_id)
            out.append({
                "source_agent_id": agent_id,
                "name": profile.get("name") or agent_id,
                "enabled": bool(profile.get("enabled", True)),
                "is_default": agent_id == source.default_agent_id,
                "already_copied": clone is not None,
                "clone_agent_id": clone["agent_id"] if clone else None,
            })
        out.sort(key=lambda item: (not item["is_default"], item["source_agent_id"]))
        return out

    def _target_agents(self, tenant_id: str) -> List[Dict[str, Any]]:
        roster = self._roster_by_id()
        default_id = self._svc.tenant_default_agent_id(tenant_id)
        out: List[Dict[str, Any]] = []
        for binding in self._svc.agents_for_tenant(tenant_id):
            profile = roster.get(binding["agent_id"])
            if profile is None:
                continue
            out.append({
                "id": profile["id"],
                "name": profile.get("name") or profile["id"],
                "enabled": bool(profile.get("enabled", True)),
                "is_default": profile["id"] == default_id,
            })
        return out

    def read_tenant(self, target_tenant_id: str) -> Dict[str, Any]:
        """Everything the tenant editor's Agent tab needs, and nothing more.

        An unresolvable source is reported (``copy_source`` is null with a
        reason) rather than raised: the tab must still render the tenant's own
        agents so the operator can see the current state.
        """
        if not self._svc.get_tenant(target_tenant_id):
            raise TenantProvisioningError("tenant not found", code="not_found", status=404)
        result: Dict[str, Any] = {
            "tenant_id": target_tenant_id,
            "agents": self._target_agents(target_tenant_id),
            "source_error": None,
        }
        try:
            source = self.resolve_source(target_tenant_id)
        except TenantProvisioningError as exc:
            result["copy_source"] = None
            result["source_error"] = str(exc)
            return result
        candidates = self._candidates(source, target_tenant_id)
        payload = {
            "tenant_id": source.tenant_id,
            "code": source.code,
            "name": source.name,
            "resolved_by": source.resolved_by,
            "default_agent_id": source.default_agent_id,
            "candidates": candidates,
        }
        if not candidates:
            payload["reason"] = "the source tenant has no copyable agents"
        result["copy_source"] = payload
        return result

    # --- identity planning ---------------------------------------------------

    def _derive_id(self, source_agent_id: str, target_code: str) -> str:
        derived = _sanitise_agent_id("%s-%s" % (source_agent_id, target_code))
        if len(derived) > _MAX_AGENT_ID_LEN:
            derived = derived[:_MAX_AGENT_ID_LEN].strip("-_")
        return derived or "agent"

    @staticmethod
    def _with_suffix(base: str, sequence: int) -> str:
        suffix = "-%d" % sequence
        room = _MAX_AGENT_ID_LEN - len(suffix)
        return base[:room].strip("-_") + suffix

    def _plan_one(self, source_agent_id: str, target: Dict[str, Any],
                  target_root: Path) -> tuple:
        """Decide the clone's id and workspace, adopting a previous orphan.

        Returns ``(agent_id, workspace, adopted)``. A crash between the roster
        write and the binding leaves an Agent that is ours but unbound; when its
        id is exactly the one we would derive and its workspace is where we put
        clones, it is adopted instead of being shadowed by a ``-2`` twin.
        """
        agents_root = target_root / "agents"
        base = self._derive_id(source_agent_id, target["code"])
        roster = self._roster_by_id()

        orphan = roster.get(base)
        if orphan is not None and self._svc.get_agent_binding(base) is None:
            workspace = Path(orphan["workspace"]).resolve()
            if workspace.parent == agents_root:
                return base, str(workspace), True

        agent_id, sequence = base, 1
        while agent_id in roster:
            sequence += 1
            agent_id = self._with_suffix(base, sequence)
        return agent_id, str(agents_root / agent_id), False

    def _compensate(self, agent_id: Optional[str], target_root: Path) -> None:
        """Undo a half-finished agent so a retry starts from a clean slate."""
        if not agent_id:
            return
        try:
            if agent_id in self._roster_by_id():
                self.admin.delete_agent(agent_id)
        except Exception as exc:  # pragma: no cover - best-effort repair
            logger.warning("[TenantProvisioning] could not drop roster entry %s: %s",
                           agent_id, exc)
        workspace = target_root / "agents" / agent_id
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    # --- the copy ------------------------------------------------------------

    def copy(self, *, target_tenant_id: str, source_agent_ids: Sequence[str],
             recent_password: str, actor_user_id: Optional[str] = None,
             actor_username: Optional[str] = None) -> Dict[str, Any]:
        """Copy ``source_agent_ids`` from the resolved source into the target.

        The order of the gates is the order of the promise: the actor's recent
        password is checked first, then the source is resolved, then the whole
        selection is validated *before* anything is written. An invalid request
        therefore cannot leave a half-applied copy behind. Past that point each
        agent is independent: one failure is reported per source id, never
        silently dropped, and never turns the response into a success.
        """
        self._svc.require_recent_password(actor_user_id, recent_password)
        tenant = self._svc.get_tenant(target_tenant_id)
        if not tenant:
            raise TenantProvisioningError("tenant not found", code="not_found", status=404)
        source = self.resolve_source(target_tenant_id)
        if source.tenant_id == target_tenant_id:
            raise TenantProvisioningError(
                "a tenant cannot copy agents from itself",
                code="bad_request", status=400)

        selected = list(dict.fromkeys(source_agent_ids or []))
        if not selected:
            raise TenantProvisioningError(
                "select at least one agent to copy", code="bad_request", status=400)

        candidates = {c["source_agent_id"]: c
                      for c in self._candidates(source, target_tenant_id)}
        invalid = [agent_id for agent_id in selected if agent_id not in candidates]
        if invalid:
            raise TenantProvisioningError(
                "not a copyable agent of the source tenant: %s" % ", ".join(invalid),
                code="bad_request", status=400)

        target_root = Path(tenant["shared_root"]).resolve()
        had_agents = bool(self._svc.agents_for_tenant(target_tenant_id)) or bool(
            self._svc.tenant_default_agent_id(target_tenant_id))

        copied: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []
        failed: List[Dict[str, str]] = []
        for source_id in selected:
            existing = self._svc.clone_of(target_tenant_id, source_id)
            if existing:
                skipped.append({"source_agent_id": source_id,
                                "agent_id": existing["agent_id"]})
                continue
            agent_id: Optional[str] = None
            adopted = False
            try:
                agent_id, workspace, adopted = self._plan_one(source_id, tenant, target_root)
                if not adopted:
                    self.admin.clone_agent(source_id, agent_id, workspace=workspace)
                self._svc.bind_agent(
                    tenant_id=target_tenant_id, agent_id=agent_id,
                    cloned_from_agent_id=source_id, actor_user_id=actor_user_id)
                copied.append({"source_agent_id": source_id, "agent_id": agent_id})
            except Exception as exc:
                self._compensate(agent_id, target_root)
                logger.warning("[TenantProvisioning] copy of %s failed: %s", source_id, exc)
                failed.append({"source_agent_id": source_id, "error": str(exc)})

        default_agent_id = None
        if not had_agents and copied:
            default_agent_id = self._appoint_default(
                source, selected, copied, target_tenant_id, actor_user_id)

        self._svc.record_agent_copy_event(
            actor_user_id=actor_user_id, actor_username=actor_username,
            source_tenant_id=source.tenant_id, target_tenant_id=target_tenant_id,
            selected=selected,
            copied=[item["agent_id"] for item in copied],
            skipped=[item["agent_id"] for item in skipped],
            failed=[item["source_agent_id"] for item in failed],
            default_agent_id=default_agent_id,
            result="success" if not failed else "partial")

        return {
            "selected": len(selected),
            "copied": len(copied),
            "skipped": len(skipped),
            "failed": failed,
            "copied_agent_ids": copied,
            "skipped_agent_ids": skipped,
            "default_agent_id": default_agent_id,
        }

    def _appoint_default(self, source: CopySource, selected: Sequence[str],
                         copied: Sequence[Dict[str, str]], target_tenant_id: str,
                         actor_user_id: Optional[str]) -> str:
        """Give a previously empty target a default, so it is immediately usable.

        Preference is the clone of the source's own default (that is the Agent
        the source tenant considered canonical). When the source default was not
        part of this selection, the first successfully copied clone takes over —
        otherwise a tenant with several bindings would have no resolvable
        default at all.
        """
        default_agent_id = None
        if source.default_agent_id and source.default_agent_id in selected:
            clone = self._svc.clone_of(target_tenant_id, source.default_agent_id)
            if clone:
                default_agent_id = clone["agent_id"]
        if default_agent_id is None:
            default_agent_id = copied[0]["agent_id"]
        self._svc.set_tenant_default_agent(
            tenant_id=target_tenant_id, agent_id=default_agent_id,
            actor_user_id=actor_user_id)
        return default_agent_id


def get_tenant_agent_provisioner() -> TenantAgentProvisioner:
    """Build a provisioner over the process-wide identity and agent services."""
    from auth.service import get_identity_service
    return TenantAgentProvisioner(get_identity_service())
