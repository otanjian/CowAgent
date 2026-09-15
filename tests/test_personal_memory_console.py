# encoding:utf-8
"""成员「我的记忆」控制台（change enable-member-personal-console, stage 5）.

「我的记忆」是**当前租户 + 当前用户**的事实，跨本人获准的任意智能体可见；它与
「该私有 Agent 记忆」（当前租户 + 该 Agent 的工作区）共用归属校验，但不共用存储根。
这些用例把这一区别固定下来，而不是靠调用方自觉：

* 入口只吃**相对标识**（``MEMORY.md`` / ``memory/<name>.md``），没有 agent、没有
  用户 id、没有绝对路径 —— 归属来自可信身份，且无法用路径构造指向他人；
* 读、写、删、清空都在本人用户域内，共享 Agent 记忆与租户知识不在这条路径上；
* 编辑/删除/清空携带版本条件，冲突不覆盖新内容；
* 删除与清空后检索不得返回旧正文，即使索引清理失败（失败要如实报告，缓存下待重试
  的标签在检索入口继续屏蔽，重试只影响目标标签）；
* 清空推进作用域版本，清空前排队的自动固化任务不能凭旧版本把内容写回来。
"""

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from auth.runtime import RequestContext
from auth.service import IdentityService
from channel.web import web_channel
from common.runtime_identity import RuntimeIdentity, use_identity
from common import state_dir

import web


def _mkdb():
    return os.path.join(tempfile.mkdtemp(), "identity.db")


class _Fixture(unittest.TestCase):
    def setUp(self):
        web.ctx.headers = []
        web.ctx.status = "200 OK"
        self.db = _mkdb()
        self.svc = IdentityService(self.db)
        self.shared = tempfile.mkdtemp(prefix="acme-")
        self.svc.bootstrap(
            tenant_code="acme", tenant_name="Acme", admin_username="root",
            admin_display="Root", admin_password="Str0ngAdminPass",
            shared_root=self.shared, allow_weak=True)
        self.tid = self.svc.list_tenants()[0]["id"]
        self.root = self.svc.list_platform_users()[0]
        self.alice = self._member("alice")
        self.bob = self._member("bob")
        self.ws = tempfile.mkdtemp(prefix="agent-x-")
        self._svc_patch = patch("auth.service.get_identity_service",
                                lambda: self.svc)
        self._svc_patch.start()
        self.addCleanup(self._svc_patch.stop)
        self.addCleanup(self._reset_caches)

    @staticmethod
    def _reset_caches():
        from agent.memory import clear_conversation_store_cache
        from agent.memory.config import reset_memory_configs
        clear_conversation_store_cache()
        reset_memory_configs()

    def _member(self, username):
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=self.tid,
            operation="create-new", username=username, display_name=username,
            temporary_password="MemTempPass1", roles=["member"])
        return [m for m in self.svc.list_members(self.tid)["items"]
                if m["username"] == username][0]["user_id"]

    def _ident(self, user_id, agent_id="agent-x", tenant_id=None):
        return RuntimeIdentity(agent_id=agent_id, user_id=user_id,
                               tenant_id=tenant_id or self.tid)

    def _svc(self, user_id, *, agent_id="agent-x", tenant_id=None, **kw):
        from agent.memory.personal import PersonalMemoryService
        return PersonalMemoryService(
            identity=self._ident(user_id, agent_id=agent_id,
                                 tenant_id=tenant_id),
            **kw)

    def _ctx(self, user_id, *, platform_admin=False, tenant_admin=False):
        return RequestContext(
            user_id=user_id, username="u", display_name="U",
            is_platform_admin=platform_admin, must_change_password=False,
            tenant_id=self.tid, membership=None,
            permissions=set(self.svc.permissions_for(user_id, self.tid)),
            is_tenant_admin=tenant_admin)

    def _user_root(self, user_id):
        with use_identity(self._ident(user_id)):
            return Path(state_dir.user_root())

    def _index_db(self, ws=None):
        return Path(ws or self.ws) / "memory" / "long-term" / "index.db"


class ListingTests(_Fixture):
    def test_list_read_save_delete_round_trip(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            self.assertEqual(svc.list_entries(), [])

            created = svc.save("MEMORY.md", "# Alice\n\nQUARTZ is my code word\n")
            self.assertEqual(created["revision"], created["revision"])
            self.assertTrue(created["revision"])

            entries = svc.list_entries()
            self.assertEqual([e["id"] for e in entries], ["MEMORY.md"])

            read = svc.read("MEMORY.md")
            self.assertIn("QUARTZ", read["content"])
            self.assertEqual(read["revision"], created["revision"])

            svc.delete("MEMORY.md", expected_revision=created["revision"])
            self.assertEqual(svc.list_entries(), [])

    def test_save_writes_to_the_user_domain_not_the_agent_workspace(self):
        shared_memory = Path(self.ws) / "MEMORY.md"
        shared_memory.write_text("SHARED-ORIGINAL\n", encoding="utf-8")

        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            svc.save("MEMORY.md", "ALICE-PRIVATE\n")
            svc.save("memory/notes.md", "ALICE-NOTE\n")
            user_root = Path(state_dir.user_root())

        self.assertTrue(
            os.path.realpath(str(user_root)).startswith(
                os.path.realpath(self.shared)),
            f"个人记忆写到了 {user_root}，不在租户共享根 {self.shared} 下")
        self.assertEqual(
            (user_root / "MEMORY.md").read_text(encoding="utf-8"),
            "ALICE-PRIVATE\n")
        self.assertEqual(
            (user_root / "memory" / "notes.md").read_text(encoding="utf-8"),
            "ALICE-NOTE\n")
        self.assertEqual(shared_memory.read_text(encoding="utf-8"),
                         "SHARED-ORIGINAL\n",
                         "个人记忆写入口改动了共享 Agent 记忆")

    def test_the_private_agent_memory_entry_point_is_still_separate(self):
        """「该私有 Agent 记忆」仍是 MemoryService(workspace) 的读取范围。"""
        from agent.memory.service import MemoryService

        with use_identity(self._ident(self.alice)):
            self._svc(self.alice).save("MEMORY.md", "ALICE-PRIVATE\n")

        agent_view = MemoryService(self.ws).list_files()
        self.assertEqual([f["filename"] for f in agent_view["list"]], [],
                         "个人记忆出现在了该 Agent 的记忆入口")


class IdentifierAndIsolationTests(_Fixture):
    def test_a_path_shaped_entry_id_is_refused(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            from agent.memory.personal import PersonalMemoryError
            for bad in ("../bob/MEMORY.md", "memory/../../MEMORY.md",
                        "/etc/passwd", "memory/sub/notes.md",
                        "memory/users/bob/MEMORY.md", "notes.txt"):
                with self.assertRaises(PersonalMemoryError, msg=bad):
                    svc.read(bad)

    def test_another_users_memory_is_not_addressable(self):
        with use_identity(self._ident(self.bob)):
            self._svc(self.bob).save("MEMORY.md", "BOB-SECRET\n")

        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            self.assertEqual(svc.list_entries(), [])
            from agent.memory.personal import PersonalMemoryError
            # No id names Bob: the caller's own root is the only one reachable.
            with self.assertRaises(PersonalMemoryError):
                svc.read("memory/users/%s/MEMORY.md" % self.bob)
            read = svc.read("MEMORY.md")
            self.assertNotIn("BOB-SECRET", read.get("content", ""))

            files = []
            root = Path(state_dir.user_root())
            for base, _dirs, names in os.walk(str(root)):
                files.extend(names)
        self.assertNotIn("BOB-SECRET", "".join(
            (Path(base) / n).read_text(encoding="utf-8")
            for base, _d, ns in os.walk(str(root)) for n in ns
            if n.endswith(".md")))

    def test_the_same_account_in_another_tenant_reads_nothing(self):
        beta_root = tempfile.mkdtemp(prefix="beta-")
        beta = self.svc.create_tenant(
            actor_user_id=self.root["id"], code="beta", name="Beta",
            shared_root=beta_root, admin_username="betaadmin",
            admin_display="Beta Admin", admin_password="Str0ng2Pass",
            recent_password="Str0ngAdminPass")
        # The *same account* is a member of both tenants. The account is the
        # same user id; the personal memory must still be tenant-local.
        self.svc.create_member(
            actor_user_id=self.root["id"], tenant_id=beta["id"],
            operation="bind-existing", username="alice", display_name="Alice",
            temporary_password="MemTempPass1", roles=["member"])

        with use_identity(self._ident(self.alice)):
            self._svc(self.alice).save("MEMORY.md", "ACME-ONLY-TOKEN\n")

        with use_identity(self._ident(self.alice, tenant_id=beta["id"])):
            svc = self._svc(self.alice, agent_id="agent-x",
                            tenant_id=beta["id"])
            self.assertEqual(svc.list_entries(), [])
            # Same account, same entry id, different tenant: the answer is this
            # tenant's own (empty) memory, never the other tenant's content.
            self.assertEqual(svc.read("MEMORY.md")["content"], "")

    def test_an_identity_without_a_user_is_refused(self):
        from agent.memory.personal import PersonalMemoryError
        svc = self._svc(None)
        with self.assertRaises(PersonalMemoryError) as exc:
            svc.list_entries()
        self.assertEqual(exc.exception.code, "no_identity")

    def test_a_tenant_admin_cannot_reach_another_members_memory(self):
        with use_identity(self._ident(self.bob)):
            self._svc(self.bob).save("MEMORY.md", "BOB-SECRET\n")

        with use_identity(self._ident(self.root["id"])):
            admin = self._svc(self.root["id"])
            admin.save("MEMORY.md", "ROOT-OWN\n")
            self.assertNotIn("BOB-SECRET", admin.read("MEMORY.md")["content"])


class EditingConflictTests(_Fixture):
    def test_a_stale_revision_is_refused_without_overwriting(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            first = svc.save("MEMORY.md", "FIRST\n")
            from agent.memory.personal import PersonalMemoryError
            with self.assertRaises(PersonalMemoryError) as exc:
                svc.save("MEMORY.md", "SECOND\n", expected_revision="not-the-one")
            self.assertEqual(exc.exception.status, 409)
            self.assertEqual(exc.exception.code, "stale_revision")
            self.assertEqual(svc.read("MEMORY.md")["content"], "FIRST\n")

    def test_an_edit_carrying_the_current_revision_wins(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            first = svc.save("MEMORY.md", "FIRST\n")
            second = svc.save("MEMORY.md", "SECOND\n",
                              expected_revision=first["revision"])
            self.assertNotEqual(second["revision"], first["revision"])
            self.assertEqual(svc.read("MEMORY.md")["content"], "SECOND\n")

    def test_editing_a_missing_entry_requires_no_revision(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            created = svc.save("memory/new.md", "NEW\n")
            self.assertTrue(created["revision"])

    def test_non_string_content_is_refused(self):
        from agent.memory.personal import PersonalMemoryError
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            with self.assertRaises(PersonalMemoryError):
                svc.save("MEMORY.md", {"not": "text"})

    def test_two_pages_saving_the_same_version_produce_one_winner(self):
        """Both pages read revision R; the second save must be refused."""
        import threading

        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            created = svc.save("MEMORY.md", "ORIGINAL\n")
            revision = created["revision"]

            outcomes = []

            def _save(label):
                try:
                    svc.save("MEMORY.md", label + "\n",
                             expected_revision=revision)
                    outcomes.append((label, "ok"))
                except Exception as exc:  # PersonalMemoryError
                    outcomes.append((label, getattr(exc, "code", "error")))

            import contextvars
            threads = []
            for name in ("FIRST-PAGE", "SECOND-PAGE"):
                # A context copy per thread, the same way the async flush worker
                # carries the caller's identity into a background thread.
                ctx = contextvars.copy_context()
                threads.append(threading.Thread(
                    target=ctx.run, args=(_save, name)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            wins = [o for o in outcomes if o[1] == "ok"]
            losses = [o for o in outcomes if o[1] == "stale_revision"]
            self.assertEqual(len(wins), 1, outcomes)
            self.assertEqual(len(losses), 1, outcomes)
            # The surviving content is the winner's, not a mixture.
            self.assertIn(svc.read("MEMORY.md")["content"].strip(),
                          ("FIRST-PAGE", "SECOND-PAGE"))

    def test_delete_uses_the_same_version_condition(self):
        from agent.memory.personal import PersonalMemoryError
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            created = svc.save("MEMORY.md", "FIRST\n")
            with self.assertRaises(PersonalMemoryError) as exc:
                svc.delete("MEMORY.md", expected_revision="stale")
            self.assertEqual(exc.exception.status, 409)
            self.assertEqual(svc.read("MEMORY.md")["content"], "FIRST\n")

            svc.delete("MEMORY.md", expected_revision=created["revision"])
            self.assertEqual(svc.list_entries(), [])


class IndexConsistencyTests(_Fixture):
    def _manager(self):
        from agent.memory.config import MemoryConfig
        from agent.memory.manager import MemoryManager
        return MemoryManager(
            config=MemoryConfig(workspace_root=self.ws, min_score=0.0),
            embedding_provider=None)

    def _search(self, query, user_id):
        import asyncio
        manager = self._manager()
        with use_identity(self._ident(user_id)):
            asyncio.run(manager.sync())
            return asyncio.run(manager.search(query, user_id=user_id))

    def test_saved_content_becomes_searchable(self):
        with use_identity(self._ident(self.alice)):
            self._svc(self.alice, index_dbs=[self._index_db()]).save(
                "MEMORY.md", "PROJECTOR-QUARTZ is my code word\n")
        hits = self._search("PROJECTOR-QUARTZ", self.alice)
        self.assertTrue(hits, "保存后的个人记忆没有被检索到")

    def test_deleted_content_is_not_returned_by_search(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice, index_dbs=[self._index_db()])
            created = svc.save("MEMORY.md", "PROJECTOR-QUARTZ is my code word\n")
            # Index it once through the real sync path too, so the purge has to
            # remove rows it did not itself write.
            self._search("PROJECTOR-QUARTZ", self.alice)
            svc.delete("MEMORY.md", expected_revision=created["revision"])
        self.assertFalse(self._search("PROJECTOR-QUARTZ", self.alice),
                         "删除后的正文仍从旧索引返回")

    def test_clear_purges_every_personal_entry_and_other_users_survive(self):
        with use_identity(self._ident(self.bob)):
            self._svc(self.bob).save("MEMORY.md", "BOBPRIVATE-KEEPALIVE\n")
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice, index_dbs=[self._index_db()])
            svc.save("MEMORY.md", "ALICEPRIVATE-ONETOKEN\n")
            svc.save("memory/day.md", "ALICEPRIVATE-TWOTOKEN\n")
            self._search("ALICEPRIVATE", self.alice)
            result = svc.clear()
            self.assertEqual(result["index_state"], "ok")
            self.assertEqual(svc.list_entries(), [])
        self.assertFalse(self._search("ALICEPRIVATE-ONETOKEN", self.alice))
        self.assertFalse(self._search("ALICEPRIVATE-TWOTOKEN", self.alice))

    def test_the_personal_memory_is_visible_from_another_agent(self):
        """Writing under Agent X, searching under Agent Y must find it."""
        from agent.memory.config import MemoryConfig
        from agent.memory.manager import MemoryManager
        import asyncio

        ws_y = tempfile.mkdtemp(prefix="agent-y-")
        db_y = Path(ws_y) / "memory" / "long-term" / "index.db"
        with use_identity(self._ident(self.alice)):
            self._svc(self.alice,
                      index_dbs=[self._index_db(), db_y]).save(
                "MEMORY.md", "PROJECTOR-QUARTZ is my code word\n")

        with use_identity(self._ident(self.alice, agent_id="agent-y")):
            manager = MemoryManager(
                config=MemoryConfig(workspace_root=ws_y, min_score=0.0),
                embedding_provider=None)
            asyncio.run(manager.sync())
            hits = asyncio.run(
                manager.search("PROJECTOR-QUARTZ", user_id=self.alice))
        self.assertTrue(hits, "个人记忆在另一获准智能体下不可见")

    def test_a_failed_index_purge_is_reported_and_still_hidden_from_search(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice, index_dbs=[self._index_db()])
            created = svc.save("MEMORY.md", "PROJECTOR-QUARTZ is my code word\n")
            self._search("PROJECTOR-QUARTZ", self.alice)

            with patch("agent.memory.personal._purge_label",
                       side_effect=RuntimeError("index unavailable")):
                result = svc.delete("MEMORY.md",
                                    expected_revision=created["revision"])

        self.assertEqual(result["index_state"], "pending")
        self.assertFalse(result["index_state"] == "ok")
        # Content is gone and, even though the index row survived, retrieval
        # must not hand the deleted body back.
        self.assertFalse(self._search("PROJECTOR-QUARTZ", self.alice),
                         "索引清理失败时旧索引仍返回了已删除正文")

        # A retry that succeeds clears the pending marker.
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice, index_dbs=[self._index_db()])
            retried = svc.retry_pending_index()
        self.assertEqual(retried["pending"], [])
        self.assertFalse(self._search("PROJECTOR-QUARTZ", self.alice))

    def test_clear_reports_pending_instead_of_success_when_the_index_fails(self):
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice, index_dbs=[self._index_db()])
            svc.save("MEMORY.md", "ALICEPRIVATE-ONETOKEN\n")
            self._search("ALICEPRIVATE-ONETOKEN", self.alice)
            with patch("agent.memory.personal._purge_label",
                       side_effect=RuntimeError("index unavailable")):
                result = svc.clear()
        self.assertEqual(result["index_state"], "pending")
        self.assertNotEqual(result.get("status", "pending"), "success")
        self.assertFalse(self._search("ALICEPRIVATE-ONETOKEN", self.alice))


class ScopeVersionTests(_Fixture):
    def test_clear_bumps_the_scope_generation(self):
        from agent.memory.personal import read_scope_generation
        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            before = read_scope_generation(self._ident(self.alice))
            svc.save("MEMORY.md", "ONE\n")
            svc.clear()
            after = read_scope_generation(self._ident(self.alice))
        self.assertEqual(after, before + 1)

    def test_a_queued_flush_cannot_restore_cleared_content(self):
        from agent.memory.summarizer import MemoryFlushManager

        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            svc.save("MEMORY.md", "OLD\n")
            queued_generation = svc.scope_generation()

            svc.clear()
            manager = MemoryFlushManager(workspace_dir=Path(self.ws))
            wrote = manager.write_daily_summary(
                "STALE-CONSOLIDATION", user_id=self.alice,
                scope_generation=queued_generation)

        self.assertFalse(wrote, "清空前排队的固化任务按旧版本写回了内容")
        self.assertFalse(self._search_daily("STALE-CONSOLIDATION"),
                         "旧版本产物进入了已清空的个人记忆")

    def test_a_flush_after_the_clear_still_persists(self):
        from agent.memory.summarizer import MemoryFlushManager

        with use_identity(self._ident(self.alice)):
            svc = self._svc(self.alice)
            svc.clear()
            current = svc.scope_generation()
            manager = MemoryFlushManager(workspace_dir=Path(self.ws))
            wrote = manager.write_daily_summary(
                "FRESH-CONSOLIDATION", user_id=self.alice,
                scope_generation=current)

        self.assertTrue(wrote, "清空后的新任务被旧版本判据误拒")
        self.assertTrue(self._search_daily("FRESH-CONSOLIDATION"))

    def _search_daily(self, token):
        import asyncio
        from agent.memory.config import MemoryConfig
        from agent.memory.manager import MemoryManager
        with use_identity(self._ident(self.alice)):
            manager = MemoryManager(
                config=MemoryConfig(workspace_root=self.ws, min_score=0.0),
                embedding_provider=None)
            asyncio.run(manager.sync())
            return asyncio.run(manager.search(token, user_id=self.alice))

    def test_a_flush_without_a_recorded_generation_is_unaffected(self):
        """同步写入（/compact）当场落盘，没有可陈旧的版本，保持原行为。"""
        from agent.memory.summarizer import MemoryFlushManager

        with use_identity(self._ident(self.alice)):
            manager = MemoryFlushManager(workspace_dir=Path(self.ws))
            self.assertTrue(manager.write_daily_summary(
                "SYNC-NOTE", user_id=self.alice))


class HandlerWiringTests(_Fixture):
    def _scope(self, ctx):
        @contextlib.contextmanager
        def _cm():
            with use_identity(self._ident(ctx.user_id,
                                          tenant_id=ctx.tenant_id)):
                yield ctx
        return _cm()

    def _get(self, handler, ctx, **params):
        with patch.object(web_channel, "_db_scope", lambda: self._scope(ctx)), \
                patch.object(web_channel.web, "input",
                             lambda **kw: type("I", (), {
                                 k: params.get(k, kw.get(k, ""))
                                 for k in set(list(params) + list(kw))})()):
            return json.loads(handler())

    def _post(self, ctx, payload):
        with patch.object(web_channel, "_db_scope", lambda: self._scope(ctx)), \
                patch.object(web_channel.web, "data",
                             lambda: json.dumps(payload).encode()):
            return json.loads(web_channel.PersonalMemoryHandler().POST())

    def test_list_and_read_go_through_the_handler(self):
        alice = self._ctx(self.alice)
        body = self._post(alice, {"action": "save", "id": "MEMORY.md",
                                  "content": "ALICE-HANDLER-TOKEN\n"})
        self.assertEqual(body["status"], "success", body)

        listed = self._get(web_channel.PersonalMemoryHandler().GET, alice)
        self.assertEqual(listed["status"], "success", listed)
        self.assertEqual([e["id"] for e in listed["entries"]], ["MEMORY.md"])

        read = self._get(web_channel.PersonalMemoryContentHandler().GET, alice,
                         id="MEMORY.md")
        self.assertIn("ALICE-HANDLER-TOKEN", read["content"])

    def test_another_member_cannot_read_it_through_the_handler(self):
        self._post(self._ctx(self.alice),
                   {"action": "save", "id": "MEMORY.md",
                    "content": "ALICE-HANDLER-TOKEN\n"})
        listed = self._get(web_channel.PersonalMemoryHandler().GET,
                           self._ctx(self.bob))
        self.assertEqual(listed["entries"], [])

        # Bob's own memory is empty; Alice's token is nowhere in it.
        bob_view = self._svc(self.bob).read("MEMORY.md")
        self.assertEqual(bob_view["content"], "")

    def test_an_admin_does_not_get_another_members_entry(self):
        self._post(self._ctx(self.alice),
                   {"action": "save", "id": "MEMORY.md",
                    "content": "ALICE-HANDLER-TOKEN\n"})
        admin = self._ctx(self.root["id"], platform_admin=True,
                          tenant_admin=True)
        listed = self._get(web_channel.PersonalMemoryHandler().GET, admin)
        self.assertEqual(listed["entries"], [])

    def test_a_stale_edit_returns_a_conflict_code(self):
        alice = self._ctx(self.alice)
        first = self._post(alice, {"action": "save", "id": "MEMORY.md",
                                   "content": "ONE\n"})
        stale = self._post(alice, {"action": "save", "id": "MEMORY.md",
                                   "content": "TWO\n",
                                   "revision": "not-current"})
        self.assertEqual(stale["status"], "error", stale)
        self.assertEqual(stale["code"], "stale_revision")
        self.assertTrue(str(web.ctx.status).startswith("409"), web.ctx.status)
        self.assertEqual(first["status"], "success")

    def test_clear_through_the_handler_reports_ok(self):
        alice = self._ctx(self.alice)
        self._post(alice, {"action": "save", "id": "MEMORY.md",
                           "content": "ONE\n"})
        cleared = self._post(alice, {"action": "clear"})
        self.assertEqual(cleared["status"], "success", cleared)
        self.assertEqual(cleared["index_state"], "ok")
        listed = self._get(web_channel.PersonalMemoryHandler().GET, alice)
        self.assertEqual(listed["entries"], [])

    def test_a_bad_entry_id_is_refused_without_a_traceback(self):
        alice = self._ctx(self.alice)
        body = self._post(alice, {"action": "save",
                                  "id": "../bob/MEMORY.md", "content": "X\n"})
        self.assertEqual(body["status"], "error", body)
        self.assertEqual(body["code"], "invalid_entry")


if __name__ == "__main__":
    unittest.main()
