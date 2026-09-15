# encoding:utf-8
"""A member's own tool/skill parameters, and the credentials they reference.

Change ``enable-member-personal-console`` lets a member save parameters for a
tool or skill *for their own use*. The requirement is separative: the member's
configuration must live in its own per-user record, must never rewrite the
public resource (the tool's definition, an MCP connection, a skill's body, its
install/enable state), and any sensitive value must go to a controlled,
owner-scoped credential rather than into the parameter blob or a plaintext file.

These tests lock the storage contract first: the per-user uniqueness key, the
new owner column on ``credentials``, and the fact that an upgrade classifies
existing rows rather than inventing anything.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from auth.crypto import decrypt_secret
from auth.service import IdentityService, IdentityServiceError
from auth.store import IdentityStore, migration_versions

CONFIGS = "personal_resource_configs"


def _db_path():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


def _seed_prereqs(con):
    con.execute(
        "INSERT INTO tenants(id, code, name, shared_root) VALUES('t1','acme','Acme','/s/acme')")
    con.execute(
        "INSERT INTO users(id, username, display_name, password_hash)"
        " VALUES('u1','alice','Alice','x')")


def _insert_config(con, **over):
    row = {
        "tenant_id": "t1",
        "user_id": "u1",
        "resource_kind": "tool",
        "resource_id": "builtin:echo",
        "params_json": "{}",
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    con.execute(
        "INSERT INTO %s(%s) VALUES(%s)" % (CONFIGS, cols, marks),
        tuple(row.values()),
    )


class _SchemaBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "identity.db")
        self.store = IdentityStore(self.path)

    def _columns(self, table):
        with self.store.connect() as con:
            return {
                r["name"]: {"notnull": r["notnull"], "default": r["dflt_value"]}
                for r in con.execute("PRAGMA table_info(%s)" % table)
            }


class CredentialOwnerColumnTests(_SchemaBase):
    """A personal credential has to name its owner.

    Without an owner column the only way to say "this one is a member's" was to
    encode it in the name, which is both fragile and leaks the owner into every
    read projection. The column keeps a credential's ownership a fact of the row.
    """

    def test_owner_user_id_is_nullable_without_a_default(self):
        cols = self._columns("credentials")
        self.assertIn("owner_user_id", cols)
        self.assertEqual(cols["owner_user_id"]["notnull"], 0,
                         "a tenant credential has no personal owner")
        self.assertIsNone(cols["owner_user_id"]["default"])

    def test_an_existing_tenant_credential_upgrades_without_an_owner(self):
        """The compatibility red line: public credentials must not gain an owner,
        or they would vanish from the tenant's own credential list."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            con.execute(
                "INSERT INTO credentials(id, tenant_id, name, resource_kind,"
                " resource_id, ciphertext, created_by)"
                " VALUES('cred1','t1','sap-prod','sap','','cipher-old','u1')")
            row = con.execute(
                "SELECT owner_user_id, ciphertext, version FROM credentials"
                " WHERE id='cred1'").fetchone()
        self.assertIsNone(row["owner_user_id"])
        self.assertEqual(row["ciphertext"], "cipher-old")
        self.assertEqual(row["version"], 1)


class PersonalResourceConfigSchemaTests(_SchemaBase):
    """One row per (tenant, user, resource): the member's own parameters."""

    def test_the_migration_is_registered(self):
        with self.store.connect() as con:
            applied = [r[0] for r in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(applied, migration_versions())

    def test_columns_are_the_expected_set(self):
        self.assertEqual(
            set(self._columns(CONFIGS)),
            {"tenant_id", "user_id", "resource_kind", "resource_id",
             "params_json", "credential_id", "version", "created_at",
             "updated_at"},
        )

    def test_identity_and_version_columns_are_not_null(self):
        cols = self._columns(CONFIGS)
        for name in ("tenant_id", "user_id", "resource_kind", "resource_id",
                     "params_json", "version"):
            self.assertEqual(cols[name]["notnull"], 1, "%s must be NOT NULL" % name)

    def test_params_default_to_an_empty_object_and_version_to_one(self):
        cols = self._columns(CONFIGS)
        self.assertEqual(cols["params_json"]["default"], "'{}'")
        self.assertEqual(cols["version"]["default"], "1")
        self.assertIsNone(cols["credential_id"]["default"])
        self.assertEqual(cols["credential_id"]["notnull"], 0)

    def test_a_configured_resource_is_unique_per_user(self):
        """The key is (tenant, user, kind, resource): a repeat must conflict
        rather than let one member accumulate duplicate parameter sets."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_config(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_config(con)

    def test_two_members_may_configure_the_same_resource(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            con.execute(
                "INSERT INTO users(id, username, display_name, password_hash)"
                " VALUES('u2','bob','Bob','x')")
            _insert_config(con)
            _insert_config(con, user_id="u2")
            n = con.execute("SELECT COUNT(*) FROM %s" % CONFIGS).fetchone()[0]
        self.assertEqual(n, 2)

    def test_one_member_may_configure_two_resources(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_config(con)
            _insert_config(con, resource_id="builtin:search")
            n = con.execute("SELECT COUNT(*) FROM %s" % CONFIGS).fetchone()[0]
        self.assertEqual(n, 2)

    def test_the_key_spans_kinds_not_just_resource_ids(self):
        """A tool and a skill may share an id-shaped name; they are distinct."""
        with self.store.connect() as con:
            _seed_prereqs(con)
            _insert_config(con, resource_kind="tool", resource_id="builtin:x")
            _insert_config(con, resource_kind="skill", resource_id="builtin:x")
            n = con.execute("SELECT COUNT(*) FROM %s" % CONFIGS).fetchone()[0]
        self.assertEqual(n, 2)

    def test_an_unknown_tenant_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_config(con, tenant_id="nope")

    def test_an_unknown_user_is_rejected_by_foreign_key(self):
        with self.store.connect() as con:
            _seed_prereqs(con)
            with self.assertRaises(sqlite3.IntegrityError):
                _insert_config(con, user_id="nope")


class PersonalResourceConfigUpgradeTests(unittest.TestCase):
    """Upgrading adds empty storage and leaves existing credentials alone."""

    def _pre_migration_store(self):
        from auth.store import _migrations
        path = _db_path()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE schema_migrations("
            " version INTEGER NOT NULL,"
            " applied_at INTEGER NOT NULL DEFAULT (unixepoch()))")
        for i in range(18):
            _migrations[i](con)
        _seed_prereqs(con)
        con.execute(
            "INSERT INTO credentials(id, tenant_id, name, resource_kind,"
            " resource_id, ciphertext, created_by)"
            " VALUES('cred1','t1','sap-prod','sap','','cipher-old','u1')")
        con.execute(
            "INSERT INTO credential_versions(credential_id, version, ciphertext,"
            " action, changed_by) VALUES('cred1',1,'cipher-old','create','u1')")
        con.execute("INSERT INTO schema_migrations(version) VALUES %s"
                    % ",".join("(%d)" % (i + 1) for i in range(18)))
        con.commit()
        con.close()
        return path

    def test_upgrade_preserves_credentials_and_adds_no_configs(self):
        path = self._pre_migration_store()
        store = IdentityStore(path)
        with store.connect() as con:
            cred = con.execute(
                "SELECT id, owner_user_id, ciphertext, version FROM credentials"
                " WHERE id='cred1'").fetchone()
            configs = con.execute("SELECT COUNT(*) FROM %s" % CONFIGS).fetchone()[0]
        self.assertEqual(cred["id"], "cred1")
        self.assertIsNone(cred["owner_user_id"])
        self.assertEqual(cred["ciphertext"], "cipher-old")
        self.assertEqual(cred["version"], 1)
        self.assertEqual(configs, 0, "the upgrade must not invent configurations")


class _PersonalConfigFixture(unittest.TestCase):
    """Acme with a member granted one tool and one skill, and a tenant admin."""

    MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    TOOL = "builtin:echo"
    SKILL = "custom:writer"
    SECRET = "s3cr3t-personal-token"

    def setUp(self):
        self._prev_key = os.environ.get("COW_CREDENTIAL_MASTER_KEY")
        os.environ["COW_CREDENTIAL_MASTER_KEY"] = self.MASTER_KEY
        self.addCleanup(self._restore_key)

        self.svc = IdentityService(_db_path())
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root="/s/acme")
        self.svc.change_password(
            self.svc.login("root", "Str0ngAdminPass").token,
            "Str0ngAdminPass", "Str0ngRootFinal")
        self.root = [u for u in self.svc.list_platform_users()
                     if u["username"] == "root"][0]
        self.tenant = self.svc.list_tenants()[0]["id"]

        self.role = self.svc.create_role(
            self.root["id"], self.tenant, "personalizer", "Personalizer", [],
            resource_grants=[
                {"resource_kind": "tool", "resource_id": self.TOOL,
                 "action": "execute"},
                {"resource_kind": "skill", "resource_id": self.SKILL,
                 "action": "use"},
            ])
        self.alice = self._add_member("alice", "Alice", ["personalizer"])
        self.bob = self._add_member("bob", "Bob", ["personalizer"])

    def _restore_key(self):
        if self._prev_key is None:
            os.environ.pop("COW_CREDENTIAL_MASTER_KEY", None)
        else:
            os.environ["COW_CREDENTIAL_MASTER_KEY"] = self._prev_key

    def _add_member(self, username, display, roles):
        with patch("agent.personal_assistant.get_personal_assistant_provisioner",
                   return_value=type("P", (), {"provision": lambda *a, **k: None})()):
            self.svc.create_member(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                operation="create-new", username=username, display_name=display,
                temporary_password="MemTempPass1", roles=roles)
        return [m for m in self.svc.list_members(self.tenant)["items"]
                if m["username"] == username][0]["user_id"]

    def _save(self, user_id=None, **over):
        args = dict(
            actor_user_id=user_id or self.alice, tenant_id=self.tenant,
            resource_kind="tool", resource_id=self.TOOL,
            params={"timeout": 30, "verbose": True})
        args.update(over)
        return self.svc.save_personal_resource_config(**args)

    def _get(self, user_id=None, **over):
        args = dict(actor_user_id=user_id or self.alice, tenant_id=self.tenant,
                    resource_kind="tool", resource_id=self.TOOL)
        args.update(over)
        return self.svc.get_personal_resource_config(**args)

    def _config_row(self, user_id=None, resource_id=None, kind="tool"):
        rows = self.svc._store.execute(
            "SELECT * FROM personal_resource_configs WHERE tenant_id=? AND"
            " user_id=? AND resource_kind=? AND resource_id=?",
            (self.tenant, user_id or self.alice, kind, resource_id or self.TOOL))
        return dict(rows[0]) if rows else None

    def _personal_credentials(self):
        return [dict(r) for r in self.svc._store.execute(
            "SELECT * FROM credentials WHERE owner_user_id IS NOT NULL")]

    def _public_grants(self):
        return (
            self.svc._store.execute(
                "SELECT COUNT(*) c FROM tenant_resource_grants")[0]["c"],
            self.svc._store.execute(
                "SELECT COUNT(*) c FROM role_resource_grants")[0]["c"],
        )


class SavePersonalResourceConfigTests(_PersonalConfigFixture):

    def test_a_member_saves_parameters_for_a_granted_tool(self):
        saved = self._save()
        self.assertEqual(saved["resource_kind"], "tool")
        self.assertEqual(saved["resource_id"], self.TOOL)
        self.assertEqual(saved["params"], {"timeout": 30, "verbose": True})
        row = self._config_row()
        self.assertEqual(json.loads(row["params_json"]),
                         {"timeout": 30, "verbose": True})
        self.assertEqual(row["version"], 1)

    def test_the_configuration_is_per_user_not_per_tenant(self):
        """Bob's save must be invisible to Alice — same resource, different row."""
        self._save()
        self._save(user_id=self.bob, params={"timeout": 5})

        self.assertEqual(self._get()["params"], {"timeout": 30, "verbose": True})
        self.assertEqual(self._get(user_id=self.bob)["params"], {"timeout": 5})

    def test_saving_twice_updates_in_place_and_bumps_the_version(self):
        self._save()
        again = self._save(params={"timeout": 60})

        self.assertEqual(again["version"], 2)
        self.assertEqual(again["params"], {"timeout": 60})
        rows = self.svc._store.execute(
            "SELECT COUNT(*) c FROM personal_resource_configs WHERE tenant_id=?"
            " AND user_id=?", (self.tenant, self.alice))
        self.assertEqual(rows[0]["c"], 1, "a member must not accumulate duplicates")

    def test_an_ungranted_resource_is_refused_and_leaves_no_row(self):
        """Saving personal parameters must not become a way to acquire a
        resource the member was never granted."""
        with self.assertRaises(IdentityServiceError) as caught:
            self._save(resource_id="builtin:rm")
        self.assertEqual(caught.exception.code, "forbidden")
        self.assertIsNone(self._config_row(resource_id="builtin:rm"))

    def test_an_unknown_resource_kind_is_refused(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self._save(resource_kind="model")
        self.assertEqual(caught.exception.code, "bad_request")

    def test_parameters_must_be_a_json_object(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self._save(params=["not", "a", "dict"])
        self.assertEqual(caught.exception.code, "bad_request")

    def test_saving_does_not_touch_public_authorization(self):
        """The separation the requirement is about: a personal save must not add
        a tenant or role grant, and must not widen anyone's access."""
        before = self._public_grants()

        self._save()

        self.assertEqual(self._public_grants(), before)
        self.assertFalse(self.svc.check_resource_action(
            self.alice, self.tenant, "tool", "builtin:rm", "execute"))

    def test_a_tenant_admin_cannot_write_another_members_config(self):
        """Personal configuration is the member's own: an admin acting 'as'
        Alice must only ever write the admin's own row."""
        self.svc.save_personal_resource_config(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            resource_kind="skill", resource_id=self.SKILL,
            params={"tone": "brief"})

        self.assertIsNone(self._config_row(kind="skill", resource_id=self.SKILL))
        self.assertIsNotNone(self._config_row(
            user_id=self.root["id"], kind="skill", resource_id=self.SKILL))

    def test_a_resource_granted_only_as_a_skill_is_not_configurable_as_a_tool(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self._save(resource_kind="tool", resource_id=self.SKILL)
        self.assertEqual(caught.exception.code, "forbidden")


class GetPersonalResourceConfigTests(_PersonalConfigFixture):

    def test_an_absent_configuration_reads_as_none(self):
        self.assertIsNone(self._get())

    def test_another_member_never_sees_this_configuration(self):
        self._save()
        self.assertIsNone(self._get(user_id=self.bob))


class PersonalSecretTests(_PersonalConfigFixture):
    """Sensitive values go to a controlled, owner-scoped credential."""

    def test_a_saved_secret_is_encrypted_and_owner_scoped(self):
        saved = self._save(secret=self.SECRET)

        creds = self._personal_credentials()
        self.assertEqual(len(creds), 1)
        self.assertEqual(creds[0]["owner_user_id"], self.alice)
        self.assertNotIn(self.SECRET, creds[0]["ciphertext"])
        self.assertEqual(decrypt_secret(creds[0]["ciphertext"]), self.SECRET)
        self.assertEqual(saved["credential_id"], creds[0]["id"])

    def test_the_secret_never_lands_in_the_parameter_blob(self):
        self._save(params={"token_name": "prod"}, secret=self.SECRET)

        row = self._config_row()
        self.assertNotIn(self.SECRET, row["params_json"])
        self.assertNotIn(self.SECRET, json.dumps(self._get()))

    def test_re_saving_rotates_the_same_credential(self):
        first = self._save(secret=self.SECRET)
        second = self._save(secret="rotated-token")

        self.assertEqual(second["credential_id"], first["credential_id"])
        self.assertEqual(len(self._personal_credentials()), 1)
        cred = self._personal_credentials()[0]
        self.assertEqual(decrypt_secret(cred["ciphertext"]), "rotated-token")
        self.assertEqual(cred["version"], 2)

    def test_a_secret_is_not_stored_when_none_is_given(self):
        self._save()

        self.assertIsNone(self._config_row()["credential_id"])
        self.assertEqual(self._personal_credentials(), [])


class ResolvePersonalResourceConfigTests(_PersonalConfigFixture):
    """Use re-checks authorization: saved parameters grant nothing on their own."""

    def test_a_granted_member_resolves_parameters_and_secret(self):
        self._save(params={"timeout": 7}, secret=self.SECRET)

        resolved = self.svc.resolve_personal_resource_config(
            actor_user_id=self.alice, tenant_id=self.tenant,
            resource_kind="tool", resource_id=self.TOOL)

        self.assertEqual(resolved["params"], {"timeout": 7})
        self.assertEqual(resolved["secret"], self.SECRET)

    def test_revoking_the_grant_makes_the_saved_configuration_inert(self):
        """The saved parameters and credential must not outlive the grant."""
        self._save(secret=self.SECRET)
        # The member loses the tool: the role grant is removed.
        role = [r for r in self.svc.list_roles(self.tenant)
                if r["code"] == "personalizer"][0]
        self.svc.update_role(
            self.root["id"], self.tenant, role["id"], role["name"], [],
            expected_version=role["version"],
            resource_grants=[
                {"resource_kind": "skill", "resource_id": self.SKILL,
                 "action": "use"}])

        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.resolve_personal_resource_config(
                actor_user_id=self.alice, tenant_id=self.tenant,
                resource_kind="tool", resource_id=self.TOOL)
        self.assertEqual(caught.exception.code, "forbidden")

    def test_resolving_without_saved_parameters_is_not_found(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.resolve_personal_resource_config(
                actor_user_id=self.alice, tenant_id=self.tenant,
                resource_kind="tool", resource_id=self.TOOL)
        self.assertEqual(caught.exception.code, "not_found")


class CredentialOwnershipTests(_PersonalConfigFixture):
    """A personal credential is private to its owner, even from administrators."""

    def test_a_tenant_admin_does_not_see_a_members_personal_credential(self):
        self._save(secret=self.SECRET)
        self.svc.create_credential(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            name="sap-prod", secret="tenant-wide-secret")

        listing = self.svc.list_credentials(
            actor_user_id=self.root["id"], tenant_id=self.tenant)

        names = [item["name"] for item in listing["items"]]
        self.assertIn("sap-prod", names, "the tenant's own credential must remain")
        self.assertEqual(
            [n for n in names if n.startswith("personal:")], [],
            "an admin must not see a member's personal credential")
        self.assertEqual(listing["total"], 1)

    def test_an_admin_cannot_resolve_a_members_personal_credential(self):
        saved = self._save(secret=self.SECRET)
        cred = self._personal_credentials()[0]
        self.assertEqual(saved["credential_id"], cred["id"])

        with self.assertRaises(IdentityServiceError) as caught:
            self.svc.resolve_credential(
                actor_user_id=self.root["id"], tenant_id=self.tenant,
                name=cred["name"])
        self.assertEqual(caught.exception.code, "forbidden")

    def test_the_owner_can_resolve_their_own_personal_credential(self):
        self._save(secret=self.SECRET)
        cred = self._personal_credentials()[0]

        plaintext = self.svc.resolve_credential(
            actor_user_id=self.alice, tenant_id=self.tenant, name=cred["name"])

        self.assertEqual(plaintext, self.SECRET)

    def test_a_public_credential_still_resolves_for_a_controller(self):
        """Owner enforcement must not break the existing public path."""
        self.svc.create_credential(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            name="sap-prod", secret="tenant-wide-secret")

        plaintext = self.svc.resolve_credential(
            actor_user_id=self.root["id"], tenant_id=self.tenant,
            name="sap-prod")

        self.assertEqual(plaintext, "tenant-wide-secret")


class ListPersonalResourceConfigsTests(_PersonalConfigFixture):
    """Task 8.3: the page lists the intersection of grants and saved configs.

    The filtering is on the *server*, at the grant level, so the console can
    never be the place where a member discovers a resource they were not given.
    """

    def _list(self, user_id=None, kind=""):
        return self.svc.list_personal_resource_configs(
            actor_user_id=user_id or self.alice, tenant_id=self.tenant,
            resource_kind=kind)

    def test_the_list_holds_only_granted_resources(self):
        entries = self._list()
        self.assertEqual({(e["resource_kind"], e["resource_id"]) for e in entries},
                         {("tool", self.TOOL), ("skill", self.SKILL)})

    def test_an_unsaved_grant_appears_as_unconfigured_and_unclearable(self):
        tool = [e for e in self._list() if e["resource_kind"] == "tool"][0]
        self.assertFalse(tool["configured"])
        self.assertEqual(tool["params"], {})
        self.assertEqual(tool["actions"], {"configure": True, "clear": False})

    def test_a_saved_grant_reports_its_state_and_becomes_clearable(self):
        self._save(secret=self.SECRET)
        tool = [e for e in self._list() if e["resource_kind"] == "tool"][0]
        self.assertTrue(tool["configured"])
        self.assertEqual(tool["params"], {"timeout": 30, "verbose": True})
        self.assertTrue(tool["has_credential"])
        self.assertEqual(tool["version"], 1)
        self.assertEqual(tool["actions"], {"configure": True, "clear": True})

    def _revoke_grants(self):
        """Revoke the fixture role's resource grants (the role itself stays)."""
        self.svc._store.execute(
            "DELETE FROM role_resource_grants WHERE tenant_id=? AND role_id=?",
            (self.tenant, self.role["id"]))

    def test_a_revoked_grant_leaves_the_list(self):
        self._save()
        self._revoke_grants()
        self.assertEqual(self._list(), [])

    def test_another_members_configuration_is_never_listed(self):
        self._save(user_id=self.bob, params={"timeout": 5})
        self.assertEqual([e for e in self._list() if e["configured"]], [])

    def test_a_kind_filter_narrows_the_list(self):
        self.assertEqual([e["resource_kind"] for e in self._list(kind="tool")],
                         ["tool"])
        self.assertEqual([e["resource_kind"] for e in self._list(kind="skill")],
                         ["skill"])

    def test_a_disabled_member_lists_nothing(self):
        self.svc._store.execute(
            "UPDATE memberships SET active=0 WHERE user_id=? AND tenant_id=?",
            (self.alice, self.tenant))
        self.assertEqual(self._list(), [])


class ClearPersonalResourceConfigTests(_PersonalConfigFixture):
    """Task 8.3: clearing removes the member's own row and revokes the secret."""

    def _clear(self, user_id=None, **over):
        args = dict(actor_user_id=user_id or self.alice, tenant_id=self.tenant,
                    resource_kind="tool", resource_id=self.TOOL)
        args.update(over)
        return self.svc.clear_personal_resource_config(**args)

    def test_clearing_removes_the_row_and_the_secret(self):
        self._save(secret=self.SECRET)

        self.assertTrue(self._clear())

        self.assertIsNone(self._config_row())
        self.assertIsNone(self._get())
        live = [c for c in self._personal_credentials() if c["active"]]
        self.assertEqual(live, [], "a cleared configuration keeps no live secret")

    def test_the_revoked_credential_keeps_its_version_history(self):
        self._save(secret=self.SECRET)
        cred = self._personal_credentials()[0]

        self._clear()

        row = self.svc._store.execute(
            "SELECT * FROM credentials WHERE id=?", (cred["id"],))[0]
        self.assertEqual(row["active"], 0)
        versions = self.svc._store.execute(
            "SELECT action FROM credential_versions WHERE credential_id=?"
            " ORDER BY version", (cred["id"],))
        self.assertIn("revoked", [v["action"] for v in versions])

    def test_clearing_is_allowed_after_the_grant_is_revoked(self):
        """Clean-up must not depend on the grant that allowed the configuration."""
        self._save(secret=self.SECRET)
        self.svc._store.execute(
            "DELETE FROM role_resource_grants WHERE tenant_id=? AND role_id=?",
            (self.tenant, self.role["id"]))

        self.assertTrue(self._clear())

        self.assertIsNone(self._config_row())

    def test_clearing_without_a_configuration_is_not_an_error(self):
        self.assertFalse(self._clear())

    def test_one_member_cannot_clear_anothers_configuration(self):
        self._save()

        self.assertFalse(self._clear(user_id=self.bob))

        self.assertIsNotNone(self._config_row())
        self.assertIsNotNone(self._get())

    def test_clearing_does_not_touch_public_authorization(self):
        self._save()
        before = self._public_grants()

        self._clear()

        self.assertEqual(self._public_grants(), before)

    def test_a_kind_without_personal_configuration_is_refused(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self._clear(resource_kind="knowledge")
        self.assertEqual(caught.exception.code, "bad_request")

    def test_clearing_requires_a_resource_id(self):
        with self.assertRaises(IdentityServiceError) as caught:
            self._clear(resource_id="")
        self.assertEqual(caught.exception.code, "bad_request")


if __name__ == "__main__":
    unittest.main()
