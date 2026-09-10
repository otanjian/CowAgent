# encoding:utf-8
"""End-to-end acceptance for the personal conversation & memory change.

This is the *operational* acceptance harness referenced by tasks 7.3/7.4 of
``openspec/changes/personal-conversation-and-memory``. It is deliberately not a
unit test: it drives a real instance (real ``identity.db``, real tenant shared
root, real Agent workspaces) so the promises are checked against the same
stores production uses.

What it verifies
----------------
1. **No forced Agent selection.** Two accounts (an admin and a plain member)
   both see the tenant's default Agent marked ``is_default`` and ``can_chat``
   without any explicit selection.
2. **Agent-less chat launch works.** A ``POST /message`` with no ``agent_id``
   is accepted for both accounts and the session lands on the tenant's resolved
   default Agent, owned by the caller.
3. **Sessions stay private.** A second account cannot resume the first one's
   session id (masked as 404).
4. **Personal memory is per-user and Agent-independent.** One user's personal
   memory file lives under ``<tenant shared root>/users/<user_id>/``, is
   visible from *every* Agent that user may use, is invisible to the other
   user, and its index rows carry ``scope=user`` with the owner's ``user_id``.

Usage
-----
Run it with the instance stopped or running; it only reads the HTTP surface and
the SQLite stores. Point ``--data-dir`` at the deployment's data root (the
directory holding ``config.json`` and ``identity.db``)::

    python scripts/acceptance_personal_context.py \\
        --data-dir ~/.cow --base-url http://127.0.0.1:9899 \\
        --tenant-code default \\
        --admin alice:Str0ng-Pass!23 --member bob:Str0ng-Pass!23

Without ``--base-url`` the HTTP checks are reported as SKIPPED and only the
memory/ownership checks run. ``--seed-admin``/``--seed-member`` are only needed
on a scratch sandbox where the accounts do not exist yet; never use them
against a production ``identity.db``.

Prerequisite for the member account
-----------------------------------
The builtin ``member`` role carries ``agent.read`` but neither ``chat.use`` nor
any per-Agent ``read``/``use`` resource grant, and builtin roles are immutable.
A freshly created member therefore sees **no** Agent and cannot chat at all.
Before running the ``--member`` checks, the tenant admin must assign a custom
role that grants ``chat.use`` + ``agent.use`` + ``agent.read`` together with the
``agent:<id>`` resource grants for the Agents that member should use. This is
the existing role-resource authorization model, not a gap introduced by this
change -- but "no Agent selection" can only be verified for a member who is
authorized to chat in the first place.

Exit code is 0 only when every executed check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIPPED"
_results: List[Tuple[str, str, str]] = []


def check(name: str, ok: Optional[bool], detail: str = "") -> bool:
    """Record one assertion. ``ok=None`` records a SKIP."""
    verdict = SKIP if ok is None else (PASS if ok else FAIL)
    _results.append((name, verdict, detail))
    line = "  [%-7s] %s" % (verdict, name)
    if detail:
        line += "  -- %s" % detail
    print(line)
    return bool(ok)


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

class Client:
    """Minimal cookie-jar client: the console authenticates with a cookie."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.cookie: Optional[str] = None

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 tenant_id: Optional[str] = None) -> Tuple[int, dict, str]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        # The console's CSRF check is same-origin based.
        req.add_header("Origin", self.base)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if tenant_id:
            req.add_header("X-Tenant-ID", tenant_id)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
                set_cookie = resp.headers.get("Set-Cookie") or ""
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            status = exc.code
            set_cookie = ""
        except Exception as exc:  # connection refused, timeout, ...
            return 0, {}, str(exc)
        if set_cookie and "cow_session=" in set_cookie:
            self.cookie = set_cookie.split(";")[0].strip()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"_raw": raw[:400]}
        return status, payload, raw

    def login(self, username: str, password: str) -> Tuple[int, dict]:
        status, payload, _ = self._request(
            "POST", "/auth/login", {"username": username, "password": password})
        return status, payload

    def get(self, path: str, tenant_id: Optional[str] = None):
        return self._request("GET", path, None, tenant_id)

    def post(self, path: str, body: dict, tenant_id: Optional[str] = None):
        return self._request("POST", path, body, tenant_id)


# --------------------------------------------------------------------------
# store helpers
# --------------------------------------------------------------------------

def _agent_store(workspace: str) -> Path:
    """The conversation/memory index that belongs to one Agent workspace."""
    return Path(workspace) / "memory" / "long-term" / "index.db"


def _sessions_for(workspace: str, session_ids: List[str]) -> Dict[str, Tuple[str, str]]:
    db = _agent_store(workspace)
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db))
    try:
        marks = ",".join("?" for _ in session_ids)
        rows = con.execute(
            "SELECT session_id, channel_type, owner FROM sessions"
            " WHERE session_id IN (%s)" % marks, tuple(session_ids)).fetchall()
    finally:
        con.close()
    return {row[0]: (row[1], row[2]) for row in rows}


def _apply_identity(tenant_id: str, user_id: str, agent_id: str):
    from common.runtime_identity import RuntimeIdentity, use_identity
    return use_identity(RuntimeIdentity(
        agent_id=agent_id, user_id=user_id, tenant_id=tenant_id))


def _memory_manager(workspace: str):
    from agent.memory.config import MemoryConfig
    from agent.memory.manager import MemoryManager
    # min_score=0: keyword-only corpora produce near-zero BM25 ranks, and this
    # harness asserts ownership/isolation, not relevance ranking.
    return MemoryManager(
        config=MemoryConfig(workspace_root=workspace, min_score=0.0),
        embedding_provider=None)


def _search(user_id: str, tenant_id: str, agent_id: str, workspace: str,
            marker: str) -> int:
    with _apply_identity(tenant_id, user_id, agent_id):
        manager = _memory_manager(workspace)
        asyncio.run(manager.sync())
        return len(asyncio.run(manager.search(marker, user_id=user_id)))


def _chunk_rows(workspace: str, marker: str) -> List[Tuple[str, str, Optional[str]]]:
    db = _agent_store(workspace)
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT path, scope, user_id FROM chunks WHERE text LIKE ?",
            ("%" + marker + "%",)).fetchall()
    finally:
        con.close()


# --------------------------------------------------------------------------
# the acceptance run
# --------------------------------------------------------------------------

def run(args) -> int:
    os.environ["COW_DATA_DIR"] = os.path.expanduser(args.data_dir)
    sys.path.insert(0, REPO_ROOT)

    from config import load_config
    load_config()

    from agent.registry import get_agent_registry
    from auth.service import IdentityService

    db_path = os.path.join(os.path.expanduser(args.data_dir), "identity.db")
    if not os.path.exists(db_path):
        print("no identity.db at %s -- is this a database-identity deployment?"
              % db_path)
        return 2
    svc = IdentityService(db_path)

    tenant = svc._find_tenant_by_code(args.tenant_code)
    if not tenant:
        print("tenant %r not found" % args.tenant_code)
        return 2
    tenant_id = tenant["id"]
    resolved_default = svc.resolved_default_agent_id(tenant_id)

    print("=" * 74)
    print("personal conversation & memory acceptance")
    print("=" * 74)
    print("data root          : %s" % os.path.expanduser(args.data_dir))
    print("tenant             : %s (%s)" % (args.tenant_code, tenant_id))
    print("shared root        : %s" % tenant.get("shared_root"))
    print("resolved default   : %s" % resolved_default)
    print("tenant configured  : %s" % svc.tenant_default_agent_id(tenant_id))
    print("bindings           : %s" % [
        (b["agent_id"], b["private_owner_user_id"]) for b in svc.agents_for_tenant(tenant_id)])
    print()

    # -- accounts ---------------------------------------------------------
    admin_id = _find_or_seed(svc, tenant_id, args.admin, args.seed_admin,
                             roles=["tenant_admin"])
    member_id = _find_or_seed(svc, tenant_id, args.member, args.seed_member,
                              roles=args.member_roles)
    if admin_id is None or member_id is None:
        return 2
    print("accounts           : admin=%s member=%s" % (admin_id, member_id))

    registry = get_agent_registry()
    agent_ids = [p.id for p in registry.list()]
    by_id = {p.id: p for p in registry.list()}
    primary = args.agent_a or resolved_default
    secondary = args.agent_b or next(
        (a for a in agent_ids if a != primary), None)
    print("agents             : primary=%s secondary=%s of %s"
          % (primary, secondary, agent_ids))
    print()

    # -- 1. no forced selection ------------------------------------------
    print("1. members see the tenant default without selecting an Agent")
    client_admin = Client(args.base_url) if args.base_url else None
    client_member = Client(args.base_url) if args.base_url else None
    if not args.base_url:
        check("HTTP checks", None, "--base-url not given")
    else:
        for label, client, cred in (
                ("admin", client_admin, args.admin),
                ("member", client_member, args.member)):
            status, payload = client.login(cred[0], cred[1])
            if not check("%s can log in" % label, status == 200,
                         "http %s" % status):
                continue
            if payload.get("must_change_password"):
                check("%s is not a restricted account" % label, False,
                      "must_change_password=1; complete the password change first")
                continue
            status, payload, _ = client.get("/api/agents", tenant_id)
            agents = payload.get("agents") or []
            ids = [a.get("id") for a in agents]
            check("%s sees the tenant's Agents" % label,
                  status == 200 and bool(agents), "http %s ids=%s" % (status, ids))
            default = [a for a in agents if a.get("is_default")]
            check("%s sees exactly one default Agent" % label,
                  len(default) == 1 and default[0].get("id") == resolved_default,
                  "default=%s" % (default[0].get("id") if default else None))
            check("%s may chat with the default Agent" % label,
                  bool(default) and default[0].get("can_chat") is True,
                  "can_chat=%s reason=%s" % (
                      default[0].get("can_chat") if default else None,
                      default[0].get("unavailable_reason") if default else None))
    print()

    # -- 2. agent-less launch --------------------------------------------
    print("2. a chat starts with no Agent selected")
    sessions = {"admin": "acc-admin-%d" % os.getpid(),
                "member": "acc-member-%d" % os.getpid()}
    launched = False
    if not args.base_url:
        check("HTTP checks", None, "--base-url not given")
    else:
        for label, client in (("admin", client_admin), ("member", client_member)):
            if client is None or not client.cookie:
                check("%s launches an Agent-less chat" % label, False, "not logged in")
                continue
            status, payload, raw = client.post(
                "/message", {"message": "acceptance ping", "session_id": sessions[label]},
                tenant_id)
            ok = status == 200 and payload.get("status") == "success"
            check("%s launches an Agent-less chat" % label, ok,
                  "http %s body=%s" % (status, raw[:120]))
            launched = launched or ok
    if launched and primary in by_id:
        rows = _sessions_for(by_id[primary].workspace, list(sessions.values()))
        for label, sid in sessions.items():
            owner = rows.get(sid, (None, None))[1]
            expected = member_id if label == "member" else admin_id
            check("%s's session landed on the default Agent (%s)" % (label, primary),
                  sid in rows and rows[sid][0] == "web",
                  "session=%s" % sid)
            check("%s's session is owned by %s" % (label, label),
                  owner == expected, "owner=%s expected=%s" % (owner, expected))
    print()

    # -- 3. session privacy ----------------------------------------------
    print("3. one member cannot resume another's session")
    if not args.base_url or client_member is None or not client_member.cookie:
        check("HTTP checks", None, "--base-url not given / not logged in")
    else:
        status, payload, _ = client_member.post(
            "/message", {"message": "should be refused",
                         "session_id": sessions["admin"]}, tenant_id)
        check("member is refused the admin's session id",
              status == 404, "http %s code=%s" % (status, payload.get("code")))
    print()

    # -- 4. personal memory ----------------------------------------------
    print("4. personal memory: per-user, but Agent-independent")
    from common import state_dir
    from agent.memory.summarizer import MemoryFlushManager

    marker = args.marker
    member_of = {"admin": admin_id, "member": member_id}
    with _apply_identity(tenant_id, admin_id, primary):
        flush = MemoryFlushManager(workspace_dir=Path(by_id[primary].workspace))
        wrote = flush.write_daily_summary(
            "%s is %s's code word" % (marker, args.admin[0]), user_id=admin_id)
        user_root = str(state_dir.user_root())
        memory_file = str(state_dir.memory_file())
    check("admin's daily summary is written", wrote)
    check("personal memory lives in the user domain",
          memory_file.startswith(user_root),
          "%s (user root %s)" % (memory_file, user_root))
    check("personal memory is not inside an Agent workspace",
          not memory_file.startswith(by_id[primary].workspace),
          memory_file)

    for label, agent_id in (("default Agent", primary), ("second Agent", secondary)):
        if secondary is None and label != "default Agent":
            check("admin's memory is visible from a second Agent", None,
                  "tenant has only one Agent")
            continue
        hits = _search(admin_id, tenant_id, agent_id, by_id[agent_id].workspace, marker)
        check("admin's memory is visible from the %s (%s)" % (label, agent_id),
              hits >= 1, "hits=%d" % hits)

    for agent_id in (primary, secondary):
        if agent_id is None:
            continue
        hits = _search(member_id, tenant_id, agent_id, by_id[agent_id].workspace, marker)
        check("the other member cannot see it (%s)" % agent_id, hits == 0,
              "hits=%d" % hits)

    rows = _chunk_rows(by_id[primary].workspace, marker)
    check("index rows are attributed to the owner",
          bool(rows) and all(r[1] == "user" and r[2] == member_of["admin"] for r in rows),
          "rows=%s" % rows)

    # -- summary ----------------------------------------------------------
    print()
    failed = [r for r in _results if r[1] == FAIL]
    skipped = [r for r in _results if r[1] == SKIP]
    print("=" * 74)
    print("%d passed, %d failed, %d skipped" % (
        len(_results) - len(failed) - len(skipped), len(failed), len(skipped)))
    print("=" * 74)
    if failed:
        print("FAIL")
        for name, _verdict, detail in failed:
            print("  - %s (%s)" % (name, detail))
        return 1
    print("PASS")
    return 0


def _find_or_seed(svc, tenant_id: str, cred, seed: bool,
                  roles: List[str]) -> Optional[str]:
    found = svc._find_user_by_username(cred[0])
    if found:
        return found["id"]
    if not seed:
        print("account %r not found; pass --seed-admin/--seed-member on a scratch "
              "sandbox" % cred[0])
        return None
    admin = svc.list_platform_users()[0]
    created = svc.create_member(
        actor_user_id=admin["id"], tenant_id=tenant_id, operation="create-new",
        username=cred[0], display_name=cred[0], temporary_password=cred[1],
        roles=roles)["user_id"]
    with svc._tx() as con:
        con.execute("UPDATE users SET must_change_password=0 WHERE id=?", (created,))
        con.commit()
    return created


def _cred(raw: str) -> Tuple[str, str]:
    if ":" not in raw:
        raise argparse.ArgumentTypeError("expected username:password")
    user, password = raw.split(":", 1)
    return user, password


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=os.environ.get("COW_DATA_DIR") or "~/.cow",
                        help="deployment data root holding config.json/identity.db")
    parser.add_argument("--base-url", default=None,
                        help="console origin, e.g. http://127.0.0.1:9899 (omit to skip HTTP checks)")
    parser.add_argument("--tenant-code", default="default")
    parser.add_argument("--admin", type=_cred, default=("alice", "Str0ng-Pass!23"),
                        help="username:password of a tenant admin")
    parser.add_argument("--member", type=_cred, default=("bob", "Str0ng-Pass!23"),
                        help="username:password of a plain member")
    parser.add_argument("--member-roles", nargs="*", default=["member"],
                        help="roles for a seeded member account")
    parser.add_argument("--agent-a", default=None, help="override the primary Agent id")
    parser.add_argument("--agent-b", default=None, help="override the comparison Agent id")
    parser.add_argument("--marker", default="PROJECTOR-QUARTZ",
                        help="unique token written to personal memory")
    parser.add_argument("--seed-admin", action="store_true",
                        help="create the admin account if missing (scratch sandboxes only)")
    parser.add_argument("--seed-member", action="store_true",
                        help="create the member account if missing (scratch sandboxes only)")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
