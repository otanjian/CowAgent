# encoding:utf-8
"""Task 4.8 A/B/C: a default change moves only *unanchored* new sessions.

`specs/user-default-agent-selection` ("用户默认只影响未锚定的新会话") and
`specs/agent-chat-launch` ("未选择智能体时的综合会话入口") make one promise:
a session is anchored to exactly one Agent, and a preference change is visible
to the next session it applies to — never to a session that already exists, and
never to an explicit target.

Anchoring is deliberately not a field somebody remembers to update: a session
lives in the conversation database of *its* Agent workspace
(``<workspace>/memory/long-term/index.db``), and every session-scoped route
addresses that store through the Agent the caller named. A default change cannot
move a row between two files, which is exactly why the property is worth pinning
here rather than by inspecting a pointer: the test would fail if resolution ever
started re-deriving a session's Agent from the preference.

Driven through the real WSGI app (`web_app`) so the anchor comes from the real
identity service and the row really lands in the Agent's own store.
"""

from __future__ import annotations

import json

import pytest


def _body(response) -> dict:
    return json.loads(response.data.decode("utf-8"))


class _World:
    """One member of a tenant holding a shared Agent and three private ones.

    ``alice-first`` / ``alice-second`` / ``alice-third`` are all hers, which is
    what makes a preference change observable (a member's candidates are her own
    private Agents) and what lets an *explicit* target be an Agent that is no
    kind of default at all.
    """

    def __init__(self, harness):
        from auth.runtime import resolve_context

        self.h = harness
        self.tenant_id = harness.tenant_id
        harness.add_agent("shared-agent")
        self.alice = harness.member("alice", ["member"])
        for agent_id in ("alice-first", "alice-second", "alice-third"):
            harness.private_agent(self.alice, agent_id)
        self.token = harness.login("alice")
        self.ctx = resolve_context(harness.service, self.token, self.tenant_id)

    # -- the product paths -------------------------------------------------

    def _pin_roster(self):
        """Resolve the registry from *this* harness's roster.

        A request may run ``load_config()`` and leave the process registry
        pointing at the deployment's own workspace (the harness patches ``conf``,
        and a reload rebinds it). These cases mix HTTP with direct service calls,
        so the roster this world declared is pinned before each direct use —
        otherwise a lookup would answer from a roster that has none of the Agents
        this test is about.
        """
        from agent import team
        from agent.registry import AgentRegistry, set_agent_registry

        set_agent_registry(
            AgentRegistry.from_config(team.resolve(dict(self.h._settings))))

    def start_agent_less_session(self, session_id: str) -> str:
        """What ``POST /api/message`` with no ``agent_id`` does, minus the model.

        ``MessageHandler`` passes whatever the body named (nothing, for a fresh
        "just type" conversation) to ``_authorize_chat_session``; the Agent-less
        case is therefore the one that has to follow the caller's default.
        """
        return self._claim(session_id, None)

    def start_session_on(self, session_id: str, agent_id: str) -> str:
        """A session created with an explicit target (the picker's path)."""
        return self._claim(session_id, agent_id)

    def _claim(self, session_id: str, agent_id):
        """Claim the session's durably-owned store, then write its first turn.

        ``POST /api/message`` claims the (Agent, session) key and then appends
        the caller's opening message, both under the request identity that
        ``_db_scope`` publishes. ``_authorize_chat_session`` is what anchors the
        session: its INSERT records the owner in *that Agent's* store, so the
        anchor is the file the row lands in.
        """
        from auth.runtime import to_runtime_identity
        from channel.web import web_channel
        from common.runtime_identity import use_identity

        self._pin_roster()
        with use_identity(to_runtime_identity(self.ctx)):
            agent_id = web_channel._authorize_chat_session(
                self.ctx, session_id, agent_id, create=True)
            store = self.store(agent_id)
            store.append_messages(
                session_id, [{"role": "user", "content": f"开场白 {session_id}"}],
                channel_type="web")
        return agent_id

    def anchor(self) -> dict:
        """The Agent a new session would anchor to, and why (the send path)."""
        from channel.web import web_channel

        self._pin_roster()
        return web_channel._resolve_default_agent(self.ctx)

    def choose(self, agent_id: str, revision=None) -> dict:
        """The console's "设为我的默认" action, over HTTP."""
        self._pin_roster()
        payload = {"action": "set_user_default", "id": agent_id}
        if revision is not None:
            payload["default_revision"] = revision
        response = self.h.post("/api/agents", payload, token=self.token)
        assert response.status == "200 OK", response.data
        body = _body(response)
        assert body.get("status") == "success", body
        return body.get("result") or {}

    def history(self, session_id: str, agent_id: str) -> dict:
        """``GET /api/history``: this session's turns, through one Agent.

        The read the console uses when it opens a conversation: the Agent in the
        query is the one that owns the row, and a session is addressed through
        exactly one Agent.
        """
        self._pin_roster()
        body = _body(self.h.get(
            f"/api/history?session_id={session_id}&agent_id={agent_id}"
            "&page=1&page_size=20", token=self.token))
        return body

    def rename(self, session_id: str, title: str, agent_id: str):
        """One session-scoped write, addressed to the Agent that owns it.

        ``console.js`` sends the owning Agent in both the query and the JSON body
        (``renameSession``); the handler reads the body, so both are set here the
        same way.
        """
        self._pin_roster()
        response = self.h.request(
            f"/api/sessions/{session_id}?agent_id={agent_id}", method="PUT",
            body={"title": title, "agent_id": agent_id}, token=self.token)
        return response.status, _body(response)

    def store(self, agent_id: str):
        from agent.memory import get_conversation_store

        self._pin_roster()
        return get_conversation_store(self.h.agent_workspace(agent_id))


@pytest.fixture
def world(web_app):
    return _World(web_app("default-scope"))


# --- A. the unanchored case still follows the member -----------------------

def test_a_default_change_moves_the_next_agent_less_session(world):
    """A new session with no explicit target resolves to the *new* default.

    The before-state is the point: nobody ever chose ``shared-agent``, so the
    first anchor is a fallback (``shared``) — and once alice chooses, the anchor
    is her own decision (``user``) with her own Agent. That is the change a new
    conversation is supposed to see.
    """
    assert world.anchor() == {"agent_id": "shared-agent", "source": "shared"}, (
        "with no preference of her own the member falls back to the shared Agent")

    world.choose("alice-first")

    assert world.anchor() == {"agent_id": "alice-first", "source": "user"}

    # The same resolver is what an Agent-less session is created with.
    assert world.start_agent_less_session("s-new") == "alice-first"
    assert world.store("alice-first").get_session_owner("s-new") == world.alice


# --- B. an existing session keeps the Agent it was anchored to -------------

def test_an_existing_session_keeps_its_agent_across_a_default_change(world):
    """旧会话不改变: the stored conversation is not re-pointed.

    ``s-old`` was created while ``alice-first`` was her default (design D4:
    已建立 session 不随偏好改变). After she moves her default to
    ``alice-second`` the session still lives in ``alice-first``'s store: its
    turns are still read through that Agent, the rename addressed to that Agent
    still succeeds, and the new default's store has never heard of it.
    """
    first = world.choose("alice-first")
    assert world.start_agent_less_session("s-old") == "alice-first"

    world.choose("alice-second", revision=first["default_agent_revision"])
    assert world.anchor() == {"agent_id": "alice-second", "source": "user"}, (
        "the preference really did change; otherwise this test proves nothing")

    assert world.store("alice-first").get_session_owner("s-old") == world.alice
    assert world.store("alice-second").get_session_owner("s-old") is None, (
        "the session must not have been moved into the new default's store")

    # Readable through its own Agent, and *only* through it.
    own = world.history("s-old", "alice-first")
    assert own["status"] == "success", own
    assert [m["role"] for m in own["messages"]] == ["user"]
    assert own["messages"][0]["content"] == "开场白 s-old", (
        "the conversation is still the one it always was")
    elsewhere = world.history("s-old", "alice-second")
    assert (elsewhere.get("messages") or []) == [], (
        "the new default must not answer for a session anchored elsewhere")

    status, body = world.rename("s-old", "重命名后仍是旧助手", "alice-first")
    assert status == "200 OK" and body["status"] == "success", body

    # Addressed through the *new* default it is not found: the preference moved,
    # the conversation did not.
    status, body = world.rename("s-old", "试图改到新默认", "alice-second")
    assert body.get("status") == "error" and "not found" in body.get("message", ""), (
        "the new default must not be able to address the old session")


def test_the_anchor_survives_a_restart(world):
    """Both halves outlive the process that made the change.

    A default is a stored preference and an anchor is a stored row, so neither
    may depend on an in-memory resolution: after dropping every cached store and
    re-opening the identity database, ``s-old`` is still owned in
    ``alice-first`` and the preference still resolves to ``alice-second``.
    """
    from agent.memory import clear_conversation_store_cache
    from auth.runtime import resolve_context
    from auth.service import IdentityService

    first = world.choose("alice-first")
    assert world.start_agent_less_session("s-old") == "alice-first"
    world.choose("alice-second", revision=first["default_agent_revision"])

    clear_conversation_store_cache()
    reopened = IdentityService(world.h.db_path)
    ctx = resolve_context(reopened, world.token, world.tenant_id)

    assert reopened.resolve_default_agent(world.tenant_id, world.alice) == {
        "agent_id": "alice-second", "source": "user"}, (
            "the preference is durable, not a process-local guess")
    assert world.store("alice-first").get_session_owner("s-old") == world.alice
    assert world.store("alice-second").get_session_owner("s-old") is None
    assert ctx.user_id == world.alice


def test_a_later_agent_less_session_uses_the_new_default(world):
    """The other half of B: the change applies going *forward* only."""
    first = world.choose("alice-first")
    assert world.start_agent_less_session("s-old") == "alice-first"

    world.choose("alice-second", revision=first["default_agent_revision"])

    assert world.start_agent_less_session("s-later") == "alice-second"
    assert world.store("alice-second").get_session_owner("s-later") == world.alice
    assert world.store("alice-first").get_session_owner("s-later") is None
    assert world.store("alice-first").get_session_owner("s-old") == world.alice, (
        "creating the later session must not disturb the earlier one")


# --- C. an explicit target is never overridden -----------------------------

def test_an_explicit_target_is_not_replaced_by_the_default(world):
    """显式目标不改变: the picker wins, and wins over a *different* default.

    ``alice-third`` is not a default in any sense — not the tenant's, not the
    member's, not a fallback. It is chosen explicitly, and the session has to be
    created in its store rather than redirected to the preference.
    """
    world.choose("alice-first")
    assert world.anchor()["agent_id"] == "alice-first"

    assert world.start_session_on("s-explicit", "alice-third") == "alice-third"

    assert world.store("alice-third").get_session_owner("s-explicit") == world.alice
    assert world.store("alice-first").get_session_owner("s-explicit") is None
    # And the explicit choice did not rewrite the preference either.
    assert world.anchor() == {"agent_id": "alice-first", "source": "user"}


def test_an_explicit_target_wins_even_for_a_fallback_anchor(world):
    """The member has no preference at all: the picker still decides.

    This is the shape that matters for a caller who has never touched the
    default: the fallback anchor is ``shared-agent``, and sending to
    ``alice-third`` must not be answered out of it.
    """
    assert world.anchor() == {"agent_id": "shared-agent", "source": "shared"}

    assert world.start_session_on("s-picked", "alice-third") == "alice-third"
    assert world.store("alice-third").get_session_owner("s-picked") == world.alice
    assert world.store("shared-agent").get_session_owner("s-picked") is None
