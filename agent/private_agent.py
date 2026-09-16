# encoding:utf-8
"""A member's own private Agent: creation, idempotency, cleanup (tasks 4.1–4.3).

The system already supplies every member one assistant
(:mod:`agent.personal_assistant`). This module is the *member's own* creation
path, and its central design decision is what it does **not** accept: there is no
``owner_user_id`` and no ``scope`` argument anywhere on
:meth:`PrivateAgentService.create_private_agent`. Tenant and owner are read from
the trusted caller context.

A request that *tries* to name an owner or ask for a shared object is
**refused**, not ignored — see
:meth:`PrivateAgentService.create_private_agent`'s ``requested_*`` parameters.
Ignoring the field would leave the client believing it chose the owner, which is
the belief the refusal exists to prevent; and a caller that sends one is either
confused or probing.

Nothing here touches either default registration. Self-creation is a *second*
private object next to the supplied assistant, so the member's personal default
and the tenant's default must both be exactly where they were — that is task 4.3
seen from the creation side, while ``PersonalAssistantProvisioner.owned_agent_id``
is the same task seen from the provisioning side (idempotency by provenance, so a
self-made Agent does not masquerade as the system's).

Failure handling follows :mod:`agent.personal_assistant`: the roster and
``identity.db`` cannot commit together, so a create that dies before the binding
is **compensated** (the roster entry and its workspace are removed) and the next
identical attempt adopts an orphan rather than piling up ``-2`` suffixes. A
half-created Agent must never be reachable — a binding is what makes an Agent
usable, so "created but unbound" has to be invisible and retryable.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from agent.deletion_guard import deletion_conflicts
from agent.tenant_provisioning import _MAX_AGENT_ID_LEN, _sanitise_agent_id
from auth.service import IdentityServiceError

logger = logging.getLogger(__name__)

#: The provenance recorded for a member's own object. Distinct from
#: ``provisioned_assistant`` on purpose: it is what keeps the supplied-assistant
#: provisioner from treating this object as one it handed out.
USER_CREATED = "user_created"


class PrivateAgentService:
    """Create and delete a member's own private Agents, within their own scope."""

    def __init__(self, identity_service, admin_service=None, runtime_probe=None):
        self._svc = identity_service
        self._admin_service = admin_service
        # ``runtime_probe(agent_id) -> bool`` answers "is a task still using this
        # Agent?". Injectable so the delete gate can be tested without standing
        # up a live bridge; the default asks the real one (see
        # ``deletion_guard.bridge_has_live_agent``).
        self._runtime_probe = runtime_probe

    # --- collaborators ---------------------------------------------------

    @property
    def admin(self):
        if self._admin_service is None:
            from agent.admin import get_agent_admin_service

            self._admin_service = get_agent_admin_service()
        return self._admin_service

    def _roster(self) -> Dict[str, Dict[str, Any]]:
        return {a["id"]: a for a in (self.admin.snapshot().get("agents") or [])}

    # --- authorization ---------------------------------------------------

    def _require_active_member(self, user_id: str, tenant_id: str) -> None:
        """The single gate both create and delete pass through.

        Membership is read from the store rather than taken on trust, so a
        caller who names a tenant they belong to no longer belongs to is refused
        on the next request instead of on the next session refresh.
        """
        if not user_id or not tenant_id:
            raise IdentityServiceError("tenant and user are required",
                                       code="forbidden", status=403)
        membership = self._svc.get_membership(user_id, tenant_id)
        if not membership or not membership.get("user_active", True):
            raise IdentityServiceError("not an active member of this tenant",
                                       code="forbidden", status=403)

    def _require_usable_source(self, user_id: str, tenant_id: str,
                               source_agent_id: str) -> None:
        """The template must be an Agent the member may actually *use*.

        Reusing the member's own resource-authorization answer is what keeps this
        from becoming a second, divergent notion of "allowed": a private Agent
        owned by someone else fails here for exactly the reason it fails the
        console, and a shared Agent the member holds ``use`` on passes.
        """
        from auth.policy import RESOURCE_ACTIONS  # noqa: F401  (documents the kind)

        if source_agent_id not in self._svc.tenant_agent_ids(tenant_id):
            raise IdentityServiceError("source agent is not in this tenant",
                                       code="not_found", status=404)
        allowed = self._svc.resource_ids_for(
            user_id, tenant_id, "agent", "use")
        if allowed is not None and source_agent_id not in allowed:
            raise IdentityServiceError("source agent is not usable by this member",
                                       code="forbidden", status=403)

    # --- planning --------------------------------------------------------

    def plan_agent_id(self, *, tenant_id: str, user_id: str,
                      name: Optional[str]) -> str:
        """The id this member's next object would take, without creating it.

        Deterministic so a retry looks for the *same* orphan: the id is derived
        from the member and their chosen name, not from a counter, and the
        adoption check in :meth:`_plan` is what makes reuse safe.
        """
        base = _sanitise_agent_id("%s-%s" % (
            (name or "").strip() or "agent", self._identity_hint(user_id)))
        return (base[:_MAX_AGENT_ID_LEN] or "private-agent")

    def _identity_hint(self, user_id: str) -> str:
        return user_id

    def _plan(self, tenant_id: str, user_id: str,
              name: Optional[str]) -> tuple:
        """``(agent_id, workspace, adopt)`` for this member's object.

        ``adopt`` is True when the roster entry exists with the expected workspace
        but has **no binding** — the signature of a create that died after writing
        the roster and before binding. Reusing it is how a retry completes rather
        than duplicating. A roster entry that *is* bound is someone else's object
        (or already this member's) and the next suffix is used instead.
        """
        root = self._svc.tenant_shared_root(tenant_id) or ""
        if not root:
            raise IdentityServiceError("tenant has no shared root",
                                       code="error", status=500)
        roster = self._roster()
        base = _sanitise_agent_id("%s-%s" % (
            (name or "").strip() or "agent", self._identity_hint(user_id)))
        base = base[:_MAX_AGENT_ID_LEN] or "private-agent"
        candidate = base
        suffix = 2
        while True:
            workspace = os.path.join(root, "agents", candidate)
            if candidate not in roster:
                return candidate, workspace, False
            if self._svc.get_agent_binding(candidate) is None and \
                    os.path.realpath(roster[candidate].get("workspace") or "") \
                    == os.path.realpath(workspace):
                return candidate, workspace, True
            tail = "-%d" % suffix
            candidate = base[: _MAX_AGENT_ID_LEN - len(tail)] + tail
            suffix += 1

    def _mine(self, tenant_id: str, user_id: str) -> list:
        return [b for b in self._svc.agents_for_tenant(tenant_id)
                if b.get("private_owner_user_id") == user_id]

    # --- create ----------------------------------------------------------

    def create_private_agent(
        self,
        *,
        user_id: str,
        tenant_id: str,
        name: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        knowledge_mode: str = "own",
        requested_owner_user_id: Optional[str] = None,
        requested_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stand up one private Agent owned by ``user_id``.

        ``requested_owner_user_id`` / ``requested_scope`` exist only to be
        *rejected*. They are not inputs to anything: a well-behaved caller never
        sends them, and a caller that does is refused before any write, so a
        forged-ownership request cannot leave a partial workspace behind.

        Raises :class:`IdentityServiceError` on any refusal or failure. Unlike the
        member-creation provisioner, this is a member-initiated action: a failure
        must be reported to the member who asked, not swallowed into a status.
        """
        if requested_owner_user_id is not None:
            raise IdentityServiceError(
                "the owner of a private agent is fixed by the caller context",
                code="forbidden", status=403)
        if requested_scope is not None:
            raise IdentityServiceError(
                "a private agent cannot be created with an explicit scope",
                code="forbidden", status=403)

        self._require_active_member(user_id, tenant_id)
        # Cheap refusal before a clone and a workspace copy when the deployment
        # has withdrawn private-agent creation (task 9.1). Non-authoritative: the
        # atomic bind below re-checks inside the quota transaction.
        self._svc.require_personal_capability("user_private_agent_management")
        # The console-wide switch, in the same cheap position: it is enforced by
        # ``bind_private_agent_with_quota`` below, and reading it here too means a
        # withdrawn console does not clone a workspace only to delete it again.
        self._svc.require_personal_capability("member_personal_console")

        if source_agent_id:
            self._require_usable_source(user_id, tenant_id, source_agent_id)

        # Cheap refusal before a clone and a workspace copy. Non-authoritative:
        # the atomic bind below is what actually decides, so a race that slips
        # past this still cannot over-create.
        self._svc.check_private_agent_quota(tenant_id=tenant_id, user_id=user_id)

        agent_id, workspace, adopt = self._plan(tenant_id, user_id, name)

        try:
            if not adopt:
                if source_agent_id:
                    self.admin.clone_agent(
                        source_agent_id, agent_id,
                        name=(name or "").strip() or None,
                        workspace=workspace, knowledge_mode=knowledge_mode)
                else:
                    self.admin.create_agent(
                        agent_id=agent_id,
                        name=(name or "").strip() or agent_id,
                        workspace=workspace)
            elif source_agent_id:
                # Adopted orphan still needs no clone: the workspace is already
                # the one we would have written.
                pass
            self._svc.bind_private_agent_with_quota(
                tenant_id=tenant_id, agent_id=agent_id, user_id=user_id,
                origin=USER_CREATED, actor_user_id=user_id)
        except IdentityServiceError:
            self._compensate(agent_id)
            raise
        except Exception as exc:
            self._compensate(agent_id)
            logger.warning("[PrivateAgent] create for %s failed: %s", user_id, exc)
            raise IdentityServiceError(str(exc), code="error", status=500)

        try:
            self._svc.record_personal_agent_event(
                action="member.personal_agent.self_create",
                tenant_id=tenant_id, user_id=user_id, result="success",
                agent_id=agent_id, actor_user_id=user_id)
        except Exception as exc:  # pragma: no cover - audit must not break the flow
            logger.warning("[PrivateAgent] audit for %s failed: %s", agent_id, exc)

        return {
            "status": "created",
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "owner_user_id": user_id,
            "workspace": workspace,
            "source_agent_id": source_agent_id or "",
            "adopted": adopt,
        }

    def _compensate(self, agent_id: Optional[str]) -> None:
        """Undo what a failed create wrote, best effort.

        The workspace is only removed when it is the layout we created
        (``<shared root>/agents/<id>``) — the same restraint ``delete_agent``
        applies, and for the same reason: a hand-picked path is not ours to erase.
        """
        if not agent_id:
            return
        try:
            self._svc.release_deleted_agent(agent_id=agent_id, actor_user_id=None)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[PrivateAgent] could not release %s: %s", agent_id, exc)
        profile = self._roster().get(agent_id) or {}
        try:
            self.admin.delete_agent(agent_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[PrivateAgent] could not remove %s: %s", agent_id, exc)
        workspace = Path(profile.get("workspace") or "")
        try:
            if workspace.is_dir() and workspace.name == agent_id \
                    and workspace.parent.name == "agents":
                shutil.rmtree(workspace, ignore_errors=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[PrivateAgent] could not clear %s: %s", workspace, exc)

    # --- delete ----------------------------------------------------------

    def delete_private_agent(self, *, user_id: str, tenant_id: str,
                             agent_id: str) -> Dict[str, Any]:
        """Delete one of the member's **own** objects, and nothing else.

        The provenance check is the point. A member deleting their own
        self-made object is routine; the supplied assistant, a system object of
        unknown origin, another member's private Agent and every shared Agent are
        all refused here even though the same person may *use* them. Without the
        origin test, "I can maintain my own Agent" would quietly become "I can
        delete the assistant the system gave me".
        """
        self._require_active_member(user_id, tenant_id)
        binding = self._svc.get_agent_binding(agent_id)
        if not binding or binding.get("tenant_id") != tenant_id:
            raise IdentityServiceError("agent not found", code="not_found", status=404)
        if not binding.get("private_owner_user_id"):
            raise IdentityServiceError(
                "a shared agent cannot be deleted through the personal entry",
                code="forbidden", status=403)
        if binding["private_owner_user_id"] != user_id:
            raise IdentityServiceError(
                "another member's private agent cannot be deleted",
                code="forbidden", status=403)
        if binding.get("origin", "unknown") != USER_CREATED:
            raise IdentityServiceError(
                "only a self-created agent can be deleted by its owner",
                code="forbidden", status=403)

        # A permission question was already answered; this is a *dependency*
        # question, and it comes after provenance so a refused object never gets
        # diagnosed as "in use" (task 4.5).
        conflicts = self._deletion_conflicts(tenant_id, agent_id)
        if conflicts:
            raise IdentityServiceError(
                "the agent is still in use: " + "; ".join(conflicts),
                code="conflict", status=409)

        # Detach before removing the roster entry. The two stores cannot commit
        # together, and an object with no binding is unreachable while a roster
        # entry with no binding is merely an adoptable orphan — so this order
        # leaves the recoverable state, never a live-looking ghost.
        try:
            self._svc.release_deleted_agent(
                agent_id=agent_id, actor_user_id=user_id)
        except Exception as exc:
            logger.warning("[PrivateAgent] detach %s failed: %s", agent_id, exc)
            raise IdentityServiceError(str(exc), code="error", status=500)
        try:
            self.admin.delete_agent(agent_id)
        except Exception as exc:
            logger.warning("[PrivateAgent] delete %s failed: %s", agent_id, exc)
            raise IdentityServiceError(str(exc), code="error", status=500)
        try:
            self._svc.record_personal_agent_event(
                action="member.personal_agent.self_delete",
                tenant_id=tenant_id, user_id=user_id, result="success",
                agent_id=agent_id, actor_user_id=user_id)
        except Exception as exc:  # pragma: no cover - audit must not break the flow
            logger.warning("[PrivateAgent] delete audit for %s failed: %s", agent_id, exc)
        return {"status": "deleted", "agent_id": agent_id}

    # --- delete conflicts -------------------------------------------------

    def _deletion_conflicts(self, tenant_id: str, agent_id: str) -> list:
        """Reasons deleting ``agent_id`` right now would break something.

        Delegated to :mod:`agent.deletion_guard` so this path, the console's
        shared delete and the roster service answer the same question once
        (task 4.3): a member erasing their own object and an administrator
        retiring a shared one must not differ in what counts as "still in use".
        """
        return deletion_conflicts(
            agent_id,
            tenant_id=tenant_id,
            identity_service=self._svc,
            runtime_probe=self._runtime_probe,
        )


def get_private_agent_service() -> PrivateAgentService:
    """Build the service against the process's identity store and roster."""
    from auth.service import get_identity_service

    return PrivateAgentService(get_identity_service())


__all__ = ["PrivateAgentService", "USER_CREATED", "get_private_agent_service"]
