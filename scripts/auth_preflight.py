#!/usr/bin/env python3
# encoding:utf-8
"""Read-only preflight inventory for the role-resource-authorization milestone.

Task 6.1 — "只读预检 + 授权映射".  This tool performs a *read-only* inventory of
every tenant's roles, their resource grants and model defaults, plus the *visible*
resource catalog (menus / skills / tools / models / agents) each tenant may
allocate.  It then produces an authorization-mapping preview that tells an operator
which existing permissions / resources should be migrated into explicit grants
during rollout, versus which are derived from platform-admin eligibility ("all").

Design contract (from docs/design/role-resource-authorization-plan.md §8):

* Already-opened read capabilities may be migrated into explicit grants per a
  concrete resource list; currently-closed execution capabilities must NOT be
  treated as authorized just because they were never enabled.
* Platform admins are derived ``all`` by eligibility (no grants needed).
* Ordinary roles gain no execution rights automatically; the skill-maintenance
  split from ``agent.read`` is an intentional tightening.  Admins configure
  maintenance/edit/enable rights explicitly via new grants.
* This tool NEVER writes (no CREATE/INSERT/UPDATE/DELETE).  It defaults to an
  in-memory read via the running identity service; pass ``--db`` to point at an
  explicit database file.  ``--json`` emits a machine-readable report.

Usage:
    python scripts/auth_preflight.py [--db PATH] [--json] [--csv-out PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional


def _kv(kind: str, rid: str, action: str) -> Dict[str, str]:
    return {"resource_kind": kind, "resource_id": rid, "action": action}


def _load_service(db: Optional[str]):
    """Return (service, path).  Read-only; never opens a write transaction."""
    if db:
        sys.path.insert(0, os.getcwd())
        from auth.service import IdentityService
        return IdentityService(db), db
    sys.path.insert(0, os.getcwd())
    from auth.service import get_identity_service
    svc = get_identity_service()
    return svc, None


def _tenant_roles(svc, tenant_id: str) -> List[Dict[str, Any]]:
    try:
        return svc.list_roles(tenant_id)
    except Exception as e:  # noqa: BLE001 - report and continue for other tenants
        return [{"error": str(e)}]


def _catalog_kinds(svc, tenant_id: str) -> Dict[str, Any]:
    """Read-only projection of the tenant-allocatable catalog per resource kind."""
    kinds = ("menu", "skill", "tool", "model", "agent")
    out: Dict[str, Any] = {}
    for kind in kinds:
        try:
            cat = svc.authorization_catalog(tenant_id, kind=kind, page_size=10000,
                                            all_mode=False)
            items = cat.get("items", [])
            out[kind] = {
                "total": cat.get("total", len(items)),
                "ids": [i["resource_id"] for i in items],
                "actions": list(cat.get("resource_actions", [])),
            }
        except Exception as e:  # noqa: BLE001
            out[kind] = {"error": str(e)}
    return out


def _collect(svc) -> Dict[str, Any]:
    tenants: List[Dict[str, Any]] = []
    try:
        tenant_rows = svc.list_tenants()
    except Exception as e:  # noqa: BLE001
        tenant_rows = []

    for t in tenant_rows:
        tid = t["id"]
        code = t.get("code", "")
        name = t.get("name", "")
        roles = _tenant_roles(svc, tid)
        catalog = _catalog_kinds(svc, tid)
        tenants.append({
            "tenant_id": tid,
            "code": code,
            "name": name,
            "roles": roles,
            "catalog": catalog,
        })

    return {"tenants": tenants}


def _render_mapping_preview(svc, data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build the authorization-mapping preview rows (which perms → actions)."""
    rows: List[Dict[str, str]] = []
    for t in data["tenants"]:
        tid = t["tenant_id"]
        for r in t.get("roles", []):
            if "error" in r:
                continue
            role_id = r.get("id", "")
            role_code = r.get("code", "")
            builtin = bool(r.get("builtin"))
            perms = r.get("permissions", []) or []
            grants = r.get("resource_grants", []) or []
            defaults = r.get("model_defaults", {}) or {}

            # 1) Explicit resource grants already present.
            for g in grants:
                rows.append({
                    "tenant": t["code"], "role": role_code, "kind": "grant",
                    "resource_kind": g.get("resource_kind", ""),
                    "resource_id": g.get("resource_id", ""),
                    "action": g.get("action", ""),
                    "note": "explicit grant",
                })
            # 2) Model defaults reference grants (must be covered by model.use).
            for cap, model_rid in defaults.items():
                rows.append({
                    "tenant": t["code"], "role": role_code, "kind": "model_default",
                    "resource_kind": "model", "resource_id": model_rid,
                    "action": "use", "note": f"default for {cap} (needs model.use grant)",
                })
            # 3) Permission-derived preview: which existing capabilities map to a
            #    resource action if the tenant opts to migrate a concrete resource.
            for p in perms:
                mapped = _permission_to_action(p)
                if mapped:
                    rows.append({
                        "tenant": t["code"], "role": role_code, "kind": "perm_preview",
                        "resource_kind": mapped[0], "resource_id": "*", "action": mapped[1],
                        "note": f"from permission {p} (migrate per concrete list)",
                    })
            # 4) Built-in roles: their effective perms come from tuple defaults.
            if builtin:
                rows.append({
                    "tenant": t["code"], "role": role_code, "kind": "builtin",
                    "resource_kind": "-", "resource_id": "-", "action": "-",
                    "note": "built-in default permission tuple",
                })
    return rows


def _permission_to_action(perm: str) -> Optional[tuple]:
    """Map a legacy permission to a (kind, action) if the milestone added one."""
    mapping = {
        "skill.read": ("skill", "read"),
        "skill.use": ("skill", "use"),
        "skill.edit": ("skill", "edit"),
        "skill.enable": ("skill", "enable"),
        "tool.read": ("tool", "read"),
        "tool.execute": ("tool", "execute"),
        "tool.configure": ("tool", "configure"),
        "model.read": ("model", "read"),
        "model.use": ("model", "use"),
        "agent.use": ("agent", "use"),
        "agent.edit": ("agent", "edit"),
        "agent.enable": ("agent", "enable"),
        "chat.use": ("chat", "use"),
    }
    return mapping.get(perm)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only role-resource authorization preflight.")
    parser.add_argument("--db", help="Explicit identity.db path (default: configured).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")
    parser.add_argument("--csv-out", help="Write the mapping preview to a CSV file.")
    args = parser.parse_args(argv)

    svc, db_path = _load_service(args.db)
    src = db_path or "configured identity.db"
    data = _collect(svc)
    mapping = _render_mapping_preview(svc, data)

    if args.json:
        print(json.dumps({
            "source": src,
            "do_not_write": True,
            "tenants": data["tenants"],
            "mapping_preview": mapping,
        }, ensure_ascii=False, indent=2))
        return 0

    # Human-readable report -------------------------------------------------
    print(f"# Role-Resource Authorization Preflight (read-only)")
    print(f"# Source: {src}")
    print()
    print(f"Tenants: {len(data['tenants'])}")
    for t in data["tenants"]:
        nroles = len(t.get("roles", []))
        print(f"\n## {t['code']} — {t['name']} ({t['tenant_id']})  roles={nroles}")
        catalog = t.get("catalog", {})
        for kind in ("menu", "skill", "tool", "model", "agent"):
            cat = catalog.get(kind, {})
            if "error" in cat:
                print(f"   {kind:<6} ERROR: {cat['error']}")
            else:
                print(f"   {kind:<6} total={cat.get('total', 0)} actions={cat.get('actions', [])}")
        if nroles:
            print(f"   -- roles --")
            for r in t.get("roles", []):
                if "error" in r:
                    print(f"     {r['error']}")
                    continue
                print(f"     {r.get('code','')}  builtin={bool(r.get('builtin'))} "
                      f"perms={len(r.get('permissions', []) or [])} "
                      f"grants={len(r.get('resource_grants', []) or [])} "
                      f"defaults={len(r.get('model_defaults', {}) or {})}")
                for d in (r.get("model_defaults", {}) or {}).items():
                    print(f"         default {d[0]} -> {d[1]}")

    print(f"\n# Authorization-mapping preview ({len(mapping)} rows)")
    for m in mapping:
        print(f"  {m['tenant']:>10} | {m['role']:<16} | {m['kind']:<14} "
              f"| {m['resource_kind']:<6} {m['resource_id']:<28} {m['action']:<10} {m['note']}")

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "tenant", "role", "kind", "resource_kind", "resource_id",
                "action", "note"])
            w.writeheader()
            for m in mapping:
                w.writerow(m)
        print(f"\n# CSV written to {args.csv_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
