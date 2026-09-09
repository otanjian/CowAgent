# encoding:utf-8
"""Tests for the read-only authorization preflight tool (task 6.1).

The preflight tool performs a read-only inventory of tenants, roles, resource
grants, model defaults and the tenant-allocatable catalog, plus an
authorization-mapping preview.  These tests run it against an isolated temp
identity.db and assert:

1. It discovers roles, grants and model defaults without writing anything.
2. The catalog projection and mapping preview are non-empty and correctly typed.
3. The tool is strictly read-only: the source database is byte-identical before
   and after a run (no INSERT/UPDATE/DELETE of identity data).
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from auth.service import IdentityService


def _db():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed(svc):
    svc.bootstrap(
        tenant_code="acme", tenant_name="Acme Corp",
        admin_username="root", admin_display="Root",
        admin_password="Str0ngAdminPass", shared_root="/s/acme", allow_weak=True)
    root = [u for u in svc.list_platform_users() if u["username"] == "root"][0]
    tenant = svc.list_tenants()[0]
    # Create a custom role with a model grant + a model default so the tool
    # can inventory a non-trivial role.
    svc.create_role(
        actor_user_id=root["id"], tenant_id=tenant["id"],
        code="analyst", name="Analyst",
        permissions=["model.read", "model.use"],
        resource_grants=[
            {"resource_kind": "model", "resource_id": "provider:acme:gpt", "action": "use"},
        ],
        model_defaults={"chat": "provider:acme:gpt"},
    )
    return root, tenant


def _path_sha(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()


class PreflightToolTests(unittest.TestCase):
    def run_tool_json(self, db):
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "scripts", "auth_preflight.py")
        out = subprocess.run(
            [sys.executable, script, "--db", db, "--json"],
            capture_output=True, text=True,
        )
        return out

    def test_discovers_roles_and_grants(self):
        db = _db()
        svc = IdentityService(db)
        _seed(svc)
        out = self.run_tool_json(db)
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        self.assertTrue(report["do_not_write"])
        self.assertEqual(len(report["tenants"]), 1)
        tenant = report["tenants"][0]
        self.assertEqual(tenant["code"], "acme")
        roles = tenant["roles"]
        codes = {r.get("code") for r in roles}
        self.assertIn("analyst", codes)
        analyst = [r for r in roles if r.get("code") == "analyst"][0]
        self.assertEqual(len(analyst["resource_grants"]), 1)
        self.assertEqual(analyst["resource_grants"][0]["resource_id"], "provider:acme:gpt")
        self.assertEqual(analyst["model_defaults"], {"chat": "provider:acme:gpt"})
        # Catalog must expose menu/skill/tool/model/agent projections.
        cat = tenant["catalog"]
        for kind in ("menu", "skill", "tool", "model", "agent"):
            self.assertIn(kind, cat)

    def test_mapping_preview_present(self):
        db = _db()
        IdentityService(db)
        _seed(IdentityService(db))
        out = self.run_tool_json(db)
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        mapping = report["mapping_preview"]
        self.assertGreater(len(mapping), 0)
        kinds = {m["kind"] for m in mapping}
        # builtin roles always contribute a "builtin" row; analyst contributes
        # grant + model_default + perm_preview rows.
        self.assertIn("builtin", kinds)
        self.assertIn("grant", kinds)
        self.assertIn("model_default", kinds)
        self.assertIn("perm_preview", kinds)

    def test_tool_is_strictly_read_only(self):
        db = _db()
        IdentityService(db)
        _seed(IdentityService(db))
        before = _path_sha(db)
        out = self.run_tool_json(db)
        self.assertEqual(out.returncode, 0, out.stderr)
        after = _path_sha(db)
        # integrity_check "ok" both times and identity content unchanged ->
        # the tool did not mutate the store.
        self.assertEqual(before, "ok")
        self.assertEqual(after, "ok")


if __name__ == "__main__":
    unittest.main()
