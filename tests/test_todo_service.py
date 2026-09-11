# encoding:utf-8
"""Tests for the personal todo service and Web routes.

Covers the storage/service layer (create-dedup, version-cas, four-state machine,
overdue derivation, terminal-edit guard) and the Web handler authorization
(legacy login required, no-password refuse, database permission gate). Uses a
temp data root so nothing touches the developer's real data.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web

from agent.todo.store import TodoStore
from agent.todo.service import (
    TodoService,
    TodoActor,
    TodoConflictError,
    TodoFieldValidationError,
    TodoPermissionDenied,
    TodoUnauthorized,
)
from channel.web import todo_handlers


def _make_service(db_dir, enabled=True):
    actor = TodoActor(
        bound=True, scope_id="tenant-1", owner_id="user-1", username="user-1",
        permissions={"todo.read", "todo.write"},
    )
    db_path = str(Path(db_dir) / "todo" / "todos.db")
    return TodoService(actor, enabled_fn=lambda: enabled, db_path=db_path)


class TodoServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = _make_service(self.tmp)

    def test_create_and_idempotent_retry(self):
        item = self.svc.create(title="  buy milk  ", create_key="k1")
        self.assertEqual(item["title"], "buy milk")
        self.assertEqual(item["status"], "pending")
        # Same key + same payload -> same id
        again = self.svc.create(title="buy milk", create_key="k1")
        self.assertEqual(again["id"], item["id"])
        # Same key + different content -> conflict
        with self.assertRaises(TodoConflictError):
            self.svc.create(title="change", create_key="k1")

    def test_edit_then_retry_original_create(self):
        item = self.svc.create(title="report", create_key="k2")
        updated = self.svc.update(item["id"], expected_version=item["version"], fields={"title": "REPORT"})
        # Re-run original create (same key, original content) -> same id, current title
        retry = self.svc.create(title="report", create_key="k2")
        self.assertEqual(retry["id"], item["id"])
        self.assertEqual(retry["title"], "REPORT")

    def test_version_conflict_and_stale_replay(self):
        item = self.svc.create(title="t", create_key="k3")
        updated = self.svc.update(item["id"], expected_version=item["version"], fields={"priority": "high"})
        with self.assertRaises(TodoConflictError):
            self.svc.update(item["id"], expected_version=item["version"], fields={"priority": "low"})

    def test_four_state_machine_and_terminal_edit_guard(self):
        item = self.svc.create(title="t", create_key="k4")
        started = self.svc.update(item["id"], expected_version=item["version"], status="in_progress")
        completed = self.svc.update(started["id"], expected_version=started["version"], status="completed")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["completed_at"])
        # terminal edit rejected
        with self.assertRaises(TodoConflictError):
            self.svc.update(completed["id"], expected_version=completed["version"], fields={"title": "x"})
        # reopen
        reopened = self.svc.update(completed["id"], expected_version=completed["version"], status="pending")
        self.assertEqual(reopened["status"], "pending")
        self.assertIsNone(reopened["completed_at"])

    def test_overdue_derivation(self):
        item = self.svc.create(title="due in past", due_at="2020-01-01T00:00:00+08:00", timezone="Asia/Shanghai", create_key="k5")
        self.assertTrue(item["overdue"])


class TodoWebHandlerTests(unittest.TestCase):
    """Exercise actor resolution without a silent local-owner fallback."""

    def test_missing_session_refuses(self):
        with patch("channel.web.todo_handlers._database_session_token", return_value=""), \
             patch("channel.web.todo_handlers._database_tenant_header", return_value="tenant-1"):
            with self.assertRaises(TodoUnauthorized):
                todo_handlers._resolve_actor()

    def test_missing_tenant_refuses(self):
        with patch("channel.web.todo_handlers._database_session_token", return_value="tok"), \
             patch("channel.web.todo_handlers._database_tenant_header", return_value=""):
            with self.assertRaises(TodoUnauthorized):
                todo_handlers._resolve_actor()


if __name__ == "__main__":
    unittest.main()
