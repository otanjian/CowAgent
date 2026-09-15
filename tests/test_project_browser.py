# encoding:utf-8
"""Scoped project browser (change ``complete-database-capability-parity`` 6.1-6.5).

``GET /api/projects/browse`` used to walk the whole host filesystem, which is
why database identity mode closed the picker entirely: a member could create a
project inside their own root but never re-open one. These tests pin the scoped
replacement -- identifiers are relative, the root comes from the identity, every
component is validated through ``common.safe_fs``, selection re-validates at
selection time, and an import publishes atomically or not at all.

Roots and identities are passed explicitly (the module supports that seam)
except where the test is specifically about identity resolution, which uses
``use_identity`` plus a stub shared root so no ambient machinery is required.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.runtime_identity import RuntimeIdentity, use_identity
from agent.workspace import project_browser
from agent.workspace.project_browser import ImportCancelled, ProjectBrowserError


class BrowserCase(unittest.TestCase):
    """A tenant shared root holding two members' private trees."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self._tmp.name)
        self.shared = os.path.join(self.base, "shared")
        self.users = os.path.join(self.shared, "users")
        self.alice = RuntimeIdentity(tenant_id="t1", user_id="u_alice")
        self.bob = RuntimeIdentity(tenant_id="t1", user_id="u_bob")
        self.alice_user = os.path.join(self.users, "u_alice")
        self.bob_user = os.path.join(self.users, "u_bob")
        self.alice_root = os.path.join(self.alice_user, "projects")
        self.bob_root = os.path.join(self.bob_user, "projects")
        os.makedirs(self.alice_root)
        os.makedirs(self.bob_root)

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------------

    def mkdir(self, root, rel):
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(path, exist_ok=True)
        return path

    def write(self, root, rel, text="body"):
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def symlink(self, target, link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(target, link)
        return link

    def names(self, result):
        return [entry["name"] for entry in result["dirs"]]

    def refuse(self, code, fn, *args, **kwargs):
        with self.assertRaises(ProjectBrowserError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code,
                         f"expected {code}, got {caught.exception.code}: "
                         f"{caught.exception}")
        return caught.exception

    def staging_leftovers(self, root):
        if not os.path.isdir(root):
            return []
        return [name for name in os.listdir(root)
                if name.startswith(project_browser._STAGING_PREFIX)]

    def patched_shared_root(self):
        from common import state_dir
        return mock.patch.object(state_dir, "shared_root",
                                 lambda identity=None: Path(self.shared))


# --- browsing ----------------------------------------------------------------


class BrowseTests(BrowserCase):
    def test_root_lists_only_directories_sorted_and_skips_hidden(self):
        self.mkdir(self.alice_root, "beta")
        self.mkdir(self.alice_root, "Alpha")
        self.mkdir(self.alice_root, "gamma/inner")
        self.mkdir(self.alice_root, ".hidden")
        self.write(self.alice_root, "notes.txt")
        outside = self.mkdir(self.base, "outside")
        self.symlink(outside, os.path.join(self.alice_root, "linkdir"))

        result = project_browser.browse(self.alice, "", root=self.alice_root)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["path"], "")
        self.assertIsNone(result["parent"])
        # Case-insensitive sort, hidden entries and files and links excluded.
        self.assertEqual(self.names(result), ["Alpha", "beta", "gamma"])
        self.assertEqual(result["dirs"][0], {"name": "Alpha", "path": "Alpha"})
        self.assertEqual(result["dirs"][2], {"name": "gamma", "path": "gamma"})

    def test_nested_browse_returns_relative_ids_and_parent(self):
        self.mkdir(self.alice_root, "beta/inner/deep")

        result = project_browser.browse(self.alice, "beta", root=self.alice_root)

        self.assertEqual(result["path"], "beta")
        self.assertEqual(result["parent"], "")
        self.assertEqual(result["dirs"],
                         [{"name": "inner", "path": "beta/inner"}])

        deeper = project_browser.browse(self.alice, "beta/inner",
                                        root=self.alice_root)
        self.assertEqual(deeper["path"], "beta/inner")
        self.assertEqual(deeper["parent"], "beta")
        self.assertEqual(deeper["dirs"],
                         [{"name": "deep", "path": "beta/inner/deep"}])

    def test_absent_blank_or_dot_relative_means_the_root(self):
        self.mkdir(self.alice_root, "alpha")
        self.assertEqual(project_browser.browse(self.alice, root=self.alice_root),
                         project_browser.browse(self.alice, "", root=self.alice_root))
        self.assertEqual(project_browser.browse(self.alice, None, root=self.alice_root),
                         project_browser.browse(self.alice, ".", root=self.alice_root))

    def test_missing_root_is_empty_not_an_error(self):
        absent = os.path.join(self.base, "no-such-root")
        result = project_browser.browse(self.alice, "", root=absent)
        self.assertEqual(result["dirs"], [])

    def test_missing_directory_is_reported_not_silently_rooted(self):
        self.mkdir(self.alice_root, "alpha")
        self.refuse(project_browser.CODE_NOT_FOUND, project_browser.browse,
                    self.alice, "nope", root=self.alice_root)

    def test_file_identifier_is_refused_rather_than_listed(self):
        self.write(self.alice_root, "notes.txt")
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "notes.txt", root=self.alice_root)

    def test_escape_attempts_are_refused(self):
        self.mkdir(self.alice_root, "a")
        for bad in ["..", "../x", "a/../../b", "/etc", "a//b", "a/./b", "./a",
                    "C:\\Windows", "C:/Windows", "a\\b"]:
            with self.subTest(path=bad):
                self.refuse(project_browser.CODE_UNSAFE_PATH,
                            project_browser.browse, self.alice, bad,
                            root=self.alice_root)

    def test_home_shortcut_is_not_a_special_name(self):
        # The legacy picker sent ``~`` for "home"; the scoped browser has no
        # home, so it is just an unknown (and therefore absent) name.
        self.refuse(project_browser.CODE_NOT_FOUND, project_browser.browse,
                    self.alice, "~", root=self.alice_root)
        self.refuse(project_browser.CODE_NOT_FOUND, project_browser.browse,
                    self.alice, "__DRIVES__", root=self.alice_root)

    def test_non_string_identifier_is_refused(self):
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, 7, root=self.alice_root)


class SymlinkTests(BrowserCase):
    def test_symlinked_entry_is_hidden_and_refused_when_navigated_into(self):
        outside = self.mkdir(self.base, "outside/secret")
        self.mkdir(self.alice_root, "alpha")
        self.symlink(outside, os.path.join(self.alice_root, "linkdir"))

        result = project_browser.browse(self.alice, "", root=self.alice_root)

        self.assertEqual(self.names(result), ["alpha"])
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "linkdir", root=self.alice_root)

    def test_symlinked_intermediate_directory_is_refused(self):
        outside = self.mkdir(self.base, "outside/secret")
        self.symlink(outside, os.path.join(self.alice_root, "linkdir"))

        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "linkdir/secret", root=self.alice_root)

    def test_symlinked_root_is_refused(self):
        self.mkdir(self.alice_root, "alpha")
        link = os.path.join(self.base, "rootlink")
        self.symlink(self.alice_root, link)

        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "", root=link)
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.trusted_root, self.alice, root=link)

    def test_directory_swapped_for_a_link_after_browsing_is_refused(self):
        """Check-then-use: the browse result is not an authorization."""
        outside = self.mkdir(self.base, "outside")
        self.write(self.alice_root, "swap/notes.md", "mine")

        first = project_browser.browse(self.alice, "swap", root=self.alice_root)
        self.assertEqual(first["path"], "swap")

        os.rename(os.path.join(self.alice_root, "swap"),
                  os.path.join(self.alice_root, "swap-away"))
        os.symlink(outside, os.path.join(self.alice_root, "swap"))

        # Selecting the browsed identifier is refused, and the session is never
        # bound to the new target.
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.resolve_selection, self.alice, "swap",
                    root=self.alice_root)
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.normalize_selection, self.alice, "swap",
                    root=self.alice_root)
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.browse, self.alice, "swap",
                    root=self.alice_root)
        # A legacy absolute value that now resolves through the link lands
        # outside the root, so it is refused as outside rather than followed.
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.normalize_selection, self.alice,
                    os.path.join(self.alice_root, "swap"), root=self.alice_root)


# --- identity / root resolution ---------------------------------------------


class RootResolutionTests(BrowserCase):
    def test_missing_user_id_is_reported_as_unsupported(self):
        for ident in (RuntimeIdentity(tenant_id="t1"), RuntimeIdentity()):
            with self.subTest(ident=ident):
                self.refuse(project_browser.CODE_NO_IDENTITY,
                            project_browser.browse, ident,
                            root=self.alice_root)
                self.refuse(project_browser.CODE_NO_IDENTITY,
                            project_browser.normalize_selection, ident, "")

    def test_ambient_identity_resolves_the_members_own_root(self):
        from agent.workspace import project_store
        with self.patched_shared_root():
            with use_identity(self.alice):
                self.assertEqual(project_browser.trusted_root(), self.alice_root)
                # Composition, not a second layout: the store's resolver and this
                # module agree on where a member's projects live.
                self.assertEqual(project_browser.trusted_root(),
                                 project_store.user_projects_root())
            with use_identity(self.bob):
                self.assertEqual(project_browser.trusted_root(), self.bob_root)

    def test_two_members_cannot_see_or_select_each_others_trees(self):
        alice_project = self.mkdir(self.alice_root, "alpha")
        self.mkdir(self.bob_root, "beta")

        with self.patched_shared_root():
            with use_identity(self.alice):
                self.assertEqual(self.names(project_browser.browse()), ["alpha"])
            with use_identity(self.bob):
                result = project_browser.browse()
                self.assertEqual(self.names(result), ["beta"])
                # B knows A's identity value; it must not resolve in B's scope.
                self.refuse(project_browser.CODE_NOT_FOUND,
                            project_browser.resolve_selection, None, "alpha")
                self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                            project_browser.resolve_selection, None,
                            alice_project)

    def test_explicit_root_argument_is_an_internal_seam_not_a_request_value(self):
        # The seam still refuses a root it can prove belongs to another member.
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.browse, self.alice, "", root=self.bob_user)
        self.refuse(project_browser.CODE_INVALID_REQUEST,
                    project_browser.browse, self.alice, "", root="relative/root")


# --- platform-root nesting ---------------------------------------------------


class NestedRootTests(BrowserCase):
    """``shared/users/<id>`` means an outer root contains private trees."""

    def setUp(self):
        super().setUp()
        self.mkdir(self.alice_root, "alpha")
        self.mkdir(self.bob_root, "secret")

    def test_outer_root_cannot_descend_into_another_members_tree(self):
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "users/u_bob", root=self.shared)
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "users/u_bob/projects", root=self.shared)
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.resolve_selection, self.alice,
                    "users/u_bob/projects/secret", root=self.shared)

    def test_outer_root_listing_omits_other_members_trees(self):
        # The ``users/`` container holds only private trees, so it is refused
        # and omitted: enumerating it would disclose whose trees exist.
        result = project_browser.browse(self.alice, "", root=self.shared)
        self.assertEqual(self.names(result), [])
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "users", root=self.shared)

    def test_a_members_own_tree_is_still_reachable_through_the_outer_root(self):
        result = project_browser.browse(self.alice, "users/u_alice/projects",
                                        root=self.shared)
        self.assertEqual(self.names(result), ["alpha"])

    def test_no_false_positive_for_a_project_folder_named_users(self):
        # ``users`` is only the layout directory when it really holds the
        # member's own tree; an ordinary folder of that name stays browsable.
        self.write(self.alice_root, "beta/users/notes.md")
        result = project_browser.browse(self.alice, "beta", root=self.alice_root)
        self.assertEqual(self.names(result), ["users"])
        inner = project_browser.browse(self.alice, "beta/users",
                                       root=self.alice_root)
        self.assertEqual(inner["path"], "beta/users")

    def test_root_inside_another_members_tree_is_refused(self):
        self.refuse(project_browser.CODE_UNSAFE_PATH,
                    project_browser.trusted_root, self.alice, root=self.bob_user)
        self.refuse(project_browser.CODE_UNSAFE_PATH, project_browser.browse,
                    self.alice, "", root=self.bob_root)


# --- selection ---------------------------------------------------------------


class SelectionTests(BrowserCase):
    def setUp(self):
        super().setUp()
        self.project = self.mkdir(self.alice_root, "alpha/inner")

    def test_relative_identifier_normalizes_by_trimming_only(self):
        self.assertEqual(
            project_browser.normalize_selection(self.alice, "alpha/inner",
                                                root=self.alice_root),
            "alpha/inner")
        self.assertEqual(
            project_browser.normalize_selection(self.alice, " alpha ",
                                                root=self.alice_root),
            "alpha")

    def test_root_identifier_normalizes_to_the_root_path(self):
        self.assertEqual(
            project_browser.normalize_selection(self.alice, "", root=self.alice_root),
            "")
        self.assertEqual(
            project_browser.resolve_selection(self.alice, "", root=self.alice_root),
            self.alice_root)

    def test_legacy_absolute_path_inside_the_root_normalizes(self):
        self.assertEqual(
            project_browser.normalize_selection(self.alice, self.project,
                                                root=self.alice_root),
            "alpha/inner")
        self.assertEqual(
            project_browser.resolve_selection(self.alice, self.project,
                                              root=self.alice_root),
            self.project)

    def test_legacy_absolute_path_outside_the_root_is_refused(self):
        outside = self.mkdir(self.base, "outside")
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.normalize_selection, self.alice, outside,
                    root=self.alice_root)
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.normalize_selection, self.alice, "/etc",
                    root=self.alice_root)
        # Another member's project is outside this member's root even though the
        # path exists and is a directory.
        bob_project = self.mkdir(self.bob_root, "beta")
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.normalize_selection, self.alice, bob_project,
                    root=self.alice_root)

    def test_legacy_absolute_path_that_no_longer_exists_is_refused(self):
        gone = os.path.join(self.alice_root, "gone")
        self.refuse(project_browser.CODE_NOT_FOUND,
                    project_browser.normalize_selection, self.alice, gone,
                    root=self.alice_root)

    def test_session_ownership_is_rechecked_with_the_injected_check(self):
        seen = []

        def owner_of(session_id):
            seen.append(session_id)
            return "u_alice"

        path = project_browser.resolve_selection(
            self.alice, "alpha", root=self.alice_root,
            session_id="sess-1", session_owner=owner_of)
        self.assertEqual(path, os.path.join(self.alice_root, "alpha"))
        self.assertEqual(seen, ["sess-1"])

        self.refuse(project_browser.CODE_NOT_OWNER,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root, session_id="sess-2",
                    session_owner=lambda _sid: "u_bob")
        self.refuse(project_browser.CODE_NOT_OWNER,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root, session_id="sess-3",
                    session_owner=lambda _sid: None)

        def boom(_sid):
            raise RuntimeError("session store down")

        self.refuse(project_browser.CODE_NOT_OWNER,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root, session_id="sess-4",
                    session_owner=boom)

    def test_session_id_without_an_ownership_seam_is_refused(self):
        self.refuse(project_browser.CODE_INVALID_REQUEST,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root, session_id="sess-1")

    def test_recorded_identity_must_match_the_current_tenant_and_user(self):
        self.assertEqual(
            project_browser.resolve_selection(self.alice, "alpha",
                                              root=self.alice_root,
                                              recorded=self.alice),
            os.path.join(self.alice_root, "alpha"))
        self.assertEqual(
            project_browser.resolve_selection(
                self.alice, "alpha", root=self.alice_root,
                recorded={"tenant_id": "t1", "user_id": "u_alice"}),
            os.path.join(self.alice_root, "alpha"))

        # The same account in another tenant is a different scope (different
        # shared root), so the old identifier must not be replayed.
        self.refuse(project_browser.CODE_STALE_SELECTION,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root,
                    recorded=RuntimeIdentity(tenant_id="t2", user_id="u_alice"))
        self.refuse(project_browser.CODE_STALE_SELECTION,
                    project_browser.resolve_selection, self.alice, "alpha",
                    root=self.alice_root, recorded=self.bob)


# --- controlled import -------------------------------------------------------


class ImportCase(BrowserCase):
    """A source tree inside the member's own root, plus a link that must be skipped."""

    FILES = {"a.txt": "hello", ".hidden.txt": "h",
             "sub/b.txt": "bb", "sub/deep/c.txt": "ccc"}

    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.alice_root, "incoming", "proj")
        for rel, text in self.FILES.items():
            self.write(self.source, rel, text)
        self.outside_file = self.write(self.base, "outside/secret.txt", "secret")
        self.outside_dir = self.mkdir(self.base, "outside/dir")
        self.symlink(self.outside_file, os.path.join(self.source, "link.txt"))
        self.symlink(self.outside_dir, os.path.join(self.source, "sub/linkdir"))

    def source_rel(self):
        return os.path.join("incoming", "proj")

    def tree(self, root):
        """``{relative path: bytes}`` for every regular file under ``root``.

        Symlinked entries are reported as ``None`` rather than read through: the
        point of the comparison is what was copied, and following a link here
        would read whatever the link points at.
        """
        found = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for name in filenames:
                full = os.path.join(dirpath, name)
                key = os.path.relpath(full, root)
                if os.path.islink(full):
                    found[key] = None
                    continue
                with open(full, "rb") as handle:
                    found[key] = handle.read()
        return found


class ImportPreviewTests(ImportCase):
    def test_preview_counts_entries_and_bytes_and_excludes_links(self):
        result = project_browser.preview_import(
            self.alice, self.source, name="imported", root=self.alice_root)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["name"], "imported")
        self.assertEqual(result["target"], "imported")
        self.assertEqual(result["source_name"], "proj")
        self.assertEqual(result["files"], 4)
        self.assertEqual(result["dirs"], 2)
        self.assertEqual(result["entries"], 6)
        self.assertEqual(result["bytes"], len("hello") + len("h") + len("bb")
                         + len("ccc"))
        self.assertEqual(result["excluded"],
                         ["incoming/proj/link.txt", "incoming/proj/sub/linkdir"])
        self.assertIsNone(result["conflict"])
        self.assertTrue(result["ready"])

    def test_preview_defaults_the_name_to_the_source_folder(self):
        result = project_browser.preview_import(
            self.alice, self.source, root=self.alice_root)
        self.assertEqual(result["name"], "proj")

    def test_preview_reports_a_conflict_without_touching_the_existing_project(self):
        existing = self.write(self.alice_root, "proj/keep.txt", "mine")

        result = project_browser.preview_import(
            self.alice, self.source, root=self.alice_root)

        self.assertFalse(result["ready"])
        self.assertEqual(result["conflict"]["code"], "name_taken")
        self.assertEqual(result["conflict"]["name"], "proj")
        with open(existing, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "mine")

    def test_preview_creates_nothing(self):
        before = sorted(os.listdir(self.alice_root))
        project_browser.preview_import(self.alice, self.source,
                                       name="imported", root=self.alice_root)
        self.assertEqual(sorted(os.listdir(self.alice_root)), before)
        self.assertEqual(self.staging_leftovers(self.alice_root), [])

    def test_preview_refuses_invalid_names(self):
        for bad in ["a/b", "..", ".hidden", "C:\\x"]:
            with self.subTest(name=bad):
                self.refuse(project_browser.CODE_INVALID_NAME,
                            project_browser.preview_import, self.alice,
                            self.source, name=bad, root=self.alice_root)

    def test_blank_name_falls_back_to_the_source_folder_name(self):
        for blank in (None, "", "   "):
            with self.subTest(name=blank):
                result = project_browser.preview_import(
                    self.alice, self.source, name=blank, root=self.alice_root)
                self.assertEqual(result["name"], "proj")


class ImportPublishTests(ImportCase):
    def test_import_publishes_the_tree_and_leaves_no_staging(self):
        result = project_browser.import_directory(
            self.alice, self.source, name="imported", root=self.alice_root)

        target = os.path.join(self.alice_root, "imported")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["path"], target)
        self.assertEqual(result["files"], 4)
        self.assertEqual(result["dirs"], 2)
        self.assertEqual(result["bytes"], 11)
        self.assertEqual(result["excluded"],
                         ["incoming/proj/link.txt", "incoming/proj/sub/linkdir"])
        # Links are excluded, never followed, so the outside data is not copied.
        self.assertEqual(self.tree(target), {
            "a.txt": b"hello", ".hidden.txt": b"h",
            os.path.join("sub", "b.txt"): b"bb",
            os.path.join("sub", "deep", "c.txt"): b"ccc",
        })
        self.assertEqual(self.staging_leftovers(self.alice_root), [])
        self.assertFalse(os.path.lexists(os.path.join(target, "link.txt")))
        self.assertFalse(os.path.lexists(os.path.join(target, "sub", "linkdir")))
        # The source is untouched: importing copies, it does not move. Its
        # symlink is still there (reported as ``None`` because the helper does
        # not read through it) -- nothing was consumed or rewritten.
        self.assertTrue(os.path.isdir(self.source))
        self.assertEqual(self.tree(self.source), {
            "a.txt": b"hello", ".hidden.txt": b"h", "link.txt": None,
            os.path.join("sub", "b.txt"): b"bb",
            os.path.join("sub", "deep", "c.txt"): b"ccc",
        })

    def test_imported_project_is_browsable_and_selectable(self):
        project_browser.import_directory(self.alice, self.source,
                                         name="imported", root=self.alice_root)

        listed = project_browser.browse(self.alice, "", root=self.alice_root)
        self.assertIn("imported", self.names(listed))
        selected = project_browser.resolve_selection(
            self.alice, "imported", root=self.alice_root)
        self.assertEqual(selected, os.path.join(self.alice_root, "imported"))

    def test_existing_project_is_reported_not_overwritten(self):
        keep = self.write(self.alice_root, "proj/keep.txt", "mine")
        self.refuse(project_browser.CODE_CONFLICT,
                    project_browser.import_directory, self.alice, self.source,
                    root=self.alice_root)
        with open(keep, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "mine")
        self.assertEqual(self.staging_leftovers(self.alice_root), [])

    def test_cancelled_import_leaves_nothing_behind(self):
        def cancel(plan):
            self.assertTrue(os.path.isdir(plan["staging"]))
            raise ImportCancelled("user cancelled")

        self.refuse(project_browser.CODE_CANCELLED,
                    project_browser.import_directory, self.alice, self.source,
                    name="imported", root=self.alice_root,
                    stage_hook=cancel)

        self.assertFalse(os.path.exists(os.path.join(self.alice_root, "imported")))
        self.assertEqual(self.staging_leftovers(self.alice_root), [])
        self.assertEqual(sorted(os.listdir(self.alice_root)), ["incoming"])

    def test_failed_publish_rolls_the_staging_tree_back(self):
        def fail(_plan):
            raise RuntimeError("audit unavailable")

        self.refuse(project_browser.CODE_IMPORT_FAILED,
                    project_browser.import_directory, self.alice, self.source,
                    name="imported", root=self.alice_root, stage_hook=fail)

        self.assertFalse(os.path.exists(os.path.join(self.alice_root, "imported")))
        self.assertEqual(self.staging_leftovers(self.alice_root), [])
        self.assertEqual(sorted(os.listdir(self.alice_root)), ["incoming"])

    def test_refused_quota_aborts_before_anything_is_created(self):
        calls = []

        def refuse_quota(plan):
            calls.append(plan)
            raise RuntimeError("quota exceeded")

        self.refuse(project_browser.CODE_QUOTA_REFUSED,
                    project_browser.import_directory, self.alice, self.source,
                    name="imported", root=self.alice_root,
                    quota_reserve=refuse_quota)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["bytes"], 11)
        self.assertEqual(calls[0]["target"], "imported")
        self.assertFalse(os.path.exists(os.path.join(self.alice_root, "imported")))
        self.assertEqual(self.staging_leftovers(self.alice_root), [])
        self.assertEqual(sorted(os.listdir(self.alice_root)), ["incoming"])

    def test_quota_reservation_is_committed_only_after_publish(self):
        target = os.path.join(self.alice_root, "imported")
        events = []

        class Reservation:
            def commit(self):
                # Committing before publish would account for a project that
                # does not exist yet.
                events.append(("commit", os.path.isdir(target)))

            def release(self):
                events.append(("release", None))

        result = project_browser.import_directory(
            self.alice, self.source, name="imported", root=self.alice_root,
            quota_reserve=lambda plan: Reservation())

        self.assertEqual(result["quota_state"], "ok")
        self.assertEqual(events, [("commit", True)])

    def test_quota_reservation_is_released_when_publish_is_rolled_back(self):
        events = []

        class Reservation:
            def commit(self):
                events.append("commit")

            def release(self):
                events.append("release")

        def fail(_plan):
            raise ImportCancelled("cancelled")

        self.refuse(project_browser.CODE_CANCELLED,
                    project_browser.import_directory, self.alice, self.source,
                    name="imported", root=self.alice_root,
                    quota_reserve=lambda plan: Reservation(), stage_hook=fail)

        self.assertEqual(events, ["release"])

    def test_quota_commit_failure_is_reported_not_hidden(self):
        class Reservation:
            def commit(self):
                raise RuntimeError("quota database down")

        result = project_browser.import_directory(
            self.alice, self.source, name="imported", root=self.alice_root,
            quota_reserve=lambda plan: Reservation())

        self.assertEqual(result["quota_state"], "commit_failed")
        self.assertTrue(os.path.isdir(os.path.join(self.alice_root, "imported")))


class ImportSourceScopeTests(ImportCase):
    def test_source_outside_the_members_root_is_refused(self):
        outside = self.mkdir(self.base, "outside/proj")

        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.preview_import, self.alice, outside,
                    root=self.alice_root)
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.import_directory, self.alice, outside,
                    name="imported", root=self.alice_root)
        self.assertEqual(sorted(os.listdir(self.alice_root)), ["incoming"])

    def test_another_members_tree_is_refused_as_a_source(self):
        bob_project = self.mkdir(self.bob_root, "beta")
        self.write(bob_project, "data.txt", "not yours")

        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.preview_import, self.alice, bob_project,
                    root=self.alice_root)

    def test_relative_or_missing_source_is_refused(self):
        self.refuse(project_browser.CODE_INVALID_REQUEST,
                    project_browser.preview_import, self.alice, "incoming/proj",
                    root=self.alice_root)
        self.refuse(project_browser.CODE_NOT_FOUND,
                    project_browser.preview_import, self.alice,
                    os.path.join(self.alice_root, "nope"), root=self.alice_root)

    def test_the_allowed_root_itself_is_not_a_source(self):
        self.refuse(project_browser.CODE_INVALID_REQUEST,
                    project_browser.preview_import, self.alice, self.alice_root,
                    root=self.alice_root)

    def test_link_out_of_the_allowed_root_is_refused(self):
        link = os.path.join(self.alice_root, "linked-out")
        os.symlink(self.outside_dir, link)
        # Resolution lands outside the root, so containment refuses it rather
        # than copying through the link.
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.preview_import, self.alice, link,
                    root=self.alice_root)

    def test_link_that_stays_inside_the_root_resolves_to_the_real_directory(self):
        # A link that never leaves the root is not an escape: the resolved path
        # is what is imported, so a link inside the member's own tree is simply
        # another way to name their own data.
        link = os.path.join(self.alice_root, "alias")
        os.symlink(os.path.join(self.alice_root, "incoming"), link)

        result = project_browser.import_directory(
            self.alice, os.path.join(link, "proj"), name="aliased",
            root=self.alice_root)

        self.assertEqual(result["path"], os.path.join(self.alice_root, "aliased"))
        self.assertEqual(result["files"], 4)
        self.assertNotIn(os.path.join("aliased", "link.txt"),
                         self.tree(result["path"]))

    def test_extra_source_roots_widen_the_scope_explicitly(self):
        allowed = os.path.join(self.base, "native", "workspace", "proj")
        self.write(allowed, "keep.txt", "native project")
        anchor = os.path.join(self.base, "native", "workspace")

        # Default policy: outside the member's root, refused.
        self.refuse(project_browser.CODE_OUTSIDE_ROOT,
                    project_browser.preview_import, self.alice, allowed,
                    root=self.alice_root)

        result = project_browser.import_directory(
            self.alice, allowed, name="native", root=self.alice_root,
            source_roots=[anchor])

        self.assertEqual(result["path"], os.path.join(self.alice_root, "native"))
        self.assertEqual(self.tree(result["path"]), {"keep.txt": b"native project"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
