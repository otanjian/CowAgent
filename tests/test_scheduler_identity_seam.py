# encoding:utf-8
"""One identity resolver for scheduled tasks (tasks 8.14-8.16).

``agent/tools/scheduler/integration.py`` is an upstream-conflicted file, so the
fork's decision "which identity does a stored task fire under" must not be an
inline branch there. These tests pin the converged shape:

* the resolver lives in ``scheduler/identity.py`` and there is exactly one;
* a task with an owner snapshot fires as that member (workspace / session /
  memory resolve as the member), a legacy task fires Agent-scoped;
* the two downstream policies (revalidation before the fire, and the task's
  notify session) stay downstream rather than inside the resolver.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools.scheduler.identity import execution_identity  # noqa: E402


def test_a_member_owned_task_fires_as_that_member():
    identity = execution_identity({
        "owner": {"user_id": "usr-1", "tenant_id": "tnt-1", "session_id": "sess-owner"},
        "action": {"type": "notify"},
    }, agent_id="agent-1")
    assert identity.user_id == "usr-1"
    assert identity.tenant_id == "tnt-1"
    assert identity.agent_id == "agent-1"


def test_the_notify_session_wins_over_the_owner_session():
    """A recurring task reports into the conversation it came from."""
    identity = execution_identity({
        "owner": {"user_id": "usr-1", "tenant_id": "tnt-1", "session_id": "sess-owner"},
        "action": {"type": "notify", "notify_session_id": "sess-notify"},
    }, agent_id="agent-1")
    assert identity.session_id == "sess-notify"


def test_a_legacy_task_stays_agent_scoped():
    """No owner (or an owner without user/tenant) keeps historical behaviour."""
    for task in ({}, {"owner": {}}, {"owner": {"user_id": "", "tenant_id": ""}},
                 {"owner": {"user_id": "usr-1"}}):
        identity = execution_identity(dict(task), agent_id="agent-1")
        assert identity.agent_id == "agent-1"
        assert not identity.user_id
        assert not identity.tenant_id


def test_an_absent_agent_id_is_not_invented():
    identity = execution_identity({"owner": {"user_id": "u", "tenant_id": "t"}})
    assert not identity.agent_id


class TestConvergedSeam:
    """8.16: one resolver, and the upstream file holds no fork copy of it."""

    def test_integration_has_no_local_identity_resolver(self):
        from agent.tools.scheduler import integration
        source = open(integration.__file__, encoding="utf-8").read()
        assert "def _execution_identity" not in source
        # No fork branch constructing a task identity in the upstream file.
        assert "RuntimeIdentity(" not in source
        assert "execution_identity" in source

    def test_the_resolver_is_defined_once_in_the_scheduler_package(self):
        import pathlib
        package = pathlib.Path(
            os.path.dirname(os.path.abspath(__import__(
                "agent.tools.scheduler.identity", fromlist=["x"]).__file__)))
        definitions = []
        for path in package.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "def execution_identity(" in text:
                definitions.append(path.name)
        assert definitions == ["identity.py"], definitions

    def test_identity_reaches_the_runtime_only_through_common_runtime_identity(self):
        from agent.tools.scheduler import identity, integration
        for module in (identity, integration):
            source = open(module.__file__, encoding="utf-8").read()
            assert "from common.runtime_identity import" in source or \
                "common.runtime_identity" in source
            for foreign in ("import contextvars", "ContextVar("):
                assert foreign not in source, f"{module.__name__} must not hold its own identity context"
