"""The sender's display name is what makes a pending attempt identifiable.

Feishu's message event carries only the author's ``open_id`` — the name lives
behind the contact API. These tests pin the best-effort contract: resolve it
when we can, never let the failure of that lookup affect the message itself.
"""
import json
from types import SimpleNamespace

import pytest

from channel.feishu import feishu_message as fm
from channel.feishu.feishu_message import FeishuMessage


def _event(text="帮我查下报销", open_id="ou_stranger"):
    return {
        "app_id": "cli_bot",
        "sender": {"sender_id": {"open_id": open_id}},
        "message": {
            "message_id": "om_1",
            "chat_id": "oc_chat",
            "message_type": "text",
            "content": json.dumps({"text": text}),
        },
    }


def _response(body, status_code=200):
    return SimpleNamespace(status_code=status_code, json=lambda: body)


@pytest.fixture(autouse=True)
def _clear_name_cache():
    # The lookup is memoised per process to keep a chatty stranger from
    # hammering the contact API; tests must not inherit each other's answers.
    fm._SENDER_NAME_CACHE.clear()
    yield
    fm._SENDER_NAME_CACHE.clear()


def test_a_known_sender_is_named(monkeypatch):
    calls = []

    def fake_get(**kwargs):
        calls.append(kwargs)
        return _response({"code": 0, "data": {"user": {"name": "张三"}}})

    monkeypatch.setattr(fm.requests, "get", fake_get)
    message = FeishuMessage(_event(), access_token="tenant-token")

    assert message.resolve_sender_name() == "张三"
    assert calls[0]["url"].endswith("/open-apis/contact/v3/users/ou_stranger")
    assert calls[0]["params"] == {"user_id_type": "open_id"}


def test_the_second_ask_is_served_from_memory(monkeypatch):
    """One lookup per author per process: this runs on the refusal path."""
    calls = []
    monkeypatch.setattr(
        fm.requests, "get",
        lambda **kwargs: (calls.append(kwargs),
                          _response({"code": 0, "data": {"user": {"name": "张三"}}}))[1],
    )
    first = FeishuMessage(_event(), access_token="t")
    second = FeishuMessage(_event(), access_token="t")

    assert first.resolve_sender_name() == "张三"
    assert second.resolve_sender_name() == "张三"
    assert len(calls) == 1


def test_a_missing_permission_degrades_to_no_name(monkeypatch):
    """The app may not hold the contact scope; binding must still be possible."""
    monkeypatch.setattr(
        fm.requests, "get",
        lambda **kwargs: _response({"code": 99991672, "msg": "no permission"}),
    )
    message = FeishuMessage(_event(), access_token="t")
    assert message.resolve_sender_name() == ""


def test_a_network_failure_never_escapes(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(fm.requests, "get", boom)
    message = FeishuMessage(_event(), access_token="t")
    assert message.resolve_sender_name() == ""


def test_a_failure_is_not_retried_for_every_message(monkeypatch):
    """A refused stranger who keeps typing must not generate an API call each time."""
    calls = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("network down")

    monkeypatch.setattr(fm.requests, "get", boom)
    FeishuMessage(_event(), access_token="t").resolve_sender_name()
    FeishuMessage(_event(), access_token="t").resolve_sender_name()
    assert len(calls) == 1


def test_without_a_token_no_lookup_is_attempted(monkeypatch):
    calls = []
    monkeypatch.setattr(fm.requests, "get",
                        lambda **kwargs: calls.append(kwargs) or _response({}))
    message = FeishuMessage(_event(), access_token=None)
    assert message.resolve_sender_name() == ""
    assert calls == []


def test_an_existing_nickname_short_circuits_the_lookup(monkeypatch):
    monkeypatch.setattr(
        fm.requests, "get",
        lambda **kwargs: pytest.fail("should not ask when the name is known"))
    message = FeishuMessage(_event(), access_token="t")
    message.actual_user_nickname = "已在消息里"
    assert message.resolve_sender_name() == "已在消息里"
